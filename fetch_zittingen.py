"""
fetch_zittingen.py — haalt de GEPLANDE zittingsdatums op van de stadswebsite.

Waarom apart van fetch_all.py: de publicatie-index kent een zitting pas zodra er
een document (agenda/besluitenlijst) van bestaat. De eerstvolgende zitting heeft
dat vaak nog niet, terwijl mechelen.be de datums vooruit al publiceert. Deze bron
voedt dus de "volgende zitting"-kaart met een echte datum in plaats van demo.

Bron: de pagina 'Zittingen van de gemeenteraad'. De raad voor maatschappelijk
welzijn vergadert aansluitend op dezelfde avond (de livestream bevat beide), dus
we nemen dezelfde datums voor beide organen. Klopt dat ooit niet, dan is het hier
één plek om aan te passen.

Output: data/geplande_zittingen.json
  [{"date": "2026-06-23", "type": "Gemeenteraad"},
   {"date": "2026-06-23", "type": "Raad voor maatschappelijk welzijn"}, ...]

Draai:  python fetch_zittingen.py     (eenmalig: python -m pip install requests)
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
import sys
from pathlib import Path
from datetime import date

URL = "https://www.mechelen.be/stad-en-bestuur/stadsbestuur-en-organisatie/gemeenteraad"
USER_AGENT = "DenkMeeMetMechelen/1.0 (burgerexperiment; contact via asgaupaust.be)"
OUT = Path("data") / "geplande_zittingen.json"

MAANDEN = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
           "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11, "december": 12}
DATUM = re.compile(r'(\d{1,2})\s+(' + "|".join(MAANDEN) + r')\s+(20\d{2})', re.IGNORECASE)

# Zelfde avond → beide organen.
ORGANEN = ["Gemeenteraad", "Raad voor maatschappelijk welzijn"]


def parse_zittingen(tekst: str) -> list[str]:
    """Pak enkel de datums uit de zittingenlijst, zodat losse datums elders op de
    pagina (reglementen e.d.) niet meetellen.

    De stad zet een vast anker <a id="zittingen"> vlak vóór de datumlijst (een <ul>).
    Dat ankerpunt verandert minder snel dan de zichtbare kop, die al eens hernoemd is
    ('Data gemeenteraad', vroeger 'Zittingen van de gemeenteraad'). We mikken er dus
    eerst op en lezen tot het einde van die lijst (</ul>). Valt het anker ooit weg,
    dan vallen we terug op de bekende koppen (lijst eindigt dan bij 'Wie zetelt')."""
    low = tekst.lower()
    anker = re.search(r'id=["\']zittingen["\']', low)
    if anker:
        i = anker.start()
        eind = low.find("</ul>", i)
        sectie = tekst[i: eind if eind != -1 else i + 1200]
    else:
        KOPPEN = ("data gemeenteraad", "zittingen van de gemeenteraad", "data van de zittingen")
        i = next((low.find(k) for k in KOPPEN if low.find(k) != -1), -1)
        if i == -1:
            return []
        j = low.find("wie zetelt", i)
        sectie = tekst[i: j if j != -1 else i + 4000]
    iso = []
    for d_, m_, y_ in DATUM.findall(sectie):
        iso.append(date(int(y_), MAANDEN[m_.lower()], int(d_)).isoformat())
    return sorted(set(iso))


def main():
    import requests
    try:
        r = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
    except Exception as e:
        sys.exit(f"[FOUT] kon de zittingenpagina niet ophalen: {e}")

    datums = parse_zittingen(r.text)
    if not datums:
        sys.exit("[FOUT] geen zittingsdatums gevonden — de pagina-opbouw is mogelijk gewijzigd.")

    geplande = [{"date": d, "type": org} for d in datums for org in ORGANEN]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(geplande, ensure_ascii=False, indent=2), encoding="utf-8")

    vandaag = date.today().isoformat()
    komende = [d for d in datums if d >= vandaag]
    print(f"{len(datums)} zittingsdatums gevonden ({len(komende)} nog te komen) → {OUT}")
    for d in datums:
        print(f"   {'→' if d >= vandaag else ' '} {d}")


if __name__ == "__main__":
    main()
