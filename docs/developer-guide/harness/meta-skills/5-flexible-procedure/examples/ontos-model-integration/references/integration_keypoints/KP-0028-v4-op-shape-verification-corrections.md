# KP-0028: V4 Op Shape 代码级验证修正

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0028 |
| 日期 | 2026-05-15 |
| 维度 | profiling |
| 严重度 | P1 |
| 状态 | discovered |
| 触发条件 | `有 Compressor 子类实例 OR 有 flatten+GEMM 组合 (如 HC/MLA)` |
| 泛化标签 | shape_verification |
| 发现者 | 逐行代码验证 |

## 问题描述

**现象**: V4 Op 清单（docs/v4-op-list.xlsx、docs/deepseek-v4-pro-integration-plan.md）中有 4 个 op 的 Shape 描述与实际代码不一致
**根因**: 两类独立的认知错误——(1) 混淆了不同模块的 head_dim 参数；(2) 对 flatten+GEMM 的维度映射理解错误
**表现**: Profiling 参数空间设定错误，simulator 延迟预测精度受影响

### 错误 1: Indexer Compressor GEMM 维度（indexer_compressor_wkv, indexer_compressor_wgate）

| 项目 | 错误值 | 正确值 |
|------|--------|--------|
| Shape | [M, 7168] × [7168, 1024] | [M, 7168] × [7168, 256] |
| 输出 dim | coff × 512 = 1024 | coff × 128 = 256 |

**根因**: Indexer 的内部 Compressor 传入的 `head_dim = args.index_head_dim = 128`（model.py:389, model.py:398），而非主注意力的 `head_dim = 512`。Compressor.__init__（model.py:297）中 `wkv = Linear(dim, coff * head_dim)`，coff=2 时输出 dim = 2×128 = 256。

### 错误 2: HC Pre GEMM 的 M 维度（hc_pre_gemm_attn, hc_pre_gemm_ffn）

| 项目 | 错误值 | 正确值 |
|------|--------|--------|
| Shape | [M×4, 28672] × [28672, 24] | [M, 28672] × [28672, 24] |
| Prefill M | M=prefill×4 | M=prefill_tokens |
| Decode M | M=4 | M=batch_size |

**根因**: `x.flatten(2)`（model.py:676）将 `[b, s, hc_mult, dim]` = `[b, s, 4, 7168]` 展平为 `[b, s, 28672]`。hc_mult=4 和 dim=7168 合并到 K 维（K=28672），M 维仍是 b*s。"×4" 属于 K 维，不是 M 维。

## 约束定义

**必须满足的条件**:
1. 分析任何 `Compressor` 子类实例时，必须确认其构造时传入的 `head_dim` 参数值，不能默认使用主注意力的 `head_dim`
2. 分析 `flatten` + GEMM 组合时，必须明确 flatten 操作将哪些维度合并到了 K（特征）维度、哪些维度构成了 M（batch/行数）维度

**违反后果**: Profiling 参数空间错误 → 延迟预测偏差 → Simulator 精度下降

**检查方法**:
- 对每个 GEMM op，追踪其 weight tensor 的实际 shape（从代码中的 Linear/ColumnParallel/RowParallel 初始化参数推导）
- 对 flatten 后的 GEMM，画出输入 tensor 的原始 shape → flatten 后 shape → GEMM 维度映射

**评估方法**:
1. 对每个 Compressor 实例（含 Indexer 内部的），确认 head_dim 参数是否与主注意力不同；若不同，GEMM 输出 dim = coff × 实际 head_dim
2. 对每个 flatten+GEMM 组合，验证 M 维 = flatten 前前两维的乘积（b*s），K 维 = flatten 合并后的维度
3. 自动化: 解析 model 代码中所有 Linear 初始化参数，自动推导 GEMM shape，与 op 清单的 Shape 列做 diff

## 代码位置

- **Indexer Compressor head_dim**: `ref/deepseek-v4/model.py:389`（`self.head_dim = args.index_head_dim`）、`model.py:398`（`self.compressor = Compressor(args, compress_ratio, self.head_dim, True)`）
- **Compressor wkv**: `model.py:297`（`self.wkv = Linear(self.dim, coff * self.head_dim, dtype=torch.float32)`）
- **HC flatten**: `model.py:676`（`x = x.flatten(2).float()`）
- **HC GEMM**: `model.py:678`（`mixes = F.linear(x, hc_fn) * rsqrt`）

## 标准解决方案

**正确做法**:
1. 对于 Indexer Compressor：`head_dim = index_head_dim = 128`，`coff = 2`，输出 = 256
2. 对于 HC Pre GEMM：`M = b*s`（total tokens），`K = hc_mult × dim = 28672`，`N = mix_hc = 24`

**常见错误做法**:
1. 直接复用主注意力的 head_dim=512 来计算 Indexer Compressor 的 shape
2. 看到 `[b, s, 4, 7168]` 就认为 GEMM 有 4×M 行

**自动化建议**: 在 op 清单生成 skill 中，添加 shape 验证规则——从代码中自动提取每个 Linear 的 in_features/out_features，与 op 清单中的 Shape 列交叉校验

## 相关关键点

- KP-0022: 双 Compressor — 本 KP 的错误 1 正是源于对 Indexer Compressor 独立 head_dim 的忽视
- KP-0025: mHC 4x 激活倍增 — 本 KP 的错误 2 源于对 HC 4x 如何影响 GEMM 维度的误解
- KP-0027: Op 清单代码级验证 — 本 KP 是 KP-0027 验证过程中发现的子问题

## 发现过程

在对照 ref/deepseek-v4/model.py 和 kernel.py 逐一验证 docs/v4-op-list.xlsx 中 50 个 op 的 Shape 时发现。验证方法：从代码中的 Linear/ColumnParallel/RowParallel 初始化参数推导 weight shape，再推导 GEMM 维度，与 xlsx 中的 Shape 列比对。

已修正文件: docs/v4-op-list.xlsx, docs/deepseek-v4-pro-integration-plan.md, docs/v4-op-list-improved.md
