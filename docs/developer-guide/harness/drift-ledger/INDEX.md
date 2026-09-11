---
status: current
authority: reference
owner: project maintainers
updated: 2026-09-11
applies_to: harness/drift-ledger/ 下全部条目的索引；由各伴生 skill 在核对后更新
---

# 漂移台账索引

条目的字段、状态机与判定规则见 [PROTOCOL.md](PROTOCOL.md)。本文件只做索引。

## 统计

| 项 | 值 |
|---|---|
| 条目总数 | 0 |
| 待处理 | 0 |
| 已自动化 | 0 |
| 已废弃 | 0 |
| 最近一次核对 | — |

## 条目表

| 标识 | 日期 | 分类 | 严重度 | 状态 | 一句话 |
|---|---|---|---|---|---|
| （空） | | | | | |

台账为空是**正常的起点**，不是失败——条目随每次功能开发完成后累积。
只有当开发发生过若干次、而这里仍然为空时，才说明这套机制没有被真正使用。

## 来源统计

| 来源 skill | 条目数 |
|---|---|
| `onefill-docs-audit` | 0 |
| `onefill-naming-audit` | 0 |
| `onefill-dirstruct-audit` | 0 |
| `onefill-arch-audit` | 0 |
| `onefill-skill-evolve` | 0 |

## 泛化标签索引

（空）

标签用于把碎片串成链——同一标签下的条目往往是同一个结构性问题的不同表现。

## 依赖关系

（空）

**这是最有价值的一节。** 单条约束是碎片，依赖链才是知识。建立若干条目之后主动整理一次，
把碎片串成链；同一类位置上反复漂移时，处置应从「打补丁」上升到「改设计」。
