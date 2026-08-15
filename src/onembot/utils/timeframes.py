# src/onembot/utils/timeframes.py
"""Gemeinsames Parsing von ccxt-Timeframe-Strings ("1m", "5m", "1h", "1d", "1w")."""
from __future__ import annotations

from datetime import timedelta

_UNIT_MINUTES = {"m": 1, "h": 60, "d": 60 * 24, "w": 60 * 24 * 7}


def timeframe_to_minutes(timeframe: str) -> int:
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    if unit not in _UNIT_MINUTES:
        raise ValueError(f"Unbekannte Timeframe-Einheit: {timeframe}")
    return value * _UNIT_MINUTES[unit]


def timeframe_to_timedelta(timeframe: str) -> timedelta:
    return timedelta(minutes=timeframe_to_minutes(timeframe))
