#!/usr/bin/env python3
"""Generate the "tratamiento noche" duotone dithered images for the Lhassa
presence site.

Sibling of Witara's carbon pipeline (docs/soul/08-sitio-corporativo.md):
same method (grayscale -> 880px wide -> Floyd-Steinberg dithered to 4 tonal
steps), different temperature. Instead of quantizing to carbon/paper, each
photo is quantized to 4 discrete steps between --noche (deep ink-blue night)
and --luna (pale moonlight paper), so the dithered grain reads as night
photography rather than a color filter forced over a daytime shot.

Usage:
    python scripts/noche.py

Reads originals from a source directory (see SOURCES below, absolute paths
into the scratch download folder) and writes:
  - public/img/noche/NN.png   (dithered duotone, 880px wide)
  - public/img/color/NN.jpg   (the original photo, recompressed, for the
    hover-reveals-color gesture)
and prints each output's file size in KB (the caption honesty pattern).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOCHE_DIR = os.path.join(ROOT, "public", "img", "noche")
COLOR_DIR = os.path.join(ROOT, "public", "img", "color")

TARGET_WIDTH = 880
STEPS = 4  # four tonal bands, same as Witara's carbon pipeline

# Duotone endpoints: darkest step lands on --noche, lightest on --luna.
NOCHE = (11, 15, 26)     # #0b0f1a
LUNA = (233, 237, 240)   # #e9edf0


@dataclass
class Source:
    path: str        # absolute path to the downloaded original
    out: str         # output basename, e.g. "01"
    alt_es: str
    alt_en: str


SOURCES = [
    Source(
        "/tmp/claude-1000/-home-w-Code-Witara/968f5e59-b9cc-4c09-b460-9c08767134e3/scratchpad/commons/orig/01-cielo.jpg",
        "01",
        "Cielo estrellado sobre el desierto de Atacama, Andes de Chile",
        "Starry sky over the Atacama desert, Chilean Andes",
    ),
    Source(
        "/tmp/claude-1000/-home-w-Code-Witara/968f5e59-b9cc-4c09-b460-9c08767134e3/scratchpad/commons/orig/02-luna.jpg",
        "02",
        "Luna llena saliendo sobre la cordillera Sangre de Cristo",
        "Full moon rising over the Sangre de Cristo mountains",
    ),
    Source(
        "/tmp/claude-1000/-home-w-Code-Witara/968f5e59-b9cc-4c09-b460-9c08767134e3/scratchpad/commons/orig/03-via-lactea.jpg",
        "03",
        "Vía láctea sobre el glaciar Juncal, Andes de Chile",
        "Milky Way over Glaciar Juncal, Chilean Andes",
    ),
    Source(
        "/tmp/claude-1000/-home-w-Code-Witara/968f5e59-b9cc-4c09-b460-9c08767134e3/scratchpad/commons/orig/04-nevado.jpg",
        "04",
        "Nevado de noche bajo un cielo estrellado, larga exposición",
        "Snow peak at night under a starry sky, long exposure",
    ),
]


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def duotone_palette() -> list[int]:
    """A 256-entry RGB palette where every entry is one of STEPS discrete
    noche->luna colors, chosen by that entry's position in the 0..255 range.
    Passing this to Image.quantize with a matching P-mode palette image and
    dither=FLOYDSTEINBERG gives error-diffusion dithering *between* the small
    set of duotone steps, instead of smooth per-pixel color -- the visible
    grain that is the pipeline's signature."""
    palette: list[int] = []
    for i in range(256):
        step = min(int(i / 256 * STEPS), STEPS - 1)
        t = step / (STEPS - 1)
        r, g, bch = lerp(NOCHE, LUNA, t)
        palette.extend([r, g, bch])
    return palette


def process(src: Source) -> None:
    im = Image.open(src.path).convert("RGB")

    # ----- color original (for the hover-reveals-color gesture) -----
    w, h = im.size
    scale = TARGET_WIDTH / w
    color_im = im.resize((TARGET_WIDTH, round(h * scale)), Image.LANCZOS)
    os.makedirs(COLOR_DIR, exist_ok=True)
    color_path = os.path.join(COLOR_DIR, f"{src.out}.jpg")
    color_im.save(color_path, format="JPEG", quality=78, optimize=True)

    # ----- noche duotone, dithered -----
    gray = color_im.convert("L")
    # Palette image: a 256x1 "P" image whose palette is our duotone ramp.
    pal_im = Image.new("P", (16, 16))
    pal_im.putpalette(duotone_palette())
    dithered = gray.convert("RGB").quantize(
        palette=pal_im, dither=Image.FLOYDSTEINBERG
    )
    # The dithered image only ever contains the STEPS distinct duotone RGB
    # triples (the ramp collapses 256 palette slots down to 4 real colors),
    # so re-quantizing to a genuine small palette loses nothing and lets the
    # PNG encoder store a real palette instead of one entry per 256-slot
    # ramp -- this is what actually shrinks the file, not just `optimize`.
    final = dithered.convert("RGB").quantize(
        colors=STEPS, method=Image.MEDIANCUT, dither=Image.NONE
    )
    os.makedirs(NOCHE_DIR, exist_ok=True)
    out_path = os.path.join(NOCHE_DIR, f"{src.out}.png")
    final.save(out_path, format="PNG", optimize=True)

    kb = os.path.getsize(out_path) / 1024
    ckb = os.path.getsize(color_path) / 1024
    print(f"{src.out}.png  {dithered.size[0]}x{dithered.size[1]}  {kb:6.1f} KB   (color: {ckb:5.1f} KB)")


def main() -> None:
    for src in SOURCES:
        process(src)


if __name__ == "__main__":
    main()
