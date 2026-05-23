from dataclasses import dataclass, field
from typing import Literal

from .instrument import Instrument


@dataclass
class EstimatedFill:
    avg_price: float
    slippage_pct: float  # vs mid_price, e.g. 0.08 meaning 0.08%
    depth_consumed_levels: int
    filled_fully: bool = True  # False if book too shallow


@dataclass
class Quote:
    instrument: Instrument
    fetched_at: float  # time.time() unix timestamp
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    mid_price: float
    taker_fee_rate: float
    maker_fee_rate: float
    funding_rate: float | None = None
    next_funding_time: float | None = None
    open_interest: float | None = None

    _bids: list[tuple[float, float]] = field(default_factory=list, repr=False)
    _asks: list[tuple[float, float]] = field(default_factory=list, repr=False)

    def estimate_fill(self, amount_base: float, side: Literal["buy", "sell"]) -> EstimatedFill:
        """
        Walk the orderbook on the relevant side to estimate fill price.

        For BUY: walk _asks (ascending price — lowest ask first).
        For SELL: walk _bids (descending price — highest bid first).

        Returns an EstimatedFill with avg_price, slippage_pct (vs mid_price),
        depth_consumed_levels, and filled_fully flag.
        """
        if amount_base == 0.0:
            return EstimatedFill(avg_price=0.0, slippage_pct=0.0, depth_consumed_levels=0, filled_fully=True)

        if side == "buy":
            orderbook = self._asks
        else:
            orderbook = self._bids

        if not orderbook:
            return EstimatedFill(avg_price=0.0, slippage_pct=0.0, depth_consumed_levels=0, filled_fully=False)

        remaining = amount_base
        total_cost = 0.0
        filled_qty = 0.0
        levels_consumed = 0

        for price, qty in orderbook:
            take = min(qty, remaining)
            total_cost += take * price
            filled_qty += take
            remaining -= take
            levels_consumed += 1
            if remaining <= 0:
                break

        if filled_qty == 0:
            return EstimatedFill(avg_price=0.0, slippage_pct=0.0, depth_consumed_levels=0, filled_fully=False)

        avg_price = total_cost / filled_qty
        filled_fully = remaining <= 1e-12

        if self.mid_price > 0:
            if side == "buy":
                slippage_pct = ((avg_price - self.mid_price) / self.mid_price) * 100
            else:
                slippage_pct = ((self.mid_price - avg_price) / self.mid_price) * 100
        else:
            slippage_pct = 0.0

        return EstimatedFill(
            avg_price=avg_price,
            slippage_pct=slippage_pct,
            depth_consumed_levels=levels_consumed,
            filled_fully=filled_fully,
        )
