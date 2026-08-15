# src/onembot/exchange/ws_client.py
"""
ccxt.pro-Wrapper fuer Bitget: liefert die kontinuierlichen Marktdaten
(Orderbook-Ticks) fuer run_loop.py und paper_broker.py. Order-Platzierung
ist im Objekt vorhanden, aber hart durch live_trading gesperrt (Phase 1
ist reiner Dry-Run -- siehe paper_broker.py fuer die simulierte
Order-Ausfuehrung, die dasselbe Interface bedient).

Eine einzelne ccxt.pro-Instanz deckt sowohl watch_*-Streaming als auch
die spaeteren echten Order-Calls ab (ccxt.pro-Exchanges erben alle
Unified-Methoden der synchronen Variante).
"""
from __future__ import annotations

import logging
from typing import Optional

import ccxt.pro as ccxtpro

logger = logging.getLogger(__name__)


class LiveTradingDisabledError(RuntimeError):
    """Wird geworfen, wenn versucht wird, ueber WsClient eine echte Order zu platzieren,
    obwohl live_trading=false ist. Das ist eine Code-Sperre zusaetzlich zur Config --
    ein Config-Fehler allein kann damit keine echte Order ausloesen (siehe Plan)."""


class WsClient:
    def __init__(self, account_config: Optional[dict], live_trading: bool):
        creds = account_config or {}
        self.live_trading = live_trading
        self.exchange = ccxtpro.bitget({
            "apiKey": creds.get("apiKey"),
            "secret": creds.get("secret"),
            "password": creds.get("password"),
            "options": {"defaultType": "swap"},
            "enableRateLimit": True,
        })

    async def watch_order_book(self, symbol: str, limit: int = 5) -> dict:
        """Liefert {'bids': [[price, size], ...], 'asks': [[price, size], ...]}."""
        return await self.exchange.watch_order_book(symbol, limit=limit)

    async def create_limit_order(self, symbol: str, side: str, amount: float, price: float, params: dict | None = None) -> dict:
        if not self.live_trading:
            raise LiveTradingDisabledError(
                f"live_trading=false -- echte Order fuer {symbol} blockiert. "
                "In Phase 1 laeuft die Ausfuehrung ausschliesslich ueber paper_broker.PaperBroker."
            )
        params = {**(params or {}), "postOnly": True}
        return await self.exchange.create_order(symbol, "limit", side, amount, price, params)

    async def cancel_order(self, order_id: str, symbol: str) -> dict:
        if not self.live_trading:
            raise LiveTradingDisabledError(f"live_trading=false -- Order-Cancel fuer {symbol} blockiert.")
        return await self.exchange.cancel_order(order_id, symbol)

    async def close(self):
        await self.exchange.close()
