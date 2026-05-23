"""Tests for CLI help text output."""

from typer.testing import CliRunner

from src.cli.main import app

runner = CliRunner()


def test_main_help():
    """`onefill --help` lists all commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "order" in result.stdout
    assert "query" in result.stdout
    assert "list" in result.stdout
    assert "cancel" in result.stdout
    assert "recover" in result.stdout
    assert "venues" in result.stdout


def test_order_help():
    """`onefill order --help` lists all options."""
    result = runner.invoke(app, ["order", "--help"])
    assert result.exit_code == 0
    assert "--base" in result.stdout
    assert "--split" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--json" in result.stdout
    assert "--yes" in result.stdout
    assert "--type" in result.stdout


def test_query_help():
    """`onefill query --help` shows argument name."""
    result = runner.invoke(app, ["query", "--help"])
    assert result.exit_code == 0
    assert "intent_id" in result.stdout or "INTENT_ID" in result.stdout


def test_list_help():
    """`onefill list --help` shows --status filter."""
    result = runner.invoke(app, ["list", "--help"])
    assert result.exit_code == 0
    assert "--status" in result.stdout


def test_cancel_help():
    """`onefill cancel --help` shows argument."""
    result = runner.invoke(app, ["cancel", "--help"])
    assert result.exit_code == 0


def test_recover_help():
    """`onefill recover --help` works."""
    result = runner.invoke(app, ["recover", "--help"])
    assert result.exit_code == 0


def test_venues_help():
    """`onefill venues --help` works."""
    result = runner.invoke(app, ["venues", "--help"])
    assert result.exit_code == 0


def test_no_args_shows_help():
    """Invoking with no arguments shows help."""
    result = runner.invoke(app, [])
    assert result.exit_code != 0  # no_args_is_help=True gives exit code 2
    assert "order" in result.stdout or "Commands" in result.stdout
