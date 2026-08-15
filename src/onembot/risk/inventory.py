# src/onembot/risk/inventory.py
"""
Netto-Exposure-Cap und Skew-Berechnung fuer den Grid-Bot.

Das Kern-Risiko von Grid/Market-Making ist Inventory-Risiko: in einer
echten Trendphase sammelt sich eine immer groessere Position in die
falsche Richtung an, bis der Grid "bricht". Der Bot handelt bewusst in
RANGE UND TREND (regime_gate.py sperrt nur noch CHAOS hart) -- auf
1-Minuten-Aufloesung ist selbst eine "trendende" Kerze noch von Noise
durchsetzt, den der Grid abschoepfen soll. Dieses Modul ist deshalb die
eigentliche Schutzschicht gegen unbegrenzte Trend-Exposure: harte
Kappungsgrenze + Skew-Faktor fuer grid_engine.apply_inventory_skew.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InventoryState:
    symbol: str
    net_usdt: float  # positiv = Long-Ueberhang, negativ = Short-Ueberhang


def compute_skew(state: InventoryState, max_net_inventory_usdt: float, skew_max: float) -> float:
    """
    Skaliert net_usdt relativ zur Cap-Grenze auf einen Skew-Faktor in
    [-skew_max, skew_max], den grid_engine.apply_inventory_skew konsumiert.
    """
    if max_net_inventory_usdt <= 0:
        raise ValueError("max_net_inventory_usdt muss > 0 sein")
    ratio = state.net_usdt / max_net_inventory_usdt
    ratio = max(-1.0, min(1.0, ratio))
    return ratio * skew_max


def is_over_cap(state: InventoryState, max_net_inventory_usdt: float) -> bool:
    """True, wenn das Netto-Inventory die Kappungsgrenze ueberschritten hat."""
    return abs(state.net_usdt) > max_net_inventory_usdt


def blocked_side(state: InventoryState, max_net_inventory_usdt: float) -> str | None:
    """
    Bei Ueberschreiten der Cap wird die Seite gesperrt, die das Inventory
    weiter in dieselbe Richtung vergroessern wuerde -- 'buy' wenn zu long,
    'sell' wenn zu short, sonst None.
    """
    if not is_over_cap(state, max_net_inventory_usdt):
        return None
    return "buy" if state.net_usdt > 0 else "sell"
