"""Tests for watchlist.yaml parsing."""

import pytest

from src.strategy.price_watch.watchlist import load_watchlist


def test_load_watchlist(tmp_path):
    p = tmp_path / "watchlist.yaml"
    p.write_text("watchlist:\n  - symbol: BTC\n    tag: 龙头\n  - symbol: sol\n    tag: 公链\n")
    items = load_watchlist(p)
    assert len(items) == 2
    assert items[0].symbol == "BTC"
    assert items[0].tag == "龙头"
    assert items[0].market_type == "perp"
    assert items[0].quote_preference == ["USDT", "USDC", "USD"]
    assert items[1].symbol == "SOL"  # upper-cased
    assert items[1].tag == "公链"


def test_per_item_overrides(tmp_path):
    p = tmp_path / "watchlist.yaml"
    p.write_text("watchlist:\n  - symbol: eth\n    tag: DeFi\n    market_type: spot\n    quote_preference: [USDT, USDC]\n")
    item = load_watchlist(p)[0]
    assert item.symbol == "ETH"
    assert item.market_type == "spot"
    assert item.quote_preference == ["USDT", "USDC"]


def test_missing_tag_raises(tmp_path):
    p = tmp_path / "watchlist.yaml"
    p.write_text("watchlist:\n  - symbol: BTC\n")
    with pytest.raises(ValueError):
        load_watchlist(p)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_watchlist(tmp_path / "nope.yaml")
