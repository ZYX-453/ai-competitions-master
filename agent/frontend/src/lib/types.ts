/** 与后端 orchestrator.py 的事件协议一一对应。
 *
 * 为什么手写而不是从 OpenAPI 生成：事件协议是 SSE 的运行时拼装，
 * 不在 FastAPI 的 schema 里。手写的代价是要和后端同步维护，
 * 好处是前端拿到的类型是"人读得懂的业务语义"，而不是一堆 any。
 */

export type BadgeLevel = 'critical' | 'warn' | 'ok' | 'info'

export interface Badge {
  label: string
  level: BadgeLevel
}

export interface SessionEvent {
  intent: string
  mode: 'llm' | 'engine'
  model: string
  week_hint: number | null
  category_hint: string | null
  question: string
}

export interface PlanEvent {
  steps: string[]
  note: string
}

export interface ThoughtEvent {
  text: string
}

export interface ToolCallEvent {
  id: string
  tool: string
  title: string
  args: Record<string, unknown>
}

export interface ToolResultEvent {
  id: string
  tool: string
  ok: boolean
  summary: string
  ms: number
  evidence: Record<string, unknown>
  note: string
  error: string
  data: unknown
}

export interface KpiItem {
  label: string
  value: string
  delta: string
  tone: 'bad' | 'good' | 'neutral'
}

export interface ChainStep {
  label: string
  text: string
  tone: 'bad' | 'warn' | 'info'
}

export interface DecompBlock {
  type: 'decomp'
  title: string
  actual: number
  parts: { label: string; value: number; tone: string }[]
  paired: { n_sku: number; mean_diff_pp: number; t_stat: number; pct_sku_increased: number }
  verdict: { code: string; headline: string; evidence: string[]; implication: string } | null
  alternatives: {
    price_change_median_pct: number
    pct_sku_discounted: number
    quality_promo: number
    quality_base: number
    cvr_promo: number
    cvr_base: number
    orders_promo_avg: number
    orders_base_avg: number
  }
}

export interface Block {
  type: string
  title?: string
  items?: unknown[]
  columns?: string[]
  rows?: string[][]
  note?: string
  [k: string]: unknown
}

export interface AnswerEvent {
  headline: string
  badge: Badge
  summary: string
  blocks: Block[]
  caveats: string[]
  week: number | null
}

export interface DoneEvent {
  elapsed_ms: number
  tool_calls: number
}

/** 一次会话的完整状态。前端只维护这一个对象，避免多处 useState 不同步。 */
export interface Turn {
  id: string
  question: string
  session?: SessionEvent
  plan?: PlanEvent
  thoughts: { text: string; seq: number }[]
  steps: StepState[]
  answer?: AnswerEvent
  done?: DoneEvent
  error?: string
  startedAt: number
  finishedAt?: number
}

export interface StepState {
  id: string
  tool: string
  title: string
  args: Record<string, unknown>
  status: 'running' | 'done' | 'fail'
  summary?: string
  ms?: number
  evidence?: Record<string, unknown>
  note?: string
  error?: string
  seq: number
}