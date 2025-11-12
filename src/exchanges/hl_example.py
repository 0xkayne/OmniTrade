import ccxt
import asyncio
import yaml

with open("config/exchanges.yaml", "r") as f:
    exchanges_config = yaml.safe_load(f)

hl_network = exchanges_config['exchanges']['hyperliquid']['default_network']
hl_api_url = exchanges_config['exchanges']['hyperliquid']['networks'][hl_network]['rest_base_url']


# 初始化交易所实例
import os

# 从 secrets.yaml 读取 Hyperliquid 的密钥信息
with open("config/secrets.yaml", "r") as sf:
    secrets = yaml.safe_load(sf)

hl_secrets = secrets.get('hyperliquid', {})
wallet_address = hl_secrets.get('walletAddress', '0xYOUR_WALLET_ADDRESS')
private_key = hl_secrets.get('privateKey', '0xYOUR_API_PRIVATE_KEY')

# 可选的 vaultAddress（可根据实际需要调整）
vault_address = hl_secrets.get('vaultAddress', None)

ccxt_config = {
    'walletAddress': wallet_address,
    'privateKey': private_key,
    'enableRateLimit': True,
    'options': {
        'testnet': True  # 切换到测试网
    }
}
if vault_address:
    ccxt_config['options'] = {'vaultAddress': vault_address}

exchange = ccxt.hyperliquid(ccxt_config)
print(dir(exchange))

if False:
  # 加载市场
  markets = exchange.load_markets()
  print("Markets:", markets.keys())

  # 获取账户余额
  balance = exchange.fetch_balance()
  print("Balance:", balance)

  # 下一个限价单（举例）
  symbol = 'BTC/USDC:USDC'  
  amount = 0.01
  price = 30000
  side = 'buy'
  order = exchange.create_limit_order(symbol, side, amount, price)
  print("Order created:", order)

  # 查询订单状态
  order_id = order['id']
  fetched = exchange.fetch_order(order_id, symbol)
  print("Fetched order:", fetched)