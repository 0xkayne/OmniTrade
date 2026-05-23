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
        raise NotImplementedError  # Subagent A implements this
