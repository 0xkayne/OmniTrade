"""oneFill CLI — multi-venue coordinated order execution."""

import typer

app = typer.Typer(
    name="onefill",
    help="Multi-venue coordinated order execution.",
    no_args_is_help=True,
)


@app.command()
def order(
    base: str = typer.Option(..., help="Base asset, e.g. BTC"),
    quote_preference: str = typer.Option("USDT,USDC", help="Comma-separated quote preference"),
    product: str = typer.Option(..., help="spot or perp"),
    side: str = typer.Option(..., help="buy or sell"),
    # 'type' shadows builtin; typer uses the parameter introspec
    order_type: str = typer.Option(..., "--type", help="market or limit"),
    total_notional_usd: float = typer.Option(..., help="Total notional in USD"),
    split: str = typer.Option(..., help="venue1=ratio,venue2=ratio (e.g. binance=0.5,hyperliquid=0.5)"),
    leverage: int = typer.Option(1, help="Leverage (perp only)"),
    limit_price: float = typer.Option(None, help="Limit price (limit orders only)"),
    max_slippage_pct: float = typer.Option(None, help="Max slippage %"),
    max_fee_usd: float = typer.Option(None, help="Max total fee USD"),
    max_funding_rate_pct: float = typer.Option(None, help="Max funding rate % (perp)"),
    execute_timeout: int = typer.Option(30, help="Execute phase timeout seconds"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan + validate only, do not send orders"),
    yes: bool = typer.Option(False, "--yes", help="Skip confirmation prompt"),
    json_output: bool = typer.Option(False, "--json", help="Output as machine-readable JSON"),
):
    """Submit a coordinated multi-venue order."""
    raise NotImplementedError("Stage 2+3")


@app.command()
def query(intent_id: str = typer.Argument(...)):
    """Query an intent by ID."""
    raise NotImplementedError("Stage 3")


@app.command()
def list_intents(
    status: str = typer.Option(None, "--status", help="Filter by status"),
):
    """List recent intents."""
    raise NotImplementedError("Stage 3")


@app.command()
def cancel(intent_id: str = typer.Argument(...)):
    """Cancel a non-terminal intent."""
    raise NotImplementedError("Stage 3")


@app.command()
def recover():
    """List NEEDS_MANUAL intents and guide resolution."""
    raise NotImplementedError("Stage 3")


@app.command()
def venues():
    """List configured venues and their connection status."""
    raise NotImplementedError("Stage 3")


if __name__ == "__main__":
    app()
