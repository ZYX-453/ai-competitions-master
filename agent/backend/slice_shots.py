# -*- coding: utf-8 -*-
"""把 Edge 截下来的超长图切成可读的段，并裁掉底部空白。

【为什么需要这个】
Edge --screenshot 只能按给定视口截，给 5200 高度就得到 2880x10400 的图。
这种尺寸交给任何看图工具，都会被整体缩小到看不清字 —— 等于没截。
真正要看的是"文字写了什么"，所以必须：

  1. 裁掉底部空白（深色主题下，空白是接近纯黑的一片，
     第一版我按"白色背景"去判断，于是一行都没裁掉）。
  2. 纵向切段：每段控制在高度的 1/4 左右，再单独放大看，
     这样字号回到正常可读范围。

【深色主题的判据】
背景色约 #0d1117 这类，单像素 RGB 和 ~40。
"有内容"的行会出现更亮的像素（文字/卡片边框），
所以判据是"这一行有没有明显亮于背景的像素"。
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent / "_tmp" / "shots"


def content_bottom(im: Image.Image) -> int:
    """找到最后一行有内容的像素行。"""
    w, h = im.size
    g = im.convert("L")
    px = g.load()
    # 从上往下每 8 像素取一列，够快也够准
    xs = range(0, w, 8)
    for y in range(h - 1, -1, -1):
        # 背景亮度约 15-25；文字/边框 > 45
        if any(px[x, y] > 45 for x in xs):
            return y
    return h - 1


def main() -> int:
    names = sys.argv[1:] or [p.stem for p in OUT.glob("*.png")
                             if not p.stem.endswith(("_trim", "_s1", "_s2", "_s3", "_s4"))]
    for name in names:
        src = OUT / f"{name}.png"
        if not src.exists():
            print(f"跳过（不存在）{src.name}")
            continue
        im = Image.open(src).convert("RGB")
        w, h = im.size
        bottom = content_bottom(im)
        im = im.crop((0, 0, w, min(h, bottom + 40)))
        w, h = im.size
        # 缩到 1440 宽（1 倍图），字号回到真实视觉大小
        im = im.resize((1440, int(h * 1440 / w)), Image.LANCZOS)
        w, h = im.size
        # 切成 4 段，每段约 1000px 高，看图时不用再缩
        seg = max(700, h // 4)
        n = 0
        for i in range(0, h, seg):
            part = im.crop((0, i, w, min(h, i + seg)))
            n += 1
            dst = OUT / f"{name}_s{n}.png"
            part.save(dst, quality=95)
            print(f"{dst.name}  {part.size[0]}x{part.size[1]}")
        print(f"  {name}: 原始 10400 -> 有效 {h}，切 {n} 段")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())