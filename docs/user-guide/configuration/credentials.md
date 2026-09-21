---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-14
applies_to: config/secrets.yaml, testnet adapters
---

# Testnet Credentials

本页说明 Arcus、Hyperliquid 和 Binance 测试网凭据的类型、获取方式，以及如何填入
`config/secrets.yaml`。示例中的值都是占位符；不要把真实私钥、API Secret 或助记词写入
Git、日志、Issue 或文档。

## 凭据对照

| 交易所 | oneFill 适配器 | 认证凭据 | 测试网环境 |
|---|---|---|---|
| Arcus | native | Ethereum 主地址 + Ed25519 API 公私钥 | Arcus Testnet |
| Hyperliquid | CCXT | Ethereum 风格钱包地址 + 私钥 | Hyperliquid Testnet |
| Binance | CCXT | HMAC API Key + Secret | Binance Futures Demo |

三套凭据相互独立。Hyperliquid 的 EVM 私钥不能替代 Arcus 的 Ed25519 私钥，也不能替代
Binance 的 HMAC Secret。测试网凭据应与主网凭据分开创建。

## Arcus

Arcus 账户由 Ethereum 主地址标识，但 API 请求和订单使用独立的 Ed25519 密钥签名。
Ed25519 公钥作为 Arcus `api_key`，Ed25519 私钥作为 `private_key`；Ethereum 私钥只在
注册或管理 API key 时用于钱包签名，不填入 `private_key`。

### 获取步骤

1. 准备一个只用于测试网的 Ethereum 钱包地址和私钥。不要复用主网资金钱包。
2. 在 [Arcus Testnet API Keys](https://testnet.arcus.xyz/api-keys) 页面创建或注册 API key。
3. 按 [Arcus Authentication](https://docs.arcus.xyz/api-reference/authentication) 的流程生成
   Ed25519 密钥并完成主地址绑定。手动生成时可以使用：

   ```bash
   openssl genpkey -algorithm ed25519 -out arcus-api-private.pem
   openssl pkey -in arcus-api-private.pem -pubout -outform DER \
     | tail -c 32 | xxd -p -c 32
   ```

   命令输出的 32 字节十六进制值是公钥，填入 `api_key`。妥善保存 PEM 私钥；转换为
   oneFill 接受的 32 字节十六进制私钥时，以 Arcus 文档要求的格式为准。
4. 在 `address` 填写与 API key 绑定的 Ethereum 主地址（包含 `0x` 前缀）。

### 配置

```yaml
arcus:
  api_key: "<arcus-ed25519-public-key>"
  private_key: "<arcus-ed25519-private-key>"
  address: "<arcus-ethereum-master-address>"
```

测试网 REST/WebSocket 地址由 `config/exchanges.yaml` 的 `default_network: testnet` 选择，
对应 `api.testnet.arcus.xyz`。Arcus 账户查询可只配置地址；下单、撤单和成交查询必须同时
配置 Ed25519 公私钥。

## Hyperliquid

Hyperliquid 使用 Ethereum 风格的钱包签名。`privateKey` 是签名下单的私钥，
`walletAddress` 是对应地址；如使用子账户或 vault，可另外配置 `vaultAddress`。

### 获取步骤

1. 在 [Hyperliquid Testnet](https://app.hyperliquid-testnet.xyz/) 创建或导入测试网钱包。
2. 按 [Testnet faucet](https://hyperliquid.gitbook.io/hyperliquid-docs/onboarding/testnet-faucet) 的
   条件领取 mock USDC，并确认钱包地址与私钥匹配。
3. 参考 [Hyperliquid API 文档](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api)。
   测试网 API 使用 `https://api.hyperliquid-testnet.xyz`，与主网 URL 不同。

### 配置

```yaml
hyperliquid:
  walletAddress: "<hyperliquid-ethereum-address>"
  privateKey: "<hyperliquid-testnet-private-key>"
  # vaultAddress: "<optional-vault-address>"
```

`privateKey` 只用于 CCXT 私有请求签名。不要把助记词直接放入配置；如果钱包软件导出
私钥，请在离线环境保存原始助记词，并只把测试网专用私钥写入受保护的 secrets 文件。

## Binance Demo Trading

完整的产品和 endpoint 矩阵见 [Binance API Integration Reference](../../reference/binance-api-reference.md)。

当前项目的 Binance `testnet` 指 Binance Demo Trading，不是 Binance Spot Testnet。
它使用 HMAC-SHA256 的 `apiKey` 和 `secret`，与 Ethereum 钱包、Arcus Ed25519 密钥无关。

`https://demo.binance.com/` 不是所有地区和账户都能直接打开的固定入口。Demo Trading
是否显示、是否允许创建 API key 由 Binance 的地区和账户资格决定。应以 Binance 的
[官方测试网与 Demo Trading 指南](https://www.binance.com/en/support/faq/detail/ab78f9a1b8824cf0a106b4229c76496d)
为准，从已登录的 Binance 官方区域站点进入 **Trade -> Demo Trading**，再在账户菜单的
**API Management** 中创建 HMAC API key。如果菜单不可见，通常表示该账户或地区暂不具备
Demo Trading 资格；这不是通过反复访问 `demo.binance.com` 可以解决的网络问题。

### 获取步骤

1. 登录 Binance 官方区域站点，并确认账户可以看到 **Trade -> Demo Trading**。
2. 进入 Demo Trading 后打开 **API Management**，创建 HMAC API key。
3. 保存 API Key 和 Secret；Secret 通常只显示一次，遗失后应撤销旧 key 并重新创建。
4. 仅开启读取和合约交易所需权限，设置 IP 白名单，并关闭提现权限。

旧的 [Futures Testnet](https://testnet.binancefuture.com/) 入口正在逐步停止提供，不能再作为
本项目的必需配置路径。Binance 已在[官方公告](https://www.binance.com/en/support/announcement/detail/616402d041c74000bc78282018bc62d4)
中说明 Futures Testnet 网站访问会逐步不可用。

### Spot Testnet 的边界

[Binance Spot Testnet](https://testnet.binance.vision/) 可以直接用于现货测试：使用 GitHub 登录，
在 API Key 页面生成 HMAC key。但它只覆盖 Spot API，不能为本项目的
`BTC/USDT:USDT` 等永续合约替代 Demo Trading 凭据。

Binance 的[Demo Mode API 说明](https://github.com/binance/binance-spot-api-docs/blob/master/demo-mode/general-info.md)
也列出了 Demo API 的 REST/WebSocket host。项目中的 API host 由 CCXT 的 demo 模式自动配置，不能在 `config/exchanges.yaml` 中
手工把 `demo.binance.com` 当作 REST API host。不要把 Spot Testnet 的 key 填入 Futures Demo
配置；两者属于不同环境和权限体系。

### 配置

```yaml
binance:
  apiKey: "<binance-futures-demo-api-key>"
  secret: "<binance-futures-demo-hmac-secret>"
```

## 验证与执行边界

先复制示例文件并限制权限：

```bash
cp config/secrets.example.yaml config/secrets.yaml
chmod 600 config/secrets.yaml
```

只读检查不会发送订单：

```bash
uv run --locked onefill arb testnet-smoke \
  --venues arcus,hyperliquid,binance --symbol BTC --market perp --account --json
```

检查结果中的 `credentials_configured` 和账户余额状态。缺少某家私有凭据时，该家仍可
读取公开行情，但账户查询会被跳过。

测试网 Canary 是唯一的受保护下单入口，必须显式传入确认词；它只执行一个小额开平周期：

```bash
uv run --locked onefill arb testnet-canary \
  --venue-a arcus --venue-b hyperliquid --base BTC --quantity 0.0001 \
  --max-notional-usd 25 --confirm TESTNET_CANARY --json
```

该命令永远使用测试网，不接受主网目标；任何未配置凭据、市场不匹配或风险校验失败都会
在提交订单前拒绝。不要在自动化任务中绕过确认词或把测试网 key 用于主网。

## 安全检查清单

- `config/secrets.yaml` 必须保持 Git ignored，提交前检查 `git status` 和 diff。
- 使用测试网专用钱包/API key，定期撤销不再使用的 key。
- 对 API key 设置最小权限、IP 白名单和过期/轮换策略，关闭提现权限。
- 不在 shell 历史、CI 输出、异常堆栈或 JSON 日志中打印私钥和 Secret。
- 泄露后立即撤销 API key；链上钱包私钥泄露时转移测试资产并停用该地址。
