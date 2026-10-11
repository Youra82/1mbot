# Ergebnis Upbit-KRW-Listing (2026-10-10)

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
