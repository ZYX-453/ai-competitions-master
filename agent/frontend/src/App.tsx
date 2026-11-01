import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { streamChat, getJson } from './lib/sse'
import type {
  AnswerEvent, DoneEvent, PlanEvent, SessionEvent, StepState, ThoughtEvent, Turn,
} from './lib/types'
import { Chain } from './components/Chain'
import { Blocks } from './components/Blocks'
import { Verdict } from './components/Verdict'
import { ErrorBoundary } from './components/ErrorBoundary'

interface Health {
  ok: boolean
  mode: string
  model: string
  data: {
    skus: number; weeks: number; latest_week: number; categories: string[]
    // 左栏"数据底盘"要用的两个汇总数。原来前端把它俩写死在 JSX 里，
    // 结果停在三个版本之前（64.7k 单 / 14.6%）。现在由后端算好下发，
    // 前端只负责显示 —— 单一数据源，改数据不用改前端。
    orders_year: number
    return_rate: number
    rows: Record<string, number>
  }
  tools: string[]
}

interface Meta {
  categories: string[]
  weeks: { min: number; max: number }
  promo_weeks: number[]
  tools: { name: string; title: string; description: string }[]
  suggestions: { label: string; question: string }[]
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [meta, setMeta] = useState<Meta | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    getJson<Health>('/api/health').then(setHealth).catch(() => setHealth(null))
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
    <div className="shell">
      <TopBar health={health} />

      <LeftRail
        health={health}
        meta={meta}
        busy={busy}
        onPick={ask}
      />

      <main className="stage">
        <div className="stage-scroll" ref={scrollRef}>
          {turns.length === 0 ? (
            <EmptyState meta={meta} onPick={ask} />
          ) : (
            turns.map((t) => <TurnView key={t.id} turn={t} />)
          )}
        </div>

        <div className="composer-wrap">
          <div className="composer">
            <textarea
              ref={taRef}
              rows={1}
              value={input}
              placeholder="问一个业务问题，例如：第 44 周退货率为什么涨了"
              onChange={(e) => {
                setInput(e.target.value)
                const el = e.currentTarget
                el.style.height = 'auto'
                el.style.height = `${Math.min(el.scrollHeight, 120)}px`
              }}
              onKeyDown={onKeyDown}
              disabled={busy}
            />
            <button className="send-btn" onClick={() => ask(input)} disabled={busy || !input.trim()} title="发送">
              {busy ? <div className="spinner" /> : <Arrow />}
            </button>
          </div>
          <div className="hint-row">
            <span>
              <kbd>Enter</kbd> 发送 · <kbd>Shift</kbd>+<kbd>Enter</kbd> 换行
              {busy && ' · 推理中，可点击推理链展开证据'}
            </span>
            <span>
              {health ? `${health.data.rows.panel_weekly.toLocaleString()} 行面板 · ${health.tools.length} 个工具` : '连接中'}
            </span>
          </div>
        </div>
      </main>

      <RightRail lastTurn={lastTurn} liveTools={liveTools} health={health} meta={meta} />
    </div>
  )
}

/* ---------------------------------------------------------------- 顶栏 */
function TopBar({ health }: { health: Health | null }) {
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark">炼</div>
        <span className="brand-name">差评炼金炉</span>
        <span className="brand-sep">/</span>
        <span className="brand-sub">归因诊断 Agent</span>
      </div>
      <div className="top-spacer" />
      {health && (
        <>
          <span className="chip">
            {health.data.skus} SKU · {health.data.weeks} 周 ·{' '}
            {Object.values(health.data.rows).reduce((a, b) => a + b, 0).toLocaleString()} 行
          </span>
          <span className={`chip ${health.mode === 'llm' ? 'live' : ''}`}>
            <span className="dot" />
            {health.mode === 'llm' ? health.model : '确定性推理引擎'}
          </span>
        </>
      )}
    </header>
  )
}

/* ---------------------------------------------------------------- 左栏 */
function LeftRail({
  health, meta, busy, onPick,
}: { health: Health | null; meta: Meta | null; busy: boolean; onPick: (q: string) => void }) {
  return (
    <aside className="rail-left">
      <div className="rail-head">
        <div className="rail-title">数据底盘</div>
        <div className="stat-grid">
          {/* 【这四格原来写死了两个数：64659/1000 → "64.7k"、退货率 "14.6%"。
              它们既不是从 /api/health 读的，也和当前数据对不上：
              实际是 61.8 万单、13.53% 退货率。
              这种错误最难被发现——页面上常驻一行数字，看着很权威，
              但它是三个数据版本之前的遗留物。用户只要拿它和正文里的
              61.8 万单一对，立刻就能得出"这系统自己在说什么"的结论。
              现在全部走 health，取不到就显示 —，绝不显示一个瞎猜的数。 */}
          <div className="stat-cell">
            <div className="stat-k">年订单</div>
            <div className="stat-v">{
              health ? (
                health.data.orders_year >= 10000
                  // 【不能用 Math.round】618058/10000 = 61.8，round 之后变 62，
                  // 一位小数的精度就这么没了。左边是"底盘数字"，
                  // 别人拿它和正文里 618,058 一对就会起疑。
                  ? <>{(health.data.orders_year / 10000).toFixed(1)}<small>万</small></>
                  : <>{health.data.orders_year.toLocaleString()}<small>单</small></>
              ) : '—'
            }</div>
          </div>
          <div className="stat-cell">
            <div className="stat-k">周次</div>
            <div className="stat-v">{health?.data.weeks ?? '—'}</div>
          </div>
          <div className="stat-cell">
            <div className="stat-k">退货率</div>
            <div className="stat-v">{
              // 【这里漏了乘 100】后端 return_rate 是小数（0.1353），
              // 直接 toFixed(2) 会显示成 "0.14%"。截图里真的出现过，
              // 而且比原来的写死值更糟——写死值是旧的，这个是错的。
              health ? <>{(health.data.return_rate * 100).toFixed(2)}<small>%</small></> : '—'
            }</div>
          </div>
          <div className="stat-cell">
            <div className="stat-k">类目</div>
            <div className="stat-v">{health?.data.categories.length ?? '—'}</div>
          </div>
        </div>
      </div>

      <div className="rail-scroll">
        <div className="rail-title" style={{ marginBottom: 6 }}>试着问</div>
        {(meta?.suggestions ?? []).map((s, i) => (
          <button key={i} className="suggest" onClick={() => onPick(s.question)} disabled={busy}>
            <span className="arrow">›</span>
            <span>{s.label}</span>
          </button>
        ))}

        {meta && (
          <>
            <div className="rail-title" style={{ margin: '18px 0 6px' }}>数据边界</div>
            <div className="tiny muted" style={{ lineHeight: 1.75, paddingLeft: 9 }}>
              周次 {meta.weeks.min}–{meta.weeks.max}
              <br />
              促销周 {meta.promo_weeks.join(' / ')}
              <br />
              类目 {meta.categories.join(' · ')}
            </div>
            <div className="tiny" style={{ color: 'var(--fg-4)', marginTop: 10, paddingLeft: 9, lineHeight: 1.65 }}>
              Agent 只在这份数据内作答。<br />超出范围会明确说"查不到"。
            </div>
          </>
        )}
      </div>
    </aside>
  )
}

/* ---------------------------------------------------------------- 右栏 */
function RightRail({
  lastTurn, liveTools, health, meta,
}: {
  lastTurn?: Turn
  liveTools: Record<string, number>
  health: Health | null
  meta: Meta | null
}) {
  const steps = lastTurn?.steps ?? []
  const active = new Set(steps.filter((s) => s.status === 'running').map((s) => s.tool))

  return (
    <aside className="rail-right">
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
          {steps.length === 0 ? (
            <div className="tiny" style={{ color: 'var(--fg-4)', lineHeight: 1.6 }}>
              提问后这里会实时显示每一步的工具调用。
            </div>
          ) : (
            <div style={{ display: 'grid', gap: 2 }}>
              {steps.map((s) => (
                <div key={s.id} className={`tool-pill ${s.status === 'running' ? 'on' : ''}`}>
                  <span style={{ width: 5, height: 5, borderRadius: '50%',
                    background: s.status === 'running' ? 'var(--accent)'
                      : s.status === 'fail' ? 'var(--bad)' : 'var(--fg-4)' }} />
                  <span className="tp-name">{s.tool}</span>
                  <span className="tp-cnt">{s.ms != null ? `${s.ms.toFixed(0)}ms` : '…'}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="rr-sec">
          <div className="rr-sec-h">工具覆盖</div>
          <div style={{ display: 'grid', gap: 1 }}>
            {(meta?.tools ?? []).map((t) => {
              const n = liveTools[t.name] ?? 0
              return (
                <div key={t.name} className={`tool-pill ${active.has(t.name) ? 'on' : ''}`}>
                  <span className="tp-name" style={{ color: n > 0 ? 'var(--fg-2)' : undefined }}>
                    {t.name}
                  </span>
                  <span className="tp-cnt">{n > 0 ? `×${n}` : '—'}</span>
                </div>
              )
            })}
          </div>
        </div>

        <div className="rr-sec">
          <div className="rr-sec-h">数据源</div>
          {health && Object.entries(health.data.rows).map(([k, v]) => (
            <div className="metric-line" key={k}>
              <span className="ml-k">{k}.csv</span>
              <span className="ml-v">{v.toLocaleString()}</span>
            </div>
          ))}
        </div>

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
function TurnView({ turn }: { turn: Turn }) {
  return (
    <div style={{ marginBottom: 26 }}>
      <div className="q-row">
        <div className="q-bubble">{turn.question}</div>
      </div>

      <ErrorBoundary label="推理链">
        <Chain steps={turn.steps} thoughts={turn.thoughts} />
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