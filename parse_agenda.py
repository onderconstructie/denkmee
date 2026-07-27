"""
parse_agenda.py — zet een agenda-PDF van de gemeenteraad of de raad voor
maatschappelijk welzijn om naar de punten van de KOMENDE zitting.

Verschil met parse_besluiten.py: een agenda heeft nog geen uitkomst. Er is dus
geen resultaat-regel; het hele blok ná de domein-kop is de titel/omschrijving
(inclusief sub-puntjes "1)/2)"). Daarnaast herkent dit de mondelinge Vragen
(V1, V2, …) en de toegevoegde punten (TP01, …). De uitnodigingsbrief vooraan
wordt overgeslagen door pas bij "Openbare zitting" te beginnen.

Wat het per punt geeft:
  - nummer    (1, 2, … / 13A / TP01 / V1)
  - categorie (domein in HOOFDLETTERS: MOBILITEIT, CULTUUR, … ; leeg bij vraag/TP)
  - titel     (de volledige omschrijving, sub-puntjes inbegrepen)
  - type      (gewoon / toegevoegd / vraag)
  - indiener  (enkel bij vraag of toegevoegd punt, bv. 'K. Lauwers')
  - zitting   ('openbaar' of 'besloten')

Draai:  python parse_agenda.py pad/naar/agenda.pdf
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

ITEM = re.compile(r'^(\d+[A-Za-z]?|TP\d+|V\d+)\.\s+(.*)$')    # "1. " / "13A. " / "TP01. " / "V1. "
KOP = re.compile(r'^([^.]+?)\.\s+(.*)$')                      # "DOMEIN. titel..."
NOISE = re.compile(
    r'^(Agenda(?:\s+volledig)?\s+\d+$'          # paginakop "Agenda 2" / "Agenda volledig 2"
    r'|AGENDA(?:\s+VOLLEDIG)?$'                  # titelkop "AGENDA" / "AGENDA VOLLEDIG"
    r'|STAD MECHELEN|OCMW MECHELEN'
    r'|\d{1,2} \w+ \d{4}$)'                      # losse datumregel "26 mei 2026"
)


def _is_echt_punt(nummer: str) -> bool:
    if nummer[0] in ("T", "V"):                 # TP.. of V.. zijn altijd echt
        return True
    return int(re.match(r'\d+', nummer).group(0)) < 1000   # geen afgebroken jaartal


def extract_text(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def parse(pdf_path):
    text = extract_text(pdf_path)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # begin pas bij de eerste echte sectie (slaat de uitnodigingsbrief over)
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
            items.append({**meta, "titel": " ".join(blok).strip()})
        blok, meta = [], None

    for l in lines:
        low = l.lower()
        if low.startswith("openbare zitting"):
            sluit(); zitting = "openbaar"; continue
        if low.startswith("besloten zitting"):
            sluit(); zitting = "besloten"; continue
        if low == "vragen":                     # sectiekop, geen punt
            sluit(); continue

        m = ITEM.match(l)
        if m and _is_echt_punt(m.group(1)):
            sluit()
            nummer, rest = m.groups()
            categorie, titel_start, indiener = "", rest, None
            k = KOP.match(rest)
            if k:
                categorie, titel_start = k.group(1).strip(), k.group(2).strip()
            if nummer.startswith("V"):
                soort, categorie = "vraag", ""
            elif nummer.startswith("TP") or categorie.upper() == "TOEGEVOEGD PUNT":
                soort, categorie = "toegevoegd", ""
            else:
                soort = "gewoon"
            if soort in ("vraag", "toegevoegd") and " - " in titel_start:   # "Naam - onderwerp"
                indiener, titel_start = [x.strip() for x in titel_start.split(" - ", 1)]
            meta = {"nummer": nummer, "categorie": categorie, "zitting": zitting,
                    "type": soort, "indiener": indiener}
            blok = [titel_start]
        elif meta is not None:
            blok.append(l)
    sluit()
    return items


def main():
    if len(sys.argv) < 2:
        print("Gebruik: python parse_agenda.py pad/naar/agenda.pdf"); return
    items = parse(sys.argv[1])
    out = Path.cwd() / (Path(sys.argv[1]).stem + ".agenda.json")
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    gewoon = sum(1 for i in items if i["type"] == "gewoon")
    tp = sum(1 for i in items if i["type"] == "toegevoegd")
    vr = sum(1 for i in items if i["type"] == "vraag")
    print(f"{len(items)} agendapunten ({gewoon} gewoon, {tp} toegevoegd, {vr} vragen) → {out.name}\n")
    for i in items:
        ind = f"  ({i['indiener']})" if i["indiener"] else ""
        print(f"{i['nummer']:>5}  {(i['categorie'] or i['type'])[:24]:<24} {i['titel'][:54]}{ind}")


if __name__ == "__main__":
    main()
