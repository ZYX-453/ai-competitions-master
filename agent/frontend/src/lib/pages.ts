/* ===========================================================================
 * 功能页面的数据契约
 *
 * 【为什么这些页面不走 Agent，而要单独一组端点】
 * 问答要"按需深入"，所以由 Agent 决定查什么；
 * 页面要"一屏看全"，所以固定查一组，不做动态决策。
 * 两条路径共用同一批底层函数，数字口径完全一致。
 *
 * 【前端在这里的职责边界很硬：只排版，不算数】
 * 后端传来的已经是 17.04 这种"带好百分号"的数，
 * 不是 0.1704。所以下面每个接口里都找不到 `* 100`。
 *
 * 这条约束不是洁癖。这套系统已经踩过：左栏"年订单 64.7k /
 * 退货率 14.6%"是写死在前端 JSX 里的，数据重建到 61.8 万单 /
 * 13.53% 之后没人记得改前端，于是每一屏都在展示一个
 * 三个版本之前的数字。它的危害比正文写错更大——
 * 正文只在回答时出现，这些数字每一屏都在。
 * =========================================================================== */

export interface NavPage {
  key: string
  title: string
  subtitle: string
  icon: string
}

export interface NavData {
  pages: NavPage[]
  categories: string[]
  entities: string[]
  weeks: number[]
  latest_week: number
  /* 【后端挑的"最值得解释的那一周"，不是 latest_week】
     它用和异常判定完全相同的 _diagnose_all 选出 Δpp 最大的异常周。
     前端第一版自己用 weeks.filter(w => w > 0) 去猜，结果拿到的是第 52 周
     —— 而最新一周常常是正常周，归因页打开时两块判定都不打勾，
     看起来像系统没生效。选周的逻辑属于判定口径，必须留在后端。 */
  default_week: number
}

/* ---------------------------------------------------------------- 总览 */
export interface Kpi {
  orders_year: number
  gmv_year: number
  gross_profit_year: number
  aov: number
  return_rate: number
  loss_total: number
  loss_share_of_gp: number
  skus: number
  weeks: number
  reviews: number
  service_logs: number
}

export interface WeekPoint {
  week: number
  rr: number
  orders: number
  returns: number
  gmv: number
  z: number
  delta_pp: number
  is_anomaly: boolean
  trivial_sig: boolean
}

export interface CatRow {
  category: string
  orders: number
  returns: number
  return_rate: number
  gmv: number
  gross_profit: number
  rr_last12: number
  rr_prev: number
  delta_pp: number
  sku_count: number
}

export interface CauseRow {
  cause: string
  code: string
  n: number
  share: number
  loss: number
  loss_per: number
}

export interface DashboardData {
  kpi: Kpi
  baseline: { mean_rr: number; std_rr: number; band_low: number; band_high: number }
  series: WeekPoint[]
  tail: WeekPoint[]
  by_category: CatRow[]
  causes: CauseRow[]
  anomalies: WeekPoint[]
  evidence: { rows_scanned: number; returns_scanned: number; scope: string }
}

/* ---------------------------------------------------------------- 归因 */
export interface ContribRow {
  category: string
  rr_now: number
  rr_base: number
  gap_pp: number
  orders: number
  orders_share: number
  contribution_pp: number
  z: number
}

export interface StructureItem {
  l1: string
  l2: string
  count: number
  share: number
  share_base: number
  share_shift_pp: number
  loss: number
  resellable: number
}

export interface PromoBlock {
  scope: string
  promo_weeks: number[]
  rr_promo: number
  rr_base: number
  actual_gap_pp: number
  structure_pp: number
  mechanism_pp: number
  structure_share: number
  n_sku: number
  mean_diff_pp: number
  sd_pp: number
  t_stat: number
  pct_sku_increased: number
  alt: {
    price_change_median_pct: number
    pct_sku_discounted: number
    quality_promo: number
    quality_base: number
    quality_gap: number
    cvr_promo: number
    cvr_base: number
    orders_promo_avg: number
    orders_base_avg: number
  }
  verdict: { code: string; headline: string; evidence: string[]; implication: string }
  evidence: Record<string, unknown>
}

export interface AttributionData {
  week: number
  ok: boolean
  is_anomaly: boolean
  return_rate: number
  baseline: number
  delta_pp: number
  zscore: number
  orders: number
  returns: number
  stat_significant: boolean
  biz_significant: boolean
  trivial_sig: boolean
  min_effect_pp: number
  enough_sample: boolean
  min_orders: number
  detectable_pp: number
  wobble_pp: number
  suspicious_small: boolean
  scope: string
  category_contribution: ContribRow[]
  structure?: {
    scope: string
    total_returns: number
    total_loss: number
    baseline_scope: string
    baseline_returns: number
    items: StructureItem[]
    evidence: Record<string, unknown>
  }
  promo?: PromoBlock
  week_causes?: { cause: string; code: string; n: number; share: number; loss: number }[]
  week_returns_total?: number
  week_by_category: { category: string; returns: number; orders: number; rr: number }[]
  available_weeks: { week: number; is_anomaly: boolean; delta_pp: number }[]
}

/* ---------------------------------------------------------------- 原因治理 */
export interface ReasonRow {
  l1: string
  l2: string
  code: string
  n: number
  share: number
  loss: number
  loss_share: number
  loss_per: number
  resellable: number
  recoverable: number
}

export interface MatrixData {
  causes: string[]
  cause_codes: string[]
  categories: string[]
  rows: Record<string, number | string>[]
  col_max: Record<string, number>
}

export interface ReasonsData {
  scope: string
  categories: string[]
  total_returns: number
  total_loss: number
  rows: ReasonRow[]
  matrix: MatrixData
  evidence: Record<string, unknown>
}

/* ---------------------------------------------------------------- 评论 */
export interface WcItem {
  word: string
  count: number
  share?: number
  lift?: number | null
  // 【字段名是 quotes（复数，数组），不是 quote】
  // 后端对每个词返回最多 3 句原话，方便读者判断这个词是不是被误切出来的。
  // 第一版按 quote 单值写，于是界面上一个原话都不显示 —— 不报错、
  // 不漏数字，只是"可回溯"这个承诺悄悄失效了。
  samples?: string[]
  quotes?: string[]
}

export interface SentimentDim {
  dim: string
  label: string
  n: number
  neg_pct: number | null
  mean_score: number | null
}

export interface SentimentData {
  ok: boolean
  entity: string
  n: number
  pos: number
  neg: number
  neu: number
  pos_pct: number
  neg_pct: number
  neu_pct: number
  mean_score: number
  net_sentiment: number
  src_mix: Record<string, number>
  dimensions?: SentimentDim[]
}

export interface PersonaItem2 {
  signal: string
  n: number
  pct: number
  quote?: string
  matched?: string
}

export interface PersonaGroup2 {
  group: string
  desc?: string
  items?: PersonaItem2[]
}

export interface PersonaData {
  ok?: boolean
  groups?: PersonaGroup2[]
  cannot?: string[]
  top_signals?: { signal: string; n: number; pct: number }[]
  rating_dist?: Record<string, number>
  n?: number
}

export interface ReviewWeek {
  week: number
  n: number
  neg_pct: number
  pos_pct: number
  net: number
}

export interface ReviewData {
  entity: string
  n: number
  sentiment: SentimentData
  wordcloud_neg: WcItem[]
  wordcloud_all: WcItem[]
  persona: PersonaData
  rating_dist: { star: number; n: number }[]
  weekly: ReviewWeek[]
  entities: { entity_name: string; n: number; mode?: string; compliance?: string }[]
  evidence: Record<string, unknown>
}

/* ---------------------------------------------------------------- 竞品 */
export interface CompEntity {
  entity: string
  n: number
  neg_pct: number
  mean_score: number
  /* 【diff_pp 的方向：对方 − 本店】
     正数 = 对方负评率更高 = 我们更好。
     这一条曾经在页面上写反过，于是四个竞品的 Δ 全为正（我们更好）
     而 KPI 写着"显著差于我们 4 家"。方向判断交给后端给的 better 字段，
     前端不要自己用 diff_pp 的正负去推。 */
  diff_pp: number
  better: boolean
  significant: boolean
  p_value: number
  z?: number
  mode_zh: string
}

export interface CompDim {
  label: string
  worst: string
  best: string
  max: number
  min: number
  spread: number
  by_entity: { entity: string; value: number; is_us: boolean }[]
}

export interface ContrastWord {
  word: string
  n: number
  /* log2(本侧比例 / 对侧比例)，后端算好，前端不做换算 */
  lr: number
}

export interface ContrastSide {
  ok: boolean
  /* 失败原因。ok=false 时页面显示它，而不是留一片空白 */
  reason?: string
  benchmark?: string
  scope?: string
  ours?: ContrastWord[]
  theirs?: ContrastWord[]
}

export interface CompetitorData {
  scope: string
  /* 本店负评率，作为 Δ 的基准值单独给出 */
  us_neg_pct: number
  entities: CompEntity[]
  dimensions: CompDim[]
  contrast: ContrastSide
  /* 后端措辞："N 个竞品与本店的差异达到统计显著（p<0.05）" */
  sig_summary: string
  nature: Record<string, unknown>
  note: string
  evidence: Record<string, unknown>
}

/* ---------------------------------------------------------------- 自检 */
export interface EvalSuite {
  name: string
  title: string
  assertions: number
  status: 'pass' | 'fail' | 'needs_key'
  warn: number
  sections: { n: number; title: string; what: string }[]
}

export interface EvalData {
  suites: EvalSuite[]
  data_scale: Record<string, number>
  independence: { claim: string; how: string; proof: string[] }
}