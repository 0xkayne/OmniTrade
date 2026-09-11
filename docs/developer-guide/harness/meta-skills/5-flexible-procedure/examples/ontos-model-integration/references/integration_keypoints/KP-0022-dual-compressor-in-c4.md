# KP-0022: V4 C4 层有两个独立 Compressor

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0022 |
| 日期 | 2026-05-14 |
| 维度 | attention |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 触发条件 | 模型有 Indexer（compress_ratio == 4 且有 index_topk 参数） |
| 泛化标签 | dual_compressor |
| 发现者 | 通过分析 V4 C4 层 source code 中 Compressor 的实例化逻辑发现 |

## 问题描述

**现象**: V4 的 C4 层有两个 Compressor 实例：一个给 attention 的 compressed KV 用（compress_ratio=4, rotate=False），一个给 Indexer 内部用（compress_ratio=4, rotate=True，带 Hadamard rotation）。如果只建模一个 Compressor，会遗漏 indexer 内部 compressor 的开销。

**根因**: Indexer 需要独立构建 compressed KV 用于 top-k 打分，它的 Compressor 与 attention 的 Compressor 参数不同（rotate=True 导致额外的 Hadamard transform + FP4 量化）。

**表现**: 低估 C4 层每步总计算量，遗漏了 indexer compressor 的 2 个 GEMM + overlap transform + softmax + sum 开销。

## 约束定义

**必须满足的条件**: 当模型的 attention 层有 Indexer 机制时，必须确认 Indexer 内部是否有独立的 Compressor。如果有，Indexer 的 profile 必须包含其内部 compressor 的完整流程耗时。

**违反后果**: 低估 C4 层每步总计算量，影响 decode 阶段耗时预测的准确性。

**评估方法**: 验证 C4 层的 execution plan 中 indexer op 的 profile 包含了其内部 compressor 的耗时。具体检查 indexer 的 profile 数据是否涵盖了 wkv + wgate + ape + softmax + sum 的完整计算。

## 代码位置

- 文件路径: `ref/deepseek-v4/model.py` (Compressor 实例化位置)
- 文件路径: `ontos/execution_time_predictor/ops/` (需要 indexer op 定义)

## 标准解决方案

**正确做法**:
1. 在 indexer 的 profile 中包含其内部 compressor 的完整流程耗时
2. 或将 indexer（含内部 compressor）作为一个整体 op profile
3. 两个 Compressor 的参数差异（rotate=True/False）需要在 profile 参数中体现

**常见错误做法**:
- 只建模 attention 的 compressor，忽略 indexer 内部也有一个 compressor
- 假设两个 Compressor 的耗时相同（实际上 rotate=True 带来额外开销）

**自动化建议**: 检查新模型的 attention layer 中 Compressor 的实例化数量。如果大于 1，确认每个 Compressor 的用途和参数差异，确保全部纳入 profile。

## 相关关键点

- KP-0020: 异构融合 kernel 导致 op 无法独立 profile (同为 V4 attention 结构复杂性)
- KP-0009: 每层是 SWA+compressed 组合而非互斥 (基础注意力架构约束)

## 发现过程

通过分析 V4 source code 中 C4 层的 Compressor 实例化逻辑发现。在 `model.py` 中搜索 Compressor 的创建位置，发现 C4 层的 `__init__` 中有两个独立的 `Compressor(...)` 调用，一个用于 attention，一个用于 indexer，参数不同。详见 `docs/v4_profiling_challenges.md`。
