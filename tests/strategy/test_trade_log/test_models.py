"""Tests for TradeRecord model."""

from src.strategy.trade_log.models import TradeRecord


def test_notional_usd_and_auto_fields():
    rec = TradeRecord(symbol="BTC", side="buy", qty=0.5, price=60000, tag="龙头")
    assert rec.notional_usd() == 30000.0
    assert rec.id and rec.ts  # auto-generated


def test_to_dict_round_trip():
    rec = TradeRecord(symbol="sol", side="sell", qty=1.0, price=10.0, strategy="AI Agent", reason="test")
    d = rec.to_dict()
    assert d["symbol"] == "sol"
    assert d["notional_usd"] == 10.0
    rec2 = TradeRecord.from_dict(d)
    assert rec2.symbol == "sol" and rec2.side == "sell" and rec2.notional_usd() == 10.0
