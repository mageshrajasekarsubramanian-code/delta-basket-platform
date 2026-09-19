#!/usr/bin/env python3
"""
Test script for Market Data Service (Step 2).

Tests:
1. Instrument loading (futures + options)
2. Expiry-based filtering
3. Ticker subscriptions
4. Live market data streaming
"""

import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.config import config
from app.services.delta_client import DeltaClient
from app.services.market_data_service import MarketDataService, TickerUpdate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_instrument_loading(mds: MarketDataService) -> bool:
    """Test loading and filtering instruments."""
    logger.info("=" * 70)
    logger.info("TEST 1: Instrument Loading & Filtering")
    logger.info("=" * 70)

    try:
        # Check futures
        btc_futures = mds.get_futures(underlying="BTC")
        eth_futures = mds.get_futures(underlying="ETH")

        logger.info(f"✅ Loaded futures:")
        logger.info(f"   BTC: {[f.symbol for f in btc_futures]}")
        logger.info(f"   ETH: {[f.symbol for f in eth_futures]}")

        if not btc_futures or not eth_futures:
            logger.warning("⚠️  Expected at least one BTC and one ETH future")

        # Check options
        expiry_dates = mds.get_expiry_dates()
        logger.info(f"\n✅ Option expiry dates: {expiry_dates}")

        for expiry in expiry_dates:
            btc_opts = mds.get_options(underlying="BTC", expiry_date=expiry)
            eth_opts = mds.get_options(underlying="ETH", expiry_date=expiry)

            logger.info(f"   {expiry}: {len(btc_opts)} BTC + {len(eth_opts)} ETH options")

            # Show sample strikes
            if btc_opts:
                btc_strikes = sorted(set(o.strike for o in btc_opts))
                logger.info(f"      BTC strikes: {btc_strikes[:5]}..." if len(btc_strikes) > 5 else f"      BTC strikes: {btc_strikes}")

        return len(btc_futures) > 0 and len(eth_futures) > 0 and len(expiry_dates) > 0

    except Exception as e:
        logger.error(f"❌ Instrument loading test failed: {e}")
        return False


async def test_ticker_subscription(mds: MarketDataService) -> bool:
    """Test ticker subscription and live updates."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: Ticker Subscription & Live Updates")
    logger.info("=" * 70)

    # Get futures to subscribe to
    futures = mds.get_futures()
    if not futures:
        logger.error("❌ No futures available to subscribe to")
        return False

    # Pick first future (should be BTCUSD or similar)
    symbol = futures[0].symbol
    logger.info(f"Subscribing to {symbol} ticker...")

    received_ticks = []

    def on_ticker(update: TickerUpdate):
        """Handle ticker update."""
        received_ticks.append(update)
        if len(received_ticks) == 1:
            logger.info(f"   Received first tick: {symbol} @ ${update.mark_price}")

    try:
        mds.subscribe_ticker(symbol, on_ticker)

        # Wait for ticks (10 seconds)
        logger.info("Listening for ticker updates... (10 seconds)")
        await asyncio.sleep(10)

        mds.unsubscribe_ticker(symbol, on_ticker)

        logger.info(f"✅ Ticker subscription test passed")
        logger.info(f"   Received {len(received_ticks)} ticker updates in 10 seconds")

        if received_ticks:
            logger.info(f"   Latest: {symbol} @ ${received_ticks[-1].mark_price}")

        return len(received_ticks) > 0

    except Exception as e:
        logger.error(f"❌ Ticker subscription test failed: {e}")
        return False


async def test_option_chain_by_expiry(mds: MarketDataService) -> bool:
    """Test option chain retrieval by expiry."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 3: Option Chain Filtering by Expiry")
    logger.info("=" * 70)

    try:
        expiry_dates = mds.get_expiry_dates()

        if not expiry_dates:
            logger.error("❌ No option expiry dates available")
            return False

        # Test each expiry
        for expiry in expiry_dates:
            btc_options = mds.get_options(underlying="BTC", expiry_date=expiry)
            eth_options = mds.get_options(underlying="ETH", expiry_date=expiry)

            logger.info(f"\n✅ {expiry}:")
            logger.info(f"   BTC: {len(btc_options)} options")
            logger.info(f"   ETH: {len(eth_options)} options")

            # Show some details
            if btc_options:
                # Separate calls and puts
                btc_calls = [o for o in btc_options if "call" in o.contract_type.lower()]
                btc_puts = [o for o in btc_options if "put" in o.contract_type.lower()]

                btc_call_strikes = sorted(set(o.strike for o in btc_calls))
                btc_put_strikes = sorted(set(o.strike for o in btc_puts))

                logger.info(f"      Calls: {len(btc_calls)} ({len(btc_call_strikes)} strikes)")
                logger.info(f"      Puts: {len(btc_puts)} ({len(btc_put_strikes)} strikes)")

        return True

    except Exception as e:
        logger.error(f"❌ Option chain test failed: {e}")
        return False


async def test_multiple_subscriptions(mds: MarketDataService) -> bool:
    """Test multiple ticker subscriptions."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 4: Multiple Ticker Subscriptions")
    logger.info("=" * 70)

    try:
        # Get a few futures
        futures = mds.get_futures()
        if len(futures) < 2:
            logger.warning("⚠️  Only one futures contract available, skipping multi-subscription test")
            return True

        # Subscribe to multiple
        symbols = [f.symbol for f in futures[:2]]  # BTC and ETH
        received = {s: [] for s in symbols}

        def make_handler(symbol):
            def handler(update: TickerUpdate):
                received[symbol].append(update)
            return handler

        for symbol in symbols:
            mds.subscribe_ticker(symbol, make_handler(symbol))

        logger.info(f"Subscribed to {symbols}")
        logger.info("Listening for 8 seconds...")

        await asyncio.sleep(8)

        # Unsubscribe
        for symbol in symbols:
            mds.unsubscribe_ticker(symbol, make_handler(symbol))

        logger.info("✅ Multiple subscription test passed")
        for symbol, updates in received.items():
            logger.info(f"   {symbol}: {len(updates)} updates")

        return all(len(updates) > 0 for updates in received.values())

    except Exception as e:
        logger.error(f"❌ Multiple subscription test failed: {e}")
        return False


async def main():
    """Run all tests."""
    logger.info("\n")
    logger.info("╔═══════════════════════════════════════════════════════════════════╗")
    logger.info("║           Market Data Service - Integration Tests (Step 2)        ║")
    logger.info("╚═══════════════════════════════════════════════════════════════════╝")
    logger.info("")

    # Validate config
    is_valid, errors = config.validate()
    if not is_valid:
        logger.error("❌ Configuration validation failed:")
        for error in errors:
            logger.error(f"   - {error}")
        return False

    # Create clients
    delta_client = DeltaClient(config.delta.api_key, config.delta.api_secret)
    mds = MarketDataService(delta_client)

    tests_passed = 0
    tests_total = 4

    try:
        # Start services
        logger.info("Starting Market Data Service...")
        await delta_client.start(config.delta.public_channels)
        await mds.start()

        # Wait for data to populate
        logger.info("Waiting for initial data load... (5 seconds)")
        await asyncio.sleep(5)

        # Run tests
        if await test_instrument_loading(mds):
            tests_passed += 1

        if await test_ticker_subscription(mds):
            tests_passed += 1

        if await test_option_chain_by_expiry(mds):
            tests_passed += 1

        if await test_multiple_subscriptions(mds):
            tests_passed += 1

    except Exception as e:
        logger.error(f"Test execution failed: {e}")

    finally:
        # Cleanup
        logger.info("\nCleaning up...")
        await mds.stop()
        await delta_client.stop()

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info(f"TEST RESULTS: {tests_passed}/{tests_total} tests passed")
    logger.info("=" * 70)

    if tests_passed == tests_total:
        logger.info("✅ Market Data Service is working correctly!")
        logger.info("\nNext step: Build Basket/Leg data model + PostgreSQL (Step 3)")
    else:
        logger.info("\n⚠️  Some tests failed. Check the output above for details.")

    logger.info("")
    return tests_passed == tests_total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
