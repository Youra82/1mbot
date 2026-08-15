# src/onembot/run_loop.py
"""
Asyncio-Orchestrierung: ein SymbolWorker pro Watchlist-Symbol, jeweils
zwei nebenlaeufige Loops:

- market_loop: haengt dauerhaft an watch_order_book (Websocket) und
  reagiert sofort auf Preisbewegung -- baut den Grid auf/prueft Fills.
- regime_loop: periodischer REST-Refresh (Hurst/ADX brauchen Kerzen-
  Aggregate, keine Tick-Daten) alle `regime_refresh_minutes`.

Phase 1: Fills laufen ausschliesslich ueber paper_broker.PaperBroker
(Dry-Run). live_trading wird zusaetzlich hart in ws_client.WsClient
geprueft, s. dort.

SymbolWorker trennt bewusst "woher kommt ein Preis-Tick" (Websocket live
vs. historische Kerze) von der eigentlichen Grid/Regime/Inventory-Logik:
on_tick() und apply_regime_data() kennen ihre Datenquelle nicht. Dadurch
kann replay.py denselben Code fuer die historische Naeherungssimulation
wiederverwenden, statt eine zweite, potenziell abweichende Grid-Logik zu
pflegen -- genau die Art von Divergenz, die in anderen Bots im Repo schon
zu Live-vs-Backtest-Ueberraschungen gefuehrt hat.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from .exchange.rest_client import RestClient
from .exchange.ws_client import WsClient
from .paper_broker import PaperBroker
from .risk.inventory import InventoryState, blocked_side, compute_skew
from .risk.portfolio import PortfolioRiskManager
from .strategy import grid_engine, sr_zones
from .strategy.regime_gate import Regime, classify_regime, compute_atr
from .utils import state as state_store
from .utils.ledger import FillLedger
from .utils.telegram import send_message
from .utils.timeframes import timeframe_to_minutes

logger = logging.getLogger(__name__)


class SymbolWorker:
    def __init__(self, symbol: str, settings: dict, rest_client: RestClient | None, ws_client: WsClient | None,
                 broker: PaperBroker, telegram_cfg: dict, ledger: FillLedger, portfolio_risk: PortfolioRiskManager,
                 persist_state: bool = True):
        self.symbol = symbol
        self.settings = settings
        self.rest = rest_client
        self.ws = ws_client
        self.broker = broker
        self.telegram_cfg = telegram_cfg
        self.ledger = ledger
        self.portfolio_risk = portfolio_risk
        self.persist_state = persist_state

        self.regime: Regime = Regime.CHAOS
        self.atr: float | None = None
        self.spacing: float | None = None
        self.active_orders: dict[str, grid_engine.GridLevel] = {}
        self._anchor_series = None
        self.sr_zones: list[sr_zones.Zone] = []
        self.daily_bias_side: str | None = None
        self.last_mark_price: float | None = None

    def _notify(self, message: str) -> None:
        if not self.telegram_cfg.get("send_status_updates"):
            return
        send_message(self.telegram_cfg.get("bot_token"), self.telegram_cfg.get("chat_id"), f"[{self.symbol}] {message}")

    def apply_regime_data(self, df_regime, df_atr) -> bool:
        """
        Verarbeitet bereits geladene OHLCV-Daten zu Regime/ATR/Anker.
        Kennt nicht, ob die Daten frisch per REST geholt (live) oder aus
        einer historischen Serie geschnitten wurden (replay) -- solange
        der Aufrufer keine Zukunftsdaten hineinschneidet (siehe replay.py),
        ist das Verhalten identisch zum Live-Pfad.

        Das Grid handelt in RANGE UND TREND (siehe on_tick/_handle_fill) --
        auf 1-Minuten-Aufloesung ist die Kursbewegung fast immer von
        Mikrostruktur-Rauschen dominiert, das der Grid unabhaengig vom
        uebergeordneten Trend abschoepfen soll (das Inventory-Cap in
        risk/inventory.py haelt die Trend-Exposure trotzdem begrenzt).
        Nur CHAOS ist der Hard-Stop -- Rueckgabe: True, wenn das Regime durch
        diesen Aufruf NEU in CHAOS gewechselt ist. Der Aufrufer muss dann
        await cancel_all_orders() aufrufen, um noch offene Grid-Levels zu
        stornieren.
        """
        regime_cfg = self.settings["regime"]
        grid_cfg = self.settings["grid"]
        atr_period = grid_cfg["atr_period"]
        was_tradeable = self.regime != Regime.CHAOS

        if df_regime is None or df_atr is None or df_regime.empty or df_atr.empty:
            logger.warning(f"{self.symbol}: keine OHLCV-Daten fuer Regime/ATR erhalten -- bleibe bei CHAOS.")
            self.regime = Regime.CHAOS
            return was_tradeable

        new_state = classify_regime(df_regime, regime_cfg, previous_regime=self.regime)
        atr_series = compute_atr(df_atr, atr_period)
        atr_value = float(atr_series.iloc[-1]) if not atr_series.empty and atr_series.notna().iloc[-1] else None

        if atr_value is None or atr_value <= 0:
            logger.warning(f"{self.symbol}: ATR nicht berechenbar -- bleibe bei CHAOS.")
            self.regime = Regime.CHAOS
            return was_tradeable

        if new_state.regime != self.regime:
            self._notify(f"Regime-Wechsel {self.regime.value} -> {new_state.regime.value} "
                          f"(hurst={new_state.hurst:.2f}, adx={new_state.adx:.1f})")

        self.regime = new_state.regime
        self.atr = atr_value
        self.spacing = grid_engine.compute_spacing(atr_value, grid_cfg["spacing_atr_mult"])
        self._anchor_series = df_atr["close"]
        return was_tradeable and new_state.regime == Regime.CHAOS

    def apply_sr_data(self, df_sr) -> None:
        """
        Verarbeitet bereits geladene OHLCV-Daten (groesserer Timeframe/
        Lookback als Regime/ATR, siehe settings.grid.sr_zones) zu
        Support-/Resistance-Zonen. Wie apply_regime_data() datenquellen-
        agnostisch -- replay.py und der Live-Pfad rufen dieselbe Methode.
        """
        sr_cfg = self.settings["grid"].get("sr_zones", {})
        if not sr_cfg.get("enabled", False):
            return
        if df_sr is None or df_sr.empty:
            return
        self.sr_zones = sr_zones.find_zones(
            df_sr,
            window=sr_cfg.get("swing_window", 3),
            tolerance_pct=sr_cfg.get("tolerance_pct", 0.0015),
            min_touches=sr_cfg.get("min_touches", 2),
        )

    def apply_daily_bias(self, day_open: float | None, day_close: float | None) -> None:
        """
        Reine Richtungs-Heuristik ("Tageskerze gruen -> long-only, rot ->
        short-only"), datenquellen-agnostisch wie apply_regime_data/
        apply_sr_data: nimmt bereits ermittelte Open/Close-Werte der
        AKTUELLEN (noch nicht abgeschlossenen) Tageskerze entgegen, statt
        selbst zu fetchen -- replay.py rekonstruiert diese aus bereits
        geladenen Intraday-Kerzen (kein Lookahead), der Live-Pfad holt sie
        per REST. Blockiert in _build_grid zusaetzlich die Gegenseite,
        ersetzt NICHT die Regime-/Cap-Logik.
        """
        daily_cfg = self.settings["grid"].get("daily_bias", {})
        if not daily_cfg.get("enabled", False) or day_open is None or day_close is None:
            self.daily_bias_side = None
            return
        if day_close > day_open:
            self.daily_bias_side = "buy"
        elif day_close < day_open:
            self.daily_bias_side = "sell"
        else:
            self.daily_bias_side = None

    async def cancel_all_orders(self) -> None:
        """Storniert alle noch offenen Grid-Levels (siehe apply_regime_data)."""
        if not self.active_orders:
            return
        n = len(self.active_orders)
        for order_id in list(self.active_orders.keys()):
            await self.broker.cancel_order(order_id, self.symbol)
        self.active_orders.clear()
        logger.info(f"{self.symbol}: {n} offene Grid-Levels storniert (Regime wechselte zu CHAOS).")

    async def refresh_regime(self) -> None:
        """Live-Pfad: holt frische OHLCV per REST und delegiert an apply_regime_data."""
        regime_cfg = self.settings["regime"]
        regime_tf = self.settings["regime_timeframe"]
        lookback = max(regime_cfg["hurst_min_lookback"] + 10, 250)
        df_regime = await asyncio.to_thread(self.rest.fetch_ohlcv, self.symbol, regime_tf, lookback)

        grid_cfg = self.settings["grid"]
        atr_tf = grid_cfg["atr_timeframe"]
        atr_period = grid_cfg["atr_period"]
        anchor_period = grid_cfg["anchor_ma_period"]
        df_atr = await asyncio.to_thread(
            self.rest.fetch_ohlcv, self.symbol, atr_tf, max(atr_period, anchor_period) + 50
        )

        exited_range = self.apply_regime_data(df_regime, df_atr)
        if exited_range:
            await self.cancel_all_orders()

    async def regime_loop(self) -> None:
        interval = self.settings["regime_refresh_minutes"] * 60
        while True:
            try:
                await self.refresh_regime()
            except Exception as e:
                logger.error(f"{self.symbol}: Fehler im Regime-Refresh: {e}")
            await asyncio.sleep(interval)

    async def refresh_sr_zones(self) -> None:
        """Live-Pfad: holt frische OHLCV per REST und delegiert an apply_sr_data."""
        sr_cfg = self.settings["grid"].get("sr_zones", {})
        timeframe = sr_cfg.get("timeframe", "15m")
        lookback_days = sr_cfg.get("lookback_days", 14)
        lookback_candles = int(lookback_days * 24 * 60 / timeframe_to_minutes(timeframe))
        df_sr = await asyncio.to_thread(self.rest.fetch_ohlcv, self.symbol, timeframe, lookback_candles)
        self.apply_sr_data(df_sr)

    async def sr_loop(self) -> None:
        sr_cfg = self.settings["grid"].get("sr_zones", {})
        interval = sr_cfg.get("refresh_hours", 4) * 3600
        while True:
            try:
                await self.refresh_sr_zones()
            except Exception as e:
                logger.error(f"{self.symbol}: Fehler im SR-Zonen-Refresh: {e}")
            await asyncio.sleep(interval)

    async def refresh_daily_bias(self) -> None:
        """Live-Pfad: holt die aktuelle (noch offene) Tageskerze per REST."""
        df_daily = await asyncio.to_thread(self.rest.fetch_ohlcv, self.symbol, "1d", 1)
        if df_daily is None or df_daily.empty:
            self.apply_daily_bias(None, None)
            return
        row = df_daily.iloc[-1]
        self.apply_daily_bias(float(row["open"]), float(row["close"]))

    async def daily_bias_loop(self) -> None:
        interval = self.settings["grid"].get("daily_bias", {}).get("refresh_minutes", 15) * 60
        while True:
            try:
                await self.refresh_daily_bias()
            except Exception as e:
                logger.error(f"{self.symbol}: Fehler im Daily-Bias-Refresh: {e}")
            await asyncio.sleep(interval)

    def _current_anchor(self) -> float | None:
        anchor_period = self.settings["grid"]["anchor_ma_period"]
        series = self._anchor_series
        if series is None or len(series) < anchor_period:
            return None
        return float(series.rolling(anchor_period).mean().iloc[-1])

    async def _place_level(self, level: grid_engine.GridLevel) -> None:
        amount = level.size_usdt / level.price
        order = await self.broker.create_limit_order(self.symbol, level.side.value, amount, level.price)
        self.active_orders[order["id"]] = level

    async def _build_grid(self) -> None:
        anchor = self._current_anchor()
        if anchor is None or self.spacing is None:
            return

        net_inv = self.broker.net_inventory_usdt(self.symbol, anchor)
        self.portfolio_risk.update(self.symbol, net_inv)

        grid_cfg = self.settings["grid"]
        levels_per_side = grid_cfg["levels_per_side"]

        if grid_cfg.get("sr_zones", {}).get("enabled", False) and self.sr_zones:
            zone_prices_below = [z.price for z in sr_zones.zones_below(anchor, self.sr_zones)]
            zone_prices_above = [z.price for z in sr_zones.zones_above(anchor, self.sr_zones)]
            levels = grid_engine.build_levels_from_zones(
                anchor, zone_prices_below, zone_prices_above,
                levels_per_side, grid_cfg["level_size_usdt"], self.spacing,
            )
        else:
            levels = grid_engine.build_levels(anchor, self.spacing, levels_per_side, grid_cfg["level_size_usdt"])

        skew = compute_skew(
            InventoryState(self.symbol, net_inv),
            self.settings["risk"]["max_net_inventory_usdt"],
            self.settings["risk"]["inventory_skew_max"],
        )
        levels = grid_engine.apply_inventory_skew(levels, skew)

        blocked_sides: set[str] = set()
        symbol_blocked = blocked_side(InventoryState(self.symbol, net_inv), self.settings["risk"]["max_net_inventory_usdt"])
        if symbol_blocked:
            blocked_sides.add(symbol_blocked)

        if self.portfolio_risk.is_over_cap():
            if net_inv == 0:
                # Dieses Symbol traegt selbst nichts zur Cap-Ueberschreitung bei
                # (kein offenes Inventory) -- ein frischer Grid wuerde die
                # bereits ueberschrittene Portfolio-Exposure nur weiter erhoehen.
                logger.info(f"{self.symbol}: Grid-Aufbau uebersprungen -- Portfolio-Exposure-Cap "
                            f"({self.portfolio_risk.total_exposure_usdt():.2f}/"
                            f"{self.portfolio_risk.max_portfolio_inventory_usdt:.2f} USDT) erreicht.")
                return
            # Portfolio-Cap ueberschritten UND dieses Symbol haelt Inventory bei --
            # nur die Seite zulassen, die dieses Inventory abbaut. Ein kompletter
            # Baustopp wuerde die Position sonst dauerhaft festhalten, weil auch
            # die abbauende Gegenorder nie platziert werden koennte (siehe
            # Kalibrierung mit knappem Kapital: Cap einmal knapp ueberschritten,
            # Grid blieb danach den ganzen Tag tot).
            blocked_sides.add("buy" if net_inv > 0 else "sell")

        if self.daily_bias_side is not None:
            # Tageskerze gruen -> nur Buy (long-only), rot -> nur Sell (short-only).
            blocked_sides.add("sell" if self.daily_bias_side == "buy" else "buy")

        for level in levels:
            if level.size_usdt <= 0 or level.side.value in blocked_sides:
                continue
            await self._place_level(level)

        logger.info(f"{self.symbol}: Grid aufgebaut ({len(self.active_orders)} Levels, Anker={anchor:.4f}, Spacing={self.spacing:.4f})")

    async def _handle_fill(self, filled_order: dict, mid_price: float, timestamp: datetime | None = None) -> None:
        level = self.active_orders.pop(filled_order["id"], None)
        if level is None:
            return

        pnl = filled_order.get("realized_pnl_usdt", 0.0)
        self._notify(f"Fill {level.side.value} @ {filled_order['price']:.4f} "
                     f"(realized PnL diese Order: {pnl:+.4f} USDT)")

        ts = timestamp if timestamp is not None else datetime.now(timezone.utc)
        self.ledger.append({
            "symbol": self.symbol,
            "side": level.side.value,
            "price": filled_order["price"],
            "amount": filled_order["amount"],
            "realized_pnl_usdt": pnl,
            "regime": self.regime.value,
            "timestamp": ts.isoformat(),
        })

        net_inv = self.broker.net_inventory_usdt(self.symbol, mid_price)
        self.portfolio_risk.update(self.symbol, net_inv)
        blocked = blocked_side(
            InventoryState(self.symbol, net_inv), self.settings["risk"]["max_net_inventory_usdt"]
        )
        portfolio_over_cap = self.portfolio_risk.is_over_cap()

        daily_bias_blocked = (
            self.daily_bias_side is not None
            and ("sell" if self.daily_bias_side == "buy" else "buy")
        )

        if self.regime != Regime.CHAOS and self.spacing is not None:
            new_level = grid_engine.replenish_after_fill(level, self.spacing)
            if blocked == new_level.side.value:
                logger.info(f"{self.symbol}: Replenish {new_level.side.value} wegen Symbol-Inventory-Cap uebersprungen.")
            elif portfolio_over_cap:
                logger.info(f"{self.symbol}: Replenish {new_level.side.value} wegen Portfolio-Exposure-Cap uebersprungen.")
            elif daily_bias_blocked == new_level.side.value:
                logger.info(f"{self.symbol}: Replenish {new_level.side.value} wegen Daily-Bias ({self.daily_bias_side}-only) uebersprungen.")
            else:
                await self._place_level(new_level)

        self.persist_state_snapshot()

    def persist_state_snapshot(self) -> None:
        """
        Schreibt den aktuellen Symbol-Zustand nach artifacts/tracker/ --
        inkl. Mark-Price und Unrealized-PnL (nicht nur bei Fills, siehe
        state_loop() im Live-Pfad), damit show_results.py/watch_balance.py
        einen echten LIVE-Kontostand berechnen koennen statt eines
        Stands, der nur bei jedem Fill aktualisiert wird und zwischen
        Fills veraltet (Preis kann sich bewegen, ohne dass ein Level
        beruehrt wird).
        """
        if not self.persist_state:
            return
        mark_price = self.last_mark_price
        net_inv = self.broker.net_inventory_usdt(self.symbol, mark_price) if mark_price else 0.0
        unrealized = self.broker.unrealized_pnl_usdt(self.symbol, mark_price) if mark_price else 0.0
        state_store.save_state(self.symbol, {
            "symbol": self.symbol,
            "regime": self.regime.value,
            "mark_price": mark_price,
            "net_inventory_usdt": net_inv,
            "unrealized_pnl_usdt": unrealized,
            "realized_pnl_usdt": self.broker.realized_pnl_usdt(self.symbol),
            "open_orders": len(self.active_orders),
        })

    async def state_loop(self, interval_seconds: int = 10) -> None:
        """Periodisches Snapshot-Update unabhaengig von Fills -- s. persist_state_snapshot()."""
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                self.persist_state_snapshot()
            except Exception as e:
                logger.error(f"{self.symbol}: Fehler beim State-Snapshot: {e}")

    async def on_tick(self, best_bid: float, best_ask: float, timestamp: datetime | None = None) -> None:
        """
        Ein Preis-Tick, unabhaengig von der Quelle (Live-Websocket oder
        historische Kerze via replay.py). Fuer Kerzen wird best_bid=High,
        best_ask=Low uebergeben (siehe replay.py-Docstring fuer die
        Begruendung/Verzerrung dieser Naeherung).
        """
        mid = (best_bid + best_ask) / 2
        self.last_mark_price = mid

        if self.regime != Regime.CHAOS and not self.active_orders:
            await self._build_grid()

        fills = self.broker.check_fills(self.symbol, best_bid, best_ask)
        for filled_order in fills:
            await self._handle_fill(filled_order, mid, timestamp)

    async def market_loop(self) -> None:
        while True:
            try:
                book = await self.ws.watch_order_book(self.symbol, limit=5)
            except Exception as e:
                logger.error(f"{self.symbol}: Websocket-Fehler: {e}")
                await asyncio.sleep(2)
                continue

            bids, asks = book.get("bids") or [], book.get("asks") or []
            if not bids or not asks:
                continue
            await self.on_tick(bids[0][0], asks[0][0])

    async def run(self) -> None:
        await self.refresh_regime()
        loops = [self.market_loop(), self.regime_loop()]
        if self.persist_state:
            loops.append(self.state_loop())
        if self.settings["grid"].get("sr_zones", {}).get("enabled", False):
            await self.refresh_sr_zones()
            loops.append(self.sr_loop())
        if self.settings["grid"].get("daily_bias", {}).get("enabled", False):
            await self.refresh_daily_bias()
            loops.append(self.daily_bias_loop())
        await asyncio.gather(*loops)


async def run_bot(settings: dict, account_config: dict | None, telegram_cfg: dict) -> None:
    rest_client = RestClient(account_config)
    ws_client = WsClient(account_config, live_trading=settings["live_trading"])
    broker = PaperBroker(maker_fee_pct=settings["costs"]["maker_fee_pct"])
    ledger = FillLedger("fills.jsonl")
    portfolio_risk = PortfolioRiskManager(settings["risk"]["max_portfolio_inventory_usdt"])

    workers = [
        SymbolWorker(symbol, settings, rest_client, ws_client, broker, telegram_cfg, ledger, portfolio_risk)
        for symbol in settings["watchlist"]
    ]

    try:
        await asyncio.gather(*(w.run() for w in workers))
    finally:
        await ws_client.close()
