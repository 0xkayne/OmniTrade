"""Tests for the order command with a mocked Orchestrator."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from src.cli.main import _STATUS_TO_EXIT, _to_json_output, app
from src.coordinator.intent import Intent

runner = CliRunner()


def _make_mock_orchestrator(result: dict):
    """Create a mock orchestrator that returns the given result dict."""
    mock = MagicMock()
    mock.submit = AsyncMock(return_value=result)
    mock._store = MagicMock()
    mock._store.close = AsyncMock()
    return mock


def _mock_build_orchestrator(orch):
    """Patch build_orchestrator to return orch."""
    return patch("src.cli.main.build_orchestrator", new=AsyncMock(return_value=orch))


# ---------------------------------------------------------------------------
# Happy path: ALL_FILLED
# ---------------------------------------------------------------------------

@pytest.fixture
def all_filled_result():
    return {
        "status": "ALL_FILLED",
        "intent_id": "test-id-123",
        "legs": [
            {
                "leg_id": "leg-1",
                "venue": "binance",
                "status": "FILLED",
                "order_id": "binance-abc",
                "filled_amount": 0.00744,
                "avg_price": 67234.50,
                "fee": 1.34,
            },
            {
                "leg_id": "leg-2",
                "venue": "hyperliquid",
                "status": "FILLED",
                "order_id": "hl-def",
                "filled_amount": 0.00742,
                "avg_price": 67242.50,
                "fee": 1.35,
            },
        ],
        "execution_time_s": 0.873,
    }


def test_order_all_filled_json_output(all_filled_result):
    """JSON output for ALL_FILLED has correct shape."""
    intent = Intent(
        intent_id="test-id-123",
        base="BTC",
        quote_preference=["USDT", "USDC"],
        product="spot",
        side="buy",
        order_type="market",
        total_notional_usd=1000.0,
        split={"binance": 0.5, "hyperliquid": 0.5},
    )

    output = _to_json_output(all_filled_result, intent)

    assert output["intent_id"] == "test-id-123"
    assert output["status"] == "ALL_FILLED"
    assert output["error"] is None
    assert len(output["legs"]) == 2

    leg1 = output["legs"][0]
    assert leg1["venue"] == "binance"
    # notional computed from filled_amount * avg_price when not explicitly in result
    assert leg1["notional_usd"] == pytest.approx(0.00744 * 67234.50)
    assert leg1["filled_amount"] == 0.00744
    assert leg1["avg_price"] == 67234.50
    assert leg1["fee_usd"] == 1.34

    agg = output["aggregate"]
    assert agg["total_notional"] == 1000.0
    assert agg["total_fee_usd"] == 2.69
    assert agg["duration_ms"] == 873


def test_order_all_filled_exit_code(all_filled_result):
    """ALL_FILLED maps to exit code 0."""
    assert _STATUS_TO_EXIT["ALL_FILLED"] == 0


# ---------------------------------------------------------------------------
# DRY_RUN
# ---------------------------------------------------------------------------

@pytest.fixture
def dry_run_result():
    return {
        "status": "DRY_RUN",
        "intent_id": "test-dry-123",
        "plan": {
            "legs": [
                {
                    "venue": "binance",
                    "instrument": "BTCUSDT",
                    "quote_matched": "USDT",
                    "planned_notional_usd": 500.0,
                    "planned_qty_base": 0.00744,
                    "estimated_avg_price": 67234.50,
                    "estimated_slippage_pct": 0.08,
                    "estimated_fee_usd": 1.34,
                },
            ],
            "rejected_venues": [],
            "aggregate": {
                "estimated_avg_price": 67234.50,
                "estimated_fee_usd": 1.34,
            },
            "is_acceptable": True,
        },
        "legs": [],
    }


def test_dry_run_json_output(dry_run_result):
    """JSON output for DRY_RUN uses plan.legs."""
    intent = Intent(
        intent_id="test-dry-123",
        base="BTC",
        quote_preference=["USDT"],
        product="spot",
        side="buy",
        order_type="market",
        total_notional_usd=500.0,
        split={"binance": 1.0},
    )

    output = _to_json_output(dry_run_result, intent)

    assert output["status"] == "DRY_RUN"
    assert len(output["legs"]) == 1
    leg = output["legs"][0]
    assert leg["venue"] == "binance"
    assert leg["instrument"] == "BTCUSDT"
    assert leg["notional_usd"] == 500.0
    assert leg["qty_base"] == 0.00744
    assert leg["slippage_pct"] == 0.08
    assert leg["fee_usd"] == 1.34
    assert leg["order_id"] is None  # not sent yet

    assert output["aggregate"]["total_fee_usd"] == 1.34


def test_dry_run_exit_code():
    """DRY_RUN maps to exit code 0."""
    assert _STATUS_TO_EXIT["DRY_RUN"] == 0


# ---------------------------------------------------------------------------
# REJECTED
# ---------------------------------------------------------------------------

@pytest.fixture
def rejected_result():
    return {
        "status": "REJECTED",
        "intent_id": "test-rej-123",
        "reason": "Plan not acceptable: no instruments match",
        "legs": [],
    }


def test_rejected_json_output(rejected_result):
    """JSON output for REJECTED includes error."""
    intent = Intent(
        intent_id="test-rej-123",
        base="SOL",
        quote_preference=["USDT"],
        product="spot",
        side="buy",
        order_type="market",
        total_notional_usd=100.0,
        split={"binance": 1.0},
    )

    output = _to_json_output(rejected_result, intent)
    assert output["status"] == "REJECTED"
    assert output["legs"] == []
    assert "Plan not acceptable" in output["error"]
    assert output["aggregate"]["duration_ms"] is None


def test_rejected_exit_code():
    """REJECTED maps to exit code 2."""
    assert _STATUS_TO_EXIT["REJECTED"] == 2


# ---------------------------------------------------------------------------
# ROLLED_BACK / ROLLED_BACK_FAILED (NEEDS_MANUAL)
# ---------------------------------------------------------------------------

@pytest.fixture
def rolled_back_result():
    return {
        "status": "ROLLED_BACK",
        "intent_id": "test-rb-123",
        "legs": [
            {
                "leg_id": "leg-1",
                "venue": "hyperliquid",
                "status": "FILLED",
                "order_id": "hl-001",
                "filled_amount": 0.00742,
                "avg_price": 67242.50,
                "fee": 1.35,
            },
        ],
        "reconciliation": {
            "status": "ROLLED_BACK",
            "legs": [
                {
                    "leg_id": "leg-1",
                    "reverse_side": "sell",
                    "compensation_status": "COMPENSATED",
                    "compensation_order_id": "hl-comp-001",
                }
            ],
            "residual_exposure_usd": 0.0,
        },
    }


@pytest.fixture
def needs_manual_result():
    return {
        "status": "ROLLED_BACK_FAILED",
        "intent_id": "test-nm-123",
        "legs": [
            {
                "leg_id": "leg-1",
                "venue": "hyperliquid",
                "status": "FILLED",
                "order_id": "hl-001",
                "filled_amount": 0.00742,
                "avg_price": 67242.50,
                "fee": 1.35,
            },
        ],
        "reconciliation": {
            "status": "ROLLED_BACK_FAILED",
            "legs": [],
            "residual_exposure_usd": 499.35,
        },
    }


def test_rolled_back_json(rolled_back_result):
    """ROLLED_BACK JSON output is correct."""
    intent = Intent(
        intent_id="test-rb-123",
        base="BTC",
        quote_preference=["USDT"],
        product="spot",
        side="buy",
        order_type="market",
        total_notional_usd=500.0,
        split={"hyperliquid": 1.0},
    )

    output = _to_json_output(rolled_back_result, intent)
    assert output["status"] == "ROLLED_BACK"
    assert len(output["legs"]) == 1


def test_rolled_back_exit_code():
    """ROLLED_BACK maps to exit code 3."""
    assert _STATUS_TO_EXIT["ROLLED_BACK"] == 3


def test_needs_manual_exit_code():
    """ROLLED_BACK_FAILED maps to exit code 4."""
    assert _STATUS_TO_EXIT["ROLLED_BACK_FAILED"] == 4


# ---------------------------------------------------------------------------
# Full CLI run with mock
# ---------------------------------------------------------------------------

def test_order_command_with_mock_success(all_filled_result):
    """Invoke the full order command with a mock orchestrator returning ALL_FILLED."""
    mock_orch = _make_mock_orchestrator(all_filled_result)

    with _mock_build_orchestrator(mock_orch):
        result = runner.invoke(app, [
            "order",
            "--base", "BTC",
            "--quote-preference", "USDT",
            "--product", "spot",
            "--side", "buy",
            "--type", "market",
            "--total-notional-usd", "1000",
            "--split", "binance=0.5,hyperliquid=0.5",
            "--yes",
        ])

    assert result.exit_code == 0
    assert "ALL_FILLED" in result.stdout


def test_order_command_json_flag(all_filled_result):
    """--json flag produces parseable JSON output."""
    mock_orch = _make_mock_orchestrator(all_filled_result)

    with _mock_build_orchestrator(mock_orch):
        result = runner.invoke(app, [
            "order",
            "--base", "BTC",
            "--quote-preference", "USDT",
            "--product", "spot",
            "--side", "buy",
            "--type", "market",
            "--total-notional-usd", "1000",
            "--split", "binance=0.5,hyperliquid=0.5",
            "--yes",
            "--json",
        ])

    # Parse stdout as JSON
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "ALL_FILLED"
    assert parsed["intent_id"] is not None
    assert "legs" in parsed
    assert "aggregate" in parsed
    assert "error" in parsed


def test_order_command_rejected_exit_code(rejected_result):
    """REJECTED status yields exit code 2."""
    mock_orch = _make_mock_orchestrator(rejected_result)

    with _mock_build_orchestrator(mock_orch):
        result = runner.invoke(app, [
            "order",
            "--base", "SOL",
            "--quote-preference", "USDT",
            "--product", "spot",
            "--side", "buy",
            "--type", "market",
            "--total-notional-usd", "100",
            "--split", "binance=1.0",
            "--yes",
        ])

    assert result.exit_code == 2
    assert "REJECTED" in result.stdout


def test_order_command_rolled_back_exit_code(rolled_back_result):
    """ROLLED_BACK status yields exit code 3."""
    mock_orch = _make_mock_orchestrator(rolled_back_result)

    with _mock_build_orchestrator(mock_orch):
        result = runner.invoke(app, [
            "order",
            "--base", "BTC",
            "--quote-preference", "USDT",
            "--product", "spot",
            "--side", "buy",
            "--type", "market",
            "--total-notional-usd", "500",
            "--split", "hyperliquid=1.0",
            "--yes",
        ])

    assert result.exit_code == 3


def test_order_command_needs_manual_exit_code(needs_manual_result):
    """ROLLED_BACK_FAILED status yields exit code 4."""
    mock_orch = _make_mock_orchestrator(needs_manual_result)

    with _mock_build_orchestrator(mock_orch):
        result = runner.invoke(app, [
            "order",
            "--base", "BTC",
            "--quote-preference", "USDT",
            "--product", "spot",
            "--side", "buy",
            "--type", "market",
            "--total-notional-usd", "500",
            "--split", "hyperliquid=1.0",
            "--yes",
        ])

    assert result.exit_code == 4


def test_order_command_dry_run(dry_run_result):
    """--dry-run returns plan info without exit code rejection."""
    mock_orch = _make_mock_orchestrator(dry_run_result)

    with _mock_build_orchestrator(mock_orch):
        result = runner.invoke(app, [
            "order",
            "--base", "BTC",
            "--quote-preference", "USDT",
            "--product", "spot",
            "--side", "buy",
            "--type", "market",
            "--total-notional-usd", "500",
            "--split", "binance=1.0",
            "--dry-run",
        ])

    assert result.exit_code == 0
    assert "DRY_RUN" in result.stdout


def test_order_command_quit_on_confirm_no(all_filled_result):
    """Answering 'n' to the confirmation prompt exits before submitting."""
    mock_orch = _make_mock_orchestrator(all_filled_result)

    with _mock_build_orchestrator(mock_orch):
        result = runner.invoke(app, [
            "order",
            "--base", "BTC",
            "--quote-preference", "USDT",
            "--product", "spot",
            "--side", "buy",
            "--type", "market",
            "--total-notional-usd", "500",
            "--split", "binance=1.0",
        ], input="n\n")

    assert result.exit_code == 0
    assert "cancelled" in result.stdout.lower()
    mock_orch.submit.assert_not_called()


def test_order_command_invalid_split():
    """Invalid --split format yields error exit code 1."""
    result = runner.invoke(app, [
        "order",
        "--base", "BTC",
        "--product", "spot",
        "--side", "buy",
        "--type", "market",
        "--total-notional-usd", "500",
        "--split", "not-valid",
        "--yes",
    ])

    assert result.exit_code == 1
    assert "Error" in result.stdout or "error" in result.stdout.lower()


def test_to_json_output_dry_run_null_aggregate():
    """DRY_RUN result with no aggregate prices populates None."""
    result = {
        "status": "DRY_RUN",
        "intent_id": "t1",
        "plan": {
            "legs": [],
            "rejected_venues": [],
            "aggregate": {"estimated_avg_price": None, "estimated_fee_usd": 0.0},
            "is_acceptable": False,
        },
        "legs": [],
    }
    intent = Intent(
        intent_id="t1", base="BTC", quote_preference=["USDT"],
        product="spot", side="buy", order_type="market",
        total_notional_usd=500.0, split={"binance": 1.0},
    )
    output = _to_json_output(result, intent)
    assert output["aggregate"]["total_fee_usd"] == 0.0
    assert output["aggregate"]["weighted_avg_price"] is None
