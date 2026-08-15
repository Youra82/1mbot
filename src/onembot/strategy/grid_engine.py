# src/onembot/strategy/grid_engine.py
"""
Grid-Level-Berechnung fuer den Market-Making-Ansatz.

Kernidee: statt eine Richtung zu wetten, werden N Buy-Levels unter und
N Sell-Levels ueber einem gleitenden Ankerpreis platziert. Wird ein Level
gefuellt, wird eine Gegenorder eine Stufe weiter aussen nachgelegt -- das
ist der eigentliche Spread-Capture-Mechanismus (siehe replenish_after_fill).

Der Level-Abstand ist ATR-basiert statt ein fixer Prozentsatz, damit sich
der Grid an die aktuelle Volatilitaet anpasst (zu eng = Whipsaw+Fee-Tod,
zu weit = kaum Fills).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class GridLevel:
    side: Side
    price: float
    size_usdt: float
    index: int  # 1 = naechster am Anker, groesser = weiter aussen


def compute_spacing(atr_value: float, spacing_atr_mult: float) -> float:
    """Absoluter Preisabstand zwischen zwei Grid-Stufen."""
    if atr_value <= 0:
        raise ValueError("atr_value muss > 0 sein")
    return atr_value * spacing_atr_mult


def build_levels(
    anchor_price: float,
    spacing: float,
    levels_per_side: int,
    level_size_usdt: float,
) -> list[GridLevel]:
    """
    Symmetrischer Grid um anchor_price: levels_per_side Buy-Levels darunter,
    levels_per_side Sell-Levels darueber, gleichmaessig gestaffelt.
    """
    if anchor_price <= 0:
        raise ValueError("anchor_price muss > 0 sein")
    if levels_per_side < 1:
        raise ValueError("levels_per_side muss >= 1 sein")

    levels: list[GridLevel] = []
    for i in range(1, levels_per_side + 1):
        levels.append(GridLevel(
            side=Side.BUY,
            price=anchor_price - i * spacing,
            size_usdt=level_size_usdt,
            index=i,
        ))
        levels.append(GridLevel(
            side=Side.SELL,
            price=anchor_price + i * spacing,
            size_usdt=level_size_usdt,
            index=i,
        ))
    return levels


def replenish_after_fill(filled_level: GridLevel, spacing: float) -> GridLevel:
    """
    Nach einem Fill wird eine Gegenorder eine Stufe weiter aussen
    nachgelegt (Buy gefuellt -> neue Sell-Order eine Stufe hoeher, und
    umgekehrt). Das ist der Spread-Capture-Schritt: die neue Gegenorder
    liegt oberhalb/unterhalb des Fill-Preises, nicht am alten Level.
    """
    if filled_level.side == Side.BUY:
        return GridLevel(
            side=Side.SELL,
            price=filled_level.price + spacing,
            size_usdt=filled_level.size_usdt,
            index=filled_level.index,
        )
    return GridLevel(
        side=Side.BUY,
        price=filled_level.price - spacing,
        size_usdt=filled_level.size_usdt,
        index=filled_level.index,
    )


def build_levels_from_zones(
    anchor_price: float,
    zone_prices_below: list[float],
    zone_prices_above: list[float],
    levels_per_side: int,
    level_size_usdt: float,
    fallback_spacing: float,
) -> list[GridLevel]:
    """
    Platziert Grid-Levels bevorzugt AN echten Support-/Resistance-Zonen
    (siehe strategy/sr_zones.py) statt an gleichmaessigen ATR-Vielfachen --
    ein Level an einem Preis, an dem der Markt bereits mehrfach gedreht
    hat, hat eine hoehere Chance auf Mean-Reversion als ein willkuerlicher
    ATR-Abstand (siehe manuelles HFT-Grid-Vorbild: Limit-Orders geclustert
    an Konsolidierungszonen/vorherigen Swing-Points, nicht gleichmaessig
    verteilt).

    Eine Zone wird aber NUR verwendet, wenn sie mindestens so weit vom
    Anker entfernt liegt wie die ATR-Spacing-Stufe -- sonst wuerde eine
    zufaellig sehr nahe Zone die kalibrierte Mindest-Spacing unterlaufen
    (empirisch schlechter: mehr, aber kleinere Fills, siehe Backtest-
    Vergleich). Zonen koennen die Platzierung also nur verbessern
    (weiter/praeziser als der ATR-Fallback), nie verengen.

    `zone_prices_below`/`_above` muessen bereits nach Naehe zum Anker
    sortiert sein (naechste Zone zuerst, z.B. via sr_zones.zones_below/
    zones_above). Fehlt fuer eine Stufe eine Zone, wird mit einem
    gleichmaessigen `fallback_spacing`-Vielfachen aufgefuellt (reiner
    ATR-Fallback wie build_levels(), falls gar keine Zonen bekannt sind).
    """
    if anchor_price <= 0:
        raise ValueError("anchor_price muss > 0 sein")
    if levels_per_side < 1:
        raise ValueError("levels_per_side muss >= 1 sein")

    levels: list[GridLevel] = []
    for i in range(1, levels_per_side + 1):
        atr_buy_price = anchor_price - i * fallback_spacing
        buy_price = min(zone_prices_below[i - 1], atr_buy_price) if i <= len(zone_prices_below) else atr_buy_price
        levels.append(GridLevel(side=Side.BUY, price=buy_price, size_usdt=level_size_usdt, index=i))

        atr_sell_price = anchor_price + i * fallback_spacing
        sell_price = max(zone_prices_above[i - 1], atr_sell_price) if i <= len(zone_prices_above) else atr_sell_price
        levels.append(GridLevel(side=Side.SELL, price=sell_price, size_usdt=level_size_usdt, index=i))
    return levels


def apply_inventory_skew(levels: list[GridLevel], skew: float) -> list[GridLevel]:
    """
    Skaliert die Level-Groesse asymmetrisch je nach Netto-Inventory-Skew.

    skew > 0  => Long-lastig -> Sell-Seite verstaerken (schneller abbauen),
                 Buy-Seite reduzieren (nicht weiter aufbauen)
    skew < 0  => Short-lastig -> umgekehrt
    skew wird in [-1, 1] erwartet (siehe risk/inventory.py).

    Reine Groessen-Skalierung, keine Preisverschiebung -- so bleiben die
    Level-Preise weiterhin ATR-basiert und unabhaengig vom Inventory-Zustand.
    """
    skew = max(-1.0, min(1.0, skew))
    adjusted: list[GridLevel] = []
    for lvl in levels:
        factor = (1.0 + skew) if lvl.side == Side.SELL else (1.0 - skew)
        factor = max(0.0, factor)
        adjusted.append(GridLevel(
            side=lvl.side,
            price=lvl.price,
            size_usdt=round(lvl.size_usdt * factor, 4),
            index=lvl.index,
        ))
    return adjusted
