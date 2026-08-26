"""Tests for trade-log export."""

from src.strategy.trade_log.export import to_csv, to_json


def test_to_csv_header_and_content():
    rows = [
        {
            "id": "1", "ts": "2026-08-26T00:00:00+00:00", "venue": None, "symbol": "BTC",
            "tag": "龙头", "side": "buy", "qty": 0.01, "price": 60000,
            "notional_usd": 600, "fee_usd": None, "pnl_usd": None,
            "strategy": None, "reason": None, "note": None,
        }
    ]
    out = to_csv(rows)
    assert out.splitlines()[0].startswith("id,ts,venue,symbol,tag,side,qty,price")
    assert "BTC" in out


def test_to_json_preserves_utf8():
    rows = [{"symbol": "BTC", "tag": "龙头", "reason": "测试"}]
    out = to_json(rows)
    assert "龙头" in out and "测试" in out
