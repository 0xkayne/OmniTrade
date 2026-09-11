# KP-0038: Attention Backend 矩阵必须区分 MLA-sparse 和 MQA-sparse

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0038 |
| 日期 | 2026-05-18 |
| 阶段 | profiling |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 发现者 | DeepSeek V4 profiling 配置分析 |

## 问题描述

**现象**: `automation/builders/ontos_profile.py` 的 `default_attention_backend_matrix()` 检查 `is_sparse_backend_model()` 返回 True 时，固定返回 MLA sparse backends（`FLASHMLA_SPARSE`, `FLASHINFER_MLA_SPARSE`）。V4 也是 sparse backend model（`index_topk=1024`）但不使用 MLA，导致被分配到错误的 backend。

**根因**: Sparse attention 有两种不同的实现路径：1) MLA-based sparse (V3.2, 使用 MLA + sparse indexer)；2) MQA-based sparse (V4, 使用 C4/C128 压缩 KV)。原代码未区分这两种情况。

**表现**: V4 被分配到 FLASHMLA_SPARSE backend → profiling 运行但使用错误的 kernel → 计时数据无意义。

## 约束定义

**必须满足的条件**: `default_attention_backend_matrix()` 必须区分 `is_sparse_backend_model() + is_mla_model()` 和 `is_sparse_backend_model() + !is_mla_model()` 两种情况。

**违反后果**: V4 被分配到 FLASHMLA_SPARSE backend → profiling 运行但使用错误的 kernel → 计时数据无意义。

**检查方法**: 对新模型验证 `default_attention_backend_matrix()` 返回的 backend 与模型实际 attention 类型匹配。

## 代码位置

- 文件路径: `automation/builders/ontos_profile.py` — `default_attention_backend_matrix()`
- 文件路径: `ontos/profiling/attn_backend/types.py` — AttentionBackend enum

## 标准解决方案

**正确做法**: 三路分支 — MLA sparse → FLASHMLA_SPARSE; MQA sparse → SPARSE_ATTN_C4/C128; 非 sparse → 标准 MHA/MLA backends。

**常见错误做法**: 仅检查 `is_sparse_backend_model()` 就返回 MLA sparse backends。

**自动化建议**: 新增 sparse attention 模型时，自动验证 default_attention_backend_matrix 返回值正确。

## 相关关键点

- KP-0006: Backend 扩展边界

## 发现过程

V4 profiling 配置分析时发现 `is_sparse_backend_model()` 为 True 导致默认选中 FLASHMLA_SPARSE。
