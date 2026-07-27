"""
fetch_uittreksels.py — haalt de individuele uittreksels (en zeldzame andere bijlagen) per
zitting op van de lblod-server. Dit zijn de OFFICIËLE besluit-per-stuk documenten, de rijkste
bron: waar de besluitenlijst enkel een titel geeft (vooral bij het college), staat hier de
volledige motivering + het besluit + de artikelen.

Waarom een apart script (niet in fetch_all.py): fetch_all.py leidt titel/telling af uit de
BESTANDSNAAM ("Uittreksels_datum_id.pdf", generiek). De echte titel + het documenttype staan
in de LINK-TEKST ("Uittreksel - <besluit>"). Dit script leest daarom de link-teksten.

De PDF's gaan naar data/raw/<orgaan>/<zitting>/uittreksels/ (git-genegeerd). De geëxtraheerde
VOLLEDIGE tekst hoort NOOIT in git: die komt in data/uittreksel_cache/ (git-genegeerd) en voedt
enkel de tagging + zoekindex. Alleen afgeleide data (samenvatting, kernbegrippen) wordt gecommit.

Draai:  python fetch_uittreksels.py            (DRY-RUN: enkel catalogiseren + tonen)
        python fetch_uittreksels.py --download  (echt downloaden)
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

import re, json, time, html as _html, urllib.parse
from pathlib import Path
from datetime import date
from collections import defaultdict, Counter
import requests

BASE_DIR = Path(__file__).parent
ROOT = "https://lblod.mechelen.be"
BASE = f"{ROOT}/LBLODWeb/Home/Overzicht"
UA = "DenkMeeMetMechelen/0.4 (burgerexperiment; contact via asgaupaust.be)"
LEGISLATUUR_START = "2024-12-01"
# Loopt automatisch mee met de kalender. Stond dit hardgecodeerd, dan haalde de pijplijn
# vanaf 1 januari van het volgende jaar stilzwijgend niets nieuws meer op.
JAREN = tuple(range(2024, date.today().year + 1))

# (orgaan-naam → (bestuurseenheid-id, stroom-id, map-slug voor data/raw))
ORGANEN = {
    "Gemeenteraad": ("be278471a2a318edba32e7ac4294c0eafbe4c8077a34dcbb9c2e43211d4a78a6",
                     "06c2b56ed7b49d146337f6db044204f19c34c4242deb3b4e142dbf925d733eda", "gemeenteraad"),
    "Raad voor maatschappelijk welzijn": ("68e8c071ddfe1957b9c7b0ccd269f6776f4863a5f5bf4fed636f0e76427200ac",
                                          "3ee5544b66963ad499afb4fe84f3de9995e6f0244f2fda120fa337e43d914f4c", "raad_voor_maatschappelijk_welzijn"),
    "College van burgemeester en schepenen": ("be278471a2a318edba32e7ac4294c0eafbe4c8077a34dcbb9c2e43211d4a78a6",
                                              "7b0258ec55a77ef7f521548d8252c8895243b28ba2247e8658a3bc02c4c09348", "college_van_burgemeester_en_schepenen"),
    "Vast bureau": ("68e8c071ddfe1957b9c7b0ccd269f6776f4863a5f5bf4fed636f0e76427200ac",
                    "af18b761c4c27f366dee0446bdefdcbe54b292c26ae543c5671d6844d5df164d", "vast_bureau"),
    "Burgemeester": ("be278471a2a318edba32e7ac4294c0eafbe4c8077a34dcbb9c2e43211d4a78a6",
                     "61b5f831241a25e43c4acf4cdf620769f334514de906151dfa1ac01146ecd971", "burgemeester"),
}

GRID = re.compile(r'<div class="publicatie-grid">(.*?)</div>', re.S)
# Robuust: matcht een pdf-GetPublication-link ongeacht extra attributen (target, class, ...).
# De oude, strengere regex ('<a href="...">') miste net de bijlage-links (reglementen,
# belastingen, amendementen) die wél een target-attribuut dragen. Groep 1 = href, 2 = link-tekst.
A = re.compile(r'<a\s[^>]*?href="([^"]*GetPublication[^"]*?\.pdf)"[^>]*>(.*?)</a>', re.S | re.I)
SPAN = re.compile(r'<span>\s*(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})\s*</span>')
DATUM = re.compile(r'(\d{2})-(\d{2})-(\d{4})')

def haal(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status(); time.sleep(1.2); return r.text

def overview_url(oid, sid, jaar):
    u = f"{BASE}/{oid}/{sid}"
    return u if jaar == date.today().year else u + f"/{jaar}"

def klasse(linktekst, filename):
    t = linktekst.lower().strip(); f = filename.lower()
    if t.startswith("uittreksel"): return "uittreksel"
    if f.startswith("besluitenlijst") or f.startswith("beslissing"): return "hoofddoc"   # via fetch_all.py
    if f.startswith("agenda") or f.startswith("toegevoegd") or f.startswith("notulen"): return "hoofddoc"
    if t in ("agenda", "notulen", "besluitenlijst"): return "hoofddoc"
    return "bijlage"   # zeldzaam: reglement, jaarrekening, ...

def catalogiseer():
    """→ lijst van {orgaan, slug, zitting, titel, klasse, id, url}."""
    docs = []
    for orgaan, (oid, sid, slug) in ORGANEN.items():
        for jaar in JAREN:
            try: html = haal(overview_url(oid, sid, jaar))
            except Exception as e:
                print(f"  ! {orgaan} {jaar}: {e}"); continue
            for block in GRID.findall(html):
                span = SPAN.search(block)
                pub = span.group(1) if span else None
                for href, tekst in A.findall(block):
                    if "filename=" not in href: continue
                    tekst = re.sub(r"\s+", " ", _html.unescape(tekst)).strip()
                    fn = urllib.parse.unquote(href.split("filename=", 1)[1])
                    k = klasse(tekst, fn)
                    if k == "hoofddoc": continue
                    dm = DATUM.search(fn)
                    if not dm: continue
                    dd, mm, yy = dm.groups()
                    zitting = f"{yy}-{mm}-{dd}"
                    if zitting < LEGISLATUUR_START: continue
                    idn = re.search(r"_(\d+)\.pdf$", fn)
                    docs.append({"orgaan": orgaan, "slug": slug, "zitting": zitting,
                                 "titel": tekst, "klasse": k, "published": pub,
                                 "id": idn.group(1) if idn else None,
                                 "url": ROOT + href, "filename": fn})
    return docs

def main():
    download = "--download" in sys.argv
    print("== fetch_uittreksels ==", "(ECHTE DOWNLOAD)" if download else "(DRY-RUN, geen download)")
    docs = catalogiseer()

    per_org = Counter(d["orgaan"] for d in docs)
    per_kl = Counter(d["klasse"] for d in docs)
    urls = [d["url"] for d in docs]
    print(f"\n{len(docs)} documenten (vanaf {LEGISLATUUR_START}) | unieke url's: {len(set(urls))}")
    print("per klasse:", dict(per_kl))
    for org in ORGANEN:
        print(f"  {org:42} {per_org.get(org,0)}")
    bijl = [d for d in docs if d["klasse"] == "bijlage"]
    print(f"\nzeldzame 'bijlage'-documenten: {len(bijl)}")
    for d in bijl[:8]: print(f"   {d['zitting']} {d['orgaan'][:10]:10} | {d['titel'][:60]!r}")

    # Uittreksels met een AFWIJKENDE bestandsnaam (niet 'Uittreksels_...'): dat zijn de
    # 'andere begin'-documenten (reglementen, jaarrekeningen). Ze worden via de link-tekst
    # tóch als uittreksel gevangen. Toon er een paar ter controle.
    afw = [d for d in docs if not d["filename"].lower().startswith("uittreksel")]
    print(f"\nuittreksels met afwijkende bestandsnaam: {len(afw)}")
    for d in afw[:10]:
        print(f"   {d['zitting']} | link={d['titel'][:42]!r} | bestand={d['filename'][:42]!r}")

    # url-uniekheid = 1 pdf per document? waarschuw bij botsingen
    bots = [u for u, n in Counter(urls).items() if n > 1]
    print(f"\ndubbele url's (zelfde pdf meerdere keren gelinkt): {len(bots)}")

    if not download:
        print("\nDRY-RUN klaar. Niets gedownload. Draai met --download om echt op te halen.")
        return

    import hashlib
    raw = BASE_DIR / "data" / "raw"
    n = 0
    for d in docs:
        # Veilige, unieke bestandsnaam: het publicatie-id (uniek per document), met een
        # url-hash als terugval als er geen id in de bestandsnaam zat.
        stam = d["id"] or hashlib.sha1(d["url"].encode()).hexdigest()[:10]
        f = raw / d["slug"] / d["zitting"] / "uittreksels" / f"{d['klasse']}_{stam}.pdf"
        if f.exists(): continue
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            r = requests.get(d["url"], headers={"User-Agent": UA}, timeout=60)
            r.raise_for_status(); f.write_bytes(r.content); n += 1
            if n % 25 == 0: print(f"   ↓ {n} gedownload…")
            time.sleep(1.2)
        except Exception as e:
            print(f"   ! {d['url']} — {e}")
    # index (METADATA, geen tekst). Staat wél in .gitignore: hij is per run herbouwbaar.
    (BASE_DIR / "data" / "uittreksels_index.json").write_text(
        json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{n} nieuwe uittreksel-PDF's gedownload → data/raw/.../uittreksels/")
    print("index geschreven: data/uittreksels_index.json (metadata, geen tekst)")

if __name__ == "__main__":
    main()
