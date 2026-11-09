/* ===========================================================================
 * 问答页（从原 App.tsx 迁入）
 *
 * 【这一页和其他六页的关系】
 * 其他页回答的是"一屏能看全"的问题；这一页回答的是"按需深入"的问题。
 * 所以它保留了三栏结构和流式推理链——那不是装饰，
 * 是让"Agent 到底查了什么"这件事在过程中就可见。
 *
 * 【迁入时改了什么】
 * 只改了两处：
 *   · 左栏的「数据底盘」四格搬到了工作台的侧边栏提示里，
 *     这里不再重复第二份（否则又是两套数字）
 *   · 顶栏由 App 统一渲染，这里不再自带
 * 其余交互逻辑一字未动——它已经被验收过，没有理由在搬家时顺手改。
 * =========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { streamChat, getJson } from '../lib/sse'
import type {
  AnswerEvent, DoneEvent, PlanEvent, SessionEvent, StepState, ThoughtEvent, Turn,
} from '../lib/types'
import type { NavData } from '../lib/pages'
import { Chain, TOOL_PAGE } from '../components/Chain'
import { Blocks } from '../components/Blocks'
import { Verdict } from '../components/Verdict'
import { ErrorBoundary } from '../components/ErrorBoundary'

interface Meta {
  categories: string[]
  weeks: { min: number; max: number }
  promo_weeks: number[]
  tools: { name: string; title: string; description: string }[]
  suggestions: { label: string; question: string }[]
}

export function ChatPage({ health, nav, go }: {
  health: { data: { rows: Record<string, number>; orders_year: number; return_rate: number } } | null
  nav: NavData
  /* 由 App 传入的页面跳转函数（go('attribution', 44)）。
     推理链步骤与右侧执行轨迹用它实现"点击直达对应数据页"。 */
  go?: (p: string, arg?: string | number) => void
}) {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getJson<Meta>('/api/meta').then(setMeta).catch(() => setMeta(null))
  }, [])

  // 自动跟到底部。只在"用户本来就在底部附近"时才滚，
  // 否则用户往上翻看证据时会被强行拽下来 —— 这是流式界面最常见的体验灾难。
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 220
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [turns])

  const patchTurn = useCallback((id: string, fn: (t: Turn) => Turn) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? fn(t) : t)))
  }, [])

  const ask = useCallback(
    async (question: string, paceOverride?: number) => {
      const q = question.trim()
      if (!q || busy) return

      const id = `t${Date.now()}`
      const turn: Turn = { id, question: q, thoughts: [], steps: [], startedAt: Date.now() }
      setTurns((prev) => [...prev, turn])
      setInput('')
      setBusy(true)

      const ac = new AbortController()
      abortRef.current = ac
      let seq = 0

      try {
        await streamChat(
          { question: q, pace_ms: paceOverride ?? 240 },
          (raw) => {
            const evt = raw as { type: string } & Record<string, unknown>
            switch (evt.type) {
              case 'session':
                patchTurn(id, (t) => ({ ...t, session: evt as unknown as SessionEvent }))
                break
              case 'plan':
                patchTurn(id, (t) => ({ ...t, plan: evt as unknown as PlanEvent }))
                break
              case 'thought':
                patchTurn(id, (t) => ({
                  ...t,
                  thoughts: [...t.thoughts, { text: (evt as unknown as ThoughtEvent).text, seq: seq++ }],
                }))
                break
              case 'tool_call': {
                const e = evt as unknown as { id: string; tool: string; title: string; args: Record<string, unknown> }
                const step: StepState = {
                  id: e.id, tool: e.tool, title: e.title, args: e.args,
                  status: 'running', seq: seq++,
                }
                patchTurn(id, (t) => ({ ...t, steps: [...t.steps, step] }))
                break
              }
              case 'tool_result': {
                const e = evt as unknown as {
                  id: string; ok: boolean; summary: string; ms: number
                  evidence: Record<string, unknown>; note: string; error: string
                }
                patchTurn(id, (t) => ({
                  ...t,
                  steps: t.steps.map((s) =>
                    s.id === e.id
                      ? { ...s, status: e.ok ? 'done' : 'fail', summary: e.summary, ms: e.ms,
                          evidence: e.evidence, note: e.note, error: e.error }
                      : s,
                  ),
                }))
                break
              }
              case 'answer':
                patchTurn(id, (t) => ({ ...t, answer: evt as unknown as AnswerEvent }))
                break
              case 'done':
                patchTurn(id, (t) => ({
                  ...t, done: evt as unknown as DoneEvent, finishedAt: Date.now(),
                }))
                break
              case 'error':
                patchTurn(id, (t) => ({ ...t, error: String(evt.message ?? '未知错误') }))
                break
            }
          },
          ac.signal,
        )
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          patchTurn(id, (t) => ({ ...t, error: String(e) }))
        }
      } finally {
        setBusy(false)
        abortRef.current = null
        patchTurn(id, (t) => ({ ...t, finishedAt: t.finishedAt ?? Date.now() }))
      }
    },
    [busy, patchTurn],
  )

  // 支持 ?q=第44周退货率为什么涨了 直接进入某个问题。
  // 用途：现场演示可以预先打开指定问题的链接，不用当众打字；
  // 截图核对时也能直接落到"有内容"的状态，而不是空页面。
  const bootRef = useRef(false)
  useEffect(() => {
    if (bootRef.current) return
    const sp = new URLSearchParams(window.location.search)
    const q = sp.get('q')
    if (q) {
      bootRef.current = true
      // pace=0 时后端不插间隔，整条流一次推完。
      // 用于截图核对与自动化测试——否则无头浏览器的虚拟时间等不到真实 sleep。
      const pace = sp.get('pace')
      setTimeout(() => ask(q, pace !== null ? Number(pace) : undefined), 60)
    }
  }, [ask])

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter 发送 / Shift+Enter 换行。
    // isComposing 判断不能省：中文输入法选词时按 Enter 也会触发 keydown，
    // 少了这个判断，用户打"为什么"按回车选词就会把半截问题发出去。
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      ask(input)
    }
  }

  const lastTurn = turns[turns.length - 1]
  const liveTools = useMemo(() => {
    const m: Record<string, number> = {}
    for (const t of turns) for (const s of t.steps) m[s.tool] = (m[s.tool] ?? 0) + 1
    return m
  }, [turns])

  return (
    <div className="chat-shell">
      <aside className="chat-rail">
        <div className="rail-head">
          <div className="rail-title">试着问</div>
          <div style={{ display: 'grid', gap: 1 }}>
            {(meta?.suggestions ?? []).map((s, i) => (
              <button key={i} className="suggest" onClick={() => ask(s.question)} disabled={busy}>
                <span className="arrow">›</span>
                <span>{s.label}</span>
              </button>
            ))}
          </div>
        </div>

        {meta && (
          <div className="rail-scroll" style={{ padding: '12px 14px' }}>
            <div className="rail-title" style={{ marginBottom: 6 }}>数据边界</div>
            <div className="tiny muted" style={{ lineHeight: 1.75, paddingLeft: 0 }}>
              周次 {meta.weeks.min}–{meta.weeks.max}
              <br />
              促销周 {meta.promo_weeks.join(' / ')}
              <br />
              类目 {meta.categories.join(' · ')}
            </div>
            <div className="tiny" style={{ color: 'var(--fg-4)', marginTop: 10, lineHeight: 1.65 }}>
              Agent 只在这份数据内作答。<br />超出范围会明确说"查不到"。
            </div>
          </div>
        )}
      </aside>

      <div className="chat-stage">
        {/* 【流式区域的读屏播报：全项目原来一处都没有】
            问答页是核心功能，但屏幕阅读器用户问完问题后**什么也听不到** ——
            所有内容都靠视觉增量渲染，辅助技术完全捕捉不到。

            【为什么不直接给 .stage-scroll 加 aria-live】
            流式输出每个 token 都在变，整块当作 live region 会让读屏
            每来几个字就播报一次，变成听觉轰炸，比不播报更糟。
            正确做法是"完成时播报一次摘要"，所以另建一个隐藏的 live region。

            【aria-atomic + 只在 done 后填内容】
            · aria-atomic="true"：每次变化重读整个区域，而不是只读增量
            · 内容取自 turn.done 存在时的 headline（后端已组装好的一句话结论）
            · 生成中留空：进度由 .stage-scroll 的 aria-busy 表达，
              读者不需要听"正在查询、正在计算"这种逐条播报

            这是视觉隐藏（.sr-only），不占布局、不影响任何现有排版。 */}
        <div className="sr-only" aria-live="polite" aria-atomic="true">
          {lastTurn?.done && lastTurn.answer
            ? `${lastTurn.question}。回答：${lastTurn.answer.headline}`
            : ''}
        </div>

        <div className="stage-scroll" ref={scrollRef} aria-busy={busy}>
          {turns.length === 0 ? (
            <EmptyState meta={meta} onPick={ask} />
          ) : (
            turns.map((t) => <TurnView key={t.id} turn={t} onOpen={go} />)
          )}
        </div>

        <div className="composer-wrap">
          <div className="composer">
            {/* 【显式 label 而不是只靠 placeholder】
                placeholder 在用户开始输入后立即消失，读屏用户回到输入框时
                听不到"这是什么框"。sr-only 的 label 用 for/id 关联，永久有效。 */}
            <label className="sr-only" htmlFor="chat-input">输入你的业务问题</label>
            <textarea
              id="chat-input"
              rows={1}
              value={input}
              placeholder="问一个业务问题，例如：第 44 周退货率为什么涨了"
              aria-describedby="chat-hint"
              onChange={(e) => {
                setInput(e.target.value)
                const el = e.currentTarget
                el.style.height = 'auto'
                el.style.height = `${Math.min(el.scrollHeight, 120)}px`
              }}
              onKeyDown={onKeyDown}
              disabled={busy}
            />
            <button className="send-btn" onClick={() => ask(input)} disabled={busy || !input.trim()} title="发送" aria-label="发送问题">
              {busy ? <div className="spinner" /> : <Arrow />}
            </button>
          </div>
          <div className="hint-row" id="chat-hint">
            <span>
              <kbd>Enter</kbd> 发送 · <kbd>Shift</kbd>+<kbd>Enter</kbd> 换行
              {busy && ' · 推理中，可点击推理链展开证据'}
            </span>
            <span>
              {health ? `${health.data.rows.panel_weekly.toLocaleString()} 行面板 · ${meta?.tools.length ?? 0} 个工具` : '连接中'}
            </span>
          </div>
        </div>
      </div>

      <aside className="chat-rr">
        <div className="rr-head">
          <span className="rr-title">运行状态</span>
          <span className="spacer" style={{ flex: 1 }} />
          {lastTurn?.done && (
            <span className="chip" style={{ height: 18, fontSize: 10 }}>
              {lastTurn.done.tool_calls} 步 · {(lastTurn.done.elapsed_ms / 1000).toFixed(1)}s
            </span>
          )}
        </div>

        <div className="rr-scroll">
          <div className="rr-sec">
            <div className="rr-sec-h">本轮推理</div>
            {!lastTurn || lastTurn.steps.length === 0 ? (
              <div className="tiny" style={{ color: 'var(--fg-4)', lineHeight: 1.6 }}>
                提问后这里会实时显示每一步的工具调用。
              </div>
            ) : (
              <div style={{ display: 'grid', gap: 2 }}>
                {lastTurn.steps.map((s) => {
                  const tp = go ? TOOL_PAGE[s.tool] : undefined
                  const wk = Number(s.args?.week)
                  const jump = tp && s.status === 'done'
                  return (
                    <div
                      key={s.id}
                      className={`tool-pill ${s.status === 'running' ? 'on' : ''}`}
                      onClick={jump && tp ? () => go!(tp, Number.isInteger(wk) && wk > 0 ? wk : undefined) : undefined}
                      style={jump ? { cursor: 'pointer' } : undefined}
                      title={jump ? '点击查看对应的数据页' : undefined}
                    >
                      <span style={{ width: 5, height: 5, borderRadius: '50%',
                        background: s.status === 'running' ? 'var(--accent)'
                          : s.status === 'fail' ? 'var(--bad)' : 'var(--fg-4)' }} />
                      <span className="tp-name">{s.tool}</span>
                      <span className="tp-cnt">{s.ms != null ? `${s.ms.toFixed(0)}ms` : '…'}</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          <div className="rr-sec">
            <div className="rr-sec-h">工具覆盖</div>
            <div style={{ display: 'grid', gap: 1 }}>
              {(meta?.tools ?? []).map((t) => {
                const n = liveTools[t.name] ?? 0
                return (
                  <div key={t.name} className="tool-pill">
                    <span className="tp-name" style={{ color: n > 0 ? 'var(--fg-2)' : undefined }}>
                      {t.name}
                    </span>
                    <span className="tp-cnt">{n > 0 ? `×${n}` : '—'}</span>
                  </div>
                )
              })}
            </div>
          </div>

          {health && (
            <div className="rr-sec">
              <div className="rr-sec-h">数据源</div>
              {Object.entries(health.data.rows).map(([k, v]) => (
                <div className="metric-line" key={k}>
                  <span className="ml-k">{k}.csv</span>
                  <span className="ml-v">{v.toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}

          <div className="rr-sec">
            <div className="rr-sec-h">可信性</div>
            <div className="tiny" style={{ color: 'var(--fg-3)', lineHeight: 1.7 }}>
              每个工具返回都带 <code style={{ color: 'var(--accent)', fontFamily: 'var(--mono)', fontSize: 10.5 }}>evidence</code> 字段，
              记录扫了多少行、用什么公式。<br />
              点推理链任一步的 <span style={{ color: 'var(--accent)' }}>+</span> 即可展开，回原始 CSV 核对。
            </div>
          </div>
        </div>
      </aside>
    </div>
  )
}

/* ---------------------------------------------------------------- 空状态 */
function EmptyState({ meta, onPick }: { meta: Meta | null; onPick: (q: string) => void }) {
  const caps = [
    { n: '01', t: '先判断有没有问题', d: '用二项检验判断这周是否真的异常，而不是看到数字波动就编故事。' },
    { n: '02', t: '再拆涨幅从哪来', d: '把涨幅分解成"订单结构"与"促销机制"，并用同 SKU 配对排除选品干扰。' },
    { n: '03', t: '最后给动作和钱', d: '归因落到具体原因码，输出可验收的动作清单与 GMV 影响换算。' },
  ]
  return (
    <div className="empty">
      <div className="empty-eyebrow">Attribution Agent</div>
      <h1 className="empty-title">
        问一句「这周差评<em>为什么</em>涨了」，<br />
        它会自己去查，而不是让你去查。
      </h1>
      <p className="empty-desc">
        Agent 会自主选择工具、执行真实的 pandas 查询、根据中间结果调整下一步。
        每一步的工具参数、耗时与<b>数据来源</b>都摊开可见——你可以拿任何一个数字回原始表核对。
      </p>

      <div className="cap-list">
        {caps.map((c) => (
          <div className="cap" key={c.n}>
            <div className="cap-ico">{c.n}</div>
            <div className="cap-body">
              <b>{c.t}</b>
              {c.d}
            </div>
          </div>
        ))}
      </div>

      <div className="flex gap6" style={{ flexWrap: 'wrap' }}>
        {(meta?.suggestions ?? []).slice(0, 4).map((s, i) => (
          <button
            key={i}
            className="suggest"
            style={{ width: 'auto', background: 'var(--panel)', border: '1px solid var(--line)', padding: '6px 11px' }}
            onClick={() => onPick(s.question)}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 一轮对话 */
function TurnView({ turn, onOpen }: { turn: Turn; onOpen?: (p: string, arg?: string | number) => void }) {
  return (
    <div style={{ marginBottom: 26 }}>
      <div className="q-row">
        <div className="q-bubble">{turn.question}</div>
      </div>

      <ErrorBoundary label="推理链">
        <Chain steps={turn.steps} thoughts={turn.thoughts} onOpen={onOpen} />
      </ErrorBoundary>

      {turn.answer && (
        <div style={{ animation: 'stepIn .35s var(--ease) both' }}>
          <ErrorBoundary label="结论">
            <Verdict answer={turn.answer} />
          </ErrorBoundary>
          <ErrorBoundary label="数据块">
            <Blocks blocks={turn.answer.blocks} />
          </ErrorBoundary>
          {turn.answer.caveats.length > 0 && (
            <div className="caveat">
              <div className="caveat-h">
                <span style={{ color: 'var(--warn)' }}>!</span> 已知局限
              </div>
              <ul>
                {turn.answer.caveats.map((c, i) => <li key={i}>{c}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}

      {turn.error && (
        <div className="card" style={{ borderColor: 'var(--bad-line)' }}>
          <div className="card-body">
            <div style={{ color: 'var(--bad)', fontSize: 12.5 }}>出错了：{turn.error}</div>
          </div>
        </div>
      )}
    </div>
  )
}

function Arrow() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
      <path d="M8 12.5V3.5M8 3.5L3.5 8M8 3.5L12.5 8" stroke="currentColor" strokeWidth="1.7"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}