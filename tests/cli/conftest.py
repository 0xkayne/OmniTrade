"""Shared fixtures for CLI command tests."""

import pytest


@pytest.fixture
def sample_order_result_all_filled():
    return {
        "status": "ALL_FILLED",
        "intent_id": "intent-001",
        "legs": [
            {
                "leg_id": "leg-1",
                "venue": "binance",
                "status": "FILLED",
                "order_id": "mock-binance-1",
                "filled_amount": 0.00744,
                "avg_price": 67234.50,
                "fee": 1.34,
            },
            {
                "leg_id": "leg-2",
                "venue": "hyperliquid",
                "status": "FILLED",
                "order_id": "mock-hl-2",
                "filled_amount": 0.00744,
                "avg_price": 67241.20,
                "fee": 2.02,
            },
        ],
        "execution_time_s": 0.873,
    }


@pytest.fixture
def sample_order_result_rejected():
    return {
        "status": "REJECTED",
        "intent_id": "intent-002",
        "reason": "Plan not acceptable: slippage too high",
        "rejected_venues": [],
        "legs": [],
    }


@pytest.fixture
def sample_order_result_rolled_back():
    return {
        "status": "ROLLED_BACK",
        "intent_id": "intent-003",
        "legs": [
            {
                "leg_id": "leg-3",
                "venue": "binance",
                "status": "FILLED",
                "order_id": "mock-1",
                "filled_amount": 0.01,
                "avg_price": 50000.0,
                "fee": 1.0,
            },
            {
                "leg_id": "leg-4",
                "venue": "hyperliquid",
                "status": "REJECTED",
                "order_id": None,
                "filled_amount": 0.0,
                "avg_price": None,
                "fee": 0.0,
            },
        ],
        "reconciliation": {
            "status": "ROLLED_BACK",
            "legs": [
                {"leg_id": "leg-3", "compensation_status": "COMPENSATED"},
            ],
            "residual_exposure_usd": 0.0,
        },
    }


@pytest.fixture
def sample_order_result_needs_manual():
    return {
        "status": "ROLLED_BACK_FAILED",
        "intent_id": "intent-004",
        "legs": [
            {
                "leg_id": "leg-5",
                "venue": "binance",
                "status": "FILLED",
                "order_id": "mock-1",
                "filled_amount": 0.01,
                "avg_price": 50000.0,
                "fee": 1.0,
            },
        ],
        "reconciliation": {
            "status": "ROLLED_BACK_FAILED",
            "legs": [
                {"leg_id": "leg-5", "compensation_status": "COMPENSATION_FAILED"},
            ],
            "residual_exposure_usd": 500.0,
        },
    }


@pytest.fixture
def sample_order_result_dry_run():
    return {
        "status": "DRY_RUN",
        "intent_id": "intent-005",
        "plan": {
            "legs": [
                {
                    "venue": "binance",
                    "instrument": "BTC/USDT",
                    "quote_matched": "USDT",
                    "planned_notional_usd": 500.0,
                    "planned_qty_base": 0.00744,
                    "estimated_fill_price": 67234.50,
                    "estimated_slippage_pct": 0.08,
                    "estimated_fee_usd": 1.34,
                },
                {
                    "venue": "hyperliquid",
                    "instrument": "BTC/USDC:USDC",
                    "quote_matched": "USDC",
                    "planned_notional_usd": 500.0,
                    "planned_qty_base": 0.00744,
                    "estimated_fill_price": 67241.20,
                    "estimated_slippage_pct": 0.10,
                    "estimated_fee_usd": 2.02,
                },
            ],
            "rejected_venues": [],
            "aggregate": {
                "estimated_avg_price": 67237.85,
                "estimated_total_fee_usd": 3.36,
            },
        },
    }
