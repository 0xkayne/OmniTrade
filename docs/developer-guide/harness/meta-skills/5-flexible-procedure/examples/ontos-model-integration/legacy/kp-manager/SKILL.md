---
name: kp-manager
description: Manage the full lifecycle of integration keypoints — capture, validate, integrate into model-integrator, query, and export. Use whenever the user discovers an error-prone area, encounters a tricky constraint, or says "记录关键点", "keypoint", "gotcha", "约束条件", "整合 KP", "KP protocol". Also trigger for reviewing, querying, summarizing, or bulk-integrating captured keypoints. This skill is the bridge between exploration (discovering constraints) and automation (feeding constraints into model-integrator skill).
---

# KP Manager — 关键点全生命周期管理

管理集成关键点从发现到自动化消费的完整生命周期：捕获、验证、整合、查询、导出。

KP 约束库是 model-integrator skill 的知识基础设施。每次捕获的约束都会自动索引到对应分析维度，在未来的模型集成中被命中。

## 核心原则

**KP 是单一知识源（Single Source of Truth）。**

- KP 文件 (`docs/integration_keypoints/KP-XXXX-*.md`) 包含完整的约束知识
- model-integrator skill 通过 **KP ID 引用** 约束，**不复制** 约束内容
- 当 KP 内容变更时，skill 自动保持正确（因为 skill 不包含 KP 的具体内容）

```
错误方式:
  KP 文件 ──复制内容──→ model-integrator skill
  问题: 两处内容，维护负担，容易不一致

正确方式:
  KP 文件 (单一知识源)
      ↓ skill 在运行时读取
  model-integrator skill (流程引擎，仅包含维度映射和 KP ID 索引)
```

## 何时触发

### 自动检测信号

对话中出现以下模式时，主动建议记录关键点：

**显式信号：**
- "这个地方容易出错" / "这里有个坑" / "注意这个"
- "之前踩过这个坑" / "上次就忘了这个"
- "这个约束很重要" / "必须满足这个条件"
- "如果不 X 就会 Y"（因果描述，尤其 Y 是错误/失败时）
- "记录一下" / "记个笔记"
- "整合 KP" / "KP protocol"

**隐式信号（建议记录，不自动创建）：**
- 代码修改后多次调试才通过（说明该点有隐式约束）
- 发现某处依赖链比预期复杂
- 遇到 config 系统 / model registry / attention backend 的特殊行为
- 某个 enum/dispatch 机制有隐藏的依赖关系
- 发现某个"约定"不在文档中，只存在于代码逻辑中
- 调试过程中发现根因不在最初怀疑的地方

## KP Schema 规范

每个 KP 文件必须包含以下结构化字段，确保可以被 model-integrator 动态消费。

```markdown
# KP-XXXX: <标题>

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-XXXX |
| 日期 | YYYY-MM-DD |
| 维度 | <维度名>                    ← 决定 KP 映射到 model-integrator 的哪个分析维度
| 严重度 | P0 / P1 / P2 / P3         ← 决定约束的强制性
| 状态 | constraint_defined / ...   ← 见下方生命周期
| 触发条件 | <布尔表达式>              ← model-integrator 用此判断是否命中
| 泛化标签 | <标签名>                ← 跨模型通用分类 (见下方说明)
| 发现者 | <来源>                     ← 谁发现的、从哪来

## 问题描述

**现象**: ...
**根因**: ...
**表现**: ...

## 约束定义

**必须满足的条件**: ...
**违反后果**: ...
**评估方法**: ...                            ← 必须是可执行的验证命令

## 代码位置

- 文件路径: `ontos/xxx/yyy.py`
- 关键函数/类: `ClassName.method_name`
- 行号范围: Lxxx-Lyyy（如适用）
- 相关 enum/config: `SomeEnum.SOME_VALUE`

## 标准解决方案

**正确做法**: ...
**常见错误做法**: ...                         ← 至少列出 1 条
**自动化建议**: 这个约束如何在未来 skill/subagent 中自动检查

## 相关关键点

- KP-XXXX: 关联关系说明

## 发现过程

简要描述如何发现这个关键点的（对话上下文），帮助未来理解。
```

### 字段说明

#### `维度` 字段 — KP 的归属维度

决定 KP 映射到 model-integrator 的哪个分析维度（同时也决定了 KP 索引到 skill 的哪个索引表）：

| 维度值 | 对应 model-integrator 维度 |
|--------|---------------------------|
| `identity` | 维度 1: 模型身份注册 |
| `attention` | 维度 2: 注意力架构 |
| `ffn` | 维度 3: FFN / MoE 架构 |
| `kv_cache` | 维度 4: KV Cache 管理 |
| `quantization` | 维度 5: 量化方案 |
| `residual` | 维度 6: 残差连接与特殊功能 |
| `decode_accel` | 维度 7: Decode 阶段加速策略 |
| `profiling` | 维度 8: Profiling 支持 |

KP 可以索引到多个维度。例如 KP-0033 同时索引到维度 1 (identity) 和维度 2 (attention)。

#### `触发条件` 字段 — 特征向量布尔表达式

```
格式: <特征名> <比较运算符> <值> [AND|OR <特征名> <比较运算符> <值>]

示例:
  compress_ratios IS NOT NONE
  num_kv_heads == 1 AND kv_lora_rank IS NONE
  head_dim != embedding_dim // num_q_heads
  n_routed_experts > 0
  scoring_func 与父类不同
```

#### `泛化标签` 字段 — 跨模型通用分类

```
用途:
  1. model-integrator 的索引表中，用泛化标签替代冗余的重复描述
  2. 新模型集成时，先检查泛化标签是否有已知 KP 可命中
  3. 避免不同模型创建语义重复的 KP

命名规范: <架构特征>_<关注点>
  示例:
    gqa_head_divisibility     — GQA 头数整除约束
    mla_detection             — MLA 检测准确性
    sparse_backend_selection  — sparse backend 子类区分
    moe_routing_inheritance   — MoE 路由配置继承
    kernel_block_size         — kernel block size 约束
```

#### `评估方法` 字段 — 必须可执行

```
好的示例:
  "验证 is_mla_model() 返回 False"
  "验证 get_head_size() == model_config.head_dim"
差的示例:
  "确保实现正确" — 太模糊，不可执行
```

#### `状态` 字段 — KP 生命周期

```
seed               → 从代码约定推断，未经实际踩坑验证
exploratory        → 未知创新架构探索中创建的临时 KP
discovered         → 实际踩坑发现，但约束定义还不完整
constraint_defined → 约束定义完整，可被 model-integrator 消费 (默认状态)
automated          → 约束已自动化为测试或 CI 检查
verified           → 经多个模型集成验证，约束准确
deprecated         → 约束已过时，被新 KP 替代
```

#### `发现者` 字段 — 来源可靠性

```
  - "集成 <model-name> 时发现"    — 实际踩坑 (最高可靠性)
  - "subagent 集成任务发现"        — 自动化集成中捕获
  - "种子 KP (代码约定提取)"       — 从框架代码逻辑中推断
  - "provisional KP (探索阶段)"    — 未知创新架构探索中创建 (待验证)
```

## 阶段分类 (辅助标签)

KP 按发现时的集成阶段分类，辅助检索。注意：**维度 (dimension)** 决定索引位置，**阶段 (phase)** 只是组织标签。

| 阶段 | 说明 | 典型关键点 |
|------|------|------------|
| **registration** | 模型注册与身份 | model config 命名、registry 发现机制、enum 值匹配 |
| **config** | 配置系统 | flat_dataclass 展开/重建、嵌套 config 依赖、CLI 参数映射 |
| **attention** | 注意力算子 | backend 选择逻辑、custom kernel 参数、per-layer dispatch |
| **kv_cache** | KV Cache 管理 | block 分配策略、压缩比计算、per-layer sizing |
| **execution** | 执行时间预测 | 预测模型选择、profiling CSV 解析、op 组合 |
| **profiling** | GPU Profiling | kernel 计时、backend matrix 展开、worktree 隔离 |
| **scheduler** | 调度器集成 | batch 组装、request queue、prefill/decode 分离 |
| **validation** | 测试验证 | 回归测试、model 兼容性、全 workflow 校验 |

## 工作流

### 1. 捕获 + 验证 + 整合 (核心流程)

当检测到关键点信号时，一次性完成捕获和整合：

**Step 1: 确认** — 向用户确认："发现一个关键点，需要记录吗？简要描述：..."

**Step 2: 去重检查** — 创建前检查同维度已有 KP：
```
1. 扫描同维度的所有已有 KP
2. 检查泛化标签是否已有匹配
3. 检查触发条件是否与已有 KP 重叠
4. 如果重叠:
   - 已有 KP 的触发条件完全覆盖 → 不创建新 KP，复用已有
   - 部分重叠 → 创建新 KP，共享泛化标签，在"相关关键点"中交叉引用
   - 触发条件相同但约束不同 → 合并为一个 KP (OR 触发条件 + 分支约束)
```

**Step 3: 提取** — 从对话上下文中提取问题、根因、代码位置

**Step 4: 补充** — 对信息不全的字段，向用户提问补充（不要编造）

**Step 5: 编号** — 扫描 `docs/integration_keypoints/` 目录，取最大编号 +1

**Step 6: 写入** — 生成结构化文件 `docs/integration_keypoints/KP-NNNN-<slug>.md`

**Step 7: 更新索引** — 在 `docs/integration_keypoints/README.md` 追加索引条目

**Step 8: 整合到 model-integrator** — 自动执行：

```
a. Schema 验证:
   ✓ 维度字段使用 8 个规范维度名之一
   ✓ 触发条件使用特征向量布尔表达式
   ✓ 评估方法是可执行的验证命令
   ✓ 常见错误做法至少列出 1 条
   ✓ 泛化标签已填写

b. 索引更新:
   在 model-integrator skill (claude-code-skills/model-integrator/SKILL.md) 的
   对应维度 KP 索引表中追加一行:
   | KP-XXXX | 严重度 | 泛化标签 | 触发条件 | 核心要点 |

c. 结构性变更判断:
   新 KP 是否揭示了全新的架构类型?
     YES → 在该维度的决策树中增加新分支
     NO  → 不需要改决策树

   新 KP 是否引入了全新的分析维度?
     YES → 在 model-integrator 中增加新维度 + 索引表
     NO  → 不需要

   新 KP 是否影响维度间依赖?
     YES → 更新依赖 DAG
     NO  → 不需要

d. 覆盖度表更新:
   更新 model-integrator 的 "KP 库覆盖度" 表中对应维度的 KP 列表
```

**不应做的事：**
```
✗ 把 KP 的触发条件、要求、评估方法、常见错误复制到 skill 中
✗ 为每个 KP 在 skill 中写独立的约束匹配块
✗ 让 skill 的某个维度只适用于特定模型
```

### 2. 批量整合

当需要一次整合多个 KP 时（如"整合 KP-0033~KP-0039"）：

1. 读取所有待整合 KP 的文件
2. 对每个 KP 评估精华/糟粕：
   - **精华**: 通用性高，代表架构约束模式，未来模型集成可能再次命中
   - **糟粕**: 一次性 bug，已修复不影响未来，不纳入索引
3. 对精华 KP 执行 Step 8 的整合流程
4. 向用户报告：哪些纳入、哪些排除、理由

### 3. 查询已有重点

当用户说"查一下关于 X 的关键点"时：
1. 扫描 `docs/integration_keypoints/` 目录
2. 按关键词或维度过滤
3. 列出匹配的关键点摘要
4. 可深入展示某个关键点的完整内容

### 4. 生成总结

当用户说"总结关键点" / "汇总一下"时：
1. 读取所有关键点文件
2. 按维度分组汇总
3. 统计各严重度分布
4. 生成 `docs/integration_keypoints/SUMMARY.md`

### 5. 导出约束

当用户说"导出约束" / "生成约束文件"时：
1. 提取所有状态为 `constraint_defined` 或更高的关键点
2. 生成 JSON 格式的约束清单，可直接被未来的 skill/subagent 消费
3. 输出到 `docs/integration_keypoints/constraints.json`

约束 JSON 格式：
```json
{
  "constraints": [
    {
      "id": "KP-0001",
      "dimension": "attention",
      "severity": "P1",
      "title": "约束标题",
      "must_satisfy": "必须满足的条件描述",
      "check_method": "如何验证",
      "trigger_condition": "触发条件表达式",
      "generalization_tag": "泛化标签",
      "files": ["ontos/config/model_config.py"],
      "auto_check_suggestion": "建议的自动检查方式"
    }
  ],
  "generated_at": "2026-05-18T...",
  "total": 42,
  "by_dimension": {"attention": 10, "ffn": 5, ...},
  "by_severity": {"P0": 2, "P1": 10, "P2": 20, "P3": 10}
}
```

## model-integrator 的内容边界

明确 model-integrator 应该包含什么、不应该包含什么：

### 应该包含（引擎逻辑）

| 内容 | 原因 |
|------|------|
| 8 个维度的定义和探测问题 | 这是分析流程的结构 |
| 决策树（如何分类和选择路径） | 这是分析引擎的逻辑 |
| KP 索引表（ID + 泛化标签 + 触发条件） | 这是 KP 到维度的映射 |
| 维度间依赖 DAG | 这是执行顺序的编排 |
| 输出模板 | 这是输出格式 |

### 不应该包含（知识内容）

| 内容 | 应该在哪 |
|------|----------|
| KP 的触发条件详细描述 | KP 文件的 `触发条件` 字段 |
| KP 的"必须满足的条件" | KP 文件的 `约束定义` 章节 |
| KP 的"评估方法" | KP 文件的 `约束定义` 章节 |
| KP 的"常见错误做法" | KP 文件的 `标准解决方案` 章节 |
| 特定模型的实现细节 | model-specific phase skills |

### 边界判断规则

> 如果这段文字在 KP 文件中已经存在 → 不要在 skill 中重复
> 如果这段文字描述的是"如何做分析"而非"分析发现了什么" → 属于 skill
> 如果这段文字只在某个特定模型上下文中有意义 → 属于 model-specific skill

## 协作闭环

```
model-integrator (计划生成器)
  输入: 新模型文档 + HF 链接 [+ --base-model 指定同系列模型]
  处理: Step -1 相似度 → Step 0~0.X → 8 维度分析 → KP 命中 → Novel 检测
  输出: 可执行的集成计划
    ↓
subagent 执行集成任务
  已知维度 → 标准实现 (命中 KP 约束)
  Novel 维度 → 第一性原理分析
  工作中发现新约束 → 调用 kp-manager → 捕获 + 整合
    ↓
kp-manager (本 skill: 约束捕获 + 整合)
  输入: 约束描述 (维度 + 现象 + 触发条件 + 评估方法)
  处理: 去重 → 生成 KP 文件 → 验证 → 索引到 model-integrator
  输出: KP-XXXX-*.md + 更新后的 model-integrator 索引
    ↓ (闭环)
下一个模型集成时，model-integrator 自动命中新增的 KP
```

### 反馈闭环验证

```
验证指标:
  - 每次 model-integrator 生成的计划中，KP 命中率是否提升
  - subagent 执行后新产生的 KP 数量是否减少
  - 同类架构的第二/三个模型集成是否比第一个更快
  - Novel 维度占全部维度的比例是否减少 (说明知识覆盖提升)

闭环条件:
  新 KP → 去重 → 更新 skill → 下次同类模型命中该 KP → 避免重复犯错
```

## 目录结构

```
docs/
└── integration_keypoints/
    ├── README.md              ← 索引（所有关键点的表格）
    ├── SUMMARY.md             ← 维度汇总（按需生成）
    ├── constraints.json       ← 导出的约束清单（按需生成）
    ├── KP-0001-model-config-naming.md
    ├── KP-0002-flat-dataclass-reconstruction.md
    └── ...
```

### README.md 索引格式

```markdown
# 模型集成关键点索引

## 统计

| 严重度 | 数量 |
|--------|------|
| P0-致命 | N |
| P1-严重 | N |
| P2-中等 | N |
| P3-轻微 | N |
| **总计** | **N** |

## 索引

| ID | 标题 | 维度 | 严重度 | 状态 |
|----|------|------|--------|------|
| [KP-0001](KP-0001-xxx.md) | 简短标题 | attention | P1 | constraint_defined |
```

## 与其他 Skill 的协作

- **model-integrator**: KP 索引的消费方，运行时读取 KP 文件
- **experiment-journal**: 实验过程中发现的关键点可以交叉引用到实验日志
- **skill-creator**: 当关键点积累到一定程度，可以用 skill-creator 创建针对性的自动化 skill

## 语言约定

- 关键点内容用中文撰写
- 代码路径、函数名、变量名保持英文
- 维度和阶段名称使用英文（与代码模块对应）
- 严重度格式: `P0` / `P1` / `P2` / `P3`
