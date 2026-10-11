# Präregistrierung 1mbot — Sekunden-Lead-Lag Binance → Bitget

Festgelegt am 2026-10-10, **bevor** die historischen Daten ausgewertet wurden. Den Download
habe ich nur technisch geprüft: Zeilenzahlen und Latenz von BTC und TIA am 2026-09-01, keine
Renditen. Die SHA-256 dieser Datei steht in `PRAEREGISTRIERUNG.sha256`. Jede spätere Änderung
fällt dadurch auf.

## Daten

- Tardis.dev, kostenlose Monatsersten 2024-12-01 … 2026-10-01 (23 Tage), Binance-Futures und
  Bitget-Futures, `quotes` (Top-of-Book) und `trades`.
- 40 feste Coins, siehe `hist_fetch.py: COINS`. Ein Coin-Tag zählt nur, wenn beide Börsen Daten haben.
- Verdichtet mit dem Recorder-Code (BookThrottle 100 ms, 1-s-Trades). Das ist exakt das Format
  der späteren VPS-Aufzeichnung.
- **IS (Auswahl):** 2024-12-01 … 2025-12-01 (13 Tage). **OOS (Nachweis):** 2026-01-01 … 2026-10-01 (10 Tage).
  OOS bleibt unberührt, bis die IS-Auswahl feststeht und hier eingetragen ist.

## Mechanismus (Warum sollte das funktionieren?)

Binance hat bei den meisten Coins ein Vielfaches des Bitget-Volumens. Neue Information
(große Order, Liquidation, News) bewegt den Binance-Preis zuerst. Bitget-Market-Maker ziehen
mit Verzögerung nach. Das belegt der 1m-Test vom 2026-10-09: Kanal B, t ≈ +30 IS / +26 OOS,
etwa 75 % des Effekts in der ersten Minute. Erwartet wird, dass die Verzögerung bei auf Bitget
dünnen Coins größer ist.

## H1 — Regel (fest, keine weiteren Parameter)

Zu jedem Zeitpunkt t gilt das, was zu `recv_ms ≤ t` bekannt war:
- `mid = (bid+ask)/2` je Börse, `d(t) = ln(mid_bn) − ln(mid_bg)`
- `base(t)` = Median von d über die letzten 10 Minuten, abgetastet jede Sekunde
- `dev(t) = d(t) − base(t)`. Positiv heißt, Binance liegt relativ höher, also ist Bitget im Rückstand und soll steigen.

**Signal** (geprüft bei jedem Binance-Buch-Update):
- `dev ≥ +Y`: **Long** auf Bitget. `dev ≤ −Y`: **Short** auf Bitget.
- Vorbedingungen: Das Binance-Buch ist höchstens 1 s alt, das Bitget-Buch existiert, und es gibt mindestens 10 Minuten Basis-Historie.
- Es gibt keine Bedingung an die Bitget-Aktualität. Ein veraltetes Bitget-Buch ist genau der Effekt.

**Ausführung (konservativ):**
- Einstieg zum Zeitpunkt t + L, als **Taker** zum Bitget-Ask (Long) bzw. -Bid (Short), mit dem Buchstand, der zu t + L bekannt ist.
- Ausstieg nach genau H Sekunden ab Einstieg, als Taker zum Bitget-Bid (Long) bzw. -Ask (Short). Es gibt keinen TP und keinen SL, damit es keine Pfad-Optimierung gibt.
- **Gebühren:** 0,06 % pro Seite (Bitget-Taker VIP0), also 0,12 % hin und zurück.
- **Füllbarkeit:** Ist die Top-of-Book-Menge auf der Einstiegsseite kleiner als 100 USDT Notional, wird der Trade verworfen.
- Pro Coin höchstens eine Position gleichzeitig. Nach dem Ausstieg hat dieser Coin H Sekunden Sperre.
- **Datenlücken:** Liegt zwischen Einstieg und Ausstieg ein Buch-Update-Abstand von mehr als 30 s auf Bitget, wird der Trade verworfen und gezählt.

**Raster (nur IS):** Y ∈ {0,15 %, 0,25 %, 0,40 %} × H ∈ {5, 15, 30, 60} s, also 12 Varianten.
Primäre Latenz L = 500 ms. Das ist konservativ: VPS → Börse ≈ 150 ms je Richtung plus Rechenzeit.
Die Tardis-Empfangszeit ist ein Collector nahe der Börse (Binance ≈ 2 ms). Unsere Latenz
ist deshalb vollständig über L zu modellieren.

**Auswahl auf IS:** Die Variante mit dem höchsten mittleren Netto pro Trade unter allen mit n ≥ 200 Trades.

## Bestehenskriterien OOS (alle müssen erfüllt sein)

1. Mittleres Netto pro Trade > 0 mit t ≥ 2,0. Der Standardfehler ist nach Coin-Tag geclustert.
2. Mindestens 7 von 10 OOS-Tagen sind netto positiv (Summe aller Trades des Tages).
3. Mindestens 20 Trades pro Tag im Schnitt über das Universum (Tauglichkeit für das Tagesziel).
4. **Robustheit:** Bei L = 1000 ms ist das mittlere Netto weiterhin > 0. Ein Bestehen nur bei 500 ms gilt als „latenzkritisch“ und braucht vor Live eine VPS-Latenzmessung.
5. Das Ergebnis hängt nicht an einem Coin: Ohne den besten Coin bleibt das mittlere Netto > 0.

Werden die Kriterien nicht erfüllt, ist H1 **nicht bestanden**. Es wird dann nicht am OOS nachoptimiert.

## H2 (sekundär, gleiche Ausführungsregeln)

Aggressiver Orderflow auf Binance führt Bitget.
`flow(t)` = (buy_usd − sell_usd) auf Binance über die letzten 3 abgeschlossenen Sekunden,
normiert durch den Median von |flow| über 60 Minuten. Das Signal ist `|flow_norm| ≥ K`, mit
Richtung = Vorzeichen.
Raster K ∈ {10, 20, 40} × H ∈ {5, 15, 30, 60}. Gleiche Auswahl, gleiche OOS-Kriterien.

**Mehrfachtest:** Weil zwei Hypothesen geprüft werden, gilt in Kriterium 1 für **beide**
t ≥ 2,3 statt 2,0 (Bonferroni).

## Danach

Was besteht, läuft unverändert mindestens 7 Tage im **Vorwärtstest auf den VPS-Recorder-Daten**
(gleicher Code, echte VPS-Latenz). Erst danach kommt eine Live-Entscheidung mit 100 USDT.
Wer daran etwas ändert, braucht eine neue Präregistrierung.

## Nachtrag 1 (2026-10-10, vor jeder Auswertung, nur Code-Prüfung)

Die Lücken-Regel „Bitget-Update-Abstand > 30 s → verwerfen“ ist ungeeignet. Das Buch wird nur
bei **Preisänderung** geschrieben (BookThrottle). Bei dünnen Coins sind über 30 s ohne
Preisänderung normal und keine Datenlücke. Die Regel hätte gerade die Coins verworfen, bei
denen der Effekt erwartet wird.
Neu: Verworfen wird erst ab **10 Minuten** ohne Bitget-Preisänderung. Das deutet auf einen
echten Ausfall hin. Bei Recorder-Daten (Vorwärtstest) zählen zusätzlich die Disconnect-Events.
Der alte Hash war 772d8a1c…9d6f. Ab jetzt gilt der neue Hash in PRAEREGISTRIERUNG.sha256.
