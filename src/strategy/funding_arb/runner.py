"""AutoArbRunner — continuous funding-rate arbitrage daemon.

Ties together FundingRateMonitor (scan), FundingRateComparator (signal),
HedgedPositionManager (track), and Orchestrator (execute) into a single
long-running loop: scan → decide → execute → repeat.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.strategy.funding_arb.comparator import FundingSpread
    from src.strategy.funding_arb.monitor import FundingRateMonitor
    from src.strategy.funding_arb.position_manager import HedgedPosition, HedgedPositionManager

logger = logging.getLogger(__name__)


@dataclass
class ArbConfig:
    """Tunable parameters for the auto arb runner."""

    min_spread_pct: float = 0.01  # minimum abs spread to open (percentage, e.g. 0.01 = 0.01%)
    exit_spread_pct: float = 0.001  # spread below which to close (percentage)
    notional_per_leg: float = 1000.0  # USD notional per leg
    max_positions: int = 5  # max concurrent hedged positions
    interval_seconds: int = 60  # seconds between scans
    dry_run: bool = False  # if True, only log decisions — never send orders


class AutoArbRunner:
    """Continuous automated funding rate arbitrage.

    Usage::

        runner = AutoArbRunner(monitor, position_manager, submit_intent, config)
        await runner.run()
    """

    def __init__(
        self,
        monitor: FundingRateMonitor,
        position_manager: HedgedPositionManager,
        submit_intent,  # async callable: (Intent) -> dict
        config: ArbConfig | None = None,
        base_filter: list[str] | None = None,
    ):
        self._monitor = monitor
        self._pm = position_manager
        self._submit_intent = submit_intent
        self._cfg = config or ArbConfig()
        self._base_filter = base_filter
        self._scan_count = 0

    # ── main loop ────────────────────────────────────────────

    async def run(self) -> None:
        """Start the continuous arbitrage loop.  Ctrl+C to stop."""
        self._log_startup()
        try:
            while True:
                await self._tick()
                await asyncio.sleep(self._cfg.interval_seconds)
        except asyncio.CancelledError:
            logger.info("AutoArb daemon stopped.")

    async def _tick(self) -> None:
        self._scan_count += 1
        t0 = time.perf_counter()
        try:
            spreads = await self._monitor.scan_once(base_filter=self._base_filter)
        except Exception:
            logger.warning("Scan #%d failed", self._scan_count, exc_info=True)
            return

        if not spreads:
            return

        open_positions = await self._pm.get_open_positions()
        open_bases = {p.pair.base for p in open_positions}

        logger.info(
            "Scan #%d: %d pair(s), %d signal(s), %d open position(s)",
            self._scan_count,
            len(spreads),
            sum(1 for s in spreads if s.signal != "none"),
            len(open_positions),
        )

        # Phase 1 — check existing positions for exit
        for pos in open_positions:
            matching = _find_spread(pos, spreads)
            if self._should_close(pos, matching):
                await self._close_position(pos)

        # Phase 2 — look for new entries
        for spread in spreads:
            if spread.signal == "none":
                continue
            if spread.pair.base in open_bases:
                continue  # already have a position on this base
            if len(open_positions) >= self._cfg.max_positions:
                break
            if abs(spread.spread or 0) * 100 < self._cfg.min_spread_pct:
                continue
            if self._should_open(spread):
                await self._open_position(spread)
                open_bases.add(spread.pair.base)
                open_positions = await self._pm.get_open_positions()

        elapsed_ms = (time.perf_counter() - t0) * 1000
        logger.debug("Tick #%d completed in %.0f ms", self._scan_count, elapsed_ms)

    # ── decision logic ────────────────────────────────────────

    def _should_open(self, spread: FundingSpread) -> bool:
        if spread.signal == "none":
            return False
        return abs(spread.spread or 0) * 100 >= self._cfg.min_spread_pct

    def _should_close(self, pos: HedgedPosition, spread: FundingSpread | None) -> bool:
        if spread is None:
            logger.info("[%s] pair no longer tradable — closing", pos.position_id)
            return True
        abs_spread = abs(spread.spread or 0) * 100
        if abs_spread < self._cfg.exit_spread_pct:
            logger.info(
                "[%s] spread %.4f%% < exit %.4f%% — closing", pos.position_id, abs_spread, self._cfg.exit_spread_pct
            )
            return True
        return False

    # ── execution ─────────────────────────────────────────────

    async def _open_position(self, spread: FundingSpread) -> None:
        """Open a delta-neutral hedged position."""
        from src.coordinator.intent import Intent, LegConfig

        pair = spread.pair
        long_venue, short_venue = pair.venue_a, pair.venue_b
        if spread.signal == "open_short_a_long_b":
            long_venue, short_venue = short_venue, long_venue

        logger.info(
            "%s: spread=%.4f%% → OPENING long %s / short %s ($%.0f/leg)",
            pair.base,
            (spread.spread or 0) * 100,
            long_venue,
            short_venue,
            self._cfg.notional_per_leg,
        )

        if self._cfg.dry_run:
            logger.info("[DRY-RUN] Would open %s hedge: long %s, short %s", pair.base, long_venue, short_venue)
            return

        intent = Intent(
            intent_id="",
            base=pair.base,
            quote_preference=["USDT", "USDC", "USD"],
            product="perp",
            side="buy",
            order_type="market",
            total_notional_usd=self._cfg.notional_per_leg * 2,
            split={long_venue: 0.5, short_venue: 0.5},
            leverage=1,
            leg_configs={
                long_venue: LegConfig(side="buy", product="perp", leverage=1),
                short_venue: LegConfig(side="sell", product="perp", leverage=1),
            },
            max_funding_rate_pct=None,
        )

        try:
            result = await self._submit_intent(intent)
            if result.get("status") in ("ALL_FILLED", "DRY_RUN", "REJECTED"):
                legs = result.get("legs", [])
                leg_ids = [l.get("leg_id", "") for l in legs]
                long_leg = next((l for l in leg_ids if l), leg_ids[0] if len(leg_ids) > 0 else "")
                short_leg = leg_ids[1] if len(leg_ids) > 1 else ""
                await self._pm.record_open(
                    pair=pair,
                    notional_per_leg=self._cfg.notional_per_leg,
                    intent_id=result.get("intent_id", ""),
                    leg_long_id=long_leg,
                    leg_short_id=short_leg,
                    rate_a=spread.rate_a,
                    rate_b=spread.rate_b,
                )
            else:
                logger.error("Open intent failed: %s", result.get("reason", "unknown"))
        except Exception:
            logger.error("Failed to submit open intent for %s:\n%s", pair.base, traceback.format_exc())

    async def _close_position(self, pos: HedgedPosition) -> None:
        """Close a hedged position by reversing both legs."""
        from src.coordinator.intent import Intent, LegConfig

        logger.info("[%s] %s: CLOSING hedged position", pos.position_id, pos.pair.base)

        if self._cfg.dry_run:
            logger.info("[DRY-RUN] Would close %s", pos.position_id)
            return

        intent = Intent(
            intent_id="",
            base=pos.pair.base,
            quote_preference=["USDT", "USDC", "USD"],
            product="perp",
            side="sell",
            order_type="market",
            total_notional_usd=pos.notional_per_leg * 2,
            split={pos.pair.venue_a: 0.5, pos.pair.venue_b: 0.5},
            leverage=1,
            leg_configs={
                pos.pair.venue_a: LegConfig(side="sell", product="perp", leverage=1),
                pos.pair.venue_b: LegConfig(side="buy", product="perp", leverage=1),
            },
            max_funding_rate_pct=None,
        )

        try:
            result = await self._submit_intent(intent)
            if result.get("status") in ("ALL_FILLED", "ROLLED_BACK"):
                await self._pm.record_close(pos.position_id, result.get("intent_id", ""))
                logger.info("[%s] %s: closed successfully", pos.position_id, pos.pair.base)
            else:
                logger.error("[%s] Close intent failed: %s", pos.position_id, result.get("reason", "unknown"))
        except Exception:
            logger.error("[%s] Failed to submit close intent:\n%s", pos.position_id, traceback.format_exc())

    # ── helpers ───────────────────────────────────────────────

    def _log_startup(self) -> None:
        logger.info(
            "AutoArb daemon started: interval=%ds, min_spread=%.3f%%, "
            "exit_spread=%.3f%%, notional=$%.0f/leg, max_positions=%d, "
            "bases=%s, dry_run=%s",
            self._cfg.interval_seconds,
            self._cfg.min_spread_pct,
            self._cfg.exit_spread_pct,
            self._cfg.notional_per_leg,
            self._cfg.max_positions,
            self._base_filter or "all",
            self._cfg.dry_run,
        )
        if self._cfg.dry_run:
            logger.info("DRY-RUN mode — no orders will be sent")


# ── helpers ───────────────────────────────────────────────────


def _find_spread(pos, spreads) -> None:
    for s in spreads:
        if s.pair.base == pos.pair.base:
            return s
    return None
