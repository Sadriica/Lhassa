#!/usr/bin/env python3
"""Generate the Open Graph image (1200x630) in the "tratamiento noche"
aesthetic, for lhassa.witara.site.

Adapted from Witara's scripts/og.py: same composition (cover-crop photo,
veils for legibility, tracked-letter wordmark, quantized palette to keep
the file small), noche/luna/acento colors instead of carbon/paper/accent.

Usage:
    python scripts/og.py
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOCHE_DIR = os.path.join(ROOT, "public", "img", "noche")

WIDTH, HEIGHT = 1200, 630

NOCHE = (11, 15, 26)      # #0b0f1a
LUNA = (233, 237, 240)    # #e9edf0
ACENTO = (201, 168, 106)  # #c9a86a

FONT_BOLD = "/usr/share/fonts/TTF/JetBrainsMonoNL-Bold.ttf"
FONT_REGULAR = "/usr/share/fonts/TTF/JetBrainsMonoNL-Regular.ttf"

PHOTO = "01.png"
SUBTITLE = "by Witara · Construye el cerebro, alquila el cuerpo"
OUT_PATH = os.path.join(ROOT, "public", "og", "home.png")


def cover_crop(im: Image.Image, w: int, h: int) -> Image.Image:
    src_w, src_h = im.size
    scale = max(w / src_w, h / src_h)
    new_w, new_h = round(src_w * scale), round(src_h * scale)
    im = im.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return im.crop((left, top, left + w, top + h))


def draw_tracked_text(draw, xy, text, font, fill, tracking):
    x, y = xy
    for ch in text:
        bbox = font.getbbox(ch)
        w = bbox[2] - bbox[0] if ch != " " else font.getbbox("x")[2] // 2
        draw.text((x, y), ch, font=font, fill=fill)
        x += w + tracking


def spaced(text: str) -> str:
    return " ".join(list(text))


STEPS = 14


def _stepped(local: float, steps: int = STEPS) -> float:
    return round(local * steps) / steps


def make_veil(w: int, h: int, top: bool, strength: float = 0.92) -> Image.Image:
    veil = Image.new("L", (1, h), 0)
    for y in range(h):
        t = y / h
        if top:
            if t > 0.25:
                a = 0
            else:
                local = 1 - (t / 0.25)
                a = int(255 * _stepped(local ** 1.6) * 0.35)
        else:
            if t < 0.40:
                a = 0
            else:
                local = (t - 0.40) / 0.60
                a = int(255 * _stepped(local ** 1.4) * strength)
        veil.putpixel((0, y), a)
    veil = veil.resize((w, h))
    layer = Image.new("RGBA", (w, h), NOCHE + (0,))
    layer.putalpha(veil)
    return layer


def build_og() -> str:
    base = Image.open(os.path.join(NOCHE_DIR, PHOTO)).convert("RGB")
    base = cover_crop(base, WIDTH, HEIGHT)
    canvas = base.convert("RGBA")

    tint = Image.new("RGBA", (WIDTH, HEIGHT), NOCHE + (40,))
    canvas = Image.alpha_composite(canvas, tint)
    canvas = Image.alpha_composite(canvas, make_veil(WIDTH, HEIGHT, top=True))
    canvas = Image.alpha_composite(canvas, make_veil(WIDTH, HEIGHT, top=False))
    canvas = canvas.convert("RGB").filter(ImageFilter.GaussianBlur(2.2)).convert("RGBA")

    draw = ImageDraw.Draw(canvas)
    margin = 96
    rule_y = HEIGHT - 190
    draw.line([(margin, rule_y), (margin + 64, rule_y)], fill=ACENTO + (255,), width=2)

    wordmark_font = ImageFont.truetype(FONT_BOLD, 52)
    subtitle_font = ImageFont.truetype(FONT_REGULAR, 20)

    word_y = rule_y + 22
    draw_tracked_text(draw, (margin, word_y), spaced("LHASSA"), wordmark_font, LUNA, 18)

    sub_y = word_y + 68
    draw.text((margin, sub_y), SUBTITLE, font=subtitle_font, fill=ACENTO + (255,))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    rgb = canvas.convert("RGB")
    quantized = rgb.quantize(colors=48, method=Image.MEDIANCUT, dither=Image.NONE)
    quantized.save(OUT_PATH, format="PNG", optimize=True, compress_level=9)
    return OUT_PATH


def main() -> None:
    out_path = build_og()
    size = os.path.getsize(out_path)
    print(f"{out_path}  {size/1024:.1f} KB")


if __name__ == "__main__":
    main()
