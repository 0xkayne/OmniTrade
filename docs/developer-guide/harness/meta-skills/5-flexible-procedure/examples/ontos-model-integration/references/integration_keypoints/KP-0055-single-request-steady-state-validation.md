# KP-0055: 先用 Warmed Single-Request Steady-State 验证算子集成

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0055 |
| 日期 | 2026-05-31 |
| 维度 | profiling |
| 严重度 | P1 |
| 状态 | constraint_defined |
| 触发条件 | `端到端误差异常 AND 当前目标是判断模型算子集成精度` |
| 泛化标签 | single_request_steady_state_validation |
| 发现者 | DeepSeek V4-Pro N8 TP8 仿真/真实 bench 对齐验证 |

## 问题描述

**现象**: DeepSeek V4-Pro N8/TP8 单请求 first request 的 vLLM TTFT 约 641 ms，而 Ontos TTFT 约 79 ms；三请求串行 `max_concurrency=1` 中，前两个请求 TTFT 为 626.7 ms 和 549.0 ms，第三个请求 TTFT 降到 61.0 ms。

**根因**: first/early request 包含 vLLM serving 冷路径、streaming/API path、CUDA graph/runtime warm state、输出处理等非模型算子因素。多请求 burst 还会额外引入调度、batching、排队和 overlap 影响。

**表现**: 如果直接使用 first request 或多请求 burst 判断 DeepSeek V4 算子集成精度，会把 serving cold path 或调度误差误判为 kernel/model integration 问题。

## 约束定义

**必须满足的条件**:
1. 当目标是评估模型算子集成精度时，必须先使用 serial single-concurrency trace。
2. 至少运行 3 个串行请求，并优先使用第 3 个或更晚 warmed request 做 steady-state operator-fidelity 对比。
3. first/early request 的 cold-path TTFT/E2E 必须单独报告，不得混入算子集成精度结论。
4. single-request steady-state 误差来源分析完成前，不应先扩展到多请求 burst/scheduler 误差归因。

**违反后果**:
- 把 vLLM 冷启动/serving path overhead 误判为模型算子实现错误。
- 把调度层误差与算子层误差混在一起，无法定位修复方向。
- 导致对集成质量的结论不稳定，重复运行差异巨大。

**评估方法**:

```bash
python3 - <<'PY'
import csv

path = "pipeline_results/request_traces/vllm-0-21-0__model-deepseekv4-pro-n8__tp8-dp1-ep1-pp1__attn-v4-flashmla-sparse__blk256__gmu0-9__mbt4096__mseq256__w-fp8__kv-fp8__prefix-off__len4096__cg-piecewise/dataset-custom__qpsinf__ctx-1k__n3.csv"
with open(path, newline="") as f:
    rows = list(csv.DictReader(f))

assert len(rows) >= 3
ttft_ms = [float(r["ttft"]) * 1000 for r in rows[:3]]
arrived = [float(r["arrived_at"]) for r in rows[:3]]
e2e = [float(r["e2e_time"]) for r in rows[:3]]
print(f"ttft_ms={ttft_ms}")
print(f"arrivals={arrived}")
assert ttft_ms[2] < ttft_ms[0] * 0.2
for i in range(1, 3):
    assert arrived[i] >= arrived[i - 1] + e2e[i - 1] - 0.05
PY
```

## 代码位置

- vLLM serial trace: `pipeline_results/request_traces/.../dataset-custom__qpsinf__ctx-1k__n3.csv`
- Ontos serial output: `pipeline_results/simulator_outputs/.../dataset-custom__qpsinf__ctx-1k__n3/2026-05-31_21-24-23-502071/request_metrics.csv`
- single-request analysis: `pipeline_results/deepseek_v4_shrink/single_request_error_analysis_n8_tp8.md`
- pipeline runner: `automation/pipeline/trace_matrix.py`

## 标准解决方案

**正确做法**: 先运行 `n>=3, max_concurrency=1` 的真实 vLLM bench 和同 trace Ontos simulation；用第三个 warmed request 做算子级分析，前两个请求作为 cold-path overhead 单独报告。

**常见错误做法**: 用 single first request 或 10-request burst 的 E2E 百分比直接评价模型算子集成精度。

**自动化建议**: trace matrix/report 生成器在目标为 operator integration validation 时，自动要求 `num_prompts >= 3` 且 `max_concurrency=1`，并默认选择 request index 2 做 steady-state 对比。

## 相关关键点

- KP-0052: warmed request profiler 也不能替代非 profiler E2E ground truth。
- KP-0056: cold-path/client overhead 与 GPU profiling 完备性是不同问题。
- KP-0021: single-request 阶段仍要检查 op overlap 假设，但不引入多请求调度。

## 发现过程

最初多请求和 first request 的 E2E 误差很大，用户指出需要降低问题复杂度。切换到 serial single-concurrency 三请求后，第三个请求 TTFT 降到 61.0 ms，E2E 误差从 first request 的约 -38% 降到 steady-state 的约 -15.1%。这说明 first/early request 主要暴露 cold/serving path，不适合作为算子集成精度主证据。

