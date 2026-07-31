"""
run_all.py — DE STARTKNOP. Draait de volledige pijplijn in de juiste volgorde,
zodat dist/index.html vanzelf ontstaat. Eén commando:  python run_all.py

Volgorde (dit is de volledige lijst; de volgorde is niet vrijblijvend, zie hieronder):
  1)   fetch_all.py       — zittingen + documenten ophalen van lblod, PDF's downloaden
  1b)  fetch_zittingen.py — geplande zittingsdatums van mechelen.be (faalt zacht)
  1c)  fetch_schriftelijke_vragen.py — pdf's van de schriftelijke vragen
  1d)  fetch_uittreksels.py — individuele uittreksels/bijlagen per zitting (faalt zacht)
  1e)  fetch_budgetten.py  — meerjarenplannen, budgetten en jaarrekeningen (faalt zacht)
  2)   maak_data.py       — tijdigheid (art. 22 / 287) + sessies in data.json zetten
  3)   per zitting        — besluitenlijst + notulen uitlezen en als agendapunten
                            (met stemming per fractie) in data.json gieten
  3a)  parse_schriftelijke_vragen.py — de pdf's uitlezen naar data.json
  3b)  straten_mechelen.py — volledige stratenlijst (optioneel, eenmalig)
  3b2) koppel_uittreksels.py — uittreksels aan de besluiten koppelen + tekst cachen
  3c)  tag_items.py       — AI-samenvattingen (optioneel, enkel met ANTHROPIC_API_KEY)
  3d)  schoon_brontekst.py — e-mailadressen uit de gepubliceerde velden redacteren
  3e)  bouw_zoekindex.py  — volledige-tekstindex voor de zoekbalk
  3f)  delf_verwijzingen.py — harde codes (MJP, zaaknummer, OMV) voor verwante dossiers
  3g)  parse_mjp_acties.py — de begrotingslijn achter elke MJP-code (faalt zacht)
  4)   build.py           — template.html + data.json  ->  dist/
  5)   opkuis             — tussenbestanden in de root wissen

LET OP bij een losse stap: elk parse_*-script herschrijft ZIJN DEEL van data.json volledig.
Draai je er één apart, dan zijn de AI-samenvattingen van die categorie weg en staan er weer
rauwe e-mailadressen in. Draai daarna altijd 3c (haalt alles gratis uit de cache), 3d, 3e en 4.

Publiceren gebeurt los van deze knop: upload de map dist/, of laat de GitHub
Pages-workflow (.github/workflows/pages.yml) het bij elke push doen.
"""
import subprocess
import sys
import os
import json
from pathlib import Path
from datetime import date

# print() met → ✓ … werkt zo ook op een Windows-console die standaard cp1252 gebruikt
# (anders crasht het eerste niet-ASCII-teken met een UnicodeEncodeError). De subprocessen
# krijgen hetzelfde mee via PYTHONUTF8 in run().
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent


def run(*args):
    print("›", " ".join(str(a) for a in args))
    env = {**os.environ, "PYTHONUTF8": "1"}   # elk pijplijn-script schrijft UTF-8, ook op cp1252
    subprocess.run([sys.executable, *map(str, args)], cwd=BASE, check=True, env=env)


def ensure_straten():
    """Volledige stratenlijst: enkel maken als ze er nog niet is (verandert zelden).
    straten_mechelen.py heeft enkel 'requests' + internet nodig (geen shapely meer:
    de geo-route draait in pure Python); faalt het, dan stopt de bouw NIET — de
    frontend valt terug op de geverifieerde demo-seed in template.html."""
    if (BASE / "data" / "straten_mechelen.json").exists():
        return
    try:
        run("straten_mechelen.py")
    except subprocess.CalledProcessError:
        print("  (overgeslagen: straten_mechelen.py faalde — frontend gebruikt de demo-seed.\n"
              "   Het vereist enkel 'requests' + internet; draai het desnoods apart.)")


def haal_uittreksels():
    """De individuele uittreksels (en zeldzame bijlagen) per zitting ophalen: de rijkste bron,
    want ze bevatten de volledige besluit-tekst waar de besluitenlijst enkel een titel geeft
    (vooral bij het college). Idempotent — downloadt enkel nieuwe pdf's. Faalt het (lblod
    onbereikbaar), dan stopt de bouw NIET: de koppeling valt dan gewoon weg."""
    try:
        run("fetch_uittreksels.py", "--download")
    except subprocess.CalledProcessError:
        print("  (overgeslagen: fetch_uittreksels.py faalde — besluiten blijven zonder uittreksel-tekst.)")


def koppel_uittreksels():
    """De opgehaalde uittreksels aan hun besluit koppelen en de volledige tekst extraheren naar de
    lokale cache (git-genegeerd). Verrijkt de brontekst voor de tagging en de zoekindex, en zet een
    'uittreksel_url' op de rechtstreeks gekoppelde besluiten. Draait ná de assemblage (die de
    besluiten vult) en vóór de tagging + zoekindex (die de koppeltabel lezen). Geen index of een
    fout → netjes overslaan; de rest van de bouw gaat door."""
    try:
        run("koppel_uittreksels.py")
    except subprocess.CalledProcessError:
        print("  (overgeslagen: koppel_uittreksels.py faalde of er is nog geen uittreksel-index.)")


def verrijk_optioneel():
    """AI-tagging (samenvattingen + thema's) enkel als er een Anthropic-sleutel is.
    Geen sleutel → netjes overslaan; een dry-run zou placeholder-tekst op de site
    zetten, dus dat doen we hier bewust niet."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("› AI-verrijking overgeslagen (geen ANTHROPIC_API_KEY gezet).")
        return
    try:
        run("tag_items.py")
    except subprocess.CalledProcessError:
        print("  (AI-verrijking faalde — punten blijven zonder samenvatting/thema's.)")


def opkuis():
    """Wis de tussenbestanden die de parse-stappen in de root achterlaten."""
    for naam in ("besluiten.agendapunten.json", "notulen.notulen.json",
                 "agenda.agenda.json", "aanvullend.agenda.json"):
        p = BASE / naam
        if p.exists():
            p.unlink()


def reset_voor_assemblage(base):
    """Schoon beginnen vóór de assemblage: wis de demo-inhoud zodat enkel echte
    besluiten overblijven, leid de volgende zitting af uit de echte sessies +
    geplande datums van de stadswebsite, en zet is_demo om. Draait bewust pas ná
    maak_data.py (echte sessies) en vóór de assemblage-lus (vult de besluiten)."""
    pad = base / "data.json"
    data = json.loads(pad.read_text(encoding="utf-8"))
    sessies = data.get("sessies", [])
    data["agendapunten"] = []                 # wordt zo dadelijk gevuld met echte besluiten
    data["college_beslissingen"] = []         # idem (assembleer_college wist ook de col--demo)

    vandaag = date.today().isoformat()

    # Geplande zittingen van de stadswebsite — betrouwbaarder voor toekomstige datums
    # dan de publicatie-index, die een zitting pas kent zodra er een document is.
    gepland = []
    gp = base / "data" / "geplande_zittingen.json"
    if gp.exists():
        gepland = json.loads(gp.read_text(encoding="utf-8"))

    SLUG = {"Gemeenteraad": "gemeenteraad", "Raad voor maatschappelijk welzijn": "rmw"}
    # Zelfde typecode als maak_data.py gebruikt. Schreven we hier de VOLLE orgaannaam weg, dan
    # herkende de duplicaat-check hieronder de echte RMW-zitting niet (die draagt "RMW"), stond
    # dezelfde zitting twee keer in data["sessies"], en ruimde maak_data.py het geplande record
    # nooit op omdat het buiten zijn verwerkte codes viel. Dat leverde eeuwig "geplande"
    # zittingen op met een datum die allang voorbij is.
    TYPECODE = {"Gemeenteraad": "Gemeenteraad", "Raad voor maatschappelijk welzijn": "RMW"}
    bekend = {(s.get("type"), s.get("date")) for s in sessies}
    geplande_komend = []
    for g in gepland:
        code = TYPECODE.get(g["type"], g["type"])
        if g["date"] >= vandaag and (code, g["date"]) not in bekend:
            geplande_komend.append({
                "id": f"{SLUG.get(g['type'], 'gemeenteraad')}-{g['date'].replace('-', '')}",
                "type": code, "date": g["date"], "datetime": g["date"] + "T20:00:00",
                "agenda_url": None, "agenda_published": None, "agenda_deadline": None,
                "status": "gepland", "late_days": 0,
            })
    # voeg de nog-onbekende geplande zittingen toe aan de sessies
    data["sessies"] = sorted(sessies + geplande_komend, key=lambda s: s["date"])

    # volgende zitting = vroegste geplande gemeenteraad vanaf vandaag (anders vroegste toekomstige)
    kandidaten = sorted((s for s in data["sessies"] if s.get("date", "") >= vandaag),
                        key=lambda s: s["date"])
    gr = [s for s in kandidaten if s.get("type") == "Gemeenteraad"]
    nm = (gr or kandidaten or [None])[0]
    data["next_meeting"] = {**nm, "agenda_status": nm.get("status", "gepland")} if nm else None

    if sessies or gepland:                    # echte of geplande data aanwezig → geen demo meer
        data["is_demo"] = False
    pad.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    vm = data["next_meeting"]["date"] if data["next_meeting"] else "geen geplande zitting"
    print(f"  schoon: demo-inhoud gewist · volgende zitting = {vm} · is_demo = {data['is_demo']}")


def haal_budgetten():
    """De budgetstukken van de stad: meerjarenplannen, aanpassingen, budgetwijzigingen en
    jaarrekeningen. Idempotent — vergelijkt de grootte bij de bron met wat er lokaal staat en
    downloadt enkel wat nieuw of vervangen is. Dit is meteen de wachtpost op die pagina: de
    stad publiceert er twee à vier keer per jaar iets, en het script meldt wat er veranderde.
    Faalt het (pagina onbereikbaar of anders opgebouwd), dan stopt de bouw NIET: de
    begrotingskoppeling valt dan gewoon terug op wat er al binnengehaald was."""
    try:
        run("fetch_budgetten.py")
    except subprocess.CalledProcessError:
        print("  (overgeslagen: fetch_budgetten.py faalde — geen nieuwe budgetstukken.)")


def koppel_mjp_acties():
    """De begrotingslijn achter elke MJP-code die in de besluiten staat. Draait ná
    delf_verwijzingen (dezelfde code-oogst, gedeelde cache) en vóór build. Zonder
    budgetstukken of bij een fout: netjes overslaan, de rest van de bouw gaat door."""
    try:
        run("parse_mjp_acties.py")
    except subprocess.CalledProcessError:
        print("  (overgeslagen: parse_mjp_acties.py faalde — besluiten blijven zonder budgetlijn.)")


def haal_zittingen():
    """De geplande zittingen van mechelen.be. Faalt dit (de stad wijzigt de opbouw van haar
    pagina), dan stopt de bouw NIET: het is één kaart op de site, geen kerngegeven. Zonder deze
    wikkel sloopte een cosmetische wijziging aan de bron de hele pijplijn, terwijl de veel
    belangrijkere fetch_uittreksels en straten_mechelen wél netjes zacht falen."""
    try:
        run("fetch_zittingen.py")
    except subprocess.CalledProcessError:
        print("  (overgeslagen: fetch_zittingen.py faalde — de kaart 'volgende zitting' "
              "valt terug op wat er al bekend is.)")


def main():
    # 1) ophalen (heeft internet nodig; draait op jouw machine, niet op lblod's tegenzin)
    run("fetch_all.py")

    # 1b) geplande zittingsdatums van de stadswebsite (voor de 'volgende zitting'-kaart)
    haal_zittingen()

    # 1c) schriftelijke vragen "buiten de gemeenteraad" (vragen van raadsleden aan de stad).
    #     Idempotent: lust de pagina's, downloadt enkel nieuwe pdf's en schrijft een bron-URL-
    #     index. De parse naar data.json gebeurt in stap 3a, de AI-tagging in 3c.
    run("fetch_schriftelijke_vragen.py")

    # 1d) individuele uittreksels/bijlagen per zitting ophalen (volledige besluit-tekst).
    #     Idempotent; de koppeling aan de besluiten gebeurt in stap 3b2, ná de assemblage.
    haal_uittreksels()

    # 1e) budgetstukken (meerjarenplannen, budgetwijzigingen, jaarrekeningen). Levert de tabel
    #     waarin staat wat een MJP-code uit een besluit werkelijk betekent; stap 3g leest die.
    haal_budgetten()

    # 2) tijdigheid + sessies uit de opgehaalde index
    run("maak_data.py")

    # 2b) schoon beginnen: demo eruit, volgende zitting + is_demo uit de echte data
    reset_voor_assemblage(BASE)

    # 3) elke zitting met een besluitenlijst uitlezen en invoegen
    for orgaan_map in sorted((BASE / "data" / "raw").glob("*")):
        naam = orgaan_map.name                       # bv. 'college_van_burgemeester_en_schepenen'
        for zitting in sorted(p for p in orgaan_map.glob("*") if p.is_dir()):
            besluiten = zitting / "besluiten.pdf"
            notulen = zitting / "notulen.pdf"
            agenda = zitting / "agenda.pdf"
            aanvullend = zitting / "aanvullend.pdf"

            if naam.startswith("burgemeester"):
                # De burgemeester beslist als zelfstandig orgaan en publiceert per
                # beslissingsdag een besluitenlijst → zelfde route als het college.
                if besluiten.exists():
                    run("parse_besluiten.py", besluiten)
                    run("assembleer_college.py", zitting.name, "besluiten.agendapunten.json",
                        "--orgaan", "Burgemeester")
                continue

            if naam.startswith("vast_bureau"):
                # Vast bureau = het uitvoerende orgaan van het OCMW, de tegenhanger van het
                # college. Publiceert enkel een besluitenlijst → net als het college assembleren,
                # maar met orgaan 'Vast bureau' zodat de twee uit elkaar te houden zijn.
                if besluiten.exists():
                    run("parse_besluiten.py", besluiten)
                    run("assembleer_college.py", zitting.name, "besluiten.agendapunten.json",
                        "--orgaan", "Vast bureau")
                continue

            if naam.startswith("college"):
                # College van burgemeester en schepenen (uitvoerend orgaan van de stad).
                if besluiten.exists():
                    run("parse_besluiten.py", besluiten)
                    run("assembleer_college.py", zitting.name, "besluiten.agendapunten.json",
                        "--orgaan", "College")
                continue

            # Gemeenteraad / raad voor maatschappelijk welzijn.
            orgaan = ("Raad voor maatschappelijk welzijn"
                      if naam.startswith("raad_voor_maatschappelijk") else "Gemeenteraad")

            if besluiten.exists():
                # Zitting is geweest → toon de beslissingen (vervangt een eventuele agenda).
                run("parse_besluiten.py", besluiten)
                if notulen.exists():
                    run("parse_notulen.py", notulen)
                    run("assembleer_agendapunten.py", zitting.name,
                        "besluiten.agendapunten.json", "notulen.notulen.json", "--orgaan", orgaan)
                else:
                    run("assembleer_agendapunten.py", zitting.name,
                        "besluiten.agendapunten.json", "--orgaan", orgaan)
            elif agenda.exists() or aanvullend.exists():
                # Zitting komt eraan → toon de agenda (sectie 02). Gewone + volledige agenda
                # worden samengevoegd; de volledige bevat ook de mondelinge vragen.
                agenda_jsons = []
                if agenda.exists():
                    run("parse_agenda.py", agenda); agenda_jsons.append("agenda.agenda.json")
                if aanvullend.exists():
                    run("parse_agenda.py", aanvullend); agenda_jsons.append("aanvullend.agenda.json")
                run("assembleer_agenda.py", zitting.name, *agenda_jsons, "--orgaan", orgaan)

    # 3a) Schriftelijke vragen "buiten de gemeenteraad" uitlezen (datum + vraag + antwoord
    #     uit de pdf) en in data.json gieten, vóór de AI-tagging zodat ze mee samengevat worden.
    run("parse_schriftelijke_vragen.py")

    # 3b) Volledige stratenlijst (optioneel, eenmalig — zie ensure_straten).
    ensure_straten()

    # 3b2) Uittreksels aan de besluiten koppelen + volledige tekst cachen. Ná de assemblage
    #      (die de besluiten vult) en vóór de tagging + zoekindex, die de koppeltabel lezen.
    koppel_uittreksels()

    # 3c) AI-verrijking (optioneel): samenvattingen + thema's. Alleen met een sleutel.
    verrijk_optioneel()

    # 3d) E-mailadressen uit de tekstvelden redacteren. Bewust ná de tagging: de tagging-cache
    #     blijft zo gesleuteld op de ruwe brontekst, enkel het gepubliceerde data.json schoont op.
    run("schoon_brontekst.py")

    # 3e) Volledige-tekstindex voor de zoekbalk (woord → punt-id's, zonder documentkopieën).
    #     Ná schoon_brontekst (leest het geschoonde data.json) en vóór build (die kopieert
    #     zoekindex.json mee naar dist/). Pdf-extractie is gecachet; een gewone run kost seconden.
    run("bouw_zoekindex.py")

    # 3f) Harde verwijzingen delven (MJP-actiecodes, zaaknummers, OMV-nummers): voedt de
    #     "verwante dossiers" in het dossierpaneel. build.py voegt verwijzingen.json in de site.
    run("delf_verwijzingen.py")

    # 3g) De begrotingslijn achter elke MJP-code: doelstelling, actie, dienst en de bedragen
    #     per jaar uit het meerjarenplan. Ná 3f (zelfde code-oogst) en vóór build.
    koppel_mjp_acties()

    # 4) bouwen
    run("build.py")

    # 5) opkuis van de tussenbestanden die parse_* in de root liet staan.
    opkuis()
    print("\n✓ Klaar. Open dist/index.html — of upload dat ene bestand naar je webhost.")


if __name__ == "__main__":
    main()
