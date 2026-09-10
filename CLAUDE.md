# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Product

**oneFill** — a multi-venue coordinated order execution engine.

A user submits a single CLI command (e.g. "buy $1000 of BTC across Binance and Hyperliquid, 50/50 split"). The system fans out orders to all venues in parallel within milliseconds, and guarantees a **coordinated final state**: either every leg fills, or partial fills get auto-compensated (reverse orders) to bring net exposure close to zero, or the system enters `NEEDS_MANUAL` and blocks further orders.

The product solves a problem human traders have: **manually placing the same order on 3 venues takes 30+ seconds, during which prices move and partial failures leave you with unwanted directional exposure**. oneFill compresses the time window and handles the failure cases.

**oneFill is an execution tool, not a strategy tool.** It does not decide *whether* to trade or *how much* — the user/Agent does that. It executes the user's already-decided intent.

**Phase 1 (current):** CLI tool, hand-driven.
**Phase 2 (future):** Wrap the CLI / Python API as tools for an **Anthropic Claude Agent SDK** agent, so users can express intent in natural language. (Built with the official SDK — never with leaked Claude Code source.)

Read `docs/docs-paradigm.md` before changing documentation or introducing a new project concept. The current product and domain contract is `docs/developer-guide/design/sys-product-requirements.md`; the current implementation snapshot is `docs/developer-guide/reference/current-status.md`.

## Repository status

oneFill is the product; `src/` is now exactly its seven packages plus `__init__.py`.

The predecessor bot — an autonomous volume-farming / arbitrage-monitoring system that used to
live in `src/legacy/` — was deleted. It had no callers, no tests, and its `volume_farming.yaml`
still targeted a venue that no longer existed, so keeping it only let it rot. The code is in git
history (`git log -- src/legacy`); revive it from there if volume farming is ever wanted again.

The verified current surface (commands, modules, test counts) is
`docs/developer-guide/reference/current-status.md`.

## Disk quota / storage

The home directory `/softhome/wangziping` is under a per-user disk quota that is effectively full — writes can fail with `EDQUOT` / `Disk quota exceeded`. Do **not** create venvs, build metadata, bytecode caches, or other generated artifacts there; put them under the shared volume `/share_data/wangziping/`:

- `.venv` is a symlink → `/share_data/wangziping/envs/omnitrade-py310` (create with `uv venv --python 3.10`).
- `setup.cfg` is a symlink → `/share_data/wangziping/omnitrade-build/setup.cfg`. It sets `[egg_info] egg_base` so setuptools writes `omnitrade.egg-info` to `/share_data/wangziping/omnitrade-build` instead of the repo root.
- `data` (SQLite store) and `logs` (JSONL audit) are symlinks → `/share_data/wangziping/omnitrade-data` and `/share_data/wangziping/omnitrade-logs`. The code defaults to the repo-relative `data/` and `logs/`, so keep the symlinks or those writes land in the quota.
- `.pytest_cache` and `.ruff_cache` are symlinks → `/share_data/wangziping/pytest-cache` and `/share_data/wangziping/ruff-cache`. With those in place the `RUFF_CACHE_DIR` below is optional.
- Bytecode cache: set `PYTHONPYCACHEPREFIX=/share_data/wangziping/pycache` before `uv run` / `pytest`.
- Ruff cache: set `RUFF_CACHE_DIR=/share_data/wangziping/ruff-cache` (or pass `--no-cache`).
- Benchmark raw output: `scripts/benchmark.py` writes to `$OMNITRADE_BENCHMARK_DIR/<date>/raw`, defaulting to `/share_data/wangziping/omnitrade-benchmark`. Never point `--output-dir` inside the repo.
- `UV_LINK_MODE=copy` — the uv package cache and the venv are on different filesystems, so hardlinking falls back to copy.

A symlinked path must be ignored **without** a trailing slash in `.gitignore`: a `dir/` pattern matches directories only, so it silently stops matching once the entry becomes a symlink.

Set these before running any `uv` / `pytest` / `ruff` command in this repo, or it fails with `Disk quota exceeded`. (The global `~/.claude/CLAUDE.md` storage-layout convention uses `/share/$USER`; this repo's shared volume is `/share_data/wangziping/`.)

## Commands

### Setup
```bash
# Install uv first (https://docs.astral.sh/uv/) if not already installed
uv sync --extra dev
cp config/secrets.example.yaml config/secrets.yaml
# Edit config/secrets.yaml with your API keys/private keys
```

### Run oneFill (new)
```bash
# Preview a coordinated order without sending it
uv run onefill order --dry-run \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5

# Execute it
uv run onefill order \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5 \
  --max-slippage-pct 0.3

# Query / list / cancel / recover
uv run onefill query <intent-id>
uv run onefill list-intents --status ROLLED_BACK_FAILED
uv run onefill recover
uv run onefill venues
```

### Tests
```bash
uv run pytest                                  # all
uv run pytest tests/exchange -vv                # one module
uv run pytest -m "not network and not slow"    # skip live network tests
uv run pytest tests/coordinator                 # oneFill coordinator only
```

### Lint / Format
```bash
uv run ruff check .          # show issues
uv run ruff check --fix .    # safe auto-fixes
uv run ruff format .         # apply formatting
uv run ruff format --check . # verify formatting (the gate that drifts)
```

A PostToolUse hook runs `ruff check --fix` + `ruff format` on modified .py files after each Write/Edit. The hook only covers files an edit actually touches, so files written before it existed never got swept up — run `ruff format --check .` before committing rather than assuming the hook kept the repo clean.

A commit that is **only** formatting must go into `.git-blame-ignore-revs` (full 40-char SHA), otherwise `git blame` attributes most lines to it. `blame.ignoreRevsFile` is configured locally, and GitHub honours the same file at the repo root.

### Dependency management
```bash
uv add <package>             # runtime dep
uv add --dev <package>       # dev dep
uv sync                      # reinstall from lockfile
uv lock --upgrade            # bump deps
```

## Architecture

依赖方向自下而上——上层消费下层，下层不知道上层。完整的目录树与允许的依赖边见
`docs/developer-guide/standards/directory-structure.md`。

```text
src/cli/           入口：Typer 命令、bootstrap 装配、agent_api 程序化入口
src/strategy/      策略层：框架 + signals/ + algos/ + funding_arb/ price_watch/ backtest/ trade_log/
src/coordinator/   执行内核：Planner → Validator → RiskValidator → Executor → Reconciler
src/market/        市场域对象：Asset · Instrument · NetworkType · Quote · InstrumentRegistry
src/exchange/      交易所接入：BaseExchange · CCXTExchange · ExchangeFactory · OrderbookCache
                   （唯一与 venue 通信的层；可以导入 market，反向禁止）
src/persistence/   SQLite + JSONL；只读写行，不构造领域对象
src/observability/ 指标与结构化日志
```

**改代码前先读对应的权威文档**，本文不重复它们的内容：

| 主题 | 文档 |
|---|---|
| 系统架构、核心工作流、落盘映射 | `docs/developer-guide/design/sys-architecture.md` |
| 产品边界、Intent/Leg、逐腿覆盖、终态 | `docs/developer-guide/design/sys-product-requirements.md` |
| 状态机（Intent / Leg 状态与合法转移） | `docs/developer-guide/design/base-state-machine.md` |
| 协调流程五个阶段 | `docs/developer-guide/design/base-coordination-pipeline.md` |
| 市场层 / 交易所层 / 持久化层 | `docs/developer-guide/design/base-{market,exchange,persistence}-*.md` |
| 策略框架与四个功能域 | `docs/developer-guide/design/strat-*.md` |
| 目录层级、依赖方向、命名 | `docs/developer-guide/standards/` |

### Configuration

- `config/exchanges.yaml` — 每个 venue 的启用开关、网络 URL、费率、symbol。
- `config/risk.yaml` — 盘前限额：单笔最大名义、当日亏损上限、单所敞口、速率限制。
- `config/watchlist.yaml` — `onefill watch` 监控的标的与分类标签。
- `config/secrets.yaml` — 凭据，gitignored。**schema 按 venue 不同**（Binance 用 `apiKey` + `secret`；
  Hyperliquid 用 `walletAddress` + `privateKey`），加载代码必须分支。

## Critical invariants (don't break these)

These are load-bearing properties that future Claude sessions should preserve unless explicitly told otherwise:

1. **Every `create_order` is preceded by a persisted leg row.** Executor must write to SQLite/JSONL before issuing the call. Crash-after-send must be recoverable.
2. **`NEEDS_MANUAL` blocks all subsequent Intents.** Don't add "retry" or "auto-recover from NEEDS_MANUAL" paths — escalation to a human is the design.
3. **Per-leg `product`/`side`/`leverage` override Intent defaults.** `Intent.product`, `Intent.side`, and `Intent.leverage` are defaults — any leg can override them via `LegConfig` (parsed from the `--split` extended syntax). A single Intent can mix spot/perp, buy/sell, and different leverage levels across venues. Spot legs must have leverage=1 (enforced in `Intent.__post_init__`).
4. **The Market layer (`Asset`/`Instrument`/`Quote`) is the only place that knows venue-native symbols.** Higher layers use Instrument objects; CLI uses `--base` and `--quote-preference`. Never let `BTCUSDT` leak into Coordinator code.
5. **Coordinator phases are pure-ish:** Planner and Validator have no side effects. Executor and Reconciler do. Tests rely on this — keep it.

## Pre-removal / pre-cleanup checklist

When deleting a feature, dependency, or config:

1. `grep -ri 'X' . --include="*.py" --include="*.yaml" --include="*.md" --include="*.toml"` for ALL references — code, configs, docs, lockfiles, `pyproject.toml` extras
2. Check both `pyproject.toml` and `uv.lock` for stale deps
3. Check `.gitignore` for any rules that referenced the removed path
4. After delete, run `uv run ruff check .` + `uv run pytest` to surface broken imports / tests
5. Keep `.gitignore` changes in their own commit, not bundled with feature commits

(This checklist exists because past removal sessions left orphan references that needed second-round fixes.)

## Key dependencies

- `ccxt` — async exchange connectivity. [Binance docs](https://docs.ccxt.com/#/exchanges/binance) · [Hyperliquid docs](https://docs.ccxt.com/#/exchanges/hyperliquid)
- `aiohttp` — async HTTP sessions

### Exchange-specific ccxt notes

**Binance:**
- Demo trading (testnet): call `exchange.enable_demo_trading(True)` **after** constructing the ccxt instance, **before** `load_markets()`. This swaps `urls.api` → `urls.demo` (demo-api.binance.com). ccxt 4.5.54 supports this natively.
- Demo mode does NOT support sapi/margin endpoints (ccxt internal comment at binance.py:2940). Use `fetchMarkets: ['spot']` option.
- `CCXTExchange.connect()` auto-enables demo mode when `self.name == "binance"` and `self.network_type == TESTNET`.
- Auth: HMAC (`apiKey` + `secret`). Ed25519 keys are not supported by ccxt.

**Hyperliquid:**
- Testnet: set `options['testnet'] = True`. `CCXTExchange._build_ccxt_config` handles this.
- Auth: `walletAddress` + `privateKey` (Ethereum-style hex). Optional `vaultAddress`.
- ccxt defaults to `swap` (perpetual) market type — correct for Hyperliquid.
- `pytest` / `pytest-asyncio` — `asyncio_mode = auto` set in `pyproject.toml`
- `ruff` — lint + format, configured in `pyproject.toml`

For current dependencies and validation commands, see `docs/developer-guide/standards/testing.md` and `docs/developer-guide/reference/current-status.md`.
