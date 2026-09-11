# KP-0046: DeepSeek V4 KV Cache 必须为 FP8 且 Model Path 要求

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0046 |
| 日期 | 2026-05-21 |
| 维度 | kv_cache, identity |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 触发条件 | `model_type == "deepseek_v4"` |
| 泛化标签 | kv_cache_dtype_hard_requirement, model_path_structure |
| 发现者 | 集成 DeepSeek V4 时发现（catalog.yaml 配置审查） |

## 问题描述

**现象**: catalog.yaml 中 V4 profile 的 `kv_cache_dtype` 配置为 `auto`（解析为 BF16），vLLM 启动时 assert 失败导致服务崩溃。`model_path` 指向了不存在的 `safetensor_weights` 子目录。

**根因**:
1. vLLM 在 `deepseek_v4_attention.py` 中硬编码了 `assert kv_cache_dtype.startswith("fp8")`，V4 的 FlashMLA sparse kernel 只支持 FP8 KV cache
2. V4 的 model 目录结构与 V2/V3 不同，权重直接放在模型根目录下而非 `safetensor_weights/` 子目录

**表现**:
- `kv_cache_dtype` 错误 → vLLM 启动时直接 assert crash
- `model_path` 错误 → vLLM 找不到 `config.json`，无法加载模型

## 约束定义

**必须满足的条件**:
1. `kv_cache_dtype` 必须设置为 `fp8`（vLLM 会自动转为 `fp8_ds_mla`）
2. `kv_cache_dtype_slug` 必须为 `kvfp8`（影响文件命名和仿真配置推导）
3. `model_path` 必须指向包含 `config.json`（含 `model_type: "deepseek_v4"`）和完整 safetensors 权重分片的目录
4. V4 的 `block_size: 64` 只影响 SWA cache，Full MLA 的 block_size=256 由 vLLM 内部硬编码，不可配置

**违反后果**: vLLM 启动失败（assert crash 或文件未找到错误），bench config 无法运行。

**评估方法**:
```bash
# 检查 kv_cache_dtype
grep "kv_cache_dtype" automation/configs/vllm/catalog.yaml | grep -i "deepseekv4" -A 5
# 确认返回 fp8，不是 auto 或 bf16

# 检查 model_path 存在性
ls <model_path>/config.json
# 确认 config.json 存在

# 检查 config.json 内容
cat <model_path>/config.json | python3 -c "import json,sys; c=json.load(sys.stdin); assert c.get('model_type') == 'deepseek_v4'"
```

## 代码位置

- vLLM assert: `.venv/.../vllm/model_executor/layers/deepseek_v4_attention.py` L697-699
- KV cache 自动转换: `.venv/.../vllm/model_executor/layers/deepseek_v4_attention.py` L700（任何 `fp8*` → `fp8_ds_mla`）
- Block size 硬编码: `.venv/.../vllm/v1/attention/backends/mla/flashmla_sparse.py` (Full MLA=256)
- catalog.yaml: `automation/configs/vllm/catalog.yaml` V4 profiles (L542+)

## 标准解决方案

**正确做法**:
```yaml
# catalog.yaml 中 V4 profile
serve:
  kv_cache_dtype: fp8
  kv_cache_dtype_slug: fp8
model:
  model_path: /data_gpu/models/share_data/modelzoo/weights/llm/deepseek/DeepSeekV4/DeepSeek-V4-Pro

# kv_cache 轴配置
- name: kv_cache
  values:
    - slug: kvfp8
      patch:
        serve:
          kv_cache_dtype: fp8
          kv_cache_dtype_slug: fp8
```

**常见错误做法**:
1. `kv_cache_dtype: auto` — 解析为 BF16，触发 assert crash
2. `kv_cache_dtype: bf16` — 直接 assert crash
3. `model_path` 指向 `safetensor_weights` 子目录 — V4 的权重直接在根目录，无此子目录
4. 从 V2/V3 的 catalog 配置复制时未修改 KV cache dtype — V2/V3 用 `auto`（BF16），V4 必须用 `fp8`

**自动化建议**: 在 `generate_configs.py` 中增加校验：当 `model_type == "deepseek_v4"` 时，强制 `kv_cache_dtype` 以 `fp8` 开头，否则报错。

## 相关关键点

- KP-0007: Block Size 约束因 Attention Backend 而异（V4 的 block_size=64 只影响 SWA）
- KP-0039: TileLang Sparse Attention Kernel Block Size 必须是 64 的倍数
- KP-0044: Serving vs Simulation 算子差异（serving 端 vLLM 使用 FP8 KV cache，simulation 需对齐）

## 发现过程

在审查 V4 bench config 时发现 `kv_cache_dtype: auto` 和 `kv_cache_dtype_slug: bf16`。通过阅读 vLLM 源码确认 V4 强制要求 FP8 KV cache（`assert kv_cache_dtype.startswith("fp8")`）。同时发现 `model_path` 指向了不存在的 `safetensor_weights` 子目录，实际正确路径为 DeepSeekV4/DeepSeek-V4-Pro（64 个 safetensors 分片，约 805GB）。
