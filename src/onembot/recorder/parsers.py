"""Reine Parser fuer Websocket-Nachrichten (ohne I/O, damit testbar).

Rueckgabe jeweils eine Liste normalisierter Events:
  ("book",  symbol, exch_ms, bid, bid_sz, ask, ask_sz)
  ("trade", symbol, exch_ms, px, qty, is_buy)
  ("liq",   symbol, exch_ms, side, qty, price, avg_price, status)
"""
from __future__ import annotations


def parse_binance(msg: dict) -> list[tuple]:
    d = msg.get("data", msg)  # combined stream: {"stream":..., "data":{...}}
    e = d.get("e")
    if e == "bookTicker":
        return [("book", d["s"], int(d.get("T") or d.get("E")),
                 float(d["b"]), float(d["B"]), float(d["a"]), float(d["A"]))]
    if e == "aggTrade":
        # m = Kaeufer ist Maker -> Aggressor hat verkauft
        return [("trade", d["s"], int(d["T"]), float(d["p"]), float(d["q"]), not d["m"])]
    if e == "forceOrder":
        o = d["o"]
        return [("liq", o["s"], int(o["T"]), o["S"], float(o["q"]), float(o["p"]),
                 float(o.get("ap") or 0), o.get("X", ""))]
    return []


def parse_bitget(msg: dict) -> list[tuple]:
    arg = msg.get("arg") or {}
    ch = arg.get("channel")
    data = msg.get("data") or []
    sym = arg.get("instId")
    out: list[tuple] = []
    if ch == "books1":
        for d in data:
            if not d.get("bids") or not d.get("asks"):
                continue
            b, a = d["bids"][0], d["asks"][0]
            out.append(("book", sym, int(d["ts"]), float(b[0]), float(b[1]),
                        float(a[0]), float(a[1])))
    elif ch == "trade":
        # Beim Abonnieren schickt Bitget einen Snapshot alter Trades -> ignorieren,
        # sonst landen historische Trades doppelt/verspaetet in den Sekunden-Buckets.
        if msg.get("action") == "snapshot":
            return []
        for d in data:
            out.append(("trade", sym, int(d["ts"]), float(d["price"]), float(d["size"]),
                        d.get("side") == "buy"))
    return out
