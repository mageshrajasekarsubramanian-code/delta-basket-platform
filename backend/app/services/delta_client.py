"""
Delta Exchange India API client wrapper.

Handles:
- REST API calls with HMAC-SHA256 signing
- WebSocket connection with exponential backoff reconnection
- System status monitoring
- REST fallback for market data when WebSocket is unavailable
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

import httpx
import websockets
from websockets.client import WebSocketClientProtocol

logger = logging.getLogger(__name__)


class SystemStatus(Enum):
    """Exchange system status."""
    OPERATIONAL = "operational"
    MAINTENANCE = "maintenance"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


@dataclass
class WebSocketMessage:
    """Parsed WebSocket message."""
    type: str
    channel: str
    data: Dict[str, Any]
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()


class DeltaRestClient:
    """
    Delta Exchange REST API client with HMAC-SHA256 authentication.

    Signature is computed as: HMAC-SHA256(method + timestamp + path + query + body)
    """

    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.india.delta.exchange"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        # Note: verify=False for local testing due to SSL cert issues
        # In production (Docker/DO), this should be verify=True
        logger.debug("⚠️  SSL verification disabled (development mode)")
        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=30.0,
            verify=False  # Disable SSL verification for local testing
        )

    def _generate_signature(self, method: str, timestamp: str, path: str, query: str = "", body: str = "") -> str:
        """
        Generate HMAC-SHA256 signature.

        Args:
            method: HTTP method (GET, POST, etc.)
            timestamp: Unix timestamp as string
            path: Request path (e.g., "/v2/tickers")
            query: Query string (e.g., "symbol=BTCUSD")
            body: Request body as JSON string

        Returns:
            Hex-encoded HMAC-SHA256 signature
        """
        message = method + timestamp + path + query + body
        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        authenticated: bool = False
    ) -> Dict[str, Any]:
        """
        Make authenticated or public REST request.

        Args:
            method: HTTP method
            path: API path (e.g., "/v2/tickers")
            params: Query parameters
            json_body: JSON request body
            authenticated: Whether this request requires authentication

        Returns:
            Parsed response JSON

        Raises:
            httpx.HTTPError: On network/HTTP errors
            ValueError: On signature/auth errors
        """
        timestamp = str(int(time.time()))

        # Build query string
        query_string = ""
        if params:
            sorted_params = sorted(params.items())
            query_string = "&".join(f"{k}={v}" for k, v in sorted_params)

        # Build body
        body_string = json.dumps(json_body) if json_body else ""

        # Prepare headers
        headers = {
            "User-Agent": "DeltaBasketPlatform/1.0",
        }

        if authenticated:
            signature = self._generate_signature(method, timestamp, path, query_string, body_string)
            headers.update({
                "api-key": self.api_key,
                "signature": signature,
                "timestamp": timestamp,
            })

        # Make request
        try:
            response = await self.client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers=headers
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"REST request failed: {method} {path} - {e}")
            raise

    async def get_tickers(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """Get live ticker data."""
        params = {}
        if symbols:
            params["symbols"] = ",".join(symbols)

        return await self._request("GET", "/v2/tickers", params=params)

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Get ticker for a specific symbol."""
        return await self._request("GET", f"/v2/tickers/{symbol}")

    async def get_products(self, limit: int = 100, after: Optional[str] = None) -> Dict[str, Any]:
        """Get list of available products."""
        params = {"limit": limit}
        if after:
            params["after"] = after

        return await self._request("GET", "/v2/products", params=params)

    async def get_product(self, symbol: str) -> Dict[str, Any]:
        """Get details for a specific product."""
        return await self._request("GET", f"/v2/products/{symbol}")

    async def get_orderbook(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        """Get order book for a symbol."""
        params = {"symbol": symbol, "depth": depth}
        return await self._request("GET", "/v2/orderbook", params=params)

    async def get_trades(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Get recent trades for a symbol."""
        params = {"symbol": symbol, "limit": limit}
        return await self._request("GET", "/v2/trades", params=params)

    async def get_indices(self) -> Dict[str, Any]:
        """Get spot price indices."""
        return await self._request("GET", "/v2/indices")

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


class DeltaWebSocketClient:
    """
    Delta Exchange WebSocket client with exponential backoff reconnection.

    Features:
    - Automatic reconnection on disconnect
    - Exponential backoff (capped at 5 minutes)
    - System status monitoring
    - Message parsing and callback handling
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        ws_url: str = "wss://stream.india.delta.exchange/v2/live",
        initial_backoff: int = 1,
        max_backoff: int = 300,  # 5 minutes
        backoff_multiplier: float = 2.0
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.ws_url = ws_url

        # Reconnection settings
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_multiplier = backoff_multiplier
        self.current_backoff = initial_backoff

        # Connection state
        self.ws: Optional[WebSocketClientProtocol] = None
        self.connected = False
        self.running = False

        # System status
        self.system_status = SystemStatus.UNKNOWN

        # Message handlers
        self.message_handlers: Dict[str, List[Callable]] = {}

        # Subscriptions
        self.subscriptions: List[str] = []

    def add_message_handler(self, channel: str, handler: Callable[[WebSocketMessage], None]) -> None:
        """Register a callback for a specific channel."""
        if channel not in self.message_handlers:
            self.message_handlers[channel] = []
        self.message_handlers[channel].append(handler)

    def remove_message_handler(self, channel: str, handler: Callable) -> None:
        """Remove a message handler."""
        if channel in self.message_handlers:
            self.message_handlers[channel] = [
                h for h in self.message_handlers[channel] if h != handler
            ]

    async def subscribe(self, channels: List[str]) -> None:
        """Subscribe to public channels."""
        self.subscriptions.extend(channels)

        if self.connected:
            for channel in channels:
                await self._send_subscription(channel)

    async def _send_subscription(self, channel: str) -> None:
        """Send subscription message for a single channel."""
        if not self.ws:
            return

        message = {
            "type": "subscribe",
            "channel": channel
        }

        try:
            await self.ws.send(json.dumps(message))
            logger.debug(f"Subscribed to {channel}")
        except Exception as e:
            logger.error(f"Failed to subscribe to {channel}: {e}")

    async def _send_auth(self) -> None:
        """Send authentication message for private channels (if needed)."""
        # For now, we only handle public channels
        # Private channel auth can be added in phase 2
        pass

    async def connect(self) -> None:
        """Connect to WebSocket and start listening."""
        self.running = True

        while self.running:
            try:
                logger.info(f"Connecting to {self.ws_url}...")
                self.ws = await websockets.connect(self.ws_url)
                self.connected = True
                self.current_backoff = self.initial_backoff
                logger.info("WebSocket connected")

                # Subscribe to configured channels
                for channel in self.subscriptions:
                    await self._send_subscription(channel)

                # Listen for messages
                await self._listen()

            except asyncio.CancelledError:
                logger.info("WebSocket connection cancelled")
                self.connected = False
                self.running = False
                break

            except Exception as e:
                self.connected = False
                logger.error(f"WebSocket error: {e}")

                if self.running:
                    await self._backoff_and_reconnect()

    async def _listen(self) -> None:
        """Listen for WebSocket messages."""
        try:
            async for raw_message in self.ws:
                try:
                    data = json.loads(raw_message)
                    message = self._parse_message(data)

                    # Handle system status updates
                    if message.channel == "system_status":
                        self._handle_system_status(message.data)

                    # Call registered handlers
                    if message.channel in self.message_handlers:
                        for handler in self.message_handlers[message.channel]:
                            try:
                                handler(message)
                            except Exception as e:
                                logger.error(f"Handler error for {message.channel}: {e}")

                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse WebSocket message: {raw_message}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.info("WebSocket connection closed")
            self.connected = False

    def _parse_message(self, data: Dict[str, Any]) -> WebSocketMessage:
        """Parse incoming WebSocket message."""
        msg_type = data.get("type", "unknown")
        channel = data.get("channel", "unknown")
        payload = data.get("data", {})

        return WebSocketMessage(
            type=msg_type,
            channel=channel,
            data=payload
        )

    def _handle_system_status(self, data: Dict[str, Any]) -> None:
        """Handle system status updates."""
        status_str = data.get("status", "unknown").lower()

        try:
            new_status = SystemStatus(status_str)
        except ValueError:
            new_status = SystemStatus.UNKNOWN

        old_status = self.system_status
        self.system_status = new_status

        if old_status != new_status:
            logger.warning(f"System status changed: {old_status.value} → {new_status.value}")

    async def _backoff_and_reconnect(self) -> None:
        """Apply exponential backoff and reconnect."""
        backoff_time = min(self.current_backoff, self.max_backoff)
        logger.warning(f"Reconnecting in {backoff_time} seconds (backoff: {self.current_backoff}s → max: {self.max_backoff}s)")

        await asyncio.sleep(backoff_time)
        self.current_backoff = min(
            int(self.current_backoff * self.backoff_multiplier),
            self.max_backoff
        )

    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        self.running = False
        if self.ws:
            await self.ws.close()
        self.connected = False

    def get_system_status(self) -> SystemStatus:
        """Get current system status."""
        return self.system_status

    def is_operational(self) -> bool:
        """Check if exchange is operational."""
        return self.system_status == SystemStatus.OPERATIONAL


class DeltaClient:
    """
    Unified Delta Exchange client combining REST and WebSocket.

    Provides a single interface for both public REST calls and WebSocket subscriptions.
    """

    def __init__(self, api_key: str, api_secret: str):
        self.rest = DeltaRestClient(api_key, api_secret)
        self.ws = DeltaWebSocketClient(api_key, api_secret)
        self.ws_task: Optional[asyncio.Task] = None

    async def start(self, channels: Optional[List[str]] = None) -> None:
        """Start WebSocket connection and subscribe to channels."""
        if channels:
            await self.ws.subscribe(channels)

        # Start WebSocket in background
        self.ws_task = asyncio.create_task(self.ws.connect())

    async def stop(self) -> None:
        """Stop WebSocket connection and close REST client."""
        await self.ws.disconnect()
        if self.ws_task:
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
        await self.rest.close()

    def add_message_handler(self, channel: str, handler: Callable[[WebSocketMessage], None]) -> None:
        """Register a message handler for a WebSocket channel."""
        self.ws.add_message_handler(channel, handler)

    async def get_tickers(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        """Get current ticker data via REST."""
        return await self.rest.get_tickers(symbols)

    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Get ticker for a specific symbol via REST."""
        return await self.rest.get_ticker(symbol)

    async def get_products(self, limit: int = 100, after: Optional[str] = None) -> Dict[str, Any]:
        """Get products list via REST."""
        return await self.rest.get_products(limit, after)

    async def get_product(self, symbol: str) -> Dict[str, Any]:
        """Get product details via REST."""
        return await self.rest.get_product(symbol)

    def is_operational(self) -> bool:
        """Check if exchange is operational."""
        return self.ws.is_operational()

    def get_system_status(self) -> SystemStatus:
        """Get current system status."""
        return self.ws.get_system_status()
