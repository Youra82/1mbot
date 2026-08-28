# tests/test_report.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

import pytest

from onembot.utils.report import build_report


def _fill(symbol, pnl, ts):
    return {"symbol": symbol, "realized_pnl_usdt": pnl, "timestamp": ts}


def test_build_report_empty():
    report = build_report([])
    assert report.total_pnl_usdt == 0.0
    assert report.num_fills == 0
    assert report.max_drawdown_usdt == 0.0
    assert report.per_symbol == {}


def test_build_report_cumulative_pnl_and_per_symbol():
    fills = [
        _fill("BTC/USDT:USDT", 1.0, "2026-01-01T00:00:00+00:00"),
        _fill("ETH/USDT:USDT", 0.5, "2026-01-01T00:01:00+00:00"),
        _fill("BTC/USDT:USDT", -0.2, "2026-01-01T00:02:00+00:00"),
    ]
    report = build_report(fills)

    assert report.num_fills == 3
    assert report.total_pnl_usdt == pytest.approx(1.3)
    assert report.per_symbol["BTC/USDT:USDT"] == {"pnl": pytest.approx(0.8), "fills": 2}
    assert report.per_symbol["ETH/USDT:USDT"] == {"pnl": pytest.approx(0.5), "fills": 1}


def test_build_report_sorts_by_timestamp_before_accumulating():
    # Absichtlich unsortiert uebergeben -- build_report muss selbst sortieren,
    # sonst waere die Equity-Kurve/Drawdown-Berechnung von der Eingabereihenfolge abhaengig.
    fills = [
        _fill("BTC/USDT:USDT", -1.0, "2026-01-01T00:02:00+00:00"),
        _fill("BTC/USDT:USDT", 2.0, "2026-01-01T00:00:00+00:00"),
        _fill("BTC/USDT:USDT", -1.0, "2026-01-01T00:01:00+00:00"),
    ]
    report = build_report(fills)

    # Chronologisch: +2 -> Peak=2, dann -1 -> 1 (Drawdown=1), dann -1 -> 0 (Drawdown=2)
    assert [round(c, 2) for _, c in report.equity_curve] == [2.0, 1.0, 0.0]
    assert report.max_drawdown_usdt == pytest.approx(2.0)


def test_build_report_max_drawdown_tracks_peak_to_trough():
    fills = [
        _fill("X", 5.0, "t1"),
        _fill("X", -3.0, "t2"),  # Drawdown 3 von Peak 5
        _fill("X", 1.0, "t3"),   # immer noch unter Peak
        _fill("X", -1.0, "t4"),  # Drawdown jetzt 3 (5 -> 2)
        _fill("X", 10.0, "t5"),  # neuer Peak 12
    ]
    report = build_report(fills)

    assert report.total_pnl_usdt == pytest.approx(12.0)
    assert report.max_drawdown_usdt == pytest.approx(3.0)


def test_payoff_ratio_computed_from_avg_win_over_avg_loss():
    fills = [
        _fill("X", 4.0, "t1"),
        _fill("X", 2.0, "t2"),   # avg_win = 3.0
        _fill("X", -1.0, "t3"),
        _fill("X", -3.0, "t4"),  # avg_loss = 2.0
    ]
    report = build_report(fills)

    assert report.win_count == 2
    assert report.loss_count == 2
    assert report.avg_win_usdt == pytest.approx(3.0)
    assert report.avg_loss_usdt == pytest.approx(2.0)
    assert report.payoff_ratio == pytest.approx(1.5)


def test_payoff_ratio_none_without_losses():
    fills = [_fill("X", 1.0, "t1"), _fill("X", 2.0, "t2")]
    report = build_report(fills)
    assert report.payoff_ratio is None


def test_payoff_ratio_zero_without_wins():
    fills = [_fill("X", -1.0, "t1"), _fill("X", -2.0, "t2")]
    report = build_report(fills)
    assert report.avg_win_usdt == 0.0
    assert report.payoff_ratio == pytest.approx(0.0)


def test_max_win_share_low_for_many_similar_wins():
    # Viele aehnlich grosse Gewinner + ein kleiner Verlierer -- ein einzelner
    # Fill dominiert die Gewinnsumme NICHT, max_win_share bleibt klein.
    fills = [_fill("X", 1.0, f"t{i}") for i in range(10)] + [_fill("X", -1.0, "t_loss")]
    report = build_report(fills)
    assert report.max_win_usdt == pytest.approx(1.0)
    assert report.max_win_share == pytest.approx(0.1)  # 1.0 / 10.0


def test_max_win_share_high_for_single_dominant_win():
    # Ein einzelner riesiger Gewinner dominiert die gesamte Gewinnsumme --
    # genau die Situation, die die Payoff-Ratio allein nicht von "viele
    # gleichmaessige Gewinner" unterscheiden kann (siehe EquityReport-Docstring).
    fills = [_fill("X", 0.1, f"t{i}") for i in range(5)] + [_fill("X", 50.0, "t_big")] + [_fill("X", -1.0, "t_loss")]
    report = build_report(fills)
    assert report.max_win_usdt == pytest.approx(50.0)
    assert report.max_win_share == pytest.approx(50.0 / 50.5)


def test_max_win_share_zero_without_wins():
    fills = [_fill("X", -1.0, "t1")]
    report = build_report(fills)
    assert report.max_win_share == 0.0


def test_max_loss_usdt_tracks_largest_single_loss():
    fills = [_fill("X", 5.0, "t1"), _fill("X", -1.0, "t2"), _fill("X", -4.0, "t3"), _fill("X", -2.0, "t4")]
    report = build_report(fills)
    assert report.max_loss_usdt == pytest.approx(4.0)


def test_build_report_empty_win_loss_fields_default_to_zero():
    report = build_report([])
    assert report.win_count == 0
    assert report.loss_count == 0
    assert report.avg_win_usdt == 0.0
    assert report.avg_loss_usdt == 0.0
    assert report.payoff_ratio is None
