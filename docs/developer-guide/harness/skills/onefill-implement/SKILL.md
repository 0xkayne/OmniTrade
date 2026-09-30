---
name: onefill-implement
description: |
  在 Omnitrade 项目中写或改 src/、tests/ 下的代码时使用。开工前先读
  docs/developer-guide/standards/ 的三份规范（目录结构 / 编码 / 命名），确保新代码落在正确的层、
  用正确的词根和后缀命名、不违反四条编码原则，并在收尾时跑机器门禁。
  用户说「按这个方案实现」「新增一个模块」「加一个策略」「重构这块」「把这个 bug 修了」时也用它。
  Use before editing any file under src/ or tests/ in this repo.
metadata:
  status: current
  authority: normative
  owner: project maintainers
  updated: 2026-09-30
  applies_to: src/ 与 tests/ 下由 AI 编写的代码
---

# 在 Omnitrade 里写代码

## 开工前：读三份规范，但要知道各读哪一节

三份文档都很短，读它们是这次改动里最便宜的一步。但不要平铺着读——按你**当下要做的决定**去查：

| 你要决定的事 | 查哪里 |
|---|---|
| 这个文件放哪个包 | `directory-structure.md` §5（各目录收什么、**不收**什么） |
| 能不能 import 那个模块 | `directory-structure.md` §3（依赖边表） |
| 这个类/函数叫什么 | `naming-conventions.md` §5（模块词根）、§6（角色后缀保留词） |
| 这个名字撞不撞 | `naming-conventions.md` §3，或直接靠测试兜底 |
| 这行写法有没有原则问题 | `code-standards.md` 的四条 |

（`CLAUDE.md` 的 Critical invariants 清单始终在上下文里，不在这里重复。）

## 四条编码原则，写成「动手前会撞上的样子」

`code-standards.md` 的四条不是风格偏好，是这个项目的契约。它们在实际写代码时长这样：

**副作用发生在显式调用点。** 你正要写模块顶层的 `PersistenceStore(...)`、
`CCXTExchange(...)`、读 `config/` 或起后台任务——停。这些必须发生在函数体里，
由 `src/cli/` 的装配在调用时触发。`import` 必须永远安全。

**失败必须可诊断。** 你正要 `except Exception: return None`、返回一个空列表让调用方以为
「没有数据」、或者抛出一个不带 venue / symbol / intent_id 的异常——停。这是执行系统的命门：
一次「看起来成功」的失败会在成交确认、对账和补偿里放大成不可追踪的敞口。
判据是：只凭这条异常，能不能定位到哪一笔、哪个 venue、哪一步。

**凭据只在受保护的 secrets 文件。** 交易所凭据在 `config/secrets.testnet.yaml` /
`config/secrets.mainnet.yaml`，公共凭据在 `config/secrets.yaml`。你正要往测试夹具、
文档示例或 commit message 里粘贴一个 API key、钱包地址或私钥——停，
用对应 `config/secrets*.example.yaml` 里的模板值。
三个真实文件均被 gitignore，抄到别处就绕过了这层保护。
`tests/test_architecture.py::test_secrets_never_appear_in_docs_or_tests` 会抓，
它已经抓出过一次真实泄露。

**跨层契约用类型表达。** 你正在把一个 `dict[str, Any]` 或裸 `tuple` 传给另一层——停，
用 dataclass 或 TypedDict。判据是：换一个调用方时，它需不需要读被调用方的实现才能用对。

## 分层是硬约束，不是审美

**领域符号不得向上泄漏。** `market/` 是唯一知道 venue 原生 symbol 的层，更高层一律用
`Instrument`，CLI 用 `--base` / `--quote-preference`。`BTCUSDT` 出现在 `coordinator/` 或
`strategy/` 里就是错的。

**`market/` 不得 import `exchange/`。** 需要类型标注时，在本层声明一个 `Protocol` 描述
所需的最小接口（`quote_fetcher.py` 就是这么做的）。反向的 `exchange → market` 是合法的，
适配器的职责正是把 venue 原始数据构造成领域对象。

**`persistence/` 是叶子。** 它只读写列，不认识 `Intent`、`Instrument`、`Quote`，
也不认识状态语义。转换函数归拥有该领域类型的层。

**Coordinator 的 Planner 和 Validator 保持无副作用**，Executor 和 Reconciler 才有。
测试依赖这条分界。

## 新增模块时多做两件事

1. `directory-structure.md` §4 的目录树必须同步更新——规范正文本身就要求这一点，
   而且它是下一个人判断「这个文件为什么在这儿」的唯一依据。
2. 确认 §3 的依赖边表仍然成立。引入了新边就先改规范、说明理由，再落代码；
   这一步不能反过来。

## 收尾：跑门禁，不要靠回忆

```bash
./scripts/verify.sh lint format arch   # 写完一段就值得跑，几秒钟
./scripts/verify.sh                    # 全部五个阶段，提交前跑
```

这个脚本封装了两件容易出错的事：一是家目录磁盘配额已满，所有缓存必须重定向到
`/share_data/wangziping/`，否则 `uv` / `pytest` / `ruff` 直接 `EDQUOT` 失败；
二是 `mkdocs build --strict` 需要 `--group docs`。手工拼这两条命令每次都可能拼错。

`arch` 阶段就是 `tests/test_architecture.py`，它在每次 `pytest` 时都会跑，
所以不必等它失败才发现结构问题——写完新模块后主动跑一次，比等到最后返工便宜。

## 测试跟着副作用走

无副作用的阶段（Planner、Validator）用纯单测就够了；有副作用的（Executor、Reconciler）
需要 `MockExchange` + 内存 SQLite。`tests/` 按模块镜像 `src/`，不按测试类型分目录。
判据和当前在用的 marker 清单见 `directory-structure.md` §8——不要在这里另起一套分类。
