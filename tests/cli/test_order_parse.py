"""Tests for CLI argument parsing — split, quote_preference, Intent construction."""

import pytest
import typer

from src.cli.main import parse_quote_preference, parse_split


class TestParseSplit:
    def test_basic_two_venue(self):
        result = parse_split("binance=0.5,hyperliquid=0.5")
        assert result == {"binance": 0.5, "hyperliquid": 0.5}

    def test_single_venue(self):
        result = parse_split("binance=1.0")
        assert result == {"binance": 1.0}

    def test_three_venue(self):
        result = parse_split("binance=0.33,hyperliquid=0.34,okx=0.33")
        assert result == {"binance": 0.33, "hyperliquid": 0.34, "okx": 0.33}

    def test_integer_ratio(self):
        result = parse_split("binance=1")
        assert result == {"binance": 1.0}

    def test_decimal_ratio(self):
        result = parse_split("binance=0.333")
        assert result == {"binance": 0.333}

    def test_strips_whitespace(self):
        result = parse_split(" binance = 0.5 , hyperliquid = 0.5 ")
        assert result == {"binance": 0.5, "hyperliquid": 0.5}

    def test_invalid_format_missing_equals(self):
        with pytest.raises(typer.BadParameter, match="Invalid split format"):
            parse_split("binance0.5")

    def test_invalid_ratio_not_a_number(self):
        with pytest.raises(typer.BadParameter, match="Invalid ratio value"):
            parse_split("binance=abc")

    def test_negative_ratio_rejected(self):
        with pytest.raises(typer.BadParameter, match="Ratio must be positive"):
            parse_split("binance=-0.5")

    def test_zero_ratio_rejected(self):
        with pytest.raises(typer.BadParameter, match="Ratio must be positive"):
            parse_split("binance=0")

    def test_empty_string(self):
        result = parse_split("")
        assert result == {}

    def test_trailing_comma_handled(self):
        result = parse_split("binance=0.5,")
        assert result == {"binance": 0.5}


class TestParseQuotePreference:
    def test_basic(self):
        result = parse_quote_preference("USDT,USDC")
        assert result == ["USDT", "USDC"]

    def test_single(self):
        result = parse_quote_preference("USDT")
        assert result == ["USDT"]

    def test_strips_whitespace(self):
        result = parse_quote_preference(" USDT , USDC ")
        assert result == ["USDT", "USDC"]

    def test_empty_string(self):
        result = parse_quote_preference("")
        assert result == []

    def test_trailing_comma(self):
        result = parse_quote_preference("USDT,")
        assert result == ["USDT"]
