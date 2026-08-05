"""
bouw_zoekindex.py — STAP 3e: bouwt de volledige-tekstindex voor de zoekbalk.

Het zoekgat dat dit dicht: de zoekbalk doorzoekt titels en samenvattingen, maar een
woord dat enkel in het VOLLEDIGE stuk staat (de notulen van een agendapunt, de pdf van
een schriftelijke vraag, de brontekst) gaf nul treffers. Deze stap leest die volledige
teksten en schrijft een compacte omgekeerde index: woord -> agendapunt-id's. De site
laadt dat bestand pas bij de eerste zoekopdracht en toont zulke treffers met het label
"in het volledige stuk".

Wat er bewust NIET gebeurt:
  - Geen documentkopieën: de index bevat losse woorden zonder posities of zinnen; de
    tekst is er niet uit terug te bouwen. Het origineel blijft enkel bij de stad.
  - Geen woorden die de zoek al vindt: per punt trekken we de woorden uit titel en
    samenvatting af, zodat elke treffer uit deze index ook écht "in het volledige stuk"
    zit en het label nooit liegt.
  - Geen e-mailadressen: die worden uit de tekst geknipt vóór het tokeniseren, in de
    geest van schoon_brontekst.py.

Bronnen per punt (alleen wat ECHT extra tekst draagt):
  - agendapunten (gemeenteraad/RMW): brontekst + resultaattekst + kernbegrippen uit
    data.json, plus de volledige notulentekst per punt (parse_notulen op notulen.pdf).
  - schriftelijke vragen: vraag + antwoord + brontekst + kernbegrippen uit data.json,
    plus de volledige pdf-tekst.
  - college/vast bureau/burgemeester: hun besluitenlijst bevat enkel titels, en die doorzoekt
    de zoekbalk al, dus daarvoor is er niets extra te indexeren. Heeft zo'n besluit wél een
    gekoppeld uittreksel (koppel_uittreksels.py), dan gaat die volledige tekst er wel in.

De pdf-extractie is traag en daarom gecachet in data/zoekcache/ (sleutel: pad + mtime +
grootte). Een ongewijzigde pdf wordt nooit opnieuw gelezen; de stap is idempotent en
een gewone run kost seconden.

Draai:  python bouw_zoekindex.py          (run_all.py doet dit automatisch, stap 3e)
"""

import sys
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import hashlib
import json
import re
import unicodedata
from pathlib import Path
import schoon_brontekst

BASE = Path(__file__).parent
CACHE_DIR = BASE / "data" / "zoekcache"
UIT = BASE / "zoekindex.json"

# Zelfde normalisatie als kwNorm() in template.html: kleine letters + accenten weg.
# De frontend normaliseert de zoekterm identiek, anders vindt "financiën" "financien" niet.
def norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

EMAIL_RX = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
TOKEN_RX = re.compile(r"[a-z0-9]+")

# De stemblokken in de notulen sommen bij elk punt alle raadsleden op ("met 31 stemmen
# voor (Seppe Vriens, Bart Somers, ...)"). Zonder deze knip kreeg elke voornaam ~270
# punten in de index en werd zoeken op een naam ruis: je vond elke stemming, niet de
# stukken waar iemand echt iets doet. De namen blijven vindbaar waar ze betekenis
# dragen: als indiener, in de titel, of in de lopende tekst.
STEMLIJST_RX = re.compile(
    r"(stemmen?\s+(?:voor|tegen)|onthoudingen?)\s*\([^)]*\)",
    re.IGNORECASE | re.DOTALL)

# Namen achter een aanspreekvorm ("de heer Janssens", "mevrouw Peeters") komen in officiële
# stukken meestal toe aan een PARTICULIER: een aanvrager, een bezwaarindiener, iemand die
# aangesteld wordt. Zonder deze knip werd zo'n achternaam een zoekterm die naar precies één
# document leidt, en dat maakt van de zoekbalk een personenregister. Zelfde redenering en
# zelfde beperking als bij de stemlijst hierboven: we knippen enkel DEZE context weg. Draagt
# een naam elders betekenis (in de titel, als indiener van een schriftelijke vraag, in de
# lopende tekst), dan blijft ze gewoon vindbaar. Zo hoeven we niemand in te delen in
# 'mandataris' of 'particulier', wat we op basis van de brontekst toch niet betrouwbaar kunnen.
# De aanspreekvorm zelf is hoofdletter-ongevoelig ("De heer" aan het zinsbegin telt mee), maar
# wat erachter komt moet met een HOOFDLETTER beginnen. Dat onderscheid doet het echte werk:
# "mevrouw de Gouverneur" blijft staan (een functie, en een zinvolle zoekterm), "de heer Janssens"
# niet. Zou de hele uitdrukking hoofdletter-ongevoelig zijn, dan knipten we ook gewone zinnen weg.
AANSPREEK_RX = re.compile(
    r"\b(?i:de\s+heer|mevrouw|mevr\.|dhr\.|mr\.|mw\.)\s+"
    r"(?:[A-Z][\w'’-]*\.?\s+){0,2}[A-Z][\w'’-]+",
    re.UNICODE)

# Twee rolaanduidingen die in deze stukken vrijwel altijd een PARTICULIER aanwijzen en die de
# aanspreekvorm hierboven niet vangt, omdat er geen 'de heer' of 'mevrouw' bij staat:
#   'OGV Denteneer'    de aanvrager van een omgevingsvergunning, in de vergunningentabel
#   'Meester Lammar'   de advocaat van een burger in een bezwaardossier
# Gemeten aanleiding: beide vormen leverden werkende zoektermen op die naar precies één stuk
# leidden. Zelfde aanpak en zelfde beperking als hierboven: we knippen de CONTEXT weg, niet de
# persoon. Draagt een naam elders betekenis, dan blijft ze vindbaar. Bewust géén regel voor de
# tabelvorm 'VAN NAAM Voornaam': die is op deze teksten niet van gewone kopregels te
# onderscheiden ('VAN DE KREDIETEN', 'VAN GESCHILLEN') en zou echte zoekwoorden opeten.
ROL_RX = re.compile(r"\b(?:OGV|Meester)\s+(?:[A-Z][\w'’-]*\.?\s+){0,2}[A-Z][\w'’-]+")

# Woorden zonder zoekwaarde. Bewust kort gehouden: de document-frequentie-drempel
# hieronder vangt de ambtelijke boilerplate ("gelet", "overwegende", "artikel") vanzelf,
# in elke variant, zonder dat we ze allemaal hoeven te raden.
STOPWOORDEN = {
    "aan", "als", "bij", "dan", "dat", "de", "den", "der", "des", "deze", "die", "dit",
    "door", "een", "en", "er", "haar", "heeft", "hem", "het", "hij", "hun", "iets",
    "ik", "in", "is", "je", "kan", "maar", "met", "mij", "na", "naar", "niet", "nog",
    "nu", "of", "om", "onder", "ons", "ook", "op", "over", "te", "tegen", "toch",
    "tot", "uit", "van", "voor", "wat", "we", "wel", "werd", "wie", "wij", "wordt",
    "worden", "zal", "ze", "zich", "zij", "zijn", "zo", "zou", "zonder",
    # maandnamen: staan in vrijwel elke datumregel en zoeken niemand als term
    "januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
    "september", "oktober", "november", "december",
}
MIN_LENGTE = 3
# Een schriftelijke vraag is een paar bladzijden. Duikt er onder één vraag een pdf op die daar
# veelvouden boven zit, dan is dat geen vraag meer maar een BUNDEL: de stad hangt er dan andere
# stukken aan die niets met de vraag te maken hebben. Zo'n bundel indexeren doet twee dingen
# fout tegelijk. Ze maakt van dat ene punt een magneet die op zowat elke zoekterm bovenkomt, en
# ze sleept de persoonsgegevens mee die in die andere stukken staan (bezwaarindieners,
# vergunningsaanvragers en hun advocaten). Gemeten op dit corpus: de mediane vraag-pdf telt
# 3.298 tekens, de grootste ECHTE vraag 23.613, en één uitschieter 520.911. Die uitschieter
# droeg in z'n eentje 6.616 indexwoorden (mediaan per punt: 55) en 23% van de woordenlijst wees
# ernaar. De vraag zelf verdwijnt niet uit de zoek: vraag, antwoord en brontekst zet de parser
# al apart en die blijven gewoon doorzoekbaar, net als de samenvatting en de link naar de bron.
VRAAG_PDF_MAX = 50_000
# Een woord dat in meer dan zoveel punten voorkomt, is geen naald meer maar hooi:
# wie erop zoekt, krijgt honderden treffers en vindt niets. Gemeten op dit corpus ligt
# de grens tussen echte termen en ambtelijke vulling ("betreffende", "toepassing",
# "budgetopgave") rond de 120 punten; alles erboven bleek vulling.
MAX_PUNTEN_PER_WOORD = 120


def tokens(tekst):
    tekst = EMAIL_RX.sub(" ", tekst)
    tekst = AANSPREEK_RX.sub(" ", tekst)   # 'de heer X' / 'mevrouw Y' → geen zoekterm
    tekst = ROL_RX.sub(" ", tekst)         # 'OGV X' / 'Meester Y'     → geen zoekterm
    uit = set()
    for t in TOKEN_RX.findall(norm(tekst)):
        if len(t) < MIN_LENGTE or t in STOPWOORDEN:
            continue
        if t.isdigit():
            continue          # losse getallen (jaartallen, bedragen) zoeken niemand los op
        uit.add(t)
    return uit


def pdf_tekst(pad: Path) -> str:
    """Volledige tekst van een pdf, met cache op pad+mtime+grootte."""
    st = pad.stat()
    sleutel = hashlib.sha1(f"{pad.as_posix()}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
    cache = CACHE_DIR / (sleutel + ".txt")
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    import pdfplumber
    with pdfplumber.open(pad) as pdf:
        tekst = "\n".join((p.extract_text() or "") for p in pdf.pages)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(tekst, encoding="utf-8")
    return tekst


def notulen_per_punt():
    """(sessie_id, nummer) -> volledige notulentekst, via parse_notulen op de pdf's."""
    import parse_notulen
    SLUG = {"gemeenteraad": "gemeenteraad", "raad_voor_maatschappelijk_welzijn": "rmw"}
    uit = {}
    for mapnaam, slug in SLUG.items():
        basis = BASE / "data" / "raw" / mapnaam
        if not basis.exists():
            continue
        for zitting in sorted(p for p in basis.glob("*") if p.is_dir()):
            pdf = zitting / "notulen.pdf"
            if not pdf.exists():
                continue
            sessie_id = f"{slug}-{zitting.name.replace('-', '')}"
            # cache de geparste punten (parse_notulen leest de pdf zelf; cache ernaast)
            st = pdf.stat()
            sleutel = hashlib.sha1(f"NOT|{pdf.as_posix()}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
            cache = CACHE_DIR / (sleutel + ".json")
            if cache.exists():
                punten = json.loads(cache.read_text(encoding="utf-8"))
            else:
                punten = [{"nummer": p["nummer"], "tekst": p["tekst"]}
                          for p in parse_notulen.parse(pdf)["punten"]]
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(punten, ensure_ascii=False), encoding="utf-8")
            for p in punten:
                uit[(sessie_id, str(p["nummer"]))] = p["tekst"]
    return uit


# Uittreksel-koppeling (koppel_uittreksels.py): de volledige tekst van gekoppelde uittreksels
# en bijlagen (in de git-genegeerde cache) mee doorzoekbaar maken. Vooral de collegebesluiten,
# die anders enkel hun titel in de zoek hebben, worden zo op hun échte inhoud vindbaar.
_KOPPEL_PAD = BASE / "data" / "uittreksel_koppeling.json"
_UITT_CACHE = BASE / "data" / "uittreksel_cache"
_koppeling_cache = None

def _koppeling():
    global _koppeling_cache
    if _koppeling_cache is None:
        _koppeling_cache = (json.loads(_KOPPEL_PAD.read_text(encoding="utf-8"))
                            if _KOPPEL_PAD.exists() else {})
    return _koppeling_cache

def uittreksel_tekst(item_id):
    """Samengevoegde tekst van alle aan dit besluit gekoppelde stukken (uittreksel + bijlagen)."""
    stukken = []
    for ref in _koppeling().get(item_id or "", []):
        sl = ref.get("cache_sleutel")
        if not sl:
            continue
        p = _UITT_CACHE / (sl + ".txt")
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            if t:
                stukken.append(t)
    return "\n".join(stukken)


def main():
    data = json.loads((BASE / "data.json").read_text(encoding="utf-8"))

    # 1) extra tekst per punt verzamelen (alles wat de zoekbalk vandaag NIET doorzoekt)
    extra = {}     # id -> tekst

    notulen = notulen_per_punt()
    n_notulen = 0
    for ap in data.get("agendapunten", []):
        stukken = [
            ap.get("brontekst") or "",
            (ap.get("result") or {}).get("text") or "",
            " ".join(ap.get("kernbegrippen") or []),
        ]
        nt = notulen.get((ap.get("sessie_id"), str(ap.get("nummer"))))
        if nt:
            stukken.append(STEMLIJST_RX.sub(" ", nt))
            n_notulen += 1
        stukken.append(uittreksel_tekst(ap["id"]))     # officiële uittreksel-tekst, indien gekoppeld
        extra[ap["id"]] = "\n".join(s for s in stukken if s)

    n_vraag_pdf = 0
    n_vraag_bundel = 0
    # De vraagteksten komen uit de lokale cache (staan niet meer in data.json); de
    # maskeer_namen-stap verderop redigeert alles wat hier binnenkomt, cache incluis.
    vragen_cache = {}
    _vc_pad = BASE / "data" / "schriftelijke_vragen.json"
    if _vc_pad.exists():
        vragen_cache = {r["id"]: r for r in json.loads(_vc_pad.read_text(encoding="utf-8"))}
    for sv in data.get("schriftelijke_vragen", []):
        _vc = vragen_cache.get(sv["id"], {})
        stukken = [
            sv.get("vraag") or _vc.get("vraag") or "",
            sv.get("antwoord") or _vc.get("antwoord") or "",
            sv.get("brontekst") or _vc.get("brontekst") or "",
            " ".join(sv.get("kernbegrippen") or []),
        ]
        pdf = sv.get("pdf")
        if pdf and (BASE / pdf).exists():
            try:
                _t = pdf_tekst(BASE / pdf)
                if len(_t) > VRAAG_PDF_MAX:
                    n_vraag_bundel += 1
                    print(f"  (bundel niet geïndexeerd: {sv['id']} telt {len(_t):,} tekens, "
                          f"grens {VRAAG_PDF_MAX:,}; vraag en antwoord blijven doorzoekbaar)")
                else:
                    stukken.append(_t)
                    n_vraag_pdf += 1
            except Exception as e:
                print(f"  (pdf overgeslagen: {pdf}: {e})")
        extra[sv["id"]] = "\n".join(s for s in stukken if s)

    # College/burgemeester/vast bureau dragen enkel een titel in de besluitenlijst en werden
    # daarom overgeslagen. Wie nu een gekoppeld uittreksel heeft (koppel_uittreksels.py), krijgt
    # wél zijn volledige officiële tekst mee in de zoek: precies waar deze zoek het armst was.
    n_college = 0
    for cb in data.get("college_beslissingen", []):
        ut = uittreksel_tekst(cb["id"])
        if ut:
            extra[cb["id"]] = ut
            n_college += 1

    # 2) per punt: tokens van de extra tekst MIN de tokens die de zoek al vindt
    #    (titel + samenvatting), zodat het label "in het volledige stuk" altijd waar is
    alle = {p["id"]: p for p in data.get("agendapunten", [])}
    alle.update({p["id"]: p for p in data.get("schriftelijke_vragen", [])})
    alle.update({p["id"]: p for p in data.get("college_beslissingen", [])})

    # De namen van gewone burgers en landmeter-experts maskeren VOOR we tokeniseren. Deze
    # tekst komt uit de pdf's en de uittrekselcache, niet uit data.json, en loopt dus niet
    # langs schoon_brontekst.py. Zonder deze regel blijft een naam die overal elders gemaskeerd
    # is, gewoon een werkende zoekterm: nagemeten stond een achternaam nog in de index terwijl hij
    # nergens meer op de pagina te zien was.
    gemaskeerd = 0
    for item_id, tekst in list(extra.items()):
        extra[item_id], n = schoon_brontekst.maskeer_namen(tekst)
        gemaskeerd += n
    print("       namen gemaskeerd in de volledige tekst: %d vermelding(en)" % gemaskeerd)

    per_item = {}
    for item_id, tekst in extra.items():
        if not tekst:
            continue
        p = alle.get(item_id, {})
        al_vindbaar = tokens((p.get("titel") or "") + " " + (p.get("decoded") or ""))
        nieuw = tokens(tekst) - al_vindbaar
        if nieuw:
            per_item[item_id] = nieuw

    # 3) omgekeerde index + document-frequentie-drempel tegen boilerplate
    ids = sorted(per_item)
    idx_van = {item_id: i for i, item_id in enumerate(ids)}
    posting = {}
    for item_id, toks in per_item.items():
        for t in toks:
            posting.setdefault(t, []).append(idx_van[item_id])
    boilerplate = [t for t, lijst in posting.items() if len(lijst) > MAX_PUNTEN_PER_WOORD]
    for t in boilerplate:
        del posting[t]
    for lijst in posting.values():
        lijst.sort()

    # Woordgrens-lijst voor de zoek. De frontend laat een zoekterm alleen midden in een langer
    # woord tellen als die daar op een woordgrens ligt: "lijst" mag "kandidatenlijst" vinden,
    # "pfas" mag niet "ontwerpfase" vinden (ont-wer|pfas|e ligt dwars over de lettergreep). Die
    # toets kijkt of het stuk vóór de term zelf een woord is, en dan mist ze precies de woorden
    # die hierboven als boilerplate of stopwoord uit de index vielen: zonder "deel" in de lijst
    # zou "fiets" zijn "deelfietsen" verliezen. Die woorden gaan dus apart mee. Ze zijn géén
    # zoekbare termen, enkel bouwstenen voor die toets, en het kost een paar kB.
    grens = sorted(set(boilerplate) | STOPWOORDEN)

    uit = {
        "v": 1,
        "gen": data.get("generated_at", ""),
        "items": ids,
        "tokens": posting,
        "grens": grens,
    }
    UIT.write_text(json.dumps(uit, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    kb = UIT.stat().st_size / 1024
    print(f"Zoekindex gebouwd: {len(posting):,} woorden over {len(ids):,} punten "
          f"({n_notulen} met notulentekst, {n_vraag_pdf} vraag-pdf's, {n_college} college-uittreksels) "
          f"-> zoekindex.json ({kb:,.0f} kB)")
    print(f"       boilerplate-drempel: {len(boilerplate):,} woorden geschrapt "
          f"(in >{MAX_PUNTEN_PER_WOORD} punten)")
    if n_vraag_bundel:
        print(f"       bundelgrens: {n_vraag_bundel} vraag-pdf('s) niet geïndexeerd "
              f"(boven {VRAAG_PDF_MAX:,} tekens)")


if __name__ == "__main__":
    main()
