# -*- coding: utf-8 -*-
"""
巡检照片批量加水印 · 图形界面版（本地网页 UI，无需编辑 json）

启动后在浏览器里操作：选文件夹、选时间段、填地点备注，点"开始处理"。
依赖仅 Python 标准库 + Pillow + 本目录的 watermarker.py 核心模块。
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from watermarker import (BASE_DIR, collect_images, draw_watermark,
                         generate_times, normalize_position, process_one,
                         usable_minutes, IMG_EXTS)
from PIL import Image, ImageOps

SETTINGS_FILE = os.path.join(BASE_DIR, "界面设置.json")
LOG_LIMIT = 400

DEFAULT_SETTINGS = {
    "src": "",
    "out": "",
    "start": datetime.now().strftime("%Y-%m-%d 08:30"),
    "end": datetime.now().strftime("%Y-%m-%d 18:00"),
    "min_gap": 3,
    "max_gap": 25,
    "location": "",
    "location_label": "巡检位置",
    "remark": "厦门银江智慧城市技术股份有限公司",
    "remark_label": "维保单位",
    "position": "左下",
    "wm_scale": 50,
    "workers": 6,
    "seed": 42,
}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    for f in ("界面设置.json", "config.json"):
        p = os.path.join(BASE_DIR, f)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8-sig") as fh:
                    raw = fh.read()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    data = json.loads(raw.replace("\\", "/"))
                # 兼容 config.json 的键名
                alias = {"min_gap_minutes": "min_gap", "max_gap_minutes": "max_gap",
                         "wm_width_pct": "wm_width", "wm_scale_pct": "wm_scale"}
                for k, v in data.items():
                    s[alias.get(k, k)] = v
            except Exception:
                pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class Task:
    """一次批量处理任务的状态机"""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.cancel = threading.Event()
        self.total = 0
        self.done = 0
        self.ok = 0
        self.failed = 0
        self.logs = []
        self.error = ""
        self.result = {"out": "", "csv": "", "times": []}

    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self.lock:
            self.logs.append(line)
            if len(self.logs) > LOG_LIMIT:
                self.logs = self.logs[-LOG_LIMIT:]

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "total": self.total,
                "done": self.done,
                "ok": self.ok,
                "failed": self.failed,
                "logs": list(self.logs[-LOG_LIMIT:]),
                "error": self.error,
                "out": self.result["out"],
                "csv": self.result["csv"],
            }


TASK = Task()


def normalize_settings(raw):
    s = dict(DEFAULT_SETTINGS)
    s.update({k: v for k, v in (raw or {}).items() if k in DEFAULT_SETTINGS})
    s["src"] = str(s["src"]).strip().strip('"')
    if "|" in s["src"]:      # 多选：逐个清洗，去空项
        s["src"] = "|".join(x.strip().strip('"') for x in s["src"].split("|") if x.strip())
    s["out"] = str(s["out"]).strip().strip('"')
    for k in ("min_gap", "max_gap", "workers", "seed"):
        try:
            s[k] = float(s[k]) if k in ("min_gap", "max_gap") else int(s[k])
        except (TypeError, ValueError):
            s[k] = DEFAULT_SETTINGS[k]
    s["workers"] = max(1, min(16, int(s["workers"])))
    s["position"] = "左下"          # 位置固定左下角，不再提供选项
    try:
        s["wm_scale"] = max(0.0, min(100.0, float(s.get("wm_scale", 50))))
    except (TypeError, ValueError):
        s["wm_scale"] = 50.0
    # 两行标签：去空格限长，留空回退默认
    s["location_label"] = (str(s.get("location_label") or "").strip())[:8] or "巡检位置"
    s["remark_label"] = (str(s.get("remark_label") or "").strip())[:8] or "维保单位"
    if not s["out"]:
        first = s["src"].split("|")[0]
        s["out"] = os.path.join(os.path.dirname(os.path.abspath(first)), "加水印输出")
    return s


def validate(s):
    if not s["src"]:
        return "请先选择原始照片"
    parts = s["src"].split("|") if "|" in s["src"] else [s["src"]]
    for p in parts:
        if not os.path.exists(p):
            return f"原始照片/文件夹不存在：{p}"
        if (os.path.isfile(p)
                and os.path.splitext(p)[1].lower() not in IMG_EXTS):
            return f"不是支持的图片文件：{p}"
    fmt = "%Y-%m-%d %H:%M"
    try:
        start = datetime.strptime(s["start"], fmt)
        end = datetime.strptime(s["end"], fmt)
    except ValueError:
        return "时间格式应为 2026-05-06 08:30"
    if end <= start:
        return "结束时间必须晚于开始时间"
    if os.path.abspath(s["out"]) == os.path.abspath(s["src"]):
        return "输出文件夹不能和原始照片文件夹相同"
    return ""


def build_jobs(s):
    files = collect_images(s["src"])
    fmt = "%Y-%m-%d %H:%M"
    times, eff = generate_times(len(files), datetime.strptime(s["start"], fmt),
                                datetime.strptime(s["end"], fmt),
                                s["min_gap"], s["max_gap"], s["seed"])
    jobs = []
    # 文件夹输入保持子目录结构；单个/多个文件输入平铺到输出目录
    flat = ("|" in s["src"]) or os.path.isfile(s["src"])
    for p, dt in zip(files, times):
        if flat:
            name, _ext = os.path.splitext(os.path.join(s["out"], os.path.basename(p)))
        else:
            rel = os.path.relpath(p, s["src"])
            name, _ext = os.path.splitext(os.path.join(s["out"], rel))
        jobs.append((p, name + ".jpg", dt.strftime("%Y-%m-%d %H:%M:%S"),
                     s["location"], s["remark"], s["position"],
                     s["wm_scale"], s.get("remark_label", "维保单位"),
                     s.get("location_label", "巡检位置")))
    return jobs, times, files, eff


def run_batch(s):
    t = TASK
    with t.lock:
        t.running = True
        t.cancel.clear()
        t.total = t.done = t.ok = t.failed = 0
        t.logs = []
        t.error = ""
    try:
        t.log("开始处理…")
        t.log(f"时间段：{s['start']} ~ {s['end']}（每天仅在 8:30~18:00 生成）")
        t.log(f"间隔参考 {s['min_gap']}~{s['max_gap']} 分钟　"
              f"水印固定左下　缩放 {s['wm_scale']:.0f}%　内容宽 16 字/行")
        jobs, times, files, eff = build_jobs(s)
        if not files:
            t.log("[×] 未找到照片，请检查文件夹")
            with t.lock:
                t.error = "未找到照片"
            return
        with t.lock:
            t.total = len(jobs)
        t.log(f"共发现 {len(jobs)} 张照片，平均间隔约 {eff:.1f} 分钟/张，输出到：{s['out']}")
        if len(jobs) > 1 and eff < s["min_gap"] - 0.1:
            t.log(f"[!] 时间跨度不够：按最小间隔 {s['min_gap']} 分钟排不下 {len(jobs)} 张，"
                  f"已自动压缩为约 {eff:.1f} 分钟/张。建议把时间段拉长一些。")
        elif eff > s["max_gap"] + 0.1:
            t.log(f"[!] 时间跨度较大：已按约 {eff:.1f} 分钟/张均匀铺满整个时间段。")
        os.makedirs(s["out"], exist_ok=True)
        csv_path = os.path.join(s["out"], "_时间对照表.csv")

        rows = []
        workers = max(1, int(s["workers"]))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(process_one, j): j for j in jobs}
            for fu in as_completed(futs):
                if t.cancel.is_set():
                    for f in futs:
                        f.cancel()
                    t.log("[!] 已停止，剩余照片未处理")
                    break
                r = fu.result()
                rows.append(r)
                with t.lock:
                    t.done += 1
                    if r[2]:
                        t.failed += 1
                    else:
                        t.ok += 1
                    d, tot = t.done, t.total
                if d % 100 == 0 or d == tot:
                    t.log(f"进度 {d}/{tot}")

        if rows and not t.cancel.is_set():
            import csv as _csv
            rows.sort(key=lambda x: x[0])
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                w = _csv.writer(f)
                w.writerow(["文件名", "水印时间", "备注"])
                for name, ts, err in rows:
                    w.writerow([name, ts, ("失败: " + err) if err else ""])
            with t.lock:
                t.result = {"out": s["out"], "csv": csv_path,
                            "times": [[r[0], r[1]] for r in rows[:500]]}
            t.log(f"完成：成功 {t.ok} 张，失败 {t.failed} 张")
            t.log(f"时间对照表：{csv_path}")
        else:
            t.log(f"已停止：已处理 {t.done} 张")
    except Exception as e:
        import traceback
        t.log(f"[×] 出错：{type(e).__name__}: {e}")
        t.log(traceback.format_exc(limit=3))
        with t.lock:
            t.error = f"{type(e).__name__}: {e}"
    finally:
        with t.lock:
            t.running = False


def make_preview(s):
    """生成第一张照片的效果预览，返回预览图路径"""
    files = collect_images(s["src"])
    if not files:
        raise RuntimeError("未找到照片")
    fmt = "%Y-%m-%d %H:%M"
    dt = datetime.strptime(s["start"], fmt)
    img = ImageOps.exif_transpose(Image.open(files[0]))
    out_img = draw_watermark(img, dt, s["location"], s["remark"], "左下",
                             s.get("wm_scale", 75),
                             remark_label=s.get("remark_label", "维保单位"),
                             location_label=s.get("location_label", "巡检位置"))
    out_dir = s["out"] or os.path.join(BASE_DIR, "预览")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "_预览效果.jpg")
    out_img.save(path, "JPEG", quality=92)
    return path


def pick_dialog(desc="请选择", mode="folder"):
    """调用系统选择框（PowerShell WinForms）：
    mode=folder 选文件夹；mode=files 多选文件。
    多选模式下：选中文件返回文件；什么都不选直接点"打开"，
    会返回"当前文件夹+占位名"，此时取其所在目录即当前打开的文件夹。
    多个结果用 | 连接（Windows 路径不含 |）。"""
    if mode == "files":
        ps = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.OpenFileDialog;"
            f"$d.Title='{desc}';"
            "$d.Filter='图片文件|*.jpg;*.jpeg;*.png;*.bmp;*.webp|所有文件|*.*';"
            "$d.Multiselect=$true;"
            "$d.CheckFileExists=$false;$d.CheckPathExists=$true;"
            "$d.ValidateNames=$false;$d.AddExtension=$false;"
            "$d.FileName='（不选文件=使用当前文件夹）';"
            "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){Write-Output ($d.FileNames -join '|')}"
        )
    else:
        ps = (
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            f"$d.Description='{desc}';"
            "$d.ShowNewFolderButton=$true;"
            "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK){Write-Output $d.SelectedPath}"
        )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                           capture_output=True, timeout=300)
        out = r.stdout.decode("utf-8", "replace").strip()
        if mode == "files" and out:
            # 修正：不存在的项 = "当前文件夹+占位名" → 取其所在文件夹；并去重
            fixed, seen = [], set()
            for x in out.split("|"):
                x = x.strip()
                if not x:
                    continue
                if not os.path.exists(x):
                    parent = os.path.dirname(x)
                    if not os.path.isdir(parent):
                        continue
                    x = parent
                if x not in seen:
                    seen.add(x)
                    fixed.append(x)
            out = "|".join(fixed)
        return out
    except Exception:
        return ""


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>巡检照片批量加水印</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family:"Microsoft YaHei UI","Microsoft YaHei",system-ui,sans-serif;
         background:#f4f6fa; color:#1f2430; }
  header { background:linear-gradient(135deg,#1e6ce0,#2f86f6); color:#fff;
           padding:18px 28px; box-shadow:0 2px 10px rgba(30,108,224,.25); }
  header h1 { margin:0; font-size:19px; font-weight:600; letter-spacing:.5px;}
  header p { margin:6px 0 0; font-size:12.5px; opacity:.9; }
  .wrap { max-width:960px; margin:20px auto 60px; padding:0 18px; }
  .card { background:#fff; border-radius:12px; padding:18px 20px 20px;
          box-shadow:0 1px 3px rgba(20,40,80,.08); margin-bottom:16px; }
  .card h2 { font-size:14px; margin:0 0 14px; color:#1e6ce0; font-weight:600;
             display:flex; align-items:center; gap:8px;}
  .card h2::before { content:""; width:3px; height:14px; background:#1e6ce0; border-radius:2px;}
  .row { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
  .field { flex:1 1 300px; min-width:220px; display:flex; flex-direction:column; gap:6px; }
  .field.sm { flex:0 1 150px; min-width:118px; }
  label { font-size:12.5px; color:#5a6478; }
  input { border:1px solid #d5dbe6; border-radius:8px; padding:9px 11px; font-size:13.5px;
          font-family:inherit; color:#1f2430; background:#fbfcfe; outline:none; transition:.15s;}
  input:focus { border-color:#1e6ce0; background:#fff; box-shadow:0 0 0 3px rgba(30,108,224,.12);}
  .with-btn { display:flex; gap:8px; }
  .with-btn input { flex:1; }
  button { font-family:inherit; font-size:13.5px; border:none; border-radius:8px;
           padding:9px 16px; cursor:pointer; transition:.15s; white-space:nowrap;}
  .ghost { background:#eef3fb; color:#1e6ce0; border:1px solid #d7e3f7;}
  .ghost:hover { background:#e2ebfa; }
  .primary { background:#1e6ce0; color:#fff; padding:11px 26px; font-size:14.5px;
             box-shadow:0 3px 10px rgba(30,108,224,.3);}
  .primary:hover { background:#1a5fc9; }
  .primary:disabled { background:#b9c6da; box-shadow:none; cursor:not-allowed;}
  .danger { background:#fff1f0; color:#d4380d; border:1px solid #ffccc7;}
  .danger:hover { background:#ffe4e0; }
  .bar { height:10px; background:#e8edf6; border-radius:6px; overflow:hidden; margin:10px 0 8px;}
  .bar > i { display:block; height:100%; width:0; border-radius:6px;
             background:linear-gradient(90deg,#1e6ce0,#48a3ff); transition:width .3s;}
  .status { font-size:13px; color:#5a6478; display:flex; justify-content:space-between;}
  pre { background:#0f172a; color:#cfd8e6; font-size:12px; line-height:1.65; padding:12px 14px;
        border-radius:8px; height:190px; overflow:auto; margin:12px 0 0;
        font-family:Consolas,"Courier New",monospace; white-space:pre-wrap;}
  .hint { font-size:12px; color:#8b95a7; margin-top:6px; line-height:1.6;}
  .tip { font-size:12.5px; color:#8b6d00; background:#fffbe6; border:1px solid #ffe58f;
         padding:9px 12px; border-radius:8px; margin-top:4px; display:none;}
  .posgrid { display:grid; grid-template-columns:repeat(3,86px); grid-auto-rows:44px;
             gap:8px; margin-top:6px; }
  .posgrid button { background:#f7f9fd; color:#6b7688; border:1px solid #dde4f0;
                    border-radius:8px; font-size:12.5px; padding:0;}
  .posgrid button:hover { background:#eaf1fc; color:#1e6ce0; }
  .posgrid button.on { background:#1e6ce0; color:#fff; border-color:#1e6ce0;
                       box-shadow:0 3px 8px rgba(30,108,224,.28); font-weight:600;}
  .sliderow { display:flex; align-items:center; gap:10px; }
  .sliderow input[type=range] { flex:1; padding:0; border:none; background:transparent;
                                box-shadow:none; accent-color:#1e6ce0; height:22px; }
  .sliderow input[type=range]:focus { box-shadow:none; }
  .sval { font-size:12.5px; color:#1e6ce0; font-weight:600; min-width:44px; text-align:right; }
  .actions { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  .tag { font-size:12px; color:#5a6478; background:#eef3fb; border-radius:20px; padding:4px 12px;}
  .labedit-row { display:flex; align-items:center; gap:2px; }
  .labedit { flex:0 0 64px; border:1px dashed #c9d2e2; border-radius:6px; padding:2px 6px;
             font-size:12.5px; color:#5a6478; background:transparent; }
  .labedit:focus { border-color:#1e6ce0; background:#fff; box-shadow:none; }
</style>
</head>
<body>
<header>
  <h1>设备巡检维护 · 照片批量加水印</h1>
  <p>时间在指定时间段内随机连续生成，整体递增、有规律</p>
</header>
<div class="wrap">
  <div class="card">
    <h2>文件夹</h2>
    <div class="row">
      <div class="field">
        <label>原始照片文件夹</label>
        <div class="with-btn">
          <input id="src" placeholder="可多选文件，或粘贴文件/文件夹路径（多个用 | 分隔）">
          <button class="ghost" onclick="pick('src','请选择原始照片：可按住 Ctrl 多选；不选文件直接点“打开”= 使用当前打开的文件夹','files')">选择文件…</button>
        </div>
      </div>
    </div>
    <div class="row">
      <div class="field">
        <label>输出文件夹（成品保存位置，可留空自动创建）</label>
        <div class="with-btn">
          <input id="out" placeholder="留空则在照片文件夹同级新建「加水印输出」">
          <button class="ghost" onclick="pick('out','请选择输出文件夹')">选择…</button>
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <h2>巡检时间</h2>
    <div class="row">
      <div class="field"><label>时间段开始</label><input id="start" type="datetime-local"></div>
      <div class="field"><label>时间段结束</label><input id="end" type="datetime-local"></div>
    </div>
    <div class="row">
      <div class="field sm"><label>最小间隔（分钟）</label><input id="min_gap" type="number" min="0.5" step="1"></div>
      <div class="field sm"><label>最大间隔（分钟）</label><input id="max_gap" type="number" min="1" step="1"></div>
      <div class="field sm"><label>随机种子</label><input id="seed" type="number" step="1"></div>
      <div class="field sm"><label>并行线程</label><input id="workers" type="number" min="1" max="16" step="1"></div>
    </div>
    <div class="hint">每张照片的时间 = 上一张的时间 + 随机间隔，且严格限制在每天的
      8:30~18:00 之间——即使开始/结束时间跨天，夜间也绝不会生成时间。
      想换一套时间顺序，改一下「随机种子」的数字即可。</div>
    <div class="row" style="margin-top:12px; align-items:center;">
      <button class="ghost" onclick="scan()">扫描照片数量 / 预估间隔</button>
      <span class="tag" id="scanTag">尚未扫描</span>
    </div>
    <div class="tip" id="scanTip"></div>
  </div>

  <div class="card">
    <h2>水印内容</h2>
    <div class="row">
      <div class="field"><label class="labedit-row">
          <input id="location_label" class="labedit" maxlength="8" placeholder="地点"
                 title="这一栏的标题，会显示在水印上，可直接修改">
          <span style="color:#b0b8c6;">（可编辑）</span></label>
        <input id="location"></div>
      <div class="field"><label class="labedit-row">
          <input id="remark_label" class="labedit" maxlength="8" placeholder="备注"
                 title="这一栏的标题，会显示在水印上，可直接修改">
          <span style="color:#b0b8c6;">（可编辑）</span></label>
        <input id="remark"></div>
    </div>
    <div class="row" style="margin-top:2px;">
      <div class="field sm" style="flex:0 1 260px;">
        <div class="sliderow">
          <input id="wm_scale" type="range" min="0" max="100" step="5" value="50"
                 oninput="document.getElementById('wm_scale_v').textContent=this.value+'%'">
          <span class="sval" id="wm_scale_v">50%</span>
        </div>
      </div>
    </div>
    <div class="hint">水印固定显示在左下角；内容区固定 16 个中文字的宽度，
      超过 16 字自动换行（最多 4 行）。两行标签分散对齐，点上面的小方框可改标签文字。</div>
  </div>

  <div class="card">
    <h2>执行</h2>
    <div class="actions">
      <button class="primary" id="btnRun" onclick="run()">开始处理</button>
      <button class="ghost" id="btnStop" onclick="stop()" disabled>停止</button>
      <button class="ghost" onclick="preview()">预览第 1 张效果</button>
      <button class="ghost" onclick="openOut()">打开输出文件夹</button>
      <button class="danger" onclick="exitApp()">退出程序</button>
      <span class="tag" id="tag">就绪</span>
    </div>
    <div class="tip" id="tip"></div>
    <div class="bar"><i id="barIn"></i></div>
    <div class="status">
      <span id="progress">等待开始</span>
      <span id="counts"></span>
    </div>
    <pre id="log">准备就绪。</pre>
  </div>
</div>

<script>
const FIELDS = ["src","out","start","end","min_gap","max_gap","location","location_label","remark","remark_label","wm_scale","workers","seed"];

// 位置固定左下角，无需选择
function initLabelInputs() {
  ["location_label", "remark_label"].forEach(id => {
    const el = document.getElementById(id);
    if (el && !el.value) el.value = id === "location_label" ? "巡检位置" : "维保单位";
  });
}

function toLocalInput(v) {
  // "2026-05-06 08:30" -> "2026-05-06T08:30"
  return (v || "").replace(" ", "T").slice(0, 16);
}
function fromLocalInput(v) {
  return (v || "").replace("T", " ").slice(0, 16);
}
function collect() {
  const o = {};
  FIELDS.forEach(k => { const el = document.getElementById(k);
    o[k] = el.value.trim(); });
  o.start = fromLocalInput(o.start); o.end = fromLocalInput(o.end);
  return o;
}
function fill(s) {
  FIELDS.forEach(k => {
    const el = document.getElementById(k);
    let v = s[k] === undefined || s[k] === null ? "" : s[k];
    if (k === "start" || k === "end") v = toLocalInput(v);
    el.value = v;
  });
  initLabelInputs();
  const sc = document.getElementById("wm_scale");
  document.getElementById("wm_scale_v").textContent = (sc.value || 50) + "%";
}
function tip(msg, ok) {
  const t = document.getElementById("tip");
  t.textContent = msg;
  t.style.background = ok ? "#f6ffed" : "#fffbe6";
  t.style.borderColor = ok ? "#b7eb8f" : "#ffe58f";
  t.style.color = ok ? "#237804" : "#8b6d00";
  t.style.display = msg ? "block" : "none";
}
async function api(path, body) {
  const r = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(body || {})});
  return await r.json();
}
async function init() {
  const s = await (await fetch("/api/state")).json();
  fill(s);
}
async function pick(kind, desc, mode) {
  tip(mode === "file" ? "请在弹出的系统窗口中选择照片文件…" : "请在弹出的系统窗口中选择文件夹…", true);
  const r = await api("/api/pick", {desc: desc, mode: mode || "folder"});
  tip("", true);
  if (r.path) {
    document.getElementById(kind).value = r.path;
    if (kind === "src") scan();
  }
}
async function scan() {
  const s = collect();
  const tag = document.getElementById("scanTag");
  const st = document.getElementById("scanTip");
  if (!s.src) { tag.textContent = "请先选择照片文件夹"; return; }
  tag.textContent = "扫描中…";
  const r = await api("/api/scan", s);
  if (r.error) { tag.textContent = "扫描失败"; st.textContent = r.error; st.style.display="block"; return; }
  tag.textContent = "共 " + r.count + " 张照片　平均间隔约 " + r.eff + " 分钟/张（" + (r.eff/60).toFixed(1) + " 小时）";
  if (r.warn) { st.textContent = "⚠ " + r.warn; st.style.display = "block"; }
  else { st.textContent = ""; st.style.display = "none"; }
}
async function run() {
  tip("", true);
  const r = await api("/api/start", collect());
  if (r.error) { tip("✕ " + r.error, false); return; }
  document.getElementById("btnRun").disabled = true;
  document.getElementById("btnStop").disabled = false;
  document.getElementById("tag").textContent = "处理中";
  poll();
}
async function stop() { await api("/api/stop", {}); }
async function preview() {
  tip("正在生成预览…", true);
  const r = await api("/api/preview", collect());
  if (r.error) { tip("✕ " + r.error, false); return; }
  tip("预览已生成并打开：" + r.file, true);
}
async function openOut() {  const s = collect();
  const path = s.out || (s.src ? s.src : "");
  const r = await api("/api/open", {path: path, settings: s});
  if (r.error) tip("✕ " + r.error, false);
}
async function exitApp() {
  // 直接退出：关闭后台程序 + 关闭当前网页，不再二次确认
  try { await api("/api/exit", {}); } catch (e) {}
  document.body.innerHTML =
    "<div style='padding:60px;text-align:center;font-size:15px;color:#5a6478'>" +
    "程序已退出。<br><span style='font-size:13px;color:#8b95a7'>" +
    "若本页面未自动关闭，请手动关闭该标签页。</span></div>";
  // 尝试关闭标签页（浏览器只允许脚本打开的窗口自行关闭，失败则保留提示）
  try { window.open("", "_self"); window.close(); } catch (e) {}
  setTimeout(() => { try { window.close(); } catch (e) {} }, 250);
}
let timer = null;
async function poll() {
  const st = await api("/api/status", {});
  const pct = st.total ? Math.round(st.done * 100 / st.total) : 0;
  document.getElementById("barIn").style.width = pct + "%";
  document.getElementById("progress").textContent =
    st.running ? ("处理中 " + pct + "%") : (st.total ? "已完成" : "等待开始");
  document.getElementById("counts").textContent =
    st.total ? ("已处理 " + st.done + " / " + st.total + "　成功 " + st.ok + "　失败 " + st.failed) : "";
  const log = document.getElementById("log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.textContent = st.logs.join("\n") || "准备就绪。";
  if (atBottom) log.scrollTop = log.scrollHeight;
  if (st.error) tip("✕ " + st.error, false);
  if (st.running) { timer = setTimeout(poll, 700); }
  else {
    document.getElementById("btnRun").disabled = false;
    document.getElementById("btnStop").disabled = true;
    document.getElementById("tag").textContent = st.error ? "出错" : "空闲";
  }
}
init();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "WatermarkGUI/1.0"

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, HTML, "text/html")
        elif self.path == "/api/state":
            self._json(load_settings())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        if path == "/api/pick":
            p = pick_dialog(body.get("desc") or "请选择",
                            body.get("mode") or "folder")
            self._json({"path": p})
        elif path == "/api/start":
            if TASK.snapshot()["running"]:
                self._json({"error": "已有任务在运行中"})
                return
            s = normalize_settings(body)
            err = validate(s)
            if err:
                self._json({"error": err})
                return
            save_settings(s)
            threading.Thread(target=run_batch, args=(s,), daemon=True).start()
            self._json({"ok": True})
        elif path == "/api/status":
            self._json(TASK.snapshot())
        elif path == "/api/scan":
            s = normalize_settings(body)
            bad = [x for x in s["src"].split("|") if not os.path.exists(x)]
            if bad or not s["src"]:
                self._json({"error": "原始照片/文件夹不存在，请重新选择"})
                return
            try:
                n = len(collect_images(s["src"]))
            except Exception as e:
                self._json({"error": f"扫描失败：{e}"})
                return
            fmt = "%Y-%m-%d %H:%M"
            try:
                st = datetime.strptime(s["start"], fmt)
                en = datetime.strptime(s["end"], fmt)
            except ValueError:
                self._json({"error": "时间格式应为 2026-05-06 08:30"})
                return
            # 只统计每天 8:30~18:00 的可用时长（跨天也只在白天生成）
            span_min = usable_minutes(st, en)
            eff = span_min / n if n else 0
            warn = ""
            if n and eff < s["min_gap"] - 0.1:
                warn = (f"时间跨度不够：{n} 张照片按每天 8:30~18:00 及最小间隔 "
                        f"{s['min_gap']} 分钟排不下，实际会压缩到约 {eff:.1f} 分钟/张，"
                        f"建议拉长时间段")
            elif n and eff > s["max_gap"] + 0.1:
                warn = (f"时间跨度较大：会按约 {eff:.1f} 分钟/张（{eff/60:.1f} 小时）"
                        f"均匀铺满每天的 8:30~18:00 时段")
            self._json({"count": n, "eff": round(eff, 2),
                        "days": round(span_min / 1440, 2), "warn": warn})
        elif path == "/api/stop":
            TASK.cancel.set()
            TASK.log("[!] 正在停止…")
            self._json({"ok": True})
        elif path == "/api/preview":
            s = normalize_settings(body)
            err = validate(s)
            if err:
                self._json({"error": err})
                return
            try:
                p = make_preview(s)
                try:
                    os.startfile(p)
                except Exception:
                    pass
                self._json({"file": p})
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"})
        elif path == "/api/open":
            p = (body.get("path") or "").strip().strip('"')
            if not p:
                s = normalize_settings(body.get("settings") or {})
                p = s["out"]
            if not p or not os.path.isdir(p):
                self._json({"error": "文件夹还不存在，先跑一次任务或手动选择"})
                return
            try:
                os.startfile(p)
                self._json({"ok": True})
            except Exception as e:
                self._json({"error": str(e)})
        elif path == "/api/exit":
            self._json({"ok": True})

            def _bye():
                time.sleep(0.4)
                if getattr(sys, "frozen", False):
                    # 单文件 exe 有引导进程外层，只退出自身会残留引导进程，
                    # 导致程序文件夹被占用无法删除；这里连引导进程整棵杀掉
                    try:
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(os.getppid())],
                            capture_output=True,
                            creationflags=0x08000000)  # CREATE_NO_WINDOW
                    except Exception:
                        pass
                os._exit(0)

            threading.Thread(target=_bye, daemon=True).start()
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, *a):
        pass


def pick_port(preferred=8765):
    for port in range(preferred, preferred + 40):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    no_browser = "--no-browser" in sys.argv
    port = None
    if "--port" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
        except Exception:
            port = None
    port = port or pick_port()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except Exception as e:
        report_startup_error(e)
        return
    url = f"http://127.0.0.1:{port}/"
    print(f"界面已启动：{url}")
    if not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def report_startup_error(e):
    """打包成无控制台 exe 时，启动失败留下日志并自动打开"""
    import traceback
    msg = f"程序启动失败：{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
    try:
        p = os.path.join(BASE_DIR, "启动错误.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(msg)
        os.startfile(p)
    except Exception:
        pass


if __name__ == "__main__":
    main()
