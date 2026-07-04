#!/usr/bin/env python3
"""oneFill execution pipeline benchmark.

Usage:
  uv run python scripts/benchmark.py run --mode dry-run --trials 20
  uv run python scripts/benchmark.py run --mode live --venue binance --product spot --trials 10
  uv run python scripts/benchmark.py analyze benchmark_data/2026-05-31
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import math
import random
import statistics
import sys
import uuid
from pathlib import Path

# Ensure project root is on sys.path so imports work regardless of cwd.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Experiment condition definitions
# ---------------------------------------------------------------------------

# Each condition: (id, venue, product, split_dict, leg_configs, mode)
# For single-venue conditions, split is {venue: 1.0}.
# For dual-venue, split is {binance: 0.5, hyperliquid: 0.5}.

DRY_RUN_CONDITIONS = [
    ("A1", "binance", "spot", {"binance": 1.0}, {}),
    ("A2", "hyperliquid", "spot", {"hyperliquid": 1.0}, {}),
    ("B1", "binance", "perp", {"binance": 1.0}, {}),
    ("B2", "hyperliquid", "perp", {"hyperliquid": 1.0}, {}),
    ("C1", "both", "spot", {"binance": 0.5, "hyperliquid": 0.5}, {}),
    ("C2", "both", "perp", {"binance": 0.5, "hyperliquid": 0.5}, {}),
]

LIVE_CONDITIONS = [
    ("D1", "binance", "spot", {"binance": 1.0}, {}),
    ("D2", "hyperliquid", "spot", {"hyperliquid": 1.0}, {}),
]

# Fixed parameters for all trials
FIXED_PARAMS = {
    "base": "BTC",
    "side": "buy",
    "order_type": "market",
    "total_notional_usd": 100.0,
    "network": "testnet",
    "max_slippage_pct": 0.5,
    "execute_timeout": 30,
}


def build_intent(venue: str, product: str, split: dict[str, float]):
    """Build an Intent for a single trial."""
    from src.coordinator.intent import Intent

    return Intent(
        intent_id=str(uuid.uuid4()),
        base=FIXED_PARAMS["base"],
        quote_preference=["USDT", "USDC"],
        product=product,  # type: ignore[arg-type]
        side=FIXED_PARAMS["side"],  # type: ignore[arg-type]
        order_type=FIXED_PARAMS["order_type"],  # type: ignore[arg-type]
        total_notional_usd=FIXED_PARAMS["total_notional_usd"],
        split=split,
        max_slippage_pct=FIXED_PARAMS["max_slippage_pct"],
        execute_timeout_seconds=FIXED_PARAMS["execute_timeout"],
    )


# ---------------------------------------------------------------------------
# Statistics helpers (no numpy/scipy dependency)
# ---------------------------------------------------------------------------


def compute_stats(values: list[float]) -> dict:
    """Return median, IQR, min, max, p95 for a list of floats."""
    if not values:
        return {"median": None, "iqr": None, "min": None, "max": None, "p95": None, "n": 0}
    s = sorted(values)
    n = len(s)

    def _percentile(sorted_vals: list[float], p: float) -> float:
        k = (p / 100.0) * (len(sorted_vals) - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_vals[int(k)]
        return sorted_vals[int(f)] * (c - k) + sorted_vals[int(c)] * (k - f)

    q1 = _percentile(s, 25)
    q3 = _percentile(s, 75)
    return {
        "median": round(statistics.median(s), 3),
        "iqr": round(q3 - q1, 3),
        "q1": round(q1, 3),
        "q3": round(q3, 3),
        "min": round(s[0], 3),
        "max": round(s[-1], 3),
        "p95": round(_percentile(s, 95), 3),
        "n": n,
    }


def _collect_leaf_metrics(timing: dict, prefix: str = "") -> dict[str, float]:
    """Walk the timing dict and collect leaf float values with dot-notation keys."""
    result: dict[str, float] = {}
    for key, value in timing.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_collect_leaf_metrics(value, full_key))
        elif isinstance(value, (int, float)):
            result[full_key] = float(value)
    return result


def aggregate_trials(raw_dir: Path) -> dict:
    """Load raw trial JSONs, group by condition, compute per-metric stats."""
    condition_data: dict[str, list[dict]] = {}
    for fpath in sorted(raw_dir.glob("*.json")):
        with open(fpath) as f:
            trial = json.load(f)
        cond = trial["condition"]
        condition_data.setdefault(cond, []).append(trial)

    conditions_summary: dict[str, dict] = {}
    for cond, trials in sorted(condition_data.items()):
        # Collect all leaf metric values across trials
        metric_values: dict[str, list[float]] = {}
        bootstrap_values: list[float] = []
        for trial in trials:
            t = trial.get("timing", {})
            if "bootstrap_ms" in t and t["bootstrap_ms"] > 0:
                bootstrap_values.append(t["bootstrap_ms"])
            leaves = _collect_leaf_metrics(t)
            for k, v in leaves.items():
                metric_values.setdefault(k, []).append(v)

        metrics_summary: dict[str, dict] = {}
        # Only report meaningful metrics (non-zero variance or non-zero median)
        for metric_name, vals in sorted(metric_values.items()):
            stats = compute_stats(vals)
            if stats["median"] is not None and stats["median"] > 0:
                metrics_summary[metric_name] = stats

        conditions_summary[cond] = {
            "parameters": trials[0]["parameters"],
            "n_trials": len(trials),
            "bootstrap_ms": compute_stats(bootstrap_values) if bootstrap_values else None,
            "metrics": metrics_summary,
        }

    return {
        "experiment_date": Path(raw_dir).parent.name if raw_dir.parent.name != "raw" else "unknown",
        "analyzed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "conditions": conditions_summary,
    }


# ---------------------------------------------------------------------------
# run subcommand
# ---------------------------------------------------------------------------


async def _run_trials(args: argparse.Namespace) -> None:
    """Run all trials for the specified mode and write raw JSON files."""
    from src.cli.bootstrap import build_orchestrator
    from src.coordinator.timing import TimingCollector

    if args.mode == "dry-run":
        conditions = DRY_RUN_CONDITIONS
    else:
        conditions = [
            c for c in LIVE_CONDITIONS
            if c[1] == args.venue and c[2] == args.product
        ]
        if not conditions:
            print(f"No live condition matching venue={args.venue} product={args.product}")
            sys.exit(1)

    # 1. Bootstrap once
    print("Bootstrapping orchestrator (one-time)...")
    from src.core.base_exchange import NetworkType

    tc_boot = TimingCollector()
    tc_boot.mark("bootstrap")
    orch = await build_orchestrator(target_network=NetworkType(FIXED_PARAMS["network"]))
    bootstrap_ms = tc_boot.pop("bootstrap")
    print(f"  bootstrap: {bootstrap_ms:.0f}ms\n")

    # 2. Warmup (3 dry-run trials, not recorded)
    print("Warmup (3 trials, not recorded)...")
    for _ in range(3):
        warmup_intent = build_intent("binance", "spot", {"binance": 1.0})
        result = await orch.submit(warmup_intent, dry_run=True)
        if result["status"] == "REJECTED":
            print(f"  WARNING: warmup REJECTED — {result.get('reason', 'unknown')}")
    print("  done.\n")

    # 3. Build flat trial list (condition_id, trial_index)
    all_trials: list[tuple[str, str, str, dict[str, float], dict]] = []
    for cond_id, venue, product, split_dict, leg_cfgs in conditions:
        all_trials.extend(
            (cond_id, venue, product, split_dict, leg_cfgs)
            for _ in range(args.trials)
        )
    # Randomise order to avoid temporal confounding
    random.shuffle(all_trials)

    # 4. Determine output directory
    today = datetime.date.today().isoformat()
    out_dir = Path(args.output_dir) if args.output_dir else Path(f"benchmark_data/{today}/raw")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Track per-condition trial index for file naming
    cond_counters: dict[str, int] = {}
    total = len(all_trials)
    dry_run_flag = (args.mode == "dry-run")

    print(f"Running {total} trials (mode={args.mode})...")
    for idx, (cond_id, venue, product, split_dict, _leg_cfgs) in enumerate(all_trials):
        intent = build_intent(venue, product, split_dict)

        tc = TimingCollector()
        tc.mark("trial")
        result = await orch.submit(intent, dry_run=dry_run_flag, timing=tc)
        trial_ms = tc.pop("trial")

        cond_counters.setdefault(cond_id, 0)
        trial_num = cond_counters[cond_id] + 1
        cond_counters[cond_id] = trial_num

        trial_data = {
            "trial_id": f"{cond_id}_{trial_num:03d}",
            "condition": cond_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "parameters": {
                "venue": venue,
                "product": product,
                "mode": args.mode,
                **FIXED_PARAMS,
            },
            "status": result["status"],
            "trial_ms": round(trial_ms, 3),
            "bootstrap_ms": round(bootstrap_ms, 3) if bootstrap_ms > 0 else None,
            "timing": result.get("timing", {}),
        }

        fpath = out_dir / f"{cond_id}_{trial_num:03d}.json"
        with open(fpath, "w") as f:
            json.dump(trial_data, f, indent=2, default=str)

        status_icon = "✓" if result["status"] in ("DRY_RUN", "ALL_FILLED") else "✗"
        print(f"  [{idx + 1}/{total}] {status_icon} {trial_data['trial_id']} "
              f"→ {fpath.name} ({trial_ms:.0f}ms)")

        # Inter-trial delay to avoid rate limiter backpressure
        await asyncio.sleep(0.5)

    await orch.close()
    print(f"\nDone. {total} trials saved to {out_dir.resolve()}")


def cmd_run(args: argparse.Namespace) -> None:
    asyncio.run(_run_trials(args))


# ---------------------------------------------------------------------------
# analyze subcommand
# ---------------------------------------------------------------------------


def cmd_analyze(args: argparse.Namespace) -> None:
    exp_dir = Path(args.experiment_dir)
    raw_dir = exp_dir / "raw"
    if not raw_dir.is_dir():
        print(f"Error: raw/ directory not found in {exp_dir}")
        sys.exit(1)

    summary = aggregate_trials(raw_dir)
    out_path = exp_dir / "summary.json"
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)

    # Print summary to stdout
    print(f"Conditions: {len(summary['conditions'])}")
    for cond_id, cond in summary["conditions"].items():
        print(f"\n  [{cond_id}] {cond['parameters']['venue']} / {cond['parameters']['product']}"
              f" — {cond['n_trials']} trials")
        for metric, stats in cond["metrics"].items():
            if stats["median"] is not None:
                print(f"    {metric}: median={stats['median']}ms  "
                      f"IQR={stats['iqr']}ms  min={stats['min']}ms  "
                      f"max={stats['max']}ms  p95={stats['p95']}ms")
    print(f"\nSummary written to {out_path.resolve()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="oneFill execution pipeline benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- run --
    p_run = sub.add_parser("run", help="Run benchmark trials")
    p_run.add_argument("--mode", choices=("dry-run", "live"), default="dry-run",
                       help="dry-run: no orders sent; live: real testnet orders")
    p_run.add_argument("--trials", type=int, default=20,
                       help="Number of trials per condition (default: 20)")
    p_run.add_argument("--venue", choices=("binance", "hyperliquid"),
                       help="Single venue filter (live mode only)")
    p_run.add_argument("--product", choices=("spot", "perp"),
                       help="Single product filter (live mode only)")
    p_run.add_argument("--output-dir", default=None,
                       help="Override output directory (default: benchmark_data/<today>/raw)")
    p_run.set_defaults(func=cmd_run)

    # -- analyze --
    p_analyze = sub.add_parser("analyze", help="Analyze raw trial data")
    p_analyze.add_argument("experiment_dir",
                           help="Path to experiment directory (e.g. benchmark_data/2026-05-31)")
    p_analyze.set_defaults(func=cmd_analyze)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
