"""oneFill CLI — multi-venue coordinated order execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, NamedTuple

import typer
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.coordinator.intent import _EMPTY_LEG_CONFIG, Intent, LegConfig
from src.coordinator.state_machine import BLOCKING_STATE, TERMINAL_STATES
from src.core.base_exchange import NetworkType

app = typer.Typer(
    name="onefill",
    help="Multi-venue coordinated order execution.",
    no_args_is_help=True,
)


@app.callback()
def _global_options(
    log_json: bool = typer.Option(False, "--log-json", help="Emit structured JSON log lines to stderr"),
) -> None:
    """Global options applied before every command."""
    if log_json:
        from src.logging_setup import setup_logging

        setup_logging(level=logging.DEBUG, json_mode=True, logger_names=["src"])


console = Console()
logger = logging.getLogger(__name__)

# Exit codes (PRD section 6.3)
EXIT_ALL_FILLED = 0
EXIT_GENERAL_ERROR = 1
EXIT_REJECTED = 2
EXIT_ROLLED_BACK = 3
EXIT_NEEDS_MANUAL = 4

_STATUS_TO_EXIT: dict[str, int] = {
    "ALL_FILLED": EXIT_ALL_FILLED,
    "REJECTED": EXIT_REJECTED,
    "ROLLED_BACK": EXIT_ROLLED_BACK,
    "ROLLED_BACK_FAILED": EXIT_NEEDS_MANUAL,
    "DRY_RUN": EXIT_ALL_FILLED,
    "NEEDS_MANUAL": EXIT_NEEDS_MANUAL,
}


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


class SplitResult(NamedTuple):
    ratios: dict[str, float]
    leg_configs: dict[str, LegConfig]


def parse_split(raw: str) -> SplitResult:
    """Parse split string into ratios and per-leg overrides.

    Format: venue=ratio[:side[:product[:leverage]]]
    Examples:
      "binance=0.5,hyperliquid=0.5"             — no overrides
      "binance=0.5,hyperliquid=0.5:sell"        — HL leg overrides side
      "binance=0.5,hyperliquid=0.5:sell:perp:3" — HL leg overrides side, product, leverage
    """
    ratios: dict[str, float] = {}
    leg_configs: dict[str, LegConfig] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise typer.BadParameter(f"Invalid split format: '{part}'. Expected venue=ratio")
        venue, rest = part.split("=", 1)
        venue = venue.strip()
        rest = rest.strip()

        # Split on ':' to separate ratio from optional override fields
        fields = rest.split(":")
        ratio_str = fields[0].strip()
        try:
            ratio = float(ratio_str)
        except ValueError as err:
            if "(" in ratio_str:
                raise typer.BadParameter(
                    f"Invalid ratio value: '{ratio_str}'. "
                    "Parenthesized overrides are not supported — "
                    "use colon syntax: venue=ratio:side:product:leverage "
                    "(e.g. binance=0.5:buy:spot)."
                ) from err
            raise typer.BadParameter(f"Invalid ratio value: '{ratio_str}'") from err
        if ratio <= 0:
            raise typer.BadParameter(f"Ratio must be positive, got {ratio}")
        ratios[venue] = ratio

        if len(fields) > 1:
            leg_side = fields[1].strip() if len(fields) > 1 and fields[1].strip() else None
            leg_product = fields[2].strip() if len(fields) > 2 and fields[2].strip() else None
            leg_leverage_str = fields[3].strip() if len(fields) > 3 and fields[3].strip() else None
            leg_leverage = None
            if leg_leverage_str:
                try:
                    leg_leverage = int(leg_leverage_str)
                except ValueError as err:
                    raise typer.BadParameter(f"Invalid leverage value for {venue}: '{leg_leverage_str}'") from err
            try:
                leg_configs[venue] = LegConfig(product=leg_product, side=leg_side, leverage=leg_leverage)
            except ValueError as e:
                raise typer.BadParameter(f"Invalid leg config for {venue}: {e}") from e

    return SplitResult(ratios=ratios, leg_configs=leg_configs)


def parse_quote_preference(raw: str) -> list[str]:
    """Parse 'USDT,USDC' into ['USDT', 'USDC']."""
    return [q.strip() for q in raw.split(",") if q.strip()]


def _precheck_instruments(
    base: str,
    product: str,
    split_dict: dict[str, float],
    leg_configs: dict[str, LegConfig],
) -> None:
    """Check the instrument cache for each venue in the split.

    Fails fast if any venue is missing the requested pair, before the
    orchestrator is bootstrapped. Best-effort — cache failures are logged
    and the order proceeds normally.
    """
    from pathlib import Path

    db_path = Path("data/onefill.db")
    if not db_path.exists():
        return

    async def _check():
        from src.cli.bootstrap import build_store

        store = await build_store()
        try:
            cache_age = await store.instrument_cache_age()
            if cache_age is None:
                return

            for venue in split_dict:
                lc = leg_configs.get(venue, _EMPTY_LEG_CONFIG)
                leg_product = lc.resolve_product(product)
                rows = await store.load_instruments_by_query(
                    base=base,
                    venue=venue,
                    market_type=leg_product,
                )
                if not rows:
                    console.print(
                        f"\n[bold red]Pre-check failed:[/bold red] {base} {leg_product} "
                        f"is not available on [cyan]{venue}[/cyan]."
                    )
                    console.print(
                        f"[dim]Run `onefill instruments --base {base}` "
                        f"to see available pairs, or `onefill instruments --refresh` "
                        f"to update the cache.[/dim]"
                    )
                    raise typer.Exit(EXIT_REJECTED)
        finally:
            await store.close()

    try:
        asyncio.run(_check())
    except typer.Exit:
        raise
    except BaseException:
        raise
    except Exception:
        logger.warning("Instrument cache pre-check failed, proceeding anyway", exc_info=True)


# ---------------------------------------------------------------------------
# JSON output helper
# ---------------------------------------------------------------------------


def _derive_leg_notional(leg: dict[str, Any]) -> float:
    notional = leg.get("notional_usd") or leg.get("planned_notional_usd") or 0.0
    if notional == 0.0:
        fill_qty = leg.get("filled_amount", 0.0)
        fill_price = leg.get("avg_price")
        if fill_qty and fill_price:
            notional = fill_qty * fill_price
    return notional


def _map_leg_for_json(leg: dict[str, Any], default_product: str, default_side: str) -> dict[str, Any]:
    """Normalise a single leg dict into the standard JSON leg schema."""
    notional = _derive_leg_notional(leg)

    market_type = leg.get("market_type", default_product)
    side = leg.get("side", default_side)

    entry: dict[str, Any] = {
        "venue": leg.get("venue", ""),
        "instrument": leg.get("instrument", leg.get("instrument_venue_symbol", "")),
        "market_type": market_type,
        "side": side,
        "leverage": leg.get("leverage", 1),
        "notional_usd": notional,
        "qty_base": leg.get("filled_amount", leg.get("planned_qty_base", 0.0)),
        "order_id": leg.get("order_id"),
        "filled_amount": leg.get("filled_amount", 0.0),
        "avg_price": leg.get("avg_price"),
        "fee_usd": leg.get("fee", leg.get("estimated_fee_usd", 0.0)),
        "slippage_pct": leg.get("estimated_slippage_pct"),
    }
    # Map planned qty if available
    if "planned_qty_base" in leg and entry["qty_base"] == 0.0:
        entry["qty_base"] = leg["planned_qty_base"]
    return entry


def _to_json_output(result: dict[str, Any], intent: Intent) -> dict[str, Any]:
    """Transform an orchestrator result dict into the standard JSON output schema."""
    status = result.get("status", "UNKNOWN")

    # Map legs from wherever they come from
    if status == "DRY_RUN":
        raw_legs = result.get("plan", {}).get("legs", [])
    else:
        raw_legs = result.get("legs", [])

    legs = [_map_leg_for_json(leg, intent.product, intent.side) for leg in raw_legs]

    # Compute aggregate
    total_notional = intent.total_notional_usd
    total_fee_usd = sum(leg.get("fee_usd", 0.0) for leg in legs)
    if status == "DRY_RUN":
        total_fee_usd = result.get("plan", {}).get("aggregate", {}).get("estimated_fee_usd", 0.0)
        weighted_avg_price = result.get("plan", {}).get("aggregate", {}).get("estimated_avg_price", None)
    else:
        # Weighted average by filled_amount
        total_filled = sum(leg.get("filled_amount", 0.0) for leg in legs)
        if total_filled > 0:
            weighted_avg_price = (
                sum((leg.get("avg_price") or 0.0) * leg.get("filled_amount", 0.0) for leg in legs) / total_filled
            )
        else:
            weighted_avg_price = None

    duration_ms = None
    if result.get("execution_time_s"):
        duration_ms = round(result["execution_time_s"] * 1000)

    error = result.get("reason") or result.get("error") or None

    output = {
        "intent_id": result.get("intent_id", intent.intent_id),
        "status": status,
        "legs": legs,
        "aggregate": {
            "total_notional": total_notional,
            "weighted_avg_price": weighted_avg_price,
            "total_fee_usd": total_fee_usd,
            "duration_ms": duration_ms,
        },
        "error": error,
    }

    if result.get("validation_failures"):
        output["validation_failures"] = result["validation_failures"]
    if result.get("risk_failures"):
        output["risk_failures"] = result["risk_failures"]
    rejected = result.get("rejected_venues") or result.get("plan", {}).get("rejected_venues")
    if rejected:
        output["rejected_venues"] = rejected

    if result.get("timing"):
        output["timing"] = result["timing"]

    return output


# ---------------------------------------------------------------------------
# Rich rendering helpers
# ---------------------------------------------------------------------------

_STATUS_COLORS: dict[str, str] = {
    "ALL_FILLED": "green",
    "REJECTED": "red",
    "ROLLED_BACK": "yellow",
    "ROLLED_BACK_FAILED": "red",
    "RESOLVED_MANUAL": "blue",
    "DRY_RUN": "cyan",
    "NEEDS_MANUAL": "red",
    "PARTIAL_FILLED": "yellow",
}


def _make_table(title: str) -> Table:
    return Table(title=title, show_header=True, header_style="bold")


def _format_leverage(market_type: str, leverage: int) -> str:
    """Display leverage only for perp legs above 1x."""
    return f"{leverage}x" if market_type == "perp" and leverage > 1 else "—"


def _render_order_result(result: dict[str, Any], intent: Intent) -> None:
    """Render an order result with rich."""
    status = result.get("status", "UNKNOWN")
    color = _STATUS_COLORS.get(status, "white")

    # Build header panel
    header_text = Text()
    header_text.append("Status:  ", style="bold")
    header_text.append(status, style=f"bold {color}")

    # Extract legs and rejected venues once (source depends on dry-run vs execution)
    if status == "DRY_RUN":
        raw_legs = result.get("plan", {}).get("legs", [])
        rejected = result.get("plan", {}).get("rejected_venues", [])
    else:
        raw_legs = result.get("legs", [])
        rejected = result.get("rejected_venues", [])

    sides = {leg.get("side", intent.side) for leg in raw_legs}
    products = {leg.get("market_type", intent.product) for leg in raw_legs}

    if len(sides) == 1 and len(products) == 1:
        summary = f"{intent.side} ${intent.total_notional_usd:,.2f} {intent.base} ({intent.product})"
    else:
        side_str = "/".join(sorted(sides)) if sides else intent.side
        product_str = "/".join(sorted(products)) if products else intent.product
        summary = f"${intent.total_notional_usd:,.2f} {intent.base} (sides: {side_str}, products: {product_str})"
    header_text.append(f"\nIntent:  {summary} across {len(intent.split)} venues")
    if "intent_id" in result:
        header_text.append(f"\nID:      {result['intent_id']}")

    reason = result.get("reason")
    if reason:
        header_text.append(f"\nReason:  {reason}", style="dim")

    panel = Panel(header_text, title="oneFill — Order Result", border_style=color)
    console.print(panel)

    # Legs table
    if raw_legs:
        table = Table(title="Leg Details", show_header=True, header_style="bold")
        table.add_column("Venue", style="cyan")
        table.add_column("Instrument")
        table.add_column("Side")
        table.add_column("Lev")
        table.add_column("Notional")
        table.add_column("Qty")
        table.add_column("Avg Price", justify="right")
        table.add_column("Slippage", justify="right")
        table.add_column("Fee", justify="right")
        table.add_column("Status")

        for leg in raw_legs:
            instrument_str = leg.get("instrument", leg.get("instrument_venue_symbol", ""))
            notional = _derive_leg_notional(leg)
            qty = leg.get("filled_amount") or leg.get("planned_qty_base", 0)
            avg_price = leg.get("avg_price") or leg.get("estimated_avg_price", "—")
            slippage = leg.get("estimated_slippage_pct", "—")
            fee = leg.get("fee") or leg.get("estimated_fee_usd", 0)
            leg_status = leg.get("status", "—")
            leg_side = leg.get("side", intent.side)
            leg_leverage = leg.get("leverage", 1)
            leg_market_type = leg.get("market_type", intent.product)

            table.add_row(
                leg.get("venue", ""),
                str(instrument_str),
                leg_side,
                _format_leverage(leg_market_type, leg_leverage),
                f"${notional:,.2f}" if isinstance(notional, (int, float)) else str(notional),
                f"{qty:.6f}" if isinstance(qty, float) else str(qty),
                f"${avg_price:,.2f}" if isinstance(avg_price, (int, float)) else str(avg_price),
                f"{slippage}%" if isinstance(slippage, (int, float)) else str(slippage),
                f"${fee:,.2f}" if isinstance(fee, (int, float)) else str(fee),
                leg_status,
            )
        console.print(table)

        leg_errors = [(leg.get("venue", "?"), leg["error"]) for leg in raw_legs if leg.get("error")]
        if leg_errors:
            console.print("\n[bold]Leg errors:[/bold]")
            for venue, err in leg_errors:
                console.print(f"  • {venue}: {err}")

    if rejected:
        formatted = ", ".join(f"{item.get('venue', '?')} ({item.get('reason', '?')})" for item in rejected)
        console.print(f"\n[dim]Rejected venues: {formatted}[/dim]")

    validation_failures = result.get("validation_failures") or []
    if validation_failures:
        console.print("\n[bold]Validation failures:[/bold]")
        for item in validation_failures:
            console.print(f"  • {item.get('venue', '?')}: {item.get('reason', '?')}")

    # Aggregate summary
    if status == "DRY_RUN":
        agg = result.get("plan", {}).get("aggregate", {})
        if agg:
            console.print(
                f"\nEstimated avg price: ${agg.get('estimated_avg_price', '—'):,.2f}"
                if isinstance(agg.get("estimated_avg_price"), (int, float))
                else ""
            )
            console.print(
                f"Estimated total fee: ${agg.get('estimated_fee_usd', '—'):,.2f}"
                if isinstance(agg.get("estimated_fee_usd"), (int, float))
                else ""
            )
    elif status == "ALL_FILLED":
        exec_time = result.get("execution_time_s", 0)
        console.print(f"\nDuration: {exec_time * 1000:.0f}ms")

    # Reconciliation info
    reconciliation = result.get("reconciliation")
    if reconciliation:
        rec_status = reconciliation.get("status", "—")
        residual = reconciliation.get("residual_exposure_usd", 0)
        console.print(f"\n[yellow]Reconciliation: {rec_status}[/yellow]")
        if residual:
            console.print(f"[yellow]Residual exposure: ${residual:,.2f}[/yellow]")

    # Timing breakdown
    timing = result.get("timing")
    if timing:
        _render_timing(timing)


def _render_timing(timing: dict[str, Any]) -> None:
    """Render a timing breakdown table."""
    table = _make_table("Timing Breakdown")
    table.add_column("Phase", style="cyan")
    table.add_column("Duration (ms)", justify="right")
    table.add_column("Per-leg detail")

    bootstrap_ms = timing.get("bootstrap_ms", 0)
    if bootstrap_ms:
        table.add_row("bootstrap", f"{bootstrap_ms:.1f}", "build_orchestrator()")

    phases = timing.get("phases", {})
    for phase_name in ("plan", "validate", "execute", "reconcile"):
        phase = phases.get(phase_name)
        if phase is None or phase.get("total_ms", 0) == 0:
            continue
        total = phase["total_ms"]
        legs = phase.get("legs", {})
        detail_parts = []
        for venue, leg_data in sorted(legs.items()):
            parts = []
            for k, v in sorted(leg_data.items()):
                if k == "poll_attempts":
                    parts.append(f"{k}={int(v)}")
                else:
                    parts.append(f"{k}={v:.0f}ms")
            detail_parts.append(f"{venue}: {', '.join(parts)}")
        detail = " | ".join(detail_parts) if detail_parts else "—"
        table.add_row(phase_name, f"{total:.1f}", detail)

    total_ms = timing.get("total_ms", 0)
    table.add_row("[bold]Total[/bold]", f"[bold]{total_ms:.1f}[/bold]", "")
    console.print(table)


def _render_query_result(intent_row: Any, leg_rows: list[Any]) -> None:
    """Render a query result with rich."""
    header = Text()
    header.append(f"Intent:  {intent_row.intent_id}", style="bold")
    header.append(f"\nStatus:  {intent_row.status}", style=f"bold {_STATUS_COLORS.get(intent_row.status, 'white')}")
    header.append(f"\nCreated: {intent_row.created_at}")
    header.append(f"\nUpdated: {intent_row.updated_at}")

    # Parse raw intent JSON for details
    try:
        raw = json.loads(intent_row.raw_intent_json)
        header.append(
            f"\nSide:    {raw.get('side', '?')} ${raw.get('total_notional_usd', 0):,.2f} "
            f"{raw.get('base', '?')} ({raw.get('product', '?')})"
        )
    except (json.JSONDecodeError, TypeError):
        pass

    panel = Panel(header, title="oneFill — Intent Query", border_style="blue")
    console.print(panel)

    if leg_rows:
        table = Table(title="Legs", show_header=True, header_style="bold")
        table.add_column("Leg ID")
        table.add_column("Venue", style="cyan")
        table.add_column("Instrument")
        table.add_column("Order ID")
        table.add_column("Lev")
        table.add_column("Status")
        table.add_column("Filled", justify="right")
        table.add_column("Avg Price", justify="right")
        table.add_column("Fee", justify="right")

        for leg in leg_rows:
            table.add_row(
                leg.leg_id[:12] + "...",
                leg.venue,
                leg.instrument_venue_symbol,
                leg.order_id or "—",
                _format_leverage(leg.instrument_market_type, leg.leverage),
                leg.status,
                f"{leg.filled_amount:.6f}" if leg.filled_amount else "—",
                f"${leg.avg_price:,.2f}" if leg.avg_price else "—",
                f"${leg.fee_usd:,.2f}" if leg.fee_usd else "—",
            )
        console.print(table)


def _render_list_table(intent_rows: list[Any]) -> None:
    """Render a list-intents table with rich."""
    if not intent_rows:
        console.print("[dim]No intents found.[/dim]")
        return

    table = Table(title="Recent Intents", show_header=True, header_style="bold")
    table.add_column("Intent ID")
    table.add_column("Status")
    table.add_column("Created")
    table.add_column("Base")
    table.add_column("Notional", justify="right")
    table.add_column("Side")
    table.add_column("Product")

    for row in intent_rows:
        status_color = _STATUS_COLORS.get(row.status, "white")
        try:
            raw = json.loads(row.raw_intent_json)
            base = raw.get("base", "?")
            notional = raw.get("total_notional_usd", 0)
            side = raw.get("side", "?")
            product = raw.get("product", "?")
        except (json.JSONDecodeError, TypeError):
            base = "?"
            notional = 0
            side = "?"
            product = "?"

        table.add_row(
            row.intent_id[:16] + "...",
            f"[{status_color}]{row.status}[/{status_color}]",
            row.created_at[:19] if row.created_at else "—",
            base,
            f"${notional:,.2f}",
            side,
            product,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def order(
    base: str = typer.Option(..., help="Base asset, e.g. BTC"),
    quote_preference: str = typer.Option("USDT,USDC", help="Comma-separated quote preference"),
    product: str = typer.Option(..., help="spot or perp"),
    side: str = typer.Option(..., help="buy or sell"),
    order_type: str = typer.Option(..., "--type", help="market or limit"),
    total_notional_usd: float = typer.Option(..., help="Total notional in USD"),
    split: str = typer.Option(
        ...,
        help=(
            "venue=ratio[:side[:product[:leverage]]],... "
            "(e.g. binance=0.5,hyperliquid=0.5:sell:perp:2). "
            "Use colons for per-leg overrides, not parentheses."
        ),
    ),
    leverage: int = typer.Option(1, help="Leverage (perp only)"),
    limit_price: float = typer.Option(None, help="Limit price (limit orders only)"),
    max_slippage_pct: float = typer.Option(
        None,
        help=(
            "Max slippage % per leg. Planner rejects the plan if estimated slippage "
            "exceeds this; on Hyperliquid it is also passed to ccxt as the IOC "
            "limit-price tolerance. If unset, ccxt applies a 5% default for "
            "Hyperliquid market orders. Recommended to set explicitly on mainnet."
        ),
    ),
    max_fee_usd: float = typer.Option(None, help="Max total fee USD"),
    max_funding_rate_pct: float = typer.Option(None, help="Max funding rate % (perp)"),
    execute_timeout: int = typer.Option(30, help="Execute phase timeout seconds"),
    time_in_force: str | None = typer.Option(
        None, "--time-in-force", help="GTC, IOC, or FOK. Default: exchange default (usually GTC)."
    ),
    poll_interval_ms: int = typer.Option(
        500,
        "--poll-interval-ms",
        help="Poll interval ms for fill confirmation (adaptive: starts at 50ms, doubles up to this cap)",
    ),
    network: str = typer.Option("testnet", "--network", help="testnet or mainnet"),
    no_websocket: bool = typer.Option(
        False, "--no-websocket", help="Disable WebSocket fill confirmation; use HTTP polling only."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan + validate only, do not send orders"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="Output as machine-readable JSON"),
):
    """Submit a coordinated multi-venue order."""
    # 1. Parse args
    try:
        split_result = parse_split(split)
        split_dict = split_result.ratios
        leg_configs = split_result.leg_configs
    except typer.BadParameter as e:
        console.print(f"[red]Error parsing --split: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    quote_list = parse_quote_preference(quote_preference)

    try:
        target_network = NetworkType(network)
    except ValueError:
        raise typer.BadParameter(f"Invalid network '{network}'. Use 'testnet' or 'mainnet'.") from None

    # 2. Build Intent
    intent_id = str(uuid.uuid4())
    try:
        intent = Intent(
            intent_id=intent_id,
            base=base,
            quote_preference=quote_list,
            product=product,  # type: ignore[arg-type]
            side=side,  # type: ignore[arg-type]
            order_type=order_type,  # type: ignore[arg-type]
            total_notional_usd=total_notional_usd,
            split=split_dict,
            leverage=leverage,
            limit_price=limit_price,
            max_slippage_pct=max_slippage_pct,
            max_fee_usd=max_fee_usd,
            max_funding_rate_pct=max_funding_rate_pct,
            execute_timeout_seconds=execute_timeout,
            time_in_force=time_in_force,  # type: ignore[arg-type]
            leg_configs=leg_configs,
        )
    except ValueError as e:
        console.print(f"[red]Invalid intent: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    # 2.5. Pre-check against instrument cache (fail fast for clearly impossible orders)
    _precheck_instruments(base, product, split_dict, leg_configs)

    # 3. Confirmation prompt
    if not yes and not dry_run and not json_output:
        console.print(f"\nOrder: {side} ${total_notional_usd:,.2f} {base} ({product})")
        console.print(f"Split: {', '.join(f'{v}={p * 100:.0f}%' for v, p in split_dict.items())}")
        if leg_configs:
            overrides = []
            for venue, lc in leg_configs.items():
                parts = [venue]
                if lc.side:
                    parts.append(f"side={lc.side}")
                if lc.product:
                    parts.append(f"product={lc.product}")
                if lc.leverage:
                    parts.append(f"leverage={lc.leverage}x")
                overrides.append(":".join(parts))
            console.print(f"Leg overrides: {', '.join(overrides)}")
        console.print(f"Quote preference: {', '.join(quote_list)}")
        console.print(f"Type: {order_type}")
        if limit_price:
            console.print(f"Limit price: ${limit_price:,.2f}")
        if max_slippage_pct:
            console.print(f"Max slippage: {max_slippage_pct}%")

        confirmed = typer.confirm("\nProceed?")
        if not confirmed:
            console.print("[dim]Order cancelled.[/dim]")
            raise typer.Exit(0)

    # 4. Build orchestrator and submit
    async def _run() -> dict[str, Any]:
        from src.cli.bootstrap import build_orchestrator
        from src.coordinator.timing import TimingCollector

        timing = TimingCollector()
        timing.mark("bootstrap")
        orch = await build_orchestrator(
            target_network=target_network, poll_interval_ms=poll_interval_ms, use_websocket=not no_websocket
        )
        timing.bootstrap_ms = timing.pop("bootstrap")

        try:
            result = await orch.submit(intent, dry_run=dry_run, timing=timing)
        finally:
            await orch.close()
        return result

    try:
        result = asyncio.run(_run())
    except FileNotFoundError as e:
        console.print(f"[red]Config error: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        logger.exception("Order submission failed")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    # 5. Output
    if json_output:
        output = _to_json_output(result, intent)
        console.print(json.dumps(output, indent=2, default=str))
    else:
        _render_order_result(result, intent)

    # 6. Exit with correct code
    status = result.get("status", "UNKNOWN")
    exit_code = _STATUS_TO_EXIT.get(status, EXIT_GENERAL_ERROR)
    raise typer.Exit(exit_code)


@app.command()
def query(intent_id: str = typer.Argument(...)):
    """Query an intent by ID."""

    async def _run():
        from src.cli.bootstrap import build_store

        store = await build_store()
        try:
            intent_row = await store.get_intent(intent_id)
            if intent_row is None:
                console.print(f"[red]Intent '{intent_id}' not found.[/red]")
                return 1, None

            leg_rows = await store.get_legs_for_intent(intent_id)
            return 0, (intent_row, leg_rows)
        finally:
            await store.close()

    try:
        exit_code, data = asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    if exit_code != 0 or data is None:
        raise typer.Exit(exit_code)

    intent_row, leg_rows = data
    _render_query_result(intent_row, leg_rows)
    raise typer.Exit(0)


@app.command()
def list_intents(
    status: str = typer.Option(None, "--status", help="Filter by status"),
):
    """List recent intents."""

    async def _run():
        from src.cli.bootstrap import build_store

        store = await build_store()
        try:
            rows = await store.list_intents(status=status, limit=50)
            return rows
        finally:
            await store.close()

    try:
        rows = asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    _render_list_table(rows)
    raise typer.Exit(0)


@app.command()
def cancel(intent_id: str = typer.Argument(...)):
    """Cancel a non-terminal intent."""

    async def _run():
        from src.cli.bootstrap import build_store

        store = await build_store()
        try:
            intent_row = await store.get_intent(intent_id)
            if intent_row is None:
                console.print(f"[red]Intent '{intent_id}' not found.[/red]")
                return

            current_status = intent_row.status

            # Terminal states cannot be cancelled
            if current_status in TERMINAL_STATES:
                console.print(
                    f"[yellow]Cannot cancel: intent {intent_id} is already in "
                    f"terminal state '{current_status}'[/yellow]"
                )
                return

            # PENDING or VALIDATED -> transition to REJECTED
            if current_status in ("PENDING", "VALIDATED"):
                await store.update_intent_status(intent_id, "REJECTED")
                console.print(f"[green]Intent {intent_id} cancelled (set to REJECTED).[/green]")
                return

            # EXECUTING — attempt per-leg cancellation
            console.print(f"[yellow]Intent {intent_id} is EXECUTING. Cancelling sent legs on exchanges…[/yellow]")
            leg_rows = await store.get_legs_for_intent(intent_id)

            # Without exchange adapters available in this command, we
            # update leg status and note the limitation.
            for leg in leg_rows:
                if leg.status in ("SENT", "PENDING_SEND"):
                    console.print(
                        f"  [dim]Leg {leg.leg_id[:12]}... on {leg.venue}: "
                        f"cannot cancel via exchange (no adapter loaded). "
                        f"Run `onefill order` first to initialise connections, "
                        f"or wait for execute timeout to trigger reconcile.[/dim]"
                    )

            # Mark intent as REJECTED so it doesn't block
            await store.update_intent_status(intent_id, "REJECTED")
            console.print(f"[green]Intent {intent_id} marked as REJECTED in store.[/green]")
            console.print(
                "[dim]Note: Exchange-level order cancellation requires a running "
                "Orchestrator. If orders were already sent, monitor your venue accounts "
                "and use `onefill recover` if needed.[/dim]"
            )
        finally:
            await store.close()

    try:
        asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    raise typer.Exit(0)


@app.command()
def ack(intent_id: str = typer.Argument(...)):
    """Acknowledge a ROLLED_BACK_FAILED intent and unblock the system.

    Operator confirms they have reviewed any residual exposure on the venues
    and is ready to resume submitting new intents. Transitions the intent
    from ROLLED_BACK_FAILED to RESOLVED_MANUAL (terminal, non-blocking).
    """

    async def _run():
        from src.cli.bootstrap import build_store

        store = await build_store()
        try:
            intent_row = await store.get_intent(intent_id)
            if intent_row is None:
                console.print(f"[red]Intent '{intent_id}' not found.[/red]")
                return 1

            if intent_row.status != BLOCKING_STATE:
                console.print(
                    f"[yellow]`ack` only applies to {BLOCKING_STATE} intents; "
                    f"{intent_id} is in status '{intent_row.status}'.[/yellow]"
                )
                return 1

            await store.update_intent_status(intent_id, "RESOLVED_MANUAL")
            console.print(
                f"[green]Intent {intent_id} acknowledged "
                f"(ROLLED_BACK_FAILED → RESOLVED_MANUAL). System unblocked.[/green]"
            )
            return 0
        finally:
            await store.close()

    try:
        exit_code = asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    raise typer.Exit(exit_code)


@app.command()
def recover():
    """List ROLLED_BACK_FAILED (a.k.a. NEEDS_MANUAL) intents and guide resolution."""

    async def _run():
        from src.cli.bootstrap import build_store

        store = await build_store()
        try:
            rows = await store.list_intents(status=BLOCKING_STATE, limit=50)
            return rows
        finally:
            await store.close()

    try:
        rows = asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    if not rows:
        console.print("[green]No intents need manual recovery.[/green]")
        raise typer.Exit(0)

    console.print(f"[yellow]{len(rows)} intent(s) need manual recovery:[/yellow]\n")

    for row in rows:
        try:
            raw = json.loads(row.raw_intent_json)
            summary = (
                f"{raw.get('side', '?')} ${raw.get('total_notional_usd', 0):,.2f} "
                f"{raw.get('base', '?')} ({raw.get('product', '?')})"
            )
        except (json.JSONDecodeError, TypeError):
            summary = "(unable to parse intent)"

        panel = Panel(
            Text(
                f"Intent: {row.intent_id}\n"
                f"Status: {row.status}\n"
                f"Created: {row.created_at}\n"
                f"Summary: {summary}\n\n"
                "Suggested action: Review positions manually on each venue, then\n"
                f"run `onefill ack {row.intent_id}` to unblock the system."
            ),
            title=f"ROLLED_BACK_FAILED — {row.intent_id[:16]}...",
            border_style="red",
        )
        console.print(panel)

    raise typer.Exit(0)


@app.command()
def venues():
    """List configured venues and their connection status."""
    config_path = Path("config/exchanges.yaml")

    if not config_path.exists():
        console.print(
            f"[red]Config file not found at {config_path}[/red]\n"
            f"[dim]oneFill expects to be run from the project root "
            f"(current working directory: {os.getcwd()}).[/dim]"
        )
        raise typer.Exit(EXIT_GENERAL_ERROR)

    try:
        with open(config_path) as f:
            config_data = yaml.safe_load(f)
    except Exception as e:
        console.print(f"[red]Failed to read config: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    exchanges_config = config_data.get("exchanges", {})
    if not exchanges_config:
        console.print("[dim]No exchanges configured.[/dim]")
        raise typer.Exit(0)

    # Try to load registry for instrument counts
    instrument_counts: dict[str, int] = {}
    registry_loaded = False
    try:
        from src.market.registry import InstrumentRegistry

        async def _load_registry():
            registry = InstrumentRegistry()
            # We can't easily build exchanges here without secrets,
            # so just check what we can
            return registry

        registry = asyncio.run(_load_registry())
        if registry.instrument_count > 0:
            registry_loaded = True
    except Exception:
        pass

    table = Table(title="Configured Venues", show_header=True, header_style="bold")
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    table.add_column("Enabled")
    table.add_column("Default Network")
    table.add_column("Symbols")

    for name, cfg in exchanges_config.items():
        enabled = "Yes" if cfg.get("enabled", False) else "No"
        symbols = ", ".join(cfg.get("symbols", [])) if cfg.get("symbols") else "—"
        count_str = f" ({instrument_counts[name]})" if name in instrument_counts else ""
        table.add_row(
            name + count_str,
            cfg.get("type", "?"),
            f"[green]{enabled}[/green]" if enabled == "Yes" else f"[dim]{enabled}[/dim]",
            cfg.get("default_network", "?"),
            symbols,
        )

    console.print(table)

    if not registry_loaded:
        console.print(
            "\n[dim]Registry not loaded (instrument counts unavailable). "
            "Run `onefill order --dry-run` with valid secrets to populate.[/dim]"
        )

    raise typer.Exit(0)


@app.command()
def instruments(
    base: str = typer.Option(None, "--base", help="Filter by base asset (e.g. BTC)"),
    venue: str = typer.Option(None, "--venue", help="Filter by venue (e.g. binance)"),
    market: str = typer.Option(None, "--market", help="Filter by market type (spot or perp)"),
    network: str = typer.Option("testnet", "--network", help="testnet or mainnet (with --refresh)"),
    refresh: bool = typer.Option(False, "--refresh", help="Force re-fetch from exchanges"),
    json_output: bool = typer.Option(False, "--json", help="Output as machine-readable JSON"),
):
    """List available trading pairs from the local instrument cache.

    Examples:
        onefill instruments --base BTC
        onefill instruments --venue binance --market perp
        onefill instruments --refresh
    """
    try:
        target_network = NetworkType(network)
    except ValueError:
        raise typer.BadParameter(f"Invalid network '{network}'. Use 'testnet' or 'mainnet'.") from None

    async def _run():
        from src.cli.bootstrap import build_orchestrator, build_store

        if refresh:
            orch = await build_orchestrator(target_network=target_network)
            try:
                await orch.refresh_instruments()
                console.print("[green]Instrument cache refreshed.[/green]")
            finally:
                await orch.close()
            return None

        store = await build_store()
        try:
            cache_age = await store.instrument_cache_age()
            if cache_age is None:
                console.print(
                    "[yellow]No instrument cache found. Run `onefill order --dry-run` "
                    "or `onefill instruments --refresh` to populate it first.[/yellow]"
                )
                return None

            rows = await store.load_instruments_by_query(
                base=base,
                venue=venue,
                market_type=market,
            )
            return rows, cache_age
        finally:
            await store.close()

    try:
        data = asyncio.run(_run())
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(EXIT_GENERAL_ERROR) from e

    if data is None:
        raise typer.Exit(0)

    rows, cache_age = data

    if json_output:
        result = {
            "cached_at": cache_age,
            "count": len(rows),
            "instruments": [
                {
                    "venue": r.venue,
                    "market_type": r.market_type,
                    "base": r.base,
                    "quote": r.quote,
                    "venue_symbol": r.venue_symbol,
                    "min_qty": r.min_qty,
                    "qty_step": r.qty_step,
                    "min_notional": r.min_notional,
                    "taker_fee_rate": r.taker_fee_rate,
                    "maker_fee_rate": r.maker_fee_rate,
                    "listing_status": r.listing_status,
                }
                for r in rows
            ],
        }
        console.print(json.dumps(result, indent=2, default=str))
        raise typer.Exit(0)

    if not rows:
        filters = []
        if base:
            filters.append(f"base={base}")
        if venue:
            filters.append(f"venue={venue}")
        if market:
            filters.append(f"market={market}")
        filter_str = ", ".join(filters) if filters else "any"
        console.print(f"[dim]No instruments found for {filter_str}.[/dim]")
        console.print("[dim]Run `onefill instruments --refresh` to update the cache.[/dim]")
        raise typer.Exit(0)

    table = Table(title=f"Instruments (cached: {cache_age[:19]})", show_header=True, header_style="bold")
    table.add_column("Venue", style="cyan")
    table.add_column("Market")
    table.add_column("Base")
    table.add_column("Quote")
    table.add_column("Min Notional", justify="right")
    table.add_column("Min Qty", justify="right")
    table.add_column("Status")

    for r in rows:
        table.add_row(
            r.venue,
            r.market_type,
            r.base,
            r.quote,
            f"${r.min_notional:,.2f}" if r.min_notional else "—",
            f"{r.min_qty:.6g}" if r.min_qty else "—",
            r.listing_status,
        )

    console.print(table)
    console.print(f"\n[dim]{len(rows)} instrument(s). Cached at {cache_age[:19]}.[/dim]")
    raise typer.Exit(0)


if __name__ == "__main__":
    app()
