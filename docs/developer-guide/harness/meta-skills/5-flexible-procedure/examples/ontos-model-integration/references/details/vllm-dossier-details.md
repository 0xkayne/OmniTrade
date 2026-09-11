# vLLM Dossier Details

This file is mechanically extracted from the preserved legacy skill copy. Keep updates in active split skills and KP files unless intentionally refreshing legacy-derived details.

## Step 0.V: vLLM 实现分析

### 为什么需要这一步

Ontos 仿真器的最终对齐标准是 **vLLM 端到端实测指标**，而不是 HuggingFace reference model。两者的实现存在系统性差异：

| 差异类型 | Reference Model 可能的样子 | vLLM 可能的实际行为 | 潜在影响 |
|---------|---------------------------|-------------------|---------|
| 算子粒度 | 多个独立算子 | 部分算子融合为单个 kernel | profiling 拆开量、execution plan 拆开求和 → 系统性偏差 |
| 执行拓扑 | 串行执行 | 独立路径可能在多 CUDA stream 上并行 | execution plan 串行求和 → 高估 |
| Kernel 实现 | 一种 kernel 实现 | vLLM 可能选择不同的 kernel 实现 | profiling 量的是不同 kernel 的性能 |
| 量化行为 | config 中声明性的量化描述 | 实际 kernel 级别的量化，可能有 per-component 差异或 fused 内部量化 | profiling 参数不匹配 |
| 配置处理 | 无特殊处理 | vLLM 可能对某些配置有硬性约束或自动重写 | bench config 填错导致 crash 或行为不一致 |
| KV Cache | 逻辑性描述 | 特定的二进制布局、block size 约束 | simulation 参数错 |

**注意：上表仅为差异类别提示，具体差异因模型而异，需要在分析中发现。**

**如果不做这一步，维度 1~8 基于 reference model 产出的 execution plan 和 profiling 参数列表，与 vLLM 实际执行对不上，导致仿真结果系统性偏差。**

### 输入

```
必须:
  - model_type (从 Step 0 提取，如 "deepseek_v4")
  - 模型在 vLLM 中的注册名 (如 "DeepseekV4ForCausalLM")

AtCode MCP 项目:
  - 项目名: vllm_v4_claude (最新 vLLM 源码的知识图谱)
  - 图谱源码路径: /share_data/wangziping/vllm_for_atcode
  - 若目标模型不在图谱中 → Fallback 到本地源码 (~/vllm/)
```

### 执行方式: AtCode MCP 直接调用

**关键约束: AtCode MCP 工具不能被 sub-agent 调用。执行本 skill 的主代理必须直接调用所有 MCP 工具。本 Step 中禁止使用 Task tool / sub-agent / background agent。**

#### 前置检查

```
1. 调用 mcp__atcode__set_project("vllm_v4_claude") 设置项目上下文

2. 验证目标模型在图谱中:
   调用 mcp__atcode__find_nodes("<registry_name>")
   - 找到 → 继续 Phase 1-4
   - 未找到 → 进入 Fallback 模式 (见本节末尾)
```

**MCP 工具使用规则:**
- 本步骤中 **禁止** 使用 Task tool / sub-agent / background agent
- 使用 AtCode MCP 工具探索 vLLM 代码
- 仅在读 ontos-servingsim 本仓库文件时使用 Read/Bash

---

#### Phase 1: 模型定位与入口发现

**目标:** 找到模型主文件、所有组件类、入口类的 qualified_name。

```
Step 1.1: 从注册名定位模型类
  ──────────────────────────────────
  调用: mcp__atcode__find_nodes("<registry_name>")
  例: find_nodes("DeepseekV4ForCausalLM")

  提取:
  - qualified_name, path, line_range, hierarchy_tree

Step 1.2: 获取继承层次结构
  ──────────────────────────────────
  调用: mcp__atcode__find_class_hierarchy("<主模型类_qualified_name>")

  提取: 父类 (mixin 特征), 子类 (同系列变体)

Step 1.3: 发现模型文件中所有组件类
  ──────────────────────────────────
  调用: mcp__atcode__get_children("<model_file_path>",
                                    identifier_type="file", depth=1, child_types="Class")

  提取: 所有 Class 子节点及其 qualified_name，特别关注:
  - XxxDecoderLayer (层的执行流入口)
  - XxxAttention / XxxMLA* (注意力)
  - XxxMoE / XxxMLP (FFN)
  - 其他辅助类

Step 1.4: 获取主模型类源码
  ──────────────────────────────────
  调用: mcp__atcode__explore_code("<主模型类_qualified_name>",
                                    include_dependency_source_code=false)

  从源码提取:
  - packed_modules_mapping (权重融合)
  - __init__ 中 aux_stream_list 等全局配置
  - load_weights 权重映射
```

---

#### Phase 2: 追踪层执行流（★ 核心步骤）

**目标:** 从 DecoderLayer.forward() 出发，按实际执行顺序记录每个 op，捕获跨组件的融合和条件分支。

**为什么这一步必须按执行流而非按组件：**
- vLLM 的 DecoderLayer.forward() 是实际执行的入口
- 执行流中包含跨组件的融合（如 mhc_fused_post_pre 融合 HC post-attn + HC pre-FFN）
- 组件属性可能被构造后修改（如 wo_a.is_bmm = True 改变 kernel 行为）
- ffn_norm 可能折叠进 MoE 的 gate（只在 DecoderLayer 层面可见）

```
Step 2.1: 获取 DecoderLayer 完整源码
  ──────────────────────────────────
  调用: mcp__atcode__explore_code("<DecoderLayer_qualified_name>",
                                    include_dependency_source_code=false)

  从 __init__ 提取:
  - 所有 self.xxx 属性及其类名
  - 构造后属性修改 (如 xxx.is_bmm = True, xxx.bmm_batch_size = ...)
  - aux_stream_list 等多 stream 配置
  - HC 相关参数 (hc_mult, hc_sinkhorn_iters, hc_attn_fn, hc_ffn_fn, ...)
  - 注释中提到的融合/折叠信息

  从 forward() 提取完整执行序列:
  对 forward() 中每个语句，按顺序记录:
  1. 操作类型: norm / linear / attention / ffn / custom_op / stream_switch / ...
  2. self.xxx 属性引用的类
  3. 是否有条件分支 (如 if platform.is_rocm(), if compress_ratio == 4)
  4. 是否有 CUDA stream 切换
  5. 是否是融合 op (如 mhc_fused_post_pre 同时处理两个阶段)

  产出: 完整的层执行流序列，格式:
    Step N: <op_name> (类=<class_name>, 类型=<norm|linear|attention|custom|...>,
             条件=<触发条件>, 融合=<融合说明或null>)

  示例 (DeepSeek V4):
    Step 1: hc_pre_attn    (类=MHCPreOp, 类型=custom, 条件=首层)
    Step 2: mhc_fused_post_pre (类=MHCFusedPostPreOp, 类型=custom,
             条件=非首层, 融合=HC_post_attn+HC_pre_attn)
    Step 3: attn_norm       (类=RMSNorm, 类型=norm)
    Step 4: attn            (类=DeepseekV4Attention, 类型=attention)
    Step 5: mhc_fused_post_pre (类=MHCFusedPostPreOp, 类型=custom,
             融合=HC_post_attn+HC_pre_ffn)  ← 跨维度融合！
    Step 6: ffn             (类=DeepseekV4MoE, 类型=ffn, 注释=ffn_norm已折叠进norm_gate)

Step 2.2: 识别需要进一步展开的子组件
  ──────────────────────────────────
  从 Step 2.1 的执行流中，标记需要 Phase 3/4 深入追踪的子组件:
  - attn 类 → Phase 3 追踪
  - ffn 类 → Phase 4 追踪
  - custom op 类 → 记录其 qualified_name 用于后续查找

Step 2.3: 追踪 Model-Level 执行流 [KP-0051]
  ──────────────────────────────────
  追踪 Model.forward() 中 per-layer loop 前/后的 op:
  1. 获取 Model 类源码 (非 DecoderLayer，而是包含 per-layer loop 的外层类)
  2. 提取 loop 前的 op (通常为 embedding + expand，可标注为框架标准操作)
  3. 提取 loop 后的 op (可能有 hc_head, final_norm, lm_head 等)
  4. 判断标准: 有 GEMM / CUDA custom kernel / Triton kernel → 必须纳入 plan
  5. 产出: Model-Level Op 清单，后续纳入 plan 的 2.1.N 段落

  示例 (DeepSeek V4):
    Post-loop Step 1: hc_post (DecoderLayer.hc_post, 已在 per-layer 覆盖)
    Post-loop Step 2: hc_head (HCHeadOp, Sinkhorn + GEMM, [M,4,7168]×fn[4,28672]) ← 必须纳入
    Post-loop Step 3: final_norm (RMSNorm, 轻量但需纳入 [KP-0047])
```

---

#### Phase 3: 追踪注意力内部执行流

**目标:** 展开 Phase 2 中发现的 Attention 子组件，追踪其内部执行流（多 stream、融合 kernel、条件分支）。

```
Step 3.1: 发现 Attention 内部组件
  ──────────────────────────────────
  Attention 可能有独立文件 (如 deepseek_v4_attention.py)。

  调用: mcp__atcode__find_nodes("<model_type>*Attention|<model_type>*MLA*|<model_type>*Indexer")

  对每个发现的类:
  调用: mcp__atcode__explore_code("<class_qualified_name>", include_dependency_source_code=false)

Step 3.2: 从 Attention __init__ 提取
  ──────────────────────────────────
  - 所有融合线性层: MergedColumnParallelLinear (记录 output_sizes), QKVParallelLinear, ColumnParallelLinear
  - 构造后属性修改 (如 is_bmm=True, bmm_batch_size=...)
  - 自定义 kernel: torch.ops.xxx 注册
  - Indexer 条件创建 (如仅 compress_ratio==4 时创建)
  - aux_stream_list 引用
  - Rotatory embedding 参数

Step 3.3: 从 Attention/MultiHeadLatentAttentionWrapper forward() 提取
  ──────────────────────────────────
  - 多 stream 执行: 哪些 op 在 default stream，哪些在 aux stream
  - 融合 kernel 调用: torch.ops.xxx 的参数
  - 条件分支: 不同 compress_ratio 下的不同执行路径
  - per-layer 行为差异: 哪些 op 在哪些层执行/不执行

  产出: Attention 内部执行流，标注每个 op 的:
    - 所在 stream
    - 融合关系
    - 条件触发 (per-layer-type)
    - kernel 类型
```

---

#### Phase 4: 追踪 FFN/MoE 内部执行流

**目标:** 展开 Phase 2 中发现的 FFN/MoE 子组件。

```
Step 4.1: 获取 MoE/FFN 类源码
  ──────────────────────────────────
  调用: mcp__atcode__explore_code("<MoE_class>", include_dependency_source_code=false)

Step 4.2: 从 MoE __init__ 提取
  ──────────────────────────────────
  - norm_gate (融合了 norm + gate matmul 的类)
  - routed expert 实现: FusedMoE / MegaMoEExperts / 其他
  - shared expert 实现
  - MoE backend 选择条件 (use_mega_moe, FusedMoE)
  - EP/TP 配置
  - 量化方案 (expert_dtype, scoring_func)

Step 4.3: 从 MoE forward() 提取
  ──────────────────────────────────
  - 执行路径分支 (MegaMoE vs FusedMoE)
  - norm_gate 调用 (融合了 norm + gate)
  - topk 路由
  - expert 计算
  - shared expert 计算 (与 routed expert 是串行还是并行?)
  - 结果合并方式 (add? residual?)

  产出: MoE 内部执行流，标注融合和并行关系
```

---

#### Phase 5: 基础设施分析

**目标:** 发现 vLLM 对此模型的 config 重写、backend 选择、KV cache 配置。

```
Step 5.1: Config 重写发现
  ──────────────────────────────────
  调用: mcp__atcode__find_nodes("<model_type>*Config", node_type="Code")
  对每个 config 文件:
  调用: mcp__atcode__read_file("<file>", pattern="<model_type>", match_mode="regex")
  提取: 字段重映射、自动重写、硬性约束

Step 5.2: Attention Backend 发现
  ──────────────────────────────────
  调用: mcp__atcode__find_nodes("<model_type>*backend*|<model_type>*Backend*")
  对每个 backend:
  调用: mcp__atcode__explore_code("<backend_class>", include_dependency_source_code=false)
  提取: block_size, kv_cache_dtype, head_dim 约束

Step 5.3: KV Cache 配置
  ──────────────────────────────────
  调用: mcp__atcode__find_nodes("<model_type>*kv_cache*")
  或: mcp__atcode__read_file("vllm/v1/core/kv_cache_utils.py",
                              pattern="<model_type>", match_mode="regex")
  提取: KV cache 布局/分配逻辑, dtype 重写, block_size 配置

Step 5.4: MoE Backend 发现
  ──────────────────────────────────
  调用: mcp__atcode__find_nodes("<model_type>*moe*backend*|select_<model_type>*moe")
  提取: kernel 选择策略, 量化方案, EP backend 选择

Step 5.5: Tokenizer / Speculative Config
  ──────────────────────────────────
  调用: mcp__atcode__find_nodes("<model_type>*tokenizer*|<model_type>*speculative*")
  提取: tokenizer_mode 自动检测, MTP 注册
```

---

#### Phase 6: Dossier 组装

将 Phase 2-5 的发现合并为 5 张表（格式见下方产出部分）。
关键变化：**Dossier 的基础是执行流而非组件列表。**

```
Step 6.1: Fused Op Map (表 A)
  来源:
  - Phase 2 的执行流中标注为"融合"的 op
  - Phase 3/4 中发现的内部融合
  - Phase 1.4 的 packed_modules_mapping

Step 6.2: Parallel Execution Map (表 B)
  来源:
  - Phase 2 中发现的跨组件并行 (如 DecoderLayer 层面的并行)
  - Phase 3 中发现的多 stream 执行

Step 6.3: Kernel Backend Map (表 C)
  来源:
  - Phase 2 中每个 op 实际使用的类
  - Phase 3/4 中 torch.ops.xxx 的 kernel 类型
  - Phase 5.2-5.4 的 backend 发现

Step 6.4: Config Rewrite Map (表 D)
  来源: Phase 5.1-5.5

Step 6.5: Profiling Wrapper Alignment Check (表 E)
  对比 vLLM kernel 与 ontos/profiling/ 下的 wrapper (Read/Bash)
```

---

#### Fallback: 模型不在图谱中时

```
1. 本地源码路径: ~/vllm/ (最新 git clone)
   或: python -c "import vllm; print(vllm.__path__[0])"

2. 使用 Read/Bash 读取本地源码，按同样的执行流追踪方法:
   - Phase 2: 读取 DecoderLayer.forward() → 提取执行序列
   - Phase 3: 读取 Attention 文件 → 追踪内部执行流
   - Phase 4: 读取 MoE 文件 → 追踪内部执行流
   - Phase 5: grep 模型名在 config/、backends/、kv_cache_utils.py

3. 可选: 将源码复制到 /share_data/wangziping/ 并调用
   mcp__atcode__manage_graph(action="build", project_name="<新项目名>",
                              project_path="/share_data/wangziping/<源码目录>")
   重建图谱后再用 MCP 分析。

4. Fallback 模式的 Dossier 标注 [Fallback]
```

### 产出: vLLM Implementation Dossier

subagent 输出以下 5 张结构化表：

**注意: 数据来自 AtCode MCP 知识图谱。每条发现标注 `[MCP]` 表示来自图谱、`[Fallback]` 表示来自本地源码读取。**

#### A. Fused Op Map

记录 reference model 中哪些独立 op 在 vLLM 中被融合为单个 kernel：

```
格式:
  reference_ops: [op1, op2, ...] → vLLM_fused_op: "<class_name>(<params>)" | vLLM_kernel: "<kernel_type>"

示例 (DeepSeek V4):
  [wq_a, wkv]                    → fused_wqa_wkv (MergedColumnParallelLinear, output_sizes=[q_lora_rank, head_dim])  [MCP]
  [q_norm, kv_norm]              → fused_q_kv_rmsnorm (Triton kernel, 2 task IDs)  [MCP]
  [q_rsqrt_norm, q_rope, kv_rope, kv_sim_quant, kv_cache_save]
                                  → fused_deepseek_v4_qnorm_rope_kv_rope_quant_insert (custom CUDA op)  [MCP]
  [compressor_norm, compressor_kv_rope, compressor_kv_sim_quant, cache_write]
                                  → _fused_kv_compress_norm_rope_insert_* (Triton, 3 variants)  [MCP]
  [compressor_wkv, compressor_wgate]
                                  → compressor.fused_wkv_wgate (MergedColumnParallelLinear)  [MCP]
```

#### B. Parallel Execution Map

记录 vLLM 中哪些 op 组并行执行，受什么条件控制：

```
格式:
  phase: <name>
  default_stream: [op_list]
  aux_streams: {stream_id: [op_list]}
  condition: <gating_condition>

示例 (DeepSeek V4):
  phase: "initial_gemm"
    default_stream: [fused_wqa_wkv(hidden_states)]
    aux_streams: {
      0: [compressor.fused_wkv_wgate(hidden_states)],
      1: [indexer.weights_proj(hidden_states)],       # C4 layers only
      2: [indexer.compressor.fused_wkv_wgate(hidden_states)]  # C4 layers only
    }
    condition: "hidden_states.shape[0] <= VLLM_MULTI_STREAM_GEMM_TOKEN_THRESHOLD (default 1024)"

  phase: "second_level"
    default_stream: [wq_b(qr), fused_qnorm_rope_kv_insert(...)]
    aux_streams: {
      0: [compressor(x)] or [indexer(x, qr)]   # C128 or C4 respectively
    }
    condition: "same as above"
```

#### C. Kernel Backend Map

记录 vLLM 实际使用的 kernel 类型，与 reference model 的对比：

```
格式:
  op_category: {reference_implementation → vLLM_implementation}

示例 (DeepSeek V4):
  sparse_attention: {TileLang sparse_attn → DeepseekV4FlashMLASparseBackend (FlashMLA sparse)}  [MCP]
  mlp_gate: {PyTorch Linear → FusedMoE (TP) or MegaMoE (EP, Blackwell only)}  [MCP]
  all2all: {无 → DeepEP high_throughput / low_latency (NCCL-based)}  [MCP]
  kv_cache: {logical FP8 → fp8_ds_mla (custom binary layout)}  [MCP]
  attention_block_size: {configurable → heterogeneous: Full MLA=256, SWA=64, C4=4, C128=8 (hardcoded)}  [MCP]
```

#### D. Config Rewrite Map

记录 vLLM 对输入配置的自动重写：

```
格式:
  field: {input_value → rewritten_value, trigger_condition}

示例 (DeepSeek V4):
  quant_method: {"fp8" → "deepseek_v4_fp8", trigger: model_type == "deepseek_v4"}  [MCP]
  kv_cache_dtype: {"fp8" → "fp8_ds_mla", trigger: model_type == "deepseek_v4"}  [MCP]
  tokenizer_mode: {auto → "deepseek_v4", trigger: arch == "DeepseekV4ForCausalLM"}  [MCP]
  kv_cache_assert: {assert kv_cache_dtype.startswith("fp8"), trigger: model_type == "deepseek_v4"}  [MCP]
```

#### E. Profiling Wrapper Alignment Check

对比 Ontos profiling wrapper 使用的 kernel 与 vLLM 实际 serving 使用的 kernel：

```
格式:
  profiling_wrapper: <ontos wrapper file and kernel>
  vllm_serving: <vllm implementation file and kernel>
  aligned: YES | NO
  gap_description: (if NO)

示例 (DeepSeek V4):
  sparse_attn_c4_wrapper:
    profiling_wrapper: ontos/profiling/attn_backend/sparse_attn_c4_wrapper.py (TileLang sparse_attn)
    vllm_serving: vllm/v1/attention/backends/mla/flashmla_sparse.py (FlashMLA sparse)
    aligned: NO
    gap_description: "Profiling 用 TileLang kernel，vLLM 用 FlashMLA kernel。两者算法不同，性能特征可能不同。"

  mlp_impl:
    profiling_wrapper: ontos/profiling/mlp/mlp_impl.py (CausalSelfV4MQA, 逐 op 独立计时)
    vllm_serving: vllm/model_executor/layers/deepseek_v4_attention.py (fused_wqa_wkv, 多 stream 并行)
    aligned: NO
    gap_description: "Profiling 逐个 op 串行计时，vLLM 融合 wq_a+wkv 为单个 GEMM 并用 4 stream 并行。粒度和并行度均不匹配。"
```

### Dossier 如何影响维度 1~8

每个维度在分析时，除现有流程外，从执行流 Dossier 中定位该维度涉及的 op 序列：

```
对每个维度执行:
  1. 现有流程: 从 reference model 分析架构特征 → 分类 → 命中 KP
  2. 从执行流 Dossier 中提取该维度涉及的 op:
     a. 在 Phase 2 的层执行流中定位该维度的 op (如维度 2 对应 attn 相关的 Step)
     b. 在 Phase 3/4 的内部执行流中展开这些 op 的细节
     c. 检查 Dossier 表:
        - Fused Op Map: 这些 op 是否被融合? → profiling 和 execution plan 应对齐到融合后的粒度
        - Parallel Map: 这些 op 是否在并行组中? → execution plan 需支持 max() 语义
        - Kernel Map: 这些 op 的 kernel 是否与 profiling wrapper 一致? → 标记风险
        - Config Rewrite Map: 这些 op 涉及的 config 是否被自动重写? → bench config 需使用重写后的值
     d. 检查执行流中的跨维度融合: 该维度的 op 是否与其他维度的 op 融合?
        (如 mhc_fused_post_pre 同时包含维度 6 的 HC 和维度 2/3 的边界)
  3. 如发现 reference ≠ vLLM:
     → 在该维度的产出中标记 "⚠ vLLM 对齐偏差"
     → 产出中增加 "vLLM 实际行为" 段落
     → 在最终 plan 中列出需要额外处理的对齐项
```

### 对最终 Plan 的要求

最终输出的实现计划中必须包含一个新的章节：

```
## vLLM 对齐清单

### 已对齐项
  - [x] attention backend: V4_FLASHMLA_SPARSE (vLLM 硬编码，catalog.yaml 已配置)
  - [x] kv_cache_dtype: fp8 (vLLM 强制要求，已修复 catalog.yaml)
  - ...

### 未对齐项 (需额外处理)
  - [ ] profiling 粒度: 当前逐 op 独立 profile，vLLM 实际融合 wq_a+wkv 为单 GEMM
        建议: 增加 fused_wqa_wkv 的联合 profiling，或在 execution plan 中对融合 op 使用单次查表
  - [ ] execution plan 并行: 当前串行求和，vLLM 用 4 stream 并行
        建议: ExecutionPlan 增加 add_parallel() 语义，对 Path A/B/C/D 取 max
  - [ ] profiling kernel 对齐: sparse_attn C4 wrapper 用 TileLang，vLLM 用 FlashMLA
        建议: 验证两种 kernel 的性能特征是否接近，或改用 FlashMLA 做 profiling
```

---
