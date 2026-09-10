---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-10
applies_to: src/exchange/base.py, src/exchange/, src/exchange/factory.py and config/exchanges.yaml
---

# 交易所接入

本文档描述当前代码接入新交易所的固定流程。当前生产适配以 `CCXTExchange` 为主，`MockExchange` 只用于测试；文档不假设不存在于源码中的原生适配器。

## 接入前确认

先确认交易所是否提供当前所需的：

- spot 或 perp 市场及可查询的 listing 状态。
- orderbook、余额、下单、撤单和订单状态接口。
- 当前网络模式对应的 REST/WS endpoint。
- 产品类型、计价资产、数量精度、最小数量和费用字段。

如果交易所不能满足这些接口，先建立 `status: proposal` 的设计说明，不要在现有适配器中添加临时字段。

## 实现步骤

1. 在 `config/exchanges.yaml` 增加交易所配置、网络 endpoint、启用开关、费率和必要的 CCXT options。
2. 在 `config/secrets.example.yaml` 增加不含真实凭据的字段说明，并确认 `secrets.yaml` 仍被 gitignore。
3. 在 `src/exchange/ccxt.py` 中补充交易所特有的连接、市场筛选或参数映射；公共行为继续由 `BaseExchange` 定义。
4. 在 `src/exchange/factory.py` 中确认配置能创建该适配器，并能正确传递目标网络。
5. 在市场层把交易所市场映射成 `Instrument`。上层只能看到 `venue`、`market_type`、`base`、`quote` 和标准精度字段。
6. 为市场转换、连接、订单生命周期和失败路径添加测试。能用 `MockExchange` 覆盖的逻辑不得依赖真实网络。
7. 添加少量 `network` 标记的连通性测试，并在凭据缺失时让测试显式跳过，而不是静默通过。

## 不变量

- 不得把交易所原生 symbol 泄漏到 Planner、Validator 或策略层。
- 不得为了兼容单个交易所修改全局 `Intent`、`Instrument` 或状态名称。
- `create_order`、`cancel_order` 和 `fetch_order` 的异常必须保留交易所名称和订单上下文。
- 网络切换必须通过配置和 `NetworkType` 完成，不能在业务代码中硬编码 testnet/mainnet 分支。
- 新交易所的默认配置必须安全：默认不启用真实资金操作，且示例不得含真实 key。

## 验证清单

```bash
uv run --locked pytest tests/unit/exchanges -q
uv run --locked pytest tests/market tests/coordinator -q
uv run --locked pytest -m network -k '<venue>'
uv run onefill venues
uv run onefill instruments --venue <venue> --refresh
```

接入完成后，必须同步更新[当前状态](../reference/current-status.md)、[系统架构](sys-architecture.md)和 API Reference；如果已有接入文档的核心前提失效，删除旧文档后重新编写，不在旧文档中追加第二套流程。

