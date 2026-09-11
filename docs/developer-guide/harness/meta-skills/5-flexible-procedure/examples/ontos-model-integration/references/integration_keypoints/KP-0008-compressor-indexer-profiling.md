# KP-0008: V4 的 Compressor 和 Indexer 需要独立的 Profiling

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0008 |
| 日期 | 2026-05-13 |
| 阶段 | profiling |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 发现者 | 通过综合分析 SGLang/Together AI 文档发现 |

## 问题描述

**现象**: V4 的 attention 耗时不仅仅是 attention kernel 本身，还包括 compressor（KV 压缩）和 indexer（top-k 选择）。如果只 profile attention kernel，会低估每步总耗时。
**根因**: V4 每 4 步（C4 层）或每 128 步（C128 层）需要运行一次 compressor：
- **Flash Compressor**: 将 5 阶段压缩流水线融合为 1 次 on-chip pass，可达峰值带宽的 80%
- **Lightning Indexer**: 对 256K 候选做 top-512 选择，优化后约 15μs（batch_size=1）

SGLang 的多流重叠（hierarchical multi-stream overlap）将 Q projection、compressor GEMM、indexer 在不同 CUDA stream 上并行执行。但在小 batch 时，这些操作的串行开销仍然显著。

**表现**: 模拟的 decode 耗时偏低（遗漏了 compressor 和 indexer 的耗时），尤其在小 batch size 时误差最大。

## 约束定义

**必须满足的条件**: V4 的 execution time predictor 必须包含以下独立操作的耗时：
1. **SWA attention**: 耗时 ∝ (batch_size, window_size=128) — 几乎恒定
2. **C4 attention**: 耗时 ∝ (batch_size, top_k=512, compressed_kv_len/4)
3. **C128 attention**: 耗时 ∝ (batch_size, compressed_kv_len/128)
4. **Compressor**: 耗时 ∝ (batch_size)，每 4/128 步触发一次
5. **Indexer**: 耗时 ∝ (batch_size, compressed_kv_len)，仅 C4 层，每步触发

**违反后果**: Decode throughput 被高估，尤其在小 batch + 长序列时误差最大（compressor/indexer 开销占比更高）。

**检查方法**: 验证 V4 的 execution plan 包含 compressor 和 indexer op（而非只有一个 generic attention op）。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/ops/` (新增 op)
- 文件路径: `ontos/execution_time_predictor/models/` (V4 predictor model)
- 文件路径: `ontos/profiling/attn_backend/` (profiling wrapper)

## 标准解决方案

**正确做法**:
1. 为 compressor 创建独立的 profiling wrapper（或在 attention wrapper 的 `forward()` 中分两个计时区间）
2. 为 indexer 创建独立的 profiling wrapper
3. 在 V4 的 execution plan 中，按层类型组装：SWA-only / SWA+C4+compressor+indexer / SWA+C128+compressor
4. 用 SGLang 的多流重叠数据校准模拟的耗时

**常见错误做法**:
- 只 profile attention kernel，把 compressor/indexer 开销混入 attention 耗时 → 无法区分不同 batch size 下的开销比例
- 忽略 compressor 的触发频率（每 4/128 步一次），当作每步都运行 → 高估耗时

**自动化建议**: 验证 V4 的 execution plan 中每个 layer type 至少包含 attention op，且 C4 层额外包含 compressor + indexer op。

## 相关关键点

- KP-0001: V4 异构 KV cache 分组建模
- KP-0006: 新增 Attention Kernel 只需改 attn_backend

## 发现过程

SGLang 博客详细描述了 Flash Compressor（5 阶段融合为 1 pass，10x 加速）和 Lightning TopK（256K 候选 15μs）的优化。Together AI 博客指出短上下文 prefill 性能受 kernel 成熟度影响。这些操作在 V3/V2 中不存在，是 V4 新增的必要开销项。
