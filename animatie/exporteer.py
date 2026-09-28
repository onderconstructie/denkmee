# -*- coding: utf-8 -*-
"""exporteer.py: zet de animatie om naar een MP4 met geluid, met de ondertitels ernaast als .srt.

De film is een webpagina (de-illusie-van-het-stadhuis.html) die elk beeld uitrekent uit de tijd
alleen. Dit script opent ze in een onzichtbare browser, zet de klok beeld per beeld verder, maakt
telkens een schermafdruk en geeft die door aan ffmpeg. Zo is de export beeldexact: geen haperingen
of gemiste beelden zoals bij een schermopname. Omdat elk beeld los staat van het vorige, kunnen
meerdere browsers tegelijk elk een stuk van de film maken; ffmpeg plakt de stukken daarna aan elkaar.
Het geluid rekent de pagina zelf uit, in één keer en even exact, en ffmpeg zet het onder het beeld.

Eenmalig nodig:
  python -m pip install playwright imageio-ffmpeg
  python -m playwright install chromium

Gebruik:
  python animatie/exporteer.py                        origineel script, 1920x1080, 30 beelden/s
  python animatie/exporteer.py --versie herwerkt      het herwerkte voorstel
  python animatie/exporteer.py --zonder-ondertitels   schoon beeld; de tekst staat enkel in de .srt
  python animatie/exporteer.py --hoogte 720           kleiner en sneller, om na te kijken
  python animatie/exporteer.py --van 40 --tot 62      enkel een stuk, in seconden
  python animatie/exporteer.py --zonder-geluid        stil beeld, bv. om zelf geluid onder te leggen
  python animatie/exporteer.py --zonder-muziek        wel de geluiden, geen achtergrondmuziek
  python animatie/exporteer.py --met-korrel           met de filmkorrel van de speler (zie hieronder)

De MP4 heeft de geluiden en de muziek van de film (papier, laden, de stempel, ...), maar geen stem: de
voice-over wordt apart ingesproken. De .srt bevat elke zin met zijn tijdcode, handig om de stem op af
te stemmen en om mee te uploaden naar sociale media.
Exports landen standaard naast dit script en staan in .gitignore: een film hoort niet in git.

De filmkorrel staat in de export standaard uit. Ruis laat zich niet samenpersen: met korrel wordt de
MP4 ongeveer drie keer zo groot (zo'n 80 MB in plaats van 25), en een platform dat de film opnieuw
codeert, maakt er blokjes van. Met --met-korrel komt ze er wel in, stilliggend.
"""
import argparse
import base64
import math
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HIER = Path(__file__).resolve().parent
FILM = HIER / "de-illusie-van-het-stadhuis.html"


def ffmpeg_pad():
    """ffmpeg uit imageio-ffmpeg (brengt zijn eigen binaire mee, ook op Windows), anders die op het PATH."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pad = shutil.which("ffmpeg")
        if not pad:
            sys.exit("Geen ffmpeg gevonden. Installeer het met: python -m pip install imageio-ffmpeg")
        return pad


def open_film(p, adres, breedte, hoogte, chromium):
    browser = p.chromium.launch(executable_path=chromium) if chromium else p.chromium.launch()
    pagina = browser.new_page(viewport={"width": breedte, "height": hoogte}, device_scale_factor=1)
    fouten = []
    pagina.on("pageerror", lambda e: fouten.append(str(e)))
    pagina.goto(adres)
    pagina.wait_for_function("window.ILLUSIE && window.ILLUSIE.klaar", timeout=30000)
    return browser, pagina, fouten


# De voortgangsteller wordt bij het opstarten van elke werker meegegeven (een gedeelde waarde kan
# niet in de taak zelf mee naar een ander proces).
_TELLER = None


def _zet_teller(teller):
    global _TELLER
    _TELLER = teller


def maak_stuk(t):
    """Eén werker: beelden [eerste, laatste) naar een eigen MP4-stuk. Draait in een apart proces."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, pagina, fouten = open_film(p, t["adres"], t["breedte"], t["hoogte"], t["chromium"])
        ff = subprocess.Popen(
            [t["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
             "-f", "image2pipe", "-framerate", str(t["fps"]), "-c:v", "mjpeg", "-i", "-",
             "-c:v", "libx264", "-preset", "medium", "-crf", str(t["kwaliteit"]), "-pix_fmt", "yuv420p",
             "-r", str(t["fps"]), t["stuk"]],
            stdin=subprocess.PIPE)
        try:
            for i in range(t["eerste"], t["laatste"]):
                pagina.evaluate("s => ILLUSIE.toon(s)", t["van"] + i / t["fps"])
                ff.stdin.write(pagina.screenshot(type="jpeg", quality=95))
                with _TELLER.get_lock():
                    _TELLER.value += 1
        finally:
            ff.stdin.close()
            ff.wait()
            browser.close()
    return ff.returncode, fouten[:1]


def main():
    ap = argparse.ArgumentParser(description="Exporteer De Illusie van het Stadhuis naar MP4 + SRT.")
    ap.add_argument("--versie", choices=("origineel", "herwerkt"), default="origineel")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--hoogte", type=int, default=1080, help="beeldhoogte in pixels (breedte volgt uit 16:9)")
    ap.add_argument("--zonder-ondertitels", action="store_true", help="geen ondertitels in beeld (wel in de .srt)")
    ap.add_argument("--zonder-geluid", action="store_true", help="geen geluid in de MP4")
    ap.add_argument("--zonder-muziek", action="store_true", help="de geluiden wel, de achtergrondmuziek niet")
    ap.add_argument("--met-korrel", action="store_true", help="met filmkorrel (de MP4 wordt zo'n drie keer groter)")
    ap.add_argument("--van", type=float, default=0.0, help="begintijd in seconden")
    ap.add_argument("--tot", type=float, default=None, help="eindtijd in seconden (standaard: het einde)")
    ap.add_argument("--kwaliteit", type=int, default=18, help="x264 CRF: lager is beter en groter (standaard 18)")
    ap.add_argument("--werkers", type=int, default=min(4, max(1, (os.cpu_count() or 2) - 1)),
                    help="aantal browsers tegelijk (standaard: het aantal kernen min een, hooguit 4)")
    ap.add_argument("--uit", type=Path, default=None, help="pad van de MP4 (standaard naast dit script)")
    ap.add_argument("--tijdelijk", type=Path, default=None,
                    help="map voor de tussenstukken (standaard de tijdelijke map van het systeem)")
    ap.add_argument("--chromium", default=os.environ.get("CHROMIUM_PAD"),
                    help="pad naar een eigen Chromium, als Playwright zijn browser niet zelf vindt")
    a = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright ontbreekt. Installeer het met: python -m pip install playwright "
                 "&& python -m playwright install chromium")

    hoogte = a.hoogte - a.hoogte % 2
    breedte = round(hoogte * 16 / 9 / 2) * 2
    naam = f"de-illusie-van-het-stadhuis-{a.versie}{'-zonder-ot' if a.zonder_ondertitels else ''}.mp4"
    uit = (a.uit or HIER / naam).resolve()
    uit.parent.mkdir(parents=True, exist_ok=True)
    adres = (FILM.as_uri() + f"?opname=1&versie={a.versie}" + ("&ondertitels=0" if a.zonder_ondertitels else "")
             + ("&korrel=1" if a.met_korrel else "") + ("&muziek=0" if a.zonder_muziek else ""))
    ffmpeg = ffmpeg_pad()

    # Eerst de lengte en de ondertitels ophalen; de .srt enkel bij een volledige export, want haar
    # tijdcodes gelden voor de hele film.
    with sync_playwright() as p:
        browser, pagina, fouten = open_film(p, adres, breedte, hoogte, a.chromium)
        if fouten:
            sys.exit("De pagina gaf een fout: " + fouten[0])
        duur = pagina.evaluate("ILLUSIE.duur")
        srt = pagina.evaluate("ILLUSIE.srt()")
        browser.close()
    van, tot = max(0.0, a.van), min(duur, a.tot if a.tot is not None else duur)
    if tot <= van:
        sys.exit(f"Niets te exporteren: --van {van} ligt niet voor --tot {tot} (de film duurt {duur:.1f} s).")
    if van == 0 and tot == duur:
        uit.with_suffix(".srt").write_text(srt, encoding="utf-8")
    beelden = math.ceil((tot - van) * a.fps)
    werkers = max(1, min(a.werkers, beelden // a.fps or 1))
    print(f"{a.versie}: {tot - van:.1f} s, {beelden} beelden van {breedte}x{hoogte}, "
          f"{werkers} werker(s) -> {uit.name}", flush=True)

    begin = time.time()
    with tempfile.TemporaryDirectory(prefix="illusie-", dir=a.tijdelijk) as tmp:
        ctx = mp.get_context("spawn")
        teller = ctx.Value("i", 0)
        grenzen = [round(beelden * k / werkers) for k in range(werkers + 1)]
        taken = [dict(adres=adres, breedte=breedte, hoogte=hoogte, fps=a.fps, van=van, kwaliteit=a.kwaliteit,
                      chromium=a.chromium, ffmpeg=ffmpeg, eerste=grenzen[k], laatste=grenzen[k + 1],
                      stuk=str(Path(tmp) / f"stuk{k:02d}.mp4")) for k in range(werkers)]
        with ctx.Pool(werkers, initializer=_zet_teller, initargs=(teller,)) as pool:
            bezig = pool.map_async(maak_stuk, taken)
            while not bezig.ready():
                bezig.wait(5)
                klaar = teller.value / beelden
                if 0 < klaar < 1:
                    rest = (time.time() - begin) / klaar * (1 - klaar)
                    print(f"  {100 * klaar:5.1f}%  (nog ongeveer {rest:4.0f} s)", flush=True)
            uitkomsten = bezig.get()
        for code, fout in uitkomsten:
            if code:
                sys.exit(f"ffmpeg stopte met code {code}.")
            if fout:
                print("Let op, de pagina gaf onderweg een fout: " + fout[0])
        lijst = Path(tmp) / "stukken.txt"
        lijst.write_text("".join(f"file '{Path(t['stuk']).as_posix()}'\n" for t in taken), encoding="utf-8")
        beeld = Path(tmp) / "beeld.mp4"
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(lijst), "-c", "copy", str(beeld)])
        if r.returncode:
            sys.exit(f"Aan elkaar plakken mislukt (ffmpeg code {r.returncode}).")
        # Het geluid: de pagina rekent het uit voor precies zo lang als het beeld duurt, en geeft een WAV.
        if a.zonder_geluid:
            geluid = []
        else:
            print("  geluid uitrekenen...", flush=True)
            with sync_playwright() as p:
                browser, pagina, fouten = open_film(p, adres, breedte, hoogte, a.chromium)
                stukken = pagina.evaluate("([v, t]) => ILLUSIE.geluidWav(v, t)", [van, van + beelden / a.fps])
                browser.close()
            wav = Path(tmp) / "geluid.wav"
            wav.write_bytes(b"".join(base64.b64decode(s) for s in stukken))
            geluid = ["-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k"]
        r = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(beeld), *geluid,
                            "-c:v", "copy", "-movflags", "+faststart", str(uit)])
        if r.returncode:
            sys.exit(f"Geluid en beeld samenvoegen mislukt (ffmpeg code {r.returncode}).")
    print(f"Klaar in {time.time() - begin:.0f} s: {uit} ({uit.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
