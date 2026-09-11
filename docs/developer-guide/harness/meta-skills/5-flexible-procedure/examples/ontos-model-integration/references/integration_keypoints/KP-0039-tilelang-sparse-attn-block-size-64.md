# KP-0039: TileLang Sparse Attention Kernel Block Size 必须是 64 的倍数

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0039 |
| 日期 | 2026-05-18 |
| 阶段 | profiling |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 发现者 | P2.6 C4/C128 TileLang kernel 分析 |

## 问题描述

**现象**: DeepSeek V4 的 TileLang sparse attention kernel（C4/C128）内部使用 `block=64` 的 tile size。如果 `resolve_attention_block_size()` 返回非 64 倍数的 block size，kernel 可能产生错误结果或崩溃。

**根因**: TileLang kernel 的 `sparse_attn_kernel` 函数中硬编码了 `block = 64`，所有 KV cache block size 必须与 kernel tile size 对齐。

**表现**: TileLang kernel 执行错误或段错误。

## 约束定义

**必须满足的条件**: C4/C128 attention backend 的 block_size 必须是 64 的倍数。

**违反后果**: TileLang kernel 执行错误或段错误。

**检查方法**: `resolve_attention_block_size(SPARSE_ATTN_C4/C128, block_size)` 返回值 % 64 == 0。

## 代码位置

- 文件路径: `ontos/profiling/attn_backend/__init__.py` — `resolve_attention_block_size()`
- 文件路径: `ref/deepseek-v4/kernel.py:289` — `block = 64`

## 标准解决方案

**正确做法**: 在 `resolve_attention_block_size()` 中为 SPARSE_ATTN_C4/C128 添加 `block_size % 64 == 0` 检查，不满足时强制 64。

**常见错误做法**: 使用默认 block_size=16（标准 MHA 默认值）。

**自动化建议**: 新增 TileLang kernel 时自动检查 block size 约束。

## 相关关键点

- KP-0007: Block Size Backend 约束

## 发现过程

P2.6 分析时发现 C4/C128 使用 TileLang kernel，其内部 block=64，需要在 resolve 函数中处理。
