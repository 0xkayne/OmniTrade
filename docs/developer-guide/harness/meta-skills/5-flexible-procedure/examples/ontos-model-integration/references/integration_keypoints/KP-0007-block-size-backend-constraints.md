# KP-0007: Block Size 约束因 Attention Backend 而异

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0007 |
| 日期 | 2026-05-13 |
| 阶段 | attention |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 发现者 | 通过代码阅读发现 |

## 问题描述

**现象**: 不同 attention backend 对 `block_size` 有不同的硬约束，使用不合法的 block_size 会导致 kernel 报错或静默返回错误结果。
**根因**: 各 kernel 的内部实现依赖于特定的 page size 对齐：
- FlashAttention / FlashInfer: block_size 必须在 `{1, 8, 16, 32, 64, 128, 256}` 中
- FlashMLA: block_size 必须是 64 的倍数（默认 64）
- FlashMLA Sparse: 固定 64
- FlashInfer MLA: block_size = 32 或 64 的倍数
- CUTLASS MLA: block_size 必须是 128 的倍数

V4 的 `sparse_attn` kernel 可能有自己的 block_size 要求。

**表现**: Profiling 阶段 kernel launch 失败，或更危险的情况——静默产出错误的 profiling 数据。

## 约束定义

**必须满足的条件**: 新增 backend 时必须在 `resolve_attention_block_size()` 中注册该 backend 的 block_size 约束。V4 的 sparse_attn backend 需要确认 FlashMLA hybrid attention kernel 的 block_size 要求。

**违反后果**: Kernel 报错（好情况）或产出错误 profiling 数据（坏情况，影响模拟精度）。

**检查方法**: 验证新 backend 的 `resolve_attention_block_size()` 逻辑覆盖了所有合法 block_size 值。

## 代码位置

- 文件路径: `ontos/profiling/attn_backend/__init__.py`
- 关键函数/类: `resolve_attention_block_size()`
- 行号范围: L34-L88
- 相关 enum/config: `AttentionBackend` 枚举

## 标准解决方案

**正确做法**:
1. 查阅 V4 sparse_attn kernel 源码确认 block_size 约束
2. 在 `resolve_attention_block_size()` 中加 V4 backend 的分支
3. 如果 V4 复用 FlashMLA 的 hybrid attention 接口，block_size 可能继承 FlashMLA 的 64 倍数约束

**常见错误做法**:
- 直接使用默认 block_size=16 → 对 MLA backend 不合法
- 不检查 V4 kernel 的 block_size 要求就假设和某个现有 backend 一样

**自动化建议**: 单元测试中对每个 `AttentionBackend` 枚举值验证 `resolve_attention_block_size()` 不会返回 0 或负数，且返回值在 `_VALID_BLOCK_SIZES` 中。

## 相关关键点

- KP-0006: 新增 Attention Kernel 只需改 attn_backend

## 发现过程

阅读 `attn_backend/__init__.py` 的 `resolve_attention_block_size()` 函数，发现从 `FlashMLA` 到 `CUTLASS_MLA` 各有不同的约束（64 倍数 vs 128 倍数），这个函数是 block_size 校验的唯一入口。
