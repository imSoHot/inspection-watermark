# -*- coding: utf-8 -*-
# 重新打包安装包 zip（排除历史遗留的嵌套旧文件夹）
import os, zipfile

root = os.path.join("安装包")
out = "巡检照片加水印工具_安装包.zip"
EXCLUDE_PREFIX = os.path.join(root, "巡检照片加水印工具_安装包")  # 旧嵌套文件夹

if os.path.exists(out):
    os.remove(out)

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.startswith(EXCLUDE_PREFIX):
            dirnames[:] = []
            continue
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if p.startswith(EXCLUDE_PREFIX):
                continue
            # relpath 已含 "巡检照片加水印工具/" 前缀，不再重复拼接
            # （之前多拼一层导致解压出两个同名文件夹）
            arc = os.path.relpath(p, root)
            z.write(p, arc)
            print("+", arc)

print("完成:", out, f"{os.path.getsize(out)/1024/1024:.1f} MB")
