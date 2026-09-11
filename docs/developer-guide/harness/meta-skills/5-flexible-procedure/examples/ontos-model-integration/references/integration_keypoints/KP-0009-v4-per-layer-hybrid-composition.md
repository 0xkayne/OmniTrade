# KP-0009: V4 每层是 SWA + 压缩 Attention 的组合，不是互斥的层类型

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0009 |
| 日期 | 2026-05-13 |
| 阶段 | attention |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 发现者 | 通过综合分析 SGLang 博客、Together AI 博客、DeepSeek 官方技术文档发现 |

## 问题描述

**现象**: 容易误解 V4 的层类型为"SWA 层"、"C4 层"、"C128 层"三种互斥的层。实际上 V4 的**每一层**都同时使用 SWA + 一种压缩 attention。
**根因**: V4 的 hybrid attention 设计：每层的 attention 输出 = SWA(local 128 tokens) + ExtraAttention(compressed KV)。区别在于"ExtraAttention"部分：
- 部分层：ExtraAttention = C4（stride=4 压缩，top-512 sparse）
- 部分层：ExtraAttention = C128（stride=128 压缩，dense）
- 最后一层（layer 60）：ExtraAttention = 无，纯 SWA

`compress_ratios` 数组（61 个元素）定义了每层的压缩比：128、4、或 0（纯 SWA）。

**表现**: 如果按互斥层类型建模，会导致：
- 忽略每层都有 SWA 部分 → 低估 SWA 的显存占用（应为每层×每请求×128 token）
- 把层的 attention op 拆成三种独立类型 → 无法正确建模 fused kernel（SWA + extra 在一次 kernel call 中完成）

## 约束定义

**必须满足的条件**:
1. 每层（除最后一层）都有两个 attention 路径：SWA + compressed
2. `compress_ratios[i]` 定义第 i 层的压缩类型（0=纯SWA, 4=C4, 128=C128）
3. SWA 部分在所有层都存在，不能省略
4. SGLang 的 FlashMLA 集成将 SWA 和 extra attention 融合为单次 kernel call

**违反后果**: SWA 显存被低估；attention 耗时被拆分成两次独立计算，无法反映 fused kernel 的实际性能。

**检查方法**: 验证 V4 的 execution plan 中每层都包含 SWA op，且 C4/C128 层额外包含 compressed attention op。

## 代码位置

- 文件路径: `ontos/config/model_config.py` (需新增 `compress_ratios` 字段)
- 文件路径: `ontos/execution_time_predictor/models/` (V4 predictor 的 attention 执行计划)
- 参考: `ontos/config/attn_pattern_config.py` (现有的 per-layer attention pattern 机制)

## 标准解决方案

**正确做法**:
1. Model config 中 `compress_ratios: list[int]` 完整保留 61 层的压缩比
2. Execution plan 中每层的 attention 拆分为：SWA op + compressed op（如果 compress_ratio > 0）
3. Profiling 时使用 FlashMLA 的 hybrid attention kernel（SWA + extra fused），计时覆盖完整调用
4. 最后一层（compress_ratio=0）只有 SWA op

**常见错误做法**:
- 按层类型把层分成三组，每组只跑一种 attention → 忽略了每层的 SWA 部分
- 认为 C128 层"只有" dense compressed attention，不需要 SWA → 错误
- 把 compress_ratios 简化为一个标量 → 丢失了 per-layer 信息

**自动化建议**: 验证 V4 的 execution plan 中 SWA op 出现次数 = num_layers，compressed op 出现次数 = num_layers - 1（或 compress_ratios 中非零元素个数）。

## 相关关键点

- KP-0001: V4 异构 KV cache 分组建模（SWA group 覆盖所有层）
- KP-0005: SWA 可能反直觉地成为内存瓶颈（正是因为每层都有 SWA）
- KP-0008: Compressor/Indexer 需要独立 profiling

## 发现过程

SGLang 博客的 Figure 1 清晰展示了 N=1024 时每层的 attention scope：每层都包含 SWA(128) + 额外的 compressed 范围。Together AI 博客确认"三种 cache 类型混合在层之间"。FlashMLA 的接口设计也反映了这一点：`k_cache` 和 `extra_k_cache` 在同一次 kernel call 中传入。
