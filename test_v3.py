# -*- coding: utf-8 -*-
# 新样式验证：标签分散对齐 / 内容区 16 字宽 / 左下角固定
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from watermarker import draw_watermark, clean_label

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        ok = False


def photo(W=1600, H=1200):
    im = Image.new("RGB", (W, H), (105, 140, 168))
    dr = ImageDraw.Draw(im)
    for y in range(0, H, 70):
        dr.line([(0, y), (W, y)], fill=(112, 147, 175))
        dr.rectangle([60 + (y % 600), y, 260 + (y % 600), y + 45], fill=(88, 108, 128))
    dr.ellipse([900, 700, 1500, 1100], fill=(80, 118, 90))
    return im


check("标签清洗", clean_label("地 点", "地点") == "地点" and clean_label("", "备注") == "备注")

# 1) 默认场景：地点 12 字（会换行），备注 8 字
im1 = draw_watermark(photo(), datetime(2026, 10, 20, 8, 30),
                     "阳塘安居小区隆兴六里23号楼东侧配电房",
                     "维保单位：银江智慧", "左下", 75)
check("渲染成功", im1.size == (1600, 1200))

# 2) 自定义标签：地点→"巡检点位"，备注→"运维单位"
im2 = draw_watermark(photo(), datetime(2026, 10, 20, 15, 40),
                     "阳塘安居小区隆兴六里",
                     "银江智慧城市科技有限公司运维部", "左下", 75,
                     remark_label="运维单位", location_label="巡检点位")

# 3) 验证内容区宽 = 16 个中文字（检查水印块宽度与字号关系）
from PIL import ImageFont as IF
from watermarker import FONT_BOLD, FONT_REG
W = 1600
base = W / 810.0
s = base * 0.75
f_info = IF.truetype(FONT_REG, max(9, int(23 * s))) if FONT_REG else IF.load_default()
cjk = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength("国", font=f_info)
expect = int(14 * s) + int(23 * s) * 2 + int(10 * s) + int(cjk * 16) + int(10 * s)
print(f"   内容区宽 {cjk*16:.0f}px（16字），整条预期约 {expect}px，占图宽 {expect/W*100:.0f}%")

# 对照图
tw = 700
tiles, cap_f = [], (ImageFont.truetype(FONT_BOLD, 20) if FONT_BOLD else ImageFont.load_default())
for im, cap in [(im1, "默认标签 · 地点 19 字自动换行"),
                (im2, "自定义标签 · 分散对齐")]:
    th = int(im.height * tw / im.width)
    small = im.resize((tw, th), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th + 38), (255, 255, 255))
    canvas.paste(small, (0, 0))
    ImageDraw.Draw(canvas).text((tw // 2, th + 19), cap, font=cap_f,
                                fill=(30, 108, 224), anchor="mm")
    tiles.append(canvas)

sheet = Image.new("RGB", (tw * 2 + 36, tiles[0].size[1] + 24), (244, 246, 250))
sheet.paste(tiles[0], (12, 12))
sheet.paste(tiles[1], (tw + 24, 12))
os.makedirs("pack_build/style_preview", exist_ok=True)
sheet.save("pack_build/style_preview/新样式预览.jpg", quality=93)
print("效果图: pack_build/style_preview/新样式预览.jpg")

print("\n===", "通过" if ok else "存在失败项", "===")
raise SystemExit(0 if ok else 1)
