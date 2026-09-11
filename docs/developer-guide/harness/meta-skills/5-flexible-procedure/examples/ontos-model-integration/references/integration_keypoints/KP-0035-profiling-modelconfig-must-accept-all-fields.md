# KP-0035: Profiling ModelConfig 必须接受所有 BaseModelConfig 字段

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0035 |
| 日期 | 2026-05-18 |
| 阶段 | config |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 发现者 | DeepSeek V4 集成测试 |

## 问题描述

**现象**: `profiling/config/model_config.py` 的 `ModelConfig.__init__()` 是手动定义的参数列表，不自动包含 `BaseModelConfig` 的所有字段。当 `BaseModelConfig` 新增字段（如 V4 的 `o_lora_rank`, `hc_mult`, `compress_ratios`），`ModelConfig.from_model_name()` 使用 `asdict()` 展开所有字段作为 kwargs 传入，遇到未知字段就 TypeError 崩溃。

**根因**: profiling ModelConfig 是手动维护的参数子集，不是 BaseModelConfig 的子类。每次 BaseModelConfig 增加字段都需要同步到 profiling ModelConfig。

**表现**: `ModelConfig.from_model_name()` 对新模型崩溃: `TypeError: got an unexpected keyword argument 'xxx'`。

## 约束定义

**必须满足的条件**: profiling ModelConfig 的 `__init__` 必须接受 BaseModelConfig 的所有字段（即使不使用）。

**违反后果**: `ModelConfig.from_model_name()` 对新模型崩溃: `TypeError: got an unexpected keyword argument 'xxx'`。

**检查方法**: 新增 BaseModelConfig 字段后，运行 `ModelConfig.from_model_name(model_name)` 验证无 TypeError。

## 代码位置

- 文件路径: `ontos/profiling/config/model_config.py` — ModelConfig.__init__()
- 文件路径: `ontos/config/model_config.py` — BaseModelConfig (dataclass)

## 标准解决方案

**正确做法**: 在 profiling ModelConfig 的 `__init__` 末尾添加 `**kwargs` 参数吸收未知字段。

**常见错误做法**: 为每个新字段手动添加参数到 profiling ModelConfig。

**自动化建议**: 在 BaseModelConfig 字段变更时，自动测试 `ModelConfig.from_model_name()` 对所有已注册模型无报错。

## 相关关键点

无直接关联 KP，属于通用基础设施约束。

## 发现过程

V4 集成后 `ModelConfig.from_model_name("DeepSeek/DeepSeekV4-Pro")` 崩溃: `got an unexpected keyword argument 'o_lora_rank'`。
