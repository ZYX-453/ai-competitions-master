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
import { useState } from 'react'
import { useFetch } from '../lib/useFetch'
import type { CompetitorData } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, pp } from '../components/PageKit'
import { useTip } from '../components/BiKit'
import { CompMatrix } from '../components/CompMatrix'

export function Competitors({
  categories, category, setCategory,
}: {
  categories: string[]
  category: string
  setCategory: (c: string) => void
}) {
  const [sel, setSel] = useState<string | null>(null)
  const tip1 = useTip()
  const tip3 = useTip()

  /* 【这一页的竞态和别处不同：切类目会换掉整张表】
     连点「服饰 → 生鲜 → 数码」时，若「服饰」的响应最后到，
     屏幕上就是**服饰的竞品数据配着「数码」的筛选高亮** ——
     而且矩阵里那 6 个维度仍然是服饰的，不会报任何错。
     统一走 useFetch 之后靠请求序号保证"只认最后一次"。 */
  const {
    data: d, err, retry,
  } = useFetch<CompetitorData>(
    `/api/pages/competitors${category ? `?category=${encodeURIComponent(category)}` : ''}`,
  )

  if (err) return <Fail msg={err} onRetry={retry} />
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

  /* ---- 模块①负评率点级图 ----
     【为什么是点级图而不是饼图/柱状图】
     5 个对象各一个比率，没有"部分占整体"的语义（饼图会误导读者去
     加总），柱状图则把"本店基准线在哪"这件事丢掉 —— 而这一页
     真正要回答的是"谁离本店有多远、够不够显著"。点 + 基准线
     把这两个问题合成一张图：点的位置给距离，实心/空心给显著性。 */
  const maxNeg = Math.max(...d.entities.map((e) => e.neg_pct), d.us_neg_pct) * 1.12

  /* 【维度矩阵已经接管了「差在哪、差多少」】
     原来这里有一整套 roving-tabindex 游标 + 点线分布条（stripDims /
     maxDim / tabDot / moveStrip），现由 CompMatrix 组件替代：
     矩阵把 6 维 × 5 家铺成格子，行内归一着色，点一处即高亮整行整列，
     键盘焦点直接落在列头上（5 个停靠点，而不是 30 个）。
     维度的排序与极值计算下沉到组件内部，这里不再保留。 */

  /* ---- 模块③蝴蝶图 ----
     【log₂ 比天然是双向的】正数偏向本店、负数偏向对标，
     左右镜像的条形让"方向"变成一眼可读的位置，而不是让读者
     先记住"正数是哪边"。 */
  const ours = (d.contrast.ours ?? []).slice(0, 12)
  const theirs = (d.contrast.theirs ?? []).slice(0, 12)
  /* 【三条必须同时满足，少一条这张图就会骗人】
     ① 宽度用 |lr|。lr = log₂(本店占比 / 对标占比)，两侧符号必然相反：
        本店侧正、对标侧负。直接 w.lr / max 会算出负宽度，CSS 当无效值
        丢弃，右栏 12 行只剩浅色轨道 —— 数据一直在，是条长算成了负数。
     ② 两侧各自归一，不共用一个分母。实测本店侧 lr 落在 3.4–4.7，
        对标侧落在 -9.3 – -10.1，量级差一倍以上。共用一个分母的结果是：
        右栏 12 根条全部接近满长（彼此差不到 3%），左栏全部缩到 3 成，
        **两边都读不出差异**，图就退化成"左右都有东西"。
     ③ 归一后不允许出现 0 长度。lr=0 意味着两边用量完全一样，
        那种词本来就不该进对比表；如果进了，留一道 2% 的最小可见宽度，
        否则空轨道会被误读成"没有数据"。

     【为什么右栏看起来还是一排等长的条 —— 这是数据本身，不是 bug】
     对标侧的词全是「只出现在竞品语料里」的低频词（n=6~39），
     本店出现 0 次，所以 log₂ 比值被分母下限压到 -9.3 ~ -10.1，
     彼此差异只有 0.8 个单位，占满量程的 92%~100%。
     条长如实反映这件事：它们确实是"同等稀薄地只属于对方"。
     为了好看而拉伸到 0–100% 才是撒谎 —— 那会让 n=6 的「主播」
     和 n=39 的「回购」看起来差好几倍，而真实差异只有 log₂ 上的 0.14。
     数字列（11/9/39/7…）才是这一段该读的信息。 */
  const maxOurs = Math.max(1, ...ours.map((w) => Math.abs(w.lr)))
  const maxTheirs = Math.max(1, ...theirs.map((w) => Math.abs(w.lr)))
  const barW = (lr: number, max: number) => `${Math.max((Math.abs(lr) / max) * 100, 2)}%`

  /** p 值的读法文案（三处复用） */
  const pText = (v: number) => v <= 0 ? '<1e-4' : v < 1e-4 ? v.toExponential(1) : v.toFixed(4)

  /** 蝴蝶图浮层：把 log₂ 比翻译成"偏向多少倍"
   *  【用 |lr| 算倍数，方向由 side 决定】
   *  lr 在本店侧为正、对标侧为负，但两侧展示的语义是同一个：
   *  "这一侧比另一侧常用 2^|lr| 倍"。拿带符号的 lr 去 pow(2, -10)
   *  会得到 0.001 倍，读者会以为"几乎不用"，而实际是压倒性常用。 */
  const bflyTip = (w: { word: string; n: number; lr: number }, side: 'ours' | 'theirs') => {
    const mag = Math.abs(w.lr)
    const other = side === 'ours' ? '对标' : '本店'
    return (
      <div>
        <div className="bi-tip-h">{w.word}</div>
        <div className="bi-tip-row">出现 <b>{nf(w.n)}</b> 条</div>
        <div className="bi-tip-row">log₂比 <b>{w.lr.toFixed(2)}</b></div>
        <div className="bi-tip-row dim">
          {mag >= 1
            ? `本侧比${other}常用约 ${Math.pow(2, mag).toFixed(1)} 倍`
            : mag >= 0.4
              ? `本侧比${other}略常用`
              : '两边用量接近'}
        </div>
      </div>
    )
  }

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
        {/* ---- 点级对比图（BI） ---- */}
        <div className="pg-cdot-wrap">
          <div className="pg-cdot" role="group" aria-label="五家负评率点级对比；虚线为本店基准，点击竞品可联动下方图表">
            {[...d.entities].sort((a, b) => b.neg_pct - a.neg_pct).map((e) => {
              const isUs = e.entity === us?.entity
              const dotTip = (
                <div>
                  <div className="bi-tip-h">{e.entity}</div>
                  <div className="bi-tip-row">负评率 <b>{e.neg_pct.toFixed(2)}%</b></div>
                  <div className="bi-tip-row">条数 <b>{nf(e.n)}</b> · 均分 <b>{e.mean_score.toFixed(3)}</b></div>
                  {!isUs && <>
                    <div className="bi-tip-row">Δ vs 本店 <b className={e.significant ? (e.better ? 't-good' : 't-bad') : ''}>
                      {pp(e.diff_pp)}pp</b></div>
                    <div className="bi-tip-row">z = {e.z?.toFixed(2)} · p {pText(e.p_value)}</div>
                    <div className="bi-tip-row dim">{e.significant ? '差异显著（p<0.05）' : '差异不显著，不能当结论'}</div>
                    <div className="bi-tip-go">{sel === e.entity ? '点击取消选中' : '点击选中，联动维度图 ›'}</div>
                  </>}
                </div>
              )
              return (
                <div
                  key={e.entity}
                  className={`pg-cdot-r${isUs ? ' is-us' : ''}${sel === e.entity ? ' is-on' : ''}${isUs ? '' : ' clickable'}`}
                  role={isUs ? undefined : 'button'}
                  tabIndex={isUs ? undefined : 0}
                  aria-pressed={!isUs && sel === e.entity ? 'true' : undefined}
                  aria-label={isUs
                    ? `本店基准：负评率 ${e.neg_pct.toFixed(2)}%，${nf(e.n)} 条`
                    : `${e.entity}，负评率 ${e.neg_pct.toFixed(2)}%，比本店高 ${pp(e.diff_pp)} 个百分点，${e.significant ? '差异显著' : '差异不显著'}。回车选中联动维度图`}
                  onKeyDown={isUs ? undefined : (ev) => {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                      ev.preventDefault()
                      setSel((c) => (c === e.entity ? null : e.entity))
                    }
                  }}
                  onMouseMove={isUs ? undefined : (ev) => tip1.show(ev, dotTip)}
                  onMouseLeave={isUs ? undefined : tip1.hide}
                  onClick={isUs ? undefined : () => setSel((c) => (c === e.entity ? null : e.entity))}
                >
                  <span className="pg-cdot-l">{e.entity}</span>
                  <span className="pg-cdot-t">
                    <i className="pg-cdot-base" style={{ left: `${(d.us_neg_pct / maxNeg) * 100}%` }} />
                    <i
                      className={`pg-cdot-d${e.significant ? ' is-sig' : ''}`}
                      style={{ left: `${(e.neg_pct / maxNeg) * 100}%` }}
                    />
                  </span>
                  <span className="pg-cdot-v num">{e.neg_pct.toFixed(2)}%</span>
                </div>
              )
            })}
            <div className="pg-cdot-x" aria-hidden="true">
              <span>0</span>
              <span style={{ left: `${(d.us_neg_pct / maxNeg) * 100}%` }}>本店 {d.us_neg_pct.toFixed(1)}%</span>
            </div>
          </div>
          <div className="pg-cdot-lg" aria-hidden="true">
            <span className="bi-lg-item"><i className="is-sig-i" />显著（p&lt;0.05）</span>
            <span className="bi-lg-item"><i className="is-ns-i" />不显著</span>
            <span className="bi-lg-item"><i className="is-us-i" />本店基准线</span>
          </div>
          {tip1.el}
        </div>
        <div className="pg-table-wrap">
          <table className="pg-table">
            <thead>
              <tr>
                <th scope="col">对象</th>
                <th scope="col" className="r">条数</th>
                <th scope="col" className="r">负评率</th>
                <th scope="col" className="r">均分</th>
                <th scope="col" className="r">Δ vs 本店</th>
                <th scope="col" className="r">p 值</th>
                <th scope="col">数据性质</th>
              </tr>
            </thead>
            <tbody>
              {d.entities.map((e) => {
                const isUs = e.entity === us?.entity
                return (
                  <tr key={e.entity} className={`${isUs ? 'pg-row-us' : ''}${sel === e.entity ? ' is-sel' : ''}`}
                    aria-current={!isUs && sel === e.entity ? 'true' : undefined}>
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
        </div>
        <div className="pg-note-inline">
          <b>数据性质</b>这一列不是装饰：竞品语料由独立 DGP（数据生成过程）模拟生成，
          用于验证方法论，不代表真实抓取。
          {d.note && <> {d.note}</>}
        </div>
      </Card>

      <Card
        title="维度差距归因"
        note={<>每个维度先筛出<b>提到该维度</b>的评论，再看其中的负评率。分母是提及数，不是总评论数。</>}
        pad={false}
      >
        {d.dimensions.length === 0
          ? <Empty what="没有维度差距数据" />
          : <CompMatrix data={d} sel={sel} setSel={setSel} />}
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
          title="本店与对标竞品的特征词差异"
          note={<>
            左右并排而不是只列「本店独有词」：<b>独有</b>可能只是选品不同，不是经营差异。
            数值是 log₂(本侧占比 / 对侧占比)，正数越大说明这个词越偏向本侧。
          </>}
        >
          <div className="pg-bfly-wrap">
            <div className="pg-bfly-h" aria-hidden="true">
              <span className="t-accent">← 本店更常用</span>
              <span className="dim">条长 = |log₂ 占比比|，词下数字是出现条数</span>
              <span className="t-info">对标更常用 →</span>
            </div>
            <div className="pg-bfly" role="group"
              aria-label={`用词差异蝴蝶图：本店侧 ${ours.length} 个特征词，对标侧 ${theirs.length} 个`}>
              <div className="pg-bfly-side ours">
                {ours.map((w) => (
                  <div className="pg-bfly-row" key={w.word}
                    onMouseMove={(ev) => tip3.show(ev, bflyTip(w, 'ours'), true)}
                    onMouseLeave={tip3.hide}>
                    <span className="pg-bfly-w">{w.word}</span>
                    <span className="pg-bfly-bar"><i style={{ width: barW(w.lr, maxOurs) }} /></span>
                    <span className="pg-bfly-n num">{nf(w.n)}</span>
                  </div>
                ))}
              </div>
              <div className="pg-bfly-axis" aria-hidden="true" />
              <div className="pg-bfly-side theirs">
                {theirs.map((w) => (
                  <div className="pg-bfly-row" key={w.word}
                    onMouseMove={(ev) => tip3.show(ev, bflyTip(w, 'theirs'), true)}
                    onMouseLeave={tip3.hide}>
                    <span className="pg-bfly-n num">{nf(w.n)}</span>
                    <span className="pg-bfly-bar"><i style={{ width: barW(w.lr, maxTheirs) }} /></span>
                    <span className="pg-bfly-w">{w.word}</span>
                  </div>
                ))}
              </div>
            </div>
            {tip3.el}
          </div>
        </Card>
      ) : (
        <Card title="本店与对标竞品的特征词差异">
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