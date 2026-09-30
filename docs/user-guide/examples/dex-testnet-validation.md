---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
applies_to: tests/e2e/test_dex_testnet.py, tests/e2e/dex_testnet_runner.py, tests/conftest.py and src/arbitrage/canary.py
---

# Arcus / Hyperliquid 测试网验证

专用入口 `tests/e2e/test_dex_testnet.py` 连接真实测试网，默认只读。只有显式传入
`--dex-testnet-trades` 才运行受保护的小额交易场景。源码实现和离线测试不代表真实交易已经
通过；每次验证以该次输出目录中的报告和订单证据为准。

本套件覆盖 Arcus 永续、Hyperliquid 永续与现货，以及 Arcus / Hyperliquid 双向永续
Canary。Binance 的验证入口见 [CLI Reference](../cli/index.md#onefill-binance-smoke)。

## 准备与只读运行

按[凭据指南](../configuration/credentials.md)配置 `config/secrets.testnet.yaml`，保留
`network: "testnet"`。只读账户查询使用主账户地址；交易场景还需要在同网络获得授权的
独立 API 签名密钥。账户余额、持仓或账户 WS 订阅成功不证明签名有效或拥有交易权限。

套件固定加载 Arcus / Hyperliquid 测试网配置，检查 REST / WS 主机和 Hyperliquid 测试网
签名域，不回落到主网。先在项目根目录执行只读验证：

```bash
export UV_CACHE_DIR=/share/$USER/uv-cache
export TMPDIR=/share/$USER/tmp
export PYTHONPYCACHEPREFIX=/share/$USER/tmp/omnitrade-pycache
mkdir -p "$TMPDIR"

uv run --locked --extra dev --group docs pytest \
  tests/e2e/test_dex_testnet.py -m "network and slow" -s \
  -o cache_dir=/share/$USER/tmp/omnitrade-pytest-cache
```

只读阶段读取市场、账户模式、余额、永续仓位、挂单、盘口、成交、K 线、资金费率和历史
账户成交，并检查 WS 盘口及重连后的盘口恢复。未实现的接口、缺失数据和读取失败会单独
记录；空盘口或没有市场观测不能算行情验证通过。历史账户成交为空可以表示暂无记录，
不能证明交易成功；订阅成功也不能代替真实事件。

候选永续优先选择 BTC / ETH；现货选择可交易的美元稳定币报价市场。筛选还要求盘口、
精度、最低数量和最低名义金额允许后续小额验证，因此只读运行也可能报告基线 `BLOCKED`。

## 显式启用交易

确认只读报告后，使用同一入口增加交易开关：

```bash
uv run --locked --extra dev --group docs pytest \
  tests/e2e/test_dex_testnet.py -m "network and slow" -s \
  --dex-testnet-trades \
  --dex-max-order-usd 100 --dex-max-total-usd 5000 \
  -o cache_dir=/share/$USER/tmp/omnitrade-pytest-cache
```

| 参数或约束 | 默认 / 上限 | 含义 |
|---|---|---|
| `--dex-testnet-trades` | 关闭 | 显式允许该套件向真实测试网发送订单 |
| `--dex-max-order-usd` | 100 USD，最高 100 | 每笔订单发送前校验的名义金额上限 |
| `--dex-max-total-usd` | 5000 USD，最高 5000 | 整轮累计预算，至少为单笔上限的四倍 |
| 订单目标 | 25 USD | 按品种最小量、最小金额及步长向上调整；超过单笔上限则阻断 |
| 价格保护 | 固定 0.5% | 相对新读取盘口的中价；按 tick 向保护区间内部取整 |
| `--dex-output-dir` | 自动生成 | 可指定一个尚不存在的独立输出目录 |

每次发送前，按稳定 client order ID 预留订单名义金额。开仓、补充对冲和平仓都计入同一
整轮预算；撤销或零成交不会返还预留额度。场景启动前还必须保留关闭敞口的预算。
`reserved_worst_case_usd` 与 `observed_turnover_usd` 分别表示预算预留和已观测成交金额，
不能把尚未观测到的成交当成零风险。

限价单始终位于盘口中价 ±0.5% 内。用于撤单检查的 GTC 单放在保护区间内、不立即穿过
盘口的位置；开平仓使用 IOC，价差过大时不放宽保护。平仓重新获取盘口，不沿用开仓价格。
Canary 实际限价先取中价 ±0.4%，为发送前再次校验预留 0.1 个百分点的价格变化余量；
最大允许区间仍为 ±0.5%，行情变化超出该区间时拒绝发送。
IOC 发单前还要求保护区间内有足够深度。未满足价格、数量精度或预算条件时，不能为了完成
测试而放大数量、改市价或无限重试。

## 场景与已有资产保护

每个交易场景前重新检查所选 symbol。永续必须为零仓位且无挂单；现货必须无挂单且
基础币总余额为零。已有仓位、订单或现货持仓会排除该市场；读取不完整也会阻断。
套件记录仓位、杠杆和保证金模式基线，不接管已有交易或使用已有现货库存完成卖出。

交易场景包括：

- 单所永续买入、卖出两个方向：GTC 下单、按服务器及 client ID 查询、撤单确认，随后
  IOC 开仓与 reduce-only 平仓。
- Arcus / Hyperliquid 两个方向的双腿永续 Canary：先持久化周期和订单身份，再发送；
  最终确认两所仓位为零且无挂单后才记为 `CLOSED`。
- Hyperliquid 现货买入后卖出本轮净获得的基础币；扣除已确认的基础币手续费，并按品种
  步长计算可卖数量。残余无法关闭时报告阻断，不借用原有余额或声称已经平仓。

清理只处理本轮订单。提交结果未知时按原订单身份查询，不能重发同一请求。清理失败、
持仓与本轮成交不符或残余敞口未确认时，报告 `BLOCKED` 并停止后续交易；Canary 保留
`RECOVERY` 和订单标识。已有 `MANUAL_REVIEW` 或未完成套利周期不会被自动清除。

## WS、持久化与恢复证据

真实账户 WS 证据必须包含与本轮服务器订单 ID 匹配的 `orders` / `fills` 事件。
Hyperliquid 现货也必须收到本轮现货订单的实际更新和成交事件，不能仅凭初始 snapshot
或永续事件判为通过。连接成功、订阅 ACK 或其他订单的事件不能使该检查通过。

账户流在无新事件时可以空闲。Python 3.10 下，监听循环按 `asyncio.TimeoutError` 处理
单次等待超时并继续调用订阅接口等待更新，不把正常空闲标为失败，也不因此发单。
Hyperliquid 多 symbol 监听复用账户级订阅，各自仍按市场等待事件；服务器拒绝订阅或
始终缺少本轮匹配事件时，报告仍须保留失败或阻断结果。

盘口重连检查主动关闭底层
连接，等待适配器重新订阅并收到有效盘口，报告名称为
`<venue>.<product>.websocket_reconnect`。

每轮创建独立 `execution.db` 和审计目录。单所订单通过 `LegOrderManager` 先落盘再发送；
双腿 Canary 先保存周期及两腿身份。`orders.restart_readback` 新建数据库连接、重建
持久化的执行上下文并按订单身份查询服务器终态，不重新下单。这是持久化回读与只读恢复
检查，**不等同于真实进程崩溃故障注入**。

## 报告与结果判断

默认输出目录是 `/share/<当前用户名>/outputs/omnitrade/dex-testnet/<UTC 时间戳>/`，
用户名与时间戳在运行时生成。`--dex-output-dir` 指定的目录也必须尚不存在，以避免覆盖
前一轮证据。报告会对加载的凭据值脱敏。

| 文件 | 内容 |
|---|---|
| `report.md` | 各检查的结果和可读证据 |
| `report.json` | 相同结果的结构化记录、运行 ID、网络、交易开关与预算汇总 |
| `budget.json` | 发生预算预留后的订单身份、累计预留和已观测成交金额 |
| `execution.db` | 本轮独立 SQLite 订单、Intent、Leg 与套利周期记录 |
| `audit/` | 本轮账户基线与执行事件 |

| 检查状态 | 含义 |
|---|---|
| `PASS` | 该检查获得了要求的证据；不能外推为其他产品或账户模式通过 |
| `FAIL` | 检查执行出现异常或接口失败 |
| `BLOCKED` | 环境、基线、预算、保护价、成交或恢复证据不足，不能安全继续 |
| `UNSUPPORTED` | 当前适配器未实现该接口或缺少对应能力 |

`FAIL` 或 `BLOCKED` 会使该 pytest 用例失败。`UNSUPPORTED` 必须在报告中保留，不能
解释成能力已通过；仅只读运行成功也不能解释成签名、订单生命周期或交易 WS 已通过。
出现未知订单或残余敞口时，先依据本轮报告、数据库与交易所账户核查，不要删除数据库后
重新运行来绕开阻断。

## 与普通 Intent 的能力边界

Arcus 和 Hyperliquid 适配器提供 `fetch_order_account`、`fetch_order_position` 与
`fetch_order_positions`，返回账户模式、可用资金和有符号原生仓位；缺失、矛盾或无法
确认的响应显式失败。这些能力供普通执行器的基线及风险校验使用。

Hyperliquid 会识别单资产、DEX abstraction、统一账户和 portfolio 模式。
普通 Coordinator 的永续执行仍要求单向持仓、单资产账户；统一账户等模式会被拒绝。
本套件直接验证适配器、`LegOrderManager` 和 `TestnetCanary` 的低层路径，不能据此
宣称普通 `onefill order` / `submit_intent` 已支持所有账户模式或完整产品组合。

实现边界见[交易所层](../../developer-guide/design/base-exchange-layer.md)和
[跨所套利](../../developer-guide/design/strat-cross-venue-arb.md)。
