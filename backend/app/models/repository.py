"""
Repository layer for database access.

Implements repository pattern for clean data access abstraction.
"""

import logging
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.basket import (
    Basket, Leg, OrderLog, TickSnapshot,
    BasketState, LegStatus, OrderState
)

logger = logging.getLogger(__name__)


class BasketRepository:
    """Data access for baskets."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        underlying: str,
        expiry_date: str,
        lot_size: int,
        entry_timeout_minutes: int = 15,
        **kwargs
    ) -> Basket:
        """Create a new basket."""
        basket = Basket(
            id=str(uuid4()),
            underlying=underlying.upper(),
            expiry_date=expiry_date,
            lot_size=lot_size,
            entry_timeout_minutes=entry_timeout_minutes,
            state=BasketState.BUILDING,
            **kwargs
        )
        self.session.add(basket)
        await self.session.flush()
        logger.info(f"Created basket {basket.id}")
        return basket

    async def get_by_id(self, basket_id: str) -> Optional[Basket]:
        """Get basket by ID."""
        result = await self.session.execute(
            select(Basket).where(Basket.id == basket_id)
        )
        return result.scalar_one_or_none()

    async def get_active(self) -> List[Basket]:
        """Get all active baskets."""
        result = await self.session.execute(
            select(Basket).where(
                Basket.state.in_([BasketState.BUILDING, BasketState.ACTIVE])
            ).order_by(desc(Basket.created_at))
        )
        return result.scalars().all()

    async def get_by_underlying_expiry(
        self,
        underlying: str,
        expiry_date: str
    ) -> List[Basket]:
        """Get baskets for a specific underlying/expiry."""
        result = await self.session.execute(
            select(Basket).where(
                and_(
                    Basket.underlying == underlying.upper(),
                    Basket.expiry_date == expiry_date
                )
            ).order_by(desc(Basket.created_at))
        )
        return result.scalars().all()

    async def update_state(self, basket_id: str, new_state: BasketState) -> Optional[Basket]:
        """Update basket state."""
        basket = await self.get_by_id(basket_id)
        if basket:
            basket.state = new_state
            if new_state == BasketState.ACTIVE:
                basket.activated_at = datetime.utcnow()
            elif new_state == BasketState.CLOSED:
                basket.closed_at = datetime.utcnow()
            await self.session.flush()
        return basket

    async def update_pnl(
        self,
        basket_id: str,
        realized_pnl: float,
        unrealized_pnl: float
    ) -> Optional[Basket]:
        """Update basket P&L."""
        basket = await self.get_by_id(basket_id)
        if basket:
            basket.realized_pnl = realized_pnl
            basket.unrealized_pnl = unrealized_pnl
            await self.session.flush()
        return basket


class LegRepository:
    """Data access for legs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        basket_id: str,
        symbol: str,
        product_id: int,
        strike: float,
        option_type: str,
        side: str,
        size: int,
    ) -> Leg:
        """Create a new leg."""
        leg = Leg(
            id=str(uuid4()),
            basket_id=basket_id,
            symbol=symbol,
            product_id=product_id,
            strike=strike,
            option_type=option_type,
            side=side.lower(),
            size=size,
            status=LegStatus.PENDING,
        )
        self.session.add(leg)
        await self.session.flush()
        logger.info(f"Created leg {leg.id} for basket {basket_id}")
        return leg

    async def get_by_id(self, leg_id: str) -> Optional[Leg]:
        """Get leg by ID."""
        result = await self.session.execute(
            select(Leg).where(Leg.id == leg_id)
        )
        return result.scalar_one_or_none()

    async def get_by_basket(self, basket_id: str) -> List[Leg]:
        """Get all legs for a basket."""
        result = await self.session.execute(
            select(Leg).where(Leg.basket_id == basket_id)
        )
        return result.scalars().all()

    async def update_status(
        self,
        leg_id: str,
        status: LegStatus,
        fill_price: Optional[float] = None
    ) -> Optional[Leg]:
        """Update leg status and fill price."""
        leg = await self.get_by_id(leg_id)
        if leg:
            leg.status = status
            if status == LegStatus.FILLED:
                leg.filled_at = datetime.utcnow()
                if fill_price is not None:
                    leg.fill_price = fill_price
            await self.session.flush()
        return leg

    async def update_pnl(
        self,
        leg_id: str,
        current_price: Optional[float] = None,
        unrealized_pnl: Optional[float] = None,
    ) -> Optional[Leg]:
        """Update leg P&L."""
        leg = await self.get_by_id(leg_id)
        if leg:
            if current_price is not None:
                leg.current_price = current_price
            if unrealized_pnl is not None:
                leg.unrealized_pnl = unrealized_pnl
            await self.session.flush()
        return leg


class OrderLogRepository:
    """Data access for order logs."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        leg_id: str,
        delta_order_id: str,
        order_type: str,
        price: Optional[float],
        size: int,
        state: OrderState,
        reason: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> OrderLog:
        """Log an order."""
        order_log = OrderLog(
            id=str(uuid4()),
            leg_id=leg_id,
            delta_order_id=delta_order_id,
            order_type=order_type,
            price=price,
            size=size,
            state=state,
            reason=reason,
            notes=notes,
        )
        self.session.add(order_log)
        await self.session.flush()
        return order_log

    async def get_by_delta_order_id(self, delta_order_id: str) -> Optional[OrderLog]:
        """Get order log by Delta order ID."""
        result = await self.session.execute(
            select(OrderLog).where(OrderLog.delta_order_id == delta_order_id)
        )
        return result.scalar_one_or_none()

    async def get_by_leg(self, leg_id: str) -> List[OrderLog]:
        """Get all orders for a leg."""
        result = await self.session.execute(
            select(OrderLog)
            .where(OrderLog.leg_id == leg_id)
            .order_by(OrderLog.created_at)
        )
        return result.scalars().all()

    async def update_state(
        self,
        order_log_id: str,
        state: OrderState,
        filled_size: Optional[int] = None
    ) -> Optional[OrderLog]:
        """Update order state."""
        result = await self.session.execute(
            select(OrderLog).where(OrderLog.id == order_log_id)
        )
        order_log = result.scalar_one_or_none()

        if order_log:
            order_log.state = state
            if state == OrderState.FILLED:
                order_log.filled_at = datetime.utcnow()
                if filled_size is not None:
                    order_log.filled_size = filled_size
            await self.session.flush()

        return order_log


class TickSnapshotRepository:
    """Data access for tick snapshots (analytics)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        basket_id: str,
        mark_prices: str,  # JSON string
        realized_pnl: float,
        unrealized_pnl: float,
    ) -> TickSnapshot:
        """Create a tick snapshot."""
        snapshot = TickSnapshot(
            id=str(uuid4()),
            basket_id=basket_id,
            mark_prices=mark_prices,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def get_by_basket(
        self,
        basket_id: str,
        limit: int = 1000
    ) -> List[TickSnapshot]:
        """Get tick snapshots for a basket."""
        result = await self.session.execute(
            select(TickSnapshot)
            .where(TickSnapshot.basket_id == basket_id)
            .order_by(desc(TickSnapshot.captured_at))
            .limit(limit)
        )
        return result.scalars().all()
