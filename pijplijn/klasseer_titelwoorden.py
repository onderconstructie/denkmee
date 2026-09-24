"""
klasseer_titelwoorden.py: welke titelwoorden wijzen op zichzelf een onderwerp aan?

De dossiervorming op de site (buildDossiers in template.html) bundelt stukken op het meest
onderscheidende woord uit hun titel. "Onderscheidend" betekende tot september 2026: zeldzaam in de
titels. Maar zeldzaam is niet hetzelfde als specifiek. Woorden als 'uitrol', 'inbegrip' of
'tegenbod' staan in weinig titels en zeggen toch niets over de zaak: zo hingen de uitrol van Diftar
en een nieuwe mascotte aan elkaar, en een zendmast aan een handelspand.

Dit script legt elk titelwoord voor aan het taalmodel: noemt het woord op zichzelf een concrete
zaak ('o', zoals zwembad, lachgas, Ragheno, politieverordening) of is het algemeen ('a', een
handeling, verandering, eigenschap, tijdsaanduiding of etiket)? Elk nieuw woord krijgt drie
onafhankelijke oordelen en de meerderheid telt; gemeten gaven drie rondes voor 94 tot 96 procent
van de woorden hetzelfde oordeel. De uitkomst staat in titelwoorden.json; build.py geeft de
algemene woorden mee aan de pagina als D.dos_algemeen.

De pagina gebruikt het oordeel op twee plaatsen. Een algemeen woord blijft een handtekening, maar
de koppeling moet bevestigd worden: door een gedeeld woord dat wél een onderwerp is, een gedeeld
kernbegrip, dezelfde ene straat of de verwijzing van het college naar de raad. En een gedeeld woord
bevestigt enkel als het een onderwerp is, hoe vaak het ook voorkomt.

Idempotent: enkel woorden die nog niet in het bestand staan, gaan naar het model; een gewone run
kost dus bijna niets. Zonder API-sleutel of bij een fout blijft het bestand zoals het was, en
behandelt de pagina een nieuw woord als onderwerp, zoals vóór deze stap.

De woordenlijst volgt exact de tokenizer van de pagina (dosTokens en dosStam): kleine letters,
accenten weg, woorden van minstens vijf tekens. Kandidaat is elk titelwoord waarvan de stam in
minstens twee titels staat; een woord in één titel koppelt toch niets.

Draai:  python pijplijn/klasseer_titelwoorden.py            (enkel nieuwe woorden)
        python pijplijn/klasseer_titelwoorden.py --droog    (tellen, geen API)
"""
import os, sys, re, json, time, argparse, unicodedata
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent.parent
MODEL = "claude-sonnet-5"
PER_BATCH = 250
STEMMEN = 3
TOKEN = re.compile(r"\b[a-z][a-z\-]{4,}\b", re.ASCII)


def zonder_accenten(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def tokens(titel):
    return set(TOKEN.findall(zonder_accenten((titel or "").lower())))


def stam(w):
    if len(w) >= 7 and w.endswith("en") and not w.endswith("een"):
        return w[:-2]
    if len(w) >= 6 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def titels(data):
    for a in data.get("agendapunten", []):
        yield a.get("titel") or ""
    for c in data.get("college_beslissingen", []):
        yield c.get("titel") or ""
    for v in data.get("schriftelijke_vragen", []):
        yield v.get("titel") or v.get("onderwerp") or ""


def kandidaten(data):
    per_titel = [tokens(t) for t in titels(data)]
    df = {}
    for ts in per_titel:
        for s in {stam(w) for w in ts}:
            df[s] = df.get(s, 0) + 1
    return sorted({w for ts in per_titel for w in ts if df[stam(w)] >= 2})


SYSTEM = """Je beoordeelt woorden uit de titels van besluiten van de stad Mechelen. Een website bundelt besluiten over dezelfde zaak tot één dossier, en doet dat soms op basis van één enkel woord dat twee titels delen. Dat werkt alleen als dat woord op zichzelf de zaak aanwijst.

Geef per woord één letter:
- "o" (onderwerp): het woord noemt op zichzelf een concrete zaak waar een reeks besluiten over kan gaan. Een plek, gebouw, voorziening, organisatie, project, dienst, voorwerp, doelgroep of een specifieke activiteit. Voorbeelden: zwembad, zwemunits, daklozenopvang, lachgas, fuiven, betaalterminals, windturbine, huiskatten, taxicheques, straathoekwerk, brandweer, jeugdwerk, evenementenhal, carillon, begraafplaats.
- "a" (algemeen): het woord kan net zo goed staan in besluiten over totaal verschillende zaken. Een handeling, procedure, verandering, eigenschap, hoeveelheid, tijdsaanduiding, of een etiket van het document zelf. Voorbeelden: behoud, uitrol, vervolgvraag, vervolg, verhoging, toename, analyse, toegang, toestand, inbegrip, verstrekt, gedeeltelijke, dringende, specifieke, meerdere, bezoek, workshop, conferentie, tegenbod, opmerking, finaal, geactualiseerd, engagementen, verantwoording.

Twijfel je, kies dan "o".
Antwoord met UITSLUITEND een JSON-object dat elk gegeven woord exact zoals gegeven koppelt aan "o" of "a", zonder uitleg."""


def vraag(client, woorden):
    import anthropic
    user = "Woorden:\n" + json.dumps(woorden, ensure_ascii=False)
    for poging in range(5):
        try:
            resp = client.messages.create(model=MODEL, max_tokens=16000, thinking={"type": "disabled"},
                                          system=SYSTEM, messages=[{"role": "user", "content": user}])
            tekst = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            m = re.search(r"\{.*\}", tekst, re.S)
            uit = json.loads(m.group(0)) if m else {}
            return {w: uit[w] for w in woorden if uit.get(w) in ("o", "a")}
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError):
            if poging == 4:
                raise
            time.sleep(2 ** poging)
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(BASE / "data.json"))
    ap.add_argument("--uit", default=str(BASE / "titelwoorden.json"))
    ap.add_argument("--droog", action="store_true", help="enkel tellen, geen API")
    args = ap.parse_args()

    data = json.loads(Path(args.data).read_text(encoding="utf-8-sig"))
    pad = Path(args.uit)
    bestand = json.loads(pad.read_text(encoding="utf-8")) if pad.exists() else {"model": MODEL, "woorden": {}}
    bekend = bestand.setdefault("woorden", {})
    nieuw = [w for w in kandidaten(data) if w not in bekend]
    print(f"Titelwoorden: {len(bekend)} beoordeeld, {len(nieuw)} nieuw.")
    if not nieuw or args.droog:
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  (overgeslagen: geen ANTHROPIC_API_KEY; nieuwe woorden tellen voorlopig als onderwerp.)")
        return

    import anthropic
    client = anthropic.Anthropic()
    for i in range(0, len(nieuw), PER_BATCH):
        deel = nieuw[i:i + PER_BATCH]
        rondes = [vraag(client, deel) for _ in range(STEMMEN)]
        uit = {}
        for w in deel:
            stemmen = [r[w] for r in rondes if w in r]
            if stemmen:
                uit[w] = "a" if stemmen.count("a") * 2 > len(stemmen) else "o"
        bekend.update(uit)
        print(f"  {i + len(deel)}/{len(nieuw)}: {len(uit)} beoordeeld, {sum(v == 'a' for v in uit.values())} algemeen")
        bestand["woorden"] = dict(sorted(bekend.items()))
        pad.write_text(json.dumps(bestand, ensure_ascii=False, indent=0), encoding="utf-8")
    alg = sum(v == "a" for v in bekend.values())
    print(f"Klaar: {len(bekend)} woorden, waarvan {alg} algemeen -> {pad.name}")


if __name__ == "__main__":
    main()
