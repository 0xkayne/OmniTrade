"""Typed configuration contracts for cross-venue arbitrage."""

from __future__ import annotations

from dataclasses import dataclass, field

from .risk import ArbitrageRiskConfig
from .scanner import ScannerConfig


@dataclass(frozen=True)
class ArbitragePairConfig:
    base: str
    market_type: str
    venue_a: str
    symbol_a: str
    venue_b: str
    symbol_b: str

    def __post_init__(self) -> None:
        if self.market_type != "perp":
            raise ValueError("cross-venue arbitrage currently supports perp pairs only")
        if self.venue_a == self.venue_b:
            raise ValueError("arbitrage pair must use different venues")


@dataclass(frozen=True)
class ArbitrageConfig:
    """Normalized config; no file or network access occurs here."""

    enabled: bool = False
    dry_run: bool = True
    execution_mode: str = "ioc_ioc"
    hedged_execution_mode: str = "offline"
    testnet_confirmed: bool = False
    venues: tuple[str, ...] = ()
    pairs: tuple[ArbitragePairConfig, ...] = ()
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    risk: ArbitrageRiskConfig = field(default_factory=ArbitrageRiskConfig)

    def __post_init__(self) -> None:
        if self.hedged_execution_mode not in {"offline", "testnet", "mainnet"}:
            raise ValueError("hedged_execution_mode must be offline, testnet, or mainnet")

    @classmethod
    def from_mapping(cls, values: dict | None) -> ArbitrageConfig:
        root = (values or {}).get("arbitrage", values or {})
        pairs = tuple(ArbitragePairConfig(**pair) for pair in root.get("pairs", ()))
        scanner = ScannerConfig.from_mapping(root)
        risk = ArbitrageRiskConfig.from_mapping(root)
        return cls(
            enabled=bool(root.get("enabled", False)),
            dry_run=bool(root.get("dry_run", True)),
            execution_mode=str(root.get("execution_mode", "ioc_ioc")),
            hedged_execution_mode=str(root.get("hedged_execution_mode", "offline")),
            testnet_confirmed=bool(root.get("testnet_confirmed", False)),
            venues=tuple(str(venue) for venue in root.get("venues", ())),
            pairs=pairs,
            scanner=scanner,
            risk=risk,
        )
