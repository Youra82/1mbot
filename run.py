#!/usr/bin/env python3
"""1mbot -- Einstieg (keine echten Orders, keine API-Keys noetig).

settings.json "mode":
  "listing"   Dry-Run-Waechter Upbit-KRW-Listing -> Papier-Short Bitget (Standard)
  "recorder"  Sekunden-Daten-Recorder Binance/Bitget

  python run.py                 # Dauerlauf (systemd-Service auf dem VPS)
  python run.py --duration 120  # Recorder-Testlauf ueber 120 Sekunden
"""
import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from onembot.recorder import main as recorder  # noqa: E402
from onembot.recorder import telegram  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default="settings.json")
    ap.add_argument("--duration", type=float, default=None, help="Sekunden, danach sauber beenden")
    ap.add_argument("--data-dir", default=None, help="ueberschreibt settings.data_dir (Tests)")
    args = ap.parse_args()

    with open(args.settings, encoding="utf-8") as f:
        settings = json.load(f)
    if args.data_dir:
        settings["data_dir"] = args.data_dir
    if settings["capital"].get("live_trading"):
        # Phase 1 kennt keine Orders -- ein versehentlich gesetztes Flag soll laut scheitern
        sys.exit("live_trading=true, aber 1mbot kennt noch keine echten Orders. Abbruch.")

    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(f"logs/{settings.get('mode', 'recorder')}.log", encoding="utf-8")],
    )
    try:
        if settings.get("mode") == "listing":
            from onembot.listing.watcher import run_forever
            creds = telegram.load_telegram() if settings["telegram"].get("enabled", True) else None
            run_forever(settings, lambda text: (logging.getLogger("1mbot").info("NOTIFY %s", text),
                                                telegram.send(creds, text)))
        else:
            asyncio.run(recorder.run(settings, args.duration))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
