#!/usr/bin/env python3
"""Analyze oneFill benchmark data and produce bottleneck charts.

Usage:
  uv run python scripts/analyze_benchmark.py benchmark_data/2026-05-31
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")  # headless

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_trials(raw_dir: Path) -> dict[str, list[dict]]:
    """Load all trial JSONs, grouped by condition."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for fpath in sorted(raw_dir.glob("*.json")):
        with open(fpath) as f:
            trial = json.load(f)
        groups[trial["condition"]].append(trial)
    return dict(groups)


def phase_metric(trial: dict, phase: str, metric: str) -> float | None:
    """Extract a numeric metric from a trial's timing dict, summing across legs."""
    legs = (
        trial.get("timing", {})
        .get("phases", {})
        .get(phase, {})
        .get("legs", {})
    )
    values = []
    for venue_data in legs.values():
        if metric in venue_data:
            values.append(venue_data[metric])
    return sum(values) if values else None


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

VENUE_COLORS = {"binance": "#F0B90B", "hyperliquid": "#00D4AA"}
PHASE_COLORS = {
    "quote_fetch": "#3498db",
    "balance_fetch": "#e67e22",
    "create_order": "#2ecc71",
    "poll": "#9b59b6",
    "cpu": "#bdc3c7",
}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def build_figure(trials: dict[str, list[dict]], out_dir: Path) -> str:
    """Generate a 2x2 bottleneck analysis figure. Returns the output path."""
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(
        "oneFill Execution Pipeline — Bottleneck Analysis (testnet, 2026-05-31)",
        fontsize=16,
        fontweight="bold",
        y=0.98,
    )

    # ── Panel 1: Dry-run per-venue phase timing ──────────────────────
    ax1 = axes[0, 0]
    _panel_dry_run_bar(ax1, trials)

    # ── Panel 2: quote_fetch boxplot ─────────────────────────────────
    ax2 = axes[0, 1]
    _panel_quote_fetch_boxplot(ax2, trials)

    # ── Panel 3: Dual-leg sequential penalty ─────────────────────────
    ax3 = axes[1, 0]
    _panel_dual_leg(ax3, trials)

    # ── Panel 4: Live execution breakdown (D1) ───────────────────────
    ax4 = axes[1, 1]
    if "D1" in trials:
        _panel_live_breakdown(ax4, trials)
    else:
        ax4.text(0.5, 0.5, "No live data (D1) available", ha="center", va="center",
                 transform=ax4.transAxes, fontsize=14, color="gray")
        ax4.set_title("Live Execution Breakdown", fontsize=13, fontweight="bold")

    plt.tight_layout(rect=[0, 0, 1, 0.95])
    out_path = out_dir / "bottleneck_analysis.png"
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(out_path)


def _panel_dry_run_bar(ax, trials: dict[str, list[dict]]):
    """Grouped bar: mean dry-run phase times per single-venue condition (A1,A2,B1,B2)."""
    conditions = ["A1", "A2", "B1", "B2"]
    labels = ["Binance\nspot", "HL\nspot", "Binance\nperp", "HL\nperp"]
    quote_means = []
    cpu_means = []

    for cond in conditions:
        ts = trials.get(cond, [])
        q_vals = [phase_metric(t, "plan", "quote_fetch_ms") for t in ts]
        c_vals = [phase_metric(t, "plan", "cpu_ms") for t in ts]
        quote_means.append(_mean([v for v in q_vals if v]))
        cpu_means.append(_mean([v for v in c_vals if v]))

    x = range(len(conditions))
    bar_q = ax.bar(x, quote_means, color=PHASE_COLORS["quote_fetch"], label="quote_fetch (orderbook HTTP)")
    bar_c = ax.bar(x, cpu_means, bottom=quote_means, color=PHASE_COLORS["cpu"], label="cpu (plan compute)")

    # Add value labels
    for i, (q, c) in enumerate(zip(quote_means, cpu_means)):
        ax.text(i, q / 2, f"{q:.0f}ms", ha="center", va="center", fontsize=8, fontweight="bold", color="white")
        if c > 0.05:
            ax.text(i, q + c / 2, f"{c:.0f}ms", ha="center", va="center", fontsize=7, color="black")

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Plan Phase: quote_fetch Dominates (dry-run, n=20)", fontsize=13, fontweight="bold")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3)

    # Highlight HL gap
    ax.annotate(
        "HL ~100ms slower\nthan Binance",
        xy=(1, quote_means[1]),
        xytext=(2.5, max(quote_means) + 30),
        fontsize=9,
        color="#00D4AA",
        arrowprops=dict(arrowstyle="->", color="#00D4AA", lw=1.5),
        fontweight="bold",
    )


def _panel_quote_fetch_boxplot(ax, trials: dict[str, list[dict]]):
    """Boxplot: quote_fetch_ms distribution per venue across all single-leg conditions."""
    binance_vals = []
    hl_vals = []
    for cond in ("A1", "A2", "B1", "B2"):
        ts = trials.get(cond, [])
        for t in ts:
            legs = t.get("timing", {}).get("phases", {}).get("plan", {}).get("legs", {})
            for venue, data in legs.items():
                if "quote_fetch_ms" in data:
                    if venue == "binance":
                        binance_vals.append(data["quote_fetch_ms"])
                    elif venue == "hyperliquid":
                        hl_vals.append(data["quote_fetch_ms"])

    bp = ax.boxplot(
        [binance_vals, hl_vals],
        tick_labels=["Binance\n(n=40)", "Hyperliquid\n(n=40)"],
        patch_artist=True,
        widths=0.4,
        showmeans=True,
        meanprops=dict(marker="D", markerfacecolor="red", markersize=6),
    )
    bp["boxes"][0].set_facecolor(VENUE_COLORS["binance"])
    bp["boxes"][1].set_facecolor(VENUE_COLORS["hyperliquid"])

    # Stats annotation
    ax.text(
        0.02, 0.98,
        f"Binance:   median={_median(binance_vals):.0f}ms,  p95={_p95(binance_vals):.0f}ms\n"
        f"HL:           median={_median(hl_vals):.0f}ms,  p95={_p95(hl_vals):.0f}ms\n"
        f"Delta:       +{_median(hl_vals) - _median(binance_vals):.0f}ms",
        transform=ax.transAxes,
        fontsize=9,
        verticalalignment="top",
        fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.5", facecolor="lightyellow", alpha=0.9),
    )

    ax.set_ylabel("Latency (ms)")
    ax.set_title("quote_fetch_ms Distribution: Binance vs Hyperliquid", fontsize=13, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)


def _panel_dual_leg(ax, trials: dict[str, list[dict]]):
    """Show that dual-leg total = binance_fetch + hyperliquid_fetch (sequential penalty)."""
    cond_labels = ["C1\n(spot)", "C2\n(perp)"]
    col_names = ["C1", "C2"]

    binance_means = []
    hl_means = []
    total_means = []

    for cond in col_names:
        ts = trials.get(cond, [])
        b_vals, h_vals, t_vals = [], [], []
        for t in ts:
            legs = t.get("timing", {}).get("phases", {}).get("plan", {}).get("legs", {})
            b = legs.get("binance", {}).get("quote_fetch_ms", 0)
            h = legs.get("hyperliquid", {}).get("quote_fetch_ms", 0)
            total = t.get("timing", {}).get("phases", {}).get("plan", {}).get("total_ms", 0)
            if b:
                b_vals.append(b)
            if h:
                h_vals.append(h)
            if total:
                t_vals.append(total)
        binance_means.append(_mean(b_vals))
        hl_means.append(_mean(h_vals))
        total_means.append(_mean(t_vals))

    x = range(len(cond_labels))
    width = 0.25
    bars_b = ax.bar([i - width for i in x], binance_means, width, color=VENUE_COLORS["binance"], label="Binance quote_fetch")
    bars_h = ax.bar(x, hl_means, width, color=VENUE_COLORS["hyperliquid"], label="HL quote_fetch")
    bars_sum = ax.bar([i + width for i in x],
                       [binance_means[i] + hl_means[i] for i in x],
                       width, color="#e74c3c", alpha=0.5, label="Sequential sum (Binance + HL)")

    # Overlay actual total
    ax.scatter([i + width for i in x], total_means, marker="*", s=200, color="red",
               zorder=5, label="Actual total_ms")

    # Value labels
    for i in x:
        ax.text(i - width, binance_means[i] / 2, f"{binance_means[i]:.0f}", ha="center", va="center", fontsize=7, fontweight="bold", color="white")
        ax.text(i, hl_means[i] / 2, f"{hl_means[i]:.0f}", ha="center", va="center", fontsize=7, fontweight="bold", color="white")

    ax.set_xticks(list(x))
    ax.set_xticklabels(cond_labels)
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Dual-Leg: Quotes Fetched SEQUENTIALLY (dry-run, n=20)", fontsize=13, fontweight="bold")
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3)


def _panel_live_breakdown(ax, trials: dict[str, list[dict]]):
    """Stacked bar of Binance live (D1) breakdown: plan + validate + execute sub-phases."""
    ts = trials.get("D1", [])
    # Average across trials
    plan_means = []
    validate_means = []
    create_order_means = []
    poll_means = []
    for t in ts:
        timing = t.get("timing", {})
        plan_means.append(timing.get("phases", {}).get("plan", {}).get("total_ms", 0))
        validate_means.append(timing.get("phases", {}).get("validate", {}).get("total_ms", 0))
        exec_legs = timing.get("phases", {}).get("execute", {}).get("legs", {})
        for data in exec_legs.values():
            create_order_means.append(data.get("create_order_ms", 0))
            poll_means.append(data.get("poll_total_ms", 0))

    p = _mean([v for v in plan_means if v])
    v = _mean([v for v in validate_means if v])
    c = _mean([v for v in create_order_means if v])
    pl = _mean([v for v in poll_means if v])
    # The remaining gap in execute total is poll_interval overhead (sleep + gather)
    exec_totals = [t.get("timing", {}).get("phases", {}).get("execute", {}).get("total_ms", 0) for t in ts]
    exec_total = _mean([v for v in exec_totals if v])
    overhead = max(0, exec_total - c - pl)

    phases = ["Plan\n(quote_fetch)", "Validate\n(balance_fetch)", "Execute:\ncreate_order",
              "Execute:\npoll fetch", "Execute:\noverhead"]
    values = [p, v, c, pl, overhead]
    colors = [PHASE_COLORS["quote_fetch"], PHASE_COLORS["balance_fetch"],
              PHASE_COLORS["create_order"], PHASE_COLORS["poll"], "#95a5a6"]

    bars = ax.barh(phases, values, color=colors)
    for bar, val in zip(bars, values):
        ax.text(bar.get_width() + 5, bar.get_y() + bar.get_height() / 2,
                f"{val:.0f}ms", va="center", fontsize=10, fontweight="bold")

    ax.set_xlabel("Latency (ms)")
    ax.set_title("Binance Spot Live Order Breakdown (n=10, ALL_FILLED)", fontsize=13, fontweight="bold")
    ax.grid(axis="x", alpha=0.3)
    # Add total annotation
    ax.text(0.98, 0.02, f"Total (end-to-end): {p + v + c + pl + overhead:.0f}ms",
            transform=ax.transAxes, fontsize=11, fontweight="bold",
            ha="right", bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.9))


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = 0.95 * (len(s) - 1)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] * (c - k) + s[c] * (k - f)


# ---------------------------------------------------------------------------
# Text summary
# ---------------------------------------------------------------------------


def print_summary(trials: dict[str, list[dict]]):
    """Print a text analysis to stdout."""
    print("=" * 72)
    print("oneFill Bottleneck Analysis — 2026-05-31 (testnet)")
    print("=" * 72)

    # ── Dry-run summary ──
    print("\n── 1. DRY-RUN: Plan Phase (quote_fetch only) ──\n")
    for cond in ("A1", "A2", "B1", "B2"):
        ts = trials.get(cond, [])
        if not ts:
            continue
        label = f"{ts[0]['parameters']['venue']}/{ts[0]['parameters']['product']}"
        q_vals = [phase_metric(t, "plan", "quote_fetch_ms") for t in ts]
        q_vals = [v for v in q_vals if v]
        if q_vals:
            print(f"  {cond} ({label:>20s}): "
                  f"median={_median(q_vals):.0f}ms  "
                  f"p95={_p95(q_vals):.0f}ms  "
                  f"n={len(q_vals)}")

    # ── Live summary ──
    if "D1" in trials:
        ts = trials["D1"]
        print(f"\n── 2. LIVE: Binance spot (D1, n={len(ts)}) ──\n")
        for phase, metric in [("plan", "quote_fetch_ms"), ("validate", "balance_fetch_ms"),
                               ("execute", "create_order_ms"), ("execute", "poll_total_ms")]:
            vals = [phase_metric(t, phase, metric) for t in ts]
            vals = [v for v in vals if v]
            if vals:
                print(f"  {phase}.{metric:>20s}: median={_median(vals):.0f}ms  "
                      f"p95={_p95(vals):.0f}ms")

    # ── Dual-leg sequential penalty ──
    print("\n── 3. DUAL-LEG: Sequential fetch penalty ──\n")
    for cond in ("C1", "C2"):
        ts = trials.get(cond, [])
        if not ts:
            continue
        label = ts[0]["parameters"]["product"]
        b_vals = []
        h_vals = []
        totals = []
        for t in ts:
            legs = t.get("timing", {}).get("phases", {}).get("plan", {}).get("legs", {})
            b = legs.get("binance", {}).get("quote_fetch_ms", 0)
            h = legs.get("hyperliquid", {}).get("quote_fetch_ms", 0)
            total = t.get("timing", {}).get("phases", {}).get("plan", {}).get("total_ms", 0)
            if b:
                b_vals.append(b)
            if h:
                h_vals.append(h)
            if total:
                totals.append(total)
        if b_vals and h_vals and totals:
            seq_sum = _median(b_vals) + _median(h_vals)
            print(f"  {cond} ({label}): Binance={_median(b_vals):.0f}ms + "
                  f"HL={_median(h_vals):.0f}ms = {seq_sum:.0f}ms sequential, "
                  f"actual total={_median(totals):.0f}ms")
            print(f"           If concurrent → would be ~{max(_median(b_vals), _median(h_vals)):.0f}ms "
                  f"(saving {seq_sum - max(_median(b_vals), _median(h_vals)):.0f}ms)")

    # ── Bottleneck conclusion ──
    print("\n── 4. BOTTLENECK CONCLUSIONS ──\n")

    # Gather all single-leg data
    binance_q = []
    hl_q = []
    for cond in ("A1", "A2", "B1", "B2"):
        for t in trials.get(cond, []):
            legs = t.get("timing", {}).get("phases", {}).get("plan", {}).get("legs", {})
            for venue, data in legs.items():
                if "quote_fetch_ms" in data:
                    (binance_q if venue == "binance" else hl_q).append(data["quote_fetch_ms"])

    if binance_q and hl_q:
        delta = _median(hl_q) - _median(binance_q)
        print("  Bottleneck #1 — Exchange orderbook API latency:")
        print(f"    Binance fetch_orderbook:  median {_median(binance_q):.0f}ms")
        print(f"    Hyperliquid fetch_orderbook: median {_median(hl_q):.0f}ms")
        print(f"    Delta: +{delta:.0f}ms (Hyperliquid is {_median(hl_q)/_median(binance_q):.1f}x slower)")
        print("    Source: HL testnet API at api.hyperliquid-testnet.xyz")

    if "D1" in trials:
        ts = trials["D1"]
        plan_total = _median([t.get("timing", {}).get("phases", {}).get("plan", {}).get("total_ms", 0) for t in ts])
        val_total = _median([t.get("timing", {}).get("phases", {}).get("validate", {}).get("total_ms", 0) for t in ts])
        exec_total = _median([t.get("timing", {}).get("phases", {}).get("execute", {}).get("total_ms", 0) for t in ts])
        print("\n  Bottleneck #2 — Execute phase dominates end-to-end:")
        print(f"    Plan phase:     {plan_total:.0f}ms ({plan_total/(plan_total+val_total+exec_total)*100:.0f}%)")
        print(f"    Validate phase: {val_total:.0f}ms ({val_total/(plan_total+val_total+exec_total)*100:.0f}%)")
        print(f"    Execute phase:  {exec_total:.0f}ms ({exec_total/(plan_total+val_total+exec_total)*100:.0f}%)")

    print("\n  Bottleneck #3 — Dual-leg quotes are FETCHED SEQUENTIALLY:")
    print("    Planner loop iterates venues one-by-one.")
    print("    Concurrent quote fetch (asyncio.gather) would reduce")
    print("    dual-leg plan time from sum(b, h) to max(b, h).")
    print(f"    Expected saving for C1: ~{_median(binance_q):.0f}ms")
    print("    (the faster leg's time is fully hidden behind the slower one)")

    print("\n  Bottleneck #4 — 500ms poll_interval adds dead time:")
    print("    Executor polls every 500ms even when order fills in ~100ms.")
    print("    A market order that fills in 100ms still pays up to 500ms")
    print("    waiting for the next poll cycle. Consider: WebSocket fill")
    print("    notifications instead of REST polling.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <experiment_dir>")
        print(f"  e.g.  python {sys.argv[0]} benchmark_data/2026-05-31")
        sys.exit(1)

    exp_dir = Path(sys.argv[1])
    raw_dir = exp_dir / "raw"
    if not raw_dir.is_dir():
        print(f"Error: raw/ not found in {exp_dir}")
        sys.exit(1)

    trials = load_trials(raw_dir)
    print(f"Loaded {sum(len(v) for v in trials.values())} trials across "
          f"{len(trials)} conditions: {sorted(trials.keys())}")

    png_path = build_figure(trials, exp_dir)
    print(f"\nChart saved to: {png_path}")

    print_summary(trials)


if __name__ == "__main__":
    main()
