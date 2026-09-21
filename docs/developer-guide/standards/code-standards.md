---
status: current
authority: normative
owner: project maintainers
updated: 2026-09-10
applies_to: src/ 全部模块；tests/ 与 docs/ 中对凭据的处理
---

# 编码规范

**语言风格不在这里定义。** Python 怎么写以
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html) 为准——
docstring 格式（Args/Returns/Raises）、类型标注、`default_factory`、命名大小写、导入顺序
都沿用它的结论。把它落到可执行层面的是 `pyproject.toml` 里的 ruff 配置和编辑时自动运行的钩子。

本文只写**本项目在通用风格之上追加的约束**。而且不是"再补充几条规则"，是四条**原则**：
每条都能推导出它没有被逐条列出的情况。

## 为什么需要这四条

Google 风格管 Python 怎么写，管不了这个项目的契约——它不知道哪些组件允许发单、
`config/secrets.yaml` 是什么、import 阶段连交易所为什么会出事。下面四条处理这类问题。

### 1. 副作用发生在显式调用点，不在 import 期

**禁止**：模块顶层连接交易所、打开数据库、起后台任务、读环境变量或配置。
配置只在 `src/cli/` 读一次并完成归一化。

**为什么**：`import` 必须永远安全。一旦 import 有副作用，测试无法隔离，文档构建会尝试联网，
任何一次 `--help` 都可能开出一个数据库连接。

**谁检查**：`tests/test_architecture.py::test_no_io_construction_at_import_time`、
`::test_config_is_read_only_at_the_cli_boundary`。

### 2. 失败必须可诊断，不得伪装成成功

**禁止**：吞掉异常后返回看起来正常的空结果；用 `None`、空字符串、`0` 或特殊字符串表示"出错"；
异常消息缺少动作、venue、symbol 或 intent/leg 标识。

**为什么**：这是执行系统的命门。一次"看起来成功"的失败会在成交确认、对账和补偿里
放大成不可追踪的敞口。

**谁检查**：机器判不了（"消息是否足够上下文"没有客观标准），留给 code review。
判据是：**只凭这条异常，能不能定位到哪一笔、哪个 venue、哪一步。**

### 3. 凭据只存在于 `config/secrets.yaml`，且永不外泄

**禁止**：把 `secrets.yaml` 的任何值复制进代码、测试、文档、示例、issue、报错粘贴或截图。
文档和示例只引用 `secrets.example.yaml` 里的模板值。

**为什么**：`secrets.yaml` 被 gitignore；抄到别处就绕过了这层保护，而且会随仓库一起提交。

**谁检查**：`tests/test_architecture.py::test_secrets_never_appear_in_docs_or_tests`——
它已经抓到过一次真实泄露（一个真实的钱包地址被当成测试夹具写进了 `tests/`）。

### 4. 跨层契约用类型表达，不用裸容器

**禁止**：跨层传 `dict[str, Any]` 或裸 `tuple`；领域数据用 dataclass、TypedDict 或明确的返回类型。

**为什么**：裸容器把结构约定留在调用方的脑子里。用类型表达之后，改一个字段会让所有使用点
立刻失效，而不是等到运行时才炸。

**谁检查**：同样留给 review。判据是：**换一个调用方时，它需不需要读被调用方的实现才能用对。**

## 其他规则在哪

交易所适配器的附加要求：协议签名、请求构造、错误映射和原始响应转换必须封装在 `src/exchange/`；业务层不得直接调用 venue endpoint。native adapter 的私钥和签名不得进入日志、异常或 fixture，响应必须先转换为内部类型；未实现能力必须显式抛出错误。

本文不重复下列内容，它们各有正文：

| 主题 | 位置 |
|---|---|
| 目录层级、允许/禁止的跨层依赖、测试目录对应 | [代码目录结构规范](directory-structure.md) |
| 类名、函数名、模块词根、角色后缀、领域术语唯一性 | [命名规范](naming-conventions.md) |
| 副作用顺序、阻断状态等硬约束 | `CLAUDE.md` 的 Critical invariants 清单 |
| 文档的增删改流程、提交前检查 | [docs-paradigm](../../docs-paradigm.md) §5、§9 |

## 能机械化的规则都在测试里

`tests/test_architecture.py` 断言上面第 1、3 条，外加四条结构性约束：

- 只允许 `directory-structure.md` §3 登记过的跨层依赖
- 没有模块级循环导入（靠惰性导入成立的环必须显式登记）
- `src/persistence/` 不导入任何业务层
- 公开符号名全项目唯一

**新增模块时它们自动生效**——这就是把规则写进测试而不是散文的全部理由：
散文覆盖的是作者想到的情况，测试覆盖每一种情况。第 2、4 条之所以留在文档里，
正是因为它们没有客观判据，机械化的尝试只会变成对措辞的检查。
