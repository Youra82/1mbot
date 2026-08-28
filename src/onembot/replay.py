# src/onembot/replay.py
"""
Historische Naeherungssimulation: "wie haette sich der Grid-Bot in den
letzten N Tagen verhalten?" -- ohne echten Tick-/Orderbuch-Verlauf (den
gibt es rueckwirkend nicht ueber die REST-API), approximiert anhand von
OHLCV-Kerzen.

Kernidee: SymbolWorker.on_tick() ist bereits datenquellen-agnostisch
(siehe run_loop.py-Docstring) -- ein "Tick" ist hier einfach
(best_bid=Candle-High, best_ask=Candle-Low) statt eines Live-Orderbuch-Ticks.
Dieselbe Grid/Regime/Inventory-Logik wie live, keine zweite Implementierung.

BEKANNTE VERZERRUNGEN dieser Naeherung (wichtig fuer die Interpretation
der Ergebnisse):
1. Optimistisch: "Kerze beruehrt Preis-Level" wird als sicherer Fill
   gewertet, ohne Beruecksichtigung von Orderbuch-Tiefe/Warteschlangen-
   position -- ein echter Post-Only-Order haette an derselben Stelle nicht
   zwingend gefuellt.
2. Reihenfolge unklar: wenn eine Kerze sowohl ein Buy- als auch ein
   Sell-Level beruehrt, ist unklar, welches zuerst geschah (High/Low
   sagen nichts ueber die Pfad-Reihenfolge innerhalb der Kerze aus).
3. Aufloesung: die Grid-Feinheit ist durch `fill_timeframe` begrenzt --
   je groeber die Kerze, desto mehr Touches werden uebersehen oder
   faelschlich zusammengefasst.

Kein Ersatz fuer den Live-Dry-Run, aber ein deutlich schnellerer erster
Blick als tagelanges Warten auf echte Fills.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .exchange.rest_client import RestClient
from .paper_broker import PaperBroker
from .risk.portfolio import PortfolioRiskManager
from .run_loop import SymbolWorker
from .utils.ledger import FillLedger
from .utils.symbol_settings import resolve_symbol_settings
from .utils.timeframes import timeframe_to_minutes
from .utils.timeframes import timeframe_to_timedelta as _timeframe_delta

logger = logging.getLogger(__name__)


async def replay_symbol(symbol: str, settings: dict, rest_client: RestClient, start: datetime, end: datetime,
                         fill_timeframe: str, ledger: FillLedger) -> int:
    """Simuliert einen Symbol-Verlauf zwischen `start` und `end`. Gibt die Anzahl der Fills zurueck."""
    settings = resolve_symbol_settings(settings, symbol)
    regime_cfg = settings["regime"]
    grid_cfg = settings["grid"]
    regime_tf = settings["regime_timeframe"]
    atr_tf = grid_cfg["atr_timeframe"]
    sr_cfg = grid_cfg.get("sr_zones", {})
    sr_enabled = sr_cfg.get("enabled", False)

    lookback_candles = max(regime_cfg["hurst_min_lookback"] + 10, 250)
    anchor_period = grid_cfg["anchor_ma_period"]
    atr_window = max(grid_cfg["atr_period"], anchor_period) + 50

    # Genug Vorlauf VOR dem eigentlichen Replay-Fenster laden, damit ab `start`
    # sofort ein vollstaendiges Regime/ATR-Fenster verfuegbar ist (kein
    # kuenstliches CHAOS am Anfang nur wegen Datenmangel).
    regime_warmup = _timeframe_delta(regime_tf) * lookback_candles
    atr_warmup = _timeframe_delta(atr_tf) * atr_window

    df_regime_full = rest_client.fetch_ohlcv_range(symbol, regime_tf, start - regime_warmup, end)
    df_atr_full = rest_client.fetch_ohlcv_range(symbol, atr_tf, start - atr_warmup, end)
    df_fill_full = rest_client.fetch_ohlcv_range(symbol, fill_timeframe, start, end)

    if df_regime_full.empty or df_atr_full.empty or df_fill_full.empty:
        logger.warning(f"{symbol}: keine ausreichenden historischen Daten fuer Replay -- uebersprungen.")
        return 0

    df_sr_full = None
    if sr_enabled:
        sr_tf = sr_cfg.get("timeframe", "15m")
        sr_warmup = timedelta(days=sr_cfg.get("lookback_days", 14))
        df_sr_full = rest_client.fetch_ohlcv_range(symbol, sr_tf, start - sr_warmup, end)
        if df_sr_full.empty:
            logger.warning(f"{symbol}: keine SR-Zonen-Daten -- laeuft ohne S/R-Filter weiter.")
            sr_enabled = False

    broker = PaperBroker(maker_fee_pct=settings["costs"]["maker_fee_pct"])
    # Eigene PortfolioRiskManager-Instanz pro Symbol: backtest_replay.py durchlaeuft
    # die Watchlist sequentiell (ein Symbol komplett, dann das naechste), nicht
    # zeitlich parallel wie im Live-Betrieb -- ein geteilter Cap ueber alle Symbole
    # waere hier nicht zeitlich ausgerichtet und wuerde spaetere Symbole faelschlich
    # durch das Endinventar frueherer Symbole blockieren. Der Portfolio-Cap ist damit
    # im Replay ein reiner Pro-Symbol-Cap (identisch zum Single-Symbol-Verhalten).
    portfolio_risk = PortfolioRiskManager(settings["risk"]["max_portfolio_inventory_usdt"])
    worker = SymbolWorker(
        symbol, settings, rest_client=None, ws_client=None, broker=broker,
        telegram_cfg={"send_status_updates": False}, ledger=ledger, portfolio_risk=portfolio_risk,
        persist_state=False,
    )

    regime_refresh = timedelta(minutes=settings["regime_refresh_minutes"])
    last_regime_recalc: datetime | None = None

    sr_refresh = timedelta(hours=sr_cfg.get("refresh_hours", 4)) if sr_enabled else None
    last_sr_recalc: datetime | None = None
    sr_lookback_candles = (
        int(sr_cfg.get("lookback_days", 14) * 24 * 60 / timeframe_to_minutes(sr_cfg.get("timeframe", "15m")))
        if sr_enabled else 0
    )

    daily_bias_enabled = grid_cfg.get("daily_bias", {}).get("enabled", False)

    for ts, candle in df_fill_full.iterrows():
        if daily_bias_enabled:
            # Rekonstruiert die "aktuelle, noch offene Tageskerze" rein aus bereits
            # geladenen Intraday-Daten -- kein Lookahead (nur bis `ts`), kein
            # zusaetzlicher REST-Call noetig (anders als Regime/SR-Zonen).
            day_slice = df_fill_full.loc[ts.normalize():ts]
            if not day_slice.empty:
                worker.apply_daily_bias(float(day_slice.iloc[0]["open"]), float(day_slice.iloc[-1]["close"]))

        if sr_enabled and (last_sr_recalc is None or (ts - last_sr_recalc) >= sr_refresh):
            # WICHTIG: nur Daten bis `ts` verwenden -- kein Lookahead (wie beim Regime-Slice unten).
            df_sr_slice = df_sr_full[df_sr_full.index <= ts].tail(sr_lookback_candles)
            worker.apply_sr_data(df_sr_slice)
            last_sr_recalc = ts

        if last_regime_recalc is None or (ts - last_regime_recalc) >= regime_refresh:
            # WICHTIG: nur Daten bis `ts` verwenden -- kein Lookahead in die Zukunft
            # der Simulation (der Klassiker aus den anderen Bots im Repo).
            df_regime_slice = df_regime_full[df_regime_full.index <= ts].tail(lookback_candles)
            df_atr_slice = df_atr_full[df_atr_full.index <= ts].tail(atr_window)
            exited_range = worker.apply_regime_data(df_regime_slice, df_atr_slice)
            if exited_range:
                await worker.cancel_all_orders()
            last_regime_recalc = ts

        await worker.on_tick(best_bid=float(candle["high"]), best_ask=float(candle["low"]), timestamp=ts.to_pydatetime())

    # Fill-Zaehlung ueber das Ledger nach Abschluss (statt Zwischenstand
    # mitzufuehren) -- einmaliger Read am Ende ist billig genug.
    return sum(1 for f in ledger.load() if f.get("symbol") == symbol)
