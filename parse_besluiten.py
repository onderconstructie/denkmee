"""
parse_besluiten.py — STAP 10: zet een besluitenlijst-PDF om naar gestructureerde
agendapunten (één record per beslissing).

Belangrijk: gebruikt pdfplumber. pypdf geeft op deze PDF's vervormde tekst
(Identity-H lettertype-codering), pdfplumber en pdftotext niet.

Wat het uit elke beslissing haalt:
  - nummer        (1, 2, … of 'TP01' voor toegevoegde punten)
  - categorie     (het domein in hoofdletters: MOBILITEIT, FINANCIËN-BELASTINGEN, …)
  - titel         (de beslissingstekst)
  - resultaat     (Goedgekeurd / Bekrachtigd / Vastgesteld / …)
  - zitting       ('openbaar' of 'besloten')
  - aanvullend    (True voor toegevoegde punten, art. 21 Decreet Lokaal Bestuur)
  - indiener      (enkel bij toegevoegde punten, bv. 'S. Van Rompaey')

De rijkere velden (mensentaal-samenvatting, thema's, straten, bevoegde schepen)
komen in de AI-tagging-stap erna; dit script levert het geraamte.

Draai:  python parse_besluiten.py pad/naar/besluiten.pdf
(eenmalig: python -m pip install pdfplumber)
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
import sys
import json
from pathlib import Path

import pdfplumber

# Een nieuw agendapunt begint met een puntnummer. Drie vormen komen voor (nagemeten over de 312
# besluitenlijsten):
#   · gewoon           "1. " / "13A. "
#   · college-subpunt  "14 A. " (punt 14, deelbeslissing A, met een SPATIE — vandaar apart)
#   · raad-punttype    "TP01. " (toegevoegd) / "HP01. " (politieverordening) / "ACT01. " (actualiteitsdebat)
# Zonder de laatste twee smolten die deelbeslissingen mee in het vorige punt (bv. een
# onteigening die de politieverordeningen HP01/HP02 opslokte).
ITEM = re.compile(r'^(\d+[A-Za-z]?|\d+ [A-Z]|[A-Z]{2,4}\d{1,3})\.\s+(.*)$')
KOP = re.compile(r'^([^.]+?)\.\s+(.*)$')                     # "Domein. titel..." (HOOFDLETTERS of Title Case)
NOISE = re.compile(
    r'^(Beslissing(?:en|s)?lijst\s+\d+$'        # paginakop, bv. "Beslissingenlijst 2"
    r'|Beslissingslijst toezicht\b'             # oude gemeenteraad-kop
    r'|BESLISSING(?:EN|S)?LIJST$'               # titelkop "BESLISSINGENLIJST"
    r'|STAD MECHELEN|OCMW MECHELEN'             # bestuurseenheid-koppen (college / vast bureau)
    r'|TOEZICHT$'
    r'|\d{1,2} \w+ \d{4}$)'                     # losse datumregel "26 mei 2026"
)

# De scheidingszin voor toegevoegde punten (art. 21 Decreet Lokaal Bestuur). De PDF breekt
# haar over twee regels, en ze staat er in drie schrijfwijzen, geteld over 310 stukken:
#   "Volgende punten werden … bestuur"    + "aan de agenda toegevoegd:"   7x
#   "Volgend punt werd … bestuur aan"     + "de agenda toegevoegd:"       2x
#   "Volgende punt werd … bestuur aan"    + "de agenda toegevoegd:"       1x
# Alleen de eerste werd herkend. Bij de twee enkelvoudige viel de staart door naar het
# volgende blok, waar sluit() de laatste regel blind tot resultaat maakt. Zo stond bij
# drie beslissingen "de agenda toegevoegd:" als uitkomst op de site.
SCHEIDING = re.compile(r'^volgende?\s+punt(en)?\s+werd(en)?\b')
# De staart, verankerd op begin én einde: een beslissingstitel die toevallig over de
# agenda gaat, mag hier niet in lopen.
SCHEIDING_STAART = re.compile(r'^(aan\s+)?de agenda toegevoegd\s*:?\s*$')

# Een echt puntnummer is een ordinaal (1, 2, … 13A, 14 A, TP01, HP01, ACT01); een afgebroken
# jaartal ("2027.") is dat niet, en een titelfragment met een letter-code ("...GAS2 en GAS3.
# Verwijzing...") evenmin.
def _is_echt_punt(nummer: str, rest: str) -> bool:
    cijfers = re.match(r'\d+', nummer)
    if cijfers and int(cijfers.group(0)) >= 1000:           # vier cijfers of meer = afgebroken jaartal
        return False
    # Een letter-prefix (TP/HP/ACT/…) telt alleen als er een categorie in HOOFDLETTERS achter staat
    # (TOEGEVOEGD PUNT, POLITIEVERORDENINGEN, ACTUALITEITSDEBAT). Zo splitst een titelfragment als
    # "…protocol GAS2 en GAS3. Verwijzing naar de gemeenteraad…" niet per ongeluk in een vals punt.
    if re.match(r'^[A-Z]{2,4}\d', nummer):
        return bool(re.match(r'[A-Z]{2,}', rest))
    return True


def extract_text(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def parse(pdf_path):
    text = extract_text(pdf_path)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # negeer alles tot de eerste echte sectie
    try:
        start = next(i for i, l in enumerate(lines) if l.lower().startswith("openbare zitting"))
    except StopIteration:
        start = 0
    lines = [l for l in lines[start:] if not NOISE.match(l)]

    items, blok, meta = [], [], None
    zitting = "openbaar"

    def sluit():
        nonlocal blok, meta
        if meta and blok:
            *titelregels, resultaat = blok if len(blok) > 1 else blok + [""]
            titel = " ".join(titelregels).strip()
            items.append({**meta, "titel": titel, "resultaat": resultaat.strip()})
        blok, meta = [], None

    for l in lines:
        low = l.lower()
        if low.startswith("openbare zitting"):
            sluit(); zitting = "openbaar"; continue
        if low.startswith("besloten zitting"):
            sluit(); zitting = "besloten"; continue
        # Scheidingszin voor toegevoegde punten: beide regels ervan sluiten het blok en
        # horen zelf nergens bij. Zie SCHEIDING hierboven voor waarom het er twee zijn.
        if SCHEIDING.match(low) or SCHEIDING_STAART.match(low):
            sluit(); continue

        m = ITEM.match(l)
        if m and _is_echt_punt(m.group(1), m.group(2)):     # nieuw punt begint (geen jaartal/fragment)
            sluit()
            nummer, rest = m.groups()
            nummer = re.sub(r'^(\d+) ([A-Z])$', r'\1\2', nummer)   # '14 A' -> '14A': schone, spatieloze id
            categorie, titel_start, indiener = "", rest, None
            k = KOP.match(rest)
            if k:
                categorie, titel_start = k.group(1).strip(), k.group(2).strip()
            # 'toegevoegd' wordt afgeleid uit het punt zelf, niet uit de tussenzin
            aanvullend = nummer.startswith("TP") or categorie.upper() == "TOEGEVOEGD PUNT"
            if aanvullend:
                categorie = ""                              # 'toegevoegd punt' is geen domein
                if " - " in titel_start:                    # "Naam - onderwerp"
                    indiener, titel_start = [x.strip() for x in titel_start.split(" - ", 1)]
            meta = {"nummer": nummer, "categorie": categorie, "zitting": zitting,
                    "aanvullend": aanvullend, "indiener": indiener,
                    "type": "toegevoegd" if aanvullend else "gewoon"}
            blok = [titel_start]
        elif meta is not None:
            blok.append(l)
    sluit()
    return items


def main():
    if len(sys.argv) < 2:
        print("Gebruik: python parse_besluiten.py pad/naar/besluiten.pdf"); return
    pdf_path = sys.argv[1]
    items = parse(pdf_path)

    out = Path.cwd() / (Path(pdf_path).stem + ".agendapunten.json")
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    gewoon = [i for i in items if not i["aanvullend"]]
    toegevoegd = [i for i in items if i["aanvullend"]]
    print(f"{len(items)} beslissingen ({len(gewoon)} gewoon, {len(toegevoegd)} toegevoegd) → {out.name}\n")
    for i in items:
        extra = f"  [indiener: {i['indiener']}]" if i["indiener"] else ""
        print(f"{i['nummer']:>4}. [{i['zitting'][:4]}] {i['categorie'][:22]:<22} {i['titel'][:60]} → {i['resultaat']}{extra}")


if __name__ == "__main__":
    main()
