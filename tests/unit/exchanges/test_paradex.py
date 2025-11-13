import asyncio
import yaml
from src.core.base_exchange import NetworkType
from src.exchanges.ccxt_exchange import CCXTExchange


def _load_paradex_config():
    with open("config/exchanges.yaml", "r") as ef:
        exchanges_yaml = yaml.safe_load(ef)
    return exchanges_yaml["exchanges"]["paradex"]


def _load_paradex_secrets():
    with open("config/secrets.yaml", "r") as sf:
        all_secrets = yaml.safe_load(sf)
    return all_secrets.get("paradex", {})


def _ensure_required_secrets(secrets):
    eth_ready = bool(secrets.get("eth_private_key") and secrets.get("eth_wallet_address"))
    stark_ready = bool(
        secrets.get("stark_private_key")
        and secrets.get("stark_public_key")
        and secrets.get("stark_address")
    )
    if not eth_ready and not stark_ready:
        raise RuntimeError(
            "config/secrets.yaml 缺少 Paradex 所需字段：请提供 ETH 私钥+地址 "
            "或 Starknet private/public/address 组合之一"
        )


def _resolve_symbol(ccxt_client, config):
    preferred = config.get("symbols", [])
    for raw_symbol in preferred:
        if raw_symbol in ccxt_client.symbols:
            return raw_symbol
        # Paradex 交易对通常为 BTC/USD:USDC 格式
        candidate = f"{raw_symbol}:USDC" if ":USDC" not in raw_symbol else raw_symbol
        if candidate in ccxt_client.symbols:
            return candidate
    return ccxt_client.symbols[0]


async def test_paradex():
    print("\n=== 测试 Paradex ===")
    config = _load_paradex_config()
    secrets = _load_paradex_secrets()
    _ensure_required_secrets(secrets)

    exchange = CCXTExchange("paradex", config, secrets)
    exchange.network_type = NetworkType.TESTNET

    try:
        await exchange.connect()
        ccxt_client = exchange.ccxt_exchange

        markets = ccxt_client.markets
        print(f"已加载 {len(markets)} 个交易对")

        symbol = _resolve_symbol(ccxt_client, config)
        market = ccxt_client.market(symbol)
        print(f"选定交易对: {symbol}, 类型: {market.get('type')}, 精度: {market.get('precision')}")

        ticker = await ccxt_client.fetch_ticker(symbol)
        print(f"{symbol} 最新行情: {ticker.get('close')}, 日成交额: {ticker.get('quoteVolume')}")

        orderbook = await exchange.fetch_orderbook(symbol, limit=10)
        top_bid = orderbook["bids"][0] if orderbook["bids"] else None
        top_ask = orderbook["asks"][0] if orderbook["asks"] else None
        print(f"{symbol} 最优买: {top_bid}, 最优卖: {top_ask}")

        balance = await exchange.fetch_balance()
        print("账户余额:", balance)

        limits = market.get("limits", {}) or {}
        min_amount = limits.get("amount", {}).get("min")
        min_cost = limits.get("cost", {}).get("min")

        reference_price = None
        if top_ask:
            reference_price = float(top_ask[0])
        elif ticker.get("close"):
            reference_price = float(ticker["close"])
        if reference_price is None:
            raise RuntimeError(f"无法获取 {symbol} 的参考价格")

        trade_amount = float(min_amount) if min_amount else 0.01
        trade_amount = max(trade_amount, 0.01)
        if min_cost:
            required_amount = (float(min_cost) / reference_price) * 1.05  # 加 5% buffer
            trade_amount = max(trade_amount, required_amount)
        trade_amount = float(ccxt_client.amount_to_precision(symbol, trade_amount))

        print(f"使用 {symbol} 交易数量: {trade_amount}")

        market_order = None
        limit_order_id = None
        try:
            print("\n=== 测试市价单返回结构 ===")
            market_order = await exchange.create_order(
                symbol,
                "market",
                "buy",
                trade_amount,
            )
            print(f"✅ 市价单下单结果:")
            print(f"   完整返回: {market_order}")
            print(f"   订单ID: {market_order.get('id')}")
            print(f"   状态: {market_order.get('status')}")
            print(f"   成交量(filled): {market_order.get('filled')}")
            print(f"   剩余量(remaining): {market_order.get('remaining')}")
            print(f"   数量(amount): {market_order.get('amount')}")
            print(f"   平均价(average): {market_order.get('average')}")
            print(f"   价格(price): {market_order.get('price')}")
            
            # 等待一下再查询订单状态
            await asyncio.sleep(2)
            
            if market_order.get('id'):
                print("\n=== 查询订单状态 ===")
                fetched_order = await exchange.fetch_order(market_order['id'], symbol)
                print(f"   查询后的状态: {fetched_order.get('status')}")
                print(f"   查询后的成交量: {fetched_order.get('filled')}")
                print(f"   查询后的完整信息: {fetched_order}")

            positions = await ccxt_client.fetch_positions([symbol])
            print(f"{symbol} 当前仓位: {positions}")

            # 构造一个低于市价的买入限价单，测试挂单与撤单
            limit_price = reference_price * 0.95
            limit_price = float(ccxt_client.price_to_precision(symbol, limit_price))
            limit_order = await exchange.create_order(
                symbol,
                "limit",
                "buy",
                trade_amount,
                price=limit_price,
                params={"postOnly": True},
            )
            limit_order_id = limit_order.get("id")
            print(f"限价单下单结果: {limit_order}")
            await asyncio.sleep(2)

            open_orders = await ccxt_client.fetch_open_orders(symbol)
            print(f"{symbol} 挂单数量: {len(open_orders)}")

            refreshed_orderbook = await exchange.fetch_orderbook(symbol, limit=5)
            refreshed_top_bid = refreshed_orderbook["bids"][0] if refreshed_orderbook["bids"] else top_bid
            close_price = float(refreshed_top_bid[0]) if refreshed_top_bid else reference_price
            close_price = float(ccxt_client.price_to_precision(symbol, close_price))

            close_amount = trade_amount
            if positions:
                for pos in positions:
                    if pos.get("symbol") == symbol and pos.get("contracts"):
                        close_amount = float(ccxt_client.amount_to_precision(symbol, float(pos["contracts"])))
                        break

            close_order = await exchange.create_order(
                symbol,
                "market",
                "sell",
                close_amount,
                params={"reduceOnly": True},
            )
            print(f"平仓市价单结果: {close_order}")
            await asyncio.sleep(2)

        finally:
            if limit_order_id:
                try:
                    cancel_result = await ccxt_client.cancel_order(limit_order_id, symbol)
                    print(f"取消限价单结果: {cancel_result}")
                    await asyncio.sleep(1)
                    remaining_orders = await ccxt_client.fetch_open_orders(symbol)
                    print(f"{symbol} 撤单后挂单数量: {len(remaining_orders)}")
                except Exception as cancel_error:
                    print(f"取消限价单失败: {cancel_error}")

    finally:
        await exchange.close()


async def test_paradex_hedge_mode():
    """测试 Paradex 双向持仓模式"""
    print("\n" + "="*60)
    print("=== 测试 Paradex 双向持仓模式 ===")
    print("="*60)
    
    config = _load_paradex_config()
    secrets = _load_paradex_secrets()
    _ensure_required_secrets(secrets)

    exchange = CCXTExchange("paradex", config, secrets)
    exchange.network_type = NetworkType.TESTNET

    try:
        await exchange.connect()
        ccxt_client = exchange.ccxt_exchange
        symbol = 'ETH/USD:USDC'
        
        # 获取市场信息
        market = ccxt_client.market(symbol)
        limits = market.get("limits", {}) or {}
        min_cost = limits.get("cost", {}).get("min")
        
        # 获取当前价格
        ticker = await ccxt_client.fetch_ticker(symbol)
        price = ticker['last']
        
        # 计算满足最小成本的数量
        if min_cost:
            trade_amount = (float(min_cost) / price) * 1.1
        else:
            trade_amount = 0.05
        
        trade_amount = float(ccxt_client.amount_to_precision(symbol, trade_amount))
        print(f"\n1. 使用交易数量: {trade_amount} ETH (约 ${trade_amount * price:.2f})")
        
        # 检查当前仓位模式
        print("\n2. 检查当前仓位模式...")
        try:
            # 尝试设置双向持仓模式
            await ccxt_client.set_position_mode(hedged=True, symbol=symbol)
            print("   ✅ 已设置为双向持仓模式 (Hedge Mode)")
        except Exception as e:
            print(f"   ⚠️  设置双向持仓模式失败: {e}")
            print("   尝试获取当前持仓模式...")
        
        # 查询当前仓位
        positions = await ccxt_client.fetch_positions([symbol])
        print(f"\n3. 当前仓位:")
        for pos in positions:
            if pos.get('contracts') and pos.get('contracts') != 0:
                print(f"   方向: {pos.get('side')}, 数量: {pos.get('contracts')}, 入场价: {pos.get('entryPrice')}")
        
        if not positions or all(p.get('contracts', 0) == 0 for p in positions):
            print("   无现有仓位")
        
        # 测试1：开多头
        print("\n4. 测试：开多头...")
        try:
            long_order = await exchange.create_order(
                symbol, "market", "buy", trade_amount
            )
            print(f"   下单结果: status={long_order.get('status')}, amount={long_order.get('amount')}")
            await asyncio.sleep(2)
            
            # 查询订单状态
            if long_order.get('id'):
                fetched = await exchange.fetch_order(long_order['id'], symbol)
                print(f"   查询后: status={fetched.get('status')}, filled={fetched.get('filled')}")
        except Exception as e:
            print(f"   ❌ 开多头失败: {e}")
            return
        
        # 查询仓位
        positions = await ccxt_client.fetch_positions([symbol])
        print(f"\n5. 开多头后的仓位:")
        for pos in positions:
            if pos.get('contracts') and pos.get('contracts') != 0:
                print(f"   方向: {pos.get('side')}, 数量: {pos.get('contracts')}")
        
        # 测试2：在有多头的情况下开空头（测试双向持仓）
        print("\n6. 测试：在有多头的情况下开空头...")
        try:
            short_order = await exchange.create_order(
                symbol, "market", "sell", trade_amount
            )
            print(f"   下单结果: status={short_order.get('status')}, amount={short_order.get('amount')}")
            await asyncio.sleep(2)
            
            # 查询订单状态
            if short_order.get('id'):
                fetched = await exchange.fetch_order(short_order['id'], symbol)
                print(f"   查询后: status={fetched.get('status')}, filled={fetched.get('filled')}")
                
                if fetched.get('status') == 'canceled':
                    print("   ❌ 订单被取消！Paradex 可能使用单向持仓模式，不支持同时持有多空仓位")
                elif fetched.get('status') == 'closed' and fetched.get('filled', 0) > 0:
                    print("   ✅ 订单成功！Paradex 支持双向持仓")
        except Exception as e:
            print(f"   ❌ 开空头失败: {e}")
        
        # 查询最终仓位
        positions = await ccxt_client.fetch_positions([symbol])
        print(f"\n7. 开空头后的仓位:")
        for pos in positions:
            if pos.get('contracts') and abs(float(pos.get('contracts', 0))) > 0.0001:
                print(f"   方向: {pos.get('side')}, 数量: {pos.get('contracts')}")
        
        # 清理：平掉所有仓位
        print("\n8. 清理仓位...")
        positions = await ccxt_client.fetch_positions([symbol])
        for pos in positions:
            contracts = float(pos.get('contracts', 0))
            if abs(contracts) > 0.0001:
                side = pos.get('side')
                # 反向操作平仓
                close_side = 'sell' if side == 'long' else 'buy'
                close_amount = abs(contracts)
                close_amount = float(ccxt_client.amount_to_precision(symbol, close_amount))
                
                try:
                    close_order = await exchange.create_order(
                        symbol, "market", close_side, close_amount,
                        params={"reduceOnly": True}
                    )
                    print(f"   平 {side} 仓: {close_amount} ETH")
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"   平仓失败: {e}")
        
        print("\n" + "="*60)
        print("✅ 测试完成！")
        print("="*60)
        print("\n结论：")
        print("- Paradex 不支持 set_position_mode() API")  
        print("- Paradex 使用净持仓模式（多空自动抵消）")
        print("- 可以在有空头仓位的情况下开多头")
        print("- 建议：确保订单数量满足最小成本要求（100 USDC）")
        
    finally:
        await exchange.close()


async def test_paradex_rapid_orders():
    """测试 Paradex 快速连续下单（模拟刷量场景）"""
    print("\n" + "="*60)
    print("=== 测试 Paradex 快速连续下单 ===")
    print("="*60)
    
    config = _load_paradex_config()
    secrets = _load_paradex_secrets()
    _ensure_required_secrets(secrets)

    exchange = CCXTExchange("paradex", config, secrets)
    exchange.network_type = NetworkType.TESTNET

    try:
        await exchange.connect()
        ccxt_client = exchange.ccxt_exchange
        symbol = 'ETH/USD:USDC'
        
        ticker = await ccxt_client.fetch_ticker(symbol)
        price = ticker['last']
        
        # 使用较小的数量测试
        small_amount = 0.035  # 约 $112
        small_amount = float(ccxt_client.amount_to_precision(symbol, small_amount))
        
        print(f"测试数量: {small_amount} ETH (约 ${small_amount * price:.2f})")
        
        # 查询初始仓位
        positions = await ccxt_client.fetch_positions([symbol])
        print(f"\n初始仓位:")
        for pos in positions:
            if abs(float(pos.get('contracts', 0))) > 0.0001:
                print(f"  {pos.get('side')}: {pos.get('contracts')}")
        
        # 测试：快速连续下3个订单（模拟刷量）
        orders = []
        for i in range(3):
            side = 'buy' if i % 2 == 0 else 'sell'
            print(f"\n订单 {i+1}: {side} {small_amount} ETH")
            
            try:
                order = await exchange.create_order(symbol, "market", side, small_amount)
                print(f"  立即返回: status={order.get('status')}, filled={order.get('filled')}")
                
                # 等待2秒后查询
                await asyncio.sleep(2)
                if order.get('id'):
                    fetched = await exchange.fetch_order(order['id'], symbol)
                    print(f"  2秒后查询: status={fetched.get('status')}, filled={fetched.get('filled')}")
                    orders.append(fetched)
                    
            except Exception as e:
                print(f"  ❌ 失败: {e}")
            
            # 短暂等待再下一单（模拟刷量间隔）
            if i < 2:
                await asyncio.sleep(1)
        
        # 查询最终仓位
        positions = await ccxt_client.fetch_positions([symbol])
        print(f"\n最终仓位:")
        for pos in positions:
            if abs(float(pos.get('contracts', 0))) > 0.0001:
                print(f"  {pos.get('side')}: {pos.get('contracts')}")
        
        # 统计
        success_count = sum(1 for o in orders if o.get('status') == 'closed' and o.get('filled', 0) > 0)
        canceled_count = sum(1 for o in orders if o.get('status') == 'canceled')
        
        print(f"\n结果统计:")
        print(f"  成功: {success_count}/3")
        print(f"  取消: {canceled_count}/3")
        
        # 清理
        print("\n清理仓位...")
        positions = await ccxt_client.fetch_positions([symbol])
        for pos in positions:
            contracts = float(pos.get('contracts', 0))
            if abs(contracts) > 0.0001:
                side = pos.get('side')
                close_side = 'sell' if side == 'long' else 'buy'
                close_amount = abs(contracts)
                close_amount = float(ccxt_client.amount_to_precision(symbol, close_amount))
                
                await exchange.create_order(
                    symbol, "market", close_side, close_amount,
                    params={"reduceOnly": True}
                )
                print(f"  已平仓: {side} {close_amount} ETH")
        
    finally:
        await exchange.close()


async def main():
    import sys
    if len(sys.argv) > 1:
        if sys.argv[1] == '--hedge-test':
            await test_paradex_hedge_mode()
        elif sys.argv[1] == '--rapid-test':
            await test_paradex_rapid_orders()
        else:
            await test_paradex()
    else:
        await test_paradex()


if __name__ == "__main__":
    asyncio.run(main())

