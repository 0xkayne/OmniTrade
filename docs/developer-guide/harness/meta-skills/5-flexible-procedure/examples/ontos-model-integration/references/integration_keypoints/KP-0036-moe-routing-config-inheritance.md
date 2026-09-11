# KP-0036: MoE 路由配置继承 — 扁平 vs 分组路由

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0036 |
| 日期 | 2026-05-18 |
| 阶段 | config |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 发现者 | DeepSeek V4 MLP profiling 分析 |

## 问题描述

**现象**: DeepSeek V4 使用扁平 top-k 路由（`n_group=1, topk_group=1, topk_method="greedy"`），但从 V2/V3 继承了分组路由参数（`n_group=8, topk_group=3, topk_method="noaux_tc"`）。未覆盖这些字段会导致：1) `has_e_score_correction_bias=True`（分配不存在的 correction bias tensor）；2) `use_grouped_topk=True`（路由器使用错误的分组选择逻辑）。

**根因**: V4 继承链 `DeepSeekV4ProModelConfig → DeepSeekV32ModelConfig → DeepSeekV3ModelConfig → DeepSeekV2ModelConfig`。V2 定义了分组路由字段，V4 不使用但未覆盖。`has_e_score_correction_bias` 属性检查 `topk_method == "noaux_tc"` 对 V4 误返回 True。

**表现**: MoE profiling 创建了不存在的 e_score_correction_bias → 可能不崩溃但逻辑错误；Router 使用错误的分组逻辑 → profiling 数据不反映真实行为。

## 约束定义

**必须满足的条件**: 当新模型的 MoE 路由策略与父类不同时，必须覆盖所有相关字段：`n_group`, `topk_group`, `topk_method`, `scoring_func`。

**违反后果**: 1) MoE profiling 创建了不存在的 e_score_correction_bias → 可能不崩溃但逻辑错误；2) Router 使用错误的分组逻辑 → profiling 数据不反映真实行为。

**检查方法**: 验证 `has_e_score_correction_bias` 与模型实际是否需要 correction bias 一致。

## 代码位置

- 文件路径: `ontos/config/model_config.py` — DeepSeekV4ProModelConfig (n_group, topk_group, topk_method 覆盖)
- 文件路径: `ontos/profiling/config/model_config.py` — has_e_score_correction_bias 属性
- 文件路径: `ontos/profiling/mlp/moe_timer.py:43-58` — MoETopKTimer.__init__() 中 use_grouped_topk 判断

## 标准解决方案

**正确做法**: 在子类 config 中显式覆盖所有与父类不同的 MoE 路由字段。

**常见错误做法**: 仅覆盖 `scoring_func` 和 `num_experts_per_tok`，忽略 `n_group`/`topk_group`/`topk_method`。

**自动化建议**: 新增 MoE 模型时，自动检查 `n_group`、`topk_group`、`topk_method` 是否与 `scoring_func` 策略一致。

## 相关关键点

- KP-0016: MoE Expert 参数量

## 发现过程

MLP profiling 分析发现 V4 的 `has_e_score_correction_bias` 错误返回 True（因为继承了 `topk_method="noaux_tc"`），导致 MoETopKTimer 分配不存在的 correction bias buffer。
