"""Tests for Intent validation (__post_init__ checks)."""

import pytest

from src.coordinator.intent import Intent


class TestIntentValidation:
    def test_valid_intent_accepts(self):
        intent = Intent(
            intent_id="i1",
            base="BTC",
            quote_preference=["USDT", "USDC"],
            product="spot",
            side="buy",
            order_type="market",
            total_notional_usd=1000.0,
            split={"binance": 0.5, "hyperliquid": 0.5},
        )
        assert intent.intent_id == "i1"
        assert intent.base == "BTC"
        assert intent.product == "spot"

    def test_split_must_sum_to_1(self):
        with pytest.raises(ValueError, match="Split ratios must sum to 1.0"):
            Intent(
                intent_id="i1",
                base="BTC",
                quote_preference=["USDT"],
                product="spot",
                side="buy",
                order_type="market",
                total_notional_usd=1000.0,
                split={"binance": 0.5, "hyperliquid": 0.3},
            )

    def test_split_near_1_accepts_floating_point(self):
        # 0.333 + 0.333 + 0.334 == 1.0 (close enough)
        intent = Intent(
            intent_id="i1",
            base="BTC",
            quote_preference=["USDT"],
            product="spot",
            side="buy",
            order_type="market",
            total_notional_usd=1000.0,
            split={"a": 0.333, "b": 0.333, "c": 0.334},
        )
        # should not raise
        assert intent is not None

    def test_product_must_be_spot_or_perp(self):
        with pytest.raises(ValueError, match="product must be"):
            Intent(
                intent_id="i1",
                base="BTC",
                quote_preference=["USDT"],
                product="option",
                side="buy",
                order_type="market",
                total_notional_usd=1000.0,
                split={"binance": 1.0},
            )

    def test_limit_order_requires_limit_price(self):
        with pytest.raises(ValueError, match="limit_price is required"):
            Intent(
                intent_id="i1",
                base="BTC",
                quote_preference=["USDT"],
                product="spot",
                side="buy",
                order_type="limit",
                total_notional_usd=1000.0,
                split={"binance": 1.0},
            )

    def test_limit_order_with_price_ok(self):
        intent = Intent(
            intent_id="i1",
            base="BTC",
            quote_preference=["USDT"],
            product="spot",
            side="buy",
            order_type="limit",
            total_notional_usd=1000.0,
            split={"binance": 1.0},
            limit_price=50000.0,
        )
        assert intent.limit_price == 50000.0

    def test_spot_leverage_must_be_1(self):
        with pytest.raises(ValueError, match="leverage must be 1 for spot"):
            Intent(
                intent_id="i1",
                base="BTC",
                quote_preference=["USDT"],
                product="spot",
                side="buy",
                order_type="market",
                total_notional_usd=1000.0,
                split={"binance": 1.0},
                leverage=5,
            )

    def test_perp_leverage_can_be_above_1(self):
        intent = Intent(
            intent_id="i1",
            base="BTC",
            quote_preference=["USDT"],
            product="perp",
            side="buy",
            order_type="market",
            total_notional_usd=1000.0,
            split={"binance": 1.0},
            leverage=3,
        )
        assert intent.leverage == 3

    def test_default_values(self):
        intent = Intent(
            intent_id="i1",
            base="ETH",
            quote_preference=["USDT"],
            product="spot",
            side="sell",
            order_type="market",
            total_notional_usd=500.0,
            split={"binance": 1.0},
        )
        assert intent.leverage == 1
        assert intent.limit_price is None
        assert intent.max_slippage_pct is None
        assert intent.max_fee_usd is None
        assert intent.execute_timeout_seconds == 30

    def test_time_in_force_must_be_valid(self):
        with pytest.raises(ValueError, match="time_in_force must be one of"):
            Intent(
                intent_id="i1",
                base="BTC",
                quote_preference=["USDT"],
                product="spot",
                side="buy",
                order_type="market",
                total_notional_usd=1000.0,
                split={"binance": 1.0},
                time_in_force="BAD",
            )

    def test_time_in_force_is_normalized(self):
        intent = Intent(
            intent_id="i1",
            base="BTC",
            quote_preference=["USDT"],
            product="spot",
            side="buy",
            order_type="market",
            total_notional_usd=1000.0,
            split={"binance": 1.0},
            time_in_force="ioc",
        )
        assert intent.time_in_force == "IOC"
