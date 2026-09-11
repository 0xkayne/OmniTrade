# Plan Validator Details

This file is mechanically extracted from the preserved legacy skill copy. Keep updates in active split skills and KP files unless intentionally refreshing legacy-derived details.

### 8.2 Subagent 任务分解规则

根据 8.1 计划文件中的 **Section 7 (实现任务分解与依赖)** 生成为 subagent 任务。任务按 Phase 组织，与计划中的 Phase 对应。

**任务粒度原则:**
- 每个计划中的 Task = 1 个 subagent 调用
- Phase 内无依赖的 Task 可同时启动
- Phase 间的依赖必须满足后才能启动下一 Phase
- Novel 维度的 Task 可能需要先启动研究型 subagent (Phase A-E 协议)

**任务生成模板:**

> ### Phase N, Task N.M: <任务名>
>
> **依赖:** Task X.Y (必须先完成)
> **可并行:** Task N.K (可同时执行)
> **类型:** 实现 / 研究 (Novel 维度先研究)
> **涉及 KP:** KP-XXXX, KP-YYYY (命中的 KP)
> **涉及 Op (来自 2.1 清单):** op_a, op_b, ...
> **涉及文件:**
>   - 新建: path/to/new_file.py
>   - 修改: path/to/existing_file.py
> **具体工作:**
>   1. <步骤1 — 引用 2.1 的 Op 定义和 2.3 的 Profiling 参数>
>   2. <步骤2>
> **验证:**
>   - <来自第 5 节 KP 命中矩阵的验证方法>
> **Keypoint 捕获接口:**
>   如果工作中发现新的约束或错误，调用 keypoint-capture:
>   → 维度: <维度名>
>   → 现象: <描述>
>   → 触发条件: <什么模型特征会触发此问题>


**Phase 依赖关系:**

```
Phase 1: 基础注册          (无依赖，可立即开始)
  ↓
Phase 2: Execution Plan    (依赖 Phase 1)
  Task 2.1 (Attention) ─┐
  Task 2.2 (FFN)        ├─ 可并行
  Task 2.4 (量化)       ─┘
  Task 2.3 (KV Cache)    (依赖 Task 2.1)
  ↓
Phase 3: Profiling        (依赖 Phase 2)
  Task 3.1 (attention/) ─┐
  Task 3.2 (mlp/)        ├─ 可并行
  Task 3.3 (collectives/)─┘
  ↓
Phase 4: Simulator 集成   (依赖 Phase 2 + Phase 3)
  ↓
Phase 5: 验证             (依赖全部)
```

**跳过规则:** 如果某个 Phase 的所有 Task 对应的维度分析结果为"复用现有，无需改动"，则跳过整个 Phase。

**增量模式规则:** 如果 `--base-model` 指定了同系列模型，相同维度标记为"继承"，不生成对应 Task。

### 8.3 Subagent 执行上下文

每个 subagent 启动时必须知道:

```
必须传入:
  1. 模型名称和 HuggingFace ID
  2. config.json 路径
  3. model.py 路径
  4. 该维度的分析结果（类型、特征向量值）
  5. 命中的 KP 列表（读取 KP 文件获取完整约束）
  6. 需要操作的文件列表
  7. 验证命令
  8. 相似度报告中最接近模型的实现路径 (参考代码)

可按需读取:
  9. 本 skill (model-integrator) 的对应维度章节（决策树、实现路径）
  10. 现有相似模型的实现代码（作为参考）
  11. docs/integration_keypoints/ 中的 KP 文件
```

### 8.4 Subagent 失败恢复协议

当 subagent 执行失败时，按以下流程处理:

```
Subagent 报错
  │
  ├─ Step 1: 立即调用 keypoint-capture 记录错误
  │   → 维度: 当前维度
  │   → 现象: 报错信息
  │   → 根因: 分析失败原因
  │   → 严重度: 根据影响评估
  │
  ├─ Step 2: 判断错误类型
  │   已命中 KP 的约束违反?
  │     YES → KP 的评估方法或约束定义不够准确 → 更新 KP
  │     NO → 新约束 → 创建新 KP
  │
  │   代码逻辑错误?
  │     → 修复后重试
  │
  │   架构理解错误?
  │     → 回滚该维度改动 → 重新进入 Phase A (原始信息收集)
  │
  ├─ Step 3: 恢复策略
  │   简单错误 (< 3 次重试) → 修复后重试当前任务
  │   理解性错误 → 回滚 → 生成新的研究型 subagent 任务
  │   框架限制 → 记录 KP (severity=P0) → 人工介入
  │
  └─ Step 4: 更新计划
      将失败教训和 KP 更新写入集成计划
      通知后续依赖任务的 subagent 注意新发现的约束
```

### 8.5 Subagent 与 keypoint-capture 的接口

subagent 在执行过程中发现新约束时，调用 keypoint-capture:

```
调用方式: 直接使用 /keypoint-capture 触发

传入参数:
  - 维度: <从 8 个维度中选择>
  - 现象: <发生了什么>
  - 根因: <为什么会这样>
  - 触发条件: <什么模型特征会命中此问题>
  - 评估方法: <如何验证>
  - 严重度: P0/P1/P2/P3
```

### 8.6 计划执行后的闭环

```
计划执行 → subagent 工作 → 发现新 KP
  ↓
keypoint-capture 记录 KP
  ↓
kp-integration-protocol 优化 model-integrator (更新索引表/决策树/泛化标签)
  ↓
下一个模型集成时，model-integrator 自动命中新 KP
```

### 8.7 Plan 完备性校验 (★ 强制步骤)

在 Step 8 生成计划后、进入 Phase 1 执行前，**必须**执行以下 5 项校验：

#### 校验 1: Op List ↔ Profiling Plan 1:1 完备性 [KP-0047]

```
遍历 2.1 Per-Layer Op 清单中的每个 op name:
  对每个 op，检查 2.3 Profiling 计划中是否有对应条目
  缺失 → 报错并要求补充 (即使是轻量 op)

遍历 2.3 Profiling 计划中的每个条目:
  对每个条目，检查 2.1 中是否有对应 op
  多余 → 说明 profiling 计划有冗余

目标: 差集为空 (两侧完全匹配)
```

#### 校验 2: Op 粒度对齐 vLLM [KP-0048]

```
对 2.1 中的每个 op:
  检查该 op 是否在 Step 0.V 的 Fused Op Map 中有 vLLM kernel 对应
  如果 op 是按 reference model 逐行展开的粒度 → 警告并建议合并为 vLLM 融合粒度

检查 vLLM 中执行的 kernel 是否都在 2.1 中有对应 op
  缺失 → 遗漏了 vLLM 实际执行的 kernel
```

#### 校验 3: 标准 8 列格式 [KP-0047]

```
检查 2.1 的每个表格是否包含完整的 8 列:
  Op | Profile | Shape | Kernel | 量化 | Prefill | Decode | 框架基类

缺失列 → 报错并要求补充
Shape 列格式: Linear 用 [M,K]x[K,N], ElementWise 用 elem=MxD
```

#### 校验 4: 参数值源码级验证 [KP-0050]

```
对 2.1 中每个有 Shape 列的 op:
  从 Step 0.V 的 AtCode MCP 源码提取对应 Linear 初始化的参数:
    MergedColumnParallelLinear: 检查 output_sizes 列表
    ColumnParallelLinear: 检查 output_size
    RowParallelLinear: 检查 input_size/output_size
  追踪条件因子推导链 (如 coff = 1 + overlap):
    确认 plan 的 Shape 中是否正确包含了条件因子

  重点检查:
  - 条件因子 (coff, overlap) 是否传播到最终 shape
  - einsum 类 op 的维度顺序是否与源码 einsum 表达式一致
  - 元素数是否使用了正确的张量维度 (n_local_heads vs n_groups)
  - compress_ratios 层数统计是否与 config.json 数组一致

  差异 → 报错并列出具体参数偏差
```

#### 校验 5: Model-Level Ops 覆盖 [KP-0051]

```
检查 Step 0.V Phase 2.3 的 Model-Level 执行流产出:
  如果有 model-level op (hc_head, final_norm 等):
    → 检查 2.1 是否有对应的 "Model-Level Ops" 段落
    → 检查 2.3 是否有对应的 profiling 条目
    缺失 → 报错并要求补充

  如果 Step 0.V 未执行 Phase 2.3:
    → 警告: "未追踪 Model.forward() 的 model-level op，可能遗漏"
```

**校验结果处理:**
- 任何校验失败 → 在计划文件中标注 "⚠ 校验失败"，列出具体问题
- 全部通过 → 在计划文件中标注 "✓ 完备性校验通过"

---
