"""Tests for CCXTExchange helper logic (no network)."""

from src.exchanges.ccxt_exchange import _is_placeholder_value


def test_placeholder_values_detected():
    assert _is_placeholder_value("your_binance_api_key") is True
    assert _is_placeholder_value("your_private_key") is True
    assert _is_placeholder_value("") is True
    assert _is_placeholder_value("xxxxx") is True
    assert _is_placeholder_value("0000") is True  # all-same-character sentinel


def test_real_credentials_not_marked_placeholder():
    # Real-looking keys / addresses must NOT be dropped.
    assert _is_placeholder_value("0x702b0677a6c4356fdfbef6b372f1e7a478448cbb") is False
    assert _is_placeholder_value("ABC123def456xyz789") is False
