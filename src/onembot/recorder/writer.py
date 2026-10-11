"""Stuendlich rotierte gzip-CSV-Dateien.

Jeder Flush haengt ein eigenes gzip-Member an (open 'ab' -> schreiben -> schliessen).
Multi-Member-gzip lesen Python/pandas transparent; ein Absturz verliert hoechstens
den Puffer seit dem letzten Flush, beschaedigt aber keine bereits geschriebenen Daten.

Layout: <root>/<YYYY-MM-DD>/<exchange>_<kind>_<HH>.csv.gz  (Zeit = UTC)
"""
from __future__ import annotations

import gzip
import os
import time
from collections import defaultdict

SCHEMAS = {
    "book": ("recv_ms", "exch_ms", "symbol", "bid", "bid_sz", "ask", "ask_sz"),
    "trades1s": ("sec", "symbol", "buy_qty", "sell_qty", "buy_usd", "sell_usd",
                 "n", "first_px", "last_px", "high_px", "low_px"),
    "liq": ("recv_ms", "exch_ms", "symbol", "side", "qty", "price", "avg_price", "status"),
    "mark": ("recv_ms", "symbol", "mark", "index", "funding_rate", "next_funding_ms"),
    "events": ("recv_ms", "exchange", "event", "detail"),
}


def _hour_key(ts_ms: int) -> tuple[str, str]:
    t = time.gmtime(ts_ms / 1000.0)
    return time.strftime("%Y-%m-%d", t), time.strftime("%H", t)


def _fmt(v) -> str:
    if v is None:
        return ""
    s = str(v)
    # Kommas/Zeilenumbrueche in Freitext (events.detail) neutralisieren
    return s.replace(",", ";").replace("\n", " ")


class HourlyGzWriter:
    def __init__(self, root: str):
        self.root = root
        self._buf: dict[tuple, list[str]] = defaultdict(list)
        self.rows_written: dict[tuple[str, str], int] = defaultdict(int)  # (exchange, kind)
        self.paused_kinds: set[str] = set()

    def add(self, exchange: str, kind: str, ts_ms: int, row: tuple) -> None:
        if kind in self.paused_kinds:
            return
        if len(row) != len(SCHEMAS[kind]):
            raise ValueError(f"{kind}: erwartet {len(SCHEMAS[kind])} Spalten, bekam {len(row)}")
        day, hour = _hour_key(ts_ms)
        self._buf[(day, hour, exchange, kind)].append(",".join(_fmt(v) for v in row))

    def path_for(self, day: str, hour: str, exchange: str, kind: str) -> str:
        return os.path.join(self.root, day, f"{exchange}_{kind}_{hour}.csv.gz")

    def flush(self) -> int:
        buf, self._buf = self._buf, defaultdict(list)
        n = 0
        for (day, hour, exchange, kind), lines in buf.items():
            if not lines:
                continue
            path = self.path_for(day, hour, exchange, kind)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            new = not os.path.exists(path)
            payload = ""
            if new:
                payload += ",".join(SCHEMAS[kind]) + "\n"
            payload += "\n".join(lines) + "\n"
            with gzip.open(path, "ab", compresslevel=6) as f:
                f.write(payload.encode())
            self.rows_written[(exchange, kind)] += len(lines)
            n += len(lines)
        return n
