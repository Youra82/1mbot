# src/onembot/strategy/sr_zones.py
"""
Support-/Resistance-Zonen aus historischen Swing-Points -- ergaenzt die rein
ATR-basierte Grid-Spacing (Volatilitaet) um echte Preis-Level, an denen der
Markt wiederholt gedreht hat. Grund: ein Grid, das seine aeusseren Levels
gegen eine starke Zone hinaus platziert, sitzt genau dort, wo ein Durchbruch
(statt Mean-Reversion) am wahrscheinlichsten ist -- das Gegenteil von dem,
was ein Market-Making-Grid gewinnbringend abschoepfen kann.

Bewusst unabhaengig von regime_gate.py: Hurst/ADX/Entropie beschreiben
statistische Eigenschaften der Preisreihe, S/R-Zonen sind konkrete
Preis-Level. Beide Signale ergaenzen sich, ersetzen sich nicht.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Zone:
    price: float
    touches: int


def find_swing_points(df: pd.DataFrame, window: int = 3) -> tuple[list[float], list[float]]:
    """
    Fractal-Swing-Highs/-Lows: eine Kerze gilt als Swing-High/-Low, wenn ihr
    High/Low das Extrem unter den `window` Kerzen davor UND danach ist.
    """
    if len(df) < 2 * window + 1:
        return [], []

    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for i in range(window, n - window):
        local_high = highs[i - window:i + window + 1]
        local_low = lows[i - window:i + window + 1]
        if highs[i] == local_high.max():
            swing_highs.append(float(highs[i]))
        if lows[i] == local_low.min():
            swing_lows.append(float(lows[i]))
    return swing_highs, swing_lows


def cluster_zones(points: list[float], tolerance_pct: float) -> list[Zone]:
    """
    Fasst nahe beieinanderliegende Swing-Points zu Zonen zusammen. Zwei
    Punkte gehoeren zur selben Zone, wenn ihr relativer Abstand zum
    laufenden Cluster-Mittelwert <= tolerance_pct ist -- die Zonenbreite
    skaliert damit automatisch mit dem Preisniveau (in USDT-Termen ist eine
    0.15%-Zone bei 100 USDT winzig, bei 100000 USDT riesig).
    """
    if not points:
        return []
    pts = sorted(points)
    clusters: list[list[float]] = [[pts[0]]]
    for p in pts[1:]:
        cluster_mean = sum(clusters[-1]) / len(clusters[-1])
        if cluster_mean > 0 and abs(p - cluster_mean) / cluster_mean <= tolerance_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return [Zone(price=sum(c) / len(c), touches=len(c)) for c in clusters]


def find_zones(df: pd.DataFrame, window: int = 3, tolerance_pct: float = 0.0015,
                min_touches: int = 2) -> list[Zone]:
    """End-to-end: Swing-Points finden, clustern, schwache Zonen (< min_touches) verwerfen."""
    highs, lows = find_swing_points(df, window)
    zones = cluster_zones(highs + lows, tolerance_pct)
    return sorted((z for z in zones if z.touches >= min_touches), key=lambda z: z.price)


def zones_below(price: float, zones: list[Zone]) -> list[Zone]:
    """Zonen unterhalb `price`, naechste zuerst (absteigend nach Preis sortiert)."""
    return sorted((z for z in zones if z.price < price), key=lambda z: -z.price)


def zones_above(price: float, zones: list[Zone]) -> list[Zone]:
    """Zonen oberhalb `price`, naechste zuerst (aufsteigend nach Preis sortiert)."""
    return sorted((z for z in zones if z.price > price), key=lambda z: z.price)


def nearest_support_resistance(price: float, zones: list[Zone]) -> tuple[Zone | None, Zone | None]:
    """Naechste Zone unterhalb (Support) und oberhalb (Resistance) von `price`."""
    below = zones_below(price, zones)
    above = zones_above(price, zones)
    return (below[0] if below else None, above[0] if above else None)
