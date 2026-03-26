"""
Market scanner for Polymarket Arbitrage Bot.
Continuously fetches active binary markets and caches the data with TTL.
"""

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from bot.client import create_client, MarketPrice

logger = logging.getLogger(__name__)


class MarketScanner:
    """
    Scans Polymarket for active binary (YES/NO) markets and returns
    their current prices.  Results are cached with a configurable TTL
    so we don't hammer the API on every scan iteration.
    """

    def __init__(self, cache_ttl: int = 15):
        """
        Args:
            cache_ttl: Seconds before the market-price cache expires.
        """
        self.client = create_client()
        self.cache_ttl = cache_ttl
        self._cache: List[MarketPrice] = []
        self._cache_ts: float = 0.0
        self._scan_count: int = 0

    # ─── Public API ───────────────────────────────────────────────────────────

    async def get_markets(self) -> List[MarketPrice]:
        """
        Return the current list of active markets.
        Uses the in-memory cache if it is still fresh.
        """
        if self._cache_is_fresh():
            return self._cache

        return await self._refresh_cache()

    async def scan_once(self) -> List[MarketPrice]:
        """
        Force a fresh scan regardless of cache state, then return all markets.
        """
        self._cache_ts = 0.0   # invalidate cache
        return await self.get_markets()

    @property
    def scan_count(self) -> int:
        return self._scan_count

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _cache_is_fresh(self) -> bool:
        return bool(self._cache) and (time.monotonic() - self._cache_ts) < self.cache_ttl

    async def _refresh_cache(self) -> List[MarketPrice]:
        """Fetch markets from the client and update the cache."""
        logger.debug("Refreshing market cache…")
        try:
            markets = await self.client.get_markets()
        except Exception as exc:
            logger.error("Scanner: error fetching markets: %s", exc)
            return self._cache  # return stale data rather than crashing

        # Filter: active, not closed, has valid token IDs
        active: List[MarketPrice] = [
            m for m in markets
            if _is_valid_binary_market(m)
        ]

        self._cache = active
        self._cache_ts = time.monotonic()
        self._scan_count += 1

        logger.info(
            "Scan #%d: %d active binary markets found (from %d total)",
            self._scan_count,
            len(active),
            len(markets),
        )
        return active

    # ─── Balance helper ───────────────────────────────────────────────────────

    async def get_balance(self) -> float:
        """Delegate to the underlying client."""
        try:
            return await self.client.get_balance()
        except Exception as exc:
            logger.error("Scanner: error fetching balance: %s", exc)
            return 0.0


def _is_valid_binary_market(m: MarketPrice) -> bool:
    """
    Return True only if the MarketPrice looks like a live binary market
    with sensible prices.
    """
    if not m.market_id:
        return False
    if not m.yes_token_id or not m.no_token_id:
        return False
    # Prices must be in (0, 1)
    if not (0.0 < m.yes_ask < 1.0):
        return False
    if not (0.0 < m.no_ask < 1.0):
        return False
    return True
