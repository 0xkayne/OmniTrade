---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-29
applies_to: config/secrets.testnet.yaml, config/secrets.mainnet.yaml, testnet adapters
---

# Testnet Credentials

本页说明 Arcus、Hyperliquid 和 Binance 测试网凭据的类型、获取方式，以及如何填入
`config/secrets.testnet.yaml`。主网凭据单独保存在 `config/secrets.mainnet.yaml`，
网络切换自动选择对应文件；公共 `config/secrets.yaml` 仅保存 Telegram 等公共凭据。
示例中的值都是占位符；不要把真实私钥、API Secret 或助记词写入
Git、日志、Issue 或文档。

## 统一模型与字段对照

Arcus 与 Hyperliquid 都采用 **主钱包账户授权独立 API 签名密钥** 的模型：主账户保存资金并
决定授权，独立 API 私钥签署交易请求。两者的界面术语和密码学身份不同，因此 API 字段不同：

| 角色 | Arcus 网页术语 → YAML | Hyperliquid 网页术语 → YAML | 含义 |
|---|---|---|---|
| 资金/授权主账户 | 连接的主钱包 → `master_wallet_address` | 连接的主钱包 → `master_wallet_address` | Ethereum 主钱包地址，用于账户查询和确定授权主体 |
| API 身份 | API Key → `api_key` | API Wallet Address → `api_wallet_address` | Arcus 是 **Ed25519 原始公钥**；Hyperliquid 是 **secp256k1 公钥派生的 EVM 地址** |
| API 签名私钥 | API Signing Key → `api_signing_key` | API Wallet Private Key → `api_wallet_private_key` | 分别是 Ed25519 / secp256k1 私钥，均独立于主钱包私钥 |

Arcus 的 `api_key` 是 API 身份，不是 EVM API 钱包地址；不能填入 Hyperliquid 的
`api_wallet_address`，反向也不成立。两个平台的 API 私钥不可互换。Binance 使用另一套
HMAC `apiKey` / `secret`，同样不能与这些 DEX 凭据互换。各平台主网、测试网分别授权和配置。

## YAML 字符串填写规范

所有 DEX/CEX 凭据值统一写成**双引号包裹的字符串**：主地址、API 地址、公钥、私钥、
`apiKey`、`secret`、占位符都如此；未配置写 `""`，不用 `null` 或留成裸值。
`network` 示例也使用双引号。这样可避免 YAML 将 `0x...` 或纯数字解析为数值，丢失前缀、
前导零等格式。业务配置中的布尔和数值（如 `enabled: false`、杠杆和费率）保持原类型。

这是填写规范；已能解析为字符串的单引号值不会因引号样式被运行时拒绝。

## Arcus

Arcus 账户由 Ethereum 主地址标识，交易请求使用单独的 Ed25519 密钥签名。
oneFill 字段与网页 API Keys 页对应如下：

| 网页字段 | YAML 字段 | 含义 |
|---|---|---|
| API Key | `api_key` | Ed25519 原始公钥/API 身份，不是 EVM 地址 |
| API Signing Key | `api_signing_key` | Ed25519 签名私钥 |
| 连接并授权的主钱包地址 | `master_wallet_address` | Ethereum 主账户地址，带 `0x` 前缀 |

两个 Ed25519 字段都是 32 字节，即 64 位十六进制字符。`api_key` 按页面原样填写，
不添加 `0x`；`api_signing_key` 额外允许 `0x` 前缀。
`api_signing_key` 不填 Ethereum 钱包私钥或助记词。官方
[REST 入门指南](https://docs.arcus.xyz/guides/rest-trading#step-2-get-an-api-key)
直接用网页 API Signing Key 构造 Ed25519 私钥，并由它导出对应 API Key。

### 获取步骤

1. 在 [Arcus Testnet API Keys](https://testnet.arcus.xyz/api-keys) 连接测试网主钱包。
2. 填名称并点击 **Generate**，保存只显示一次的 **API Signing Key** 和对应 **API Key**。
3. 选择子账户与有效期，点击 **Authorize**，用主钱包完成授权签名；仅生成尚未完成注册。
4. `master_wallet_address` 填该主钱包地址。所选子账户须与 `config/exchanges.yaml` 中 Arcus 的
   `options.account_index` 一致（默认 `0`）。手动生成/注册流程见
   [Arcus Authentication](https://docs.arcus.xyz/api-reference/authentication)。

### 配置

```yaml
network: "testnet"
arcus:
  api_key: "<API Key: Ed25519 public key>"
  api_signing_key: "<API Signing Key: Ed25519 private key>"
  master_wallet_address: "<ethereum-master-address>"
```

旧的 Arcus `address` / `wallet_address` 必须改为 `master_wallet_address`；
`apiKey` 改为 `api_key`，`private_key` / `privateKey` 改为 `api_signing_key`。旧字段会报迁移错误，
改名后移除旧字段，不要同时保留两种名字。此变更仅针对配置，Arcus 协议请求中的
`address` 与签名载荷中的 `ad` 保持原名，由适配器从 `master_wallet_address` 映射。

测试网 REST/WebSocket 地址由 `config/exchanges.yaml` 的网络选择，对应 `api.testnet.arcus.xyz`。
下单、撤单需要已注册的密钥对。`GET /account`、订单/成交查询和账户频道订阅是公开读取，
只需要账户地址；读到余额或成交不能证明签名私钥正确，也不能证明服务器已校验签名。
官方说明见 [Get account](https://docs.arcus.xyz/api-reference/public/get-account) 与
[WebSocket 读取权限](https://docs.arcus.xyz/api-reference/websocket#what-authorizes-a-subscription)。

### 账户可读但下单无权限 { #arcus-api-key-scope }

`GET /v1/account` 能读到资金，不等于当前 API Key 可以操作该子账户。
通过 `GET /v1/apiKeys` 按主钱包地址读取注册信息，找到与当前 `api_key` 公钥匹配的记录，
检查下列字段；不要在报告中输出私钥或完整凭据。

| 字段 / 配置 | 核对内容 |
|---|---|
| `status` | 注册记录必须为 `ACTIVE`，生成密钥或本地签名验证通过不能替代服务器授权 |
| `validUntil` | 授权必须未过期，按接口返回的有效期判断 |
| `accountIndex` | 当 `allSubaccounts` 为 `false` 时，必须与目标子账户一致 |
| `allSubaccounts` | 区分覆盖所有子账户的授权和仅覆盖某个 index 的授权 |
| `options.account_index` | 位于公共 `config/exchanges.yaml` 的 Arcus 条目，默认 `0`；不是 secrets 字段 |

目标子账户、资金所在子账户和 API Key 的授权范围必须一致。某个子账户可读、有资金，
不能证明另一个 index 已存在或有可用保证金；账户查询返回无活动记录时也不能自动回落到
其他子账户。先确认目标 index，再配置对应已授权密钥；仅修改 `account_index` 不会移动
资金、扩大授权或激活目标账户。注册信息核对也不是一次实际签名交易的成功证明。
接口定义见 [Arcus API Reference](../../reference/arcus-api-reference.md)。

## Hyperliquid

oneFill 配置将资金账户和 API Wallet（agent）签名者分为三个字段：

| 网页/账户信息 | YAML 字段 | 用途 |
|---|---|---|
| 连接并有资金的主钱包地址 | `master_wallet_address` | 查询主账户余额、仓位和订单 |
| API Wallet Address | `api_wallet_address` | secp256k1 公钥派生的 EVM 地址，用于核对签名身份 |
| API Wallet Private Key | `api_wallet_private_key` | API Wallet 的 secp256k1 签名私钥 |

API 地址必须与 API 私钥推导出的地址一致，并且不能等于主钱包地址。两个 API 字段必须
同时填写或同时留空；只填 `master_wallet_address`、两个 API 字段留空时可做公开只读查询。
此 YAML 不接受主钱包私钥直接签名。

API Wallet 只负责签名，查询余额和仓位使用实际资金账户地址；误用 agent 地址查询会得到
空账户。依据 [Hyperliquid API wallets](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets)。

### 获取步骤

1. 在 [Hyperliquid Testnet](https://app.hyperliquid-testnet.xyz/) 连接主钱包，按
   [Testnet faucet](https://hyperliquid.gitbook.io/hyperliquid-docs/onboarding/testnet-faucet)
   条件领取 mock USDC。
2. 在同一测试网账户的 API 页面创建并批准 API Wallet，保存 **API Wallet Address** 与
   **API Wallet Private Key**。主网和测试网的批准状态独立。
3. 三个字段分别填写主钱包地址、API Wallet 地址和 API Wallet 私钥。
   私钥属于 API Wallet，不属于主钱包。

适配器按所选网络同时固定 API 地址与 CCXT 的 `sandboxMode` 签名域；
测试网授权和签名不能用于主网，反之亦然。

### 配置

```yaml
network: "testnet"
hyperliquid:
  master_wallet_address: "<funded-master-account-address>"
  api_wallet_address: "<approved-api-wallet-address>"
  api_wallet_private_key: "<approved-api-wallet-private-key>"
  # vaultAddress: "<optional-vault-or-subaccount-address>"
```

旧的 `walletAddress`、`wallet_address`、`privateKey`、`private_key` 会报迁移错误，不做
兼容回退。迁移时保留实际资金账户地址到 `master_wallet_address`，填入已授权且独立于
主钱包的 API Wallet 地址和私钥，并删除旧字段。若尚未授权 API Wallet，将两个 API
字段留空以保持只读；不要把原来的主钱包私钥改名后继续填写。

适配器内部将 `master_wallet_address` 映射为 CCXT `walletAddress`，
`api_wallet_private_key` 映射为 CCXT `privateKey`；这些内部名字不是 YAML 配置字段。
`api_wallet_address` 用于校验私钥身份。本地匹配通过不代表交易所已批准该 signer，
只读余额成功也不证明签名权限。

`vaultAddress` 指定签名动作的 vault/子账户目标，适配器将其传入 CCXT `options.vaultAddress`。
仅设置该值不会让 CCXT 公开查询自动改查目标账户，读取账户路由必须单独保持一致；
不要仅凭此字段认定完整 vault/子账户流程可用。主账户对目标和 API Wallet 的权限必须成立。

## Binance Demo Trading

同一网络凭据交给三个固定产品客户端；公开行情成功仅代表公共连接正常。Spot、USDⓈ-M、
COIN-M 的私有账户与权限分别验证，不能用一个账户查询通过推断其他产品有交易权限。
可用 `onefill binance-smoke --family usdm --account --json` 做只读账户检查。


完整的产品和 endpoint 矩阵见 [Binance API Integration Reference](../../reference/binance-api-reference.md)。

当前项目的 Binance `testnet` **只接入 Demo Trading**，现货与合约均默认使用在
`demo.binance.com` 创建的 API key。它使用 HMAC-SHA256 的 `apiKey` 和 `secret`，
与 Ethereum 钱包、Arcus Ed25519 密钥无关。能否交易具体产品仍取决于相应权限。

### 获取步骤

1. 登录 Binance 后打开 [Demo API Management](https://demo.binance.com/zh-CN/my/settings/api-management)。
   若直接入口不可访问，可从 Binance 的 **Trade -> Demo Trading -> API Management** 进入；
   检查登录状态、网络和账户可用性。
2. 在 Demo 环境中创建 HMAC API key；不要使用 `www.binance.com` 的主网 API 管理页创建测试 key。
3. 保存 API Key 和 Secret；Secret 通常只显示一次，遗失后应撤销旧 key 并重新创建。
4. 按测试范围开启读取、现货交易和合约交易权限；设置 IP 白名单，不开启提现权限。

旧的 [Futures Testnet](https://testnet.binancefuture.com/) 入口正在逐步停止提供，不能再作为
本项目的必需配置路径。Binance 已在[官方公告](https://www.binance.com/en/support/announcement/detail/616402d041c74000bc78282018bc62d4)
中说明 Futures Testnet 网站访问会逐步不可用。

### Spot Testnet 的存在与边界（项目不接入）

[Binance Spot Testnet](https://testnet.binance.vision/) 是仍在使用的独立现货 API 测试网，
与 Spot Demo 并存；可在其页面登录并生成专属 key。

| 环境 | 创建 key 的网站 | 现货 REST 基础地址 | 本项目测试环境 |
|---|---|---|---|
| Spot Demo | `demo.binance.com` | `https://demo-api.binance.com`（`/api/v3/*`） | 使用此环境及其 key |
| Spot Testnet | `testnet.binance.vision` | `https://testnet.binance.vision`（`/api/v3/*`） | 仅文档说明，不接入 |

两套环境的 key、余额和订单不能混用。即使只测试现货，本项目仍使用 Spot Demo；
Spot Testnet key 也不能用于本项目的 `BTC/USDT:USDT` 永续合约。
官方说明见 [Spot Testnet](https://github.com/binance/binance-spot-api-docs/blob/master/testnet/general-info.md)。

Binance 的[Demo Mode API 说明](https://github.com/binance/binance-spot-api-docs/blob/master/demo-mode/general-info.md)
也列出了 Demo API 的 REST/WebSocket host。项目中的 API host 由 CCXT 的 demo 模式自动配置，不能在 `config/exchanges.yaml` 中
手工把 `demo.binance.com` 当作 REST API host。现货对应 `demo-api.binance.com`，
U 本位合约对应 `demo-fapi.binance.com`；默认凭据统一称为 Demo Trading key。

### 配置

```yaml
network: "testnet"
binance:
  apiKey: "<binance-demo-api-key>"
  secret: "<binance-demo-hmac-secret>"
```

## 验证与执行边界

先复制示例文件并限制权限：

```bash
cp config/secrets.testnet.example.yaml config/secrets.testnet.yaml
chmod 600 config/secrets.testnet.yaml
```

保留模板中的顶层 `network: "testnet"`，把上面需要的交易所条目放在同一个测试网文件中。
如果文件已经存在，直接编辑它，不要用模板覆盖已有凭据。主网文件使用对应的主网模板和
`network: "mainnet"`；完整的加载规则及旧配置迁移见[配置指南](index.md)。

只读检查固定读取测试网凭据，不会发送订单：

```bash
uv run --locked onefill arb testnet-smoke \
  --venues arcus,hyperliquid,binance --symbol BTC --market perp --account --json
```

检查结果中的 `credentials_configured` 和账户余额状态。凭据配置存在仅表示本地字段齐全。
Arcus/Hyperliquid 账户数据可公开读取，余额成功不是服务器签名验证或下单权限证明；
smoke 是否尝试账户查询以其凭据门控为准。缺少密钥仍可读取公开行情。

完整的 Arcus / Hyperliquid 账户、订单、WS 与持久化回读验证使用
[DEX 测试网验证](../examples/dex-testnet-validation.md)的专用 pytest 入口。默认只读；
显式 `--dex-testnet-trades` 才允许限额交易，每笔最高 100 USD、整轮最高 5000 USD。
报告区分 `PASS`、`FAIL`、`BLOCKED`、`UNSUPPORTED`，不能把账户读取成功当作真实交易通过。

跨所测试网 Canary 必须显式传入确认词；它只执行一个小额开平周期：

```bash
uv run --locked onefill arb testnet-canary \
  --venue-a arcus --venue-b hyperliquid --base BTC --quantity 0.0001 \
  --max-notional-usd 25 --confirm TESTNET_CANARY --json
```

该命令永远使用测试网和 `secrets.testnet.yaml`，不接受主网目标，也不读取主网凭据；任何未配置凭据、市场不匹配或风险校验失败都会
在提交订单前拒绝。不要在自动化任务中绕过确认词或把测试网 key 用于主网。

## 安全检查清单

- `config/secrets.testnet.yaml`、`config/secrets.mainnet.yaml` 和公共 `config/secrets.yaml`
  必须保持 Git ignored，提交前检查 `git status` 和 diff。
- 使用测试网专用钱包/API key，定期撤销不再使用的 key。
- 对 API key 设置最小权限、IP 白名单和过期/轮换策略，关闭提现权限。
- 不在 shell 历史、CI 输出、异常堆栈或 JSON 日志中打印私钥和 Secret。
- 泄露后立即撤销 API key；链上钱包私钥泄露时转移测试资产并停用该地址。
