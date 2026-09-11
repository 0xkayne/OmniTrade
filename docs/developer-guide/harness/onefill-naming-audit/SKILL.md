---
name: onefill-naming-audit
description: |
  核对 oneFill（OmniTrade 仓库）的命名规范登记表是否与源码仍然一致时使用。每次功能开发完成之后、
  提交之前跑一次——尤其是新增了公开类、类型别名、模块级函数、角色后缀或策略之后。
  它逐条检查命名规范的登记表（全局唯一性、模块词根、保留后缀、无宾语函数、易混名、有意例外），
  产出偏差条目草案。用户说「名字还符合规范吗」「核对命名」「登记表要更新吗」时也用它。
  Use after adding any public symbol in this repo, to check naming-conventions.md registers it.
metadata:
  status: current
  authority: normative
  owner: project maintainers
  updated: 2026-09-11
  applies_to: src/ 下全部公开符号，在每次功能开发完成之后
---

# 核对命名规范与源码是否一致

## 这个 skill 存在的理由

[命名规范](../../standards/naming-conventions.md) §4 已经要求「每条可机械判定的规则附一条
可执行命令」。那些命令的问题是**默认在写代码时被想起来**。

命名规范失效的方式恰恰是**没人想起来查**：新符号被就地起名，检查命令一次也没有运行。
而重名、同义词、未登记后缀这几类问题都不会报错，只会在检索和重构时漏检——
搜到一个名字，拿不到这个概念的全部实现，漏掉的部分不会以任何方式提示自己存在。

所以触发点是**事件**（一次功能开发完成），不是记性。

## 先读规范，不要背清单

核对对象与判定依据全部写在 [命名规范](../../standards/naming-conventions.md) 里。
**不要在这里复制那几张表**——它们是规范正文，复制出来的第二份会在下次改名时腐烂。

| 读什么 | 为什么 |
|---|---|
| `naming-conventions.md` §5 模块词根表 | 新增符号是否含有本模块词根 |
| `naming-conventions.md` §6 保留后缀表 | 是否出现未登记的后缀 |
| `naming-conventions.md` §4 正式术语表右列 | 是否出现禁用同义词 |
| `naming-conventions.md` §7 无宾语函数例外登记 | 是否有未登记的无宾语函数 |
| `naming-conventions.md` §8 易混名对照 | 易混名是否被互相替换 |
| `naming-conventions.md` §9 有意例外 | 登记的例外是否被新增代码侵入 |

条目 schema 与记录格式见 [漂移台账协议](../drift-ledger/PROTOCOL.md)，不在这里重复。

## 核对流程

### 第一步：圈出本次新增或改名的公开符号

```bash
# 改了哪些文件
git diff --name-only

# 本次新增的公开类/函数/类型别名
git diff -U0 -- 'src/**/*.py' | grep -E '^\+(class |def |[A-Z_]+ = )'
```

### 第二步：逐条核对

| 核对对象 | 判定依据 |
|---|---|
| 全局唯一性 | `naming-conventions.md` §3 的命令；或直接靠 `test_public_symbol_names_are_unique` 兜底 |
| 术语唯一性 | 是否出现正式术语表右列的禁用同义词 |
| 模块词根 | 新增公开符号是否含有本模块词根，或已登记为例外 |
| 保留后缀 | 是否出现未登记的后缀 |
| 无宾语函数 | 是否有未登记的无宾语函数 |
| 易混名 | 易混名对照表中的名字是否被互相替换 |
| 例外不扩散 | §9 登记的例外类别是否被新增代码侵入 |
| **登记表自身** | §5/§6/§7 中列出的符号**是否仍存在于源码中** |

最后一行最容易漏，也最容易出问题：**登记表是单向增长的**。符号被删除或改名后，
表里那一行就变成了一条不成立的断言——它描述的东西已经不存在了，而表看起来仍然完整。

全局唯一性那条有机器兜底（`tests/test_architecture.py::test_public_symbol_names_are_unique`），
跑 `./scripts/verify.sh arch` 即可，不必手查。

### 第三步：写下应波及的断言，再核对

**先写下来，再核对。** 不写的话核对会退化成把规范重读一遍——而重读对已经失效的断言
没有任何作用，因为**失效的断言读起来和有效的一样**。

### 第四步：偏差只产出条目草案

条目 schema、三类回流判定、记录格式全部见 [漂移台账协议](../drift-ledger/PROTOCOL.md)。
**不要就地改规范**——就地修改会让漂移本身失去记录价值。

## 什么不在这个 skill 的范围里

- **行数、测试数等文档数字**，以及 `docs/` 与代码的总体一致性 —— `onefill-docs-audit`
- **目录结构与依赖边**是否仍符合规范 —— `onefill-dirstruct-audit`
- **架构图与设计文档的双向对账** —— `onefill-arch-audit`

分工重叠会让两处都以为对方在管。只核对本文件列出的这几类。

## 伴生件的元数据

本文件是 skill，不是 `docs-paradigm.md` §4 意义上的正式文档：frontmatter 的字段集合是封闭的，
五个文档字段挂在 `metadata:` 下。改动后用校验脚本验证：

```bash
uv run --locked python scripts/validate_skills.py
```
