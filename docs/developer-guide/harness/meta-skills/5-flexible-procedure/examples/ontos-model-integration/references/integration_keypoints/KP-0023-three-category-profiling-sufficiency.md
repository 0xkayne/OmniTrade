# KP-0023: 三分类 profiling 架构足以支撑 V4，但 ExecutionPlan 需要扩展

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0023 |
| 日期 | 2026-05-14 |
| 维度 | profiling |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 触发条件 | 任何有新型融合 kernel 或跨 op 并行的新模型 |
| 泛化标签 | profiling_architecture |
| 发现者 | 通过验证 DeepSeek V4 所有 ops 是否可归入现有三分类发现 |

## 问题描述

**现象**: V4 的所有 ops 都可以归入现有的三类：token-level（Q/KV/O 投影、Compressor、Indexer、HC、FFN）、sequence-level（sparse_attn）、communication（all2all）。不需要新增 profiling 类别。但 simulator 层的 ExecutionPlan 需要两个扩展：并行 op 组和条件触发。

**根因**: 当前框架的设计哲学是 profiling 层保持简单（三分类、参数空间可控），融合和并行逻辑抽象到 simulator 层。V4 验证了这个哲学的正确性：需要扩展的是 simulator 层而非 profiling 层。

**表现**: 不遵循此约束会导致在 profiling 层过度设计（为每种融合模式创建新的 profiling 类别），参数空间爆炸。

## 约束定义

**必须满足的条件**: 新模型的所有 profiling 必须归入三个标准类别：token-level（mlp/）、sequence-level（attention/）、communication（collectives/）。融合 kernel 整体作为单个 op profile，不拆分。跨 op 并行在 simulator 层处理，不在 profiling 层处理。

具体归分类别：
- Q/KV/O 投影、Compressor、Indexer、HC 操作、FFN 专家 → token-level (mlp/)
- sparse_attn（含 SWA + compressed + sink）→ sequence-level (attention/)
- all2all 通信 → communication (collectives/)

**违反后果**: 在 profiling 层为融合或并行创建新类别会导致参数空间爆炸（组合数远超单独维度），且 profiling 数据不反映实际执行。

**评估方法**: 验证新模型的 profiling 配置只使用三个标准文件夹（attention/、mlp/、collectives/），没有新增 profiling 类别。融合和并行逻辑全部在 simulator 层处理。

## 代码位置

- 文件路径: `ontos/profiling/attn_backend/` (attention sequence-level profiling)
- 文件路径: `ontos/profiling/mlp/` (MLP/MoE token-level profiling)
- 文件路径: `ontos/profiling/collectives/` (communication profiling)

## 标准解决方案

**正确做法**:
1. 新模型的所有 ops 归入三类：token-level (mlp/)、sequence-level (attention/)、communication (collectives/)
2. 融合 kernel 整体作为单个 op profile，不拆分
3. 跨 op 并行在 simulator 层的 ExecutionPlan 中用 ParallelOpGroup 处理
4. 条件触发用 op.trigger_frequency 属性处理

**常见错误做法**:
- 在 profiling 层创建新的 op 类别来处理融合，参数空间爆炸
- 尝试在 profiling 层测量并行执行的组合，数据不反映实际
- 为 C4 和 C128 创建不同的 profiling 类别，实际上它们只是参数不同，类别相同

**自动化建议**: 当发现新模型需要新的 profiling 子目录时，先验证是否可以归入现有三类。只有当新 op 的输入/输出模式与三类都不同时，才考虑新增类别。

## 相关关键点

- KP-0020: 异构融合 kernel 导致 op 无法独立 profile (融合 kernel 的处理方式)
- KP-0021: 多流重叠导致串行累加高估耗时 (并行 op 在 simulator 层的处理方式)

## 发现过程

通过逐个检查 V4 的所有 ops 是否能归入现有三分类发现。V4 的 sparse_attn 是 sequence-level op（参数为 kv_cache_size 维度），Compressor/Indexer 是 token-level op（参数为 batch_size 维度），all2all 是 communication op。所有 ops 都有明确的归属，验证了三分类架构的充分性。详见 `docs/v4_profiling_challenges.md`。
