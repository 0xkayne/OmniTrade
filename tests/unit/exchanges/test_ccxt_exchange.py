"""Tests for CCXTExchange helper logic (no network)."""

from src.exchanges.ccxt_exchange import CCXTExchange, _is_placeholder_value


def _hyperliquid_config(options=None):
    """A minimal config dict matching the exchanges.yaml hyperliquid section."""
    config = {
        "type": "ccxt",
        "enabled": True,
        "default_network": "testnet",
        "networks": {
            "mainnet": {
                "rest_base_url": "https://api.hyperliquid.xyz",
                "websocket_url": "wss://api.hyperliquid.xyz/ws",
            },
            "testnet": {
                "rest_base_url": "https://api.hyperliquid-testnet.xyz",
                "websocket_url": "wss://api.hyperliquid-testnet.xyz/ws",
            },
        },
        "fees": {"taker": 0.00015, "maker": 0.00045},
    }
    if options is not None:
        config["options"] = options
    return config


class _FakeCCXT:
    """Minimal stand-in for the ccxt instance used by _filter_hip3_dexes."""

    def __init__(self, perp_dexs):
        self.options = {
            "fetchMarkets": {
                "types": ["spot", "swap", "hip3"],
                "hip3": {"dexes": ["hyna", "xyz", "io"]},
            }
        }
        self._perp_dexs = perp_dexs

    async def publicPostInfo(self, params):
        return self._perp_dexs


# ---- _is_placeholder_value ----


def test_placeholder_values_detected():
    assert _is_placeholder_value("your_binance_api_key") is True
    assert _is_placeholder_value("your_private_key") is True
    assert _is_placeholder_value("") is True
    assert _is_placeholder_value("xxxxx") is True
    assert _is_placeholder_value("0000") is True  # all-same-character sentinel


def test_real_credentials_not_marked_placeholder():
    # Real-looking keys / addresses must NOT be dropped.
    assert _is_placeholder_value("0x702b0677a6c4356fdfbef6b372f1e7a478448cbb") is False
    assert _is_placeholder_value("ABC123def456xyz789") is False


# ---- _build_ccxt_config merge ----


def test_build_ccxt_config_merges_fetchmarkets_options():
    options = {"fetchMarkets": {"types": ["spot", "swap", "hip3"], "hip3": {"dexes": ["hyna", "xyz", "io"]}}}
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(options), {})
    cfg = ex._build_ccxt_config()
    fm = cfg["options"]["fetchMarkets"]
    assert fm["types"] == ["spot", "swap", "hip3"]
    assert fm["hip3"]["dexes"] == ["hyna", "xyz", "io"]
    assert cfg["options"]["defaultType"] == "swap"
    assert cfg["options"]["testnet"] is True


def test_build_ccxt_config_default_excludes_hip3():
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(), {})
    fm = ex._build_ccxt_config()["options"]["fetchMarkets"]
    assert fm["types"] == ["spot", "swap"]
    assert "hip3" not in fm


# ---- _filter_hip3_dexes guard ----


async def test_filter_hip3_dexes_drops_missing_dex():
    # testnet: `io` (EntropyIO) is mainnet-only, so it is dropped, hyna/xyz kept.
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(), {})
    ex.ccxt_exchange = _FakeCCXT([None, {"name": "hyna"}, {"name": "xyz"}])
    await ex._filter_hip3_dexes()
    fm = ex.ccxt_exchange.options["fetchMarkets"]
    assert fm["hip3"]["dexes"] == ["hyna", "xyz"]
    assert fm["types"] == ["spot", "swap", "hip3"]


async def test_filter_hip3_dexes_disables_when_all_missing():
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(), {})
    ex.ccxt_exchange = _FakeCCXT([None, {"name": "other"}])
    await ex._filter_hip3_dexes()
    fm = ex.ccxt_exchange.options["fetchMarkets"]
    assert "hip3" not in fm["types"]
    assert "dexes" not in fm.get("hip3", {})


async def test_filter_hip3_dexes_ignores_non_hyperliquid():
    ex = CCXTExchange("binance", _hyperliquid_config(), {})
    ex.ccxt_exchange = _FakeCCXT([None, {"name": "hyna"}])
    await ex._filter_hip3_dexes()
    fm = ex.ccxt_exchange.options["fetchMarkets"]
    assert fm["types"] == ["spot", "swap", "hip3"]  # untouched


async def test_filter_hip3_dexes_disables_on_perpdexs_failure():
    ex = CCXTExchange("hyperliquid", _hyperliquid_config(), {})
    fake = _FakeCCXT([None, {"name": "hyna"}])

    async def boom(params):
        raise RuntimeError("network")

    fake.publicPostInfo = boom
    ex.ccxt_exchange = fake
    await ex._filter_hip3_dexes()
    fm = ex.ccxt_exchange.options["fetchMarkets"]
    assert "hip3" not in fm["types"]
    assert "dexes" not in fm.get("hip3", {})
