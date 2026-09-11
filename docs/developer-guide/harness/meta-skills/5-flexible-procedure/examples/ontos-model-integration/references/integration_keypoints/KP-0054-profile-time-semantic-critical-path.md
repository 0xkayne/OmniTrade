# KP-0054: Profiling 时间语义必须区分 Rank-Sum 与 Critical-Path Wall

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0054 |
| 日期 | 2026-05-31 |
| 维度 | profiling |
| 严重度 | P0 |
| 状态 | constraint_defined |
| 触发条件 | `tensor_parallel_size > 1 AND profiling 数据用于 simulation execution time predictor` |
| 泛化标签 | rank_sum_vs_wall_time_semantics |
| 发现者 | DeepSeek V4-Pro N8 TP8 仿真/真实 bench 对齐验证 |

## 问题描述

**现象**: DeepSeek V4-Pro request-3 中，Ontos `fused_experts` decode 总时间 178.9 ms，几乎等于 vLLM low-overhead profiler 的 8-rank GPU self sum 180.0 ms，但 vLLM wall proxy 只有 32.6 ms。相反，TP collective/all-reduce Ontos 为 1159.6 ms，vLLM wall proxy 为 1783.4 ms，表现为明显 underprediction。

**根因**: 多 GPU profiling 数据存在至少两种不同语义：per-rank GPU self time 求和，以及 TP/DP group critical-path wall time。仿真中串行 execution plan 应消费 critical-path wall；如果误用 rank-sum，会高估 TP 并行 compute；如果通信模型低估同步路径，会低估 collective。

**表现**: Compute overprediction 和 communication underprediction 会互相抵消，使最终 E2E 误差看起来“还可以”，但逐 operator 误差非常大，修复方向会被掩盖。

## 约束定义

**必须满足的条件**:
1. 每份 multi-rank profiling 数据必须显式标注时间语义：`rank_self_sum_ms`、`rank_max_wall_ms`、`group_critical_path_ms` 或等价字段。
2. Ontos simulation 默认只能使用 TP/DP group critical-path wall time。
3. 报告中不得混用 rank-sum 和 wall time 计算误差百分比。
4. 当 vLLM profiler 中 `gpu_self_sum / wall_proxy` 远大于 1 时，必须检查该 op 是否被 TP 并行执行，不能把 rank-sum 直接喂给 serial plan。

**违反后果**:
- TP compute op 被系统性高估。
- collective/synchronization 被系统性低估。
- 误差互相抵消，导致“端到端误差小”但模型集成精度并不可信。

**评估方法**:

```bash
python3 - <<'PY'
import csv

path = "pipeline_results/deepseek_v4_shrink/req3_operator_error_sources_lowoverhead_20260531_221456.csv"
with open(path, newline="") as f:
    rows = {row["group"]: row for row in csv.DictReader(f)}

moe = rows["routed_moe_experts"]
ontos = float(moe["ontos_ms"])
wall = float(moe["vllm_wall_proxy_ms_lowoverhead_profiler"])
rank_sum = float(moe["vllm_gpu_self_sum_8ranks_ms_lowoverhead_profiler"])
print(f"fused_experts ontos={ontos:.1f}ms wall={wall:.1f}ms rank_sum={rank_sum:.1f}ms")
assert abs(ontos - rank_sum) / rank_sum < 0.05
assert wall < ontos * 0.3

collective = rows["tp_collective_all_reduce"]
print(
    "all_reduce "
    f"ontos={float(collective['ontos_ms']):.1f}ms "
    f"wall={float(collective['vllm_wall_proxy_ms_lowoverhead_profiler']):.1f}ms"
)
assert float(collective["vllm_wall_proxy_ms_lowoverhead_profiler"]) > float(collective["ontos_ms"])
PY
```

## 代码位置

- execution plan: `ontos/execution_time_predictor/execution_plan.py`
- MoE predictor: `ontos/execution_time_predictor/models/ffn.py`
- communication predictor: `ontos/execution_time_predictor/ops/communication.py`
- profiling loader: `ontos/execution_time_predictor/sklearn_execution_time_predictor.py`
- profile data: `data/profiling/compute/h100/DeepSeek/DeepSeekV4-Pro/fusedmoe_none.csv`
- network data: `data/profiling/network/h100_dgx/all_reduce.csv`
- current evidence: `pipeline_results/deepseek_v4_shrink/req3_operator_error_sources_lowoverhead_20260531_221456.csv`

## 标准解决方案

**正确做法**: profiling pipeline 同时记录 rank-sum 和 critical-path wall，并在 CSV schema 中显式命名。Predictor 训练和 simulation 查表默认使用 critical-path wall；rank-sum 只用于 GPU work/利用率分析。

**常见错误做法**: 对 8 张 GPU 的 per-rank self time 求和后，直接作为一个 request 在 TP=8 下的 op latency。

**自动化建议**: profile audit 增加 `time_semantics` 字段检查；当 `num_tensor_parallel_workers > 1` 且 CSV 只有 `time_stats.*.median` 而无语义标记时，报告 warning 或 failure。

## 相关关键点

- KP-0021: 跨 op 并行要求 `max` 而非 `sum`。
- KP-0048: op 粒度对齐 vLLM kernel 后，还必须对齐时间语义。
- KP-0018: MoE profiling backend 注册不足以保证计时语义正确。
- KP-0053: 该分析依赖保留重复 op occurrence 的 raw op list。

## 发现过程

用户质疑端到端误差过大后，改为单请求第三个 warmed request，并用 vLLM torch profiler 抓 GPU kernel。逐 operator 对比发现 `fused_experts` 的 Ontos 时间与 8-rank GPU self sum 近似相等，而不是与 vLLM wall proxy 对齐；同时 all-reduce 在 Ontos 中明显低估。这暴露出当前框架 profiling 数据语义未区分 rank-sum 与 critical-path wall。

