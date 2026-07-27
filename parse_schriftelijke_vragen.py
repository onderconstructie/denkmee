"""
parse_schriftelijke_vragen.py — leest de gedownloade pdf's van de schriftelijke vragen
"buiten de gemeenteraad" uit tot gestructureerde records.

Elke pdf bevat de VRAAG (van een raadslid) en meestal het ANTWOORD van het college.
We halen per stuk: de echte datum (uit de pdf, niet uit de mapnaam), de indiener, het
onderwerp, het nummer, de vraagtekst en het antwoord. Lukt een veld niet, dan valt het
terug op wat in de bestandsnaam staat (nummer, indiener, onderwerp).

Bron-pdf's: data/raw/schriftelijke_vragen/<jaar-maand>/<bestand>.pdf
Output:     data/schriftelijke_vragen.json  (lijst records, klaar voor de assemblage)

Draai:  python parse_schriftelijke_vragen.py     (eenmalig: pip install pdfplumber)
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

import re
import json
import hashlib
from pathlib import Path
from datetime import date

BASE = Path(__file__).parent
RAW = BASE / "data" / "raw" / "schriftelijke_vragen"
OUT = BASE / "data" / "schriftelijke_vragen.json"

MAANDEN = {"januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
           "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11, "december": 12}


def extract_text(pdf_path):
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def schoon(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def parse_datum(text):
    """Indieningsdatum: staat direct na het éérste 'Datum:'/'Datum indiening:'-label, in
    woord- of cijfervorm. We ankeren op het label en lezen de datum er pal achter, zodat een
    datum die later in de vraagtekst opduikt (bv. een verwijzing) niet per ongeluk wint."""
    maanden = "|".join(MAANDEN)
    for m in re.finditer(r"Datum(?:\s+indiening)?\s*:\s*", text):
        rest = text[m.end():m.end() + 40]
        w = re.match(r"(\d{1,2})\s+(" + maanden + r")\s+(20\d{2})", rest, re.I)
        if w:
            try: return date(int(w.group(3)), MAANDEN[w.group(2).lower()], int(w.group(1))).isoformat()
            except (ValueError, KeyError): pass
        n = re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](20\d{2})", rest)
        if n:
            try: return date(int(n.group(3)), int(n.group(2)), int(n.group(1))).isoformat()
            except ValueError: pass
    return None


def parse_uit_bestandsnaam(naam):
    """'048. K. Lauwers - Geluidsschermen Battel - vervolg' -> nummer, indiener, onderwerp."""
    stam = re.sub(r"\.pdf$", "", naam, flags=re.I)
    nummer = None
    m = re.match(r"\s*(\d{1,4})[.\-_\s]+(.*)", stam)
    if m:
        nummer = m.group(1).lstrip("0") or "0"
        stam = m.group(2)
    indiener, onderwerp = None, schoon(stam.replace("-", " "))
    if " - " in stam:
        indiener, onderwerp = [schoon(x) for x in stam.split(" - ", 1)]
    return nummer, indiener, onderwerp


def parse_pdf(pdf_path, jaar_maand):
    text = extract_text(pdf_path)
    f_nummer, f_indiener, f_onderwerp = parse_uit_bestandsnaam(pdf_path.name)

    indiener = None
    m = re.search(r"Indiener\s*:\s*(.+?)\s*(?:Datum|Onderwerp|\n)", text)
    if m: indiener = schoon(m.group(1))

    onderwerp = None
    m = re.search(r"Onderwerp(?:\s+vraag)?\s*:\s*(.+?)(?:\n|Toelichtende|$)", text)
    if m: onderwerp = schoon(m.group(1)).rstrip(". ;,")

    nummer = f_nummer
    m = re.search(r"\b(20\d{2})\s*/\s*(\d{2,4})\b", text)
    if m: nummer = f"{m.group(1)}/{m.group(2)}"

    datum = parse_datum(text) or (jaar_maand + "-01")   # terugval: 1e van de uploadmaand

    # Vraag: tussen 'Toelichtende nota' en 'Naam' (of het antwoord-blok).
    vraag = None
    m = re.search(r"Toelichtende nota\s*(.+?)(?:\n\s*Naam\s*:|SCHRIFTELIJK ANTWOORD|Antwoord college|$)", text, re.S | re.I)
    if m: vraag = schoon(m.group(1))
    # Terugval voor pdf's met een andere sectiekop ('Context', 'Vragen', 'Toelichting' i.p.v.
    # 'Toelichtende nota'): neem de tekst tussen die kop en het antwoord-/ondertekeningsblok.
    if not vraag:
        m = re.search(r"\n\s*(?:Toelichting|Context|Vragen)\s*\n(.+?)(?:\n\s*Naam\s*:|SCHRIFTELIJK ANTWOORD|Antwoord\s+college|Met vriendelijke groet|$)", text, re.S | re.I)
        if m: vraag = schoon(m.group(1))

    # Antwoord van het college: na 'Antwoord college' tot de ondertekening ('Mechelen, <datum>').
    antwoord = None
    m = re.search(r"Antwoord\s+college\s*(.+?)(?:\nMechelen,|\nOpdracht burgemeester|Gert Eeraerts|$)", text, re.S | re.I)
    if m: antwoord = schoon(m.group(1))

    ind = indiener or f_indiener or "een raadslid"
    onderw = (onderwerp or f_onderwerp or "Schriftelijke vraag").strip()
    # Leading-leesteken wegpoetsen ('Toelichtende nota:' laat soms een losse ': ' achter).
    vr = re.sub(r"^[\s:–—.\-]+", "", (vraag or "").strip())
    antw = re.sub(r"^[\s:–—.\-]+", "", (antwoord or "").strip())

    # brontekst = wat de tagging samenvat en de zoekbalk doorzoekt: de vraag, met het
    # college-antwoord erbij als dat er is.
    brontekst = f"Schriftelijke vraag van {ind} aan de stad, onderwerp: {onderw}.\n\n{vr}"
    if antw:
        brontekst += f"\n\nAntwoord van het college:\n{antw}"

    return {
        "id": f"sv-{jaar_maand}-{(nummer or '0').replace('/', '-')}",
        "type": "schriftelijke_vraag",
        "date": datum,
        "nummer": nummer,
        "indiener": indiener or f_indiener,
        "onderwerp": onderw,
        "titel": onderw,                 # de dossier-engine + tagging lezen 'titel'
        "vraag": vr,
        "antwoord": antw,
        "heeft_antwoord": bool(antw),
        "brontekst": brontekst.strip(),  # voor de tagging + zoekindex
        "pdf": str(pdf_path.relative_to(BASE)).replace("\\", "/"),
    }


def main():
    if not RAW.exists():
        sys.exit(f"[FOUT] geen pdf-map gevonden: {RAW} (draai eerst fetch_schriftelijke_vragen.py)")
    # Bron-URL-index van de fetch: per vraag de echte pdf-link bij de stad (voor de bronlink).
    bron_idx = (RAW / "_bron_urls.json")
    bron_urls = json.loads(bron_idx.read_text(encoding="utf-8")) if bron_idx.exists() else {}

    records, mislukt = [], 0
    for ym_dir in sorted(RAW.glob("*")):
        if not ym_dir.is_dir():
            continue
        jm = ym_dir.name
        for pdf in sorted(ym_dir.glob("*.pdf")):
            try:
                rec = parse_pdf(pdf, jm)
                rec["bron_url"] = bron_urls.get(str(pdf.relative_to(RAW)).replace("\\", "/"))
                records.append(rec)
            except Exception as e:
                mislukt += 1
                print(f"  [fout] {pdf.name}: {e}")
    # Scope: niet verder terug dan de oudste besluit-/agendadatum, zodat de schriftelijke
    # vragen hetzelfde tijdvenster delen als de rest van de site (besluiten starten eind 2024).
    # We leiden de grens af uit data.json, dus ze schuift vanzelf mee bij een verse build.
    dpad = BASE / "data.json"
    data = json.loads(dpad.read_text(encoding="utf-8")) if dpad.exists() else None
    if data:
        ses = {s.get("id"): s.get("date") for s in data.get("sessies", [])}
        dts = [c.get("date") for c in data.get("college_beslissingen", []) if c.get("date")]
        dts += [ses.get(a.get("sessie_id")) for a in data.get("agendapunten", []) if ses.get(a.get("sessie_id"))]
        cutoff = min(dts) if dts else None
        if cutoff:
            voor = len(records)
            records = [r for r in records if r["date"] >= cutoff]
            print(f"  scope: enkel vanaf {cutoff} (zelfde venster als de besluiten); {voor - len(records)} oudere vragen weggelaten")

    # Botsende id's ontdubbelen. De stad hergebruikt soms hetzelfde volgnummer voor twee
    # verschillende vragen (bv. 2025/096 van twee raadsleden op verschillende datums). Omdat
    # het id "sv-<uploadmaand>-<nummer>" is, kregen die dan hetzelfde id: één vraag overschreef
    # de andere in de zoekindex en in alles wat op id werkt.
    # Regel: de OUDSTE vraag van een botsend paar houdt het kale id, zodat bestaande dieplinks
    # en de ankers in correcties.json blijven werken. De andere krijgen een achtervoegsel dat
    # enkel van hun EIGEN pdf afhangt (korte hash), dus het verschuift niet als er later nog een
    # vraag met datzelfde nummer bijkomt.
    per_id = {}
    for r in records:
        per_id.setdefault(r["id"], []).append(r)
    botsingen = 0
    for basis, groep in per_id.items():
        if len(groep) < 2:
            continue
        botsingen += 1
        groep.sort(key=lambda r: (r["date"], r["pdf"]))
        for r in groep[1:]:
            kort = hashlib.sha1(r["pdf"].encode("utf-8")).hexdigest()[:6]
            r["id"] = f"{basis}-{kort}"
            print(f"  [dubbel nummer] {basis}: '{r['titel'][:45]}' ({r['date']}) -> {r['id']}")
    if botsingen:
        print(f"  {botsingen} hergebruikt volgnummer ontdubbeld (oudste vraag houdt het kale id)")

    records.sort(key=lambda r: (r["date"], r["id"]), reverse=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    # Invouwen in data.json onder 'schriftelijke_vragen', zodat de AI-tagging en build.py ze
    # verderop in de pijplijn meenemen.
    if data:
        data["schriftelijke_vragen"] = records
        dpad.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  in data.json gezet onder 'schriftelijke_vragen' ({len(records)} records)")

    geen_datum = sum(1 for r in records if r["date"].endswith("-01") )
    geen_vraag = sum(1 for r in records if not r["vraag"])
    met_antw = sum(1 for r in records if r["heeft_antwoord"])
    print(f"\nKlaar: {len(records)} vragen geparsed ({mislukt} mislukt) -> {OUT}")
    print(f"  met antwoord: {met_antw} · zonder vraagtekst: {geen_vraag} · datum-terugval (1e vd maand): {geen_datum}")


if __name__ == "__main__":
    main()
