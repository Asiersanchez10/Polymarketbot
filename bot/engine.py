"""
Main bot engine for Polymarket Arbitrage Bot.
Orchestrates scanner → detector → risk manager → trader.
Runs the main async loop and pushes events to the dashboard via asyncio.Queue.
"""

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from bot import database as db
from bot.arbitrage_detector import ArbitrageDetector, ArbitrageOpportunity
from bot.risk_manager import RiskManager
from bot.scanner import MarketScanner
from bot.trader import Trader
from config import config

logger = logging.getLogger(__name__)


class BotEngine:
    """
    Coordinates all bot components and drives the main scan-detect-trade loop.
    """

    def __init__(self, event_queue: Optional[asyncio.Queue] = None):
        self.scanner = MarketScanner(cache_ttl=config.SCAN_INTERVAL // 2)
        self.detector = ArbitrageDetector()
        self.risk_manager = RiskManager()
        self.trader = Trader()
        self.event_queue: asyncio.Queue = event_queue or asyncio.Queue(maxsize=500)

        self._running: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._stats_task: Optional[asyncio.Task] = None
        self._start_time: Optional[float] = None

        # Cumulative counters
        self._opportunities_found: int = 0
        self._total_trades_opened: int = 0
        self._winning_trades: int = 0

    # ─── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            logger.warning("Engine already running.")
            return
        self._running = True
        self._start_time = time.time()
        logger.info("Bot engine starting…")
        self._loop_task = asyncio.create_task(self._main_loop(), name="bot_main_loop")
        self._stats_task = asyncio.create_task(self._stats_loop(), name="bot_stats_loop")
        await self._emit_event("status", {"running": True, "message": "Bot started"})

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        logger.info("Bot engine stopping…")
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        if self._stats_task:
            self._stats_task.cancel()
            try:
                await self._stats_task
            except asyncio.CancelledError:
                pass
        await self._emit_event("status", {"running": False, "message": "Bot stopped"})
        logger.info("Bot engine stopped.")

    @property
    def is_running(self) -> bool:
        return self._running

    # ─── Main scan loop ───────────────────────────────────────────────────────

    async def _main_loop(self) -> None:
        logger.info("Main scan loop started. Interval=%ds", config.SCAN_INTERVAL)
        while self._running:
            try:
                await self._run_scan_cycle()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Unhandled error in main loop: %s", exc, exc_info=True)

            # Sleep until next scan
            try:
                await asyncio.sleep(config.SCAN_INTERVAL)
            except asyncio.CancelledError:
                raise

    async def _run_scan_cycle(self) -> None:
        """One complete scan → detect → trade cycle."""
        cycle_start = time.time()

        # 1. Refresh portfolio value for sizing decisions
        balance = await self.scanner.get_balance()
        self.detector.update_portfolio_value(balance)
        self.risk_manager.update_portfolio_value(balance)

        # 2. Check stop-loss
        if self.risk_manager.stop_loss_check():
            logger.critical("Stop-loss triggered – halting trading.")
            await self._emit_event("alert", {"level": "critical", "message": "Stop-loss triggered!"})
            await self.stop()
            return

        # 3. Scan markets
        markets = await self.scanner.scan_once()
        if not markets:
            logger.warning("No markets returned from scanner.")
            return

        # 4. Detect opportunities
        opportunities = self.detector.detect(markets)
        self._opportunities_found += len(opportunities)

        if opportunities:
            await self._emit_event(
                "opportunities",
                {"count": len(opportunities), "items": [o.as_dict() for o in opportunities[:5]]},
            )

        # 5. Save opportunities to DB and attempt to execute
        for opp in opportunities:
            opp_id = await db.save_opportunity(
                market_id=opp.market_id,
                question=opp.question,
                yes_price=opp.yes_price,
                no_price=opp.no_price,
                spread=opp.spread,
                potential_profit_pct=opp.profit_pct,
            )

            # Risk check
            risk_result = self.risk_manager.check_opportunity(
                market_id=opp.market_id,
                profit_pct=opp.profit_pct,
                requested_size=opp.recommended_size,
            )

            if not risk_result.approved:
                logger.info("Risk rejected opportunity %s: %s", opp.market_id, risk_result.reason)
                continue

            # Execute trade
            trade_id = await self.trader.execute_arbitrage(opp, risk_result.approved_size)
            if trade_id is None:
                logger.warning("Trade execution failed for %s", opp.market_id)
                continue

            # Register with risk manager
            total_cost = risk_result.approved_size
            self.risk_manager.register_position(
                trade_id=trade_id,
                market_id=opp.market_id,
                total_cost=total_cost,
                expected_profit=opp.net_profit_per_unit * (total_cost / opp.net_cost),
            )

            # Mark opportunity as executed
            await db.mark_opportunity_executed(opp_id, trade_id)

            self._total_trades_opened += 1

            await self._emit_event(
                "trade_opened",
                {"trade_id": trade_id, "market_id": opp.market_id, "question": opp.question[:80]},
            )

            # In mock mode, schedule async resolution
            if config.MOCK_MODE:
                asyncio.create_task(self._mock_resolve(trade_id))

        elapsed = time.time() - cycle_start
        logger.debug("Scan cycle completed in %.2fs | markets=%d opps=%d", elapsed, len(markets), len(opportunities))

    async def _mock_resolve(self, trade_id: int) -> None:
        """Resolve a mock trade after a simulated delay."""
        try:
            await self.trader.simulate_resolution(trade_id)
            # After resolution update the risk manager
            pos = self.trader.open_trades.get(trade_id)
            # trade already removed from open_trades by simulate_resolution
            # find from closed trades
            trades = await db.get_trades(limit=1)
            if trades:
                latest = trades[0]
                profit = latest.get("actual_profit", 0.0) or 0.0
                self.risk_manager.close_position(trade_id, profit)
                if profit > 0:
                    self._winning_trades += 1
                await self._emit_event(
                    "trade_closed",
                    {"trade_id": trade_id, "actual_profit": round(profit, 4)},
                )
        except Exception as exc:
            logger.error("Error during mock resolution of trade %d: %s", trade_id, exc)

    # ─── Stats snapshot loop ──────────────────────────────────────────────────

    async def _stats_loop(self) -> None:
        """Periodically save a bot stats snapshot to the database."""
        while self._running:
            try:
                await asyncio.sleep(60)   # snapshot every 60 seconds
                await self._snapshot_stats()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Stats loop error: %s", exc)

    async def _snapshot_stats(self) -> None:
        """Write a BotStats row to the database."""
        balance = await self.scanner.get_balance()
        pnl = self.trader.total_pnl
        portfolio_value = balance + pnl

        await db.save_bot_stats(
            total_pnl=pnl,
            total_trades=self._total_trades_opened,
            winning_trades=self._winning_trades,
            portfolio_value=portfolio_value,
            opportunities_found=self._opportunities_found,
        )
        logger.debug(
            "Stats snapshot: pnl=%.4f trades=%d opps=%d",
            pnl, self._total_trades_opened, self._opportunities_found,
        )

    # ─── Event emission ───────────────────────────────────────────────────────

    async def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        payload = {"type": event_type, "data": data, "ts": time.time()}
        try:
            self.event_queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.debug("Event queue full; dropping event %s", event_type)

    # ─── Status summary ───────────────────────────────────────────────────────

    async def get_status(self) -> Dict[str, Any]:
        balance = await self.scanner.get_balance()
        risk = self.risk_manager.summary()
        return {
            "running": self._running,
            "mock_mode": config.MOCK_MODE,
            "uptime_seconds": round(time.time() - self._start_time, 1) if self._start_time else 0,
            "balance": round(balance, 2),
            "total_pnl": round(self.trader.total_pnl, 4),
            "total_trades": self._total_trades_opened,
            "winning_trades": self._winning_trades,
            "win_rate": round(
                (self._winning_trades / self._total_trades_opened * 100)
                if self._total_trades_opened > 0 else 0.0,
                1,
            ),
            "open_positions": risk["open_positions"],
            "total_exposure": risk["total_exposure"],
            "opportunities_found_session": self._opportunities_found,
            "scan_count": self.scanner.scan_count,
            "scan_interval": config.SCAN_INTERVAL,
        }
