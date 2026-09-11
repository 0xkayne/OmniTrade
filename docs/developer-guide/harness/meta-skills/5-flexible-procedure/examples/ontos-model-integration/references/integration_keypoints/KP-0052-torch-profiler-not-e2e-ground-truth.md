# KP-0052: Torch Profiler Trace 不能作为端到端 Ground Truth

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0052 |
| 日期 | 2026-05-31 |
| 维度 | profiling |
| 严重度 | P1 |
| 状态 | constraint_defined |
| 触发条件 | `需要用 torch profiler trace 解释 vLLM/Ontos 端到端误差` |
| 泛化标签 | profiler_wall_perturbation |
| 发现者 | DeepSeek V4-Pro N8 TP8 仿真/真实 bench 对齐验证 |

## 问题描述

**现象**: DeepSeek V4-Pro N8/TP8 第三个串行请求中，非 profiler vLLM bench 的 E2E 为 2008.1 ms；low-overhead torch profiler wall 为 2828.6 ms；high-detail profiler wall 为 3364.6 ms。

**根因**: torch profiler 会引入 CUDA event、trace collection、NVTX/stack 记录和导出开销。即使关闭 stack 和 layerwise NVTX，profiling window 仍会显著拉长 request wall time。

**表现**: 如果把 torch profiler wall time 当作真实 E2E，会把 vLLM ground truth 从 2008.1 ms 错误改写为 2828.6 ms，从而掩盖或反转 Ontos 的误差方向。Profiler trace 只能用于 kernel family attribution、排序和相对归因，不能替代非 profiler vLLM bench 的 client-observed E2E。

## 约束定义

**必须满足的条件**:
1. 端到端 TTFT/TPOT/E2E ground truth 必须来自非 profiler vLLM bench。
2. torch profiler trace 只能用于误差归因、kernel family 对齐和候选修复定位。
3. 报告中必须同时写明 profiler mode、profiling wall time、非 profiler bench wall time，以及 profiler perturbation 百分比。
4. 如果 profiler wall 与非 profiler E2E 偏差超过 5%，不得使用 profiler wall 计算最终仿真误差百分比。

**违反后果**:
- 端到端误差百分比被 profiler 开销污染。
- 修复优先级会被错误排序，例如把 profiler overhead 当作 vLLM 模型执行时间。
- 多个 profiler mode 之间的差异会被误认为模型/kernel 真实波动。

**评估方法**:

```bash
python3 - <<'PY'
import csv
import json

bench = "pipeline_results/request_traces/vllm-0-21-0__model-deepseekv4-pro-n8__tp8-dp1-ep1-pp1__attn-v4-flashmla-sparse__blk256__gmu0-9__mbt4096__mseq256__w-fp8__kv-fp8__prefix-off__len4096__cg-piecewise/dataset-custom__qpsinf__ctx-1k__n3.csv"
profile = "pipeline_results/deepseek_v4_shrink/vllm_req3_profile_client_lowoverhead_20260531_221456.json"

with open(bench, newline="") as f:
    rows = list(csv.DictReader(f))
bench_e2e_s = float(rows[2]["e2e_time"])
with open(profile) as f:
    prof = json.load(f)
prof_wall_s = float(prof["profile_window_wall_s"])
delta = (prof_wall_s - bench_e2e_s) / bench_e2e_s

print(f"non_profiled_e2e_ms={bench_e2e_s * 1000:.1f}")
print(f"profile_wall_ms={prof_wall_s * 1000:.1f}")
print(f"profiler_perturbation_pct={delta * 100:.1f}")
assert delta > 0.05
PY
```

## 代码位置

- vLLM bench trace: `pipeline_results/request_traces/.../dataset-custom__qpsinf__ctx-1k__n3.csv`
- profiler client metadata: `pipeline_results/deepseek_v4_shrink/vllm_req3_profile_client_lowoverhead_20260531_221456.json`
- kernel attribution report: `pipeline_results/deepseek_v4_shrink/req3_kernel_error_analysis_20260531.md`

## 标准解决方案

**正确做法**: 先运行非 profiler vLLM bench 获取端到端 ground truth，再对 warmed request 单独开 profiler 抓 kernel 归因。最终误差百分比只用非 profiler bench；profiler 结果只解释误差来源。

**常见错误做法**: 把 profiler trace 的 request wall、Chrome trace 总时长或 profiler window wall 直接作为 vLLM E2E。

**自动化建议**: 所有 benchmark report 生成器在发现 profiler metadata 时，自动计算 `profile_wall / non_profiled_e2e`；超过 5% 时强制标注 `profiler_wall_not_e2e_ground_truth`。

## 相关关键点

- KP-0055: 必须先用 warmed single-request steady-state 排除冷启动/调度影响。
- KP-0054: profiler 可用于发现 rank-sum vs critical-path wall 的计时语义错误。
- KP-0048: profiler 的 kernel family 归因仍需对齐 vLLM 实际融合 kernel 边界。

## 发现过程

在 DeepSeek V4-Pro N8/TP8 第三个串行 ShareGPT 请求上，非 profiler bench E2E 为 2008.1 ms；low-overhead profiler 增至 2828.6 ms；high-detail profiler 增至 3364.6 ms。该差异证明 profiler wall 不能作为端到端真实指标，只能用于逐 kernel 归因。

