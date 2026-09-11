# Attention And KV Details

This file is mechanically extracted from the preserved legacy skill copy. Keep updates in active split skills and KP files unless intentionally refreshing legacy-derived details.

## 维度 2: 注意力架构

当前 KP 库中覆盖最密集的维度（KP-0006~0019）。注意力是模型集成中最复杂、最容易出错的维度。

### 探测问题

| # | 问题 | 提取来源 | 判定逻辑 |
|---|------|----------|----------|
| 2.1 | Q/KV 头数比例？ | `num_attention_heads` vs `num_key_value_heads` | 见决策树 |
| 2.2 | KV 投影路径？ | model.py 的 proj 定义 | 直接投影 / 两阶段 / 其他 |
| 2.3 | 是否有 per-layer attention 变化？ | `compress_ratios` 或 model.py 的 per-layer config | 是否存在逐层差异 |
| 2.4 | 是否有滑动窗口？ | `sliding_window` 或 `window_size` | 直接读取 |
| 2.5 | O 投影结构？ | model.py 的 o_proj / wo_a / wo_b | 标准 / grouped low-rank |
| 2.6 | 每层的 attention 是单一路径还是组合？ | model.py forward 逻辑 | 单一 / SWA+Extra 组合 |
| 2.7 | 压缩注意力的选择策略？ | model.py / config | Sparse top-k / Dense / 无 |
| 2.8 | 压缩是否有重叠感受野？ | stride vs receptive_field | stride < field 即重叠 |
| 2.9 | 位置编码方案？ | `rope_scaling` / model.py | 标准 RoPE / YaRN / ALiBi / 其他 |
| 2.10 | 逐层窗口大小是否交替变化？ | config 中 sliding_window/window_size 是数组还是标量 | 数组 → SWA_Alternating (如 Gemma-2/3) |
| 2.V | vLLM Dossier: attention 相关 op 的融合/并行/kernel 对齐？ | Dossier A+B+C | 检查 attention op 是否被融合、是否并行执行、kernel 是否与 profiling wrapper 一致 |

### 决策树

```
┌─ 第一层: KV 投影路径分类
│
├─ 有 kv_lora_rank (两阶段潜在投影)?
│   YES → MLA 类型
│         kv_a(dim → kv_lora_rank) → kv_b(kv_lora_rank → heads)
│
├─ num_kv_heads == num_q_heads?
│   YES → MHA (标准多头)
│
├─ num_kv_heads > 1 且 < num_q_heads?
│   YES → GQA (分组查询) [KP-0015]
│
├─ num_kv_heads == 1 且无 kv_lora_rank?
│   YES → MQA (单 KV 头直接投影)
│         有 compress_ratios? [KP-0038]
│           YES → MQA + Sparse (如 V4 C4/C128)
│                 注意: 这是非 MLA sparse，backend 不能用 FLASHMLA_SPARSE
│                 正确 backend: SPARSE_ATTN_C4 / SPARSE_ATTN_C128
│           NO → 标准 MQA
│
├─ model.py 中无标准 Q/K/V 投影 (如 linear attention / SSM)?
│   YES → Novel → 进入未知创新处理协议
│
└─ 其他 → Novel → 进入未知创新处理协议

┌─ 第一层补充: O 投影结构 [KP-0026]
│
├─ o_groups > 1 AND o_lora_rank IS NOT NONE?
│   YES → 分组低秩 O 投影 (如 V4):
│         wo_a: batched einsum, [heads_per_group * head_dim, o_lora_rank] × o_groups
│         wo_b: 标准 GEMM, [o_groups * o_lora_rank, hidden]
│         TP 约束: o_groups % tp_size == 0
│         参数量: o_groups × heads_per_group × head_dim × o_lora_rank + o_groups × o_lora_rank × hidden
│         ≠ 标准 O 投影，不能合并为单个 GEMM
│   NO → 标准 O 投影 (o_proj: [num_heads * head_dim, hidden])

┌─ 第二层: 逐层注意力组合模式
│
├─ compress_ratios 存在?
│   YES → Hybrid 组合注意力 (每层 SWA + compressed)
│         ratio=0 → 纯 SWA 层
│         ratio=N>0 → SWA + compressed 层
│         [KP-0009] 每层都是组合，不是互斥的层类型!
│   NO → sliding_window 是数组 (逐层不同)?
│     YES → SWA_Alternating (如 Gemma-2/3: 奇数层 SWA，偶数层 Full)
│           [KP-0017] KV cache 有上限层和无限层，需分组
│   NO → sliding_window 是标量?
│     YES → 全层 SWA
│     NO → 标准全层 attention

┌─ 第三层: 压缩 attention 选择策略 (仅 compress_ratios 存在时)
│
├─ 有 index_topk 或 top_k 参数?
│   YES → Sparse top-k 选择 [KP-0011]
│         attention 计算量 ∝ top_k, 与 compressed_kv_len 几乎无关
│
│         Indexer 内部是否有独立的 Compressor? [KP-0022]
│           检查: model.py 中 Compressor 实例化数量 > 1?
│           YES → 双 Compressor: attention 的 Compressor + indexer 内部的 Compressor
│                 indexer profile 必须包含其内部 compressor 的完整流程耗时
│                 两个 Compressor 参数可能不同 (如 rotate=True/False)
│           NO → 单 Compressor，标准处理
│
│   NO → Dense attention [KP-0011]
│         attention 计算量 ∝ compressed_kv_len
│
│   stride < receptive_field? [KP-0010]
│     YES → 重叠感受野 (如 C4: stride=4, field=8)
│           entries 数量仍由 stride 决定
│           需 overlap buffer
│     NO → 无重叠 (如 C128: stride=field=128)

┌─ 第四层: 位置编码 (影响 attention 维度计算)
│
├─ rope_scaling 存在?
│   YES → YaRN / NTK-aware / Dynamic NTK → [KP-0019] 检查是否影响 head_dim 拆分
│   NO → 标准 RoPE (rope 部分 = qk_rope_head_dim)
│
│   ALiBi? → 无 RoPE 维度，位置信息通过 bias 注入
│   无位置编码? → 仅限特定模型
│   其他 → Novel → 进入未知创新处理协议
```

### KP 约束索引

运行时: 对每个 KP，用 Step 0 的特征向量评估触发条件。命中的 KP 读取其文件完整内容，提取约束定义、评估方法、常见错误。

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0009 | P0 | per_layer_attn_variation | `compress_ratios IS NOT NONE` | 每层是 SWA+compressed 组合，不是互斥层类型 |
| KP-0010 | P1 | compressed_attn | `stride < receptive_field` | 压缩有重叠感受野，entries 数由 stride 决定 |
| KP-0011 | P1 | compressed_attn | 有 per-layer 选择策略差异 | C4 sparse top-k vs C128 dense 是根本不同的策略，必须独立 op |
| KP-0006 | P1 | backend_extension | 需要新 attention backend | kernel 调用只在 attn_backend/，不动 attention/ |
| KP-0012 | P2 | mqa_kv_dim | `num_kv_heads==1 AND kv_lora_rank IS NONE` | MQA 的 KV 维度是 head_dim 不是 kv_lora_rank |
| KP-0007 | P2 | backend_extension | 新增 backend | block_size 约束因 backend 而异，需在 resolve 中注册 |
| KP-0008 | P2 | extra_ops_profiling | 有 compressor/indexer | compressor/indexer 需独立 profiling，按触发频率建模 |
| KP-0015 | P1 | gqa_head_divisibility | `num_kv_heads > 1 AND num_kv_heads < num_q_heads` | GQA 的 num_q_heads 必须被 num_kv_heads 整除 |
| KP-0017 | P1 | swa_bounded_kv | `sliding_window IS NOT NONE` | SWA 的 KV cache 有硬上限，memory planner 需感知 |
| KP-0019 | P2 | rope_dimension | `rope_scaling IS NOT NONE OR rope_theta != 10000.0` | 非标准 RoPE 配置可能需要 attention backend 特殊处理 |
| KP-0020 | P0 | heterogeneous_fusion | `compress_ratios IS NOT NONE AND 有融合 kernel` | 异构融合 kernel (SWA+compressed+sink合一) 作为整体 sequence-level op profile，不拆分 |
| KP-0021 | P0 | cross_op_parallelism | `同层有多个无依赖 GEMM` | Q proj/Compressor/Indexer 可并行执行，ExecutionPlan 需 ParallelOpGroup 取 max 而非 sum |
| KP-0022 | P1 | dual_compressor | `有 Indexer (compress_ratio==4 且有 index_topk)` | C4 层有两个独立 Compressor (attention 用 + indexer 内部用)，indexer profile 必须含内部 compressor |
| KP-0026 | P1 | grouped_lowrank_o_proj | `o_groups > 1 AND o_lora_rank IS NOT NONE` | O 投影拆为 wo_a batched einsum + wo_b GEMM 两步，TP 约束 o_groups % tp_size == 0 |
| KP-0033 | P0 | mla_detection | `qk_nope_head_dim IS NOT NONE AND kv_lora_rank IS NONE` | is_mla_model() 必须基于 kv_lora_rank，不是 RoPE 维度拆分；MQA with RoPE split 不是 MLA |
| KP-0038 | P1 | sparse_backend_selection | `is_sparse_backend_model() AND NOT is_mla_model()` | sparse attention 有 MLA-sparse 和 MQA-sparse 子类，backend 选择必须区分 |
| KP-0042 | P1 | per_layer_attn_mechanism | `新模型有非 SWA/full 交替的逐层注意力变化` | `attn_layer_patterns` 不能表达复杂逐层行为（C4/C128/SWA），必须用模型特定元组（如 `compress_ratios`） |
| KP-0045 | P2 | naming_convention_map | `新模型复用已有术语但语义不同` | vLLM 外部名称与 ontos 内部名称是两套命名系统，必须建立映射表；V4 "FlashMLA" 指的是 kernel 库名而非 MLA 机制 |

#### 通用约束（框架内置，非 KP）

**新注意力类型判定**: KV 投影路径本质不同时，新建 AttentionModel 子类，不得在现有类中加 `if model_type` 分支。评估: 新类的 `_get_param_size_impl()` 不含 model-specific 分支。

**MQA/GQA batching overhead**: num_q > num_kv 时基类已自动处理。评估: `get_execution_plan()` 不含自定义 batching overhead op。

### 参数量计算模板

```
MHA:  q_proj(q×h×d) + k_proj(kv×h×d) + v_proj(kv×h×d) + o_proj(q×h×d)
GQA:  同 MHA，但 kv < q
MLA:  q_a(d×q_lora) + q_b(q_lora→q×(nope+rope)) + kv_a(d×kv_lora) + kv_b(kv_lora→(nope+v)) + o_proj
MQA:  q_proj(q×h×d) + wkv(d→head_dim) + wo_a(head_dim→groups×o_lora) + wo_b(groups×o_lora→d)
```

**必须**使用 Step 0.5 的自动计算，并与模型官方公布的参数量交叉验证。当自动计算结果偏差 > 5% 时，手工排查。

---


## 维度 4: KV Cache 管理

核心问题: **模型有几种不同的 KV cache 结构？**

### 探测问题

| # | 问题 | 提取来源 | 判定逻辑 |
|---|------|----------|----------|
| 4.1 | 所有层的 KV cache 结构是否相同？ | compress_ratios / sliding_window / window_size | 有逐层差异即异构 |
| 4.2 | KV cache 条目大小是否均匀？ | 各层的 KV head 维度 | 按 layer 检查 |
| 4.3 | KV cache 增长是否线性？ | 是否有固定窗口 / 压缩比 | 线性 / 有上限 / 压缩增长 |
| 4.4 | 各 group 是否支持 prefix caching？ | per-group: KV 是否持久且确定性 | 滚动淘汰的组不行，持久累积的组可以 (见下方说明) |
| 4.V | vLLM Dossier: kv_cache_dtype 约束、block_size 硬编码值？ | Dossier C+D | 检查 KV cache dtype 强制要求、block_size 是否与 profiling 参数一致 |

### 决策树

```
所有层 KV cache 相同?
  YES → 1 group
    有 sliding_window?
      YES → 1 group with SWA [KP-0017]
      NO → 1 group, uniform (标准模型)
  NO → 按结构差异分组
    差异来源?
      compress_ratios → 2 group: SWA(ratio=0) + Compressed(ratio>0)
      sliding_window 数组 → 2 group: SWA + Full (如 Gemma-2/3)
      其他 → Novel → 进入未知创新处理协议

注意: SSM / Linear Attention 模型可能无 KV cache → kv_cache_type = None
```

**原则: 组数越少越好。** 模拟器不需要逐层精度，只需要在统计意义上准确。

### Prefix Caching 兼容性 (Per-Group 判定)

Prefix caching 的兼容性应 **per-group 判定**，不是全局一刀切：

```
对每个 KV cache group，检查:
  1. KV 是否持久 (非滚动淘汰)? → SWA 组: 滚动淘汰，不支持; Full/Compressed 组: 持久，支持
  2. KV 是否确定性 (相同 input → 相同 KV)? → 标准 attention: 确定; Compressed: 确定 (同一 compressor)

判定结果:
  ┌─────────────────────────┬──────────────┬─────────────────────────┐
  │ Group 类型              │ Prefix Cache │ 原因                    │
  ├─────────────────────────┼──────────────┼─────────────────────────┤
  │ Full attention          │ ✓ 可以       │ KV 持久，确定性         │
  │ SWA (sliding window)    │ ✗ 不行       │ KV 滚动淘汰，prefix 可能已被驱逐 │
  │ Compressed (CSA/C4)     │ ✓ 可以       │ 压缩 KV 持久累积，同一 compressor 确定性压缩 │
  │ Compressed (HCA/C128)   │ ✓ 可以       │ 同上                    │
  └─────────────────────────┴──────────────┴─────────────────────────┘

示例 — DeepSeek-V4:
  SWA group (window=128):           不支持 prefix caching
  Compressed group (C4 + C128):     可以支持 prefix caching
  → 框架现状: 全局 _caching_active，多 group 时直接禁用 (保守策略)
  → 正确做法: 应 per-group 支持，compressed group 可缓存

示例 — Gemma-2/3:
  Full attention group:             可以 prefix caching
  SWA group:                        不可以
  → 框架现状: 同上，全局禁用
  → 正确做法: Full group 可缓存
```

### KP 约束索引

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0001 | P0 | heterogeneous_kv | `compress_ratios 存在且不全相同` | 异构 KV cache 需要分组建模，KVCacheGroupSpec 增加 compression_ratio |
| KP-0003 | P0 | compression_memory | `有 KV 压缩机制` | Memory Planner per-token KV 计算必须适配压缩比 |
| KP-0002 | P1 | prefix_caching_conflict | `>1 个 KV cache group OR 有 sliding_window` | 框架现状: 全局 _caching_active 禁用; 正确做法: per-group 判定，持久 KV 的 group 仍可 prefix cache |
| KP-0013 | P2 | prefix_caching_bounded | `多 group 且有共享 prefix 场景` | prefix caching 可用 bounded recompute 模型，不需完整 ShadowRadix |
| KP-0004 | P2 | kv_growth_rate | `有滑动窗口和/或压缩 KV cache` | SWA 有上限，compressed 按压缩比增长，增长速率不同 |
| KP-0005 | P2 | swa_memory_bottleneck | `有 SWA 且高并发` | SWA 可能成为内存瓶颈，per-entry 用原始（未压缩）KV 大小 |
| KP-0046 | P0 | kv_cache_dtype_hard_requirement | `model_type == "deepseek_v4"` | V4 强制 FP8 KV cache (vLLM assert)，block_size=64 仅影响 SWA，Full MLA block_size=256 由 vLLM 内部硬编码 |

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
