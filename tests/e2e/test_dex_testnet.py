"""Explicitly selected, bounded Arcus/Hyperliquid testnet acceptance suite."""

import getpass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.e2e.dex_testnet_runner import _DexRun

pytestmark = [pytest.mark.network, pytest.mark.slow]


async def test_dex_testnet(request):
    supplied = request.config.getoption("--dex-output-dir")
    output = (
        Path(supplied)
        if supplied
        else (
            Path("/share")
            / getpass.getuser()
            / "outputs"
            / "omnitrade"
            / "dex-testnet"
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        )
    )
    run = _DexRun(
        output,
        trade=request.config.getoption("--dex-testnet-trades"),
        maximum_order=request.config.getoption("--dex-max-order-usd"),
        maximum_total=request.config.getoption("--dex-max-total-usd"),
    )
    checks = await run.run()
    failures = [check["check"] for check in checks if check["status"] in {"FAIL", "BLOCKED"}]
    print(f"DEX testnet report: {output / 'report.md'}")
    assert not failures, f"Unverified checks: {', '.join(failures)}; see {output / 'report.md'}"
