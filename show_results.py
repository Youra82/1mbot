#!/usr/bin/env python3
"""Ergebnisse je nach settings.json-Modus: Listing-Waechter (Standard) oder Recorder."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import numpy as np


def show_listing(path: str) -> None:
    if not os.path.exists(path):
        sys.exit(f"Kein {path} -- laeuft der Waechter? (sudo systemctl status 1mbot.service)")
    s = json.load(open(path, encoding="utf-8"))
    age = datetime.now(timezone.utc).timestamp() - os.path.getmtime(path)
    print("=" * 72)
    print(f" 1mbot Listing-Waechter (DRY-RUN)  |  Zustand vor {age:.0f}s gespeichert")
    if age > 180:
        print(" !! Zustand veraltet -- Prozess vermutlich gestoppt")
    print(f" Papier-Konto {s['equity']:.2f} USDT (Start {s['start_equity']:.0f}) | "
          f"offen {len(s['open'])} | wartend {len(s['pending'])} | geschlossen {len(s['closed'])}")
    print("=" * 72)
    for p in s["pending"]:
        print(f" WARTET  {p['ticker']:8s} {p['symbol']:14s} Einstieg {p['entry_at'][:16]} UTC")
    for o in s["open"]:
        print(f" OFFEN   {o['ticker']:8s} Short @ {o['entry_bid']:.6g} | Stop {o['stop_price']:.6g} | "
              f"Ausstieg {o['exit_at'][:16]} UTC | Margin {o['margin']:.2f}")
    if s["closed"]:
        print("\n Geschlossen:")
        print(f"   {'Ticker':8s} {'Einstieg':16s} {'Grund':5s} {'Rendite':>8s} {'roh':>8s} {'Funding':>8s} {'max+':>6s} {'PnL':>7s}")
        for c in s["closed"]:
            print(f"   {c['ticker']:8s} {c['entry_time'][:16]} {c['reason']:5s} "
                  f"{c['ret_margin_backtest_costs'] * 100:+7.1f}% {c['ret_margin_raw_fees'] * 100:+7.1f}% "
                  f"{c['funding_short'] * 100:+7.2f}% {c['max_rise_pct']:5.1f}% {c['pnl_usdt']:+7.2f}")
        r = np.array([c["ret_margin_backtest_costs"] for c in s["closed"]])
        print(f"\n Mittel {r.mean() * 100:+.1f}% | Median {np.median(r) * 100:+.1f}% | positiv {np.mean(r > 0):.0%} "
              f"| n={len(r)}   (Backtest OOS: Mittel +11,8 %, Median +3,9 %)")
        if len(r) < 20:
            print(f" Hinweis: erst {len(r)} Trades -- Aussagekraft ab etwa 20-30 Ereignissen")
    if s["skipped"]:
        print("\n Uebersprungen (letzte 8):")
        for k in s["skipped"][-8:]:
            print(f"   {k['T'][:16]} {k['ticker']:8s} {k['reason']}")


def main() -> None:
    settings = json.load(open("settings.json", encoding="utf-8"))
    if settings.get("mode") == "listing":
        show_listing(settings["listing"]["state_file"])
    else:
        subprocess.run([sys.executable, "show_recorder.py", *sys.argv[1:]])


if __name__ == "__main__":
    main()
