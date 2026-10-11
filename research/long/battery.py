#!/usr/bin/env python3
"""Ideen-Batterie A..I -- siehe PRAEREGISTRIERUNG.md (gleicher Ordner).

  python battery.py --phase is            # Raster aller Hypothesen auf IS, schreibt selection.json
  python battery.py --phase oos           # nur die ausgewaehlte Variante je Hypothese auf OOS
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DATA = os.environ.get("BATTERY_DATA", os.path.join(ROOT, "data_long"))
PREREG_SHA = "f23666403259f3b6bdd4f8a63ddba4ce83864c223ad01ab5d277079d50cf7c7c"

BAR = 300_000                      # 5 min in ms
COST = 0.002                       # 0,20 % hin und zurueck
IS_END = pd.Timestamp("2024-07-01", tz="UTC").value // 10**6
OOS_END = pd.Timestamp("2026-10-01", tz="UTC").value // 10**6
START = pd.Timestamp("2022-01-01", tz="UTC").value // 10**6
T_CRIT = 2.54

GRIDS = {
    "A": [{"L": L, "H": H} for L in (12, 48) for H in (12, 48)],
    "B": [{"z": z, "y": y, "H": H} for z in (3, 4) for y in (0.01, 0.02) for H in (12, 48)],
    "C": [{"thr": t, "w": w} for t in (0.0003, 0.0008) for w in (6, 12)],
    "D": [{"k": k, "H": H} for k in (3, 4) for H in (12, 48, 288)],
    "E": [{"q": q, "H": H} for q in (0.99, 0.995) for H in (12, 48)],
    "F": [{"k": k, "H": H} for k in (2, 3) for H in (96, 288)],
    "G": [{"N": N, "ls": ls} for N in (20, 55) for ls in (False, True)],
    "H": [{"y": y, "z": 2, "H": H} for y in (0.03, 0.06) for H in (12, 48)],
    "I": [{"k": k, "H": H} for k in (5, 8) for H in (3, 12)],
}


def check_prereg() -> None:
    with open(os.path.join(HERE, "PRAEREGISTRIERUNG.md"), "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    if sha != PREREG_SHA:
        sys.exit(f"PRAEREGISTRIERUNG.md veraendert ({sha[:12]}) -- Abbruch")


def symbols() -> list[str]:
    d = os.path.join(DATA, "klines")
    return sorted(f[:-8] for f in os.listdir(d) if f.endswith(".parquet"))


# ---------------- Laden: alles auf ein lueckenloses 5m-Raster ----------------
def load(sym: str) -> dict:
    k = pd.read_parquet(os.path.join(DATA, "klines", f"{sym}.parquet"))
    k = k[(k.open_time >= START) & (k.open_time < OOS_END)]
    grid = np.arange(k.open_time.iloc[0], k.open_time.iloc[-1] + BAR, BAR)
    k = k.set_index("open_time").reindex(grid)
    bad = k.volume.isna() | (k.volume <= 0)
    for c in ("open", "high", "low", "close"):
        k.loc[bad, c] = np.nan
    out = {"sym": sym, "t": grid, "o": k.open.values, "h": k.high.values, "l": k.low.values,
           "c": k.close.values, "v": k.volume.values, "qv": k.quote_volume.values,
           "tbq": k.taker_buy_quote_volume.values}

    p = os.path.join(DATA, "premium", f"{sym}.parquet")
    if os.path.exists(p):
        pr = pd.read_parquet(p).set_index("open_time").reindex(grid)
        out["prem"] = pr.close.values
    p = os.path.join(DATA, "metrics", f"{sym}.parquet")
    if os.path.exists(p):
        m = pd.read_parquet(p)
        m["time"] = (m.time // BAR) * BAR
        m = m.drop_duplicates("time").set_index("time").reindex(grid)
        # Wert mit create_time == open_t ist zum Schluss der Kerze t bekannt (eine Kerze Verzoegerung)
        out["oi"] = pd.to_numeric(m.sum_open_interest, errors="coerce").values
        out["ttr"] = pd.to_numeric(m.sum_toptrader_long_short_ratio, errors="coerce").values
    p = os.path.join(DATA, "funding", f"{sym}.parquet")
    if os.path.exists(p):
        f = pd.read_parquet(p)
        f["time"] = ((f.time + 30_000) // 60_000) * 60_000
        out["f_t"] = f.time.values.astype(np.int64)
        out["f_r"] = f.funding_rate.values.astype(float)
    else:
        out["f_t"], out["f_r"] = np.array([], dtype=np.int64), np.array([])
    return out


def funding_paid(d: dict, t_in: int, t_out: int, direction: int) -> float:
    """Rendite-Abzug durch Funding fuer Abrechnungen in (t_in, t_out]."""
    ft = d["f_t"]
    if len(ft) == 0:
        return 0.0
    a, b = np.searchsorted(ft, t_in, side="right"), np.searchsorted(ft, t_out, side="right")
    return direction * d["f_r"][a:b].sum()


def make_trades(d: dict, sig_idx, sig_dir, hold) -> list[tuple]:
    """Einstieg Open t+1, Ausstieg Open t+1+hold; keine Ueberlappung; Luecke -> verwerfen."""
    o, t = d["o"], d["t"]
    n = len(o)
    out, busy_until = [], -1
    for i, dr in zip(sig_idx, sig_dir):
        if dr == 0 or i < busy_until:
            continue
        e, x = i + 1, i + 1 + (hold if np.isscalar(hold) else hold[i])
        if x >= n:
            break
        seg = o[e:x + 1]
        if np.isnan(seg).any():
            continue
        gross = dr * (o[x] / o[e] - 1)
        net = gross - COST - funding_paid(d, t[e], t[x], dr)
        out.append((int(t[e]), d["sym"], int(dr), float(net)))
        busy_until = x
    return out


def roll_std(x: np.ndarray, w: int, shift: int = 0) -> np.ndarray:
    s = pd.Series(x).rolling(w, min_periods=w // 2).std()
    return s.shift(shift).values


def hour_mask(t: np.ndarray) -> np.ndarray:
    # Kerze t schliesst zur vollen Stunde, wenn open_t + 5 min durch 1 h teilbar ist
    return ((t + BAR) % 3_600_000) == 0


# ---------------- Hypothesen pro Coin (B..I) ----------------
def hyp_B(d, p):
    if "oi" not in d:
        return []
    c, oi = d["c"], d["oi"]
    r15 = c / np.roll(c, 3) - 1
    r15[:3] = np.nan
    sig = roll_std(r15, 288)
    doi = oi / np.roll(oi, 3) - 1
    doi[:3] = np.nan
    with np.errstate(invalid="ignore"):
        fall = doi <= -p["y"]
        dirn = np.where(fall & (r15 <= -p["z"] * sig), 1, np.where(fall & (r15 >= p["z"] * sig), -1, 0))
    idx = np.where(dirn != 0)[0]
    return make_trades(d, idx, dirn[idx], p["H"])


def hyp_C(d, p):
    ft, fr, t, o = d["f_t"], d["f_r"], d["t"], d["o"]
    out = []
    w = p["w"]
    for j in range(1, len(ft)):
        r = fr[j - 1]                      # zuletzt abgerechnete Rate, bekannt seit ft[j-1]
        if abs(r) < p["thr"]:
            continue
        T = ft[j]
        k = np.searchsorted(t, T)          # Kerze mit open == T
        if k >= len(t) or t[k] != T or k - w < 0 or k + w >= len(t):
            continue
        payer = 1 if r > 0 else -1         # r>0: Longs zahlen
        for e, x, dr in ((k - w, k, -payer), (k, k + w, payer)):
            if ft[j - 1] > t[e]:
                continue                   # Rate zum Einstieg noch nicht bekannt
            seg = o[e:x + 1]
            if np.isnan(seg).any():
                continue
            net = dr * (o[x] / o[e] - 1) - COST   # Ausstieg/Einstieg genau bei T -> kein Funding
            out.append((int(t[e]), d["sym"], int(dr), float(net)))
    return out


def hyp_D(d, p):
    if "prem" not in d:
        return []
    pr = pd.Series(d["prem"])
    mu, sd = pr.rolling(2016, min_periods=1000).mean(), pr.rolling(2016, min_periods=1000).std()
    z = ((pr - mu) / sd).values
    with np.errstate(invalid="ignore"):
        dirn = np.where(z >= p["k"], -1, np.where(z <= -p["k"], 1, 0))
    idx = np.where(dirn != 0)[0]
    return make_trades(d, idx, dirn[idx], p["H"])


def _hourly(d):
    hm = hour_mask(d["t"])
    return np.where(hm)[0]


def hyp_E(d, p):
    hi = _hourly(d)
    qv = pd.Series(d["qv"]).rolling(12, min_periods=12).sum().values
    tb = pd.Series(d["tbq"]).rolling(12, min_periods=12).sum().values
    with np.errstate(invalid="ignore", divide="ignore"):
        share = tb / qv
    c = d["c"]
    r1 = c / np.roll(c, 12) - 1
    sh = pd.Series(share[hi])
    hiq = sh.rolling(720, min_periods=360).quantile(p["q"]).shift(1).values
    loq = sh.rolling(720, min_periods=360).quantile(1 - p["q"]).shift(1).values
    s, r = share[hi], r1[hi]
    with np.errstate(invalid="ignore"):
        dirn = np.where((s >= hiq) & (r > 0), -1, np.where((s <= loq) & (r < 0), 1, 0))
    m = dirn != 0
    return make_trades(d, hi[m], dirn[m], p["H"])


def hyp_F(d, p):
    if "ttr" not in d:
        return []
    x = pd.Series(d["ttr"])
    z = ((x - x.rolling(8640, min_periods=4000).mean()) / x.rolling(8640, min_periods=4000).std()).values
    hi = _hourly(d)
    with np.errstate(invalid="ignore"):
        dirn = np.where(z[hi] >= p["k"], -1, np.where(z[hi] <= -p["k"], 1, 0))
    m = dirn != 0
    return make_trades(d, hi[m], dirn[m], p["H"])


def hyp_G(d, p):
    t, o, h, l, c = d["t"], d["o"], d["h"], d["l"], d["c"]
    # 4h-Kerzen: Start bei open_time % 4h == 0
    starts = np.where((t % 14_400_000) == 0)[0]
    starts = starts[starts + 48 <= len(t)]
    if len(starts) < p["N"] + 5:
        return []
    O = o[starts]
    H4 = np.array([np.nanmax(h[s:s + 48]) if not np.isnan(h[s:s + 48]).all() else np.nan for s in starts])
    L4 = np.array([np.nanmin(l[s:s + 48]) if not np.isnan(l[s:s + 48]).all() else np.nan for s in starts])
    C4 = c[starts + 47]
    N, Nx = p["N"], p["N"] // 2
    hiN = pd.Series(H4).rolling(N).max().shift(1).values
    loN = pd.Series(L4).rolling(N).min().shift(1).values
    hiX = pd.Series(H4).rolling(Nx).max().shift(1).values
    loX = pd.Series(L4).rolling(Nx).min().shift(1).values
    out, pos, e_i = [], 0, None
    for j in range(len(starts) - 1):
        cj = C4[j]
        if np.isnan(cj):
            continue
        nxt = j + 1                        # Ausfuehrung zum Open der naechsten 4h-Kerze
        if pos != 0:
            if (pos > 0 and cj < loX[j]) or (pos < 0 and cj > hiX[j]):
                if not np.isnan(O[nxt]) and not np.isnan(O[e_i]):
                    gross = pos * (O[nxt] / O[e_i] - 1)
                    net = gross - COST - funding_paid(d, t[starts[e_i]], t[starts[nxt]], pos)
                    out.append((int(t[starts[e_i]]), d["sym"], pos, float(net)))
                pos = 0
                continue
        if pos == 0:
            if cj > hiN[j]:
                pos, e_i = 1, nxt
            elif p["ls"] and cj < loN[j]:
                pos, e_i = -1, nxt
    return out


def hyp_H(d, p):
    if "oi" not in d:
        return []
    hi = _hourly(d)
    c, oi = d["c"], d["oi"]
    r1 = (c / np.roll(c, 12) - 1)[hi]
    doi = (oi / np.roll(oi, 12) - 1)[hi]
    sig = pd.Series(r1).rolling(168, min_periods=84).std().shift(1).values
    with np.errstate(invalid="ignore"):
        up = doi >= p["y"]
        dirn = np.where(up & (r1 >= p["z"] * sig), 1, np.where(up & (r1 <= -p["z"] * sig), -1, 0))
    m = dirn != 0
    return make_trades(d, hi[m], dirn[m], p["H"])


def hyp_I(d, p):
    c, v = d["c"], d["v"]
    r = c / np.roll(c, 1) - 1
    r[0] = np.nan
    sig = roll_std(r, 288, shift=1)
    medv = pd.Series(v).rolling(288, min_periods=144).median().shift(1).values
    with np.errstate(invalid="ignore"):
        big = (np.abs(r) >= p["k"] * sig) & (v >= 5 * medv)
    dirn = np.where(big, -np.sign(r), 0).astype(int)
    idx = np.where(dirn != 0)[0]
    return make_trades(d, idx, dirn[idx], p["H"])


HYP_FN = {"B": hyp_B, "C": hyp_C, "D": hyp_D, "E": hyp_E, "F": hyp_F, "G": hyp_G, "H": hyp_H, "I": hyp_I}


def run_coin(args):
    sym, variants = args            # variants: {hyp: [param dicts]}
    d = load(sym)
    res = {}
    for hyp, plist in variants.items():
        for vi, p in enumerate(plist):
            res[(hyp, vi)] = HYP_FN[hyp](d, p)
    return res


# ---------------- A: Cross-Section-Portfolio ----------------
def hourly_matrix(syms: list[str]) -> tuple[np.ndarray, pd.DataFrame, dict]:
    opens, closes, funds = {}, {}, {}
    for s in syms:
        k = pd.read_parquet(os.path.join(DATA, "klines", f"{s}.parquet"),
                            columns=["open_time", "open", "close", "volume"])
        k = k[(k.open_time >= START) & (k.open_time < OOS_END) & (k.volume > 0)]
        ko = k[(k.open_time % 3_600_000) == 0]
        opens[s] = ko.set_index("open_time").open
        # Close der 5m-Kerze, die zur vollen Stunde endet -> Signalpreis (vor dem Einstieg bekannt)
        kc = k[((k.open_time + BAR) % 3_600_000) == 0]
        closes[s] = pd.Series(kc.close.values, index=kc.open_time.values + BAR)
        fp = os.path.join(DATA, "funding", f"{s}.parquet")
        if os.path.exists(fp):
            f = pd.read_parquet(fp)
            funds[s] = (((f.time + 30_000) // 60_000) * 60_000).values, f.funding_rate.values
    O = pd.DataFrame(opens).sort_index()
    full = np.arange(O.index[0], O.index[-1] + 3_600_000, 3_600_000)
    O = O.reindex(full)
    C = pd.DataFrame(closes).reindex(index=full, columns=O.columns)
    return full, (O, C), funds


def hyp_A(full, OC, funds, p, exclude: str | None = None):
    """Signal aus 5m-Closes zur vollen Stunde; Einstieg/Ausstieg zum Open der folgenden Kerze."""
    O, C = OC
    cols = [c for c in O.columns if c != exclude]
    P = O[cols].values
    PC = C[cols].values
    L, H = p["L"] // 12, p["H"] // 12
    out, contrib = [], {c: 0.0 for c in cols}
    for i in range(L, len(full) - H, H):
        cpast, cnow = PC[i - L], PC[i]
        now, fut = P[i], P[i + H]
        ok = ~np.isnan(cpast) & ~np.isnan(cnow) & ~np.isnan(now) & ~np.isnan(fut)
        if ok.sum() < 10:
            continue
        r = cnow / cpast - 1
        rel = r - np.nanmean(r[ok])
        idx = np.where(ok)[0]
        order = idx[np.argsort(rel[idx])]
        longs, shorts = order[:5], order[-5:]
        fwd = fut / now - 1

        def fpaid(j, dr):
            s = cols[j]
            if s not in funds:
                return 0.0
            ft, fr = funds[s]
            a, b = np.searchsorted(ft, full[i], side="right"), np.searchsorted(ft, full[i + H], side="right")
            return dr * fr[a:b].sum()

        lr = np.array([fwd[j] - COST - fpaid(j, 1) for j in longs])
        sr = np.array([-fwd[j] - COST - fpaid(j, -1) for j in shorts])
        for j, x in zip(longs, lr):
            contrib[cols[j]] += x
        for j, x in zip(shorts, sr):
            contrib[cols[j]] += x
        out.append((int(full[i]), "PORT", 0, float(0.5 * lr.mean() + 0.5 * sr.mean())))
    return out, contrib


# ---------------- Statistik ----------------
def nw_t(x: np.ndarray, lag: int = 5) -> float:
    n = len(x)
    if n < 20:
        return float("nan")
    mu = x.mean()
    e = x - mu
    g0 = (e @ e) / n
    s = g0
    for k in range(1, lag + 1):
        gk = (e[k:] @ e[:-k]) / n
        s += 2 * (1 - k / (lag + 1)) * gk
    se = np.sqrt(s / n)
    return float(mu / se) if se > 0 else float("nan")


def stats(trades: list[tuple], lo: int, hi: int, portfolio: bool = False) -> dict:
    tr = [x for x in trades if lo <= x[0] < hi]
    if not tr:
        return {"n": 0}
    df = pd.DataFrame(tr, columns=["t", "sym", "dir", "net"])
    df["day"] = pd.to_datetime(df.t, unit="ms").dt.floor("D")
    df["month"] = df.day.dt.to_period("M")
    daily = df.groupby("day").net.sum() if portfolio else df.groupby("day").net.mean()
    monthly = df.groupby("month").net.sum()
    n_days = (hi - lo) / 86_400_000
    out = {"n": len(df), "trades_per_day": len(df) / n_days, "mean_net_pct": df.net.mean() * 100,
           "t_nw": nw_t(daily.values), "days_with_trades": len(daily),
           "days_pos_share": float((daily > 0).mean()), "months_pos_share": float((monthly > 0).mean()),
           "long_share": float((df.dir > 0).mean()) if not portfolio else None}
    if not portfolio:
        tot = df.groupby("sym").net.sum()
        best = tot.idxmax()
        out["best_coin"] = best
        out["mean_net_pct_without_best"] = df[df.sym != best].net.mean() * 100
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["is", "oos"], required=True)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    check_prereg()
    sel_path = os.path.join(HERE, "selection.json")
    hyps = args.only or list(GRIDS)
    syms = symbols()
    lo, hi = (START, IS_END) if args.phase == "is" else (IS_END, OOS_END)

    if args.phase == "oos":
        if not os.path.exists(sel_path):
            sys.exit("OOS gesperrt: erst --phase is (selection.json)")
        sel = json.load(open(sel_path, encoding="utf-8"))
        variants = {h: [sel[h]["params"]] for h in hyps if h in sel}
    else:
        variants = {h: GRIDS[h] for h in hyps}

    results: dict = {}
    coin_vars = {h: v for h, v in variants.items() if h != "A"}
    if coin_vars:
        with ProcessPoolExecutor(args.workers) as pool:
            for res in pool.map(run_coin, [(s, coin_vars) for s in syms]):
                for k, v in res.items():
                    results.setdefault(k, []).extend(v)
    if "A" in variants:
        full, O, funds = hourly_matrix(syms)
        for vi, p in enumerate(variants["A"]):
            tr, contrib = hyp_A(full, O, funds, p)
            results[("A", vi)] = tr
            results[("A_contrib", vi)] = contrib

    report = {}
    for h in hyps:
        if h not in variants:
            continue
        rows = []
        for vi, p in enumerate(variants[h]):
            s = stats(results.get((h, vi), []), lo, hi, portfolio=(h == "A"))
            rows.append((p, s))
            print(f"{h} {json.dumps(p):40s} n={s.get('n', 0):>7} /Tag={s.get('trades_per_day', 0):7.2f} "
                  f"netto={s.get('mean_net_pct', float('nan')):+.4f}% t={s.get('t_nw', float('nan')):+.2f} "
                  f"Monate+={s.get('months_pos_share', float('nan')):.2f}", flush=True)
        report[h] = rows

    if args.phase == "is":
        # bestehende Auswahl anderer Hypothesen behalten (Laeufe in Teilen, z. B. erst ohne Metrics)
        sel = json.load(open(sel_path, encoding="utf-8")) if os.path.exists(sel_path) else {}
        for h, rows in report.items():
            cand = [(p, s) for p, s in rows if s.get("n", 0) >= 100 and not np.isnan(s.get("t_nw", np.nan))]
            if cand:
                p, s = max(cand, key=lambda x: x[1]["t_nw"])
                sel[h] = {"params": p, "is": s}
        json.dump(sel, open(sel_path, "w", encoding="utf-8"), indent=2, default=str)
        print("\nAuswahl gespeichert:", {h: v["params"] for h, v in sel.items()})
    else:
        verdict = {}
        for h, rows in report.items():
            p, s = rows[0]
            if h == "A":
                contrib = results[("A_contrib", 0)]
                best = max(contrib, key=contrib.get)
                full, O, funds = hourly_matrix(syms)
                s2 = stats(hyp_A(full, O, funds, p, exclude=best)[0], lo, hi, portfolio=True)
                s["best_coin"], s["mean_net_pct_without_best"] = best, s2.get("mean_net_pct")
            ok = (s.get("n", 0) > 0 and s["t_nw"] >= T_CRIT and s["mean_net_pct"] > 0
                  and s["months_pos_share"] >= 0.55 and s.get("mean_net_pct_without_best", -1) > 0)
            verdict[h] = {"params": p, "oos": s, "BESTANDEN": bool(ok)}
            print(f"==> {h}: {'BESTANDEN' if ok else 'nicht bestanden'}")
        json.dump(verdict, open(os.path.join(HERE, "oos_result.json"), "w", encoding="utf-8"), indent=2, default=str)


if __name__ == "__main__":
    main()
