/* ===========================================================================
 * 总览页
 *
 * 【这一页要回答什么】
 * 三个问题，从上到下：现在怎么样 → 哪里最疼 → 什么时候出过问题。
 *
 * 【为什么第一屏一定要有曲线，而不是只放几个大数字】
 * "退货率 13.53%" 这个数字单独存在时是没法判断的：它算高吗？
 * 和上个月比呢？波动是正常的还是在恶化？
 * 曲线一次性回答了"基准在哪、波动多大、现在偏在哪"三件事。
 * 而且它是后面所有异常判定的视觉依据——图上标红的那几个点，
 * 就是归因页要解释的那几周。
 * =========================================================================== */
import { useEffect, useState } from 'react'
import { getJson } from '../lib/sse'
import type { DashboardData, WeekPoint } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, money, pp } from '../components/PageKit'

export function Dashboard({ onOpenWeek }: { onOpenWeek: (w: number) => void }) {
  const [d, setD] = useState<DashboardData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => {
    setErr('')
    getJson<DashboardData>('/api/pages/dashboard')
      .then(setD)
      .catch((e) => setErr(String(e)))
  }, [tick])

  if (err) return <Fail msg={err} onRetry={() => setTick((x) => x + 1)} />
  if (!d) return <Loading label="汇总 52 周数据" />

  const k = d.kpi
  const loss = money(k.loss_total)
  const gmv = money(k.gmv_year)
  const gp = money(k.gross_profit_year)

  return (
    <div className="pg">
      <div className="pg-kpi-row">
        <Stat
          label="年订单"
          value={nf(k.orders_year)}
          unit="单"
          sub={`${k.weeks} 周 · ${k.skus} 个 SKU`}
        />
        <Stat label="GMV" value={gmv.v} unit={gmv.u} sub={`客单价 ¥${k.aov.toFixed(2)}`} />
        <Stat
          label="退货率"
          value={k.return_rate.toFixed(2)}
          unit="%"
          sub={`基准 ${d.baseline.mean_rr.toFixed(2)}% ± ${d.baseline.std_rr.toFixed(2)}`}
          tone={k.return_rate > d.baseline.mean_rr + d.baseline.std_rr ? 'bad' : undefined}
        />
        <Stat
          label="退货损失"
          value={loss.v}
          unit={loss.u}
          sub={`占毛利 ${k.loss_share_of_gp.toFixed(1)}%`}
          tone="bad"
          title="口径：退货造成的净损失（含物流与折价），不含机会成本"
        />
        <Stat
          label="需解释的周"
          value={d.anomalies.length}
          unit={`/ ${k.weeks}`}
          sub={d.anomalies.length
            ? `第 ${d.anomalies.map((a) => a.week).join('、')} 周`
            : '无异常周'}
          tone={d.anomalies.length ? 'warn' : 'good'}
          title="双门槛判定：|z|≥2 且 |Δpp|≥1.5，两个都满足才算异常"
        />
      </div>

      <Card
        title="52 周退货率"
        note={<>灰带是 ±2.58σ 的正常波动范围；红点是<b>双门槛</b>判定的异常周（|z|≥2 且偏离 ≥1.5pp）。点红点直接进归因。</>}
        pad={false}
      >
        <TrendChart
          series={d.series}
          band={d.baseline}
          onPick={onOpenWeek}
        />
      </Card>

      <div className="pg-2col">
        <Card
          title="类目横向对比"
          note={<>按近 12 周退货率排序。Δ 是近 12 周相对之前的百分点变化。</>}
          pad={false}
        >
          <table className="pg-table">
            <thead>
              <tr>
                <th>类目</th>
                <th className="r">订单</th>
                <th className="r">退货率</th>
                <th className="r">近12周</th>
                <th className="r">变化</th>
                <th className="r">SKU</th>
              </tr>
            </thead>
            <tbody>
              {d.by_category.map((c) => (
                <tr key={c.category}>
                  <td>{c.category}</td>
                  <td className="r num">{nf(c.orders)}</td>
                  <td className="r num">
                    <div className="pg-cell-bar">
                      <span>{c.return_rate.toFixed(2)}%</span>
                      <Bar
                        pct={(c.return_rate / Math.max(...d.by_category.map((x) => x.return_rate), 1)) * 100}
                        tone="bad"
                        height={4}
                      />
                    </div>
                  </td>
                  <td className="r num">{c.rr_last12.toFixed(2)}%</td>
                  <td className={`r num ${c.delta_pp > 0.3 ? 't-bad' : c.delta_pp < -0.3 ? 't-good' : ''}`}>
                    {pp(c.delta_pp)}
                  </td>
                  <td className="r num dim">{c.sku_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card
          title="退货原因结构"
          note={<>按条数排。损失/条 = 该原因平摊到每条退货的净损失。</>}
          pad={false}
        >
          <table className="pg-table">
            <thead>
              <tr>
                <th>原因</th>
                <th className="r">条数</th>
                <th className="r">占比</th>
                <th className="r">损失</th>
                <th className="r">损失/条</th>
              </tr>
            </thead>
            <tbody>
              {d.causes.map((c) => (
                <tr key={c.code}>
                  <td>
                    {c.cause}
                    <span className="pg-code">{c.code}</span>
                  </td>
                  <td className="r num">{nf(c.n)}</td>
                  <td className="r num">{c.share.toFixed(2)}%</td>
                  <td className="r num">
                    {money(c.loss).v}{money(c.loss).u}
                  </td>
                  <td className="r num dim">¥{c.loss_per.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <Card
        title="需要解释的周"
        note={<>只列通过双门槛的周。点一周进归因页，看它涨在哪、为什么涨。</>}
        pad={false}
      >
        {d.anomalies.length === 0 ? (
          <Empty
            what="没有异常周"
            why="52 周里没有任何一周同时满足统计显著与业务显著——这本身是个结论，不是数据缺失"
          />
        ) : (
          <table className="pg-table">
            <thead>
              <tr>
                <th>周次</th>
                <th className="r">退货率</th>
                <th className="r">偏离基准</th>
                <th className="r">z 值</th>
                <th className="r">订单</th>
                <th className="r">退货</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {d.anomalies.map((a) => (
                <tr key={a.week} className="clickable" onClick={() => onOpenWeek(a.week)}>
                  <td className="num strong">第 {a.week} 周</td>
                  <td className="r num">{a.rr.toFixed(2)}%</td>
                  <td className={`r num ${a.delta_pp > 0 ? 't-bad' : 't-good'}`}>
                    {pp(a.delta_pp)} pp
                  </td>
                  <td className="r num">{a.z.toFixed(2)}</td>
                  <td className="r num dim">{nf(a.orders)}</td>
                  <td className="r num dim">{nf(a.returns)}</td>
                  <td className="r">
                    <span className="pg-go">归因 ›</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <div className="pg-foot">
        扫描 {nf(d.evidence.rows_scanned)} 行周度面板 · {nf(d.evidence.returns_scanned)} 条退货记录 ·
        {' '}口径：{d.evidence.scope}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 折线图 */
function TrendChart({
  series, band, onPick,
}: {
  series: WeekPoint[]
  band: { mean_rr: number; band_low: number; band_high: number }
  onPick: (w: number) => void
}) {
  const W = 1000
  const H = 220
  const PAD = { t: 14, r: 12, b: 22, l: 34 }

  const vals = series.map((s) => s.rr)
  // 【y 轴范围要把灰带包进去】
  // 只按数据算的话，±2.58σ 的带子会被切掉一截，
  // 于是"正常范围"看起来比实际窄——这直接影响读者对"偏了多少"的判断。
  const lo = Math.min(...vals, band.band_low)
  const hi = Math.max(...vals, band.band_high)
  const span = Math.max(hi - lo, 0.5)
  const y0 = lo - span * 0.12
  const y1 = hi + span * 0.12

  const x = (i: number) => PAD.l + (i / Math.max(series.length - 1, 1)) * (W - PAD.l - PAD.r)
  const y = (v: number) => PAD.t + (1 - (v - y0) / (y1 - y0)) * (H - PAD.t - PAD.b)

  const line = series.map((s, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(s.rr).toFixed(1)}`).join(' ')
  const area = `${line} L${x(series.length - 1).toFixed(1)},${y(y0).toFixed(1)} L${x(0).toFixed(1)},${y(y0).toFixed(1)} Z`

  // y 轴刻度取 4 条
  const ticks = Array.from({ length: 4 }, (_, i) => y0 + ((y1 - y0) * i) / 3)

  return (
    <div className="pg-chart">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="pg-chart-svg">
        <defs>
          <linearGradient id="dashFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.20" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.01" />
          </linearGradient>
        </defs>

        {/* 正常波动带 */}
        <rect
          x={PAD.l} y={y(band.band_high)}
          width={W - PAD.l - PAD.r}
          height={Math.max(y(band.band_low) - y(band.band_high), 1)}
          fill="rgba(168,178,196,.07)"
        />
        {/* 基线 */}
        <line
          x1={PAD.l} x2={W - PAD.r} y1={y(band.mean_rr)} y2={y(band.mean_rr)}
          stroke="rgba(168,178,196,.35)" strokeDasharray="3 4" strokeWidth="1"
        />

        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)}
              stroke="rgba(255,255,255,.035)" strokeWidth="1" />
            <text x={PAD.l - 7} y={y(t) + 3.5} textAnchor="end"
              fill="var(--fg-4)" fontSize="9.5" fontFamily="var(--mono)">
              {t.toFixed(1)}
            </text>
          </g>
        ))}

        <path d={area} fill="url(#dashFill)" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="1.5"
          strokeLinejoin="round" strokeLinecap="round" />

        {series.map((s, i) => {
          if (!s.is_anomaly) return null
          return (
            <g key={s.week} className="pg-dot-g" onClick={() => onPick(s.week)}>
              {/* 透明大圆扩大点击热区：1.5px 的线太难点 */}
              <circle cx={x(i)} cy={y(s.rr)} r="9" fill="transparent" />
              <circle cx={x(i)} cy={y(s.rr)} r="3.4" fill="var(--bad)"
                stroke="var(--bg)" strokeWidth="1.6" />
              <title>第 {s.week} 周 · {s.rr.toFixed(2)}%（{pp(s.delta_pp)}pp, z={s.z.toFixed(1)}）</title>
            </g>
          )
        })}

        {/* x 轴只标几个关键周，标满会挤成一团 */}
        {series.filter((_, i) => i % 8 === 0 || i === series.length - 1).map((s, _, arr) => {
          const i = series.indexOf(s)
          return (
            <text key={s.week} x={x(i)} y={H - 5} textAnchor="middle"
              fill="var(--fg-4)" fontSize="9.5" fontFamily="var(--mono)">
              W{s.week}
            </text>
          )
        })}
      </svg>
    </div>
  )
}