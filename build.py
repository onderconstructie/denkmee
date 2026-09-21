from pathlib import Path

# --- De poort scherp zetten. Hooks reizen niet mee met een kloon en git config is per kloon,
#     dus zonder deze regels staat de bescherming na een verse kloon uit zonder dat iets het
#     meldt. Elke build zet ze opnieuw goed. Zie .githooks/poort.py voor wat ze weigert. ---
def _poort_scherpzetten():
    import subprocess
    hier = Path(__file__).resolve().parent
    if not (hier / ".githooks" / "poort.py").exists():
        return
    huidig = subprocess.run(["git", "config", "core.hooksPath"], capture_output=True,
                            cwd=str(hier)).stdout.decode("utf-8", "replace").strip()
    if huidig != ".githooks":
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=str(hier))
        print("       poort scherpgezet: core.hooksPath -> .githooks")

_poort_scherpzetten()

import sys
import json
import re
import shutil
from datetime import date, datetime
from html import escape as html_escape

# print() met → … × werkt zo ook op een Windows-console die standaard cp1252 gebruikt
# (anders crasht een niet-ASCII-teken met een UnicodeEncodeError).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE / "pijplijn"))   # schoon_brontekst en koppel_uittreksels staan daar

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

# 2d) De zittingskaart vanaf de eerste verf. Type, datum en tijd staan al in de HTML, in het
#     formaat van hydrateHero(), dagen en label in dat van tick(); corrigeerVolgendeZitting() blijft in de browser het
#     vangnet voor een zitting die sinds de build voorbij is. Aantal dagen en label houden enkel de
#     breedte vast: ze blijven onzichtbaar tot tick() ze met de datum van de lezer invult. Zonder
#     zitting blijven datum en label leeg, zoals hydrateHero() ze dan laat.
MAANDEN = ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus",
           "september", "oktober", "november", "december")
WEEKDAGEN = ("maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag")
_zit = data.get("next_meeting") or {}
_kaart_tag = "Geen geplande zitting"
if _zit:
    _zit_type = "Raad voor maatschappelijk welzijn" if _zit.get("type") == "RMW" else (_zit.get("type") or "Gemeenteraad")
    _kaart_tag = "Volgende " + _zit_type.lower()
_kaart_datum, _kaart_tijd, _kaart_dagen, _kaart_label = "", "", 0, ""
if _zit.get("date"):
    _zd = date.fromisoformat(str(_zit["date"])[:10])
    _kaart_datum = f"{_zd.day} {MAANDEN[_zd.month - 1]} {_zd.year}"
    _kaart_dagen = max(0, (_zd - date.today()).days)
    _kaart_label = "dag te gaan" if _kaart_dagen == 1 else "dagen te gaan"
if _zit.get("datetime"):
    _zdt = datetime.fromisoformat(str(_zit["datetime"]))
    if _zdt.tzinfo:
        _zdt = _zdt.astimezone()
    _kaart_tijd = f"{WEEKDAGEN[_zdt.weekday()]} · {_zdt:%H:%M}"
_kaart = {
    "__MC_TAG__": _kaart_tag,
    "__MC_DATUM__": _kaart_datum,
    "__MC_TIJD__": _kaart_tijd,
    "__CD_DAGEN__": str(_kaart_dagen),
    "__CD_LABEL__": _kaart_label,
}
html = template
for _token, _waarde in _kaart.items():
    if html.count(_token) != 1:
        sys.exit(f"[STOP] Plaatshouder {_token} staat {html.count(_token)}x in template.html, verwacht 1x.")
    html = html.replace(_token, html_escape(_waarde))

data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
html = html.replace("__DENKMEE_DATA__", data_json)

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
    _proef = [i.get("id") for i in _items if str(i.get("decoded") or "").startswith("[dry-run")]
    if _proef:
        sys.exit(f"[STOP] Live build geweigerd: {len(_proef)} samenvatting(en) zijn plaatshouders van een "
                 f"proefrun (tag_items.py --dry-run), bv. {_proef[0]}. Herstel met git checkout -- data.json "
                 f"en een volledige run_all.py.")
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

# 3c2) Veiligheidsklep: de ledentabel van een adviesraad met burgers (de GECORO benoemt
#      deskundigen en vertegenwoordigers van verenigingen) mag geen persoonsnamen dragen, en een
#      ondersteunende personeelsrol evenmin. schoon_brontekst.py maskeert die via
#      maskeer_ledenlijst(); deze klep controleert het resultaat, zodat een volgende benoemingsronde
#      niet stil opnieuw namen publiceert. Aanleiding: op 16/09/2026 stond de volledige GECORO-tabel
#      in de build, tot in de samenvatting.
import schoon_brontekst as _sb_leden
_leden_lek = []
for _it in _items:
    for _v in GEB_VELDEN:
        _t = str(_it.get(_v) or "")
        if not _t:
            continue
        _schoon, _n = _sb_leden.maskeer_ledenlijst(_t)
        if _n:
            _leden_lek.append(str(_it.get("id")))
            break
if _leden_lek:
    print(f"[ledenlijst] persoonsnamen in een ledentabel of naast een personeelsrol in "
          f"{len(_leden_lek)} stuk(ken): " + ", ".join(_leden_lek[:5]))
    if not is_demo:
        sys.exit("[STOP] Live build geweigerd: er staan namen van burgers in een ledentabel. Draai "
                 "schoon_brontekst.py opnieuw, dan bouw_zoekindex.py, en bouw daarna de site opnieuw.")
    print("   (waarschuwing genegeerd: is_demo staat nog op true)\n")
else:
    print("       ledentabellen: geen persoonsnamen in de build ✓")

# 3d) Woonadressen en geboortedatums in lopende tekst. schoon_brontekst.py maskeert een adres na
#     "wonend/woonachtig/gedomicilieerd" en een datum na "°" of "geboren". Deze klep controleert of
#     dat ook echt gebeurd is, met exact dezelfde patronen, zodat een losse stap die de opkuis
#     overslaat de site niet live kan zetten. Aanleiding: op 14/09/2026 stonden er twee woonadressen
#     van burgers live, telkens naast een naam die zelf netjes "[naam]" was. De naam maskeren
#     beschermt niemand als de zin ernaast zegt waar die persoon woont.
import schoon_brontekst as _sb
_ctx_lek = []
for i in _items:
    for v in GEB_VELDEN:
        _t = str(i.get(v) or "")
        # Dezelfde regels als maskeer_context, behalve het contactformulier: dat staat enkel in de
        # tekst die de zoekindex voedt, niet in deze velden.
        if (_sb.ADRES_NA_WOON.search(_t) or _sb.ADRES_NA_NAAM.search(_t) or _sb.ADRES_TEGENPROEF.search(_t)
                or _sb.EMAIL_VERHULD.search(_t) or _sb.GEB_TEKEN.search(_t)):
            _ctx_lek.append(str(i.get("id")))
            break
if _ctx_lek:
    print(f"[context-lek] woonadres of geboortedatum in {len(_ctx_lek)} stuk(ken): "
          f"{', '.join(_ctx_lek[:5])}")
    if not is_demo:
        sys.exit("[STOP] Live build geweigerd: er staat een woonadres of geboortedatum van een persoon "
                 "in de build. Draai schoon_brontekst.py opnieuw, dan bouw_zoekindex.py, en bouw "
                 "daarna de site opnieuw (zie de volgorde in run_all.py).")
    print("   (waarschuwing genegeerd: is_demo staat nog op true)\n")
else:
    print("       woonadressen en geboortedatums: niets in de build ✓")

# 3e) Uittreksel-koppeling. De tekst die de samenvatting van een stuk voedt, moet van het eigen
#     besluit komen. koppel_uittreksels.py koppelt op het puntnummer dat de stad zelf in de kopregel
#     zet; deze klep leest de koppeltabel na, zodat een latere wijziging aan de koppeling niet stil een
#     verkeerde brontekst live zet. Aanleiding: op 18/09/2026 lazen 137 stukken een bijlage in plaats
#     van hun besluit, en toonde een cultuurpunt een samenvatting over parkeertarieven. De toetsen:
#     de gecachete tekst hoort bij het document zelf, en het bestand waaruit ze kwam bestaat; een
#     bijlage hangt bij het punt van haar eigen uittreksel; een document hangt aan één punt; een
#     punt heeft hooguit één uittreksel; het puntnummer in de kopregel is dat van het stuk; geen
#     twee documenten delen een tekst in de koppeltabel, of een bestandsnaam in de catalogus.
#     Die laatste twee bestaan omdat bijlagen het publicatie-id van hun uittreksel delen: gaf
#     pdf_pad hun één bestandsnaam, dan haalde fetch_uittreksels.py er maar één op en las elke
#     andere bijlage met dat id de tekst van dat ene bestand (aanleiding 19/09/2026). De zoekindex
#     en de tagging lezen de tekst via de cache_sleutel in de koppeltabel, dus de klep toetst die
#     sleutel zelf en niet enkel de naamgeving: een verouderde koppeltabel wijst anders nog naar het
#     bestand van een ander document.
#     De koppeltabel en de tekstcache staan buiten git; ontbreken ze, dan valt er niets te toetsen.
_koppel_pad = BASE / "data" / "uittreksel_koppeling.json"
_index_pad = BASE / "data" / "uittreksels_index.json"
if _koppel_pad.exists() and _index_pad.exists():
    import hashlib as _hl
    import koppel_uittreksels as _ku
    _tabel = json.loads(_koppel_pad.read_text(encoding="utf-8"))
    _per_url = {d["url"]: d for d in json.loads(_index_pad.read_text(encoding="utf-8"))}
    _k_tekst, _k_wees, _k_nummer, _k_meer, _k_bij = [], [], [], [], {}
    _k_zonder, _k_sleutel, _k_sleutel_bij = [], {}, {}
    for _item, _refs in _tabel.items():
        _eigen = {r["uittreksel_id"] for r in _refs if r["klasse"] == "uittreksel"}
        if sum(1 for r in _refs if r["klasse"] == "uittreksel") > 1:
            _k_meer.append(_item)
        for _r in _refs:
            _k_bij.setdefault(_r["url"], set()).add(_item)
            if _r["klasse"] == "bijlage" and _r["uittreksel_id"] not in _eigen:
                _k_wees.append(_item)
            _doc, _sl = _per_url.get(_r["url"]), _r.get("cache_sleutel")
            if _sl:
                _k_sleutel.setdefault(_sl, set()).add(_r["url"])
                _k_sleutel_bij.setdefault(_sl, set()).add(_item)
            if not (_doc and _sl):
                continue
            _p = _ku.pdf_pad(_doc)
            # Zonder bestand valt niet na te gaan van welk document de gecachete tekst komt.
            if not _p.exists():
                _k_zonder.append(_item)
                continue
            _st = _p.stat()
            if _hl.sha1(f"{_p.as_posix()}|{_st.st_mtime_ns}|{_st.st_size}".encode()).hexdigest() != _sl:
                _k_tekst.append(_item)
                continue
            _c = _ku.CACHE / (_sl + ".txt")
            if _r["klasse"] == "uittreksel" and _c.exists():
                _nr, _lt = _ku.kopregel_nummer(_c.read_text(encoding="utf-8"))
                _inr, _ilt = _ku.puntnummer_uit_id(_item)
                if _nr is not None and _inr is not None and (_nr != _inr or (_lt and _ilt and _lt != _ilt)):
                    _k_nummer.append(_item)
    _k_dubbel = [u for u, s in _k_bij.items() if len(s) > 1]
    # Dezelfde sleutel onder twee url's: twee documenten lezen dezelfde tekst.
    _k_gedeeld = [_sl for _sl, _u in _k_sleutel.items() if len(_u) > 1]
    # Dezelfde bestandsnaam voor twee url's uit de catalogus: de download schrijft er maar een weg.
    _k_pad = {}
    for _u, _d in _per_url.items():
        _k_pad.setdefault(_ku.pdf_pad(_d), set()).add(_u)
    _k_botsing = {p: u for p, u in _k_pad.items() if len(u) > 1}
    _k_fout = sorted(set(_k_tekst + _k_wees + _k_nummer + _k_meer + _k_zonder
                         + [_i for _sl in _k_gedeeld for _i in _k_sleutel_bij[_sl]]))
    if _k_fout or _k_dubbel or _k_botsing:
        print(f"[koppeling] tekst van een ander document {len(set(_k_tekst))} · bijlage los van haar "
              f"uittreksel {len(set(_k_wees))} · puntnummer klopt niet {len(set(_k_nummer))} · punt met meer dan een "
              f"uittreksel {len(_k_meer)} · document aan meer dan een stuk {len(_k_dubbel)} · tekst gedeeld door "
              f"meer dan een document {len(_k_gedeeld)} · tekst zonder bestand {len(set(_k_zonder))} · "
              f"bestandsnaam gedeeld in de catalogus {len(_k_botsing)}: " + (", ".join(_k_fout[:5]) or "-"))
        if _k_botsing:
            print("   gedeelde bestandsnaam: " + ", ".join(f"{p.parent.parent.parent.name}/{p.parent.parent.name}/"
                                                         f"{p.name} ({len(u)} url's)"
                                                         for p, u in sorted(_k_botsing.items())[:5]))
        if not is_demo:
            if _k_botsing:
                sys.exit("[STOP] Live build geweigerd: meerdere documenten krijgen dezelfde bestandsnaam, "
                         "dus een stuk leest de tekst van een ander document. Geef in fetch_uittreksels.py en "
                         "koppel_uittreksels.pdf_pad elk document een eigen bestandsnaam, haal de ontbrekende "
                         "documenten op, draai koppel_uittreksels.py opnieuw, hertag de geraakte stukken, en "
                         "bouw daarna de site opnieuw (zie de volgorde in run_all.py).")
            sys.exit("[STOP] Live build geweigerd: een stuk leest de tekst van een ander document, of een "
                     "tekst waarvan het bestand ontbreekt. Haal de ontbrekende documenten op "
                     "(fetch_uittreksels.py --download), draai koppel_uittreksels.py opnieuw, hertag de "
                     "geraakte stukken, en bouw daarna de site opnieuw (zie de volgorde in run_all.py).")
        print("   (waarschuwing genegeerd: is_demo staat nog op true)\n")
    else:
        print(f"       uittreksel-koppeling: {sum(len(v) for v in _tabel.values())} documenten, elk bij "
              f"het eigen punt; {sum(len(u) for u in _k_sleutel.values())} met tekst, elk uit een eigen "
              f"bestand; {len(_per_url)} in de catalogus, elk met een eigen bestandsnaam ✓")
else:
    print("       uittreksel-koppeling: geen koppeltabel, niets te toetsen")

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
<meta name="theme-color" content="#f5f1e8">
<title>Pagina niet gevonden, Denk mee met Mechelen</title>
<meta name="robots" content="noindex">
<link rel="icon" type="image/png" href="/beelden/mug.png">
<link rel="preload" href="/fonts/geist-var.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="/fonts/jbmono-var.woff2" as="font" type="font/woff2" crossorigin>
<style>
@font-face{font-family:'Geist';font-style:normal;font-weight:100 900;font-display:swap;src:url('/fonts/geist-var.woff2') format('woff2')}
@font-face{font-family:'JetBrains Mono';font-style:normal;font-weight:100 800;font-display:swap;src:url('/fonts/jbmono-var.woff2') format('woff2')}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
  background:#f5f1e8;background-image:radial-gradient(rgba(26,23,18,.04) 1px, transparent 1px);background-size:3px 3px;color:#2b2621;font-family:'Geist',system-ui,sans-serif;line-height:1.6;padding:1.5rem}
.doos{max-width:34rem;text-align:center}
.mug{width:96px;height:96px;border-radius:50%;margin:0 auto 1.6rem;display:block}
.code{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.72rem;letter-spacing:.14em;
  text-transform:uppercase;color:#005f51;margin:0 0 .6rem}
h1{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:clamp(1.5rem,5vw,2.2rem);
  font-weight:600;letter-spacing:-.02em;margin:0 0 .9rem}
p{color:#514a40;margin:0 0 1.8rem}
.wegen{display:flex;flex-wrap:wrap;gap:.7rem;justify-content:center}
.wegen a{font-family:'JetBrains Mono',ui-monospace,monospace;font-size:.74rem;letter-spacing:.06em;
  text-transform:uppercase;text-decoration:none;padding:.7rem 1.1rem;border-radius:999px;
  border:1px solid rgba(0,0,0,.18);color:#2b2621;transition:.15s}
.wegen a:hover{border-color:#00796b;color:#005f51}
.wegen a.prim{background:#00796b;border-color:#00796b;color:#fff}
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
