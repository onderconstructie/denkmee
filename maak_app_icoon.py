"""
maak_app_icoon.py — hulpscript (eenmalig, buiten run_all): tekent het toren-in-cirkel-logo
als PNG-app-iconen voor het webmanifest, exact dezelfde geometrie als de favicon-SVG.

Uitvoer: beelden/toren-180.png (apple-touch), toren-192.png en toren-512.png.
Draai:   python maak_app_icoon.py
"""
from PIL import Image, ImageDraw
from pathlib import Path

BASE = Path(__file__).parent
UIT = BASE / "beelden"

PINK, CREAM = (255, 0, 102, 255), (245, 241, 232, 255)
BASIS = 480          # het SVG-canvas
SS = 4               # supersampling tegen kartelranden


def t(p):
    """De groep-transform uit de SVG: translate(-36,-36) scale(1.15) → p*1.15 - 36."""
    return p * 1.15 - 36


def teken(maat):
    s = maat * SS / BASIS                      # schaal SVG-eenheid → superpixel
    img = Image.new("RGBA", (maat * SS, maat * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # De pink bol (geen transform in de SVG)
    d.ellipse([(240 - 212) * s, (240 - 212) * s, (240 + 212) * s, (240 + 212) * s], fill=PINK)

    # De toren in cream (met de groep-transform)
    for x, y, w, h in [(188, 150, 104, 222), (194, 130, 16, 20), (218, 130, 16, 20),
                       (242, 130, 16, 20), (266, 130, 16, 20)]:
        d.rectangle([t(x) * s, t(y) * s, (t(x) + w * 1.15) * s, (t(y) + h * 1.15) * s], fill=CREAM)

    # De pink details: galmgaten + torenspits
    for x, y, w, h in [(218, 195, 8, 85), (236, 182, 8, 110), (254, 195, 8, 85)]:
        d.rectangle([t(x) * s, t(y) * s, (t(x) + w * 1.15) * s, (t(y) + h * 1.15) * s], fill=PINK)
    spits = [(224, 372), (224, 338), (240, 318), (256, 338), (256, 372)]
    d.polygon([(t(x) * s, t(y) * s) for x, y in spits], fill=PINK)

    return img.resize((maat, maat), Image.LANCZOS)


if __name__ == "__main__":
    UIT.mkdir(exist_ok=True)
    for maat in (512, 192, 180):
        pad = UIT / f"toren-{maat}.png"
        teken(maat).save(pad)
        print(f"geschreven: {pad.relative_to(BASE)} ({maat}x{maat})")
