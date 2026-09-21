---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-19
applies_to: Binance Spot, USDⓈ-M Futures, COIN-M Futures, and the CCXT adapter
---

# Binance API Integration Reference

本页是 oneFill 的 Binance 专属接入参考。它把 Binance 的产品、环境、REST、WebSocket
API 和行情流分开描述。`https://demo.binance.com/` 是网页入口，不是通用 REST host；不要
把网页地址填入 `rest_base_url`。

## 1. 产品和环境

Binance 的“合约”包含两套不同的产品：

- **Spot**：现货，使用 `/api/*`；账户余额通常是主账户资产。
- **USDⓈ-M Futures**：U 本位线性合约，使用 `/fapi/*`；CCXT 市场类型通常是 `linear`
  或 `swap`，oneFill 归一化为 `perp`。
- **COIN-M Futures**：币本位反向合约，使用 `/dapi/*`；CCXT 市场类型通常是 `inverse`
  或 `delivery`，合约张数和计价单位不能按线性合约处理。

测试环境还需要区分：

- **Spot Testnet（legacy Spot Test Network）**：`testnet.binance.vision`，仍是独立可用的
  Spot 测试网，虚拟余额和订单簿与主网独立，约每月重置。
- **Spot Demo Trading**：`demo-api.binance.com`，使用 Binance 主站账户中的 Demo Trading
  资格，行情更接近主网，余额可在网页中重置。
- **Futures Legacy Testnet**：`testnet.binancefuture.com`，旧的 USDⓈ-M/COIN-M 合约测试网。
  官方旧 SDK 和 signature examples 仍保留这些地址，但 Binance 已公告其网页访问逐步停止。
- **Futures Demo Trading**：新的模拟交易环境。REST 已使用 `demo-fapi.binance.com`（USDⓈ-M）
  和 `demo-dapi.binance.com`（COIN-M）；其部分 WebSocket API 文档仍使用旧
  `testnet.binancefuture.com` host，见第 3 节的迁移说明。

官方资料入口：

- [binance-spot-api-docs](https://github.com/binance/binance-spot-api-docs)
- [Spot Testnet general-info](https://github.com/binance/binance-spot-api-docs/blob/master/testnet/general-info.md)
- [Spot Demo Mode general-info](https://github.com/binance/binance-spot-api-docs/blob/master/demo-mode/general-info.md)
- [binance-connector-python](https://github.com/binance/binance-connector-python)
- [官方 connector endpoint constants](https://raw.githubusercontent.com/binance/binance-connector-python/master/common/src/binance_common/constants.py)
- [binance-futures-connector-python](https://github.com/binance/binance-futures-connector-python)
- [Binance Developer Docs](https://developers.binance.com/en/docs/introduction)

## 2. Endpoint 矩阵

表中的 REST host 是域名根；实际路径由产品前缀决定。Spot 例如使用
`/api/v3/exchangeInfo`，USDⓈ-M 使用 `/fapi/v1/exchangeInfo`，COIN-M 使用
`/dapi/v1/exchangeInfo`。

### 2.1 主网

| 产品 | REST | WebSocket API（请求/响应） | WebSocket 行情/用户流 |
|---|---|---|---|
| Spot | `https://api.binance.com/api`；公开行情可用 `https://data-api.binance.vision` | `wss://ws-api.binance.com/ws-api/v3` | `wss://stream.binance.com/ws` 或 `/stream` |
| USDⓈ-M Futures | `https://fapi.binance.com` | `wss://ws-fapi.binance.com/ws-fapi/v1` | `wss://fstream.binance.com`；按当前迁移文档使用 `/public`、`/market`、`/private` |
| COIN-M Futures | `https://dapi.binance.com` | `wss://ws-dapi.binance.com/ws-dapi/v1` | `wss://dstream.binance.com`，使用 `/ws/<stream>` 或 `/stream?streams=...` |

Spot 主网还提供 `api-gcp.binance.com`、`api1.binance.com` 至 `api4.binance.com` 等
REST 备选域名。备选域名只替换 host，不改变 `/api/v3` 路径。

### 2.2 Spot Testnet

| 接口 | Endpoint |
|---|---|
| REST | `https://testnet.binance.vision/api`；备选 `https://api1.testnet.binance.vision/api` |
| WebSocket API | `wss://ws-api.testnet.binance.vision/ws-api/v3` |
| 行情流 | `wss://stream.testnet.binance.vision/ws` 或 `/stream` |
| SBE 行情流 | `wss://stream-sbe.testnet.binance.vision/ws` 或 `/stream` |

Spot Testnet 只开放 `/api/*`。`/sapi/*` 资金、钱包等接口不能直接套用到 Spot
Testnet。它的 API key 通过 `testnet.binance.vision` 注册，和主网/Demo key 隔离。

### 2.3 Spot Demo Trading

| 接口 | Endpoint |
|---|---|
| REST | `https://demo-api.binance.com/api` |
| WebSocket API | `wss://demo-ws-api.binance.com/ws-api/v3` |
| 行情流 | `wss://demo-stream.binance.com/ws` 或 `/stream` |
| SBE 行情流 | `wss://demo-stream-sbe.binance.com/ws` 或 `/stream` |

Demo key 在登录 Binance 后进入 **Demo Trading → API Management** 创建。账户或地区
看不到 Demo Trading 时，不应继续重试 `demo.binance.com`，而应按资格限制处理。

### 2.4 Futures Legacy Testnet

| 产品 | REST | WebSocket API | 行情/用户流 |
|---|---|---|---|
| USDⓈ-M | `https://testnet.binancefuture.com/fapi` | `wss://testnet.binancefuture.com/ws-fapi/v1` | `wss://stream.binancefuture.com` |
| COIN-M | `https://testnet.binancefuture.com/dapi` | `wss://testnet.binancefuture.com/ws-dapi/v1` | `wss://dstream.binancefuture.com` |

这些 host 仍会出现在官方旧 connector、signature examples 和 SDK 常量中，但不能据此
判断它们是 Binance 当前推荐的新测试环境。新的接入默认使用 Demo Trading，并把 Legacy
Testnet 作为兼容 profile 保留。

### 2.5 Futures Demo Trading

| 产品 | REST | WebSocket 行情/用户流 | WebSocket API（请求/响应） |
|---|---|---|---|
| USDⓈ-M | `https://demo-fapi.binance.com/fapi` | `wss://demo-fstream.binance.com` | 当前官方页面仍列 `wss://testnet.binancefuture.com/ws-fapi/v1` |
| COIN-M | `https://demo-dapi.binance.com/dapi` | `wss://demo-dstream.binance.com` | 当前官方页面仍列 `wss://testnet.binancefuture.com/ws-dapi/v1` |

这里的 `testnet.binancefuture.com` 只表示 Futures WebSocket API 文档尚未完成迁移，
不等于 REST 仍应使用旧 Futures Testnet。不要把 `demo-fstream` 或 `demo-dstream` 当成
WebSocket 请求/响应 API；它们是行情和用户数据流 host。上线前必须用目标账户分别验证：

1. REST `exchangeInfo`、账户查询和订单接口；
2. 行情/用户流 listen key 是否由同一环境签发；
3. WebSocket API 下单或查询是否接受该 Demo key。

如果 SDK 的 `TESTNET` 枚举仍解析到 `testnet.binancefuture.com`，应显式选择 `DEMO` profile
并覆盖 REST/行情流/WebSocket API 三个字段，不能只切换一个布尔 `testnet` 开关。

## 3. 官方资料中的迁移不一致

截至 2026-09-19，官方资料存在以下可复现的不一致：

1. 当前 Binance Developer Docs 的 Futures 页面把新 Futures Demo 的 REST/行情流写成
   `demo-fapi`/`demo-fstream` 和 `demo-dapi`/`demo-dstream`。
2. `binance-connector-python/common/src/binance_common/constants.py` 仍将
   `*_REST_API_TESTNET_URL` 指向 `testnet.binancefuture.com`，并且只新增了 USDⓈ-M 的
   `REST_API_DEMO_URL`，没有完整的 Futures Demo WebSocket 常量。
3. 官方旧 connector README 仍说明 `/fapi/*` 和 `/dapi/*` 使用 Futures Testnet。

因此，接入层必须把环境名和 endpoint profile 分开：

```text
environment = live | spot_testnet | futures_legacy_testnet | demo
market_family = spot | usdm | coinm
rest_base_url
ws_market_base_url
ws_api_base_url
```

`environment=testnet` 不能再同时代表 Spot Testnet、Legacy Futures Testnet 和 Futures
Demo。每个 profile 的三个 endpoint 也不能合并成一个 `websocket_url`。

## 4. oneFill 当前实现映射

当前代码使用 CCXT 的统一 `binance` adapter：

- `src/exchange/ccxt.py` 在 Binance `NetworkType.TESTNET` 上调用
  `enable_demo_trading(True)`，因此 REST 会切到 `demo-api`、`demo-fapi` 或 `demo-dapi`。
- 当前配置里的 Binance `rest_base_url`/`websocket_url` 是兼容和展示字段；CCXT adapter 会
  保留 Binance 自己的 product-specific URL，不能把 YAML 中的 Spot URL 当成合约 URL。
- `options.defaultType` 当前默认为 `swap`，影响没有明确 market context 时的路由和账户类型；
  CCXT 的市场发现仍可能返回 Spot、linear 和 inverse。oneFill 的订单能力会拒绝
  inverse/Coin-M；跨 venue 套利当前只允许线性 perp。
- `OrderbookCache` 使用 `ccxt.pro` 的 URL 映射，尚未按 USDⓈ-M 当前 `/public`、`/market`、
  `/private` 拆分连接；因此不能把当前 Binance WebSocket 行情缓存视为已完成迁移。
- 当前自动化网络测试覆盖 Binance Spot Demo 的行情加载；USDⓈ-M Demo 私有订单、COIN-M
  下单、Legacy profile 和 Futures WebSocket API 尚未形成等价覆盖。

当前支持边界：

| 能力 | Spot | USDⓈ-M Futures | COIN-M Futures |
|---|---:|---:|---:|
| 主网公开市场发现 | 可读 | 可读 | 可读 |
| Demo/测试网公开市场发现 | Spot Demo 可读；Spot Legacy profile 未接入 | Demo REST 可读 | Demo REST 可读但需单独验证 |
| 当前统一订单执行 | 支持条件取决于 market profile | 支持线性 perp | 拒绝 inverse，下单未支持 |
| 跨 venue 套利 | 非当前目标 | 当前目标 | 不支持 |

## 5. 推荐配置扩展

新增 Binance 产品时，不要继续扩展一个含义不清的 `default_network: testnet`。推荐的
配置形状如下；它是目标 schema，当前版本尚未全部实现：

```yaml
binance:
  type: ccxt
  adapter: binance
  environment: demo
  market_family: usdm
  credentials_profile: binance_demo
  endpoints:
    rest: https://demo-fapi.binance.com
    ws_market: wss://demo-fstream.binance.com
    ws_api: wss://testnet.binancefuture.com/ws-fapi/v1
```

实现时可以使用 CCXT 专用 exchange id（`binance`、`binanceusdm`、`binancecoinm`），也可以
保留统一 `binance` 并由 `market_family` 设置 `options.defaultType`。无论选择哪种方式，
必须在配置校验阶段拒绝以下组合：Spot key 调用 `/fapi` 或 `/dapi`、Legacy key 调用 Demo
REST、线性数量模型调用 inverse 合约、以及只配置行情流却尝试订阅私有订单流。

## 6. 凭据和只读验证

凭据类型不是钱包私钥：

- 主网和 Demo 使用 Binance API key/secret；可用的签名类型以目标产品文档为准。
- Spot Testnet 在 `testnet.binance.vision` 生成独立 key。
- Futures Legacy Testnet 使用旧测试账户中的独立 key。
- Futures Demo 在 Demo Trading 的 API Management 中创建 key；不能拿 Spot Testnet key
  或主网 key 直接替代。

先验证公开 endpoint，再验证私有 API。以下命令不会下单：

```bash
curl -fsS https://api.binance.com/api/v3/exchangeInfo >/dev/null
curl -fsS https://demo-api.binance.com/api/v3/exchangeInfo >/dev/null
curl -fsS https://demo-fapi.binance.com/fapi/v1/exchangeInfo >/dev/null
curl -fsS https://demo-dapi.binance.com/dapi/v1/exchangeInfo >/dev/null

uv run --locked onefill arb testnet-smoke \
  --venues binance --symbol BTC --market perp --account --json
```

私有验证必须确认输出中的 `credentials_configured` 和账户查询状态。测试网 smoke 不应
自动发送订单；真实测试订单只能经过项目的受保护 canary 入口和明确的 Demo profile。

## 7. 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `demo.binance.com` 打不开 | 网页入口受地区、账户资格或 WAF 影响 | 从 Binance 官方站点进入 Demo Trading；API 调用使用 `demo-api`/`demo-fapi`/`demo-dapi` |
| `-2008 Invalid Api-Key ID` | key 与 REST 环境不匹配 | 区分 Spot Testnet、Spot Demo、Legacy Futures 和 Futures Demo key |
| REST 正常、私有 WS 没有事件 | listen key 和 WS host 属于不同 profile | 同时配置 REST、行情/用户流和 WS API，并用同一 Demo 账户验证 |
| 现货接口请求 `/sapi/*` 失败 | Spot Testnet 只提供 `/api/*` | 改用 `/api/v3`，或改用主网/Demo 产品能力 |
| Coin-M 订单被 oneFill 拒绝 | 当前订单模型只允许线性合约 | 保持 Coin-M 为只读，完成 inverse 数量、保证金和结算资产模型后再开放 |

## 8. 官方链接

- [Spot REST API](https://developers.binance.com/en/docs/products/spot/rest-api)
- [Spot WebSocket API](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-api.md)
- [Spot WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)
- [USDⓈ-M Futures API](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/Introduction)
- [COIN-M Futures API](https://developers.binance.com/en/docs/products/derivatives-trading-coin-futures/Introduction)
- [USDⓈ-M Futures General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)
- [COIN-M Futures General Info](https://developers.binance.com/docs/derivatives/coin-margined-futures/general-info)
- [USDⓈ-M WebSocket migration notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice)
- [Binance testnet/demo FAQ](https://www.binance.com/en-AU/support/faq/detail/ab78f9a1b8824cf0a106b4229c76496d4)
- [Demo Trading availability FAQ](https://www.binance.com/en-KZ/support/faq/detail/9be58f73e5e14338809e3b705b9687dd)
- [Futures Testnet retirement announcement](https://www.binance.com/en/support/announcement/detail/616402d041c74000bc78282018bc62d4)
