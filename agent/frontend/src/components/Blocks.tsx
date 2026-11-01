import type {
  Block, ContrastBlock, DecompBlock, KpiItem, PersonaBlock, WordCloudBlock,
} from '../lib/types'

/** 回答主体：按 block.type 分派渲染。
 *
 * 为什么后端返回"块"而不是直接返回 HTML：
 * 1. 前端能用真正的组件渲染（可交互、可展开、可 hover 看来源）
 * 2. 同样的数据将来能复用到 PDF / 周报，不用二次解析
 * 3. 渲染逻辑集中在这里，改样式不用动后端
 */
export function Blocks({ blocks }: { blocks: Block[] }) {
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.type) {
          case 'kpi':
            return <KpiRow key={i} items={b.items as KpiItem[]} />
          case 'chain':
            return <ChainBlock key={i} block={b} />
          case 'decomp':
            return <Decomp key={i} block={b as unknown as DecompBlock} />
          case 'table':
            return <Table key={i} block={b} />
          case 'bars':
            return <Bars key={i} block={b} />
          case 'quotes':
            return <Quotes key={i} block={b} />
          case 'actions':
            return <Actions key={i} block={b} />
          case 'curve':
            return <Curve key={i} block={b} />
          case 'trend':
            return <Trend key={i} block={b} />
          /* ---- v3.1 评论分析四件套 ---- */
          case 'wordcloud':
            return <WordCloud key={i} block={b as unknown as WordCloudBlock} />
          case 'persona':
            return <Persona key={i} block={b as unknown as PersonaBlock} />
          case 'contrast':
            return <Contrast key={i} block={b as unknown as ContrastBlock} />
          default:
            return null
        }
      })}
    </>
  )
}

function Card({ title, note, children }: { title?: string; note?: string; children: React.ReactNode }) {
  return (
    <div className="card">
      {/* 渐变定义提到全局一次。
          之前写在 Curve 组件内部，结果 Trend 组件引用同一个 id 时
          因为 DOM 里还没有 Curve 而拿不到，填充色直接变黑。
          SVG 的 defs 是按 id 全局查找的，不是按组件作用域。 */}
      <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden>
        <defs>
          <linearGradient id="curveFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.22" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
      </svg>
      {title && (
        <div className="card-head">
          <h3 className="card-title">{title}</h3>
        </div>
      )}
      <div className="card-body">
        {children}
        {note && <div className="tiny muted" style={{ marginTop: 10, lineHeight: 1.6 }}>{note}</div>}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- KPI */
function KpiRow({ items }: { items: KpiItem[] }) {
  return (
    <div className="card">
      <div className="card-body" style={{ padding: 0 }}>
        <div className="kpi-row" style={{ border: 'none', borderRadius: 0 }}>
          {items.map((k, i) => (
            <div className={`kpi ${k.tone}`} key={i}>
              <div className="kpi-k">{k.label}</div>
              <div className="kpi-v">{k.value}</div>
              <div className="kpi-d">{k.delta}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 归因链 */
function ChainBlock({ block }: { block: Block }) {
  const steps = block.steps as { label: string; text: string; tone: string }[]
  return (
    <Card title={(block.title as string) ?? '归因链'}>
      <div className="tl">
        {steps.map((s, i) => (
          <div className={`tl-item ${s.tone}`} key={i}>
            <div className="tl-dot"><i /></div>
            <div className="tl-l">{s.label}</div>
            <div className="tl-t" dangerouslySetInnerHTML={{ __html: markNum(s.text) }} />
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- 分解条 */
function Decomp({ block }: { block: DecompBlock }) {
  // 结构效应可能是负数（抵消）。堆叠条只画正的部分，负的单独标注——
  // 负值画进堆叠条会让人以为"总涨幅变小了却还占一段"。
  const pos = block.parts.filter((p) => p.value > 0)
  const neg = block.parts.filter((p) => p.value < 0)
  const totalPos = pos.reduce((s, p) => s + p.value, 0) || 1

  return (
    <Card title={block.title}>
      <div className="decomp">
        <div className="decomp-top">
          <span className="decomp-total">
            {block.actual >= 0 ? '+' : ''}{block.actual.toFixed(2)}pp
            <small>实际涨幅</small>
          </span>
          {neg.length > 0 && (
            <span className="tiny muted">
              {neg.map((n) => `${n.label} ${n.value.toFixed(2)}pp`).join(' · ')}
            </span>
          )}
        </div>

        <div className="stack">
          {pos.map((p, i) => (
            <div
              key={i}
              className={`stack-seg ${p.tone}`}
              style={{ width: `${Math.max((p.value / totalPos) * 100, 6)}%` }}
              title={`${p.label} ${p.value.toFixed(2)}pp`}
            >
              {/* 【条内标注只在"需要靠它区分色块"时才出现】
                  原来无条件写 `${p.value.toFixed(1)}`，两个毛病：
                  ① 只有一个正分段时，条占满 100%，图例就在下一行写着
                     "促销机制 +3.93pp"，条内还浮一个孤零零的 "3.9" —— 既没单位
                     又丢精度，看起来像是渲染错位；
                  ② 多段时 3.9 / 0.3 这种裸数字也要让人回头数颜色。
                  现在：单段不标（图例够用），多段标"名称"，靠文字区分而不是靠颜色。 */}
              {pos.length > 1 && p.value / totalPos > 0.14 ? p.label : ''}
            </div>
          ))}
        </div>

        <div className="legend">
          {block.parts.map((p, i) => (
            <span className="legend-item" key={i}>
              <span
                className="legend-swatch"
                style={{ background: p.tone === 'primary' ? 'var(--accent)' : '#3a4457' }}
              />
              {p.label}
              <b>{p.value > 0 ? '+' : ''}{p.value.toFixed(2)}pp</b>
            </span>
          ))}
        </div>

        {/* 三个排除性检验：这是"机制识别"最硬的部分 */}
        <div className="mt12" style={{ display: 'grid', gap: 5 }}>
          <Check
            ok={block.paired.pct_sku_increased > 0.6}
            label="同 SKU 配对"
            detail={`${block.paired.n_sku} 个 SKU 中 ${(block.paired.pct_sku_increased * 100).toFixed(0)}% 自身退货倾向上升，均值 +${block.paired.mean_diff_pp.toFixed(2)}pp，t = ${block.paired.t_stat}`}
          />
          <Check
            ok={block.alternatives.pct_sku_discounted < 0.2}
            label="排除降价换量"
            detail={`降价 SKU 占比 ${(block.alternatives.pct_sku_discounted * 100).toFixed(0)}%，价格中位变化 ${block.alternatives.price_change_median_pct.toFixed(2)}%`}
          />
          <Check
            ok={block.alternatives.quality_promo - block.alternatives.quality_base > -0.05}
            label="排除低质选品"
            detail={`促销周质量均值 ${block.alternatives.quality_promo.toFixed(3)} vs 基线 ${block.alternatives.quality_base.toFixed(3)}`}
          />
        </div>

        {block.verdict && (
          <div className="mt12" style={{
            padding: '10px 12px',
            background: 'var(--bg)',
            border: '1px solid var(--accent-line)',
            borderRadius: 'var(--r)',
          }}>
            <div style={{ fontSize: 12.5, color: 'var(--fg)', fontWeight: 600, marginBottom: 4 }}>
              {block.verdict.headline}
            </div>
            <div className="tiny" style={{ color: 'var(--fg-3)', lineHeight: 1.65 }}>
              {block.verdict.implication}
            </div>
          </div>
        )}
      </div>
    </Card>
  )
}

function Check({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 11.5 }}>
      <span
        style={{
          flexShrink: 0,
          width: 14, height: 14,
          borderRadius: 3,
          display: 'grid', placeItems: 'center',
          fontSize: 9,
          marginTop: 1,
          background: ok ? 'var(--accent-dim)' : 'var(--warn-dim)',
          color: ok ? 'var(--accent)' : 'var(--warn)',
          border: `1px solid ${ok ? 'var(--accent-line)' : 'var(--warn-line)'}`,
        }}
      >
        {ok ? '✓' : '!'}
      </span>
      <span style={{ color: 'var(--fg-3)', lineHeight: 1.55 }}>
        <b style={{ color: 'var(--fg-2)', fontWeight: 550 }}>{label}</b>
        <span style={{ margin: '0 5px', color: 'var(--fg-4)' }}>·</span>
        {detail}
      </span>
    </div>
  )
}

/* ---------------------------------------------------------------- 表格 */
function Table({ block }: { block: Block }) {
  const cols = block.columns ?? []
  const rows = block.rows ?? []
  return (
    <Card title={block.title} note={block.note}>
      <div className="tbl-wrap">
        <table className="tbl">
          <thead>
            <tr>{cols.map((c, i) => <th key={i}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- 条形 */
function Bars({ block }: { block: Block }) {
  const items = block.items as { label: string; value: number; share: number; pct: number; extra: string }[]
  return (
    <Card title={block.title}>
      <div className="bars">
        {items.map((x, i) => (
          <div className="bar-row" key={i}>
            <div className="bar-l" title={x.label}>{x.label}</div>
            <div className="bar-track" title={x.extra}>
              <div className="bar-fill" style={{ width: `${Math.max(x.pct * 100, 2)}%` }} />
              <div className="bar-meta">
                <span className="pc">{(x.share * 100).toFixed(1)}%</span>
                <span>{x.value} 条</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- 引用 */
function Quotes({ block }: { block: Block }) {
  const items = block.items as { rating: number; category: string; cause: string; text: string }[]
  return (
    <Card title={block.title}>
      <div className="quotes">
        {items.map((q, i) => (
          <div className="quote" key={i}>
            <div className="quote-meta">
              <span className="stars">{'★'.repeat(q.rating)}</span>
              <span>{q.category}</span>
              <span style={{ color: 'var(--fg-4)' }}>·</span>
              <span>{q.cause}</span>
            </div>
            <div className="quote-t">{q.text}</div>
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- 动作 */
function Actions({ block }: { block: Block }) {
  const items = block.items as { action: string; impact_on: string; owner: string; kpi: string }[]
  return (
    <Card title={block.title}>
      <div className="acts">
        {items.map((a, i) => (
          <div className="act" key={i}>
            <div className="act-n">{i + 1}</div>
            <div>
              <div className="act-a">{a.action}</div>
              <div className="act-tags">
                <span className="tag k">影响</span>
                <span className="tag">{a.impact_on}</span>
                <span className="tag k">责任方</span>
                <span className="tag">{a.owner}</span>
                <span className="tag k">验收</span>
                <span className="tag">{a.kpi}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </Card>
  )
}

/* ---------------------------------------------------------------- 补偿曲线 */
function Curve({ block }: { block: Block }) {
  const pts = block.points as {
    compensation: number; n: number; save_rate: number; avg_gm: number; expected_profit: number
  }[]
  const opt = block.optimal as { compensation: number; expected_profit: number; save_rate: number }
  const conf = block.confound as number

  /* 【这里的两个"坑"都值得记下来】
   *
   * 坑1：preserveAspectRatio="none" 会把文字一起拉伸。
   *      容器宽约 900px、viewBox 宽 300，横向被放大 3 倍；
   *      而纵向几乎不放大。结果文字被横向压扁成一条糊，字号再调也没用。
   *      解法：不用统一 viewBox。SVG 只画线，坐标改用百分比（0–100），
   *      文字改用 HTML 绝对定位——HTML 文字永远不会被拉伸。
   *
   * 坑2：峰值标注放哪里都会撞。
   *      正上方顶到图表上沿；正下方曲线正好穿过（峰值两侧几乎等高）。
   *      解法：不标在点上，而是标在**最右侧留白处**，并用引线指向峰值点。
   */

  const maxY = Math.max(...pts.map((p) => p.expected_profit))
  const minY = Math.min(...pts.map((p) => p.expected_profit))
  const span = Math.max(maxY - minY, 1e-6)

  /* 【x 轴必须按真实档位铺开，不能按数组下标等分】
     这是改正一个会误导人的错误。
     档位是 0 / 5 / 10 / 15 / 20 / 30 / 50，不是等距的。
     原来用 i/(n-1) 等分，于是 ¥30→¥50 这一段（跨度 20 元）
     和 ¥0→¥5 那一段（跨度 5 元）被画成一样宽。
     视觉上会造成两个误判：
     ① 以为 ¥50 只比 ¥30 贵一点，实际贵了 67%；
     ② 看清楚"利润在 ¥30 见顶、到 ¥50 断崖"这件事，
        本来靠的就是横轴尺度，尺度骗人等于结论也骗人。
     现在按 (comp - minC) / (maxC - minC) 映射，¥30→¥50 自然被拉宽。 */
  const minC = pts[0].compensation
  const maxC = pts[pts.length - 1].compensation
  const cSpan = Math.max(maxC - minC, 1e-9)

  const Y_TOP = 12
  const Y_BOT = 86
  /* 右侧留 6%，让最后一个刻度 ¥50 的文字不顶到边框 */
  const X_MAX = 94
  const xPct = (c: number) => ((c - minC) / cSpan) * X_MAX
  const yPct = (v: number) => Y_BOT - ((v - minY) / span) * (Y_BOT - Y_TOP)

  const linePts = pts.map((p) => `${xPct(p.compensation)},${yPct(p.expected_profit)}`).join(' ')
  const optIdx = pts.findIndex((p) => p.compensation === opt.compensation)
  const areaPts = `0,100 ${linePts} ${X_MAX},100`

  return (
    <Card title={block.title} note={block.note as string}>
      {/* 【为什么拆成"刻度列 + 图区"两个盒子，而不是给一个盒子加 padding】
          给父盒加 paddingLeft 后，absolute 子元素的百分比 left 仍然以 padding box 计算，
          而 SVG 用 left:30 起画 —— 两套基准差了 30px，数据点会整体偏左。
          拆开之后，图区是一个干净的无 padding 盒子，SVG、数据点、徽章、x 刻度
          全部以它为基准，坐标系唯一，不可能错位。 */}
      <div style={{ display: 'flex', gap: 0, marginTop: 4 }}>
        {/* y 轴刻度列：只标最大/中位/最小，够定位又不喧宾夺主 */}
        <div style={{ position: 'relative', width: 26, height: 118, flexShrink: 0 }}>
          {[
            { v: maxY, y: Y_TOP },
            { v: (maxY + minY) / 2, y: (Y_TOP + Y_BOT) / 2 },
            { v: minY, y: Y_BOT },
          ].map((t, k) => (
            <div key={`y${k}`} style={{
              position: 'absolute',
              right: 6,
              top: `${t.y}%`,
              transform: 'translateY(-50%)',
              fontSize: 9.5,
              fontFamily: 'var(--mono)',
              color: 'var(--fg-4)',
              whiteSpace: 'nowrap',
              pointerEvents: 'none',
            }}>
              {t.v.toFixed(0)}
            </div>
          ))}
        </div>

        <div style={{ position: 'relative', height: 118, flex: 1, minWidth: 0 }}>
          <svg
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}
          >
          {/* 三条水平网格：顶/中/底，给纵轴一个参照，否则只有一条折线悬在空中 */}
          {[Y_TOP, (Y_TOP + Y_BOT) / 2, Y_BOT].map((y, k) => (
            <line key={k} x1="0" y1={y} x2={X_MAX} y2={y}
              stroke="var(--line-soft)" strokeWidth="0.6"
              strokeDasharray={k === 2 ? undefined : '2 3'}
              vectorEffect="non-scaling-stroke" />
          ))}

          <polygon points={areaPts} fill="url(#curveFill)" />
          <polyline points={linePts} fill="none" stroke="var(--accent)" strokeWidth="1.4"
            vectorEffect="non-scaling-stroke" strokeLinejoin="round" strokeLinecap="round" />
        </svg>

        {/* 数据点用 HTML 画。
            【为什么不用 SVG <circle>】
            viewBox 宽 100、容器宽约 900，preserveAspectRatio="none"
            把圆横向放大 9 倍 —— circle 变成扁椭圆，远看像折线上粘着几块补丁。
            r 改小没用（几何形变，不是描边粗细）。HTML 的 border-radius 不受影响。 */}
        {pts.map((p, i) => {
          const isOpt = i === optIdx
          const s = isOpt ? 8 : 5
          return (
            <div key={i} style={{
              position: 'absolute',
              left: `${xPct(p.compensation)}%`,
              top: `${yPct(p.expected_profit)}%`,
              width: s, height: s, borderRadius: '50%',
              background: isOpt ? 'var(--accent)' : 'var(--panel)',
              border: `1.5px solid ${isOpt ? '#06231f' : '#4a5468'}`,
              transform: 'translate(-50%, -50%)',
              pointerEvents: 'none',
            }} />
          )
        })}

        {/* 峰值标注：HTML 绝对定位，不受 SVG 拉伸影响。
            位置用 translate(-100%) 让徽章左缘贴住峰值点，
            ¥30 右侧还有 ¥50 那一段，这样摆不会盖住右半段曲线。 */}
        {optIdx >= 0 && (
          <div style={{
            position: 'absolute',
            left: `${xPct(pts[optIdx].compensation)}%`,
            top: `${yPct(pts[optIdx].expected_profit)}%`,
            transform: 'translate(-50%, -155%)',
            fontSize: 10,
            fontFamily: 'var(--mono)',
            fontWeight: 600,
            color: 'var(--accent)',
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
            background: 'var(--panel)',
            padding: '1px 4px',
            borderRadius: 3,
            border: '1px solid var(--accent-line)',
          }}>
            峰值 ¥{pts[optIdx].expected_profit.toFixed(1)}
          </div>
        )}

        {/* x 轴刻度：按真实档位定位，与点位同源，天然对齐 */}
        {pts.map((p, i) => (
          <div key={`x${i}`} style={{
            position: 'absolute',
            left: `${xPct(p.compensation)}%`,
            bottom: 0,
            transform: i === 0 ? 'translateX(0)'
              : i === pts.length - 1 ? 'translateX(-100%)' : 'translateX(-50%)',
            fontSize: 10,
            fontFamily: 'var(--mono)',
            color: i === optIdx ? 'var(--accent)' : 'var(--fg-4)',
            fontWeight: i === optIdx ? 600 : 400,
            whiteSpace: 'nowrap',
          }}>
            ¥{p.compensation}
          </div>
        ))}
        </div>
      </div>

      <div className="mt12" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
        <MiniStat k="最优档位" v={`¥${opt.compensation}`} hl />
        <MiniStat k="期望利润" v={`¥${opt.expected_profit.toFixed(2)}`} />
        <MiniStat k="该档挽单率" v={`${(opt.save_rate * 100).toFixed(1)}%`} />
        <MiniStat k="混淆检验 r" v={conf.toFixed(4)} ok={Math.abs(conf) < 0.05} />
      </div>
    </Card>
  )
}

function MiniStat({ k, v, hl, ok }: { k: string; v: string; hl?: boolean; ok?: boolean }) {
  return (
    <div style={{ padding: '7px 9px', background: 'var(--panel-2)', borderRadius: 'var(--r-sm)', border: '1px solid var(--line-soft)' }}>
      <div style={{ fontSize: 9.5, color: 'var(--fg-4)', marginBottom: 2 }}>{k}</div>
      <div style={{
        fontFamily: 'var(--mono)',
        fontSize: 13,
        fontWeight: 600,
        color: hl ? 'var(--accent)' : ok === false ? 'var(--warn)' : 'var(--fg)',
      }}>
        {v}
      </div>
    </div>
  )
}

/** 迷你时序图。
 *
 * 和补偿曲线共用同一套"百分比坐标 + HTML 文字"的做法，
 * 但这里额外标出：
 * · 均值参考线（判断"这一周相对平时"的基准）
 * · 最后一点高亮（用户最关心"现在在哪"）
 * · 最大最小值的范围（知道波动幅度）
 */
function Trend({ block }: { block: Block }) {
  const series = block.series as { week: number; value: number }[]
  if (!series?.length) return null
  const min = block.min as number
  const max = block.max as number
  const avg = block.avg as number
  const span = Math.max(max - min, 1e-9)

  const Y_TOP = 14
  const Y_BOT = 84
  /* 【右侧留白】
     末点徽章用 right:0 定位，文字“W52 13.88%”宽度约 76px，
     会一直顶到卡片内边距边缘，视觉上像是溢出。
     所以折线本身只画到 92% 宽，右边 8% 专供标签。
     均值文字占左侧，故左端也留 0（均值标注已设 left:2 + 底色遮罩，不冲突）。 */
  const X_MAX = 92
  const xPct = (i: number) => (series.length <= 1 ? 46 : (i / (series.length - 1)) * X_MAX)
  const yPct = (v: number) => Y_BOT - ((v - min) / span) * (Y_BOT - Y_TOP)

  const line = series.map((p, i) => `${xPct(i)},${yPct(p.value)}`).join(' ')
  const last = series[series.length - 1]
  const isPct = max < 1

  return (
    <Card title={block.title} note={`${block.scope} · 均值 ${isPct ? (avg * 100).toFixed(2) + '%' : avg.toFixed(4)} · 区间 ${isPct ? (min * 100).toFixed(2) : min.toFixed(3)}–${isPct ? (max * 100).toFixed(2) + '%' : max.toFixed(3)}`}>
      <div style={{ position: 'relative', height: 96, marginTop: 2 }}>
        <svg viewBox="0 0 100 100" preserveAspectRatio="none"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }}>
          {/* 均值参考线。
              【这条线改了三轮】
              第一版 opacity=0.5 + strokeWidth=0.7，在深色底上几乎看不见——
              放大截图能发现在，正常观看距离等于没有。参考线不就是为了"看一眼知道中枢在哪"，
              看不见就白画。现在 1px / 0.75，够清楚又不抢数据线。
              另外试过在图内加「均值 14.22%」文字标签，失败两次：
              加在左端，曲线起点正好穿过它，底色遮罩把曲线切断了，像断线；
              加在别处又和峰值撞。专业图表的惯例就是参考线不标字，数值写进说明行——
              所以均值挪回 Card 的 note，图内只留线。 */}
          <line x1="0" y1={yPct(avg)} x2={X_MAX} y2={yPct(avg)}
            stroke="var(--warn)" strokeWidth="1" strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke" opacity="0.75" />
          <polygon points={`0,100 ${line} ${X_MAX},100`}
            fill="url(#curveFill)" />
          <polyline points={line} fill="none" stroke="var(--accent)" strokeWidth="1.3"
            vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        </svg>

        {/* 末点高亮。
            【为什么不用 SVG <circle>】
            这是踩过的坑：viewBox 100 宽、容器 900px 宽，preserveAspectRatio="none"
            会把横向放大 9 倍，circle 直接变成一条 23px 宽的扁椭圆——
            远看像一段脱离主线的短线，非常像渲染 bug。
            circle 没法用 vectorEffect 救（那只管描边宽度，不管几何形变）。
            解法：圆点改用 HTML，div + border-radius。HTML 永远不会被 SVG 拉伸。 */}
        <div style={{
          position: 'absolute',
          left: `${X_MAX}%`,
          top: `${yPct(last.value)}%`,
          width: 5, height: 5, borderRadius: '50%',
          background: 'var(--accent)',
          border: '1.5px solid #06231f',
          transform: 'translate(-50%, -50%)',
          pointerEvents: 'none',
        }} />

        <div style={{
          position: 'absolute',
          left: `${X_MAX}%`,
          top: `${yPct(last.value)}%`,
          transform: 'translate(0, -145%)',
          fontSize: 10, fontFamily: 'var(--mono)', fontWeight: 600,
          color: 'var(--accent)', whiteSpace: 'nowrap',
        }}>
          W{last.week} {isPct ? (last.value * 100).toFixed(2) + '%' : last.value.toFixed(3)}
        </div>

        <div style={{
          position: 'absolute', left: 0, bottom: 0,
          fontSize: 10, fontFamily: 'var(--mono)', color: 'var(--fg-4)',
        }}>
          W{series[0].week}
        </div>
        <div style={{
          position: 'absolute', left: `${X_MAX}%`, bottom: 0,
          fontSize: 10, fontFamily: 'var(--mono)', color: 'var(--fg-4)',
          transform: 'translateX(-100%)',
        }}>
          W{last.week}
        </div>
      </div>
    </Card>
  )
}
function markNum(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/(-?\d[\d,]*\.?\d*%|-?¥[\d,]+\.?\d*|-?\b\d[\d,]*\.\d+\b)/g, '<b>$1</b>')
}

/* ==================================================================
 * v3.1 评论分析四件套的展示块
 *
 * 【这三个组件共同要解决的一件事：让数字可以被反驳】
 * 情感占比、高频词、用户画像、竞品差距，本质上都是"我对一堆文本
 * 做了一次判断"。如果界面上只出现结论，读者无从验证，只能选择信
 * 或不信。所以每个数字旁边都必须能摸到它的原材料：
 *   · 词云 → 每个词挂一句包含它的原话
 *   · 画像 → 每条结论挂一句命中信号的原话
 *   · 对比 → 两侧的词都标出它在各自语料里的出现次数
 * 这不是"多给点信息"，是让结论可被证伪。
 * ================================================================== */

/* ------------------------------------------------------------ 词云 */
/** 词云块。
 *
 * 【为什么是"条形 + 悬停原句"而不是经典的随机字号布局】
 * 随机布局的词云看起来漂亮，但它有一个致命缺陷：位置和大小都不
 * 对应任何可读的量，读者只能看出"哪个大"，看不出"大多少"，
 * 也看不出排序。而这里要回答的是"抱怨点什么最突出"，
 * 需要一个能排队的结构。横向条形天然带顺序，还能同时挂频次和 lift。
 *
 * 【为什么要显示 lift 而不是只显示次数】
 * 因为次数会骗人。差评里"苹果"出现 182 次远多于"发霉"的 7 次，
 * 但"苹果"是商品名，在好评里也一样多——它对"抱怨什么"零信息量。
 * lift 就是"这个词在差评里比在好评里常见多少倍"，
 * 商品名 lift≈0 会自动沉底。所以次数和 lift 必须同时显示，
 * 让读者自己看出这两件事不是一回事。
 */
function WordCloud({ block }: { block: WordCloudBlock }) {
  const items = block.items ?? []
  if (!items.length) return null

  const isLift = block.rank_by === 'lift'
  // 归一化用最大值，让第一名的条占满，其余按比例。
  // 【不用对数刻度】词频本身跨度大，对数会让"17 次"和"7 次"
  // 看起来差不多长——而这两个词的证据强度差一倍多，
  // 视觉上不该抹平。
  const maxCount = Math.max(...items.map((i) => i.count), 1)

  return (
    <Card
      title={block.title}
      note={[
        block.rank_note,
        block.compare_n
          ? `对照组 ${block.compare_n.toLocaleString()} 条反向评论；lift = log2(本类出现率 ÷ 对照出现率)`
          : '',
      ].filter(Boolean).join(' · ')}
    >
      <div style={{ display: 'grid', gap: 1 }}>
        {items.map((it, i) => (
          <div className="wc-row" key={i} title={it.quote || '（该词没有可回溯的原句）'}>
            <div className="wc-word">{it.word}</div>

            <div className="wc-track">
              <div
                className="wc-fill"
                style={{ width: `${Math.max((it.count / maxCount) * 100, 3)}%` }}
              />
            </div>

            <div className="wc-n">{it.count}</div>

            {/* 【lift 一栏在没有对照组时不显示，而不是显示 0】
                显示 0 会被读成"这个词的区分度是零"，
                而实际含义是"这次没算区分度"。两者完全不同。 */}
            {isLift && (
              <div
                className="wc-lift"
                title={`差评里比好评里常见 ${Math.pow(2, it.lift ?? 0).toFixed(1)} 倍`}
              >
                {it.lift != null ? `${it.lift > 0 ? '+' : ''}${it.lift.toFixed(1)}` : '—'}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* 【原句单独列一条，不挤在行内】
          行内放不下 60 个字，而一句真实评论常常超过这个长度；
          强行截断就会把命中词切掉（这个 bug 在后端已经修过一次）。
          这里选第一条最突出的词做样例，其余靠 title 悬停看。 */}
      {items[0]?.quote && (
        <div className="wc-sample">
          <span className="wc-sample-k">原话 · {items[0].word}</span>
          <span className="wc-sample-t">{items[0].quote}</span>
        </div>
      )}
    </Card>
  )
}

/* ------------------------------------------------------------ 画像 */
/** 用户画像块。
 *
 * 【这个组件最重要的职责不是"展示画像"，是"防止画像被当成人群标签"】
 * 后端返回的 cannot 数组写明了"评论里没有年龄性别收入，回答不了这些"。
 * 如果界面上只渲染那些 20% 的百分比，读者一定会把它读成
 * "我的用户 15.8% 是品质优先型人群"——那是编的。
 * 所以 cannot 必须显示在画像块**内部**，而且要用中性但显眼的方式，
 * 不能塞进脚注、不能折叠、不能放到 caveats 里（那里用户不看）。
 */
function Persona({ block }: { block: PersonaBlock }) {
  const groups = block.groups ?? []
  const cannot = block.cannot ?? []
  if (!groups.length) return null

  // 每组内按条数降序，让"最强的信号"永远在最上面。
  // 后端已经排过，这里再排一次是为了防止将来后端改口径后
  // 前端出现"看着乱"的组——排序是展示层的底线，不该完全依赖上游。
  const sorted = groups.map((g) => ({
    ...g,
    items: [...(g.items ?? [])].sort((a, b) => b.n - a.n),
  }))
  const maxN = Math.max(...sorted.flatMap((g) => g.items.map((i) => i.n)), 1)

  return (
    <Card title={block.title}>
      <div style={{ display: 'grid', gap: 14 }}>
        {sorted.map((g, gi) => (
          <div key={gi}>
            <div className="ps-head">
              <span className="ps-group">{g.group}</span>
              {g.desc && <span className="ps-desc">{g.desc}</span>}
            </div>

            <div style={{ display: 'grid', gap: 7, marginTop: 7 }}>
              {g.items.map((it, i) => (
                <div className="ps-item" key={i}>
                  <div className="ps-top">
                    <span className="ps-sig">{it.signal}</span>
                    <span className="ps-bar">
                      <i style={{ width: `${Math.max((it.n / maxN) * 100, 2)}%` }} />
                    </span>
                    <span className="ps-n">
                      {it.n.toLocaleString()}
                      <small>{(it.pct * 100).toFixed(1)}%</small>
                    </span>
                  </div>

                  {/* 【原话必须标出它是被哪个词命中的】
                      画像模块出过最严重的一个 bug 就是"原话和信号对不上"——
                      挂的原话里根本没有那个信号词。加了 matched 之后，
                      读者可以自己核对：句中高亮的词就是判定依据。
                      这比任何"我们做了校验"的说明都可信。 */}
                  {it.quote && (
                    <div className="ps-quote">
                      <Higlight text={it.quote} hit={it.matched} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* 【"回答不了什么"放在画像块内部，不放进 caveats】
          放 caveats 等于没写：那是页面底部的折叠区，用户读到画像时
          已经形成印象了。这个清单的作用是**在印象形成的同时**拦住误读。 */}
      {cannot.length > 0 && (
        <div className="ps-cannot">
          <div className="ps-cannot-h">这份画像回答不了的事</div>
          <ul>
            {cannot.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      )}
    </Card>
  )
}

/** 把句子里命中信号的那个词标出来。
 *
 * 【为什么要做这件事，而不是整句原样显示】
 * 画像结论的可信度全在"这句话确实说了这件事"。
 * 如果只是把一句话贴在结论下面，读者还得自己去句子里找证据——
 * 多数人不会找，会直接默认它成立。
 * 标出来以后，读者一眼能看出"哦，是因为它提到了『囤货』"，
 * 也能立刻发现"这句话里根本没有那个词"（如果真的错挂的话）。
 */
function Higlight({ text, hit }: { text: string; hit?: string }) {
  if (!hit) return <>{text}</>
  const i = text.indexOf(hit)
  if (i < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, i)}
      <em className="ps-hit">{hit}</em>
      {text.slice(i + hit.length)}
    </>
  )
}

/* ------------------------------------------------------------ 用词差异 */
/** 本店 vs 对标的用词差异。
 *
 * 【为什么要做成左右对照，而不是一张"本店独有词"排行】
 * 单独列"本店独有词"会诱导一个错误结论：这些词就是我们的特色。
 * 但"独有"可能只是因为对标卖的是别的东西（选品差异），
 * 不是经营差异。左右并排能让读者立刻看到两边各自在说什么，
 * 从而自己判断"这是不是同一个话题下的差别"。
 *
 * 所以块头必须显示 compare_scope（"数码类目内"），
 * 那是这个对比成立的唯一前提。
 */
function Contrast({ block }: { block: ContrastBlock }) {
  const ours = block.ours ?? []
  const theirs = block.theirs ?? []
  if (!ours.length && !theirs.length) return null

  const maxLr = Math.max(
    ...ours.map((x) => Math.abs(x.lr)),
    ...theirs.map((x) => Math.abs(x.lr)),
    0.1,
  )

  return (
    <Card
      title={block.title}
      note={block.scope ? `对比范围：${block.scope}。不在同一类目内比较时，差异反映的是选品不同，不是经营差异。` : undefined}
    >
      <div className="ct-grid">
        <Side
          label={`本店更常说`}
          tone="ours"
          items={ours}
          maxLr={maxLr}
        />
        <Side
          label={`${block.benchmark || '对标'}更常说`}
          tone="theirs"
          items={theirs}
          maxLr={maxLr}
        />
      </div>
    </Card>
  )
}

function Side({
  label, tone, items, maxLr,
}: {
  label: string
  tone: 'ours' | 'theirs'
  items: { word: string; n: number; lr: number }[]
  maxLr: number
}) {
  return (
    <div className={`ct-side ${tone}`}>
      <div className="ct-h">{label}</div>
      <div style={{ display: 'grid', gap: 4 }}>
        {items.map((x, i) => (
          <div className="ct-row" key={i} title={`在${tone === 'ours' ? '本店' : '对标'}语料中 ${x.n} 次`}>
            <span className="ct-w">{x.word}</span>
            <span className="ct-bar">
              <i style={{ width: `${Math.max((Math.abs(x.lr) / maxLr) * 100, 4)}%` }} />
            </span>
            <span className="ct-n">{x.n}</span>
          </div>
        ))}
        {items.length === 0 && <div className="tiny muted">没有显著独有的词</div>}
      </div>
    </div>
  )
}