---
name: onefill-docs-sync
description: |
  在 oneFill（OmniTrade 仓库）中改完代码后同步文档时使用。用户说「更新一下文档」
  「文档同步一下」「文档是不是过期了」，或者一次改动碰了 src/ 的结构、公开符号、CLI 命令、
  测试数量之后，都应当用它。它按 docs-paradigm.md §5 的生命周期流程找出哪些断言已经失效，
  并区分「规则」与「快照」两类内容——后者是文档腐烂的主要来源。
  Use after implementing any change under src/, before committing.
metadata:
  status: current
  authority: normative
  owner: project maintainers
  updated: 2026-09-11
  applies_to: docs/ 下所有页面，在 src/ 发生改动之后
---

# 改完代码后同步文档

## 先理解文档为什么会腐烂

这个仓库已经删过好几份文档，原因高度一致，值得先理解它——因为它决定了你该改什么、
以及什么内容根本不该继续存在：

> **陈述「规则」的文档能活；陈述「快照」的文档必烂。**

- **规则**：「`persistence/` 不导入任何业务层」——代码怎么演进它都成立。
- **快照**：「测试收集数为 455」、拷贝的一份目录树、拷贝的一份命令列表——
  它们描述的是**某个时刻的状态**，而状态每次改动都会变。

所以同步文档时，你要做两件事：**更新**已经失效的快照，以及**判断这个快照值不值得存在**。
后者更重要——如果一段文字可以变成一条命令、一个测试或一个链接，那么变成它们，
它就不会再腐烂了。

## 第一步：找出谁的断言被你的改动推翻了

设计文档的 frontmatter 里有 `applies_to`，写明了它覆盖的源码路径。用它反查：

```bash
# 举例：这次改了 persistence，哪些文档声称覆盖它？
grep -l 'src/persistence' docs/developer-guide/design/*.md
```

然后**打开它，逐条读它的断言**——不是扫一眼文件名就放过。重点找这几类句子：

- 「当前支持 / 默认 / 保证」之类的断言
- 表格里的行（命令表、状态表、字段表）
- 具体的数字、路径、类名

## 第二步：挨个检查已知的腐烂热点

这些地方以前烂过，每次都值得主动看一眼：

| 热点 | 在哪 | 为什么容易烂 |
|---|---|---|
| 测试数量 | `reference/current-status.md`、`README.md` | 加一个测试就过期，而且同一组数字手写在多个文件里 |
| 目录树 | `directory-structure.md` §4 | 新增/移动模块后不同步更新 |
| 符号清单 | `naming-conventions.md` §5、§6 的「现有」列 | 新增类/后缀后没有回填 |
| CLI 命令列表 | `reference/current-status.md` | 加一个 Typer 命令就过期 |
| 交叉链接与导航 | 任何被改名/删除的页面 | `mkdocs.yml` 的 nav、`developer-guide/index.md` 的表格 |

**数字不要手写。** 测试数量这类东西的正确做法是让文档指向命令而不是抄结果——
`current-status.md` 已经这么做了（给出 `pytest --collect-only -q` 让读者自己复核）。
发现别处还在手写数字时，考虑把它删掉换成命令，而不是更新成新的数字。

## 第三步：核心前提失效就重写，不要在末尾叠加

`docs-paradigm.md` §5 的流程：原文档语义还能维持时直接改并更新 `updated`；
**核心前提已经失效时，删掉旧文档、重新写一份完整的**，不要在旧文档尾部加「新版本说明」。
一份文档里同时存在两个都像正确答案的说法，比没有文档更糟。

删除或重命名页面后，必须同步四处：`mkdocs.yml` 的 nav、`developer-guide/index.md` 的表格、
所有指向它的相对链接、以及 `CLAUDE.md` 里的入口指针。

## 第四步：改完跑门禁

```bash
./scripts/verify.sh docs
```

`mkdocs build --strict` 会抓住孤儿页和死链——这恰好是改名和删除最容易破坏的两样东西。
它抓不住的是**内容层面的过期**（断言还在，只是不再成立），那部分只能靠上面三步。

## 不写进文档的东西

`docs-paradigm.md` §5 明确禁止把下列内容作为当前文档保留：

- 阶段性的 subagent 契约、已完成的重构计划
- 过时的状态快照
- 与当前代码路径不一致的接入指南
- 无法运行的配置示例

判断标准很简单：**它描述的是现在的系统，还是某次任务的经过？** 后者属于 commit message
和 `git log`，不属于 `docs/`。

## 文档自身的元数据

每份正式文档开头要有 `status` / `authority` / `owner` / `updated` / `applies_to` 五个字段
（`docs-paradigm.md` §4）。内容变了就要更新 `updated`；`applies_to` 必须写成可验证的
模块或命令范围，不能写「全部系统」。
