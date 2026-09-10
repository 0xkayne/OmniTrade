---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-10
applies_to: all files under docs/ and all AI-assisted project changes
---

# Docs 编写规范

本文档是 OmniTrade 文档系统的唯一编写规范。所有新增、修改和删除文档的工作都必须遵循本文档；当其他文档与本文档冲突时，以本文档为准。

## 1. 文档系统目标

文档的首要读者是项目开发者和参与开发的 AI。文档必须让读者能够回答三件事：

1. 当前项目有哪些已经实现的能力。
2. 当前能力使用哪些唯一的概念、类型、状态和路径表达。
3. 修改代码时哪些约束不能被破坏。

文档不是聊天记录、开发日志或愿望清单。不能把推测、过期实现、临时任务拆分和未验证的代码示例写成当前规范。

## 2. 目录结构

`docs/` 只保留两个业务文档目录：`user-guide/` 面向使用者，`developer-guide/` 面向开发者和 AI。两者内部都按文档用途划分为固定子目录，不允许出现其他子目录：

```text
docs/
  docs-paradigm.md       # 本文档，唯一的文档编写规范
  index.md               # 文档入口
  user-guide/            # 用户运行项目必须知道的内容
    index.md             # user-guide 的索引
    getting_started/     # 安装、配置与第一次运行
    cli/                 # 命令行使用方式
    configuration/       # 配置项与预交易限制
    examples/            # 功能使用示例
    api/                 # 可供外部使用的接口
  developer-guide/       # 开发、扩展、设计和查阅的全部内容
    index.md             # developer-guide 的索引和阅读路径
    design/              # 功能模块与架构的设计文档；文件名 <域>-<主题>.md
    standards/           # 开发规范定义文档
    reference/           # 参考文档
      api/               # 从源码 docstring 生成的公开 Python API
    llm-harness/         # 预留：项目开发使用的 skill、MCP、plugin、AGENTS.md 等
```

### `user-guide/`

只描述当前用户可以使用的能力：安装、配置、命令、运行方式、输出、风险和故障处理。用户指南中的命令和配置示例必须与当前源码一致，并且优先使用可以直接复制执行的示例。

| 子目录 | 收录内容 |
|---|---|
| `getting_started/` | 安装步骤、凭据与风险配置、第一次 `--dry-run`（至少包含 `quickstart.md`） |
| `cli/` | 命令行入口、参数、输出和退出码 |
| `configuration/` | 配置文件说明和预交易限制、人工恢复流程 |
| `examples/` | 按功能分组的可运行示例 |
| `api/` | 可供外部调用的接口；只能写已在源码和测试中确认的接口，未实现的 HTTP / RPC 服务必须标注为 proposal 或直接不写 |

### `developer-guide/`

`developer-guide/` 按文档**用途**划分子目录，而不是按技术名词或历史任务划分。新增文档必须先判断它属于哪一类；除 `index.md` 外，不允许在 `developer-guide/` 根目录直接新增页面。

| 子目录 | 收录内容 | 判定标准 |
|---|---|---|
| `design/` | 系统架构、产品与领域约束、状态机、不变量、各层设计、交易所接入设计、策略设计、Agent 接口设计 | 回答「系统为什么这样设计、各模块如何协作」 |
| `standards/` | 代码结构、命名、测试规范及其他开发约定 | 回答「写代码时必须遵守什么」 |
| `reference/` | 源码 docstring 生成的 API、当前实现状态、外部 venue API 等查阅型资料 | 回答「查什么」，不描述做法 |
| `llm-harness/` | 项目开发使用的 skill、MCP、plugin、AGENTS.md 等 | 服务于 AI 开发流程本身，不描述产品行为 |

设计文档和 API 文档不能再作为 `docs/` 顶层分类存在，只能出现在 `developer-guide/design/` 和 `developer-guide/reference/` 下。

#### `design/` 的文件命名

`design/` 保持平铺，不建子目录；**层级由文件名前缀表达**。每份设计文档必须命名为
`<域>-<主题>.md`，域前缀是封闭集合，与 `src/` 的依赖方向一致：

| 域 | 含义 | 对应源码 |
|---|---|---|
| `sys-` | 全系统，跨层 | 无单一包 |
| `base-` | 执行内核与基础层（oneFill 本体） | `src/exchange/`、`src/market/`、`src/coordinator/`、`src/persistence/` |
| `strat-` | 策略层（消费执行内核） | `src/strategy/` |
| `entry-` | 外部入口与边界 | `src/cli/` |
| `legacy-` | 兼容边界 | `src/legacy/` |

新增文档必须落入已有域；确实需要新域时，**先在本规范登记再创建文件**。

### 根目录文件

根目录只放 `docs-paradigm.md` 和 `index.md`。

**架构图等图示一律用 Mermaid 内联在正文中**，不导出为图片：Mermaid 是纯文本，能随代码一起 diff，GitHub 与 MkDocs 都能直接渲染，而导出的 PNG / SVG 没有生成脚本就无法重新产出，重构一次就会过期。确实需要位图等静态资源时再放 `docs/assets/`，但它不作为文档分类。功能说明、需求说明、状态说明和理论说明必须归入 `developer-guide/`；用户操作说明必须归入 `user-guide/`。

## 3. 权威级别

项目采用以下权威顺序：

1. 当前源码、配置文件和测试：已实现行为的最终依据。
2. `authority: normative` 的开发者文档：对概念、流程、状态和约束的正式说明。
3. `authority: reference` 的用户/API 文档：对当前使用方式或公开接口的准确索引。
4. `authority: proposal` 的设计文档：尚未实现或尚未批准的方案，不能指导当前代码修改。

当源码和文档不一致时，先修正文档或明确记录代码缺陷；不能在文档中保留两个都像正确答案的说法。

## 4. 正式文档元数据

每份正式文档必须在文件开头声明以下元数据：

```yaml
status: current       # current | proposal | deprecated
authority: normative  # normative | reference | proposal
owner: project maintainers
updated: YYYY-MM-DD
applies_to: "具体模块、命令或版本范围"
```

字段约束：

- `current` 只能描述当前代码已经支持的内容。
- `proposal` 必须同时说明未实现的部分和批准条件。
- `deprecated` 只用于仍需短期迁移的内容；不再适用的旧文档直接删除，不建立 archive 目录保存过期规范。
- `updated` 在文档内容发生变化时必须更新。
- `applies_to` 必须写出模块、命令、配置或版本范围，不能使用“全部系统”这类无法验证的描述。

## 5. 文档生命周期

功能发生变化时，文档维护遵循以下流程：

1. 先检查现有文档是否仍准确覆盖新实现。
2. 能够保持原文档语义时，直接修改原文档并更新 `updated`。
3. 原文档的核心前提已经失效时，删除旧文档，重新创建一份完整的新文档；不要在旧文档末尾不断叠加“新版本说明”。
4. 删除或重命名文档后，必须同步更新 `index.md`、MkDocs 导航、交叉链接和 AI 入口文件。
5. 完成代码功能时，代码、测试和当前文档必须在同一次变更中保持一致。

禁止把以下内容作为当前文档保留：阶段性 subagent 契约、已完成的重构计划、过时的状态快照、与当前代码路径不一致的接入指南、无法运行的配置示例。

## 6. 术语和标识符规则

- 一个概念只定义一个正式名称。别名只能在第一次出现时注明，之后全部使用正式名称。
- 状态、枚举值、命令、配置键、模块路径和类名必须使用源码中的精确拼写。
- 不得为了让文字“更好懂”而创建源码中不存在的新状态或新层级。
- 文档中使用的缩写必须在第一次出现时展开；跨文档使用的术语应先写入术语章节，再在其他文档复用。
- 任何“当前”“默认”“支持”“保证”等断言都必须能在源码、配置或测试中找到依据。

## 7. 各类文档的写法

### 用户文档（`user-guide/`）

按“目的 → 前置条件 → 命令/配置 → 预期结果 → 失败处理”组织。每个命令至少包含用途、必填参数、一个可运行示例和退出/错误行为。不要把内部类名、历史阶段名或未公开接口写入用户指南。

### 开发规范（`standards/`）

按“范围 → 组件职责 → 数据流/状态 → 不变量 → 源码位置 → 测试位置”组织。架构文档只描述当前实现；无法从代码确认的内容必须单独标记为 proposal。

### API Reference（`reference/api/`）

API Reference 以源码 docstring 自动生成内容为准。手写部分只负责模块索引、公开入口和调用约束，不复制另一套函数签名或参数默认值。

### 设计文档（`design/`）

文件名必须是 `<域>-<主题>.md`，域前缀取自本文 §2 的封闭集合，**不得使用集合之外的域**。

正文必须说明问题、决策、替代方案、影响范围和实现状态。已批准但尚未实现的设计使用
`status: proposal`；实现完成后应合并进 `standards/` 或 `reference/`，并删除重复的设计正文。
一个功能域的完整设计文档应同时覆盖**模型/为什么**与**实现/怎么做**两层——
只写理论而不描述对应源码的文档会与实现脱节，不算合格的设计文档。

## 8. AI 开发约束

AI 开始编码前必须：

1. 阅读本文档和对应的 `user-guide/` 或 `developer-guide/` 页面。
2. 使用 `rg` 检查已有术语、状态、配置键和模块路径。
3. 先复用现有概念；确需新概念时，在开发者规范中先定义，再修改代码。
4. 不得因为问题暂时没有文档就自行创建新的同义词、状态或抽象层。
5. 代码完成后同步更新受影响的当前文档，并删除已经不适用的旧文档。

## 9. 提交前检查

文档变更完成后至少执行：

```bash
uv run --locked --group docs mkdocs build --strict --site-dir /share/wangziping/tmp/omnitrade-mkdocs-site
uv run --locked pytest -m "not network"
```

同时检查：

- MkDocs 导航没有 orphan page 或死链接。
- 示例中的命令、配置键和源码路径存在。
- 每份正式文档的元数据完整且日期正确。
- 没有遗留旧名称、旧路径或相互冲突的数字统计。
- 文档变更没有把 secrets、真实凭据或生成产物写入仓库。
