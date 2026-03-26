"""
Entry point for Polymarket Arbitrage Bot.
Starts the bot engine and the FastAPI dashboard together.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys

import uvicorn
from dotenv import load_dotenv

# Load .env before importing anything else
load_dotenv()

from config import config
from bot import database as db
from bot.engine import BotEngine
from dashboard.app import app as dashboard_app, set_engine


def setup_logging(level: str = "INFO") -> None:
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # Quiet noisy libs
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Polymarket Arbitrage Bot")
    parser.add_argument(
        "--mock",
        action="store_true",
        default=None,
        help="Force mock mode (overrides MOCK_MODE env var)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Dashboard port (overrides DASHBOARD_PORT env var)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Dashboard host (overrides DASHBOARD_HOST env var)",
    )
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="Do not automatically start the trading bot on launch",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    return parser.parse_args()


async def run(
    host: str,
    port: int,
    autostart: bool = True,
) -> None:
    """Main async entry point."""

    # ── Database ──────────────────────────────────────────────────────────────
    logger.info("Initialising database…")
    await db.init_database()
    logger.info("Database ready.")

    # ── Bot engine ────────────────────────────────────────────────────────────
    event_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    engine = BotEngine(event_queue=event_queue)
    set_engine(engine, event_queue)

    if autostart:
        logger.info("Auto-starting bot engine…")
        await engine.start()

    # ── Uvicorn server ────────────────────────────────────────────────────────
    uv_config = uvicorn.Config(
        app=dashboard_app,
        host=host,
        port=port,
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(uv_config)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    loop = asyncio.get_event_loop()

    def _handle_signal():
        logger.info("Shutdown signal received…")
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    # ── Start serving ─────────────────────────────────────────────────────────
    logger.info(
        "Dashboard available at http://%s:%d  (mock_mode=%s)",
        host if host != "0.0.0.0" else "localhost",
        port,
        config.MOCK_MODE,
    )

    try:
        await server.serve()
    finally:
        logger.info("Shutting down bot engine…")
        await engine.stop()
        logger.info("Goodbye.")


def main() -> None:
    args = parse_args()

    # Apply CLI overrides before Config is used
    if args.mock:
        os.environ["MOCK_MODE"] = "true"
        # Re-read the class attribute
        config.__class__.MOCK_MODE = True

    if args.port:
        os.environ["DASHBOARD_PORT"] = str(args.port)
        config.__class__.DASHBOARD_PORT = args.port

    if args.host:
        os.environ["DASHBOARD_HOST"] = args.host
        config.__class__.DASHBOARD_HOST = args.host

    log_level = args.log_level or config.LOG_LEVEL
    setup_logging(log_level)

    logger.info("=" * 60)
    logger.info("  Polymarket Arbitrage Bot")
    logger.info("  Mock mode : %s", config.MOCK_MODE)
    logger.info("  Dashboard : http://%s:%d", config.DASHBOARD_HOST, config.DASHBOARD_PORT)
    logger.info("=" * 60)

    # Validate config
    try:
        config.validate()
    except ValueError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    autostart = not args.no_autostart

    asyncio.run(
        run(
            host=config.DASHBOARD_HOST,
            port=config.DASHBOARD_PORT,
            autostart=autostart,
        )
    )


if __name__ == "__main__":
    main()
