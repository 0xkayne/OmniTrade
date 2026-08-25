"""Alert rule: detect 10%-type breaks vs a 7-day window, event + re-arm."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AlertRule:
    """Thresholds as fractions (0.10 == 10%)."""

    drop_pct: float = 0.10
    rise_pct: float = 0.10


@dataclass
class AlertState:
    """Per-asset alert state machine (event fires once; re-arms when price recovers)."""

    armed: bool = True
    trigger_level: float | None = None
    last_direction: str | None = None  # "lower" | "upper"


def evaluate(
    rule: AlertRule,
    state: AlertState,
    latest: float,
    min_low: float,
    max_high: float,
) -> str | None:
    """Evaluate the alert condition; returns a message string or None.

    Mutates ``state`` in place:
      - when armed and ``latest`` breaks 10% below ``min_low`` (or above
        ``max_high``) it fires once and disarms, recording the trigger level;
      - when disarmed it re-arms once ``latest`` returns past the trigger level.
    """
    if latest is None or min_low is None or max_high is None:
        return None

    if state.armed:
        down = min_low * (1 - rule.drop_pct)
        up = max_high * (1 + rule.rise_pct)
        if latest <= down:
            state.armed = False
            state.trigger_level = down
            state.last_direction = "lower"
            pct = (1 - latest / min_low) * 100
            return (
                f"▽ broke below 7d low {min_low:.6g}: now {latest:.6g} "
                f"(-{pct:.1f}%, threshold {rule.drop_pct * 100:.0f}%)"
            )
        if latest >= up:
            state.armed = False
            state.trigger_level = up
            state.last_direction = "upper"
            pct = (latest / max_high - 1) * 100
            return (
                f"△ broke above 7d high {max_high:.6g}: now {latest:.6g} "
                f"(+{pct:.1f}%, threshold {rule.rise_pct * 100:.0f}%)"
            )

    # Re-arm when price returns past the trigger level
    if not state.armed and state.trigger_level is not None and state.last_direction is not None:
        lower_rearmed = state.last_direction == "lower" and latest >= state.trigger_level
        upper_rearmed = state.last_direction == "upper" and latest <= state.trigger_level
        if lower_rearmed or upper_rearmed:
            state.armed = True
            state.trigger_level = None
            state.last_direction = None

    return None
