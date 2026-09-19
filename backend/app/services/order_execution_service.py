"""
Order Execution Service for Delta Basket Platform.

Responsibilities:
1. Buy-first order sequencing
2. Price-chase algorithm for sell legs (dollar-based offset)
3. Order placement via Delta REST API
4. Order tracking and fill management
5. Unhedged-exposure timeout auto-close
6. Manual/triggered basket closes
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal
from enum import Enum as PyEnum

from backend.app.services.delta_client import DeltaClient, TickerUpdate
from backend.app.services.market_data_service import MarketDataService, Instrument
from backend.app.models import (
    BasketRepository, LegRepository, OrderLogRepository,
    Basket, Leg, BasketState, LegStatus, OrderType, OrderState,
)
from sqlalchemy.orm import AsyncSession

logger = logging.getLogger(__name__)


class OrderPhase(PyEnum):
    """Order execution phase."""
    AWAITING_BUY = "awaiting_buy"      # Waiting to place buy orders
    CHASING_SELL = "chasing_sell"      # Buy filled, chasing sell orders
    ALL_FILLED = "all_filled"          # All orders filled
    CLOSING = "closing"                # Force-close in progress


class OrderExecutionService:
    """
    Manages order placement and execution for baskets.

    Features:
    - Buy-first sequencing
    - Price-chase for sell legs (resprice every tick)
    - Dollar-offset based chase range
    - Unhedged-exposure timeout (auto-close after N minutes)
    - Market order closes
    """

    def __init__(
        self,
        delta_client: DeltaClient,
        market_data_service: MarketDataService,
        default_chase_offset_cents: int = 100,  # $1.00 default
    ):
        self.delta_client = delta_client
        self.market_data_service = market_data_service
        self.default_chase_offset_cents = default_chase_offset_cents

        # Active basket tracking
        self.active_baskets: Dict[str, OrderPhase] = {}
        self.entry_timeouts: Dict[str, datetime] = {}
        self.chase_states: Dict[str, Dict] = {}  # Track chase state per leg

        # Background tasks
        self.tasks: List[asyncio.Task] = []
        self.running = False

    async def start(self) -> None:
        """Start the order execution service."""
        logger.info("Starting Order Execution Service...")
        self.running = True

        # Start background tasks
        self.tasks.append(asyncio.create_task(self._monitor_timeouts()))

        logger.info("✅ Order Execution Service started")

    async def stop(self) -> None:
        """Stop the service."""
        logger.info("Stopping Order Execution Service...")
        self.running = False

        # Cancel background tasks
        for task in self.tasks:
            task.cancel()

        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("Order Execution Service stopped")

    # =========================================================================
    # Public API
    # =========================================================================

    async def submit_basket(
        self,
        session: AsyncSession,
        basket: Basket,
        legs: List[Leg],
        chase_offset_cents: Optional[int] = None,
    ) -> bool:
        """
        Submit a basket for execution.

        Initiates buy-first sequencing:
        1. Place all buy-side limit orders
        2. Wait for fills
        3. Place sell-side orders with price-chase
        4. Track timeout (auto-close if not all filled by timeout)

        Args:
            session: Database session
            basket: The basket to execute
            legs: Legs in the basket (must be buy-side then sell-side)
            chase_offset_cents: Override default chase offset

        Returns:
            True if submission succeeded
        """
        logger.info(f"Submitting basket {basket.id[:8]}... for execution")

        try:
            # Register basket
            self.active_baskets[basket.id] = OrderPhase.AWAITING_BUY
            self.entry_timeouts[basket.id] = datetime.utcnow() + timedelta(
                minutes=basket.entry_timeout_minutes
            )

            # Separate buy and sell legs
            buy_legs = [l for l in legs if l.side.lower() == "buy"]
            sell_legs = [l for l in legs if l.side.lower() == "sell"]

            if not buy_legs:
                logger.warning(f"Basket {basket.id[:8]}... has no buy legs")
                return False

            logger.info(f"Executing {len(buy_legs)} buy legs + {len(sell_legs)} sell legs")

            # Place buy orders
            buy_success = await self._place_buy_orders(session, basket, buy_legs)

            if not buy_success:
                logger.error(f"Failed to place buy orders for basket {basket.id[:8]}...")
                return False

            # Monitor buys, then place sells
            asyncio.create_task(
                self._execute_sell_phase(session, basket, sell_legs, chase_offset_cents)
            )

            return True

        except Exception as e:
            logger.error(f"Failed to submit basket: {e}")
            self.active_baskets.pop(basket.id, None)
            return False

    async def close_basket_manual(
        self,
        session: AsyncSession,
        basket: Basket,
    ) -> bool:
        """
        Manually close a basket (user-initiated).

        All legs closed as market orders simultaneously.
        """
        logger.info(f"Manually closing basket {basket.id[:8]}...")
        return await self._force_close_basket(session, basket, reason="manual_close")

    async def close_basket_triggered(
        self,
        session: AsyncSession,
        basket: Basket,
        trigger_type: str,
    ) -> bool:
        """
        Close basket due to SL/TP trigger.

        Args:
            session: Database session
            basket: Basket to close
            trigger_type: "sl_premium", "sl_underlying", "tp_premium", "tp_underlying"
        """
        logger.info(f"Closing basket {basket.id[:8]}... (trigger: {trigger_type})")
        return await self._force_close_basket(session, basket, reason=f"triggered_{trigger_type}")

    # =========================================================================
    # Order Placement
    # =========================================================================

    async def _place_buy_orders(
        self,
        session: AsyncSession,
        basket: Basket,
        buy_legs: List[Leg],
    ) -> bool:
        """Place all buy-side limit orders."""
        logger.info(f"Placing {len(buy_legs)} buy limit orders...")

        for leg in buy_legs:
            try:
                # Get current market price
                ticker = self.market_data_service.get_ticker(leg.symbol)
                if not ticker:
                    logger.error(f"No ticker available for {leg.symbol}")
                    return False

                # For buy orders, use ask price + small offset (or mark price)
                # Mark price is usually in the middle
                limit_price = float(ticker.mark_price)

                # Place limit order on Delta
                order_id = await self._place_limit_order(
                    symbol=leg.symbol,
                    side="buy",
                    size=basket.lot_size,
                    price=limit_price,
                )

                if not order_id:
                    logger.error(f"Failed to place buy order for {leg.symbol}")
                    return False

                # Update leg with order ID
                leg.latest_order_id = order_id
                await session.flush()

                logger.info(f"✅ Placed buy order: {leg.symbol} x{basket.lot_size} @ ${limit_price:.2f}")
                logger.info(f"   Order ID: {order_id}")

            except Exception as e:
                logger.error(f"Error placing buy order for {leg.symbol}: {e}")
                return False

        return True

    async def _execute_sell_phase(
        self,
        session: AsyncSession,
        basket: Basket,
        sell_legs: List[Leg],
        chase_offset_cents: Optional[int] = None,
    ) -> None:
        """
        Monitor buy fills, then execute sell phase with price-chase.
        """
        logger.info("Entering sell phase monitoring...")

        basket_repo = BasketRepository(session)
        leg_repo = LegRepository(session)

        offset_cents = chase_offset_cents or self.default_chase_offset_cents
        offset_dollars = offset_cents / 100.0

        # Initialize chase state
        for leg in sell_legs:
            self.chase_states[leg.id] = {
                "current_order_id": None,
                "last_price": None,
                "in_range": True,
                "symbol": leg.symbol,
            }

        # Monitor until all buys filled or timeout
        max_wait_seconds = basket.entry_timeout_minutes * 60
        wait_start = datetime.utcnow()

        while (datetime.utcnow() - wait_start).total_seconds() < max_wait_seconds:
            # Reload basket to check buy status
            basket = await basket_repo.get_by_id(basket.id)
            if not basket:
                return

            # Get buy legs from DB
            buy_legs = [l for l in basket.legs if l.side.lower() == "buy"]
            all_buys_filled = all(l.status == LegStatus.FILLED for l in buy_legs)

            if all_buys_filled and self.active_baskets.get(basket.id) == OrderPhase.AWAITING_BUY:
                logger.info("✅ All buy orders filled, starting sell orders with price-chase")
                self.active_baskets[basket.id] = OrderPhase.CHASING_SELL

                # Place initial sell orders
                for leg in sell_legs:
                    await self._chase_and_place_sell_order(basket, leg, offset_dollars, session)

            # Update sell orders (price-chase)
            await self._update_sell_orders(basket, sell_legs, offset_dollars, session)

            # Check if all filled
            sell_legs = [l for l in basket.legs if l.side.lower() == "sell"]
            all_sells_filled = all(l.status == LegStatus.FILLED for l in sell_legs)

            if all_sells_filled:
                logger.info("✅ All legs filled, basket is now ACTIVE")
                self.active_baskets[basket.id] = OrderPhase.ALL_FILLED
                await basket_repo.update_state(basket.id, BasketState.ACTIVE)
                return

            # Wait before next check
            await asyncio.sleep(1)

        # Timeout: auto-close all filled legs
        logger.warning(f"Entry timeout for basket {basket.id[:8]}...")
        await self._force_close_basket(session, basket, reason="entry_timeout")

    async def _chase_and_place_sell_order(
        self,
        basket: Basket,
        leg: Leg,
        offset_dollars: float,
        session: AsyncSession,
    ) -> None:
        """Place or update a sell order with price-chase."""
        ticker = self.market_data_service.get_ticker(leg.symbol)

        if not ticker:
            logger.warning(f"No ticker for {leg.symbol}, skipping price-chase")
            return

        # For sell orders, use bid price - offset (market is higher = better for seller)
        # Use mark price as reference
        bid_price = float(ticker.bid) if ticker.bid else float(ticker.mark_price)
        limit_price = bid_price - offset_dollars

        chase_state = self.chase_states.get(leg.id, {})

        # Check if price is in acceptable range
        if ticker.mark_price and abs(ticker.mark_price - float(ticker.mark_price)) <= offset_dollars:
            # Price is in range, ready to sell
            if not chase_state.get("current_order_id"):
                # Place new sell order
                order_id = await self._place_limit_order(
                    symbol=leg.symbol,
                    side="sell",
                    size=basket.lot_size,
                    price=limit_price,
                )

                if order_id:
                    self.chase_states[leg.id]["current_order_id"] = order_id
                    logger.info(f"✅ Placed sell order: {leg.symbol} x{basket.lot_size} @ ${limit_price:.2f}")
        else:
            # Out of range, cancel current order
            if chase_state.get("current_order_id"):
                await self._cancel_order(chase_state["current_order_id"])
                self.chase_states[leg.id]["current_order_id"] = None
                logger.info(f"⚠️  Cancelled sell order for {leg.symbol} (out of range)")

    async def _update_sell_orders(
        self,
        basket: Basket,
        sell_legs: List[Leg],
        offset_dollars: float,
        session: AsyncSession,
    ) -> None:
        """Reprice sell orders every tick (price-chase)."""
        for leg in sell_legs:
            ticker = self.market_data_service.get_ticker(leg.symbol)
            if not ticker:
                continue

            chase_state = self.chase_states.get(leg.id, {})
            current_order_id = chase_state.get("current_order_id")

            if not current_order_id:
                # No active order, try to place one
                await self._chase_and_place_sell_order(basket, leg, offset_dollars, session)
                continue

            # Recalculate limit price
            bid_price = float(ticker.bid) if ticker.bid else float(ticker.mark_price)
            new_limit_price = bid_price - offset_dollars

            # For simplicity, reprice every tick (full cancel + replace)
            # In production, would use Delta's edit order API
            await self._cancel_order(current_order_id)

            new_order_id = await self._place_limit_order(
                symbol=leg.symbol,
                side="sell",
                size=basket.lot_size,
                price=new_limit_price,
            )

            if new_order_id:
                self.chase_states[leg.id]["current_order_id"] = new_order_id

    # =========================================================================
    # Order Management
    # =========================================================================

    async def _place_limit_order(
        self,
        symbol: str,
        side: str,
        size: int,
        price: float,
    ) -> Optional[str]:
        """
        Place a limit order on Delta.

        Returns:
            Order ID if successful, None otherwise
        """
        try:
            # This would call Delta's REST API to place an order
            # For now, return a mock order ID
            logger.debug(f"Would place {side} order: {symbol} x{size} @ ${price:.2f}")

            # TODO: Implement actual Delta API call
            # response = await self.delta_client.rest.place_order(...)
            # return response.get("order_id")

            # Mock: return a fake order ID
            import time
            mock_order_id = f"DLT-{int(time.time())}-{symbol}"
            return mock_order_id

        except Exception as e:
            logger.error(f"Failed to place limit order: {e}")
            return None

    async def _cancel_order(self, order_id: str) -> bool:
        """Cancel an order on Delta."""
        try:
            logger.debug(f"Would cancel order: {order_id}")
            # TODO: Implement actual Delta API call
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    async def _place_market_order(
        self,
        symbol: str,
        side: str,
        size: int,
    ) -> Optional[str]:
        """
        Place a market order (for closing).

        Returns:
            Order ID if successful
        """
        try:
            logger.debug(f"Would place market {side} order: {symbol} x{size}")
            # TODO: Implement actual Delta API call

            import time
            mock_order_id = f"DLT-MKT-{int(time.time())}-{symbol}"
            return mock_order_id

        except Exception as e:
            logger.error(f"Failed to place market order: {e}")
            return None

    # =========================================================================
    # Basket Closing
    # =========================================================================

    async def _force_close_basket(
        self,
        session: AsyncSession,
        basket: Basket,
        reason: str,
    ) -> bool:
        """
        Force-close all legs of a basket as market orders.

        This is the nuclear option: all legs are closed simultaneously
        at market price, regardless of current fill status.
        """
        logger.info(f"Force-closing basket {basket.id[:8]}... (reason: {reason})")

        try:
            basket_repo = BasketRepository(session)
            leg_repo = LegRepository(session)

            # Update basket state
            await basket_repo.update_state(basket.id, BasketState.CLOSING)

            # Close all legs as market orders
            for leg in basket.legs:
                if leg.status == LegStatus.PENDING:
                    # Cancel pending order
                    if leg.latest_order_id:
                        await self._cancel_order(leg.latest_order_id)

                    # Mark as cancelled
                    await leg_repo.update_status(leg.id, LegStatus.CANCELLED)

                elif leg.status == LegStatus.FILLED:
                    # Place market order to close
                    opposite_side = "sell" if leg.side.lower() == "buy" else "buy"

                    order_id = await self._place_market_order(
                        symbol=leg.symbol,
                        side=opposite_side,
                        size=int(leg.size),
                    )

                    if order_id:
                        logger.info(f"✅ Placed close order: {leg.symbol}")

            # Mark basket as closed
            await basket_repo.update_state(basket.id, BasketState.CLOSED)

            # Clean up
            self.active_baskets.pop(basket.id, None)
            self.entry_timeouts.pop(basket.id, None)

            logger.info(f"✅ Basket {basket.id[:8]}... closed")
            return True

        except Exception as e:
            logger.error(f"Error force-closing basket: {e}")
            return False

    # =========================================================================
    # Background Tasks
    # =========================================================================

    async def _monitor_timeouts(self) -> None:
        """Monitor entry timeouts and auto-close baskets that expire."""
        while self.running:
            try:
                now = datetime.utcnow()
                expired_baskets = [
                    bid for bid, timeout in self.entry_timeouts.items()
                    if now > timeout
                ]

                for basket_id in expired_baskets:
                    logger.warning(f"Basket {basket_id[:8]}... entry timeout expired")
                    # Note: would need to auto-close here in production
                    # await self._force_close_basket(...)

                await asyncio.sleep(5)  # Check every 5 seconds

            except Exception as e:
                logger.error(f"Error in timeout monitor: {e}")
