/* ===========================================================================
 * 归因页
 *
 * 【这一页是整套系统的正题】
 * 前面所有页都只是在说"哪里疼"，这一页要说"为什么疼、该动谁"。
 *
 * 【四个问题必须按顺序回答，顺序不能换】
 *   ① 这一周到底异常吗？        → 双门槛判定
 *   ② 涨了多少、涨在哪个类目？  → 贡献度分解
 *   ③ 是不是卖的东西变了？      → 原因构成对比
 *   ④ 是不是促销本身导致的？    → SKU 配对检验
 *
 * 【为什么 ③ 和 ④ 不能合并成一个"原因"字段】
 * 它们是两种不同性质的解释，而且可以同时成立：
 *   · 构成变化 = "这周卖的东西本来退货率就高" → 选品问题
 *   · 促销机制 = "同一件东西在促销周退货率更高" → 行为问题
 * 治理动作完全相反：前者要调货品结构，后者要管下单预期。
 * 合成一个字段必然丢掉其中一层的可反驳性。
 *
 * 【双门槛现在怎么露出来 —— 2026-11-03 改版】
 * 原来有一张「为什么这样判」的大卡，把 z≥2 / Δ≥1.5pp 和"异常"再摊一遍。
 * 删掉的原因很直接：这三个结论上面 KPI 卡的副标题（统计门槛 / 业务门槛）
 * 和周次选择器里的「异常周」标签已经在说了，同一屏说两次，
 * 读者反而会怀疑"这两处是不是不一样"。
 * 留下的是一条 10px 的说明行 —— 只保留那句没法从数字里看出来的判据理由：
 * **两条必须同时成立**。删掉这句话，读者就会把"z 大"当成"异常"，
 * 而那正是这个项目最想避免的误读（z=3.97 但只偏离 0.99pp 的周不该报）。
 * =========================================================================== */
import { useEffect, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { getJson } from '../lib/sse'
import type { AttributionData } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, money, pp } from '../components/PageKit'
import { Segmented } from '../components/BiKit'
import { Waterfall, Donut, Diverging, Quadrant, DualRing, Slope, Treemap, Dumbbell, StackedShare } from '../components/BiCharts'
import type { WaStep, TreeLeaf } from '../components/BiCharts'

export function Attribution({
  week, setWeek,
}: { week: number; setWeek: (w: number) => void }) {
  const [d, setD] = useState<AttributionData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [busy, setBusy] = useState(false)

  /* 【为什么是"钉住"而不是"筛选"】
     这一页是按周诊断的，没有"只看服饰"这个维度 ——
     Dashboard 才有（那边点类目会换全店口径，要重新请求）。
     这里点一下柱子/扇区/条，作用是**在三种视图之间保持同一个焦点**：
     钉住「服饰」，切到占比看它占多少单，再切到差距看它差多少。
     如果做成重新请求，就得改后端、加参数，而收益只是"少一行数据"。
     所以选中的含义是"我盯上这一类了"，不是"把别的滤掉"。 */
  const [catPick, setCatPick] = useState<string | null>(null)
  const [catView, setCatView] = useState<'waterfall' | 'dumbbell' | 'share' | 'gap' | 'bubble' | 'table'>('waterfall')
  const [stView, setStView] = useState<'shift' | 'recover' | 'matrix' | 'dual' | 'tree' | 'table'>('shift')
  /* 【第二张卡也要有"钉住"】
     第一张卡的 5 个视图共用 catPick，切视图时选中不丢。
     第二张卡原来没有这个状态 —— 4 张图都不可点，
     用户在象限图里看到「材质做工问题」损失最大，
     切到「明细」想看它的具体数字，选中没了，得自己在 7 行里再找一遍。
     看板里"选中"必须是全局概念，不能每张图各记各的。 */
  const [stPick, setStPick] = useState<string | null>(null)
  const pickSt = (k: string) => setStPick(stPick === k ? null : k)

  useEffect(() => {
    // 【week<=0 表示"周次还没确定"，不许发请求】
    // App 的 week 初值是 0（见 App.tsx 里为什么不用写死的 44）：
    // nav 还没回来时它确实是 0，而 0 不是合法周次。
    // 不拦住的话会打出 ?week=0，后端 diagnose_week(0) 返回 ok=False，
    // 用户先看到一次"判定失败"再看到正确结果 —— 一次莫名其妙的红屏。
    if (!week || week <= 0) return
    setBusy(true)
    setErr('')
    getJson<AttributionData>(`/api/pages/attribution?week=${week}`)
      .then((r) => { setD(r); setBusy(false) })
      .catch((e) => { setErr(String(e)); setBusy(false) })
  }, [week, tick])

  if (err) return <Fail msg={err} onRetry={() => setTick((x) => x + 1)} />
  if (!week || week <= 0) return <Loading label="确定默认周次" />
  if (!d) return <Loading label={`拆解第 ${week} 周`} />

  /* 【ok=false 必须显式拦掉，不能让页面继续往下画】
     后端对"这一周不存在"（比如问第十周之外的第六十周）返回的是
     ok=False + 一堆 0，而不是 HTTP 4xx。不拦的话：
     退货率 0.00%、订单 0 单、瀑布图全空、判定规则照常写着"|z|≥2"，
     整页看起来像一个"数据异常干净的周"，而不是"这一周不存在"。
     这正是用户投诉过的那类问题 —— 系统看起来答了，其实答的不是你问的。 */
  if (!d.ok) {
    const hi = Math.max(0, ...d.available_weeks.map((w) => w.week))
    const lo = d.available_weeks.length ? Math.min(...d.available_weeks.map((w) => w.week)) : 0
    const median = d.available_weeks.length
      ? d.available_weeks[Math.floor(d.available_weeks.length / 2)].week
      : 0
    /* 【异常周不能写死 44】
       44 是这一份数据集里的最大异常周，但它是数据属性、不是代码常量：
       换一份数据、或者后端调了双门槛，44 可能就是个完全正常的周。
       从 available_weeks 里按 |delta_pp| 挑最狠的那一周，才不会写死一个
       在别的数据集里站不住的结论。 */
    const worst = d.available_weeks
      .filter((w) => w.is_anomaly)
      .sort((a, b) => Math.abs(b.delta_pp) - Math.abs(a.delta_pp))[0]
    /* 【不能只甩一句 Empty 就走】
       整页被一个空态占满，连周次选择器都没了 —— 用户被"卡"在 60 周这一屏，
       唯一的出路是改地址栏。越界本来是最需要给出退路的一种状态：
       所以要带上「最近一周」「数据中位周」两个一键跳转。 */
    return (
      <div className="pg">
        <Card title={`数据里没有第 ${d.week} 周`}>
          <Empty
            what={`当前数据集只有第 ${lo}–${hi} 周`}
            why={`你问的第 ${d.week} 周不在范围内。换一个周次，或直接跳过去看：`}
          />
          <div className="pg-note-line" style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button className="pg-btn ghost" onClick={() => setWeek(hi)}>
              跳到最近一周（第 {hi} 周）
            </button>
            <button className="pg-btn ghost" onClick={() => setWeek(median)}>
              跳到第 {median} 周
            </button>
            {worst && (
              <button className="pg-btn ghost" onClick={() => setWeek(worst.week)}>
                跳到异常周（第 {worst.week} 周，偏离 {pp(worst.delta_pp)}pp）
              </button>
            )}
          </div>
        </Card>
      </div>
    )
  }

  const anomalies = d.available_weeks.filter((w) => w.is_anomaly)
  const loss = d.structure ? money(d.structure.total_loss) : null

  /* 瀑布图的台阶。三部分：各类目贡献 → 结构效应 → 全店 Δ。
     【为什么按贡献绝对值从大到小排】
     瀑布的读法是"沿着台阶往下/往上走"，
     把最大的一节放最前面，读者第一眼就落在主因上；
     按类目名排序的话，主因可能藏在第三根柱子里。 */
  /* 损失总额：优先用后端给的 total_loss；万一缺失就按明细相加兜底，
     否则双环内环会全变成 0（除以 0 被 Math.max 兜成 1，8 个扇区都 0）。
     两者应当一致 —— 不一致说明后端口径变了，前端不能自己改分母。 */
  const lossTotal = d.structure
    ? (d.structure.total_loss || d.structure.items.reduce((s, x) => s + x.loss, 0))
    : 0

  const catSteps: WaStep[] = (() => {
    const items = [...d.category_contribution].sort(
      (a, b) => Math.abs(b.contribution_pp) - Math.abs(a.contribution_pp),
    )
    const steps: WaStep[] = items.map((c) => ({
      key: c.category,
      label: c.category,
      value: c.contribution_pp,
      sub: `占 ${c.orders_share.toFixed(0)}% 单`,
      tip: (
        <div>
          <div className="bi-tip-h">{c.category}</div>
          <div className="bi-tip-row">本周 <b>{c.rr_now.toFixed(2)}%</b></div>
          <div className="bi-tip-row">基准 <b>{c.rr_base.toFixed(2)}%</b></div>
          <div className="bi-tip-row">
            差距 <b className={c.gap_pp > 0 ? 't-bad' : 't-good'}>{pp(c.gap_pp)}pp</b>
          </div>
          <div className="bi-tip-row">占当周订单 <b>{c.orders_share.toFixed(1)}%</b></div>
          <div className="bi-tip-row">
            对全店的贡献 <b>{pp(c.contribution_pp)}pp</b>
          </div>
          <div className="bi-tip-row dim">贡献 = 差距 × 订单占比</div>
        </div>
      ),
    }))

    /* 结构效应：Σ类目贡献 与 delta_pp 的差。
       它不是"剩下的凑数项"，而是一个有名字的效应：
       订单在类目之间迁移（本周流向退货率更低的类目）。
       后端算好给出来，前端不自己减。 */
    if (typeof d.structure_effect_pp === 'number' && Math.abs(d.structure_effect_pp) > 0.005) {
      steps.push({
        key: '__mix',
        label: '结构效应',
        value: d.structure_effect_pp,
        sub: '订单迁移',
        tip: (
          <div>
            <div className="bi-tip-h">结构效应</div>
            <div className="bi-tip-row">
              数值 <b>{pp(d.structure_effect_pp)}pp</b>
            </div>
            <div className="bi-tip-row dim">
              各类目贡献相加是 {pp(d.contribution_sum_pp ?? 0)}pp，
              全店却是 {pp(d.delta_pp)}pp。
            </div>
            <div className="bi-tip-row dim">
              差出来的这部分来自"订单在类目之间怎么分"变了 ——
              本周订单流向了退货率更低的类目，
              所以即使每个类目内部都没变好，全店退货率也会降。
            </div>
          </div>
        ),
      })
    }

    steps.push({
      key: '__total',
      label: '全店 Δ',
      value: d.delta_pp,
      isTotal: true,
      sub: `第 ${d.week} 周`,
      tip: (
        <div>
          <div className="bi-tip-h">全店第 {d.week} 周</div>
          <div className="bi-tip-row">本周 <b>{d.return_rate.toFixed(2)}%</b></div>
          <div className="bi-tip-row">基准 <b>{d.baseline.toFixed(2)}%</b></div>
          <div className="bi-tip-row">
            变化 <b className={d.delta_pp > 0 ? 't-bad' : 't-good'}>{pp(d.delta_pp)}pp</b>
          </div>
          <div className="bi-tip-row dim">上面各节相加，应当落在这个数上</div>
        </div>
      ),
    })
    return steps
  })()

  return (
    <div className="pg">
      {/* ---- 周次选择器 ---- */}
      <Card pad={false} className="">
        <div className="pg-weekbar">
          <div className="pg-weekbar-l">
            <button className="pg-mini" onClick={() => setWeek(Math.max(1, d.week - 1))}
              disabled={d.week <= 1}>‹</button>
            <span className="pg-weekbar-v">
              第 <b>{d.week}</b> 周
            </span>
            <button className="pg-mini" onClick={() => setWeek(Math.min(52, d.week + 1))}
              disabled={d.week >= 52}>›</button>
            {d.is_anomaly
              ? <span className="pg-tag t-bad">异常周</span>
              : d.trivial_sig
                ? <span className="pg-tag t-warn">统计显著但幅度小</span>
                : <span className="pg-tag t-good">正常</span>}
          </div>
          <div className="pg-weekbar-r">
            <span className="pg-weekbar-hint">需要解释的周</span>
            {anomalies.map((a) => (
              <button
                key={a.week}
                className={`pg-wchip ${a.week === d.week ? 'on' : ''}`}
                onClick={() => setWeek(a.week)}
                title={`偏离基准 ${pp(a.delta_pp)}pp`}
              >
                W{a.week}
              </button>
            ))}
          </div>
        </div>
      </Card>

      {/* ---- ① 判定 ---- */}
      <div className="pg-kpi-row">
        <Stat label="本周退货率" value={d.return_rate.toFixed(2)} unit="%"
          sub={`基准 ${d.baseline.toFixed(2)}%`} />
        <Stat label="偏离基准" value={pp(d.delta_pp)} unit="pp"
          tone={d.delta_pp > 0 ? 'bad' : 'good'}
          sub={`业务门槛 ${d.min_effect_pp.toFixed(1)}pp`} />
        <Stat label="z 值" value={d.zscore.toFixed(2)}
          tone={Math.abs(d.zscore) >= 2 ? 'warn' : undefined}
          sub={`统计门槛 |z| ≥ 2`} />
        <Stat label="订单 / 退货" value={nf(d.orders)} unit="单"
          sub={`${nf(d.returns)} 条退货`} />
        <Stat
          label="可识别的最小效应"
          value={d.detectable_pp.toFixed(2)}
          unit="pp"
          sub={d.detectable_pp > d.min_effect_pp
            ? '样本不足以识别业务门槛，本周"无异常"不等于"没问题"'
            : `采样抖动约 ±${d.wobble_pp.toFixed(2)}pp`}
          tone={d.detectable_pp > d.min_effect_pp ? 'warn' : undefined}
        />
      </div>

      {/* 【原来的「为什么这样判」大卡已删】
          那张卡把 z≥2 / Δ≥1.5pp 和"异常"三个结论又摊了一遍，
          而这三件事上面 KPI 卡的副标题（统计门槛 / 业务门槛）
          和周次选择器里的「异常周」标签已经在说了 —— 同一屏说两次，
          读者第一反应是"这两处是不是不一样"。
          留下的只有那句判据理由：为什么必须两条同时成立。
          它没法从数字里看出来，删了就等于让人以为"z 大就是异常"。 */}
      <div className="pg-note-line">
        <b>判定规则：</b>异常 = 统计显著（|z| ≥ 2）<b>且</b>幅度过业务门槛（{d.min_effect_pp.toFixed(1)}pp）。
        只卡统计显著性，样本量大时会把 0.6pp 的日常抖动全报成异常。
      </div>

      {/* ---- ② 涨在哪 ---- */}
      <Card
        title="涨在哪：类目贡献度"
        note={<>
          贡献 = 该类目相对基准的差距 × 它占当周订单的比重。
          {catView === 'waterfall' && ' 瀑布图里每一节相加 = 最右边那根总柱，' }
          {catView === 'waterfall' && <b>结构效应</b>}
          {catView === 'waterfall' && '是订单在类目间迁移造成的差额，不是误差。'}
          {catView === 'dumbbell' && ' 空心点是基准、实心点是本周。横向位置 = 本来多高，两点距离 = 这周差多少 —— 这两件事发散条分不开。'}
          {catView === 'gap' && ' 零轴右侧 = 退货率高于基准（要查），左侧 = 低于基准。'}
          {catView === 'share' && ' 订单在四个类目之间怎么分 —— 这是部分与整体的关系。'}
          {' '}<b>点选一个类目</b>可钉住，切视图和明细都会保留。
        </>}
        pad={false}
        right={(
          <Segmented
            value={catView}
            onChange={setCatView}
            options={[
              { key: 'waterfall', label: '贡献瀑布' },
              { key: 'dumbbell', label: '本周vs基准' },
              { key: 'share', label: '订单占比' },
              { key: 'gap', label: '差距' },
              { key: 'bubble', label: '规模×差距' },
              { key: 'table', label: '明细' },
            ]}
          />
        )}
      >
        {d.category_contribution.length === 0 ? (
          <Empty what="这一周没有分到这个类目" />
        ) : catView === 'waterfall' ? (
          <Waterfall
            steps={catSteps}
            fmt={(v) => pp(v)}
            activeKey={catPick}
            onPick={(k) => {
              /* 点柱子 = 钉住这一类。再点一次取消。
                 钉住之后切到"占比""差距"，同一个类目仍然是高亮的，
                 三张图就能对着看同一行数据。 */
              if (k.startsWith('__')) return     // 结构效应/总计不是类目，不可点
              setCatPick(catPick === k ? null : k)
            }}
          />
        ) : catView === 'dumbbell' ? (
          /* 【补这一张的理由：另外四张都答不了"它本来就这么高吗"】
             发散条只画差值，丢掉了绝对水平 —— 12%→20% 和 30%→38%
             画出来一模一样，但后者才是真麻烦。
             这张把"本来多高"（横向位置，共用一把尺）和
             "这周差多少"（两点距离）分成两个通道。 */
          <Dumbbell
            items={d.category_contribution.map((c) => ({
              key: c.category,
              label: c.category,
              base: c.rr_base,
              now: c.rr_now,
              gap: c.gap_pp,
              z: c.z,
              weight: c.orders,
              tip: (
                <div>
                  <div className="bi-tip-h">{c.category}</div>
                  <div className="bi-tip-row">
                    基准 <b>{c.rr_base.toFixed(2)}%</b>
                    <span className="dim"> → 本周 </span>
                    <b>{c.rr_now.toFixed(2)}%</b>
                  </div>
                  <div className="bi-tip-row">
                    差距 <b className={c.gap_pp > 0 ? 't-bad' : 't-good'}>{pp(c.gap_pp)}pp</b>
                  </div>
                  <div className="bi-tip-row">
                    订单 <b>{nf(c.orders)}</b>
                    <span className="dim">（占 {c.orders_share.toFixed(1)}%）</span>
                  </div>
                  <div className="bi-tip-row">对全店贡献 <b>{pp(c.contribution_pp)}pp</b></div>
                  <div className="bi-tip-row dim">
                    横向位置 = 本来多高；两点距离 = 这周差多少。
                    {c.rr_base > 25 && c.gap_pp > 0
                      ? '它本来就偏高，这周又往上走 —— 先看这一类。'
                      : c.rr_base <= 25 && c.gap_pp > 2
                        ? '平时不高，这周才冲上去 —— 更像一次事件。'
                        : '两者都不突出。'}
                  </div>
                </div>
              ),
            }))}
            fmt={(v) => `${v.toFixed(0)}%`}
            onPick={(k) => setCatPick(catPick === k ? null : k)}
            activeKey={catPick}
          />
        ) : catView === 'share' ? (
          <Donut
            items={d.category_contribution.map((c) => ({
              key: c.category,
              label: c.category,
              value: c.orders_share,
              sub: `${nf(c.orders)} 单`,
            }))}
            centerLabel="当周订单"
            centerValue={`${nf(d.category_contribution.reduce((s, c) => s + c.orders, 0))}`}
            activeKey={catPick}
            onPick={(k) => setCatPick(catPick === k ? null : k)}
          />
        ) : catView === 'gap' ? (
          <Diverging
            items={d.category_contribution.map((c) => ({
              key: c.category,
              label: c.category,
              value: c.gap_pp,
              sub: `z ${c.z.toFixed(2)}`,
              tip: (
                <div>
                  <div className="bi-tip-h">{c.category}</div>
                  <div className="bi-tip-row">本周 <b>{c.rr_now.toFixed(2)}%</b></div>
                  <div className="bi-tip-row">基准 <b>{c.rr_base.toFixed(2)}%</b></div>
                  <div className="bi-tip-row">
                    差距 <b className={c.gap_pp > 0 ? 't-bad' : 't-good'}>{pp(c.gap_pp)}pp</b>
                  </div>
                  <div className="bi-tip-row dim">
                    {Math.abs(c.z) >= 2
                      ? 'z 超过 2：这个差距不太可能是抽样波动'
                      : 'z 不到 2：可能在正常波动范围内'}
                  </div>
                </div>
              ),
            }))}
            fmt={(v) => `${pp(v)}pp`}
            zeroLabel={['← 低于基准', '高于基准 →']}
            activeKey={catPick}
            onPick={(k) => setCatPick(catPick === k ? null : k)}
          />
        ) : catView === 'bubble' ? (
          /* 【为什么类目这一块用斜率图，而不是气泡图】
             上面三张图各自只回答一半问题：
               瀑布 → 贡献怎么加起来（但看不出盘子大小）
               环形 → 订单怎么分（但看不出谁在出问题）
               发散 → 差多少（但看不出差得多的那个盘子有多大）
             真实的决策问题还是那句话：**先管哪个类目？**
             它取决于「差多少」×「盘子多大」——两个维度。

             第 44 周有个很好的反例：
               服饰 差距最大（8.83pp），但只占 17.3% 订单，贡献 1.52pp
               生鲜 差距只有 2.79pp，却占 37.1% 订单，贡献 1.04pp
             只按差距排会把生鲜排到最后，只按订单量排会把服饰排到第三。

             【为什么最终没用气泡图】
             先写的就是气泡图，实测四个点被撑到画面四个角、中间全空 ——
             中位数分割线 + 16% 留白在 n=4 时把范围撑得过大。
             更根本的问题是：**4 个点用"看位置"来比较，恰恰是最不准的读法**。
             换成斜率图后，两列各自按自己的指标排序，中间连线：
             线是平的 = 两个排名一致（按哪个排都一样）；
             线很陡 = 排名错位，这个类目值得单独看。
             4 项数据下，"谁和谁交叉"比"谁在哪个象限"好读得多。 */
          <Slope
            items={d.category_contribution.map((c) => ({
              key: c.category,
              label: c.category,
              left: c.orders_share,
              right: c.gap_pp,
              weight: Math.max(c.contribution_pp, 0.01),
              tip: (
                <div>
                  <div className="bi-tip-h">{c.category}</div>
                  <div className="bi-tip-row">订单占比 <b>{c.orders_share.toFixed(1)}%</b>
                    <span className="dim"> · {nf(c.orders)} 单</span>
                  </div>
                  <div className="bi-tip-row">与基准差距 <b>{pp(c.gap_pp)}pp</b>
                    <span className="dim"> · {c.rr_now.toFixed(2)}% vs {c.rr_base.toFixed(2)}%</span>
                  </div>
                  <div className="bi-tip-row">对全店的贡献 <b>{pp(c.contribution_pp)}pp</b></div>
                  <div className="bi-tip-row">z 值 <b>{c.z.toFixed(2)}</b></div>
                  <div className="bi-tip-row dim">
                    贡献 = 差距 × 订单占比，左列排名和右列排名差得越多，
                    说明"哪个类目更严重"取决于你按哪个口径看
                  </div>
                </div>
              ),
            }))}
            leftLabel="订单占比 %（盘子大小）"
            rightLabel="与基准的差距 pp（超了多少）"
            fmtLeft={(v) => `${v.toFixed(1)}%`}
            fmtRight={(v) => `${pp(v)}pp`}
            /* 卡片说明写着"点选一个类目可钉住，切视图和明细都会保留"，
               所以这里必须把 onPick 接上 —— 不然那一句是空头承诺。 */
            onPick={(k) => setCatPick(catPick === k ? null : k)}
            activeKey={catPick}
            /* 4 行数据不需要 230px：行距会被拉开到 40px 以上，
               四条线在中间大片空白里飘着，反而看不出交叉关系。
               按行数给高度，行距稳定在 34px 左右。 */
            height={Math.max(d.category_contribution.length * 34 + 28, 120)}
          />
        ) : (
          <table className="pg-table">
            <thead>
              <tr>
                <th scope="col">类目</th>
                <th scope="col" className="r">本周</th>
                <th scope="col" className="r">基准</th>
                <th scope="col" className="r">差距</th>
                <th scope="col" className="r">订单占比</th>
                <th scope="col" className="r">贡献</th>
                <th scope="col" className="r">z</th>
              </tr>
            </thead>
            <tbody>
              {d.category_contribution.map((c) => (
                <tr
                  key={c.category}
                  /* 【这一行原来是"鼠标专享"】
                      只写了 onClick 和 is-pick：没有 clickable 类，所以既没有指针光标、
                      也没有悬停反馈；没有 tabIndex，<tr> 又不在浏览器默认可聚焦列表里，
                      键盘用户根本够不着；没有 Enter/空格，按了也没反应。
                      三个信号一起缺，鼠标用户看不出能点，键盘用户点不到，
                      而自动检查靠 cursor:pointer 判"可点"—— 没有它，这个缺陷连门禁都测不出来。
                      修法照 Plan.tsx 那套：clickable + tabIndex + 回车/空格 + aria-current。 */
                  className={`clickable${catPick === c.category ? ' is-pick' : ''}`}
                  aria-current={catPick === c.category ? 'true' : undefined}
                  tabIndex={0}
                  onClick={() => setCatPick(catPick === c.category ? null : c.category)}
                  onKeyDown={(e: KeyboardEvent<HTMLTableRowElement>) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      setCatPick(catPick === c.category ? null : c.category)
                    }
                  }}
                >
                  <td>{c.category}</td>
                  <td className="r num">{c.rr_now.toFixed(2)}%</td>
                  <td className="r num dim">{c.rr_base.toFixed(2)}%</td>
                  <td className={`r num ${c.gap_pp > 0 ? 't-bad' : 't-good'}`}>{pp(c.gap_pp)}</td>
                  <td className="r num">{c.orders_share.toFixed(1)}%</td>
                  <td className="r num">
                    <div className="pg-cell-bar">
                      <span className={c.contribution_pp > 0 ? 't-bad' : 't-good'}>
                        {pp(c.contribution_pp)}
                      </span>
                      <Bar
                        pct={Math.abs(c.contribution_pp) /
                          Math.max(...d.category_contribution.map((x) => Math.abs(x.contribution_pp)), 0.01) * 100}
                        tone={c.contribution_pp > 0 ? 'bad' : 'accent'}
                        height={4}
                      />
                    </div>
                  </td>
                  <td className="r num dim">{c.z.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* ---- ③ 卖的东西变了吗 ---- */}
      {d.structure && (
        <Card
          title="卖的东西变了吗：原因构成对比"
          note={<>
            本周 vs {d.structure.baseline_scope}。
            {stView === 'shift' && <><b>份额变化</b>接近 0 说明"没换货"，那这周的涨幅就不是选品造成的。</>}
            {stView === 'recover' && <>一条原因亏的钱里，<b className="t-good">多少还能通过二次销售拿回来</b>、多少是真亏。同样亏 10 万，可再售 80% 的降价清库存就行，可再售 10% 的只能报损 —— 这个区别决定动作，不只是排序。</>}
            {stView === 'matrix' && <>横轴是损失金额，纵轴是可再售率，气泡大小是条数。<b>右下角</b>是损失大且基本卖不掉的，最该先管。</>}
            {stView === 'dual' && <>外环按<b>条数</b>、内环按<b>金额</b>。两环宽度不一致 = 这条原因的"单件代价"和别的不一样，按条数排会排错优先级。</>}
            {stView === 'tree' && <>面积 = <b>损失金额</b>，按<b>一级归属</b>分组。单条原因不大的，加总后可能是最大一块 —— 这才是"该找谁谈"的问法。</>}
            {stView === 'table' && <>份额变化接近 0 说明"没换货"，那这周的涨幅就不是选品造成的。</>}
            {' '}<b>点选一条原因</b>可钉住，切视图和明细都会保留。
          </>}
          pad={false}
          right={(
            <Segmented
              value={stView}
              onChange={setStView}
              options={[
                { key: 'shift', label: '份额变化' },
                { key: 'recover', label: '挽回潜力' },
                { key: 'matrix', label: '损失×可再售' },
                { key: 'dual', label: '条数×金额' },
                { key: 'tree', label: '归属树图' },
                { key: 'table', label: '明细' },
              ]}
            />
          )}
        >
          {stView === 'shift' ? (
            <Diverging
              items={d.structure.items.map((x) => ({
                key: x.l2,
                label: x.l2,
                value: x.share_shift_pp,
                sub: `${x.share.toFixed(1)}%`,
                tip: (
                  <div>
                    <div className="bi-tip-h">{x.l2}</div>
                    <div className="bi-tip-row dim">{x.l1}</div>
                    <div className="bi-tip-row">本周份额 <b>{x.share.toFixed(2)}%</b></div>
                    <div className="bi-tip-row">基准份额 <b>{x.share_base.toFixed(2)}%</b></div>
                    <div className="bi-tip-row">
                      份额变化{' '}
                      <b className={Math.abs(x.share_shift_pp) >= 2 ? 't-warn' : ''}>
                        {pp(x.share_shift_pp)}pp
                      </b>
                    </div>
                    <div className="bi-tip-row">条数 <b>{nf(x.count)}</b></div>
                    <div className="bi-tip-row dim">
                      {Math.abs(x.share_shift_pp) >= 2
                        ? '份额移动超过 2pp：构成确实变了'
                        : '份额基本没动'}
                    </div>
                  </div>
                ),
              }))}
              fmt={(v) => `${pp(v)}pp`}
              zeroLabel={['← 份额减少', '份额增加 →']}
              onPick={pickSt}
              activeKey={stPick}
            />
          ) : stView === 'recover' ? (
            <StackedShare
              /* 按可挽回金额排序 —— 这才是"先救哪个"的排法。
                 按总损失排会把"亏得多但救不回来"的排前面，
                 那种只能报损，排在前面也不产生动作。 */
              sortBy="keep"
              onPick={pickSt}
              activeKey={stPick}
              items={d.structure.items.map((x) => {
                const keep = x.loss * x.resellable / 100
                return {
                  key: x.l2,
                  label: x.l2,
                  total: x.loss,
                  keep,
                  tip: (
                    <div>
                      <div className="bi-tip-h">{x.l2}</div>
                      <div className="bi-tip-row dim">{x.l1}</div>
                      <div className="bi-tip-row">
                        总损失 <b>{money(x.loss).v}{money(x.loss).u}</b>
                      </div>
                      <div className="bi-tip-row">
                        可挽回 <b className="t-good">{money(keep).v}{money(keep).u}</b>
                        <span className="dim">（可再售 {x.resellable.toFixed(1)}%）</span>
                      </div>
                      <div className="bi-tip-row">
                        净损失 <b>{money(x.loss - keep).v}{money(x.loss - keep).u}</b>
                      </div>
                      <div className="bi-tip-row">条数 <b>{nf(x.count)}</b></div>
                      <div className="bi-tip-row dim">
                        {x.resellable >= 60
                          ? '大部分能二次销售：降价清库存能抢回来'
                          : x.resellable < 30
                            ? '基本卖不掉：只能报损，别再往里投人力'
                            : '有一部分能救，按单件代价决定要不要救'}
                      </div>
                    </div>
                  ),
                }
              })}
            />
          ) : stView === 'matrix' ? (
            <Quadrant
              points={d.structure.items.map((x) => ({
                key: x.l2,
                label: x.l2,
                x: x.loss,
                y: x.resellable,
                size: x.count,
                tip: (
                  <div>
                    <div className="bi-tip-h">{x.l2}</div>
                    <div className="bi-tip-row dim">{x.l1}</div>
                    <div className="bi-tip-row">
                      损失 <b>{money(x.loss).v}{money(x.loss).u}</b>
                    </div>
                    <div className="bi-tip-row">可再售 <b>{x.resellable.toFixed(1)}%</b></div>
                    <div className="bi-tip-row">条数 <b>{nf(x.count)}</b></div>
                    <div className="bi-tip-row">
                      可挽回金额{' '}
                      <b>{money(x.loss * x.resellable / 100).v}{money(x.loss * x.resellable / 100).u}</b>
                    </div>
                    <div className="bi-tip-row dim">
                      {x.resellable < 30 && x.loss >= 8000
                        ? '损失大且基本卖不掉：真损失，优先处理'
                        : x.resellable >= 60
                          ? '大部分能二次销售，损失可以抢回来'
                          : '介于两者之间'}
                    </div>
                  </div>
                ),
              }))}
              xLabel="损失金额"
              yLabel="可再售率"
              fmtX={(v) => `${money(v).v}${money(v).u}`}
              fmtY={(v) => `${v.toFixed(0)}%`}
              quadrants={[
                '损失大 · 可挽回',
                '损失大 · 真损失',
                '损失小 · 可挽回',
                '损失小',
              ]}
              onPick={pickSt}
              activeKey={stPick}
            />
          ) : stView === 'dual' ? (
            /* 【为什么这张图能补上前面几张都看不到的东西】
               表格里"本周份额"和"损失"是两列并排的数字，8 行读下来
               没人会去逐行做减法；份额变化图看的是"结构有没有移动"，
               象限图看的是"损失和可再售的组合"—— 都不回答同一个问题：
               **按条数排出来的优先级，和按钱排出来的，一样吗？**

               第 44 周两个反例：
                 尺码版型不符  12.5% 的条数 → 只有 7.2% 的钱（可再售 81%）
                 功能性能故障   4.7% 的条数 → 却有 9.0% 的钱（基本报废）
               只看条数，尺码排在功能前面；只看钱，结论反过来。
               双环把两个口径放在同一条半径上，宽度差就是这个错位。 */
            <DualRing
              /* 【必须补一节"其余未单列"，否则环上会缺一块】
                 后端返回的 items 是 Top-N（第 44 周只有 8 类），
                 而 total_loss / total_returns 是全部。
                 实测：条数 3807 vs 共 3880（缺 73），
                       损失 245327 vs 共 248503（缺 3176，约 1.3%）。
                 直接拿 total_loss 当分母，内环只画到 98.7% ——
                 环上留一道没有名字的缝，看起来就是渲染坏了。
                 把它显式画成"其余未单列"，这个残差才变成一个可读的事实：
                 有 1.3% 的损失根本没被归到任何原因里，那本身值得追。 */
              items={(() => {
                const rest = (() => {
                  const nSum = d.structure.items.reduce((s, x) => s + x.count, 0)
                  const lSum = d.structure.items.reduce((s, x) => s + x.loss, 0)
                  const nRest = d.structure.total_returns - nSum
                  const lRest = lossTotal - lSum
                  if (nRest <= 0 && lRest <= 0) return null
                  return {
                    key: '__rest',
                    label: '其余未单列',
                    outer: (nRest / Math.max(d.structure.total_returns, 1)) * 100,
                    inner: (lRest / Math.max(lossTotal, 1)) * 100,
                    outerText: `${nf(nRest)} 条`,
                    innerText: `${money(lRest).v}${money(lRest).u}`,
                  }
                })()
                return [
                  ...d.structure.items.map((x) => ({
                    key: x.l2,
                    label: x.l2,
                    outer: x.share,
                    inner: (x.loss / Math.max(lossTotal, 1)) * 100,
                    outerText: `${nf(x.count)} 条`,
                    innerText: `${money(x.loss).v}${money(x.loss).u}`,
                  })),
                  ...(rest ? [rest] : []),
                ]
              })()}
              outerLabel="条数份额"
              innerLabel="金额份额"
              centerLabel="本周损失合计"
              centerValue={`${money(lossTotal).v}${money(lossTotal).u}`}
              onPick={pickSt}
              activeKey={stPick}
            />
          ) : stView === 'tree' ? (
            /* 【为什么这一张是补的，而不是重复】
               上面四张（份额变化 / 损失×可再售 / 条数×金额 / 明细）
               全部把两级归属**压平到二级**。被压平掉的那一层会改变结论：
               第 44 周产品本体的三条原因单看分别排第 4、5、6，
               一条都进不了前二；但按一级归属加总是 30.0%，
               是第二大类，离第一名只差 1,265 元。
               "该找哪个归属谈"是按 l1 问的，按 l2 排的图回答不了。 */
            <Treemap
              leaves={(() => {
                /* 显式标注类型：不加的话会被推断成 items.map 的形状
                   （sub 是必需的 string），后面推"未归因"那条就塞不进 undefined */
                const leaves: TreeLeaf[] = d.structure.items.map((x) => ({
                  key: x.l2,
                  group: x.l1,
                  label: x.l2,
                  /* 面积给钱，不给条数 —— 两个口径在这一份数据里会给出相反的优先级 */
                  value: x.loss,
                  sub: `${nf(x.count)} 条 · 可再售 ${x.resellable.toFixed(0)}%`,
                  tip: (
                    <div>
                      <div className="bi-tip-h">{x.l2}</div>
                      <div className="bi-tip-row dim">{x.l1}</div>
                      <div className="bi-tip-row">
                        损失 <b>{money(x.loss).v}{money(x.loss).u}</b>
                        <em className="dim"> 占总损失 {((x.loss / Math.max(lossTotal, 1)) * 100).toFixed(1)}%</em>
                      </div>
                      <div className="bi-tip-row">条数 <b>{nf(x.count)}</b>（占 {x.share.toFixed(1)}%）</div>
                      <div className="bi-tip-row">可再售 <b>{x.resellable.toFixed(1)}%</b></div>
                      <div className="bi-tip-row">
                        可挽回 <b>{money(x.loss * x.resellable / 100).v}{money(x.loss * x.resellable / 100).u}</b>
                      </div>
                      <div className="bi-tip-row">
                        份额变化{' '}
                        <b className={Math.abs(x.share_shift_pp) >= 2 ? 't-warn' : ''}>
                          {pp(x.share_shift_pp)}pp
                        </b>
                      </div>
                      <div className="bi-tip-row dim">
                        {x.resellable < 30
                          ? '基本卖不掉：这部分是净损失'
                          : x.resellable >= 60
                            ? '大部分能二次销售，损失还有救'
                            : '介于两者之间'}
                      </div>
                    </div>
                  ),
                }))
                /* 【必须补一节"未归因"，和双环图同一个理由】
                   items 是 Top-N，而 total_loss 是全部。
                   实测第 44 周：items 损失合计 245,327，total_loss 248,503，
                   差 3,176（1.3%）。
                   不补这块，树图面积只画到总损失的 98.7% ——
                   而格子上又写着"占总损失 x%"，加起来不到 100%，
                   读者会以为哪里算错了。
                   补成"未归因"之后，这块残差本身也变成一个可读的事实：
                   有 1.3% 的损失根本没被任何原因覆盖，那件事值得追。 */
                const rest = lossTotal - d.structure.items.reduce((s, x) => s + x.loss, 0)
                const restN = d.structure.total_returns
                  - d.structure.items.reduce((s, x) => s + x.count, 0)
                if (rest > 0.5) {
                  leaves.push({
                    key: '__rest',
                    group: '未归因',
                    label: '其余未单列',
                    value: rest,
                    sub: restN > 0 ? `${nf(restN)} 条` : undefined,
                    tip: (
                      <div>
                        <div className="bi-tip-h">其余未单列</div>
                        <div className="bi-tip-row dim">不在 Top-N 原因里的部分</div>
                        <div className="bi-tip-row">
                          损失 <b>{money(rest).v}{money(rest).u}</b>
                          <em className="dim"> 占总损失 {((rest / Math.max(lossTotal, 1)) * 100).toFixed(1)}%</em>
                        </div>
                        {restN > 0 && (
                          <div className="bi-tip-row">条数 <b>{nf(restN)}</b></div>
                        )}
                        <div className="bi-tip-row dim">
                          这部分钱没被任何原因解释 —— 覆盖度本身是个可改进项
                        </div>
                      </div>
                    ),
                  })
                }
                return leaves
              })()}
              fmt={(v) => `${money(v).v}${money(v).u}`}
              onPick={pickSt}
              activeKey={stPick}
            />
          ) : (
            <table className="pg-table">
              <thead>
                <tr>
                  <th scope="col">原因</th>
                  <th scope="col">层级</th>
                  <th scope="col" className="r">本周条数</th>
                  <th scope="col" className="r">本周份额</th>
                  <th scope="col" className="r">基准份额</th>
                  <th scope="col" className="r">份额变化</th>
                  <th scope="col" className="r">损失</th>
                  <th scope="col" className="r">可再售</th>
                </tr>
              </thead>
              <tbody>
                {d.structure.items.map((x) => (
                  /* 【明细行也要响应"钉住"】
                     图里选了「材质做工问题」再切到明细，
                     8 行数字长得一模一样，得自己一行行找 ——
                     等于把刚才那次点击白费了。
                     行上带 data-pick，CSS 负责高亮；同时给 tabIndex，
                     让键盘也能把焦点落到这一行（读屏会念出整行）。 */
                  <tr
                    key={x.l2}
                    className={stPick === x.l2 ? 'is-pick' : undefined}
                    onClick={() => pickSt(x.l2)}
                  >
                    <td>{x.l2}</td>
                    <td className="dim">{x.l1}</td>
                    <td className="r num">{nf(x.count)}</td>
                    <td className="r num">{x.share.toFixed(2)}%</td>
                    <td className="r num dim">{x.share_base.toFixed(2)}%</td>
                    <td className={`r num ${Math.abs(x.share_shift_pp) >= 2 ? 't-warn' : 'dim'}`}>
                      {pp(x.share_shift_pp)}
                    </td>
                    <td className="r num">{money(x.loss).v}{money(x.loss).u}</td>
                    <td className="r num dim">{x.resellable.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="pg-note-inline">
            本周共 {nf(d.structure.total_returns)} 条退货，净损失
            {' '}{loss?.v}{loss?.u}；对照口径为「{d.structure.baseline_scope}」
            {' '}{nf(d.structure.baseline_returns)} 条。
          </div>
        </Card>
      )}

      {/* ---- ④ 促销机制 ---- */}
      {/* 只在"这一周本身是促销周"时才展开。原因见后端 pages.py 里 is_promo_week 的注释：
          promo_mechanism 算的是促销周 vs 非促销周的整体对照，不接 week 参数。
          放在"这一周为什么涨"的标题下而不加这个条件，非促销周也会被解释成促销导致的。 */}
      {d.promo && d.promo.is_promo_week && (
        <Card
          title="是不是促销本身导致的"
          note={<>把涨幅拆成<b>结构效应</b>（卖的东西变了）和<b>机制效应</b>（同样的东西变了）。配对检验比的是同一批 SKU 自己。这里是{d.promo.promo_weeks.length > 1 ? `${d.promo.promo_weeks.length} 次` : ''}促销周的<b>合并结论</b>，不是单看第 {d.week} 周。</>}
        >
          {/* 【三个数并列，不做堆叠条 —— 这里原来摆了个说不通的等式】
              过去是一条"结构 + 机制"的堆叠条，右边标"合计 3.28pp"。
              但机制 3.98 是 151 个 SKU 自身涨幅的等权平均，
              合计 3.28 是按订单加权的总体差 —— 两个口径本来就不该相加。
              画成堆叠条，几何上就在宣称"两段相加等于全长"；
              而 0.04 + 3.98 = 4.02，比标出来的 3.28 多 0.74pp，这个差额没有出处。
              改成并列账目：每一项都标清它对的是谁，不画"相加"这个暗示。 */}
          <div className="pg-ledger">
            <div className="pg-ledger-i">
              <div className="pg-ledger-k">实际涨幅</div>
              <div className="pg-ledger-v num t-bad">
                {pp(d.promo.actual_gap_pp)}<small>pp</small>
              </div>
              <div className="pg-ledger-s">
                促销周 {d.promo.rr_promo.toFixed(2)}% <span className="dim">vs</span> 非促销周{' '}
                {d.promo.rr_base.toFixed(2)}%，按订单加权
              </div>
            </div>
            <div className="pg-ledger-i is-quiet">
              <div className="pg-ledger-k">结构效应</div>
              <div className="pg-ledger-v num">
                {pp(d.promo.structure_pp)}<small>pp</small>
              </div>
              <div className="pg-ledger-s">
                在"结构 / 机制"两项里只占 {d.promo.structure_share.toFixed(1)}%
                —— <b>不是"卖的东西变了"</b>
              </div>
            </div>
            <div className="pg-ledger-i">
              <div className="pg-ledger-k">同批 SKU 自身涨幅</div>
              <div className="pg-ledger-v num t-bad">
                {pp(d.promo.mean_diff_pp)}<small>pp</small>
              </div>
              <div className="pg-ledger-s">
                {d.promo.n_sku} 个 SKU 的等权平均（标准差 {d.promo.sd_pp.toFixed(2)}pp）
                <span className="pg-ledger-warn">与"实际涨幅"口径不同，不可相加</span>
              </div>
            </div>
            {/* 【为什么这三个数不能相加，得说出来】
                结构与自身之和比实际涨幅多一个 residual_pp 这么多。不说清，
                读者会自己去做那个加法，然后得出"这页算错了"。
                差额是权重口径造成的：实际涨幅按订单加权，自身涨幅是每个 SKU 等权。
                差额由后端给（promo.residual_pp），前端不自己减。

                位置：这行是**账目块的脚注**，所以放在 .pg-ledger 内部当全宽底行。
                原来它放在账目和 KPI 之间，上下间距几乎一样（10 vs 12），
                读者分不清它属于账目还是 KPI 的标题。并进去之后归属没有歧义，
                外面也不用再多一层间距。 */}
            <div className="pg-ledger-note">
              三个数<b>不能相加</b>：实际涨幅与「结构 + 自身」相差{' '}
              {Math.abs(d.promo.residual_pp).toFixed(2)}pp。
              差额来自权重口径 —— 实际涨幅按订单加权，自身涨幅是 {d.promo.n_sku} 个 SKU 等权，不是算错。
            </div>
          </div>

          {/* KPI 只留"这次检验有多硬"，不再放「平均自身涨幅」——
              那就是上面第三格的那个 3.98，同一张卡里换个名字说第二遍。 */}
          <div className="pg-kpi-row tight">
            <Stat label="配对 SKU" value={d.promo.n_sku} unit="个"
              sub="同一批商品在促销周与非促销周各算一次" />
            <Stat label="t 值" value={d.promo.t_stat.toFixed(1)}
              sub="配对 t 检验" tone={Math.abs(d.promo.t_stat) > 2 ? 'warn' : undefined} />
            <Stat label="变差的 SKU 占比" value={d.promo.pct_sku_increased.toFixed(1)} unit="%"
              tone="bad" sub="自身退货率上升的比例" />
          </div>

          {/* 结论只留 headline 和治理方向。
              后端 verdict 里还有三条 evidence，但它们是上面三个账目格
              （构成没变 / 同批 SKU 变差）和下面四格排他性检验（没降价 / 没换低质）
              的文字复述。同一件事在一张卡里说第二遍，读者只会去比对两处措辞。 */}
          <div className="pg-verdict">
            <div className="pg-verdict-h">{d.promo.verdict.headline}</div>
            <div className="pg-verdict-impl">
              <span className="pg-verdict-k">治理方向</span>
              {d.promo.verdict.implication}
            </div>
          </div>

          {/* 排他性检验：如果不是促销机制，这些指标就该有异常 */}
          <div className="pg-alt">
            <div className="pg-alt-h">
              排他性检验 <span>如果不是"促销机制"，那下面这几项应该能看到异常</span>
            </div>
            <div className="pg-alt-grid">
              <AltItem
                k="降价（中位数）"
                v={`${d.promo.alt.price_change_median_pct.toFixed(1)}%`}
                ok={Math.abs(d.promo.alt.price_change_median_pct) < 3}
                okTxt="没降价" badTxt="有明显降价"
              />
              <AltItem
                k="打折 SKU 占比"
                v={`${d.promo.alt.pct_sku_discounted.toFixed(1)}%`}
                ok={d.promo.alt.pct_sku_discounted < 10}
                okTxt="几乎没打折" badTxt="大面积打折"
              />
              <AltItem
                k="质量分（促销 − 基准）"
                v={pp(d.promo.alt.quality_gap, 4)}
                ok={Math.abs(d.promo.alt.quality_gap) < 0.05}
                okTxt="质量没变" badTxt="质量显著变化"
              />
              <AltItem
                k="转化率（促销 / 基准）"
                v={`${d.promo.alt.cvr_promo.toFixed(2)}% / ${d.promo.alt.cvr_base.toFixed(2)}%`}
                ok={Math.abs(d.promo.alt.cvr_promo - d.promo.alt.cvr_base) < 0.5}
                okTxt="流量质量没变" badTxt="流量质量变了"
              />
            </div>
          </div>
        </Card>
      )}

      {/* 非促销周：促销这条排除项不适用。
          用一行说明带过，不留一张空卡 —— 但也不能凭空消失：
          "这一周为什么涨"的论证链里，④ 是排除项之一，
          读者突然看不到它，会以为漏了一环。 */}
      {d.promo && !d.promo.is_promo_week && (
        <div className="pg-note-line">
          <b>第 {d.week} 周不是促销周</b>
          {d.promo.promo_weeks.length > 0 &&
            `（本数据里的促销周是第 ${d.promo.promo_weeks.join('、')} 周）`}
          ，所以「是不是促销导致的」这条排除项对本周不适用，下面不展开。
        </div>
      )}

      {busy && <div className="pg-busy">切换周次…</div>}
    </div>
  )
}

/** 排他性检验的一格。 */
function AltItem({
  k, v, ok, okTxt, badTxt,
}: { k: string; v: string; ok: boolean; okTxt: string; badTxt: string }) {
  return (
    <div className="pg-alt-item">
      <div className="pg-alt-k">{k}</div>
      <div className="pg-alt-v">{v}</div>
      <div className={`pg-alt-s ${ok ? 'ok' : 'bad'}`}>{ok ? okTxt : badTxt}</div>
    </div>
  )
}