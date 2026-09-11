# KP-0021: 多流重叠导致串行累加高估耗时

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0021 |
| 日期 | 2026-05-14 |
| 维度 | attention |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 触发条件 | 模型在同一 layer 的 forward 中有多个可并行执行的 GEMM/compute op（如 Q proj 与 Compressor 与 Indexer） |
| 泛化标签 | cross_op_parallelism |
| 发现者 | 通过对比 Llama3-8B 与 DeepSeek V4 的 kernel 融合模式分析发现 |

## 问题描述

**现象**: DeepSeek V4 的 decode 阶段，Q projection、Compressor GEMM、Indexer 在不同 CUDA stream 上并行执行。仿真器按线性序列累加这三个 op 的耗时，高估近 2 倍。

**根因**: 当前 ExecutionPlan 是严格的线性列表，只有 add(op) 操作，所有 op 串行累加。没有"并行 op 组"的概念，无法表达多个 op 在不同 stream 上同时执行的关系。

**表现**: V4 decode 每步仿真耗时约 95us（串行累加），实际约 50us（并行执行取 max），误差近 2 倍。随 batch 增大，三个 op 的耗时差异缩小，误差可能降低但不会消失。

## 约束定义

**必须满足的条件**: 当新模型的同一 layer forward 中存在多个无数据依赖的 GEMM/compute 操作时（即它们的输入不依赖彼此的输出），ExecutionPlan 必须将这些 op 组织为 ParallelOpGroup，组内耗时取 max 而非 sum。

**违反后果**: 串行累加并行执行的 op 会显著高估 decode 阶段耗时，误差可达 2 倍或更多，取决于并行 op 的数量和各自的耗时比例。

**评估方法**: 检查新模型的 attention forward 中是否存在多个独立的 GEMM 可以并行执行。如果存在，验证 ExecutionPlan 中这些 ops 被组织为 ParallelOpGroup 而非串行序列。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/` (ExecutionPlan 数据结构需要扩展)
- 文件路径: `ontos/entities/batch_stage.py` (耗时计算逻辑需要支持并行组)
- 参考: SGLang multi-stream overlap 实现

## 标准解决方案

**正确做法**:
1. 扩展 ExecutionPlan 支持 ParallelOpGroup，组内取 max 而非 sum
2. Profiling 层不变，仍独立测量每个 op 的耗时
3. Simulator 层负责决定哪些 op 可以并行，并应用 max 规则
4. 并行关系的判断依据：两个 op 之间没有数据依赖（一个的输出不是另一个的输入）

**常见错误做法**:
- 在 profiling 层尝试测量并行执行的组合，参数空间爆炸
- 忽略并行直接串行累加，高估耗时
- 在 profiling 层为并行组合创建新的 profiling 类别，违反三分类架构原则

**自动化建议**: 分析新模型 forward 代码中的数据依赖图，自动检测无依赖的 op 对，标记为可并行。

## 相关关键点

- KP-0020: 异构融合 kernel 导致 op 无法独立 profile (同为 V4 profile 精度问题)
- KP-0023: 三分类 profiling 架构足以支撑 V4 (架构层面的约束)

## 发现过程

通过对比 Llama3-8B 和 DeepSeek V4 的 kernel 执行模式发现。Llama3-8B 所有 kernel 在同一 CUDA stream 串行执行，不存在跨 op 并行。V4 使用 SGLang multi-stream overlap，Q proj、Compressor、Indexer 分别在不同 stream 上并行执行。详见 `docs/v4_profiling_challenges.md`。
