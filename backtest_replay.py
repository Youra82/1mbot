#!/usr/bin/env python3
"""
Zeigt eine Naeherung, wie sich 1mbot in den letzten N Tagen verhalten
haette -- Candle-basierte Fill-Simulation (kein echter Tick-Verlauf
verfuegbar, siehe onembot/replay.py fuer die bekannten Verzerrungen).

Benutzung:
    python backtest_replay.py --days 3
    python backtest_replay.py --days 7 --fill-timeframe 1m --symbols BTC/USDT:USDT
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


async def main(days: int, fill_timeframe: str, symbols: list[str] | None) -> None:
    settings = load_json(ROOT / "settings.json")
    rest_client = RestClient(None)
    watchlist = [normalize_symbol(s) for s in symbols] if symbols else settings["watchlist"]

    invalid = rest_client.validate_symbols(watchlist)
    if invalid:
        print_invalid_symbols(invalid, "bitget")
        sys.exit(1)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    ledger = FillLedger(f"replay_fills_{run_id}.jsonl")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    for symbol in watchlist:
        logger.info(f"Replay {symbol} ueber die letzten {days} Tage ({fill_timeframe}-Aufloesung)...")
        n_fills = await replay_symbol(symbol, settings, rest_client, start, end, fill_timeframe, ledger)
        logger.info(f"{symbol}: {n_fills} simulierte Fills.")

    fills = ledger.load()
    report = build_report(fills)

    print("")
    print(f"=== Replay-Ergebnis: letzte {days} Tage ({fill_timeframe}-Naeherung) ===")
    print(f"Fills gesamt: {report.num_fills}")
    print(f"Realisierter PnL (approximiert): {report.total_pnl_usdt:+.4f} USDT")
    print(f"Max Drawdown (approximiert):     {report.max_drawdown_usdt:.4f} USDT")
    print("")
    print(f"{'Symbol':<20} {'Fills':>6} {'PnL':>12}")
    for sym, data in sorted(report.per_symbol.items(), key=lambda kv: -kv[1]["pnl"]):
        print(f"{sym:<20} {data['fills']:>6} {data['pnl']:>12.4f}")
    print("")
    print(f"Fill-Log gespeichert: {ledger.path}")
    print("HINWEIS: Candle-basierte Naeherung (High/Low als Touch-Proxy, optimistisch,")
    print("Reihenfolge innerhalb einer Kerze unbekannt) -- siehe src/onembot/replay.py.")
    print("Kein Ersatz fuer den Live-Dry-Run, nur ein schnellerer erster Anhaltspunkt.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="1mbot Grid-Replay gegen historische OHLCV-Daten")
    parser.add_argument("--days", type=int, default=3, help="Anzahl Tage rueckwirkend (Standard: 3)")
    parser.add_argument("--fill-timeframe", default="5m", help="Kerzen-Aufloesung fuer die Fill-Naeherung (Standard: 5m)")
    parser.add_argument("--symbols", nargs="*", default=None, help="Nur diese Symbole (Standard: watchlist aus settings.json)")
    args = parser.parse_args()
    asyncio.run(main(args.days, args.fill_timeframe, args.symbols))
