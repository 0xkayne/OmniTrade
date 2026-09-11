# KP-0017: SWA 的 KV Cache 有硬上限，Memory Planner 需感知

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0017 |
| 日期 | 2026-05-14 |
| 维度 | kv_cache |
| 严重度 | P1 |
| 状态 | seed |
| 触发条件 | `sliding_window IS NOT NONE` |
| 泛化标签 | swa_bounded_kv |
| 发现者 | 种子 KP (代码约定提取) |

## 问题描述

**现象**: 使用滑动窗口注意力 (SWA) 的模型，每层的 KV cache 有一个硬上限 `sliding_window`，不会随序列长度无限增长。
**根因**: SWA 只保留最近 `sliding_window` 个 token 的 KV，超出窗口的 KV 被丢弃。这意味着 KV cache 的最大 size 是确定的，不受 `max_position_embeddings` 影响。
**表现**: 如果 memory planner 按 `max_position_embeddings` 分配 KV cache，会严重高估内存需求；如果按 `sliding_window` 分配，需要确保 block 分配逻辑正确处理上限。

## 约束定义

**必须满足的条件**:
```
SWA 层的 KV cache 最大 token 数 = min(sliding_window, max_position_embeddings)
实际通常 = sliding_window (远小于 max_position_embeddings)

Memory Planner 计算 per-token KV size 时:
  SWA 层: kv_per_token = 2 × num_kv_heads × head_dim × dtype_bytes
  KV cache blocks = ceil(sliding_window / block_size)

Block 分配:
  最大 block 数 = ceil(sliding_window / block_size)
  不需要预分配 max_position_embeddings 长度的 blocks
```

**违反后果**: Memory planner 按全序列长度分配 KV cache → 高估内存 → 模拟出的 batch size 偏小 → 吞吐量低估。

**评估方法**:
```python
# 验证 KV cache 分配不超过 sliding_window
max_blocks = ceil(sliding_window / block_size)
actual_allocated = kv_cache_manager.get_max_blocks_for_group("swa_group")
assert actual_allocated <= max_blocks, \
    f"SWA KV cache allocated {actual_allocated} blocks, max expected {max_blocks}"
```

## 代码位置

- 文件路径: `ontos/kv_cache/kv_cache_block_pool.py`
- 相关类: `KVCacheBlockPool`, `KVCacheManager`
- 相关逻辑: block 分配策略, memory planner

## 标准解决方案

**正确做法**:
1. 在 KV cache group 定义中，SWA group 的 `max_num_blocks` 由 `sliding_window` 决定
2. Memory planner 计算 per-request 内存时，SWA 层按 `sliding_window` 而非 `max_seq_len` 计算
3. SWA 层的 KV cache 可以更积极地进行 block 回收 (滚动窗口)

**常见错误做法**:
- 对 SWA 层和 Full attention 层使用相同的 block 分配策略
- 忽略滑动窗口大小可能是逐层变化的 (如 Gemma-2/3: 交替 SWA/Full)
- 忘记 SWA 的 block 回收机制可以降低峰值内存

## 相关关键点

- KP-0005: SWA 可能反直觉地成为内存瓶颈 (高并发时 per-entry 用原始 KV 大小)
- KP-0004: Decode 阶段不同 KV cache group 增长速率不同
- KP-0002: SWA 层不支持传统 prefix caching

## 发现过程

种子 KP — 从框架 KV cache 管理逻辑中提取。SWA 被越来越多的模型采用 (Mistral, Gemma-2/3, Qwen-2.5 的部分变体，DeepSeek-V4)，但 KP 库中之前只有 V4 特定的 SWA 相关 KP (KP-0004/0005)，缺少通用 SWA 约束。
