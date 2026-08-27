"""Tests for backtest performance metrics."""

import pytest

from src.strategy.backtest.metrics import compute_metrics
from src.strategy.backtest.portfolio import Portfolio

T1 = "2026-08-27T00:00:00+00:00"
T2 = "2026-08-27T01:00:00+00:00"


def test_metrics_computed_from_realized_trades():
    p = Portfolio(capital=10000, per_trade_usd=1000, fee_rate=0)
    # win +100
    p.execute({"ts": T1, "symbol": "BTC", "direction": "buy", "price": 100})
    p.execute({"ts": T2, "symbol": "BTC", "direction": "sell", "price": 110})
    # loss -200
    p.execute({"ts": T1, "symbol": "ETH", "direction": "buy", "price": 50})
    p.execute({"ts": T2, "symbol": "ETH", "direction": "sell", "price": 40})
    m = compute_metrics(p)
    assert m["num_sells"] == 2
    assert m["win_rate_pct"] == pytest.approx(50.0)
    assert m["realized_pnl"] == pytest.approx(-100.0)  # 100 - 200
    assert m["final_equity"] == pytest.approx(9900.0)  # 10000 - 100
    assert m["return_pct"] == pytest.approx(-1.0)
    assert m["max_drawdown_pct"] > 0
