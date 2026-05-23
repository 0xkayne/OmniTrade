# Intent-level states (see PRD §7.1)
INTENT_STATES = [
    ("PENDING", "Intent created, not yet processed"),
    ("VALIDATED", "Passed validation, about to execute"),
    ("EXECUTING", "Orders being sent/polled"),
    ("ALL_FILLED", "All legs filled — terminal"),
    ("PARTIAL_FILLED", "Some legs filled, some failed — entering reconciliation"),
    ("ROLLING_BACK", "Reverse orders being sent for filled legs"),
    ("ROLLED_BACK", "Compensation succeeded — terminal"),
    ("ROLLED_BACK_FAILED", "Compensation failed — terminal, blocks further Intents"),
    ("REJECTED", "Plan or validation rejected before any orders — terminal"),
]

TERMINAL_STATES = {"ALL_FILLED", "ROLLED_BACK", "ROLLED_BACK_FAILED", "REJECTED"}
BLOCKING_STATE = "ROLLED_BACK_FAILED"  # also referred to as NEEDS_MANUAL

# Leg-level states
LEG_STATES = [
    ("PENDING_SEND", "Leg created, not yet sent"),
    ("SENT", "Order sent, awaiting fill"),
    ("FILLED", "Fully filled"),
    ("PARTIAL_FILLED", "Partially filled"),
    ("REJECTED", "Order rejected by venue"),
    ("TIMEOUT", "Fill polling timed out"),
    ("CANCELLED", "Canceled before fill"),
    ("COMPENSATING", "Reverse order in flight to flatten this leg"),
    ("COMPENSATED", "Reverse order filled"),
    ("COMPENSATION_FAILED", "Reverse order failed"),
]

# Transition table: from_state -> set of allowed to_state values
_TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"EXECUTING", "REJECTED"},
    "EXECUTING": {"ALL_FILLED", "PARTIAL_FILLED", "EXECUTE_TIMEOUT"},
    "PARTIAL_FILLED": {"ROLLING_BACK"},
    "EXECUTE_TIMEOUT": {"ROLLING_BACK"},
    "ROLLING_BACK": {"ROLLED_BACK", "ROLLED_BACK_FAILED"},
    # Terminal states — no outgoing transitions:
    "ALL_FILLED": set(),
    "ROLLED_BACK": set(),
    "ROLLED_BACK_FAILED": set(),
    "REJECTED": set(),
}


def is_valid_transition(from_state: str, to_state: str) -> bool:
    """Check whether the state machine allows this transition."""
    valid_targets = _TRANSITIONS.get(from_state)
    if valid_targets is None:
        raise ValueError(f"Unknown from_state: {from_state!r}")
    return to_state in valid_targets
