# src/onembot/utils/state.py
"""
Persistiert den Grid/Inventory-Zustand je Symbol nach artifacts/tracker/,
analog zu mbots artifacts/tracker/active_positions.json -- damit ueberlebt
der Zustand einen Neustart und laesst sich fuer Monitoring auslesen.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

TRACKER_DIR = Path(__file__).resolve().parents[3] / "artifacts" / "tracker"


def _symbol_slug(symbol: str) -> str:
    return symbol.replace("/", "-").replace(":", "_")


def state_path(symbol: str) -> Path:
    return TRACKER_DIR / f"{_symbol_slug(symbol)}.json"


def load_state(symbol: str) -> dict:
    path = state_path(symbol)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(symbol: str, state: dict) -> None:
    TRACKER_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(symbol)
    tmp_path = path.with_suffix(".json.tmp")
    payload = {**state, "last_updated": datetime.now(timezone.utc).isoformat()}
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    tmp_path.replace(path)
