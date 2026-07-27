"""
assembleer_agendapunten.py — voegt de geparste beslissingen van één zitting als
echte agendapunten toe aan data.json, met het stemgedrag per fractie uit de notulen.

Dit is de ontbrekende schakel tussen de parsers en build.py: parse_besluiten +
parse_notulen leveren JSON, dit script giet die in het formaat dat de site leest.

Wat nog NIET ingevuld wordt (komt in de AI-tagging, stap 11):
  - decoded (mensentaal-samenvatting) → voorlopig de officiële titel
  - streets / neighborhood / schepen → leeg
Thema's worden voorlopig afgeleid uit de categorie (deterministische map, geen AI).

Gebruik:  python assembleer_agendapunten.py 2026-04-28 besluiten.agendapunten.json notulen.notulen.json
"""

# Robuuste console-uitvoer: zet stdout/stderr op UTF-8, zodat print() met niet-ASCII
# (pijlen, vinkjes) niet crasht op een Windows-console die standaard cp1252 gebruikt.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import sys, json
from pathlib import Path

THEMA = {
 "POLITIEKE ORGANEN-WERKING": ["Participatie"], "POLITIEVERORDENINGEN": ["Veiligheid"],
 "FINANCIËN-BELASTINGEN": ["Financiën"], "CULTUUR": ["Cultuur"], "JEUGD": ["Jeugd"],
 "SPORT": ["Sport"], "ONDERWIJS": ["Onderwijs"], "SOCIALE ECONOMIE": ["Economie","Sociale zaken"],
 "SOCIALE ZAKEN": ["Sociale zaken"], "MOBILITEIT": ["Mobiliteit"], "STADSONTWIKKELING": ["Stedenbouw"],
 "OPENBAAR DOMEIN": ["Openbare werken"], "GEBOUWEN": ["Openbare werken"],
 "VASTGOEDBELEID": ["Stedenbouw"], "ICT": [], "JURIDISCHE ZAKEN": [],
}

# resultaat-tekst -> slug die de frontend kent (voor badge + filter)
RESULTAAT = {
 "Goedgekeurd": "goedgekeurd", "Bekrachtigd": "bekrachtigd", "Vastgesteld": "vastgesteld",
 "Niet ter stemming gelegd": "niet-gestemd", "Verdaagd": "verdaagd", "Verworpen": "verworpen",
}

def main():
    # --orgaan bepaalt of dit de gemeenteraad of de raad voor maatschappelijk welzijn is;
    # standaard gemeenteraad. Dit stuurt de sessie_id-prefix (gemeenteraad- / rmw-) die
    # de frontend gebruikt om het orgaan te tonen én de juiste bron-ingang te kiezen.
    args = sys.argv[1:]
    orgaan = "Gemeenteraad"
    if "--orgaan" in args:
        i = args.index("--orgaan"); orgaan = args[i + 1]; del args[i:i + 2]
    datum, besl_path = args[0], args[1]
    not_path = args[2] if len(args) > 2 else None
    data = json.loads(Path("data.json").read_text(encoding="utf-8"))
    besluiten = json.loads(Path(besl_path).read_text(encoding="utf-8"))
    notulen = {}
    if not_path and Path(not_path).exists():
        notulen = {p["nummer"]: p for p in json.loads(Path(not_path).read_text(encoding="utf-8"))["punten"]}

    SLUG = {"Gemeenteraad": "gemeenteraad", "Raad voor maatschappelijk welzijn": "rmw"}
    sessie_id = f"{SLUG.get(orgaan, 'gemeenteraad')}-{datum.replace('-','')}"
    nieuwe = []
    for it in besluiten:
        if it.get("zitting") == "besloten":          # besloten zitting niet publiceren (zoals bij college/agenda)
            continue
        nr = it["nummer"]
        note = notulen.get(nr, {})
        nieuwe.append({
            "id": f"{sessie_id}-{nr}",
            "sessie_id": sessie_id,
            "sessie_date": datum,
            "nummer": nr,
            "url": None,
            "status": "beslist",
            "result": {"decision": RESULTAAT.get(it["resultaat"], it["resultaat"].lower()),
                       "text": it["resultaat"], "published": datum},
            "impact": 2,
            "type": it["type"],
            "aanvullend": it["aanvullend"],
            "titel": it["titel"],
            "decoded": it["titel"],                       # placeholder tot AI-tagging
            "themes": THEMA.get(it["categorie"], []),
            "schepen": None,
            "streets": [],
            "neighborhood": None,
            "financial_impact": None,
            "indiener": it.get("indiener"),
            "stemming": note.get("stemming"),             # incl. per_fractie + kleur
            "brontekst": note.get("tekst"),               # volledige notulentekst → input AI-tagging + 'Toon originele tekst'
        })

    # verwijder eventuele eerdere punten van deze zitting, voeg de echte toe
    data["agendapunten"] = [a for a in data.get("agendapunten", []) if a.get("sessie_id") != sessie_id] + nieuwe
    Path("data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    met_stem = sum(1 for a in nieuwe if a.get("stemming") and a["stemming"].get("per_fractie"))
    print(f"{len(nieuwe)} agendapunten toegevoegd voor {datum} ({met_stem} met stemming per fractie)")

if __name__ == "__main__":
    main()
