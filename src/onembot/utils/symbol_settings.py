# src/onembot/utils/symbol_settings.py
"""
Loest optionale Pro-Symbol-Overrides aus settings.json["per_symbol_overrides"]
zu einem fuer dieses Symbol effektiven Settings-Snapshot auf.

EINE gemeinsame Funktion fuer Live (run_loop.py::run_bot) und Backtest
(replay.py::replay_symbol) -- beide rufen sie an der einzigen Stelle auf, an
der ein SymbolWorker konstruiert wird, damit Live und Backtest niemals eine
zweite, potenziell abweichende Override-Logik bekommen (siehe
run_loop.py-Docstring zum geteilten SymbolWorker).

Ein Override ersetzt einen kompletten Top-Level-Block (z.B. "grid"), kein
Deep-Merge einzelner Unterfelder -- optimizer.py schreibt daher immer den
vollstaendigen grid-Block pro Symbol, nie nur ein einzelnes Feld.
"""
from __future__ import annotations


def resolve_symbol_settings(settings: dict, symbol: str) -> dict:
    override = settings.get("per_symbol_overrides", {}).get(symbol)
    if not override:
        return settings
    resolved = dict(settings)
    resolved.update(override)
    return resolved
