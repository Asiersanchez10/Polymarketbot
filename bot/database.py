"""
Database module for Polymarket Arbitrage Bot.
Uses SQLAlchemy with async SQLite backend.
"""

import logging
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    Text,
    select,
    desc,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Opportunity(Base):
    """Detected arbitrage opportunities."""

    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)
    market_id = Column(String(255), nullable=False, index=True)
    question = Column(Text, nullable=False)
    yes_price = Column(Float, nullable=False)
    no_price = Column(Float, nullable=False)
    spread = Column(Float, nullable=False)
    potential_profit_pct = Column(Float, nullable=False)
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    executed = Column(Boolean, default=False, nullable=False)
    trade_id = Column(Integer, nullable=True)


class Trade(Base):
    """Executed trades."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, index=True)
    market_id = Column(String(255), nullable=False, index=True)
    question = Column(Text, nullable=False)
    strategy = Column(String(50), nullable=False, default="yes_no_arb")
    yes_size = Column(Float, nullable=False)
    no_size = Column(Float, nullable=False)
    yes_price = Column(Float, nullable=False)
    no_price = Column(Float, nullable=False)
    total_cost = Column(Float, nullable=False)
    expected_profit = Column(Float, nullable=False)
    actual_profit = Column(Float, nullable=True)
    status = Column(String(20), nullable=False, default="open")  # open/closed/cancelled
    opened_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)


class BotStats(Base):
    """Periodic bot performance snapshots."""

    __tablename__ = "bot_stats"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    total_pnl = Column(Float, nullable=False, default=0.0)
    total_trades = Column(Integer, nullable=False, default=0)
    winning_trades = Column(Integer, nullable=False, default=0)
    portfolio_value = Column(Float, nullable=False, default=0.0)
    opportunities_found = Column(Integer, nullable=False, default=0)


# Engine and session factory (initialized on startup)
_engine = None
_async_session: Optional[async_sessionmaker] = None


async def init_database() -> None:
    """Initialize the database, creating tables if they don't exist."""
    global _engine, _async_session

    db_url = config.DATABASE_URL
    logger.info(f"Initializing database at: {db_url}")

    _engine = create_async_engine(
        db_url,
        echo=False,
        future=True,
        connect_args={"check_same_thread": False},
    )

    _async_session = async_sessionmaker(
        _engine, class_=AsyncSession, expire_on_commit=False
    )

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database initialized successfully.")


def get_session() -> AsyncSession:
    """Get a new async database session."""
    if _async_session is None:
        raise RuntimeError("Database not initialized. Call init_database() first.")
    return _async_session()


async def save_opportunity(
    market_id: str,
    question: str,
    yes_price: float,
    no_price: float,
    spread: float,
    potential_profit_pct: float,
) -> int:
    """Persist a detected arbitrage opportunity. Returns the new record ID."""
    async with get_session() as session:
        opp = Opportunity(
            market_id=market_id,
            question=question,
            yes_price=yes_price,
            no_price=no_price,
            spread=spread,
            potential_profit_pct=potential_profit_pct,
            detected_at=datetime.utcnow(),
            executed=False,
        )
        session.add(opp)
        await session.commit()
        await session.refresh(opp)
        return opp.id


async def mark_opportunity_executed(opportunity_id: int, trade_id: int) -> None:
    """Mark an opportunity as executed with its corresponding trade ID."""
    async with get_session() as session:
        result = await session.execute(
            select(Opportunity).where(Opportunity.id == opportunity_id)
        )
        opp = result.scalar_one_or_none()
        if opp:
            opp.executed = True
            opp.trade_id = trade_id
            await session.commit()


async def save_trade(
    market_id: str,
    question: str,
    strategy: str,
    yes_size: float,
    no_size: float,
    yes_price: float,
    no_price: float,
    total_cost: float,
    expected_profit: float,
) -> int:
    """Persist a new trade record. Returns the new trade ID."""
    async with get_session() as session:
        trade = Trade(
            market_id=market_id,
            question=question,
            strategy=strategy,
            yes_size=yes_size,
            no_size=no_size,
            yes_price=yes_price,
            no_price=no_price,
            total_cost=total_cost,
            expected_profit=expected_profit,
            status="open",
            opened_at=datetime.utcnow(),
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        return trade.id


async def close_trade(trade_id: int, actual_profit: float, status: str = "closed") -> None:
    """Update a trade with its final profit and closed status."""
    async with get_session() as session:
        result = await session.execute(select(Trade).where(Trade.id == trade_id))
        trade = result.scalar_one_or_none()
        if trade:
            trade.actual_profit = actual_profit
            trade.status = status
            trade.closed_at = datetime.utcnow()
            await session.commit()


async def save_bot_stats(
    total_pnl: float,
    total_trades: int,
    winning_trades: int,
    portfolio_value: float,
    opportunities_found: int,
) -> None:
    """Snapshot current bot stats."""
    async with get_session() as session:
        stats = BotStats(
            timestamp=datetime.utcnow(),
            total_pnl=total_pnl,
            total_trades=total_trades,
            winning_trades=winning_trades,
            portfolio_value=portfolio_value,
            opportunities_found=opportunities_found,
        )
        session.add(stats)
        await session.commit()


async def get_recent_opportunities(limit: int = 50) -> List[dict]:
    """Fetch the most recent arbitrage opportunities."""
    async with get_session() as session:
        result = await session.execute(
            select(Opportunity).order_by(desc(Opportunity.detected_at)).limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "market_id": r.market_id,
                "question": r.question,
                "yes_price": r.yes_price,
                "no_price": r.no_price,
                "spread": r.spread,
                "potential_profit_pct": r.potential_profit_pct,
                "detected_at": r.detected_at.isoformat() if r.detected_at else None,
                "executed": r.executed,
                "trade_id": r.trade_id,
            }
            for r in rows
        ]


async def get_trades(limit: int = 100) -> List[dict]:
    """Fetch trade history, most recent first."""
    async with get_session() as session:
        result = await session.execute(
            select(Trade).order_by(desc(Trade.opened_at)).limit(limit)
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "market_id": r.market_id,
                "question": r.question,
                "strategy": r.strategy,
                "yes_size": r.yes_size,
                "no_size": r.no_size,
                "yes_price": r.yes_price,
                "no_price": r.no_price,
                "total_cost": r.total_cost,
                "expected_profit": r.expected_profit,
                "actual_profit": r.actual_profit,
                "status": r.status,
                "opened_at": r.opened_at.isoformat() if r.opened_at else None,
                "closed_at": r.closed_at.isoformat() if r.closed_at else None,
            }
            for r in rows
        ]


async def get_open_trades() -> List[dict]:
    """Fetch currently open positions."""
    async with get_session() as session:
        result = await session.execute(
            select(Trade).where(Trade.status == "open").order_by(desc(Trade.opened_at))
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "market_id": r.market_id,
                "question": r.question,
                "strategy": r.strategy,
                "yes_size": r.yes_size,
                "no_size": r.no_size,
                "yes_price": r.yes_price,
                "no_price": r.no_price,
                "total_cost": r.total_cost,
                "expected_profit": r.expected_profit,
                "opened_at": r.opened_at.isoformat() if r.opened_at else None,
            }
            for r in rows
        ]


async def get_latest_stats() -> Optional[dict]:
    """Fetch the most recent bot stats snapshot."""
    async with get_session() as session:
        result = await session.execute(
            select(BotStats).order_by(desc(BotStats.timestamp)).limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            "id": row.id,
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            "total_pnl": row.total_pnl,
            "total_trades": row.total_trades,
            "winning_trades": row.winning_trades,
            "portfolio_value": row.portfolio_value,
            "opportunities_found": row.opportunities_found,
        }


async def get_stats_history(limit: int = 100) -> List[dict]:
    """Fetch historical stats for chart rendering."""
    async with get_session() as session:
        result = await session.execute(
            select(BotStats).order_by(desc(BotStats.timestamp)).limit(limit)
        )
        rows = result.scalars().all()
        # Return in chronological order for chart plotting
        return [
            {
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                "total_pnl": r.total_pnl,
                "portfolio_value": r.portfolio_value,
                "opportunities_found": r.opportunities_found,
                "total_trades": r.total_trades,
            }
            for r in reversed(rows)
        ]


async def count_opportunities_today() -> int:
    """Count opportunities detected today."""
    async with get_session() as session:
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        result = await session.execute(
            select(func.count(Opportunity.id)).where(Opportunity.detected_at >= today)
        )
        return result.scalar() or 0
