# Denk mee met Mechelen

Volg in mensentaal wat de Mechelse politiek beslist. Live op
[denkmee.asgaupaust.be](https://denkmee.asgaupaust.be), onderdeel van
[asgaupaust.be](https://asgaupaust.be).

De pijplijn haalt de officiële documenten op van de vijf Mechelse bestuursorganen, vat ze
samen in mensentaal, bundelt stukken over hetzelfde onderwerp tot dossiers, en meet of de
stad haar wettelijke publicatietermijnen haalt.

Hoe dat werkt, waar de stukken vandaan komen en waar het experiment tekortschiet, staat op
de site zelf: [denkmee.asgaupaust.be/techniek](https://denkmee.asgaupaust.be/techniek/).
Wat er met bezoekersgegevens gebeurt: [asgaupaust.be/privacy](https://asgaupaust.be/privacy/).
Die pagina's zijn de enige bron; deze README herhaalt ze bewust niet.

## Zelf draaien

```bash
python -m pip install requests          # minimaal, voor het ophalen
python run_all.py                       # de hele pijplijn → dist/
```

Voor de AI-samenvattingen (Anthropic-API, met cache zodat herruns goedkoop zijn):

```bash
python -m pip install anthropic
setx ANTHROPIC_API_KEY "JOUW-EIGEN-SLEUTEL"
```

`run_all.py` orkestreert alle stappen; elke stap documenteert zichzelf in de docstring van
zijn script. Persoonsgegevens van burgers worden vóór publicatie geredigeerd.

## Publiceren

`dist/` is de publiceerbare map: upload ze, of gebruik de GitHub Pages-workflow
(`.github/workflows/pages.yml`, Settings → Pages → Source = "GitHub Actions"). Draai je een
eigen kopie voor een andere gemeente, pas dan `CUSTOM_DOMAIN` in `build.py` aan.
