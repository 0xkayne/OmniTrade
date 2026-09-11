# KP-0025: V4 mHC 引入 4x 激活张量倍增和 bandwidth-bound GEMM

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0025 |
| 日期 | 2026-05-14 |
| 维度 | residual |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 触发条件 | hc_mult IS NOT NONE AND hc_mult > 1 |
| 泛化标签 | mhc_activation_multiplier |
| 发现者 | 通过分析 V4 model.py 中 Block/ParallelHead 类和 kernel.py 中 hc_split_sinkhorn kernel 发现 |

## 问题描述

**现象**: V4 使用 modified Hyper-Connections (mHC) 替代标准残差连接，每层维护 hc_mult=4 份隐藏状态副本。这导致：(1) 所有激活张量从 [b, s, 7168] 变为 [b, s, 4, 7168]，内存带宽需求 4x；(2) 每个子层调用（attn + ffn）前各有一个 bandwidth-bound 的大 GEMM 用于计算混合权重；(3) Sinkhorn 迭代本身计算量可忽略（4x4 矩阵 × 20 轮），但 hc_post 产生 [4, 4, 7168] 中间张量。

**根因**: mHC 的数学约束要求混合矩阵必须是 doubly-stochastic（行和=1 且列和=1），通过 Sinkhorn 迭代实现。为此需要：(1) 一个大 GEMM [n, 28672] x [28672, 24] 计算混合参数（pre/post/comb 各 4 个值）；(2) 20 轮 row+column normalization on 4x4 matrix；(3) 加权求和收缩 4 副本→1 和广播扩展 1→4 副本。

**表现**: 对 decode 阶段（M=1），hc_pre 的 GEMM [1, 28672] x [28672, 24] 是极端 bandwidth-bound（算术强度 ~6 FLOP/byte），每次读取 ~2.7MB 权重仅产生 24 个 FP32 输出。每层 4 次 HC 操作（hc_pre_attn + hc_post_attn + hc_pre_ffn + hc_post_ffn），其中 2 次包含这个大 GEMM。更严重的是，4x 激活倍增影响模型中所有算子的内存带宽。

## 约束定义

**必须满足的条件**: 当新模型使用 mHC（hc_mult > 1）时，profiling 和仿真必须考虑：

1. **mHC 引入的新 op**（每层 4 个，attn 和 ffn 各 2 个）：
   - HCPre: RMS-norm + 大 GEMM [n, hc_mult*dim] x [hc_mult*dim, mix_hc] + Sinkhorn(20 iters) + weighted-sum 收缩
   - HCPost: broadcast-multiply + doubly-stochastic weighted-sum + expand
2. **核心 GEMM 参数**：`hc_fn` shape 为 `[mix_hc, hc_dim]` = `[24, 28672]`，mix_hc = (2 + hc_mult) * hc_mult
3. **Sinkhorn 可忽略**：4x4 矩阵 × 20 轮 = 320 次浮点除法，对 profile 无影响
4. **4x 激活倍增的系统性影响**：所有与 hidden state 交互的 op（layernorm, attention input/output, FFN input/output）的带宽消耗 ×4
5. **每层参数增量**：2 个 `hc_fn` (attn + ffn) × 24 × 28672 × 4 bytes ≈ 20.9 MB/block，60 层总计 ~1.25 GB

**违反后果**: 忽略 mHC 的开销会低估 decode 每步耗时。仅 4x 激活倍增就会使所有 bandwidth-bound op 耗时翻倍以上。hc_pre 的大 GEMM 在 decode 时每次 ~2.7MB 权重读取，每层 2 次，60 层合计 ~324MB 额外带宽。

**评估方法**: 验证新模型的 execution plan 中每个 Block 包含 4 个 HC op（hc_pre_attn, hc_post_attn, hc_pre_ffn, hc_post_ffn），且 hc_pre 包含 [n, hc_mult*dim] x [hc_mult*dim, mix_hc] GEMM 的 profile 数据。

## 代码位置

- 文件路径: `ref/deepseek-v4/model.py:78-80` (ModelArgs: hc_mult, hc_sinkhorn_iters, hc_eps)
- 文件路径: `ref/deepseek-v4/model.py:647-700` (Block: hc_pre, hc_post, forward)
- 文件路径: `ref/deepseek-v4/model.py:703-735` (ParallelHead: hc_head 简化版)
- 文件路径: `ref/deepseek-v4/kernel.py:371-427` (hc_split_sinkhorn kernel: Sinkhorn 迭代实现)
- 文件路径: `ref/deepseek-v4/kernel.py:430-438` (hc_split_sinkhorn Python wrapper)

## 标准解决方案

**正确做法**:
1. 将 HCPre 和 HCPost 作为独立 token-level op 加入 execution plan
2. HCPre profile：主要测量 GEMM [n, 28672] x [28672, 24]，Sinkhorn 和 weighted-sum 可作为固定开销附加
3. HCPost profile：主要测量 broadcast-multiply + weighted-sum 的带宽开销
4. 所有与 hidden state 交互的现有 op 的 profile 参数需要考虑 4x 激活张量宽度
5. 早期近似：HC ops 可估算为 bandwidth-bound 固定开销（decode: ~2.7MB × 2/层 × 60 层 ≈ 324MB 额外带宽）

**常见错误做法**:
- 将 HC 开销视为可忽略（"只是 element-wise"）→ 实际包含大 GEMM，不可忽略
- 将 Sinkhorn 迭代视为 HC 的主要开销 → Sinkhorn 计算 4x4 矩阵可忽略，真正开销在 GEMM 和 4x 激活倍增
- 用标准 residual add 的 profile 近似 mHC → mHC 比 add 复杂几个数量级
- 忽略 4x 激活倍增对其他所有 op 的系统性影响 → 低估整体带宽需求

**自动化建议**: 检查新模型的 config 中 hc_mult 字段。如果 hc_mult > 1，标记需要 mHC-specific profiling，并检查所有现有 op 的带宽模型是否需要乘以 hc_mult 倍数。

## 相关关键点

- KP-0023: 三分类 profiling 架构足以支撑 V4 (HC ops 归入 token-level 类别)
- KP-0021: 多流重叠导致串行累加高估耗时 (HC ops 可能参与并行调度)

## 发现过程

通过分析 V4 model.py 中 Block 类和 kernel.py 中 hc_split_sinkhorn kernel 发现。mHC 的三个组件（HCPre GEMM、Sinkhorn、HCPost expand）中，GEMM 和 4x 激活倍增是主要开销，Sinkhorn 计算量可忽略。关键洞察：mHC 不仅是"几个额外 op"，而是通过 4x 激活倍增影响模型中所有 op 的带宽特性。
