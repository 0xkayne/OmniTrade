# Orchestrator Details

This file is mechanically extracted from the preserved legacy skill copy. Keep updates in active split skills and KP files unless intentionally refreshing legacy-derived details.

## 核心原则：vLLM 是 Ground Truth

**本 skill 最重要的一句话：最终验证标准是 vLLM 端到端实测指标，不是 HuggingFace reference model。**

Reference model（model.py、kernel.py）的定位是**理解架构意图**——"这个模型想做什么"。但 Ontos 仿真器的验证对象是 vLLM serving 的实际表现。reference model 和 vLLM 实现之间几乎必然存在差异——差异的具体形式因模型而异，需要在 Step 0.V 中通过分析 vLLM 源码来发现，而非提前枚举。

这些差异可能出现在以下维度（不限于此，具体取决于模型）：

- **算子粒度**：reference 中多个独立 op 可能在 vLLM 中被融合为单个 kernel
- **执行拓扑**：reference 中的串行执行可能在 vLLM 中变为多流并行
- **Kernel 选择**：vLLM 可能使用与 reference 不同的 kernel 实现
- **量化行为**：vLLM 可能有 per-component 或 fused kernel 内部的特殊量化处理
- **配置约束**：vLLM 可能对某些配置有硬性约束或自动重写，而 reference 中没有
- **KV Cache 格式**：vLLM 可能有特定的二进制布局或 block size 约束

**以上仅为可能出现的差异类别，不是固定清单。每个模型的具体差异需要在 Step 0.V 中实际分析 vLLM 代码才能确定。**

本 skill 的分析流程因此是双源驱动的：
1. **Reference Model** — 理解"模型想做什么"（架构意图、数学定义、数据流）
2. **vLLM Implementation** — 理解"vLLM 实际怎么做的"（具体差异需要在分析中发现）

两者缺一不可。Reference model 提供正确的数学语义，vLLM 提供正确的工程实现。最终 plan 必须两者都对齐。

## 本 Skill 是什么

一个 **以 vLLM 为对齐标准的知识驱动模型集成引擎**。核心知识存储在 `docs/integration_keypoints/` 的 KP 约束库中。本 skill 从 reference model 提取架构意图，从 vLLM 实现提取工程约束，逐维度匹配 KP 约束，输出对齐 vLLM 的结构化实现计划。

```
知识层 (KP 约束库)           ← docs/integration_keypoints/  (持续积累)
    ↓ 约束驱动
分析层 (本 Skill)             ← 双源输入: Reference Model (架构意图) + vLLM (工程实现)
    ↓ 结构化输出
执行层 (Phase Skills)         ← 各维度的具体实现 (可人工可自动)
    ↓
验证层 (sim-bench)            ← 仿真 vs vLLM 实测对比 (端到端 + 算子级)
```

## 设计目标

1. **vLLM 对齐优先** — 任何维度分析都要以 vLLM 的实际实现为准，reference model 仅辅助理解
2. **模型无关** — 适用于任意 transformer-based LLM，不绑定任何特定模型
3. **知识积累** — 每次集成发现的坑自动沉淀为 KP，后续模型受益
4. **标准化评估** — 每个 KP 自带评估方案，可直接生成验证命令
5. **可持续进化** — KP 库持续扩充 → skill 的分析能力自动增强
6. **未知创新应对** — 遇到决策树无法分类的新颖架构时，自动切换到第一性原理分析模式

---

## 执行流程

```
输入: config.json + modeling_xxx.py + vLLM model_registry 中的模型注册名
      [可选: kernel.py, 论文/博客]
  │
  ├─ Step -1: 输入验证 + 相似度评估 + 增量检测
  │
  ├─ Step 0: 自动提取架构参数 → 特征向量 (基于 reference model)
  │
  ├─ Step 0.5: 参数量自动验证
  │
  ├─ Step 0.X: 新颖度检测 (标记未知维度)
  │     对每个维度的特征向量检查是否落入已知分类
  │     产出: Novel 维度列表 + 高/低新颖度评估
  │
  ├─ Step 0.V: vLLM 实现分析 (★ 关键步骤 — 从 reference 切换到 vLLM 视角)
  │     主代理通过 AtCode MCP 分析 vLLM 知识图谱 (项目名: vllm_v4_claude)
  │     若图谱不含目标模型 → Fallback 到本地源码读取 (~/vllm)
  │     产出: Fused Op Map + Parallel Map + Kernel Map + Config Rewrite Map
  │           + Profiling Wrapper Alignment Check
  │
  ├─ 维度 1~8: 逐维度分析 (每个维度同时参考 reference model 和 vLLM Dossier)
  │     探测 (从提取参数判断特征)
  │       → 分类 (落入哪个架构类型)
  │         → [新颖?] 切换到第一性原理分析模式 (见"未知创新架构处理协议")
  │         → [已知?] 命中 KP → 评估 → 产出
  │
  ├─ Step 8: 汇总 — 输出实现计划 + KP 命中矩阵 + 验证清单
  │
  └─ Step 9: 知识回灌 — 发现新约束 → 记录为新 KP
```

---


## 维度间依赖顺序 (DAG)

8 维度的分析依赖和实现依赖 (与 8.1 计划文件 Section 7 的 Phase 对应):

```
分析阶段 (维度 1~8):
  维度 1 (身份)          → 全部下游的基础
    ↓
  维度 2 (注意力)        ─┐
  维度 3 (FFN)           ├→ 三路并行
  维度 5 (量化)          ─┘
    ↓
  维度 4 (KV Cache)      ← 依赖维度 2 (需 KV cache size)
    ↓
  维度 6 (残差/特殊)     ← 依赖维度 2, 3
    ↓
  维度 7 (Decode加速)    ← 依赖维度 2, 3, 6
  维度 8 (Profiling)     ← 依赖维度 2, 3, 5, 6, 7

实现阶段 (对应 Phase 1~5):
  Phase 1: ModelConfig 注册        (维度 1)
    ↓
  Phase 2: Execution Plan 定义      (维度 2/3/4/5/6 → Op 清单 2.1)
    ↓
  Phase 3: Profiling 配置与执行      (维度 8 → Profiling 计划 2.3)
    ↓
  Phase 4: Simulator 集成            (维度 7 + 综合)
    ↓
  Phase 5: 验证                      (KP 命中矩阵 → 验证清单)
```

核心依赖链: **Op 清单 (Phase 2) → Profiling 计划 (Phase 3) → Simulator (Phase 4)**

---

## KP 库覆盖度

| 维度 | 已有 KP | 种子 KP | 覆盖度 | 需要补充 |
|------|---------|---------|--------|----------|
| 身份注册 | KP-0033,035,041 | - | 较好 | 更多 config 验证约束 |
| 注意力 | KP-0006,7,8,9,10,11,12,20,22,26,033,038,042,045 | KP-0015, 0017, 0019 | 较好 | Linear Attention, SSM_Hybrid |
| FFN/MoE | KP-0024,036 | KP-0016, 0018 | 基础 | Expert-Choice routing, Fine-grained experts |
| KV Cache | KP-0001~5,13 | - | 较好 | 更多压缩策略, 无 KV cache 模型 |
| 量化 | KP-0024, 040 | - | 较好 | KV cache 量化，算子量化 |
| 残差/特殊 | KP-0025 | - | 基础 | HC 详细约束 |
| Decode 加速 | KP-0014 | - | 基础 | MTP, Medusa, Speculative 约束 |
| Profiling | KP-0006,8,20,21,23,034,035,039,043,044,047,048,049,050,051,052,053,054,055,056,057 | KP-0018 | 较好 | 更多融合模式, 自适应 PD 分析 |
