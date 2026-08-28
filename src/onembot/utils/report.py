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
    win_count: int = 0
    loss_count: int = 0
    total_win_usdt: float = 0.0     # Summe aller positiven Fill-PnLs
    total_loss_usdt: float = 0.0    # Summe der BETRAEGE aller negativen Fill-PnLs (positiv)
    per_symbol: dict = field(default_factory=dict)         # symbol -> {"pnl": float, "fills": int}
    equity_curve: list = field(default_factory=list)        # [(timestamp_str, cumulative_pnl), ...]

    @property
    def avg_win_usdt(self) -> float:
        return self.total_win_usdt / self.win_count if self.win_count else 0.0

    @property
    def avg_loss_usdt(self) -> float:
        return self.total_loss_usdt / self.loss_count if self.loss_count else 0.0

    @property
    def payoff_ratio(self) -> float | None:
        """avg_win/avg_loss -- None wenn keine Verlust-Fills vorliegen (Ratio waere
        undefiniert/unendlich, kein sinnvoller Zahlenwert). total_win_usdt/total_loss_usdt
        werden bewusst als ROHE SUMMEN gespeichert (nicht als fertige Durchschnitte) --
        optimizer.py muss den Payoff-Ratio ueber mehrere Tage/EquityReports hinweg
        aggregieren, und der Durchschnitt mehrerer Tages-Durchschnitte waere bei
        unterschiedlicher Fill-Zahl pro Tag falsch gewichtet."""
        avg_loss = self.avg_loss_usdt
        return (self.avg_win_usdt / avg_loss) if avg_loss > 0 else None


def build_report(fills: list[dict]) -> EquityReport:
    fills_sorted = sorted(fills, key=lambda f: f.get("timestamp", ""))

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    curve: list[tuple[str, float]] = []
    per_symbol: dict[str, dict] = {}
    win_count = 0
    loss_count = 0
    total_win = 0.0
    total_loss = 0.0

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

        if pnl > 0:
            win_count += 1
            total_win += pnl
        elif pnl < 0:
            loss_count += 1
            total_loss += -pnl

    return EquityReport(
        total_pnl_usdt=cumulative,
        num_fills=len(fills_sorted),
        max_drawdown_usdt=max_dd,
        win_count=win_count,
        loss_count=loss_count,
        total_win_usdt=total_win,
        total_loss_usdt=total_loss,
        per_symbol=per_symbol,
        equity_curve=curve,
    )
