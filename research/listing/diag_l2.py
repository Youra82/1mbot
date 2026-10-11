"""Nachpruefung L2 (E=30min, H=72h): Funding, Zwischenverlust (MAE), Bitget-Verfuegbarkeit."""
import io, os, zipfile, json, requests
import numpy as np, pandas as pd
import backtest_listing as bl
B = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
ev = bl.load_events()
bg = {x["symbol"] for x in requests.get("https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES", timeout=20).json()["data"]}
fcache = {}
def funding(sym, a, b):
    out = []
    for m in pd.period_range(pd.Timestamp(a, unit="ms"), pd.Timestamp(b, unit="ms"), freq="M"):
        key = (sym, str(m))
        if key not in fcache:
            r = requests.get(f"{B}/{sym}/{sym}-fundingRate-{m}.zip", timeout=60)
            if r.status_code != 200:
                fcache[key] = None
            else:
                z = zipfile.ZipFile(io.BytesIO(r.content)); fcache[key] = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        f = fcache[key]
        if f is not None:
            f = f[(f.calc_time > a) & (f.calc_time <= b)]
            out += list(f.last_funding_rate)
    return out
rows = []
for _, r in ev.iterrows():
    k = pd.read_parquet(os.path.join(bl.HERE, "ev", r.tag + "_k1m.parquet"))
    sym = open(os.path.join(bl.HERE, "ev", r.tag + "_k1m.parquet.sym")).read()
    m0 = (r.t0 // 60_000) * 60_000; te, tx = m0 + 30 * 60_000, m0 + (30 + 4320) * 60_000
    w = k[(k.open_time >= te) & (k.open_time < tx)]
    oe = k.loc[k.open_time == te, "open"]
    if oe.empty or w.empty: continue
    e = float(oe.iloc[0])
    net = bl.l2_net(k, r.t0, {"E": 30, "H": 4320})
    fr = funding(sym, te, tx)
    rows.append({"tag": r.tag, "T": r["T"], "oos": r["T"] >= bl.SPLIT, "net": net,
                 "funding_short_pnl": float(np.sum(fr)),       # Short erhaelt +rate
                 "mae": float(w.high.max() / e - 1), "mfe": float(1 - w.low.min() / e),
                 "bitget": sym in bg or sym.replace("1000", "") in bg})
d = pd.DataFrame(rows); d.to_csv("diag_l2.csv", index=False)
for name, g in (("IS", d[~d.oos]), ("OOS", d[d.oos])):
    print(f"--- {name} n={len(g)}")
    print(f" netto ohne Funding {g.net.mean()*100:+.2f}%  Funding-Ergebnis Short Mittel {g.funding_short_pnl.mean()*100:+.2f}%  (min {g.funding_short_pnl.min()*100:+.2f}%)")
    print(f" netto inkl. Funding {(g.net+g.funding_short_pnl).mean()*100:+.2f}%  Median {(g.net+g.funding_short_pnl).median()*100:+.2f}%")
    print(f" MAE (max Anstieg gg. Short): Median {g.mae.median()*100:.1f}%  p75 {g.mae.quantile(.75)*100:.1f}%  p90 {g.mae.quantile(.9)*100:.1f}%  max {g.mae.max()*100:.1f}%")
    print(f" Anteil MAE >20% {(g.mae>0.2).mean():.0%}  >33% {(g.mae>0.33).mean():.0%}  >50% {(g.mae>0.5).mean():.0%}")
    print(f" heute auf Bitget gelistet: {g.bitget.mean():.0%}")
