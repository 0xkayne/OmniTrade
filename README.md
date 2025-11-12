# arbitrage_bot —— 加密货币跨交易所套利机器人

## 📌 项目简介

`arbitrage_bot` 是一个基于 Python 的异步加密货币跨交易所套利系统原型，目标是在多个交易所之间实时监控行情差异并自动捕捉套利机会。系统已经实现了统一的交易所抽象层、可扩展的交易所适配器、基础的价差检测引擎以及网络切换工具，便于在测试网环境中快速迭代与验证策略。

当前代码聚焦于：

- 通过统一接口管理 CCXT 生态和原生 API 交易所。
- 在协程环境下并发抓取买卖盘口数据。
- 基于最优买卖报价计算潜在套利机会。
- 支持在主网/测试网网络之间一键切换。
- 为后续接入真实交易执行与风险控制预留扩展点。

## ✨ 功能亮点

- **统一抽象层**：所有交易所均继承自 `BaseExchange`，确保具有一致的接口语义（行情、余额、下单等）。
- **多交易所模式**：同时支持 CCXT 适配器（支持 Binance、OKX、Hyperliquid 等已经被 CCXT 集成的交易所）与原生适配器（如 Lighter、Paradex 等未被 CCXT 集成的交易所），方便按需扩展。
- **异步架构**：基于 `asyncio`、`aiohttp` 与 `websockets`，兼顾 REST 与实时行情流的响应速度。
- **价差监控引擎**：`ArbitrageEngine` 与 `SpreadArbitrageStrategy` 提供价差计算、排序与执行决策的核心逻辑。
- **网络管理器**：`NetworkManager` 可批量切换所有交易所的主网/测试网配置，并检查网络一致性。
- **完善的测试雏形**：提供 Pytest 测试框架、夹具示例和若干交易所适配器的单元测试。

> 备注：设计文档中提到的 Rust 拓展、AWS 部署、监控面板等高级特性尚未在当前仓库中落地，可作为后续迭代方向。

## 🧱 目录结构

```text
arbitrage_bot/
├── config/                     # 配置文件与密钥模板
│   ├── exchanges.yaml          # 交易所及网络配置
│   ├── secrets.yaml            # 本地密钥（请勿提交）
│   └── secrets.example.yaml    # 密钥模板
├── src/
│   ├── core/
│   │   ├── arbitrage_engine.py # 套利机会计算核心
│   │   ├── base_exchange.py    # 交易所抽象基类
│   │   └── exchange_factory.py # 交易所工厂
│   ├── exchanges/              # 交易所适配器实现
│   │   ├── ccxt_exchange.py
│   │   ├── lighter.py
│   │   ├── paradex.py
│   │   └── hl_example.py       # Hyperliquid 使用示例/草稿
│   ├── strategies/
│   │   └── spread_arbitrage.py # 价差套利策略
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

- **`BaseExchange`**：定义标准化的交易所接口、统一的认证流程、REST/WebSocket 端点管理以及主网/测试网切换逻辑。
- **`ExchangeFactory`**：根据 `config/exchanges.yaml` 自动挑选 `CCXTExchange`、`LighterExchange` 或 `ParadexExchange` 等具体实现并完成初始化。
- **`ArbitrageEngine`**：异步聚合各交易所订单簿，并对任意两交易所组合计算双向价差，返回满足阈值的机会列表。
- **`SpreadArbitrageStrategy`**：在策略层面计算价差与可执行体量，提供风险预算、成交量裁剪与资金校验方法。
- **`NetworkManager`**：批量切换网络、检查一致性、输出各交易所当前网络状态。
- **`ArbitrageBot` (`main.py`)**：整合配置加载、交易所创建、网络切换、机会监控与信号处理，是运行时的协调中心。
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

### 3. 运行套利机器人

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
# 测试基于 CCXTExchange class 实现对 hyperliquid 的访问
python -m tests.unit.exchanges.test_paradex
```

- 查看慢测试与日志：仓库已在 `pytest.ini` 中启用 `--durations=10` 和实时日志输出。

> 当前 `tests/integration`、`tests/fixtures` 多为占位文件，可据此扩展真实 REST/WebSocket 集成测试。

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

后续可考虑：

- 实现 `HyperliquidExchange`、Binance 测试网等适配器的完整功能。
- 引入真正的订单执行、风险控制与资金调度模块。
- 建立监控/报警与可观测性（Prometheus/Grafana、自定义指标等）。
- 引入持久化存储（如 PostgreSQL、DynamoDB）保存成交与行情历史。
- 完善 CI/CD 流程、Docker 镜像与云部署脚本。
- 编写更完整的集成测试、回测框架和性能分析工具。

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

---

如需进一步了解设计愿景，请参考 DeepSeek 分享链接；如需定制化开发或问题反馈，欢迎在 Issue 区留言。祝使用顺利！

