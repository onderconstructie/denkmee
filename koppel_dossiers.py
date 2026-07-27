"""
koppel_dossiers.py

Knoopt de treffers uit zoek_dossier.py vast aan de items die al in data.json staan
(de agendapunten van de gemeenteraad en de raad voor maatschappelijk welzijn, en de
college_beslissingen). Resultaat: een dossier verwijst naar je eigen items met hun
id, niet naar losse regels uit de bron.

Afgestemd op de echte data.json:
  - agendapunten:        orgaan zit in de sessie_id-prefix (gemeenteraad / rmw),
                         datum in sessie_date, titel in titel, id in id.
  - college_beslissingen: orgaan is altijd het college, datum in date, id begint
                         met "college-".

De match gebeurt op drie dingen tegelijk: hetzelfde bestuursorgaan, dezelfde
zittingsdatum, en een voldoende gelijkende titel. De bron-links uit de treffers
worden NIET overgenomen; die blijven interne pipeline-informatie.

Gebruik:
  python koppel_dossiers.py --data data.json dossier-fietspad-n15.json
  python koppel_dossiers.py --data data.json dossier-*.json --debug
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
import glob
import json
import sys
import unicodedata
from difflib import SequenceMatcher

# Volledige orgaan-namen, afgeleid uit de sessie_id-prefix. College staat los,
# want die items zitten in een eigen lijst zonder sessie_id.
ORGAAN_VAN_PREFIX = {
    "gemeenteraad": "Gemeenteraad",
    "rmw": "Raad voor Maatschappelijk Welzijn",
}
ORGAAN_COLLEGE = "College van Burgemeester en Schepenen"

# Hoe sterk de titels minstens moeten lijken (0 tot 1). Hoger is strenger.
STANDAARD_DREMPEL = 0.72

MAANDEN = {
    "jan": 1, "januari": 1, "feb": 2, "februari": 2, "mrt": 3, "maart": 3,
    "apr": 4, "april": 4, "mei": 5, "jun": 6, "juni": 6, "jul": 7, "juli": 7,
    "aug": 8, "augustus": 8, "sep": 9, "sept": 9, "september": 9,
    "okt": 10, "oktober": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def normaliseer(tekst: str) -> str:
    tekst = (tekst or "").strip().lower()
    tekst = "".join(c for c in unicodedata.normalize("NFKD", tekst) if not unicodedata.combining(c))
    return " ".join(tekst.split())


def normaliseer_titel(tekst: str) -> str:
    """Kleine letters, accenten en leestekens weg, zodat titels eerlijk vergeleken worden."""
    t = normaliseer(tekst)
    return " ".join("".join(c if c.isalnum() or c == " " else " " for c in t).split())


def normaliseer_datum(tekst: str) -> str:
    """Brengt datums terug tot JJJJ-MM-DD. Herkent ISO, dd/mm/jjjj en "24 februari 2025"."""
    t = normaliseer(tekst)
    if not t:
        return ""
    delen = t.split("-")
    if len(delen) == 3 and len(delen[0]) == 4 and delen[0].isdigit():
        j, m, d = delen
        if m.isdigit() and d.isdigit():
            return f"{int(j):04d}-{int(m):02d}-{int(d):02d}"
    for scheider in ("/", "-", "."):
        if scheider in t:
            stukken = [s for s in t.split(scheider) if s]
            if len(stukken) == 3 and all(s.isdigit() for s in stukken) and len(stukken[2]) == 4:
                d, m, j = stukken
                return f"{int(j):04d}-{int(m):02d}-{int(d):02d}"
    woorden = t.replace(",", " ").split()
    for i in range(max(0, len(woorden) - 2)):
        d, maand, j = woorden[i], woorden[i + 1], woorden[i + 2]
        if d.isdigit() and j.isdigit() and len(j) == 4 and maand in MAANDEN:
            return f"{int(j):04d}-{MAANDEN[maand]:02d}-{int(d):02d}"
    return ""


def orgaan_komt_overeen(bron_orgaan: str, item_orgaan: str) -> bool:
    """De bron schrijft bv. "Gemeenteraad Mechelen", het item "Gemeenteraad"."""
    a, b = normaliseer(bron_orgaan), normaliseer(item_orgaan)
    if not a or not b:
        return False
    return a in b or b in a


def verzamel_kandidaten(data: dict) -> list:
    """Maakt een platte lijst van koppelbare items uit de twee echte plekken in data.json."""
    kandidaten = []
    for a in data.get("agendapunten", []):
        prefix = str(a.get("sessie_id", "")).rsplit("-", 1)[0]
        kandidaten.append({
            "id": a.get("id"),
            "orgaan": ORGAAN_VAN_PREFIX.get(prefix, prefix or "Onbekend orgaan"),
            "datum": a.get("sessie_date", ""),
            "titel": a.get("titel", ""),
        })
    for c in data.get("college_beslissingen", []):
        kandidaten.append({
            "id": c.get("id"),
            "orgaan": ORGAAN_COLLEGE,
            "datum": c.get("date", ""),
            "titel": c.get("titel", ""),
        })
    return kandidaten


def beste_kandidaat(treffer: dict, kandidaten: list, drempel: float):
    """Zoekt het item dat het best bij een treffer past, of (None, score)."""
    bron_org = treffer.get("orgaan", "")
    bron_datum = normaliseer_datum(treffer.get("datum_zitting", ""))
    bron_titel = normaliseer_titel(treffer.get("titel", ""))
    beste, beste_score = None, 0.0
    for k in kandidaten:
        if not orgaan_komt_overeen(bron_org, k["orgaan"]):
            continue
        if bron_datum and normaliseer_datum(k["datum"]) != bron_datum:
            continue
        score = SequenceMatcher(None, bron_titel, normaliseer_titel(k["titel"])).ratio()
        if score > beste_score:
            beste, beste_score = k, score
    return (beste, beste_score) if beste and beste_score >= drempel else (None, beste_score)


def koppel_bestand(pad: str, kandidaten: list, drempel: float, debug=False) -> dict:
    with open(pad, encoding="utf-8") as f:
        dossier = json.load(f)

    gekoppeld, ongekoppeld = [], []
    for treffer in dossier.get("stukken", []):
        item, score = beste_kandidaat(treffer, kandidaten, drempel)
        if item is not None:
            gekoppeld.append({
                "item_id": item["id"],
                "orgaan": item["orgaan"],
                "datum": item["datum"],
                "titel": item["titel"],
                "match_score": round(score, 3),
            })
        else:
            ongekoppeld.append({
                "orgaan": treffer.get("orgaan", ""),
                "datum_zitting": treffer.get("datum_zitting", ""),
                "titel": treffer.get("titel", ""),
                "beste_score": round(score, 3),
            })
        if debug:
            status = "gekoppeld" if item is not None else "GEEN match"
            print(f"[debug] {status} (score {score:.2f}): {treffer.get('titel','')[:70]}", file=sys.stderr)

    return {
        "trefwoord": dossier.get("trefwoord", ""),
        "periode": dossier.get("periode", {}),
        "aantal_gekoppeld": len(gekoppeld),
        "aantal_ongekoppeld": len(ongekoppeld),
        "items": gekoppeld,
        "ongekoppeld": ongekoppeld,
    }


def main():
    parser = argparse.ArgumentParser(description="Koppelt dossier-treffers aan items in data.json.")
    parser.add_argument("dossiers", nargs="+", help="Een of meer dossier-*.json bestanden (jokertekens mogen)")
    parser.add_argument("--data", default="data.json", help="Pad naar data.json")
    parser.add_argument("--uit", default="dossiers-gekoppeld.json", help="Uitvoerbestand")
    parser.add_argument("--drempel", type=float, default=STANDAARD_DREMPEL, help="Minimale titel-gelijkenis (0-1)")
    parser.add_argument("--debug", action="store_true", help="Toon per treffer of er gekoppeld werd")
    args = parser.parse_args()

    with open(args.data, encoding="utf-8") as f:
        data = json.load(f)
    kandidaten = verzamel_kandidaten(data)
    if args.debug:
        print(f"[debug] {len(kandidaten)} koppelbare items uit data.json "
              f"(agendapunten + college_beslissingen)", file=sys.stderr)
    if not kandidaten:
        print("Geen koppelbare items in data.json gevonden.", file=sys.stderr)
        sys.exit(1)

    paden = []
    for patroon in args.dossiers:
        paden.extend(sorted(glob.glob(patroon)) or [patroon])

    resultaat = [koppel_bestand(p, kandidaten, args.drempel, debug=args.debug) for p in paden]

    with open(args.uit, "w", encoding="utf-8") as f:
        json.dump(resultaat, f, ensure_ascii=False, indent=2)

    for d in resultaat:
        print(f"Dossier \"{d['trefwoord']}\": {d['aantal_gekoppeld']} gekoppeld, "
              f"{d['aantal_ongekoppeld']} niet gekoppeld.")
    print(f"Opgeslagen in {args.uit}.")


if __name__ == "__main__":
    main()
