"""
fetch_schriftelijke_vragen.py — haalt de schriftelijke vragen "buiten de gemeenteraad"
op van de stadswebsite. Dat zijn vragen van individuele raadsleden aan de stad, los van
een zitting. Ze staan verspreid over meerdere (groeiende) pagina's, met per vraag een pdf.

Bron: .../gemeenteraad/schriftelijke-vragen-buiten-de-gemeenteraad (Drupal-pager ?page=N).
We lussen de pagina's tot er geen nieuwe pdf-links meer bijkomen, en downloaden enkel wat
er nog niet staat (idempotent). Beleefd: een korte pauze tussen de aanvragen.

Output: data/raw/schriftelijke_vragen/<jaar-maand>/<bestandsnaam>.pdf

Draai:  python fetch_schriftelijke_vragen.py     (eenmalig: python -m pip install requests)
"""
import sys
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError): pass

import re
import time
from pathlib import Path
from urllib.parse import unquote, urljoin

ROOT = "https://www.mechelen.be"
URL = ROOT + "/stad-en-bestuur/stadsbestuur-en-organisatie/gemeenteraad/schriftelijke-vragen-buiten-de-gemeenteraad"
USER_AGENT = "DenkMeeMetMechelen/1.0 (burgerexperiment; contact via asgaupaust.be)"
OUT = Path("data") / "raw" / "schriftelijke_vragen"
MAX_PAGINAS = 60          # ruime veiligheidsgrens tegen een eindeloze lus
PAUZE = 0.5               # seconden tussen aanvragen, om de servers niet te belasten


def pdf_links(html: str) -> list[str]:
    """Alle pdf-links naar de map met schriftelijke vragen, in paginavolgorde, ontdubbeld."""
    links = re.findall(r'href="([^"]+\.pdf[^"]*)"', html, re.IGNORECASE)
    uniek, gezien = [], set()
    for l in links:
        if "schriftelijke-vragen" in l.lower() and l not in gezien:
            gezien.add(l); uniek.append(l)
    return uniek


def lokale_naam(link: str) -> Path | None:
    """data/raw/schriftelijke_vragen/<jaar-maand>/<bestandsnaam> uit de URL afleiden.
    De link komt van een externe pagina, dus we staan enkel gewone padstukken toe: geen
    '..' of lege stukken die buiten de doelmap zouden kunnen schrijven (path traversal)."""
    pad = unquote(link.split("?")[0])
    staart = pad.split("/files/")[-1] if "/files/" in pad else pad.rsplit("/", 1)[-1]
    # Windows-onvriendelijke tekens neutraliseren, mappenstructuur (jaar-maand) behouden
    delen = [re.sub(r'[<>:"|?*]', "_", d).strip() for d in staart.split("/")]
    if any(d in ("", ".", "..") or d.startswith("\\") for d in delen):
        return None
    return OUT.joinpath(*delen)


def main():
    import requests
    OUT.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers["User-Agent"] = USER_AGENT

    alle_links, nieuw, overgeslagen = [], 0, 0
    bron_urls = {}            # lokale-relatieve pdf-naam -> volledige bron-URL (voor de bronlink)
    gezien = set()
    pagina = 0
    while pagina < MAX_PAGINAS:
        u = URL + (f"?page={pagina}" if pagina else "")
        try:
            r = sess.get(u, timeout=30); r.raise_for_status()
        except Exception as e:
            print(f"[stop] pagina {pagina} niet op te halen: {e}"); break

        links = [l for l in pdf_links(r.text) if l not in gezien]
        if not links:
            print(f"  pagina {pagina}: geen nieuwe vragen — einde pager."); break

        print(f"  pagina {pagina}: {len(links)} vragen")
        for l in links:
            gezien.add(l); alle_links.append(l)
            dest = lokale_naam(l)
            url = urljoin(ROOT, l)
            # Enkel stukken van de stad zelf: een absolute link naar een ander domein in de
            # bron-HTML mag nooit een download of een bronlink op de site opleveren.
            if dest is None or not url.startswith(ROOT + "/"):
                print(f"    [geweigerd: verdachte link] {l[:90]}…"); continue
            bron_urls[str(dest.relative_to(OUT)).replace("\\", "/")] = url
            if dest.exists():
                overgeslagen += 1; continue
            try:
                resp = sess.get(url, timeout=60); resp.raise_for_status()
            except Exception as e:
                print(f"    [fout] {url[:90]}…: {e}"); continue
            if resp.content[:4] != b"%PDF":
                print(f"    [geen pdf] {url[:90]}…"); continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            nieuw += 1
            print(f"    + {dest.relative_to(OUT)}")
            time.sleep(PAUZE)
        pagina += 1
        time.sleep(PAUZE)

    # Bron-URL-index wegschrijven: parse_schriftelijke_vragen.py hangt er per vraag de
    # echte pdf-link aan, zodat de site naar het bronstuk bij de stad kan verwijzen.
    if bron_urls:
        import json
        (OUT / "_bron_urls.json").write_text(
            json.dumps(bron_urls, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nKlaar: {len(alle_links)} vragen gezien over {pagina} pagina's · "
          f"{nieuw} nieuw gedownload, {overgeslagen} stonden er al.")


if __name__ == "__main__":
    main()
