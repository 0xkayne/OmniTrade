# KP-0042: `attn_layer_patterns` 不能表达逐层稀疏注意力 — 使用模型特定元组

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0042 |
| 日期 | 2026-05-18 |
| 阶段 | attention |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 泛化标签 | per_layer_attn_mechanism |
| 触发条件 | `新模型有非 SWA/full 交替的逐层注意力变化（如 compress_ratios, per-layer sparse patterns）` |
| 发现者 | DeepSeek V4 attn_layer_patterns 分析 |

## 问题描述

**现象**: V4 继承了 V3.2 的 `AttnPatternConfig.make_same_layers(61, "full")`，但 V4 实际有 3 种逐层注意力类型：C128（31 层）、C4（29 层）、SWA（1 层）。`attn_layer_patterns` 无法表达这种差异。

**根因**: `attn_layer_patterns`（`ontos/config/attn_pattern_config.py`）仅为简单的 SWA/full 交替设计（如 Gemma-2 的 full+sliding 模式），支持 `FULL` 和 `SLIDING` 两种类型。V4 的 `compress_ratios` 元组表达了更复杂的逐层行为：ratio=0 为 SWA、ratio=4 为 SWA+C4+TopK、ratio=128 为 SWA+C128 dense。

**影响**:
- `sklearn_execution_time_predictor.py:193` 使用 `attn_layer_patterns.file_suffix` 决定是否加载 extra pattern attention file — V4 不会加载 sliding window 文件
- KV Cache Manager 使用 `attn_layer_patterns.use_sliding_window` 决定是否对 SWA 层做滑动窗口驱逐 — V4 的 SWA 层不会触发驱逐
- 但 V4 的实际逐层行为由 `compress_ratios` 在 `V4Attention.__init__` 中正确驱动，因此仿真结果不受影响

## 约束定义

**必须满足的条件**: 当新模型的逐层注意力变化由模型特定参数（如 `compress_ratios`）驱动时，该参数必须是 primary mechanism，不要尝试将语义映射到 `attn_layer_patterns`。

**禁止操作**: 不要将 `attn_layer_patterns` 的 layer_patterns 设置为混合 FULL/SLIDING 来表达 V4 的 C4/C128/SWA，因为：
1. `AttnPatternConfig` 只支持 FULL 和 SLIDING 两种类型，无法表达 C4/C128
2. 这样做会导致 predictor 加载不存在的 `_sliding` 后缀 CSV 文件
3. V3.2 也使用 all-FULL 的 `attn_layer_patterns`（有 sparse attention 但不通过此机制表达）

**正确做法**: 保持 `attn_layer_patterns` 为 all-FULL（与 V3.2 一致），添加注释说明 per-layer 行为由模型特定字段驱动。

## 代码位置

- 文件路径: `ontos/config/attn_pattern_config.py` — AttnPatternConfig 定义（仅支持 FULL/SLIDING）
- 文件路径: `ontos/config/model_config.py:228-229` — V3.2 设置 all-FULL（先例）
- 文件路径: `ontos/config/model_config.py:268` — V4 保持 all-FULL + 注释
- 文件路径: `ontos/execution_time_predictor/models/v4_attention.py:60` — `compress_ratios[layer_idx]` 驱动实际行为

## 标准解决方案

**正确做法**: 使用模型特定的 per-layer 参数（如 `compress_ratios`）驱动逐层行为，保持 `attn_layer_patterns` 为 all-FULL。在 config 中添加注释说明两套机制的分工。

**设计决策**: V4 的 KV Cache Manager 可能因此不会对 SWA 层做滑动窗口驱逐（内存估算偏高），但这是可接受的——V4 只有一层 SWA，影响极小。

## 相关关键点

- KP-0009: compress_ratios 每层定义压缩类型（正确机制）
- KP-0006: attention backend 扩展边界（backend 选择不依赖 attn_layer_patterns）

## 发现过程

V4 集成时检查 `attn_layer_patterns` 是否应反映 V4 的 3 种注意力类型。发现 V3.2 也使用 all-FULL 但有 sparse attention（`index_topk=2048`），确认了 per-layer sparse 行为不通过 `attn_layer_patterns` 表达的设计模式。
