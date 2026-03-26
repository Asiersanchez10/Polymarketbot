"""
Risk manager for Polymarket Arbitrage Bot.
Enforces position limits, exposure caps, Kelly sizing, and stop-loss logic.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from config import config

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    """Outcome of a risk pre-check."""
    approved: bool
    reason: str
    approved_size: float = 0.0   # adjusted (possibly reduced) position size in USD


@dataclass
class PositionRecord:
    """Lightweight view of an open position for exposure tracking."""
    trade_id: int
    market_id: str
    total_cost: float
    expected_profit: float


class RiskManager:
    """
    Centralized risk gate that must approve every trade before execution.
    """

    def __init__(
        self,
        max_position_pct: float = None,
        max_exposure_pct: float = None,
        min_profit_pct: float = None,
        max_concurrent_positions: int = None,
        kelly_fraction: float = None,
        fee_rate: float = None,
    ):
        self.max_position_pct = max_position_pct or config.MAX_POSITION_PCT
        self.max_exposure_pct = max_exposure_pct or config.MAX_EXPOSURE_PCT
        self.min_profit_pct = min_profit_pct or config.MIN_PROFIT_PCT
        self.max_concurrent_positions = max_concurrent_positions or config.MAX_CONCURRENT_POSITIONS
        self.kelly_fraction = kelly_fraction or config.KELLY_FRACTION
        self.fee_rate = fee_rate or config.FEE_RATE

        self._portfolio_value: float = config.INITIAL_PORTFOLIO
        self._open_positions: Dict[int, PositionRecord] = {}   # trade_id → PositionRecord
        self._total_pnl: float = 0.0

    # ─── Portfolio value ──────────────────────────────────────────────────────

    def update_portfolio_value(self, value: float) -> None:
        self._portfolio_value = max(value, 0.01)

    @property
    def portfolio_value(self) -> float:
        return self._portfolio_value

    # ─── Position tracking ────────────────────────────────────────────────────

    def register_position(self, trade_id: int, market_id: str, total_cost: float, expected_profit: float) -> None:
        """Called after a trade is successfully opened."""
        self._open_positions[trade_id] = PositionRecord(
            trade_id=trade_id,
            market_id=market_id,
            total_cost=total_cost,
            expected_profit=expected_profit,
        )
        logger.debug("Risk: registered position %d | cost=%.2f", trade_id, total_cost)

    def close_position(self, trade_id: int, actual_profit: float) -> None:
        """Called after a trade is closed/resolved."""
        pos = self._open_positions.pop(trade_id, None)
        if pos is None:
            logger.warning("Risk: close_position called for unknown trade_id=%d", trade_id)
            return
        self._total_pnl += actual_profit
        logger.debug(
            "Risk: closed position %d | actual_profit=%.4f | cumulative_pnl=%.4f",
            trade_id, actual_profit, self._total_pnl,
        )

    @property
    def open_position_count(self) -> int:
        return len(self._open_positions)

    @property
    def total_exposure(self) -> float:
        return sum(p.total_cost for p in self._open_positions.values())

    @property
    def total_pnl(self) -> float:
        return self._total_pnl

    # ─── Risk checks ──────────────────────────────────────────────────────────

    def check_opportunity(
        self,
        market_id: str,
        profit_pct: float,
        requested_size: float,
    ) -> RiskCheckResult:
        """
        Validate whether we should trade an opportunity.
        Returns RiskCheckResult with approved=True and the approved (possibly
        reduced) size, or approved=False with a reason.
        """

        # 1. Profit threshold
        if profit_pct < self.min_profit_pct:
            return RiskCheckResult(
                approved=False,
                reason=f"Profit {profit_pct:.4f}% below minimum {self.min_profit_pct}%",
            )

        # 2. Max concurrent positions
        if self.open_position_count >= self.max_concurrent_positions:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Max concurrent positions reached "
                    f"({self.open_position_count}/{self.max_concurrent_positions})"
                ),
            )

        # 3. Already in this market?
        if any(p.market_id == market_id for p in self._open_positions.values()):
            return RiskCheckResult(
                approved=False,
                reason=f"Already have an open position in market {market_id}",
            )

        # 4. Maximum position size
        max_size = self._portfolio_value * (self.max_position_pct / 100.0)
        approved_size = min(requested_size, max_size)

        # 5. Total exposure cap
        remaining_exposure = (self._portfolio_value * (self.max_exposure_pct / 100.0)) - self.total_exposure
        if remaining_exposure <= 0:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Total exposure cap reached: "
                    f"${self.total_exposure:.2f} / "
                    f"${self._portfolio_value * self.max_exposure_pct / 100:.2f}"
                ),
            )
        approved_size = min(approved_size, remaining_exposure)

        # 6. Minimum viable size ($1)
        if approved_size < 1.0:
            return RiskCheckResult(
                approved=False,
                reason=f"Approved size ${approved_size:.2f} too small (minimum $1.00)",
            )

        logger.debug(
            "Risk: APPROVED market=%s profit=%.4f%% size=$%.2f (requested=$%.2f)",
            market_id, profit_pct, approved_size, requested_size,
        )
        return RiskCheckResult(approved=True, reason="ok", approved_size=approved_size)

    def stop_loss_check(self) -> bool:
        """
        Returns True if trading should be halted due to excessive losses.
        Trigger: total P&L < -20% of initial portfolio.
        """
        loss_threshold = -0.20 * config.INITIAL_PORTFOLIO
        if self._total_pnl < loss_threshold:
            logger.warning(
                "STOP-LOSS triggered: total P&L=%.2f below threshold=%.2f",
                self._total_pnl, loss_threshold,
            )
            return True
        return False

    # ─── Kelly position sizing ────────────────────────────────────────────────

    def kelly_size(self, net_profit_per_unit: float, net_cost_per_unit: float) -> float:
        """
        Calculate the Kelly-optimal position size in USD.
        For a risk-free arb (p=1), Kelly fraction = min(b, 1) * kelly_fraction.
        b = fractional return = net_profit / net_cost.
        """
        if net_cost_per_unit <= 0:
            return 0.0
        b = net_profit_per_unit / net_cost_per_unit
        raw_kelly = min(b, 1.0)
        kelly_size = self._portfolio_value * self.kelly_fraction * raw_kelly
        max_size = self._portfolio_value * (self.max_position_pct / 100.0)
        return min(kelly_size, max_size)

    # ─── Summary ─────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        return {
            "portfolio_value": round(self._portfolio_value, 2),
            "total_pnl": round(self._total_pnl, 4),
            "open_positions": self.open_position_count,
            "total_exposure": round(self.total_exposure, 2),
            "exposure_pct": round(
                (self.total_exposure / self._portfolio_value) * 100
                if self._portfolio_value > 0 else 0.0,
                2,
            ),
            "max_concurrent_positions": self.max_concurrent_positions,
            "stop_loss_triggered": self.stop_loss_check(),
        }
