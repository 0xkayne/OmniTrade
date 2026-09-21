"""Durable order lifecycle used for both original and compensation orders."""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import asdict

from ccxt.base.errors import AuthenticationError, InsufficientFunds, InvalidOrder, OrderNotFound, PermissionDenied

from src.exchange.account_type import account_type_params
from src.exchange.base import BaseExchange
from src.exchange.order import OrderRequest, OrderSnapshot, parse_order_snapshot
from src.market.instrument import Instrument
from src.observability.metrics import MetricsEmitter, NoopMetrics
from src.persistence.store import OrderRow, PersistenceStore

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

    async def execute(
        self, request: OrderRequest, leg_id: str, intent_id: str, purpose: str, deadline: float
    ) -> OrderSnapshot:
        request_json = json.dumps(asdict(request), sort_keys=True)
        is_new = await self.store.create_order_row(request.client_order_id, leg_id, intent_id, purpose, request_json)
        row = await self.store.get_order_row(request.client_order_id)
        if row.request_json != request_json:
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
        try:
            snapshot = await asyncio.wait_for(
                self.exchange.submit_order(request, self.instrument), max(0.001, deadline - time.monotonic())
            )
            await self._record(row, snapshot)
        except (AuthenticationError, PermissionDenied, InsufficientFunds, InvalidOrder) as exc:
            if isinstance(exc, OrderNotFound):
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
            old = OrderSnapshot(**json.loads(previous.snapshot_json))
            if (snapshot.filled_qty_base or 0) < (old.filled_qty_base or 0):
                raise ValueError(f"{row.leg_id}: cumulative filled quantity regressed")
        await self.store.update_order_row(row.client_order_id, snapshot.status, json.dumps(asdict(snapshot)), error)
        await self.store.append_event(
            row.intent_id,
            "order_observed",
            {
                "leg_id": row.leg_id,
                "client_order_id": row.client_order_id,
                "snapshot": asdict(snapshot),
            },
        )

    @staticmethod
    def _is_confirmed(snapshot: OrderSnapshot) -> bool:
        return (
            snapshot.is_terminal
            and snapshot.filled_qty_base is not None
            and (snapshot.filled_qty_base == 0 or snapshot.avg_price is not None)
        )

    async def confirm(self, row: OrderRow, deadline: float, *, use_websocket: bool | None = None) -> OrderSnapshot:
        if row.status == "PENDING_SEND":
            snapshot = OrderSnapshot(None, "rejected", 0.0, None)
            await self._record(row, snapshot, "reserved order was never sent")
            return snapshot
        request = OrderRequest(**json.loads(row.request_json))
        snapshot = (
            OrderSnapshot(**json.loads(row.snapshot_json))
            if row.snapshot_json
            else OrderSnapshot(None, "unknown", None, None)
        )
        if row.status != "UNKNOWN" and self._is_confirmed(snapshot):
            return snapshot
        can_watch = self.use_websocket if use_websocket is None else use_websocket
        if can_watch and snapshot.order_id and time.monotonic() < deadline:
            try:
                updates = await asyncio.wait_for(
                    self.exchange.watch_orders(request.symbol, params=account_type_params(request.product)),
                    min(0.2, max(0.001, deadline - time.monotonic())),
                )
                for update in updates if isinstance(updates, list) else [updates]:
                    if str(update.get("id")) == snapshot.order_id:
                        observed = parse_order_snapshot(
                            update, self.instrument.base.symbol, self.instrument.quote.symbol
                        )
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
        )

    async def cancel(self, row: OrderRow, deadline: float) -> OrderSnapshot:
        """Cancel, then confirm the final cumulative fill, including cancel/fill races."""
        if row.status == "PENDING_SEND":
            return await self.confirm(row, deadline, use_websocket=False)
        request = OrderRequest(**json.loads(row.request_json))
        snapshot = (
            OrderSnapshot(**json.loads(row.snapshot_json))
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
                    self.exchange.cancel_order(snapshot.order_id, request.symbol, account_type_params(request.product)),
                    max(0.001, deadline - time.monotonic()),
                )
        except Exception as exc:
            await self._record_error(row, exc)
        row = await self.store.get_order_row(row.client_order_id)
        return await self.confirm(row, deadline, use_websocket=False)
