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
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websocket  # 来自 websocket-client

EDGE = r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
BASE = "http://127.0.0.1:8848/"
PORT = 9333
OUT = Path(__file__).resolve().parent / "_tmp" / "shots"

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
    url = BASE + "?" + urllib.parse.urlencode({"q": question, "pace": "0"})
    proc = subprocess.Popen([
        EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
        f"--remote-debugging-port={PORT}",
        "--user-data-dir=" + str(OUT / "_cdp_profile"),
        "--window-size=1440,1000",
        url,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        c = CDP(wait_devtools())
        c.send("Page.enable")
        c.send("Runtime.enable")

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
        # 第四版（当前）：认右栏那个 chip。
        #   前端只有在收到 done 事件后才渲染「N 步 · X.Xs」这个小标签，
        #   它是整个页面上唯一一个"后端真的说完了"的直接证据。
        #   判据：.rail-right .chip 存在，且文本里带"步"。
        t0 = time.time()
        ready = False
        state = {}
        while time.time() - t0 < 90:
            state = c.js("""(() => {
              const t = document.body.innerText || '';
              const chip = document.querySelector('.rail-right .chip');
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
        h = c.js("""(() => {
          const q = s => { const e = document.querySelector(s); return e ? e.scrollHeight : 0; };
          return Math.max(document.documentElement.scrollHeight,
                          q('.rail-left'), q('.rail-right'), q('main') || 0,
                          window.innerHeight);
        })()""") or 1000
        h = int(min(max(h + 40, 900), 6000))

        c.send("Emulation.setDeviceMetricsOverride", width=1440, height=h,
               deviceScaleFactor=2, mobile=False)
        time.sleep(0.5)
        shot = c.send("Page.captureScreenshot", format="png", captureBeyondViewport=True)
        import base64
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