# -*- coding: utf-8 -*-
"""
根据两次标签之间的提交记录，生成中文分类 changelog（Markdown）。

用法（在 CI 中通过环境变量传入，或命令行参数）：
    python .github/scripts/gen_changelog.py --from v1.0.4 --to v1.0.5 --out CHANGELOG_BODY.md

分类规则按 Conventional Commits 的 type 映射到中文小节：
    feat     新功能
    fix      问题修复
    perf     性能优化
    refactor 重构
    docs     文档
    test     测试
    ci       构建与发布
    build    构建与发布
    chore    其他改动
    style    代码风格
其余未匹配的一律归入「其他改动」。
"""
import argparse
import os
import re
import subprocess
import sys

# type -> (中文标题, 排序权重)
CATEGORY = {
    "feat": ("新功能", 10),
    "fix": ("问题修复", 20),
    "perf": ("性能优化", 30),
    "refactor": ("重构", 40),
    "docs": ("文档", 50),
    "test": ("测试", 60),
    "ci": ("构建与发布", 70),
    "build": ("构建与发布", 70),
    "chore": ("其他改动", 80),
    "style": ("代码风格", 90),
}
OTHER = ("其他改动", 99)

# feat(ci): xxx / feat: xxx / feat!: xxx / fix(scope)!: xxx
SUBJECT_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<breaking>!)?:\s*(?P<desc>.+)$")


def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {r.stderr.strip()}")
    return r.stdout.strip()


def resolve_range(frm, to):
    """返回 git log 的提交区间。from 为空时取 to 之前的最近一个标签。"""
    if frm:
        return f"{frm}..{to}"
    tags = git("tag", "--sort=-creatordate").splitlines()
    # 排除目标标签自身，取最近的另一个标签
    prev = [t for t in tags if t != to]
    return f"{prev[0]}..{to}" if prev else to


def parse_commit(sha, subject):
    m = SUBJECT_RE.match(subject)
    if not m:
        return None, OTHER[0], 0, False, subject
    typ = m.group("type").lower()
    scope = (m.group("scope") or "").strip()
    desc = m.group("desc").strip()
    breaking = bool(m.group("breaking"))
    name, weight = CATEGORY.get(typ, OTHER)
    # 去掉描述开头与分类重复的动词，避免出现「问题修复 / 修复 xxx」的冗余
    for prefix in ("修复", "修正", "fix: ", "Fix: "):
        if desc.startswith(prefix) and len(desc) > len(prefix):
            desc = desc[len(prefix):].lstrip("：: ")
            break
    # scope 与 type 同属「构建/CI」语义时不再重复展示（如 fix(ci) 已归入问题修复）
    show_scope = bool(scope) and not (scope.lower() in ("ci", "build") and typ in ("ci", "build", "fix"))
    text = f"〔{scope}〕{desc}" if show_scope else desc
    return typ, name, weight, breaking, text


def collect(rng):
    raw = git("log", rng, "--pretty=format:%h%x1f%s%x1f%b%x1e")
    items = []
    for rec in raw.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        parts = rec.split("\x1f")
        if len(parts) < 2:
            continue
        sha, subject = parts[0].strip(), parts[1].strip()
        body = parts[2] if len(parts) > 2 else ""
        typ, cat, weight, breaking, text = parse_commit(sha, subject)
        is_breaking = breaking or "BREAKING CHANGE" in body.upper()
        items.append({
            "sha": sha, "cat": cat, "weight": weight,
            "text": text, "breaking": is_breaking,
        })
    return items


def build_markdown(items, tag, repo):
    lines = []
    groups = {}
    for it in items:
        groups.setdefault(it["cat"], []).append(it)

    order = sorted(
        groups.items(),
        key=lambda kv: dict((v[0], v[1]) for v in CATEGORY.values()).get(kv[0], OTHER[1]),
    )

    if not items:
        lines.append("本次发布无代码变更。")
    else:
        breaking = [i for i in items if i["breaking"]]
        if breaking:
            lines.append("### ⚠️ 破坏性变更\n")
            for i in breaking:
                lines.append(f"- {i['text']} (`{i['sha']}`)")
            lines.append("")

        for cat, its in order:
            lines.append(f"### {cat}\n")
            for i in its:
                lines.append(f"- {i['text']} (`{i['sha']}`)")
            lines.append("")

    lines.append("### 使用说明\n")
    lines.append("- 系统要求：**32 位 / 64 位 Windows**（无需安装 Python）")
    lines.append("- 下载下方 `inspection-watermark-win32.zip`，解压后双击 `巡检加水印.exe` 运行")
    lines.append("- 详细操作见包内 `使用说明.txt`")
    return "\n".join(lines).strip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default=os.environ.get("PREV_TAG", ""))
    ap.add_argument("--to", dest="to", default=os.environ.get("NEW_TAG", ""))
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--out", default="CHANGELOG_BODY.md")
    a = ap.parse_args()

    if not a.to:
        print("缺少 --to 或 NEW_TAG", file=sys.stderr)
        return 2

    rng = resolve_range(a.frm, a.to)
    items = collect(rng)
    md = build_markdown(items, a.to, a.repo)

    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"区间: {rng}  提交数: {len(items)}  输出: {a.out}")
    print("-" * 40)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
