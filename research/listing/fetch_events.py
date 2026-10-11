#!/usr/bin/env python3
"""Ereignisse aus upbit_notices.json bauen und je Ereignis Binance-Perp-Daten laden.

Ausgabe:
  events.csv                      alle Kandidaten inkl. Grund fuer Verwerfen
  ev/<id>_<SYM>_trades.parquet    aggTrades in [T-1h, T+4h+10min]
  ev/<id>_<SYM>_k1m.parquet       1m-Klines Tag(T-1h) .. Tag(T+3d+3h)
Es werden hier KEINE Renditen berechnet.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), "src"))
from onembot.listing import rules  # noqa: E402  -- gleiche Regeln wie der Live-Waechter
B = "https://data.binance.vision/data/futures/um/daily"
S = requests.Session()
AGG_COLS = ["agg_trade_id", "price", "quantity", "first_trade_id", "last_trade_id", "transact_time", "is_buyer_maker"]
K_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
          "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def build_events() -> pd.DataFrame:
    notices = json.load(open(os.path.join(HERE, "upbit_notices.json"), encoding="utf-8"))
    rows = []
    for n in notices:
        t = n["title"]
        if not rules.is_krw_listing(t):
            continue
        T = datetime.fromisoformat(n["first_listed_at"]).astimezone(timezone.utc)
        for tk in rules.extract_tickers(t):
            rows.append({"notice_id": n["id"], "T": T, "ticker": tk})
    ev = pd.DataFrame(rows).sort_values("T").reset_index(drop=True)
    # Dublette: pro Ticker nur das erste Ereignis innerhalb von 30 Tagen
    keep, last = [], {}
    for _, r in ev.iterrows():
        keep.append(not rules.is_duplicate(r.ticker, r["T"], last))
        if keep[-1]:
            last[r.ticker] = r["T"]
    ev["dup"] = ~pd.Series(keep)
    return ev


def get_zip(url: str, names) -> pd.DataFrame | None:
    for a in range(6):
        try:
            r = S.get(url, timeout=120)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                raw = z.read(z.namelist()[0])
                header = not raw[:1].isdigit()
                return pd.read_csv(io.BytesIO(raw), header=0 if header else None, names=None if header else names)
        except Exception:
            pass
        time.sleep(2 ** a)
    raise RuntimeError(url)


def days_between(a: datetime, b: datetime) -> list[str]:
    d, out = a.date(), []
    while d <= b.date():
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def fetch_one(r: dict) -> dict:
    T = r["T"]
    res = dict(r)
    tag = f"{r['notice_id']}_{r['ticker']}"
    kpath = os.path.join(HERE, "ev", f"{tag}_k1m.parquet")
    tpath = os.path.join(HERE, "ev", f"{tag}_trades.parquet")
    if os.path.exists(kpath) and os.path.exists(tpath):
        res["status"] = "ok"
        res["symbol"] = open(kpath + ".sym").read()
        return res
    for sym in (f"{r['ticker']}USDT", f"1000{r['ticker']}USDT"):
        kd = [get_zip(f"{B}/klines/{sym}/1m/{sym}-1m-{d}.zip", K_COLS)
              for d in days_between(T - timedelta(hours=1), T + timedelta(days=3, hours=3))]
        kd = [k for k in kd if k is not None]
        if not kd:
            continue
        k = pd.concat(kd, ignore_index=True)
        k["open_time"] = pd.to_numeric(k.open_time, errors="coerce")
        k = k.dropna(subset=["open_time"])
        t0 = int(T.timestamp() * 1000)
        pre = k[(k.open_time >= t0 - 3_600_000) & (k.open_time < t0)]
        if pd.to_numeric(pre["count"], errors="coerce").sum() < 20:
            res["status"] = "kein_handel_vorher"
            res["symbol"] = sym
            return res
        trades = []
        for d in days_between(T - timedelta(hours=1), T + timedelta(hours=4, minutes=10)):
            a = get_zip(f"{B}/aggTrades/{sym}/{sym}-aggTrades-{d}.zip", AGG_COLS)
            if a is not None:
                a = a[["transact_time", "price", "quantity"]].apply(pd.to_numeric, errors="coerce").dropna()
                a = a[(a.transact_time >= t0 - 3_600_000) & (a.transact_time <= t0 + 4 * 3_600_000 + 600_000)]
                trades.append(a)
        if not trades:
            res["status"] = "keine_aggtrades"
            return res
        os.makedirs(os.path.join(HERE, "ev"), exist_ok=True)
        pd.concat(trades).sort_values("transact_time").to_parquet(tpath, index=False)
        k[K_COLS[:11]].apply(pd.to_numeric, errors="coerce").to_parquet(kpath, index=False)
        open(kpath + ".sym", "w").write(sym)
        res["status"], res["symbol"] = "ok", sym
        return res
    res["status"] = "kein_binance_perp"
    return res


def main() -> None:
    ev = build_events()
    todo = [r for r in ev.to_dict("records") if not r["dup"]]
    print(f"{len(ev)} Ticker-Ereignisse, {len(todo)} ohne Dublette", flush=True)
    out = []
    with ThreadPoolExecutor(4) as ex:
        for i, r in enumerate(ex.map(fetch_one, todo), 1):
            out.append(r)
            if i % 10 == 0:
                print(f"{i}/{len(todo)}", flush=True)
    df = pd.DataFrame(out)
    df.to_csv(os.path.join(HERE, "events.csv"), index=False)
    print(df.status.value_counts().to_string())


if __name__ == "__main__":
    main()
