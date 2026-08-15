# src/onembot/utils/ledger.py
"""
Append-only Fill-Log (JSONL), getrennt von state.py (das nur den aktuellen
Snapshot haelt). Ohne ein echtes Log laesst sich keine Equity-Kurve/Drawdown
rekonstruieren -- state.py allein reicht dafuer nicht, siehe report.py.

Live-Lauf und historischer Replay schreiben bewusst in getrennte Dateien
(siehe run.py vs. backtest_replay.py), damit sich approximierte
Replay-Ergebnisse nicht mit echten Live-Dry-Run-Fills vermischen.
"""
from __future__ import annotations

import json
from pathlib import Path

TRACKER_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "tracker"


class FillLedger:
    def __init__(self, filename: str):
        self.path = TRACKER_DIR / filename

    def append(self, fill: dict) -> None:
        TRACKER_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(fill) + "\n")

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows
