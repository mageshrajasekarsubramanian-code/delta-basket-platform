#!/usr/bin/env python3
"""
Test script for database layer (Step 3).

Tests:
1. Database initialization
2. Creating baskets and legs
3. Querying data
4. Updating P&L
5. Order logging
"""

import asyncio
import logging
import sys
from pathlib import Path
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.config import config
from app.models import (
    init_db, close_db, get_session,
    BasketRepository, LegRepository, OrderLogRepository,
    BasketState, LegStatus, OrderType, OrderState,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def test_database_init() -> bool:
    """Test database initialization."""
    logger.info("=" * 70)
    logger.info("TEST 1: Database Initialization")
    logger.info("=" * 70)

    try:
        await init_db()
        logger.info("✅ Database initialized successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        return False


async def test_create_basket() -> bool:
    """Test creating baskets and legs."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 2: Creating Baskets & Legs")
    logger.info("=" * 70)

    try:
        async with get_session() as session:
            basket_repo = BasketRepository(session)
            leg_repo = LegRepository(session)

            # Create a basket
            basket = await basket_repo.create(
                underlying="BTC",
                expiry_date="2026-09-19",
                lot_size=1,
                entry_timeout_minutes=15,
            )

            logger.info(f"✅ Created basket: {basket.id}")
            logger.info(f"   Underlying: {basket.underlying}")
            logger.info(f"   Expiry: {basket.expiry_date}")
            logger.info(f"   State: {basket.state.value}")

            # Create legs
            leg1 = await leg_repo.create(
                basket_id=basket.id,
                symbol="C-BTC-80000-190926",
                product_id=123456,
                strike=80000.0,
                option_type="call",
                side="buy",
                size=1,
            )

            leg2 = await leg_repo.create(
                basket_id=basket.id,
                symbol="P-BTC-75000-190926",
                product_id=123457,
                strike=75000.0,
                option_type="put",
                side="sell",
                size=1,
            )

            logger.info(f"✅ Created legs:")
            logger.info(f"   Leg 1: {leg1.symbol} {leg1.side} @ {leg1.strike}")
            logger.info(f"   Leg 2: {leg2.symbol} {leg2.side} @ {leg2.strike}")

            return True

    except Exception as e:
        logger.error(f"❌ Basket/leg creation failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_query_data() -> bool:
    """Test querying data."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 3: Querying Data")
    logger.info("=" * 70)

    try:
        async with get_session() as session:
            basket_repo = BasketRepository(session)

            # Get active baskets
            baskets = await basket_repo.get_active()
            logger.info(f"✅ Found {len(baskets)} active baskets")

            for basket in baskets:
                logger.info(f"   Basket {basket.id[:8]}... ({basket.underlying} {basket.expiry_date})")
                logger.info(f"      State: {basket.state.value}")
                logger.info(f"      Legs: {len(basket.legs)}")

                for leg in basket.legs:
                    logger.info(f"        - {leg.symbol} {leg.side} {leg.status.value}")

            return len(baskets) > 0

    except Exception as e:
        logger.error(f"❌ Query failed: {e}")
        return False


async def test_update_pnl() -> bool:
    """Test updating basket P&L."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 4: Updating P&L")
    logger.info("=" * 70)

    try:
        async with get_session() as session:
            basket_repo = BasketRepository(session)
            leg_repo = LegRepository(session)

            # Get first basket
            baskets = await basket_repo.get_active()
            if not baskets:
                logger.warning("⚠️  No active baskets to update")
                return True

            basket = baskets[0]

            # Update basket P&L
            await basket_repo.update_pnl(
                basket.id,
                realized_pnl=150.00,
                unrealized_pnl=25.50
            )

            logger.info(f"✅ Updated basket P&L:")
            logger.info(f"   Realized: ${basket.realized_pnl:.2f}")
            logger.info(f"   Unrealized: ${basket.unrealized_pnl:.2f}")

            # Update leg status and P&L
            if basket.legs:
                leg = basket.legs[0]

                await leg_repo.update_status(
                    leg.id,
                    status=LegStatus.FILLED,
                    fill_price=80100.50
                )

                await leg_repo.update_pnl(
                    leg.id,
                    current_price=80200.00,
                    unrealized_pnl=100.00
                )

                logger.info(f"✅ Updated leg {leg.symbol[:15]}...")
                logger.info(f"   Status: {leg.status.value}")
                logger.info(f"   Fill price: ${leg.fill_price:.2f}")
                logger.info(f"   Current: ${leg.current_price:.2f}")

            return True

    except Exception as e:
        logger.error(f"❌ P&L update failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_order_logging() -> bool:
    """Test order logging."""
    logger.info("\n" + "=" * 70)
    logger.info("TEST 5: Order Logging")
    logger.info("=" * 70)

    try:
        async with get_session() as session:
            basket_repo = BasketRepository(session)
            order_repo = OrderLogRepository(session)

            # Get first basket
            baskets = await basket_repo.get_active()
            if not baskets or not baskets[0].legs:
                logger.warning("⚠️  No baskets with legs to log orders")
                return True

            leg = baskets[0].legs[0]

            # Log an order
            order_log = await order_repo.create(
                leg_id=leg.id,
                delta_order_id="DLT-ORDER-12345",
                order_type=OrderType.LIMIT,
                price=80100.50,
                size=1,
                state=OrderState.OPEN,
                reason="initial",
                notes="Initial entry order"
            )

            logger.info(f"✅ Logged order:")
            logger.info(f"   Delta Order ID: {order_log.delta_order_id}")
            logger.info(f"   Price: ${order_log.price:.2f}")
            logger.info(f"   State: {order_log.state.value}")

            # Log a fill
            await order_repo.update_state(
                order_log.id,
                state=OrderState.FILLED,
                filled_size=1
            )

            logger.info(f"✅ Updated order state: FILLED")

            # Get order history
            orders = await order_repo.get_by_leg(leg.id)
            logger.info(f"✅ Order history for leg:")
            for order in orders:
                logger.info(f"   - {order.delta_order_id} {order.state.value}")

            return True

    except Exception as e:
        logger.error(f"❌ Order logging failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Run all tests."""
    logger.info("\n")
    logger.info("╔═══════════════════════════════════════════════════════════════════╗")
    logger.info("║        Basket/Leg Data Model - Database Tests (Step 3)           ║")
    logger.info("╚═══════════════════════════════════════════════════════════════════╝")
    logger.info("")

    tests_passed = 0
    tests_total = 5

    try:
        # Test 1: DB init
        if await test_database_init():
            tests_passed += 1

        # Test 2: Create baskets
        if await test_create_basket():
            tests_passed += 1

        # Test 3: Query data
        if await test_query_data():
            tests_passed += 1

        # Test 4: Update P&L
        if await test_update_pnl():
            tests_passed += 1

        # Test 5: Order logging
        if await test_order_logging():
            tests_passed += 1

    finally:
        # Cleanup
        logger.info("\nCleaning up...")
        await close_db()

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info(f"TEST RESULTS: {tests_passed}/{tests_total} tests passed")
    logger.info("=" * 70)

    if tests_passed == tests_total:
        logger.info("✅ Database layer is working correctly!")
        logger.info("\nNext step: Build Order Execution Service (Step 4)")
    else:
        logger.info("\n⚠️  Some tests failed. Check output above for details.")

    logger.info("")
    return tests_passed == tests_total


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
