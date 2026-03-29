"""
FastAPI web dashboard for Polymarket Arbitrage Bot.
Provides REST API endpoints and real-time SSE updates.
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

from bot import database as db
from config import config

logger = logging.getLogger(__name__)

# These are injected at startup by main.py
_bot_engine = None
_event_queue: Optional[asyncio.Queue] = None

app = FastAPI(title="Polymarket Arbitrage Bot", version="1.0.0")

_html_path = Path(__file__).resolve().parent / "templates" / "index.html"

# ─── Dependency injection helpers ────────────────────────────────────────────

def set_engine(engine, queue: asyncio.Queue) -> None:
    global _bot_engine, _event_queue
    _bot_engine = engine
    _event_queue = queue


def get_engine():
    if _bot_engine is None:
        raise HTTPException(status_code=503, detail="Bot engine not initialised")
    return _bot_engine


# ─── HTML Dashboard ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return HTMLResponse(content=_html_path.read_text(encoding="utf-8"))


# ─── API: Bot Control ─────────────────────────────────────────────────────────

@app.post("/api/bot/start")
async def start_bot():
    engine = get_engine()
    if engine.is_running:
        return {"status": "already_running", "message": "Bot is already running"}
    await engine.start()
    return {"status": "started", "message": "Bot started successfully"}


@app.post("/api/bot/stop")
async def stop_bot():
    engine = get_engine()
    if not engine.is_running:
        return {"status": "already_stopped", "message": "Bot is not running"}
    await engine.stop()
    return {"status": "stopped", "message": "Bot stopped successfully"}


# ─── API: Stats ───────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    engine = get_engine()
    try:
        live_status = await engine.get_status()
    except Exception as exc:
        logger.error("get_stats error: %s", exc)
        live_status = {}

    # Merge with latest DB snapshot
    db_stats = await db.get_latest_stats() or {}
    opps_today = await db.count_opportunities_today()
    stats_history = await db.get_stats_history(limit=50)

    return {
        "live": live_status,
        "snapshot": db_stats,
        "opportunities_today": opps_today,
        "pnl_history": stats_history,
        "config": config.summary(),
    }


# ─── API: Opportunities ───────────────────────────────────────────────────────

@app.get("/api/opportunities")
async def get_opportunities(limit: int = 50):
    rows = await db.get_recent_opportunities(limit=min(limit, 200))
    return {"opportunities": rows, "count": len(rows)}


# ─── API: Trades ─────────────────────────────────────────────────────────────

@app.get("/api/trades")
async def get_trades(limit: int = 100):
    rows = await db.get_trades(limit=min(limit, 500))
    return {"trades": rows, "count": len(rows)}


# ─── API: Open Positions ─────────────────────────────────────────────────────

@app.get("/api/positions")
async def get_positions():
    rows = await db.get_open_trades()
    engine = get_engine()

    # Augment with in-memory P&L estimate
    in_memory = engine.trader.open_trades if hasattr(engine, "trader") else {}
    for row in rows:
        tid = row.get("id")
        mem = in_memory.get(tid, {})
        row["unrealised_pnl"] = round(mem.get("expected_profit", row.get("expected_profit", 0)), 4)

    return {"positions": rows, "count": len(rows)}


# ─── API: SSE Events ─────────────────────────────────────────────────────────

@app.get("/api/events")
async def sse_events(request: Request):
    """Server-Sent Events stream for real-time dashboard updates."""

    async def event_generator() -> AsyncGenerator[str, None]:
        # Send an initial ping
        yield "data: {\"type\": \"ping\"}\n\n"

        last_ping = time.time()
        while True:
            if await request.is_disconnected():
                break

            # Send keep-alive ping every 15 s
            if time.time() - last_ping > 15:
                yield "data: {\"type\": \"ping\"}\n\n"
                last_ping = time.time()

            # Drain the event queue
            try:
                event = _event_queue.get_nowait() if _event_queue else None
                if event:
                    yield f"data: {json.dumps(event)}\n\n"
                    continue
            except asyncio.QueueEmpty:
                pass

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
