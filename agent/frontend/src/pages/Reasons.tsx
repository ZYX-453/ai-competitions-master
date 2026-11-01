/* ===========================================================================
 * 原因治理页
 *
 * 【这一页要回答的问题跟总览不同】
 * 总览问"哪个原因条数最多"，那是个描述。
 * 这一页问"**哪个原因值得花钱去管**"——这是个决策。
 *
 * 两个问题的答案经常不一样，而且不一样的地方恰恰是重点：
 *   · 条数最多的原因，可能每条只损失几十块，管它不划算
 *   · 条数少但单条损失大的（比如数码的整机故障），才是真正在吃毛利
 * 所以表头的排序默认按**损失**而不是按条数。
 *
 * 【"可再售"这一列为什么必须出现】
 * 同样 10 万损失，可再售率高的那些还能挽回一部分货值，
 * 可再售率接近 0 的是纯沉没。不带这一列，"值不值得救"就没法判断。
 * 但注意别把它读成质检结论——它是账面口径（restock_loss_rate），
 * 不是"这批货还能卖"的实际判定。这句话写在页面上了。
 * =========================================================================== */
import { useEffect, useState } from 'react'
import { getJson } from '../lib/sse'
import type { ReasonsData } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, money } from '../components/PageKit'

export function Reasons({
  categories, category, setCategory,
}: {
  categories: string[]
  category: string
  setCategory: (c: string) => void
}) {
  const [d, setD] = useState<ReasonsData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [sortBy, setSortBy] = useState<'loss' | 'n' | 'loss_per'>('loss')

  useEffect(() => {
    setErr('')
    const q = category ? `?category=${encodeURIComponent(category)}` : ''
    getJson<ReasonsData>(`/api/pages/reasons${q}`)
      .then(setD)
      .catch((e) => setErr(String(e)))
  }, [category, tick])

  if (err) return <Fail msg={err} onRetry={() => setTick((x) => x + 1)} />
  if (!d) return <Loading label="汇总 9 类退货原因" />

  const rows = [...d.rows].sort((a, b) => b[sortBy] - a[sortBy])
  const totalLoss = money(d.total_loss)
  const maxLoss = Math.max(...d.rows.map((r) => r.loss), 1)
  const totalRecoverable = d.rows.reduce((s, r) => s + r.recoverable, 0)
  const rc = money(totalRecoverable)

  // 【只看损失最大的那条，给出一个能直接动手的判断】
  const top = [...d.rows].sort((a, b) => b.loss - a.loss)[0]

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
        <Stat label="退货条数" value={nf(d.total_returns)} unit="条"
          sub={d.scope} />
        <Stat label="净损失" value={totalLoss.v} unit={totalLoss.u} tone="bad"
          sub={`平均每条 ¥${(d.total_loss / Math.max(d.total_returns, 1)).toFixed(0)}`} />
        <Stat label="其中可挽回" value={rc.v} unit={rc.u} tone="good"
          sub={`占损失 ${(totalRecoverable / Math.max(d.total_loss, 1) * 100).toFixed(1)}%`}
          title="按各原因的 is_resellable 占比折算。这是账面口径，不等于「这批货真的还能卖」。" />
        <Stat label="损失最大的原因" value={top?.l2 ?? '—'}
          sub={top ? `${money(top.loss).v}${money(top.loss).u} · 占 ${top.loss_share.toFixed(1)}%` : ''}
          tone="warn" />
      </div>

      <Card
        title="每个原因值不值得管"
        note={<>点表头可以换排序。<b>按损失排</b>和<b>按条数排</b>给出的答案经常不同——那正是要看清的地方。</>}
        pad={false}
      >
        <table className="pg-table">
          <thead>
            <tr>
              <th>原因</th>
              <th>层级</th>
              <th className="r sortable" onClick={() => setSortBy('n')}>
                条数 {sortBy === 'n' && '↓'}
              </th>
              <th className="r">占条数</th>
              <th className="r sortable" onClick={() => setSortBy('loss')}>
                损失 {sortBy === 'loss' && '↓'}
              </th>
              <th className="r">占损失</th>
              <th className="r sortable" onClick={() => setSortBy('loss_per')}>
                损失/条 {sortBy === 'loss_per' && '↓'}
              </th>
              <th className="r">可再售</th>
              <th className="r">可挽回</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.code}>
                <td>
                  {r.l2}
                  <span className="pg-code">{r.code}</span>
                </td>
                <td className="dim">{r.l1}</td>
                <td className="r num">{nf(r.n)}</td>
                <td className="r num dim">{r.share.toFixed(2)}%</td>
                <td className="r num">
                  <div className="pg-cell-bar">
                    <span>{money(r.loss).v}{money(r.loss).u}</span>
                    <Bar pct={(r.loss / maxLoss) * 100} tone="bad" height={4} />
                  </div>
                </td>
                <td className="r num">{r.loss_share.toFixed(2)}%</td>
                <td className="r num">¥{r.loss_per.toFixed(0)}</td>
                <td className={`r num ${r.resellable < 5 ? 't-bad' : r.resellable > 30 ? 't-good' : ''}`}>
                  {r.resellable.toFixed(1)}%
                </td>
                <td className="r num dim">
                  {money(r.recoverable).v}{money(r.recoverable).u}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="pg-note-inline">
          <b>可再售</b>是账面口径（退货入库时按品相折价后的残值），不是质检结论。
          它回答"这批货还能挽回多少账面价值"，不回答"这批货还能不能卖"。
        </div>
      </Card>

      <Card
        title="类目 × 原因 热力矩阵"
        note={<>格子值是<b>该原因占这个类目退货的比例</b>。用占比而不是绝对条数，是因为类目体量差 3 倍，绝对数会让所有格子都偏向大目。</>}
        pad={false}
      >
        {d.matrix.categories.length === 0 ? (
          <Empty what="没有矩阵数据" />
        ) : (
          <div className="pg-heat-wrap">
            <table className="pg-table pg-heat">
              <thead>
                <tr>
                  <th className="pg-heat-corner">类目 \ 原因</th>
                  {d.matrix.causes.map((c) => (
                    <th key={c} className="r pg-heat-th">{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.matrix.categories.map((cat) => {
                  const row = d.matrix.rows.find((x) => x.category === cat)
                  return (
                    <tr key={cat}>
                      <td className="strong">{cat}</td>
                      {d.matrix.causes.map((c) => {
                        const v = Number(row?.[c] ?? 0)
                        const mx = d.matrix.col_max[c] || 1
                        // 【用相对本列最大值的强度着色】
                        // 绝对阈值会让某些列（比如"价值感知不符"）
                        // 整列深、另一些列整列浅，看不出类目间的相对差异。
                        const a = Math.min(1, v / mx)
                        return (
                          <td key={c} className="r num pg-heat-td">
                            <span
                              className="pg-heat-fill"
                              style={{
                                background: v > 0
                                  ? `rgba(242,85,90,${0.06 + a * 0.52})`
                                  : 'transparent',
                              }}
                            >
                              {v > 0 ? v.toFixed(1) : ''}
                            </span>
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <div className="pg-note-inline">
              每列颜色深浅是<b>相对本列最大值</b>归一化的，所以横向比"深浅"有意义，
              纵向比"深浅"没有。要纵向比就看数字本身。
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}