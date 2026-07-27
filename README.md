# Denk mee met Mechelen

Een burgerexperiment dat de **Mechelse besluitvorming leesbaar maakt**: het haalt de
officiële documenten van de gemeenteraad, de raad voor maatschappelijk welzijn, het
college van burgemeester en schepenen, de burgemeester en het vast bureau op,
controleert of ze **tijdig** gepubliceerd zijn volgens het Decreet Lokaal Bestuur, en
giet alles in één los, te uploaden bestand: `dist/index.html`.

Bronnen: `lblod.mechelen.be` (het officiële publicatieplatform van de stad) voor de
zittingen, besluiten en notulen, en `mechelen.be` voor de geplande zittingsdatums en de
schriftelijke vragen van raadsleden. Een kant-en-klare API is er niet: het platform
serveert webpagina's en pdf's, maar met een vaste structuur, en precies die maakt het
automatisch uitlezen mogelijk.

---

## Snelstart

```bash
python -m pip install requests          # minimaal, voor het ophalen
python run_all.py                       # de hele pijplijn → dist/index.html
```

Open daarna `dist/index.html` in een browser, of upload dat ene bestand naar je webhost.

Optioneel (voor de AI-samenvattingen):

```bash
python -m pip install anthropic
setx ANTHROPIC_API_KEY "JOUW-EIGEN-SLEUTEL"   # Windows; daarna draait tag_items.py mee
```

De volledige stratenlijst vereist niets extra's: `straten_mechelen.py` gebruikt enkel
`requests` (het Vlaams Adressenregister) en wordt door `run_all.py` automatisch gedraaid
zodra `data/straten_mechelen.json` nog ontbreekt.

---

## De pijplijn (`run_all.py`)

De startknop draait alle stappen in de juiste volgorde:

| # | Stap | Wat | Internet? |
|---|------|-----|-----------|
| 1 | `fetch_all.py` | Zittingen + PDF's (agenda / aanvullend / besluiten / notulen) ophalen naar `data/raw/` | ja |
| 1b | `fetch_zittingen.py` | Geplande zittingsdatums van de stadswebsite (voor "volgende zitting") | ja |
| 1c | `fetch_schriftelijke_vragen.py` | Schriftelijke vragen van raadsleden (pdf's) ophalen + bron-URL-index | ja |
| 2 | `maak_data.py` | **Tijdigheid** berekenen + sessies in `data.json` zetten | nee |
| 2b | `reset_voor_assemblage` | Demo-inhoud wissen, volgende zitting afleiden, `is_demo=False` | nee |
| 3 | `parse_*` + `assembleer_*` | Per zitting de besluiten/notulen/agenda uitlezen en als agendapunten (met stemming per fractie) en collegebesluiten invoegen | nee |
| 3a | `parse_schriftelijke_vragen.py` | Schriftelijke vragen uitlezen (datum/vraag/antwoord **uit de pdf**) → `data.json` | nee |
| 3b | `straten_mechelen.py` | *(optioneel)* Volledige stratenlijst → `data/straten_mechelen.json` | ja |
| 3c | `tag_items.py` | *(optioneel)* AI-samenvattingen + thema's per punt | ja (API) |
| 3d | `schoon_brontekst.py` | E-mailadressen uit de gepubliceerde tekst redacteren (ná de tagging) | nee |
| 4 | `build.py` | `template.html` + `data.json` → `dist/index.html` | nee |
| 5 | `opkuis` | Tussenbestanden in de root wissen | nee |

Stappen 3b en 3c worden **alleen** uitgevoerd als hun voorwaarden vervuld zijn
(register ontbreekt nog / `ANTHROPIC_API_KEY` is gezet). Falen ze, dan stopt de bouw
niet: de frontend valt terug op de geverifieerde demo-seed.

### De juridische telregels (`maak_data.py`)

Hele dagen, uitleg Agentschap Binnenlands Bestuur:

- **Agenda** — tijdig als (zitting − publicatie) ≥ 8 (zeven vrije dagen). *Art. 22 DLB.*
- **Notulen** — tijdig als (vólgende zitting − publicatie) ≥ 8.
- **Besluiten** — tijdig als (publicatie − zitting) ≤ 10. *Art. 287 DLB.*

College en vast bureau publiceren enkel een besluitenlijst → enkel de art.-287-regel.

---

## Bestanden

**Pijplijn-scripts**
- `run_all.py` — de startknop (orkestreert alles hieronder)
- `fetch_all.py`, `fetch_zittingen.py`, `fetch_schriftelijke_vragen.py` — ophalen
- `maak_data.py` — tijdigheid + sessies
- `parse_besluiten.py`, `parse_notulen.py`, `parse_agenda.py` — PDF → JSON
- `parse_schriftelijke_vragen.py` — schriftelijke vragen (datum/vraag/antwoord uit de pdf) → `data.json`
- `assembleer_agendapunten.py`, `assembleer_agenda.py`, `assembleer_college.py` — JSON → `data.json`
- `straten_mechelen.py` — stratenregister (NIS 12025) *(optioneel)*
- `tag_items.py` — AI-tagging *(optioneel, met cache)*
- `schoon_brontekst.py` — e-mailadressen uit de gepubliceerde tekst redacteren
- `build.py` — `dist/index.html` bouwen (met bron-lek-veiligheidsklep)

**Hulp / eenmalig** *(buiten de live pijplijn)*
- `maak_toren.py` — tekent het Sint-Romboutstoren-logo en patcht het in `template.html`
- `maak_raadsleden.py` — `data/raadsleden.json` (naam→fractie-naslag van alle 43 leden; niet door de pijplijn gelezen)
- `koppel_dossiers.py`, `zoek_dossier.py` — dossierkoppeling als los CLI-research-tool (de site bouwt dossiers client-side)

**Data**
- `template.html` — de UI-schil (placeholder `__DENKMEE_DATA__`)
- `data.json` — alle verwerkte data (wordt in het template gespoten)
- `data/` — `sessies_index.json`, `geplande_zittingen.json`, `raadsleden.json`, `straten_mechelen.json`*, `tags_cache.json`*
- `data/raw/<orgaan>/<datum>/*.pdf` — de opgehaalde brondocumenten
- `dist/index.html` — **het eindproduct**

*\* worden door de optionele stappen gemaakt.*

---

## Ontwerpprincipes

- **Niets verzinnen.** Thema's komen uit een vaste taxonomie; straten/buurten enkel
  als ze letterlijk in de brontekst staan; de bevoegde schepen blijft leeg tenzij
  expliciet vermeld.
- **Geen rauwe bron op de site.** `build.py` weigert een live build (`is_demo=False`)
  als er een niet-toegestane `lblod`-URL in het eindbestand zit.
- **Data, geen ontwerp.** Stratenlijst en logo komen uit scripts, niet uit handwerk.
- **Beleefd tegen de bron.** Identificeerbare User-Agent, 1 verzoek per seconde.
- **Eerlijk over de tooling.** De site is gebouwd met hulp van Claude Code, de
  AI-programmeerassistent van Anthropic. Ze is geen journalistiek eindproduct, maar een
  opstapje: een manier om de besluitvorming op te volgen en zelf te beslissen waar je je
  tanden in zet.

---

## Publiceren

`dist/` is de publiceerbare map: `index.html` met de data erin gebakken, plus de zoekindex
(`zoekindex.json`, client-side nagehaald voor de volledige-tekstzoek), de technische pagina
(`techniek/`), de zelf-gehoste fonts, de beelden en het `CNAME`. Upload de hele map naar je host.
Voor de gratis weg ligt een GitHub Pages-workflow klaar in
`.github/workflows/pages.yml`: zet **Settings → Pages → Source = "GitHub Actions"**, en
elke push die `dist/` wijzigt publiceert vanzelf.

Draai je een eigen kopie? Pas `CUSTOM_DOMAIN` in `build.py` aan naar je eigen domein
(of maak de waarde leeg voor een gewone `*.github.io`-URL) en stel datzelfde domein in
bij **Settings → Pages**.

## Bekende open eindjes

- **Deelgemeente-fijnmazigheid** — `straten_mechelen.py` levert nu de volledige ~825
  straten met deelgemeente uit het Vlaams Adressenregister (enkel `requests`, geen geo).
  Twee randgevallen vergen nog de geo-route: Walem deelt postcode 2800 met Mechelen, en
  Hombeek/Leest delen 2811 (nu samen onder één label "Hombeek-Leest"). De nog fijnere
  **buurt** (STATBEL-sector) blijft eveneens een geo-uitbreiding; `buurt` is voorlopig leeg.
- **AI-samenvattingen** — `tag_items.py` staat klaar in de pijplijn; zet
  `ANTHROPIC_API_KEY` om ze echt te genereren (test gratis met `python tag_items.py --dry-run`).
- **Schriftelijke vragen** — datum en tekst komen uit de pdf zelf. Enkele stukken zijn
  **beeld-pdf's zonder tekstlaag**: daarvan is er geen vraagtekst uit te lezen en valt de
  datum terug op de 1e van de uploadmaand. Dat zou OCR vergen; de rest leest volledig uit.
