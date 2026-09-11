# KP-0034: get_head_size() 必须使用显式 head_dim 字段

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0034 |
| 日期 | 2026-05-18 |
| 阶段 | profiling |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 发现者 | DeepSeek V4 profiling 分析 |

## 问题描述

**现象**: `profiling/config/model_config.py` 的 `get_head_size()` 使用 `embedding_dim // num_q_heads` 计算 head 维度。对于 V4 (head_dim=512, emb//q=56) 和 Gemma2 (head_dim=256, emb//q=224) 返回错误值。导致 attention profiling 中 QKV 张量维度错误。

**根因**: 原始假设 `head_dim = embedding_dim / num_q_heads` 仅对标准 MHA 成立（如 Llama）。对以下模型不成立：1) MLA 模型（但 MLA wrapper 自行覆盖 head_dim 所以不受影响）；2) 自定义 head_dim 的模型（Gemma2, V4 MQA）。

**表现**: attention wrapper 创建错误维度的 QKV 张量 → profiling kernel 启动失败或返回无意义计时。

## 约束定义

**必须满足的条件**: `get_head_size()` 返回值 == model config 中声明的 `head_dim`。

**违反后果**: attention wrapper 创建错误维度的 QKV 张量 → profiling kernel 启动失败或返回无意义计时。

**检查方法**: 对所有 model config 验证 `get_head_size() == head_dim`。

## 代码位置

- 文件路径: `ontos/profiling/config/model_config.py` — ModelConfig.get_head_size()
- 文件路径: `ontos/profiling/attn_backend/base_attention_wrapper.py:30` — `self.head_dim = model_config.get_head_size()`
- 文件路径: `ontos/profiling/attention/attention_wrapper.py:71` — `self._head_dim = self._model_config.get_head_size()`

## 标准解决方案

**正确做法**: `return self.head_dim`（构造函数已正确存储此值）。

**常见错误做法**: `return self.embedding_dim // self.num_q_heads`

**自动化建议**: 新增 model 时自动检查 `head_dim == get_head_size()` 一致性。

## 相关关键点

- KP-0012: MQA KV 维度

## 发现过程

V4 profiling 分析时发现 attention wrapper 用 head_dim=56（错误），V4 实际 head_dim=512。
