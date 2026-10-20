# -*- coding: utf-8 -*-
# 验证：超过 10 个字自动换行，水印高度随行数增长
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from watermarker import draw_watermark, wrap_text, FONT_REG

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        ok = False


d = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
f = ImageFont.truetype(FONT_REG, 25)

lines = wrap_text(d, "阳塘安居小区隆兴六里23号楼", f, 400, 10, 4)
check("12字→2行", len(lines) == 2, str(lines))
check("首行10字", len(lines[0]) == 10, lines[0])

lines2 = wrap_text(d, "维保单位：银江智慧", f, 400, 10, 4)
check("9字→1行", len(lines2) == 1, str(lines2))

long_text = ("阳塘安居小区隆兴六里二十三号楼二单元东侧配电房顶楼"
             "室外机柜巡检点位补充说明文字用于测试超长截断逻辑")
lines3 = wrap_text(d, long_text, f, 400, 10, 4)
check("超长文本最多4行", len(lines3) == 4, f"{len(lines3)}行")
check("截断时末行有省略号", lines3[-1].endswith("…"), lines3[-1])

check("空文本返回空列表", wrap_text(d, "", f, 400) == [])


def photo(W=1600, H=1200):
    im = Image.new("RGB", (W, H), (110, 145, 170))
    dr = ImageDraw.Draw(im)
    for y in range(0, H, 60):
        dr.line([(0, y), (W, y)], fill=(115, 150, 175))
        dr.rectangle([100 + (y % 500), y, 300 + (y % 500), y + 40], fill=(90, 110, 130))
    return im


short = draw_watermark(photo(), datetime(2026, 5, 12, 10, 20),
                       "小区A", "维保单位：银江智慧", "左下", 75)
long_ = draw_watermark(photo(), datetime(2026, 5, 12, 10, 25),
                       "阳塘安居小区隆兴六里23号楼东侧配电房",
                       "维保单位：银江智慧城市科技有限公司运维部",
                       "左下", 75)
print("短文本尺寸:", short.size, "长文本尺寸:", long_.size)

# 对比图
tw = 520
tiles = []
fcap = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 20)
for im, cap in [(short, "10字以内：单行"), (long_, "超过10字：自动换行")]:
    th = int(im.height * tw / im.width)
    small = im.resize((tw, th), Image.LANCZOS)
    canvas = Image.new("RGB", (tw, th + 38), (255, 255, 255))
    canvas.paste(small, (0, 0))
    ImageDraw.Draw(canvas).text((tw // 2, th + 19), cap, font=fcap,
                                fill=(30, 108, 224), anchor="mm")
    tiles.append(canvas)

sheet = Image.new("RGB", (tw * 2 + 36, max(t.size[1] for t in tiles) + 24), (244, 246, 250))
sheet.paste(tiles[0], (12, 12))
sheet.paste(tiles[1], (tw + 24, 12))
sheet.save("pack_build/style_preview/自动换行对照图.jpg", quality=93)
print("对照图: pack_build/style_preview/自动换行对照图.jpg")

print("\n===", "全部通过" if ok else "存在失败项", "===")
raise SystemExit(0 if ok else 1)
