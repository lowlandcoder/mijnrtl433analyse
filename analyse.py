#!/usr/bin/env python3
"""Omgevingsanalyse van de RTL433-ontvangst.

Leest events.jsonl (de -F json-uitvoer van de rtl_433-container), maakt een
rapport met grafieken en een omgevingskaart, en toont dat als pagina. Een knop
"Ververs" maakt het rapport opnieuw op basis van de dan aanwezige data.

Instellingen (omgevingsvariabelen):
  DATA_FILE   pad naar events.jsonl   (standaard /data/events.jsonl)
  POORT       poort van de pagina      (standaard 8000)
"""

import base64
import collections
import io
import json
import os
import statistics
import threading
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from flask import Flask, redirect
from waitress import serve

DATA_FILE = os.environ.get("DATA_FILE", "/data/events.jsonl")
POORT = int(os.environ.get("POORT", "8000"))

BG = "#0b1f3a"; ACC = "#4dabf7"; TXT = "#f7fbff"; MUT = "#9db0c9"; GRN = "#69db7c"; GAS = "#f4a261"
plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": BG, "savefig.facecolor": BG,
    "text.color": TXT, "axes.labelcolor": MUT, "xtick.color": MUT, "ytick.color": MUT,
    "axes.edgecolor": "#28405f", "grid.color": "#1c3350", "font.size": 10,
})

_cache = {"html": None, "op": None}
_lock = threading.Lock()

TPMS_MODELLEN = {
    "Toyota", "Ford", "Hyundai-VDO", "Renault", "Citroen", "Schrader",
    "Schrader-EG53MA4", "Abarth-124Spider", "Elantra2012", "Truck",
}


def _parse(t):
    if len(t) >= 5 and t[-5] in "+-" and t[-3] != ":":
        t = t[:-2] + ":" + t[-2:]
    return datetime.fromisoformat(t)


def _naar_int(i):
    try:
        return int(i, 16)
    except (ValueError, TypeError):
        try:
            return int(i)
        except (ValueError, TypeError):
            return None


def cluster_voertuigen(rows):
    """Groepeert bandensensoren (TPMS) tot voertuigen. Een auto heeft vier
    banden die rond dezelfde tijd zenden; sensoren die telkens samen in korte
    tijdvensters opduiken (zelfde model en protocol) horen bij één auto. Auto's
    die permanent naast elkaar staan, zijn op tijd niet te scheiden; die worden
    daarna gesplitst op de opeenvolgende id-nummering.

    Geeft terug: (vaste voertuigen, geschat aantal passerende auto's)."""
    ev = [(r["_t"], r.get("model"), str(r.get("id")), r.get("protocol"))
          for r in rows if r.get("model") in TPMS_MODELLEN]
    if not ev:
        return [], 0
    t0 = min(e[0] for e in ev)
    BIN = 60
    id_bins = collections.defaultdict(set)
    idinfo = {}
    bin_ids = collections.defaultdict(set)
    id_times = collections.defaultdict(list)
    for t, m, i, p in ev:
        b = int((t - t0).total_seconds() // BIN)
        k = (m, i, p)
        id_bins[k].add(b); bin_ids[b].add(k); idinfo[k] = (m, p); id_times[k].append(t)

    cooc = collections.Counter()
    for keys in bin_ids.values():
        keys = list(keys)
        for a in range(len(keys)):
            for c in range(a + 1, len(keys)):
                if idinfo[keys[a]] == idinfo[keys[c]]:
                    cooc[frozenset((keys[a], keys[c]))] += 1

    adj = collections.defaultdict(set)
    for pair, n in cooc.items():
        k1, k2 = tuple(pair)
        score = n / min(len(id_bins[k1]), len(id_bins[k2]))
        if score >= 0.4 and n >= 3:
            adj[k1].add(k2); adj[k2].add(k1)

    seen = set(); comps = []
    for k in id_bins:
        if k in seen:
            continue
        stack = [k]; comp = []
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x); comp.append(x); stack.extend(adj[x] - seen)
        comps.append(comp)

    # Over-samengevoegde clusters (permanent samen) splitsen op id-nummering.
    voertuigen = []
    for comp in comps:
        if len(comp) > 4:
            mi = sorted((v, k) for k in comp if (v := _naar_int(k[1])) is not None)
            groep = [mi[0][1]]
            for (pv, _), (cv, k) in zip(mi, mi[1:]):
                if cv - pv > 0x10000:
                    voertuigen.append(groep); groep = [k]
                else:
                    groep.append(k)
            voertuigen.append(groep)
        else:
            voertuigen.append(comp)

    res = []
    for v in voertuigen:
        tot = sum(len(id_times[k]) for k in v)
        alle = [t for k in v for t in id_times[k]]
        span = (max(alle) - min(alle)).total_seconds() / 3600
        res.append({"model": v[0][0], "ids": sorted(k[1] for k in v),
                    "banden": len(v), "metingen": tot, "uur": span})
    vast = sorted((r for r in res if r["uur"] > 12 and r["metingen"] >= 50),
                  key=lambda r: -r["metingen"])
    passant = [r for r in res if not (r["uur"] > 12 and r["metingen"] >= 50)]
    return vast, len(passant)


def _png(fig):
    b = io.BytesIO()
    fig.savefig(b, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def _lees():
    rijen = []
    if not os.path.exists(DATA_FILE):
        return rijen
    with open(DATA_FILE, encoding="utf-8", errors="replace") as f:
        for regel in f:
            regel = regel.strip()
            if regel:
                try:
                    r = json.loads(regel)
                    r["_t"] = _parse(r["time"])
                    rijen.append(r)
                except Exception:
                    pass
    rijen.sort(key=lambda r: r["_t"])
    return rijen


def genereer():
    """Maakt het rapport-HTML uit de huidige data en zet het in de cache."""
    rows = _lees()
    op = datetime.now().astimezone()
    if not rows:
        _cache["html"] = _omhulsel("<div class='kaart'>Nog geen data in "
                                   f"<code>{DATA_FILE}</code>.</div>", op)
        _cache["op"] = op
        return

    t0, t1 = rows[0]["_t"], rows[-1]["_t"]
    span_h = max((t1 - t0).total_seconds() / 3600, 0.1)

    dev = collections.Counter((r.get("model"), str(r.get("id"))) for r in rows)
    modellen = collections.Counter(r.get("model") for r in rows)
    vaste_voertuigen, passant_autos = cluster_voertuigen(rows)

    # Grafiek 1: temperatuur eigen sensor (Nexus-TH, sterkst vertegenwoordigd)
    th = [r for r in rows if r.get("model") == "Nexus-TH" and "temperature_C" in r]
    grafieken = []
    if th:
        fig, ax = plt.subplots(figsize=(8, 2.8))
        ax.plot([r["_t"] for r in th], [r["temperature_C"] for r in th], color=ACC, lw=1.4)
        ax.set_ylabel("°C"); ax.set_title("Temperatuur — Nexus-TH (eigen sensor)", color=TXT, loc="left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %H:%M")); ax.grid(True, alpha=.3)
        grafieken.append(("Temperatuur eigen sensor", _png(fig),
                          f"Gemiddeld {statistics.mean([r['temperature_C'] for r in th]):.1f} °C."))

    # Grafiek 2: metingen per uur
    byhour = collections.Counter(r["_t"].replace(minute=0, second=0, microsecond=0) for r in rows)
    uren = sorted(byhour)
    fig, ax = plt.subplots(figsize=(8, 2.8))
    ax.bar(uren, [byhour[u] for u in uren], width=0.03, color=ACC)
    ax.set_ylabel("metingen"); ax.set_title("Activiteit per uur", color=TXT, loc="left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %H:%M")); ax.grid(True, axis="y", alpha=.3)
    grafieken.append(("Activiteit per uur", _png(fig), "De drukte volgt doorgaans de spits."))

    # Grafiek 3: passerende auto's per uur
    cnt = collections.Counter((r.get("model"), str(r.get("id"))) for r in rows if r.get("model") in TPMS_MODELLEN)
    passant = {k for k, v in cnt.items() if v < 10}
    per_uur = collections.defaultdict(set)
    for r in rows:
        k = (r.get("model"), str(r.get("id")))
        if k in passant:
            per_uur[r["_t"].replace(minute=0, second=0, microsecond=0)].add(k)
    if per_uur:
        uren2 = sorted(per_uur)
        fig, ax = plt.subplots(figsize=(8, 2.8))
        ax.bar(uren2, [len(per_uur[u]) for u in uren2], width=0.03, color=GAS)
        ax.set_ylabel("auto's"); ax.set_title("Passerende auto's per uur (unieke bandensensoren)", color=TXT, loc="left")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %H:%M")); ax.grid(True, axis="y", alpha=.3)
        grafieken.append(("Passerend verkeer", _png(fig),
                          "Unieke passerende bandensensoren per uur — een ruwe verkeersteller."))

    # Grafiek 4: meest gehoorde apparaten
    top = dev.most_common(12)[::-1]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.barh([f"{m} {i}" for (m, i), _ in top], [c for _, c in top], color=GRN)
    ax.set_title("Meest gehoorde apparaten", color=TXT, loc="left"); ax.grid(True, axis="x", alpha=.3)
    grafieken.append(("Meest gehoorde apparaten", _png(fig), ""))

    kpis = [
        (len(rows), "metingen"),
        (len(dev), "unieke apparaten"),
        (len(vaste_voertuigen), "vaste voertuigen"),
        (passant_autos, "passerende auto's"),
    ]

    inhoud = f"""
    <p class="sub">Ontvangen op de sdr-server · {t0:%d %b %H:%M} tot {t1:%d %b %H:%M} · {span_h:.0f} uur data</p>
    <div class="grid">
      {''.join(f'<div class="kpi"><b>{w}</b><span>{l}</span></div>' for w, l in kpis)}
    </div>
    <h2>Wat er in de buurt zendt</h2>
    <div class="kaart"><ul>
      <li><b>Eigen sensoren</b> — o.a. de Nexus-TH weersensor, de hele periode aanwezig met sterk signaal.</li>
      <li><b>Eigen huisautomatisering (433 MHz)</b> — schakelaars en melders dichtbij. Deze verschijnen soms onder meerdere namen tegelijk; dat is één apparaat dat door meerdere decoders wordt herkend.</li>
      <li><b>Vaste voertuigen</b> — {len(vaste_voertuigen)} auto's dichtbij, geclusterd uit hun bandensensoren; vrijwel zeker eigen of vlak-naast geparkeerd.</li>
      <li><b>Passerend verkeer</b> — naar schatting {passant_autos} voorbijrijdende auto's in deze periode.</li>
      <li><b>Overige</b> — losse afstandsbedieningen en een enkele beveiligingsmelder, plus enkele ruis-decodes.</li>
    </ul></div>
    """

    if vaste_voertuigen:
        rijen_v = "".join(
            f'<li><b>auto_{n}</b> — {v["model"]}, '
            f'{v["banden"]} {"band" if v["banden"] == 1 else "banden"}, '
            f'{v["uur"]:.0f} uur aanwezig <span class="muted">({", ".join(v["ids"])})</span></li>'
            for n, v in enumerate(vaste_voertuigen, 1)
        )
        inhoud += f"""
    <h2>Voertuigen</h2>
    <div class="kaart">
      <p>De vier banden van een auto zenden rond dezelfde tijd. Sensoren die telkens samen opduiken (zelfde model en protocol) worden tot één auto gegroepeerd; auto's die permanent naast elkaar staan, worden op hun opeenvolgende id-nummering gescheiden.</p>
      <ul>{rijen_v}</ul>
      <p class="muted">Passerend verkeer: naar schatting {passant_autos} auto's. Elke passant wordt meestal met maar één band gehoord, dus dat aantal ligt dicht bij het aantal losse sensoren. Clusteren is een benadering.</p>
    </div>
    """
    for titel, bron, bijschrift in grafieken:
        inhoud += f'<h2>{titel}</h2><div class="kaart"><img src="{bron}" alt="{titel}">'
        if bijschrift:
            inhoud += f'<p class="muted">{bijschrift}</p>'
        inhoud += "</div>"

    inhoud += """
    <h2>Aandachtspunten</h2>
    <div class="kaart"><ul>
      <li>Met één stick verzamelt RTL433 alleen data als die container draait; de analyse dekt dus alleen die periodes.</li>
      <li>Bandensensoren (TPMS) hebben een unieke, volgbare id. Aan de vaste id's is te zien wanneer een auto thuis is; met het oog op privacy is de pagina afgeschermd.</li>
      <li>Sommige modellen met heel weinig metingen en onmogelijke waarden (bijvoorbeeld temperatuur ver onder nul) zijn ruis-decodes, geen echte sensoren.</li>
    </ul></div>
    """
    _cache["html"] = _omhulsel(inhoud, op)
    _cache["op"] = op


def _omhulsel(inhoud, op):
    return f"""<!doctype html><html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#061225">
<title>Lab023 — MijnRTL433-analyse</title><style>
:root{{--bg:#061225;--kaart:rgba(15,36,67,.72);--rand:rgba(255,255,255,.10);--tekst:#f7fbff;--muted:#9db0c9;--accent:#4dabf7;--gas:#f4a261;--geld:#69db7c}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{color:var(--tekst);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:24px;
background:radial-gradient(circle at 15% 15%,rgba(44,135,205,.25),transparent 32rem),radial-gradient(circle at 90% 90%,rgba(105,74,207,.20),transparent 30rem),linear-gradient(145deg,#071a32,var(--bg))}}
.app{{width:min(920px,100%);margin:0 auto;padding:26px;border:1px solid var(--rand);border-radius:28px;background:rgba(5,18,39,.68)}}
.balk{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}}
h1{{font-size:22px}}h1 em{{color:var(--accent);font-style:normal}}
h2{{font-size:16px;margin:24px 0 8px}}.sub{{color:var(--muted);font-size:13px;margin-top:4px}}
.kaart{{padding:16px 18px;border:1px solid var(--rand);border-radius:16px;background:var(--kaart);margin-top:10px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}}
.kpi{{padding:14px;border:1px solid var(--rand);border-radius:14px;background:var(--kaart)}}
.kpi b{{display:block;font-size:22px;color:var(--accent)}}.kpi span{{color:var(--muted);font-size:12px}}
@media(max-width:640px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}
img{{width:100%;border-radius:12px;margin-top:8px}}
ul{{margin:4px 0 0 18px}}li{{margin:3px 0}}.muted{{color:var(--muted)}}
.ververs{{color:var(--tekst);background:rgba(77,171,247,.16);border:1px solid rgba(77,171,247,.45);
border-radius:10px;padding:9px 14px;font-size:13px;cursor:pointer}}
.ververs:hover{{background:rgba(77,171,247,.28)}}
.stand{{color:var(--muted);font-size:12px;text-align:right}}
.voet{{color:var(--muted);font-size:11.5px;text-align:center;margin-top:22px}}
</style></head><body><main class="app">
<div class="balk">
  <div><h1>Mijn<em>RTL433</em>-analyse</h1><p class="sub">Omgevingsanalyse van de 433 MHz-ontvangst.</p></div>
  <form method="post" action="ververs" style="text-align:right">
    <button class="ververs" type="submit">Ververs</button>
    <div class="stand">bijgewerkt: {op:%d %b %H:%M}</div>
  </form>
</div>
{inhoud}
<p class="voet">Gemaakt uit events.jsonl · rtl_433 op de sdr-server · Lab023</p>
</main></body></html>"""


app = Flask(__name__)


@app.route("/")
def index():
    if _cache["html"] is None:
        with _lock:
            if _cache["html"] is None:
                genereer()
    return _cache["html"]


@app.route("/ververs", methods=["POST"])
def ververs():
    with _lock:
        genereer()
    return redirect("./")


def main():
    print(f"Analysepagina gestart op poort {POORT}, bron {DATA_FILE}.", flush=True)
    serve(app, host="0.0.0.0", port=POORT)


if __name__ == "__main__":
    main()
