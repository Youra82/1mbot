# tests/test_run_loop.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

import pandas as pd

from onembot.paper_broker import PaperBroker
from onembot.risk.portfolio import PortfolioRiskManager
from onembot.run_loop import SymbolWorker
from onembot.strategy import grid_engine
from onembot.strategy.regime_gate import Regime
from onembot.utils import ledger as ledger_module
from onembot.utils.ledger import FillLedger


def make_settings():
    return {
        "regime": {
            "hurst_trend_min": 0.55,
            "adx_trend_min": 25.0,
            "hurst_range_max": 0.50,
            "adx_range_max": 20.0,
            "entropy_chaos_min": 0.97,
            "adx_strong_trend_block": 35.0,
            "hurst_min_lookback": 200,
        },
        "grid": {
            "atr_timeframe": "15m",
            "atr_period": 14,
            "spacing_atr_mult": 0.5,
            "levels_per_side": 2,
            "level_size_usdt": 6.0,
            "anchor_ma_period": 3,
        },
        "risk": {
            "max_net_inventory_usdt": 30.0,
            "inventory_skew_max": 0.6,
        },
    }


def make_worker(symbol, broker, portfolio_risk, ledger):
    return SymbolWorker(
        symbol, make_settings(), rest_client=None, ws_client=None, broker=broker,
        telegram_cfg={"send_status_updates": False}, ledger=ledger, portfolio_risk=portfolio_risk,
        persist_state=False,
    )


# ── apply_regime_data / cancel_all_orders (Eintritt in CHAOS muss offene Levels stornieren) ──

async def test_apply_regime_data_reports_entry_into_chaos_from_range():
    worker = make_worker("BTC/USDT:USDT", PaperBroker(), PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.regime = Regime.RANGE

    entered_chaos = worker.apply_regime_data(None, None)  # keine Daten -> faellt auf CHAOS zurueck

    assert entered_chaos is True
    assert worker.regime == Regime.CHAOS


async def test_apply_regime_data_reports_entry_into_chaos_from_trend():
    worker = make_worker("BTC/USDT:USDT", PaperBroker(), PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.regime = Regime.TREND

    entered_chaos = worker.apply_regime_data(None, None)

    assert entered_chaos is True
    assert worker.regime == Regime.CHAOS


async def test_apply_regime_data_no_entry_when_already_chaos():
    worker = make_worker("BTC/USDT:USDT", PaperBroker(), PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.regime = Regime.CHAOS

    entered_chaos = worker.apply_regime_data(None, None)

    assert entered_chaos is False


async def test_cancel_all_orders_clears_broker_and_local_state():
    broker = PaperBroker()
    worker = make_worker("BTC/USDT:USDT", broker, PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    level = grid_engine.GridLevel(side=grid_engine.Side.BUY, price=100.0, size_usdt=6.0, index=1)
    await worker._place_level(level)

    assert len(worker.active_orders) == 1
    assert len(broker.open_orders("BTC/USDT:USDT")) == 1

    await worker.cancel_all_orders()

    assert worker.active_orders == {}
    assert broker.open_orders("BTC/USDT:USDT") == []


# ── Portfolio-Exposure-Cap (Gesamt-Cap ueber alle Symbole, nicht nur pro Symbol) ──

async def test_build_grid_skipped_when_portfolio_over_cap():
    broker = PaperBroker()
    portfolio_risk = PortfolioRiskManager(max_portfolio_inventory_usdt=10.0)
    portfolio_risk.update("ETH/USDT:USDT", 50.0)  # anderes Symbol treibt die Portfolio-Exposure ueber den Cap
    worker = make_worker("BTC/USDT:USDT", broker, portfolio_risk, FillLedger("t.jsonl"))
    worker.spacing = 1.0
    worker._anchor_series = pd.Series([100.0, 100.0, 100.0])

    await worker._build_grid()

    assert worker.active_orders == {}
    assert broker.open_orders("BTC/USDT:USDT") == []


async def test_build_grid_allows_reducing_side_when_own_inventory_pushed_portfolio_over_cap():
    broker = PaperBroker(maker_fee_pct=0.0)
    portfolio_risk = PortfolioRiskManager(max_portfolio_inventory_usdt=10.0)
    worker = make_worker("BTC/USDT:USDT", broker, portfolio_risk, FillLedger("t.jsonl"))
    worker.spacing = 1.0
    worker._anchor_series = pd.Series([100.0, 100.0, 100.0])
    # BTC selbst haelt bereits long Inventory, das den Portfolio-Cap ueberschreitet.
    broker._apply_to_lots("BTC/USDT:USDT", "buy", 100.0, 0.15)  # net_inv = 15 USDT > 10 USDT Cap

    await worker._build_grid()

    # Kein kompletter Baustopp -- die abbauende Sell-Seite muss weiter platziert
    # werden koennen, sonst bleibt die ueberschuessige Position fuer immer stehen.
    assert all(lvl.side == grid_engine.Side.SELL for lvl in worker.active_orders.values())
    assert len(worker.active_orders) > 0


async def test_build_grid_places_levels_when_within_portfolio_cap():
    broker = PaperBroker()
    portfolio_risk = PortfolioRiskManager(max_portfolio_inventory_usdt=90.0)
    worker = make_worker("BTC/USDT:USDT", broker, portfolio_risk, FillLedger("t.jsonl"))
    worker.spacing = 1.0
    worker._anchor_series = pd.Series([100.0, 100.0, 100.0])

    await worker._build_grid()

    levels_per_side = make_settings()["grid"]["levels_per_side"]
    assert len(worker.active_orders) == 2 * levels_per_side


async def test_handle_fill_skips_replenish_when_portfolio_over_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "TRACKER_DIR", tmp_path)
    broker = PaperBroker(maker_fee_pct=0.0)
    portfolio_risk = PortfolioRiskManager(max_portfolio_inventory_usdt=5.0)
    worker = make_worker("BTC/USDT:USDT", broker, portfolio_risk, FillLedger("fills.jsonl"))
    worker.regime = Regime.RANGE
    worker.spacing = 1.0

    level = grid_engine.GridLevel(side=grid_engine.Side.BUY, price=100.0, size_usdt=6.0, index=1)
    order = await broker.create_limit_order("BTC/USDT:USDT", "buy", amount=0.06, price=100.0)
    worker.active_orders[order["id"]] = level
    filled = broker.check_fills("BTC/USDT:USDT", best_bid=99.0, best_ask=100.0)[0]

    await worker._handle_fill(filled, mid_price=100.0)

    # Fill allein bringt die Portfolio-Exposure (6 USDT) schon ueber den 5-USDT-Cap
    # -> die Sell-Gegenorder darf nicht nachgelegt werden.
    assert worker.active_orders == {}


async def test_handle_fill_replenishes_when_within_portfolio_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "TRACKER_DIR", tmp_path)
    broker = PaperBroker(maker_fee_pct=0.0)
    portfolio_risk = PortfolioRiskManager(max_portfolio_inventory_usdt=90.0)
    worker = make_worker("BTC/USDT:USDT", broker, portfolio_risk, FillLedger("fills.jsonl"))
    worker.regime = Regime.RANGE
    worker.spacing = 1.0

    level = grid_engine.GridLevel(side=grid_engine.Side.BUY, price=100.0, size_usdt=6.0, index=1)
    order = await broker.create_limit_order("BTC/USDT:USDT", "buy", amount=0.06, price=100.0)
    worker.active_orders[order["id"]] = level
    filled = broker.check_fills("BTC/USDT:USDT", best_bid=99.0, best_ask=100.0)[0]

    await worker._handle_fill(filled, mid_price=100.0)

    assert len(worker.active_orders) == 1  # Sell-Gegenorder wurde nachgelegt


# ── Grid handelt in RANGE UND TREND, nur CHAOS ist der Hard-Stop ──

async def test_handle_fill_replenishes_in_trend_regime_too(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "TRACKER_DIR", tmp_path)
    broker = PaperBroker(maker_fee_pct=0.0)
    portfolio_risk = PortfolioRiskManager(max_portfolio_inventory_usdt=90.0)
    worker = make_worker("BTC/USDT:USDT", broker, portfolio_risk, FillLedger("fills.jsonl"))
    worker.regime = Regime.TREND
    worker.spacing = 1.0

    level = grid_engine.GridLevel(side=grid_engine.Side.BUY, price=100.0, size_usdt=6.0, index=1)
    order = await broker.create_limit_order("BTC/USDT:USDT", "buy", amount=0.06, price=100.0)
    worker.active_orders[order["id"]] = level
    filled = broker.check_fills("BTC/USDT:USDT", best_bid=99.0, best_ask=100.0)[0]

    await worker._handle_fill(filled, mid_price=100.0)

    assert len(worker.active_orders) == 1  # Sell-Gegenorder wurde auch im TREND nachgelegt


async def test_handle_fill_skips_replenish_in_chaos_regime(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "TRACKER_DIR", tmp_path)
    broker = PaperBroker(maker_fee_pct=0.0)
    portfolio_risk = PortfolioRiskManager(max_portfolio_inventory_usdt=90.0)
    worker = make_worker("BTC/USDT:USDT", broker, portfolio_risk, FillLedger("fills.jsonl"))
    worker.regime = Regime.CHAOS
    worker.spacing = 1.0

    level = grid_engine.GridLevel(side=grid_engine.Side.BUY, price=100.0, size_usdt=6.0, index=1)
    order = await broker.create_limit_order("BTC/USDT:USDT", "buy", amount=0.06, price=100.0)
    worker.active_orders[order["id"]] = level
    filled = broker.check_fills("BTC/USDT:USDT", best_bid=99.0, best_ask=100.0)[0]

    await worker._handle_fill(filled, mid_price=100.0)

    assert worker.active_orders == {}  # CHAOS bleibt der einzige Hard-Stop, keine Gegenorder


async def test_on_tick_builds_grid_in_trend_regime():
    broker = PaperBroker()
    worker = make_worker("BTC/USDT:USDT", broker, PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.regime = Regime.TREND
    worker.spacing = 1.0
    worker._anchor_series = pd.Series([100.0, 100.0, 100.0])

    await worker.on_tick(best_bid=100.0, best_ask=100.0)

    levels_per_side = make_settings()["grid"]["levels_per_side"]
    assert len(worker.active_orders) == 2 * levels_per_side


async def test_on_tick_does_not_build_grid_in_chaos_regime():
    broker = PaperBroker()
    worker = make_worker("BTC/USDT:USDT", broker, PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.regime = Regime.CHAOS
    worker.spacing = 1.0
    worker._anchor_series = pd.Series([100.0, 100.0, 100.0])

    await worker.on_tick(best_bid=100.0, best_ask=100.0)

    assert worker.active_orders == {}


# ── Daily-Bias ("Tageskerze gruen -> long-only, rot -> short-only") ──

async def test_apply_daily_bias_green_candle_sets_buy_only():
    worker = make_worker("BTC/USDT:USDT", PaperBroker(), PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.settings["grid"]["daily_bias"] = {"enabled": True}

    worker.apply_daily_bias(day_open=100.0, day_close=101.0)

    assert worker.daily_bias_side == "buy"


async def test_apply_daily_bias_red_candle_sets_sell_only():
    worker = make_worker("BTC/USDT:USDT", PaperBroker(), PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.settings["grid"]["daily_bias"] = {"enabled": True}

    worker.apply_daily_bias(day_open=100.0, day_close=99.0)

    assert worker.daily_bias_side == "sell"


async def test_apply_daily_bias_flat_candle_sets_no_bias():
    worker = make_worker("BTC/USDT:USDT", PaperBroker(), PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.settings["grid"]["daily_bias"] = {"enabled": True}

    worker.apply_daily_bias(day_open=100.0, day_close=100.0)

    assert worker.daily_bias_side is None


async def test_apply_daily_bias_disabled_ignores_input():
    worker = make_worker("BTC/USDT:USDT", PaperBroker(), PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    # daily_bias fehlt in make_settings() -> Default enabled=False

    worker.apply_daily_bias(day_open=100.0, day_close=101.0)

    assert worker.daily_bias_side is None


async def test_build_grid_skips_sell_levels_when_daily_bias_is_buy_only():
    broker = PaperBroker()
    worker = make_worker("BTC/USDT:USDT", broker, PortfolioRiskManager(90.0), FillLedger("t.jsonl"))
    worker.settings["grid"]["daily_bias"] = {"enabled": True}
    worker.spacing = 1.0
    worker._anchor_series = pd.Series([100.0, 100.0, 100.0])
    worker.daily_bias_side = "buy"

    await worker._build_grid()

    assert worker.active_orders != {}
    assert all(lvl.side == grid_engine.Side.BUY for lvl in worker.active_orders.values())


async def test_handle_fill_skips_replenish_against_daily_bias(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "TRACKER_DIR", tmp_path)
    broker = PaperBroker(maker_fee_pct=0.0)
    worker = make_worker("BTC/USDT:USDT", broker, PortfolioRiskManager(90.0), FillLedger("fills.jsonl"))
    worker.settings["grid"]["daily_bias"] = {"enabled": True}
    worker.regime = Regime.RANGE
    worker.spacing = 1.0
    worker.daily_bias_side = "buy"  # long-only -> die Sell-Gegenorder nach einem Buy-Fill ist gesperrt

    level = grid_engine.GridLevel(side=grid_engine.Side.BUY, price=100.0, size_usdt=6.0, index=1)
    order = await broker.create_limit_order("BTC/USDT:USDT", "buy", amount=0.06, price=100.0)
    worker.active_orders[order["id"]] = level
    filled = broker.check_fills("BTC/USDT:USDT", best_bid=99.0, best_ask=100.0)[0]

    await worker._handle_fill(filled, mid_price=100.0)

    assert worker.active_orders == {}
