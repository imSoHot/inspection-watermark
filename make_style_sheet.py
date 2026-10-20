# -*- coding: utf-8 -*-
# 验证磨砂玻璃 + 深灰字 + 50% 透明度效果
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from watermarker import draw_watermark

os.makedirs("pack_build/style_preview", exist_ok=True)


def make_photo(W=1200, H=1600):
    """造一张有细节纹理的“照片”，用来检验磨砂模糊效果"""
    p = Image.new("RGB", (W, H), (96, 128, 150))
    d = ImageDraw.Draw(p)
    # 天空渐变
    for y in range(0, H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(120 - 40 * k), int(150 - 30 * k), int(178 - 20 * k)))
    # 建筑/树木纹理
    for i in range(0, W, 90):
        d.rectangle([i, 900, i + 60, 1400], fill=(86, 106, 122))
        d.rectangle([i + 10, 940, i + 50, 1030], fill=(206, 200, 170))
    d.ellipse([200, 1200, 900, 1520], fill=(78, 116, 88))
    d.rectangle([0, 1400, W, H], fill=(120, 120, 118))
    return p


dt = datetime(2026, 5, 6, 8, 30)
loc = "阳塘安居小区隆兴六里"
rmk = "维保单位：银江智慧"

tiles = []
f = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 22)
for blur, tag in [(True, "磨砂玻璃（新）"), (False, "无模糊对照")]:
    for pos in ["左下", "上方"]:
        im = draw_watermark(make_photo(), dt, loc, rmk, pos, 85, blur)
        tw = 430
        th = int(im.height * tw / im.width)
        small = im.resize((tw, th), Image.LANCZOS)
        canvas = Image.new("RGB", (tw, th + 42), (255, 255, 255))
        canvas.paste(small, (0, 0))
        ImageDraw.Draw(canvas).text((tw // 2, th + 21), f"{tag} · {pos}",
                                    font=f, fill=(30, 108, 224), anchor="mm")
        tiles.append(canvas)

cols = 2
rows = (len(tiles) + cols - 1) // cols
tw, th = tiles[0].size
pad = 14
sheet = Image.new("RGB", (cols * tw + (cols + 1) * pad, rows * th + (rows + 1) * pad),
                  (244, 246, 250))
for i, t in enumerate(tiles):
    r, c = divmod(i, cols)
    sheet.paste(t, (pad + c * (tw + pad), pad + r * (th + pad)))
out = "pack_build/style_preview/水印样式对照图.jpg"
sheet.save(out, quality=93)
print("已生成:", out, sheet.size)
