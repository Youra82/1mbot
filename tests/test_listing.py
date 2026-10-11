import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from onembot.listing import rules  # noqa: E402
from onembot.listing.watcher import Watcher, new_state  # noqa: E402

T0 = datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)


# ---------------- Regeln ----------------
def test_listing_title_filter_real_titles():
    assert rules.is_krw_listing("카이아(KAIA) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)")
    assert rules.is_krw_listing("뉴메레르(NMR) KRW, USDT 마켓 디지털 자산 추가")
    assert not rules.is_krw_listing("블라스트(BLAST) 거래 유의 종목 지정 안내")
    assert not rules.is_krw_listing("폰즈(PONS) 신규 거래지원 안내 (BTC, USDT 마켓)")   # ohne KRW


def test_extract_tickers_skips_market_names():
    assert rules.extract_tickers("카이아(KAIA) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)") == ["KAIA"]
    assert rules.extract_tickers("에이(AAA), 비(BBB2) 신규 거래지원 안내 (KRW 마켓)") == ["AAA", "BBB2"]


def test_duplicate_window():
    last = {"AAA": T0}
    assert rules.is_duplicate("AAA", T0 + timedelta(days=29), last)
    assert not rules.is_duplicate("AAA", T0 + timedelta(days=31), last)
    assert not rules.is_duplicate("BBB", T0, last)


def test_short_return_time_exit():
    r, why, _ = rules.short_return_on_margin(100.0, np.array([101.0, 105.0]), 90.0, 0.0)
    gross = (100 * (1 - rules.COST_SIDE)) / (90 * (1 + rules.COST_SIDE)) - 1
    assert why == "time" and abs(r - 3 * gross) < 1e-12


def test_short_return_stop_and_liq():
    r, why, i = rules.short_return_on_margin(100.0, np.array([110.0, 116.0, 140.0]), 90.0, 0.0)
    assert why == "stop" and i == 1
    assert abs(r - 3 * (-(0.15 + 0.01) - 2 * rules.COST_SIDE)) < 1e-12
    # ohne Stop bei 3x: Liquidation ab +32 %
    r, why, _ = rules.short_return_on_margin(100.0, np.array([110.0, 133.0]), 90.0, 0.0, stop=None)
    assert (r, why) == (-1.0, "liq")


def test_short_return_live_funding_until_stop():
    r, _, _ = rules.short_return_on_margin(100.0, np.array([116.0]), np.nan, 0.0, funding_until_stop=-0.02)
    assert abs(r - 3 * (-(0.16) - 2 * rules.COST_SIDE - 0.02)) < 1e-12


# ---------------- Waechter mit Fake-Boersen ----------------
class FakeUpbit:
    def __init__(self):
        self.items = []

    def notices(self, page=1):
        return list(reversed(self.items)) if page == 1 else []


class FakeBitget:
    def __init__(self):
        self.bid, self.ask = 1.00, 1.002
        self.candles = []          # (open_time_ms, high, open)
        self.funding = []          # (time_ms, rate)
        self.fail_ticker = False

    def contracts(self):
        return {"KAIAUSDT", "1000BONKUSDT"}

    def ticker(self, symbol):
        if self.fail_ticker:
            raise RuntimeError("Bitget down")
        return {"bid": self.bid, "ask": self.ask, "bid_sz": 1e6, "ask_sz": 1e6, "last": self.bid, "funding_rate": 0}

    def candles_1m(self, symbol, start_ms):
        return [c for c in self.candles if c[0] >= start_ms]

    def funding_history(self, symbol):
        return self.funding


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _ms(t):
    return int(t.timestamp() * 1000)


def _setup():
    up, bg, clk = FakeUpbit(), FakeBitget(), Clock(T0)
    w = Watcher(new_state(100.0), up, bg, {"margin_fraction": 0.2}, now=clk, sleep=lambda s: None)
    w.bootstrap()
    return w, up, bg, clk


def test_old_notices_not_traded_on_first_start():
    up, bg, clk = FakeUpbit(), FakeBitget(), Clock(T0)
    up.items = [{"id": 1, "title": "카이아(KAIA) 신규 거래지원 안내 (KRW 마켓)", "T": T0 - timedelta(hours=2)}]
    w = Watcher(new_state(100.0), up, bg, {"margin_fraction": 0.2}, now=clk, sleep=lambda s: None)
    w.bootstrap()
    w.poll_upbit()
    assert w.s["pending"] == [] and "KAIA" in w.s["last_seen_ticker"]


def test_full_cycle_time_exit_with_funding():
    w, up, bg, clk = _setup()
    up.items.append({"id": 10, "title": "카이아(KAIA) 신규 거래지원 안내 (KRW, BTC, USDT 마켓)", "T": T0})
    clk.t = T0 + timedelta(seconds=12)
    w.poll_upbit()
    assert len(w.s["pending"]) == 1 and w.s["pending"][0]["detect_latency_s"] == 12.0
    w.enter_due()
    assert w.s["open"] == []                       # noch vor T+30 min
    clk.t = T0 + timedelta(minutes=30)
    w.enter_due()
    o = w.s["open"][0]
    assert o["entry_bid"] == 1.00 and abs(o["margin"] - 20.0) < 1e-9
    entry_ms = _ms(clk.t)
    bg.candles = [(entry_ms + i * 60_000, 1.05, 1.0) for i in range(10)]
    bg.funding = [(entry_ms + 3_600_000, -0.001), (entry_ms - 1, -0.5)]   # zweite vor Einstieg -> zaehlt nicht
    clk.t = T0 + timedelta(minutes=45)
    w.check_open()
    assert len(w.s["open"]) == 1 and len(w.s["open"][0]["highs"]) == 10
    bg.ask = 0.80
    clk.t = T0 + timedelta(minutes=30) + rules.HOLD
    w.check_open()
    assert w.s["open"] == [] and len(w.s["closed"]) == 1
    c = w.s["closed"][0]
    assert c["reason"] == "time" and abs(c["funding_short"] + 0.001) < 1e-12
    exp, _, _ = rules.short_return_on_margin(1.00, np.array([1.05] * 10), 0.80, -0.001)
    assert abs(c["ret_margin_backtest_costs"] - exp) < 1e-12
    assert abs(w.s["equity"] - (100 + 20 * exp)) < 1e-9


def test_stop_triggers_on_running_minute():
    w, up, bg, clk = _setup()
    up.items.append({"id": 11, "title": "봉크(BONK) 신규 거래지원 안내 (KRW 마켓)", "T": T0})
    w.poll_upbit()
    assert w.s["pending"][0]["symbol"] == "1000BONKUSDT"
    clk.t = T0 + timedelta(minutes=30)
    w.enter_due()
    now_min = _ms(clk.t) // 60_000 * 60_000
    bg.candles = [(now_min, 1.20, 1.0)]            # laufende Minute schiesst ueber Stop (+15 %)
    clk.t = clk.t + timedelta(seconds=20)
    w.check_open()
    assert w.s["closed"][0]["reason"] == "stop"
    assert w.s["equity"] < 100


def test_skip_reasons():
    w, up, bg, clk = _setup()
    up.items.append({"id": 12, "title": "없음(NOPE) 신규 거래지원 안내 (KRW 마켓)", "T": T0})
    up.items.append({"id": 13, "title": "늦음(LATE) 신규 거래지원 안내 (KRW 마켓)", "T": T0 - timedelta(minutes=40)})
    w.poll_upbit()
    reasons = {s["ticker"]: s["reason"] for s in w.s["skipped"]}
    assert reasons == {"NOPE": "kein Bitget-Perp", "LATE": "zu spaet erkannt"}


def test_ticker_failure_keeps_pending_without_duplicates():
    w, up, bg, clk = _setup()
    up.items.append({"id": 14, "title": "카이아(KAIA) 신규 거래지원 안내 (KRW 마켓)", "T": T0})
    w.poll_upbit()
    clk.t = T0 + timedelta(minutes=31)
    bg.fail_ticker = True
    w.enter_due()
    assert len(w.s["pending"]) == 1 and w.s["open"] == []
    bg.fail_ticker = False
    w.enter_due()
    w.enter_due()
    assert len(w.s["pending"]) == 0 and len(w.s["open"]) == 1
