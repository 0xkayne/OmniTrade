# KP-0018: MoE 模型必须注册 All-to-All Profiling Backend

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0018 |
| 日期 | 2026-05-14 |
| 维度 | profiling |
| 严重度 | P1 |
| 状态 | seed |
| 触发条件 | `n_routed_experts > 0` |
| 泛化标签 | moe_all2all |
| 发现者 | 种子 KP (代码约定提取) |

## 问题描述

**现象**: MoE 模型在 Expert Parallelism 场景下需要 all-to-all 通信（发送 tokens 到对应 expert 所在的 GPU，收集结果），这部分的延迟必须被 profiling 和建模。
**根因**: Expert Parallelism 下，每个 GPU 只持有部分 expert。路由决策后，tokens 需要通过 all-to-all 发送到目标 expert 所在的 GPU，计算完成后再通过 all-to-all 收集回来。这个通信开销在 MoE 模型中占比较大。
**表现**: 如果不建模 all-to-all 延迟，MoE 模型的端到端延迟会被严重低估，尤其在多 GPU EP 场景下。

## 约束定义

**必须满足的条件**:
```
当 is_moe_model() == True 时:
  1. 必须在 ontos/profiling/collectives/all_to_all/ 注册至少一个 backend
  2. All-to-all profiling 必须覆盖目标 EP 并行度
  3. Simulation config 中必须配置 all-to-all backend
```

**违反后果**: 模拟中缺少 all-to-all 延迟 → MoE 执行时间低估 → 吞吐量高估。

**评估方法**:
```python
from ontos.config.model_config import BaseModelConfig
cfg = BaseModelConfig.create_from_name('model-name')
if cfg.is_moe_model():
    # 验证 all-to-all backend 已注册
    from ontos.profiling.collectives.all_to_all import get_all_to_all_backends
    backends = get_all_to_all_backends()
    assert len(backends) > 0, "MoE model requires at least one all-to-all profiling backend"
```

## 代码位置

- 文件路径: `ontos/profiling/collectives/all_to_all/`
- 相关 backend: naive, deepee_ll, deepee_ht, agrs
- 注册机制: backend 枚举 + wrapper 工厂

## 标准解决方案

**正确做法**:
1. 在 profiling config 中为 MoE 模型配置 all-to-all backend
2. 运行 all-to-all profiling 获取不同 message size 下的延迟
3. 在 simulation 的 execution plan 中包含 all-to-all op (dispatch + collect)

**常见错误做法**:
- 只在单 GPU 场景测试 MoE (all-to-all 延迟为 0)，多 GPU 时才发现遗漏
- 把 all-to-all 延迟混入 MoE FFN 的执行时间，而不是作为独立 op

## 相关关键点

- KP-0016: MoE expert 参数量独立计算
- KP-0008: compressor/indexer 需独立 profiling (类似的"额外 op 需独立 profiling"原则)

## 发现过程

种子 KP — 从框架 profiling 逻辑中提取。当前框架已支持多种 all-to-all backend (naive, DeepEP LL/HT, AGRs)，但 KP 库中没有显式声明 "MoE → 必须 all2all" 的约束。新集成 MoE 模型时容易忘记这一步。
