# KP-0016: MoE Expert 参数量需独立计算并与官方数据交叉验证

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0016 |
| 日期 | 2026-05-14 |
| 维度 | ffn |
| 严重度 | P1 |
| 状态 | seed |
| 触发条件 | `n_routed_experts > 0` |
| 泛化标签 | moe_expert_params |
| 发现者 | 种子 KP (代码约定提取) |

## 问题描述

**现象**: MoE 模型的 FFN 层参数量计算方式与 Dense FFN 根本不同，不能简单复用 Dense 公式。
**根因**: MoE 有 `n_routed_experts` 个独立 expert + 可选 `n_shared_experts`，每个 expert 的结构与 Dense FFN 相同 (gate_proj + up_proj + down_proj)。参数量 = expert 数量 × 单个 expert 参数量。
**表现**: 如果按 Dense FFN 计算参数量，会严重低估 MoE 层的参数量，导致 memory planner 分配不足、weight_size 错误。

## 约束定义

**必须满足的条件**:
```
单个 expert 参数量 = 3 × hidden_size × intermediate_size  (SwiGLU: gate + up + down)
总 FFN 参数量 = n_routed_experts × single_expert + n_shared_experts × single_expert (如有)
```
对于 expert-only 量化模型，expert 和非 expert 的 dtype 可能不同，需分别计算 bytes。

**违反后果**: 参数量计算错误 → weight_size 错误 → memory planner 分配错误 → 模拟结果失真。

**评估方法**:
```python
# 计算 MoE FFN 参数量
single_expert = 3 * hidden_size * intermediate_size
total_ffn = n_routed_experts * single_expert
if n_shared_experts > 0:
    total_ffn += n_shared_experts * single_expert

# 与官方数据对比 (仅 FFN 部分)
# 例: DeepSeek-V2, 160 experts, intermediate=1536, hidden=5120
# single = 3 * 5120 * 1536 = 23,592,960
# total = 160 * 23,592,960 = 3,774,873,600 (仅 routed)
```

## 代码位置

- 文件路径: `ontos/execution_time_predictor/models/moe.py`
- 关键类: `MoEFFN`
- 相关方法: `_get_param_size_impl()`

## 标准解决方案

**正确做法**:
1. 在 `MoEFFN._get_param_size_impl()` 中，计算 `n_routed_experts × single_expert + n_shared_experts × single_expert`
2. 如果有 expert-only 量化，expert 和 shared expert 按 `expert_dtype` 计算 bytes
3. Router (gate) 的参数量也要计入: `hidden_size × n_routed_experts`

**常见错误做法**:
- 忘记计算 router/gate 的参数量
- 把 shared expert 的参数量与 routed expert 混在一起按同一 dtype 计算
- 忽略 fine-grained experts 的单 expert 参数量可能更小 (如 DBRX: 64 个小 expert)

## 相关关键点

- KP-0018: MoE 模型需要 all-to-all profiling
- KP-0016 与 expert-only 量化交互: expert 按 expert_dtype，shared expert 按主 dtype

## 发现过程

种子 KP — 从框架代码逻辑中提取。MoE 是当前主流大模型的标配架构 (Mixtral, Qwen-MoE, DeepSeek, DBRX, Llama-4)，但框架中没有显式的参数量验证步骤。
