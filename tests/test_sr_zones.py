# tests/test_sr_zones.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

import pandas as pd

from onembot.strategy.sr_zones import Zone, cluster_zones, find_swing_points, find_zones, nearest_support_resistance


def _make_df(highs: list[float], lows: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(highs), freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": lows, "high": highs, "low": lows, "close": lows, "volume": 1.0,
    }, index=idx)


def test_find_swing_points_detects_single_local_peak():
    highs = [100, 101, 102, 110, 102, 101, 100]
    lows = [h - 1 for h in highs]
    df = _make_df(highs, lows)
    swing_highs, swing_lows = find_swing_points(df, window=2)
    assert swing_highs == [110]


def test_find_swing_points_detects_single_local_trough():
    lows = [100, 99, 98, 90, 98, 99, 100]
    highs = [l + 1 for l in lows]
    df = _make_df(highs, lows)
    swing_highs, swing_lows = find_swing_points(df, window=2)
    assert swing_lows == [90]


def test_find_swing_points_too_short_series_returns_empty():
    df = _make_df([100, 101, 102], [99, 100, 101])
    swing_highs, swing_lows = find_swing_points(df, window=3)
    assert swing_highs == []
    assert swing_lows == []


def test_cluster_zones_groups_nearby_points():
    points = [100.0, 100.05, 100.1, 200.0, 200.2]
    zones = cluster_zones(points, tolerance_pct=0.01)
    assert len(zones) == 2
    assert zones[0].touches == 3
    assert zones[1].touches == 2


def test_cluster_zones_keeps_far_points_separate():
    points = [100.0, 500.0, 1000.0]
    zones = cluster_zones(points, tolerance_pct=0.001)
    assert len(zones) == 3


def test_cluster_zones_empty_input():
    assert cluster_zones([], tolerance_pct=0.01) == []


def test_find_zones_filters_weak_zones_by_min_touches():
    # Ein isolierter Swing-Punkt (touches=1) soll bei min_touches=2 verworfen werden,
    # ein wiederholt beruehrtes Level soll bestehen bleiben.
    highs = [100, 101, 150, 101, 100, 130, 100, 101, 150, 101, 100]
    lows = [h - 1 for h in highs]
    df = _make_df(highs, lows)
    zones = find_zones(df, window=2, tolerance_pct=0.01, min_touches=2)
    assert any(z.touches >= 2 for z in zones)
    assert all(z.touches >= 2 for z in zones)


def test_nearest_support_resistance_picks_closest_on_each_side():
    zones = [Zone(price=90.0, touches=3), Zone(price=95.0, touches=2), Zone(price=110.0, touches=2), Zone(price=120.0, touches=1)]
    support, resistance = nearest_support_resistance(100.0, zones)
    assert support.price == 95.0
    assert resistance.price == 110.0


def test_nearest_support_resistance_no_support_below():
    zones = [Zone(price=110.0, touches=2)]
    support, resistance = nearest_support_resistance(100.0, zones)
    assert support is None
    assert resistance.price == 110.0


def test_nearest_support_resistance_no_resistance_above():
    zones = [Zone(price=90.0, touches=2)]
    support, resistance = nearest_support_resistance(100.0, zones)
    assert support.price == 90.0
    assert resistance is None
