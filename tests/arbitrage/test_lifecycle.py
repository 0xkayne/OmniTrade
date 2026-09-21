"""Tests for arbitrage cycle transitions and accounting helpers."""

import pytest

from src.arbitrage.lifecycle import (
    CycleTransitionError,
    aggregate_cycle_pnl,
    cycle_net_delta_base,
    cycle_unhedged_qty_base,
    is_valid_transition,
    leg_delta_base,
    transition_cycle,
)
from src.arbitrage.models import ArbCycle, ArbFill, ArbPair


def make_cycle(**kwargs) -> ArbCycle:
    pair = ArbPair(
        base="BTC",
        market_type="perp",
        venue_a="arcus",
        symbol_a="BTC-USD",
        venue_b="hyperliquid",
        symbol_b="BTC/USD:USDC",
        contract_size_a=0.1,
        contract_size_b=1.0,
        hedge_ratio=1.0,
    )
    return ArbCycle(cycle_id="cycle-1", pair=pair, direction="buy_a_sell_b", target_qty=2.0, **kwargs)


def test_transition_cycle_returns_copy_and_preserves_input() -> None:
    cycle = make_cycle()
    transitioned = transition_cycle(cycle, "prechecked")

    assert cycle.status == "detected"
    assert transitioned.status == "prechecked"
    assert transitioned is not cycle
    assert transitioned.fills is not cycle.fills


def test_invalid_and_terminal_transitions_are_rejected() -> None:
    assert is_valid_transition("opening", "hedging")
    assert not is_valid_transition("detected", "open")
    assert not is_valid_transition("closed", "opening")
    with pytest.raises(CycleTransitionError):
        transition_cycle(make_cycle(), "open")


def test_cycle_delta_accounts_for_contract_sizes_and_direction() -> None:
    cycle = make_cycle(filled_qty_a=10.0, filled_qty_b=0.8)

    assert leg_delta_base(cycle, "a") == pytest.approx(1.0)
    assert leg_delta_base(cycle, "b") == pytest.approx(-0.8)
    assert cycle_net_delta_base(cycle) == pytest.approx(0.2)
    assert cycle_unhedged_qty_base(cycle) == pytest.approx(0.2)


def test_pnl_aggregation_uses_fill_cash_flows_and_costs() -> None:
    cycle = make_cycle(
        realized_funding_usd=0.3,
        realized_slippage_usd=0.2,
        fills=[
            ArbFill("cycle-1", "arcus", "BTC-USD", "a-1", "buy", 1.0, 100.0, fee=0.1),
            ArbFill("cycle-1", "hyperliquid", "BTC/USD:USDC", "b-1", "sell", 1.0, 101.5, fee=0.1),
        ],
    )

    pnl = aggregate_cycle_pnl(cycle)
    assert pnl.gross_pnl_usd == pytest.approx(1.5)
    assert pnl.fee_usd == pytest.approx(0.2)
    assert pnl.net_pnl_usd == pytest.approx(0.8)


def test_pnl_aggregation_falls_back_to_realized_record() -> None:
    cycle = make_cycle(
        realized_gross_pnl_usd=5.0,
        realized_fee_usd=1.0,
        realized_funding_usd=0.5,
        realized_slippage_usd=0.25,
        realized_net_pnl_usd=3.25,
    )

    assert aggregate_cycle_pnl(cycle).net_pnl_usd == pytest.approx(3.25)
