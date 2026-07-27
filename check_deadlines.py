# -*- coding: utf-8 -*-
"""check_deadlines.py — de tijdigheidsberekening zelf natrekken, met de bron erbij.

Dit script rekent NIETS opnieuw uit op basis van data.json. Het vertrekt van de ruwe index
(data/sessies_index.json, wat er letterlijk op het publicatieplatform van de stad stond) en past
de termijnregels opnieuw toe. Zo controleer je niet of data.json met zichzelf klopt, maar of de
regel correct op de echte publicatiedatums is toegepast. Daarna vergelijkt het script zijn eigen
uitkomst met wat er in data.json staat en meldt elk verschil.

De regels (Decreet Lokaal Bestuur):
  agenda          uiterlijk  8 dagen VOOR de zitting   (artikel 22)
  besluitenlijst  uiterlijk 10 dagen NA  de zitting    (artikel 287)
  notulen         geen termijn-oordeel: de raad keurt ze pas goed op de volgende zitting,
                  dus een publicatie daarna is het normale verloop en geen tekortkoming

Gebruik:
  python check_deadlines.py                    alles, samengevat per orgaan
  python check_deadlines.py --telaat           alleen wat te laat was (het interessantste)
  python check_deadlines.py --orgaan raad      enkel organen waarvan de naam 'raad' bevat
  python check_deadlines.py --steekproef 8     8 willekeurige zittingen, gespreid over de organen
  python check_deadlines.py --urls             de bron-URL per regel, om zelf te gaan kijken

Elke regel is met de hand te controleren: open de URL, zoek de publicatiedatum op de pagina van
de stad, en vergelijk met de kolommen hieronder.
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent
INDEX = BASE / "data" / "sessies_index.json"
DATA = BASE / "data.json"

# Welk document hoort bij welk orgaan, met welke termijn. Dit spiegelt maak_data.py; wijkt het
# daarvan af, dan is dat precies het soort verschil dat je wil zien.
RADEN = ("Gemeenteraad", "Raad voor maatschappelijk welzijn")


def d(s):
    return date.fromisoformat(s) if s else None


def beoordeel(organ, soort, zitting, gepubliceerd):
    """-> (deadline, marge_in_dagen, status). Marge positief = op tijd, negatief = te laat."""
    if soort == "agenda":
        deadline = zitting - timedelta(days=8)
        marge = (deadline - gepubliceerd).days
        return deadline, marge, ("tijdig" if marge >= 0 else "te laat")
    if soort == "besluiten":
        deadline = zitting + timedelta(days=10)
        marge = (deadline - gepubliceerd).days
        return deadline, marge, ("tijdig" if marge >= 0 else "te laat")
    # notulen: bewust geen oordeel, zie de kop van dit bestand
    return None, None, "geen oordeel"


def main():
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--telaat", action="store_true", help="alleen de overtredingen")
    p.add_argument("--orgaan", default="", help="filter op (deel van) de orgaannaam")
    p.add_argument("--steekproef", type=int, default=0, help="N zittingen, gespreid over de organen")
    p.add_argument("--urls", action="store_true", help="bron-URL per regel tonen")
    a = p.parse_args()

    if not INDEX.exists():
        sys.exit("data/sessies_index.json niet gevonden. Draai eerst fetch_all.py.")
    index = json.loads(INDEX.read_text(encoding="utf-8"))

    rijen = []
    for zit in index:
        organ = zit.get("organ") or "?"
        if a.orgaan and a.orgaan.lower() not in organ.lower():
            continue
        zd = d(zit.get("date"))
        if not zd:
            continue
        for soort, doc in (zit.get("documenten") or {}).items():
            pub = d((doc or {}).get("published"))
            if not pub:
                continue
            deadline, marge, status = beoordeel(organ, soort, zd, pub)
            rijen.append({
                "organ": organ, "zitting": zd, "soort": soort, "pub": pub,
                "deadline": deadline, "marge": marge, "status": status,
                "url": (doc or {}).get("url") or "",
            })

    if a.telaat:
        rijen = [r for r in rijen if r["status"] == "te laat"]
    if a.steekproef:
        # gespreid: om beurten uit elk orgaan, zodat een steekproef niet één orgaan wordt
        per = {}
        for r in rijen:
            per.setdefault(r["organ"], []).append(r)
        for v in per.values():
            v.sort(key=lambda r: r["zitting"], reverse=True)
        keuze, i = [], 0
        while len(keuze) < a.steekproef and any(len(v) > i for v in per.values()):
            for v in per.values():
                if len(v) > i and len(keuze) < a.steekproef:
                    keuze.append(v[i])
            i += 1
        rijen = keuze

    rijen.sort(key=lambda r: (r["organ"], r["zitting"]), reverse=True)

    print()
    print("REGELS  agenda: uiterlijk 8 dagen VOOR de zitting (art. 22)")
    print("        besluitenlijst: uiterlijk 10 dagen NA de zitting (art. 287)")
    print("        notulen: geen oordeel (goedkeuring gebeurt op de volgende zitting)")
    print()
    kop = "%-34s %-11s %-15s %-13s %-13s %6s  %s" % (
        "ORGAAN", "ZITTING", "DOCUMENT", "GEPUBLICEERD", "DEADLINE", "MARGE", "STATUS")
    print(kop)
    print("-" * len(kop))
    for r in rijen:
        label = {"agenda": "agenda", "besluiten": "besluitenlijst", "notulen": "notulen"}.get(r["soort"], r["soort"])
        print("%-34s %-11s %-15s %-13s %-13s %6s  %s" % (
            r["organ"][:34], r["zitting"], label, r["pub"],
            r["deadline"] or "n.v.t.",
            ("%+d d" % r["marge"]) if r["marge"] is not None else "n.v.t.",
            r["status"]))
        if a.urls and r["url"]:
            print("      bron: %s" % r["url"])

    # Samenvatting per orgaan en documenttype
    print()
    print("SAMENVATTING (alleen documenten met een termijn)")
    tel = {}
    for r in rijen:
        if r["status"] == "geen oordeel":
            continue
        k = (r["organ"], r["soort"])
        t = tel.setdefault(k, [0, 0])
        t[1] += 1
        if r["status"] == "tijdig":
            t[0] += 1
    for (organ, soort), (ok, tot) in sorted(tel.items()):
        label = {"agenda": "agenda", "besluiten": "besluitenlijst"}.get(soort, soort)
        print("  %-34s %-15s %3d/%-3d = %5.1f%%" % (organ[:34], label, ok, tot, 100.0 * ok / tot))

    # Kruiscontrole tegen data.json: staat daar hetzelfde?
    if DATA.exists() and not (a.telaat or a.steekproef or a.orgaan):
        data = json.loads(DATA.read_text(encoding="utf-8"))
        verschillen = 0
        for organ, blok in (data.get("tijdigheid_per_orgaan") or {}).items():
            for z in blok.get("zittingen", []):
                zd, pub = d(z.get("date")), d(z.get("published"))
                if not (zd and pub):
                    continue
                soort = "agenda" if organ in RADEN else "besluiten"
                _, _, mijn = beoordeel(organ, soort, zd, pub)
                hunne = "tijdig" if z.get("status") == "tijdig" else "te laat"
                if mijn != hunne:
                    verschillen += 1
                    print("  [verschil] %s %s: dit script zegt '%s', data.json zegt '%s'"
                          % (organ, zd, mijn, z.get("status")))
        print()
        print("KRUISCONTROLE met data.json: %s"
              % ("geen enkel verschil" if verschillen == 0 else "%d verschil(len), zie hierboven" % verschillen))
    print()


if __name__ == "__main__":
    main()
