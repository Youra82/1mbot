"""Gemeinsame Regeln Upbit-Listing-Short -- EINE Quelle fuer Backtest (research/listing) und Live-Waechter.

Praeregistriert: research/listing/PRAEREGISTRIERUNG.md (Ereignis-Definition, E=30 min, H=72 h).
Hebel/Stop auf IS gewaehlt: research/listing/sizing_l2.py -> 3x, Stop +15 %.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import numpy as np

LISTING_KEYS = ("신규 거래지원", "디지털 자산 추가", "마켓 추가")
NOT_TICKERS = {"KRW", "BTC", "USDT"}
ENTRY_DELAY = timedelta(minutes=30)       # E
HOLD = timedelta(hours=72)                # H
DEDUP_WINDOW = timedelta(days=30)
COST_SIDE = 0.0006 + 0.0020               # Backtest-Kostenannahme pro Seite
STOP_SLIPPAGE = 0.01                      # Stop-Fill = Stop + 1 % (Pump-Gaps)
MAINT_MARGIN = 0.01
LEVERAGE = 3
STOP = 0.15


def is_krw_listing(title: str) -> bool:
    return "KRW" in title and any(k in title for k in LISTING_KEYS)


def extract_tickers(title: str) -> list[str]:
    return [t for t in re.findall(r"\(([A-Z0-9]{2,10})\)", title) if t not in NOT_TICKERS]


def is_duplicate(ticker: str, T: datetime, last_seen: dict[str, datetime]) -> bool:
    """Pro Ticker nur das erste Ereignis innerhalb von 30 Tagen."""
    prev = last_seen.get(ticker)
    return prev is not None and (T - prev) <= DEDUP_WINDOW


def bitget_symbol_candidates(ticker: str) -> list[str]:
    return [f"{ticker}USDT", f"1000{ticker}USDT", f"1000000{ticker}USDT"]


def short_return_on_margin(entry: float, highs: np.ndarray, exit_price: float, funding_short: float,
                           lev: float = LEVERAGE, stop: float | None = STOP,
                           cost_side: float = COST_SIDE,
                           funding_until_stop: float | None = None) -> tuple[float, str, int]:
    """Rendite auf die isolierte Margin eines Shorts.

    highs: Hochs je Minute ab Einstieg (chronologisch) bis Ausstieg.
    funding_short: Summe Funding aus Short-Sicht ueber die ganze Haltedauer (+ = erhalten).
    funding_until_stop: live bekanntes Funding bis zum Stop; ohne Angabe (Backtest) anteilig geschaetzt.
    Rueckgabe: (Rendite, Grund 'liq'|'stop'|'time', Index der Ausloesekerze oder -1).
    """
    rel = highs / entry - 1
    liq = 1 / lev - MAINT_MARGIN
    trig = stop if stop is not None else np.inf
    hit = np.where(rel >= min(trig, liq))[0]
    if len(hit):
        i = int(hit[0])
        if rel[i] >= liq and liq <= trig:
            return -1.0, "liq", i
        f = funding_until_stop if funding_until_stop is not None else i / max(len(highs), 1) * funding_short
        loss = -(trig + STOP_SLIPPAGE) - 2 * cost_side + f
        return max(lev * loss, -1.0), "stop", i
    gross = (entry * (1 - cost_side)) / (exit_price * (1 + cost_side)) - 1
    return max(lev * (gross + funding_short), -1.0), "time", -1
