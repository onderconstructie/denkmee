"""
schoon_brontekst.py — redacteert e-mailadressen en geboortedatums uit de gepubliceerde tekstvelden.

Officiële diensten- en kabinetsadressen duiken soms op in de brontekst van besluiten en in
de vraag/het antwoord van schriftelijke vragen. Ze horen niet op de publieke site (of in de
meegecommite data.json) thuis, dus we vervangen elk adres door de tekst '[e-mailadres]'.

Draait als LAATSTE databewerking, ná de AI-tagging. Zo blijft de tagging-cache gesleuteld op
de RUWE brontekst (die elke run identiek uit de pdf's komt), terwijl enkel het gepubliceerde
data.json geschoond wordt. De getoonde samenvatting (decoded) bevat normaal geen adressen,
maar we schonen ze voor de zekerheid mee.

Draai:  python pijplijn/schoon_brontekst.py
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

import re
import json
from pathlib import Path

BASE = Path(__file__).parent.parent   # de repomap; dit script staat in pijplijn/
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

# --- Context die een persoon herleidbaar maakt -----------------------------------------------
# Een gemaskeerde naam beschermt niemand als de zin ernaast zegt waar die persoon woont of
# wanneer hij geboren is. Nagemeten op 14/09/2026 stonden er twee zulke zinnen live
# ("Contactpersoon is [naam], wonend op ... 23, 3140 ...") en zat in de zoekcache een kind
# met naam, "°dd-mm-jjjj" en woonadres. De geboortedatumklep hierboven zag dat niet: die kijkt
# alleen naar dd/mm/jj ná de tabelkop "Geboortedatum Beroep".
#
# 1. Een geboortedatum met het graadteken ("°22-03-2021") of na "geboren (op)". Dat teken staat in
#    deze stukken enkel voor een geboortedatum, dus een tabelkop als anker is hier niet nodig.
#    Dag en maand weg, het jaar blijft, net als bij de kandidatentabellen.
GEB_TEKEN = re.compile(
    r"(°\s?|\bgeboren\s+(?:op\s+)?)(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})(?!\d)", re.I)
GEB_TEKEN_MASKER = r"\1··-··-\4"

# 2. Een WOONadres. Het werkwoord is het anker: wonend, wonende, woonachtig, gedomicilieerd.
#    Zonder dat woord blijft een adres gewoon staan, want dan gaat het over de plek zelf (een
#    perceel, een vergunning, een bedrijf op zijn zetel), en dat is precies wat het dossier moet
#    tonen. Het werkwoord blijft staan zodat de zin leesbaar blijft. Let op: GEEN re.IGNORECASE op
#    het geheel, want dan wordt [A-Z] gelijk aan [a-z] en eet de regel lopende tekst op; alleen
#    het werkwoord zelf is hoofdletterongevoelig. De gemeente achteraan gaat enkel mee na een
#    komma, "te", "in" of een postcode, anders slokt de regel het volgende zinsbegin op.
_ADRES_WOORD = r"[\w'’À-ÿ-]"
ADRES_NA_WOON = re.compile(
    r"(\b(?i:wonend(?:e)?|woonachtig|gedomicilieerd)\s+(?:(?i:op|te|in|aan)\s+)?)"
    r"(?:[A-Z]" + _ADRES_WOORD + r"*\.?\s+){0,3}?" + _ADRES_WOORD + r"*?"
    r"(?:straat|laan|weg|dreef|steenweg|vest|plein|kaai|lei|baan|markt|hof|pad|singel|dijk|berg|veld)"
    # Het huisnummer mag één hoofdletter dragen (23A), maar alleen als die letter NIET aan een
    # woord vastzit. Eerst stond hier \s?[A-Za-z]?, en de zelftest toonde wat dat doet: de regel
    # greep de eerste letter van het volgende woord mee, zodat "54 in Proefdorp" eindigde als
    # "[adres]n Proefdorp" en "5 De aanvraag" als "[adres]e aanvraag".
    r"\s+\d+(?:\s?[A-Z](?![A-Za-zÀ-ÿ]))?(?:\s*(?:bus|b\.?)\s*\d+)?"
    r"(?:(?:\s*,\s*|\s+(?:te|in)\s+|\s+)\d{4}\s+[A-Z]" + _ADRES_WOORD + r"+"
    r"|(?:\s*,\s*|\s+(?:te|in)\s+)[A-Z]" + _ADRES_WOORD + r"+)?")
ADRES_MASKER = r"\1[adres]"

# 3. Een adres DIRECT na een gemaskeerde naam: "De heer [naam], Proefstraat 54 in 2800 Mechelen".
#    Geen werkwoord, gewoon een komma of een spatie. Het anker is het masker zelf: maskeer_namen
#    heeft er al "[naam]" van gemaakt, dus wat erachter staat is per definitie het adres van die
#    persoon. Nagemeten op 14/09/2026: 1 keer in data.json en 13 keer in de tekst die de zoekindex
#    voedt ("[naam] Voorbeeldstraat 46 2800 Mechelen"). Draai maskeer_namen dus EERST.
_STRAAT_NR = (r"(?:[A-Z]" + _ADRES_WOORD + r"*\.?\s+){0,3}?" + _ADRES_WOORD + r"*?"
              r"(?:straat|laan|weg|dreef|steenweg|vest|plein|kaai|lei|baan|markt|hof|pad|singel|dijk|berg|veld)"
              r"\s+\d+(?:\s?[A-Z](?![A-Za-zÀ-ÿ]))?(?:\s*(?:bus|b\.?)\s*\d+)?")
_GEMEENTE = (r"(?:(?:\s*,\s*|\s+(?:te|in)\s+|\s+)\d{4}\s+[A-Z]" + _ADRES_WOORD + r"+"
             r"|(?:\s*,\s*|\s+(?:te|in)\s+)[A-Z]" + _ADRES_WOORD + r"+)?")
ADRES_NA_NAAM = re.compile(r"(\[naam\],?\s+)" + _STRAAT_NR + _GEMEENTE)

# 4. Een contactformulier met veldlabels in plaats van een zin, zoals in de budgetten van de
#    kerkfabrieken: "Contactpersoon <naam>", dan "Straat en nummer", "Postcode en gemeente",
#    "Telefoon" en "Email". Alleen BINNEN zo'n blok, dus in de regels vlak na een regel die met
#    "Contactpersoon" begint: elders zijn dezelfde labels het adres van een organisatie, en dat
#    mag blijven.
FORM_BLOK = re.compile(r"(?im)^(?:naam\s+)?contactpersoon\b[^\n]*(?:\n[^\n]*){0,8}")
FORM_VELD = re.compile(
    r"(?im)^((?:straat en nummer|postcode en gemeente|telefoon|tel\.?|gsm|e-?mail)\s*:?\s+)\S[^\n]*")

# 5. Een verhuld e-mailadres. De gewone regel eist een punt voor het domein, en in de stukken
#    staat soms een komma ("...@voorbeeld,be") of "[at]". Nagemeten op 14/09/2026 werd zo de voor- en
#    achternaam in het lokale deel van het adres een ZOEKTERM, terwijl diezelfde naam overal
#    elders gemaskeerd was. De naammaskering werd dus volledig omzeild.
#    Geen spatie na de punt of komma voor het domeinachtervoegsel, en dat achtervoegsel is kort.
#    Een eerste versie liet die spatie toe, en de droogtest toonde meteen wat dat kost: de
#    organisatie "vzw J@M." gevolgd door "Aktename" las als een e-mailadres, in 19 titels van
#    collegebesluiten. Een echt verhuld adres ("...@voorbeeld,be") heeft die spatie niet.
EMAIL_VERHULD = re.compile(
    r"[A-Za-z0-9._%+-]+(?:@|\s?\[at\]\s?|\s?\(at\)\s?)[A-Za-z0-9-]+(?:[.,][A-Za-z0-9-]+)*"
    r"[.,][A-Za-z]{2,4}(?![A-Za-z])")


def maskeer_context(tekst):
    """Haalt weg wat een persoon herleidbaar maakt, ook als de naam al gemaskeerd is. Geeft
    (nieuwe tekst, aantal adres- en contactgegevens, aantal geboortedatums) terug:

      - een woonadres na 'wonend/woonachtig/gedomicilieerd'
      - een adres direct na '[naam]'
      - de adres-, telefoon- en mailvelden in een contactformulier
      - een verhuld e-mailadres (komma of [at] in plaats van punt of @)
      - een geboortedatum na '°' of 'geboren'

    Gedeeld met bouw_zoekindex.py, om dezelfde reden als maskeer_namen: de volledige tekst uit de
    pdf's loopt daar niet langs data.json, dus zonder deze gedeelde functie bleef het een zoekterm.
    Draai maskeer_namen EERST, want de tweede regel steunt op het masker '[naam]'."""
    if not tekst:
        return tekst, 0, 0
    tekst, a1 = ADRES_NA_WOON.subn(ADRES_MASKER, tekst)
    tekst, a2 = ADRES_NA_NAAM.subn(r"\1[adres]", tekst)
    velden = [0]

    def _in_blok(m):
        nieuw, n = FORM_VELD.subn(r"\1[verwijderd]", m.group(0))
        velden[0] += n
        return nieuw

    tekst = FORM_BLOK.sub(_in_blok, tekst)
    tekst, a4 = EMAIL_VERHULD.subn("[e-mailadres]", tekst)
    tekst, g = GEB_TEKEN.subn(GEB_TEKEN_MASKER, tekst)
    return tekst, a1 + a2 + velden[0] + a4, g

# --- Namen van gewone burgers ---------------------------------------------------------------
# Besluiten noemen soms een natuurlijke persoon bij naam: de eigenaars van een woning, wie een
# perceel koopt, wie bezwaar aantekent, wie een vergunning aanvraagt. Die stukken zijn openbaar,
# maar hier worden ze doorzoekbaar naast 5.000 andere, en dat is een ander soort openbaarheid
# dan één pdf op het stadsportaal. De naam voegt journalistiek ook niets toe: het dossier gaat
# over de plek, niet over de persoon. Het ADRES van de plek blijft dus staan (dat ís de zaak), de
# naam niet. Een WOONadres dat de persoon zelf aanwijst gaat wél weg: zie ADRES_NA_WOON hierboven.
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
_PRIVACY_RUW = {}


def _lees_privacylijst():
    global _PRIVACY_RUW
    if not PRIVACY_BESTAND.exists():
        _PRIVACY_RUW = {}
        return (), (), ()
    rauw = _PRIVACY_RUW = json.loads(PRIVACY_BESTAND.read_text(encoding="utf-8"))
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


# --- Ledentabel van een adviesraad met burgers, en ondersteunende personeelsrollen -----------
# Een raadsbesluit dat de GECORO benoemt, zet elf deskundigen en zeven vertegenwoordigers van
# verenigingen bij naam in de tekst, elk met een plaatsvervanger. Dat zijn burgers: hun naam hoorde
# nooit op de site, en stond er op 16/09/2026 wel, tot in de samenvatting. De organisaties zelf
# (Natuurpunt, VOKA, UNIZO, Fietsersbond) blijven staan; die horen bij het besluit.
#
# Twee smalle regels, in de vorm van de personeelsgids-knip hierboven:
#  1. LEDENBLOK: binnen een ledenopsomming van zo'n commissie maskeren we de persoonsnamen. Het
#     blok is afgebakend met eigen ankers, zodat de rest van het besluit (stemmingen met
#     raadsleden, juridische grond) ongemoeid blijft.
#  2. ONDERSTEUNENDE ROL: de naam naast preventieadviseur, arbeidsarts, vertrouwenspersoon of
#     HR-manager. Dat is personeel in een ondersteunende functie, dezelfde categorie die de
#     personeelsgids-regel elders al wegknipt.
# Mandatarissen blijven staan: raadsleden, het college en de bevoegde schepenen staan in de witte
# lijst, en organen waar zij zetelen (bijzonder comité, raad van bestuur, algemene vergadering)
# vallen buiten deze regels.
ROL_WOORD = re.compile(
    r"(?i)\b(?:effectief lid|plaatsvervangend lid|plaatsvervanger|deskundige|vertegenwoordiger|"
    r"afgevaardigde|ondervoorzitter|voorzitter|secretaris|waarnemer|vervanger|lid namens)\b")
PRIVAAT_CONTEXT = re.compile(
    r"(?i)(gecoro|commissie voor ruimtelijke ordening|syndicaal overleg|syndicale afvaardiging|"
    r"overheidsdelegatie|onderhandelingscomite|onderhandelingscomité|basisoverlegcomite|basisoverlegcomité)")
LEDENBLOK = re.compile(
    r"(?is)(?:deskundigen\s*\(\d+\)\s*:|maatschappelijke geledingen\s+effectief lid\s+plaatsvervanger|"
    r"effectief lid\s+plaatsvervanger).{0,4000}?(?=\n\s*besluit\b|\n\s*artikel\s+1\b|\n\s*financi|\n\s*•|$)")
PERSOON_PAAR = re.compile(
    r"\b[A-ZÀ-Þ][a-zà-ÿ'’\-]{1,}(?:\s+(?:van|de|den|der|het|'t|Van|De|Den|Der|Vande|Vanden))?"
    r"\s+[A-ZÀ-Þ][a-zà-ÿ'’\-]{2,}\b")
# Woorden die verraden dat het om een orgaan, vakgebied, plaats of datum gaat, niet om een persoon.
GEEN_PERSOON = re.compile(
    r"(?i)\b(?:stad|gemeente|provincie|vlaams|vlaamse|agentschap|departement|dienst|vzw|bv|nv|cvba|"
    r"ocmw|politie|zone|school|hogeschool|academie|universiteit|kabinet|college|raad|bureau|comite|"
    r"comité|commissie|gecoro|fractie|maatschappij|groep|bedrijf|centrum|vereniging|verenigingen|"
    r"bond|punt|straat|laan|plein|weg|kaai|baan|park|huis|mechelen|brussel|antwerpen|leuven|"
    r"ruimtelijke|ordening|codex|decreet|beleid|reglement|overleg|kunstonderwijs|deeltijds|"
    r"januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)\b")
ONDERSTEUNENDE_ROL = re.compile(
    r"(?i)(\b(?:interne preventieadviseur|preventieadviseur|arbeidsarts|vertrouwensperso[a-z]*|"
    r"hr-manager)\b[^\w\n]{0,4})("
    r"[A-ZÀ-Þ][a-zà-ÿ'’\-]{1,}(?:\s+(?:van|de|den|der|het|'t|Van|De|Den|Der))?\s+[A-ZÀ-Þ][a-zà-ÿ'’\-]{2,})")
# De politieke waarnemers in zo'n commissie staan als "partij: naam" buiten de ledentabel. Ook zij
# zijn geen mandataris in dit besluit; enkel wie in de witte lijst staat, blijft staan.
PARTIJ_NAAM = re.compile(
    r"(?i)(\b(?:n-va|vooruit|cd&v|pvda|groen|open vld|vlaams belang|vld|sp\.a)\b[^\w\n]{0,14})("
    r"[A-ZÀ-Þ][a-zà-ÿ'’\-]{1,}(?:\s+(?:van|de|den|der|het|'t|Van|De|Den|Der|Vande|Vanden))?\s+[A-ZÀ-Þ][a-zà-ÿ'’\-]{2,})")


def _publieke_namen():
    """Mandatarissen en al beoordeelde publieke namen: die blijven staan."""
    namen = set()
    try:
        ruw = json.loads((BASE / "data" / "raadsleden.json").read_text(encoding="utf-8"))
        if isinstance(ruw, dict):
            namen |= {k.strip() for k in ruw}
    except Exception:
        pass
    namen |= {n.strip() for n in _PRIVACY_RUW.get("beoordeeld_publiek", []) if isinstance(n, str)}
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
        for lid in d.get("college", []):
            if isinstance(lid, dict) and lid.get("name"):
                namen.add(lid["name"].strip())
        for x in d.get("college_beslissingen", []) + d.get("agendapunten", []):
            s = x.get("schepen")
            if isinstance(s, str) and s.strip():
                namen.add(re.sub(r"(?i)^(?:schepen|burgemeester|voorzitter)\s+", "", s).strip())
    except Exception:
        pass
    namen |= {" ".join(reversed(n.split())) for n in list(namen) if len(n.split()) == 2}
    return {n for n in namen if len(n.split()) >= 2}


_PUBLIEK = None


def _maskeer_paren(blok):
    """Vervangt de persoonsnamen in één ledenblok. Geeft (blok, aantal)."""
    global _PUBLIEK
    if _PUBLIEK is None:
        _PUBLIEK = _publieke_namen()
    uit, laatst, aantal = [], 0, 0
    for m in PERSOON_PAAR.finditer(blok):
        naam = m.group(0)
        if naam in _PUBLIEK or GEEN_PERSOON.search(naam):
            continue
        uit.append(blok[laatst:m.start()]); uit.append(NAAM_MASKER); laatst = m.end(); aantal += 1
    uit.append(blok[laatst:])
    return "".join(uit), aantal


def maskeer_ledenlijst(tekst):
    """Maskeert namen in de ledenlijst van een adviesraad en naast een ondersteunende rol.

    Een benoemingsbesluit van zo'n raad IS een personenlijst: de namen staan in de tabel, maar ook
    bij de kandidaten, de voorgedragen voorzitter en de politieke waarnemers. Daarom maskeren we in
    zo'n stuk ELK persoonsnaampaar, behalve de mandatarissen uit de witte lijst en de woordparen die
    een orgaan, vakgebied of plaats benoemen. Buiten zo'n stuk raakt de regel niets aan."""
    if not tekst:
        return tekst, 0
    aantal = 0
    # Drie ingangen, want zo'n lijst heeft niet altijd tabelvorm: het besluit zelf (LEDENBLOK), een
    # opsomming met rolwoorden, en de samenvatting in lopende tekst (een rol plus meerdere namen).
    _rollen = len(ROL_WOORD.findall(tekst))
    if PRIVAAT_CONTEXT.search(tekst) and (LEDENBLOK.search(tekst) or _rollen >= 3
                                          or (_rollen >= 1 and len(PERSOON_PAAR.findall(tekst)) >= 3)):
        tekst, aantal = _maskeer_paren(tekst)
    # subn() telt élke treffer, ook wanneer we de tekst bewust ongemoeid laten (een organisatienaam
    # naast een rol). Zelf tellen dus, anders meldt de klep in build.py een lek dat er niet is.
    geteld = [0]

    def _rol(m):
        naam = m.group(2)
        if naam in (_PUBLIEK or set()) or GEEN_PERSOON.search(naam):
            return m.group(0)
        geteld[0] += 1
        return m.group(1) + NAAM_MASKER
    tekst = ONDERSTEUNENDE_ROL.sub(_rol, tekst)
    if PRIVAAT_CONTEXT.search(tekst):
        tekst = PARTIJ_NAAM.sub(_rol, tekst)
    return tekst, aantal + geteld[0]


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
    tekst, leden = maskeer_ledenlijst(tekst)
    if met_achternamen is None:
        met_achternamen = any(v in tekst for v in PRIVE_VORMEN)
    vormen = sorted(PRIVE_VORMEN, key=len, reverse=True)
    if met_achternamen:
        vormen = vormen + sorted(PRIVE_ACHTERNAAM, key=len, reverse=True)
    aantal = 0
    for vorm in vormen:
        # Tussen de delen van een naam mag elke witruimte staan: een pdf breekt een naam soms over
        # twee regels ("Van" op de ene, de rest op de volgende), en dan glipte hij erdoor.
        patroon = r"\s+".join(re.escape(deel) for deel in vorm.split())
        tekst, n = re.subn(r"\b%s\b" % patroon, NAAM_MASKER, tekst)
        aantal += n
    tekst, n = BEROEP_NAAM.subn(r"\1 " + NAAM_MASKER, tekst) if BEROEP_NAAM else (tekst, 0)
    return tekst, aantal + n + gids + leden


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

# Namen die we beoordeeld hebben en NIET maskeren, uit hetzelfde lokale bestand. Ook dit
# oordeel hoort niet in een publieke repo: "wij vinden deze persoon publiek" is een uitspraak
# over een mens, en die staat er dan zwart op wit naast een naam die soms in opspraak is.
BEOORDEELD_PUBLIEK = tuple(_PRIVACY_RUW.get("beoordeeld_publiek", ()))


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

    # 1e) Context die een persoon herleidbaar maakt: een woonadres na "wonend/woonachtig/
    #     gedomicilieerd" en een geboortedatum na "°" of "geboren". Over ALLE stukken, niet enkel
    #     die met een naam van de lijst: nagemeten stond het adres naast een naam die al "[naam]"
    #     was, en droeg een naam die op geen enkele lijst staat (een kind) er een geboortedatum bij.
    adressen = 0
    geb_teken = 0
    for lijst in LIJSTEN:
        for item in data.get(lijst, []):
            for veld in VELDEN + ("kernbegrippen",):
                waarde = item.get(veld)
                if isinstance(waarde, str):
                    nieuw, a, g = maskeer_context(waarde)
                    if a or g:
                        item[veld] = nieuw
                        adressen += a
                        geb_teken += g
                elif isinstance(waarde, list):
                    for i, deel in enumerate(waarde):
                        if isinstance(deel, str):
                            nieuw, a, g = maskeer_context(deel)
                            if a or g:
                                waarde[i] = nieuw
                                adressen += a
                                geb_teken += g

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
    print(f"Woonadressen gemaskeerd: {adressen}; geboortedatums met ° of 'geboren': {geb_teken}.")
    print(f"E-mailadressen geredacteerd in {geredacteerd} tekstveld(en); "
          f"{leeggemaakt} contactveld(en) van de roster leeggemaakt.")
    print(f"Geboortedatums gemaskeerd: {datums} in {stukken} stuk(ken) met een kandidatentabel.")
    print(f"Rekeningnummers gemaskeerd: {rekeningen}.")
    print(f"Namen van burgers/landmeters gemaskeerd: {namen} vermelding(en), "
          f"{len(PRIVE_VOLLEDIG)} personen op de lijst.")
    waakhond(data)


if __name__ == "__main__":
    main()
