"""
Trigger Monitor for Delta Basket Platform.

Monitors basket conditions and fires closes when SL/TP triggers are met.

Two trigger types:
1. Leg premium trigger: if any leg's price reaches threshold, close entire basket
2. Underlying LTP trigger: if underlying future price reaches threshold, close entire basket
"""

import asyncio
import json
import logging
from typing import Dict, Optional, List
from decimal import Decimal

from backend.app.services.market_data_service import MarketDataService, TickerUpdate
from backend.app.services.order_execution_service import OrderExecutionService
from backend.app.models import Basket, BasketState
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class TriggerType:
    """Trigger type constants."""
    PREMIUM = "premium"         # Leg premium reaches threshold
    UNDERLYING = "underlying"   # Underlying future reaches threshold


class TriggerMonitor:
    """
    Monitors basket conditions for SL/TP triggers.

    Features:
    - Premium-based triggers (leg price threshold)
    - Underlying-based triggers (future price threshold)
    - Real-time evaluation on every tick
    - Fires basket closes when triggered
    - Handles system maintenance pauses
    """

    def __init__(
        self,
        market_data_service: MarketDataService,
        order_execution_service: OrderExecutionService,
    ):
        self.market_data_service = market_data_service
        self.order_execution_service = order_execution_service

        # Tracked baskets and their triggers
        self.monitored_baskets: Dict[str, Basket] = {}
        self.triggers: Dict[str, Dict] = {}  # basket_id -> trigger config

        # Exchange status tracking
        self.monitoring_paused = False  # Paused during maintenance

        # Background tasks
        self.tasks: List[asyncio.Task] = []
        self.running = False

    async def start(self) -> None:
        """Start the trigger monitor."""
        logger.info("Starting Trigger Monitor...")
        self.running = True

        # Start background monitoring task
        self.tasks.append(asyncio.create_task(self._monitor_loop()))

        logger.info("✅ Trigger Monitor started")

    async def stop(self) -> None:
        """Stop the trigger monitor."""
        logger.info("Stopping Trigger Monitor...")
        self.running = False

        # Cancel background tasks
        for task in self.tasks:
            task.cancel()

        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("Trigger Monitor stopped")

    # =========================================================================
    # Public API
    # =========================================================================

    async def register_basket(
        self,
        basket: Basket,
        sl_premium_config: Optional[Dict] = None,
        sl_underlying_config: Optional[Dict] = None,
        tp_premium_config: Optional[Dict] = None,
        tp_underlying_config: Optional[Dict] = None,
    ) -> None:
        """
        Register a basket for trigger monitoring.

        Args:
            basket: Basket to monitor
            sl_premium_config: {"symbol": "...", "threshold_usd": 50}
            sl_underlying_config: {"ltp_stop": 45000}
            tp_premium_config: {"symbol": "...", "target_usd": 150}
            tp_underlying_config: {"ltp_target": 50000}
        """
        logger.info(f"Registering basket {basket.id[:8]}... for trigger monitoring")

        self.monitored_baskets[basket.id] = basket
        self.triggers[basket.id] = {
            "sl_premium": sl_premium_config,
            "sl_underlying": sl_underlying_config,
            "tp_premium": tp_premium_config,
            "tp_underlying": tp_underlying_config,
        }

        # Log trigger configuration
        if sl_premium_config:
            logger.info(f"  SL Premium: {sl_premium_config}")
        if sl_underlying_config:
            logger.info(f"  SL Underlying: {sl_underlying_config}")
        if tp_premium_config:
            logger.info(f"  TP Premium: {tp_premium_config}")
        if tp_underlying_config:
            logger.info(f"  TP Underlying: {tp_underlying_config}")

    async def unregister_basket(self, basket_id: str) -> None:
        """Unregister a basket from trigger monitoring."""
        logger.info(f"Unregistering basket {basket_id[:8]}...")
        self.monitored_baskets.pop(basket_id, None)
        self.triggers.pop(basket_id, None)

    # =========================================================================
    # Monitoring
    # =========================================================================

    async def _monitor_loop(self) -> None:
        """Main monitoring loop - evaluate triggers."""
        while self.running:
            try:
                # Check each monitored basket
                for basket_id, basket in list(self.monitored_baskets.items()):
                    if basket.state != BasketState.ACTIVE:
                        # Only monitor active baskets
                        continue

                    if self.monitoring_paused:
                        # Skip trigger checks during maintenance
                        continue

                    # Evaluate triggers
                    triggered = await self._evaluate_triggers(basket)

                    if triggered:
                        # Close the basket
                        await self.order_execution_service.close_basket_triggered(
                            session=None,  # Would need session from caller
                            basket=basket,
                            trigger_type=triggered
                        )
                        # Unregister
                        await self.unregister_basket(basket_id)

                # Check every tick (or poll if needed)
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")

    async def _evaluate_triggers(self, basket: Basket) -> Optional[str]:
        """
        Evaluate all triggers for a basket.

        Returns:
            Trigger type that fired, or None
        """
        config = self.triggers.get(basket.id, {})

        # Check SL Premium trigger
        if config.get("sl_premium"):
            if self._check_premium_trigger(basket, config["sl_premium"], "stop_loss"):
                return "sl_premium"

        # Check SL Underlying trigger
        if config.get("sl_underlying"):
            if self._check_underlying_trigger(basket, config["sl_underlying"], "stop_loss"):
                return "sl_underlying"

        # Check TP Premium trigger
        if config.get("tp_premium"):
            if self._check_premium_trigger(basket, config["tp_premium"], "take_profit"):
                return "tp_premium"

        # Check TP Underlying trigger
        if config.get("tp_underlying"):
            if self._check_underlying_trigger(basket, config["tp_underlying"], "take_profit"):
                return "tp_underlying"

        return None

    def _check_premium_trigger(
        self,
        basket: Basket,
        config: Dict,
        trigger_type: str,
    ) -> bool:
        """
        Check if a leg's premium reached the threshold.

        Args:
            basket: Basket to check
            config: {"symbol": "...", "threshold_usd": 50}
            trigger_type: "stop_loss" or "take_profit"

        Returns:
            True if trigger condition met
        """
        symbol = config.get("symbol")
        threshold = config.get("threshold_usd", 0)

        if not symbol:
            return False

        # Find leg with this symbol
        leg = next((l for l in basket.legs if l.symbol == symbol), None)
        if not leg:
            return False

        # Get current price
        ticker = self.market_data_service.get_ticker(symbol)
        if not ticker:
            return False

        current_price = float(ticker.mark_price)
        fill_price = float(leg.fill_price or 0)

        if not fill_price:
            return False

        # Calculate P&L for this leg
        if leg.side.lower() == "buy":
            # For long: loss if current < fill, gain if current > fill
            current_pnl = current_price - fill_price
        else:
            # For short: loss if current > fill, gain if current < fill
            current_pnl = fill_price - current_price

        logger.debug(f"Premium trigger check: {symbol} P&L=${current_pnl:.2f}, threshold=${threshold:.2f}")

        # Check trigger condition
        if trigger_type == "stop_loss":
            # Loss exceeded threshold (negative P&L)
            return current_pnl <= -threshold
        else:  # take_profit
            # Profit exceeded threshold (positive P&L)
            return current_pnl >= threshold

    def _check_underlying_trigger(
        self,
        basket: Basket,
        config: Dict,
        trigger_type: str,
    ) -> bool:
        """
        Check if underlying future price reached the threshold.

        Args:
            basket: Basket to check
            config: {"ltp_stop": 45000} or {"ltp_target": 50000}
            trigger_type: "stop_loss" or "take_profit"

        Returns:
            True if trigger condition met
        """
        # Map underlying to future symbol
        future_symbol_map = {
            "BTC": "BTCUSD",
            "ETH": "ETHUSD",
        }

        future_symbol = future_symbol_map.get(basket.underlying)
        if not future_symbol:
            return False

        # Get current future price
        ticker = self.market_data_service.get_ticker(future_symbol)
        if not ticker:
            return False

        current_price = float(ticker.mark_price)

        if trigger_type == "stop_loss":
            threshold = config.get("ltp_stop")
            if threshold is None:
                return False

            logger.debug(f"Underlying SL trigger check: {future_symbol} ${current_price:.2f}, stop=${threshold:.2f}")

            # Trigger if price drops below stop
            return current_price <= threshold

        else:  # take_profit
            threshold = config.get("ltp_target")
            if threshold is None:
                return False

            logger.debug(f"Underlying TP trigger check: {future_symbol} ${current_price:.2f}, target=${threshold:.2f}")

            # Trigger if price rises above target
            return current_price >= threshold

    # =========================================================================
    # System Status Handling
    # =========================================================================

    def _on_system_status_change(self, status) -> None:
        """Handle system status changes."""
        from app.services.delta_client import SystemStatus

        old_paused = self.monitoring_paused

        # Check if operational
        if status == SystemStatus.OPERATIONAL:
            self.monitoring_paused = False
        else:
            self.monitoring_paused = True

        if old_paused != self.monitoring_paused:
            if self.monitoring_paused:
                logger.warning("⚠️  Trigger monitoring PAUSED (exchange maintenance)")
                # Log reminder about gap risk
                logger.warning("   Triggers remain configured but won't fire")
                logger.warning("   User should monitor via Delta portal during outage")
            else:
                logger.info("✅ Trigger monitoring RESUMED")
                logger.info("   Re-evaluating all triggers against current prices")

    # =========================================================================
    # Reporting
    # =========================================================================

    def get_basket_triggers(self, basket_id: str) -> Dict:
        """Get trigger configuration for a basket."""
        config = self.triggers.get(basket_id, {})

        return {
            "basket_id": basket_id,
            "sl_premium": config.get("sl_premium"),
            "sl_underlying": config.get("sl_underlying"),
            "tp_premium": config.get("tp_premium"),
            "tp_underlying": config.get("tp_underlying"),
            "monitoring_paused": self.monitoring_paused,
        }

    async def print_basket_triggers(self, basket: Basket) -> None:
        """Print trigger configuration for a basket."""
        triggers = self.get_basket_triggers(basket.id)

        logger.info("\n" + "=" * 70)
        logger.info(f"Triggers: Basket {basket.id[:8]}...")
        logger.info("=" * 70)

        if triggers["monitoring_paused"]:
            logger.warning("⚠️  MONITORING PAUSED (exchange maintenance)")

        if triggers["sl_premium"]:
            logger.info(f"SL Premium: {triggers['sl_premium']}")
        if triggers["sl_underlying"]:
            logger.info(f"SL Underlying: {triggers['sl_underlying']}")
        if triggers["tp_premium"]:
            logger.info(f"TP Premium: {triggers['tp_premium']}")
        if triggers["tp_underlying"]:
            logger.info(f"TP Underlying: {triggers['tp_underlying']}")

        if not any([triggers["sl_premium"], triggers["sl_underlying"],
                    triggers["tp_premium"], triggers["tp_underlying"]]):
            logger.info("No triggers configured")

        logger.info("=" * 70)
