# -*- coding: utf-8 -*-
"""
批量给巡检照片加"设备巡检维护"水印（仿水印相机样式）

两种用法：
1) 配置文件方式（推荐，双击 一键运行加水印.bat 即可）：
   python watermarker.py --config config.json
2) 命令行参数方式：
   python watermarker.py --src "D:/原图" --out "D:/加水印" --start "2026-05-06 08:30" --end "2026-05-11 18:00" --location "xxx" --remark "xxx"
"""
import argparse
import csv
import json
import multiprocessing
import os
import random
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, time, timedelta

from PIL import Image, ImageDraw, ImageFont, ImageOps

# 程序所在目录（打包成 exe 后即为 exe 所在目录）
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"
if not os.path.exists(FONT_BOLD):
    FONT_BOLD = r"C:\Windows\Fonts\simhei.ttf"
    FONT_REG = FONT_BOLD

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# 底色统一 50% 透明度
BLUE = (30, 108, 224, 128)          # 标题栏：蓝色 50% 透明
BLUE_DEEP = (23, 92, 199, 128)      # 时间栏：深一点的蓝 50% 透明
INFO_BG = (255, 255, 255, 128)      # 地点/备注：磨砂白 50% 透明
BLUR_BG = (247, 249, 252, 128)      # 磨砂玻璃底色
WHITE = (255, 255, 255, 255)
ICON_YELLOW = (255, 200, 40, 255)
ICON_DARK = (60, 60, 60, 255)
TEXT_DARK = (26, 30, 40, 255)       # 地点/备注内容文字：近黑深灰（磨砂底上对比度高）
TEXT_LABEL = (44, 50, 62, 255)      # 标签：深灰蓝（对比度高，半透明底上也清晰）
TEXT_MUTED = (110, 118, 132, 255)   # 标签：中灰（已弃用，保留兼容）
TEXT_ON_BLUE = (255, 255, 255, 255)  # 标题/时间：白字（蓝底上）


def log(msg):
    print(msg, flush=True)


def collect_images(src):
    """src 可以是文件夹（递归收集）、单个图片文件，
    或用 | 分隔的多项组合（文件夹与文件可混选）"""
    if "|" in src:
        out = []
        for part in src.split("|"):
            out.extend(collect_images(part))
        return out
    if os.path.isfile(src):
        return [src] if os.path.splitext(src)[1].lower() in IMG_EXTS else []
    files = []
    for root, _dirs, names in os.walk(src):
        for n in names:
            if os.path.splitext(n)[1].lower() in IMG_EXTS:
                files.append(os.path.join(root, n))
    files.sort()
    return files


DAY_START = "08:30"   # 每天允许生成的最早时间
DAY_END = "18:00"     # 每天允许生成的最晚时间


def daytime_segments(start, end, day_start=DAY_START, day_end=DAY_END):
    """把 [start, end] 按天切分，返回与每天 day_start~day_end 的交集段列表。
    跨天的情况也只在每天的白天时段内生成时间，绝不产生夜间时间。"""
    hs, ms = (int(x) for x in day_start.split(":"))
    he, me = (int(x) for x in day_end.split(":"))
    ts, te = time(hs, ms), time(he, me)
    segs = []
    d = start.date()
    while d <= end.date():
        a = max(datetime.combine(d, ts), start)
        b = min(datetime.combine(d, te), end)
        if a < b:
            segs.append((a, b))
        d += timedelta(days=1)
    return segs


def usable_minutes(start, end):
    """[start, end] 内实际可用于排时间的白天总分钟数"""
    segs = daytime_segments(start, end)
    return sum((b - a).total_seconds() for a, b in segs) / 60.0


def generate_times(n, start, end, min_gap_min, max_gap_min, seed=42):
    """生成 n 个连续递增的时间，均匀铺满 [start, end] 的每天白天时段：
    - 时间段先按天切分，只保留每天 8:30~18:00 的部分（跨天也只在白天生成）
    - 平均间隔 base = 白天可用总时长 / 张数
    - 每张的实际间隔 = base × 随机抖动(0.55~1.5)，有快有慢但整体递增
    - 生成的时间严格落在每天 8:30~18:00 之间
    返回 (时间列表, 实际平均间隔分钟)
    """
    rnd = random.Random(seed)
    segs = daytime_segments(start, end)
    if n <= 0:
        return [], 0.0
    if n == 1 or not segs:
        first = segs[0][0] if segs else start
        return [first] * n, 0.0

    total = sum((b - a).total_seconds() for a, b in segs)
    base = total / n
    lo_min = max(min_gap_min, 0.5) * 60
    hi_max = max(max_gap_min, min_gap_min + 1) * 60

    gaps = [base * rnd.uniform(0.55, 1.5) for _ in range(n - 1)]

    # 用户给的间隔范围可行时才夹取（否则以铺满可用时段为准）
    if lo_min <= base <= hi_max:
        gaps = [min(max(g, lo_min), hi_max) for g in gaps]

    # 缩放到刚好覆盖可用时段（留 3% 余量），保证最后一张不越界
    total_g = sum(gaps)
    target = total * 0.97
    if total_g > target:
        k = target / total_g
        gaps = [max(g * k, 15.0) for g in gaps]

    # 累计偏移映射到各天时段内；偏移落在夜间空档时自然落到次日 8:30
    bounds, acc = [], 0.0
    for a, b in segs:
        dur = (b - a).total_seconds()
        bounds.append((a, acc, acc + dur))
        acc += dur

    times = [segs[0][0]]
    cur = 0.0
    for g in gaps:
        cur = min(cur + g, target)
        for a, c0, c1 in bounds:
            if cur <= c1:
                times.append(a + timedelta(seconds=max(0.0, cur - c0)))
                break
        else:
            times.append(segs[-1][1])

    eff = total / 60.0 / (n - 1)
    return times, eff


POSITIONS = ["左上", "上方", "右上", "左下", "下方", "右下"]

POSITION_ALIASES = {
    "左上角": "左上", "上中": "上方", "上方居中": "上方", "右上角": "右上",
    "左下角": "左下", "下中": "下方", "下方居中": "下方", "右下角": "右下",
    "top-left": "左上", "top-center": "上方", "top-right": "右上",
    "bottom-left": "左下", "bottom-center": "下方", "bottom-right": "右下",
}


def normalize_position(p):
    p = (p or "").strip()
    p = POSITION_ALIASES.get(p, p)
    return p if p in POSITIONS else "左下"


VERT_MAP = {"左上": "top", "上方": "top", "右上": "top",
            "左下": "bottom", "下方": "bottom", "右下": "bottom"}
HORIZ_MAP = {"左上": "left", "左下": "left",
             "上方": "center", "下方": "center",
             "右上": "right", "右下": "right"}


def fit_text(d, text, font, max_w):
    """文字超出可用宽度时截断并加省略号"""
    text = text or ""
    if not text or max_w <= 0:
        return ""
    try:
        if d.textlength(text, font=font) <= max_w:
            return text
        ell = "…"
        while text and d.textlength(text + ell, font=font) > max_w:
            text = text[:-1]
        return text + ell
    except Exception:
        return text


def clean_label(label, default):
    """标签文字清洗：去掉全角/半角空格（分散对齐时由排版自动拉开间距）"""
    t = (label or "").replace("　", " ").replace(" ", "").strip()
    return t or default


def draw_label_justified(d, x, cy, text, font, col_w, fill):
    """把标签在固定列宽内分散对齐：首字贴左、末字贴右，字间距均分"""
    widths = [d.textlength(ch, font=font) for ch in text]
    total = sum(widths)
    if len(text) <= 1 or total >= col_w:
        d.text((x, cy), text, font=font, fill=fill, anchor="lm")
        return
    space = (col_w - total) / (len(text) - 1)
    cur = x
    for ch, w in zip(text, widths):
        d.text((cur, cy), ch, font=font, fill=fill, anchor="lm")
        cur += w + space


def wrap_text(d, text, font, max_w, max_chars=10, max_lines=4):
    """按每行最多 max_chars 个字（且不超像素宽度 max_w）自动换行。
    返回行列表；超过 max_lines 行时截断并在末行加省略号。"""
    text = (text or "").strip()
    if not text:
        return []
    lines, cur = [], ""
    for ch in text:
        cand = cur + ch
        if cur and (len(cur) >= max_chars or d.textlength(cand, font=font) > max_w):
            lines.append(cur)
            cur = ch
        else:
            cur = cand
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        last = kept[-1]
        while last and d.textlength(last + "…", font=font) > max_w:
            last = last[:-1]
        kept[-1] = last + "…"
        return kept
    return lines


def draw_watermark(img, dt, location, remark, position="左下",
                   scale_pct=75.0, blur=True,
                   remark_label="备注", location_label="地点",
                   value_chars=16):
    """绘制水印（位置默认固定左下角）
    scale_pct      整体字号/高度缩放百分比（25~100）
    blur           地点/备注区域是否使用磨砂玻璃质感（背景模糊）
    location_label 地点一行的标签文字（可自定义）
    remark_label   备注一行的标签文字（可自定义）
    value_chars    内容区固定宽度（中文字符数，默认 16），超出自动换行
    标签列在两行之间分散对齐（首字贴左、末字贴右）
    """
    W, H = img.size
    pos = normalize_position(position)
    vert = VERT_MAP[pos]
    horiz = HORIZ_MAP[pos]

    base = W / 810.0                      # 与原样图的比例基准
    s = base * (max(25.0, min(100.0, scale_pct)) / 100.0)

    title_h = max(14, int(46 * s))
    time_h = max(16, int(50 * s))
    info_h = max(13, int(42 * s))         # 单行信息的高度 / 行距
    margin = max(5, int(12 * base))

    f_title = ImageFont.truetype(FONT_BOLD, max(10, int(27 * s)))
    f_time = ImageFont.truetype(FONT_BOLD, max(10, int(29 * s)))
    f_label = ImageFont.truetype(FONT_BOLD, max(9, int(23 * s)))
    f_info = ImageFont.truetype(FONT_REG, max(9, int(23 * s)))

    pad_l = int(14 * s)
    pad_r = int(10 * s)
    gap = int(10 * s)

    # ---- 先排版：标签列宽 + 固定 16 字的内容区 → 反推水印总宽 ----
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    lab_loc = clean_label(location_label, "地点")
    lab_rmk = clean_label(remark_label, "备注")
    lab_col = max(int(scratch.textlength(t, font=f_label)) for t in (lab_loc, lab_rmk))
    cjk_w = scratch.textlength("国", font=f_info)          # 一个中文字的实际宽度
    value_w = max(int(cjk_w * value_chars), 1)
    bar_w = pad_l + lab_col + gap + value_w + pad_r
    bar_w = min(bar_w, W - 2 * margin)                     # 小图防溢出

    # 水平：左 / 居中 / 右
    if horiz == "left":
        x0 = margin
    elif horiz == "right":
        x0 = W - margin - bar_w
    else:
        x0 = (W - bar_w) // 2
    x0 = max(0, x0)

    vx = pad_l + lab_col + gap          # 值的统一起点（两行对齐）
    vmax = bar_w - pad_r - vx
    rows = []
    for label, value in ((lab_loc, location), (lab_rmk, remark)):
        lines = wrap_text(scratch, value, f_info, vmax,
                          max_chars=value_chars, max_lines=4)
        if not lines:
            lines = [""]
        rows.append({"label": label, "lines": lines, "h": info_h * len(lines)})

    info_h_total = sum(r["h"] for r in rows)
    block_h = title_h + time_h + info_h_total

    # 垂直：上 / 下（水印高度随行数变化，位置据此计算）
    y0 = margin if vert == "top" else H - margin - block_h
    y0 = max(0, y0)

    info_top = y0 + title_h + time_h      # 地点/备注区域起始 y
    info_bottom = min(info_top + info_h_total, H)   # 防越界

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # ---- 地点/备注区域：磨砂玻璃质感 ----
    if info_bottom > info_top:
        region_box = (x0, info_top, x0 + bar_w, info_bottom)
        region_h = info_bottom - info_top
        if blur:
            from PIL import ImageFilter
            br = max(2, int(10 * base))       # 模糊半径随图缩放
            region = img.crop(region_box).filter(ImageFilter.GaussianBlur(br)).convert("RGBA")
        else:
            region = Image.new("RGBA", (bar_w, region_h), BLUR_BG)
        # 叠一层 50% 白，形成玻璃底（直角，不做圆角处理）
        glass = Image.alpha_composite(region, Image.new("RGBA", region.size, INFO_BG))
        overlay.paste(glass, (x0, info_top))
        # 细边框，强化玻璃边缘（直角）
        d.rectangle(region_box, outline=(255, 255, 255, 150),
                    width=max(1, int(2 * base)))

    # ---- 标题栏（50% 透明蓝）----
    d.rectangle([x0, y0, x0 + bar_w, y0 + title_h], fill=BLUE)
    tx = x0 + pad_l
    title = fit_text(d, "设备巡检维护", f_title, bar_w - (tx - x0) - pad_r)
    d.text((tx, y0 + title_h // 2), title, font=f_title, fill=TEXT_ON_BLUE, anchor="lm")

    # ---- 时间栏（50% 透明深蓝）----
    y1 = y0 + title_h
    d.rectangle([x0, y1, x0 + bar_w, y1 + time_h], fill=BLUE_DEEP)
    tstr = fit_text(d, dt.strftime("%Y.%m.%d  %H:%M"), f_time, bar_w - pad_l - pad_r)
    d.text((x0 + pad_l, y1 + time_h // 2), tstr, font=f_time, fill=TEXT_ON_BLUE, anchor="lm")

    # ---- 信息行文字：深灰（磨砂底上），标签分散对齐、值逐行绘制 ----
    yr = info_top
    for r in rows:
        if yr + r["h"] > H:
            break
        cy = yr + info_h // 2          # 标签与首行文字垂直居中对齐
        draw_label_justified(d, x0 + pad_l, cy, r["label"], f_label, lab_col, TEXT_LABEL)
        for k, line in enumerate(r["lines"]):
            if line:
                d.text((x0 + vx, cy + k * info_h), line,
                       font=f_info, fill=TEXT_DARK, anchor="lm")
        yr += r["h"]

    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def process_one(job):
    """处理单张照片，返回 (原文件名, 水印时间字符串, 错误信息)"""
    (src, dst, ts_str, location, remark, position,
     wm_scale, remark_label, location_label) = job
    try:
        img = Image.open(src)
        img = ImageOps.exif_transpose(img)
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        out_img = draw_watermark(img, dt, location, remark, position, wm_scale,
                                 remark_label=remark_label,
                                 location_label=location_label)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        out_img.save(dst, "JPEG", quality=92)
        return (os.path.basename(src), dt.strftime("%Y.%m.%d %H:%M"), "")
    except Exception as e:
        return (os.path.basename(src), ts_str, f"{type(e).__name__}: {e}")


def build_parser():
    ap = argparse.ArgumentParser(description="批量给巡检照片加水印")
    ap.add_argument("--config", default="", help="配置文件路径(JSON)")
    ap.add_argument("--src", default="", help="原始照片目录")
    ap.add_argument("--out", default="", help="输出目录")
    ap.add_argument("--start", default="", help="时间段开始 2026-05-06 08:30")
    ap.add_argument("--end", default="", help="时间段结束 2026-05-11 18:00")
    ap.add_argument("--location", default="")
    ap.add_argument("--remark", default="")
    ap.add_argument("--location-label", default="", help="地点一行的标签文字（默认：地点）")
    ap.add_argument("--remark-label", default="", help="备注一行的标签文字（默认：备注）")
    ap.add_argument("--position", default="", help="水印位置（默认固定左下角，一般不用改）")
    ap.add_argument("--wm-width", type=float, default=None, help="（已废弃，内容区固定 16 字宽，忽略）")
    ap.add_argument("--wm-scale", type=float, default=None, help="水印高度与字号缩放百分比 25~100")
    ap.add_argument("--min-gap", type=float, default=None, help="最小间隔(分钟)")
    ap.add_argument("--max-gap", type=float, default=None, help="最大间隔(分钟)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--workers", type=int, default=None, help="并行进程数")
    ap.add_argument("--csv", default="", help="时间对照表输出路径")
    return ap


def load_config(path):
    r"""读取配置；自动兼容记事本手写的单反斜杠 Windows 路径（D:\xx -> D:/xx）。
    注意 \t、\n、\b 等是合法 JSON 转义，json.loads 不会报错但会毁掉路径，
    因此解析失败时把所有反斜杠统一替换为正斜杠再解析。"""
    with open(path, "r", encoding="utf-8-sig") as f:
        raw = f.read()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        fixed = raw.replace("\\", "/")
        try:
            cfg = json.loads(fixed)
            log("[!] 提示：config.json 里的路径使用了单反斜杠，已自动修正。")
            log("    建议以后路径写成正斜杠 D:/xx 或双反斜杠 D:/xx")
            return cfg
        except json.JSONDecodeError as e:
            log(f"[×] config.json 格式有误（第 {e.lineno} 行第 {e.colno} 列附近），请检查引号和逗号是否完整")
            sys.exit(1)


def main():
    args = build_parser().parse_args()
    cfg = {}
    # 未传任何参数（例如双击 exe）时，自动读取同目录下的 config.json
    if not args.config and not args.src:
        auto = os.path.join(BASE_DIR, "config.json")
        if os.path.exists(auto):
            args.config = auto
    if args.config:
        p = args.config
        if not os.path.isabs(p):
            p = os.path.join(BASE_DIR if os.path.exists(os.path.join(BASE_DIR, p)) else os.getcwd(), p)
        if not os.path.exists(p):
            log(f"[×] 找不到配置文件: {p}")
            sys.exit(1)
        cfg = load_config(p)
        log(f"[√] 已读取配置: {p}")

    def pick(a, b, default=None):
        return a if a not in ("", None) else cfg.get(b, default)

    args.src = pick(args.src, "src", "")
    args.out = pick(args.out, "out", "")
    args.start = pick(args.start, "start", "")
    args.end = pick(args.end, "end", "")
    args.location = pick(args.location, "location", "")
    args.remark = pick(args.remark, "remark", "")
    remark_label = (args.remark_label or cfg.get("remark_label") or "备注").strip() or "备注"
    location_label = (args.location_label or cfg.get("location_label") or "地点").strip() or "地点"
    position = normalize_position(args.position or cfg.get("position", "左下"))
    wm_scale = args.wm_scale if args.wm_scale is not None else cfg.get("wm_scale_pct", 75)
    try:
        wm_scale = float(wm_scale)
    except (TypeError, ValueError):
        wm_scale = 75.0
    mg = args.min_gap if args.min_gap is not None else cfg.get("min_gap_minutes", 3)
    Mg = args.max_gap if args.max_gap is not None else cfg.get("max_gap_minutes", 25)
    seed = args.seed if args.seed is not None else cfg.get("seed", 42)
    workers = args.workers if args.workers is not None else cfg.get("workers", 4)

    if not args.src or not args.start or not args.end:
        log("[×] 缺少必填项：src / start / end（请检查 config.json）")
        sys.exit(1)
    if not os.path.isdir(args.src):
        log(f"[×] 原始照片目录不存在: {args.src}")
        sys.exit(1)
    if not args.out:
        args.out = os.path.join(os.path.dirname(os.path.abspath(args.src)), "加水印输出")

    fmt = "%Y-%m-%d %H:%M"
    try:
        start = datetime.strptime(args.start, fmt)
        end = datetime.strptime(args.end, fmt)
    except ValueError:
        log("[×] 时间格式应为 2026-05-06 08:30")
        sys.exit(1)
    if end <= start:
        log("[×] 结束时间必须晚于开始时间")
        sys.exit(1)

    files = collect_images(args.src)
    if not files:
        log(f"[×] 未找到照片: {args.src}")
        sys.exit(1)

    log(f"照片数量: {len(files)}")
    log(f"时间段: {args.start} ~ {args.end}（每天仅在 8:30~18:00 生成）")
    log(f"水印位置: {position}（固定左下）  缩放: {wm_scale:.0f}%  内容宽: 16 字/行")
    log(f"输出目录: {args.out}")

    times, eff = generate_times(len(files), start, end, mg, Mg, seed)
    log(f"平均间隔约 {eff:.1f} 分钟/张（{eff/60:.1f} 小时）")

    jobs = []
    # 文件夹输入保持子目录结构；单个/多个文件输入平铺到输出目录
    flat = ("|" in args.src) or os.path.isfile(args.src)
    for p, dt in zip(files, times):
        if flat:
            name, _ext = os.path.splitext(os.path.join(args.out, os.path.basename(p)))
        else:
            rel = os.path.relpath(p, args.src)
            name, _ext = os.path.splitext(os.path.join(args.out, rel))
        jobs.append((p, name + ".jpg", dt.strftime("%Y-%m-%d %H:%M:%S"),
                     args.location, args.remark, position, wm_scale,
                     remark_label, location_label))

    os.makedirs(args.out, exist_ok=True)
    csv_path = args.csv or os.path.join(args.out, "_时间对照表.csv")
    done, errors, rows = 0, [], []
    workers = max(1, int(workers))

    if workers == 1:
        for j in jobs:
            r = process_one(j)
            rows.append(r)
            done += 1
            if r[2]:
                errors.append(r)
            if done % 200 == 0:
                log(f"进度: {done}/{len(jobs)}")
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(process_one, jobs, chunksize=8):
                rows.append(r)
                done += 1
                if r[2]:
                    errors.append(r)
                if done % 200 == 0:
                    log(f"进度: {done}/{len(jobs)}")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["文件名", "水印时间", "备注"])
        for name, t, err in rows:
            w.writerow([name, t, ("失败: " + err) if err else ""])

    log(f"完成: 成功 {len(rows) - len(errors)} 张, 失败 {len(errors)} 张")
    log(f"时间对照表: {csv_path}")
    for e in errors[:10]:
        log(f"  失败 {e[0]}: {e[2]}")

    # 双击 exe 运行时，跑完不立刻关窗口
    if getattr(sys, "frozen", False):
        try:
            if sys.stdin and sys.stdin.isatty():
                input("\n按回车键退出...")
        except Exception:
            pass


if __name__ == "__main__":
    multiprocessing.freeze_support()
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log(f"[×] 运行出错：{type(e).__name__}: {e}")
        if getattr(sys, "frozen", False):
            try:
                if sys.stdin and sys.stdin.isatty():
                    input("\n按回车键退出...")
            except Exception:
                pass
        sys.exit(1)
