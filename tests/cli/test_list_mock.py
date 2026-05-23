"""Tests for the list-intents command with a mocked PersistenceStore."""

from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from src.cli.main import app
from src.persistence.store import IntentRow

runner = CliRunner()


def _make_mock_list_store(rows):
    """Create a mock store whose list_intents returns the given rows."""
    mock = AsyncMock()
    mock.list_intents = AsyncMock(return_value=rows)
    mock.close = AsyncMock()
    return mock


def test_list_empty():
    """List with no intents shows empty message."""
    mock_store = _make_mock_list_store([])

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "No intents" in result.stdout


def test_list_with_data():
    """List displays a table with intent data."""
    rows = [
        IntentRow(
            intent_id="intent-abc-123",
            status="ALL_FILLED",
            raw_intent_json='{"side":"buy","total_notional_usd":1000.0,"base":"BTC","product":"spot"}',
            created_at="2026-05-23T14:32:00",
            updated_at="2026-05-23T14:32:01",
        ),
        IntentRow(
            intent_id="intent-def-456",
            status="ROLLED_BACK",
            raw_intent_json='{"side":"sell","total_notional_usd":500.0,"base":"ETH","product":"perp"}',
            created_at="2026-05-23T14:28:00",
            updated_at="2026-05-23T14:29:00",
        ),
    ]

    mock_store = _make_mock_list_store(rows)

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "ALL_FILLED" in result.stdout
    assert "ROLLED_BACK" in result.stdout
    assert "BTC" in result.stdout
    assert "ETH" in result.stdout
    assert "buy" in result.stdout.lower()
    assert "sell" in result.stdout.lower()


def test_list_filtered_by_status():
    """List --status filters results."""
    rows = [
        IntentRow(
            intent_id="intent-1",
            status="NEEDS_MANUAL",
            raw_intent_json='{"side":"buy","total_notional_usd":200.0,"base":"SOL","product":"spot"}',
            created_at="2026-05-23T10:00:00",
            updated_at="2026-05-23T10:01:00",
        ),
    ]

    mock_store = _make_mock_list_store(rows)

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["list", "--status", "NEEDS_MANUAL"])

    assert result.exit_code == 0
    mock_store.list_intents.assert_called_once_with(status="NEEDS_MANUAL", limit=50)


def test_list_with_malformed_json():
    """List does not crash when some rows have unparseable JSON."""
    rows = [
        IntentRow(
            intent_id="bad-row",
            status="ALL_FILLED",
            raw_intent_json="garbage",
            created_at="2026-05-23T12:00:00",
            updated_at="2026-05-23T12:00:01",
        ),
    ]

    mock_store = _make_mock_list_store(rows)

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "bad-row" in result.stdout


def test_list_shows_multiple_intents():
    """Listing 3 intents displays all in the table."""
    rows = []
    for i in range(3):
        rows.append(
            IntentRow(
                intent_id=f"intent-{i}",
                status="ALL_FILLED",
                raw_intent_json='{"side":"buy","total_notional_usd":100.0,"base":"BTC","product":"spot"}',
                created_at="2026-05-23T14:00:00",
                updated_at="2026-05-23T14:00:01",
            )
        )

    mock_store = _make_mock_list_store(rows)

    with patch("src.cli.bootstrap.build_store", new=AsyncMock(return_value=mock_store)):
        result = runner.invoke(app, ["list"])

    assert result.exit_code == 0
    assert "intent-0" in result.stdout
    assert "intent-1" in result.stdout
    assert "intent-2" in result.stdout
