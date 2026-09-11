# KP-0001: V4 异构 KV Cache 需要分组建模而非单一 Pool

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0001 |
| 日期 | 2026-05-13 |
| 阶段 | kv_cache |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 发现者 | 通过对话探索发现（综合 SGLang/Together AI/vLLM 分析） |

## 问题描述

**现象**: 如果把 V4 的 SWA、C4、C128 三种 KV cache 当作统一的 block pool 管理，会导致 block 分配数量严重错误。
**根因**: V4 的三种 attention 类型产生完全不同的 KV cache 条目：
- SWA: 原始 KV，固定 128 token 窗口
- C4 (CSA): 4 token 压缩为 1 个 entry，用 top-512 sparse 选择
- C128 (HCA): 128 token 压缩为 1 个 entry，dense attention

同一个 token 位置，在不同层需要的 block 数量不同（差异达 128 倍）。
**表现**: 模拟结果中的 KV cache 容量、batch size 上限、throughput 全部不准确。

## 约束定义

**必须满足的条件**: `KVCacheGroupSpec` 必须增加 `compression_ratio` 字段，V4 的 `_build_groups()` 必须返回至少两组：
- Group 0: `KVCacheGroupSpec(sliding_window=128, compression_ratio=1)` — SWA
- Group 1: `KVCacheGroupSpec(sliding_window=None, compression_ratio=effective_ratio)` — Compressed

其中 `effective_ratio` 是所有层 compress_ratios 的加权平均（约 7.7）。

**违反后果**: Block 分配数量错误 → 内存容量估算错误 → 模拟的 concurrency 和 throughput 全部失真。

**检查方法**:
1. 验证 `KVCacheGroupSpec` 有 `compression_ratio` 属性且默认值为 1
2. 验证 V4 模型创建的 KVCacheManager 有 2 个 groups
3. 验证 compressed group 的 block 需求 = `cdiv(required_blocks, compression_ratio)`

## 代码位置

- 文件路径: `ontos/kv_cache/base_kv_cache_manager.py`
- 关键函数/类: `KVCacheGroupSpec`, `KVCacheManager._build_groups()`, `KVCacheManager._compute_new_block_counts()`
- 行号范围: L26-L36 (KVCacheGroupSpec), L96-L122 (_build_groups), L284-L312 (_compute_new_block_counts)
- 相关 enum/config: 无现有 enum，需新增 `compress_ratios` 到 model config

## 标准解决方案

**正确做法**:
1. `KVCacheGroupSpec` 增加 `compression_ratio: int = 1`
2. `_build_groups()` 增加 V4 分支，返回 `[SWA(compression_ratio=1), Compressed(compression_ratio=avg)]`
3. `_compute_new_block_counts()` 对 compressed group 做 `cdiv(required, ratio)` 除法
4. SWA group 的 sliding window eviction 完全复用现有 `_advance_sliding_window()` 逻辑

**常见错误做法**:
- 尝试创建三个 group（SWA + C4 + C128），每层单独管理 → 过度细化，模拟器不需要逐层精度
- 把 compression_ratio 放在 BlockPool 层面 → 压缩比是 per-group 语义，不是 per-pool 物理属性
- 忽略 SWA 的固定窗口，把 SWA 也当作无限增长 → 高估显存占用

**自动化建议**: 新增 model config 时，自动检查：如果模型有 `compress_ratios` 字段，验证 KVCacheManager 创建时传入正确的 `attn_patterns` 且 `_build_groups` 返回 2 个 group。

## 相关关键点

- KP-0002: Prefix caching 与混合 attention 的互斥性
- KP-0003: Memory planner 的 per-token KV 计算必须适配压缩比
- KP-0004: Decode 阶段三个池的增长速率不同

## 发现过程

通过深入分析 SGLang ShadowRadix 论文、Together AI 服务博客、vLLM hybrid KV cache manager 文档，对比 Ontos 当前 `KVCacheGroupSpec` 仅有 `sliding_window` 字段，确认 V4 的 `compression_ratio` 是必须新增的字段。SGLang 用三个物理池 + shadow 映射，但 Ontos 作为模拟器只需要两个逻辑 group 即可准确建模。
