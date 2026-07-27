"""
koppel_uittreksels.py — verwerkt de opgehaalde uittreksels/bijlagen (fetch_uittreksels.py):
  1) extraheert de VOLLEDIGE tekst per stuk (pdfplumber) → data/uittreksel_cache/ (git-genegeerd);
  2) koppelt elk stuk aan een agendapunt/collegebesluit, per zitting:
       - UITTREKSEL (link 'Uittreksel - <titel>'): aan het besluit met die titel (exact of beste
         stam-overlap). Verrijkt de brontekst van dat besluit.
       - BIJLAGE (reglement, belasting, ...): sub-document; aan het besluit van dezelfde zitting
         met de hoogste stam-overlap (bv. 'belasting op nachtwinkels' → 'Hernieuwing
         belastingreglementen'). Zo wordt de bijlage-tekst doorzoekbaar via dat punt.
  3) schrijft de koppeltabel data/uittreksel_koppeling.json (git-genegeerd) en voegt aan de
     rechtstreeks gekoppelde besluiten in data.json een 'uittreksel_url' toe (link, GEEN tekst).

De volledige tekst blijft in de lokale cache en voedt straks de tagging + zoekindex; enkel
afgeleide data (samenvatting, kernbegrippen, de url) komt in de gecommitte bestanden.

Draai:  python koppel_uittreksels.py            (verwerkt alles + schrijft)
        python koppel_uittreksels.py --meetlat   (enkel de koppel-cijfers, wijzigt niets)
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

import re, json, hashlib, unicodedata
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
    buiging op ('belasting' ~ 'belastingreglementen')."""
    wa, wb = woorden(a), woorden(b)
    return sum(1 for x in wa if any(x == y or y.startswith(x) or x.startswith(y) for y in wb))

def zitting_van_id(pid):
    m = re.match(r'[a-z]+[-_]?(\d{8})', pid)
    if not m: return None
    d = m.group(1); return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

def pdf_pad(doc):
    slug = SLUGMAP_RAW.get(doc["orgaan"], "")
    stam = doc["id"] or hashlib.sha1(doc["url"].encode()).hexdigest()[:10]
    return BASE / "data" / "raw" / slug / doc["zitting"] / "uittreksels" / f"{doc['klasse']}_{stam}.pdf"

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
    for d in docs:
        kand = idx.get((SLUGO[d["orgaan"]], d["zitting"]), [])
        nt = norm(d["titel"])
        gekoppeld = None
        if kand:
            exact = [pid for cn, pid in kand if cn == nt]
            if exact and d["klasse"] == "uittreksel":
                gekoppeld = exact[0]
            else:
                # Uittreksel verrijkt de brontekst van één besluit → streng (fout = foute
                # samenvatting): minstens 2 gedeelde woorden. Bijlage voedt enkel de zoek → los.
                drempel = 2 if d["klasse"] == "uittreksel" else 1
                scored = sorted(((overlap(cn, nt), pid) for cn, pid in kand), reverse=True)
                if scored and scored[0][0] >= drempel and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                    gekoppeld = scored[0][1]     # duidelijke beste overlap
        soort = "geen"
        if gekoppeld:
            soort = "exact" if (d["klasse"] == "uittreksel" and norm(next(cn for cn, pid in kand if pid == gekoppeld)) == nt) else "overlap"
        stat[d["klasse"]][soort] += 1
        if gekoppeld and not meetlat:
            koppeling[gekoppeld].append({"uittreksel_id": d["id"], "url": d["url"], "klasse": d["klasse"]})
            if d["klasse"] == "uittreksel":
                directe_url[gekoppeld] = d["url"]

    for kl in stat:
        r = stat[kl]; tot = sum(r.values())
        print(f"{kl}: {tot} | exact {r['exact']} · overlap {r['overlap']} · geen {r['geen']}")
    if meetlat:
        print("(meetlat-modus: niets weggeschreven)"); return

    # tekst extraheren voor alle gekoppelde stukken (→ cache), cache-sleutel in de koppeltabel
    per_id = {d["id"]: d for d in docs}
    n_tekst = 0
    for item_id, refs in koppeling.items():
        for ref in refs:
            doc = per_id.get(ref["uittreksel_id"])
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
    KOPPEL.write_text(json.dumps(koppeling, ensure_ascii=False, indent=2), encoding="utf-8")
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tot_refs = sum(len(v) for v in koppeling.values())
    print(f"\ngekoppelde besluiten: {len(koppeling)} | documenten gekoppeld: {tot_refs} | "
          f"tekst gecachet: {n_tekst} | uittreksel-url toegevoegd: {aantal_url}")
    print("koppeltabel → data/uittreksel_koppeling.json (git-genegeerd)")

if __name__ == "__main__":
    main()
