"""Tests for Asset dataclass."""

import pytest

from src.market.asset import Asset


class TestAsset:
    """Asset equality, hashing, and frozen behavior."""

    def test_equality_same_symbols(self):
        a1 = Asset(symbol="BTC")
        a2 = Asset(symbol="BTC")
        assert a1 == a2

    def test_equality_different_symbols(self):
        a1 = Asset(symbol="BTC")
        a2 = Asset(symbol="ETH")
        assert a1 != a2

    def test_equality_different_kinds(self):
        a1 = Asset(symbol="USDT", kind="crypto")
        a2 = Asset(symbol="USDT", kind="fiat")
        assert a1 != a2

    def test_hash_same_symbols(self):
        a1 = Asset(symbol="BTC")
        a2 = Asset(symbol="BTC")
        assert hash(a1) == hash(a2)

    def test_hash_different_symbols(self):
        a1 = Asset(symbol="BTC")
        a2 = Asset(symbol="ETH")
        assert hash(a1) != hash(a2)

    def test_used_as_dict_key(self):
        d = {Asset("BTC"): "bitcoin", Asset("ETH"): "ethereum"}
        assert d[Asset("BTC")] == "bitcoin"
        assert d[Asset("ETH")] == "ethereum"

    def test_frozen_cannot_set_attribute(self):
        a = Asset(symbol="BTC")
        with pytest.raises(AttributeError):
            a.symbol = "ETH"

    def test_default_kind_is_crypto(self):
        a = Asset(symbol="BTC")
        assert a.kind == "crypto"

    def test_repr_includes_symbol(self):
        a = Asset(symbol="BTC")
        r = repr(a)
        assert "BTC" in r
