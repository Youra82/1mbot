"""L2-Umsetzung: Hebel/Stop auf IS waehlen, OOS berichten. Pfadgenau auf 1m-Hochs.
Stop: Ausloesung, wenn 1m-Hoch >= Einstieg*(1+s); Fill = Stop-Preis + 1 % Slippage (Pump-Gaps).
Isolierte Margin: Liquidation bei Anstieg >= 1/L - 1 % (Wartungsmarge) -> -100 % der Margin.
Funding anteilig bis zum Ausstieg. Rendite je Ereignis auf die eingesetzte Margin."""
import os, numpy as np, pandas as pd, io, zipfile, requests
import backtest_listing as bl
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(bl.HERE)), 'src'))
from onembot.listing import rules
d = pd.read_csv("diag_l2.csv").dropna(subset=["net"]); d["T"] = pd.to_datetime(d["T"], utc=True)
ev = bl.load_events().set_index("tag")
FUND = {}
def path(tag):
    k = pd.read_parquet(os.path.join(bl.HERE, "ev", tag + "_k1m.parquet"))
    t0 = ev.loc[tag, "t0"]; m0 = (t0 // 60_000) * 60_000
    te, tx = m0 + 30 * 60_000, m0 + 4350 * 60_000
    w = k[(k.open_time >= te) & (k.open_time < tx)].sort_values("open_time")
    return float(w.open.iloc[0]), w.high.values, float(k.loc[k.open_time == tx, "open"].iloc[0])
P = {r.tag: path(r.tag) for r in d.itertuples()}
def ev_ret(r, L, s):
    e, highs, x = P[r.tag]
    return rules.short_return_on_margin(e, highs, x, r.funding_short_pnl, lev=L, stop=s)[0]


SL = [None, 0.15, 0.25, 0.40]; LEV = [1, 2, 3]
def table(g):
    out = {}
    for L in LEV:
        for s in SL:
            x = np.array([ev_ret(r, L, s) for r in g.itertuples()])
            out[(L, s)] = x
    return out
IS, OOS = d[~d.oos], d[d.oos]
ti, to = table(IS), table(OOS)
print(f"{'Hebel':>5} {'Stop':>5} | {'IS mean':>8} {'IS med':>7} {'IS t':>5} {'IS liq':>6} {'IS logw':>7} | {'OOS mean':>8} {'OOS med':>7} {'OOS t':>5} {'OOS liq':>7}")
for key in ti:
    a, b = ti[key], to[key]
    lg = lambda x, f=0.2: np.mean(np.log1p(f * x))   # Wachstum bei 20 % Kapital je Ereignis
    tt = lambda x: x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
    print(f"{key[0]:>5} {str(key[1]):>5} | {a.mean()*100:+7.2f}% {np.median(a)*100:+6.2f}% {tt(a):5.2f} {np.mean(a<=-0.999):6.0%} {lg(a)*100:+6.2f}% | {b.mean()*100:+7.2f}% {np.median(b)*100:+6.2f}% {tt(b):5.2f} {np.mean(b<=-0.999):7.0%}")
best = max(ti, key=lambda k: np.mean(np.log1p(0.2 * ti[k])))
print("IS-Wahl (max log-Wachstum bei 20 % je Ereignis):", best)
import json; json.dump({"lev": best[0], "stop": best[1]}, open("sizing_selection.json", "w"))
