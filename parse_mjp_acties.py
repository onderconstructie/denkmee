"""
parse_mjp_acties.py — STAP 3g: leest de begrotingslijnen uit de budgetstukken en koppelt ze
aan de MJP-actiecodes die in de besluiten staan.

Het gat dat dit dicht: een besluit vermeldt "MJP004937" en verder niets. Wat dat is, welke
actie eronder hangt, welke dienst het draagt en hoeveel geld ervoor staat, weet alleen het
meerjarenplan. Dat plan bevat één tabel waarin precies dat staat: het "Overzicht van alle
beleidsdoelstellingen, actieplannen en acties met ramingen" (in oudere plannen heet dat
"Beleidsdoelstellingen, actieplannen en acties (+ ramingen)").

De pdf draagt echte tabellijnen, dus de kolommen zijn uit te lezen zonder te gokken op
tekstposities. De kolomvolgorde verschilt wél per plan (het oude plan zet er ook al
gerealiseerde rekeningcijfers naast), daarom worden de kolommen per document herkend aan
hun INHOUD (welke kolom draagt MJP-codes, welke BD/AP/AC-codes) en de jaartallen aan hun
kop. Klopt er iets niet, dan slaat het document over in plaats van een verkeerd bedrag te
publiceren: liever geen cijfer dan een fout cijfer.

Wat er WEL en NIET gepubliceerd wordt: mjp_acties.json bevat enkel de codes die in dit
corpus echt voorkomen (nu enkele honderden van de ruim twaalfduizend). De volledige tabel
blijft lokaal in de cache. Zo publiceren we geen kopie van het document, enkel de lijnen
waar een concreet besluit naar verwijst.

Nieuwste plan wint: de stukken staan op de pagina van nieuw naar oud, en die volgorde
volgen we. Een code die in het meerjarenplan 2026-2031 staat, krijgt dus dat bedrag, niet
het bedrag uit een aanpassing van het vorige plan.

Uitvoer: mjp_acties.json          (klein, wordt meegecommit)
         data/mjp_cache/*.json    (volledige tabel per document, git-genegeerd)

Draai:  python parse_mjp_acties.py                (run_all.py doet dit automatisch, stap 3g)
        python parse_mjp_acties.py --diep         (alle planversies, niet enkel de nieuwste)
        python parse_mjp_acties.py --code MJP004937   (één lijn opzoeken in de cache)
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import argparse
import hashlib
import json
import re
from pathlib import Path

BASE = Path(__file__).parent
BUDGET = BASE / "data" / "raw" / "budgetten"
INDEX = BUDGET / "_index.json"
CACHE = BASE / "data" / "mjp_cache"
UIT = BASE / "mjp_acties.json"

MJP_RX = re.compile(r"^MJP\d{4,}$")
BD_RX = re.compile(r"^BD\d+$")
AP_RX = re.compile(r"^AP\d+$")
AC_RX = re.compile(r"^AC\d+$")
JAAR_RX = re.compile(r"\b(20\d{2})\b")
# 10.000 · -1.234 · (1.234) · 4.768.384 · 1.234,50 — de tabellen gebruiken de Belgische
# schrijfwijze (punt als duizendtal, komma als decimaal).
BEDRAG_RX = re.compile(r"^-?\(?\d{1,3}(?:\.\d{3})*(?:,\d+)?\)?$|^-?\(?\d+(?:,\d+)?\)?$")

# Titels van de tabel die we nodig hebben. De ODAA-varianten zonder "ramingen" bevatten
# enkel de boomstructuur zonder bedragen en zijn hier dus nutteloos.
TITEL_RX = re.compile(r"(met ramingen|\+\s*ramingen|totaaloverzicht.*raming)", re.IGNORECASE)


def schoon(cel) -> str:
    return re.sub(r"\s+", " ", (cel or "").replace("\n", " ")).strip()


def bedrag(cel):
    """'4.768.384' -> 4768384, '(1.234)' -> -1234, '' -> None. Geeft None bij alles wat
    niet onmiskenbaar een bedrag is, zodat er nooit een half gelezen getal doorglipt."""
    t = schoon(cel)
    if not t or not BEDRAG_RX.match(t):
        return None
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.strip("-()").replace(".", "").replace(",", ".")
    try:
        w = float(t)
    except ValueError:
        return None
    return int(round(-w if neg else w))


def kolomkaart(header, rijen):
    """Welke kolom draagt wat? De codekolommen herkennen we aan hun inhoud (dat is
    plan-onafhankelijk), de jaarkolommen aan hun kop. Geeft None als de tabel niet de
    tabel is die we zoeken."""
    n = len(header)
    def telling(rx, i):
        return sum(1 for r in rijen if i < len(r) and rx.match(schoon(r[i])))

    kaart = {}
    for naam, rx in (("mjp", MJP_RX), ("bd", BD_RX), ("ap", AP_RX), ("ac", AC_RX)):
        scores = [(telling(rx, i), i) for i in range(n)]
        beste, idx = max(scores)
        # minstens de helft van de rijen moet de code dragen, anders is het toeval
        kaart[naam] = idx if beste >= max(2, len(rijen) // 2) else None
    if kaart["mjp"] is None:
        return None

    # De omschrijving staat telkens in de kolom naast de code (zo is de tabel opgebouwd).
    for naam in ("bd", "ap", "ac", "mjp"):
        i = kaart[naam]
        kaart[naam + "_oms"] = i + 1 if i is not None and i + 1 < n else None

    kaart["dienst"] = next((i for i, h in enumerate(header) if "dienst" in schoon(h).lower()), None)

    # Jaarkolommen: kop draagt een jaartal én de cellen eronder zijn bedragen. Die tweede
    # eis houdt een kolom als 'Code beleidsveld 2020-02' buiten de bedragen.
    jaren = []
    for i, h in enumerate(header):
        m = JAAR_RX.search(schoon(h))
        if not m:
            continue
        gevuld = sum(1 for r in rijen if i < len(r) and bedrag(r[i]) is not None)
        if gevuld < max(2, len(rijen) // 3):
            continue
        kop = schoon(h).lower()
        soort = ("rekening" if "rekening" in kop else
                 "krediet" if "krediet" in kop else "raming")
        jaren.append({"kolom": i, "jaar": int(m.group(1)), "soort": soort})
    kaart["jaren"] = jaren
    return kaart


def lees_document(pdf: Path) -> dict:
    """Alle begrotingslijnen uit één pdf. Gecachet op pad + wijzigingstijd + grootte: een
    ongewijzigd document wordt nooit een tweede keer uitgelezen (het duurt minuten)."""
    st = pdf.stat()
    sleutel = hashlib.sha1(f"MJP|{pdf.as_posix()}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
    cache = CACHE / (sleutel + ".json")
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))

    import pdfplumber
    lijnen, kaart, paginas = {}, None, 0
    with pdfplumber.open(pdf) as doc:
        for pagina in doc.pages:
            tabel = pagina.extract_table()
            if not tabel or len(tabel) < 2:
                continue
            header, rijen = tabel[0], tabel[1:]
            if kaart is None:
                kaart = kolomkaart(header, rijen)
                if kaart is None:
                    continue
            paginas += 1
            for r in rijen:
                code = schoon(r[kaart["mjp"]]) if kaart["mjp"] < len(r) else ""
                if not MJP_RX.match(code) or code in lijnen:
                    continue
                def cel(sleutelnaam):
                    i = kaart.get(sleutelnaam)
                    return schoon(r[i]) if i is not None and i < len(r) else ""
                bedragen = []
                for j in kaart["jaren"]:
                    w = bedrag(r[j["kolom"]]) if j["kolom"] < len(r) else None
                    if w is not None:
                        bedragen.append({"jaar": j["jaar"], "soort": j["soort"], "bedrag": w})
                lijnen[code] = {
                    "entiteit": schoon(r[0]) if r else "",
                    "doelstelling": [cel("bd"), cel("bd_oms")],
                    "actieplan": [cel("ap"), cel("ap_oms")],
                    "actie": [cel("ac"), cel("ac_oms")],
                    "dienst": cel("dienst"),
                    "omschrijving": cel("mjp_oms"),
                    "bedragen": bedragen,
                }

    uit = {"lijnen": lijnen, "paginas": paginas,
           "jaren": [j["jaar"] for j in (kaart or {}).get("jaren", [])]}
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(uit, ensure_ascii=False), encoding="utf-8")
    return uit


def kandidaten(diep: bool) -> list[dict]:
    """De overzichtstabellen uit de index, in paginavolgorde (nieuwste eerst).

    Zonder --diep houden we per meerjarenplan enkel de nieuwste versie van Stad en OCMW
    over. Dat is bewust zuinig: elke extra pdf kost minuten uitleeswerk, en een oudere
    aanpassing van hetzelfde plan voegt vooral achterhaalde bedragen toe."""
    if not INDEX.exists():
        return []
    docs = json.loads(INDEX.read_text(encoding="utf-8"))["documenten"]
    uit = [d for d in docs if TITEL_RX.search(d["titel"]) and d.get("aanwezig")]
    if diep:
        return uit
    gezien, kort = set(), []
    for d in uit:
        if not d["entiteit"].lower().startswith("stad en ocmw"):
            continue
        m = re.search(r"(20\d{2})\s*-\s*(20\d{2})", d["set"] or "")
        plan = m.group(0) if m else d["set"]
        if plan in gezien:
            continue
        gezien.add(plan)
        kort.append(d)
    return kort


def corpus_codes() -> dict:
    """MJP-code -> lijst van punt-id's die ernaar verwijzen (via delf_verwijzingen, zodat
    beide stappen exact dezelfde oogst gebruiken en de caches gedeeld blijven)."""
    import delf_verwijzingen as dv
    uit = {}
    for sleutel, items in dv.verzamel_codes().items():
        soort, _, code = sleutel.partition(":")
        if soort == "mjp":
            uit[code] = sorted(items)
    return uit


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--diep", action="store_true",
                    help="alle planversies en alle entiteiten uitlezen, niet enkel de nieuwste")
    ap.add_argument("--code", help="één MJP-code opzoeken in de lokale cache en tonen")
    args = ap.parse_args()

    docs = kandidaten(args.diep)
    if not docs:
        print("  (geen budgetstukken gevonden — draai eerst fetch_budgetten.py)")
        return

    bronnen, alles = [], {}
    for d in docs:
        pdf = BUDGET / d["bestand"]
        if not pdf.exists():
            continue
        gelezen = lees_document(pdf)
        if not gelezen["lijnen"]:
            print(f"  (overgeslagen, geen leesbare tabel: {d['titel'][:60]})")
            continue
        bron = len(bronnen)
        bronnen.append({"entiteit": d["entiteit"], "set": d["set"], "titel": d["titel"],
                        "url": d["url"], "jaren": gelezen["jaren"]})
        nieuw = 0
        for code, lijn in gelezen["lijnen"].items():
            if code not in alles:          # nieuwste plan wint (paginavolgorde)
                alles[code] = {**lijn, "bron": bron}
                nieuw += 1
        print(f"  {d['set'] or d['entiteit']} · {d['titel'][:52]}: "
              f"{len(gelezen['lijnen']):,} lijnen ({nieuw:,} nieuw)")

    if args.code:
        lijn = alles.get(args.code.upper())
        print(json.dumps(lijn, ensure_ascii=False, indent=2) if lijn
              else f"{args.code} staat niet in deze stukken.")
        return

    gebruikt = corpus_codes()
    acties = {c: {**alles[c], "punten": ids} for c, ids in sorted(gebruikt.items()) if c in alles}
    UIT.write_text(json.dumps({"gen": "", "bronnen": bronnen, "acties": acties},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    zonder = len(gebruikt) - len(acties)
    kb = UIT.stat().st_size / 1024
    print(f"MJP-acties gekoppeld: {len(acties)} van de {len(gebruikt)} codes in het corpus "
          f"({zonder} niet teruggevonden) uit {len(alles):,} begrotingslijnen "
          f"-> mjp_acties.json ({kb:,.0f} kB)")


if __name__ == "__main__":
    main()
