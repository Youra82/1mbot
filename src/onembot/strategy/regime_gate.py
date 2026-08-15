# src/onembot/strategy/regime_gate.py
# 1:1 portiert aus superbot/src/superbot/strategy/regime_gate.py — siehe dort fuer Historie/Herleitung.
"""
Layer 0 — Regime-/Chaos-Gate.

Klassifiziert jede Kerze in TREND | RANGE | CHAOS, bevor irgendein
Signal-Modul ueberhaupt aufgerufen wird. Kombiniert die vier unabhaengig
in apexbot (Hurst+Entropie/RADAR), pbot (ADX/ATR-Percentile), mbot
(Phasenraum-Entropie) und ltbbot (harter ADX>30 Block) entstandenen
Ansaetze zu einem gemeinsamen Nenner.

Kernregel (in 4+ Bots unabhaengig wiederentdeckt): bei CHAOS wird nicht
gehandelt — das ist der groesste Sicherheitshebel im ganzen Portfolio.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Regime(str, Enum):
    TREND = "TREND"
    RANGE = "RANGE"
    CHAOS = "CHAOS"


@dataclass
class RegimeState:
    regime: Regime
    hurst: float
    entropy: float
    adx: float
    confidence: float          # 0..1 — wie eindeutig die Klassifikation ist
    details: dict


# ── Indikatoren (apexbot radar.py Pattern) ─────────────────────────────────────

def compute_hurst(close: pd.Series, lags: int = 20, min_lookback: int = 200) -> float:
    """
    Hurst-Exponent via klassischer R/S-Analyse auf Log-Returns.
    H > 0.5 : persistent / trending
    H < 0.5 : anti-persistent / mean-reverting
    H = 0.5 : random walk

    KORRIGIERTE Version — apexbots radar.py (woher diese Funktion urspruenglich
    stammt) wendet R/S direkt auf rohe Preisniveaus statt auf Returns an. Das
    ist methodisch falsch: beim Smoke-Test dieser superbot-Uebernahme lieferte
    die Original-Formel fuer einen reinen Random Walk H~=0.19 (statt ~0.5) und
    fuer einen perfekten linearen Trend sogar noch niedriger — also strukturell
    falschherum. Diese Version rechnet stattdessen auf kumulierten, mittelwert-
    bereinigten Log-Return-Abweichungen (Standard-R/S-Verfahren).

    Bekannte Einschraenkung: einfache R/S-Schaetzer sind bei kleinen
    Stichproben (<500 Kerzen) nach oben verzerrt (dokumentiertes Problem des
    Verfahrens, siehe Anis-Lloyd/Lo-Korrekturen) — die Schwellenwerte in
    DEFAULT_CONFIG (hurst_trend_min/hurst_range_max) sind Platzhalter aus
    apexbot und muessen per Backtest/Walk-Forward auf echten Daten
    nachkalibriert werden, sobald genug Historie vorliegt.
    """
    n_needed = max(min_lookback, lags * 4)
    if len(close) < n_needed + 1:
        return 0.5

    prices = close.values[-(n_needed + 1):]
    log_returns = np.diff(np.log(np.maximum(prices, 1e-10)))

    tau, lag_list = [], []
    max_lag = min(lags, len(log_returns) // 4)
    for lag in range(2, max(max_lag, 3)):
        n_chunks = len(log_returns) // lag
        if n_chunks < 4:
            continue
        rs_vals = []
        for c in range(n_chunks):
            chunk = log_returns[c * lag:(c + 1) * lag]
            dev = np.cumsum(chunk - chunk.mean())
            r = dev.max() - dev.min()
            s = chunk.std()
            if s > 1e-10:
                rs_vals.append(r / s)
        if rs_vals:
            tau.append(float(np.mean(rs_vals)))
            lag_list.append(lag)

    if len(tau) < 2:
        return 0.5

    try:
        poly = np.polyfit(np.log(lag_list), np.log(np.maximum(tau, 1e-10)), 1)
        return float(np.clip(poly[0], 0.0, 1.0))
    except Exception:
        return 0.5


def compute_entropy(close: pd.Series, n: int = 20, bins: int = 10) -> float:
    """
    Shannon-Entropie der letzten n Log-Returns, normalisiert auf [0, 1].
    0 = maximale Ordnung, 1 = maximales Chaos.

    BEKANNTE SCHWAECHE (beim Smoke-Test der superbot-Uebernahme aus apexbots
    radar.py entdeckt, ungefixt): dieses Histogramm-basierte Mass diskriminiert
    in der Praxis kaum zwischen Regimes. Empirischer Test ueber mehrere
    synthetische Serien: reines weisses Rauschen ~0.87-0.90, ein sehr sauberer
    Trend (SNR 15:1) ~0.86-0.93, eine konstruierte "chaotische" Sprung-Serie
    ~0.78-0.80 — also SCHWAECHER (chaotischer sollte hoeher sein!) als
    weisses Rauschen. Ursache: Histogramm-Entropie mit n=20/bins=10 ist bei
    dieser Stichprobengroesse dominiert von Bin-Zaehl-Rauschen, nicht von der
    tatsaechlichen Ordnung des Prozesses. Deshalb steht der CHAOS-Schwellwert
    (entropy_chaos_min) bewusst sehr hoch (0.97 statt der urspruenglichen
    apexbot-Vorgabe 0.70) — sonst wuerde diese Komponente PERMANENT CHAOS
    ausloesen und den Bot faktisch stilllegen. Hurst (siehe compute_hurst,
    dort bereits korrigiert) und ADX tragen aktuell die Hauptlast der Regime-
    Erkennung; die Entropie-Komponente ist bis zu einer echten Neukonzeption
    (z.B. Sample-/Permutation-Entropie statt Histogramm-Entropie) nur ein
    schwaches Zusatzsignal, kein verlaesslicher Chaos-Filter.
    """
    if len(close) < n + 1:
        return 1.0
    prices = close.values[-(n + 1):]
    returns = np.diff(np.log(np.maximum(prices, 1e-10)))
    counts, _ = np.histogram(returns, bins=bins)
    probs = counts / counts.sum()
    probs = probs[probs > 0]
    entropy = -np.sum(probs * np.log(probs))
    max_entropy = np.log(bins)
    return float(entropy / max_entropy) if max_entropy > 0 else 1.0


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_adx(df: pd.DataFrame, period: int = 14) -> float:
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    tr = compute_atr(df, period)
    plus_di = 100 * (plus_dm.rolling(period).mean() / tr)
    minus_di = 100 * (minus_dm.rolling(period).mean() / tr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(period).mean().iloc[-1]
    return float(adx) if pd.notna(adx) else 0.0


# ── Regime-Klassifikation ──────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "hurst_trend_min": 0.55,     # >= : persistent genug fuer Trend-Modul
    "adx_trend_min": 25.0,       # >= : Richtungsstaerke fuer Trend-Modul
    "hurst_range_max": 0.50,     # <= : mean-reverting genug fuer Range-Modul
    "adx_range_max": 20.0,       # <= : keine Richtungsstaerke -> Range-Modul
    # >= : zu ungeordnet -> CHAOS, kein Trading. BEWUSST hoch (nicht apexbots
    # 0.70 uebernommen) — siehe Warnung in compute_entropy(): mit 0.70 waere
    # dieser Filter bei realistischem Marktrauschen praktisch immer aktiv und
    # wuerde den Bot stilllegen. 0.97 laesst nur echte Ausreisser durch, bis
    # die Entropie-Berechnung selbst ueberarbeitet ist.
    "entropy_chaos_min": 0.97,
    "adx_strong_trend_block": 35.0,  # >= : extrem starker Trend -> Mean-Reversion-Modul sperren
    "hurst_min_lookback": 200,   # Mindest-Kerzen fuer eine halbwegs stabile Hurst-Schaetzung
}


def classify_regime(df: pd.DataFrame, config: dict | None = None,
                     previous_regime: Regime | None = None) -> RegimeState:
    """
    Klassifiziert das aktuelle Marktregime auf Basis der letzten Kerze.

    Reihenfolge (apexbot-Pattern): Entropie zuerst pruefen — hohe Entropie
    override't jede andere Klassifikation, weil unvorhersehbares Verhalten
    die staerkste Sicherheitsregel im gesamten Bot-Portfolio ist.

    Braucht mindestens `hurst_min_lookback` Kerzen (Default 200), weil
    compute_hurst() bei kleineren Stichproben stark verrauscht ist (siehe
    Docstring dort) — zu wenig Daten fuehrt bewusst zu CHAOS statt zu einer
    unzuverlaessigen TREND/RANGE-Klassifikation.

    `previous_regime` gibt Hysterese fuer die uneindeutige Zone (weder klar
    TREND noch klar RANGE, aber Entropie unauffaellig): ADX oszilliert bei
    1-Minuten-Aufloesung haeufig knapp um adx_trend_min, was ohne Hysterese
    zu staendigem CHAOS<->TREND-Flackern fuehrt (im Live-Betrieb beobachtet:
    ~1 Regimewechsel alle 16 Minuten, davon >95% aus dieser Grauzone, <5%
    echtes Entropie-Chaos) -- jeder Wechsel storniert/baut das Grid neu und
    kostet Handelszeit ohne echten Sicherheitsgewinn. Ohne uebergebenes
    `previous_regime` faellt die Grauzone weiterhin auf CHAOS zurueck
    (Default-Verhalten fuer Erstklassifikation / Tests unveraendert).
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    min_len = max(50, cfg["hurst_min_lookback"])

    if df is None or len(df) < min_len:
        return RegimeState(
            regime=Regime.CHAOS, hurst=0.5, entropy=1.0, adx=0.0,
            confidence=0.0, details={"reason": f"zu wenig Kerzen (<{min_len})"},
        )

    entropy = compute_entropy(df["close"])
    hurst = compute_hurst(df["close"], min_lookback=cfg["hurst_min_lookback"])
    adx = compute_adx(df)

    details = {"hurst": round(hurst, 3), "entropy": round(entropy, 3), "adx": round(adx, 2)}

    if entropy >= cfg["entropy_chaos_min"]:
        return RegimeState(
            regime=Regime.CHAOS, hurst=hurst, entropy=entropy, adx=adx,
            confidence=min(1.0, (entropy - cfg["entropy_chaos_min"]) / (1.0 - cfg["entropy_chaos_min"]) + 0.5),
            details={**details, "reason": "Entropie ueber Chaos-Schwelle"},
        )

    if hurst >= cfg["hurst_trend_min"] and adx >= cfg["adx_trend_min"]:
        confidence = min(1.0, (hurst - 0.5) * 2) * min(1.0, adx / 50.0)
        return RegimeState(
            regime=Regime.TREND, hurst=hurst, entropy=entropy, adx=adx,
            confidence=confidence, details=details,
        )

    if hurst <= cfg["hurst_range_max"] and adx <= cfg["adx_range_max"]:
        confidence = min(1.0, (0.5 - hurst) * 2 + 0.3) * min(1.0, 1.0 - adx / cfg["adx_range_max"] + 0.3)
        return RegimeState(
            regime=Regime.RANGE, hurst=hurst, entropy=entropy, adx=adx,
            confidence=max(0.0, min(1.0, confidence)), details=details,
        )

    # Uneindeutig (weder klar TREND noch klar RANGE, aber Entropie ok) --
    # Hysterese: beim vorherigen (tradebaren) Regime bleiben statt auf CHAOS
    # zu wechseln, sonst flackert das Grid bei jedem ADX-Wackler ums
    # Trend-Threshold komplett ab/an (siehe Docstring oben).
    sticky_regime = previous_regime if previous_regime is not None else Regime.CHAOS
    reason = "uneindeutige Zone zwischen TREND und RANGE"
    if sticky_regime != Regime.CHAOS:
        reason += f" -- Hysterese haelt vorheriges Regime ({sticky_regime.value})"
    return RegimeState(
        regime=sticky_regime, hurst=hurst, entropy=entropy, adx=adx,
        confidence=0.3, details={**details, "reason": reason},
    )


def strong_trend_block(regime_state: RegimeState, config: dict | None = None) -> bool:
    """
    ltbbot-Regel: extrem starker Trend (ADX weit ueber Trend-Schwelle) sperrt
    Mean-Reversion-Signale hart, unabhaengig vom Regime-Label — Mean-Reversion
    scheitert systematisch in Crash/Recovery-Trends (siehe Live-vs-Backtest-
    Divergenz-Analyse in ltbbot, Winrate 25% statt versprochener 78%).
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    return regime_state.adx >= cfg["adx_strong_trend_block"]
