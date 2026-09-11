# KP-0011: C4 用 Sparse Top-K 选择，C128 用 Dense Attention

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0011 |
| 日期 | 2026-05-13 |
| 阶段 | attention |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 发现者 | 通过综合分析多个技术文档发现 |

## 问题描述

**现象**: 容易认为 C4 和 C128 只是压缩比不同，attention 计算方式相同。实际上它们的 attention 策略完全不同。
**根因**: 两者的核心区别不在压缩比，而在 attention 的选择策略：

| 特性 | C4 (CSA) | C128 (HCA) |
|------|----------|------------|
| 压缩比 | 4:1 | 128:1 |
| Attention 策略 | **Sparse top-k** (选 512 个) | **Dense** (全部 attend) |
| 需要 Indexer | 是 | 否 |
| 每步 read 量 | O(top_k) = O(512) | O(seq_len/128) |
| 适用场景 | 精细的局部检索 | 全局粗粒度上下文 |

C4 的 indexer 对每个 query 独立地从 ~seq_len/4 个 compressed entries 中选 top-512 个最相关的。这意味着 C4 的 attention 计算量与序列长度**几乎无关**（只与 top_k=512 有关），而 C128 的 attention 计算量随序列线性增长（但基数很小：seq_len/128）。

在 1M token 时：C4 pool 有 ~250K entries，但只读 512 个；C128 pool 有 ~8K entries，全部读。

**表现**: 如果把 C4 和 C128 的 attention 耗时用同一个公式建模，会在长序列时严重高估 C4 的耗时（因为误以为要 attend 所有 compressed entries）。

## 约束定义

**必须满足的条件**:
1. C4 的 attention 耗时 ∝ (batch_size, top_k=512)，与 compressed_kv_len 几乎无关
2. C128 的 attention 耗时 ∝ (batch_size, compressed_kv_len / 128)
3. C4 需要额外的 indexer 耗时（Lightning TopK），C128 不需要
4. 两者的 profiling 必须分开进行

**违反后果**: C4 层的 attention 耗时在长序列时被高估 ~500 倍（250K / 512），导致模拟的 decode throughput 严重偏低。

**检查方法**: 验证 V4 的 execution plan 中 C4 层的 attention op 使用固定 top_k 参数而非 compressed_kv_len。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/ops/` (需新增 V4C4Attention 和 V4C128Attention 两个独立 op)
- 文件路径: `ontos/profiling/attn_backend/` (C4 和 C128 可能需要不同的 wrapper 或不同参数)

## 标准解决方案

**正确做法**:
1. 创建两个独立的 attention op：`V4C4Attention`（sparse, top_k=512）和 `V4C128Attention`（dense）
2. `V4C4Attention` 的 feature key 使用 `(batch_size, top_k)` 而非 `(batch_size, kv_len)`
3. C4 层额外添加 `V4Indexer` op，耗时 ∝ (batch_size, compressed_kv_len)
4. 分别 profiling C4 和 C128 的 kernel

**常见错误做法**:
- 用同一个 op 并传入 `compress_ratio` 参数区分 → 隐藏了两种根本不同的 attention 策略
- 把 C4 的 kv_len 参数设为 compressed_kv_len（250K）而非 top_k（512）→ 耗时高估
- 忽略 indexer 开销 → 低估 C4 层的总耗时

**自动化建议**: 检查 V4 的 attention op 中，C4 层的 kv_cache_size 参数不超过 top_k 值（512）。

## 相关关键点

- KP-0009: V4 每层是 SWA + 压缩 Attention 的组合
- KP-0008: Compressor/Indexer 需要独立 profiling
- KP-0010: C4 压缩有重叠感受野

## 发现过程

SGLang 博客描述了 Lightning TopK 的优化（256K 候选 15μs），明确指出这是 C4 层特有的操作。Together AI 博客区分了 CSA（"sparse top-k over compressed KV"）和 HCA（"dense over 128:1-compressed KV"）。HuggingFace 的 `DeepseekV4CSACache` 包含 indexer state 而 `DeepseekV4HCACache` 不包含。
