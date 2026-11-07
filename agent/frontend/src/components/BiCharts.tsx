/* ===========================================================================
 * 新图表：瀑布 / 环形 / 发散条 / 象限散点
 *
 * 【为什么单独一个文件，不塞进 BiKit】
 * BiKit 是"通用 BI 控件"（切指标、排序、热力矩阵），被多个页面共用。
 * 这里四个是归因页专用的形状：瀑布图只对"可加分解"成立，
 * 象限图只对"两维+一个规模"成立。混进去会让 BiKit 的边界变糊，
 * 以后想复用还得先判断"这个组件其实只给一个页面用"。
 *
 * 【为什么除环形图之外都用 HTML+CSS，不用 SVG】
 * 现有折线图用 `preserveAspectRatio="none"`，好处是宽度自适应、
 * 坏处是**文字会被横向拉伸**（容器 1122px / viewBox 1000 → 1.12 倍）。
 * 折线图文字少、字号小，看不出来；但瀑布图每根柱子下方都有中文标签、
 * 上方都有数值，拉伸 12% 会被看出来。
 *
 * 更重要的原因：HTML 元素**天然可聚焦、可挂 aria-label**。
 * SVG 里的 <g> 默认进不了 Tab 序列（折线图当初就踩过这个坑）。
 * 象限图的每个点、瀑布图的每根柱都要能键盘到达，
 * 用 <button> 做比给 SVG 补 role/tabIndex 更省事，也更可靠。
 *
 * 环形图是唯一例外：它需要画弧，CSS 画不出真正的圆弧（conic-gradient
 * 有锯齿且不能留缝），所以用 SVG，但用 `xMidYMid meet` 保证圆不变形。
 * =========================================================================== */
import { useMemo, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { useTip } from './BiKit'

/* ------------------------------------------------------------- 瀑布图 */

export interface WaStep {
  key: string
  label: string
  /** 已算好的带符号数值（单位由调用方决定，通常是 pp） */
  value: number
  /** 总计柱：从 0 起画，不接在上一步的水位上 */
  isTotal?: boolean
  /** 颜色语义。不传则按正负判断（负 = 下降 = 改善） */
  tone?: 'bad' | 'good'
  sub?: string
  tip?: ReactNode
}

/**
 * 瀑布图：把"一个总量"拆成"谁贡献了多少"。
 *
 * 【为什么这张图比表格和横条都合适】
 * 表格里 `贡献` 那一列是 4 个各自独立的数字，
 * 读者看不出"它们加起来正好等于全店 Δpp"这件最关键的事 ——
 * 而这恰恰是"分解"的全部意义。横条的条长是按绝对值归一化的，
 * 连"加得起来"的暗示都没有。
 *
 * 瀑布图的每一节都是一次**加减**，末尾那根总柱是结果。
 * 图上天生就写着"这些相加 = 那个"，不需要文字解释。
 *
 * 【为什么必须有一个"结构效应"节】
 * 实测 week=49：四个类目贡献合计 -1.69pp，而 delta_pp 是 -1.84pp。
 * 差的 0.15pp 不是误差，是订单在类目之间迁移造成的影响
 * （订单从高退货率类目流向低退货率类目）。
 * 少了这一节，四根条的长度加起来对不上最右边那根 —— 一眼假。
 * 这个值由后端算好（pages.py 的 structure_effect_pp），
 * 前端只负责把它画成一根，不自己减。
 */
export function Waterfall({
  steps, fmt, onPick, activeKey, height = 196,
}: {
  steps: WaStep[]
  /** 数值格式化。由调用方决定小数位，避免组件里出现第二套口径 */
  fmt: (v: number) => string
  onPick?: (key: string) => void
  activeKey?: string | null
  height?: number
}) {
  const { show, hide, el } = useTip()

  /* 水位与量程。这里算的是**几何位置**，不是业务口径 ——
     真实数值全部来自 steps，组件只是把它们串成楼梯。 */
  const geo = useMemo(() => {
    let cum = 0
    const rows = steps.map((s) => {
      if (s.isTotal) {
        return { ...s, from: 0, to: s.value, before: cum, after: cum }
      }
      const from = cum
      cum += s.value
      return { ...s, from, to: cum, before: from, after: cum }
    })
    const vals = rows.flatMap((r) => [r.from, r.to])
    let lo = Math.min(0, ...vals)
    let hi = Math.max(0, ...vals)
    if (hi - lo < 1e-9) { lo = -1; hi = 1 }
    /* 上下各留 14%：数值标签要写在柱子上方/下方，
       不留余量的话最高那根的数字会被卡片顶裁掉。 */
    const pad = (hi - lo) * 0.14
    return { rows, lo: lo - pad, hi: hi + pad }
  }, [steps])

  const span = geo.hi - geo.lo
  const yPct = (v: number) => ((v - geo.lo) / span) * 100

  return (
    <div className="bi-wa">
      <div className="bi-wa-plot" style={{ height }}>
        {/* 零轴：所有"下降"和"上升"的分界。比普通网格线实，
            因为在这张图里它同时是"改善/恶化"的分界。 */}
        <div className="bi-wa-zero" style={{ bottom: `${yPct(0)}%` }} />

        {geo.rows.map((r, i) => {
          const top = Math.max(r.from, r.to)
          const bot = Math.min(r.from, r.to)
          const tone = r.tone ?? (r.value > 0 ? 'bad' : 'good')
          const isZero = Math.abs(r.value) < 1e-9
          const hPct = Math.abs(yPct(top) - yPct(bot))
          /* 数值标签放在柱子远离零轴的一侧：正值写顶上、负值写底下。
             统一写在顶上会让所有负值标签都挤在零轴附近，互相压。 */
          const valUp = r.value >= 0
          return (
            <div key={r.key} className="bi-wa-col">
              {/* 连接上一节的虚线：从上一节结束的水位水平拉过来。
                  没有它，楼梯是断开的，读者得自己脑补每一节接在哪。 */}
              {i > 0 && (
                <div
                  className="bi-wa-conn"
                  style={{ bottom: `${yPct(r.before)}%` }}
                />
              )}
              <button
                type="button"
                className={
                  `bi-wa-bar t-${tone}` +
                  (r.isTotal ? ' is-total' : '') +
                  (isZero ? ' is-zero' : '') +
                  (activeKey === r.key ? ' on' : '')
                }
                style={{ bottom: `${yPct(bot)}%`, height: `${Math.max(hPct, 0.8)}%` }}
                aria-label={`${r.label} ${fmt(r.value)}`}
                disabled={!onPick}
                onMouseMove={r.tip ? (e) => show(e, r.tip!, true) : undefined}
                onMouseLeave={r.tip ? hide : undefined}
                onFocus={r.tip
                  ? (e) => {
                    /* 键盘聚焦时没有鼠标坐标，用柱子自身位置喂给同一个浮层。
                       否则 Tab 过来什么都不发生，等于键盘用户看不到解释。 */
                    const b = e.currentTarget.getBoundingClientRect()
                    show({ clientX: b.left + b.width / 2, clientY: b.top },
                      r.tip!, true)
                  }
                  : undefined}
                onBlur={r.tip ? hide : undefined}
                onClick={onPick ? () => onPick(r.key) : undefined}
              />
              <div
                className={`bi-wa-val${valUp ? ' up' : ' down'}`}
                style={valUp
                  ? { bottom: `${yPct(top)}%` }
                  : { bottom: `${yPct(bot)}%` }}
              >
                {fmt(r.value)}
              </div>
            </div>
          )
        })}
      </div>

      <div className="bi-wa-axis">
        {geo.rows.map((r) => (
          <div key={r.key} className="bi-wa-al">
            <span className={r.isTotal ? 'is-total' : undefined}>{r.label}</span>
            {r.sub && <em>{r.sub}</em>}
          </div>
        ))}
      </div>
      {el}
    </div>
  )
}

/* ------------------------------------------------------------- 环形图 */

export interface DonutItem {
  key: string
  label: string
  /** 占比（0–100），由后端给 */
  value: number
  /** 环里显示的副文本，比如条数 */
  sub?: string
}

/* 【四级灰阶，不能更暗】
 * 第一版最后一个色用了 #56565e —— 那其实是项目色板里的 --fg-5，
 * 定位是"刻度线/占位"，实测在 #151517 卡片上几乎看不见，
 * 截图里那个扇区看起来像**图缺了一块**，而不是"第四个类目"。
 *
 * 所以从 --fg 往下取四档，最暗的 #7c7c84 在卡片上仍然可辨。
 * 全部是中性灰：这一页的红/绿只留给"恶化/改善"，
 * 类目是并列关系，不该有任何一方看起来更"严重"。
 */
const DONUT_COLORS = ['#f2f2f3', '#c6c6cc', '#9c9ca4', '#7c7c84']

/**
 * 类目名 → 固定颜色。
 *
 * 【为什么不让颜色跟着输入顺序走】
 * 这一页的类目顺序是按"贡献绝对值"排的，换一周顺序就变。
 * 若按下标配色，同一个「服饰」这周是白色、下周变深灰 ——
 * 读者跨周对比时会以为看错了图。
 *
 * 做法：对**全部类目名排序后**取位置。这样同一组类目无论以什么顺序
 * 传进来，颜色都不变；只有类目集合真的变了才会重排。
 * 不用哈希 —— 4 个类目配 4 个色，哈希有相当概率撞车，
 * 撞了就变成"两个类目同一个颜色"，比顺序变化更糟。
 */
function colorMap(names: string[]): Map<string, string> {
  const uniq = [...new Set(names)].sort()
  const m = new Map<string, string>()
  uniq.forEach((n, i) => m.set(n, DONUT_COLORS[i % DONUT_COLORS.length]))
  return m
}

/**
 * 环形图：部分与整体。
 *
 * 【为什么这里用饼图是对的，而别处不该用】
 * 饼图只有一个适用场景：**几个部分合成一个整体，且部分数很少**。
 * 它有天然缺陷 —— 人眼比较角度远不如比较长度准确，
 * 所以"哪一类退货率高"绝不能用饼图（那是比较，不是构成）。
 * 但"订单在四个类目之间怎么分"恰好就是构成，而且四个。
 *
 * 【图例必须带数字】
 * 灰阶系统里四个扇区靠亮度区分，一旦用户开了高对比度、
 * 或者把图截图印成黑白，颜色就不可靠了。
 * 所以每个图例项都写上自己的百分比和绝对量 ——
 * 颜色只是"帮你对上号"，数值才是数据本身。
 */
export function Donut({
  items, centerLabel, centerValue, onPick, activeKey,
}: {
  items: DonutItem[]
  centerLabel: string
  centerValue: string
  onPick?: (key: string) => void
  activeKey?: string | null
}) {
  const { show, hide, el } = useTip()
  const [hover, setHover] = useState<string | null>(null)
  const on = hover ?? activeKey ?? null

  const total = items.reduce((s, x) => s + x.value, 0) || 1
  const R = 76
  const r = 50
  const cx = 90
  const cy = 90
  /* 扇区之间留缝：不留缝时相邻两个同亮度的扇区会连成一片，
     看起来像只有一个扇区。1.4° 在 4 个扇区时长约 1.9px，够看。 */
  const GAP = 1.4
  /* 颜色按名字固定，不跟输入顺序走 —— 见 colorMap 的注释 */
  const cmap = useMemo(() => colorMap(items.map((x) => x.label)), [items])

  const arcs = useMemo(() => {
    let acc = -90
    return items.map((it, i) => {
      const sweep = (it.value / total) * 360
      const a0 = acc + GAP / 2
      const a1 = acc + sweep - GAP / 2
      acc += sweep
      const rad = (d: number) => (d * Math.PI) / 180
      const p = (rr: number, d: number) => [cx + rr * Math.cos(rad(d)), cy + rr * Math.sin(rad(d))]
      const [x0o, y0o] = p(R, a0)
      const [x1o, y1o] = p(R, a1)
      const [x1i, y1i] = p(r, a1)
      const [x0i, y0i] = p(r, a0)
      const large = a1 - a0 > 180 ? 1 : 0
      return {
        ...it,
        i,
        // 单扇区占满（只有一个类目）时画整环，A 弧起止点重合会画不出来
        d: sweep >= 359.9
          ? `M ${cx - R} ${cy} A ${R} ${R} 0 1 1 ${cx + R} ${cy} A ${R} ${R} 0 1 1 ${cx - R} ${cy}
             M ${cx - r} ${cy} A ${r} ${r} 0 1 0 ${cx + r} ${cy} A ${r} ${r} 0 1 0 ${cx - r} ${cy}`
          : `M ${x0o} ${y0o} A ${R} ${R} 0 ${large} 1 ${x1o} ${y1o}
             L ${x1i} ${y1i} A ${r} ${r} 0 ${large} 0 ${x0i} ${y0i} Z`,
        color: cmap.get(it.label) ?? DONUT_COLORS[i % DONUT_COLORS.length],
        // 高对比度下用 opacity 区分（颜色会被抹平，opacity 不会）
        op: 1 - i * (0.55 / Math.max(items.length - 1, 1)),
      }
    })
  }, [items, total, cmap])

  return (
    <div className="bi-donut">
      <div className="bi-donut-fig">
        <svg viewBox="0 0 180 180" className="bi-donut-svg" role="img"
          aria-label={`${centerLabel} ${centerValue}`}>
          {arcs.map((a) => {
            const dim = on !== null && on !== a.key
            return (
              <path
                key={a.key}
                className="bi-donut-seg"
                d={a.d}
                fill={a.color}
                style={{
                  opacity: dim ? 0.34 : 1,
                  // 只有高对比度媒体查询会读这个变量，正常模式不影响
                  ['--seg-op' as string]: a.op,
                } as CSSProperties}
                onMouseMove={(e) => {
                  setHover(a.key)
                  show(e, (
                    <div>
                      <div className="bi-tip-h">{a.label}</div>
                      <div className="bi-tip-row">占比 <b>{a.value.toFixed(1)}%</b></div>
                      {a.sub && <div className="bi-tip-row">{a.sub}</div>}
                    </div>
                  ), true)
                }}
                onMouseLeave={() => { setHover(null); hide() }}
                onClick={onPick ? () => onPick(a.key) : undefined}
                // 图表本身不进 Tab 序列（下面的图例才是键盘入口），
                // 所以鼠标事件要标成装饰性
                role="presentation"
              />
            )
          })}
        </svg>
        <div className="bi-donut-mid">
          <b>{centerValue}</b>
          <em>{centerLabel}</em>
        </div>
      </div>

      {/* 图例：键盘/触屏的真正入口。每个按钮都能聚焦、能点、带读数。
          扇区靠 hover，图例靠 Tab —— 两条路通向同一个筛选。 */}
      <div className="bi-donut-legend">
        {items.map((it) => (
          <button
            key={it.key}
            type="button"
            className={`bi-donut-li${on === it.key ? ' on' : ''}`}
            onClick={onPick ? () => onPick(it.key) : undefined}
            disabled={!onPick}
            onMouseMove={(e) => {
              setHover(it.key)
              show(e, (
                <div>
                  <div className="bi-tip-h">{it.label}</div>
                  <div className="bi-tip-row">占比 <b>{it.value.toFixed(1)}%</b></div>
                  {it.sub && <div className="bi-tip-row">{it.sub}</div>}
                </div>
              ), true)
            }}
            onMouseLeave={() => { setHover(null); hide() }}
          >
            {/* 色块必须和扇区取同一个颜色表 —— 这是读者把图例和扇区对上的唯一线索。
                所以这里也走 cmap，不能自己按下标算，否则两边对不上号。 */}
            <i style={{ background: cmap.get(it.label) ?? DONUT_COLORS[0] }} />
            <span className="bi-donut-n">{it.label}</span>
            <b>{it.value.toFixed(1)}%</b>
            {it.sub && <em>{it.sub}</em>}
          </button>
        ))}
      </div>
      {el}
    </div>
  )
}

/* ------------------------------------------------------------- 发散条 */

export interface DvItem {
  key: string
  label: string
  /** 带符号数值；零是中心 */
  value: number
  sub?: string
  tip?: ReactNode
}

/**
 * 堆叠份额条：一条损失里，有多少能救回来、多少是净亏。
 *
 * 【为什么现有 5 个视图都答不了这个】
 *   · 份额变化 —— 答"构成变没变"
 *   · 损失×可再售 —— 散点，答"损失大且卖不掉的在哪"，但读不出绝对金额
 *   · 条数×金额 —— 答"按条数排会不会排错"
 *   · 归属树图 —— 答"该找哪个部门"
 *   · 明细 —— 原始数
 *
 * 缺的那问是：**这笔钱里有百分之多少能拿回来。**
 * 两个原因都亏 10 万，可再售率 80% 和 10% 的处置方式完全不同：
 * 前者是降价清库存，后者只能报损。这个区别决定动作，而不只是排序。
 *
 * 【为什么用堆叠条而不是饼图】
 * 要同时读两件事：每个原因的**总量**（条长）和**可挽回占比**（段内比例）。
 * 饼图只能读其一 —— 分成 8 块的饼，既看不清总量也看不清比例。
 * 堆叠条共用同一把横轴，两个通道都保住了。
 *
 * 【高对比度下为什么还读得出来】
 * 不靠颜色深浅，靠**段的位置**（左=可挽回，右=净损失）
 * 和右侧直接写出的金额。forced-colors 抹掉配色后依然成立。
 */
export function StackedShare({
  items, onPick, activeKey, sortBy,
}: {
  items: {
    key: string
    label: string
    /** 总量（决定条长） */
    total: number
    /** 其中可挽回的部分 */
    keep: number
    tip?: ReactNode
  }[]
  onPick?: (key: string) => void
  activeKey?: string | null
  /** 排序口径：按总量，还是按可挽回金额（后者才是"先救哪个"的排法） */
  sortBy: 'total' | 'keep'
}) {
  const { show, hide, el } = useTip()
  const sorted = [...items].sort((a, b) =>
    sortBy === 'keep' ? b.keep - a.keep : b.total - a.total)
  const maxTotal = Math.max(...items.map((x) => x.total), 1e-9)
  const sumTotal = items.reduce((s, x) => s + x.total, 0)
  const sumKeep = items.reduce((s, x) => s + x.keep, 0)

  return (
    <div className="bi-ss">
      {/* 先给整体口径：读者知道总盘子，再去看每一行 */}
      <div className="bi-ss-head">
        <span>
          可挽回 <b className="t-good">{money0(sumKeep)}</b>
          <em>占损失 {pct1(sumKeep, sumTotal)}%</em>
        </span>
        <span className="dim">左段 = 可二次销售可挽回，右段 = 净损失</span>
      </div>

      {sorted.map((it) => {
        const keepW = it.total > 0 ? (it.keep / it.total) * 100 : 0
        const barW = (it.total / maxTotal) * 100
        return (
          <div
            key={it.key}
            className={`bi-ss-row${activeKey === it.key ? ' on' : ''}`}
            onMouseMove={it.tip ? (e) => show(e, it.tip!) : undefined}
            onMouseLeave={it.tip ? hide : undefined}
            onClick={onPick ? () => onPick(it.key) : undefined}
            /* 可点的行必须进 Tab 序列，展示行不进 ——
               和 Diverging / HBars 同一条规矩。 */
            role={onPick ? 'button' : undefined}
            tabIndex={onPick ? 0 : undefined}
            onKeyDown={onPick
              ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onPick(it.key)
                }
              }
              : undefined}
          >
            <div className="bi-ss-l" title={it.label}>{it.label}</div>
            <div className="bi-ss-track">
              {/* 条宽按"占最大值的比例"，不是占 100% ——
                  否则 8 个原因里最小的那条会细成一根线，看不清有没有。 */}
              <div className="bi-ss-bar" style={{ width: `${barW}%` }}>
                <div className="bi-ss-keep" style={{ width: `${keepW}%` }} />
              </div>
              {/* 【比例标在轨道右端，不跟在条后面】
                  第一版写成条后紧跟，结果条一长就把百分比推到轨道外面，
                  4 行的比例直接看不见了。
                  改成定位在轨道右端：无论条多长，数字都在同一个位置，
                  竖着扫一列就能比较 —— 这本身也比"跟着条尾巴跑"更好读。 */}
              <em className="bi-ss-pct">{keepW.toFixed(0)}%</em>
            </div>
            <div className="bi-ss-v">
              <b>{money0(it.keep)}</b>
              <em>/ {money0(it.total)}</em>
            </div>
          </div>
        )
      })}
      {el}
    </div>
  )
}

/** 金额：上万折成「x.x 万」，否则原样。表格里已经这么显示，保持一致。 */
function money0(v: number): string {
  const a = Math.abs(v)
  if (a >= 1e4) return `${(v / 1e4).toFixed(1)}万`
  return Math.round(v).toLocaleString('zh-CN')
}
function pct1(a: number, b: number): string {
  if (!b) return '0.0'
  return ((a / b) * 100).toFixed(1)
}

/**
 * 发散条：以零为中心，向左右两侧生长。
 *
 * 【为什么不用普通横条】
 * 普通横条从左边起画，正负都朝右 —— 读者必须去读数字才知道方向。
 * 而"差距"这个量最要紧的信息就是**方向**：
 * 比基准高还是低，决定要不要动手。绕零发散把方向变成位置，
 * 不用看数字、不用看颜色。
 *
 * 【也是唯一在高对比度下完全无损的图】
 * 它纯粹靠"零轴左边还是右边、伸出去多长"传信息，
 * 颜色被系统覆盖掉也不影响读数。项目里其他图都得为 HC 做特殊处理，
 * 这张不用。
 */
export function Diverging({
  items, fmt, zeroLabel, onPick, activeKey,
}: {
  items: DvItem[]
  fmt: (v: number) => string
  /** 零刻度下方的一行小字，解释"往右意味着什么" */
  zeroLabel?: [string, string]
  onPick?: (key: string) => void
  activeKey?: string | null
}) {
  const { show, hide, el } = useTip()
  const maxAbs = Math.max(...items.map((x) => Math.abs(x.value)), 1e-9)

  return (
    <div className="bi-dv">
      {zeroLabel && (
        <div className="bi-dv-head">
          <span>{zeroLabel[0]}</span>
          <span>{zeroLabel[1]}</span>
        </div>
      )}
      {items.map((it) => {
        const w = (Math.abs(it.value) / maxAbs) * 50
        const neg = it.value < 0
        return (
          <div
            key={it.key}
            className={`bi-dv-row${activeKey === it.key ? ' on' : ''}`}
            onMouseMove={it.tip ? (e) => show(e, it.tip!) : undefined}
            onMouseLeave={it.tip ? hide : undefined}
            onClick={onPick ? () => onPick(it.key) : undefined}
            /* 和 BiKit 的 HBars 同一条规矩：可点的行必须进 Tab 序列，
               展示行不该进（否则 Tab 会在无意义的地方停住）。 */
            role={onPick ? 'button' : undefined}
            tabIndex={onPick ? 0 : undefined}
            onKeyDown={onPick
              ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onPick(it.key)
                }
              }
              : undefined}
          >
            <div className="bi-dv-l" title={it.label}>{it.label}</div>
            <div className="bi-dv-track">
              <div className="bi-dv-zero" />
              <div
                className={`bi-dv-bar ${neg ? 'neg' : 'pos'}`}
                style={neg
                  ? { right: '50%', width: `${w}%` }
                  : { left: '50%', width: `${w}%` }}
              />
            </div>
            <div className="bi-dv-v">
              <span className={neg ? 't-good' : 't-bad'}>{fmt(it.value)}</span>
              {it.sub && <em>{it.sub}</em>}
            </div>
          </div>
        )
      })}
      {el}
    </div>
  )
}

/* ------------------------------------------------------------- 象限散点 */

export interface QPoint {
  key: string
  label: string
  /** 横轴值（越大越靠右） */
  x: number
  /** 纵轴值（越大越靠上） */
  y: number
  /** 气泡大小依据（通常是条数） */
  size: number
  tip?: ReactNode
}

/**
 * 象限散点：两个维度的组合，找出"该先管哪个"。
 *
 * 【为什么原因构成那一块需要它】
 * 表格能告诉你"破损损失 16789、可再售 8.5%、份额 +1.56pp"，
 * 但你得自己在脑子里把这三列合成一句话。而真实的决策问题只有一个：
 * **哪个原因既损失大、又基本无法挽回？**
 *
 * 把"损失"和"可再售率"分别放到两个轴上，答案就变成了位置：
 *   右下（损失大 + 不可再售）= 真金白银的净损失，最该管
 *   右上（损失大 + 可再售）  = 能抢回来，优先做二次上架
 *   左侧                    = 钱不多，先放着
 * 气泡大小用条数 —— 顺带回答"这类退货多不多"。
 *
 * 【四象限的划分线用中位数，不用平均值】
 * 平均值会被最大的那个点拽过去，导致某一象限只有孤零零一个点。
 * 中位数保证两边点数相当，"象限"才有意义。
 */
export function Quadrant({
  points, xLabel, yLabel, fmtX, fmtY, fmtSize, height = 258, quadrants,
  onPick, activeKey,
}: {
  points: QPoint[]
  xLabel: string
  yLabel: string
  fmtX: (v: number) => string
  fmtY: (v: number) => string
  fmtSize?: (v: number) => string
  height?: number
  /* 【象限名改成可选，好让别的地方复用这张图】
     这张图的本体其实是一个"两维 + 规模"的散点图，
     象限只是其中一种读法。类目贡献度那块要用 x=订单占比、y=差距
     画气泡，但它没有"哪个象限更好"这回事（右上只是"更值得先管"），
     套上"损失大/可挽回"那套词会串味。
     不传时只画散点，连分割线也一起不画 —— 半套象限线比没有更误导。 */
  quadrants?: [string, string, string, string]   // [右上, 右下, 左上, 左下]
  /* 【选中态：让"钉住一个点"能跨视图保留】
     其他几张图（环形/发散/双环/树图）都支持 onPick + activeKey，
     只有象限图没有。结果是：用户在象限图里点中「材质做工问题」，
     切到「明细」想看它的具体数字 —— 选中丢了，得重新找。
     一张看板里"选中"必须是全局概念，不能每张图各记各的。 */
  onPick?: (key: string) => void
  activeKey?: string | null
}) {
  const { show, hide, el } = useTip()
  const [hover, setHover] = useState<string | null>(null)

  const mid = (arr: number[]) => {
    const s = [...arr].sort((a, b) => a - b)
    const n = s.length
    if (!n) return 0
    return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2
  }

  const geo = useMemo(() => {
    const xs = points.map((p) => p.x)
    const ys = points.map((p) => p.y)
    const xm = mid(xs)
    const ym = mid(ys)
    let x0 = Math.min(...xs); let x1 = Math.max(...xs)
    let y0 = Math.min(...ys); let y1 = Math.max(...ys)
    /* 四周留 16%，让最边上的点不贴边 —— 贴边的气泡和它的标签
       会被卡片边缘切掉一半，看起来像少了一个点。 */
    const px = (x1 - x0) * 0.16 || 1
    const py = (y1 - y0) * 0.16 || 1
    x0 -= px; x1 += px; y0 -= py; y1 += py
    const maxSize = Math.max(...points.map((p) => p.size), 1)
    return { x0, x1, y0, y1, xm, ym, maxSize }
  }, [points])

  const px = (v: number) => ((v - geo.x0) / (geo.x1 - geo.x0)) * 100
  /* y 轴要翻转：SVG/CSS 的 top 从上方算起，而数值越大该越靠上 */
  const py = (v: number) => 100 - ((v - geo.y0) / (geo.y1 - geo.y0)) * 100
  const rOf = (s: number) => 7 + (s / geo.maxSize) * 13

  /* 【标签防重叠】
     第一版标签一律挂在气泡右侧，实测左下角三个点
     （售后流程问题 12.1% / 功能性能故障 30.6% / 时效慢 18.6%，
     top 分别是 51.1 / 49.3 / 46.7）挤在 4% 的高度里，
     三个中文标签叠成一团，读者分不清哪行字属于哪个点。

     两条规则一起用：
       1. 点在右半边 → 标签挂左侧；左半边 → 挂右侧。
          这样标签朝"更空的那一边"伸展，占用空间立刻少一半。
       2. 同侧标签按 y 排序，逐个下压，保证最小行距 LABEL_H。
          只做 1 不做 2 的话，同侧多个点挨得近时还是叠。

     偏移量用 px 计算（height 已知），因为百分比在一屏内不线性 ——
     4% 在 258px 里是 10px，而"一行小字"需要 14px。 */
  const LABEL_H = 14
  const labelPos = useMemo(() => {
    const m = new Map<string, { side: 'l' | 'r'; topPct: number }>()
    const raw = points.map((p) => ({
      key: p.key,
      xPct: px(p.x),
      yPct: py(p.y),
      side: (px(p.x) > 55 ? 'l' : 'r') as 'l' | 'r',
    }))
    for (const side of ['l', 'r'] as const) {
      const group = raw.filter((r) => r.side === side).sort((a, b) => a.yPct - b.yPct)
      let lastPx = -Infinity
      for (const g of group) {
        const want = (g.yPct / 100) * height
        const got = Math.max(want, lastPx + LABEL_H)
        lastPx = got
        m.set(g.key, { side, topPct: (got / height) * 100 })
      }
    }
    return m
  }, [points, height, geo])

  return (
    <div className="bi-q">
      <div className="bi-q-plot" style={{ height }}>
        {/* 象限分割线 */}
        {quadrants && (
          <>
            <div className="bi-q-split v" style={{ left: `${px(geo.xm)}%` }} />
            <div className="bi-q-split h" style={{ top: `${py(geo.ym)}%` }} />
          </>
        )}

        {/* 象限名。放在角落，用最弱的字重 —— 它是读图的提示，
            不该比数据本身更显眼。

            【v4.9：底部两个角整体上移 6px】
            原来 bottom:4，而轴标签紧贴在画布下方 12px 处 ——
            左下角的"损失小"和外部的"可再售率 ↑"垂直距离只有 8px，
            字号都是 9~9.5px，实测看起来是两行糊在一起的字。
            上移后两者之间留够一行的高度，读起来才是两个独立标签。 */}
        {quadrants && (
          <>
            <span className="bi-q-q" style={{ right: 6, top: 4 }}>{quadrants[0]}</span>
            <span className="bi-q-q" style={{ right: 6, bottom: 10 }}>{quadrants[1]}</span>
            <span className="bi-q-q" style={{ left: 6, top: 4 }}>{quadrants[2]}</span>
            <span className="bi-q-q" style={{ left: 6, bottom: 10 }}>{quadrants[3]}</span>
          </>
        )}

        {points.map((p) => {
          const on = hover === p.key
          const dim = hover !== null && !on
          const pinned = activeKey === p.key
          return (
            <button
              key={p.key}
              type="button"
              className={`bi-q-dot${on ? ' on' : ''}${pinned ? ' pin' : ''}`}
              style={{
                left: `${px(p.x)}%`,
                top: `${py(p.y)}%`,
                width: rOf(p.size) * 2,
                height: rOf(p.size) * 2,
                opacity: dim ? 0.3 : 1,
              }}
              aria-label={`${p.label}：${xLabel} ${fmtX(p.x)}，${yLabel} ${fmtY(p.y)}`}
              aria-pressed={onPick ? pinned : undefined}
              onMouseEnter={() => setHover(p.key)}
              onMouseLeave={() => { setHover(null); hide() }}
              onFocus={() => setHover(p.key)}
              onBlur={() => { setHover(null); hide() }}
              onMouseMove={p.tip ? (e) => show(e, p.tip!, true) : undefined}
              onClick={onPick ? () => onPick(p.key) : undefined}
              onFocusCapture={p.tip
                ? (e) => {
                  const b = (e.currentTarget as HTMLElement).getBoundingClientRect()
                  show({ clientX: b.left + b.width / 2, clientY: b.top }, p.tip!, true)
                }
                : undefined}
            >
              {/* 气泡本体留空：标签单独画在外层，见下面的 label 层 */}
              <span className="bi-q-hit" aria-hidden="true" />
            </button>
          )
        })}

        {/* 【标签必须单独一层】
            第一版把 <span class="bi-q-lab"> 放在气泡按钮内部，
            于是它受气泡的层叠上下文约束 —— 气泡是 z-index:1，
            标签就算写 z-index:6 也压不到旁边那个 z-index:1 的气泡上面
            （子元素的 z-index 只在父级上下文内比较）。
            实测表现：「描述与实物不符」的标签被右侧气泡盖掉一半。

            所以标签抽出来做成与气泡同级的独立层，整层 z-index 更高。
            代价是标签不再随按钮 hover 自动高亮，得自己用 hover 状态判断 ——
            一行 ternary 换一个永远不被遮挡的标签，划算。 */}
        <div className="bi-q-labs" aria-hidden="true">
          {points.map((p) => {
            const pos = labelPos.get(p.key)
            const on = hover === p.key
            const side = pos?.side ?? 'r'
            /* 偏移量必须用 px 算：标签的定位基准是"气泡中心"这个点，
               要往右让出气泡半径 + 6px 的间隙。
               半径是 rOf(p.size)，所以这里能直接传一个像素位移，
               不用 CSS 的 100% —— 那 100% 指的是整个标签层宽度。 */
            const gap = rOf(p.size) + 6
            return (
              <span
                key={p.key}
                className={`bi-q-lab side-${side}${on ? ' on' : ''}`}
                style={{
                  top: `${pos?.topPct ?? py(p.y)}%`,
                  left: `${px(p.x)}%`,
                  transform: side === 'r'
                    ? `translate(${gap}px, -50%)`
                    : `translate(calc(-100% - ${gap}px), -50%)`,
                }}
              >
                {p.label}
              </span>
            )
          })}
        </div>
      </div>

      <div className="bi-q-axes">
        <span className="bi-q-yl">{yLabel} ↑</span>
        <span className="bi-q-xl">{xLabel} →</span>
      </div>
      {el}
    </div>
  )
}

/* ------------------------------------------------------------- 斜率图 */

export interface SlopeItem {
  key: string
  label: string
  /** 左轴的值（通常"盘子大小"） */
  left: number
  /** 右轴的值（通常"差多少"） */
  right: number
  /** 线宽/圆点大小的依据（通常贡献 pp） */
  weight?: number
  /** 左值在全体里排第几（1 起），用来决定上下次序 */
  tip?: ReactNode
}

/**
 * 斜率图：同一批对象在两个指标上的位置，用连线把两个位置接起来。
 *
 * 【为什么这里不用散点/气泡】
 * 这张图只有 4 个类目。气泡图的读法是"看位置"，而 4 个点分散在大片
 * 空白里时，位置比较反而是最不准的 —— 实测四个点被撑到四个角，
 * 中间全是空的，读者要靠想象把「服饰在左上、生鲜在右下」翻译成排序。
 *
 * 斜率图把两个指标**各自归一化成一列位置**，中间连线：
 *   · 线越陡 = 两个指标的排名差越大 = 越值得单独看一眼
 *   · 水平线 = 两个指标排名一致 = 按哪个排都一样
 * 4 个项目时，"谁和谁交叉"比"谁在坐标系的哪个象限"好读得多，
 * 而且不会浪费 80% 的画布。
 *
 * 【左右两列共用同一套上下顺序】
 * 各自独立排序的话连线会缠成一团，交叉反而看不清。
 * 这里两列都按 left 降序排位置，右列的位置则按 right 降序算 ——
 * 所以"连线平不平"直接等于"两个排名差多少"。
 */
export function Slope({
  items, leftLabel, rightLabel, fmtLeft, fmtRight, height = 230,
  onPick, activeKey,
}: {
  items: SlopeItem[]
  leftLabel: string
  rightLabel: string
  fmtLeft: (v: number) => string
  fmtRight: (v: number) => string
  height?: number
  onPick?: (key: string) => void
  activeKey?: string | null
}) {
  const { show, hide, el } = useTip()
  const [hover, setHover] = useState<string | null>(null)

  const geo = useMemo(() => {
    const n = Math.max(items.length, 2)
    /* 左右各占 26% / 74% 的横向位置，中间是连线区。
       两侧留出的宽度要放得下"类目名 + 数值"，否则标签会压到线上。 */
    const byLeft = [...items].sort((a, b) => b.left - a.left)
    const byRight = [...items].sort((a, b) => b.right - a.right)
    const posL = new Map(byLeft.map((x, i) => [x.key, i]))
    const posR = new Map(byRight.map((x, i) => [x.key, i]))
    const yOf = (i: number) => ((i + 1) / (n + 1)) * 100
    return {
      rows: items.map((x) => ({
        ...x,
        yL: yOf(posL.get(x.key) ?? 0),
        yR: yOf(posR.get(x.key) ?? 0),
        rankL: (posL.get(x.key) ?? 0) + 1,
        rankR: (posR.get(x.key) ?? 0) + 1,
      })),
    }
  }, [items])

  /* 【为什么不用 weight 决定线宽】
     试过用贡献 pp 做线宽（越重要越粗），实测算不出来：
     贡献是 0.38–1.52，映射到线宽只差 3 倍，而四个类目的贡献本来就接近，
     线粗几乎一样；反而让"是否交叉"这个主视觉被粗细干扰。
     线宽统一，"陡不陡"才是唯一可读的变量。 */
  return (
    <div className="bi-slope">
      {/* 【表头必须写清"各自按什么排序"】
          这张图的读法是"两列各自的排名有没有错位"，而两列各自降序：
          左列按订单占比降、右列按差距降。不写出来的话，
          读者会以为两列的上下位置代表同一个排名，
          于是把"线陡"误读成"数值变化大"——其实它表示"名次变化大"。 */}
      <div className="bi-slope-head">
        <span>{leftLabel}<em>按此项降序</em></span>
        <span>{rightLabel}<em>按此项降序</em></span>
      </div>
      <div className="bi-slope-plot" style={{ height }}>
        {/* 【线必须用 100% 宽高的 SVG + 不拉伸坐标系】
            第一版用 viewBox="0 0 100 100" 配 preserveAspectRatio="none"，
            结果 strokeWidth 被横向拉伸了几倍，四条线糊成手指粗的白条，
            反而盖住了它们"是否交叉"这个唯一的看点。
            vector-effect: non-scaling-stroke 让描边宽度不随缩放变化 ——
            这是 SVG 里唯一能同时做到"坐标用百分比"和"线宽固定像素"的办法。 */}
        <svg className="bi-slope-svg" viewBox="0 0 100 100" preserveAspectRatio="none"
          aria-hidden="true">
          {geo.rows.map((r) => {
            const on = hover === r.key
            const dim = hover !== null && !on
            return (
              <line
                key={r.key}
                x1={26} y1={r.yL} x2={74} y2={r.yR}
                className={'bi-slope-line' + (on ? ' on' : '')}
                strokeWidth={on ? 2.2 : 1.3}
                vectorEffect="non-scaling-stroke"
                style={{ opacity: dim ? 0.18 : 1 }}
              />
            )
          })}
        </svg>

        {geo.rows.map((r) => {
          const on = hover === r.key
          const dim = hover !== null && !on
          const moved = r.rankL - r.rankR
          const pinned = activeKey === r.key
          return (
            <div key={r.key}>
              {/* 左端：类目名 + 左值。用 button 做，键盘可聚焦。
                  【v4.9：补上 onPick】
                  这张图原来两头都是 button，有 hover/focus 浮层，
                  但**点下去什么都不发生** —— 而卡片说明写着"点选一个类目可钉住，
                  切视图和明细都会保留"。说明与实现不一致，用户点两次就以为坏了。
                  现在两端都触发同一个 onPick：点左端点右端点都是"选这个类目"，
                  因为在读图的人眼里，两端本来就是同一个东西。 */}
              <button
                type="button"
                className={'bi-slope-node l' + (on ? ' on' : '') + (pinned ? ' is-pick' : '')}
                style={{ top: r.yL + '%', opacity: dim ? 0.35 : 1 }}
                onMouseEnter={() => setHover(r.key)}
                onMouseLeave={() => { setHover(null); hide() }}
                onFocus={() => setHover(r.key)}
                onBlur={() => { setHover(null); hide() }}
                onMouseMove={r.tip ? (e) => show(e, r.tip!, true) : undefined}
                onClick={onPick ? () => onPick(r.key) : undefined}
                aria-pressed={onPick ? pinned : undefined}
                aria-label={r.label + '：' + leftLabel + ' ' + fmtLeft(r.left)
                  + '，' + rightLabel + ' ' + fmtRight(r.right)}
              >
                <span className="bi-slope-lab">{r.label}</span>
                <b>{fmtLeft(r.left)}</b>
              </button>
              {/* 右端：右值 + 名次变化。
                  类目名不再重复 —— 左端已经写了名字，中间的线就是
                  "同一个东西"的凭证。两边都写名字会让这张图多一倍字，
                  读起来像两张表拼在一起，而不是一张图。 */}
              <button
                type="button"
                className={'bi-slope-node r' + (on ? ' on' : '') + (pinned ? ' is-pick' : '')}
                style={{ top: r.yR + '%', opacity: dim ? 0.35 : 1 }}
                onMouseEnter={() => setHover(r.key)}
                onMouseLeave={() => { setHover(null); hide() }}
                onFocus={() => setHover(r.key)}
                onBlur={() => { setHover(null); hide() }}
                onMouseMove={r.tip ? (e) => show(e, r.tip!, true) : undefined}
                onClick={onPick ? () => onPick(r.key) : undefined}
                aria-pressed={onPick ? pinned : undefined}
                aria-label={r.label + '：' + rightLabel + ' ' + fmtRight(r.right)}
              >
                {moved !== 0 && (
                  <em className={moved > 0 ? 't-bad' : 't-good'}>
                    {moved > 0 ? '↓' + moved : '↑' + (-moved)}
                  </em>
                )}
                <b>{fmtRight(r.right)}</b>
              </button>
            </div>
          )
        })}
      </div>
      {el}
    </div>
  )
}

/* ------------------------------------------------------------- 双环对比 */

export interface DualItem {
  key: string
  label: string
  /** 外环：按条数的份额（%） */
  outer: number
  /** 内环：按金额的份额（%） */
  inner: number
  outerText?: string
  innerText?: string
  tip?: ReactNode
}

/**
 * 双环：同一批项目，两套计量口径各占多少。
 *
 * 【为什么必须是双环，不是两个饼图并排】
 * 这一块要回答的问题很具体：**按条数算最多的原因，是不是也最费钱？**
 * 实测第 44 周：
 *   尺码版型不符  12.47% 的条数，只占 7.16% 的损失（可再售 81%，能卖回去）
 *   功能性能故障   4.66% 的条数，却占 9.04% 的损失（件少但每件都报废）
 * 两个独立的饼图看不出这种错位 —— 读者得在心里把两张图逐项对齐，
 * 而 8 个项目对齐一遍之后，早就忘了第一项的占比。
 * 同心环把两个口径放在同一条半径上，**扇区宽度的差就是口径差**，
 * 错位直接变成看得见的宽度变化。
 *
 * 【外环条数、内环金额】
 * 不是随便定的：读者对"条数"的直觉更强（"一半的退货是破损"），
 * 放外圈先建立基数印象；金额在里圈紧跟其后，形成"但是……"的转折。
 *
 * 【中心必须写金额总额，不写百分比】
 * 两环各是 100%，中心再放一个 100% 是废话。
 * 放"24.9万"这个绝对值，这张图才和"可挽回多少钱"挂上钩。
 */
export function DualRing({
  items, centerLabel, centerValue, onPick, activeKey, outerLabel, innerLabel,
}: {
  items: DualItem[]
  centerLabel: string
  centerValue: string
  onPick?: (key: string) => void
  activeKey?: string | null
  outerLabel: string
  innerLabel: string
}) {
  const { show, hide, el } = useTip()
  const [hover, setHover] = useState<string | null>(null)
  const on = hover ?? activeKey ?? null

  const cx = 90
  const cy = 90
  /* 外环 80→62，内环 58→40，中心半径 40。
     两环之间留 4px 空隙，否则"外环内边界"和"内环外边界"糊成一条。 */
  const O = { r0: 80, r1: 62 }
  const I = { r0: 58, r1: 40 }
  const GAP = 1.1
  /* 【"其余未单列"必须用最弱的一档，不能跟正常项目抢颜色】
     它是残差、不是分析对象：给它是为了让环闭合（否则缺一块像渲染坏了），
     但用最暗的灰 + 斜线纹理，一眼能看出"这不是一个具体原因"。
     colorMap 按名字排序分配，如果把它混进正常项目里，
     它可能拿到最亮的颜色，反而比真实原因更显眼。 */
  const REST_KEY = '__rest'
  const cmap = useMemo(() => {
    const real = items.filter((x) => x.key !== REST_KEY)
    return colorMap(real.map((x) => x.label))
  }, [items])
  const colorOf = (it: DualItem, i: number) =>
    it.key === REST_KEY ? 'rgba(140,140,148,.55)'
      : (cmap.get(it.label) ?? DONUT_COLORS[i % DONUT_COLORS.length])

  const arcs = useMemo(() => {
    const totO = items.reduce((s, x) => s + x.outer, 0) || 1
    const totI = items.reduce((s, x) => s + x.inner, 0) || 1
    const rad = (d: number) => (d * Math.PI) / 180
    const p = (rr: number, d: number) => [cx + rr * Math.cos(rad(d)), cy + rr * Math.sin(rad(d))]

    /* 两环各自从 -90° 起画、按同一顺序累加 ——
       这样同一项目的两个扇区落在同一条半径方向上，
       "宽度差"才有意义。顺序不一致的话对比的是两个不同方向的扇区。 */
    const ring = (key: 'outer' | 'inner', band: { r0: number; r1: number }, tot: number) => {
      let acc = -90
      return items.map((it, i) => {
        const sweep = (it[key] / tot) * 360
        const a0 = acc + GAP / 2
        const a1 = acc + sweep - GAP / 2
        acc += sweep
        const [x0o, y0o] = p(band.r0, a0)
        const [x1o, y1o] = p(band.r0, a1)
        const [x1i, y1i] = p(band.r1, a1)
        const [x0i, y0i] = p(band.r1, a0)
        const large = a1 - a0 > 180 ? 1 : 0
        return {
          key: it.key + '__' + key,
          label: it.label,
          item: it,
          ring: key,
          i,
          d: `M ${x0o} ${y0o} A ${band.r0} ${band.r0} 0 ${large} 1 ${x1o} ${y1o}
              L ${x1i} ${y1i} A ${band.r1} ${band.r1} 0 ${large} 0 ${x0i} ${y0i} Z`,
        }
      })
    }
    return [...ring('outer', O, totO), ...ring('inner', I, totI)]
  }, [items])

  const tipOf = (it: DualItem) => {
    const d = it.inner - it.outer
    return (
      <div>
        <div className="bi-tip-h">{it.label}</div>
        <div className="bi-tip-row">
          {outerLabel} <b>{it.outer.toFixed(2)}%</b>
          {it.outerText && <span className="dim"> · {it.outerText}</span>}
        </div>
        <div className="bi-tip-row">
          {innerLabel} <b>{it.inner.toFixed(2)}%</b>
          {it.innerText && <span className="dim"> · {it.innerText}</span>}
        </div>
        <div className="bi-tip-row">
          口径差{' '}
          <b className={d > 0 ? 't-bad' : 't-good'}>
            {d > 0 ? '+' : ''}{d.toFixed(2)}pp
          </b>
        </div>
        <div className="bi-tip-row dim">
          {d >= 2
            ? '条数不多但很费钱：单件损失重，优先降单价'
            : d <= -2
              ? '条数多但每件赔得少：靠量，适合批量处理'
              : '两种口径基本一致：按条数排就行'}
        </div>
      </div>
    )
  }

  return (
    <div className="bi-dring">
      <div className="bi-dring-fig">
        <svg viewBox="0 0 180 180" className="bi-dring-svg" role="img"
          aria-label={outerLabel + '与' + innerLabel + '对照，' + centerLabel + ' ' + centerValue}>
          {arcs.map((a) => {
            const dim = on !== null && on !== a.item.key
            return (
              <path
                key={a.key}
                className="bi-dring-seg"
                d={a.d}
                fill={colorOf(a.item, a.i)}
                style={{
                  opacity: dim ? 0.3 : 1,
                  /* 只有高对比度媒体查询会读这个变量：那里颜色被抹平，
                     靠"外亮内暗"保住两环的层次（见 app.css 的 forced-colors 段） */
                  ['--ring-op' as string]: a.ring === 'inner' ? 0.62 : 1,
                } as CSSProperties}
                onMouseMove={(e) => { setHover(a.item.key); show(e, tipOf(a.item), true) }}
                onMouseLeave={() => { setHover(null); hide() }}
                onClick={onPick ? () => onPick(a.item.key) : undefined}
                role="presentation"
              />
            )
          })}
        </svg>
        <div className="bi-dring-mid">
          <b>{centerValue}</b>
          <em>{centerLabel}</em>
        </div>
      </div>

      <div className="bi-dring-legend">
        <div className="bi-dring-head">
          <span><i className="bi-dring-sw o" />{outerLabel}</span>
          <span><i className="bi-dring-sw i" />{innerLabel}</span>
          <span className="dim">口径差</span>
        </div>
        {items.map((it) => {
          const d = it.inner - it.outer
          return (
            <button
              key={it.key}
              type="button"
              className={'bi-dring-li' + (on === it.key ? ' on' : '')}
              onClick={onPick ? () => onPick(it.key) : undefined}
              disabled={!onPick}
              onMouseMove={(e) => { setHover(it.key); show(e, tipOf(it), true) }}
              onMouseLeave={() => { setHover(null); hide() }}
              onFocus={(e) => {
                const b = e.currentTarget.getBoundingClientRect()
                setHover(it.key)
                show({ clientX: b.left + b.width / 2, clientY: b.top }, tipOf(it), true)
              }}
              onBlur={() => { setHover(null); hide() }}
            >
              <i style={{ background: colorOf(it, items.indexOf(it)) }} />
              <span className="bi-dring-n">{it.label}</span>
              <b>{it.outer.toFixed(1)}%</b>
              <b className="dim">{it.inner.toFixed(1)}%</b>
              {/* 口径差必须显示：这是整张图唯一的结论。
                  只给两个百分比，读者仍然要自己做减法。 */}
              <em className={Math.abs(d) >= 2 ? (d > 0 ? 't-bad' : 't-good') : 'dim'}>
                {d > 0 ? '+' : ''}{d.toFixed(1)}
              </em>
            </button>
          )
        })}
      </div>
      {el}
    </div>
  )
}

/* ------------------------------------------------------------- 矩形树图 */

export interface TreeLeaf {
  key: string
  /** 小组名（一级归属），决定它落在哪个父块里 */
  group: string
  label: string
  /** 面积驱动量。这里传金额，不传条数 —— 理由见组件注释 */
  value: number
  /** 格子里的第二行小字，比如"848 条 · 可再售 3.7%" */
  sub?: string
  tip?: ReactNode}

/* 【五级灰阶】
 * 组数比环形图多一档（5 个一级归属 vs 4 个类目），
 * 所以另开一组比 DONUT_COLORS 更密的色阶。
 * 最暗的 #909098 在卡片 #151517 上仍然能撑住深色文字（对比度约 5.6:1）——
 * 再暗就得上浅色字，同一张图里两种字色会让"哪个更重要"变得可疑。
 * 全程中性灰：这一页的红/绿只留给"恶化/改善"，归属之间是并列关系。
 */
const TREE_COLORS = ['#f7f7f8', '#dcdce2', '#c2c2ca', '#a8a8b0', '#909098']

/**
 * 平方化树图（squarified treemap）布局。
 *
 * 【为什么不是"按顺序切条"】
 * 最朴素的实现是按面积切竖条，做法是遍历一次就行。
 * 但那样出来的格子又长又窄 —— 标签放不进去，面积也读不准
 * （人眼对细长条的面积估计误差极大）。
 *
 * 平方化的做法：每次贪心地往当前行里塞，只要"最差长宽比"还在变好就继续塞；
 * 一旦塞进去让最差长宽比变差，就结束这一行、换下一行。
 * 结果是每块都尽量接近正方形 —— 这既是能放标签的前提，
 * 也让"面积比较"重新变得可信。
 *
 * 这是 Bruls/Huizing/van Wijk 2000 的经典算法，不自己发明。
 */
function squarify(
  items: { key: string; value: number }[],
  W: number,
  H: number,
): Map<string, { x: number; y: number; w: number; h: number }> {
  const out = new Map<string, { x: number; y: number; w: number; h: number }>()
  const total = items.reduce((s, i) => s + i.value, 0) || 1
  const scale = (W * H) / total
  const nodes = items.map((i) => ({ key: i.key, a: Math.max(i.value * scale, 0.0001) }))

  let x = 0
  let y = 0
  let w = W
  let h = H
  let i = 0

  while (i < nodes.length) {
    const short = Math.min(w, h)
    if (short <= 0.0001) break
    /* 逐块试探：把下一个塞进当前行，最差长宽比是变好还是变坏 */
    const row: typeof nodes = []
    let rowArea = 0
    let bestWorst = Infinity
    while (i < nodes.length) {
      const a = nodes[i].a
      const area = rowArea + a
      const thickness = area / short
      let worst = 0
      for (const r of [...row, nodes[i]]) {
        const side = r.a / thickness
        const ar = Math.max(side / thickness, thickness / side)
        if (ar > worst) worst = ar
      }
      /* 第一块无论如何都要放（否则空行）；之后只要不变差就继续 */
      if (row.length > 0 && worst > bestWorst) break
      row.push(nodes[i])
      rowArea = area
      bestWorst = worst
      i++
    }
    /* 落位。w >= h 时行是一条竖带（沿短边 h 铺），否则是一条横带 */
    const thickness = rowArea / short
    let off = 0
    for (const r of row) {
      const side = r.a / thickness
      if (w >= h) {
        out.set(r.key, { x, y: y + off, w: thickness, h: side })
      } else {
        out.set(r.key, { x: x + off, y, w: side, h: thickness })
      }
      off += side
    }
    if (w >= h) { x += thickness; w -= thickness } else { y += thickness; h -= thickness }
  }
  return out
}

/**
 * 矩形树图：两级归属的"钱花在哪"。
 *
 * 【为什么这个模块需要它，而现有四张图都给不了】
 * 数据本身是两级的：一级归属（履约物流 / 产品本体 / 信息承诺 …）
 * → 二级原因（破损/变质/污损、尺码版型不符 …）。
 * 但现有四张视图**全部把层级压平到二级**：
 *
 *   份额变化   按 l2 排，看不出"哪一类归属整体最贵"
 *   损失×可再售 按 l2 散点，同样没有汇总
 *   条数×金额   双环按 l2，两环还都是占比
 *   明细       表格，8 行数字逐行读，没人会自己加起来
 *
 * 而被压平掉的那一层恰好改变了结论。第 44 周实测：
 *
 *   履约物流  74,847（30.5%）  ← 单条最大 60,303，排第 1
 *   产品本体  73,582（30.0%）  ← 单条最大 33,168，只排第 4
 *   信息承诺  57,853（23.6%）  ← 单条 57,853，排第 2
 *
 * 产品本体的三条原因（材质做工 33,168 / 功能故障 22,467 / 尺码版型 17,947）
 * 单独看分别排第 4、5、6，一条都进不了前二；
 * 但只要按归属加总，它是**第二大类，和第一名只差 1,265 元**。
 * 按 l2 排序的图永远看不到这件事 —— 而"该找哪个部门谈"恰恰是按归属问的。
 *
 * 【为什么面积传金额，不传条数】
 * 条数和金额在这一份数据里会给出相反的优先级：
 * 尺码版型不符占 12.5% 的条数，却只有 7.2% 的钱（可再售 81%）；
 * 功能性能故障占 4.7% 的条数，却有 9.0% 的钱（基本报废）。
 * 这张图的主题是"损失在哪"，所以面积给钱。
 * 条数不丢，放在格子副标题和浮层里 —— 两个口径都给，读者才能看见那个错位。
 *
 * 【为什么不用饼图】
 * 饼图只能表达"一层、少量部分合成整体"。这里是两级，且二级有 8 项，
 * 8 个扇区的角度差肉眼分不出来。层级 + 面积是树图的本职。
 *
 * 【为什么格子用 <button> 而不是 SVG <rect>】
 * 沿用本文件的约定：HTML 元素天然可聚焦、可挂 aria-label。
 * SVG 的 <rect> 默认进不了 Tab 序列，得手工补 role/tabIndex，
 * 而且嵌套层级还得自己算坐标。
 */
export function Treemap({
  leaves, fmt, onPick, activeKey, height = 300,
}: {
  leaves: TreeLeaf[]
  fmt: (v: number) => string
  onPick?: (key: string) => void
  activeKey?: string | null
  height?: number
}) {
  const { show, hide, el } = useTip()
  const [hover, setHover] = useState<string | null>(null)
  const on = hover ?? activeKey ?? null

  /* 组按总额降序 —— 最大的放左上，符合"从哪看起"的阅读顺序 */
  const groups = useMemo(() => {
    const m = new Map<string, { group: string; total: number; items: TreeLeaf[] }>()
    for (const l of leaves) {
      const g = m.get(l.group) ?? { group: l.group, total: 0, items: [] }
      g.total += l.value
      g.items.push(l)
      m.set(l.group, g)
    }
    return [...m.values()].sort((a, b) => b.total - a.total)
  }, [leaves])

  const grandTotal = groups.reduce((s, g) => s + g.total, 0) || 1

  /* 外层：按组总额分配矩形 */
  const outer = useMemo(
    () => squarify(groups.map((g) => ({ key: g.group, value: g.total })), 100, 100),
    [groups],
  )

  const colorOfGroup = useMemo(() => {
    const m = new Map<string, string>()
    groups.forEach((g, i) => m.set(g.group, TREE_COLORS[i % TREE_COLORS.length]))
    return m
  }, [groups])

  return (
    <div className="bi-tree">
      {/* 【外层必须是滚动容器，不能只给最小宽度】
          写了 min-width: 420px 但没有 overflow-x: auto 的话，
          容器一窄，最小宽度撑出去的部分会被父级直接裁掉 ——
          右侧整列格子消失，而且不会有滚动条提示"还有内容"。
          这种"内容被静默裁掉"比出现滚动条糟糕得多。 */}
      <div className="bi-tree-scroll">
        <div className="bi-tree-fig" style={{ height }}>
        {groups.map((g) => {
          const r = outer.get(g.group)
          if (!r) return null
          /* 内层：组内按二级原因再铺一次，坐标系换成百分比 */
          const inner = squarify(
            g.items.map((it) => ({ key: it.key, value: it.value })),
            100,
            100,
          )
          const base = colorOfGroup.get(g.group) ?? TREE_COLORS[0]
          return (
            <div
              key={g.group}
              className="bi-tree-g"
              style={{ left: `${r.x}%`, top: `${r.y}%`, width: `${r.w}%`, height: `${r.h}%` }}
            >
              <div className="bi-tree-gh">
                <span className="bi-tree-gn">{g.group}</span>
                <b>{fmt(g.total)}</b>
                <em>{((g.total / grandTotal) * 100).toFixed(0)}%</em>
              </div>
              <div className="bi-tree-gb">
                {g.items.map((it) => {
                  const q = inner.get(it.key)
                  if (!q) return null
                  const dim = on !== null && on !== it.key
                  return (
                    <button
                      key={it.key}
                      type="button"
                      className={'bi-tree-c' + (on === it.key ? ' on' : '')}
                      style={{
                        left: `${q.x}%`,
                        top: `${q.y}%`,
                        width: `${q.w}%`,
                        height: `${q.h}%`,
                        background: base,
                        /* 同组内按金额给透明度：一眼能分出主次，
                           又不破坏"同组同色"的归属感。
                           高对比度下颜色被抹平，靠这个变量保住强弱（见 app.css） */
                        opacity: dim ? 0.3 : 1,
                        ['--cell-op' as string]: String(
                          1 - (g.items.indexOf(it) * 0.16),
                        ),
                      } as CSSProperties}
                      aria-label={`${g.group} · ${it.label}，损失 ${fmt(it.value)}，占合计 ${((it.value / grandTotal) * 100).toFixed(1)}%`}
                      onMouseMove={(e) => { setHover(it.key); show(e, it.tip ?? (<div>{it.label}</div>), true) }}
                      onMouseLeave={() => { setHover(null); hide() }}
                      onFocus={(e) => {
                        const b = e.currentTarget.getBoundingClientRect()
                        setHover(it.key)
                        show({ clientX: b.left + b.width / 2, clientY: b.top }, it.tip ?? (<div>{it.label}</div>), true)
                      }}
                      onBlur={() => { setHover(null); hide() }}
                      onClick={onPick ? () => onPick(it.key) : undefined}
                    >
                      <span className="bi-tree-cl">{it.label}</span>
                      <span className="bi-tree-cv">{fmt(it.value)}</span>
                      {it.sub && <span className="bi-tree-cs">{it.sub}</span>}
                    </button>
                  )
                })}
              </div>
            </div>
          )
        })}
        </div>
      </div>
      {el}
    </div>
  )
}
/* --------------------------------------------------------------- 哑铃图 */

export interface DumbItem {
  key: string
  label: string
  /** 基准值（左端） */
  base: number
  /** 本周值（右端） */
  now: number
  /** 差距，用于决定颜色与方向 */
  gap: number
  /** 显著性；有就显示，没有不占位 */
  z?: number
  /** 规模（订单数），用来决定点的大小 */
  weight?: number
  tip?: ReactNode
}

/**
 * 哑铃图：同一批对象，"基准 vs 本周"两个值并排。
 *
 * 【为什么这一段不能用条形图】
 * 卡片里已经有「差距」发散条（只画差值）和「明细」表格（两列数字并排）：
 *   · 发散条回答"差多少"，但**丢掉了绝对水平** ——
 *     一个类目从 12% 涨到 20%，另一个从 30% 涨到 38%，差距都是 +8pp，
 *     发散条画出来一模一样，可这两件事的严重程度完全不同。
 *   · 表格有全部数字，但 4 行 × 7 列读下来没人会去逐行比较。
 *
 * 哑铃图把「绝对水平」和「相对变化」放在同一条线上：
 *   点的横向位置 = 这个类目本来多高（共用一把尺，可跨行比）
 *   两点的距离   = 这周差了多少（也就是发散条画的量）
 * 于是"本来就是最高的"和"这周才冲上去的"第一次能分开看。
 *
 * 实测第 44 周正好是这两种情况的对照：
 *   服饰  28.0% → 36.8%   水平最高，且这周又跳了 8.8pp
 *   数码  18.6% → 20.5%   水平居中，涨得也少
 * 只看差距排序，两条都是"要查"；看哑铃图，优先级一目了然。
 *
 * 【点的大小编码订单量】
 * 差距大但盘子小的类目，对全店的贡献可能不如差距小但盘子大的。
 * 直径按订单量开方缩放（面积正比于订单量），不按线性 ——
 * 线性映射下 8450 单的点会比 3929 单的大一倍多，视觉上过于夸张。
 */
export function Dumbbell({
  items, fmt, height, onPick, activeKey,
}: {
  items: DumbItem[]
  fmt: (v: number) => string
  height?: number
  onPick?: (key: string) => void
  activeKey?: string | null
}) {
  const { show, hide, el } = useTip()
  const [hover, setHover] = useState<string | null>(null)

  const geo = useMemo(() => {
    const all = items.flatMap((d) => [d.base, d.now])
    let lo = Math.min(...all)
    let hi = Math.max(...all)
    if (!isFinite(lo) || !isFinite(hi) || hi - lo < 1e-9) { lo -= 1; hi += 1 }
    /* 两侧各留 12%：最边上的点还要放得下数值标签 */
    const pad = (hi - lo) * 0.12
    const w = Math.max(...items.map((d) => d.weight ?? 1), 1)
    return { lo: lo - pad, hi: hi + pad, maxW: w }
  }, [items])

  const pos = (v: number) => ((v - geo.lo) / (geo.hi - geo.lo)) * 100

  const rOf = (w?: number) => {
    if (!w) return 5
    /* 开方缩放：面积正比于规模。5.5 起步、最大 10.5 ——
       第一版是 4~8，实测点在 46px 行高里太不显眼，
       而且空心点的 1.5px 描边在小尺寸下糊成一圈灰。 */
    return 5.5 + Math.sqrt(w / geo.maxW) * 5
  }

  const rowH = 46
  const H = height ?? Math.max(items.length * rowH, 150)

  return (
    <div className="bi-db">
      {/* 顶部刻度：共用一把尺，这是"跨行比水平"的前提。
          左右留白必须和 .bi-db-row 里的两列宽度一致（96 / 104），
          否则刻度和点会错位 —— 那比没有刻度更糟。 */}
      <div className="bi-db-axis">
        <div className="bi-db-axis-in">
          {[0, 25, 50, 75, 100].map((t) => (
            <span key={t} className="bi-db-tick" style={{ left: `${t}%` }}>
              {fmt(geo.lo + (geo.hi - geo.lo) * (t / 100))}
            </span>
          ))}
        </div>
      </div>

      <div className="bi-db-plot" style={{ height: H }}>
        {items.map((d, i) => {
          const on = hover === d.key
          const dim = hover !== null && !on
          const pinned = activeKey === d.key
          const xa = pos(d.base)
          const xb = pos(d.now)
          const left = Math.min(xa, xb)
          const width = Math.abs(xb - xa)
          const up = d.gap > 0
          const r = rOf(d.weight)
          return (
            <div
              key={d.key}
              className={'bi-db-row' + (on ? ' on' : '') + (pinned ? ' is-pick' : '')}
              style={{
                top: `${(i / items.length) * 100}%`,
                height: `${100 / items.length}%`,
                opacity: dim ? 0.34 : 1,
              }}
              /* 【整个行都要能出浮层，不只是类目名】
                 两个点是 pointer-events: none（否则会挡住行的 hover），
                 于是鼠标落在点上时，事件目标其实是这一行。
                 第一版只在类目名按钮上挂 show，结果"移到点上没反应" ——
                 而点恰恰是最自然想悬停的地方。
                 挂到行上之后，名字、连线、点、数值列任意位置都能读数。 */
              onMouseMove={d.tip
                ? (e) => { setHover(d.key); show(e, d.tip!, true) }
                : undefined}
              onMouseLeave={d.tip ? () => { setHover(null); hide() } : undefined}
            >
              {/* 行标签：类目名。必须写在左侧固定列，
                  否则每行的字都会跟着点的位置横跳，读起来像在扫视表格。 */}
              <button
                type="button"
                className="bi-db-name"
                aria-label={`${d.label}，基准 ${fmt(d.base)}，本周 ${fmt(d.now)}，差距 ${d.gap > 0 ? '+' : ''}${d.gap.toFixed(2)}pp`}
                aria-pressed={onPick ? pinned : undefined}
                onMouseEnter={() => setHover(d.key)}
                onFocus={() => setHover(d.key)}
                onBlur={() => { setHover(null); hide() }}
                onClick={onPick ? () => onPick(d.key) : undefined}
              >
                <span className="bi-db-lab">{d.label}</span>
              </button>

              {/* 绘图区：连线和两个点都挂在这一层，
                  它的宽度就是"尺"，跨行共用。 */}
              <div className="bi-db-track">
                {/* 连线：长度就是差距。方向由位置差决定，不额外画箭头 ——
                    读者看两个点的相对位置就够，多一个箭头反而乱。 */}
                <div
                  className={'bi-db-link' + (up ? ' up' : ' down')}
                  style={{ left: `${left}%`, width: `${Math.max(width, 0.6)}%` }}
                />

                {/* 基准点：空心。空心=参照物、实心=当前值，
                    这是全图唯一的形状编码，不靠颜色。 */}
                <span
                  className="bi-db-dot is-base"
                  style={{
                    left: `${xa}%`, width: r * 2, height: r * 2,
                  }}
                  aria-hidden="true"
                />
                {/* 本周点：实心，涨用 --bad、跌用 --good */}
                <span
                  className={'bi-db-dot is-now' + (up ? ' up' : ' down') + (on ? ' on' : '')}
                  style={{
                    left: `${xb}%`, width: r * 2, height: r * 2,
                  }}
                  aria-hidden="true"
                />
              </div>

              {/* 右侧：差距 + z。放最右一列对齐，
                  这样 4 行的差距可以直接上下扫，不用跟着点跑。 */}
              <div className="bi-db-val">
                <b className={up ? 't-bad' : 't-good'}>
                  {d.gap > 0 ? '+' : ''}{d.gap.toFixed(2)}pp
                </b>
                {d.z !== undefined && <em>z {d.z.toFixed(2)}</em>}
              </div>
            </div>
          )
        })}
      </div>

      <div className="bi-db-legend">
        <span><i className="bi-db-lg is-base" />基准</span>
        <span><i className="bi-db-lg is-now up" />本周 · 高于基准</span>
        <span><i className="bi-db-lg is-now down" />本周 · 低于基准</span>
        <span className="bi-db-lg-note">点的大小 = 订单量 · 连线长度 = 差距</span>
      </div>
      {el}
    </div>
  )
}
