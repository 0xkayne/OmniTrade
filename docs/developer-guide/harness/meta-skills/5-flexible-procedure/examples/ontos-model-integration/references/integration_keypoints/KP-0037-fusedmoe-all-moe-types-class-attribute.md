# KP-0037: FusedMoE ALL_MOE_TYPES 必须定义为类属性

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0037 |
| 日期 | 2026-05-18 |
| 阶段 | profiling |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 发现者 | MLP profiling 代码审查 |

## 问题描述

**现象**: `ontos/profiling/mlp/mlp_impl.py` 的 `FusedMoE.__init__()` 引用 `self.ALL_MOE_TYPES` 进行断言检查，但该类从未定义此属性。任何 MoE profiling 运行都会 `AttributeError: 'FusedMoE' object has no attribute 'ALL_MOE_TYPES'`。

**根因**: 可能是重构时遗漏。类方法中引用了 `ALL_MOE_TYPES` 但未在类体中定义。

**表现**: MoE profiling 运行时立即崩溃。

## 约束定义

**必须满足的条件**: 引用类属性前必须定义。

**违反后果**: MoE profiling 运行时立即崩溃。

**检查方法**: 运行 `FusedMoE(config, parallel_config, FFNType.MOE_NONE, "bf16")` 验证无 AttributeError。

## 代码位置

- 文件路径: `ontos/profiling/mlp/mlp_impl.py:581` — `assert ffn_type in self.ALL_MOE_TYPES`
- 需要在类体中定义 `ALL_MOE_TYPES = frozenset({FFNType.MOE_NONE, ...})`

## 标准解决方案

**正确做法**: 在 FusedMoE 类体中定义 `ALL_MOE_TYPES = frozenset({...})`。

**常见错误做法**: 无。

**自动化建议**: 对 profiling 类进行静态检查，确保引用的类属性均有定义。

## 相关关键点

无直接关联 KP，属于通用代码完整性约束。

## 发现过程

MLP profiling 分析时代码审查发现 `self.ALL_MOE_TYPES` 从未定义。
