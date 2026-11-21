"""Oeffentliche REST-Endpunkte (keine API-Keys): Upbit-Ankuendigungen, Bitget-Perp-Marktdaten."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

UPBIT = "https://api-manager.upbit.com/api/v1/announcements?os=web&page={p}&per_page=20&category=trade"
BG = "https://api.bitget.com/api/v2/mix/market"
PT = "USDT-FUTURES"
HDR = {"User-Agent": "Mozilla/5.0"}


class ExchangeError(RuntimeError):
    pass


def _get(url: str, params: dict | None = None, retries: int = 4) -> dict:
    """GET mit Wiederholung bei Drosselung (429) und Serverfehlern (5xx)."""
    for attempt in range(retries + 1):
        r = requests.get(url, params=params, headers=HDR, timeout=15)
        if r.status_code == 429 or r.status_code >= 500:
            if attempt < retries:
                wait = 2 ** (attempt + 1)
                try:                                      # Server-Vorgabe (Upbit/Cloudflare) beachten, max. 60 s
                    wait = max(wait, min(60, int(r.headers.get("Retry-After", 0))))
                except ValueError:
                    pass
                time.sleep(wait)
                continue
        r.raise_for_status()
        return r.json()
    raise ExchangeError(f"keine Antwort: {url}")


class Upbit:
    def notices(self, page: int = 1) -> list[dict]:
        d = _get(UPBIT.format(p=page))
        if not d.get("success"):
            raise ExchangeError(f"Upbit: {d}")
        out = []
        for n in d["data"]["notices"]:
            out.append({"id": n["id"], "title": n["title"],
                        "T": datetime.fromisoformat(n["first_listed_at"]).astimezone(timezone.utc)})
        return out


class Bitget:
    def _data(self, path: str, **params):
        d = _get(f"{BG}/{path}", {"productType": PT, **params})
        if d.get("code") != "00000":
            raise ExchangeError(f"Bitget {path}: {d.get('code')} {d.get('msg')}")
        return d["data"]

    def contracts(self) -> set[str]:
        return {c["symbol"] for c in self._data("contracts") if c.get("symbolStatus") == "normal"}

    def ticker(self, symbol: str) -> dict:
        t = self._data("ticker", symbol=symbol)[0]
        return {"bid": float(t["bidPr"]), "ask": float(t["askPr"]), "bid_sz": float(t["bidSz"]),
                "ask_sz": float(t["askSz"]), "last": float(t["lastPr"]), "funding_rate": float(t["fundingRate"])}

    def candles_1m(self, symbol: str, start_ms: int) -> list[tuple[int, float, float]]:
        """(open_time, high, open) ab start_ms, aufsteigend; die letzte Kerze kann noch offen sein.

        Bitget liefert ohne endTime nur die LETZTEN 1000 Minuten, egal wie alt startTime ist (geprueft 21.11.2026).
        Nach einem Ausfall > ~16 h fehlten sonst Hochs still (verpasster Stop). Deshalb aeltere Abschnitte in
        1000-Minuten-Fenstern mit endTime laden; mit endTime zaehlt startTime exklusiv -> eine Minute frueher fragen.
        """
        out: dict[int, tuple[float, float]] = {}
        s = start_ms
        while int(time.time() * 1000) - s > 900 * 60_000:
            rows = self._data("candles", symbol=symbol, granularity="1m", startTime=str(s - 60_000),
                              endTime=str(s + 999 * 60_000), limit="1000")
            ts = [int(r[0]) for r in rows]
            for r in rows:
                if int(r[0]) >= s:
                    out[int(r[0])] = (float(r[2]), float(r[1]))
            s = max(ts) + 60_000 if ts and max(ts) >= s else s + 1000 * 60_000
        rows = self._data("candles", symbol=symbol, granularity="1m", startTime=str(s), limit="1000")
        for r in rows:
            if int(r[0]) >= start_ms:
                out[int(r[0])] = (float(r[2]), float(r[1]))
        return sorted((t, h, o) for t, (h, o) in out.items())

    def funding_history(self, symbol: str) -> list[tuple[int, float]]:
        rows = self._data("history-fund-rate", symbol=symbol, pageSize="100")
        return sorted((int(r["fundingTime"]), float(r["fundingRate"])) for r in rows)
