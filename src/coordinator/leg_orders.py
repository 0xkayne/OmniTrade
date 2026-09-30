"""Durable order lifecycle used for both original and compensation orders."""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from ccxt.base.errors import AuthenticationError, InsufficientFunds, InvalidOrder, OrderNotFound, PermissionDenied

from src.exchange.base import BaseExchange
from src.exchange.order import OrderRequest, OrderSnapshot, parse_order_snapshot
from src.market.instrument import Instrument
from src.observability.metrics import MetricsEmitter, NoopMetrics
from src.persistence.store import OrderFillRow, OrderRow, PersistenceStore

from .leg_context import get_leg_fill_qty

logger = logging.getLogger(__name__)


class LegOrderManager:
    """Persist requests before sending and query ambiguous sends without resubmitting."""

    def __init__(
        self,
        exchange: BaseExchange,
        store: PersistenceStore,
        instrument: Instrument,
        poll_interval_ms: int = 500,
        use_websocket: bool = True,
        metrics: MetricsEmitter | None = None,
    ):
        self.exchange = exchange
        self.store = store
        self.instrument = instrument
        self.poll_interval_seconds = max(0.01, poll_interval_ms / 1000)
        self.use_websocket = use_websocket
        self.metrics = metrics or NoopMetrics()
        self.submit_ms = 0.0
        self.poll_total_ms = 0.0
        self.poll_attempts = 0

    @staticmethod
    def client_order_id(leg_id: str, purpose: str, index: int) -> str:
        digest = hashlib.sha256(f"{leg_id}:{purpose}:{index}".encode()).hexdigest()[:32]
        return f"0x{digest}"

    @staticmethod
    def request_from_json(raw: str) -> OrderRequest:
        data = json.loads(raw)
        if data.pop("schema_version", 1) not in (1, 2):
            raise ValueError("unsupported persisted order request version")
        data["amount"] = float(data["amount"])
        return OrderRequest(**data)

    @staticmethod
    def snapshot_from_json(raw: str) -> OrderSnapshot:
        data = json.loads(raw)
        if data.pop("schema_version", 1) not in (1, 2):
            raise ValueError("unsupported persisted order snapshot version")
        for key in ("filled_qty_native", "filled_notional_quote"):
            if data.get(key) is not None:
                data[key] = float(data[key])
        return OrderSnapshot(**data)

    async def execute(
        self, request: OrderRequest, leg_id: str, intent_id: str, purpose: str, deadline: float
    ) -> OrderSnapshot:
        request_data = asdict(request)
        request_data.update(schema_version=2, amount=str(Decimal(str(request.amount))))
        request_json = json.dumps(request_data, sort_keys=True)
        is_new = await self.store.create_order_row(request.client_order_id, leg_id, intent_id, purpose, request_json)
        row = await self.store.get_order_row(request.client_order_id)
        if self.request_from_json(row.request_json) != request:
            raise ValueError(f"{intent_id}/{leg_id}: order identity reused with different parameters")
        if not is_new:
            return await self.confirm(row, deadline)
        if deadline <= time.monotonic():
            snapshot = OrderSnapshot(None, "rejected", 0.0, None)
            await self._record(row, snapshot, "execution deadline elapsed before send")
            return snapshot

        # UNKNOWN is durable before network I/O: a crash must never permit a resend.
        await self.store.update_order_row(request.client_order_id, "UNKNOWN")
        await self.store.append_event(
            intent_id,
            "order_sending",
            {
                "leg_id": leg_id,
                "client_order_id": request.client_order_id,
                "request": asdict(request),
            },
        )
        started = time.monotonic()
        if started >= deadline or (request.expires_at is not None and time.time() >= request.expires_at):
            snapshot = OrderSnapshot(None, "rejected", 0.0, None)
            await self._record(row, snapshot, "quote or execution deadline expired during persistence")
            return snapshot
        received_ack = False
        try:
            snapshot = await asyncio.wait_for(
                self.exchange.submit_order(request, self.instrument), max(0.001, deadline - time.monotonic())
            )
            received_ack = True
            await self._record(row, snapshot)
        except (AuthenticationError, PermissionDenied, InsufficientFunds, InvalidOrder) as exc:
            if received_ack or isinstance(exc, OrderNotFound):
                await self._record_error(row, exc)
            else:
                snapshot = OrderSnapshot(None, "rejected", 0.0, None)
                await self._record(row, snapshot, f"{type(exc).__name__}: order rejected")
                return snapshot
        except Exception as exc:
            await self._record_error(row, exc)
        finally:
            self.submit_ms += (time.monotonic() - started) * 1000
            self.metrics.histogram(
                "order.submit_ms", (time.monotonic() - started) * 1000, {"venue": self.instrument.venue}
            )
        row = await self.store.get_order_row(request.client_order_id)
        return await self.confirm(row, deadline)

    async def _record_error(self, row: OrderRow, exc: Exception) -> None:
        message = (
            f"{row.intent_id}/{row.leg_id}/{self.instrument.venue}: order {row.client_order_id} {type(exc).__name__}"
        )
        previous = await self.store.get_order_row(row.client_order_id)
        if previous.error_msg != message:
            logger.warning(message)
        await self.store.update_order_row(row.client_order_id, "UNKNOWN", error_msg=message)
        self.metrics.increment("order.unknown", tags={"venue": self.instrument.venue})

    async def _record(self, row: OrderRow, snapshot: OrderSnapshot, error: str | None = None) -> None:
        previous = await self.store.get_order_row(row.client_order_id)
        if previous.snapshot_json:
            old = self.snapshot_from_json(previous.snapshot_json)
            old_qty = get_leg_fill_qty(old, self.instrument)
            observed_qty = get_leg_fill_qty(snapshot, self.instrument)
            if (
                old_qty is not None
                and old_qty > 0
                and (observed_qty is None or Decimal(str(observed_qty)) < Decimal(str(old_qty)))
            ):
                raise ValueError(f"{row.leg_id}: cumulative filled quantity regressed")
        data = asdict(snapshot)
        data["schema_version"] = 2
        for key in ("filled_qty_native", "filled_notional_quote"):
            if data[key] is not None:
                data[key] = str(Decimal(str(data[key])))
        await self._record_fills(row, snapshot)
        await self.store.update_order_row(row.client_order_id, snapshot.status, json.dumps(data), error)
        await self.store.append_event(
            row.intent_id,
            "order_observed",
            {
                "leg_id": row.leg_id,
                "client_order_id": row.client_order_id,
                "snapshot": asdict(snapshot),
            },
        )

    def _is_confirmed(self, snapshot: OrderSnapshot) -> bool:
        native = get_leg_fill_qty(snapshot, self.instrument)
        return (
            snapshot.is_terminal
            and native is not None
            and (native == 0 or (snapshot.avg_price is not None and snapshot.filled_qty_base is not None))
        )

    async def _record_fills(self, row: OrderRow, snapshot: OrderSnapshot) -> None:
        request = self.request_from_json(row.request_json)
        inst = self.instrument
        family = request.account_family or (
            "spot" if inst.market_type == "spot" else ("coinm" if inst.is_inverse else "usdm")
        )
        if inst.venue != "binance":
            family = inst.market_type
        for fill in snapshot.fills:
            if fill.get("id") is None or fill.get("timestamp") is None:
                continue  # Never invent a trade identity or execution time.
            qty, price = float(fill["amount"]), float(fill["price"])
            timestamp = datetime.fromtimestamp(float(fill["timestamp"]) / 1000, timezone.utc).isoformat()
            fees = fill.get("fees")
            fee_usd = 0.0 if fees else None
            for fee in fees or []:
                currency, cost = fee.get("currency"), fee.get("cost")
                rate = 1.0 if currency in ("USD", "USDT", "USDC") else price if currency == inst.base.symbol else None
                if cost is None or rate is None:
                    fee_usd = None
                    break
                fee_usd += float(cost) * rate
            settlement = inst.settlement_asset or inst.quote
            realized = fill.get("realized_pnl")
            # Perpetual open validation excludes reducing/reversing an existing position.
            if (
                realized is None
                and row.purpose == "original"
                and not request.is_reduce_only
                and (
                    (inst.market_type == "perp" and self.exchange.order_capabilities(inst).has_position_validation)
                    or (inst.market_type == "spot" and request.side == "buy")
                )
            ):
                realized = 0.0
            pnl_currency = fill.get("realized_pnl_currency") or settlement.symbol
            valuation = (
                1.0 if pnl_currency in ("USD", "USDT", "USDC") else price if pnl_currency == inst.base.symbol else None
            )
            await self.store.upsert_order_fill(
                OrderFillRow(
                    network=inst.network.value,
                    product_family=family,
                    venue=inst.venue,
                    symbol=inst.venue_symbol,
                    trade_id=str(fill["id"]),
                    client_order_id=row.client_order_id,
                    leg_id=row.leg_id,
                    intent_id=row.intent_id,
                    qty_native=str(Decimal(str(qty))),
                    price=str(Decimal(str(price))),
                    qty_base=str(Decimal(str(inst.base_equivalent(qty, price)))),
                    notional_quote=str(Decimal(str(inst.quote_notional(qty, price)))),
                    exchange_timestamp=timestamp,
                    side=request.side,
                    settlement_asset=pnl_currency,
                    fees_json=json.dumps(fees or []),
                    fee_usd=str(fee_usd) if fee_usd is not None else None,
                    realized_pnl_settlement=str(realized) if realized is not None else None,
                    realized_pnl_usd=str(float(realized) * valuation)
                    if realized is not None and valuation is not None
                    else None,
                    valuation_price=str(valuation) if valuation is not None else None,
                    valuation_timestamp=timestamp if valuation is not None else None,
                )
            )

    async def confirm(self, row: OrderRow, deadline: float, *, use_websocket: bool | None = None) -> OrderSnapshot:
        if row.status == "PENDING_SEND":
            snapshot = OrderSnapshot(None, "rejected", 0.0, None)
            await self._record(row, snapshot, "reserved order was never sent")
            return snapshot
        request = self.request_from_json(row.request_json)
        snapshot = (
            self.snapshot_from_json(row.snapshot_json)
            if row.snapshot_json
            else OrderSnapshot(None, "unknown", None, None)
        )
        if row.status != "UNKNOWN" and self._is_confirmed(snapshot):
            return snapshot
        can_watch = self.use_websocket if use_websocket is None else use_websocket
        if can_watch and snapshot.order_id and time.monotonic() < deadline:
            try:
                updates = await asyncio.wait_for(
                    self.exchange.watch_orders(request.symbol, params=self.exchange.account_params(self.instrument)),
                    min(0.2, max(0.001, deadline - time.monotonic())),
                )
                for update in updates if isinstance(updates, list) else [updates]:
                    if str(update.get("id")) == snapshot.order_id:
                        observed = parse_order_snapshot(update, self.instrument)
                        await self._record(row, observed)
                        snapshot = observed
            except Exception as exc:
                logger.info(
                    "%s/%s: WS confirmation fallback (%s)", row.leg_id, self.instrument.venue, type(exc).__name__
                )
        backoff = 0.05
        # REST verifies terminal WS events and supplies complete cumulative fields.
        while time.monotonic() < deadline:
            poll_started = time.monotonic()
            try:
                observed = await asyncio.wait_for(
                    self.exchange.fetch_order_snapshot(request, self.instrument, snapshot.order_id),
                    min(2.0, max(0.001, deadline - time.monotonic())),
                )
                await self._record(row, observed)
                snapshot = observed
                if self._is_confirmed(snapshot):
                    return snapshot
            except Exception as exc:
                await self._record_error(row, exc)
            finally:
                self.poll_total_ms += (time.monotonic() - poll_started) * 1000
                self.poll_attempts += 1
            await asyncio.sleep(min(backoff, max(0, deadline - time.monotonic())))
            backoff = min(backoff * 2, self.poll_interval_seconds)
        return OrderSnapshot(
            snapshot.order_id,
            "unknown",
            snapshot.filled_qty_base,
            snapshot.avg_price,
            snapshot.fee_usd,
            snapshot.fees,
            snapshot.fills,
            filled_qty_native=snapshot.filled_qty_native,
            filled_notional_quote=snapshot.filled_notional_quote,
        )

    async def cancel(self, row: OrderRow, deadline: float) -> OrderSnapshot:
        """Cancel, then confirm the final cumulative fill, including cancel/fill races."""
        if row.status == "PENDING_SEND":
            return await self.confirm(row, deadline, use_websocket=False)
        request = self.request_from_json(row.request_json)
        snapshot = (
            self.snapshot_from_json(row.snapshot_json)
            if row.snapshot_json
            else OrderSnapshot(None, "unknown", None, None)
        )
        if row.status != "UNKNOWN" and self._is_confirmed(snapshot):
            return snapshot
        try:
            if not snapshot.order_id:
                snapshot = await asyncio.wait_for(
                    self.exchange.fetch_order_snapshot(request, self.instrument),
                    max(0.001, deadline - time.monotonic()),
                )
                await self._record(row, snapshot)
            if snapshot.order_id and not snapshot.is_terminal:
                await asyncio.wait_for(
                    self.exchange.cancel_order(
                        snapshot.order_id, request.symbol, self.exchange.account_params(self.instrument)
                    ),
                    max(0.001, deadline - time.monotonic()),
                )
        except Exception as exc:
            await self._record_error(row, exc)
        row = await self.store.get_order_row(row.client_order_id)
        return await self.confirm(row, deadline, use_websocket=False)
