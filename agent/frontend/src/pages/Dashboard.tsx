/* ===========================================================================
 * 总览页 · 可交互看板
 *
 * 【这一页要回答什么】
 * 现在怎么样 → 哪里最疼 → 什么时候出过问题。三层，从上到下。
 *
 * 【v3.3 从"四张静态区块"改成"一张能动手的看板"】
 * 改动动机不是"图表比表格好看"，而是一个具体的读图障碍：
 * 原来三个表各自独立，看到"服饰 28.33%"却没法立刻知道
 * "服饰的钱主要花在哪个原因上"——得去原因治理页重选一遍。
 * 联动之后，点一下服饰，KPI、曲线、原因结构、异常周全跟着切。
 *
 * 【三条不做的事，和这个项目的核心约束一致】
 * 1. 前端不算任何百分比。条的长度用后端给的 return_rate，
 *    占比用后端给的 share。切换范围会改变分母，所以切换走服务端。
 * 2. 判定不重写。哪些周异常来自 diagnose_week（后端按范围算好的），
 *    这里只负责把 is_anomaly 画成红点/红框。
 * 3. 色阶阈值不拍脑袋。热力图的 scale_pp 由后端下发。
 *
 * 【为什么保留了表格而不是全换成图】
 * 图负责"看出形状"，表负责"读到数字并排序"。评审要核对某个数时
 * 一定是查表而不是读柱子的高度。所以两者并存，不是过渡状态。
 * =========================================================================== */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getJson } from '../lib/sse'
import type { CauseRow, CatRow, DashboardData, WeekPoint } from '../lib/pages'
import { Card, Stat, Empty, Loading, Fail, nf, money, pp } from '../components/PageKit'
import {
  HBars, HeatGrid, MiniBar, ScopePick, Segmented, SortTable, useTip,
} from '../components/BiKit'
import type { BarItem, Col } from '../components/BiKit'
import { Danmaku } from '../components/Danmaku'

type CauseMetric = 'share' | 'n' | 'loss'
type CatMetric = 'rr' | 'delta' | 'orders' | 'loss'

export function Dashboard({
  onOpenWeek, onOpenCategory,
}: {
  onOpenWeek: (w: number) => void
  onOpenCategory: (c: string) => void
}) {
  /* 当前口径范围。null = 全店。切换会重新请求，因为分母变了。
   *
   * 【初值从 URL 读，不用 useState(null)】
   * 一开始写成 `useState<string | null>(null)`，结果是：
   *   · 截图脚本传 ?category=服饰 → 页面照旧显示全店（参数被忽略）
   *   · 评委想分享"服饰这一屏" → 发出去的链接打开是全店
   *   · 刷新一下联动状态就没了
   * ChatPage 早就用 ?q= 做深链（现场演示预先打开问题），
   * 看板这边漏了同一件事。BI 看板的状态本来就该是可寻址的 ——
   * "你去看服饰那一屏"是评审现场最常发生的一句对话。
   */
  const [scope, setScope] = useState<string | null>(() => {
    try {
      return new URLSearchParams(window.location.search).get('category') || null
    } catch {
      return null
    }
  })
  const [d, setD] = useState<DashboardData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [causeMetric, setCauseMetric] = useState<CauseMetric>('share')
  const [catMetric, setCatMetric] = useState<CatMetric>('rr')
  /** 同一个 scope 第二次切换时用缓存，只在这两个之间复用 */
  const [cache, setCache] = useState<Record<string, DashboardData>>({})
  /* 趋势图上"当前关注"的周（悬停的那一周，或框选出的那一段）。
     提升到这里是为了让下面的热力矩阵能同步高亮对应的列 ——
     "这周整体偏高，是哪几个类目撑起来的"必须两张图一起看才答得出来，
     各看各的就只能靠人肉在 208 个格子里找。 */
  const [linkWeeks, setLinkWeeks] = useState<number[] | null>(null)

  /* 范围变化时同步回 URL（replace 不产生历史记录，否则每点一次类目
     就要按好几次后退键才能退出这一页）。hash 部分由 App 管，这里不动。 */
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search)
    if (scope) sp.set('category', scope)
    else sp.delete('category')
    const qs = sp.toString()
    const next = window.location.pathname + (qs ? `?${qs}` : '') + window.location.hash
    window.history.replaceState(null, '', next)
  }, [scope])

  useEffect(() => {
    const key = scope || '__all__'
    if (cache[key]) { setD(cache[key]); setErr(''); return }
    setErr('')
    const qs = scope ? `?category=${encodeURIComponent(scope)}` : ''
    getJson<DashboardData>(`/api/pages/dashboard${qs}`)
      .then((data) => {
        setD(data)
        setCache((c) => ({ ...c, [key]: data }))
      })
      .catch((e) => setErr(String(e)))
  }, [scope, tick])   // eslint-disable-line react-hooks/exhaustive-deps

  /* ---- 派生：全部是"取哪一列"，没有一处四则运算 ---- */
  // 【max 和 items 必须一起算，不能各算一遍】
  // 条长归一分母和排序指标是同一个东西。分成两处写，一旦改了指标
  // 忘了改另一处，就会出现"条最长的不在第一位"——而图上没有任何报错。
  const catBars = useMemo<{ items: BarItem[]; max: number }>(() => {
    if (!d) return { items: [], max: 1 }
    // 归一化分母：跨指标取同一列的最大值。**这是视觉刻度，不是业务值**，
    // 所以只在展示层出现，且必须取"当前排序指标"那一列，
    // 否则换了排序会看到条长和数字不一致。
    const pick = (c: CatRow): number =>
      catMetric === 'rr' ? c.return_rate
        : catMetric === 'delta' ? Math.abs(c.delta_pp)
          : catMetric === 'orders' ? c.orders
            : c.loss_total
    const max = Math.max(...d.by_category.map(pick), 1)
    const sorted = [...d.by_category].sort((a, b) => pick(b) - pick(a))
    const items = sorted.map((c) => ({
      key: c.category,
      label: c.category,
      pct: pick(c),
      text: catMetric === 'rr' ? `${c.return_rate.toFixed(2)}%`
        : catMetric === 'delta' ? `${pp(c.delta_pp)}pp`
          : catMetric === 'orders' ? nf(c.orders)
            : `¥${money(c.loss_total).v}${money(c.loss_total).u}`,
      // 副值固定显示"近 12 周"，它是判断"在恶化还是改善"的唯一线索，
      // 不管主指标换成什么都值得留在旁边
      pct2: catMetric === 'rr' ? c.rr_last12 : undefined,
      text2: catMetric === 'rr' ? `近12周 ${c.rr_last12.toFixed(2)}%` : undefined,
      // 【v4：tone 不再用语义色，改用"亮度分级"】
      // 改造前这里是 'bad'（红色）。灰阶方案下，一整块原因榜全是红条 =
      // 半个屏幕都是暖色，画面立刻变脏，而且"每个原因都是坏的"这种表达
      // 本身就没有信息量 —— 读者无法从颜色里读出任何差异。
      // 现在按数值高低给三档灰：越靠前越亮。这样"最深的那根"就是最该看的，
      // 不需要读者去记颜色含义。
      tone: (pick(c) / max > 0.66 ? 'hi' : pick(c) / max > 0.33 ? 'mid' : 'lo') as BarItem['tone'],
      active: scope === c.category,
      onClick: () => setScope(scope === c.category ? null : c.category),
      tip: (
        <div>
          <div className="bi-tip-h">{c.category}</div>
          <div className="bi-tip-row">订单 <b>{nf(c.orders)}</b> 单</div>
          <div className="bi-tip-row">退货 <b>{nf(c.returns)}</b> 条</div>
          <div className="bi-tip-row">全期退货率 <b>{c.return_rate.toFixed(2)}%</b></div>
          <div className="bi-tip-row">近 12 周 <b>{c.rr_last12.toFixed(2)}%</b></div>
          <div className="bi-tip-row">
            变化 <b className={c.delta_pp > 0 ? 't-bad' : 't-good'}>{pp(c.delta_pp)}pp</b>
          </div>
          <div className="bi-tip-row">
            退货损失 <b>¥{money(c.loss_total).v}{money(c.loss_total).u}</b>
            <span className="dim"> · 单条 ¥{c.loss_per.toFixed(0)}</span>
          </div>
          <div className="bi-tip-row dim">{c.sku_count} 个 SKU</div>
          <div className="bi-tip-go">{scope === c.category ? '点击退回全店' : '点击只看这一类'}</div>
        </div>
      ),
    }))
    return { items, max }
  }, [d, catMetric, scope])

  const causeBars = useMemo<{ items: BarItem[]; max: number }>(() => {
    if (!d) return { items: [], max: 1 }
    const pick = (c: CauseRow): number =>
      causeMetric === 'share' ? c.share
        : causeMetric === 'n' ? c.n
          : c.loss
    const max = Math.max(...d.causes.map(pick), 1)
    const sorted = [...d.causes].sort((a, b) => pick(b) - pick(a))
    const items = sorted.map((c) => ({
      key: c.code,
      label: c.cause,
      pct: pick(c),
      text: causeMetric === 'loss' ? `¥${money(c.loss).v}${money(c.loss).u}`
        : `${c.share.toFixed(2)}%`,
      text2: causeMetric === 'loss'
        ? `${c.share.toFixed(1)}% · ${nf(c.n)} 条`
        : `¥${money(c.loss).v}${money(c.loss).u}`,
      // 同 catBars：按数值分三档灰，不用语义色
      tone: (pick(c) / max > 0.66 ? 'hi' : pick(c) / max > 0.33 ? 'mid' : 'lo') as BarItem['tone'],
      onClick: () => onOpenCategory(scope || ''),
      tip: (
        <div>
          <div className="bi-tip-h">{c.cause} <span className="bi-tip-code">{c.code}</span></div>
          <div className="bi-tip-row">条数 <b>{nf(c.n)}</b></div>
          <div className="bi-tip-row">占 {d.scope} 退货 <b>{c.share.toFixed(2)}%</b></div>
          <div className="bi-tip-row">损失 <b>¥{money(c.loss).v}{money(c.loss).u}</b></div>
          <div className="bi-tip-row">每条平摊 <b>¥{c.loss_per.toFixed(0)}</b></div>
          <div className="bi-tip-go">点击进原因治理 ›</div>
        </div>
      ),
    }))
    return { items, max }
  }, [d, causeMetric, scope, onOpenCategory])

  const anomalyCols = useMemo<Col<WeekPoint>[]>(() => [
    {
      key: 'week', label: '周次', width: '88px',
      sortVal: (r) => r.week,
      render: (r) => <span className="strong">第 {r.week} 周</span>,
    },
    {
      key: 'rr', label: '退货率', num: true,
      sortVal: (r) => r.rr,
      render: (r) => `${r.rr.toFixed(2)}%`,
    },
    {
      key: 'delta', label: '偏离基线', num: true,
      sortVal: (r) => r.delta_pp,
      render: (r) => (
        <span className={r.delta_pp > 0 ? 't-bad' : 't-good'}>{pp(r.delta_pp)} pp</span>
      ),
    },
    {
      key: 'z', label: 'z 值', num: true, width: '76px',
      sortVal: (r) => r.z,
      render: (r) => r.z.toFixed(2),
    },
    {
      key: 'orders', label: '订单', num: true,
      sortVal: (r) => r.orders,
      render: (r) => <span className="dim">{nf(r.orders)}</span>,
    },
    {
      // 量级提示：不排序，只让眼睛一眼找到"多退了多少条"
      key: 'bar', label: '退货量',
      render: (r) => {
        const max = Math.max(...(d?.anomalies.map((x) => x.returns) || [1]), 1)
        return (
          <div className="bi-cell-bar">
            {/* 迷你条改用中性灰：它只是"这一周多退了多少"的量级提示，
                不需要方向语义（方向已经在上面"偏离基线"那一列用 ± 表达了） */}
            <MiniBar pct={(r.returns / max) * 100} tone="accent" />
            <span className="dim">{nf(r.returns)}</span>
          </div>
        )
      },
    },
    {
      key: 'go', label: '',
      render: () => <span className="pg-go">归因 ›</span>,
    },
  ], [d])

  if (err) return <Fail msg={err} onRetry={() => { setCache({}); setTick((x) => x + 1) }} />
  if (!d) return <Loading label={scope ? `汇总 ${scope} 52 周` : '汇总 52 周数据'} />

  const k = d.kpi
  const loss = money(k.loss_total)
  const gmv = money(k.gmv_year)

  return (
    <div className="pg">
      {/* 弹幕层：无痕开关，纯氛围，挂在范围选择器上方这条横向空白带 */}
      <Danmaku category={scope} />

      {/* 切换范围会换掉整页分母，所以放在最上面、且任何时候都可见 */}
      <ScopePick
        categories={d.by_category.map((c) => c.category)}
        value={scope}
        onChange={(v) => setScope(v)}
        latest={(
          <span className="bi-scope-hint">
            口径：<b>{d.scope}</b> · 52 周
          </span>
        )}
      />

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
            ? (d.anomalies.length <= 4
                ? `第 ${d.anomalies.map((a) => a.week).join('、')} 周`
                : `第 ${d.anomalies.slice(0, 4).map((a) => a.week).join('、')} 等 ${d.anomalies.length} 周`)
            : '无异常周'}
          tone={d.anomalies.length ? 'warn' : 'good'}
          title="双门槛判定：|z|≥2 且 |Δpp|≥1.5，两个都满足才算异常。范围切换后按该范围基线重算。"
        />
      </div>

      <Card
        title={`${d.scope} · 52 周退货率`}
        note={<>
          灰带是 ±2.58σ 的正常波动范围；红点是<b>双门槛</b>判定的异常周（|z|≥2 且偏离 ≥1.5pp）。
          <b>拖选一段周</b>看区间结论，<b>悬停</b>读单周数值，<b>单击</b>进该周判定。
        </>}
        pad={false}
      >
        <TrendChart
          series={d.series}
          band={d.baseline}
          onPick={onOpenWeek}
          onHoverWeek={setLinkWeeks}
        />
      </Card>

      {/* 热力图放在曲线下面：曲线讲整体走势，它讲"哪一类、哪一周" */}
      <Card
        title="类目 × 周 热力矩阵"
        note={<>颜色 = 二项 z（偏离几个标准误，<b>跨类目可比</b>）；红框 = 双门槛判定的异常周。行首灰字 = 该类目基准退货率。点格子进归因。</>}
        pad={false}
        right={<span className="bi-card-hint">按 z 上色，不是按退货率高低</span>}
      >
        <HeatGrid
          rows={d.heatmap.rows}
          weeks={d.heatmap.weeks}
          scaleZ={d.heatmap.scale_z}
          sigZ={d.heatmap.sig_z}
          highlightWeeks={linkWeeks}
          onPick={(w) => onOpenWeek(w)}
        />
      </Card>

      <div className="pg-2col">
        <Card
          title="类目横向对比"
          note={<>{scope ? `已选中 ${scope}，点一下退回全店。` : '点一行只看这一类，下面全程联动。'}</>}
          pad={false}
          right={(
            <Segmented
              value={catMetric}
              onChange={setCatMetric}
              options={[
                { key: 'rr', label: '退货率' },
                { key: 'delta', label: '变化' },
                { key: 'orders', label: '订单' },
                { key: 'loss', label: '损失/条' },
              ]}
            />
          )}
        >
          <div className="bi-bar-body">
            <HBars
              items={catBars.items}
              max={catBars.max}
              note={<>条长按当前指标的最大值归一（不是共用绝对刻度），数值以右侧为准。
                {catMetric === 'rr' && ' 淡色底条 = 近 12 周水平，比实条长说明在恶化。'}</>}
            />
          </div>
        </Card>

        <Card
          title="退货原因结构"
          note={<>{d.scope}口径，按当前指标排序。</>}
          pad={false}
          right={(
            <Segmented
              value={causeMetric}
              onChange={setCauseMetric}
              options={[
                { key: 'share', label: '占比' },
                { key: 'n', label: '条数' },
                { key: 'loss', label: '损失' },
              ]}
            />
          )}
        >
          <div className="bi-bar-body">
            <HBars
              items={causeBars.items}
              max={causeBars.max}
              note={<>按条数排序时最大的是「破损」，按损失排序时排序会变——<b>这两个口径经常指向不同的原因</b>，所以给了切换。</>}
            />
          </div>
        </Card>
      </div>

      <Card
        title={`需要解释的周 · ${d.scope}`}
        note={<>只列通过双门槛的周。点表头可排序，点行进归因页。</>}
        pad={false}
      >
        {d.anomalies.length === 0 ? (
          <Empty
            what="没有异常周"
            why="52 周里没有任何一周同时满足统计显著与业务显著——这本身是个结论，不是数据缺失"
          />
        ) : (
          <SortTable
            cols={anomalyCols}
            rows={d.anomalies}
            rowKey={(r) => String(r.week)}
            onRowClick={(r) => onOpenWeek(r.week)}
            defaultSort={{ key: 'delta', dir: 'desc' }}
          />
        )}
      </Card>

      <div className="pg-foot">
        扫描 {nf(d.evidence.rows_scanned)} 行周度面板 · {nf(d.evidence.returns_scanned)} 条退货记录 ·
        {' '}口径：{d.evidence.scope}
        {scope && (
          <> · <button className="bi-link" onClick={() => setScope(null)}>退回全店</button></>
        )}
      </div>
    </div>
  )
}

/* ---------------------------------------------------------------- 折线图 */
/**
 * 52 周走势。
 *
 * 【v4.6：只有 30 个红点能交互，另外 22 周点不了】
 * 原来这张图唯一的交互点是异常周的圆点 —— 点它跳归因。
 * 问题在于读者最想问的往往不是异常周，而是：
 * "W17 那个小鼓包到底是多少？""这周比基线高还是低？"
 * 鼠标划过去没有任何反馈，52 个数据点里有 22 个是纯图形，
 * 和旁边的热力矩阵（每格都能读、都能点）形成两种交互语言。
 *
 * 现在的规则是**整条线都可读**：
 *   · 鼠标在图上任意位置 → 顺移到最近的一周，出准星 + 浮层
 *   · 点任意一周 → 进该周判定页（正常周也能看"两条判据各差多少"，
 *     这本身就回答了"为什么它不算异常"）
 *   · 异常周的圆点依然可 Tab 聚焦，方向键在异常周之间快速跳
 *
 * 【准星为什么按 x 反算最近周，而不是给每周画一个透明矩形】
 * 52 个矩形意味着 52 个 DOM 节点和 52 套事件，而且每个宽 19px 的矩形
 * 之间必然有 1–2px 的缝隙（取整误差），鼠标划过会出现"闪一下又没了"。
 * 用 clientX 反算索引，热区是连续的一整条，不会漏。
 *
 * 【为什么索引要按 svg 的 getBoundingClientRect 换算】
 * viewBox 是 1000 宽、preserveAspectRatio=none，实际显示宽度随窗口变，
 * 1 个 CSS 像素不等于 1 个 viewBox 单位。直接用 clientX 会越往右偏得越多。
 *
 * 【v4.7：加框选，因为"读一个点"回答不了最常问的那个问题】
 * 悬停能告诉你"W26 是 12.95%"，但读者真正想知道的是
 * "下半年是不是整体变差了""这段里哪几周要解释"——这是区间问题，
 * 单点读数是回答不了的。而原来看区间只能靠眼睛在 52 个点上估。
 *
 * 所以加了三件事：
 *   1. 按住拖选一段 → 选区外的曲线压暗，选中的段落浮出来
 *   2. 图下方出摘要：跨度 / 均值 / 比基线多少 / 退货订单合计 / 异常周 / 峰谷
 *   3. 选区（或悬停的那一周）**同时高亮下方热力图的对应列** ——
 *      "这周整体偏高，是哪几个类目撑起来的"，一眼就能顺着列看下去
 *
 * 【为什么不把框选做成"点击开始、再点击结束"】
 * 两次点击之间读者没法修正 —— 拖歪了只能取消重来。
 * 拖动所见即所得，松手即成，且和"单击=进这一周"共用同一条时间轴，
 * 不需要额外按钮切换模式（模式切换是这类小图上最贵的交互成本）。
 */
function TrendChart({
  series, band, onPick, onHoverWeek,
}: {
  series: WeekPoint[]
  band: { mean_rr: number; band_low: number; band_high: number }
  onPick: (w: number) => void
  /** 把"当前关注的周"抛给热力图做列高亮。null = 解除联动 */
  onHoverWeek?: (weeks: number[] | null) => void
}) {
  const W = 1000
  const H = 220
  const PAD = { t: 14, r: 12, b: 22, l: 34 }

  const svgRef = useRef<SVGSVGElement>(null)
  const { show, hide, el } = useTip()
  /* 鼠标悬停与键盘聚焦分开存：
     两者同时存在时鼠标优先（用 hoverIdx ?? focusIdx），
     否则鼠标移开后浮层会顽固地停在最后一次 Tab 的位置上。 */
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)
  const [focusIdx, setFocusIdx] = useState<number | null>(null)

  /* 【框选状态：drag 是拖动中，sel 是已确定】
     分开存而不是共用一份，因为两者语义不同：
       drag —— 还没松手，边框用虚线，摘要先不出（数字还会变，出了反而晃眼）
       sel  —— 松手了，边框实线，摘要固定下来可以慢慢读 */
  const [drag, setDrag] = useState<{ a: number; b: number } | null>(null)
  const [sel, setSel] = useState<{ a: number; b: number } | null>(null)
  /* drag 的镜像。window 上的 mouseup 回调靠它读最新值 ——
     直接读 state 会拿到闭包里的旧快照（拖动中 state 每帧都在变），
     结果就是"拖到 W40 松手，选区停在 W31"。 */
  const dragRef = useRef<{ a: number; b: number } | null>(null)
  /* 刚拖完应该吞掉紧随其后的 click。
     不吞的话：拖选 W20–W40 松手，还会顺带跳进 W40 的判定页。 */
  const draggedRef = useRef(false)
  /* 【触屏专用状态】
     实测（CDP 派发真实 touch 事件）：只绑 mouse 事件的话，
     触屏上完全拖不动 —— box=False / bar=False，
     桌面浏览器不会为"手指在没滚动的位置划过"合成 mousemove。
     而这张图的区间分析恰恰是最该在平板上做操作的部分。

     start 是手指按下时的锚点，active 表示"已经确认这是一次横向拖拽"。
     分开存是为了先观察 8px 再决定跟谁走：
     手指按下去可能想滚页面，也可能想框选区，一开始分不出来。 */
  const touchRef = useRef<{ x: number; y: number; i: number; active: boolean } | null>(null)
  /* 触屏拖完浏览器仍会补发一次 click（tap 的语义）。
     用一个时间戳挡掉它 —— 不能复用 draggedRef，
     因为那个在 mouseup 后一个宏任务就清掉了，而合成 click 要晚 300ms。 */
  const touchGuardRef = useRef(0)

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

  /* 把鼠标的屏幕坐标换算成"最近的一周"。
     返回 null 表示落在图外侧（左侧 y 轴留白、右侧边距），不该有反馈。 */
  const idxAt = (clientX: number): number | null => {
    const node = svgRef.current
    if (!node) return null
    const box = node.getBoundingClientRect()
    if (box.width <= 0) return null
    const vx = ((clientX - box.left) / box.width) * W
    const step = (W - PAD.l - PAD.r) / Math.max(series.length - 1, 1)
    const i = Math.round((vx - PAD.l) / step)
    return i < 0 || i >= series.length ? null : i
  }

  const tipFor = (s: WeekPoint) => (
    <div>
      <div className="bi-tip-h">第 {s.week} 周</div>
      <div className="bi-tip-row">退货率 <b>{s.rr.toFixed(2)}%</b></div>
      <div className="bi-tip-row">
        比基线
        <b className={s.delta_pp > 0 ? 't-bad' : 't-good'}>{pp(s.delta_pp)}pp</b>
      </div>
      <div className="bi-tip-row">z 值 <b>{s.z.toFixed(2)}</b></div>
      <div className="bi-tip-row">
        退货 / 订单 <b>{nf(s.returns)} / {nf(s.orders)}</b>
      </div>
      <div className="bi-tip-row dim">
        {s.is_anomaly
          ? '双门槛都过了：异常，需要解释'
          : s.trivial_sig
            ? '统计显著但幅度不够，不需要动手'
            : `在 ${band.band_low.toFixed(1)}–${band.band_high.toFixed(1)}% 的正常带内`}
      </div>
      <div className="bi-tip-go">
        {s.is_anomaly ? '点击进归因 ›' : '点击看这一周的判定 ›'}
      </div>
    </div>
  )

  const active = hoverIdx ?? focusIdx

  /* ---------------- 框选：开始 / 延伸 / 收尾 ---------------- */
  const selStart = (i: number) => {
    draggedRef.current = false
    dragRef.current = { a: i, b: i }
    setDrag({ a: i, b: i })
  }
  const selExtend = (i: number) => {
    const cur = dragRef.current
    if (!cur || i === cur.b) return
    const next = { a: cur.a, b: i }
    dragRef.current = next
    setDrag(next)
    /* 跨过一周才算"真的在拖"。用来区分"手抖了一下"和"要选一段"，
       前者应当仍然是一次普通点击。 */
    if (Math.abs(next.a - next.b) >= 1) draggedRef.current = true
  }

  /* 收尾挂在 window 上：鼠标拖到图外面再松手也要能结束，
     否则鼠标一离开容器就永远松不开，选区卡在拖动中。 */
  /* 【提交逻辑单独抽出来，鼠标和触屏共用】
     两套事件各自实现一遍"松手时该干什么"，迟早会漏掉一边 ——
     touch 那次就漏了，直到写了触屏用例才发现。
     useCallback 是为了让下面的 cleanup effect 依赖稳定。 */
  const selCommit = useCallback(() => {
    const cur = dragRef.current
    if (!cur) return
    dragRef.current = null
    setDrag(null)
    /* 只有跨过一周才算选区。原地按一下 = 想点这一周，不是想选一段。 */
    if (Math.abs(cur.a - cur.b) >= 1) {
      setSel({ a: Math.min(cur.a, cur.b), b: Math.max(cur.a, cur.b) })
      hide()
    }
    /* 复位推迟到下一个宏任务：click 在 mouseup 之后同步派发，
       在这里立刻清掉的话，紧随的 click 就看不到"刚拖过"了。 */
    setTimeout(() => { draggedRef.current = false }, 0)
  }, [hide])

  useEffect(() => {
    const up = () => selCommit()
    window.addEventListener('mouseup', up)
    return () => window.removeEventListener('mouseup', up)
  }, [selCommit])

  /* Esc 清选区。框选是个"进得去出不来"的典型陷阱 ——
     没有明确的退出键，读者会以为图卡住了。 */
  useEffect(() => {
    if (!sel) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setSel(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [sel])

  /* 联动上报。选区优先于悬停 ——
     选区已经是一次有意图的选择，鼠标顺手划过不该把它盖掉。 */
  useEffect(() => {
    if (!onHoverWeek) return
    if (sel) {
      onHoverWeek(series.slice(sel.a, sel.b + 1).map((s) => s.week))
    } else if (active !== null) {
      onHoverWeek([series[active].week])
    } else {
      onHoverWeek(null)
    }
  }, [sel, active, series, onHoverWeek])

  /* 【区间摘要】
     口径两处要写清楚，否则会被当成"重新算了一个退货率"：
       · 均值是**逐周退货率的简单平均**，不是总退货/总订单（那会变成加权，
         和上方 KPI 的口径不一致，同一个词两个数会引发争论）
       · 峰谷取的是区间内实际极值周，不是"均值最高的一段" */
  const selInfo = useMemo(() => {
    if (!sel) return null
    const part = series.slice(sel.a, sel.b + 1)
    if (!part.length) return null
    const n = part.length
    const mean = part.reduce((s, x) => s + x.rr, 0) / n
    const hi = part.reduce((m, x) => (x.rr > m.rr ? x : m), part[0])
    const lo = part.reduce((m, x) => (x.rr < m.rr ? x : m), part[0])
    /* 【区间斜率：回答"这一段在变好还是变坏"】
       均值只能回答"这段整体偏高还是偏低"，回答不了方向 ——
       一个 +3pp 然后 -3pp 的段落和一个平稳的段落，均值一模一样。

       用最小二乘而不是"末周 - 首周"：首尾各是一个单周观测，
       挑中一个异常周就能把结论反转。回归用上区间内全部点，
       单周噪声被平均掉，这才是"趋势"该有的稳健性。

       单位是 pp/周，乘 10 后约等于"每 10 周变化多少 pp"，
       界面上两个都给人看，因为"每周 +0.04"读起来太小、容易被当成噪声。 */
    const slope = (() => {
      if (n < 3) return 0
      const mx = (n - 1) / 2
      const my = mean
      let num = 0
      let den = 0
      for (let i = 0; i < n; i++) {
        num += (i - mx) * (part[i].rr - my)
        den += (i - mx) * (i - mx)
      }
      return den ? num / den : 0
    })()
    return {
      from: part[0].week,
      to: part[n - 1].week,
      n,
      mean,
      dev: mean - band.mean_rr,
      slope,
      returns: part.reduce((s, x) => s + x.returns, 0),
      orders: part.reduce((s, x) => s + x.orders, 0),
      anom: part.filter((x) => x.is_anomaly),
      hi,
      lo,
    }
  }, [sel, series, band.mean_rr])

  /* 选区的几何位置。左右各外扩半格，让首尾两周整个落在框里 ——
     否则框线正好压在这两周的点上，看起来像"没选上它们"。 */
  const selGeom = (() => {
    const r = drag ?? sel
    if (!r) return null
    const i0 = Math.min(r.a, r.b)
    const i1 = Math.max(r.a, r.b)
    const step = (W - PAD.l - PAD.r) / Math.max(series.length - 1, 1)
    const xa = Math.max(PAD.l, x(i0) - step / 2)
    const xb = Math.min(W - PAD.r, x(i1) + step / 2)
    return { x: xa, w: Math.max(xb - xa, 1), y: PAD.t, h: H - PAD.t - PAD.b }
  })()

  return (
    <div
      className="pg-chart"
      /* 事件挂在容器上而不是 SVG 元素上：
         落在异常周圆点上时命中的是 <g>，挂在 svg 上会正常冒泡，
         但鼠标在圆点正上方移动时事件目标是圆点，容器仍然收得到 —— 
         这是"整条线连续可用"的前提。 */
      onMouseDown={(e) => {
        /* 只认左键，且必须落在数据区里。
           在 y 轴留白上按下不该开始框选 —— 那里不是数据。 */
        if (e.button !== 0) return
        /* 点摘要条/快捷按钮时不要起框：否则按下瞬间会闪一个 1 周宽的虚线框，
           松手才消失，看起来像"点了个按钮顺手把选区搞乱了"。 */
        if ((e.target as HTMLElement).closest('.pg-selbar, .pg-selgo')) return
        const i = idxAt(e.clientX)
        if (i === null) return
        selStart(i)
      }}
      onMouseMove={(e) => {
        const i = idxAt(e.clientX)
        /* 【拖动中：只更新选区，不出浮层】
           浮层跟着鼠标跑会正好压在正在框的那段曲线上，
           读者一边拖一边看不见自己选了哪儿。 */
        if (dragRef.current && (e.buttons & 1)) {
          if (i !== null) { selExtend(i); setHoverIdx(i) }
          hide()
          return
        }
        setHoverIdx(i)
        if (i === null) { hide(); return }
        /* 光标在图的下半区 → 浮层往上翻；上半区 → 往下。
           图只有 220px 高、浮层约 150px，不做这个判断时
           浮层会恒定压在光标右侧那段折线上，正好挡住要接着看的部分。 */
        const box = svgRef.current?.getBoundingClientRect()
        const above = box ? e.clientY > box.top + box.height * 0.55 : false
        show(e, tipFor(series[i]), above)
      }}
      onMouseLeave={() => { setHoverIdx(null); hide() }}
      /* ---------------- 触屏 ---------------- */
      /* 【为什么不能只靠合成鼠标事件】
         移动浏览器确实会把 touch 合成为 mousedown/move/up，
         但前提是浏览器判定这段手势"不是滚动"。图高只有 220px、
         手指滑动的方向又多带垂直分量，实测下来合成事件根本没来。
         所以这里直接处理 touch 三件套。 */
      onTouchStart={(e) => {
        if (e.touches.length !== 1) return
        if ((e.target as HTMLElement).closest('.pg-selbar, .pg-selgo')) return
        const t = e.touches[0]
        const i = idxAt(t.clientX)
        if (i === null) return
        /* 先只记锚点，不开始框选。
           手指按下去的时候还分不清"想滚页面"还是"想框选区"。 */
        touchRef.current = { x: t.clientX, y: t.clientY, i, active: false }
      }}
      onTouchMove={(e) => {
        const st = touchRef.current
        if (!st) return
        const t = e.touches[0]
        if (!t) return
        if (!st.active) {
          const dx = Math.abs(t.clientX - st.x)
          const dy = Math.abs(t.clientY - st.y)
          if (dx < 8) return                      // 还没离开"点按"的范围
          if (dy > dx) {                          // 以纵向为主 → 交给页面滚
            touchRef.current = null
            return
          }
          st.active = true
          selStart(st.i)
        }
        /* 确认是横向拖拽后才拦。
           提前拦会连带阻止页面纵向滚动，用户会觉得"手指在这块图上失灵"。
           在这里拦同时也是**阻止浏览器补发合成鼠标事件** ——
           否则松手后会多出一次 click，直接跳进某一周的判定页。 */
        if (e.cancelable) e.preventDefault()
        const i = idxAt(t.clientX)
        if (i !== null) selExtend(i)
        hide()
      }}
      onTouchEnd={() => {
        const st = touchRef.current
        touchRef.current = null
        if (!st?.active) return
        touchGuardRef.current = Date.now() + 600
        selCommit()
      }}
      onClick={(e) => {
        /* 只认左键。右键/中键点图表不该跳页 —— 用户想的是"复制"或"滚动"。 */
        if (e.button !== 0) return
        /* 触屏拖完补发的那一下 click 不算"点选某一周"。 */
        if (Date.now() < touchGuardRef.current) return
        /* 【摘要条和快捷选区按钮在容器内部，它们的点击不能当成"点图表"】
           实测踩到的两个后果：
             · 点「近 26 周」→ 选中了，随后冒泡到这里的 idxAt，
               算出坐标落在图外 → 走"点空白 = 清空"，选中的那一下等于白点
             · 点「清除」→ 清掉选区之后继续冒泡，顺带跳进某一周的判定页
           这两处都是"按钮自己的语义被容器的语义覆盖"，
           所以在这里按事件来源排除，而不是给每个按钮加 stopPropagation ——
           那样以后再加按钮还得记得加一次，迟早漏。 */
        if ((e.target as HTMLElement).closest('.pg-selbar, .pg-selgo')) return
        /* 刚框选完的那一下点击是"松手"，不是"选这一周"。 */
        if (draggedRef.current) { draggedRef.current = false; return }
        const i = idxAt(e.clientX)
        if (i === null) { setSel(null); return }   // 点图外空白 = 退出选区
        onPick(series[i].week)
      }}
    >
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="pg-chart-svg">
        <defs>
          <linearGradient id="dashFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.13" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.005" />
          </linearGradient>
        </defs>

        {/* 正常波动带 */}
        <rect
          x={PAD.l} y={y(band.band_high)}
          width={W - PAD.l - PAD.r}
          height={Math.max(y(band.band_low) - y(band.band_high), 1)}
          fill="rgba(255,255,255,.07)"
        />
        {/* 基线 */}
        <line
          x1={PAD.l} x2={W - PAD.r} y1={y(band.mean_rr)} y2={y(band.mean_rr)}
          stroke="rgba(255,255,255,.35)" strokeDasharray="3 4" strokeWidth="1"
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

        {/* 【选区压暗：画在曲线之前】
            压暗放在曲线之下、灰带之上，被压的区域连灰带一起变暗，
            选中的那段才浮得出来。放到曲线之后就变成"把选中的线也压暗了"，
            正好反了。

            用 var(--scrim) 而不是 var(--bg)：卡片 #151517 和 --bg #0b0b0c
            只差 10 级亮度，用 --bg 压上去等于没压（实测看不出来）。
            高对比度模式下整块遮罩会被隐藏，见 app.css 的 forced-colors 段。 */}
        {selGeom && (
          <g className="pg-sel-mask" pointerEvents="none" aria-hidden="true">
            <rect x={PAD.l} y={selGeom.y} fill="var(--scrim)"
              width={Math.max(selGeom.x - PAD.l, 0)} height={selGeom.h} />
            <rect x={selGeom.x + selGeom.w} y={selGeom.y} fill="var(--scrim)"
              width={Math.max(W - PAD.r - selGeom.x - selGeom.w, 0)} height={selGeom.h} />
          </g>
        )}

        <path d={area} fill="url(#dashFill)" />

        {/* 底下一层柔光：灰阶里白线容易"飘"，加一层同色低透明度描边压住 */}
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="4"
          strokeOpacity=".10" strokeLinejoin="round" strokeLinecap="round" />
        <path d={line} fill="none" stroke="var(--accent)" strokeWidth="1.8"
          strokeLinejoin="round" strokeLinecap="round" />

        {series.map((s, i) => {
          if (!s.is_anomaly) return null
          return (
            /* 【异常周圆点：必须能用键盘到达】
              它是这张图唯一的交互点 —— 点一下直接跳到该周的归因。
              原来只有 onClick，<g> 默认不可聚焦，键盘用户 Tab 过去
              会直接越过整张图，等于这一页最核心的入口对他们不存在。
              加 role/tabIndex/onKeyDown 三件套即可（SVG2 支持 tabIndex）。

              空格键要 preventDefault：否则页面会跟着滚动一下，
              看起来像"点了但没反应"。 */
            <g
              key={s.week}
              className="pg-dot-g"
              role="button"
              tabIndex={0}
              aria-label={`第 ${s.week} 周，退货率 ${s.rr.toFixed(2)}%，偏离 ${pp(s.delta_pp)}pp，查看归因`}
              /* 【不再挂 onClick，鼠标点击交给容器统一处理】
                 否则点在圆点上时，圆点和容器各触发一次 onPick，
                 一次点击跳两次页 —— 在带过渡的路由里是看得见的闪烁。
                 两者算出的是同一个周次，重复纯属多余。 */
              onFocus={(e) => {
                setFocusIdx(i)
                const b = e.currentTarget.getBoundingClientRect()
                /* 键盘没有鼠标坐标，用圆点自身的位置喂给同一套浮层，
                   保证"Tab 过来"和"鼠标划过"看到的是同一个东西。
                   方向沿用同一判据：圆点在下半区就往上翻。 */
                const svgBox = svgRef.current?.getBoundingClientRect()
                const above = svgBox ? b.top > svgBox.top + svgBox.height * 0.55 : false
                show({ clientX: b.left + b.width / 2, clientY: b.top }, tipFor(s), above)
              }}
              onBlur={() => { setFocusIdx(null); hide() }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onPick(s.week)
                  return
                }
                /* 方向键只在异常周之间跳。
                   52 周如果逐周移动，从 W1 到 W49 要按 48 次 —— 
                   而异常周恰恰是这张图里唯一有"下一步"的东西。 */
                if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
                e.preventDefault()
                const all = Array.from(
                  e.currentTarget.ownerSVGElement?.querySelectorAll<SVGGElement>('.pg-dot-g') || [],
                )
                const k = all.indexOf(e.currentTarget)
                all[k + (e.key === 'ArrowRight' ? 1 : -1)]?.focus()
              }}
            >
              {/* 透明大圆扩大点击热区：1.5px 的线太难点 */}
              <circle cx={x(i)} cy={y(s.rr)} r="9" fill="transparent" />
              <circle cx={x(i)} cy={y(s.rr)} r="3.4" fill="var(--bad)"
                stroke="var(--bg)" strokeWidth="1.6" />
              <title>第 {s.week} 周 · {s.rr.toFixed(2)}%（{pp(s.delta_pp)}pp, z={s.z.toFixed(1)}）</title>
            </g>
          )
        })}

        {/* 【选区边框：画在最上层】
            拖动中是虚线 —— 还没定下来，视觉上要"松"，不然会以为已经确定了。
            松手后变实线并带一点填充，从"正在框"过渡到"已经选中"。
            首尾各画一根竖线：跨度大的时候只有边框的话，
            读者要靠左右边界去对周次，多一根竖线就能直接拉下来看是哪一周。 */}
        {selGeom && (
          <g pointerEvents="none" aria-hidden="true">
            <rect
              className="pg-sel-box"
              x={selGeom.x} y={selGeom.y} width={selGeom.w} height={selGeom.h}
              fill="var(--accent)" fillOpacity={drag ? '.04' : '.07'}
              stroke="var(--accent)" strokeOpacity={drag ? '.4' : '.62'} strokeWidth="1"
              strokeDasharray={drag ? '4 3' : undefined}
            />
            <line className="pg-sel-edge"
              x1={selGeom.x} x2={selGeom.x} y1={selGeom.y} y2={selGeom.y + selGeom.h}
              stroke="var(--accent)" strokeWidth="1.2" strokeOpacity=".85" />
            <line className="pg-sel-edge"
              x1={selGeom.x + selGeom.w} x2={selGeom.x + selGeom.w}
              y1={selGeom.y} y2={selGeom.y + selGeom.h}
              stroke="var(--accent)" strokeWidth="1.2" strokeOpacity=".85" />
          </g>
        )}

        {/* 【准星：只在有光标时出现，pointer-events:none 让它不挡点击】
            竖线从顶穿到底：读者要判断"这个点落在灰带的哪一侧"，
            眼睛是从线往上下扫的，线断了就得靠猜。 */}
        {active !== null && (
          <g pointerEvents="none" aria-hidden="true">
            <line
              className="pg-cross"
              x1={x(active)} x2={x(active)}
              y1={PAD.t} y2={H - PAD.b}
            />
            {/* 光标点比圆点大一圈，异常周保持红色 —— 
                光标是"我在读这一周"，颜色是"这一周是什么"，两个信息不能互相覆盖。 */}
            <circle
              className="pg-cursor-dot"
              cx={x(active)} cy={y(series[active].rr)} r="4.2"
              fill={series[active].is_anomaly ? 'var(--bad)' : 'var(--accent)'}
              stroke="var(--bg)" strokeWidth="1.8"
            />
          </g>
        )}

        {/* x 轴只标几个关键周，标满会挤成一团 */}
        {series.map((s, i) => {
          if (i % 8 !== 0 && i !== series.length - 1) return null
          /* 光标所在周的字要变亮：刻度是这张图唯一的定位参照，
             浮层里写着"第 28 周"而轴上找不到 28，等于没有定位。 */
          const on = active === i
          return (
            <text key={s.week} x={x(i)} y={H - 5} textAnchor="middle"
              fill={on ? 'var(--fg)' : 'var(--fg-4)'}
              fontSize="9.5" fontFamily="var(--mono)">
              W{s.week}
            </text>
          )
        })}
      </svg>

      {/* 【区间摘要条】
          放在图**下方**而不是浮在图里：框选之后要读的是六七个数字，
          浮层压在图上会挡住刚选出来的那段曲线 —— 那正是读者要对照的东西。

          出现时卡片会变高一点，这是有意的：它是"这一屏多出来的新内容"，
          不该做成浮在别处、随时会消失的东西。 */}
      {selInfo && (
        <div className="pg-selbar">
          <span className="pg-selbar-h">
            第 {selInfo.from}–{selInfo.to} 周
            <em>{selInfo.n} 周</em>
          </span>
          <span className="pg-selbar-i">
            均值 <b>{selInfo.mean.toFixed(2)}%</b>
          </span>
          <span className="pg-selbar-i">
            比基线
            <b className={selInfo.dev > 0 ? 't-bad' : 't-good'}>{pp(selInfo.dev)}pp</b>
          </span>
          {/* 【趋势这一项只在周数够时才出现】
              3 周以内算斜率没有意义（两个点连一条线都能"slope>0"），
              会给出一个看着精确、其实全靠噪声的数 —— 那比不给更糟。 */}
          {selInfo.n >= 4 && (
            <span className="pg-selbar-i" title="区间内逐周退货率的最小二乘斜率，单位 pp/周">
              趋势
              <b className={selInfo.slope > 0.005 ? 't-bad' : selInfo.slope < -0.005 ? 't-good' : ''}>
                {selInfo.slope > 0.005 ? '↑ 恶化' : selInfo.slope < -0.005 ? '↓ 改善' : '→ 平稳'}
              </b>
              <em className="mono">{selInfo.slope >= 0 ? '+' : ''}{(selInfo.slope * 10).toFixed(2)}pp/10周</em>
            </span>
          )}
          <span className="pg-selbar-i">
            退货 <b>{nf(selInfo.returns)}</b> / 订单 {nf(selInfo.orders)}
          </span>
          <span className="pg-selbar-i">
            异常
            <b className={selInfo.anom.length ? 't-bad' : 't-good'}>{selInfo.anom.length}</b> 周
            {selInfo.anom.length > 0 && (
              <em className="dim">（{selInfo.anom.map((a) => `W${a.week}`).join('、')}）</em>
            )}
          </span>
          <span className="pg-selbar-i">
            峰 <b>{selInfo.hi.rr.toFixed(2)}%</b>
            <em>W{selInfo.hi.week}</em>
            谷 <b>{selInfo.lo.rr.toFixed(2)}%</b>
            <em>W{selInfo.lo.week}</em>
          </span>
          {/* 均值是逐周退货率的简单平均，不是总退货/总订单。
              两者会给出不同的数，不在界面上说清楚就会有人拿它对 KPI。 */}
          <span className="pg-selbar-note">均值为逐周简单平均</span>
          <button className="bi-link pg-selbar-x" onClick={() => setSel(null)}>
            清除 <kbd>Esc</kbd>
          </button>
        </div>
      )}

      {el}

      {/* 【选区入口：没有选区时，这里是"怎么开始"】
          拖动是鼠标专属的，键盘和触屏都做不了 —— 而这张图最关键的一条结论
          （"这段时间在变好还是变坏"）只能从区间里读出来。
          所以给三个"一键选区"的按钮：键盘能 Tab、触屏能点，
          鼠标用户也省得每次去拖（"看下半年"比"从 W27 拖到 W52"快得多）。

          位置和摘要条共用：有选区时摘要条接管这一块，
          没有选区时才显示这三个按钮。同一块空间不说两句话。 */}
      {!selInfo && (
        <div className="pg-selgo">
          <span className="pg-selgo-h">拖选一段周看区间结论</span>
          {[
            { label: '近 13 周', n: 13 },
            { label: '近 26 周', n: 26 },
            { label: '全部 52 周', n: series.length },
          ].map((q) => {
            const n = Math.min(q.n, series.length)
            return (
              <button
                key={q.label}
                className="pg-selgo-b"
                onClick={() => setSel({ a: series.length - n, b: series.length - 1 })}
              >
                {q.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
