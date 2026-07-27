"""
maak_toren.py — DE BRAND-MARK. Tekent de Sint-Romboutstoren als één compacte SVG,
codeert hem als data-URI, en zet die op de juiste plaatsen in template.html.

Waarom een script en geen met-de-hand geplakte base64
-----------------------------------------------------
Net als de straten en de zittingen is het logo "data, geen toeval": het hoort uit
één bron te komen, zodat je het kunt bijsturen zonder 16 kB base64 met de hand te
bewerken. Pas hieronder de coördinaten aan en draai opnieuw:  python maak_toren.py

Wat het tekent
--------------
De échte Sint-Romboutstoren: één brede, NOOIT-afgewerkte gotische toren met een
platte top (geen hoge spits), hoektorentjes, een groot spitsboogportaal onderaan
en twee paar smalle galmgaten/lancetvensters. Vlak silhouet in de huisstijl:
bot-witte toren, zwarte lijnen, in de pink cirkel — consistent met de hero.

Wat het aanpast
---------------
- schrijft sint-romboutstoren.svg (de leesbare bron-SVG, om in een browser te bekijken);
- schrijft toren-datauri.txt (dezelfde SVG als data-URI, voor hergebruik/inspectie);
- vervangt in template.html ALLE base64-SVG-data-URI's (de brand-marks in nav/menu/hero/
  footer/wizard EN de <link rel="icon">-favicon) door de nieuwe — alles blijft zo in sync.
Draai daarna build.py (of run_all.py) om dist/index.html te verversen.
"""
from __future__ import annotations

# Robuuste console-uitvoer: zet stdout/stderr op UTF-8, zodat print() met niet-ASCII
# (pijlen, vinkjes) niet crasht op een Windows-console die standaard cp1252 gebruikt.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import base64
import re
import sys
from pathlib import Path

BASE = Path(__file__).parent

# Huisstijl-kleuren (zelfde als in template.html).
PINK = "#FF0066"   # --pink, de cirkel
BONE = "#F5F1E8"   # --bone, de steen van de toren
INK = "#0A0A0A"    # --black, de lijnen
SW = "2.6"          # lijndikte


def build_svg() -> str:
    """De Sint-Romboutstoren als strak, afgewerkt Bauhaus-silhouet in de pink cirkel:
    enkel de toren — de brede romp met platte (onafgewerkte) kruin van kantelen, drie
    hoge vensterspleten en een spitsboogportaal aan de voet. Eén vlak silhouet; de
    spleten, de gaten tussen de kantelen en het portaal zijn uitsparingen die de cirkel
    laten doorschijnen. Gecentreerd met lucht eromheen, zodat de toren mooi in de
    cirkel ligt (niet tegen de randen)."""
    return "".join([
        '<svg viewBox="0 0 480 480" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Sint-Romboutstoren">',
        f'<circle cx="240" cy="240" r="212" fill="{PINK}"/>',
        # Iets groter/voller in de cirkel: schaal 1,15 rond het middelpunt (240,240),
        # met behoud van wat lucht eromheen.
        '<g transform="translate(-36,-36) scale(1.15)">',
        f'<g fill="{BONE}">',
        '<rect x="188" y="150" width="104" height="222"/>',     # torenromp
        # platte kruin: vier kantelen
        '<rect x="194" y="130" width="16" height="20"/>',
        '<rect x="218" y="130" width="16" height="20"/>',
        '<rect x="242" y="130" width="16" height="20"/>',
        '<rect x="266" y="130" width="16" height="20"/>',
        '</g>',
        f'<g fill="{PINK}">',
        # drie hoge vensterspleten (de middelste langer)
        '<rect x="218" y="195" width="8" height="85"/>',
        '<rect x="236" y="182" width="8" height="110"/>',
        '<rect x="254" y="195" width="8" height="85"/>',
        # spitsboogportaal aan de voet
        '<polygon points="224,372 224,338 240,318 256,338 256,372"/>',
        '</g>',
        '</g>',
        '</svg>',
    ])


def main() -> None:
    svg = build_svg()

    # sanity: moet geldige XML zijn voor we iets vervangen
    import xml.dom.minidom as minidom
    try:
        minidom.parseString(svg)
    except Exception as e:
        sys.exit(f"[FOUT] SVG is geen geldige XML: {e}")

    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    data_uri = f"data:image/svg+xml;base64,{b64}"

    (BASE / "sint-romboutstoren.svg").write_text(svg + "\n", encoding="utf-8")
    (BASE / "toren-datauri.txt").write_text(data_uri + "\n", encoding="utf-8")

    tpl_path = BASE / "template.html"
    tpl = tpl_path.read_text(encoding="utf-8")
    # Alleen base64-SVG's zijn de brand-marks; de ruis-filter gebruikt ;utf8, (geen base64).
    pat = re.compile(r"data:image/svg\+xml;base64,[A-Za-z0-9+/=]+")
    n = len(pat.findall(tpl))
    if n == 0:
        sys.exit("[FOUT] Geen base64-SVG-brand-mark gevonden in template.html.")
    tpl = pat.sub(lambda m: data_uri, tpl)
    tpl_path.write_text(tpl, encoding="utf-8")

    print(f"[OK] Sint-Romboutstoren getekend: {len(svg)} tekens SVG, {len(data_uri)} tekens data-URI.")
    print(f"     {n} brand-mark(s) in template.html vervangen. Draai nu build.py om dist te verversen.")


if __name__ == "__main__":
    main()
