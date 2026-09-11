# KP-0002: Prefix Caching 在混合 Attention 模式下不兼容

## 基本信息

| 字段 | 值 |
|------|-----|
| ID | KP-0002 |
| 日期 | 2026-05-13 |
| 阶段 | kv_cache |
| 严重度 | P1-严重 |
| 状态 | constraint_defined |
| 发现者 | 通过代码阅读确认 |

## 问题描述

**现象**: 开启 prefix caching 时，V4 的混合 attention（SWA + compressed）会导致缓存不一致。
**根因**: Prefix caching 要求所有层共享相同的 KV cache 生命周期语义。但 SWA 层会自动驱逐旧 block（超出窗口的 token），而 compressed 层永久保留。共享的 prefix block 在 SWA 层已经被驱逐，但 prefix cache 认为它仍然有效。
**表现**: 当前代码已通过 `_caching_active` 标志正确处理了这个情况——多 group 或有 sliding_window 时自动禁用 prefix caching（`base_kv_cache_manager.py:71-75`）。

## 约束定义

**必须满足的条件**: `_caching_active` 的三个条件缺一不可：
1. `enable_caching` 配置为 True
2. 只有 1 个 KV cache group（`len(self.groups) == 1`）
3. 该 group 没有 sliding window（`self.groups[0].sliding_window is None`）

对于 V4，条件 2 和 3 都不满足（有 2 个 group，其中一个是 SWA），所以 **V4 模型不支持传统的 prefix caching**。

**违反后果**: 如果强制在 V4 上开启 prefix caching，会导致访问已释放的 block 或引用计数错误。

**检查方法**: 验证 V4 模型配置下 `_caching_active == False`。

## 代码位置

- 文件路径: `ontos/kv_cache/base_kv_cache_manager.py`
- 关键函数/类: `KVCacheManager.__init__()` 中的 `_caching_active` 计算
- 行号范围: L71-L75

## 标准解决方案

**正确做法**:
- V4 的 prefix caching 应通过 SGLang ShadowRadix 式的解耦生命周期实现（tombstone SWA，保留 compressed）
- Ontos 作为模拟器，可以选择：**不建模 V4 的 prefix caching**（简化），或建模一个 "compressed-only prefix reuse" 策略
- 短期建议：V4 模型下 `_caching_active=False`，prefix caching 开销记为 0

**常见错误做法**:
- 在多 group 场景下强行开启 prefix caching → 引用计数和驱逐逻辑不一致
- 忽略 `_caching_active` 的检查，以为配置了 `enable_caching=True` 就会生效

**自动化建议**: 单元测试中验证：对任何有多个 group 或含 sliding_window 的模型，`_caching_active` 必须为 False。

## 相关关键点

- KP-0001: V4 异构 KV cache 分组建模
- KP-0005: SWA 可能反直觉地成为内存瓶颈

## 发现过程

阅读 `base_kv_cache_manager.py` 的 `_caching_active` 计算逻辑，结合 V4 需要两个 group（SWA + compressed），确认 V4 不满足 prefix caching 的启用条件。SGLang 通过 ShadowRadix 的 tombstone 机制绕过了这个限制，但那需要完整的 radix tree 实现，不适合模拟器。
