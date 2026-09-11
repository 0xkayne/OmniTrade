# KP-0045: 外部 vs 内部命名规范映射必须显式文档化

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0045 |
| 日期 | 2026-05-18 |
| 阶段 | attention |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 泛化标签 | naming_convention_map |
| 触发条件 | `新模型复用已有术语但语义不同（如 V4 的 "FlashMLA" 是 MQA 而非 MLA）` |
| 发现者 | DeepSeek V4 attention backend 命名分析 |

## 问题描述

**现象**: V4 的 attention backend 在不同系统中有三种名称：
- **vLLM CLI**: `V4_FLASHMLA_SPARSE`（vLLM 源码 `flashmla_sparse.py:150` 定义）
- **ontos 内部**: `flashmla_sparse`（`config.py:673` auto-detect 定义）
- **profiling CSV 路径**: `attention_flashmla_sparse_bf16.csv`（使用 ontos 内部名）

集成过程中 `automation/naming.py` 返回 `V4_FLASHMLA_SPARSE`，此值正确用于 vLLM serve CLI 和 server_id 命名，但如果流入 ontos 内部（经 `normalize_attention_backend_name` 小写化后变为 `v4_flashmla_sparse`），会找不到 profiling CSV 文件。

**根因**: V4 的 "FlashMLA" 术语有歧义：
- **MLA 机制**: DeepSeek V2/V3 使用的 Multi-head Latent Attention（`kv_lora_rank` 压缩 KV cache）
- **FlashMLA kernel 库**: 一个优化的 CUDA kernel 库，既支持 MLA 模式也支持 MQA 模式
- V4 使用 **MQA**（`num_kv_heads=1`）+ **latent KV compression**（`kv_lora_rank=head_dim`），通过 FlashMLA kernel 库的 sparse 变体执行
- vLLM 将 V4 的 backend 命名为 `V4_FLASHMLA_SPARSE`，其中 "FlashMLA" 指的是 kernel 库名而非 MLA 机制

## 约束定义

**必须满足的条件**: 集成新模型时，当模型复用已有术语但语义不同时，必须建立命名映射表：

| 命名空间 | V4 sparse backend | 用途 |
|----------|-------------------|------|
| vLLM CLI / server_id | `V4_FLASHMLA_SPARSE` | 传给 `--attention-backend`、`build_server_id` |
| ontos ReplicaConfig | `flashmla_sparse` | config.py auto-detect、CSV 路径模板 |
| automation naming | `V4_FLASHMLA_SPARSE` | `_infer_attention_backend()` 返回值 |

**关键规则**: simulation 配置中 `attention_backend` 必须设为 `null`，让 ontos auto-detect 正确解析。不要将 vLLM 名称直接传给 ontos（`_add_arg` 在值为 None 时跳过 CLI 参数，见 `ontos_main.py:380`）。

**禁止操作**: 不要将 `V4_FLASHMLA_SPARSE`（大写，vLLM 名）写入 ontos 的 config 或 predictor 路径。

## 代码位置

- 文件路径: `automation/naming.py:123` — `_infer_attention_backend` 返回 `V4_FLASHMLA_SPARSE`
- 文件路径: `ontos/config/config.py:671-681` — ReplicaConfig auto-detect 使用 `flashmla_sparse`
- 文件路径: `ontos/config/utils.py:45` — `normalize_attention_backend_name` 小写化
- 文件路径: `automation/builders/ontos_main.py:380` — `_add_arg` 在 None 时跳过
- 文件路径: vLLM `vllm/v1/attention/backends/mla/flashmla_sparse.py:150` — `get_name()` 返回 `"V4_FLASHMLA_SPARSE"`

## 标准解决方案

**正确做法**:
1. `_infer_attention_backend` 返回 vLLM 风格名称（如 `V4_FLASHMLA_SPARSE`）用于 vLLM CLI 和 server_id
2. simulation 配置使用 `attention_backend: null`，让 ontos auto-detect 正确解析
3. 在集成计划中显式文档化命名映射表

**常见错误做法**: 将 vLLM 名称直接作为 ontos config 的 `attention_backend` 值传递。

## 相关关键点

- KP-0033: is_mla_model 必须检查 kv_lora_rank（同一命名混淆的根因：V4 有 qk_nope_head_dim 但不是 MLA）
- KP-0038: attention backend 矩阵必须区分 MLA-sparse 和 MQA-sparse
- KP-0006: attention backend 扩展边界

## 发现过程

V4 集成时 `naming.py` 初始返回 `FLASHMLA_SPARSE`（与 V3.2 相同），后发现 vLLM 源码中 V4 backend 叫 `V4_FLASHMLA_SPARSE`。改为 `V4_FLASHMLA_SPARSE` 后发现此名称不应流入 ontos 内部（会找不到 CSV）。最终确认：vLLM 名称用于 CLI/命名，ontos 内部用 auto-detect 的 `flashmla_sparse`。两套命名系统通过 `attention_backend: null` + auto-detect 桥接。
