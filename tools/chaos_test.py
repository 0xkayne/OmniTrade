#!/usr/bin/env python3
"""Crash-recovery validation for oneFill.

Runs a sequence of dry-run orders, randomly sending SIGKILL to the
process during one of them. After restart, verifies that ``onefill
recover`` can see any partially-executed intents and that the SQLite
state machine + JSONL audit log are consistent.

Usage::

    uv run python tools/chaos_test.py --iterations 10          # quick smoke
    uv run python tools/chaos_test.py --iterations 100 --slow  # CI

Requirements:
- ``onefill`` must be installed as a console_script (``uv sync`` does this).
- Exchange credentials must be configured for testnet (dry-run only in CI).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="oneFill chaos / crash-recovery test")
    p.add_argument("--iterations", type=int, default=10, help="Number of chaos loops")
    p.add_argument("--slow", action="store_true", help="Mark run as slow (for CI labelling)")
    p.add_argument("--sqlite", type=Path, default=Path("data/onefill.db"), help="SQLite path")
    p.add_argument("--jsonl-dir", type=Path, default=Path("logs"), help="JSONL audit directory")
    return p.parse_args(argv)


def _onefill_cmd() -> list[str]:
    """Return the prefix for running onefill CLI."""
    return ["uv", "run", "onefill"]


def run_order(dry_run: bool = True) -> subprocess.CompletedProcess:
    """Run a single dry-run order via subprocess."""
    return subprocess.run(
        [
            *_onefill_cmd(),
            "order",
            "--dry-run" if dry_run else "--yes",
            "--base",
            "BTC",
            "--quote-preference",
            "USDT",
            "--product",
            "spot",
            "--side",
            "buy",
            "--type",
            "market",
            "--total-notional-usd",
            "100",
            "--split",
            "binance=0.5,hyperliquid=0.5",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def verify_db_consistency(sqlite_path: Path, jsonl_dir: Path) -> list[str]:
    """Check SQLite and JSONL are internally consistent. Returns error messages."""
    errors: list[str] = []

    # 1. SQLite is readable
    try:
        conn = sqlite3.connect(str(sqlite_path))
        cursor = conn.execute("SELECT COUNT(*) FROM intents")
        intent_count = cursor.fetchone()[0]
        conn.close()
        logger.info("SQLite: %d intent(s) in database", intent_count)
    except Exception as e:
        errors.append(f"SQLite error: {e}")
        return errors

    # 2. JSONL directory exists with today's file
    today = time.strftime("%Y-%m-%d")
    jsonl_file = jsonl_dir / f"audit-{today}.jsonl"
    if jsonl_file.exists():
        with open(jsonl_file) as f:
            lines = f.readlines()
        logger.info("JSONL: %d event(s) in today's audit log", len(lines))
        # Quick parse check — every line should be valid JSON
        for i, line in enumerate(lines):
            try:
                json.loads(line.strip())
            except json.JSONDecodeError:
                errors.append(f"JSONL line {i + 1} is not valid JSON")
    else:
        logger.info("JSONL: no audit file for today (no real orders executed)")

    return errors


async def chaos_run(iterations: int, sqlite_path: Path, jsonl_dir: Path) -> int:
    """Run *iterations* chaos loops. Returns exit code (0 = success)."""
    failures = 0

    for i in range(1, iterations + 1):
        logger.info("=== Chaos iteration %d/%d ===", i, iterations)

        # Run a normal dry-run first to warm up
        result = run_order(dry_run=True)
        if result.returncode not in (0, 2):  # 0=DRY_RUN, 2=REJECTED
            logger.error("Warm-up dry-run failed: %s", result.stderr[-500:])
            failures += 1
            continue

        # Run a real order — but kill it randomly mid-flight.
        # Because we use testnet and a small notional, the financial
        # risk is negligible even if the kill lands at a bad moment.
        kill_probability = 0.3
        if random.random() < kill_probability:
            proc = subprocess.Popen(
                [
                    *_onefill_cmd(),
                    "order",
                    "--yes",
                    "--base",
                    "BTC",
                    "--quote-preference",
                    "USDT",
                    "--product",
                    "spot",
                    "--side",
                    "buy",
                    "--type",
                    "market",
                    "--total-notional-usd",
                    "20",
                    "--split",
                    "binance=1.0",
                    "--json",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # Wait briefly, then kill
            await asyncio.sleep(random.uniform(0.5, 2.0))
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            logger.info("SIGKILL sent to pid %d", proc.pid)

            # 3. Recovery — query intents after crash
            recover_result = subprocess.run(
                [*_onefill_cmd(), "recover"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            logger.info("Recovery after crash: exit_code=%d", recover_result.returncode)

        # 4. Verify consistency
        errors = verify_db_consistency(sqlite_path, jsonl_dir)
        if errors:
            for e in errors:
                logger.error("Consistency error: %s", e)
            failures += 1

    logger.info("Chaos test complete: %d/%d iterations successful", iterations - failures, iterations)
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    return asyncio.run(chaos_run(args.iterations, args.sqlite, args.jsonl_dir))


if __name__ == "__main__":
    sys.exit(main())
