"""
parse_notulen.py — STAP 10b: zet een notulen-PDF om naar de RIJKE laag per zitting.

De besluitenlijst is het skelet (één regel per beslissing). De notulen voegen toe:
  - de aanwezigheidslijst (wie aanwezig / tijdelijk afwezig);
  - per punt het STEMGEDRAG (eenparig, of voor/tegen/onthoudingen);
  - de VOLLEDIGE tekst per punt (input voor de AI-samenvatting in stap 11);
  - het actualiteitsdebat (ACT), toegevoegde punten (TP) en mondelinge vragen (V).

Koppeling met de besluitenlijst: via het puntnummer (1, 2, … / TP01 / ACT1 / V1).

Belangrijk: gebruikt pdfplumber (pypdf geeft vervormde tekst op deze PDF's).
Een echt genummerd agendapunt wordt herkend aan een categorie in HOOFDLETTERS;
zo vallen opsommingen binnen de tekst ("1. Efficiënter gebruik…") er vanzelf buiten.

Draai:  python parse_notulen.py pad/naar/notulen.pdf
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

NUM = re.compile(r'^(\d+)\.\s+([A-ZÀ-Ÿ][A-ZÀ-Ÿ0-9 /\-]{2,40})\.\s+(.*)$')   # 6. FINANCIËN-BELASTINGEN. ...
SPEC = re.compile(r'^(TP\d+|ACT\d+|V\d+)\.\s+([^.]+?)\.\s+(.*)$')            # TP01. TOEGEVOEGD PUNT. Naam - ...
NOISE = re.compile(r'^(Notulen gemeenteraad|STAD MECHELEN|Gemeenteraad . Notulen|Vergadering van |NAMENS DE)')

TYPE = {"ACT": "actualiteitsdebat", "TP": "toegevoegd", "V": "vraag"}
ROLLEN = ["plaatsvervangend voorzitter voor 10", "algemeen directeur",
          "gemeenteraadsleden", "schepenen", "burgemeester", "voorzitter", "afwezig voor 10"]


def extract_text(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def marker(line):
    m = NUM.match(line)
    if m:
        return m.group(1), m.group(2).strip(), m.group(3).strip(), "gewoon"
    m = SPEC.match(line)
    if m:
        nr = m.group(1)
        soort = TYPE[re.match(r'[A-Z]+', nr).group(0)]
        return nr, m.group(2).strip(), m.group(3).strip(), soort
    return None


def parse_stemming(blok):
    low = blok.lower()
    if "eenparigheid van stemmen" in low:
        return {"modus": "eenparig", "voor": None, "tegen": None, "onthoudingen": None}
    v = re.search(r'(\d+)\s+stemmen voor', low)
    te = re.search(r'(\d+)\s+stemmen tegen', low)
    o = re.search(r'(\d+)\s+onthoudingen', low)
    if v or te or o:
        voor = int(v.group(1)) if v else 0
        tegen = int(te.group(1)) if te else 0
        onth = int(o.group(1)) if o else 0
        # Sanity: de Mechelse gemeenteraad telt 43 leden, dus een telling boven 43 kan niet
        # kloppen (een verkeerd opgepikt cijfer, bv. een jaartal dat tegen "stemmen tegen"
        # plakte). Dan is de telling onbetrouwbaar: we markeren ze als onbekend.
        if voor + tegen + onth > 43:
            return {"modus": "onbekend"}
        return {"modus": "geteld", "voor": voor, "tegen": tegen, "onthoudingen": onth}
    if "niet ter stemming" in low:
        return {"modus": "niet-ter-stemming"}
    if "mondeling beantwoord" in low:
        return {"modus": "mondeling-beantwoord"}
    return {"modus": "geen"}


def parse_aanwezigheid(lines):
    try:
        a = next(i for i, l in enumerate(lines) if l.startswith("Aanwezig:"))
        eind = next(i for i, l in enumerate(lines) if l.lower().startswith("openbare zitting"))
    except StopIteration:
        return {"aanwezig": [], "afwezig": [], "ruw": ""}
    blok = " ".join(lines[a:eind]).replace("Aanwezig:", "")
    if "Tijdelijk afwezig:" in blok:
        aanwezig_txt, afwezig_txt = re.split(r'Tijdelijk afwezig:', blok, maxsplit=1)
    else:
        aanwezig_txt, afwezig_txt = blok, ""

    def namen(txt):
        for rol in ROLLEN:
            txt = re.sub(rol, ",", txt, flags=re.I)
        stukken = [s.strip(" ,.") for s in txt.split(",")]
        # behoud enkel echte namen (≥ 2 woorden, hoofdletters), ontdubbel
        uit = []
        for s in stukken:
            if re.match(r'^[A-ZÀ-Ÿ][\w’\-]+( [A-ZÀ-Ÿ][\w’\-\.]+)+$', s) and s not in uit:
                uit.append(s)
        return uit

    return {"aanwezig": namen(aanwezig_txt), "afwezig": namen(afwezig_txt),
            "ruw": " ".join(lines[a:eind])}


def parse(pdf_path):
    text = extract_text(pdf_path)
    lines = [l.strip() for l in text.splitlines() if l.strip() and not NOISE.match(l)]

    aanwezigheid = parse_aanwezigheid(lines)

    posities = [(i, marker(l)) for i, l in enumerate(lines)]
    punten = [(i, mk) for i, mk in posities if mk]
    grenzen = [i for i, _ in punten] + [len(lines)]

    items = []
    for k, (i, (nr, cat, rest, soort)) in enumerate(punten):
        body = "\n".join(lines[i:grenzen[k + 1]])
        indiener, titel = None, rest
        if soort != "gewoon" and " - " in rest:
            indiener, titel = [x.strip() for x in rest.split(" - ", 1)]
        items.append({
            "nummer": nr,
            "type": soort,
            "categorie": "" if soort != "gewoon" else cat,
            "titel": titel,
            "indiener": indiener,
            "stemming": parse_stemming(body),
            "tekst": body,            # volledige tekst → input voor AI-tagging (stap 11)
        })
    return {"aanwezigheid": aanwezigheid, "punten": items}


def main():
    if len(sys.argv) < 2:
        print("Gebruik: python parse_notulen.py pad/naar/notulen.pdf"); return
    res = parse(sys.argv[1])
    out = Path.cwd() / (Path(sys.argv[1]).stem + ".notulen.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    aw = res["aanwezigheid"]
    print(f"Aanwezig: {len(aw['aanwezig'])} · tijdelijk afwezig: {len(aw['afwezig'])}")
    print(f"{len(res['punten'])} punten → {out.name}\n")
    for p in res["punten"]:
        s = p["stemming"]
        stem = (s["modus"] if s["modus"] != "geteld"
                else f"voor {s['voor']} / tegen {s['tegen']} / onth {s['onthoudingen']}")
        ind = f"  ({p['indiener']})" if p["indiener"] else ""
        print(f"{p['nummer']:>5}  {(p['categorie'] or p['type'])[:20]:<20} {p['titel'][:42]:<42} → {stem}{ind}")


if __name__ == "__main__":
    main()
