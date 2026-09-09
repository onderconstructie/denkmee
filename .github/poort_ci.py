#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Poort op de server: breekt de publicatie af als er een prive-waarde in de repo staat.

Dit is de derde en laatste laag. De twee lagen ervoor draaien op de laptop en zijn daar te
omzeilen: een pre-commit-hook met `git commit --no-verify`, en de weigering in build.py door
dist/ met de hand aan te passen. Deze laag draait op GitHub, in de publicatie zelf, en staat
VOOR de injectie van de secrets. Wat hier langskomt, is dus de boom zoals hij in git staat.

Aanleiding: op 07/09/2026 belandde een met curl opgehaalde kopie van de LIVE portaalpagina in
een commit. De naam van de initiatiefnemer stond erin, want die wordt bij het publiceren
geinjecteerd. Geen van de toenmalige controles keek die richting op.

Wat deze poort weigert, over de hele uitgecheckte boom:
  1. een INGEVULDE naam op de plek van de plaatshouder (class="wie-init")
  2. een e-mailadres in platte tekst, buiten de duidelijk verzonnen voorbeelden
  3. een bestandsnaam die nooit in git hoort (losse werkbestanden, caches, privacylijst)

Faalt de poort, dan stopt de hele workflow en gaat er niets live.
"""
import os
import re
import subprocess
import sys

MERK = "%%OVER" + "_NAAM%%"
WIE = re.compile(r'class="wie-init">([^<]*)<')
MAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@(?!voorbeeld\.|example\.|domein\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
VERBODEN_NAAM = re.compile(r"\.(tmp|log|out|bak|wxr)$|^privacy_namen|tags_cache", re.I)

TEKST = (".html", ".py", ".json", ".md", ".js", ".css", ".yml", ".yaml", ".svg", ".bat")
OVERSLAAN = {".git", "node_modules", "__pycache__", "fonts", "beelden"}


def bestanden_in_git():
    """Alleen wat ECHT in git zit. Over de schijf lopen zou genegeerde werkmappen meepakken
    (de zoekcache, de rauwe export) die bij een checkout nooit meekomen: vals alarm, en het
    verbergt of de poort de juiste dingen ziet."""
    uit = subprocess.run(["git", "ls-files"], capture_output=True).stdout
    return [r for r in uit.decode("utf-8", "replace").splitlines() if r.strip()]


def main():
    klachten = []
    for pad in bestanden_in_git():
        naam = pad.split("/")[-1]
        if any(deel in OVERSLAAN for deel in pad.split("/")[:-1]):
            continue
        if VERBODEN_NAAM.search(naam):
            klachten.append("bestand hoort niet in git: %s" % pad)
            continue
        if not naam.lower().endswith(TEKST):
            continue
        try:
            with open(pad, encoding="utf-8", errors="replace") as f:
                tekst = f.read()
        except OSError:
            continue
        if naam.lower().endswith(".html"):
            for m in WIE.finditer(tekst):
                if MERK not in m.group(1):
                    klachten.append("%s: een ingevulde naam op de plek van de plaatshouder" % pad)
                    break
        m = MAIL.search(tekst)
        if m:
            klachten.append("%s: e-mailadres in platte tekst (%s...)" % (pad, m.group(0)[:3]))

    if klachten:
        print("::error::Publicatie afgebroken: prive-waarde in de repo")
        for k in klachten:
            print("  - %s" % k)
        return 1
    print("poort: geen ingevulde naam, geen e-mailadres in platte tekst, geen verboden bestand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
