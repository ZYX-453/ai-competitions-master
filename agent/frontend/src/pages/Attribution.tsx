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
 * 【为什么把 stat_significant / biz_significant 都显示出来】
 * 界面上只写"异常 / 正常"，读者无法反驳，只能选择信或不信。
 * 把双门槛的两个条件都摊开，他自己就能判断
 * "z=3.97 但只偏离 0.99pp，所以不报"是否合理。
 * =========================================================================== */
import { useEffect, useState } from 'react'
import { getJson } from '../lib/sse'
import type { AttributionData } from '../lib/pages'
import { Card, Stat, Bar, Empty, Loading, Fail, nf, money, pp } from '../components/PageKit'

export function Attribution({
  week, setWeek,
}: { week: number; setWeek: (w: number) => void }) {
  const [d, setD] = useState<AttributionData | null>(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const [busy, setBusy] = useState(false)

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

  const anomalies = d.available_weeks.filter((w) => w.is_anomaly)
  const loss = d.structure ? money(d.structure.total_loss) : null

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

      {/* ---- 双门槛 ---- */}
      <Card
        title="为什么这样判"
        note={<>判据是<b>两条同时成立</b>。只卡统计显著性，会在样本量大时把 0.6pp 的日常抖动全报成异常。</>}
      >
        <div className="pg-gate">
          <Gate
            ok={d.stat_significant}
            label="统计显著"
            rule="|z| ≥ 2"
            actual={`z = ${d.zscore.toFixed(2)}`}
            why="这一周的偏离幅度，用二项 z 检验判断" 
          />
          <div className="pg-gate-op">且</div>
          <Gate
            ok={d.biz_significant}
            label="业务值得管"
            rule={`|Δ| ≥ ${d.min_effect_pp.toFixed(1)}pp`}
            actual={`Δ = ${pp(d.delta_pp)} pp`}
            why="偏离不够大时，改动作的成本高于收益"
          />
          <div className="pg-gate-op">=</div>
          <div className={`pg-gate-res ${d.is_anomaly ? 'bad' : 'ok'}`}>
            {d.is_anomaly ? '异常' : '不报'}
            <small>
              {d.is_anomaly
                ? '需要解释并动手'
                : d.trivial_sig
                  ? '显著，但幅度不值得动'
                  : '两条都不满足'}
            </small>
          </div>
        </div>
      </Card>

      {/* ---- ② 涨在哪 ---- */}
      <Card
        title="涨在哪：类目贡献度"
        note={<>贡献 = 该类目相对基准的差距 × 它占当周订单的比重。各项相加 ≈ 全店 Δpp。</>}
        pad={false}
      >
        {d.category_contribution.length === 0 ? (
          <Empty what="这一周没有分到这个类目" />
        ) : (
          <table className="pg-table">
            <thead>
              <tr>
                <th>类目</th>
                <th className="r">本周</th>
                <th className="r">基准</th>
                <th className="r">差距</th>
                <th className="r">订单占比</th>
                <th className="r">贡献</th>
                <th className="r">z</th>
              </tr>
            </thead>
            <tbody>
              {d.category_contribution.map((c) => (
                <tr key={c.category}>
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
            <b>份额变化</b>接近 0 说明"没换货"，那这周的涨幅就不是选品造成的。
          </>}
          pad={false}
        >
          <table className="pg-table">
            <thead>
              <tr>
                <th>原因</th>
                <th>层级</th>
                <th className="r">本周条数</th>
                <th className="r">本周份额</th>
                <th className="r">基准份额</th>
                <th className="r">份额变化</th>
                <th className="r">损失</th>
                <th className="r">可再售</th>
              </tr>
            </thead>
            <tbody>
              {d.structure.items.map((x) => (
                <tr key={x.l2}>
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
          <div className="pg-note-inline">
            本周共 {nf(d.structure.total_returns)} 条退货，净损失
            {' '}{loss?.v}{loss?.u}；对照口径为「{d.structure.baseline_scope}」
            {' '}{nf(d.structure.baseline_returns)} 条。
          </div>
        </Card>
      )}

      {/* ---- ④ 促销机制 ---- */}
      {d.promo && (
        <Card
          title="是不是促销本身导致的"
          note={<>把涨幅拆成<b>结构效应</b>（卖的东西变了）和<b>机制效应</b>（同样的东西变了）。配对检验比的是同一批 SKU 自己。</>}
        >
          <div className="pg-decomp">
            <div className="pg-decomp-bar">
              <div
                className="pg-decomp-seg s-struct"
                style={{
                  width: `${Math.max(2, Math.abs(d.promo.structure_pp) /
                    Math.max(Math.abs(d.promo.structure_pp) + Math.abs(d.promo.mechanism_pp), 0.01) * 100)}%`,
                }}
                title={`结构效应 ${pp(d.promo.structure_pp)}pp`}
              >
                <span>结构 {pp(d.promo.structure_pp)}</span>
              </div>
              <div
                className="pg-decomp-seg s-mech"
                style={{
                  width: `${Math.max(2, Math.abs(d.promo.mechanism_pp) /
                    Math.max(Math.abs(d.promo.structure_pp) + Math.abs(d.promo.mechanism_pp), 0.01) * 100)}%`,
                }}
                title={`机制效应 ${pp(d.promo.mechanism_pp)}pp`}
              >
                <span>机制 {pp(d.promo.mechanism_pp)}</span>
              </div>
            </div>
            <div className="pg-decomp-legend">
              合计 {pp(d.promo.actual_gap_pp)}pp
              （促销周 {d.promo.rr_promo.toFixed(2)}% vs 非促销周 {d.promo.rr_base.toFixed(2)}%）。
              结构效应只占 <b>{d.promo.structure_share.toFixed(1)}%</b>
              —— 也就是说，这周的涨幅不是"卖的东西变了"造成的。
            </div>
          </div>

          <div className="pg-kpi-row tight">
            <Stat label="配对 SKU" value={d.promo.n_sku} unit="个"
              sub="同一批商品在促销周与非促销周各算一次" />
            <Stat label="平均自身涨幅" value={pp(d.promo.mean_diff_pp)} unit="pp"
              sub={`标准差 ${d.promo.sd_pp.toFixed(2)}pp`} tone="bad" />
            <Stat label="t 值" value={d.promo.t_stat.toFixed(1)}
              sub="配对 t 检验" tone={Math.abs(d.promo.t_stat) > 2 ? 'warn' : undefined} />
            <Stat label="变差的 SKU 占比" value={d.promo.pct_sku_increased.toFixed(1)} unit="%"
              tone="bad" sub="自身退货率上升的比例" />
          </div>

          <div className="pg-verdict">
            <div className="pg-verdict-h">{d.promo.verdict.headline}</div>
            <ul className="pg-verdict-list">
              {d.promo.verdict.evidence.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
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

      {/* ---- 本周原因构成 ---- */}
      {d.week_causes && (
        <div className="pg-2col">
          <Card title="本周退货原因构成" note={<>只列条数前 8。</>} pad={false}>
            <table className="pg-table">
              <thead>
                <tr>
                  <th>原因</th>
                  <th className="r">条数</th>
                  <th className="r">占比</th>
                  <th className="r">损失</th>
                </tr>
              </thead>
              <tbody>
                {d.week_causes.map((c) => (
                  <tr key={c.code}>
                    <td>{c.cause}</td>
                    <td className="r num">{nf(c.n)}</td>
                    <td className="r num">{c.share.toFixed(2)}%</td>
                    <td className="r num">{money(c.loss).v}{money(c.loss).u}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="pg-note-inline">
              本周共 {nf(d.week_returns_total ?? 0)} 条退货。
              <b>这里的份额只描述"这周的样子"，不描述"和平时比多了什么"</b>
              —— 要看变化，用上面那张构成对比表。
            </div>
          </Card>

          <Card title="同周类目对比" note={<>这周的退货率，按类目拆。</>} pad={false}>
            <table className="pg-table">
              <thead>
                <tr>
                  <th>类目</th>
                  <th className="r">退货率</th>
                  <th className="r">订单</th>
                  <th className="r">退货</th>
                </tr>
              </thead>
              <tbody>
                {d.week_by_category.map((c) => (
                  <tr key={c.category}>
                    <td>{c.category}</td>
                    <td className="r num">{c.rr.toFixed(2)}%</td>
                    <td className="r num dim">{nf(c.orders)}</td>
                    <td className="r num dim">{nf(c.returns)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}

      {busy && <div className="pg-busy">切换周次…</div>}
    </div>
  )
}

/** 双门槛里的单个条件。 */
function Gate({
  ok, label, rule, actual, why,
}: { ok: boolean; label: string; rule: string; actual: string; why: string }) {
  return (
    <div className={`pg-gate-item ${ok ? 'ok' : 'no'}`}>
      <div className="pg-gate-top">
        <span className="pg-gate-ic">{ok ? '✓' : '✕'}</span>
        <span className="pg-gate-label">{label}</span>
      </div>
      <div className="pg-gate-rule">{rule}</div>
      <div className="pg-gate-actual">{actual}</div>
      <div className="pg-gate-why">{why}</div>
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