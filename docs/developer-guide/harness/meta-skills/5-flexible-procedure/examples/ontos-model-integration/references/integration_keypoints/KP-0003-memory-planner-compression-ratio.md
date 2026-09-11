# KP-0003: Memory Planner 的 Per-Token KV 计算必须适配压缩比

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0003 |
| 日期 | 2026-05-13 |
| 阶段 | kv_cache |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 发现者 | 通过代码链路追踪发现 |

## 问题描述

**现象**: 如果 `get_kv_cache_memory_per_token()` 返回的仍然是 MLA 的固定值（`kv_lora_rank + qk_rope_head_dim`），V4 的 block 总数会严重低估，导致模拟高估可用容量。
**根因**: Memory planner 的计算链是：
```
num_tokens = (可用显存 - 参数占用) / kv_per_token
num_blocks = num_tokens // block_size
```
V4 的 `kv_per_token` 不再是固定值。三层混合：
- SWA: 每 token 存原始 KV，但只占 128 token（固定）
- C4: 每 4 token 存 1 个 compressed entry
- C128: 每 128 token 存 1 个 compressed entry

必须计算一个**等价的加权平均 per-token 占用**。
**表现**: 如果不改，`num_gpu_blocks` 会被高估 5-10 倍（因为实际占用比计算值大），模拟的 batch size 和 concurrency 会严重偏高。

## 约束定义

**必须满足的条件**: V4 model config 的 `get_kv_cache_memory_per_token()` 必须返回考虑压缩比的等效值。计算方式：
```
effective_per_token = (
    swa_size * swa_layer_count +           # SWA: 128 token 固定窗口
    c4_size / 4 * c4_layer_count +        # C4: 4x 压缩
    c128_size / 128 * c128_layer_count    # C128: 128x 压缩
) / total_layers
```

其中 `swa_size`, `c4_size`, `c128_size` 是每种类型每个 compressed entry 的字节大小。

**违反后果**: `num_gpu_blocks` 计算错误 → 所有下游的 block 分配、capacity planning、throughput 预测全部不准确。

**检查方法**: 对比 V4 模型的 `get_kv_cache_memory_per_token()` 返回值与手动计算值（约 7.9x 压缩比，即标准 MLA 的 ~12.7%）。

## 代码位置

- 文件路径: `ontos/utils/memory_planner.py` (消费者)
- 文件路径: `ontos/execution_time_predictor/models/deepseek_v2.py` (提供者: `get_kv_cache_memory_per_token`)
- 关键函数/类: `MemoryPlanner.get_max_kv_cache_size_in_tokens()`, `DeepSeekV2PredictorModel._get_kv_cache_size_per_token_impl()`
- 行号范围: memory_planner.py L34-L54, deepseek_v2.py L71

## 标准解决方案

**正确做法**:
1. V4 model config 新增 `effective_compression_ratio` 属性（所有层的加权平均）
2. `get_kv_cache_memory_per_token()` 对 V4 返回 `base_per_token / effective_compression_ratio`
3. 或者更精确地：按 SWA/C4/C128 的 layer 比例分别计算后加权求和

**常见错误做法**:
- 用 MLA 的 `kv_lora_rank + qk_rope_head_dim` 直接作为 V4 的 per-token 占用 → 忽略了 token 轴压缩
- 只考虑 compressed pool，忘记 SWA 的固定 128 token 窗口占用 → 低估 SWA 层贡献
- 用最大压缩比 128 而不是加权平均 → 低估 C4 层的占用

**自动化建议**: 新增 model config 时，如果有 `compress_ratios` 字段，自动验证 `get_kv_cache_memory_per_token()` 的返回值不为 MLA 默认值。

## 相关关键点

- KP-0001: V4 异构 KV cache 分组建模
- KP-0004: Decode 阶段三个池的增长速率不同

## 发现过程

追踪 `KVCacheManager` 的参数 `num_gpu_blocks` 的完整来源链：`memory_planner.py` → `base_replica_scheduler.py` → `cache_config.num_blocks`。发现计算公式完全依赖 `kv_per_token`，而当前 V3 的实现只考虑了 MLA head 维度压缩，没有 token 轴压缩。
