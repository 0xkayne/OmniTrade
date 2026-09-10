"""Tests for the backtest portfolio (shared-cash, skip-if-short, realized pnl)."""

import pytest

from src.strategy.backtest.portfolio import Portfolio

T1 = "2026-08-27T00:00:00+00:00"
T2 = "2026-08-27T01:00:00+00:00"


def test_buy_opens_position_and_debits_cash():
    p = Portfolio(capital=10000, per_trade_usd=1000, fee_rate=0.001)
    p.execute({"ts": T1, "symbol": "BTC", "direction": "buy", "price": 100})
    assert "BTC" in p.positions
    assert p.positions["BTC"].qty == pytest.approx(10.0)  # 1000/100
    assert p.cash == pytest.approx(8999.0)  # 10000 - (1000 + 1 fee)


def test_buy_skipped_when_cash_short():
    p = Portfolio(capital=500, per_trade_usd=1000, fee_rate=0)
    p.execute({"ts": T1, "symbol": "BTC", "direction": "buy", "price": 100})
    assert not p.positions
    assert p.cash == 500


def test_sell_books_pnl_and_recycles_cash():
    p = Portfolio(capital=10000, per_trade_usd=1000, fee_rate=0)
    p.execute({"ts": T1, "symbol": "BTC", "direction": "buy", "price": 100})
    p.execute({"ts": T2, "symbol": "BTC", "direction": "sell", "price": 110})
    assert "BTC" not in p.positions
    assert p.realized_pnl == pytest.approx(100.0)  # (110-100)*10
    assert p.cash == pytest.approx(10100.0)
    sells = [t for t in p.trades if t["side"] == "sell"]
    assert sells[0]["pnl"] == pytest.approx(100.0)


def test_equity_marked_after_each_fill():
    p = Portfolio(capital=10000, per_trade_usd=1000, fee_rate=0)
    p.execute({"ts": T1, "symbol": "BTC", "direction": "buy", "price": 100})
    assert p.equity_curve[-1][1] == pytest.approx(10000.0)  # cash 9000 + 10*100
    p.execute({"ts": T2, "symbol": "BTC", "direction": "sell", "price": 110})
    assert p.equity_curve[-1][1] == pytest.approx(10100.0)
