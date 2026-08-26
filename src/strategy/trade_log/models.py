"""Manual trade-log domain model — one record per single order."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal


@dataclass
class TradeRecord:
    """A single manually-logged trade (one order).

    Not tied to a oneFill intent — this is the user's own trade journal for
    later strategy analysis. ``notional_usd`` is derived from ``qty * price``.
    """

    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    price: float
    venue: str | None = None
    tag: str | None = None
    fee_usd: float | None = None
    pnl_usd: float | None = None
    strategy: str | None = None
    reason: str | None = None
    note: str | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: str | None = None  # default: now

    def __post_init__(self) -> None:
        if self.ts is None:
            self.ts = datetime.now(timezone.utc).isoformat()

    def notional_usd(self) -> float:
        return self.qty * self.price

    def to_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["notional_usd"] = self.notional_usd()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> TradeRecord:
        return cls(
            symbol=str(data["symbol"]),
            side=str(data["side"]),
            qty=float(data["qty"]),
            price=float(data["price"]),
            venue=data.get("venue"),
            tag=data.get("tag"),
            fee_usd=data.get("fee_usd"),
            pnl_usd=data.get("pnl_usd"),
            strategy=data.get("strategy"),
            reason=data.get("reason"),
            note=data.get("note"),
            id=data.get("id"),
            ts=data.get("ts"),
        )
