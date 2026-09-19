"""
REST API endpoints for basket management.

Endpoints:
- POST /baskets - create a basket
- GET /baskets - list all baskets
- GET /baskets/{id} - get basket details with legs
- POST /baskets/{id}/close - manually close a basket
- GET /baskets/{id}/pnl - get P&L details
"""

import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import AsyncSession

from app.models import (
    get_db,
    BasketRepository, LegRepository,
    Basket, BasketState, LegStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/baskets", tags=["baskets"])


# =========================================================================
# Request/Response Models
# =========================================================================

class LegResponse(BaseModel):
    """Response model for a leg."""
    id: str
    symbol: str
    side: str
    status: str
    fill_price: Optional[float]
    current_price: Optional[float]
    unrealized_pnl: float
    realized_pnl: float

    class Config:
        from_attributes = True


class BasketCreateRequest(BaseModel):
    """Request to create a basket."""
    underlying: str  # BTC, ETH
    expiry_date: str  # YYYY-MM-DD
    lot_size: int
    entry_timeout_minutes: int = 15

    # Trigger configs (JSON strings)
    sl_premium_config: Optional[str] = None
    sl_underlying_config: Optional[str] = None
    tp_premium_config: Optional[str] = None
    tp_underlying_config: Optional[str] = None


class BasketResponse(BaseModel):
    """Response model for a basket."""
    id: str
    underlying: str
    expiry_date: str
    lot_size: int
    state: str
    created_at: datetime
    activated_at: Optional[datetime]
    closed_at: Optional[datetime]
    realized_pnl: float
    unrealized_pnl: float
    legs: List[LegResponse]

    class Config:
        from_attributes = True


class BasketListResponse(BaseModel):
    """List of baskets."""
    baskets: List[BasketResponse]
    total: int


class BasketPnLResponse(BaseModel):
    """P&L details for a basket."""
    basket_id: str
    state: str
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    legs: List[LegResponse]


# =========================================================================
# Endpoints
# =========================================================================

@router.post("", response_model=BasketResponse, status_code=status.HTTP_201_CREATED)
async def create_basket(
    request: BasketCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> BasketResponse:
    """Create a new basket."""
    logger.info(f"Creating basket: {request.underlying} {request.expiry_date}")

    try:
        basket_repo = BasketRepository(db)

        # Create basket
        basket = await basket_repo.create(
            underlying=request.underlying,
            expiry_date=request.expiry_date,
            lot_size=request.lot_size,
            entry_timeout_minutes=request.entry_timeout_minutes,
            sl_premium_config=request.sl_premium_config,
            sl_underlying_config=request.sl_underlying_config,
            tp_premium_config=request.tp_premium_config,
            tp_underlying_config=request.tp_underlying_config,
        )

        await db.commit()
        await db.refresh(basket)

        logger.info(f"✅ Created basket {basket.id[:8]}...")

        return BasketResponse(
            **basket.__dict__,
            legs=[LegResponse(**leg.__dict__) for leg in basket.legs]
        )

    except Exception as e:
        logger.error(f"Error creating basket: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=BasketListResponse)
async def list_baskets(
    db: AsyncSession = Depends(get_db),
) -> BasketListResponse:
    """List all baskets."""
    try:
        basket_repo = BasketRepository(db)
        baskets = await basket_repo.get_active()

        return BasketListResponse(
            baskets=[
                BasketResponse(
                    **basket.__dict__,
                    legs=[LegResponse(**leg.__dict__) for leg in basket.legs]
                )
                for basket in baskets
            ],
            total=len(baskets)
        )

    except Exception as e:
        logger.error(f"Error listing baskets: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{basket_id}", response_model=BasketResponse)
async def get_basket(
    basket_id: str,
    db: AsyncSession = Depends(get_db),
) -> BasketResponse:
    """Get a specific basket with its legs."""
    try:
        basket_repo = BasketRepository(db)
        basket = await basket_repo.get_by_id(basket_id)

        if not basket:
            raise HTTPException(status_code=404, detail="Basket not found")

        return BasketResponse(
            **basket.__dict__,
            legs=[LegResponse(**leg.__dict__) for leg in basket.legs]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting basket: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{basket_id}/pnl", response_model=BasketPnLResponse)
async def get_basket_pnl(
    basket_id: str,
    db: AsyncSession = Depends(get_db),
) -> BasketPnLResponse:
    """Get P&L details for a basket."""
    try:
        basket_repo = BasketRepository(db)
        basket = await basket_repo.get_by_id(basket_id)

        if not basket:
            raise HTTPException(status_code=404, detail="Basket not found")

        total_pnl = float(basket.realized_pnl or 0) + float(basket.unrealized_pnl or 0)

        return BasketPnLResponse(
            basket_id=basket.id,
            state=basket.state.value,
            realized_pnl=float(basket.realized_pnl or 0),
            unrealized_pnl=float(basket.unrealized_pnl or 0),
            total_pnl=total_pnl,
            legs=[LegResponse(**leg.__dict__) for leg in basket.legs]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting basket P&L: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{basket_id}/close")
async def close_basket(
    basket_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manually close a basket."""
    try:
        basket_repo = BasketRepository(db)
        basket = await basket_repo.get_by_id(basket_id)

        if not basket:
            raise HTTPException(status_code=404, detail="Basket not found")

        if basket.state == BasketState.CLOSED:
            raise HTTPException(status_code=400, detail="Basket already closed")

        # Update state to closing
        await basket_repo.update_state(basket_id, BasketState.CLOSING)
        await db.commit()

        logger.info(f"Basket {basket_id[:8]}... marked for closing")

        # In production, would trigger order execution service here
        # await order_execution_service.close_basket_manual(db, basket)

        return {"status": "closing", "basket_id": basket_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error closing basket: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{basket_id}/legs", response_model=List[LegResponse])
async def get_basket_legs(
    basket_id: str,
    db: AsyncSession = Depends(get_db),
) -> List[LegResponse]:
    """Get all legs for a basket."""
    try:
        leg_repo = LegRepository(db)
        legs = await leg_repo.get_by_basket(basket_id)

        return [LegResponse(**leg.__dict__) for leg in legs]

    except Exception as e:
        logger.error(f"Error getting basket legs: {e}")
        raise HTTPException(status_code=500, detail=str(e))
