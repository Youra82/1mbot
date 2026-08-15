#!/usr/bin/env python3
"""
Zeigt den aktuellen Paper-Trading-Zustand:
0. Theoretischer Kontostand (Startkapital aus settings.json + realisierter
   + unrealisierter PnL ueber alle Symbole) -- die Frage "wie viel haette
   ich jetzt, wenn das echtes Geld waere" laesst sich sonst nicht auf einen
   Blick beantworten (Snapshot/Equity-Report zeigen nur Rohgroessen pro
   Symbol, kein zusammengefasster Kontostand).
1. Snapshot pro Symbol aus artifacts/tracker/*.json (aktueller Stand)
2. Equity-/Drawdown-Auswertung aus dem Fill-Log artifacts/tracker/fills.jsonl
   (Zeitachse -- ohne das laesst sich nicht beurteilen, ob der Bot ueber Zeit
   gut oder schlecht lief, ein reiner Endstand sagt dazu nichts).

Anders als bei den anderen Bots gibt es hier keine Backtest-Kennzahlen zu
zeigen (siehe Kontext: kein Candle-Backtest fuer den Grid-Ansatz moeglich).
Fuer eine schnelle historische Naeherung siehe backtest_replay.py.

--watch [Sekunden] fuer eine live aktualisierende Ansicht (fuer den
Dauerbetrieb auf VPS/Mini-PC gedacht -- ein Terminal offenlassen statt
das Skript manuell erneut aufzurufen).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from onembot.utils.ledger import FillLedger  # noqa: E402
from onembot.utils.report import build_report  # noqa: E402

ROOT = Path(__file__).resolve().parent
TRACKER_DIR = ROOT / "artifacts" / "tracker"


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_symbol_states() -> list[dict]:
    files = sorted(TRACKER_DIR.glob("*.json"))
    rows = []
    for f in files:
        try:
            rows.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return rows


def compute_account_balance(start_capital_usdt: float, rows: list[dict]) -> dict:
    total_realized = sum(r.get("realized_pnl_usdt", 0.0) for r in rows)
    total_unrealized = sum(r.get("unrealized_pnl_usdt", 0.0) for r in rows)
    return {
        "start_capital_usdt": start_capital_usdt,
        "balance_usdt": start_capital_usdt + total_realized + total_unrealized,
        "total_realized_pnl_usdt": total_realized,
        "total_unrealized_pnl_usdt": total_unrealized,
    }


def print_account_balance(rows: list[dict]) -> None:
    try:
        settings = load_json(ROOT / "settings.json")
    except (json.JSONDecodeError, OSError):
        print("settings.json nicht lesbar -- Kontostand nicht berechenbar.")
        return

    start_capital = settings.get("account", {}).get("start_capital_usdt")
    if start_capital is None:
        print("Kein 'account.start_capital_usdt' in settings.json konfiguriert -- Kontostand nicht berechenbar.")
        return
    if not rows:
        print(f"Kontostand (theoretisch):  {start_capital:.4f} USDT  (noch keine Aktivitaet, reines Startkapital)")
        return

    acc = compute_account_balance(start_capital, rows)
    pnl_total = acc["total_realized_pnl_usdt"] + acc["total_unrealized_pnl_usdt"]
    pnl_pct = (pnl_total / start_capital * 100.0) if start_capital else 0.0
    print(f"Kontostand (theoretisch):  {acc['balance_usdt']:.4f} USDT  "
          f"(Start: {start_capital:.2f} USDT, PnL: {pnl_total:+.4f} USDT / {pnl_pct:+.2f}%)")
    print(f"  davon realisiert:   {acc['total_realized_pnl_usdt']:+.4f} USDT")
    print(f"  davon unrealisiert: {acc['total_unrealized_pnl_usdt']:+.4f} USDT (Mark-to-Market, offene Positionen)")


def print_snapshot(rows: list[dict]) -> None:
    if not rows:
        print("Keine State-Dateien in artifacts/tracker/ gefunden -- 1mbot laeuft noch nicht "
              "oder hat noch keinen einzigen Tick verarbeitet.")
        return

    header = (f"{'Symbol':<20} {'Regime':<8} {'Mark Price':>12} {'Inventory USDT':>16} "
              f"{'Realized':>10} {'Unrealized':>10} {'Orders':>7}  Last Update")
    print(header)
    print("-" * len(header))
    for r in rows:
        mark = r.get("mark_price")
        mark_str = f"{mark:.4f}" if mark is not None else "-"
        print(f"{r.get('symbol', '?'):<20} {r.get('regime', '?'):<8} {mark_str:>12} "
              f"{r.get('net_inventory_usdt', 0.0):>16.4f} {r.get('realized_pnl_usdt', 0.0):>10.4f} "
              f"{r.get('unrealized_pnl_usdt', 0.0):>10.4f} {r.get('open_orders', 0):>7}  {r.get('last_updated', '?')}")
    print("-" * len(header))


def print_equity_report() -> None:
    ledger = FillLedger("fills.jsonl")
    fills = ledger.load()
    if not fills:
        print("Noch keine Fills im Live-Ledger (artifacts/tracker/fills.jsonl) -- "
              "Equity-/Drawdown-Auswertung erst nach den ersten simulierten Fills moeglich.")
        return

    report = build_report(fills)
    print(f"Fills gesamt (seit Start des Live-Dry-Runs): {report.num_fills}")
    print(f"Realisierter PnL:                            {report.total_pnl_usdt:+.4f} USDT")
    print(f"Max Drawdown (Peak-zu-Tal, realisiert):       {report.max_drawdown_usdt:.4f} USDT")
    print("")
    print(f"{'Symbol':<20} {'Fills':>6} {'PnL':>12}")
    for sym, data in sorted(report.per_symbol.items(), key=lambda kv: -kv[1]["pnl"]):
        print(f"{sym:<20} {data['fills']:>6} {data['pnl']:>12.4f}")


def print_all() -> None:
    rows = load_symbol_states()
    print("=== Kontostand ===")
    print_account_balance(rows)
    print("")
    print("=== Aktueller Stand (Snapshot) ===")
    print_snapshot(rows)
    print("")
    print("=== Equity-/Drawdown-Verlauf (aus Fill-Log) ===")
    print_equity_report()


def main() -> None:
    parser = argparse.ArgumentParser(description="1mbot Paper-Trading-Status")
    parser.add_argument("--watch", type=int, nargs="?", const=10, default=None,
                         help="Live-Ansicht: Bildschirm alle N Sekunden neu zeichnen (Standard: 10s).")
    args = parser.parse_args()

    if args.watch is None:
        print_all()
        return

    try:
        while True:
            print("\033[2J\033[H", end="")  # ANSI: Bildschirm leeren, Cursor an den Anfang
            print(f"1mbot -- Live-Status (Aktualisierung alle {args.watch}s, Strg+C zum Beenden)")
            print("=" * 60)
            print_all()
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()
