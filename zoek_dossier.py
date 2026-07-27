"""
zoek_dossier.py

Vormt een dossier door op een trefwoord te zoeken in de VOLLEDIGE TEKST van de
gepubliceerde bestuursdocumenten, over alle vier de bestuursorganen heen en over
alle zittingen sinds de start van de huidige legislatuur.

Idee:
  De publieke zoekpagina van de stad heeft een optie "Zoeken in document". Die
  zet de parameter isDocumentSearch op true, waardoor niet alleen titels maar de
  hele documenttekst doorzocht wordt. Eenzelfde trefwoord vindt zo de agenda, de
  aanvullende agenda, de besluitenlijst en de notulen die over dat onderwerp gaan.
  Samen vormen die stukken een dossier.

Belangrijk:
  De technische bron-URL is interne pipeline-informatie. Die mag NOOIT in de
  gepubliceerde site terechtkomen. Dit script draait dus lokaal en schrijft naar
  een intern bestand. De koppeling naar de site gebeurt later, waarbij de
  bron-URL net als elders wordt gestript.

Vereisten: requests en lxml (pip install requests lxml).
Gebruik:    python zoek_dossier.py "fietspad N15"
            python zoek_dossier.py "Prolocus" --vanaf 2024-12-01 --tot 2026-07-08
            python zoek_dossier.py "mobiliteit" --debug
"""

# Robuuste console-uitvoer: zet stdout/stderr op UTF-8, zodat print() met niet-ASCII
# (pijlen, vinkjes) niet crasht op een Windows-console die standaard cp1252 gebruikt.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


import argparse
import json
import sys
import time
import unicodedata
from datetime import date
from urllib.parse import urlencode

import requests
from lxml import html as lxml_html

# ----------------------------------------------------------------------------
# CONFIGURATIE
# ----------------------------------------------------------------------------

# De bron-host wordt hier los gehouden zodat hij makkelijk te beheren is en
# nergens hardgecodeerd in de site-output belandt.
BRON_HOST = "lblod.mechelen.be"
ZOEK_PAD = "/LBLODWeb/Home/SearchPublicaties"

# Nette, identificeerbare bezoeker. Pas het contactadres aan naar je eigen mail.
USER_AGENT = "DenkMeeMetMechelen-burgerexperiment (contact: [e-mailadres])"

# Lage frequentie: minstens deze pauze tussen requests.
PAUZE_SECONDEN = 1.0

# Alleen de huidige legislatuur.
START_LEGISLATUUR = "2024-12-01"

# De kolomkoppen die we op de resultatenpagina verwachten, genormaliseerd naar
# kleine letters zonder accenten. We mappen op de tekst van de kop, niet op
# interne class-namen, zodat het robuust blijft als de opmaak wijzigt.
KOLOM_HERKENNING = {
    "bestuursorgaan": "orgaan",
    "datum zitting": "datum_zitting",
    "document type": "documenttype",
    "titel": "titel",
    "documenten": "documenten",
    "publicatie datum": "publicatiedatum",
}


def normaliseer(tekst: str) -> str:
    """Kleine letters, accenten weg, spaties samengetrokken. Voor robuuste vergelijking."""
    tekst = (tekst or "").strip().lower()
    tekst = "".join(c for c in unicodedata.normalize("NFKD", tekst) if not unicodedata.combining(c))
    return " ".join(tekst.split())


def bouw_zoek_url(trefwoord: str, vanaf: str, tot: str) -> str:
    """Bouwt de zoek-URL met full-text zoeken (isDocumentSearch=true) en alle organen/types."""
    params = {
        "publicationtitle": trefwoord,   # met isDocumentSearch=true wordt dit op de hele tekst toegepast
        "isDocumentSearch": "true",      # dit is het vinkje "Zoeken in document"
        "bestuursorgaanId": "0",         # 0 = alle bestuursorganen
        "documenttypeId": "0",           # 0 = alle documenttypes
        "besluittypeId": "0",            # 0 = alle besluittypes
        "searchFrom": vanaf,
        "searchTo": tot,
    }
    return f"https://{BRON_HOST}{ZOEK_PAD}?{urlencode(params)}"


def haal_pagina(url: str, debug: bool = False) -> str:
    """Haalt de HTML op met een nette User-Agent en foutafhandeling."""
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "nl-BE,nl;q=0.9"}
    if debug:
        print(f"[debug] GET {url}", file=sys.stderr)
    antwoord = requests.get(url, headers=headers, timeout=30)
    antwoord.raise_for_status()
    return antwoord.text


def vind_resultaattabel(boom):
    """Zoekt de tabel waarvan de koppen de verwachte kolomnamen bevatten.

    Geeft (tabel_element, {kolomindex: veldnaam}) terug, of (None, None).
    """
    for tabel in boom.xpath("//table"):
        kop_cellen = tabel.xpath(".//tr[1]/th | .//tr[1]/td | .//thead//th")
        kop_teksten = [normaliseer(c.text_content()) for c in kop_cellen]
        index_naar_veld = {}
        for i, kop in enumerate(kop_teksten):
            for herken, veld in KOLOM_HERKENNING.items():
                if herken in kop:
                    index_naar_veld[i] = veld
        # We beschouwen het als de juiste tabel zodra orgaan en titel herkend zijn.
        if "orgaan" in index_naar_veld.values() and "titel" in index_naar_veld.values():
            return tabel, index_naar_veld
    return None, None


def parse_resultaten(html_tekst: str, debug: bool = False):
    """Parst de resultatenpagina tot een lijst van stukken."""
    boom = lxml_html.fromstring(html_tekst)
    tabel, index_naar_veld = vind_resultaattabel(boom)

    if tabel is None:
        if debug:
            tabellen = boom.xpath("//table")
            print(f"[debug] geen herkenbare resultaattabel gevonden. "
                  f"aantal tabellen op pagina: {len(tabellen)}", file=sys.stderr)
            print("[debug] mogelijk wordt de pagina via JavaScript opgebouwd. "
                  "Controleer de ruwe HTML.", file=sys.stderr)
        return []

    if debug:
        print(f"[debug] kolommen herkend: {index_naar_veld}", file=sys.stderr)

    stukken = []
    rijen = tabel.xpath(".//tbody/tr") or tabel.xpath(".//tr[position()>1]")
    for rij in rijen:
        cellen = rij.xpath("./td")
        if not cellen:
            continue
        item = {
            "orgaan": "", "datum_zitting": "", "documenttype": "",
            "titel": "", "publicatiedatum": "", "bron_links": [],
        }
        for i, cel in enumerate(cellen):
            veld = index_naar_veld.get(i)
            if veld is None:
                continue
            if veld == "documenten":
                for a in cel.xpath(".//a[@href]"):
                    href = a.get("href", "").strip()
                    if href:
                        item["bron_links"].append(href)
            else:
                item[veld] = " ".join(cel.text_content().split())
        # Alleen rijen met echte inhoud bewaren.
        if item["orgaan"] or item["titel"]:
            stukken.append(item)
    return stukken


def zoek_dossier(trefwoord: str, vanaf: str, tot: str, debug: bool = False) -> dict:
    """Voert de full-text zoekopdracht uit en geeft een dossier-structuur terug."""
    url = bouw_zoek_url(trefwoord, vanaf, tot)
    time.sleep(PAUZE_SECONDEN)  # vriendelijk voor de bron
    html_tekst = haal_pagina(url, debug=debug)
    stukken = parse_resultaten(html_tekst, debug=debug)

    # Groeperen per orgaan + datum zitting geeft de zittingen waarin het
    # onderwerp speelde. Dat is de natuurlijke ruggengraat van een dossier.
    zittingen = {}
    for s in stukken:
        sleutel = (s["orgaan"], s["datum_zitting"])
        zittingen.setdefault(sleutel, []).append(s)

    return {
        "trefwoord": trefwoord,
        "periode": {"vanaf": vanaf, "tot": tot},
        "gevormd_op": date.today().isoformat(),
        "aantal_stukken": len(stukken),
        "aantal_zittingen": len(zittingen),
        "stukken": stukken,
    }


def main():
    parser = argparse.ArgumentParser(description="Vormt een dossier via full-text zoeken in de bron.")
    parser.add_argument("trefwoord", help="Het onderwerp, bijvoorbeeld: \"fietspad N15\"")
    parser.add_argument("--vanaf", default=START_LEGISLATUUR, help="Begindatum (YYYY-MM-DD)")
    parser.add_argument("--tot", default=date.today().isoformat(), help="Einddatum (YYYY-MM-DD)")
    parser.add_argument("--uit", default=None, help="Pad naar het uitvoerbestand (.json)")
    parser.add_argument("--debug", action="store_true", help="Toon diagnose-informatie")
    args = parser.parse_args()

    try:
        dossier = zoek_dossier(args.trefwoord, args.vanaf, args.tot, debug=args.debug)
    except requests.RequestException as fout:
        print(f"Fout bij het ophalen van de bron: {fout}", file=sys.stderr)
        sys.exit(1)

    uit = args.uit or f"dossier-{normaliseer(args.trefwoord).replace(' ', '-')}.json"
    # Overschrijven, niet aanvullen: elke run levert een verse momentopname.
    with open(uit, "w", encoding="utf-8") as f:
        json.dump(dossier, f, ensure_ascii=False, indent=2)

    print(f"Dossier \"{args.trefwoord}\": {dossier['aantal_stukken']} stukken "
          f"in {dossier['aantal_zittingen']} zittingen. Opgeslagen in {uit}.")
    if dossier["aantal_stukken"] == 0:
        print("Let op: geen stukken gevonden. Draai met --debug om te zien of de "
              "resultatentabel herkend werd.")


if __name__ == "__main__":
    main()
