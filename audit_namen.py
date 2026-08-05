"""
audit_namen.py — controleert de GEPUBLICEERDE zoekindex op persoonsnamen.

Waarom dit bestaat: op 05/08/2026 bleek dat je op de site de achternaam van een burger kon
intikken en een dossier kreeg. De redactie in schoon_brontekst.py en bouw_zoekindex.py knipt
namen weg, maar alleen in de contexten die we kennen ("de heer X", "OGV X"). Deze audit draait
de vraag om: neem elk woord dat in de index staat, kijk in de tekst van de stukken waar dat
woord aan hangt, en meld het als het daar naast een persoonsaanduiding blijkt te staan.

De valkuil die dit script vermijdt: de context moet uit het EIGEN stuk van het woord komen.
Een eerdere versie zocht de context in willekeurige bronbestanden en koppelde die aan de
punten uit de index, en dat verwart een naam in stuk A met hetzelfde woord in stuk B.

Draai:  python audit_namen.py            (leest zoekindex.json + dezelfde bronnen als de bouw)
Uitvoer: een lijst kandidaten, zeldzaamste eerst. Beoordeel ze met de hand; wat een burger is
gaat naar privacy_namen.json ("volledig"), wat een functie is naar "beoordeeld_publiek".
"""

import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
import bouw_zoekindex as bz
import schoon_brontekst

# Woorden die vlak vóór een naam staan in officiële stukken. Ruimer dan wat de redactie zelf
# wegknipt: deze lijst mag ruis opleveren, want een mens beoordeelt de uitkomst.
MARKERS = (
    r"(?:de\s+heer|mevrouw|mevr\.|dhr\.|meester|advocaat|raadsman|raadsvrouw|aanvrager|"
    r"aanvraag\s+van|belanghebbende|bezwaarindiener|bezwaar\s+ingediend\s+door|verzoeker|"
    r"klager|eigenaar|contactpersoon|penningmeester|secretaris|voorzitter|exploitant|"
    r"vertegenwoordigd\s+door|huurder|koper|verkoper|erfgenaam|nabestaande|geboren|"
    r"namens|ondergetekende|kandidaat-huurder|OGV|notaris)"
)
RX_MARKER = re.compile(MARKERS, re.I)
# Een persoon ziet er in deze stukken uit als twee hoofdletterwoorden naast elkaar:
# "Peter Aerts", "LOBBESTAEL Wouter", "Van Cammeren Daniël". Eén los hoofdletterwoord is
# meestal een zinsbegin of een kop, en dat leverde in de eerste versie honderden valse meldingen.
RX_NAAMPAAR = re.compile(
    r"\b([A-ZÀ-Ü][a-zA-ZÀ-ÿ'’-]{2,}|[A-ZÀ-Ü]{3,})\s+([A-ZÀ-Ü][a-zA-ZÀ-ÿ'’-]{2,}|[A-ZÀ-Ü]{3,})\b")
# Tweede signaal: een achternaam komt in de dataset nooit met een kleine letter voor, een
# gewoon woord ("bezwaar", "eigenaar", "aandeel") juist bijna altijd. Woorden die we ergens
# midden in een zin klein zien staan, vallen dus af.
RX_KLEIN = re.compile(r"(?<![.!?\n]\s)(?<!^)\b([a-zà-ÿ][a-zà-ÿ'’-]{2,})\b")
VENSTER = 90

# Woorden die na een marker opduiken maar geen persoon zijn.
STOP = {
    "stad", "mechelen", "college", "gemeenteraad", "besluit", "artikel", "vlaamse", "vlaanderen",
    "ocmw", "raad", "burgemeester", "schepen", "stadsbestuur", "brussel", "antwerpen", "motivering",
    "voorgeschiedenis", "feiten", "argumentatie", "juridische", "grond", "bijlage", "dienst",
    "aldus", "overwegende", "gelet", "adres", "ondernemingsnummer", "afdeling", "sectie",
    "provincie", "gemeente", "straat", "laan", "steenweg", "handtekening", "verhuurder",
    "beheerder", "kandidatenlijst", "algemeen", "directeur", "diensthoofd", "afdelingshoofd",
}


def bronteksten():
    """id -> volledige tekst, exact zoals bouw_zoekindex hem aan de index voert."""
    data = json.loads((BASE / "data.json").read_text(encoding="utf-8"))
    extra = {}
    notulen = bz.notulen_per_punt()
    for ap in data.get("agendapunten", []):
        stukken = [
            ap.get("brontekst") or "",
            (ap.get("result") or {}).get("text") or "",
            " ".join(ap.get("kernbegrippen") or []),
        ]
        nt = notulen.get((ap.get("sessie_id"), str(ap.get("nummer"))))
        if nt:
            stukken.append(bz.STEMLIJST_RX.sub(" ", nt))
        stukken.append(bz.uittreksel_tekst(ap["id"]))
        extra[ap["id"]] = "\n".join(s for s in stukken if s)

    vragen = {}
    _vc = BASE / "data" / "schriftelijke_vragen.json"
    if _vc.exists():
        vragen = {r["id"]: r for r in json.loads(_vc.read_text(encoding="utf-8"))}
    for sv in data.get("schriftelijke_vragen", []):
        v = vragen.get(sv["id"], {})
        stukken = [
            sv.get("vraag") or v.get("vraag") or "",
            sv.get("antwoord") or v.get("antwoord") or "",
            sv.get("brontekst") or v.get("brontekst") or "",
            " ".join(sv.get("kernbegrippen") or []),
        ]
        pdf = sv.get("pdf")
        if pdf and (BASE / pdf).exists():
            try:
                t = bz.pdf_tekst(BASE / pdf)
                if len(t) <= bz.VRAAG_PDF_MAX:      # zelfde bundelgrens als de bouw
                    stukken.append(t)
            except Exception:
                pass
        extra[sv["id"]] = "\n".join(s for s in stukken if s)

    for cb in data.get("college_beslissingen", []):
        ut = bz.uittreksel_tekst(cb["id"])
        if ut:
            extra[cb["id"]] = ut

    # Zelfde maskering als de bouw, anders meldt de audit wat allang geredigeerd is.
    for k, t in list(extra.items()):
        extra[k] = schoon_brontekst.maskeer_namen(t)[0]
    return extra


def main():
    zi = json.loads((BASE / "zoekindex.json").read_text(encoding="utf-8"))
    items, tokens = zi["items"], zi["tokens"]
    per_woord = {w: [items[i] for i in lijst] for w, lijst in tokens.items()}
    teksten = bronteksten()

    # Eerst het tegenbewijs verzamelen: elk woord dat we ergens midden in een zin met een
    # kleine letter zien, is een gewoon woord en geen achternaam.
    klein = set()
    for tekst in teksten.values():
        for m in RX_KLEIN.finditer(tekst):
            klein.add(m.group(1))

    # Per stuk één keer de namen-vensters uitrekenen, dat is veel goedkoper dan per woord.
    kandidaten = {}      # woord -> {punten: [...], voorbeeld: str}
    for item_id, tekst in teksten.items():
        if not tekst:
            continue
        gevonden = {}
        for m in RX_MARKER.finditer(tekst):
            venster = tekst[m.end():m.end() + VENSTER]
            for hw in RX_NAAMPAAR.finditer(venster):
                for deel in hw.groups():
                    w = deel.lower()
                    if w in STOP or w in klein or len(w) < 4:
                        continue
                    if w not in gevonden:
                        gevonden[w] = re.sub(r"\s+", " ", tekst[max(0, m.start() - 25):m.end() + 70])
        for w, ctx in gevonden.items():
            if item_id in per_woord.get(w, ()):
                k = kandidaten.setdefault(w, {"punten": set(), "voorbeeld": ctx})
                k["punten"].add(item_id)

    print(f"woorden in de index          : {len(tokens):,}")
    print(f"stukken met brontekst        : {sum(1 for t in teksten.values() if t):,}")
    print(f"woorden die in HUN EIGEN stuk naast een persoonsaanduiding staan: {len(kandidaten):,}")
    zeldzaam = {w: v for w, v in kandidaten.items() if len(v["punten"]) <= 3}
    print(f"daarvan in hoogstens 3 stukken (de echte kandidaten): {len(zeldzaam):,}\n")
    for w in sorted(zeldzaam, key=lambda x: (len(zeldzaam[x]["punten"]), x)):
        v = zeldzaam[w]
        print(f"[{w}]  {sorted(v['punten'])}")
        print(f"     {v['voorbeeld'][:160]}")


if __name__ == "__main__":
    main()
