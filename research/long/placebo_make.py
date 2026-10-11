"""Placebo-Daten: Zufallspfade ohne jeden Vorteil im gleichen Format wie data_long/."""
import os, sys
import numpy as np, pandas as pd
out = sys.argv[1]
rng = np.random.default_rng(7)
t = np.arange(pd.Timestamp("2022-01-01", tz="UTC").value // 10**6, pd.Timestamp("2026-10-01", tz="UTC").value // 10**6, 300_000)
for k in ("klines", "premium", "metrics", "funding"):
    os.makedirs(os.path.join(out, k), exist_ok=True)
for i in range(12):
    sym = f"P{i:02d}USDT"
    vol = 0.002 * np.exp(0.3 * np.sin(np.arange(len(t)) / 5000 + i))      # wechselnde Volatilitaet
    r = rng.standard_t(4, len(t)) * vol / np.sqrt(2)
    c = 100 * np.exp(np.cumsum(r)); o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2))); l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2)))
    v = rng.lognormal(10, 1, len(t)); qv = v * c; tb = qv * np.clip(rng.normal(0.5, 0.08, len(t)), 0.01, 0.99)
    pd.DataFrame({"open_time": t, "open": o, "high": h, "low": l, "close": c, "volume": v, "close_time": t + 299_999,
                  "quote_volume": qv, "count": 1, "taker_buy_volume": tb / c, "taker_buy_quote_volume": tb}).to_parquet(f"{out}/klines/{sym}.parquet")
    pr = np.zeros(len(t)); e = rng.normal(0, 3e-4, len(t))
    for j in range(1, len(t)): pr[j] = 0.98 * pr[j - 1] + e[j]
    pd.DataFrame({"open_time": t, "open": pr, "high": pr, "low": pr, "close": pr, "volume": 0, "close_time": t,
                  "quote_volume": 0, "count": 0, "taker_buy_volume": 0, "taker_buy_quote_volume": 0}).to_parquet(f"{out}/premium/{sym}.parquet")
    oi = 1e6 * np.exp(np.cumsum(rng.normal(0, 0.004, len(t)))); ttr = np.exp(np.cumsum(rng.normal(0, 0.003, len(t))) * 0.3)
    pd.DataFrame({"time": t, "sum_open_interest": oi, "sum_open_interest_value": oi * c, "count_toptrader_long_short_ratio": 1,
                  "sum_toptrader_long_short_ratio": ttr, "count_long_short_ratio": 1, "sum_taker_long_short_vol_ratio": 1}).to_parquet(f"{out}/metrics/{sym}.parquet")
    ft = t[(t % 28_800_000) == 0]
    pd.DataFrame({"time": ft, "funding_rate": rng.normal(0.0001, 0.0005, len(ft))}).to_parquet(f"{out}/funding/{sym}.parquet")
print("ok")
