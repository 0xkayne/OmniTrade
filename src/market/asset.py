from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    symbol: str  # "BTC", "USDT"
    kind: str = "crypto"
