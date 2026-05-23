import hashlib
import hmac
import time

import pytest
import yaml

import ccxt.async_support as ccxt_mod


def _load_binance_secrets():
    with open("config/secrets.yaml") as f:
        all_secrets = yaml.safe_load(f)
    return all_secrets.get("binance", {})


@pytest.mark.network
async def test_binance():
    print("\n=== 测试 Binance (demo trading) ===")
    secrets = _load_binance_secrets()
    key = secrets["api_key"]
    secret = secrets["secret"]

    exchange = ccxt_mod.binance({
        "apiKey": key,
        "secret": secret,
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
    })

    # Binance demo trading uses demo-api.binance.com.
    # ccxt 4.5.54 doesn't fully support demo URL routing for signed
    # endpoints, so we verify connectivity with absolute URLs.
    demo_base = exchange.urls["demo"]["private"]

    try:
        # 1. Public: exchange info (works fine)
        resp = await exchange.fetch(f"{demo_base}/exchangeInfo", "GET")
        symbols = [s["symbol"] for s in resp.get("symbols", [])]
        print(f"✅ 公开行情 OK — {len(symbols)} spot 交易对")
        assert len(symbols) > 100

        # 2. Private: account balance (manual signature, absolute demo URL)
        ts = int(time.time() * 1000)
        qs = f"timestamp={ts}&recvWindow=10000"
        sig = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        resp = await exchange.fetch(
            f"{demo_base}/account?{qs}&signature={sig}",
            "GET",
            {"X-MBX-APIKEY": key},
        )

        account_type = resp.get("accountType", "UNKNOWN")
        usdt_bal = next(
            (b["free"] for b in resp.get("balances", []) if b["asset"] == "USDT"),
            "0",
        )
        print(f"✅ 账户验证 OK — {account_type}, USDT: {usdt_bal}")
        assert account_type == "SPOT"

    finally:
        await exchange.close()
