#!/bin/bash
set -e

echo "--- Sicheres Update wird ausgefuehrt ---"

# secret.json sichern (falls vorhanden); data/ ist git-ignoriert und bleibt unberuehrt
if [ -f secret.json ]; then cp secret.json secret.json.bak; fi

git fetch origin
git reset --hard origin/main

if [ -f secret.json.bak ]; then cp secret.json.bak secret.json; rm secret.json.bak; fi

find . -type f -name "*.pyc" -delete
find . -type d -name "__pycache__" -delete
chmod +x *.sh

.venv/bin/pip install -r requirements.txt --quiet

# Service-Datei kann sich geaendert haben -> neu eintragen, dann neu starten
./install_service.sh
sudo systemctl restart 1mbot.service

echo "Update fertig. Status: ./show_results.sh"
