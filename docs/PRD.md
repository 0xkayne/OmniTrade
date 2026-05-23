# oneFill — PRD

> Multi-venue coordinated order execution.
> Status: Draft v0.2 · Author: kayne · Date: 2026-05-23
>
> Changelog:
> - v0.2: Add Instrument abstraction (§4.5–§4.8) to handle per-venue
>   symbol/quote/product/microstructure differences. Switch order
>   sizing to USD notional. Switch Intent to "base asset + quote
>   preference" model.
> - v0.1: Initial draft.

---

## 1. 问题陈述

### 1.1 用户痛点

加密交易者经常需要**同时在多个交易所对同一标的下单**,典型场景:

- 在 N 个交易所同时建仓某币(分散风险、规避单一交易所流动性限制、刷多个交易所积分)
- 跨 CEX/DEX 同时进场以避免单一交易所的对手方风险
- 大额订单按比例拆到多个交易所减少单一深度冲击

**手动操作的核心问题:**

| 问题 | 后果 |
|---|---|
| 人手切换交易所、复制粘贴、点击下单,**时间误差可达 10~60 秒** | 每个交易所成交价不同,造成跨所价差暴露 |
| 部分下单失败时无法及时回滚 | 单边暴露 → 行情逆向时直接亏损 |
| 滑点/手续费/资金费率难以并行评估 | 不知道哪个交易所成本最低,常常事后才发现 |
| 永续合约还要单独看资金费率窗口 | 时间踩错可能多付一笔 funding |

### 1.2 oneFill 要做什么

**一句话:** 用户通过单条 CLI 指令表达"在多个交易所同时执行某笔交易",系统在**毫秒级时间窗口内**并发下单,并保证**最终一致性**(要么全部成交,要么全部撤销/补偿至接近零暴露)。

**核心价值:**

1. **时间一致性** — 所有腿在 < 500ms 内同时发出请求
2. **协调失败处理** — 任何腿失败后自动补偿,用户看到的状态是"成功" 或 "已回滚至接近零暴露 + 详细报告"
3. **执行前预估** — 在真正下单前,告诉用户每个交易所的预期成交价、滑点、手续费、(合约时)资金费率,用户可基于预估决定是否执行
4. **可恢复** — 服务崩溃/重启后,未完成的订单意图保留在状态机中,可手动 resume 或 abort

### 1.3 不做什么(Non-goals)

- **不做策略生成** — 用户给"在 A/B/C 三个所各下 0.3/0.4/0.3 个 BTC"这种明确指令,系统不会自动判断"是否该下单"或"该下多少"
- **不做做市/套利** — 这不是策略机器人,是执行器
- **不做高频** — 目标响应延迟在百毫秒级,而非微秒级
- **不做账户管理** — 资金转账、提现等不在范围
- **第一阶段不做 GUI/Web 面板** — 仅 CLI

---

## 2. 用户角色与典型场景

### 2.1 用户角色

| 角色 | 关键诉求 |
|---|---|
| **个人交易者** | 一条命令在自己常用的 2-4 个交易所同时建/平仓 |
| **空投/积分玩家** | 同时在多个 Perp DEX 刷量,但不希望像传统刷量机器人那样跑无人值守循环 |
| **(第二阶段)Agent 用户** | 自然语言("帮我在 Binance 和 Hyperliquid 各买 0.05 BTC") → Agent 调 CLI |

### 2.2 典型场景示例

**场景 A:现货分散建仓**
```bash
onefill order \
  --symbol BTC/USDT --side buy --type market \
  --total-amount 0.1 \
  --split binance=0.4,okx=0.6 \
  --max-slippage 0.3%
```

**场景 B:永续合约对冲建仓**
```bash
onefill order \
  --symbol BTC/USDT --side buy --type market --product perp \
  --total-amount 0.2 --leverage 3 \
  --split binance=0.5,hyperliquid=0.5 \
  --max-slippage 0.5% --max-funding-rate 0.01%
```

**场景 C:dry-run 预估(不真实下单)**
```bash
onefill order --dry-run \
  --symbol ETH/USDT --side sell --type market \
  --total-amount 1.0 \
  --split binance=0.5,hyperliquid=0.5
# 输出每个 venue 的预估成交价、滑点、手续费、净 PnL
```

---

## 3. 核心概念

### 3.1 术语

| 术语 | 定义 |
|---|---|
| **Asset** | 用户视角的资产(如 "BTC"),与 venue 和 quote 无关。详见 §4.5 |
| **Instrument** | 系统的最小可下单单位 = (venue, market_type, base, quote) 四元组。详见 §4.5 |
| **Quote** | 某 Instrument 在某瞬间的市场快照(价格、深度、fee、funding)。详见 §4.5 |
| **Intent** | 用户提交的下单意图(base + quote_preference + product + side + total_notional_usd + split + 限制条件) |
| **Plan** | 由 Intent 派生的执行计划,Planner 为每个 venue 选定 Instrument 后,产出每个 leg 的预估数据(数量、成交价、滑点、fee、funding) |
| **Leg** | Plan 中针对单个 venue 的一笔订单(对应一个 Instrument) |
| **Execution** | 一次 Plan 真正下单的运行实例,持有状态机 |
| **Reconciliation** | 部分失败后的补偿动作(用反向市价单冲掉已成交腿) |
| **Coordinated atomicity** | "尽力补偿" 的最终一致性,**不是分布式 ACID 事务**。承诺的是:Execution 终态要么是 `ALL_FILLED`,要么是 `ROLLED_BACK`(已成交腿被反向冲销)或 `NEEDS_MANUAL`(补偿失败,需人工介入) |

### 3.2 重要澄清:为什么不叫"原子操作"

**真正的跨系统原子性需要 2PC、共享事务日志、可回滚的写入**,这些交易所都不提供。我们能做的是:

1. **预先验证** 减少进入 execute 阶段后失败的概率
2. **并发执行 + 快速补偿** 在失败发生后尽快冲销已成交腿
3. **完整审计** 任何状态都可追溯、可重放

文档和代码中**不使用 atomic / atomicity 一词**,统一用 **coordinated** / **coordinated execution**。

---

## 4. 系统架构(逻辑视图)

```
┌─────────────────────────────────────────────────────────┐
│                     CLI Layer                            │
│  onefill order / query / cancel / recover / dry-run     │
└────────────────────────┬────────────────────────────────┘
                         │ (Intent)
┌────────────────────────▼────────────────────────────────┐
│                 Coordinator (核心)                       │
│                                                          │
│  Planner ──→ Validator ──→ Executor ──→ Reconciler      │
│                                                          │
│  状态机: PENDING → VALIDATED → EXECUTING                 │
│         → ALL_FILLED                                     │
│         → PARTIAL_FILLED → ROLLING_BACK → ROLLED_BACK   │
│         → ROLLED_BACK_FAILED (= NEEDS_MANUAL)           │
│         → REJECTED (Validator 阶段拒绝)                  │
└────┬───────────────┬───────────────┬────────────────────┘
     │               │               │
     ▼               ▼               ▼
┌──────────┐  ┌──────────────┐  ┌─────────────────────────┐
│Persistence│  │Exchange Layer │  │Observability            │
│SQLite +   │  │BaseExchange + │  │structured logs +        │
│JSONL audit│  │ Binance,       │  │metrics(预留 Prometheus) │
│           │  │ Hyperliquid,  │  │                         │
│           │  │ ... adapters  │  │                         │
└──────────┘  └──────────────┘  └─────────────────────────┘
```

**复用现有代码:** `BaseExchange`、`CCXTExchange`、`LighterExchange` 全部保留。新写的 Coordinator 把它们当作下层 API 使用。

**旧代码并行保留:** `VolumeEngine`、`HedgeVolumeStrategy`、`ArbitrageEngine` 不动,作为 legacy mode 继续可运行,新 CLI 与之并列。

---

## 4.5 关键抽象:Asset / Instrument / Quote

跨交易所执行的复杂度,**绝大部分来自每个交易所对"同一资产"的表达方式不同**。下面四层抽象是整个系统的脊梁,Coordinator、CLI、持久化都建立在它们之上。

### 4.5.1 差异的四个层次

| 层次 | 例子 | 影响 |
|---|---|---|
| L1: 上线币种不同 | Lighter 没有 SOL perp,Hyperliquid 有 | 同一资产并非在所有 venue 可交易 |
| L2: 同币不同 quote | Binance 现货有 `BTC/USDT`、`BTC/USDC`、`BTC/FDUSD`;Hyperliquid perp 是 `BTC/USDC:USDC`;某些 DEX 是 `BTC/USDH` | 用户说"BTC"时,系统必须**选哪个交易对** |
| L3: 现货 vs 合约 | Binance `BTC/USDT` 现货 和 `BTC/USDT` 永续是**两个独立市场**,深度、价格、手续费、是否有 funding 全不同 | 一个 Intent 必须明确市场类型 |
| L4: 同交易对不同 venue 的微观结构 | `BTC/USDT` 在 Binance 与在 Hyperliquid 完全是两回事 — 深度曲线、taker fee、最小下单单位、价格精度、(perp)funding rate 全不同 | Plan 阶段必须分别评估,不能"全用一个价" |

### 4.5.2 核心概念

#### Asset(资产)

```
Asset(symbol="BTC", kind="crypto")
```

用户视角的统一锚点。用户脑子里想的是"BTC",而不是 `BTC/USDT`。Asset 不绑定任何 venue 或 quote。

#### Instrument(可交易品种)

```python
Instrument(
    venue="binance",
    market_type="spot" | "perp" | "futures",
    base_asset=Asset("BTC"),
    quote_asset=Asset("USDT"),
    venue_symbol="BTCUSDT",        # 该 venue 上的原生 symbol
    contract_size=1.0,             # perp 场景
    min_qty=0.00001,
    qty_step=0.00001,
    price_step=0.01,
    fee_schedule=FeeSchedule(...),
    is_inverse=False,              # USD-margined vs Coin-margined
    listing_status="trading",      # trading / delisted / preopen
)
```

**Instrument 是系统的最小可下单单位。** 任何"下单"在内部都是"对某个 Instrument 下单"。

定义:**(venue, market_type, base, quote) 四元组唯一确定一个 Instrument**。同 base 不同 quote → 不同 Instrument;现货 vs 合约 → 不同 Instrument。

#### InstrumentRegistry(品种注册中心)

启动时调用每个 venue 的 markets API,把所有可交易 Instrument 加载入内存:

```python
registry.list_instruments(
    base="BTC",
    market_type="spot",
    venue="binance",
)
# → [Instrument(BTC/USDT, spot), Instrument(BTC/USDC, spot), ...]

registry.find_one(
    base="BTC",
    venue="hyperliquid",
    market_type="perp",
    quote_preference=["USDC", "USDH"],
)
# → Instrument(BTC/USDC:USDC, perp)  # 按 preference 第一个命中
```

**缓存策略:** 启动时拉一次,缓存 12–24h(下个版本可加后台刷新)。新上线币种须重启服务才能识别 — MVP 可接受。

#### Quote(实时市场快照)

一个 Instrument 在某一瞬间的微观状态。**Plan 阶段对每个候选 Instrument 拉一次 Quote**,用于估值与决策:

```python
Quote(
    instrument=Instrument(...),
    fetched_at=datetime,

    # Top of book
    bid_price, bid_size,
    ask_price, ask_size,
    mid_price,

    # 深度穿透估算(Plan 阶段最关键的字段)
    estimate_fill(amount_base, side) -> EstimatedFill(avg_price, slippage_pct, depth_consumed_levels)

    # Fee(基于 venue + 用户配置的 VIP tier)
    taker_fee_rate, maker_fee_rate,

    # Perp 场景独有
    funding_rate,                  # 当前资金费率
    next_funding_time,             # 下一次结算时刻
    open_interest,
)
```

### 4.5.3 Intent 接收 base+quote_preference,而非 symbol

旧设计 `--symbol BTC/USDT` 已废弃。新 Intent 字段:

```python
Intent(
    base="BTC",                        # 用户视角的资产
    quote_preference=["USDT", "USDC", "USDH"],  # quote 选择优先级
    product="spot" | "perp",            # 市场类型,一个 Intent 内只允许一种(见 §4.5.5)
    side="buy" | "sell",
    type="market" | "limit",
    total_notional_usd=1000.0,         # 以 USD 名义价值计价(见 §4.5.6)
    split={                            # 用户手动指定每个 venue 的比例
        "binance": 0.4,
        "hyperliquid": 0.6,
    },
    # ... 限制阈值同前(max_slippage / max_fee / max_funding_rate)
)
```

### 4.5.4 Instrument 选择算法(Planner 内部)

对 Intent 中的每个 venue:

```
1. registry.list_instruments(
     base=intent.base,
     market_type=intent.product,
     venue=venue,
   )
   → 候选 Instrument 列表(可能多个 quote)

2. 按 intent.quote_preference 顺序遍历:
     for pref_quote in intent.quote_preference:
       for candidate in 候选:
         if candidate.quote == pref_quote and account_has_balance(venue, pref_quote):
           selected = candidate
           break

3. 如果 selected 为 None:
     → Plan 阶段 reject 这个 venue,
       报告"BTC/{preferences} 在 venue 上都不可交易或账户无对应 quote 余额"
```

**关键点:** Plan 报告里**必须显示**为什么选了这个 Instrument(命中了哪个 preference、跳过了哪些候选、跳过原因),让用户能事后审查。

### 4.5.5 同一 Intent 不混 spot 和 perp

**约束:** `Intent.product` 是单值字段(`spot` 或 `perp`),整个 Intent 的所有 leg 都用同一种市场类型。

**理由:**
1. **仓位语义不同** — 现货是真实持仓,永续是合约持仓(可平),用户对"对冲""平仓"等概念预期完全不同
2. **资金科目不同** — 现货是 quote 货币消耗,永续是保证金占用
3. **风险参数不同** — 永续有杠杆、清算价、funding 成本,现货没有

如果用户真要"现货 + 永续同时下",**发两条 Intent**。这样语义清晰,审计也清晰。

### 4.5.6 数量单位:USD 名义价值

用户通过 `--total-notional-usd` 指定**总下单的美元名义价值**,系统在 Plan 阶段:

1. 用每个 venue 当前 mid price 把 USD 数量换算成 base 货币数量
2. 按 `--split` 比例拆分到每个 venue
3. 对齐到该 venue Instrument 的 `qty_step`,记录因取整产生的微小差异

**理由:**
- **跨 venue 计量一致** — `BTC/USDT` 和 `BTC/USDC` 价格略不同,用 USD 名义值才能保证用户预期的"我要花多少钱"是准确的
- **永续合约友好** — perp 仓位天然以 USD 名义价值衡量
- **空投玩家友好** — 刷量目标本来就是按 USD 算的

> 注意 USDT/USDC/USDH 等稳定币短期都按 1 USD 估算,长期(>1小时)的脱锚不在 MVP 处理范围。Plan 报告会标注"以 USDT ≈ USD = 1.0 估算"。

### 4.5.7 CLI 接口的修订

新 `order` 命令参数(替换 §6.2):

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `--base` | str | ✅ | 资产符号(`BTC`),不带 quote |
| `--quote-preference` | str | ❌ | 逗号分隔的偏好 quote 列表(默认 `USDT,USDC,USDH,USDE`) |
| `--product` | enum | ✅ | `spot` / `perp`,一个 Intent 内统一 |
| `--side` | enum | ✅ | `buy` / `sell` |
| `--type` | enum | ✅ | `market` / `limit` |
| `--total-notional-usd` | float | ✅ | 总下单美元名义价值 |
| `--split` | str | ✅ | `venue1=ratio,venue2=ratio`,和为 1.0 |
| `--leverage` | int | ❌ (perp) | 默认 1 |
| `--price` | float | ❌ (limit) | 限价单价格(若多 venue 限价单语义复杂,MVP 可只支持单 venue 限价) |
| `--max-slippage` / `--max-fee` / `--max-funding-rate` | str | ❌ | 同前 |
| `--dry-run` / `--yes` / `--json` | flag | ❌ | 同前 |

**示例:**

```bash
# 用 1000 USD 在 Binance 和 Hyperliquid 现货上买 BTC,quote 偏好 USDT
onefill order \
  --base BTC --quote-preference USDT,USDC \
  --product spot --side buy --type market \
  --total-notional-usd 1000 \
  --split binance=0.5,hyperliquid=0.5 \
  --max-slippage 0.3%

# Plan 输出示例
Plan for "buy $1000 of BTC across binance, hyperliquid (spot)":

  binance:
    selected:  BTC/USDT (spot)  [why: quote=USDT matched preference[0], balance=$2341]
    skipped:   BTC/USDC (would match preference[1], not reached)
    notional:  $500.00  →  0.00744 BTC @ 67224.50
    estimated fill:  67234.50  (slippage 0.08%, depth crossed: 3 levels)
    estimated fee:   $1.34  (taker 0.10%, no maker portion)
    final size:      0.00744 BTC (qty_step=0.00001, no rounding loss)

  hyperliquid:
    selected:  BTC/USDC:USDC (perp)  [⚠ this Intent requested product=spot, but venue
               has no spot market — REJECTED]
    ...

→ Plan REJECTED: hyperliquid has no spot BTC instrument.
  Suggestion: either drop hyperliquid from --split,
  or use --product perp.
```

(上面的例子也展示了:当 venue 没有对应 product 的市场时,**Plan 阶段直接 reject** 整单。这是 §4.5.5 的强约束的直接表现。)

### 4.5.8 数据模型新增字段

`legs` 表新增:

| 字段 | 说明 |
|---|---|
| `instrument_venue_symbol` | 实际下单时使用的 venue-native symbol(如 `BTCUSDT`) |
| `instrument_base` / `instrument_quote` | base / quote asset symbol |
| `instrument_market_type` | spot / perp / futures |
| `quote_preference_matched` | 命中的 quote(如 `USDT`),用于审计"为何选了这个交易对" |
| `instrument_selection_log` | JSON,记录候选清单和跳过原因 |
| `planned_notional_usd` | 该 leg 的 USD 名义价值 |
| `planned_qty_base` | 转换并对齐 qty_step 后的实际数量 |
| `funding_rate_at_plan` | (perp) Plan 阶段记录的 funding rate |
| `next_funding_time_at_plan` | (perp) 同上 |

---

## 5. 核心流程详解

### 5.1 Plan 阶段(无副作用)

**输入:** Intent(用户原始指令)
**输出:** Plan(每个 venue 的预期 leg + 总体预估)
**副作用:** 无

**做的事:**

1. 把用户提供的 `--split` 比例展开成每个 venue 的目标数量
2. 对每个 venue 并发执行只读查询:
   - 拉 orderbook → 估算 fill price(按目标数量穿透 N 档)
   - 拉 fee schedule(基于配置或交易所 API)
   - 永续合约场景:拉 funding rate + 下个 funding 时刻
3. 计算每 leg 的预估指标 + 总体指标:
   - 预估成交价
   - 滑点(vs. mid price)
   - 手续费(USD)
   - 资金费率(永续合约场景)
4. 卡用户给的阈值:`--max-slippage` / `--max-funding-rate` / `--max-fee`
5. **任何 leg 超阈值 → 直接 reject Intent**,不进入 Validator

### 5.2 Validate 阶段(只读检查)

**输入:** Plan
**输出:** 验证通过 / 失败 + 失败原因清单
**副作用:** 无(纯查询)

并发对每个 venue 检查:

- ✅ Symbol 在 venue 存在且可交易
- ✅ 账户余额 ≥ 预估保证金/成本
- ✅ 数量符合 venue 的 min/max/step 规则
- ✅ 杠杆设置可行(perp 场景)
- ✅ 价格(限价单)在合理范围

**有一条失败 → 整单 reject**,不进入 Execute。失败原因清单完整返回给用户。

### 5.3 Execute 阶段(产生副作用)

**输入:** 通过 Validate 的 Plan
**输出:** 每个 leg 的 fill 结果
**副作用:** 真实下单 ⚠️

执行步骤:

1. 把 Plan 持久化到 SQLite,状态 `EXECUTING`,所有 leg 状态 `PENDING_SEND`
2. **并发** 对每个 venue 调 `create_order`(`asyncio.gather`)
3. 每个 leg 拿到 order id 后,持久化为 `SENT`
4. **并发轮询** 每个 leg 的成交状态(或订阅 WS),直到:
   - 全部 filled → 状态 `ALL_FILLED`,完成
   - 任意一个 timeout/rejected/canceled → 进入 Reconcile

**关键约束:**

- 步骤 2 的并发发起到所有请求出网的时间差 < 50ms(asyncio 同步发起,不串行 await)
- 步骤 4 轮询周期 500ms,总等待预算可配置(默认 30s)

### 5.4 Reconcile 阶段(补偿)

**触发条件:** Execute 阶段任意 leg 失败 或 超时未成交

**逻辑:**

```
for each leg:
    if status == FILLED:
        发起反向市价单冲销(close 多 → 卖,close 空 → 买)
        持久化 compensation_order_id
    elif status == PARTIAL_FILLED:
        按已成交数量发起反向市价单
    elif status == PENDING/SENT but not filled:
        尝试 cancel
    elif status == REJECTED/FAILED:
        no-op
```

**补偿成功:** 状态 `ROLLED_BACK`,残余暴露 ≈ 0(可能有少量滑点损失,记录在审计日志)

**补偿失败:** 状态 `ROLLED_BACK_FAILED` = `NEEDS_MANUAL`,**阻塞后续所有 Intent**(避免连环暴露),写入告警日志,等人工介入

---

## 6. CLI 接口契约

### 6.1 命令列表

```bash
onefill order [options]              # 下单(主命令)
onefill query <intent-id>            # 查询某个 Intent 当前状态
onefill list [--status STATUS]       # 列出 Intent
onefill cancel <intent-id>           # 取消未完成的 Intent
onefill recover                      # 列出 NEEDS_MANUAL 的 Intent,引导处理
onefill venues                       # 列出当前可用 venue + 连接状态
onefill --version | --help
```

### 6.2 `order` 命令参数

参数列表与示例见 **§4.5.7 CLI 接口的修订**(因为参数与 Asset/Instrument 抽象强相关,放在抽象章节一起讲更连贯)。

### 6.3 退出码

| 退出码 | 含义 |
|---|---|
| 0 | `ALL_FILLED` |
| 1 | 通用错误(参数错、网络异常) |
| 2 | `REJECTED`(Validate 阶段拒绝) |
| 3 | `ROLLED_BACK`(部分失败已成功补偿) |
| 4 | `NEEDS_MANUAL`(需要人工介入) |

### 6.4 输出格式

默认人类可读 + 颜色。`--json` 切换为机器可读 JSON(给 Agent 用):

```json
{
  "intent_id": "intent_2026...",
  "status": "ALL_FILLED",
  "legs": [
    {"venue": "binance", "order_id": "...", "filled_amount": 0.04, "avg_price": 67234.5, "fee": 2.69},
    {"venue": "hyperliquid", "order_id": "...", "filled_amount": 0.06, "avg_price": 67241.2, "fee": 2.02}
  ],
  "total_filled": 0.1,
  "weighted_avg_price": 67238.5,
  "total_fee_usd": 4.71,
  "compensation": null,
  "duration_ms": 873
}
```

---

## 7. 状态机与持久化

### 7.1 Intent 状态机

```
              ┌──→ REJECTED (Plan/Validate 失败)
PENDING ──→ VALIDATED ──→ EXECUTING ──┬──→ ALL_FILLED
                                       ├──→ PARTIAL_FILLED ──→ ROLLING_BACK ──┬──→ ROLLED_BACK
                                       │                                       └──→ ROLLED_BACK_FAILED
                                       └──→ EXECUTE_TIMEOUT ──→ ROLLING_BACK (同上)
```

**终态:** `REJECTED`, `ALL_FILLED`, `ROLLED_BACK`, `ROLLED_BACK_FAILED`

### 7.2 持久化模型

**SQLite 表:**

```sql
intents (intent_id PK, status, created_at, updated_at, raw_intent_json)
legs (leg_id PK, intent_id FK, venue, planned_amount, status,
      sent_at, order_id, filled_amount, avg_price, fee, error_msg,
      compensation_order_id, compensation_filled_amount)
audit_events (id PK, intent_id, timestamp, event_type, payload_json)
```

**JSONL 审计日志:** `logs/audit-YYYY-MM-DD.jsonl`,每行一个事件(下单/状态变更/补偿),append-only,用于事后重放和 debug。

**为什么双层:** SQLite 提供事务和查询,JSONL 提供 append-only 完整记录(SQLite 出问题时还能从 JSONL 恢复)。

### 7.3 重启恢复

服务启动时:

1. 扫 SQLite 查所有非终态 Intent
2. **不自动续作** — 因为不知道距上次崩溃多久了,行情可能已变
3. 把这些 Intent 列入 `onefill recover` 输出,等用户决定(continue / cancel / mark as manual)

---

## 8. 非功能性需求

### 8.1 性能

| 指标 | 目标 | 备注 |
|---|---|---|
| Plan + Validate 总耗时 | < 1s | 网络好的情况下 |
| Execute 阶段所有腿发起时间差 | < 50ms | 同一 asyncio 事件循环内 |
| Reconcile 启动延迟(从检测到失败) | < 200ms | 不等其他 leg 慢慢 timeout |

### 8.2 资金安全

- **永远先持久化再下单** — 任何 `create_order` 调用前,leg 状态必须先写 SQLite + JSONL
- **`NEEDS_MANUAL` 状态阻塞后续下单** — 防止单边暴露累积
- **Reconcile 失败不重试** — 重试可能造成更多暴露,统一交人工
- **没有 secret 落盘** — `secrets.yaml` 不动,API key 仅在内存

### 8.3 可观测性

- 结构化日志(JSON,可被 Loki/Datadog 采集)
- 关键指标埋点(可选 Prometheus exporter,后期加):
  - Intent 总数 / 各状态计数
  - Plan-to-Fill 时延分布
  - 各 venue 错误率
  - Reconcile 触发率 / 成功率

### 8.4 测试要求

- Coordinator 各阶段必须有单元测试(mock exchange)
- 至少一个端到端 testnet 测试(Binance + Hyperliquid)
- Reconcile 路径必须有"故意让一腿失败"的测试(注入失败)

---

## 9. MVP 范围

### 9.1 第一阶段(MVP)— oneFill CLI

**覆盖:**

- ✅ 现货市价单 / 现货限价单
- ✅ 永续合约市价单 / 永续合约限价单
- ✅ Binance + Hyperliquid 两个 venue
- ✅ CLI 一次性指令(无 REPL)
- ✅ **Asset / Instrument / Quote 三层抽象**(§4.5),InstrumentRegistry 启动加载 + 缓存 12-24h
- ✅ 用户传 `--base` + `--quote-preference`,系统按偏好顺序自动选 Instrument
- ✅ 用户手动指定拆单比例(`--split`)
- ✅ 下单量以 USD 名义价值计(`--total-notional-usd`)
- ✅ 同一 Intent 内 product 单值(spot 或 perp,不混)
- ✅ Plan / Validate / Execute / Reconcile 全流程
- ✅ SQLite + JSONL 持久化(含 instrument 选择审计)
- ✅ 重启后 `onefill recover` 列出未完成 Intent

**不在 MVP:**

- ❌ 自动智能拆单
- ❌ TWAP/VWAP/Iceberg 等高级订单类型
- ❌ Web UI
- ❌ Lighter 等 DEX(可在第一阶段完成后增量加)
- ❌ Prometheus exporter(预留接口,实现延后)

### 9.2 第二阶段(愿景,本 PRD 不深入设计)

基于 **Anthropic Claude Agent SDK** 构建自然语言 Agent:

- Agent 把 oneFill CLI(或直接把 oneFill 的 Python 函数)注册为 tool
- 用户自然语言输入意图 → Agent 规划 → 调 tool 执行
- 复用 oneFill 的状态机、持久化、补偿逻辑 — Agent 只是新的入口

**第二阶段的 PRD 在第一阶段稳定后再单独写。**

> 注:Claude Code 是 Anthropic 的商业闭源产品,其代码即使外泄也受版权保护。本项目第二阶段使用 Anthropic 官方提供的 Claude Agent SDK(Python / TS),不复用任何 Claude Code 私有代码。

---

## 10. 风险与开放问题

| 风险 | 缓解 |
|---|---|
| 交易所 API 速率限制可能在并发下单时被触发 | 每个 venue 用独立 session;预留 backoff 策略 |
| Reconcile 反向单本身可能失败(行情快速变化、深度不够) | `NEEDS_MANUAL` 状态机 + 阻塞后续 Intent + 告警 |
| 部分交易所(尤其 DEX)的订单状态查询延迟较高,影响 Reconcile 决策 | Execute 阶段超时阈值可按 venue 调优 |
| 滑点估算与真实成交价偏差 | 提供 `--max-slippage` 卡阈值;事后审计日志记录 estimated vs actual |
| 同一 base 在不同 venue 可能没有相同 quote(用户偏好的 USDT 在某 DEX 不存在) | `--quote-preference` 列表回退机制;Plan 阶段透明展示选择过程;无任何匹配则该 venue 被 reject |
| InstrumentRegistry 缓存过期导致新上线币种暂时不可见 | MVP 文档说明"启动加载,12-24h 过期";后续版本加后台刷新 |
| 稳定币短期脱锚(USDT 不再 = $1)影响 USD 名义价值估算 | Plan 输出明确标注"按 USDT≈USD=1.0 估算";MVP 不处理脱锚场景 |

**待回答的开放问题:**

- [ ] perp 场景下,多 venue 同时建仓的"等效仓位"该如何核对?(USD 名义价值 vs 张数)
- [ ] 是否需要支持 IOC / FOK / Post-only 等高级订单标志?(MVP 暂不支持)
- [ ] 多账户场景(同一 venue 多个 sub-account)是否需要支持?(MVP 不支持)

---

## 11. 验收标准(第一阶段)

满足以下全部条目即视为 MVP 完成:

1. ✅ `onefill --version` 与 `onefill venues` 可运行
2. ✅ `onefill order --dry-run` 可输出完整 Plan(含滑点/手续费/资金费率预估)
3. ✅ Binance + Hyperliquid 双 venue testnet 上能 100 次连续无误地完成 `ALL_FILLED`
4. ✅ 注入"Hyperliquid 失败"的故障测试 → 系统正确进入 `ROLLED_BACK` 状态,Binance 已成交腿被反向冲销至残余 < 0.5%
5. ✅ 服务运行中 kill -9 → 重启后 `onefill recover` 列出受影响 Intent
6. ✅ `onefill order --json` 输出符合契约的 JSON
7. ✅ Coordinator 各阶段有 ≥ 80% 单测覆盖
8. ✅ README + CLAUDE.md 反映新架构,提供完整 quickstart
