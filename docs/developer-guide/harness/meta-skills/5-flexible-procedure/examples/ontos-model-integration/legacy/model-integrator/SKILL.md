---
name: model-integrator
description: >
  系统化集成新 LLM 模型到 Ontos 仿真器。以 vLLM 实际 serving 实现为对齐标准（而非 HuggingFace reference model），通过 KP 约束库驱动，按 8 维度分析架构特征、产出结构化实现计划。核心原则：vLLM 是 ground truth，reference model.py/kernel.py 仅用于理解架构意图，最终 profiling 和 execution plan 必须对齐 vLLM 的实际 kernel、fusion、并行策略。触发词: "integrate new model", "add model", "集成新模型", "添加模型", "模型适配"。
---

# Model Integrator — 以 vLLM 为对齐标准的新模型集成框架

## 核心原则：vLLM 是 Ground Truth

**本 skill 最重要的一句话：最终验证标准是 vLLM 端到端实测指标，不是 HuggingFace reference model。**

Reference model（model.py、kernel.py）的定位是**理解架构意图**——"这个模型想做什么"。但 Ontos 仿真器的验证对象是 vLLM serving 的实际表现。reference model 和 vLLM 实现之间几乎必然存在差异——差异的具体形式因模型而异，需要在 Step 0.V 中通过分析 vLLM 源码来发现，而非提前枚举。

这些差异可能出现在以下维度（不限于此，具体取决于模型）：

- **算子粒度**：reference 中多个独立 op 可能在 vLLM 中被融合为单个 kernel
- **执行拓扑**：reference 中的串行执行可能在 vLLM 中变为多流并行
- **Kernel 选择**：vLLM 可能使用与 reference 不同的 kernel 实现
- **量化行为**：vLLM 可能有 per-component 或 fused kernel 内部的特殊量化处理
- **配置约束**：vLLM 可能对某些配置有硬性约束或自动重写，而 reference 中没有
- **KV Cache 格式**：vLLM 可能有特定的二进制布局或 block size 约束

**以上仅为可能出现的差异类别，不是固定清单。每个模型的具体差异需要在 Step 0.V 中实际分析 vLLM 代码才能确定。**

本 skill 的分析流程因此是双源驱动的：
1. **Reference Model** — 理解"模型想做什么"（架构意图、数学定义、数据流）
2. **vLLM Implementation** — 理解"vLLM 实际怎么做的"（具体差异需要在分析中发现）

两者缺一不可。Reference model 提供正确的数学语义，vLLM 提供正确的工程实现。最终 plan 必须两者都对齐。

## 本 Skill 是什么

一个 **以 vLLM 为对齐标准的知识驱动模型集成引擎**。核心知识存储在 `docs/integration_keypoints/` 的 KP 约束库中。本 skill 从 reference model 提取架构意图，从 vLLM 实现提取工程约束，逐维度匹配 KP 约束，输出对齐 vLLM 的结构化实现计划。

```
知识层 (KP 约束库)           ← docs/integration_keypoints/  (持续积累)
    ↓ 约束驱动
分析层 (本 Skill)             ← 双源输入: Reference Model (架构意图) + vLLM (工程实现)
    ↓ 结构化输出
执行层 (Phase Skills)         ← 各维度的具体实现 (可人工可自动)
    ↓
验证层 (sim-bench)            ← 仿真 vs vLLM 实测对比 (端到端 + 算子级)
```

## 设计目标

1. **vLLM 对齐优先** — 任何维度分析都要以 vLLM 的实际实现为准，reference model 仅辅助理解
2. **模型无关** — 适用于任意 transformer-based LLM，不绑定任何特定模型
3. **知识积累** — 每次集成发现的坑自动沉淀为 KP，后续模型受益
4. **标准化评估** — 每个 KP 自带评估方案，可直接生成验证命令
5. **可持续进化** — KP 库持续扩充 → skill 的分析能力自动增强
6. **未知创新应对** — 遇到决策树无法分类的新颖架构时，自动切换到第一性原理分析模式

---

## 执行流程

```
输入: config.json + modeling_xxx.py + vLLM model_registry 中的模型注册名
      [可选: kernel.py, 论文/博客]
  │
  ├─ Step -1: 输入验证 + 相似度评估 + 增量检测
  │
  ├─ Step 0: 自动提取架构参数 → 特征向量 (基于 reference model)
  │
  ├─ Step 0.5: 参数量自动验证
  │
  ├─ Step 0.X: 新颖度检测 (标记未知维度)
  │     对每个维度的特征向量检查是否落入已知分类
  │     产出: Novel 维度列表 + 高/低新颖度评估
  │
  ├─ Step 0.V: vLLM 实现分析 (★ 关键步骤 — 从 reference 切换到 vLLM 视角)
  │     主代理通过 AtCode MCP 分析 vLLM 知识图谱 (项目名: vllm_v4_claude)
  │     若图谱不含目标模型 → Fallback 到本地源码读取 (~/vllm)
  │     产出: Fused Op Map + Parallel Map + Kernel Map + Config Rewrite Map
  │           + Profiling Wrapper Alignment Check
  │
  ├─ 维度 1~8: 逐维度分析 (每个维度同时参考 reference model 和 vLLM Dossier)
  │     探测 (从提取参数判断特征)
  │       → 分类 (落入哪个架构类型)
  │         → [新颖?] 切换到第一性原理分析模式 (见"未知创新架构处理协议")
  │         → [已知?] 命中 KP → 评估 → 产出
  │
  ├─ Step 8: 汇总 — 输出实现计划 + KP 命中矩阵 + 验证清单
  │
  └─ Step 9: 知识回灌 — 发现新约束 → 记录为新 KP
```

---

## Step -1: 输入验证与相似度评估

在进入正式分析之前，验证输入完整性并评估与已集成模型的相似度。

### 输入验证

```
必须存在的输入:
  1. config.json — 包含模型架构参数 (JSON 格式)
  2. model.py — 包含模型类定义 (Python 源码)

可选输入:
  3. kernel.py — 自定义 kernel 实现
  4. 论文/技术博客 — 架构说明文档
  5. HuggingFace 模型页面链接

验证检查:
  硬性要求 (失败则中止):
    ✓ config.json 可解析为 JSON
    ✓ model.py 可解析为 Python (至少包含 class 定义)

  尽力而为的探测 (失败仅警告，不中止):
    ? config.json 字段命名风格检测:
        HuggingFace 风格:  hidden_size, num_hidden_layers, num_attention_heads ...
        自定义风格:        dim, n_layers, n_heads, d_model ... (如 DeepSeek, Qwen 原生)
        → 如果是自定义风格，Step 0 提取脚本需要适配字段映射
    ? model.py 主类名检测:
        HuggingFace 风格:  *ForCausalLM, *PreTrainedModel, *Model
        自定义风格:        Transformer, LanguageModel, Model, Net ...
        → 记录主类名，后续步骤引用
    ? model.py 注意力类检测:
        搜索: *Attention*, *Attn*, *SelfAttention*, *CrossAttention*
        → 如果找不到，grep 更广泛的模式 (如 "scale", "softmax", "qkv")
```

**设计原则**: 不同开源厂商的代码风格差异很大。DeepSeek 用 `dim`/`n_layers`/`Transformer`，HuggingFace 标准用 `hidden_size`/`num_hidden_layers`/`*ForCausalLM`。验证规则不应排斥非 HuggingFace 原生格式，而是探测风格并适配。

### 相似度评估

将新模型与框架中已集成的所有模型做特征比对，找到最接近的"模板模型"：

```python
# 已集成模型特征向量 (从 BaseModelConfig 子类提取)
known_models = {
    "meta-llama/Meta-Llama-3-8B": {"attn": "GQA", "ffn": "Dense", "kv": "Uniform", "quant": "None", "residual": "Add", "decode_accel": "None", "special": []},
    "deepseek-ai/DeepSeek-V2":    {"attn": "MLA", "ffn": "MoE+Shared", "kv": "Uniform", "quant": "None", "residual": "Add", "decode_accel": "None", "special": []},
    "deepseek-ai/DeepSeek-V4-Pro": {"attn": "Hybrid", "ffn": "MoE+Shared", "kv": "MultiGroup", "quant": "ExpertOnly", "residual": "HC", "decode_accel": "MTP", "special": ["SWA"]},
    # ... 其他已集成模型
}

# 计算 8 维度匹配率
match_score = sum(1 for dim in dims if new_model[dim] == template[dim]) / 8
```

输出格式：

```
=== 相似度报告 ===
最相似模型: deepseek-ai/DeepSeek-V2 (匹配度 63%)
匹配维度: 注意力(MLA), FFN(MoE+Shared), KV Cache(Uniform), 残差(Add), Decode加速(无)
差异维度:
  - 量化: V2=None → 新模型=ExpertOnly (需新增)
  - 特殊功能: V2=[] → 新模型=[MTP] (需新增)

推荐策略: 基于 DeepSeek-V2 模板增量开发，聚焦差异维度
```

### 增量集成模式

当 `--base-model` 参数指定了同系列已集成模型时，跳过相同维度，只分析差异：

```
输入: 新模型 config.json + --base-model deepseek-ai/DeepSeek-V2
处理:
  1. 提取新模型特征向量
  2. 与 base-model 特征向量逐维度对比
  3. 相同维度 → 标记 "继承 base-model，无需改动"
  4. 差异维度 → 进入标准分析流程
输出: 增量计划 (只包含差异维度的 subagent 任务)
```

---

## Step 0: 架构参数自动提取

从 config.json 和 model.py 中提取所有关键参数，填入统一的特征向量。后续所有维度的决策都基于此向量。

**注意**: 不同开源厂商的 config.json 字段命名差异很大。提取脚本先尝试 HuggingFace 标准字段，再尝试常见的自定义字段名，最后 fallback 到 dump 所有字段。

### 执行命令

```bash
# 从 config.json 提取 — 多风格适配
cat config.json | python -c "
import json, sys
d = json.load(sys.stdin)

# 先尝试所有已知字段名 (HuggingFace 标准 + 常见自定义)
field_aliases = {
    # 通用
    'model_type':        ['model_type'],
    'architectures':     ['architectures'],
    'num_hidden_layers': ['num_hidden_layers', 'n_layers', 'num_layers'],
    'num_attention_heads':['num_attention_heads', 'n_heads', 'num_heads'],
    'num_key_value_heads':['num_key_value_heads', 'n_kv_heads', 'num_kv_heads'],
    'hidden_size':       ['hidden_size', 'dim', 'd_model', 'hidden_dim'],
    'intermediate_size': ['intermediate_size', 'moe_inter_dim', 'ffn_dim', 'intermediate_dim'],
    'head_dim':          ['head_dim'],
    'vocab_size':        ['vocab_size'],
    'max_position_embeddings': ['max_position_embeddings', 'max_seq_len', 'original_seq_len'],
    'sliding_window':    ['sliding_window', 'window_size'],
    'rope_theta':        ['rope_theta'],
    'rope_scaling':      ['rope_scaling'],
    'rms_norm_eps':      ['rms_norm_eps', 'layer_norm_eps'],
    # MoE
    'n_routed_experts':  ['n_routed_experts', 'num_local_experts', 'num_experts'],
    'n_shared_experts':  ['n_shared_experts', 'num_shared_experts'],
    'num_experts_per_tok':['num_experts_per_tok', 'n_activated_experts', 'num_selected_experts', 'topk'],
    'expert_dtype':      ['expert_dtype'],
    # 注意力特殊字段
    'kv_lora_rank':      ['kv_lora_rank', 'kv_lora_dim'],
    'q_lora_rank':       ['q_lora_rank'],
    'qk_rope_head_dim':  ['qk_rope_head_dim', 'rope_head_dim', 'rope_dim'],
    'qk_nope_head_dim':  ['qk_nope_head_dim', 'nope_head_dim'],
    'v_head_dim':        ['v_head_dim'],
    'compress_ratios':   ['compress_ratios', 'compression_ratios'],
    'hc_mult':           ['hc_mult'],
    'compress_rope_theta':['compress_rope_theta'],
    'index_topk':        ['index_topk', 'index_top_k'],
    'o_lora_rank':       ['o_lora_rank'],
    'o_groups':          ['o_groups'],
    # 量化
    'quantization_config':['quantization_config'],
    'dtype':             ['dtype'],  # 自定义格式常用
}

found_any = False
for canonical, aliases in field_aliases.items():
    for alias in aliases:
        if alias in d:
            val = d[alias]
            if alias != canonical:
                print(f'{canonical}: {json.dumps(val) if isinstance(val, (list, dict)) else val}  (from: {alias})')
            else:
                print(f'{canonical}: {json.dumps(val) if isinstance(val, (list, dict)) else val}')
            found_any = True
            break

if not found_any:
    print('WARNING: No recognized fields found. Dumping all keys:')
    for k, v in d.items():
        print(f'  {k}: {json.dumps(v) if isinstance(v, (list, dict)) else v}')
"

# 从 model.py 提取 — 多风格适配
echo "=== Classes ==="
grep -n "^class " model.py
echo "=== KV Projections (多风格) ==="
grep -n "self.k_proj\|self.v_proj\|self.kv_a\|self.kv_b\|self.wkv\|self.q_proj\|self.o_proj\|self.wo_a\|self.wo_b\|self.wq\|self.wk\|self.wv\|self.wo\|self.q_a\|self.q_b" model.py
echo "=== Special Ops ==="
grep -n "self.compressor\|self.indexer\|self.hc\|self.swiglu\|self.mtp\|self.gate\|self.w_gate\|self.moe\|self.experts\|self.shared_expert" model.py
echo "=== Forward Flow ==="
grep -n "def forward" model.py
```

### 特征向量

将提取结果组织为以下特征向量，后续维度分析直接引用：

```
attention_type:   MHA | GQA | MQA | MLA | Hybrid | SWA_Alternating | Linear | SSM_Hybrid | Novel
ffn_type:         Dense | MoE | MoE+Shared | MoE+FineGrained | MoE+ExpertChoice | Other
kv_cache_type:    Uniform | MultiGroup | Compressed | Hybrid | Bounded_SWA | None
quant_type:       None | WeightOnly | WeightAndActivation | Heterogeneous
residual_type:    Add | HC | mHC | Other
special_features: [SWA, MTP, YaRN, ALiBi, MoD, ...]

num_q_heads:      (int)
num_kv_heads:     (int)
kv_lora_rank:     (int | None)
head_dim:         (int)
compress_ratios:  (tuple[int] | None)
n_routed_experts: (int | None)
n_shared_experts: (int | None)
expert_dtype:     (str | None)
hc_mult:          (int | None)
sliding_window:   (int | None)
o_groups:         (int | None)
o_lora_rank:      (int | None)
has_sim_quant:    (bool)          # model.py 中有 inplace Q/DQ roundtrip (QAT simulation)
scale_dtype:      (str | None)    # "fp32" | "fp8" | None
```

---

## Step 0.5: 参数量自动验证

给定特征向量，参数量计算是确定性的。自动计算并与官方数据交叉验证，不依赖手工。

### 自动计算公式

```python
def calc_params(fv):
    """从特征向量计算模型参数量"""
    L = fv.num_hidden_layers
    H = fv.num_q_heads
    D = fv.hidden_size
    d = fv.head_dim

    # === Embedding ===
    embedding = fv.vocab_size * D

    # === Attention per layer ===
    if fv.attention_type == "MHA":
        attn = (H + 2*fv.num_kv_heads) * d * D + H * d * D  # q + k + v + o
    elif fv.attention_type == "GQA":
        attn = H * d * D + 2 * fv.num_kv_heads * d * D + H * d * D  # q + kv + o
    elif fv.attention_type == "MQA":
        attn = H * d * D + D * d + fv.o_groups * fv.o_lora_rank + fv.o_groups * fv.o_lora_rank * D  # q + wkv + wo_a + wo_b
    elif fv.attention_type == "MLA":
        # q: q_a + q_b, kv: kv_a + kv_b, o: o_proj
        attn = (D * fv.q_lora_rank + fv.q_lora_rank * H * d +
                D * fv.kv_lora_rank + fv.kv_lora_rank * (fv.qk_nope_head_dim + fv.v_head_dim) +
                H * d * D)
    else:
        attn = None  # 未知类型，需手动计算

    # === FFN per layer ===
    if fv.ffn_type == "Dense":
        ffn = 3 * D * fv.intermediate_size  # gate + up + down (SwiGLU)
    elif "MoE" in fv.ffn_type:
        single_expert = 3 * D * fv.intermediate_size
        ffn = fv.n_routed_experts * single_expert
        if fv.n_shared_experts:
            ffn += fv.n_shared_experts * single_expert
    else:
        ffn = None

    # === Per-layer extras ===
    extras_per_layer = 0
    if fv.hc_mult:  # HC: 4 projections per layer
        extras_per_layer += 4 * D * D
    if fv.compress_ratios:  # Compressor: wkv + wgate
        extras_per_layer += 2 * D * d  # approximate

    # === Norm ===
    norm = 2 * D  # RMSNorm per layer (input + post-attn)

    # === Total ===
    total = embedding
    if attn is not None:
        total += L * (attn + norm)
    if ffn is not None:
        total += L * ffn
    total += L * extras_per_layer

    return {"embedding": embedding, "attn_per_layer": attn,
            "ffn_per_layer": ffn, "extras_per_layer": extras_per_layer,
            "total": total}
```

### 验证流程

```
1. 用上述公式计算 total
2. 从 HuggingFace model card 或文档获取官方参数量
3. 计算偏差率 = |calc - official| / official
4. 偏差 > 5% → 发出警告，标记哪些组件的公式可能不完整
5. attn=None 或 ffn=None → 该维度包含未知架构，需手动补充
```

---

## Step 0.X: 新颖度检测

在进入逐维度分析之前，检测哪些维度包含决策树无法分类的架构创新。

### 检测逻辑

```
对每个维度的特征向量值，检查是否落入已知分类:

attention_type ∈ {MHA, GQA, MQA, MLA, Hybrid, SWA_Alternating}?
  NO → 标记为 Novel，进入未知创新处理协议

ffn_type ∈ {Dense, MoE, MoE+Shared}?
  NO → 标记为 Novel

residual_type ∈ {Add, HC}?
  NO → 标记为 Novel

special_features 中是否有未识别的项?
  YES → 标记为 Novel
```

### 新颖度报告

```
=== 新颖度检测报告 ===
已知维度 (5/8): 身份, FFN(MoE+Shared), KV Cache(MultiGroup), 量化(None), Profiling
未知维度 (2/8):
  - 维度 2-注意力: attention_type=Novel
    原因: KV 投影使用三阶段 pipeline (与已知 MHA/GQA/MQA/MLA 均不同)
    决策树命中: "其他" 分支
  - 维度 6-残差: residual_type=Novel
    原因: 发现未知的 "Cross-Layer Attention" 机制

影响评估:
  - 高新颖度维度 (>3) → 建议人工主导探索，subagent 辅助
  - 低新颖度维度 (1~2) → subagent 可独立工作，探索任务用第一性原理协议
```

---

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

## 维度 1: 模型身份注册

### 探测问题

| # | 问题 | 提取来源 | 判定逻辑 |
|---|------|----------|----------|
| 1.1 | 模型的标识符是什么？ | config.json `model_type` (HuggingFace) 或目录名/文件名 (自定义) | 见下方决策树 |
| 1.2 | 是否已有同系列已注册模型？ | `get_all_subclasses(BaseModelConfig)` | 遍历查找 |
| 1.3 | config.json 是 HuggingFace 格式还是自定义格式？ | Step -1 风格检测结果 | 决定如何映射到框架的 ModelConfig 字段 |
| 1.V | vLLM Dossier: Config Rewrite Map 中是否有 model_type 相关的重写？ | Dossier D | 影响框架 ModelConfig 的注册名和 catalog.yaml 配置 |

### 决策树

```
config.json 有 model_type 字段? (HuggingFace 标准格式)
  YES → 用 model_type 作为标识符
        model_type 已有匹配的 ModelConfig?
          YES → 复用子类，检查是否需要扩展字段
          NO  → 新建 BaseModelConfig 子类

  NO → 自定义格式 (如 DeepSeek 原生、Qwen 原生)
        从以下来源推断标识符:
          1. 用户提供的模型名称 (如 "deepseek-ai/DeepSeek-V4-Pro")
          2. 文件所在目录名或仓库名
          3. model.py 主类名 (如 Transformer → 推断 deepseek 系列)
        推断的标识符已有匹配的 ModelConfig?
          YES → 复用子类
          NO  → 新建 BaseModelConfig 子类

新建时需处理:
  - 将自定义字段名映射到框架的 ModelConfig 标准字段
    (如 dim → hidden_size, n_layers → num_hidden_layers)
  - 记录映射关系到 ModelConfig 子类注释中

注册后完整性验证:
  is_mla_model() 是否准确? [KP-0033]
    检查: 如果 qk_nope_head_dim IS NOT NONE 且 kv_lora_rank IS NONE
      → 这是 "MQA with RoPE split"，不是 MLA
      → is_mla_model() 必须基于 kv_lora_rank is not None 判定
      → 错误判定会导致模型进入 MLA 代码路径 (错误 backend / wrong KV cache sizing)

  Profiling ModelConfig 是否接受新字段? [KP-0035]
    验证: ModelConfig.from_model_name(model_name) 不崩溃
    如果崩溃 → profiling ModelConfig.__init__ 缺少新增的 BaseModelConfig 字段
      → 修复: 增加 **kwargs 兜底或显式添加参数
```

### 实现路径

文件: `ontos/config/model_config.py`

```python
@dataclass
class NewModelConfig(BaseModelConfig):
    @staticmethod
    def get_name() -> str:
        return "org/model-name"
    # 后续维度分析确定需要的特有字段
```

发现机制: `BaseModelConfig.create_from_name()` 通过 `get_all_subclasses()` 自动发现，无需显式注册。

### 验证

```bash
python -c "
from ontos.config.model_config import BaseModelConfig
cfg = BaseModelConfig.create_from_name('org/model-name')
assert cfg.get_name() == 'org/model-name'
"
```

### KP 约束索引

| KP | 严重度 | 泛化标签 | 触发条件 | 核心要点 |
|----|--------|----------|----------|----------|
| KP-0033 | P0 | mla_detection | `qk_nope_head_dim IS NOT NONE AND kv_lora_rank IS NONE` | is_mla_model() 必须基于 kv_lora_rank，不是 RoPE 维度拆分 |
| KP-0035 | P1 | config_field_sync | `新增 BaseModelConfig 字段` | profiling ModelConfig 必须接受所有 BaseModelConfig 字段 (**kwargs 兜底) |
| KP-0041 | P1 | config_inheritance_audit | `新模型 config 继承自已有 ModelConfig 子类` | 继承链中每个字段都必须与官方规格对比，不同的必须显式覆盖；V4 错误继承了 q_lora_rank/routed_scaling_factor/first_k_dense_replace |
| KP-0046 | P0 | kv_cache_dtype_hard_requirement | `model_type == "deepseek_v4"` | V4 要求 FP8 KV cache (vLLM assert `fp8` 前缀)，model_path 指向含 config.json 的目录 (无 safetensor_weights 子目录) |

---

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


### 8.2 Subagent 任务分解规则

根据 8.1 计划文件中的 **Section 7 (实现任务分解与依赖)** 生成为 subagent 任务。任务按 Phase 组织，与计划中的 Phase 对应。

**任务粒度原则:**
- 每个计划中的 Task = 1 个 subagent 调用
- Phase 内无依赖的 Task 可同时启动
- Phase 间的依赖必须满足后才能启动下一 Phase
- Novel 维度的 Task 可能需要先启动研究型 subagent (Phase A-E 协议)

**任务生成模板:**

> ### Phase N, Task N.M: <任务名>
>
> **依赖:** Task X.Y (必须先完成)
> **可并行:** Task N.K (可同时执行)
> **类型:** 实现 / 研究 (Novel 维度先研究)
> **涉及 KP:** KP-XXXX, KP-YYYY (命中的 KP)
> **涉及 Op (来自 2.1 清单):** op_a, op_b, ...
> **涉及文件:**
>   - 新建: path/to/new_file.py
>   - 修改: path/to/existing_file.py
> **具体工作:**
>   1. <步骤1 — 引用 2.1 的 Op 定义和 2.3 的 Profiling 参数>
>   2. <步骤2>
> **验证:**
>   - <来自第 5 节 KP 命中矩阵的验证方法>
> **Keypoint 捕获接口:**
>   如果工作中发现新的约束或错误，调用 keypoint-capture:
>   → 维度: <维度名>
>   → 现象: <描述>
>   → 触发条件: <什么模型特征会触发此问题>


**Phase 依赖关系:**

```
Phase 1: 基础注册          (无依赖，可立即开始)
  ↓
Phase 2: Execution Plan    (依赖 Phase 1)
  Task 2.1 (Attention) ─┐
  Task 2.2 (FFN)        ├─ 可并行
  Task 2.4 (量化)       ─┘
  Task 2.3 (KV Cache)    (依赖 Task 2.1)
  ↓
Phase 3: Profiling        (依赖 Phase 2)
  Task 3.1 (attention/) ─┐
  Task 3.2 (mlp/)        ├─ 可并行
  Task 3.3 (collectives/)─┘
  ↓
Phase 4: Simulator 集成   (依赖 Phase 2 + Phase 3)
  ↓
Phase 5: 验证             (依赖全部)
```

**跳过规则:** 如果某个 Phase 的所有 Task 对应的维度分析结果为"复用现有，无需改动"，则跳过整个 Phase。

**增量模式规则:** 如果 `--base-model` 指定了同系列模型，相同维度标记为"继承"，不生成对应 Task。

### 8.3 Subagent 执行上下文

每个 subagent 启动时必须知道:

```
必须传入:
  1. 模型名称和 HuggingFace ID
  2. config.json 路径
  3. model.py 路径
  4. 该维度的分析结果（类型、特征向量值）
  5. 命中的 KP 列表（读取 KP 文件获取完整约束）
  6. 需要操作的文件列表
  7. 验证命令
  8. 相似度报告中最接近模型的实现路径 (参考代码)

可按需读取:
  9. 本 skill (model-integrator) 的对应维度章节（决策树、实现路径）
  10. 现有相似模型的实现代码（作为参考）
  11. docs/integration_keypoints/ 中的 KP 文件
```

### 8.4 Subagent 失败恢复协议

当 subagent 执行失败时，按以下流程处理:

```
Subagent 报错
  │
  ├─ Step 1: 立即调用 keypoint-capture 记录错误
  │   → 维度: 当前维度
  │   → 现象: 报错信息
  │   → 根因: 分析失败原因
  │   → 严重度: 根据影响评估
  │
  ├─ Step 2: 判断错误类型
  │   已命中 KP 的约束违反?
  │     YES → KP 的评估方法或约束定义不够准确 → 更新 KP
  │     NO → 新约束 → 创建新 KP
  │
  │   代码逻辑错误?
  │     → 修复后重试
  │
  │   架构理解错误?
  │     → 回滚该维度改动 → 重新进入 Phase A (原始信息收集)
  │
  ├─ Step 3: 恢复策略
  │   简单错误 (< 3 次重试) → 修复后重试当前任务
  │   理解性错误 → 回滚 → 生成新的研究型 subagent 任务
  │   框架限制 → 记录 KP (severity=P0) → 人工介入
  │
  └─ Step 4: 更新计划
      将失败教训和 KP 更新写入集成计划
      通知后续依赖任务的 subagent 注意新发现的约束
```

### 8.5 Subagent 与 keypoint-capture 的接口

subagent 在执行过程中发现新约束时，调用 keypoint-capture:

```
调用方式: 直接使用 /keypoint-capture 触发

传入参数:
  - 维度: <从 8 个维度中选择>
  - 现象: <发生了什么>
  - 根因: <为什么会这样>
  - 触发条件: <什么模型特征会命中此问题>
  - 评估方法: <如何验证>
  - 严重度: P0/P1/P2/P3
```

### 8.6 计划执行后的闭环

```
计划执行 → subagent 工作 → 发现新 KP
  ↓
keypoint-capture 记录 KP
  ↓
kp-integration-protocol 优化 model-integrator (更新索引表/决策树/泛化标签)
  ↓
下一个模型集成时，model-integrator 自动命中新 KP
```

### 8.7 Plan 完备性校验 (★ 强制步骤)

在 Step 8 生成计划后、进入 Phase 1 执行前，**必须**执行以下 5 项校验：

#### 校验 1: Op List ↔ Profiling Plan 1:1 完备性 [KP-0047]

```
遍历 2.1 Per-Layer Op 清单中的每个 op name:
  对每个 op，检查 2.3 Profiling 计划中是否有对应条目
  缺失 → 报错并要求补充 (即使是轻量 op)

遍历 2.3 Profiling 计划中的每个条目:
  对每个条目，检查 2.1 中是否有对应 op
  多余 → 说明 profiling 计划有冗余

目标: 差集为空 (两侧完全匹配)
```

#### 校验 2: Op 粒度对齐 vLLM [KP-0048]

```
对 2.1 中的每个 op:
  检查该 op 是否在 Step 0.V 的 Fused Op Map 中有 vLLM kernel 对应
  如果 op 是按 reference model 逐行展开的粒度 → 警告并建议合并为 vLLM 融合粒度

检查 vLLM 中执行的 kernel 是否都在 2.1 中有对应 op
  缺失 → 遗漏了 vLLM 实际执行的 kernel
```

#### 校验 3: 标准 8 列格式 [KP-0047]

```
检查 2.1 的每个表格是否包含完整的 8 列:
  Op | Profile | Shape | Kernel | 量化 | Prefill | Decode | 框架基类

缺失列 → 报错并要求补充
Shape 列格式: Linear 用 [M,K]x[K,N], ElementWise 用 elem=MxD
```

#### 校验 4: 参数值源码级验证 [KP-0050]

```
对 2.1 中每个有 Shape 列的 op:
  从 Step 0.V 的 AtCode MCP 源码提取对应 Linear 初始化的参数:
    MergedColumnParallelLinear: 检查 output_sizes 列表
    ColumnParallelLinear: 检查 output_size
    RowParallelLinear: 检查 input_size/output_size
  追踪条件因子推导链 (如 coff = 1 + overlap):
    确认 plan 的 Shape 中是否正确包含了条件因子

  重点检查:
  - 条件因子 (coff, overlap) 是否传播到最终 shape
  - einsum 类 op 的维度顺序是否与源码 einsum 表达式一致
  - 元素数是否使用了正确的张量维度 (n_local_heads vs n_groups)
  - compress_ratios 层数统计是否与 config.json 数组一致

  差异 → 报错并列出具体参数偏差
```

#### 校验 5: Model-Level Ops 覆盖 [KP-0051]

```
检查 Step 0.V Phase 2.3 的 Model-Level 执行流产出:
  如果有 model-level op (hc_head, final_norm 等):
    → 检查 2.1 是否有对应的 "Model-Level Ops" 段落
    → 检查 2.3 是否有对应的 profiling 条目
    缺失 → 报错并要求补充

  如果 Step 0.V 未执行 Phase 2.3:
    → 警告: "未追踪 Model.forward() 的 model-level op，可能遗漏"
```

**校验结果处理:**
- 任何校验失败 → 在计划文件中标注 "⚠ 校验失败"，列出具体问题
- 全部通过 → 在计划文件中标注 "✓ 完备性校验通过"

---

## 维度间依赖顺序 (DAG)

8 维度的分析依赖和实现依赖 (与 8.1 计划文件 Section 7 的 Phase 对应):

```
分析阶段 (维度 1~8):
  维度 1 (身份)          → 全部下游的基础
    ↓
  维度 2 (注意力)        ─┐
  维度 3 (FFN)           ├→ 三路并行
  维度 5 (量化)          ─┘
    ↓
  维度 4 (KV Cache)      ← 依赖维度 2 (需 KV cache size)
    ↓
  维度 6 (残差/特殊)     ← 依赖维度 2, 3
    ↓
  维度 7 (Decode加速)    ← 依赖维度 2, 3, 6
  维度 8 (Profiling)     ← 依赖维度 2, 3, 5, 6, 7

实现阶段 (对应 Phase 1~5):
  Phase 1: ModelConfig 注册        (维度 1)
    ↓
  Phase 2: Execution Plan 定义      (维度 2/3/4/5/6 → Op 清单 2.1)
    ↓
  Phase 3: Profiling 配置与执行      (维度 8 → Profiling 计划 2.3)
    ↓
  Phase 4: Simulator 集成            (维度 7 + 综合)
    ↓
  Phase 5: 验证                      (KP 命中矩阵 → 验证清单)
```

核心依赖链: **Op 清单 (Phase 2) → Profiling 计划 (Phase 3) → Simulator (Phase 4)**

---

## KP 库覆盖度

| 维度 | 已有 KP | 种子 KP | 覆盖度 | 需要补充 |
|------|---------|---------|--------|----------|
| 身份注册 | KP-0033,035,041 | - | 较好 | 更多 config 验证约束 |
| 注意力 | KP-0006,7,8,9,10,11,12,20,22,26,033,038,042,045 | KP-0015, 0017, 0019 | 较好 | Linear Attention, SSM_Hybrid |
| FFN/MoE | KP-0024,036 | KP-0016, 0018 | 基础 | Expert-Choice routing, Fine-grained experts |
| KV Cache | KP-0001~5,13 | - | 较好 | 更多压缩策略, 无 KV cache 模型 |
| 量化 | KP-0024, 040 | - | 较好 | KV cache 量化，算子量化 |
| 残差/特殊 | KP-0025 | - | 基础 | HC 详细约束 |
| Decode 加速 | KP-0014 | - | 基础 | MTP, Medusa, Speculative 约束 |
| Profiling | KP-0006,8,20,21,23,034,035,039,043,044,047,048,049,050,051,052,053,054,055,056,057 | KP-0018 | 较好 | 更多融合模式, 自适应 PD 分析 |
