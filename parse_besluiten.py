"""
parse_besluiten.py — STAP 10: zet een besluitenlijst-PDF om naar gestructureerde
agendapunten (één record per beslissing).

Belangrijk: gebruikt pdfplumber. pypdf geeft op deze PDF's vervormde tekst
(Identity-H lettertype-codering), pdfplumber en pdftotext niet.

Wat het uit elke beslissing haalt:
  - nummer        (1, 2, … of 'TP01' voor toegevoegde punten)
  - categorie     (het domein in hoofdletters: MOBILITEIT, FINANCIËN-BELASTINGEN, …)
  - titel         (de beslissingstekst)
  - resultaat     (Goedgekeurd / Bekrachtigd / Vastgesteld / …)
  - zitting       ('openbaar' of 'besloten')
  - aanvullend    (True voor toegevoegde punten, art. 21 Decreet Lokaal Bestuur)
  - indiener      (enkel bij toegevoegde punten, bv. 'S. Van Rompaey')

De rijkere velden (mensentaal-samenvatting, thema's, straten, bevoegde schepen)
komen in de AI-tagging-stap erna; dit script levert het geraamte.

Draai:  python parse_besluiten.py pad/naar/besluiten.pdf
(eenmalig: python -m pip install pdfplumber)
"""

# Robuuste console-uitvoer: zet stdout/stderr op UTF-8, zodat print() met niet-ASCII
# (pijlen, vinkjes) niet crasht op een Windows-console die standaard cp1252 gebruikt.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


import re
import sys
import json
from pathlib import Path

import pdfplumber

# Een nieuw agendapunt begint met een puntnummer. Vier vormen komen voor (nagemeten over de 338
# besluitenlijsten):
#   · gewoon           "1. " / "13A. "
#   · college-subpunt  "14 A. " (punt 14, deelbeslissing A, met een SPATIE, vandaar apart)
#   · raad-punttype    "TP01. " (toegevoegd) / "HP01. " (politieverordening) / "ACT01. " (actualiteitsdebat)
#   · zonder punt      "7E Bestuurlijk Beheer. " (college: de punt na het nummer ontbreekt)
# Zonder de raad-punttypes smolten die deelbeslissingen mee in het vorige punt (bv. een
# onteigening die de politieverordeningen HP01/HP02 opslokte).
ITEM = re.compile(r'^(\d+[A-Za-z]?|\d+ [A-Z]|[A-Z]{2,4}\d{1,3})\.\s+(.*)$')
# De vierde vorm: de punt na het nummer ontbreekt. Zonder deze regel verdween zo'n punt in het
# vorige, met zijn tekst achter diens titel (collegepunt 7E van 17/02/2026 hing achter 7D). Smal
# gehouden, want ook een afgebroken titelregel kan met een nummer beginnen ("1A Voorbereidende
# sloop- en rioleringswerken’."): geen voorloopnul, dan een rubriek van enkel letters, spaties,
# &, - en haakjes, haar punt, en daarna nog tekst. _volgt_op eist bovendien dat het nummer het
# volgende in de reeks is.
ITEM_ZONDER_PUNT = re.compile(r'^([1-9]\d{0,2}[A-Z]?) ([A-Z](?:[^\W\d_]|[ &()\-]){1,60}\.\s+\S.*)$')
KOP = re.compile(r'^([^.]+?)\.\s+(.*)$')                     # "Domein. titel..." (HOOFDLETTERS of Title Case)
NOISE = re.compile(
    r'^(Beslissing(?:en|s)?lijst\s+\d+$'        # paginakop, bv. "Beslissingenlijst 2"
    r'|Beslissingslijst toezicht\b'             # oude gemeenteraad-kop
    r'|BESLISSING(?:EN|S)?LIJST$'               # titelkop "BESLISSINGENLIJST"
    r'|STAD MECHELEN|OCMW MECHELEN'             # bestuurseenheid-koppen (college / vast bureau)
    r'|TOEZICHT$'
    r'|\d{1,2} \w+ \d{4}$)'                     # losse datumregel "26 mei 2026"
)

# De scheidingszin voor toegevoegde punten (art. 21 Decreet Lokaal Bestuur). De PDF breekt
# haar over twee regels, en ze staat er in drie schrijfwijzen, geteld over 310 stukken:
#   "Volgende punten werden … bestuur"    + "aan de agenda toegevoegd:"   7x
#   "Volgend punt werd … bestuur aan"     + "de agenda toegevoegd:"       2x
#   "Volgende punt werd … bestuur aan"    + "de agenda toegevoegd:"       1x
# Alleen de eerste werd herkend. Bij de twee enkelvoudige viel de staart door naar het
# volgende blok, waar sluit() de laatste regel blind tot resultaat maakt. Zo stond bij
# drie beslissingen "de agenda toegevoegd:" als uitkomst op de site.
SCHEIDING = re.compile(r'^volgende?\s+punt(en)?\s+werd(en)?\b')
# De staart, verankerd op begin én einde: een beslissingstitel die toevallig over de
# agenda gaat, mag hier niet in lopen.
SCHEIDING_STAART = re.compile(r'^(aan\s+)?de agenda toegevoegd\s*:?\s*$')

# Een echt puntnummer is een ordinaal (1, 2, … 13A, 14 A, TP01, HP01, ACT01, en zonder punt 7E); een
# afgebroken jaartal ("2027.") is dat niet, en een titelfragment met een letter-code ("...GAS2 en
# GAS3. Verwijzing...") evenmin. Voor een nummer zonder punt geldt daarnaast _volgt_op.
def _is_echt_punt(nummer: str, rest: str) -> bool:
    cijfers = re.match(r'\d+', nummer)
    if cijfers and int(cijfers.group(0)) >= 1000:           # vier cijfers of meer = afgebroken jaartal
        return False
    # Een letter-prefix (TP/HP/ACT/…) telt alleen als er een categorie in HOOFDLETTERS achter staat
    # (TOEGEVOEGD PUNT, POLITIEVERORDENINGEN, ACTUALITEITSDEBAT). Zo splitst een titelfragment als
    # "…protocol GAS2 en GAS3. Verwijzing naar de gemeenteraad…" niet per ongeluk in een vals punt.
    if re.match(r'^[A-Z]{2,4}\d', nummer):
        return bool(re.match(r'[A-Z]{2,}', rest))
    return True


# Een nummer zonder punt opent enkel een punt als het rechtstreeks op het vorige punt volgt:
# 7E na 7D, 7A na 7, 11 na 10 of 10C. Een titelregel die toevallig met een nummer en een
# woord met punt begint, valt zo bijna nooit precies op het volgende nummer.
def _volgt_op(vorig, nummer: str) -> bool:
    v = re.fullmatch(r'(\d+)([A-Za-z]?)', vorig or "0")
    n = re.fullmatch(r'(\d+)([A-Za-z]?)', nummer)
    if not v or not n:                                      # na TP01/HP01/ACT01: geen reeks
        return False
    v_nr, v_letter = int(v.group(1)), v.group(2).upper()
    n_nr, n_letter = int(n.group(1)), n.group(2).upper()
    if n_letter:                                            # deelpunt: zelfde nummer, volgende letter
        verwacht = chr(ord(v_letter) + 1) if v_letter else "A"
        return n_nr == v_nr and n_letter == verwacht
    return n_nr == v_nr + 1


def _puntkop(regel: str, vorig):
    """(nummer, rest) als de regel een nieuw punt opent, anders None."""
    m = ITEM.match(regel)
    if m and _is_echt_punt(m.group(1), m.group(2)):        # geen jaartal/fragment
        return m.groups()
    m = ITEM_ZONDER_PUNT.match(regel)
    if m and _is_echt_punt(m.group(1), m.group(2)) and _volgt_op(vorig, m.group(1)):
        return m.groups()
    return None


def extract_text(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def parse(pdf_path, text=None):
    if text is None:
        text = extract_text(pdf_path)
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # negeer alles tot de eerste echte sectie
    try:
        start = next(i for i, l in enumerate(lines) if l.lower().startswith("openbare zitting"))
    except StopIteration:
        start = 0
    lines = [l for l in lines[start:] if not NOISE.match(l)]

    items, blok, meta = [], [], None
    zitting = "openbaar"
    vorig = None                                            # nummer van het laatst geopende punt

    def sluit():
        nonlocal blok, meta
        if meta and blok:
            *titelregels, resultaat = blok if len(blok) > 1 else blok + [""]
            titel = " ".join(titelregels).strip()
            items.append({**meta, "titel": titel, "resultaat": resultaat.strip()})
        blok, meta = [], None

    for l in lines:
        low = l.lower()
        if low.startswith("openbare zitting"):
            sluit(); zitting = "openbaar"; continue
        if low.startswith("besloten zitting"):
            sluit(); zitting = "besloten"; continue
        # Scheidingszin voor toegevoegde punten: beide regels ervan sluiten het blok en
        # horen zelf nergens bij. Zie SCHEIDING hierboven voor waarom het er twee zijn.
        if SCHEIDING.match(low) or SCHEIDING_STAART.match(low):
            sluit(); continue

        kop = _puntkop(l, vorig)
        if kop:                                             # nieuw punt begint
            sluit()
            nummer, rest = kop
            nummer = re.sub(r'^(\d+) ([A-Z])$', r'\1\2', nummer)   # '14 A' -> '14A': schone, spatieloze id
            vorig = nummer
            categorie, titel_start, indiener = "", rest, None
            k = KOP.match(rest)
            if k:
                categorie, titel_start = k.group(1).strip(), k.group(2).strip()
            # 'toegevoegd' en 'actuadebat' worden afgeleid uit het punt zelf, niet uit de tussenzin
            aanvullend = nummer.startswith("TP") or categorie.upper() == "TOEGEVOEGD PUNT"
            actuadebat = nummer.startswith("ACT") or categorie.upper() == "ACTUALITEITSDEBAT"
            if aanvullend:
                categorie = ""                              # 'toegevoegd punt' is geen domein
                if " - " in titel_start:                    # "Naam - onderwerp"
                    indiener, titel_start = [x.strip() for x in titel_start.split(" - ", 1)]
            if actuadebat:
                categorie = ""                              # 'actualiteitsdebat' is geen domein
            meta = {"nummer": nummer, "categorie": categorie, "zitting": zitting,
                    "aanvullend": aanvullend, "indiener": indiener,
                    "type": "actuadebat" if actuadebat else ("toegevoegd" if aanvullend else "gewoon")}
            blok = [titel_start]
        elif meta is not None:
            blok.append(l)
    sluit()
    return items


# Klep op de geparste punten, voor er iets wordt weggeschreven: een gemiste of valse puntkop zet een
# verkeerde titel en samenvatting live. Aanleiding: op 19/09/2026 hing collegepunt 7E van 17/02/2026
# achter 7D, en stonden er spookpunten uit afgebroken titelregels op de site. Zes toetsen:
#   · gat        een nummer, of een letter in een deelreeks, ontbreekt (bij 7D en 7F hoort 7E), of
#                een deelpunt mist zijn hoofdpunt (bij 11A hoort 11).
#   · dubbel     een nummer komt twee keer voor.
#   · volgorde   een nummer daalt binnen de openbare of de besloten zitting.
#   · resultaat  het resultaat is geen gekende uitkomst (UITKOMSTEN). Een gemiste of valse kop
#                schuift een titelregel in het resultaat; enkel een geschrapt punt heeft er geen.
#   · opgeslokt  een titel bevat een uitkomst met daarna een puntnummer en een hoofdletter: de kop
#                van het volgende punt liep mee in de titel. Die toets ziet ook een gemist laatste
#                punt, dat geen gat achterlaat.
#   · punttype   de tekst telt meer koppen met ACTUALITEITSDEBAT, TOEGEVOEGD PUNT of
#                POLITIEVERORDENINGEN dan er punten van dat type zijn; een gemiste kop voor punt 1
#                laat geen ander spoor na.
# Een melding stopt de pijplijn. Staat de afwijking echt zo in de besluitenlijst, zet de melding dan
# letterlijk in BEKENDE_AFWIJKINGEN, onder "<orgaanmap>/<zitting>". Gebruikt de stad een nieuwe
# uitkomst, zet ze dan in UITKOMSTEN.
UITKOMSTEN = {
    "Goedgekeurd", "Goedgekeurd na amendering", "Aktename", "Kennisname", "Vastgesteld",
    "Bekrachtigd", "Gunstig geadviseerd", "Verworpen", "Niet ter stemming gelegd", "Verdaagd",
    "Afgevoerd van agenda", "Verkozen verklaring", "Aangesteld", "Geschrapt",
    "Bespreking ter zitting", "Behandeld ter zitting",
}
SLIK = re.compile(r'\b(?:' + '|'.join(sorted(map(re.escape, UITKOMSTEN), key=len, reverse=True)) +
                  r')\s+(?:\d{1,3} ?[A-Z]?|[A-Z]{2,4}\d{1,3})(?:[.:)]\s*|\s+)[A-Z]')
TYPEKOP = re.compile(r'^(?:[A-Z]{2,4}\d{0,3}|\d{1,3} ?[A-Z]?)\.?\s*'
                     r'(ACTUALITEITSDEBAT|TOEGEVOEGD PUNT|POLITIEVERORDENINGEN)\.')
BEKENDE_AFWIJKINGEN = {
    # 18A en 18B hebben in de bron geen resultaatregel.
    "college_van_burgemeester_en_schepenen/2026-08-18": {
        "18A: resultaat 'naar aanleiding van Steragas Vlaams-Brabant Classic op 13 september 2026.'",
        "18B: resultaat 'naar aanleiding van Crossweekend op 10 oktober 2026.'",
    },
    # Het resultaat van punt 2 loopt in de bron over twee regels.
    "gemeenteraad/2025-10-21": {
        "2: resultaat 'Niet toekenning titel 1 ereraadslid.'",
    },
    # Tijdelijk, tot _is_titelvervolg in parse() staat: spookpunt uit een titelregel.
    "college_van_burgemeester_en_schepenen/2026-03-31": {
        "7 dubbel",
        "7 na 50",
        "50: resultaat 'loten 2, 3, 4 en 8. Goedkeuring tot overdracht naar openbaar domein van lot'",
    },
    # Tijdelijk, tot _is_titelvervolg in parse() staat: spookpunt uit een titelregel.
    "college_van_burgemeester_en_schepenen/2026-05-12": {
        "03 dubbel",
        "03 na 11",
        "11: resultaat ''",
    },
    # Tijdelijk, tot _is_titelvervolg in parse() staat: spookpunt uit een titelregel.
    "vast_bureau/2026-02-10": {
        "5 na 6",
        "4: resultaat 'de niet-toegewezen loten van de openbare verkopen van gronden deel 1 t.e.m.'",
    },
    # Tijdelijk, tot ITEM_PUNTTYPE in _puntkop() staat: gemist actualiteitsdebat.
    "gemeenteraad/2025-09-16": {
        "ACTUALITEITSDEBAT: 1 koppen, 0 punten",
    },
    # Tijdelijk, tot ITEM_PUNTTYPE in _puntkop() staat: gemist actualiteitsdebat.
    "gemeenteraad/2026-01-26": {
        "ACTUALITEITSDEBAT: 2 koppen, 0 punten",
    },
}


def _reeks(nummer):
    m = re.fullmatch(r'(\d+)([A-Za-z]?)', nummer)
    return (int(m.group(1)), m.group(2).upper()) if m else None


def controleer(items, tekst):
    """De meldingen van de klep voor een besluitenlijst; leeg als alles klopt."""
    meldingen = []
    # gat
    hoofd, letters = set(), {}
    for it in items:
        r = _reeks(it["nummer"])
        if r and r[1]:
            letters.setdefault(r[0], set()).add(r[1])
        elif r:
            hoofd.add(r[0])
    hoogste = max(hoofd | set(letters), default=0)
    meldingen += [f"{nr} ontbreekt" for nr in range(1, hoogste + 1) if nr not in hoofd and nr not in letters]
    for nr, reeks in sorted(letters.items()):
        if nr not in hoofd:
            meldingen.append(f"{nr}{min(reeks)} zonder {nr}")
        verwacht = {chr(c) for c in range(ord("A"), ord(max(reeks)) + 1)}
        meldingen += [f"{nr}{x} ontbreekt" for x in sorted(verwacht - reeks)]
    # dubbel en volgorde
    gezien, vorig, deel = set(), None, None
    for it in items:
        r = _reeks(it["nummer"]) or it["nummer"]
        if r in gezien:
            meldingen.append(f"{it['nummer']} dubbel")
        gezien.add(r)
        if it["zitting"] != deel:
            vorig, deel = None, it["zitting"]
        if isinstance(r, tuple):
            if vorig and r < vorig[0]:
                meldingen.append(f"{it['nummer']} na {vorig[1]}")
            vorig = (r, it["nummer"])
    # resultaat en opgeslokt
    for it in items:
        res, titel = it["resultaat"], it["titel"]
        geschrapt = titel.upper().startswith("GESCHRAPT")
        if not (res == "" and geschrapt) and not all(d.strip() in UITKOMSTEN for d in res.split(" / ")):
            meldingen.append(f"{it['nummer']}: resultaat {res[:80]!r}")
        m = SLIK.search(titel)
        if m:
            meldingen.append(f"{it['nummer']}: puntkop in de titel {titel[m.start():m.start() + 50]!r}")
    # punttype
    koppen = {}
    for regel in tekst.splitlines():
        m = TYPEKOP.match(regel.strip())
        if m:
            koppen[m.group(1)] = koppen.get(m.group(1), 0) + 1
    punten = {
        "ACTUALITEITSDEBAT": sum(it["type"] == "actuadebat" for it in items),
        "TOEGEVOEGD PUNT": sum(it["type"] == "toegevoegd" for it in items),
        "POLITIEVERORDENINGEN": sum(it["categorie"].upper() == "POLITIEVERORDENINGEN" for it in items),
    }
    for typ, n in sorted(koppen.items()):
        if n > punten[typ]:
            meldingen.append(f"{typ}: {n} koppen, {punten[typ]} punten")
    return meldingen


def main():
    if len(sys.argv) < 2:
        print("Gebruik: python parse_besluiten.py pad/naar/besluiten.pdf"); return
    pdf_path = sys.argv[1]
    tekst = extract_text(pdf_path)
    items = parse(pdf_path, tekst)

    zitting = "/".join(Path(pdf_path).resolve().parts[-3:-1])     # bv. college_van_.../2026-02-17
    meldingen = [m for m in controleer(items, tekst) if m not in BEKENDE_AFWIJKINGEN.get(zitting, set())]
    if meldingen:
        sys.exit(f"[STOP] {zitting}: {'; '.join(meldingen)}. Mist of verzint de parser een puntkop, "
                 "herstel dan parse(); staat het echt zo in de besluitenlijst, zet de melding dan "
                 "in BEKENDE_AFWIJKINGEN.")

    out = Path.cwd() / (Path(pdf_path).stem + ".agendapunten.json")
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    gewoon = [i for i in items if not i["aanvullend"]]
    toegevoegd = [i for i in items if i["aanvullend"]]
    print(f"{len(items)} beslissingen ({len(gewoon)} gewoon, {len(toegevoegd)} toegevoegd) → {out.name}\n")
    for i in items:
        extra = f"  [indiener: {i['indiener']}]" if i["indiener"] else ""
        print(f"{i['nummer']:>4}. [{i['zitting'][:4]}] {i['categorie'][:22]:<22} {i['titel'][:60]} → {i['resultaat']}{extra}")


if __name__ == "__main__":
    main()
