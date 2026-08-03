# Tratamiento obsidiana: el dithering de la casa en la paleta Matte Obsidian
# del dashboard de Lhassa (carbon calido -> blanco hueso). Mismo metodo que
# el tratamiento carbon de witara.site; distinta piel.
from PIL import Image, ImageOps, ImageEnhance
import json
import os

STOPS = [(20, 17, 13), (74, 63, 47), (168, 152, 122), (236, 228, 211)]

def obsidiana(im, width=880):
    im = im.convert('L')
    im = ImageOps.autocontrast(im, cutoff=1)
    im = ImageEnhance.Contrast(im).enhance(1.06)
    r = width / im.width
    im = im.resize((width, int(im.height * r)), Image.LANCZOS)
    pal_img = Image.new('P', (1, 1))
    pal = []
    for g in [0, 85, 170, 255]:
        pal += [g, g, g]
    pal_img.putpalette(pal + [0] * (768 - len(pal)))
    q = im.convert('RGB').quantize(palette=pal_img, dither=Image.FLOYDSTEINBERG)
    newpal = []
    for s in STOPS:
        newpal += list(s)
    q.putpalette(newpal + [0] * (768 - len(newpal)))
    return q

if __name__ == '__main__':
    sizes = {}
    for name in ['potala', 'everest', 'banderas', 'luna']:
        src = Image.open(f'assets-src/orig/{name}.jpg').convert('RGB')
        obsidiana(src).save(f'public/img/obsidiana/{name}.png', optimize=True)
        col = src.copy()
        r = 880 / col.width
        col = col.resize((880, int(col.height * r)), Image.LANCZOS)
        col.save(f'public/img/color/{name}.jpg', quality=68, optimize=True)
        sizes[name] = {
            'obs_kb': os.path.getsize(f'public/img/obsidiana/{name}.png') // 1024,
            'w': 880, 'h': int(Image.open(f'public/img/obsidiana/{name}.png').height),
        }
    print(json.dumps(sizes, indent=1))
