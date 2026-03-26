"""
Polymarket Arbitrage Bot - Entry Point
Usage:
  python main.py              # runs in mock mode (default)
  python main.py --live       # runs with real funds (requires PRIVATE_KEY in .env)
  python main.py --port 9090  # custom dashboard port
"""

import argparse
import asyncio
import logging
import os
import sys

import uvicorn

from bot.database import init_database
from bot.engine import BotEngine
from config import config
from dashboard.app import app, set_engine


def setup_logging():
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def parse_args():
    parser = argparse.ArgumentParser(description="Polymarket Arbitrage Bot")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Disable mock mode and trade with real funds (requires PRIVATE_KEY)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=config.DASHBOARD_PORT,
        help=f"Dashboard port (default: {config.DASHBOARD_PORT})",
    )
    parser.add_argument(
        "--host",
        default=config.DASHBOARD_HOST,
        help=f"Dashboard host (default: {config.DASHBOARD_HOST})",
    )
    parser.add_argument(
        "--no-autostart",
        action="store_true",
        help="Launch dashboard but do not auto-start the bot",
    )
    return parser.parse_args()


async def run(args):
    logger = logging.getLogger("main")

    # Apply CLI overrides before any other module reads config
    if args.live:
        os.environ["MOCK_MODE"] = "false"
        # Re-read config value
        config.MOCK_MODE = False
        logger.warning("LIVE MODE ENABLED - real funds will be used!")
    else:
        os.environ["MOCK_MODE"] = "true"
        config.MOCK_MODE = True

    try:
        config.validate()
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    # Database
    await init_database()
    logger.info("Database initialised.")

    # Bot engine
    engine = BotEngine()
    set_engine(engine, engine.event_queue)
    logger.info("Bot engine created (mock_mode=%s).", config.MOCK_MODE)

    # Auto-start unless suppressed
    if not args.no_autostart:
        await engine.start()
        logger.info("Bot auto-started.")

    # Dashboard
    dashboard_url = f"http://localhost:{args.port}"
    logger.info("Dashboard starting at %s", dashboard_url)
    print(f"\n  Polymarket Arbitrage Bot")
    print(f"  Dashboard: {dashboard_url}")
    print(f"  Mode: {'MOCK' if config.MOCK_MODE else 'LIVE'}")
    print(f"  Press Ctrl+C to stop\n")

    server_config = uvicorn.Config(
        app=app,
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    server = uvicorn.Server(server_config)

    try:
        await server.serve()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("Shutting down...")
        await engine.stop()
        logger.info("Shutdown complete.")


def main():
    setup_logging()
    args = parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
