# KP-0015: GQA 的 num_q_heads 必须被 num_kv_heads 整除

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0015 |
| 日期 | 2026-05-14 |
| 维度 | attention |
| 严重度 | P1 |
| 状态 | seed |
| 触发条件 | `num_kv_heads > 1 AND num_kv_heads < num_q_heads` |
| 泛化标签 | gqa_head_divisibility |
| 发现者 | 种子 KP (代码约定提取) |

## 问题描述

**现象**: 当 `num_attention_heads` 不能被 `num_key_value_heads` 整除时，GQA 的 KV 共享机制无法正确工作。
**根因**: GQA 将 Q heads 分成 `num_kv_heads` 组，每组共享一个 KV head。如果 Q heads 不能均分到 KV groups，会导致某些 KV head 服务不同数量的 Q head，attention 计算出错。
**表现**: 参数量计算错误、attention shape mismatch、或者模拟结果偏差。

## 约束定义

**必须满足的条件**: `num_attention_heads % num_key_value_heads == 0`

**违反后果**: 无法正确计算 GQA 的参数量（特别是 grouped O projection 的维度），attention 计算的 batching overhead 建模错误。

**评估方法**:
```python
assert num_attention_heads % num_key_value_heads == 0, \
    f"GQA: num_attention_heads ({num_attention_heads}) must be divisible by num_key_value_heads ({num_key_value_heads})"
# 验证 per_group_q_heads = num_attention_heads // num_key_value_heads 是整数
```

## 代码位置

- 文件路径: `ontos/config/model_config.py`
- 相关逻辑: `_get_num_q_heads_per_kv_group()` 或等效方法
- 相关模型: LLaMA-2/3, Qwen-2/2.5, Mistral, Gemma, Phi-3 等所有 GQA 模型

## 标准解决方案

**正确做法**: 在 ModelConfig 初始化时验证整除关系。GQA 参数量公式中，`per_group = num_q_heads // num_kv_heads` 必须是整数。

**常见错误做法**:
- 假设 GQA 的 KV 维度与 MHA 相同（实际 KV heads 更少，但每个 KV head 的维度不变）
- 在参数量计算中混用 `num_q_heads` 和 `num_kv_heads`

## 相关关键点

- KP-0012: MQA 的 KV 维度是 head_dim（num_kv_heads=1 的特例）

## 发现过程

种子 KP — 从框架代码约定中提取。所有 GQA 模型 (LLaMA-3, Qwen-2.5, Mistral 等) 都满足此约束，但框架文档中未显式声明。集成新 GQA 模型时容易忽略此验证。
