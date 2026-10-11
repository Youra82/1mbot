#!/bin/bash
# 1mbot - Installation (Phase 1: Daten-Recorder)
set -e
echo "=== 1mbot Installation ==="

python3 -m venv .venv
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt --quiet
echo "Packages installiert."

mkdir -p logs data
chmod +x *.sh

if [ ! -f "secret.json" ]; then
    echo "HINWEIS: secret.json fehlt -> laeuft ohne Telegram. Vorlage: secret.json.example"
fi

echo ""
echo "Speicherplatz:"
df -h . | tail -1

./install_service.sh
echo ""
echo "=== Fertig ==="
echo "Starten:  sudo systemctl start 1mbot.service"
echo "Status:   ./show_results.sh"
echo "Logs:     tail -f logs/listing.log"
