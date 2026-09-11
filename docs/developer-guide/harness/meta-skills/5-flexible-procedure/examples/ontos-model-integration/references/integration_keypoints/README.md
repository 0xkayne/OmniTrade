# 模型集成关键点索引

## 统计

| 严重度 | 数量 |
|--------|------|
| P0-致命 | 15 |
| P1-严重 | 29 |
| P2-中等 | 13 |
| P3-轻微 | 0 |
| **总计** | **57** |

## 索引

| ID | 标题 | 阶段 | 严重度 | 状态 | 泛化标签 |
|----|------|------|--------|------|----------|
| [KP-0001](KP-0001-v4-heterogeneous-kv-cache-groups.md) | V4 异构 KV Cache 需要分组建模而非单一 Pool | kv_cache | P0 | constraint_defined | heterogeneous_kv |
| [KP-0002](KP-0002-prefix-caching-hybrid-attention-conflict.md) | Prefix Caching 在混合 Attention 模式下不兼容 | kv_cache | P1 | constraint_defined | prefix_caching_conflict |
| [KP-0003](KP-0003-memory-planner-compression-ratio.md) | Memory Planner 的 Per-Token KV 计算必须适配压缩比 | kv_cache | P0 | constraint_defined | compression_memory |
| [KP-0004](KP-0004-decode-three-pool-growth-rates.md) | Decode 阶段三个 KV Cache 池的增长速率不同 | kv_cache | P2 | constraint_defined | kv_growth_rate |
| [KP-0005](KP-0005-swa-memory-bottleneck.md) | SWA 可能反直觉地成为内存瓶颈 | kv_cache | P2 | constraint_defined | swa_memory_bottleneck |
| [KP-0006](KP-0006-attn-backend-extension-boundary.md) | 新增 Attention Kernel 只需改 attn_backend，不动 attention 编排层 | attention | P1 | constraint_defined | backend_extension |
| [KP-0007](KP-0007-block-size-backend-constraints.md) | Block Size 约束因 Attention Backend 而异 | attention | P2 | constraint_defined | backend_extension |
| [KP-0008](KP-0008-compressor-indexer-profiling.md) | V4 的 Compressor 和 Indexer 需要独立的 Profiling | profiling | P2 | constraint_defined | extra_ops_profiling |
| [KP-0009](KP-0009-v4-per-layer-hybrid-composition.md) | V4 每层是 SWA + 压缩 Attention 的组合，不是互斥的层类型 | attention | P0 | constraint_defined | per_layer_attn_variation |
| [KP-0010](KP-0010-c4-overlapping-receptive-field.md) | C4 压缩有重叠感受野（stride=4, receptive_field=8） | attention | P1 | constraint_defined | compressed_attn |
| [KP-0011](KP-0011-c4-sparse-vs-c128-dense.md) | C4 用 Sparse Top-K 选择，C128 用 Dense Attention | attention | P1 | constraint_defined | compressed_attn |
| [KP-0012](KP-0012-v4-mqa-single-kv-head.md) | V4 所有 Attention 类型共享 MQA（num_kv_heads=1） | attention | P2 | constraint_defined | mqa_kv_dim |
| [KP-0013](KP-0013-shadow-radix-virtual-coordinate.md) | ShadowRadix 虚拟坐标系 + Shadow 投影的设计思想 | attention | P2 | constraint_defined | prefix_caching_bounded |
| [KP-0014](KP-0014-mtp-swa-only-no-compressor.md) | MTP Head 只用 SWA Attention，不走 Compressor/Indexer | attention | P2 | constraint_defined | mtp_swa_only |
| [KP-0015](KP-0015-gqa-head-divisibility.md) | GQA 的 num_q_heads 必须被 num_kv_heads 整除 | attention | P1 | seed | gqa_head_divisibility |
| [KP-0016](KP-0016-moe-expert-parameter-counting.md) | MoE Expert 参数量需独立计算并与官方数据交叉验证 | ffn | P1 | seed | moe_expert_params |
| [KP-0017](KP-0017-swa-bounded-kv-cache.md) | SWA 的 KV Cache 有硬上限，Memory Planner 需感知 | kv_cache | P1 | seed | swa_bounded_kv |
| [KP-0018](KP-0018-moe-all2all-profiling.md) | MoE 模型必须注册 All-to-All Profiling Backend | profiling | P1 | seed | moe_all2all |
| [KP-0019](KP-0019-rope-scaling-affects-attention-dimensions.md) | 非标准 RoPE 配置可能影响 Attention 维度计算和 Backend 选择 | attention | P2 | seed | rope_dimension |
| [KP-0020](KP-0020-heterogeneous-fused-kernel.md) | 异构融合 kernel 将不同类型 op 合并为一个 kernel call，op 边界消失 | attention | P0 | constraint_defined | heterogeneous_fusion |
| [KP-0021](KP-0021-multi-stream-overhead-estimation.md) | 多流并行执行导致串行累加高估 decode 耗时近 2 倍 | attention | P0 | constraint_defined | cross_op_parallelism |
| [KP-0022](KP-0022-dual-compressor-in-c4.md) | V4 C4 层有两个独立 Compressor（attention + indexer 各一个） | attention | P1 | constraint_defined | dual_compressor |
| [KP-0023](KP-0023-three-category-profiling-sufficiency.md) | 三分类 profiling 架构足以支撑 V4，但 ExecutionPlan 需扩展 | profiling | P1 | constraint_defined | profiling_architecture |
| [KP-0024](KP-0024-fp4-expert-dual-scale-quantization.md) | V4 FP4 Expert 量化是双层 scale 架构，不同组件量化方案不同 | ffn | P1 | constraint_defined | fp4_dual_scale |
| [KP-0025](KP-0025-mhc-four-x-activation-multiplier.md) | V4 mHC 引入 4x 激活张量倍增和 bandwidth-bound 大 GEMM | residual | P1 | constraint_defined | mhc_activation_multiplier |
| [KP-0026](KP-0026-grouped-lowrank-o-projection.md) | V4 分组低秩 O 投影拆分为 wo_a batched einsum + wo_b GEMM 两步 | attention | P1 | constraint_defined | grouped_lowrank_o_proj |
| [KP-0027](KP-0027-v4-per-layer-op-list-verification.md) | V4 Per-Layer Op 清单代码级验证发现 9 项遗漏/错误 | profiling | P1 | discovered | op_list_verification |
| [KP-0028](KP-0028-v4-op-shape-verification-corrections.md) | V4 Op Shape 代码级验证修正（Indexer Compressor 维度 + HC GEMM M 维度） | profiling | P1 | discovered | shape_verification |
| [KP-0029](KP-0029-moe-ep-separation-modeling-accuracy-hopper.md) | MoE EP 分离式建模在 Hopper 上精度充分，无需 overlap 模型 | execution | P1 | constraint_defined | ep_separation_accuracy |
| [KP-0030](KP-0030-megamoe-platform-constraint-and-backend-selection.md) | MegaMoE 融合 kernel 仅 Blackwell 可用，Hopper 必须走 DeepEP HT/LL | execution | P1 | constraint_defined | megamoe_platform |
| [KP-0031](KP-0031-deepep-ht-vs-ll-backend-selection-strategy.md) | DeepEP HT vs LL 的通信组、finalize 融合和 chunk 语义差异 | execution | P1 | constraint_defined | deepep_backend_selection |
| [KP-0032](KP-0032-v4-ep-specific-profiling-parameters.md) | V4 EP 特有参数 (384 experts, top-6, FP8 on H100) 需独立 profiling | profiling | P1 | constraint_defined | v4_ep_profiling_params |
| [KP-0033](KP-0033-is-mla-model-check-kv-lora-rank.md) | is_mla_model() 必须检查 kv_lora_rank 而非 RoPE 维度 | config | P0 | constraint_defined | mla_detection |
| [KP-0034](KP-0034-get-head-size-must-use-explicit-field.md) | get_head_size() 必须使用显式 head_dim 字段 | profiling | P0 | constraint_defined | head_dim_calculation |
| [KP-0035](KP-0035-profiling-modelconfig-must-accept-all-fields.md) | Profiling ModelConfig 必须接受所有 BaseModelConfig 字段 | config | P1 | constraint_defined | config_field_sync |
| [KP-0036](KP-0036-moe-routing-config-inheritance.md) | MoE 路由配置继承 — 扁平 vs 分组路由 | config | P1 | constraint_defined | moe_routing_inheritance |
| [KP-0037](KP-0037-fusedmoe-all-moe-types-class-attribute.md) | FusedMoE ALL_MOE_TYPES 必须定义为类属性 | profiling | P1 | constraint_defined | missing_class_attribute |
| [KP-0038](KP-0038-attn-backend-matrix-mla-mqa-sparse.md) | Attention Backend 矩阵必须区分 MLA-sparse 和 MQA-sparse | profiling | P1 | constraint_defined | sparse_backend_selection |
| [KP-0039](KP-0039-tilelang-sparse-attn-block-size-64.md) | TileLang Sparse Attention Kernel Block Size 必须是 64 的倍数 | profiling | P2 | constraint_defined | tilelang_block_size |
| [KP-0040](KP-0040-heterogeneous-quantization-infrastructure.md) | 异构量化模型需要 per-component 精度覆盖机制 | quantization | P0 | constraint_defined | heterogeneous_quantization |
| [KP-0041](KP-0041-config-inheritance-audit.md) | Config 继承链全面审计 — 所有父类字段必须与新模型规格对比 | config | P1 | constraint_defined | config_inheritance_audit |
| [KP-0042](KP-0042-attn-layer-patterns-limitation.md) | `attn_layer_patterns` 不能表达逐层稀疏注意力，使用模型特定元组 | attention | P1 | constraint_defined | per_layer_attn_mechanism |
| [KP-0043](KP-0043-elementwise-op-zero-time.md) | ElementWiseOp CSV 列缺失时静默返回 0 时间 | profiling | P2 | constraint_defined | csv_column_silence |
| [KP-0044](KP-0044-serving-vs-simulation-ops.md) | 区分 Serving-Only 与 Simulation-Required 操作 | profiling | P2 | constraint_defined | serving_vs_simulation_ops |
| [KP-0045](KP-0045-naming-convention-map.md) | 外部 vs 内部命名规范映射必须显式文档化 | attention | P2 | constraint_defined | naming_convention_map |
| [KP-0046](KP-0046-v4-kv-cache-dtype-and-model-path.md) | V4 KV Cache 必须为 FP8 且 Model Path 需正确配置 | kv_cache | P0 | constraint_defined | kv_cache_dtype_hard_requirement, model_path_structure |
| [KP-0047](KP-0047-profiling-op-completeness.md) | Execution Plan Op 必须与 Profiling 数据 1:1 完备 | profiling | P0 | constraint_defined | profiling_op_completeness |
| [KP-0048](KP-0048-plan-op-granularity-vllm-alignment.md) | Plan Op 粒度必须对齐 vLLM 融合 Kernel 边界 | profiling | P0 | constraint_defined | vllm_fused_op_alignment |
| [KP-0049](KP-0049-composite-pipeline-and-conditional-kernels.md) | 复合管线 Op 和条件 Kernel 变体需独立 Profiling | profiling | P1 | constraint_defined | composite_pipeline_profiling |
| [KP-0050](KP-0050-plan-parameter-source-code-verification.md) | Plan 参数值必须通过 vLLM 源码级自动验证 | profiling | P0 | constraint_defined | plan_parameter_verification |
| [KP-0051](KP-0051-plan-must-cover-model-level-ops.md) | Plan 必须覆盖 Model-Level Ops（非 Per-Layer） | profiling | P0 | constraint_defined | model_level_ops_coverage |
| [KP-0052](KP-0052-torch-profiler-not-e2e-ground-truth.md) | Torch Profiler Trace 不能作为端到端 Ground Truth | profiling | P1 | constraint_defined | profiler_wall_perturbation |
| [KP-0053](KP-0053-trace-op-occurrence-preservation.md) | Trace 导出必须保留重复 Op Occurrence | profiling | P0 | constraint_defined | trace_op_occurrence_preservation |
| [KP-0054](KP-0054-profile-time-semantic-critical-path.md) | Profiling 时间语义必须区分 Rank-Sum 与 Critical-Path Wall | profiling | P0 | constraint_defined | rank_sum_vs_wall_time_semantics |
| [KP-0055](KP-0055-single-request-steady-state-validation.md) | 先用 Warmed Single-Request Steady-State 验证算子集成 | profiling | P1 | constraint_defined | single_request_steady_state_validation |
| [KP-0056](KP-0056-profile-complete-not-client-e2e-complete.md) | Profiling 数据完备不等于 Client E2E 模型完备 | profiling | P1 | constraint_defined | profiling_data_vs_e2e_scope |
| [KP-0057](KP-0057-config-reconstruction-preserve-enum-types.md) | 手工重建 Config 必须保留 Enum 类型语义 | profiling | P2 | constraint_defined | config_enum_type_preservation |

## 来源统计

| 来源类型 | 数量 |
|----------|------|
| 集成 DeepSeek-V4 时发现 | 40 |
| DeepSeek V4-Pro N8/TP8 仿真-真实 bench 对齐验证 | 6 |
| 种子 KP (代码约定提取) | 5 |

## 泛化标签索引

| 泛化标签 | 关联 KP | 适用模型范围 |
|----------|---------|-------------|
| per_layer_attn_variation | KP-0009 | V4, Gemma-2/3 (SWA+Full 交替) |
| compressed_attn | KP-0010, KP-0011 | V4 (C4/C128) |
| backend_extension | KP-0006, KP-0007 | 所有需要新 backend 的模型 |
| extra_ops_profiling | KP-0008 | 所有有额外 op 的模型 |
| gqa_head_divisibility | KP-0015 | LLaMA-2/3, Qwen-2/2.5, Mistral, Gemma, Phi-3 |
| moe_expert_params | KP-0016 | Mixtral, Qwen-MoE, DeepSeek, DBRX, Llama-4 |
| swa_bounded_kv | KP-0017 | Mistral, Gemma-2/3, Qwen-2.5, DeepSeek-V4 |
| moe_all2all | KP-0018 | 所有 MoE 模型 |
| rope_dimension | KP-0019 | Qwen-2 (YaRN), CodeLlama (Dynamic NTK) |
| prefix_caching_conflict | KP-0002 | 所有有多 group KV cache 的模型 |
| compression_memory | KP-0003 | 所有有 KV 压缩的模型 |
| heterogeneous_kv | KP-0001 | V4, Gemma-2/3 |
| mqa_kv_dim | KP-0012 | V4, 其他 MQA 模型 |
| mtp_swa_only | KP-0014 | DeepSeek-V4, 其他有 MTP 的模型 |
| heterogeneous_fusion | KP-0020 | V4, 其他有异构融合 kernel 的模型 |
| cross_op_parallelism | KP-0021 | V4, 其他有多流并行执行的模型 |
| dual_compressor | KP-0022 | V4 C4 层 |
| profiling_architecture | KP-0023 | 所有新型融合/并行模型 |
| fp4_dual_scale | KP-0024 | V4, 其他 FP4 expert 模型 |
| mhc_activation_multiplier | KP-0025 | V4, 其他使用 mHC 的模型 |
| grouped_lowrank_o_proj | KP-0026 | V4, 其他分组低秩 O 投影模型 |
| op_list_verification | KP-0027 | 所有新模型集成 (op 清单验证) |
| shape_verification | KP-0028 | 所有新模型集成 (shape 代码级验证) |
| ep_separation_accuracy | KP-0029 | 所有 MoE 模型 (EP 建模方式选择) |
| megamoe_platform | KP-0030 | DeepSeek-V4 (Blackwell 融合路径) |
| deepep_backend_selection | KP-0031 | 所有使用 DeepEP 的 MoE 模型 |
| v4_ep_profiling_params | KP-0032 | DeepSeek-V4 (384 experts, top-6) |
| mla_detection | KP-0033 | 所有使用 qk_nope/qk_rope 但非 MLA 的模型 (V4, 其他 MQA+RoPE 模型) |
| head_dim_calculation | KP-0034 | 所有自定义 head_dim 的模型 (V4 MQA, Gemma2) |
| config_field_sync | KP-0035 | 所有新增 BaseModelConfig 字段时 |
| moe_routing_inheritance | KP-0036 | 所有从 MoE 父类继承但路由策略不同的模型 |
| missing_class_attribute | KP-0037 | 通用代码完整性约束 |
| sparse_backend_selection | KP-0038 | 所有 sparse attention 模型 (MLA-sparse vs MQA-sparse) |
| tilelang_block_size | KP-0039 | 所有使用 TileLang kernel 的 backend |
| heterogeneous_quantization | KP-0040 | 所有同一层内不同组件使用不同精度的模型 (V4, 未来异构量化模型) |
| config_inheritance_audit | KP-0041 | 所有继承已有 ModelConfig 的新模型 |
| per_layer_attn_mechanism | KP-0042 | 所有有非 SWA/full 逐层注意力变化的模型 (V4, 未来混合稀疏模型) |
| csv_column_silence | KP-0043 | 所有新增 ElementWiseOp 的模型集成 |
| serving_vs_simulation_ops | KP-0044 | 所有参考模型中有仅 serving 依赖的模型 (V4, 其他有 CUDA kernel 依赖的模型) |
| naming_convention_map | KP-0045 | 所有复用已有术语但语义不同的模型 (V4 FlashMLA, 未来同名不同义的模型) |
| kv_cache_dtype_hard_requirement | KP-0046 | 所有 KV cache dtype 有硬性约束的模型 (V4 FP8, 未来自定义 KV cache 格式) |
| model_path_structure | KP-0046 | 所有权重目录结构非标准的模型 (V4 根目录 vs V2/V3 子目录) |
| profiler_wall_perturbation | KP-0052 | 所有用 torch profiler trace 解释真实 serving E2E 误差的模型 |
| trace_op_occurrence_preservation | KP-0053 | 所有需要 per-op/per-layer trace 聚合和逐 kernel 对比的模型 |
| rank_sum_vs_wall_time_semantics | KP-0054 | 所有使用 TP/DP/EP 多 rank profiling 数据训练或校准 simulation 的模型 |
| single_request_steady_state_validation | KP-0055 | 所有用真实 serving benchmark 判断算子集成精度的模型 |
| profiling_data_vs_e2e_scope | KP-0056 | 所有以 client-observed TTFT/TPOT/E2E 为最终指标的验证任务 |
| config_enum_type_preservation | KP-0057 | 所有从 YAML/JSON 手工恢复 dataclass config 的分析脚本 |

## 依赖关系

```
KP-0009 (每层组合) ──┬──→ KP-0001 (分组建模) ──┬──→ KP-0003 (memory planner)
                     │                          ├──→ KP-0004 (增长速率)
                     │                          ├──→ KP-0005 (SWA 瓶颈)
                     │                          └──→ KP-0002 (prefix caching)
                     ├──→ KP-0010 (重叠感受野)
                     ├──→ KP-0011 (sparse vs dense)
                     ├──→ KP-0012 (MQA)
                     ├──→ KP-0014 (MTP SWA-only)
                     └──→ KP-0022 (双 Compressor)

KP-0006 (attn_backend 边界) ──→ KP-0007 (block size 约束)
KP-0006 (attn_backend 边界) ──→ KP-0008 (compressor/indexer profiling)

KP-0002 (prefix caching 互斥) ←── KP-0013 (ShadowRadix 设计思想)

KP-0017 (SWA 有界 KV) ──→ KP-0005 (SWA 瓶颈)
KP-0016 (MoE 参数量) ──→ KP-0018 (MoE all2all)
KP-0015 (GQA 整除) ──→ 参数量计算 (Step 0.5)

KP-0009 (每层组合) ──→ KP-0020 (异构融合) ──┬──→ KP-0021 (多流并行)
                                              └──→ KP-0023 (三分类架构充分性)

KP-0016 (MoE 参数量) ──→ KP-0024 (FP4 双层 scale 量化)
KP-0024 (FP4 量化) ──→ KP-0022 (双 Compressor 中的 FP4 simulation)

KP-0025 (mHC 4x 激活倍增) ──→ 全局带宽影响 (所有 op 的 bandwidth model)

KP-0009 (每层组合) ──→ KP-0026 (分组低秩 O 投影)
KP-0026 (O 投影) ──→ TP 约束 (o_groups % tp_size == 0)

KP-0027 (Op 清单验证) ──┬──→ KP-0008 (compressor/indexer profiling 的子 op 拆分)
                         ├──→ KP-0025 (mHC 4x 倍增 — hc_pre GEMM 即产生 4x 的 op)
                         ├──→ KP-0026 (wo_a grouped einsum 特性)
                         ├──→ KP-0023 (三分类架构充分性 — 新增 op 影响)
                         ├──→ KP-0020 (异构融合 — hc_pre 异构子操作)
                         └──→ KP-0022 (双 Compressor — indexer compressor 中也有 RoPE/Hadamard)

KP-0028 (Shape 验证修正) ──┬──→ KP-0022 (双 Compressor — Indexer Compressor 的独立 head_dim=128)
                            ├──→ KP-0025 (mHC 4x — flatten 只影响 K 维不影响 M 维)
                            └──→ KP-0027 (Op 清单验证过程中发现的具体 shape 错误)

KP-0029 (Hopper EP 分离式建模精度) ──┬──→ KP-0030 (MegaMoE 平台约束 — 同一分析的不同方面)
                                      └──→ KP-0031 (DeepEP HT vs LL 选择)

KP-0018 (MoE all2all) ──→ KP-0031 (DeepEP HT vs LL 选择策略)
KP-0016 (MoE 参数量) ──→ KP-0032 (V4 EP 特有 profiling 参数)
KP-0024 (FP4 量化) ──→ KP-0032 (V4 EP 特有 profiling 参数 — FP4 vs FP8 路径)
KP-0030 (MegaMoE 平台) ──→ KP-0032 (V4 EP profiling — 平台决定 dtype)

KP-0033 (is_mla_model 检查) ──→ KP-0038 (sparse backend 选择 — 依赖正确的 MLA 判断)
KP-0012 (MQA KV 维度) ──→ KP-0033 (is_mla_model 检查 — MQA 模型不应被判为 MLA)
KP-0012 (MQA KV 维度) ──→ KP-0034 (head_dim 计算 — MQA 的 emb//q != head_dim)
KP-0016 (MoE 参数量) ──→ KP-0036 (MoE 路由继承 — 路由参数影响 expert 选择)

KP-0006 (attn_backend 边界) ──→ KP-0038 (sparse backend 区分 — backend 选择逻辑扩展)
KP-0007 (block size 约束) ──→ KP-0039 (TileLang block size 64 — 新 backend 的 block 约束)
KP-0038 (sparse backend 选择) ──→ KP-0039 (TileLang block size — C4/C128 backend 的 block 约束)

KP-0024 (FP4 双层 scale 量化) ──→ KP-0040 (异构量化基础设施 — V4 异构精度的框架层面抽象)
KP-0016 (MoE 参数量) ──→ KP-0040 (异构量化 — 参数量计算依赖正确的 per-component 精度)
KP-0025 (mHC 4x 激活倍增) ──→ KP-0040 (HC 参数是 FP32，需要 per-component 覆盖)
KP-0026 (分组低秩 O 投影) ──→ KP-0040 (wo_a 是 BF16，需要 per-component 覆盖)

KP-0033 (is_mla_model) ──┬──→ KP-0041 (继承链审计 — 同一继承链的通用化抽象)
KP-0036 (MoE 路由继承) ──┘
KP-0009 (每层组合) ──→ KP-0042 (attn_layer_patterns 局限 — compress_ratios 是正确机制)
KP-0020 (异构融合) ──┬──→ KP-0043 (ElementWiseOp 填 0 — 融合 kernel 子 op 的隐含处理)
                      └──→ KP-0044 (serving-only ops — 融合 kernel 内子 op 不需要独立 profiling)
KP-0033 (is_mla_model) ──┬──→ KP-0045 (命名映射 — 同名不同义的根因)
KP-0038 (sparse backend) ─┘

KP-0007 (block size 约束) ──→ KP-0046 (V4 KV cache dtype + model path — V4 的 block_size=64 只影响 SWA)
KP-0044 (serving vs simulation) ──→ KP-0046 (serving 端 FP8 KV cache，simulation 端需对齐配置)

KP-0047 (op 完备性) ──→ KP-0056 (profile complete 只代表 GPU/model op 完备，不代表 client E2E 完备)
KP-0048 (vLLM 融合粒度) ──→ KP-0054 (对齐 kernel 边界后还必须对齐 rank-sum vs wall 时间语义)
KP-0055 (single-request steady-state) ──┬──→ KP-0052 (profiler 只做归因，不做 E2E ground truth)
                                        ├──→ KP-0053 (逐 op 分析需 lossless occurrence trace)
                                        └──→ KP-0054 (用 warmed request 识别 critical-path 时间语义)
KP-0035/KP-0041 (config 字段/继承审计) ──→ KP-0057 (手工重建 config 不得破坏 Enum 语义)
```

## 关键依赖链

**KP-0009 → KP-0001 是最核心的依赖链。**
KP-0009 揭示了 V4 每层都是 SWA + compressed 的组合，这直接决定了 KP-0001（需要两个 KV cache group）的设计。
所有其他关键点都从这条链衍生。
