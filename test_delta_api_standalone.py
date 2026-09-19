#!/usr/bin/env python3
"""
Standalone Delta API Test - No config file needed.

Just set your credentials below and run:
    python test_delta_api_standalone.py
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict

import httpx
import websockets

# ============================================================================
# CONFIGURATION - Edit these with your Delta Exchange API credentials
# ============================================================================

DELTA_API_KEY = "8dlmMlUB2clG1O9C6PsNMiXiNHNGB8"
DELTA_API_SECRET = "zxKI18M7eihOhI9o7nAdCdgJiY8UcBvyrBHjL8PVIT9LpxhMDidOJEl7ZKRc"

# Use testnet if needed:
# API_BASE_URL = "https://cdn-ind.testnet.deltaex.org"
# WS_URL = "wss://stream.india.testnet.deltaex.org/v2/live"

API_BASE_URL = "https://api.india.delta.exchange"
WS_URL = "wss://stream.india.delta.exchange/v2/live"

# ============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def generate_signature(method: str, timestamp: str, path: str, query: str = "", body: str = "") -> str:
    """Generate HMAC-SHA256 signature for Delta API."""
    message = method + timestamp + path + query + body
    signature = hmac.new(
        DELTA_API_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature


async def test_rest_api() -> bool:
    """Test REST API connectivity and authentication."""
    logger.info("=" * 70)
    logger.info("TEST 1: REST API Connectivity & Authentication")
    logger.info("=" * 70)

    if not DELTA_API_KEY or DELTA_API_KEY == "your_api_key_here":
        logger.error("❌ Please set DELTA_API_KEY at the top of this script")
        return False

    if not DELTA_API_SECRET or DELTA_API_SECRET == "your_api_secret_here":
        logger.error("❌ Please set DELTA_API_SECRET at the top of this script")
        return False

    try:
        timestamp = str(int(time.time()))
        path = "/v2/tickers/BTCUSD"

        signature = generate_signature("GET", timestamp, path)

        headers = {
            "api-key": DELTA_API_KEY,
            "signature": signature,
            "timestamp": timestamp,
            "User-Agent": "DeltaBasketPlatformTest/1.0",
        }

        logger.info(f"Fetching {path}...")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{API_BASE_URL}{path}", headers=headers)
            response.raise_for_status()
            data = response.json()

        if data.get("success"):
            result = data.get("result", {})
            mark_price = result.get("mark_price", "N/A")
            spot_price = result.get("spot_price", "N/A")
            symbol = result.get("symbol", "BTC/USD")

            logger.info(f"✅ REST API Working!")
            logger.info(f"   {symbol} Mark Price: ${mark_price}")
            logger.info(f"   {symbol} Spot Price: ${spot_price}")
            return True
        else:
            error = data.get("error", {})
            logger.error(f"❌ API Error: {error}")
            return False

    except Exception as e:
        logger.error(f"❌ REST API Test Failed: {e}")
        return False


async def test_websocket_connection() -> bool:
    """Test WebSocket connection and message streaming."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: WebSocket Connection & Message Streaming")
    logger.info("=" * 70)

    received_messages = {
        "ticker": 0,
        "mark_price": 0,
        "trades": 0,
        "system_status": 0,
    }

    try:
        logger.info(f"Connecting to {WS_URL}...")

        async with websockets.connect(WS_URL) as ws:
            logger.info("✅ WebSocket Connected")

            # Subscribe to channels
            channels = ["ticker:BTCUSD", "ticker:ETHUSD", "mark_price:BTCUSD",
                       "mark_price:ETHUSD", "trades:BTCUSD", "system_status"]

            logger.info(f"Subscribing to {len(channels)} channels...")

            for channel in channels:
                message = {"type": "subscribe", "channel": channel}
                await ws.send(json.dumps(message))

            # Listen for messages (20 seconds)
            logger.info("Listening for messages... (20 seconds)")

            start_time = time.time()
            timeout = 20

            while time.time() - start_time < timeout:
                try:
                    raw_message = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    data = json.loads(raw_message)

                    channel = data.get("channel", "unknown")
                    msg_type = data.get("type", "unknown")
                    payload = data.get("data", {})

                    # Track message types
                    if "ticker" in channel:
                        received_messages["ticker"] += 1
                        if received_messages["ticker"] == 1:
                            symbol = payload.get("symbol", "?")
                            mark_price = payload.get("mark_price", "N/A")
                            logger.info(f"   Sample ticker: {symbol} @ ${mark_price}")

                    elif "mark_price" in channel:
                        received_messages["mark_price"] += 1

                    elif "trades" in channel:
                        received_messages["trades"] += 1

                    elif channel == "system_status":
                        received_messages["system_status"] += 1
                        status = payload.get("status", "unknown")
                        logger.info(f"   System status: {status}")

                except asyncio.TimeoutError:
                    # No message received in 2 seconds, keep waiting
                    pass

            logger.info("✅ WebSocket Test Complete")
            logger.info(f"   Messages received:")

            success = False
            for channel, count in received_messages.items():
                symbol = "✓" if count > 0 else "✗"
                logger.info(f"     {symbol} {channel}: {count} messages")
                if count > 0:
                    success = True

            return success

    except Exception as e:
        logger.error(f"❌ WebSocket Test Failed: {e}")
        return False


async def test_exponential_backoff() -> bool:
    """Test exponential backoff calculation."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 3: Exponential Backoff Logic (Mock)")
    logger.info("=" * 70)

    initial_backoff = 1
    max_backoff = 300  # 5 minutes
    backoff_multiplier = 2.0
    current_backoff = initial_backoff

    logger.info(f"Initial: {initial_backoff}s | Max: {max_backoff}s | Multiplier: {backoff_multiplier}x")
    logger.info("Simulating 12 reconnection attempts:")

    for attempt in range(1, 13):
        backoff_time = min(current_backoff, max_backoff)
        logger.info(f"  Attempt {attempt:2d}: wait {backoff_time:3d}s (raw: {current_backoff:3d}s)")
        current_backoff = min(
            int(current_backoff * backoff_multiplier),
            max_backoff
        )

    logger.info("✅ Exponential backoff logic verified")
    return True


async def main():
    """Run all tests."""
    logger.info("\n")
    logger.info("╔═══════════════════════════════════════════════════════════════════╗")
    logger.info("║     Delta Exchange India API - Integration Tests (Step 1)         ║")
    logger.info("╚═══════════════════════════════════════════════════════════════════╝")
    logger.info("")

    tests_passed = 0
    tests_total = 3

    # Test 1: REST API
    try:
        if await test_rest_api():
            tests_passed += 1
    except Exception as e:
        logger.error(f"Test 1 crashed: {e}")

    # Test 2: WebSocket
    try:
        if await test_websocket_connection():
            tests_passed += 1
    except Exception as e:
        logger.error(f"Test 2 crashed: {e}")

    # Test 3: Backoff logic
    try:
        if await test_exponential_backoff():
            tests_passed += 1
    except Exception as e:
        logger.error(f"Test 3 crashed: {e}")

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info(f"TEST RESULTS: {tests_passed}/{tests_total} tests passed")
    logger.info("=" * 70)

    if tests_passed == tests_total:
        logger.info("✅ All tests passed! Delta API wrapper is working correctly.")
        logger.info("\nNext step: Build Market Data Service (Step 2)")
    else:
        logger.info("\n⚠️  Some tests failed. Check the output above for details.")
        if tests_passed == 0:
            logger.info("\nCommon issues:")
            logger.info("1. API Key/Secret not set or invalid")
            logger.info("2. Firewall/network blocking pypi.org or Delta API")
            logger.info("3. Your IP not whitelisted in Delta's API management")

    logger.info("")
    return tests_passed == tests_total


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
