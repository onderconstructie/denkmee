"""
oordeel_vragen.py: hoort een schriftelijke vraag bij hetzelfde dossier als een ander stuk?

De dossiers op de site bundelen stukken over dezelfde concrete zaak (buildDossiers in template.html).
Voor de meeste stukken ligt er een zelfde-zaakoordeel klaar (dossier_oordelen.json). Een schriftelijke
vraag van een raadslid krijgt daar geen oordeel, en de eigen regels van de site koppelden een vraag vaak
op een gedeeld woord of een straatnaam aan een stuk over iets anders.

Dit script legt elke schriftelijke vraag naast haar kandidaten en laat het taalmodel per paar zeggen of
het over dezelfde concrete zaak gaat. Kandidaten zijn de stukken waarmee de vraag de meeste zeldzame
woorden deelt (titel, kernbegrippen, straten; algemene titelwoorden uit titelwoorden.json tellen niet),
eerdere vragen van hetzelfde raadslid met een gedeeld zeldzaam woord, en paren die een sessie één keer
met --extra meegaf. Elk paar krijgt twee oordelen, één keer met de vraag eerst en één keer met het andere
stuk eerst. Enkel als beide "ja" zeggen, telt het paar als zelfde zaak.

De uitkomst staat in vraag_oordelen.json (enkel stuk-id's): de ja-paren, de paren met een ander oordeel
(nee) en de vragen die beoordeeld werden. build.py geeft het mee aan de pagina (D.dos_vragen). Daar doet een
beoordeelde vraag niet mee in de eigen regels. Daarna sluit ze aan bij hooguit een groep (de ja-stap in
buildDossiers): vragen met onderlinge ja-paren kiezen samen de groep met de meeste ja-paren, tenzij ze daar minstens
evenveel andere oordelen hebben (zegt het model dat twee van die vragen niet over dezelfde zaak gaan, dan gaat de
vraag met de meeste zulke oordelen eruit en kiest apart); vragen zonder groep vormen samen een dossier; en een stuk
zonder eigen oordeel dat alleen stond, volgt de vragen waarmee het een ja-paar heeft. Ontbreekt het bestand, dan bundelt de site de vragen
zoals voorheen.

De oordelen staan in data/vraag_oordelen_cache.json (buiten git), op een sleutel uit het model, de
promptversie en de tekst van beide stukken: een gewone run beoordeelt enkel nieuwe paren en kost dus
bijna niets. Verandert een samenvatting, dan wordt het paar opnieuw beoordeeld. Een gewone run doet
hooguit MAX_LOS oordelen; meer gaat via de Batches-API (--batch, met --go om echt in te dienen).
Het bestand wordt enkel herschreven als elk kandidaat-paar volledig beoordeeld is; zonder API-sleutel, bij
te veel werk of bij een fout blijft het zoals het was. Een onleesbare cache stopt het script (anders zouden
alle betaalde oordelen verloren gaan); bij elke bewaring blijft de vorige versie als .bak staan. Een batch
bewaart haar id in de cache, zodat een volgende run haar resultaten nog ophaalt als het pollen afbrak; een gewone
run (zonder --batch) wacht daar niet op, maar laat de uitkomst dan zoals ze was. De
promptversie (PROMPT) zit in de sleutel; verandert de prompt zonder nieuwe versie, dan stopt het script.

Draai:  python pijplijn/oordeel_vragen.py                    (nieuwe paren, hooguit MAX_LOS oordelen)
        python pijplijn/oordeel_vragen.py --droog            (tellen, geen API)
        python pijplijn/oordeel_vragen.py --batch [--go]     (raming, of indienen via de Batches-API)
        python pijplijn/oordeel_vragen.py --extra paren.json (paren één keer als kandidaat toevoegen)
"""
import os, sys, re, json, math, time, hashlib, argparse, unicodedata
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

BASE = Path(__file__).parent.parent
MODEL = "claude-sonnet-5"
PROMPT = "v1"
TOP = 6                 # zoveel gelijkende stukken per vraag
DREMPEL = 6.0           # minimale gelijkenis (som van de zeldzaamheid van de gedeelde woorden)
DF_MAX = 40             # een woord in meer stukken telt niet als zeldzaam
MAX_LOS = 300           # meer oordelen in één gewone run: dan via --batch
TEKST_MAX = 900
CACHE = BASE / "data" / "vraag_oordelen_cache.json"
UIT = BASE / "vraag_oordelen.json"
WOORD = re.compile(r"\b[a-z][a-z\-]{4,}\b", re.ASCII)


def zonder_accenten(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def lijst(v):
    """Kernbegrippen en straten staan soms als tekst van een lijst in data.json."""
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str) and v.strip().startswith("["):
        try:
            import ast
            return [str(x) for x in ast.literal_eval(v)]
        except (ValueError, SyntaxError):
            return []
    return []


def stopwoorden():
    """De stoplijst van de pagina zelf (DOS_STOP in template.html), zodat er één bron is."""
    try:
        t = (BASE / "template.html").read_text(encoding="utf-8")
        m = re.search(r"const DOS_STOP = new Set\('([^']*)'", t)
        if m:
            return set(m.group(1).split())
    except OSError:
        pass
    return set()


def algemene_woorden():
    try:
        w = json.loads((BASE / "titelwoorden.json").read_text(encoding="utf-8")).get("woorden", {})
        return {k for k, v in w.items() if v == "a"}
    except (OSError, ValueError):
        return set()


def stukken(data):
    """Alle stukken als {id: {...}}, met de velden die het oordeel en de kandidaten nodig hebben."""
    uit = {}
    for a in data.get("agendapunten", []):
        uit[a["id"]] = dict(titel=a.get("titel") or "", datum=a.get("sessie_date") or "",
                            orgaan="raad voor maatschappelijk welzijn" if a["id"].startswith("rmw") else "gemeenteraad",
                            tekst=a.get("decoded") or "", kb=lijst(a.get("kernbegrippen")), straten=lijst(a.get("streets")),
                            indiener=None)
    for c in data.get("college_beslissingen", []):
        uit[c["id"]] = dict(titel=c.get("titel") or "", datum=c.get("date") or "", orgaan=(c.get("orgaan") or "college").lower(),
                            tekst=c.get("decoded") or "", kb=lijst(c.get("kernbegrippen")), straten=[], indiener=None)
    for v in data.get("schriftelijke_vragen", []):
        uit[v["id"]] = dict(titel=v.get("titel") or v.get("onderwerp") or "", datum=v.get("date") or "",
                            orgaan="schriftelijke vraag", tekst=v.get("decoded") or "", kb=lijst(v.get("kernbegrippen")),
                            straten=lijst(v.get("streets")), indiener=(v.get("indiener") or "").split("(")[0].strip() or None)
    return uit


def kandidaten(st, extra):
    """Per schriftelijke vraag: de best gelijkende stukken, eerdere vragen van hetzelfde raadslid met een
    gedeeld zeldzaam woord, en de extra paren. Geeft een gesorteerde lijst paren (vraag, ander)."""
    stop, alg = stopwoorden(), algemene_woorden()
    woorden = {}
    for i, s in st.items():
        t = " ".join([s["titel"], *s["kb"], *s["straten"]])
        woorden[i] = {w for w in WOORD.findall(zonder_accenten(t.lower())) if w not in stop and w not in alg}
    df = {}
    for ws in woorden.values():
        for w in ws:
            df[w] = df.get(w, 0) + 1
    n = len(st)
    gewicht = {w: math.log(n / d) for w, d in df.items() if d <= DF_MAX}
    straat = {i: {x.lower() for x in s["straten"]} for i, s in st.items()}
    paren = set()
    vragen = sorted(i for i in st if i.startswith("sv-"))
    for v in vragen:
        score = []
        for x in st:
            if x == v:
                continue
            g = sum(gewicht.get(w, 0) for w in woorden[v] & woorden[x])
            if len(straat[v]) == 1 and straat[v] == straat[x]:
                g += 2
            if g > 0:
                score.append((g, x))
        score.sort(key=lambda p: (-p[0], p[1]))
        for g, x in score[:TOP]:
            if g >= DREMPEL:
                paren.add(tuple(sorted((v, x))))
        if st[v]["indiener"]:
            for x in vragen:
                if x != v and st[x]["indiener"] == st[v]["indiener"] and woorden[v] & woorden[x] & set(gewicht):
                    paren.add(tuple(sorted((v, x))))
    for a, b in extra:
        if a in st and b in st and a != b and (a.startswith("sv-") or b.startswith("sv-")):
            paren.add(tuple(sorted((a, b))))
    return sorted(paren)


SYSTEM = """Je beoordeelt of twee stukken uit de Mechelse besluitvorming over dezelfde concrete zaak gaan, zodat een lezer ze in één dossier wil zien. Minstens één van beide is een schriftelijke vraag van een raadslid.
- "ja": hetzelfde project, dezelfde werf of plek met hetzelfde onderwerp, dezelfde procedure of hetzelfde dossier, een vervolgvraag, of een besluit dat de vraag rechtstreeks beantwoordt of uitvoert.
- "nee": een ander onderwerp, of enkel hetzelfde brede thema (bv. allebei over verkeer, subsidies of afval), of enkel dezelfde straat of buurt met een ander onderwerp.
- "twijfel": de gegevens volstaan niet om te beslissen.
Antwoord met UITSLUITEND een JSON-object, zonder uitleg: {"oordeel": "ja"} of {"oordeel": "nee"} of {"oordeel": "twijfel"}."""


def beschrijf(s):
    tekst = re.sub(r"\s+", " ", s["tekst"])[:TEKST_MAX]
    return f"Titel: {s['titel']}\nDatum: {s['datum']}\nOrgaan: {s['orgaan']}\nSamenvatting: {tekst}"


def bericht(st, eerst, tweede):
    return f"Stuk A\n{beschrijf(st[eerst])}\n\nStuk B\n{beschrijf(st[tweede])}"


def sleutel(user):
    return hashlib.sha1(f"{MODEL}|{PROMPT}|{user}".encode("utf-8")).hexdigest()[:24]


def lees_oordeel(tekst):
    m = re.search(r"\{.*?\}", tekst or "", re.S)
    try:
        o = json.loads(m.group(0)).get("oordeel") if m else None
    except ValueError:
        o = None
    return o if o in ("ja", "nee", "twijfel") else None


def werk(st, paren, cache):
    """De berichten die nog een oordeel nodig hebben: {sleutel: user}."""
    nodig = {}
    for a, b in paren:
        for eerst, tweede in ((a, b), (b, a)):
            u = bericht(st, eerst, tweede)
            k = sleutel(u)
            if k not in cache["oordelen"]:
                nodig[k] = u
    return nodig


def raming(nodig):
    in_tok = int(sum(len(SYSTEM) + len(u) for u in nodig.values()) / 4 * 1.3)
    uit_tok = len(nodig) * 15
    return in_tok, uit_tok, (in_tok / 1e6) * 3 + (uit_tok / 1e6) * 15


def los(nodig, cache):
    import anthropic
    client = anthropic.Anthropic()
    for n, (k, u) in enumerate(nodig.items(), 1):
        for poging in range(5):
            try:
                r = client.messages.create(model=MODEL, max_tokens=60, thinking={"type": "disabled"}, system=SYSTEM,
                                           messages=[{"role": "user", "content": u}])
                o = lees_oordeel("".join(b.text for b in r.content if getattr(b, "type", "") == "text"))
                cache["oordelen"][k] = o or "twijfel"   # een onleesbaar antwoord telt als twijfel, zodat niets blijft hangen
                break
            except anthropic.APIStatusError as e:
                if e.status_code not in (429, 500, 502, 503, 504, 529) or poging == 4:
                    bewaar_cache(cache)
                    raise
                time.sleep(2 ** poging)
            except anthropic.APIConnectionError:
                if poging == 4:
                    bewaar_cache(cache)
                    raise
                time.sleep(2 ** poging)
        if n % 10 == 0:
            print(f"  {n}/{len(nodig)} oordelen", flush=True)
            bewaar_cache(cache)


def batch(nodig, cache):
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    client = anthropic.Anthropic()
    reqs = [Request(custom_id=k, params=MessageCreateParamsNonStreaming(
                model=MODEL, max_tokens=60, thinking={"type": "disabled"}, system=SYSTEM,
                messages=[{"role": "user", "content": u}]))
            for k, u in nodig.items()]
    b = client.messages.batches.create(requests=reqs)
    cache["lopende_batch"] = b.id
    bewaar_cache(cache)
    print(f"Batch ingediend: {b.id}, pollen tot klaar...", flush=True)
    haal_batch(client, b.id, cache)


def haal_batch(client, batch_id, cache):
    """Pol een batch tot ze klaar is en giet de oordelen in de cache; daarna is ze niet meer lopend."""
    while True:
        s = client.messages.batches.retrieve(batch_id)
        if s.processing_status == "ended":
            break
        rc = s.request_counts
        print(f"  {s.processing_status}: {rc.processing} bezig, {rc.succeeded} klaar, {rc.errored} fout", flush=True)
        time.sleep(30)
    ok = fout = 0
    for res in client.messages.batches.results(batch_id):
        if res.result.type == "succeeded":
            o = lees_oordeel("".join(x.text for x in res.result.message.content if getattr(x, "type", "") == "text"))
            cache["oordelen"][res.custom_id] = o or "twijfel"   # onleesbaar telt als twijfel
            ok += 1
        else:
            fout += 1   # mislukt: het paar blijft open en gaat mee in een volgende run
    cache.pop("lopende_batch", None)
    bewaar_cache(cache)
    print(f"Batch klaar: {ok} oordelen, {fout} mislukt.")


def lees_cache():
    if CACHE.exists():
        try:
            c = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            sys.exit(f"STOP: {CACHE.name} is onleesbaar ({e}); herstel het (eventueel uit .bak) voor een nieuwe run.")
    else:
        c = {}
    c.setdefault("versie", 1)
    c.setdefault("extra", [])
    c.setdefault("oordelen", {})
    vingerafdruk = hashlib.sha1(SYSTEM.encode("utf-8")).hexdigest()[:12]
    if c.get("prompt") == PROMPT and c.get("prompt_vingerafdruk") not in (None, vingerafdruk):
        sys.exit("STOP: de prompt veranderde, maar PROMPT niet; verhoog PROMPT zodat de paren opnieuw beoordeeld worden.")
    if (c.get("prompt"), c.get("prompt_vingerafdruk")) != (PROMPT, vingerafdruk):
        c["prompt"], c["prompt_vingerafdruk"] = PROMPT, vingerafdruk
        if c["oordelen"]:
            bewaar_cache(c)   # meteen vastleggen, anders blijft de wacht uit tot een run iets nieuws beoordeelt
    return c


def bewaar_cache(c):
    CACHE.parent.mkdir(exist_ok=True)
    if CACHE.exists():
        CACHE.with_suffix(".json.bak").write_bytes(CACHE.read_bytes())
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(CACHE)


def schrijf_uitkomst(st, paren, cache):
    """vraag_oordelen.json: de ja-paren (beide oordelen ja), de andere volledig beoordeelde paren (nee) en de
    vragen met minstens één volledig beoordeeld paar. Enkel stuk-id's."""
    ja, nee, beoordeeld = [], [], set()
    for a, b in paren:
        o1 = cache["oordelen"].get(sleutel(bericht(st, a, b)))
        o2 = cache["oordelen"].get(sleutel(bericht(st, b, a)))
        if not o1 or not o2:
            continue
        beoordeeld.update(x for x in (a, b) if x.startswith("sv-"))
        if o1 == o2 == "ja":
            ja.append([a, b])
        else:
            nee.append([a, b])
    uit = {"versie": 1, "ja": sorted(ja), "nee": sorted(nee), "beoordeeld": sorted(beoordeeld)}
    try:
        oud = json.loads(UIT.read_text(encoding="utf-8")) if UIT.exists() else None
    except (OSError, ValueError):
        oud = None   # een onleesbaar vorig bestand wordt gewoon vervangen
    if uit != oud:
        tmp = UIT.with_suffix(".tmp")
        tmp.write_text(json.dumps(uit, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(UIT)
    return uit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--droog", action="store_true", help="enkel tellen, geen API")
    ap.add_argument("--batch", action="store_true", help="via de Batches-API (zonder --go enkel de raming)")
    ap.add_argument("--go", action="store_true", help="met --batch: echt indienen")
    ap.add_argument("--extra", help="json-lijst van paren [a, b] om één keer als kandidaat toe te voegen")
    args = ap.parse_args()

    data = json.loads((BASE / "data.json").read_text(encoding="utf-8-sig"))
    st = stukken(data)
    cache = lees_cache()
    if args.extra:
        nieuw = [list(p) for p in json.loads(Path(args.extra).read_text(encoding="utf-8")) if len(p) == 2]
        bekend = {tuple(p) for p in cache["extra"]}
        cache["extra"] += [p for p in nieuw if tuple(p) not in bekend]
        bewaar_cache(cache)
    if cache.get("lopende_batch") and not args.droog and os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        client = anthropic.Anthropic()
        if args.batch:
            print(f"Een vorige batch ({cache['lopende_batch']}) werd niet afgehaald: eerst die resultaten ophalen.")
            haal_batch(client, cache["lopende_batch"], cache)
        else:
            # Een gewone run (run_all, de routine) wacht nooit op een batch: die kan tot 24 uur duren.
            try:
                klaar = client.messages.batches.retrieve(cache["lopende_batch"]).processing_status == "ended"
            except Exception as e:
                print(f"  (batch {cache['lopende_batch']} niet op te vragen: {e}; {UIT.name} blijft zoals het was.)")
                return
            if not klaar:
                print(f"  (batch {cache['lopende_batch']} loopt nog; {UIT.name} blijft zoals het was.)")
                return
            haal_batch(client, cache["lopende_batch"], cache)
    paren = kandidaten(st, cache["extra"])
    nodig = werk(st, paren, cache)
    in_tok, uit_tok, kost = raming(nodig)
    print(f"Vraagoordelen: {len(paren)} kandidaat-paren over {len({p for q in paren for p in q if p.startswith('sv-')})} vragen; "
          f"{len(nodig)} oordelen nodig (~{in_tok:,} tokens in, ~${kost:.2f} los, ~${kost / 2:.2f} via de Batches-API).")
    if nodig and not args.droog:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("  (overgeslagen: geen ANTHROPIC_API_KEY; de vorige oordelen blijven gelden.)")
        elif args.batch:
            if args.go:
                batch(nodig, cache)
                bewaar_cache(cache)
            else:
                print("  (raming: niets ingediend. Draai met --batch --go om in te dienen.)")
        elif len(nodig) > MAX_LOS:
            print(f"  (overgeslagen: meer dan {MAX_LOS} oordelen; dat gaat via --batch --go.)")
        else:
            los(nodig, cache)
            bewaar_cache(cache)
    if args.droog:
        return
    open_ = werk(st, paren, cache)
    if open_:
        print(f"  {len(open_)} oordelen ontbreken nog: {UIT.name} blijft zoals het was.")
        return
    uit = schrijf_uitkomst(st, paren, cache)
    print(f"  {len(uit['beoordeeld'])} vragen beoordeeld, {len(uit['ja'])} paren zelfde zaak, {len(uit['nee'])} niet -> {UIT.name}")


if __name__ == "__main__":
    main()
