"""
tag_items.py — STAP 11: de semantische laag (AI-tagging).

Zet de kale parse (titel + resultaat) om naar de leesbare laag die het product
eigenlijk maakt: een samenvatting in mensentaal, thema's uit de vaste taxonomie,
genoemde straten, de buurt, en — enkel als de brontekst het expliciet zegt — de
bevoegde schepen. Werkt op data.json en verrijkt de agendapunten, de
(gemeenteraad / raad voor maatschappelijk welzijn) als de college_beslissingen.

Twee principes uit het project, hard ingebouwd:
  • Niets verzinnen. Thema's mogen ALLEEN uit data["themes"] komen; straten/buurt
    enkel als ze duidelijk in de tekst staan (en, als het straten-register bestaat,
    worden ze daartegen gevalideerd); schepen blijft null tenzij expliciet vermeld.
  • Eén keer per document. Resultaten worden gecachet op een inhoud-hash in
    data/tags_cache.json — een tweede run kost niets voor wat niet veranderd is.

Gebruik:
  python tag_items.py --dry-run         # gratis: test de hele pijplijn zonder API
  python tag_items.py --limit 5         # echt taggen, max 5 API-calls (kosten beperken)
  python tag_items.py                   # alles taggen wat nog geen samenvatting heeft
  python tag_items.py --overwrite       # ook al-getagde items opnieuw doen
Vereist voor echte runs:  pip install anthropic  +  omgevingsvariabele ANTHROPIC_API_KEY
"""
from __future__ import annotations
import os
import sys
import json
import time
import hashlib
import argparse
from pathlib import Path

# print() met → ✓ … werkt zo ook op een Windows-console die standaard cp1252 gebruikt
# (anders crasht een niet-ASCII-teken met een UnicodeEncodeError).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent
DATA = BASE / "data.json"
CACHE = BASE / "data" / "tags_cache.json"
STRATEN = BASE / "data" / "straten_mechelen.json"

# Het model voor de samenvattingen. Pas hier aan, niet verspreid door de code.
#   claude-sonnet-5   sterke prijs-kwaliteit, bijna Opus ($3 / $15, introprijs $2 / $10 t/m 2026-08-31)
#   claude-opus-4-8   krachtigst ($5 in / $25 uit per miljoen tokens)
#   claude-haiku-4-5  goedkoopst ($1 / $5), voor eenvoudig batchwerk
# We taggen dit archief met Sonnet 5: op extractie/samenvatten bijna Opus-kwaliteit, aan de
# helft van de prijs, en met de Batches-API (--batch) nog eens 50% korting. Dat past het budget
# op de grootste sprong: de collegebesluiten die nu, dankzij de gekoppelde uittreksels, eindelijk
# een volledige brontekst hebben in plaats van enkel een titel.
# Let op: de cache sleutelt op PROMPT_VERSION + inhoud, niet op het model. Een model-
# wissel hertagt dus NIET vanzelf alles (geen verrassingskosten); bestaande samenvattingen
# blijven staan en enkel gewijzigde of nieuwe stukken gebruiken het nieuwe model. Wil je het hele
# archief opnieuw met dit model: verhoog PROMPT_VERSION en reken op een volledige run.
MODEL = "claude-sonnet-5"
# Thinking staat op Sonnet 5 standaard AAN (adaptief). Voor een extractietaak willen we dat niet:
# het kost denk-tokens en kan de JSON afkappen binnen MAX_TOKENS. Expliciet uit (geldig op Sonnet 5
# én Opus 4.8; op beide is 'disabled' de facto het oude gedrag). De kwaliteitswinst zit in de rijkere
# brontekst, niet in het denken.
THINKING = {"type": "disabled"}
MAX_TOKENS = 2000        # kop voor een rijkere samenvatting + lange opsommingen; je betaalt enkel de
                         # werkelijk gegenereerde tokens, dus dit voorkomt afkappen zonder extra kost
PAUSE_SECONDS = 1.0          # beleefd + voorkomt rate-limit-pieken


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------
def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            # Cache raakte beschadigd (bv. de pc viel uit tijdens het schrijven). Negeer
            # hem en begin leeg: de reeds getagde items staan in data.json en worden
            # overgeslagen, dus enkel de nog-niet-getagde worden opnieuw gedaan.
            print(f"[let op] {CACHE.name} was beschadigd en is genegeerd; ik begin met een lege cache.")
    return {}


def save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    # Atomair schrijven: eerst volledig naar een temp-bestand, dan in één keer omwisselen.
    # Valt de pc precies tijdens het schrijven uit, dan blijft de vorige (geldige) cache
    # staan — hij kan dus nooit half geschreven en onleesbaar achterblijven.
    tmp = CACHE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CACHE)


# Bump bij elke promptwijziging. Zit mee in de cache-sleutel, dus dan vervalt de oude
# cache vanzelf en wordt alles opnieuw getagd — maar wel HERVATBAAR (een onderbroken
# her-tag pikt verder op uit de cache, zonder dubbel te betalen).
PROMPT_VERSION = "v4-actief-onambtelijk"


# --------------------------------------------------------------------------
# Uittreksel-verrijking: voor besluiten met een gekoppeld uittreksel (koppel_uittreksels.py)
# gebruiken we de VOLLEDIGE officiële tekst uit de lokale cache als brontekst. Zo krijgt vooral
# een collegebesluit (dat anders enkel een titel heeft) een rijke samenvatting. De tekst blijft
# in de cache (git-genegeerd); enkel de afgeleide samenvatting komt in data.json.
_KOPPEL_PAD = BASE / "data" / "uittreksel_koppeling.json"
_UITT_CACHE = BASE / "data" / "uittreksel_cache"
_koppeling_cache = None

def _koppeling() -> dict:
    global _koppeling_cache
    if _koppeling_cache is None:
        _koppeling_cache = (json.loads(_KOPPEL_PAD.read_text(encoding="utf-8"))
                            if _KOPPEL_PAD.exists() else {})
    return _koppeling_cache

def uittreksel_tekst(item_id: str, klassen=None) -> str:
    """Samengevoegde volledige tekst van de aan dit besluit gekoppelde stukken; optioneel enkel
    bepaalde klassen ('uittreksel' = het besluit zelf; 'bijlage' = reglement/tarief-tekst)."""
    stukken = []
    for ref in _koppeling().get(item_id or "", []):
        if klassen and ref.get("klasse") not in klassen:
            continue
        sl = ref.get("cache_sleutel")
        if not sl:
            continue
        p = _UITT_CACHE / (sl + ".txt")
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            if t:
                stukken.append(t)
    return "\n\n".join(stukken)

# Tagging leest niet meer dan dit aantal tekens brontekst: één besluit past ruim, maar een
# verzamelpunt met tientallen bijlagen zou anders de input (en de kost) laten exploderen.
MAX_BRON = 24000

def brontekst_voor_tagging(item: dict) -> str:
    """De brontekst die de tagging ziet: primair de officiële BESLUIT-tekst (klasse 'uittreksel',
    rijker dan de besluitenlijst); de bijlage-reglementen laten we hier weg (die voeden de zoek,
    niet de samenvatting). Wordt NIET naar data.json teruggeschreven."""
    bt = (item.get("brontekst") or "").strip()
    ut = uittreksel_tekst(item.get("id", ""), klassen={"uittreksel"})
    if not ut:                                   # geen eigen uittreksel: val terug op bijlagen
        ut = uittreksel_tekst(item.get("id", ""), klassen={"bijlage"})
    bron = (ut + ("\n\n" + bt if bt else "")).strip() if ut else bt
    return bron[:MAX_BRON]


def content_key(item: dict) -> str:
    """Hash over de bron-inhoud én de promptversie. Wijzigt de titel/brontekst (incl. een nieuw
    gekoppeld uittreksel) of de prompt, dan vervalt de cache vanzelf en wordt het item opnieuw
    getagd; anders hergebruiken we het resultaat (zo is een her-tag hervatbaar)."""
    bron = PROMPT_VERSION + "\n" + (item.get("titel") or "") + "\n" + brontekst_voor_tagging(item)
    return hashlib.sha1(bron.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------
ORG_VOLUIT = {
    "College": "het college van burgemeester en schepenen",
    "Vast bureau": "het vast bureau",
    "Burgemeester": "de burgemeester",
    "Gemeenteraad": "de gemeenteraad",
    "Raad voor maatschappelijk welzijn": "de raad voor maatschappelijk welzijn",
    "RMW": "de raad voor maatschappelijk welzijn",
}


def orgaan_van(item: dict):
    """Welk bestuursorgaan nam dit besluit? Voor college/vast bureau staat het in 'orgaan';
    voor raadsstukken leiden we het af uit de sessie-id ('rmw-…' of 'gemeenteraad-…').
    Zo kan het taalmodel het orgaan niet meer verkeerd gokken (de grootste foutenbron)."""
    if item.get("orgaan"):
        return item["orgaan"]
    sid = str(item.get("sessie_id") or "")
    if sid.startswith("rmw"):
        return "Raad voor maatschappelijk welzijn"
    if sid.startswith("gemeenteraad"):
        return "Gemeenteraad"
    return None


def _messages_vraag(bron: str, themes: list[str], buurt_hint: str) -> tuple[str, str]:
    """Prompt voor een schriftelijke vraag: geen orgaan dat beslist, maar een raadslid dat
    de stad iets vraagt. We laten de samenvatting starten bij wie vraagt en waarover, en
    vatten het college-antwoord kort mee samen als het in de bron staat."""
    system = (
        "Je bent een neutrale stadsverslaggever voor een burgerjournalistiek experiment dat de "
        "Mechelse politiek leesbaar maakt. Je vat een SCHRIFTELIJKE VRAAG van een raadslid aan "
        "de stad samen in heerlijk heldere, concrete taal voor een gewone burger. Regels:\n"
        "1. Korte zinnen, één gedachte per zin. Alledaagse woorden, geen jargon, geen mening, geen aannames.\n"
        "2. Schrijf ACTIEF en begin bij wie handelt: 'Raadslid X vraagt de stad ...'. Vermijd passief "
        "('er wordt gevraagd', 'werd geantwoord').\n"
        "3. VERMIJD AMBTELIJKE TAAL: vervang stadhuiswoorden door gewone met dezelfde betekenis "
        "('vraagt' niet 'verzoekt', 'over' niet 'met betrekking tot' of 'inzake', 'volgens' niet "
        "'conform'). Schrap opvulsels als 'onderhavige', 'desbetreffende', 'in het kader van'. Minder "
        "ambtelijk mag nooit minder nauwkeurig worden.\n"
        "4. STRIKT BIJ DE FEITEN: voeg nooit een cijfer, datum, naam, gevolg of interpretatie "
        "toe die niet letterlijk in de bron staat. Bij twijfel laat je het weg.\n"
        "5. Staat er een antwoord van het college bij, vat dat in één of twee zinnen mee samen. "
        "Staat er geen antwoord, schrijf dan niets over een antwoord.\n"
        "6. Gebruik geen gedachtestreepjes (— of –), ook niet als koppelteken tussen twee namen of in een traject of reeks: schrijf 'Dorpstraat en Juniorslaan' of 'Dorpstraat-Juniorslaan', nooit 'Dorpstraat – Juniorslaan'. Kies anders een komma, dubbele punt of punt.\n"
        "7. Antwoord met UITSLUITEND geldige JSON, zonder uitleg of code-haakjes."
    )
    user = f"""Vat de volgende schriftelijke vraag van een raadslid samen en tag ze.

BRON:
{bron}

Geef exact dit JSON-object terug:
{{
  "decoded": "Heldere, volledige samenvatting in mensentaal. Begin bij wie wat vraagt aan de stad ('Raadslid X vraagt de stad of/naar ...') en noem het concrete onderwerp exact bij naam zoals het in de bron staat (de specifieke locatie, straat, dienst, organisatie of het project). Neem de concrete feiten uit de vraag mee (cijfers, datums, plaatsen die er letterlijk staan). Staat er een antwoord van het college bij, vat de kern daarvan in één tot drie zinnen mee samen. Uitsluitend op basis van de bron, niets verzinnen. Een vloeiende alinea van 2 tot 5 zinnen; alleen opsommingstekens als de vraag echt meerdere losse punten bevat. Hoogstens ~140 woorden.",
  "kernbegrippen": ["2 tot 6 distinctieve termen die deze vraag identificeren en helpen om ze met verwante stukken tot één dossier te bundelen: eigennamen, projectnamen, straat- of pleinnamen, organisaties, adressen. UITSLUITEND wat LETTERLIJK in de bron staat, exact geschreven. Geen algemene woorden, geen thema's, niets verzinnen. Niets distinctiefs? Lege lijst."],
  "themes": ["één tot drie thema's, ALLEEN uit de toegestane lijst"],
  "streets": ["straatnamen die letterlijk in de tekst staan; anders lege lijst"],
  "neighborhood": "buurt of deelgemeente, of null",
  "schepen": null
}}

Toegestane thema's (kies enkel hieruit): {", ".join(themes)}.
{buurt_hint}
Blijf strikt bij de feiten uit de brontekst. Verzin nooit een gevolg, cijfer, straat, buurt, antwoord of kernbegrip die niet letterlijk in de tekst staat. Bij twijfel: laat weg."""
    return system, user


def build_messages(item: dict, themes: list[str], buurten: list[str]) -> tuple[str, str]:
    bron = (item.get("titel") or "").strip()
    volledige = brontekst_voor_tagging(item)   # incl. de gekoppelde uittreksel-tekst (uit cache)
    if volledige:
        bron += "\n\nVolledige tekst:\n" + volledige

    buurt_hint = (
        "Kies 'neighborhood' uitsluitend uit deze buurten/deelgemeenten: "
        + ", ".join(buurten) + "."
        if buurten else
        "Er is geen buurtenlijst beschikbaar; vul 'neighborhood' alleen in als de "
        "tekst een Mechelse buurt of deelgemeente letterlijk noemt, anders null."
    )

    # Schriftelijke vraag: geen besluit van een orgaan, maar een raadslid dat de stad iets
    # vraagt. Aparte framing (geen orgaan-attributie, wél 'Raadslid X vraagt ...').
    if item.get("type") == "schriftelijke_vraag":
        return _messages_vraag(bron, themes, buurt_hint)

    org = orgaan_van(item)
    org_naam = ORG_VOLUIT.get(org, org)
    org_regel = (f"\nBESLISSEND ORGAAN: {org_naam}. Vermeld je wie besliste, gebruik dan EXACT "
                 f"dit orgaan, nooit een ander (schrijf bv. niet 'de gemeenteraad' als het het "
                 f"college is). Hoef je de beslisser niet te noemen, blijf dan neutraal.\n"
                 if org_naam else "")

    system = (
        "Je bent een neutrale stadsverslaggever voor een burgerjournalistiek experiment dat de "
        "Mechelse besluitvorming leesbaar maakt. Je vat één agendapunt of besluit samen in "
        "heerlijk heldere, concrete taal voor een gewone burger. Volg deze regels strikt:\n"
        "1. Korte zinnen, één gedachte per zin. Schrijf zoals je het een buur zou uitleggen: "
        "alledaagse woorden, geen jargon, geen mening, geen aannames, geen aanloopjes.\n"
        "2. Schrijf ACTIEF: zet de handelende partij vooraan en laat ze het werkwoord doen "
        "('het college keurt goed', 'het vast bureau verwijst het dossier door'), nooit passief "
        "('het dossier wordt doorverwezen', 'werd vastgesteld', 'werd goedgekeurd'). Zie je 'wordt' "
        "of 'werd' als hulpwerkwoord, herschrijf dan zodat wie handelt vooraan komt.\n"
        "3. VERMIJD AMBTELIJKE TAAL: vervang stadhuiswoorden door gewone met exact dezelfde betekenis. "
        "'keurt goed' niet 'verleent goedkeuring'; 'beslist' niet 'gaat over tot'; 'over' niet 'met "
        "betrekking tot' of 'inzake'; 'volgens' niet 'conform'; 'daarna' niet 'vervolgens'; 'vraagt' "
        "niet 'verzoekt'; 'begin' niet 'aanvang'. Schrap opvulsels als 'onderhavige', 'desbetreffende', "
        "'voormelde', 'in het kader van', 'naar aanleiding van'. Heeft een term een precieze betekenis "
        "('neemt akte van', 'stelt vast'), geef die dan in gewone taal weer zonder ze te verdraaien. "
        "Minder ambtelijk mag NOOIT minder nauwkeurig worden.\n"
        "4. VOLLEDIG binnen de bron: neem élk concreet feit uit de brontekst mee (wat er beslist is, "
        "over welke concrete zaak, welk bedrag, welke datum, welke locatie, welke partijen) en noem elke "
        "eigennaam exact zoals ze er staat. Vollediger betekent méér feiten uit de bron, niet langere zinnen: schrijf geen vulzinnen.\n"
        "5. STRIKT BIJ DE FEITEN: voeg nooit een gevolg, context, motief, datum, bedrag, naam of "
        "interpretatie toe die niet letterlijk in de brontekst staat. Bij twijfel laat je het weg. Is "
        "de bron kort (soms één regel), houd je samenvatting dan ook kort: liever te kort dan iets verzinnen.\n"
        "6. Gebruik geen gedachtestreepjes (— of –), ook niet als koppelteken tussen twee namen of in een traject of reeks: schrijf 'Dorpstraat en Juniorslaan' of 'Dorpstraat-Juniorslaan', nooit 'Dorpstraat – Juniorslaan'. Kies anders een komma, dubbele punt of punt.\n"
        "7. Schrijf 'gemeenteraad' en 'raad voor maatschappelijk welzijn' voluit, nooit afgekort. "
        "Verwijs nooit naar de technische bron of een website.\n"
        "8. Antwoord met UITSLUITEND geldige JSON, zonder uitleg of code-haakjes."
    )

    user = f"""Vat het volgende besluit samen en tag het.

BRON:
{bron}
{org_regel}
Geef exact dit JSON-object terug:
{{
  "decoded": "Heldere, VOLLEDIGE samenvatting in mensentaal van wat er beslist is, UITSLUITEND op basis van de bron, zonder toegevoegde gevolgen, context of interpretatie. Neem élk concreet feit uit de bron mee: het besluit zelf, de concrete zaak, bedragen, datums, locaties, betrokken partijen. Noem elk concreet onderwerp ALTIJD bij zijn exacte naam zoals in de bron: de organisatie, vereniging, school, club, het project, de locatie, de straat, de persoon, of een dossier- of zaaknummer. Veralgemeen een naam die in de bron staat nooit ('SK Heffen', niet 'een sportclub'). Staat er geen concrete naam of extra feit in de bron, verzin er dan geen en blijf kort. OPMAAK: gaat het besluit over MEERDERE losse punten of deelbeslissingen, begin dan met één korte inleidende zin en zet daarna ELK punt op een NIEUWE regel die begint met '• ' (opsommingsteken + spatie); houd elk punt kort. Gaat het over één enkele beslissing, schrijf dan een korte vloeiende alinea. Gebruik echte regeleinden tussen de punten. Vollediger betekent méér feiten uit de bron, geen vulzinnen. Bij een LANGE, juridische of technische bron: vat de KERN samen, transcribeer niet alles; som niet elke straat, elk stemcijfer of elk technisch detail op, maar noem de belangrijkste en vat de rest samen ('en 12 andere straten', 'goedgekeurd met ruime meerderheid'). Totaal hoogstens ~180 woorden.",
  "kernbegrippen": ["2 tot 6 distinctieve termen die dit stuk uniek identificeren en helpen om het samen met verwante stukken tot één dossier te bundelen: eigennamen, projectnamen, verenigingen, scholen, straat- of pleinnamen, adressen, dossier- of zaaknummers. UITSLUITEND termen die LETTERLIJK in de bron staan, exact zoals geschreven. Geen algemene woorden ('subsidie', 'reglement', 'goedkeuring'), geen thema's, niets verzinnen. Geen distinctieve termen in de bron? Geef een lege lijst."],
  "themes": ["één tot drie thema's, ALLEEN uit de toegestane lijst"],
  "streets": ["straatnamen die letterlijk in de tekst staan; anders lege lijst"],
  "neighborhood": "buurt of deelgemeente, of null",
  "schepen": "naam van de bevoegde schepen ALLEEN als die expliciet vermeld wordt, anders null"
}}

Toegestane thema's (kies enkel hieruit): {", ".join(themes)}.
{buurt_hint}
Blijf strikt bij de feiten uit de brontekst. Verzin nooit een punt, gevolg, motief, straat, buurt, schepen of kernbegrip die niet letterlijk in de tekst staat. Bij twijfel: laat weg."""
    return system, user


def parse_tags(text: str) -> dict:
    """Strip eventuele code-haakjes en parse naar dict; valideer ruw."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if "```" in t[3:] else t
        t = t.lstrip("json").strip().strip("`").strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"geen JSON-object gevonden in: {text[:200]}")
    # strict=False: het model zet opsommingspunten op ECHTE nieuwe regels (zoals de prompt vraagt),
    # en zo'n letterlijke newline in een JSON-string laat de strenge parser afketsen. We laten
    # control-tekens in strings toe; json ontsnapt ze zelf weer bij het terugschrijven.
    return json.loads(t[start:end + 1], strict=False)


# --------------------------------------------------------------------------
# API (echt) en dry-run (gratis)
# --------------------------------------------------------------------------
_client = None


def call_api(system: str, user: str) -> str:
    global _client
    import anthropic
    if _client is None:
        _client = anthropic.Anthropic()      # leest ANTHROPIC_API_KEY uit de omgeving
    for poging in range(5):
        try:
            resp = _client.messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, thinking=THINKING, system=system,
                messages=[{"role": "user", "content": user}],
            )
            return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        except (anthropic.RateLimitError, anthropic.InternalServerError,
                anthropic.APIConnectionError) as e:
            if poging == 4:
                raise
            time.sleep(2 ** poging)          # 1,2,4,8s back-off bij rate-limit/overbelasting
    return ""


def dry_tag(item: dict) -> dict:
    """Geen API: een herkenbare placeholder zodat je de hele pijplijn gratis test."""
    return {"decoded": f"[dry-run, niet getagd] {item.get('titel', '')}".strip(),
            "kernbegrippen": [], "themes": [], "streets": [], "neighborhood": None, "schepen": None}


# --------------------------------------------------------------------------
# Validatie + merge
# --------------------------------------------------------------------------
def schoon(tags: dict, themes: set[str], straten: set[str], buurten: set[str]) -> dict:
    th = [t for t in (tags.get("themes") or []) if t in themes]        # alleen geldige thema's
    streets = tags.get("streets") or []
    if straten:                                                        # valideer tegen register
        streets = [s for s in streets if s in straten]
    hood = tags.get("neighborhood")
    if buurten and hood not in buurten:
        hood = None
    # Kernbegrippen: distinctieve termen uit de bron. Trim, ontdubbel (hoofdletter-ongevoelig),
    # gooi lege of te lange weg, hoogstens 8. Niet tegen een register gevalideerd (het zijn vrije
    # termen), maar de prompt eist 'alleen wat letterlijk in de bron staat'.
    kb, gezien = [], set()
    for k in (tags.get("kernbegrippen") or []):
        if not isinstance(k, str):
            continue
        k = k.strip()
        kl = k.lower()
        if not k or len(k) > 60 or kl in gezien:
            continue
        gezien.add(kl); kb.append(k)
    return {"decoded": (tags.get("decoded") or "").strip(),
            "kernbegrippen": kb[:8],
            "themes": th, "streets": streets,
            "neighborhood": hood, "schepen": tags.get("schepen") or None}


def merge(item: dict, tags: dict, overwrite: bool) -> None:
    def zet(veld, waarde):
        leeg = not item.get(veld) or (veld == "decoded" and item.get("decoded") == item.get("titel"))
        if waarde and (overwrite or leeg):
            item[veld] = waarde
    zet("decoded", tags["decoded"])
    if tags["themes"] and (overwrite or not item.get("themes")):
        item["themes"] = tags["themes"]
    if tags["streets"] and (overwrite or not item.get("streets")):
        item["streets"] = tags["streets"]
    if tags["neighborhood"] and (overwrite or not item.get("neighborhood")):
        item["neighborhood"] = tags["neighborhood"]
    if tags["schepen"] and (overwrite or not item.get("schepen")):
        item["schepen"] = tags["schepen"]
    if tags["kernbegrippen"] and (overwrite or not item.get("kernbegrippen")):
        item["kernbegrippen"] = tags["kernbegrippen"]


def heeft_tagging(item: dict) -> bool:
    return bool(item.get("decoded")) and item.get("decoded") != item.get("titel") \
        and bool(item.get("themes"))


# --------------------------------------------------------------------------
# Batch (Message Batches API): één inzending, 50% goedkoper, resultaat binnen ~1 uur.
# Ideaal voor een grote (her)tag-ronde, bv. na het koppelen van de uittreksels: de
# collegebesluiten krijgen dan hun eerste volwaardige samenvatting op de volledige tekst.
# --------------------------------------------------------------------------
def batch_werklijst(doelen, themes, buurten, cache) -> dict:
    """{content_key: (system, user)} voor élk stuk waarvan de inhoud NIET meer in de cache zit.
    Dat is precies de te (her)taggen set: nooit-getagde stukken én stukken waarvan de brontekst
    veranderde (een nieuw gekoppeld uittreksel wijzigt de content_key). Al-getagde, onveranderde
    stukken zitten nog in de cache en vallen dus vanzelf weg. Ontdubbeld op identieke inhoud."""
    werk = {}
    for item in doelen:
        key = content_key(item)
        if key in cache or key in werk:
            continue
        werk[key] = build_messages(item, themes, buurten)
    return werk


def batch_raming(werk: dict) -> tuple[int, int, float]:
    """Ruwe kostenraming. tekens/4 ≈ tokens; Sonnet 5 tokeniseert ~1,3× dichter (marge).
    Introprijs t/m 2026-08-31: $2 in / $10 uit per 1M; Batches-API = 50% korting."""
    tekens = sum(len(s) + len(u) for s, u in werk.values())
    in_tok = int(tekens / 4 * 1.3)
    uit_tok = len(werk) * 400                       # ~400 tokens per JSON-samenvatting
    kost = (in_tok / 1e6) * 2 * 0.5 + (uit_tok / 1e6) * 10 * 0.5
    return in_tok, uit_tok, kost


def batch_run(werk: dict, go: bool, cache: dict) -> int:
    """Dien de werklijst in als één Message-batch, pol tot klaar en giet de resultaten in de
    cache (op dezelfde content_key). Zonder --go: enkel de kostenraming, niets ingediend, geen
    API-kost. Geeft het aantal nieuw gecachte stukken terug."""
    in_tok, uit_tok, kost = batch_raming(werk)
    print(f"Batch: {len(werk)} stukken te (her)taggen | ~{in_tok:,} in + ~{uit_tok:,} uit tokens | "
          f"raming ~${kost:.2f}  ({MODEL}, introprijs + 50% batchkorting)")
    if not go:
        print("(voorbeeld — niets ingediend, geen kost. Draai met  --batch --go  om echt te taggen.)")
        return 0

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    client = anthropic.Anthropic()

    reqs = [Request(custom_id=key,
                    params=MessageCreateParamsNonStreaming(
                        model=MODEL, max_tokens=MAX_TOKENS, thinking=THINKING, system=system,
                        messages=[{"role": "user", "content": user}]))
            for key, (system, user) in werk.items()]
    batch = client.messages.batches.create(requests=reqs)
    print(f"Batch ingediend: {batch.id} — pollen tot klaar (meestal < 1 uur)...", flush=True)

    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        rc = b.request_counts
        print(f"  {b.processing_status}: {rc.processing} bezig · {rc.succeeded} klaar · {rc.errored} fout",
              flush=True)
        time.sleep(30)

    ok = fout = 0
    for res in client.messages.batches.results(batch.id):
        if res.result.type == "succeeded":
            msg = res.result.message
            tekst = "".join(x.text for x in msg.content if getattr(x, "type", "") == "text")
            try:
                cache[res.custom_id] = parse_tags(tekst); ok += 1
            except Exception as e:
                print(f"  ! parse mislukt {res.custom_id}: {e}"); fout += 1
        else:
            print(f"  ! {res.custom_id}: {res.result.type}"); fout += 1
    save_cache(cache)
    print(f"Batch klaar: {ok} gecachet, {fout} mislukt.")
    return ok


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="AI-tagging van agendapunten en collegebesluiten.")
    ap.add_argument("--dry-run", action="store_true", help="geen API-calls; test de pijplijn gratis")
    ap.add_argument("--limit", type=int, default=0, help="max aantal API-calls deze run (0 = onbeperkt)")
    ap.add_argument("--overwrite", action="store_true", help="ook al-getagde items opnieuw doen")
    ap.add_argument("--only", choices=["raad", "college", "vragen", "all"], default="all")
    ap.add_argument("--batch", action="store_true",
                    help="tag de gewijzigde/nieuwe stukken via de Message Batches API (50%% korting)")
    ap.add_argument("--go", action="store_true",
                    help="bij --batch: echt indienen (zonder --go enkel een gratis kostenraming)")
    args = ap.parse_args()

    data = json.loads(DATA.read_text(encoding="utf-8"))
    themes = data.get("themes", [])
    themes_set = set(themes)
    register = json.loads(STRATEN.read_text(encoding="utf-8")) if STRATEN.exists() else {}
    straten = {s["naam"] for s in register.get("straten", [])}
    buurten = [b["naam"] for b in register.get("buurten", [])] + register.get("deelgemeenten", [])
    buurten_set = set(buurten)

    # De letterlijke vraagteksten staan niet meer in data.json (wel in de lokale cache):
    # vul ze hier in het GEHEUGEN aan, zodat de tag-input en de content_key byte-identiek
    # blijven aan vroeger. strip_vraagteksten() haalt ze er vóór elke schrijf weer uit.
    _vc_pad = BASE / "data" / "schriftelijke_vragen.json"
    if _vc_pad.exists():
        _vc = {r["id"]: r for r in json.loads(_vc_pad.read_text(encoding="utf-8"))}
        for _it in data.get("schriftelijke_vragen", []):
            _bron = _vc.get(_it.get("id"))
            if _bron and not _it.get("brontekst"):
                for _veld in ("vraag", "antwoord", "brontekst"):
                    if _bron.get(_veld):
                        _it[_veld] = _bron[_veld]

    def strip_vraagteksten(d):
        for _it in d.get("schriftelijke_vragen", []):
            for _veld in ("vraag", "antwoord", "brontekst"):
                _it.pop(_veld, None)

    doelen = []
    if args.only in ("raad", "all"):
        doelen += data.get("agendapunten", [])
    if args.only in ("college", "all"):
        doelen += data.get("college_beslissingen", [])
    if args.only in ("vragen", "all"):
        doelen += data.get("schriftelijke_vragen", [])

    cache = load_cache()

    # --- Batch-modus: (her)tag de gewijzigde/nieuwe stukken in één goedkope inzending ---
    if args.batch:
        werk = batch_werklijst(doelen, themes, buurten, cache)
        if not werk:
            print("Batch: niets te (her)taggen — alle inhoud zit al in de cache."); return
        nieuw = batch_run(werk, args.go, cache)
        if not args.go:
            return                                       # voorbeeld: geen merge, geen schrijf, geen kost
        batched = set(werk)
        for item in doelen:                              # de (her)getagde stukken in data.json gieten
            key = content_key(item)
            if key in batched and key in cache:
                merge(item, schoon(cache[key], themes_set, straten, buurten_set), overwrite=True)
        DATA.with_suffix(".json.bak").write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
        strip_vraagteksten(data)
        DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Klaar — {nieuw} stukken (her)getagd via batch; {len(doelen)} bekeken; data.json bijgewerkt.")
        return

    calls = nieuw = uit_cache = 0

    try:
        for item in doelen:
            if heeft_tagging(item) and not args.overwrite:
                continue
            key = content_key(item)
            if key in cache:                               # cache-sleutel bevat de promptversie,
                tags = cache[key]; uit_cache += 1          # dus een hit is altijd actueel → hervatbaar
            elif args.dry_run:
                tags = dry_tag(item)
            else:
                if args.limit and calls >= args.limit:
                    continue
                system, user = build_messages(item, themes, buurten)
                try:
                    tags = parse_tags(call_api(system, user))
                except Exception as e:
                    print(f"  ! mislukt voor {item.get('id')}: {e}")
                    continue
                calls += 1; nieuw += 1
                cache[key] = tags
                save_cache(cache)                          # na elke call: niets verloren bij onderbreking
                time.sleep(PAUSE_SECONDS)
                if nieuw % 25 == 0:
                    print(f"  ... {nieuw} getagd via API (van {len(doelen)} bekeken)", flush=True)

            merge(item, schoon(tags, themes_set, straten, buurten_set), args.overwrite)
    except KeyboardInterrupt:
        # Netjes stoppen bij Ctrl+C: de voortgang tot hier wordt hieronder bewaard, dus
        # een herstart pikt gewoon verder op (geen traceback, geen dubbel werk).
        print(f"\n[onderbroken] Gestopt. De {nieuw} nieuw getagde items zijn bewaard — "
              f"start dezelfde opdracht opnieuw om verder te gaan.")

    if not args.dry_run:
        DATA.with_suffix(".json.bak").write_text(DATA.read_text(encoding="utf-8"), encoding="utf-8")
    strip_vraagteksten(data)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    modus = "DRY-RUN (niets betaald)" if args.dry_run else f"{nieuw} nieuw getagd, {uit_cache} uit cache"
    print(f"Klaar — {modus}. {len(doelen)} items bekeken; data.json bijgewerkt.")
    if not straten:
        print("Let op: geen straten-register gevonden — straten/buurt niet gevalideerd. "
              "Draai straten_mechelen.py voor strengere controle.")


if __name__ == "__main__":
    main()
