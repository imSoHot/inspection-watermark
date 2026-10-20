# -*- coding: utf-8 -*-
# 新范围下的水印效果对照（含默认值）
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from watermarker import draw_watermark

os.makedirs("pack_build/size_preview", exist_ok=True)

W, H = 1200, 1600
photo = Image.new("RGB", (W, H), (108, 134, 160))
d = ImageDraw.Draw(photo)
d.rectangle([120, 420, 1080, 1200], fill=(150, 168, 186))
d.text((150, 470), "PHOTO  SAMPLE", fill=(255, 255, 255))

dt = datetime(2026, 5, 6, 8, 30)
loc = "阳塘安居小区隆兴六里"
rmk = "维保单位：银江智慧"

combos = [(35, 75), (20, 25), (50, 100), (30, 50), (25, 40), (45, 60)]

tiles = []
f = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 24)
for w, sc in combos:
    im = draw_watermark(photo.copy(), dt, loc, rmk, "左下", w, sc)
    tw = 420
    th = int(im.height * tw / im.width)
    small = im.resize((tw, th), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th + 44), (255, 255, 255))
    canvas.paste(small, (0, 0))
    tag = " ← 默认" if (w, sc) == (35, 75) else ""
    ImageDraw.Draw(canvas).text((tw // 2, th + 22), f"宽度{w}%  缩放{sc}%{tag}",
                                font=f, fill=(30, 108, 224), anchor="mm")
    tiles.append(canvas)

cols, rows = 3, 2
tw, th = tiles[0].size
pad = 14
sheet = Image.new("RGB", (cols * tw + (cols + 1) * pad, rows * th + (rows + 1) * pad),
                  (244, 246, 250))
for i, t in enumerate(tiles):
    r, c = divmod(i, cols)
    sheet.paste(t, (pad + c * (tw + pad), pad + r * (th + pad)))
out = "pack_build/size_preview/水印尺寸对照图_v2.jpg"
sheet.save(out, quality=92)
print("已生成:", out, sheet.size)
