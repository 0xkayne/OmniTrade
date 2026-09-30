"""CLI product routing and explicit side-effect boundaries, without venue I/O."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from src.cli.main import app
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType


@pytest.fixture
def cli_orchestrator(monkeypatch, tmp_path):
    from src.cli import bootstrap

    monkeypatch.chdir(tmp_path)
    orch = SimpleNamespace(
        submit=AsyncMock(return_value={"status": "DRY_RUN", "plan": {"legs": [], "aggregate": {}}}),
        refresh_status=AsyncMock(return_value={"intent_id": "sample-intent", "status": "ROLLED_BACK_FAILED"}),
        close=AsyncMock(),
    )
    monkeypatch.setattr(bootstrap, "build_orchestrator", AsyncMock(return_value=orch))
    return orch


def test_contract_overrides_preserve_split_and_existing_leg_fields(cli_orchestrator):
    result = CliRunner().invoke(
        app,
        [
            "order",
            "--base",
            "BTC",
            "--product",
            "perp",
            "--side",
            "buy",
            "--type",
            "market",
            "--total-notional-usd",
            "100",
            "--split",
            "binance=0.5:sell:perp:2,hyperliquid=0.5",
            "--contract-type",
            "linear",
            "--leg-contract-type",
            "binance=inverse",
            "--leg-settlement-asset",
            "binance=BTC",
            "--dry-run",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    intent = cli_orchestrator.submit.await_args.args[0]
    assert intent.split == {"binance": 0.5, "hyperliquid": 0.5}
    assert intent.contract_type == "linear"
    leg = intent.leg_configs["binance"]
    assert (leg.side, leg.leverage, leg.contract_type, leg.settlement_asset) == ("sell", 2, "inverse", "BTC")


def test_close_all_omits_notional_and_reaches_coordinator(cli_orchestrator):
    result = CliRunner().invoke(
        app,
        [
            "order",
            "--base",
            "BTC",
            "--product",
            "perp",
            "--side",
            "sell",
            "--type",
            "market",
            "--split",
            "binance=1",
            "--position-effect",
            "close",
            "--close-all",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    intent = cli_orchestrator.submit.await_args.args[0]
    assert intent.total_notional_usd is None
    assert intent.close_all and intent.position_effect == "close"
    assert "all position" in result.output


@pytest.mark.parametrize(
    "overrides",
    [
        ["--leg-contract-type", "missing=inverse"],
        ["--leg-contract-type", "binance=inverse", "--leg-contract-type", "binance=linear"],
        ["--leg-settlement-asset", "binance="],
    ],
)
def test_bad_leg_overrides_fail_before_bootstrap(cli_orchestrator, overrides):
    result = CliRunner().invoke(
        app,
        [
            "order",
            "--base",
            "BTC",
            "--product",
            "perp",
            "--side",
            "buy",
            "--type",
            "market",
            "--total-notional-usd",
            "100",
            "--split",
            "binance=1",
            "--dry-run",
            *overrides,
        ],
    )
    assert result.exit_code != 0
    cli_orchestrator.submit.assert_not_awaited()


def test_status_refresh_only_calls_read_only_refresh(cli_orchestrator):
    result = CliRunner().invoke(app, ["status", "sample-intent", "--refresh", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "ROLLED_BACK_FAILED"
    cli_orchestrator.refresh_status.assert_awaited_once_with("sample-intent")
    cli_orchestrator.submit.assert_not_awaited()
    cli_orchestrator.close.assert_awaited_once()


@pytest.mark.parametrize(
    "arguments",
    [
        ["--allow-orders"],
        ["--allow-orders", "--notional-cap", "-1"],
        ["--allow-orders", "--notional-cap", "nan"],
        ["--allow-orders", "--notional-cap", "20", "--network", "mainnet"],
    ],
)
def test_smoke_requires_explicit_demo_budget_before_initialization(monkeypatch, arguments):
    from src.exchange.factory import ExchangeFactory

    initialize = AsyncMock()
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", initialize)
    result = CliRunner().invoke(app, ["binance-smoke", *arguments])
    assert result.exit_code != 0
    initialize.assert_not_awaited()


def test_smoke_defaults_to_public_access_without_any_order(monkeypatch):
    from src.cli import main
    from src.exchange.factory import ExchangeFactory

    instrument = Instrument("binance", NetworkType.TESTNET, "spot", Asset("BTC"), Asset("USDT"), "BTC/USDT")
    exchange = SimpleNamespace(
        family_errors={},
        list_markets=AsyncMock(return_value=[instrument]),
        fetch_orderbook=AsyncMock(return_value={"bids": [[100, 1]], "asks": [[101, 1]]}),
        has_credentials=MagicMock(return_value=False),
        fetch_order_account=AsyncMock(),
        submit_order=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(main, "load_exchange_configuration", lambda **kwargs: ({"binance": {}}, {}))
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", AsyncMock(return_value={"binance": exchange}))
    result = CliRunner().invoke(app, ["binance-smoke", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "PUBLIC_OK" and not payload["orders_sent"]
    assert payload["account_check"] == "not_requested"
    exchange.fetch_order_account.assert_not_awaited()
    exchange.submit_order.assert_not_awaited()
    exchange.close.assert_awaited_once()


@pytest.mark.parametrize("status,orders_sent,exit_code", [("CLOSED", True, 0), ("SKIPPED", False, 4)])
def test_smoke_order_cycle_uses_coordinator_and_requires_completed_cycle(monkeypatch, status, orders_sent, exit_code):
    from src.cli import bootstrap, main
    from src.exchange.factory import ExchangeFactory
    from src.exchange.order import OrderAccountSnapshot

    instrument = Instrument("binance", NetworkType.TESTNET, "spot", Asset("BTC"), Asset("USDT"), "BTC/USDT")
    exchange = SimpleNamespace(
        family_errors={},
        list_markets=AsyncMock(return_value=[instrument]),
        fetch_orderbook=AsyncMock(return_value={"bids": [[100, 1]], "asks": [[101, 1]]}),
        has_credentials=MagicMock(return_value=True),
        fetch_order_account=AsyncMock(return_value=OrderAccountSnapshot("spot", {"USDT": 100})),
        submit_order=AsyncMock(),
        close=AsyncMock(),
    )
    orch = SimpleNamespace(
        smoke_roundtrip=AsyncMock(return_value={"status": status, "orders_sent": orders_sent}),
        close=AsyncMock(),
    )
    monkeypatch.setattr(main, "load_exchange_configuration", lambda **kwargs: ({"binance": {}}, {}))
    initialize = AsyncMock(return_value={"binance": exchange})
    monkeypatch.setattr(ExchangeFactory, "initialize_exchanges", initialize)
    build = AsyncMock(return_value=orch)
    monkeypatch.setattr(bootstrap, "build_orchestrator", build)
    result = CliRunner().invoke(app, ["binance-smoke", "--allow-orders", "--notional-cap", "25", "--json"])
    assert result.exit_code == exit_code, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == status and payload["orders_sent"] is orders_sent
    orch.smoke_roundtrip.assert_awaited_once_with(instrument, 25)
    exchange.submit_order.assert_not_awaited()
    orch.close.assert_awaited_once()
    assert build.call_args.kwargs["use_websocket"] is False
    assert initialize.call_args.args[0]["binance"]["market_families"] == ["spot"]


def test_json_retains_inverse_native_quantity_and_unknown_fee():
    from src.cli.main import _to_json_output
    from src.coordinator.intent import Intent

    intent = Intent("native", "BTC", ["USD"], "perp", "buy", "market", 100, {"binance": 1}, contract_type="inverse")
    result = _to_json_output(
        {
            "status": "ROLLED_BACK_FAILED",
            "legs": [
                {
                    "venue": "binance",
                    "market_type": "perp",
                    "contract_type": "inverse",
                    "settlement_asset": "BTC",
                    "quantity_unit": "contracts",
                    "filled_qty_native": "1",
                    "filled_amount": 0.002,
                    "avg_price": 50000,
                    "fee": None,
                    "has_complete_fees": False,
                    "remaining_requested_qty_native": "0",
                    "position_before_qty_native": "3",
                }
            ],
        },
        intent,
    )
    leg = result["legs"][0]
    assert leg["filled_qty_native"] == "1" and leg["qty_base"] == 0.002
    assert leg["contract_type"] == "inverse" and leg["settlement_asset"] == "BTC"
    assert leg["has_complete_fees"] is False
    assert leg["position_before_qty_native"] == "3"
    assert result["aggregate"]["total_fee_usd"] is None


@pytest.mark.parametrize("filled_native", [None, 0])
def test_execution_display_never_substitutes_planned_native_for_missing_or_zero_fill(cli_orchestrator, filled_native):
    cli_orchestrator.submit.return_value = {
        "status": "ROLLED_BACK_FAILED",
        "legs": [
            {
                "venue": "binance",
                "instrument": "BTC/USD:BTC",
                "market_type": "perp",
                "planned_qty_native": "1999",
                "filled_qty_native": filled_native,
                "filled_amount": 0,
                "contract_type": "inverse",
                "settlement_asset": "BTC",
            }
        ],
    }
    result = CliRunner().invoke(
        app,
        [
            "order",
            "--base",
            "BTC",
            "--product",
            "perp",
            "--side",
            "buy",
            "--type",
            "market",
            "--total-notional-usd",
            "100",
            "--split",
            "binance=1",
            "--contract-type",
            "inverse",
            "--yes",
        ],
    )
    assert result.exit_code == 4, result.output
    assert "1999" not in result.output
