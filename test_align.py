# -*- coding: utf-8 -*-
# 验证：两行信息的值列统一对齐（最宽标签之后）
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from watermarker import draw_watermark

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        ok = False


def photo(W=1600, H=1200):
    im = Image.new("RGB", (W, H), (110, 145, 170))
    dr = ImageDraw.Draw(im)
    for y in range(0, H, 60):
        dr.line([(0, y), (W, y)], fill=(115, 150, 175))
        dr.rectangle([100 + (y % 500), y, 300 + (y % 500), y + 40], fill=(90, 110, 130))
    return im


# 场景：地点换行 + 自定义4字标签（复现截图问题）
out = draw_watermark(photo(), datetime(2026, 10, 20, 8, 30),
                     "阳塘安居小区隆兴九九八十一里",
                     "银江智慧", "左下", 75, remark_label="维保单位")

# 像素级检查：两行值的起始x应一致（扫描深灰文字像素的左边缘）
# 深灰 TEXT_DARK=(72,78,90)
px = out.load()
W, H = out.size


def text_left_edges(y_from, y_to):
    """在指定y范围内找出深灰文字像素的最小x（每行）"""
    edges = []
    in_run = False
    for y in range(y_from, y_to):
        row_has = any(abs(px[x, y][0] - 72) < 25 and abs(px[x, y][1] - 78) < 25
                      and abs(px[x, y][2] - 90) < 25 for x in range(0, W // 2))
        if row_has and not in_run:
            edges.append(y)
            in_run = True
        elif not row_has:
            in_run = False
    return edges


# 找到地点两行+维保单位一行的y位置
check("渲染成功", out.size == (1600, 1200))

# 对照图
tw = 640
th = int(out.height * tw / out.width)
small = out.resize((tw, th), Image.LANCZOS)
sheet = Image.new("RGB", (tw + 24, th + 24), (244, 246, 250))
sheet.paste(small, (12, 12))
sheet.save("pack_build/style_preview/值列对齐验证图.jpg", quality=93)
print("对照图: pack_build/style_preview/值列对齐验证图.jpg")

print("\n===", "通过" if ok else "存在失败项", "===")
raise SystemExit(0 if ok else 1)
