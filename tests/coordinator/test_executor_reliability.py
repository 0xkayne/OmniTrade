"""Execution, compensation and restart invariants through the real coordinator."""

import json
import time
from dataclasses import asdict

import pytest

from src.coordinator.executor import Executor
from src.coordinator.orchestrator import Orchestrator
from src.coordinator.planner import Planner
from src.coordinator.reconciler import Reconciler
from tests.coordinator.conftest import make_intent


@pytest.fixture
def orch(sample_registry, quote_fetcher, fake_exchanges, fake_store):
    return Orchestrator(sample_registry, quote_fetcher, fake_exchanges, fake_store, use_websocket=False)


@pytest.mark.asyncio
async def test_split_orders_have_unique_durable_ids(orch, fake_store, fake_binance):
    intent = make_intent(split={"binance": 1.0}, total_notional_usd=900)
    intent.max_order_notional_usd = 350
    result = await orch.submit(intent)
    assert result["status"] == "ALL_FILLED"
    calls = fake_binance.create_order_calls
    assert len(calls) == 3
    assert len({call["params"]["clientOrderId"] for call in calls}) == 3
    assert all(call["order_type"] == "limit" and call["params"]["timeInForce"] == "IOC" for call in calls)
    assert all(call["amount"] * call["price"] <= 350 for call in calls)
    rows = await fake_store.get_orders_for_leg(result["legs"][0]["leg_id"])
    assert len(rows) == 3
    assert all(row.snapshot_json for row in rows)
    assert sum(call["amount"] for call in calls) == pytest.approx(result["legs"][0]["planned_qty_base"])


@pytest.mark.asyncio
async def test_duplicate_intent_returns_recorded_result(orch, fake_binance):
    intent = make_intent(split={"binance": 1.0})
    first = await orch.submit(intent)
    second = await orch.submit(intent)
    assert first["status"] == second["status"] == "ALL_FILLED"
    assert second["is_duplicate"]
    assert len(fake_binance.create_order_calls) == 1


@pytest.mark.asyncio
async def test_closed_partial_fill_is_compensated_for_actual_qty(orch, fake_binance):
    fake_binance.inject_next_order_result(
        "BTCUSDT",
        {
            "id": "partial-original",
            "status": "canceled",
            "filled": 0.004,
            "average": 50000,
            "fee": {"cost": 0.2, "currency": "USDT"},
        },
    )
    result = await orch.submit(make_intent(split={"binance": 1.0}))
    assert result["status"] == "ROLLED_BACK"
    assert fake_binance.create_order_calls[-1]["side"] == "sell"
    assert fake_binance.create_order_calls[-1]["amount"] == 0.004
    assert result["reconciliation"]["residual_exposure_usd"] == 0


@pytest.mark.asyncio
async def test_reconciliation_preserves_unknown_original_fee(orch, fake_binance, fake_store):
    fake_binance.inject_next_order_result(
        "BTCUSDT",
        {"id": "partial-no-fee", "status": "canceled", "filled": 0.004, "average": 50000},
    )
    result = await orch.submit(make_intent(split={"binance": 1.0}))
    assert result["status"] == "ROLLED_BACK"
    leg = await fake_store.get_leg(result["legs"][0]["leg_id"])
    assert leg.fee_usd is None


@pytest.mark.asyncio
async def test_partial_compensation_blocks_with_dollar_residual(orch, fake_binance):
    original = fake_binance.create_order

    async def partial(*args, **kwargs):
        order = await original(*args, **kwargs)
        qty = 0.004 if order["side"] == "buy" else 0.002
        order.update(status="canceled", filled=qty, average=50000)
        return order

    fake_binance.create_order = partial
    result = await orch.submit(make_intent(split={"binance": 1.0}))
    assert result["status"] == "ROLLED_BACK_FAILED"
    assert result["reconciliation"]["residual_exposure_usd"] == pytest.approx(100)
    next_result = await orch.submit(make_intent(intent_id="next", split={"binance": 1.0}))
    assert next_result["status"] == "REJECTED"
    assert len(fake_binance.create_order_calls) == 2


@pytest.mark.asyncio
async def test_recovery_reuses_original_and_compensation_ids(
    orch, fake_store, fake_binance, quote_fetcher, sample_registry, fake_exchanges
):
    intent = make_intent(split={"binance": 1.0})
    await fake_store.create_intent(intent, "VALIDATED")
    plan = await Planner(sample_registry, quote_fetcher).plan(intent)
    result = await Executor(fake_exchanges, fake_store, use_websocket=False).execute(plan)
    assert result.status == "ALL_FILLED"
    # Simulate a crash after order confirmation but before the intent terminal write.
    await fake_store.update_intent_status(intent.intent_id, "EXECUTING")
    recovered = await orch.recover(intent.intent_id)
    assert recovered["status"] == "ROLLED_BACK"
    assert len(fake_binance.create_order_calls) == 2
    again = await orch.recover(intent.intent_id)
    assert again["status"] == "ROLLED_BACK"
    assert len(fake_binance.create_order_calls) == 2


@pytest.mark.asyncio
async def test_ambiguous_original_blocks_without_claiming_zero_exposure(orch, fake_binance):
    intent = make_intent(split={"binance": 1.0}, execute_timeout_seconds=0.05)
    intent.reconcile_timeout_seconds = 0.05
    fake_binance.inject_order_error("BTCUSDT", TimeoutError("response lost"))
    result = await orch.submit(intent)
    assert result["status"] == "ROLLED_BACK_FAILED"
    assert result["reconciliation"]["residual_exposure_usd"] is None
    assert len(fake_binance.create_order_calls) == 1


@pytest.mark.asyncio
async def test_preflight_rejects_price_move_before_any_send(
    fake_store, fake_exchanges, fake_binance, quote_fetcher, sample_registry
):
    intent = make_intent(split={"binance": 1.0})
    plan = await Planner(sample_registry, quote_fetcher).plan(intent)
    await fake_store.create_intent(intent, "VALIDATED")
    fake_binance.set_orderbook("BTCUSDT", [(52000, 10)], [(52001, 10)])
    result = await Executor(fake_exchanges, fake_store).execute(plan)
    assert result.status == "REJECTED"
    assert not fake_binance.create_order_calls


@pytest.mark.asyncio
async def test_recovery_does_not_retry_manual_terminal(orch, fake_store, fake_binance):
    intent = make_intent(split={"binance": 1.0})
    await fake_store.create_intent(intent, "ROLLED_BACK_FAILED")
    assert (await orch.recover(intent.intent_id))["status"] == "ROLLED_BACK_FAILED"
    assert not fake_binance.create_order_calls


@pytest.mark.asyncio
async def test_dry_run_validates_balance_and_leaves_no_active_intent(orch, fake_binance, fake_store):
    fake_binance.set_balance("USDT", 0)
    result = await orch.submit(make_intent(split={"binance": 1.0}), dry_run=True)
    assert result["validation_failures"]
    assert await fake_store.count_intents_with_status("PENDING") == 0
    assert not fake_binance.create_order_calls
