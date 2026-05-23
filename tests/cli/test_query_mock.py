"""Tests for the query command with a mocked PersistenceStore."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from src.cli.main import app
from src.persistence.store import IntentRow, LegRow

runner = CliRunner()


def _make_mock_query_store(intent_row, leg_rows):
    """Create a mock store that returns the given intent and leg rows."""
    mock = AsyncMock()
    mock.get_intent = AsyncMock(return_value=intent_row)
    mock.get_legs_for_intent = AsyncMock(return_value=leg_rows)
    mock.close = AsyncMock()
    return mock


def test_query_found():
    """Query command displays intent + legs when found."""
    intent_row = IntentRow(
        intent_id="abc-123",
        status="ALL_FILLED",
        raw_intent_json='{"side":"buy","total_notional_usd":1000.0,"base":"BTC","product":"spot"}',
        created_at="2026-05-23T14:32:00",
        updated_at="2026-05-23T14:32:01",
    )
    leg_row = LegRow(
        leg_id="leg-1",
        intent_id="abc-123",
        venue="binance",
        instrument_venue_symbol="BTCUSDT",
        instrument_base="BTC",
        instrument_quote="USDT",
        instrument_market_type="spot",
        status="FILLED",
        order_id="binance-abc",
        filled_amount=0.00744,
        avg_price=67234.50,
        fee_usd=1.34,
    )

    mock_store = _make_mock_query_store(intent_row, [leg_row])

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["query", "abc-123"])

    assert result.exit_code == 0
    assert "abc-123" in result.stdout
    assert "ALL_FILLED" in result.stdout
    assert "BTCUSDT" in result.stdout
    assert "binance-abc" in result.stdout


def test_query_not_found():
    """Query command shows error when intent not found."""
    mock_store = _make_mock_query_store(None, [])

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["query", "nonexistent"])

    assert "not found" in result.stdout.lower()
    assert result.exit_code == 1


def test_query_no_legs():
    """Query command works even with no legs."""
    intent_row = IntentRow(
        intent_id="def-456",
        status="REJECTED",
        raw_intent_json='{"side":"sell","total_notional_usd":500.0,"base":"ETH","product":"perp"}',
        created_at="2026-05-23T14:28:00",
        updated_at="2026-05-23T14:28:00",
    )

    mock_store = _make_mock_query_store(intent_row, [])

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["query", "def-456"])

    assert result.exit_code == 0
    assert "def-456" in result.stdout
    assert "REJECTED" in result.stdout


def test_query_with_malformed_intent_json():
    """Query doesn't crash when raw_intent_json is unparseable."""
    intent_row = IntentRow(
        intent_id="bad-json",
        status="ALL_FILLED",
        raw_intent_json="not-valid-json",
        created_at="2026-05-23T14:00:00",
        updated_at="2026-05-23T14:00:01",
    )

    mock_store = _make_mock_query_store(intent_row, [])

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["query", "bad-json"])

    assert result.exit_code == 0
    assert "bad-json" in result.stdout
