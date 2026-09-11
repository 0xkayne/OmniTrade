# KP-0041: Config 继承链全面审计 — 所有父类字段必须与新模型规格对比

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0041 |
| 日期 | 2026-05-18 |
| 阶段 | config |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 泛化标签 | config_inheritance_audit |
| 触发条件 | `新模型 config 继承自已有 ModelConfig 子类` |
| 发现者 | DeepSeek V4 全面检查 |

## 问题描述

**现象**: DeepSeek V4 从 V3.2→V3→V2 继承了多个字段，其中 3 个与 V4 实际架构不符：`q_lora_rank=1536`（应为 1024）、`routed_scaling_factor=2.5`（应为 1.0）、`first_k_dense_replace=3`（应为 0，V4 全 MoE 无 dense 层）。

**根因**: V4 继承链 `DeepSeekV4ProModelConfig → DeepSeekV32ModelConfig → DeepSeekV3ModelConfig → DeepSeekV2ModelConfig`，每个父类都定义了合理的默认值，但 V4 在核心机制（MQA vs MLA、flat routing vs grouped routing、all-MoE vs dense+MoE）上与父类均不同。集成时只覆盖了"显而易见"的字段（head_dim, num_kv_heads 等），忽略了"看起来合理但实际错误"的字段。

**影响**:
- `q_lora_rank=1536` → `v4_attention.py` 中 Q 投影参数量偏高约 39%（`7168×1536 + 1536×65536` vs 正确的 `7168×1024 + 1024×65536`）
- `routed_scaling_factor=2.5` → MoE timer 中 routed expert 输出幅度被错误放大 2.5 倍
- `first_k_dense_replace=3` → 语义误导，如果未来代码读取此字段做 FFN 层类型决策会出错

## 约束定义

**必须满足的条件**: 当新模型的 config 继承自已有 ModelConfig 时，必须对父链中**每一个字段**执行以下审计：
1. 对照新模型的官方规格（HuggingFace config.json、参考模型代码）逐一验证
2. 不匹配的字段必须在子类中显式覆盖
3. 审计范围包括但不限于：注意力参数（q_lora_rank, head_dim, num_heads）、MoE 路由（n_group, topk_group, topk_method, scoring_func, routed_scaling_factor）、层结构（first_k_dense_replace, num_layers）、量化配置

**违反后果**: 参数大小计算错误（影响 memory 预估）、profiling 数据不反映真实行为、语义误导导致未来集成 bug。

**检查方法**: `DeepSeekV4ProModelConfig()` 实例化后，逐字段与 `ref/deepseek-v4/config.json` 对比。自动化：`assert c.q_lora_rank == reference_config["q_lora_rank"]`。

## 代码位置

- 文件路径: `ontos/config/model_config.py` — DeepSeekV4ProModelConfig (需覆盖字段的定义)
- 文件路径: `ref/deepseek-v4/config.json` — V4 官方参数（审计基准）
- 文件路径: `ontos/execution_time_predictor/models/v4_attention.py:209-213` — `get_param_size` 消费 `q_lora_rank`
- 文件路径: `ontos/profiling/mlp/mlp_impl.py:620-623` — `routed_scaling_factor` 消费点

## 标准解决方案

**正确做法**: 集成新模型时，在子类 config 中显式覆盖所有与父类不同的字段，即使父类值"看起来合理"。

**审计清单模板**:
```
[ ] q_lora_rank — 对照官方 config.json
[ ] routed_scaling_factor — 对照参考模型 route_scale
[ ] first_k_dense_replace — 确认 dense/MoE 层比例
[ ] n_group, topk_group, topk_method, scoring_func — 确认路由策略
[ ] kv_lora_rank — 确认 MLA vs MQA
[ ] head_dim, num_q_heads, num_kv_heads — 确认注意力维度
[ ] embedding_dim, vocab_size — 确认模型规模
```

**常见错误做法**: 只覆盖"明显不同"的字段（如 head_dim），忽略"看起来合理但实际错误"的字段（如 q_lora_rank）。

## 相关关键点

- KP-0033: is_mla_model 必须检查 kv_lora_rank（同一继承链的另一症状）
- KP-0036: MoE 路由配置继承（同一继承链的 MoE 路由症状）
- KP-0035: profiling ModelConfig 必须接受所有字段

## 发现过程

V4 全面检查中发现 `q_lora_rank` 继承 V2 的 1536 但 V4 reference (`ref/deepseek-v4/model.py:58`) 明确为 1024；`routed_scaling_factor` 继承 V3 的 2.5 但 V4 reference (`ref/deepseek-v4/model.py:55`) 明确为 1.0。经与 vLLM 源码交叉验证确认。此问题被 KP-0033/KP-0036 部分覆盖但未抽象为通用审计流程。
