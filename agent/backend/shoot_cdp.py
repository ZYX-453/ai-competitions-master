# -*- coding: utf-8 -*-
"""用 CDP 精确等待页面渲染完成后再截图。

【为什么不用 --screenshot】
第一条路是 `msedge --headless --screenshot`，它的问题是**只截一次、立刻返回**。
本项目是 SSE 流式回答：页面加载 → 发请求 → 后端边算边推 → 前端逐字渲染。
--virtual-time-budget 推进的是虚拟时钟，管不住真实网络的往返，
于是截到的是"提问气泡刚出现、回答还空着"的中间态 ——
右侧工具覆盖全是「-」，中间区域一整片空白。
拿这种图去核对"用户看到什么"，等于什么都没核对。

【CDP 的做法】
开一个带 --remote-debugging-port 的 Edge，连上它的 WebSocket，
用 Runtime.evaluate 轮询 DOM，直到：
  · 回答区域出现结论标题（说明 answer 事件已渲染）
  · 且「工具覆盖」里不再有「-」（说明所有 tool_call 都回填了）
再调 Page.captureScreenshot 按内容高度截全图。
这样截到的一定是稳定态。

【为什么按 fullPage 手动计算高度】
CDP 原生支持 captureBeyondViewport，配合内容高度就能一次截全，
但内容高度要靠 JS 量：document.documentElement.scrollHeight。
本项目是固定三栏布局（左右 rail 各占一列，中间滚动），
所以真正要量的不是整页，而是中间主列的 scrollHeight。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket  # 来自 websocket-client

EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
BASE = "http://127.0.0.1:8848/"
PORT = 9333
OUT = Path(__file__).resolve().parent / "_tmp" / "shots"


def list_pages() -> list[dict]:
    """列出当前所有 page 类型的 target。"""
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=3) as r:
        tabs = json.load(r) or []
    return [t for t in tabs
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]


def close_strays(keep_id: str) -> list[str]:
    """关掉除 keep_id 以外的页面，返回被关掉的 url 列表。

    【这件事必须做，理由是实测出来的，不是洁癖】
    Edge 带 `--remote-debugging-port` 启动时，实际会开出 **两个** target，
    而且两个的 URL 一模一样（都是我们传进去的那个）。
    于是出现这种极难自证的组合：
      · Runtime.evaluate 落在 A 页 → 我们代点登录 → A 页变成问答页 → 正文正确
      · Page.captureScreenshot 的像素却来自 B 页 → B 页还停在登录卡片
    两份证据互相打架，而图又"看着很干净"。
    只留一个页面，就不存在"像素来自哪一页"这个问题了。
    """
    killed = []
    for t in list_pages():
        tid = t.get("id")
        if not tid or tid == keep_id:
            continue
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{PORT}/json/close/{tid}", timeout=3).read()
            killed.append(str(t.get("url"))[:60])
        except Exception:
            pass
    return killed


def pick_target(prefer_prefix: str = "http://127.0.0.1",
                timeout: float = 25.0) -> dict:
    """等 DevTools 起来，挑出"我们那个"页面，返回整个 target 字典（含 id）。

    【为什么不能取第一个】
    原来这里是 "遍历 tabs，第一个 type==page 就返回"。
    实测打脸：DevTools 里会同时存在 **2 个 page**，
    一个是我们的标签页，另一个是 Edge 自己带出来的（会话恢复/新标签页）。
    连上哪一个全看 /json 的顺序，而顺序不保证。

    更坑的是**症状完全不像"连错靶子"**：
      · Runtime.evaluate 落在了对的页面上 → 落盘的正文是完全正确的问答页
      · 但 Page.captureScreenshot 出的像素来自另一个窗口 → 图上是一张登录卡片
    一次运行产出两份互相矛盾的证据，而图看着"很干净"，很容易当成截图脚本没修好。
    所以这里按 URL 前缀挑，挑不到就明确报出来，不猜。
    """
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{PORT}/json", timeout=3) as r:
                tabs = json.load(r) or []
            pages = [t for t in tabs
                     if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            ours = [t for t in pages
                    if str(t.get("url") or "").startswith(prefer_prefix)]
            if ours:
                return ours[0]
            if pages:
                # 页面可能还没导航到目标地址，等一轮再说
                pass
        except Exception:
            pass
        time.sleep(0.4)
    # 超时了：把现场说清楚，别静默截错页
    try:
        pages = list_pages()
    except Exception:
        pages = []
    if pages:
        print(f"  [警告] 没有 url 以 {prefer_prefix} 开头的页面，"
              f"现有 {[str(t.get('url'))[:50] for t in pages]}")
        return pages[0]
    raise RuntimeError("DevTools 端口没起来")

CASES = [
    ("cdp_digital_w52", "数码类目第52周为什么退货率高"),
    ("cdp_greeting", "你好你是谁"),
    # 【v2.7 新增：数据里没有这一类的原因】
    # 这一条要看的不是"答得好不好"，而是**界面会不会露出一堆空壳**。
    # 知识库路径没有条数、没有损失、没有 KPI 卡片（kpi=[]），
    # 如果前端对空 kpi 的处理不对，右上角会显示四个"—"，
    # 看起来像"数据加载失败"，而不是"这类问题没有数据"。
    ("cdp_reason_kb", "标签脱落这个问题该怎么解决"),
    # 有真实数据的那条，用来对照：KPI 卡片应该是满的
    ("cdp_reason_p1", "尺码版型不符这个问题该怎么解决"),
]


def wait_devtools(timeout: float = 25.0) -> str:
    """等 DevTools 端口起来，返回 webSocketDebuggerUrl。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2) as r:
                tabs = json.load(r)
            for t in tabs:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.4)
    raise RuntimeError("DevTools 端口没起来")


class CDP:
    def __init__(self, url: str):
        self.ws = websocket.create_connection(url, timeout=60)
        self.n = 0

    def send(self, method: str, **params):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.send("Runtime.evaluate", expression=expr,
                      returnByValue=True, awaitPromise=False)
        return r.get("result", {}).get("value")


def capture(name: str, question: str) -> Path | None:
    # 【URL 必须带 #/chat，否则截到的是总览页】
    # ChatPage 只在 page==='chat' 时渲染，而 page 由 hash 决定，
    # 默认是 'dashboard'。原来这里只拼了 ?q=...，于是：
    #   页面加载 → App 解析 hash（空）→ dashboard → 根本没挂 ChatPage
    #   → ?q= 那个 useEffect 压根没执行 → 也不会有 answer 事件
    #   → 等 90s 超时 → 截下来是一张完整的总览页
    # 危险的地方在于：图看着"是对的"（总览页渲染得很好），只有对着
    # 用例清单核对才会发现"这不是我问的那一题"。
    # 这是加 hash 路由（v4.0）时漏改的，此后没重跑过截图脚本，所以一直没暴露。
    #
    # parseHash 只读 hash 部分（#/chat），?q= 仍从 window.location.search 读，
    # 两者互不干扰，所以答案是 search + hash 同时给。
    url = (BASE + "?" + urllib.parse.urlencode({"q": question, "pace": "0"})
           + "#/chat")
    # 【配置目录必须每次全新】
    # 原来固定用 OUT/_cdp_profile。第二次跑时 Edge 会从这个目录恢复上次的会话，
    # 于是 DevTools 里出现 2 个 page：一个是我们的标签页，一个是恢复出来的。
    # 后果见 pick_target 的注释：正文对、截图错。
    # 换成临时目录，跑完删掉 —— 没有可恢复的会话，就只有我们这一页。
    _profile = Path(tempfile.mkdtemp(prefix="cdp_profile_"))
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={PORT}",
        "--user-data-dir=" + str(_profile),
        "--no-first-run", "--no-default-browser-check",
        # 【必须禁同步】实测第二个 page 是 `edge://sync-confirmation-dialog/`，
        # 即 Edge 自己弹的同步确认窗口。它在运行中途出现（不是启动时），
        # 于是"启动时数一遍页面"根本数不到它，而截图那一刻它已经在了。
        "--disable-sync", "--disable-background-networking",
        "--window-size=1440,1000",
        url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _tab = pick_target("http://127.0.0.1")
        _killed = close_strays(_tab["id"])
        if _killed:
            print(f"  · 已关掉 {len(_killed)} 个多余页面（否则像素可能来自它们）")
            time.sleep(0.4)
        c = CDP(_tab["webSocketDebuggerUrl"])
        c.send("Page.enable")
        c.send("Runtime.enable")

        # ---- 先过登录页 ----
        # 【登录页是 v4 之后加的，截图脚本因此又一次全线失效】
        # Login.tsx 的 isLoggedIn 初始 false，也不读任何 URL 参数，
        # 于是 headless 打开后直接停在登录卡片上：
        #   .chat-shell 永远等不到 → 轮询一路等到 90s 超时 → 截到一张登录页。
        # 更麻烦的是"等着不动"看起来像后端挂了，排查方向会完全跑偏。
        # 这里在开始轮询前代点一次登录（演示环境不校验口令，任意值即可）。
        #
        # 注意 React 受控 input：必须用原生 setter + 派发 input 事件。
        # 直接 el.value = 'x' 不会触发 onChange，React 里的 state 仍是空，
        # 点提交只会报"请输入账号"，然后又是一轮空等。
        # 必须轮询，不能只试一次：Edge 刚启动时页面可能还是空白，
        # 一次就判"不是登录页"会直接漏过。三种返回值的含义：
        #   waiting       —— 页面还没渲染出登录卡片（继续等）
        #   submitting    —— 已点过，登录按钮在 400ms 校验里 disabled（继续等）
        #   login-clicked —— 刚点上去（继续等它变成 logged-in）
        #   logged-in     —— .chat-shell 已挂载，可以交给下面的稳定态轮询
        _probe_js = """(function(){
          if (document.querySelector('.chat-shell')) return 'logged-in';
          var acc = document.getElementById('login-account');
          var pwd = document.getElementById('login-password');
          if (!acc || !pwd) return 'waiting';
          var btn = document.querySelector('button.login-btn');
          if (!btn) return 'waiting';
          if (btn.disabled) return 'submitting';
          var setter = Object.getOwnPropertyDescriptor(
              window.HTMLInputElement.prototype, 'value').set;
          [[acc, 'demo'], [pwd, 'demo']].forEach(function(p){
            setter.call(p[0], p[1]);
            p[0].dispatchEvent(new Event('input', { bubbles: true }));
          });
          btn.click();
          return 'login-clicked';
        })()"""
        _seen, _t_login = '', time.time()
        while time.time() - _t_login < 30:
            try:
                _v = c.js(_probe_js)          # 注意用 c.js()：CDP.send 是 (method, **params)
            except Exception as _e:
                _v = f"err:{type(_e).__name__}"
            if _v and _v != _seen:
                print(f"  · 登录处理：{_v}")
                _seen = _v
            if _v == "logged-in":
                break
            time.sleep(0.5)
        else:
            print(f"  · 登录处理超时：最后一次状态={_seen!r}")

        # ---- 收掉登录遮罩层 ----
        # 【这是本次排查最费劲的一条，结论反直觉，记下来】
        # 现象：登录成功后，DOM 查着一切正常 ——
        #   .chat-shell 存在、.login-page 数量 0、正文也是完整的问答页；
        #   可截出来的像素**永远是登录卡片**。
        # 排除过程（都排除了）：
        #   · 不是连错靶子：DevTools 里 page=1，且 Runtime.evaluate 的 href 正确
        #   · 不是页面重载：window 上的内存标记跨过 setDeviceMetricsOverride 还在
        #   · 不是截图管道坏了：导航到空白页能截出白页
        # 真因：dist 里登录页的根节点是 `.glass-login`，
        # position:fixed、z-index:99999、铺满整个视口（1410x905）。
        # 登录后 React 把里面的表单子树摘掉了，但这个**空壳容器仍然在**，
        # 而无头 Edge（--disable-gpu）保留着它上一帧的合成层 ——
        # 于是像素上一直挂着一张已经不存在于 DOM 里的登录卡片。
        # 把这一层收掉，合成器拿到新的一帧，问答页才露出来。
        # 它只是个装饰底（问答页自己有底色），收掉不影响"用户看到什么"的判断。
        _hide = c.js("""(function(){
          var n = 0;
          document.querySelectorAll('.glass-login').forEach(function(e){
            e.style.display = 'none'; n++;
          });
          return n;
        })()""")
        if _hide:
            print(f"  · 已收掉 {_hide} 层登录遮罩（否则像素会停在登录卡片）")
            time.sleep(0.4)

        # 轮询直到稳定。
        # 【判据试了四版才定下来，过程记录如下，免得以后又走回头路】
        # 第一版：有工具覆盖 + 正文够长 + 没有未回填横杠
        #   → 截到"正在用 qwen-plus 组织结论"那一帧，结论还没写出来。
        # 第二版：加"正文里不含'正在'"
        #   → 永远等不到。因为"正在"是模型正文里的正常用词
        #     （"正在运营""正在出现问题"），不是加载状态。
        # 第三版：看发送按钮是否 disabled
        #   → 也永远等不到。按钮的逻辑是 `busy || !input.trim()`，
        #     提问后输入框被清空，所以它一直是 disabled，跟 busy 无关。
        # 第四版：认右栏那个 chip。
        #   前端只有在收到 done 事件后才渲染「N 步 · X.Xs」这个小标签，
        #   它是整个页面上唯一一个"后端真的说完了"的直接证据。
        # 第五版（当前）：见下面 uncovered 那段的说明。
        #
        # 【选择器必须按问答页的类名，不能用工作台的】
        # v4.0 加了多页面工作台之后，问答页的骨架是
        #   .chat-shell > .chat-rail / .chat-stage / .chat-rr
        # 而 .rail-left / .rail-right 是工作台那几个页面的类名。
        # 原来这里写 .rail-right .chip，在问答页上永远匹配不到 →
        # chip 恒为空 → doneChip 恒为 False → 每次等满 90s 超时，
        # 然后在"模型还在生成"的中间态截一张图。
        # 图本身不报错、甚至挺好看，只有比对文案才会发现
        # "正在用 qwen-plus 组织结论"还挂在屏幕上。
        # 现在两个都试，工作台/问答页都能用。
        t0 = time.time()
        ready = False
        state = {}
        while time.time() - t0 < 90:
            state = c.js("""(() => {
              const t = document.body.innerText || '';
              const chip = document.querySelector('.chat-rr .chip')
                        || document.querySelector('.rail-right .chip');
              const chipTxt = chip ? chip.innerText : '';
              return {
                len: t.length,
                badge: !!document.querySelector('.badge'),
                doneChip: chipTxt.includes('步'),
                chipTxt: chipTxt,
                pending: (t.match(/\\s-\\s/g) || []).length,
                // 【第五版追加：本次调用过的工具，覆盖面板里必须有次数】
                // 【这个判据我写错过一次，记下来省得再犯】
                // 第一版查的是"有几个 pill 显示—"，要求等于 0。
                // 但系统一共 13 个工具，一次问答只调 2 个，剩 11 个本来就不该亮，
                // 于是永远等不到 0、每次超时。要保证的不是"全亮"，
                // 而是"调过的都亮了"。
                // 【第二版又踩了一个】：两个区块都用 .tool-pill 类名，
                // 全局选会把"本轮推理"里的也当成覆盖面板。
                // 只能按 .rr-sec-h 的标题文字定位到具体区块。
                uncovered: (() => {
                  const sec = [...document.querySelectorAll('.rr-sec')].find(
                    el => (el.querySelector('.rr-sec-h') || {}).innerText === '工具覆盖');
                  if (!sec) return 0;
                  const used = [...document.querySelectorAll('.chain .step')]
                    .map(el => (el.getAttribute('data-tool') || '').trim())
                    .filter(Boolean);
                  const pills = [...sec.querySelectorAll('.tool-pill')];
                  // 从推理链拿不到工具名时，退一步：只看推理区块里的名字
                  const names = used.length ? used
                    : [...document.querySelectorAll('.rr-sec')]
                        .filter(el => (el.querySelector('.rr-sec-h') || {}).innerText === '本轮推理')
                        .flatMap(el => [...el.querySelectorAll('.tool-pill .tp-name')]
                          .map(x => x.innerText.trim()));
                  return pills.filter(el => {
                    const nm = (el.querySelector('.tp-name') || el).innerText.trim();
                    const cnt = (el.querySelector('.tp-cnt') || el).innerText || '';
                    return names.includes(nm) && cnt.includes('—');
                  }).length;
                })(),
              };
            })()""")
            if (state and state.get("doneChip")
                    and state.get("pending", 1) == 0
                    and state.get("uncovered", 1) == 0):
                time.sleep(0.9)
                ready = True
                break
            time.sleep(0.5)

        if not ready:
            print(f"  [警告] {name} 未在 90s 内进入稳定态，仍尝试截图")
            print(f"         状态：badge={state.get('badge')} "
                  f"chip={state.get('chipTxt')!r} pending={state.get('pending')}")

        # 量内容高度：取三栏里最高的那一栏
        # 问答页的类名是 .chat-rail / .chat-rr，工作台是 .rail-left / .rail-right，
        # 两边都量，取最大值——否则在问答页上量不到滚动区高度，
        # 只会截到首屏 945px，正文后面全被切掉。
        h = c.js("""(() => {
          const q = s => { const e = document.querySelector(s); return e ? e.scrollHeight : 0; };
          const sh = document.querySelector('.chat-shell');
          const st = document.querySelector('.stage-scroll');
          const rr = document.querySelector('.rr-scroll');
          // 【页头 + 输入框吃掉的高度】
          // .chat-shell 是 100vh 的网格：上面页头、下面输入框，中间才是滚动区。
          // 滚动区能用的高度 = 视口 - 这两块，所以"内容多高"不等于"视口要多高"。
          // 之前只把内容高度当视口高度，滚动区实际只拿到 内容-页头-输入框，
          // 于是答案最后那张「各类目退货率对照」表刚好卡在输入框上被切掉半截
          // —— 而且图看起来"完整"，只有对着正文核对才发现最后一块没了。
          const chromeOf = (outer, inner) =>
            (outer && inner) ? Math.max(0, outer.clientHeight - inner.clientHeight) : 0;
          const needStage = st ? st.scrollHeight + chromeOf(sh, st) : 0;
          const needRr = rr ? rr.scrollHeight + chromeOf(sh, rr) : 0;
          return Math.max(document.documentElement.scrollHeight,
                          q('.rail-left'), q('.rail-right'),
                          q('.chat-rail'), q('.chat-rr'), q('.chat-stage'),
                          needStage, needRr,
                          q('.stage-scroll'), q('.rr-scroll'), q('.rail-scroll'),
                          q('main') || 0,
                          window.innerHeight);
        })()""") or 1000
        h = int(min(max(h + 40, 900), 6000))

        # 把屏幕上的文字一起落盘。
        # 【为什么不满足于"截了张图"】
        # 图只有人眼能核，而这次要核的是"headline / 徽章 / summary / note
        # 四处说法是否一致"。靠看图逐字比对既慢又容易漏，
        # 而落成 .txt 就能直接 diff、能 grep、能进验收日志。
        # 图仍然是给人看的证据，txt 是给脚本核的证据，两者都要。
        try:
            _txt = c.js("""(() => {
              const el = document.querySelector('.stage-scroll')
                      || document.querySelector('.chat-stage')
                      || document.body;
              return el.innerText || '';
            })()""") or ''
            (OUT / f"{name}.txt").write_text(_txt, encoding='utf-8')
            print(f"  -> {name}.txt  ({len(_txt)} 字)")
        except Exception as _e:
            print(f"  · 正文落盘跳过：{type(_e).__name__}: {_e}")

        # 【截图前最后一道闸：只允许一个页面】
        # 这一条是被真实坑出来的，不是预防性的。
        # 症状：一次运行里落盘的 txt 是完整问答页，png 却是登录卡片。
        # 分步排查的结果很反直觉 ——
        #   · window 上的内存标记还在（页面没重载）
        #   · .chat-shell = 1、.login-page = 0（DOM 确实是问答页）
        #   · 可截出来的像素就是登录页
        # 真因：DevTools 里有 2 个 page（会话恢复带出来的那个），
        # Runtime.evaluate 落在我们的页面上，Page.captureScreenshot 的像素
        # 却来自另一个窗口。所以**查 DOM 查不出这个病**——
        # 必须直接数 target 数量，多于一个就拒绝出图。
        # 宁可不产出，也不要一张"看着很干净、其实不是这一页"的图：
        # 这种图会被人当成"已经验证过了"，比没有图更糟。
        # 截图前再清一次：多余页面是**中途冒出来**的（同步确认弹窗），
        # 只在启动时数一遍会漏。清完再数，数不到 1 个就拒绝出图。
        _killed2 = close_strays(_tab["id"])
        if _killed2:
            print(f"  · 截图前又关掉 {len(_killed2)} 个页面：{_killed2}")
            time.sleep(0.5)
        _pages = list_pages()
        _npages = len(_pages)
        _snap = c.js("""JSON.stringify({
          chat: document.querySelectorAll('.chat-shell').length,
          glass: document.querySelectorAll('.glass-login').length,
          head: (document.body.innerText||'').slice(0,20)
        })""")
        try:
            _s = json.loads(_snap or '{}')
        except Exception:
            _s = {}
        print(f"  · 截图检查：page={_npages} chat={_s.get('chat')} "
              f"glass={_s.get('glass')} head={(str(_s.get('head')) or '').replace(chr(10), ' ')}")
        if _npages != 1:
            raise RuntimeError(
                f"DevTools 里有 {_npages} 个页面，无法确定截的是哪一个（拒绝出图）")
        if not _s.get('chat'):
            raise RuntimeError(
                "截图瞬间页面上没有 .chat-shell，拒绝出图")

        import base64
        # 【高度要迭代，不能一次算准】
        # 第一版只量内容高度当视口高度 → 只截到一屏（外层的 scrollHeight 就是视口）
        # 第二版补上"页头 + 输入框"占掉的高度 → 仍然差一行，
        #   最后那张「各类目退货率对照」的第 4 行刚好压在输入框下面。
        # 原因是一堆小数：栅格间距、textarea 自适应高度、滚动条是否占位，
        # 都在视口变化后跟着变，任何"一次性算式"都会差几十像素。
        # 所以改成闭环：套用视口 → 量还差多少 → 不够就加高 → 再量。
        # 判据直接用 scrollHeight - clientHeight（真的被藏住了多少），
        # 不是"我觉得应该够高"。
        for _ in range(4):
            c.send("Emulation.setDeviceMetricsOverride", width=1440, height=h,
                   deviceScaleFactor=2, mobile=False)
            time.sleep(0.6)
            over = c.js("""(() => {
              const f = e => e ? Math.max(0, e.scrollHeight - e.clientHeight) : 0;
              return Math.max(f(document.querySelector('.stage-scroll')),
                              f(document.querySelector('.rr-scroll')));
            })()""") or 0
            if over <= 2:
                break
            print(f"  · 还差 {over}px 被藏住，视口加高到 {h + over + 40}")
            h = int(min(h + over + 40, 6000))
        shot = c.send("Page.captureScreenshot", format="png",
                      captureBeyondViewport=True)
        dst = OUT / f"{name}.png"
        dst.write_bytes(base64.b64decode(shot["data"]))
        print(f"  -> {dst.name}  (高 {h})")
        c.ws.close()
        return dst
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        # 临时配置目录用完就删：留着的话下次启动又会恢复会话
        shutil.rmtree(_profile, ignore_errors=True)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ok = 0
    for name, q in CASES:
        print(f"[{name}] {q}")
        try:
            if capture(name, q):
                ok += 1
        except Exception as e:
            print(f"  失败：{type(e).__name__}: {e}")
    print(f"\n完成 {ok}/{len(CASES)}")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())