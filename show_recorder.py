#!/usr/bin/env python3
"""Zustand des Recorders: Live-Status, Datenmenge pro Tag, letzte Verbindungs-Events."""
import glob
import gzip
import json
import os
import sys
import time

DATA = sys.argv[1] if len(sys.argv) > 1 else "data"


def main() -> None:
    path = os.path.join(DATA, "status.json")
    if not os.path.exists(path):
        sys.exit(f"Kein {path} -- laeuft der Recorder? (sudo systemctl status 1mbot.service)")
    with open(path, encoding="utf-8") as f:
        st = json.load(f)
    age = time.time() - os.path.getmtime(path)
    print("=" * 64)
    print(f" 1mbot Recorder  |  Stand {st['updated_utc']} UTC  (vor {age:.0f}s)")
    if age > 120:
        print(" !! status.json veraltet -- Prozess vermutlich gestoppt")
    print(f" Laufzeit {st['uptime_h']} h | {st['n_symbols']} Coins | Uhr-Versatz "
          f"{st['clock_offset_ms_vs_binance']} ms | Daten {st['data_size_gb']} GB | frei {st['disk_free_gb']} GB")
    if st.get("paused"):
        print(f" !! PAUSIERT (Speicher): {st['paused']}")
    print("=" * 64)
    for ex, e in st["exchanges"].items():
        print(f" {ex:8s} Latenz {e['latency_ms_median']} ms (p90 {e['latency_ms_p90']}) | "
              f"Coins aktiv {e['symbols_with_book_5min']}/{st['n_symbols']} | Reconnects {e['reconnects']} | "
              f"letzte Nachricht vor {e['last_msg_age_s']}s")
        print(f"          Zeilen seit Start: {e['rows']}")

    print("\n Daten pro Tag:")
    for day in sorted(d for d in os.listdir(DATA) if os.path.isdir(os.path.join(DATA, d)))[-14:]:
        files = glob.glob(os.path.join(DATA, day, "*.gz"))
        size = sum(os.path.getsize(f) for f in files) / 1e6
        hours = len({os.path.basename(f)[-8:-6] for f in files if "_book_" in f})
        print(f"   {day}: {size:7.1f} MB, {hours:2d} Stunden mit Buchdaten")

    ev_files = sorted(glob.glob(os.path.join(DATA, "*", "*_events_*.csv.gz")))[-3:]
    lines = []
    for f in ev_files:
        with gzip.open(f, "rt") as fh:
            lines += [ln.strip() for ln in fh if not ln.startswith("recv_ms") and "clock_offset" not in ln]
    if lines:
        print("\n Letzte Verbindungs-Events:")
        for ln in lines[-8:]:
            t, ex, ev, detail = (ln.split(",", 3) + ["", "", ""])[:4]
            ts = time.strftime("%m-%d %H:%M:%S", time.gmtime(int(t) / 1000))
            print(f"   {ts} {ex:8s} {ev:12s} {detail[:60]}")


if __name__ == "__main__":
    main()
