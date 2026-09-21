"""Cross-venue arbitrage domain models, decisions, and bounded execution."""

from .canary import CONFIRMATION_TOKEN, CanaryRequest, CanaryResult, TestnetCanary
from .config import ArbitrageConfig, ArbitragePairConfig
from .executor import ArbOrderRequest, HedgedExecutionResult, HedgedExecutor, LegResult
from .lifecycle import (
    ALLOWED_TRANSITIONS,
    CycleTransitionError,
    PnlBreakdown,
    aggregate_cycle_pnl,
    cycle_net_delta_base,
    cycle_unhedged_qty_base,
    is_valid_cycle_transition,
    is_valid_transition,
    leg_delta_base,
    transition_cycle,
)
from .models import (
    ArbCycle,
    ArbFill,
    ArbitrageCycle,
    ArbitrageOpportunity,
    ArbPair,
    QuoteSnapshot,
    SpreadOpportunity,
)
from .normalization import common_base_quantity, normalize_symbol, quote_snapshot
from .profitability import estimate_vwap, evaluate_spread
from .recovery import ArbitrageRecovery, RecoveryResult
from .risk import ArbitrageRiskConfig, ArbitrageRiskValidator, RiskDecision
from .scanner import CrossVenueArbitrageScanner, ScannerConfig

__all__ = [
    "ALLOWED_TRANSITIONS",
    "CONFIRMATION_TOKEN",
    "ArbCycle",
    "ArbFill",
    "ArbOrderRequest",
    "ArbPair",
    "ArbitrageConfig",
    "ArbitrageCycle",
    "ArbitrageOpportunity",
    "ArbitragePairConfig",
    "ArbitrageRecovery",
    "ArbitrageRiskConfig",
    "ArbitrageRiskValidator",
    "CanaryRequest",
    "CanaryResult",
    "CrossVenueArbitrageScanner",
    "CycleTransitionError",
    "HedgedExecutionResult",
    "HedgedExecutor",
    "LegResult",
    "PnlBreakdown",
    "QuoteSnapshot",
    "RecoveryResult",
    "RiskDecision",
    "ScannerConfig",
    "SpreadOpportunity",
    "TestnetCanary",
    "aggregate_cycle_pnl",
    "common_base_quantity",
    "cycle_net_delta_base",
    "cycle_unhedged_qty_base",
    "estimate_vwap",
    "evaluate_spread",
    "is_valid_cycle_transition",
    "is_valid_transition",
    "leg_delta_base",
    "normalize_symbol",
    "quote_snapshot",
    "transition_cycle",
]
