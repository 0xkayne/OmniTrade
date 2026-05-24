import ccxt.async_support as ccxt_mod
import pytest
import yaml


def _load_binance_secrets():
    with open("config/secrets.yaml") as f:
        all_secrets = yaml.safe_load(f)
    return all_secrets.get("binance", {})


@pytest.mark.network
async def test_binance():
    print("\n=== 测试 Binance (demo trading) ===")
    secrets = _load_binance_secrets()

    exchange = ccxt_mod.binance({
        "apiKey": secrets["api_key"],
        "secret": secrets["secret"],
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "fetchMarkets": ["spot"],
        },
    })
    exchange.enable_demo_trading(True)

    try:
        await exchange.load_markets()
        spot_count = sum(1 for m in exchange.markets.values() if m.get("spot"))
        print(f"✅ 行情加载 OK — {spot_count} spot 交易对")
        assert spot_count > 100

        balance = await exchange.fetch_balance()
        usdt = balance.get("USDT", {}).get("free", 0)
        print(f"💰 USDT: {usdt}")
        assert usdt > 0

    finally:
        await exchange.close()
