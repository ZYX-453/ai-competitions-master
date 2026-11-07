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
 * 【v4.8：两块从"两张静态表"改成一块联动的看板】
 * 原来上面是表、下面是矩阵，各看各的。读者想知道"材质做工问题在哪个类目最重"，
 * 得自己在 4×9 的矩阵里找那一列 —— 这一步是眼睛在替程序做连接。
 * 现在有了交叉选择：选中一个原因，矩阵整列跟着亮；点矩阵里的一格，
 * 整页范围切到那个类目、表格里那一行跟着亮。
 * **联动的价值不在动效，在没有名字的那句话变成看得见的那一格。**
 *
 * 【"可再售"这一列为什么必须出现】
 * 同样 10 万损失，可再售率高的那些还能挽回一部分货值，
 * 可再售率接近 0 的是纯沉没。不带这一列，"值不值得救"就没法判断。
 * 但注意别把它读成质检结论——它是账面口径（restock_loss_rate），
 * 不是"这批货还能卖"的实际判定。这句话写在页面上了。
 * =========================================================================== */
import { useEffect, useMemo, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { getJson } from '../lib/sse'
import type { MatrixCell, ReasonsData } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, money } from '../components/PageKit'
import { Segmented, useTip } from '../components/BiKit'

type SortKey = 'n' | 'loss' | 'loss_per'
/** 矩阵的两个口径：占该类目退货条数 / 占该类目损失金额 */
type Metric = 'share' | 'loss_share'

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
  const [sortBy, setSortBy] = useState<SortKey>('loss')
  /** 【升序也要给】原来点表头只有"降序"一种结果，
      想看"哪个原因最不值得管"就得自己从最后一行往上看 —— 那不是排序，那是倒着读。 */
  const [sortDir, setSortDir] = useState<'desc' | 'asc'>('desc')
  const [metric, setMetric] = useState<Metric>('share')
  /** 交叉选择：一个原因代码 + 一个类目。表里点行只给 code（cat 取当前页面范围）。 */
  const [pick, setPick] = useState<{ code: string; cat: string } | null>(null)
  /** 悬停/聚焦的临时十字线，优先级高于 pick，但不覆盖它 */
  const [hover, setHover] = useState<{ code: string; cat: string } | null>(null)
  /** 矩阵的 roving tabindex 游标（整块只留 1 个 Tab 停靠点） */
  const [cur, setCur] = useState<{ r: number; c: number } | null>(null)
  const gridRef = useRef<HTMLTableElement>(null)
  const { show, hide, el: tipEl } = useTip()

  useEffect(() => {
    setErr('')
    const q = category ? `?category=${encodeURIComponent(category)}` : ''
    getJson<ReasonsData>(`/api/pages/reasons${q}`)
      .then(setD)
      .catch((e) => setErr(String(e)))
  }, [category, tick])

  /* 【hooks 全部放在早退之前】
     `if (!d) return <Loading/>` 之后再写 useMemo，就是"这次渲染 3 个 hook、
     下次 5 个"——React 会直接抛 "Rendered more hooks than during the previous render"，
     而且只在数据到齐那一刻炸，看起来像后端的问题。 */
  const rows = useMemo(() => {
    const s = [...(d?.rows ?? [])]
    s.sort((a, b) => (sortDir === 'desc' ? b[sortBy] - a[sortBy] : a[sortBy] - b[sortBy]))
    return s
  }, [d, sortBy, sortDir])

  /** 36 格按 (类目|代码) 建索引：直接 find 是 36×36 次比较，虽然不多，
      但这段在悬停时每帧都跑，建一次表更省。 */
  const cellMap = useMemo(() => {
    const m = new Map<string, MatrixCell>()
    for (const c of d?.matrix.cells ?? []) m.set(`${c.category}|${c.code}`, c)
    return m
  }, [d])

  if (err) return <Fail msg={err} onRetry={() => setTick((x) => x + 1)} />
  if (!d) return <Loading label="汇总 9 类退货原因" />

  /* 【可排序表头必须同时满足四件事，少一件就是半成品】
     ① 看得懂：class sortable 给光标 + hover 变色
     ② 点得动：onClick
     ③ Tab 得到：tabIndex=0 + Enter/空格同效
        —— 缺这条，键盘用户永远改不了排序，而鼠标用户一切正常，
           所以这个缺陷不会被任何人主动发现
     ④ 读屏说得出：aria-sort 三态之一
        —— 只给当前列设、其他列不设，读屏会把"没有属性"和
           "不可排序"混为一谈，用户听到列名后不知道这列能不能点
     v4.8 起"点同一列"改成切换升降序，所以三态真的都用上了：
     ascending / descending 给当前列，none 给其余列。*/
  const activate = (key: SortKey) => {
    if (sortBy === key) setSortDir((x) => (x === 'desc' ? 'asc' : 'desc'))
    else {
      setSortBy(key)
      setSortDir('desc')
    }
  }
  const sortProps = (key: SortKey) => ({
    className: 'r sortable',
    tabIndex: 0,
    'aria-sort': (sortBy === key
      ? (sortDir === 'desc' ? 'descending' : 'ascending')
      : 'none') as 'ascending' | 'descending' | 'none',
    onClick: () => activate(key),
    onKeyDown: (e: KeyboardEvent<HTMLTableCellElement>) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        activate(key)
      }
    },
  })
  const sortMark = (key: SortKey) => (sortBy === key ? (sortDir === 'desc' ? ' ↓' : ' ↑') : '')

  const totalLoss = money(d.total_loss)
  const maxLoss = Math.max(...d.rows.map((r) => r.loss), 1)
  // 可挽回额度与占比都由后端给（前端不拿绝对量现除）
  const rc = money(d.recoverable_total)

  // 【只看损失最大的那条，给出一个能直接动手的判断】
  const top = [...d.rows].sort((a, b) => b.loss - a.loss)[0]

  // ---- 交叉选择：当前真正在强调的 (类目, 原因) ----
  const active = hover ?? pick
  const isCol = (code: string) => !!active && active.code === code
  const isRow = (cat: string) => !!active && active.cat === cat

  const pickReason = (code: string, cat: string) => {
    setPick(pick && pick.code === code && pick.cat === cat ? null : { code, cat })
  }

  /* 【矩阵格的键盘可达：roving tabindex】
     36 格如果每格都 tabIndex=0，Tab 序列就多出 36 次停顿，
     键盘用户想走到下面的内容得按到手软 —— 可达性没解决，可用性反而更差。
     整块只留一个停靠点，进去之后用方向键在同一平面内移动（WAI-ARIA grid 的常规做法）。
     本矩阵没有空格子（后端把没有的组合补成真实的 0），所以不用跳过。 */
  const cats = d.matrix.categories
  const codes = d.matrix.cause_codes
  const home = { r: 0, c: 0 }
  const curAlive = !!cur && cur.r >= 0 && cur.r < cats.length && cur.c >= 0 && cur.c < codes.length
  const tabCell = curAlive ? cur! : home

  const moveFocus = (e: KeyboardEvent<HTMLTableCellElement>, ri: number, ci: number) => {
    const dir: Record<string, [number, number]> = {
      ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0],
    }
    const dd = dir[e.key]
    if (!dd) return false
    e.preventDefault()
    const nr = ri + dd[0]
    const nc = ci + dd[1]
    // 越界停在原地（不 wrap）：二维矩阵里从最右列跳回最左列会让人瞬间失去方位感
    if (nr < 0 || nr >= cats.length || nc < 0 || nc >= codes.length) return true
    const t = gridRef.current?.querySelector<HTMLElement>(`[data-r="${nr}"][data-c="${nc}"]`)
    if (t) {
      setCur({ r: nr, c: nc })
      t.focus()
    }
    return true
  }

  const drill = (cat: string, code: string) => {
    pickReason(code, cat)
    // 整页范围跟着切：KPI 和上表一起变成这个类目的口径。
    // 不切的话，"点了格子但上面的数字没动"会让人以为点击没生效。
    if (cat !== category) setCategory(cat)
  }

  const valOf = (c: MatrixCell) => (metric === 'share' ? c.share : c.loss_share)
  const maxOf = (name: string) => {
    const v = metric === 'share'
      ? d.matrix.col_max[name]
      : d.matrix.col_max_loss[name]
    return v && v > 0 ? v : 1
  }

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
          sub={`占损失 ${d.recoverable_pct.toFixed(1)}%`}
          title="按各原因的 is_resellable 占比折算。这是账面口径，不等于「这批货真的还能卖」。" />
        <Stat label="损失最大的原因" value={top?.l2 ?? '—'}
          sub={top ? `${money(top.loss).v}${money(top.loss).u} · 占 ${top.loss_share.toFixed(1)}%` : ''}
          tone="warn" />
      </div>

      <Card
        title="每个原因值不值得管"
        note={<>点表头换排序，<b>再点一次切升序</b>。选中一行，下面的矩阵会把这一列点亮。</>}
        pad={false}
      >
        <div className="pg-table-wrap">
          <table className="pg-table">
            <thead>
              <tr>
                <th scope="col">原因</th>
                <th scope="col">层级</th>
                <th scope="col" {...sortProps('n')}>
                  条数 {sortMark('n')}
                </th>
                <th scope="col" className="r">占条数</th>
                <th scope="col" {...sortProps('loss')}>
                  损失 {sortMark('loss')}
                </th>
                <th scope="col" className="r">占损失</th>
                <th scope="col" {...sortProps('loss_per')}>
                  损失/条 {sortMark('loss_per')}
                </th>
                <th scope="col" className="r">可再售率</th>
                <th scope="col" className="r">可挽回</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const on = pick?.code === r.code && pick?.cat === category
                return (
                  <tr
                    key={r.code}
                    /* 【可点行必须 Tab 得到 + 回车能用】
                       <tr> 不在浏览器的默认可聚焦列表里：鼠标点得到、键盘永远够不着，
                       而且这类缺陷不报错、不变形。class 走 clickable（给光标和悬停反馈），
                       不挂 role="button" —— 那会让读屏丢掉行语义，表头与单元格的对应关系随之断裂。 */
                    className={`clickable${on ? ' is-on' : ''}${r.is_void ? ' is-void' : ''}`}
                    aria-current={on ? 'true' : undefined}
                    tabIndex={0}
                    onClick={() => pickReason(r.code, category)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        pickReason(r.code, category)
                      }
                    }}
                  >
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
                )
              })}
            </tbody>
          </table>
        </div>
        <div className="pg-note-inline">
          <b>可再售率</b>是账面口径（退货入库时按品相折价后的残值），不是质检结论。
          它回答"这批货还能挽回多少账面价值"，不回答"这批货还能不能卖"。
          {/* 【两列的关系要写出来，不能让读者自己反推】
              并排放"率"和"额"是常规做法，但读者看到 5.8% 和 7.4万 时，
              第一反应是"这是两个指标还是一个"——不点破就得多算一遍。
              顺带这也是对账锚点：9 个原因的可挽回加起来 = 页顶 KPI 的 192.1 万。 */}
          {" "}<b>可挽回 = 损失 × 可再售率</b>，是同一件事的金额表达；
          表里各行加起来正好是页顶的"其中可挽回"。
          {/* 【这一句不是补充说明，是在纠一个会被读反的数】
              未归类那行的可再售率 72.4% 是全表最高。横向扫这一列的人
              会自然得出"未归类最值得先管"，而它连"为什么被退"都不知道。
              层级列已经写了「数据缺口」，这里再把话说完：
              「数据缺口」跟其余 8 个「层级」不是同一个维度上的并列项。 */}
          {" "}末行的层级是<b>「数据缺口」</b>而非某个环节：它不是第 9 个原因，
          是没归上原因的退货。它的可再售率最高不代表最值得管 ——
          那类要先做归因，才有得管。
        </div>
      </Card>

      <Card
        title="类目 × 原因 热力矩阵"
        right={
          <Segmented<Metric>
            value={metric}
            onChange={setMetric}
            options={[
              { key: 'share', label: '占条数' },
              { key: 'loss_share', label: '占损失' },
            ]}
          />
        }
        note={
          <>
            格子值是<b>该原因占这个类目{"条数"}</b>的比例（切到「占损失」则是占该类目损失额）。
            用占比而不是绝对条数，是因为类目体量差 3 倍，绝对数会让所有格子都偏向大目。
            {/* 【矩阵不跟随范围筛选】它唯一的用途就是横向比类目，
                跟着筛就只剩一行，等于把表拆了。但这会造成一个误会：
                页顶写着"范围：数码"、底下却还是四行，看着像筛选没生效。
                所以必须在这里明说它固定是全类目视角。 */}
            这张矩阵固定是<b>四个类目的横向对比</b>，不随上面的范围筛选变；
            <b>点一格</b>才把整页范围切到那个类目。
          </>
        }
        pad={false}
      >
        {d.matrix.categories.length === 0 ? (
          <Empty what="没有矩阵数据" />
        ) : (
          <div className="pg-heat-wrap">
            <table className="pg-table pg-heat" ref={gridRef}>
              <thead>
                <tr>
                  <th scope="col" className="pg-heat-corner">类目 \ 原因</th>
                  {d.matrix.causes.map((c, ci) => (
                    <th
                      scope="col"
                      key={c}
                      className={`r pg-heat-th${isCol(codes[ci]) ? ' is-col' : ''}`}
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.matrix.categories.map((cat, ri) => {
                  const tot = d.matrix.cat_totals[cat]
                  return (
                    <tr key={cat} className={isRow(cat) ? 'is-row' : undefined}>
                      <td className="strong pg-heat-cat">
                        {cat}
                        {tot && (
                          <span className="pg-heat-sub">{nf(tot.n)} 条</span>
                        )}
                      </td>
                      {d.matrix.causes.map((cname, ci) => {
                        const code = codes[ci]
                        const c = cellMap.get(`${cat}|${code}`)
                        if (!c) {
                          /* 后端已经把不存在的组合补成真实的 0，走到这里说明字段对不上。
                             宁可印一个明显的占位，也不要渲染成空白 —— 空白会和"没渲染"同形。 */
                          return <td key={code} className="r num pg-heat-td">—</td>
                        }
                        const v = valOf(c)
                        // 【用相对本列最大值的强度着色】
                        // 绝对阈值会让某些列整列深、另一些列整列浅，看不出类目间的相对差异。
                        // 两个口径各有一套列最大值：换口径时若不重新归一，颜色深浅会跟数字对不上。
                        const a = Math.min(1, v / maxOf(cname))
                        /* 【压暗只在悬停时用，锁定状态不用】
                           第一版把"选中"也做成压暗其余格子。看截图才发现问题：
                           这一页的颜色通道**已经在编码数值大小**了，
                           把一格压到 .38 之后，一个被压暗的 27% 看起来
                           和一个正常的 8% 一模一样 —— 两个通道打架，
                           读者反而分不清"这块是暗的"到底是数值小还是没选中。
                           所以：锁定态只用**不改颜色**的信号（列头点亮 + 行标签竖条 + 选中格描边），
                           压暗只留给悬停那一瞬间 —— 那时浮层正把数字摊开，
                           读者不需要再从颜色深浅读数。 */
                        const dim = !!hover && !isCol(code) && !isRow(cat)
                        const isTab = tabCell.r === ri && tabCell.c === ci
                        const money0 = money(c.loss)
                        return (
                          <td
                            key={code}
                            data-r={ri}
                            data-c={ci}
                            /* 【为什么不挂 role="button"】
                               这是真表格里的一格，读屏靠"行头 + 列头"两个方向定位它。
                               挂 role="button" 会把 td 的单元格语义整个换掉，
                               表头与格子的对应关系随之断裂 —— 补可达性不该毁语义。
                               所以只加 tabIndex + 键盘处理，让它在保留单元格语义的
                               前提下可聚焦；动作提示写进 aria-label 的最后一句。 */
                            /* 读屏读的是这一格的**结论**，不是"格子"这个形状。
                               视觉上占比 + 两个绝对量靠浮层折叠着，键盘和读屏没有浮层，
                               所以同样的内容必须进可访问名。 */
                            aria-label={
                              `${cat}，${c.name}：占本类目退货 ${c.share.toFixed(2)}%，`
                              + `${nf(c.n)} 条，损失 ${money0.v}${money0.u}，`
                              + `占本类目损失 ${c.loss_share.toFixed(2)}%。`
                              + `回车把整页范围切到${cat}`
                            }
                            tabIndex={isTab ? 0 : -1}
                            className={'r num pg-heat-td'
                              + (dim ? ' is-dim' : '')
                              + (isCol(code) && isRow(cat) ? ' is-pick' : '')}
                            onFocus={() => setCur({ r: ri, c: ci })}
                            onMouseEnter={(e) => {
                              setHover({ code, cat })
                              /* 浮层里的数字全部来自后端下发的 cells，
                                 前端只负责排版 —— 这里一旦自己乘一个 100，
                                 同一个格子就会有两个可能对不上的值。 */
                              show(e, (
                                <>
                                  <div className="bi-tip-h">
                                    {cat} · {c.name}
                                    <span className="bi-tip-code">{code}</span>
                                  </div>
                                  <div className="bi-tip-row">
                                    <span>占本类目退货</span>
                                    <b>{c.share.toFixed(2)}%</b>
                                  </div>
                                  <div className="bi-tip-row">
                                    <span>退货条数</span>
                                    <b>{nf(c.n)}</b>
                                  </div>
                                  <div className="bi-tip-row">
                                    <span>占本类目损失</span>
                                    <b>{c.loss_share.toFixed(2)}%</b>
                                  </div>
                                  <div className="bi-tip-row">
                                    <span>损失金额</span>
                                    <b>{money0.v}{money0.u}</b>
                                  </div>
                                  <div className="bi-tip-go">点击把整页切到「{cat}」›</div>
                                </>
                              ))
                            }}
                            onMouseLeave={() => { setHover(null); hide() }}
                            onBlur={() => { setHover(null); hide() }}
                            onKeyDown={(e) => {
                              if (moveFocus(e, ri, ci)) return
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault()
                                drill(cat, code)
                              }
                            }}
                            onClick={() => drill(cat, code)}
                          >
                            <span
                              className={`pg-heat-fill${v > 0 ? '' : ' is-zero'}`}
                              style={{
                                background: v > 0
                                  ? `rgba(194,102,99,${0.06 + a * 0.52})`
                                  : 'transparent',
                              }}
                            >
                              {/* 【0 也要印出来】
                                  原来 0 渲染成空白，于是"这一类目没有这种退货"和
                                  "这个格子没算出来 / 没渲染"长得完全一样 ——
                                  矩阵上出现几个洞，读者只能猜。
                                  0 是一次真实测量（该类目下这类退货为 0），
                                  印成 0 才是准确的说法；用 .is-zero 压暗，
                                  不让它跟真有大值的格子抢注意力。 */}
                              {v > 0 ? v.toFixed(1) : '0'}
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
              写 <b>0</b> 表示这一类目下没有这种原因的退货，不是缺数。
              {pick && (
                <>
                  {" "}当前锁定：<b>{cellMap.get(`${pick.cat}|${pick.code}`)?.name ?? pick.code}</b>
                  {pick.cat ? ` · ${pick.cat}` : '（全部类目）'}。
                  <button className="pg-linkbtn" onClick={() => setPick(null)}>取消</button>
                </>
              )}
            </div>
          </div>
        )}
      </Card>
      {tipEl}
    </div>
  )
}
