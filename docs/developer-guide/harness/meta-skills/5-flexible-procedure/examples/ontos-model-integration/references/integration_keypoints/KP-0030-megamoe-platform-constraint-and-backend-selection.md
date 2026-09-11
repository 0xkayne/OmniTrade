# KP-0030: MegaMoE 平台约束与 Backend 选择

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0030 |
| 日期 | 2026-05-15 |
| 阶段 | execution |
| 严重度 | P1 |
| 状态 | constraint_defined |
| 发现者 | EP 策略分析 |

## 问题描述

**现象**: DeepSeek-V4 技术报告提到 MegaMoE 实现 1.5~1.96× 加速，容易误判为所有平台都应使用 MegaMoE。
**根因**: MegaMoE 是 DeepGEMM 库的融合 CUDA kernel，将 EP dispatch + 2×GEMM + SwiGLU + EP combine 融合为单个 kernel，通过 NVLink 通信与 Tensor Core 计算流水线重叠。但该 kernel 有严格平台要求。
**表现**: 平台支持矩阵如下:
- Blackwell (sm_100): `deep_gemm.fp8_fp4_mega_moe` 已合并 (DeepGEMM PR #304)
- Hopper (sm_90): `deep_gemm.fp8_mega_moe` RFC 阶段 (vLLM #42284, DeepGEMM #323)，未合并

## 约束定义

**必须满足的条件**: H100 测试必须使用 DeepEP HT/LL 分离路径 (框架已有 DEEPEPHT/DEEPEPLL backend)，不可使用 MegaMoE 融合路径。Blackwell 测试才需要新增 MegaMoE 融合 op。
**违反后果**: 在 H100 上尝试 profile 或模拟 MegaMoE 会因缺少 kernel 支持而失败，或产生无意义的模拟数据。
**检查方法**: 确认 GPU arch。sm_90 → DEEPEPHT/DEEPEPLL; sm_100 → MEGAMOE (需新增)。

## 代码位置

- 文件路径: `ontos/types/all2all_backend.py` (All2AllBackend enum)
- 文件路径: `ontos/profiling/collectives/all_to_all/` (DeepEP HT/LL runners)
- 相关外部: DeepGEMM PR #304 (MegaMoE), vLLM RFC #42284 (Hopper MegaMoE)

## 标准解决方案

**正确做法**: H100 测试使用 DEEPEPHT/DEEPEPLL，直接复用现有 profiling runner 和 prediction ops。
**常见错误做法**: (1) 看到 DeepSeek-V4 报告的加速数据就认为必须用 MegaMoE；(2) 在 H100 profiling 脚本中配置 MegaMoE backend。
**自动化建议**: 在 `All2AllBackend` enum 的 resolve 逻辑中，根据 GPU arch 自动限制可选 backend。sm_90 不允许 MEGAMOE。

## 相关关键点

- KP-0029: MoE EP 分离式建模在 Hopper 上的精度充分性
- KP-0031: DeepEP HT vs LL Backend 选择策略
- KP-0018: MoE All-to-All Profiling Backend 注册

## 发现过程

研究 DeepSeek-V4 技术报告中提到的 MegaMoE，查阅 DeepGEMM PR #304、vLLM RFC #42284 和相关 benchmark 数据，确认 MegaMoE 的平台支持状态和 vLLM 集成路径。
