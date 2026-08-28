#!/usr/bin/env python3
"""
Optuna-Parametersuche fuer die Grid-Settings, EIN Study pro Symbol.

Warum pro Symbol: das README-Kalibrierungskapitel zeigt bereits, dass BTC
und ETH bei identischen Settings unterschiedlich abschneiden -- ein
gemeinsamer globaler Parametersatz fuer die ganze Watchlist ist strukturell
ein Kompromiss. Das Ergebnis landet als Override in
settings.json["per_symbol_overrides"][symbol]["grid"], aufgeloest ueber
utils/symbol_settings.py -- DIESELBE Funktion, die auch der Live-Pfad
(run_loop.py::run_bot) benutzt, damit ein optimiertes Symbol live exakt so
handelt wie im Training bewertet.

Zielfunktion ("Strict"-Modus, analog zu ltbbot/stbot): maximiert den Gesamt-
PnL ueber ein Multi-Day-Backtest-Fenster (siehe backtest_multiday.py), aber
nur Trials, die zusaetzlich
  (a) eine Mindest-Gewinntage-Quote und
  (b) eine Mindest-Fill-Zahl
erreichen, gelten als gueltig. Ohne (b) waere ein spacing_atr_mult, das so
hoch ist, dass praktisch nie gehandelt wird, ein triviales "perfektes"
0-Fills/0-Verlust-Optimum -- nicht das, was hier gesucht wird (vgl.
Erfahrung aus anderen Bots im Repo: bei zu wenig Signalen zuerst die
Trade-Menge fixen, nicht die Profitabilitaet auf Basis von ~0 Trades
bewerten).

Sucht NUR ueber Parameter, die den benoetigten historischen Warmup-Zeitraum
NICHT veraendern (spacing_atr_mult, levels_per_side, sr_zones.*) -- atr_period,
anchor_ma_period und sr_zones.lookback_days bleiben pro Symbol fix. Dadurch
ist die (symbol, timeframe, since, until)-Kombination fuer jeden REST-Abruf
ueber ALLE Trials einer Studie identisch, und der In-Memory-Cache unten
(CachingRestClient) spart nach dem ersten Trial praktisch jeden weiteren
Netzwerk-Call -- ohne das waeren z.B. 60 Trials x 20 Tage x 3-4 Timeframes
komplett neu abgerufene Historie pro Symbol.

Benutzung (typischerweise ueber run_pipeline.sh):
    python optimizer.py --start 2026-07-19 --end 2026-08-14 --count 20 \
        --trials 60 --min-win-ratio 0.5 --min-fills 20 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import optuna  # noqa: E402

from backtest_multiday import EXCHANGE_OPTIONS, run_multiday  # noqa: E402
from onembot.exchange.rest_client import RestClient  # noqa: E402

ROOT = Path(__file__).resolve().parent

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger().setLevel(logging.WARNING)  # backtest_multiday.py hat beim Import bereits INFO
# gesetzt -- logging.basicConfig() ist danach ein No-Op, ohne das explizite setLevel() wuerden
# Dutzende Trials lang "Grid aufgebaut"/"storniert"-Meldungen aus run_loop.py/grid_engine.py den
# eigentlich interessanten Trial-Fortschritt unten zumuellen.
logger = logging.getLogger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class CachingRestClient:
    """
    Wrapper um RestClient: cacht fetch_ohlcv_range() im Speicher fuer die
    Dauer eines optimizer.py-Laufs. Siehe Modul-Docstring -- entscheidend
    fuer die Laufzeit, da sich das (symbol, timeframe, since, until)-Fenster
    zwischen Trials nicht aendert.
    """

    def __init__(self, inner: RestClient):
        self._inner = inner
        self._cache: dict[tuple, "object"] = {}

    def fetch_ohlcv_range(self, symbol: str, timeframe: str, since: datetime, until: datetime,
                           page_limit: int = 1000):
        key = (symbol, timeframe, since.isoformat(), until.isoformat())
        if key not in self._cache:
            self._cache[key] = self._inner.fetch_ohlcv_range(symbol, timeframe, since, until, page_limit)
        return self._cache[key].copy()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def suggest_grid_cfg(trial: optuna.Trial, base_grid_cfg: dict) -> dict:
    cfg = copy.deepcopy(base_grid_cfg)
    cfg["spacing_atr_mult"] = trial.suggest_float("spacing_atr_mult", 1.0, 8.0)
    cfg["levels_per_side"] = trial.suggest_int("levels_per_side", 1, 3)
    sr_enabled = trial.suggest_categorical("sr_zones_enabled", [True, False])
    cfg.setdefault("sr_zones", {})["enabled"] = sr_enabled
    if sr_enabled:
        cfg["sr_zones"]["tolerance_pct"] = trial.suggest_float("sr_tolerance_pct", 0.0005, 0.003)
        cfg["sr_zones"]["min_touches"] = trial.suggest_int("sr_min_touches", 2, 4)
    return cfg


async def evaluate(trial: optuna.Trial, symbol: str, base_settings: dict, rest_client,
                    start_date: datetime, end_date: datetime, count: int, fill_timeframe: str,
                    min_win_ratio: float, min_fills: int) -> float:
    grid_cfg = suggest_grid_cfg(trial, base_settings["grid"])
    trial_settings = dict(base_settings)
    trial_settings["per_symbol_overrides"] = {
        **base_settings.get("per_symbol_overrides", {}),
        symbol: {"grid": grid_cfg},
    }

    day_results = await run_multiday(
        trial_settings, [symbol], start_date, end_date, count, fill_timeframe, rest_client,
        ledger_prefix=f"optuna_{symbol.replace('/', '_').replace(':', '_')}_{trial.number}",
        cleanup=True, log_progress=False,
    )

    total_pnl = sum(r.total_pnl_usdt for _, r in day_results)
    total_fills = sum(r.num_fills for _, r in day_results)
    win_days = sum(1 for _, r in day_results if r.total_pnl_usdt > 0)
    win_ratio = win_days / len(day_results) if day_results else 0.0

    trial.set_user_attr("total_pnl", total_pnl)
    trial.set_user_attr("total_fills", total_fills)
    trial.set_user_attr("win_ratio", win_ratio)

    if total_fills < min_fills or win_ratio < min_win_ratio:
        violation = max(0, min_fills - total_fills) + max(0.0, min_win_ratio - win_ratio) * 100
        return -1000.0 - violation
    return total_pnl


def run_symbol_study(symbol: str, base_settings: dict, rest_client, args: argparse.Namespace) -> optuna.trial.FrozenTrial:
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))

    def objective(trial: optuna.Trial) -> float:
        return asyncio.run(evaluate(
            trial, symbol, base_settings, rest_client,
            args.start_date, args.end_date, args.count, args.fill_timeframe,
            args.min_win_ratio, args.min_fills,
        ))

    study.optimize(objective, n_trials=args.trials, show_progress_bar=False)
    return study.best_trial


def apply_results(settings: dict, results: dict[str, optuna.trial.FrozenTrial], min_fills: int,
                   min_win_ratio: float) -> None:
    overrides = settings.setdefault("per_symbol_overrides", {})
    applied, skipped = [], []
    for symbol, best in results.items():
        feasible = best.user_attrs["total_fills"] >= min_fills and best.user_attrs["win_ratio"] >= min_win_ratio
        if not feasible:
            skipped.append(symbol)
            continue
        grid_cfg = copy.deepcopy(settings["grid"])
        grid_cfg["spacing_atr_mult"] = best.params["spacing_atr_mult"]
        grid_cfg["levels_per_side"] = best.params["levels_per_side"]
        grid_cfg.setdefault("sr_zones", {})["enabled"] = best.params["sr_zones_enabled"]
        if best.params["sr_zones_enabled"]:
            grid_cfg["sr_zones"]["tolerance_pct"] = best.params["sr_tolerance_pct"]
            grid_cfg["sr_zones"]["min_touches"] = best.params["sr_min_touches"]
        overrides[symbol] = {"grid": grid_cfg}
        applied.append(symbol)

    (ROOT / "settings.json").write_text(json.dumps(settings, indent=4) + "\n", encoding="utf-8")

    if applied:
        print(f"\n[OK] settings.json aktualisiert -- per_symbol_overrides fuer: {', '.join(applied)}")
    if skipped:
        print(f"[!] Gate verfehlt (min_fills={min_fills}, min_win_ratio={min_win_ratio}) -- "
              f"NICHT uebernommen, laeuft weiter mit dem globalen grid-Block: {', '.join(skipped)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="1mbot Grid-Parameter-Optimierung (Optuna, pro Symbol)")
    parser.add_argument("--symbols", nargs="*", default=None, help="Nur diese Symbole (Standard: watchlist aus settings.json)")
    parser.add_argument("--start", required=True, help="Beginn des Trainingsfensters, YYYY-MM-DD (UTC)")
    parser.add_argument("--end", required=True, help="Ende des Trainingsfensters, YYYY-MM-DD (UTC)")
    parser.add_argument("--count", type=int, default=20, help="Anzahl gleichmaessig verteilter Tage (Standard: 20)")
    parser.add_argument("--fill-timeframe", default="1m", help="Kerzen-Aufloesung fuer die Fill-Naeherung (Standard: 1m)")
    parser.add_argument("--trials", type=int, default=60, help="Optuna-Trials pro Symbol (Standard: 60)")
    parser.add_argument("--min-win-ratio", type=float, default=0.5,
                         help="Mindest-Gewinntage-Quote, sonst gilt der Trial als ungueltig (Standard: 0.5)")
    parser.add_argument("--min-fills", type=int, default=20,
                         help="Mindest-Fills ueber das gesamte Fenster, sonst gilt der Trial als ungueltig (Standard: 20)")
    parser.add_argument("--exchange", default="bitget", choices=list(EXCHANGE_OPTIONS.keys()),
                         help="Exchange fuer den historischen OHLCV-Abruf (Standard: bitget)")
    parser.add_argument("--apply", action="store_true",
                         help="Bestes Ergebnis pro Symbol direkt in settings.json uebernehmen (nur wenn Gates erreicht)")
    args = parser.parse_args()
    args.start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    args.end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    settings = load_json(ROOT / "settings.json")
    watchlist = args.symbols or settings["watchlist"]
    rest_client = CachingRestClient(
        RestClient(None, exchange_id=args.exchange, exchange_options=EXCHANGE_OPTIONS.get(args.exchange))
    )

    print(f"=== 1mbot Grid-Optimierung: {len(watchlist)} Symbol(e), {args.trials} Trials, "
          f"{args.count} Tage zwischen {args.start} und {args.end} ({args.exchange}) ===")

    results: dict[str, optuna.trial.FrozenTrial] = {}
    for symbol in watchlist:
        print(f"\n--- {symbol}: optimiere ({args.trials} Trials) ---")
        best = run_symbol_study(symbol, settings, rest_client, args)
        results[symbol] = best
        feasible = best.user_attrs["total_fills"] >= args.min_fills and best.user_attrs["win_ratio"] >= args.min_win_ratio
        flag = "OK" if feasible else "GATE VERFEHLT"
        print(f"  Bestes Ergebnis [{flag}]: PnL={best.user_attrs['total_pnl']:+.4f} USDT, "
              f"Fills={best.user_attrs['total_fills']}, Gewinntage-Quote={best.user_attrs['win_ratio']*100:.1f}%")
        print(f"  Params: {best.params}")

    print("\n=== Zusammenfassung ===")
    for symbol, best in results.items():
        feasible = best.user_attrs["total_fills"] >= args.min_fills and best.user_attrs["win_ratio"] >= args.min_win_ratio
        flag = "OK" if feasible else "GATE VERFEHLT"
        print(f"  {symbol:<20} PnL={best.user_attrs['total_pnl']:+.4f} USDT  [{flag}]")

    if args.apply:
        apply_results(settings, results, args.min_fills, args.min_win_ratio)
    else:
        print("\n(--apply nicht gesetzt: settings.json wurde NICHT veraendert.)")


if __name__ == "__main__":
    main()
