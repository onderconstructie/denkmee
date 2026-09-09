#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-commit-poort: houdt een commit tegen die een prive-waarde meeneemt.

Deze poort staat bewust NIET in de repo. Ze leest wat ze moet beschermen uit lokale,
genegeerde bestanden, zodat de poort zelf niets prijsgeeft.

Aanleiding: op 07/09/2026 belandde een met curl opgehaalde kopie van de LIVE pagina
in een commit. De live pagina draagt wel de naam die nergens in git mag staan, en
`git add -A` neemt blind alles mee wat niet genegeerd is. De bestaande controles keken
de verkeerde kant op: het vangnet in inject_over.py bewaakt of de PLAATSHOUDER weg is,
niet of de INGEVULDE waarde er staat.

Wat ze weigert:
  1. verboden paden (privacylijst, caches, ruwe documenten, losse werkbestanden)
  2. een ingevulde naam op de plek van de plaatshouder (class="wie-init")
  3. e-mailadressen in platte tekst (het publieke adres hoort base64 in de code)
  4. namen van burgers uit privacy_namen.json, als dat bestand er is
  5. een rauwe dd/mm/jj in een stuk met de tabelkop "Geboortedatum"

Overslaan kan met `git commit --no-verify`, maar doe dat niet zonder te weten waarom.
"""
import json
import os
import re
import subprocess
import sys

WORTEL = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                        capture_output=True).stdout.decode("utf-8", "replace").strip()

VERBODEN_PAD = re.compile(
    r"(^|/)(privacy_namen|tags_cache|_sweep_cache|_dg_cache)"
    r"|(^|/)(data/zoekcache|data/raw|data/uittreksel_cache|data/mjp_cache|export|bron)/"
    r"|\.(tmp|log|out|bak|wxr)$",
    re.I)

# jij@voorbeeld.be en soortgenoten zijn duidelijk verzonnen en mogen als voorbeeld blijven
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@(?!voorbeeld\.|example\.|domein\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
WIE_INIT = re.compile(r'class="wie-init">([^<]*)<')
GEBOORTE = re.compile(r"Geboortedatum\s+Beroep")
RAUWE_DATUM = re.compile(r"(?<![\d/])\d{2}/\d{2}/\d{2}(?![\d/])")

TEKST = (".html", ".py", ".json", ".md", ".txt", ".js", ".css", ".bat", ".yml", ".yaml", ".xml", ".svg")


def gestaged():
    uit = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                         capture_output=True).stdout.decode("utf-8", "replace")
    return [r for r in uit.split("\n") if r.strip()]


def inhoud(pad):
    r = subprocess.run(["git", "show", ":" + pad], capture_output=True)
    if r.returncode:
        return ""
    return r.stdout.decode("utf-8", "replace")


def burgernamen():
    pad = os.path.join(WORTEL, "privacy_namen.json")
    if not os.path.exists(pad):
        return []
    try:
        d = json.load(open(pad, encoding="utf-8"))
    except Exception:
        return []
    namen = [r["naam"] for r in d.get("volledig", []) if r.get("naam")]
    omgekeerd = [" ".join(n.split()[1:] + n.split()[:1]) for n in namen if len(n.split()) > 1]
    return namen + omgekeerd


def main():
    bestanden = gestaged()
    if not bestanden:
        return 0
    namen = burgernamen()
    klachten = []

    for pad in bestanden:
        if VERBODEN_PAD.search(pad):
            klachten.append("verboden pad gestaged: %s" % pad)
            continue
        if not pad.lower().endswith(TEKST):
            continue
        tekst = inhoud(pad)
        if not tekst:
            continue

        # Alleen op HTML: in de python-bron van het injectiescript staat dezelfde tekst als
        # regex, en die is geen ingevulde naam maar juist het gereedschap dat hem plaatst.
        if pad.lower().endswith(".html"):
            for m in WIE_INIT.finditer(tekst):
                if "%%OVER" + "_NAAM%%" not in m.group(1):
                    klachten.append("%s: er staat een ingevulde naam op de plek van de plaatshouder" % pad)
                    break

        for m in EMAIL.finditer(tekst):
            klachten.append("%s: e-mailadres in platte tekst (%s...)" % (pad, m.group(0)[:3]))
            break

        for n in namen:
            if re.search(r"\b%s\b" % re.escape(n), tekst):
                gemaskeerd = " ".join(d[0] + "." * (len(d) - 1) for d in n.split())
                klachten.append("%s: naam van de privacylijst (%s)" % (pad, gemaskeerd))
                break

        # Alleen binnen een VENSTER na de tabelkop kijken, niet over het hele bestand. dist/index.html
        # draagt de volledige dataset: daarin staat de kop ergens en staat elders altijd wel een
        # dd/mm/jj (eedaflegging, ontvangst, einde mandaat, budgetcodes). Een bestandsbrede scan
        # meldt dan altijd een lek, ook als de geboortedatums netjes gemaskeerd zijn. build.py van
        # denkmee doet dit per stuk; hier is een venster van 400 tekens de goedkope variant.
        for m in GEBOORTE.finditer(tekst):
            venster = tekst[m.end():m.end() + 400]
            if RAUWE_DATUM.search(venster):
                klachten.append("%s: rauwe dd/mm/jj vlak na de kop Geboortedatum" % pad)
                break

    if klachten:
        sys.stderr.write("\nCOMMIT GEWEIGERD door de pre-commit-poort:\n")
        for k in klachten:
            sys.stderr.write("  - %s\n" % k)
        sys.stderr.write("\nHaal het uit de staging (git restore --staged <pad>) of los het op.\n"
                         "Weet je zeker dat het mag: git commit --no-verify\n\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
