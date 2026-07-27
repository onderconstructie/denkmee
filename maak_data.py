"""
maak_data.py — STAP 9: zet data/sessies_index.json om naar het formaat van de site.

Juridische telregels (uitleg Agentschap Binnenlands Bestuur — hele dagen):
  - AGENDA   : tijdig als (zitting − publicatie) >= 8   (zeven vrije dagen). Artikel 22 van het Decreet Lokaal Bestuur.
  - NOTULEN  : tijdig als (VOLGENDE zitting − publicatie) >= 8 (zelfde telling, t.o.v. de volgende vergadering).
  - BESLUITEN: tijdig als (publicatie − zitting) <= 10. Artikel 287 van het Decreet Lokaal Bestuur.
Geldt voor de gemeenteraad én de raad voor maatschappelijk welzijn.
(College, vast bureau en burgemeester: enkel de besluiten-regel van artikel 287.)

Niet-destructief: maakt eerst data.json.bak en vervangt enkel de gegevens van de organen
die in de index zitten. is_demo wordt door run_all.reset_voor_assemblage op False gezet.

Draai:  python maak_data.py
"""

# Robuuste console-uitvoer: zet stdout/stderr op UTF-8, zodat print() met niet-ASCII
# (pijlen, vinkjes) niet crasht op een Windows-console die standaard cp1252 gebruikt.
import sys
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import json
import shutil
from pathlib import Path
from datetime import date, timedelta

BASE = Path(__file__).parent
DATA = BASE / "data.json"
INDEX = BASE / "data" / "sessies_index.json"

# Volledige orgaannaam (zichtbaar) → korte interne code voor de frontend-logica.
TYPECODE = {"Gemeenteraad": "Gemeenteraad", "Raad voor maatschappelijk welzijn": "RMW"}
SLUG = {"Gemeenteraad": "gemeenteraad", "Raad voor maatschappelijk welzijn": "rmw"}

# Volledige orgaannaam → de korte sleutel die de frontend (BRON_ORGAAN_ID / ORG_BRON) gebruikt,
# voor de directe-PDF-opzoektabel data['bron_pdfs'].
ORGKEY = {"Gemeenteraad": "Gemeenteraad", "Raad voor maatschappelijk welzijn": "RMW",
          "College van burgemeester en schepenen": "College", "Vast bureau": "Vast bureau",
          "Burgemeester": "Burgemeester"}

# Organen die enkel een besluitenlijst publiceren: alleen artikel 287 (10 dagen ná
# de zitting of de beslissing). Geen agenda → geen artikel 22, geen notulen, geen sessie-kaart.
BESLUITEN_ONLY = ["College van burgemeester en schepenen", "Vast bureau", "Burgemeester"]

# Termijn-config per orgaan voor het tijdigheidsdashboard. De frontend leest deze velden
# uit data.json; ze horen dus bij ELKE run opnieuw gezet te worden (een nieuw orgaan zonder
# deze meta zou in de grafiek alles onterecht 'te laat' kleuren).
# Let op het wetsartikel: artikel 22 spreekt letterlijk over "de vergaderingen van de
# gemeenteraad". Voor de raad voor maatschappelijk welzijn geldt de tegenhanger, artikel 74.
# Eén label voor beide raden zou een verkeerd artikelnummer op de site zetten, en net dat
# valt op bij het publiek dat het decreet kent.
AGENDA_META = {"deadline_days": 8, "deadline_direction": "before",
               "doc_label": "agenda", "wet_label": "art. 22 DLB"}
AGENDA_META_RMW = {**AGENDA_META, "wet_label": "art. 74 DLB"}
BESLUIT_META = {"deadline_days": 10, "deadline_direction": "after",
                "doc_label": "besluitenlijst", "wet_label": "art. 287 DLB"}


def d(s):
    return date.fromisoformat(s) if s else None


def agenda_status(zitting, pub):
    deadline = zitting - timedelta(days=8)          # uiterste publicatiedag
    return ("tijdig", 0) if pub <= deadline else ("te-laat", (pub - deadline).days)


def notulen_status(volgende_zitting, pub):
    """Notulen krijgen bewust GEEN te-laat-oordeel.

    De raad keurt de notulen van een zitting pas goed op de eerstvolgende zitting. Vóór dat
    moment bestaan er geen goedgekeurde notulen om te publiceren, dus de publicatiedatum zegt
    niets over nalatigheid: hij volgt gewoon op de goedkeuring. De cijfers bevestigen dat
    patroon zonder uitzondering: 15 van de 16 keer verschijnen ze één of twee dagen ná die
    volgende zitting.

    Deze functie toetste de webpublicatie eerder aan "uiterlijk 8 dagen vóór de volgende
    zitting". Die termijn gaat echter over het ter beschikking stellen van de ONTWERP-notulen
    aan de raadsleden, zodat zij ze kunnen nalezen vóór de goedkeuring. Dat is iets anders dan
    publiceren op de webtoepassing, en het is van buitenaf niet waarneembaar. De oude regel
    zette daardoor bij vrijwel elke zitting "te-laat" met een aantal dagen erbij: een verwijt
    aan de stad dat deze data niet kan dragen. Niets toonde het, maar het stond wel in het
    publieke data.json, klaar om verkeerd geciteerd te worden.

    We registreren daarom enkel wat we echt zien: kwam de publicatie ná de goedkeurende
    zitting (het gebruikelijke verloop) of al ervoor. Geen van beide is een tekortkoming.
    """
    if not volgende_zitting:
        return "wachtend", 0
    return ("voor-goedkeuring", 0) if pub < volgende_zitting else ("na-goedkeuring", 0)


def besluiten_status(zitting, pub):
    over = (pub - zitting).days
    return ("tijdig", 0) if over <= 10 else ("te-laat", over - 10)


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    index = json.loads(INDEX.read_text(encoding="utf-8"))

    # Tweede klep, naast die in fetch_all.py. Dit script vervangt verderop data["sessies"] en
    # data["bron_pdfs"] destructief. Op een lege index zou dat alle zittingen en alle directe
    # bronlinks wissen terwijl de agendapunten (die uit de lokale map komen) blijven staan: een
    # half kapotte site die geen enkele bouwklep tegenhoudt. Weigeren is hier het juiste antwoord.
    if not index:
        sys.exit("[STOP] data/sessies_index.json is leeg. data.json blijft ongemoeid. "
                 "Draai eerst fetch_all.py opnieuw.")

    # groepeer de index per orgaan, chronologisch
    per_orgaan = {}
    for z in index:
        per_orgaan.setdefault(z["organ"], []).append(z)
    for lst in per_orgaan.values():
        lst.sort(key=lambda z: z["date"])

    nieuwe_sessies = []
    samenvatting = []

    for orgaan, zlijst in per_orgaan.items():
        if orgaan not in TYPECODE:
            continue                                  # college/vast bureau: later
        zittingen = []
        for i, z in enumerate(zlijst):
            zd = d(z["date"]); docs = z.get("documenten", {})
            ag, be, no = docs.get("agenda", {}), docs.get("besluiten", {}), docs.get("notulen", {})
            volgende = d(zlijst[i + 1]["date"]) if i + 1 < len(zlijst) else None

            if ag.get("published"):
                st, ld = agenda_status(zd, d(ag["published"]))
                agp = ag["published"]; deadline = (zd - timedelta(days=8)).isoformat() + "T23:59:59"
            else:
                st, ld, agp, deadline = "geen", 0, None, None

            entry = {"date": z["date"], "published": agp, "status": st, "late_days": ld}
            if be.get("published"):
                bst, bld = besluiten_status(zd, d(be["published"]))
                entry["besluiten"] = {"published": be["published"], "status": bst, "late_days": bld}
            if no.get("published"):
                nst, nld = notulen_status(volgende, d(no["published"]))
                entry["notulen"] = {"published": no["published"], "status": nst, "late_days": nld,
                                    "ref_zitting": zlijst[i + 1]["date"] if volgende else None}
            zittingen.append(entry)

            nieuwe_sessies.append({
                "id": f"{SLUG[orgaan]}-{zd.strftime('%Y%m%d')}",
                "type": TYPECODE[orgaan],
                "datetime": z["date"] + "T20:00:00",
                "date": z["date"],
                "agenda_url": None,        # bewust geen bronlink: nooit lblod- of stads-URL op de site
                "agenda_published": agp,
                "agenda_deadline": deadline,
                "status": st,
                "late_days": ld,
                # publicatiedatum van de notulen (None zolang ze er niet zijn). De frontend
                # leidt hieruit af of een agendapunt van deze zitting al naar de notulen mag linken,
                # los van of er een stemming geregistreerd is.
                "notulen_published": no.get("published"),
            })

        entry = data.setdefault("tijdigheid_per_orgaan", {}).setdefault(orgaan, {})
        entry.update(AGENDA_META_RMW if orgaan.startswith("Raad voor maatschappelijk") else AGENDA_META)
        entry["zittingen"] = zittingen
        telaat = sum(1 for z in zittingen if z["status"] == "te-laat")
        samenvatting.append((orgaan, len(zittingen), telaat,
                             [(z["date"], z["late_days"]) for z in zittingen if z["status"] == "te-laat"]))

    # College + vast bureau: enkel de besluiten-regel (artikel 287). Platte vorm
    # {date, published, status, late_days} — geen sessie, geen agenda-deadline.
    for orgaan in BESLUITEN_ONLY:
        zlijst = per_orgaan.get(orgaan)
        if not zlijst:
            continue                                   # nog niet opgehaald → demo blijft staan
        zittingen = []
        for z in zlijst:
            zd = d(z["date"]); be = z.get("documenten", {}).get("besluiten", {})
            if be.get("published"):
                bst, bld = besluiten_status(zd, d(be["published"]))
                zittingen.append({"date": z["date"], "published": be["published"],
                                  "status": bst, "late_days": bld})
            else:
                zittingen.append({"date": z["date"], "published": None,
                                  "status": "geen", "late_days": 0})
        entry = data.setdefault("tijdigheid_per_orgaan", {}).setdefault(orgaan, {})
        entry.update(BESLUIT_META)
        entry["zittingen"] = zittingen
        telaat = sum(1 for z in zittingen if z["status"] == "te-laat")
        samenvatting.append((orgaan, len(zittingen), telaat,
                             [(z["date"], z["late_days"]) for z in zittingen if z["status"] == "te-laat"]))

    # vervang de sessies van de verwerkte organen; behoud de rest
    verwerkte_codes = {TYPECODE[o] for o in per_orgaan if o in TYPECODE}
    overige = [s for s in data.get("sessies", []) if s.get("type") not in verwerkte_codes]
    data["sessies"] = sorted(nieuwe_sessies + overige, key=lambda s: s["date"])

    # Directe PDF-URL per zitting-document (besluitenlijst / notulen / agenda), zodat de site
    # rechtstreeks naar het stuk linkt in plaats van de zoekpagina van de stad (dat ene klikje
    # minder). De GetPublication-URL zit al in sessies_index.json; we bewaren een compacte
    # opzoektabel gesleuteld op "{orgKey}|{datum}|{doctype}", niet op elk los besluit (dat zou
    # dezelfde URL duizenden keren herhalen). De frontend valt terug op de zoek-ingang waar geen
    # directe URL is. 'aanvullend' laten we weg: dat hoort bij de agenda-terugval.
    bron_pdfs = {}
    for z in index:
        ok = ORGKEY.get(z.get("organ"))
        if not ok:
            continue
        for dt in ("besluiten", "notulen", "agenda"):
            url = (z.get("documenten", {}).get(dt) or {}).get("url")
            if url:
                bron_pdfs[f"{ok}|{z['date']}|{dt}"] = url
    data["bron_pdfs"] = bron_pdfs

    data["generated_at"] = date.today().isoformat()

    shutil.copy(DATA, DATA.with_suffix(".json.bak"))
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    for orgaan, n, telaat, details in samenvatting:
        if orgaan in BESLUITEN_ONLY:
            print(f"{orgaan}: {n} zittingen, {telaat} te laat (artikel 287 Decreet Lokaal Bestuur)")
            label = "besluitenlijst"
        else:
            print(f"{orgaan}: {n} zittingen, {telaat} te laat (artikel 22 Decreet Lokaal Bestuur)")
            label = "agenda"
        for datum, ld in details:
            print(f"   - {datum}: {label} {ld} dag(en) over de deadline")
    print(f"\nGeschreven naar {DATA.name} (back-up {DATA.name}.bak). is_demo blijft True tot ook de rest echt is.")


if __name__ == "__main__":
    main()
