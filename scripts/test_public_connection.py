"""Read-only Public.com API connection test.

Verifies:
  1. PUBLIC_API_SECRET + PUBLIC_ACCOUNT_NUMBER are set in .env
  2. Authentication succeeds
  3. We can list accounts (sees Brokerage + IRA if present)
  4. We can fetch portfolio (positions, cash, equity)

Does NOT:
  - Place any orders
  - Modify any account state
  - Print API key value (only prefix for verification)

Run:  python scripts/test_public_connection.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Load .env
from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

API_SECRET = os.getenv("PUBLIC_API_SECRET", "")
ACCOUNT_NUMBER = os.getenv("PUBLIC_ACCOUNT_NUMBER", "")


def main():
    print("=" * 70)
    print("PUBLIC.COM CONNECTION TEST (read-only)")
    print("=" * 70)

    # Step 1: env check
    if not API_SECRET:
        print("✗ PUBLIC_API_SECRET not set in .env")
        return 1
    if not ACCOUNT_NUMBER:
        print("✗ PUBLIC_ACCOUNT_NUMBER not set in .env")
        return 1
    # Print only first 4 + last 4 chars to confirm key is loaded without leaking
    masked = f"{API_SECRET[:4]}...{API_SECRET[-4:]}" if len(API_SECRET) >= 12 else "(too short)"
    print(f"✓ env vars loaded:")
    print(f"  PUBLIC_API_SECRET    = {masked}")
    print(f"  PUBLIC_ACCOUNT_NUMBER = {ACCOUNT_NUMBER}")
    print()

    # Step 2: build client
    try:
        from public_api_sdk import (
            PublicApiClient, ApiKeyAuthConfig, PublicApiClientConfiguration,
        )
        client = PublicApiClient(
            ApiKeyAuthConfig(api_secret_key=API_SECRET),
            config=PublicApiClientConfiguration(default_account_number=ACCOUNT_NUMBER),
        )
        print(f"✓ PublicApiClient instantiated")
    except Exception as e:
        print(f"✗ client init failed: {type(e).__name__}: {e}")
        return 1

    # Step 3: list accounts (should see brokerage + IRA if both exist)
    try:
        accounts = client.get_accounts()
        print(f"\n✓ get_accounts() succeeded — {len(accounts.accounts) if hasattr(accounts, 'accounts') else 'N/A'} account(s):")
        accts_list = accounts.accounts if hasattr(accounts, 'accounts') else accounts
        for a in accts_list:
            attrs = {k: v for k, v in vars(a).items() if not k.startswith('_')}
            print(f"  account_id: {attrs.get('account_id', attrs.get('id', '?'))}")
            print(f"    type: {attrs.get('account_type', attrs.get('type', '?'))}")
            print(f"    status: {attrs.get('status', '?')}")
    except Exception as e:
        print(f"✗ get_accounts failed: {type(e).__name__}: {e}")
        return 1

    # Step 4: fetch portfolio for the configured account
    try:
        portfolio = client.get_portfolio(account_id=ACCOUNT_NUMBER)
        print(f"\n✓ get_portfolio(account_id={ACCOUNT_NUMBER}) succeeded")
        attrs = {k: v for k, v in vars(portfolio).items() if not k.startswith('_')}
        for k, v in attrs.items():
            if k == "positions":
                print(f"  positions: {len(v) if v else 0} holding(s)")
                if v:
                    for p in v[:5]:
                        p_attrs = {pk: pv for pk, pv in vars(p).items() if not pk.startswith('_')}
                        sym = p_attrs.get('instrument', {})
                        print(f"    {sym}: qty={p_attrs.get('quantity')}")
            elif not isinstance(v, (list, dict)):
                print(f"  {k}: {v}")
    except Exception as e:
        print(f"✗ get_portfolio failed: {type(e).__name__}: {e}")
        return 1

    print()
    print("=" * 70)
    print("✓ CONNECTION VERIFIED — Public.com API is reachable + authenticated")
    print("=" * 70)
    print()
    print("NEXT STEPS (do NOT do these yet):")
    print("  1. If your IRA isn't open yet → open it via the dashboard")
    print("  2. Once IRA approved → generate a NEW API key scoped to the IRA")
    print("  3. Build broker abstraction layer (docs/MIGRATION_ALPACA_TO_PUBLIC.md)")
    print("  4. Continue Alpaca paper trading for ~85 more days")
    print("  5. Only after go_live_gate.py shows 9/9 → flip BROKER=public_live")
    return 0


if __name__ == "__main__":
    sys.exit(main())
