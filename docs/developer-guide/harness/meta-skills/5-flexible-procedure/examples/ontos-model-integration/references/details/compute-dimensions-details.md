# Compute Dimensions Details

This file is mechanically extracted from the preserved legacy skill copy. Keep updates in active split skills and KP files unless intentionally refreshing legacy-derived details.

## 维度 3: FFN / MoE 架构

### 探测问题

| # | 问题 | 提取来源 | 判定逻辑 |
|---|------|----------|----------|
| 3.1 | 是否为 MoE？ | `n_routed_experts` | > 0 即 MoE |
| 3.2 | 有无 shared experts？ | `n_shared_experts` | > 0 即有 |
| 3.3 | top-k 路由数？ | `num_experts_per_tok` | 直接读取 |
| 3.4 | 路由方式？ | model.py gate 实现 | Top-K / Hash / Expert-Choice / 其他 |
| 3.5 | 激活函数？ | model.py FFN 定义 | SwiGLU / GeLU / 其他 |
| 3.6 | Expert 粒度？ | `n_routed_experts` 数量 | Fine-grained (>32) vs Coarse-grained (≤32) |
| 3.7 | 是否有交替 Dense/MoE 层？ | model.py DecoderLayer 定义 | 部分层用 Dense FFN |
| 3.V | vLLM Dossier: MoE kernel 类型、all2all 实现方式、EP backend？ | Dossier A+C | 检查 MoE op 融合、kernel 选择 (FusedMoE/MegaMoE)、all2all backend 对齐 |

### 决策树

```
n_routed_experts > 0?
  YES → MoE
    路由方式?
      Top-K → 复用 MoEFFN class
      Expert-Choice → Novel (expert 选 token，不是 token 选 expert)
      Hash → 需新建或扩展
      其他 → Novel → 进入未知创新处理协议
    n_shared_experts > 0?
      YES → MoE + Shared Experts → 现有 MoEFFN 已支持
      NO → 纯 MoE
    Expert 粒度?
      Fine-grained (>32) → [KP-0016] 参数量计算和 all2all 行为不同于粗粒度
      Coarse (≤32) → 标准 MoE
    有交替 Dense/MoE 层?
      YES → 需要在 execution plan 中按层区分 FFN 类型
      NO → 所有层统一 MoE

    Expert 量化方案? [KP-0024]
      Routed expert 和 shared expert 使用不同量化?
        YES → 需要独立的 profiling:
              Routed: 按实际量化 kernel profile (如 FP4 GEMM)
              Shared: 按 FP8/BF16 profile
              两者 GEMM shape 可能相同但耗时不同
        NO → 统一 profiling

      Compressor/Indexer 有 FP4 simulation (Q/DQ roundtrip)?
        YES → 额外的量化模拟开销需独立 profile 或估算
        NO → 不需要

  NO → Dense FFN → 复用 FFN class
```

### 实现路径

- Dense FFN → `ontos/execution_time_predictor/models/ffn.py` (复用)
- MoE → `ontos/execution_time_predictor/models/ffn.py` (复用 `MoEFFN`)
- 路由方式变化 → 扩展 MoEFFN 或新建

### KP 约束索引

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0016 | P1 | moe_expert_params | `n_routed_experts > 0` | 每个 expert 的参数量需独立计算: routed × single + shared × single |
| KP-0018 | P1 | moe_all2all | `n_routed_experts > 0` | MoE 模型必须注册 all-to-all profiling backend |
| KP-0024 | P1 | fp4_dual_scale | `expert_dtype == "fp4"` | FP4 expert 是双层 scale 架构 (FP4→FP8+Tensor Core+scale correction)，不同组件量化方案不同，profile 不能跨方案复用 |
| KP-0036 | P1 | moe_routing_inheritance | `scoring_func 与父类不同` | MoE 路由字段强相关 (scoring_func, n_group, topk_group, topk_method)，必须全部覆盖，否则 has_e_score_correction_bias 误判 |
| KP-0029 | P1 | moe_ep_hopper_accuracy | `MoE EP modeling on Hopper (sm_90)` | Hopper 用分离式 DeepEP kernel (无 overlap)，per-op 累加建模准确；仅 Blackwell/MegaMoE 需融合建模 |
| KP-0030 | P1 | megamoe_platform_constraint | `选择 EP backend 目标平台` | MegaMoE 融合 kernel 仅 Blackwell (sm_100) 可用；H100 必须走 DeepEP HT/LL 分离路径 |
| KP-0031 | P1 | deepep_ht_vs_ll_selection | `在 DeepEP HT 与 LL 间选择` | DeepEP HT/LL 用 EP group (非 DP group)，LL 将 finalize 融入 combine，LL 用分块执行+累加 |

#### 通用约束

> **Expert-only 量化** — 当 `expert_dtype` 与主 dtype 不同时，expert 权重和主权重必须按不同 dtype 计算参数量。不能让 expert 量化影响非 expert 权重。
>
> **评估**: 分别计算 expert 和非 expert 的 weight_size，加和后与官方参数量对比。

### 验证

```bash
python -c "
from ontos.config.model_config import BaseModelConfig
cfg = BaseModelConfig.create_from_name('model-name')
print(f'is_moe={cfg.is_moe_model()}')
# MoE: expert 参数量 = routed × single + shared × single
# 验证与官方参数量一致
"
```

---


## 维度 5: 量化方案

量化不是单一的"权重用什么精度"的问题。一个模型可能同时在三个不同层次上使用量化，每个层次的机制、目的、对 profiling 的影响完全不同。

### 三个量化层次

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 层次 1: 权重量化 (Weight Quantization)                                       │
│   什么: 权重以低精度格式存储                                                  │
│   影响: GEMM kernel 选择 (fp4_gemm / fp8_gemm / BF16 GEMM)                  │
│   Profile: 必须用对应 kernel profile，不同权重精度的 GEMM 不能共用数据          │
│                                                                             │
│ 层次 2: 激活量化 (Activation Quantization)                                    │
│   什么: GEMM 前将激活从 BF16 量化为低精度                                      │
│   影响: 融合在 GEMM kernel 内部，不是独立 op                                   │
│   Profile: 不需单独 profile，已包含在 GEMM kernel 耗时中                       │
│                                                                             │
│ 层次 3: QAT 模拟量化 (Simulation Quantization)                                │
│   什么: 对中间张量做 inplace Q/DQ roundtrip (量化→反量化)                      │
│   目的: 不是节省计算，是模拟训练时的量化噪声 (QAT fidelity)                      │
│   影响: 增加非 GEMM 算子的额外开销 (attention/compressor/indexer pipeline)     │
│   Profile: 开销需包含在对应 op 的 profile 中                                  │
│         可能按维度选择性量化 (如 RoPE 维不变，非 RoPE 维做 Q/DQ)                │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 探测问题

| # | 问题 | 层次 | 提取来源 | 判定逻辑 |
|---|------|------|----------|----------|
| 5.1 | 是否有权重量化？ | W | `quantization_config` / `expert_dtype` / `default_dtype` | 非全 BF16 即有 |
| 5.2 | 各组件的权重精度分别是什么？ | W | config + model.py `Linear` 构造的 dtype 参数 | 逐组件记录 (expert / attn / gate / HC / 其他) |
| 5.3 | 是否有激活量化？ | A | model.py `linear()` 中 `act_quant` 调用 | GEMM 前有 quantize 即有 |
| 5.4 | 激活量化格式和 block size？ | A | `act_quant` 参数 | dtype / block_size / scale_format |
| 5.5 | 是否有 QAT 模拟量化？ | S | model.py 中 `inplace=True` 的 quant 调用 (非 `linear()` 内的) | 有即触发 |
| 5.6 | 模拟量化发生在哪些位置？ | S | model.py 中 `fp4_act_quant` / `act_quant(..., inplace=True)` 的调用点 | 记录每个位置 + 格式 |
| 5.7 | 模拟量化是否有按维度选择性量化？ | S | 切片模式 (如 `kv[..., :-rd]`) | 有切片即部分维度不量化 |
| 5.8 | Scale dtype 是什么？ | W/A | `scale_dtype` 配置 | FP32 / E8M0 / 其他 |
| 5.V | vLLM Dossier: 量化相关的 Config Rewrite 和 kernel 映射？ | Dossier C+D | 检查量化配置是否被 vLLM 自动重写、profiling kernel 是否与 vLLM 实际 kernel 一致 |

### 决策树

```
┌─ 层次 1: 权重量化
│
│  有非 BF16 权重? (检查: config 中 dtype/expert_dtype, model.py 中 Linear 的 dtype 参数)
│    YES → 量化粒度?
│
│      ┌─ 全局统一 (所有 Linear 用同一 dtype):
│      │  → WeightQuantMode 枚举，所有 weight_size 统一计算
│      │  → Profile: 统一使用对应 kernel
│      │
│      ┌─ 按组件异构 (不同组件用不同 dtype):
│      │  逐组件枚举，构建量化映射表:
│      │    expert_dtype? → routed expert 权重格式
│      │    default_dtype? → attention projection 权重格式
│      │    显式 dtype 参数? → 特定 Linear 的权重格式 (如 wo_a=Bf16, HC=FP32)
│      │
│      │  [KP-0040] 框架必须支持 per-component 精度覆盖:
│      │    → 在 ModelConfig 中设置 component_dtype_overrides
│      │    → 异构模型的 get_param_size() 覆写基类按组件分组计算
│      │    → modules_to_not_convert 只能回退 BF16，FP32 组件必须用 overrides
│      │
│      │  每个 (组件, dtype) 组合需要独立的 GEMM profile
│      │  即使 shape 相同，不同 dtype 的 GEMM 耗时也不同 [KP-0024]
│      │
│      │  权重格式 → GEMM kernel 映射:
│      │    FP4 → fp4_gemm (FP4→FP8 cast + FP8×FP8 Tensor Core + dual-scale)
│      │           weight_block_size=32, act_block_size=128
│      │    FP8 → fp8_gemm (FP8×FP8 Tensor Core + single-scale)
│      │           block_size=128
│      │    BF16 → F.linear (标准 cuBLAS)
│      │    FP32 → F.linear (标准 cuBLAS, 通常仅 HC/Gate 等小矩阵)
│      │    其他 → Novel
│      │
│      ┌─ 按层异构 (不同层用不同 dtype):
│      │  → Per-layer 量化配置，需在 execution plan 中按层选择 kernel
│      │  例: 某 MoE 模型前半层用 BF16 expert，后半层用 FP8 expert
│
│    NO → 全 BF16，无权重量化

┌─ 层次 2: 激活量化
│
│  linear() 中 GEMM 前是否有 act_quant 调用?
│    YES → 激活量化存在:
│      激活量化通常与权重量化配套:
│        FP4 权重 → FP8 激活 (act_quant block=128)
│        FP8 权重 → FP8 激活 (act_quant block=128)
│        BF16 权重 → 无激活量化
│      → 已融合在 GEMM kernel 内，不是独立 op
│      → 不需要单独 profile
│      → 但 block_size 和 scale_format 影响 GEMM kernel 行为
│         需在 GEMM profile 参数中体现
│    NO → 无激活量化

┌─ 层次 3: QAT 模拟量化
│
│  model.py 中是否有 inplace quant 调用且不在 linear() 内?
│  (搜索: fp4_act_quant / act_quant / 其他 quant 函数
│   且调用点在 attention/compressor/indexer 的 forward 中
│   而不是在 linear() 的 dispatch 逻辑中)
│
│    YES → QAT 模拟量化存在:
│
│      模拟量化的位置 → 影响哪些 op 需要包含额外开销:
│        ┌───────────────────────────────┬────────────────────┬─────────────────────┐
│        │ 位置                          │ 量化方式            │ 归入哪个 op 的 profile │
│        ├───────────────────────────────┼────────────────────┼─────────────────────┤
│        │ Compressor rotate=True 的 KV  │ FP4 Q/DQ (blk=32)  │ Indexer compressor  │
│        │ Compressor rotate=False 的 KV │ FP8 Q/DQ (blk=64)  │ Attention compressor│
│        │ Indexer query                 │ FP4 Q/DQ (blk=32)  │ Indexer op          │
│        │ Attention window KV           │ FP8 Q/DQ (blk=64)  │ Attention op        │
│        │ 其他位置                      │ 按实际分析          │ 对应 op             │
│        └───────────────────────────────┴────────────────────┴─────────────────────┘
│
│      是否有按维度选择性量化? (检查切片模式)
│        YES → 例: kv[..., :-rd] 只量化非 RoPE 维度
│              RoPE 维度保持原始精度 (位置信息不可损)
│              → op 的 profile 需反映这种混合精度行为
│        NO → 全量量化
│
│      模拟量化对 profiling 的影响:
│        Q/DQ roundtrip 的开销 ∝ 张量大小
│        不改变 op 的参数空间 (仍是 token-level 或 sequence-level)
│        但增加了每个 op 的固定开销 → 必须用包含 Q/DQ 的实际 kernel profile
│        不能用"无 Q/DQ 的 profile + 估算 Q/DQ 开销"近似
│
│    NO → 无 QAT 模拟量化
```

### 量化方案对 Profiling 的影响总结

```
┌─────────────────┬──────────────────────────────────┬──────────────────────────────────┐
│ 量化层次         │ Profiling 影响                    │ 处理方式                          │
├─────────────────┼──────────────────────────────────┼──────────────────────────────────┤
│ 权重量化         │ 决定 GEMM kernel 选择              │ 每种 (dtype, shape) 独立 profile   │
│                 │ 不同 dtype → 不同 kernel → 不同耗时 │ 不能跨 dtype 复用 profile 数据      │
│                 │ FP4 额外: 权重带宽 ×0.5 (memory)   │ 即使 shape 相同也需分别 profile     │
│                 │              dual-scale (compute)  │                                   │
├─────────────────┼──────────────────────────────────┼──────────────────────────────────┤
│ 激活量化         │ 融合在 GEMM kernel 内              │ 不需单独 profile                   │
│                 │ 不产生独立 op                      │ GEMM profile 已包含此开销           │
│                 │ 但 block_size/scale 影响 kernel    │ profile 参数中需体现 block_size     │
├─────────────────┼──────────────────────────────────┼──────────────────────────────────┤
│ QAT 模拟量化     │ 增加 attention pipeline 的额外开销  │ 开销包含在对应 op 的 profile 中      │
│                 │ 不是独立 op，是 op 内部的额外步骤    │ 必须用含 Q/DQ 的实际 kernel profile │
│                 │ 可能按维度选择性量化                │ 不能用无 Q/DQ 的 profile 近似       │
└─────────────────┴──────────────────────────────────┴──────────────────────────────────┘
```

### KP 约束索引

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0024 | P1 | fp4_dual_scale | `expert_dtype == "fp4"` | FP4 是双层 scale 架构 (FP4→FP8+Tensor Core+scale correction)，不同组件量化方案不同，profile 不能跨方案复用 |
| KP-0040 | P0 | heterogeneous_quantization | `同一层内存在组件使用不同量化精度` | 框架必须支持 per-component 精度覆盖 (component_dtype_overrides)，不能退化为全局统一模式；参数大小计算必须按组件分组 |

### 通用约束

> **Profile 不可跨量化方案复用** — 即使 GEMM shape 完全相同，FP4/FP8/BF16 三种权重的 GEMM 耗时不同（kernel 内部行为、带宽、scale 修正均不同）。每种 (dtype, shape) 必须独立 profile。
>
> **QAT 模拟量化不是可选优化** — 如果模型在训练时使用了 QAT，推理时对应位置的 Q/DQ roundtrip 是必须的（影响数值精度），不能跳过。其开销必须反映在 profile 中。
>
> **按维度选择性量化需逐维度分析** — 如果量化只作用于张量的部分维度（如 V4 的非 RoPE 维），需确认 kernel 是否有对应的切片/混合精度处理，profile 参数需体现实际处理的元素数量。
>
> **异构量化必须先检查基础设施能力** — 当官方模型同一层内不同组件使用不同精度（如 FP8 权重 + BF16 O 投影 + FP32 HC 参数），框架的量化基础设施必须支持 per-component 精度覆盖。`modules_to_not_convert` 只能回退 BF16，无法表达 FP32。需要 `component_dtype_overrides` 机制 + 异构模型的 `get_param_size()` 覆写基类按组件分组计算。[KP-0040]

---

## 维度 6: 残差连接与特殊功能

### 探测问题

| # | 问题 | 提取来源 | 判定逻辑 |
|---|------|----------|----------|
| 6.1 | 残差连接方式？ | `hc_mult` / model.py | Add / HC / mHC / 其他 |
| 6.2 | HC 类型？ (如有 hc_mult) | model.py 中是否有 Sinkhorn / doubly stochastic | HC vs mHC (见下方区别) |
| 6.3 | 是否有投机解码/MTP？ | model.py / config | 有/无 |
| 6.4 | 位置编码？ | `rope_scaling` / model.py | RoPE / YaRN / ALiBi |
| 6.5 | Norm 类型？ | model.py | RMSNorm / LayerNorm |
| 6.V | vLLM Dossier: HC/MTP 相关 op 的融合和并行情况？ | Dossier A+B | 检查 HC op 是否被融合、MTP 的 execution plan 是否独立于主模型 |

### 决策树

```
残差:
  hc_mult 存在?
    YES → 检查 HC 变体:
      model.py 中使用 Sinkhorn-Knopp / doubly stochastic?
        YES → mHC (Manifold-Constrained Hyper-Connections, V4 使用)
              与标准 HC 的区别见下方
        NO → 标准 HC (Zhu et al., 2025)

      共同特征 (HC 和 mHC 共享):
        → 每层 4 个 op: hc_pre_attn, hc_post_attn, hc_pre_ffn, hc_post_ffn
        → hidden state: [batch, seq, hc_mult, dim]
        → 参数量: 每层 hc_attn_fn + hc_ffn_fn + hc_attn_base + hc_ffn_base + scales

      [KP-0025] hc_mult 倍激活张量的系统性影响:
        → 所有与 hidden state 交互的 op 带宽消耗 ×hc_mult
        → HCPre 核心开销: GEMM [n, hc_mult*dim] x [hc_mult*dim, mix_hc]
           mix_hc = (2 + hc_mult) * hc_mult
        → Sinkhorn 迭代计算量可忽略 (hc_mult × hc_mult 矩阵 × 少量轮次)
        → Decode 时 HCPre 的 GEMM 是极端 bandwidth-bound
        → 每层参数增量: 2 × mix_hc × hc_mult × dim × dtype_size

    NO → Standard Add → 复用现有 Add op
    其他 → Novel → 进入未知创新处理协议

特殊功能:
  MTP → MTPBlock 是完整 Block (不是简单投影)
  自定义位置编码 → 检查是否影响 KV cache / attention
```

### HC vs mHC 的区别

从仿真器角度，HC 和 mHC 的 **结构完全相同** (同样的 hc_pre/hc_post 流程，同样的 4 个 op/层，同样的 `[batch, seq, hc_mult, dim]` 隐藏状态形状)。区别仅在残差映射矩阵 `B_l` 的约束方式：

| 特征 | HC (标准) | mHC (V4) |
|------|-----------|----------|
| 残差映射 `B_l` | 自由学习 | 约束到双随机矩阵流形 (Birkhoff polytope) |
| 实现方式 | 直接学习 `n_hc × n_hc` 矩阵 | Sinkhorn-Knopp 迭代投影 |
| 训练稳定性 | 深层可能不稳定 | 非扩张变换，深层稳定 |
| **仿真器影响** | **无区别** | **无区别** |

**对仿真器的结论**: mHC 的 Sinkhorn 约束是训练时的数值技巧，推理时 `B_l` 已经是固定权重。从仿真器的 execution plan 角度，HC 和 mHC 完全等价——同样的 op 结构、同样的参数量、同样的内存布局。因此维度 6 的分析中不需要区分 HC 和 mHC，`hc_mult IS NOT NONE` 统一走 HC 处理路径即可。

### KP 约束索引

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0025 | P1 | mhc_activation_multiplier | `hc_mult IS NOT NONE AND hc_mult > 1` | mHC 引入 hc_mult 倍激活张量，每层 4 个 HC op (含大 GEMM)，所有 op 带宽消耗 ×hc_mult |

---

## 维度 7: Decode 阶段加速策略

### 设计动机

前 6 个维度分析的是模型的**静态架构**（注意力、FFN、KV cache 等结构特征）。但 decode 阶段的**执行策略**——每步产出多少有效 token、每步额外付出多少计算——是独立于模型结构的建模维度。

当前主流的 decode 加速策略谱系：

| 策略 | 代表模型/实现 | 核心思路 | 额外计算量 |
|------|-------------|----------|-----------|
| 标准 AR | 所有模型 | 每 step 产出 1 token | 无额外 |
| Speculative Decoding | vLLM, SGLang | 独立小模型起草，主模型验证 | draft model forward + 验证 |
| MTP (Multi-Token Prediction) | DeepSeek-V3/V4 | 模型自带额外 Block 预测未来 token | n_mtp_layers × 完整 Block |
| Medusa Heads | Medusa | lm_head 后挂多个线性预测头 | 几个 Linear 层 |
| Early Exit | 研究阶段 | 简单 token 跳过部分层 | 减少 per-token 计算量 |
| 其他 Novel | 未来模型 | — | 按未知创新处理协议分析 |

### 共性建模

所有策略都改变两个量：
- `tokens_per_step`: 每 decode step 产出的有效 token 数 (标准 AR = 1)
- `step_compute`: 每 decode step 的计算开销 (标准 AR = base_execution_plan)

仿真器的吞吐量计算：`throughput ∝ tokens_per_step / step_compute_time`

### 探测问题

| # | 问题 | 提取来源 | 判定逻辑 |
|---|------|----------|----------|
| 7.1 | 模型是否有 decode 加速策略？ | config.json / model.py | n_mtp_layers / medusa_heads / speculative_config |
| 7.2 | 加速策略类型？ | 策略特征 | 标准AR / MTP / Speculative / Medusa / 其他 |
| 7.3 | 加速策略的额外计算结构是什么？ | model.py 中相关类的继承关系 | 见下方决策树 |
| 7.4 | 加速策略对 execution plan 有何影响？ | 额外 ops + accept 逻辑 | 见下方分析 |
| 7.5 | 加速策略产出的额外 token 的 accept 率如何建模？ | 论文/实验数据 | 影响有效吞吐量 |
| 7.V | vLLM Dossier: MTP/speculative 的注册方式和执行流？ | Dossier A+D | 检查 vLLM 是否有对应的 speculative config 注册、实际执行路径 |

### 决策树

```
模型是否有 decode 加速策略?
  NO → 标准 AR: tokens_per_step = 1, 无额外计算

  YES → 策略类型?

    ┌─ MTP (模型自带预测层):
    │  特征: model.py 中有 MTPBlock / MTPLayer / 类似类
    │  config: n_mtp_layers > 0
    │
    │  MTPBlock 的结构 (以 DeepSeek V4 为例):
    │    继承 Block → 包含完整 Attention + MoE + HC
    │    新增: e_proj + h_proj + enorm + hnorm (embedding 投影融合)
    │    共享: embed 和 head (不重复计算)
    │    Attention: 只用 SWA (无 compressor/indexer) [KP-0014]
    │
    │  对仿真器的影响:
    │    execution plan: 每 decode step 多 n_mtp_layers 组 ops
    │      → SWA attention + MoE FFN + HC ops + 投影 ops
    │    参数量: MTPBlock 自身有 e_proj, h_proj, norms 等额外权重
    │    内存: MTPBlock 权重常驻 GPU (虽然共享 embed/head)
    │    accept 建模: tokens_per_step = 1 + n_mtp_layers × accept_rate
    │    profiling: 需要 profile MTPBlock 的 SWA attention + MoE + HC
    │
    │  注意: MTPBlock 的 attention/FFN/HC 复用维度 2/3/6 的分析结果
    │  但需要独立分析其 execution plan 和 profiling 需求

    ┌─ Medusa Heads (轻量线性预测头):
    │  特征: model.py 中有 MedusaHead / 类似轻量结构
    │  config: medusa_num_heads > 0
    │
    │  结构: 在 lm_head 后挂 num_heads 个 Linear(dim, vocab_size)
    │  每个预测头独立预测一个未来位置的 token
    │
    │  对仿真器的影响:
    │    execution plan: 每 decode step 多 num_heads 个 Linear op
    │    参数量: num_heads × (dim × vocab_size) — 通常不大
    │    accept 建模: 树状验证，accept_rate 取决于 head 准确度
    │    profiling: 只需 profile 额外的 Linear 层

    ┌─ Speculative Decoding (独立 draft model):
    │  特征: 配置中指定 draft_model_name / speculative_config
    │  不是一个模型内的结构，而是两个模型协作
    │
    │  对仿真器的影响:
    │    需要对 draft model 独立建模 (复用全套 1~9 维度分析)
    │    execution plan: draft model forward + target model 验证 forward
    │    accept 建模: tokens_per_step = draft_length × accept_rate
    │    profiling: draft model 的所有算子都需独立 profile
    │
    │  注: 这是仿真器层面的扩展，不是单个模型集成的问题

    ┌─ Early Exit (提前退出):
    │  特征: model.py 中有 exit_classifier / exit_threshold
    │  简单 token 在第 k 层就退出 (k < num_layers)
    │
    │  对仿真器的影响:
    │    per-token 的 execution plan 不再固定
    │    需要建模退出概率分布 P(exit_at_layer_k | token)
    │    平均计算量 = Σ P(k) × compute(first_k_layers)

    └─ 其他 → Novel → 进入未知创新处理协议
```

### 通用约束

**加速策略的 execution plan 独立于主模型**: 无论哪种策略，其额外 ops 应作为独立的 execution plan 片段建模，不能混入主模型的 execution plan。评估: 主模型的 `get_execution_plan()` 不含加速策略的 ops。

**Accept 率影响有效吞吐量**: 加速策略的有效性取决于 accept 率。`effective_throughput = base_throughput × (1 + extra_tokens × accept_rate) / (1 + compute_overhead_ratio)`。仿真器必须建模 accept 率，不能假设 100% 接受。

**额外权重占用的内存**: 加速策略引入的额外参数 (MTPBlock 权重、Medusa heads、draft model) 占用 GPU 内存，减少可用于 KV cache 的空间。必须在 memory planner 中计入。

### KP 约束索引

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0014 | P2 | mtp_swa_only | `有混合注意力 AND 有 MTP` | MTPBlock 的 attention 只用 SWA，不走 compressor/indexer |
| (待创建) | P1 | mtp_full_block | `n_mtp_layers > 0` | MTPBlock 继承完整 Block，参数量和计算量远大于简单投影 |
| (待创建) | P1 | decode_accel_memory | `有任何 decode 加速策略` | 加速策略的额外权重占用 GPU 内存，影响 KV cache 可用空间 |
| (待创建) | P2 | accept_rate_modeling | `tokens_per_step > 1` | accept 率建模影响有效吞吐量，不能假设 100% 接受 |

---


## 未知创新架构处理协议

当 Step 0.X 检测到某个维度包含决策树无法分类的架构创新时，该维度进入本协议。本协议是"模式匹配"模式的替代——从第一性原理出发，逐步理解新架构并映射到框架。

### 设计动机

以 DeepSeek V4 为例: 其注意力机制是 MQA + Compressor + Sparse Attn 的组合，在集成之初，决策树的所有已知分支都无法正确分类 (MLA? 不对。MQA? 只对了一半。Hybrid? 太笼统)。KP 库也没有可命中的约束。这种情况下，需要一套系统化的"探索→理解→建模"流程。

### 协议流程

```
检测到 Novel 维度
  │
  ├─ Phase A: 原始信息收集
  │     深度阅读 model.py 源码，提取计算图
  │
  ├─ Phase B: 第一性原理分析
  │     从 Memory / Compute / Data Flow 三个轴分析影响
  │
  ├─ Phase C: 框架映射
  │     将新架构映射到框架的 op / model / attention 抽象
  │
  ├─ Phase D: 探索性任务生成
  │     生成研究型 subagent 任务 (不是实现型任务)
  │
  └─ Phase E: 经验沉淀
        创建 provisional KP → 集成完成后升级为正式 KP
```

### Phase A: 原始信息收集

对 Novel 维度，执行深度代码阅读 (而非仅依赖 config.json):

```
1. 找到 model.py 中该维度相关的所有类和方法
2. 提取完整的计算图:
   - 输入张量形状
   - 每步操作的类型 (Linear / Activation / Attention / Norm / Custom)
   - 输出张量形状
   - 中间变量的生命周期
3. 与已知模式做对比:
   - 哪些部分与已知模式相同? (可复用)
   - 哪些部分是新增的? (需建模)
   - 新增部分的本质是什么? (新投影? 新选择策略? 新注意力计算?)

输出: 计算图文档 (Markdown 格式，包含每步的 shape 注解)
```

### Phase B: 第一性原理分析

从三个轴分析新架构对仿真器的影响:

```
Memory 轴:
  - 新架构如何影响 KV cache 大小? (per token 的 KV 存储量)
  - 是否引入新的中间状态? (如 HC 的 [batch, seq, hc_mult, dim])
  - 峰值显存占用如何变化?

Compute 轴:
  - 新增了哪些计算操作? (不能混入已有 op 的耗时)
  - 每个新操作的计算量 ∝ 什么? (seq_len? batch? hidden_dim?)
  - 是否改变了已有操作的计算量? (如 sparse attention 改变了 attention 的 FLOPs)

Data Flow 轴:
  - 新架构改变了层间数据流吗?
  - 是否引入新的维度间依赖? (如 compress_ratios 影响 KV cache)
  - 是否需要新的 execution plan op?

输出: 三轴影响报告
```

### Phase C: 框架映射

将第一性原理分析的结果映射到框架的具体抽象:

```
映射检查清单:

1. 需要新的 AttentionModel 子类吗?
   判断: KV 投影路径是否与所有已知类本质不同
   是 → 新建子类
   否 → 扩展现有子类

2. 需要新的 execution plan op 吗?
   判断: 是否有计算操作无法用现有 op 表达
   是 → 定义新 op (名称、输入、输出、参数量公式)

3. 需要新的 KV cache group 吗?
   判断: 是否存在 per-layer 的 KV cache 大小差异
   是 → 定义新 group

4. 需要新的 attention backend 吗?
   判断: 是否使用了现有 backend 不支持的 kernel
   是 → 在 attn_backend/ 新增

5. 需要新的 profiling wrapper 吗?
   判断: 是否有无法用现有 wrapper 覆盖的新操作
   是 → 新建 wrapper

输出: 框架映射方案 (列出所有需要的新建/扩展)
```

### Phase D: 探索性任务生成

与已知维度的"实现型任务"不同，Novel 维度生成"研究型任务":

> ### Exploration Task: 理解 <Novel 特性>
>
> **类型:** 研究 (不是实现)
> **目标:** 确认 <Novel 特性> 在框架中的正确建模方式
>
> **研究步骤:**
>   1. 阅读 model.py 中 <相关类> 的 forward 方法
>   2. 绘制完整计算图 (标注每步 shape)
>   3. 对比最相似的已知模式 (Step -1 相似度报告推荐)
>   4. 确认三轴影响 (Memory/Compute/Data Flow)
>   5. 输出: 框架映射方案
>
> **研究完成后:**
>   - 如果映射方案确定 → 转为实现型 subagent 任务
>   - 如果仍有不确定 → 创建 provisional KP，标记为 exploratory
>
> **Keypoint 捕获:**
>   研究过程中发现的任何约束或陷阱，立即调用 keypoint-capture


### Phase E: 经验沉淀

探索完成后，将发现沉淀为正式 KP:

```
1. 研究过程中创建的 provisional KP:
   状态: exploratory → constraint_defined
   内容: 补充完整的触发条件、评估方法

2. 实现过程中发现的额外约束:
   状态: discovered → constraint_defined
   内容: 标准 KP 流程

3. 更新决策树:
   在对应维度的决策树中增加新分支
   新分支的判定逻辑从探索结果中提取

4. 更新相似度基准:
   新模型成为已集成模型库的一员
   其特征向量加入相似度评估的候选集
```

---
