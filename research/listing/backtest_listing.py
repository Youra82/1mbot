#!/usr/bin/env python3
"""Backtest L1 (Long-Fortsetzung) / L2 (Short-Umkehr) nach Upbit-KRW-Listing -- PRAEREGISTRIERUNG.md.

  python backtest_listing.py --phase is
  python backtest_listing.py --phase oos
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PREREG_SHA = "6774eaf3f1826255cc77c11b6a640b2a1823cd721d56ca3803dc639b3f99764c"
SPLIT = pd.Timestamp("2025-07-01", tz="UTC")
COST_SIDE = 0.0006 + 0.0020
T_CRIT = 2.24
GRID = {
    "L1": [{"D": d, "H": h} for d in (30, 60, 180) for h in (300, 1800, 14400)],
    "L2": [{"E": e, "H": h} for e in (30, 120) for h in (1440, 4320)],   # Minuten
}


def check_prereg():
    sha = hashlib.sha256(open(os.path.join(HERE, "PRAEREGISTRIERUNG.md"), "rb").read()).hexdigest()
    if sha != PREREG_SHA:
        sys.exit("PRAEREGISTRIERUNG.md veraendert -- Abbruch")


def load_events() -> pd.DataFrame:
    ev = pd.read_csv(os.path.join(HERE, "events.csv"))
    ev = ev[ev.status == "ok"].copy()
    ev["T"] = pd.to_datetime(ev["T"], utc=True)
    ev["t0"] = ev["T"].astype("int64") // 10**6
    ev["tag"] = ev.notice_id.astype(str) + "_" + ev.ticker
    return ev.reset_index(drop=True)


def vwap(tr: pd.DataFrame, a: int, b: int) -> float:
    w = tr[(tr.transact_time >= a) & (tr.transact_time <= b)]
    if w.empty:
        return float("nan")
    return float((w.price * w.quantity).sum() / w.quantity.sum())


def last_price(tr: pd.DataFrame, t: int) -> float:
    w = tr[tr.transact_time <= t]
    return float(w.price.iloc[-1]) if len(w) else float("nan")


def l1_net(tr, t0, p) -> float:
    e = vwap(tr, t0 + p["D"] * 1000, t0 + p["D"] * 1000 + 10_000)
    x = vwap(tr, t0 + (p["D"] + p["H"]) * 1000, t0 + (p["D"] + p["H"]) * 1000 + 10_000)
    if np.isnan(e) or np.isnan(x):
        return float("nan")
    return (x * (1 - COST_SIDE)) / (e * (1 + COST_SIDE)) - 1


def l2_net(k, t0, p) -> float:
    m0 = (t0 // 60_000) * 60_000
    te, tx = m0 + p["E"] * 60_000, m0 + (p["E"] + p["H"]) * 60_000
    oe = k.loc[k.open_time == te, "open"]
    ox = k.loc[k.open_time == tx, "open"]
    if oe.empty or ox.empty:
        return float("nan")
    e, x = float(oe.iloc[0]), float(ox.iloc[0])
    return (e * (1 - COST_SIDE)) / (x * (1 + COST_SIDE)) - 1      # Short


def evaluate(ev: pd.DataFrame, hyp: str, p: dict) -> pd.DataFrame:
    rows = []
    for _, r in ev.iterrows():
        base = os.path.join(HERE, "ev", r.tag)
        if hyp == "L1":
            tr = pd.read_parquet(base + "_trades.parquet")
            net = l1_net(tr, r.t0, p)
        else:
            k = pd.read_parquet(base + "_k1m.parquet")
            net = l2_net(k, r.t0, p)
        rows.append({"tag": r.tag, "T": r["T"], "cluster": r.t0 // 60_000, "net": net})
    return pd.DataFrame(rows).dropna(subset=["net"])


def stat(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n_cluster": 0}
    c = df.groupby("cluster").net.mean()
    n = len(c)
    t = c.mean() / (c.std(ddof=1) / np.sqrt(n)) if n > 2 and c.std() > 0 else float("nan")
    best = df.net.idxmax()
    return {"n_events": len(df), "n_cluster": n, "mean_net_pct": c.mean() * 100,
            "median_net_pct": float(c.median() * 100), "t": float(t),
            "share_pos": float((c > 0).mean()),
            "mean_without_best_pct": float(df.drop(index=best).groupby("cluster").net.mean().mean() * 100)}


def diagnostics(ev: pd.DataFrame) -> dict:
    """Vorlauf T-5min -> T und Sprungprofil (Mid aus letztem Trade), ohne Kosten."""
    pre, prof = [], {s: [] for s in (10, 30, 60, 180, 600)}
    for _, r in ev.iterrows():
        tr = pd.read_parquet(os.path.join(HERE, "ev", r.tag + "_trades.parquet"))
        p_m5, p0 = last_price(tr, r.t0 - 300_000), last_price(tr, r.t0)
        if np.isnan(p_m5) or np.isnan(p0):
            continue
        pre.append(p0 / p_m5 - 1)
        for s in prof:
            ps = last_price(tr, r.t0 + s * 1000)
            prof[s].append(ps / p0 - 1)
    return {"n": len(pre), "vorlauf_T-5min_bis_T_mean_pct": float(np.mean(pre) * 100),
            "vorlauf_median_pct": float(np.median(pre) * 100),
            **{f"T_bis_T+{s}s_mean_pct": float(np.mean(v) * 100) for s, v in prof.items()},
            **{f"T_bis_T+{s}s_median_pct": float(np.median(v) * 100) for s, v in prof.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["is", "oos"], required=True)
    args = ap.parse_args()
    check_prereg()
    ev = load_events()
    part = ev[ev["T"] < SPLIT] if args.phase == "is" else ev[ev["T"] >= SPLIT]
    sel_path = os.path.join(HERE, "selection.json")
    print(f"{args.phase.upper()}: {len(part)} Ereignisse")
    print("Diagnose:", json.dumps(diagnostics(part), indent=1))
    if args.phase == "is":
        sel = {}
        for hyp, grid in GRID.items():
            best = None
            for p in grid:
                s = stat(evaluate(part, hyp, p))
                print(f"{hyp} {p}: {s}")
                if s.get("n_cluster", 0) >= 30 and not np.isnan(s["t"]) and (best is None or s["t"] > best[1]["t"]):
                    best = (p, s)
            if best:
                sel[hyp] = {"params": best[0], "is": best[1]}
        json.dump(sel, open(sel_path, "w"), indent=2)
        print("Auswahl:", {h: v["params"] for h, v in sel.items()})
    else:
        if not os.path.exists(sel_path):
            sys.exit("OOS gesperrt")
        sel = json.load(open(sel_path))
        out = {}
        for hyp, v in sel.items():
            s = stat(evaluate(part, hyp, v["params"]))
            ok = (s.get("n_cluster", 0) >= 30 and s["t"] >= T_CRIT and s["mean_net_pct"] > 0
                  and s["median_net_pct"] > 0 and s["mean_without_best_pct"] > 0)
            out[hyp] = {"params": v["params"], "oos": s, "BESTANDEN": bool(ok)}
            print(f"==> {hyp} {v['params']}: {s}  {'BESTANDEN' if ok else 'nicht bestanden'}")
        json.dump(out, open(os.path.join(HERE, "oos_result.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
