# -*- coding: utf-8 -*-
# 生成 6 个水印位置的对照图
import os
from datetime import datetime

from PIL import Image, ImageDraw

from watermarker import POSITIONS, draw_watermark

os.makedirs("pack_build/pos_preview", exist_ok=True)

# 造一张“照片”
W, H = 1200, 1600
photo = Image.new("RGB", (W, H), (108, 134, 160))
d = ImageDraw.Draw(photo)
for i in range(0, H, 160):
    d.line([(0, i), (W, i)], fill=(96, 122, 148), width=2)
d.rectangle([120, 420, 1080, 1200], fill=(150, 168, 186))
d.text((150, 470), "PHOTO  SAMPLE", fill=(255, 255, 255))
dt = datetime(2026, 5, 6, 8, 30)

tiles = []
for pos in POSITIONS:
    im = draw_watermark(photo.copy(), dt, "阳塘安居小区隆兴六里", "维保单位：银江智慧",
                        "水印相机", pos)
    # 缩到统一小图并在下方写字
    tw = 420
    th = int(im.height * tw / im.width)
    small = im.resize((tw, th), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th + 46), (255, 255, 255))
    canvas.paste(small, (0, 0))
    cd = ImageDraw.Draw(canvas)
    from PIL import ImageFont
    f = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 26)
    cd.text((tw // 2, th + 22), pos, font=f, fill=(30, 108, 224), anchor="mm")
    tiles.append(canvas)

cols, rows = 3, 2
tw, th = tiles[0].size
pad = 16
sheet = Image.new("RGB", (cols * tw + (cols + 1) * pad, rows * th + (rows + 1) * pad),
                  (244, 246, 250))
for i, t in enumerate(tiles):
    r, c = divmod(i, cols)
    sheet.paste(t, (pad + c * (tw + pad), pad + r * (th + pad)))
out = "pack_build/pos_preview/水印位置对照图.jpg"
sheet.save(out, quality=92)
print("已生成:", out, sheet.size)
