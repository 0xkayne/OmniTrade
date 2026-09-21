from pathlib import Path

import pytest
import yaml

from src.arbitrage.config import ArbitrageConfig


def test_arbitrage_config_normalizes_repository_example() -> None:
    raw = yaml.safe_load(Path("config/arbitrage.yaml").read_text())
    config = ArbitrageConfig.from_mapping(raw)

    assert not config.enabled
    assert config.dry_run
    assert config.execution_mode == "ioc_ioc"
    assert config.hedged_execution_mode == "offline"
    assert not config.testnet_confirmed
    assert config.scanner.quantity_base == 0.001
    assert config.risk.max_unhedged_ms == 1500
    assert config.pairs[0].market_type == "perp"


def test_arbitrage_config_rejects_non_perpetual_pair() -> None:
    with pytest.raises(ValueError, match="perp pairs only"):
        ArbitrageConfig.from_mapping(
            {"arbitrage": {"pairs": [{"base": "BTC", "market_type": "spot", "venue_a": "a", "symbol_a": "BTC/USD", "venue_b": "b", "symbol_b": "BTC/USD"}]}}
        )


def test_arbitrage_config_rejects_unknown_hedged_execution_mode() -> None:
    with pytest.raises(ValueError, match="hedged_execution_mode"):
        ArbitrageConfig.from_mapping({"arbitrage": {"hedged_execution_mode": "production"}})
