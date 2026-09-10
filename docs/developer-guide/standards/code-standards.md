---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-10
applies_to: src/, tests/, config/ and all new AI-assisted implementation work
---

# 代码结构规范

本文档定义当前项目的代码组织和命名边界。目标不是追求形式统一，而是让开发者和 AI 能够根据一个概念稳定地找到它的实现、测试和文档。

## 1. 模块分层

```text
src/
  cli/                 # CLI 参数、命令、装配和程序化入口
  coordinator/         # Intent 执行编排：规划、校验、风险、执行、回滚
  market/              # Asset、Instrument、Quote 和市场数据适配
  exchanges/           # 具体交易所适配器
  core/                # 共享交易所底层接口和 legacy 引擎
  persistence/         # SQLite、JSONL 和持久化模型
  observability/       # 指标和可观测性接口
  strategy/            # 新策略、信号、回测和策略数据服务
  strategies_legacy/   # 旧 TradeBot 策略，只维护兼容性
  utils/               # 无领域归属的通用工具
```

依赖方向必须保持清晰：

```text
cli → strategy / coordinator → market / exchanges / persistence
legacy → core / exchanges
observability ← coordinator / strategy / cli
```

- `market` 负责把交易所原生 symbol 转成统一的 `Instrument`；上层不得自行拼接或解析原生 symbol。
- `coordinator` 只处理执行，不生成交易策略。
- `strategy` 生成信号或构造 Intent；发单必须通过 Coordinator。
- `persistence` 不依赖 CLI 和具体策略；表结构变化必须同步迁移、读写代码和测试。
- `core` 不是新业务的默认归属地。新代码不得放入 `core`，除非它是所有新旧入口共享的底层交易所抽象。
- `strategies_legacy` 只保留旧入口所需的兼容代码，不与 `strategy/` 混用。

## 2. 文件和目录命名

- Python 文件使用小写 `snake_case.py`，目录使用小写 `snake_case/`。
- 文件名应表达一个主要职责：`planner.py`、`state_machine.py`、`quote_fetcher.py`；不要使用 `misc.py`、`common.py`、`new_x.py` 作为长期归属。
- 一个文件可以定义多个紧密相关的小型类型，但新的公共领域类型应放入职责明确的模块。
- 测试路径镜像源码职责：`src/coordinator/planner.py` 对应 `tests/coordinator/test_planner.py`；策略子包对应同名测试子目录。
- 新增模块时必须同时新增或更新对应测试和开发者文档；不能先创建孤立模块，之后再猜测它属于哪一层。
- `__init__.py` 只负责包边界和必要的公开导出，不在其中放业务实现。

## 3. 命名

类、类型别名、模块级函数的大小写约定、单位后缀、模块词根所有权、角色后缀保留词和易混名对照，全部定义在[命名规范](naming-conventions.md)。本文不重复这些规则。

领域术语的唯一性由[命名规范](naming-conventions.md) §4 维护；需要新概念时，先在[产品与领域约束](../design/sys-product-requirements.md)增加定义，再改代码，不要在某个局部模块中临时命名。

## 4. 参数和返回值

- 公共函数必须有类型标注和 docstring；docstring 使用 Google 风格，说明 Args、Returns 和 Raises。
- 使用 `dict[str, Any]` 前，先确认是否应定义 dataclass、TypedDict 或明确的返回模型。跨层数据优先使用显式类型。
- `None` 表示“没有值”时要在类型和文档中明确；不要用空字符串、`0` 或特殊字符串代替。
- 单位必须进入字段名、类型说明或 docstring。特别是价格、数量、名义金额、百分比和时间戳。
- 可变默认值必须使用 `default_factory`；不得以模块级可变对象作为函数默认参数。
- 配置读取后应在边界处完成校验和归一化，核心模块不要反复猜测配置格式。

## 5. 异步和副作用

- 网络 I/O、数据库 I/O 和交易所下单接口使用 `async def`，调用方必须显式 `await`。
- Planner、Validator 和 RiskValidator 保持无交易副作用；Executor、Reconciler 和明确标注的策略运行器才允许发单。
- 任何 `create_order` 之前必须先持久化对应 `Leg`。
- 不要在 import 阶段连接交易所、创建后台任务或读取真实凭据。
- 异常消息必须包含动作、venue、symbol 或 intent/leg 标识等足够上下文；不得吞掉异常后返回看似成功的空结果。

## 6. 配置和安全

- 配置键保持 YAML 中的正式拼写；在代码中只在一个边界层读取和转换。
- `config/secrets.yaml` 永远不进入文档示例、日志和测试输出；只引用 `secrets.example.yaml`。
- 新增配置必须同时更新示例配置、用户配置文档和失败校验测试。
- 网络、交易所、产品类型和状态值不得散落为未经约束的字符串；优先复用现有常量或类型。

## 7. 变更流程

每次功能变更按以下顺序完成：

1. 确认功能属于哪个模块和现有领域概念。
2. 先写或更新失败测试，明确输入、输出和副作用。
3. 在对应目录实现，保持依赖方向和命名规范。
4. 更新当前开发者文档和 API docstring。
5. 如果原有文档核心前提失效，直接删除旧文档并重写，不保留并列的历史规范。
6. 运行离线测试、lint 和严格文档构建。

## 8. 当前代码的有意例外

- `src/core/` 中的 `VolumeEngine`、`ArbitrageEngine` 是 legacy 代码，保留其现有命名以维持兼容性。
- 交易所适配器遵循 CCXT 的 `symbol`、`amount`、`side` 等接口名称；这些名称不得扩散到领域层。
- 旧代码中的 `exchange`、`strategy` 和历史配置键不因本规范一次性重命名；只有新代码和被修改的边界使用本规范。

