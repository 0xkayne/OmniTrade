# KP-0019: 非标准 RoPE 配置可能影响 Attention 维度计算和 Backend 选择

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0019 |
| 日期 | 2026-05-14 |
| 维度 | attention |
| 严重度 | P2 |
| 状态 | seed |
| 触发条件 | `rope_scaling IS NOT NONE OR rope_theta != 10000.0` |
| 泛化标签 | rope_dimension |
| 发现者 | 种子 KP (代码约定提取) |

## 问题描述

**现象**: 部分模型使用非标准的 RoPE 配置（如 YaRN、NTK-aware scaling、Dynamic NTK），这些配置可能影响 attention 的维度拆分 (nope vs rope) 和 attention backend 的兼容性。
**根因**: RoPE 只作用于 `qk_rope_head_dim` 维度，而 `qk_nope_head_dim` 不做旋转。当 `rope_scaling` 改变了 effective rotation 维度或频率基数时，部分 attention kernel (如 flash_attention 的某些版本) 可能不支持。
**表现**: 如果 backend 不支持该 RoPE 配置，profiling 可能静默产生错误结果，或运行时 attention 计算异常。

## 约束定义

**必须满足的条件**:
```
1. 检查 rope_scaling 类型 (linear, dynamic, yarn, longrope, llama3 等)
2. 确认 attention backend 支持该 scaling 类型
3. 如果是新 scaling 类型:
   - 检查是否影响 head_dim 拆分 (nope + rope)
   - 检查是否影响 KV cache 存储格式
   - 检查是否需要自定义 RoPE kernel
```

**违反后果**: Attention kernel 不支持该 RoPE 配置 → profiling 数据错误 → 模拟精度下降。

**评估方法**:
```python
# 检查 rope_scaling 配置
if rope_scaling is not None:
    scaling_type = rope_scaling.get("type", "unknown")
    known_types = {"linear", "dynamic", "yarn", "longrope", "llama3"}
    if scaling_type not in known_types:
        print(f"WARNING: Unknown rope_scaling type: {scaling_type}")
        print("  → Verify attention backend compatibility")
```

## 代码位置

- 文件路径: `ontos/config/model_config.py` (rope 相关字段)
- 文件路径: `ontos/profiling/attention/attn_backend/` (backend 兼容性)

## 标准解决方案

**正确做法**:
1. 在 Step 0 提取 `rope_theta` 和 `rope_scaling`
2. 在维度 2 分析中确认 attention 类型后，检查 backend 兼容性
3. 标准 RoPE (theta=10000, 无 scaling) → 无需额外处理
4. 非 标准 RoPE → 确认 backend 支持，必要时新建 wrapper

**常见错误做法**:
- 忽略 `rope_scaling` 配置，直接用标准 flash attention
- 假设所有 attention backend 都支持所有 RoPE 变体

## 相关关键点

- KP-0006: 新增 attention backend 的扩展边界
- KP-0007: block_size 约束因 backend 而异

## 发现过程

种子 KP — 从框架 RoPE 处理逻辑中提取。越来越多的长上下文模型采用 YaRN (Qwen-2 128K), Dynamic NTK (CodeLlama), Llama-3 RoPE scaling 等非标准配置。当前 KP 库没有覆盖此约束。
