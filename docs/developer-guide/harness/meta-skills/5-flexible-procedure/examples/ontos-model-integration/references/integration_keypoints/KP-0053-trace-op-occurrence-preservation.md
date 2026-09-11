# KP-0053: Trace 导出必须保留重复 Op Occurrence

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0053 |
| 日期 | 2026-05-31 |
| 维度 | profiling |
| 严重度 | P0 |
| 状态 | constraint_defined |
| 触发条件 | `需要从 Ontos chrome_trace.json 或 execution_time.to_dict() 做逐 op/逐 layer 误差分析` |
| 泛化标签 | trace_op_occurrence_preservation |
| 发现者 | DeepSeek V4-Pro N8 TP8 仿真/真实 bench 对齐验证 |

## 问题描述

**现象**: `chrome_trace.json` 中 `args.execution_time` 不能用于精确的 per-op 总量分析。重复 op 名称在 `ExecutionTime.to_dict()` 中按 key 覆盖，最终只保留最后一次 occurrence。

**根因**: `ExecutionTime.__init__()` 将 `time_list` 写入 `model_op_execution_times` 字典，key 是 `op_name`。同名 op 在多个 layer、多个 nested group 或多个 decode step 中重复出现时，前面的值会被后面的值静默覆盖。

**表现**: DeepSeek V4-Pro request-3 分析中，必须绕过 `chrome_trace.json`，从 predictor 内部 `_time_list` 重建 `ontos_req3_raw_ops_20260531_214604.csv`，才得到 23425 行重复 op occurrence。若直接使用 `to_dict()`，会严重低估重复算子总时长。

## 约束定义

**必须满足的条件**:
1. 任何 per-op/per-layer/per-kernel 对比都必须使用保留 occurrence 顺序的数据结构，例如 list 或 dict-of-lists。
2. `chrome_trace.json` 的 `args.execution_time` 如果来自 `ExecutionTime.to_dict()`，只能作为概览，不能作为聚合真值。
3. 导出 trace 时至少要保留 `op_name`、`occurrence_index`、`layer_or_stage`、`execution_time_ms`。
4. 分析报告必须声明使用的是 lossless occurrence list 还是 lossy dict。

**违反后果**:
- 重复算子被覆盖，per-op 总时间被静默低估。
- Ontos/vLLM operator error source 排名错误。
- 会把 trace 导出 bug 误判为模型算子集成精度问题。

**评估方法**:

```bash
.venv/bin/python3 - <<'PY'
from ontos.entities.execution_time import ExecutionTime, OpExecutionTime

et = ExecutionTime(
    1, 0, 0, 0, 0, 0,
    [OpExecutionTime("all_reduce", 1.0), OpExecutionTime("all_reduce", 2.0)],
)
print(et.to_dict())
print(f"occurrences={len(et._time_list)} dict_entries={len(et.to_dict())}")
assert len(et.to_dict()) < len(et._time_list)
PY
```

## 代码位置

- 文件路径: `ontos/entities/execution_time.py`
- 关键类: `ExecutionTime`, `OpExecutionTime`
- 关键函数: `ExecutionTime.__init__()`, `ExecutionTime.to_dict()`
- trace 使用点: `ontos/entities/batch_stage.py` 的 `BatchStage.to_chrome_trace()`
- 当前绕过产物: `pipeline_results/deepseek_v4_shrink/ontos_req3_raw_ops_20260531_214604.csv`

## 标准解决方案

**正确做法**: 保留 `_time_list` 的 ordered occurrence 语义。对外导出时增加 `execution_time_occurrences` list，或将 `execution_time` 改为 `op_name -> [occurrences]`，并在分析脚本中显式按 list 聚合。

**常见错误做法**: 从 `chrome_trace.json` 的 `args.execution_time` 直接 `sum(values())`，认为它代表 batch stage 中所有 op 总和。

**自动化建议**: trace 导出测试中构造两个同名 `OpExecutionTime`，断言导出的 occurrence 数等于输入 `_time_list` 长度；如果只剩一个 dict key，测试失败。

## 相关关键点

- KP-0047: op 完备性检查不能只看最终 dict key。
- KP-0054: rank-sum vs critical-path wall 的语义分析依赖 lossless op occurrence。
- KP-0048: vLLM kernel 边界对齐需要保留每次实际出现的 plan op。

## 发现过程

在抓取 vLLM 第三个请求 profiler 后，需要把 Ontos 的 operator 时间和 vLLM kernel family 逐组对比。直接检查 `chrome_trace.json` 发现同名 op 被覆盖，无法恢复每层/每次 decode 的重复 occurrence。最终通过 predictor 内部 `_time_list` 导出 raw op CSV 才完成分析。

