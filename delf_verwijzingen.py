"""
delf_verwijzingen.py — STAP 3f: delft harde verwijzingen tussen agendapunten.

Ambtelijke stukken dragen gestructureerde codes die punten aan elkaar koppelen zonder
dat er één woord AI aan te pas komt:

  - MJP-actiecodes (MJP004937): de lijn in het meerjarenplan waarop een beslissing
    weegt. Punten die dezelfde code delen, hangen aan hetzelfde budget of project.
    Gemeten op dit corpus is dat meestal een echt verband (de twee "Taaloefenkansen
    Nederlands"-punten delen een code terwijl hun titels verschillen), maar soms een
    generieke budgetlijn (één aankooplijn voor verschillende panden). Daarom voedt dit
    "verwante dossiers", en NOOIT een automatische samenvoeging: suggereren mag op
    zacht-plus bewijs, samenvoegen vraagt hard bewijs.
  - Zaaknummers (2024_GR_00169, 2024_CBS_01251): expliciete verwijzingen naar een
    specifiek besluit. Zeldzaam in de stukken, maar ijzersterk waar ze staan.
  - OMV-nummers (OMV_2025099849): één omgevingsvergunningsdossier.

Uitvoer: verwijzingen.json met enkel de codes die 2+ punten delen (een code met één
punt koppelt niets). build.py voegt dat als data.verwijzingen in de site, waar het
dossierpaneel er een "Verwante dossiers"-blok mee vult.

Draai:  python delf_verwijzingen.py     (run_all.py doet dit automatisch, stap 3f)
"""

import sys
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import json
import re
from pathlib import Path

import bouw_zoekindex as bz    # hergebruik: notulen-per-punt en pdf-tekst, mét cache

BASE = Path(__file__).parent
UIT = BASE / "verwijzingen.json"

PATRONEN = {
    # type -> (regex, normalisatie)
    "mjp":  re.compile(r"\bMJP\d{4,}\b"),
    "zaak": re.compile(r"\b20\d\d[_-](?:GR|CBS|VB|RMW|OCMW|BURG)[_-]\d+\b", re.IGNORECASE),
    "omv":  re.compile(r"\bOMV[_/]?\d{6,}\b", re.IGNORECASE),
}

def normaliseer(soort, code):
    code = code.upper().replace("/", "_").replace("-", "_")
    return f"{soort}:{code}"


def verzamel_codes(data=None):
    """code -> set van item-id's, over ALLE codes (ook die maar één punt raken).

    Apart gezet omdat een tweede lezer dezelfde oogst nodig heeft: parse_mjp_acties.py
    wil weten welke MJP-codes in dit corpus voorkomen, om enkel díe begrotingslijnen uit
    de budgetstukken te publiceren. main() hieronder houdt er enkel de koppelende (2+) uit
    over; wie alles wil, roept deze functie aan. De pdf-teksten komen uit de cache van
    bouw_zoekindex, dus dit twee keer aanroepen kost geen tweede extractie."""
    if data is None:
        data = json.loads((BASE / "data.json").read_text(encoding="utf-8"))
    notulen = bz.notulen_per_punt()

    code2items = {}
    def registreer(item_id, tekst):
        for soort, rx in PATRONEN.items():
            for code in rx.findall(tekst):
                sleutel = normaliseer(soort, code)
                code2items.setdefault(sleutel, set()).add(item_id)

    for ap in data.get("agendapunten", []):
        tekst = " ".join([
            ap.get("titel") or "", ap.get("decoded") or "", ap.get("brontekst") or "",
            notulen.get((ap.get("sessie_id"), str(ap.get("nummer")))) or "",
        ])
        registreer(ap["id"], tekst)

    # De vraagteksten komen uit de lokale cache (staan niet meer in data.json).
    vragen_cache = {}
    _vc_pad = BASE / "data" / "schriftelijke_vragen.json"
    if _vc_pad.exists():
        vragen_cache = {r["id"]: r for r in json.loads(_vc_pad.read_text(encoding="utf-8"))}
    for sv in data.get("schriftelijke_vragen", []):
        _vc = vragen_cache.get(sv["id"], {})
        tekst = " ".join([
            sv.get("titel") or "", sv.get("decoded") or "",
            sv.get("brontekst") or _vc.get("brontekst") or "",
            sv.get("vraag") or _vc.get("vraag") or "",
            sv.get("antwoord") or _vc.get("antwoord") or "",
        ])
        pdf = sv.get("pdf")
        if pdf and (BASE / pdf).exists():
            try:
                tekst += " " + bz.pdf_tekst(BASE / pdf)
            except Exception:
                pass
        registreer(sv["id"], tekst)

    # college-besluiten dragen enkel titel + samenvatting, maar een code daarin telt mee
    for cb in data.get("college_beslissingen", []):
        registreer(cb["id"], (cb.get("titel") or "") + " " + (cb.get("decoded") or ""))

    return code2items


def main():
    data = json.loads((BASE / "data.json").read_text(encoding="utf-8"))
    code2items = verzamel_codes(data)

    # Enkel codes die echt koppelen (2+ punten). Eén punt per code = geen verband.
    koppels = {c: sorted(its) for c, its in code2items.items() if len(its) >= 2}

    UIT.write_text(json.dumps({
        "gen": data.get("generated_at", ""),
        "codes": koppels,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    per_soort = {}
    for c in koppels:
        per_soort[c.split(":")[0]] = per_soort.get(c.split(":")[0], 0) + 1
    tot_punten = len({i for its in koppels.values() for i in its})
    print(f"Verwijzingen gedolven: {len(koppels)} koppelende codes "
          f"({', '.join(f'{k}: {v}' for k, v in sorted(per_soort.items()))}) "
          f"over {tot_punten} punten -> verwijzingen.json "
          f"({UIT.stat().st_size/1024:,.1f} kB)")


if __name__ == "__main__":
    main()
