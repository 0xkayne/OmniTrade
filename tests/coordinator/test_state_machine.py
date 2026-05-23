"""Tests for is_valid_transition()."""

import pytest

from src.coordinator.state_machine import is_valid_transition


class TestStateMachine:
    # -- valid transitions ------------------------------------------------

    @pytest.mark.parametrize("from_s, to_s", [
        ("PENDING", "VALIDATED"),
        ("PENDING", "REJECTED"),
        ("VALIDATED", "EXECUTING"),
        ("VALIDATED", "REJECTED"),
        ("EXECUTING", "ALL_FILLED"),
        ("EXECUTING", "PARTIAL_FILLED"),
        ("EXECUTING", "EXECUTE_TIMEOUT"),
        ("PARTIAL_FILLED", "ROLLING_BACK"),
        ("EXECUTE_TIMEOUT", "ROLLING_BACK"),
        ("ROLLING_BACK", "ROLLED_BACK"),
        ("ROLLING_BACK", "ROLLED_BACK_FAILED"),
    ])
    def test_valid_transitions(self, from_s, to_s):
        assert is_valid_transition(from_s, to_s) is True

    # -- invalid transitions ----------------------------------------------

    @pytest.mark.parametrize("from_s, to_s", [
        ("PENDING", "ALL_FILLED"),
        ("PENDING", "EXECUTING"),
        ("VALIDATED", "ALL_FILLED"),
        ("EXECUTING", "PENDING"),
        ("EXECUTING", "ROLLED_BACK"),
        ("PARTIAL_FILLED", "EXECUTING"),
        ("PARTIAL_FILLED", "ALL_FILLED"),
        ("ROLLING_BACK", "EXECUTING"),
    ])
    def test_invalid_transitions(self, from_s, to_s):
        assert is_valid_transition(from_s, to_s) is False

    # -- terminal states have no outgoing transitions -----------------------

    @pytest.mark.parametrize("state", ["ALL_FILLED", "ROLLED_BACK", "ROLLED_BACK_FAILED", "REJECTED"])
    def test_terminal_states_no_outgoing(self, state):
        assert is_valid_transition(state, "PENDING") is False
        assert is_valid_transition(state, "EXECUTING") is False

    # -- unknown from_state raises ------------------------------------------

    def test_unknown_from_state_raises(self):
        with pytest.raises(ValueError, match="Unknown from_state"):
            is_valid_transition("NONEXISTENT", "PENDING")
