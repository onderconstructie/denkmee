from pathlib import Path
import sys
import json
import re
import shutil

# print() met → … × werkt zo ook op een Windows-console die standaard cp1252 gebruikt
# (anders crasht een niet-ASCII-teken met een UnicodeEncodeError).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent

# 1) Lees de UI-schil en de data (twee aparte bestanden).
template = (BASE / "template.html").read_text(encoding="utf-8")
data = json.loads((BASE / "data.json").read_text(encoding="utf-8"))

# 1b) Stratenregister (als straten_mechelen.py het al maakte): de volledige Mechelse
#     straten→buurt→deelgemeente-lijst vervangt de demo-seed in data.json. De frontend
#     bouwt de hele zone-laag (D_PLACES, resolvePlace) op uit D.straten, dus dit volstaat
#     — elke straat draagt zelf zijn buurt + deelgemeente. Ontbreekt het register, dan
#     blijft de geverifieerde demo-seed staan (en is_demo hoort dan True te zijn).
register_pad = BASE / "data" / "straten_mechelen.json"
if register_pad.exists():
    register = json.loads(register_pad.read_text(encoding="utf-8"))
    if register.get("straten"):
        data["straten"] = register["straten"]
        for sleutel in ("buurten", "deelgemeenten"):
            if register.get(sleutel):
                data[sleutel] = register[sleutel]
        print(f"Stratenregister geladen: {len(register['straten'])} straten (vervangt de demo-seed).")

# 2) Zet de data terug om naar JSON-tekst en spuit hem in de placeholder.
#    De data staat als JS-object-literal in een <script>, dus een letterlijke "</script>"
#    (of "</…") in een brontekst zou de tag vroegtijdig sluiten. We escapen "</" naar "<\/":
#    binnen een JS-string is dat identiek, maar het kan de tag niet meer breken.
# 1c) Harde verwijzingen (delf_verwijzingen.py): voedt "verwante dossiers" in het paneel.
#     Klein bestand (enkele kB), dus gewoon mee in de pagina; ontbreekt het, dan bouwt
#     de site zonder dat blok, net als bij het stratenregister.
verwijzingen_pad = BASE / "verwijzingen.json"
if verwijzingen_pad.exists():
    verw = json.loads(verwijzingen_pad.read_text(encoding="utf-8"))
    data["verwijzingen"] = verw.get("codes", {})
    print(f"Verwijzingen geladen: {len(data['verwijzingen'])} koppelende codes.")

# 1d) Handmatige correcties op de dossiervorming (correcties.json, door de redactie
#     onderhouden — NOOIT automatisch). Losmaken/samenvoegen ankeren op stabiele stuk-id's;
#     de frontend past ze toe in buildDossiers. Ontbreekt het bestand, dan bouwt de site
#     gewoon zonder correcties, net als bij verwijzingen. Dit bestand hoort WEL in de repo
#     (in tegenstelling tot data/zoekcache): het is de redactionele beslissing zelf.
correcties_pad = BASE / "correcties.json"
if correcties_pad.exists():
    corr = json.loads(correcties_pad.read_text(encoding="utf-8"))
    data["correcties"] = {
        "losmaken": corr.get("losmaken", []),
        "samenvoegen": corr.get("samenvoegen", []),
        "verplaatsen": corr.get("verplaatsen", []),
        "dossiers": corr.get("dossiers", []),
        "alias": corr.get("alias", {}),
    }
    n = (len(data["correcties"]["losmaken"]) + len(data["correcties"]["samenvoegen"])
         + len(data["correcties"]["verplaatsen"]) + len(data["correcties"]["dossiers"]))
    print(f"Correcties geladen: {n} ingreep/ingrepen (losmaken + samenvoegen + verplaatsen + dossiers).")

# 1e) Synoniemgroepen voor de zoekbalk (synoniemen.json, handmatig gecureerd en getoetst
#     aan het corpus). Wie "stoep" intikt, vindt ook "voetpad". Klein bestand, mee in de
#     pagina; ontbreekt het, dan zoekt de site gewoon zonder synoniemen.
synoniemen_pad = BASE / "synoniemen.json"
if synoniemen_pad.exists():
    syn = json.loads(synoniemen_pad.read_text(encoding="utf-8"))
    data["synoniemen"] = syn.get("groepen", [])
    print(f"Synoniemen geladen: {len(data['synoniemen'])} groepen.")

# 2c) Schriftelijke vragen: de letterlijke vraag- en antwoordteksten blijven UIT de
#     gepubliceerde pagina. De samenvatting plus de directe bron-link volstaan; de
#     zoekindex leest de pdf's apart. data.json zelf houdt de velden: de AI-tagging
#     leest ze daar als input.
for _v in data.get("schriftelijke_vragen", []):
    for _veld in ("vraag", "antwoord", "brontekst"):
        _v.pop(_veld, None)

data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
html = template.replace("__DENKMEE_DATA__", data_json)

# 2b) Veiligheidsklep: de samenvattingen ("in mensentaal") zijn de kern van de site.
#     Wordt de AI-tagging overgeslagen (geen ANTHROPIC_API_KEY) of faalt ze halverwege,
#     dan staan er (bijna) geen 'decoded'-teksten in data.json. Zo'n kale build mag nooit
#     stilletjes live gaan: bij is_demo == False stoppen we hard onder de drempel.
MIN_DECODED_PCT = 90
_items = (data.get("agendapunten") or []) + (data.get("college_beslissingen") or []) \
         + (data.get("schriftelijke_vragen") or [])
if _items and not data.get("is_demo"):
    # Tellen op een ECHTE samenvatting. De assemblage zet 'decoded' alvast op de titel als
    # placeholder (assembleer_agendapunten.py, assembleer_agenda.py), dus een simpele
    # aanwezigheidstest zou 100% melden terwijl de site de ambtelijke titel herhaalt in het
    # 'in mensentaal'-blok. Zelfde test als tag_items.py gebruikt om ongetagd te herkennen.
    _echt = lambda i: bool(i.get("decoded")) and i["decoded"].strip() != (i.get("titel") or "").strip()
    _pct = 100 * sum(1 for i in _items if _echt(i)) // len(_items)
    if _pct < MIN_DECODED_PCT:
        sys.exit(f"[STOP] Live build geweigerd: maar {_pct}% van de {len(_items):,} punten heeft "
                 f"een samenvatting (drempel {MIN_DECODED_PCT}%). Draai tag_items.py met een "
                 f"ANTHROPIC_API_KEY (de cache maakt dat goedkoop) en bouw dan opnieuw.")
    print(f"       samenvattingen: {_pct}% van {len(_items):,} punten (drempel {MIN_DECODED_PCT}%) ✓")

# 3) Veiligheidsklep: de technische bron (lblod) mag niet ongecontroleerd op de
#    site staan. Twee uitzonderingen zijn bewust toegestaan: de vaste algemene
#    bron-ingangen van de gemeenteraad en de raad voor maatschappelijk welzijn.
#    Elk ander lblod-voorkomen (per-stuk-URL's, een teruggekeerde agenda_url, …)
#    blokkeert een ECHTE build (is_demo == False); bij een demo-build: waarschuwing.
TOEGESTANE_INGANGEN = [
    "https://lblod.mechelen.be/LBLODWeb/Home/Overzicht/be278471a2a318edba32e7ac4294c0eafbe4c8077a34dcbb9c2e43211d4a78a6/06c2b56ed7b49d146337f6db044204f19c34c4242deb3b4e142dbf925d733eda",
    "https://lblod.mechelen.be/LBLODWeb/Home/Overzicht/68e8c071ddfe1957b9c7b0ccd269f6776f4863a5f5bf4fed636f0e76427200ac/3ee5544b66963ad499afb4fe84f3de9995e6f0244f2fda120fa337e43d914f4c",
]
is_demo = bool(data.get("is_demo"))
scan = html
for ingang in TOEGESTANE_INGANGEN:
    scan = scan.replace(ingang, "")          # toegestane ingangen tellen niet mee
# Ook toegestaan: de publieke ZOEK-ingang op titel (SearchPublicaties). Dat is een
# stabiele zoekpagina van de stad, geen hardgecodeerde per-document-URL — de site
# gebruikt ze om elk besluit naar zijn officiële stuk te laten zoeken.
scan = re.sub(r"https://lblod\.mechelen\.be/LBLODWeb/Home/SearchPublicaties[^\"'\s]*", "", scan)
# Ook toegestaan, met dezelfde bedoeling als de zoek-ingang maar rechtstreeks: de gekoppelde
# officiële uittreksels (koppel_uittreksels.py). We whitelisten ENKEL het uittreksel_url-veld,
# niet elke GetPublication-URL: zo blijft de klep een verdwaalde agenda_url of een andere
# ongecontroleerde per-stuk-link wél vangen. Dit is een bewuste, gecureerde per-besluit-link.
scan = re.sub(r'"uittreksel_url":\s*"https://lblod\.mechelen\.be/[^"]*"', "", scan)
# Ook toegestaan, zelfde bedoeling: de directe zitting-PDF's uit de opzoektabel data['bron_pdfs']
# (maak_data.py) waarmee elk besluit rechtstreeks naar zijn besluitenlijst/notulen/agenda linkt in
# plaats van de zoekpagina. Dat is één blok met stringwaarden in het ingespoten data.json; we
# strippen het hele veld (geen nested braces erin), zodat de klep élke ándere ongecontroleerde
# lblod-URL (een verdwaalde agenda_url, een losse per-stuk-link) nog steeds vangt.
scan = re.sub(r'"bron_pdfs":\s*\{[^{}]*\}', "", scan)
treffers = [m.start() for m in re.finditer(r"lblod", scan, flags=re.IGNORECASE)]
if treffers:
    print(f"[bron-lek] 'lblod' komt {len(treffers)}× ongewenst voor in het eindbestand. Voorbeelden:")
    for pos in treffers[:3]:
        fragment = re.sub(r"\s+", " ", scan[max(0, pos - 60):pos + 60]).strip()
        print(f"   …{fragment}…")
    if not is_demo:
        sys.exit("[STOP] Live build geweigerd: verwijder de niet-toegestane bronverwijzingen "
                 "uit data.json/template.html, of houd is_demo op true.")
    print("   (waarschuwing genegeerd: is_demo staat nog op true)\n")

# 3b) Veiligheidsklep: persoonlijke e-mailadressen horen nooit op de site. schoon_brontekst.py
#     redacteert ze ('[e-mailadres]'), maar een losse her-tag (tag_items.py --batch) schrijft het
#     veld 'decoded' buiten de run_all-volgorde om terug, zodat een adres alsnog kan lekken. Deze
#     klep vangt dat: elk plat e-mailadres in het eindbestand of de zoekindex blokkeert een ECHTE
#     build. De bewuste placeholder in het contactformulier (jij@voorbeeld.be) is toegestaan.
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_OK = {"jij@voorbeeld.be"}
_mail_bronnen = [("dist/index.html", html)]
_zoek_src = BASE / "zoekindex.json"
if _zoek_src.exists():
    _mail_bronnen.append(("zoekindex.json", _zoek_src.read_text(encoding="utf-8")))
_mail_lek = sorted({m.group(0) for _n, _t in _mail_bronnen for m in EMAIL_RE.finditer(_t)} - EMAIL_OK)
if _mail_lek:
    print(f"[e-mail-lek] {len(_mail_lek)} adres(sen) in de build: {', '.join(_mail_lek[:5])}")
    if not is_demo:
        sys.exit("[STOP] Live build geweigerd: er staan e-mailadressen in de build. Draai "
                 "schoon_brontekst.py opnieuw, dan bouw_zoekindex.py, en bouw daarna de site "
                 "opnieuw (zie de volgorde in run_all.py).")
    print("   (waarschuwing genegeerd: is_demo staat nog op true)\n")
else:
    print("       e-mailcontrole: geen adressen in eindbestand en zoekindex ✓")

# 3c) Geboortedatums. Kandidatenlijsten voor de politieraad en het bijzonder comité staan in de
#     notulen als een tabel met een kolom 'Geboortedatum'. schoon_brontekst.py maskeert dag en
#     maand (··/··/74) en houdt het jaar. Deze klep controleert het resultaat: staat de tabelkop
#     in de build terwijl er nog een ONgemaskeerde dd/mm/jj vlak achter een naam staat, dan is de
#     redactie overgeslagen. Zoeken op de kop alleen volstaat niet, want die mag blijven staan.
#     De controle loopt per STUK, niet over de hele pagina: dd/mm/jj betekent elders iets heel
#     anders (eedaflegging, ontvangst, einde mandaat), en een paginabrede scan zou daarop blijven
#     afgaan, ook nadat de echte geboortedatums netjes gemaskeerd zijn.
GEB_KOP_RE = re.compile(r"geboortedatum\s+beroep", re.IGNORECASE)
GEB_RUW_RE = re.compile(r"(?<![\d/])\d{2}/\d{2}/\d{2}(?![\d/])")
GEB_VELDEN = ("brontekst", "decoded", "vraag", "antwoord", "titel")
_geb_kandidaten = [i for i in _items
                   if any(GEB_KOP_RE.search(str(i.get(v) or "")) for v in GEB_VELDEN)]
if _geb_kandidaten:
    _geb_lek = [m for i in _geb_kandidaten for v in GEB_VELDEN
                for m in GEB_RUW_RE.findall(str(i.get(v) or ""))]
    if _geb_lek:
        print(f"[geboortedatum] {len(_geb_lek)} ongemaskeerde datum(s) in "
              f"{len(_geb_kandidaten)} stuk(ken) met een kandidatentabel")
        if not is_demo:
            sys.exit("[STOP] Live build geweigerd: er staan ongemaskeerde geboortedatums in de "
                     "build. Draai schoon_brontekst.py opnieuw, dan bouw_zoekindex.py, en bouw "
                     "daarna de site opnieuw (zie de volgorde in run_all.py).")
        print("   (waarschuwing genegeerd: is_demo staat nog op true)\n")
    else:
        print("       geboortedatums: kandidatentabel(len) gemaskeerd ✓")

# 4) Schrijf het eindproduct.
out_dir = BASE / "dist"
out_dir.mkdir(exist_ok=True)
out_file = out_dir / "index.html"
out_file.write_text(html, encoding="utf-8")

# 5) CNAME voor het eigen (sub)domein op GitHub Pages. Door dit hier mee te schrijven
#    zit het altijd in de gepubliceerde map, ongeacht hoe dist/ ontstaat. Eénmalig in
#    GitHub: Settings → Pages → Custom domain = dit domein; en een DNS CNAME-record
#    'denkmee' → 'onderconstructie.github.io' bij de DNS-beheerder van asgaupaust.be.
CUSTOM_DOMAIN = "denkmee.asgaupaust.be"
(out_dir / "CNAME").write_text(CUSTOM_DOMAIN + "\n", encoding="utf-8")

# robots.txt. Er stond er geen, dus elke bot kreeg tot nu toe helemaal geen signaal (en bij een
# eigen domein zet GitHub Pages er zelf niets neer). Bewust OPEN: deze site bestaat om gevonden
# en gelezen te worden, en het project draait om hergebruik. Zelf de deur dichtdoen die we bij
# het stadsportaal voorbijlopen, zou slecht passen. Enkel de eigen foutpagina blijft eruit.
ROBOTS = """# %s
# Van harte welkom. Deze site is openbaar en mag gelezen, geciteerd en hergebruikt worden.
# De code staat publiek. Wil je zoiets voor je eigen stad bouwen, neem gerust contact op.
User-agent: *
Allow: /
Disallow: /404.html

# Zoekmachines en archieven blijven welkom: gevonden en bewaard worden is het punt.
# Deze crawlers niet. Ze brengen geen lezers, ze verzamelen linkprofielen om door te
# verkopen aan marketingbureaus, en ze halen daarvoor telkens de volledige pagina op.
User-agent: AhrefsBot
User-agent: SemrushBot
User-agent: MJ12bot
User-agent: DotBot
User-agent: BLEXBot
User-agent: DataForSeoBot
User-agent: Barkrowler
User-agent: SEOkicks
Disallow: /
"""
(out_dir / "robots.txt").write_text(ROBOTS % CUSTOM_DOMAIN, encoding="utf-8")

# 5a) Eigen 404-pagina. Zonder dit bestand toont GitHub Pages zijn Engelstalige "Page not
#     found": geen merk, geen Nederlands, geen weg terug. Eén tikfout in een gedeelde link
#     volstaat. Zelfstandig bestand met eigen stijl inline: een 404 mag niet afhangen van de
#     rest van de site.
PAGINA_404 = """<!doctype html>
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#FF0066">
<title>Pagina niet gevonden, Denk mee met Mechelen</title>
<meta name="robots" content="noindex">
<link rel="icon" type="image/png" href="/beelden/mug.png">
<style>
@font-face{font-family:'Geist';font-style:normal;font-weight:100 900;font-display:swap;src:url('/fonts/geist-var.woff2') format('woff2')}
@font-face{font-family:'JetBrains Mono';font-style:normal;font-weight:100 800;font-display:swap;src:url('/fonts/jbmono-var.woff2') format('woff2')}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#f5f1e8;background-image:radial-gradient(rgba(26,23,18,.04) 1px, transparent 1px);background-size:3px 3px;color:#2b2621;font-family:'Geist',system-ui,sans-serif;line-height:1.6;padding:1.5rem}
.doos{max-width:34rem;text-align:center}
.mug{width:96px;height:96px;border-radius:50%;margin:0 auto 1.6rem;display:block}
.code{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.72rem;letter-spacing:.14em;
  text-transform:uppercase;color:#c80054;margin:0 0 .6rem}
h1{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:clamp(1.5rem,5vw,2.2rem);
  font-weight:600;letter-spacing:-.02em;margin:0 0 .9rem}
p{color:#514a40;margin:0 0 1.8rem}
.wegen{display:flex;flex-wrap:wrap;gap:.7rem;justify-content:center}
.wegen a{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.74rem;letter-spacing:.06em;
  text-transform:uppercase;text-decoration:none;padding:.7rem 1.1rem;border-radius:999px;
  border:1px solid rgba(0,0,0,.18);color:#2b2621;transition:.15s}
.wegen a:hover{border-color:#FF0066;color:#c80054}
.wegen a.prim{background:#FF0066;border-color:#FF0066;color:#fff}
.wegen a.prim:hover{background:#b3004a;border-color:#b3004a;color:#fff}
</style>
</head>
<body>
  <main class="doos">
    <img class="mug" src="/beelden/mug.png" alt="" aria-hidden="true" width="512" height="512">
    <p class="code">Fout 404</p>
    <h1>Deze pagina bestaat niet</h1>
    <p>Misschien is de link verouderd, of staat er een tikfout in het adres.
       Hieronder raak je weer op weg.</p>
    <div class="wegen">
      <a class="prim" href="/">Naar Denk mee</a>
      <a href="/techniek/">Hoe het werkt</a>
      <a href="https://asgaupaust.be/">As Gau Paust</a>
    </div>
  </main>
</body>
</html>
"""
(out_dir / "404.html").write_text(PAGINA_404, encoding="utf-8")

# 5b) Zelf-gehoste lettertypes meekopieren naar dist/fonts/. Sinds we niet meer bij Google
#     Fonts laden, gaat er geen bezoekers-IP meer naar derden. We nemen de variabele woff2's
#     mee plus de SIL Open Font License-teksten (die horen bij herdistributie onder de OFL).
fonts_src = BASE / "fonts"
if fonts_src.exists():
    fonts_dst = out_dir / "fonts"
    fonts_dst.mkdir(exist_ok=True)
    gekopieerd = 0
    for f in fonts_src.iterdir():
        if f.suffix.lower() in (".woff2", ".txt"):
            shutil.copy2(f, fonts_dst / f.name)
            gekopieerd += 1
    print(f"       fonts gekopieerd naar dist/fonts/: {gekopieerd} bestanden (woff2 + OFL)")

# 5b-bis) Eigen beeldmerken meekopieren naar dist/beelden/. Voorlopig enkel de mug (het
#     platform-teken van As Gau Paust) voor de portaal-terugkeer bovenaan het menu. Zelf-
#     gehost, net als bij Lees mee: geen bezoekers-IP naar een ander domein. De toren blijft
#     inline (base64) als sitemerk; de mug is een echt bestand omdat het een rasterbeeld is.
beelden_src = BASE / "beelden"
if beelden_src.exists():
    beelden_dst = out_dir / "beelden"
    beelden_dst.mkdir(exist_ok=True)
    b_kopie = 0
    for f in beelden_src.iterdir():
        if f.suffix.lower() in (".png", ".svg", ".webp"):
            shutil.copy2(f, beelden_dst / f.name)
            b_kopie += 1
    print(f"       beelden gekopieerd naar dist/beelden/: {b_kopie} bestand(en)")

# 5b-ter) App-bestanden: het webmanifest en de service worker maken de site installeerbaar
#     op het beginscherm (zelfde patroon als asgaupaust.be: netwerk-eerst, geen trackers).
for app_bestand in ("manifest.json", "sw.js"):
    bron = BASE / app_bestand
    if bron.exists():
        shutil.copy2(bron, out_dir / app_bestand)
print("       app-bestanden gekopieerd: manifest.json + sw.js")

# 5c) Volledige-tekstindex voor de zoekbalk meekopiëren (gemaakt door bouw_zoekindex.py).
#     De site laadt hem pas bij de eerste zoekopdracht, dus de pagina zelf blijft licht.
zoekindex = BASE / "zoekindex.json"
if zoekindex.exists():
    shutil.copy2(zoekindex, out_dir / "zoekindex.json")
    print(f"       zoekindex gekopieerd: dist/zoekindex.json ({zoekindex.stat().st_size/1024:,.0f} kB)")

# 6) Aparte techniek-pagina op /techniek/ (architectuur + waarom lokaal + zelfkritiek).
#    Ze deelt EXACT de <head> van de hoofdpagina (zelfde CSS, fonts en brand-mark/favicon):
#    één bron, geen duplicaat dat uit de pas gaat lopen. Enkel titel en omschrijving
#    krijgen een eigen waarde. Heeft geen data nodig — het is een vaste uitleg-pagina.
tech_template_pad = BASE / "template-techniek.html"
if tech_template_pad.exists():
    head_match = re.search(r"<head>.*?</head>", template, flags=re.DOTALL)
    gedeelde_head = head_match.group(0) if head_match else ""
    gedeelde_head = re.sub(
        r"<title>.*?</title>",
        "<title>Technische pagina, Denk mee met Mechelen</title>",
        gedeelde_head, count=1, flags=re.DOTALL)
    _tech_titel = "Technische pagina, Denk mee met Mechelen"
    _tech_oms = ("De technische pagina van Denk mee met Mechelen: de architectuur, waarom het "
                 "lokaal draait, en wat we mogelijk over het hoofd zien.")
    _tech_url = "https://denkmee.asgaupaust.be/techniek/"
    # Ook de deelkaart-tags meenemen: anders deelt /techniek/ zich als de startpagina.
    for _patroon, _nieuw in (
        (r'(<meta name="description" content=")[^"]*(">)', _tech_oms),
        (r'(<meta property="og:title" content=")[^"]*(">)', _tech_titel),
        (r'(<meta property="og:description" content=")[^"]*(">)', _tech_oms),
        (r'(<meta property="og:url" content=")[^"]*(">)', _tech_url),
        (r'(<link rel="canonical" href=")[^"]*(">)', _tech_url),
    ):
        gedeelde_head = re.sub(_patroon, lambda mm, w=_nieuw: mm.group(1) + w + mm.group(2),
                               gedeelde_head, count=1)
    tech_html = tech_template_pad.read_text(encoding="utf-8").replace("__DENKMEE_HEAD__", gedeelde_head)

    # De techniek-pagina viel buiten beide veiligheidskleppen hierboven: ze wordt ná de controles
    # gebouwd en geschreven. Ze erft wél de <head> van de hoofdpagina, dus een lek daarin zou hier
    # meeliften. Zelfde twee tests, vóór het schrijven.
    _tech_scan = re.sub(r"https://lblod\.mechelen\.be/LBLODWeb/Home/SearchPublicaties[^\"'\s]*", "", tech_html)
    for _ingang in TOEGESTANE_INGANGEN:
        _tech_scan = _tech_scan.replace(_ingang, "")
    if re.search(r"lblod", _tech_scan, flags=re.IGNORECASE) and not is_demo:
        sys.exit("[STOP] Live build geweigerd: niet-toegestane lblod-verwijzing in de techniek-pagina.")
    _tech_mail = sorted({m.group(0) for m in EMAIL_RE.finditer(tech_html)} - EMAIL_OK)
    if _tech_mail and not is_demo:
        sys.exit(f"[STOP] Live build geweigerd: e-mailadres(sen) in de techniek-pagina: {', '.join(_tech_mail[:3])}")

    tech_dir = out_dir / "techniek"
    tech_dir.mkdir(exist_ok=True)
    (tech_dir / "index.html").write_text(tech_html, encoding="utf-8")
    print(f"       techniek-pagina gebouwd: dist/techniek/index.html ({len(tech_html):,} tekens)")

demo = " (LET OP: demo-data — is_demo staat nog op true)" if is_demo else ""
print(f"Klaar! index.html gebouwd uit template.html + data.json: {len(html):,} tekens{demo}")
print(f"       CNAME geschreven: {CUSTOM_DOMAIN}")
