#!/usr/bin/env python3
"""
straten_mechelen.py — vult de VOLLEDIGE stratenlijst van Mechelen (NIS 12025) en
koppelt elke straat aan haar ECHTE deelgemeente. Schrijft data/straten_mechelen.json,
dat build.py vervolgens als D.straten in de site giet (vervangt de demo-seed).

Hoe de deelgemeente bepaald wordt
---------------------------------
Het Vlaamse Adressenregister geeft per adres een 'volledigAdres' zoals
"Mezenstraat 11, 2800 Mechelen". De gemeentenaam is altijd "Mechelen" (fusiegemeente),
en óók de postnaam achter de postcode is altijd "Mechelen" — de postcode splitst de
deelgemeenten dus niet volledig:

  - 2801 = Heffen   (eenduidig)
  - 2812 = Muizen   (eenduidig)
  - 2811 = Hombeek OF Leest   (zelfde postcode, niet uit de postcode af te leiden)
  - 2800 = Mechelen OF Walem  (zelfde postcode, niet uit de postcode af te leiden)

Voor 2800 en 2811 bepalen we de deelgemeente daarom GEOGRAFISCH: we nemen de
coördinaat van het adres (het detail-endpoint geeft een punt in Lambert 72, EPSG:31370)
en kijken in welke deelgemeente-grens dat punt valt (point-in-polygon). De grenzen komen
uit OpenStreetMap (admin_level 9 = deelgemeente), eenmalig opgehaald en gecachet in
data/deelgemeenten_mechelen.geojson. Coördinaten staan al in Lambert 72 en we rekenen
ze met een eigen, gevalideerde formule om naar WGS84 (de CRS van de polygonen), zodat er
geen externe geo-bibliotheek nodig is (geen shapely, geen pyproj).

Validatie: de eenduidige postcodes (2801→Heffen, 2812→Muizen) komen via deze geo-route
exact zo uit, wat de transformatie + point-in-polygon bevestigt. 2811 splitst correct in
Hombeek en Leest.

Output  (data/straten_mechelen.json):
{
  "deelgemeenten": ["Mechelen","Heffen","Hombeek","Leest","Muizen","Walem"],
  "buurten":  [],
  "straten":  [{"naam": "...", "buurt": "", "deelgemeente": "..."}, ...]
}

Draai:  python straten_mechelen.py
        python straten_mechelen.py --inspect      # toon eerste records en stop
"""
from __future__ import annotations
import json
import math
import re
import sys
import time
import argparse
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path

NIS_MECHELEN = "12025"
BASE = Path(__file__).resolve().parent
OUT_PATH = BASE / "data" / "straten_mechelen.json"
GEOJSON_PATH = BASE / "data" / "deelgemeenten_mechelen.geojson"
# Hervat-caches (gitignored): zo overleeft een lange API-run een onderbreking en
# hervat een herstart waar hij stopte, in plaats van alles opnieuw op te halen.
SWEEP_CACHE = BASE / "data" / "_sweep_cache.json"
DG_CACHE = BASE / "data" / "_dg_cache.json"
USER_AGENT = "DenkMeeMetMechelen/1.0 (burgerexperiment; contact via asgaupaust.be)"
API = "https://api.basisregisters.vlaanderen.be/v2"
PAUSE_SECONDS = 0.3          # beleefd; de API antwoordt in ~0,4s per pagina van 500
PAUSE_DETAIL = 0.12          # korter; detail-calls zijn licht
PAGE = 500                   # maximale paginagrootte die de API toestaat

# Eenduidige postcodes: één deelgemeente, geen geo nodig.
POSTCODE_EENDUIDIG = {
    "2801": "Heffen",
    "2812": "Muizen",
}
# Dubbelzinnige postcodes: één postcode dekt twee deelgemeenten → geografisch beslissen.
POSTCODE_DUBBELZINNIG = {
    "2800": ("Mechelen", "Walem"),
    "2811": ("Hombeek", "Leest"),
}
DEELGEMEENTEN = ["Mechelen", "Heffen", "Hombeek", "Leest", "Muizen", "Walem"]

# OSM-relaties (admin_level 9) van de Mechelse deelgemeenten, voor de grens-cache.
OSM_DEELGEMEENTEN = {
    "Mechelen": 3369538, "Heffen": 3369535, "Hombeek": 3369536,
    "Leest": 3369537, "Muizen": 3359778, "Walem": 3369539,
}

# "Straat 11, 2800 Mechelen"  ->  (links, postcode, postnaam)
_ADRES = re.compile(r"^(.*?),\s*(\d{4})\s+(.+?)\s*$")
_GML_POS = re.compile(r"<gml:pos>([\d.]+)\s+([\d.]+)</gml:pos>")


def _get(url: str, pogingen: int = 4) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for i in range(pogingen):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, TimeoutError) as e:
            if i == pogingen - 1:
                sys.exit(f"\n[FOUT] Kan de bron niet bereiken: {url}\n  {e}")
            time.sleep(1.5 * (i + 1))                  # korte back-off en opnieuw
    return {}


def _get_soft(url: str, pogingen: int = 3) -> dict | None:
    """Tolerante GET: geeft None terug bij blijvende fout (geen sys.exit), zodat één
    haperende detail-call de hele run niet doodt. De straat valt dan terug op de
    postcode en wordt bij een herstart opnieuw geprobeerd."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for i in range(pogingen):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:                          # rate limit: even rustiger aan
                time.sleep(2.0 * (i + 1)); continue
            if i == pogingen - 1:
                return None
            time.sleep(1.0 * (i + 1))
        except (urllib.error.URLError, TimeoutError):
            if i == pogingen - 1:
                return None
            time.sleep(1.0 * (i + 1))
    return None


def _laad_cache(pad: Path) -> dict:
    try:
        return json.loads(pad.read_text(encoding="utf-8")) if pad.exists() else {}
    except Exception:
        return {}


def _schrijf_cache(pad: Path, data: dict) -> None:
    try:
        pad.parent.mkdir(parents=True, exist_ok=True)
        pad.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _get_raw(url: str, pogingen: int = 4) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for i in range(pogingen):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if i == pogingen - 1:
                sys.exit(f"\n[FOUT] Kan de bron niet bereiken: {url}\n  {e}")
            time.sleep(1.5 * (i + 1))
    return b""


# ---------------------------------------------------------------------------
# Lambert 72 (EPSG:31370) -> WGS84 (lon, lat). Zuivere Python, geen pyproj.
# Inverse Lambert Conformal Conic (2SP, Hayford 1924 / BD72) + Helmert BD72->WGS84.
# Gevalideerd: de eenduidige postcodes komen via deze route exact in hun deelgemeente.
# ---------------------------------------------------------------------------
def lambert72_to_wgs84(X: float, Y: float) -> tuple[float, float]:
    a = 6378388.0
    f = 1 / 297.0
    e = math.sqrt(2 * f - f * f)
    phi1 = math.radians(49.8333339)
    phi2 = math.radians(51.16666723333333)
    phi0 = math.radians(90.0)
    lon0 = math.radians(4.367486666666666)
    FE, FN = 150000.013, 5400088.438

    def mm(p): return math.cos(p) / math.sqrt(1 - e * e * math.sin(p) ** 2)
    def tt(p): return math.tan(math.pi / 4 - p / 2) / (((1 - e * math.sin(p)) / (1 + e * math.sin(p))) ** (e / 2))

    m1, m2 = mm(phi1), mm(phi2)
    t1, t2, t0 = tt(phi1), tt(phi2), tt(phi0)
    n = (math.log(m1) - math.log(m2)) / (math.log(t1) - math.log(t2))
    F = m1 / (n * (t1 ** n))
    rho0 = a * F * (t0 ** n)
    dx, dy = X - FE, Y - FN
    rho = math.copysign(math.hypot(dx, rho0 - dy), n)
    t_ = (rho / (a * F)) ** (1 / n)
    theta = math.atan2(dx, rho0 - dy)
    lon = theta / n + lon0
    phi = math.pi / 2 - 2 * math.atan(t_)
    for _ in range(20):
        phi = math.pi / 2 - 2 * math.atan(t_ * (((1 - e * math.sin(phi)) / (1 + e * math.sin(phi))) ** (e / 2)))

    # BD72 geodetisch -> geocentrisch (Hayford)
    N = a / math.sqrt(1 - e * e * math.sin(phi) ** 2)
    Xg = N * math.cos(phi) * math.cos(lon)
    Yg = N * math.cos(phi) * math.sin(lon)
    Zg = (N * (1 - e * e)) * math.sin(phi)
    # Helmert BD72 -> WGS84 (coordinate-frame conventie)
    tx, ty, tz = -106.8686, 52.2978, -103.7239
    rx = math.radians(-0.3366 / 3600)
    ry = math.radians(0.4570 / 3600)
    rz = math.radians(-1.8422 / 3600)
    ds = 1 - 1.2747e-6
    Xw = tx + ds * (Xg + rz * Yg - ry * Zg)
    Yw = ty + ds * (-rz * Xg + Yg + rx * Zg)
    Zw = tz + ds * (ry * Xg - rx * Yg + Zg)
    # WGS84 geocentrisch -> geodetisch (GRS80)
    aw = 6378137.0
    fw = 1 / 298.257223563
    ew2 = 2 * fw - fw * fw
    p = math.hypot(Xw, Yw)
    lonw = math.atan2(Yw, Xw)
    phiw = math.atan2(Zw, p * (1 - ew2))
    for _ in range(20):
        Nw = aw / math.sqrt(1 - ew2 * math.sin(phiw) ** 2)
        phiw = math.atan2(Zw + ew2 * Nw * math.sin(phiw), p)
    return math.degrees(lonw), math.degrees(phiw)


# ---------------------------------------------------------------------------
# Point-in-polygon (ray casting) op de GeoJSON-polygonen van de deelgemeenten.
# ---------------------------------------------------------------------------
def _ring_bevat(lon: float, lat: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _geom_bevat(lon: float, lat: float, geom: dict) -> bool:
    t = geom.get("type")
    if t == "Polygon":
        polys = [geom["coordinates"]]
    elif t == "MultiPolygon":
        polys = geom["coordinates"]
    elif t == "GeometryCollection":
        return any(_geom_bevat(lon, lat, g) for g in geom["geometries"])
    else:
        return False
    for poly in polys:
        if _ring_bevat(lon, lat, poly[0]) and not any(_ring_bevat(lon, lat, h) for h in poly[1:]):
            return True
    return False


def laad_deelgemeenten() -> dict:
    """Lees de gecachete OSM-grenzen; haal ze eenmalig op als de cache ontbreekt."""
    if GEOJSON_PATH.exists():
        return json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))
    print("Deelgemeente-grenzen ophalen uit OpenStreetMap (eenmalig)...", flush=True)
    polys = {}
    for naam, rid in OSM_DEELGEMEENTEN.items():
        url = f"https://polygons.openstreetmap.fr/get_geojson.py?id={rid}&params=0"
        polys[naam] = json.loads(_get_raw(url))
        time.sleep(0.4)
    GEOJSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEOJSON_PATH.write_text(json.dumps(polys, ensure_ascii=False), encoding="utf-8")
    return polys


def deelgemeente_van_punt(X: float, Y: float, grenzen: dict, kandidaten=None) -> str | None:
    """Welke deelgemeente bevat het Lambert 72-punt (X, Y)? None als geen enkele."""
    lon, lat = lambert72_to_wgs84(X, Y)
    namen = kandidaten or grenzen.keys()
    for naam in namen:
        if naam in grenzen and _geom_bevat(lon, lat, grenzen[naam]):
            return naam
    # buiten alle kandidaten (grens/water): probeer álle deelgemeenten
    if kandidaten:
        for naam, geom in grenzen.items():
            if _geom_bevat(lon, lat, geom):
                return naam
    return None


def split_straat(volledig: str) -> tuple[str, str, str] | None:
    """'Varkensstraat 9 bus 205, 2800 Mechelen' -> ('Varkensstraat', '2800', 'Mechelen').
    Strip het huisnummer (12, 12A, 106-108, 12/3) én een eventueel 'bus N'-suffix."""
    m = _ADRES.match(volledig)
    if not m:
        return None
    links, postcode, postnaam = m.group(1), m.group(2), m.group(3)
    straat = re.sub(r"\s+\d[\w\-/]*(\s+bus\s+\S+)?\s*$", "", links, flags=re.IGNORECASE)
    return straat.strip(" ,"), postcode, postnaam.strip()


def sweep_adressen(max_pages: int | None, inspect: bool) -> dict[str, dict]:
    """Loop gepagineerd door alle Mechelse adressen en bouw per straat de postcode(s)
    op, plus per postcode één representatief adres-objectId (voor de geo-lookup)."""
    url = f"{API}/adressen?gemeentenaam=Mechelen&limit={PAGE}"
    straten: dict[str, dict] = {}
    pagina = 0
    while url:
        data = _get(url)
        rijen = data.get("adressen", [])
        if inspect and pagina == 0:
            print("[INSPECT] eerste volledigAdres-waarden:")
            for ad in rijen[:5]:
                print("  -", ad.get("volledigAdres", {}).get("geografischeNaam", {}).get("spelling"))
            return {}
        for ad in rijen:
            spelling = ad.get("volledigAdres", {}).get("geografischeNaam", {}).get("spelling", "")
            parsed = split_straat(spelling)
            if not parsed:
                continue
            naam, postcode, _postnaam = parsed
            if not naam:
                continue
            oid = ad.get("identificator", {}).get("objectId")
            sleutel = " ".join(naam.split()).casefold()
            rec = straten.setdefault(sleutel, {"naam": naam, "postcodes": Counter(), "oid_per_pc": {}})
            rec["postcodes"][postcode] += 1
            rec["oid_per_pc"].setdefault(postcode, oid)        # eerste adres per postcode
        pagina += 1
        print(f"  pagina {pagina}: {len(rijen)} adressen, {len(straten)} straten tot nu", flush=True)
        url = data.get("volgende") if rijen else None
        if max_pages and pagina >= max_pages:
            break
        if url:
            time.sleep(PAUSE_SECONDS)
    return straten


def positie_van_adres(oid: str) -> tuple[float, float] | None:
    """Haal de Lambert 72-coördinaat (X, Y) van een adres-objectId. Tolerant: None bij fout."""
    det = _get_soft(f"{API}/adressen/{oid}")
    if not det:
        return None
    gml = det.get("adresPositie", {}).get("geometrie", {}).get("gml", "")
    m = _GML_POS.search(gml)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def fetch_straatnamen() -> set[str]:
    """De canonieke stratenlijst (om de dekking van de adres-sweep te controleren)."""
    url = f"{API}/straatnamen?gemeentenaam=Mechelen&limit=100"
    namen: set[str] = set()
    while url:
        data = _get(url)
        rijen = data.get("straatnamen", [])
        for s in rijen:
            sp = s.get("straatnaam", {}).get("geografischeNaam", {}).get("spelling")
            if sp:
                namen.add(" ".join(sp.split()).casefold())
        url = data.get("volgende") if rijen else None
        if url:
            time.sleep(PAUSE_SECONDS)
    return namen


def main() -> None:
    ap = argparse.ArgumentParser(description="Bouw de Mechelse straten→deelgemeente-lijst voor de pipeline.")
    ap.add_argument("--inspect", action="store_true", help="Toon enkele bron-records en stop.")
    ap.add_argument("--max-pages", type=int, default=0, help="Beperk het aantal adres-pagina's (test).")
    ap.add_argument("--out", default=str(OUT_PATH))
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")     # Windows-console is anders cp1252
    except Exception:
        pass

    # Sweep — uit cache als die er is (scheelt ~70s bij een herstart), anders ophalen.
    ruw = _laad_cache(SWEEP_CACHE)
    if ruw:
        for rec in ruw.values():                       # Counter terug uit JSON-dict
            rec["postcodes"] = Counter(rec["postcodes"])
        print(f"Sweep uit cache: {len(ruw)} straten.", flush=True)
    else:
        ruw = sweep_adressen(args.max_pages or None, args.inspect)
        if args.inspect:
            return
        if ruw:
            _schrijf_cache(SWEEP_CACHE, ruw)
    if not ruw:
        sys.exit("[FOUT] Geen adressen opgehaald — niets weggeschreven.")

    grenzen = laad_deelgemeenten()

    # Hervat-cache: al opgeloste straten (sleutel -> deelgemeente) niet opnieuw ophalen.
    dg_cache = _laad_cache(DG_CACHE)
    todo = {k: rec for k, rec in ruw.items()
            if rec["postcodes"].most_common(1)[0][0] in POSTCODE_DUBBELZINNIG and k not in dg_cache}
    print(f"\nGeografisch te bepalen (2800/2811): {len(todo)} straten "
          f"({len(dg_cache)} al in cache) ...", flush=True)

    straten = []
    onbekende_postcodes: Counter = Counter()
    geo_gelukt = geo_terugval = 0
    verwerkt = 0
    for k, rec in ruw.items():
        postcode, _aantal = rec["postcodes"].most_common(1)[0]    # meerderheids-postcode
        if postcode in POSTCODE_EENDUIDIG:
            dg = POSTCODE_EENDUIDIG[postcode]
        elif postcode in POSTCODE_DUBBELZINNIG:
            kand = POSTCODE_DUBBELZINNIG[postcode]
            if k in dg_cache:
                dg = dg_cache[k]
            else:
                verwerkt += 1
                dg = None
                oid = rec["oid_per_pc"].get(postcode) or next(iter(rec["oid_per_pc"].values()), None)
                if oid:
                    xy = positie_van_adres(oid)
                    if xy:
                        dg = deelgemeente_van_punt(xy[0], xy[1], grenzen, kandidaten=kand)
                    time.sleep(PAUSE_DETAIL)
                if dg:
                    dg_cache[k] = dg
                    geo_gelukt += 1
                else:
                    dg = kand[0]        # val terug op de hoofd-deelgemeente (niet cachen → herprobeer)
                    geo_terugval += 1
                if verwerkt % 25 == 0:
                    _schrijf_cache(DG_CACHE, dg_cache)
                    print(f"  ... {verwerkt}/{len(todo)} geo-lookups "
                          f"({geo_gelukt} ok, {geo_terugval} terugval)", flush=True)
        else:
            onbekende_postcodes[postcode] += 1
            dg = "Mechelen"
        straten.append({"naam": rec["naam"], "buurt": "", "deelgemeente": dg})
    _schrijf_cache(DG_CACHE, dg_cache)
    straten.sort(key=lambda s: s["naam"].casefold())

    # Dekking t.o.v. de canonieke stratenlijst (straten zonder enig adres komen hier niet voor).
    try:
        canoniek = fetch_straatnamen()
        gedekt = set(ruw.keys())
        ontbrekend = canoniek - gedekt
    except SystemExit:
        canoniek, ontbrekend = set(), set()

    data = {
        "deelgemeenten": DEELGEMEENTEN,
        "buurten": [],                       # bewust leeg: buurt vereist de fijnere geo-route
        "straten": straten,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    per_dg = Counter(s["deelgemeente"] for s in straten)
    print(f"\n[OK] {len(straten)} straten -> {out}")
    for dg in DEELGEMEENTEN:
        print(f"   {dg:<10} {per_dg.get(dg, 0)}")
    print(f"   geo-lookups deze run: {geo_gelukt} ok, {geo_terugval} terugval "
          f"(terugval = punt niet gevonden, viel terug op hoofd-deelgemeente; herstart herprobeert)")
    if onbekende_postcodes:
        print("   LET OP - postcodes buiten de bekende mapping:", dict(onbekende_postcodes))
    if canoniek:
        print(f"   dekking: {len(straten)} van {len(canoniek)} canonieke straatnamen "
              f"({len(ontbrekend)} zonder adres, niet opgenomen)")


if __name__ == "__main__":
    main()
