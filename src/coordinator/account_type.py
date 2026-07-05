"""Helpers for selecting the exchange account used by a planned leg."""


def ccxt_account_type(market_type: str) -> str:
    """Map OmniTrade market_type to the ccxt account type parameter."""
    return "swap" if market_type == "perp" else market_type


def account_type_params(market_type: str) -> dict[str, str]:
    return {"type": ccxt_account_type(market_type)}


def compensation_order_params(market_type: str) -> dict[str, str | bool]:
    """Params for compensation (reverse) orders.

    For perp, includes reduceOnly=True so the reverse order closes the
    existing position rather than opening a new opposite-side position.
    """
    params: dict[str, str | bool] = {"type": ccxt_account_type(market_type)}
    if market_type == "perp":
        params["reduceOnly"] = True
    return params


def extract_fee_usd(order: dict) -> float:
    """Extract fee in USD from a ccxt order response dict."""
    fee = order.get("fee")
    if isinstance(fee, dict):
        return fee.get("cost", 0.0) or 0.0
    return 0.0
