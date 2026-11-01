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

/* -------------------------------------------------------------------------
 * v3.1 评论分析四件套的 block
 *
 * 【为什么这几个要写具体类型，而通用 Block 保留了索引签名】
 * 老的那几种块（kpi/bars/table/quotes）结构简单，前端取值时靠
 * 少量 as 断言就够了。新的三种块内部有嵌套（画像的分组、对比的两侧），
 * 靠 as 断言会在"后端改了字段名"时静默渲染成空白——不报错、不提示，
 * 只是少了一块内容。上次采集器块就是这么消失的。
 * 写具体类型能让 tsc 直接把这种错挡在编译期。
 * ---------------------------------------------------------------------- */

export interface WordCloudItem {
  word: string
  /** 在该范围内出现的评论数 */
  count: number
  /** log2 lift：本类出现率 ÷ 对照组出现率。无对照组时为 null */
  lift?: number | null
  /** 包含该词的第一条原句（已截断），用于悬停回溯 */
  quote?: string
}

export interface WordCloudBlock {
  type: 'wordcloud'
  title?: string
  /** 'lift' = 按区分度排序；'count' = 按出现次数排序 */
  rank_by?: 'lift' | 'count'
  /** 实际生效的极性（可能由后端按意图补齐，不等于用户原话） */
  polarity?: string
  rank_note?: string
  /** 对照组样本量。为 0 表示这次没做区分度校正 */
  compare_n?: number
  scope?: unknown
  items?: WordCloudItem[]
}

export interface PersonaItem {
  signal: string
  n: number
  pct: number
  /** 命中该信号的原话（已按命中词取窗口截断） */
  quote?: string
  /** 原话里命中的那个信号词，用于高亮 */
  matched?: string
}

export interface PersonaGroup {
  group: string
  desc?: string
  items?: PersonaItem[]
}

export interface PersonaBlock {
  type: 'persona'
  title?: string
  groups?: PersonaGroup[]
  /** 这份画像明确回答不了的问题（如年龄/性别/收入） */
  cannot?: string[]
}

export interface ContrastItem {
  word: string
  n: number
  /** 对数比率，用于决定条形长度 */
  lr: number
}

export interface ContrastBlock {
  type: 'contrast'
  title?: string
  /** 对比范围，如"数码类目内"。跨类目对比时这里会写明不可当经营差异读 */
  scope?: string
  ours?: ContrastItem[]
  theirs?: ContrastItem[]
  benchmark?: string
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