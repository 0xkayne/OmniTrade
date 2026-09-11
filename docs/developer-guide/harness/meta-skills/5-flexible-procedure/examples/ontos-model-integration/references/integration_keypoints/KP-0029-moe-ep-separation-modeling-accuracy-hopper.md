# KP-0029: MoE EP 分离式建模在 Hopper 上的精度充分性

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0029 |
| 日期 | 2026-05-15 |
| 阶段 | execution |
| 严重度 | P1 |
| 状态 | constraint_defined |
| 发现者 | EP 策略分析 |

## 问题描述

**现象**: 框架将 MoE EP 拆为 6 个独立 op (TopK → Dispatch → Prepare → FusedExperts → Finalize → Combine)，逐 op 查 profiling CSV 求和。可能误判为低估真实延迟（认为缺少通信-计算重叠）。
**根因**: 分离式建模是否准确取决于目标平台上 EP 的实际执行方式。Hopper 上 DeepEP HT/LL 是分离式 kernel 串行执行（无 overlap），因此逐 op 求和与实际一致。Blackwell 上 MegaMoE 是单 kernel 融合（通信-计算流水线重叠），逐 op 求和会系统性高估延迟。
**表现**: 若在 Hopper 上错误引入 overlap 模型，会低估延迟；若在 Blackwell 上用分离式建模，会高估延迟。

## 约束定义

**必须满足的条件**: EP 建模方式必须匹配目标平台的实际执行模式。Hopper 用分离式建模（当前方式）；Blackwell 需要融合 op 模型。
**违反后果**: Hopper 上引入不必要的 overlap 补偿会低估延迟；Blackwell 上使用分离式建模会高估 1.5~1.96× 延迟。
**检查方法**: 确认目标平台 (sm_90 vs sm_100) 后选择对应建模方式。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/models/ffn.py`
- 关键函数/类: `MoEFFN.get_execution_plan()` (lines 40-173)
- 相关文件: `ontos/execution_time_predictor/ops/moe.py` (_MoEOpBase), `ontos/execution_time_predictor/ops/communication.py` (_MoEAll2AllBase)

## 标准解决方案

**正确做法**: H100 测试直接使用现有 DEEPEPHT/DEEPEPLL 分离式建模路径，无需修改。
**常见错误做法**: (1) 误以为所有平台都需要融合建模而引入不必要的复杂度；(2) 看到 MegaMoE 论文数据后误判 Hopper 也需要融合路径。
**自动化建议**: 在 config 中增加 `gpu_arch` 字段 (sm_80/sm_90/sm_100)，自动选择 EP 建模方式。Hopper→分离式，Blackwell→融合式。

## 相关关键点

- KP-0030: MegaMoE 平台约束与 Backend 选择 (同一分析的不同方面)
- KP-0020: 异构融合 kernel (融合 op 的通用处理方式)
- KP-0018: MoE All-to-All Profiling Backend 注册

## 发现过程

分析 DeepSeek-V4 技术报告中提到的 MegaMoE (DeepGEMM) EP 实现，研究其对 Ontos 模拟精度的影响。发现 MegaMoE 仅限 Blackwell，H100 上 vLLM 走 DeepEP HT/LL 分离式 pipeline，与当前框架建模方式一致。确认 H100 测试无需修改 EP 建模策略。
