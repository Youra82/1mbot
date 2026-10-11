#!/usr/bin/env python3
"""Backtest H1 (Mid-Abweichung Binance/Bitget) und H2 (Binance-Orderflow) -- siehe PRAEREGISTRIERUNG.md.

  python research/backtest_leadlag.py --hyp h1 --phase is            # Raster auf IS, schreibt Auswahl
  python research/backtest_leadlag.py --hyp h1 --phase oos           # nur mit gespeicherter Auswahl
  python research/backtest_leadlag.py --hyp h1 --phase oos --latency 1000

Daten: data_hist/<Tag>/<exchange>_<kind>_<SYMBOL>.csv.gz (hist_fetch.py) -- gleiches Format wie der Recorder.
Alle Zeitpunkte = recv_ms (was ein Bot zu diesem Moment wissen konnte).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data_hist")
PREREG = os.path.join(HERE, "PRAEREGISTRIERUNG.md")
PREREG_SHA = "649b17372bfb5265696e38c6551a6063ccaeeb453afcbd2c3d37f22a84ff7a90"

IS_DAYS = [f"2024-12-01"] + [f"2025-{m:02d}-01" for m in range(1, 13)]
OOS_DAYS = [f"2026-{m:02d}-01" for m in range(1, 11)]

FEE_SIDE = 0.0006          # Bitget Taker VIP0
MIN_NOTIONAL = 100.0       # Top-of-Book muss >= 100 USDT haben
MAX_GAP_MS = 600_000       # 10 min ohne Bitget-Preisaenderung -> Ausfall (Nachtrag 1)
BASE_WIN_S = 600           # 10 min Basis
FLOW_NORM_S = 3600         # 60 min Normierung
FLOW_AVAIL_MS = 200        # Sekunde s ist ab (s+1)*1000+200 bekannt

GRID = {
    "h1": [(y, h) for y in (0.0015, 0.0025, 0.0040) for h in (5, 15, 30, 60)],
    "h2": [(k, h) for k in (10, 20, 40) for h in (5, 15, 30, 60)],
}


def check_prereg() -> None:
    with open(PREREG, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    if sha != PREREG_SHA:
        sys.exit(f"PRAEREGISTRIERUNG.md veraendert ({sha[:12]}…) -- Abbruch")


# ---------------- Daten ----------------
def load_book(day: str, ex: str, sym: str) -> pd.DataFrame | None:
    p = os.path.join(DATA, day, f"{ex}_book_{sym}.csv.gz")
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, usecols=["recv_ms", "bid", "bid_sz", "ask", "ask_sz"])
    df = df[(df.bid > 0) & (df.ask > 0) & (df.ask >= df.bid)].sort_values("recv_ms", kind="stable")
    return df.reset_index(drop=True)


def load_trades(day: str, ex: str, sym: str) -> pd.DataFrame | None:
    p = os.path.join(DATA, day, f"{ex}_trades1s_{sym}.csv.gz")
    if not os.path.exists(p):
        return None
    return pd.read_csv(p, usecols=["sec", "buy_usd", "sell_usd"])


def asof_idx(times: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Index der letzten Zeile mit time <= t (-1 = noch nichts bekannt)."""
    return np.searchsorted(times, t, side="right") - 1


# ---------------- Signale (Kandidaten: Zeit, Richtung, Staerke) ----------------
def signals_h1(bn: pd.DataFrame, bg: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    bn_t = bn.recv_ms.values
    bg_t = bg.recv_ms.values
    bn_mid = np.log((bn.bid.values + bn.ask.values) / 2)
    bg_mid = np.log((bg.bid.values + bg.ask.values) / 2)

    # Sekundenraster: d zum Zeitpunkt s*1000 (as-of beide Buecher)
    s0 = max(bn_t[0], bg_t[0]) // 1000 + 1
    s1 = min(bn_t[-1], bg_t[-1]) // 1000
    secs = np.arange(s0, s1 + 1)
    ib, ig = asof_idx(bn_t, secs * 1000), asof_idx(bg_t, secs * 1000)
    d_sec = bn_mid[ib] - bg_mid[ig]
    base = pd.Series(d_sec).rolling(BASE_WIN_S, min_periods=BASE_WIN_S).median().values

    # Ereignis = jedes Binance-Update; Basis aus der letzten abgeschlossenen Sekunde
    jg = asof_idx(bg_t, bn_t)
    ok = jg >= 0
    k = bn_t // 1000 - s0               # Index der Sekunde floor(t) im Raster (Sample bei s*1000 <= t)
    ok &= (k >= 0) & (k < len(secs))
    kk = np.clip(k, 0, len(secs) - 1)
    b = np.where(ok, base[kk], np.nan)
    dev = bn_mid - bg_mid[np.clip(jg, 0, None)] - b
    good = ok & ~np.isnan(dev)
    return bn_t[good], np.sign(dev[good]), np.abs(dev[good])


def signals_h2(tr: pd.DataFrame, day_start_s: int, day_end_s: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    secs = np.arange(day_start_s, day_end_s + 1)
    f = np.zeros(len(secs))
    idx = tr.sec.values - day_start_s
    m = (idx >= 0) & (idx < len(secs))
    np.add.at(f, idx[m], (tr.buy_usd.values - tr.sell_usd.values)[m])
    flow3 = pd.Series(f).rolling(3, min_periods=3).sum().values
    norm = pd.Series(np.abs(flow3)).rolling(FLOW_NORM_S, min_periods=FLOW_NORM_S).median().values
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(norm > 0, flow3 / norm, np.nan)
    good = ~np.isnan(z)
    t = (secs[good] + 1) * 1000 + FLOW_AVAIL_MS
    return t, np.sign(z[good]), np.abs(z[good])


# ---------------- Ausfuehrung (gemeinsam fuer H1/H2) ----------------
def simulate(sig_t, sig_dir, sig_mag, thr: float, hold_s: int, bg: pd.DataFrame, latency_ms: int) -> list[dict]:
    bg_t = bg.recv_ms.values
    bid, ask = bg.bid.values, bg.ask.values
    bsz, asz = bg.bid_sz.values, bg.ask_sz.values
    gaps = np.diff(bg_t)
    sel = np.where((sig_mag >= thr) & (sig_dir != 0))[0]
    trades, blocked_until, dropped = [], -1, {"gap": 0, "size": 0}
    hold = hold_s * 1000
    for i in sel:
        t = sig_t[i]
        if t < blocked_until:
            continue
        te = t + latency_ms
        tx = te + hold
        ie, ix = asof_idx(bg_t, np.array([te, tx]))
        if ie < 0 or tx > bg_t[-1]:
            continue
        # Luecke im Bitget-Strom zwischen Einstieg und Ausstieg?
        lo, hi = ie, max(ix, ie)
        nxt = min(hi + 1, len(bg_t) - 1)
        span_gaps = gaps[lo:nxt] if nxt > lo else np.array([0])
        if (span_gaps.max() if len(span_gaps) else 0) > MAX_GAP_MS:
            dropped["gap"] += 1
            blocked_until = tx + hold
            continue
        d = int(sig_dir[i])
        if d > 0:
            if ask[ie] * asz[ie] < MIN_NOTIONAL:
                dropped["size"] += 1
                continue
            gross = bid[ix] / ask[ie] - 1
        else:
            if bid[ie] * bsz[ie] < MIN_NOTIONAL:
                dropped["size"] += 1
                continue
            gross = bid[ie] / ask[ix] - 1
        trades.append({"t": int(t), "dir": d, "gross": gross, "net": gross - 2 * FEE_SIDE})
        blocked_until = tx + hold   # Position + Sperre H nach Ausstieg
    return trades, dropped


def run_coin_day(args) -> dict:
    hyp, day, sym, latency = args
    bn, bg = load_book(day, "binance", sym), load_book(day, "bitget", sym)
    if bn is None or bg is None or len(bn) < 100 or len(bg) < 100:
        return {"day": day, "sym": sym, "skip": True}
    if hyp == "h1":
        st, sd, sm = signals_h1(bn, bg)
    else:
        tr = load_trades(day, "binance", sym)
        if tr is None or tr.empty:
            return {"day": day, "sym": sym, "skip": True}
        st, sd, sm = signals_h2(tr, int(bn.recv_ms.iloc[0] // 1000), int(bn.recv_ms.iloc[-1] // 1000))
    out = {"day": day, "sym": sym, "skip": False, "res": {}}
    for thr, h in GRID[hyp]:
        trades, dropped = simulate(st, sd, sm, thr, h, bg, latency)
        out["res"][f"{thr}|{h}"] = {"net": [x["net"] for x in trades], "gross": [x["gross"] for x in trades],
                                     "dropped": dropped}
    return out


# ---------------- Statistik ----------------
def cluster_t(groups: list[np.ndarray]) -> tuple[float, float, int]:
    allx = np.concatenate([g for g in groups if len(g)]) if any(len(g) for g in groups) else np.array([])
    n = len(allx)
    if n < 2:
        return float("nan"), float("nan"), n
    mu = allx.mean()
    se = np.sqrt(sum(((g - mu).sum()) ** 2 for g in groups if len(g))) / n
    return mu, (mu / se if se > 0 else float("nan")), n


def summarize(results: list[dict], key: str, days: list[str]) -> dict:
    groups, per_day, per_sym = [], {d: 0.0 for d in days}, {}
    gross_all, dropped = [], {"gap": 0, "size": 0}
    for r in results:
        if r.get("skip"):
            continue
        x = r["res"][key]
        net = np.array(x["net"])
        groups.append(net)
        gross_all += x["gross"]
        per_day[r["day"]] += net.sum()
        per_sym[r["sym"]] = per_sym.get(r["sym"], 0.0) + net.sum()
        for k2 in dropped:
            dropped[k2] += x["dropped"][k2]
    mu, t, n = cluster_t(groups)
    days_pos = sum(1 for v in per_day.values() if v > 0)
    out = {"n": n, "trades_per_day": n / len(days), "mean_net_pct": mu * 100, "t_cluster": t,
           "mean_gross_pct": (np.mean(gross_all) * 100) if gross_all else float("nan"),
           "days_positive": f"{days_pos}/{len(days)}", "dropped": dropped,
           "per_day_sum_pct": {d: round(v * 100, 2) for d, v in per_day.items()}}
    if per_sym:
        best = max(per_sym, key=per_sym.get)
        rest = [g for r, g in zip([r for r in results if not r.get("skip")], groups) if r["sym"] != best]
        out["best_coin"] = best
        out["mean_net_pct_without_best"] = cluster_t(rest)[0] * 100
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hyp", choices=["h1", "h2"], required=True)
    ap.add_argument("--phase", choices=["is", "oos"], required=True)
    ap.add_argument("--latency", type=int, default=500)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    check_prereg()

    sel_path = os.path.join(HERE, f"selection_{args.hyp}.json")
    if args.phase == "oos" and not os.path.exists(sel_path):
        sys.exit("OOS gesperrt: erst IS laufen lassen (schreibt selection_*.json)")
    days = IS_DAYS if args.phase == "is" else OOS_DAYS
    syms = sorted({os.path.basename(p).split("_")[-1][:-7]
                   for p in glob.glob(os.path.join(DATA, "*", "bitget_book_*.csv.gz"))})
    jobs = [(args.hyp, d, s, args.latency) for d in days for s in syms]
    with ProcessPoolExecutor(args.workers) as pool:
        results = list(pool.map(run_coin_day, jobs, chunksize=4))
    used = sum(1 for r in results if not r.get("skip"))
    print(f"{args.hyp.upper()} {args.phase.upper()} | Latenz {args.latency} ms | {used} Coin-Tage mit Daten "
          f"({len(days)} Tage x {len(syms)} Coins)")

    if args.phase == "is":
        table = []
        for thr, h in GRID[args.hyp]:
            s = summarize(results, f"{thr}|{h}", days)
            table.append((thr, h, s))
            print(f"  thr={thr:<7} H={h:>2}s  n={s['n']:>6}  /Tag={s['trades_per_day']:>7.1f}  "
                  f"brutto={s['mean_gross_pct']:+.4f}%  netto={s['mean_net_pct']:+.4f}%  t={s['t_cluster']:+.2f}  "
                  f"Tage+ {s['days_positive']}")
        cand = [x for x in table if x[2]["n"] >= 200]
        if not cand:
            sys.exit("keine Variante mit n >= 200 -- H nicht testbar")
        thr, h, s = max(cand, key=lambda x: x[2]["mean_net_pct"])
        with open(sel_path, "w", encoding="utf-8") as f:
            json.dump({"thr": thr, "hold_s": h, "is_summary": s}, f, indent=2)
        print(f"Auswahl (IS): thr={thr} H={h}s -> {sel_path}")
    else:
        with open(sel_path, encoding="utf-8") as f:
            sel = json.load(f)
        key = f"{sel['thr']}|{sel['hold_s']}"
        s = summarize(results, key, days)
        print(json.dumps(s, indent=2))
        out = os.path.join(HERE, f"oos_{args.hyp}_L{args.latency}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"selection": sel, "oos": s}, f, indent=2)


if __name__ == "__main__":
    main()
