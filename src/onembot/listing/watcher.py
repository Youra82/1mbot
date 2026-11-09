"""Dry-Run-Waechter Upbit-KRW-Listing -> Papier-Short auf Bitget (T+30 min, 72 h, 3x, Stop +15 %).

Ablauf:
  1. Upbit-Ankuendigungen pollen; neue KRW-Listings -> 'pending' mit Einstieg T+30 min
  2. Faellig: Bitget-Ticker holen, Papier-Short zum Bid (Rohpreis gespeichert)
  3. Offene Positionen: 1m-Hochs von Bitget nachladen, Stop/Liquidation ueber rules.short_return_on_margin
     (gleiche Funktion wie der Backtest), nach 72 h Ausstieg zum Ask
  4. Funding: echte Bitget-Abrechnungen waehrend der Haltedauer
Es werden KEINE echten Orders gesendet.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np

from . import rules

log = logging.getLogger("1mbot.listing")
FEE_SIDE = 0.0006


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def new_state(start_equity: float) -> dict:
    return {"version": 1, "bootstrapped": False, "seen_notice_ids": [], "last_seen_ticker": {},
            "pending": [], "open": [], "closed": [], "skipped": [], "equity": start_equity,
            "start_equity": start_equity}


class Watcher:
    def __init__(self, state: dict, upbit, bitget, settings: dict,
                 notify: Callable[[str], None] = lambda s: None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 sleep: Callable[[float], None] = time.sleep):
        self.s = state
        self.sleep = sleep
        self.upbit = upbit
        self.bitget = bitget
        self.cfg = settings
        self.notify = notify
        self.now = now
        self._contracts: set[str] = set()
        self._contracts_at: datetime | None = None

    # ---------- 1. Erkennung ----------
    def bootstrap(self) -> None:
        """Erster Start: Dubletten-Gedaechtnis aus juengsten Listings fuellen, alte Ankuendigungen als gesehen markieren."""
        if self.s["bootstrapped"]:
            return
        now = self.now()
        for page in range(1, 6):
            if page > 1:
                self.sleep(1.5)                                  # Upbit drosselt schnelle Folgeabfragen
            for n in self.upbit.notices(page):
                if n["T"] + rules.ENTRY_DELAY < now:          # zu alt zum Handeln
                    self.s["seen_notice_ids"].append(n["id"])
                    if rules.is_krw_listing(n["title"]):
                        for tk in rules.extract_tickers(n["title"]):
                            prev = self.s["last_seen_ticker"].get(tk)
                            if prev is None or _dt(prev) < n["T"]:
                                self.s["last_seen_ticker"][tk] = _iso(n["T"])
        self.s["bootstrapped"] = True
        log.info("Bootstrap: %d Ankuendigungen, %d Ticker im Dubletten-Gedaechtnis",
                 len(self.s["seen_notice_ids"]), len(self.s["last_seen_ticker"]))

    def contracts(self) -> set[str]:
        if self._contracts_at is None or self.now() - self._contracts_at > timedelta(minutes=10):
            self._contracts = self.bitget.contracts()
            self._contracts_at = self.now()
        return self._contracts

    def poll_upbit(self) -> None:
        seen = set(self.s["seen_notice_ids"])
        now = self.now()
        for n in sorted(self.upbit.notices(1), key=lambda x: x["T"]):
            if n["id"] in seen:
                continue
            self.s["seen_notice_ids"].append(n["id"])
            if not rules.is_krw_listing(n["title"]):
                continue
            latency = (now - n["T"]).total_seconds()
            for tk in rules.extract_tickers(n["title"]):
                self._on_listing(n, tk, latency)

    def _on_listing(self, n: dict, tk: str, latency: float) -> None:
        T = n["T"]
        last = {k: _dt(v) for k, v in self.s["last_seen_ticker"].items()}
        base = {"notice_id": n["id"], "ticker": tk, "T": _iso(T), "detect_latency_s": round(latency, 1)}
        if rules.is_duplicate(tk, T, last):
            return self._skip(base, "Dublette (<30 Tage)")
        self.s["last_seen_ticker"][tk] = _iso(T)
        if self.now() >= T + rules.ENTRY_DELAY:
            return self._skip(base, "zu spaet erkannt")
        sym = next((c for c in rules.bitget_symbol_candidates(tk) if c in self.contracts()), None)
        if sym is None:
            return self._skip(base, "kein Bitget-Perp")
        p = dict(base, symbol=sym, entry_at=_iso(T + rules.ENTRY_DELAY))
        self.s["pending"].append(p)
        self.notify(f"1mbot Listing erkannt: {tk} ({sym}) auf Upbit KRW\n"
                    f"Verzoegerung {latency:.0f}s | Papier-Short um {p['entry_at'][11:16]} UTC")

    def _skip(self, base: dict, why: str) -> None:
        self.s["skipped"].append(dict(base, reason=why))
        log.info("uebersprungen %s: %s", base["ticker"], why)
        self.notify(f"1mbot Listing {base['ticker']} uebersprungen: {why}")

    # ---------- 2. Einstieg ----------
    def enter_due(self) -> None:
        now = self.now()
        still = []
        for p in self.s["pending"]:
            if now < _dt(p["entry_at"]):
                still.append(p)
                continue
            try:
                t = self.bitget.ticker(p["symbol"])
            except Exception as ex:          # pro Eintrag: bei Fehler naechste Runde erneut versuchen
                log.warning("Ticker %s fehlgeschlagen: %r", p["symbol"], ex)
                still.append(p)
                continue
            used = sum(o["margin"] for o in self.s["open"])
            margin = self.s["equity"] * self.cfg["margin_fraction"]
            if used + margin > self.s["equity"] or margin * rules.LEVERAGE < 5.0:
                self._skip(p, "nicht genug freies Papier-Kapital")
                continue
            o = dict(p, entry_time=_iso(now), entry_bid=t["bid"], entry_ask=t["ask"],
                     entry_bid_notional=t["bid"] * t["bid_sz"], margin=margin,
                     notional=margin * rules.LEVERAGE, stop_price=t["bid"] * (1 + rules.STOP),
                     exit_at=_iso(now + rules.HOLD), highs=[], last_candle_ms=_ms(now) // 60_000 * 60_000)
            self.s["open"].append(o)
            self.notify(f"1mbot PAPIER-SHORT {p['ticker']} @ {t['bid']} (Spread "
                        f"{(t['ask'] / t['bid'] - 1) * 100:.2f}%)\nMargin {margin:.2f} USDT x{rules.LEVERAGE}, "
                        f"Stop {o['stop_price']:.6g}, Ausstieg {o['exit_at'][:16]} UTC")
        self.s["pending"] = still

    # ---------- 3./4. Positionen ----------
    def funding_since(self, o: dict, until: datetime) -> float:
        t0, t1 = _ms(_dt(o["entry_time"])), _ms(until)
        return float(sum(r for ts, r in self.bitget.funding_history(o["symbol"]) if t0 < ts <= t1))

    def check_open(self) -> None:
        now = self.now()
        still = []
        for o in self.s["open"]:
            try:
                closed = self._check_one(o, now)
            except Exception as ex:          # pro Position: Fehler -> Position bleibt offen, naechste Runde
                log.warning("Pruefung %s fehlgeschlagen: %r", o["symbol"], ex)
                closed = False
            if not closed:
                still.append(o)
        self.s["open"] = still

    def _check_one(self, o: dict, now: datetime) -> bool:
        candles = self.bitget.candles_1m(o["symbol"], o["last_candle_ms"])
        cur_min = _ms(now) // 60_000 * 60_000
        done = [c for c in candles if o["last_candle_ms"] <= c[0] < cur_min]
        partial = [c[1] for c in candles if c[0] >= cur_min][:1]   # laufende Minute: Stop greift sofort
        highs = np.array(o["highs"] + [c[1] for c in done] + partial, dtype=float)
        fund = self.funding_since(o, now)
        if len(highs):
            r, why, _ = rules.short_return_on_margin(o["entry_bid"], highs, float("nan"), 0.0,
                                                     funding_until_stop=fund)
            if why in ("stop", "liq"):
                self._close(o, r, why, None, fund, highs, now)
                return True
        if now >= _dt(o["exit_at"]):
            tick = self.bitget.ticker(o["symbol"])
            r, _, _ = rules.short_return_on_margin(o["entry_bid"], highs if len(highs) else np.array([o["entry_bid"]]),
                                                   tick["ask"], fund)
            self._close(o, r, "time", tick, fund, highs, now)
            return True
        # erst nach erfolgreicher Pruefung den Fortschritt uebernehmen
        o["highs"] += [c[1] for c in done]
        if done:
            o["last_candle_ms"] = done[-1][0] + 60_000
        return False

    def _close(self, o: dict, r: float, why: str, tick: dict | None, fund: float,
               highs: np.ndarray, now: datetime) -> None:
        exit_ask = tick["ask"] if tick else None
        if why == "time":
            raw = rules.LEVERAGE * (rules.linear_short(o["entry_bid"], exit_ask, FEE_SIDE) + fund)
        else:
            raw = r                          # Stop/Liquidation: gleiche konservative Annahme wie Backtest
        r, raw = max(r, -1.0), max(raw, -1.0)
        pnl = o["margin"] * r
        self.s["equity"] += pnl
        c = {k: v for k, v in o.items() if k != "highs"}
        c.update(close_time=_iso(now), reason=why, exit_ask=exit_ask, funding_short=fund,
                 ret_margin_backtest_costs=r, ret_margin_raw_fees=raw, pnl_usdt=pnl,
                 equity_after=self.s["equity"],
                 max_rise_pct=float((highs.max() / o["entry_bid"] - 1) * 100) if len(highs) else 0.0)
        self.s["closed"].append(c)
        self.notify(f"1mbot PAPIER-SHORT {o['ticker']} geschlossen ({why})\n"
                    f"Rendite auf Margin {r * 100:+.1f}% (roh {raw * 100:+.1f}%), Funding {fund * 100:+.2f}%\n"
                    f"PnL {pnl:+.2f} USDT | Papier-Konto {self.s['equity']:.2f} USDT")

    # ---------- Bericht ----------
    def summary(self) -> str:
        c = self.s["closed"]
        lines = [f"1mbot Listing-Waechter (Dry-Run) | Konto {self.s['equity']:.2f} USDT "
                 f"(Start {self.s['start_equity']:.0f})",
                 f"offen {len(self.s['open'])} | wartend {len(self.s['pending'])} | geschlossen {len(c)} | "
                 f"uebersprungen {len(self.s['skipped'])}"]
        if c:
            rr = np.array([x["ret_margin_backtest_costs"] for x in c])
            lines.append(f"Mittel {rr.mean() * 100:+.1f}% / Trade auf Margin, Median {np.median(rr) * 100:+.1f}%, "
                         f"positiv {np.mean(rr > 0):.0%} (Backtest OOS: +11,8 % / Median +3,9 %)")
        return "\n".join(lines)


def load_state(path: str, start_equity: float) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return new_state(start_equity)


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def run_forever(settings: dict, notify: Callable[[str], None]) -> None:
    from .exchange import Bitget, Upbit
    cfg = settings["listing"]
    path = cfg["state_file"]
    state = load_state(path, cfg["start_equity"])
    w = Watcher(state, Upbit(), Bitget(), cfg, notify)
    w.bootstrap()
    save_state(path, state)
    notify("1mbot Listing-Waechter gestartet (Dry-Run, keine echten Orders)\n" + w.summary())
    last_check, last_daily, fail_since, warned = 0.0, None, None, False
    while True:
        try:
            w.poll_upbit()
            fail_since, warned = None, False
        except Exception as ex:
            log.warning("Upbit-Abfrage fehlgeschlagen: %r", ex)
            fail_since = fail_since or time.time()
            if not warned and time.time() - fail_since > 600:
                notify(f"1mbot WARNUNG: Upbit seit 10 min nicht erreichbar ({ex!r})")
                warned = True
        try:
            w.enter_due()
            if time.time() - last_check >= cfg["check_seconds"]:
                w.check_open()
                last_check = time.time()
        except Exception as ex:
            log.warning("Bitget-Fehler: %r", ex)
        save_state(path, state)
        now = datetime.now(timezone.utc)
        if now.hour == cfg.get("daily_report_hour_utc", 7) and last_daily != now.date():
            last_daily = now.date()
            notify(w.summary())
        time.sleep(cfg["poll_seconds"])
