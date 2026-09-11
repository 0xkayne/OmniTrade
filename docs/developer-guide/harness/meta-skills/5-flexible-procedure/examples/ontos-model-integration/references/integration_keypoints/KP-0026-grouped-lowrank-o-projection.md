# KP-0026: V4 分组低秩 O 投影拆分标准 O-proj 为 wo_a einsum + wo_b GEMM 两步

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0026 |
| 日期 | 2026-05-14 |
| 维度 | attention |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 触发条件 | o_groups IS NOT NONE AND o_groups > 1 AND o_lora_rank IS NOT NONE |
| 泛化标签 | grouped_lowrank_o_proj |
| 发现者 | 通过分析 V4 model.py 中 Attention 类的 wo_a/wo_b 定义和 forward 中 einsum 计算发现 |

## 问题描述

**现象**: V4 的 O 投影不使用标准的一步大 GEMM [hidden, num_heads*head_dim] = [7168, 65536]（469M 参数），而是分为两步：(1) wo_a: 16 个分组独立的 batched einsum，每组 [4096, 1024]；(2) wo_b: 一个标准 GEMM [16384, 7168]。总参数量 ~184M，仅为标准 O 投影的 39%。

**根因**: V4 有 128 个 Q heads、head_dim=512，标准 O 投影参数量巨大（469M/层 × 60 层 ≈ 28GB）。分组低秩设计将 128 heads 分为 16 组（每组 8 heads），先在组内做低秩压缩（4096→1024），再跨组混合回 hidden_size（16384→7168）。这与 Q 路径的低秩设计（wq_a + wq_b）形成对称结构。

**表现**: 仿真器不能将 O 投影建模为单个 `RowParallelLinear(65536, 7168)` op。它由两个不同类型的操作组成：(1) `wo_a` 是 ColumnParallelLinear 的 batched einsum（无 all-reduce），(2) `wo_b` 是 RowParallelLinear（有 all-reduce）。两步的 TP sharding 策略不同。

## 约束定义

**必须满足的条件**: 当新模型使用分组低秩 O 投影时：

1. **Execution plan 必须包含两个独立 op**：
   - `attn_wo_a`: batched einsum, shape per-group [heads_per_group * head_dim, o_lora_rank] = [4096, 1024], batch=16, ColumnParallelLinear（无通信）
   - `attn_wo_b`: 标准 GEMM [n_groups * o_lora_rank, hidden] = [16384, 7168], RowParallelLinear（有 all-reduce）
2. **TP 约束**: `o_groups` 必须能被 `world_size` 整除。TP=8 时每组 rank 处理 2 个 group；TP=16 时每组 1 个 group；TP>16 不支持（除非 o_groups >= TP）
3. **wo_a 实际是 FP8 权重**（checkpoint 中），参考实现用 BF16 einsum。如果仿真支持 FP8，profile 应使用 FP8 GEMM 数据
4. **两步之间有低秩瓶颈**: 中间维度 o_lora_rank=1024，仅为原始维度 65536 的 1.5%

**违反后果**: 将分组低秩 O 投影建模为单个标准 O 投影，会：(1) 参数量不匹配（469M vs 184M），影响内存估算；(2) GEMM shape 不匹配，profile 查表失败或数据不准；(3) TP sharding 逻辑错误（标准 O 投影是 RowParallelLinear all-reduce，但 wo_a 是 ColumnParallelLinear 无通信）。

**评估方法**: 验证新模型的 O 投影 execution plan 包含 attn_wo_a (einsum) + attn_wo_b (GEMM) 两个 op，且两者的 shape 和 parallel 类型正确。检查 o_groups % world_size == 0。

## 代码位置

- 文件路径: `ref/deepseek-v4/model.py:62-63` (ModelArgs: o_groups=16, o_lora_rank=1024)
- 文件路径: `ref/deepseek-v4/model.py:446,450-451` (Attention: o_lora_rank, n_groups, n_local_groups)
- 文件路径: `ref/deepseek-v4/model.py:462-463` (wo_a: ColumnParallelLinear, wo_b: RowParallelLinear)
- 文件路径: `ref/deepseek-v4/model.py:537-542` (forward: reshape → einsum → flatten → wo_b)

## 标准解决方案

**正确做法**:
1. 在 execution plan 中将 O 投影拆分为两个 op：`attn_wo_a` (batched einsum) + `attn_wo_b` (standard GEMM)
2. `attn_wo_a` profile: batched GEMM 参数 (batch=16, M=token_count, K=4096, N=1024)
3. `attn_wo_b` profile: 标准 GEMM 参数 (M=token_count, K=16384, N=7168)
4. TP 配置验证: o_groups % tp_size == 0

**常见错误做法**:
- 将 O 投影建模为单个 [7168, 65536] GEMM → shape 不匹配，profile 查表失败
- 忽略 wo_a 的 batched einsum 特性，用单个大 GEMM 近似 → 小 batch 时 batched GEMM 与单个大 GEMM 性能差异显著
- 将 wo_a 当作 RowParallelLinear → wo_a 是 ColumnParallelLinear，无 all-reduce
- 忽略 TP 约束 (o_groups % tp_size == 0) → TP=32 时 crash

**自动化建议**: 检查新模型的 config 中 o_groups 和 o_lora_rank 字段。如果两者都存在且 > 1，标记 O 投影为分组低秩结构，需要两个 op profile。同时验证 o_groups % tp_size == 0 的约束。

## 相关关键点

- KP-0006: 新增 Attention Kernel 只需改 attn_backend (wo_a/wo_b 是 Linear op，不是 attention kernel)
- KP-0023: 三分类 profiling 架构足以支撑 V4 (wo_a/wo_b 归入 token-level 类别)

## 发现过程

通过分析 V4 Attention 类的参数定义和 forward 方法发现。wo_a 被声明为 ColumnParallelLinear，但在 forward 中不是标准的 linear 调用，而是先 reshape 为 [n_local_groups, o_lora_rank, d] 再用 einsum("bsgd,grd->bsgr") 计算分组独立投影。这与 Q 路径的低秩两步设计（wq_a + wq_b）形成对称结构，但 O 路径额外引入了分组维度。
