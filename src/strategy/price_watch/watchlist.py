"""Watchlist model — the user's tagged asset list (config/watchlist.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_QUOTE_PREFERENCE = ["USDT", "USDC", "USD"]


@dataclass(frozen=True)
class WatchItem:
    """One watchlist asset with a user-assigned category label."""

    symbol: str
    tag: str
    market_type: str = "perp"
    quote_preference: list[str] = field(default_factory=lambda: list(DEFAULT_QUOTE_PREFERENCE))


def load_watchlist(path: Path) -> list[WatchItem]:
    """Parse ``config/watchlist.yaml`` into a list of :class:`WatchItem`.

    Schema (top-level ``watchlist:`` list of dicts)::

        watchlist:
          - symbol: BTC
            tag: 龙头
          - symbol: SOL
            tag: 公链
            market_type: perp
            quote_preference: [USDT, USDC, USD]
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Watchlist config not found at {path.absolute()}. "
            f"oneFill expects to be run from the project root (current cwd: {Path.cwd()})."
        )
    with open(path) as f:
        data = yaml.safe_load(f) or {}

    items: list[WatchItem] = []
    for raw in data.get("watchlist", []):
        if "symbol" not in raw or "tag" not in raw:
            raise ValueError(f"Each watchlist entry needs 'symbol' and 'tag': {raw!r}")
        items.append(
            WatchItem(
                symbol=str(raw["symbol"]).upper(),
                tag=str(raw["tag"]),
                market_type=str(raw.get("market_type", "perp")),
                quote_preference=[
                    q.upper() for q in raw.get("quote_preference", DEFAULT_QUOTE_PREFERENCE)
                ],
            )
        )
    return items
