# OmniTrade

## 📌 项目简介

`OmniTrade` 旨在构建一个具备良好可拓展性的交易系统，主要关注 Perp DEX 浪潮之下的两个机会：1. 刷交易量赚取空投回报 2. 在不同 Perp DEX 之间做价格差套利交易。

OmniTrade 优先考虑通过实现在不同 Perp DEX 之间开对冲合约刷交易量，经过对市场中多个 Perp DEX 的调研，DEX 官网开发的 SDK 都具有 Python 版本，并且 Python 中拥有一个 CCXT 库，对众多主流 CEX 与少数 DEX API 接口进行了集成，大大降低了开发的复杂度，故系统优先牺牲性能选择 Python 作为开发语言。未来在实现套利交易时将会考虑开发追求性能的 Rust 版本。

目前系统已经实现了统一的交易所抽象层（BaseExchange）、可扩展的交易所适配器（CCXTExchange与定制化Exchange）、基础的价差检测引擎（ArbitrageEngine）、**对冲刷量引擎（VolumeEngine）**以及测试网与主网切换工具，便于在测试网环境中快速迭代与验证策略。

当前代码聚焦于：

- 通过统一接口管理 CCXT 生态和原生 API 交易所。
- 在协程环境下并发抓取买卖盘口数据。
- 基于最优买卖报价计算潜在套利机会。
- **通过对冲开仓方式智能刷交易量，内置反女巫机制。**
- 支持在主网/测试网网络之间一键切换。
- 为后续接入真实交易执行与风险控制预留扩展点。

## 项目目标一
在多个交易所之间自动化地开启对冲仓位，以尽可能小的磨损与反女巫方式（引入开单时间、仓位大小的随机性）刷交易量，赚取积分。

## 项目目标二
在多个交易所(Perp DEX 为主)之间实时监控行情差异并自动捕捉套利机会。Python 版本下只考虑理论可行性，仅验证逻辑，真实套利需开发 Rust 版本。

## ✨ 功能亮点

- **统一抽象层**：所有交易所均继承自 `BaseExchange`，确保具有一致的接口语义（行情、余额、下单等）。
- **多交易所模式**：同时支持 CCXT 适配器（支持 Binance、OKX、Hyperliquid 等已经被 CCXT 集成的交易所）与原生适配器（如 Lighter、Paradex 等未被 CCXT 集成的交易所），方便按需扩展。
- **异步架构**：基于 `asyncio`、`aiohttp` 与 `websockets`，兼顾 REST 与实时行情流的响应速度。
- **价差监控引擎**：`ArbitrageEngine` 与 `SpreadArbitrageStrategy` 提供价差计算、排序与执行决策的核心逻辑。
- **🆕 对冲刷量引擎**：`VolumeEngine` 与 `HedgeVolumeStrategy` 实现智能刷量，通过在不同交易所之间开对冲仓位的方式安全刷量，内置反女巫机制（时间随机化、仓位随机化、交易所随机化）。
- **网络管理器**：`NetworkManager` 可批量切换所有交易所的主网/测试网配置，并检查网络一致性。
- **多模式运行**：支持套利监控、刷量、同时运行三种模式，灵活切换。
- **完善的测试雏形**：提供 Pytest 测试框架、夹具示例和若干交易所适配器的单元测试。

## 🧱 目录结构

```text
OmniTrade/
├── config/                     # 配置文件与密钥模板
│   ├── exchanges.yaml          # 交易所及网络配置
│   ├── volume_farming.yaml     # 刷量配置（新增）
│   ├── secrets.yaml            # 本地密钥（请勿提交）
│   └── secrets.example.yaml    # 密钥模板
├── src/
│   ├── core/
│   │   ├── arbitrage_engine.py # 套利机会计算核心
│   │   ├── volume_engine.py    # 刷量引擎核心（新增）
│   │   ├── base_exchange.py    # 交易所抽象基类
│   │   └── exchange_factory.py # 交易所工厂
│   ├── exchanges/              # 交易所适配器实现
│   │   ├── ccxt_exchange.py    # 创建 CCXT 已经集成的交易所实例
│   │   ├── lighter.py          # 基于官方 API 开发 CCXT 未集成的交易所实例
│   │   └── hl_example.py       # Hyperliquid 使用示例/草稿
│   ├── strategies/
│   │   ├── spread_arbitrage.py # 价差套利策略
│   │   └── hedge_volume.py     # 对冲刷量策略（新增）
│   ├── utils/
│   │   ├── data_processor.py   # 行情数据处理工具
│   │   ├── logger.py           # 日志工具
│   │   └── network_manager.py  # 网络切换工具
│   └── main.py                 # 机器人入口
├── tests/                      # Pytest 测试
│   ├── conftest.py             # 全局夹具
│   ├── integration/            # 集成测试占位
│   ├── unit/                   # 单元测试
│   └── fixtures/               # 测试数据占位
├── requirements.txt            # Python 依赖
├── pytest.ini                  # Pytest 配置
├── Dockerfile                  # （待实现）容器化配置
└── README.md                   # 项目文档（本文件）
```

## 🧠 核心组件概览

### 交易所层
- **`BaseExchange`**：定义标准化的交易所接口、统一的认证流程、REST/WebSocket 端点管理以及主网/测试网切换逻辑。
- **`ExchangeFactory`**：根据 `config/exchanges.yaml` 自动挑选 `CCXTExchange`、`LighterExchange` 或 `ParadexExchange` 等具体实现并完成初始化。

### 套利模块
- **`ArbitrageEngine`**：异步聚合各交易所订单簿，并对任意两交易所组合计算双向价差，返回满足阈值的机会列表。
- **`SpreadArbitrageStrategy`**：在策略层面计算价差与可执行体量，提供风险预算、成交量裁剪与资金校验方法。

### 🆕 刷量模块
- **`VolumeEngine`**：对冲刷量引擎，负责在不同交易所之间智能开对冲仓位，并管理仓位生命周期。核心特性：
  - 时间随机化：开仓和持仓时间随机，避免被识别为机器人
  - 仓位随机化：使用对数正态分布生成仓位大小
  - 交易所随机化：随机选择交易所组合
  - 价差控制：只在价差可接受范围内开仓
  - 风险管理：并发仓位限制、每日交易量限制、价差成本控制
- **`HedgeVolumeStrategy`**：刷量策略层，提供更智能的决策：
  - 根据目标完成度和优先级选择交易对
  - 动态计算最优仓位大小
  - 智能平仓决策（考虑持仓时间和价差变化）
  - 进度追踪和报告

### 基础设施
- **`NetworkManager`**：批量切换网络、检查一致性、输出各交易所当前网络状态。
- **`TradeBot` (`main.py`)**：整合配置加载、交易所创建、网络切换、机会监控、刷量管理与信号处理，是运行时的协调中心。支持三种运行模式：套利监控、刷量、同时运行。
- **`DataProcessor` 与 `logger` 工具**：提供订单簿归一化、统计分析、加权平均价计算和统一的日志配置。

项目整体执行流程如下：

1. 读取 `config/exchanges.yaml` 与 `config/secrets.yaml`。
2. `ExchangeFactory` 根据配置创建并异步连接交易所。
3. `NetworkManager` 可选地将所有交易所切换至指定网络，并输出当前状态。
4. `ArbitrageEngine` 周期性获取订单簿，计算跨交易所价差。
5. 控制台输出满足阈值的套利机会，为后续下单执行、风险控制提供依据。

## ⚙️ 配置说明

### 交易所与网络配置

`config/exchanges.yaml` 定义每个交易所的启用状态、类型、支持的交易对以及主网/测试网端点。例如：

```yaml
exchanges:
  lighter:
    type: native
    enabled: true
    default_network: testnet
    networks:
      testnet:
        rest_base_url: https://api-testnet.lighter.com
        websocket_url: wss://ws-testnet.lighter.com
        api_paths:
          orderbook: /api/v1/orderbook
          balance: /api/v1/account/balance
          order: /api/v1/order
    symbols: ["ETH/USD", "BTC/USD"]
```

> 注意：文档中的 URL 和交易对仅为示例，实际可根据交易所官方接口调整。

### 密钥管理

- 模板文件：`config/secrets.example.yaml`
- 实际密钥：`config/secrets.yaml`（应加入 `.gitignore`，当前仓库内的示例密钥仅用于演示，请立即替换为你自己的测试网凭证或环境变量注入）。
- 推荐做法：部署时通过环境变量或密钥管理服务（例如 AWS Secrets Manager）注入，以降低泄露风险。

## 🚀 快速开始

### 1. 准备环境

- Python ≥ 3.10（建议使用 `pyenv` 或 `conda` 管理虚拟环境）
- 安装系统依赖（若需要编译依赖库，请确保已安装 `gcc` 等工具）

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 2. 配置交易所

```bash
cp config/secrets.example.yaml config/secrets.yaml
# 编辑 config/secrets.yaml，填入测试网 API 或私钥
```

若需自定义交易所列表或切换默认网络，请修改 `config/exchanges.yaml`。

### 3. 交易所刷量

查看文档 VOLUME_FARMING_GUIDE.md 了解详细情况

### 4. 运行套利机器人

```bash
python -m src.main --network testnet
```

常用参数：

- `--network mainnet` / `--network testnet`：初始化后自动切换所有交易所到指定网络。

停止程序：按 `Ctrl + C`，程序会触发信号处理器执行清理逻辑。

## 🧪 测试与质量保障

- 运行全部测试：

```bash
pytest
```

- 只运行单元测试：

```bash
pytest tests/unit -vv
```

```bash
# 测试基于 CCXTExchange class 实现对 hyperliquid 的访问
python -m tests.unit.exchanges.test_hyperliquid
```

```bash
# 测试基于原生 API 实现的 paradex 访问
python -m tests.unit.exchanges.test_paradex
```

- 查看慢测试与日志：仓库已在 `pytest.ini` 中启用 `--durations=10` 和实时日志输出。

> 当前 `tests/integration`、`tests/fixtures` 多为占位文件，可据此扩展真实 REST/WebSocket 集成测试。

## 📊 刷量模块使用指南 (Volume Farming Guide)

OmniTrade 的刷量引擎采用**无损对冲 (Lossless Hedging)** 策略，在两个交易所之间同时开设相反方向的仓位（一个做多，一个做空），从而在保持市场中性（Delta Neutral）的同时刷取交易量。

### 🌟 核心特性

1.  **无损/盈利对冲**: 引擎会自动计算两个交易所的价差和手续费（Taker/Maker），只有当 `净利润 > 阈值` 时才会开仓。
    *   如果阈值设为 `0`，则实现**无损刷量**（覆盖手续费成本）。
    *   如果阈值设为 `>0`，则实现**套利刷量**（刷量的同时获利）。
2.  **反女巫机制**: 内置多重随机化机制（时间、仓位大小、交易所选择），模拟真实人类交易行为。
3.  **智能资金管理**: 
    *   启动前自动检查交易所余额，确保资金充足。
    *   运行时持续监控保证金使用情况。
    *   资金不足时自动平仓释放保证金，避免强平风险。
4.  **USD 价值计价**: 仓位大小直接以 USD 价值设定，自动适配不同标的（BTC/ETH 等），无需手动换算。

### ⚡ 快速开始

#### 第一步：配置 API 密钥

编辑 `config/secrets.yaml`，填入您的交易所 API 密钥：

```yaml
exchanges:
  hyperliquid:
    api_key: "your-api-key"
    api_secret: "your-api-secret"
    wallet_address: "your-wallet-address"  # Hyperliquid 特需
  
  paradex:
    api_key: "your-api-key"
    api_secret: "your-api-secret"
    account: "your-account-address"  # Paradex 特需
```

> ⚠️ **重要**: 请勿将 `secrets.yaml` 提交到代码仓库！

#### 第二步：配置交易所费率

编辑 `config/exchanges.yaml`，设置正确的费率（用于计算无损对冲）：

```yaml
exchanges:
  hyperliquid:
    fees:
      taker: 0.00035   # 根据您的 VIP 等级调整
      maker: -0.00002
  
  paradex:
    fees:
      taker: 0.0003
      maker: 0.0
```

#### 第三步：配置刷量策略

编辑 `config/volume_farming.yaml`，根据您的资金量和风险偏好调整参数：

**关键参数说明：**

| 参数分类 | 参数名 | 说明 | 推荐值/示例 |
| :--- | :--- | :--- | :--- |
| **基础** | `enabled` | 是否启用刷量功能 | `true` |
| | `exchanges` | 参与刷量的交易所列表 | `["paradex", "hyperliquid"]` |
| **时间 (反女巫)** | `min_interval` | 最小开仓间隔（秒） | `30` |
| | `max_interval` | 最大开仓间隔（秒） | `600` |
| | `min_position_lifetime` | 最小持仓时间（秒） | `300` (5分钟) |
| | `max_position_lifetime` | 最大持仓时间（秒） | `7200` (2小时) |
| **仓位 (USD 计价)** | `min_size` | 单笔最小仓位价值 (USD) | `50.0` - 小资金<br>`500.0` - 中等资金 |
| | `max_size` | 单笔最大仓位价值 (USD) | `100.0` - 小资金<br>`1000.0` - 中等资金 |
| | `leverage` | 杠杆倍数 | `2` |
| **风险控制** | `min_profit_threshold` | **最小净利润阈值 (%)** <br> `0.0`: 无损对冲 <br> `>0`: 套利对冲 <br> `<0`: 允许微亏 | `0.0` (推荐) |
| | `min_fund_balance` | **最小资金要求 (USD)** <br> 低于此值不启动或暂停开仓 | `50.0` - 测试<br>`500.0` - 实盘 |
| | `max_spread_tolerance` | 最大允许价差 (%) | `0.5` |
| | `daily_max_volume` | 每日最大刷量总额 | `1000` |
| | `max_concurrent_positions`| 最大同时持仓数 | `10` |

**配置示例（小资金量）：**

```yaml
volume_farming:
  enabled: true
  exchanges: ["paradex", "hyperliquid"]
  
  position:
    min_size: 50.0      # 每单最少 $50
    max_size: 100.0     # 每单最多 $100
    leverage: 2         # 2 倍杠杆（需要 $25-$50 保证金/单）
  
  risk:
    min_profit_threshold: 0.0   # 无损对冲
    min_fund_balance: 100.0     # 至少 $100 余额才启动
    max_concurrent_positions: 5 # 最多 5 个仓位同时持有
    daily_max_volume: 500       # 每日最多刷 $500
```

**配置示例（中等资金量）：**

```yaml
volume_farming:
  position:
    min_size: 500.0     # 每单最少 $500
    max_size: 1000.0    # 每单最多 $1000
    leverage: 2         # 2 倍杠杆（需要 $250-$500 保证金/单）
  
  risk:
    min_fund_balance: 1000.0    # 至少 $1000 余额
    max_concurrent_positions: 10
    daily_max_volume: 10000
```

#### 第四步：设置刷量目标

在 `volume_farming.yaml` 的 `targets` 部分设置具体交易对：

```yaml
targets:
  - symbol: "BTC/USD:USDC"
    daily_target_volume: 1000.0  # 每日目标交易量
    priority: 1                  # 优先级 (1 最高)
  
  - symbol: "ETH/USD:USDC"
    daily_target_volume: 500.0
    priority: 2
```

#### 第五步：启动刷量

```bash
# 测试网运行（推荐先测试）
python -m src.main --mode volume --network testnet

# 主网运行
python -m src.main --mode volume --network mainnet
```

### 💻 控制台输出说明

刷量引擎运行后，控制台会显示简洁的实时状态：

```
💰 检查初始资金 (最低要求: $500.0)...
✅ paradex 资金充足: $983860.14
✅ hyperliquid 资金充足: $903.11

🔍 [BTC/USD:USDC] 检查价差 (paradex <-> hyperliquid)...
🎯 发现机会 [BTC/USD:USDC]
   方向: paradex(多) <-> hyperliquid(空) | 预期收益: $15.23 (PnL: 1.52%)
   🚀 执行开仓 (Size: 0.0068, Value: $612.50)...
   ✅ 开仓成功 (ID: 538938)
      成本: $13.70 | 今日量: 612.50/10000
```

**输出说明：**
*   **💰 初始资金检查**: 启动时验证各交易所资金是否满足 `min_fund_balance`。
*   **🔍 检查价差**: 实时监控各交易所价格，寻找对冲机会。
*   **🎯 发现机会**: 当满足 `min_profit_threshold` 时，显示机会详情（方向、预期收益/成本）。
*   **🚀 执行开仓**: 显示开仓动作及 USD 价值、实际数量。
*   **✅ 开仓成功**: 显示成交详情、成本及今日累计刷量进度。
*   **⚠️ 资金不足**: 如果保证金不足，会自动平仓最小的仓位以释放资金。

### 🔐 资金管理机制

引擎内置多重资金安全保障：

1.  **启动检查**: 程序启动时检查所有交易所余额，低于 `min_fund_balance` 则拒绝启动。
2.  **实时监控**: 每次开仓前检查可用保证金，确保足够支付新仓位。
3.  **自动释放**: 如果资金不足：
    *   等待 5 分钟并重试（最多 3 次）。
    *   仍不足则**自动关闭成本最小的仓位**以释放保证金。
    *   避免因资金不足导致开仓失败或强平。

### 📈 统计报告说明

程序运行时每 5 分钟自动输出统计报告，各指标含义如下：

```
============================================================
📊 刷量统计报告
============================================================
活跃仓位: 2
总开仓次数: 5
累计交易量: $4523.50 USD
累计价差成本: $15.23
累计盈亏: $-2.45
平均价差成本: $3.05
平均持仓时长: 1245.3秒
今日交易量: $4523.50 USD
今日剩余额度: $5476.50
============================================================
```

| 指标 | 说明 | 计算方式 | 评估 |
| :--- | :--- | :--- | :--- |
| **活跃仓位** | 当前未平仓的对冲仓位数量 | 实时统计 | ✅ 正确 |
| **总开仓次数** | 自启动以来的总开仓次数（包括已平和未平） | 累计计数 | ✅ 正确 |
| **累计交易量** | 所有仓位的 USD 名义价值总和 | `Σ(开仓均价 × 数量)` | ✅ **已修复** <br> 现在正确显示 USD 价值 |
| **累计价差成本** | 所有仓位开仓时的总价差成本 | `Σ(|多头价 - 空头价| × 数量)` | ✅ 正确 <br> 反映实际成本 |
| **累计盈亏** | 已平仓位的总盈亏（不含手续费） | `Σ(平仓盈亏)` | ⚠️ 部分正确 <br> 仅统计已平仓，活跃仓位浮盈未计入 |
| **平均价差成本** | 每单平均开仓成本 | `累计价差成本 / 总开仓次数` | ✅ 正确 |
| **平均持仓时长** | 已平仓位的平均持仓时间（秒） | `Σ(持仓时长) / 已平仓数` | ✅ 正确 <br> 仅统计已平仓 |
| **今日交易量** | 今日累计刷量（USD 价值） | 实时累计，每日UTC 0点重置 | ✅ **已修复** <br> 现在正确显示 USD 价值 |
| **今日剩余额度** | 今日还可刷的交易量 | `daily_max_volume - 今日交易量` | ✅ 正确 |

**关键修复说明：**
*   ✅ **累计交易量** 和 **今日交易量** 已从"币的数量"修正为"USD 价值"，与配置中的 `daily_max_volume` 单位一致。
*   ✅ 所有 USD 相关数值现在都明确标注 `$` 符号和 `USD` 单位。

**指标合理性评估：**
1.  **累计盈亏** 仅统计已平仓位是合理的，因为活跃仓位的浮盈可能变化，难以准确反映最终盈亏。
2.  **平均持仓时长** 仅统计已平仓也是合理的，活跃仓位尚未结束，不应计入平均值。
3.  **累计价差成本** 使用绝对价差是准确的，它反映了对冲开仓时的实际成本（忽略方向）。

## 🔐 安全与合规建议

- **密钥安全**：切勿将真实主网私钥写入仓库；使用环境变量或密钥管理服务暴露到运行环境。
- **权限控制**：将交易所 API 权限限制在“只读”或“最低交易额度”直至策略成熟。
- **网络隔离**：建议在专用 VPS/云服务器中部署，配合防火墙或安全组限制访问。
- **资金风控**：在 `SpreadArbitrageStrategy` 中扩展最大持仓、回撤、滑点等风险参数，确保策略稳定。

## 🛠️ 故障排查提示

- **连接失败**：确认 `rest_base_url`、`websocket_url`、API 密钥和网络状态是否匹配。
- **请求限流**：交易所通常设置速率限制，可结合配置中的 `rate_limit` 字段或在实现中增加节流逻辑。
- **订单簿为空**：部分交易所在测试网可能不提供真实流动性，请切换其他交易对或改用模拟数据。
- **缺失模块**：`ExchangeFactory` 中的 `HyperliquidExchange` 尚未定义，可参考 `hl_example.py` 或 CCXT 文档自行实现。

## 📅 发展路线（Roadmap）

### 已完成 ✅
- ✅ 统一的交易所抽象层（BaseExchange）
- ✅ 多交易所适配器（CCXT + 原生 API）
- ✅ 套利机会监控引擎
- ✅ **对冲刷量引擎与策略**
- ✅ 主网/测试网切换工具
- ✅ 多模式运行支持

### 进行中 🚧
- 🚧 更多交易所适配器（Binance、OKX 等）
- 🚧 完善单元测试与集成测试

### 计划中 📋
- 📋 真实订单执行与风险控制模块
- 📋 监控/报警与可观测性（Prometheus/Grafana）
- 📋 持久化存储（PostgreSQL、DynamoDB）
- 📋 资金调度与自动平衡
- 📋 Web 控制面板
- 📋 Docker 镜像与云部署脚本
- 📋 回测框架
- 📋 性能分析工具
- 📋 Rust 版本（高频套利场景）

## 🤝 贡献指南

欢迎提交 Issue 与 Pull Request。建议步骤：

1. Fork 仓库并创建特性分支：`git checkout -b feature/my-update`
2. 编写/更新代码与测试。
3. 运行 `pytest` 确保现有测试通过。
4. 提交 PR 时说明变更动机、主要实现点与影响面。
5. 联系 0xkayne@gmail.com

## 📄 许可证

当前仓库未附带正式许可证文件。若计划对外开源，请添加合适的开源许可证（例如 MIT、Apache-2.0 等）。

## ⚠️ 风险提示

加密货币交易存在显著市场与合规风险。请先在测试网充分验证策略和风控，再考虑使用真实资金。本项目仅用于技术研究与教学示例，不提供任何投资建议。



