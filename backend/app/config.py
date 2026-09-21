"""
Configuration management for Delta Basket Platform.

Credentials are read from config.yaml in the project root.
Phase 2 will add encryption for credentials.
"""

import os
import yaml
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from datetime import timedelta


@dataclass
class DeltaConfig:
    """Delta Exchange API configuration."""

    # API Credentials (from config.yaml)
    api_key: str = ""
    api_secret: str = ""

    # API Endpoints
    base_url: str = "https://api.india.delta.exchange"
    ws_url: str = "wss://stream.india.delta.exchange/v2/live"

    # Request Configuration
    timeout_seconds: int = 30
    signature_version: str = "v1"

    # WebSocket Configuration
    ws_reconnect_enabled: bool = True
    ws_initial_backoff_seconds: int = 1
    ws_max_backoff_seconds: int = 300  # 5 minutes
    ws_backoff_multiplier: float = 2.0

    # Market Data Configuration
    # Public channels to subscribe to (no auth required)
    public_channels: list = field(default_factory=lambda: [
        "ticker",
        "mark_price",
        "trades",
        "ob_l1",
        "spot_price",
        "system_status"
    ])

    # REST Fallback (when WebSocket disconnects)
    rest_fallback_enabled: bool = True
    rest_fallback_interval_seconds: int = 2  # Poll every 2 seconds as fallback


@dataclass
class DatabaseConfig:
    """PostgreSQL database configuration."""

    user: str = "postgres"
    password: str = "postgres"
    host: str = "localhost"
    port: int = 5432
    database: str = "delta_basket_db"

    @property
    def url(self) -> str:
        """SQLAlchemy async connection string."""
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"


@dataclass
class PlatformConfig:
    """Platform-wide configuration."""

    # Environment
    env: str = "development"  # development, staging, production
    debug: bool = True
    log_level: str = "INFO"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True

    # Basket Trading Configuration
    entry_timeout_minutes: int = 15  # Default unhedged exposure timeout
    default_chase_offset_cents: int = 100  # $1.00 default chase offset for sell legs

    # Monitoring
    tick_processing_interval_seconds: float = 0.1  # Process ticks every 100ms
    position_update_interval_seconds: float = 1  # Update P&L every 1 second

    # Components
    enable_market_data_service: bool = True
    enable_order_execution: bool = True
    enable_basket_engine: bool = True
    enable_trigger_monitor: bool = True


class Config:
    """Master configuration loader."""

    _instance: Optional["Config"] = None

    def __init__(self):
        self.delta = DeltaConfig()
        self.database = DatabaseConfig()
        self.platform = PlatformConfig()
        self._load_config_file()
        self._load_env_overrides()

    def _load_config_file(self) -> None:
        """Load configuration from config.yaml if it exists."""
        config_path = Path(__file__).parent.parent.parent / "config.yaml"

        if not config_path.exists():
            print(f"⚠️  Config file not found at {config_path}")
            print("   Using defaults. Create config.yaml to override.")
            return

        try:
            with open(config_path, "r") as f:
                config_data = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"⚠️  Error loading config.yaml: {e}")
            return

        # Load Delta configuration
        if "delta" in config_data:
            delta_cfg = config_data["delta"]
            self.delta.api_key = delta_cfg.get("api_key", "")
            self.delta.api_secret = delta_cfg.get("api_secret", "")
            self.delta.base_url = delta_cfg.get("base_url", self.delta.base_url)
            self.delta.ws_url = delta_cfg.get("ws_url", self.delta.ws_url)

        # Load Database configuration
        if "database" in config_data:
            db_cfg = config_data["database"]
            self.database.user = db_cfg.get("user", self.database.user)
            self.database.password = db_cfg.get("password", self.database.password)
            self.database.host = db_cfg.get("host", self.database.host)
            self.database.port = db_cfg.get("port", self.database.port)
            self.database.database = db_cfg.get("database", self.database.database)

        # Load Platform configuration
        if "platform" in config_data:
            plat_cfg = config_data["platform"]
            self.platform.env = plat_cfg.get("env", self.platform.env)
            self.platform.debug = plat_cfg.get("debug", self.platform.debug)
            self.platform.log_level = plat_cfg.get("log_level", self.platform.log_level)
            self.platform.entry_timeout_minutes = plat_cfg.get("entry_timeout_minutes", 15)

    def _load_env_overrides(self) -> None:
        """Load overrides from environment variables (for Docker/deployment)."""
        api_key = os.getenv("DELTA_API_KEY")
        if api_key:
            self.delta.api_key = api_key

        api_secret = os.getenv("DELTA_API_SECRET")
        if api_secret:
            self.delta.api_secret = api_secret

        # Load database configuration from environment variables
        self.database.host = os.getenv("DB_HOST", self.database.host)
        db_port = os.getenv("DB_PORT")
        if db_port:
            self.database.port = int(db_port)
        self.database.user = os.getenv("DB_USER", self.database.user)
        self.database.password = os.getenv("DB_PASSWORD", self.database.password)
        self.database.database = os.getenv("DB_NAME", self.database.database)

        env = os.getenv("ENVIRONMENT")
        if env:
            self.platform.env = env

        debug = os.getenv("DEBUG")
        if debug:
            self.platform.debug = debug.lower() == "true"

    def validate(self) -> tuple[bool, list[str]]:
        """Validate required configuration."""
        errors = []

        if not self.delta.api_key:
            errors.append("DELTA_API_KEY is required")
        if not self.delta.api_secret:
            errors.append("DELTA_API_SECRET is required")

        return len(errors) == 0, errors

    @classmethod
    def get_instance(cls) -> "Config":
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# Singleton instance
config = Config.get_instance()
