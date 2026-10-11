# Präregistrierung — Ideen-Batterie 5m bis 4h (Binance USDT-M, 40 Coins)

Festgelegt am 2026-10-10, **bevor** die Langzeitdaten ausgewertet wurden. Zu diesem Zeitpunkt
lief nur der Download. Den Abruf habe ich auf zwei Monaten BTC technisch geprüft, ohne
Renditen anzusehen. Die SHA-256 steht in `PRAEREGISTRIERUNG.sha256`.

## Gemeinsame Regeln (für alle Hypothesen gleich)

- **Daten:** data.binance.vision mit 5m-Klines (inkl. Taker-Kaufvolumen), Premium-Index 5m,
  Funding und Metrics 5m (OI, Long/Short-Ratios). 40 Coins wie in `fetch_binance.py`. Ein Coin
  zählt ab dem Monat, ab dem Daten existieren.
- **IS (Auswahl):** 2022-01-01 … 2024-06-30. **OOS (Nachweis):** 2024-07-01 … 2026-09-30.
  Der Code sperrt OOS, solange keine IS-Auswahl gespeichert ist.
- **Kein Lookahead:**
  - Das Signal entsteht aus Daten bis zum Schluss der 5m-Kerze t.
  - Der Einstieg erfolgt zum Open der Kerze t+1, der Ausstieg zum Open der Kerze t+1+h.
  - Metrics-Werte gelten erst eine Kerze nach `create_time`.
  - Funding ist erst ab dem Abrechnungszeitpunkt bekannt.
- **Kosten:** 0,10 % pro Seite (0,06 % Taker + 0,04 % Slippage), also 0,20 % hin und zurück.
  Bei Short-Haltedauer über einen Funding-Zeitpunkt wird das Funding verrechnet
  (Long zahlt +rate, Short erhält +rate).
- **Keine Überlappung:** Pro Coin und Hypothese höchstens eine Position. Neue Signale während
  einer offenen Position werden ignoriert. Kein SL/TP außer dort, wo die Regel ihn nennt.
- Kerzen mit Volumen 0 oder Datenlücke im Haltefenster: Der Trade wird verworfen.
- **Statistik:**
  - Tägliche Serie = Mittel der Netto-Renditen aller an diesem Tag eröffneten Trades.
    Tage ohne Trades fehlen in der Serie.
  - t-Wert mit Newey-West-Standardfehler (Lag 5) auf dieser Tagesserie.
  - Beim Cross-Section-Portfolio (A) ist die Serie die Tagessumme der Portfolio-Rendite.
- **Auswahl auf IS:** Je Hypothese gewinnt die Rastervariante mit dem höchsten IS-t-Wert unter
  allen mit n ≥ 100 Trades. Nur diese **eine** Variante geht in OOS.
- **Mehrfachtest:** 9 Hypothesen ergeben Bonferroni α = 0,05/9, einseitig, also **t ≥ 2,54**.

## Bestehenskriterien OOS (alle)

1. Newey-West-t der Tagesserie ≥ 2,54.
2. Mittleres Netto pro Trade > 0.
3. Mindestens 55 % der OOS-Monate positiv (Summe der Trade-Netto-Renditen).
4. Ohne den besten Coin bleibt das mittlere Netto > 0.

Zusätzlich berichtet (kein Kriterium): Trades pro Tag, Anteil positiver Tage und eine
Kontosimulation mit 100 USDT und verschiedenen Hebeln (Tagesziel 1–2 %).

## Hypothesen (Mechanismus → feste Regel → Raster nur für die IS-Auswahl)

**A — Cross-Section-Reversal (Liquiditätsprämie).** Wer kurzfristig ohne Information überschießt,
läuft zurück, weil Liquiditätsgeber für das Gegenrisiko bezahlt werden.
Zur vollen Stunde: Coins nach Rendite über die letzten L minus Mittel aller Coins ordnen.
Long auf die 5 schwächsten, Short auf die 5 stärksten, gleich gewichtet, halten für H.
Portfolio-Rendite = Mittel aus Long-Bein und Short-Bein, minus Kosten für jedes Bein.
Raster: L ∈ {1h, 4h} × H ∈ {1h, 4h}.

**B — Flush-Reversal (erzwungene Schließungen).** Liquidationen verkaufen ohne Preisrücksicht,
der Preis schießt über.
Signal auf 5m:
- Rendite über 15 min ≤ −z·σ, mit σ = Std der 15m-Renditen der letzten 24 h,
- **und** OI fällt in diesen 15 min um mindestens y,
- → **Long**.
Spiegelbildlich (Rendite ≥ +z·σ und OI fällt) → **Short**. Halten für H.
Raster: z ∈ {3, 4} × y ∈ {1 %, 2 %} × H ∈ {1h, 4h}.

**C — Funding-Abrechnungs-Drift.** Wer zahlen müsste, schließt vor der Abrechnung, danach baut
er die Position wieder auf.
Für jede Abrechnung T (00/08/16 UTC) gilt die zuletzt abgerechnete Rate r (bekannt). Bei
|r| ≥ thr: **vor** T in Gegenrichtung der Zahler (r > 0 → Short) von T−w bis T, **nach** T in
Zahlerrichtung (r > 0 → Long) von T bis T+w. Beide Beine zählen als Trades derselben Hypothese.
Raster: thr ∈ {0,03 %, 0,08 %} × w ∈ {30 min, 60 min}.

**D — Premium-Extrem (überfüllter Hebel).** Ein Perp-Aufschlag, der extrem über dem Index liegt,
bedeutet überfüllte Longs, die abgebaut werden. Spiegelbildlich für Shorts.
z = (Premium-Close − Mittel 7 Tage) / Std 7 Tage, auf 5m. z ≥ +k → Short, z ≤ −k → Long. Halten für H.
Raster: k ∈ {3, 4} × H ∈ {1h, 4h, 24h}.

**E — Taker-Erschöpfung (temporärer Preisimpact).** Aggressive Order-Ströme ohne Information
verschieben den Preis nur vorübergehend.
Auf jeder vollen Stunde:
- Taker-Kaufanteil der letzten Stunde (taker_buy_quote/quote) ≥ Quantil q der letzten 30 Tage
  **und** Stundenrendite > 0 → **Short**.
- Spiegelbildlich bei Anteil ≤ Quantil 1−q **und** Rendite < 0 → **Long**.
Halten für H. Raster: q ∈ {0,99, 0,995} × H ∈ {1h, 4h}.

**F — Top-Trader-Crowding.** Extreme Positionierung der großen Konten ist kontraindikativ.
z = z-Score von `sum_toptrader_long_short_ratio` über 30 Tage (5m-Werte), geprüft zur vollen
Stunde. z ≥ k → Short, z ≤ −k → Long. Halten für H.
Raster: k ∈ {2, 3} × H ∈ {8h, 24h}.

**G — Trendfolge 4h (Time-Series-Momentum, Donchian).** Langsame Informationsverarbeitung und
Herdenverhalten erzeugen Trends.
Auf 4h-Kerzen (aus 5m gebaut):
- Close > Hoch der letzten N Kerzen → **Long**,
- Ausstieg, wenn Close < Tief der letzten N/2 Kerzen,
- Short spiegelbildlich.
Ein- und Ausstieg zum Open der nächsten 4h-Kerze. Der Ausstieg ist hier der in der Regel
genannte Exit, kein festes H.
Raster: N ∈ {20, 55} × Richtung ∈ {nur Long, Long+Short}.

**H — OI-Zufluss-Fortsetzung (neues gehebeltes Geld).** Steigendes OI mit Kursbewegung
bedeutet frisches Positionieren, und der Trend setzt sich fort.
Zur vollen Stunde: OI über 1h ≥ +y **und** 1h-Rendite ≥ +z·σ (σ = Std der 1h-Renditen über
7 Tage) → Long. Spiegelbildlich (OI ≥ +y, Rendite ≤ −z·σ) → Short. Halten für H.
Raster: y ∈ {3 %, 6 %} × z ∈ {2} × H ∈ {1h, 4h}.

**I — Einzelkerzen-Impact-Fade.** Eine einzelne riesige 5m-Kerze mit Volumen-Spitze ist
meist eine große Market-Order, deren Impact teilweise zurückgeht.
|Rendite 5m| ≥ k·σ5 (σ5 = Std der 5m-Renditen über 24 h) **und** Volumen ≥ 5 × Median-Volumen
der letzten 24 h → Gegenrichtung, halten für H.
Raster: k ∈ {5, 8} × H ∈ {15 min, 1h}.

## Danach

Was besteht, kommt nicht sofort live. Zuerst prüfe ich die Ausführung auf Bitget-Ticks, wo
Tardis-Tage vorhanden sind. Danach folgt ein mindestens 14-tägiger Vorwärtstest im Dry-Run,
mit unverändertem Code und als eine gemeinsame Signalfunktion für Live und Backtest.
Was nicht besteht, wird nicht am OOS nachjustiert.
