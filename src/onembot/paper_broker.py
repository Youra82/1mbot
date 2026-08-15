# src/onembot/paper_broker.py
"""
Simulierter Broker fuer Phase 1 (Dry-Run): bedient dasselbe
create_limit_order/cancel_order-Interface wie exchange.ws_client.WsClient,
matched Fills aber nur gegen echte, live gestreamte Orderbook-Ticks --
keine echten Order-Calls. Phase 2 tauscht nur die Broker-Instanz aus,
run_loop.py bleibt unveraendert.

Fill-Simulation ist bewusst konservativ (Touch-basiert): ein Buy-Limit
gilt erst als gefuellt, wenn der best_ask tatsaechlich auf/unter den
Order-Preis gehandelt hat (nicht schon, wenn der Preis nur in die Naehe
kommt) -- optimistischere Annahmen wuerden die Paper-Ergebnisse schoenen.

PnL-Tracking per FIFO-Lot-Matching pro Symbol, inkl. Maker-Fee.
"""
from __future__ import annotations

import itertools
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class PaperBroker:
    def __init__(self, maker_fee_pct: float = 0.02):
        self.maker_fee_pct = maker_fee_pct
        self._orders: dict[str, dict] = {}
        self._lots: dict[str, list[tuple[int, float, float]]] = {}  # symbol -> [(sign, price, amount), ...] FIFO
        self._realized_pnl: dict[str, float] = {}
        self._order_seq = itertools.count(1)

    async def create_limit_order(self, symbol: str, side: str, amount: float, price: float, params: Optional[dict] = None) -> dict:
        order_id = f"paper-{next(self._order_seq)}"
        order = {"id": order_id, "symbol": symbol, "side": side, "amount": amount, "price": price, "status": "open"}
        self._orders[order_id] = order
        return order

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> dict:
        order = self._orders.pop(order_id, None)
        if order:
            order["status"] = "canceled"
        return order or {}

    def open_orders(self, symbol: str) -> list[dict]:
        return [o for o in self._orders.values() if o["symbol"] == symbol and o["status"] == "open"]

    def check_fills(self, symbol: str, best_bid: float, best_ask: float) -> list[dict]:
        """Prueft alle offenen Orders eines Symbols gegen die aktuellen
        Orderbook-Touches und fuellt sie ggf. Rueckgabe: Liste gefuellter
        Order-Dicts inkl. 'realized_pnl_usdt'."""
        filled = []
        for order_id, order in list(self._orders.items()):
            if order["symbol"] != symbol or order["status"] != "open":
                continue
            if order["side"] == "buy" and best_ask <= order["price"]:
                filled.append(self._fill(order_id, order))
            elif order["side"] == "sell" and best_bid >= order["price"]:
                filled.append(self._fill(order_id, order))
        return filled

    def _fill(self, order_id: str, order: dict) -> dict:
        order["status"] = "filled"
        del self._orders[order_id]
        fee = order["price"] * order["amount"] * (self.maker_fee_pct / 100.0)
        realized = self._apply_to_lots(order["symbol"], order["side"], order["price"], order["amount"]) - fee
        self._realized_pnl[order["symbol"]] = self._realized_pnl.get(order["symbol"], 0.0) + realized
        return {**order, "realized_pnl_usdt": realized}

    def _apply_to_lots(self, symbol: str, side: str, price: float, amount: float) -> float:
        sign = 1 if side == "buy" else -1
        lots = self._lots.setdefault(symbol, [])
        realized = 0.0
        remaining = amount

        while remaining > 1e-12 and lots and lots[0][0] == -sign:
            lot_sign, lot_price, lot_amount = lots[0]
            matched = min(remaining, lot_amount)
            if sign == 1:  # Buy schliesst eine Short-Lot -> Gewinn wenn lot_price > price
                realized += (lot_price - price) * matched
            else:  # Sell schliesst eine Long-Lot -> Gewinn wenn price > lot_price
                realized += (price - lot_price) * matched
            remaining -= matched
            if matched >= lot_amount - 1e-12:
                lots.pop(0)
            else:
                lots[0] = (lot_sign, lot_price, lot_amount - matched)

        if remaining > 1e-12:
            lots.append((sign, price, remaining))
        return realized

    def net_inventory_usdt(self, symbol: str, mark_price: float) -> float:
        lots = self._lots.get(symbol, [])
        net_amount = sum(sign * amount for sign, _, amount in lots)
        return net_amount * mark_price

    def realized_pnl_usdt(self, symbol: str) -> float:
        return self._realized_pnl.get(symbol, 0.0)

    def unrealized_pnl_usdt(self, symbol: str, mark_price: float) -> float:
        """Mark-to-Market-PnL der noch offenen Lots (nicht der Notional-Wert
        wie net_inventory_usdt, sondern der tatsaechliche Gewinn/Verlust
        gegenueber dem jeweiligen Einstandspreis)."""
        lots = self._lots.get(symbol, [])
        return sum(sign * (mark_price - price) * amount for sign, price, amount in lots)
