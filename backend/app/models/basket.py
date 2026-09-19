"""
SQLAlchemy ORM models for baskets and legs.

Schema:
- Basket: parent container for a set of legs
- Leg: individual option leg (buy/sell)
- OrderLog: audit trail of all orders/edits/cancellations per leg
- TickSnapshot: periodic P&L snapshots (for analytics)
"""

from datetime import datetime, timedelta
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Enum,
    Text, Numeric, Index
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class BasketState(PyEnum):
    """Basket lifecycle states."""
    BUILDING = "building"      # Waiting for all legs to fill
    ACTIVE = "active"          # All legs filled, ready to trade
    CLOSING = "closing"        # Force-close in progress
    CLOSED = "closed"          # All legs closed
    ABANDONED = "abandoned"    # Entry timed out, legs may be partially filled


class LegStatus(PyEnum):
    """Leg execution status."""
    PENDING = "pending"        # Waiting to be filled
    FILLED = "filled"          # Order filled
    CANCELLED = "cancelled"    # Order cancelled/rejected
    PARTIAL = "partial"        # Partially filled


class OrderType(PyEnum):
    """Order type."""
    LIMIT = "limit"
    MARKET = "market"


class OrderState(PyEnum):
    """Order state (per Delta API)."""
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class Basket(Base):
    """
    A basket = a set of option legs on one underlying, same expiry, same lot size.

    All legs are filled together (or timeout auto-closes).
    P&L is tracked at basket level.
    Closes are executed for the entire basket at once (all-or-none).
    """

    __tablename__ = "baskets"

    # Primary key
    id = Column(String(36), primary_key=True, index=True)  # UUID

    # Basket definition
    underlying = Column(String(10), nullable=False, index=True)  # BTC, ETH
    expiry_date = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    lot_size = Column(Integer, nullable=False)  # Number of contracts per leg

    # Lifecycle
    state = Column(Enum(BasketState), default=BasketState.BUILDING, index=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    activated_at = Column(DateTime, nullable=True)  # When all legs filled
    closing_started_at = Column(DateTime, nullable=True)  # When close initiated
    closed_at = Column(DateTime, nullable=True)  # When all legs closed

    # Configuration (immutable after creation)
    entry_timeout_minutes = Column(Integer, default=15)

    # SL/TP triggers (configured at basket creation)
    sl_premium_config = Column(Text, nullable=True)  # JSON: {"symbol": "...", "threshold_usd": 50}
    sl_underlying_config = Column(Text, nullable=True)  # JSON: {"ltp_stop": 45000}
    tp_premium_config = Column(Text, nullable=True)  # JSON: {"symbol": "...", "target_usd": 150}
    tp_underlying_config = Column(Text, nullable=True)  # JSON: {"ltp_target": 50000}

    # P&L (updated in real-time)
    realized_pnl = Column(Numeric(18, 2), default=0, nullable=False)  # USD
    unrealized_pnl = Column(Numeric(18, 2), default=0, nullable=False)  # USD (mark-to-market)

    # Relationships
    legs = relationship("Leg", back_populates="basket", cascade="all, delete-orphan")
    tick_snapshots = relationship("TickSnapshot", back_populates="basket", cascade="all, delete-orphan")

    # Indices
    __table_args__ = (
        Index("idx_basket_state_created", "state", "created_at"),
        Index("idx_basket_underlying_expiry", "underlying", "expiry_date"),
    )

    def __repr__(self):
        return f"<Basket {self.id} {self.underlying} {self.state.value}>"


class Leg(Base):
    """
    A single option leg (buy call, sell put, etc.) in a basket.

    Immutable after creation. Fill price and status tracked separately.
    """

    __tablename__ = "legs"

    # Primary key
    id = Column(String(36), primary_key=True, index=True)  # UUID

    # Foreign key
    basket_id = Column(String(36), ForeignKey("baskets.id"), nullable=False, index=True)
    basket = relationship("Basket", back_populates="legs")

    # Instrument definition (immutable)
    symbol = Column(String(50), nullable=False, index=True)  # e.g., "C-BTC-50000-260926"
    product_id = Column(Integer, nullable=False)
    strike = Column(Numeric(18, 2), nullable=False)
    option_type = Column(String(10), nullable=False)  # call, put

    # Order definition (immutable)
    side = Column(String(10), nullable=False)  # buy, sell
    size = Column(Integer, nullable=False)  # Number of contracts

    # Execution (mutable)
    status = Column(Enum(LegStatus), default=LegStatus.PENDING, nullable=False, index=True)
    fill_price = Column(Numeric(18, 2), nullable=True)  # Avg fill price (USD)
    filled_at = Column(DateTime, nullable=True)

    # Latest order info
    latest_order_id = Column(String(50), nullable=True)  # Delta order ID

    # P&L (updated with ticks)
    entry_price = Column(Numeric(18, 2), nullable=True)  # Entry price (mark at fill time)
    current_price = Column(Numeric(18, 2), nullable=True)  # Current mark price
    mark_price_at_close = Column(Numeric(18, 2), nullable=True)  # Mark at close
    leg_pnl = Column(Numeric(18, 2), default=0, nullable=False)  # Realized P&L
    unrealized_pnl = Column(Numeric(18, 2), default=0, nullable=False)  # Current MtM

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    order_logs = relationship("OrderLog", back_populates="leg", cascade="all, delete-orphan")

    # Indices
    __table_args__ = (
        Index("idx_leg_basket_status", "basket_id", "status"),
        Index("idx_leg_symbol", "symbol"),
    )

    def __repr__(self):
        return f"<Leg {self.id} {self.symbol} {self.side} {self.status.value}>"


class OrderLog(Base):
    """
    Audit trail for all orders placed on a leg.

    Tracks: initial order, price-chase reprices, cancellations, fills.
    Complete history for debugging and analytics.
    """

    __tablename__ = "order_logs"

    # Primary key
    id = Column(String(36), primary_key=True, index=True)  # UUID

    # Foreign key
    leg_id = Column(String(36), ForeignKey("legs.id"), nullable=False, index=True)
    leg = relationship("Leg", back_populates="order_logs")

    # Order info (from Delta API)
    delta_order_id = Column(String(50), nullable=False, unique=True, index=True)
    order_type = Column(Enum(OrderType), nullable=False)  # limit, market
    price = Column(Numeric(18, 2), nullable=True)  # For limit orders
    size = Column(Integer, nullable=False)
    filled_size = Column(Integer, default=0)  # Cumulative filled

    # Order state (from Delta API)
    state = Column(Enum(OrderState), nullable=False, index=True)

    # Pricing event (for price-chase tracking)
    reason = Column(String(100), nullable=True)  # "initial", "rePrice:bid+$10", "cancelled", etc.

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    filled_at = Column(DateTime, nullable=True)

    # Notes for debugging
    notes = Column(Text, nullable=True)

    # Indices
    __table_args__ = (
        Index("idx_order_leg_created", "leg_id", "created_at"),
        Index("idx_order_state_created", "state", "created_at"),
    )

    def __repr__(self):
        return f"<OrderLog {self.delta_order_id} {self.state.value}>"


class TickSnapshot(Base):
    """
    Periodic snapshots of basket P&L for historical charting.

    Optional: captured every N seconds for analytics/charting.
    Not required for MVP, but data model supports it.
    """

    __tablename__ = "tick_snapshots"

    # Primary key
    id = Column(String(36), primary_key=True, index=True)  # UUID

    # Foreign key
    basket_id = Column(String(36), ForeignKey("baskets.id"), nullable=False, index=True)
    basket = relationship("Basket", back_populates="tick_snapshots")

    # Market state at this snapshot
    mark_prices = Column(Text, nullable=False)  # JSON: {"symbol": mark_price, ...}

    # P&L at this moment
    realized_pnl = Column(Numeric(18, 2), nullable=False)
    unrealized_pnl = Column(Numeric(18, 2), nullable=False)

    # Timestamp
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Indices
    __table_args__ = (
        Index("idx_snapshot_basket_captured", "basket_id", "captured_at"),
    )

    def __repr__(self):
        return f"<TickSnapshot {self.basket_id} @ {self.captured_at}>"
