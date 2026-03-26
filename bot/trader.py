"""
Trade executor for Polymarket Arbitrage Bot.
Takes an ArbitrageOpportunity, executes both YES and NO legs,
tracks open positions, and closes them when markets resolve.
"""

import asyncio
import logging
import random
import time
from typing import Dict, Optional

from bot.arbitrage_detector import ArbitrageOpportunity
from bot.client import create_client, OrderResult
from bot import database as db
from config import config

logger = logging.getLogger(__name__)


class Trader:
    """
    Executes arbitrage trades and tracks their lifecycle.
    """

    def __init__(self):
        self.client = create_client()
        # trade_id → dict with position metadata
        self._open_trades: Dict[int, dict] = {}
        self._closed_trades: int = 0
        self._total_pnl: float = 0.0

    # ─── Execute ──────────────────────────────────────────────────────────────

    async def execute_arbitrage(
        self,
        opportunity: ArbitrageOpportunity,
        position_size_usd: float,
    ) -> Optional[int]:
        """
        Buy YES and NO tokens simultaneously.
        Returns the database trade_id on success, None on failure.
        """
        yes_price = opportunity.yes_price
        no_price = opportunity.no_price
        net_cost_per_unit = yes_price + no_price

        if net_cost_per_unit <= 0:
            logger.error("Invalid net_cost_per_unit for %s", opportunity.market_id)
            return None

        # Number of contracts (units) to buy with the approved capital
        units = position_size_usd / net_cost_per_unit
        yes_size = round(units, 4)
        no_size = round(units, 4)
        total_cost = round(units * net_cost_per_unit, 6)
        fees = round(total_cost * config.FEE_RATE, 6)
        expected_profit = round(units * opportunity.net_profit_per_unit, 6)

        logger.info(
            "Executing arb on %s | units=%.4f YES@%.4f NO@%.4f cost=$%.2f expected_profit=$%.4f",
            opportunity.market_id,
            units,
            yes_price,
            no_price,
            total_cost,
            expected_profit,
        )

        # ── Place orders ──────────────────────────────────────────────────────
        yes_result, no_result = await asyncio.gather(
            self.client.place_order(
                market_id=opportunity.market_id,
                token_id=opportunity.yes_token_id,
                side="BUY",
                price=yes_price,
                size=yes_size,
            ),
            self.client.place_order(
                market_id=opportunity.market_id,
                token_id=opportunity.no_token_id,
                side="BUY",
                price=no_price,
                size=no_size,
            ),
        )

        # ── Handle failures / partial fills ──────────────────────────────────
        if yes_result.status == "CANCELLED" and no_result.status == "CANCELLED":
            logger.error(
                "Both legs cancelled for market %s – aborting", opportunity.market_id
            )
            return None

        # Use actual filled sizes
        actual_yes = yes_result.filled_size or yes_size
        actual_no = no_result.filled_size or no_size
        actual_cost = (
            actual_yes * yes_price
            + actual_no * no_price
            + yes_result.fees_paid
            + no_result.fees_paid
        )
        actual_expected_profit = (
            min(actual_yes, actual_no) * (1.0 - net_cost_per_unit)
            - (actual_cost * config.FEE_RATE)
        )

        if yes_result.status == "CANCELLED":
            logger.warning("YES leg cancelled; cancelling NO leg too (partial fill risk)")
            # In live mode we'd cancel the NO order; in mock it's already filled
            return None

        if no_result.status == "CANCELLED":
            logger.warning("NO leg cancelled; cancelling YES leg too (partial fill risk)")
            return None

        # ── Persist to DB ─────────────────────────────────────────────────────
        try:
            trade_id = await db.save_trade(
                market_id=opportunity.market_id,
                question=opportunity.question,
                strategy="yes_no_arb",
                yes_size=actual_yes,
                no_size=actual_no,
                yes_price=yes_price,
                no_price=no_price,
                total_cost=round(actual_cost, 4),
                expected_profit=round(actual_expected_profit, 6),
            )
        except Exception as exc:
            logger.error("Failed to save trade to DB: %s", exc)
            return None

        self._open_trades[trade_id] = {
            "market_id": opportunity.market_id,
            "question": opportunity.question,
            "yes_token_id": opportunity.yes_token_id,
            "no_token_id": opportunity.no_token_id,
            "yes_size": actual_yes,
            "no_size": actual_no,
            "yes_price": yes_price,
            "no_price": no_price,
            "total_cost": actual_cost,
            "expected_profit": actual_expected_profit,
            "yes_order_id": yes_result.order_id,
            "no_order_id": no_result.order_id,
            "opened_at": time.time(),
        }

        logger.info(
            "Trade %d opened for market %s | cost=$%.4f | expected_profit=$%.6f",
            trade_id,
            opportunity.market_id,
            actual_cost,
            actual_expected_profit,
        )
        return trade_id

    # ─── Close / resolution ───────────────────────────────────────────────────

    async def close_trade(
        self,
        trade_id: int,
        actual_profit: float,
        status: str = "closed",
    ) -> None:
        """
        Mark a trade as closed in the database and update internal state.
        """
        pos = self._open_trades.pop(trade_id, None)
        if pos is None:
            logger.warning("close_trade: unknown trade_id=%d", trade_id)
            return

        try:
            await db.close_trade(trade_id, actual_profit, status)
        except Exception as exc:
            logger.error("Failed to update trade %d in DB: %s", trade_id, exc)

        self._total_pnl += actual_profit
        self._closed_trades += 1

        logger.info(
            "Trade %d closed | actual_profit=$%.4f | cumulative_pnl=$%.4f",
            trade_id,
            actual_profit,
            self._total_pnl,
        )

    async def simulate_resolution(self, trade_id: int) -> None:
        """
        Mock-mode only: randomly resolve the market after a short delay,
        paying out 1.0 on the winning leg.
        """
        if not config.MOCK_MODE:
            return

        pos = self._open_trades.get(trade_id)
        if pos is None:
            return

        # Simulate waiting for resolution (2–10 seconds in mock mode)
        await asyncio.sleep(random.uniform(2, 10))

        # In a real arb both outcomes yield profit; simulate the payout
        winning_size = pos["yes_size"]  # either leg, same size in our arb
        payout = winning_size * 1.0     # $1 per winning token
        actual_profit = payout - pos["total_cost"]

        await self.close_trade(trade_id, actual_profit)

        if config.MOCK_MODE:
            # Update mock balance
            self.client.mock_credit(payout) if hasattr(self.client, "mock_credit") else None

    # ─── Accessors ────────────────────────────────────────────────────────────

    @property
    def open_trades(self) -> Dict[int, dict]:
        return dict(self._open_trades)

    @property
    def total_pnl(self) -> float:
        return self._total_pnl

    @property
    def closed_trades(self) -> int:
        return self._closed_trades
