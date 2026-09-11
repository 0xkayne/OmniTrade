---
name: onefill-skill-evolve
description: |
  为 oneFill（OmniTrade 仓库）的三个开发流程 skill（onefill-plan / onefill-implement /
  onefill-docs-sync）做自进化回流时使用。每次走完其中一个流程之后加载它，把运行中发现的
  「skill 描述与实际不符之处」回流成漂移条目。用户说「这个 skill 该更新了」
  「skill 里写的和实际不一样」「流程跑起来不顺手」时也用它。
  Use after running onefill-plan, onefill-implement or onefill-docs-sync.
metadata:
  status: current
  authority: normative
  owner: project maintainers
  updated: 2026-09-11
  applies_to: onefill-plan / onefill-implement / onefill-docs-sync 三个流程 skill 的自我优化
---

# 三个流程 skill 的自进化回流

## 这个 skill 存在的理由

`onefill-plan`、`onefill-implement`、`onefill-docs-sync` 描述的是**流程**——
「面对一次改动，先做什么、读什么、产出什么」。流程的失效方式是**静默失真**：
它写的路径、命令、章节号在代码库演进后会指错地方，但流程照跑，产出照出，
**只是依据已经不对了**。

这类失真在当次运行中不产生任何失败，因而不会被自动记录。这正是
`meta-skills/7-skill-rsi/skill_self_optimization.md` §「应用记录与回流」
要把第四段规定为**强制**的原因：**「与描述不符之处」精确标记了 skill 的过时点。**

## 触发时机

每次走完三个流程 skill 中的任意一个之后。**在流程的运行过程中就要挂捕获点**——
不要等结束再回忆，那时已经想不起哪一步是绕过去走的。

三个 skill 各自最自然的捕获位置（它们自己的收尾节）：

| 流程 skill | 挂钩位置 |
|---|---|
| `onefill-plan` | `## 方案交付之后` |
| `onefill-implement` | `## 收尾：跑门禁，不要靠回忆` |
| `onefill-docs-sync` | `## 第四步：改完跑门禁` |

## 捕获点：运行中留意这四类信号

| 信号 | 例子 |
|---|---|
| **绕过去的做法** | 「skill 说读 §3 的边表，但那次实际是直接看测试输出确定的」 |
| **指向失效** | skill 里的章节号在规范文档里已经不存在 |
| **命令跑不通** | skill 里的命令参数已变、路径已移 |
| **判定失效** | skill 让做的某个判断，实际做不了或没有意义 |

第一类最重要，也最容易被忽略：绕过 skill 的做法**在当次运行中不产生任何失败**，
但它说明 skill 在那一步已经不可操作了。

## 漂移探针（只读，先跑）

在流程开始之前，先跑一遍探针，检查三个 skill 中硬编码的事实：

```bash
# skill 引用的规范章节是否仍然存在
grep -n '^## ' docs/developer-guide/standards/directory-structure.md | head -20
grep -n '^## ' docs/developer-guide/standards/naming-conventions.md | head -20

# skill 引用的脚本与门禁是否仍在
ls -la scripts/verify.sh

# skill 引用的设计文档是否仍在
ls docs/developer-guide/design/

# 全部 SKILL.md 是否仍能通过校验（含本 skill 自己）
uv run --locked python scripts/validate_skills.py
```

探针必须**只读**，且在任何耗时操作之前。

**连续失败比单次失败更重要。** 同一类位置上反复漂移（例如章节号反复对不上），
说明当初就不该写死章节号——处置应从「打补丁」上升到**改设计**：
把「读 §3」改成「读规范里列依赖边的那一节」，或者直接引用一条命令。

## 流程结束后的强制产出

五段，第四段是重点：

```text
实际执行范围 / 复用的历史产物 / 失败与终止 /
与 skill 描述不符之处 / 本次使用的条目
```

条目 schema、状态机、三类回流判定、索引更新方式全部见
[漂移台账协议](../drift-ledger/PROTOCOL.md)——**不在这里重复**。

## 回流只产出草案，不改 skill 正文

**不要在本次运行中就地修改 skill。** 就地修改会让漂移失去记录价值，
而且执行者并不具备判断「这是 skill 过时、环境变化还是一次性问题」所需的全部信息——
他只知道这一次没按 skill 走，不知道为什么。

产出条目草案，由人判定后执行。判定为「skill 过时」时，才回到
`docs/developer-guide/harness/skills/<name>/SKILL.md` 修改正文并更新 `metadata.updated`。

## 本 skill 自身的过时

本 skill 也会过时。发现它描述的挂钩位置或探针命令与实际不符时，
**同样记录为条目，不自修改**——它是自己的伴生件，规则与它对别的要求一致。

## 伴生件的元数据

本文件是 skill，不是 `docs-paradigm.md` §4 意义上的正式文档：frontmatter 的字段集合是封闭的，
五个文档字段挂在 `metadata:` 下。改动后用校验脚本验证：

```bash
uv run --locked python scripts/validate_skills.py
```
