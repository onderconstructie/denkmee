"""
maak_raadsleden.py — bouwt data/raadsleden.json (naam -> fractie) uit de
ABB-mandatendatabank. Dit is de gezaghebbende bron: volledig (43 leden), met
fractie, en actueler dan de stadswebsite (die liep één opvolger achter).

Drie namen verschillen per bron; daarvoor staan aliassen ingebouwd zodat de
koppeling stemming -> fractie sluit:
  - ABB 'Nkandu Rabau'            = notulen 'Rina Rabau Nkandu'
  - ABB 'Kristof Calvo y Castañer'= notulen 'Kristof Calvo'
  - ABB 'Pita Saïd Aghassaiy'     = website 'Saïd Aghassaiy'
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
from pathlib import Path

# (voornaam, familienaam, fractie) — letterlijk uit de ABB-tabel
ABB = [
 ("Abdrahman","Labsir","Voor Mechelen"),("Alexander","Vandersmissen","Voor Mechelen"),
 ("Anne","Delvoye","N-VA"),("Ariane","Van Craen","Voor Mechelen"),("Arnout","Geys","N-VA"),
 ("Arthur","Orlians","Voor Mechelen"),("Barbara","Van de Perre","Voor Mechelen"),
 ("Bart","Somers","Voor Mechelen"),("Bert","Delanoeije","Voor Mechelen"),
 ("Björn","Siffer","Voor Mechelen"),("Catherine","François","VLAAMS BELANG"),
 ("Dirk","Letens","Voor Mechelen"),("Dirk","Tuypens","PVDA"),("Dries","Devillé","VLAAMS BELANG"),
 ("Elisabet","Okmen","Voor Mechelen"),("Freya","Perdaens","N-VA"),("Frieda","Ardies","Vooruit"),
 ("Gabriella","De Francesco","Voor Mechelen"),("Greet","Geypen","Voor Mechelen"),
 ("Ingrid","Kluppels","VLAAMS BELANG"),("Jan","Verbergt","N-VA"),
 ("Johan","De Vleeshouwer","Vooruit"),("Karl","Lauwers","cd&v Mechelen"),
 ("Kathleen","Teughels","N-VA"),("Katleen","Den Roover","N-VA"),("Klaas","Delrue","Voor Mechelen"),
 ("Kristof","Calvo y Castañer","Voor Mechelen"),("Laura","Cornette","Voor Mechelen"),
 ("Luc","Geysels","VLAAMS BELANG"),("Marc","Hendrickx","N-VA"),("Marleen","Daenen","N-VA"),
 ("Nicole","Van Dessel","cd&v Mechelen"),("Nkandu","Rabau","Voor Mechelen"),
 ("Patrick","Princen","Voor Mechelen"),("Pia","Indigne","Voor Mechelen"),
 ("Piet","den Boer","Voor Mechelen"),("Pita Saïd","Aghassaiy","PVDA"),
 ("Sabe","De Graef","Vooruit"),("Sara","Van Rompaey","PVDA"),("Seppe","Vriens","Vooruit"),
 ("Stefaan","Deleus","cd&v Mechelen"),("Thijs","Verbeurgt","Vooruit"),
 ("Yves","Vanden Bosch","N-VA"),
]

# fractienamen netjes en consistent
FRACTIE = {"VLAAMS BELANG":"Vlaams Belang", "cd&v Mechelen":"cd&v"}

# afwijkende schrijfwijzen in notulen/website -> zelfde fractie
ALIASSEN = {
 "Rina Rabau Nkandu": "Voor Mechelen",
 "Kristof Calvo": "Voor Mechelen",
 "Saïd Aghassaiy": "PVDA",
}

def main():
    roster = {}
    for voornaam, familienaam, fr in ABB:
        roster[f"{voornaam} {familienaam}"] = FRACTIE.get(fr, fr)
    roster.update(ALIASSEN)

    out = Path("data"); out.mkdir(exist_ok=True)
    (out / "raadsleden.json").write_text(json.dumps(roster, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    telling = Counter(FRACTIE.get(fr, fr) for _,_,fr in ABB)
    print(f"{len(ABB)} raadsleden weggeschreven naar data/raadsleden.json")
    for fr, n in telling.most_common():
        print(f"  {fr:<16} {n}")
    coalitie = telling["Voor Mechelen"]+telling["Vooruit"]+telling["cd&v"]
    print(f"\nCoalitie (Voor Mechelen + Vooruit + cd&v): {coalitie} / {sum(telling.values())}")

if __name__ == "__main__":
    main()
