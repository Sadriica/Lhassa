# OG 1200x630: el Potala en obsidiana + wordmark, misma tecnica que witara
# (velo + blur suave antes del texto, cuantizar sin dithering para comprimir).
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os

W, H = 1200, 630
BG = (20, 17, 13)
PAPER = (236, 228, 211)
GOLD = (217, 165, 33)

base = Image.open('public/img/obsidiana/potala.png').convert('RGB')
r = W / base.width
base = base.resize((W, int(base.height * r)), Image.LANCZOS)
canvas = Image.new('RGB', (W, H), BG)
canvas.paste(base, (0, (H - base.height) // 2))

veil = Image.new('L', (W, H), 0)
dv = ImageDraw.Draw(veil)
for i, alpha in enumerate([60, 110, 160, 200]):
    dv.rectangle([0, H - 240 + i * 60, W, H - 240 + (i + 1) * 60], fill=alpha)
dark = Image.new('RGB', (W, H), BG)
canvas = Image.composite(dark, canvas, veil)
canvas = canvas.filter(ImageFilter.GaussianBlur(2.0))

d = ImageDraw.Draw(canvas)
font_b = ImageFont.truetype('/usr/share/fonts/TTF/JetBrainsMonoNL-Bold.ttf', 54)
font_r = ImageFont.truetype('/usr/share/fonts/TTF/JetBrainsMonoNL-Regular.ttf', 22)

def tracked(draw, pos, text, font, fill, tracking):
    x, y = pos
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking
    return x

x_end = tracked(d, (70, H - 150), 'LHASSA', font_b, PAPER, 16)
tracked(d, (74, H - 78), 'by Witara · Colombia', font_r, GOLD, 6)

q = canvas.quantize(colors=40, dither=Image.Dither.NONE)
os.makedirs('public/og', exist_ok=True)
q.save('public/og/home.png', optimize=True)
print('og:', os.path.getsize('public/og/home.png') // 1024, 'KB')
