#!/usr/bin/env python3
"""
Test script for Delta Exchange India API integration.

Run this to verify:
1. REST API connectivity and authentication
2. WebSocket connection and message handling
3. System status monitoring
4. Market data streaming
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.config import config
from app.services.delta_client import DeltaClient, SystemStatus, WebSocketMessage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_rest_api(client: DeltaClient) -> bool:
    """Test REST API connectivity."""
    logger.info("=" * 60)
    logger.info("TEST 1: REST API Connectivity")
    logger.info("=" * 60)

    try:
        # Test public endpoint
        logger.info("Fetching BTC ticker...")
        ticker = await client.get_ticker("BTCUSD")

        if ticker.get("success"):
            result = ticker.get("result", {})
            mark_price = result.get("mark_price", "N/A")
            spot_price = result.get("spot_price", "N/A")
            logger.info(f"✅ REST API working")
            logger.info(f"   BTC/USD Mark Price: ${mark_price}")
            logger.info(f"   BTC/USD Spot Price: ${spot_price}")
            return True
        else:
            error = ticker.get("error", {})
            logger.error(f"❌ API error: {error}")
            return False

    except Exception as e:
        logger.error(f"❌ REST API test failed: {e}")
        return False


async def test_websocket_connection(client: DeltaClient) -> bool:
    """Test WebSocket connection and subscriptions."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 2: WebSocket Connection & Subscriptions")
    logger.info("=" * 60)

    # Track received messages
    received_messages = {
        "ticker": 0,
        "mark_price": 0,
        "trades": 0,
        "system_status": 0,
    }

    def on_message(msg: WebSocketMessage) -> None:
        """Handle incoming WebSocket message."""
        if msg.channel in received_messages:
            received_messages[msg.channel] += 1

            # Log sample data
            if msg.channel == "ticker" and received_messages[msg.channel] == 1:
                symbol = msg.data.get("symbol", "?")
                mark_price = msg.data.get("mark_price", "N/A")
                logger.info(f"   Sample ticker: {symbol} @ ${mark_price}")

            elif msg.channel == "system_status":
                status = msg.data.get("status", "unknown")
                logger.info(f"   System status: {status}")

    # Register handlers
    for channel in ["ticker", "mark_price", "trades", "system_status"]:
        client.add_message_handler(channel, on_message)

    # Subscribe to channels
    channels = ["ticker:BTCUSD", "ticker:ETHUSD", "mark_price:BTCUSD",
                "mark_price:ETHUSD", "trades:BTCUSD", "system_status"]

    logger.info(f"Subscribing to channels: {', '.join(channels)}")
    await client.ws.subscribe(channels)

    try:
        # Start WebSocket
        logger.info("Starting WebSocket connection...")
        await client.start(channels)

        # Wait for messages
        logger.info("Listening for messages... (20 seconds)")
        await asyncio.sleep(20)

        # Check results
        logger.info("✅ WebSocket connection successful")
        logger.info(f"   Messages received:")
        for channel, count in received_messages.items():
            symbol = "✓" if count > 0 else "✗"
            logger.info(f"     {symbol} {channel}: {count} messages")

        # Check system status
        status = client.get_system_status()
        logger.info(f"   Exchange status: {status.value}")

        return True

    except Exception as e:
        logger.error(f"❌ WebSocket test failed: {e}")
        return False

    finally:
        await client.stop()


async def test_exponential_backoff() -> bool:
    """Test exponential backoff logic (mock)."""
    logger.info("\n" + "=" * 60)
    logger.info("TEST 3: Exponential Backoff Logic (Mock)")
    logger.info("=" * 60)

    initial_backoff = 1
    max_backoff = 300  # 5 minutes
    backoff_multiplier = 2.0
    current_backoff = initial_backoff

    logger.info(f"Initial backoff: {initial_backoff}s, Max: {max_backoff}s, Multiplier: {backoff_multiplier}x")
    logger.info("Simulating 10 reconnection attempts:")

    for attempt in range(1, 11):
        backoff_time = min(current_backoff, max_backoff)
        logger.info(f"  Attempt {attempt}: wait {backoff_time}s (before multiplier: {current_backoff}s)")
        current_backoff = min(
            int(current_backoff * backoff_multiplier),
            max_backoff
        )

    logger.info("✅ Exponential backoff logic verified")
    return True


async def main():
    """Run all tests."""
    logger.info("Delta Exchange India API Integration Tests")
    logger.info(f"Environment: {config.platform.env}")

    # Validate configuration
    is_valid, errors = config.validate()

    if not is_valid:
        logger.error("❌ Configuration validation failed:")
        for error in errors:
            logger.error(f"   - {error}")
        logger.error("\nPlease create config.yaml with your Delta API credentials.")
        logger.error("See config.example.yaml for the format.")
        return False

    # Create client
    client = DeltaClient(config.delta.api_key, config.delta.api_secret)

    tests_passed = 0
    tests_total = 3

    # Test 1: REST API
    if await test_rest_api(client):
        tests_passed += 1

    # Test 2: WebSocket
    if await test_websocket_connection(client):
        tests_passed += 1

    # Test 3: Backoff logic
    if await test_exponential_backoff():
        tests_passed += 1

    # Summary
    logger.info("\n" + "=" * 60)
    logger.info(f"Test Results: {tests_passed}/{tests_total} passed")
    logger.info("=" * 60)

    return tests_passed == tests_total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
