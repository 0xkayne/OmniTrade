# KP-0005: SWA 可能反直觉地成为内存瓶颈

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0005 |
| 日期 | 2026-05-13 |
| 阶段 | kv_cache |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 发现者 | 通过 Together AI 博客发现 |

## 问题描述

**现象**: 直觉上 V4 的 compressed KV cache（C4/C128）是显存瓶颈，但实际服务中发现 SWA 才是限制并发数的主要因素。
**根因**: Together AI 的实测数据：
- 完整 SWA 实现每 token 占 ~3.8KB，比 V3 的 MLA ~3.4KB 还大
- C4/C128 compressed cache 经过 4x-128x 压缩后非常小
- SWA 是**每层每个请求**都保留 128 token 的原始 KV，在高并发时累积
- 61 层 × 128 token × N 个并发请求 × 3.8KB/token = 大量显存

Together AI 通过优化 SWA 缓存策略，将单 B200 节点的容量从 1.2M token 提升到 3.7M token（3x 提升）。
**表现**: 模拟可能低估 SWA 的显存占用，导致高估并发能力。

## 约束定义

**必须满足的条件**: V4 的显存模型中，SWA 的每 token 占用不能按压缩后的大小计算。SWA 存的是**原始 KV**（未压缩），每 token 大小 = `2 × num_kv_heads × head_dim × dtype_size`（V4 用 MQA，num_kv_heads=1）。

**违反后果**: 低估 SWA 的显存占用 → 高估最大并发请求数 → 模拟的 throughput 偏高。

**检查方法**: 确认 SWA group 的 per-token KV 大小使用原始（未压缩）的 head_dim，而不是 compressed_dim。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/models/deepseek_v2.py` 或新的 V4 predictor
- 关键函数/类: `get_kv_cache_memory_per_token()`
- 相关: `ontos/utils/memory_planner.py`

## 标准解决方案

**正确做法**:
1. SWA pool 的 per-entry 大小 = 原始 KV 大小（MQA: `2 × 1 × head_dim × dtype`）
2. SWA pool 的总大小 = per-entry × 128 × num_layers × num_concurrent_requests
3. 在 memory planner 中分别计算 SWA 和 compressed 的占用

**常见错误做法**:
- 把 SWA 也按 compressed 的大小计算 → 低估 4-128 倍
- 认为只有 128 token 就忽略 SWA → 忘记乘以 num_layers × batch_size

**自动化建议**: 验证 V4 模型的 SWA 显存占用计算中使用了原始（未压缩）的 KV 大小。

## 相关关键点

- KP-0001: V4 异构 KV cache 分组建模
- KP-0002: Prefix caching 与混合 attention 的互斥性

## 发现过程

Together AI 博客明确指出："V4 的服务容量受 SWA 状态管理的支配远超 compressed CSA/HCA cache。" 这是反直觉的——压缩的 KV 不是瓶颈，反而是那个"小小的"128 token 窗口在高并发下累积成为瓶颈。这对模拟器的容量规划模型有直接影响。
