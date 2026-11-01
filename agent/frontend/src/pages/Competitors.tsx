/* ===========================================================================
 * 竞品雷达页
 *
 * 【这一页最大的风险不是算错，是被读错】
 * 竞品评论是**模拟数据**（独立 DGP 生成）。如果页面上不写这件事，
 * 读者会把它读成"我们真的抓了竞品的评论"。那就不只是数字问题，
 * 是性质问题。所以"数据性质"这一列必须逐行显示，而不是写成脚注。
 *
 * 【为什么负评率差要带 z 检验和 p 值】
 * 4 个竞品各 1,040 条时，4pp 的差异是显著的；
 * 但如果某家只有 200 条，同样的 4pp 就是噪声。
 * 不标显著性的话，读者会把"表里最小的那个数"直接读成"最好的那家"。
 *
 * 【为什么维度差距要做成表格而不是雷达图】
 * 雷达图好看，但它让"差多少"变得不可读：
 * 四条线叠在一个多边形里，读者只能看出"谁包住了谁"，
 * 看不出具体差几个点，也排不了序。而这一页要回答的正是"差在哪、差多少"。
 * =========================================================================== */
import { useEffect, useState } from 'react'
import { getJson } from '../lib/sse'
import type { CompetitorData } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, pp } from '../components/PageKit'

export function Competitors({
  categories, category, setCategory,
}: {
  categories: string[]
  category: string
  setCategory: (c: string) => void
}) {
  const [d, setD] = useState<CompetitorData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)

  useEffect(() => {
    setErr('')
    const q = category ? `?category=${encodeURIComponent(category)}` : ''
    getJson<CompetitorData>(`/api/pages/competitors${q}`)
      .then(setD)
      .catch((e) => setErr(String(e)))
  }, [category, tick])

  if (err) return <Fail msg={err} onRetry={() => setTick((x) => x + 1)} />
  if (!d) return <Loading label="对齐 5 家语料" />

  const us = d.entities.find((e) => e.entity.startsWith('本店'))
  const rank = us ? [...d.entities].sort((a, b) => a.neg_pct - b.neg_pct)
    .findIndex((e) => e.entity === us.entity) + 1 : 0
  // 【不要在这里数 "diff_pp > 0" 当"我们更差的家数"】
  // diff_pp 的方向是「对方 − 本店」，正数意味着对方更差。
  // 按 diff_pp > 0 数出来的 4 家，实际是"4 家都不如我们"，
  // 而第一版把它标成了"显著差于我们"，语义正好倒过来。
  // 这里只数"显著"的家人数，方向交给后端的 sig_summary 说清楚。
  const sigCount = d.entities.filter((e) => e.significant).length
  const worseThanUs = d.entities.filter((e) => e.better).length

  return (
    <div className="pg">
      <div className="pg-filterbar">
        <span className="pg-filterbar-k">类目</span>
        <button className={`pg-fchip ${!category ? 'on' : ''}`} onClick={() => setCategory('')}>
          全部类目
        </button>
        {categories.map((c) => (
          <button key={c} className={`pg-fchip ${category === c ? 'on' : ''}`}
            onClick={() => setCategory(c)}>{c}</button>
        ))}
      </div>

      <div className="pg-kpi-row">
        <Stat label="本店负评率" value={us ? us.neg_pct.toFixed(2) : '—'} unit="%"
          sub={us ? `${nf(us.n)} 条` : ''} tone="bad" />
        <Stat label="同类排名" value={rank || '—'} unit={`/ ${d.entities.length}`}
          sub="按负评率从低到高（越小越好）" />
        {/* 【KPI 说的是"显著差异有几家"，不是"几家比我们差"】
            后端的 sig_summary 已经把方向和显著性写成一个完整句子，
            直接引它，不在这里重新组织语言 —— 同一件事写两遍，
            其中一遍迟早会写反（实际已经反过一次）。 */}
        <Stat label="显著差异" value={sigCount} unit="家"
          sub={d.sig_summary}
          tone={sigCount === 0 ? undefined : (worseThanUs === sigCount ? 'good' : 'warn')} />
        <Stat label="最大维度差距" value={
          d.dimensions.length
            ? Math.max(...d.dimensions.map((x) => x.spread)).toFixed(1)
            : '—'
        } unit="pp" sub="最好与最差之间差多少" tone="warn" />
      </div>

      <Card
        title="负评率横向对比"
        note={<>
          Δ = 对方负评率 − 本店，<b>正数表示对方更差（我们更好）</b>。
          <b>显著</b>指 p&lt;0.05，不显著的差异不应被当成结论。
        </>}
        pad={false}
      >
        <table className="pg-table">
          <thead>
            <tr>
              <th>对象</th>
              <th className="r">条数</th>
              <th className="r">负评率</th>
              <th className="r">均分</th>
              <th className="r">Δ vs 本店</th>
              <th className="r">p 值</th>
              <th>数据性质</th>
            </tr>
          </thead>
          <tbody>
            {d.entities.map((e) => {
              const isUs = e.entity === us?.entity
              return (
                <tr key={e.entity} className={isUs ? 'pg-row-us' : ''}>
                  <td className="strong">{e.entity}</td>
                  <td className="r num dim">{nf(e.n)}</td>
                  <td className="r num">
                    <div className="pg-cell-bar">
                      <span>{e.neg_pct.toFixed(2)}%</span>
                      <Bar pct={e.neg_pct} tone={isUs ? 'bad' : 'warn'} height={4} />
                    </div>
                  </td>
                  <td className="r num">{e.mean_score.toFixed(3)}</td>
                  <td className="r num">
                    {isUs ? <span className="dim">基准</span> : (
                      <span className={e.significant
                        ? (e.better ? 't-good' : 't-bad')
                        : 'dim'}>
                        {pp(e.diff_pp)}
                        {!e.significant && <small className="pg-ns"> 不显著</small>}
                      </span>
                    )}
                  </td>
                  {/* 【p 值要给够位数，但不能显示成 0.0000】
                      后端算的是两比例 z 检验，n=1040 时 z=11.56 的 p
                      在浮点下溢成 0.0。显示 "0.0000" 会被读成
                      "没做检验"或"字段没填"，而实际是"小到测不出来"。
                      这两种含义的差别很大，必须写成 <1e-4 才不含糊。 */}
                  <td className="r num dim">
                    {isUs ? '—' : (
                      e.p_value <= 0 ? '<1e-4'
                        : e.p_value < 1e-4 ? e.p_value.toExponential(1)
                          : e.p_value.toFixed(4)
                    )}
                  </td>
                  <td>
                    <span className={`pg-tag ${e.mode_zh === '种子批次' ? '' : 't-good'}`}>
                      {e.mode_zh}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div className="pg-note-inline">
          <b>数据性质</b>这一列不是装饰：竞品语料由独立 DGP（数据生成过程）模拟生成，
          用于验证方法论，不代表真实抓取。
          {d.note && <> {d.note}</>}
        </div>
      </Card>

      <Card
        title="维度差距：差在哪"
        note={<>每个维度先筛出<b>提到该维度</b>的评论，再看其中的负评率。分母是提及数，不是总评论数。</>}
        pad={false}
      >
        {d.dimensions.length === 0 ? (
          <Empty what="没有维度差距数据" />
        ) : (
          <table className="pg-table">
            <thead>
              <tr>
                <th>维度</th>
                <th>最差</th>
                <th>最好</th>
                <th className="r">最差负评率</th>
                <th className="r">最好负评率</th>
                <th className="r">差距</th>
                <th>各家数值</th>
              </tr>
            </thead>
            <tbody>
              {[...d.dimensions].sort((a, b) => b.spread - a.spread).map((x) => (
                <tr key={x.label}>
                  <td>{x.label}</td>
                  <td className="t-bad">{x.worst}</td>
                  <td className="t-good">{x.best}</td>
                  <td className="r num">{x.max.toFixed(1)}%</td>
                  <td className="r num">{x.min.toFixed(1)}%</td>
                  <td className="r num">
                    <div className="pg-cell-bar">
                      <span>{x.spread.toFixed(1)}</span>
                      <Bar
                        pct={(x.spread / Math.max(...d.dimensions.map((y) => y.spread), 1)) * 100}
                        tone="warn" height={4}
                      />
                    </div>
                  </td>
                  <td>
                    <div className="pg-dimvals">
                      {[...(x.by_entity ?? [])]
                        .sort((a, b) => b.value - a.value)
                        .map((b) => (
                          <span
                            key={b.entity}
                            className={`pg-dimval ${b.is_us ? 'us' : ''}`}
                            title={`${b.entity}：${b.value.toFixed(1)}%`}
                          >
                            {b.is_us ? '本' : b.entity.replace(/^竞品/, '')}
                            <i>{b.value.toFixed(1)}</i>
                          </span>
                        ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* ---- 用词差异 ---- */}
      {/* 【这一块不能"没数据就整块消失"】
          第一版这里写成 `d.contrast.ours?.length ? ... : null`，
          而字段名对不上导致两边都是 undefined，于是卡片被静默跳过 ——
          页面上看不出任何异常，就像这一页本来没有这块内容。
          现在分成三种状态：有数据 / 明确说为什么没有。
          一个"为什么没有"比一个消失的模块有用得多。 */}
      {d.contrast?.ok ? (
        <Card
          title="我们在说什么，他们在说什么"
          note={<>
            左右并排而不是只列「本店独有词」：<b>独有</b>可能只是选品不同，不是经营差异。
            数值是 log₂(本侧占比 / 对侧占比)，正数越大说明这个词越偏向本侧。
          </>}
        >
          <div className="pg-contrast">
            <div className="pg-ct-side ours">
              <div className="pg-ct-h">
                本店
                {d.contrast.scope && <small className="dim">（{d.contrast.scope}）</small>}
              </div>
              {(d.contrast.ours ?? []).slice(0, 12).map((w) => (
                <div className="pg-ct-row" key={w.word}>
                  <span className="pg-ct-w">{w.word}</span>
                  <span className="pg-ct-n num">{nf(w.n)}</span>
                  <span className="pg-ct-lr num dim" title="相对对标的用词偏好对数比">
                    {w.lr.toFixed(2)}
                  </span>
                </div>
              ))}
            </div>
            <div className="pg-ct-side theirs">
              <div className="pg-ct-h">
                对标：{d.contrast.benchmark ?? '—'}
              </div>
              {(d.contrast.theirs ?? []).slice(0, 12).map((w) => (
                <div className="pg-ct-row" key={w.word}>
                  <span className="pg-ct-w">{w.word}</span>
                  <span className="pg-ct-n num">{nf(w.n)}</span>
                  <span className="pg-ct-lr num dim">{w.lr.toFixed(2)}</span>
                </div>
              ))}
            </div>
          </div>
        </Card>
      ) : (
        <Card title="我们在说什么，他们在说什么">
          <Empty what={d.contrast?.reason || '这一组对比没有可用的特征词'} />
        </Card>
      )}

      <div className="pg-foot">
        口径：{d.scope} ·
        {' '}显著性用双侧两比例 z 检验，不是看谁的数字大小 ·
        {' '}竞品侧为模拟语料，仅用于验证方法论
      </div>
    </div>
  )
}