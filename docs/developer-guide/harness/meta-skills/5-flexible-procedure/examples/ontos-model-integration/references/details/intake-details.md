# Intake Details

This file is mechanically extracted from the preserved legacy skill copy. Keep updates in active split skills and KP files unless intentionally refreshing legacy-derived details.

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
