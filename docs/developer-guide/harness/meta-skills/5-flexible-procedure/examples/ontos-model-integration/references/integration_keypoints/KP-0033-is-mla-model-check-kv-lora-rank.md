# KP-0033: is_mla_model() 必须检查 kv_lora_rank 而非 RoPE 维度

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0033 |
| 日期 | 2026-05-18 |
| 阶段 | config |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 发现者 | DeepSeek V4 profiling readiness 分析 |

## 问题描述

**现象**: V4 使用 MQA (Multi-Query Attention) 而非 MLA，但设置了 `qk_nope_head_dim=448` 和 `qk_rope_head_dim=64`（描述 head_dim=512 内部的 RoPE/NoPE 拆分）。原来的 `is_mla_model()` 只检查这两个字段是否非 None，导致 V4 被误判为 MLA → profiling 走入 MLA 路径 → 崩溃（因为 `kv_lora_rank=None`）。

**根因**: MLA 的定义性特征是 KV 低秩压缩（`kv_lora_rank`），而非 RoPE 维度拆分。V4 复用了 `qk_nope_head_dim`/`qk_rope_head_dim` 字段但语义不同：MLA 中表示 latent→head 映射的维度拆分，V4 MQA 中表示大 head_dim 内的 RoPE 应用范围。

**表现**: 非 MLA 模型被误判为 MLA → 进入 MLA 代码路径 → `kv_lora_rank` 为 None 导致 AttributeError/AssertionError。

## 约束定义

**必须满足的条件**: `is_mla_model()` 应检查 `kv_lora_rank is not None`，不应仅检查 `qk_nope_head_dim`/`qk_rope_head_dim`。

**违反后果**: 非 MLA 模型被误判为 MLA → 进入 MLA 代码路径 → `kv_lora_rank` 为 None 导致 AttributeError/AssertionError。

**检查方法**: 对所有 model config 验证 `is_mla_model() == (kv_lora_rank is not None)` 一致性。

## 代码位置

- 文件路径: `ontos/config/model_config.py` — BaseModelConfig.is_mla_model() (simulation side, already has V4 override)
- 文件路径: `ontos/profiling/config/model_config.py` — ModelConfig.is_mla_model() (profiling side, was broken)

## 标准解决方案

**正确做法**: `is_mla_model() = kv_lora_rank is not None`

**常见错误做法**: `is_mla_model() = qk_nope_head_dim is not None or qk_rope_head_dim is not None`

**自动化建议**: 新增 model config 时，自动验证 `is_mla_model()` 与 `kv_lora_rank` 一致性。

## 相关关键点

- KP-0012: MQA KV 维度

## 发现过程

DeepSeek V4 profiling readiness 分析中发现 MLP profiling 崩溃。V4 的 profiling ModelConfig.is_mla_model() 返回 True 导致走入 CausalSelfMLA 路径。
