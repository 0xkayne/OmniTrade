# KP-0020: 异构融合 kernel 导致 op 无法独立 profile

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0020 |
| 日期 | 2026-05-14 |
| 维度 | attention |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 触发条件 | compress_ratios IS NOT NONE AND compress_ratios != 0 (模型有 SWA + compressed attention 融合 kernel) |
| 泛化标签 | heterogeneous_fusion |
| 发现者 | 通过对比 Llama3-8B 与 DeepSeek V4 的 kernel 融合模式分析发现 |

## 问题描述

**现象**: DeepSeek V4 的 `sparse_attn` kernel 将 SWA attention + compressed attention + attn_sink_correction 融合为一个原子 CUDA kernel call。仿真器的 ExecutionPlan 将其拆为多个 op（SWA_attn + compressed_attn），但实际只有一个 kernel call，无法分别测量各部分耗时。

**根因**: Llama3-8B 的融合是**同构**的——多个相同类型小操作合并为一个大 GEMM（如 Q+K+V 三个 `(dim, head_dim)` 投影融合为一个 `(dim, 3×head_dim)` GEMM）。融合边界与 op 划分边界对齐：融合前 3 个投影 → 融合后仍对应 1 个 op "attn_pre_proj"，op 数量不变。

V4 的融合是**异构**的——不同类型操作合并为一个 kernel（SWA attention + compressed attention + attn_sink）。融合后 op 边界消失：仿真器中有 2 个 op（SWA + compressed），但 GPU 上只有 1 个 kernel call。

**表现**: 无法获得 SWA attention 和 compressed attention 的独立耗时数据。如果把 `sparse_attn` 当整体 profile，CSV 的 `kv_cache_size` 参数需要表达混合长度（window + topk 或 window + compressed_len），而非标准 attention 的单一 `kv_cache_size`。

## 约束定义

**必须满足的条件**: 当新模型的 attention kernel 融合了多种不同类型的计算时（异构融合），必须将融合 kernel 作为一个整体 sequence-level op 进行 profile，参数为 `(batch_size, total_kv_entries_attended)`，而非尝试拆分为多个独立 op。

具体参数计算：
- C4 decode: `total = window(128) + topk(512) ≈ 640`
- C128 decode: `total = window(128) + compressed_len / 128`
- 纯 SWA (ratio=0): `total = window(128)`
- Prefill: `total = window + seq_len / ratio`

**违反后果**: 如果尝试拆分 profile 融合 kernel，要么测不到（kernel 是原子的），要么测量数据不反映实际执行。

**评估方法**: 验证新模型的 attention kernel 是否为融合 kernel。如果是，profile 时作为单个 sequence-level op 测量，参数为 `(batch_size, total_kv_entries_attended)` 而非 `(batch_size, kv_cache_size)`。

## 代码位置

- 文件路径: `ontos/profiling/attn_backend/` (需要新的 wrapper for sparse_attn)
- 文件路径: `ontos/execution_time_predictor/ops/attention.py` (可能需要新的 op 子类)
- 参考: `ref/deepseek-v4/model.py:528,533` (sparse_attn kernel call)

## 标准解决方案

**正确做法**:
1. 将融合 kernel 作为单个 op profile，参数为 `total_kv_entries_attended`
2. 分别为 C4 和 C128 两种 variant profile（因为 C4 用 top-k，C128 用 dense，KV 长度特征不同）
3. 纯 SWA 层（ratio=0）只有 window 部分，可以用标准 SWA attention profile
4. 不尝试拆分测量融合 kernel 的各部分

**常见错误做法**:
- 把 SWA attention 和 compressed attention 当作独立 op 分别 profile 再累加 → 实际只有一个 kernel call，分开测量的数据不反映真实执行
- 用标准 `(batch_size, kv_cache_size)` 参数 profile 融合 kernel → kv_cache_size 无法表达混合长度
- 忽略 C4 和 C128 的行为差异（top-k vs dense），用同一组 profile 数据 → 长序列时 C4 耗时被高估 ~500 倍

**自动化建议**: 检查新模型的 attention forward 中是否一个 kernel call 同时处理多种 KV 来源。如果是，标记为"融合 kernel op"，使用 `total_kv_entries_attended` 作为 profile 参数。

## 相关关键点

- KP-0021: 多流重叠导致串行累加高估耗时 (同为 V4 profile 精度问题)
- KP-0022: C4 层有两个独立 Compressor (同为 V4 attention 结构复杂性)
- KP-0023: 三分类 profiling 架构足以支撑 V4 (架构层面的约束)

## 发现过程

通过对比 Llama3-8B 和 DeepSeek V4 的 kernel 融合模式发现。Llama3-8B 的 QKV 投影融合是同构的（多个小 GEMM → 一个大 GEMM），op 边界不变。V4 的 SWA+compressed attention 融合是异构的（不同类型操作合并），op 边界消失。详见 `docs/v4_profiling_challenges.md`。
