"""
FastAPI main application.

Phase 1 (current): Placeholder with basic structure
Phase 2+: Will add market data service, order execution, basket engine, etc.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import config
from app.services.delta_client import DeltaClient
from app.services.market_data_service import MarketDataService

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.platform.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Global service instances
delta_client: DeltaClient = None
market_data_service: MarketDataService = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifecycle manager.

    Startup: Initialize Delta client and services
    Shutdown: Clean up connections
    """
    global delta_client, market_data_service

    logger.info(f"Starting Delta Basket Platform ({config.platform.env} mode)")

    # Validate configuration
    is_valid, errors = config.validate()
    if not is_valid:
        logger.error("Configuration validation failed:")
        for error in errors:
            logger.error(f"  - {error}")
        raise RuntimeError("Invalid configuration")

    # Initialize Delta client
    logger.info("Initializing Delta Exchange client...")
    delta_client = DeltaClient(
        config.delta.api_key,
        config.delta.api_secret
    )

    # Start WebSocket (background)
    if config.delta.ws_reconnect_enabled:
        logger.info("Starting WebSocket connection...")
        try:
            await delta_client.start(config.delta.public_channels)
            logger.info("✅ WebSocket started (running in background)")
        except Exception as e:
            logger.error(f"Failed to start WebSocket: {e}")
            # Don't fail startup, will retry on reconnection

    # Initialize Market Data Service
    if config.platform.enable_market_data_service:
        logger.info("Starting Market Data Service...")
        try:
            market_data_service = MarketDataService(delta_client)
            await market_data_service.start()
            logger.info("✅ Market Data Service started")
        except Exception as e:
            logger.error(f"Failed to start Market Data Service: {e}")
            market_data_service = None

    yield  # App runs here

    # Shutdown
    logger.info("Shutting down Delta Basket Platform...")
    if market_data_service:
        await market_data_service.stop()
    if delta_client:
        await delta_client.stop()
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Delta Basket Platform",
    description="Crypto options basket trading on Delta Exchange India",
    version="0.1.0",
    lifespan=lifespan
)

# Add CORS middleware for dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: Restrict to frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Health check endpoint
@app.get("/health")
async def health_check():
    """Check platform health and Delta Exchange status."""
    global delta_client, market_data_service

    if not delta_client:
        return {
            "status": "initializing",
            "delta_ws_connected": False,
            "delta_status": "unknown",
            "market_data_service": None
        }

    mds_info = None
    if market_data_service:
        mds_info = {
            "running": market_data_service.running,
            "futures_count": len(market_data_service.futures),
            "options_count": len(market_data_service.options),
            "tickers_cached": len(market_data_service.tickers),
            "expiry_dates": market_data_service.get_expiry_dates(),
        }

    return {
        "status": "ok",
        "delta_ws_connected": delta_client.ws.connected,
        "delta_status": delta_client.get_system_status().value,
        "market_data_service": mds_info,
    }


@app.get("/")
async def root():
    """API root."""
    return {
        "message": "Delta Basket Platform API",
        "docs": "/docs",
        "health": "/health"
    }


# Phase 2+ endpoints will be added here:
# - POST /baskets - create a new basket
# - GET /baskets - list all baskets
# - GET /baskets/{id} - get basket details
# - POST /baskets/{id}/close - manually close a basket
# - GET /baskets/{id}/legs - get legs for a basket
# - WebSocket /ws - push live P&L and market data to dashboard


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config.platform.host,
        port=config.platform.port,
        reload=config.platform.reload if config.platform.env == "development" else False
    )
