"""Recorder-Hauptprozess: alle Streams + Flush/Status/Mark-Schleifen."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import statistics
import time
from collections import defaultdict, deque

import aiohttp

from . import telegram
from .aggregate import BookState, BookThrottle, TradeAggregator
from .streams import (binance_stream_groups, bitget_args, chunks, now_ms,
                      run_binance, run_bitget)
from .universe import load_or_build
from .writer import HourlyGzWriter

log = logging.getLogger("1mbot.recorder")

BINANCE_PREMIUM = "https://fapi.binance.com/fapi/v1/premiumIndex"
BINANCE_TIME = "https://fapi.binance.com/fapi/v1/time"
EXCHANGES = ("binance", "bitget")


class Recorder:
    def __init__(self, settings: dict, symbols: list[str], tg_creds=None):
        rc = settings["recorder"]
        self.settings = settings
        self.rc = rc
        self.symbols = symbols
        self.data_dir = settings["data_dir"]
        self.writer = HourlyGzWriter(self.data_dir)
        self.throttle = {ex: BookThrottle(rc["book_min_interval_ms"]) for ex in EXCHANGES}
        self.trades = {ex: TradeAggregator() for ex in EXCHANGES}
        self.tg = tg_creds
        self.started_ms = now_ms()
        self.msgs = defaultdict(int)
        self.last_recv = {ex: 0 for ex in EXCHANGES}
        self.last_book = {ex: {} for ex in EXCHANGES}   # symbol -> recv_ms
        self.lat = {ex: deque(maxlen=5000) for ex in EXCHANGES}
        self.reconnects = defaultdict(int)
        self.clock_offset_ms: int | None = None
        self.stalled: set[str] = set()
        self.stop = asyncio.Event()

    # ---------- Callbacks aus den Streams ----------
    def on_events(self, ex: str, recv: int, events: list) -> None:
        self.msgs[ex] += 1
        self.last_recv[ex] = recv
        for ev in events:
            kind = ev[0]
            if kind == "book":
                _, sym, exch_ms, bid, bsz, ask, asz = ev
                self.lat[ex].append(recv - exch_ms)
                self.last_book[ex][sym] = recv
                out = self.throttle[ex].update(sym, BookState(recv, exch_ms, bid, bsz, ask, asz))
                if out:
                    self._write_book(ex, sym, out)
            elif kind == "trade":
                _, sym, exch_ms, px, qty, is_buy = ev
                self.trades[ex].add(sym, exch_ms, px, qty, is_buy)
            elif kind == "liq":
                _, sym, exch_ms, side, qty, price, avg, status = ev
                self.writer.add(ex, "liq", recv, (recv, exch_ms, sym, side, qty, price, avg, status))

    def on_conn(self, ex: str, event: str, detail: str) -> None:
        if event in ("disconnect", "error"):
            self.reconnects[ex] += 1
            log.warning("%s %s %s", ex, event, detail)
        else:
            log.info("%s %s %s", ex, event, detail)
        self.writer.add(ex, "events", now_ms(), (now_ms(), ex, event, detail[:300]))

    def _write_book(self, ex: str, sym: str, st: BookState) -> None:
        self.writer.add(ex, "book", st.recv_ms,
                        (st.recv_ms, st.exch_ms, sym, st.bid, st.bid_sz, st.ask, st.ask_sz))

    # ---------- periodische Schleifen ----------
    async def flush_loop(self) -> None:
        last_flush = time.monotonic()
        while not self.stop.is_set():
            await asyncio.sleep(0.1)
            t = now_ms()
            for ex in EXCHANGES:
                for sym, st in self.throttle[ex].due(t):
                    self._write_book(ex, sym, st)
            if time.monotonic() - last_flush >= 1.0 or self.stop.is_set():
                for ex in EXCHANGES:
                    for row in self.trades[ex].pop_closed(t):
                        self.writer.add(ex, "trades1s", row[0] * 1000, row)
            if time.monotonic() - last_flush >= self.rc["flush_seconds"]:
                last_flush = time.monotonic()
                self._check_disk()
                await asyncio.to_thread(self.writer.flush)
        await self.final_flush()

    async def final_flush(self) -> None:
        t = now_ms()
        for ex in EXCHANGES:
            for row in self.trades[ex].pop_closed(t, force=True):
                self.writer.add(ex, "trades1s", row[0] * 1000, row)
        await asyncio.to_thread(self.writer.flush)

    def _check_disk(self) -> None:
        free_gb = shutil.disk_usage(self.data_dir).free / 1e9
        if free_gb < self.rc["min_free_gb"]:
            if "book" not in self.writer.paused_kinds:
                self.writer.paused_kinds |= {"book", "trades1s"}
                self._notify(f"1mbot WARNUNG: nur {free_gb:.1f} GB frei -- Book/Trade-Aufzeichnung pausiert")
        elif self.writer.paused_kinds:
            self.writer.paused_kinds.clear()
            self._notify(f"1mbot: wieder {free_gb:.1f} GB frei -- Aufzeichnung laeuft weiter")

    async def mark_loop(self, session: aiohttp.ClientSession) -> None:
        wanted = set(self.symbols)
        while not self.stop.is_set():
            try:
                t0 = now_ms()
                async with session.get(BINANCE_TIME, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    srv = (await r.json())["serverTime"]
                t1 = now_ms()
                self.clock_offset_ms = int(srv - (t0 + t1) / 2)
                self.writer.add("binance", "events", t1,
                                (t1, "binance", "clock_offset_ms", f"{self.clock_offset_ms} rtt={t1 - t0}"))
                async with session.get(BINANCE_PREMIUM, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    data = await r.json()
                recv = now_ms()
                for d in data:
                    if d["symbol"] in wanted:
                        self.writer.add("binance", "mark", recv,
                                        (recv, d["symbol"], d["markPrice"], d["indexPrice"],
                                         d["lastFundingRate"], d["nextFundingTime"]))
            except Exception as ex:
                self.on_conn("binance", "mark_error", repr(ex))
            await self._sleep(self.rc["mark_poll_seconds"])

    async def status_loop(self) -> None:
        last_daily = None
        while not self.stop.is_set():
            await self._sleep(self.rc["status_seconds"])
            st = self.status()
            path = os.path.join(self.data_dir, "status.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, indent=2)
            os.replace(tmp, path)

            for ex in EXCHANGES:
                age = st["exchanges"][ex]["last_msg_age_s"]
                if age is not None and age > self.rc["stall_alert_seconds"] and ex not in self.stalled:
                    self.stalled.add(ex)
                    self._notify(f"1mbot WARNUNG: {ex} seit {age:.0f}s keine Daten")
                elif age is not None and age <= self.rc["stall_alert_seconds"] and ex in self.stalled:
                    self.stalled.discard(ex)
                    self._notify(f"1mbot: {ex} liefert wieder Daten")

            h = time.gmtime()
            day = time.strftime("%Y-%m-%d", h)
            if h.tm_hour == self.settings["telegram"].get("daily_report_hour_utc", 7) and last_daily != day:
                last_daily = day
                self._notify(self.daily_text(st))

    async def _sleep(self, s: float) -> None:
        try:
            await asyncio.wait_for(self.stop.wait(), timeout=s)
        except asyncio.TimeoutError:
            pass

    def _notify(self, text: str) -> None:
        log.info("NOTIFY %s", text)
        if self.settings["telegram"].get("enabled", True) and self.tg:
            asyncio.get_running_loop().run_in_executor(None, telegram.send, self.tg, text)

    # ---------- Status ----------
    def status(self) -> dict:
        t = now_ms()
        exs = {}
        for ex in EXCHANGES:
            # Rohlatenz = lokale Empfangszeit - Boersenzeit; um den gemessenen Uhrfehler
            # korrigieren, sonst zeigt eine falsch gehende Uhr Sekunden statt Millisekunden
            off = self.clock_offset_ms or 0
            lat = [v + off for v in self.lat[ex]]
            fresh = sum(1 for v in self.last_book[ex].values() if t - v < 300_000)
            exs[ex] = {
                "msgs": self.msgs[ex],
                "last_msg_age_s": round((t - self.last_recv[ex]) / 1000, 1) if self.last_recv[ex] else None,
                "latency_ms_median": round(statistics.median(lat)) if lat else None,
                "latency_ms_p90": round(sorted(lat)[int(len(lat) * 0.9)]) if lat else None,
                "symbols_with_book_5min": fresh,
                "reconnects": self.reconnects[ex],
                "rows": {k: v for (e, k), v in self.writer.rows_written.items() if e == ex},
            }
        return {
            "updated_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t / 1000)),
            "uptime_h": round((t - self.started_ms) / 3_600_000, 2),
            "n_symbols": len(self.symbols),
            "clock_offset_ms_vs_binance": self.clock_offset_ms,
            "disk_free_gb": round(shutil.disk_usage(self.data_dir).free / 1e9, 1),
            "data_size_gb": round(_dir_size(self.data_dir) / 1e9, 2),
            "paused": sorted(self.writer.paused_kinds),
            "exchanges": exs,
        }

    def daily_text(self, st: dict) -> str:
        lines = [f"1mbot Recorder -- Tagesbericht {st['updated_utc']} UTC",
                 f"Laufzeit {st['uptime_h']:.1f} h | {st['n_symbols']} Coins | "
                 f"Daten {st['data_size_gb']} GB | frei {st['disk_free_gb']} GB"]
        for ex, e in st["exchanges"].items():
            lines.append(f"{ex}: Latenz Median {e['latency_ms_median']} ms (p90 {e['latency_ms_p90']}), "
                         f"Coins aktiv {e['symbols_with_book_5min']}, Reconnects {e['reconnects']}")
        return "\n".join(lines)


def _dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


async def run(settings: dict, duration_s: float | None = None) -> Recorder:
    data_dir = settings["data_dir"]
    os.makedirs(data_dir, exist_ok=True)
    symbols = load_or_build(data_dir, settings["universe"])
    rec = Recorder(settings, symbols, telegram.load_telegram())
    rc = settings["recorder"]
    log.info("Universum (%d): %s", len(symbols), ", ".join(symbols))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, rec.stop.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: Ctrl+C kommt als KeyboardInterrupt

    async with aiohttp.ClientSession() as session:
        tasks = []
        for group, names in binance_stream_groups(symbols).items():
            for i, part in enumerate(chunks(names, rc["binance_streams_per_conn"])):
                tasks.append(run_binance(session, group, part, f"bn-{group}{i}",
                                         rec.on_events, rec.on_conn, rec.stop))
        for i, part in enumerate(chunks(bitget_args(symbols), rc["bitget_channels_per_conn"])):
            tasks.append(run_bitget(session, part, f"bg{i}", rec.on_events, rec.on_conn, rec.stop))
        tasks += [rec.mark_loop(session), rec.status_loop()]
        running = [asyncio.create_task(t) for t in tasks]
        flusher = asyncio.create_task(rec.flush_loop())
        rec._notify(f"1mbot Recorder gestartet: {len(symbols)} Coins, Binance+Bitget")
        try:
            if duration_s:
                await rec._sleep(duration_s)
                rec.stop.set()
            else:
                await rec.stop.wait()
        finally:
            rec.stop.set()
            for t in running:
                t.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            await flusher
    return rec
