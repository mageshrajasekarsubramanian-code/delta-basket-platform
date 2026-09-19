#!/usr/bin/env python3
"""
Debug script to inspect Delta products API response.
"""

import asyncio
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.config import config
from app.services.delta_client import DeltaClient


async def main():
    """Fetch and inspect products."""
    # Validate config
    is_valid, errors = config.validate()
    if not is_valid:
        print("❌ Config validation failed:")
        for err in errors:
            print(f"   - {err}")
        return False

    # Create client
    client = DeltaClient(config.delta.api_key, config.delta.api_secret)

    try:
        print("Fetching products...")
        resp = await client.rest.get_products(limit=500)

        if not resp.get("success"):
            print(f"❌ API Error: {resp.get('error')}")
            return False

        products = resp.get("result", [])
        print(f"\n✅ Got {len(products)} products\n")

        # Show structure
        if products:
            print("=" * 70)
            print("Sample product #1:")
            print("=" * 70)
            sample = products[0]
            print(json.dumps(sample, indent=2, default=str))

        # Find BTC/ETH products
        print("\n" + "=" * 70)
        print("Looking for BTC/ETH futures and options...")
        print("=" * 70)

        btc_items = []
        eth_items = []

        for product in products:
            symbol = product.get("symbol", "")
            state = product.get("state", "")
            contract_type = product.get("contract_type", "")
            underlying = product.get("underlying_asset", {})
            underlying_symbol = underlying.get("symbol", "") if isinstance(underlying, dict) else underlying

            if "BTC" in symbol or underlying_symbol == "BTC":
                btc_items.append((symbol, contract_type, state))
            elif "ETH" in symbol or underlying_symbol == "ETH":
                eth_items.append((symbol, contract_type, state))

        print(f"\nBTC items ({len(btc_items)}):")
        for symbol, ct, state in btc_items[:10]:
            print(f"  {symbol}: {ct} (state: {state})")
        if len(btc_items) > 10:
            print(f"  ... and {len(btc_items) - 10} more")

        # Look specifically for perpetual futures
        print("\n" + "=" * 70)
        print("Searching for perpetual futures...")
        print("=" * 70)
        perps = []
        for product in products:
            ct = product.get("contract_type", "")
            sym = product.get("symbol", "")
            if "perpetual" in ct.lower() or "future" in ct.lower():
                perps.append((sym, ct))

        if perps:
            print(f"Found {len(perps)} perpetuals:")

            # Look for BTC and ETH specifically
            btc_perps = [p for p in perps if "BTC" in p[0]]
            eth_perps = [p for p in perps if "ETH" in p[0]]

            print(f"\nBTC perpetuals: {btc_perps}")
            print(f"ETH perpetuals: {eth_perps}")

            # Show all
            print(f"\nAll {len(perps)} perpetuals:")
            for sym, ct in perps:
                print(f"  {sym}: {ct}")
        else:
            print("❌ No perpetual futures found in products list!")
            print("\nLooking for USD contracts (possible perpetuals)...")
            usd_contracts = []
            for product in products:
                sym = product.get("symbol", "")
                if "USD" in sym or "usd" in sym.lower():
                    ct = product.get("contract_type", "")
                    usd_contracts.append((sym, ct))

            if usd_contracts:
                print(f"Found {len(usd_contracts)} USD-related contracts:")
                for sym, ct in usd_contracts[:15]:
                    print(f"  {sym}: {ct}")
            else:
                print("No USD contracts found either")

        print(f"\nETH items ({len(eth_items)}):")
        for symbol, ct, state in eth_items[:10]:
            print(f"  {symbol}: {ct} (state: {state})")
        if len(eth_items) > 10:
            print(f"  ... and {len(eth_items) - 10} more")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False

    finally:
        await client.rest.close()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
