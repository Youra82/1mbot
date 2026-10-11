# Ergebnis Ideen-Batterie A–I (2026-10-10) — 0 von 9 bestanden

Präregistrierung: PRAEREGISTRIERUNG.md (sha256 f2366640…7c7c). Grundlage sind 40 Coins Binance
USDT-M mit 5m-Daten. IS 2022-01 … 2024-06, OOS 2024-07 … 2026-09. Kosten 0,20 % hin und
zurück plus Funding. Der Placebo-Test (Zufallspfade, `placebo_make.py`) liefert für alle
Hypothesen ≈ −0,20 %. Die Engine hat also keinen Lookahead.

| Hyp. | Idee | IS-Auswahl | OOS Trades/Tag | OOS netto/Trade | OOS t (NW) | Monate + |
|---|---|---|---|---|---|---|
| A | Cross-Section-Reversal | L 4h, H 4h | 6,0 | −0,195 % | −19,7 | 0 % |
| B | Flush-Reversal (OI ↓) | z 3, OI −2 %, H 4h | 3,2 | +0,027 % | +0,30 | 52 % |
| C | Funding-Abrechnungs-Drift | 0,08 %, 30 min | 2,4 | −0,134 % | −2,9 | 22 % |
| D | Premium-Extrem | k 4, H 24h | 9,8 | +0,019 % | +0,13 | 52 % |
| E | Taker-Erschöpfung | q 0,995, H 4h | 11,1 | −0,190 % | −7,2 | 0 % |
| F | Top-Trader-Crowding | k 3, H 24h | 1,8 | +0,173 % | +0,35 | 56 % |
| G | Donchian 4h nur Long | N 55 | 1,4 | +0,958 % | +0,36 | 33 % |
| H | OI-Zufluss-Fortsetzung | OI +6 %, H 4h | 1,0 | +0,034 % | −0,59 | 41 % |
| I | Riesenkerzen-Fade | k 8, H 15 min | 5,0 | +0,009 % | −2,42 | 41 % |

Bestehensschwelle: t ≥ 2,54 (Bonferroni 9) **und** mindestens 55 % positive Monate **und**
netto > 0 ohne den besten Coin.

Beobachtungen:
- Alles, was nur aus Kurs, Volumen, OI, Funding und Premium abgeleitet ist, liegt nach Kosten bei ≈ 0 oder darunter.
- Brutto-Effekte gibt es einige (B, D, F, G), aber sie sind kleiner als 0,2 % Kosten oder zu unregelmäßig.
- G (Trendfolge) hat einen positiven Mittelwert, lebt aber von wenigen Ausreißern: 33 % positive Monate. Das ist das Gegenteil von „täglich Gewinn“.
- Zusammen mit H1/H2 (Sekunden-Lead-Lag) sind heute 33 präregistrierte Tests gelaufen: **0 bestanden**.
