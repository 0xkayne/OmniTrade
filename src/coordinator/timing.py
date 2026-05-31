"""Lightweight hierarchical timing collector for oneFill pipeline instrumentation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

_PHASES = ("plan", "validate", "execute", "reconcile")


@dataclass
class TimingCollector:
    """Accumulates wall-clock timing during a pipeline run.

    All durations are milliseconds (float).  Nested dicts keyed by label
    strings for trivial JSON serialisation.
    """

    # Phase-level totals
    plan_ms: float = 0.0
    validate_ms: float = 0.0
    execute_ms: float = 0.0
    reconcile_ms: float = 0.0

    # Per-leg breakdowns  (venue -> detail dict)
    plan_legs: dict[str, dict[str, float]] = field(default_factory=dict)
    validate_legs: dict[str, dict[str, float]] = field(default_factory=dict)
    execute_legs: dict[str, dict[str, float]] = field(default_factory=dict)
    reconcile_legs: dict[str, dict[str, float]] = field(default_factory=dict)

    # End-to-end
    bootstrap_ms: float = 0.0

    # Internal scratch space for in-flight timers (not serialised)
    _marks: dict[str, float] = field(default_factory=dict, repr=False)

    def mark(self, label: str) -> None:
        self._marks[label] = time.perf_counter()

    def pop(self, label: str) -> float:
        elapsed = (time.perf_counter() - self._marks.pop(label)) * 1000.0
        return elapsed

    def has_mark(self, label: str) -> bool:
        return label in self._marks

    def ensure_leg(self, group: str, venue: str) -> dict[str, float]:
        field_name = f"{group}_legs"
        target: dict[str, dict[str, float]] = getattr(self, field_name)
        if venue not in target:
            target[venue] = {}
        return target[venue]

    def to_dict(self) -> dict[str, Any]:
        phases: dict[str, dict[str, Any]] = {}
        for name in _PHASES:
            total_ms: float = getattr(self, f"{name}_ms")
            legs: dict[str, dict[str, float]] = getattr(self, f"{name}_legs")
            phases[name] = {
                "total_ms": round(total_ms, 3),
                "legs": {
                    v: {k: round(val, 3) for k, val in d.items()}
                    for v, d in legs.items()
                },
            }
        total_ms = self.bootstrap_ms + sum(phases[name]["total_ms"] for name in _PHASES)
        return {
            "bootstrap_ms": round(self.bootstrap_ms, 3),
            "phases": phases,
            "total_ms": round(total_ms, 3),
        }
