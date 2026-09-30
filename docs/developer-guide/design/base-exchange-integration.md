---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-29
applies_to: src/exchange/, src/market/, src/coordinator/, config/exchanges.yaml
---

# 交易所接入设计

本文档定义接入交易所的统一边界。交易所可以通过 CCXT 接入，也可以通过原生适配器接入；上层代码不得依赖其中一种实现。Arcus 属于原生适配器场景：CCXT 当前没有 Arcus 实现，因此不能把它伪装成 `CCXTExchange`，也不能在 CCXT 适配器中堆叠 Arcus 特殊分支。

Binance 使用独立 `BinanceExchange(BaseExchange)` 管理三个固定 CCXT 产品客户端，避免泛化方法
隐式选择错误账户。新增 venue 仍遵循下列统一类型合同；Binance 细节见
[Binance 接入设计](base-binance-integration.md)。

## 目标边界

适配器负责把交易所协议转换为项目内部模型：`Instrument`（市场）、`OrderRequest`/`OrderSnapshot`（订单）和 `BaseExchange`（统一异步生命周期）。Planner、Validator、策略、执行器和持久化层只能使用这些内部模型及 `BaseExchange` 方法。原生 symbol、签名字段、HTTP 状态码、WebSocket frame 名称和 CCXT 参数不得越过 exchange 层。

## 接入前调查

编码前建立能力矩阵，记录官方文档链接、请求/响应样例和未支持状态：

| 能力 | 必须确认 |
| --- | --- |
| 网络 | mainnet/testnet REST、WS 地址及账户体系 |
| 市场 | spot/perp、symbol、合约乘数、方向、保证金资产、listing |
| 精度 | price tick、quantity step、最小数量/名义金额、最大杠杆 |
| 数据 | markets、BBO、orderbook、trades、candles、funding |
| 账户 | balance、positions、open orders、history、fills |
| 订单 | market/limit、TIF、reduce-only、client order ID、批量能力 |
| 认证 | key、私钥、签名、时间戳/nonce、权限和错误响应 |
| 推送 | 公共和私有 channel、订阅、鉴权、心跳、重连、顺序 |
| 限流 | REST/WS 限额、响应头、退避和幂等重试规则 |

Arcus 的 Ed25519 签名、REST `/v1/*`、单 socket 多路复用和订单/市场 channel 必须在 native adapter 内实现，不得复用 CCXT 的 HMAC 或 `type` 假设。

## 实现结构

### 配置与密钥

在 `config/exchanges.yaml` 增加独立配置：

```yaml
arcus:
  type: native
  adapter: arcus
  enabled: false
  default_network: testnet
  networks:
    mainnet: {rest_base_url: https://api.arcus.xyz, websocket_url: wss://api.arcus.xyz/v1/ws}
    # Replace with the official testnet endpoints after verifying Arcus availability.
    testnet: {rest_base_url: https://<arcus-testnet-rest>, websocket_url: wss://<arcus-testnet-ws>}
  symbols: []
  fees: {taker: 0.0, maker: 0.0}
```

端点必须来自 Arcus 官方文档并在实现前复核；默认关闭且示例不得含真实凭据。交易所敏感字段只写入
`config/secrets.testnet.yaml` / `config/secrets.mainnet.yaml`，保留匹配的顶层 `network` 标记，
并在两个网络的 example 模板中说明名称、格式和权限；公共 `secrets.yaml` 仅保存 Telegram
等公共凭据。Arcus 只使用网页同名的 `api_key` 和 `api_signing_key`，均为 32 字节十六进制
Ed25519 值；`api_key` 不加 `0x`，`api_signing_key` 允许 `0x` 前缀；`master_wallet_address` 是授权主钱包。旧 `address` / `wallet_address` 和 `apiKey`、`private_key`、`privateKey`
字段应报迁移错误，不回退；协议字段 `address` / `ad` 不改。凭据用双引号字符串，空值 `""`，
避免 YAML 将十六进制/数字解析成数值；不因单引号样式拒绝已解析字符串。
Arcus 和 Hyperliquid 都由主账户授权独立 API 签名者；前者 API 身份为 Ed25519 原始公钥，
后者为 secp256k1 公钥派生的 EVM 地址，不可互换。私钥不得进入日志、异常或快照。

入口统一通过 `src/cli/config.py` 按最终网络选择端点配置和凭据，再把字典传给工厂。
新增 adapter 不自行读取文件，不从其他网络或旧单文件回退取 key。

### 工厂注册

`ExchangeFactory.create_exchange` 根据 `type`/`adapter` 注册并实例化适配器。`native` 不应再被无条件拒绝；未知 adapter 必须立即抛出带交易所名称的配置错误。初始化仍统一执行网络覆盖、`connect()`、超时和日志。建议使用显式注册表：`NATIVE_ADAPTERS = {"arcus": ArcusExchange}`。

### 原生适配器

新增 `src/exchange/arcus.py`，继承 `BaseExchange`，只实现 Arcus 协议和内部模型转换。共享 HTTP 会话、网络切换、余额缓存和资源关闭逻辑；必要时覆盖 `_get_auth_headers` 或增加专用签名函数。

至少实现：

1. `connect()`：服务检查、市场加载和 WS 状态初始化。
2. `list_markets()`：转换为 `Instrument`，明确 spot/perp、精度和 listing。
3. `fetch_orderbook()`/订阅：统一 `bids`、`asks`、时间戳；处理快照、增量、序列号和重同步。
4. `_fetch_balance_impl()`：按账户/产品区分可用与冻结余额。
5. `create_order()`：校验精度和能力，映射请求并保留 client order ID。
6. `fetch_order()`/`cancel_order()`：支持 order ID，能用时支持 client ID；错误包含 venue、symbol 和订单 ID。
7. `watch_orders()`：按 venue 协议订阅订单，逐条输出规范化更新；Arcus 账户频道公开可读，订阅成功不证明签名权限。断线和序列缺口可观测。
8. `order_capabilities()`：只声明已验证的订单类型、TIF、reduce-only、client ID 等能力。

响应必须经过 `parse_order_snapshot` 或等价 native 转换，不能把 Arcus 状态名直接传给协调器。无法可靠计算的费用、均价或数量保持 `None`，不能猜测。

### WebSocket 生命周期

WS 必须有连接状态、订阅表、心跳、重连退避和关闭路径；仅在 venue 要求时刷新鉴权。公共行情和账户订单可共用 socket，但消息必须按 channel、market、账户隔离。重连后重新订阅并标记订单簿需要快照；断线期间的增量不得伪装成连续数据。

### 市场与 symbol 映射

原生 `venue_symbol` 只保存在 `Instrument`。映射必须双向且可测试：内部 `base/quote/market_type` 到 Arcus symbol，以及响应 symbol 到内部 key。处理下架、精度变化和未知市场，不能静默覆盖已有 Instrument。

## 错误、重试与安全

- 认证失败、参数错误、余额不足、限流、网络错误和拒单映射为可区分异常或错误代码。
- 只自动重试幂等 GET、查询和带稳定 client ID 的请求；下单超时进入“结果未知”查询流程，禁止盲目重发。
- 日志记录 endpoint、请求 ID、延迟和结果类别，脱敏 key、签名、私钥和完整请求体。
- 时间戳偏差、nonce 重复、签名失败和 WS 鉴权失败提供可操作信息。
- 默认配置不得启用真实交易；网络切换通过 `NetworkType` 和配置完成，业务代码不得硬编码网络分支。

## 测试要求

- 单元：签名向量、序列化、错误映射、symbol/精度、状态转换和能力矩阵。
- 适配器：fake HTTP/WS 覆盖连接、快照/增量、重连、鉴权、下单超时、撤单和查询。
- 集成：工厂、网络覆盖、Instrument 注册、订单确认和余额缓存，不依赖真实网络。
- `@pytest.mark.network`：默认只读的官方 testnet 接口验证；未满足运行条件时明确报告。
  [DEX 专用验证](../../user-guide/examples/dex-testnet-validation.md)须显式开启真实交易，
  并执行独立数据库、预算、账户基线和恢复检查，不能在普通网络用例中隐式发单。
- 回归：Binance、Hyperliquid、Mock 测试继续通过，确保 BaseExchange 合同未被 Arcus 特化。

## Arcus 落地顺序

1. 从 Arcus API Reference 固化 endpoint、签名和响应 fixture，完成能力矩阵。
2. 实现配置、工厂注册、认证和只读市场/订单簿接口。
3. 实现余额、下单、查询、撤单及 `OrderSnapshot` 转换，加入结果未知和幂等测试。
4. 实现公共/私有 WS、重连、盘口序列恢复、`userFills` 和订单确认，再接入执行器。
5. 通过 testnet 网络测试后才启用真实配置，并更新当前状态、系统架构和 Arcus API Reference。

## 验证命令

```bash
uv run --locked pytest tests/exchange -q
uv run --locked pytest tests/market tests/coordinator -q
uv run --locked pytest -m network -k arcus
uv run onefill venues
uv run onefill instruments --venue arcus --refresh
```

完成标准：Arcus 可通过同一工厂和 `BaseExchange` 合同初始化；上层无需知道实现是否使用 CCXT；所有已声明能力有 fixture 或 testnet 证据；WS 的 `contents` envelope、盘口 `lastSequenceId` gap 恢复、`userFills` 快照/去重和 client ID 查询均有测试；未实现能力显式失败而不是模拟成功。
