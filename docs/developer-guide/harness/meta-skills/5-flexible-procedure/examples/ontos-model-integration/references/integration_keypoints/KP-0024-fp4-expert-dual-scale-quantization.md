# KP-0024: V4 FP4 Expert 量化是双层 scale 架构，不是简单精度缩放

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0024 |
| 日期 | 2026-05-14 |
| 维度 | ffn |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 触发条件 | expert_dtype == "fp4" |
| 泛化标签 | fp4_dual_scale |
| 发现者 | 通过深入分析 V4 model.py 和 kernel.py 的量化流程发现 |

## 问题描述

**现象**: V4 的 routed expert 权重使用 FP4 (float4_e2m1fn) 存储，但 FP4 GEMM 并非简单的低精度 GEMM。实际执行流程为：激活量化为 FP8 → FP4 权重在 kernel 内部 cast 为 FP8 → FP8 x FP8 Tensor Core 计算 → 双层 scale 修正（activation scale per 128 + weight scale per 32）→ 输出 BF16。不能简单用 BF16/FP8 的 profile 数据乘以 0.5 缩放因子近似。

**根因**: FP4 只有 4 bit（2 exponent + 1 mantissa），硬件 Tensor Core 不原生支持 FP4 输入。V4 的 FP4 GEMM kernel 采用 FP4→FP8 cast + FP8xFP8 GEMM + 联合 scale 修正的策略。权重 scale 粒度为每 32 个元素（FP8 是每 128 个），产生 4x 更细的 scale 分辨率，影响计算精度和 GEMM 内部行为。

**表现**: 如果直接用 BF16 expert GEMM 的 profile 数据近似 FP4，会忽略：(1) activation quantization 的额外 kernel 开销；(2) FP4→FP8 cast 的 inner-loop 开销；(3) 更细粒度 scale 带来的不同 GEMM 调度模式；(4) FP4 权重加载带宽仅为 BF16 的 1/4，对 memory-bound 场景（小 batch decode）影响显著。

## 约束定义

**必须满足的条件**: 当新模型使用 FP4 expert 权重时，profiling 必须考虑以下异构量化管线的影响：

1. **Expert GEMM 不是单一 op**：实际流程包含 activation quant → FP4 GEMM (含 FP4→FP8 cast + dual-scale correction) → 输出
2. **不同组件量化方案不同**：
   - Routed experts: FP4 权重 + FP8 激活 (weight block=32, act block=128)
   - Shared experts: FP8 权重 + FP8 激活 (weight block=128, act block=128)
   - Attention projections: FP8 或 BF16
   - Gate: BF16 (FP32 计算)
   - Compressor (ratio=4): FP4 simulation (fused Q/DQ roundtrip, block=32)
   - Indexer: FP4 simulation (fused Q/DQ roundtrip, block=32)
3. **Compressor/Indexer 中的 FP4 simulation 不是真正的 FP4 计算**：它对 BF16 数据做 quantize→dequantize roundtrip，模拟 QAT 训练时的量化误差，这部分开销需要单独 profile
4. **Profile 数据不能跨量化方案复用**：FP4 expert、FP8 expert、BF16 expert 的 GEMM 耗时不同，需要各自的 profile 数据

**违反后果**: 使用 BF16/FP8 的 profile 数据乘以缩放因子近似 FP4，会在 memory-bound 场景（decode 小 batch）下严重低估带宽优势（FP4 权重带宽仅为 BF16 的 1/4），在 compute-bound 场景下高估或低估取决于 FP4→FP8 cast 开销。

**评估方法**: 验证新模型的 expert profiling 配置区分了 FP4 和 FP8 两种量化方案。如果 expert_dtype="fp4"，确保 profile 使用实际的 FP4 GEMM kernel 而非 BF16/FP8 近似。

## 代码位置

- 文件路径: `ref/deepseek-v4/model.py:18` (fp4_block_size=32)
- 文件路径: `ref/deepseek-v4/model.py:108-120` (Linear dispatch: FP4 路径)
- 文件路径: `ref/deepseek-v4/model.py:131-137` (FP4 权重张量布局)
- 文件路径: `ref/deepseek-v4/model.py:623-627` (Expert vs shared expert dtype 差异)
- 文件路径: `ref/deepseek-v4/kernel.py:441-511` (FP4 GEMM kernel 内部实现)
- 文件路径: `ref/deepseek-v4/kernel.py:128-183` (FP4 quant kernel，用于 Compressor/Indexer)

## 标准解决方案

**正确做法**:
1. Routed expert GEMM 用实际 FP4 kernel profile，不做缩放近似
2. Shared expert GEMM 用 FP8 kernel profile（与 V2/V3 共享数据）
3. Compressor/Indexer 中的 FP4 simulation (Q/DQ roundtrip) 作为独立 op profile 或估算
4. Activation quantization 开销可忽略（融合在 GEMM kernel 内），不需单独 profile
5. 早期快速验证路径：用 FP8 expert profile × 0.5~0.7 缩放因子近似 FP4，但标记为临时方案

**常见错误做法**:
- 用 BF16 expert profile × 0.5 近似 FP4 → 忽略了 FP4 的带宽优势和不同 GEMM 内部行为
- 将 routed expert 和 shared expert 使用同一份 profile 数据 → 两者量化方案不同
- 忽略 Compressor/Indexer 中的 FP4 simulation 开销 → 低估 C4 层计算量
- 认为 FP4 GEMM 是 dequantize-to-BF16 再做标准 GEMM → 实际是 FP4→FP8→Tensor Core + scale correction

**自动化建议**: 检查新模型的 expert 权重 dtype。如果是 FP4，标记需要 FP4-specific profiling。同时检查 attention 和 Compressor 中是否有 FP4 simulation 路径（inplace fused Q/DQ）。

## 相关关键点

- KP-0022: V4 C4 层有两个独立 Compressor (Compressor 中的 FP4 simulation)
- KP-0020: 异构融合 kernel 导致 op 无法独立 profile (FP4 GEMM 内部的多步骤融合)

## 发现过程

通过深入分析 V4 的 model.py 和 kernel.py 中的量化流程发现。V4 的 FP4 不是简单的"把 BF16 乘以 0.5"，而是一个完整的异构量化管线：不同组件使用不同量化方案（FP4/FP8/BF16/FP32），FP4 GEMM kernel 内部通过 FP4→FP8 cast + FP8xFP8 Tensor Core + 双层 scale 修正实现，且 Compressor/Indexer 中还有独立的 FP4 simulation (Q/DQ roundtrip) 路径。
