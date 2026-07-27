"""
assembleer_college.py — voegt de geparste besluitenlijst van één college-zitting
toe aan data.json als college_beslissingen (de Collegemonitor, sectie 04).

Het college van burgemeester en schepenen publiceert enkel een besluitenlijst
(geen agenda, geen notulen). Dit script giet de output van parse_besluiten.py in
de minimale vorm die de frontend leest: {id, date, categorie, titel}.

'categorie' wordt afgeleid uit het domein (de HOOFDLETTERS-kop in de besluitenlijst)
via een deterministische thema-map — geen AI. De rijke laag (mensentaal-samenvatting,
straten, bevoegde schepen) komt in de AI-tagging-stap erna.

Belangrijk: zodra er voor het eerst ECHTE collegebesluiten binnenkomen, worden de
demo-records (id begint met 'col-') volledig verwijderd, zodat demo en echte data
nooit vermengd raken. De is_demo-vlag in data.json laat je pas op False zetten als
álle organen echt zijn.

Gebruik:  python assembleer_college.py 2026-05-26 besluiten.agendapunten.json
"""

# Robuuste console-uitvoer: zet stdout/stderr op UTF-8, zodat print() met niet-ASCII
# (pijlen, vinkjes) niet crasht op een Windows-console die standaard cp1252 gebruikt.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import sys, json, argparse, hashlib
from pathlib import Path

# College/vast bureau gebruiken administratieve dienstnamen als domein
# ("Bouwdienst", "Toezicht Financiën", …), geen burger-thema's. Waar een dienst
# helder op één thema valt, mappen we deterministisch; anders behouden we het
# echte domein (nooit een verzonnen thema). De AI-tagging-stap verfijnt dit later.
THEMA = {
    "Bestuurlijk Beheer": "Bestuur", "Burgerzaken": "Bestuur",
    "Strategie & ICT": "Bestuur", "Interne Dienst Preventie en Bescherming": "Bestuur",
    "Overheidsopdrachten": "Bestuur", "Zaalbeheer & Protocol": "Bestuur",
    "Boekhouding": "Financiën", "Belastingen en Inningen": "Financiën",
    "Staf Financiën": "Financiën", "Toezicht Financiën": "Financiën",
    "Bouwdienst": "Stedenbouw", "Vastgoedmanagement": "Stedenbouw",
    "Integraal Stedelijk Beleid": "Stedenbouw",
    "Project Management Bureau": "Openbare werken",
    "Infrastructuurprojecten Openbaar Domein": "Openbare werken",
    "Sociaal Beleid": "Sociale zaken", "Sociale zaken": "Sociale zaken",
    "Jeugd": "Jeugd", "Sport": "Sport", "Kunstonderwijs": "Onderwijs",
    "Mechelen Feest": "Cultuur", "Milieu en Landbouw": "Milieu",
    "GAS Rivierenland": "Veiligheid",
}


def thema(domein: str) -> str:
    if not domein:
        return "Overig"
    domein = domein.strip()
    return THEMA.get(domein, domein)           # geen match → echt domein behouden


def main():
    ap = argparse.ArgumentParser(description="Voeg de besluitenlijst van één college- of vast-bureau-zitting toe aan data.json.")
    ap.add_argument("datum")
    ap.add_argument("besl_path")
    ap.add_argument("--orgaan", default="College",
                    help="Welk orgaan: 'College' (standaard), 'Vast bureau' of 'Burgemeester'.")
    args = ap.parse_args()
    datum, besl_path, orgaan = args.datum, args.besl_path, args.orgaan
    prefix = {"Vast bureau": "vastbureau", "Burgemeester": "burgemeester"}.get(orgaan, "college")

    data = json.loads(Path("data.json").read_text(encoding="utf-8"))
    besluiten = json.loads(Path(besl_path).read_text(encoding="utf-8"))

    nieuwe = []
    gezien_id = set()
    for it in besluiten:
        if it.get("zitting") == "besloten":          # besloten zitting niet publiceren
            continue
        titel = (it.get("titel") or "").strip()
        if not titel or titel.upper().startswith("GESCHRAPT"):   # geschrapte punten overslaan
            continue
        nr = it["nummer"]
        stuk_id = f"{prefix}-{datum.replace('-', '')}-{nr}"
        # Eén besluitenlijst kan twee punten met hetzelfde nummer bevatten (gezien op de
        # collegezitting van 31/03/2026). Zonder ontdubbeling kregen die hetzelfde id, waarna
        # koppel_uittreksels dezelfde bronlink aan allebei hing en de zoekindex er stilletjes
        # één liet verdwijnen. Het eerste punt houdt het kale id, zodat bestaande links en de
        # ankers in correcties.json blijven werken; het volgende krijgt een titel-hash, die
        # niet verschuift als er later nog een punt bijkomt.
        if stuk_id in gezien_id:
            kort = hashlib.sha1(titel.encode("utf-8")).hexdigest()[:6]
            print(f"  [dubbel nummer] {stuk_id}: '{titel[:45]}' -> {stuk_id}-{kort}")
            stuk_id = f"{stuk_id}-{kort}"
        gezien_id.add(stuk_id)
        nieuwe.append({
            "id": stuk_id,
            "date": datum,
            "orgaan": orgaan,
            "categorie": thema(it.get("categorie", "")),
            "titel": titel,
        })

    # College én vast bureau leven in dezelfde lijst, uit elkaar gehouden door 'orgaan'.
    # We vervangen enkel deze (datum + orgaan)-combinatie en wissen oude demo-records ('col-').
    def org_van(b): return b.get("orgaan", "College")
    bestaand = [b for b in data.get("college_beslissingen", [])
                if not str(b.get("id", "")).startswith("col-")
                and not (b.get("date") == datum and org_van(b) == orgaan)]

    data["college_beslissingen"] = sorted(bestaand + nieuwe,
                                           key=lambda b: b["date"], reverse=True)
    Path("data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{len(nieuwe)} besluiten ({orgaan}) toegevoegd voor {datum}; "
          f"{len(data['college_beslissingen'])} totaal in college_beslissingen")


if __name__ == "__main__":
    main()
