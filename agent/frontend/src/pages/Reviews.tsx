/* ===========================================================================
 * 评论洞察页
 *
 * 【这一页的每一块，都要能让人反驳】
 * 情感占比、高频词、用户画像，本质上都是"我对一堆文本做了一次判断"。
 * 如果界面上只出现结论，读者无从验证，只能选择信或不信。
 * 所以每个数字旁边都必须能摸到它的原材料：
 *   · 词云 → 每个词挂一句包含它的原话
 *   · 画像 → 每条结论挂一句命中信号的原话
 *   · 走势 → 每个点标注样本量
 *
 * 【画像块如何避免被读成人群标签】
 * 它只展示评论里"说出来的场景"，不推人口属性。
 * 这层界线写在标题与副标题上：
 * 「评论侧写 · 典型画像 / 由评论文本归纳，非人口属性」。
 * （原先还在块内渲染过一段"回答不了什么"的清单，已按产品要求删除。）
 * =========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useFetch } from '../lib/useFetch'
import type { ReviewData } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, pp } from '../components/PageKit'
import { HBars, Segmented, useTip } from '../components/BiKit'
import { PersonaBoard } from '../components/PersonaBoard'

export function Reviews({
  entities, entity, setEntity,
}: {
  entities: string[]
  entity: string
  setEntity: (e: string) => void
}) {
  const [wcMode, setWcMode] = useState<'neg' | 'all'>('neg')
  // 【默认只显示前 12 个】
  // 40 个词每个挂一句原话时，这一块单独占了页面 3000px（整页 3659px）——
  // 读者滚到中途就失去参照，反而记不住前三个是什么。
  // 12 个足够看清"抱怨集中在哪"，剩下的按需展开。
  const [wcTop, setWcTop] = useState<'12' | '24' | 'all'>('12')

  const { data: d, err, retry } = useFetch<ReviewData>(
    `/api/pages/reviews?entity=${encodeURIComponent(entity)}`,
  )

  if (err) return <Fail msg={err} onRetry={retry} />
  if (!d) return <Loading label={`读 ${entity} 的评论`} />

  const s = d.sentiment
  const wc = wcMode === 'neg' ? d.wordcloud_neg : d.wordcloud_all
  const wcIsLift = wcMode === 'neg'
  // 【排序也要跟着口径走】
  // 差评栏按 lift 排（这是它被切出来的理由），全部栏按次数排。
  // 后端已经把数组排好了，这里只做截断 —— 不重排，避免两处口径分叉。
  const wcShown = wcTop === 'all' ? wc : wc.slice(0, Number(wcTop))
  /* 【归一化分母只算一次，条长和亮度分级共用】
     这是上一轮踩过的坑：max 和 items 各算一遍，改了指标忘改另一处，
     就会出现"条最长的不在第一位"，而图上没有任何报错。
     提成变量之后，两处不可能分叉。 */
  const wcTopMax = Math.max(...wcShown.map((w) =>
    wcIsLift ? Math.pow(2, w.lift ?? 0) : w.count), 1)
  const ratingTotal = d.rating_dist.reduce((a, b) => a + b.n, 0)
  // 走势里最大的偏差，用来定"最近是不是在恶化"的浓度
  const lastWeeks = d.weekly.slice(-4)
  const negTrend = lastWeeks.length >= 2
    ? lastWeeks[lastWeeks.length - 1].neg_pct - lastWeeks[0].neg_pct
    : 0

  return (
    <div className="pg">
      <div className="pg-filterbar">
        <span className="pg-filterbar-k">对象</span>
        {entities.map((e) => (
          <button
            key={e}
            className={`pg-fchip ${entity === e ? 'on' : ''}`}
            onClick={() => setEntity(e)}
          >
            {e}
          </button>
        ))}
      </div>

      <div className="pg-kpi-row">
        <Stat label="评论总量" value={nf(s.n)} unit="条" sub={d.entity} />
        <Stat label="负评占比" value={(s.neg_pct * 100).toFixed(2)} unit="%"
          tone="bad" sub={`${nf(s.neg)} 条`} />
        <Stat label="正评占比" value={(s.pos_pct * 100).toFixed(2)} unit="%"
          tone="good" sub={`${nf(s.pos)} 条`} />
        <Stat label="净情感值" value={s.net_sentiment.toFixed(3)}
          sub="正评占比 − 负评占比" />
        <Stat
          label="近 4 周负评变化"
          value={pp(negTrend)}
          unit="pp"
          tone={negTrend > 0.5 ? 'bad' : negTrend < -0.5 ? 'good' : undefined}
          sub={negTrend > 0.5 ? '口碑在恶化' : negTrend < -0.5 ? '口碑在改善' : '基本平稳'}
          title="最近 4 周里，最新一周的负评率减去最早一周的负评率。只看方向，不做显著性判断。"
        />
      </div>

      <div className="pg-2col">
        {/* ---- 情感维度 ---- */}
        <Card
          title="按维度看负评率"
          note={<>分母是<b>提到该维度的评论数</b>，不是全部评论。否则"物流"会因为多数评论不提物流而显得占比很低。</>}
          pad={false}
        >
          {!s.dimensions || s.dimensions.length === 0 ? (
            <Empty what="没有维度数据" why="语料里没有任何评论命中这 8 个维度的关键词" />
          ) : (
            <table className="pg-table">
              <thead>
                <tr>
                  <th scope="col">维度</th>
                  <th scope="col" className="r">提及数</th>
                  <th scope="col" className="r">负评率</th>
                  <th scope="col" className="r">均分</th>
                </tr>
              </thead>
              <tbody>
                {s.dimensions.filter((x) => x.n > 0).map((x) => (
                  <tr key={x.dim}>
                    <td>{x.label}</td>
                    <td className="r num dim">{nf(x.n)}</td>
                    <td className="r num">
                      <div className="pg-cell-bar">
                        <span className={x.neg_pct != null && x.neg_pct > 0.3 ? 't-bad' : ''}>
                          {x.neg_pct != null ? (x.neg_pct * 100).toFixed(1) : '—'}%
                        </span>
                        <Bar pct={(x.neg_pct ?? 0) * 100} tone="bad" height={4} />
                      </div>
                    </td>
                    <td className="r num">{x.mean_score != null ? x.mean_score.toFixed(3) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        {/* ---- 星级分布 ---- */}
        <Card
          title="星级分布"
          note={<>情感判定<b>不读取星级</b>（仅在文本信号不足时兜底），二者非同源指标，可交叉检验。</>}
          pad={false}
        >
          {d.rating_dist.length === 0 ? (
            <Empty what="没有星级数据" />
          ) : (
            <div className="pg-stars">
              {[...d.rating_dist].sort((a, b) => b.star - a.star).map((r) => (
                <div className="pg-star-row" key={r.star}>
                  <span className="pg-star-k">{r.star} 星</span>
                  <div className="pg-star-bar">
                    <Bar pct={(r.n / Math.max(ratingTotal, 1)) * 100}
                      tone={r.star <= 2 ? 'bad' : r.star === 3 ? 'warn' : 'accent'}
                      height={9} />
                  </div>
                  <span className="pg-star-n num">{nf(r.n)}</span>
                  <span className="pg-star-p num dim">
                    {((r.n / Math.max(ratingTotal, 1)) * 100).toFixed(1)}%
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      {/* ---- 高频词 ---- */}
      <Card
        title="高频词"
        note={wcIsLift
          ? <>按 <b>log2 lift</b> 排序：这个词在差评里的出现率，比在好评里高多少倍。商品名会自动沉底，因为它在两边一样多。<b>鼠标停在词上</b>看原话。</>
          : <>按出现次数排，<b>没有做区分度校正</b>，所以会混着商品名和泛词。要看抱怨点，切到"差评"那一栏。</>}
        pad={false}
        right={(
          <div className="rv-wc-ctrl">
            <Segmented
              value={wcMode}
              onChange={setWcMode}
              options={[
                { key: 'neg', label: '差评（按区分度）' },
                { key: 'all', label: '全部（按次数）' },
              ]}
            />
            <Segmented
              value={wcTop}
              onChange={setWcTop}
              options={[
                { key: '12', label: '12' },
                { key: '24', label: '24' },
                { key: 'all', label: '全部' },
              ]}
            />
          </div>
        )}
      >
        {wc.length === 0 ? (
          <Empty what="没有词频数据" />
        ) : (
          <div className="bi-bar-body">
            <HBars
              items={wcShown.map((w) => {
                // 【条长按"当前排序键"归一，不是固定按 count】
                // 原来词云只有 count 一个量纲，条长就是 count/maxCount。
                // 现在多了 lift，两个量纲差着数量级（count 上千、lift 是个位数），
                // 混用会让"条最长"和"排最前"不是同一个词。
                const q = w.quotes?.[0] || w.samples?.[0] || ''
                const liftN = w.lift != null ? Math.pow(2, w.lift) : null
                // 当前口径下的取值与全集最大值 —— 亮度分级要用
                const pctVal = wcIsLift ? (liftN ?? 0) : w.count
                return {
                  key: w.word,
                  label: w.word,
                  pct: pctVal,
                  text: wcIsLift ? (liftN != null ? `${liftN.toFixed(1)}×` : '—')
                                 : nf(w.count),
                  text2: wcIsLift ? `${nf(w.count)} 次` : (liftN != null ? `${liftN.toFixed(1)}×` : '—'),
                  // 【v4：同样改成亮度分级，不用语义红】
                  // 12 根红条会占掉页面中央一大块，灰阶方案立即破功；
                  // 而且"每个抱怨词都是红的"没有区分度——
                  // 按数值分三档灰之后，最亮的那几个词自然跳出来。
                  tone: (pctVal / wcTopMax > 0.66 ? 'hi'
                    : pctVal / wcTopMax > 0.33 ? 'mid' : 'lo') as 'hi' | 'mid' | 'lo',
                  // 原话移进浮层：40 个词各挂一句就是 3000px 的文字墙，
                  // 读者扫不完，而真正想验证某个词的人一定愿意把鼠标移上去。
                  tip: (
                    <div>
                      <div className="bi-tip-h">{w.word}</div>
                      <div className="bi-tip-row">出现 <b>{nf(w.count)}</b> 次</div>
                      {liftN != null && (
                        <div className="bi-tip-row">
                          区分度 <b>{liftN.toFixed(1)}×</b>
                          <span className="dim">（差评里比好评里常见这么多倍）</span>
                        </div>
                      )}
                      {q && (
                        <div className="rv-tip-q">
                          <Mark text={q} hit={w.word} />
                        </div>
                      )}
                      {(w.quotes?.length ?? 0) > 1 && (
                        <div className="bi-tip-row dim">
                          这个词还有 {(w.quotes?.length ?? 0) - 1} 句原话，展开列表可见
                        </div>
                      )}
                    </div>
                  ),
                }
              })}
              max={wcTopMax}
              note={<>
                条长按当前排序口径归一（{wcIsLift ? '区分度倍数' : '出现次数'}），数值以右侧为准。
                {wcTop !== 'all' && ` 仅显示前 ${wcTop} 个 —— 右侧"全部"可展开。`}
              </>}
            />

            {/* 【原话列表保留，但默认收起】
                浮层解决"随手验证"，折叠区解决"逐条核对"。
                两个都要：评审现场只会看一眼，真审材料的人会想全部读一遍。 */}
            <details className="rv-wc-quotes">
              <summary>展开全部 {wcShown.length} 个词的原话</summary>
              <div className="rv-wc-qlist">
                {wcShown.map((w) => {
                  const qs = w.quotes?.length ? w.quotes : (w.samples || [])
                  if (!qs.length) return null
                  return (
                    <div className="rv-wc-qitem" key={w.word}>
                      <div className="rv-wc-qw">
                        {w.word}
                        <span className="num dim">{nf(w.count)} 次</span>
                      </div>
                      {qs.slice(0, 3).map((q, i) => (
                        <div className="rv-wc-qs" key={i}>
                          <Mark text={q} hit={w.word} />
                        </div>
                      ))}
                    </div>
                  )
                })}
              </div>
            </details>
          </div>
        )}
      </Card>

      {/* ---- 画像 ---- */}
      <Card
        title="用户画像"
        note={<>每条结论都带<b>占比</b>与<b>命中原话</b>。点一条信号展开它的证据，原话中高亮的就是被判成这条依据的那个词。</>}
      >
        {!d.persona.groups || d.persona.groups.length === 0 ? (
          <Empty what="没有画像数据" />
        ) : (
          /* 【用 PersonaBoard 取代原来的 .pg-persona 静态块】
             旧块只有条形 + n 数，而且读的是 it.quote（单数），
             而后端给的是 quotes[] —— 所以原话证据从来没显示过。
             新块补回证据、加上占比与雷达概览。
             注意类名换成了 .persona-board：glass.js 的 enhancePersona
             只认 .pg-persona，找不到就自动跳过，不会再来改写这一块。 */
          <PersonaBoard data={d.persona} />
        )}
      </Card>

      {/* ---- 分周走势 ---- */}
      <Card
        title="分周情感走势"
        note={<>
          "负评 8.1%"背后可能是"一直 8%"，也可能是"最近三周涨到 15%"——两者的应对完全不同。
          <b>悬停</b>读单周（带抽样误差），<b>拖选一段</b>看区间趋势。
        </>}
        pad={false}
      >
        {d.weekly.length === 0 ? (
          <Empty what="没有按周数据" why="语料里没有 week 字段，或每周样本量都低于 30 条" />
        ) : (
          <WeeklyChart weekly={d.weekly} />
        )}
      </Card>

      <div className="pg-foot">
        扫描 {nf(Number(d.evidence.scanned) || 0)} 条评论 ·
        {' '}{String(d.evidence.note ?? '')}
      </div>
    </div>
  )
}

/** 把命中词标出来。命中词找不到时退化成原样显示，不隐藏整句。 */
function Mark({ text, hit }: { text: string; hit?: string }) {
  if (!hit) return <>{text}</>
  const i = text.indexOf(hit)
  if (i < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, i)}
      <em className="pg-hit">{hit}</em>
      {text.slice(i + hit.length)}
    </>
  )
}

/** 分周折线：正评率与负评率两条线 + 样本量柱。
 *
 * 【v4.8：从"能看"改成"能问"】
 * 原来这张图只有 SVG 原生 <title> —— 悬停一秒才出、样式跟页面不搭、
 * 键盘和触屏完全够不到。而这张图承载的恰恰是本页最该被追问的一条：
 * "负评 8.1% 是一直 8%，还是最近几周涨上来的？"
 * 这两种情况应对方式完全不同，光看曲线本身分不出来。
 *
 * 现在给了三条路径，对应三种问法：
 *   · 悬停 → 单周读数 + 抽样误差（这一周的 12% 是真的，还是样本太少晃出来的）
 *   · 拖选 → 区间均值 + 趋势斜率（这一段在变好还是变坏）
 *   · 快捷按钮 → 键盘 / 触屏也能拿到区间结论（拖动是鼠标专属动作）
 */
function WeeklyChart({
  weekly,
}: {
  weekly: { week: number; n: number; neg_pct: number; pos_pct: number }[]
}) {
  const W = 1000
  const H = 190
  const PAD = { t: 12, r: 40, b: 20, l: 30 }

  const lo = Math.min(...weekly.map((w) => Math.min(w.neg_pct, w.pos_pct)))
  const hi = Math.max(...weekly.map((w) => Math.max(w.neg_pct, w.pos_pct)))
  const y0 = Math.max(0, lo - 4)
  const y1 = Math.min(100, hi + 4)

  const x = (i: number) => PAD.l + (i / Math.max(weekly.length - 1, 1)) * (W - PAD.l - PAD.r)
  const y = (v: number) => PAD.t + (1 - (v - y0) / Math.max(y1 - y0, 1)) * (H - PAD.t - PAD.b)

  const path = (key: 'neg_pct' | 'pos_pct') =>
    weekly.map((w, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(w[key]).toFixed(1)}`).join(' ')

  const maxN = Math.max(...weekly.map((w) => w.n), 1)
  const barW = Math.max(2, (W - PAD.l - PAD.r) / weekly.length * 0.55)

  const { show, hide, el } = useTip()
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null)
  const [sel, setSel] = useState<{ a: number; b: number } | null>(null)
  const dragRef = useRef<{ a: number; b: number } | null>(null)
  const draggedRef = useRef(false)
  const touchRef = useRef<{ x: number; y: number; i: number; active: boolean } | null>(null)
  const touchGuardRef = useRef(0)
  const svgRef = useRef<SVGSVGElement>(null)

  /* 【抽样误差：这个百分比到底晃多少】
     负评率 = 命中数 / 样本数。样本 40 条的周和样本 2000 条的周，
     同样的 "12%" 可信度差 7 倍。
     1.96·√(p(1-p)/n) 是 95% 置信区间半宽 —— 没有它，读者会把
     W31 的 12% 和 W32 的 9% 读成"降了 3pp"，
     而样本少的时候这个差完全落在误差范围内，属于噪声。

     这也解释了图例那句"每周样本 < 30 条不画"：
     n=30 时半宽约 ±10pp，画出来是一串尖刺，看着像数据，其实全是抽样抖动。 */
  const ciHalf = (p: number, n: number) => {
    if (!n) return 0
    const r = p / 100
    return 1.96 * Math.sqrt(Math.max(r * (1 - r), 0) / n) * 100
  }

  const idxAt = (clientX: number) => {
    const node = svgRef.current
    if (!node) return null
    const box = node.getBoundingClientRect()
    if (box.width <= 0) return null
    /* preserveAspectRatio="none"：viewBox 的 x 和屏幕 x 是线性对应，
       直接用比例换算，不用 getScreenCTM。 */
    const vx = ((clientX - box.left) / box.width) * W
    const step = (W - PAD.l - PAD.r) / Math.max(weekly.length - 1, 1)
    const i = Math.round((vx - PAD.l) / step)
    return i < 0 || i >= weekly.length ? null : i
  }

  const tipFor = (w: (typeof weekly)[number]) => {
    const ci = ciHalf(w.neg_pct, w.n)
    return (
      <div>
        <div className="bi-tip-h">第 {w.week} 周</div>
        <div className="bi-tip-row">负评率 <b>{w.neg_pct.toFixed(2)}%</b></div>
        <div className="bi-tip-row">正评率 <b>{w.pos_pct.toFixed(2)}%</b></div>
        <div className="bi-tip-row">样本 <b>{nf(w.n)}</b> 条</div>
        <div className="bi-tip-row">
          抽样误差 <b>±{ci.toFixed(1)}pp</b>
        </div>
        <div className="bi-tip-row dim">
          {ci > 5
            ? '样本偏少，周间涨跌主要来自抽样波动'
            : ci > 2
              ? '样本中等，±2pp 以内的变化不必当真'
              : '样本充足，这一周的百分比可以直接用'}
        </div>
        <div className="bi-tip-go">拖动可选一段周看区间结论 ›</div>
      </div>
    )
  }

  const active = hoverIdx

  /* ---------------- 框选：开始 / 延伸 / 收尾 ---------------- */
  const selStart = (i: number) => {
    draggedRef.current = false
    dragRef.current = { a: i, b: i }
    setDrag({ a: i, b: i })
  }
  const selExtend = (i: number) => {
    const cur = dragRef.current
    if (!cur || i === cur.b) return
    const next = { a: cur.a, b: i }
    dragRef.current = next
    setDrag(next)
    if (Math.abs(next.a - next.b) >= 1) draggedRef.current = true
  }

  /* 收尾挂 window：鼠标拖到图外再松手也要能结束，
     否则一离开容器就永远松不开，选区卡在拖动中。 */
  const selCommit = useCallback(() => {
    const cur = dragRef.current
    if (!cur) return
    dragRef.current = null
    setDrag(null)
    if (Math.abs(cur.a - cur.b) >= 1) {
      setSel({ a: Math.min(cur.a, cur.b), b: Math.max(cur.a, cur.b) })
      hide()
    }
    /* 复位推迟一个宏任务：click 在 mouseup 之后同步派发 */
    setTimeout(() => { draggedRef.current = false }, 0)
  }, [hide])

  useEffect(() => {
    const up = () => selCommit()
    window.addEventListener('mouseup', up)
    return () => window.removeEventListener('mouseup', up)
  }, [selCommit])

  /* Esc 清选区。框选是"进得去出不来"的典型陷阱。 */
  useEffect(() => {
    if (!sel) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSel(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sel])

  /* 【区间摘要】
     均值是**逐周百分比的简单平均**，不是"总命中/总样本"（加权）。
     两者会给出不同的数，不在界面上说清楚就会有人拿它对 KPI。
     趋势用最小二乘而不是"末周-首周"：首尾各是一个单周观测，
     挑中一个噪声大的周就能把结论反转。 */
  const selInfo = useMemo(() => {
    if (!sel) return null
    const part = weekly.slice(sel.a, sel.b + 1)
    if (!part.length) return null
    const n = part.length
    const meanNeg = part.reduce((s, w) => s + w.neg_pct, 0) / n
    const meanPos = part.reduce((s, w) => s + w.pos_pct, 0) / n
    const allNegMean = weekly.reduce((s, w) => s + w.neg_pct, 0) / weekly.length

    const slope = (() => {
      if (n < 3) return 0
      const mx = (n - 1) / 2
      let num = 0
      let den = 0
      for (let i = 0; i < n; i++) {
        num += (i - mx) * (part[i].neg_pct - meanNeg)
        den += (i - mx) * (i - mx)
      }
      return den ? num / den : 0
    })()

    const peak = part.reduce((m, w) => (w.neg_pct > m.neg_pct ? w : m), part[0])
    const trough = part.reduce((m, w) => (w.neg_pct < m.neg_pct ? w : m), part[0])
    const totalN = part.reduce((s, w) => s + w.n, 0)
    /* 区间整体的抽样误差用总样本算 —— 样本合并后精度比单周高得多，
       这也正是"看区间比看单周靠谱"的数学原因。 */
    const ci = ciHalf(meanNeg, totalN)

    return {
      n, meanNeg, meanPos, slope, peak, trough, totalN, ci,
      deltaVsAll: meanNeg - allNegMean,
    }
  }, [sel, weekly])

  /* 选区几何。跨满一格：首尾各延伸到该周格子的边缘，
     否则 1 周的选区会窄成一条线，看不见。 */
  const selGeom = useMemo(() => {
    const r = drag ?? sel
    if (!r) return null
    const i0 = Math.min(r.a, r.b)
    const i1 = Math.max(r.a, r.b)
    const step = (W - PAD.l - PAD.r) / Math.max(weekly.length - 1, 1)
    const xa = Math.max(PAD.l, x(i0) - step / 2)
    const xb = Math.min(W - PAD.r, x(i1) + step / 2)
    return { x: xa, w: Math.max(xb - xa, 1), y: PAD.t, h: H - PAD.t - PAD.b }
  }, [drag, sel, weekly.length])

  return (
    <div
      className="pg-chart"
      onMouseDown={(e) => {
        if (e.button !== 0) return
        /* 摘要条和快捷按钮在容器内部，点它们不该起框 */
        if ((e.target as HTMLElement).closest('.pg-selbar, .pg-selgo')) return
        const i = idxAt(e.clientX)
        if (i === null) return
        selStart(i)
      }}
      onMouseMove={(e) => {
        const i = idxAt(e.clientX)
        /* 拖动中只更新选区，不出浮层 —— 浮层会正好压住正在框的那段曲线 */
        if (dragRef.current && (e.buttons & 1)) {
          if (i !== null) { selExtend(i); setHoverIdx(i) }
          hide()
          return
        }
        setHoverIdx(i)
        if (i === null) { hide(); return }
        const box = svgRef.current?.getBoundingClientRect()
        const above = box ? e.clientY > box.top + box.height * 0.55 : false
        show(e, tipFor(weekly[i]), above)
      }}
      onMouseLeave={() => { setHoverIdx(null); hide() }}
      /* ---------------- 触屏 ---------------- */
      /* 和趋势图同一套判据：dx≥8 且 dx>dy 才认定是"框选"，
         否则交给页面滚动。图只有 190px 高，手指滑动必然带垂直分量，
         不先观察就直接 preventDefault 会让这块图"手指失灵"。 */
      onTouchStart={(e) => {
        if (e.touches.length !== 1) return
        if ((e.target as HTMLElement).closest('.pg-selbar, .pg-selgo')) return
        const t = e.touches[0]
        const i = idxAt(t.clientX)
        if (i === null) return
        touchRef.current = { x: t.clientX, y: t.clientY, i, active: false }
      }}
      onTouchMove={(e) => {
        const st = touchRef.current
        if (!st) return
        const t = e.touches[0]
        if (!t) return
        if (!st.active) {
          const dx = Math.abs(t.clientX - st.x)
          const dy = Math.abs(t.clientY - st.y)
          if (dx < 8) return
          if (dy > dx) { touchRef.current = null; return }
          st.active = true
          selStart(st.i)
        }
        if (e.cancelable) e.preventDefault()
        const i = idxAt(t.clientX)
        if (i !== null) selExtend(i)
        hide()
      }}
      onTouchEnd={() => {
        const st = touchRef.current
        touchRef.current = null
        if (!st?.active) return
        /* 触屏拖完浏览器会补发一次 click（tap 语义），挡掉它 */
        touchGuardRef.current = Date.now() + 600
        selCommit()
      }}
      onClick={(e) => {
        if (e.button !== 0) return
        if (Date.now() < touchGuardRef.current) return
        if ((e.target as HTMLElement).closest('.pg-selbar, .pg-selgo')) return
        if (draggedRef.current) { draggedRef.current = false; return }
        /* 点空白 = 退出选区。这一页没有"某一周的详情页"可跳，
           所以点击的语义只能是"清掉我刚才选的那段"。 */
        if (idxAt(e.clientX) === null) setSel(null)
      }}
    >
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="pg-chart-svg">
        {[y0, (y0 + y1) / 2, y1].map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)}
              stroke="rgba(255,255,255,.035)" />
            <text x={PAD.l - 6} y={y(t) + 3.5} textAnchor="end" fill="var(--fg-4)"
              fontSize="9.5" fontFamily="var(--mono)">{t.toFixed(0)}</text>
          </g>
        ))}

        {/* 样本量柱：放在底部，提示"这一周的百分比有多可信"。
            圆角按柱宽取（柱宽/3，上限 2.5）：柱只有 ~10px 宽，
            照搬 CSS 那边的 6px 会把一根 10px 的柱做成药丸。
            这里是 viewBox 坐标且 preserveAspectRatio="none"，
            rx 会跟着被横向拉伸，所以上限比视觉想要的值再小一点。 */}
        {weekly.map((w, i) => (
          <rect
            key={w.week}
            x={x(i) - barW / 2}
            y={H - PAD.b - (w.n / maxN) * 26}
            width={barW}
            height={(w.n / maxN) * 26}
            rx={Math.min(2.5, barW / 3)}
            fill="rgba(90,90,97,.20)"
          />
        ))}

        {/* 【选区压暗：画在曲线之前】
            压暗放在曲线之下，选中的那段才浮得出来。
            放到曲线之后就变成"把选中的线也压暗了"，正好反了。
            用 var(--scrim) 而不是 --bg：卡片和 --bg 只差 10 级亮度，
            用 --bg 压上去等于没压。 */}
        {selGeom && (
          <g className="pg-sel-mask" pointerEvents="none" aria-hidden="true">
            <rect x={PAD.l} y={selGeom.y} fill="var(--scrim)"
              width={Math.max(selGeom.x - PAD.l, 0)} height={selGeom.h} />
            <rect x={selGeom.x + selGeom.w} y={selGeom.y} fill="var(--scrim)"
              width={Math.max(W - PAD.r - selGeom.x - selGeom.w, 0)} height={selGeom.h} />
          </g>
        )}

        {/* 【v4：两条线都改灰，靠"亮度 + 线型"区分，不靠色相】
            原来是白(正评) + 红(负评)。红在灰阶界面里是唯一的大色块，
            而这里的"负评"并不需要警示色——它只是两条对比曲线之一。
            改法：正评=亮灰实线，负评=暗灰虚线。
            亮度差（0.92 vs 0.42）足以分开两条线，
            线型差（实线 vs 虚线）让色弱读者也能分辨 —— 这比用颜色更稳妥。 */}
        <path d={path('pos_pct')} fill="none" stroke="rgba(242,242,243,.92)" strokeWidth="1.8"
          strokeLinejoin="round" />
        <path d={path('neg_pct')} fill="none" stroke="rgba(196,196,202,.90)" strokeWidth="1.6"
          strokeDasharray="5 3" strokeLinejoin="round" />

        {/* 【v4.8：圆点不再挂 <title>】
            原生 <title> 只在悬停 1 秒后弹出、样式无法控制，
            而且和下面我们自己的浮层会同时出现两层提示 —— 
            一个屏幕上报同一个数字两遍。整块浮层已经接管了读数。 */}
        {weekly.map((w, i) => (
          <g key={w.week} pointerEvents="none">
            <circle cx={x(i)} cy={y(w.neg_pct)} r="2.2" fill="rgba(196,196,202,.92)" />
            <circle cx={x(i)} cy={y(w.pos_pct)} r="2.2" fill="rgba(242,242,243,.95)" />
          </g>
        ))}

        {/* 准星：竖线从顶穿到底，读者要从线往上下扫去比对两条线 */}
        {active !== null && (
          <g pointerEvents="none" aria-hidden="true">
            <line
              className="pg-cross"
              x1={x(active)} x2={x(active)}
              y1={PAD.t} y2={H - PAD.b}
            />
            <circle
              className="pg-cursor-dot"
              cx={x(active)} cy={y(weekly[active].neg_pct)} r="4.2"
              fill="rgba(196,196,202,.95)"
              stroke="var(--bg)" strokeWidth="1.8"
            />
            <circle
              cx={x(active)} cy={y(weekly[active].pos_pct)} r="4.2"
              fill="rgba(242,242,243,.98)"
              stroke="var(--bg)" strokeWidth="1.8"
            />
          </g>
        )}

        {/* 选区边框画在最上层：拖动中是虚线（还没定），松手后变实线 */}
        {selGeom && (
          <g pointerEvents="none" aria-hidden="true">
            <rect
              className="pg-sel-box"
              x={selGeom.x} y={selGeom.y} width={selGeom.w} height={selGeom.h}
              fill="var(--accent)" fillOpacity={drag ? '.04' : '.07'}
              stroke="var(--accent)" strokeOpacity={drag ? '.4' : '.62'} strokeWidth="1"
              strokeDasharray={drag ? '4 3' : undefined}
            />
            <line className="pg-sel-edge"
              x1={selGeom.x} x2={selGeom.x} y1={selGeom.y} y2={selGeom.y + selGeom.h}
              stroke="var(--accent)" strokeWidth="1.2" strokeOpacity=".85" />
            <line className="pg-sel-edge"
              x1={selGeom.x + selGeom.w} x2={selGeom.x + selGeom.w}
              y1={selGeom.y} y2={selGeom.y + selGeom.h}
              stroke="var(--accent)" strokeWidth="1.2" strokeOpacity=".85" />
          </g>
        )}

        {/* x 轴只标几个关键周，标满会挤成一团。
            光标所在周的字要变亮 —— 浮层写着"第 28 周"而轴上找不到 28，
            等于没有定位。 */}
        {weekly.map((w, i) => {
          if (i % 8 !== 0 && i !== weekly.length - 1) return null
          const on = active === i
          return (
            <text key={w.week} x={x(i)} y={H - 4} textAnchor="middle"
              fill={on ? 'var(--fg)' : 'var(--fg-4)'} fontWeight={on ? 600 : 400}
              fontSize="9.5" fontFamily="var(--mono)">W{w.week}</text>
          )
        })}
      </svg>

      {/* 区间摘要：有选区时接管图例下面这块空间 */}
      {selInfo && (
        <div className="pg-selbar">
          <span className="pg-selbar-h">
            第 {weekly[sel!.a].week}–{weekly[sel!.b].week} 周
            <em>{selInfo.n} 周</em>
          </span>
          <span className="pg-selbar-i">
            负评均值 <b>{selInfo.meanNeg.toFixed(2)}%</b>
            <em className={selInfo.deltaVsAll > 0 ? 't-bad' : 't-good'}>
              比全期 {pp(selInfo.deltaVsAll)}pp
            </em>
          </span>
          <span className="pg-selbar-i">
            趋势{' '}
            {selInfo.n < 3 ? (
              /* 样本点太少时不做回归：3 个点算出来的斜率看着精确，其实全是噪声 */
              <b className="dim">不足 3 周，不给趋势</b>
            ) : (
              <>
                {selInfo.slope > 0 ? '↑ 恶化' : '↓ 改善'}
                <b className={selInfo.slope > 0 ? 't-bad' : 't-good'}>
                  {pp(selInfo.slope * 10)}pp/10周
                </b>
                {/* 【点太少时，数字要自己承认不可靠】
                    实测「近 4 周」会给出 -13.98pp/10周 这种精确到小数后两位的结果。
                    4 个点里挑中一个噪声大的周，斜率能翻几倍甚至换符号 ——
                    这个精度是假的。不加这句，读者会拿它当结论去开会。
                    6 周是经验阈值：再少的时候单点权重超过 25%，稳健性不够。 */}
                {selInfo.n < 6 && <em className="dim">点少，仅看方向</em>}
              </>
            )}
          </span>
          <span className="pg-selbar-i">
            正评均值 <b>{selInfo.meanPos.toFixed(2)}%</b>
          </span>
          <span className="pg-selbar-i">
            峰 <b>{selInfo.peak.neg_pct.toFixed(1)}%</b>
            <em>W{selInfo.peak.week}</em>
            谷 <b>{selInfo.trough.neg_pct.toFixed(1)}%</b>
            <em>W{selInfo.trough.week}</em>
          </span>
          <span className="pg-selbar-i">
            样本 <b>{nf(selInfo.totalN)}</b> 条
            <em>±{selInfo.ci.toFixed(1)}pp</em>
          </span>
          <span className="pg-selbar-note">均值为逐周简单平均</span>
          <button className="bi-link pg-selbar-x" onClick={() => setSel(null)}>
            清除 <kbd>Esc</kbd>
          </button>
        </div>
      )}

      {el}

      {/* 【选区间入口：选区只能靠拖，而拖动是鼠标专属动作】
          键盘用户和触屏用户拖不了 —— 但"这段在变好还是变坏"
          恰恰只能从区间里读出来。给几个一键区间，
          鼠标用户也省得每次去拖。 */}
      {!selInfo && (
        <div className="pg-selgo">
          <span className="pg-selgo-h">拖选一段周看区间结论</span>
          {[
            { label: '近 4 周', n: 4 },
            { label: '近 13 周', n: 13 },
            { label: '全部', n: weekly.length },
          ].map((q) => {
            const k = Math.min(q.n, weekly.length)
            return (
              <button
                key={q.label}
                className="pg-selgo-b"
                onClick={() => setSel({ a: weekly.length - k, b: weekly.length - 1 })}
              >
                {q.label}
              </button>
            )
          })}
        </div>
      )}

      <div className="pg-chart-legend">
        <span><i className="sw good" />正评率</span>
        <span><i className="sw bad" />负评率</span>
        <span><i className="sw gray" />样本量</span>
        <span className="pg-chart-legend-note">每周样本 &lt; 30 条不画（标准误会超过 8pp，画出来全是尖刺）</span>
      </div>
    </div>
  )
}