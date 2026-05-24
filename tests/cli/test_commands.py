"""Integration tests for CLI commands using typer CliRunner + mocks."""

import json

import pytest
from typer.testing import CliRunner

from src.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# order command
# ---------------------------------------------------------------------------

ORDER_BASE_ARGS = [
    "order",
    "--base", "BTC",
    "--quote-preference", "USDT,USDC",
    "--product", "spot",
    "--side", "buy",
    "--type", "market",
    "--total-notional-usd", "1000",
    "--split", "binance=0.5,hyperliquid=0.5",
]


def _mock_orchestrator(return_value):
    """Create an async mock orchestrator that returns `return_value` on submit()."""

    class MockStore:
        async def close(self):
            pass

    class MockOrch:
        def __init__(self):
            self._store = MockStore()

        async def submit(self, intent, dry_run=False):
            return return_value

    async def _build(*args, **kwargs):
        return MockOrch()

    return _build


class TestOrderCommand:
    def test_dry_run_json_output(self):
        from unittest.mock import patch

        mock_build = _mock_orchestrator({
            "status": "DRY_RUN",
            "intent_id": "intent-001",
            "plan": {
                "legs": [
                    {
                        "venue": "binance",
                        "instrument": "BTC/USDT",
                        "quote_matched": "USDT",
                        "planned_notional_usd": 500.0,
                        "planned_qty_base": 0.00744,
                        "estimated_fill_price": 67234.50,
                        "estimated_slippage_pct": 0.08,
                        "estimated_fee_usd": 1.34,
                    },
                ],
                "rejected_venues": [],
                "aggregate": {"estimated_avg_price": 67234.50, "estimated_total_fee_usd": 1.34},
            },
        })

        with patch("src.cli.bootstrap.build_orchestrator", mock_build):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--dry-run", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["status"] == "DRY_RUN"
        assert "legs" in data

    def test_dry_run_no_orders_sent(self):
        from unittest.mock import patch

        mock_build = _mock_orchestrator({
            "status": "DRY_RUN",
            "intent_id": "intent-001",
            "plan": {"legs": [], "rejected_venues": [], "aggregate": {}},
        })

        with patch("src.cli.bootstrap.build_orchestrator", mock_build):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--dry-run"])

        assert result.exit_code == 0

    def test_all_filled_json_output(self):
        from unittest.mock import patch

        mock_build = _mock_orchestrator({
            "status": "ALL_FILLED",
            "intent_id": "intent-001",
            "legs": [
                {
                    "leg_id": "leg-1",
                    "venue": "binance",
                    "status": "FILLED",
                    "order_id": "mock-1",
                    "filled_amount": 0.00744,
                    "avg_price": 67234.50,
                    "fee": 1.34,
                },
            ],
            "execution_time_s": 0.873,
        })

        with patch("src.cli.bootstrap.build_orchestrator", mock_build):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--yes", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["status"] == "ALL_FILLED"

    def test_rejected_exit_code(self):
        from unittest.mock import patch

        mock_build = _mock_orchestrator({
            "status": "REJECTED",
            "intent_id": "intent-002",
            "reason": "Plan not acceptable: max slippage exceeded",
            "rejected_venues": [],
            "legs": [],
        })

        with patch("src.cli.bootstrap.build_orchestrator", mock_build):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--yes"])

        assert result.exit_code == 2

    def test_rolled_back_exit_code(self):
        from unittest.mock import patch

        mock_build = _mock_orchestrator({
            "status": "ROLLED_BACK",
            "intent_id": "intent-003",
            "legs": [
                {
                    "leg_id": "leg-1", "venue": "binance", "status": "FILLED",
                    "order_id": "mock-1", "filled_amount": 0.01,
                    "avg_price": 50000.0, "fee": 1.0,
                },
            ],
            "reconciliation": {
                "status": "ROLLED_BACK",
                "legs": [{"leg_id": "leg-1", "compensation_status": "COMPENSATED"}],
                "residual_exposure_usd": 0.0,
            },
        })

        with patch("src.cli.bootstrap.build_orchestrator", mock_build):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--yes"])

        assert result.exit_code == 3

    def test_needs_manual_exit_code(self):
        from unittest.mock import patch

        mock_build = _mock_orchestrator({
            "status": "ROLLED_BACK_FAILED",
            "intent_id": "intent-004",
            "legs": [],
            "reconciliation": {
                "status": "ROLLED_BACK_FAILED",
                "legs": [],
                "residual_exposure_usd": 500.0,
            },
        })

        with patch("src.cli.bootstrap.build_orchestrator", mock_build):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--yes"])

        assert result.exit_code == 4

    def test_invalid_split_format_exits_with_error(self):
        from unittest.mock import patch

        with patch("src.cli.bootstrap.build_orchestrator", _mock_orchestrator({})):
            result = runner.invoke(
                app,
                ORDER_BASE_ARGS[:-1] + ["--split", "invalid_format"],
            )

        # Typer catches BadParameter and returns exit code 2
        assert result.exit_code == 2

    def test_invalid_intent_valueerror_exits_with_error(self):
        from unittest.mock import patch

        # split sums to 0.7, not 1.0 — triggers Intent.__post_init__ ValueError.
        # Typer catches it before our except block, returns exit code 2.
        with patch("src.cli.bootstrap.build_orchestrator", _mock_orchestrator({})):
            result = runner.invoke(
                app,
                ORDER_BASE_ARGS[:-1] + ["--split", "binance=0.3,hyperliquid=0.4"],
            )

        assert result.exit_code == 2

    def test_config_not_found_exits_with_error(self):
        from unittest.mock import patch

        async def _raise(*args, **kwargs):
            raise FileNotFoundError("config/exchanges.yaml")

        with patch("src.cli.bootstrap.build_orchestrator", _raise):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--yes"])

        assert result.exit_code == 1
        assert "Config error" in result.stdout

    def test_yes_flag_skips_confirm(self):
        from unittest.mock import patch

        mock_build = _mock_orchestrator({
            "status": "ALL_FILLED",
            "intent_id": "intent-001",
            "legs": [],
            "execution_time_s": 0.5,
        })

        with patch("src.cli.bootstrap.build_orchestrator", mock_build):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--yes"])

        # Should complete without prompting — exit code 0
        assert result.exit_code == 0

    def test_rich_output_not_json(self):
        from unittest.mock import patch

        mock_build = _mock_orchestrator({
            "status": "ALL_FILLED",
            "intent_id": "intent-001",
            "legs": [
                {
                    "leg_id": "leg-1", "venue": "binance", "status": "FILLED",
                    "order_id": "mock-1", "filled_amount": 0.00744,
                    "avg_price": 67234.50, "fee": 1.34,
                },
            ],
            "execution_time_s": 0.5,
        })

        with patch("src.cli.bootstrap.build_orchestrator", mock_build):
            result = runner.invoke(app, ORDER_BASE_ARGS + ["--yes"])

        assert result.exit_code == 0
        # Non-JSON mode renders rich text with status
        assert "ALL_FILLED" in result.stdout


# ---------------------------------------------------------------------------
# query command
# ---------------------------------------------------------------------------


class TestQueryCommand:
    def test_query_found(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.get_intent = AsyncMock(return_value=MagicMock(
                intent_id="intent-001",
                status="ALL_FILLED",
                raw_intent_json='{"base":"BTC","side":"buy","product":"spot","total_notional_usd":1000.0}',
                created_at="2024-01-01T00:00:00+00:00",
                updated_at="2024-01-01T00:00:00+00:00",
            ))
            store.get_legs_for_intent = AsyncMock(return_value=[])
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["query", "intent-001"])

        assert result.exit_code == 0
        assert "ALL_FILLED" in result.stdout

    def test_query_not_found(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.get_intent = AsyncMock(return_value=None)
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["query", "intent-nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout


# ---------------------------------------------------------------------------
# list_intents command
# ---------------------------------------------------------------------------


class TestListIntentsCommand:
    def test_list_all(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.list_intents = AsyncMock(return_value=[
                MagicMock(
                    intent_id="intent-001",
                    status="ALL_FILLED",
                    raw_intent_json='{"base":"BTC","side":"buy","product":"spot","total_notional_usd":1000.0}',
                    created_at="2024-01-01T00:00:00+00:00",
                ),
            ])
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["list-intents"])

        assert result.exit_code == 0
        assert "intent-001" in result.stdout

    def test_list_filtered_by_status(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.list_intents = AsyncMock(return_value=[])
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["list-intents", "--status", "ROLLED_BACK_FAILED"])

        assert result.exit_code == 0

    def test_list_empty(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.list_intents = AsyncMock(return_value=[])
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["list-intents"])

        assert result.exit_code == 0
        assert "No intents found" in result.stdout


# ---------------------------------------------------------------------------
# cancel command
# ---------------------------------------------------------------------------


class TestCancelCommand:
    def test_cancel_pending_intent(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.get_intent = AsyncMock(return_value=MagicMock(
                intent_id="intent-001",
                status="PENDING",
                raw_intent_json="{}",
                created_at="2024-01-01T00:00:00+00:00",
            ))
            store.update_intent_status = AsyncMock()
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["cancel", "intent-001"])

        assert result.exit_code == 0
        assert "cancelled" in result.stdout

    def test_cancel_terminal_intent(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.get_intent = AsyncMock(return_value=MagicMock(
                intent_id="intent-001",
                status="ALL_FILLED",
                raw_intent_json="{}",
                created_at="2024-01-01T00:00:00+00:00",
            ))
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["cancel", "intent-001"])

        assert result.exit_code == 0
        assert "Cannot cancel" in result.stdout

    def test_cancel_not_found(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.get_intent = AsyncMock(return_value=None)
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["cancel", "intent-nonexistent"])

        assert result.exit_code == 0
        assert "not found" in result.stdout


# ---------------------------------------------------------------------------
# recover command
# ---------------------------------------------------------------------------


class TestRecoverCommand:
    def test_recover_no_manual_intents(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.list_intents = AsyncMock(return_value=[])
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["recover"])

        assert result.exit_code == 0
        assert "No intents need manual recovery" in result.stdout

    def test_recover_with_manual_intents(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _build(*args, **kwargs):
            store = MagicMock()
            store.list_intents = AsyncMock(return_value=[
                MagicMock(
                    intent_id="intent-bad-001",
                    status="ROLLED_BACK_FAILED",
                    raw_intent_json='{"base":"BTC","side":"buy","product":"spot","total_notional_usd":1000.0}',
                    created_at="2024-01-01T00:00:00+00:00",
                ),
            ])
            store.close = AsyncMock()
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["recover"])

        assert result.exit_code == 0
        assert "intent(s) need manual recovery" in result.stdout
        assert "ROLLED_BACK_FAILED" in result.stdout
        assert "onefill ack" in result.stdout


# ---------------------------------------------------------------------------
# ack command
# ---------------------------------------------------------------------------


class TestAckCommand:
    def test_ack_rolled_back_failed_intent(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        store = MagicMock()
        store.get_intent = AsyncMock(return_value=MagicMock(
            intent_id="intent-001",
            status="ROLLED_BACK_FAILED",
        ))
        store.update_intent_status = AsyncMock()
        store.close = AsyncMock()

        async def _build(*args, **kwargs):
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["ack", "intent-001"])

        assert result.exit_code == 0
        assert "acknowledged" in result.stdout
        store.update_intent_status.assert_awaited_once_with("intent-001", "RESOLVED_MANUAL")

    def test_ack_wrong_status_rejected(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        store = MagicMock()
        store.get_intent = AsyncMock(return_value=MagicMock(
            intent_id="intent-001",
            status="ALL_FILLED",
        ))
        store.update_intent_status = AsyncMock()
        store.close = AsyncMock()

        async def _build(*args, **kwargs):
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["ack", "intent-001"])

        assert result.exit_code == 1
        assert "only applies to ROLLED_BACK_FAILED" in result.stdout
        store.update_intent_status.assert_not_awaited()

    def test_ack_not_found(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        store = MagicMock()
        store.get_intent = AsyncMock(return_value=None)
        store.close = AsyncMock()

        async def _build(*args, **kwargs):
            return store

        with patch("src.cli.bootstrap.build_store", _build):
            result = runner.invoke(app, ["ack", "intent-nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.stdout


# ---------------------------------------------------------------------------
# venues command
# ---------------------------------------------------------------------------


class TestVenuesCommand:
    def test_venues_with_config(self, tmp_path):
        from unittest.mock import patch

        import yaml

        config_path = tmp_path / "exchanges.yaml"
        config_data = {
            "exchanges": {
                "binance": {
                    "type": "ccxt",
                    "enabled": True,
                    "default_network": "testnet",
                    "networks": {
                        "testnet": {
                            "rest_base_url": "https://demo-api.binance.com",
                            "websocket_url": "wss://stream.binance.com:9443/ws",
                        }
                    },
                    "fees": {"taker": 0.0004, "maker": 0.0002},
                },
                "hyperliquid": {
                    "type": "ccxt",
                    "enabled": True,
                    "default_network": "testnet",
                    "networks": {
                        "testnet": {
                            "rest_base_url": "https://api.hyperliquid-testnet.xyz",
                            "websocket_url": "wss://api.hyperliquid-testnet.xyz/ws",
                        }
                    },
                    "fees": {"taker": 0.00015, "maker": 0.00045},
                },
            }
        }
        config_path.write_text(yaml.dump(config_data))

        with patch("src.cli.main.Path", lambda p: tmp_path / "exchanges.yaml"):
            result = runner.invoke(app, ["venues"])

        assert result.exit_code == 0
        assert "binance" in result.stdout
        assert "hyperliquid" in result.stdout

    def test_venues_config_missing(self, tmp_path):
        from unittest.mock import patch

        with patch("src.cli.main.Path", lambda p: tmp_path / "nonexistent.yaml"):
            result = runner.invoke(app, ["venues"])

        assert result.exit_code == 1
        assert "not found" in result.stdout


# ---------------------------------------------------------------------------
# Top-level help
# ---------------------------------------------------------------------------


class TestHelp:
    def test_help_shows_commands(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "order" in result.stdout
        assert "query" in result.stdout
        assert "list-intents" in result.stdout
        assert "cancel" in result.stdout
        assert "recover" in result.stdout
        assert "venues" in result.stdout
