# KP-0027: V4 Per-Layer Op 清单验证发现

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0027 |
| 日期 | 2026-05-15 |
| 维度 | profiling |
| 严重度 | P1-严重 |
| 状态 | discovered |
| 触发条件 | `op 清单生成后 (任何新模型)` |
| 泛化标签 | op_list_verification |
| 发现者 | 代码级验证 docs/deepseek-v4-pro-integration-plan.md 2.1 节 vs ref/deepseek-v4/model.py & kernel.py |

## 问题描述

**现象**: docs/deepseek-v4-pro-integration-plan.md Section 2.1 的 per-layer op 列表存在多项遗漏和错误，将直接导致 Phase 2 (Execution Plan) 和 Phase 3 (Profiling) 的实现不准确。

**根因**: 初始 op 列表从架构文档和高层理解推导，未逐行对照参考实现代码。导致：(1) 将不同性能特征的子操作合并为单一 op；(2) dtype 推断错误；(3) 遗漏了代码中显式存在但容易被忽视的轻量级 op。

**表现**: 若按当前 2.1 表直接实现，profiling 结果将缺失关键 kernel 的耗时数据，仿真器预测的 latency 会系统性偏低。特别是 hc_pre 合并问题会导致无法正确预测 compute-bound 和 memory-bound 阶段的各自耗时。

## 详细发现清单

### 发现 1: hc_pre 必须拆分为两个独立 kernel

**问题**: 当前 2.1 表将 hc_pre 作为单一 op。
**实际情况**: `model.py:673-681` 的 `hc_pre()` 包含两个性能特征完全不同的子操作：
- **(a) FP32 GEMM** (`model.py:678`): `F.linear(x, hc_fn)` — 输入 shape `[b*s, hc*d]`, 权重 shape `[mix_hc, hc*d]`，即 `[M*hc_mult*dim, 28672] x [28672, 24]` (hc_mult=4, dim=7168, mix_hc=(2+4)*4=24)。compute-bound。
- **(b) hc_split_sinkhorn TileLang kernel** (`kernel.py:372-438`): 20 轮迭代 (默认 `sinkhorn_iters=20`)，对 `[n, 24]` 矩阵做 Sinkhorn normalization。memory-bound（数据量小但迭代多）。

**约束**: 两者必须分别 profile，不能合并为一个 op entry。

**代码位置**:
- GEMM: `model.py:678` — `mixes = F.linear(x, hc_fn) * rsqrt`
- Sinkhorn: `model.py:679` → `kernel.py:430` → `kernel.py:372` — `hc_split_sinkhorn_kernel`

### 发现 2: indexer_wq_b 精度标注错误

**问题**: 2.1 表中标注为 "cuBLAS BF16"。
**实际情况**: `model.py:393` — `self.wq_b = ColumnParallelLinear(self.q_lora_rank, self.n_heads * self.head_dim)` 未指定 dtype 参数。
- `ColumnParallelLinear.__init__` (`model.py:130`): `dtype = dtype or default_dtype`
- `default_dtype` 由 `Transformer.__init__` (`model.py:776`) 设定: `default_dtype = torch.float8_e4m3fn if args.dtype == "fp8" else torch.bfloat16`
- V4 默认 args.dtype="fp8"，因此 `default_dtype = torch.float8_e4m3fn`，即 **FP8**。
- 对比 `model.py:394` — `self.weights_proj = ColumnParallelLinear(self.dim, self.n_heads, dtype=torch.bfloat16)` 显式指定 BF16。

**约束**: indexer_wq_b 应标注为 FP8 GEMM，不是 BF16。

**代码位置**: `model.py:393`, `model.py:126-130`, `model.py:776`

### 发现 3: 缺失的 RMSNorm op (4 处)

**问题**: 2.1 表未列出 RMSNorm。
**实际情况**: 代码中有 4 处 RMSNorm 调用：
- **(a) q_norm**: `model.py:496` — `qr = q = self.q_norm(self.wq_a(x))` (wq_a 后、wq_b 前)
- **(b) kv_norm**: `model.py:503` — `kv = self.kv_norm(kv)` (wkv 后、RoPE 前)
- **(c) attn_norm / ffn_norm**: `model.py:691,697` — `x = self.attn_norm(x)` / `x = self.ffn_norm(x)` (Block 中 attention/FFN 前)
- **(d) compressor.norm**: `model.py:362` — `kv = self.norm(kv.to(dtype))` (压缩后)

**约束**: 虽然 RMSNorm 单次计算量小，但高频调用 (每层 x2 + attention 内 x2)，对 decode 阶段 latency 有可测量影响，应纳入 profiling。

### 发现 4: 缺失的 apply_rotary_emb op (4 处)

**问题**: 2.1 表未列出 RoPE 应用。
**实际情况**: 代码中有 4 处 apply_rotary_emb 调用：
- **(a) Q RoPE**: `model.py:499` — `apply_rotary_emb(q[..., -rd:], freqs_cis)`
- **(b) KV RoPE**: `model.py:504` — `apply_rotary_emb(kv[..., -rd:], freqs_cis)`
- **(c) Compressed KV RoPE**: `model.py:367` — `apply_rotary_emb(kv[..., -rd:], freqs_cis)` (Compressor 内)
- **(d) O 反向 RoPE**: `model.py:534` — `apply_rotary_emb(o[..., -rd:], freqs_cis, True)` (反向，用于 attention 输出)

**约束**: 4 处 RoPE 调用应纳入 op list。其计算量与 seq_len 和 head_dim 相关。

### 发现 5: 缺失的 silu_and_mul

**问题**: 2.1 表未列出 SwiGLU 激活函数。
**实际情况**: `model.py:603` — `x = F.silu(gate) * up`，在 `Expert.forward()` 和 `SharedExpert` 中均使用。
- Expert 中: `model.py:598-603` — gate=w1(x), up=w3(x), 然后 silu(gate)*up
- SharedExpert 中: 同样结构

**约束**: 每个 Expert 和 SharedExpert 各有一次 silu_and_mul。对于 top-k=8 的配置，每层共 9 次。这是一个 fused kernel（silu + element-wise multiply），应单独 profile。

### 发现 6: 缺失的 rotate_activation (Hadamard 变换)

**问题**: 2.1 表未列出 Hadamard 变换。
**实际情况**: `model.py:247-251` 定义 `rotate_activation()`，内部调用 `fast_hadamard_transform.hadamard_transform(x, scale=x.size(-1)**-0.5)`。使用位置：
- **(a) Indexer Q**: `model.py:414` — `q = rotate_activation(q)` (Indexer.forward 中)
- **(b) Indexer Compressor**: `model.py:369` — `kv = rotate_activation(kv)` (Compressor 中，当 rotate=True 时)

**约束**: Hadamard 变换是独立 kernel，来自 fast_hadamard_transform 库。访存模式与 GEMM 不同，需单独 profile。

### 发现 7: 缺失的 MoE TP all_reduce

**问题**: 2.1 表只在 wo_b 后列了一个 all_reduce，遗漏了 MoE 内部的 all_reduce。
**实际情况**: `model.py:641-642`:
```python
if world_size > 1:
    dist.all_reduce(y)
```
这是合并各 TP rank 上不同 expert 分片的计算结果。在 TP>1 的配置中必须执行。

**约束**: 每层的 MoE 输出有一个 all_reduce，加上 wo_b 后的 all_reduce，共 2 个 all_reduce per layer。这直接影响通信建模的准确性。

### 发现 8: wo_a 是分组 einsum 非标准 GEMM

**问题**: 2.1 表可能将 wo_a 当作标准 GEMM 处理。
**实际情况**: `model.py:541` — `o = torch.einsum("bsgd,grd->bsgr", o, wo_a)` 相当于 16 个独立的小 GEMM（o_groups=16），每个 `[seq, o_lora_rank] x [o_lora_rank, head_dim//o_groups]`。
- 这是一种 batched/grouped GEMM，访存模式不同于单个大 GEMM
- 代码注释 (`model.py:539-540`) 也指出: "wo_a is FP8 in checkpoint; could do FP8 einsum here for better perf, but using BF16 for simplicity."

**约束**: wo_a 的 profiling 不能直接复用标准大 GEMM 的结果。需要使用 batched GEMM 或 einsum 特定的 profiling。

### 发现 9: Shape 公式缺少 TP 分片信息

**问题**: 2.1 表中 Shape 列标注的是全局 shape，未考虑 TP 分片后的 per-rank 实际值。
**实际影响的 op**:
- `ColumnParallelLinear` (wq_b, indexer_wq_b, wo_a): 输出维度按 `world_size` 分片
- `RowParallelLinear` (wo_b): 输入维度按 `world_size` 分片

**约束**: 对 profiling 参数空间至关重要——profiling 的是 per-rank 的实际计算量，不是全局量。Shape 标注应注明 `per_rank: [M, N/tp]` 或类似格式。

## 约束定义

**必须满足的条件**:
1. hc_pre 必须拆为 GEMM + Sinkhorn 两个独立 op entry
2. indexer_wq_b 精度必须标注为 FP8 (非 BF16)
3. op list 必须包含: RMSNorm x4, RoPE x4, silu_and_mul, rotate_activation x2, MoE all_reduce x1
4. wo_a 必须标注为 grouped einsum (非标准 GEMM)
5. 所有 ColumnParallelLinear/RowParallelLinear 的 Shape 必须标注 per-rank 值

**违反后果**: profiling 参数空间不完整/不正确，仿真器 prediction 系统性偏低，无法正确预测真实 GPU 上的 latency。

**检查方法**: 逐行对照 `ref/deepseek-v4/model.py` forward() 路径，确认每个 kernel call 都在 op list 中有对应 entry，且 dtype/shape 标注与代码一致。

**评估方法**:
1. 从 model.py 提取 forward() 中所有 Linear/ColumnParallel/RowParallel 的 (in_features, out_features, dtype) 参数，与 op list 的 Shape/量化列交叉校验
2. 从 kernel.py 提取所有自定义 kernel 的调用参数，与 op list 的 Kernel 列交叉校验
3. 统计 op list entry 数 vs forward() 路径中实际 kernel call 数，差值应为 0

## 代码位置

### 主要参考文件
- `ref/deepseek-v4/model.py` — V4 参考实现
- `ref/deepseek-v4/kernel.py` — TileLang kernel 实现

### 关键行号
| 发现 | 文件 | 行号 |
|------|------|------|
| hc_pre GEMM | model.py | L673-681 |
| hc_split_sinkhorn | kernel.py | L372-438 |
| indexer_wq_b dtype | model.py | L393, L126-130, L776 |
| q_norm | model.py | L496 |
| kv_norm | model.py | L503 |
| attn_norm / ffn_norm | model.py | L691, L697 |
| compressor.norm | model.py | L362 |
| Q RoPE | model.py | L499 |
| KV RoPE | model.py | L504 |
| Compressed KV RoPE | model.py | L367 |
| O 反向 RoPE | model.py | L534 |
| silu_and_mul | model.py | L603 |
| rotate_activation 定义 | model.py | L247-251 |
| rotate_activation (Indexer Q) | model.py | L414 |
| rotate_activation (Compressor) | model.py | L369 |
| MoE all_reduce | model.py | L641-642 |
| wo_a einsum | model.py | L541 |
| ColumnParallelLinear dtype 逻辑 | model.py | L126-130 |
| Transformer default_dtype | model.py | L776 |

## 标准解决方案

**正确做法**:
1. 更新 2.1 表：拆分 hc_pre 为两个 op，修正 indexer_wq_b dtype 为 FP8
2. 新增 8 个缺失的 op entry（RMSNorm x4 entry 或按合并策略处理、RoPE x4、silu_and_mul、rotate_activation x2、MoE all_reduce）
3. wo_a 标注为 grouped einsum 并说明 profiling 策略
4. 所有并行线性层的 Shape 标注 per-rank 值
5. 对完整的更新后 op list 再次执行代码行级验证

**常见错误做法**:
- 将 RMSNorm/RoPE/silu_and_mul 归为"negligible"而跳过——这些在 decode 阶段的占比不可忽略
- 假设 hc_pre 中的 Sinkhorn 可以融合到 GEMM kernel 中——两者计算模式完全不同
- 直接用标准 GEMM profiling 结果近似 wo_a 的 grouped einsum

**自动化建议**: 建立一个代码到 op list 的自动对照工具，解析 PyTorch model 的 forward graph，自动枚举所有 kernel call，与 op list 做差异比对。

## 相关关键点

- KP-0008: V4 的 Compressor 和 Indexer 需要独立的 Profiling — 本 KP 中的发现 1/5/6 进一步细化了 compressor/indexer 内部需要独立 profile 的子 op
- KP-0025: V4 mHC 引入 4x 激活张量倍增 — 本 KP 发现 1 的 hc_pre GEMM 就是产生 4x 倍增的关键 op
- KP-0026: V4 分组低秩 O 投影 — 本 KP 发现 8 详细说明了 wo_a 的 grouped einsum 特性
- KP-0023: 三分类 profiling 架构充分性 — 本 KP 新增的多个 op 可能影响三分类架构的完整性评估
- KP-0020: 异构融合 kernel — 本 KP 发现 1 的 hc_pre 包含异构子操作，进一步印证融合拆分的必要性
- KP-0022: V4 C4 层有两个独立 Compressor — 发现 4c/6b 的 RoPE 和 Hadamard 在 indexer compressor 中也有调用

## 发现过程

通过对 docs/deepseek-v4-pro-integration-plan.md Section 2.1 的 per-layer op 列表与 ref/deepseek-v4/model.py 及 kernel.py 的逐行代码级验证，发现 9 项遗漏/错误。验证方法：跟踪 Attention.forward()、Block.forward()、MoE.forward()、Compressor.forward()、Indexer.forward() 的完整执行路径，对照每个 kernel call 与 2.1 表中的 entry，标注差异。
