"""配对波段轮动信号：从近期高点回撤买入，涨过买入价止盈卖出。

每个标的独立维护一个 FLAT(空仓) → LONG(持仓+买入价) 状态机。系统在报
"买入"信号时假设按信号价(当时的收盘)成交并转入 LONG，之后当价格涨过买入价
``sell_rise_pct`` 时报"卖出"并回到 FLAT。配对数 ≈ 波段数，不会刷屏。

为抑制高波动标的（如 meme）在 5 天窗口里反复穿越触发线造成的"短周期噪声
波段"，叠加一个**同方向信号最小间隔**：同一标的相邻两次"买入"，或相邻两次
"卖出"，间隔不足 ``min_signal_interval_seconds`` 时本根静默、等待下一根。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BandRule:
    """波段参数。"""

    buy_drawdown_pct: float = 0.10              # 从近期高点回撤多少 -> 买入
    sell_rise_pct: float = 0.15                 # 涨过买入价多少 -> 卖出
    min_signal_interval_seconds: float = 21600  # 同方向信号最小间隔 (6h)


@dataclass
class BandState:
    """每标的波段状态：是否持仓、假设成交的买入价、最近一次信号时间。"""

    holding: bool = False
    buy_price: float | None = None
    last_signal_ts: float | None = None


def evaluate_band(
    rule: BandRule,
    state: BandState,
    latest: float,
    window_high: float,
    now_ts: float | None,
) -> str | None:
    """评估配对波段信号；返回信号文本（买入/卖出）或 None。

    假设信号价成交：报买入时记买入价 = ``latest`` 并转 LONG；报卖出时转回 FLAT。
    ``window_high`` 为近 N 天（含当前）最高价，``now_ts`` 为当前根的时间戳（秒）。

    去重：为抑制高波动标的反复穿越触发线造成的短周期噪声波段，同一标的**相邻
    任意两个信号**（买→卖、卖→买 都算）间隔不足 ``min_signal_interval_seconds``
    时本根静默、等待下一根 —— 否则"卖后 2 小时又买"这类反向快速交替会漏网。
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
            state.holding = True
            state.buy_price = latest
            state.last_signal_ts = now_ts
            pct = (1 - latest / window_high) * 100
            return (
                f"🔘 买入信号: 现价 {latest:.6g} 自近期高点 {window_high:.6g} "
                f"回撤 {pct:.1f}% (触发线 {buy_line:.6g})"
            )
    else:
        sell_line = state.buy_price * (1 + rule.sell_rise_pct)
        if latest >= sell_line:
            if _too_recent():
                return None
            prev_buy = state.buy_price
            pct = (latest / prev_buy - 1) * 100
            state.holding = False
            state.buy_price = None
            state.last_signal_ts = now_ts
            return (
                f"🟢 卖出信号: 现价 {latest:.6g} 较买入价 {prev_buy:.6g} "
                f"上涨 {pct:.1f}% (触发线 {sell_line:.6g})"
            )

    return None
