/* ===========================================================================
 * 预警 · 策划页
 *
 * 【这一页取代了原来的「自检」页】
 * 自检页讲的是"我们验了什么"，是给评审看的质保材料，业务用户看了只会觉得
 * 系统在自夸。这一页换成用户真正要的两件事：
 *   ① 预警：**哪几周需要解释**（以及为什么是这几周）
 *   ② 策划：**选中的那一周谁去做什么**（动作 / 责任方 / 验收指标）
 *      默认落在恶化最严重的那一周，不是"最新一周" —— 所以要写"那一周"，
 *      写成"下一周"会让人以为在看未来排期。
 *
 * 【为什么预警只列"恶化"的周】
 * 判定用的是双门槛（统计显著 + 业务值得管），而它是**双向**的 ——
 * 退货率显著下降的周也会被标成异常。把改善周排进预警清单是错的：
 * 那是好消息，不该派活。所以清单只收 Δpp > 0 的周，
 * 改善周只给一个计数（"另有 N 周显著改善"），读者才知道变好不是判据失灵。
 *
 * 【为什么每一条动作都要带责任方和验收指标】
 * 只写"加强质检""优化包装"这种话，派下去等于没说 —— 没人认领、也没法验收。
 * 所以动作清单来自 action_playbook，它按数据里的 9 类原因给
 * （动作 / 影响指标 / 责任方 / 验收指标）四元组。
 * 缺动作的那一类（比如「未归类」）如实标注"缺的是方案，不是数据"，不拿通用话术去填。
 *
 * 【数字一律是后端算好的成品】
 * rr_now 是 17.04（百分数），delta_pp 是 3.64（百分点），loss_share 是占比。
 * 前端不做除法：一旦在这里除以 total，"总损失"和"各项占比"就可能
 * 因为四舍五入对不上（100.1%），而这种不一致被评审抓住一次，整页数字就没人信了。
 * =========================================================================== */
import { useEffect, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { getJson } from '../lib/sse'
import type { PlanData, PlanTrendPoint } from '../lib/pages'
import { Card, Stat, Empty, Loading, Fail, nf, money, pp } from '../components/PageKit'
import { Segmented, HBars, useTip } from '../components/BiKit'

type ReasonMetric = 'loss' | 'count'

function WeekTrend({
  points, selected, onPick,
}: {
  points: PlanTrendPoint[]
  selected: number
  onPick: (week: number) => void
}) {
  if (!points.length) return null
  const W = 960
  const H = 226
  const left = 48
  const right = 18
  const top = 18
  const bottom = 34
  const values = points.flatMap((p) => [p.rr_now, p.rr_base])
  const rawMin = Math.min(...values)
  const rawMax = Math.max(...values)
  const margin = Math.max((rawMax - rawMin) * 0.12, 0.4)
  const yMin = Math.max(0, rawMin - margin)
  const yMax = rawMax + margin
  const x = (i: number) => left + i * (W - left - right) / Math.max(points.length - 1, 1)
  const y = (v: number) => top + (yMax - v) * (H - top - bottom) / Math.max(yMax - yMin, 0.01)
  const path = (key: 'rr_now' | 'rr_base') => points
    .map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(2)},${y(p[key]).toFixed(2)}`)
    .join(' ')
  const ticks = [0, 1, 2, 3].map((i) => yMax - i * (yMax - yMin) / 3)
  const labelEvery = Math.max(1, Math.ceil(points.length / 8))
  const selectedIndex = Math.max(0, points.findIndex((p) => p.week === selected))

  /* ---- hover 通道：整条曲线都是活的，不只预警点 ----
     【为什么必须做】上一版只有 4 个预警点有 title，其余 48 个点鼠标放上去
     毫无反应 —— 用户原话"太呆板"。BI 走势图的底线是：鼠标扫过哪里，
     哪里就要给出读数。实现是三件事：
       ① 跟随竖线（悬停周的位置，和"选中"的虚线区分：悬停是实线细线）
       ② 最近点放大（CSS transition r，离开缩回 —— 动态感来自这个过渡）
       ③ 浮层读数（周 / 退货率 / 基线 / 偏离 / 判定，above 防盖住折线）
     用 mousemove 找最近周，而不是给 52 个点各挂事件：
     点的命中半径小、扫读时容易丢；整张 SVG 收事件再换算坐标，一次命中。 */
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const { show, hide, el } = useTip()

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    /* clientX → viewBox 坐标：SVG 是等比缩放的，直接拿 clientX 除 step 会错位 */
    const vx = (e.clientX - rect.left) / rect.width * W
    const i = Math.round((vx - left) / ((W - left - right) / Math.max(points.length - 1, 1)))
    const idx = Math.max(0, Math.min(points.length - 1, i))
    setHoverIdx(idx)
    const p = points[idx]
    const statusText = p.status === 'alert' ? '需要派活'
      : p.status === 'improving' ? '显著改善' : '未触发预警'
    show(e, (
      <div>
        <div className="bi-tip-h">第 {p.week} 周 · {statusText}</div>
        <div className="bi-tip-row">退货率 <b>{p.rr_now.toFixed(2)}%</b></div>
        <div className="bi-tip-row">基线 <b>{p.rr_base.toFixed(2)}%</b></div>
        <div className="bi-tip-row">
          偏离{' '}
          <b className={p.delta_pp > 0 ? 't-bad' : 't-good'}>
            {p.delta_pp > 0 ? '+' : ''}{p.delta_pp.toFixed(2)} pp
          </b>
        </div>
        {p.status === 'alert' && <div className="bi-tip-go">点击查看这一周方案 ›</div>}
      </div>
    ), true)
  }
  const onLeave = () => { setHoverIdx(null); hide() }

  return (
    <div className="pg-trend-shell">
      <div className="pg-trend-legend">
        <span><i className="pg-trend-key now" />实际退货率</span>
        <span><i className="pg-trend-key base" />基线</span>
        <span><i className="pg-trend-key alert" />需要派活</span>
        <span><i className="pg-trend-key improve" />显著改善</span>
      </div>
      <div className="pg-trend-wrap">
        <svg
          className="pg-trend"
          viewBox={`0 0 ${W} ${H}`}
          role="group"
          aria-label="52 周退货率走势；红色预警点可回车选择"
          onMouseMove={onMove}
          onMouseLeave={onLeave}
        >
          {ticks.map((v) => (
            <g key={v}>
              <line className="pg-trend-grid" x1={left} x2={W - right} y1={y(v)} y2={y(v)} />
              <text className="pg-trend-y" x={left - 8} y={y(v) + 3}>{v.toFixed(1)}%</text>
            </g>
          ))}
          <line className="pg-trend-cursor" x1={x(selectedIndex)} x2={x(selectedIndex)} y1={top} y2={H - bottom} />
          {/* 悬停跟随线：实线细线，与选中虚线区分 */}
          {hoverIdx !== null && (
            <line
              className="pg-trend-hoverline"
              x1={x(hoverIdx)} x2={x(hoverIdx)} y1={top} y2={H - bottom}
            />
          )}
          <path className="pg-trend-base" d={path('rr_base')} />
          <path className="pg-trend-now" d={path('rr_now')} />
          {points.map((p, i) => {
            const alert = p.status === 'alert'
            const statusText = alert ? '需要派活' : p.status === 'improving' ? '显著改善' : '未触发预警'
            const label = `第 ${p.week} 周，退货率 ${p.rr_now.toFixed(2)}%，基线 ${p.rr_base.toFixed(2)}%，偏离 ${pp(p.delta_pp)} 个百分点，${statusText}`
            const hovered = hoverIdx === i
            return (
              <g
                key={p.week}
                className={alert ? `pg-week-alert${p.week === selected ? ' is-on' : ''}` : 'pg-week-normal'}
                role={alert ? 'button' : undefined}
                tabIndex={alert ? 0 : undefined}
                aria-label={alert ? `${label}。回车查看这一周方案` : undefined}
                aria-current={alert && p.week === selected ? 'true' : undefined}
                onClick={alert ? () => onPick(p.week) : undefined}
                onKeyDown={alert ? (e: KeyboardEvent<SVGGElement>) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onPick(p.week)
                  }
                } : undefined}
              >
                {alert && <circle className="pg-week-hit" cx={x(i)} cy={y(p.rr_now)} r={13} />}
                {/* 悬停放大：r 走 CSS transition；普通点 2.6→4.2，预警点 5→7 */}
                <circle
                  className={`pg-week-point ${p.status}${hovered ? ' is-hover' : ''}`}
                  cx={x(i)} cy={y(p.rr_now)} r={alert ? 5 : 2.6}
                />
                {alert && <text className="pg-week-label" x={x(i)} y={y(p.rr_now) - 12}>W{p.week}</text>}
                <title>{label}</title>
              </g>
            )
          })}
          {points.map((p, i) => (i % labelEvery === 0 || i === points.length - 1) && (
            <text key={`x-${p.week}`} className="pg-trend-x" x={x(i)} y={H - 10}>W{p.week}</text>
          ))}
        </svg>
      </div>
      {el}
    </div>
  )
}

export function Plan({
  categories, category, setCategory,
}: {
  categories: string[]
  category: string
  setCategory: (c: string) => void
}) {
  const [d, setD] = useState<PlanData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  /* 【选中的周必须记清"是在哪个范围里选的"】
     只存一个裸周号会出这种事：在「全部类目」停在第 44 周，切到「个护」——
     个护只有第 48 周异常，但请求仍带着 week=44，后端照 44 返回，
     于是预警清单里没有 44 这一行，下面却写着「第 44 周的策划方案」，
     左边清单和右边方案连不上。不是显示错，是取数口径错。
     把范围和周号绑在一起，换范围就等于"重新让后端挑默认周"。
     默认打开哪一周是业务判断，属于后端的事（见下面 useEffect 的注释）。 */
  const [sel, setSel] = useState<{ wk: number; cat: string } | null>(null)
  const [reasonMetric, setReasonMetric] = useState<ReasonMetric>('loss')
  const [reasonSel, setReasonSel] = useState<{ key: string; week: number; scope: string } | null>(null)
  /* 贡献行浮层：读数（贡献占比 / 基线差 / 单量）随鼠标给出 */
  const { show, hide, el } = useTip()
  const week = sel && sel.cat === category ? sel.wk : null

  useEffect(() => {
    let live = true
    setErr('')
    const q = new URLSearchParams()
    if (category) q.set('category', category)
    if (week != null) q.set('week', String(week))
    const qs = q.toString()
    getJson<PlanData>(`/api/pages/plan${qs ? `?${qs}` : ''}`)
      .then((x) => { if (live) setD(x) })
      .catch((e) => { if (live) setErr(String(e)) })
    return () => { live = false }
  }, [category, week, tick])

  if (err) return <Fail msg={err} onRetry={() => setTick((x) => x + 1)} />
  if (!d) return <Loading label="汇总 52 周的异常判定" />

  const h = d.headline
  const lossV = money(d.loss_actionable)
  const maxContribution = Math.max(...d.contributions.map((c) => c.contribution_pp), 0.01)
  const sortedReasons = [...d.reasons].sort((a, b) => (
    reasonMetric === 'loss' ? b.loss - a.loss : b.count - a.count
  ))
  /* 【选中态必须和"范围+周"绑定，失效就回到当前榜第一名】
     只看 reason 名会串：换到另一周，同名原因可能还在，但那条是另一周的数。
     所以 reasonSel 记 { key, week, scope }，三者都吻合才算"还记得"。
     失效时 fallback 到 sortedReasons[0]（当前指标下的第一名），
     而不是 d.reasons[0]（固定损失第一名）—— 否则左侧按条数排、
     右侧却展开损失第一名，左右对不上。 */
  const remembered = reasonSel && reasonSel.week === d.week && reasonSel.scope === d.scope
    ? d.reasons.find((r) => r.reason === reasonSel.key)
    : undefined
  const activeReason = remembered || sortedReasons[0]

  /* 行级键盘：可点的行必须 Tab 得到（<tr> 不在默认可聚焦列表里） */
  const rowProps = (wk: number) => ({
    tabIndex: 0,
    onClick: () => setSel({ wk, cat: category }),
    onKeyDown: (e: KeyboardEvent<HTMLTableRowElement>) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        setSel({ wk, cat: category })
      }
    },
  })

  return (
    <div className="pg">
      <div className="pg-filterbar">
        <span className="pg-filterbar-k">范围</span>
        <button className={`pg-fchip ${!category ? 'on' : ''}`} onClick={() => setCategory('')}>
          全部类目
        </button>
        {categories.map((c) => (
          <button
            key={c}
            className={`pg-fchip ${category === c ? 'on' : ''}`}
            onClick={() => setCategory(c)}
          >
            {c}
          </button>
        ))}
      </div>

      <div className="pg-kpi-row">
        <Stat
          label="需要解释的周"
          value={d.alert_total}
          unit="周"
          tone={d.alert_total ? 'bad' : 'good'}
          sub={d.improving_total ? `另有 ${d.improving_total} 周显著改善（不进预警）` : '无显著改善周'}
        />
        <Stat
          label={`第 ${h.week} 周退货率`}
          value={h.rr_now}
          unit="%"
          sub={`基线 ${h.rr_base}% · ${d.scope}`}
        />
        <Stat
          label="比基线高出"
          value={pp(h.delta_pp)}
          unit="pp"
          tone={h.delta_pp > 0 ? 'bad' : 'good'}
          sub={`z=${h.z} · 样本 ${nf(h.orders)} 单`}
        />
        <Stat
          label="有动作的损失"
          value={lossV.v}
          unit={lossV.u}
          sub={`占本周损失 ${d.actionable_pct}%`}
        />
      </div>

      <Card
        title="哪几周需要解释"
        note={
          <>
            按 <b>超出基线的百分点</b> 排序，不按 z 值 —— z 会随样本量放大，
            而 Δpp 才是实际多付的代价。点一行可以看那一周该怎么处理。
          </>
        }
        pad={false}
      >
        <WeekTrend points={d.trend} selected={d.week} onPick={(wk) => setSel({ wk, cat: category })} />
        {!d.alerts.length ? (
          <Empty
            what="这 52 周没有需要派活的异常周"
            why={`双门槛判定：既要统计显著，也要超出 ${h.min_effect_pp}pp 的业务线。当前范围内没有一周同时满足。`}
          />
        ) : (
          <div className="pg-table-wrap pg-alert-table">
            <table className="pg-table">
              <thead>
                <tr>
                  <th scope="col">周次</th>
                <th scope="col" className="r">退货率</th>
                <th scope="col" className="r">比基线</th>
                <th scope="col" className="r">z 值</th>
                <th scope="col" className="r">单量</th>
                <th scope="col" className="r">超出业务线</th>
              </tr>
            </thead>
            <tbody>
              {d.alerts.map((a) => (
                <tr
                  key={a.week}
                  className={`clickable${a.week === d.week ? ' is-on' : ''}`}
                  aria-current={a.week === d.week ? 'true' : undefined}
                  {...rowProps(a.week)}
                >
                  <td>第 {a.week} 周</td>
                  <td className="r num">{a.rr_now.toFixed(2)}%</td>
                  <td className="r num t-bad">{pp(a.delta_pp)} pp</td>
                  <td className="r num dim">{a.z.toFixed(2)}</td>
                  <td className="r num dim">{nf(a.orders)}</td>
                  <td className="r num dim">
                    {(a.delta_pp - h.min_effect_pp).toFixed(2)} pp
                  </td>
                </tr>
              ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="pg-note-inline">
          <b>改善周为什么不在这里：</b>判定是双向的，退货率显著下降的周同样"异常"。
          把它们排进预警会让人去查一件好事，所以只计数、不派活。
        </div>
      </Card>

      <div className="pg-2col is-fill">
        <Card
          title="这一周是谁推上去的"
          note={<>按「类目占比 × 与自身基线的差距」拆开。<b>点一行进入该类目</b>，周次按新范围重选。</>}
        >
          {d.contributions.length ? (
            <>
              <div className="pg-contrib">
                {d.contributions.map((c) => {
                  const share = maxContribution > 0
                    ? (c.contribution_pp / maxContribution) * 100 : 0
                  /* 贡献占比（本类目贡献 / 总 Δpp），浮层里回答"这 1.52pp 是总涨幅的多大一块"；
                     后端不算它是因为它是纯展示比值（分母是页面上已有的总数），不是口径换算 */
                  const ofTotal = h.delta_pp !== 0
                    ? (c.contribution_pp / h.delta_pp) * 100 : 0
                  return (
                    <button
                      type="button"
                      className={`pg-contrib-r${category === c.category ? ' is-on' : ''}`}
                      key={c.category}
                      onClick={() => setCategory(c.category)}
                      onMouseMove={(e) => show(e, (
                        <div>
                          <div className="bi-tip-h">{c.category || '未分类'} · 本周贡献</div>
                          <div className="bi-tip-row">
                            贡献 <b>{pp(c.contribution_pp)} pp</b>
                            （占涨幅 {ofTotal > 0 ? ofTotal.toFixed(0) : '—'}%）
                          </div>
                          <div className="bi-tip-row">
                            退货率 <b>{c.rr_now.toFixed(2)}%</b> / 基线 {c.rr_base.toFixed(2)}%
                          </div>
                          <div className="bi-tip-row dim">
                            与自身基线差 {pp(c.gap_pp)} pp · {nf(c.orders_now)} 单
                          </div>
                          <div className="bi-tip-go">点击进入{c.category || '未分类'} ›</div>
                        </div>
                      ))}
                      onMouseLeave={hide}
                      aria-pressed={category === c.category}
                      aria-label={`查看${c.category || '未分类'}的预警策划；本周贡献 ${pp(c.contribution_pp)} 个百分点`}
                    >
                      <span className="pg-contrib-k">{c.category || '未分类'}</span>
                      <span className="pg-contrib-b">
                        <span className="pg-contrib-track">
                          <span
                            className="pg-contrib-fill"
                            style={{ width: `${Math.max(0, Math.min(100, share))}%` }}
                          />
                        </span>
                      </span>
                      <span className="pg-contrib-v num t-bad">{pp(c.contribution_pp)}pp</span>
                      <span className="pg-contrib-s dim num">
                        {c.rr_now.toFixed(2)}% / 基线 {c.rr_base.toFixed(2)}%
                      </span>
                    </button>
                  )
                })}
              </div>
              {/* 【把差额摆出来，否则读者会以为算错了】
                  4 项之和 3.53pp 与总量 3.64pp 之间差 0.11pp，
                  那是类目占比本身在变（结构变化），不是加错。
                  差额小于 0.05pp 就不提 —— 那是四舍五入的尾巴，
                  专门标一句反而像在解释一个不存在的问题。 */}
              {Math.abs(d.contrib_rest_pp) >= 0.05 && (
                <div className="pg-contrib-foot">
                  <span className="num">{d.contrib_n <= 4 ? '四项合计' : '前四项合计'} {pp(d.contrib_sum_pp)}pp</span>
                  <span className="pg-contrib-foot-r">
                    未分配 {d.contrib_rest_pp > 0 ? '+' : ''}{pp(d.contrib_rest_pp)}pp
                    —— 类目占比本身在变（结构变化），不是加错
                  </span>
                </div>
              )}
            </>
          ) : (
            <Empty what="没有类目贡献拆分" why="这一周的样本不足以拆到类目层。" />
          )}
        </Card>

        <Card title="判定依据" note="判定不是黑箱：两个门槛的数都摆出来，可以自己核对。">
          <div className="pg-verdict2">
            <div className="pg-verdict2-r">
              <span className="pg-verdict2-k">统计显著</span>
              <span className="pg-verdict2-v num">z = {h.z}</span>
              <span className={`pg-verdict2-j ${h.z >= 2 ? 'ok' : 'warn'}`}>
                {h.z >= 2 ? '过线' : '未达'}
              </span>
            </div>
            <div className="pg-verdict2-sub">
              可检测下限 {h.detectable_pp}pp（显著线 z=2）
            </div>
            <div className="pg-verdict2-r">
              <span className="pg-verdict2-k">业务值得管</span>
              <span className="pg-verdict2-v num t-bad">{pp(h.delta_pp)}pp</span>
              <span className="pg-verdict2-j ok">过线</span>
            </div>
            <div className="pg-verdict2-sub">
              业务线 {h.min_effect_pp}pp，超出 {(h.delta_pp - h.min_effect_pp).toFixed(2)}pp
            </div>
            <div className="pg-verdict2-r">
              <span className="pg-verdict2-k">样本充足</span>
              <span className="pg-verdict2-v num">{nf(h.orders)} 单</span>
              <span className={`pg-verdict2-j ${h.enough_sample ? 'ok' : 'warn'}`}>
                {h.enough_sample ? '充足' : '偏少'}
              </span>
            </div>
            <div className="pg-verdict2-sub">
              {h.enough_sample ? '足够拆到类目层' : '样本偏少，结论仅供参考'}
            </div>
            <div className="pg-verdict2-r">
              <span className="pg-verdict2-k">命中结论</span>
              <span className="pg-verdict2-v">
                {h.is_anomaly ? '双门槛都过' : '未通过双门槛'}
              </span>
              <span className={`pg-verdict2-j ${h.is_anomaly ? 'bad' : 'muted'}`}>
                {h.is_anomaly ? '需要解释' : '不报'}
              </span>
            </div>
          </div>
        </Card>
      </div>

      <Card
        title={`第 ${d.week} 周的策划方案`}
        note={
          <>
            左边按原因<b>排行</b>，右边只展开<b>当前选中</b>那一类的动作。
            默认按损失排，先动钱最多的那条；每条动作都带责任方和验收指标。
          </>
        }
        pad={false}
      >
        {d.reasons.length ? (
          <div className="pg-plan-master">
            {/* ---- 左：原因排行（可切换指标、可点选） ---- */}
            <div className="pg-plan-rank">
              <div className="pg-plan-rank-head">
                <Segmented<ReasonMetric>
                  value={reasonMetric}
                  options={[
                    { key: 'loss', label: '按损失' },
                    { key: 'count', label: '按条数' },
                  ]}
                  onChange={setReasonMetric}
                />
              </div>
              <HBars
                items={sortedReasons.map((r) => {
                  const lv = money(r.loss)
                  return {
                    key: r.reason,
                    label: r.reason,
                    /* 【条长按当前指标归一，不是装饰】
                       切到"按条数"时，条形长度要跟着条数重排，
                       否则读者看到"损失 8.4 万"的条最长、却排在"条数"榜第一名下面，
                       长度和文字打架。分母取本指标的最大值。 */
                    pct: reasonMetric === 'loss' ? r.loss : r.count,
                    text: reasonMetric === 'loss' ? `${lv.v}${lv.u}` : `${nf(r.count)} 条`,
                    text2: reasonMetric === 'loss'
                      ? `${nf(r.count)} 条 · 占损失 ${r.loss_share}%`
                      : `损失 ${lv.v}${lv.u} · 占 ${r.share}%`,
                    tone: r.has_plan ? 'bad' : 'lo',
                    active: activeReason && activeReason.reason === r.reason,
                    onClick: () => setReasonSel({ key: r.reason, week: d.week, scope: d.scope }),
                    tip: (
                      <div>
                        <div className="bi-tip-h">{r.reason}</div>
                        <div className="bi-tip-row">损失 <b>{lv.v}{lv.u}</b>（占 {r.loss_share}%）</div>
                        <div className="bi-tip-row">{nf(r.count)} 条 · 占退货 {r.share}%</div>
                        <div className="bi-tip-row dim">{r.has_plan ? '有既定动作方案' : '缺动作方案 —— 缺的是方案不是数据'}</div>
                      </div>
                    ),
                  }
                })}
                max={Math.max(
                  ...sortedReasons.map((r) => (reasonMetric === 'loss' ? r.loss : r.count)),
                  1,
                )}
              />
            </div>

            {/* ---- 右：当前原因的动作明细 ---- */}
            <div className="pg-plan-detail">
              {activeReason && (
                <>
                  <div className="pg-plan-detail-h">
                    <span className="pg-plan-n">
                      {activeReason.reason}
                      {activeReason.cause_name && activeReason.cause_name !== activeReason.reason && (
                        <em className="pg-plan-alias">动作归到「{activeReason.cause_name}」</em>
                      )}
                    </span>
                    <span className="pg-plan-m dim num">
                      {nf(activeReason.count)} 条 · 占 {activeReason.share}% · 损失 {money(activeReason.loss).v}{money(activeReason.loss).u}（占 {activeReason.loss_share}%）
                    </span>
                    {!activeReason.has_plan && <span className="pg-tag">缺动作方案</span>}
                  </div>
                  {activeReason.has_plan ? (
                    <table className="pg-table">
                      <thead>
                        <tr>
                          <th scope="col">动作</th>
                          <th scope="col">影响</th>
                          <th scope="col">责任方</th>
                          <th scope="col">验收指标</th>
                        </tr>
                      </thead>
                      <tbody>
                        {activeReason.actions.map((a, k) => (
                          <tr key={k}>
                            <td>{a.action}</td>
                            <td className="dim">{a.impact_on}</td>
                            <td>{a.owner}</td>
                            <td className="dim">{a.kpi}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <p className="pg-note-inline">{activeReason.plan_note}</p>
                  )}
                </>
              )}
            </div>
          </div>
        ) : (
          <Empty what="这一周没有拆出原因" why="退货记录里这一周的原因字段为空。" />
        )}
        <div className="pg-note-inline">
          <b>动作从哪来：</b>按数据里的原因码取自 action_playbook（9 类原因各一组）。
          它们是从业务口径整理的整改动作，<b>不是</b>模型现场编的；
          没有既定动作的原因会标「缺动作方案」，不会用通用话术凑数。
        </div>
      </Card>
      {el}
    </div>
  )
}
