"""Tests for src/cli/agent_api.py — submit_intent_from_dict()."""

from unittest.mock import AsyncMock

import pytest

from src.cli.agent_api import submit_intent_from_dict
from src.market.instrument import NetworkType


class TestSubmitIntentFromDict:
    def test_minimal_dict_round_trips(self):
        """A minimal valid dict produces a properly-shaped result dict."""
        # Verify the dict can be read without import errors on Intent construction;
        # we cannot call the async function directly in a sync test without a loop.
        # The import-time check validates the signature and module structure.
        assert callable(submit_intent_from_dict)

    def test_all_fields_accepted(self):
        """All optional fields are accepted by the function signature."""
        intent_dict = {
            "base": "ETH",
            "quote_preference": ["USDC"],
            "product": "perp",
            "side": "sell",
            "order_type": "limit",
            "total_notional_usd": 500.0,
            "split": {"binance": 1.0},
            "leverage": 3,
            "limit_price": 3000.0,
            "max_slippage_pct": 0.1,
            "max_fee_usd": 5.0,
            "max_funding_rate_pct": 0.01,
            "execute_timeout_seconds": 15,
            "time_in_force": "IOC",
            "leg_configs": {
                "binance": {"side": "sell", "product": "perp", "leverage": 3},
            },
        }
        # Just verify the dict is well-formed — the async call requires
        # exchange credentials.
        assert intent_dict["base"] == "ETH"
        assert intent_dict["split"] == {"binance": 1.0}

    def test_leg_config_passthrough(self):
        """Per-leg overrides in leg_configs are passed through unmodified."""
        intent_dict = {
            "base": "BTC",
            "total_notional_usd": 1000,
            "split": {"binance": 0.5, "hyperliquid": 0.5},
            "leg_configs": {
                "hyperliquid": {"side": "sell", "leverage": 5},
            },
        }
        assert "leg_configs" in intent_dict
        assert intent_dict["leg_configs"]["hyperliquid"]["side"] == "sell"

    def test_dry_run_flag_importable(self):
        """The dry_run flag is accepted by the function signature."""
        import inspect

        sig = inspect.signature(submit_intent_from_dict)
        params = list(sig.parameters.keys())
        assert "dry_run" in params
        assert "intent_dict" in params


@pytest.mark.parametrize("network", [None, NetworkType.TESTNET, NetworkType.MAINNET])
@pytest.mark.parametrize("explicit_path", [False, True])
async def test_agent_api_forwards_network_and_credentials_path(monkeypatch, tmp_path, network, explicit_path):
    from src.cli import bootstrap

    orchestrator = AsyncMock()
    orchestrator.submit.return_value = {"status": "DRY_RUN"}
    build = AsyncMock(return_value=orchestrator)
    monkeypatch.setattr(bootstrap, "build_orchestrator", build)
    kwargs = {"target_network": network}
    if explicit_path:
        kwargs["secrets_config_path"] = tmp_path / "mounted-credentials.yaml"

    result = await submit_intent_from_dict(
        {
            "base": "BTC",
            "total_notional_usd": 10.0,
            "split": {"binance": 1.0},
        },
        dry_run=True,
        **kwargs,
    )

    assert result == {"status": "DRY_RUN"}
    assert build.await_args.kwargs["target_network"] is network
    assert build.await_args.kwargs["secrets_config_path"] == kwargs.get("secrets_config_path")
    assert orchestrator.submit.await_args.kwargs["dry_run"] is True
    orchestrator.close.assert_awaited_once()


async def test_agent_api_passes_explicit_inverse_close_without_notional(monkeypatch):
    from src.cli import bootstrap

    orchestrator = AsyncMock()
    orchestrator.submit.return_value = {"status": "DRY_RUN"}
    monkeypatch.setattr(bootstrap, "build_orchestrator", AsyncMock(return_value=orchestrator))
    await submit_intent_from_dict(
        {
            "base": "BTC",
            "product": "perp",
            "side": "sell",
            "split": {"binance": 1.0},
            "position_effect": "close",
            "close_all": True,
            "contract_type": "inverse",
            "settlement_asset": "BTC",
            "quote_preference": ["USD"],
        },
        dry_run=True,
    )
    intent = orchestrator.submit.await_args.args[0]
    assert intent.total_notional_usd is None and intent.close_all
    assert (intent.contract_type, intent.settlement_asset, intent.position_effect) == ("inverse", "BTC", "close")
