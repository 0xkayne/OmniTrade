# KP-0013: ShadowRadix 的虚拟坐标系 + Shadow 投影是解决异构 KV Cache Prefix Caching 的核心设计

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0013 |
| 日期 | 2026-05-13 |
| 阶段 | attention |
| 严重度 | P2-中等 |
| 状态 | constraint_defined |
| 发现者 | 通过 SGLang 博客深入分析发现 |

## 问题描述

**现象**: V4 的 prefix caching 不能简单复用传统的 radix tree（假设单一 KV pool + 统一生命周期）。需要理解 ShadowRadix 的核心设计思想才能正确评估其复杂度和在模拟器中的简化方式。
**根因**: 传统 radix tree 的假设：
1. 一个逻辑 token 位置对应一个物理 KV entry
2. Entry 要么在 cache 中（可复用），要么不在（需重算）
3. 所有层的 KV 生命周期相同

V4 打破了这三个假设。ShadowRadix 的解决方案：

**虚拟全 token 坐标系**: Radix tree 仍然按完整 token 序列建索引（和传统一样），但每个 radix node 不直接对应物理 block，而是通过 **shadow（影子投影）** 映射到三个独立的物理池：

```
Radix Node (虚拟位置)
  ├── shadow_0 → SWA pool slot    （短寿命，128 token 后 tombstone）
  ├── shadow_1 → C4 pool slot     （长寿命，prefix cache 可复用）
  └── shadow_2 → C128 pool slot   （长寿命，prefix cache 可复用）
```

**Two-counter lock**: 每个 node 有两个独立的引用计数：
- `full_lock_ref`: 管理整个 node 的存活（包括 C4/C128 shadow）
- `swa_lock_ref`: 只跟踪 SWA shadow 是否仍在某个请求的滑动窗口内

当 `swa_lock_ref=0` 时，SWA slots 被 **tombstone（标记删除）**，但 radix node 仍在树中，C4/C128 shadow 继续活着且可被其他请求复用。

**SWA-safe matching**: 新请求做 prefix matching 时，从匹配点开始需要 128 个连续存活 token 才能延伸到 SWA 窗口内。这保证了 SWA 状态的完整性。

**表现**: 如果不理解这个设计，可能会：
- 误以为 Ontos 需要实现完整的 ShadowRadix → 过度设计
- 忽略 prefix caching 对 V4 的效果 → 低估共享前缀的性能提升

## 约束定义

**必须满足的条件**:
1. Ontos 模拟器**不需要**实现 ShadowRadix 的 radix tree 和 shadow 投影机制
2. 但需要建模 V4 prefix caching 的效果：compressed KV 可复用，SWA 不可复用（需重算）
3. 可简化为：prefix cache hit 时，C4/C128 的 compressed KV 直接复用（跳过 prefill），SWA 部分需要重算最后 128 token × num_layers 的 KV

**违反后果**: 对 V4 prefix caching 的建模过于复杂（实现 ShadowRadix）或过于简单（完全忽略）。

**检查方法**: 验证 V4 的 prefix cache 模型中，compressed 部分的 reuse 不需要 recompute，SWA 部分需要 bounded recompute（128 × layers）。

## 代码位置

- 无直接代码位置（这是架构设计决策）
- 相关: `ontos/kv_cache/base_kv_cache_manager.py` (prefix caching 逻辑)
- 相关: `ontos/execution_time_predictor/` (prefill 耗时建模)

## 标准解决方案

**正确做法**: Ontos 中 V4 的 prefix caching 简化建模：
1. Prefix cache hit 时：compressed KV（C4 + C128）直接复用，耗时 = 0
2. SWA 部分需要重建：recompute 代价 = 128 tokens × num_layers × attention 耗时
3. 这个 recompute 代价有固定上界（~128 × 61 = 8K tokens 的 prefill），不随 prefix 长度增长
4. 可以建模为一次额外的短 prefill 操作

**常见错误做法**:
- 完全禁用 V4 的 prefix caching（过于保守）→ 低估有共享前缀场景的 throughput
- 尝试在模拟器中实现完整的 ShadowRadix → 过度工程，模拟器不需要真正的内存分配
- 忽略 SWA recompute 的开销（过于激进）→ 低估 prefix cache hit 时的延迟

**自动化建议**: 验证 V4 prefix caching 模型的 recompute 代价 ≤ 128 × num_layers tokens 的 prefill 耗时。

## 相关关键点

- KP-0002: Prefix Caching 在混合 Attention 模式下不兼容（传统 prefix caching）
- KP-0009: V4 每层是 SWA + 压缩 Attention 的组合
- KP-0005: SWA 可能成为内存瓶颈（也影响 prefix caching 策略选择）

## 发现过程

深入阅读 SGLang 博客的 ShadowRadix 章节，理解了虚拟坐标系、shadow 投影、two-counter lock、tombstone、SWA-safe matching 等核心概念。结合 Together AI 博客描述的三种 SWA prefix caching 策略（full-store / periodic-checkpoint / recompute-on-hit），确认 ShadowRadix 实质上实现了 recompute-on-hit 策略（通过 tombstone 自动释放 SWA），而 Ontos 可以用 bounded recompute 模型来近似。
