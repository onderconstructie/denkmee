"""
schoon_brontekst.py — redacteert e-mailadressen en geboortedatums uit de gepubliceerde tekstvelden.

Officiële diensten- en kabinetsadressen duiken soms op in de brontekst van besluiten en in
de vraag/het antwoord van schriftelijke vragen. Ze horen niet op de publieke site (of in de
meegecommite data.json) thuis, dus we vervangen elk adres door de tekst '[e-mailadres]'.

Draait als LAATSTE databewerking, ná de AI-tagging. Zo blijft de tagging-cache gesleuteld op
de RUWE brontekst (die elke run identiek uit de pdf's komt), terwijl enkel het gepubliceerde
data.json geschoond wordt. De getoonde samenvatting (decoded) bevat normaal geen adressen,
maar we schonen ze voor de zekerheid mee.

Draai:  python schoon_brontekst.py
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

import re
import json
from pathlib import Path

BASE = Path(__file__).parent
DATA = BASE / "data.json"

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# --- Geboortedatums -------------------------------------------------------------------------
# Kandidatenlijsten (politieraad, bijzonder comité) staan in de notulen als een tabel
# "Kandidaat | Naam | Geboortedatum | Beroep", met de datum als dd/mm/jj. Die lijst is openbaar,
# maar naam + exacte geboortedatum is nu net de combinatie die je niet vlot doorzoekbaar wil
# maken. We houden het geboortejaar (dat zegt journalistiek iets over de leeftijd) en maskeren
# dag en maand.
#
# De vervanging is bewust op TWEE manieren ingeperkt, want dd/mm/jj is een gevaarlijk patroon:
#  1. Enkel in stukken die de tabelKOP dragen. In alle andere stukken betekent dd/mm/jj iets
#     heel anders (datum van eedaflegging, datum van ontvangst, einddatum van een mandaat).
#  2. Niet als er cijfers of een schuine streep tegenaan staan. Budgetcodes als
#     "2026/7450100/08/89/01" bevatten anders een schijnbare datum en zouden stukgaan.
# --- Rekeningnummers ------------------------------------------------------------------------
# Besluiten over domiciliëringen, borgstellingen en subsidies noemen soms een rekeningnummer
# voluit. Het gaat om rekeningen uit openbare stukken (meestal die van de stad zelf, soms van
# een vzw), dus het is geen geheim; maar op deze site wordt het wél doorzoekbaar naast 5.000
# andere stukken, en dat is een ander soort openbaarheid dan één pdf op het stadsportaal.
# We maskeren het nummer en laten de rest van de zin staan: de lezer ziet dát er een rekening
# in het besluit staat, en het bronstuk blijft één klik ver.
#
# Twee vormen, allebei streng afgebakend zodat gewone cijferreeksen (budgetcodes, MJP-codes,
# bedragen) niet per ongeluk sneuvelen:
#  1. IBAN: twee hoofdletters + twee controlecijfers, daarna 3 tot 7 groepen van vier cijfers.
#  2. De oude Belgische vorm 999-9999999-99.
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ .]?\d{4}){3,7}\b")
OUD_REK = re.compile(r"\b\d{3}-\d{7}-\d{2}\b")
REK_MASKER = "[rekeningnummer]"

GEB_KOP = re.compile(r"geboortedatum\s+beroep", re.I)
GEB_DATUM = re.compile(r"(?<![\d/])(\d{2})/(\d{2})/(\d{2})(?![\d/])")
GEB_MASKER = r"··/··/\3"

# --- Namen van gewone burgers ---------------------------------------------------------------
# Besluiten noemen soms een natuurlijke persoon bij naam: de eigenaars van een woning, wie een
# perceel koopt, wie bezwaar aantekent, wie een vergunning aanvraagt. Die stukken zijn openbaar,
# maar hier worden ze doorzoekbaar naast 5.000 andere, en dat is een ander soort openbaarheid
# dan één pdf op het stadsportaal. De naam voegt journalistiek ook niets toe: het dossier gaat
# over de plek, niet over de persoon. Het ADRES blijft dus staan (dat ís de zaak), de naam niet.
# Idem voor de landmeter-experts die een schattingsverslag tekenen: hun kantoor blijft staan,
# hun naam hoeft er niet bij.
#
# Wie NIET gemaskeerd wordt, en dat is bewust: politieke mandatarissen (nooit), ambtenaren in
# functie, bedrijven, en wie namens een organisatie optreedt. Die staan er in hun publieke rol.
#
# Dit kan niet met een patroon: geen enkele regex ziet het verschil tussen een raadslid en een
# buurtbewoner. Het is dus een gecureerde lijst, en onderaan staat een waakhond die nieuwe
# kandidaten meldt zodat de lijst niet stilletjes veroudert.
NAAM_MASKER = "[naam]"

# De namen staan NIET in dit bestand. Deze repo is publiek, en een lijst van burgers met de
# reden erbij ("eigenaar van een woning", "tekende bezwaar aan") zou hier een keurig register
# van privesituaties van maken. Precies wat we op de site wilden vermijden, maar dan
# leesbaarder. Ze staan dus in privacy_namen.json, dat in .gitignore staat.
#
# Ontbreekt dat bestand, dan maskeert deze stap niets. Voor wie de pijplijn overneemt voor zijn
# eigen stad is dat het juiste gedrag: die begint met een lege lijst. Voor deze site zou het een
# stille terugval zijn, en daarom staat er verderop een harde stop die dat geval herkent.
PRIVACY_BESTAND = BASE / "privacy_namen.json"


def _lees_privacylijst():
    if not PRIVACY_BESTAND.exists():
        return (), (), ()
    rauw = json.loads(PRIVACY_BESTAND.read_text(encoding="utf-8"))
    volledig = tuple(r["naam"] for r in rauw.get("volledig", []) if r.get("naam"))
    return volledig, tuple(rauw.get("achternaam", [])), tuple(rauw.get("beroep_achternaam", []))


PRIVE_VOLLEDIG, _ACHTERNAAM_UIT_BESTAND, _BEROEP_UIT_BESTAND = _lees_privacylijst()


def _omgekeerd(naam):
    """'Voornaam Achternaam' -> 'Achternaam Voornaam'.

    Officiële stukken schrijven de naam geregeld achternaam-eerst ("Contactpersoon Janssens
    Jan, Straat en nummer ..."). Op die vorm sloeg de lijst hierboven niet aan, en
    dan bleef de naam een werkende zoekterm terwijl hij overal elders gemaskeerd was. We leiden
    de omgekeerde vorm af in plaats van hem in te tikken, zodat een nieuwe naam op de lijst er
    vanzelf tegen beschermd is.
    """
    delen = naam.split()
    return " ".join(delen[1:] + delen[:1]) if len(delen) > 1 else naam


# Beide schrijfwijzen, zonder dubbels en zonder handwerk.
PRIVE_VORMEN = tuple(dict.fromkeys(list(PRIVE_VOLLEDIG) + [_omgekeerd(n) for n in PRIVE_VOLLEDIG]))

# Losse achternamen: ENKEL maskeren in een stuk dat ook de volledige naam draagt. Dat is nodig,
# want sommige achternamen op de lijst zijn gewone Vlaamse namen, en er lopen naamgenoten door
# de data (een vertegenwoordigster van UNIZO, een oud-raadslid) die er gewoon horen te staan.
PRIVE_ACHTERNAAM = _ACHTERNAAM_UIT_BESTAND

# Achternaam met het beroep ervoor. Die combinatie is wél eenduidig, ook zonder voornaam:
# "het opmetingsplan van landmeter <achternaam>", "akkoord met aangepast plan landmeter <achternaam>".
BEROEP_NAAM = re.compile(
    r"\b(landmeter(?:-expert)?)\s+(?:%s)\b"
    % "|".join(re.escape(n) for n in _BEROEP_UIT_BESTAND)) if _BEROEP_UIT_BESTAND else None
# Velden die op de site terechtkomen (zoekindex of zichtbaar): brontekst wordt doorzocht;
# vraag/antwoord staan in de dossier-uitklap; decoded is de samenvatting; titel de kop.
VELDEN = ("brontekst", "decoded", "vraag", "antwoord", "titel")
LIJSTEN = ("agendapunten", "college_beslissingen", "schriftelijke_vragen")


# --- Personeelsgids in het arbeidsreglement --------------------------------------------------
# Het arbeidsreglement draagt een interne telefoongids met zich mee: de vertrouwenspersonen met
# hun rechtstreekse nummer en e-mailadres, de arbeidsarts, de preventieadviseurs, en per
# stadsgebouw de lijst van bedrijfshulpverleners. Tientallen medewerkers, met naam en werkplek,
# en die namen werden zoektermen. Een zoekbalk over besluitvorming hoort geen personeelsregister
# te zijn, en deze mensen staan er niet omdat ze iets beslissen maar omdat ze een verbanddoos
# beheren of een vertrouwelijk gesprek voeren.
#
# We knippen de passage weg en laten de ONDERWERPEN staan: wie op 'EHBO', 'vertrouwenspersoon'
# of 'arbeidsreglement' zoekt, vindt het stuk nog altijd. Aangehaakt op de koptekst en op het
# begin van het volgende DEEL, niet op het artikelnummer: dat nummer verschuift bij een nieuw
# reglement, de kop niet.
PERSONEELSGIDS = re.compile(
    r"Artikel\s+\d+\.\s*Aanspreekpunten\s*-\s*contactpersonen.*?(?=DEEL\s+\d)", re.S)
PERSONEELSGIDS_TEKST = (
    "Aanspreekpunten, contactpersonen en EHBO. In elk gebouw is een verbanddoos beschikbaar. "
    "[Namen en contactgegevens van de vertrouwenspersonen, de arbeidsarts, de preventieadviseurs "
    "en de bedrijfshulpverleners: weggelaten.] ")


def maskeer_namen(tekst, met_achternamen=None):
    """Maskeert de gecureerde namen in één tekst. Geeft (nieuwe tekst, aantal) terug.

    met_achternamen=None laat de functie zelf kijken of de volledige naam in DEZE tekst staat.
    Bij data.json geven we het antwoord mee vanaf het hele stuk: daar staat de volledige naam
    soms in de brontekst en enkel de achternaam in de samenvatting.

    Wordt door twee stappen gebruikt: schoon_brontekst.py voor data.json, en bouw_zoekindex.py
    voor de volledige notulen- en uittrekseltekst die uit de pdf's komt. Die tweede weg loopt
    NIET via data.json, dus zonder deze gedeelde functie bleef een gemaskeerde naam gewoon een
    zoekterm in de index. Precies het lek dat we wilden dichten.

    Lang naar kort, anders blijft de voornaam los staan voor het masker. De achternaam gaat
    alleen mee als de volledige naam in dezelfde tekst staat, zodat naamgenoten ongemoeid
    blijven: dezelfde achternaam hoort geregeld ook toe aan een mandataris of een
    organisatievertegenwoordiger, en die horen er gewoon te staan.
    """
    if not tekst:
        return tekst, 0
    tekst, gids = PERSONEELSGIDS.subn(PERSONEELSGIDS_TEKST, tekst)
    if met_achternamen is None:
        met_achternamen = any(v in tekst for v in PRIVE_VORMEN)
    vormen = sorted(PRIVE_VORMEN, key=len, reverse=True)
    if met_achternamen:
        vormen = vormen + sorted(PRIVE_ACHTERNAAM, key=len, reverse=True)
    aantal = 0
    for vorm in vormen:
        tekst, n = re.subn(r"\b%s\b" % re.escape(vorm), NAAM_MASKER, tekst)
        aantal += n
    tekst, n = BEROEP_NAAM.subn(r"\1 " + NAAM_MASKER, tekst)
    return tekst, aantal + n + gids


def tekst_van(item):
    """Alle doorzoekbare tekst van één stuk aan elkaar, om er patronen op te toetsen."""
    delen = []
    for veld in VELDEN + ("kernbegrippen",):
        waarde = item.get(veld)
        if isinstance(waarde, str):
            delen.append(waarde)
        elif isinstance(waarde, list):
            delen.extend(x for x in waarde if isinstance(x, str))
    return " \n".join(delen)


# --- Waakhond -------------------------------------------------------------------------------
# De lijst hierboven is met de hand samengesteld en veroudert dus vanzelf: elke maand komen er
# nieuwe stukken bij. Deze waakhond zoekt de zinsbouw waarin een natuurlijke persoon opduikt
# (iemand dient een aanvraag in, bezit, koopt, huurt, woont, tekent bezwaar aan) en meldt elke
# naam die noch gemaskeerd wordt, noch als publiek bekend staat.
#
# Hij BLOKKEERT niet. De meeste namen in deze data zijn mandatarissen, en die horen er te staan;
# een harde stop zou de pijplijn maandelijks tegenhouden op een terechte vermelding. Hij meldt,
# jij beoordeelt, en wat een gewone burger blijkt te zijn gaat in PRIVE_VOLLEDIG.
_DEEL = r"[A-ZÉÈÖ][a-zéèëïöüç'’\-]+"
_TUSSEN = r"(?:van|de|den|der|het|ter|te|Van|De|Den|Der|Vander|Vanden)"
_NAAM = rf"(?:{_DEEL}|{_TUSSEN})(?:\s+(?:{_DEEL}|{_TUSSEN})){{1,3}}"
_AANHEF = r"(?i:(?:de\s+heer|mevrouw|mevr\.|dhr\.))\s+"
ROLZINNEN = [re.compile(p) for p in (
    rf"(?i:(?:aanvraag\s+)?(?:ingediend|aangevraagd|ingevuld|opgemaakt)\s+door)\s+(?:{_AANHEF})?({_NAAM})",
    rf"(?i:(?:eigendom|eigenaar|eigenaars)\s+(?:van\s+)?)(?:{_AANHEF})?({_NAAM})",
    rf"(?i:(?:verkocht|verkoop|verhuurd|toegewezen|overgedragen)\s+aan)\s+(?:{_AANHEF})?({_NAAM})",
    rf"(?i:(?:koper|kopers|huurder|huurders|pachter|erfpachtnemer)\s*(?:is|zijn|:)?)\s+(?:{_AANHEF})?({_NAAM})",
    rf"({_NAAM}),?\s+(?i:(?:wonend|woonachtig))",
    rf"(?i:bezwaar\s+(?:van|door|ingediend\s+door))\s+(?:{_AANHEF})?({_NAAM})",
    rf"(?i:(?:erfgenaam|erfgenamen|nalatenschap\s+van))\s+(?:{_AANHEF})?({_NAAM})",
    rf"(?i:contactpersoon\s*(?:is|:)?)\s+(?:{_AANHEF})?({_NAAM})",
    rf"{_AANHEF}({_NAAM})",
)]
# Woorden die verraden dat het geen natuurlijke persoon is.
GEEN_PERSOON = re.compile(
    r"\b(vzw|nv|bv|bvba|cvba|cv|vereniging|stad|gemeente|ocmw|agb|provincie|vlaamse|vlaams|"
    r"gewest|agentschap|college|raad|bureau|comite|comité|fractie|maatschappij|groep|bank|"
    r"school|kerk|kerkbestuur|museum|team|dienst|zone|politie|intercommunale|waterweg|erfgoed|"
    r"woonmaatschappij|woonkade|woonstroom|woonland|investissement|syntra|officenter|keerdok|"
    r"fluvius|infrabel|theatrium|regie|bestemming|gouverneur)\b", re.I)
# Een straatnaam is geen persoon. 'van Leestsesteenweg' en 'de Zelestraat' haalden de vorige
# versie van deze waakhond nog wél, en dat was de grootste bron van ruis.
STRAATNAAM = re.compile(r"(?:straat|laan|weg|dreef|plein|kaai|baan|vest|lei|markt|hof|pad)$", re.I)
# Een bedrijfsvorm vlak achter de naam: 'Emiel De Coninck NV', 'Keerdok Mechelen VVZRL'.
BEDRIJFSVORM = re.compile(r"^\s*(NV|BV|BVBA|CVBA|CV|VZW|VVZRL|Comm\.\s?V|SA|BVBA)\b")

# Eenmalig beoordeeld en publiek bevonden: mandatarissen buiten de roster, ambtenaren in functie,
# en wie namens een organisatie optreedt. Zonder deze lijst meldt de waakhond ze elke run opnieuw
# en leer je hem negeren, en dan is hij niets meer waard.
BEOORDEELD_PUBLIEK = (
    "Bart Somers", "Kim Brooks", "Dries Devillé", "Luc Geysels", "Luc Hilderson",
    "Danny Huygelen", "Nick Vrijdag", "Bram Van der Auwera", "Sergio Zearo",
    "Koen Anciaux",            # oud-voorzitter OCMW, eretitel
    "Erik Laga",               # algemeen directeur
    "Rik Schaerlaecken", "Henri Schaerlaecken",   # financieel beheerder
    "Nick Hermans",            # schatter-onderhandelaar bij de Vlaamse overheid
    "Bart De Pauw",            # namens welzijnsvereniging Audio
    "Ignace De Paepe",         # gedelegeerd bestuurder MG Real Estate
    "Bregt Brosens",           # teamcoördinator NMBS Railway Heritage
    "Emiel De Coninck", "Officenter Mechelen", "Keerdok Mechelen",   # bedrijven
)


def waakhond(data):
    """Meldt persoonsnamen die nog niet beoordeeld zijn. Wijzigt niets."""
    stukken = [tekst_van(it) for lijst in LIJSTEN for it in data.get(lijst, [])]

    # De publieke namen komen uit de data zelf: de college- en fractieroster, plus de
    # verkozenenlijsten in de notulen. Niet ingetikt, dus ze groeien mee met een nieuwe legislatuur.
    publiek = {lid["name"].strip() for sleutel in ("college", "fracties")
               for lid in data.get(sleutel, []) if lid.get("name")}
    ruw = re.compile(_NAAM)
    for tekst in stukken:
        if re.search(r"naam\s+verkozene|verkozen\s+verklaard", tekst, re.I):
            publiek.update(" ".join(m.group(0).split()) for m in ruw.finditer(tekst))

    gezien = {}
    for tekst in stukken:
        for patroon in ROLZINNEN:
            for m in patroon.finditer(tekst):
                naam = " ".join(m.group(1).split()).strip(" ,.;:")
                delen = naam.split()
                if len(delen) < 2 or NAAM_MASKER in naam:
                    continue
                # Een persoonsnaam begint hier nooit met een klein tussenvoegsel: 'van
                # Bleukensstraat' en 'de Gouverneur' zijn geen mensen.
                if delen[0] in ("van", "de", "den", "der", "het", "ter", "te"):
                    continue
                # Een naam eindigt evenmin op een tussenvoegsel. Het patroon kapt soms te ruim
                # af ('Over te', 'Luc Geysels de'); dat is een zin, geen persoon.
                if delen[-1] in ("van", "de", "den", "der", "het", "ter", "te"):
                    continue
                if any(STRAATNAAM.search(d) for d in delen):
                    continue
                if naam in publiek or GEEN_PERSOON.search(naam):
                    continue
                if BEDRIJFSVORM.match(tekst[m.end(1):m.end(1) + 16]):
                    continue
                if any(v in naam or naam in v for v in PRIVE_VORMEN):
                    continue
                # Ook de afgekapte varianten die het patroon oplevert ('Luc Geysels Lid',
                # 'Bram Van de') horen bij een naam die al beoordeeld is.
                if any(naam.startswith(p) or p.startswith(naam) for p in BEOORDEELD_PUBLIEK):
                    continue
                i = m.start(1)
                gezien.setdefault(naam, " ".join(tekst[max(0, i - 120):i + 120].split()))

    if not gezien:
        print("Waakhond: geen nieuwe persoonsnamen om te beoordelen.")
        return
    print(f"Waakhond: {len(gezien)} naam/namen nog niet beoordeeld. Nakijken, en wie een gewone")
    print("          burger is toevoegen aan PRIVE_VOLLEDIG in dit bestand:")
    for naam, context in sorted(gezien.items()):
        print(f"   - {naam}")
        print(f"     …{context[:150]}…")


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))

    # Veiligheidsklep. Ontbreekt de privacylijst terwijl er in een vorige run wél gemaskeerd is,
    # dan is het bestand kwijt, niet leeg bedoeld. Zonder deze stop zou een volgende publicatie
    # de namen stilzwijgend terugzetten: schoon_brontekst zou niets vinden om te maskeren, en de
    # waakhond zou ze als 'nieuwe kandidaten' melden in een uitvoer die niemand meer leest.
    if not PRIVE_VOLLEDIG and NAAM_MASKER in DATA.read_text(encoding="utf-8"):
        sys.exit("[STOP] %s ontbreekt, maar data.json bevat al '%s'.\n"
                 "       De lijst is dus kwijt en niet leeg bedoeld. Zet het bestand terug\n"
                 "       (het staat bewust niet in git) of maak het leeg als je echt niets\n"
                 "       meer wil maskeren." % (PRIVACY_BESTAND.name, NAAM_MASKER))
    geredacteerd = 0

    # 1) Lopende tekst (zoekindex + zichtbare velden): adres → '[e-mailadres]'.
    #    Ook lijsten van strings, want 'kernbegrippen' is er zo één: vrije termen die het model
    #    letterlijk uit de bron overneemt. Sloegen we die over, dan blokkeerde de klep in build.py
    #    terecht de publicatie, maar loste een nieuwe run van dít script het niet op, en dan wees
    #    de STOP-boodschap ("draai schoon_brontekst.py opnieuw") de verkeerde kant op.
    for lijst in LIJSTEN:
        for item in data.get(lijst, []):
            for veld in VELDEN + ("kernbegrippen",):
                waarde = item.get(veld)
                if isinstance(waarde, str) and EMAIL.search(waarde):
                    item[veld] = EMAIL.sub("[e-mailadres]", waarde)
                    geredacteerd += 1
                elif isinstance(waarde, list):
                    for i, deel in enumerate(waarde):
                        if isinstance(deel, str) and EMAIL.search(deel):
                            waarde[i] = EMAIL.sub("[e-mailadres]", deel)
                            geredacteerd += 1

    # 1b) Geboortedatums in kandidatentabellen: dag en maand maskeren, jaar behouden.
    datums = 0
    stukken = 0
    for lijst in LIJSTEN:
        for item in data.get(lijst, []):
            if not any(GEB_KOP.search(str(item.get(v) or "")) for v in VELDEN):
                continue
            geraakt = False
            for veld in VELDEN:
                waarde = item.get(veld)
                if not isinstance(waarde, str):
                    continue
                nieuw, n = GEB_DATUM.subn(GEB_MASKER, waarde)
                if n:
                    item[veld] = nieuw
                    datums += n
                    geraakt = True
            stukken += geraakt

    # 1c) Rekeningnummers maskeren in alle zichtbare en doorzoekbare velden.
    rekeningen = 0
    for lijst in LIJSTEN:
        for item in data.get(lijst, []):
            for veld in VELDEN + ("kernbegrippen",):
                waarde = item.get(veld)
                if isinstance(waarde, str):
                    nieuw, n1 = IBAN.subn(REK_MASKER, waarde)
                    nieuw, n2 = OUD_REK.subn(REK_MASKER, nieuw)
                    if n1 or n2:
                        item[veld] = nieuw
                        rekeningen += n1 + n2
                elif isinstance(waarde, list):
                    for i, deel in enumerate(waarde):
                        if isinstance(deel, str):
                            nieuw, n1 = IBAN.subn(REK_MASKER, deel)
                            nieuw, n2 = OUD_REK.subn(REK_MASKER, nieuw)
                            if n1 or n2:
                                waarde[i] = nieuw
                                rekeningen += n1 + n2

    # 1d) Namen van gewone burgers en van landmeter-experts maskeren.
    namen = 0
    for lijst in LIJSTEN:
        for item in data.get(lijst, []):
            # De losse achternaam alleen loslaten op een stuk dat óók de volledige naam draagt.
            # Die vraag stellen we op het HELE stuk, niet per veld: de volledige naam staat vaak
            # in de brontekst terwijl de samenvatting enkel de achternaam gebruikt.
            heel = tekst_van(item)
            met_achter = any(v in heel for v in PRIVE_VORMEN)
            for veld in VELDEN + ("kernbegrippen",):
                waarde = item.get(veld)
                if isinstance(waarde, str):
                    nieuw, n = maskeer_namen(waarde, met_achter)
                    if n:
                        item[veld] = nieuw
                        namen += n
                elif isinstance(waarde, list):
                    schoon = []
                    for deel in waarde:
                        if not isinstance(deel, str):
                            schoon.append(deel)
                            continue
                        nieuw, n = maskeer_namen(deel, met_achter)
                        namen += n
                        # Een kernbegrip dat enkel nog het masker is, is een zoekfacet zonder
                        # betekenis. De tagger nam de namen letterlijk over als kernbegrip.
                        if nieuw.strip() != NAAM_MASKER:
                            schoon.append(nieuw)
                    item[veld] = schoon

    # 2) Gestructureerde 'email'-velden van de college- en fractieroster. Die roster wordt
    #    niet meer getoond op de site, dus de contactadressen van schepenen/fracties horen
    #    niet in de publieke data. Leegmaken (apart veld, geen lopende tekst om te redacteren).
    leeggemaakt = 0
    for sleutel in ("college", "fracties"):
        for lid in data.get(sleutel, []):
            if lid.get("email"):
                lid["email"] = ""
                leeggemaakt += 1

    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"E-mailadressen geredacteerd in {geredacteerd} tekstveld(en); "
          f"{leeggemaakt} contactveld(en) van de roster leeggemaakt.")
    print(f"Geboortedatums gemaskeerd: {datums} in {stukken} stuk(ken) met een kandidatentabel.")
    print(f"Rekeningnummers gemaskeerd: {rekeningen}.")
    print(f"Namen van burgers/landmeters gemaskeerd: {namen} vermelding(en), "
          f"{len(PRIVE_VOLLEDIG)} personen op de lijst.")
    waakhond(data)


if __name__ == "__main__":
    main()
