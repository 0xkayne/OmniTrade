# KP-0050: Plan 参数值必须通过 vLLM 源码级自动验证

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0050 |
| 日期 | 2026-05-22 |
| 维度 | profiling |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 触发条件 | `任何新模型的集成计划生成后 (Step 8 输出后)` |
| 泛化标签 | plan_parameter_verification |
| 发现者 | DeepSeek V4 v2 plan 源码验证 |

## 问题描述

**现象**: model-integrator 生成的集成计划 v2 中，22 个 op 的参数值存在 4 处错误：
1. `fused_indexer_compressor_gemm` 输出维度少算 coff=2 因子：[7168, 256] → 实际 [7168, 512]
2. `fp8_einsum_wo_a` 权重张量两个维度写反：[16, 4096, 1024] → 实际 [16, 1024, 4096]（einsum "bhr,hdr->bhd" 要求 [h,d,r] 顺序）
3. `fused_inv_rope_fp8_quant` 元素数用 n_groups 代替 n_local_heads：M×16×512 → 实际 M×128×512
4. `compress_ratios` 层数统计错误：C4=30/C128=30 → 实际 C4=29/C128=31

**根因**: model-integrator 的 Step 0.V 通过 AtCode MCP 分析 vLLM 源码提取 op 信息，但提取过程依赖人工阅读和推理。三个系统性缺失：
1. **无自动推导**: Linear 层的 output_size 推导需要追踪 `__init__` 中的参数传递链（如 `coff = 1 + (compress_ratio == 4)` → `MergedColumnParallelLinear(hidden_size, [coff * head_dim, coff * head_dim])`），当前仅靠人工计算
2. **无交叉校验**: 参数值（Shape、元素数、维度顺序）写入 plan 后，没有反向验证步骤
3. **条件因子遗漏**: 条件计算的中间变量（如 `coff`、`overlap`）影响最终 shape，但容易在阅读时被忽略

**表现**:
- Plan 中的错误 Shape 直接传递给 profiling wrapper → profiling 用错误维度测量 → CSV 数据无效
- 层数统计错误 → KV cache 总量估算偏差 → simulation 内存模型不准确
- 维度顺序错误 → 自定义 kernel 的 profiling 参数错误

## 约束定义

**必须满足的条件**:
1. Plan 中每个 op 的 Shape 列必须能从 vLLM 源码的 `__init__` 参数链精确推导（非近似估算）
2. 涉及条件中间变量（如 `coff`、`overlap`）的 shape 计算，plan 必须显式标注推导链
3. einsum/bmm 类 op 的维度顺序必须与源码中的 einsum 表达式严格一致
4. 元素数类指标（elem=...）必须使用正确的张量维度（如 n_local_heads 而非 n_groups）
5. Plan 生成后必须有 Step 8.7 验证步骤：对每个 op，从源码提取 weight shape 并与 plan 的 Shape 列交叉校验

**违反后果**: Profiling 参数错误 → CSV 数据无效 → execution_time_predictor 查表得到错误延迟 → 仿真结果系统性偏差

**评估方法**:
```bash
# 1. 从 vLLM 源码提取每个 Linear 的 output_sizes
# 对 MergedColumnParallelLinear: 检查 output_sizes 参数
grep -A5 "MergedColumnParallelLinear\|ColumnParallelLinear\|RowParallelLinear" vllm/model_executor/models/<model>.py | \
  grep -P "(hidden_size|input_size|output_sizes|output_size)\s*[,=]"

# 2. 对比 plan 的 Shape 列与源码推导结果
# 关键检查项:
#   - 条件因子 (coff, overlap) 是否正确传播到最终 shape
#   - einsum 维度顺序是否匹配源码的 einsum 表达式
#   - 元素数是否使用了正确的张量维度

# 3. compress_ratios 层数统计
python -c "
import json
d = json.load(open('config.json'))
ratios = d['compress_ratios']
from collections import Counter
c = Counter(ratios)
print(f'C128: {c[128]} layers, C4: {c[4]} layers, SWA: {c[0]} layers, Total: {len(ratios)}')
"
```

## 代码位置

- Compressor coff 推导: `vllm/model_executor/layers/deepseek_compressor.py` L198-199 (`self.overlap = compress_ratio == 4; self.coff = 1 + self.overlap`)
- wo_a einsum: `vllm/model_executor/layers/deepseek_v4_attention.py` — `torch.ops.vllm.deepseek_v4_fp8_einsum(..., "bhr,hdr->bhd", ...)`
- fused_inv_rope_fp8_quant 输入: `vllm/model_executor/layers/deepseek_v4_attention.py` — `o [num_tokens, n_local_heads, head_dim]`
- compress_ratios: `config.json` → `compress_ratios` 数组

## 标准解决方案

**正确做法**:
1. model-integrator Step 0.V Phase 1~4 中，对每个 `Linear/ColumnParallel/RowParallel/MergedColumnParallel` 初始化，提取完整参数链：
   - 输入: `input_size` 参数值
   - 输出: `output_sizes` 参数值（含列表展开）
   - 条件因子: 追踪 `self.xxx = 1 + condition` 类的中间变量
2. Plan 的 Shape 列使用从源码推导的精确值，标注推导链（如 `[7168, 2*coff*128]` 其中 `coff=2 因为 compress_ratio==4`）
3. einsum 类 op：从源码复制完整的 einsum 表达式和实际张量 shape，不手动简写
4. 元素数：使用张量的实际 shape 乘积，不用近似公式
5. Step 8.7 增加"参数源码验证"子步骤：对 plan 的每个 op Shape，从 AtCode MCP 提取对应的 Linear 初始化参数做交叉校验

**常见错误做法**:
- 从参数名的字面意思估算 Shape（如看到 `index_head_dim` 就写 `2*index_head_dim`，忘了 `coff` 因子）
- 手动简写 einsum 的维度顺序（容易把 d 和 r 写反）
- 用 n_groups 代替 n_local_heads（前者是分组数，后者是实际头数，差 heads_per_group 倍）
- 不做层数统计验证，凭记忆写 "30 层"
- 仅依赖人工 review 发现参数错误，不建立自动化验证

**自动化建议**:
1. Step 0.V 中增加"参数快照"产出：从 vLLM 源码提取每个 Linear 的 `(input_size, output_sizes, quant_config)` 三元组
2. Step 8.7 自动对比 plan 的 Shape 列与参数快照，输出 diff 报告
3. einsum 类 op：自动从源码提取 einsum 表达式和张量 shape，验证维度一致性

## 相关关键点

- KP-0028: V4 Op Shape 代码级验证修正 — 本 KP 是 KP-0028 的系统性升级：从"发现了 N 个具体 shape 错误"升级为"为什么 shape 错误会系统性发生 + 如何防止"
- KP-0048: Plan Op 粒度必须对齐 vLLM — 本 KP 是 KP-0048 的数据层面补充：不仅 op 粒度要对齐，op 的参数值也要对齐
- KP-0047: Execution Plan Op 必须与 Profiling 数据 1:1 完备 — 参数错误导致 profiling 数据虽然"有"但"错"

## 发现过程

生成 DeepSeek V4 v2 集成计划后，用户要求逐 op 验证参数正确性。通过 AtCode MCP 追踪 vLLM 源码中每个组件的 `__init__` 参数传递链，与 plan 的 Shape 列逐一比对，发现 4 处参数错误。这暴露了 model-integrator 在参数提取环节缺乏自动验证机制的系统性缺陷。v1 plan (KP-0028) 中也曾发现类似的 shape 错误（Indexer Compressor 的 head_dim 混淆），说明这是重复发生的模式，需要流程层面的修复。
