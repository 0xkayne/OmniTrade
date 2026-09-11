# KP-0049: 复合管线 Op 和条件 Kernel 变体需独立 Profiling

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0049 |
| 日期 | 2026-05-22 |
| 维度 | profiling |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 触发条件 | `模型有条件触发的管线 op（如 compressor/indexer）或 per-layer kernel 变体（如 HC 首/非首层）` |
| 泛化标签 | composite_pipeline_profiling |
| 发现者 | DeepSeek V4 v2 集成计划审查 |

## 问题描述

**现象**: Plan 的 2.3 Profiling 计划中遗漏了 3 类需要独立 profiling 的 op：
1. 复合管线 op（compressor_fused_pipeline、indexer_full_pipeline）
2. 条件 kernel 变体（hc_pre_attn 首层 vs hc_fused_post_pre 非首层）
3. vLLM 折叠 op（norm_gate 替代独立的 ffn_norm + gate）

**根因**: 这类 op 在 vLLM 中不是简单的单个 GEMM，而是多步骤管线或条件变体：
- `compressor_fused_pipeline` 是一个 Triton kernel（_fused_kv_compress_norm_rope_insert_*），执行 gated pooling → RMSNorm → RoPE → FP8 quant → cache write 的完整管线
- `indexer_full_pipeline` 包含 wq_b GEMM + internal compressor + fused_q_rope_quant + scoring + topk 的多 kernel 组合
- HC 首层用 MHCPreOp（单 GEMM + Sinkhorn），非首层用 MHCFusedPostPreOp（2x GEMM + 2x Sinkhorn + combine），计算量差约 2 倍

**表现**: 若不独立 profile：
- Compressor/Indexer 管线的开销被遗漏，仿真低估 decode 延迟
- HC 首/非首层用同一个 profiling 数据，首层延迟估计偏差约 50%
- norm_gate 的 fused 行为无法从独立 norm + gate 的 profile 中推导

## 约束定义

**必须满足的条件**:
1. 多步骤管线 op（如 compressor 的 _fused_kernel）必须作为独立 profiling 条目，不能用子组件 profile 的简单求和替代
2. 同一逻辑 op 的不同 kernel 变体（如 HC 首/非首层、C4/C128 的不同 compressor）必须有独立的 profiling 条目
3. vLLM 中融合了多个 reference model op 的 kernel（如 NormGateLinear 融合 norm+gate），必须作为融合后的整体 profiling，不能拆分为独立 profile

**违反后果**: 仿真延迟偏差，特别是 decode 阶段（compressor/indexer 的触发频率高，HC 首层/非首层的差异大）

**评估方法**:
```bash
# 检查 plan 的 profiling 条目是否覆盖所有变体
grep -c "hc_pre_attn\|hc_fused_post_pre" <plan_file>  # 应为 2（首层+非首层）
grep -c "compressor_fused_pipeline\|indexer_full_pipeline" <plan_file>  # 应 >= 1
```

## 代码位置

- Compressor _fused_kernel: `vllm/model_executor/layers/deepseek_compressor.py:330-378`
- Indexer forward: `vllm/model_executor/layers/deepseek_v4_attention.py:1153-1173`
- HC MHCPreOp: `vllm/model_executor/layers/mhc.py:13-92`
- HC MHCFusedPostPreOp: `vllm/model_executor/layers/mhc.py:231-286`
- NormGateLinear: `vllm/model_executor/layers/fused_moe/router/norm_gate_linear.py:62-114`

## 标准解决方案

**正确做法**:
1. 识别 vLLM 中所有复合管线 op（通过 DecoderLayer.forward() → 子组件 forward() 的调用链追踪）
2. 为每个管线 op 创建独立 profiling 条目，标注完整执行流
3. 为同一 op 的不同 kernel 变体创建独立条目（如 hc_pre_attn 和 hc_fused_post_pre）
4. 在 profiling wrapper 中直接调用 vLLM 的复合 op（如 compressor.forward()），测量整体耗时

**常见错误做法**:
- 仅 profile 复合管线的初始 GEMM（如 compressor 的 fused_wkv_wgate），遗漏后续的 _fused_kernel
- 将 HC 首/非首层视为"同一 op"共用一个 profile
- 尝试用独立 op 的 profile 求和近似复合管线（忽略了管线内部的数据依赖和 kernel launch overhead）

**自动化建议**: Step 0.V 的 Phase 3/4 中，追踪每个子组件的完整 forward() 执行流，标注哪些是"初始 GEMM"（可在 Phase 1 并行组中）和哪些是"完整管线"（需独立 profile）。

## 相关关键点

- KP-0022: V4 C4 层有两个独立 Compressor — 本 KP 的具体案例：attention compressor + indexer compressor
- KP-0025: mHC 激活倍增 — 本 KP 的具体案例：hc_pre 和 hc_fused_post_pre 的不同 compute 量
- KP-0048: Plan op 粒度对齐 vLLM — 本 KP 是 KP-0048 在复合管线层面的细化

## 发现过程

在审查 DeepSeek V4 v2 plan 的 2.1 vs 2.3 对比时，发现 compressor_fused_pipeline（Triton _fused_kernel）和 indexer_full_pipeline（多 kernel 组合）在 2.1 中列出但 2.3 中缺失。进一步检查发现 HC 首/非首层也是不同 kernel。这些都不是"可忽略的轻量 op"，而是有真实计算量的管线 op。
