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
            market_order = await exchange.create_order(
                symbol,
                "market",
                "buy",
                trade_amount,
            )
            print(f"市价单下单结果: {market_order}")
            await asyncio.sleep(2)

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


async def main():
    await test_paradex()


if __name__ == "__main__":
    asyncio.run(main())

