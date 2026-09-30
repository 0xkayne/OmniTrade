"""Versioned execution facts and position checks owned by the coordinator."""

import asyncio
import json
import math
import time
from dataclasses import asdict
from decimal import Decimal

from src.exchange.base import BaseExchange
from src.exchange.order import OrderPositionSnapshot, OrderSnapshot
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.market.quote import EstimatedFill

from .plan import PlannedLeg


def serialize_leg_context(leg: PlannedLeg) -> str:
    data = asdict(leg)
    for key in ("planned_qty_native", "position_before_qty_native"):
        if data[key] is not None:
            data[key] = str(Decimal(str(data[key])))
    return json.dumps({"schema_version": 2, "leg": data}, default=lambda value: value.value)


def deserialize_leg_context(raw: str) -> PlannedLeg:
    context = json.loads(raw)
    version = context.get("schema_version", 1)
    if version not in (1, 2):
        raise ValueError(f"unsupported leg execution context version {version}")
    data = context["leg"] if version == 2 else context
    instrument = dict(data["instrument"])
    instrument["base"] = Asset(**instrument["base"])
    instrument["quote"] = Asset(**instrument["quote"])
    if instrument.get("settlement_asset"):
        instrument["settlement_asset"] = Asset(**instrument["settlement_asset"])
    instrument["network"] = NetworkType(instrument["network"])
    data["instrument"] = Instrument(**instrument)
    if version == 1 and (data["instrument"].is_inverse or data["instrument"].contract_size != 1):
        raise ValueError("legacy contract lacks reliable native quantity facts")
    data["estimated_fill"] = EstimatedFill(**data["estimated_fill"])
    for key in ("planned_qty_native", "position_before_qty_native"):
        if data.get(key) is not None:
            data[key] = float(data[key])
    return PlannedLeg(**data)


def get_leg_fill_qty(snapshot: OrderSnapshot, instrument: Instrument) -> float | None:
    if snapshot.filled_qty_native is not None:
        return snapshot.filled_qty_native
    # A local, never-sent rejection has an exact zero even without venue metadata.
    if snapshot.filled_qty_base == 0:
        return 0.0
    if not instrument.is_inverse and instrument.contract_size == 1:
        return snapshot.filled_qty_base
    return None


async def validate_leg_position(
    exchange: BaseExchange, leg: PlannedLeg, *, expected_qty_native: float | None = None
) -> OrderPositionSnapshot | None:
    """Read positions without changing account settings or submitting orders."""
    instrument = leg.instrument
    if instrument.market_type != "perp":
        return None
    if not exchange.order_capabilities(instrument).has_position_validation:
        if leg.position_effect == "close" or instrument.is_inverse:
            raise ValueError(f"{leg.venue}: contract execution requires position validation")
        return None
    baseline = expected_qty_native if expected_qty_native is not None else leg.position_before_qty_native
    for attempt in range(3):
        position = await asyncio.wait_for(exchange.fetch_order_position(instrument), 2.0)
        if (
            position.symbol != instrument.venue_symbol
            or not all(
                value is not None and math.isfinite(value)
                for value in (position.qty_native, position.timestamp, position.mark_price)
            )
            or position.mark_price <= 0
        ):
            raise ValueError(f"{leg.venue}: invalid position snapshot")
        if abs(time.time() - position.timestamp) > 10:
            raise ValueError(f"{leg.venue}: stale position snapshot")
        if baseline is None or abs(Decimal(str(position.qty_native)) - Decimal(str(baseline))) <= Decimal("1e-12"):
            break
        if attempt == 2:
            raise ValueError(f"{leg.venue}: position_drift; expected {baseline}, observed {position.qty_native}")
        await asyncio.sleep(0.1 * (attempt + 1))
    if expected_qty_native is not None:
        return position
    direction = 1 if leg.side == "buy" else -1
    if leg.position_effect == "close":
        if direction * position.qty_native >= 0 or leg.native_qty > abs(position.qty_native) + 1e-12:
            raise ValueError(f"{leg.venue}: close must reduce an existing position without exceeding it")
    else:
        if direction * position.qty_native < 0:
            raise ValueError(f"{leg.venue}: open cannot reduce or reverse an existing position")
        if position.qty_native and position.leverage != leg.leverage:
            raise ValueError(f"{leg.venue}: existing position leverage differs from requested leverage")
    leg.position_before_qty_native = position.qty_native
    leg.position_entry_price = position.entry_price
    return position
