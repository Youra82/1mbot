#!/bin/bash
# run_pipeline.sh - 1mbot Grid-Parameter-Trainings-Pipeline (Optuna, pro Symbol)
#
# Anders als bei den candle-backtest-basierten Bots gibt es hier keine
# Pro-Symbol-Configs auf der Festplatte -- das Ergebnis landet als
# per_symbol_overrides-Eintrag direkt in settings.json (siehe optimizer.py
# und src/onembot/utils/symbol_settings.py).
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
    source .venv/Scripts/activate
else
    echo -e "${RED}Fehler: Virtuelle Umgebung nicht gefunden. Bitte install.sh ausfuehren.${NC}"
    exit 1
fi
PYTHON="python3"
command -v python3 &>/dev/null || PYTHON="python"

echo -e "${GREEN}✔ Virtuelle Umgebung wurde erfolgreich aktiviert.${NC}"
echo ""
echo -e "${BLUE}=======================================================${NC}"
echo "      1mbot Grid-Optimierungs-Pipeline (Optuna, pro Symbol)"
echo -e "${BLUE}=======================================================${NC}"

# --- Symbole ---
echo ""
read -p "Symbol(e) eingeben (ohne /USDT:USDT, z.B. BTC ETH) [leer=Watchlist aus settings.json]: " SYMBOLS
SYMBOLS_ARGS=""
if [ -n "$SYMBOLS" ]; then
    SYMBOLS_ARGS="--symbols $SYMBOLS"
else
    WATCHLIST=$("$PYTHON" -c "import json; s=json.load(open('settings.json')); print(' '.join(s['watchlist']))" 2>/dev/null)
    echo -e "  ${BLUE}Watchlist: $WATCHLIST${NC}"
fi

# --- Trainingsfenster ---
echo ""
echo -e "${BLUE}--- Hinweis: Bitgets oeffentliche REST-API liefert 1m-Historie nur ca. 28 Tage zurueck.${NC}"
echo -e "${BLUE}    Fuer aeltere Zeitraeume unten bei Exchange 'binance' waehlen (siehe README Cross-Exchange-Check).${NC}"
TODAY=$(date +%F)
DEFAULT_START=$(date -d "28 days ago" +%F 2>/dev/null || date -v-28d +%F)
read -p "Start des Trainingsfensters (JJJJ-MM-TT) [Standard: $DEFAULT_START]: " START_DATE; START_DATE=${START_DATE:-$DEFAULT_START}
read -p "Ende des Trainingsfensters (JJJJ-MM-TT) [Standard: $TODAY]: " END_DATE; END_DATE=${END_DATE:-$TODAY}
read -p "Anzahl gleichmaessig verteilter Tage [Standard: 20, wie die README-Kalibrierung]: " COUNT; COUNT=${COUNT:-20}
read -p "Fill-Timeframe fuer die Naeherung [Standard: 1m]: " FILL_TF; FILL_TF=${FILL_TF:-1m}

echo ""
read -p "Exchange fuer den historischen Abruf (bitget/binance) [Standard: bitget]: " EXCHANGE; EXCHANGE=${EXCHANGE:-bitget}

# --- Optuna-Parameter ---
echo ""
read -p "Optuna-Trials pro Symbol [Standard: 60]: " TRIALS; TRIALS=${TRIALS:-60}
echo ""
echo "Mindest-Gewinntage-Quote und Mindest-Fills wirken als Gate (Strict-Modus):"
echo "  Ein Trial zaehlt nur, wenn BEIDE Schwellen erreicht werden -- maximiert PnL"
echo "  ist sonst trivial durch 'praktisch nie handeln' erreichbar (0 Fills = 0 Verlust)."
read -p "Mindest-Gewinntage-Quote (0-1) [Standard: 0.5]: " MIN_WIN_RATIO; MIN_WIN_RATIO=${MIN_WIN_RATIO:-0.5}
read -p "Mindest-Fills ueber das In-Sample-Fenster [Standard: 20]: " MIN_FILLS; MIN_FILLS=${MIN_FILLS:-20}

echo ""
echo "In-Sample-/Out-of-Sample-Split (wie ltbbot/stbot): Optuna sieht beim Suchen nur"
echo "  die aeltesten X% der ausgewaehlten Tage -- der Rest (juengste Tage) bestaetigt"
echo "  danach den besten gefundenen Parametersatz, ohne dass Optuna ihn je gesehen hat."
read -p "In-Sample-Anteil [Standard: 0.7]: " IS_FRACTION; IS_FRACTION=${IS_FRACTION:-0.7}

# --- Uebernahme ---
echo ""
echo -e "${YELLOW}Möchtest du die besten gefundenen Parameter automatisch als per_symbol_overrides in settings.json übernehmen?${NC}"
echo "  (Nur Symbole, die die Gates oben erreichen, werden uebernommen -- Rest bleibt beim globalen grid-Block.)"
read -p "Automatisch übernehmen? (j/n) [Standard: n]: " APPLY_CHOICE; APPLY_CHOICE=${APPLY_CHOICE:-n}
APPLY_ARG=""
if [[ "$APPLY_CHOICE" == "j" || "$APPLY_CHOICE" == "J" ]]; then
    APPLY_ARG="--apply"
fi

echo ""
echo -e "${BLUE}=======================================================${NC}"
echo -e "${GREEN}>>> Starte Optimierung...${NC}"
echo -e "${BLUE}=======================================================${NC}"

"$PYTHON" optimizer.py \
    $SYMBOLS_ARGS \
    --start "$START_DATE" \
    --end "$END_DATE" \
    --count "$COUNT" \
    --fill-timeframe "$FILL_TF" \
    --exchange "$EXCHANGE" \
    --trials "$TRIALS" \
    --min-win-ratio "$MIN_WIN_RATIO" \
    --min-fills "$MIN_FILLS" \
    --is-fraction "$IS_FRACTION" \
    $APPLY_ARG
RC=$?

deactivate
echo ""
echo -e "${BLUE}=======================================================${NC}"
if [ $RC -eq 0 ]; then
    echo -e "${GREEN}✔ Pipeline abgeschlossen!${NC}"
else
    echo -e "${RED}Pipeline mit Fehler beendet (Exit Code: $RC).${NC}"
fi
echo -e "${BLUE}=======================================================${NC}"
exit $RC
