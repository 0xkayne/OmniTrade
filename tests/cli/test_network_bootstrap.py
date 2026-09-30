"""Offline checks that entry points use network-scoped credentials."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from src.cli.bootstrap import build_arb_scanner, build_backtest, build_orchestrator, build_price_watcher
from src.cli.main import app
from src.exchange.factory import ExchangeFactory
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.market.registry import InstrumentRegistry


@pytest.fixture
def network_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "exchanges.yaml").write_text(
        yaml.safe_dump(
            {
                "exchanges": {
                    "binance": {"enabled": True, "type": "ccxt", "default_network": "mainnet"},
                    "hyperliquid": {"enabled": False, "type": "ccxt", "default_network": "mainnet"},
                }
            }
        )
    )
    for network in NetworkType:
        (config_dir / f"secrets.{network.value}.yaml").write_text(
            yaml.safe_dump(
                {
                    "network": network.value,
                    "binance": {"apiKey": f"synthetic-binance-{network.value}"},
                    "hyperliquid": {
                        "master_wallet_address": f"synthetic-master-{network.value}",
                        "api_wallet_address": f"synthetic-api-wallet-{network.value}",
                        "api_wallet_private_key": f"synthetic-hyperliquid-{network.value}",
                    },
                }
            )
        )
    # A legacy file must never supply exchange credentials.
    (config_dir / "secrets.yaml").write_text(
        yaml.safe_dump(
            {
                "binance": {"apiKey": "synthetic-legacy"},
            }
        )
    )
    return config_dir


@pytest.mark.parametrize("network", [None, NetworkType.TESTNET, NetworkType.MAINNET])
async def test_orchestrator_selects_credentials_before_initialization(network_config, monkeypatch, network):
    initialize = AsyncMock(return_value={})
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", initialize)
    monkeypatch.setattr(InstrumentRegistry, "load_all", AsyncMock())

    await build_orchestrator(_store=MagicMock(), target_network=network, use_websocket=False)

    configs, secrets = initialize.await_args.args
    expected = network.value if network else "mainnet"
    assert configs["binance"]["default_network"] == expected
    assert secrets == {"binance": {"apiKey": f"synthetic-binance-{expected}"}}
    assert "target_network" not in initialize.await_args.kwargs


@pytest.mark.parametrize("builder", [build_arb_scanner, build_backtest])
@pytest.mark.parametrize("network", [None, NetworkType.TESTNET, NetworkType.MAINNET])
async def test_market_builders_select_credentials_before_initialization(
    network_config,
    monkeypatch,
    builder,
    network,
):
    from src.persistence import store as store_module

    exchanges = {"binance": object()}
    initialize = AsyncMock(return_value=exchanges)
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", initialize)
    load_markets = AsyncMock()
    monkeypatch.setattr(InstrumentRegistry, "load_all", load_markets)
    store = SimpleNamespace(initialize=AsyncMock(), load_instruments_by_query=AsyncMock(return_value=[]))
    monkeypatch.setattr(store_module, "PersistenceStore", MagicMock(return_value=store))
    (network_config / "watchlist.yaml").write_text("watchlist: []\n")

    result = await builder(target_network=network)

    configs, secrets = initialize.await_args.args
    expected = network.value if network else "mainnet"
    assert configs["binance"]["default_network"] == expected
    assert secrets == {"binance": {"apiKey": f"synthetic-binance-{expected}"}}
    assert initialize.await_args.kwargs == {}
    assert result[0] is exchanges
    store.initialize.assert_awaited_once()
    if builder is build_arb_scanner:
        assert result[2] is store
        store.load_instruments_by_query.assert_awaited_once()
    else:
        assert result[3] is store
        load_markets.assert_awaited_once_with(exchanges, store=None)


@pytest.mark.parametrize("explicit_common_path", [False, True])
async def test_watcher_keeps_telegram_separate_from_exchange_credentials(
    network_config,
    monkeypatch,
    explicit_common_path,
):
    initialize = AsyncMock(return_value={})
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", initialize)
    monkeypatch.setattr(InstrumentRegistry, "load_all", AsyncMock())
    (network_config / "watchlist.yaml").write_text("watchlist: []\n")
    common_path = network_config / ("notifications.yaml" if explicit_common_path else "secrets.yaml")
    common_path.write_text(
        yaml.safe_dump(
            {
                "telegram": {"bot_token": "synthetic-shared-telegram", "chat_id": "synthetic-chat"},
                "binance": {"apiKey": "synthetic-legacy"},
            }
        )
    )
    kwargs = {"common_secrets_config_path": common_path} if explicit_common_path else {}

    watcher = await build_price_watcher(
        _store=MagicMock(),
        target_network=NetworkType.TESTNET,
        **kwargs,
    )

    assert initialize.await_args.args[1] == {"binance": {"apiKey": "synthetic-binance-testnet"}}
    assert watcher._telegram.bot_token == "synthetic-shared-telegram"
    assert watcher._telegram.chat_ids == ["synthetic-chat"]


def _public_exchange():
    instrument = Instrument(
        "binance",
        NetworkType.TESTNET,
        "perp",
        Asset("BTC"),
        Asset("USDT"),
        "BTC/USDT:USDT",
    )
    return SimpleNamespace(
        network_type=NetworkType.TESTNET,
        ccxt_exchange=None,
        has_credentials=MagicMock(return_value=False),
        list_markets=AsyncMock(return_value=[instrument]),
        fetch_orderbook=AsyncMock(return_value={"bids": [[100, 1]], "asks": [[101, 1]]}),
        order_capabilities=MagicMock(return_value=SimpleNamespace(supports_client_order_id=True)),
        fetch_balance=AsyncMock(),
        submit_order=AsyncMock(),
        close=AsyncMock(),
    )


@pytest.mark.parametrize("credentials_present", [False, True])
def test_smoke_selects_testnet_credentials_and_remains_read_only(
    network_config,
    monkeypatch,
    credentials_present,
):
    if not credentials_present:
        (network_config / "secrets.testnet.yaml").unlink()
    exchange = _public_exchange()
    initialize = AsyncMock(return_value={"binance": exchange})
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", initialize)

    result = CliRunner().invoke(app, ["arb", "testnet-smoke", "--venues", "binance", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["status"] == "ok"
    configs, secrets = initialize.await_args.args
    assert configs["binance"]["default_network"] == "testnet"
    expected = {"apiKey": "synthetic-binance-testnet"} if credentials_present else {}
    assert secrets == {"binance": expected}
    assert initialize.await_args.kwargs == {"fail_fast": True}
    exchange.submit_order.assert_not_awaited()
    exchange.fetch_balance.assert_not_awaited()
    exchange.close.assert_awaited_once()


def test_canary_without_confirmation_does_not_initialize_exchanges(network_config, monkeypatch):
    initialize = AsyncMock()
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", initialize)

    result = CliRunner().invoke(app, ["arb", "testnet-canary"])

    assert result.exit_code != 0
    assert "TESTNET_CANARY" in result.output
    initialize.assert_not_awaited()


def test_canary_forces_testnet_even_for_disabled_mainnet_venue(network_config, monkeypatch):
    from src.arbitrage.canary import TestnetCanary
    from src.cli import bootstrap

    exchanges = {name: SimpleNamespace(close=AsyncMock()) for name in ("binance", "hyperliquid")}
    initialize = AsyncMock(return_value=exchanges)
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", initialize)
    store = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(bootstrap, "build_store", AsyncMock(return_value=store))
    run = AsyncMock(
        return_value=SimpleNamespace(
            status="CLOSED",
            direction="buy_a_sell_b",
            venue_a="binance",
            venue_b="hyperliquid",
            symbol_a="BTC/USDT:USDT",
            symbol_b="BTC/USDC:USDC",
            quantity_base=0.001,
            buy_price=100,
            sell_price=101,
            error=None,
            opening=None,
            closing=None,
        )
    )
    monkeypatch.setattr(TestnetCanary, "run", run)

    result = CliRunner().invoke(
        app,
        [
            "arb",
            "testnet-canary",
            "--venue-a",
            "binance",
            "--venue-b",
            "hyperliquid",
            "--confirm",
            "TESTNET_CANARY",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    configs, secrets = initialize.await_args.args
    assert all(config["default_network"] == "testnet" and config["enabled"] for config in configs.values())
    assert secrets == {
        "binance": {"apiKey": "synthetic-binance-testnet"},
        "hyperliquid": {
            "master_wallet_address": "synthetic-master-testnet",
            "api_wallet_address": "synthetic-api-wallet-testnet",
            "api_wallet_private_key": "synthetic-hyperliquid-testnet",
        },
    }
    assert initialize.await_args.kwargs == {"fail_fast": True}
    run.assert_awaited_once()
    store.close.assert_awaited_once()
    for exchange in exchanges.values():
        exchange.close.assert_awaited_once()
