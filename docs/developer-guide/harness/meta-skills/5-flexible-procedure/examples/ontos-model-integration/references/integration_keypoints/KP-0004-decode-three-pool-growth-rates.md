# KP-0004: Decode 阶段三个 KV Cache 池的增长速率不同

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0004 |
| 日期 | 2026-05-13 |
| 阶段 | kv_cache |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 发现者 | 通过综合分析 SGLang/Together AI/HuggingFace 文档发现 |

## 问题描述

**现象**: 如果假设 V4 的 KV cache 随序列线性增长，会高估显存占用和 decode 延迟。
**根因**: Decode 时三个池的增长行为完全不同：
- **SWA pool**: 每步 +1 token KV，但达到 128 后恒定（FIFO 驱逐）
- **C4 compressed pool**: 每 **4 步** +1 compressed entry
- **C128 compressed pool**: 每 **128 步** +1 compressed entry
- **Ring buffer**: 固定大小（C4: 8 slot, C128: 128 slot），原地覆写

总 KV cache 增长速率 = 128（固定）+ seq_len/4（C4）+ seq_len/128（C128），而非 seq_len × layers。

**表现**: 长序列（>100K token）的显存占用和 attention 计算量被严重高估。

## 约束定义

**必须满足的条件**:
1. SWA 组的 block 需求上限 = `cdiv(128, block_size) + 1`，不随序列增长
2. Compressed 组的 block 需求 = `cdiv(seq_len, compression_ratio * block_size)`
3. Ring buffer 大小固定，不参与 block 分配

**违反后果**: 长序列模拟时显存估算偏高，导致模拟器认为 batch size 必须很小（过于保守），throughput 预测偏低。

**检查方法**: 验证 1M token 序列的总 block 需求 ≈ 128/16 + 1000000/(7.7×16) ≈ 8 + 8117 ≈ 8125 blocks（而非 1000000/16 = 62500 blocks）。

## 代码位置

- 文件路径: `ontos/kv_cache/base_kv_cache_manager.py`
- 关键函数/类: `KVCacheManager._compute_new_block_counts()`, `KVCacheManager._advance_sliding_window()`
- 行号范围: L284-L312, L314-L347
- 相关 enum/config: `_sliding_cap()` (L124-L127)

## 标准解决方案

**正确做法**:
1. SWA group: 现有 `_advance_sliding_window()` + `_sliding_cap()` 已正确处理，block 需求有上限
2. Compressed group: `_compute_new_block_counts()` 中用 `cdiv(required, compression_ratio)` 计算
3. Ring buffer: 不需要建模为 block，因为大小固定且很小

**常见错误做法**:
- 把 compressed pool 也当作线性增长 → 忽略压缩比
- 把 ring buffer 也纳入 block 分配 → 浪费且不必要
- 把 SWA 和 compressed 混在一个 group 里 → 无法分别管理增长速率

**自动化建议**: 单元测试中验证：给定 V4 模型和长序列，total block 需求的增长斜率 ≈ 1/effective_compression_ratio 而非 1。

## 相关关键点

- KP-0001: V4 异构 KV cache 分组建模
- KP-0003: Memory planner 的 per-token KV 计算必须适配压缩比

## 发现过程

综合 SGLang 博客（ShadowRadix 的 shadow/tombstone 机制）、Together AI 博客（三种 cache 类型的描述）、HuggingFace Transformers 的 `DeepseekV4CSACache`/`DeepseekV4HCACache` 实现（ring buffer 固定大小），确认三个池的增长行为完全不同。
