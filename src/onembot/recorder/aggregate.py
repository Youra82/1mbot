"""Verdichtung der Rohstroeme, bevor sie auf Platte gehen.

BookThrottle: Top-of-Book nur schreiben, wenn sich Bid- oder Ask-PREIS aendert,
und pro Symbol hoechstens alle `min_interval_ms`. Liegt eine Aenderung innerhalb
des Intervalls, wird der JUENGSTE Zustand als 'pending' gemerkt und mit seinem
eigenen Empfangszeitpunkt nachgeliefert (kein Lookahead: es wird nie ein Zustand
mit einem frueheren Zeitstempel geschrieben, als er tatsaechlich bekannt war).

TradeAggregator: Einzeltrades -> 1-Sekunden-Buckets nach Boersen-Zeitstempel.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BookState:
    recv_ms: int
    exch_ms: int
    bid: float
    bid_sz: float
    ask: float
    ask_sz: float


class BookThrottle:
    def __init__(self, min_interval_ms: int = 100):
        self.min_interval_ms = min_interval_ms
        self._last_emit_ms: dict[str, int] = {}
        self._last_px: dict[str, tuple[float, float]] = {}
        self._pending: dict[str, BookState] = {}

    def update(self, symbol: str, st: BookState) -> BookState | None:
        """Gibt den zu schreibenden Zustand zurueck oder None."""
        px = (st.bid, st.ask)
        if self._last_px.get(symbol) == px and symbol not in self._pending:
            return None
        last = self._last_emit_ms.get(symbol)
        if last is None or st.recv_ms - last >= self.min_interval_ms:
            return self._emit(symbol, st)
        if self._last_px.get(symbol) == px:
            # zurueck auf den zuletzt geschriebenen Preis -> nichts nachzuliefern
            self._pending.pop(symbol, None)
        else:
            self._pending[symbol] = st
        return None

    def due(self, now_ms: int) -> list[tuple[str, BookState]]:
        out = []
        for sym, st in list(self._pending.items()):
            if now_ms - self._last_emit_ms.get(sym, 0) >= self.min_interval_ms:
                out.append((sym, self._emit(sym, st)))
        return out

    def _emit(self, symbol: str, st: BookState) -> BookState:
        self._pending.pop(symbol, None)
        self._last_emit_ms[symbol] = st.recv_ms
        self._last_px[symbol] = (st.bid, st.ask)
        return st


class _Bucket:
    __slots__ = ("buy_qty", "sell_qty", "buy_usd", "sell_usd", "n",
                 "first_px", "last_px", "high_px", "low_px")

    def __init__(self, px: float):
        self.buy_qty = self.sell_qty = self.buy_usd = self.sell_usd = 0.0
        self.n = 0
        self.first_px = self.last_px = self.high_px = self.low_px = px


class TradeAggregator:
    def __init__(self, grace_seconds: int = 2):
        self.grace_seconds = grace_seconds
        self._b: dict[tuple[str, int], _Bucket] = {}

    def add(self, symbol: str, ts_ms: int, px: float, qty: float, is_buy: bool) -> None:
        key = (symbol, ts_ms // 1000)
        b = self._b.get(key)
        if b is None:
            b = self._b[key] = _Bucket(px)
        if is_buy:
            b.buy_qty += qty
            b.buy_usd += qty * px
        else:
            b.sell_qty += qty
            b.sell_usd += qty * px
        b.n += 1
        b.last_px = px
        if px > b.high_px:
            b.high_px = px
        if px < b.low_px:
            b.low_px = px

    def pop_closed(self, now_ms: int, force: bool = False) -> list[tuple]:
        """Buckets, die aelter als grace_seconds sind, als fertige Zeilen."""
        cutoff = now_ms // 1000 - self.grace_seconds
        out = []
        for key in sorted(k for k in self._b if force or k[1] < cutoff):
            b = self._b.pop(key)
            out.append((key[1], key[0], round(b.buy_qty, 8), round(b.sell_qty, 8),
                        round(b.buy_usd, 2), round(b.sell_usd, 2), b.n,
                        b.first_px, b.last_px, b.high_px, b.low_px))
        return out
