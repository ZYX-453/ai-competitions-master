/** SSE 客户端：用 fetch + ReadableStream 手动解析。
 *
 * 为什么不用 EventSource：
 * 1. EventSource 只能发 GET，问题文本要进 query string，中文长句容易被截断或转义出错。
 * 2. EventSource 会自动重连，但对话流不应该重连——重连会把整条推理链再跑一遍。
 *
 * 这里最容易出错的地方是**分帧**：网络分片不保证按 SSE 事件边界切分，
 * 一个 `data: {...}` 可能被拆成两半到达。所以必须维护 buffer，
 * 只处理以 `\n\n` 结尾的完整帧。第一版没做这个，现场演示时会偶发
 * "Unexpected end of JSON input"。
 */

export type SseHandler = (evt: Record<string, unknown>) => void

export async function streamChat(
  body: { question: string; week?: number | null; pace_ms?: number },
  onEvent: SseHandler,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })

  if (!res.ok) {
    const txt = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}${txt ? `: ${txt.slice(0, 200)}` : ''}`)
  }
  if (!res.body) throw new Error('响应没有可读流')

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buf += decoder.decode(value, { stream: true })

    // 按空行分帧。保留最后一段（可能是半帧）到下一轮。
    const frames = buf.split('\n\n')
    buf = frames.pop() ?? ''

    for (const frame of frames) {
      const payload = frame
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trim())
        .join('')
      if (!payload) continue // `: connected` 这类注释帧直接跳过
      try {
        onEvent(JSON.parse(payload))
      } catch {
        // 单帧解析失败不应该中断整条流，但要能看见
        console.warn('[sse] 丢弃无法解析的帧', payload.slice(0, 120))
      }
    }
  }
}

/** 取 JSON。带超时与一次重试。
 *
 * 【为什么必须有超时】
 * 原来只有 fetch(url) —— 浏览器默认没有请求超时。后端某个接口卡住
 * （读大文件、算力吃满、GC 停顿）时，Promise 永远不 resolve 也不 reject，
 * 于是页面的 catch 永远不执行、`setErr` 永远不被调用，
 * 用户看到的是**永久转圈**：没有错误、没有重试按钮、不知道发生了什么。
 * 这比报错更糟 —— 报错至少告诉你可以重试。
 *
 * 【超时值怎么定的】
 * 实测各接口：最快 15ms（reviews）、最慢 141ms（dashboard）。
 * 取 20s 是给"冷启动 + 首次算 32 万行"留足余量，
 * 而不是按平均值卡。真正会慢的是首次请求，后续都有热缓存。
 *
 * 【为什么重试一次而不是三次】
 * 这个后端是本地单进程，失败基本分两类：瞬时（连接被拒、正在重启）
 * 和确定性（参数错、数据缺）。前者重试一次就好，后者重试十次也一样。
 * 重试三次只是把"3 秒后报错"拖成"9 秒后报错"，对演示无益。
 * 第二次失败就把真实错误抛出去，交给页面的 Fail 组件 + 重试按钮。
 */
export async function getJson<T>(
  url: string,
  opts?: { timeoutMs?: number; retries?: number },
): Promise<T> {
  const timeoutMs = opts?.timeoutMs ?? 20000
  const retries = opts?.retries ?? 1

  let lastErr: unknown = null
  for (let attempt = 0; attempt <= retries; attempt++) {
    /* 【AbortSignal.timeout 而不是手写 setTimeout + abort】
       前者在 fetch 结束时会自动清理内部定时器；手写的版本如果忘了
       clearTimeout，每次请求都会留一个游离的 timer —— 页面开久了
       会攒下几十个。Chrome 103+ / Firefox 100+ / Safari 16+ 都支持，
       这套界面本来就不支持更老的浏览器（用了 :has() 选择器）。 */
    const signal = AbortSignal.timeout(timeoutMs)
    try {
      const r = await fetch(url, { signal })
      if (!r.ok) {
        /* 【4xx 不重试】
           参数错误、路由不存在这类问题重试一定还是错。
           只有 5xx 和网络层失败值得再试一次。 */
        if (r.status >= 400 && r.status < 500) {
          throw new Error(`HTTP ${r.status} on ${url}`)
        }
        lastErr = new Error(`HTTP ${r.status} on ${url}`)
      } else {
        return (await r.json()) as T
      }
    } catch (e) {
      /* 超时要单独说清楚。用户看到 "The operation was aborted"
         完全不知道发生了什么，也不会想到是"后端没响应"。 */
      if (e instanceof DOMException && e.name === 'TimeoutError') {
        lastErr = new Error(`请求超时（${Math.round(timeoutMs / 1000)} 秒无响应）：${url}`)
      } else if (e instanceof Error && /^HTTP 4\d\d/.test(e.message)) {
        throw e          // 4xx 直接抛，不进入重试
      } else {
        lastErr = e
      }
    }
    /* 重试前稍等，避免后端正在重启时立刻打第二次 */
    if (attempt < retries) await new Promise((r) => setTimeout(r, 600))
  }
  throw lastErr instanceof Error ? lastErr : new Error(String(lastErr))
}