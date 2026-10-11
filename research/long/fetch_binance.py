#!/usr/bin/env python3
"""Binance-USDT-M-Langzeitdaten (data.binance.vision) -> data_long/<dataset>/<SYMBOL>.parquet

Datasets: klines 5m (inkl. Taker-Kaufvolumen), premiumIndexKlines 5m, fundingRate, metrics 5m
(Open Interest, Long/Short-Ratios, Taker-Ratio). Zeitraum 2022-01 .. 2026-09.
Fortsetzbar (fertige Parquets werden uebersprungen). Fehlende Monate/Tage = Coin noch nicht gelistet.
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OUT = os.path.join(ROOT, "data_long")
B = "https://data.binance.vision/data/futures/um"

COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "BNB", "AVAX", "LINK", "DOT",
         "LTC", "BCH", "NEAR", "ATOM", "FIL", "UNI", "AAVE", "APT", "ARB", "OP",
         "SUI", "TIA", "SEI", "INJ", "WLD", "ENA", "ONDO", "ZEC", "SAND", "MINA",
         "STRK", "ETC", "TRX", "MAGIC", "OGN", "BAT", "QNT", "PIXEL", "CRV", "LDO"]
SYMBOLS = [c + "USDT" for c in COINS]
MONTHS = [f"{y}-{m:02d}" for y in range(2022, 2027) for m in range(1, 13) if f"{y}-{m:02d}" <= "2026-09"]
KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
         "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]

S = requests.Session()


def get_zip_csv(url: str, names=None) -> pd.DataFrame | None:
    for attempt in range(5):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                raw = z.read(z.namelist()[0])
                first = raw[:200].decode(errors="ignore")
                has_header = not first[:1].isdigit()
                return pd.read_csv(io.BytesIO(raw), header=0 if has_header else None,
                                   names=None if has_header else names)
        except Exception:
            pass
        time.sleep(2 ** attempt)
    raise RuntimeError(f"Download fehlgeschlagen: {url}")


def fetch_monthly(sym: str, kind: str) -> pd.DataFrame | None:
    frames = []
    for m in MONTHS:
        if kind == "klines":
            url, names = f"{B}/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip", KCOLS
        elif kind == "premium":
            url, names = f"{B}/monthly/premiumIndexKlines/{sym}/5m/{sym}-5m-{m}.zip", KCOLS
        else:
            url, names = f"{B}/monthly/fundingRate/{sym}/{sym}-fundingRate-{m}.zip", None
        df = get_zip_csv(url, names)
        if df is not None and len(df):
            frames.append(df)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    if kind in ("klines", "premium"):
        df = df[KCOLS[:11]].copy()
        df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce")
        df = df.dropna(subset=["open_time"])
        for c in KCOLS[1:11]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["open_time"] = df["open_time"].astype("int64")
        df = df.drop_duplicates("open_time").sort_values("open_time")
    else:
        df.columns = [c.strip() for c in df.columns]
        df = df.rename(columns={"calc_time": "time", "last_funding_rate": "funding_rate"})
        df = df[["time", "funding_rate"]].drop_duplicates("time").sort_values("time")
    return df.reset_index(drop=True)


def fetch_metrics(sym: str) -> pd.DataFrame | None:
    d, end = date(2022, 1, 1), date(2026, 9, 30)
    days = []
    while d <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    with ThreadPoolExecutor(8) as ex:
        frames = list(ex.map(lambda dd: get_zip_csv(f"{B}/daily/metrics/{sym}/{sym}-metrics-{dd}.zip"), days))
    frames = [f for f in frames if f is not None and len(f)]
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["create_time"]).astype("int64") // 10**6
    df = df.drop(columns=["create_time", "symbol"]).drop_duplicates("time").sort_values("time")
    return df.reset_index(drop=True)


def job(sym: str, kind: str) -> str:
    path = os.path.join(OUT, kind, f"{sym}.parquet")
    if os.path.exists(path):
        return f"{kind} {sym} schon da"
    df = fetch_metrics(sym) if kind == "metrics" else fetch_monthly(sym, kind)
    if df is None:
        return f"{kind} {sym} KEINE DATEN"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_parquet(path + ".tmp", index=False)
    os.replace(path + ".tmp", path)
    return f"{kind} {sym} {len(df)} Zeilen"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kinds", nargs="*", default=["klines", "funding", "premium", "metrics"])
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    jobs = [(s, k) for k in args.kinds for s in SYMBOLS]
    if not jobs:
        sys.exit("keine Jobs")
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as ex:
        for i, msg in enumerate(ex.map(lambda a: job(*a), jobs), 1):
            print(f"[{i}/{len(jobs)} {(time.time() - t0) / 60:.1f} min] {msg}", flush=True)


if __name__ == "__main__":
    main()
