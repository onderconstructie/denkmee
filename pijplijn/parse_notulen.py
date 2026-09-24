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

Draai:  python pijplijn/parse_notulen.py pad/naar/notulen.pdf
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

NUM = re.compile(r'^(\d+)\.(?:\s+|(?=[A-ZÀ-Ÿ]))([A-ZÀ-Ÿ][A-ZÀ-Ÿ0-9 /()\-]{2,60})\.\s+(.*)$')   # 6. FINANCIËN-BELASTINGEN. ...
SPEC = re.compile(r'^(TP\d+|ACT\d*|HP\d+|V\d+)\.\s+([^.]+?)\.\s+(.*)$')            # TP01. TOEGEVOEGD PUNT. Naam - ...
NAAM_SCHEIDING = re.compile(r'\s[-\u2013]\s')                         # tussen vraagsteller en onderwerp
NOISE = re.compile(r'^(Notulen gemeenteraad|STAD MECHELEN|Gemeenteraad . Notulen|Vergadering van |NAMENS DE)')

TYPE = {"ACT": "actualiteitsdebat", "TP": "toegevoegd", "HP": "politieverordening", "V": "vraag"}
ROLLEN = ["plaatsvervangend voorzitter voor 10", "algemeen directeur",
          "gemeenteraadsleden", "schepenen", "burgemeester", "voorzitter", "afwezig voor 10"]


def extract_text(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def marker(line):
    # Oudere notulen (gemeenteraad januari, februari en juni 2025) zetten een opsommingsteken
    # (U+F0B7) voor de kop van een punt, en vanaf punt 10 staat er geen spatie na het nummer
    # ("10.FINANCIËN-BELASTINGEN."). Zonder deze regel las de code in die zittingen geen enkel
    # punt, en dus geen stemming. Sinds 24/09/2026 geldt dat ook voor toegevoegde punten,
    # actualiteitsdebatten en HP-punten (politieverordeningen): zolang hun kop niet herkend werd,
    # liep de tekst van het punt ervoor door tot de volgende kop en kreeg dat punt ook hun
    # stemming. Gemeten over alle 34 notulen: 14 punten erbij, geen punt weg, geen dubbele kop.
    # Een rubriek mag haakjes dragen ("GEMEENTELIJKE ADMINISTRATIEVE SANCTIES (GAS)").
    line = re.sub(r'^\uf0b7\s*(?=(?:V|TP|HP)?\d+\.|ACT\d*\.)', '', line)
    m = NUM.match(line)
    if m:
        return m.group(1), m.group(2).strip(), m.group(3).strip(), "gewoon"
    m = SPEC.match(line)
    if m:
        nr = m.group(1)
        soort = TYPE[re.match(r'[A-Z]+', nr).group(0)]
        return nr, m.group(2).strip(), m.group(3).strip(), soort
    return None


MAANDEN = "januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december"
# Een paginavoet ("16 december 2025") kan midden in een stemformule vallen.
_VOETREGEL = re.compile(r"^\s*\d{1,2}\s+(?:%s)\s+\d{4}\s*$" % MAANDEN, re.I | re.M)
# Een telling ("23 stemmen voor", ook enkelvoud en over een regeleinde heen) of een eenparige stemming.
_STEMDEEL = re.compile(r"(\d+)\s+(stemmen\s+voor|stem\s+voor|stemmen\s+tegen|stem\s+tegen|onthoudingen|onthouding)\b"
                       r"|eenparigheid\s+van\s+stemmen")
# Wat er tussen twee tellingen van dezelfde stemming mag staan: de namenlijst tussen haakjes,
# een komma of puntkomma, en eventueel "en" of "met".
_TUSSEN = re.compile(r"[\s,;]*(?:\((?:[^()]|\([^()]*\)){0,3000}\))?[\s,;]*(?:en\s+)?(?:met\s+)?")
_PUNT_ZELF = re.compile(r"\bstemresultaat\s+(?:agenda)?punt\s+zelf\b")


def _stemformules(low):
    """Elke stemming in de tekst als eigen formule, in volgorde: een telling
    {"voor", "tegen", "onthoudingen"} of {"eenparig": True}. Tellingen die elkaar opvolgen met
    enkel een namenlijst, komma of "en" ertussen horen bij dezelfde stemming."""
    formules, huidige, einde = [], None, None
    for m in _STEMDEEL.finditer(low):
        if m.group(1) is None:                          # "met eenparigheid van stemmen"
            formules.append({"eenparig": True})
            huidige, einde = None, m.end()
            continue
        getal = int(m.group(1))
        if getal > 60:
            # Een jaartal of paginanummer vlak voor "stemmen ...": de telling is dan niet zeker te
            # lezen. Liever 'onbekend' dan een categorie die stil op 0 komt.
            formules.append({"onbetrouwbaar": True})
            huidige, einde = None, m.end()
            continue
        soort = "voor" if "voor" in m.group(2) else ("tegen" if "tegen" in m.group(2) else "onthoudingen")
        tussen = low[einde:m.start()] if einde is not None else None
        if huidige is not None and _TUSSEN.fullmatch(tussen) and soort not in huidige:
            huidige[soort] = getal
        else:
            huidige = {soort: getal}
            formules.append(huidige)
        einde = m.end()
    return [f if (f.get("eenparig") or f.get("onbetrouwbaar")) else
            {"voor": f.get("voor", 0), "tegen": f.get("tegen", 0), "onthoudingen": f.get("onthoudingen", 0)}
            for f in formules]


def parse_stemming(blok):
    """De stemming over dit punt, of de eerlijke vaststelling dat er meer dan een was.

    Een punt kan meerdere stemmingen dragen: eerst amendementen en dan "het agendapunt zelf",
    of aparte deelbeslissingen (agenda en mandaat van een algemene vergadering, elk artikel
    apart). Getallen uit verschillende stemmingen mogen nooit samen in een telling. Staat er
    "Stemresultaat (agenda)punt zelf", dan tellen enkel de stemmingen daarna. Eén stemming blijft
    'geteld' of 'eenparig', meerdere verschillende worden 'meerdere', met de lijst erbij."""
    low = _VOETREGEL.sub("", blok).lower()
    zelf = [m.start() for m in _PUNT_ZELF.finditer(low)]
    deel = low[zelf[-1]:] if zelf else low
    formules = _stemformules(deel)
    if zelf and not formules:                  # de markering zonder stemming erna: hele tekst
        deel = low
        formules = _stemformules(deel)
    if not formules:
        if "niet ter stemming" in deel:
            return {"modus": "niet-ter-stemming"}
        if "mondeling beantwoord" in deel:
            return {"modus": "mondeling-beantwoord"}
        return {"modus": "geen"}
    if any(f.get("onbetrouwbaar") for f in formules):
        return {"modus": "onbekend"}
    verschillend = []
    for f in formules:
        if f not in verschillend:
            verschillend.append(f)
    # Sanity: de Mechelse gemeenteraad telt 43 leden, dus een telling boven 43 kan niet kloppen
    # (een verkeerd opgepikt cijfer). Dan is de telling onbetrouwbaar: we markeren ze als onbekend.
    if any(not f.get("eenparig") and f["voor"] + f["tegen"] + f["onthoudingen"] > 43 for f in verschillend):
        return {"modus": "onbekend"}
    if len(verschillend) > 1:
        return {"modus": "meerdere", "stemmingen": verschillend}
    f = verschillend[0]
    if f.get("eenparig"):
        return {"modus": "eenparig", "voor": None, "tegen": None, "onthoudingen": None}
    return {"modus": "geteld", "voor": f["voor"], "tegen": f["tegen"], "onthoudingen": f["onthoudingen"]}


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
        if soort != "gewoon" and NAAM_SCHEIDING.search(rest):     # "Naam - onderwerp", ook met een lang streepje
            indiener, titel = [x.strip() for x in NAAM_SCHEIDING.split(rest, 1)]
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
        print("Gebruik: python pijplijn/parse_notulen.py pad/naar/notulen.pdf"); return
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
