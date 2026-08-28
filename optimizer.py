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

IN-SAMPLE / OUT-OF-SAMPLE-SPLIT (wie ltbbot/stbot, --is-fraction, Standard
70/30): Optuna sieht beim Suchen NUR die aeltesten `is_fraction` der ueber
das Fenster verteilten Tage. Die juengsten (1-is_fraction) Tage sieht kein
einziger Trial waehrend der Suche -- sie dienen ausschliesslich der
Bestaetigung des am Ende gefundenen besten Parametersatzes. Ohne das waere
das Ergebnis reines In-Sample-Rauschen und nicht vertrauenswuerdig zu
bewerten (siehe dnabot: ein echter Walk-Forward-Test zeigte dort PnL -99.5%
bis -100% ueber ALLE Lookbacks, obwohl der reine In-Sample-Optimizer-Lauf
+151.2% zeigte -- Auto-Optimizer-Ergebnisse ohne OOS-Bestaetigung sind
strukturell nicht von Zufall zu unterscheiden). --apply uebernimmt ein
Symbol nur, wenn die Gates BEIDE Male greifen -- in-sample UND out-of-sample.

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
        --trials 60 --min-win-ratio 0.5 --min-fills 20 --is-fraction 0.7 --apply
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# Ohne das hier ist stdout voll gepuffert, sobald es NICHT an ein Terminal
# geht (z.B. `| tee log.txt`, systemd-Journal, oder einfach ein langsameres
# Terminal-Backend) -- bei 60 stillen Trials sieht man dann minutenlang gar
# nichts, bis der Puffer volllaeuft oder der Prozess endet (genau das Problem,
# das beim ersten echten Testlauf auftrat: "haengt er? laedt er? optimiert er?").
sys.stdout.reconfigure(line_buffering=True)

import optuna  # noqa: E402

from backtest_multiday import EXCHANGE_OPTIONS, pick_days, run_multiday  # noqa: E402
from onembot.exchange.rest_client import RestClient, normalize_symbol, print_invalid_symbols  # noqa: E402

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


class LiveTicker:
    """
    Laufender "laeuft seit MM:SS"-Heartbeat auf einem eigenen Hintergrund-
    Thread, ueber eine sich selbst ueberschreibende Zeile (\r).

    Notwendig, weil sowohl das Nachladen der Preishistorie (ccxt ist
    synchron -- ein einzelner REST-Call blockiert den Hauptthread fuer die
    Dauer des Requests) als auch die eigentliche Kerze-fuer-Kerze-Simulation
    minutenlang ohne einen einzigen natuerlichen Print-Zeitpunkt laufen
    koennen. Ein einzelner Thread kann waehrend eines blockierenden Aufrufs
    nichts anderes tun -- nur ein zweiter Thread kann in der Zwischenzeit
    weiter "ticken".

    Alle "echten" Zeilen waehrend der Laufzeit MUESSEN ueber print_line()
    statt print() gehen, sonst ueberschreiben sich Ticker-Zeile und echte
    Ausgabe gegenseitig.
    """

    def __init__(self, interval: float = 1.0):
        self._interval = interval
        self._start = time.monotonic()
        self._fetch_count = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._start = time.monotonic()
        self._fetch_count = 0
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._interval + 1)
        with self._lock:
            print("\r" + " " * 70 + "\r", end="", flush=True)

    def on_fetch(self) -> None:
        with self._lock:
            self._fetch_count += 1

    def print_line(self, text: str) -> None:
        with self._lock:
            print("\r" + " " * 70 + "\r" + text, flush=True)

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            with self._lock:
                elapsed = time.monotonic() - self._start
                mins, secs = divmod(int(elapsed), 60)
                print(f"\r  ... laeuft seit {mins:02d}:{secs:02d}  "
                      f"({self._fetch_count} Preishistorie-Abrufe geladen)  ", end="", flush=True)


class CachingRestClient:
    """
    Wrapper um RestClient: cacht fetch_ohlcv_range() im Speicher fuer die
    Dauer eines optimizer.py-Laufs. Siehe Modul-Docstring -- entscheidend
    fuer die Laufzeit, da sich das (symbol, timeframe, since, until)-Fenster
    zwischen Trials nicht aendert.
    """

    def __init__(self, inner: RestClient, ticker: LiveTicker | None = None):
        self._inner = inner
        self._cache: dict[tuple, "object"] = {}
        self.ticker = ticker

    def fetch_ohlcv_range(self, symbol: str, timeframe: str, since: datetime, until: datetime,
                           page_limit: int = 1000):
        key = (symbol, timeframe, since.isoformat(), until.isoformat())
        if key not in self._cache:
            if self.ticker:
                self.ticker.on_fetch()
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


def params_to_grid_cfg(base_grid_cfg: dict, params: dict) -> dict:
    """Baut denselben grid-Block, den suggest_grid_cfg() waehrend eines Trials
    erzeugt hat, aus dessen abgespeicherten Optuna-Params nach -- genutzt fuer
    die OOS-Bestaetigung UND fuer apply_results(), damit beide garantiert
    denselben Parametersatz verwenden wie der Trial, der ihn gefunden hat."""
    cfg = copy.deepcopy(base_grid_cfg)
    cfg["spacing_atr_mult"] = params["spacing_atr_mult"]
    cfg["levels_per_side"] = params["levels_per_side"]
    cfg.setdefault("sr_zones", {})["enabled"] = params["sr_zones_enabled"]
    if params["sr_zones_enabled"]:
        cfg["sr_zones"]["tolerance_pct"] = params["sr_tolerance_pct"]
        cfg["sr_zones"]["min_touches"] = params["sr_min_touches"]
    return cfg


async def run_scored_backtest(symbol: str, base_settings: dict, grid_cfg: dict, rest_client,
                               days: list[datetime], fill_timeframe: str, ledger_prefix: str) -> dict:
    """Ein Multi-Day-Backtest ueber `days` mit einem festen grid_cfg, aufbereitet
    zu denselben drei Kennzahlen (total_pnl/total_fills/win_ratio), die sowohl
    die Optuna-Zielfunktion (auf IS-Tagen) als auch die OOS-Bestaetigung danach
    (auf OOS-Tagen) brauchen -- eine gemeinsame Auswertung fuer beide."""
    trial_settings = dict(base_settings)
    trial_settings["per_symbol_overrides"] = {
        **base_settings.get("per_symbol_overrides", {}),
        symbol: {"grid": grid_cfg},
    }
    day_results = await run_multiday(
        trial_settings, [symbol], days, fill_timeframe, rest_client,
        ledger_prefix=ledger_prefix, cleanup=True, log_progress=False,
    )
    total_pnl = sum(r.total_pnl_usdt for _, r in day_results)
    total_fills = sum(r.num_fills for _, r in day_results)
    win_days = sum(1 for _, r in day_results if r.total_pnl_usdt > 0)
    win_ratio = win_days / len(day_results) if day_results else 0.0
    return {"total_pnl": total_pnl, "total_fills": total_fills, "win_ratio": win_ratio}


async def evaluate(trial: optuna.Trial, symbol: str, base_settings: dict, rest_client,
                    is_days: list[datetime], fill_timeframe: str,
                    min_win_ratio: float, min_fills: int) -> float:
    grid_cfg = suggest_grid_cfg(trial, base_settings["grid"])
    metrics = await run_scored_backtest(
        symbol, base_settings, grid_cfg, rest_client, is_days, fill_timeframe,
        ledger_prefix=f"optuna_{symbol.replace('/', '_').replace(':', '_')}_{trial.number}",
    )
    total_pnl, total_fills, win_ratio = metrics["total_pnl"], metrics["total_fills"], metrics["win_ratio"]

    trial.set_user_attr("total_pnl", total_pnl)
    trial.set_user_attr("total_fills", total_fills)
    trial.set_user_attr("win_ratio", win_ratio)

    if total_fills < min_fills or win_ratio < min_win_ratio:
        violation = max(0, min_fills - total_fills) + max(0.0, min_win_ratio - win_ratio) * 100
        return -1000.0 - violation
    return total_pnl


def is_feasible(metrics: dict, min_fills: int, min_win_ratio: float) -> bool:
    return metrics["total_fills"] >= min_fills and metrics["win_ratio"] >= min_win_ratio


def run_symbol_study(symbol: str, base_settings: dict, rest_client, args: argparse.Namespace,
                      is_days: list[datetime], oos_days: list[datetime],
                      min_oos_fills: int) -> tuple[optuna.trial.FrozenTrial, dict]:
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))

    def objective(trial: optuna.Trial) -> float:
        return asyncio.run(evaluate(
            trial, symbol, base_settings, rest_client,
            is_days, args.fill_timeframe, args.min_win_ratio, args.min_fills,
        ))

    def report_progress(study: optuna.Study, trial: optuna.trial.FrozenTrial) -> None:
        # Ohne dieses Lebenszeichen sieht man bei 60 stillen Trials nicht, ob
        # der Prozess haengt, noch Daten laedt oder einfach nur rechnet --
        # genau die Frage, die beim ersten echten Testlauf aufkam.
        feasible = is_feasible(trial.user_attrs, args.min_fills, args.min_win_ratio)
        best_pnl = study.best_trial.user_attrs.get("total_pnl", 0.0)
        dur = trial.duration.total_seconds() if trial.duration is not None else None
        dur_str = f"{dur:.1f}s" if dur is not None else "?"
        rest_client.ticker.print_line(
            f"  [{trial.number + 1}/{args.trials}] IS-PnL={trial.user_attrs.get('total_pnl', 0.0):+.4f} USDT  "
            f"Fills={trial.user_attrs.get('total_fills', 0)}  "
            f"Gewinntage={trial.user_attrs.get('win_ratio', 0.0)*100:.0f}%  "
            f"[{'OK' if feasible else 'Gate'}]  (bisher bestes: {best_pnl:+.4f} USDT)  "
            f"-- Dauer: {dur_str}"
        )

    rest_client.ticker.print_line(
        f"  (In-Sample: {len(is_days)} Tage, Out-of-Sample: {len(oos_days)} Tage. "
        "Erster Trial laedt die Preishistorie per REST -- dauert am laengsten, "
        "danach greift der Cache und es wird deutlich schneller)"
    )
    study.optimize(objective, n_trials=args.trials, show_progress_bar=False, callbacks=[report_progress])
    best = study.best_trial

    rest_client.ticker.print_line(
        f"  IS-bestes Ergebnis: PnL={best.user_attrs['total_pnl']:+.4f} USDT, "
        f"Fills={best.user_attrs['total_fills']}, Gewinntage-Quote={best.user_attrs['win_ratio']*100:.1f}%"
    )
    rest_client.ticker.print_line(f"  Params: {best.params}")
    rest_client.ticker.print_line("  Bestaetige auf Out-of-Sample-Tagen (von Optuna nie gesehen)...")

    grid_cfg = params_to_grid_cfg(base_settings["grid"], best.params)
    oos_metrics = asyncio.run(run_scored_backtest(
        symbol, base_settings, grid_cfg, rest_client, oos_days, args.fill_timeframe,
        ledger_prefix=f"oos_{symbol.replace('/', '_').replace(':', '_')}",
    ))
    oos_ok = is_feasible(oos_metrics, min_oos_fills, args.min_win_ratio)
    rest_client.ticker.print_line(
        f"  OOS-Ergebnis [{'OK' if oos_ok else 'GATE VERFEHLT'}]: PnL={oos_metrics['total_pnl']:+.4f} USDT, "
        f"Fills={oos_metrics['total_fills']} (Mindest: {min_oos_fills}), "
        f"Gewinntage-Quote={oos_metrics['win_ratio']*100:.1f}%"
    )
    return best, oos_metrics


def apply_results(settings: dict, results: dict[str, tuple[optuna.trial.FrozenTrial, dict]], min_fills: int,
                   min_win_ratio: float, min_oos_fills: int) -> None:
    overrides = settings.setdefault("per_symbol_overrides", {})
    applied, skipped = [], []
    for symbol, (best, oos_metrics) in results.items():
        # Beide Gates muessen greifen -- ein Parametersatz, der nur in-sample
        # gut aussieht, ist per Definition dieses Skripts genau das
        # In-Sample-Rauschen, das der OOS-Split verhindern soll (siehe
        # Modul-Docstring, dnabot-Praezedenzfall).
        if not (is_feasible(best.user_attrs, min_fills, min_win_ratio)
                and is_feasible(oos_metrics, min_oos_fills, min_win_ratio)):
            skipped.append(symbol)
            continue
        overrides[symbol] = {"grid": params_to_grid_cfg(settings["grid"], best.params)}
        applied.append(symbol)

    (ROOT / "settings.json").write_text(json.dumps(settings, indent=4) + "\n", encoding="utf-8")

    if applied:
        print(f"\n[OK] settings.json aktualisiert -- per_symbol_overrides fuer: {', '.join(applied)}")
    if skipped:
        print(f"[!] IS- oder OOS-Gate verfehlt (min_fills={min_fills}, min_oos_fills={min_oos_fills}, "
              f"min_win_ratio={min_win_ratio}) -- NICHT uebernommen, laeuft weiter mit dem globalen "
              f"grid-Block: {', '.join(skipped)}")


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
                         help="Mindest-Fills ueber das gesamte In-Sample-Fenster, sonst gilt der Trial als ungueltig (Standard: 20)")
    parser.add_argument("--is-fraction", type=float, default=0.7,
                         help="Anteil der aeltesten Tage, die Optuna beim Suchen sieht -- der Rest (juengste Tage) "
                              "dient nur der Out-of-Sample-Bestaetigung danach (Standard: 0.7)")
    parser.add_argument("--exchange", default="bitget", choices=list(EXCHANGE_OPTIONS.keys()),
                         help="Exchange fuer den historischen OHLCV-Abruf (Standard: bitget)")
    parser.add_argument("--apply", action="store_true",
                         help="Bestes Ergebnis pro Symbol direkt in settings.json uebernehmen (nur wenn IS- UND OOS-Gates erreicht)")
    args = parser.parse_args()
    args.start_date = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    args.end_date = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if not 0.0 < args.is_fraction < 1.0:
        print(f"[FEHLER] --is-fraction muss zwischen 0 und 1 liegen (bekommen: {args.is_fraction}).")
        sys.exit(1)

    all_days = pick_days(args.start_date, args.end_date, args.count)
    split_idx = max(1, round(len(all_days) * args.is_fraction))
    is_days, oos_days = all_days[:split_idx], all_days[split_idx:]
    if not oos_days:
        print(f"[FEHLER] Bei --count {args.count} und --is-fraction {args.is_fraction} bleiben 0 Out-of-Sample-Tage "
              f"uebrig ({len(is_days)} In-Sample). --count erhoehen oder --is-fraction senken.")
        sys.exit(1)
    # Mindest-Fills fuer die OOS-Bestaetigung proportional zum kleineren Zeitfenster
    # skaliert -- derselbe absolute --min-fills-Wert waere auf einem 30%-Fenster
    # unfair streng.
    min_oos_fills = max(1, round(args.min_fills * len(oos_days) / len(is_days)))

    settings = load_json(ROOT / "settings.json")
    watchlist = [normalize_symbol(s) for s in args.symbols] if args.symbols else settings["watchlist"]
    ticker = LiveTicker()
    rest_client = CachingRestClient(
        RestClient(None, exchange_id=args.exchange, exchange_options=EXCHANGE_OPTIONS.get(args.exchange)),
        ticker=ticker,
    )

    invalid = rest_client.validate_symbols(watchlist)
    if invalid:
        print_invalid_symbols(invalid, args.exchange)
        sys.exit(1)

    print(f"=== 1mbot Grid-Optimierung: {len(watchlist)} Symbol(e), {args.trials} Trials, "
          f"{args.count} Tage zwischen {args.start} und {args.end} ({args.exchange}) ===")
    print(f"    In-Sample: {len(is_days)} Tage (aelteste) | Out-of-Sample: {len(oos_days)} Tage "
          f"(juengste, min_oos_fills={min_oos_fills})")

    results: dict[str, tuple[optuna.trial.FrozenTrial, dict]] = {}
    ticker.start()
    for symbol in watchlist:
        ticker.print_line(f"\n--- {symbol}: optimiere ({args.trials} Trials) ---")
        results[symbol] = run_symbol_study(symbol, settings, rest_client, args, is_days, oos_days, min_oos_fills)
    ticker.stop()

    print("\n=== Zusammenfassung ===")
    for symbol, (best, oos_metrics) in results.items():
        is_ok = is_feasible(best.user_attrs, args.min_fills, args.min_win_ratio)
        oos_ok = is_feasible(oos_metrics, min_oos_fills, args.min_win_ratio)
        flag = "OK" if (is_ok and oos_ok) else ("OOS GATE VERFEHLT" if is_ok else "IS GATE VERFEHLT")
        print(f"  {symbol:<20} IS-PnL={best.user_attrs['total_pnl']:+.4f} USDT  "
              f"OOS-PnL={oos_metrics['total_pnl']:+.4f} USDT  [{flag}]")

    if args.apply:
        apply_results(settings, results, args.min_fills, args.min_win_ratio, min_oos_fills)
    else:
        print("\n(--apply nicht gesetzt: settings.json wurde NICHT veraendert.)")


if __name__ == "__main__":
    main()
