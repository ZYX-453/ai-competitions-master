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

export async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status} on ${url}`)
  return (await r.json()) as T
}