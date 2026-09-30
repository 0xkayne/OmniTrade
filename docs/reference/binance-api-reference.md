---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
applies_to: Binance Spot, USDⓈ-M Futures, COIN-M Futures, and the CCXT adapter
---

# Binance API Integration Reference

本页是 oneFill 的 Binance 专属接入参考。它把 Binance 的产品、环境、REST、WebSocket
API 和行情流分开描述。`https://demo.binance.com/` 是网页入口，不是通用 REST host；不要
把网页地址填入 `rest_base_url`。

**项目约定：Binance 的测试环境只接入 Demo Trading。** `default_network: testnet`
对现货和合约都选择 Demo，并默认使用在
[Demo API Management](https://demo.binance.com/zh-CN/my/settings/api-management)
创建的 API key。Spot Testnet 和旧 Futures Testnet 在本文中仅作为官方环境背景说明，
不属于项目接入范围，也不作为后续配置扩展要求。

## 1. 产品和环境

Binance 的“合约”包含两套不同的产品：

- **Spot**：现货，使用 `/api/*`；账户余额通常是主账户资产。
- **USDⓈ-M Futures**：U 本位线性合约，使用 `/fapi/*`；CCXT 市场类型通常是 `linear`
  或 `swap`，oneFill 归一化为 `perp`。
- **COIN-M Futures**：币本位反向合约，使用 `/dapi/*`；CCXT 市场类型通常是 `inverse`
  或 `delivery`，合约张数和计价单位不能按线性合约处理。

测试环境还需要区分：

- **Spot Testnet（Spot Test Network）**：`testnet.binance.vision`，仍是独立可用的
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

### 2.2 Spot Testnet（仅说明，项目不接入）

| 接口 | Endpoint |
|---|---|
| REST | `https://testnet.binance.vision/api`；备选 `https://api1.testnet.binance.vision/api` |
| WebSocket API | `wss://ws-api.testnet.binance.vision/ws-api/v3` |
| 行情流 | `wss://stream.testnet.binance.vision/ws` 或 `/stream` |
| SBE 行情流 | `wss://stream-sbe.testnet.binance.vision/ws` 或 `/stream` |

Spot Testnet 只开放 `/api/*`。`/sapi/*` 资金、钱包等接口不能直接套用到 Spot
Testnet。它的 API key 通过 `testnet.binance.vision` 注册，和主网/Demo key 隔离。
它与 Spot Demo 并存，并未因 Demo 出现而废弃；这里列出地址是为了识别环境，不能将其
key 填入本项目的 Binance 测试网配置。现货模拟交易在本项目中使用 Spot Demo。

### 2.3 Spot Demo Trading

| 接口 | Endpoint |
|---|---|
| REST | `https://demo-api.binance.com/api` |
| WebSocket API | `wss://demo-ws-api.binance.com/ws-api/v3` |
| 行情流 | `wss://demo-stream.binance.com/ws` 或 `/stream` |
| SBE 行情流 | `wss://demo-stream-sbe.binance.com/ws` 或 `/stream` |

Demo key 在登录 Binance 后进入
[Demo Trading → API Management](https://demo.binance.com/zh-CN/my/settings/api-management)
创建，可用于 Spot Demo；合约使用对应的 Futures Demo 接口，各产品仍须具备相应交易权限。
网页入口不可访问时需检查登录状态、网络和账户可用性，不能据此判断 API host 是否可用。

### 2.4 Futures Legacy Testnet

| 产品 | REST | WebSocket API | 行情/用户流 |
|---|---|---|---|
| USDⓈ-M | `https://testnet.binancefuture.com/fapi` | `wss://testnet.binancefuture.com/ws-fapi/v1` | `wss://stream.binancefuture.com` |
| COIN-M | `https://testnet.binancefuture.com/dapi` | `wss://testnet.binancefuture.com/ws-dapi/v1` | `wss://dstream.binancefuture.com` |

这些 host 仍会出现在官方旧 connector、signature examples 和 SDK 常量中，但不能据此
判断它们是 Binance 当前推荐的新测试环境。本项目测试环境只使用 Demo Trading，
不保留旧 Futures Testnet 的兼容配置或自动回退路径。

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

使用其他 SDK 时，如果其 `TESTNET` 常量仍解析到旧 REST host
`testnet.binancefuture.com`，需选择其支持的 Demo 模式或配置已核实的 Demo endpoint。
本项目的 CCXT 路径直接调用 `enable_demo_trading(True)`，不新增 `DEMO` profile 配置键。

## 3. 官方资料中的迁移不一致

截至 2026-09-19，官方资料存在以下可复现的不一致：

1. 当前 Binance Developer Docs 的 Futures 页面把新 Futures Demo 的 REST/行情流写成
   `demo-fapi`/`demo-fstream` 和 `demo-dapi`/`demo-dstream`。
2. `binance-connector-python/common/src/binance_common/constants.py` 仍将
   `*_REST_API_TESTNET_URL` 指向 `testnet.binancefuture.com`，并且只新增了 USDⓈ-M 的
   `REST_API_DEMO_URL`，没有完整的 Futures Demo WebSocket 常量。
3. 官方旧 connector README 仍说明 `/fapi/*` 和 `/dapi/*` 使用 Futures Testnet。

本项目保留现有 `NetworkType` 和 `default_network`，给 Binance 固定以下映射：

| 项目网络 | Binance 环境 | 凭据来源 |
|---|---|---|
| `mainnet` | 主网 | Binance 主网 API Management |
| `testnet` | Demo Trading（现货与合约） | `demo.binance.com` 的 API Management |

不新增 `spot_testnet` 或 `futures_legacy_testnet` 配置值。CCXT 的
`set_sandbox_mode(True)` 与本项目选择的 `enable_demo_trading(True)` 不是同一种环境切换。
REST、WebSocket API 和行情/用户流仍需按产品分别核对；仅完成 REST 切换不能证明 WS 已验证。

## 4. oneFill 当前实现映射

`BinanceExchange` 保留单一项目 venue，内部使用三个固定客户端：

| 产品 | CCXT 客户端 | 产品范围 | 订单执行 |
|---|---|---|---|
| Spot | `binance`，只加载 spot | 普通现货账户 | 受保护买卖 |
| USDⓈ-M | `binanceusdm` | 仅 linear swap，普通单向、单资产保证金账户 | 显式开平仓 |
| COIN-M | `binancecoinm` | 仅 inverse swap，普通单向账户 | 显式开平仓、按原生张数核对 |

币本位不进入跨所套利；交割合约、期权、现货杠杆和组合保证金不接入。
`market_families` 默认为 `[spot, usdm]`，添加 `coinm` 才启用币本位。
固定客户端隔离 CCXT 产品状态；初始化失败会报告具体 family，不改用另一账户。
报价、订单、余额和 WS 都按同一产品与网络路由。

软件与离线测试覆盖不等于 Demo 交易验收。`binance-smoke` 区分公开检查、私有读取和
显式授权的受保护模拟交易；没有运行订单的验证不能写成“实测成交通过”。
数量换算、模式限制和恢复流程见 [Binance 接入设计](../developer-guide/design/base-binance-integration.md)。

### 2026 合约整合约束

USDⓈ-M 与 COIN-M 的 API 产品路由仍应显式区分；不得根据账户整合自行合并 symbol、
保证金币种或原生数量。共享额度、账户模式与流的上游变化以
[官方 CM/UM 整合通知](https://developers.binance.com/en/docs/products/derivatives-trading-coin-futures/Important-CM-UM-Integration-Notice)
为准。独立客户端不代表独立账户配额，项目通过共享额度协调 REST 与 WS 内部请求。
币本位账户、下单和行情分别参照
[账户接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-coin-m-futures/api/rest-api/account)、
[交易接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-coin-m-futures/api/rest-api/trade)、
[行情接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-coin-m-futures/api/rest-api/market-data)。

## 5. 项目配置约定

```yaml
exchanges:
  binance:
    type: ccxt
    enabled: true
    default_network: testnet
    market_families: [spot, usdm]  # 需要币本位时添加 coinm
    networks:
      mainnet: {}
      testnet: {}
```

Binance 的一对 Spot 占位 URL 无法描述三产品；各产品 URL 由适配器派生并校验。
主网凭据存入 `config/secrets.mainnet.yaml`，标记 `network: "mainnet"`；Demo 凭据存入
`config/secrets.testnet.yaml`，标记 `network: "testnet"`。CLI 网络覆盖同时决定地址和凭据。
不从另一网络或旧单文件回退。完整规则见 [配置指南](../user-guide/configuration/index.md)。

## 6. 凭据和只读验证

凭据类型不是钱包私钥：

- 主网和 Demo 使用 Binance API key/secret；可用的签名类型以目标产品文档为准。
- 项目测试网默认使用 Demo Trading 的 API Management 创建的 key，分别按权限访问
  Spot Demo 和 Futures Demo；不能拿 Spot Testnet key 或主网 key 直接替代。
- Spot Testnet 在 `testnet.binance.vision` 生成独立 key，仅用于该独立现货测试网，
  不填入本项目默认配置。旧 Futures Testnet 凭据也不属于项目接入范围。

```yaml
network: "testnet"
binance:
  apiKey: "<binance-demo-api-key>"
  secret: "<binance-demo-hmac-secret>"
```

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
| `-2008 Invalid Api-Key ID` | key 无效或与 REST 环境不匹配 | 项目 `testnet` 应填写 Demo Trading key，并检查相应产品权限 |
| REST 正常、私有 WS 没有事件 | listen key 和 WS host 属于不同 profile | 同时配置 REST、行情/用户流和 WS API，并用同一 Demo 账户验证 |
| 在 `testnet.binance.vision` 请求 `/sapi/*` 失败 | 独立 Spot Testnet 只提供 `/api/*` | 本项目应恢复 Demo Trading 配置并使用 Demo key；接口可用性按 Demo 文档核对 |
| COIN-M 订单被拒绝 | 未启用 family、未显式选择 inverse，或账户模式/额度不支持 | 启用 coinm，显式指定 inverse 与结算资产，检查普通单向单资产账户及返回的拒绝原因 |

## 8. 官方链接

- [Spot REST API](https://developers.binance.com/en/docs/products/spot/rest-api)
- [Spot WebSocket API](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-api.md)
- [Spot WebSocket Streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)
- [USDⓈ-M Futures API](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/Introduction)
- [COIN-M Futures API](https://developers.binance.com/en/docs/products/derivatives-trading-coin-futures/Introduction)
- [USDⓈ-M Futures General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)
- [COIN-M Futures General Info](https://developers.binance.com/docs/derivatives/coin-margined-futures/general-info)
- [USDⓈ-M WebSocket migration notice](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice)
- [Binance testnet/demo FAQ](https://www.binance.com/en-AU/support/faq/detail/ab78f9a1b8824cf0a106b4229c76496d)
- [Demo Trading availability FAQ](https://www.binance.com/en-KZ/support/faq/detail/9be58f73e5e14338809e3b705b9687dd)
- [Futures Testnet retirement announcement](https://www.binance.com/en/support/announcement/detail/616402d041c74000bc78282018bc62d4)
