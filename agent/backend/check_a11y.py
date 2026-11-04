# -*- coding: utf-8 -*-
"""可访问性与响应式验收：键盘焦点环 · 降动效降级 · 窄屏布局。

    python check_a11y.py

=== 为什么这三件事必须单独验 ===

它们有三个共同点，决定了"写完"和"生效"之间隔着很远：

1. **键盘焦点环** —— 我在 v4 里统一改成了白色双环，但全站散着
   大量 `outline: none`。如果哪一处把它盖掉了，键盘用户按 Tab
   会看到"焦点凭空消失"。而这种问题 **鼠标用户永远遇不到**。

2. **降动效降级** —— `@media (prefers-reduced-motion: reduce)` 里写的
   `transition-duration: .01ms !important` 是否为真？我这次的动效层
   有 transform / filter / box-shadow 三种过渡，而 `!important` 写法
   一旦有优先级更高的一条规则，整条降级就失效了。系统设置我改不了，
   但 **CDP 能模拟这个媒体特性**（Emulation.setEmulatedMedia）。

3. **窄屏布局** —— 卡片里大量 `grid-template-columns` 是定值。
   1366px 的笔记本（也是评审最可能用的分辨率）会不会横向溢出？
   截图只拍 1440px，看不到这个问题。

=== 判据 ===

· 焦点：Tab 走 12 步，每一步都必须有一个元素带可见焦点环
        （box-shadow 或 outline 不为 none），且面积 > 0
· 降动效：模拟 reduce 后，抽查元素的 transition-duration 必须 ≤ 1ms
· 窄屏：1280 / 1366 两档，document.scrollWidth 不得超出 viewport\n· 语义：th 有 scope、可排序列有 aria-sort、输入框有 label、\n        aria-live 在无障碍树里、触控目标 >= 24×24\n· 高对比度：折线不能与底色同色、热力格仍可分档、条形长度仍有差异\n· 交互：可点元素必须可聚焦（role/tabIndex）、SVG 过渡必须在插值、\n        回车能触发、空格不滚页
"""
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket

from check_contrast import find_edge, BASE, OUT, free_port, Ctl as _BaseCtl

TABS = 12          # 走多少步 Tab


class Ctl(_BaseCtl):
    """在 check_contrast.Ctl 之上只补本文件要用的两件事。

    【为什么不各写一份】
    这两个文件的 Ctl 曾经是两份独立实现（send/js/wait_until 逐字重复）。
    今天给父类加"事件缓冲"时，如果还是两份，就得记得改两处 ——
    而今天已经因为"同一个判断散在两处"踩过一次（facts_from / .bi-mini）。
    所以这里只保留差异：Page.enable 与键盘注入，其余全部继承。
    """

    def __init__(self, ws):
        super().__init__(ws, 0)
        self.ws.send(json.dumps({"id": -1, "method": "Page.enable"}))

    def key(self, key, code, key_code, modifiers=0):
        for kind in ("rawKeyDown", "keyUp"):
            self.send("Input.dispatchKeyEvent", type=kind, key=key, code=code,
                      windowsVirtualKeyCode=key_code, nativeVirtualKeyCode=key_code,
                      modifiers=modifiers)


class Browser:
    def __init__(self, port, url, width=1440, height=1200):
        # 同 check_contrast.Browser：先占真空闲端口，避免连到残留实例造成假红
        port = free_port(port)
        prof = OUT / f"_profile_a11y_{port}_{int(time.time() * 1000)}"
        self.proc = subprocess.Popen(
            [find_edge(), "--headless=new", "--disable-gpu", "--no-first-run",
             f"--remote-debugging-port={port}", "--user-data-dir=" + str(prof),
             f"--window-size={width},{height}", url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws_url = None
        for _ in range(100):
            try:
                for t in json.load(urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json", timeout=2)):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        ws_url = t["webSocketDebuggerUrl"]
                        break
                if ws_url:
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not ws_url:
            self.close()
            raise RuntimeError("CDP 连接超时")
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.ctl = Ctl(self.ws)

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


# 当前焦点元素的可视指示。返回 null 表示"焦点在这里但看不见"。
FOCUS_JS = r"""
(() => {
  const el = document.activeElement;
  if (!el || el === document.body || el === document.documentElement) {
    return { none: true, tag: el ? el.tagName : 'null' };
  }
  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();

  // 焦点环可能是 box-shadow，也可能是 outline —— v4 两种都用了
  const hasShadow = cs.boxShadow && cs.boxShadow !== 'none';
  const hasOutline = cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0;
  const visible = (hasShadow || hasOutline) && r.width > 0 && r.height > 0;

  /* 【"有 outline" != "看得见"】
     4% 白的环在技术上也是 outline，但完全看不见。
     WCAG 2.1 对非文字元素要求至少 3:1。
     焦点环落在元素外侧（outline-offset），所以它压的是**页面底色**，
     按页面底色算对比度即可。 */
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
    return 0.2126*f(c[0]) + 0.7152*f(c[1]) + 0.0722*f(c[2]);
  };
  const ratio = (a, b) => {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };
  const parseC = (c) => {
    const m = (c || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(/[,/\s]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };
  const base = [11, 11, 12];          // 与 styles/app.css 的 --bg 一致
  const mix = (c) => [                // 半透明环在与底色合成后的实际颜色
    c[0]*c[3] + base[0]*(1-c[3]),
    c[1]*c[3] + base[1]*(1-c[3]),
    c[2]*c[3] + base[2]*(1-c[3]),
  ];

  let ringCR = null;
  let ringDesc = '(无环)';
  if (hasOutline) {
    const oc = parseC(cs.outlineColor);
    if (oc) {
      ringCR = Math.round(ratio(mix(oc), base) * 100) / 100;
      ringDesc = cs.outline;
    }
  } else if (hasShadow) {
    // box-shadow 环：取阴影里第一个非透明色的 alpha 近似
    const sc = parseC(cs.boxShadow);
    if (sc) {
      ringCR = Math.round(ratio(mix(sc), base) * 100) / 100;
      ringDesc = cs.boxShadow.slice(0, 60);
    }
  }

  return {
    none: false,
    tag: el.tagName,
    cls: (el.className || '').toString().slice(0, 46),
    text: (el.innerText || el.value || el.placeholder || '').trim().slice(0, 24),
    visible: visible,
    ringCR: ringCR,
    ringDesc: ringDesc,
    shadow: hasShadow ? cs.boxShadow.slice(0, 60) : '(无)',
    outline: hasOutline ? cs.outline : '(无)',
    size: Math.round(r.width) + 'x' + Math.round(r.height),
    /* 坐标是为"走完一整圈"服务的：判闭合靠"回到第一个元素"，
       而 SVG 元素的 className 是 SVGAnimatedString，toString() 后全都一样、
       innerText 又为空 —— 只靠 tag+cls+text 会让 5 个 <g> 互相认成同一个，
       第二格就误判成闭合。带上坐标才能唯一确定"同一格"。 */
    x: Math.round(r.left),
    y: Math.round(r.top),
    // 焦点元素是否在视口内。Tab 到页脚外的东西算正常，但看不到就是问题
    inView: r.top < innerHeight && r.bottom > 0,
  };
})()
"""


def section(t):
    print(f"\n{'=' * 68}\n{t}\n{'=' * 68}")


def check_focus(port) -> int:
    """把每一页的 Tab 序列走完一整圈，每一步都必须看得见焦点在哪。

    【为什么是"走完一整圈"，而不是"走 N 步"】
    这条检查改过三次，前两次都是靠猜步数，结果都在自欺：
      · 第 1 版只测总览/问答两页 → 其余 5 页的焦点环从没被看过
      · 第 2 版给每页配了固定步数（总览 16 / 问答 40 / 其余 10），
        但实测每页的 Tab 停靠点是 52 / 41 / 16 / 22 / 13 / 8 / 23 个，
        而且前 7 步永远是同一组侧边栏 —— 也就是说 quiet 页真正被验到的
        页面元素只有 3 个，attribution 那 41 个停靠点里 38 个从没被看过。
    "猜一个够用的步数"这件事本身就是错的：页面一变，步数就失准，
    而失准的表现是**更少**的检查 + 依然全绿，比报错难发现得多。
    所以改成走到"回到第一个元素"为止（序列闭合），把每个停靠点都验一遍；
    闭合与否本身也打印出来 —— 没闭合说明有元素被跳过或焦点逃逸。

    【focus ring 是逐页的，不是一条全局规则】
    `:focus-visible` 是全局的，但"谁可聚焦"和"谁的 outline 被谁盖掉"完全逐页：
    某页只要有一条 `.xxx { outline: none }`，焦点就凭空消失，
    这种规则不报错，鼠标用户永远遇不到。
    """
    section("键盘焦点：走完每一页的 Tab 一整圈，每一步都必须看得见焦点在哪")
    total_bad = 0
    try:
        navkeys = [p["key"] for p in json.loads(
            urllib.request.urlopen(BASE + "/api/pages/nav", timeout=10).read())["pages"]]
    except Exception:
        navkeys = ['dashboard', 'attribution', 'reasons', 'plan', 'reviews',
                   'competitors', 'chat']

    # 【必须覆盖到的元素类型】不是步数。textarea 是本项目唯一的文本输入，
    # 也是问答页唯一的入口，走完一圈却没见到它就说明检测器漏了。
    NEED = {"chat": "TEXTAREA"}
    MAX_STEPS = 90          # 防死循环；实测最多的一页 52 步

    for pg in navkeys:
        port += 1
        br = Browser(port, f"{BASE}/#/{pg}")
        page_bad = 0
        steps = 0
        seen_need = False
        first_key = None
        wrapped = False
        min_cr = None
        try:
            br.ctl.wait_until(
                "document.querySelectorAll('.sidenav-item.on').length===1", 60)
            time.sleep(1.4)
            br.ctl.js("document.body.focus()")
            time.sleep(0.2)
            need = NEED.get(pg)
            for i in range(1, MAX_STEPS + 1):
                br.ctl.key("Tab", "Tab", 9)
                time.sleep(0.15)       # 让 :focus-visible 的样式稳定
                f = br.ctl.js(FOCUS_JS) or {}
                if f.get("none"):
                    continue
                # 【身份要带坐标】SVG 元素的 className 是 SVGAnimatedString，
                # toString() 后全是 "[object SVGAnimatedString]"、innerText 为空，
                # 只用 tag+cls+text 会让 5 个 <g> 互相认成同一个，
                # 第二格就误判成"闭合"，整圈提前结束。
                key = (f.get("tag"), f.get("cls"), f.get("text"),
                       f.get("x"), f.get("y"))
                if first_key is None:
                    first_key = key
                elif key == first_key:
                    wrapped = True
                    break
                steps += 1
                cr = f.get("ringCR")
                # 三条件：有焦点指示 / 指示够显眼（>=3:1，WCAG 非文字要求）/ 在视口内
                ok = (f.get("visible") and f.get("inView")
                      and cr is not None and cr >= 3.0)
                if cr is not None:
                    min_cr = cr if min_cr is None else min(min_cr, cr)
                if not ok:
                    page_bad += 1
                    total_bad += 1
                    print(f"    ★ {pg} 第 {i} 步 {f.get('tag')} "
                          f"{str(f.get('cls'))[:28]:<28} 「{f.get('text')}」")
                    print(f"        焦点不可见 → ring={f.get('ringDesc')} "
                          f"shadow={f.get('shadow')} outline={f.get('outline')} "
                          f"inView={f.get('inView')}")
                if need and f.get("tag") == need:
                    seen_need = True
            if need and not seen_need:
                # 【没覆盖到就是失败，不能算通过】
                print(f"    ★ {pg}：走完 {steps} 步没见到 {need} —— 检测器未覆盖目标元素")
                total_bad += 1
            if steps == 0:
                print(f"    ★ {pg:<12} 没有任何可聚焦元素")
                total_bad += 1
                continue
            flag = "OK  " if page_bad == 0 else "★   "
            cr_s = f"{min_cr:.2f}" if min_cr is not None else "—"
            print(f"    {flag}{pg:<12} 停靠点 {steps:>3} 个"
                  f"{'（闭合）' if wrapped else f'（未闭合，已达上限 {MAX_STEPS}）'}"
                  f"  最低环对比 {cr_s}  不可见 {page_bad} 处")
        except Exception as e:                      # noqa: BLE001
            print(f"    ★   {pg:<12} [失败] {type(e).__name__}: {e}")
            total_bad += 1
        finally:
            br.close()
            time.sleep(0.35)

    print(f"\n  不可见焦点：{total_bad} 处")
    return total_bad


def check_reduced_motion(port) -> int:
    """模拟系统级"减少动画"，要求过渡时长降到 ~0。"""
    section("降动效降级：模拟 prefers-reduced-motion: reduce")

    PROBE = r"""
    (() => {
      const sels = ['.pg-card', '.pg-btn', '.bi-bar-f', '.bi-heat-c',
                    '.sidenav-item', '.bi-scope-i', '.act', '.chip'];
      const out = [];
      for (const s of sels) {
        const e = document.querySelector(s);
        if (!e) { out.push({sel: s, missing: true}); continue; }
        const cs = getComputedStyle(e);
        // 取四个时间里的最大值：只要有一个没降下来，动效就还在跑
        const durs = (cs.transitionDuration || '0s').split(',').map(v => parseFloat(v) || 0);
        const anims = (cs.animationDuration || '0s').split(',').map(v => parseFloat(v) || 0);
        out.push({
          sel: s,
          maxTransition: Math.max(...durs),
          maxAnimation: Math.max(...anims),
          transform: cs.transform,
        });
      }
      return out;
    })()
    """

    results = {}
    br = Browser(port, f"{BASE}/#/dashboard")
    try:
        br.ctl.wait_until("document.querySelectorAll('.bi-heat-c').length > 0", 60)
        time.sleep(0.8)

        # 基准：不降级时的过渡时长
        normal = br.ctl.js(PROBE) or []
        print("\n  [基准] 未降级（单位 s）:")
        for r in normal:
            if r.get("missing"):
                continue
            print(f"    {r['sel']:<16} transition={r['maxTransition']:.3f}  "
                  f"animation={r['maxAnimation']:.3f}")

        # 模拟 media 特性
        br.ctl.send("Emulation.setEmulatedMedia", features=[
            {"name": "prefers-reduced-motion", "value": "reduce"},
        ])
        time.sleep(0.5)
        reduced = br.ctl.js(PROBE) or []
        results = {r['sel']: r for r in reduced if not r.get('missing')}

        print("\n  [降级后] prefers-reduced-motion: reduce:")
        bad = 0
        for r in reduced:
            if r.get("missing"):
                print(f"    {r['sel']:<16} [跳过] 元素不存在")
                continue
            # 0.01ms = 0.00001s；允许到 1ms 容差
            okT = r["maxTransition"] <= 0.001
            okA = r["maxAnimation"] <= 0.001
            bad += 0 if (okT and okA) else 1
            mark = "OK  " if (okT and okA) else "★   "
            print(f"    {mark}{r['sel']:<16} transition={r['maxTransition']:.5f}  "
                  f"animation={r['maxAnimation']:.5f}")

        # 降级后悬停不应该再位移
        print("\n  [降级后] 悬停不应产生位移:")
        pos = br.ctl.js("""(() => {
          const e = document.querySelector('.bi-scope-i:not(.on)');
          if (!e) return null;
          const r = e.getBoundingClientRect();
          return {x: r.x + r.width/2, y: r.y + r.height/2};
        })()""")
        if pos:
            before = br.ctl.js(
                "getComputedStyle(document.querySelector('.bi-scope-i:not(.on)')).transform")
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                        x=int(pos["x"]), y=int(pos["y"]), button="none")
            time.sleep(0.4)
            after = br.ctl.js(
                "getComputedStyle(document.querySelector('.bi-scope-i:not(.on)')).transform")
            moved = before != after
            bad += 1 if moved else 0
            print(f"    {'★   ' if moved else 'OK  '}悬停前={before}  悬停后={after}")
        else:
            print("    [跳过] 找不到 .bi-scope-i")
    finally:
        br.close()
    print(f"\n  未降级项：{bad} 处")
    return bad


def check_narrow(port) -> int:
    """1280 / 1366 两档 × 全部页面：横向溢出（含被裁掉的越界元素）。

    原文档写的是"横向溢出与元素重叠"，但**重叠从来没实现过** ——
    这种注释比没有注释更糟：它让人以为覆盖到了。
    现在只声称实现过的两件事：页面横滚、元素越界（含被 overflow:hidden 裁掉）。
    """
    section("窄屏：1280 / 1366 × 全部页面（评审最可能用的笔记本分辨率）")

    OVERFLOW_JS = r"""
    (() => {
      const de = document.documentElement;
      const vw = window.innerWidth;
      // 页面里确实有几处"设计上就该横向滚动"的容器（.tbl-wrap / .pg-heat-wrap /
      // .bi-heat-wrap…）。它们里面的元素超出视口是**故意的**，
      // 不算页面溢出 —— 判据必须区分"页面横滚"和"容器内横滚"。
      const inScroller = (el) => {
        let p = el.parentElement;
        while (p && p !== document.documentElement) {
          const ox = getComputedStyle(p).overflowX;
          if (ox === 'auto' || ox === 'scroll') return true;
          p = p.parentElement;
        }
        return false;
      };
      const over = [];
      // 找出所有"右边界超过视口"的可见元素
      document.querySelectorAll('*').forEach(e => {
        const r = e.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) return;
        const cs = getComputedStyle(e);
        if (cs.display === 'none' || cs.visibility === 'hidden') return;
        if (r.right > vw + 2 && !inScroller(e)) {
          over.push({
            tag: e.tagName,
            cls: (e.className || '').toString().slice(0, 44),
            right: Math.round(r.right),
            w: Math.round(r.width),
          });
        }
      });
      // 去重：父子都溢出时只报最外层（宽度最大那个）
      const seen = new Set();
      const uniq = [];
      for (const o of over.sort((a, b) => b.w - a.w)) {
        const k = o.cls.split(' ')[0] || o.tag;
        if (seen.has(k)) continue;
        seen.add(k);
        uniq.push(o);
      }
      return {
        vw,
        scrollW: de.scrollWidth,
        overflowX: de.scrollWidth > vw + 2,
        // 【两种失效形态都要算失败】
        //   ① 页面真的能横滚（出现横向滚动条）
        //   ② 元素超出视口但被祖先 overflow:hidden **裁掉** —— 页面不横滚，
        //      滚动条也没有，可那块内容就是看不见了。
        // 2026-11-03 实测：注入一个 3000px 宽的块，overflowX 仍是 false，
        // 只有 offenders 能看见它。所以判失败必须看 offenders，不能只看滚动条。
        offenders: uniq.slice(0, 8),
      };
    })()
    """

    bad = 0
    # 【为什么从"只测 dashboard"扩到全部页面】
    # 横向溢出是**逐页布局**问题：卡片栅格、表格列宽、图例换行都写在各自页面里。
    # 只在 dashboard 上量，等于假设另外 6 页的栅格不会撑破 —— 而这个假设
    # 没有任何理由成立。2026-11-03 把覆盖矩阵拉出来才看见这个洞。
    # 页面清单以后端为准，别手写（手写的清单会漂移，且漂移不报错）。
    try:
        nav = json.loads(urllib.request.urlopen(BASE + "/api/pages/nav",
                                                timeout=10).read())
        keys = [p["key"] for p in nav.get("pages", [])]
    except Exception as e:
        print(f"    ★   取不到 /api/pages/nav：{e}")
        return 1

    for w, h in [(1280, 900), (1366, 900)]:
        port += 1
        br = Browser(port, f"{BASE}/#/{keys[0]}", width=w, height=h)
        try:
            br.ctl.wait_until("(document.querySelector('main') || {}).innerText "
                              "&& document.querySelector('main').innerText.length > 50", 90)
            time.sleep(1.0)
            for k in keys:
                br.ctl.js(f"location.hash = '#/{k}'")
                time.sleep(1.2)
                r = br.ctl.js(OVERFLOW_JS) or {}
                offs = r.get("offenders") or []
                # 判失败看两样：页面能横滚，或有元素被裁/越界（见 OVERFLOW_JS 注释）
                is_bad = bool(r.get("overflowX")) or bool(offs)
                bad += 1 if is_bad else 0
                print(f"    {'★    ' if is_bad else 'OK   '}[{w}px] {k:12s} "
                      f"viewport={r.get('vw')} scrollWidth={r.get('scrollW')}"
                      f"{'  ★ 横向溢出' if r.get('overflowX') else ''}"
                      f"{f'  ★ 越界元素 {len(offs)} 个' if offs else ''}")
                for o in offs[:4]:
                    print(f"        {o['tag']:<6} w={o['w']:>5} right={o['right']:>5}  {o['cls']}")
        finally:
            br.close()
            time.sleep(0.3)
    print(f"\n  溢出档位：{bad} 个")
    return bad


def check_interactive(port) -> int:
    """鼠标能点的东西，键盘也必须能点；且过渡不能"瞬间跳"。

    【为什么单独一节】
    前几节验的是"看得见"，这一节验的是"点得到"。
    一个元素可以对比度满分、焦点环漂亮，却因为是个裸 <div onClick>
    而永远进不了 Tab 序列 —— 键盘用户根本到不了它面前。

    另外验一件很容易写错的事：
    **transition 作用在 SVG 几何属性（r）上是否真的在插值。**
    写了 `transition: r 180ms` 不代表生效，浏览器可能当作未知属性忽略，
    且不报任何错。唯一判据是 hover 后立刻读，值应当还在中间。
    """
    section("交互可达性：鼠标能点 = 键盘能点；过渡不能是瞬间跳变")
    bad = 0
    port += 1
    br = Browser(port, f"{BASE}/#/dashboard")
    try:
        br.ctl.wait_until("document.querySelectorAll('.pg-dot-g').length > 0", 60)
        time.sleep(1.0)

        # ---- 1. 可点元素必须可聚焦 ----
        print("\n  [1] 可点击元素的可聚焦性")
        r = br.ctl.js(r"""(() => {
          const out = [];
          // 只查"我们自己挂的 role=button"，用来看有没有漏挂
          document.querySelectorAll('.pg-dot-g, .bi-bar-row.clickable').forEach(e => {
            out.push({
              cls: (e.className && e.className.baseVal !== undefined
                    ? e.className.baseVal : e.className || '').toString().slice(0, 24),
              tabIndex: e.tabIndex,
              role: e.getAttribute('role'),
            });
          });
          return out;
        })()""") or []
        for it in r:
            ok = it["tabIndex"] >= 0 and it["role"] == "button"
            bad += 0 if ok else 1
            print(f"    {'OK  ' if ok else '★   '}{it['cls']:<24} "
                  f"tabIndex={it['tabIndex']:<3} role={it['role']}")
        if not r:
            print("    ★ 一个都没找到 —— 选择器可能已失效")
            bad += 1

        # ---- 2. 圆点过渡是否在插值 ----
        print("\n  [2] 异常周圆点半径过渡（必须读到中间值）")
        vals = br.ctl.js(r"""(() => {
          const g = document.querySelector('.pg-dot-g');
          const c = g.children[1];
          return {r0: parseFloat(getComputedStyle(c).r)};
        })()""") or {}
        pos = br.ctl.js(r"""(() => {
          const g = document.querySelector('.pg-dot-g');
          const b = g.children[0].getBoundingClientRect();
          return {x: Math.round(b.x + b.width/2), y: Math.round(b.y + b.height/2)};
        })()""") or {}
        r0 = vals.get("r0")
        if pos and r0 is not None:
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                        x=pos["x"], y=pos["y"])
            time.sleep(0.05)          # 远小于 180ms
            r_mid = br.ctl.js(
                "parseFloat(getComputedStyle("
                "document.querySelector('.pg-dot-g').children[1]).r)")
            time.sleep(0.6)
            r_end = br.ctl.js(
                "parseFloat(getComputedStyle("
                "document.querySelector('.pg-dot-g').children[1]).r)")
            mid_ok = r_mid is not None and r0 < r_mid < r_end
            bad += 0 if mid_ok else 1
            print(f"    静止 r={r0}  +50ms r={r_mid}  +650ms r={r_end}")
            print(f"    {'OK   ' if mid_ok else '★    '}"
                  f"{'在插值（过渡生效）' if mid_ok else '瞬间跳变（transition 未生效）'}")
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
            time.sleep(0.4)
        else:
            print("    ★ 找不到圆点")
            bad += 1

        # ---- 3. 回车真的能触发 ----
        print("\n  [3] 聚焦后按回车是否真的跳转")
        h0 = br.ctl.js("location.hash")
        br.ctl.js("document.querySelector('.pg-dot-g').focus()")
        time.sleep(0.25)
        for kind in ("rawKeyDown", "keyUp"):
            br.ctl.send("Input.dispatchKeyEvent", type=kind, key="Enter",
                        code="Enter", windowsVirtualKeyCode=13,
                        nativeVirtualKeyCode=13)
        time.sleep(1.0)
        h1 = br.ctl.js("location.hash")
        ok = h0 != h1
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}{h0} → {h1}")

        # ---- 4. 空格不滚页 ----
        print("\n  [4] 空格键不应滚动页面")
        br.ctl.js("history.back()")
        time.sleep(1.2)
        br.ctl.wait_until("document.querySelectorAll('.pg-dot-g').length > 0", 30)
        # 【滚动容器是 .app-scroll，不是 window】
        # 原来写的是 window.scrollTo(0, 240) + 读 window.scrollY。
        # 但页面滚动发生在 overflow-y:auto 的 .app-scroll 上，
        # window.scrollTo 对这个容器毫无作用 —— 于是 y0 和 y1 恒为 0，
        # `y0 == y1` 永远成立，这条断言从来没真正执行过。
        # 这类"恒真的测试"比没有测试更危险：它给出的绿灯是假的。
        #
        # 【但改成真容器之后又发现：不能靠"位置没变"来判】
        # 空格在这里是双重语义：既要 preventDefault 防止滚屏，
        # 又会 onPick 跳到归因页。一跳页，新页面的 scrollTop 自然是 0 ——
        # 于是"200 → 0"看起来像"滚回顶部了"，实际是**页面换了**。
        # 用位置差来判，无论怎么写都会把这两种情况混在一起。
        #
        # 所以直接判那条真正要保证的事：**空格事件的默认行为有没有被拦下**。
        # 拦住 = 浏览器不会滚；这是因果关系，不是现象巧合。
        #
        # 监听挂在 document 上：React 把事件委托到 #root，
        # #root 是 body 的子节点，冒泡顺序保证 document 的监听
        # 在 React handler 之后执行，读到的 defaultPrevented 才是最终值。
        br.ctl.js(r"""(() => {
          window.__sp = null;
          document.addEventListener('keydown', (e) => {
            if (e.code === 'Space') window.__sp = e.defaultPrevented;
          }, false);
          document.querySelector('.app-scroll').scrollTop = 200;
          return true;
        })()""")
        time.sleep(0.3)
        y0 = br.ctl.js("Math.round(document.querySelector('.app-scroll').scrollTop)")
        if not y0:
            print(f"    ★ 前提不成立：滚动容器没滚起来（scrollTop={y0}）")
            bad += 1
        br.ctl.js("document.querySelector('.pg-dot-g').focus()")
        for kind in ("rawKeyDown", "keyUp"):
            br.ctl.send("Input.dispatchKeyEvent", type=kind, key=" ",
                        code="Space", windowsVirtualKeyCode=32,
                        nativeVirtualKeyCode=32)
        time.sleep(0.6)
        prevented = br.ctl.js("window.__sp")
        # 跳页后 .app-scroll 可能已经重建，读不到就退回 window
        y1 = br.ctl.js(r"""(() => {
          const s = document.querySelector('.app-scroll');
          return s ? Math.round(s.scrollTop) : null;
        })()""")
        ok4 = bool(y0) and prevented is True
        bad += 0 if ok4 else 1
        print(f"    {'OK   ' if ok4 else '★    '}空格 defaultPrevented={prevented}"
              f"（拦住 = 浏览器不滚）· 起点 scrollTop {y0}"
              f" · 触发后 {y1}（None = 已跳页）")

        # ---- 5. 走势图整条线可读（不只是异常周那几个红点）----
        # 【为什么要单独测这一条】
        # v4.6 之前，52 个点里只有 5 个异常周可交互，其余 47 周
        # 鼠标划过毫无反应 —— 而"W17 到底多少"恰恰是读者最常问的。
        # 这种缺失**截图看不出来**：图长得完好，只是不响应。
        # 所以必须用"悬停一个正常周，看有没有读数"来断言。
        print("\n  [5] 走势图：整条线是否可读（不再只有异常周）")
        # 【起点必须显式回到总览页，不能靠 history.back()】
        # 上一节的空格用例会 preventDefault + onPick，**页面已经跳到归因页**了
        # （"空格不滚页"依然通过，因为滚动确实被拦住了）。
        # 一开始我以为 back() 能兜住，结果位置取决于前面的用例走了几步 ——
        # 这类"靠残留状态碰巧正确"的测试，改一次顺序就全崩。
        # 直接设 hash 跳回目标页，让这一节自己负责自己的前置条件。
        br.ctl.js("location.hash = '#/dashboard'")
        # 条件要挑"只有目标页才有的东西"，且要判宽度 —— 元素存在但
        # 尺寸为 0 时（还在布局）算出来的坐标全是 0，等于没测。
        arrived = br.ctl.wait_until(
            "(() => { const s = document.querySelector('.pg-chart-svg');"
            " return !!s && s.getBoundingClientRect().width > 100; })()", 30)
        if not arrived:
            print("    ★ 没能回到总览页（.pg-chart-svg 不存在或尺寸为 0）")
            bad += 1
        time.sleep(0.4)
        br.ctl.js("document.querySelector('.pg-chart').scrollIntoView({block:'center'})")
        time.sleep(0.4)
        geo = br.ctl.js(r"""(() => {
          const svg = document.querySelector('.pg-chart-svg');
          if (!svg) return {missing: 'svg'};
          const b = svg.getBoundingClientRect();
          if (b.width < 100) return {missing: 'zero-width'};
          const N = 52, W = 1000, L = 34, R = 12, step = (W - L - R) / (N - 1);
          const at = (i) => ({x: Math.round(b.left + (L + i * step) / W * b.width),
                              y: Math.round(b.top + b.height / 2)});
          return {n: N, norm: at(5),
                  out: {x: Math.round(b.left - 8), y: Math.round(b.top + b.height / 2)},
                  dots: document.querySelectorAll('.pg-dot-g').length};
        })()""") or {}
        # 【js() 在脚本出错时返回 {"__error__": ...}，它是个 truthy 的 dict】
        # 直接当数据用会得到 KeyError，而 KeyError 看起来像"测试脚本写错"，
        # 实际是"页面里这段 JS 崩了"。分开报，别混在一起。
        if "__error__" in geo:
            print("    ★ 取几何信息时页面 JS 报错：", geo["__error__"])
            bad += 1
        elif not geo.get("norm"):
            print(f"    ★ 拿不到图区几何信息：{geo.get('missing') or geo}")
            bad += 1
        else:
            # 5a. 悬停一个"肯定不是异常周"的位置（索引 5，W6）
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                        x=geo["norm"]["x"], y=geo["norm"]["y"])
            time.sleep(0.4)
            r = br.ctl.js(r"""(() => {
              const tip = document.querySelector('.bi-tip');
              const cross = document.querySelector('.pg-cross');
              const dot = document.querySelector('.pg-cursor-dot');
              return {tip: tip ? tip.innerText.split('\n')[0] : null,
                      cross: cross ? parseFloat(cross.getAttribute('x1')) : null,
                      dot: dot ? getComputedStyle(dot).fill : null};
            })()""") or {}
            ok5a = bool(r.get("tip")) and r.get("cross") is not None and bool(r.get("dot"))
            bad += 0 if ok5a else 1
            print(f"    {'OK   ' if ok5a else '★    '}悬停非异常周 → 浮层: {r.get('tip')}"
                  f" / 准星 x={r.get('cross')} / 光标点 {r.get('dot')}")

            # 5b. 光标点颜色必须区分异常/正常（否则"这一周是什么"读不出来）
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
            time.sleep(0.3)
            norm_fill = r.get("dot")

            # 5c. 移出图外必须全部消失（残留的准星比不显示更糟）
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                        x=geo["out"]["x"], y=geo["out"]["y"])
            time.sleep(0.4)
            r = br.ctl.js(r"""({tip: !!document.querySelector('.bi-tip'),
                               cross: !!document.querySelector('.pg-cross')})""") or {}
            ok5c = not r.get("tip") and not r.get("cross")
            bad += 0 if ok5c else 1
            print(f"    {'OK   ' if ok5c else '★    '}移出图外 → 浮层 {r.get('tip')} / 准星 {r.get('cross')}")

            # 5d. 点击一个非异常周必须也能跳转
            # 【这条最容易漏】改造时只给容器挂了 onMouseMove，忘了 onClick，
            # 图就变成"能读数但点不动"，而 note 上写着"点任意一周"。
            h0 = br.ctl.js("location.hash")
            p = geo["norm"]
            for kind in ("mousePressed", "mouseReleased"):
                br.ctl.send("Input.dispatchMouseEvent", type=kind, x=p["x"], y=p["y"],
                            button="left", clickCount=1)
            time.sleep(1.2)
            h1 = br.ctl.js("location.hash")
            ok5d = h0 != h1 and "attribution" in (h1 or "")
            bad += 0 if ok5d else 1
            print(f"    {'OK   ' if ok5d else '★    '}点击非异常周 → {h0} → {h1}")

            # ----------------------------------------------------------
            # [6] 框选：v4.7 新增的区间分析
            # 【为什么这一节必须自动化】
            # 拖拽相关的东西手工测几乎测不全 —— 鼠标按下/移动/松开三件事
            # 分散在三个事件里，中间还有"刚拖完不要顺带触发 click"这种
            # 时序问题。我第一版就写错过：快捷选区按钮的点击冒泡到容器，
            # 被容器的"点空白 = 清空"逻辑吃掉，选中瞬间就消失。
            # 点一下就看得出来，但漏了就会以为功能没做。
            # ----------------------------------------------------------
            print("\n  [6] 框选区间（拖拽 → 摘要 → 热力图联动）")
            # 【必须先回总览页】
            # 5d 刚点了一周，页面已经跳到 #/attribution/N，
            # 这时 .pg-chart-svg 根本不在文档里。
            # 第一版漏了这一步，报错是"取拖拽坐标失败：svg"——
            # 看起来像选择器写错，实际是"还没回去"。
            br.ctl.js("location.hash = '#/dashboard'")
            back = br.ctl.wait_until(
                "(() => { const s = document.querySelector('.pg-chart-svg');"
                " return !!s && s.getBoundingClientRect().width > 100; })()", 30)
            time.sleep(0.6)
            if not back:
                print("    ★ 没能回到总览页，跳过框选检查")
                bad += 1
            br.ctl.js("document.querySelector('.pg-chart').scrollIntoView({block:'center'})")
            time.sleep(0.3)
            g2 = br.ctl.js(r"""(() => {
              const svg = document.querySelector('.pg-chart-svg');
              if (!svg) return {missing: 'svg'};
              const b = svg.getBoundingClientRect();
              const N = 52, W = 1000, L = 34, R = 12, step = (W - L - R) / (N - 1);
              const at = (i) => ({x: Math.round(b.left + (L + i * step) / W * b.width),
                                  y: Math.round(b.top + b.height / 2)});
              return {from: at(10), to: at(30), far: at(48)};
            })()""") or {}
            if "__error__" in g2 or not g2.get("from"):
                print(f"    ★ 取拖拽坐标失败：{g2.get('missing') or g2}")
                bad += 1
            else:
                # 6a. 真拖 20 周，摘要必须出现
                a, z = g2["from"], g2["to"]
                br.ctl.send("Input.dispatchMouseEvent", type="mousePressed",
                            x=a["x"], y=a["y"], button="left", buttons=1, clickCount=1)
                for k in range(1, 11):
                    br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                                x=a["x"] + (z["x"] - a["x"]) * k / 10, y=a["y"],
                                button="left", buttons=1)
                    time.sleep(0.04)
                # 拖动中：框在、摘要还没出（数字还在变，出了反而晃眼）
                mid = br.ctl.js(r"""({box: !!document.querySelector('.pg-sel-box'),
                                      bar: !!document.querySelector('.pg-selbar')})""") or {}
                br.ctl.send("Input.dispatchMouseEvent", type="mouseReleased",
                            x=z["x"], y=z["y"], button="left", buttons=0, clickCount=1)
                time.sleep(0.6)
                r6 = br.ctl.js(r"""(() => {
                  const bar = document.querySelector('.pg-selbar');
                  const box = document.querySelector('.pg-sel-box');
                  const masks = document.querySelectorAll('.pg-sel-mask rect');
                  return {
                    bar: bar ? bar.innerText.replace(/\n/g, ' ') : null,
                    box: !!box,
                    dashed: box ? box.getAttribute('stroke-dasharray') : 'none',
                    maskW: Array.from(masks).map(m => Math.round(m.getBoundingClientRect().width)),
                    link: document.querySelectorAll('.bi-heat-c.is-link').length,
                    total: document.querySelectorAll('.bi-heat-c').length,
                  };
                })()""") or {}
                ok6a = (bool(r6.get("bar")) and r6.get("box")
                        and r6.get("dashed") in (None, "null", "none")
                        and mid.get("box") is True)
                bad += 0 if ok6a else 1
                print(f"    {'OK   ' if ok6a else '★    '}拖选 W11–W31 → 摘要: "
                      f"{(r6.get('bar') or '无')[:58]}")
                print(f"         拖动中有框={mid.get('box')} 松手后虚线={r6.get('dashed')} "
                      f"遮罩宽度={r6.get('maskW')}")

                # 6b. 热力图必须联动到对应的那一批列
                # 20 周 × 4 行 = 80 格。数字对不上说明传给 HeatGrid 的周次错了。
                n_week = 21
                want = n_week * 4
                ok6b = r6.get("link") == want
                bad += 0 if ok6b else 1
                print(f"    {'OK   ' if ok6b else '★    '}热力图联动 {r6.get('link')} 格"
                      f"（{n_week} 周 × 4 行 = 应 {want}）")

                # 6c. 拖完不能顺带跳页 —— 松手那一下是"确定选区"，不是"选这一周"
                ok6c = "attribution" not in (br.ctl.js("location.hash") or "")
                bad += 0 if ok6c else 1
                print(f"    {'OK   ' if ok6c else '★    '}松手不误触跳页 → {br.ctl.js('location.hash')}")

                # 6d. Esc 必须能退出。框选是典型的"进得去出不来"设计陷阱。
                br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                            x=z["x"], y=z["y"] + 300)
                time.sleep(0.2)
                br.ctl.key("Escape", "Escape", 27)
                time.sleep(0.5)
                r6d = br.ctl.js(r"""({bar: !!document.querySelector('.pg-selbar'),
                                      box: !!document.querySelector('.pg-sel-box'),
                                      link: document.querySelectorAll('.bi-heat-c.is-link').length})""") or {}
                ok6d = not r6d.get("bar") and not r6d.get("box") and r6d.get("link") == 0
                bad += 0 if ok6d else 1
                print(f"    {'OK   ' if ok6d else '★    '}Esc 清除 → 摘要 {r6d.get('bar')} "
                      f"/ 框 {r6d.get('box')} / 联动残留 {r6d.get('link')}")

                # 6e. 快捷选区按钮：键盘/触屏唯一的入口，且必须不被容器吞掉
                r6e = br.ctl.js(r"""(() => {
                  const b = document.querySelectorAll('.pg-selgo-b');
                  if (!b.length) return {n: 0};
                  b[1].click();
                  return {n: b.length, h: Math.round(b[0].getBoundingClientRect().height)};
                })()""") or {}
                time.sleep(0.5)
                r6f = br.ctl.js(r"""(() => {
                  const bar = document.querySelector('.pg-selbar');
                  return {bar: bar ? bar.innerText.split('\n')[0] : null,
                          link: document.querySelectorAll('.bi-heat-c.is-link').length};
                })()""") or {}
                ok6e = r6e.get("n") == 3 and bool(r6f.get("bar")) and r6f.get("link", 0) > 0
                bad += 0 if ok6e else 1
                print(f"    {'OK   ' if ok6e else '★    '}快捷选区 {r6e.get('n')} 个按钮"
                      f"（高 {r6e.get('h')}px）→ {r6f.get('bar')} / 联动 {r6f.get('link')} 格")

                # ------------------------------------------------------
                # [7] 触屏：横向拖=框选，纵向滑=滚页面
                # 【为什么这条必须单独测】
                # 只绑 mouse 事件的话，桌面浏览器不会为"手指在图上划过"
                # 合成 mousemove，触屏上完全拖不动。而桌面自动化里
                # 用的是 dispatchMouseEvent，压根测不出这个问题。
                # 第一版就是这么漏的：桌面 5 条全过，触屏一条都不行。
                # ------------------------------------------------------
                print("\n  [7] 触屏：横向框选 / 纵向滚动 互不干扰")
                br.ctl.send("Emulation.setTouchEmulationEnabled",
                            enabled=True, maxTouchPoints=5)
                time.sleep(0.3)
                br.ctl.js("document.querySelector('.pg-selbar-x')?.click()")
                br.ctl.js("document.querySelector('.pg-chart').scrollIntoView({block:'center'})")
                time.sleep(0.5)
                g3 = br.ctl.js(r"""(() => {
                  const s = document.querySelector('.app-scroll');
                  const b = document.querySelector('.pg-chart-svg').getBoundingClientRect();
                  return {x: b.x, y: b.y, w: b.width, h: b.height,
                          top: s ? Math.round(s.scrollTop) : null,
                          canUp: s ? s.scrollTop > 0 : null};
                })()""") or {}

                def swipe(x0, y0, x1, y1, n=8):
                    br.ctl.send("Input.dispatchTouchEvent", type="touchStart",
                                touchPoints=[{"x": x0, "y": y0}])
                    for k in range(1, n + 1):
                        br.ctl.send("Input.dispatchTouchEvent", type="touchMove",
                                    touchPoints=[{"x": x0 + (x1 - x0) * k / n,
                                                  "y": y0 + (y1 - y0) * k / n}])
                        time.sleep(0.04)
                    br.ctl.send("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
                    time.sleep(0.7)

                if not g3.get("x"):
                    print("    ★ 取触屏几何失败，跳过")
                    bad += 1
                else:
                    ty = g3["y"] + g3["h"] * 0.5
                    swipe(g3["x"] + g3["w"] * 0.3, ty, g3["x"] + g3["w"] * 0.72, ty)
                    r7 = br.ctl.js(r"""(() => {
                      const bar = document.querySelector('.pg-selbar');
                      return {box: !!document.querySelector('.pg-sel-box'),
                              bar: bar ? bar.innerText.split('\n')[0] : null,
                              link: document.querySelectorAll('.bi-heat-c.is-link').length,
                              hash: location.hash};
                    })()""") or {}
                    ok7a = (r7.get("box") and r7.get("bar")
                            and r7.get("link", 0) > 0
                            and "attribution" not in (r7.get("hash") or ""))
                    bad += 0 if ok7a else 1
                    print(f"    {'OK   ' if ok7a else '★    '}触屏横拖 → 框 {r7.get('box')}"
                          f" / {r7.get('bar')} / 联动 {r7.get('link')} 格"
                          f" / 未误跳 {r7.get('hash')}")

                    # 纵向滑必须仍然滚页面
                    br.ctl.js("document.querySelector('.pg-selbar-x')?.click()")
                    time.sleep(0.3)
                    br.ctl.js("document.querySelector('.app-scroll').scrollTop = 120")
                    time.sleep(0.3)
                    g4 = br.ctl.js(r"""(() => {
                      const s = document.querySelector('.app-scroll');
                      const b = document.querySelector('.pg-chart-svg').getBoundingClientRect();
                      return {x: b.x, y: b.y, w: b.width, h: b.height,
                              top: Math.round(s.scrollTop),
                              canDown: s.scrollTop + s.clientHeight < s.scrollHeight - 2};
                    })()""") or {}
                    ty2 = g4["y"] + g4["h"] * 0.5
                    swipe(g4["x"] + g4["w"] * 0.5, ty2 + 40, g4["x"] + g4["w"] * 0.5, ty2 - 90, n=10)
                    r7b = br.ctl.js(r"""(() => {
                      const s = document.querySelector('.app-scroll');
                      return {top: Math.round(s.scrollTop),
                              box: !!document.querySelector('.pg-sel-box'),
                              bar: !!document.querySelector('.pg-selbar')};
                    })()""") or {}
                    moved = (r7b.get("top") or 0) != g4.get("top")
                    ok7b = moved and not r7b.get("box") and not r7b.get("bar")
                    bad += 0 if ok7b else 1
                    print(f"    {'OK   ' if ok7b else '★    '}触屏纵滑 → scrollTop "
                          f"{g4.get('top')} → {r7b.get('top')}"
                          f"（滚动了={moved}）· 没误触框选 {r7b.get('box')}/{r7b.get('bar')}")
    finally:
        br.close()
    print(f"\n  交互可达性问题：{bad} 处")
    return bad


# 表格语义探针：dashboard 与"逐页扫"两个地方都要用，
# 放模块级一份 —— 两处各写一遍就是"修一份忘一份"（今天的 `.bi-mini` 老坑）。
_TABLE_PROBE = r"""(() => {
  const ths = [...document.querySelectorAll('table th')];
  /* 【判据必须是"行为"，不是类名】
     这里原来用 className.includes('bi-th-sort') 统计可排序列。
     原因治理页的表头是另一套实现（class="r sortable"），
     于是 sortable 恒为 0，而调用处写的是
     `if sortable and focusable != sortable` —— 0 直接短路成通过。
     结果是：整整一个页面的可排序表头不可聚焦，检查却报 OK。
     类名会漂移、会分叉（本项目已经有两套表头），光标不会：
     设计上"点得动"统一用 cursor:pointer 表达，两套实现都遵守。 */
  const ptr = (x) => getComputedStyle(x).cursor === 'pointer';
  const sortTh = ths.filter(ptr);
  /* 可点行同理，而且更隐蔽：<tr> 从来不在浏览器的默认可聚焦列表里，
     所以"行可点"这件事必须显式补 tabIndex，漏了不会有任何视觉迹象。 */
  const rows = [...document.querySelectorAll('tbody tr')].filter(ptr);
  return {
    th: ths.length,
    scoped: ths.filter(x => x.getAttribute('scope')).length,
    sortable: sortTh.length,
    sortableNoAria: sortTh.filter(x => !x.getAttribute('aria-sort')).length,
    sortableNoTab: sortTh.filter(x => x.tabIndex < 0).length,
    clickRows: rows.length,
    clickRowsNoTab: rows.filter(x => x.tabIndex < 0).length,
  };
})()"""


def check_semantics(port) -> int:
    """语义与读屏：表格有 scope、流式区有 aria-live、输入框有 label。

    【为什么这一节最该测但最容易漏】
    前面几节都是"视觉/触觉"层面的：看得见、点得到。
    这一节是"听觉"层面的 —— 对用读屏的人来说，**前面全部无效**。

    最典型的是 aria-live：
    · 完全没有 → 问完问题什么也听不到（本项目原来就是 0 个）
    · 有但写在流式容器上 → 每个 token 播报一次，变成噪音
    · 有但用了 display:none → 从无障碍树消失，等于没有

    第三种最坑：代码里明明写着 aria-live，看起来完全正确。
    判据必须是"display 不是 none、visibility 不是 hidden、仍在文档流里"。
    """
    section("语义与读屏：表格 scope · aria-live · 输入框 label · 触控尺寸")
    bad = 0

    # ---------- 总览页 ----------
    port += 1
    br = Browser(port, f"{BASE}/#/dashboard")
    try:
        br.ctl.wait_until("document.querySelectorAll('.bi-heat-c').length > 0", 60)
        time.sleep(1.0)

        print("\n  [1] 表格语义")
        t = br.ctl.js(_TABLE_PROBE) or {}
        for k, v in t.items():
            print(f"    {k}: {v}")
        if t.get("th") and t.get("scoped") != t.get("th"):
            print(f"    ★ {t['th'] - t['scoped']} 个 th 缺 scope —— 读屏说不出列名")
            bad += 1
        if t.get("sortableNoAria"):
            print(f"    ★ {t['sortableNoAria']} 个可排序列缺 aria-sort")
            bad += 1
        if t.get("sortableNoTab"):
            print(f"    ★ {t['sortableNoTab']} 个可排序列不可 Tab 聚焦 —— 键盘用户改不了排序")
            bad += 1
        if t.get("clickRowsNoTab"):
            print(f"    ★ {t['clickRowsNoTab']}/{t['clickRows']} 个可点行不可 Tab 聚焦"
                  f" —— 键盘用户点不开明细")
            bad += 1

        print("\n  [2] 触控目标（WCAG 2.2 SC 2.5.8，需 24×24；含伪元素命中层）")
        sz = br.ctl.js(r"""(() => {
          const m = (sel) => {
            const e = document.querySelector(sel);
            if (!e) return null;
            const b = e.getBoundingClientRect();
            const a = getComputedStyle(e, '::after');
            // 命中层高度可能是 px 也可能是 auto
            const ah = parseFloat(a.height) || 0;
            return {w: Math.round(b.width), h: Math.round(b.height),
                    hitH: Math.round(Math.max(b.height, ah)),
                    hitW: Math.round(b.width)};
          };
          return {seg: m('.bi-seg-i'), scope: m('.bi-scope-i')};
        })()""") or {}
        for k, label in [("seg", ".bi-seg-i"), ("scope", ".bi-scope-i")]:
            d = sz.get(k)
            if not d:
                print(f"    {label}: [跳过] 不存在")
                continue
            ok = d["hitH"] >= 24 and d["hitW"] >= 24
            bad += 0 if ok else 1
            print(f"    {'OK  ' if ok else '★   '}{label:<14} 视觉 {d['w']}×{d['h']}  "
                  f"命中 {d['hitW']}×{d['hitH']}")
    finally:
        br.close()
        time.sleep(0.4)

    # ---------- 六页的 th 覆盖（只测总览页会漏掉 90%）----------
    # 【为什么必须逐页扫】
    # 总览页只有 7 个 th，而全部六页共 79 个。
    # 只在总览页测，会得出"scope 已全覆盖"的结论，
    # 而实际上原因治理/归因/竞品三页各有十几个表头 ——
    # 它们才是这个产品里读者最常读的表。
    print("\n  [1b] 各页 th 语义（表格是逐页手写的，dashboard 对不代表别处对）")
    # 页面清单读后端下发，不手写（手写清单会漂移，且漂移不报错）。
    try:
        navkeys = [p["key"] for p in json.loads(
            urllib.request.urlopen(BASE + "/api/pages/nav", timeout=10).read())["pages"]]
    except Exception:
        navkeys = ['dashboard', 'attribution', 'reasons', 'plan', 'reviews',
                   'competitors', 'chat']
    tot_th = tot_sc = 0
    for pg in navkeys:
        port += 1
        b2 = Browser(port, f"{BASE}/#/{pg}")
        try:
            b2.ctl.wait_until(
                "document.querySelectorAll('table th, .pg-card, .pg-table').length > 0", 50)
            time.sleep(1.6)
            r2 = b2.ctl.js(_TABLE_PROBE) or {}
        except Exception as e:                      # noqa: BLE001
            print(f"    {pg}: [跳过] {type(e).__name__}")
            continue
        finally:
            b2.close()
            time.sleep(0.3)
        th, sc = r2.get("th", 0), r2.get("scoped", 0)
        if not th:
            print(f"    --   {pg:<12} 无表格")
            continue
        tot_th += th
        tot_sc += sc
        # scope 之外还量三件：可排序列必须带 aria-sort、必须可 Tab 聚焦，
        # 可点行必须可 Tab 聚焦（<tr> 默认不可聚焦，漏了没有任何视觉迹象）
        ok1 = (th == sc)
        ok2 = not r2.get("sortableNoAria")
        ok3 = not r2.get("sortableNoTab")
        ok4 = not r2.get("clickRowsNoTab")
        bad += (0 if ok1 else 1) + (0 if ok2 else 1) + (0 if ok3 else 1) + (0 if ok4 else 1)
        flag = "OK  " if (ok1 and ok2 and ok3 and ok4) else "★   "
        print(f"    {flag}{pg:<12} th={th:>3}  scoped={sc:>3}"
              f"  可排序 {r2.get('sortable', 0)}（缺 aria-sort {r2.get('sortableNoAria', 0)}"
              f" · 不可聚焦 {r2.get('sortableNoTab', 0)}）"
              f"  可点行 {r2.get('clickRows', 0)}（不可聚焦 {r2.get('clickRowsNoTab', 0)}）")
    print(f"    合计 {tot_sc}/{tot_th}")
    if tot_sc != tot_th:
        print("    ★ 有表头缺 scope —— 读屏说不出列名")
        bad += 1

    # ---------- 问答页 ----------
    port += 1
    br = Browser(port, f"{BASE}/#/chat")
    try:
        br.ctl.wait_until("!!document.querySelector('.composer textarea')", 60)
        time.sleep(1.0)

        print("\n  [3] 输入框的可访问名与描述")
        f = br.ctl.js(r"""(() => {
          const ta = document.querySelector('.composer textarea');
          if (!ta) return null;
          const lab = ta.id ? document.querySelector(`label[for="${ta.id}"]`) : null;
          const db = ta.getAttribute('aria-describedby');
          return {
            hasLabel: !!(lab && lab.innerText.trim()),
            label: lab ? lab.innerText.trim().slice(0, 24) : '',
            describedBy: db || '',
            // 【关键】描述目标必须真实存在，否则是条死链接
            describedExists: !!(db && document.getElementById(db)),
          };
        })()""") or {}
        for k, v in f.items():
            print(f"    {k}: {v}")
        if not f.get("hasLabel"):
            print("    ★ 输入框没有 label（placeholder 输入后就消失，不能代替）")
            bad += 1
        if f.get("describedBy") and not f.get("describedExists"):
            print("    ★ aria-describedby 指向的 id 不存在")
            bad += 1

        print("\n  [4] 流式播报区（aria-live）")
        live = br.ctl.js(r"""(() => {
          return [...document.querySelectorAll('[aria-live]')].map(e => {
            const cs = getComputedStyle(e);
            const r = e.getBoundingClientRect();
            return {
              mode: e.getAttribute('aria-live'),
              atomic: e.getAttribute('aria-atomic'),
              display: cs.display,
              visibility: cs.visibility,
              w: Math.round(r.width), h: Math.round(r.height),
            };
          });
        })()""") or []
        if not live:
            print("    ★ 没有任何 aria-live —— 读屏用户听不到回答")
            bad += 1
        for l in live:
            # 【核心判据】必须"视觉不可见但仍在无障碍树里"
            # display:none / visibility:hidden 都会让它从无障碍树消失
            in_tree = l["display"] != "none" and l["visibility"] != "hidden"
            tiny = l["w"] <= 4 and l["h"] <= 4
            ok = in_tree and l["mode"] in ("polite", "assertive")
            bad += 0 if ok else 1
            print(f"    {'OK  ' if ok else '★   '}mode={l['mode']} atomic={l['atomic']} "
                  f"display={l['display']} visibility={l['visibility']} "
                  f"尺寸={l['w']}×{l['h']}")
            if not in_tree:
                print("        ★ 被 display/visibility 隐藏 → 读屏完全跳过，等于没做")
            elif not tiny:
                print("        ★ 尺寸不小，可能占据了布局，检查是否漏了 sr-only")

        print("\n  [5] 生成态 aria-busy")
        b = br.ctl.js(
            "(() => { const e=document.querySelector('.stage-scroll');"
            " return e ? e.getAttribute('aria-busy') : 'N/A'; })()")
        # 空闲时应为 false 或字符串 "false"，生成中为 true
        ok = b in (False, "false", "true", True)
        bad += 0 if ok else 1
        print(f"    {'OK  ' if ok else '★   '}.stage-scroll aria-busy = {b}")
    finally:
        br.close()
    print(f"\n  语义/读屏问题：{bad} 处")
    return bad


def check_forced_colors(port) -> int:
    """Windows 高对比度（forced-colors: active）：数据会不会消失。

    【为什么这一项比"文字看不清"更严重】
    forced-colors 会强制覆盖 background / background-image /
    color / fill / stroke，把页面换成用户选定的高对比配色。
    而本项目整套设计是"用亮度区分元素"：

        条形   = background: linear-gradient(三级灰)
        热力格 = background: rgba(...)
        折线   = stroke: var(--accent)   ← 白

    实测（未修前）：白线画在白底上 —— 整条折线消失；
    三档灰全合并 —— 条形只剩虚线轮廓；208 个热力格全白 —— 数据完全丢失。
    这不是"不好看"，是**图表变成空白**。

    【判据不能是"文字还可读"】
    必须直接量"两档不同的数据值，渲染后还能不能区分"：
    · 热力格：可区分的渲染签名数（正常 94 → HC 仍需 >1 且分档清晰）
    · 折线：stroke 颜色与画布底色的对比度（白底白线 = 1.0）
    · 条形：长度是否仍然编码数值（靠截图确认，脚本查 DOM 宽度）
    """
    section("高对比度模式：(forced-colors: active) 数据是否还在")
    bad = 0
    port += 1
    br = Browser(port, f"{BASE}/#/dashboard")
    try:
        br.ctl.wait_until("document.querySelectorAll('.bi-heat-c').length > 0", 60)
        time.sleep(1.2)

        PROBE = r"""(() => {
          const uniq = a => [...new Set(a)];
          const heat = [...document.querySelectorAll('.bi-heat-c:not(.is-empty)')];
          const heatSig = heat.map(e => {
            const s = getComputedStyle(e);
            const b = getComputedStyle(e, '::before');
            return s.backgroundColor + '|' + b.opacity + '|' + b.backgroundColor;
          }).filter(x => !x.startsWith('rgba(0, 0, 0, 0)'));
          const ops = uniq(heat.map(e => getComputedStyle(e, '::before').opacity));
          const line = document.querySelector('.pg-chart-svg path[stroke]');
          const bars = [...document.querySelectorAll('.bi-bar-f:not(.is-ghost)')]
            .slice(0, 8).map(e => Math.round(e.getBoundingClientRect().width));
          return {
            fc: matchMedia('(forced-colors: active)').matches,
            heat: heat.length,
            heatDistinct: uniq(heatSig).length,
            opLevels: ops.length,
            opSample: ops.slice(0, 4),
            lineStroke: line ? getComputedStyle(line).stroke : 'N/A',
            barWidths: bars,
            bodyBg: getComputedStyle(document.body).backgroundColor,
          };
        })()"""

        before = br.ctl.js(PROBE) or {}
        print(f"\n  [正常] 热力格 {before.get('heat')} 个 / 可区分 "
              f"{before.get('heatDistinct')} 种 / 折线 {before.get('lineStroke')}")

        # 切到高对比度
        br.ctl.send("Emulation.setEmulatedMedia", features=[
            {"name": "forced-colors", "value": "active"}])
        time.sleep(1.2)

        after = br.ctl.js(PROBE) or {}
        print(f"  [HC]   fc={after.get('fc')} / 可区分 {after.get('heatDistinct')} 种 / "
              f"opacity {after.get('opLevels')} 档 {after.get('opSample')}")
        print(f"  [HC]   折线 {after.get('lineStroke')} / 页面底色 {after.get('bodyBg')}")

        if not after.get("fc"):
            print("    ★ forced-colors 模拟未生效 —— 这一节没测到东西")
            bad += 1
        else:
            # 1) 折线必须与画布底色不同（白底白线 = 消失）
            stroke = str(after.get("lineStroke") or "")
            bg = str(after.get("bodyBg") or "")
            if stroke.replace(" ", "") == bg.replace(" ", ""):
                print(f"    ★ 折线色与底色相同（{stroke}）→ 线条不可见")
                bad += 1
            else:
                print(f"    OK   折线与底色可区分")

            # 2) 热力格必须仍有多个可区分档位（全黑 = 深浅信息丢失）
            if (after.get("heatDistinct") or 0) <= 1:
                print(f"    ★ 热力格只剩 {after.get('heatDistinct')} 种 → 208 格全一样")
                bad += 1
            else:
                print(f"    OK   热力格仍有 {after.get('heatDistinct')} 种可区分渲染")

            # 3) opacity 档位要足够多（这是 HC 下唯一的"深浅"通道）
            if (after.get("opLevels") or 0) <= 3:
                print(f"    ★ 强度只有 {after.get('opLevels')} 档，分不出强弱")
                bad += 1
            else:
                print(f"    OK   强度 {after.get('opLevels')} 档（opacity 承载深浅）")

            # 4) 条形长度必须仍然递增（长度是条形唯一的数据通道）
            ws_ = after.get("barWidths") or []
            if len(ws_) >= 3 and len(set(ws_)) <= 1:
                print(f"    ★ 所有条形宽度相同 {ws_[:4]} → 长度信息丢失")
                bad += 1
            elif ws_:
                print(f"    OK   条形宽度仍有差异 {ws_[:5]}")

            # 5) 【reviews 页单独验一遍】
            # 上面这些 HC 规则里，`.pg-chart-svg` / `.pg-cross` / `.pg-sel-box`
            # 这一组**两个页面都在用**（Dashboard.tsx 与 Reviews.tsx 各画一张图）。
            # 只在 dashboard 上量，等于假设同一套规则在另一张 svg 上也成立 ——
            # 而 svg 的 stroke/fill 是被各自 inline 写上去的，规则命中与否
            # 取决于元素结构，不取决于规则本身。
            br.ctl.js("location.hash = '#/reviews'")
            time.sleep(2.0)
            rv = br.ctl.js(r"""(() => {
              const line = document.querySelector('.pg-chart-svg path[stroke]');
              // 【量高度，不是宽度】reviews 页底部那排是"样本量"柱：
              // 宽度是常量（barW），数值编码在**高度**上。
              // 第一版量的是宽度，12 根全是 11px → 报了个假红。
              // 判据要跟着这张图的设计走：哪一维承载数据，就量哪一维。
              const rects = [...document.querySelectorAll('.pg-chart-svg rect')]
                .slice(0, 12)
                .map(e => ({w: Math.round(e.getBoundingClientRect().width),
                            h: Math.round(e.getBoundingClientRect().height)}));
              return {
                fc: matchMedia('(forced-colors: active)').matches,
                stroke: line ? getComputedStyle(line).stroke : 'N/A',
                bg: getComputedStyle(document.body).backgroundColor,
                nRect: rects.length,
                rectH: [...new Set(rects.map(r => r.h))].filter(h => h > 0).slice(0, 6),
                rectW: [...new Set(rects.map(r => r.w))].slice(0, 3),
              };
            })()""") or {}
            if not rv.get("fc"):
                print("    ★ reviews 页 HC 模拟未生效")
                bad += 1
            else:
                same = (str(rv.get("stroke") or "").replace(" ", "")
                        == str(rv.get("bg") or "").replace(" ", ""))
                bad += 1 if same else 0
                print(f"    {'★    ' if same else 'OK   '}reviews 折线 {rv.get('stroke')}"
                      f" / 底色 {rv.get('bg')}"
                      + ("（同色 → 线不可见）" if same else ""))
                rh = rv.get("rectH") or []
                ok_rect = len(rh) >= 3
                bad += 0 if ok_rect else 1
                print(f"    {'OK   ' if ok_rect else '★    '}reviews 样本柱"
                      f" {rv.get('nRect')} 根 / 高度档 {rh}"
                      f"（宽 {rv.get('rectW')} 为常量，数值看高度；应 ≥3 档）")
    finally:
        # 必须还原，否则影响后续章节
        try:
            br.ctl.send("Emulation.setEmulatedMedia", features=[])
        except Exception:
            pass
        br.close()
    print(f"\n  高对比度问题：{bad} 处")
    return bad


def check_bi_charts(port) -> int:
    """归因页四张新图：瀑布 / 环形 / 发散 / 象限。

    【为什么不验"图画出来了没"，只验"数字对不对"】
    「有个图」和「图是可信的」是两件事。四张图各有各的假法：
      · 瀑布  柱子高度和数值不成比例 / 加起来对不上总柱
      · 环形  扇区角度不按占比（画得好看但比例是错的）
      · 发散  正负方向画反（左侧是恶化还是改善读起来完全相反）
      · 象限  轴翻转（数值越大应该越靠上，写成越靠下）
    这些都能"渲染成功、截图好看、数字全错"，所以必须逐项对。

    【像素比例是唯一能验的东西】
    图上的数字标签可以照抄数据，但**柱长/角度**是浏览器按我们给的
    百分比算的。量的就是它 —— 数据变了图没跟着变，这里会红。
    """
    section("归因页四张图：瀑布闭合 / 环形比例 / 发散方向 / 象限轴向")
    bad = 0
    port += 1
    br = Browser(port, f"{BASE}/#/attribution", width=1440, height=1500)
    try:
        br.ctl.wait_until("!!document.querySelector('.bi-wa')", 90)
        time.sleep(1.5)
        # 后端数据，用来和图上像素对账
        wk = br.ctl.js("(location.hash.match(/week=(\\d+)/)||[])[1]") or None
        try:
            raw = urllib.request.urlopen(
                f"{BASE}/api/pages/attribution" + (f"?week={wk}" if wk else ""),
                timeout=60).read()
            api = json.loads(raw)
        except Exception as e:
            print(f"  ★ 取后端数据失败：{e}")
            return bad + 1

        cc = api.get("category_contribution") or []

        # ---- 1. 瀑布：台阶自洽 + 闭合 ----
        print("\n  [1] 瀑布：柱高按数值、各节相加 = 总柱")
        wa = br.ctl.js(r"""(() => {
          const cols=[...document.querySelectorAll('.bi-wa-col')];
          const ax=[...document.querySelectorAll('.bi-wa-al')];
          return cols.map((c,i)=>({
            lab: ax[i] ? ax[i].querySelector('span').innerText : '',
            val: c.querySelector('.bi-wa-val').innerText,
            h: c.querySelector('.bi-wa-bar').getBoundingClientRect().height,
            total: c.querySelector('.bi-wa-bar').className.includes('is-total'),
          }));
        })()""") or []
        # 1a 台阶数 = 类目数 + 结构效应 + 总柱
        expect_n = len(cc) + 1 + (1 if abs(api.get("structure_effect_pp") or 0) > 0.005 else 0)
        ok = len(wa) == expect_n
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}台阶 {len(wa)} 个（类目 {len(cc)} + 结构效应"
              f" + 总柱，应 {expect_n}）")
        # 1b 最后一根必须是总柱，且高度最大
        last = wa[-1] if wa else {}
        ok = bool(last.get("total"))
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}末柱是总柱（is-total={last.get('total')}）")
        if wa:
            hs = [x["h"] for x in wa]
            ok = abs(last["h"] - max(hs)) < 0.5
            bad += 0 if ok else 1
            print(f"    {'OK   ' if ok else '★    '}总柱最高 {round(last['h'])}px"
                  f"（各柱 {[round(h) for h in hs]}）")
        # 1c 柱高与数值单调：最大的那节应该最高（总柱除外）
        seg = [x for x in wa if not x["total"]]
        if len(seg) >= 2:
            def num(s):
                try:
                    return abs(float(s.replace("pp", "").replace("+", "")))
                except Exception:
                    return 0.0
            pairs = [(num(x["val"]), x["h"]) for x in seg]
            pairs_sorted = sorted(pairs, key=lambda t: t[0])
            mono = all(pairs_sorted[i][1] <= pairs_sorted[i + 1][1] + 1.5
                       for i in range(len(pairs_sorted) - 1))
            bad += 0 if mono else 1
            print(f"    {'OK   ' if mono else '★    '}柱高随数值单调递增 "
                  f"{[(round(a, 2), round(b)) for a, b in pairs_sorted]}")

        # ---- 2. 环形：角度按占比 ----
        print("\n  [2] 环形：扇区角度 ∝ 占比、图例同色、圆不变形")
        br.ctl.js("""[...document.querySelectorAll('.bi-seg-i')]
            .find(b=>b.innerText.trim()==='订单占比')?.click()""")
        time.sleep(1.3)
        dnut = br.ctl.js(r"""(() => {
          const segs=[...document.querySelectorAll('.bi-donut-seg')];
          const li=[...document.querySelectorAll('.bi-donut-li')];
          const svg=document.querySelector('.bi-donut-svg').getBoundingClientRect();
          return {
            n: segs.length,
            items: segs.map((s,i)=>({
              lab: li[i] ? li[i].querySelector('.bi-donut-n').innerText : '',
              pct: li[i] ? parseFloat(li[i].querySelector('b').innerText) : 0,
              fill: s.getAttribute('fill'),
              sw: li[i] ? getComputedStyle(li[i].querySelector('i')).backgroundColor : '',
              bb: (b=>({w:b.width, h:b.height}))(s.getBBox()),
              d: s.getAttribute('d'),
            })),
            w: svg.width, h: svg.height,
          };
        })()""") or {}
        ok = dnut.get("n") == len(cc)
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}扇区 {dnut.get('n')} 个（类目 {len(cc)} 个）")
        # 圆不能变形：宽高必须相等
        ok = abs((dnut.get("w") or 0) - (dnut.get("h") or 0)) < 1
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}环 {round(dnut.get('w') or 0)}x"
              f"{round(dnut.get('h') or 0)}（必须相等，否则是椭圆）")
        # 角度 ∝ 占比：从 path 里的端点坐标反推弧度
        import math
        angles = []
        for it in dnut.get("items") or []:
            nums = re.findall(r"-?\d+\.?\d*", it["d"] or "")
            if len(nums) < 8:
                angles.append(None)
                continue
            x0, y0 = float(nums[0]), float(nums[1])
            x1, y1 = float(nums[-2]), float(nums[-1])
            # 起止角（相对圆心 90,90）
            a0 = math.degrees(math.atan2(y0 - 90, x0 - 90))
            a1 = math.degrees(math.atan2(y1 - 90, x1 - 90))
            angles.append(((a0, a1), it["pct"]))
        # 用 bbox 宽高做交叉验证更稳：面积 ∝ 占比（宽松阈值，只抓"画错方向"）
        areas = [max(it["bb"]["w"] * it["bb"]["h"], 1) for it in dnut.get("items") or []]
        pcts = [it["pct"] for it in dnut.get("items") or []]
        if len(areas) >= 2:
            big_area = areas.index(max(areas))
            big_pct = pcts.index(max(pcts))
            ok = big_area == big_pct
            bad += 0 if ok else 1
            print(f"    {'OK   ' if ok else '★    '}最大扇区 = 最大占比"
                  f"（{dnut['items'][big_area]['lab']} {pcts[big_area]}%）")
        # 图例色块必须和扇区同色。
        # 【不能直接比字符串】扇区的 fill 是 `#9c9ca4`（十六进制，写在属性上），
        # 图例的 background 读回来是 `rgb(156, 156, 164)`（计算值）。
        # 第一版直接做 `hex in rgb_str` 的判断 —— 永远为假，
        # 于是 4 个类目全被报成"颜色不一致"。**断言写错比没断言更难查**，
        # 因为它给的是红灯，会让人去改一个本来没坏的东西。
        # 正确做法：两边都归一成 (r,g,b) 再比。
        def _rgb(s):
            s = (s or "").strip()
            if s.startswith("#"):
                h = s.lstrip("#")
                if len(h) == 3:
                    h = "".join(c * 2 for c in h)
                try:
                    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
                except Exception:
                    return None
            nums = re.findall(r"\d+", s)
            if len(nums) >= 3:
                return tuple(int(x) for x in nums[:3])
            return None

        mismatch = []
        for it in dnut.get("items") or []:
            a, b = _rgb(it["fill"]), _rgb(it["sw"])
            if a is not None and b is not None and a != b:
                mismatch.append(f"{it['lab']} 扇{it['fill']}≠图例{it['sw']}")
        ok = not mismatch
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}图例与扇区同色"
              f"{'' if ok else '（' + '；'.join(mismatch) + '）'}")

        # ---- 3. 发散：方向 ----
        print("\n  [3] 发散条：负值必须画在零轴左侧")
        br.ctl.js("""[...document.querySelectorAll('.bi-seg-i')]
            .find(b=>b.innerText.trim()==='差距')?.click()""")
        time.sleep(1.2)
        dv = br.ctl.js(r"""(() => {
          const card=[...document.querySelectorAll('.pg-card')]
            .find(c=>c.innerText.includes('类目贡献度'));
          if(!card) return null;
          return [...card.querySelectorAll('.bi-dv-row')].map(r=>({
            lab: r.querySelector('.bi-dv-l').innerText,
            val: r.querySelector('.bi-dv-v span').innerText,
            neg: r.querySelector('.bi-dv-bar').className.includes('neg'),
            w: r.querySelector('.bi-dv-bar').getBoundingClientRect().width,
          }));
        })()""") or []
        wrong = []
        for it in dv:
            v = 0.0
            try:
                v = float(it["val"].replace("pp", "").replace("+", ""))
            except Exception:
                pass
            if (v < 0) != it["neg"]:
                wrong.append(f"{it['lab']}({it['val']},neg={it['neg']})")
        ok = not wrong and bool(dv)
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}{len(dv)} 行方向"
              f"{'' if ok else '；错向：' + str(wrong)}")
        # 条长 ∝ 绝对值
        if dv:
            ls = [(abs(float(x["val"].replace("pp", "").replace("+", ""))), x["w"]) for x in dv]
            ls_sorted = sorted(ls)
            mono = all(ls_sorted[i][1] <= ls_sorted[i + 1][1] + 1.5
                       for i in range(len(ls_sorted) - 1))
            bad += 0 if mono else 1
            print(f"    {'OK   ' if mono else '★    '}条长随绝对值递增 "
                  f"{[(round(a, 1), round(b)) for a, b in ls_sorted]}")

        # ---- 4. 象限：轴向与标签 ----
        print("\n  [4] 象限：y 越大越靠上、标签不重叠不越界")
        br.ctl.js("""[...document.querySelectorAll('.bi-seg-i')]
            .find(b=>b.innerText.trim()==='损失×可再售')?.click()""")
        time.sleep(1.4)
        q = br.ctl.js(r"""(() => {
          const dots=[...document.querySelectorAll('.bi-q-dot')];
          const labs=[...document.querySelectorAll('.bi-q-lab')];
          const plot=document.querySelector('.bi-q-plot').getBoundingClientRect();
          const L=labs.map(l=>{const b=l.getBoundingClientRect();
            return {t:l.innerText, x:b.left, y:b.top, w:b.width, h:b.height};});
          const ov=[];
          for(let i=0;i<L.length;i++) for(let j=i+1;j<L.length;j++){
            const a=L[i], b=L[j];
            const ox=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x);
            const oy=Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y);
            if(ox>1&&oy>1) ov.push(a.t+'×'+b.t);
          }
          return {
            n: dots.length,
            pts: dots.map(d=>({lab:d.querySelector('.bi-q-lab')?.innerText || '',
                               top:parseFloat(d.style.top),
                               w:parseFloat(d.style.width)})),
            overlaps: ov,
            outside: L.filter(l=>l.x < plot.left-2 || l.x+l.w > plot.right+90).map(l=>l.t),
          };
        })()""") or {}
        ok = q.get("n") == len(api.get("structure", {}).get("items") or [])
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}气泡 {q.get('n')} 个")
        # y 轴翻转核对：可再售率最高的点，top 应该最小
        items = api.get("structure", {}).get("items") or []
        if items and q.get("pts"):
            hi = max(items, key=lambda x: x["resellable"])
            rec = max(q["pts"], key=lambda p: p["w"])   # 无法直接对应，改验范围
            tops = [p["top"] for p in q["pts"]]
            ok = all(-1 <= t <= 101 for t in tops)
            bad += 0 if ok else 1
            print(f"    {'OK   ' if ok else '★    '}所有点落在图内 "
                  f"top∈[{min(tops):.0f}%, {max(tops):.0f}%]")
            print(f"          （可再售最高的是「{hi['l2']}」{hi['resellable']}%，应在图顶部）")
        ok = not q.get("overlaps")
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}标签无重叠"
              f"{'' if ok else '（' + str(q['overlaps']) + '）'}")
        ok = not q.get("outside")
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}标签未跑出图外"
              f"{'' if ok else '（' + str(q['outside']) + '）'}")

        # ---- 5. 斜率图（类目：盘子大小 vs 差距）----
        # 这张图存在的理由就是"两列排名会错位"，所以必须验到：
        #   · 左右节点数相等且等于行数（只有半边等于画了一半）
        #   · 线数等于行数
        #   · 线宽是固定像素 —— 用 viewBox 百分比坐标 + preserveAspectRatio=none
        #     时 strokeWidth 会被横向拉伸，实测 1.3 变手指粗的白条，
        #     正好盖住"是否交叉"这个唯一看点
        print("\n  [5] 斜率图：左右成对 / 线宽未被 viewBox 拉伸")
        br.ctl.js("""[...document.querySelectorAll('.bi-seg-i')]
            .find(b=>b.innerText.includes('规模'))?.click()""")
        time.sleep(1.3)
        sl = br.ctl.js(r"""(() => {
          const nodes=[...document.querySelectorAll('.bi-slope-node')];
          const lines=[...document.querySelectorAll('.bi-slope-line')];
          return {
            left: nodes.filter(n=>n.className.includes(' l')).length,
            right: nodes.filter(n=>n.className.includes(' r')).length,
            lines: lines.length,
            labs: nodes.map(n=>(n.querySelector('.bi-slope-lab')||{}).innerText).filter(Boolean),
            sw: lines.length ? getComputedStyle(lines[0]).strokeWidth : null,
          };
        })()""") or {}
        n_cc = len(cc)
        ok = (sl.get("left") == n_cc and sl.get("right") == n_cc
              and sl.get("lines") == n_cc and n_cc >= 2)
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}左{sl.get('left')}/右{sl.get('right')}"
              f"/线{sl.get('lines')}（类目 {n_cc} 个）· 名 {sl.get('labs')}")
        try:
            swf = float(str(sl.get("sw")).replace("px", ""))
        except Exception:
            swf = 0
        ok = 0 < swf <= 3.5
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}线宽 {sl.get('sw')}"
              f"（>3.5px 说明被 viewBox 横向拉伸了）")

        # ---- 6. 双环（原因：条数份额 vs 金额份额）----
        # 判据是"两环各自必须合计 100%"：
        # 后端 items 是 Top-N（第 44 周只给 8 类），而 total 是全部，
        # 实测条数 3807/3880、损失 245327/248503。
        # 直接拿 total 当分母，环只画到 98% —— 缺口没名字，看着像渲染坏了。
        # 前端补了一节「其余未单列」，这里量的就是它补没补对。
        print("\n  [6] 双环：两环各自合计 100% / 口径差自洽 / 中心是绝对值")
        br.ctl.js("""[...document.querySelectorAll('.bi-seg-i')]
            .find(b=>b.innerText.includes('条数'))?.click()""")
        time.sleep(1.3)
        dr = br.ctl.js(r"""(() => {
          const card=[...document.querySelectorAll('.pg-card')]
            .find(c=>(c.querySelector('.pg-card-t')||{}).innerText?.includes('卖的东西'));
          if(!card) return null;
          const lis=[...card.querySelectorAll('.bi-dring-li')];
          const rows=lis.map(b=>{
            const bs=[...b.querySelectorAll('b')].map(e=>parseFloat(e.innerText));
            return {name:(b.querySelector('.bi-dring-n')||{}).innerText,
                    outer:bs[0], inner:bs[1],
                    diff:(b.querySelector('em')||{}).innerText};
          });
          return {
            segs: card.querySelectorAll('.bi-dring-seg').length,
            n: lis.length, rows,
            sumO: rows.reduce((s,r)=>s+(r.outer||0),0),
            sumI: rows.reduce((s,r)=>s+(r.inner||0),0),
            mid: (card.querySelector('.bi-dring-mid')||{}).innerText || '',
          };
        })()""") or {}
        ok = (dr.get("segs") or 0) == (dr.get("n") or 0) * 2 and (dr.get("n") or 0) >= 2
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}扇区 {dr.get('segs')}"
              f"（{dr.get('n')} 项 × 2 环）")
        so, si = dr.get("sumO") or 0, dr.get("sumI") or 0
        # 容差 1.0pp：图例保留 1 位小数，9 项各截断一次最多累积 ~0.45pp
        ok = abs(so - 100) < 1.0 and abs(si - 100) < 1.0
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}条数合计 {so:.1f}% · 金额合计 {si:.1f}%"
              f"（应为 100）{'缺口没补，环上有无名空档' if not ok else ''}")
        # 口径差必须真的等于 inner - outer
        mism = [r for r in (dr.get("rows") or [])
                if r.get("outer") is not None and r.get("inner") is not None
                and abs((r["inner"] - r["outer"])
                        - float(str(r.get("diff") or "0").replace("+", ""))) > 0.25]
        ok = not mism and (dr.get("rows") or [])
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}口径差与两列相符"
              f"{'' if ok else '（' + str(len(mism)) + ' 行对不上）'}")
        # 中心必须是绝对值：两环各 100%，中心再写 100% 是废话
        mid = dr.get("mid") or ""
        ok = "100" not in mid and bool(mid.strip())
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}中心写绝对值 "
              f"{mid.replace(chr(10), ' / ')}")
    finally:
        br.close()
    print(f"\n  归因图表问题：{bad} 处")
    return bad


def check_attribution_views(port) -> int:
    """归因页两块卡片的全部视图：切得过去、画得出来、点得中。

    【为什么这一节要"逐个视图"验，而不是抽查一个】
    这两块卡的视图是互斥渲染的（三元表达式切分支），
    一个视图坏了，其余视图完全看不出来 ——
    切到那一栏才会发现是空白，而没人会每天都把 11 个视图点一遍。

    【判据：图形元素数 > 3】
    只验"容器存在"是不够的：`<div>` 永远在，里面可以是空的。
    数图形元素才能区分"画出来了"和"容器在但没内容"。

    【踩过的坑：不能只数 svg 里的元素】
    本项目这几张图有两种实现：
      · Donut / DualRing / Slope  用 SVG
      · Waterfall / Diverging / Quadrant / Treemap  用 HTML + CSS
    第一版只查 `svg path, rect, ...`，于是四张 HTML 图全被判"空白"，
    而它们其实渲染得好好的。判据必须同时看 svg 图形和 HTML 子元素。
    """
    section("归因页两块的视图：逐个切换、渲染、可点")
    bad = 0
    port += 1
    br = Browser(port, f"{BASE}/#/attribution", width=1440, height=1200)
    try:
        br.ctl.wait_until("document.querySelectorAll('.pg-card').length > 4", 60)
        time.sleep(1.5)

        cards = br.ctl.js(r"""(() => {
          const out = [];
          [...document.querySelectorAll('.pg-card')].forEach((c, i) => {
            const segs = [...c.querySelectorAll('.bi-seg .bi-seg-i')];
            if (!segs.length) return;
            out.push({ i, title: (c.querySelector('.pg-card-t')?.innerText || '').slice(0, 20),
                       labels: segs.map(b => b.innerText) });
          });
          return out;
        })()""") or []
        if len(cards) < 2:
            print(f"  ★ 只找到 {len(cards)} 张带视图切换的卡片（应为 2）")
            return bad + 1

        for card in cards:
            idx = card["i"]
            print(f"\n  [{card['title']}] {len(card['labels'])} 个视图")
            for k, label in enumerate(card["labels"]):
                br.ctl.js(f"[...document.querySelectorAll('.pg-card')][{idx}]"
                          f".querySelectorAll('.bi-seg .bi-seg-i')[{k}].click()")
                time.sleep(0.55)
                r = br.ctl.js(r"""(() => {
                  const c = [...document.querySelectorAll('.pg-card')][%d];
                  const svg = c.querySelector('svg');
                  const svgN = svg
                    ? svg.querySelectorAll('path, rect, circle, polygon, polyline, line, text').length
                    : 0;
                  /* HTML 实现的图：数"有尺寸的直接子元素"。
                     不能数所有后代 —— 卡片头、note、分段控件都在里面。 */
                  const body = c.querySelector('.pg-card-b') || c;
                  const htmlN = [...body.querySelectorAll('div, span, button')]
                    .filter(e => {
                      const b = e.getBoundingClientRect();
                      return b.width > 4 && b.height > 4;
                    }).length;
                  const rows = body.querySelectorAll('table tr').length;
                  return {
                    svgN, htmlN, rows,
                    btn: c.querySelectorAll('button').length,
                    empty: /没有|无法|暂无数据/.test(body.innerText),
                  };
                })()""" % idx) or {}
                drawn = (r.get("svgN") or 0) + (r.get("rows") or 0) + (r.get("htmlN") or 0)
                ok = drawn > 3 and not r.get("empty")
                bad += 0 if ok else 1
                print(f"    {'OK   ' if ok else '★    '}{label:<12}"
                      f"图形 {r.get('svgN', 0):>3} · HTML {r.get('htmlN', 0):>4}"
                      f" · 表格行 {r.get('rows', 0):>2} · 按钮 {r.get('btn', 0):>2}"
                      f"{'  ← 疑似空白' if not ok else ''}")

        # ---- 钉住（onPick）必须真的在起作用 ----
        # 卡片说明写着"点选可钉住，切视图和明细都会保留"。
        # 这句话曾经在「规模×差距」视图上是假的：Slope 两端都是 button，
        # 有 hover/focus，却没有 onClick。
        print("\n  [钉住] 点选后切视图，选中状态要保留")
        # 先切到「规模×差距」（第 4 个视图，index 4）
        br.ctl.js(f"[...document.querySelectorAll('.pg-card')][{cards[0]['i']}]"
                  ".querySelectorAll('.bi-seg .bi-seg-i')[4].click()")
        time.sleep(0.6)
        # 点左端第一个节点
        clicked = br.ctl.js(r"""(() => {
          const n = document.querySelector('.bi-slope-node.l');
          if (!n) return null;
          n.click();
          return n.innerText.split('\n')[0];
        })()""")
        time.sleep(0.5)
        pinned1 = br.ctl.js("document.querySelectorAll('.bi-slope-node.is-pick').length")
        okP1 = bool(clicked) and (pinned1 or 0) >= 2
        bad += 0 if okP1 else 1
        print(f"    {'OK   ' if okP1 else '★    '}斜率图点「{clicked}」→ "
              f"{pinned1} 个节点钉住（两端应同时亮）")

        # 切到明细，同一行要带 is-pick
        br.ctl.js(f"[...document.querySelectorAll('.pg-card')][{cards[0]['i']}]"
                  ".querySelectorAll('.bi-seg .bi-seg-i')[" + str(len(cards[0]['labels']) - 1) + "].click()")
        time.sleep(0.6)
        keep = br.ctl.js("document.querySelectorAll('.pg-table tr.is-pick').length")
        okP2 = (keep or 0) >= 1
        bad += 0 if okP2 else 1
        print(f"    {'OK   ' if okP2 else '★    '}切到明细后仍高亮 {keep} 行（选中要跟着视图走）")

        # ---- 哑铃图：移到点上要能读数 ----
        # 【为什么单独验这个位置，而不是验"图里随便悬停一下"】
        # 哑铃图的两个点是 pointer-events: none（不然会挡住行的 hover），
        # 于是鼠标落在点上时事件目标是行。第一版只把 show 挂在
        # 类目名按钮上 —— 悬停类目名有浮层，**悬停点反而没有**，
        # 而点恰恰是最自然想去看的地方。这种"部分区域无响应"
        # 只有把坐标精确打到点上才测得出来。
        print("\n  [哑铃图] 移到点上要能读数")
        br.ctl.js(f"[...document.querySelectorAll('.pg-card')][{cards[0]['i']}]"
                  ".querySelectorAll('.bi-seg .bi-seg-i')[1].click()")
        time.sleep(0.8)
        dot = br.ctl.js(r"""(() => {
          const d = document.querySelector('.bi-db-dot.is-now');
          if (!d) return null;
          const b = d.getBoundingClientRect();
          return {x: b.x + b.width / 2, y: b.y + b.height / 2};
        })()""")
        if not dot:
            print("    ★ 找不到哑铃图的点")
            bad += 1
        else:
            # 先移开再移上去：同一坐标连续两次 mouseMoved 不会触发事件
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                        x=dot["x"] - 30, y=dot["y"] - 30)
            time.sleep(0.15)
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                        x=dot["x"], y=dot["y"])
            time.sleep(0.7)
            tip = br.ctl.js("(document.querySelector('.bi-tip')||{}).innerText") or ""
            okD1 = "基准" in tip and "本周" in tip and "差距" in tip
            bad += 0 if okD1 else 1
            print(f"    {'OK   ' if okD1 else '★    '}点上的浮层 "
                  f"{tip.replace(chr(10), ' · ')[:66] if tip else '没出来'}")
            hl = br.ctl.js("document.querySelectorAll('.bi-db-row.on').length")
            okD2 = (hl or 0) == 1
            bad += 0 if okD2 else 1
            print(f"    {'OK   ' if okD2 else '★    '}属于哪一行要能看出来（高亮 {hl} 行）")

        # ---- 堆叠份额条：挽回潜力 ----
        print("\n  [堆叠条] 可挽回 / 净损失 两段相加 = 总量")
        br.ctl.js(f"[...document.querySelectorAll('.pg-card')][{cards[-1]['i']}]"
                  ".querySelectorAll('.bi-seg .bi-seg-i')"
                  ".forEach(b => { if (b.innerText.trim() === '挽回潜力') b.click() })")
        time.sleep(1.0)
        ss = br.ctl.js(r"""(() => {
          const rows = [...document.querySelectorAll('.bi-ss-row')];
          return {
            n: rows.length,
            rows: rows.map(r => ({
              label: r.querySelector('.bi-ss-l').innerText,
              bar: parseFloat(r.querySelector('.bi-ss-bar').style.width) || 0,
              keep: parseFloat(r.querySelector('.bi-ss-keep').style.width) || 0,
              pct: r.querySelector('.bi-ss-pct') ? r.querySelector('.bi-ss-pct').innerText : '',
              tabIndex: r.tabIndex,
              role: r.getAttribute('role'),
            })),
            head: (document.querySelector('.bi-ss-head') || {}).innerText || '',
          };
        })()""") or {}
        okS1 = (ss.get("n") or 0) >= 3
        bad += 0 if okS1 else 1
        print(f"    {'OK   ' if okS1 else '★    '}行数 {ss.get('n')}")

        # 1) 可挽回段必须是全条的一个部分，不能超过 100%
        over = [r for r in (ss.get("rows") or []) if r["keep"] > 100.01 or r["keep"] < -0.01]
        okS2 = not over
        bad += 0 if okS2 else 1
        print(f"    {'OK   ' if okS2 else '★    '}可挽回段占比全部在 0–100%"
              f"{'  越界: ' + str(over[:2]) if over else ''}")

        # 2) 百分比必须和段宽一致（差 1 以内是取整）
        mism = [r for r in (ss.get("rows") or [])
                if r["pct"] and abs(float(r["pct"].rstrip('%')) - r["keep"]) > 1.01]
        okS3 = not mism
        bad += 0 if okS3 else 1
        print(f"    {'OK   ' if okS3 else '★    '}标注百分比 = 段宽"
              f"{'  不一致: ' + str(mism[:2]) if mism else ''}")

        # 3) 【百分比不能被挤出可见区】
        # 第一版把 % 跟在条后面，条一长就推到了轨道外 ——
        # 4 行的比例直接看不见。这里量的是"数字是否落在卡片内"。
        clipped = br.ctl.js(r"""(() => {
          const card = [...document.querySelectorAll('.pg-card')].find(c => c.querySelector('.bi-ss'));
          if (!card) return null;
          const cb = card.getBoundingClientRect();
          return [...card.querySelectorAll('.bi-ss-pct')].filter(e => {
            const b = e.getBoundingClientRect();
            return b.right > cb.right - 2 || b.width < 4;
          }).length;
        })()""")
        okS4 = (clipped or 0) == 0
        bad += 0 if okS4 else 1
        print(f"    {'OK   ' if okS4 else '★    '}百分比全部可见（被裁 {clipped} 个）")

        # 3b) 【最后一行必须落在卡片内】
        # 卡片是 overflow:hidden。行数一多（8 条原因）就可能被裁掉底部，
        # 而裁掉的那几条在截图上看起来只是"没画"，
        # 很容易被当成数据就这么少。这一条量的是几何，不是观感。
        geo = br.ctl.js(r"""(() => {
          const card = document.querySelector('.bi-ss')?.closest('.pg-card');
          if (!card) return null;
          const cb = card.getBoundingClientRect();
          const rows = [...card.querySelectorAll('.bi-ss-row')];
          if (!rows.length) return null;
          const last = rows[rows.length - 1].getBoundingClientRect();
          return {rows: rows.length, over: Math.round(last.bottom - cb.bottom)};
        })()""") or {}
        okS4b = (geo.get("over") or 0) <= 0
        bad += 0 if okS4b else 1
        print(f"    {'OK   ' if okS4b else '★    '}{geo.get('rows')} 行全部在卡片内"
              f"（末行{'超出 ' + str(geo.get('over')) + 'px' if not okS4b else '留白 ' + str(-(geo.get('over') or 0)) + 'px'}）")

        # 4) 整体口径要写出来
        okS5 = "可挽回" in (ss.get("head") or "") and "占损失" in (ss.get("head") or "")
        bad += 0 if okS5 else 1
        print(f"    {'OK   ' if okS5 else '★    '}表头给出总额 "
              f"{(ss.get('head') or '').replace(chr(10), ' ')[:44]}")

        # 5) 可点的行必须能聚焦（键盘要够得到"先救哪个"）
        if ss.get("rows"):
            r0 = ss["rows"][0]
            # 【别写 `r0["tabIndex"] or -1`】
            # tabIndex=0 是合法值，但 `0 or -1` 在 Python 里返回 -1，
            # 于是这条永远判失败 —— 而屏幕上明明是对的。
            # 这是"断言写错"，不是"功能坏了"，但两者的报错长得一样。
            ti = r0["tabIndex"]
            ti = -1 if ti is None else ti
            okS6 = r0["role"] == "button" and ti >= 0
            bad += 0 if okS6 else 1
            print(f"    {'OK   ' if okS6 else '★    '}可聚焦 role={r0['role']} tabIndex={ti}")

        # ---- 分段控件不能被挤出卡片 ----
        # 视图从 5 个加到 6 个之后，标题栏可能放不下
        print("\n  [布局] 分段控件不溢出卡片")
        for card in cards:
            r = br.ctl.js(r"""(() => {
              const c = [...document.querySelectorAll('.pg-card')][%d];
              const seg = c.querySelector('.bi-seg');
              if (!seg) return null;
              const sb = seg.getBoundingClientRect();
              const cb = c.getBoundingClientRect();
              return {segR: Math.round(sb.right), cardR: Math.round(cb.right),
                      over: sb.right > cb.right + 1};
            })()""" % card["i"]) or {}
            okL = not r.get("over")
            bad += 0 if okL else 1
            print(f"    {'OK   ' if okL else '★    '}{card['title'][:14]}："
                  f"分段右 {r.get('segR')} / 卡片右 {r.get('cardR')}"
                  f"{'  ← 溢出' if r.get('over') else ''}")
    finally:
        br.close()
    print(f"\n  归因页视图问题：{bad} 处")
    return bad


def check_reviews_chart(port) -> int:
    """评论页「分周情感走势」：悬停读数 / 拖选区间 / 快捷选区 / Esc。

    【为什么这张图要单独一节】
    它是全站最后一张"看着能交互、其实不能"的图：
    复用了 .pg-chart 类，于是继承了 cursor:crosshair ——
    鼠标移上去变成十字光标，暗示"这里可以框选"，
    但组件里一个事件都没挂。**承诺了不存在的交互，比看起来静态更糟**：
    用户会以为是自己没点对，而不是这里没有功能。

    【判据不是"有没有浮层"，而是"数字对不对"】
    悬停浮层里的值必须和图上的数据对得上，否则只是个装饰。
    所以这里顺手校验：负评率 + 正评率 ≈ 100（两者互斥），
    以及抽样误差 > 0（样本量真的参与了计算）。
    """
    section("评论页分周走势：悬停 / 拖选 / 快捷选区 / Esc")
    bad = 0
    port += 1
    br = Browser(port, f"{BASE}/#/reviews", width=1440, height=1050)
    try:
        br.ctl.wait_until("document.querySelectorAll('.pg-chart-svg path').length > 1", 60)
        time.sleep(1.5)
        br.ctl.js("document.querySelector('.pg-chart').scrollIntoView({block:'center'})")
        time.sleep(0.6)
        g = br.ctl.js(r"""(() => {
          const b = document.querySelector('.pg-chart-svg').getBoundingClientRect();
          return {x: b.x, y: b.y, w: b.width, h: b.height};
        })()""") or {}
        if not g.get("w"):
            print("  ★ 取不到图尺寸")
            return bad + 1

        # ---- 1. 悬停：浮层要出来，数字要自洽 ----
        print("\n  [1] 悬停读数")
        br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                    x=g["x"] + g["w"] * 0.42, y=g["y"] + g["h"] * 0.5)
        time.sleep(0.7)
        tip = br.ctl.js("(document.querySelector('.bi-tip')||{}).innerText") or ""
        ok1 = bool(tip) and "负评率" in tip and "样本" in tip and "抽样误差" in tip
        bad += 0 if ok1 else 1
        print(f"    {'OK   ' if ok1 else '★    '}浮层 "
              f"{tip.replace(chr(10), ' · ')[:70] if tip else '没出来'}")

        # 数字自洽：负评 + 正评 应当约等于 100（两类互斥）
        nums = br.ctl.js(r"""(() => {
          const t = document.querySelector('.bi-tip');
          if (!t) return null;
          const m = t.innerText.match(/([\d.]+)%/g) || [];
          return m.map(s => parseFloat(s));
        })()""") or []
        if len(nums) >= 2:
            s = nums[0] + nums[1]
            ok1b = abs(s - 100) < 1.5
            bad += 0 if ok1b else 1
            print(f"    {'OK   ' if ok1b else '★    '}负评 {nums[0]}% + 正评 {nums[1]}%"
                  f" = {s:.2f}%（应≈100）")
        # 准星要跟着出现（否则读者无法把浮层对回哪一周）
        ok1c = bool(br.ctl.js("!!document.querySelector('.pg-cross')"))
        bad += 0 if ok1c else 1
        print(f"    {'OK   ' if ok1c else '★    '}准星出现")
        # 原生 <title> 必须清掉：否则会和自绘浮层同时弹两层提示
        n_title = br.ctl.js("document.querySelectorAll('.pg-chart-svg title').length")
        ok1d = (n_title or 0) == 0
        bad += 0 if ok1d else 1
        print(f"    {'OK   ' if ok1d else '★    '}原生 title 残留 {n_title} 个（应为 0）")

        # ---- 2. 拖选 ----
        print("\n  [2] 拖选区间")
        y = g["y"] + g["h"] * 0.5
        x0 = g["x"] + g["w"] * 0.20
        x1 = g["x"] + g["w"] * 0.55
        br.ctl.send("Input.dispatchMouseEvent", type="mousePressed",
                    button="left", clickCount=1, x=x0, y=y)
        for k in range(1, 9):
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved", button="left",
                        x=x0 + (x1 - x0) * k / 8, y=y)
            time.sleep(0.04)
        br.ctl.send("Input.dispatchMouseEvent", type="mouseReleased",
                    button="left", clickCount=1, x=x1, y=y)
        time.sleep(0.9)
        bar = br.ctl.js("(document.querySelector('.pg-selbar')||{}).innerText") or ""
        ok2 = bool(bar) and "负评均值" in bar and "趋势" in bar and "样本" in bar
        bad += 0 if ok2 else 1
        print(f"    {'OK   ' if ok2 else '★    '}摘要 "
              f"{bar.replace(chr(10), ' · ')[:100] if bar else '没出来'}")
        ok2b = bool(br.ctl.js("!!document.querySelector('.pg-sel-box')"))
        bad += 0 if ok2b else 1
        print(f"    {'OK   ' if ok2b else '★    '}选区框留在图上")
        ok2c = not br.ctl.js("!!document.querySelector('.pg-selgo')")
        bad += 0 if ok2c else 1
        print(f"    {'OK   ' if ok2c else '★    '}快捷选区已让位（同一块空间不说两句话）")

        # ---- 3. Esc 清除 ----
        print("\n  [3] Esc 清除")
        for kind in ("rawKeyDown", "keyUp"):
            br.ctl.send("Input.dispatchKeyEvent", type=kind, key="Escape", code="Escape",
                        windowsVirtualKeyCode=27, nativeVirtualKeyCode=27)
        time.sleep(0.5)
        gone = (not br.ctl.js("!!document.querySelector('.pg-selbar')")
                and not br.ctl.js("!!document.querySelector('.pg-sel-box')"))
        bad += 0 if gone else 1
        print(f"    {'OK   ' if gone else '★    '}摘要和选区框都已清除")

        # ---- 4. 快捷选区：键盘/触屏唯一的入口 ----
        print("\n  [4] 快捷选区按钮")
        n_btn = br.ctl.js("document.querySelectorAll('.pg-selgo-b').length")
        ok4 = (n_btn or 0) >= 3
        bad += 0 if ok4 else 1
        print(f"    {'OK   ' if ok4 else '★    '}{n_btn} 个按钮")
        # 按钮必须真的能聚焦（键盘用户到达路径）
        br.ctl.js(r"""(() => {
          const b = document.querySelectorAll('.pg-selgo-b')[0];
          if (b) b.focus();
          return true;
        })()""")
        time.sleep(0.2)
        focused = br.ctl.js(r"""(() => {
          const a = document.activeElement;
          return a ? a.className : null;
        })()""")
        ok4b = "pg-selgo-b" in (focused or "")
        bad += 0 if ok4b else 1
        print(f"    {'OK   ' if ok4b else '★    '}可聚焦（activeElement={focused}）")
        # 点一下要出结果
        br.ctl.js("document.querySelector('.pg-selgo-b').click()")
        time.sleep(0.6)
        bar2 = br.ctl.js("(document.querySelector('.pg-selbar')||{}).innerText") or ""
        ok4c = bool(bar2) and "周" in bar2
        bad += 0 if ok4c else 1
        print(f"    {'OK   ' if ok4c else '★    '}点击出结果 "
              f"{bar2.replace(chr(10), ' · ')[:80] if bar2 else '没出来'}")

        # ---- 5. 点少时不给假精度 ----
        print("\n  [5] 点太少时，趋势要自己承认不可靠")
        # 「近 4 周」= 4 个点，斜率噪声大，必须带提示
        br.ctl.js("document.querySelector('.pg-selbar-x').click()")
        time.sleep(0.4)
        br.ctl.js(r"""(() => {
          const b = [...document.querySelectorAll('.pg-selgo-b')]
            .find(x => x.innerText.includes('4'));
          if (b) b.click();
          return true;
        })()""")
        time.sleep(0.6)
        bar3 = br.ctl.js("(document.querySelector('.pg-selbar')||{}).innerText") or ""
        ok5 = "仅看方向" in bar3
        bad += 0 if ok5 else 1
        print(f"    {'OK   ' if ok5 else '★    '}近 4 周带「点少，仅看方向」提示 "
              f"{'✓' if ok5 else '（缺，会让人拿假精度去开会）'}")

        # ---- 6. 底部样本量柱要有圆角 ----
        #        这张图是全站唯一用 SVG <rect> 画柱的地方，所以 CSS 里的
        #        --r-bar 管不到它 —— 全站条形统一时它被漏在外面，
        #        52 根 10px 宽的方柱贴在坐标轴下面。
        #        这里只判"有圆角且不为 0"：rx 写在 viewBox 坐标里，
        #        且 preserveAspectRatio="none" 会把它横向拉伸，
        #        换算成像素没有意义，能证明"不是直角"就够。
        print("\n  [6] 底部样本量柱不是直角")
        rxs = br.ctl.js(r"""(() => {
          const rects = [...document.querySelectorAll('.pg-chart-svg rect')]
            .filter(x => !x.getAttribute('class')
                      && parseFloat(x.getAttribute('width')) < 20);
          return {n: rects.length,
                  rx: rects.length ? parseFloat(rects[0].getAttribute('rx')) : null};
        })()""") or {}
        ok6 = (rxs.get("n") or 0) > 10 and (rxs.get("rx") or 0) >= 1.5
        bad += 0 if ok6 else 1
        print(f"    {'OK   ' if ok6 else '★    '}{rxs.get('n')} 根柱，rx={rxs.get('rx')}（应 ≥1.5）")
    finally:
        br.close()
    print(f"\n  评论走势图问题：{bad} 处")
    return bad


def check_treemap(port) -> int:
    """归因页「归属树图」：布局是否无重叠、面积是否忠于数据、键盘够不够得到。

    【为什么树图必须单独验布局】
    树图的两个失败模式都"看起来正常"：

      1. 格子重叠 —— 绝对定位算错时，后画的格子会盖住前一个。
         文字还在、颜色还在，只是少了几块，肉眼很难发现
         （被盖住的那个格子连名字都看不到，你不会注意到少了一个）。
      2. 面积不等于数值 —— 这是树图的**全部意义**。
         面积和金额脱钩之后，图还在，但它说的已经不是数据了。
         这种错最危险：读者会按面积判断"哪个最贵"，而那是错的。

    所以这里不验"图有没有画出来"，验的是几何事实。
    """
    section("归因页归属树图：无重叠 / 面积忠于金额 / 可聚焦")
    bad = 0
    port += 1
    br = Browser(port, f"{BASE}/#/attribution", width=1440, height=1100)
    try:
        br.ctl.wait_until("document.querySelectorAll('.bi-seg').length > 1", 60)
        time.sleep(1.2)
        ok = br.ctl.js(r"""(() => {
          for (const s of document.querySelectorAll('.bi-seg')) {
            const b = [...s.querySelectorAll('button')]
              .find(x => x.innerText.trim() === '归属树图');
            if (b) { b.click(); return true; }
          }
          return false;
        })()""")
        if not ok:
            print("  ★ 找不到「归属树图」分段按钮")
            return bad + 1
        time.sleep(1.2)
        br.ctl.js("document.querySelector('.bi-tree')?.scrollIntoView({block:'center'})")
        time.sleep(0.5)

        r = br.ctl.js(r"""(() => {
          const fig = document.querySelector('.bi-tree-fig');
          if (!fig) return {err: 'no fig'};
          const fb = fig.getBoundingClientRect();
          const cells = [...document.querySelectorAll('.bi-tree-c')].map(e => {
            const b = e.getBoundingClientRect();
            return {label: e.querySelector('.bi-tree-cl')?.innerText || '',
                    val: e.querySelector('.bi-tree-cv')?.innerText || '',
                    x: b.x - fb.x, y: b.y - fb.y, w: b.width, h: b.height,
                    area: b.width * b.height,
                    aria: e.getAttribute('aria-label') || ''};
          });
          const groups = [...document.querySelectorAll('.bi-tree-g')].map(e => {
            const b = e.getBoundingClientRect();
            return {name: e.querySelector('.bi-tree-gn')?.innerText || '',
                    total: e.querySelector('.bi-tree-gh b')?.innerText || '',
                    pct: e.querySelector('.bi-tree-gh em')?.innerText || '',
                    area: b.width * b.height};
          });
          return {W: fb.width, H: fb.height, cells, groups};
        })()""") or {}
        if r.get("err") or not r.get("cells"):
            print(f"  ★ 树图没渲染出来：{r.get('err') or '无格子'}")
            return bad + 1

        cells, groups = r["cells"], r["groups"]
        print(f"\n  [1] 结构：{len(groups)} 个归属 / {len(cells)} 个格子"
              f"（画布 {round(r['W'])}x{round(r['H'])}）")
        for g in groups:
            print(f"      {g['name']:<8} 合计 {g['total']:>8}  {g['pct']:>4}")

        # ---- 2. 无重叠。阈值 0.5px：相邻格子共享边界时会有亚像素重叠 ----
        print("\n  [2] 格子不重叠")
        ov = []
        for i in range(len(cells)):
            for j in range(i + 1, len(cells)):
                a, b = cells[i], cells[j]
                if (a["x"] < b["x"] + b["w"] - 0.5
                        and b["x"] < a["x"] + a["w"] - 0.5
                        and a["y"] < b["y"] + b["h"] - 0.5
                        and b["y"] < a["y"] + a["h"] - 0.5):
                    ov.append(f"{a['label']}×{b['label']}")
        bad += 0 if not ov else 1
        print(f"    {'OK   ' if not ov else '★    '}重叠 {len(ov)} 对"
              f"{'：' + '、'.join(ov[:3]) if ov else ''}")

        # ---- 3. 面积必须跟金额走。允许 12% 相对误差： -
        #        树图的行/列是矩形拼出来的，不可能和比例完全一致；
        #        但"最大那块面积也是最大"必须是硬结论，所以再加一条排序校验。
        print("\n  [3] 面积与金额同序")
        def parse_num(s: str) -> float:
            import re
            m = re.search(r"([\d,]+\.?\d*)\s*([万kK]?)", s or "")
            if not m:
                return 0.0
            v = float(m.group(1).replace(",", ""))
            if m.group(2) == "万":
                v *= 10000
            elif m.group(2) in ("k", "K"):
                v *= 1000
            return v

        pairs = [(c["label"], c["area"], parse_num(c["val"])) for c in cells]
        by_area = [p[0] for p in sorted(pairs, key=lambda t: -t[1])]
        by_val = [p[0] for p in sorted(pairs, key=lambda t: -t[2])]
        # 排序一致性：允许个别相邻位次交换（面积接近的两个格子谁大都有可能）
        rank_gap = max(
            abs(by_area.index(n) - by_val.index(n)) for n in by_area
        ) if by_area else 0
        ok3a = rank_gap <= 2
        bad += 0 if ok3a else 1
        print(f"    {'OK   ' if ok3a else '★    '}面积排序与金额排序最大位次差 {rank_gap}"
              f"（≤2 视为一致）")

        # 总面积的占比 vs 总金额的占比
        tot_a = sum(p[1] for p in pairs) or 1
        tot_v = sum(p[2] for p in pairs) or 1
        worst = max(
            (abs(p[1] / tot_a - p[2] / tot_v) * 100, p[0]) for p in pairs
        )
        ok3b = worst[0] <= 4.0
        bad += 0 if ok3b else 1
        print(f"    {'OK   ' if ok3b else '★    '}面积占比与金额占比最大偏差 "
              f"{worst[0]:.2f}pp（{worst[1]}，≤4pp）")

        # ---- 4. 组汇总必须存在（这是这张图补上的那一层）----
        print("\n  [4] 一级归属汇总（现有四张图都没有这层）")
        ok4 = len(groups) >= 2 and all(g["total"] for g in groups)
        bad += 0 if ok4 else 1
        print(f"    {'OK   ' if ok4 else '★    '}{len(groups)} 个归属都带合计金额")
        # 组金额之和 ≈ 格子金额之和
        gsum = sum(parse_num(g["total"]) for g in groups)
        csum = sum(parse_num(c["val"]) for c in cells)
        ok4b = csum > 0 and abs(gsum - csum) / csum < 0.05
        bad += 0 if ok4b else 1
        print(f"    {'OK   ' if ok4b else '★    '}组合计 {gsum:,.0f} vs 格子合计 "
              f"{csum:,.0f}（应一致）")

        # ---- 5. 键盘可达 + aria ----
        print("\n  [5] 键盘与读屏")
        br.ctl.js("document.querySelector('.bi-tree-c').focus()")
        time.sleep(0.3)
        cls = br.ctl.js("document.activeElement?.className") or ""
        aria = br.ctl.js("document.activeElement?.getAttribute('aria-label')") or ""
        ok5 = "bi-tree-c" in cls
        bad += 0 if ok5 else 1
        print(f"    {'OK   ' if ok5 else '★    '}格子可聚焦（{cls}）")
        ok5b = bool(aria) and "损失" in aria
        bad += 0 if ok5b else 1
        print(f"    {'OK   ' if ok5b else '★    '}aria-label：{aria[:56]}")

        # ---- 6. 悬停浮层：百分比必须和 aria 说的是同一个数 ----
        #        第一版这里就出过两个分母（24.3% vs 24.6%），
        #        同一张图两个百分比，读者会怀疑哪个是对的。
        print("\n  [6] 浮层与 aria 的百分比口径一致")
        c0 = br.ctl.js(r"""(() => {
          const e = document.querySelector('.bi-tree-c');
          const b = e.getBoundingClientRect();
          return {x: b.x + b.width / 2, y: b.y + b.height / 2};
        })()""") or {}
        br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                    x=c0.get("x", 0), y=c0.get("y", 0))
        time.sleep(0.6)
        tip = br.ctl.js("(document.querySelector('.bi-tip')||{}).innerText") or ""
        import re as _re
        tip_pcts = _re.findall(r"(\d+\.\d)%", tip)
        aria_pcts = _re.findall(r"(\d+\.\d)%", aria)
        agree = bool(tip_pcts) and bool(aria_pcts) and tip_pcts[0] == aria_pcts[0]
        bad += 0 if agree else 1
        print(f"    {'OK   ' if agree else '★    '}浮层 {tip_pcts[:1]} vs aria {aria_pcts[:1]}")

        # ---- 7. 文字不许被压扁 ----
        #        这一条是被用户那句"柱柱不圆润"顺带查出来的：
        #        19px 高的格子里，flex 的默认 flex-shrink 把 14px 的行盒
        #        压成 7px，屏幕上是半行字 —— 但 scrollHeight === clientHeight，
        #        [2][3] 这些溢出/面积断言全绿。所以必须直接量行盒高度。
        print("\n  [7] 格子里的文字没有被压扁")
        spans = br.ctl.js(r"""(() => {
          return [...document.querySelectorAll('.bi-tree-cl, .bi-tree-cv, .bi-tree-cs')]
            .map(s => {
              const cs = getComputedStyle(s);
              return {cls: s.className, dis: cs.display,
                      h: s.getBoundingClientRect().height,
                      fs: parseFloat(cs.fontSize)};
            });
        })()""") or []
        shown = [s for s in spans if s["dis"] != "none"]
        # 行盒高度 < 字号的 1.1 倍 = 被压过（正常是 line-height 1.25 倍）
        squashed = [s for s in shown if s["h"] < s["fs"] * 1.1]
        bad += 0 if not squashed else 1
        print(f"    {'OK   ' if not squashed else '★    '}"
              f"可见文字 {len(shown)} 个，被压扁 {len(squashed)} 个"
              + (f"：{[(s['cls'], round(s['h'], 1)) for s in squashed[:3]]}" if squashed else ""))
        hidden = [s for s in spans if s["dis"] == "none"]
        print(f"    OK   {len(hidden)} 个文字整行隐藏（格子太矮，塞不下就整行不写）")
    finally:
        br.close()
    print(f"\n  树图问题：{bad} 处")
    return bad


# 条/块类选择器：圆角必须引用变量。豁免要写明理由，否则下一次"统一圆角"
# 会把它们一起改成胶囊：
#   .bi-heat-c      纯色小方格（22px，里面没有字），圆角是"刻度"的一部分
#   .bi-*-li i      图例色块，同上
#   .pg-selbar-x    选区工具条里的 <kbd> 键帽，名字里有 bar 但不是条
#   .bi-heat-legend 热力图图例色块，同上
_R_BARISH = re.compile(r"(bar|track|fill|mini|heat|tree-c|stack-seg|-wa-|-dv-|-ss-)")
_R_EXEMPT = (".bi-heat-c", ".bi-lg-item", ".bi-donut-li", ".bi-dring-li",
             ".pg-selbar-x", ".bi-heat-legend")


def _radius_scan(css: str):
    """扫一份 CSS 文本，返回 (写死小圆角明细, 同选择器取值冲突明细)。

    【为什么单独抽出来】
    同一套判断要跑两遍：一遍在源码 app.css（防"漏改"），一遍在
    dist 产物（防"改了没重新打包，服务端还在发旧文件"）。
    两份各写一遍就又是"修一份忘一份"—— 这正是 `.bi-mini` 那个坑的成因。
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)      # 注释里的例子不算
    hits, dup, seen = [], [], {}
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel = " ".join(m.group(1).split())
        if not _R_BARISH.search(sel) or any(e in sel for e in _R_EXEMPT):
            continue
        for d in re.finditer(r"border-radius\s*:\s*([^;]+);", m.group(2)):
            raw = d.group(1).strip()
            val = " ".join(raw.split())
            if "var(" not in val and "inherit" not in val:
                nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)px", val)]
                if [n for n in nums if n < 4]:
                    hits.append((sel, val))
            if sel in seen and seen[sel] != val:
                dup.append((sel, seen[sel], val))
            seen[sel] = val
    return hits, dup


def check_dist_fresh() -> int:
    """服务端真正发出去的那份 CSS，是否就是改完的这份。

    【为什么这条必须单独存在】
    源码门禁查的是 `frontend/src/styles/app.css`，但后端挂的是
    `frontend/dist/`。两者之间隔着一个 `npm run build` —— 而这一步
    没人会记得每次都做。于是最容易出现的假成功是：
    **源码改对了、门禁全绿、屏幕上还是旧的**。
    这比"没改"更危险，因为它有绿勾作证。

    判据（都可证伪、都不需要浏览器）：
      1. dist/index.html 引用的 CSS / JS 都真实存在（不是上次的哈希）
      2. 它们都不早于 src/ 下**任何**源文件的最新修改时间
      3. CSS 里确实有 --r-bar / --r-cell / --r-pill 三个变量
      4. 用同一套 _radius_scan 扫一遍，没有写死小圆角 / 同名冲突

    【第 2 条为什么看整个 src/ 而不是只看 app.css】
    第一版只比 app.css 与产物 CSS 的时间 —— 于是改 `.tsx` 完全不报警。
    而昨天那次「Reviews.tsx 的柱子加 rx」就是纯 tsx 改动，CSS 一个字节都没动：
    在这种情况下旧门禁照样全绿，浏览器里却是旧柱子。
    所以基准必须是 `src/` 下所有文件的最新 mtime，产物也必须 CSS 与 JS 一起比。
    """
    section("产物一致性：dist 就是改完的那份")
    bad = 0
    fe = Path(__file__).parent.parent / "frontend"
    idx = fe / "dist" / "index.html"
    src_dir = fe / "src"
    if not idx.exists():
        print(f"    ★   dist/index.html 不存在，先 npm run build")
        return 1
    if not src_dir.is_dir():
        print(f"    ★   找不到源码目录 {src_dir}")
        return 1

    html = idx.read_text(encoding="utf-8", errors="ignore")
    assets = []
    for ext in ("css", "js"):
        m = re.search(r'assets/(index-[A-Za-z0-9_-]+\.%s)' % ext, html)
        if not m:
            print(f"    ★   index.html 里找不到 {ext} 引用")
            bad += 1
            continue
        p = fe / "dist" / "assets" / m.group(1)
        ok = p.exists()
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}引用产物存在：{m.group(1)}")
        if ok:
            assets.append(p)
    if not assets:
        print(f"\n  产物一致性问题：{bad} 处")
        return bad

    # 2) 过期检测：产物早于 src/ 里最新的那个文件 = 改了没打包
    src_files = [p for p in src_dir.rglob("*") if p.is_file()]
    newest = max(src_files, key=lambda p: p.stat().st_mtime)
    t_src, t_newest_name = newest.stat().st_mtime, newest.name
    oldest_asset = min(assets, key=lambda p: p.stat().st_mtime)
    age = t_src - oldest_asset.stat().st_mtime
    ok = age <= 1.0        # 1s 容差，避免同一秒写入的抖动
    bad += 0 if ok else 1
    stale = "" if ok else (f"（{t_newest_name} 比产物新 {age / 60:.1f} 分钟，"
                           f"去跑 npm run build）")
    print(f"    {'OK   ' if ok else '★    '}产物不早于 src/ 最新改动"
          f"（基准 {len(src_files)} 个文件）{stale}")

    # 3) / 4) 只在 CSS 产物上做
    css_path = next((p for p in assets if p.suffix == ".css"), None)
    if css_path is None:
        print(f"\n  产物一致性问题：{bad} 处")
        return bad
    css = css_path.read_text(encoding="utf-8", errors="ignore")
    for var in ("--r-bar", "--r-cell", "--r-pill"):
        ok = re.search(re.escape(var) + r"\s*:\s*\d+px", css) is not None
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}产物含变量 {var}")

    hits, dup = _radius_scan(css)
    ok = not hits and not dup
    bad += 0 if ok else 1
    print(f"    {'OK   ' if ok else '★    '}产物内条/块圆角均为变量"
          + ("" if ok else f"：{hits[:3]} {dup[:2]}"))

    print(f"\n  产物一致性问题：{bad} 处")
    return bad


_KB_PROBE = r"""(() => {
  /* 【判"可交互"要看有没有真的处理器，不看 cursor】
     cursor:pointer 只是设计意图，可能只用于 tooltip。用光标当判据会一次报出
     288 个，其中绝大多数是"可点祖先的后代"——点了其实由祖先处理。
     React 把 props 挂在 __reactProps$xxx 上，onClick 才是真交互。 */
  const props = (el) => {
    for (const k in el) if (k.startsWith('__reactProps$')) return el[k];
    return null;
  };
  const NATIVE = ['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'SUMMARY', 'IFRAME'];
  /* 【这里判的是"能不能 Tab 到"，必须是 el.tabIndex 而不是 getAttribute】
     <summary> 原生可聚焦但属性为 null（第一版据此把 reviews 页的
     "展开全部 12 个词的原话"报成了缺陷）。disabled 控件本来就不该可聚焦，
     也不是缺陷（chat 的发送按钮就是这么被误报的）。 */
  const nativelyOk = (el) => {
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return true;
    if (NATIVE.indexOf(el.tagName) >= 0) return true;
    return !!el.isContentEditable;
  };

  const cands = [];
  for (const el of document.querySelectorAll('main *')) {
    const p = props(el);
    if (!p || !(p.onClick || p.onMouseDown)) continue;
    if (nativelyOk(el)) continue;
    if (el.querySelector('a[href],button,input,select,textarea,summary')) continue;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) continue;
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    cands.push(el);
  }

  /* 【必须按"同类控件"成组判定，不能逐个判】
     热力矩阵 208 格用的是 roving tabindex：只有游标格 tabIndex=0，
     其余故意 -1（否则 Tab 要停 208 次）。逐个判会把 207 个正常设计报成缺陷。
     所以判据升级为：**每种同类控件里，至少有一个能 Tab 到**；
     如果只有一个能到而同类有很多个，那它必须是 roving —— 即方向键真的能换格。
     后半句必须靠真按一次键来验：
     "只留一个停靠点、但没实现方向键" = 其余格子永远够不着，比不做更糟。 */
  const groups = new Map();
  for (const el of cands) {
    /* 【分组键取"块名"，即第一个 class token】
       不能拿完整类名当键：同一类控件会带状态修饰符
       （`bi-heat-c` / `bi-heat-c is-anom` / `bi-heat-c is-link`），
       按完整类名分会拆成好几组，其中"异常格"那组往往一个 tabIndex=0 都没有，
       于是被误判成"整类不可达"。
       本项目的类名约定是「块名 + 修饰符」，所以第一个 token 就是块名。
       若将来有人写成「修饰符在前」，这里会分组错误 —— 表现为误报（变红），
       误报会促使人来看一眼，比静默漏报安全。 */
    const block = (el.className || '').toString().trim().split(/\s+/)[0] || '';
    const k = el.tagName + '|' + (el.getAttribute('role') || '') + '|' + block;
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(el);
  }

  const out = [];
  for (const [k, els] of groups) {
    const reach = els.filter((x) => x.tabIndex >= 0);
    let arrowOk = null;
    if (reach.length === 1 && els.length > 1) {
      const t0 = reach[0];
      t0.focus();
      let moved = false;
      for (const key of ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp']) {
        const from = document.activeElement;
        from.dispatchEvent(new KeyboardEvent('keydown',
          { key: key, bubbles: true, cancelable: true }));
        if (document.activeElement !== from) { moved = true; break; }
        from.focus();
      }
      arrowOk = moved;
    }
    out.push({
      cls: (els[0].className || '').toString().slice(0, 40),
      tag: els[0].tagName,
      total: els.length,
      reachable: reach.length,
      arrowOk: arrowOk,
      txt: (els[0].innerText || '').trim().replace(/\s+/g, ' ').slice(0, 20),
    });
  }
  return out;
})()"""


def check_keyboard_reachable(port) -> int:
    """点得动的东西，键盘也必须够得着。

    【为什么单独一节，而不是并进 check_focus】
    check_focus 是"Tab 走过去看焦点环亮不亮"——它只能发现**已经可聚焦**的
    元素里环被吃掉的。而这一节要抓的是另一种缺陷：元素根本进不了 Tab 序列。
    两者的证明方式相反，前者靠走一遍，后者靠数一遍。

    【这一节存在的理由：这类缺陷永远不会被自己暴露】
    <tr>、<div>、<th> 都不在浏览器的默认可聚焦列表里。给它们挂 onClick
    只会得到"鼠标点得到、键盘够不着"，不报错、不变形、不遮挡任何东西。
    鼠标用户一辈子碰不到，所以没人会来报这个 bug。
    本期实际抓到的三处：总览页 5 个可点周次行、原因治理页 3 个可排序表头、
    208 个热力格（tooltip 上明写着"点击进归因"）。

    【判据经过三次修正才站得住】
    · 第 1 版用 cursor:pointer 判可交互 → 288 个，绝大多数是可点祖先的后代
    · 第 2 版用 React onClick → 215 个，但把 disabled 按钮、
      原生可聚焦的 <summary> 都算成了缺陷
    · 第 3 版（当前）排除上述两类后逐个判 tabIndex → 又误伤 207 个
      roving tabindex 的热力格 —— 于是改成"按同类控件成组 + 方向键实测"

    【"有 onClick 但内部靠运行时守卫兜底"仍算缺陷】
    例如表头无条件挂 onClick、靠 `if (!sortVal) return` 才不会出错：
    它渲染出来的就是一个"点下去没反应"的交互暗示。修法是不挂处理器，
    而不是让探测器放过它。
    """
    section("键盘可达：点了有反应的东西，Tab 也必须能到")
    try:
        navkeys = [p["key"] for p in json.loads(
            urllib.request.urlopen(BASE + "/api/pages/nav", timeout=10).read())["pages"]]
    except Exception:
        navkeys = ['dashboard', 'attribution', 'reasons', 'plan', 'reviews',
                   'competitors', 'chat']

    bad = 0
    for pg in navkeys:
        port += 1
        br = Browser(port, f"{BASE}/#/{pg}")
        try:
            br.ctl.wait_until(
                "document.querySelectorAll('.sidenav-item.on').length===1", 50)
            time.sleep(1.8)
            groups = br.ctl.js(_KB_PROBE) or []
        except Exception as e:                      # noqa: BLE001
            print(f"    ★   {pg:<12} [探针失败] {type(e).__name__}")
            bad += 1
            continue
        finally:
            br.close()
            time.sleep(0.3)

        problems = []
        roving = []
        for g in groups:
            if g["reachable"] == 0:
                problems.append(g)                       # 整类都够不着
            elif g["total"] > 1 and g["reachable"] == 1:
                if g["arrowOk"]:
                    roving.append(g)                     # roving 且方向键有效
                else:
                    problems.append(g)                   # 只留一个停靠点却没方向键
        flag = "OK  " if not problems else "★   "
        note = f"（roving {len(roving)} 组，方向键实测可换格）" if roving else ""
        print(f"    {flag}{pg:<12} 有问题 {len(problems)} 组{note}")
        for g in problems:
            why = "整类不可聚焦" if g["reachable"] == 0 else "只 1 个停靠点但方向键无效"
            print(f"          {g['tag']:<8} {g['cls'][:30]:<30} "
                  f"{g['reachable']}/{g['total']} 可达 · {why} 「{g['txt']}」")
        for g in roving:
            print(f"          --   {g['tag']:<8} {g['cls'][:30]:<30} "
                  f"{g['reachable']}/{g['total']} 可达 · roving tabindex")
        bad += 1 if problems else 0

    print(f"\n  键盘不可达：{bad} 个页面有问题")
    return bad


def check_plan_numbers() -> int:
    """预警策划页的数字必须自己跟自己对得上（纯接口核对，不开浏览器）。

    【为什么这条门禁非有不可】
    这一页最容易出的不是版式错，而是**单位错**：同一个字段名在不同接口里
    语义不一致。`share` 在 reasons / dashboard 里是百分数（21.86），
    在 plan 里曾经是小数（0.22），前端照旧拼了个 "%"，于是屏幕上印出
    「占 0.22%」—— 数字小了 100 倍，而同一页上别的数字全都正常。

    这类错：排版检不出来（它没溢出）、对比度检不出来（颜色没问题）、
    键盘可达也检不出来（它本来就不该被点）。它长得完全像一条正常结论，
    只能靠"同一份数据内部自洽"来抓。

    【容差是写死的，不是"看着差不多"】
    每一条都给出可验算的关系与明确容差。容差只用来吃掉四舍五入的尾巴
    （各占比之和 99.9 / 100.1），不用来吃掉"差 100 倍"。

    判据：
      1. 各原因 loss_share 之和 = 100（±0.6）
      2. 每个原因 share ≈ count / 本周退货单量 × 100（±0.6pp）
         —— 这一条正是当初「0.22%」那个 bug 的照妖镜
      3. 各列贡献之和 + 未分配差额 = headline.delta_pp（±0.02）
      4. actionable_pct = 可动作损失 / 总损失 × 100（±0.2）
      5. 预警清单只收恶化周：delta_pp 全 > 0 且按 delta_pp 降序
      6. 改善周不进清单：列表长度 = alert_total，improving_total 另计
    """
    section("预警策划：数字自己跟自己对账（单位错了排版看不出来）")
    bad = 0
    scopes = [None, "服饰"]
    for scope in scopes:
        url = BASE + "/api/pages/plan"
        if scope:
            url += "?category=" + urllib.parse.quote(scope)
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print(f"    ★   {scope or '全部'}：接口取不到（{e}）")
            bad += 1
            continue
        tag = scope or "全部"
        h = d.get("headline") or {}
        reasons = d.get("reasons") or []

        # 1) 损失占比之和 = 100
        s = sum(float(x.get("loss_share") or 0) for x in reasons)
        ok = abs(s - 100.0) <= 0.6
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}{tag} 各原因损失占比之和 = {s:.1f}%"
              f"（应 ≈100，小数当百分数会变成 1.0 左右）")

        # 2) 每个原因的单量占比 = count / 本周退货单量 × 100
        rets = float(h.get("returns") or 0)
        if rets > 0:
            worst = None
            for x in reasons:
                exp = float(x.get("count") or 0) / rets * 100
                got = float(x.get("share") or 0)
                if abs(got - exp) > 0.6:
                    worst = (x.get("reason"), got, exp)
                    break
            ok = worst is None
            bad += 0 if ok else 1
            if ok:
                first = reasons[0] if reasons else {}
                exp0 = float(first.get("count") or 0) / rets * 100
                print(f"    OK   {tag} 单量占比与单量自洽（首个原因 "
                      f"{float(first.get('share') or 0):.2f}% ≈ {exp0:.2f}%）")
            else:
                print(f"    ★    {tag} 「{worst[0]}」占比对不上单量："
                      f"下发 {worst[1]:.2f}%，按 count/退货量 应是 {worst[2]:.2f}%"
                      f"（差 100 倍就是忘了 ×100）")

        # 3) 贡献之和 + 未分配 = 总差额
        cs = float(d.get("contrib_sum_pp") or 0)
        cr = float(d.get("contrib_rest_pp") or 0)
        dl = float(h.get("delta_pp") or 0)
        ok = abs(cs + cr - dl) <= 0.02
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}{tag} 贡献 {cs:+.2f}pp + 未分配 "
              f"{cr:+.2f}pp = {cs + cr:+.2f}pp（总差额 {dl:+.2f}pp）")

        # 4) 可动作比例 = 可动作损失 / 总损失
        lt = float(d.get("loss_total") or 0)
        la = float(d.get("loss_actionable") or 0)
        ap = float(d.get("actionable_pct") or 0)
        exp = la / lt * 100 if lt else 0.0
        ok = abs(ap - exp) <= 0.2
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}{tag} 有动作的损失占 {ap:.1f}%"
              f"（{la:.0f}/{lt:.0f}，应 ≈{exp:.1f}%）")

        # 5 / 6) 预警只收恶化周，且降序；改善周单独计数
        alerts = d.get("alerts") or []
        dps = [float(a.get("delta_pp") or 0) for a in alerts]
        ok = all(v > 0 for v in dps) and dps == sorted(dps, reverse=True)
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}{tag} 预警 {len(alerts)} 周全为恶化且按 Δpp 降序"
              + ("" if ok else f"：Δpp={dps}"))

        ok = len(alerts) == int(d.get("alert_total") or -1)
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}{tag} 清单长度 {len(alerts)} = alert_total "
              f"{d.get('alert_total')}（改善周 {d.get('improving_total')} 周另计）")

    # 【这里本来还有第 7 条，被我删了】
    # 原意是"值落在 0–1 之间就当小数没 ×100"，写完发现它会误报：
    # 一个真正稀有的原因（占损失 0.8%）合法地就落在 0–1 之间，
    # 门禁会在数据完全正常时闪红灯。而"占比之和 = 100%"（第 1 条）
    # 已经精确覆盖了同一件事 —— 全部漏乘时和会掉到 1.0 左右。
    # 能用可验算的关系替代时，不要留启发式判据。

    print(f"\n  策划页数字问题：{bad} 处")
    return bad


def check_attr_promo() -> int:
    """归因页：促销卡的**适用范围**与**账目闭合**（纯接口，不开浏览器）。

    【这张卡曾经在两个意义上说不通，所以两条都得守】
    1. 跟选中的周无关。promo_mechanism() 不接 week 参数，算的是
       "促销周 vs 非促销周"的整体对照。第 52 周不是促销周、退货率还在降
       （−0.98pp），页面照样显示「促销周退货率上升 +3.98pp」，
       而这一页的标题是「这一周为什么涨了」——读者只能理解成它在解释 52 周。
       → 判据 1、2 守这一点。

    2. 三个数摆成一个等式。原来是一条"结构 + 机制"的堆叠条，右边标
       "合计 3.28pp"。但机制 3.98 是 151 个 SKU 配对差的**等权平均**
       （每个 SKU 一样重），合计 3.28 是**按订单加权**的总体差。
       0.04 + 3.98 = 4.02 ≠ 3.28，而堆叠条的几何语义正是"两段相加等于全长"。
       → 判据 3、4 守这一点：差额必须被命名、被展示，且不能大到
         说明分解口径本身出了问题。

    【为什么不用"看文案里有没有'不可相加'四个字"当判据】
    文案改了门禁就空转。这里验的是数字之间的关系 —— 文案可以重写，
    关系不能变。
    """
    section("归因页：促销卡的适用范围与账目闭合（口径错了版式看不出来）")
    bad = 0

    # 促销周取自 promo_weeks 本身，不写死 —— 换一份数据周号就变了。
    #
    # 【为什么一定要有第 36 周这一条】
    # 四种组合都要覆盖，缺一种就漏一种改法：
    #   44 / 48  异常 + 促销   → 展示促销卡
    #   36       异常 + 非促销 → **不展示**（这条最容易被写错）
    #   52       不异常 + 非促销 → 不展示
    # 假如有人把判定简化成"异常周就展示促销卡"（is_promo_week = is_anomaly），
    # 44/48 照样通过、52 照样通过，**只有 36 会红** —— 它是这个错误的唯一照妖镜。
    # 反过来，若写成"永远展示"，52 会红；写成"永远不展示"，44 会红。
    cases = [(44, True), (48, True), (36, False), (52, False)]
    data = {}
    promo_weeks = None
    for wk, expect in cases:
        try:
            raw = urllib.request.urlopen(
                f"{BASE}/api/pages/attribution?week={wk}", timeout=90).read()
            d = json.loads(raw)
        except Exception as e:
            print(f"  ★ 取第 {wk} 周数据失败：{e}")
            return bad + 1
        p = d.get("promo") or {}
        if promo_weeks is None:
            promo_weeks = [int(x) for x in (p.get("promo_weeks") or [])]
        if wk == 44:
            data = d
        got = bool(p.get("is_promo_week"))
        # 双重判定：既要对得上预期，也要对得上 promo_weeks 本身
        ok = got == expect and got == (wk in promo_weeks)
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}第 {wk} 周 is_promo_week={got}"
              f"（应为 {expect}；promo_weeks={promo_weeks}）")

    p = data.get("promo") or {}
    if not p:
        print("  ★ 第 44 周没有 promo 块，后面的闭合项无从验起")
        return bad + 1

    # 3) 闭合差额 = 实际涨幅 − (结构 + 自身涨幅)
    resid = float(p.get("residual_pp"))
    exp = (float(p.get("actual_gap_pp") or 0)
           - (float(p.get("structure_pp") or 0) + float(p.get("mean_diff_pp") or 0)))
    ok = abs(resid - exp) <= 0.02
    bad += 0 if ok else 1
    print(f"    {'OK   ' if ok else '★    '}闭合差额 {resid:+.2f}pp"
          f"（= {p.get('actual_gap_pp')} − ({p.get('structure_pp')} + "
          f"{p.get('mean_diff_pp')}) = {exp:+.2f}）")

    # 4) 差额不能吃掉大头：真变大了说明分解口径要重做，不是加一行说明能圆的
    gap = abs(float(p.get("actual_gap_pp") or 0))
    ok = abs(resid) <= max(gap * 0.5, 0.5)
    bad += 0 if ok else 1
    print(f"    {'OK   ' if ok else '★    '}差额量级 {abs(resid):.2f}pp"
          f" ≤ 实际涨幅的一半 {gap * 0.5:.2f}pp（再大就不能靠「口径不同」解释了）")

    print(f"\n  归因页促销卡问题：{bad} 处")
    return bad


def check_reasons_recon() -> int:
    """原因治理页：三处账目必须真的对得上（纯接口，不开浏览器）。

    【为什么这一页特别需要"对账"而不是"断言数字长什么样"】
    这一页有两种口吻并存：
      · 上表是**原因**视角（9 个原因，各占全量多少）
      · 下图是**类目**视角（4 个类目 × 9 个原因）
    同一页两套切法，任何一处漏了行、把某几行折进「未归因」、或者
    某一格用了错的分母，页面都**照样渲染、照样好看、照样不报错**。
    只有把行加起来和顶上那个数比一比，才知道是不是同一批账。

    【守的七条】
      R1  Σ行.recoverable = KPI.recoverable_total
      R2  Σ行.loss        = KPI.total_loss
      R3  Σ行.n           = KPI.total_returns
      R4  单行 recoverable ≈ loss × resellable/100
          —— 卡片脚注里写着「可挽回 = 损失 × 可再售率」，
             这句话是给读者当验算锚点用的，不能只是句文案。
      R5  全类目下 Σ矩阵格.loss = KPI.total_loss
      R6  全类目下 Σ矩阵格.n    = KPI.total_returns
      R7  「未归类」这一行的身份是「数据缺口」，不是第 9 个原因
          —— 前六条验的是数字，这条验的是**这行是什么**。
             数字全对但把"没归上原因"混进"9 个原因"，
             读者会拿它 72.4% 的可再售率（全表最高）去跟真原因比，
             得出"未归类最值得先管"：每个数都对，结论是反的。

    【R1–R3 有一个必须说清的局限】
    它们当前的实现里，KPI 就是从行合计推出来的（recoverable_total =
    sum(rows.recoverable)），所以**只要后端没换算法，R1 恒真**。
    实测验证过：把某一行 recoverable 乘 1.1，R4 五条全红、R1 一条不红 ——
    因为被篡改的行同时也进了合计，两边一起动，比值不变。
    这不是白测：R1 守的是"哪天 KPI 改成从另一个数据源取"这种分家。
    但**真正抓单行算错的是 R4**，不要以为有 R1 就安全了。
    这一类"两边同源所以恒等"的断言要特别当心，很容易自我安慰。

    【R5/R6 只在"全部类目"口径下验】
    矩阵固定是全类目视角（不随筛选变，见 Reasons.tsx 的卡片说明）。
    切到单类目时矩阵仍是全量，拿它跟该类目的 total 比必然差 300 万，
    那是设计，不是缺陷 —— 所以这两条只在 cat="" 时成立。

    【容差的来历，不是随手给的 5%】
      · R1–R3：全 0。差额为 0 才说明是同一批账；给大容差等于白测。
        只留 1 元吸收浮点减法误差。
      · R4：实测最大相对误差 0.72%（resellable 只保留 1 位小数，
        531 万的 0.05% 就是 2600 元，误差被绝对额放大）。
        给 1.5% —— 容得下四舍五入，又拦得住"定义被改掉"（那会差 100% 量级）。
      · R5：实测差 2 元（逐格 _f 取整）。给 5 元。
    """
    section("原因治理页：行合计 / 矩阵格合计 / 乘法关系（口径错了页面不会变形）")
    bad = 0

    # 类目清单取自接口本身，不写死 —— 换数据类目就变了
    try:
        base = json.loads(urllib.request.urlopen(
            f"{BASE}/api/pages/reasons", timeout=60).read())
    except Exception as e:
        print(f"  ★ 取原因页数据失败：{e}")
        return bad + 1
    cats = [""] + list(base.get("categories") or [])

    # ---- R7 身份：「未归类」不是原因，是归因失败 ----
    # 【为什么这条要独立于 R1–R6】
    # 前六条全都在验"数字加得对不对"，而这一条验的是**这一行的身份**。
    # 一个数字全对、但把"未归上原因"混进"9 个原因"里的页面，
    # 会让读者拿它的可再售率（全表最高 72.4%）去跟真原因比，
    # 得出"未归类最值得先管"——每个数字都对，结论是反的。
    # 判据只看接口下发的身份位，不看前端怎么渲染：
    # 前端把 l1 写死成「数据缺口」也能让页面看起来对，但那是把业务含义
    # 塞在一个字符串里，换份数据就失准。
    void_rows = [r for r in (base.get("rows") or []) if r.get("is_void")]
    ok = len(void_rows) == 1
    bad += 0 if ok else 1
    print(f"    {'OK   ' if ok else '★    '}R7 恰好 1 行被标为「数据缺口」"
          f"（现在 {len(void_rows)} 行）"
          + ("" if ok else "—— 未归类漏标，读者会拿它跟真原因比"))
    if ok:
        v = void_rows[0]
        ok = v.get("l1") == "数据缺口"
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}R7 该行层级写「数据缺口」而非「其他」"
              + ("" if ok else f"：现在是「{v.get('l1')}」"))
        # 反向：任何非 is_void 的行都不许冒用「数据缺口」
        fake = [r.get("l2") for r in (base.get("rows") or [])
                if not r.get("is_void") and r.get("l1") == "数据缺口"]
        ok = not fake
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}R7 没有别的行冒用「数据缺口」"
              + ("" if ok else f"：{fake}"))


    for cat in cats:
        q = urllib.parse.quote(cat)
        try:
            d = json.loads(urllib.request.urlopen(
                f"{BASE}/api/pages/reasons?category={q}", timeout=60).read())
        except Exception as e:
            print(f"  ★ 取「{cat or '全部类目'}」数据失败：{e}")
            bad += 1
            continue
        rows = d.get("rows") or []
        label = cat or "全部类目"

        s_rec = sum(float(r.get("recoverable") or 0) for r in rows)
        s_loss = sum(float(r.get("loss") or 0) for r in rows)
        s_n = sum(int(r.get("n") or 0) for r in rows)
        t_rec = float(d.get("recoverable_total") or 0)
        t_loss = float(d.get("total_loss") or 0)
        t_n = int(d.get("total_returns") or 0)

        pairs = [
            ("R1", "行可挽回合计", s_rec, t_rec, 1.0),
            ("R2", "行损失合计", s_loss, t_loss, 1.0),
            ("R3", "行条数合计", s_n, t_n, 0),
        ]
        for tag, name, got, want, tol in pairs:
            ok = abs(got - want) <= tol
            bad += 0 if ok else 1
            print(f"    {'OK   ' if ok else '★    '}{label} · {tag} {name}"
                  f" {got:,.0f} = {want:,.0f}"
                  + ("" if ok else f"（差 {got - want:,.1f}）"))

        # R4 —— 逐行验乘法关系，取误差最大的那一行报出来
        worst = 0.0
        worst_row = None
        for r in rows:
            loss = float(r.get("loss") or 0)
            rate = float(r.get("resellable") or 0)
            got = float(r.get("recoverable") or 0)
            calc = loss * rate / 100.0
            # 两边都接近 0 时相对误差没有意义，用绝对 1 元兜底
            err = (abs(calc - got) / got) if got > 1 else abs(calc - got)
            if err > worst:
                worst, worst_row = err, (r, calc, got)
        ok = worst <= 0.015
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}{label} · R4 单行 可挽回 = 损失 × "
              f"可再售率（最大相对误差 {worst * 100:.2f}%）"
              + ("" if ok else
                 f"：{worst_row[0].get('l2')} 应为 {worst_row[1]:,.0f}，"
                 f"实为 {worst_row[2]:,.0f}"))

        # R5 / R6 —— 只有全类目口径下，矩阵格合计才该等于总量
        if not cat:
            cells = (d.get("matrix") or {}).get("cells") or []
            c_loss = sum(float(c.get("loss") or 0) for c in cells)
            c_n = sum(int(c.get("n") or 0) for c in cells)
            ok = abs(c_loss - t_loss) <= 5
            bad += 0 if ok else 1
            print(f"    {'OK   ' if ok else '★    '}R5 矩阵格损失合计 {c_loss:,.0f}"
                  f" = 总量 {t_loss:,.0f}"
                  + ("" if ok else f"（差 {c_loss - t_loss:,.1f}）"))
            ok = abs(c_n - t_n) <= 0
            bad += 0 if ok else 1
            print(f"    {'OK   ' if ok else '★    '}R6 矩阵格条数合计 {c_n:,}"
                  f" = 总量 {t_n:,}" + ("" if ok else f"（差 {c_n - t_n}）"))

    print(f"\n  原因页对账问题：{bad} 处")
    return bad


def check_plan_interact(port) -> int:
    """预警策划页：选周这件事，点、切类目、回车三条路都必须自洽。

    【为什么单独立一节】
    这一页所有内容都是"某一周"的，选周错了，后面每一个数字都答的是另一个问题
    —— 页面不会报错、不会溢出、数字也都不假，只是**看着像在回答别人**。
    这类错只有真的去点、去切、去按才看得见。

    【守的不变量（每一步都重新验一遍）】
      A. KPI 卡标题「第 N 周退货率」
      B. 预警清单里唯一一行高亮 tr.is-on
      C. 策划方案卡标题「第 N 周的策划方案」
      三者必须是同一周，且 N 必须在当前范围的预警清单里。

    【为什么必须包含"切类目"】
    曾经的真实缺陷：选中的周只存了一个裸周号，切到「个护」后仍带着旧周号去请求。
    个护只有第 48 周异常，页面却按第 44 周取数 —— 清单里没有 44 那一行，
    下面却写着"第 44 周的策划方案"，左右连不上。

    【为什么必须真按 Enter】
    `check_keyboard_reachable` 只证明"元素能被 Tab 到"，证明不了"按下去有效果"。
    两个都成立才算键盘能用；只验前者，等于假设处理函数一定写对了。
    """
    section("预警策划：选周交互（点击 / 切类目 / 回车）")
    STATE = r"""(() => {
      const num = (s) => { const m = (s||'').match(/第\s*(\d+)\s*周/); return m ? +m[1] : null; };
      // KPI 卡标题「第 N 周退货率」——用整页文字找，不依赖卡片层级
      const km = document.body.innerText.match(/第\s*(\d+)\s*周退货率/);
      const kw = km ? +km[1] : null;
      const ons = [...document.querySelectorAll('.pg-table tbody tr.is-on')];
      const onW = ons.length === 1 ? num(ons[0].innerText) : null;
      let pw = null, pt = '';
      for (const e of document.querySelectorAll('.pg-card-t')) {
        if (/周的策划方案/.test(e.innerText || '')) { pt = e.innerText.trim(); pw = num(pt); break; }
      }
      const rows = [...document.querySelectorAll('.pg-table tbody tr.clickable')].map(tr => num(tr.innerText));
      const chip = ((document.querySelector('.pg-fchip.on') || {}).innerText || '').trim();
      return {kw, onCount: ons.length, onW, pw, pt, rows, chip};
    })()"""

    br = Browser(port, f"{BASE}/#/plan", width=1440, height=1250)
    bad = 0
    try:
        br.ctl.wait_until(
            "document.querySelectorAll('.pg-table tbody tr.clickable').length > 0", 90)
        time.sleep(1.3)

        def st():
            return br.ctl.js(STATE) or {}

        def step(label):
            """验一遍三处一致性，返回状态。"""
            nonlocal bad
            s = st()
            probs = []
            if s.get("onCount") != 1:
                probs.append(f"高亮行 {s.get('onCount')} 行（应为 1）")
            if s.get("kw") != s.get("onW"):
                probs.append(f"KPI 第 {s.get('kw')} 周 ≠ 高亮第 {s.get('onW')} 周")
            if s.get("pw") != s.get("onW"):
                probs.append(f"方案标题「{s.get('pt')}」≠ 高亮第 {s.get('onW')} 周")
            if s.get("onW") not in (s.get("rows") or []):
                probs.append(f"高亮周 {s.get('onW')} 不在清单 {s.get('rows')} 里")
            bad += 0 if not probs else 1
            print(f"    {'OK   ' if not probs else '★    '}{label:<16} "
                  f"范围={s.get('chip')} 周={s.get('onW')} 清单={s.get('rows')}")
            for x in probs:
                print(f"          -> {x}")
            return s

        s0 = step("默认打开")
        rows = s0.get("rows") or []

        def click_row(wk):
            br.ctl.js(f"""(() => {{
              const tr = [...document.querySelectorAll('.pg-table tbody tr.clickable')]
                .find(t => /第\\s*{wk}\\s*周/.test(t.innerText));
              if (tr) tr.click();
            }})()""")
            time.sleep(1.3)

        def click_chip(name):
            br.ctl.js(f"""(() => {{
              const b = [...document.querySelectorAll('.pg-fchip')]
                .find(e => e.innerText.trim() === {json.dumps(name)});
              if (b) b.click();
            }})()""")
            time.sleep(1.6)

        if len(rows) >= 2:
            click_row(rows[1])
            step(f"点第 {rows[1]} 周")
            click_row(rows[0])
            step(f"点回第 {rows[0]} 周")

        # 切到个护：它只有第 48 周异常，旧周号 44 必须被丢掉
        click_chip("个护")
        step("切到「个护」")
        click_chip("全部类目")
        step("切回「全部类目」")

        # ---- 回车：聚焦到另一行后真按一次键 ----
        s_cur = st()
        others = [r for r in (s_cur.get("rows") or []) if r != s_cur.get("onW")]
        if not others:
            print("    --   这一屏只有一周，跳过回车")
        else:
            tgt = others[0]
            focus = br.ctl.js(f"""(() => {{
              const tr = [...document.querySelectorAll('.pg-table tbody tr.clickable')]
                .find(t => /第\\s*{tgt}\\s*周/.test(t.innerText));
              if (!tr) return 'no-row';
              tr.focus();
              return document.activeElement === tr ? 'focused' : 'focus-failed';
            }})()""")
            if focus != "focused":
                bad += 1
                print(f"    ★    第 {tgt} 周的行聚焦失败（{focus}）")
            else:
                for kind in ("rawKeyDown", "keyUp"):
                    br.ctl.send("Input.dispatchKeyEvent", type=kind, key="Enter",
                                code="Enter", windowsVirtualKeyCode=13,
                                nativeVirtualKeyCode=13)
                time.sleep(1.4)
                s2 = st()
                ok = (s2.get("onW") == tgt and s2.get("kw") == tgt and s2.get("pw") == tgt)
                bad += 0 if ok else 1
                print(f"    {'OK   ' if ok else '★    '}回车换周 "
                      f"{s_cur.get('onW')} → {tgt}：实际停在第 {s2.get('onW')} 周"
                      f"（KPI {s2.get('kw')} / 方案 {s2.get('pw')}）")
                if not ok:
                    print("          -> 行能聚焦但按回车没反应 = 键盘用户卡在这一步")

        # ---- 原因排行（主从看板）：口径切换真的重排 + 右侧明细跟随 ----
        # 【为什么这两条必须真点】
        # 主从结构最怕的是"左边点了右边没动"——那不是报错，是看板名存实亡；
        # 以及"切了口径数字变了但顺序没重排"——读者以为在看条数榜，
        # 实际看的是损失榜换了个标题。两者都只有去点才看得见。
        has_rank = br.ctl.js("!!document.querySelector('.pg-plan-master .bi-bar-row')")
        if not has_rank:
            print("    --   无原因排行（范围内无原因拆分），跳过主从断言")
        else:
            def rank_labels():
                return br.ctl.js(
                    "[...document.querySelectorAll('.pg-plan-master .bi-bar-l')]"
                    ".map(e=>e.textContent)") or []

            # 口径切换：两个口径的完整顺序都和接口算出来的一致
            # 【接口必须带页面当前周】主从断言跑在回车换周之后，
            # 页面停在第 48 周——拿默认周（44）的序来对，等于两把不同的尺子。
            cur_wk = br.ctl.js(
                "(document.body.innerText.match(/第\\s*(\\d+)\\s*周退货率/)||[])[1]")
            plan_api = json.load(urllib.request.urlopen(
                f"{BASE}/api/pages/plan" + (f"?week={cur_wk}" if cur_wk else "")))
            by_loss = [r['reason'] for r in sorted(plan_api['reasons'], key=lambda x: -x['loss'])]
            by_count = [r['reason'] for r in sorted(plan_api['reasons'], key=lambda x: -x['count'])]
            if len(set(by_loss)) >= 2 and by_loss != by_count:
                # 数据在两口径下顺序确实不同，才断"切换必须重排"；
                # 若两口径顺序恰好一样，断"重排"反而要求页面变出假差异。
                br.ctl.js("[...document.querySelectorAll('.pg-plan-rank .bi-seg-i')]"
                          ".find(x=>x.textContent.includes('条数')).click()")
                time.sleep(1.0)
                got_count = rank_labels()
                ok_c = got_count == by_count
                bad += 0 if ok_c else 1
                print(f"    {'OK   ' if ok_c else '★    '}切「按条数」后排行 = 接口条数序")
                if not ok_c:
                    print(f"          -> 期望 {by_count} 实际 {got_count}")
                br.ctl.js("[...document.querySelectorAll('.pg-plan-rank .bi-seg-i')]"
                          ".find(x=>x.textContent.includes('损失')).click()")
                time.sleep(1.0)
                ok_l = rank_labels() == by_loss
                bad += 0 if ok_l else 1
                print(f"    {'OK   ' if ok_l else '★    '}切回「按损失」后排行 = 接口损失序")
            else:
                print("    --   两口径顺序恰好相同，重排断言跳过（不造假差异）")

            # 主从联动：点排行第 2 行，右侧明细标题必须跟着变
            labels = rank_labels()
            if len(labels) >= 2:
                target = labels[1]
                br.ctl.js(f"""(() => {{
                  const rows = [...document.querySelectorAll('.pg-plan-master .bi-bar-row')];
                  const hit = rows.find(r =>
                    (r.querySelector('.bi-bar-l')||{{}}).textContent === {json.dumps(target)});
                  if (hit) hit.click();
                }})()""")
                time.sleep(0.6)
                detail = br.ctl.js(
                    "(document.querySelector('.pg-plan-detail .pg-plan-n')||{}).textContent || ''")
                # 明细标题可能带别名后缀，用 includes 判
                ok_m = target in (detail or '')
                bad += 0 if ok_m else 1
                print(f"    {'OK   ' if ok_m else '★    '}点排行「{target}」右侧明细跟随"
                      f"（明细标题：{ (detail or '')[:24] }）")
                if not ok_m:
                    print("          -> 左边点了右边没动 = 主从看板名存实亡")
                # 选中态恰好一行
                on_n = br.ctl.js(
                    "document.querySelectorAll('.pg-plan-master .bi-bar-row.on').length")
                ok_o = on_n == 1
                bad += 0 if ok_o else 1
                print(f"    {'OK   ' if ok_o else '★    '}排行选中态恰好 1 行（实际 {on_n}）")

        # ---- 走势图 hover：跟随线 + 点放大 + 浮层，移出即恢复 ----
        # 【为什么 hover 也要门禁】
        # 用户原话"鼠标放上去一点都不动态、太呆板"——hover 反馈就是这块的
        # 核心功能，坏了等于回到呆板版。断言锚行为：真派发 mousemove，
        # 看 hoverline / is-hover / bi-tip 三样出现，移出后消失。
        has_trend = br.ctl.js(
            "document.querySelectorAll('.pg-trend .pg-week-point').length > 40")
        if not has_trend:
            print("    --   无 52 周走势，跳过 hover 断言")
        else:
            box = br.ctl.js("""(() => {
              const r = document.querySelector('.pg-trend').getBoundingClientRect();
              return JSON.stringify({x: r.x, y: r.y, w: r.width, h: r.height});
            })()""")
            bb = json.loads(box)
            # 图中部偏左（避开预警点）与图外右下各移一次
            for phase, (mx, my), expect in (
                ("进入", (bb['x'] + bb['w'] * .38, bb['y'] + bb['h'] * .5), True),
                ("移出", (30, bb['y'] + bb['h'] + 300), False),
            ):
                br.ctl.send('Input.dispatchMouseEvent', type='mouseMoved',
                            x=mx, y=my)
                time.sleep(0.6)
                got = br.ctl.js("""(() => ({
                  line: !!document.querySelector('.pg-trend-hoverline'),
                  pt: document.querySelectorAll('.pg-week-point.is-hover').length,
                  tip: !!document.querySelector('.bi-tip'),
                }))()""")
                ok_h = (bool(got.get('line')) == expect
                        and (got.get('pt', 0) > 0) == expect
                        and bool(got.get('tip')) == expect)
                bad += 0 if ok_h else 1
                print(f"    {'OK   ' if ok_h else '★    '}走势图 hover {phase}："
                      f"跟随线={got.get('line')} 放大点={got.get('pt')} 浮层={got.get('tip')}"
                      f"（应 {'全出现' if expect else '全消失'}）")
    finally:
        br.close()
        time.sleep(0.3)

    print(f"\n  策划页交互问题：{bad} 处")
    return bad


def check_radius_tokens() -> int:
    """圆角是否只剩"一个出处"——直接读 CSS 源码，不开浏览器。

    【为什么这条要查源码，不查渲染结果】
    渲染结果只能证明"我打开的这一页是对的"。可圆角这个 bug 的特点是
    **同一类元素散落在不同页面、由不同人分几次写**：
      · `.bar-track` 提到 6px 了，`.wc-track`（对话页词云条）还是 2px
      · `.pg-heat-fill` 4px 了，`.bi-heat-c`（业务洞察热力格）还是 2px
      · 更糟的是 `.bi-mini` 写了两遍：前面 2px、后面 pill，后者盖前者 ——
        读哪一遍都像"没问题"，实际生效的是后面那条
    这些都是**静态可判定的**：条/块类选择器里不该再出现个位数 px 的圆角。
    所以这里做一次源码扫描，把"漏改"变成断言。

    豁免要写明理由（纯色小方块是"刻度"，放大就变成装饰了）。
    """
    section("圆角来源：条/块类样式必须引用变量，不能再写死 px")
    bad = 0
    css_path = Path(__file__).parent.parent / "frontend" / "src" / "styles" / "app.css"
    if not css_path.exists():
        print(f"    ★   找不到 {css_path}")
        return 1
    src = css_path.read_text(encoding="utf-8")

    # 1) 变量必须真实存在（否则 var() 会静默失效成 0，圆角全变直角）
    for var in ("--r-bar", "--r-cell", "--r-pill"):
        ok = re.search(re.escape(var) + r"\s*:\s*\d+px", src) is not None
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}变量 {var} 有定义")

    hits, dup = _radius_scan(src)

    # 2) 条/块类选择器不允许再出现 <4px 的写死圆角
    ok = not hits
    bad += 0 if ok else len(hits)
    print(f"    {'OK   ' if ok else '★    '}条/块类选择器无写死小圆角（<4px）"
          + ("" if ok else f"：{hits[:4]}"))

    # 3) 条/块类选择器不许写两遍圆角且取值不同（.bi-mini 的老坑：
    #    前面 2px、后面 pill，读哪一遍都像"没问题"，生效的是后面那条）
    ok = not dup
    bad += 0 if ok else len(dup)
    print(f"    {'OK   ' if ok else '★    '}条/块类选择器圆角取值一致"
          + ("" if ok else f"：{dup[:3]}"))

    print(f"\n  圆角来源问题：{bad} 处")
    return bad


def check_console_clean(port) -> int:
    """12→7 个页面走一遍：无未捕获异常、无 console.error、每页都不空白。

    【为什么这条必须存在，而且必须带自检】
    之前所有检查看的都是"某几个元素对不对"。但只要一个页面在渲染时抛错，
    结果是**整块卡片空白** —— 颜色、圆角、对比度全都"没东西可测"，
    于是所有检查照样全绿。这类问题没有监听器就永远发现不了。

    三件事一起做，缺一个就不可信：
      1. **监听器自检**：先故意抛一个异常，确认被捕获。
         不做这步的话，"0 个错误"分不清是页面干净还是监听断了 ——
         这和"检查没执行"的假绿是同一个病。
      2. **页面清单以后端下发为准**，并核对侧边栏实际按钮数。
         这条是被自己坑出来的：我第一版手写了 12 个 tab 名，
         实际路由只有 7 个（dashboard/attribution/reasons/reviews/
         competitors/eval/chat），另外 5 个是我编的 ——
         跳过去只会落到兜底页或空白，而"空白页上没有报错"当然全绿。
      3. **每页必须有内容**：`main` 的可见文字 < 80 字算失败。
      4. **路由必须真的生效**：切过去之后，侧边栏第 i 项要有 `on`。
         这条是第 3 条的补丁，也是被自己坑出来的第二层：
         未知 hash 不会渲染空白，它会**停在上一个页面**——
         实测 `#/overview`、`#/kb`、`#/ops` 全都显示 dashboard 的 1132 字。
         也就是说"内容 1132 字、0 报错"这种绿灯，可能只是六次测了同一页。
         光看字数是发现不了的，必须看"当前选中的导航项是不是我要求的那一项"。

    事件和命令响应走同一条 WebSocket，靠 id 区分；空闲期到达的事件
    要等下一次 send 的读循环才取出，所以每页结束补一次排空。
    """
    section("运行时干净：每个页面无未捕获异常 / 无 console.error / 不空白")
    bad = 0
    port += 1

    # 页面清单：以后端为准，别手写
    try:
        nav = json.loads(urllib.request.urlopen(BASE + "/api/pages/nav",
                                                timeout=10).read())
        keys = [p["key"] for p in nav.get("pages", [])]
    except Exception as e:
        print(f"    ★   取不到 /api/pages/nav：{e}")
        return 1
    print(f"    后端下发 {len(keys)} 个页面：{', '.join(keys)}")

    br = Browser(port, f"{BASE}/#/{keys[0]}", width=1366, height=900)
    try:
        br.ctl.send("Log.enable")
        br.ctl.wait_until("!!document.querySelector('.sidenav-item')", 90)
        time.sleep(1.2)

        # ---- 1) 监听器自检 ----
        br.ctl.js("setTimeout(() => { throw new Error('__probe_alive__') }, 0); 1")
        time.sleep(0.6)
        br.ctl.send("Runtime.evaluate", expression="1")
        alive = any("__probe_alive__" in json.dumps(e, ensure_ascii=False)
                    for e in br.ctl.events)
        bad += 0 if alive else 1
        print(f"    {'OK   ' if alive else '★    '}监听器自检：故意抛的异常被捕获")

        # ---- 2) 侧边栏项数 == 后端下发的页面数 ----
        n_side = br.ctl.js("document.querySelectorAll('.sidenav-item').length") or 0
        ok = n_side == len(keys)
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}侧边栏 {n_side} 项 = 后端 {len(keys)} 页")

        # ---- 3) 逐个页面 ----
        for idx, k in enumerate(keys):
            start = len(br.ctl.events)
            br.ctl.js(f"location.hash = '#/{k}'")
            time.sleep(1.6)
            # 排空：空闲期到达的事件要等下一次 send 才取出
            br.ctl.send("Runtime.evaluate", expression="1")
            time.sleep(0.2)
            br.ctl.send("Runtime.evaluate", expression="1")
            evs = list(br.ctl.events[start:])

            txt = br.ctl.js("""(() => {
              const m = document.querySelector('main');
              return m ? m.innerText.replace(/\\s+/g, '').length : -1;
            })()""")
            txt = -1 if txt is None else int(txt)

            # 路由是否真的生效：第 idx 个导航项应处于 on
            landed = br.ctl.js("""(() => {
              const items = [...document.querySelectorAll('.sidenav-item')];
              const el = items[%d];
              if (!el) return null;
              return {on: el.classList.contains('on'),
                      txt: (el.innerText || '').trim().slice(0, 8)};
            })()""" % idx)
            if not isinstance(landed, dict):
                landed = {}
            routed = bool(landed.get("on"))

            errs = []
            for e in evs:
                m, p = e.get("method"), e.get("params", {})
                if m == "Runtime.exceptionThrown":
                    d = p.get("exceptionDetails", {})
                    desc = ((d.get("exception") or {}).get("description")
                            or d.get("text") or "?")
                    errs.append("异常: " + str(desc).splitlines()[0][:90])
                elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
                    vals = [str(a.get("value", a.get("description", "")))[:60]
                            for a in p.get("args", [])]
                    errs.append("console.error: " + " ".join(vals)[:90])
                elif (m == "Log.entryAdded"
                      and (p.get("entry") or {}).get("level") == "error"):
                    errs.append("log: " + str(p["entry"].get("text", ""))[:90])

            ok_txt = txt >= 80
            ok_err = not errs
            ok_route = routed
            bad += (0 if ok_txt else 1) + (0 if ok_err else 1) + (0 if ok_route else 1)
            flag = "OK   " if (ok_txt and ok_err and ok_route) else "★    "
            note = ""
            if not ok_route:
                # 落到别的页面时，说清楚"实际停在哪一页"，否则这句日志没用
                note = (f" → 路由未生效，导航项「{landed.get('txt') or '?'}」仍选中"
                        f"（未知 hash 会停在上一个页面，不会报错）")
            elif not ok_err:
                note = f" → {errs[0]}"
            print(f"    {flag}{k:12s} 内容 {txt:>5} 字 · 报错 {len(errs)} 条"
                  f" · 路由 {'生效' if ok_route else '失效'}{note}")
    finally:
        br.close()
    print(f"\n  运行时问题：{bad} 处")
    return bad


def check_bar_shape(port) -> int:
    """条形元素的形状：圆角是否到位、总计柱是否有实底。

    【为什么值得单独验】
    这两条都是"没人会报错、只会有人皱眉"的问题：
      · 圆角：全站 .bar-fill / .stack-bar 早就从 3px 提到 6px，
        归因页这两张图的柱子却还是 1–2px。1px 在 100px 宽的柱子上
        等于直角，图上看起来像"没做样式"。它是漏改，不会报错。
      · 总计柱：曾经是"淡底 + 近白描边"的空心框，理由是纯白刺眼。
        但在 1280 宽的窗口里它又高又宽，描边比任何数据柱都亮，
        中间却是空的 —— 第一眼读成"这块图没渲染出来"。
        换成实心中性灰之后才读得对。

    所以这里量的不是"好看"，是两条可证伪的性质：
      1. 柱子的取值端要有明显圆角（≥5px，6px 在 10px 高的条上即半圆）
      2. 总计柱必须有实底（alpha ≥ 0.9），不能是描边空框
    第二条尤其要看 alpha：空心框的 backgroundColor 往往也是"设过的"，
    只看"有没有背景色"会漏判。
    """
    section("条形形状：取值端圆角 / 零点端齐平 / 总计柱有实底")
    bad = 0
    port += 1
    br = Browser(port, f"{BASE}/#/attribution", width=1280, height=900)
    try:
        br.ctl.wait_until("!!document.querySelector('.bi-wa-bar')", 90)
        time.sleep(1.5)
        r = br.ctl.js(r"""(() => {
          const rad = (s) => {
            const v = String(s).trim().split(/\s+/).map(parseFloat);
            if (v.length === 1) return [v[0], v[0], v[0], v[0]];
            if (v.length === 2) return [v[0], v[1], v[0], v[1]];
            if (v.length === 3) return [v[0], v[1], v[2], v[1]];
            return v;
          };
          const alpha = (s) => {
            const m = String(s).match(/rgba?\(([^)]+)\)/);
            if (!m) return 1;
            const p = m[1].split(',').map(x => x.trim());
            return p.length > 3 ? parseFloat(p[3]) : 1;
          };
          const q = (sel) => document.querySelector(sel);
          const wa = [...document.querySelectorAll('.bi-wa-bar')]
            .find(b => !b.className.includes('is-total'));
          const tot = q('.bi-wa-bar.is-total');
          const dp = q('.bi-dv-bar.pos'), dn = q('.bi-dv-bar.neg');
          const g = (e) => e ? getComputedStyle(e) : null;
          return {
            wa: wa ? rad(g(wa).borderRadius) : null,
            totBg: tot ? g(tot).backgroundColor : null,
            totAlpha: tot ? alpha(g(tot).backgroundColor) : null,
            dp: dp ? rad(g(dp).borderRadius) : null,
            dn: dn ? rad(g(dn).borderRadius) : null,
            totH: tot ? Math.round(tot.getBoundingClientRect().height) : 0,
            totW: tot ? Math.round(tot.getBoundingClientRect().width) : 0,
          };
        })()""") or {}

        # 1) 瀑布柱：四角都圆
        wa = r.get("wa") or [0, 0, 0, 0]
        ok = min(wa) >= 5
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}瀑布柱四角圆角 {wa}（应都 ≥5，2px 在 100px 宽的柱上等于直角）")

        # 2) 发散柱：外端圆、零点端齐平
        dp, dn = r.get("dp") or [0, 0, 0, 0], r.get("dn") or [0, 0, 0, 0]
        # 角序 [TL, TR, BR, BL]；右伸的圆右侧、左伸的圆左侧
        ok = dp[1] >= 5 and dp[2] >= 5 and dp[0] == 0 and dp[3] == 0
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}右伸柱 [TL,TR,BR,BL]={dp}（远端圆、零点端 0）")
        ok = dn[0] >= 5 and dn[3] >= 5 and dn[1] == 0 and dn[2] == 0
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}左伸柱 [TL,TR,BR,BL]={dn}（远端圆、零点端 0）")

        # 3) 总计柱：必须有实底
        a = r.get("totAlpha")
        ok = a is not None and a >= 0.9
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}总计柱底色 alpha={a}（{r.get('totBg')}，"
              f"{r.get('totW')}×{r.get('totH')}px）—— 空心框会被读成\"没渲染\"")

        # 4) 堆叠份额条：同一个卡片切到「挽回潜力」后再看
        # 【按标题找卡片，不按下标】
        # 这里原来是 `.pg-card`[3] —— 2026-11-03 归因页删掉「为什么这样判」那张卡后，
        # 后面的卡片整体前移一格，这个下标就指到了另一张卡上：
        # 找不到「挽回潜力」分段控件 → 没点 → `.bi-ss-bar` 不存在 → 量出 [0]。
        # 报错信息是"圆角 0"，看着像样式退化，实际是选择器指错了元素。
        # 下标会随任何一次增删卡而失效，标题不会。
        br.ctl.js("""(() => {
          const c = [...document.querySelectorAll('.pg-card')].find(e => {
            const t = e.querySelector('.pg-card-t');
            return t && t.innerText.includes('卖的东西变了吗');
          });
          if (!c) return false;
          const b = [...c.querySelectorAll('.bi-seg-i')].find(x => x.innerText.trim() === '挽回潜力');
          if (b) b.click();
          return !!b;
        })()""")
        time.sleep(1.4)
        ss = br.ctl.js(r"""(() => {
          const e = document.querySelector('.bi-ss-bar');
          if (!e) return null;
          const v = String(getComputedStyle(e).borderRadius).trim().split(/\s+/).map(parseFloat);
          return v.length === 1 ? [v[0], v[0], v[0], v[0]] : v;
        })()""")
        # 【"没找到元素"和"圆角是 0"必须分开报】
        # 两者都判定为失败，但一句"圆角 [0]"会把人引去改样式，
        # 而真正的问题往往是"视图没切过去 / 元素没渲染"。
        if ss is None:
            bad += 1
            print("    ★    堆叠份额条没找到（.bi-ss-bar 不存在，先确认切视图有没有生效）")
        else:
            ok = min(ss) >= 5
            bad += 0 if ok else 1
            print(f"    {'OK   ' if ok else '★    '}堆叠份额条圆角 {ss}（应 ≥5）")

        # 5) 别的页面上的条形（同一批组件在别处又实现了一遍）
        #    归因页修好不等于全站都对：`.bi-bar-*`（仪表盘横向条）与
        #    `.bi-mini-*`（表格里的迷你条）都还是 2px，跟这一页毫无关系，
        #    却出现在同一个界面的相邻页面里。所以按"类名清单"逐个钉住，
        #    而不是只量这几张图。
        br.ctl.js("location.hash = '#/dashboard'")
        br.ctl.wait_until("!!document.querySelector('.bi-bar-f')", 60)
        time.sleep(1.6)
        dash = br.ctl.js(r"""(() => {
          const one = (sel) => {
            const e = document.querySelector(sel);
            if (!e) return null;
            const v = String(getComputedStyle(e).borderRadius).trim().split(/\s+/).map(parseFloat);
            return v.length === 1 ? v[0] : Math.min(...v);
          };
          return {f: one('.bi-bar-f'), track: one('.bi-bar-track'),
                  mini: one('.bi-mini-f'), miniTrack: one('.bi-mini')};
        })()""") or {}
        ok = (dash.get("f") or 0) >= 5 and (dash.get("track") or 0) >= 5
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}仪表盘横条 f={dash.get('f')} / track={dash.get('track')}（应 ≥5）")
        # 迷你条：轨道与填充必须同圆角，否则短条右端会露出一块直角
        mn, mnt = dash.get("mini"), dash.get("miniTrack")
        ok = mn is not None and mn >= 5 and abs((mn or 0) - (mnt or 0)) < 0.5
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}迷你条 填充 {mn} = 轨道 {mnt}（应相等且 ≥5）")

        br.ctl.js("location.hash = '#/reasons'")
        br.ctl.wait_until("!!document.querySelector('.pg-heat-fill')", 60)
        time.sleep(1.6)
        heat = br.ctl.js(r"""(() => {
          const e = document.querySelector('.pg-heat-fill');
          if (!e) return null;
          const v = String(getComputedStyle(e).borderRadius).trim().split(/\s+/).map(parseFloat);
          return v.length === 1 ? v[0] : Math.min(...v);
        })()""")
        ok = heat is not None and heat >= 4
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}原因页热力格 {heat}（是色块不是条，应 ≥4）")

        # ---- 矩阵每一格都必须印出数字（0 也要印） ----
        # 【为什么这条要单独查】
        # 原来 `v > 0 ? v.toFixed(1) : ''` 把 0 渲染成空白，
        # 于是"这一类目没有这种退货"（真值 0）和"格子没算出来/没渲染"
        # 在屏幕上是同一个样子。矩阵上出现几个洞，读者只能猜。
        # 判据：格子数 > 0，且**没有一个空格**，且内容全是数字。
        cells = br.ctl.js(r"""(() => {
          const all = [...document.querySelectorAll('.pg-heat-td .pg-heat-fill')];
          const txt = all.map(e => (e.innerText || '').trim());
          return {
            cells: all.length,
            empty: txt.filter(t => t === '').length,
            zeros: txt.filter(t => t === '0').length,
            nonnum: txt.filter(t => t !== '' && !/^\d+(\.\d+)?$/.test(t)).length,
          };
        })()""") or {}
        n = cells.get("cells") or 0
        ok = n > 0 and cells.get("empty") == 0 and cells.get("nonnum") == 0
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}原因矩阵 {n} 格全部有数字"
              f"（空白 {cells.get('empty')} 格 · 其中写作 0 的 {cells.get('zeros')} 格"
              f" · 非数字 {cells.get('nonnum')} 格）")
        if not ok:
            print("          -> 空白格会被读成\"没渲染/缺数\"，0 必须印成 0")
    finally:
        br.close()
    print(f"\n  条形形状问题：{bad} 处")
    return bad


def check_bi_board(port) -> int:
    """原因治理页：两块改成联动看板之后，联动是不是真的在联动。

    【为什么单独立一节，而不是加点静态断言】
    这一页的交互有三个"看起来完全正常"的失败方式：
      · 口径切换按钮点了、样式也变了，但数字没变（切了等于没切）
      · 点矩阵格有反馈（描边），但整页范围没跟着切 —— 上下两块口径分家
      · 36 格全给了 tabIndex=0：键盘"能用"，但要按 36 次才能走过去
    三个都不会报错、不会变形、不会溢出，静态断言一个也看不见。
    只有真的去点、去按、去数，才会露出来。

    【守的不变量】
      1. 36 格都有可访问名，且名字里带着占比 + 绝对条数 + 绝对损失
         （读屏没有浮层，浮层里的东西必须同时在 aria-label 里）
      2. 默认口径的格子数字 = 接口的 share
      3. 切到「占损失」后数字真的变了，且 = 接口的 loss_share
      4. 表格里按 Enter 选中一行 → 矩阵对应列头点亮，且**不压暗任何格子**
      5. 点矩阵一格 → 页面范围切到那个类目、表格那一行跟着亮、锁定的是那一格
      6. 整块矩阵只有 1 个 Tab 停靠点，方向键能移，停靠点跟着走
      7. 下钻把范围切成单类目之后，矩阵仍然是全类目的 n 行 —— 不塌成 1 行

    【第 4 条里那句"不压暗"是有来历的】
    第一版锁定后用 opacity 把其余格子压暗，看截图才发现：
    颜色通道本来就在编码数值大小，压到 .38 之后一个被压暗的 27%
    和一个正常的 8% 长得一模一样。**两个通道打架，读者反而读错。**
    所以锁定只允许用不改颜色的信号（列头 / 行标签竖条 / 格内描边），
    压暗只留给悬停那一瞬间。这条断言防的就是"哪天又给锁加回压暗"。
    """
    section("原因治理：两块看板的交叉联动（口径 / 选中 / 下钻 / 键盘）")
    bad = 0
    br = Browser(port, f"{BASE}/#/reasons", width=1440, height=900)
    try:
        br.ctl.wait_until(
            "document.querySelectorAll('.pg-heat-td').length > 0 "
            "&& !document.querySelector('.pg-loading')", 90)
        time.sleep(1.2)

        data = json.loads(urllib.request.urlopen(
            BASE + "/api/pages/reasons", timeout=30).read())
        m = data["matrix"]
        cats, codes = m["categories"], m["cause_codes"]
        by_key = {(c["category"], c["code"]): c for c in m["cells"]}

        def near(shown, num):
            """格子上的数是"两位小数四舍五入到一位"，不能用字符串相等去比。

            接口给 27.25、界面显示 27.3，而 Python 的 f"{27.25:.1f}" 给 27.2
            —— 一个银行家舍入、一个半入，两个都对，比出来却是失败。
            判据应该是"显示值离真值不超过半个末位"。"""
            try:
                return abs(float(shown) - num) <= 0.05 + 1e-9
            except Exception:
                return False

        def dom():
            return br.ctl.js(r"""(() => {
              return [...document.querySelectorAll('.pg-heat-td')].map(td => ({
                r: +td.dataset.r, c: +td.dataset.c,
                label: td.getAttribute('aria-label') || '',
                txt: (td.innerText || '').trim(),
              }));
            })()""") or []

        cells = dom()
        ok = len(cells) == len(m["cells"])
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}矩阵 {len(cells)} 格"
              f"（接口 {len(m['cells'])} 格）")

        miss = [x for x in cells
                if not all(k in x["label"] for k in
                           ("占本类目退货", "条", "损失", "占本类目损失"))]
        ok = len(cells) > 0 and not miss
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}每格的可访问名都带占比 + 条数 + 损失额"
              + ("" if ok else f"（缺 {len(miss)} 格：读屏拿不到浮层里的数）"))

        # ---- 2) 默认口径 ----
        def vals():
            return {(cats[x["r"]], codes[x["c"]]): x["txt"] for x in dom()}

        v1 = vals()
        mism = [k for k, v in v1.items() if not near(v, by_key[k]["share"])]
        ok = not mism
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}默认口径「占条数」的格子数字 = 接口 share"
              + ("" if ok else f"（{len(mism)} 格不符，如 {mism[0]}）"))

        # ---- 3) 切口径 ----
        clicked = br.ctl.js(r"""(() => {
          const b = [...document.querySelectorAll('.bi-seg-i')]
            .find(x => (x.innerText || '').trim() === '占损失');
          if (!b) return false;
          b.click();
          return true;
        })()""")
        time.sleep(0.8)
        v2 = vals()
        changed = [k for k in v2 if v2[k] != v1.get(k)]
        ok = bool(clicked) and len(changed) > 0
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}切到「占损失」后数字真的变了"
              f"（{len(changed)}/36 格）")
        mism2 = [k for k, v in v2.items() if not near(v, by_key[k]["loss_share"])]
        ok = not mism2
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}新数字 = 接口 loss_share"
              + ("" if ok else f"（{len(mism2)} 格不符，如 {mism2[0]}）"))

        # ---- 4) 表格回车选中一行 ----
        REASON = "材质做工问题"
        focus = br.ctl.js("""(() => {
          const tr = [...document.querySelectorAll('.pg-table tbody tr.clickable')]
            .find(t => t.innerText.includes('%s'));
          if (!tr) return 'no-row';
          tr.focus();
          return document.activeElement === tr ? 'ok' : 'focus-failed';
        })()""" % REASON)
        ok = focus == "ok"
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}表格行可被聚焦（键盘够得着）"
              + ("" if ok else f"：{focus}"))
        br.ctl.key("Enter", "Enter", 13)
        time.sleep(0.7)
        st = br.ctl.js(r"""(() => {
          const on = document.querySelector('.pg-table tbody tr.is-on');
          return {
            on: on ? on.innerText.split('\n')[0] : null,
            colTh: [...document.querySelectorAll('.pg-heat-th.is-col')]
              .map(e => (e.innerText || '').trim()),
            isRow: document.querySelectorAll('.pg-heat tbody tr.is-row').length,
            dim: document.querySelectorAll('.pg-heat-td.is-dim').length,
          };
        })()""") or {}
        ok = bool(st.get("on")) and REASON in (st.get("on") or "")
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}回车选中了那一行"
              + ("" if ok else f"：{st.get('on')}"))
        ok = st.get("colTh") == [REASON]
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}矩阵对应列头点亮"
              + ("" if ok else f"：{st.get('colTh')}"))
        ok = st.get("dim") == 0
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}锁定时不压暗格子"
              f"（压暗 {st.get('dim')} 格 —— 颜色深浅只能表示数值大小）")

        # ---- 4b) 悬停：临时十字线 + 浮层 ----
        hv = br.ctl.js("""(() => {
          const td = document.querySelector('.pg-heat-td[data-r="0"][data-c="1"]');
          if (!td) return null;
          td.scrollIntoView({block: 'center'});
          const b = td.getBoundingClientRect();
          return {x: Math.round(b.left + b.width / 2),
                  y: Math.round(b.top + b.height / 2)};
        })()""")
        if hv:
            br.ctl.send("Input.dispatchMouseEvent", type="mouseMoved",
                        x=hv["x"], y=hv["y"])
        time.sleep(0.6)
        hst = br.ctl.js(r"""(() => {
          const tip = document.querySelector('.bi-tip');
          return {dim: document.querySelectorAll('.pg-heat-td.is-dim').length,
                  tip: tip ? (tip.innerText || '') : null};
        })()""") or {}
        # 4 行 + 9 列 − 交叉那 1 格 = 12 格留在十字线上，其余 24 格压暗
        ok = hst.get("dim") == 24
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}悬停时十字线成立（压暗 {hst.get('dim')} 格，应 24）")
        ok = bool(hst.get("tip")) and "条" in (hst.get("tip") or "")
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}悬停浮层带绝对条数与损失"
              + ("" if ok else "（只有占比的话，读者仍不知道那是多少条）"))

        # ---- 5) 点矩阵一格下钻 ----
        ri, ci = cats.index("数码"), m["causes"].index(REASON)
        pos = br.ctl.js("""(() => {
          const td = document.querySelector('.pg-heat-td[data-r="%d"][data-c="%d"]');
          if (!td) return null;
          /* 先滚进视口再量坐标：视口只有 805px 高，矩阵第 2 行可能落在
             y=872 —— 点的位置在屏幕外面，点击会被浏览器丢掉，
             看起来像"点击事件没绑上"。 */
          td.scrollIntoView({block: 'center'});
          const b = td.getBoundingClientRect();
          const x = Math.round(b.left + b.width / 2);
          const y = Math.round(b.top + b.height / 2);
          const top = document.elementFromPoint(x, y);
          return {x: x, y: y,
                  hit: !!(top && top.closest && top.closest('.pg-heat-td') === td)};
        })()""" % (ri, ci))
        ok = bool(pos) and pos.get("hit") is True
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}目标格滚进视口、坐标确实落在它身上"
              + ("" if ok else f"：{pos}"))
        if pos:
            for t in ("mousePressed", "mouseReleased"):
                br.ctl.send("Input.dispatchMouseEvent", type=t, x=pos["x"],
                            y=pos["y"], button="left", clickCount=1)
        time.sleep(2.0)
        br.ctl.wait_until(
            "document.querySelectorAll('.pg-heat-td').length > 0 "
            "&& !document.querySelector('.pg-loading')", 60)
        time.sleep(0.8)
        st2 = br.ctl.js(r"""(() => {
          const chip = document.querySelector('.pg-fchip.on');
          const on = document.querySelector('.pg-table tbody tr.is-on');
          return {
            chip: chip ? chip.innerText.trim() : null,
            on: on ? on.innerText.split('\n')[0] : null,
            pick: [...document.querySelectorAll('.pg-heat-td.is-pick')]
              .map(td => (+td.dataset.r) + ',' + (+td.dataset.c)),
          };
        })()""") or {}
        ok = st2.get("chip") == "数码"
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}点格子后整页范围切到「数码」"
              + ("" if ok else f"：{st2.get('chip')}"))
        ok = bool(st2.get("on")) and REASON in (st2.get("on") or "")
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}表格里那一行跟着亮"
              + ("" if ok else f"：{st2.get('on')}"))
        ok = st2.get("pick") == ["%d,%d" % (ri, ci)]
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}矩阵锁定的正是点的那一格"
              + ("" if ok else f"：{st2.get('pick')}"))

        # ---- 5b) 下钻之后矩阵不塌成一行 ----
        # 【为什么这条也要守】矩阵跟不跟着范围筛选走，两种做法都不会报错。
        # 但跟着筛就只剩 1 行，"类目 × 原因"变成"类目内 × 原因"，
        # 横向比较这个唯一的用途就没了 —— 而屏幕上不会有任何提示。
        # 页顶写着"范围：数码"、底下还是 4 行，看着像筛选没生效，
        # 所以卡片说明里明说了它固定是全类目视角；这条断言守的是那个承诺。
        nrow = br.ctl.js(
            "document.querySelectorAll('.pg-heat tbody tr').length") or 0
        ok = nrow == len(cats)
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}下钻后矩阵仍是 {len(cats)} 行"
              f"（不随范围筛选变）（现在 {nrow} 行）")

        # ---- 6) roving tabindex ----
        tb = br.ctl.js(r"""(() => {
          const all = [...document.querySelectorAll('.pg-heat-td')];
          const zero = all.filter(x => x.tabIndex === 0);
          return {all: all.length, zero: zero.length};
        })()""") or {}
        ok = tb.get("zero") == 1
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}整块只有 1 个 Tab 停靠点"
              f"（{tb.get('zero')}/{tb.get('all')} —— 全给 tabIndex=0 要按 36 次）")

        # ---- 7) 方向键 ----
        start = br.ctl.js("""(() => {
          const td = document.querySelector('.pg-heat-td[tabindex="0"]');
          if (!td) return null;
          td.focus();
          return (+td.dataset.r) + ',' + (+td.dataset.c);
        })()""")
        br.ctl.key("ArrowRight", "ArrowRight", 39)
        time.sleep(0.5)
        after = br.ctl.js("""(() => {
          const a = document.activeElement;
          if (!a || !a.classList.contains('pg-heat-td')) return null;
          return {at: (+a.dataset.r) + ',' + (+a.dataset.c),
                  zero: [...document.querySelectorAll('.pg-heat-td')]
                    .filter(x => x.tabIndex === 0).length};
        })()""")
        if start and after:
            r0, c0 = [int(x) for x in start.split(",")]
            ok = after["at"] == f"{r0},{c0 + 1}" and after["zero"] == 1
            bad += 0 if ok else 1
            print(f"    {'OK   ' if ok else '★    '}按 → 从 ({start}) 移到 "
                  f"({after['at']})，停靠点跟着走")
        else:
            bad += 1
            print(f"    ★    按 → 后焦点跑出矩阵（start={start} after={after}）")
    finally:
        br.close()
        time.sleep(0.4)
    print(f"\n  看板联动问题：{bad} 处")
    return bad


def check_comp_interact(port) -> int:
    """竞品雷达页：三块 BI 看板（点级图 / 维度分布条 / 蝴蝶图）。

    【守的不变量】
      1. 点级图 5 行，每行都有基准虚线段；竞品行可 Tab、回车选中
      2. 选中竞品后：点级图 1 行 is-on、表格 1 行 is-sel 且 aria-current、
         维度分布条里该竞品的 6 个点点亮、其余竞品 18 个点压暗
      3. 再点一次/再按一次回车 = 取消（BI 的选中必须可逆）
      4. 分布条整块只有 1 个 Tab 停靠点，方向键移动游标（roving）
      5. 蝴蝶图两侧各 12 行，hover 出浮层且浮层带"偏向 N 倍"
      6. 点级图 hover 出浮层且浮层带 p 值

    【为什么单独写一节】
    选中联动跨三个模块（点级图→表格→分布条），任何一处 state 断了
    （比如某块还读旧变量）都不会报错——界面照常渲染，只是不跟着动。
    """
    section("竞品雷达：三块看板的联动与键盘（点级图 / 分布条 / 蝴蝶图）")
    bad = 0
    br = Browser(port, f"{BASE}/#/competitors", width=1440, height=900)
    try:
        br.ctl.wait_until(
            "document.querySelectorAll('.pg-cdot-r').length === 5 "
            "&& document.querySelectorAll('.pg-dstrip-d').length > 0", 90)
        time.sleep(1.0)

        # 1) 结构：5 行点级图、每行有基准段；分布条 6 行 × 5 点、6 条范围线
        n = br.ctl.js(r"""(() => ({
          rows: document.querySelectorAll('.pg-cdot-r').length,
          bases: document.querySelectorAll('.pg-cdot-base').length,
          dots: document.querySelectorAll('.pg-dstrip-d').length,
          ranges: document.querySelectorAll('.pg-dstrip-range').length,
          bfly: document.querySelectorAll('.pg-bfly-row').length,
          stops: [...document.querySelectorAll('.pg-dstrip-d')].filter(d => d.tabIndex === 0).length,
        }))()""") or {}
        ok = n.get('rows') == 5 and n.get('bases') == 5
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}点级图 5 行且每行带本店基准线（实际 {n}）")
        ok = n.get('dots') == 30 and n.get('ranges') == 6
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}分布条 30 点 + 6 条范围线")
        ok = n.get('bfly') == 24
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}蝴蝶图两侧共 24 行")
        ok = n.get('stops') == 1
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}分布条 roving：整类只有 1 个可聚焦点（实际 {n.get('stops')}）")

        # 2) 选中联动：点竞品C → 三处同步
        br.ctl.js(r"""[...document.querySelectorAll('.pg-cdot-r')]
          .find(r => r.textContent.includes('竞品C')).click()""")
        time.sleep(0.6)
        n = br.ctl.js(r"""(() => ({
          on: document.querySelectorAll('.pg-cdot-r.is-on').length,
          tr: !!document.querySelector('.pg-table tr.is-sel'),
          cur: document.querySelector('.pg-table tr.is-sel')?.getAttribute('aria-current'),
          lit: document.querySelectorAll('.pg-dstrip-d.is-on').length,
          dim: document.querySelectorAll('.pg-dstrip-d.is-dim').length,
        }))()""") or {}
        ok = n.get('on') == 1 and n.get('tr') and n.get('cur') == 'true'
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}选中竞品C：点级图 1 行选中 + 表格行 aria-current 同步")
        ok = n.get('lit') == 6 and n.get('dim') == 18
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}分布条：该竞品 6 点亮、其余 18 点压暗（实际 {n}）")

        # 3) 取消：回车再按一次（键盘路径）
        br.ctl.js(r"""(() => {
          const r = [...document.querySelectorAll('.pg-cdot-r.is-on')][0];
          r.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
        })()""")
        time.sleep(0.5)
        n = br.ctl.js("document.querySelectorAll('.pg-cdot-r.is-on').length")
        ok = n == 0
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}回车再按一次 = 取消选中（可逆）")

        # 4) roving：focus 停靠点 → 方向键必须真的搬焦点（activeElement 变）
        br.ctl.js(r"""(() => {
          const t = [...document.querySelectorAll('.pg-dstrip-d')].find(d => d.tabIndex === 0);
          t.focus();
        })()""")
        br.ctl.js(r"""(() => {
          const t = [...document.querySelectorAll('.pg-dstrip-d')].find(d => d.tabIndex === 0);
          t.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowRight', bubbles: true}));
        })()""")
        time.sleep(0.4)
        ok = br.ctl.js(r"""(() => {
          const cur = document.querySelector('.pg-dstrip-d.is-cur');
          return !!cur && document.activeElement === cur;
        })()""")
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}方向键把焦点真搬到下一个点（activeElement 跟随）")

        # 5) 蝴蝶图浮层带"偏向 N 倍"
        br.ctl.js(r"""document.querySelector('.pg-bfly-row').dispatchEvent(
          new MouseEvent('mousemove', {bubbles: true, clientX: 500, clientY: 600}))""")
        time.sleep(0.5)
        tip = br.ctl.js("(document.querySelector('.bi-tip')||{}).innerText || ''")
        ok = '偏向' in tip and '倍' in tip
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}蝴蝶图浮层把 log₂比 翻译成\"偏向 N 倍\"")
        br.ctl.js(r"""document.querySelector('.pg-bfly-row').dispatchEvent(
          new MouseEvent('mouseleave', {bubbles: true}))""")
        time.sleep(0.4)

        # 6) 点级图浮层带 p 值（新开一个探针避免读到残留浮层）
        br.ctl.js(r"""document.querySelectorAll('.pg-cdot-r.clickable')[0].dispatchEvent(
          new MouseEvent('mousemove', {bubbles: true, clientX: 600, clientY: 400}))""")
        time.sleep(0.5)
        tip = br.ctl.js("[...document.querySelectorAll('.bi-tip')].map(t=>t.innerText).join(' ') || ''")
        ok = 'p ' in tip and '负评率' in tip
        bad += 0 if ok else 1
        print(f"    {'OK   ' if ok else '★    '}点级图浮层带负评率 + p 值")
    except Exception as e:                          # noqa: BLE001
        bad += 1
        print(f"    ★    探测失败：{type(e).__name__}: {e}")
    finally:
        br.close()
    return bad


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=5).read()
    except Exception:
        print(f"[x] {BASE} 没有响应，先启动服务")
        return 2

    total = 0
    total += check_focus(9430)
    total += check_reduced_motion(9431)
    total += check_narrow(9432)
    total += check_interactive(9434)
    total += check_semantics(9436)
    total += check_forced_colors(9438)
    total += check_reviews_chart(9440)
    total += check_attribution_views(9442)
    total += check_treemap(9442)
    total += check_bi_charts(9442)
    total += check_radius_tokens()
    total += check_dist_fresh()
    total += check_console_clean(9452)
    total += check_keyboard_reachable(9470)
    total += check_bar_shape(9450)
    total += check_plan_numbers()
    total += check_attr_promo()
    total += check_reasons_recon()
    total += check_plan_interact(9486)
    total += check_bi_board(9490)
    total += check_comp_interact(9494)

    print(f"\n{'=' * 68}")
    print("全部通过" if total == 0 else f"合计 {total} 处问题")
    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
