"""
assembleer_agendapunten.py — voegt de geparste beslissingen van één zitting als
echte agendapunten toe aan data.json, met het stemgedrag per fractie uit de notulen.

Dit is de ontbrekende schakel tussen de parsers en build.py: parse_besluiten +
parse_notulen leveren JSON, dit script giet die in het formaat dat de site leest.

Wat nog NIET ingevuld wordt (komt in de AI-tagging, stap 11):
  - decoded (mensentaal-samenvatting) → voorlopig de officiële titel
  - streets / neighborhood / schepen → leeg
Thema's worden voorlopig afgeleid uit de categorie (deterministische map, geen AI).

Gebruik:  python pijplijn/assembleer_agendapunten.py 2026-04-28 besluiten.agendapunten.json notulen.notulen.json
"""

# Robuuste console-uitvoer: zet stdout/stderr op UTF-8, zodat print() met niet-ASCII
# (pijlen, vinkjes) niet crasht op een Windows-console die standaard cp1252 gebruikt.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import sys, json, re
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

# De kop van een volgend agendapunt: nummer (of TP/ACT/V met nummer), een punt en een rubriek in
# hoofdletters met een punt erachter, eventueel met een opsommingsteken ervoor. Een genummerde
# opsomming in een schriftelijk antwoord heeft geen rubriek in hoofdletters en blijft dus staan.
VOLGEND_PUNT = re.compile(r"\n\s*(?:\s*)?(?:TP\d+|ACT\d+|V\d+|\d+)\.\s*[A-ZÀ-Þ][A-ZÀ-Þ \-]{2,40}\.")


def raadslid_voluit(kort):
    """'K. Lauwers' -> 'Karl Lauwers (CD&V)' via data/raadsleden.json, als er precies een raadslid past
    (zelfde familienaam, voornaam met dezelfde beginletter). Anders blijft de korte vorm staan."""
    if not kort:
        return kort
    m = re.match(r"^([A-Z])\.\s*(.+)$", kort.strip())
    pad = Path("data") / "raadsleden.json"
    if not m or not pad.exists():
        return kort
    letter, fam = m.group(1), m.group(2).strip().lower()
    leden = json.loads(pad.read_text(encoding="utf-8"))
    pas = [(naam, fr) for naam, fr in leden.items()
           if naam.lower().endswith(" " + fam) and naam[:1].upper() == letter]
    if len(pas) != 1:
        return kort
    naam, fractie = pas[0]
    return f"{naam} ({fractie})" if fractie else naam


def main():
    # --orgaan bepaalt of dit de gemeenteraad of de raad voor maatschappelijk welzijn is;
    # standaard gemeenteraad. Dit stuurt de sessie_id-prefix (gemeenteraad- / rmw-) die
    # de frontend gebruikt om het orgaan te tonen én de juiste bron-ingang te kiezen.
    args = sys.argv[1:]
    orgaan = "Gemeenteraad"
    if "--orgaan" in args:
        i = args.index("--orgaan"); orgaan = args[i + 1]; del args[i:i + 2]
    # --agenda <bestand> (mag herhaald): de geparste agenda en aanvullende agenda. Daaruit komen de
    # mondelinge vragen zolang de notulen er nog niet zijn.
    agenda_paden = []
    while "--agenda" in args:
        i = args.index("--agenda"); agenda_paden.append(args[i + 1]); del args[i:i + 2]
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
        # Besloten zitting: de stad publiceert in de openbare besluitenlijst enkel de TITEL
        # (geen stukken, geen stemming). Die titel is dus al publiek en gaat gewoon mee;
        # de site labelt het punt als 'besloten zitting' zodat de lezer weet waarom er
        # niets achter zit. De documenten zelf bestaan publiek niet en blijven dus weg.
        besloten = it.get("zitting") == "besloten"
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
            **({"zitting": "besloten"} if besloten else {}),
        })

    # Mondelinge vragen (V1., V2., ...) staan niet in de besluitenlijst: er wordt niet over gestemd.
    # De notulen dragen ze wel, met de vraagsteller en de vraag. Meestal volgt het antwoord mondeling
    # ter zitting en staat het niet in de notulen; soms volgt het schriftelijk na de zitting, en dan
    # staat het er wel in. Zolang de notulen er niet zijn, komt de vraag van de agenda: enkel titel
    # en vraagsteller. Een agenda schrijft "V1", sommige notulen "V01": dat is dezelfde vraag.
    def vraagnr(nr):
        return re.sub(r"^V0*(?=\d)", "V", nr)
    agenda = {}
    for pad in agenda_paden:
        if Path(pad).exists():
            ruw = json.loads(Path(pad).read_text(encoding="utf-8"))
            for p in (ruw if isinstance(ruw, list) else ruw.get("punten", [])):
                if p.get("type") == "vraag":
                    agenda.setdefault(vraagnr(p["nummer"]), p)
    vragen = {vraagnr(nr): note for nr, note in notulen.items() if note.get("type") == "vraag"}
    in_lijst = {vraagnr(n["nummer"]) for n in nieuwe}
    for nr in sorted(set(vragen) | set(agenda), key=lambda x: int(x[1:])):
        if nr in in_lijst:
            continue
        note = vragen.get(nr) or agenda[nr]
        tekst = note.get("tekst") if nr in vragen else None
        if tekst and "Antwoord" in tekst:
            # Na het antwoord hoort de tekst te stoppen. Staat de kop van het volgende punt met een
            # opsommingsteken in de notulen, dan herkent de parser die niet en loopt de vraag door.
            m = VOLGEND_PUNT.search(tekst, tekst.find("Antwoord"))
            if m:
                tekst = tekst[:m.start()].rstrip()
        if tekst is None:
            antwoord = None
        elif "De vraag wordt schriftelijk beantwoord" in tekst:
            antwoord = "schriftelijk beantwoord"
        else:
            antwoord = "mondeling beantwoord"
        titel = (note.get("titel") or "").strip()
        nieuwe.append({
            "id": f"{sessie_id}-{nr}",
            "sessie_id": sessie_id,
            "sessie_date": datum,
            "nummer": nr,
            "url": None,
            "status": "beslist" if antwoord else "agenda",
            "result": ({"decision": antwoord, "text": antwoord.capitalize(), "published": datum}
                       if antwoord else None),
            "impact": 1,
            "type": "vraag",
            "aanvullend": True,
            "titel": titel[:1].upper() + titel[1:],
            "decoded": titel[:1].upper() + titel[1:],
            "themes": [],
            "schepen": None,
            "streets": [],
            "neighborhood": None,
            "financial_impact": None,
            "indiener": raadslid_voluit(note.get("indiener")),
            "stemming": None,
            "brontekst": tekst,
        })
    # Vaste klep: elke vraag op de agenda moet op de site staan.
    ontbreekt = sorted(set(agenda) - {vraagnr(n["nummer"]) for n in nieuwe})
    if ontbreekt:
        sys.exit(f"[STOP] {sessie_id}: vragen van de agenda ontbreken ({', '.join(ontbreekt)}).")

    # verwijder eventuele eerdere punten van deze zitting, voeg de echte toe
    data["agendapunten"] = [a for a in data.get("agendapunten", []) if a.get("sessie_id") != sessie_id] + nieuwe
    from koppel_uittreksels import schrijf_json     # tijdelijk bestand + vervangen, met herhaling
    schrijf_json(Path("data.json"), data)

    met_stem = sum(1 for a in nieuwe if a.get("stemming") and a["stemming"].get("per_fractie"))
    print(f"{len(nieuwe)} agendapunten toegevoegd voor {datum} ({met_stem} met stemming per fractie)")

if __name__ == "__main__":
    main()
