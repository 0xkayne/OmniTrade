# 刷量模块使用指南

## 概述

OmniTrade 的刷量模块通过在不同交易所之间开对冲仓位的方式，以最小的价差成本刷交易量，赚取积分和空投奖励。

## 核心优势

### 1. 对冲策略 - 降低风险
- 同时在两个交易所开多空对冲仓位
- 价格波动影响相互抵消
- 只承担价差成本，不承担方向性风险

### 2. 反女巫机制 - 模拟真人
- **时间随机化**：开仓间隔和持仓时间都是随机的
- **仓位随机化**：使用对数正态分布，模拟真实交易者行为
- **交易所随机化**：随机选择交易所组合和做多做空方向

### 3. 智能风控 - 保护资金
- 价差检查：只在价差可接受范围内开仓
- 并发限制：限制同时持有的仓位数量
- 每日限额：设置每日总交易量上限
- 成本控制：限制单次价差成本

## 快速开始

### 步骤 1：配置交易所

确保 `config/exchanges.yaml` 中至少配置了 2 个交易所：

```yaml
exchanges:
  hyperliquid:
    type: "ccxt"
    enabled: true
    default_network: "testnet"
    symbols: ["ETH/USD", "BTC/USD"]
  
  paradex:
    type: "ccxt"
    enabled: true
    default_network: "testnet"
    symbols: ["ETH/USD", "BTC/USD"]
```

### 步骤 2：配置刷量目标

编辑 `config/volume_farming.yaml`：

```yaml
volume_farming:
  enabled: true
  
  # 时间配置 - 控制随机性
  timing:
    min_interval: 60        # 最少等 60 秒再开下一单
    max_interval: 300       # 最多等 300 秒
    min_position_lifetime: 600    # 最短持仓 10 分钟
    max_position_lifetime: 3600   # 最长持仓 1 小时
  
  # 仓位配置
  position:
    min_size: 0.01          # 最小 0.01 个币
    max_size: 0.1           # 最大 0.1 个币
    size_distribution: 'lognormal'  # 对数正态分布
  
  # 风险控制
  risk:
    max_spread_tolerance: 0.3     # 价差超过 0.3% 就不开仓
    max_spread_cost: 50           # 单次价差成本不超过 $50
    max_concurrent_positions: 5   # 最多同时持有 5 个仓位
    daily_max_volume: 100         # 每天最多刷 100 个币
  
  # 每日目标
  targets:
    - symbol: "ETH/USD"
      daily_target_volume: 10.0   # ETH 每天刷 10 个
      priority: 1
    
    - symbol: "BTC/USD"
      daily_target_volume: 1.0    # BTC 每天刷 1 个
      priority: 2
```

### 步骤 3：运行刷量模式

```bash
# 只运行刷量（推荐）
python -m src.main --network testnet --mode volume

# 同时运行套利监控和刷量
python -m src.main --network testnet --mode both
```

## 运行示例

### 启动时的输出

```
🚀 初始化套利机器人...
切换所有交易所到 testnet 网络...
  hyperliquid: 成功
  paradex: 成功

📊 当前网络状态:
  hyperliquid: testnet (测试网)
  paradex: testnet (测试网)

✅ 刷量引擎初始化完成
   刷量目标: 2 个交易对
✅ 机器人初始化完成 - 模式: volume, 已连接 2 个交易所

🔄 启动刷量模式...
开始刷量任务 - 交易对: ['ETH/USD', 'BTC/USD']
```

### 运行中的输出

```
准备开仓: ETHUSD_hyperliquid_paradex_1699876543 | Long@hyperliquid | Short@paradex | Size: 0.0234
✅ 对冲开仓成功: ETHUSD_hyperliquid_paradex_1699876543
   Long@hyperliquid: 1823.4500
   Short@paradex: 1824.1200
   Size: 0.0234
   Spread Cost: $0.0157

等待 127.3 秒后继续...

仓位 ETHUSD_hyperliquid_paradex_1699876543 达到最大持仓时间 3601s, 准备平仓
准备平仓: ETHUSD_hyperliquid_paradex_1699876543
✅ 平仓完成: ETHUSD_hyperliquid_paradex_1699876543 | 持仓时长: 3601s | PnL: $-0.0082
```

### 统计报告（每 5 分钟输出）

```
============================================================
📊 刷量统计报告
============================================================
活跃仓位: 3
历史仓位: 47
累计交易量: 1.2345
累计价差成本: $2.3456
累计盈亏: $-1.2345
平均价差成本: $0.0498
平均持仓时长: 1847.3秒
今日交易量: 8.7654
今日剩余额度: 91.2346
============================================================

=== 刷量进度报告 ===
总体进度: 8.77/11.00 (79.7%)

🔄 BTC/USD      |     0.77/    1.00 ( 77.0%) | 优先级: 2
🔄 ETH/USD      |     8.00/   10.00 ( 80.0%) | 优先级: 1
```

## 配置参数详解

### 时间配置 (timing)

| 参数 | 说明 | 推荐值 | 反女巫效果 |
|------|------|--------|-----------|
| `min_interval` | 最小开仓间隔（秒） | 60-300 | 避免固定频率 |
| `max_interval` | 最大开仓间隔（秒） | 300-600 | 增加不可预测性 |
| `min_position_lifetime` | 最短持仓时间（秒） | 300-600 | 避免秒开秒关 |
| `max_position_lifetime` | 最长持仓时间（秒） | 3600-7200 | 模拟真实交易 |

**建议**：间隔越大、持仓时间越长，越安全但刷量速度越慢。

### 仓位配置 (position)

| 参数 | 说明 | 推荐值 |
|------|------|--------|
| `min_size` | 最小仓位 | 根据交易所最小单位 |
| `max_size` | 最大仓位 | 根据资金量和目标 |
| `size_distribution` | 分布类型 | 'lognormal' 更自然 |

**重要**：使用对数正态分布而不是均匀分布，能更好地模拟真实交易者行为。

### 风险配置 (risk)

| 参数 | 说明 | 推荐值 | 影响 |
|------|------|--------|------|
| `max_spread_tolerance` | 最大价差% | 0.3-0.5 | 越小越保守 |
| `max_spread_cost` | 最大价差成本 | 根据预算 | 单次最大损失 |
| `max_concurrent_positions` | 最大并发仓位 | 5-10 | 控制风险敞口 |
| `daily_max_volume` | 每日交易量限制 | 根据目标 | 防止过度交易 |

### 目标配置 (targets)

```yaml
targets:
  - symbol: "ETH/USD"        # 交易对
    daily_target_volume: 50  # 每日目标交易量
    priority: 1              # 优先级（1-10，越大越优先）
```

**工作原理**：
- 系统会根据完成度和优先级加权选择交易对
- 未完成的目标会获得更高权重
- 优先级高的交易对会被更频繁选择

## 成本估算

### 单次开仓成本

假设：
- ETH 价格：$2000
- 两个交易所价差：0.2%（$4）
- 开仓大小：0.1 ETH

**成本计算**：
```
价差成本 = 价差 × 仓位大小
        = $4 × 0.1
        = $0.4
```

### 每日成本估算

假设配置：
- 每日目标：10 ETH
- 平均价差：0.2%
- ETH 价格：$2000

**每日成本**：
```
每日成本 = 交易量 × 价格 × 平均价差%
        = 10 × $2000 × 0.2%
        = $40
```

### 手续费（如果适用）

部分交易所可能收取手续费，需要额外计算：
```
手续费 = 交易量 × 价格 × 手续费率 × 2（开仓+平仓）
```

## 最佳实践

### 1. 从小额开始

```yaml
position:
  min_size: 0.001
  max_size: 0.01      # 先用小仓位测试
```

### 2. 选择价差小的交易所

- 优先选择流动性好的交易所
- 避免价差经常超过容忍度的组合
- 可以先运行套利监控模式观察价差

### 3. 设置合理的时间参数

**保守配置**（更安全）：
```yaml
timing:
  min_interval: 300      # 5 分钟
  max_interval: 1800     # 30 分钟
  min_position_lifetime: 1800   # 30 分钟
  max_position_lifetime: 7200   # 2 小时
```

**激进配置**（刷量更快）：
```yaml
timing:
  min_interval: 30       # 30 秒
  max_interval: 300      # 5 分钟
  min_position_lifetime: 300    # 5 分钟
  max_position_lifetime: 1800   # 30 分钟
```

### 4. 监控统计数据

关注以下指标：
- **平均价差成本**：如果持续偏高，考虑调整 `max_spread_tolerance`
- **平均持仓时长**：应该在配置的范围内随机分布
- **每日交易量进度**：按时完成目标

### 5. 分散到多个交易对

```yaml
targets:
  - symbol: "ETH/USD"
    daily_target_volume: 30
    priority: 1
  
  - symbol: "BTC/USD"
    daily_target_volume: 2
    priority: 1
  
  - symbol: "SOL/USD"
    daily_target_volume: 200
    priority: 2
```

## 风险警告

### 1. 价格波动风险

虽然是对冲策略，但在以下情况可能出现损失：
- 订单执行延迟导致价格变化
- 一边成功一边失败（系统会尝试紧急平仓）
- 极端行情下流动性不足

### 2. 交易所风险

- 交易所可能暂停交易
- API 可能限流或故障
- 账户可能被风控

### 3. 女巫检测风险

即使有反女巫机制，仍可能被检测到：
- 建议定期调整配置参数
- 不要在太多账户使用完全相同的参数
- 考虑手动添加一些随机性操作

## 常见问题

### Q: 为什么开仓很慢？

A: 可能原因：
1. `min_interval` 设置太大
2. 价差经常超过 `max_spread_tolerance`
3. 已达到 `max_concurrent_positions` 限制
4. 已达到 `daily_max_volume` 限制

**解决方法**：查看日志，根据具体原因调整配置。

### Q: 如何减少价差成本？

A: 方法：
1. 降低 `max_spread_tolerance`（但会降低开仓频率）
2. 选择价差更小的交易所组合
3. 在价差较小的时段运行（如流动性充足时）

### Q: 可以多账户并行运行吗？

A: 可以，但建议：
1. 每个账户使用不同的配置参数
2. 错开运行时间
3. 使用不同的交易所组合

### Q: 如何估算能赚多少积分？

A: 取决于各交易所的积分规则：
- 有些按交易量计算
- 有些按交易次数计算
- 有些有持仓时长要求

建议先查看目标交易所的积分规则文档。

## 进阶技巧

### 1. 根据时段调整策略

不同时段价差不同，可以：
- 流动性好的时段（美股交易时段）：使用更严格的价差限制
- 流动性差的时段：放宽价差限制或暂停运行

### 2. 动态调整目标

根据完成进度调整：
```python
# 可以编写脚本定期更新 volume_farming.yaml
# 例如：快完成的交易对降低优先级，未完成的提高优先级
```

### 3. 多策略组合

```bash
# 终端 1：运行刷量
python -m src.main --mode volume --network testnet
# python -m src.main --mode volume --network mainnet

# 终端 2：运行套利监控（观察价差变化）
python -m src.main --mode arbitrage
```

## 总结

刷量模块是一个功能完善、风险可控的工具，通过合理配置可以：
- ✅ 安全地完成刷量目标
- ✅ 最小化价差成本
- ✅ 避免被识别为机器人
- ✅ 灵活应对各种场景

**记住**：先在测试网充分测试，确认策略可行后再切换到主网！

