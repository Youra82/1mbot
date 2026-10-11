"""Coin-Universum: USDT-Perps, die auf Binance UND Bitget handelbar sind.

Auswahl = Top-N nach Binance-24h-Volumen. Die Auswahl wird beim ersten Start in
<data_dir>/universe.json eingefroren und danach wiederverwendet, damit sich die
Stichprobe waehrend der Aufzeichnung nicht still veraendert (sonst waeren Tage
nicht vergleichbar). Neu bestimmt wird nur, wenn sich die Universum-Settings aendern.
"""
from __future__ import annotations

import hashlib
import json
import os

import requests

BINANCE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
BINANCE_24H = "https://fapi.binance.com/fapi/v1/ticker/24hr"
BITGET_TICKERS = "https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES"


def select_universe(binance_info: dict, binance_24h: list, bitget_tickers: list,
                    n: int, exclude: list[str]) -> list[str]:
    tradable = {s["symbol"] for s in binance_info["symbols"]
                if s.get("status") == "TRADING" and s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"}
    on_bitget = {t["symbol"] for t in bitget_tickers}
    vol = {t["symbol"]: float(t["quoteVolume"]) for t in binance_24h}
    cand = sorted((tradable & on_bitget) - set(exclude), key=lambda s: -vol.get(s, 0.0))
    return cand[:n]


def _settings_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def load_or_build(data_dir: str, cfg: dict) -> list[str]:
    path = os.path.join(data_dir, "universe.json")
    h = _settings_hash(cfg)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("settings_hash") == h and saved.get("symbols"):
            return saved["symbols"]

    if cfg.get("symbols"):
        symbols = list(cfg["symbols"])
    else:
        info = requests.get(BINANCE_INFO, timeout=20).json()
        t24 = requests.get(BINANCE_24H, timeout=20).json()
        bg = requests.get(BITGET_TICKERS, timeout=20).json()["data"]
        symbols = select_universe(info, t24, bg, cfg["n_symbols"], cfg.get("exclude", []))
    if not symbols:
        # leeres Universum -> hart abbrechen statt still nichts aufzuzeichnen
        raise RuntimeError("Universum leer -- Settings/Exchange-Antworten pruefen")

    os.makedirs(data_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"settings_hash": h, "symbols": symbols}, f, indent=2)
    return symbols
