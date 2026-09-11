---
id: DRIFT-0001
date: 2026-09-11
category: spec-conflict
severity: medium
status: 已自动化
trigger: 当一份规范正文（docs-paradigm.md）与它所依据的范式（harness/meta-skills/）互相矛盾时
tags: [规范冲突, 图表, 生成物]
found_by: 核对型 / onefill-arch-audit（人工触发）
applies_to: docs/docs-paradigm.md §2 与 harness/meta-skills/1-architecture-analysis/architecture-analysis.md §6
revalidate: ./scripts/verify.sh diagrams
last_verified: 2026-09-11
---

## 问题描述

**现象。** `docs/docs-paradigm.md` §2 写着「架构图等图示**一律**用 Mermaid 内联在正文中，
不导出为图片」，而 `harness/meta-skills/1-architecture-analysis` §6 写的是「**图**是静态资源，
存于 `docs/assets/`，由文档正文相对引用」。两条规则方向相反。

**根因。** 不是笔误，是两条各自成立的理由撞在一起：

- 范式 §6 要求图与 workflow 文档**成对**存在，且 §4 要求「图由代码推导」——它假设图是
  一份可以独立于正文演进、可被逐节点与源码对账的产物。
- `docs-paradigm.md` 的规则来自 `3eae116`（2026-09-10），那个 commit 删掉了
  `docs/assets/architecture.{svg,png}`，理由写在 commit message 里：它们 **had no generator**，
  无法重新产出，所以在一次目录重构内就过期了。规则是对**那个具体失败**的反应，不是对图的否定。

两条都不是错的，但字面执行只有一种可能。而 `docs-paradigm.md` §开头写着
「当其他文档与本文档冲突时，以本文档为准」—— 于是冲突被静默地按 Mermaid 一侧解决，
没有留下任何记录。

**表现。** 后果是 `docs/assets/` 目录始终不存在，`design/` 下的图全部内联，
范式 §2 要求的「图 + 文档成对」在形式上成立、在形态上不成立：图无法被单独对账，
也无法作为一个产物被引用。

## 约束定义

**必须满足的条件：** 图同时满足「可 diff」与「可重新产出」两条，缺一不可。

- 违反「可 diff」：图变成二进制资产，正文的改图意图无法在 review 中看见。
- 违反「可重新产出」：图成为只读的证据，重构一次即过期，且**没有任何检查会发现**
  ——这正是 `3eae116` 的失败，也是 README「共享的验证共识」要防的那一类。

**评估方法（可执行）：**

```bash
./scripts/verify.sh diagrams          # .svg 是否与 .dot 一致
ls docs/assets/*.dot docs/assets/*.svg | wc -l   # 两者必须成对，数量应为 2N
```

## 代码位置

- `docs/assets/*.dot`（源码，15 份）与同名 `.svg`（生成物）
- `scripts/render_diagrams.sh` —— 重新生成入口，`--check` 为比对模式
- `scripts/verify.sh` 的 `diagrams` 阶段
- `docs/docs-paradigm.md` §2 —— 已改写为「架构图是生成物，不是手绘图片」

## 标准解决方案

**正确做法。** `.dot` 源码入库，`.svg` 由它渲染、同样入库，两者**必须同时提交**。
「可 diff」由 `.dot` 承担，「可重新产出」由 `render_diagrams.sh` 承担。
重新生成入口可执行，所以 README 那条「凡是能自动生成的产物都应有一个可执行的重新生成入口」
在图上成立。

**常见错误做法。**

- 只提交 `.svg` —— 退回「没有生成脚本的静态图片」，即 `3eae116` 删掉的那批。
- 手工编辑 `.svg` —— 下一次重新生成会静默覆盖它，而覆盖前它看起来是对的。
- 把 `.dot` 放在 `docs/` 下新开的子目录 —— `docs-paradigm.md` §2 的目录树是封闭的，
  `docs/` 下只允许 `assets/` 作为静态资源位置。

**自动化建议。** `--check` 已接进 `verify.sh`。它验证的是「`.svg` 是 `.dot` 渲染出来的那一个」，
**不验证「`.dot` 描述的还是代码里那个系统」** —— 后者只能靠 `onefill-arch-audit` 的双向对账。
两者不可互相替代。

## 关联条目

本次冲突的**结构性成因**值得单独记一条：规范正文与它所依据的范式放在两处，
而两者都可能独立演进，却没有一处检查它们是否一致。
`meta-skills/README.md` 现在声明了「范本是冻结的、产物持续更新、不一致时以产物为准」，
但那只解决范本侧，不解决同一仓库内两份**产物**级规范互相矛盾的情况。
若再次出现同类冲突，应记为新条目并引用本条。

## 发现过程

在按用户要求补架构图时发现。我先按 `docs-paradigm.md` §2 做了 13 张内联 Mermaid 图并复述了
那条理由，用户指出要的是 `examples/` 下那种 SVG、应放在 `docs/assets/`。
复查时用 `git log -S'一律用 Mermaid 内联'` 定位到规则来源 `3eae116`，
确认其理由是「没有生成脚本」——而本机 graphviz 2.43.0 与生成 `examples/` 那两张图
**是同一版本**，前提已不成立。冲突因此可以同时满足两边，而不是二选一。
