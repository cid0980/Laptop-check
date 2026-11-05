"""Rasterise the open-source icon set (Feather = ISC, Tabler = MIT) into PNGs
that tkinter can load. Run once when the icon set changes:

    python tools/make_icons.py

The app itself stays 100% standard library: it only loads the committed PNGs.
"""
import os
import sys

try:
    import cairosvg
except ImportError:
    print("needs: pip install cairosvg")
    sys.exit(1)

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "icon_src")
OUT = os.path.join(HERE, "..", "assets", "icons")

COLOURS = {
    "white": "#e2e8f0", "muted": "#94a3b8", "good": "#10b981",
    "okay": "#f59e0b", "bad": "#ef4444", "accent": "#3b82f6",
}


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    n = 0
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith(".svg"):
            continue
        name = fn[:-4]
        raw = open(os.path.join(SRC, fn), encoding="utf-8").read()
        for ck, cv in COLOURS.items():
            svg = raw.replace("currentColor", cv)
            out = os.path.join(OUT, "%s_%s.png" % (name, ck))
            cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=out,
                             output_width=48, output_height=48,
                             background_color=None)
            n += 1
    print("wrote %d icon pngs to assets/icons" % n)


if __name__ == "__main__":
    main()
