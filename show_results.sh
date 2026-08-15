#!/bin/bash
# show_results.sh - 1mbot Paper-Trading-Status
#
# Anders als bei den candle-backtest-basierten Bots gibt es hier kein
# Auswahlmenue mit mehreren Analyse-Modi -- es wird einfach der aktuelle
# Dry-Run-Zustand aus artifacts/tracker/*.json angezeigt.
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
elif [ -f "$SCRIPT_DIR/.venv/Scripts/activate" ]; then
    source "$SCRIPT_DIR/.venv/Scripts/activate"
else
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausfuehren.${NC}"
    exit 1
fi

python3 "$SCRIPT_DIR/show_results.py" "$@"

deactivate
