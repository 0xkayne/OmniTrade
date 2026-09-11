# KP-0031: DeepEP HT vs LL Backend 选择策略

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0031 |
| 日期 | 2026-05-15 |
| 阶段 | execution |
| 严重度 | P1 |
| 状态 | constraint_defined |
| 发现者 | EP 策略分析 |

## 问题描述

**现象**: DeepEP 提供两种 backend (High-Throughput 和 Low-Latency)，选择不当会导致模拟严重偏离实际。
**根因**: HT 和 LL 有完全不同的执行语义、通信组定义和融合行为:
- HT: 标准缓冲区模式，适合大 batch prefill
- LL: 低延迟 chunked 模式，适合小 batch decode，且 combine 中融合了 finalize

**表现**:
1. 通信组混淆: HT/LL 用 EP group (`ep_size = dp_size × tp_size`)，NAIVE/AGRS 用 DP group (`dp_size`)
2. LL finalize 融合: DeepEP LL 的 combine kernel 内部完成了 weight+reduce，不需要单独的 finalize op
3. LL chunked 执行: `chunk_size = moe_dp_chunk_size × dp_size`，大 batch 需要多 chunk 累加

## 约束定义

**必须满足的条件**:
1. DeepEP HT/LL 的通信组是 EP group (非 DP group)
2. DeepEP LL 不需要独立的 finalize op (`has_standalone_finalize = False`)
3. DeepEP LL 的 chunk 累加由 `_MoEOpBase.get_execution_time()` 处理
4. vLLM serving 模式下会根据 batch 大小自动切换 HT/LL，Ontos 模拟需相应处理

**违反后果**: 通信组用错会导致查表时 dp_size 不匹配；遗漏 finalize 融合会多算一个 op 的耗时；chunk 模型不正确会导致大 batch 延迟预测偏差。

**检查方法**: (1) 检查 `communication.py:165-167` 的 `_comm_group_size` 赋值; (2) 检查 `ffn.py` 的 `has_standalone_finalize` 条件; (3) 检查 `moe.py:24-40` 的 chunk 累加逻辑。

## 代码位置

- 文件路径: `ontos/execution_time_predictor/ops/communication.py`
- 关键行: L165-167 (通信组选择), L186 (_is_deepepll), L188-224 (get_execution_time 含 chunk 逻辑)
- 文件路径: `ontos/execution_time_predictor/ops/moe.py`
- 关键行: L24-40 (chunk 累加), L124-135 (NoCommFinalize 对 DEEPEPLL 返回 0)
- 文件路径: `ontos/execution_time_predictor/models/ffn.py`
- 关键行: L69-92 (has_standalone_prepare/finalize 决策)
- 文件路径: `ontos/profiling/collectives/all_to_all/deepepll/impl.py`
- 关键行: buffer `low_latency_mode=True`, NVSHMEM RDMA, 需要 ≥4 IB 端口

## 标准解决方案

**正确做法**:
- Prefill heavy 场景 → DeepEP HT (`dispatch_dtype=bf16` 或 `fp8`)
- Decode heavy 场景 → DeepEP LL (chunked, `moe_dp_chunk_size=256`)
- Mixed 场景 → 根据 per-step token 数动态选择 (大 batch→HT, 小 batch→LL)

**常见错误做法**:
1. 将 DeepEP HT/LL 的通信组设为 `dp_size` (应该是 `ep_size`)
2. 为 DeepEP LL 添加独立的 finalize op (已融合在 combine 中)
3. 忽略 chunk 累加，直接用总 token 数查 LL 的 profiling 表

**自动化建议**: 在 execution plan 构建时，根据 `all2all_backend` 枚举值自动设置 `_comm_group_size`、`has_standalone_finalize` 和 chunk 逻辑。当前框架已实现此逻辑，无需额外自动化。

## 相关关键点

- KP-0029: MoE EP 分离式建模在 Hopper 上的精度充分性
- KP-0030: MegaMoE 平台约束与 Backend 选择
- KP-0032: V4 EP 特有参数对 Profiling 的影响
- KP-0018: MoE All-to-All Profiling Backend 注册

## 发现过程

深入分析 `ontos/execution_time_predictor/ops/` 中 MoE 和 communication ops 的实现，发现 DeepEP HT/LL 在通信组、finalize 融合和 chunk 语义上有重要差异，框架已正确处理但容易在集成新模型时忽略。
