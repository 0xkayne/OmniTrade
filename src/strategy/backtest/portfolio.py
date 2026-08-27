"""Backtest portfolio: simulate a shared cash pool across rotating positions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Position:
    symbol: str
    qty: float
    avg_cost: float


@dataclass
class Portfolio:
    """Shared-cash portfolio. Buys cost ``per_trade_usd`` (skipped if cash short);
    sells a symbol's whole position, booking realized pnl. Equity is marked after
    every fill plus once at the end."""

    capital: float = 10_000.0
    per_trade_usd: float = 1_000.0
    fee_rate: float = 0.0005
    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    trades: list[dict] = field(default_factory=list)
    realized_pnl: float = field(default=0.0, init=False)
    last_price: dict[str, float] = field(default_factory=dict)
    equity_curve: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cash = self.capital

    def execute(self, event: dict) -> None:
        ts, symbol, direction, price = event["ts"], event["symbol"], event["direction"], event["price"]
        now = datetime.fromisoformat(ts).timestamp()
        if direction == "buy":
            self._buy(symbol, price, ts)
        else:
            self._sell(symbol, price, ts)
        self.last_price[symbol] = price
        self._record_equity(now)

    def _buy(self, symbol: str, price: float, ts: str) -> None:
        qty = self.per_trade_usd / price
        cost = qty * price
        fee = cost * self.fee_rate
        total = cost + fee
        if self.cash < total:
            return  # cash constraint: skip the buy rather than over-leveraging
        self.cash -= total
        pos = self.positions.get(symbol)
        if pos:
            new_qty = pos.qty + qty
            pos.avg_cost = (pos.avg_cost * pos.qty + cost) / new_qty
            pos.qty = new_qty
        else:
            self.positions[symbol] = Position(symbol, qty, price)
        self.trades.append({"ts": ts, "symbol": symbol, "side": "buy", "qty": qty, "price": price, "fee": fee, "pnl": None})

    def _sell(self, symbol: str, price: float, ts: str) -> None:
        pos = self.positions.get(symbol)
        if pos is None or pos.qty <= 0:
            return
        qty = pos.qty
        proceeds = qty * price
        fee = proceeds * self.fee_rate
        pnl = (price - pos.avg_cost) * qty - fee
        self.cash += proceeds - fee
        self.realized_pnl += pnl
        del self.positions[symbol]
        self.trades.append({"ts": ts, "symbol": symbol, "side": "sell", "qty": qty, "price": price, "fee": fee, "pnl": pnl})

    def _record_equity(self, now: float) -> None:
        self.equity_curve.append((now, self.equity()))

    def equity(self) -> float:
        open_value = sum(p.qty * self.last_price.get(p.symbol, p.avg_cost) for p in self.positions.values())
        return self.cash + open_value
