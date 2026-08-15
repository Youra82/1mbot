# src/onembot/exchange/rest_client.py
"""
Synchroner ccxt.bitget-Wrapper fuer alles, was NICHT laufend per Websocket
gebraucht wird: OHLCV-Historie fuer das Regime-Gate (Hurst/ADX brauchen
Kerzen-Aggregate, keine Tick-Daten) und Balance-Abfrage.

Vorbild: mbot/src/mbot/utils/exchange.py (gleiches ccxt.bitget-Setup,
gleiche defaultType='swap'-Konvention). Bewusst ohne Order-Platzierung --
das laeuft in Phase 1 ausschliesslich simuliert ueber paper_broker.py.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


class RestClient:
    def __init__(self, account_config: Optional[dict] = None, exchange_id: str = "bitget",
                 exchange_options: Optional[dict] = None):
        """
        account_config=None => rein oeffentlicher Client (OHLCV reicht ohne
        Keys). Fuer Balance-Abfragen sind echte Credentials noetig.

        exchange_id/exchange_options nur fuer historische Cross-Checks gegen
        eine ANDERE Exchange gedacht (z.B. Binance fuer 1m-Historie, die
        Bitgets oeffentliche REST-API nicht mehr zurueckgibt -- siehe
        backtest_multiday.py --exchange). Live/Order-Platzierung bleiben
        ausschliesslich Bitget (ws_client.py), das hier betrifft nur den
        historischen OHLCV-Abruf fuer Backtests.
        """
        creds = account_config or {}
        exchange_class = getattr(ccxt, exchange_id)
        self.exchange = exchange_class({
            "apiKey": creds.get("apiKey"),
            "secret": creds.get("secret"),
            "password": creds.get("password"),
            "options": exchange_options or {"defaultType": "swap"},
            "enableRateLimit": True,
        })
        try:
            self.markets = self.exchange.load_markets()
        except Exception as e:
            logger.error(f"Maerkte konnten nicht geladen werden: {e}")
            self.markets = {}

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 250) -> pd.DataFrame:
        """
        Holt die juengsten `limit` Kerzen. Bewusst als Einzel-Call ohne
        Rueckwaerts-Paginierung -- fuer Regime/ATR reichen ein paar hundert
        Kerzen, die Bitget in einem Call liefert. Vermeidet damit den
        Rueckwaerts-Paginierungs-Bug, der in mehreren aelteren Bots im
        Repo dokumentiert ist (Luecken bei mehrfachen since-Calls).
        """
        if not self.markets:
            return pd.DataFrame()
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        except Exception as e:
            logger.error(f"OHLCV-Abruf fehlgeschlagen fuer {symbol}/{timeframe}: {e}")
            return pd.DataFrame()

        if not ohlcv:
            return pd.DataFrame()

        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        df = df[~df.index.duplicated(keep="last")]
        return df

    def fetch_ohlcv_range(self, symbol: str, timeframe: str, since: datetime, until: datetime,
                           page_limit: int = 1000) -> pd.DataFrame:
        """
        Vorwaerts-paginierter Abruf ueber einen festen Zeitraum (fuer replay.py --
        fetch_ohlcv() oben reicht nur fuer die juengsten ~250 Kerzen).

        Cursor wird als `letzte_Kerze_timestamp + 1ms` fortgeschrieben (nicht
        `+ timeframe_ms` und nicht `since` einfach hochzaehlen) -- Bitgets
        since-Parameter ist exklusiv (liefert nur Kerzen mit timestamp > since).
        `+ timeframe_ms` trifft exakt den Timestamp der naechsten Kerze und
        ueberspringt sie dadurch an jeder page_limit-Grenze (derselbe Off-by-one,
        der in dbot/dnabot/knnbot gefunden und gefixt wurde -- siehe dortige
        Historie). Zusaetzliche no-progress-Guard bricht ab, falls ein Call
        keinen Fortschritt bringt.
        """
        if not self.markets:
            return pd.DataFrame()

        since_ms = int(since.timestamp() * 1000)
        until_ms = int(until.timestamp() * 1000)

        all_rows = []
        cursor = since_ms
        while cursor < until_ms:
            try:
                batch = self.exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=page_limit)
            except Exception as e:
                logger.error(f"OHLCV-Range-Abruf fehlgeschlagen fuer {symbol}/{timeframe} ab {cursor}: {e}")
                break
            if not batch:
                break
            all_rows.extend(batch)
            next_cursor = batch[-1][0] + 1
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            time.sleep(self.exchange.rateLimit / 1000)

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        df = df[~df.index.duplicated(keep="last")]

        lower = pd.Timestamp(since_ms, unit="ms", tz="UTC")
        upper = pd.Timestamp(until_ms, unit="ms", tz="UTC")
        return df[(df.index >= lower) & (df.index <= upper)]

    def fetch_balance_usdt(self) -> float:
        if not self.markets:
            return 0.0
        try:
            params = {"marginCoin": "USDT", "productType": "USDT-FUTURES"}
            balance = self.exchange.fetch_balance(params=params)
        except Exception as e:
            logger.error(f"Balance-Abruf fehlgeschlagen: {e}")
            return 0.0

        if "USDT" in balance and balance["USDT"].get("free") is not None:
            return float(balance["USDT"]["free"])
        if "info" in balance and isinstance(balance["info"], list):
            for item in balance["info"]:
                if item.get("marginCoin") == "USDT":
                    return float(item.get("available", 0.0))
        return 0.0
