# -*- coding: utf-8 -*-
"""全站文字对比度验收（WCAG 2.1 AA）。

    python check_contrast.py               # 全部：6 个功能页 + 问答页 + 交互态
    python check_contrast.py pages         # 只查静态页面
    python check_contrast.py chat          # 只查问答页（欢迎态 + 回答后）
    python check_contrast.py states        # 只查 hover / 选中 等交互态

=== 为什么需要这个脚本 ===

v4 把 4 个语义色砍成灰阶之后，最大的风险不再是"配色不好看"，
而是"看不见"——白字落在白底上、深灰字落在中灰底上。

这类问题有三个特点，决定了它必须靠机器而不是靠眼睛：
  1. **截图看不出来。** 1440px 的截图缩到 1000px 显示，人眼会自动补足
     对比度。我最初版本的 --fg-4(#5a5a61) 在深底上只有 2.88 的对比度，
     看着"挺有层次"，但低亮度屏、投影仪、或者稍微眯一下眼就彻底消失。
  2. **全站规模。** 一次审计跑出 74 处不达标，全部是同一类：
     "标签 / 单位 / 说明"这类小字。手工逐个查不可能。
  3. **改配色必复发。** 每次调 :root 里的色阶，所有引用处一起变，
     而有些元素有自己的局部底色（浮层、徽章、选中态），
     全局变量调好了不代表它就好了。

=== 判据 ===

WCAG 2.1 AA：
  · 正文（< 18.5px，或 < 14px 且非加粗）  对比度 ≥ 4.5
  · 大字（≥ 18.5px，或 ≥ 14px 且加粗）    对比度 ≥ 3.0

只有"直接含文字节点"的元素参与计算。整块容器的继承色不重复算，
否则同一个问题会被报几十次。

=== 背景色的取值比字色难 ===

文字自己通常没有背景，真实底色来自祖先链，中间可能夹着半透明层。
必须逐层 alpha 合成，否则算出来的对比度比实际好很多。
渐变还要单独处理（getComputedStyle().backgroundColor 对渐变返回透明）——
这两处都在下面踩过坑，注释里写清楚了。
"""
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websocket

EDGE = [
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
]
HERE = Path(__file__).resolve().parent
OUT = HERE / "_tmp" / "pages"
BASE = "http://127.0.0.1:8848"
# 默认底色：与 styles/app.css 的 --bg 保持一致。
# 它是合成链的兜底值，改了 --bg 记得同步这里。
ROOT_BG = "11,11,12"

AUDIT_JS = r"""
(() => {
  const ROOT_BG = [__ROOT_BG__, 1];

  /* ---------- 1. 颜色解析 ---------- */
  const parse = (c) => {
    if (!c) return null;
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return c === 'transparent' ? [0, 0, 0, 0] : null;
    const p = m[1].split(/[,/\s]+/).filter(Boolean).map(Number);
    return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
  };

  /* ---------- 2. alpha 合成：把上层盖到下层上 ---------- */
  const over = (top, bot) => {
    const a = top[3];
    if (a >= 1) return [top[0], top[1], top[2], 1];
    return [
      top[0] * a + bot[0] * (1 - a),
      top[1] * a + bot[1] * (1 - a),
      top[2] * a + bot[2] * (1 - a),
      1,
    ];
  };

  /* ---------- 3. 沿祖先链求真实底色 ---------- */
  const bgOf = (el) => {
    const stack = [];
    let n = el;
    while (n && n.nodeType === 1) {
      const cs = getComputedStyle(n);
      let bc = parse(cs.backgroundColor);

      /* 【渐变底必须单独处理，且必须 alpha 加权】
         backgroundColor 对渐变返回 transparent，真实底色在
         backgroundImage 里。不处理的话，品牌标那种"白渐变底 + 深字"
         会被算成"深底 + 深字"，报 1.04 的假警报。

         但**不能简单取 RGB 平均**：`linear-gradient(transparent,
         rgba(255,255,255,.06))` 这种顶部装饰高光，RGB 平均会得到
         rgb(128,128,128) —— 一个根本不存在的"中灰底"，于是问答页
         报了 5 处假警报。装饰渐变的 RGB 是亮的、alpha 接近 0，
         忽略 alpha 就会把它当实色。
         按 alpha 加权求颜色，再取平均 alpha 作为这一层的遮盖度。 */
      const bi = cs.backgroundImage;
      if ((!bc || bc[3] === 0) && bi && bi.indexOf('gradient') >= 0) {
        const cols = (bi.match(/rgba?\([^)]+\)/g) || []).map(parse).filter(Boolean);
        const sumA = cols.reduce((s, c) => s + c[3], 0);
        if (cols.length && sumA > 0.01) {
          bc = [
            cols.reduce((s, c) => s + c[0] * c[3], 0) / sumA,
            cols.reduce((s, c) => s + c[1] * c[3], 0) / sumA,
            cols.reduce((s, c) => s + c[2] * c[3], 0) / sumA,
            sumA / cols.length,
          ];
        }
      }

      if (bc && bc[3] > 0) {
        stack.push(bc);
        if (bc[3] >= 1) break;
      }
      n = n.parentElement;
    }
    let out = ROOT_BG.slice();
    for (let i = stack.length - 1; i >= 0; i--) out = over(stack[i], out);
    return out;
  };

  /* ---------- 4. 相对亮度 + 对比度（WCAG 官方公式） ---------- */
  const lum = (c) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const ratio = (a, b) => {
    const la = lum(a), lb = lum(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  };

  /* ---------- 5. 遍历 ---------- */
  const bad = [];
  const seen = new Set();
  const walk = (el) => {
    if (el.nodeType !== 1) return;
    const tag = el.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'svg' || tag === 'PATH') return;

    // 只处理"直接含文字节点"的元素，避免同一问题被祖先和后代各报一次
    let ownText = '';
    for (const c of el.childNodes) {
      if (c.nodeType === 3) ownText += c.nodeValue;
    }
    ownText = ownText.trim();

    if (ownText.length > 0) {
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const opacity = Number(cs.opacity);
      const visible = cs.display !== 'none' && cs.visibility !== 'hidden'
                      && opacity > 0.25 && r.width > 2 && r.height > 2;
      if (visible) {
        const fg = parse(cs.color);
        if (fg) {
          // opacity 作用在整棵子树上，要乘进字色
          const fga = over([fg[0], fg[1], fg[2], fg[3] * opacity], [0, 0, 0, 1]);
          const bg = bgOf(el);
          const cr = ratio(fga, bg);

          const size = parseFloat(cs.fontSize);
          const bold = Number(cs.fontWeight) >= 600;
          const large = size >= 18.5 || (size >= 14 && bold);
          const need = large ? 3.0 : 4.5;

          if (cr < need) {
            // 去重键：同色 + 同底 + 同字号 视为同一类问题
            const key = cs.color + '|' + bg.map(x => Math.round(x)).join(',')
                        + '|' + cs.fontSize;
            if (!seen.has(key)) {
              seen.add(key);
              bad.push({
                sel: el.className && typeof el.className === 'string'
                     ? '.' + el.className.trim().split(/\s+/).join('.') : tag,
                text: ownText.slice(0, 34),
                color: cs.color,
                bg: `rgb(${bg.map(x => Math.round(x)).join(',')})`,
                cr: Math.round(cr * 100) / 100,
                need: need,
                size: cs.fontSize,
                weight: cs.fontWeight,
              });
            }
          }
        }
      }
    }
    for (const c of el.children) walk(c);
  };
  walk(document.body);
  bad.sort((a, b) => a.cr - b.cr);
  return { total: bad.length, items: bad.slice(0, 24) };
})()
""".replace("__ROOT_BG__", ROOT_BG)

PAGES = [
    ('dashboard', '#/dashboard', "document.querySelectorAll('.bi-heat-c').length > 0"),
    # 归因页就绪判据原来等的是 `.pg-gate-item`（判定卡）。
    # 2026-11-03 那张卡删了（内容与 KPI 副标题重复），判据要跟着换 ——
    # 否则不是"检查失败"，而是永远等不到、90s 后超时，看起来像后端挂了。
    # 换成 .pg-weekbar：它同样只在数据回来之后才渲染。
    ('attribution', '#/attribution', "document.querySelectorAll('.pg-weekbar').length > 0"),
    ('reasons', '#/reasons', "document.querySelectorAll('.pg-fchip').length > 0"),
    ('reviews', '#/reviews', "document.querySelectorAll('.bi-bar-row').length > 0"),
    ('competitors', '#/competitors', "document.querySelectorAll('.pg-card').length > 0"),
    # 自检页（eval）2026-11-03 已从导航移除，不再审它的对比度。
    # chat 不在这里：它有自己的两节（欢迎态 / 真实回答后），
    # 因为推理链、结论卡这些元素要等一条回答回来才存在。
    # 2026-11-03 曾把它加进来，结果是同一个页面被审两遍、表头"6 个功能页"变成错话 —— 已撤回。
]

# 交互态。v4 新增了十几种"状态样式"，而状态样式恰恰最容易漏对比度 ——
# 它只在鼠标放上去时才存在，静态审计一次都碰不到。
# 元组：(页面, 选择器, 说明)
STATES = [
    ('dashboard', '.bi-scope-i:not(.on)', '范围胶囊 · 悬停'),
    ('dashboard', '.bi-scope-i.on', '范围胶囊 · 已选中悬停'),
    ('dashboard', '.bi-seg-i:not(.on)', '分段控件 · 悬停'),
    ('dashboard', '.bi-seg-i.on', '分段控件 · 已选中悬停'),
    ('dashboard', '.sidenav-item:not(.on)', '侧栏项 · 悬停'),
    ('dashboard', '.sidenav-item.on', '侧栏项 · 已选中悬停'),
    ('dashboard', '.pg-btn', '按钮 · 悬停'),
    ('dashboard', '.bi-bar-row', '条形行 · 悬停'),
    ('dashboard', '.bi-heat-c', '热力格 · 悬停'),
    ('dashboard', '.chip', '状态 chip · 悬停'),
    ('attribution', '.pg-wchip', '周次胶囊 · 悬停'),
    ('attribution', '.pg-tag', '状态标签 · 悬停'),
    ('attribution', '.pg-mini', '周次箭头 · 悬停'),
    ('reasons', '.pg-fchip', '原因页筛选 · 悬停'),
]

READY = {name: ready for name, _, ready in PAGES}


def find_edge() -> str:
    for p in EDGE:
        if Path(p).exists():
            return p
    raise SystemExit("[x] 找不到 Edge / Chrome。这个脚本靠真实浏览器取计算样式，"
                     "没有它就等于没验。")


def free_port(preferred: int, tries: int = 40) -> int:
    """从 preferred 起，找一个**此刻真的能 bind** 的端口。

    【为什么必须先自己 bind 一次】
    原来是直接 Popen 带 --remote-debugging-port=N。如果 N 还被上一轮没退干净的
    实例占着，新启动的 Edge 会因为端口冲突而绑定失败退出，但 `http://127.0.0.1:N/json`
    仍然是**旧实例**在应答 —— 探针的等待循环顺利拿到 ws_url，一路连到别人的页面上，
    goto 之后采样到的状态与本次要查的无关。

    后果不是报错，而是**偶发假红**：连跑 10 次会红一次，报一处根本不存在的不达标。
    这种红比没有门禁更坏 —— 它教人忽略红灯。

    注意不要设 SO_REUSEADDR：Windows 上它允许 bind 一个已被监听的端口，
    那样"探测"永远成功，等于没探测。
    """
    for i in range(tries):
        p = preferred + i
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(('127.0.0.1', p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"从 {preferred} 起 {tries} 个端口都被占用，无法启动调试实例")


class Ctl:
    """轻量 CDP 客户端。只需要 Runtime.evaluate 和 Input.dispatchMouseEvent。

    【events 缓冲】
    CDP 的事件（Runtime.exceptionThrown / Log.entryAdded / consoleAPICalled…）
    和命令响应走同一条 WebSocket，靠 `id` 字段区分。原来这个循环把
    "不是我的响应"直接丢掉 —— 于是**页面的运行时报错全被静默吞了**，
    任何脚本都不可能发现"某个卡片炸了、渲染成空白"。
    现在非响应消息都进 self.events，供需要的人事后翻。
    """

    def __init__(self, ws, port):
        self.ws = ws
        self.port = port
        self.n = 0
        self.events = []
        ws.send(json.dumps({"id": 0, "method": "Runtime.enable"}))

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                return msg.get("result", {})
            if msg.get("method"):
                self.events.append(msg)
                # 缓冲别无限长：跑 12 个 tab 时 console 可能刷几百条
                if len(self.events) > 4000:
                    del self.events[:2000]

    def js(self, expr):
        r = self.send("Runtime.evaluate", expression=expr,
                      returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            return {"__error__": str(r["exceptionDetails"])[:160]}
        return r.get("result", {}).get("value")

    def wait_until(self, expr, timeout=45):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.js(expr):
                return True
            time.sleep(0.3)
        return False

    def move_mouse(self, x, y):
        """真实鼠标移动。不是加 :hover 类 ——
        伪类能测样式，但测不到"这个元素到底在不在鼠标下面"。"""
        self.send("Input.dispatchMouseEvent", type="mouseMoved",
                  x=int(x), y=int(y), button="none")


class Browser:
    def __init__(self, port, url):
        # 先占一个真空闲的端口，避免连到上一轮残留实例（见 free_port 注释）
        port = free_port(port)
        prof = OUT / f"_profile_chk_{port}_{int(time.time() * 1000)}"
        self.proc = subprocess.Popen(
            [find_edge(), "--headless=new", "--disable-gpu", "--no-first-run",
             f"--remote-debugging-port={port}", "--user-data-dir=" + str(prof),
             "--window-size=1440,1200", url],
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
            raise RuntimeError("CDP 连接超时（端口可能被上一个实例占用）")
        self.ws = websocket.create_connection(ws_url, timeout=60)
        self.ctl = Ctl(self.ws, port)

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


def check(url, ready_expr, label, port):
    """打开页面 → 等就绪 → 审计。返回不达标项数。"""
    br = Browser(port, url)
    try:
        br.ctl.wait_until(ready_expr, timeout=60)
        time.sleep(1.2)          # 让入场动画和过渡收尾，否则量到中间态
        res = br.ctl.js(AUDIT_JS) or {}
        return report(label, res)
    finally:
        br.close()


def report(tag, res):
    if not res or "__error__" in (res or {}):
        print(f"[{tag}] 脚本异常: {(res or {}).get('__error__')}")
        return 99
    items = res.get("items") or []
    total = res.get("total", 0)
    print(f"[{tag}] {'OK' if total == 0 else f'★ {total} 处不达标'}")
    for b in items:
        print(f"   {b['cr']:>5.2f} (需 {b['need']})  {b['size']}/{b['weight']}  "
              f"{b['color']} on {b['bg']}")
        print(f"          {b['sel'][:56]}  「{b['text']}」")
    return total


def section(title):
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def run_pages(port):
    section("静态页面：6 个功能页，每个元素的文字都要看得清")
    total = 0
    for name, h, ready in PAGES:
        port += 1
        try:
            total += check(f"{BASE}/{h}", ready, name, port)
        except Exception as e:                                  # noqa: BLE001
            print(f"[{name}] 失败：{type(e).__name__}: {e}")
            total += 99
        print()
    return total


def run_chat(port):
    section("问答页：欢迎态 + 真实回答后（推理链/结论卡这时才存在）")
    total = 0
    br = Browser(port, BASE + "/?q=%E6%9C%80%E6%96%B0%E4%B8%80%E5%91%A8%E6%80%8E%E4%B9%88%E6%A0%B7#/chat")
    try:
        br.ctl.wait_until("!!document.querySelector('.composer textarea')", timeout=45)
        time.sleep(1.0)
        total += report("问答页 · 欢迎态", br.ctl.js(AUDIT_JS))

        # 判据用右栏的「N 步 · X.Xs」：这是整个页面上唯一一个
        # "后端真的说完了"的直接证据。等输入框之类都会误判（提问后它被清空）。
        done = br.ctl.wait_until(
            "(() => { const e = document.querySelector('.chat-rr');"
            " return !!(e && /\\d+\\s*步/.test(e.innerText)); })()", timeout=100)
        time.sleep(1.0)
        if not done:
            print("\n   [警告] 回答未在 100s 内完成，下面这轮审计的是半成品界面")
        total += report("问答页 · 回答后", br.ctl.js(AUDIT_JS))
    finally:
        br.close()
    return total


def run_states(port):
    section("交互态：hover / 选中 —— 状态样式只在鼠标放上去时存在")
    total = 0
    by_page = {}
    for page, sel, label in STATES:
        by_page.setdefault(page, []).append((sel, label))

    for page, items in by_page.items():
        port += 1
        br = None
        try:
            br = Browser(port, f"{BASE}/#/{page}")
            br.ctl.wait_until(READY.get(page, "document.querySelectorAll('.pg-card').length>0"),
                              timeout=60)
            time.sleep(1.2)
            print(f"\n--- {page} ---")
            for sel, label in items:
                pos = br.ctl.js(f"""(() => {{
                  const e = document.querySelector({sel!r});
                  if (!e) return null;
                  const r = e.getBoundingClientRect();
                  if (r.width < 2 || r.height < 2) return null;
                  return {{x: r.x + r.width / 2, y: r.y + r.height / 2}};
                }})()""")
                if not pos:
                    # 跳过不等于通过，必须显式打出来，
                    # 否则选择器写错会被当成"没有这个问题"
                    print(f"  [跳过] {label}：元素不存在或不可见")
                    continue
                br.ctl.move_mouse(pos["x"], pos["y"])
                time.sleep(0.5)          # > 最长过渡 320ms
                total += report(label, br.ctl.js(AUDIT_JS))
                br.ctl.move_mouse(3, 3)
                time.sleep(0.2)
        except Exception as e:                                  # noqa: BLE001
            print(f"[{page}] 失败：{type(e).__name__}: {e}")
            total += 99
        finally:
            if br:
                br.close()
            time.sleep(0.4)
    return total


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    OUT.mkdir(parents=True, exist_ok=True)

    # 先确认服务在跑。不在跑就没必要启浏览器了。
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=5).read()
    except Exception:
        print(f"[x] {BASE} 没有响应。先启动服务：\n"
              "      cd agent/backend && python -m uvicorn main:app --port 8848")
        return 2

    total = 0
    port = 9410
    if what in ('all', 'pages'):
        total += run_pages(port)
        port += 10
    if what in ('all', 'chat'):
        total += run_chat(port)
        port += 10
    if what in ('all', 'states'):
        total += run_states(port)

    print(f"\n{'=' * 68}")
    if total == 0:
        print("全部通过：所有可见文字的对比度都达到 WCAG AA。")
    else:
        print(f"合计 {total} 处不达标。")
        print("修法通常是两条：")
        print("  1. 抬高一档文字色（--fg-3 → --fg-2），或")
        print("  2. 该元素自己的底色偏亮，需要局部覆写而不是改全局变量")
    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
