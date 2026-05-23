# 交易所集成指南 (Exchange Integration Guide)

本文档总结了在 OmniTrade 项目中集成新交易所的关键步骤和最佳实践。后续接入新交易所时，结合此文档和新交易所的 API 文档，可以快速完成集成。

---

## 📁 项目结构概览

```
OmniTrade/
├── config/
│   ├── exchanges.yaml          # 交易所配置（URL、费率、交易对）
│   ├── secrets.yaml            # API 密钥（不提交到仓库）
│   └── volume_farming.yaml     # 刷量配置（指定刷量交易所）
├── src/
│   ├── core/
│   │   ├── base_exchange.py    # 抽象基类 ⭐ 必须继承
│   │   ├── exchange_factory.py # 工厂类 ⭐ 需要注册新交易所
│   │   └── volume_engine.py    # 刷量引擎
│   ├── exchanges/
│   │   ├── ccxt_exchange.py    # CCXT 适配器（Hyperliquid 等）
│   │   └── lighter_exchange.py # Lighter 原生 SDK 适配器 ⭐ 参考模板
│   ├── utils/
│   │   └── network_manager.py  # 网络切换 ⭐ 需要处理新交易所资源
│   └── main.py                 # 入口文件
```

---

## 🎯 集成决策树

```
新交易所是否已被 CCXT 支持？
│
├─ 是 → 使用 CCXTExchange 适配器
│       ├─ 在 exchanges.yaml 中配置 type: "ccxt"
│       └─ 可能需要在 ccxt_exchange.py 中添加特殊处理
│
└─ 否 → 创建原生 SDK 适配器
        ├─ 安装官方 SDK: uv add <sdk-package>
        ├─ 创建 src/exchanges/<name>_exchange.py
        ├─ 在 exchanges.yaml 中配置 type: "native"
        └─ 在 exchange_factory.py 中注册
```

---

## 📝 核心步骤

### Step 1: 创建交易所适配器类

新建 `src/exchanges/<name>_exchange.py`，继承 `BaseExchange`：

```python
from typing import Dict, Optional, List, Any
from src.core.base_exchange import BaseExchange, NetworkType

# 导入交易所 SDK
from <sdk_package> import Client, ApiClient

class NewExchange(BaseExchange):
    """新交易所适配器"""
    
    def __init__(self, name: str, config: Dict, secrets: Dict):
        super().__init__(name, config, secrets)
        
        # ⭐ 关键：API 客户端延迟初始化（在 connect() 中创建）
        self.api_client = None
        self.signer = None
        
        # 解析网络特定的密钥
        network_secrets = self.secrets.get(self.network_type.value, self.secrets)
        self._wallet_address = network_secrets.get('wallet_address')
        self._private_key = network_secrets.get('private_key')
        
        # 市场缓存
        self.markets: Dict[str, int] = {}
```

### Step 2: 实现必须的接口方法

```python
# ================== 连接管理 ==================

async def connect(self):
    """连接并初始化客户端
    ⭐ 关键：每次连接时重新创建 API 客户端（支持网络切换）
    """
    # 获取当前网络的 URL
    self.api_url = self.config['networks'][self.network_type.value]['rest_base_url']
    
    # 创建 API 客户端
    self.api_client = ApiClient(url=self.api_url)
    
    # 加载市场
    await self._load_markets()
    
    # 初始化签名器（如果有私钥）
    if self._private_key:
        await self._init_signer()
    
    self.logger.info(f"{self.name} {self.network_type.value} connected.")

async def close(self):
    """清理资源
    ⭐ 关键：关闭所有 aiohttp 会话，避免资源泄漏
    """
    if self.signer:
        try:
            await self.signer.close()
        except Exception as e:
            self.logger.warning(f"Error closing signer: {e}")
        self.signer = None
    
    if self.api_client:
        try:
            await self.api_client.close()
        except Exception as e:
            self.logger.warning(f"Error closing api_client: {e}")
        self.api_client = None
    
    await super().close()

# ================== 公共数据 ==================

async def fetch_orderbook(self, symbol: str, limit: int = 10) -> Dict:
    """获取订单簿
    返回格式：{'bids': [[price, size], ...], 'asks': [[price, size], ...]}
    """
    # 调用 SDK 获取数据
    raw = await self.api_client.get_orderbook(symbol)
    
    # 转换为标准格式
    return {
        'bids': [[float(b.price), float(b.size)] for b in raw.bids[:limit]],
        'asks': [[float(a.price), float(a.size)] for a in raw.asks[:limit]],
        'symbol': symbol,
        'timestamp': raw.timestamp
    }

# ================== 账户数据 ==================

async def fetch_balance(self) -> Dict:
    """获取账户余额
    返回格式：{'free': {'USDC': 100.0}, 'used': {...}, 'total': {...}}
    """
    account = await self.api_client.get_account()
    
    result = {'info': account, 'free': {}, 'used': {}, 'total': {}}
    
    # ⭐ 关键：解析可用余额/保证金作为 USDC（用于资金检测）
    if hasattr(account, 'available_balance'):
        balance = float(account.available_balance)
        result['free']['USDC'] = balance
        result['total']['USDC'] = balance
    
    return result

# ================== 交易功能 ==================

async def create_order(self, symbol: str, order_type: str, side: str, 
                       amount: float, price: float = None, params: Dict = None) -> Dict:
    """创建订单
    ⭐ 关键：需要处理市价单的滑点保护
    """
    market_id = self.markets.get(symbol)
    if market_id is None:
        raise ValueError(f"Unknown symbol: {symbol}")
    
    if order_type == 'market':
        # 市价单需要设置价格边界（滑点保护）
        orderbook = await self.fetch_orderbook(symbol)
        if side == 'buy':
            price = orderbook['asks'][0][0] * 1.01  # 1% 滑点
        else:
            price = orderbook['bids'][0][0] * 0.99
    
    # 调用 SDK 下单
    result = await self.signer.create_order(
        market_id=market_id,
        side=side,
        price=price,
        quantity=amount,
        order_type=order_type
    )
    
    return {'id': result.order_id, 'info': result}

async def cancel_order(self, order_id: str, symbol: str = None) -> Dict:
    """取消订单"""
    result = await self.signer.cancel_order(order_id)
    return {'id': order_id, 'status': 'canceled', 'info': result}
```

### Step 3: 注册到工厂类

编辑 `src/core/exchange_factory.py`：

```python
from src.exchanges.new_exchange import NewExchange

class ExchangeFactory:
    @staticmethod
    def create_exchange(name: str, config: Dict, secrets: Dict) -> BaseExchange:
        exchange_type = config.get('type', 'ccxt')
        
        if exchange_type == 'native':
            if name == 'lighter':
                return LighterExchange(name, config, secrets)
            elif name == 'new_exchange':  # ⭐ 添加新交易所
                return NewExchange(name, config, secrets)
            else:
                raise ValueError(f"不支持的native交易所: {name}")
        elif exchange_type == 'ccxt':
            return CCXTExchange(name, config, secrets)
```

### Step 4: 更新网络管理器

编辑 `src/utils/network_manager.py`，在 `switch_all_networks()` 中添加资源清理：

```python
async def switch_all_networks(self, network: NetworkType):
    for name, exchange in self.exchanges.items():
        # ⭐ 关闭旧连接
        if hasattr(exchange, 'api_client') and exchange.api_client:
            await exchange.api_client.close()
        if hasattr(exchange, 'signer') and exchange.signer:
            await exchange.signer.close()
        
        # 切换网络并重连
        exchange.switch_network(network)
        await exchange.connect()
```

### Step 5: 添加配置

#### exchanges.yaml

```yaml
exchanges:
  new_exchange:
    type: "native"
    enabled: true
    default_network: "testnet"
    networks:
      mainnet:
        rest_base_url: "https://api.newexchange.com"
        websocket_url: "wss://ws.newexchange.com"
      testnet:
        rest_base_url: "https://testnet.newexchange.com"
        websocket_url: "wss://testnet.newexchange.com/ws"
    rate_limit: 100
    symbols: ["ETH/USDC", "BTC/USDC"]
    fees:
      taker: 0.0003
      maker: 0.0001
```

#### secrets.yaml

```yaml
new_exchange:
  testnet:
    wallet_address: "0x..."
    private_key: "..."
    api_key: "..."
  mainnet:
    wallet_address: "0x..."
    private_key: "..."
```

---

## ⚠️ 常见问题与解决方案

### 1. "Unclosed client session" 错误

**原因**：aiohttp 会话未正确关闭

**解决**：
- 在 `__init__` 中只声明 `self.api_client = None`
- 在 `connect()` 中创建客户端
- 在 `close()` 中关闭所有客户端
- 在 `network_manager.py` 中切换前关闭旧连接

### 2. CCXT 特定交易所错误（如 Hyperliquid HIP3）

**原因**：CCXT 默认行为不适合

**解决**：在 `ccxt_exchange.py` 的 `_build_ccxt_config()` 中添加特殊处理：

```python
if self.name == 'hyperliquid':
    options['fetchMarkets'] = {'types': ['spot', 'swap']}  # 跳过 hip3
```

### 3. 密钥格式问题

**原因**：不同交易所对密钥格式要求不同

**解决**：
- 检查是否需要 `0x` 前缀
- 检查密钥长度要求
- 支持 testnet/mainnet 分离配置

### 4. 余额检测失败

**原因**：`fetch_balance()` 返回格式不正确

**解决**：确保返回包含 `'free': {'USDC': amount}` 用于资金检测

---

## ✅ 集成检查清单

- [ ] 创建 `src/exchanges/<name>_exchange.py`
- [ ] 继承 `BaseExchange` 并实现所有必须方法
- [ ] 在 `exchange_factory.py` 中注册
- [ ] 在 `network_manager.py` 中添加资源清理
- [ ] 添加 `exchanges.yaml` 配置
- [ ] 添加 `secrets.yaml` 配置
- [ ] 通过 `uv add <pkg>` 添加新 SDK 依赖(自动写入 `pyproject.toml` 和 `uv.lock`)
- [ ] 测试连接和基本功能
- [ ] 测试网络切换
- [ ] 测试与 VolumeEngine 集成
- [ ] 验证无资源泄漏（无 Unclosed session 错误）

---

## 📋 必须实现的方法列表

| 方法 | 用途 | 必须 |
|------|------|------|
| `connect()` | 初始化连接 | ✅ |
| `close()` | 清理资源 | ✅ |
| `fetch_orderbook()` | 获取订单簿 | ✅ |
| `fetch_balance()` | 获取余额 | ✅ |
| `create_order()` | 创建订单 | ✅ |
| `cancel_order()` | 取消订单 | ✅ |
| `switch_network()` | 切换网络 | 继承自基类 |
| `get_network_info()` | 获取网络信息 | 继承自基类 |

### 可选高级功能

| 方法 | 用途 |
|------|------|
| `set_leverage()` | 设置杠杆 |
| `close_position()` | 一键平仓 |
| `transfer_funds()` | 资金划转 |
| `fetch_positions()` | 获取持仓 |

---

## 🔗 参考文件

- **Lighter 完整实现**：`src/exchanges/lighter_exchange.py`
- **CCXT 适配器**：`src/exchanges/ccxt_exchange.py`
- **基类定义**：`src/core/base_exchange.py`
- **工厂类**：`src/core/exchange_factory.py`
- **网络管理器**：`src/utils/network_manager.py`

---

*最后更新: 2024-12-06*
