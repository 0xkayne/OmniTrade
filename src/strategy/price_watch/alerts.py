"""配对波段轮动信号：从近期高点回撤买入，涨过买入价止盈卖出。

每个标的独立维护一个 FLAT(空仓) → LONG(持仓+买入价) 状态机。系统在报
"买入"信号时假设按信号价(当时的收盘)成交并转入 LONG，之后当价格涨过买入价
``sell_rise_pct`` 时报"卖出"并回到 FLAT。配对数 ≈ 波段数，不会刷屏。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BandRule:
    """波段参数。"""

    buy_drawdown_pct: float = 0.10  # 从近期高点回撤多少 -> 买入
    sell_rise_pct: float = 0.15     # 涨过买入价多少 -> 卖出


@dataclass
class BandState:
    """每标的波段状态：是否持仓、假设成交的买入价。"""

    holding: bool = False
    buy_price: float | None = None


def evaluate_band(
    rule: BandRule,
    state: BandState,
    latest: float,
    window_high: float,
) -> str | None:
    """评估配对波段信号；返回信号文本（买入/卖出）或 None。

    假设信号价成交：报买入时记买入价 = ``latest`` 并转 LONG；报卖出时转回 FLAT。
    ``window_high`` 为近 N 天（含当前）最高价。
    """
    if latest is None or window_high is None:
        return None

    if not state.holding:
        buy_line = window_high * (1 - rule.buy_drawdown_pct)
        if latest <= buy_line:
            state.holding = True
            state.buy_price = latest
            pct = (1 - latest / window_high) * 100
            return (
                f"🔘 买入信号: 现价 {latest:.6g} 自近期高点 {window_high:.6g} "
                f"回撤 {pct:.1f}% (触发线 {buy_line:.6g})"
            )
    else:
        sell_line = state.buy_price * (1 + rule.sell_rise_pct)
        if latest >= sell_line:
            prev_buy = state.buy_price
            pct = (latest / prev_buy - 1) * 100
            state.holding = False
            state.buy_price = None
            return (
                f"🟢 卖出信号: 现价 {latest:.6g} 较买入价 {prev_buy:.6g} "
                f"上涨 {pct:.1f}% (触发线 {sell_line:.6g})"
            )

    return None
