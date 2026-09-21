import time

import pytest

from src.arbitrage.normalization import common_base_quantity, normalize_symbol, pair_from_instruments
from src.arbitrage.profitability import estimate_vwap, evaluate_spread
from src.arbitrage.risk import ArbitrageRiskConfig, ArbitrageRiskValidator
from src.market.asset import Asset
from src.market.instrument import Instrument, NetworkType
from src.market.quote import Quote


def _instrument(venue: str, symbol: str = "BTC-USD", contract_size: float = 1.0) -> Instrument:
    return Instrument(
        venue=venue,
        network=NetworkType.TESTNET,
        market_type="perp",
        base=Asset("BTC"),
        quote=Asset("USD"),
        venue_symbol=symbol,
        contract_size=contract_size,
        taker_fee_rate=0.0005,
    )


def _quote(instrument: Instrument, bid: float, ask: float, *, fee: float | None = None) -> Quote:
    now = time.time()
    return Quote(
        instrument=instrument,
        fetched_at=now,
        exchange_at=now,
        bid_price=bid,
        bid_size=2.0,
        ask_price=ask,
        ask_size=2.0,
        mid_price=(bid + ask) / 2,
        taker_fee_rate=instrument.taker_fee_rate if fee is None else fee,
        maker_fee_rate=instrument.maker_fee_rate,
        _bids=[(bid, 1.0), (bid - 1, 1.0)],
        _asks=[(ask, 1.0), (ask + 1, 1.0)],
    )


def test_estimate_vwap_walks_multiple_levels() -> None:
    quote = _quote(_instrument("a"), 100, 101)
    vwap, notional, full = estimate_vwap(quote, 1.5, "buy")
    assert vwap == pytest.approx((101 + 101 + 102) / 3)
    assert notional == pytest.approx(152.0)
    assert full


def test_evaluate_spread_selects_profitable_direction_and_costs() -> None:
    pair = pair_from_instruments(_instrument("a"), _instrument("b"))
    opportunity = evaluate_spread(pair, _quote(pair.instrument_a, 100, 101), _quote(pair.instrument_b, 104, 105), 1)
    assert opportunity.direction == "buy_a_sell_b"
    assert opportunity.gross_edge_usd == pytest.approx(3)
    assert opportunity.net_edge_usd < opportunity.gross_edge_usd
    assert opportunity.is_profitable


def test_fee_and_slippage_can_remove_edge() -> None:
    pair = pair_from_instruments(_instrument("a"), _instrument("b"))
    low_edge = evaluate_spread(
        pair,
        _quote(pair.instrument_a, 100, 100.1, fee=0.01),
        _quote(pair.instrument_b, 100.2, 100.3, fee=0.01),
        1,
    )
    assert low_edge.net_edge_usd < 0
    assert not low_edge.is_profitable


def test_normalization_and_contract_ratio() -> None:
    assert normalize_symbol("BTC/USDT:USDT") == ("BTC", "USDT")
    assert normalize_symbol("ETHUSDC") == ("ETH", "USDC")
    pair = pair_from_instruments(_instrument("a", contract_size=10), _instrument("b", contract_size=5))
    assert common_base_quantity(pair, 2) == pytest.approx(4)


def test_risk_validator_rejects_disabled_stale_and_small_edge() -> None:
    pair = pair_from_instruments(_instrument("a"), _instrument("b"))
    opportunity = evaluate_spread(pair, _quote(pair.instrument_a, 100, 101), _quote(pair.instrument_b, 100, 101), 1)
    decision = ArbitrageRiskValidator(ArbitrageRiskConfig()).check(opportunity)
    assert not decision.allowed
    assert "arbitrage is disabled" in decision.failures


def test_risk_validator_allows_eligible_opportunity() -> None:
    pair = pair_from_instruments(_instrument("a"), _instrument("b"))
    opportunity = evaluate_spread(pair, _quote(pair.instrument_a, 100, 101), _quote(pair.instrument_b, 104, 105), 1)
    config = ArbitrageRiskConfig(enabled=True, min_net_edge_bps=1, max_quote_age_ms=1_000)
    decision = ArbitrageRiskValidator(config).check(opportunity, now=opportunity.created_at)
    assert decision.allowed, decision.failures
