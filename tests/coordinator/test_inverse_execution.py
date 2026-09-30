"""Offline COIN-M execution through real planning, persistence and recovery."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.coordinator.intent import Intent, LegConfig
from src.coordinator.orchestrator import Orchestrator
from src.coordinator.planner import Planner
from src.coordinator.risk import RiskValidator
from src.coordinator.state_machine import BLOCKING_STATE
from src.exchange.mock import MockExchange
from src.exchange.order import OrderCapabilities
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.market.quote_fetcher import QuoteFetcher
from src.market.registry import InstrumentRegistry
from src.persistence.store import PersistenceStore


def make_intent(**overrides) -> Intent:
    values = {
        "intent_id": "inverse-intent",
        "base": "BTC",
        "quote_preference": ["USD"],
        "product": "perp",
        "side": "buy",
        "order_type": "market",
        "total_notional_usd": 1000.0,
        "split": {"binance": 1.0},
        "contract_type": "inverse",
        "settlement_asset": "BTC",
        "max_quote_age_ms": 10000,
        "execute_timeout_seconds": 5,
        "reconcile_timeout_seconds": 5,
    }
    return Intent(**(values | overrides))


def set_book(exchange, instrument, price=50000):
    exchange.set_orderbook(
        instrument.venue_symbol,
        bids=[(price - 1, 100)],
        asks=[(price + 1, 100)],
    )


def inject_fill(harness, quantity, price=50000, *, status="closed", fee_currency="BTC"):
    harness.exchange.inject_next_order_result(
        harness.instrument.venue_symbol,
        {
            "id": f"filled-{len(harness.exchange.create_order_calls)}",
            "status": status,
            "filled": quantity,
            "average": price,
            "fee": {"currency": fee_currency, "cost": 0.000001},
        },
    )


@pytest.fixture
async def inverse(tmp_path):
    instrument = Instrument(
        venue="binance",
        network=NetworkType.TESTNET,
        market_type="perp",
        base=Asset("BTC"),
        quote=Asset("USD"),
        venue_symbol="BTC/USD:BTC",
        min_qty=1,
        qty_step=1,
        price_step=0.1,
        contract_size=100,
        is_inverse=True,
        settlement_asset=Asset("BTC"),
        quantity_unit="contracts",
        taker_fee_rate=0.0005,
    )
    exchange = MockExchange("binance")
    exchange.set_markets([instrument])
    exchange.set_balance("BTC", 10)
    exchange.set_position(instrument.venue_symbol, 0)
    set_book(exchange, instrument)
    exchanges = {"binance": exchange}
    registry = InstrumentRegistry()
    registry.add(instrument)
    fetcher = QuoteFetcher(exchanges)
    store = PersistenceStore(tmp_path / "orders.db", tmp_path / "audit")
    await store.initialize()
    harness = SimpleNamespace(
        instrument=instrument,
        exchange=exchange,
        exchanges=exchanges,
        registry=registry,
        fetcher=fetcher,
        store=store,
        db_path=tmp_path / "orders.db",
        audit_path=tmp_path / "audit",
        orchestrator=Orchestrator(registry, fetcher, exchanges, store, use_websocket=False, poll_interval_ms=1),
    )
    try:
        yield harness
    finally:
        await harness.orchestrator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("price", [20000, 50000])
async def test_inverse_plan_notional_sizes_contracts_independent_of_price(inverse, price):
    set_book(inverse.exchange, inverse.instrument, price)

    plan = await Planner(inverse.registry, inverse.fetcher, inverse.exchanges).plan(make_intent())

    assert plan.is_acceptable
    assert plan.legs[0].native_qty == 10
    assert plan.legs[0].planned_qty_base == pytest.approx(1000 / price)
    assert not inverse.exchange.create_order_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline", [0, 7])
async def test_inverse_open_preserves_existing_position_and_native_facts(inverse, baseline):
    inverse.exchange.set_position(inverse.instrument.venue_symbol, baseline)
    inject_fill(inverse, 10)

    result = await inverse.orchestrator.submit(make_intent())

    assert result["status"] == "ALL_FILLED", result
    assert Decimal(result["legs"][0]["filled_qty_native"]) == 10
    assert Decimal(result["legs"][0]["position_before_qty_native"]) == baseline
    position = await inverse.exchange.fetch_order_position(inverse.instrument)
    assert position.qty_native == baseline + 10
    rows = await inverse.store.get_legs_for_intent(result["intent_id"])
    assert Decimal(rows[0].planned_qty_native) == Decimal(rows[0].filled_qty_native) == 10
    facts = await inverse.store.get_order_fills(intent_id=result["intent_id"])
    assert len(facts) == 1
    assert Decimal(facts[0].qty_native) == 10
    assert Decimal(facts[0].qty_base) == Decimal("0.02")
    assert Decimal(facts[0].notional_quote) == 1000


@pytest.mark.asyncio
async def test_inverse_close_at_changed_price_reduces_contracts_without_base_residual(inverse):
    inverse.exchange.set_position(inverse.instrument.venue_symbol, 10, entry_price=20000)
    set_book(inverse.exchange, inverse.instrument, 25000)
    inject_fill(inverse, 2, 25000)

    result = await inverse.orchestrator.submit(
        make_intent(position_effect="close", side="sell", total_notional_usd=200, quantity_native=2)
    )

    assert result["status"] == "ALL_FILLED", result
    leg = result["legs"][0]
    assert leg["filled_amount"] == pytest.approx(0.008)
    assert leg["filled_amount"] != pytest.approx(2 * 100 / 20000)
    assert Decimal(leg["remaining_requested_qty_native"]) == 0
    assert Decimal(leg["position_after_qty_native"]) == 8
    assert len(inverse.exchange.create_order_calls) == 1
    assert inverse.exchange.create_order_calls[0]["params"]["reduceOnly"] is True
    facts = await inverse.store.get_order_fills(intent_id=result["intent_id"])
    assert Decimal(facts[0].realized_pnl_usd) == pytest.approx(50)


@pytest.mark.asyncio
async def test_partial_close_blocks_without_reopening_and_terminal_recovery_does_nothing(inverse):
    inverse.exchange.set_position(inverse.instrument.venue_symbol, 10, entry_price=20000)
    set_book(inverse.exchange, inverse.instrument, 25000)
    inject_fill(inverse, 2, 25000, status="canceled")

    result = await inverse.orchestrator.submit(
        make_intent(position_effect="close", side="sell", total_notional_usd=400, quantity_native=4)
    )

    assert result["status"] == BLOCKING_STATE, result
    leg = result["legs"][0]
    assert Decimal(leg["remaining_requested_qty_native"]) == 2
    assert Decimal(leg["position_after_qty_native"]) == 8
    assert "close_incomplete" in leg["error"]
    assert len(inverse.exchange.create_order_calls) == 1
    orders = await inverse.store.get_orders_for_leg(leg["leg_id"])
    assert [order.purpose for order in orders] == ["original"]
    before = (len(inverse.exchange.create_order_calls), len(inverse.exchange.cancel_order_calls))
    recovered = await inverse.orchestrator.recover(result["intent_id"])
    assert recovered["status"] == BLOCKING_STATE
    assert before == (len(inverse.exchange.create_order_calls), len(inverse.exchange.cancel_order_calls))


@pytest.mark.asyncio
async def test_partial_inverse_open_compensates_only_its_native_fill_to_baseline(inverse):
    linear = replace(
        inverse.instrument,
        venue="hyperliquid",
        venue_symbol="BTC/USDC:USDC",
        quote=Asset("USDC"),
        settlement_asset=Asset("USDC"),
        contract_size=1,
        is_inverse=False,
        min_qty=0.001,
        qty_step=0.001,
    )
    other = MockExchange("hyperliquid")
    other.set_markets([linear])
    other.set_position(linear.venue_symbol, 0)
    other.set_balance("USDC", 100000)
    other.set_fail_create(True, "injected rejection")
    set_book(other, linear)
    inverse.exchanges["hyperliquid"] = other
    inverse.registry.add(linear)
    inverse.exchange.set_position(inverse.instrument.venue_symbol, 3)
    inject_fill(inverse, 2, status="canceled")

    result = await inverse.orchestrator.submit(
        make_intent(
            split={"binance": 0.5, "hyperliquid": 0.5},
            quote_preference=["USD", "USDC"],
            leg_configs={"hyperliquid": LegConfig(contract_type="linear", settlement_asset="USDC")},
        )
    )

    assert result["status"] == "ROLLED_BACK", result
    position = await inverse.exchange.fetch_order_position(inverse.instrument)
    assert position.qty_native == 3
    calls = inverse.exchange.create_order_calls
    assert [(call["side"], call["amount"]) for call in calls] == [("buy", 5), ("sell", 2)]
    assert calls[1]["params"]["reduceOnly"] is True
    rows = await inverse.store.get_legs_for_intent(result["intent_id"])
    row = next(row for row in rows if row.venue == "binance")
    assert Decimal(row.filled_qty_native) == Decimal(row.compensation_filled_qty_native) == 2


@pytest.mark.asyncio
async def test_restart_after_close_accepted_without_reply_queries_without_resubmitting(inverse, monkeypatch):
    inverse.exchange.set_position(inverse.instrument.venue_symbol, 10)
    inject_fill(inverse, 2)
    create_order = inverse.exchange.create_order

    async def crash_after_acceptance(*args, **kwargs):
        await create_order(*args, **kwargs)
        raise asyncio.CancelledError("process interrupted after exchange acceptance")

    monkeypatch.setattr(inverse.exchange, "create_order", crash_after_acceptance)
    intent = make_intent(position_effect="close", side="sell", total_notional_usd=200, quantity_native=2)
    with pytest.raises(asyncio.CancelledError):
        await inverse.orchestrator.submit(intent)
    rows = await inverse.store.get_legs_for_intent(intent.intent_id)
    orders = await inverse.store.get_orders_for_leg(rows[0].leg_id)
    assert orders[0].status == "UNKNOWN"
    assert (await inverse.store.get_intent(intent.intent_id)).status == "EXECUTING"
    await inverse.store.close()
    inverse.store = PersistenceStore(inverse.db_path, inverse.audit_path)
    await inverse.store.initialize()
    inverse.orchestrator = Orchestrator(
        inverse.registry, inverse.fetcher, inverse.exchanges, inverse.store, use_websocket=False
    )

    result = await inverse.orchestrator.recover(intent.intent_id)

    assert result["status"] == "ALL_FILLED", result
    assert len(inverse.exchange.create_order_calls) == 1
    assert inverse.exchange.fetch_order_calls
    assert (await inverse.exchange.fetch_order_position(inverse.instrument)).qty_native == 8
    assert len(await inverse.store.get_order_fills(intent_id=intent.intent_id)) == 1


@pytest.mark.asyncio
async def test_legacy_context_with_missing_inverse_metadata_blocks_recovery(inverse):
    intent = make_intent()
    plan = await Planner(inverse.registry, inverse.fetcher, inverse.exchanges).plan(intent)
    legacy = asdict(plan.legs[0])
    for key in ("planned_qty_native", "position_before_qty_native", "position_entry_price", "position_effect"):
        legacy.pop(key)
    legacy["instrument"].update(is_inverse=False, contract_size=1)
    legacy["instrument"].pop("quantity_unit")
    legacy["instrument"].pop("settlement_asset")
    await inverse.store.create_intent(intent, status="EXECUTING")
    leg_id = await inverse.store.create_leg(
        intent_id=intent.intent_id,
        venue="binance",
        instrument_venue_symbol=inverse.instrument.venue_symbol,
        instrument_base="BTC",
        instrument_quote="USD",
        instrument_market_type="perp",
    )
    await inverse.store.update_leg(
        leg_id, status="UNKNOWN", execution_context_json=json.dumps(legacy, default=lambda item: item.value)
    )

    result = await inverse.orchestrator.recover(intent.intent_id)

    assert result["status"] == BLOCKING_STATE
    assert "legacy contract quantity" in result["reason"]
    assert not inverse.exchange.create_order_calls
    assert not inverse.exchange.cancel_order_calls


@pytest.mark.asyncio
async def test_smoke_roundtrip_remains_recoverable_until_compensation_is_durable(inverse, monkeypatch):
    transitions = []
    update_status = inverse.store.update_intent_status

    async def record_status(intent_id, status):
        transitions.append(status)
        await update_status(intent_id, status)

    monkeypatch.setattr(inverse.store, "update_intent_status", record_status)

    result = await inverse.orchestrator.smoke_roundtrip(inverse.instrument, 200)

    assert result["status"] == "CLOSED", result
    assert result["orders_sent"] is True
    assert "ALL_FILLED" not in transitions
    assert (await inverse.store.get_intent(result["intent_id"])).status == "ROLLED_BACK"
    assert (await inverse.exchange.fetch_order_position(inverse.instrument)).qty_native == 0
    rows = await inverse.store.get_legs_for_intent(result["intent_id"])
    orders = await inverse.store.get_orders_for_leg(rows[0].leg_id)
    assert [row.purpose for row in orders] == ["original", "compensation"]
    assert Decimal(rows[0].filled_qty_native) == Decimal(rows[0].compensation_filled_qty_native) == 2
    assert len(await inverse.store.get_order_fills(intent_id=result["intent_id"])) == 2


@pytest.mark.asyncio
async def test_unknown_fee_valuation_blocks_new_exposure_but_allows_reduce_only_close(inverse):
    inverse.orchestrator = Orchestrator(
        inverse.registry,
        inverse.fetcher,
        inverse.exchanges,
        inverse.store,
        use_websocket=False,
        risk_validator=RiskValidator(inverse.store, {"daily_loss_limit_usd": 100}, exchanges=inverse.exchanges),
    )
    inject_fill(inverse, 10, fee_currency="BNB")
    opened = await inverse.orchestrator.submit(make_intent())
    assert opened["status"] == "ALL_FILLED", opened
    assert await inverse.store.has_incomplete_order_accounting()

    rejected = await inverse.orchestrator.submit(make_intent(intent_id="blocked-new-open"))

    assert rejected["status"] == "REJECTED"
    assert any("PnL is incomplete" in reason for reason in rejected["risk_failures"])
    assert len(inverse.exchange.create_order_calls) == 1
    inject_fill(inverse, 2)
    closed = await inverse.orchestrator.submit(
        make_intent(
            intent_id="allowed-close",
            position_effect="close",
            side="sell",
            total_notional_usd=200,
            quantity_native=2,
        )
    )
    assert closed["status"] == "ALL_FILLED", closed
    assert (await inverse.exchange.fetch_order_position(inverse.instrument)).qty_native == 8


@pytest.mark.asyncio
async def test_spot_plan_counts_current_inverse_contracts_without_counting_closed_history(inverse):
    inject_fill(inverse, 10)
    assert (await inverse.orchestrator.submit(make_intent()))["status"] == "ALL_FILLED"
    inject_fill(inverse, 8)
    closed = await inverse.orchestrator.submit(
        make_intent(
            intent_id="reduce-before-spot",
            position_effect="close",
            side="sell",
            total_notional_usd=800,
            quantity_native=8,
        )
    )
    assert closed["status"] == "ALL_FILLED", closed
    spot = replace(
        inverse.instrument,
        market_type="spot",
        venue_symbol="BTC/USDT",
        quote=Asset("USDT"),
        settlement_asset=None,
        is_inverse=False,
        contract_size=1,
        quantity_unit="base",
        qty_step=0.00001,
        min_qty=0.00001,
    )
    inverse.exchange.set_markets([inverse.instrument, spot])
    inverse.exchange.set_balance("USDT", 10000)
    inverse.registry.add(spot)
    set_book(inverse.exchange, spot)
    inverse.orchestrator = Orchestrator(
        inverse.registry,
        inverse.fetcher,
        inverse.exchanges,
        inverse.store,
        use_websocket=False,
        risk_validator=RiskValidator(inverse.store, {"max_venue_exposure_usd": 350}, exchanges=inverse.exchanges),
    )
    spot_intent = make_intent(
        intent_id="spot-preview-two-contracts",
        product="spot",
        quote_preference=["USDT"],
        contract_type=None,
        settlement_asset=None,
        total_notional_usd=100,
    )

    allowed = await inverse.orchestrator.submit(spot_intent, dry_run=True)

    assert allowed["risk_failures"] == []  # Current 2 * $100 + new $100; historical opens/closes excluded.
    inverse.exchange.set_position(inverse.instrument.venue_symbol, 4, entry_price=10000)
    rejected = await inverse.orchestrator.submit(
        replace(spot_intent, intent_id="spot-preview-four-contracts"), dry_run=True
    )
    assert any("exposure" in reason for reason in rejected["risk_failures"])
    assert len(inverse.exchange.create_order_calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["nan_quantity", "stale_timestamp"])
async def test_invalid_position_refresh_cannot_acknowledge_blocked_close(inverse, monkeypatch, invalid):
    inverse.exchange.set_position(inverse.instrument.venue_symbol, 10)
    inject_fill(inverse, 2, status="canceled")
    result = await inverse.orchestrator.submit(
        make_intent(position_effect="close", side="sell", total_notional_usd=400, quantity_native=4)
    )
    assert result["status"] == BLOCKING_STATE, result
    fetch_position = inverse.exchange.fetch_order_position

    async def invalid_position(instrument):
        position = await fetch_position(instrument)
        return (
            replace(position, qty_native=float("nan")) if invalid == "nan_quantity" else replace(position, timestamp=0)
        )

    monkeypatch.setattr(inverse.exchange, "fetch_order_position", invalid_position)
    before = (len(inverse.exchange.create_order_calls), len(inverse.exchange.cancel_order_calls))

    refreshed = await inverse.orchestrator.refresh_status(result["intent_id"])

    assert refreshed["legs"][0]["position_check_error"]
    assert refreshed["orders_sent"] is False
    with pytest.raises(ValueError, match="orders or positions are unresolved"):
        await inverse.orchestrator.acknowledge(result["intent_id"])
    assert (await inverse.store.get_intent(result["intent_id"])).status == BLOCKING_STATE
    assert before == (len(inverse.exchange.create_order_calls), len(inverse.exchange.cancel_order_calls))


@pytest.mark.asyncio
async def test_zero_fill_with_external_position_drift_blocks_without_compensation(inverse, monkeypatch):
    inverse.exchange.set_position(inverse.instrument.venue_symbol, 3)
    inject_fill(inverse, 0, status="canceled")
    create_order = inverse.exchange.create_order

    async def drift_after_rejection(*args, **kwargs):
        response = await create_order(*args, **kwargs)
        inverse.exchange.set_position(inverse.instrument.venue_symbol, 4)
        return response

    monkeypatch.setattr(inverse.exchange, "create_order", drift_after_rejection)

    result = await inverse.orchestrator.submit(make_intent())

    assert result["status"] == BLOCKING_STATE, result
    assert len(inverse.exchange.create_order_calls) == 1
    assert (await inverse.exchange.fetch_order_position(inverse.instrument)).qty_native == 4
    rows = await inverse.store.get_legs_for_intent(result["intent_id"])
    assert Decimal(rows[0].filled_qty_native) == 0
    assert "position_drift" in rows[0].error_msg
    assert not await inverse.store.get_orders_for_leg(rows[0].leg_id, "compensation")


@pytest.mark.asyncio
@pytest.mark.parametrize("violation", ["protected_price", "fee_budget"])
async def test_fully_filled_close_with_protection_violation_stays_blocked_without_reopening(inverse, violation):
    inverse.exchange.set_position(inverse.instrument.venue_symbol, 2)
    inverse.exchange.inject_next_order_result(
        inverse.instrument.venue_symbol,
        {
            "id": "close-protection-violation",
            "status": "closed",
            "filled": 2,
            "average": 49000 if violation == "protected_price" else 50000,
            "fee": {"currency": "BTC", "cost": 0.0001 if violation == "fee_budget" else 0.000001},
        },
    )

    result = await inverse.orchestrator.submit(
        make_intent(
            position_effect="close",
            side="sell",
            total_notional_usd=200,
            quantity_native=2,
            max_fee_usd=1,
        )
    )

    assert result["status"] == BLOCKING_STATE, result
    assert (await inverse.exchange.fetch_order_position(inverse.instrument)).qty_native == 0
    assert Decimal(result["legs"][0]["filled_qty_native"]) == 2
    expected_error = "protected price" if violation == "protected_price" else "exceeds max_fee_usd"
    assert expected_error in result["legs"][0]["error"]
    assert len(inverse.exchange.create_order_calls) == 1
    row = (await inverse.store.get_legs_for_intent(result["intent_id"]))[0]
    assert not await inverse.store.get_orders_for_leg(row.leg_id, "compensation")


@pytest.mark.asyncio
async def test_preexisting_open_order_prevents_new_perpetual_execution(inverse):
    existing = await inverse.exchange.create_order(
        inverse.instrument.venue_symbol, "limit", "buy", 1, 49000, {"clientOrderId": "external-existing"}
    )

    result = await inverse.orchestrator.submit(make_intent())

    assert result["status"] == "REJECTED", result
    assert len(inverse.exchange.create_order_calls) == 1
    assert not inverse.exchange.set_leverage_calls
    assert not inverse.exchange.cancel_order_calls
    assert inverse.exchange.get_order(existing["id"])["status"] == "open"
    assert not await inverse.store.get_legs_for_intent(result["intent_id"])


@pytest.mark.asyncio
async def test_invalid_later_leg_prevents_earlier_leverage_change(inverse, monkeypatch):
    other_instrument = replace(inverse.instrument, venue="other")
    other = MockExchange("other")
    other.set_markets([other_instrument])
    other.set_position(other_instrument.venue_symbol, 0)
    other.set_balance("BTC", 10)
    set_book(other, other_instrument)
    inverse.exchanges["other"] = other
    inverse.registry.add(other_instrument)
    monkeypatch.setattr(other, "order_capabilities", lambda instrument: OrderCapabilities(True, ("GTC",), True))

    result = await inverse.orchestrator.submit(make_intent(split={"binance": 0.5, "other": 0.5}, leverage=3))

    assert result["status"] == "REJECTED", result
    assert not inverse.exchange.set_leverage_calls
    assert not other.set_leverage_calls
    assert not inverse.exchange.create_order_calls
    assert not other.create_order_calls
    assert not await inverse.store.get_legs_for_intent(result["intent_id"])
