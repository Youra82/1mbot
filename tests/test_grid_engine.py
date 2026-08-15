# tests/test_grid_engine.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

import pytest

from onembot.strategy import grid_engine
from onembot.strategy.grid_engine import GridLevel, Side


def test_compute_spacing():
    assert grid_engine.compute_spacing(atr_value=10.0, spacing_atr_mult=0.5) == 5.0


def test_compute_spacing_rejects_non_positive_atr():
    with pytest.raises(ValueError):
        grid_engine.compute_spacing(atr_value=0.0, spacing_atr_mult=0.5)


def test_build_levels_symmetric_around_anchor():
    levels = grid_engine.build_levels(anchor_price=100.0, spacing=1.0, levels_per_side=3, level_size_usdt=6.0)

    buys = sorted([l for l in levels if l.side == Side.BUY], key=lambda l: l.index)
    sells = sorted([l for l in levels if l.side == Side.SELL], key=lambda l: l.index)

    assert len(buys) == 3 and len(sells) == 3
    assert [round(b.price, 4) for b in buys] == [99.0, 98.0, 97.0]
    assert [round(s.price, 4) for s in sells] == [101.0, 102.0, 103.0]
    assert all(l.size_usdt == 6.0 for l in levels)


def test_build_levels_rejects_bad_input():
    with pytest.raises(ValueError):
        grid_engine.build_levels(anchor_price=0, spacing=1.0, levels_per_side=1, level_size_usdt=6.0)
    with pytest.raises(ValueError):
        grid_engine.build_levels(anchor_price=100, spacing=1.0, levels_per_side=0, level_size_usdt=6.0)


def test_replenish_after_fill_buy_places_sell_one_spacing_higher():
    filled = GridLevel(side=Side.BUY, price=99.0, size_usdt=6.0, index=1)
    replenished = grid_engine.replenish_after_fill(filled, spacing=1.0)

    assert replenished.side == Side.SELL
    assert replenished.price == 100.0
    assert replenished.size_usdt == 6.0


def test_replenish_after_fill_sell_places_buy_one_spacing_lower():
    filled = GridLevel(side=Side.SELL, price=101.0, size_usdt=6.0, index=2)
    replenished = grid_engine.replenish_after_fill(filled, spacing=1.0)

    assert replenished.side == Side.BUY
    assert replenished.price == 100.0


def test_build_levels_from_zones_uses_zone_prices_when_available():
    levels = grid_engine.build_levels_from_zones(
        anchor_price=100.0, zone_prices_below=[97.5], zone_prices_above=[103.2],
        levels_per_side=1, level_size_usdt=6.0, fallback_spacing=1.0,
    )
    buy = next(l for l in levels if l.side == Side.BUY)
    sell = next(l for l in levels if l.side == Side.SELL)
    assert buy.price == 97.5   # Zone statt Anker-1.0
    assert sell.price == 103.2


def test_build_levels_from_zones_falls_back_to_spacing_without_zones():
    levels = grid_engine.build_levels_from_zones(
        anchor_price=100.0, zone_prices_below=[], zone_prices_above=[],
        levels_per_side=2, level_size_usdt=6.0, fallback_spacing=1.0,
    )
    buys = sorted([l.price for l in levels if l.side == Side.BUY])
    sells = sorted([l.price for l in levels if l.side == Side.SELL])
    assert buys == [98.0, 99.0]
    assert sells == [101.0, 102.0]


def test_build_levels_from_zones_fills_missing_slots_with_fallback():
    # Nur eine Zone pro Seite bekannt, levels_per_side=2 -> zweite Stufe faellt auf Spacing zurueck.
    levels = grid_engine.build_levels_from_zones(
        anchor_price=100.0, zone_prices_below=[98.5], zone_prices_above=[101.7],
        levels_per_side=2, level_size_usdt=6.0, fallback_spacing=1.0,
    )
    buys = sorted([l.price for l in levels if l.side == Side.BUY])
    sells = sorted([l.price for l in levels if l.side == Side.SELL])
    assert buys == [98.0, 98.5]      # Stufe 1 = Zone, Stufe 2 = Anker - 2*spacing (Fallback)
    assert sells == [101.7, 102.0]   # Stufe 1 = Zone, Stufe 2 = Anker + 2*spacing (Fallback)


def test_build_levels_from_zones_ignores_zone_closer_than_atr_spacing():
    # Zonen bei 99.5/100.5 liegen NAEHER am Anker als die ATR-Stufe (99.0/101.0)
    # -> duerfen die kalibrierte Mindest-Spacing nicht unterlaufen, ATR-Fallback gewinnt.
    levels = grid_engine.build_levels_from_zones(
        anchor_price=100.0, zone_prices_below=[99.5], zone_prices_above=[100.5],
        levels_per_side=1, level_size_usdt=6.0, fallback_spacing=1.0,
    )
    buy = next(l for l in levels if l.side == Side.BUY)
    sell = next(l for l in levels if l.side == Side.SELL)
    assert buy.price == 99.0
    assert sell.price == 101.0


def test_build_levels_from_zones_rejects_bad_input():
    with pytest.raises(ValueError):
        grid_engine.build_levels_from_zones(0, [], [], 1, 6.0, 1.0)
    with pytest.raises(ValueError):
        grid_engine.build_levels_from_zones(100, [], [], 0, 6.0, 1.0)


def test_apply_inventory_skew_zero_is_noop():
    levels = grid_engine.build_levels(100.0, 1.0, 2, 6.0)
    adjusted = grid_engine.apply_inventory_skew(levels, skew=0.0)
    assert [l.size_usdt for l in adjusted] == [l.size_usdt for l in levels]


def test_apply_inventory_skew_positive_favors_sell_side():
    levels = grid_engine.build_levels(100.0, 1.0, 1, 10.0)
    adjusted = grid_engine.apply_inventory_skew(levels, skew=0.5)

    buy = next(l for l in adjusted if l.side == Side.BUY)
    sell = next(l for l in adjusted if l.side == Side.SELL)

    assert sell.size_usdt == 15.0  # 10 * (1 + 0.5)
    assert buy.size_usdt == 5.0    # 10 * (1 - 0.5)


def test_apply_inventory_skew_clamped_to_valid_range():
    levels = grid_engine.build_levels(100.0, 1.0, 1, 10.0)
    adjusted = grid_engine.apply_inventory_skew(levels, skew=5.0)  # clamped to 1.0

    buy = next(l for l in adjusted if l.side == Side.BUY)
    sell = next(l for l in adjusted if l.side == Side.SELL)

    assert sell.size_usdt == 20.0
    assert buy.size_usdt == 0.0
