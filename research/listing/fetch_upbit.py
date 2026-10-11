"""Alle Upbit-Handelsankuendigungen -> upbit_notices.json (nur Metadaten, keine Kurse)."""
import json, time, requests
U = "https://api-manager.upbit.com/api/v1/announcements?os=web&page={p}&per_page=20&category=trade"
out, p = [], 1
while True:
    for a in range(8):
        try:
            d = requests.get(U.format(p=p), headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()["data"]; break
        except Exception:
            time.sleep(2 ** a)
    else:
        raise RuntimeError(f"Seite {p} fehlgeschlagen")
    out += d["notices"]
    if p >= d["total_pages"]:
        break
    p += 1; time.sleep(1.0)
json.dump(out, open("upbit_notices.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(out), "Ankuendigungen")
