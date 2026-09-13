# Delta Basket Platform

A standalone Python platform for trading BTC/ETH options on Delta Exchange India with:
- **Basket-level P&L tracking** (across multiple option legs)
- **Stop-loss / Take-profit triggers** at the basket level (based on leg premium or underlying price)
- **Mobile-responsive live dashboard**
- **24/7 autonomous order execution** with price-chase and entry timeout safety

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Delta Basket Platform                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌──────────────────┐         ┌──────────────────────────┐  │
│  │  Market Data     │         │  Order Execution Service │  │
│  │  Service         │◄────────┤  (buy-first, price-chase)│  │
│  │  (WebSocket +    │         │                          │  │
│  │   REST fallback) │         └──────────────────────────┘  │
│  └────────┬─────────┘                                        │
│           │                                                  │
│           ▼                                                  │
│  ┌──────────────────┐         ┌──────────────────────────┐  │
│  │  Delta REST +    │         │  Basket Engine           │  │
│  │  WebSocket       │◄───────►│  (lifecycle, P&L calc)   │  │
│  │  Client Wrapper  │         │                          │  │
│  │  (auth, backoff) │         └──────────────────────────┘  │
│  └─────────┬────────┘                                        │
│            │                                                 │
│            ▼ (live ticks)                    ┌────────────┐ │
│                                  ┌──────────►│ PostgreSQL │ │
│                                  │           │ Database   │ │
│                    ┌─────────────┐          │ (baskets,  │ │
│                    │ Trigger     │          │  legs,     │ │
│                    │ Monitor     │          │  orders)   │ │
│                    │ (SL/TP)     │          └────────────┘ │
│                    └─────────────┘                           │
│                          │                                   │
│                    ┌─────▼──────────┐                        │
│                    │ REST API +     │                        │
│                    │ WebSocket Push │                        │
│                    │ (dashboard)    │                        │
│                    └────────────────┘                        │
│                          │                                   │
│                          ▼                                   │
│                    ┌──────────────┐                          │
│                    │  Vue.js      │                          │
│                    │  Dashboard   │                          │
│                    │  (mobile-    │                          │
│                    │   first)     │                          │
│                    └──────────────┘                          │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 1: Delta API Wrapper (Current)

### What's Included

✅ **DeltaRestClient**
  - REST API calls with HMAC-SHA256 authentication
  - Public endpoints: tickers, products, orderbook, trades
  - Proper error handling and timeouts

✅ **DeltaWebSocketClient**
  - WebSocket connection to Delta's live market data feed
  - Exponential backoff reconnection (capped at 5 minutes)
  - System status monitoring
  - Message parsing and callback routing

✅ **DeltaClient** (unified interface)
  - Combines REST and WebSocket
  - Clean async/await pattern
  - Ready for integration with higher services

✅ **Configuration Management**
  - YAML-based config (credentials, database, platform settings)
  - Environment variable overrides (for Docker)
  - Validation on startup

✅ **Test Script**
  - Verifies REST API connectivity
  - Tests WebSocket subscriptions and live message flow
  - Validates exponential backoff logic

---

## Quick Start

### 1. Install Dependencies

```bash
cd delta-basket-platform
python -m venv venv

# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Create Configuration

```bash
# Copy the example config
cp config.example.yaml config.yaml
```

Edit `config.yaml` and fill in your Delta Exchange API credentials:

```yaml
delta:
  api_key: "your_api_key_here"
  api_secret: "your_api_secret_here"
```

**Where to get your API key:**
1. Log in to [Delta Exchange India](https://india.delta.exchange)
2. Account → API Settings
3. Create a new API key with **Trading + Read Data** permissions
4. **Important**: Whitelist the static IP of your deployment droplet in Delta's API Management

### 3. Run the Integration Test

```bash
python test_delta_api.py
```

Expected output:
```
TEST 1: REST API Connectivity
✅ REST API working
   BTC/USD Mark Price: $42500
   BTC/USD Spot Price: $42450

TEST 2: WebSocket Connection & Subscriptions
Listening for messages... (20 seconds)
✅ WebSocket connection successful
   Messages received:
     ✓ ticker: 15 messages
     ✓ mark_price: 18 messages
     ✓ trades: 8 messages
     ✓ system_status: 1 messages
   Exchange status: operational

TEST 3: Exponential Backoff Logic (Mock)
✅ Exponential backoff logic verified

Test Results: 3/3 passed
```

---

## Configuration Reference

### `config.yaml` - Delta Section

```yaml
delta:
  api_key: string              # Your Delta API key
  api_secret: string           # Your Delta API secret
  base_url: string             # API base URL (default: production)
  ws_url: string               # WebSocket URL
  
  # Reconnection settings (exponential backoff)
  ws_initial_backoff_seconds: int    # Start at 1s (default)
  ws_max_backoff_seconds: int        # Cap at 5 minutes (default)
  ws_backoff_multiplier: float       # 2x on each retry (default)
  
  # Market data
  public_channels: list        # Channels to subscribe to
  rest_fallback_enabled: bool  # Fall back to REST polling if WS fails
  rest_fallback_interval_seconds: int  # Poll every 2 sec
```

### Environment Variables (for Docker)

```bash
DELTA_API_KEY=your_key
DELTA_API_SECRET=your_secret
ENVIRONMENT=production
DEBUG=false
DB_HOST=postgres
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=delta_basket_db
```

---

## API Wrapper Usage

### Basic Usage

```python
from app.services.delta_client import DeltaClient
from app.config import config
import asyncio

async def main():
    # Create client
    client = DeltaClient(config.delta.api_key, config.delta.api_secret)
    
    # Start WebSocket and subscribe to channels
    await client.start(["ticker:BTCUSD", "mark_price:BTCUSD"])
    
    # Register message handler
    def on_ticker(msg):
        print(f"Ticker update: {msg.channel} - {msg.data}")
    
    client.add_message_handler("ticker:BTCUSD", on_ticker)
    
    # Let it run
    await asyncio.sleep(60)
    
    # Clean up
    await client.stop()

asyncio.run(main())
```

### REST-Only (no WebSocket)

```python
async def get_market_data():
    client = DeltaClient(api_key, api_secret)
    
    # Get current ticker
    ticker = await client.get_ticker("BTCUSD")
    print(ticker["result"]["mark_price"])
    
    # Get products
    products = await client.get_products()
    
    # No WebSocket, so just close REST
    await client.rest.close()
```

---

## System Status Monitoring

The platform automatically monitors Delta's system status:

```python
# Check current status
status = client.get_system_status()  # Returns: SystemStatus.OPERATIONAL

# Check if operational
if client.is_operational():
    print("✅ Ready to trade")
else:
    print("⚠️ Exchange maintenance or degraded mode")
```

**Behavior during maintenance:**
- New basket entries are paused
- Trigger-driven force-closes are paused
- Dashboard shows explicit "Exchange Maintenance" banner
- Triggers remain configured but don't fire
- On resume, triggers re-arm and evaluate immediately (gap risk acknowledged)

---

## WebSocket Reconnection Strategy

Exponential backoff with 5-minute cap:

```
Attempt  |  Backoff Wait  |  Total Elapsed
---------|----------------|---------------
   1     |      1s        |      1s
   2     |      2s        |      3s
   3     |      4s        |      7s
   4     |      8s        |     15s
   5     |     16s        |     31s
   6     |     32s        |     63s
   7     |     64s        |    127s
   8     |    128s        |    255s
   9     |    256s        |    511s
  10     |   300s (5min)  |   2811s (46 min)
  11+    |   300s (capped)|   ...
```

If WebSocket remains unavailable, the platform falls back to REST polling (`/v2/tickers`) every 2 seconds as a safety net for trigger monitoring.

---

## Known Limitations (Step 1)

- ⚠️ Credentials stored in plain text in `config.yaml` (phase 2 will add encryption)
- ⚠️ No database integration yet (step 3)
- ⚠️ No order execution yet (step 4)
- ⚠️ WebSocket only handles public channels (private channels for orders coming in step 4)
- ⚠️ No ticker filtering by expiry (will be added in step 2 when fetching option chains)

---

## Next Steps

After reviewing this step 1 foundation:

1. ✅ **Step 1 (CURRENT)**: Delta API wrapper ← You are here
2. **Step 2**: Market Data Service
   - Stream BTC/ETH futures + option chains
   - Filter by expiry date
   - Internal pub/sub for downstream services
3. **Step 3**: Basket/Leg data model + PostgreSQL persistence
4. **Step 4**: Order Execution Service
   - Buy-first sequencing
   - Price-chase algorithm for sell legs
   - Unhedged-exposure timeout auto-close
5. **Step 5**: P&L Engine
6. **Step 6**: Trigger Monitor (SL/TP)
7. **Step 7**: REST API for dashboard
8. **Step 8**: Mobile-first Vue.js dashboard
9. **Step 9**: Docker deployment to DigitalOcean

---

## Troubleshooting

### "DELTA_API_KEY is required"

Make sure `config.yaml` exists in the project root and contains your API credentials:

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your credentials
```

### WebSocket connection fails / keeps reconnecting

1. Check your internet connection
2. Verify API credentials are correct
3. Check if Delta Exchange is under maintenance (check [Delta status page](https://status.delta.exchange))
4. Verify your IP is whitelisted in Delta's API Management (for trading-permission keys)

### REST API returns 401 Unauthorized

- Check API key and secret are correct
- Verify timestamp is synced with system time (within 5 seconds of Delta's server)
- Ensure signature generation is correct (test with `test_delta_api.py`)

### "All messages lost" during reconnection

This is expected behavior. The platform uses live market data and does not cache ticks. On reconnection:
- WebSocket resubscribes immediately
- Basket triggers are re-evaluated against new prices
- Any gap during outage is a known risk (acknowledged in §5.3 of spec)

---

## References

- [Delta Exchange API Docs](https://docs.delta.exchange)
- [Delta Exchange India](https://india.delta.exchange)
- [API Rate Limits](https://docs.delta.exchange/#rate-limits)
- [WebSocket Channels](https://docs.delta.exchange/#websocket-channels)

---

## License

Internal use only.
