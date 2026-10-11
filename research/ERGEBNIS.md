# Ergebnis H1/H2 (2026-10-10) — beide NICHT bestanden

Präregistrierung: PRAEREGISTRIERUNG.md (sha256 649b1737…7a90, inkl. Nachtrag 1).
Daten: Tardis-Monatserste. IS 13 Tage / 509 Coin-Tage, OOS 10 Tage / 399 Coin-Tage. 40 Coins, Binance + Bitget, Top-of-Book und Trades.

| | Auswahl IS | OOS n | Trades/Tag | brutto/Trade | netto/Trade | t (Cluster) | Tage + |
|---|---|---|---|---|---|---|---|
| H1 Mid-Abweichung, L = 500 ms | Y 0,15 %, H 60 s | 6.821 | 682 | −0,094 % | −0,214 % | −16,2 | 0/10 |
| H1, L = 1000 ms | " | | | | −0,215 % | −16,1 | 0/10 |
| H2 Binance-Orderflow, L = 500 ms | K 10, H 60 s | 184.317 | 18.432 | −0,030 % | −0,150 % | −86 | 0/10 |

Alle 12 IS-Varianten beider Hypothesen sind schon brutto negativ, an 0 von 13 Tagen positiv.

## Mechanismus-Diagnose (nur IS, scratchpad diag_h1.py)

Grundlage sind 2.686 Signale (dev ≥ 0,25 %, auf 60 s entkoppelt). Bitget-Mid in Signalrichtung:

| Verzögerung L | Bewegung 0 → L | danach 5 s | danach 30 s |
|---|---|---|---|
| 0 ms | 0 | +0,304 % | +0,299 % |
| 100 ms | +0,117 % | +0,186 % | +0,182 % |
| 500 ms | +0,304 % | +0,001 % | −0,003 % |
| 2000 ms | +0,308 % | −0,004 % | −0,006 % |

Der Lead-Lag ist **echt, aber nach ≤ 500 ms vollständig eingepreist**. 40 % davon passieren in
den ersten 100 ms. Nach der Verzögerung bleiben Spread (Median 0,05 %) und Taker-Gebühr
übrig. Das ist ein HFT-Feld mit Co-Location und Reaktionszeit unter 50 ms. Von einem VPS
(≈ 150 ms nur für den Empfang) ist es nicht erreichbar.

Damit ist auch das 1m-Ergebnis vom 2026-10-09 erklärt: Der Effekt entsteht dort durch
Kerzenschlüsse, die die Sub-Sekunden-Reaktion verdecken.
