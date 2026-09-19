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
from app.services.order_execution_service import OrderExecutionService
from app.services.pnl_engine import PnLEngine
from app.models import init_db, close_db

logger = logging.getLogger(__name__)

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.platform.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Global service instances
delta_client: DeltaClient = None
market_data_service: MarketDataService = None
order_execution_service: OrderExecutionService = None
pnl_engine: PnLEngine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifecycle manager.

    Startup: Initialize Delta client and services
    Shutdown: Clean up connections
    """
    global delta_client, market_data_service, order_execution_service, pnl_engine

    logger.info(f"Starting Delta Basket Platform ({config.platform.env} mode)")

    # Validate configuration
    is_valid, errors = config.validate()
    if not is_valid:
        logger.error("Configuration validation failed:")
        for error in errors:
            logger.error(f"  - {error}")
        raise RuntimeError("Invalid configuration")

    # Initialize database
    logger.info("Initializing database...")
    try:
        await init_db()
        logger.info("✅ Database initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise RuntimeError("Database initialization failed")

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

    # Initialize Order Execution Service
    if config.platform.enable_order_execution and market_data_service:
        logger.info("Starting Order Execution Service...")
        try:
            order_execution_service = OrderExecutionService(delta_client, market_data_service)
            await order_execution_service.start()
            logger.info("✅ Order Execution Service started")
        except Exception as e:
            logger.error(f"Failed to start Order Execution Service: {e}")
            order_execution_service = None

    # Initialize P&L Engine
    if market_data_service:
        logger.info("Starting P&L Engine...")
        try:
            pnl_engine = PnLEngine(market_data_service)
            await pnl_engine.start()
            logger.info("✅ P&L Engine started")
        except Exception as e:
            logger.error(f"Failed to start P&L Engine: {e}")
            pnl_engine = None

    yield  # App runs here

    # Shutdown
    logger.info("Shutting down Delta Basket Platform...")
    if pnl_engine:
        await pnl_engine.stop()
    if order_execution_service:
        await order_execution_service.stop()
    if market_data_service:
        await market_data_service.stop()
    if delta_client:
        await delta_client.stop()
    await close_db()
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
    global delta_client, market_data_service, order_execution_service, pnl_engine

    if not delta_client:
        return {
            "status": "initializing",
            "delta_ws_connected": False,
            "delta_status": "unknown",
            "market_data_service": None,
            "order_execution_service": None,
            "pnl_engine": None
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

    oes_info = None
    if order_execution_service:
        oes_info = {
            "running": order_execution_service.running,
            "active_baskets": len(order_execution_service.active_baskets),
        }

    pnl_info = None
    if pnl_engine:
        pnl_info = {
            "running": pnl_engine.running,
            "tracked_baskets": len(pnl_engine.tracked_baskets),
        }

    return {
        "status": "ok",
        "delta_ws_connected": delta_client.ws.connected,
        "delta_status": delta_client.get_system_status().value,
        "market_data_service": mds_info,
        "order_execution_service": oes_info,
        "pnl_engine": pnl_info,
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
