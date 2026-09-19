"""
koppel_uittreksels.py — verwerkt de opgehaalde uittreksels/bijlagen (fetch_uittreksels.py):
  1) extraheert de VOLLEDIGE tekst per stuk (pdfplumber) → data/uittreksel_cache/ (git-genegeerd);
  2) koppelt elk stuk aan een agendapunt/collegebesluit, per zitting:
       - UITTREKSEL: aan het punt met het nummer uit zijn eigen kopregel ("13. FINANCIËN-..."),
         dat de stad er zelf in zet, met een titelcontrole erbovenop. Bestaat dat punt niet, dan
         blijft het uittreksel los. Pas zonder nummer: aan het besluit met die titel (exact en
         uniek, of minstens twee gedeelde woorden met een duidelijke winnaar). Verrijkt de
         brontekst van dat besluit, dus een fout hier geeft een foute samenvatting.
       - BIJLAGE (reglement, belasting, ...): volgt het uittreksel met hetzelfde publicatie-id in
         dezelfde zitting. Geen woordoverlap: die gaf ooit een cultuurpunt het parkeerreglement
         van een ander punt. Zo wordt de bijlage-tekst doorzoekbaar via het eigen punt.
     De tekst zoekt het script op URL, niet op publicatie-id: dat id draagt het uittreksel en al
     zijn bijlagen. build.py (klep 3e) leest de koppeltabel na vóór er iets live gaat.
  3) schrijft de koppeltabel data/uittreksel_koppeling.json (git-genegeerd) en voegt aan de
     rechtstreeks gekoppelde besluiten in data.json een 'uittreksel_url' toe (link, GEEN tekst).

De volledige tekst blijft in de lokale cache en voedt straks de tagging + zoekindex; enkel
afgeleide data (samenvatting, kernbegrippen, de url) komt in de gecommitte bestanden.

Draai:  python koppel_uittreksels.py            (verwerkt alles + schrijft)
        python koppel_uittreksels.py --meetlat   (enkel de koppel-cijfers; vult hooguit de tekstcache aan)
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

import os, re, json, hashlib, unicodedata
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent
INDEX = BASE / "data" / "uittreksels_index.json"
CACHE = BASE / "data" / "uittreksel_cache"
KOPPEL = BASE / "data" / "uittreksel_koppeling.json"
DATA = BASE / "data.json"

SLUGO = {"College van burgemeester en schepenen": "college", "Gemeenteraad": "gemeenteraad",
         "Raad voor maatschappelijk welzijn": "rmw", "Burgemeester": "burgemeester", "Vast bureau": "vast"}
SLUGMAP_RAW = {"College van burgemeester en schepenen": "college_van_burgemeester_en_schepenen",
               "Gemeenteraad": "gemeenteraad", "Raad voor maatschappelijk welzijn": "raad_voor_maatschappelijk_welzijn",
               "Burgemeester": "burgemeester", "Vast bureau": "vast_bureau"}

def norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r'^\s*uittreksel\s*[-–:]\s*', '', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

# Generieke bestuurswoorden die géén onderwerp dragen: een toevallige match hierop
# (bv. 'Vlaams Belang' ~ 'algemeen belang') mag geen koppeling maken.
STOP = {"belang", "mechelen", "mechelse", "stedelijk", "stedelijke", "betreffende",
        "houdende", "beslissing", "beslissingslijst", "besluit", "besluiten",
        "goedkeuring", "vaststelling", "aanvullend", "toepassing", "algemeen"}

def woorden(n):
    return [w for w in n.split() if len(w) >= 5 and w not in STOP]

def overlap(a, b):
    """Aantal betekenisvolle (niet-generieke) woorden dat A en B delen; prefix-match vangt
    buiging op ('belasting' ~ 'belastingreglementen'). Elk woord telt één keer: een titel die
    hetzelfde woord twee keer herhaalt, kocht vroeger een tweede punt zonder meer onderwerp te delen."""
    wa, wb = set(woorden(a)), set(woorden(b))
    return sum(1 for x in wa if any(x == y or y.startswith(x) or x.startswith(y) for y in wb))


# De puntregel van een uittreksel staat na de aanwezigheidslijst, in de vorm "13. FINANCIËN-
# BELASTINGEN. ...", "7 D. Bestuurlijk Beheer. ..." of "7E Bestuurlijk Beheer. ...". Dat nummer zet
# de stad er zelf in: het is de betrouwbaarste sleutel naar het agendapunt. Een deelpunt ("1)
# Goedkeuring ...") telt niet. Enkel de kop wordt gelezen: verderop genummerde artikels of
# opsommingen zouden anders voor een puntnummer doorgaan.
KOP_NR = re.compile(r"^\s*(\d{1,3})\s*(?:([A-Z])\s*\.?|\.)\s+[A-ZÀ-Þ][A-Za-zÀ-ÿ]{2,}")
KOP_REGELS = 25


def kopregel_nummer(tekst):
    for regel in (tekst or "").splitlines()[:KOP_REGELS]:
        m = KOP_NR.match(regel)
        if m:
            return int(m.group(1)), (m.group(2) or "")
    return None, None


def puntnummer_uit_id(item_id):
    """(nummer, letter) uit een id als 'college-20260217-7D'. Een tweede punt met hetzelfde nummer
    draagt een staart met een hash ('college-20260331-7-06de99') en telt als hetzelfde nummer."""
    m = re.search(r"-(\d{8})-(\d+)([A-Z]*)(?:-[0-9a-f]{6})?$", item_id)
    return (int(m.group(2)), m.group(3) or "") if m else (None, None)


def beste_titel(kand, nt, minimum):
    """Het punt uit kand [(genorm.titel, id)] waarvan de titel past: exact en uniek, anders
    minstens `minimum` gedeelde woorden met een duidelijke winnaar. Twee punten met dezelfde
    titel of een gelijkspel beslissen niets: dan (None, 'geen')."""
    exact = [pid for cn, pid in kand if cn == nt]
    if len(exact) == 1:
        return exact[0], "exact"
    if len(exact) > 1:
        return None, "geen"
    scored = sorted(((overlap(cn, nt), pid) for cn, pid in kand), reverse=True)
    if scored and scored[0][0] >= minimum and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return scored[0][1], "overlap"
    return None, "geen"


def schrijf_json(pad, obj):
    """Eerst naar een tijdelijk bestand, dan in één beweging vervangen: een onderbroken run laat
    nooit een half geschreven data.json achter."""
    tmp = pad.with_name(pad.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, pad)

def zitting_van_id(pid):
    m = re.match(r'[a-z]+[-_]?(\d{8})', pid)
    if not m: return None
    d = m.group(1); return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

def bestandsnaam(doc):
    """Naam van het pdf-bestand van een document uit de uittreksel-index. Eén functie voor beide
    kanten: fetch_uittreksels.py schrijft onder deze naam (via pdf_pad), de koppeling leest ze. Een
    uittreksel heet naar zijn publicatie-id. Een bijlage deelt dat id met haar uittreksel en soms met
    andere bijlagen, dus draagt ze er een stukje hash van haar url bij: met enkel het id schreef de
    download per id maar één bijlage weg, en las elke andere bijlage met dat id de tekst van dat ene
    bestand (aanleiding 19/09/2026). Zonder id: een hash van de url."""
    url_hash = hashlib.sha1(doc["url"].encode()).hexdigest()
    if not doc["id"]:
        return f"{doc['klasse']}_{url_hash[:10]}.pdf"
    if doc["klasse"] == "bijlage":
        return f"bijlage_{doc['id']}_{url_hash[:8]}.pdf"
    return f"{doc['klasse']}_{doc['id']}.pdf"

def pdf_pad(doc):
    slug = SLUGMAP_RAW.get(doc["orgaan"], "")
    return BASE / "data" / "raw" / slug / doc["zitting"] / "uittreksels" / bestandsnaam(doc)

def pdf_tekst(pad):
    st = pad.stat()
    sleutel = hashlib.sha1(f"{pad.as_posix()}|{st.st_mtime_ns}|{st.st_size}".encode()).hexdigest()
    c = CACHE / (sleutel + ".txt")
    if c.exists():
        return sleutel, c.read_text(encoding="utf-8")
    import pdfplumber
    with pdfplumber.open(pad) as pdf:
        tekst = "\n".join((p.extract_text() or "") for p in pdf.pages)
    CACHE.mkdir(parents=True, exist_ok=True)
    c.write_text(tekst, encoding="utf-8")
    return sleutel, tekst

def main():
    meetlat = "--meetlat" in sys.argv
    if not INDEX.exists():
        sys.exit("data/uittreksels_index.json ontbreekt — draai eerst fetch_uittreksels.py --download")
    docs = json.loads(INDEX.read_text(encoding="utf-8"))
    data = json.loads(DATA.read_text(encoding="utf-8"))

    # besluiten per (slug, zitting) → [(genorm.titel, id)]
    idx = defaultdict(list)
    for coll in ("college_beslissingen", "agendapunten"):
        for p in data.get(coll, []):
            m = re.match(r'(college|gemeenteraad|rmw|burgemeester|vast)', p["id"])
            if m:
                idx[(m.group(1), zitting_van_id(p["id"]))].append((norm(p.get("titel", "")), p["id"]))

    koppeling = defaultdict(list)   # item_id → [{uittreksel_id, url, klasse, cache_sleutel}]
    directe_url = {}                # item_id → url (enkel voor rechtstreekse uittreksel-match)
    stat = defaultdict(lambda: defaultdict(int))
    per_url = {d["url"]: d for d in docs}
    # Punten per (orgaan, zitting, puntnummer, letter). Een lijst, want een nummer kan twee keer
    # voorkomen ('3' en '03', of een tweede punt 7 met een hash-staart): dan beslist de titel.
    per_nummer = defaultdict(list)
    for (slug, zit), lijst in idx.items():
        for cn, pid in lijst:
            nr, lt = puntnummer_uit_id(pid)
            if nr is not None:
                per_nummer[(slug, zit, nr, lt)].append((cn, pid))

    # PAS 1: uittreksels. Het puntnummer uit de EIGEN kopregel beslist (de stad zet het er zelf in),
    # met minstens één gedeeld woord of een exacte titel als controle. Staat er een nummer maar
    # bestaat dat punt niet, dan geen gok op de titel: zo belandde het uittreksel van punt 7E ooit
    # op punt 7D. Pas zonder nummer telt de titel: exact en uniek, of minstens twee gedeelde woorden
    # met een duidelijke winnaar. Een fout hier geeft een foute samenvatting, dus streng.
    uid_naar_punt = {}
    los = []
    for d in docs:
        if d["klasse"] != "uittreksel":
            continue
        slug = SLUGO[d["orgaan"]]
        kand = idx.get((slug, d["zitting"]), [])
        nt = norm(d["titel"])
        gekoppeld, soort, nr, lt = None, "geen", None, None
        pad = pdf_pad(d)
        if pad.exists():
            try:
                _sl, tekst = pdf_tekst(pad)
                nr, lt = kopregel_nummer(tekst)
            except Exception as e:
                print(f"  (tekst mislukt {d['id']}: {e})")
        if nr is not None:
            gekoppeld, _s = beste_titel(per_nummer.get((slug, d["zitting"], nr, lt), []), nt, 1)
            soort = "puntnummer" if gekoppeld else "nummer zonder passend punt"
        elif kand:
            gekoppeld, soort = beste_titel(kand, nt, 2)
            if gekoppeld:
                soort = "titel " + soort
        stat["uittreksel"][soort] += 1
        if not gekoppeld:
            los.append(f"{d['id']} ({d['zitting']}, kop {nr}{lt or ''})" if nr is not None else f"{d['id']} ({d['zitting']})")
            continue
        uid_naar_punt[(slug, d["zitting"], d["id"])] = gekoppeld
        if not meetlat:
            koppeling[gekoppeld].append({"uittreksel_id": d["id"], "url": d["url"], "klasse": "uittreksel"})
            directe_url[gekoppeld] = d["url"]

    # PAS 2: een bijlage hoort bij het punt van het uittreksel met hetzelfde publicatie-id, van
    # hetzelfde orgaan en in dezelfde zitting (de stad hangt ze samen onder één id). Geen woordoverlap
    # meer: die gaf het cultuurpunt 46 het parkeerreglement van punt 22, en zonder eigen uittreksel
    # werd dat de bron van zijn samenvatting.
    for d in docs:
        if d["klasse"] != "bijlage":
            continue
        gekoppeld = uid_naar_punt.get((SLUGO[d["orgaan"]], d["zitting"], d["id"]))
        stat["bijlage"]["via uittreksel" if gekoppeld else "geen"] += 1
        if gekoppeld and not meetlat:
            koppeling[gekoppeld].append({"uittreksel_id": d["id"], "url": d["url"], "klasse": "bijlage"})
    if los:
        print(f"uittreksels zonder punt ({len(los)}): " + "; ".join(los[:10]))

    for kl in stat:
        r = stat[kl]; tot = sum(r.values())
        print(f"{kl}: {tot} | " + " · ".join(f"{k} {v}" for k, v in sorted(r.items())))
    if meetlat:
        print("(meetlat-modus: niets weggeschreven)"); return

    # tekst extraheren voor alle gekoppelde stukken (→ cache), cache-sleutel in de koppeltabel
    # Tekst opzoeken op URL, NIET op publicatie-id: dat id draagt het uittreksel en al zijn bijlagen,
    # en een opzoeking op id gaf 137 stukken de tekst van een bijlage in plaats van hun besluit.
    n_tekst = 0
    for item_id, refs in koppeling.items():
        for ref in refs:
            doc = per_url.get(ref["url"])
            pad = pdf_pad(doc) if doc else None
            if pad and pad.exists():
                try:
                    sleutel, t = pdf_tekst(pad)
                    ref["cache_sleutel"] = sleutel
                    if t.strip(): n_tekst += 1
                except Exception as e:
                    print(f"  (tekst mislukt {ref['uittreksel_id']}: {e})")
    # url toevoegen aan de rechtstreeks gekoppelde besluiten (geen tekst!)
    aantal_url = 0
    for coll in ("college_beslissingen", "agendapunten"):
        for p in data.get(coll, []):
            if p["id"] in directe_url:
                p["uittreksel_url"] = directe_url[p["id"]]; aantal_url += 1
            else:
                p.pop("uittreksel_url", None)   # geen eigen uittreksel meer: geen link naar dat van een ander
    schrijf_json(KOPPEL, koppeling)
    schrijf_json(DATA, data)
    tot_refs = sum(len(v) for v in koppeling.values())
    print(f"\ngekoppelde besluiten: {len(koppeling)} | documenten gekoppeld: {tot_refs} | "
          f"tekst gecachet: {n_tekst} | uittreksel-url toegevoegd: {aantal_url}")
    print("koppeltabel → data/uittreksel_koppeling.json (git-genegeerd)")

if __name__ == "__main__":
    main()
