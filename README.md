# Denk mee met Mechelen

Wat de Mechelse politiek beslist, in mensentaal en altijd met de bron erbij.

**[denkmee.asgaupaust.be](https://denkmee.asgaupaust.be)**

Hoe het werkt en waar het tekortschiet, staat op de [techniekpagina](https://denkmee.asgaupaust.be/techniek/).

## Zelf draaien

```
python -m pip install requests pdfplumber anthropic
python run_all.py
```

De pijplijn haalt de openbare stukken op, vat ze samen met AI en bouwt de site in `dist/`.
Voor de samenvattingen heb je een eigen Anthropic-sleutel nodig (`ANTHROPIC_API_KEY`).
De scripts staan in `pijplijn/` en leggen in hun docstring uit wat ze doen. Elke push naar `main` zet `dist/` online.

Een verse kopie van deze repo bouwt niet met enkel `python build.py`: de build toetst de data aan lijsten
van persoonsnamen die bewust niet in git staan, en weigert zonder die lijsten. Voor je eigen stad bouw je
de data opnieuw op met `run_all.py`.

## Licenties

Code onder de MIT-licentie. De grenzen van de deelgemeenten (`data/deelgemeenten_mechelen.geojson`) komen uit
OpenStreetMap: © [OpenStreetMap-bijdragers](https://www.openstreetmap.org/copyright), beschikbaar onder de ODbL.
De straten komen uit het Vlaams Adressenregister (bron: Digitaal Vlaanderen,
[modellicentie gratis hergebruik](https://data.vlaanderen.be/doc/licentie/modellicentie-gratis-hergebruik/v1.0)).
Lettertypes onder de Open Font License.

Onderdeel van [As Gau Paust](https://asgaupaust.be) · [Privacy](https://asgaupaust.be/privacy/)
