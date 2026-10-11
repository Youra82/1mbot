#!/bin/bash
# systemd-Service (neu) eintragen -- Dauerlauf-Prozess, kein Cron
set -e
SERVICE_SRC="$(pwd)/deploy/1mbot.service"
SERVICE_DST="/etc/systemd/system/1mbot.service"
sed "s#/home/REPLACE_ME/botprojekte/1mbot#$(pwd)#g" "$SERVICE_SRC" | sudo tee "$SERVICE_DST" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable 1mbot.service > /dev/null 2>&1
echo "Service 1mbot.service eingetragen."
