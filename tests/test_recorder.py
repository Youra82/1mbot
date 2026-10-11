import gzip
import json
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from onembot.recorder import universe  # noqa: E402
from onembot.recorder.aggregate import BookState, BookThrottle, TradeAggregator  # noqa: E402
from onembot.recorder.parsers import parse_binance, parse_bitget  # noqa: E402
from onembot.recorder.streams import binance_stream_groups, bitget_args, chunks  # noqa: E402
from onembot.recorder.writer import HourlyGzWriter  # noqa: E402

T0 = 1_791_620_000_000  # 2026-10-10 08:13:20 UTC


# ---------------- Writer ----------------
def test_writer_multi_member_header_once(tmp_path):
    w = HourlyGzWriter(str(tmp_path))
    w.add("bitget", "book", T0, (T0, T0 - 5, "BTCUSDT", 1.0, 2.0, 1.1, 3.0))
    w.flush()
    w.add("bitget", "book", T0 + 1000, (T0 + 1000, T0 + 995, "BTCUSDT", 1.05, 2.0, 1.1, 3.0))
    w.flush()
    path = w.path_for("2026-10-10", "08", "bitget", "book")
    df = pd.read_csv(path)
    assert list(df.columns) == ["recv_ms", "exch_ms", "symbol", "bid", "bid_sz", "ask", "ask_sz"]
    assert len(df) == 2 and df.bid.tolist() == [1.0, 1.05]
    assert w.rows_written[("bitget", "book")] == 2


def test_writer_hour_rotation(tmp_path):
    w = HourlyGzWriter(str(tmp_path))
    w.add("binance", "events", T0, (T0, "binance", "x", "a"))
    w.add("binance", "events", T0 + 3_600_000, (T0, "binance", "y", "b"))
    w.flush()
    assert os.path.exists(w.path_for("2026-10-10", "08", "binance", "events"))
    assert os.path.exists(w.path_for("2026-10-10", "09", "binance", "events"))


def test_writer_rejects_wrong_width_and_escapes_commas(tmp_path):
    w = HourlyGzWriter(str(tmp_path))
    with pytest.raises(ValueError):
        w.add("binance", "book", T0, (1, 2, 3))
    w.add("binance", "events", T0, (T0, "binance", "error", "a,b\nc"))
    w.flush()
    df = pd.read_csv(w.path_for("2026-10-10", "08", "binance", "events"))
    assert df.detail.iloc[0] == "a;b c"


def test_writer_paused_kind_dropped(tmp_path):
    w = HourlyGzWriter(str(tmp_path))
    w.paused_kinds.add("book")
    w.add("binance", "book", T0, (T0, T0, "BTCUSDT", 1, 1, 1, 1))
    assert w.flush() == 0


# ---------------- BookThrottle ----------------
def _st(t, bid, ask):
    return BookState(t, t - 3, bid, 1.0, ask, 1.0)


def test_throttle_skips_unchanged_price():
    th = BookThrottle(100)
    assert th.update("X", _st(0, 10, 11)) is not None
    assert th.update("X", _st(500, 10, 11)) is None  # nur Groesse geaendert


def test_throttle_pending_delivers_latest_state_with_own_timestamp():
    th = BookThrottle(100)
    th.update("X", _st(0, 10, 11))
    assert th.update("X", _st(20, 10.1, 11)) is None
    assert th.update("X", _st(50, 10.2, 11)) is None
    assert th.due(80) == []  # Intervall noch nicht um
    out = th.due(120)
    assert len(out) == 1 and out[0][1].recv_ms == 50 and out[0][1].bid == 10.2


def test_throttle_revert_to_last_written_price_drops_pending():
    th = BookThrottle(100)
    th.update("X", _st(0, 10, 11))
    th.update("X", _st(20, 10.1, 11))
    th.update("X", _st(40, 10, 11))
    assert th.due(200) == []


def test_throttle_emits_directly_after_interval():
    th = BookThrottle(100)
    th.update("X", _st(0, 10, 11))
    out = th.update("X", _st(150, 10.1, 11))
    assert out is not None and out.recv_ms == 150


# ---------------- TradeAggregator ----------------
def test_trade_aggregator_buckets_and_grace():
    ag = TradeAggregator(grace_seconds=2)
    ag.add("BTC", 10_000, 100.0, 1.0, True)
    ag.add("BTC", 10_500, 102.0, 2.0, False)
    ag.add("BTC", 10_900, 99.0, 1.0, True)
    ag.add("BTC", 11_100, 101.0, 1.0, True)
    assert ag.pop_closed(11_900) == []  # Sekunde 10 noch in der Karenz
    rows = ag.pop_closed(13_000)
    assert len(rows) == 1
    sec, sym, bq, sq, busd, susd, n, first, last, hi, lo = rows[0]
    assert (sec, sym, bq, sq, n) == (10, "BTC", 2.0, 2.0, 3)
    assert (busd, susd) == (199.0, 204.0)
    assert (first, last, hi, lo) == (100.0, 99.0, 102.0, 99.0)
    assert len(ag.pop_closed(0, force=True)) == 1


# ---------------- Parser ----------------
def test_parse_binance_book_trade_liq():
    book = {"stream": "btcusdt@bookTicker", "data": {"e": "bookTicker", "s": "BTCUSDT", "T": 5, "E": 6,
                                                     "b": "100.0", "B": "2", "a": "100.1", "A": "3"}}
    assert parse_binance(book) == [("book", "BTCUSDT", 5, 100.0, 2.0, 100.1, 3.0)]
    # m=True: Kaeufer ist Maker -> Aggressor verkauft
    tr = {"data": {"e": "aggTrade", "s": "BTCUSDT", "T": 7, "p": "100", "q": "0.5", "m": True}}
    assert parse_binance(tr) == [("trade", "BTCUSDT", 7, 100.0, 0.5, False)]
    liq = {"data": {"e": "forceOrder", "o": {"s": "ETHUSDT", "S": "SELL", "q": "1", "p": "9",
                                             "ap": "9.5", "X": "FILLED", "T": 8}}}
    assert parse_binance(liq) == [("liq", "ETHUSDT", 8, "SELL", 1.0, 9.0, 9.5, "FILLED")]
    assert parse_binance({"result": None, "id": 1}) == []


def test_parse_bitget_book_and_trade_snapshot_skipped():
    book = {"action": "snapshot", "arg": {"instType": "USDT-FUTURES", "channel": "books1", "instId": "BTCUSDT"},
            "data": [{"bids": [["100.0", "2"]], "asks": [["100.1", "3"]], "ts": "123"}]}
    assert parse_bitget(book) == [("book", "BTCUSDT", 123, 100.0, 2.0, 100.1, 3.0)]
    arg = {"instType": "USDT-FUTURES", "channel": "trade", "instId": "BTCUSDT"}
    snap = {"action": "snapshot", "arg": arg, "data": [{"ts": "1", "price": "1", "size": "1", "side": "buy"}]}
    assert parse_bitget(snap) == []
    upd = {"action": "update", "arg": arg, "data": [{"ts": "9", "price": "100", "size": "2", "side": "sell"}]}
    assert parse_bitget(upd) == [("trade", "BTCUSDT", 9, 100.0, 2.0, False)]
    empty = {"action": "snapshot", "arg": book["arg"], "data": [{"bids": [], "asks": [], "ts": "1"}]}
    assert parse_bitget(empty) == []


# ---------------- Streams/Universum ----------------
def test_stream_groups_split_public_market():
    g = binance_stream_groups(["BTCUSDT"])
    assert g["public"] == ["btcusdt@bookTicker"]
    assert g["market"] == ["btcusdt@aggTrade", "!forceOrder@arr"]
    assert len(bitget_args(["A", "B"])) == 4
    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_select_universe_intersection_rank_exclude():
    info = {"symbols": [
        {"symbol": s, "status": "TRADING", "contractType": "PERPETUAL", "quoteAsset": "USDT"}
        for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "USDCUSDT")
    ] + [{"symbol": "OLDUSDT", "status": "BREAK", "contractType": "PERPETUAL", "quoteAsset": "USDT"}]}
    t24 = [{"symbol": "BTCUSDT", "quoteVolume": "9"}, {"symbol": "ETHUSDT", "quoteVolume": "5"},
           {"symbol": "SOLUSDT", "quoteVolume": "7"}, {"symbol": "USDCUSDT", "quoteVolume": "99"},
           {"symbol": "OLDUSDT", "quoteVolume": "50"}]
    bg = [{"symbol": s} for s in ("BTCUSDT", "SOLUSDT", "USDCUSDT", "OLDUSDT")]
    assert universe.select_universe(info, t24, bg, 5, ["USDCUSDT"]) == ["BTCUSDT", "SOLUSDT"]


def test_load_or_build_freezes_and_rebuilds_on_settings_change(tmp_path):
    cfg = {"n_symbols": 2, "symbols": ["BTCUSDT", "ETHUSDT"], "exclude": []}
    assert universe.load_or_build(str(tmp_path), cfg) == ["BTCUSDT", "ETHUSDT"]
    saved = json.load(open(tmp_path / "universe.json"))
    assert saved["symbols"] == ["BTCUSDT", "ETHUSDT"]
    cfg2 = dict(cfg, symbols=["SOLUSDT"])
    assert universe.load_or_build(str(tmp_path), cfg2) == ["SOLUSDT"]


def test_load_or_build_empty_universe_fails_hard(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "select_universe", lambda *a, **k: [])

    class R:
        def json(self):
            return {"data": [], "symbols": []}

    monkeypatch.setattr(universe.requests, "get", lambda *a, **k: R())
    with pytest.raises(RuntimeError):
        universe.load_or_build(str(tmp_path), {"n_symbols": 5, "symbols": [], "exclude": []})
