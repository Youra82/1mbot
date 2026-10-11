#!/usr/bin/env python3
"""README-Grafiken aus den echten Forschungsdaten -> docs/*.png

  python research/make_figures.py
Braucht research/listing/ev/ (fetch_events.py) und die *_result.json der Backtests.
"""
from __future__ import annotations

import json
import os
import sys

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, os.path.join(HERE, "listing"))
sys.path.insert(0, os.path.join(ROOT, "src"))

# Referenzpalette (dataviz): die ersten drei Slots sind fuer alle Paarungen validiert
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SURFACE, INK, INK2, GRID, MUTED = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e0", "#b8b7b0"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": MUTED, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "font.size": 11, "axes.titleweight": "bold", "axes.titlesize": 13,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
})


def save(fig, name):
    os.makedirs(DOCS, exist_ok=True)
    fig.savefig(os.path.join(DOCS, name), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("geschrieben:", name)


# ---------------------------------------------------------------- 1. Mechanismus
def fig_mechanism():
    import backtest_listing as bl
    ev = bl.load_events()
    sec_grid = np.arange(-120, 601, 2)                      # Sekunden um T
    min_grid = np.arange(-10, 72 * 60 + 31, 10)              # Minuten um T
    S, M = [], []
    for _, r in ev.iterrows():
        tr = pd.read_parquet(os.path.join(bl.HERE, "ev", r.tag + "_trades.parquet"))
        t, p = tr.transact_time.values, tr.price.values
        i0 = np.searchsorted(t, r.t0 - 60_000, side="right") - 1
        if i0 < 0:
            continue
        p0 = p[i0]
        idx = np.searchsorted(t, r.t0 + sec_grid * 1000, side="right") - 1
        S.append(np.where(idx >= 0, p[np.clip(idx, 0, None)] / p0 - 1, np.nan))
        k = pd.read_parquet(os.path.join(bl.HERE, "ev", r.tag + "_k1m.parquet")).sort_values("open_time")
        m0 = (r.t0 // 60_000) * 60_000
        ks = k.set_index("open_time").close.reindex(m0 + min_grid * 60_000).values
        M.append(ks / p0 - 1)
    S, M = np.array(S) * 100, np.array(M) * 100

    fig, (a, b) = plt.subplots(1, 2, figsize=(13, 4.8), gridspec_kw={"width_ratios": [1, 1.6]})
    for ax, x, Y, unit in ((a, sec_grid, S, "Sekunden"), (b, min_grid / 60, M, "Stunden")):
        med = np.nanmedian(Y, axis=0)
        q1, q3 = np.nanpercentile(Y, 25, axis=0), np.nanpercentile(Y, 75, axis=0)
        ax.fill_between(x, q1, q3, color=BLUE, alpha=0.15, linewidth=0, label="mittlere 50 % der Ereignisse")
        ax.plot(x, med, color=BLUE, linewidth=2.2, label="Median")
        ax.axvline(0, color=INK2, linewidth=1, linestyle="--")
        ax.axhline(0, color=MUTED, linewidth=1)
        ax.set_xlabel(f"{unit} nach der Upbit-Ankündigung")
        ax.set_ylabel("Kurs ggü. 1 min vor Ankündigung (%)")
    a.set_title("Der Sprung: fertig nach ~10 Sekunden")
    j10 = np.nanmedian(S[:, np.searchsorted(sec_grid, 10)])
    a.annotate(f"Median +{j10:.0f} % in 10 s\n→ hinterherkaufen zu spät", xy=(10, j10), xytext=(160, j10 * 0.45),
               color=INK, fontsize=10, arrowprops=dict(arrowstyle="->", color=INK2))
    b.set_title("Danach: der Hype läuft über 72 h zurück")
    e_i = np.searchsorted(min_grid, 30)
    x_i = np.searchsorted(min_grid, 30 + 72 * 60)
    for xi, lab, col in ((e_i, "Short-Einstieg\nT + 30 min", ORANGE), (x_i, "Ausstieg\nT + 72,5 h", AQUA)):
        xv, yv = min_grid[xi] / 60, np.nanmedian(M[:, xi])
        b.scatter([xv], [yv], s=90, color=col, zorder=5, edgecolor=SURFACE, linewidth=2)
        b.annotate(lab, xy=(xv, yv), xytext=(xv + (4 if xi == e_i else -16), yv + 6), color=INK, fontsize=10,
                   arrowprops=dict(arrowstyle="->", color=INK2))
    b.legend(loc="upper right", frameon=False)
    fig.suptitle(f"Upbit-KRW-Listings 2021–2026 auf dem Binance-Perp (n = {len(S)})", fontsize=14,
                 fontweight="bold", y=1.02)
    save(fig, "listing_mechanism.png")


# ---------------------------------------------------------------- 2. Forschungsuebersicht
def fig_research():
    rows = []
    for key, lab in (("oos_h1_L500.json", "H1 Sekunden-Lead-Lag"), ("oos_h2_L500.json", "H2 Binance-Orderflow")):
        d = json.load(open(os.path.join(HERE, key)))["oos"]
        rows.append((lab, d["mean_net_pct"], False))
    names = {"A": "A Cross-Section-Reversal", "B": "B Liquidations-Flush", "C": "C Funding-Abrechnung",
             "D": "D Premium-Extrem", "E": "E Taker-Erschöpfung", "F": "F Top-Trader-Crowding",
             "G": "G Trendfolge 4h", "H": "H OI-Zufluss", "I": "I Riesenkerzen-Fade"}
    for f in ("oos_result_ACDEGI.json", "oos_result_BFH.json"):
        for h, v in json.load(open(os.path.join(HERE, "long", f))).items():
            rows.append((names[h], v["oos"]["mean_net_pct"], v["BESTANDEN"]))
    for h, v in json.load(open(os.path.join(HERE, "listing", "oos_result.json"))).items():
        lab = "L1 Listing: Long hinterher" if h == "L1" else "L2 Listing: Short T+30 min, 72 h"
        rows.append((lab, v["oos"]["mean_net_pct"], v["BESTANDEN"]))
    rows.sort(key=lambda r: r[1])
    fig, ax = plt.subplots(figsize=(10, 6.4))
    y = np.arange(len(rows))
    cols = [AQUA if ok else BLUE for _, _, ok in rows]
    ax.barh(y, [r[1] for r in rows], color=cols, height=0.62)
    ax.set_yticks(y, [r[0] for r in rows])
    ax.axvline(0, color=INK2, linewidth=1)
    ax.set_xscale("symlog", linthresh=1)
    ax.set_xticks([-1, 0, 1, 10], ["−1 %", "0", "+1 %", "+10 %"])
    ax.set_xlabel("OOS netto pro Trade nach Kosten (gestauchte Skala über ±1 %)")
    for yi, (lab, v, ok) in zip(y, rows):
        ax.text(v + (0.15 if v >= 0 else -0.15), yi, f"{v:+.2f} %" + ("  ✔ bestanden" if ok else ""),
                va="center", ha="left" if v >= 0 else "right", fontsize=9, color=INK)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(-1.5, 60)
    ax.set_title("13 präregistrierte Ideen, OOS geprüft – eine besteht")
    ax.text(0.99, 0.02, "blau = nicht bestanden · türkis = bestanden", transform=ax.transAxes,
            ha="right", fontsize=9, color=INK2)
    save(fig, "research_overview.png")


# ---------------------------------------------------------------- 3. Waechter-Ablauf
def fig_flow():
    fig, ax = plt.subplots(figsize=(13, 3.6))
    ax.set_xlim(0, 13)
    ax.set_ylim(0, 3.6)
    ax.axis("off")
    steps = [("Upbit pollen", "alle 10 s\nneue KRW-Listings", BLUE),
             ("30 min warten", "Sprung & Hype\nabklingen lassen", "#6b8fc7"),
             ("Papier-Short", "Bitget-Perp zum Bid\n20 % Margin · 3x", ORANGE),
             ("Überwachen", "1m-Hochs alle 30 s\nStop bei +15 %", "#d08a2c"),
             ("Schließen", "nach 72 h zum Ask\n+ echtes Funding", AQUA)]
    w, h, gap = 2.1, 1.7, 0.42
    for i, (title, sub, col) in enumerate(steps):
        x = 0.2 + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, 1.2), w, h, boxstyle="round,pad=0.02,rounding_size=0.18",
                                    facecolor=col, edgecolor="none"))
        ax.text(x + w / 2, 1.2 + h * 0.68, title, ha="center", va="center", color="white",
                fontsize=12.5, fontweight="bold")
        ax.text(x + w / 2, 1.2 + h * 0.3, sub, ha="center", va="center", color="white", fontsize=9.5)
        if i < len(steps) - 1:
            ax.add_patch(FancyArrowPatch((x + w + 0.04, 1.2 + h / 2), (x + w + gap - 0.04, 1.2 + h / 2),
                                         arrowstyle="-|>", mutation_scale=16, color=INK2, linewidth=1.5))
    ax.text(6.5, 0.6, "Jeder Schritt → Telegram · alles nur auf Papier (keine echten Orders) · "
            "Stop-/Renditerechnung = dieselbe Funktion wie im Backtest (rules.py)",
            ha="center", fontsize=10, color=INK2)
    ax.text(6.5, 3.35, "Der Dry-Run-Wächter", ha="center", fontsize=14, fontweight="bold")
    save(fig, "watcher_flow.png")


# ---------------------------------------------------------------- 4. Papier-Konto aus dem Backtest
def fig_equity():
    import backtest_listing as bl
    from onembot.listing import rules
    d = pd.read_csv(os.path.join(HERE, "listing", "diag_l2.csv")).dropna(subset=["net"])
    d["T"] = pd.to_datetime(d["T"], utc=True)
    d = d.sort_values("T").reset_index(drop=True)
    ev = bl.load_events().set_index("tag")
    rets = []
    for r in d.itertuples():
        k = pd.read_parquet(os.path.join(bl.HERE, "ev", r.tag + "_k1m.parquet"))
        t0 = ev.loc[r.tag, "t0"]
        m0 = (t0 // 60_000) * 60_000
        te, tx = m0 + 30 * 60_000, m0 + 4350 * 60_000
        w = k[(k.open_time >= te) & (k.open_time < tx)].sort_values("open_time")
        x = float(k.loc[k.open_time == tx, "open"].iloc[0])
        rets.append(rules.short_return_on_margin(float(w.open.iloc[0]), w.high.values, x, r.funding_short_pnl)[0])
    rets = np.array(rets)
    eq = 100 * np.cumprod(1 + 0.2 * rets)
    fig, (a, b) = plt.subplots(1, 2, figsize=(13, 4.6), gridspec_kw={"width_ratios": [1.6, 1]})
    split = int((d["T"] < bl.SPLIT).sum())
    a.axvspan(split - 0.5, len(eq) - 0.5, color=AQUA, alpha=0.08, linewidth=0)
    a.plot(np.arange(len(eq)), eq, color=BLUE, linewidth=2.2)
    a.axhline(100, color=MUTED, linewidth=1)
    a.text(split / 2, eq.max() * 0.97, "IS (Auswahl)", ha="center", color=INK2)
    a.text(split + (len(eq) - split) / 2, eq.max() * 0.97, "OOS (Nachweis)", ha="center", color=INK2)
    a.set_yscale("log")
    a.set_xlabel("Ereignis Nr. (chronologisch 2021–2026)")
    a.set_ylabel("Papier-Konto (USDT, log)")
    a.set_title(f"100 USDT → {eq[-1]:,.0f} USDT im Backtest (vereinfacht)".replace(",", "."))
    bins = np.linspace(-100, 150, 26)
    ci, _ = np.histogram(rets[:split] * 100, bins=bins)
    co, _ = np.histogram(rets[split:] * 100, bins=bins)
    mid, bw = (bins[:-1] + bins[1:]) / 2, bins[1] - bins[0]
    b.bar(mid - bw * 0.22, ci, width=bw * 0.42, color=BLUE, label="IS")
    b.bar(mid + bw * 0.22, co, width=bw * 0.42, color=ORANGE, label="OOS")
    b.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(integer=True))
    b.axvline(0, color=INK2, linewidth=1)
    b.set_xlabel("Rendite je Trade auf die Margin (%)")
    b.set_ylabel("Anzahl Ereignisse")
    b.set_title("Verteilung: viele Gewinne, harte Stops")
    b.legend(frameon=False)
    a.text(0.01, -0.22, "Annahmen: 3x, Stop +15 % (+1 % Slippage), 0,26 % Kosten/Seite, echtes Binance-Funding, "
           "20 % des Kontos je Ereignis, Überlappungen vernachlässigt – kein Versprechen für live.",
           transform=a.transAxes, fontsize=8.5, color=INK2)
    save(fig, "listing_backtest_equity.png")


if __name__ == "__main__":
    fig_mechanism()
    fig_research()
    fig_flow()
    fig_equity()
