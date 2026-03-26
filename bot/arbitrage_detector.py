"""
Arbitrage detector for Polymarket Arbitrage Bot.

Strategy: YES/NO Sum Arbitrage
  - If cost_to_buy_YES + cost_to_buy_NO < 1.0 - fees, one of the tokens will
    resolve to $1 and the other to $0, guaranteeing a profit on the combined
    position regardless of market outcome.

  Net cost  = yes_ask + no_ask
  Net revenue on resolution = $1.00 (winner) + $0.00 (loser)
  Gross profit per unit = 1.0 - net_cost
  Net profit per unit   = gross_profit - fees (= net_cost * fee_rate)
                        = 1.0 - yes_ask - no_ask - (yes_ask + no_ask) * fee_rate
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

from bot.client import MarketPrice
from config import config

logger = logging.getLogger(__name__)


@dataclass
class ArbitrageOpportunity:
    """Encapsulates a detected YES/NO sum arbitrage opportunity."""

    market_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_price: float           # ask price for YES (cost to buy)
    no_price: float            # ask price for NO (cost to buy)
    net_cost: float            # yes_price + no_price
    fees: float                # estimated fees on the combined position
    gross_profit_per_unit: float   # 1.0 - net_cost
    net_profit_per_unit: float     # gross_profit - fees
    profit_pct: float          # net_profit / net_cost * 100
    spread: float              # 1.0 - yes_price - no_price  (pre-fee gap)
    recommended_size: float    # recommended position size in USD
    kelly_fraction: float      # raw Kelly fraction used
    volume_24h: float = 0.0

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "question": self.question,
            "yes_token_id": self.yes_token_id,
            "no_token_id": self.no_token_id,
            "yes_price": round(self.yes_price, 4),
            "no_price": round(self.no_price, 4),
            "net_cost": round(self.net_cost, 4),
            "fees": round(self.fees, 6),
            "gross_profit_per_unit": round(self.gross_profit_per_unit, 6),
            "net_profit_per_unit": round(self.net_profit_per_unit, 6),
            "profit_pct": round(self.profit_pct, 4),
            "spread": round(self.spread, 4),
            "recommended_size": round(self.recommended_size, 2),
            "kelly_fraction": round(self.kelly_fraction, 6),
            "volume_24h": round(self.volume_24h, 2),
        }


class ArbitrageDetector:
    """
    Scans a list of MarketPrice objects and identifies YES/NO sum arbitrage
    opportunities that meet the configured profit threshold.
    """

    def __init__(
        self,
        fee_rate: float = None,
        min_profit_pct: float = None,
        kelly_fraction: float = None,
        portfolio_value: float = None,
        max_position_pct: float = None,
    ):
        self.fee_rate = fee_rate if fee_rate is not None else config.FEE_RATE
        self.min_profit_pct = min_profit_pct if min_profit_pct is not None else config.MIN_PROFIT_PCT
        self.kelly_fraction = kelly_fraction if kelly_fraction is not None else config.KELLY_FRACTION
        self.portfolio_value = portfolio_value if portfolio_value is not None else config.INITIAL_PORTFOLIO
        self.max_position_pct = max_position_pct if max_position_pct is not None else config.MAX_POSITION_PCT

    # ─── Public API ───────────────────────────────────────────────────────────

    def detect(self, markets: List[MarketPrice]) -> List[ArbitrageOpportunity]:
        """
        Check each market for YES/NO sum arbitrage.
        Returns opportunities sorted by descending net profit percentage.
        """
        opportunities: List[ArbitrageOpportunity] = []

        for market in markets:
            opp = self._check_market(market)
            if opp is not None:
                opportunities.append(opp)

        opportunities.sort(key=lambda o: o.profit_pct, reverse=True)

        if opportunities:
            logger.info(
                "Detected %d arbitrage opportunity(ies). Best: %.4f%% profit on '%s'",
                len(opportunities),
                opportunities[0].profit_pct,
                opportunities[0].question[:60],
            )
        return opportunities

    def update_portfolio_value(self, value: float) -> None:
        """Update the portfolio value used for position sizing."""
        self.portfolio_value = max(value, 1.0)

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _check_market(self, m: MarketPrice) -> Optional[ArbitrageOpportunity]:
        """Return an ArbitrageOpportunity if one exists for this market, else None."""
        yes_ask = m.yes_ask
        no_ask = m.no_ask

        if yes_ask <= 0 or no_ask <= 0:
            return None
        if yes_ask >= 1.0 or no_ask >= 1.0:
            return None

        net_cost = yes_ask + no_ask
        spread = 1.0 - net_cost  # positive → raw gap before fees

        # Fees apply to the total capital deployed (both legs)
        fees = net_cost * self.fee_rate

        gross_profit = 1.0 - net_cost   # revenue ($1) minus cost
        net_profit = gross_profit - fees

        # Profit as a percentage of capital deployed
        profit_pct = (net_profit / net_cost) * 100.0

        if profit_pct < self.min_profit_pct:
            return None

        # ── Kelly criterion position sizing ───────────────────────────────────
        # For a "bet" with probability p of winning and net odds b:
        #   p = probability of winning one leg (= 1.0, arb is risk-free)
        #   b = net_profit / net_cost  (fractional gain)
        # Kelly = (p * b - (1 - p)) / b  → for a risk-free arb, Kelly = 1.0
        # We then scale by KELLY_FRACTION and cap at MAX_POSITION_PCT.
        b = net_profit / net_cost  # fractional return per unit invested
        kelly_raw = min(b, 1.0)    # risk-free → full Kelly, but cap at 1

        max_position = self.portfolio_value * (self.max_position_pct / 100.0)
        kelly_position = self.portfolio_value * self.kelly_fraction * kelly_raw
        recommended_size = min(kelly_position, max_position)
        recommended_size = max(recommended_size, 1.0)   # at least $1

        logger.debug(
            "Opportunity: %s | YES=%.4f NO=%.4f spread=%.4f profit=%.4f%%",
            m.market_id, yes_ask, no_ask, spread, profit_pct,
        )

        return ArbitrageOpportunity(
            market_id=m.market_id,
            question=m.question,
            yes_token_id=m.yes_token_id,
            no_token_id=m.no_token_id,
            yes_price=yes_ask,
            no_price=no_ask,
            net_cost=net_cost,
            fees=fees,
            gross_profit_per_unit=gross_profit,
            net_profit_per_unit=net_profit,
            profit_pct=profit_pct,
            spread=spread,
            recommended_size=round(recommended_size, 2),
            kelly_fraction=kelly_raw,
            volume_24h=m.volume_24h,
        )
