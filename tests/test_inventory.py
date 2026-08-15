# tests/test_inventory.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

import pytest

from onembot.risk.inventory import InventoryState, blocked_side, compute_skew, is_over_cap


def test_compute_skew_scales_within_cap():
    state = InventoryState(symbol="BTC/USDT:USDT", net_usdt=15.0)
    skew = compute_skew(state, max_net_inventory_usdt=30.0, skew_max=0.6)
    assert skew == pytest.approx(0.3)  # (15/30) * 0.6


def test_compute_skew_clamped_beyond_cap():
    state = InventoryState(symbol="BTC/USDT:USDT", net_usdt=90.0)
    skew = compute_skew(state, max_net_inventory_usdt=30.0, skew_max=0.6)
    assert skew == pytest.approx(0.6)


def test_compute_skew_negative_for_short_inventory():
    state = InventoryState(symbol="BTC/USDT:USDT", net_usdt=-15.0)
    skew = compute_skew(state, max_net_inventory_usdt=30.0, skew_max=0.6)
    assert skew == pytest.approx(-0.3)


def test_compute_skew_rejects_non_positive_cap():
    state = InventoryState(symbol="BTC/USDT:USDT", net_usdt=1.0)
    with pytest.raises(ValueError):
        compute_skew(state, max_net_inventory_usdt=0.0, skew_max=0.6)


def test_is_over_cap():
    assert is_over_cap(InventoryState("X", 31.0), max_net_inventory_usdt=30.0) is True
    assert is_over_cap(InventoryState("X", 30.0), max_net_inventory_usdt=30.0) is False
    assert is_over_cap(InventoryState("X", -31.0), max_net_inventory_usdt=30.0) is True


def test_blocked_side_long_blocks_buy():
    state = InventoryState("X", 35.0)
    assert blocked_side(state, max_net_inventory_usdt=30.0) == "buy"


def test_blocked_side_short_blocks_sell():
    state = InventoryState("X", -35.0)
    assert blocked_side(state, max_net_inventory_usdt=30.0) == "sell"


def test_blocked_side_none_within_cap():
    state = InventoryState("X", 10.0)
    assert blocked_side(state, max_net_inventory_usdt=30.0) is None
