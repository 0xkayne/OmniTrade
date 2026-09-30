---
name: onefill-plan
description: |
  为 Omnitrade 项目设计改动方案时使用。凡是要新增或修改 src/ 下的模块、新增交易所、
  新增策略、改动协调流程或数据库表结构，或用户说「这个功能该怎么加」「新模块放哪里」
  「这样改会不会影响别的模块」「先设计一下」，都先用本 skill 再动手。
  它规定调研顺序（按 applies_to 找出覆盖本次改动的设计文档 → 定位层级 → 确认依赖边 → 定名），
  以及一份合格的 plan 必须回答的五个问题。
  Use before planning any change under src/ in this repo.
metadata:
  status: current
  authority: normative
  owner: project maintainers
  updated: 2026-09-30
  applies_to: AI 参与的 src/ 改动的设计阶段
---

# 设计 Omnitrade 的改动方案

## 为什么这件事有固定流程

Omnitrade 的架构约束是**机器断言**的：`tests/test_architecture.py` 检查每一对跨包 import，
`test_public_symbol_names_are_unique` 检查每一个公开符号名。方案里写下一个不存在的依赖边、
或者一个和别处重名的类，代码写完 `pytest` 当场失败。

所以本 skill 的目标不是「写一份好看的方案」，而是**在写第一行代码之前，把会被测试拦下来的
决定先做对**。返工的成本随改动量增长，而这一步的成本是固定的。

## 调研顺序

四个步骤，每一步都指向规范的正文，不在这里复制结论——复制出来的第二份会腐烂。

### 1. 这次改动落在哪一层

读 `docs/developer-guide/standards/directory-structure.md` 的**§3 分层模型**（依赖边表）
和 **§5 各目录收录规则**。

§5 的重点是每条「**不收**」：`market/` 不收 venue 适配细节、`persistence/` 不收任何业务层类型、
`coordinator/` 不收策略、`strategy/` 的功能域之间不得互相导入。新模块放错层，
通常不是因为它看起来该在那儿，而是因为它偷偷依赖了不该依赖的东西。

### 2. 只读**覆盖到本次改动**的设计文档

`docs/developer-guide/design/` 下的每份文档，frontmatter 里都有 `applies_to`，
写着它覆盖的源码路径。按它筛，不要全读：

```bash
# 举例：这次要改 coordinator，看哪些设计文档声明覆盖它
grep -l 'src/coordinator' docs/developer-guide/design/*.md
```

文件名前缀就是层级（`sys-` / `base-` / `strat-` / `entry-`），
`docs/developer-guide/index.md` 的「按任务阅读」表给了常见任务的阅读顺序。

**读设计，不只是读接口。** 设计文档记录的是「为什么这样分层」「为什么这个状态不允许自动恢复」；
这些理由决定你的方案能不能成立。`sys-architecture.md` 的关键不变量和 `CLAUDE.md` 的
Critical invariants 是硬约束——例如 `NEEDS_MANUAL` 之后不得加自动恢复路径，
那是设计意图而不是待修的缺陷。方案与之冲突时，改方案，不改那条约束。

### 3. 涉及落盘时读持久化层

新表、新列、新审计事件都归 `base-persistence-layer.md` 管。
关键是那条「只存不译」：持久化层只读写列，不认识任何业务层类型。新增的行结构用 `*Row`，
「行 ↔ 领域对象」的转换函数写在**拥有该领域类型的层**里，不写在 `persistence/` 里。

### 4. 触及执行路径时读协调流程

改动 `coordinator/` 的任何阶段，读 `base-coordination-pipeline.md` 和 `base-state-machine.md`。
还应当确认 `CLAUDE.md` 的 Critical invariants 里「每条 `create_order` 之前已有持久化 leg 行」
这条顺序没有被你的方案破坏——这是崩溃后能否恢复的前提。

## 一份合格的 plan 必须回答五个问题

缺任何一条，都说明还没调研完。把这五条写进方案正文：

1. **每处新增或改动的模块落在哪个包**，以及为什么不能放在别处。
2. **是否引入新的跨包依赖边。** 引入了就显式写出，并说明它是否已登记在
   `directory-structure.md` §3 的封闭表里；不在表里意味着要先改规范再动代码。
   注意：类型标注专用的 `if TYPE_CHECKING:` 导入**同样算依赖**。
3. **新增的公开类/函数叫什么名字**，词根取自哪个模块（`naming-conventions.md` §5），
   后缀是否已在 §6 的保留后缀表里。这两张表是封闭的，新后缀要先登记。
4. **用什么测试证明它成立。** 无副作用的阶段（Planner、Validator）用纯单测就够了；
   有副作用的（Executor、Reconciler）需要 `MockExchange` + 内存 SQLite。
   具体判据见 `directory-structure.md` §8。
5. **哪些文档会因此过期。** 这一条在实现完成之后要真的去执行，见 `onefill-docs-sync`。

## 不要做的

- 不要为了让方案好懂而发明新状态、新层级或新术语。`naming-conventions.md` §4 列了正式术语
  及其被禁用的同义词；源码里没有的概念，不要先在方案里造一个。
- 不要以「以后可能会用到」为由预留抽象层。
- 架构图是**生成物**：改 `docs/assets/<模块名>.dot`，跑 `scripts/render_diagrams.sh <模块名>`
  重新生成 `.svg`，两个文件一起提交。不要手工编辑 `.svg`，也不要只提交其中一个
  （`docs-paradigm.md` §2 说明了原因：没有生成脚本的静态图片重构一次就过期）。

## 方案交付之后

明确告诉用户下一步走 `onefill-implement`，并点名它开工前要读的三份规范。
两个 skill 之间没有自动触发——不点名，下一步就靠对方自己想起来。
