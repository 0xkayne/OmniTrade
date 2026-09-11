---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-11
applies_to: src/strategy/trade_log/ and onefill trades
---

# 交易台账

手工交易台账是用户自己的**逐笔交易流水**，用于事后复盘和策略分析。

它和 oneFill 的 Intent 体系**没有关系**：一笔台账不代表系统发过单，系统发过的单也不会
自动出现在台账里。没有任何自动推导——要记就显式记一笔。这个边界是刻意的：
台账记录的是用户的真实决策（含理由、标签），而不是执行引擎的副产品。

## 组件与数据流

<figure markdown="span">
  <img src="../../../assets/strat-trade-log.svg" alt="strat-trade-log" width="100%">
</figure>

（图源码 `docs/assets/strat-trade-log.dot`，重新生成：`scripts/render_diagrams.sh strat-trade-log`）

**两个写入方写同一张表，这是刻意的**：Telegram `/log` 与 CLI `trades record` 产生的行结构完全一致，
所以事后复盘不必区分来源。代价是 `price_watch → trade_log` 成为策略层里**唯一**的功能域间依赖
（见[策略框架](strat-framework.md) 的数据流图）。

### 不负责什么

| 不负责 | 归谁 |
|---|---|
| 从 Intent/Leg 自动推导交易记录 | **没有任何组件** —— 边界是刻意的，见上文 |
| 判断一笔交易是否盈利 | 只做 `sell` 与最近未匹配 `buy` 的配对，不做策略评价 |
| 读取 `orders` / `legs` 表 | 台账与执行内核的表完全隔离 |
| 导出格式的业务含义 | `export.py` 只保证列顺序，不解释列 |

## 数据模型：`TradeRecord`

`src/strategy/trade_log/models.py`。一笔记录对应**一个订单**：

| 字段 | 说明 |
|---|---|
| `symbol`、`side`（`buy`/`sell`）、`qty`、`price` | 必填 |
| `venue`、`tag`、`fee_usd`、`pnl_usd`、`strategy`、`reason`、`note` | 可选 |
| `id` | 默认 `uuid4().hex` |
| `ts` | ISO 时间戳，默认当前时间（`__post_init__` 填充） |

`notional_usd()` 由 `qty * price` 派生，**不单独存储为字段**，`to_dict()` 会把它加进
序列化结果里。`from_dict()` 是反向构造。

`tag` 与 `config/watchlist.yaml` 里的标的分类对应（见[价格监控](strat-price-watch.md)），
所以台账可以按分类聚合；`strategy` 和 `reason` 是自由文本。

## 落盘

写的是 `trades` 表（见[持久化层](base-persistence-layer.md)），入口是
`PersistenceStore.record_trade(...)`。

`sell` 且 `pnl_usd` 为 `None` 时，store 会自动**配对最近一笔未匹配的 buy** 并算 pnl：

```
pnl = (卖出价 - 买入价) * 数量 - 卖出手续费 - 买入手续费
```

配对关系记在 `matched_buy_id` 列；`_find_unmatched_buy` 的查询排除了已被其它 sell
匹配过的 buy，所以同一笔买入不会被重复配对。显式传了 `pnl_usd` 就不做配对。

这个自动配对只在 `sell` 上生效，且是**尽力而为**：没有可配对的 buy 时 `pnl_usd` 保持
`None`，不会报错。

## 两个写入方

同一张表、同一个 `record_trade` 入口，两个写入路径：

| 入口 | 场景 |
|---|---|
| `onefill trades record` | 命令行逐笔录入，参数最全 |
| Telegram `/log` | 手机上快速记一笔；**仅私聊可用** |

Telegram `/log` 的参数位置固定，可选项比 CLI 少（没有 `--pnl`/`--note`/`--ts`），
解析实现在 `PriceWatcher._log_trade`（见[价格监控](strat-price-watch.md)）。
`sell` 时回执里会带上自动算出的 pnl。

## 读取与导出

`src/strategy/trade_log/export.py` 定义导出列的固定顺序 `FIELDS`
（`id, ts, venue, symbol, tag, side, qty, price, notional_usd, fee_usd, pnl_usd, strategy, reason, note`）：

- `to_csv(rows)` — `DictWriter`，未知键忽略、缺失键留空；
- `to_json(rows)` — 缩进 JSON，`ensure_ascii=False`，中文 `tag`/`note` 不会被转义。

CLI：

- `onefill trades list [--tag T] [--limit N] [--json]` — 按时间倒序列出；
- `onefill trades export [--format csv|json] [--out FILE] [--tag T]` — 导出，不传 `--out`
  就打到标准输出。`--out` 写文件时用 UTF-8。

导出不复用 `trades list` 的 `--limit`（固定取全量），所以全量导出不会被默认的 200 行截断。

## 模块位置

| 文件 | 内容 |
|---|---|
| `src/strategy/trade_log/models.py` | `TradeRecord` |
| `src/strategy/trade_log/export.py` | `FIELDS`、`to_csv`、`to_json` |
| `src/cli/main.py` | `trades record` / `trades list` / `trades export` |
| `src/strategy/price_watch/watcher.py` | Telegram `/log` 解析 |
