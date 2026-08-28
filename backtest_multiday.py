#!/usr/bin/env python3
"""
Backtest ueber mehrere EINZELNE Tage statt eines zusammenhaengenden
Zeitraums -- soll zeigen, wie sich 1mbot an unterschiedlichen Tagen
(unterschiedliche Marktphasen) verhalten haette, statt nur den letzten
zusammenhaengenden Block zu betrachten (siehe backtest_replay.py fuer
den Zeitraum-Modus). Waehlt standardmaessig `--count` gleichmaessig ueber
`--start`..`--end` verteilte Kalendertage (UTC, 00:00-24:00) aus.

Candle-basierte Naeherung, dieselben bekannten Verzerrungen wie
backtest_replay.py -- siehe src/onembot/replay.py.

Benutzung:
    python backtest_multiday.py --start 2026-02-01 --end 2026-08-14 --count 20
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from onembot.exchange.rest_client import RestClient, normalize_symbol, print_invalid_symbols  # noqa: E402
from onembot.replay import replay_symbol  # noqa: E402
from onembot.utils.ledger import FillLedger  # noqa: E402
from onembot.utils.report import build_report  # noqa: E402

ROOT = Path(__file__).resolve().parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def pick_days(start_date: datetime, end_date: datetime, count: int) -> list[datetime]:
    """Waehlt `count` Kalendertage gleichmaessig zwischen start_date und
    end_date (beide inklusive), erster und letzter Tag immer dabei."""
    total_days = (end_date - start_date).days
    if count < 2 or total_days == 0:
        return [start_date]
    offsets = sorted({round(i * total_days / (count - 1)) for i in range(count)})
    return [start_date + timedelta(days=o) for o in offsets]


EXCHANGE_OPTIONS = {
    "bitget": {"defaultType": "swap"},
    "binance": {"defaultType": "future"},
}


async def run_multiday(settings: dict, watchlist: list[str], start_date: datetime, end_date: datetime,
                        count: int, fill_timeframe: str, rest_client: RestClient,
                        ledger_prefix: str = "replay_multiday", cleanup: bool = False,
                        log_progress: bool = True) -> list[tuple[datetime, "object"]]:
    """
    Kernschleife des Multi-Day-Backtests, herausgeloest aus main() -- wird
    sowohl vom CLI-Skript hier unten als auch von optimizer.py (ein Trial =
    ein run_multiday()-Aufruf ueber dasselbe Fenster mit anderen Grid-Params)
    aufgerufen. So bewertet die Optuna-Zielfunktion exakt dieselbe Backtest-
    Logik, die auch beim manuellen `./show_results.sh`-Multi-Day-Modus laeuft
    -- keine zweite, potenziell abweichende Auswertung.

    `cleanup=True` loescht die Ledger-Datei jedes Tages sofort nach dem
    Auslesen (fuer optimizer.py: Dutzende Trials x Dutzende Tage wuerden
    sonst artifacts/tracker/ mit Wegwerf-Dateien zumuellen).
    """
    days = pick_days(start_date, end_date, count)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    day_results = []

    for day_start in days:
        day_end = day_start + timedelta(days=1)
        ledger = FillLedger(f"{ledger_prefix}_{run_id}_{day_start.strftime('%Y%m%d')}.jsonl")

        for symbol in watchlist:
            n_fills = await replay_symbol(symbol, settings, rest_client, day_start, day_end, fill_timeframe, ledger)
            if log_progress:
                logger.info(f"{day_start.strftime('%Y-%m-%d')} {symbol}: {n_fills} simulierte Fills.")

        fills = ledger.load()
        report = build_report(fills)
        day_results.append((day_start, report))

        if cleanup:
            ledger.path.unlink(missing_ok=True)

    return day_results


async def main(start_str: str, end_str: str, count: int, fill_timeframe: str, symbols: list[str] | None,
                exchange_id: str) -> None:
    settings = load_json(ROOT / "settings.json")
    rest_client = RestClient(None, exchange_id=exchange_id, exchange_options=EXCHANGE_OPTIONS.get(exchange_id))
    watchlist = [normalize_symbol(s) for s in symbols] if symbols else settings["watchlist"]

    invalid = rest_client.validate_symbols(watchlist)
    if invalid:
        print_invalid_symbols(invalid, exchange_id)
        sys.exit(1)

    start_date = datetime.strptime(start_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_date = datetime.strptime(end_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    logger.info(f"Ausgewaehlte Tage: {count} verteilt zwischen {start_str} und {end_str}")

    day_results = await run_multiday(settings, watchlist, start_date, end_date, count, fill_timeframe, rest_client)

    print("")
    print(f"=== Multi-Day-Replay ({exchange_id}): {len(day_results)} Tage zwischen {start_str} und {end_str} ({fill_timeframe}-Naeherung) ===")
    print(f"{'Datum':<12} {'Fills':>6} {'PnL USDT':>12}")
    total_fills = 0
    total_pnl = 0.0
    win_days = 0
    loss_days = 0
    for day_start, report in day_results:
        print(f"{day_start.strftime('%Y-%m-%d'):<12} {report.num_fills:>6} {report.total_pnl_usdt:>12.4f}")
        total_fills += report.num_fills
        total_pnl += report.total_pnl_usdt
        if report.total_pnl_usdt > 0:
            win_days += 1
        elif report.total_pnl_usdt < 0:
            loss_days += 1

    print("")
    print(f"Fills gesamt:      {total_fills}")
    print(f"PnL gesamt:        {total_pnl:+.4f} USDT")
    print(f"PnL Durchschnitt:  {total_pnl / len(day_results):+.4f} USDT/Tag")
    print(f"Gewinn-/Verlust-Tage: {win_days} Gewinn / {loss_days} Verlust / {len(day_results) - win_days - loss_days} neutral (0 Fills)")
    print("")
    print("HINWEIS: Candle-basierte Naeherung (High/Low als Touch-Proxy, optimistisch,")
    print("Reihenfolge innerhalb einer Kerze unbekannt) -- siehe src/onembot/replay.py.")
    print("Kein Ersatz fuer den Live-Dry-Run, nur ein schnellerer erster Anhaltspunkt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="1mbot Grid-Replay ueber mehrere einzelne, verteilte Tage")
    parser.add_argument("--start", required=True, help="Beginn des Auswahlzeitraums, YYYY-MM-DD (UTC)")
    parser.add_argument("--end", required=True, help="Ende des Auswahlzeitraums, YYYY-MM-DD (UTC)")
    parser.add_argument("--count", type=int, default=20, help="Anzahl gleichmaessig verteilter Tage (Standard: 20)")
    parser.add_argument("--fill-timeframe", default="1m", help="Kerzen-Aufloesung fuer die Fill-Naeherung (Standard: 1m)")
    parser.add_argument("--symbols", nargs="*", default=None, help="Nur diese Symbole (Standard: watchlist aus settings.json)")
    parser.add_argument("--exchange", default="bitget", choices=list(EXCHANGE_OPTIONS.keys()),
                         help="Exchange fuer den historischen OHLCV-Abruf (Standard: bitget). "
                              "Nur fuer Cross-Checks -- Grid/Regime-Settings bleiben unveraendert aus settings.json, "
                              "nur die Datenquelle fuer die Historie wechselt (z.B. binance fuer Zeitraeume, "
                              "die Bitgets 1m-REST-Retention (~28 Tage) nicht mehr abdeckt).")
    args = parser.parse_args()
    asyncio.run(main(args.start, args.end, args.count, args.fill_timeframe, args.symbols, args.exchange))
