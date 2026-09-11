# KP-0047: Execution Plan Op 必须与 Profiling 数据 1:1 完备

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0047 |
| 日期 | 2026-05-22 |
| 维度 | profiling |
| 严重度 | P0-致命 |
| 状态 | constraint_defined |
| 触发条件 | `任何新模型的 execution plan 生成后` |
| 泛化标签 | profiling_op_completeness |
| 发现者 | DeepSeek V4 v2 集成计划生成过程中的 plan 审查 |

## 问题描述

**现象**: 集成计划的 2.1 节（Op 清单）列出的 op 与 2.3 节（Profiling 计划）的 profiling 条目数量不一致。6 个 op 在 2.1 中列出但 2.3 中没有对应的 profiling 条目。

**根因**: 将轻量 op（RMSNorm、RoPE、routing 逻辑等）视为"开销可忽略"而跳过 profiling。但 `execution_time_predictor` 按 op name 从 profiling CSV 中查表获取耗时数据。如果 op name 在 CSV 中找不到对应列，框架静默填 0（KP-0043），导致仿真系统性低估延迟。

**表现**:
1. 仿真结果与 vLLM 实测值偏差大
2. 轻量 op 的时间被静默设为 0，无法区分"设计如此"和"遗漏"

## 约束定义

**必须满足的条件**:
1. 集成计划 2.1 节（Op 清单）中的每一个 op name，必须在 2.3 节（Profiling 计划）中有对应的 profiling 条目
2. 即使 op 的耗时极小（如 RMSNorm、topk_routing），也必须产出 profiling CSV 数据行（可以是接近 0 的值）
3. Op name 必须在 execution plan 代码和 profiling CSV 列名之间严格一致（大小写、下划线/连字符）

**违反后果**: execution_time_predictor 查表失败，op 耗时被静默设为 0，仿真延迟系统性低估。

**评估方法**:
```bash
# 1. 从 execution plan 代码提取所有 op name
grep -oP 'op_name\s*=\s*["\x27](\w+)["\x27]' ontos/execution_time_predictor/models/<model>.py | sort -u

# 2. 从 profiling CSV header 提取所有列名
head -1 data/profiling/compute/h100/<Model>/mlp/*.csv | tr ',' '\n' | grep 'time_stats' | sort -u

# 3. 对比差集
diff <(step1_output) <(step2_output)  # 差集应为空
```

## 代码位置

- 文件路径: `ontos/execution_time_predictor/ops/_base.py:140-142` — NonAttention.load_df 缺失列填 0
- 文件路径: `ontos/execution_time_predictor/models/` — 各模型的 get_execution_plan() 定义 op name
- 文件路径: `ontos/profiling/mlp/` — profiling wrapper 输出 CSV

## 标准解决方案

**正确做法**:
1. 生成 op list 后，立即与 profiling 计划做 1:1 对比
2. 为轻量 op 创建 profiling 条目，标注为"极轻量，耗时接近噪声底"
3. 在 profiling wrapper 中为轻量 op 使用简单的计时（如 `torch.cuda.Event`）
4. CSV 数据行即使值为 0.001ms 也必须有数据

**常见错误做法**:
- 将 RMSNorm/RoPE/routing 标记为"可忽略"而跳过 profiling
- 假设轻量 op 的时间已"隐含在相邻 op 的测量中"——除非是明确的融合 kernel，否则不应做此假设
- 不做 op list 与 profiling plan 的完备性校验

**自动化建议**: 在 model-integrator 的 Step 8（生成计划）中增加自动校验步骤：遍历 2.1 的每个 op，检查 2.3 中是否有匹配条目。

## 相关关键点

- KP-0043: ElementWiseOp CSV 列缺失时静默返回 0 — 本 KP 的反面：如果不提供数据，就会触发 KP-0043 的行为
- KP-0027: V4 per-layer op 清单验证 — 本 KP 升级了 KP-0027 的约束：不仅 op list 要完整（对照代码），profiling plan 也要完整（对照 op list）

## 发现过程

生成 DeepSeek V4 v2 集成计划时，2.1 节列出 22 个 op 但 2.3 节只有 16 个 profiling 条目。用户指出："如果缺失了数据怎么读取？时间开销就算小，我们也要保证完整性"。这个约束对任何新模型集成都适用。
