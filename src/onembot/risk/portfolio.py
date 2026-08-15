# src/onembot/risk/portfolio.py
"""
Portfolio-weiter Exposure-Cap ueber alle Watchlist-Symbole.

Warum zusaetzlich zum Pro-Symbol-Cap (siehe inventory.py): der Pro-Symbol-
Cap schuetzt jedes Symbol einzeln, aber nicht vor dem Fall, dass mehrere
Symbole gleichzeitig in dieselbe Trend-Richtung laufen (z.B. ein
marktweiter Crash) -- dann bleibt jedes Symbol innerhalb seines eigenen
Caps, waehrend sich ueber die gesamte Watchlist trotzdem unbegrenztes
Risiko aufbaut. Exposure wird als Summe der Betraege (nicht Netto-Saldo)
ueber alle Symbole gemessen, weil unterschiedliche Symbole unkorreliert
in unterschiedliche Richtungen laufen koennen -- die Summe der Betraege
ist die konservative Schaetzung des insgesamt gebundenen Kapitals.

Eine Instanz wird pro Live-Lauf zwischen allen SymbolWorkern geteilt
(asyncio ist single-threaded -- kein Lock noetig).
"""
from __future__ import annotations


class PortfolioRiskManager:
    def __init__(self, max_portfolio_inventory_usdt: float):
        if max_portfolio_inventory_usdt <= 0:
            raise ValueError("max_portfolio_inventory_usdt muss > 0 sein")
        self.max_portfolio_inventory_usdt = max_portfolio_inventory_usdt
        self._net_usdt: dict[str, float] = {}

    def update(self, symbol: str, net_usdt: float) -> None:
        self._net_usdt[symbol] = net_usdt

    def total_exposure_usdt(self) -> float:
        return sum(abs(v) for v in self._net_usdt.values())

    def is_over_cap(self) -> bool:
        return self.total_exposure_usdt() > self.max_portfolio_inventory_usdt
