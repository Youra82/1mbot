# tests/test_portfolio.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

import pytest

from onembot.risk.portfolio import PortfolioRiskManager


def test_total_exposure_sums_absolute_values_across_symbols():
    mgr = PortfolioRiskManager(max_portfolio_inventory_usdt=90.0)
    mgr.update("BTC/USDT:USDT", 30.0)
    mgr.update("ETH/USDT:USDT", -20.0)

    assert mgr.total_exposure_usdt() == pytest.approx(50.0)
    assert mgr.is_over_cap() is False


def test_is_over_cap_true_when_summed_exposure_exceeds_max():
    mgr = PortfolioRiskManager(max_portfolio_inventory_usdt=50.0)
    mgr.update("BTC/USDT:USDT", 30.0)
    mgr.update("ETH/USDT:USDT", -25.0)

    assert mgr.total_exposure_usdt() == pytest.approx(55.0)
    assert mgr.is_over_cap() is True


def test_update_overwrites_previous_value_for_same_symbol():
    mgr = PortfolioRiskManager(max_portfolio_inventory_usdt=90.0)
    mgr.update("BTC/USDT:USDT", 30.0)
    mgr.update("BTC/USDT:USDT", 10.0)

    assert mgr.total_exposure_usdt() == pytest.approx(10.0)


def test_rejects_non_positive_cap():
    with pytest.raises(ValueError):
        PortfolioRiskManager(max_portfolio_inventory_usdt=0.0)
