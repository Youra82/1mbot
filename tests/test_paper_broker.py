# tests/test_paper_broker.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

import pytest

from onembot.paper_broker import PaperBroker


async def test_buy_order_fills_when_ask_touches_price():
    broker = PaperBroker(maker_fee_pct=0.0)
    await broker.create_limit_order("BTC/USDT:USDT", "buy", amount=0.01, price=100.0)

    # best_ask noch ueber dem Order-Preis -> kein Fill
    assert broker.check_fills("BTC/USDT:USDT", best_bid=100.5, best_ask=100.5) == []

    # best_ask erreicht/unterschreitet den Order-Preis -> Fill
    fills = broker.check_fills("BTC/USDT:USDT", best_bid=99.5, best_ask=100.0)
    assert len(fills) == 1
    assert fills[0]["status"] == "filled"
    assert broker.open_orders("BTC/USDT:USDT") == []


async def test_sell_order_fills_when_bid_touches_price():
    broker = PaperBroker(maker_fee_pct=0.0)
    await broker.create_limit_order("BTC/USDT:USDT", "sell", amount=0.01, price=101.0)

    assert broker.check_fills("BTC/USDT:USDT", best_bid=100.5, best_ask=101.5) == []

    fills = broker.check_fills("BTC/USDT:USDT", best_bid=101.0, best_ask=101.5)
    assert len(fills) == 1


async def test_round_trip_realizes_spread_as_pnl():
    broker = PaperBroker(maker_fee_pct=0.0)
    symbol = "BTC/USDT:USDT"

    await broker.create_limit_order(symbol, "buy", amount=0.1, price=100.0)
    broker.check_fills(symbol, best_bid=99.5, best_ask=100.0)  # Buy-Fill, kein realisierter PnL noch

    assert broker.realized_pnl_usdt(symbol) == pytest.approx(0.0)
    assert broker.net_inventory_usdt(symbol, mark_price=100.0) == pytest.approx(10.0)  # 0.1 * 100

    await broker.create_limit_order(symbol, "sell", amount=0.1, price=101.0)
    broker.check_fills(symbol, best_bid=101.0, best_ask=101.5)  # Sell-Fill schliesst die Long-Lot

    # Spread von 1.0 USDT auf 0.1 Einheiten = 0.1 USDT realisierter Gewinn
    assert broker.realized_pnl_usdt(symbol) == pytest.approx(0.1)
    assert broker.net_inventory_usdt(symbol, mark_price=101.0) == pytest.approx(0.0)


async def test_maker_fee_reduces_realized_pnl():
    broker = PaperBroker(maker_fee_pct=0.02)  # 0.02% je Fill
    symbol = "BTC/USDT:USDT"

    await broker.create_limit_order(symbol, "buy", amount=1.0, price=100.0)
    broker.check_fills(symbol, best_bid=99.5, best_ask=100.0)
    await broker.create_limit_order(symbol, "sell", amount=1.0, price=101.0)
    fills = broker.check_fills(symbol, best_bid=101.0, best_ask=101.5)

    expected_fee_sell = 101.0 * 1.0 * (0.02 / 100.0)
    # Bruttogewinn 1.0 USDT, abzueglich Sell-Fee (Buy-Fee wurde bereits beim ersten Fill abgezogen)
    assert fills[0]["realized_pnl_usdt"] == pytest.approx(1.0 - expected_fee_sell)


async def test_unrealized_pnl_for_open_long_lot():
    broker = PaperBroker(maker_fee_pct=0.0)
    symbol = "BTC/USDT:USDT"
    await broker.create_limit_order(symbol, "buy", amount=0.1, price=100.0)
    broker.check_fills(symbol, best_bid=99.5, best_ask=100.0)

    assert broker.unrealized_pnl_usdt(symbol, mark_price=110.0) == pytest.approx(1.0)  # 0.1*(110-100)
    assert broker.unrealized_pnl_usdt(symbol, mark_price=90.0) == pytest.approx(-1.0)


async def test_unrealized_pnl_for_open_short_lot():
    broker = PaperBroker(maker_fee_pct=0.0)
    symbol = "BTC/USDT:USDT"
    await broker.create_limit_order(symbol, "sell", amount=0.1, price=100.0)
    broker.check_fills(symbol, best_bid=100.0, best_ask=100.5)

    assert broker.unrealized_pnl_usdt(symbol, mark_price=90.0) == pytest.approx(1.0)  # 0.1*(100-90)
    assert broker.unrealized_pnl_usdt(symbol, mark_price=110.0) == pytest.approx(-1.0)


async def test_unrealized_pnl_zero_without_open_lots():
    broker = PaperBroker(maker_fee_pct=0.0)
    assert broker.unrealized_pnl_usdt("BTC/USDT:USDT", mark_price=100.0) == 0.0


async def test_cancel_order_removes_open_order():
    broker = PaperBroker()
    order = await broker.create_limit_order("BTC/USDT:USDT", "buy", amount=0.01, price=100.0)
    canceled = await broker.cancel_order(order["id"], "BTC/USDT:USDT")

    assert canceled["status"] == "canceled"
    assert broker.open_orders("BTC/USDT:USDT") == []
    assert broker.check_fills("BTC/USDT:USDT", best_bid=99.0, best_ask=99.5) == []
