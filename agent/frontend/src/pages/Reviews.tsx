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
 * 【为什么"用户画像"这块必须显示 cannot】
 * 后端返回的 cannot 写明了"评论里没有年龄性别收入，回答不了这些"。
 * 如果界面上只渲染那些百分比，读者一定会把它读成
 * "我的用户 15.8% 是品质优先型人群"——那是编的。
 * 所以 cannot 显示在画像块**内部**，不能塞脚注、不能折叠、
 * 不能放到页面底部的"已知局限"里（那里用户不看）。
 * =========================================================================== */
import { useEffect, useState } from 'react'
import { getJson } from '../lib/sse'
import type { ReviewData } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, pp } from '../components/PageKit'

export function Reviews({
  entities, entity, setEntity,
}: {
  entities: string[]
  entity: string
  setEntity: (e: string) => void
}) {
  const [d, setD] = useState<ReviewData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [wcMode, setWcMode] = useState<'neg' | 'all'>('neg')

  useEffect(() => {
    setErr('')
    getJson<ReviewData>(`/api/pages/reviews?entity=${encodeURIComponent(entity)}`)
      .then(setD)
      .catch((e) => setErr(String(e)))
  }, [entity, tick])

  if (err) return <Fail msg={err} onRetry={() => setTick((x) => x + 1)} />
  if (!d) return <Loading label={`读 ${entity} 的评论`} />

  const s = d.sentiment
  const wc = wcMode === 'neg' ? d.wordcloud_neg : d.wordcloud_all
  const wcIsLift = wcMode === 'neg'
  const ratingTotal = d.rating_dist.reduce((a, b) => a + b.n, 0)
  const maxWc = Math.max(...wc.map((x) => x.count), 1)
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
                  <th>维度</th>
                  <th className="r">提及数</th>
                  <th className="r">负评率</th>
                  <th className="r">均分</th>
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
          note={<>情感判定<b>不读星级</b>（除文本信号太弱时兜底），所以这两个数不是同源的，可以互相检验。</>}
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

      {/* ---- 词云 ---- */}
      <Card
        title={
          <span className="pg-title-withswitch">
            高频词
            <span className="pg-switch">
              <button className={wcMode === 'neg' ? 'on' : ''} onClick={() => setWcMode('neg')}>
                差评（按区分度）
              </button>
              <button className={wcMode === 'all' ? 'on' : ''} onClick={() => setWcMode('all')}>
                全部（按次数）
              </button>
            </span>
          </span>
        }
        note={wcIsLift
          ? <>按 <b>log2 lift</b> 排序：这个词在差评里的出现率，比在好评里高多少倍。商品名会自动沉底，因为它在两边一样多。</>
          : <>按出现次数排，<b>没有做区分度校正</b>，所以会混着商品名和泛词。要看抱怨点，切到"差评"那一栏。</>}
        pad={false}
      >
        {wc.length === 0 ? (
          <Empty what="没有词频数据" />
        ) : (
          <div className="pg-wc">
            {wc.map((w) => (
              <div className="pg-wc-row" key={w.word}>
                <span className="pg-wc-w">{w.word}</span>
                <div className="pg-wc-bar">
                  <Bar
                    pct={(w.count / maxWc) * 100}
                    tone={wcMode === 'neg' ? 'bad' : 'accent'}
                    height={10}
                  />
                </div>
                <span className="pg-wc-n num">{nf(w.count)}</span>
                <span className="pg-wc-lift num" title={
                  w.lift != null
                    ? `差评里比好评里常见 ${Math.pow(2, w.lift).toFixed(1)} 倍`
                    : '这一栏没有算区分度'
                }>
                  {w.lift != null ? `${Math.pow(2, w.lift).toFixed(1)}×` : '—'}
                </span>
                {/* 【原话一定挂在词旁边】
                    只给词不给句子，读者没法判断这个词是不是被误切出来的，
                    也不知道它是"用户在抱怨"还是"用户只是在提"。
                    后端每个词给最多 3 句，取第一句就够 —— 三条会让
                    这一块变成一片文字墙，反而不好扫。

                    【命中词要标出来】
                    原话里那个词常常埋在句子中间（"这样的苹果和商家太坑人了"），
                    不标的话读者要自己找，多数人不会找。 */}
                {w.quotes && w.quotes.length > 0 && (
                  <div className="pg-wc-q">
                    <Mark text={w.quotes[0]} hit={w.word} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* ---- 画像 ---- */}
      <Card
        title="用户画像"
        note={<>每条结论都挂了一句<b>命中信号的原话</b>。原话里打黄底的那个词，就是它被判成这条依据的原因。</>}
      >
        {!d.persona.groups || d.persona.groups.length === 0 ? (
          <Empty what="没有画像数据" />
        ) : (
          <div className="pg-persona">
            {d.persona.groups.map((g) => (
              <div className="pg-ps-group" key={g.group}>
                <div className="pg-ps-h">
                  {g.group}
                  {g.desc && <span className="pg-ps-desc">{g.desc}</span>}
                </div>
                {(g.items ?? []).map((it) => (
                  <div className="pg-ps-item" key={it.signal}>
                    <div className="pg-ps-top">
                      <span className="pg-ps-signal">{it.signal}</span>
                      <span className="pg-ps-pct num">{(it.pct ?? 0).toFixed(1)}%</span>
                      <span className="pg-ps-n num dim">n={nf(it.n)}</span>
                    </div>
                    {it.quote && (
                      <div className="pg-ps-quote">
                        <Mark text={it.quote} hit={it.matched} />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        {/* 【cannot 在画像块内部，不折叠】 */}
        {d.persona.cannot && d.persona.cannot.length > 0 && (
          <div className="pg-cannot">
            <div className="pg-cannot-h">这组画像回答不了什么</div>
            <ul>
              {d.persona.cannot.map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </div>
        )}
      </Card>

      {/* ---- 分周走势 ---- */}
      <Card
        title="分周情感走势"
        note={<>"负评 8.1%"背后可能是"一直 8%"，也可能是"最近三周涨到 15%"——两者的应对完全不同。</>}
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

/** 分周折线：正评率与负评率两条线 + 样本量柱。 */
function WeeklyChart({ weekly }: { weekly: { week: number; n: number; neg_pct: number; pos_pct: number }[] }) {
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

  return (
    <div className="pg-chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="pg-chart-svg">
        {[y0, (y0 + y1) / 2, y1].map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)}
              stroke="rgba(255,255,255,.035)" />
            <text x={PAD.l - 6} y={y(t) + 3.5} textAnchor="end" fill="var(--fg-4)"
              fontSize="9.5" fontFamily="var(--mono)">{t.toFixed(0)}</text>
          </g>
        ))}

        {/* 样本量柱：放在底部，提示"这一周的百分比有多可信" */}
        {weekly.map((w, i) => (
          <rect
            key={w.week}
            x={x(i) - barW / 2}
            y={H - PAD.b - (w.n / maxN) * 26}
            width={barW}
            height={(w.n / maxN) * 26}
            fill="rgba(111,122,143,.20)"
          />
        ))}

        <path d={path('pos_pct')} fill="none" stroke="var(--accent)" strokeWidth="1.5"
          strokeLinejoin="round" />
        <path d={path('neg_pct')} fill="none" stroke="var(--bad)" strokeWidth="1.5"
          strokeLinejoin="round" />

        {weekly.map((w, i) => (
          <g key={w.week}>
            <circle cx={x(i)} cy={y(w.neg_pct)} r="6" fill="transparent">
              <title>第 {w.week} 周 · 负评 {w.neg_pct.toFixed(1)}% · 样本 {w.n} 条</title>
            </circle>
            <circle cx={x(i)} cy={y(w.neg_pct)} r="2.2" fill="var(--bad)" />
            <circle cx={x(i)} cy={y(w.pos_pct)} r="2.2" fill="var(--accent)" />
          </g>
        ))}

        {weekly.filter((_, i) => i % 8 === 0 || i === weekly.length - 1).map((w) => {
          const i = weekly.indexOf(w)
          return (
            <text key={w.week} x={x(i)} y={H - 4} textAnchor="middle" fill="var(--fg-4)"
              fontSize="9.5" fontFamily="var(--mono)">W{w.week}</text>
          )
        })}
      </svg>
      <div className="pg-chart-legend">
        <span><i className="sw good" />正评率</span>
        <span><i className="sw bad" />负评率</span>
        <span><i className="sw gray" />样本量</span>
        <span className="pg-chart-legend-note">每周样本 &lt; 30 条不画（标准误会超过 8pp，画出来全是尖刺）</span>
      </div>
    </div>
  )
}