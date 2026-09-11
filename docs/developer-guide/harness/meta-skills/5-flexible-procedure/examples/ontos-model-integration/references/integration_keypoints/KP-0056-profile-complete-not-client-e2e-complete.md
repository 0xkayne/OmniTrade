# KP-0056: Profiling 数据完备不等于 Client E2E 模型完备

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0056 |
| 日期 | 2026-05-31 |
| 维度 | profiling |
| 严重度 | P1 |
| 状态 | constraint_defined |
| 触发条件 | `profile_audit.status == complete AND 目标指标包含 client-observed TTFT/TPOT/E2E` |
| 泛化标签 | profiling_data_vs_e2e_scope |
| 发现者 | DeepSeek V4-Pro N8 TP8 仿真/真实 bench 对齐验证 |

## 问题描述

**现象**: `profile_audit_n8_tp8_after_fused_qnorm_fix.json` 显示 DeepSeek V4-Pro N8/TP8 的 simulation-required GPU operator profiling 数据完整，`incomplete_requirements=0`。但 vLLM client-observed E2E 仍包含 CPU/serving/sampling/memory path 等 Ontos 当前未建模部分。

**根因**: profile audit 的范围是 simulation-required GPU/model operator 数据；它不代表 HTTP/API、streaming、tokenizer/detokenizer、scheduler loop、sampler、memory copy/fill、CUDA graph launch 等 client-visible serving overhead 都已建模。当前配置默认 `skip_cpu_overhead_modeling=true`，且 checkout 中没有 `data/profiling/cpu_overhead` 数据。

**表现**: 报告里如果只写“profiling 完整”，读者会误以为端到端 client E2E 的所有组成部分都已建模，进而把剩余误差错误归咎于 DeepSeek V4 GPU 算子。

## 约束定义

**必须满足的条件**:
1. profile audit complete 只能表述为 GPU/model operator profiling complete。
2. 如果最终指标是 vLLM bench 的 client-observed TTFT/TPOT/E2E，必须检查并报告 CPU/serving overhead modeling 是否启用、数据是否存在。
3. 当 `skip_cpu_overhead_modeling=true` 或 `data/profiling/cpu_overhead` 缺失时，最终报告必须明确写出该限制。
4. 不得用 GPU op profile complete 推导 client E2E complete。

**违反后果**:
- 隐藏数据边界，造成误导性精度结论。
- 将 serving/client overhead 误归因为模型算子 profile 缺失或实现错误。
- 对 first/early request 的冷路径误差无法解释。

**评估方法**:

```bash
python3 - <<'PY'
import json
from pathlib import Path

audit_path = Path("pipeline_results/deepseek_v4_shrink/profile_audit_n8_tp8_after_fused_qnorm_fix.json")
with audit_path.open() as f:
    audit = json.load(f)

config_py = Path("ontos/config/config.py").read_text()
cpu_profile_dir = Path("data/profiling/cpu_overhead")

print(f"profile_audit_status={audit['status']}")
print(f"incomplete={audit['summary']['incomplete_requirements']}")
print(f"cpu_overhead_profile_exists={cpu_profile_dir.exists()}")
assert audit["status"] == "complete"
assert audit["summary"]["incomplete_requirements"] == 0
assert "skip_cpu_overhead_modeling" in config_py and "default=True" in config_py
assert not cpu_profile_dir.exists()
PY
```

## 代码位置

- audit generator: `automation/deepseek_v4_shrink.py`
- pipeline disclosure: `automation/pipeline/trace_matrix.py`
- CPU overhead config: `ontos/config/config.py`
- predictor skip logic: `ontos/execution_time_predictor/sklearn_execution_time_predictor.py`
- audit artifact: `pipeline_results/deepseek_v4_shrink/profile_audit_n8_tp8_after_fused_qnorm_fix.json`

## 标准解决方案

**正确做法**: 报告中分开写两类结论：GPU/model operator profiling coverage 和 client E2E scope coverage。前者 complete 时实验可以继续；后者若缺 CPU/serving overhead，必须列为 residual limitation。

**常见错误做法**: 看到 `profile_audit.status=complete` 后，在最终报告中省略 CPU/serving overhead 缺失说明。

**自动化建议**: report generator 读取 profile audit 后，同时检查 `skip_cpu_overhead_modeling` 和 `data/profiling/cpu_overhead`。若目标列包含 E2E/TTFT/TPOT 且 CPU overhead 未建模，自动加入 disclosure block。

## 相关关键点

- KP-0047: simulation-required op 数据必须完备，缺失不能糊弄过去。
- KP-0044: serving-only 与 simulation-required op 必须区分。
- KP-0055: first/early request cold path 不能用于判断算子集成精度。

## 发现过程

用户强调 simulation 阶段必须使用 profiling 出来的数据，缺失不能糊弄过去。后续审计确认 N8/TP8 所需 GPU operator 数据完整，但单请求分析又显示 first/early request 有巨大 cold-path TTFT，且当前 CPU overhead modeling 被跳过。因此需要单独记录：profiling complete 不等于 client E2E 模型 complete。

