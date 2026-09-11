# KP-0043: ElementWiseOp CSV 列缺失时静默返回 0 时间

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0043 |
| 日期 | 2026-05-18 |
| 阶段 | profiling |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 泛化标签 | csv_column_silence |
| 触发条件 | `execution plan 中新增 ElementWiseOp 但 CSV 无对应列` |
| 发现者 | DeepSeek V4 profiling 数据流分析 |

## 问题描述

**现象**: V4 的 execution plan 包含多个 `ElementWiseOp`（hadamard, sinkhorn, rsqrt_norm, sim_quant 等），但 attention profiling CSV 中没有对应的 `time_stats.indexer_q_hadamard.median` 等列。这些 op 的执行时间被静默设为 0。

**根因**: `_base.py:140-142` 中 `NonAttention.load_df` 对缺失列的处理：

```python
column = f"time_stats.{self.op_name}.median"
if column not in df_with_derived_features.columns:
    df_with_derived_features[column] = 0  # 静默填 0
```

**这是设计决策而非 bug**: 这些 ElementWiseOp 的时间已隐含在 attention profiling 的端到端测量中（见 KP-0020 融合 kernel），单独测量会导致双重计费。

## 约束定义

**必须了解的行为**: 当 execution plan 中引用的 op_name 在 CSV 中找不到对应列时，framework 静默返回 0 时间，不报错、不警告。

**适用场景**:
- **设计如此**: 融合 kernel 内的子 op（hadamard, sinkhorn 等）不需要单独 profiling
- **潜在风险**: 如果一个本应显式 profiling 的 op 意外使用了 `ElementWiseOp`，其 0 时间会被当作正常值，导致仿真严重低估延迟

**检查方法**: 集成新模型后，检查 `get_execution_time` 返回值中是否有非预期的大量 0 值。对比 attention 层总延迟与 vLLM 实测值的偏差。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/ops/_base.py:140-142` — `NonAttention.load_df` 缺失列填 0
- 文件路径: `ontos/execution_time_predictor/models/v4_attention.py:103-104` — V4 的 `indexer_q_hadamard` 等 ElementWiseOp
- 文件路径: `ontos/profiling/attention/attention_wrapper.py:248` — attention profiling 只测端到端时间

## 标准解决方案

**正确做法**: 理解 ElementWiseOp 赋 0 的设计意图。对于融合 kernel 内的子 op，这是正确的。对于需要独立 profiling 的 op，应使用 `Attention` 或 `Linear` 等有独立 CSV 列的 op 类型。

**调试建议**: 如需验证哪些 op 实际为 0 时间，在 `NonAttention.load_df` 中临时添加 `logger.warning` 输出缺失列名。

## 相关关键点

- KP-0023: 三类 profiling 充分性（C4/C128 共享 CSV 的分析）
- KP-0020: 异构融合 kernel（端到端测量、不拆分子 op 的原则）

## 发现过程

V4 集成时检查 hadamard/sinkhorn 等 op 的时间数据来源。追踪数据流发现 CSV 中无对应列，但 framework 静默填 0。确认这是设计决策：这些 op 的时间已含在 sparse attention kernel 的端到端 profiling 中。
