#!/bin/bash
# 1mbot - Installations-Skript
set -e

echo "=== 1mbot Installation ==="

# Virtual Environment erstellen
python3 -m venv .venv
echo "venv erstellt."

# Packages installieren
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
echo "Packages installiert."

# Verzeichnisse anlegen
mkdir -p logs artifacts/tracker

# Skripte ausfuehrbar machen
chmod +x *.sh

# secret.json pruefen
if [ ! -f "secret.json" ]; then
    echo "WARNUNG: secret.json fehlt! Ohne Datei laeuft 1mbot rein oeffentlich (kein Telegram-Reporting)."
    echo "Vorlage: secret.json.example"
else
    echo "secret.json gefunden."
fi

# systemd-Service registrieren (kein Cron -- 1mbot ist ein Dauerlauf-Prozess,
# der laufend Orderbook-Ticks per Websocket beobachtet statt periodisch zu pollen)
echo ""
echo "Richte systemd-Service ein (braucht sudo)..."
SERVICE_SRC="$(pwd)/deploy/1mbot.service"
SERVICE_DST="/etc/systemd/system/1mbot.service"
sed "s#/home/REPLACE_ME/botprojekte/1mbot#$(pwd)#g" "$SERVICE_SRC" | sudo tee "$SERVICE_DST" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable 1mbot.service
echo "Service registriert (noch nicht gestartet)."

echo ""
echo "=== Installation abgeschlossen ==="
echo ""
echo "Naechste Schritte:"
echo "  1. secret.json mit Bitget API-Keys und Telegram-Bot befuellen (optional fuer Dry-Run)"
echo "  2. settings.json anpassen (Watchlist, Grid-Parameter, Risiko)"
echo "  3. Service starten:  sudo systemctl start 1mbot.service"
echo "  4. Logs pruefen:     journalctl -u 1mbot.service -f"
echo ""
echo "WICHTIG: live_trading steht in settings.json auf false und wird zusaetzlich"
echo "hart im Code geprueft (run.py + ws_client.py) -- Phase 1 ist reiner Dry-Run."
echo ""
