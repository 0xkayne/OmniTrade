# KP-0057: 手工重建 Config 必须保留 Enum 类型语义

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0057 |
| 日期 | 2026-05-31 |
| 维度 | profiling |
| 严重度 | P2 |
| 状态 | constraint_defined |
| 触发条件 | `从 YAML/JSON 手工重建 dataclass config 或在 __post_init__ 后覆盖 config 字段` |
| 泛化标签 | config_enum_type_preservation |
| 发现者 | DeepSeek V4-Pro N8 TP8 仿真/真实 bench 对齐验证 |

## 问题描述

**现象**: 分析脚本如果从 YAML/JSON 手工重建 `RandomForestExecutionTimePredictorConfig`，再把 `all2all_backend` 等字段覆盖为字符串，可能破坏 `__post_init__()` 已完成的 Enum 转换。后续代码期望 `.value` 或 Enum 比较时，会走错分支或抛出异常，造成假的 profiling 缺失/列缺失问题。

**根因**: `BaseExecutionTimePredictorConfig.__post_init__()` 会把 `cache_mode`、`all2all_backend` 等字符串转换为 Enum。手工赋值绕过了 dataclass 初始化和 post-init 规范化。

**表现**: 例如 `all2all_backend` 应为 `All2AllBackend.NONE`，但被覆盖成 `"none"` 字符串后，依赖 `All2AllBackend.NONE` 比较或 `.value` 的代码可能失效。DeepSeek V4-Pro 对齐过程中，曾出现由手工 config 重建造成的 misleading missing-column/dispatch 分支问题，需要单独排除。

## 约束定义

**必须满足的条件**:
1. 从序列化配置恢复 predictor config 时，必须通过正式 dataclass/CLI 构造路径，让 `__post_init__()` 执行。
2. 不得在 `__post_init__()` 之后把 Enum 字段重新赋为裸字符串。
3. 分析脚本传入 predictor/model plan 前，必须断言关键字段类型，例如 `isinstance(config.all2all_backend, All2AllBackend)`。
4. 如果必须接受字符串输入，应立即调用对应 Enum 构造器归一化。

**违反后果**:
- profile audit 可能报出假的缺列、假缺 profile 或错误 backend。
- MoE all2all/fusedmoe 分支选择错误。
- 误把分析脚本 config bug 当成 DeepSeek V4 集成缺陷。

**评估方法**:

```bash
.venv/bin/python3 - <<'PY'
from ontos.config.config import RandomForestExecutionTimePredictorConfig
from ontos.types import All2AllBackend

c = RandomForestExecutionTimePredictorConfig()
assert isinstance(c.all2all_backend, All2AllBackend)
c.all2all_backend = "none"
print(f"after_manual_override_type={type(c.all2all_backend).__name__}")
assert not isinstance(c.all2all_backend, All2AllBackend)
PY
```

## 代码位置

- config normalization: `ontos/config/config.py` 的 `BaseExecutionTimePredictorConfig.__post_init__()`
- predictor loader: `ontos/execution_time_predictor/sklearn_execution_time_predictor.py`
- FFN backend logic: `ontos/execution_time_predictor/models/ffn.py`
- config builders: `automation/builders/ontos_main.py`

## 标准解决方案

**正确做法**: 使用 CLI/builder/dataclass constructor 统一构造 config；如果分析脚本必须从 dict 重建，应先过滤字段再调用 dataclass constructor，并在传给 predictor 前执行 Enum 类型断言。

**常见错误做法**: `cfg = RandomForestExecutionTimePredictorConfig(); cfg.all2all_backend = raw_yaml["all2all_backend"]`。

**自动化建议**: 增加一个轻量 config invariant check：对 `all2all_backend`、`cache_mode`、`attention_decode_kv_length_method` 等 post-init Enum 字段执行 `isinstance` 校验；失败时直接报 config reconstruction error。

## 相关关键点

- KP-0035: Profiling ModelConfig 必须接受所有 BaseModelConfig 字段。
- KP-0041: Config 继承链和默认值必须审计。
- KP-0036: MoE routing/all2all 分支依赖 config 语义正确。

## 发现过程

在 DeepSeek V4-Pro N8/TP8 误差分析中，需要手工复现实验 config 并调用 predictor/plan。过程中发现如果把序列化字符串直接覆盖到 post-init 后的 config 对象，会破坏 Enum 语义，进而产生与真实 profiling 数据无关的错误信号。该问题虽然不是模型算子本身，但会污染 profiling completeness 和 operator error source 分析。

