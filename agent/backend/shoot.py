# -*- coding: utf-8 -*-
"""用本机 Edge 无头模式给真实页面截图，核对"用户到底看到什么"。

【为什么必须做这一步】
这两轮投诉的起因都是**截图**，不是日志。
后端返回的 JSON 里没有"样本不足"四个字，不等于屏幕上没有 ——
它可能藏在表格的 note 里、藏在 caveats 里、藏在徽章里。
只有真的把页面画出来看一眼，才知道改完了没有。

【方法】
前端支持 ?q=问题&pace=0 直接进入某个问题的结果态，
所以不需要驱动点击，只要打开对应 URL 截图即可。
Edge 的 --screenshot 只能截视口，长页面会被裁掉，
因此先给一个很大的 --window-size，再用 Pillow 按内容裁白边。
"""
from __future__ import annotations

import subprocess
import sys
import urllib.parse
from pathlib import Path

from PIL import Image

EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
BASE = "http://127.0.0.1:8848/"
OUT = Path(__file__).resolve().parent / "_tmp" / "shots"

# 只截这几条：前两条是用户投诉的原话，后两条是边界与对照
CASES = [
    ("digital_w52", "数码类目第52周为什么退货率高"),
    ("greeting", "你好你是谁"),
    ("apparel_w52", "服饰类目第52周为什么退货率高"),
    ("promo_w44", "第44周退货率为什么涨了"),
]

# 视口给足高度：结果态一张卡片通常 1500~3000px。
# 给 5200 是为了让 90% 的情况一次截全，超出的再靠裁剪兜底。
W, H = 1440, 5200


def shoot(name: str, question: str) -> Path | None:
    url = BASE + "?" + urllib.parse.urlencode({"q": question, "pace": "0"})
    raw = OUT / f"{name}.png"
    cmd = [
        EDGE,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=2",  # 2 倍图，字号看清才谈得上核对
        "--virtual-time-budget=9000",     # 等 SSE 跑完；pace=0 时整条流一次推完
        f"--window-size={W},{H}",
        f"--screenshot={raw}",
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    if not raw.exists():
        print(f"  截图失败：{r.stderr.decode('utf-8', 'ignore')[:200]}")
        return None
    return trim(raw, OUT / f"{name}_trim.png")


def trim(src: Path, dst: Path) -> Path:
    """裁掉底部大片空白，否则每张图都拖着几千像素的白。"""
    im = Image.open(src).convert("RGB")
    w, h = im.size
    px = im.load()
    # 从底部往上扫，找到第一行"不是纯背景色"的行
    last = h - 1
    while last > 0:
        row = [px[x, last] for x in range(0, w, 17)]
        # 允许轻微色差：只要这一行有明显的非背景像素就停
        if any(sum(c) < 720 for c in row):
            break
        last -= 1
    if last < h - 1:
        im = im.crop((0, 0, w, min(h, last + 60)))
        im.save(dst)
        return dst
    return src


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not Path(EDGE).exists():
        print(f"找不到 Edge：{EDGE}")
        return 1
    ok = 0
    for name, q in CASES:
        print(f"[{name}] {q}")
        p = shoot(name, q)
        if p:
            ok += 1
            print(f"  -> {p}")
    print(f"\n完成 {ok}/{len(CASES)} 张")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())