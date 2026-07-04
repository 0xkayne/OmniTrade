"""Tests for realized daily PnL risk accounting."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from src.persistence.store import PersistenceStore


@dataclass
class FakeIntent:
    intent_id: str
    side: str = "buy"
    split: dict | None = None

    def __post_init__(self):
        if self.split is None:
            self.split = {"binance": 1.0}


@pytest.fixture
async def store(tmp_path):
    s = PersistenceStore(Path(":memory:"), tmp_path / "logs")
    await s.initialize()
    yield s
    await s.close()


async def create_filled_leg(store: PersistenceStore, *, intent_id: str = "intent-001", side: str = "buy") -> str:
    await store.create_intent(FakeIntent(intent_id=intent_id, side=side))
    leg_id = await store.create_leg(
        intent_id=intent_id,
        venue="binance",
        instrument_venue_symbol="BTCUSDT",
        instrument_base="BTC",
        instrument_quote="USDT",
        instrument_market_type="spot",
        planned_notional_usd=10_000.0,
        planned_qty_base=0.2,
    )
    await store.update_leg(
        leg_id,
        status="FILLED",
        filled_amount=0.2,
        avg_price=50_000.0,
        fee_usd=0.0,
    )
    return leg_id


@pytest.mark.asyncio
async def test_daily_pnl_does_not_treat_filled_buy_notional_as_loss(store):
    await create_filled_leg(store, side="buy")

    assert await store.get_daily_pnl() is None


@pytest.mark.asyncio
async def test_daily_pnl_uses_compensated_realized_pnl_and_fees(store):
    leg_id = await create_filled_leg(store, side="buy")
    await store.update_leg(
        leg_id,
        status="COMPENSATED",
        fee_usd=1.0,
        compensation_filled_amount=0.2,
        compensation_avg_price=49_900.0,
        compensation_fee_usd=2.0,
    )

    assert await store.get_daily_pnl() == pytest.approx(-23.0)
