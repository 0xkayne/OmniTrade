"""Tests for account_type helpers."""

from src.coordinator.account_type import account_type_params, ccxt_account_type, compensation_order_params


class TestCcxtAccountType:
    def test_spot(self):
        assert ccxt_account_type("spot") == "spot"

    def test_perp(self):
        assert ccxt_account_type("perp") == "swap"


class TestAccountTypeParams:
    def test_spot(self):
        assert account_type_params("spot") == {"type": "spot"}

    def test_perp(self):
        assert account_type_params("perp") == {"type": "swap"}


class TestCompensationOrderParams:
    def test_spot_has_no_reduce_only(self):
        params = compensation_order_params("spot")
        assert params["type"] == "spot"
        assert "reduceOnly" not in params

    def test_perp_includes_reduce_only(self):
        params = compensation_order_params("perp")
        assert params["type"] == "swap"
        assert params["reduceOnly"] is True

    def test_perp_params_are_independent(self):
        """Verify mutation safety — each call returns a fresh dict."""
        p1 = compensation_order_params("perp")
        p2 = compensation_order_params("perp")
        p1["extra"] = "test"
        assert "extra" not in p2
