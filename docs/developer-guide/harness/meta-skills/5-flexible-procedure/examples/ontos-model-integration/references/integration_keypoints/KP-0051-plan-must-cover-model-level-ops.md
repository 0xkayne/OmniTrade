# KP-0051: Plan 必须覆盖 Model-Level Ops（非 Per-Layer）

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0051 |
| 日期 | 2026-05-22 |
| 维度 | profiling |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 触发条件 | `模型的 forward() 中存在 per-layer loop 之外的实质性计算 op` |
| 泛化标签 | model_level_ops_coverage |
| 发现者 | DeepSeek V4 v2 plan 源码验证

## 问题描述

**现象**: model-integrator 生成的集成计划 v2 仅有 2.1.1~2.1.9 的 per-layer ops，完全遗漏了 `DeepseekV4Model.forward()` 中在 per-layer loop 之外执行的 2 个 op：
1. `hc_head` (HCHeadOp)：将 hc_mult=4 个 residual streams 合并为单流输出，计算量约 [M, 4, 7168] × fn [4, 28672]
2. `final_norm` (RMSNorm(7168))：最终的 RMSNorm 归一化

**根因**: model-integrator 的 Step 0.V Phase 2 以 `DecoderLayer.forward()` 为追踪入口，仅分析 per-layer 的执行流。但 vLLM 的 `Model.forward()`（如 `DeepseekV4Model.forward()`）在 per-layer loop 前后都有实质性计算：
- **Loop 前**: embedding + expand（本例中 `hidden_states.unsqueeze(-2).repeat(1, hc_mult, 1)`），通常属于框架标准操作可忽略
- **Loop 后**: hc_post (已在 2.1.9 覆盖) + **hc_head** + **final_norm** — 这两个 op 有真实计算量，不能忽略

model-integrator 的 op list 模板仅包含 per-layer ops 的 8 列表格，没有 "Model-Level Ops" 的专门段落。

**表现**:
- hc_head 的计算开销（[M, 28672] × [28672, 4] 规模的 GEMM + Sinkhorn）被遗漏，端到端延迟系统性低估
- final_norm 虽然是轻量 op，但 execution_time_predictor 仍需按 op name 查表（KP-0047），缺失导致查表失败
- 仿真器的端到端延迟 = sum(per-layer ops) + model-level ops，缺少 model-level ops 则仿真不完整

## 约束定义

**必须满足的条件**:
1. Step 0.V 必须追踪两层执行流：(a) `DecoderLayer.forward()` 的 per-layer 流，(b) `Model.forward()` 的 per-layer loop 前/后的 model-level 流
2. Model-level 中有实质性计算的 op（非简单的 embedding 查表或 output 投影）必须出现在 plan 的 op list 中
3. Plan 的 2.1 节应增加 "2.1.N Model-Level Ops" 段落，用标准 8 列格式列出
4. 2.3 Profiling 计划也必须有对应的 profiling 条目
5. 轻量 model-level op（如 final_norm）仍需列入，理由同 KP-0047

**违反后果**: 端到端延迟仿真缺少 model-level op 的贡献 → 低估延迟。HC 类模型的 hc_head 有显著计算量，遗漏影响大。

**评估方法**:
```bash
# 1. 检查 vLLM Model.forward() 中 per-layer loop 前/后是否有实质性计算
grep -A20 "for layer in" vllm/model_executor/models/<model>.py | \
  grep -v "^#\|pass\|return\|embed\|lm_head"

# 2. 检查 plan 是否有 model-level ops 段落
grep "Model-Level\|model_level\|hc_head\|final_norm" docs/<model>-integration-plan*.md

# 3. 确认 execution plan 代码包含 model-level op 调用
grep "hc_head\|final_norm\|model_level" ontos/execution_time_predictor/models/<model>.py
```

## 代码位置

- DeepseekV4Model.forward(): `vllm/model_executor/models/deepseek_v4.py` L1497-1514
  - hc_head: `self.hc_head_op(hidden_states, self.hc_head_fn, self.hc_head_scale, self.hc_head_base, ...)`
  - final_norm: `self.norm(hidden_states)` — `RMSNorm(config.hidden_size, config.rms_norm_eps)`
- HCHeadOp: `vllm/model_executor/layers/mhc.py` — 与 MHCPreOp 类似的 Sinkhorn 操作
- hc_head_fn 参数: `self.hc_head_fn = nn.Parameter(torch.empty((self.hc_mult, self.hc_dim), dtype=torch.float32))` — shape [4, 28672]

## 标准解决方案

**正确做法**:
1. Step 0.V 增加 Phase 2.5: 在追踪完 DecoderLayer 后，追踪 `Model.forward()` 的完整执行流，识别 per-layer loop 前/后的 op
2. 在 plan 的 2.1 节末尾增加 "Model-Level Ops" 段落，列出有实质性计算的 op
3. 判断"实质性计算"的标准：是否有 GEMM / CUDA custom kernel / Triton kernel，而非简单的 tensor view 操作
4. Embedding 查表和 lm_head 投影通常由框架处理，不列入（除非有特殊的融合或量化）

**常见错误做法**:
- 假设 "per-layer ops 的 sum ≈ 端到端延迟"，忽略 model-level op 的贡献
- 只追踪 DecoderLayer.forward()，不追踪 Model.forward()
- 将 model-level op 标记为"框架开销可忽略"——hc_head 包含 Sinkhorn 迭代和大规模 GEMM，不可忽略
- 不在 plan 中为 model-level op 创建 profiling 条目，导致 execution_time_predictor 查表失败

**自动化建议**:
1. Step 0.V Phase 2 的追踪范围从 DecoderLayer 扩展到 Model，自动识别 per-layer loop 前/后的 op
2. 在 model-integrator 的 plan 模板中增加 "Model-Level Ops" 段落的占位符，提示分析师必须检查

## 相关关键点

- KP-0047: Execution Plan Op 必须与 Profiling 数据 1:1 完备 — 本 KP 是遗漏的根因：model-integrator 的追踪范围不够大，导致部分 op 从未被纳入 op list
- KP-0049: 复合管线 Op 和条件 Kernel 变体需独立 Profiling — hc_head 是一个条件触发的 model-level op
- KP-0025: mHC 激活倍增 — hc_head 的计算量与 hc_mult 直接相关

## 发现过程

在逐 op 验证 DeepSeek V4 v2 集成计划的参数正确性时，通过 AtCode MCP 读取 `DeepseekV4Model.forward()` 源码，发现在 per-layer loop 之后有两行实质性计算（hc_head_op 和 norm）未出现在 plan 的 op list 中。Plan 仅覆盖到 `hc_post_ffn_final`（2.1.9），但 hc_head 和 final_norm 在 Model 级别执行，不在任何 DecoderLayer 中。这是一个 scope 问题：model-integrator 的追踪范围从 DecoderLayer 扩展到 Model 时存在 gap。
