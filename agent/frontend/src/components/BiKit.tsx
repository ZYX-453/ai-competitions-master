/* ===========================================================================
 * BI 交互组件
 *
 * 【这一组件和 PageKit 的分工】
 * PageKit 负责"一块数据的版面"（卡片、KPI 格、空态、错误）。
 * 这里负责"让人能动手看数据"：切指标、排序、悬停读数、点选联动、热力矩阵。
 *
 * 【三条硬约束，都是为了守住"前端不算数"】
 *
 * 1. 组件只接受**已经算好的**数字。Bar 的 pct 是 0–100 的成品，
 *    不是原始值 —— 一旦让组件自己除分母，口径就有了第二份实现。
 *    上一版左栏写死"64.7k"、退货率漏乘 100，都是"前端碰了数"带来的。
 *
 * 2. 排序可以放在前端（只改顺序，不改值），筛选不行（筛选要换分母）。
 *    所以类的切换走服务端请求，表内排序走本地。边界在"有没有改变占比"。
 *
 * 3. 视觉编码的**阈值也由后端给**（比如热力图的 scale_pp）。
 *    颜色深浅会直接左右读者"觉得哪块最疼"，那是个判断，不是装饰。
 *    前端自己拍一个满色阈值，换份数据同一块红的含义就变了，
 *    而图上没有任何提示。
 * =========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'

/* ---------------------------------------------------------------- Tooltip */

interface TipState { x: number; y: number; node: ReactNode; above?: boolean }

/**
 * 跟随鼠标的读数浮层。
 *
 * 【为什么不用原生的 <title>】
 * title 有 1 秒左右延迟、不能排版、跟着系统主题走（深色页面上弹出白框）。
 * 而这套界面里 tooltip 要装的是"这一格到底多少、比基线高多少"
 * ——那是读数，不是辅助说明，必须即时且可控。
 *
 * 【为什么用 portal 挂到 body】
 * 图区里常有 overflow:hidden 和 transform，fixed 定位在那种祖先下会失效，
 * tooltip 会被裁在卡片里。portal 到 body 是不受任何祖先影响的做法。
 */
export function useTip() {
  const [tip, setTip] = useState<TipState | null>(null)

  /* 【v4.6：允许指定"优先放在光标上方"】
     趋势图只有 220px 高，浮层本身约 150px。默认的"右下 14px"
     在趋势图里会正好盖住光标右侧那一段折线 —— 而那一段恰恰是
     读者接着要看的走势。
     热力图不需要（格子小、下方就是图例），所以做成可选参数，
     不传时行为完全不变。 */
  const show = useCallback(
    (e: { clientX: number; clientY: number }, node: ReactNode, above = false) => {
      setTip({ x: e.clientX, y: e.clientY, node, above })
    },
    [],
  )
  const hide = useCallback(() => setTip(null), [])

  // 【滚轮/滚动时要关掉】
  // 浮层是 fixed 定位，页面一滚它就停在原地而鼠标已经不在那一格上了，
  // 看起来像"卡住了"。滚动即关闭最省事也最符合直觉。
  useEffect(() => {
    if (!tip) return
    const off = () => setTip(null)
    window.addEventListener('scroll', off, true)
    return () => window.removeEventListener('scroll', off, true)
  }, [tip])

  const el = tip
    ? createPortal(
        <div
          className="bi-tip"
          style={{
            // 右下偏移 14px，避免被鼠标本身遮住
            left: Math.min(tip.x + 14, window.innerWidth - 260),
            /* above 时不写死高度（内容行数会变），用 translateY(-100%)
               让它自己往上翻，再留 14px 给光标。
               下限 160 是估的浮层最大高度 + 8px 边距：真实高度由内容决定，
               但"翻出视口顶部"比"离光标远一点"严重得多 ——
               前者是内容看不到，后者只是看起来偏上。 */
            top: tip.above
              ? Math.max(tip.y - 14, 160)
              : Math.min(tip.y + 14, window.innerHeight - 140),
            transform: tip.above ? 'translateY(-100%)' : undefined,
            /* 翻到上方时，左对齐也要收一下，别在窄屏上被右边切掉 */
            maxWidth: 260,
          }}
        >
          {tip.node}
        </div>,
        document.body,
      )
    : null

  return { show, hide, el }
}

/* ---------------------------------------------------------------- 分段控件 */

/**
 * 指标/排序切换器。
 *
 * 【为什么用分段控件而不是下拉框】
 * 选项只有 2–4 个，全展开让人一眼看见"还有哪些口径可选"；
 * 下拉框会把选项藏起来，用户不知道能换，也就不会去换。
 * BI 里的维度切换首先得让人知道"可切"。
 */
export function Segmented<T extends string>({
  value, options, onChange, label,
}: {
  value: T
  options: { key: T; label: string }[]
  onChange: (v: T) => void
  label?: string
}) {
  return (
    <div className="bi-seg-wrap">
      {label && <span className="bi-seg-label">{label}</span>}
      <div className="bi-seg" role="tablist">
        {options.map((o) => (
          <button
            key={o.key}
            role="tab"
            aria-selected={o.key === value}
            className={`bi-seg-i${o.key === value ? ' on' : ''}`}
            onClick={() => onChange(o.key)}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 横向条形 */

export interface BarItem {
  key: string
  label: string
  /** 主值（0–100 的成品百分比或已归一到 0–100 的长度），后端给 */
  pct: number
  /** 显示在主值位置的文字 */
  text: string
  /** 次要值（用于双条对比，比如"近12周"）；不给就只画一条 */
  pct2?: number
  text2?: string
  /** 语义色调：正向恶化用 bad，改善用 good，中性用 accent */
  /* 【v4 新增 hi/mid/lo 三档灰】
     灰阶方案里，条形的"颜色"不再表达语义（红=坏），而是表达**量级**：
     越大的值越亮。这样读者扫一眼就知道哪几根最重，不需要先学颜色含义。
     旧的语义 tone 保留，用于确实需要区分方向的场景（如热力图图例）。 */
  tone?: 'hi' | 'mid' | 'lo' | 'bad' | 'good' | 'accent' | 'info' | 'warn'
  /** 悬停浮层内容 */
  tip?: ReactNode
  /** 点一下干什么 */
  onClick?: () => void
  /** 高亮（当前选中项） */
  active?: boolean
}

/**
 * 横向条形列表。
 *
 * 【为什么用横向条形而不是竖向柱状图】
 * 类目名/原因名是中文短语，竖柱的 x 轴标签要么斜排要么截断，
 * 中文斜排基本不可读。横向条形把标签放在左边整行，天然可读，
 * 而且能容纳"数值 + 变化量"两列对齐——这才是审阅数据的姿势。
 *
 * 【为什么不做成"共用同一个 x 轴刻度"的严格图表】
 * 这四个类目的差距是 7% vs 28%，共用刻度会让小值几乎看不见；
 * 各自归一化又会让读者误以为可以横向比较长度。
 * 折中：条长按**当前列表最大值**归一（注释里写明），
 * 并在每条右侧给出精确数值。读者看长度找排序，看数字读量级。
 */
export function HBars({
  items, max = 100, note,
}: {
  items: BarItem[]
  /** 归一化分母，由调用方给（通常是列表里的最大值），不要在组件内猜 */
  max?: number
  note?: ReactNode
}) {
  const { show, hide, el } = useTip()
  const denom = max > 0 ? max : 1

  return (
    <div className="bi-bars">
      {items.map((it) => {
        const len = Math.max(0, Math.min(100, (it.pct / denom) * 100))
        const len2 = it.pct2 === undefined
          ? null
          : Math.max(0, Math.min(100, (it.pct2 / denom) * 100))
        return (
          <div
            key={it.key}
            className={`bi-bar-row${it.active ? ' on' : ''}${it.onClick ? ' clickable' : ''}`}
            /* 【可点击的行必须是键盘可达的】
               这些行点了会切类目 / 跳原因治理，但它们是 div，
               默认既不可聚焦也不响应回车。键盘用户只能看着它们，
               鼠标用户却能点 —— 同一个界面两种权限，不合理。

               只在真的可点时才挂 role/tabIndex：
               纯展示行（没有 onClick）不该进 Tab 序列，
               否则 Tab 会在无意义的地方停住，反而更烦。 */
            role={it.onClick ? 'button' : undefined}
            tabIndex={it.onClick ? 0 : undefined}
            aria-label={it.onClick ? `${it.label}（点击查看）` : undefined}
            onKeyDown={it.onClick
              ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()      // 空格默认会滚动页面，必须拦
                  it.onClick!()
                }
              }
              : undefined}
            onMouseMove={it.tip ? (e) => show(e, it.tip) : undefined}
            onMouseLeave={it.tip ? hide : undefined}
            onClick={it.onClick}
          >
            <div className="bi-bar-l" title={it.label}>{it.label}</div>
            <div className="bi-bar-track">
              {/* 双条对比：底条是全期，顶条是近 12 周，同一根轴上叠着画 */}
              {len2 !== null && (
                <div
                  className="bi-bar-f is-ghost"
                  style={{ width: `${len2}%` }}
                />
              )}
              <div
                className={`bi-bar-f t-${it.tone || 'accent'}`}
                style={{ width: `${len}%` }}
              />
            </div>
            <div className="bi-bar-v">
              <span className="bi-bar-main">{it.text}</span>
              {it.text2 && <span className="bi-bar-sub">{it.text2}</span>}
            </div>
          </div>
        )
      })}
      {note && <div className="bi-bars-note">{note}</div>}
      {el}
    </div>
  )
}

/* ---------------------------------------------------------------- 热力矩阵 */

export interface HeatCell {
  week: number
  rr: number | null
  dev_pp: number | null
  z: number | null
  is_anomaly: boolean
  trivial_sig: boolean
}

export interface HeatRow {
  category: string
  base_rr: number | null
  cells: HeatCell[]
}

/**
 * 类目 × 周 热力矩阵。
 *
 * 【这一块是整页最能一眼看出"哪里、什么时候"的东西】
 * 折线图回答"整体怎么走"，但它把四个类目压成了一条线：
 * 服饰 28% 和数码 10% 混在一起，异常周互相抵消。
 * 热力图把"类目"和"周"两个维度同时摊开，深色格子就是需要问的地方。
 *
 * 【上色键是二项 z，不是偏离的百分点】
 * 第一版按 dev_pp 上色，结果服饰（基线 28%）整行深红、数码（基线 10%）
 * 几乎无色 —— 因为服饰周间波动天然就大，±3pp 是它的常态；
 * 而数码 ±3pp 已经严重异常。**同一把尺子量不同尺度的类目，
 * 读者看到的其实是"类目退货率不同"这个废话，而不是"哪一周出事了"。**
 * z 是"偏离了几个标准误"，已按各自基线率与样本量归一，跨类目可比，
 * 而且和双门槛判定的第一条同源 —— 不会出现"图很红但判定说没事"。
 *
 * 【颜色表示方向，描边表示判定】
 * 两条视觉通道分开用：颜色说"往哪个方向偏了多少"，
 * 描边说"这件事值不值得管"。合成一条通道的话，
 * 深红和更红就没法区分了（而它们含义完全不同：
 * 一个是"偏得多但都是噪声"，一个是"真该开会")。
 */
export function HeatGrid({
  rows, weeks, scaleZ, sigZ, onPick, highlightWeeks,
}: {
  rows: HeatRow[]
  weeks: number[]
  /** 满色阈值（z），后端给 */
  scaleZ: number
  /** 统计显著线（z=2），后端给，仅用于标尺 */
  sigZ: number
  onPick: (week: number, category: string) => void
  /** 需要高亮的周（来自上方趋势图的悬停/框选），不传则不高亮 */
  highlightWeeks?: number[] | null
}) {
  const { show, hide, el } = useTip()
  const scale = scaleZ > 0 ? scaleZ : 4
  /* 【联动高亮用 Set 而不是数组 includes】
     52 周 × 5 行 = 260 次判断，每次 render 都跑。
     用数组的话是 O(n·m)，框选 20 周就是 5200 次比较；
     Set 是一次性建好、O(1) 查询。数据量不大，但这段在鼠标移动时
     每帧都会重跑，省下来的是"拖动时候不卡"。
     只在 highlightWeeks 真的变化时重建，避免每帧 new Set。 */
  const hl = useMemo(
    () => (highlightWeeks && highlightWeeks.length ? new Set(highlightWeeks) : null),
    [highlightWeeks],
  )

  /* 【热力格的键盘可达：roving tabindex】
     每一次点击都走 onPick → 跳去归因页，tooltip 上也明写着"点击进归因 ›"。
     但 <div> 不在默认可聚焦列表里，所以这块是**鼠标专享**的入口。

     直接给 208 格都加 tabIndex=0 是错的：Tab 序列会变成 208 次停顿，
     键盘用户想走到下面的图得按到手软 —— 可达性没解决，可用性反而更差。
     正确做法是整块只保留**一个** Tab 停靠点，进入之后用方向键在同一平面内移动
     （WAI-ARIA 对 grid 的标准做法）。所以：只有"游标格"是 tabIndex=0，
     其余 -1；方向键改游标并 focus；空格/回车触发与点击相同的动作。 */
  const gridRef = useRef<HTMLDivElement>(null)
  const [cur, setCur] = useState<{ w: number; cat: string } | null>(null)

  /* 默认停靠点必须是**真有数据的格子**：cell 可能缺某周（is-empty），
     如果写死 rows[0] + weeks[0] 恰好落在空格上，整块就没有任何 tabIndex=0，
     键盘用户直接跨过这张图 —— 比不可达更隐蔽。 */
  const home = useMemo(() => {
    for (const r of rows) {
      for (const w of weeks) {
        if (r.cells.some((x) => x.week === w)) return { w, cat: r.category }
      }
    }
    return null
  }, [rows, weeks])
  /* 【游标也可能失效，不只是"还没设过"】
     同一个坑有两个入口：cur 为 null（没聚焦过）好办，`cur ?? home` 就兜住了；
     但 cur 指向的格子若在新数据里已经不存在（换了类目/换了周区间），
     它仍然非空、仍然"看起来有效"，结果整块一个 tabIndex=0 都没有。
     所以必须显式校验 cur 是否还落在当前 rows 里，失效就退回 home。 */
  const curAlive = useMemo(() => {
    if (!cur) return false
    const r = rows.find((x) => x.category === cur.cat)
    return !!r && r.cells.some((x) => x.week === cur.w)
  }, [cur, rows])
  const tab = curAlive ? cur : home

  /** 方向键在同一平面内移动焦点，跳过没有数据的格子。 */
  const moveFocus = (e: React.KeyboardEvent, ri: number, ci: number) => {
    const dir: Record<string, [number, number]> = {
      ArrowLeft: [0, -1], ArrowRight: [0, 1], ArrowUp: [-1, 0], ArrowDown: [1, 0],
    }
    const d = dir[e.key]
    if (!d) return false
    e.preventDefault()
    const grid = gridRef.current
    if (!grid) return true
    let nr = ri + d[0]
    let nc = ci + d[1]
    /* 越界就停在原地（不 wrap）：热力图是二维矩阵，
       从最右列按右键跳回最左列会让人瞬间失去方位感。 */
    while (nr >= 0 && nr < rows.length && nc >= 0 && nc < weeks.length) {
      const t = grid.querySelector<HTMLElement>(`[data-r="${nr}"][data-c="${nc}"]`)
      if (t) {
        setCur({ w: weeks[nc], cat: rows[nr].category })
        t.focus()
        return true
      }
      nr += d[0]
      nc += d[1]
    }
    return true
  }

  /** z → 背景色。正=红、负=青绿，强度按 |z|/scale 饱和。 */
  const color = (z: number | null): string => {
    if (z === null || !isFinite(z)) return 'transparent'
    const t = Math.min(1, Math.abs(z) / scale)
    /* 【v4：热力图 => "亮度即显著性，色相只表方向"】
       这是整页唯一的大面积着色区（4×52=208 格），它决定了全屏冷暖观感。

       关键约束：**格子必须比背景亮**。深底上叠暗色会让格子看起来"凹进墙里"，
       读者会读成"这里没有数据"，而不是"这里异常"。所以底色不能往下压，
       只能在背景 (#151517) 之上叠浅色。

       两个通道：
         亮度 = 显著性（|z| 越大越亮）   ← 主通道，纯灰阶语言
         色相 = 方向（恶化极淡暖调 / 改善极淡冷调）← 辅通道，饱和度≤0.42

       数值经过试算：
         · z=0（持平）   亮度 ~0.06，几乎融进背景
         · z=2（显著线） 亮度 ~0.26，明显可见但仍是灰
         · z=4（满色）   亮度 ~0.46，全图最亮
       色相偏移控制在 (255,228,224) 与 (222,238,232) —— 两者都是"接近白的灰"，
       色差极小，铺满 208 格也不会让画面变脏。 */
    if (z > 0) {
      const a = 0.06 + t * 0.40
      return `rgba(255, 226, 221, ${a.toFixed(3)})`   // 暖白偏：恶化
    }
    const a = 0.05 + t * 0.34
    return `rgba(220, 238, 233, ${a.toFixed(3)})`     // 冷白偏：改善
  }

  /* 【强度单独暴露一份，专门为高对比度模式准备】
     实测：forced-colors 会把 background-color **整个替换**成系统色，
     连 rgba 里的 alpha 一起抹掉（读回来是 rgb(0,0,0)，不透明）。
     所以"靠半透明表达深浅"在 HC 下完全失效，208 格会变成实心黑块。

     forced-colors **不会**覆盖 opacity，所以它是那个模式下唯一
     还能编码"程度"的通道。这里把强度单独算一份写进 CSS 变量，
     由 .bi-heat-c::before 的 opacity 消费（见 app.css）。

     注意：数值必须和上面 color() 的 alpha 同源，
     否则正常模式和高对比模式会画出两种强度，看起来是两张图。 */
  const strength = (z: number | null): number => {
    if (z === null || !isFinite(z)) return 0
    const t = Math.min(1, Math.abs(z) / scale)
    return z > 0 ? 0.06 + t * 0.40 : 0.05 + t * 0.34
  }

  return (
    <div className="bi-heat-wrap">
      <div className="bi-heat" ref={gridRef}>
        {/* 周次表头：每 4 周标一个，标满会挤成一片 */}
        <div className="bi-heat-row is-head">
          <div className="bi-heat-label" />
          {weeks.map((w) => (
            <div key={w} className={`bi-heat-wk${hl?.has(w) ? ' is-link' : ''}`}>
              {w % 4 === 1 ? w : ''}
            </div>
          ))}
        </div>

        {rows.map((r, ri) => (
          <div key={r.category} className="bi-heat-row">
            <div className="bi-heat-label">
              <span className="bi-heat-cat">{r.category}</span>
              {r.base_rr !== null && (
                <span className="bi-heat-base">{r.base_rr.toFixed(1)}%</span>
              )}
            </div>
            {weeks.map((w, ci) => {
              const c = r.cells.find((x) => x.week === w)
              const on = hl?.has(w) ?? false
              if (!c) return <div key={w} className="bi-heat-c is-empty" />
              /* 读屏读的是这一格的**结论**，不是"格子"这个形状。
                 视觉上三行信息（退货率 / 偏离 / 判定）靠 tooltip 折叠着，
                 键盘和读屏没有 tooltip，必须把同样的内容塞进可访问名。 */
              const rrS = c.rr === null ? '无数据' : `${c.rr.toFixed(2)}%`
              const devS = c.dev_pp === null
                ? '基线未知'
                : `比基线 ${c.dev_pp > 0 ? '+' : ''}${c.dev_pp.toFixed(2)}pp`
              const verdict = c.is_anomaly
                ? '异常，需要解释'
                : c.trivial_sig ? '统计显著但幅度不够' : '正常波动'
              return (
                <div
                  key={w}
                  data-r={ri}
                  data-c={ci}
                  role="button"
                  aria-label={`${r.category}，第 ${w} 周，退货率 ${rrS}，${devS}，${verdict}。回车查看归因`}
                  tabIndex={tab && tab.w === w && tab.cat === r.category ? 0 : -1}
                  onFocus={() => setCur({ w, cat: r.category })}
                  onKeyDown={(e) => {
                    if (moveFocus(e, ri, ci)) return
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      onPick(w, r.category)
                    }
                  }}
                  className={`bi-heat-c${c.is_anomaly ? ' is-anom' : ''}${c.trivial_sig ? ' is-trivial' : ''}${on ? ' is-link' : ''}`}
                  /* 【颜色和强度分两个通道】
                     正常情况下着色层自己带 alpha，强度变量用不上；
                     但在 forced-colors 下 background-color 会被整个替换成
                     系统色、alpha 一起抹掉，208 格会全变成实心黑块。
                     所以强度另存一份到 --heat-op，HC 模式改用 opacity 渲染。 */
                  style={{
                    ['--heat-bg' as string]: color(c.z),
                    ['--heat-op' as string]: String(Math.max(strength(c.z), 0.02)),
                  } as React.CSSProperties}
                  onMouseMove={(e) => show(e, (
                    <div>
                      <div className="bi-tip-h">{r.category} · 第 {w} 周</div>
                      <div className="bi-tip-row">
                        退货率 <b>{c.rr === null ? '—' : c.rr.toFixed(2) + '%'}</b>
                      </div>
                      <div className="bi-tip-row">
                        比本类目基线
                        <b className={c.dev_pp !== null && c.dev_pp > 0 ? 't-bad' : 't-good'}>
                          {c.dev_pp === null ? '—' : `${c.dev_pp > 0 ? '+' : ''}${c.dev_pp.toFixed(2)}pp`}
                        </b>
                      </div>
                      <div className="bi-tip-row">
                        z 值 <b>{c.z === null ? '—' : c.z.toFixed(2)}</b>
                      </div>
                      <div className="bi-tip-row dim">
                        {c.is_anomaly
                          ? '双门槛判定：异常，需要解释'
                          : c.trivial_sig
                            ? '统计显著但幅度不够，不需要动手'
                            : '正常波动'}
                      </div>
                      <div className="bi-tip-go">点击进归因 ›</div>
                    </div>
                  ))}
                  onMouseLeave={hide}
                  onClick={() => onPick(w, r.category)}
                />
              )
            })}
          </div>
        ))}
      </div>

      {/* 【图例不能省】颜色是这套图唯一的语义载体，没有图例等于没有单位 */}
      <div className="bi-heat-legend">
        {/* 【图例的色块必须与 color() 的实际输出同源】
            这里写死 rgba 是不理想的，但比"从函数里反推"清晰——
            关键是三个值要和上面的 0.06+t*0.40 / 0.05+t*0.34 对得上，
            取 z=scale（满色）那一档。改函数时必须同步改这里。 */}
        <span className="bi-lg-item">
          <i style={{ background: 'rgba(220,238,233,.39)' }} />
          低于基线
        </span>
        <span className="bi-lg-item">
          <i style={{ background: 'rgba(255,255,255,.06)' }} />
          持平
        </span>
        <span className="bi-lg-item">
          <i style={{ background: 'rgba(255,226,221,.46)' }} />
          高于基线
        </span>
        <span className="bi-lg-sep" />
        <span className="bi-lg-item">
          <i className="is-anom-box" />
          双门槛判定为异常
        </span>
        <span className="bi-lg-sep" />
        <span className="bi-lg-note">
          色深 = 二项 z（跨类目可比），z={sigZ.toFixed(1)} 为显著线，{scale.toFixed(1)} 饱和
        </span>
      </div>
      {el}
    </div>
  )
}

/* ---------------------------------------------------------------- 可排序表 */

export type SortDir = 'asc' | 'desc'

export interface Col<T> {
  key: string
  label: string
  /** 数值列会右对齐并用等宽字体 */
  num?: boolean
  /** 取排序用的原始值（不是显示文字） */
  sortVal?: (row: T) => number
  /** 渲染单元格 */
  render: (row: T) => ReactNode
  width?: string
}

/**
 * 可排序表格。
 *
 * 【排序放在前端的理由】
 * 排序只改行的先后，不改变任何一个数字、也不改变分母，
 * 所以它不违反"前端不算数"。而切换类目会改变占比的分母，
 * 那个必须回服务端 —— 两者的界线在"动没动分母"。
 *
 * 【默认排序方向按列类型猜】
 * 数值列默认降序（用户点"损失"是想看最大的那几个），
 * 文本列默认升序。让用户点两次才能得到常见视图，是很常见的体验缺陷。
 */
export function SortTable<T>({
  cols, rows, rowKey, onRowClick, defaultSort, emptyHint,
}: {
  cols: Col<T>[]
  rows: T[]
  rowKey: (r: T) => string
  onRowClick?: (r: T) => void
  defaultSort?: { key: string; dir: SortDir }
  emptyHint?: ReactNode
}) {
  const [sort, setSort] = useState<{ key: string; dir: SortDir } | null>(defaultSort || null)

  const sorted = (() => {
    if (!sort) return rows
    const col = cols.find((c) => c.key === sort.key)
    if (!col?.sortVal) return rows
    const arr = [...rows]
    arr.sort((a, b) => {
      const va = col.sortVal!(a)
      const vb = col.sortVal!(b)
      if (va === vb) return 0
      return (va < vb ? -1 : 1) * (sort.dir === 'asc' ? 1 : -1)
    })
    return arr
  })()

  const click = (c: Col<T>) => {
    if (!c.sortVal) return
    setSort((cur) => {
      if (!cur || cur.key !== c.key) {
        // 首次点击：数值列降序（想看最大），文本列升序
        return { key: c.key, dir: c.num ? 'desc' : 'asc' }
      }
      if (cur.dir === (c.num ? 'desc' : 'asc')) {
        return { key: c.key, dir: c.num ? 'asc' : 'desc' }
      }
      return null          // 第三次点回到原始顺序
    })
  }

  if (!rows.length) {
    return <div className="bi-empty">{emptyHint || '没有可显示的行'}</div>
  }

  return (
    <div className="pg-table-wrap">
      <table className="pg-table bi-table">
        <thead>
          <tr>
            {cols.map((c) => (
              /* 【scope="col" 不是可选的】
                 没有它，读屏软件读单元格时只能说"第 3 列"，说不出
                 "退货率 28.33%"。7 个 th 原本一个 scope 都没有 ——
                 表格在视觉上很好读，在听觉上是一串没有标签的数字。

                 可排序表头还要 aria-sort：它告诉读屏"当前按这一列升序"。
                 原来只有一个 ↑↓ 箭头符号，纯视觉信息。

                 【为什么是 tabIndex 而不是 role="button"】
                 把 th 改成 role="button" 会**丢掉 columnheader 语义** ——
                 读屏就不再把这格当表头，单元格的列名又没了。
                 正确做法：保留表头语义 + 单独补上键盘可达性。
                 这是 th 这种"既表意又能动"的元素的标准处理方式。 */
              <th
                key={c.key}
                scope="col"
                aria-sort={
                  /* 【可排序列必须显式给三种值之一】
                     ascending / descending / none。
                     只给当前排序列设、其他列不设，读屏会把"没属性"和
                     "不可排序"混在一起 —— 用户听到列名后不知道这列能不能点。
                     所以：可排序 → 三态之一；不可排序 → 完全不设（undefined）。 */
                  c.sortVal
                    ? (sort?.key === c.key
                      ? (sort.dir === 'asc' ? 'ascending' : 'descending')
                      : 'none')
                    : undefined
                }
                tabIndex={c.sortVal ? 0 : undefined}
                onKeyDown={c.sortVal
                  ? (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      click(c)
                    }
                  }
                  : undefined}
                className={`${c.num ? 'r' : ''}${c.sortVal ? ' bi-th-sort' : ''}`}
                style={c.width ? { width: c.width } : undefined}
                /* 【不可排序的列不要挂 onClick】
                   原来这里无条件挂 `onClick={() => click(c)}`，靠 click() 内部
                   `if (!c.sortVal) return` 兜底 —— 行为上没错，但那是一个
                   "有处理器、点下去什么也不做"的表头：审计工具按"有 onClick
                   = 可交互"来判键盘可达性时会把它们全报成缺陷，
                   而它们其实什么都不是。可交互暗示要收干净，别靠运行时守卫。 */
                onClick={c.sortVal ? () => click(c) : undefined}
              >
                {c.label}
                {c.sortVal && (
                  <span className={`bi-sort-mark${sort?.key === c.key ? ' on' : ''}`}>
                    {sort?.key === c.key ? (sort.dir === 'asc' ? '↑' : '↓') : '↕'}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr
              key={rowKey(r)}
              className={onRowClick ? 'clickable' : ''}
              /* 【可点行必须 Tab 得到】
                 这一行挂的是真 onClick（点开周次明细），但 <tr> 不在浏览器的
                 默认可聚焦列表里 —— 鼠标点得到、键盘永远够不着，
                 而这类缺陷不会报错、不会变形，鼠标用户一辈子碰不到。
                 tabIndex=0 + Enter/空格是行级"按钮"的最小完整实现。
                 不给 role="button"：那会让读屏丢掉 tr 的行语义，
                 表头与单元格的对应关系随之断裂 —— 补可达性不该以毁语义为代价。 */
              tabIndex={onRowClick ? 0 : undefined}
              onClick={onRowClick ? () => onRowClick(r) : undefined}
              onKeyDown={onRowClick
                ? (e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    onRowClick(r)
                  }
                }
                : undefined}
            >
              {cols.map((c) => (
                <td key={c.key} className={c.num ? 'r num' : ''}>{c.render(r)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ---------------------------------------------------------------- 联动筛选条 */

/**
 * 类目切换条（BI 的"维度选择器"）。
 *
 * 【为什么"全店"要单独占一格】
 * 它是基准，不是第五个类目。选中类目之后其他块都按类目重算，
 * 读者需要一个"退回去看整体"的出口，否则会迷路在类目里。
 */
export function ScopePick({
  categories, value, onChange, latest,
}: {
  categories: string[]
  value: string | null
  onChange: (v: string | null) => void
  latest?: ReactNode
}) {
  return (
    <div className="bi-scope">
      <span className="bi-scope-k">范围</span>
      <div className="bi-scope-list">
        <button
          className={`bi-scope-i${value === null ? ' on' : ''}`}
          onClick={() => onChange(null)}
        >
          全店
        </button>
        {categories.map((c) => (
          <button
            key={c}
            className={`bi-scope-i${value === c ? ' on' : ''}`}
            onClick={() => onChange(c)}
          >
            {c}
          </button>
        ))}
      </div>
      {latest && <div className="bi-scope-right">{latest}</div>}
    </div>
  )
}

/* ---------------------------------------------------------------- 迷你柱 */

/**
 * 一行长度极小的柱条，用于表格内的"量级提示"。
 * 不做坐标轴、不做 tooltip —— 它只负责让眼睛快速找到最大那行。
 */
export function MiniBar({
  pct, tone = 'accent',
}: { pct: number; tone?: 'accent' | 'bad' | 'good' | 'info' }) {
  const ref = useRef<HTMLDivElement>(null)
  return (
    <div className="bi-mini" ref={ref}>
      <div
        className={`bi-mini-f t-${tone}`}
        style={{ width: `${Math.max(0, Math.min(100, pct))}%` }}
      />
    </div>
  )
}
