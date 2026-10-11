#!/usr/bin/env python3
"""Historische Tick-Daten (Tardis.dev, kostenlose Monatsersten) -> Recorder-Format.

Laedt pro (Tag, Coin, Boerse) quotes + trades, verdichtet sie mit DENSELBEN Klassen
wie der Live-Recorder (BookThrottle 100 ms, TradeAggregator 1 s) und schreibt
  data_hist/<YYYY-MM-DD>/<exchange>_book_<SYMBOL>.csv.gz
  data_hist/<YYYY-MM-DD>/<exchange>_trades1s_<SYMBOL>.csv.gz
Spalten identisch zum Recorder -> ein Backtester fuer Historie und Live-Aufzeichnung.

recv_ms = Tardis local_timestamp (Empfangszeit beim Tardis-Collector), exch_ms = Boersenzeit.

  python research/hist_fetch.py --workers 4
Fortsetzbar: fertige Dateien werden uebersprungen, fehlende Coin-Tage (noch nicht gelistet) protokolliert.
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from onembot.recorder.aggregate import BookState, BookThrottle, TradeAggregator  # noqa: E402
from onembot.recorder.writer import SCHEMAS  # noqa: E402

URL = "https://datasets.tardis.dev/v1/{ex}/{ty}/{y}/{m}/{d}/{sym}.csv.gz"
EXCH = {"binance": "binance-futures", "bitget": "bitget-futures"}

# Feste Liste etablierter Coins, auf beiden Boersen seit spaetestens Ende 2024 gelistet.
# Mischung aus Majors und auf Bitget duennen Mid-Caps (dort war der 1m-Lead-Lag am staerksten).
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "AVAX", "LINK", "DOT",
         "LTC", "BCH", "NEAR", "ATOM", "FIL", "UNI", "AAVE", "APT", "ARB", "OP",
         "SUI", "TIA", "SEI", "INJ", "WLD", "ENA", "ONDO", "ZEC", "SAND", "MINA",
         "STRK", "ETC", "TRX", "MAGIC", "OGN", "BAT", "QNT", "PIXEL", "CRV", "LDO"]
SYMBOLS = [c + "USDT" for c in COINS]


def all_days() -> list[str]:
    days, y, m = [], 2024, 12
    while (y, m) <= (2026, 10):
        days.append(f"{y}-{m:02d}-01")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return days


def _download(ex: str, ty: str, day: str, sym: str) -> bytes | None:
    y, m, d = day.split("-")
    url = URL.format(ex=EXCH[ex], ty=ty, y=y, m=m, d=d, sym=sym)
    for attempt in range(5):
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=120)
            if r.status_code == 200:
                return r.content
            if r.status_code in (400, 404):
                return None
        except requests.RequestException:
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Download dauerhaft fehlgeschlagen: {url}")


def _write(path: str, kind: str, rows: list[tuple]) -> None:
    tmp = path + ".tmp"
    with gzip.open(tmp, "wt", compresslevel=6) as f:
        f.write(",".join(SCHEMAS[kind]) + "\n")
        for r in rows:
            f.write(",".join("" if v is None else str(v) for v in r) + "\n")
    os.replace(tmp, path)  # erst komplett fertig -> sichtbar (fortsetzbar ohne Halbdateien)


def condense_quotes(raw: bytes, sym: str, min_interval_ms: int = 100) -> list[tuple]:
    df = pd.read_csv(io.BytesIO(raw), compression="gzip",
                     usecols=["timestamp", "local_timestamp", "ask_amount", "ask_price", "bid_price", "bid_amount"])
    df = df.dropna()
    th = BookThrottle(min_interval_ms)
    out = []
    recv = (df.local_timestamp.values // 1000).astype(np.int64)
    exch = (df.timestamp.values // 1000).astype(np.int64)
    bid, bsz = df.bid_price.values, df.bid_amount.values
    ask, asz = df.ask_price.values, df.ask_amount.values
    for i in range(len(df)):
        t = int(recv[i])
        # entspricht dem 100-ms-Timer des Live-Recorders: faellige pending-Zustaende zuerst
        for _, st in th.due(t):
            out.append((st.recv_ms, st.exch_ms, sym, st.bid, st.bid_sz, st.ask, st.ask_sz))
        st = th.update(sym, BookState(t, int(exch[i]), float(bid[i]), float(bsz[i]), float(ask[i]), float(asz[i])))
        if st:
            out.append((st.recv_ms, st.exch_ms, sym, st.bid, st.bid_sz, st.ask, st.ask_sz))
    for _, st in th.due(2 ** 62):
        out.append((st.recv_ms, st.exch_ms, sym, st.bid, st.bid_sz, st.ask, st.ask_sz))
    return out


def condense_trades(raw: bytes, sym: str) -> list[tuple]:
    df = pd.read_csv(io.BytesIO(raw), compression="gzip", usecols=["timestamp", "side", "price", "amount"])
    df = df[df.side.isin(["buy", "sell"])]
    ag = TradeAggregator()
    ts = (df.timestamp.values // 1000).astype(np.int64)
    for t, s, p, q in zip(ts, df.side.values, df.price.values, df.amount.values):
        ag.add(sym, int(t), float(p), float(q), s == "buy")
    return ag.pop_closed(0, force=True)


def job(out_dir: str, day: str, sym: str) -> dict:
    res = {"day": day, "sym": sym, "missing": [], "rows": 0}
    ddir = os.path.join(out_dir, day)
    os.makedirs(ddir, exist_ok=True)
    for ex in EXCH:
        for ty, kind in (("quotes", "book"), ("trades", "trades1s")):
            path = os.path.join(ddir, f"{ex}_{kind}_{sym}.csv.gz")
            if os.path.exists(path) or os.path.exists(path + ".missing"):
                continue
            raw = _download(ex, ty, day, sym)
            if raw is None:
                open(path + ".missing", "w").close()
                res["missing"].append(f"{ex}/{ty}")
                continue
            rows = condense_quotes(raw, sym) if kind == "book" else condense_trades(raw, sym)
            if not rows:
                open(path + ".missing", "w").close()
                res["missing"].append(f"{ex}/{ty}(leer)")
                continue
            _write(path, kind, rows)
            res["rows"] += len(rows)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "data_hist"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--days", nargs="*", default=None)
    ap.add_argument("--symbols", nargs="*", default=None)
    args = ap.parse_args()
    days = args.days or all_days()
    syms = args.symbols or SYMBOLS
    if not days or not syms:
        sys.exit("leere Tag-/Symbolliste -- Abbruch")
    jobs = [(d, s) for d in days for s in syms]
    print(f"{len(jobs)} Coin-Tage, {args.workers} Prozesse -> {args.out}", flush=True)
    log_path = os.path.join(args.out, "fetch_log.jsonl")
    os.makedirs(args.out, exist_ok=True)
    t0 = time.time()
    with ProcessPoolExecutor(args.workers) as pool, open(log_path, "a", encoding="utf-8") as log:
        futs = {pool.submit(job, args.out, d, s): (d, s) for d, s in jobs}
        for n, f in enumerate(as_completed(futs), 1):
            d, s = futs[f]
            try:
                r = f.result()
            except Exception as ex:
                r = {"day": d, "sym": s, "error": repr(ex)}
            log.write(json.dumps(r) + "\n")
            log.flush()
            if n % 20 == 0 or "error" in r:
                el = time.time() - t0
                print(f"{n}/{len(jobs)} ({el / 60:.1f} min, Rest ~{el / n * (len(jobs) - n) / 60:.0f} min) {r}", flush=True)
    print("fertig", flush=True)


if __name__ == "__main__":
    main()
