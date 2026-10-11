"""Websocket-Verbindungen zu Binance USDT-M und Bitget USDT-FUTURES.

Jede Verbindung laeuft in einer eigenen Endlosschleife mit Reconnect + Backoff.
Jeder Verbindungsabbruch wird als Event geschrieben, damit spaeter Datenluecken
erkennbar sind und nicht als "Markt hat sich nicht bewegt" fehlinterpretiert werden.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

import aiohttp

from .parsers import parse_binance, parse_bitget

log = logging.getLogger("1mbot.streams")

# Binance hat die USDT-M-Streams aufgeteilt (Stand 2026): bookTicker/Depth nur noch
# ueber /public, aggTrade/forceOrder/markPrice ueber /market. Der alte Pfad /stream
# liefert still nur noch bookTicker -- deshalb pro Gruppe eigene Basis-URL.
BINANCE_WS = {
    "public": "wss://fstream.binance.com/public/stream?streams=",
    "market": "wss://fstream.binance.com/market/stream?streams=",
}
BITGET_WS = "wss://ws.bitget.com/v2/ws/public"

OnEvents = Callable[[str, int, list], None]      # (exchange, recv_ms, events)
OnConn = Callable[[str, str, str], None]         # (exchange, event, detail)


def now_ms() -> int:
    return int(time.time() * 1000)


def chunks(seq: list, n: int) -> list[list]:
    return [seq[i:i + n] for i in range(0, len(seq), n)]


def binance_stream_groups(symbols: list[str]) -> dict[str, list[str]]:
    lows = [s.lower() for s in symbols]
    return {
        "public": [f"{s}@bookTicker" for s in lows],
        # !forceOrder@arr = alle Liquidationen, marktweit
        "market": [f"{s}@aggTrade" for s in lows] + ["!forceOrder@arr"],
    }


def bitget_args(symbols: list[str]) -> list[dict]:
    args = []
    for s in symbols:
        for ch in ("books1", "trade"):
            args.append({"instType": "USDT-FUTURES", "channel": ch, "instId": s})
    return args


async def _backoff(attempt: int) -> None:
    await asyncio.sleep(min(60, 2 ** min(attempt, 6)))


async def run_binance(session: aiohttp.ClientSession, group: str, streams: list[str], tag: str,
                      on_events: OnEvents, on_conn: OnConn, stop: asyncio.Event) -> None:
    url = BINANCE_WS[group] + "/".join(streams)
    attempt = 0
    while not stop.is_set():
        try:
            async with session.ws_connect(url, heartbeat=20, max_msg_size=0) as ws:
                on_conn("binance", "connect", f"{tag} streams={len(streams)}")
                attempt = 0
                async for m in ws:
                    if m.type != aiohttp.WSMsgType.TEXT:
                        if m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                        continue
                    recv = now_ms()
                    try:
                        evs = parse_binance(json.loads(m.data))
                    except Exception as ex:  # einzelne kaputte Nachricht darf den Stream nicht toeten
                        on_conn("binance", "parse_error", f"{tag} {ex!r}")
                        continue
                    if evs:
                        on_events("binance", recv, evs)
                    if stop.is_set():
                        break
            on_conn("binance", "disconnect", tag)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            on_conn("binance", "error", f"{tag} {ex!r}")
        attempt += 1
        if not stop.is_set():
            await _backoff(attempt)


async def run_bitget(session: aiohttp.ClientSession, args: list[dict], tag: str,
                     on_events: OnEvents, on_conn: OnConn, stop: asyncio.Event) -> None:
    attempt = 0
    while not stop.is_set():
        try:
            async with session.ws_connect(BITGET_WS, max_msg_size=0) as ws:
                await ws.send_str(json.dumps({"op": "subscribe", "args": args}))
                on_conn("bitget", "connect", f"{tag} channels={len(args)}")
                attempt = 0

                async def pinger():
                    # Bitget trennt nach 2 min ohne "ping"
                    while True:
                        await asyncio.sleep(25)
                        await ws.send_str("ping")

                ping_task = asyncio.create_task(pinger())
                try:
                    async for m in ws:
                        if m.type != aiohttp.WSMsgType.TEXT:
                            if m.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        if m.data == "pong":
                            continue
                        recv = now_ms()
                        try:
                            msg = json.loads(m.data)
                            if msg.get("event") == "error":
                                on_conn("bitget", "sub_error", f"{tag} {msg}")
                                continue
                            evs = parse_bitget(msg)
                        except Exception as ex:
                            on_conn("bitget", "parse_error", f"{tag} {ex!r}")
                            continue
                        if evs:
                            on_events("bitget", recv, evs)
                        if stop.is_set():
                            break
                finally:
                    ping_task.cancel()
            on_conn("bitget", "disconnect", tag)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            on_conn("bitget", "error", f"{tag} {ex!r}")
        attempt += 1
        if not stop.is_set():
            await _backoff(attempt)
