"""
fetch_budgetten.py — haalt de budgetstukken op van de stadswebsite: meerjarenplannen,
aanpassingen daarvan, budgetwijzigingen en jaarrekeningen, voor de stad, het OCMW en de
autonome bedrijven (AGB SAM, AGB MAC, AGB Energiepunt) en de welzijnsorganisaties.

Bron: .../bekendmakingen-verslagen-en-documenten/budget-meerjarenplannen-en-jaarrekeningen
Eén pagina, opgebouwd als h2 = entiteit (met jaartal), h3 = documentenset, en daaronder
de pdf-links. Die opbouw nemen we mee: zonder de kop waaronder een pdf staat, zegt een
bestandsnaam als "04 Samenstelling beleidsdomeinen.pdf" niets over welk plan het is.

Waarom dit script bestaat: de besluiten op de site dragen MJP-actiecodes (MJP004937), maar
wat achter zo'n code zit — welke actie, welke dienst, hoeveel geld — staat enkel in deze
stukken. parse_mjp_acties.py leest dat eruit; dit script zorgt dat de stukken er zijn.

Idempotent en zuinig: één HEAD per pdf om de grootte te vergelijken met wat er lokaal staat.
Enkel wat ontbreekt of van grootte veranderde, wordt gedownload. Zo kost een run waarin de
stad niets publiceerde één pagina-ophaling plus wat HEAD-verkeer, en geen enkele download.

Meteen ook de wachtpost: de stad publiceert hier twee à vier keer per jaar (jaarrekening rond
juni, meerjarenplanaanpassing rond december). Elke run vergelijkt de pagina met de vorige
index en meldt wat nieuw, gewijzigd of verdwenen is, zodat een nieuwe publicatie niet
maanden onopgemerkt blijft.

Uitvoer: data/raw/budgetten/<entiteit>/<set>/<bestand>.pdf
         data/raw/budgetten/_index.json        (wat er staat, met bron-URL en kop)
         data/raw/budgetten/_wijzigingen.json  (wat er sinds de vorige run veranderde)

Draai:  python fetch_budgetten.py            (run_all.py doet dit automatisch, stap 1e)
        python fetch_budgetten.py --enkel-kijken   (niets downloaden, enkel melden)
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import argparse
import hashlib
import json
import re
import time
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

BASE = Path(__file__).parent
ROOT = "https://www.mechelen.be"
URL = (ROOT + "/stad-en-bestuur/stadsbestuur-en-organisatie/bekendmakingen-verslagen-en-documenten"
       "/budget-meerjarenplannen-en-jaarrekeningen")
USER_AGENT = "DenkMeeMetMechelen/1.0 (burgerexperiment; contact via asgaupaust.be)"
OUT = BASE / "data" / "raw" / "budgetten"
INDEX = OUT / "_index.json"
WIJZIGINGEN = OUT / "_wijzigingen.json"
PAUZE = 0.4                 # seconden tussen aanvragen, om de servers niet te belasten

# De stukken staan op twee hosts: de gewone site en de bestandsserver van de stad. Enkel
# deze twee mogen; een absolute link naar een ander domein in de bron-HTML wordt geweigerd.
TOEGESTANE_HOSTS = {"www.mechelen.be", "mechelen.be",
                    "www.assetsmechelen.be", "assetsmechelen.be"}


def slug(tekst: str) -> str:
    """'Stad en OCMW 2025' -> 'stad_en_ocmw_2025'. Accenten weg, enkel letters/cijfers/_."""
    t = unicodedata.normalize("NFD", (tekst or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    return t[:80] or "overig"


def ontdoe_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


# Eén doorloop over de pagina die koppen en pdf-links in leesvolgorde oppikt. Een pdf erft
# zo de h2 (entiteit + jaar) en h3 (documentenset) waaronder hij staat.
TOKENS_RX = re.compile(
    r'<h2[^>]*>(?P<h2>.*?)</h2>'
    r'|<h3[^>]*>(?P<h3>.*?)</h3>'
    r'|<a\b[^>]*href="(?P<href>[^"]+\.pdf[^"]*)"[^>]*>(?P<titel>.*?)</a>',
    re.S | re.I)

# Koppen van de pagina-omkadering (kruimelpad, deelknoppen, footer). Alles wat daarna komt of
# ertussen staat, is geen entiteit. Zonder deze lijst kregen de laatste pdf's op de pagina
# 'delen' of 'huis_van_de_mechelaar' als entiteit mee.
GEEN_ENTITEIT = {"kruimelpad", "delen", "social", "op deze pagina", "hoe kunnen we je helpen?"}


def documenten(html: str) -> list[dict]:
    """Alle pdf-links met de kop waaronder ze staan, in paginavolgorde, ontdubbeld op URL."""
    uit, gezien = [], set()
    entiteit = documentenset = ""
    for m in TOKENS_RX.finditer(html):
        if m.group("h2") is not None:
            kop = ontdoe_tags(m.group("h2"))
            entiteit = "" if kop.lower() in GEEN_ENTITEIT else kop
            documentenset = ""
            continue
        if m.group("h3") is not None:
            kop = ontdoe_tags(m.group("h3"))
            documentenset = "" if kop.lower() in GEEN_ENTITEIT else kop
            continue
        href = m.group("href").strip()
        url = urljoin(URL, href)
        if url in gezien or not entiteit:
            continue
        gezien.add(url)
        uit.append({
            "url": url,
            "entiteit": entiteit,
            "set": documentenset,
            "titel": ontdoe_tags(m.group("titel")) or Path(unquote(urlparse(url).path)).stem,
        })
    return uit


def lokaal_pad(doc: dict) -> Path | None:
    """data/raw/budgetten/<entiteit>/<set>/<bestandsnaam>. De bestandsnaam komt van een
    externe pagina, dus enkel een gewoon padstuk is toegestaan: geen '..', geen mapscheiding,
    geen Windows-onvriendelijke tekens (path traversal).

    De stad geeft haar stukken soms namen van 125 tekens ("stad-aanpassing-meerjarenplan-
    2020-2025-nr.-1.-vaststelling-deel-stad.-1.1-amjp1_…"). Samen met de projectmap loopt dat
    over de Windows-padgrens van 260 tekens en mislukt het wegschrijven. Daarom korten we de
    naam in tot het pad past, met een stukje van de URL-vingerafdruk erachter zodat twee
    ingekorte namen nooit op elkaar vallen."""
    naam = unquote(urlparse(doc["url"]).path).rsplit("/", 1)[-1]
    naam = re.sub(r'[<>:"|?*\\/]', "_", naam).strip()
    if not naam or naam in (".", "..") or not naam.lower().endswith(".pdf"):
        return None
    delen = [slug(doc["entiteit"])] + ([slug(doc["set"])] if doc["set"] else [])
    pad = OUT.joinpath(*delen, naam)
    if len(str(pad)) > 250:
        staart = "-" + hashlib.sha1(doc["url"].encode()).hexdigest()[:8] + ".pdf"
        ruimte = 250 - len(str(pad.parent)) - 1 - len(staart)
        if ruimte < 8:
            return None                      # zelfs ingekort past het niet: liever overslaan
        pad = pad.parent / (naam[:-4][:ruimte] + staart)
    return pad


def vorige_index() -> dict:
    if INDEX.exists():
        try:
            return {d["url"]: d for d in json.loads(INDEX.read_text(encoding="utf-8"))["documenten"]}
        except Exception:
            pass
    return {}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--enkel-kijken", action="store_true",
                    help="niets downloaden: enkel melden wat er nieuw of gewijzigd is")
    args = ap.parse_args()

    import requests
    OUT.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT

    r = sess.get(URL, timeout=60)
    r.raise_for_status()
    docs = documenten(r.text)
    if not docs:
        # Geen enkele link gevonden = de stad heeft de opbouw van haar pagina gewijzigd.
        # Dan liever luid falen dan stil een lege index wegschrijven over een goede heen.
        raise SystemExit("[fout] geen pdf-links gevonden op de budgetpagina — opbouw gewijzigd?")

    oud = vorige_index()
    nieuwe_index, wijzigingen = [], []
    nieuw = bijgewerkt = ongewijzigd = geweigerd = mislukt = 0

    for doc in docs:
        host = urlparse(doc["url"]).netloc.lower()
        dest = lokaal_pad(doc)
        if host not in TOEGESTANE_HOSTS or dest is None:
            print(f"    [geweigerd: verdachte link] {doc['url'][:90]}…")
            geweigerd += 1
            continue

        # Grootte bij de bron opvragen (goedkoop) en vergelijken met wat er lokaal staat.
        # Zo halen we een ongewijzigd stuk nooit opnieuw op, maar merken we wél dat de stad
        # een document stilletjes verving: zelfde naam, andere inhoud.
        bron_bytes = 0
        try:
            h = sess.head(doc["url"], timeout=30, allow_redirects=True)
            if h.status_code == 200:
                bron_bytes = int(h.headers.get("Content-Length") or 0)
        except Exception as e:
            print(f"    [head faalde] {doc['url'][:80]}…: {e}")

        rel = dest.relative_to(OUT).as_posix()
        lokaal_bytes = dest.stat().st_size if dest.exists() else 0
        was = oud.get(doc["url"])
        soort = None
        if not dest.exists():
            soort = "nieuw" if not was else "ontbrak"
        elif bron_bytes and bron_bytes != lokaal_bytes:
            soort = "gewijzigd"

        if soort in ("nieuw", "gewijzigd"):
            wijzigingen.append({"soort": soort, "entiteit": doc["entiteit"], "set": doc["set"],
                                "titel": doc["titel"], "url": doc["url"], "bestand": rel})

        if soort and not args.enkel_kijken:
            try:
                resp = sess.get(doc["url"], timeout=300)
                resp.raise_for_status()
                if resp.content[:4] != b"%PDF":
                    print(f"    [geen pdf] {doc['url'][:80]}…")
                    mislukt += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(resp.content)
                lokaal_bytes = len(resp.content)
                if soort == "gewijzigd":
                    bijgewerkt += 1
                else:
                    nieuw += 1
                print(f"    + {rel}  ({lokaal_bytes/1024/1024:.1f} MB)")
                time.sleep(PAUZE)
            except Exception as e:
                print(f"    [fout] {doc['url'][:80]}…: {e}")
                mislukt += 1
                continue
        elif not soort:
            ongewijzigd += 1

        nieuwe_index.append({**doc, "bestand": rel,
                             "bytes": lokaal_bytes or bron_bytes,
                             "aanwezig": dest.exists()})
        time.sleep(PAUZE / 4)

    verdwenen = [u for u in oud if u not in {d["url"] for d in nieuwe_index}]
    for u in verdwenen:
        wijzigingen.append({"soort": "verdwenen", "entiteit": oud[u].get("entiteit", ""),
                            "set": oud[u].get("set", ""), "titel": oud[u].get("titel", ""),
                            "url": u, "bestand": oud[u].get("bestand", "")})

    if not args.enkel_kijken:
        INDEX.write_text(json.dumps({"gen": date.today().isoformat(), "bron": URL,
                                     "documenten": nieuwe_index},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
        # Een korte geschiedenis van wat de stad wanneer publiceerde of verving. Handig bij
        # 'sinds wanneer staat dit online?' en het bewijs dat de wachtpost echt kijkt.
        log = []
        if WIJZIGINGEN.exists():
            try:
                log = json.loads(WIJZIGINGEN.read_text(encoding="utf-8"))
            except Exception:
                log = []
        if wijzigingen:
            log.append({"datum": date.today().isoformat(), "wijzigingen": wijzigingen})
            WIJZIGINGEN.write_text(json.dumps(log[-50:], ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    mb = sum(d["bytes"] for d in nieuwe_index) / 1024 / 1024
    print(f"\nBudgetstukken: {len(nieuwe_index)} documenten ({mb:,.0f} MB) over "
          f"{len({d['entiteit'] for d in nieuwe_index})} entiteiten · "
          f"{nieuw} nieuw, {bijgewerkt} vervangen, {ongewijzigd} ongewijzigd"
          + (f", {mislukt} mislukt" if mislukt else "")
          + (f", {geweigerd} geweigerd" if geweigerd else ""))
    if wijzigingen:
        print("  Let op, de stad publiceerde of verving stukken:")
        for w in wijzigingen[:12]:
            print(f"    [{w['soort']}] {w['entiteit']} · {w['set'] or '-'} · {w['titel'][:60]}")
        if len(wijzigingen) > 12:
            print(f"    … en {len(wijzigingen) - 12} meer")


if __name__ == "__main__":
    main()
