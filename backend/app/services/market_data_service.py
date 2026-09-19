"""
Market Data Service for Delta Basket Platform.

Responsibilities:
1. Stream BTC/ETH perpetual futures (BTCUSD, ETHUSD) via WebSocket
2. Fetch and cache option chains (current-day + next-day)
3. Filter instruments by expiry date
4. Provide internal pub/sub for downstream services (basket engine, triggers)
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from delta_client import DeltaClient, WebSocketMessage, SystemStatus

logger = logging.getLogger(__name__)


class ContractType(Enum):
    """Contract type enumeration."""
    PERPETUAL = "perpetual_futures"
    CALL_OPTION = "call_options"
    PUT_OPTION = "put_options"
    SPOT = "spot"


@dataclass
class Instrument:
    """Market instrument definition."""
    symbol: str
    product_id: int
    contract_type: str
    underlying: str  # BTC, ETH, etc.
    strike: Optional[float] = None
    expiry_date: Optional[str] = None  # YYYY-MM-DD
    is_active: bool = True


@dataclass
class TickerUpdate:
    """Live ticker update from market data."""
    symbol: str
    mark_price: float
    spot_price: Optional[float] = None
    ltp: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    iv: Optional[float] = None
    greeks: Dict[str, float] = field(default_factory=dict)
    timestamp: float = 0


@dataclass
class TradeUpdate:
    """Trade execution update."""
    symbol: str
    price: float
    size: float
    side: str  # buy/sell
    timestamp: float = 0


class MarketDataService:
    """
    Manages live market data streams and option chain snapshots.

    Features:
    - Futures ticker streaming (WebSocket)
    - Option chain fetching + caching (REST)
    - Expiry-based filtering
    - Pub/sub for downstream services
    """

    def __init__(self, delta_client: DeltaClient):
        self.delta_client = delta_client
        self.running = False

        # Instruments cache
        self.futures: Dict[str, Instrument] = {}  # symbol -> Instrument
        self.options: Dict[str, Instrument] = {}  # symbol -> Instrument
        self.all_instruments: Dict[str, Instrument] = {}  # symbol -> Instrument (union)

        # Option chains by expiry (for quick lookups)
        self.options_by_expiry: Dict[str, List[Instrument]] = {}  # date -> list of instruments

        # Live market data cache
        self.tickers: Dict[str, TickerUpdate] = {}  # symbol -> latest tick
        self.trades: Dict[str, TradeUpdate] = {}  # symbol -> latest trade

        # Last fetch time for option chains
        self.last_option_chain_fetch: Optional[datetime] = None
        self.option_chain_fetch_interval = timedelta(minutes=5)  # Refresh every 5 min

        # Message handlers (pub/sub)
        self.ticker_handlers: Dict[str, List[Callable]] = {}
        self.trade_handlers: Dict[str, List[Callable]] = {}
        self.instrument_update_handlers: List[Callable] = []

        # Background tasks
        self.tasks: List[asyncio.Task] = []

    async def start(self) -> None:
        """Start the market data service."""
        logger.info("Starting Market Data Service...")
        self.running = True

        try:
            # Register WebSocket handlers
            self._register_ws_handlers()

            # Fetch initial instrument list and option chains
            await self._refresh_instruments()

            # Start background tasks
            self.tasks.append(asyncio.create_task(self._periodic_option_chain_refresh()))

            logger.info("✅ Market Data Service started")

        except Exception as e:
            logger.error(f"Failed to start Market Data Service: {e}")
            self.running = False
            raise

    async def stop(self) -> None:
        """Stop the market data service."""
        logger.info("Stopping Market Data Service...")
        self.running = False

        # Cancel background tasks
        for task in self.tasks:
            task.cancel()

        await asyncio.gather(*self.tasks, return_exceptions=True)
        logger.info("Market Data Service stopped")

    def _register_ws_handlers(self) -> None:
        """Register WebSocket message handlers."""
        # Ticker updates (for futures and options)
        self.delta_client.add_message_handler("ticker", self._on_ticker_message)
        self.delta_client.add_message_handler("mark_price", self._on_mark_price_message)
        self.delta_client.add_message_handler("trades", self._on_trade_message)

    async def _refresh_instruments(self) -> None:
        """
        Fetch instruments from Delta and populate caches.

        Gets:
        - BTC/ETH perpetual futures
        - BTC/ETH options (current-day + next-day)
        """
        logger.info("Fetching instruments from Delta...")

        try:
            # Fetch all products
            products_resp = await self.delta_client.get_products(limit=500)

            if not products_resp.get("success"):
                logger.error(f"Failed to fetch products: {products_resp.get('error')}")
                return

            products = products_resp.get("result", [])

            # Process products
            today = datetime.utcnow().date()
            tomorrow = today + timedelta(days=1)

            futures_symbols = set()
            options_symbols = set()

            for product in products:
                symbol = product.get("symbol", "")
                product_id = product.get("id", 0)
                contract_type = product.get("contract_type", "").lower()
                underlying = product.get("underlying_asset", {}).get("symbol", "").upper()

                # Skip inactive products
                if product.get("state") != "ACTIVE":
                    continue

                # Futures: BTC and ETH perpetuals
                if contract_type == "perpetual_futures" and underlying in ["BTC", "ETH"]:
                    instrument = Instrument(
                        symbol=symbol,
                        product_id=product_id,
                        contract_type=contract_type,
                        underlying=underlying
                    )
                    self.futures[symbol] = instrument
                    self.all_instruments[symbol] = instrument
                    futures_symbols.add(symbol)

                # Options: BTC and ETH, only current-day + next-day
                elif contract_type in ["call_options", "put_options"] and underlying in ["BTC", "ETH"]:
                    expiry_str = product.get("expiration_date", "")

                    if expiry_str:
                        try:
                            expiry_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()

                            # Only include current-day and next-day
                            if expiry_date in [today, tomorrow]:
                                strike = float(product.get("strike_price", 0))

                                instrument = Instrument(
                                    symbol=symbol,
                                    product_id=product_id,
                                    contract_type=contract_type,
                                    underlying=underlying,
                                    strike=strike,
                                    expiry_date=expiry_str
                                )
                                self.options[symbol] = instrument
                                self.all_instruments[symbol] = instrument
                                options_symbols.add(symbol)

                                # Index by expiry
                                if expiry_str not in self.options_by_expiry:
                                    self.options_by_expiry[expiry_str] = []
                                self.options_by_expiry[expiry_str].append(instrument)

                        except (ValueError, TypeError) as e:
                            logger.warning(f"Failed to parse expiry for {symbol}: {e}")

            logger.info(f"✅ Loaded instruments:")
            logger.info(f"   {len(futures_symbols)} futures: {', '.join(sorted(futures_symbols))}")
            logger.info(f"   {len(options_symbols)} options ({len(self.options_by_expiry)} expiries)")

            # Subscribe to futures tickers
            futures_channels = [f"ticker:{symbol}" for symbol in sorted(futures_symbols)]
            if futures_channels:
                await self.delta_client.ws.subscribe(futures_channels)
                logger.info(f"Subscribed to {len(futures_channels)} futures ticker channels")

            # Mark last refresh time
            self.last_option_chain_fetch = datetime.utcnow()

            # Notify subscribers
            await self._notify_instrument_update()

        except Exception as e:
            logger.error(f"Failed to refresh instruments: {e}")

    async def _periodic_option_chain_refresh(self) -> None:
        """Periodically refresh option chains (every 5 minutes)."""
        while self.running:
            try:
                # Check if refresh is needed
                if self.last_option_chain_fetch:
                    elapsed = datetime.utcnow() - self.last_option_chain_fetch
                    if elapsed < self.option_chain_fetch_interval:
                        await asyncio.sleep(30)  # Check again in 30 seconds
                        continue

                logger.info("Refreshing option chains...")
                await self._refresh_instruments()

            except Exception as e:
                logger.error(f"Error in option chain refresh: {e}")

            # Wait before next refresh
            await asyncio.sleep(30)

    def _on_ticker_message(self, message: WebSocketMessage) -> None:
        """Handle incoming ticker message from WebSocket."""
        try:
            data = message.data
            symbol = data.get("symbol", "")

            if not symbol:
                return

            # Parse ticker data
            update = TickerUpdate(
                symbol=symbol,
                mark_price=float(data.get("mark_price", 0)),
                spot_price=data.get("spot_price"),
                ltp=data.get("ltp"),
                bid=data.get("best_bid"),
                ask=data.get("best_ask"),
                iv=data.get("ask_iv") or data.get("bid_iv"),  # IV from option
                timestamp=message.timestamp
            )

            # Parse greeks if present
            greeks = data.get("greeks", {})
            if greeks:
                update.greeks = {
                    "delta": float(greeks.get("delta", 0)),
                    "gamma": float(greeks.get("gamma", 0)),
                    "theta": float(greeks.get("theta", 0)),
                    "vega": float(greeks.get("vega", 0)),
                    "rho": float(greeks.get("rho", 0)),
                }

            # Cache update
            self.tickers[symbol] = update

            # Call handlers
            self._notify_ticker_update(symbol, update)

        except Exception as e:
            logger.error(f"Error processing ticker message: {e}")

    def _on_mark_price_message(self, message: WebSocketMessage) -> None:
        """Handle mark price update."""
        try:
            data = message.data
            symbol = data.get("symbol", "")

            if not symbol or symbol not in self.tickers:
                # Create new ticker update
                update = TickerUpdate(
                    symbol=symbol,
                    mark_price=float(data.get("mark_price", 0)),
                    timestamp=message.timestamp
                )
                self.tickers[symbol] = update
            else:
                # Update existing
                self.tickers[symbol].mark_price = float(data.get("mark_price", 0))
                self.tickers[symbol].timestamp = message.timestamp

            # Notify handlers
            if symbol in self.tickers:
                self._notify_ticker_update(symbol, self.tickers[symbol])

        except Exception as e:
            logger.error(f"Error processing mark price message: {e}")

    def _on_trade_message(self, message: WebSocketMessage) -> None:
        """Handle trade/fill message."""
        try:
            data = message.data
            symbol = data.get("symbol", "")

            if not symbol:
                return

            # Parse trade data
            update = TradeUpdate(
                symbol=symbol,
                price=float(data.get("price", 0)),
                size=float(data.get("size", 0)),
                side=data.get("side", "").lower(),
                timestamp=message.timestamp
            )

            self.trades[symbol] = update

            # Call handlers
            self._notify_trade_update(symbol, update)

        except Exception as e:
            logger.error(f"Error processing trade message: {e}")

    # =========================================================================
    # Public API
    # =========================================================================

    def get_instrument(self, symbol: str) -> Optional[Instrument]:
        """Get instrument by symbol."""
        return self.all_instruments.get(symbol)

    def get_futures(self, underlying: Optional[str] = None) -> List[Instrument]:
        """Get futures instruments (optionally filtered by underlying)."""
        if underlying:
            return [i for i in self.futures.values() if i.underlying == underlying.upper()]
        return list(self.futures.values())

    def get_options(
        self,
        underlying: Optional[str] = None,
        expiry_date: Optional[str] = None
    ) -> List[Instrument]:
        """
        Get options instruments.

        Args:
            underlying: BTC or ETH (optional)
            expiry_date: YYYY-MM-DD (optional)

        Returns:
            List of matching options
        """
        options = list(self.options.values())

        if underlying:
            options = [o for o in options if o.underlying == underlying.upper()]

        if expiry_date:
            options = [o for o in options if o.expiry_date == expiry_date]

        return options

    def get_expiry_dates(self) -> List[str]:
        """Get all available option expiry dates."""
        return sorted(self.options_by_expiry.keys())

    def get_ticker(self, symbol: str) -> Optional[TickerUpdate]:
        """Get latest ticker for a symbol."""
        return self.tickers.get(symbol)

    def get_tickers(self, symbols: Optional[List[str]] = None) -> Dict[str, TickerUpdate]:
        """Get tickers for specified symbols (or all if None)."""
        if symbols:
            return {s: self.tickers[s] for s in symbols if s in self.tickers}
        return dict(self.tickers)

    def subscribe_ticker(self, symbol: str, handler: Callable[[TickerUpdate], None]) -> None:
        """Subscribe to ticker updates for a symbol."""
        if symbol not in self.ticker_handlers:
            self.ticker_handlers[symbol] = []
        self.ticker_handlers[symbol].append(handler)

    def unsubscribe_ticker(self, symbol: str, handler: Callable) -> None:
        """Unsubscribe from ticker updates."""
        if symbol in self.ticker_handlers:
            self.ticker_handlers[symbol] = [
                h for h in self.ticker_handlers[symbol] if h != handler
            ]

    def subscribe_trades(self, symbol: str, handler: Callable[[TradeUpdate], None]) -> None:
        """Subscribe to trade updates for a symbol."""
        if symbol not in self.trade_handlers:
            self.trade_handlers[symbol] = []
        self.trade_handlers[symbol].append(handler)

    def unsubscribe_trades(self, symbol: str, handler: Callable) -> None:
        """Unsubscribe from trade updates."""
        if symbol in self.trade_handlers:
            self.trade_handlers[symbol] = [
                h for h in self.trade_handlers[symbol] if h != handler
            ]

    def subscribe_instruments(self, handler: Callable[[], None]) -> None:
        """Subscribe to instrument updates (when new options expire, etc)."""
        self.instrument_update_handlers.append(handler)

    def unsubscribe_instruments(self, handler: Callable) -> None:
        """Unsubscribe from instrument updates."""
        self.instrument_update_handlers = [
            h for h in self.instrument_update_handlers if h != handler
        ]

    # =========================================================================
    # Internal helpers
    # =========================================================================

    def _notify_ticker_update(self, symbol: str, update: TickerUpdate) -> None:
        """Notify all handlers for a ticker symbol."""
        handlers = self.ticker_handlers.get(symbol, [])
        for handler in handlers:
            try:
                handler(update)
            except Exception as e:
                logger.error(f"Error in ticker handler for {symbol}: {e}")

    def _notify_trade_update(self, symbol: str, update: TradeUpdate) -> None:
        """Notify all handlers for a trade symbol."""
        handlers = self.trade_handlers.get(symbol, [])
        for handler in handlers:
            try:
                handler(update)
            except Exception as e:
                logger.error(f"Error in trade handler for {symbol}: {e}")

    async def _notify_instrument_update(self) -> None:
        """Notify all instrument update subscribers."""
        for handler in self.instrument_update_handlers:
            try:
                handler()
            except Exception as e:
                logger.error(f"Error in instrument update handler: {e}")
