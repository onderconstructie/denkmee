"""
assembleer_agenda.py — zet de geparste agenda van één zitting in data.json als de
punten van de KOMENDE gemeenteraad of raad voor maatschappelijk welzijn (sectie 02).

Deze punten krijgen status 'op-agenda' en GEEN resultaat — zo verschijnen ze in de
agenda-sectie (die filtert op sessie_id == next_meeting.id) maar niet in het archief
(dat enkel beslissingen mét uitkomst toont). Zodra de besluitenlijst van diezelfde
zitting binnenkomt, vervangt assembleer_agendapunten.py deze punten (zelfde sessie_id).

Meerdere agenda-bestanden mogen meegegeven worden (bv. de gewone én de volledige
agenda met vragen); ze worden samengevoegd op puntnummer, laatste wint.

Gebruik:  python assembleer_agenda.py 2026-06-23 agenda.agenda.json aanvullend.agenda.json --orgaan "Gemeenteraad"
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

# Domein (HOOFDLETTERS) → thema uit de vaste taxonomie. Enkel de heldere gevallen;
# de rest blijft leeg en wordt door de AI-tagging-stap ingevuld. Niets verzinnen.
THEMA = {
    "POLITIEVERORDENINGEN": ["Veiligheid"], "FINANCIËN-TOEZICHT EREDIENSTEN": ["Financiën"],
    "FINANCIËN-BELASTINGEN": ["Financiën"], "SPORT": ["Sport"], "CULTUUR": ["Cultuur"],
    "MUSEA": ["Cultuur", "Erfgoed"], "JEUGD": ["Jeugd"], "PARTICIPATIE": ["Participatie"],
    "SOCIAAL BELEID": ["Sociale zaken"], "MOBILITEIT": ["Mobiliteit"],
    "VASTGOEDBELEID": ["Stedenbouw"], "STADSONTWIKKELING": ["Stedenbouw"],
    "ONDERWIJS": ["Onderwijs"], "KUNSTONDERWIJS": ["Onderwijs"],
}

SLUG = {"Gemeenteraad": "gemeenteraad", "Raad voor maatschappelijk welzijn": "rmw"}


def main():
    args = sys.argv[1:]
    orgaan = "Gemeenteraad"
    if "--orgaan" in args:
        i = args.index("--orgaan"); orgaan = args[i + 1]; del args[i:i + 2]
    if len(args) < 2:
        print("Gebruik: python assembleer_agenda.py JJJJ-MM-DD agenda.agenda.json [meer.json] --orgaan X")
        return
    datum, agenda_paden = args[0], args[1:]

    data = json.loads(Path("data.json").read_text(encoding="utf-8"))
    sessie_id = f"{SLUG.get(orgaan, 'gemeenteraad')}-{datum.replace('-', '')}"

    # voeg de agenda-bestanden samen op puntnummer (laatste wint → volledige agenda
    # overschrijft de basisagenda, en vult aan met vragen/toegevoegde punten)
    per_nummer = {}
    for pad in agenda_paden:
        for it in json.loads(Path(pad).read_text(encoding="utf-8")):
            per_nummer[it["nummer"]] = it

    nieuwe = []
    for it in per_nummer.values():
        if it.get("zitting") == "besloten":          # besloten zitting niet publiceren
            continue
        nr = it["nummer"]
        nieuwe.append({
            "id": f"{sessie_id}-{nr}",
            "sessie_id": sessie_id,
            "sessie_date": datum,
            "nummer": nr,
            "url": None,
            "status": "op-agenda",
            "result": None,                          # nog geen uitkomst
            "impact": 2,
            "type": it.get("type", "gewoon"),
            "aanvullend": it.get("type") == "toegevoegd",
            "titel": it["titel"],
            "decoded": it["titel"],                  # placeholder tot AI-tagging
            "themes": THEMA.get(it.get("categorie", ""), []),
            "schepen": None,
            "streets": [],
            "neighborhood": None,
            "financial_impact": None,
            "indiener": it.get("indiener"),
            "stemming": None,
            "brontekst": it["titel"],                # agenda heeft geen volledige tekst → titel
        })

    # vervang de punten van deze zitting, behoud de rest
    data["agendapunten"] = [a for a in data.get("agendapunten", []) if a.get("sessie_id") != sessie_id] + nieuwe
    Path("data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    vr = sum(1 for a in nieuwe if a["type"] == "vraag")
    print(f"{len(nieuwe)} agendapunten voor {orgaan} {datum} op de agenda gezet ({vr} vragen)")


if __name__ == "__main__":
    main()
