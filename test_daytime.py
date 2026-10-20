# -*- coding: utf-8 -*-
# 验证：时间严格限制在每天 8:30~18:00；备注标签自定义；宽度默认 40%
import inspect
from datetime import datetime

from watermarker import (generate_times, usable_minutes, daytime_segments,
                         draw_watermark, clean_label)
from PIL import Image, ImageDraw

ok = True


def check(name, cond, detail=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + ("  " + detail if detail else ""))
    if not cond:
        ok = False


def in_day(dt):
    h = dt.hour + dt.minute / 60.0
    return (8.5 <= h <= 18.0)


# 用例1：跨天（含夜间起止），时间必须全部落在每天 8:30~18:00
start = datetime(2026, 5, 6, 7, 0)    # 早于 8:30
end = datetime(2026, 5, 11, 20, 29)   # 晚于 18:00（截图中的情况）
times, eff = generate_times(60, start, end, 3, 25, seed=42)
check("60张跨6天 全部在8:30~18:00", all(in_day(t) for t in times))
check("时间严格递增", all(a < b for a, b in zip(times, times[1:])))
check("首张不早于开始日", times[0] >= start and times[0].day == 6)
check("末张不晚于结束", times[-1] <= end)
print("   样例:", [t.strftime("%m-%d %H:%M") for t in times[:5]], "...",
      times[-1].strftime("%m-%d %H:%M"))

# 用例2：开始时间在晚上（20:00），首张应落到次日 8:30
times2, _ = generate_times(10, datetime(2026, 5, 6, 20, 0), datetime(2026, 5, 9, 18, 0), 3, 25)
check("晚间开始→首张为次日8:30后", all(in_day(t) for t in times2) and times2[0] >= datetime(2026, 5, 7, 8, 30),
      times2[0].strftime("%m-%d %H:%M"))

# 用例3：单日
times3, _ = generate_times(30, datetime(2026, 5, 6, 8, 30), datetime(2026, 5, 6, 18, 0), 3, 25)
check("单日30张 全部在窗口内", all(in_day(t) for t in times3))
check("单日首张=8:30", times3[0].strftime("%H:%M") == "08:30", times3[0].strftime("%H:%M"))

# 用例4：可用分钟数
mins = usable_minutes(datetime(2026, 5, 6, 7, 0), datetime(2026, 5, 8, 23, 0))
check("3天可用时长=3×9.5小时", abs(mins - 3 * 570) < 1, f"{mins:.0f} 分钟")

# 用例5：备注标签
check("两字标签清洗", clean_label("巡 检", "备注") == "巡检")
check("三字标签原样", clean_label("巡检人", "备注") == "巡检人")
check("空标签回退默认", clean_label("", "备注") == "备注")

# 用例6：默认宽度 40%
sig = inspect.signature(draw_watermark)
check("draw_watermark 内容宽16字", sig.parameters["value_chars"].default == 16)
check("remark_label 参数存在", "remark_label" in sig.parameters)

# 用例7：自定义标签渲染
img = Image.new("RGB", (1200, 900), (120, 150, 175))
ImageDraw.Draw(img).rectangle([200, 300, 900, 700], fill=(90, 110, 130))
out = draw_watermark(img, datetime(2026, 5, 12, 17, 58), "阳塘安居小区隆兴六里",
                     "维保单位：银江智慧", "左下", 75, remark_label="巡检人")
check("自定义标签渲染不报错", out.size == (1200, 900))
out.save("pack_build/style_preview/自定义标签预览.jpg", quality=92)
print("   预览已保存: pack_build/style_preview/自定义标签预览.jpg")

print("\n===", "全部通过" if ok else "存在失败项", "===")
raise SystemExit(0 if ok else 1)
