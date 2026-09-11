# KP-0012: V4 所有 Attention 类型共享 MQA（num_kv_heads=1）

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0012 |
| 日期 | 2026-05-13 |
| 阶段 | attention |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 发现者 | 通过分析 V4 模型配置和 FlashMLA 接口发现 |

## 问题描述

**现象**: V4 的 KV cache 大小计算中，容易继续使用 V2/V3 的 MLA 维度（kv_lora_rank=512）而非 MQA 维度。
**根因**: V2/V3 用 MLA（Multi-Head Latent Attention），KV cache 存储的是压缩后的 latent 向量（维度 = kv_lora_rank + qk_rope_head_dim = 576）。

V4 改用 MQA（Multi-Query Attention），所有 Q head 共享一个 KV head：
- `num_kv_heads = 1`（V2/V3 也是 1，但 MLA 的 latent 维度很大）
- V4 的 KV 直接存储，不走 latent 压缩
- KV 维度 = head_dim（而非 kv_lora_rank）
- 压缩发生在 token 轴（4:1 或 128:1），不是 head 轴

**表现**: 如果错误使用 MLA 的 kv_lora_rank 计算 per-entry 大小，会高估单个 compressed entry 的大小。

## 约束定义

**必须满足的条件**:
1. V4 的 `num_kv_heads = 1`（MQA）
2. V4 的 per-entry KV 大小 = `2 × 1 × head_dim × dtype_size`（K 和 V 各一个 head）
3. Compressed entry 大小 = per-entry 大小（压缩是减少 entries 数量，不是减小 entry 大小）
4. V4 不使用 MLA 的 latent 投影，不需要 `kv_lora_rank` 参数

**违反后果**: 单个 compressed entry 的大小被高估（用 kv_lora_rank=512 而非 head_dim），导致总显存估算偏高。

**检查方法**: 验证 V4 model config 的 `get_kv_cache_memory_per_token()` 不使用 `kv_lora_rank`。

## 代码位置

- 文件路径: `ontos/config/model_config.py` (V4 model config)
- 文件路径: `ontos/execution_time_predictor/models/` (V4 predictor 的 per-token KV 计算)

## 标准解决方案

**正确做法**:
1. V4 model config 不设置 `kv_lora_rank`（或设为 None / 0）
2. Per-entry KV 大小 = `2 × head_dim × dtype_size`
3. Compressed pool 总大小 = num_entries × per_entry_size
4. `is_mla_model()` 对 V4 返回 False

**常见错误做法**:
- 继承 DeepSeekV2ModelConfig 并保留 `kv_lora_rank=512` → 误导 per-entry 计算
- 把 V4 当作 MLA 模型处理 → 走错误的 attention backend 路径

**自动化建议**: 验证 V4 model config 的 `is_mla_model()` 返回 False，且 `get_num_kv_heads()` 返回 1。

## 相关关键点

- KP-0003: Memory Planner 的 per-token KV 计算必须适配压缩比
- KP-0009: V4 每层是 SWA + 压缩 Attention 的组合

## 发现过程

SGLang 博客提到 V4 使用 FlashMLA 的 hybrid attention 接口，但 V4 的 KV cache 格式不同于 V3 的 MLA latent。HuggingFace 的 model doc 确认 V4 的 `kv_proj` 产出单一 KV head（MQA）。V4 的创新是在 token 轴压缩（CSA/HCA），而不是在 head 轴压缩（MLA）。
