"""
Polymarket CLOB client wrapper.
Supports both real trading (via py_clob_client) and mock mode for testing.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

MOCK_MARKET_QUESTIONS = [
    "Will the Fed raise rates in Q1 2026?",
    "Will Bitcoin reach $150,000 by end of 2026?",
    "Will the US enter a recession in 2026?",
    "Will Ethereum 2.0 launch by mid-2026?",
    "Will the S&P 500 end 2026 above 6000?",
    "Will there be a US government shutdown in 2026?",
    "Will inflation drop below 2% by mid-2026?",
    "Will Nvidia stock split in 2026?",
    "Will the US elect a new president in 2028?",
    "Will OpenAI release GPT-5 in 2026?",
    "Will Tesla launch its robotaxi by Q3 2026?",
    "Will China invade Taiwan in 2026?",
    "Will gold hit $3500 by end of 2026?",
    "Will oil prices exceed $100/barrel in 2026?",
    "Will SpaceX land humans on Mars by 2030?",
    "Will the EU ban TikTok in 2026?",
    "Will Meta release AR glasses by Q2 2026?",
    "Will unemployment in the US exceed 5% in 2026?",
    "Will Amazon acquire another major company in 2026?",
    "Will quantum computing achieve supremacy by 2027?",
]


@dataclass
class MarketPrice:
    """YES and NO token prices for a market."""
    market_id: str
    question: str
    yes_token_id: str
    no_token_id: str
    yes_ask: float   # Best ask price for YES (what we'd pay to buy YES)
    no_ask: float    # Best ask price for NO (what we'd pay to buy NO)
    yes_bid: float   # Best bid (what we'd receive selling YES)
    no_bid: float    # Best bid (what we'd receive selling NO)
    volume_24h: float = 0.0
    end_date: Optional[str] = None


@dataclass
class OrderResult:
    """Result from placing an order."""
    order_id: str
    market_id: str
    token_id: str
    side: str        # "BUY" or "SELL"
    price: float
    size: float
    status: str      # "MATCHED", "LIVE", "CANCELLED"
    filled_size: float = 0.0
    fees_paid: float = 0.0


class MockPolymarketClient:
    """
    Simulated Polymarket client for testing without real funds.
    Generates realistic market data with occasional arbitrage opportunities.
    """

    def __init__(self):
        self._markets: dict[str, MarketPrice] = {}
        self._balance = config.INITIAL_PORTFOLIO
        self._order_counter = 0
        self._init_mock_markets()
        logger.info(f"[MOCK] Client initialized with ${self._balance:.2f} USDC balance")

    def _init_mock_markets(self):
        for i, question in enumerate(MOCK_MARKET_QUESTIONS):
            market_id = f"mock-market-{i:03d}"
            # Random fair prices that roughly sum to 1.0
            yes_mid = round(random.uniform(0.10, 0.90), 3)
            no_mid = round(1.0 - yes_mid, 3)
            spread = random.uniform(0.005, 0.015)
            self._markets[market_id] = MarketPrice(
                market_id=market_id,
                question=question,
                yes_token_id=f"yes-token-{i:03d}",
                no_token_id=f"no-token-{i:03d}",
                yes_ask=round(min(yes_mid + spread / 2, 0.99), 4),
                no_ask=round(min(no_mid + spread / 2, 0.99), 4),
                yes_bid=round(max(yes_mid - spread / 2, 0.01), 4),
                no_bid=round(max(no_mid - spread / 2, 0.01), 4),
                volume_24h=round(random.uniform(1_000, 500_000), 2),
            )

    def _drift_markets(self):
        """Randomly drift prices to simulate live market movement."""
        for market_id, mp in self._markets.items():
            # Occasionally introduce a real arbitrage opportunity (~5% chance)
            if random.random() < 0.05:
                gap = random.uniform(0.008, 0.04)
                yes_ask = round(random.uniform(0.10, 0.90), 3)
                no_ask = round(1.0 - yes_ask - gap, 3)
                no_ask = max(0.01, min(0.99, no_ask))
                spread = random.uniform(0.003, 0.010)
                self._markets[market_id] = MarketPrice(
                    market_id=market_id,
                    question=mp.question,
                    yes_token_id=mp.yes_token_id,
                    no_token_id=mp.no_token_id,
                    yes_ask=round(yes_ask, 4),
                    no_ask=round(no_ask, 4),
                    yes_bid=round(yes_ask - spread, 4),
                    no_bid=round(no_ask - spread, 4),
                    volume_24h=mp.volume_24h,
                )
            else:
                # Normal drift
                drift = random.uniform(-0.005, 0.005)
                yes_ask = max(0.01, min(0.99, mp.yes_ask + drift))
                no_ask = max(0.01, min(0.99, mp.no_ask - drift))
                spread_y = mp.yes_ask - mp.yes_bid
                spread_n = mp.no_ask - mp.no_bid
                self._markets[market_id] = MarketPrice(
                    market_id=market_id,
                    question=mp.question,
                    yes_token_id=mp.yes_token_id,
                    no_token_id=mp.no_token_id,
                    yes_ask=round(yes_ask, 4),
                    no_ask=round(no_ask, 4),
                    yes_bid=round(max(0.01, yes_ask - spread_y), 4),
                    no_bid=round(max(0.01, no_ask - spread_n), 4),
                    volume_24h=mp.volume_24h,
                )

    async def get_markets(self) -> list[MarketPrice]:
        """Return all simulated markets."""
        await asyncio.sleep(0.05)  # simulate network latency
        self._drift_markets()
        return list(self._markets.values())

    async def get_balance(self) -> float:
        """Return simulated USDC balance."""
        await asyncio.sleep(0.02)
        return self._balance

    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> OrderResult:
        """Simulate placing an order (always fills immediately in mock mode)."""
        await asyncio.sleep(random.uniform(0.05, 0.15))
        self._order_counter += 1
        order_id = f"mock-order-{self._order_counter:06d}-{int(time.time())}"
        fees = size * price * config.FEE_RATE

        if side == "BUY":
            cost = size * price + fees
            if cost > self._balance:
                logger.warning(f"[MOCK] Insufficient balance: need ${cost:.2f}, have ${self._balance:.2f}")
                return OrderResult(
                    order_id=order_id,
                    market_id=market_id,
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    status="CANCELLED",
                    filled_size=0.0,
                    fees_paid=0.0,
                )
            self._balance -= cost
        else:
            proceeds = size * price - fees
            self._balance += proceeds

        logger.debug(
            f"[MOCK] Order {order_id}: {side} {size} tokens @ ${price:.4f} "
            f"(fees ${fees:.4f}) | balance=${self._balance:.2f}"
        )
        return OrderResult(
            order_id=order_id,
            market_id=market_id,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            status="MATCHED",
            filled_size=size,
            fees_paid=fees,
        )

    async def resolve_position(self, market_id: str, winning_token_id: str, size: float) -> float:
        """Simulate market resolution - winning side pays $1 per token."""
        payout = size * 1.0
        self._balance += payout
        logger.info(f"[MOCK] Market {market_id} resolved. Payout: ${payout:.2f}")
        return payout


class LivePolymarketClient:
    """
    Real Polymarket CLOB client.
    Uses py_clob_client for authenticated trading on Polygon.
    """

    def __init__(self):
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            creds = None
            if config.CLOB_API_KEY:
                creds = ApiCreds(
                    api_key=config.CLOB_API_KEY,
                    api_secret=config.CLOB_API_SECRET,
                    api_passphrase=config.CLOB_API_PASSPHRASE,
                )

            self._client = ClobClient(
                host=config.CLOB_API_URL,
                chain_id=config.CHAIN_ID,
                key=config.PRIVATE_KEY,
                creds=creds,
            )
            logger.info("Live Polymarket CLOB client initialized.")
        except ImportError:
            raise RuntimeError(
                "py_clob_client not installed. Run: pip install py-clob-client"
            )

    async def get_markets(self) -> list[MarketPrice]:
        """Fetch active binary markets from Polymarket CLOB."""
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, self._client.get_markets)
        except Exception as e:
            logger.error(f"Failed to fetch markets: {e}")
            return []

        markets = []
        for m in raw.get("data", []):
            try:
                tokens = m.get("tokens", [])
                if len(tokens) < 2:
                    continue
                yes_token = next((t for t in tokens if t.get("outcome") == "Yes"), None)
                no_token = next((t for t in tokens if t.get("outcome") == "No"), None)
                if not yes_token or not no_token:
                    continue

                yes_book = await loop.run_in_executor(
                    None, self._client.get_order_book, yes_token["token_id"]
                )
                no_book = await loop.run_in_executor(
                    None, self._client.get_order_book, no_token["token_id"]
                )

                yes_ask = float(yes_book.asks[0].price) if yes_book.asks else 0.99
                no_ask = float(no_book.asks[0].price) if no_book.asks else 0.99
                yes_bid = float(yes_book.bids[0].price) if yes_book.bids else 0.01
                no_bid = float(no_book.bids[0].price) if no_book.bids else 0.01

                markets.append(MarketPrice(
                    market_id=m["condition_id"],
                    question=m.get("question", "Unknown"),
                    yes_token_id=yes_token["token_id"],
                    no_token_id=no_token["token_id"],
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    yes_bid=yes_bid,
                    no_bid=no_bid,
                    volume_24h=float(m.get("volume24hr", 0)),
                ))
            except Exception as e:
                logger.debug(f"Skipping market {m.get('condition_id', '?')}: {e}")
                continue

        logger.info(f"Fetched {len(markets)} live markets.")
        return markets

    async def get_balance(self) -> float:
        """Return USDC balance from the wallet."""
        loop = asyncio.get_event_loop()
        try:
            bal = await loop.run_in_executor(None, self._client.get_balance)
            return float(bal)
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0.0

    async def place_order(
        self,
        market_id: str,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> OrderResult:
        """Place a limit order on Polymarket CLOB."""
        from py_clob_client.clob_types import OrderArgs, Side as ClobSide

        loop = asyncio.get_event_loop()
        try:
            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=ClobSide.BUY if side == "BUY" else ClobSide.SELL,
            )
            resp = await loop.run_in_executor(
                None, self._client.create_and_post_order, order_args
            )
            order_id = resp.get("orderID", "unknown")
            status = resp.get("status", "LIVE")
            filled = float(resp.get("sizeMatched", 0))
            fees = filled * price * config.FEE_RATE
            return OrderResult(
                order_id=order_id,
                market_id=market_id,
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                status=status,
                filled_size=filled,
                fees_paid=fees,
            )
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            return OrderResult(
                order_id="error",
                market_id=market_id,
                token_id=token_id,
                side=side,
                price=price,
                size=size,
                status="CANCELLED",
                filled_size=0.0,
                fees_paid=0.0,
            )


def create_client():
    """Factory: returns mock or live client based on config."""
    if config.MOCK_MODE:
        logger.info("Running in MOCK mode. No real funds will be used.")
        return MockPolymarketClient()
    else:
        logger.info("Running in LIVE mode. Real funds will be used!")
        return LivePolymarketClient()
