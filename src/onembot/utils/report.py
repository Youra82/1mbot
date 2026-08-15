# src/onembot/utils/report.py
"""
Baut Equity-Kurve, Max-Drawdown und Pro-Symbol-Aufschluesselung aus einer
Liste von Fill-Dicts (aus ledger.FillLedger.load()). Bewusst getrennt von
ledger.py -- Speichern und Auswerten sind unabhaengige Verantwortlichkeiten.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EquityReport:
    total_pnl_usdt: float
    num_fills: int
    max_drawdown_usdt: float
    per_symbol: dict = field(default_factory=dict)         # symbol -> {"pnl": float, "fills": int}
    equity_curve: list = field(default_factory=list)        # [(timestamp_str, cumulative_pnl), ...]


def build_report(fills: list[dict]) -> EquityReport:
    fills_sorted = sorted(fills, key=lambda f: f.get("timestamp", ""))

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    curve: list[tuple[str, float]] = []
    per_symbol: dict[str, dict] = {}

    for f in fills_sorted:
        pnl = float(f.get("realized_pnl_usdt", 0.0))
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
        curve.append((f.get("timestamp", ""), cumulative))

        symbol = f.get("symbol", "?")
        entry = per_symbol.setdefault(symbol, {"pnl": 0.0, "fills": 0})
        entry["pnl"] += pnl
        entry["fills"] += 1

    return EquityReport(
        total_pnl_usdt=cumulative,
        num_fills=len(fills_sorted),
        max_drawdown_usdt=max_dd,
        per_symbol=per_symbol,
        equity_curve=curve,
    )
