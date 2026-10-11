import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research"))
import backtest_leadlag as bt  # noqa: E402

T0 = 1_000_000_000  # ms


def _books(jump_at_s: float, follow_delay_s: float, n_s: int = 900, jump: float = 0.01):
    """Binance springt bei jump_at_s um +jump, Bitget folgt follow_delay_s spaeter."""
    t = T0 + np.arange(0, n_s * 1000, 200)   # alle 200 ms ein Update
    bn_mid = np.where(t >= T0 + jump_at_s * 1000, 100 * (1 + jump), 100.0)
    bg_mid = np.where(t >= T0 + (jump_at_s + follow_delay_s) * 1000, 100 * (1 + jump), 100.0)
    # kleines Zittern, damit es Updates gibt
    wig = np.where((np.arange(len(t)) % 2) == 0, 0.0, 0.001)
    mk = lambda m: pd.DataFrame({"recv_ms": t, "bid": m - 0.005 + wig, "bid_sz": 1e4,
                                 "ask": m + 0.005 + wig, "ask_sz": 1e4})
    return mk(bn_mid), mk(bg_mid)


def test_h1_lag_is_captured_after_latency():
    bn, bg = _books(jump_at_s=700, follow_delay_s=3)
    st, sd, sm = bt.signals_h1(bn, bg)
    trades, dropped = bt.simulate(st, sd, sm, 0.0040, 5, bg, latency_ms=500)
    assert len(trades) == 1
    tr = trades[0]
    assert tr["dir"] == 1
    assert tr["t"] >= T0 + 700_000                     # kein Signal vor dem Sprung
    assert 0.009 < tr["gross"] < 0.0101
    assert abs(tr["net"] - (tr["gross"] - 0.0012)) < 1e-12


def test_h1_no_profit_when_bitget_follows_faster_than_latency():
    bn, bg = _books(jump_at_s=700, follow_delay_s=0.2)
    st, sd, sm = bt.signals_h1(bn, bg)
    trades, _ = bt.simulate(st, sd, sm, 0.0040, 5, bg, latency_ms=500)
    # Einstieg erst nach dem Nachziehen -> brutto ~0 (nur Spread), netto negativ
    assert all(x["gross"] < 0.001 for x in trades)


def test_h1_needs_10min_base_history():
    bn, bg = _books(jump_at_s=300, follow_delay_s=3)
    st, _, _ = bt.signals_h1(bn, bg)
    assert len(st) == 0 or st.min() >= T0 + bt.BASE_WIN_S * 1000


def test_size_filter_drops_thin_book():
    bn, bg = _books(jump_at_s=700, follow_delay_s=3)
    bg["ask_sz"] = 0.5          # 0,5 * ~100 = 50 USDT < 100
    st, sd, sm = bt.signals_h1(bn, bg)
    trades, dropped = bt.simulate(st, sd, sm, 0.0040, 5, bg, latency_ms=500)
    assert trades == [] and dropped["size"] >= 1


def test_h2_flow_available_only_after_second_closes():
    secs = np.arange(1000, 1000 + 4000)
    tr = pd.DataFrame({"sec": secs, "buy_usd": 10.0, "sell_usd": 5.0})
    tr.loc[tr.sec == 4800, "buy_usd"] = 10_000.0
    t, d, m = bt.signals_h2(tr, 1000, 4999)
    hit = t[m > 40]
    assert len(hit) and hit.min() == (4800 + 1) * 1000 + bt.FLOW_AVAIL_MS


def test_cluster_t():
    g = [np.array([0.01, 0.02]), np.array([0.015, 0.012])]
    mu, t, n = bt.cluster_t(g)
    assert n == 4 and abs(mu - 0.01425) < 1e-12 and t > 0
