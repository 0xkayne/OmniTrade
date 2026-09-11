# KP-0014: MTP Head 只用 SWA Attention，不走 Compressor/Indexer

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0014 |
| 日期 | 2026-05-13 |
| 阶段 | attention |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 发现者 | 通过 SGLang 博客分析发现 |

## 问题描述

**现象**: 建模 V4 的 MTP（Multi-Token Prediction）speculative decoding 时，容易假设 MTP head 和主模型一样走完整的 SWA + CSA/HCA attention。
**根因**: MTP head 的设计特点：
- 单层 decoder layer（不是完整的 transformer block）
- 使用 **SWA-only attention**（128 token 窗口）
- **没有 compressor，没有 indexer**
- 输入 = 上一步 hidden state (`h_proj`) + next-token embedding (`e_proj`) 的组合

这意味着 MTP head 的 KV cache 只有 SWA 部分（128 token × 1 层），极其轻量。但 attention metadata 准备仍然很重。

SGLang 花了大量工程优化 MTP 的 metadata 准备：将 hybrid attention 的 per-pass metadata 准备融合进 CUDA graph，避免 Python 在 replay 时触碰 per-pass 路径。

**表现**: 如果给 MTP head 加上不必要的 compressor/indexer 开销，会高估 draft token 生成耗时。

## 约束定义

**必须满足的条件**:
1. MTP head 的 attention 只有 SWA（无 compressed path）
2. MTP head 的 KV cache = 128 token × 1 层（极小）
3. MTP 的 attention 耗时 ≈ SWA-only attention 耗时
4. MTP 不触发 compressor 或 indexer kernel

**违反后果**: 高估 MTP draft token 生成耗时 → 高估 speculative decoding 的理论加速比。

**检查方法**: 验证 MTP head 的 execution plan 不包含 compressor/indexer op。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/` (MTP execution plan)
- 参考: `ontos/config/model_config.py` (MTP 相关配置)

## 标准解决方案

**正确做法**:
1. MTP head 的 attention op 使用 SWA-only 耗时数据
2. MTP 的 KV cache 占用按 128 token × 1 层 × head_dim 计算
3. Speculative decoding 的 accept_length multiplier 基于 SGLang 实测数据校准

**常见错误做法**:
- 给 MTP head 加上和主模型相同的 attention 执行计划 → 过度计算
- 忽略 MTP 的 KV cache 写入开销（虽然只有 128 token，但仍需要写入）

**自动化建议**: 验证 MTP execution plan 中 attention op 数量 = 1（只有 SWA），且无 compressor/indexer op。

## 相关关键点

- KP-0009: V4 每层是 SWA + 压缩 Attention 的组合（MTP 是例外）
- KP-0008: Compressor/Indexer 需要独立 profiling（MTP 不涉及）

## 发现过程

SGLang 博客明确描述 MTP head："a separately trained DSv4 decoder layer that runs SWA-only attention (no compressor, no indexer)"。同时指出 hybrid attention metadata preparation 是 speculative decoding 的性能瓶颈，需要 in-graph metadata fusion 优化。
