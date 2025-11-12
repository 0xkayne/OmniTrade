import asyncio
import yaml
from src.core.base_exchange import NetworkType
from src.exchanges.ccxt_exchange import CCXTExchange

def _load_hyperliquid_config():
    with open("config/exchanges.yaml", "r") as ef:
        exchanges_yaml = yaml.safe_load(ef)
    return exchanges_yaml["exchanges"]["hyperliquid"]


def _load_hyperliquid_secrets():
    with open("config/secrets.yaml", "r") as f:
        all_secrets = yaml.safe_load(f)
    return all_secrets.get("hyperliquid", {})


async def test_hyperliquid():
    print("\n=== 测试 Hyperliquid ===")
    config = _load_hyperliquid_config()
    secrets = _load_hyperliquid_secrets()
    if not secrets.get("walletAddress"):
        raise RuntimeError("config/secrets.yaml 缺少 Hyperliquid 的 walletAddress")
    if not secrets.get("privateKey"):
        raise RuntimeError("config/secrets.yaml 缺少 Hyperliquid 的 privateKey")

    exchange = CCXTExchange("hyperliquid", config, secrets)
    exchange.network_type = NetworkType.TESTNET

    try:
        await exchange.connect()
        ccxt_client = exchange.ccxt_exchange

        markets = ccxt_client.markets
        print(f"已加载 {len(markets)} 个交易对")

        swap_markets = await ccxt_client.fetch_swap_markets()
        print(f"Swap 市场数量: {len(swap_markets)}")
        swap_symbol = swap_markets[0]["symbol"] if swap_markets else None
        swap_market = ccxt_client.market(swap_symbol) if swap_symbol else None

        spot_markets = await ccxt_client.fetch_spot_markets()
        print(f"Spot 市场数量: {len(spot_markets)}")
        spot_symbol = spot_markets[0]["symbol"] if spot_markets else None

        balance = await exchange.fetch_balance()
        print("账户余额:", balance)

        if swap_symbol:
            ticker = await ccxt_client.fetch_ticker(swap_symbol)
            print(f"{swap_symbol} 最新行情: {ticker.get('close')}")

            orderbook = await exchange.fetch_orderbook(swap_symbol, limit=5)
            top_bid = orderbook["bids"][0] if orderbook["bids"] else None
            top_ask = orderbook["asks"][0] if orderbook["asks"] else None
            print(f"{swap_symbol} 最优买: {top_bid}, 最优卖: {top_ask}")

            trades = await ccxt_client.fetch_trades(swap_symbol, limit=5)
            print(f"{swap_symbol} 最近成交数量: {len(trades)}")

            ohlcv = await ccxt_client.fetch_ohlcv(swap_symbol, timeframe="1m", limit=5)
            print(f"{swap_symbol} 1m K线条数: {len(ohlcv)}")

            funding_rates = await ccxt_client.fetch_funding_rates([swap_symbol])
            swap_funding = funding_rates.get(swap_symbol, {})
            print(f"{swap_symbol} 当前资金费率: {swap_funding.get('fundingRate')}")

            # === 交易流程测试 ===
            min_amount = None
            min_notional = None
            if swap_market:
                limits = swap_market.get("limits", {})
                amount_limits = limits.get("amount", {}) if limits else {}
                min_amount = amount_limits.get("min") or min_amount
                cost_limits = limits.get("cost", {}) if limits else {}
                min_notional = cost_limits.get("min") or min_notional
            execution_ask = float(top_ask[0]) if top_ask else None
            if execution_ask is None and ticker.get("close"):
                execution_ask = float(ticker["close"])
            if swap_market:
                cost_limits = swap_market.get("limits", {}).get("cost")
                if cost_limits and cost_limits.get("min"):
                    min_notional = float(cost_limits["min"])
            if execution_ask is None:
                raise RuntimeError(f"无法获取 {swap_symbol} 的市价参考价格")

            trade_amount = min_amount or 0.01
            trade_amount = max(float(trade_amount), 0.01)
            if min_notional:
                required_amount = (float(min_notional) / execution_ask) * 1.05
                trade_amount = max(trade_amount, required_amount)
            trade_amount = float(ccxt_client.amount_to_precision(swap_symbol, trade_amount))

            print(f"使用 {swap_symbol} 交易数量: {trade_amount}")

            # 市价单开仓
            market_order = await exchange.create_order(
                swap_symbol,
                "market",
                "buy",
                trade_amount,
                price=execution_ask,
            )
            print(f"市价单下单结果: {market_order}")
            await asyncio.sleep(2)

            positions = await ccxt_client.fetch_positions([swap_symbol])
            print(f"{swap_symbol} 仓位信息: {positions}")

            # 限价单挂单
            reference_price = None
            if top_bid:
                reference_price = float(top_bid[0]) * 0.95
            elif ticker.get("close"):
                reference_price = float(ticker["close"]) * 0.95
            limit_price = float(ccxt_client.price_to_precision(swap_symbol, reference_price)) if reference_price else None
            limit_order_id = None
            if limit_price:
                limit_order = await exchange.create_order(
                    swap_symbol,
                    "limit",
                    "buy",
                    trade_amount,
                    price=limit_price,
                )
                limit_order_id = limit_order.get("id")
                print(f"限价单下单结果: {limit_order}")
                await asyncio.sleep(2)

                open_orders = await ccxt_client.fetch_open_orders(swap_symbol)
                print(f"{swap_symbol} 当前挂单数量: {len(open_orders)}")

            # 市价单平仓（reduceOnly）
            close_amount = trade_amount
            if positions:
                for pos in positions:
                    if pos.get("symbol") == swap_symbol:
                        contracts = pos.get("contracts") or pos.get("size")
                        if contracts:
                            close_amount = float(ccxt_client.amount_to_precision(swap_symbol, float(contracts)))
                            break
            refreshed_orderbook = await exchange.fetch_orderbook(swap_symbol, limit=5)
            refreshed_top_bid = refreshed_orderbook["bids"][0] if refreshed_orderbook["bids"] else top_bid
            close_price = float(refreshed_top_bid[0]) if refreshed_top_bid else execution_ask

            if close_price is None:
                raise RuntimeError(f"无法获取 {swap_symbol} 平仓参考价格")

            close_order = await exchange.create_order(
                swap_symbol,
                "market",
                "sell",
                close_amount,
                price=close_price,
                params={"reduceOnly": True},
            )
            print(f"平仓市价单结果: {close_order}")
            await asyncio.sleep(2)

            if limit_order_id:
                try:
                    cancelled = await ccxt_client.cancel_order(limit_order_id, swap_symbol)
                    print(f"取消限价单结果: {cancelled}")
                    await asyncio.sleep(1)
                    remaining_orders = await ccxt_client.fetch_open_orders(swap_symbol)
                    print(f"{swap_symbol} 取消后挂单数量: {len(remaining_orders)}")
                except Exception as cancel_error:
                    print(f"取消限价单失败: {cancel_error}")

        if spot_symbol:
            spot_ticker = await ccxt_client.fetch_ticker(spot_symbol)
            print(f"{spot_symbol} 最新行情: {spot_ticker.get('close')}")

            spot_orderbook = await ccxt_client.fetch_order_book(spot_symbol, limit=5)
            spot_top_bid = spot_orderbook["bids"][0] if spot_orderbook["bids"] else None
            spot_top_ask = spot_orderbook["asks"][0] if spot_orderbook["asks"] else None
            print(f"{spot_symbol} 最优买: {spot_top_bid}, 最优卖: {spot_top_ask}")

    finally:
        await exchange.close()
    
async def main():
    await test_hyperliquid()
    
if __name__ == "__main__":
    asyncio.run(main())