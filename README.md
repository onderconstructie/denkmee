# Denk mee met Mechelen

De besluitenradar van [asgaupaust.be](https://asgaupaust.be): volg in mensentaal wat de
Mechelse politiek beslist. Live op [denkmee.asgaupaust.be](https://denkmee.asgaupaust.be).

De site haalt de officiële documenten op van de vijf Mechelse bestuursorganen (de
gemeenteraad, de raad voor maatschappelijk welzijn, het college van burgemeester en
schepenen, het vast bureau en de burgemeester), vat ze samen in mensentaal, bundelt
stukken over hetzelfde onderwerp tot dossiers, en toont of de stad haar publicaties
tijdig online zet volgens de termijnen van het Decreet Lokaal Bestuur.

Bronnen: `lblod.mechelen.be` (het officiële publicatieplatform van de stad) en
`mechelen.be`. Een kant-en-klare API is er niet: het platform serveert webpagina's en
pdf's met een vaste structuur, en precies die maakt het automatisch uitlezen mogelijk.

## Snelstart

```bash
python -m pip install requests          # minimaal, voor het ophalen
python run_all.py                       # de hele pijplijn → dist/
```

Voor de AI-samenvattingen (via de Anthropic-API, met cache zodat herruns goedkoop zijn):

```bash
python -m pip install anthropic
setx ANTHROPIC_API_KEY "JOUW-EIGEN-SLEUTEL"
```

`run_all.py` is de startknop en orkestreert alle stappen in de juiste volgorde: ophalen,
uitlezen, samenvatten, indexeren en bouwen. Elke stap documenteert zichzelf in de
docstring van zijn script; wat je in de code leest, is wat er draait.

## Ontwerpprincipes

- **Niets verzinnen.** Alles op de site komt uit de officiële stukken; wat er niet
  letterlijk in staat, blijft leeg.
- **Geen rauwe bron op de site.** `build.py` weigert een live build als er een
  niet-toegestane bron-URL in het eindbestand zit.
- **Geen persoonsgegevens van burgers.** De pijplijn redigeert e-mailadressen en namen
  van private personen vóór publicatie; besloten zittingen blijven eruit.
- **Data, geen handwerk.** Stratenlijst, logo en zoekindex komen uit scripts.
- **Beleefd tegen de bron.** Identificeerbare User-Agent, één verzoek per seconde.
- **Eerlijk over de tooling.** De site is gebouwd met hulp van Claude Code, de
  AI-programmeerassistent van Anthropic. Ze is journalistiek gereedschap, geen afgewerkt
  artikel: een opstapje om de vinger aan de pols te houden.

## Publiceren

`dist/` is de publiceerbare map: upload ze naar je host, of gebruik de meegeleverde
GitHub Pages-workflow (`.github/workflows/pages.yml`, Settings → Pages → Source =
"GitHub Actions"). Draai je een eigen kopie, pas dan `CUSTOM_DOMAIN` in `build.py` aan.
