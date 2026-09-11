# KP-0044: 区分 Serving-Only 与 Simulation-Required 操作

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0044 |
| 日期 | 2026-05-18 |
| 阶段 | profiling |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 泛化标签 | serving_vs_simulation_ops |
| 触发条件 | `参考模型代码中出现仅在 serving 时需要的 op（如 fast_hadamard_transform）` |
| 发现者 | DeepSeek V4 依赖安装分析 |

## 问题描述

**现象**: V4 的参考模型代码 `ref/deepseek-v4/model.py:250` 导入 `fast_hadamard_transform`，KP-0027 Finding 6 将其标记为需要 profiling 的 "missing op"。但实际上 `fast_hadamard_transform` 仅在 vLLM real serving 时使用，ontos 的 profiling 和 simulation 完全不需要它。

**根因**: 参考模型中的 op 有三种角色，但集成时未明确分类：
- **(A) 需显式 profiling**: 独立 kernel 有自己的 CSV 列（如 Linear, Attention）
- **(B) 隐含在融合 kernel profiling 中**: 子 op 时间被端到端测量覆盖（如 hadamard, sinkhorn）
- **(C) 仅 serving 需要**: vLLM 执行路径依赖但 simulator 不执行（如 fast_hadamard_transform）

**`fast_hadamard_transform` 属于 (C)**:
- ontos profiling 只测 attention forward pass 端到端时间（`attention_wrapper.py:244`）
- ontos simulation 用 sklearn predictor 预测时间，不执行实际 CUDA kernel
- `fast_hadamard_transform` 的 hadamard 变换在 vLLM serving 中由 `ref/deepseek-v4/model.py:250` 的 `rotate_activation()` 调用，但在 profiling wrapper（`sparse_attn_c4_wrapper.py`）中不调用

## 约束定义

**必须满足的条件**: 集成新模型时，对参考模型中出现的每个 op 进行分类：
1. **(A) 需显式 profiling**: 必须有对应的 profiling wrapper 和 CSV 列
2. **(B) 隐含在融合 kernel 中**: 时间已覆盖，不需要独立 profiling（KP-0043）
3. **(C) Serving-only**: 不影响 profiling 和 simulation，不需要安装或 profiling

**判断标准**: 如果一个 op 只在 vLLM 的 serving 代码路径中出现，且 ontos profiling wrapper 不调用它，则为 (C) 类。

## 代码位置

- 文件路径: `ref/deepseek-v4/model.py:250` — `from fast_hadamard_transform import hadamard_transform`（serving-only）
- 文件路径: `ontos/profiling/attn_backend/sparse_attn_c4_wrapper.py` — profiling wrapper 不调用 hadamard_transform
- 文件路径: `ontos/profiling/attention/attention_wrapper.py:244` — profiling 只测 `forward()` 端到端

## 标准解决方案

**正确做法**: 在集成计划的 op 分析阶段，对每个 op 标注分类 (A)/(B)/(C)。只有 (A) 类 op 需要创建 profiling wrapper 和 CSV。(B) 类 op 在 execution plan 中用 `ElementWiseOp` 占位（时间为 0 或含在融合 kernel 中）。(C) 类 op 不需要在 simulator 中实现。

**安装依赖**: (C) 类 op 的 Python 包（如 `fast_hadamard_transform`）不需要安装在 ontos 的 venv 中。仅在跑 vLLM real serving/benchmark 时需要。

## 相关关键点

- KP-0020: 异构融合 kernel（端到端测量原则）
- KP-0027: V4 profiling ops 分析（**Finding 6 需修正**：`fast_hadamard_transform` 不是 "missing op"，而是 serving-only op）
- KP-0043: ElementWiseOp 缺失列填 0（(B) 类 op 的框架支持）

## 发现过程

尝试安装 `fast_hadamard_transform` 时发现无 cu128 预编译 wheel，源码编译失败。追踪代码发现 ontos profiling/simulation 从不调用此包。确认其为 serving-only 依赖后，标记为不影响 profiling。同时发现 KP-0027 Finding 6 的分类有误。
