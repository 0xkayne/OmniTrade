# oneFill Benchmark Experiments

## Purpose

Rigorously measure the end-to-end latency breakdown of the oneFill execution
pipeline across venues (Binance, Hyperliquid), products (spot, perp), and
leg configurations (single, dual). The data answers: *where is the time spent,
and what explains the gap between Binance (~700ms) and Hyperliquid (~3000ms)?*

## Directory Structure

```
benchmark_data/
  README.md              # this file
  YYYY-MM-DD/            # one experiment session
    raw/                 # raw JSON per trial (one file per trial)
      A1_001.json
      A1_002.json
      ...
    summary.json         # aggregated statistics across all trials
```

## How to Run

### Prerequisites

- Valid API keys in `config/secrets.yaml`
- Instrument cache populated (run `onefill order --dry-run ...` once first)

### Dry-run benchmark (safe, no orders sent)

```bash
# Full experiment: all 6 conditions, 20 trials each = 120 trials
uv run python scripts/benchmark.py run --mode dry-run --trials 20

# Quick smoke test: 3 trials
uv run python scripts/benchmark.py run --mode dry-run --trials 3
```

### Live benchmark (real testnet orders)

```bash
# Binance spot only, 10 trials
uv run python scripts/benchmark.py run --mode live --venue binance --product spot --trials 10

# Hyperliquid spot only
uv run python scripts/benchmark.py run --mode live --venue hyperliquid --product spot --trials 10
```

### Analyze results

```bash
uv run python scripts/benchmark.py analyze benchmark_data/2026-05-31
```

This produces `summary.json` with per-condition, per-metric statistics.

## Experiment Design

### Conditions

| ID | Venue | Product | Legs | Trials (dry) | Trials (live) |
|----|-------|---------|------|-------------|--------------|
| A1 | binance | spot | 1 | 20 | — |
| A2 | hyperliquid | spot | 1 | 20 | — |
| B1 | binance | perp | 1 | 20 | — |
| B2 | hyperliquid | perp | 1 | 20 | — |
| C1 | both | spot | 2 | 20 | — |
| C2 | both | perp | 2 | 20 | — |
| D1 | binance | spot | 1 | — | 10 |
| D2 | hyperliquid | spot | 1 | — | 10 |

Fixed: `base=BTC`, `side=buy`, `type=market`, `notional=$100`, `network=testnet`,
`max_slippage_pct=0.5`.

### Protocol

1. **Bootstrap once** — `build_orchestrator()` is called once and reused for all
   trials. Instrument cache must already be warm (24h TTL).
2. **Warmup** — 3 dry-run trials (not recorded) to eliminate cold-start variance.
3. **Trial order randomised** — prevents time-of-day drift from confounding results.
4. **Inter-trial delay** — 500ms to avoid ccxt rate limiter backpressure.
5. **Statistics** — median ± IQR, min, max, p95 reported per metric.

### Metrics Collected

From each trial's `TimingCollector.to_dict()`:

| Metric | What it measures |
|--------|-----------------|
| `bootstrap_ms` | `build_orchestrator()` latency (recorded once) |
| `plan.quote_fetch_ms` | Orderbook fetch HTTP round-trip |
| `plan.cpu_ms` | Planner CPU computation |
| `validate.balance_fetch_ms` | Balance fetch HTTP round-trip |
| `validate.cpu_ms` | Validator CPU computation |
| `execute.create_order_ms` | Order placement HTTP round-trip |
| `execute.poll_total_ms` | Sum of all fill-poll HTTP round-trips |
| `execute.poll_attempts` | Number of poll cycles |
| `execute.set_leverage_ms` | Leverage configuration HTTP (perp only) |
| `total_ms` | End-to-end pipeline (excludes bootstrap) |

## Reproducibility

- All trials use the same fixed parameters (notional, side, base asset).
- Trial order is shuffled with a fixed `random.seed()` (edit `benchmark.py` to
  pin the seed for exact reproducibility across runs).
- Raw data is preserved at `benchmark_data/<date>/raw/*.json` for independent
  re-analysis.
