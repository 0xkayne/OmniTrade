# 资金费率套利的理论基础与实践分析

> 这份文档记录了从"吃费率差"到"premium 均值回归"的理论演进过程。

## 1. 资金费率的本质

资金费率不是独立的价格信号，而是**永续合约价格偏离现货价格的校正机制**。

```
funding_rate ≈ perp_mark_price - spot_price   (简化形式)

perp > spot → rate > 0 → 多头付费给空头 → 抑制做多, 激励做空 → perp 价格被压回
perp < spot → rate < 0 → 空头付费给多头 → 抑制做空, 激励做多 → perp 价格被抬回
```

**费率是均值回归的。它的存在就是为了消灭自身。** 如果你今天看到一个 0.10% 的正费率，它正在被设计成会趋向于 0。

## 2. 为什么"吃费率差"赚不到钱

### 错误模型（PR 1-5 使用的）

```
profit = spread × notional × time - fees - slippage

假设: 双边交易所同时、对称地产生 funding 收益
```

### 为什么错

Funding 是**离散结算**的，不是连续累加：
- Hyperliquid: 每小时结算一次
- Binance: 每 8 小时结算一次
- 提前平仓 → 0 funding（没有按比例分配）

**不同步结算 → 时序风险：**

```
t=0:    HL rate=-0.05%, Binance rate=+0.03%, spread=0.08%
        开仓: long HL + short Binance

t=1h:   HL 结算, 收到 0.05%
        HL 新费率 = +0.01%（费率差消失）
        平仓: Binance 0 funding + 双边手续费
```

在这种场景下：
- HL 收入 = +0.05%
- Binance 支出 = 0
- 手续费 = 0.20%（5bp taker × 4 trades）
- **净亏损 = -0.15%**

日常的 0.05-0.10% funding spread 根本无法覆盖双边手续费。

## 3. 真正的利润来源: Premium 均值回归

当两个交易所的 perp 出现异常的 premium 偏离时，套利机会才出现。

```
正常状态: Binance BTC perp premium ≈ HL BTC perp premium ≈ 0%
异常状态: Binance +0.50%, HL -0.30% → spread = 0.80%
```

**这不是在赌 funding rate 差会持续，而是在赌 premium 会回归到均衡水平。**

### 利润分解

```
net_profit = funding_collected + premium_convergence_pnl - fees - slippage

其中:

premium_convergence_pnl = (premium_A_initial - premium_A_final) × direction_A
                        + (premium_B_final - premium_B_initial) × direction_B

当 premium 回归到均值:
  - long HL (premium=-0.30%): HL perp 价格上升 → +0.30%
  - short Binance (premium=+0.50%): Binance perp 价格下跌 → +0.50%
  - convergence_pnl = 0.30% + 0.50% = 0.80%

加上 funding:
  - HL 结算 1h: +0.30%
  - 总收益 = 0.80% + 0.30% = 1.10%

扣除成本:
  - fees = 0.20%
  - net = 1.10% - 0.20% = 0.90% ✓
```

### 什么情况下会出现这种大 spread

- 某个交易所出现流动性危机（大量强制平仓推高/压低 perp 价格）
- 市场剧烈波动时不同交易所反应速度不同
- 大户在单个交易所大额开仓导致短期 premium 偏离
- 某个交易所的做市商暂时下线

这些事件发生的频率不高，但一旦发生，spread 足够大就能覆盖所有成本。

## 4. 正确的策略框架

### 不是"持续吃费率差"

不要期望每天每个币种都有套利机会。正常市场条件下确实没有。

### 而是"异常检测 → 均值回归下注"

1. **持续监控 premium index**（perp mark price vs spot index price）— 这是领先指标
2. **当某个交易所出现显著偏离时触发信号**（例如 premium > 0.3% 或 < -0.3%）
3. **开对冲仓位**: long discount venue + short premium venue
4. **当 premium 回归到均衡水平时平仓**: 赚 convergence_pnl + 期间收到的 funding
5. **在首次结算后评估**: 如果 spread 已消失 → 平仓。如果还在 → 继续持有

### 关键指标: Premium Index（而非 Funding Rate）

| 指标 | 性质 | 为什么重要 |
|---|---|---|
| Funding Rate | 滞后指标（基于过去的 premium） | 告诉你过去发生了什么 |
| Premium Index | 领先指标（当前的市场定价偏差） | 告诉你现在发生了什么 |
| Next Funding Time | 结算时间 | 告诉你什么时候必须决策 |

Funding rate 是 premium 的滞后表示。Premium index 才是实时信号。当 premium 偏离时，funding rate 还没变，但套利窗口已经打开了。

## 5. 盈利充分条件

在下面的条件下，套利是确定盈利的:

```
条件 1: funding_rate_A 和 funding_rate_B 的方向必须相反
        （一个正一个负，意味着存在 cross-venue divergence）

条件 2: abs(premium_A) + abs(premium_B) > 2 × (fee_A + fee_B)
        （premium 回归的收益足以覆盖双边 4 笔手续费）

条件 3: 开仓前确认两个 venue 的 perp 都在正常交易中
        （避免在一方做市商缺失时开仓）
```

当这三个条件同时满足时：
- 即使 funding rate 在开仓后立刻消失，premium mean-reversion 的收益也足以 cover 所有成本
- 策略不依赖时间（不需要等 8 小时），只依赖价格的正常回归

## 6. 实践考量

### 为什么不总是有这种机会

- 市场大部分时间是有效的，cross-venue premium divergence 很小（< 0.05%）
- 当出现大 divergence 时，专业做市商和高频交易者会比我们更快
- 我们的优势是：可以坐等（HF 有资金成本）、可以跨 CEX（HF 通常只在一个交易所做市）

### 什么事件会创造机会

| 事件类型 | 典型 spread | 持续时间 | 频率 |
|---|---|---|---|
| 大户单边开仓 | 0.1-0.3% | 几分钟到几小时 | 每天数次 |
| 交易所限价单失衡 | 0.2-0.5% | 数十分钟 | 每周数次 |
| 强制平仓潮 | 0.5-2.0% | 几分钟 | 每月数次 |
| 极端波动 | 1.0-5.0% | 数分钟到数小时 | 每季度 |
