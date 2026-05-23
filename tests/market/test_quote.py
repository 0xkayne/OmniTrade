"""Tests for Quote.estimate_fill()."""

import pytest

from src.market.asset import Asset
from src.market.instrument import Instrument
from src.market.quote import EstimatedFill, Quote


BTC = Asset("BTC")
USDT = Asset("USDT")


def make_instrument() -> Instrument:
    return Instrument(
        venue="binance", market_type="spot", base=BTC, quote=USDT,
        venue_symbol="BTCUSDT", taker_fee_rate=0.001, maker_fee_rate=0.0005,
    )


def make_quote(bids=None, asks=None) -> Quote:
    instr = make_instrument()
    bid_price = bids[0][0] if bids else 50000.0
    ask_price = asks[0][0] if asks else 50010.0
    mid = (bid_price + ask_price) / 2.0
    return Quote(
        instrument=instr,
        fetched_at=0.0,
        bid_price=bid_price,
        bid_size=bids[0][1] if bids else 1.0,
        ask_price=ask_price,
        ask_size=asks[0][1] if asks else 1.0,
        mid_price=mid,
        taker_fee_rate=0.001,
        maker_fee_rate=0.0005,
        _bids=list(bids) if bids else [(50000.0, 1.0), (49999.0, 2.0)],
        _asks=list(asks) if asks else [(50010.0, 1.0), (50020.0, 2.0)],
    )


class TestEstimateFillBuy:
    """Buy side: walk asks (ascending price)."""

    def test_buy_fills_from_lowest_ask(self):
        asks = [(50010.0, 0.5), (50020.0, 0.5)]
        q = make_quote(asks=asks)
        fill = q.estimate_fill(amount_base=0.5, side="buy")
        assert fill.filled_fully is True
        # avg_price should exactly be 50010 (only first level consumed)
        assert fill.avg_price == pytest.approx(50010.0)

    def test_buy_crosses_multiple_levels(self):
        asks = [(50010.0, 0.2), (50020.0, 0.2), (50050.0, 1.0)]
        q = make_quote(asks=asks)
        fill = q.estimate_fill(amount_base=0.5, side="buy")
        assert fill.filled_fully is True
        assert fill.depth_consumed_levels == 3
        # weighted avg: (0.2*50010 + 0.2*50020 + 0.1*50050) / 0.5
        expected = (0.2 * 50010 + 0.2 * 50020 + 0.1 * 50050) / 0.5
        assert fill.avg_price == pytest.approx(expected)

    def test_buy_slippage_positive_when_above_mid(self):
        asks = [(50500.0, 1.0)]
        q = make_quote(asks=asks, bids=[(50000.0, 1.0)])
        fill = q.estimate_fill(amount_base=1.0, side="buy")
        assert fill.slippage_pct > 0

    def test_buy_slippage_zero_when_no_price_movement(self):
        asks = [(50000.0, 1.0)]
        bids = [(50000.0, 1.0)]
        q = make_quote(asks=asks, bids=bids)
        fill = q.estimate_fill(amount_base=1.0, side="buy")
        # mid = 50000.0, avg_price = 50000.0, slippage = 0%
        assert fill.slippage_pct == pytest.approx(0.0, abs=1e-9)


class TestEstimateFillSell:
    """Sell side: walk bids (descending price)."""

    def test_sell_fills_from_highest_bid(self):
        bids = [(50000.0, 0.5), (49990.0, 0.5)]
        q = make_quote(bids=bids)
        fill = q.estimate_fill(amount_base=0.5, side="sell")
        assert fill.filled_fully is True
        assert fill.avg_price == pytest.approx(50000.0)

    def test_sell_crosses_multiple_levels(self):
        bids = [(50000.0, 0.2), (49950.0, 0.3), (49900.0, 1.0)]
        q = make_quote(bids=bids)
        fill = q.estimate_fill(amount_base=0.6, side="sell")
        assert fill.filled_fully is True
        assert fill.depth_consumed_levels == 3
        expected = (0.2 * 50000 + 0.3 * 49950 + 0.1 * 49900) / 0.6
        assert fill.avg_price == pytest.approx(expected)


class TestEstimateFillPartial:
    """Partial fill: book too shallow."""

    def test_buy_partial_fill_book_too_shallow(self):
        asks = [(50010.0, 0.5)]
        q = make_quote(asks=asks)
        fill = q.estimate_fill(amount_base=1.0, side="buy")
        assert fill.filled_fully is False
        assert fill.depth_consumed_levels == 1
        assert fill.avg_price == pytest.approx(50010.0)

    def test_sell_partial_fill_book_too_shallow(self):
        bids = [(50000.0, 0.5)]
        q = make_quote(bids=bids)
        fill = q.estimate_fill(amount_base=1.0, side="sell")
        assert fill.filled_fully is False
        assert fill.depth_consumed_levels == 1
        assert fill.avg_price == pytest.approx(50000.0)


class TestEstimateFillEdgeCases:
    """Edge cases for estimate_fill."""

    def test_empty_asks_returns_partial_with_zero_price(self):
        q = make_quote()
        q._asks = []
        fill = q.estimate_fill(amount_base=1.0, side="buy")
        assert fill.filled_fully is False
        assert fill.avg_price == 0.0

    def test_empty_bids_returns_partial_with_zero_price(self):
        q = make_quote()
        q._bids = []
        fill = q.estimate_fill(amount_base=1.0, side="sell")
        assert fill.filled_fully is False
        assert fill.avg_price == 0.0

    def test_amount_zero_returns_zero_fill(self):
        q = make_quote()
        fill = q.estimate_fill(amount_base=0.0, side="buy")
        assert fill.avg_price == 0.0
        assert fill.depth_consumed_levels == 0
        assert fill.filled_fully is True

    def test_filled_fully_field_exists(self):
        """Verify EstimatedFill has the filled_fully field."""
        ef = EstimatedFill(avg_price=50000.0, slippage_pct=0.0, depth_consumed_levels=1)
        assert ef.filled_fully is True
