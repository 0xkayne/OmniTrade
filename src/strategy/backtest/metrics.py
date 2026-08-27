"""Backtest performance metrics."""

from __future__ import annotations

from src.strategy.backtest.portfolio import Portfolio


def compute_metrics(portfolio: Portfolio) -> dict:
    trades = portfolio.trades
    final = portfolio.equity()
    ret = (final / portfolio.capital - 1) * 100 if portfolio.capital else 0.0

    sells = [t for t in trades if t["side"] == "sell"]
    wins = [t for t in sells if (t["pnl"] or 0) > 0]
    losses = [t for t in sells if (t["pnl"] or 0) <= 0]
    win_rate = (len(wins) / len(sells) * 100) if sells else 0.0
    gross_win = sum(t["pnl"] or 0 for t in wins)
    gross_loss = abs(sum(t["pnl"] or 0 for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)

    peak = 0.0
    max_dd = 0.0
    for _ts, eq in portfolio.equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak)
    max_dd *= 100

    avg_win = (sum(t["pnl"] or 0 for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(t["pnl"] or 0 for t in losses) / len(losses)) if losses else 0.0

    return {
        "capital": portfolio.capital,
        "final_equity": final,
        "return_pct": ret,
        "realized_pnl": portfolio.realized_pnl,
        "num_trades": len(trades),
        "num_sells": len(sells),
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "max_drawdown_pct": max_dd,
    }
