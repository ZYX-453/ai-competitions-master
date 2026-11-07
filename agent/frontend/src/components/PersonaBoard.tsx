/* ===========================================================================
 * 用户画像工作台
 *
 * 【这一版要解决什么】
 * 旧版是四张静态卡 + 条形 + n 数，问题有三个：
 *   ① 证据没显示。接口给的是 quotes[]（原话 + 星级 + 命中词），
 *      前端读成了 quote 单数，整片原话从来没渲染过。没有证据的画像
 *      就是标签堆砌，读者无法判断"品质优先"这个结论是怎么来的。
 *   ② 没有占比概念。只给 n=2131，读者不知道这是 13,500 里的多少。
 *   ③ 四组之间不可比。使用场景 / 关注点 / 决策特征 / 情绪强度各自成卡，
 *      读者看不出"哪一组才是这批用户最鲜明的特征"。
 *
 * 【这版的形态】
 * 左：画像特征轮廓雷达（四轴 = 四组，轴值 = 该组的组内集中度）
 *     让"这批用户最鲜明的特征"变成一个形状，而不是四张并列的卡。
 * 右：可展开的证据卡。点一条 signal，展开它挂靠的原话，
 *     原话里命中词高亮 —— 这条结论的出处就地可查。
 *
 * 【为什么雷达轴用"组内集中度"而不是组内总和、也不是绝对占比】
 * 组内总和会把"信号多的组"天然抬高（关注点有 5 条、决策特征只有 4 条），
 * 那是分组粒度在说话，不是用户特征在说话。
 * 绝对占比则相反：四组量级差 20 倍（15.8% vs 0.76%），雷达退化成一条线。
 * 集中度 = 最强信号 / 本组总和，四组落在 28%–85%，形状可比，
 * 且本身有业务含义：高 = 该维度成因单一、可单点施策；
 * 低 = 内部分散、需组合动作。真实占比以轴下小字同时给出。
 * =========================================================================== */
import { useMemo, useState } from 'react'
import type { PersonaData, PersonaGroup2, PersonaItem2 } from '../lib/pages'

const nf = (n: number) => n.toLocaleString('zh-CN')

/* 四组的配色。刻意沿用本页已有的色系，不引入新色相。 */
const GROUP_TONE = ['#6B5B95', '#3E8E87', '#C08A3E', '#B0657A']

/* ---------------------------------------------------------------- 雷达图 */
function PersonaRadar({
  axes, active, onHover, onPick,
}: {
  axes: {
    key: string; label: string; value: number; max: number; tone: string
    /* 副标注 = 该组最强信号的真实占比（占全部评论），与主值同屏给出，
       避免读者把"集中度 85%"误读成"85% 的用户是这样" */
    sub: string
  }[]
  active: string | null
  onHover: (k: string | null) => void
  onPick: (k: string) => void
}) {
  const W = 400, H = 322, CX = W / 2, CY = H / 2 + 4, R = 92
  const N = axes.length
  if (!N) return null
  /* 角度从正上方开始，顺时针。第 i 轴的角度 = -90° + i*360/N。 */
  const ang = (i: number) => (-90 + (i * 360) / N) * (Math.PI / 180)
  const pt = (i: number, r: number) => [CX + Math.cos(ang(i)) * r, CY + Math.sin(ang(i)) * r]
  const ring = (frac: number) =>
    axes.map((_, i) => pt(i, R * frac).map((v) => v.toFixed(1)).join(',')).join(' ')

  /* 【为什么半径直接线性映射，不再开方】
     轴值已经换成组内集中度（29%–85%），量程比原来的绝对占比（0.8%–15.8%）
     窄得多，线性映射不会把小值压没。之前那版开方缩放是为了救绝对占比，
     现在反而会把 85% 与 29% 的差距压成 1.7 倍，让"情绪强度最集中"这个
     结论看不出来。 */
  const rOf = (v: number, max: number) => R * Math.min(1, Math.max(0, v / Math.max(max, 1e-9)))
  const poly = axes.map((a, i) => pt(i, rOf(a.value, a.max)).map((v) => v.toFixed(1)).join(',')).join(' ')

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="psb-radar" role="img"
      aria-label={`画像特征轮廓雷达图：${axes.map((a) => `${a.label} 组内集中度 ${a.value.toFixed(0)}%`).join('，')}`}>
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon key={f} points={ring(f)} fill="none" stroke="rgba(120,110,140,.16)" strokeWidth={1} />
      ))}
      {axes.map((a, i) => {
        const [x, y] = pt(i, R)
        return <line key={a.key} x1={CX} y1={CY} x2={x} y2={y}
          stroke={active === a.key ? a.tone : 'rgba(120,110,140,.22)'}
          strokeWidth={active === a.key ? 1.8 : 1} />
      })}
      <polygon points={poly} fill="rgba(107,91,149,.16)" stroke="#6B5B95" strokeWidth={2.2}
        strokeLinejoin="round" />
      {axes.map((a, i) => {
        const [x, y] = pt(i, rOf(a.value, a.max))
        const isOn = active === a.key
        return (
          <g key={a.key}>
            <circle cx={x} cy={y} r={isOn ? 6.5 : 5} fill={a.tone} stroke="#fff" strokeWidth={2} />
            {/* 轴标签放在轴末端外侧，避免压住图形 */}
            {(() => {
              const [lx, ly] = pt(i, R + 20)
              const anchor = Math.abs(lx - CX) < 8 ? 'middle' : lx > CX ? 'start' : 'end'
              return (
                <g onMouseEnter={() => onHover(a.key)} onMouseLeave={() => onHover(null)}
                  onClick={() => onPick(a.key)} style={{ cursor: 'pointer' }}>
                  <text x={lx} y={ly - 9} textAnchor={anchor} fontSize={12.5} fontWeight={650}
                    fill={isOn ? a.tone : '#4A4458'}>{a.label}</text>
                  <text x={lx} y={ly + 5} textAnchor={anchor} fontSize={12}
                    fill={isOn ? a.tone : '#6E687D'}>
                    {a.value.toFixed(0)}<tspan fontSize={9.5} fill={isOn ? a.tone : '#9A93A8'}>% 集中</tspan>
                  </text>
                  <text x={lx} y={ly + 17} textAnchor={anchor} fontSize={9.5} fill="#A39CB0">{a.sub}</text>
                  {/* 透明热区：让文字和空白处都好点 */}
                  <rect x={anchor === 'middle' ? lx - 50 : anchor === 'start' ? lx - 4 : lx - 96}
                    y={ly - 26} width={100} height={50} fill="transparent" />
                </g>
              )
            })()}
          </g>
        )
      })}
    </svg>
  )
}

/* ---------------------------------------------------------------- 原话 */
function QuoteBlock({ q, tone }: { q: { text: string; rating?: number; matched?: string }, tone: string }) {
  /* 命中词高亮。用 split 而不是 innerHTML：评论文本是外部数据，
     拼进 HTML 会有注入风险，而 split 产出的是纯文本节点。 */
  const hit = q.matched?.trim()
  const parts = hit ? q.text.split(hit) : [q.text]
  return (
    <div className="psb-quote">
      <div className="psb-quote-t">
        {q.rating != null && (
          <span className="psb-stars" aria-label={`${q.rating} 星`}>
            {'★'.repeat(Math.max(0, Math.min(5, q.rating)))}
            <i>{'★'.repeat(5 - Math.max(0, Math.min(5, q.rating)))}</i>
          </span>
        )}
        {hit && <span className="psb-hit" style={{ color: tone }}>命中「{hit}」</span>}
      </div>
      <div className="psb-quote-x">
        {parts.map((p, i) => (
          <span key={i}>{p}{i < parts.length - 1 && <mark style={{ background: `${tone}22`, color: tone }}>{hit}</mark>}</span>
        ))}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 单条信号 */
function SignalRow({
  it, tone, open, onToggle, rel,
}: { it: PersonaItem2; tone: string; open: boolean; onToggle: () => void; rel: number }) {
  const pct = (it.pct ?? 0) * 100
  const quotes = it.quotes ?? []
  /* 【条形长度用组内相对值 rel，不用绝对占比】
     绝对占比在组间差 20 倍，同一个 4 倍系数下：
     关注点的 15.8% 撑满、使用场景的 2.1% 只剩 8% 宽 —— 组内五条几乎等高，
     看不出"哪条主导"。rel 由父组件按本组 Top1 归一，
     每组内部都有满格条，排位可读；真实数值仍以右侧百分比为准。 */
  const w = Math.min(100, Math.max(rel * 100, 2))
  return (
    <div className={`psb-sig${open ? ' open' : ''}`}>
      <button className="psb-sig-h" onClick={onToggle}
        aria-expanded={open} aria-label={`${it.signal}，占 ${pct.toFixed(1)}%，${nf(it.n)} 条，${quotes.length ? '点击查看原话证据' : '无原话'}`}>
        <span className="psb-sig-name">{it.signal}</span>
        <span className="psb-sig-track">
          <i style={{ width: `${w}%`, background: tone }} />
        </span>
        <span className="psb-sig-pct" style={{ color: tone }}>{pct.toFixed(1)}%</span>
        <span className="psb-sig-n">{nf(it.n)}</span>
        {quotes.length > 0 && <span className="psb-sig-caret" aria-hidden="true">{open ? '−' : '+'}</span>}
      </button>
      {open && quotes.length > 0 && (
        <div className="psb-sig-body">
          {quotes.map((q, i) => <QuoteBlock key={q.id ?? i} q={q} tone={tone} />)}
        </div>
      )}
    </div>
  )
}

/* ---------------------------------------------------------------- 主组件 */
export function PersonaBoard({ data }: { data: PersonaData }) {
  const groups = useMemo(() => (data.groups ?? []).filter((g) => (g.items ?? []).length), [data.groups])
  const [active, setActive] = useState<string | null>(null)
  const [openKey, setOpenKey] = useState<string | null>(null)

  const axes = useMemo(() => groups.map((g: PersonaGroup2, i) => {
    const items = g.items ?? []
    /* 组内最强信号 = 占比最高的那条。它已经排好序，但这里仍取 max，
       避免依赖后端的排序约定。 */
    const top = items.reduce((m, x) => ((x.pct ?? 0) > (m.pct ?? 0) ? x : m), items[0])
    /* 【主值为什么用"组内集中度"而不是直接占比】
       接口的 pct 是「占全部评论的比例」，四组量级差 20 倍
       （关注点 15.8% vs 决策特征 0.76%）。直接画雷达，形状会退化成
       尖朝右上的一条线，读者读不出任何"特征轮廓"。
       集中度 = 组内最强信号 / 本组所有信号之和，回答的是另一个问题：
       「这一类特征内部，是不是由一个信号主导」。四组都在 28%–85%，
       形状可比，且结论仍有业务含义 —— 集中度高说明该维度成因单一，
       集中度低说明该维度内部分散、不能只针对一条施策。
       真实占比作为副标注同时给出，不让读者误读量级。 */
    const sum = items.reduce((s, x) => s + (x.pct ?? 0), 0)
    const conc = sum > 0 ? ((top?.pct ?? 0) / sum) * 100 : 0
    return {
      key: g.group,
      label: g.group,
      value: conc,
      sub: `最强信号 ${((top?.pct ?? 0) * 100).toFixed(1)}%`,
      topSignal: top?.signal ?? '',
      tone: GROUP_TONE[i % GROUP_TONE.length],
      max: 0,
    }
  }), [groups])
  /* 各轴共用一个 max，否则四轴各归一就等于没有可比性 */
  const max = Math.max(...axes.map((a) => a.value), 100)
  const axesN = axes.map((a) => ({ ...a, max }))

  const dist = data.rating_dist ?? {}
  const totalStar = Object.values(dist).reduce((s, v) => s + v, 0)
  const posShare = totalStar ? ((dist['4'] ?? 0) + (dist['5'] ?? 0)) / totalStar * 100 : 0
  const avgStar = totalStar
    ? Object.entries(dist).reduce((s, [k, v]) => s + Number(k) * v, 0) / totalStar : 0

  if (!groups.length) return null

  return (
    <div className="persona-board" role="group"
      aria-label={`用户画像工作台：${groups.length} 个维度、${data.n ?? 0} 条评论。左侧雷达给出各维度的组内集中度，右侧每条信号可展开原话证据。`}>
      {/* 摘要带：给这组画像一个量级感 */}
      <div className="psb-summary">
        <div className="psb-sum-i"><b>{nf(data.n ?? totalStar)}</b><span>评论样本</span></div>
        <div className="psb-sum-i"><b>{avgStar.toFixed(2)}</b><span>平均星级</span></div>
        <div className="psb-sum-i"><b>{posShare.toFixed(1)}%</b><span>四星及以上占比</span></div>
        <div className="psb-sum-i"><b>{groups.length}</b><span>画像维度</span></div>
      </div>

      <div className="psb-main">
        <div className="psb-left">
          <div className="psb-left-t">画像特征轮廓</div>
          <PersonaRadar axes={axesN} active={active}
            onHover={setActive} onPick={(k) => setActive(active === k ? null : k)} />
          <div className="psb-radar-note">
            轴值 = 该维度的<strong>组内集中度</strong>（最强信号占本组信号总和）。
            高表示这一维度由单一信号主导、可单点施策；低表示内部分散。
            真实占比见轴下小字。四轴共用同一量程，形状可直接比较。
          </div>
        </div>

        <div className="psb-right">
          {groups.map((g, gi) => {
            const tone = GROUP_TONE[gi % GROUP_TONE.length]
            const on = active === g.group
            return (
              <section key={g.group}
                className={`psb-grp${on ? ' on' : ''}`}
                style={{ ['--tone' as string]: tone }}
                onMouseEnter={() => setActive(g.group)}>
                <header className="psb-grp-h">
                  <span className="psb-grp-dot" style={{ background: tone }} />
                  <b>{g.group}</b>
                  {g.desc && <span className="psb-grp-d">{g.desc}</span>}
                </header>
                <div className="psb-grp-list">
                  {(() => {
                    const items = g.items ?? []
                    const topPct = Math.max(...items.map((x) => x.pct ?? 0), 1e-9)
                    return items.map((it) => {
                      const k = `${g.group}/${it.signal}`
                      return (
                        <SignalRow key={k} it={it} tone={tone} open={openKey === k}
                          rel={(it.pct ?? 0) / topPct}
                          onToggle={() => setOpenKey(openKey === k ? null : k)} />
                      )
                    })
                  })()}
                </div>
              </section>
            )
          })}
        </div>
      </div>

      {!!data.cannot?.length && (
        <div className="psb-cannot">
          <b>这组画像回答不了什么</b>
          <ul>{data.cannot.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
    </div>
  )
}