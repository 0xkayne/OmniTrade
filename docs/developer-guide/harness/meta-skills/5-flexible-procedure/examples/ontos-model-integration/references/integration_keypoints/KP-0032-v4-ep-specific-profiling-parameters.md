# KP-0032: V4 EP 特有参数对 Profiling 的影响

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0032 |
| 日期 | 2026-05-15 |
| 阶段 | profiling |
| 严重度 | P1 |
| 状态 | constraint_defined |
| 发现者 | EP 策略分析 |

## 问题描述

**现象**: 直接复用 V3 的 EP profiling 数据或配置来模拟 V4，会导致预测偏差。
**根因**: V4 相比 V3 有多项 EP 相关参数变化:
- 384 experts (V3=256) → 每 rank 分配更多 experts
- top-6 (V3=top-8) → 每 token 路由到更少 experts，消息量降低
- intermediate=3072 (V3=2048) → expert 计算量增加 50%
- H100 上 FP8 expert weights (非 FP4 — FP4 是 Blackwell 专属)
**表现**: (1) FusedExperts profiling 使用错误的 GEMM shape; (2) dispatch 消息量计算错误; (3) FP4 GEMM 在 H100 上不可用。

## 约束定义

**必须满足的条件**:
1. FusedExperts profiling 必须使用 FP8 GEMM (非 FP4)，shape `[M', 7168] × [7168, 3072]`
2. DeepEP HT dispatch profiling 需覆盖 `dispatch_dtype=bf16` 和 `fp8` 两种路径
3. top-6 影响消息量: 每 token dispatch 消息 = `hidden_dim × dtype_size × topk = 7168 × 2 × 6` (bf16) 或 `7168 × 1 × 6` (fp8)
4. V4 在 H100 上 FP4 expert weights 不可用 — FP4 需要 Blackwell FP4 tensor cores

**违反后果**: 使用错误 GEMM shape 会导致 FusedExperts 耗时预测偏差 ~50%；使用 FP4 路径在 H100 上会 profiling 失败。

**检查方法**: (1) 验证 FusedExperts profiling CSV 中 intermediate_dim=3072; (2) 确认 GEMM dtype 为 FP8 (非 FP4) on H100; (3) 检查 all2all profiling 覆盖 bf16+fp8 dispatch dtype。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/ops/moe.py` (FusedExperts op, L88-99)
- 文件路径: `ontos/profiling/mlp/` (MLP/MoE GEMM profiling)
- 文件路径: `ontos/profiling/collectives/all_to_all/` (all2all profiling runners)
- 文件路径: `ontos/config/model_config.py` (model config 中 moe_mlp_hidden_dim, n_routed_experts 等)

## 标准解决方案

**正确做法**:
1. FusedExperts profiling: FP8 GEMM, shape 基于 V4 的 intermediate_dim=3072 和 hidden_dim=7168
2. All2All profiling: 覆盖 DeepEP HT (bf16+fp8 dispatch) 和 DeepEP LL (use_fp8=True/False)
3. 参数空间: dp_size ∈ {2, 4, 8, 16}, num_tokens 从 1 到目标 max

**常见错误做法**:
1. 复用 V3 的 FusedExperts profiling CSV (intermediate=2048, 差 50%)
2. 在 H100 上尝试 FP4 GEMM profiling
3. 只覆盖 bf16 dispatch dtype，遗漏 fp8 路径

**自动化建议**: 在 model config 中定义 `expert_quant_config` 字段，根据 GPU arch 自动选择 profiling dtype。H100→FP8, Blackwell→FP4。

## 相关关键点

- KP-0024: FP4 Expert 双层 Scale 量化 (FP4 量化的详细约束)
- KP-0030: MegaMoE 平台约束 (为什么 H100 不用 FP4)
- KP-0031: DeepEP HT vs LL Backend 选择 (dispatch dtype 路径)
- KP-0018: MoE All-to-All Profiling Backend 注册
- KP-0016: MoE Expert 参数量 (384 experts 参数量计算)

## 发现过程

对比 V3 (256 experts, top-8, intermediate=2048) 和 V4 (384 experts, top-6, intermediate=3072) 的 EP 参数差异，分析对 profiling 和 prediction 的影响。发现 V4 在 H100 上只能走 FP8 路径（FP4 是 Blackwell 专属），且 expert 计算量增加 50% 需要重新 profiling。
