# KP-0048: Plan Op 粒度必须对齐 vLLM 融合 Kernel 边界

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0048 |
| 日期 | 2026-05-22 |
| 维度 | profiling |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 触发条件 | `任何新模型的集成计划生成后` |
| 泛化标签 | vllm_fused_op_alignment |
| 发现者 | DeepSeek V4 v1 vs v2 集成计划对比 |

## 问题描述

**现象**: v1 集成计划的 op list 基于 reference model (model.py) 逐行展开，每行代码对应一个独立 op。但 vLLM 实际执行时大量融合了这些 op。导致 profiling 量和 execution plan 与 vLLM 实际 serving 行为系统性偏差。

**根因**: Reference model 的设计目标是"正确性"和"可读性"，每个操作单独写。vLLM 的设计目标是"性能"，尽可能融合多个操作为单个 kernel。两者的粒度天然不同。

**表现**（DeepSeek V4 具体案例）:
| Reference Model 中的独立 op | vLLM 中的融合 kernel |
|---|---|
| wq_a, wkv (2 个 GEMM) | fused_wqa_wkv (1 个 MergedColumnParallelLinear GEMM) |
| q_norm, kv_norm (2 个 RMSNorm) | fused_q_kv_rmsnorm (1 个 Triton kernel) |
| q_rsqrt, q_rope, kv_rope, kv_quant, cache_save (5 个 op) | fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert (1 个 CUDA kernel) |
| compressor_wkv, compressor_wgate (2 个 GEMM) | compressor.fused_wkv_wgate (1 个 GEMM) |
| gated_pool, norm, rope, quant, cache_write (5 个 op) | _fused_kv_compress_norm_rope_insert_* (1 个 Triton kernel) |
| hc_post, hc_pre (2 个 op) | mhc_fused_post_pre (1 个 CUDA custom op) |
| ffn_norm, gate (2 个 op) | NormGateLinear (1 个 fused kernel) |

v1 计划有 ~40 op/层，vLLM 实际只有 ~15-20 kernel call/层。逐 op 独立 profile 再求和会系统性高估延迟（串行假设 vs 实际融合/并行）。

## 约束定义

**必须满足的条件**:
1. 集成计划的 op list 粒度必须对齐 vLLM 的融合 kernel 边界，而非 reference model 的逐行展开
2. 多个 reference model op 在 vLLM 中被融合为单个 kernel 时，plan 中应列为 1 个 op（而非 N 个）
3. Plan 的 op name 应反映 vLLM 的融合语义（如 `fused_wqa_wkv` 而非 `wq_a` + `wkv`）
4. vLLM 不再独立执行的 op（如被折叠的 `ffn_norm`）不应出现在 plan 的 op list 中

**违反后果**:
- Profiling 粒度与 vLLM 实际执行不匹配，仿真结果系统性偏差
- Execution plan 串行求和多个本应融合的 op → 高估延迟
- 无法利用 ParallelOpGroup 建模多流并行

**评估方法**:
1. 从 vLLM `DecoderLayer.forward()` 提取实际 kernel call 序列
2. 与 plan 的 op list 逐一对比
3. 每个 vLLM kernel call 应有且仅有 1 个 plan op 对应
4. 不应有 plan op 在 vLLM 中无对应 kernel call（除非明确标注为"框架开销"）

## 代码位置

- vLLM 模型主文件: `vllm/model_executor/models/deepseek_v4.py` — DecoderLayer._forward_cuda()
- vLLM Attention 文件: `vllm/model_executor/layers/deepseek_v4_attention.py` — MultiHeadLatentAttentionWrapper
- vLLM Compressor: `vllm/model_executor/layers/deepseek_compressor.py` — DeepseekCompressor
- vLLM HC ops: `vllm/model_executor/layers/mhc.py` — MHCPreOp / MHCFusedPostPreOp

## 标准解决方案

**正确做法**:
1. 在 model-integrator 的 Step 0.V 中，通过 AtCode MCP 追踪 vLLM 的实际执行流
2. 从 DecoderLayer.forward() → Attention → Compressor → MoE 逐层追踪
3. 每个 torch.ops.xxx 调用、每个融合 Linear/RMSNorm 对应 plan 中的 1 个 op
4. 用 vLLM 执行流 Dossier (Fused Op Map + Parallel Map) 指导 plan 的 op 定义

**常见错误做法**:
- 直接从 reference model model.py 提取 op list（粒度不匹配 vLLM）
- 假设 "逐 op 独立 profile 再求和" 可以近似融合 kernel 的耗时（忽略 kernel launch overhead 减少和 register reuse 等优化）
- 忽略 vLLM 中 "某些 op 不再独立执行" 的情况（如 ffn_norm 折叠进 norm_gate）

**自动化建议**: model-integrator Step 0.V 应产出 Fused Op Map，后续维度分析引用此 map 而非 reference model 的代码结构。

## 相关关键点

- KP-0020: 异构融合 kernel (SWA+compressed+sink 合一) — 本 KP 的具体案例之一
- KP-0021: 跨 op 并行 — 本 KP 的并行维度（融合之外还有多流并行）
- KP-0027: V4 per-layer op 清单验证 — 本 KP 升级了验证基准：从 reference model 代码级验证 → vLLM 执行流级验证

## 发现过程

DeepSeek V4 v1 集成计划的 op list 基于 ref/deepseek-v4/model.py 逐行展开（~40 op/层）。通过 AtCode MCP 分析 vLLM 源码后发现 vLLM 实际执行大量融合（fused_wqa_wkv、fused_qnorm_rope_kv_insert、mhc_fused_post_pre 等），实际 kernel call 仅 ~15-20 个/层。v1 plan 的逐 op 独立 profiling 完全不匹配 vLLM 的实际执行行为。v2 plan 以 vLLM 执行流为准重新定义 op 粒度。
