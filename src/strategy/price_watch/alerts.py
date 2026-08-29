"""配对波段轮动信号：从近期高点回撤买入，涨过买入价止盈卖出。

每个标的独立维护一个 FLAT(空仓) → LONG(持仓+买入价) 状态机。系统在报
"买入"信号时假设按信号价(当时的收盘)成交并转入 LONG，之后当价格涨过买入价
``sell_rise_pct`` 时报"卖出"并回到 FLAT。配对数 ≈ 波段数，不会刷屏。

为抑制高波动标的（如 meme）在 5 天窗口里反复穿越触发线造成的"短周期噪声
波段"，叠加一个**相邻信号最小间隔**：同一标的相邻任意两个信号（买→卖、
卖→买 都算）间隔不足 ``min_signal_interval_seconds`` 时本根静默、等待下一根。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class BandRule:
    """波段参数。"""

    buy_drawdown_pct: float = 0.10              # 从近期高点回撤多少 -> 买入
    sell_rise_pct: float = 0.15                 # 涨过买入价多少 -> 卖出
    min_signal_interval_seconds: float = 21600  # 相邻信号最小间隔 (6h)


@dataclass
class BandState:
    """每标的波段状态：是否持仓、假设成交的买入价、最近一次信号时间。"""

    holding: bool = False
    buy_price: float | None = None
    last_signal_ts: float | None = None


@dataclass
class BandSignal:
    """一次波段信号（方向 + 触发时的关键价格），由展示层格式化为通知。"""

    direction: Literal["buy", "sell"]
    price: float              # 信号时的现价
    trigger: float            # 触发线（buy=高点×折扣, sell=买入价×涨幅）
    buy_price: float | None = None      # sell 信号：对应的买入价
    window_high: float | None = None    # buy 信号：窗口高点


def evaluate_band(
    rule: BandRule,
    state: BandState,
    latest: float,
    window_high: float,
    now_ts: float | None,
    buy_allowed: bool = True,
) -> BandSignal | None:
    """评估配对波段信号；返回 :class:`BandSignal` 或 None。

    假设信号价成交：报买入时记买入价 = ``latest`` 并转 LONG；报卖出时转回 FLAT。
    ``window_high`` 为近 N 天（含当前）最高价，``now_ts`` 为当前根的时间戳（秒）。
    ``buy_allowed`` 为可选的多周期买入开关：False 时禁止买入（不改变状态，等条件
    满足后再试），默认 True 不拦截。
    """
    if latest is None or window_high is None:
        return None

    def _too_recent() -> bool:
        return (
            state.last_signal_ts is not None
            and now_ts is not None
            and (now_ts - state.last_signal_ts) < rule.min_signal_interval_seconds
        )

    if not state.holding:
        buy_line = window_high * (1 - rule.buy_drawdown_pct)
        if latest <= buy_line:
            if _too_recent():
                return None
            if not buy_allowed:  # multi-timeframe gate -> do not enter, leave state flat
                return None
            state.holding = True
            state.buy_price = latest
            state.last_signal_ts = now_ts
            return BandSignal(direction="buy", price=latest, trigger=buy_line, window_high=window_high)
    else:
        sell_line = state.buy_price * (1 + rule.sell_rise_pct)
        if latest >= sell_line:
            if _too_recent():
                return None
            prev_buy = state.buy_price
            state.holding = False
            state.buy_price = None
            state.last_signal_ts = now_ts
            return BandSignal(direction="sell", price=latest, trigger=sell_line, buy_price=prev_buy)

    return None
