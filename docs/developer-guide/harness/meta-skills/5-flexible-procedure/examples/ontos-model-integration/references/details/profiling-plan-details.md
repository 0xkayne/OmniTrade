# Profiling Plan Details

This file is mechanically extracted from the preserved legacy skill copy. Keep updates in active split skills and KP files unless intentionally refreshing legacy-derived details.

## 维度 8: Profiling 支持

### 前提: Profiling 的工作方式

Profiling 在**真实 GPU** 上执行，测量每个算子的实际耗时。框架的 profiling 是**逐算子单独测量**，不是跑整个模型：

```
Profiling 子系统 (各自独立):
  attention/  → 对 (batch_size, seq_len, kv_cache_size, is_prefill, tp) 的笛卡尔积
                 调用 attention kernel → 记录耗时 → 输出 CSV
  mlp/        → 对 (num_tokens, ffn_type, tp/ep) 的组合
                 调用 MLP/MoE kernel → 记录耗时 → 输出 CSV
  collectives/ → 对 (message_size, world_size) 的组合
                  调用 NCCL all-reduce/all-to-all → 记录耗时 → 输出 CSV
```

仿真时，execution time predictor 从 CSV 中插值查询对应参数组合的耗时。因此 profiling 数据的准确性直接决定了仿真结果的准确性。

### Prefill 与 Decode 阶段的 Profiling 差异分析

LLM serving 分为 prefill (处理 prompt) 和 decode (逐 token 生成) 两个阶段，它们的计算模式不同，需要在 profiling 时分析每个子系统是否需要区分对待：

```
各子系统对 Prefill/Decode 的敏感度分析:

┌─────────────────┬──────────────────────────────┬───────────┬──────────────────────────────────────┐
│ 子系统          │ Prefill vs Decode 的差异      │ 需区分 PD? │ 原因                                 │
├─────────────────┼──────────────────────────────┼───────────┼──────────────────────────────────────┤
│ Attention       │ 根本不同:                     │ ✓ 必须    │ Prefill: Q=[1, chunk, h, d] 大矩阵   │
│                 │ prefill=compute-bound         │           │   compute-bound, batch=1              │
│                 │ decode=memory-bound           │           │ Decode: Q=[batch, 1, h, d] 1 token    │
│                 │                               │           │   memory-bound, 读整个 KV cache       │
│                 │                               │           │ 两者的耗时曲线无法用同一条线拟合       │
├─────────────────┼──────────────────────────────┼───────────┼──────────────────────────────────────┤
│ MLP / MoE       │ 无本质差异:                   │ ✗ 不需要  │ MLP 是纯 feed-forward，无状态依赖     │
│                 │ 耗时只取决于 num_tokens        │           │ 同样 512 tokens:                      │
│                 │ 不取决于 token 来自哪个阶段     │           │   1 req prefill 512 → [512, dim]      │
│                 │                               │           │   512 req decode 1 → [512, dim]       │
│                 │                               │           │ MLP 看到的输入 shape 相同              │
│                 │                               │           │ 扫描 num_tokens 全范围 (1~128K) 即可  │
├─────────────────┼──────────────────────────────┼───────────┼──────────────────────────────────────┤
│ Collectives     │ 间接差异:                     │ △ 需分析  │ PD 不直接影响 kernel 行为              │
│ (all-reduce /   │ message_size 在 PD 阶段不同    │           │ 但 PD 影响 message_size:              │
│  all-to-all)    │ 但由调度层决定，profiling 时   │           │   Prefill: 大 activation → 大 message │
│                 │ 只扫 message_size 即可         │           │   Decode: 小 activation → 小 message  │
│                 │                               │           │ 扫描 message_size 全范围即可           │
├─────────────────┼──────────────────────────────┼───────────┼──────────────────────────────────────┤
│ Compressor /    │ 可能有差异:                   │ △ 需分析  │ Prefill: 批量压缩多 token             │
│ Indexer (V4)    │ 批量 vs 增量压缩模式不同       │           │ Decode: 增量压缩 1 token              │
│                 │ 取决于 kernel 是否区分 PD      │           │ 如果 kernel 实现统一 → 不需要区分     │
│                 │                               │           │ 如果有专用 decode kernel → 需要区分   │
├─────────────────┼──────────────────────────────┼───────────┼──────────────────────────────────────┤
│ HC ops          │ 无差异                        │ ✗ 不需要  │ hc_pre/hc_post 耗时只取决于 dim 和    │
│                 │                               │           │ hc_mult，与 PD 无关                   │
└─────────────────┴──────────────────────────────┴───────────┴──────────────────────────────────────┘

结论:
  Attention → 必须区分 PD，且两者都要做完整参数扫描
  MLP/MoE → 不需要区分，扫描 num_tokens 全范围覆盖 PD 场景
  Collectives → 不需要区分，扫描 message_size 全范围覆盖
  额外算子 (compressor/indexer 等) → 需逐个分析 kernel 是否有 PD 特化
```

**并行策略与 Profiling 的关系**

不同并行策略对 profiling 的影响差异很大。核心判断原则：**是否改变了单个 GPU 上算子的输入参数（shape/dtype/count）？** 改变了 → 必须作为 profiling 扫描维度；未改变 → 不需要。

```
┌────────────┬─────────────────────────────────────────┬──────────┬──────────────────────────────────────────────────────┐
│ 策略       │ 对 Profiling 的影响                      │ 需扫描?  │ 何时可能变为有影响                                    │
├────────────┼─────────────────────────────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ TP (张量   │ 直接改变算子输入参数:                     │ ✓ 必须   │ (已为有影响)                                          │
│ 并行)      │  ColumnParallel: [N,K]→[N/tp,K]         │          │ TP 不均匀切分 (如 head_dim 不可整除) → padding 影响   │
│            │  RowParallel:    [N,K]→[N,K/tp]         │          │ profile 参数                                          │
│            │  Attention:      num_heads→num_heads/tp │          │                                                       │
│            │  → 必须对 tp ∈ {1,2,4,8,...} 扫描       │          │                                                       │
├────────────┼─────────────────────────────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ EP (专家   │ 直接改变 per-device expert 数量:          │ ✓ 必须   │ (已为有影响)                                          │
│ 并行)      │  local_experts = n_routed / ep_degree   │          │ Expert 负载均衡策略变化 → 影响实际 per-device         │
│            │  → all-to-all 通信模式和 expert GEMM     │          │ expert 数量，可能需动态 profiling                      │
│            │    输入参数都变化                         │          │                                                       │
│            │  → 必须对 ep_degree 扫描                 │          │                                                       │
├────────────┼─────────────────────────────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ CP (上下文 │ 改变 attention 和相关算子的参数:           │ ✓ 必须   │ (已为有影响)                                          │
│ 并行)      │  本地 seq_len = global_seq / cp_degree  │          │                                                       │
│            │  但 kv_cache_size 可能仍为全局 (ring)    │          │                                                       │
│            │  详见下方 CP 深入分析                    │          │                                                       │
├────────────┼─────────────────────────────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ PP (流水线 │ 不改变算子参数:                           │ ✗ 不需要 │ 异构 GPU 集群: 不同 stage 用不同 GPU 类型 →           │
│ 并行)      │  每个 stage 执行完整 forward，            │          │ 需要 per-GPU-type 独立 profiling                      │
│            │  算子的 shape/dtype 不变                  │          │ 非均匀 stage: 某些 stage 跨度不同 → 调度影响，        │
│            │  只影响调度 (micro-batch 交错)            │          │ 但 per-op profiling 仍不变                             │
├────────────┼─────────────────────────────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ DP (数据   │ 不改变算子参数:                           │ ✗ 不需要 │ Expert replication 与 EP 混合: 如果部分 expert        │
│ 并行)      │  完整模型复制到每个设备，                  │          │ 在多个 rank 上 replicate → 改变 per-device expert     │
│            │  每个设备独立处理不同请求                  │          │ 数量，实质上变成 EP 的问题                             │
│            │  (推理阶段无梯度同步开销)                  │          │                                                       │
├────────────┼─────────────────────────────────────────┼──────────┼──────────────────────────────────────────────────────┤
│ SP (序列   │ 改变 token-level 算子的 activation shape: │ △ 需分析 │ 独立于 TP 的 SP 实现: 某些框架将 SP 作为独立          │
│ 并行)      │  LayerNorm/Dropout 的 activation 沿      │          │ 配置项而非 TP 的附属 → 需作为独立扫描维度              │
│            │  seq 维切分，activation 大小变为           │          │                                                       │
│            │  [batch, seq/tp, dim]                    │          │                                                       │
│            │  通常作为 TP 实现的一部分，不独立扫描      │          │                                                       │
│            │  但对 bandwidth-bound 算子可能有影响      │          │                                                       │
└────────────┴─────────────────────────────────────────┴──────────┴──────────────────────────────────────┘
```

**CP (Context Parallelism) 深入分析**

CP 将长序列沿 seq 维切分到多个设备，对 profiling 的影响因模型的注意力机制而异：

```
┌─ 标准模型 (MHA/GQA) + CP:
│  Ring Attention 实现:
│    每个设备持有 seq/cp_degree 的 token
│    KV 通过 ring 在设备间流动
│    Attention kernel 参数:
│      Q: [batch, seq_local, heads, head_dim]
│      KV: [batch, seq_local, heads, head_dim] (多次迭代，每次处理不同 segment)
│    → seq_len 变为 seq/cp_degree，但需迭代 cp_degree 次处理完整 KV
│    → 单次 attention kernel 调用的参数与 seq=seq_local/cp 等价
│    → Profiling 扫描维度: cp_degree 影响 effective seq_len
│
│  All-to-all CP 实现:
│    每个 rank 有完整 KV (通过 all-to-all 交换)
│    → Attention kernel 参数不变 (seq=global_seq, kv=global_kv)
│    → 但增加了 all-to-all 通信 op
│    → Profiling: attention 不变，collectives 需新增 KV exchange pattern

┌─ V4 (C4/C128 压缩注意力) + CP:
│  压缩机制与 CP 的交互是关键差异:
│
│  Compressor 在 CP 下的行为:
│    每个 CP rank 只持有 local tokens → compressor 压缩 local segment
│    compressed_kv_len_local = local_seq / compress_ratio
│    如果直接 all-gather → 全局 compressed_kv = cp_degree × compressed_kv_local
│    → C4 的 top-k 选择范围扩大 (在更大池中选 top-k)
│    → C128 的 dense attention 范围扩大
│
│  Indexer 在 CP 下的行为:
│    C4 的 Indexer 需要对所有 compressed KV 打分做 top-k
│    CP 下 compressed KV 分布在各 rank → 需要 compressed KV all-gather
│    → 新增通信 op: compressed_kv_all_gather (不同于标准 NCCL)
│    → Indexer 的打分范围 = 全局 compressed KV (比无 CP 时更大)
│    → sparse_attn 的 total_kv_entries_attended [KP-0020] 发生变化:
│       无 CP: window(128) + topk(512)
│       有 CP: window(128) + topk(512) × cp_degree (或保持 512，取决于实现)
│
│  对 Profiling 的影响:
│    1. Attention kernel: effective kv_cache_size 随 cp_degree 变化
│       → 必须将 cp_degree 加入 attention profiling 扫描维度
│    2. Compressor/Indexer: 输入参数可能变化 (local vs global seq)
│       → 需确认 kernel 是否区分 local/global compressed KV
│    3. 新增通信 op: compressed KV all-gather
│       → collectives profiling 需新增此 pattern
│    4. sparse_attn 的 total_kv_entries_attended [KP-0020]:
│       公式可能变为: total = window + topk_per_rank (或 total = window + global_topk)
│       需分析实际 kernel 实现确定
│
│  SWA 层 (ratio=0) + CP:
│    SWA 只看本地窗口 (window=128)，窗口内的 token 大概率在同一个 CP rank
│    → CP 对 SWA 层几乎无影响 (除非 seq_len < window × cp_degree)
│    → 但需验证: 如果 window 跨越 CP boundary，需要 ring attention 或 KV exchange

│  通用结论:
│    有压缩注意力机制 (compress_ratios IS NOT NONE) + CP → 必须分析压缩 KV 的
│    分布和聚合策略，这可能引入新的通信 pattern 和改变 attention kernel 参数
│    纯 SWA 层 + CP → 影响小，但需验证 boundary case
│    标准注意力 + CP → 影响 seq_len 参数，已有成熟处理方式
```

**Profiling 扫描维度总结:**

```
每个子系统的扫描维度 (笛卡尔积):

Attention:  (batch_size, seq_len, kv_cache_size, is_prefill, tp_degree[, cp_degree])
            cp_degree 仅在模型使用 CP 时加入
            对 V4: kv_cache_size → total_kv_entries_attended [KP-0020]

MLP/MoE:   (num_tokens, ffn_type, tp_degree[, ep_degree])
            ep_degree 仅在 MoE 模型时加入
            对 V4 FP4 expert: 需区分 FP4/FP8 路径 [KP-0024]

Collectives: (message_size, world_size, collective_type)
             collective_type ∈ {all_reduce, all_to_all, send_recv[, compressed_kv_all_gather]}
             compressed_kv_all_gather 仅在有压缩注意力 + CP 时加入

额外算子:    (num_tokens, tp_degree[, cp_degree])
  (Compressor  cp_degree 仅在算子行为随 CP 变化时加入
   /Indexer    需逐个分析 kernel 是否区分 local/global context
   /HC 等)
```

### 探测问题

| # | 问题 | 提取来源 | 判定逻辑 |
|---|------|----------|----------|
| 8.1 | 是否需要新 attention backend？ | 注意力类型 vs 现有 backend 覆盖 | 不在现有枚举中即需要 |
| 8.2 | 模型是否提供了自定义 kernel？ | 检查 kernel.py / kernel 文件 | 有自定义 kernel 则必须用它做 profiling |
| 8.3 | 自定义 kernel 使用了什么框架？ | kernel.py 的 import | Triton / TileLang / CUDA C++ / 其他 |
| 8.4 | 有无额外操作需 profiling？ | compressor / indexer / HC / 其他 | 有额外 op 即需要 |
| 8.5 | Block size 约束？ | kernel 文档 / 源码 | 直接读取 |
| 8.6 | 哪些并行策略影响 profiling 扫描维度？ | 配置中的 tp/ep/cp degree | TP/EP/CP 改变算子参数则加入扫描；PP/DP 不影响则不加 |
| 8.7 | CP 是否影响压缩注意力的 kernel 参数？ | compress_ratios + cp_degree | 有压缩注意力 + CP → 分析 compressed KV 的分布策略 |
| 8.V | vLLM Dossier: profiling wrapper kernel 与 vLLM serving kernel 是否一致？ | Dossier E | 对齐检查: profiling 用的 kernel ≠ vLLM 用的 kernel → 标记对齐风险 |

### 决策树

```
┌─ 第零层: Profiling 配置完整性验证
│
│  get_head_size() 是否返回正确的 head_dim? [KP-0034]
│    验证: model_config.get_head_size() == model_config.head_dim
│    如果不等 → get_head_size() 实现有 bug
│      常见原因: 用 embedding_dim // num_q_heads 而非显式 head_dim
│      影响: 所有 attention wrapper 的 QKV 张量维度错误
│
│  Profiling ModelConfig 是否接受所有字段? [KP-0035]
│    验证: ModelConfig.from_model_name(model_name) 不崩溃
│    如果崩溃 → profiling ModelConfig 缺少新增的 BaseModelConfig 字段
│      修复: 在 __init__ 增加 **kwargs 兜底或显式添加参数
│
│  block_size 约束是否正确传播? [KP-0039]
│    验证: resolve_attention_block_size(backend, block_size) 返回 backend 兼容值
│    如果新 backend 有 block size 约束但 resolve 函数未处理 → 添加处理逻辑
│
┌─ 第一层: 自定义 Kernel 分析 (新增)
│
│  模型是否提供了 kernel.py 或自定义 kernel?
│    YES → 必须分析 kernel.py 内容，提取所有自定义 kernel:
│
│      ┌─ kernel 清单提取:
│      │  grep "def " kernel.py → 列出所有 kernel 函数
│      │  分类每个 kernel 的用途:
│      │    attention 相关: sparse_attn, flash_attn, ...
│      │    GEMM 相关:      fp8_gemm, fp4_gemm, ...
│      │    activation 相关: act_quant, fp4_act_quant, ...
│      │    其他:           hc_split_sinkhorn, ...
│      │
│      ├─ kernel 框架识别:
│      │  import tilelang  → TileLang (如 V4)
│      │  import triton    → Triton (如 FlashAttention)
│      │  torch.cuda       → 原生 CUDA
│      │  其他 → Novel
│      │
│      ├─ kernel 到 profiling backend 的映射:
│      │  自定义 attention kernel → 必须用它而非通用 backend 做 profiling
│      │    例: V4 sparse_attn (TileLang) ≠ flash_attention (Triton)
│      │    用通用 backend 近似 → 耗时严重不准
│      │  自定义 GEMM kernel → MLP profiling wrapper 需调用它
│      │    例: V4 fp4_gemm ≠ 标准 cuBLAS FP8 GEMM
│      │  其他 kernel → 评估是否影响关键路径耗时
│      │
│      └─ 为什么不能用通用 kernel 近似:
│         sparse_attn: 只计算 top-k KV，跳过其余 → 耗时远低于全序列 flash_attention
│         fp4_gemm: 4-bit 权重解包 + GEMM 融合 → 耗时不同于标准 FP8 GEMM
│         用错误的 kernel profiling → 仿真结果系统性偏差
│
│    NO → 使用通用 kernel (flash_attention, cuBLAS 等)

┌─ 第二层: Attention Backend 注册
│
│  需要新 backend?
│    YES → 仅在 attn_backend/ 操作:
│      1. types.py 新增 AttentionBackend 枚举值
│      2. <new>_wrapper.py 实现 BaseAttentionWrapper 接口
│         wrapper 内部应调用模型的官方 kernel (kernel.py)
│         而非用通用 kernel 近似
│      3. __init__.py 的 get_attention_wrapper() 工厂加分支
│    NO → 复用现有 backend

┌─ 第三层: 融合 Kernel 分析 [KP-0020]
│
│  Attention kernel 是否融合了多种不同类型的计算?
│    (检查: 一个 kernel call 是否同时处理多种 KV 来源)
│    YES → 异构融合 kernel:
│      作为整体 sequence-level op profile，不拆分
│      参数: (batch_size, total_kv_entries_attended)
│      例: V4 sparse_attn 融合 SWA + compressed + attn_sink
│        C4 decode: total = window(128) + topk(512) ≈ 640
│        C128 decode: total = window(128) + compressed_len/128
│        纯 SWA (ratio=0): total = window(128)
│      错误做法: 把 SWA attention 和 compressed attention 分别 profile 再累加
│    NO → 标准独立 kernel，每个 op 可分别 profile

│  模型的 attention kernel 融合是同构还是异构? [KP-0020]
│    同构融合 (多个相同类型 op → 一个大 kernel):
│      融合边界与 op 边界对齐 → 不影响 profiling 策略
│      例: Q+K+V 投影融合为一个 (dim, 3×head_dim) GEMM
│    异构融合 (不同类型 op → 一个 kernel):
│      op 边界消失 → 必须作为整体 profile
│      例: SWA + compressed + attn_sink 融合为一个 sparse_attn kernel

┌─ 第四层: 跨 Op 并行分析 [KP-0021]
│
│  同一层内是否有多个无数据依赖的 GEMM?
│    (检查: model.py forward 中是否有 CUDA stream 分配或多流并行)
│    YES → 跨 op 并行:
│      ExecutionPlan 中用 ParallelOpGroup 建模，取 max 而非 sum
│      例: V4 的 Q proj + Compressor + Indexer 在不同 stream 上并行
│      串行累加会高估 decode 耗时近 2 倍
│    NO → 标准串行 ExecutionPlan

┌─ 第五层: Token-level 额外算子识别与独立 Profiling
│
│  模型除了 attention 之外，是否有其他 token-level 算子?
│  (token-level 算子: 耗时 ∝ num_tokens，不依赖 kv_cache_size)
│
│  已知 token-level 算子清单:
│    ┌─────────────────┬──────────────────────────────────────────────────┐
│    │ 算子             │ 触发条件                                         │
│    ├─────────────────┼──────────────────────────────────────────────────┤
│    │ Linear 投影      │ 所有模型 (Q/KV/O 投影)                           │
│    │ (含低秩拆分)      │ 分组低秩 O 投影: o_groups > 1 [KP-0026]           │
│    │ Compressor       │ compress_ratios IS NOT NONE [KP-0022]             │
│    │ Indexer          │ index_topk 存在 [KP-0022]                         │
│    │ HC ops           │ hc_mult IS NOT NONE [KP-0025]                     │
│    │ MoE Gate (路由)  │ n_routed_experts > 0                              │
│    │ RMSNorm          │ 所有模型 (通常融合到投影中，不需独立 profile)       │
│    │ RoPE 应用        │ 有 RoPE 的模型 (通常融合到 attention 中)            │
│    │ Activation 量化  │ 有 FP4/FP8 量化路径 [KP-0024]                     │
│    │ (act_quant)      │ 通常融合在 GEMM kernel 中，不需独立 profile        │
│    │ FP4 simulation   │ compressor/indexer 中有 Q/DQ roundtrip [KP-0024]  │
│    │ (Q/DQ roundtrip) │ 作为 compressor/indexer profile 的一部分           │
│    └─────────────────┴──────────────────────────────────────────────────┘
│
│  关键原则:
│    大部分 token-level 算子都属于 mlp/ profiling 类别
│    但在 ExecutionPlan 中是独立的 op，不能合并到 attention op 的耗时中
│
│  kernel.py 中有非 attention 的关键 kernel?
│    YES → 评估是否需要独立的 profiling wrapper
│           例: fp4_gemm → MLP profiling wrapper 需调用它 (仍在 mlp/ 下)
│    NO → 标准 mlp/ profiling

┌─ 第六层: Profiling 归类充分性检查 [KP-0023]
│
│  检查: 新模型的所有 ops 是否可归入三个标准类别?
│    token-level (mlp/):   Q/KV/O 投影、Compressor、Indexer、HC、FFN
│    sequence-level (attention/): attention ops (含融合 kernel 整体)
│    communication (collectives/): all2all、all-reduce
│
│  全部可归类 → 三分类架构充分，不需要新增 profiling 类别
│  有 op 无法归类 → 分析原因:
│    是否因为融合/并行? → 在 simulator 层处理 (ParallelOpGroup/trigger)
│    是否真的有新维度? → 评估是否需要新增 profiling 类别 (极罕见)
```

### 自定义 Kernel 分析示例 — DeepSeek V4

```
kernel.py 清单:
  sparse_attn(h, d)      → C4 层的 sparse top-k attention (TileLang)
  fp8_gemm(N, K)         → 主权重的 FP8 GEMM (TileLang)
  fp4_gemm(N, K)         → Expert 权重的 FP4 GEMM (TileLang)
  fp4_act_quant()        → FP4 activation 量化
  act_quant()            → FP8 activation 量化
  hc_split_sinkhorn()    → mHC Sinkhorn 投影 (计算量小，影响低)

Profiling 映射:
  sparse_attn → 必须作为 SPARSE_ATTN_C4 backend 的 wrapper 内核
                 不能用 flashinfer/flash_attention 近似
                 [KP-0020] 异构融合 kernel，作为整体 sequence-level op profile
                 参数: (batch_size, total_kv_entries_attended)，不拆分
  fp4_gemm    → MLP profiling wrapper 需要支持 FP4 GEMM 路径
  fp8_gemm    → MLP profiling wrapper 需要支持 FP8 GEMM 路径
  hc_split_sinkhorn → 计算量占比较小，可合并到 HC op 耗时或独立 profiling

Profiling 约束:
  [KP-0020] sparse_attn 是异构融合 kernel:
    C4 decode: total_kv = window(128) + topk(512) ≈ 640
    C128 decode: total_kv = window(128) + compressed_len/128
    纯 SWA (ratio=0): total_kv = window(128)
    不尝试拆分为 SWA + compressed 分别测量
  [KP-0021] Q proj / Compressor / Indexer 在不同 CUDA stream 并行:
    ExecutionPlan 中这三个 op 用 ParallelOpGroup，取 max
    串行累加 decode 耗时高估 ~2 倍
  [KP-0023] V4 所有 ops 归入三类 (token-level/sequence-level/communication):
    不需要新增 profiling 类别
    融合/并行在 simulator 层的 ExecutionPlan 中处理
```

### KP 约束索引

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0006 | P1 | backend_extension | `需要新 attention backend` | kernel 调用只在 attn_backend/，不动 attention/ |
| KP-0007 | P2 | backend_extension | `新增 backend` | block_size 约束因 backend 而异，需在 resolve 中注册 |
| KP-0008 | P2 | extra_ops_profiling | `有 compressor/indexer` | compressor/indexer 需独立 profiling，按触发频率建模 |
| KP-0018 | P1 | moe_all2all | `n_routed_experts > 0` | MoE 模型必须注册 all-to-all profiling backend |
| KP-0020 | P0 | heterogeneous_fusion | `attention kernel 融合多种不同计算类型` | 融合 kernel 作为整体 sequence-level op profile，参数为 total_kv_entries_attended，不拆分 |
| KP-0021 | P0 | cross_op_parallelism | `同层有多个无依赖 GEMM 可并行` | ExecutionPlan 需 ParallelOpGroup 取 max 而非 sum，否则串行累加高估 ~2x |
| KP-0023 | P1 | profiling_architecture | `任何新模型的 profiling 归类检查` | 所有 ops 必须归入 token-level/sequence-level/communication 三类，不需要新增类别 |
| KP-0027 | P1 | op_list_verification | `op 清单生成后 (任何新模型)` | op 清单必须逐行代码级验证: hc_pre 等多子操作 op 不可合并、dtype 必须追溯到 default_dtype 推导、RMSNorm/RoPE/silu 等轻量 op 不可省略、ColumnParallel/RowParallel Shape 必须标注 per-rank |
| KP-0028 | P1 | shape_verification | `有 Compressor 子类实例 OR 有 flatten+GEMM 组合` | GEMM shape 必须从代码中 Linear 的 (in_features, out_features) 推导而非从高层理解猜测；Compressor 子类的 head_dim 可能与主注意力不同；flatten+GEMM 的 M 维是 b*s 不是 b*s*hc_mult |
| KP-0034 | P0 | head_dim_calculation | `head_dim != embedding_dim // num_q_heads` | get_head_size() 必须用显式 head_dim 字段，不能假设为 emb//q (影响 Gemma2, V4, V2 等) |
| KP-0035 | P1 | config_field_sync | `新增 BaseModelConfig 字段` | profiling ModelConfig 必须接受所有 BaseModelConfig 字段 (用 **kwargs 兜底) |
| KP-0039 | P2 | kernel_block_size | `新增 attention backend` | resolve_attention_block_size() 必须处理新 backend 的 block size 约束 (如 TileLang 要求 64 倍数) |
| KP-0043 | P2 | csv_column_silence | `execution plan 中新增 ElementWiseOp 但 CSV 无对应列` | NonAttention.load_df 对缺失列填 0 不报警；设计如此（时间含在融合 kernel 中），但可能误导新集成者 |
| KP-0044 | P2 | serving_vs_simulation_ops | `参考模型代码中出现仅 serving 时需要的 op` | 参考模型中的 op 分三类: (A) 需显式 profiling (B) 隐含在融合 kernel 中 (C) serving-only；只有 (A)(B) 影响仿真 |
| KP-0032 | P1 | v4_ep_profiling_params | `Profiling V4 模型时使用 V3 参数` | V4 参数变化 (384 experts, top-6, intermediate=3072, FP8) 要求重新 profiling；V3 数据偏差约 50% |
| KP-0037 | P1 | fused_moe_class_attribute | `运行任何 MoE profiling` | FusedMoE 引用 self.ALL_MOE_TYPES 但未定义，导致 MoE profiling 即刻 AttributeError |
| KP-0047 | P0 | profiling_op_completeness | `任何新模型的 execution plan 生成后` | 2.1 Op 清单中的每个 op 必须在 2.3 Profiling 计划中有对应条目；execution_time_predictor 按 op name 查表，缺失则静默填 0 |
| KP-0048 | P0 | vllm_fused_op_alignment | `任何新模型的集成计划生成后` | Plan op 粒度必须对齐 vLLM 融合 kernel 边界，不能按 reference model 逐行展开；vLLM 融合多个 op 为单 kernel 时，plan 中应对应 1 个 op |
| KP-0049 | P1 | composite_pipeline_profiling | `模型有条件触发的管线 op 或 per-layer kernel 变体` | 复合管线 op (compressor_fused_pipeline, indexer_full_pipeline) 和条件 kernel 变体 (HC 首/非首层) 需独立 profiling，不能用子组件 profile 求和替代 |
| KP-0050 | P0 | plan_parameter_verification | `任何新模型的集成计划生成后` | Plan 中每个 op 的 Shape/元素数/维度顺序必须从 vLLM 源码精确推导并交叉校验，不能靠人工估算；条件因子 (coff/overlap) 必须显式追踪 |
| KP-0051 | P0 | model_level_ops_coverage | `模型的 forward() 中存在 per-layer loop 之外的实质性计算 op` | Plan 必须覆盖 Model.forward() 中 per-layer loop 前/后的 op（如 hc_head, final_norm），不能只追踪 DecoderLayer |
| KP-0052 | P1 | profiler_wall_perturbation | `使用 torch profiler trace 解释 serving E2E 误差` | 非 profiler vLLM bench 才是端到端 ground truth；torch profiler wall 只能用于 kernel 归因，并必须报告 perturbation |
| KP-0053 | P0 | trace_op_occurrence_preservation | `从 chrome_trace.json 或 execution_time.to_dict() 做逐 op 聚合` | Trace 导出必须保留重复 op occurrence；按 op name 的 dict 会覆盖同名 op，不能作为 per-op 总量真值 |
| KP-0054 | P0 | rank_sum_vs_wall_time_semantics | `tensor_parallel_size > 1 AND profiling 数据进入 execution predictor` | Multi-rank profiling 必须区分 per-rank GPU self sum 与 group critical-path wall；simulation 默认消费 critical-path wall |
| KP-0055 | P1 | single_request_steady_state_validation | `端到端误差异常 AND 目标是算子集成精度` | 先用 serial single-concurrency 的 warmed request 验证算子集成，再分析多请求调度误差 |
| KP-0056 | P1 | profiling_data_vs_e2e_scope | `profile_audit complete AND 指标包含 client TTFT/TPOT/E2E` | GPU/model op profiling 完备不等于 client E2E 模型完备；CPU/serving overhead 缺失必须在报告中明示 |
| KP-0057 | P2 | config_enum_type_preservation | `从 YAML/JSON 手工重建 dataclass config` | Config 重建必须保留 post-init Enum 类型语义，不能把 all2all_backend 等字段覆盖为裸字符串 |

---


## Step 8: 生成模型集成计划

8 维度分析完成后，将分析结果结构化为一个**可执行的模型集成计划文件**，输出到 `docs/<model-name>-integration-plan.md`。

### 8.1 计划文件模板

**设计原则**: 计划的核心产出是 **Op 清单与 Profiling 计划**（第 2 节）。8 个维度的分析结果都应汇聚到这里，转化为可直接指导实现的算子级信息。Simulator 对精度极度敏感，每个算子的 profile 参数空间必须在计划中精确定义。

> # <Model Name> 集成计划
>
> ## 0. 基本信息
>
> - 生成时间: YYYY-MM-DD
> - 输入: config.json (来源) + model.py (来源) [+ kernel.py (来源)]
> - 框架版本: 当前 git commit
> - 相似度报告: 最接近模型 <model> (匹配度 XX%)
> - 增量模式: 基于 <base-model> (如适用)
>
> ## 1. 新颖度报告
>
> (从 Step 0.X 输出，列出 Novel 维度和处理方式)
>
> ## 2. Op 清单与 Execution Plan
>
> ### 2.1 Per-Layer Op 清单
>
> (从维度 2/3/5/6 分析中综合。**Op 粒度必须对齐 vLLM 融合 kernel 边界** (KP-0048)，不能按 reference model 逐行展开。)
> (**使用标准 8 列格式** (KP-0047): Op | Profile | Shape | Kernel | 量化 | Prefill | Decode | 框架基类)
>
> | Op | Profile | Shape | Kernel | 量化 | Prefill | Decode | 框架基类 |
> |----|---------|-------|--------|------|---------|--------|----------|
> | ... | ... | ... | ... | ... | ... | ... | ... |
> | hc_pre_attn | token | HC pre-mixing (GEMM+Sinkhorn) | M=tokens, K=hc_mult×dim, N=mix_hc | mlp/ | bf16 (FP32 compute) | FP32 W | — | 每层 |
> | hc_post_attn | token | HC post-expansion | elem=tokens × hc_mult × dim | mlp/ | bf16 elemwise | — | — | 每层 |
> | hc_pre_ffn | token | HC pre-mixing (同 hc_pre_attn) | M=tokens, K=hc_mult×dim, N=mix_hc | mlp/ | bf16 (FP32 compute) | FP32 W | — | 每层 |
> | hc_post_ffn | token | HC post-expansion (同 hc_post_attn) | elem=tokens × hc_mult × dim | mlp/ | bf16 elemwise | — | — | 每层 |
> | gate | token | MoE 路由 | M=tokens, K=dim, N=n_routed | mlp/ | bf16 (FP32 compute) | BF16 | n_routed > 0 | 每层 |
> | expert_w1 | token | Expert gate 投影 (FP4) | M=tokens, K=dim, N=moe_inter | mlp/ | fp4_gemm | FP4 W + FP8 act | routed expert | 每层 |
> | expert_w3 | token | Expert up 投影 (FP4) | M=tokens, K=dim, N=moe_inter | mlp/ | fp4_gemm | FP4 W + FP8 act | routed expert | 每层 |
> | expert_w2 | token | Expert down 投影 (FP4) | M=tokens, K=moe_inter, N=dim | mlp/ | fp4_gemm | FP4 W + FP8 act | routed expert | 每层 |
> | shared_w1 | token | Shared expert gate 投影 | M=tokens, K=dim, N=moe_inter | mlp/ | bf16 | BF16 | n_shared > 0 | 每层 |
> | shared_w3 | token | Shared expert up 投影 | M=tokens, K=dim, N=moe_inter | mlp/ | bf16 | BF16 | n_shared > 0 | 每层 |
> | shared_w2 | token | Shared expert down 投影 | M=tokens, K=moe_inter, N=dim | mlp/ | bf16 | BF16 | n_shared > 0 | 每层 |
> | all2all | comm | Expert 并行 all-to-all | msg=token×dim×topk | collectives/ | nccl all2all | — | EP > 1 | 每层 |
> | all_reduce | comm | TP all-reduce (wo_b 后) | msg=tokens×dim | collectives/ | nccl all_reduce | — | TP > 1 | 每层 |
>
> ### 2.1.N Model-Level Ops [KP-0051]
>
> (从 Step 0.V Phase 2.3 的 Model-Level 执行流产出。列出 Model.forward() 中 per-layer loop 前/后的 op。)
> (判断标准: 有 GEMM / CUDA custom kernel / Triton kernel → 必须列出；轻量 op 也需列出 [KP-0047])
>
> | Op | Profile | Shape | Kernel | 量化 | Prefill | Decode | 框架基类 |
> |----|---------|-------|--------|------|---------|--------|----------|
> | hc_head | mlp/ | [M, hc_mult, dim] → [M, dim]; fn [hc_mult, hc_dim] | CUDA custom (HCHeadOp) | FP32 W | M=prefill_tokens | M=batch_size | ElementWiseOp |
> | final_norm | mlp/ | elem = M x dim | RMSNorm | -- | M=prefill_tokens | M=batch_size | RMSNorm |
> ### 2.2 融合与并行关系
>
> (从维度 8 第三/四层决策树输出)
>
> 融合组 (多个逻辑 op = 1 个 kernel call):
>   sparse_attn = {swa_attention, compressed_attention, attn_sink_correction}
>     → 实际执行: 1 个 sparse_attn kernel call
>     → Profile 参数: (batch_size, total_kv_entries_attended)
>     → C4 decode: total = window(128) + topk(512) ≈ 640
>     → C128 decode: total = window(128) + compressed_len/128
>     → 纯 SWA: 此融合不触发，用标准 swa_attn
>     → [KP-0020] 不能拆分为独立 op 分别 profile
>
> 并行组 (同层多个无依赖 op 可同时执行):
>   ParallelGroup_decode = {attn_wq_a, compressor, indexer}
>     → 条件: 仅 decode 阶段 (prefill 时数据依赖不同)
>     → 可在不同 CUDA stream 并行
>     → ExecutionPlan 取 max，不是 sum
>     → [KP-0021] 串行累加高估 decode 耗时近 2 倍
>
> ### 2.3 Profiling 计划
>
> (按 profiling 子系统组织。每个子系统列出: 扫描参数空间、kernel、独立数据组)
>
> #### attention/ (sequence-level)
>
> | Profile 名称 | Kernel | 参数空间 | 条件 |
> |-------------|--------|----------|------|
> | sparse_attn_C4 | sparse_attn (TileLang) | batch × total_kv × is_prefill × tp | compress_ratio=4 |
> | sparse_attn_C128 | sparse_attn (TileLang) | batch × total_kv × is_prefill × tp | compress_ratio=128 |
> | swa_attn | flash_attention | batch × kv_cache_size × is_prefill × tp | compress_ratio=0 |
>
> total_kv 计算公式:
>   Prefill: total = window + seq_len / ratio
>   Decode:  total = window + topk(C4) 或 window + compressed_len/ratio(C128) 或 window(SWA)
>
> #### mlp/ (token-level)
>
> | Profile 名称 | Kernel | Shape (N, K) | 独立数据组 | 条件 |
> |-------------|--------|-------------|-----------|------|
> | fp4_expert_gemm | fp4_gemm (TileLang) | (moe_inter, dim) 和 (dim, moe_inter) | FP4 | routed expert |
> | bf16_shared_gemm | cuBLAS | (moe_inter, dim) 和 (dim, moe_inter) | BF16 | shared expert |
> | fp8_attn_proj | fp8_gemm (TileLang) | (q_lora, dim), (heads×head_dim, q_lora), (head_dim, dim), ... | FP8 | attention |
> | bf16_wo_a | bf16 einsum | (hpg×head_dim, o_lora) × n_groups | BF16 | — |
> | hc_gemm | cuBLAS (FP32 compute) | (mix_hc, hc_mult×dim) | FP32 | — |
> | kv_sim_quant | act_quant | (tokens, nope_dim) | FP8 sim | 每层 |
> | fp4_sim_quant | fp4_act_quant | (tokens, head_dim) | FP4 sim | C4 层 |
>
> 扫描维度: num_tokens × tp (对所有 mlp profile)
>
> #### collectives/ (communication)
>
> | Profile 名称 | 类型 | 参数空间 | 条件 |
> |-------------|------|----------|------|
> | all2all | nccl | message_size × world_size | EP > 1 |
> | all_reduce | nccl | message_size × world_size | TP > 1 |
>
> ### 2.4 KV Cache 架构
>
> (从维度 4 分析输出)
>
> | Group 名称 | KV 类型 | 增长模型 | 上限 | Block Size | Prefix Cache | 层数 |
> |-----------|--------|---------|------|-----------|-------------|------|
> | SWA | 原始 KV (未压缩) | 线性增长，窗口内滚动 | window_size / block_size | 按 backend 约束 | ✗ (滚动淘汰) | 全部 |
> | Compressed_C4 | 压缩 KV (ratio=4) | seq_len / ratio | 无硬上限 | 按 backend 约束 | ✓ (持久) | 40 |
> | Compressed_C128 | 压缩 KV (ratio=128) | seq_len / ratio | 无硬上限 | 按 backend 约束 | ✓ (持久) | 10 |
>
> Memory Planner 适配:
>   per-entry KV 字节数: head_dim × dtype_size (SWA) vs head_dim / ratio × dtype_size (compressed)
>   [KP-0003] 压缩组的 per-token KV 计算必须除以 ratio
>
> ### 2.5 Profiling 可行性分析
>
> (基于 2.1 的 Op 清单，逐一检验框架隐含假设。识别会在 profiling 或仿真中"断"的位置)
>
> 框架隐含假设 (Llama3-8B 等标准模型满足全部):
>   1. op = kernel call (1:1 映射，op 可独立 profile)
>   2. op 之间串行执行 (耗时 Σ 累加)
>   3. 每个 op 每次 batch stage 都执行
>
> 本模型对三个假设的检验:
>
> | Op / Op 组 | 假设 1: 可独立 profile? | 假设 2: 串行执行? | 假设 3: 每次都执行? | 影响 |
> |-----------|------------------------|-------------------|-------------------|------|
> | sparse_attn (SWA+compressed+sink) | ✗ 异构融合，1 kernel 对应多个逻辑 op | ✓ | ✓ | 需作为整体 profile，参数用 total_kv [KP-0020] |
> | Q_proj + Compressor + Indexer (decode) | ✓ | ✗ 多 stream 并行 | — | 需 ParallelOpGroup，取 max [KP-0021] |
> | Compressor (decode) | ✓ | ✓ | ✗ 每 ratio 步触发 1 次 | 需 trigger_frequency 属性 |
> | 其他 token-level ops | ✓ | ✓ | ✓ | 标准处理 |
>
> 框架扩展需求汇总 (与 Section 3 对应):
>   - 融合 op 类型: <需要/不需要> — 原因: ...
>   - ParallelOpGroup: <需要/不需要> — 原因: ...
>   - 条件触发: <需要/不需要> — 原因: ...
>   - 新 Profiling Backend: <需要/不需要> — 原因: ...
>
> 对比参考: Llama3-8B 的融合是同构的 (多个小 GEMM → 1 个大 GEMM，op 边界不变)，
>           本模型的融合是异构的 (不同类型 op → 1 个 kernel，op 边界消失)。
>           详见 docs/v4_profiling_challenges.md
>
> ## 3. Simulator 扩展需求
>
> (将维度 1/2/3/4/6/7 的分析转化为具体代码改动。以下扩展需求来自 2.5 可行性分析)
>
> ### 3.1 ModelConfig (维度 1)
>
> 新建/修改: <BaseModelConfig 子类名>
> 新增字段: <列出新增字段>
> Flag: is_moe_model() / is_mla_model() / is_sparse_backend_model() 的返回值
>
> ### 3.2 AttentionModel (维度 2)
>
> 选择/新建: <AttentionModel 子类名>
> Execution Plan op 序列:
>   per-layer ops = [hc_pre_attn, attn_wq_a, attn_wkv, compressor?, indexer?,
>                    sparse_attn/swa_attn, attn_wo_a, attn_wo_b, hc_post_attn]
>   融合/并行关系: 见 2.2
>
> ### 3.3 FFNModel (维度 3)
>
> 选择/新建: <FFN 子类名>
> Execution Plan op 序列:
>   per-layer ops = [hc_pre_ffn, gate, expert_w1, expert_w3, expert_w2,
>                    shared_w1, shared_w3, shared_w2, all2all?, all_reduce?, hc_post_ffn]
>
> ### 3.4 Memory Planner (维度 4)
>
> 适配: <KV cache group 配置>
> per-group 参数: KV type, block_size, max_blocks, growth_rate
>
> ### 3.5 Scheduler 扩展 (维度 7)
>
> Decode 加速策略: <None / MTP / ...>
> 影响: decode step 的 execution plan 是否包含额外 ops
>
> ## 4. 特征向量与参数验证
>
> ### 4.1 特征向量
>
> (从 Step 0 提取的完整特征向量)
>
> ### 4.2 参数量验证
>
> | 组件 | 自动计算 | 官方数据 | 偏差率 |
> |------|---------|---------|--------|
> | Embedding | ... | ... | ... |
> | Attention/layer | ... | ... | ... |
> | Compressor/layer | ... | ... | ... |
> | HC/layer | ... | ... | ... |
> | FFN(Expert)/layer | ... | ... | ... |
> | FFN(Shared)/layer | ... | ... | ... |
> | 总计 | ... | ... | ... |
>
> (从 Step 0.5 输出。偏差 > 5% 标红警告)
>
> ## 5. KP 命中矩阵
>
> | KP | 标题 | 严重度 | 命中? | 触发条件 | 处理方式 | 验证方法 |
> |----|------|--------|-------|----------|----------|----------|
> | KP-0001 | 异构 KV Cache 分组 | P0 | ✓ | compress_ratios 存在 | 2 group: SWA + Compressed | 检查 KVCacheGroupSpec 数量 |
> | KP-0009 | 每层 SWA+compressed 组合 | P0 | ✓ | compress_ratios 存在 | 每层都是组合，非互斥 | 检查 execution plan |
> | ... | ... | ... | ... | ... | ... | ... |
> | KP-XXXX | ... | P? | ✗ | 不命中 | — | — |
>
> (列出全部 KP，命中和未命中的都列出)
>
> ## 6. 文件改动汇总
>
> | 类别 | 新建文件 | 修改文件 | 不变文件 |
> |------|----------|----------|----------|
> | ModelConfig | — | ontos/config/model_config.py | — |
> | AttentionModel | ontos/execution_time_predictor/models/deepseek_v4.py | — | — |
> | Attention Backend | ontos/profiling/attn_backend/sparse_attn_wrapper.py | ontos/profiling/attn_backend/types.py | — |
> | MLP Profiling | — | ontos/profiling/mlp/ | — |
> | Execution Plan | — | ontos/entities/batch_stage.py | — |
> | ... | ... | ... | ... |
>
> ## 7. 实现任务分解与依赖
>
> ### Phase 1: 基础注册 (无依赖)
> - Task 1.1: 新建 ModelConfig 子类 (维度 1)
> - Task 1.2: 验证参数量 (Step 0.5)
>
> ### Phase 2: Execution Plan 定义 (依赖 Phase 1)
> - Task 2.1: 新建/选择 AttentionModel 子类，定义 op 序列 (维度 2 + 2.2 融合/并行)
> - Task 2.2: 选择/扩展 FFNModel，定义 op 序列 (维度 3)
> - Task 2.3: 配置 KV cache groups 和 memory planner (维度 4 + 2.4)
> - Task 2.4: 配置量化路径选择逻辑 (维度 5)
>
> ### Phase 3: Profiling 配置与执行 (依赖 Phase 2)
> - Task 3.1: 配置 attention profiling (2.3 attention/ 表)
> - Task 3.2: 配置 mlp profiling (2.3 mlp/ 表)
> - Task 3.3: 配置 collectives profiling (2.3 collectives/ 表)
>
> ### Phase 4: Simulator 集成 (依赖 Phase 2, Phase 3)
> - Task 4.1: 集成 execution plan 到 simulator
> - Task 4.2: 配置 scheduler (维度 7)
>
> ### Phase 5: 端到端验证 (依赖全部)
> - Task 5.1: KP 验证清单 (从第 5 节 KP 命中矩阵的验证方法)
> - Task 5.2: 回归测试 (确保已有模型不受影响)
>
> 并行机会: Phase 2 的 Task 2.1/2.2/2.4 可并行; Phase 3 的 Task 3.1/3.2/3.3 可并行
