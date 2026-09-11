# KP-0010: C4 压缩有重叠感受野（stride=4, receptive_field=8）

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0010 |
| 日期 | 2026-05-13 |
| 阶段 | attention |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 发现者 | 通过 Together AI 博客和 DeepSeek 论文发现 |

## 问题描述

**现象**: 容易误解 C4 的压缩为"每 4 个 token 独立压缩为 1 个 entry"。实际上 C4 的压缩窗口有 50% 重叠。
**根因**: C4 的 stride=4（每 4 token 产出 1 个 compressed entry），但每个 entry 的感受野是 8 个 token。相邻 compressed entry 的感受野在边界重叠：

```
Token 序列:   [0 1 2 3 4 5 6 7 8 9 10 11 12 ...]
Entry 0:      [0 1 2 3 4 5 6 7] → compressed_0
Entry 1:              [4 5 6 7 8 9 10 11] → compressed_1
Entry 2:                      [8 9 10 11 12 13 14 15] → compressed_2
```

Together AI 博客确认："Each entry summarizes an 8-token neighborhood, so adjacent compressed entries overlap at the boundaries." 这使得 query 选择 compressed entry 时获得的是**局部邻域摘要**，而非孤立 token 位置的压缩。

C128 层没有这种重叠——stride=128，感受野也是 128。

**表现**: 如果忽略重叠，compressed pool 的大小计算会出错。对于 N 个 token：
- 无重叠假设: N/4 个 compressed entries
- 实际（有重叠）: 也是 N/4 个（stride 仍是 4），但每个 entry 编码了更多上下文

更重要的是，overlap transform 在 Context Parallelism 下跨越 rank 边界，需要 halo exchange。

## 约束定义

**必须满足的条件**:
1. C4 的 compressed entries 数量 = floor(N/4)，不是 floor(N/8)（stride 决定数量，不是感受野）
2. 每个 compressed entry 编码了 8 个 token 的信息，而非 4 个
3. Compressor 需要维护重叠状态（overlap buffer），大小 = 每层 4 token 的中间状态
4. C128 无重叠，不需要 overlap buffer

**违反后果**: Compressed pool 大小计算可能正确（因为 entries 数量由 stride 决定），但 compressor 的状态管理（ring buffer 大小、中间缓冲区）会出错。更关键的是，如果模拟 CP 场景，需要正确处理跨 rank 边界的 overlap。

**检查方法**: 验证 C4 的 ring buffer / compressor state 包含 overlap 位置（4 token 的 overlap 状态）。

## 代码位置

- 文件路径: `ontos/profiling/attn_backend/` (V4 wrapper 的 compressor 模拟)
- 文件路径: `ontos/execution_time_predictor/` (compressor 耗时建模)

## 标准解决方案

**正确做法**:
1. Profiling 时用 FlashMLA 的 hybrid attention 接口，compressor 的 overlap 由 kernel 内部处理
2. 模拟时只需知道 compressor 每 4 步触发一次，不需要手动跟踪 overlap 状态
3. 如果未来需要建模 CP 场景，需要在 attention 的 collectives 部分增加 halo exchange 逻辑

**常见错误做法**:
- 把 C4 等同于"4 个 token 压缩为 1 个"的简单平均 → 忽略了感受野是 8 不是 4
- 忘记 C4 有 overlap 而 C128 没有 → 混淆两者的 compressor 逻辑
- 把 overlap 的额外开销记入 KV cache 大小 → overlap 状态不在 compressed pool 中，在 ring buffer 中

**自动化建议**: 检查 V4 attention wrapper 的 compressor 参数是否正确设置了 overlap stride 和 receptive field。

## 相关关键点

- KP-0009: V4 每层是 SWA + 压缩 Attention 的组合
- KP-0008: Compressor/Indexer 需要独立 profiling

## 发现过程

Together AI 博客的图示清楚展示了 C4 的 overlap："CSA compresses context with stride 4, but each compressed entry is built from a slightly wider receptive field. In V4's configuration, each entry summarizes an 8-token neighborhood, so adjacent compressed entries overlap at the boundaries." SGLang 博客提到 CP 场景下 overlap 跨越 rank 边界需要特殊处理。
