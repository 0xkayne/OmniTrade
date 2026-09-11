# KP-0006: 新增 Attention Kernel 只需改 attn_backend，不动 attention 编排层

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0006 |
| 日期 | 2026-05-13 |
| 阶段 | attention |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 发现者 | 通过 git 历史分析和代码架构梳理发现 |

## 问题描述

**现象**: 扩展新 attention 类型时，容易把 kernel 调用逻辑写进 `attention/attention_wrapper.py`，导致实验编排和 kernel 适配耦合。
**根因**: 原始 Ontos 只有一种 backend（FlashInfer），所有逻辑混在 `attention/attention_wrapper.py` 中。本 fork 的 `f21f8ec` 提交将 kernel 适配层抽取到 `attn_backend/`，形成 Strategy + 工厂模式。但后来者可能不理解这个分离的意图。

**表现**: 如果在 `attention/attention_wrapper.py` 中加入 kernel-specific 逻辑，会导致：
- 每加一个 backend 都要改同一个文件
- 无法独立测试单个 backend
- 代码膨胀和冲突

## 约束定义

**必须满足的条件**: 新增 attention kernel（如 V4 的 sparse_attn）时，改动范围严格限制在：
1. `ontos/profiling/attn_backend/types.py` — 新增 `AttentionBackend` 枚举值
2. `ontos/profiling/attn_backend/<new>_wrapper.py` — 实现 `BaseAttentionWrapper` 接口
3. `ontos/profiling/attn_backend/__init__.py` — 在 `get_attention_wrapper()` 工厂中加一个分支

**`attention/` 目录下的文件不应改动**（除非是全新的 tensor shape 需求，如 MLA 的特殊维度，此时应在 `_get_input_tensors()` 中用 `_is_mla` 式的条件分支）。

**违反后果**: 违反开闭原则，增加维护负担和代码冲突风险。

**检查方法**: `git diff` 中验证 `attention/` 目录下只有 `attention_wrapper.py` 的 tensor shape 相关改动。

## 代码位置

- 文件路径: `ontos/profiling/attn_backend/` (kernel 适配层)
- 文件路径: `ontos/profiling/attention/` (实验编排层)
- 关键函数/类: `BaseAttentionWrapper` (ABC), `get_attention_wrapper()` (工厂)
- 相关 enum/config: `AttentionBackend` 枚举

## 标准解决方案

**正确做法**:
1. 新建 `attn_backend/sparse_attn_c4_wrapper.py`，继承 `BaseAttentionWrapper`
2. 实现 `init()`, `begin_forward()`, `forward()`, `end_forward()`, `get_cache_block()`
3. 在 `types.py` 加 `SPARSE_ATTN_C4 = "SPARSE_ATTN_C4"`
4. 在 `__init__.py` 的 `get_attention_wrapper()` 加分支
5. 如需特殊 tensor shape，在 `attention/attention_wrapper.py` 的 `_get_input_tensors()` 中加 `_is_v4_sparse` 条件

**常见错误做法**:
- 把 FlashMLA 的 API 调用直接写在 `attention/attention_wrapper.py` 里
- 复制 `flashmla_wrapper.py` 的内容到 `attention/` 目录
- 在 `main.py` 中按 backend 做条件分支

**自动化建议**: CI 检查 `attention/` 目录下不出现 import 任何 attention kernel 库（flash_attn, flashinfer, triton 等）的代码。

## 相关关键点

- KP-0007: Block size 约束因 backend 而异

## 发现过程

通过 `git log --follow` 追溯 `attn_backend/` 的演化历史：原始代码在 `b7bf448` 从 `ontos/profiling/sarathi/` 提取，`f21f8ec` 重命名为 `attn_backend/`。目前有 15 个 backend wrapper 子类，`attention/` 的实验编排代码完全 backend-agnostic。
