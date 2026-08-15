# tests/test_ledger.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

from onembot.utils import ledger as ledger_module
from onembot.utils.ledger import FillLedger


def test_load_missing_file_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "TRACKER_DIR", tmp_path)
    fl = FillLedger("does_not_exist.jsonl")
    assert fl.load() == []


def test_append_and_load_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "TRACKER_DIR", tmp_path)
    fl = FillLedger("fills.jsonl")

    fl.append({"symbol": "BTC/USDT:USDT", "realized_pnl_usdt": 1.5})
    fl.append({"symbol": "ETH/USDT:USDT", "realized_pnl_usdt": -0.5})

    rows = fl.load()
    assert len(rows) == 2
    assert rows[0]["symbol"] == "BTC/USDT:USDT"
    assert rows[1]["realized_pnl_usdt"] == -0.5


def test_load_skips_corrupt_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "TRACKER_DIR", tmp_path)
    fl = FillLedger("fills.jsonl")
    fl.append({"symbol": "BTC/USDT:USDT", "realized_pnl_usdt": 1.0})
    with open(fl.path, "a", encoding="utf-8") as f:
        f.write("not valid json\n")
    fl.append({"symbol": "ETH/USDT:USDT", "realized_pnl_usdt": 2.0})

    rows = fl.load()
    assert len(rows) == 2
    assert [r["symbol"] for r in rows] == ["BTC/USDT:USDT", "ETH/USDT:USDT"]
