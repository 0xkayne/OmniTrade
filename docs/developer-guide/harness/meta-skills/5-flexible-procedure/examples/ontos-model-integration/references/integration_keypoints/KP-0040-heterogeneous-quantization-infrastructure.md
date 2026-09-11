# KP-0040: 异构量化模型需要 per-component 精度覆盖机制

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0040 |
| 日期 | 2026-05-18 |
| 维度 | quantization |
| 严重度 | P0 |
| 状态 | constraint_defined |
| 触发条件 | `同一层内存在组件使用不同量化精度` (如 FP8 权重 + BF16 O 投影 + FP32 HC 参数) |
| 泛化标签 | heterogeneous_quantization |
| 发现者 | 集成 DeepSeek V4 时发现 — 对比官方 model.py 与框架实现后确认 |

## 问题描述

**现象**: V4 官方 model.py 中同一层内不同组件使用不同精度：wq_a/wq_b/wkv/wo_b 为 FP8，wo_a 为 BF16，HC 参数/compressor wkv/wgate 为 FP32，gate 为 BF16，embedding 为 BF16，lm_head 为 FP32。但框架的 `QuantizationConfig` 只有 4 个全局枚举（gemm/moe/kv_cache/comm），同一域内所有组件共享同一精度。

**根因**: 框架设计时假设同一域内所有组件使用统一量化精度。`modules_to_not_convert` 虽然支持跳过，但只能回退到 BF16（2 bytes），无法表达 FP32（4 bytes）或其他精度。`AttentionModel.get_param_size()` 对所有子组件统一乘以单一 `weight_size`，无法按组件区分。

**表现**: 当全局 `gemm_quant_mode=FP8` 时：
- wo_a（应为 BF16=2B）被算作 FP8=1B → 参数大小低估 2x
- HC 参数（应为 FP32=4B）被算作 FP8=1B → 参数大小低估 4x
- lm_head（应为 FP32=4B）被算作 FP8=1B → 参数大小低估 4x
- embedding（应为 BF16=2B）被算作 FP8=1B → 参数大小低估 2x
- HC FFN 参数在 DeepSeekV4DecoderLayer 中是原始元素数直接与字节数值相加（类型不匹配 bug）
- MTP 投影权重未乘以任何 weight_size（quant_cfg 赋值后未使用 bug）

## 约束定义

**必须满足的条件**:

1. **量化基础设施必须支持 per-component 精度覆盖**: 当官方模型同一层内不同组件使用不同精度时，框架必须能表达这种异构性，不能退化为全局统一精度
2. **参数大小计算必须按组件分组**: 异构模型的 `get_param_size()` 不能使用基类的"单一 weight_size × 总元素数"模式，必须按组件分组分别计算字节大小
3. **覆盖值必须支持 FP32**: `modules_to_not_convert` 只能回退到 BF16，不够。需要新机制支持任意 bytes/elem 值（包括 FP32=4.0）
4. **Model-level 默认覆盖必须自动合并**: 异构模型的默认覆盖（定义在 ModelConfig 中）必须在 ReplicaConfig 构建时自动合并到实际使用的 quantization_config，不能依赖用户在 CLI/YAML 中手动配置

**违反后果**: GPU 显存规划严重失准。部分组件参数大小被低估（FP8 替代了应有的 BF16/FP32），导致 simulator 低估总显存需求，可能在规划中给出不切实际的 GPU 配置建议。

**评估方法**:
```python
# 验证 QuantizationConfig 支持 per-component 覆盖
cfg = QuantizationConfig(
    gemm_quant_mode=GEMMQuantMode.FP8,
    component_dtype_overrides={"wo_a": 2.0, "hc_pre_gemm": 4.0},
)
assert cfg.effective_weight_size("model.layers.0.self_attn.wq_a") == 1.0  # 全局 FP8
assert cfg.effective_weight_size("model.layers.0.self_attn.wo_a") == 2.0  # BF16 覆盖
assert cfg.effective_weight_size("model.layers.0.self_attn.hc_pre_gemm_attn") == 4.0  # FP32 覆盖

# 验证 V4 ModelConfig 有正确的默认覆盖
v4_cfg = DeepSeekV4ProModelConfig()
overrides = v4_cfg.quantization_config.component_dtype_overrides
assert overrides["hc_pre_gemm"] == 4.0
assert overrides["wo_a"] == 2.0
assert overrides["lm_head"] == 4.0
assert overrides["embed_tokens"] == 2.0
```

## 代码位置

- 文件路径: `ontos/config/quantization_config.py`
- 关键类: `QuantizationConfig`
- 新增字段: `component_dtype_overrides: dict[str, float]`
- 新增方法: `effective_weight_size(prefix) -> float`
- 关键方法: `effective_gemm_quant_mode(prefix)` — 仅用于 CSV 路径解析，不用于参数大小计算
- 文件路径: `ontos/config/config.py`
- 关键位置: `ReplicaConfig.__post_init__()` — 合并 model-level overrides
- 文件路径: `ontos/config/model_config.py`
- 关键类: `DeepSeekV4ProModelConfig` — 设置 V4 默认覆盖
- 文件路径: `ontos/execution_time_predictor/models/v4_attention.py`
- 关键方法: `V4Attention.get_param_size()` — 覆写基类实现异构按组件计算
- 文件路径: `ontos/execution_time_predictor/models/deepseek_v4.py`
- 修复位置: HC FFN 参数大小、embed/lm_head 大小、MTP 投影大小
- 文件路径: `ontos/execution_time_predictor/models/ffn.py`
- 修复位置: `MoEFFN.get_param_size()` 中 gate/shared_experts 使用 `effective_weight_size()`

## 标准解决方案

**正确做法**:
1. 在 `QuantizationConfig` 中新增 `component_dtype_overrides: dict[str, float]` 字段，映射前缀子串到 bytes/elem
2. 新增 `effective_weight_size(prefix) -> float` 方法，解析优先级：overrides → skip list → 全局模式
3. 需要异构量化的模型在 `ModelConfig` 中设置默认 overrides（V4 在 `DeepSeekV4ProModelConfig` 中）
4. `ReplicaConfig.__post_init__` 自动合并 model-level overrides 到实际使用的 config
5. 异构模型的 `get_param_size()` 覆写基类，按组件分组计算（参考 V4Attention 的实现模式）
6. 同时在 `MoEQuantMode` 中补充 FP4 枚举（`weight_size=0.5`），为 Blackwell 平台准备

**常见错误做法**:
- 将全局 `gemm_quant_mode` 应用于所有组件，忽略官方模型中特定组件的精度差异
- 在 `modules_to_not_convert` 中列出需要不同精度的组件 — 只能回退到 BF16，无法表达 FP32
- 在 `get_param_size()` 中混用原始元素数和字节数（HC FFN bug）
- 在 `get_mtp_param_size()` 中赋值 `quant_cfg` 但不使用（MTP bug）
- 在 `MoEFFN.get_param_size()` 中对 gate 使用 `effective_gemm_quant_mode().weight_size` 而非 `effective_weight_size()` — overrides 不会生效

**自动化建议**: model-integrator 在分析新模型时，检查官方实现中同一层内是否有组件使用了不同于全局 dtype 的精度。如果有，标记需要 `component_dtype_overrides` 扩展，并列出具体的覆盖键值对。

## 相关关键点

- KP-0024: FP4 expert 双层 scale 量化 — 同一层内不同量化方案的另一个体现
- KP-0016: MoE Expert 参数量需独立计算 — 本 KP 的约束直接影响了参数量计算的精度
- KP-0025: mHC 4x 激活倍增 — HC 参数是 FP32，与本 KP 中的 FP32 覆盖直接相关
- KP-0026: 分组低秩 O 投影 — wo_a 是 BF16，与本 KP 中的 BF16 覆盖直接相关

## 发现过程

集成 DeepSeek V4 时，逐行对比官方 model.py 中每个 Linear 层的 dtype 与框架实现。发现官方模型中 5 类组件（FP8 默认、BF16 wo_a、FP32 HC、FP32 compressor、FP32 lm_head）使用了 3 种不同精度，但框架只能表达全局统一的 FP8。进一步分析发现 `AttentionModel.get_param_size()` 的"单一 weight_size × 总元素数"模式是根本原因，需要覆写为按组件分组计算。同时还发现了 HC FFN 参数原始元素数与字节数混用的 bug、MTP 投影权重未量化的 bug、以及 MoEFFN gate 使用 `effective_gemm_quant_mode().weight_size` 导致 overrides 不生效的问题。
