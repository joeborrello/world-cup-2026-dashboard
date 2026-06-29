#!/usr/bin/env python3
"""Generate the social link-preview image (Open Graph / Twitter card).

When the dashboard URL is pasted into Discord, iMessage/SMS, Slack, WhatsApp,
Twitter/X, etc., those clients fetch the page and look for an ``og:image`` to
render a rich preview card. They want a real raster image at an absolute URL —
SVG is unreliable across them — so this script renders a branded 1200×630 PNG
once and writes it to both surfaces that serve the site:

  * ``static/og/preview.png``  — served by the Flask app (templates/base.html)
  * ``docs/og-preview.png``    — served by the GitHub Pages landing site

It's a build-time tool, not a runtime dependency: the PNG is committed, so the
app and Pages serve it as a plain static file and nothing renders it per request.
Re-run it (with Pillow installed) only when the card's wording or art changes.

    python make_og_image.py
"""

import os

from PIL import Image, ImageDraw, ImageFont

try:
    import numpy as np
except ImportError:  # pragma: no cover - dev tool, numpy is expected
    np = None

BASE = os.path.dirname(os.path.abspath(__file__))
W, H = 1200, 630  # the size every major scraper expects for a large card

# Palette lifted straight from the site's hero (docs/index.html / style.css).
GREEN_D = (19, 82, 46)
GREEN = (31, 122, 69)
GREEN_L = (42, 140, 82)
GOLD = (224, 161, 6)
WHITE = (255, 255, 255)
CREAM = (234, 246, 238)

_FONT_DIRS = [
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
]
_FONT_FILES = {
    "bold": ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"),
    "regular": ("DejaVuSans.ttf", "LiberationSans-Regular.ttf"),
}


def _font(kind, size):
    for name in _FONT_FILES[kind]:
        for d in _FONT_DIRS:
            path = os.path.join(d, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _gradient():
    """Diagonal green gradient (top-left dark → bottom-right light), like the hero."""
    if np is not None:
        # t runs 0→1 along the main diagonal; vectorised so it's instant.
        yy, xx = np.mgrid[0:H, 0:W]
        t = ((xx / (W - 1)) + (yy / (H - 1))) / 2.0
        img = np.empty((H, W, 3), dtype=np.float64)
        for i in range(3):
            # two-stop ramp: GREEN_D → GREEN (first 55%) → GREEN_L
            lo = np.where(t < 0.55,
                          GREEN_D[i] + (GREEN[i] - GREEN_D[i]) * (t / 0.55),
                          GREEN[i] + (GREEN_L[i] - GREEN[i]) * ((t - 0.55) / 0.45))
            img[..., i] = lo
        return Image.fromarray(img.clip(0, 255).astype("uint8"), "RGB")
    # Fallback: simple vertical gradient without numpy.
    img = Image.new("RGB", (W, H), GREEN)
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        c = tuple(int(GREEN_D[i] + (GREEN_L[i] - GREEN_D[i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    return img


def _soccer_ball(draw, cx, cy, r):
    """A simple, recognisable soccer ball: white disc with black pentagon patches."""
    import math
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE,
                 outline=(13, 27, 42), width=max(2, r // 22))

    def pent(ox, oy, pr, rot=-90):
        pts = []
        for k in range(5):
            a = math.radians(rot + k * 72)
            pts.append((ox + pr * math.cos(a), oy + pr * math.sin(a)))
        return pts

    # central patch + five around it, with seams radiating to the rim
    draw.polygon(pent(cx, cy, r * 0.34), fill=(13, 27, 42))
    for k in range(5):
        a = math.radians(-90 + k * 72)
        ox, oy = cx + r * 0.62 * math.cos(a), cy + r * 0.62 * math.sin(a)
        draw.polygon(pent(ox, oy, r * 0.20, rot=90 + k * 72), fill=(13, 27, 42))
        # seam from the central patch's vertex outward
        va = math.radians(-90 + k * 72)
        draw.line([(cx + r * 0.34 * math.cos(va), cy + r * 0.34 * math.sin(va)),
                   (ox, oy)], fill=(13, 27, 42), width=max(2, r // 30))


def _flag(draw, x, y, w, h, kind):
    """Tiny host-nation flag chip (no emoji font needed)."""
    red, white = (197, 54, 47), WHITE
    if kind == "ca":  # Canada: red | white | red
        draw.rectangle([x, y, x + w, y + h], fill=white)
        draw.rectangle([x, y, x + w // 4, y + h], fill=red)
        draw.rectangle([x + 3 * w // 4, y, x + w, y + h], fill=red)
        draw.polygon([(x + w // 2, y + h * 0.28), (x + w * 0.58, y + h * 0.5),
                      (x + w * 0.42, y + h * 0.5)], fill=red)
    elif kind == "us":  # USA: stripes + canton
        stripe = h / 7
        for i in range(7):
            draw.rectangle([x, y + i * stripe, x + w, y + (i + 1) * stripe],
                           fill=red if i % 2 == 0 else white)
        draw.rectangle([x, y, x + w * 0.42, y + stripe * 4], fill=(42, 95, 176))
    elif kind == "mx":  # Mexico: green | white | red
        third = w / 3
        draw.rectangle([x, y, x + third, y + h], fill=(0, 104, 71))
        draw.rectangle([x + third, y, x + 2 * third, y + h], fill=white)
        draw.rectangle([x + 2 * third, y, x + w, y + h], fill=red)
    draw.rectangle([x, y, x + w, y + h], outline=(255, 255, 255, 120), width=1)


def build():
    img = _gradient().convert("RGBA")

    # soft radial highlight, top-right (mirrors the hero's ::after glow)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W - 620, -360, W + 260, 520], fill=(255, 255, 255, 26))
    img = Image.alpha_composite(img, glow)

    draw = ImageDraw.Draw(img)
    PAD = 80

    # decorative ball, tucked into the top-right corner (clear of the headline)
    _soccer_ball(draw, W - 150, 152, 106)

    # eyebrow / badge pill
    badge = "48 TEAMS · 16 CITIES · 104 MATCHES"
    bf = _font("bold", 26)
    bb = draw.textbbox((0, 0), badge, font=bf)
    bw, bh = bb[2] - bb[0], bb[3] - bb[1]
    bx, by = PAD, 84
    draw.rounded_rectangle([bx, by, bx + bw + 44, by + bh + 26], radius=(bh + 26) // 2,
                           fill=(255, 255, 255, 36), outline=(255, 255, 255, 110), width=2)
    draw.text((bx + 22, by + 13 - bb[1]), badge, font=bf, fill=CREAM)

    # headline
    draw.text((PAD, 180), "World Cup 2026", font=_font("bold", 90), fill=WHITE)
    draw.text((PAD, 298), "LIVE DASHBOARD", font=_font("bold", 52), fill=GOLD)

    # tagline
    tag = "Bracket · Predictions · Maps · Weather · Live scores"
    draw.text((PAD, 380), tag, font=_font("regular", 33), fill=CREAM)

    # host flags + dates, anchored to the bottom
    fx, fy, fw, fh = PAD, 506, 66, 44
    for i, kind in enumerate(("ca", "us", "mx")):
        _flag(draw, fx + i * (fw + 16), fy, fw, fh, kind)
    meta = "Canada · USA · Mexico    —    Jun 11 – Jul 19, 2026"
    mf = _font("regular", 29)
    mb = draw.textbbox((0, 0), meta, font=mf)
    draw.text((fx + 3 * (fw + 16) + 8, fy + (fh - (mb[3] - mb[1])) // 2 - mb[1]),
              meta, font=mf, fill=WHITE)

    out = img.convert("RGB")
    targets = [os.path.join(BASE, "static", "og", "preview.png"),
               os.path.join(BASE, "docs", "og-preview.png")]
    for path in targets:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out.save(path, "PNG", optimize=True)
        print("wrote", os.path.relpath(path, BASE))


if __name__ == "__main__":
    build()
