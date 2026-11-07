/* ===========================================================================
 * 竞品维度矩阵（CompMatrix）
 *
 * 【这一版要解决什么】
 * 旧版是 6 行「点线分布条」：每行一根轴，5 个点散落其上，右侧给差距 pp。
 * 问题有三个，都是结构性的，不是配色能救的：
 *   ① 行与行不可比。每行按自己的 max 归一，读者无法横向看出
 *      "尺码版型差 58.6pp、客服售后差 24.5pp" 到底是什么量级的差别，
 *      只能逐行去读右侧那个小字。
 *   ② 看不出"谁在哪一维最差"。五家的点叠在同一根轴上，
 *      密集处直接重叠；而这一页真正要回答的正是"我们对标时，
 *      最该防的是谁、防哪一维"。
 *   ③ 占 6 行 × 40px = 240px 高度，却只承载 30 个数值。
 *      信息密度低到需要一张表来补，而表就在上面 —— 等于同一份数据
 *      画了两遍，还画得不如表清楚。
 *
 * 【这版的形态：维度 × 竞品 矩阵】
 *   行 = 6 个维度（按差距从大到小），列 = 5 家（本店固定第一列）。
 *   格子 = 该家在该维度的负评率，**行内归一着色**：
 *     同一维度里最红的是最差、最浅的是最好。行内归一而不是全表归一，
 *     因为维度之间的绝对水平本来就不可比（尺码版型全行业都高、
 *     物流全行业都低），全表归一会让整张图变成"尺码版型一列红"，
 *     而读者会误读成"尺码版型这一维度所有家都有问题"。
 *   右侧另给一行"差距 pp"，把行内的 极差 单独拎出来 —— 它回答的是
 *   "这一维值不值得管"，和"谁最差"是两个问题。
 *
 * 【交互】
 *   · 悬停任一格 → 整行整列高亮（十字），其余降到 25% —— 读单点时
 *     不必来回扫视行列标题；浮层给出该格数值、行内排名、与最差值差多少。
 *   · 点击列头或单元格 → 锁定该竞品，全局联动（下面的特征词差异卡
 *     会用 sel 过滤）。再次点击取消。
 *   · 键盘：列头可 Tab 聚焦，左右键换列、回车锁定。
 * =========================================================================== */
import { useMemo, useState } from 'react'
import type { CompetitorData } from '../lib/pages'
import { nf } from '../components/PageKit'

/** 行内归一的着色：0 = 最好（浅），1 = 最差（深红）。
 *  【为什么用 HSL 拼色而不是几档 CSS 类】
 *  每行只有 5 个值，分档会丢掉"差 1pp 还是差 20pp"的连续感；
 *  而且 6 行 × 5 列 = 30 个值分档后会出现同一档里差异极大、
 *  相邻档差异极小的情况，色阶反而误导。连续插值没有这个问题。
 *  色相锁在玫红一段（350→352），只动饱和度与明度，
 *  避免"浅色偏黄、深色偏紫"这种色相漂移让读者以为类别不同。 */
function cellTone(t: number, alpha = 1) {
  /* 浅端 #F4EDF0（近白，带一丝本页主色的紫调）→ 深端 #96394A。
     用两个端点做 sRGB 线性插值，比 HSL 插值稳：HSL 在明度两端
     会经过一段灰，看起来像"脏"，而两端直接插值不会。

     【浅端为什么不再往白里走】
     下限原本是 (250,247,248)：本店在所有维度都最优，
     于是每一行本店格都拿到 t=0 的极限浅色，与白色卡面
     几乎完全同色 —— 六行里最左一列看起来像"六格空数据"，
     而实际上它们承载着这一页最重要的基准值。
     现在把浅端压到 (238,228,232)，并给本店列单独一道
     底色与左侧基准线（见 .cmx-cell.is-us），让"基准"这个身份
     不靠色阶深浅来表达。 */
  const from = [238, 228, 232]
  const to = [150, 57, 74]
  const k = Math.min(1, Math.max(0, t))
  const c = from.map((v, i) => Math.round(v + (to[i] - v) * k))
  return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`
}

/** 格子里的文字颜色：底色深到一定程度就翻白，保证对比度。
 *  阈值 0.52 是实测：低于它时 #2A2533 的黑字对比度仍在 4.5:1 以上。 */
const inkOn = (t: number) => (t > 0.52 ? '#FFF6F7' : '#2A2533')

export function CompMatrix({
  data, sel, setSel,
}: {
  data: CompetitorData
  sel: string | null
  setSel: (v: string | null) => void
}) {
  const [hover, setHover] = useState<{ r: string; c: string } | null>(null)

  const dims = useMemo(
    () => [...data.dimensions].sort((a, b) => b.spread - a.spread),
    [data.dimensions],
  )
  /* 列顺序：本店在最左（基准），其余按负评率从低到高 ——
     和上面的点级图、表格保持同一顺序，避免同一页三处排序不同。 */
  const cols = useMemo(() => {
    const us = data.entities.filter((e) => e.entity.startsWith('本店'))
    const rest = data.entities.filter((e) => !e.entity.startsWith('本店'))
      .sort((a, b) => a.neg_pct - b.neg_pct)
    return [...us, ...rest]
  }, [data.entities])

  if (!dims.length || !cols.length) return null

  /* 行内极值。用于着色与"最差/最好"标记。
     全为 0 的行（理论上不会有，但缺数据时会）单独跳过，避免除零。 */
  const rowStat = dims.map((d) => {
    const vals = d.by_entity.map((b) => b.value)
    const max = Math.max(...vals, 0)
    const min = Math.min(...vals, max)
    /* 【最差用"哪一家"来判，不用 v === max 来判】
       浮点相等在这里不可靠：30 个值里一旦出现两个十分接近的数
       （如 62.5 与 62.499999），只有一个能通过 ===，
       而读者看到的是两个都显示 62.5 —— 于是"最差"标签随机落在其中一格。
       改成先用 reduce 定位唯一的最差家，比较时只比 entity。
       并列确实存在时（两格数值完全相同）只标第一个 —— 这比
       两个都标更少见也更容易接受，且不会误导。 */
    let worstEnt = ''
    d.by_entity.forEach((b) => {
      if (!worstEnt || b.value > (d.by_entity.find((x) => x.entity === worstEnt)?.value ?? -1)) {
        worstEnt = b.entity
      }
    })
    return { max, min, span: max - min, worstEnt }
  })

  const cellVal = (dimLabel: string, ent: string) => {
    const d = dims.find((x) => x.label === dimLabel)
    return d?.by_entity.find((b) => b.entity === ent)
  }

  return (
    <div className="cmx-wrap" role="group"
      aria-label={`竞品维度矩阵：${dims.length} 个维度 × ${cols.length} 家（本店为首列基准）。格子颜色在同一行内比较，越深表示该维度负评率越高。行末给出该维度的极差。`}>
      <div className="cmx" role="table"
        aria-label="维度 × 竞品负评率矩阵。行内归一着色，越深表示该维度内负评率越高。">
        {/* ---- 表头 ---- */}
        <div className="cmx-hrow" role="row">
          <div className="cmx-hcell cmx-hdim" role="columnheader">维度</div>
          {cols.map((c) => {
            const isUs = c.entity.startsWith('本店')
            const on = sel === c.entity
            return (
              <div key={c.entity} role="columnheader"
                className={`cmx-hcell cmx-hcol${isUs ? ' is-us' : ''}${on ? ' is-on' : ''}`}
                tabIndex={isUs ? undefined : 0}
                aria-pressed={!isUs ? on : undefined}
                aria-label={`${c.entity}，整体负评率 ${c.neg_pct.toFixed(2)}%${isUs ? '（本店基准）' : '，回车锁定该竞品'}`}
                onClick={isUs ? undefined : () => setSel(on ? null : c.entity)}
                onKeyDown={isUs ? undefined : (ev) => {
                  if (ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault()
                    setSel(on ? null : c.entity)
                  }
                }}
                onMouseEnter={() => setHover(null)}
                title={c.entity}>
                <span className="cmx-hcol-n">{c.entity}</span>
                <span className="cmx-hcol-v num">{c.neg_pct.toFixed(1)}%</span>
                {isUs && <span className="cmx-hcol-tag">基准</span>}
              </div>
            )
          })}
          <div className="cmx-hcell cmx-hgap" role="columnheader" title="该维度内最好与最差的差距">极差</div>
        </div>

        {/* ---- 数据行 ---- */}
        {dims.map((d, ri) => {
          const st = rowStat[ri]
          const rowOn = hover?.r === d.label
          return (
            <div key={d.label} role="row"
              className={`cmx-row${rowOn ? ' is-rowon' : ''}`}
              onMouseLeave={() => setHover(null)}>
              <div className="cmx-dcell" role="rowheader">
                <span className="cmx-dname">{d.label}</span>
                <span className="cmx-dbar" aria-hidden="true">
                  <i style={{ width: `${Math.max(4, (st.span / 70) * 100)}%` }} />
                </span>
              </div>
              {cols.map((c) => {
                const b = cellVal(d.label, c.entity)
                const v = b?.value ?? 0
                const t = st.span > 0 ? (v - st.min) / st.span : 0
                const isUs = c.entity.startsWith('本店')
                const colOn = hover?.c === c.entity
                const on = sel === c.entity
                const isWorst = c.entity === st.worstEnt && !isUs
                const dimmed = (hover && hover.r !== d.label && hover.c !== c.entity)
                  || (!!sel && !isUs && !on)
                return (
                  <div key={c.entity} role="cell"
                    className={`cmx-cell${isUs ? ' is-us' : ''}${on ? ' is-on' : ''}${dimmed ? ' is-dim' : ''}${isWorst ? ' is-worst' : ''}${colOn ? ' is-colon' : ''}`}
                    style={{ background: cellTone(t) }}
                    onMouseEnter={() => setHover({ r: d.label, c: c.entity })}
                    onClick={isUs ? undefined : () => setSel(on ? null : c.entity)}
                    title={`${c.entity} · ${d.label}：${v.toFixed(2)}%`}>
                    <span className="cmx-v num" style={{ color: inkOn(t) }}>
                      {v.toFixed(1)}
                    </span>
                    {isWorst && <span className="cmx-worst" style={{ color: inkOn(t) }}>最差</span>}
                  </div>
                )
              })}
              <div className="cmx-cell cmx-gap" role="cell">
                <span className="num">{st.span.toFixed(1)}</span>
                <small>pp</small>
              </div>
            </div>
          )
        })}
      </div>

      {/* ---- 浮层：跟随十字高亮给出该格的定位解释 ---- */}
      {hover && (() => {
        const d = dims.find((x) => x.label === hover.r)
        const b = d?.by_entity.find((x) => x.entity === hover.c)
        if (!d || !b) return null
        const st = rowStat[dims.indexOf(d)]
        /* 【排名按"从好到差"算，不是按数值升序就该说"第1"】
           升序第 1 = 负评率最低 = 最好。但读者看到"第 1"会默认
           是"最差的那个"，尤其在偏差语境里。所以文案明确写
           "从好到差第 N"，并且 N=1 时补一句"本维最好"。 */
        const sorted = [...d.by_entity].sort((a, b2) => a.value - b2.value)
        const rank = sorted.findIndex((x) => x.entity === hover.c) + 1
        const isUs = hover.c.startsWith('本店')
        const usVal = d.by_entity.find((x) => x.is_us)?.value ?? 0
        return (
          <div className="cmx-tip">
            <div className="cmx-tip-h">{hover.r} · {hover.c}</div>
            <div className="cmx-tip-row">
              负评率 <b>{b.value.toFixed(2)}%</b>
              <span className="dim">（分母为提及该维度的评论数）</span>
            </div>
            <div className="cmx-tip-row">
              从好到差第 <b>{rank}</b> / {sorted.length}
              <span className="dim">
                {isUs ? '　本店基准'
                  : rank === 1 ? '　本维最好'
                    : `　比本店高 ${(b.value - usVal).toFixed(1)}pp`}
              </span>
            </div>
            <div className="cmx-tip-row dim">
              本维度极差 {st.span.toFixed(1)}pp（{st.min.toFixed(1)} 到 {st.max.toFixed(1)}）
            </div>
          </div>
        )
      })()}

      <div className="cmx-legend">
        <span className="cmx-lg-t">行内归一：</span>
        <span className="cmx-lg-grad" aria-hidden="true" />
        <span className="cmx-lg-e">该维最好</span>
        <span className="cmx-lg-sp" />
        <span className="cmx-lg-e">该维最差</span>
        <span className="cmx-lg-note">
          颜色只在同一行内可比；跨行看深浅没有意义（各维度绝对水平不同）。
        </span>
      </div>
    </div>
  )
}