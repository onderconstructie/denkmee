"""
fetch_all.py — ophaler voor de raden (gemeenteraad + raad voor maatschappelijk welzijn).

  1) leest per jaar (2024–2026) de overzichtspagina van elk orgaan;
  2) pakt per zitting de vier hoofddocumenten + hun publicatiedatum
     (agenda / aanvullende agenda / besluitenlijst / notulen) → tijdigheid;
  3) catalogiseert ook de uittreksels (titel + link), zonder ze standaard te downloaden;
  4) schrijft data/sessies_index.json;
  5) downloadt de vier hoofd-PDF's naar data/raw/ (bestaande overgeslagen).

Tijdigheidsregels (artikel 22 en artikel 287 van het Decreet Lokaal Bestuur) worden
in stap 9 (maak_data.py) toegepast; dit script verzamelt enkel de feiten.

Draai:  python fetch_all.py     (eenmalig: python -m pip install requests)
Alle vijf de organen staan in ORGANEN; een orgaan erbij = één regel daar.
"""

# Robuuste console-uitvoer: zet stdout/stderr op UTF-8, zodat print() met niet-ASCII
# (pijlen, vinkjes) niet crasht op een Windows-console die standaard cp1252 gebruikt.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


import re
import json
import time
import urllib.parse
from pathlib import Path
from datetime import date, datetime

import requests

ROOT = "https://lblod.mechelen.be"
BASE = f"{ROOT}/LBLODWeb/Home/Overzicht"
USER_AGENT = "DenkMeeMetMechelen/0.4 (burgerexperiment; contact via asgaupaust.be)"
LEGISLATUUR_START = date(2024, 12, 1)
# Loopt automatisch mee met de kalender. Stond dit hardgecodeerd, dan haalde de pijplijn
# vanaf 1 januari van het volgende jaar stilzwijgend niets nieuws meer op.
JAREN = tuple(range(2024, date.today().year + 1))
DOWNLOAD_PDFS = True
DOWNLOAD_UITTREKSELS = False

# Per orgaan: (orgaan-id, stroom-id). De volledige naam is meteen de sleutel in de data.
ORGANEN = {
    "Gemeenteraad": ("be278471a2a318edba32e7ac4294c0eafbe4c8077a34dcbb9c2e43211d4a78a6",
                     "06c2b56ed7b49d146337f6db044204f19c34c4242deb3b4e142dbf925d733eda"),
    "Raad voor maatschappelijk welzijn": ("68e8c071ddfe1957b9c7b0ccd269f6776f4863a5f5bf4fed636f0e76427200ac",
                                          "3ee5544b66963ad499afb4fe84f3de9995e6f0244f2fda120fa337e43d914f4c"),
    # College en vast bureau publiceren enkel een besluitenlijst (geen agenda/notulen).
    # Eerste hash = bestuurseenheid (stad resp. OCMW), tweede hash = de stroom per orgaan.
    "College van burgemeester en schepenen": ("be278471a2a318edba32e7ac4294c0eafbe4c8077a34dcbb9c2e43211d4a78a6",
                                              "7b0258ec55a77ef7f521548d8252c8895243b28ba2247e8658a3bc02c4c09348"),
    "Vast bureau": ("68e8c071ddfe1957b9c7b0ccd269f6776f4863a5f5bf4fed636f0e76427200ac",
                    "af18b761c4c27f366dee0446bdefdcbe54b292c26ae543c5671d6844d5df164d"),
    # De burgemeester beslist als zelfstandig orgaan (politieverordeningen, ongeschiktheid,
    # noodbevelen, …) en publiceert per beslissingsdag een besluitenlijst — zelfde stroom-
    # patroon als college/vast bureau.
    "Burgemeester": ("be278471a2a318edba32e7ac4294c0eafbe4c8077a34dcbb9c2e43211d4a78a6",
                     "61b5f831241a25e43c4acf4cdf620769f334514de906151dfa1ac01146ecd971"),
}

HREF = re.compile(r'href="([^"]*?GetPublication/?\?filename=[^"]+?\.pdf)"', re.I)
UITT = re.compile(r'^(.*)_(\d{2}-\d{2}-\d{4})_(\d+)\.pdf$')           # titel_datum_nummer.pdf
DATUM = re.compile(r'_(\d{2}-\d{2}-\d{4})_')
SPAN = re.compile(r'<span>\s*(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\s*</span>')


def doctype(filename):
    """Bepaal het documenttype aan het begin van de bestandsnaam (orgaan-onafhankelijk)."""
    prefix = filename.split("_", 1)[0].lower()
    if prefix.startswith("toegevoegd"):
        return "aanvullend"
    if prefix.startswith("agenda"):
        return "agenda"
    if prefix.startswith("besluit") or prefix.startswith("beslissing"):
        return "besluiten"
    if prefix.startswith("notulen"):
        return "notulen"
    return None


def overview_url(orgaan_id, stroom_id, jaar):
    url = f"{BASE}/{orgaan_id}/{stroom_id}"
    if jaar != date.today().year:
        url += f"/{jaar}"
    return url


def haal(url):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    time.sleep(1)
    return r.text


def naam_en_url(rel):
    filename = urllib.parse.unquote(rel.split("filename=", 1)[1])
    return filename, ROOT + rel


def parse_overview(html, organ):
    hoofd, uittreksels = [], []

    # Hoofddocumenten uit de grid-blokjes (daar staat de publicatiedatum naast).
    for block in re.findall(r'<div class="publicatie-grid">(.*?)</div>', html, re.S):
        h = HREF.search(block)
        if not h:
            continue
        filename, url = naam_en_url(h.group(1))
        if UITT.match(filename):                 # eindigt op _datum_nummer → uittreksel, niet hier
            continue
        typ = doctype(filename)
        dm = DATUM.search(filename)
        if not typ or not dm:
            continue
        dd, mm, yy = (int(x) for x in dm.group(1).split("-"))
        ts = SPAN.search(block)
        published = datetime.strptime(ts.group(1), "%d/%m/%Y %H:%M:%S").date().isoformat() if ts else None
        hoofd.append({"type": typ, "organ": organ,
                      "session": date(yy, mm, dd).isoformat(), "published": published, "url": url})

    # Uittreksels: alle pdf-links die op _datum_nummer.pdf eindigen.
    for rel in HREF.findall(html):
        filename, url = naam_en_url(rel)
        u = UITT.match(filename)
        if not u:
            continue
        titel, dstr, idn = u.groups()
        dd, mm, yy = (int(x) for x in dstr.split("-"))
        uittreksels.append({"organ": organ, "titel": titel.replace("_", " ").strip(),
                            "session": date(yy, mm, dd).isoformat(), "id": idn, "url": url})
    return hoofd, uittreksels


def main():
    base_dir = Path(__file__).parent
    data_dir = base_dir / "data"; data_dir.mkdir(exist_ok=True)

    hoofd_all, uittr_all = [], []
    for orgaan, (oid, sid) in ORGANEN.items():
        for jaar in JAREN:
            url = overview_url(oid, sid, jaar)
            print(f"→ {orgaan} {jaar}: {url}")
            try:
                h, u = parse_overview(haal(url), orgaan)
                hoofd_all += h; uittr_all += u
            except Exception as e:
                print(f"  MISLUKT: {e}")

    zittingen = {}
    def zit(organ, sessie):
        return zittingen.setdefault((organ, sessie),
            {"organ": organ, "date": sessie, "documenten": {}, "uittreksels": []})

    for d_ in hoofd_all:
        if date.fromisoformat(d_["session"]) < LEGISLATUUR_START:
            continue
        zit(d_["organ"], d_["session"])["documenten"][d_["type"]] = {"published": d_["published"], "url": d_["url"]}
    for u in uittr_all:
        if date.fromisoformat(u["session"]) < LEGISLATUUR_START:
            continue
        zit(u["organ"], u["session"])["uittreksels"].append({"titel": u["titel"], "id": u["id"], "url": u["url"]})

    geordend = sorted(zittingen.values(), key=lambda z: (z["organ"], z["date"]))

    # Veiligheidsklep. Elke mislukte pagina hierboven wordt opgevangen en gelogd, en daarna
    # schreven we de index onvoorwaardelijk weg. Bij een storing bij de bron (of een gewijzigde
    # pagina-opbouw) leverde dat een lege of halve index op, die maak_data.py vervolgens
    # destructief overnam: een site zonder zittingen en zonder bron-PDF-links, terwijl alle
    # kleppen in build.py gewoon groen bleven omdat de agendapunten uit de lokale map komen.
    # Liever hier stoppen en de vorige index laten staan.
    index_pad = data_dir / "sessies_index.json"
    vorige = []
    if index_pad.exists():
        try:
            vorige = json.loads(index_pad.read_text(encoding="utf-8"))
        except Exception:
            vorige = []
    if not geordend:
        sys.exit("[STOP] Geen enkele zitting opgehaald. De bestaande index blijft staan. "
                 "Controleer de verbinding of de opbouw van de bronpagina's en probeer opnieuw.")
    if vorige and len(geordend) < len(vorige) * 0.8:
        sys.exit(f"[STOP] Slechts {len(geordend)} zittingen opgehaald tegenover {len(vorige)} in de "
                 f"bestaande index. Dat wijst op een storing, niet op nieuws. De bestaande index "
                 f"blijft staan; draai opnieuw of verwijder de index bewust als de krimp klopt.")

    index_pad.write_text(json.dumps(geordend, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n================ ZITTINGEN ================")
    huidig = None
    for z in geordend:
        if z["organ"] != huidig:
            huidig = z["organ"]
            print(f"\n[{huidig}]")
            print(f"{'zitting':<12}{'agenda':<6}{'aanv':<6}{'besl':<6}{'not':<6}{'uittr.'}")
        dd = z["documenten"]
        v = lambda k: "✓" if k in dd else "·"
        print(f"{z['date']:<12}{v('agenda'):<6}{v('aanvullend'):<6}{v('besluiten'):<6}{v('notulen'):<6}{len(z['uittreksels'])}")
    print(f"\n{len(geordend)} zittingen → data/sessies_index.json")

    if DOWNLOAD_PDFS:
        raw = data_dir / "raw"; n = 0
        for z in geordend:
            map_ = raw / z["organ"].lower().replace(" ", "_") / z["date"]
            for typ, doc in z["documenten"].items():
                f = map_ / f"{typ}.pdf"
                if f.exists(): continue
                f.parent.mkdir(parents=True, exist_ok=True)
                try:
                    r = requests.get(doc["url"], headers={"User-Agent": USER_AGENT}, timeout=60)
                    r.raise_for_status(); f.write_bytes(r.content); n += 1
                    print(f"  ↓ {f.relative_to(base_dir)} ({len(r.content)//1024} kB)"); time.sleep(1)
                except Exception as e:
                    print(f"  ! mislukt: {doc['url']} — {e}")
            if DOWNLOAD_UITTREKSELS:
                for u in z["uittreksels"]:
                    f = map_ / "uittreksels" / f"{u['id']}.pdf"
                    if f.exists(): continue
                    f.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        r = requests.get(u["url"], headers={"User-Agent": USER_AGENT}, timeout=60)
                        r.raise_for_status(); f.write_bytes(r.content); n += 1; time.sleep(1)
                    except Exception as e:
                        print(f"  ! mislukt: {u['url']} — {e}")
        print(f"\n{n} nieuwe PDF's gedownload.")


if __name__ == "__main__":
    main()
