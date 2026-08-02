"""
meetlat_zoek.py — de meetlat voor het zoekgat (stap 0 van de zoekverbetering).

Toetst, onafhankelijk van hoe de index gebouwd is, twee dingen:

1. HET GAT: hoeveel woorden staan er in de volledige stukken die de huidige zoekbalk
   (titel + samenvatting + dossiernaam/thema's/plaatsen/schepenen) niet kan vinden?
   Dat is precies wat de index hoort te dichten.
2. DE DICHTING: vindt de index die woorden ook echt, en liegt het label nooit? We nemen
   een steekproef uit de index en controleren met een ONAFHANKELIJKE herberekening dat
   elk bemonsterd woord (a) in de volledige tekst van dat punt staat en (b) niet in wat
   de huidige zoek al doorzoekt.

Draai:  python meetlat_zoek.py     (na bouw_zoekindex.py; wijzigt niets, meet alleen)
"""

import sys
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import json
import random
from pathlib import Path

import bouw_zoekindex as bz

BASE = Path(__file__).parent


def main():
    data = json.loads((BASE / "data.json").read_text(encoding="utf-8"))
    index = json.loads((BASE / "zoekindex.json").read_text(encoding="utf-8"))
    ids = index["items"]
    toks = index["tokens"]

    alle = {p["id"]: p for p in data.get("agendapunten", [])}
    alle.update({p["id"]: p for p in data.get("schriftelijke_vragen", [])})
    vragen_cache = {}
    _vc_pad = BASE / "data" / "schriftelijke_vragen.json"
    if _vc_pad.exists():
        vragen_cache = {r["id"]: r for r in json.loads(_vc_pad.read_text(encoding="utf-8"))}

    # ------- 1. het gat, geteld -------
    print("=== HET ZOEKGAT ===")
    print(f"woorden die enkel in het volledige stuk staan: {len(toks):,}")
    print(f"punten met zulke extra woorden: {len(ids):,}")

    # ------- 2. de dichting, onafhankelijk hergecontroleerd -------
    print("\n=== STEEKPROEF (onafhankelijke hercontrole) ===")
    notulen = bz.notulen_per_punt()
    random.seed(20260718)
    steekproef = random.sample(sorted(toks), 40)
    fouten = 0
    for woord in steekproef:
        # neem het eerste punt uit de postinglijst en hercontroleer beide beweringen
        item_id = ids[toks[woord][0]]
        p = alle.get(item_id, {})
        zoekbaar = bz.norm((p.get("titel") or "") + " " + (p.get("decoded") or ""))
        # volledige tekst opnieuw samenstellen, zoals de bouwer maar los ervan herteld
        _vc = vragen_cache.get(p.get("id"), {})
        vol = [p.get("brontekst") or _vc.get("brontekst") or "", (p.get("result") or {}).get("text") or "",
               " ".join(p.get("kernbegrippen") or []),
               p.get("vraag") or _vc.get("vraag") or "", p.get("antwoord") or _vc.get("antwoord") or ""]
        nt = notulen.get((p.get("sessie_id"), str(p.get("nummer"))))
        if nt:
            vol.append(bz.STEMLIJST_RX.sub(" ", nt))
        pdf = p.get("pdf")
        if pdf and (BASE / pdf).exists():
            vol.append(bz.pdf_tekst(BASE / pdf))
        in_vol = woord in bz.tokens("\n".join(vol))
        in_zoek = woord in bz.tokens(zoekbaar)
        ok = in_vol and not in_zoek
        if not ok:
            fouten += 1
            print(f"  FOUT  {woord!r} bij {item_id}: in_volledig={in_vol}, al_zoekbaar={in_zoek}")
    print(f"steekproef van {len(steekproef)} woorden: {len(steekproef)-fouten} correct, {fouten} fout")
    print("(correct = het woord staat in het volledige stuk en is vandaag onvindbaar,")
    print(" dus het label 'in het volledige stuk' is waar)")

    # ------- 3. leesbare voorbeelden voor de mens -------
    print("\n=== 12 VOORBEELDEN van straks-vindbaar ===")
    voorbeelden = random.sample(sorted(toks), 200)
    getoond = 0
    for woord in voorbeelden:
        if getoond >= 12:
            break
        if len(toks[woord]) <= 4 and woord.isalpha() and len(woord) >= 6:
            item_id = ids[toks[woord][0]]
            titel = (alle.get(item_id, {}).get("titel") or "")[:64]
            print(f"  '{woord}'  ->  {item_id}  ({titel})")
            getoond += 1

    if fouten:
        sys.exit(f"[STOP] {fouten} steekproeffouten: het label zou kunnen liegen.")
    print("\nMeetlat: OK")


if __name__ == "__main__":
    main()
