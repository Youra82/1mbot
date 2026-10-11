# Präregistrierung — Upbit-KRW-Listing → Binance-Perp

Festgelegt am 2026-10-10, **bevor** Kursdaten zu den Ereignissen geladen oder angesehen
wurden. Bekannt sind bisher nur die Ankündigungs-Metadaten (`upbit_notices.json`: Titel, Zeit).

## Mechanismus

Upbit ist die größte Börse Koreas. Ein neues KRW-Paar öffnet einen großen, sonst
abgeschotteten Kreis von Privatanlegern für den Coin. Die Nachfrage-Erwartung lässt den
Preis weltweit sofort springen, auch auf Binance und Bitget. Zwei gegensätzliche Effekte
sind plausibel:
- **L1, Fortsetzung:** Nachzügler, langsame Bots und Menschen tragen den Sprung über Minuten weiter.
- **L2, Umkehr:** Der Sprung ist Euphorie. Nach dem Hype fällt der Kurs zurück („sell the news“).

## Ereignisse

- Grundlage: Upbit-Ankündigungen, Kategorie Handel.
- Ereignis = Titel enthält „KRW“ **und** („신규 거래지원“ **oder** „디지털 자산 추가“ **oder** „마켓 추가“).
- **T** = `first_listed_at`, die Erstveröffentlichung.
- **Ticker:** Großbuchstaben/Ziffern (2–10 Zeichen) in Klammern im Titel. Mehrere Ticker im Titel ergeben mehrere Ereignisse zur selben Zeit.
- **Handelbar** heißt: Binance-USDT-M-Perp `<TICKER>USDT`, alternativ `1000<TICKER>USDT`, hat in der Stunde vor T mindestens 20 Trades. Sonst wird das Ereignis verworfen und gezählt.
- **Dubletten:** Pro Ticker zählt nur das erste Ereignis innerhalb von 30 Tagen.
- **IS (Auswahl):** T < 2025-07-01. **OOS (Nachweis):** T ≥ 2025-07-01. Der Code sperrt OOS ohne gespeicherte Auswahl.

## Preise und Kosten

- **L1:** Binance-USDT-M-aggTrades (Tick-genau, data.binance.vision).
  - Einstieg = VWAP aller Trades in [T+D, T+D+10 s].
  - Ausstieg = VWAP in [T+D+H, T+D+H+10 s].
  - Gibt es in einem Fenster keine Trades, wird das Ereignis verworfen.
- **L2:** Binance-1m-Klines. Einstieg zum Open der Minute T+E, Ausstieg zum Open der Minute T+E+H.
- **Kosten pro Seite:** 0,06 % Gebühr plus **0,20 % Slippage**, also **0,52 % hin und zurück**.
  Das ist bewusst hoch, weil Listing-Sprünge chaotisch sind. Funding wird vernachlässigt
  (L1 ≤ 4h; bei L2 wird es angegeben, falls erheblich).

## Regeln und Raster (nur IS-Auswahl)

- **L1 Long:** D ∈ {30 s, 60 s, 180 s} × H ∈ {5 min, 30 min, 4 h}, also 9 Varianten.
- **L2 Short:** E ∈ {30 min, 2 h} × H ∈ {24 h, 72 h}, also 4 Varianten.
- **Auswahl:** Je Hypothese die Variante mit dem höchsten IS-t-Wert unter allen mit n ≥ 30.

## Statistik und Bestehen (OOS, je Hypothese)

- Ereignisse zur selben Minute (mehrere Ticker) werden zu **einem Cluster** gemittelt. t wird auf den Cluster-Mitteln berechnet.
- Zwei Hypothesen ergeben **t ≥ 2,24**. Das entspricht einseitig α = 0,0125, also Bonferroni 0,025/2, konservativ.
- Bestehen heißt, alles gleichzeitig:
  1. t ≥ 2,24,
  2. mittleres Netto > 0,
  3. Median des Nettos > 0,
  4. ohne das beste Ereignis bleibt das Mittel > 0,
  5. n ≥ 30 Cluster.

## Pflicht-Diagnose (kein Kriterium, aber wird berichtet)

- **Vorlauf:** Kursbewegung von T−5 min bis T. Ist sie groß, stimmt der Zeitstempel nicht
  (die Ankündigung war früher sichtbar), oder es gab Insider bzw. ein Leck. Dann wäre der
  Test nicht handelbar.
- **Sprungprofil:** mittlere Bewegung T → T+10 s, +30 s, +60 s, +180 s. Das zeigt, wie viel
  der Bewegung vor unserer frühesten Reaktion schon gelaufen ist.

## Danach

Was besteht, kommt zuerst in einen Live-Wächter im Dry-Run. Er misst die echte Verzögerung
von der Ankündigung bis zur möglichen Order und die echten Bitget-Fills.
