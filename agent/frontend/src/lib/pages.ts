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
  /** 该类目全年退货损失（与 KPI 的 loss_total 同一列 sum，四类目相加≈全店） */
  loss_total: number
  /** 单条退货平摊损失 */
  loss_per: number
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

/* ---- v3.3 联动看板：两张矩阵 ----
 * 【为什么矩阵要整份发下来，而不是切换时再请求】
 * 切换类目要的是"别的类目长什么样"做对照，一次拿全比来回请求快；
 * 而且 by_category 本身就是切换器，它必须在任何范围下都完整。
 * 注意每个格子的 share 也是后端算的 —— 分母随类目变，
 * 前端一除就是第二份口径实现。 */
export interface CauseCell {
  code: string
  category: string
  n: number
  share: number
  loss: number
}

export interface HeatCell {
  week: number
  rr: number | null
  dev_pp: number | null
  /** 二项 z。**上色键用它，不用 dev_pp**：dev 跨类目不可比（服饰 28% 天然抖 ±3pp）。 */
  z: number | null
  is_anomaly: boolean
  trivial_sig: boolean
}

export interface HeatRow {
  category: string
  base_rr: number | null
  cells: HeatCell[]
}

export interface DashboardData {
  kpi: Kpi
  baseline: { mean_rr: number; std_rr: number; band_low: number; band_high: number }
  series: WeekPoint[]
  tail: WeekPoint[]
  by_category: CatRow[]
  causes: CauseRow[]
  anomalies: WeekPoint[]
  cause_matrix: {
    causes: { code: string; cause: string }[]
    categories: string[]
    cells: CauseCell[]
  }
  heatmap: {
    rows: HeatRow[]
    weeks: number[]
    /* 满色阈值（z）。**后端给，前端不许拍**：
       颜色的深浅直接决定读者觉得哪块最疼，那是判断不是装饰。 */
    scale_z: number
    /** 统计显著线（z=2），用于图例标注 */
    sig_z: number
  }
  /** 当前口径范围：'全店' 或某个类目名 */
  scope: string
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
  /** 当前选中的这一周本身是不是促销周。
      promo_mechanism 算的是"促销周 vs 非促销周"的整体对照，不接 week，
      所以在非促销周（比如第 52 周，退货率还在降）也会返回同一份数字。
      前端必须靠这个字段决定展不展示，否则会把促销的结论安到无关的一周头上。 */
  is_promo_week: boolean
  rr_promo: number
  rr_base: number
  actual_gap_pp: number
  structure_pp: number
  mechanism_pp: number
  /** 分解的闭合差额：actual_gap_pp − (structure_pp + mean_diff_pp)。
      不是算错 —— 结构项是加权分解，机制项是 151 个 SKU 配对差的等权平均，
      权重口径不同，相加必然对不上按订单加权的实际涨幅。
      后端算好下发，前端不自己减，否则差额会变成没人认领的数字。 */
  residual_pp: number
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
  /* 【贡献分解的闭合项】
     Σ contribution_pp 并不严格等于 delta_pp，差的那部分是**结构效应**：
     订单在类目之间迁移带来的影响（本周订单流向了退货率更低的类目）。
     瀑布图必须把它画成独立一节，否则几根柱子加起来对不上总柱。
     由后端 pages.py 算好，前端不自己减 —— 否则"这个差是什么"
     就变成了一个没人命名的减法。 */
  contribution_sum_pp?: number
  structure_effect_pp?: number
  /** 结构效应占整体变化的比例（0–1），用来说明"这次变化里有多少来自换货" */
  structure_effect_ratio?: number
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
  available_weeks: { week: number; is_anomaly: boolean; delta_pp: number }[]
}

/* ---------------------------------------------------------------- 原因治理 */
export interface ReasonRow {
  l1: string
  l2: string
  code: string
  /** 这一行是不是"没归上原因"（code = X）。
      【为什么后端要显式给这一位，而不是前端判 code === 'X'】
      它是一个**身份**判断，不是排版判断：这行不能派活、不能跟其余行
      按同一把尺子比"值不值得管"。放在前端写死，就等于把业务含义
      藏进了一个字符串比较里，换一份数据换个代码就悄悄失准。 */
  is_void: boolean
  n: number
  share: number
  loss: number
  loss_share: number
  loss_per: number
  resellable: number
  recoverable: number
}

/* 矩阵的逐格明细。
   【为什么要单独下发一份，而不是让前端拿 share 反推】
   浮层要回答"这 15% 是多少条、多少钱"，两个绝对量后端才有。
   前端若能自己乘除，就会出现"矩阵里的占比"和"表里的占比"各算一遍，
   迟早对不上（本项目已经因为这类重算出过单位错）。 */
export interface MatrixCell {
  category: string
  code: string
  name: string
  n: number
  loss: number
  share: number
  loss_share: number
}

export interface MatrixData {
  causes: string[]
  cause_codes: string[]
  categories: string[]
  rows: Record<string, number | string>[]
  /** 每列在"占条数"口径下的最大值，用于着色归一 */
  col_max: Record<string, number>
  /** 每列在"占损失"口径下的最大值。两个口径各有一套尺子，
      换口径时不重新归一的话，颜色深浅会跟数字对不上。 */
  col_max_loss: Record<string, number>
  cells: MatrixCell[]
  /** 每个类目的退货条数与损失合计，矩阵行标签旁边要显示"这一行是按多少条算的" */
  cat_totals: Record<string, { n: number; loss: number }>
}

export interface ReasonsData {
  scope: string
  categories: string[]
  total_returns: number
  total_loss: number
  // 【可挽回的两个量由后端下发】
  // 前端曾经自己写 sum(rows.recoverable) / total_loss * 100，
  // 这一页其余占比全由后端给，只有这一个在前端算 —— 四舍五入位置不同就会末位对不上。
  recoverable_total: number
  recoverable_pct: number
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

/** 一条画像结论附带的原始评论证据。
 *  matched 是这条评论里命中该信号的那个词，用于在原话里高亮。 */
export interface PersonaQuote {
  id?: string
  text: string
  rating?: number
  matched?: string
}

export interface PersonaItem2 {
  signal: string
  n: number
  pct: number
  /* 【字段名必须是 quotes，不能是 quote】
     后端给的是数组（每个信号可能配多条原话，元素含 id/text/rating/matched）。
     早先这里写成 quote?: string 单数，读 `it.quote` 永远是 undefined，
     于是画像卡上的"原话证据"一整片从来没渲染过 —— 不报错、不留空框，
     看起来就像"这一页本来就不带证据"。这是本项目反复出现的同一类静默失效。 */
  quotes?: PersonaQuote[]
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
/* ------------------------------------------------------- 预警 · 策划 */
/** 一周的预警条目。rr_* 与 delta_pp 都是**后端算好的成品**（百分数 / 百分点），
 *  前端只负责显示 —— 一旦在这里做除法，"这个 17.04%" 就有了第二份口径。 */
export interface PlanAlert {
  week: number
  rr_now: number
  rr_base: number
  delta_pp: number
  z: number
  orders: number
  is_anomaly: boolean
  trivial_sig: boolean
}

export interface PlanTrendPoint {
  week: number
  rr_now: number
  rr_base: number
  delta_pp: number
  /** 后端按同一套双门槛派生；只有 alert 点允许进入策划明细 */
  status: 'alert' | 'improving' | 'normal'
}

export interface PlanHeadline {
  week: number
  rr_now: number
  rr_base: number
  delta_pp: number
  z: number
  returns: number
  orders: number
  is_anomaly: boolean
  trivial_sig: boolean
  enough_sample: boolean
  /** 双门槛的两个数：判定的依据要能自己核对，不能只给一个勾 */
  min_effect_pp: number
  detectable_pp: number
  scope: string
}

/** 类目对本周涨幅的贡献（contribution_pp 单位是百分点，可直接相加≈总 Δpp） */
export interface PlanContribution {
  category: string
  contribution_pp: number
  rr_now: number
  rr_base: number
  gap_pp: number
  orders_now: number
}

export interface PlanAction {
  action: string
  impact_on: string
  owner: string
  kpi: string
}

export interface PlanReason {
  reason: string
  count: number
  /** 占本周退货单量的比例（百分数 0–100，后端已乘 100）—— 不要再 ×100 */
  share: number
  loss: number
  /** false = 这一类没有既定动作清单（缺的是方案，不是数据） */
  has_plan: boolean
  cause_name: string
  actions: PlanAction[]
  plan_note: string
  /** 占本周损失的比例（百分数，后端算好）—— 前端不做除法 */
  loss_share: number
}

export interface PlanData {
  scope: string
  week: number
  alerts: PlanAlert[]
  /** 52 周实际值/基线/状态；走势与预警表共用同一批诊断结果 */
  trend: PlanTrendPoint[]
  alert_total: number
  /** 显著改善的周数：双门槛是双向的，这些周不该进预警，但也不能假装没有 */
  improving_total: number
  headline: PlanHeadline
  contributions: PlanContribution[]
  /** 各列贡献之和（pp）—— 与 headline.delta_pp 的差额由结构变化解释 */
  contrib_sum_pp: number
  /** delta_pp 减去各列之和剩下的未分配部分（pp），后端算好，前端只显示 */
  contrib_rest_pp: number
  /** 当周实际有几个类目参与拆分（页面上只列前 4 个） */
  contrib_n: number
  reasons: PlanReason[]
  loss_total: number
  loss_actionable: number
  /** 有动作的损失占本周总损失的比例（百分数） */
  actionable_pct: number
  evidence: Record<string, unknown>
}
