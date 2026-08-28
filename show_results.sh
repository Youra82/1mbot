#!/bin/bash
# show_results.sh - 1mbot Status & Backtest-Menue
#
# Modus 1-2: Paper-Trading-Status aus artifacts/tracker/ (show_results.py).
# Modus 3-4: Backtest-Naeherungen (backtest_replay.py / backtest_multiday.py)
# -- candle-basiert, siehe src/onembot/replay.py fuer die bekannten
# Verzerrungen. Kein echter Orderbuch-Backtest wie bei den anderen Bots
# im Repo moeglich (Grid-Fills haengen vom Bid/Ask-Touch ab, nicht vom
# Candle-Close).
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
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

echo -e "\n${YELLOW}Wähle einen Modus für 1mbot:${NC}"
echo "  1) Status-Snapshot (Kontostand + Aktueller Stand + Equity-Verlauf, einmalig)"
echo "  2) Live-Watch (aktualisiert fortlaufend, für den Dauerbetrieb auf VPS/Mini-PC)"
echo "  3) Zeitraum-Backtest (ein zusammenhängender Block, backtest_replay.py)"
echo "  4) Multi-Day-Backtest (mehrere verteilte Einzeltage, backtest_multiday.py)"
read -p "Auswahl (1-4) [Standard: 1]: " MODE; MODE=${MODE:-1}

case "$MODE" in
    1)
        "$PYTHON" show_results.py
        ;;
    2)
        read -p "Aktualisierungsintervall in Sekunden [Standard: 10]: " WATCH_SEC; WATCH_SEC=${WATCH_SEC:-10}
        "$PYTHON" show_results.py --watch "$WATCH_SEC"
        ;;
    3)
        echo ""
        read -p "Anzahl Tage rückwirkend [Standard: 3]: " DAYS; DAYS=${DAYS:-3}
        read -p "Fill-Timeframe für die Näherung [Standard: 5m]: " FILL_TF; FILL_TF=${FILL_TF:-5m}
        read -p "Symbol(e), ohne /USDT:USDT (z.B. BTC ETH) [leer = Watchlist aus settings.json]: " SYMBOLS
        SYMBOLS_ARGS=""
        [ -n "$SYMBOLS" ] && SYMBOLS_ARGS="--symbols $SYMBOLS"
        echo ""
        echo -e "${GREEN}>>> Starte Zeitraum-Backtest ($DAYS Tage, $FILL_TF)...${NC}"
        "$PYTHON" backtest_replay.py --days "$DAYS" --fill-timeframe "$FILL_TF" $SYMBOLS_ARGS
        ;;
    4)
        echo ""
        TODAY=$(date +%F)
        DEFAULT_START=$(date -d "28 days ago" +%F 2>/dev/null || date -v-28d +%F)
        read -p "Start (JJJJ-MM-TT) [Standard: $DEFAULT_START]: " START_DATE; START_DATE=${START_DATE:-$DEFAULT_START}
        read -p "Ende (JJJJ-MM-TT) [Standard: $TODAY]: " END_DATE; END_DATE=${END_DATE:-$TODAY}
        read -p "Anzahl gleichmäßig verteilter Tage [Standard: 20]: " COUNT; COUNT=${COUNT:-20}
        read -p "Fill-Timeframe für die Näherung [Standard: 1m]: " FILL_TF; FILL_TF=${FILL_TF:-1m}
        read -p "Symbol(e), ohne /USDT:USDT (z.B. BTC ETH) [leer = Watchlist aus settings.json]: " SYMBOLS
        SYMBOLS_ARGS=""
        [ -n "$SYMBOLS" ] && SYMBOLS_ARGS="--symbols $SYMBOLS"
        echo ""
        echo -e "${BLUE}Hinweis: Bitgets öffentliche REST-API liefert 1m-Historie nur ca. 28 Tage zurück.${NC}"
        read -p "Exchange (bitget/binance) [Standard: bitget]: " EXCHANGE; EXCHANGE=${EXCHANGE:-bitget}
        echo ""
        echo -e "${GREEN}>>> Starte Multi-Day-Backtest ($COUNT Tage zwischen $START_DATE und $END_DATE, $EXCHANGE)...${NC}"
        "$PYTHON" backtest_multiday.py --start "$START_DATE" --end "$END_DATE" --count "$COUNT" \
            --fill-timeframe "$FILL_TF" --exchange "$EXCHANGE" $SYMBOLS_ARGS
        ;;
    *)
        echo -e "${RED}Ungültige Auswahl.${NC}"
        deactivate
        exit 1
        ;;
esac
RC=$?

deactivate
exit $RC
