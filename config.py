"""
Configuration module for Polymarket Arbitrage Bot.
Loads settings from environment variables with sensible defaults.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration class loaded from environment variables."""

    # API Configuration
    CLOB_API_URL: str = os.getenv("CLOB_API_URL", "https://clob.polymarket.com")
    PRIVATE_KEY: str = os.getenv("PRIVATE_KEY", "")
    CHAIN_ID: int = int(os.getenv("CHAIN_ID", "137"))
    CLOB_API_KEY: str = os.getenv("CLOB_API_KEY", "")
    CLOB_API_SECRET: str = os.getenv("CLOB_API_SECRET", "")
    CLOB_API_PASSPHRASE: str = os.getenv("CLOB_API_PASSPHRASE", "")

    # Trading Parameters
    MIN_PROFIT_PCT: float = float(os.getenv("MIN_PROFIT_PCT", "0.5"))
    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "5.0"))
    MAX_EXPOSURE_PCT: float = float(os.getenv("MAX_EXPOSURE_PCT", "50.0"))
    MAX_CONCURRENT_POSITIONS: int = int(os.getenv("MAX_CONCURRENT_POSITIONS", "10"))
    FEE_RATE: float = float(os.getenv("FEE_RATE", "0.02"))

    # Mode
    MOCK_MODE: bool = os.getenv("MOCK_MODE", "true").lower() in ("true", "1", "yes")

    # Dashboard
    DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", "8080"))
    DASHBOARD_HOST: str = os.getenv("DASHBOARD_HOST", "0.0.0.0")

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///./polymarket_bot.db"
    )

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Scanning
    SCAN_INTERVAL: int = int(os.getenv("SCAN_INTERVAL", "30"))

    # Portfolio
    INITIAL_PORTFOLIO: float = float(os.getenv("INITIAL_PORTFOLIO", "1000.0"))

    # Kelly criterion fraction (fraction of full Kelly to use, 0.25 = quarter Kelly)
    KELLY_FRACTION: float = float(os.getenv("KELLY_FRACTION", "0.25"))

    # Minimum spread to consider (absolute, not percentage)
    MIN_SPREAD: float = float(os.getenv("MIN_SPREAD", "0.005"))

    @classmethod
    def validate(cls) -> None:
        """Validate critical configuration settings."""
        if not cls.MOCK_MODE and not cls.PRIVATE_KEY:
            raise ValueError(
                "PRIVATE_KEY must be set when MOCK_MODE is false. "
                "Set MOCK_MODE=true for testing without real funds."
            )
        if cls.MIN_PROFIT_PCT < 0:
            raise ValueError("MIN_PROFIT_PCT must be non-negative.")
        if not (0 < cls.MAX_POSITION_PCT <= 100):
            raise ValueError("MAX_POSITION_PCT must be between 0 and 100.")
        if not (0 < cls.MAX_EXPOSURE_PCT <= 100):
            raise ValueError("MAX_EXPOSURE_PCT must be between 0 and 100.")
        if not (0 <= cls.FEE_RATE < 1):
            raise ValueError("FEE_RATE must be between 0 and 1.")

    @classmethod
    def summary(cls) -> dict:
        """Return a safe summary (no secrets) of current config."""
        return {
            "clob_api_url": cls.CLOB_API_URL,
            "chain_id": cls.CHAIN_ID,
            "mock_mode": cls.MOCK_MODE,
            "min_profit_pct": cls.MIN_PROFIT_PCT,
            "max_position_pct": cls.MAX_POSITION_PCT,
            "max_exposure_pct": cls.MAX_EXPOSURE_PCT,
            "max_concurrent_positions": cls.MAX_CONCURRENT_POSITIONS,
            "fee_rate": cls.FEE_RATE,
            "scan_interval": cls.SCAN_INTERVAL,
            "dashboard_port": cls.DASHBOARD_PORT,
            "initial_portfolio": cls.INITIAL_PORTFOLIO,
            "kelly_fraction": cls.KELLY_FRACTION,
        }


config = Config()
