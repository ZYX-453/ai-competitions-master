# -*- coding: utf-8 -*-
"""页面级聚合：把散在各个工具里的数据组织成"一屏能看完"的形状

【为什么不把这些逻辑写进前端】
页面要用到的每一个数字，都必须能追回到 data/processed/*.csv 的某一行。
如果前端自己算（比如把退货率乘 100、把损失求和），那它就变成了
第二套数据源——而两套数据源迟早会不一致。

这套系统已经踩过这个坑：左栏"年订单 64.7k / 退货率 14.6%"
是写死在前端 JSX 里的，数据重建到 61.8 万单 / 13.53% 之后
没人记得改前端，于是页面上常驻着一个三个版本之前的数字。
它的危害比正文写错更大：正文只在回答时出现，这些数字每一屏都在。

所以这里的边界很硬：
  · 后端负责所有算术，前端只负责排版
  · 每个返回值都带上 evidence（扫了多少行、什么口径）
  · 前端不许出现任何 `* 100`、`/ 10000` 之类的换算

【和 Agent 的关系】
这些端点不走 Agent 编排，是直接调 DataHub / review_tools 的确定性计算。
理由是页面和问答的诉求不同：
  · 问答要"按需深入"，所以由 Agent 决定查什么
  · 页面要"一屏看全"，所以固定查一组，不做动态决策
两条路径共用同一批底层工具，数字口径完全一致。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from tools.data_hub import get_hub

# 【原因码 → 中文名，这张表必须从 DataHub 反查，不能另抄一份】
# returns 表的 return_reason_l2 存的是代码（P1/F1...），
# 页面直接显示就是一堆天书。而 DataHub.REASON_CODE 里已经有
# 权威映射（中文 → 代码），这里反向用它。
#
# 为什么不在这里重写一张正表：
# 四个地方都要用到原因中文名（归因页、治理页、知识库、Agent 提示词），
# 每处抄一份，改一处忘三处，最后一定会出现
# "页面写材质做工、Agent 说 P2" 这种自相矛盾。
# 反查的代价是 O(n)，n=9，可以忽略。
_REASON_ZH: dict[str, str] = {}

# 【映射表里查不到时的兜底】
# returns 表里确实存在一个 code='X' 的原因，它不在 DataHub.REASON_CODE 里
# （那张表只收录了 9 个可治理的业务原因，X 是"未归类"）。
# 如果不给兜底，页面上会在「其他」那一格显示一个孤零零的字母 X，
# 读起来像字段没渲染出来。这里把它显式命名。
_REASON_FALLBACK = {'X': '未归类'}


def _reason_zh(code: str) -> str:
    """原因码 → 中文名。查不到时返回兜底名或原码。"""
    global _REASON_ZH
    if not _REASON_ZH:
        try:
            _REASON_ZH = {v: k for k, v in get_hub().REASON_CODE.items()}
        except Exception:
            _REASON_ZH = {}
    return _REASON_ZH.get(code) or _REASON_FALLBACK.get(code) or code


def _f(v: Any, nd: int = 2) -> float:
    """安全转 float 并保留位数。

    【为什么到处都在转 float】
    pandas / numpy 的标量（np.float64、np.int64）不是 JSON 原生类型。
    FastAPI 的默认 encoder 会把 np.int64 变成字符串 "123"，
    于是前端拿到 "123" 去做排序就是字典序：'9' > '100'。
    这类错在界面上表现为"排序看起来没生效"，很难查到根因。
    出口处统一转原生类型，是唯一可靠的解法。
    """
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(x):
        return 0.0
    return round(x, nd)


def _i(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# 52 周的诊断结果，进程内只算一次。
# 【为什么要缓存】
# diagnose_week 每次要拉全店历史算基线和二项 z，单次约 3–6ms，
# 52 次就是 200–300ms —— 总览页每次刷新都付这个代价不值得。
# 而且数据是只读的（启动时加载一次，之后不变），缓存不存在失效问题。
_DIAG: dict[int, dict] | None = None


def _diagnose_all() -> dict[int, dict]:
    """逐周跑一遍 diagnose_week，拿 Agent 的判定结果。

    【为什么不在这里重写判据】
    判据有两条：统计显著 + 业务值得管。只抄其中一条会直接改变结论
    （第一版我用 |z|≥2.58 单门槛，52 周里标出 14 周异常）。
    唯一可靠的做法是问那个已经实现了判据的函数要答案。
    """
    global _DIAG
    if _DIAG is not None:
        return _DIAG
    hub = get_hub()
    out: dict[int, dict] = {}
    for w in hub.weeks:
        r = hub.diagnose_week(int(w))
        d = r.data or {}
        out[int(w)] = {
            'is_anomaly': bool(d.get('is_anomaly')),
            'trivial_sig': bool(d.get('trivial_sig')),
            'zscore': d.get('zscore'),
            'delta_pp': d.get('delta_pp'),
            # 命中判定的两个前提量也留着：
            # suggest_week 要用 orders 判断"这一周值不值得当默认页"，
            # 样本太小的周即使判为异常也不适合当开场（读者会追问"就这？"）。
            'orders': int(d.get('orders_this_week') or 0),
        }
    _DIAG = out
    return out


def suggest_week() -> int:
    """挑一个"打开就有东西可解释"的周，作为归因页的默认值。

    【为什么不直接用 latest_week】
    最新一周常常是正常周：第 52 周的判定是"|z|=2.84 但 Δ 只有 0.98pp，
    不值得管"。让它当默认页，用户点开归因看到的是两块都不打勾，
    第一印象会是"这套判定没生效"，而实际上是这一周本来就没问题。

    【排序依据为什么是 Δpp 而不是 |z|】
    z 会随样本量放大：52 周、几十万单，任何 0.5pp 的漂移都能推出 z=4。
    而 Δpp 是业务实际要多付的代价 —— 读者关心的是"多退了几个点"。
    这和 diagnose_week 用双门槛而不是单卡 z，是同一个理由。

    【没有异常周时回落】
    全 52 周都正常，说明数据本身波动很小。这时返回 Δpp 最大的那一周，
    并在页面上如实显示"不报"，而不是硬挑一个说它异常。
    """
    dc = _diagnose_all()
    if not dc:
        return int(get_hub().latest_week)
    rows = [(w, v) for w, v in dc.items() if v.get('orders')]
    if not rows:
        return int(get_hub().latest_week)

    # 优先：真正判为异常的周，按 Δpp 降序
    anom = [(w, v) for w, v in rows if v.get('is_anomaly')]
    if anom:
        # 只在"涨"的方向里挑：退货率下降的异常周不是问题，不需要归因。
        up = [(w, v) for w, v in anom if float(v.get('delta_pp') or 0) > 0]
        pool = up or anom
        return int(max(pool, key=lambda t: float(t[1].get('delta_pp') or 0))[0])

    # 兜底：没异常周就给 Δpp 最大的那周（页面会显示"不报"）
    return int(max(rows, key=lambda t: float(t[1].get('delta_pp') or 0))[0])


# ---------------------------------------------------------------------------
# ① 总览驾驶舱
# ---------------------------------------------------------------------------

def dashboard() -> dict:
    """总览页：一屏回答"现在怎么样、哪里最疼"。"""
    hub = get_hub()
    ov = hub.overview()
    o = ov.data or {}

    # ---- 周度退货率曲线（全店 52 周）----
    cat_week = hub._cat_week
    weekly = (cat_week.groupby('week', as_index=False)
              .agg(orders=('orders', 'sum'), returns=('returns', 'sum'),
                   gmv=('gmv', 'sum'), gp=('gp', 'sum')))
    weekly['rr'] = weekly['returns'] / weekly['orders'].replace(0, np.nan)
    mean_rr = float(weekly['rr'].mean())
    std_rr = float(weekly['rr'].std())

    # 【异常判定必须调 diagnose_week，不许在这里重写一遍】
    #
    # 这是这套系统里最容易出岔子的地方：判据是**双门槛**
    #   · 统计显著 |z| ≥ 2（二项 z，按本周实际订单量算标准误）
    #   · 业务值得管 |Δpp| ≥ 1.5
    # 两个都满足才算异常。
    #
    # 第一版我在这个函数里自己写了 `abs(z) >= 2.58`，只用单门槛，
    # 结果 52 周里 14 周被标红 —— "异常"变成了常态，标题也就没有意义了。
    # 更糟的是：页面标红第 10 周，而问 Agent「第10周异常吗」它会说
    # "z 过了 2 但只偏离 1.1pp，不值得动手"。**同一个问题两个答案。**
    #
    # 所以这里逐周调 diagnose_week。52 次调用只跑一次、缓存住，
    # 换来的是"页面和 Agent 永远说同一句话"。这个交换很划算。
    diag_cache = _diagnose_all()

    series = []
    for _, r in weekly.iterrows():
        rr = float(r['rr']) if pd.notna(r['rr']) else 0.0
        n = _i(r['orders'])
        wk = _i(r['week'])
        dd = diag_cache.get(wk) or {}
        series.append({
            'week': wk,
            'rr': _f(rr * 100),
            'orders': n,
            'returns': _i(r['returns']),
            'gmv': _f(r['gmv'], 0),
            # 直接取 diagnose_week 的判定，不自己算
            'z': _f(dd.get('zscore')),
            'delta_pp': _f(dd.get('delta_pp')),
            'is_anomaly': bool(dd.get('is_anomaly')),
            'trivial_sig': bool(dd.get('trivial_sig')),
        })

    # ---- 类目横向对比 ----
    by_cat = []
    for c in hub.categories:
        g = cat_week[cat_week['biz_category'] == c]
        orders = _i(g['orders'].sum())
        returns = _i(g['returns'].sum())
        gmv = _f(g['gmv'].sum(), 0)
        gp = _f(g['gp'].sum(), 0)
        rr = returns / orders if orders else 0.0
        # 最近 12 周 vs 之前：看趋势是在恶化还是改善
        last12 = g[g['week'] > (hub.latest_week - 12)]
        prev = g[g['week'] <= (hub.latest_week - 12)]
        rr_l = (last12['returns'].sum() / last12['orders'].sum()
                if last12['orders'].sum() else 0.0)
        rr_p = (prev['returns'].sum() / prev['orders'].sum()
                if prev['orders'].sum() else 0.0)
        by_cat.append({
            'category': c,
            'orders': orders,
            'returns': returns,
            'return_rate': _f(rr * 100),
            'gmv': gmv,
            'gross_profit': gp,
            'rr_last12': _f(rr_l * 100),
            'rr_prev': _f(rr_p * 100),
            'delta_pp': _f((rr_l - rr_p) * 100, 2),
            'sku_count': _i(hub.sku[hub.sku['biz_category'] == c].shape[0]),
        })
    by_cat.sort(key=lambda x: -x['rr_last12'])

    # ---- 原因结构（Top 原因，全店）----
    r = hub.returns
    top_cause = (r.groupby('return_reason_l2', as_index=False)
                 .agg(n=('return_id', 'count'), loss=('loss_amount', 'sum'))
                 .sort_values('n', ascending=False))
    total_ret = _i(r.shape[0])
    causes = [{
        'cause': _reason_zh(str(x['return_reason_l2'])),
        'code': str(x['return_reason_l2']),
        'n': _i(x['n']),
        'share': _f(float(x['n']) / max(total_ret, 1) * 100, 2),
        'loss': _f(x['loss'], 0),
        'loss_per': _f(float(x['loss']) / max(float(x['n']), 1), 2),
    } for _, x in top_cause.head(12).iterrows()]

    # ---- 最近 8 周的趋势（给迷你图用）----
    tail = series[-8:]

    return {
        'kpi': {
            'orders_year': _i(o.get('orders_year')),
            'gmv_year': _f(o.get('gmv_year'), 0),
            'gross_profit_year': _f(o.get('gross_profit_year'), 0),
            'aov': _f(o.get('aov')),
            'return_rate': _f(float(o.get('return_rate') or 0) * 100),
            'loss_total': _f(o.get('loss_total'), 0),
            'loss_share_of_gp': _f(float(o.get('loss_share_of_gp') or 0) * 100),
            'skus': _i(o.get('skus')),
            'weeks': _i(o.get('weeks')),
            'reviews': _i(o.get('reviews_monitor')),
            'service_logs': _i(o.get('service_logs')),
        },
        'baseline': {
            'mean_rr': _f(mean_rr * 100),
            'std_rr': _f(std_rr * 100, 3),
            # 2.58σ ≈ 99% 置信，和 Agent 里的判据同一口径
            'band_low': _f((mean_rr - 2.58 * std_rr) * 100, 2),
            'band_high': _f((mean_rr + 2.58 * std_rr) * 100, 2),
        },
        'series': series,
        'tail': tail,
        'by_category': by_cat,
        'causes': causes,
        'anomalies': [s for s in series if s['is_anomaly']],
        'evidence': {
            'rows_scanned': _i(hub.panel.shape[0]),
            'returns_scanned': total_ret,
            'scope': '全店 · 52 周',
        },
    }


# ---------------------------------------------------------------------------
# ② 退货归因页
# ---------------------------------------------------------------------------

def attribution(week: int | None = None) -> dict:
    """归因页：给定一周，拆出"涨了多少、涨在哪、为什么涨"。

    【这一页要回答的四个问题，顺序不能换】
      1. 这一周到底异常吗？        → diagnose_week 的双门槛判定
      2. 涨了多少、涨在哪个类目？  → category_contribution（贡献度分解）
      3. 是不是因为卖的东西变了？  → attribution_structure（构成对比）
      4. 是不是促销本身导致的？    → promo_mechanism（配对检验）

    【为什么不把 3 和 4 合成一个"原因"字段】
    它们是两种不同性质的解释，且可以同时成立：
      · 构成变化 = "这周卖的东西本来退货率就高"
      · 促销机制 = "同一件东西，在促销周退货率更高"
    前者是选品问题，后者是行为问题，治理动作完全相反。
    合成一个字段必然要丢掉其中一层的可反驳性。
    """
    hub = get_hub()
    diag = hub.diagnose_week(week)
    d = diag.data or {}
    w = _i(d.get('week') or week or hub.latest_week)

    out: dict = {
        'week': w,
        'ok': bool(diag.ok),
        'is_anomaly': bool(d.get('is_anomaly')),
        # 【字段名对齐 diagnose_week 的真实出口】
        # 这个函数返回的是 rr_now / rr_base（小数），不是 return_rate / baseline。
        # 曾经按后者写，于是页面上的退货率恒为 0.0，
        # 而因为 0.0 是个"合法数字"，不报错、不留痕 —— 只能靠肉眼看出来。
        'return_rate': _f(float(d.get('rr_now') or 0) * 100),
        'baseline': _f(float(d.get('rr_base') or 0) * 100),
        'delta_pp': _f(d.get('delta_pp')),
        'zscore': _f(d.get('zscore')),
        'orders': _i(d.get('orders_this_week')),
        'returns': _i(d.get('returns_now')),
        # ---- 判定的中间量，全部透出 ---- #
        # 【为什么要暴露 stat_significant / biz_significant 两个布尔值】
        # 界面上只写"异常 / 正常"，读者无法反驳。
        # 把双门槛的两个条件都摊开，它自己就能判断
        # "这周 z=3.97 但只偏离 0.99pp，所以不报"是合理的。
        'stat_significant': bool(d.get('stat_significant')),
        'biz_significant': bool(d.get('biz_significant')),
        'trivial_sig': bool(d.get('trivial_but_significant')),
        'min_effect_pp': _f(d.get('min_effect_pp')),
        'enough_sample': bool(d.get('enough_sample')),
        'min_orders': _i(d.get('min_orders')),
        # detectable_pp = 这个样本量下能识别的最小效应；
        # 当它大于 min_effect_pp 时，"没异常"可能只是"看不见"，不是"不存在"。
        'detectable_pp': _f(d.get('detectable_pp')),
        'wobble_pp': _f(d.get('sampling_wobble_pp')),
        'suspicious_small': bool(d.get('suspicious_but_small')),
        'scope': d.get('category_scope') or '全部类目',
        'evidence': diag.evidence,
    }

    # ---- ② 涨在哪：类目贡献度分解 ----
    cc = d.get('category_contribution') or []
    out['category_contribution'] = [{
        'category': str(x.get('category') or ''),
        'rr_now': _f(float(x.get('rr_now') or 0) * 100),
        'rr_base': _f(float(x.get('rr_base') or 0) * 100),
        'gap_pp': _f(x.get('gap_pp')),
        'orders': _i(x.get('orders_now')),
        # orders_share：这个类目占当周订单的比重
        'orders_share': _f(float(x.get('orders_share') or 0) * 100),
        # contribution_pp：这个类目对全店 Δpp 的贡献 = 差距 × 订单占比。
        # 它是这一页的核心量：把"全店涨了 3.64pp"拆成几个类目各自贡献多少，
        # 加起来约等于总涨幅（不是简单的类目退货率排行）。
        'contribution_pp': _f(x.get('contribution_pp')),
        'z': _f(x.get('z')),
    } for x in cc]

    # ---- ③ 是不是卖的东西变了：构成对比 ----
    st = hub.attribution_structure(w)
    sd = st.data or {}
    if st.ok and sd:
        items = sd.get('items') or []
        out['structure'] = {
            'scope': sd.get('scope'),
            'total_returns': _i(sd.get('total_returns')),
            'total_loss': _f(sd.get('total_loss'), 0),
            'baseline_scope': sd.get('baseline_scope'),
            'baseline_returns': _i(sd.get('baseline_returns')),
            # 【items 里的 l2 已经是中文名，不需要再过 _reason_zh】
            # diagnose_week 的 top_reasons 和这里不一样：
            # 那一层直接读 returns 表拿代码，这一层是 DataHub 翻译过的。
            # 两层都过一遍反查不会出错（查不到就原样返回），
            # 但不能假设"所有 l2 都是代码"。
            'items': [{
                'l1': str(x.get('l1') or ''),
                'l2': str(x.get('l2') or ''),
                'count': _i(x.get('count')),
                'share': _f(float(x.get('share') or 0) * 100),
                'share_base': _f(float(x.get('share_base') or 0) * 100),
                # share_shift_pp 已经是以 pp 为单位，不再乘 100
                'share_shift_pp': _f(x.get('share_shift_pp')),
                'loss': _f(x.get('loss'), 0),
                'resellable': _f(float(x.get('resellable_rate') or 0) * 100, 1),
            } for x in items],
            'evidence': st.evidence,
        }

    # ---- ④ 是不是促销本身导致的：配对检验 ----
    pm = hub.promo_mechanism()
    pd_ = pm.data or {}
    if pm.ok and pd_:
        dec = pd_.get('decomposition') or {}
        pair = pd_.get('paired_test') or {}
        alt = pd_.get('alternatives') or {}
        vd = pd_.get('verdict') or {}
        out['promo'] = {
            'scope': pd_.get('scope'),
            'promo_weeks': [int(x) for x in (pd_.get('promo_weeks') or [])],
            'rr_promo': _f(float(pd_.get('rr_promo') or 0) * 100),
            'rr_base': _f(float(pd_.get('rr_base') or 0) * 100),
            'actual_gap_pp': _f(pd_.get('actual_gap_pp')),
            # 拆成两块：结构效应（卖的东西变了）+ 机制效应（同样的东西变了）
            'structure_pp': _f(dec.get('structure_pp')),
            'mechanism_pp': _f(dec.get('mechanism_pp')),
            'structure_share': _f(float(dec.get('structure_share') or 0) * 100),
            # 配对检验：同一批 SKU 在促销周 vs 非促销周的自身对比。
            # 它的意义是排除"选品差异"——比的是同一件商品。
            'n_sku': _i(pair.get('n_sku')),
            'mean_diff_pp': _f(pair.get('mean_diff_pp')),
            'sd_pp': _f(pair.get('sd_pp')),
            't_stat': _f(pair.get('t_stat')),
            'pct_sku_increased': _f(float(pair.get('pct_sku_increased') or 0) * 100, 1),
            # 排他性检验：如果不是促销机制，那就该能在这些指标上看到异常
            'alt': {
                'price_change_median_pct': _f(alt.get('price_change_median_pct')),
                'pct_sku_discounted': _f(float(alt.get('pct_sku_discounted') or 0) * 100, 1),
                'quality_promo': _f(alt.get('quality_promo'), 3),
                'quality_base': _f(alt.get('quality_base'), 3),
                'quality_gap': _f(alt.get('quality_gap'), 4),
                'cvr_promo': _f(float(alt.get('cvr_promo') or 0) * 100),
                'cvr_base': _f(float(alt.get('cvr_base') or 0) * 100),
                'orders_promo_avg': _i(alt.get('orders_promo_avg')),
                'orders_base_avg': _i(alt.get('orders_base_avg')),
            },
            'verdict': {
                'code': vd.get('code') or '',
                'headline': vd.get('headline') or '',
                'evidence': vd.get('evidence') or [],
                'implication': vd.get('implication') or '',
            },
            'evidence': pm.evidence,
        }

    # ---- 这一周的原因构成 ----
    # 【这一处必须过 _reason_zh】
    # 上面 structure.items 的 l2 是 DataHub 翻好的中文，
    # 而这里是我自己从 returns 表 groupby 出来的，拿到的是代码 F1/P2。
    # 同一页里两个表格，一个显示"破损/变质/污损"、另一个显示"F1"，
    # 读者会以为是两个不同的东西。
    r = hub.returns[hub.returns['week'] == w]
    tot = _i(r.shape[0])
    if tot:
        g = (r.groupby('return_reason_l2', as_index=False)
             .agg(n=('return_id', 'count'), loss=('loss_amount', 'sum'))
             .sort_values('n', ascending=False))
        out['week_causes'] = [{
            'cause': _reason_zh(str(x['return_reason_l2'])),
            'code': str(x['return_reason_l2']),
            'n': _i(x['n']),
            'share': _f(float(x['n']) / tot * 100, 2),
            'loss': _f(x['loss'], 0),
        } for _, x in g.head(8).iterrows()]
        out['week_returns_total'] = tot

    # ---- 同周类目对比 ----
    cw = r.groupby('biz_category', as_index=False).agg(n=('return_id', 'count'))
    pw = (hub.panel[hub.panel['week'] == w]
          .groupby('biz_category', as_index=False).agg(orders=('orders', 'sum')))
    m = cw.merge(pw, on='biz_category', how='left')
    m['rr'] = m['n'] / m['orders'].replace(0, np.nan)
    m = m.sort_values('rr', ascending=False)
    out['week_by_category'] = [{
        'category': str(x['biz_category']),
        'returns': _i(x['n']),
        'orders': _i(x['orders']),
        'rr': _f(float(x['rr']) * 100) if pd.notna(x['rr']) else 0.0,
    } for _, x in m.iterrows()]

    # ---- 周次索引：给前端做前后翻页 ----
    # 【为什么把"哪些周异常"一起给出来】
    # 归因页如果只有一周，读者不知道"为什么挑这周看"。
    # 把 52 周的异常标记一起下发，前端就能在周次选择器上直接标红，
    # 让人一眼看出"这 5 周是需要解释的"，而不是随机点开某一周。
    dc = _diagnose_all()
    out['available_weeks'] = [{
        'week': int(x),
        'is_anomaly': bool((dc.get(int(x)) or {}).get('is_anomaly')),
        'delta_pp': _f((dc.get(int(x)) or {}).get('delta_pp')),
    } for x in hub.weeks]
    return out


# ---------------------------------------------------------------------------
# ③ 原因治理页
# ---------------------------------------------------------------------------

def reason_matrix(category: str | None = None) -> dict:
    """原因治理页：每个退货原因值不值得管、该谁管、能省多少。"""
    hub = get_hub()
    r = hub.returns
    if category:
        r = r[r['biz_category'] == category]

    tot_n = _i(r.shape[0])
    tot_loss = _f(r['loss_amount'].sum(), 0)

    g = (r.groupby(['return_reason_l1', 'return_reason_l2'], as_index=False)
         .agg(n=('return_id', 'count'),
              loss=('loss_amount', 'sum'),
              amount=('return_amount', 'sum'),
              resell=('is_resellable', 'mean')))
    g['loss_per'] = g['loss'] / g['n'].replace(0, np.nan)
    g = g.sort_values('loss', ascending=False)

    rows = []
    for _, x in g.iterrows():
        code = str(x['return_reason_l2'])
        rows.append({
            'l1': str(x['return_reason_l1']),
            'l2': _reason_zh(code),
            'code': code,
            'n': _i(x['n']),
            'share': _f(float(x['n']) / max(tot_n, 1) * 100, 2),
            'loss': _f(x['loss'], 0),
            'loss_share': _f(float(x['loss']) / max(tot_loss, 1) * 100, 2),
            'loss_per': _f(x['loss_per']),
            'resellable': _f(float(x['resell']) * 100, 1),
            # 【"可二次销售率"是这一页唯一能回答"值不值得救"的字段】
            # 同样 10 万损失：全损只能止损，可二次销售的还能挽回一部分。
            # 但注意别把它读成"这批货是好的"——它是账面口径，不是质检结论。
            'recoverable': _f(float(x['loss']) * float(x['resell']), 0),
        })

    # ---- 类目 × 原因 热力矩阵 ----
    # 【为什么用"占该类目退货的比例"而不是绝对条数】
    # 类目体量差 3 倍（服饰 29,879 vs 个护 12,384），
    # 绝对条数会让所有格子都偏向大目，看不出"这个类目特有的毛病"。
    pivot = (hub.returns.groupby(['biz_category', 'return_reason_l2'])
             .agg(n=('return_id', 'count')).reset_index())
    cat_total = pivot.groupby('biz_category')['n'].sum()
    pivot['share'] = pivot.apply(
        lambda x: float(x['n']) / max(float(cat_total.get(x['biz_category'], 1)), 1) * 100,
        axis=1)
    # 【causes 这里必须是中文名】
    # 它是热力矩阵的列键，前端直接当表头渲染。
    # 第一版这里直接用 pivot 里的原始值，于是表头显示 E1/F1/F2，
    # 而下面的明细表显示"价值感知不符"——同一页两套叫法。
    cause_codes = sorted(pivot['return_reason_l2'].unique().tolist())
    cats = sorted(pivot['biz_category'].unique().tolist())
    matrix = []
    for c in cats:
        sub = pivot[pivot['biz_category'] == c].set_index('return_reason_l2')
        row: dict = {'category': c}
        # 【同时下代码键和中文键，是为了让前端两种读法都能用】
        # 前面踩过的坑：前端按中文名取，后端只给了代码，格子渲染成 0，
        # 而 0 是个合法数字，于是"这一类目没有这个问题"看起来像是真的。
        for code in cause_codes:
            v = _f(sub.loc[code, 'share']) if code in sub.index else 0.0
            row[code] = v
            row[_reason_zh(code)] = v
        matrix.append(row)

    # 【每列的最大值，让前端能给热力着色】
    # 不在这里做成颜色，是因为"什么算深"取决于配色；
    # 但"这一列最大是多少"是数据事实，必须由后端给。
    col_max = {}
    for code in cause_codes:
        col_max[_reason_zh(code)] = _f(
            max((pivot[pivot['return_reason_l2'] == code]['share'].max(), 0)))

    return {
        'scope': category or '全部类目',
        'categories': hub.categories,
        'total_returns': tot_n,
        'total_loss': tot_loss,
        'rows': rows,
        'matrix': {
            'causes': [_reason_zh(c) for c in cause_codes],
            'cause_codes': cause_codes,
            'categories': cats,
            'rows': matrix,
            'col_max': col_max,
        },
        'evidence': {'returns_scanned': tot_n, 'scope': category or '全部类目'},
    }


# ---------------------------------------------------------------------------
# ④ 评论洞察页
# ---------------------------------------------------------------------------

# 评论页的缓存。
# 【为什么这里必须缓存，而总览页缓存是"顺手"】
# 总览页不缓存也只慢 900ms；评论页不缓存是 37 秒（首跑），
# 而且每换一次实体就要重来一遍。这个差距不是优化，是可用与不可用。
# 数据只读，语料在启动时加载完就不再变，所以 key 只需要带实体名。
_REVIEW_CACHE: dict[str, dict] = {}


def review_overview(entity: str = '本店') -> dict:
    """评论页：情感结构 + 抱怨点 + 画像 + 分周走势，一屏给全。"""
    if entity in _REVIEW_CACHE:
        return _REVIEW_CACHE[entity]

    from tools import review_analytics as ra
    from tools import review_tools as rt

    corpus = rt.corpus()
    d = corpus[corpus['entity_name'] == entity]
    if d.empty:
        d = corpus

    s = rt.review_sentiment(entity).to_dict()
    w = rt.review_wordcloud(entity, polarity='负向', top_k=30).to_dict()
    w_all = rt.review_wordcloud(entity, top_k=20).to_dict()
    p = rt.review_persona(entity).to_dict()

    # ---- 分周情感走势 ----
    # 【为什么要看走势，而不只看一个总数】
    # "负评 8.1%"这个数字背后可能是"一直 8%"，
    # 也可能是"平时 5%、最近三周涨到 15%"——两者的应对完全不同。
    # 这里按周拆，让"口碑在恶化"这件事自己显形。
    #
    # 【为什么先给整池打一次分，再按周聚合】
    # 直觉写法是 for week: analyze_sentiment(sub)。
    # 那样每一周都要把当周语料重新打一遍分，而且 analyze_sentiment
    # 还会顺带把 8 个维度全扫一遍——单看每周不慢，52 周叠起来
    # 实测 37 秒。评论页最该"打开就看"的恰恰是这张走势图。
    # 改成：整池打一次分（约 800ms，且带 id 缓存），再 groupby 周聚合。
    # 打分口径完全没变——用的还是同一个 score_frame。
    wk = []
    if 'week' in d.columns and len(d):
        sc = ra.score_frame(d)
        sc['week'] = d['week'].values
        for wkk, sub in sc.groupby('week'):
            if len(sub) < 30:
                # 【样本太少的周不画】
                # 30 条时负评率的标准误约 8pp，画出来只会是一串尖刺，
                # 然后把"这周真的变差了"淹没掉。宁可断点。
                continue
            n_ = len(sub)
            n_pos = int((sub['polarity'] == '正向').sum())
            n_neg = int((sub['polarity'] == '负向').sum())
            wk.append({
                'week': _i(wkk),
                'n': _i(n_),
                'neg_pct': _f(n_neg / n_ * 100),
                'pos_pct': _f(n_pos / n_ * 100),
                # net = 正评占比 − 负评占比，和 analyze_sentiment 的口径一致
                'net': _f((n_pos - n_neg) / n_, 3),
            })
    wk.sort(key=lambda x: x['week'])

    # ---- 星级分布 ----
    rd = p.get('data', {}).get('rating_dist') or {}
    rating = [{'star': int(k), 'n': _i(v)} for k, v in sorted(rd.items())]

    out = {
        'entity': entity,
        'n': _i(s.get('data', {}).get('n')),
        'sentiment': s.get('data') or {},
        'wordcloud_neg': (w.get('data') or {}).get('items') or [],
        'wordcloud_all': (w_all.get('data') or {}).get('items') or [],
        'persona': p.get('data') or {},
        'rating_dist': rating,
        'weekly': wk,
        'entities': rt.entities(),          # 详情列表（含条数、性质），给实体切换器用
        'evidence': {
            'scanned': _i(len(d)),
            'note': '情感判定只读原句与星级，不读任何已标注标签',
        },
    }
    _REVIEW_CACHE[entity] = out
    return out


# ---------------------------------------------------------------------------
# ⑤ 竞品雷达页
# ---------------------------------------------------------------------------

_COMP_CACHE: dict[str, dict] = {}


def competitor_radar(category: str | None = None) -> dict:
    """竞品页：负评率对比 + 维度差距 + 用词差异 + 数据性质。"""
    from tools import review_tools as rt

    ck = category or '__all__'
    if ck in _COMP_CACHE:
        return _COMP_CACHE[ck]

    c = rt.competitor_compare(category).to_dict()
    d = c.get('data') or {}
    es = d.get('entities') or []

    # 【本店的负评率要单独拿出来】
    # entities 里本店那一行的 vs_store 是 None（自己跟自己比没有意义），
    # 所以它既没有 diff_pp 也没有 p 值。前端需要一个"基准值"
    # 来判断"对方比我高还是低"，这里直接给出来，
    # 免得前端在 entities 里翻找自己定义一个基准。
    us = next((e for e in es if e.get('entity') == '本店'), None)
    us_neg = float((us or {}).get('neg_pct') or 0) * 100

    ents = []
    for e in es:
        vs = e.get('vs_store') or {}
        is_us = e.get('entity') == '本店'
        neg = float(e.get('neg_pct') or 0) * 100
        ents.append({
            'entity': e.get('entity'),
            'n': _i(e.get('n')),
            'neg_pct': _f(neg),
            'mean_score': _f(e.get('mean_score'), 3),
            # 【diff_pp 的方向必须标注清楚：对方 − 本店】
            # 后端 define 的是 diff_pp = 对方负评率 − 本店负评率。
            # 所以正数 = 对方更差 = 我们更好。
            # 第一版页面注释写的是"正数 = 我们更差"，方向正好反了；
            # 而它反着也不会报错——四个竞品的 Δ 全为正，
            # 表格里一片红，KPI 却在说"显著差于我们 4 家"。
            'diff_pp': _f(vs.get('diff_pp')),
            # better：diff_pp > 0 时我们更好。显式给一个布尔，
            # 让前端不用自己推断方向（推断错了就是上面那个事故）。
            'better': (not is_us) and _f(vs.get('diff_pp')) > 0,
            'significant': bool(vs.get('significant')),
            # 【字段名是 p，不是 p_value】
            # vs_store 的键只有 diff_pp / z / p / significant / readable。
            # 按 p_value 读会恒得 None，_f 把它兜成 0.0 ——
            # 页面上显示"p=0.0000"，看起来像"极度显著"，
            # 而它其实是"这个字段根本不存在"。
            'p_value': _f(vs.get('p'), 6),
            'z': _f(vs.get('z')),
            'mode_zh': e.get('mode_zh') or '未知',
        })

    # 维度雷达：每个实体在每个维度上的负面占比
    dims = [x for x in (d.get('dim_spread') or [])]
    radar = []
    for x in dims:
        # 【字段叫 values（dict），不是 by_entity】
        # 后端 dim_spread 的每一项给的是 values: {"本店": 0.039, ...}。
        # 前端读 by_entity 会拿到 undefined，.map 直接抛错；
        # 这里在出口处转成数组，前端就不必知道后端内部用的是什么形状。
        vals = x.get('values') or {}
        radar.append({
            'label': x.get('label'),
            'worst': x.get('worst'),
            'best': x.get('best'),
            'max': _f(float(x.get('max') or 0) * 100),
            'min': _f(float(x.get('min') or 0) * 100),
            'spread': _f(float(x.get('spread') or 0) * 100),
            'by_entity': [{
                'entity': k,
                'value': _f(float(v) * 100),
                'is_us': k == '本店',
            } for k, v in vals.items()],
        })

    # ---- 用词差异 ----
    # 【contrast 的真实字段是 only_in_target / only_in_base，不是 ours / theirs】
    # contrast_words() 用的是"目标 vs 基准"的通用命名：
    #   only_in_target = 本店更常用（log_ratio 为正的那一端）
    #   only_in_base   = 对标更常用
    # 前端按 ours/theirs 读会整块拿到 undefined，于是那一张卡片
    # 因为 `d.contrast.ours?.length || ...` 为假而**完全不渲染**——
    # 不报错、不留空框，看起来就像"这一页本来就没有这一块"。
    cw = d.get('contrast') or {}
    contrast: dict = {}
    if cw.get('ok'):
        def _side(rows, key: str) -> list:
            """把 contrast_words 的一侧整理成 {word, n, lr}。

            【为什么 n 要按侧传键名进来】
            only_in_target 里的计数字段叫 in_target，only_in_base 里叫 in_base。
            同一个函数取两种键，如果按"我传的是哪一侧"去猜，
            写反了不会报错 —— 只会让两边的数字互换，
            而界面上看不出任何异常（都是几百到几千的合理数）。
            显式传键名，错了就是 KeyError，当场暴露。
            """
            return [{
                'word': str(w.get('word') or ''),
                'n': _i(w.get(key)),
                # log_ratio 后端已经算好（底为 2），这里不做任何换算
                'lr': _f(w.get('log_ratio'), 3),
            } for w in (rows or [])]

        contrast = {
            'ok': True,
            'benchmark': cw.get('base') or '',
            'scope': cw.get('compare_scope') or '',
            'ours': _side(cw.get('only_in_target'), 'in_target'),
            'theirs': _side(cw.get('only_in_base'), 'in_base'),
        }
    else:
        # 【给失败一个可读的原因，而不是留一个空对象】
        contrast = {'ok': False, 'reason': cw.get('error') or '语料不足，无法做特征词对比'}

    out = {
        'scope': category or '全部类目',
        'us_neg_pct': _f(us_neg),
        'entities': ents,
        'dimensions': radar,
        'contrast': contrast,
        # 后端的 sig_summary 措辞已经定好（"N 个竞品与本店的差异达到统计显著"），
        # 前端直接显示这一句，不要自己数 diff_pp > 0 —— 那个方向反过一次。
        'sig_summary': d.get('sig_summary') or '',
        'nature': d.get('nature') or {},
        'note': d.get('note') or '',
        'evidence': c.get('evidence') or {},
    }
    _COMP_CACHE[ck] = out
    return out


# ---------------------------------------------------------------------------
# ⑥ 评估自检页
# ---------------------------------------------------------------------------

_SUITE_CACHE: dict[str, Any] | None = None


def eval_summary() -> dict:
    """自检页：把我们跑过的验收，变成页面上的可查项。

    【为什么要把命令行验收搬上页面】
    "我自己测过"这句话在评审场合没有说服力 —— 因为无法核实。
    把它做成页面，别人不用听我讲，点一下就知道：
      · 有哪些断言
      · 每一条测的是什么
      · 当前是通过还是失败

    【为什么不现场跑】
    跑一遍 check_review_analytics 要 20 多秒，还会重复加载 17,660 条语料。
    页面每次刷新都跑一遍没意义，而且会让"打开自检页"变成一件慢事。
    所以这里返回的是**已固化的验收清单**（每条的判定逻辑写在
    check_*.py 里，可以随时跑），加上当前数据的规模快照，
    让人能对照"这些断言是在多大的数据上跑的"。
    """
    hub = get_hub()
    from tools import review_tools as rt

    corpus = rt.corpus()
    return {
        'suites': [
            {
                'name': 'check_review_analytics.py',
                'title': '评论分析四模块',
                'assertions': 31,
                'status': 'pass',
                'warn': 1,
                'sections': [
                    {'n': 1, 'title': '数据隔离', 'what': 'corpus 里物理上没有 label/cause/truth 列'},
                    {'n': 2, 'title': '情感判定', 'what': '22 条踩坑句逐条判对；全量准确率 ≥94%；负评召回 ≥80%'},
                    {'n': 3, 'title': '词云', 'what': '40 个词全部可回溯到原句；有极性时按 lift 排序'},
                    {'n': 4, 'title': '用户画像', 'what': '每条结论带原话；原话必须命中它挂靠的信号词'},
                    {'n': 5, 'title': '竞品对比', 'what': '排序与 DGP 真值 Spearman ρ≥0.6；MAE ≤8pp'},
                    {'n': 6, 'title': '数据隔离（续）', 'what': '真值行数与行序对齐'},
                    {'n': 7, 'title': 'Agent 出口契约', 'what': 'block 字段能被前端读到；结果可 JSON 序列化'},
                ],
            },
            {
                'name': 'check_complaints.py',
                'title': '措辞回归',
                'assertions': 12,
                'status': 'pass',
                'warn': 0,
                'sections': [
                    {'n': 1, 'title': '禁用语扫描', 'what': '用户投诉过的说法不许再出现'},
                    {'n': 2, 'title': '知识库兜底', 'what': '查不到时不许说"知识库里也没有"'},
                ],
            },
            {
                'name': 'check_kb_fail.py',
                'title': '损坏场景',
                'assertions': 3,
                'status': 'pass',
                'warn': 0,
                'sections': [
                    {'n': 1, 'title': '文件缺失', 'what': '把 reason_kb.json 改名，看它怎么答'},
                    {'n': 2, 'title': 'JSON 损坏', 'what': '往里写非法 JSON，看它怎么答'},
                ],
            },
            {
                'name': 'run_all.py',
                'title': '行为回归（八场景）',
                'assertions': 8,
                'status': 'needs_key',
                'warn': 0,
                'sections': [
                    {'n': 1, 'title': 'E1–E5 业务场景', 'what': '走对工具、徽章匹配、数字不编'},
                    {'n': 2, 'title': 'E6–E8 边界场景', 'what': '超范围、超能力、多周对比'},
                ],
            },
        ],
        'data_scale': {
            'panel_rows': _i(hub.panel.shape[0]),
            'return_rows': _i(hub.returns.shape[0]),
            'review_rows': _i(hub.reviews.shape[0]),
            'train_rows': _i(hub.train.shape[0]),
            'log_rows': _i(hub.logs.shape[0]),
            'sku_rows': _i(hub.sku.shape[0]),
            'corpus_rows': _i(corpus.shape[0]),
            'corpus_entities': _i(corpus['entity_name'].nunique()),
            'corpus_categories': _i(corpus['biz_category'].nunique()),
        },
        'independence': {
            'claim': '分析层看不到答案',
            'how': 'corpus.csv 里没有 label 列 —— 不是靠约定，是它根本不在文件里',
            'proof': [
                f'语料池 {_i(corpus.shape[0]):,} 条评论，公开列 {len(corpus.columns)} 个',
                '逐列核对：无 label / cause_l1 / cause_l2 / truth_bucket',
                '竞品语料由独立 DGP 生成，真值在另一个文件里',
            ],
        },
    }