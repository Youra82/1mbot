# Ergebnis Upbit-KRW-Listing (2026-10-10)

> **KORREKTUR 09.11.2026 — gilt vor allen Zahlen unten.** Ein unabhängiges Codex-Review (evobot,
> `CODEX_REVIEW_RESEARCH.md`) fand einen Formelfehler. Die Short-Rendite war `entry/exit − 1` und damit die eines
> **inversen** Kontrakts. Bitget-USDT-Perps sind aber **linear**: `1 − exit/entry − c·(1 + exit/entry)`
> (`rules.linear_short`). Die alte Formel überschätzt Gewinne und deckelt Squeeze-Verluste: Bei Kursverdopplung
> gab sie −50 % statt −100 %. Neu gerechnet mit unveränderter präregistrierter Auswahl (E 30 min, H 72 h); die alten
> Dateien liegen als `*_inverse_2026-10-10.*` daneben:
>
> | L2 | alt (invers) | **korrekt (linear)** |
> |---|---:|---:|
> | OOS netto je Ereignis (n 79) | +15,0 %, t 4,45 | **+6,12 %, t 1,77 → nicht bestanden** (Schwelle 2,24) |
> | OOS inkl. Funding | +9,3 % | **+0,28 %** (Median +6,0 %) |
> | IS inkl. Funding | +7,3 % | +2,62 % |
> | 3x / Stop +15 % auf Margin, OOS | +11,8 %, t 1,40 | **−0,36 %, t −0,06** |
>
> **Fazit neu: Die KRW-Listing-Short-Nische hat nach Funding und Umsetzung keinen Vorteil.** Der scheinbare Edge
> kam aus gedeckelten Squeeze-Verlusten. Der Papier-Wächter rechnet ab diesem Stand korrekt. Bereits
> geschlossene Papier-Trades vor dem Update sind mit der alten Formel gebucht.
> Übertragung auf **Nicht-KRW**-Listings (nur BTC/USDT-Markt), eigene Präregistrierung evobot evo-004, besteht
> auch linear: +7,48 % je Cluster, t 4,22; Funding dort nur −0,3 %, Pumps klein.

Präregistrierung: PRAEREGISTRIERUNG.md (sha256 6774eaf3…764c). 216 KRW-Listing-Ankündigungen
(2021–2026). Davon haben 131 einen Binance-USDT-M-Perp, der vor T aktiv war. IS 50 / OOS 81.

**Diagnose**
- Kein Vorlauf vor T: −5 min bis T ≈ +0,1 bis +0,25 %. Die Zeitstempel sind sauber.
- Der Sprung ist im Mittel **+20 % in den ersten 10 s** und danach praktisch fertig: +24 % nach 30 s, +23 % nach 10 min.

| | Auswahl (IS) | OOS netto/Ereignis | Median | t | Ergebnis |
|---|---|---|---|---|---|
| L1 Long hinterher | D 180 s, H 30 min | +0,76 % | +0,48 % | 0,91 | nicht bestanden |
| **L2 Short ab T+30 min, 72 h** | E 30 min, H 72 h | **+15,0 %** | +10,8 % | **4,45** | **BESTANDEN** (präreg.) |

## Nachprüfung L2 (diag_l2.py, sizing_l2.py)

- **Funding** (Binance, Short zahlt bei negativer Rate): im Mittel −4,4 % (IS) bzw. −5,6 % (OOS)
  pro Trade, im Extremfall −44 %. Inklusive Funding bleiben IS +7,3 % und OOS +9,3 % pro Ereignis.
- **Zwischenverlust gegen den Short:** OOS-Median +13 %, in 23 % der Fälle über +33 %, in 10 % über
  +50 %, maximal +265 %.
- **Umsetzung mit isolierter Margin:** Liquidation bei 1/L − 1 %. Stop-Fill = Stop + 1 % Slippage,
  Funding anteilig. Die Wahl erfolgte auf IS nach log-Wachstum bei 20 % Kapital pro Ereignis:
  **3x, Stop +15 %**. Ergebnis OOS: +11,8 % auf Margin pro Ereignis, Median +3,9 %, **t 1,40**,
  1 % liquidiert. Ohne Stop mit 1x: +6,4 %, t 1,47.
- Heute auf Bitget gelistet: 98 % der OOS-Coins.
- **Häufigkeit:** etwa 5 Ereignisse pro Monat. Das ist **kein tägliches Einkommen**.

**Fazit:** Das Rohsignal ist echt und robust. Der Mechanismus ist eine Prämie für das Risiko,
frisch gepumpte Coins zu shorten (Squeeze, negatives Funding): Genau dieses Risiko hält andere
ab. Nach realistischer Umsetzung ist der Vorteil im Mittel stark positiv, aber nicht mehr
signifikant (t ≈ 1,4–1,6). Nächster ehrlicher Schritt ist ein Dry-Run-Wächter. Er misst das
Bitget-Funding, die echte Reaktionszeit und Fills.
