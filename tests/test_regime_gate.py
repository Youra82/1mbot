# tests/test_regime_gate.py
"""
regime_gate.py wurde unveraendert aus superbot uebernommen (siehe Kopfkommentar
der Datei). Diese Tests pruefen bewusst nur strukturelles Verhalten
(Datenmenge, Schwellenwert-Verdrahtung), nicht konkrete Hurst/Entropie-Werte
auf synthetischen Serien -- die Docstrings dort warnen ausdruecklich, dass
beide Schaetzer bei kleinen/synthetischen Stichproben verrauscht sind.
"""
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

import numpy as np
import pandas as pd
import pytest

from onembot.strategy.regime_gate import Regime, classify_regime, compute_atr, strong_trend_block, RegimeState


def _make_df(n: int, start: float = 100.0, step: float = 0.5) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = start + np.arange(n) * step
    return pd.DataFrame({
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1.0,
    }, index=idx)


def test_classify_regime_insufficient_data_returns_chaos():
    df = _make_df(50)  # unter hurst_min_lookback=200 Default
    state = classify_regime(df)
    assert state.regime == Regime.CHAOS
    assert state.confidence == 0.0


def test_classify_regime_none_df_returns_chaos():
    state = classify_regime(None)
    assert state.regime == Regime.CHAOS


def test_classify_regime_entropy_override_forces_chaos():
    df = _make_df(300)
    # entropy_chaos_min=0.0 -> jede Entropie (>=0) triggert die Chaos-Regel
    state = classify_regime(df, config={"entropy_chaos_min": 0.0})
    assert state.regime == Regime.CHAOS
    assert state.details.get("reason") == "Entropie ueber Chaos-Schwelle"


def test_classify_regime_trend_branch_when_thresholds_trivially_met():
    df = _make_df(300)
    # Schwellen so gesetzt, dass die TREND-Bedingung strukturell garantiert greift,
    # unabhaengig vom konkreten Hurst/ADX-Wert der synthetischen Serie.
    state = classify_regime(df, config={
        "entropy_chaos_min": 1.1,  # nie erreichbar (normalisierte Entropie <= 1.0)
        "hurst_trend_min": 0.0,
        "adx_trend_min": 0.0,
    })
    assert state.regime == Regime.TREND


def test_classify_regime_range_branch_when_thresholds_trivially_met():
    df = _make_df(300)
    state = classify_regime(df, config={
        "entropy_chaos_min": 1.1,
        "hurst_trend_min": 1.1,   # TREND-Zweig darf nicht zuerst greifen
        "adx_trend_min": 1000.0,
        "hurst_range_max": 1.0,
        "adx_range_max": 1000.0,
    })
    assert state.regime == Regime.RANGE


def _ambiguous_config() -> dict:
    """Schwellen so gesetzt, dass weder TREND- noch RANGE-Zweig jemals
    greifen kann, unabhaengig vom konkreten Hurst/ADX/Entropie-Wert --
    zwingt classify_regime() strukturell in die uneindeutige Zone."""
    return {
        "entropy_chaos_min": 1.1,   # nie erreichbar (normalisierte Entropie <= 1.0)
        "hurst_trend_min": 1.1,     # nie erreichbar (Hurst geclippt auf [0,1])
        "hurst_range_max": -1.0,   # nie erreichbar
    }


def test_classify_regime_ambiguous_zone_without_previous_defaults_to_chaos():
    df = _make_df(300)
    state = classify_regime(df, config=_ambiguous_config())
    assert state.regime == Regime.CHAOS
    assert "uneindeutige Zone" in state.details["reason"]


def test_classify_regime_ambiguous_zone_sticks_to_previous_trend():
    df = _make_df(300)
    state = classify_regime(df, config=_ambiguous_config(), previous_regime=Regime.TREND)
    assert state.regime == Regime.TREND


def test_classify_regime_ambiguous_zone_sticks_to_previous_range():
    df = _make_df(300)
    state = classify_regime(df, config=_ambiguous_config(), previous_regime=Regime.RANGE)
    assert state.regime == Regime.RANGE


def test_classify_regime_ambiguous_zone_previous_chaos_stays_chaos():
    df = _make_df(300)
    state = classify_regime(df, config=_ambiguous_config(), previous_regime=Regime.CHAOS)
    assert state.regime == Regime.CHAOS


def test_strong_trend_block_above_threshold():
    state = RegimeState(regime=Regime.TREND, hurst=0.7, entropy=0.5, adx=40.0, confidence=1.0, details={})
    assert strong_trend_block(state) is True


def test_strong_trend_block_below_threshold():
    state = RegimeState(regime=Regime.TREND, hurst=0.7, entropy=0.5, adx=25.0, confidence=1.0, details={})
    assert strong_trend_block(state) is False


def test_compute_atr_converges_to_constant_high_low_range():
    df = _make_df(50)  # high - low ist konstant 2.0 in der Fixture
    atr = compute_atr(df, period=14)
    assert atr.iloc[-1] == pytest.approx(2.0, abs=0.3)
