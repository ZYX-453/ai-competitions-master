# -*- coding: utf-8 -*-
"""截功能页面（不是问答页），用于核对"改完的看板到底长什么样"。

【为什么不能复用 shoot_cdp.py】
那个脚本等的是"回答区域出现 N 步 · X.Xs"——那是问答页独有的信号。
功能页面没有那个标签，拿它去等只会 90s 超时然后截一张中间态。
这里换一套判据：**等页面把数据渲染出来**。

【怎么判断"渲染完了"】
功能页的数据是进页面后异步请求的，加载中会显示 .pg-loading。
所以判据是：
  1. .pg-loading 消失（请求回来了）
  2. 主内容区有实际文字（不是骨架）
  3. 没有 .pg-fail（后端挂了会有明确的错误块，不该当成功截）

【为什么这判据比问答页简单】
问答页是流式的，要说"说完了"；功能页是一次性返回后渲染，
"不再加载中"就等于"画完了"。不需要猜中间态。

用法：
    python shoot_page.py                     # 默认截总览页（全店 + 服饰联动）
    python shoot_page.py dashboard 服饰 数码   # 指定页面与范围
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket

# 【CDP / 取页面 / 过登录 一律复用 shoot_cdp】
# 这三件事原来在本文件里各有一份实现，而 shoot_cdp 那边修了四个坑
# （登录页、高度、.glass-login 残帧、多余的 page target），这里一份都没跟上。
# 于是"问答页截图好了、工作台截图还是登录卡片"。同一件事只能有一份实现。
from shoot_cdp import (CDP, close_strays, drop_login_veil, free_port,
                       list_pages, pass_login, pick_target)

EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
BASE = "http://127.0.0.1:8848/"
# 【不再固定端口】原来写死 9334，上一个 Edge 退出滞后时会连到它身上。
# 现在每次 free_port() 要一个空闲端口（见 shoot_cdp.free_port 的说明）。
OUT = Path(__file__).resolve().parent / "_tmp" / "pages"

# 页面 key → hash 路由
HASH = {
    'dashboard': '#/dashboard',
    'attribution': '#/attribution/44',
    'reasons': '#/reasons',
    'plan': '#/plan',
    'reviews': '#/reviews',
    'competitors': '#/competitors',
}


def wait_devtools(timeout: float = 25.0) -> str:
    """【已废弃，保留只为不破坏外部调用】请用 shoot_cdp.pick_target。

    原来这里和 shoot_cdp.py 各写了一份"取第一个 page 的 ws 地址"，
    而那份实现有硬伤：DevTools 里会有第二个页面（Edge 同步确认弹窗、
    会话恢复出来的标签页），取到哪个全看 /json 的顺序。
    连错靶子时的症状是"DOM 对、像素错"，极难自证。
    两份实现 → 修一份漏一份，所以现在统一走 shoot_cdp。
    """
    return pick_target(port=free_port())["webSocketDebuggerUrl"]


def capture(name: str, page: str = 'dashboard',
            category: str | None = None) -> Path | None:
    qs = f"?category={urllib.parse.quote(category)}" if category else ""
    url = BASE + qs + HASH.get(page, '#/dashboard')

    # 【每次用独立的 profile 目录】
    # 原来所有截图共用一个 _profile，跑第二张时上一个 Edge 还没完全释放
    # 那个目录（文件锁在 Windows 上滞后），新实例起得来但页面是白的：
    # 判据里 len=0、cards=0，然后等满 60s 超时。
    # 更阴的是它不报错 —— 只是截出一张 24KB 的空白图。
    # 用**时间戳**做后缀而不是 name：同名重跑时会撞上上一次留下的目录，
    # 又回到同一个坑；时间戳保证每次都是全新的空 profile。
    profile = OUT / f"_profile_{int(time.time()*1000)}"
    # 端口也不能固定：上一个 Edge 没退干净时会连到它身上（表现为"第一次探针就 logged-in"）
    port = free_port()

    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--no-default-browser-check",
        # 【禁同步】否则 Edge 会在运行中途弹 edge://sync-confirmation-dialog/，
        # DevTools 里就多出一个 page，截图可能取到它（症状：DOM 对、像素错）
        "--disable-sync", "--disable-background-networking",
        f"--remote-debugging-port={port}",
        "--user-data-dir=" + str(profile),
        "--window-size=1440,1200",
        url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        tab = pick_target("http://127.0.0.1", port=port)
        killed = close_strays(tab["id"], port=port)
        if killed:
            print(f"  · 已关掉 {len(killed)} 个多余页面")
            time.sleep(0.4)
        c = CDP(tab["webSocketDebuggerUrl"])
        c.send("Page.enable")
        c.send("Runtime.enable")

        # 工作台七页同样在登录页后面 —— 不代点登录，判据永远等不到 .pg-card，
        # 满 60s 超时后截一张登录卡片。这一步以前整段缺失。
        pass_login(c)
        drop_login_veil(c)

        t0 = time.time()
        ready = False
        st = {}
        while time.time() - t0 < 60:
            st = c.js("""(() => {
              const q = s => document.querySelector(s);
              const txt = (q('main') || document.body).innerText || '';
              return {
                loading: !!q('.pg-loading') || !!q('.pg-boot'),
                fail: q('.pg-fail') ? q('.pg-fail').innerText.slice(0, 120) : '',
                len: txt.length,
                cards: document.querySelectorAll('.pg-card').length,
                kpis: document.querySelectorAll('.pg-stat').length,
                bars: document.querySelectorAll('.bi-bar-row').length,
                // 【两页的热力格类名不一样，都要数】
                // 总览页是 .bi-heat-c（BiKit 的矩阵），原因治理页是 .pg-heat-td。
                // 只数前者的话，原因页会报"热力格 0"——矩阵明明画好了，
                // 日志却说 0，读起来像没渲染出来，白排查一轮。
                heat: document.querySelectorAll('.bi-heat-c, .pg-heat-td').length,
                nan: (txt.match(/NaN|undefined/g) || []).length,
              };
            })()""")
            # 数据回来了、卡片画出来了、且不是错误页
            if (st and not st.get('loading') and not st.get('fail')
                    and st.get('cards', 0) >= 1 and st.get('len', 0) > 200):
                time.sleep(0.6)      # 等条形的 width 过渡跑完
                ready = True
                break
            time.sleep(0.4)

        if not ready:
            print(f"  [警告] {name} 未就绪：{st}")

        h = c.js("""(() => Math.max(
            document.querySelector('.app-scroll') ?
              document.querySelector('.app-scroll').scrollHeight : 0,
            document.documentElement.scrollHeight, window.innerHeight))()""") or 1200
        h = int(min(max(h + 30, 900), 8000))

        # 【高度闭环自校正】理由同 shoot_cdp：一次性算式永远差几十像素，
        # 大看板（原因治理页）最后一行会正好被卡在边界上。
        for _ in range(4):
            c.send("Emulation.setDeviceMetricsOverride", width=1440, height=h,
                   deviceScaleFactor=2, mobile=False)
            time.sleep(0.5)
            over = c.js("""(() => {
              const e = document.querySelector('.app-scroll');
              return e ? Math.max(0, e.scrollHeight - e.clientHeight) : 0;
            })()""") or 0
            if over <= 2:
                break
            print(f"  · 还差 {over}px 被藏住，视口加高到 {h + over + 40}")
            h = int(min(h + over + 40, 8000))
        if len(list_pages(port)) != 1:
            raise RuntimeError("DevTools 里不止一个页面，拒绝出图")
        shot = c.send("Page.captureScreenshot", format="png", captureBeyondViewport=True)
        dst = OUT / f"{name}.png"
        dst.write_bytes(base64.b64decode(shot["data"]))
        print(f"  -> {dst.name} (高 {h}) | 卡片 {st.get('cards')} · KPI {st.get('kpis')} "
              f"· 条 {st.get('bars')} · 热力格 {st.get('heat')} · NaN {st.get('nan')} "
              f"· {int((time.time()-t0)*1000)}ms")
        if st.get('fail'):
            print(f"  [错误块] {st['fail']}")
        if st.get('nan'):
            print("  [注意] 页面上出现了 NaN/undefined")
        c.ws.close()
        return dst
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        # 【profile 目录不在这里自动删】
        # 一个 Edge user-data-dir 约 20MB，反复调试十几轮能堆到 300MB。
        # 试过在 finally 里 shutil.rmtree，但被工作区的"批量删除保护"拦下了
        # （一次删 292 个文件）——那个保护是对的，不该绕过。
        # 所以改成只提示，由人决定什么时候清：
        #     rm -rf agent/backend/_tmp/pages/_profile*   （或直接删整个 _tmp）
        # 反正每次用时间戳新建目录，不清也不会互相冲突。
        pass


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    page = args[0] if args else 'dashboard'
    cats = args[1:] if len(args) > 1 else [None]

    ok = 0
    for cat in cats:
        name = f"{page}_{cat or 'all'}"
        print(f"[{name}] {page} · {cat or '全店'}")
        try:
            if capture(name, page, cat):
                ok += 1
        except Exception as e:
            print(f"  失败：{type(e).__name__}: {e}")
        # 【每张之间停一下，让上一个 Edge 真正退出】
        # 端口 9334 也要等释放，否则下一张连的是上一个实例的残留 websocket。
        time.sleep(1.5)
    print(f"\n完成 {ok}/{len(cats)}")
    return 0 if ok == len(cats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
