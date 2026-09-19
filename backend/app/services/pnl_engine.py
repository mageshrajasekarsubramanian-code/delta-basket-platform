"""
P&L Engine for Delta Basket Platform.

Responsibilities:
1. Calculate leg-level P&L (realized and unrealized)
2. Calculate basket-level P&L
3. Update in real-time from market data ticks
4. Track realized P&L on closes
5. Capture periodic snapshots for analytics
"""

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Dict, Optional, List, Tuple

from backend.app.services.market_data_service import MarketDataService, TickerUpdate
from backend.app.models import (
    BasketRepository, LegRepository, TickSnapshotRepository,
    Basket, Leg, BasketState, LegStatus,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PnLCalculator:
    """Calculate P&L for individual legs and baskets."""

    @staticmethod
    def calculate_leg_pnl(
        leg: Leg,
        current_price: float,
    ) -> Tuple[float, float]:
        """
        Calculate P&L for a single leg.

        Args:
            leg: Leg object with fill_price and current prices
            current_price: Current market price

        Returns:
            (unrealized_pnl, entry_price_used)

        Notes:
            For long positions (buy):
                P&L = (current_price - fill_price) * size
            For short positions (sell):
                P&L = (fill_price - current_price) * size
        """
        if not leg.fill_price or leg.status != LegStatus.FILLED:
            # Not filled yet
            return 0.0, 0.0

        fill_price = float(leg.fill_price)
        size = float(leg.size)

        # Calculate based on side
        if leg.side.lower() == "buy":
            # Long: profit if current > fill
            unrealized_pnl = (current_price - fill_price) * size
        else:
            # Short: profit if current < fill
            unrealized_pnl = (fill_price - current_price) * size

        return unrealized_pnl, fill_price

    @staticmethod
    def calculate_realized_pnl(
        leg: Leg,
        close_price: float,
    ) -> float:
        """
        Calculate realized P&L when a leg is closed.

        Args:
            leg: Leg with fill_price
            close_price: Price at which leg was closed

        Returns:
            Realized P&L in USD
        """
        if not leg.fill_price:
            return 0.0

        fill_price = float(leg.fill_price)
        size = float(leg.size)

        if leg.side.lower() == "buy":
            realized = (close_price - fill_price) * size
        else:
            realized = (fill_price - close_price) * size

        return realized

    @staticmethod
    def calculate_basket_pnl(
        legs: List[Leg],
    ) -> Tuple[float, float]:
        """
        Calculate basket-level P&L as sum of all legs.

        Returns:
            (total_unrealized, total_realized)
        """
        total_unrealized = 0.0
        total_realized = 0.0

        for leg in legs:
            if leg.unrealized_pnl:
                total_unrealized += float(leg.unrealized_pnl)
            if leg.leg_pnl:
                total_realized += float(leg.leg_pnl)

        return total_unrealized, total_realized


class PnLEngine:
    """
    Real-time P&L calculation and tracking.

    Features:
    - Leg-level P&L updates from ticks
    - Basket-level P&L aggregation
    - Realized P&L tracking on closes
    - Periodic snapshot capture for analytics
    """

    def __init__(self, market_data_service: MarketDataService):
        self.market_data_service = market_data_service
        self.calculator = PnLCalculator()

        # Tracked baskets
        self.tracked_baskets: Dict[str, Basket] = {}

        # Background tasks
        self.tasks: List[asyncio.Task] = []
        self.running = False

        # Snapshot configuration
        self.snapshot_interval_seconds = 5  # Capture every 5 seconds

    async def start(self) -> None:
        """Start the P&L engine."""
        logger.info("Starting P&L Engine...")
        self.running = True

        # Start background snapshot task
        self.tasks.append(asyncio.create_task(self._periodic_snapshot_capture()))

        logger.info("✅ P&L Engine started")

    async def stop(self) -> None:
        """Stop the P&L engine."""
        logger.info("Stopping P&L Engine...")
        self.running = False

        # Cancel background tasks
        for task in self.tasks:
            task.cancel()

        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("P&L Engine stopped")

    # =========================================================================
    # Public API
    # =========================================================================

    async def track_basket(
        self,
        basket: Basket,
        session: AsyncSession,
    ) -> None:
        """
        Start tracking P&L for an active basket.

        Subscribes to ticker updates for all legs.
        """
        logger.info(f"Starting P&L tracking for basket {basket.id[:8]}...")

        self.tracked_baskets[basket.id] = basket

        # Subscribe to ticker updates for each leg
        for leg in basket.legs:
            handler = lambda update, bid=basket.id, lid=leg.id: asyncio.create_task(
                self._on_ticker_update(bid, lid, update)
            )
            self.market_data_service.subscribe_ticker(leg.symbol, handler)

    async def untrack_basket(self, basket_id: str) -> None:
        """Stop tracking a basket."""
        logger.info(f"Stopping P&L tracking for basket {basket_id[:8]}...")
        self.tracked_baskets.pop(basket_id, None)

    async def record_realized_pnl(
        self,
        session: AsyncSession,
        leg: Leg,
        close_price: float,
    ) -> None:
        """
        Record realized P&L when a leg is closed.

        Args:
            session: Database session
            leg: Leg being closed
            close_price: Price at which leg was closed
        """
        realized = self.calculator.calculate_realized_pnl(leg, close_price)

        logger.info(f"Recording realized P&L for {leg.symbol}:")
        logger.info(f"  Fill price: ${float(leg.fill_price):.2f}")
        logger.info(f"  Close price: ${close_price:.2f}")
        logger.info(f"  Realized P&L: ${realized:.2f}")

        # Update leg
        leg_repo = LegRepository(session)
        await leg_repo.update_pnl(leg.id)

        # Leg P&L will be updated to realized amount
        leg.leg_pnl = Decimal(str(realized))
        leg.mark_price_at_close = Decimal(str(close_price))

    # =========================================================================
    # Ticker Updates (Real-time P&L)
    # =========================================================================

    async def _on_ticker_update(
        self,
        basket_id: str,
        leg_id: str,
        update: TickerUpdate,
    ) -> None:
        """Handle ticker update for a leg."""
        try:
            basket = self.tracked_baskets.get(basket_id)
            if not basket:
                return

            # Find leg
            leg = next((l for l in basket.legs if l.id == leg_id), None)
            if not leg:
                return

            # Calculate P&L for this leg
            current_price = float(update.mark_price)
            unrealized_pnl, _ = self.calculator.calculate_leg_pnl(leg, current_price)

            # Update leg
            leg.current_price = Decimal(str(current_price))
            leg.unrealized_pnl = Decimal(str(unrealized_pnl))

            # Update basket P&L (sum of all legs)
            basket_unrealized = sum(
                float(l.unrealized_pnl or 0) for l in basket.legs
            )
            basket_realized = sum(
                float(l.leg_pnl or 0) for l in basket.legs
            )

            basket.unrealized_pnl = Decimal(str(basket_unrealized))
            basket.realized_pnl = Decimal(str(basket_realized))

        except Exception as e:
            logger.error(f"Error updating P&L for basket {basket_id[:8]}...: {e}")

    # =========================================================================
    # Snapshots (Historical Analytics)
    # =========================================================================

    async def _periodic_snapshot_capture(self) -> None:
        """Periodically capture P&L snapshots for all tracked baskets."""
        while self.running:
            try:
                await asyncio.sleep(self.snapshot_interval_seconds)

                for basket_id, basket in list(self.tracked_baskets.items()):
                    await self._capture_snapshot(basket)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in snapshot capture: {e}")

    async def _capture_snapshot(self, basket: Basket) -> None:
        """Capture a single P&L snapshot for a basket."""
        try:
            # Build mark prices dict
            mark_prices = {}
            for leg in basket.legs:
                if leg.current_price:
                    mark_prices[leg.symbol] = float(leg.current_price)

            if not mark_prices:
                return

            # Create snapshot document (would be persisted in DB in production)
            snapshot = {
                "basket_id": basket.id,
                "timestamp": datetime.utcnow().isoformat(),
                "mark_prices": mark_prices,
                "unrealized_pnl": float(basket.unrealized_pnl or 0),
                "realized_pnl": float(basket.realized_pnl or 0),
            }

            logger.debug(f"Snapshot for basket {basket.id[:8]}...: {snapshot}")

            # In production, would save to database
            # snapshot_repo = TickSnapshotRepository(session)
            # await snapshot_repo.create(...)

        except Exception as e:
            logger.error(f"Error capturing snapshot: {e}")

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_basket_summary(self, basket: Basket) -> Dict:
        """Get P&L summary for a basket."""
        return {
            "basket_id": basket.id,
            "state": basket.state.value,
            "underlying": basket.underlying,
            "expiry": basket.expiry_date,
            "realized_pnl": float(basket.realized_pnl or 0),
            "unrealized_pnl": float(basket.unrealized_pnl or 0),
            "total_pnl": float((basket.realized_pnl or 0) + (basket.unrealized_pnl or 0)),
            "legs": [self.get_leg_summary(leg) for leg in basket.legs],
        }

    def get_leg_summary(self, leg: Leg) -> Dict:
        """Get P&L summary for a leg."""
        return {
            "leg_id": leg.id,
            "symbol": leg.symbol,
            "side": leg.side,
            "status": leg.status.value,
            "fill_price": float(leg.fill_price or 0),
            "current_price": float(leg.current_price or 0),
            "unrealized_pnl": float(leg.unrealized_pnl or 0),
            "realized_pnl": float(leg.leg_pnl or 0),
        }

    async def print_basket_pnl(self, basket: Basket) -> None:
        """Print detailed P&L report for a basket."""
        summary = self.get_basket_summary(basket)

        logger.info("\n" + "=" * 70)
        logger.info(f"P&L Report: Basket {basket.id[:8]}...")
        logger.info("=" * 70)
        logger.info(f"State: {summary['state']}")
        logger.info(f"Underlying: {summary['underlying']} | Expiry: {summary['expiry']}")
        logger.info("")
        logger.info("P&L Summary:")
        logger.info(f"  Realized P&L:   ${summary['realized_pnl']:>10.2f}")
        logger.info(f"  Unrealized P&L: ${summary['unrealized_pnl']:>10.2f}")
        logger.info(f"  Total P&L:      ${summary['total_pnl']:>10.2f}")
        logger.info("")
        logger.info("Leg Breakdown:")

        for i, leg_summary in enumerate(summary['legs'], 1):
            logger.info(f"  Leg {i}: {leg_summary['symbol']}")
            logger.info(f"    Side: {leg_summary['side']} | Status: {leg_summary['status']}")
            logger.info(f"    Fill: ${leg_summary['fill_price']:.2f} | Current: ${leg_summary['current_price']:.2f}")
            logger.info(f"    Unrealized: ${leg_summary['unrealized_pnl']:>8.2f} | Realized: ${leg_summary['realized_pnl']:>8.2f}")

        logger.info("=" * 70)
