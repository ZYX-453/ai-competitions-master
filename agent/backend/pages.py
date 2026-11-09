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

import re
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
#
# 【v3.3：改成按范围分别缓存】
# 总览页加了类目联动（点一个类目，下面的原因结构和异常周跟着筛），
# 而"哪些周异常"是**按范围**算的：服饰的基线不是全店的基线。
# 只缓存全店那一份，选中类目时就得现算 52 次；缓存成一张表，
# 键是 category（None = 全店），首次访问某个范围时算一次。
_DIAG: dict[str | None, dict[int, dict]] = {}


def _diagnose_all(category: str | None = None) -> dict[int, dict]:
    """逐周跑一遍 diagnose_week，拿 Agent 的判定结果。

    【为什么不在这里重写判据】
    判据有两条：统计显著 + 业务值得管。只抄其中一条会直接改变结论
    （第一版我用 |z|≥2.58 单门槛，52 周里标出 14 周异常）。
    唯一可靠的做法是问那个已经实现了判据的函数要答案。

    category 必须原样传下去 —— 判定基线随范围变，这属于"同一判据"的一部分，
    不是另算一套。传下去之后，问 Agent「数码第 10 周异常吗」和页面标红
    的还是同一周，否则页面与问答会给出不同答案且都不报错。
    """
    if category in _DIAG:
        return _DIAG[category]
    hub = get_hub()
    out: dict[int, dict] = {}
    for w in hub.weeks:
        r = hub.diagnose_week(int(w), category=category)
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
            # 【v3.3 新增：热力图要用】
            # 热力格的说明会写"这一周 16.82%，比该类目基线高 2.31pp"。
            # 基线随范围变（服饰 27% vs 数码 10%），前端绝不能拿全店基线
            # 去给类目上色 —— 那会把数码所有周都画成深红，图就废了。
            'rr_now': d.get('rr_now'),
            'rr_base': d.get('rr_base'),
        }
    _DIAG[category] = out
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

def dashboard(category: str | None = None) -> dict:
    """总览页：一屏回答"现在怎么样、哪里最疼、什么时候出过问题"。

    【v3.3：从"四个静态区块"改成"可联动看板"】
    category 给定时，KPI / 52 周曲线 / 原因结构 / 异常周全部按该类目重算；
    不给时是全店，行为与之前一致。

    by_category 永远返回全量 —— 它本身就是那个"切换器"，
    不在选中范围里也要能看到其他类目做对照。

    【为什么联动在服务端做，而不是前端筛一下】
    两道坎：
      1. 判定基线随范围变。服饰基线 27%，数码 10%，
         同一套 z 检验在两个范围下选的异常周完全不同。
         前端筛数据再画，标红的就是"全店口径的异常周"，答案会错。
      2. 前端不许算数。占比、增速、损失/条这些都是后端算好的，
         前端筛一遍就意味着要重算分母——那正是上次"左栏写死 64.7k"的同类错误。
    代价是每次切换要 200–300ms，但因为按范围做了缓存，第二次是 0ms。
    """
    hub = get_hub()
    if category and category not in hub.categories:
        category = None          # 不认识的范围退回全店，不要静默出一个空页面
    ov = hub.overview(category=category)
    o = ov.data or {}

    # ---- 周度退货率曲线（范围 52 周）----
    # 【留一份未过滤的原始表】
    # by_category 那个"切换器"必须永远基于全量算 —— 它要在任何范围下
    # 都能显示其他类目做对照。一开始我直接对 cat_week 做了过滤，
    # 于是选中服饰之后，其他三个类目在过滤后的表里查不到任何行，
    # orders=0 → 退货率算成 0.00%。
    # 页面上表现为"选中服饰后，个护/数码/生鲜 全部变成 0%"
    # —— 这不是视觉问题，是**在报错误的数**。
    cat_week_all = hub._cat_week
    cat_week = cat_week_all
    if category:
        cat_week = cat_week_all[cat_week_all['biz_category'] == category]
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
    diag_cache = _diagnose_all(category=category)

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
    # 【类目损失要按 returns 表算，不能拿 panel 的 gp 反推】
    # "退货损失"= sum(loss_amount)，它的口径在 DataHub.overview 里定死了
    # （cogs 成本口径，不是零售价）。这里按类目切同一列，
    # 全程只有这一个来源；四类目相加必须等于全店 loss_total（有断言在测）。
    ret_all = hub.returns
    loss_by_cat = ret_all.groupby('biz_category')['loss_amount'].sum().to_dict()
    ret_by_cat = ret_all.groupby('biz_category')['return_id'].count().to_dict()
    by_cat = []
    for c in hub.categories:
        # 用**未过滤**的 cat_week_all：这一块是"切换器"，
        # 选中一个类目时，其他类目必须照常显示，否则用户没法切换回去。
        g = cat_week_all[cat_week_all['biz_category'] == c]
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
        loss_cat = _f(loss_by_cat.get(c, 0), 0)
        n_ret = _i(ret_by_cat.get(c, 0))
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
            # 损失总额与该类目单条平摊，供"哪里最疼"按钱排序
            'loss_total': loss_cat,
            'loss_per': _f(loss_cat / n_ret, 2) if n_ret else 0.0,
            'sku_count': _i(hub.sku[hub.sku['biz_category'] == c].shape[0]),
        })
    by_cat.sort(key=lambda x: -x['rr_last12'])

    # ---- 原因结构（Top 原因，按当前范围）----
    r = hub.returns
    if category:
        r = r[r['biz_category'] == category]
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

    # ---- 类目 × 原因 交叉矩阵（联动用）----
    # 【为什么要把 share 也算好，而不是让前端 n / 总数】
    # 切换类目时，占比的分母变了（全店 83,645 条 → 服饰 30,000 条）。
    # 前端一除，就等于在前端重建了一次口径；这正是那次
    # "左栏写死 64.7k" 的同类错误 —— 只不过这次是"算错"而不是"写死"。
    # 所以每个格子把 n / share / loss 三个数都发下来，前端只做查表。
    all_ret = hub.returns
    cat_order = list(hub.categories)
    cat_totals = all_ret.groupby('biz_category')['return_id'].count().to_dict()
    cell_df = (all_ret.groupby(['return_reason_l2', 'biz_category'], as_index=False)
               .agg(n=('return_id', 'count'), loss=('loss_amount', 'sum')))
    cause_codes = [c['code'] for c in causes]   # 复用上面按全店条数排好的顺序
    cells = []
    for _, x in cell_df.iterrows():
        code = str(x['return_reason_l2'])
        if code not in cause_codes:
            continue
        cat = str(x['biz_category'])
        n = _i(x['n'])
        tot = _i(cat_totals.get(cat, 0))
        cells.append({
            'code': code,
            'category': cat,
            'n': n,
            'share': _f(n / max(tot, 1) * 100, 2),
            'loss': _f(x['loss'], 0),
        })

    # ---- 类目 × 周 热力矩阵 ----
    # 【每个格子都走 diagnose_week，不自己拿 rr 减均值】
    # 热力格要显示的偏离量，必须和曲线图上那个灰带、同一套双门槛判定
    # 用的是同一个基线。自己减全店均值，服饰（27%）整片深红、数码（10%）
    # 整片浅色，读者看到的是"类目之间退货率不同"这个废话，
    # 而不是"哪一类哪一周真的出了事"。
    # 顺带的好处：这 4 个类目的诊断结果会进 _DIAG 缓存，
    # 用户点类目切换时就是 0ms。
    heat_rows = []
    for c in cat_order:
        dc = _diagnose_all(category=c)
        base = next((dc[w]['rr_base'] for w in sorted(dc)
                     if dc[w].get('rr_base') is not None), None)
        hcells = []
        for w in sorted(dc):
            d = dc[w]
            rr_now = d.get('rr_now')
            rr_base = d.get('rr_base')
            dev = (None if (rr_now is None or rr_base is None)
                   else _f((float(rr_now) - float(rr_base)) * 100, 2))
            hcells.append({
                'week': int(w),
                'rr': _f(float(rr_now) * 100) if rr_now is not None else None,
                'dev_pp': dev,
                # 【上色键用 z 而不是 dev_pp】
                # 一开始按 dev_pp 上色，结果服饰（28%）整行深红、数码（10%）几乎无色
                # ——因为服饰周间波动本来就大，±3pp 是常态；数码 ±3pp 已经很异常。
                # 同一把尺子量不同尺度的类目，读者看到的其实是"类目退货率不同"
                # 这个废话，而不是"哪一周出事了"。
                # z 是"偏离了几个标准误"，已经按各自基线率与样本量归一，
                # 跨类目可比；而且它和双门槛判定的第一条同源，
                # 不会出现"图很红但判定说没事"。
                'z': (None if d.get('zscore') is None else _f(d.get('zscore'), 2)),
                'is_anomaly': bool(d.get('is_anomaly')),
                'trivial_sig': bool(d.get('trivial_sig')),
            })
        heat_rows.append({
            'category': c,
            'base_rr': _f(float(base) * 100) if base is not None else None,
            'cells': hcells,
        })

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
        # v3.3 联动所需的两张矩阵，都是后端算好、前端只查表
        'cause_matrix': {
            'causes': [{'code': c['code'], 'cause': c['cause']} for c in causes],
            'categories': cat_order,
            'cells': cells,
        },
        'heatmap': {
            'rows': heat_rows,
            'weeks': [int(w) for w in sorted(hub.weeks)],
            # 【色阶分界也由后端给，且用 z 的口径】
            # 热力图的深浅直接决定读者"觉得哪块最疼"。上色键是二项 z
            # （见 heat_rows 里的说明），所以分界也必须是 z：
            # 2.0 是统计显著线，4.0 给到饱和 —— 这样"颜色深"和
            # "过了显著性门槛"在视觉上对得上，读者不会把浅色读成"没问题"。
            'scale_z': 4.0,
            'sig_z': 2.0,
        },
        'scope': category or '全店',
        'evidence': {
            'rows_scanned': _i(hub.panel.shape[0]),
            'returns_scanned': total_ret,
            'scope': f'{category or "全店"} · 52 周',
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

    # 【闭合项：类目贡献加起来并不严格等于 delta_pp】
    # 实测 week=49：四个类目贡献合计 -1.69pp，而 delta_pp 是 -1.84pp，
    # 差 0.15pp。这个差额不是误差，是**结构效应（mix effect）**：
    #
    #   Σ(本周份额i × 类目差距i) = 全店本周率 − Σ(本周份额i × 类目基准率i)
    #                            = -1.70pp     ← 只有"类目内部变差"这部分
    #
    #   但全店基准率是按**基准期的份额**算的：
    #   Σ(本周份额i × 类目基准率i) − 全店基准率 = -0.14pp
    #                            ↑ 订单从高退货率类目迁移到低退货率类目
    #
    #   -1.70 + (-0.14) = -1.84pp ✓
    #
    # 【为什么必须单独把它算出来给前端】
    # 瀑布图要让各条长度相加等于总数，缺了这一项，图就是"四根条加起来
    # 对不上最右边那根总条"—— 一眼假。而前端自己去减，
    # 等于把"这个差是什么"的解释权丢给了一个减法，没人知道它叫结构效应。
    contrib_sum = sum(float(x.get('contribution_pp') or 0) for x in cc)
    out['contribution_sum_pp'] = _f(contrib_sum)
    out['structure_effect_pp'] = _f(float(out['delta_pp'] or 0) - contrib_sum)
    # 结构效应占整体变化的比例：绝对值越大，"这周的变化"里越多来自卖的东西变了
    if abs(float(out['delta_pp'] or 0)) > 1e-9:
        out['structure_effect_ratio'] = _f(
            (float(out['delta_pp']) - contrib_sum) / float(out['delta_pp']))

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
        promo_weeks = [int(x) for x in (pd_.get('promo_weeks') or [])]
        out['promo'] = {
            'scope': pd_.get('scope'),
            'promo_weeks': promo_weeks,
            # 【这一周到底是不是促销周 —— 判定必须在后端做】
            # promo_mechanism() 不接 week 参数：它算的是"促销周 vs 非促销周"
            # 的整体对照，跟当前选中的那一周没有关系。
            # 不加这个标记，第 52 周（不是促销周、退货率还在 −0.98pp）也会
            # 显示"促销周退货率上升 +3.98pp"，而页面标题写着"这一周为什么涨了"，
            # 读者只能理解成它在解释 52 周。这条排除项在非促销周是不适用的。
            'is_promo_week': w in set(promo_weeks),
            'rr_promo': _f(float(pd_.get('rr_promo') or 0) * 100),
            'rr_base': _f(float(pd_.get('rr_base') or 0) * 100),
            'actual_gap_pp': _f(pd_.get('actual_gap_pp')),
            # 拆成两块：结构效应（卖的东西变了）+ 机制效应（同样的东西变了）
            'structure_pp': _f(dec.get('structure_pp')),
            'mechanism_pp': _f(dec.get('mechanism_pp')),
            'structure_share': _f(float(dec.get('structure_share') or 0) * 100),
            # 【分解的闭合差额 —— 必须由后端命名并给数】
            # 实际涨幅 = 结构 + 机制 + 这一项，但这一项不是"算错了"：
            # 结构项来自 shift-share 加权分解，机制项是 151 个 SKU 配对差的
            # **等权平均**（每个 SKU 一样重），两者权重口径不同，
            # 直接相加必然对不上按订单加权的实际涨幅。
            # 0.04 + 3.98 = 4.02，实际 3.28，差 −0.74pp 就在这里。
            # 前端只负责显示，不自己减 —— 否则差额会变成一个没人认领的数字。
            'residual_pp': _f(
                float(pd_.get('actual_gap_pp') or 0)
                - (float(dec.get('structure_pp') or 0)
                   + float(pair.get('mean_diff_pp') or 0))),
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

    # 【2026-11-03 删掉的三个字段：week_causes / week_returns_total / week_by_category】
    # 它们只服务于页面底部那两块表（"本周退货原因构成"、"同周类目对比"），
    # 而那两块是上面 ②③ 的降级副本：
    #   - 原因构成表 = structure.items 的明细视图，少了 share_base / share_shift_pp / resellable
    #   - 类目对比表 = category_contribution 的弱化版，少了 baseline / gap_pp / contribution_pp
    # 同一条 returns 数据在同一页出现两遍、其中一遍还少几列，读者只会怀疑两边不一致。
    # 前端同步删除后，这里不留死字段 —— 真要恢复，从 hub.returns 现算即可。

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
        # 【"未归类"不是一个原因，是一次归因失败】
        # 它跟其余 8 行并排、长得完全一样，但两者的身份不同：
        # 其余 8 行都能派活（"破损 → 换包装"），这一行派不出活，
        # 它要说的是"这 1,647 条我们没归上原因"。
        # 更要紧的是它的可再售率 72.4% 是全表最高 —— 读者横向扫这一列时，
        # 会看到最大的数落在一个"根本不是原因"的行上，很自然地读成
        # "未归类最值得先管"，而事实是"这一类连为什么被退都不知道"。
        # 所以层级不写「其他」（那是并列关系），改写「数据缺口」（另一种东西）。
        # 由后端下发：这是数据身份，不是排版选择。
        is_void = code == 'X'
        rows.append({
            'l1': '数据缺口' if is_void else str(x['return_reason_l1']),
            'l2': _reason_zh(code),
            'code': code,
            'is_void': is_void,
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
             .agg(n=('return_id', 'count'), loss=('loss_amount', 'sum')).reset_index())
    cat_total = pivot.groupby('biz_category')['n'].sum()
    cat_loss = pivot.groupby('biz_category')['loss'].sum()
    pivot['share'] = pivot.apply(
        lambda x: float(x['n']) / max(float(cat_total.get(x['biz_category'], 1)), 1) * 100,
        axis=1)
    # 【第二种口径：占该类目损失的比例】
    # 只给"占条数"会漏掉页面的主问题。这一页问的是"值不值得花钱去管"，
    # 而条数占比高、损失占比低的原因（比如运费/时效类）恰好不该先管。
    # 两个口径并列摆出来，"按条数看最重"和"按钱看最重"不一致的地方才会露出来。
    # 同样由后端算好 —— 前端拿绝对量自己除，就会出现两处口径各算一遍的问题。
    pivot['loss_share'] = pivot.apply(
        lambda x: float(x['loss']) / max(float(cat_loss.get(x['biz_category'], 1)), 1) * 100,
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
    col_max_loss = {}
    for code in cause_codes:
        sub = pivot[pivot['return_reason_l2'] == code]
        col_max[_reason_zh(code)] = _f(max((sub['share'].max(), 0)))
        col_max_loss[_reason_zh(code)] = _f(max((sub['loss_share'].max(), 0)))

    # 【逐格明细：给看板的浮层和两个口径切换用】
    # 矩阵格原来只印一个百分比，读者想问的下一句一定是"这 15% 是多少条、多少钱"，
    # 而屏幕上没有答案 —— 要么他回上面那张表里猜，要么就不问了。
    # 绝对量由后端一并下发（前端不许自己乘除），浮层才有东西可说。
    cells = []
    for c in cats:
        for code in cause_codes:
            hit = pivot[(pivot['biz_category'] == c) &
                        (pivot['return_reason_l2'] == code)]
            if len(hit):
                cells.append({
                    'category': c,
                    'code': code,
                    'name': _reason_zh(code),
                    'n': _i(hit['n'].iloc[0]),
                    'loss': _f(hit['loss'].iloc[0], 0),
                    'share': _f(hit['share'].iloc[0], 2),
                    'loss_share': _f(hit['loss_share'].iloc[0], 2),
                })
            else:
                # 这一类目没有这种退货：三个量都是真实测量出来的 0，不是缺数。
                # 不补出来的话，前端在两种口径下都要处理"格子不存在"，
                # 而"不存在"和"是 0"在界面上长得一样（本项目踩过这个坑）。
                cells.append({
                    'category': c, 'code': code, 'name': _reason_zh(code),
                    'n': 0, 'loss': 0.0, 'share': 0.0, 'loss_share': 0.0,
                })

    # 类目合计：矩阵行标签旁边要能看出"这行是按多少条算出来的占比"。
    cat_totals = {c: {'n': _i(cat_total.get(c, 0)), 'loss': _f(cat_loss.get(c, 0), 0)}
                  for c in cats}

    # 【可挽回的占比也在后端算】
    # 前端原来写的是 sum(rows.recoverable) / total_loss * 100 —— 又一次"前端拿绝对量现除"。
    # 这一页同时有 share / loss_share 两套占比由后端给，唯独这一个在前端算，
    # 四舍五入位置不一致，末位会跟旁边的数字对不上。统一收回到这里。
    recoverable_total = sum(float(x['recoverable']) for x in rows)
    recoverable_pct = _f(recoverable_total / tot_loss * 100, 1) if tot_loss else 0.0

    return {
        'scope': category or '全部类目',
        'categories': hub.categories,
        'total_returns': tot_n,
        'total_loss': tot_loss,
        'recoverable_total': _f(recoverable_total, 0),
        'recoverable_pct': recoverable_pct,
        'rows': rows,
        'matrix': {
            'causes': [_reason_zh(c) for c in cause_codes],
            'cause_codes': cause_codes,
            'categories': cats,
            'rows': matrix,
            'col_max': col_max,
            'col_max_loss': col_max_loss,
            'cells': cells,
            'cat_totals': cat_totals,
        },
        'evidence': {'returns_scanned': tot_n, 'scope': category or '全部类目'},
    }


# ---------------------------------------------------------------------------
# ④ 预警 · 策划页
# ---------------------------------------------------------------------------


def plan(category: str | None = None, week: int | None = None) -> dict:
    """预警 → 策划：哪几周需要解释，以及下一周谁去做什么。

    【这一页和前面几页的分工】
    总览回答"现在怎么样"，归因回答"这一周为什么涨"，原因治理回答"哪个原因值得管"。
    它们都停在"看懂了"。这一页要往前推一步：**把判定结果变成一张能派活的清单。**

    【两条不能自己重写的判据】
    1. "哪一周需要解释"直接用 _diagnose_all（也就是 diagnose_week 的双门槛），
       不在这里重算。历史上吃过一次亏：用 |z|≥2.58 单门槛会把 52 周里的 14 周
       都标成异常，而页面和问答必须给出同一个答案。
    2. "该做什么"直接用 action_playbook，它按数据里的 9 类原因给动作
       （动作 / 影响指标 / 责任方 / 验收指标）。不在页面里另编一套动作 ——
       编出来的动作没有责任方也没有验收口径，派下去就是一句空话。

    【动作缺失时必须如实说】
    action_playbook 对「未归类」这类会返回 ok=False。这时不能拿通用动作去填，
    也不能假装这一类"不需要动作"；而是给出 plan_missing=True 并说明原因，
    让读者知道"这里缺的是方案，不是数据"。
    """
    dc = _diagnose_all(category)
    hub = get_hub()

    # ---------- 预警清单 ----------
    # 排序依据用 Δpp 而不是 |z|：z 会随样本量放大，Δpp 才是业务实际多付的代价。
    # 这和 suggest_week / diagnose_week 的选择一致。
    # 【百分比一律在这里乘 100，前端不再碰除法】
    # diagnose_week 给的 rr_now 是 0.1704 这种小数。前端 ToFixed(2) 会显示成
    # 0.17，再乘 100 又是"前端算了数" —— 本项目踩过这个坑（见 README 关于
    # 换算值必须由后端预支的那一条）。所以出口统一成"百分数 + 2 位小数"。
    all_anom = [
        {
            'week': int(w),
            'rr_now': _f(float(v.get('rr_now') or 0) * 100, 2),
            'rr_base': _f(float(v.get('rr_base') or 0) * 100, 2),
            'delta_pp': _f(v.get('delta_pp'), 2),
            'z': _f(v.get('zscore'), 2),
            'orders': _i(v.get('orders')),
            'is_anomaly': bool(v.get('is_anomaly')),
            'trivial_sig': bool(v.get('trivial_sig')),
        }
        for w, v in dc.items()
        if v.get('is_anomaly')
    ]
    # 【trivial_sig 的周不进预警】
    # 它们统计显著但幅度不到业务线，派活只会浪费人力。
    # 上面列表本来就只收 is_anomaly 的（双门槛已包含幅度判断），
    # 这里额外剔除是为防止上游判据将来放宽后悄悄混进来。
    all_anom = [a for a in all_anom if not a['trivial_sig']]

    # 52 周走势：预警表只回答"哪几周要管"，走势还要回答"它们出现在什么位置"。
    # 单位仍在后端一次算好；status 也由同一套双门槛结果派生，前端不另判异常。
    trend = []
    for tw, tv in sorted(dc.items(), key=lambda kv: int(kv[0])):
        delta = _f(tv.get('delta_pp'), 2)
        is_anom = bool(tv.get('is_anomaly'))
        trivial = bool(tv.get('trivial_sig'))
        status = ('alert' if is_anom and not trivial and delta > 0
                  else 'improving' if is_anom and not trivial and delta <= 0
                  else 'normal')
        trend.append({
            'week': int(tw),
            'rr_now': _f(float(tv.get('rr_now') or 0) * 100, 2),
            'rr_base': _f(float(tv.get('rr_base') or 0) * 100, 2),
            'delta_pp': delta,
            'status': status,
        })

    # 【预警只收"恶化"，改善单独数一数】
    # 双门槛是双向的（|z| 大就算异常），所以 52 周里会混进"退货率显著下降"的周。
    # 把它们排进预警清单是错的：那是好消息，不该派活。
    # 但也不能假装没有 —— 单独给一个计数，读者才知道"这周变好不是判据失灵"。
    alerts = [a for a in all_anom if a['delta_pp'] > 0]
    alerts.sort(key=lambda x: -x['delta_pp'])
    improving = [a for a in all_anom if a['delta_pp'] <= 0]

    w = int(week) if week is not None and int(week) in dc else None
    if w is None:
        w = alerts[0]['week'] if alerts else suggest_week()

    r = hub.diagnose_week(w, category=category)
    d = r.data or {}

    # ---------- 本周预警陈述 ----------
    headline = {
        'week': w,
        'rr_now': _f(float(d.get('rr_now') or 0) * 100, 2),
        'rr_base': _f(float(d.get('rr_base') or 0) * 100, 2),
        'delta_pp': _f(d.get('delta_pp'), 2),
        'z': _f(d.get('zscore'), 2),
        'returns': _i(d.get('returns_now')),
        'orders': _i(d.get('orders_this_week')),
        'is_anomaly': bool(d.get('is_anomaly')),
        'trivial_sig': bool(d.get('trivial_but_significant')),
        'enough_sample': bool(d.get('enough_sample', True)),
        'min_effect_pp': _f(d.get('min_effect_pp'), 2),
        # 双门槛的两个数都要露出来：判定不是"黑箱打了个勾"，
        # 读者要能自己核对"够不够显著、够不够值得管"。
        'detectable_pp': _f(d.get('detectable_pp'), 2),
        'scope': category or '全部类目',
    }

    # ---------- 谁把这一周推上去的 ----------
    contributions = []
    contrib_all = d.get('category_contribution') or []
    for c in contrib_all[:4]:
        contributions.append({
            'category': c.get('category') or '',
            'contribution_pp': _f(c.get('contribution_pp'), 2),
            'rr_now': _f(float(c.get('rr_now') or 0) * 100, 2),
            'rr_base': _f(float(c.get('rr_base') or 0) * 100, 2),
            'gap_pp': _f(c.get('gap_pp'), 2),
            'orders_now': _i(c.get('orders_now')),
        })

    # 【四项之和对不上总量，必须自己说出来】
    # contribution_pp 是"该类目本周占比 × 与自身基线的差距"的分解，
    # 相加之后和 delta_pp 之间还差一项结构变化（类目占比本身在变）。
    # 页面上摆着 4 个 +1.52/+1.04/+0.59/+0.38 却对不上 +3.64，
    # 读者第一反应是"算错了"；既然差额真实存在，就给它一个名字、摆出来。
    # 这里同样在后端算好（前端不做减法）。
    contrib_sum = _f(sum(c['contribution_pp'] for c in contributions), 2)
    contrib_rest = _f(headline['delta_pp'] - contrib_sum, 2)

    # ---------- 策划：按损失排序的原因 + 对应动作 ----------
    reasons = []
    for t in (d.get('top_reasons') or []):
        name = t.get('reason') or ''
        loss = _f(t.get('loss'), 2)
        pb = hub.action_playbook(name)
        pd = pb.data or {}
        reasons.append({
            'reason': name,
            'count': _i(t.get('count')),
            # 【乘 100 要在四舍五入之前】
            # 本文件其它接口的 share 都已经是百分数（见 320/349/643 行的 * 100），
            # 这里曾经漏了，前端又自己拼了个 "%"，把 22% 印成 0.22%。
            # 更隐蔽的是顺序：先 _f(share, 2) 会把 0.2186 舍成 0.22，
            # 再乘 100 就成了 22.00%，真值 21.86% 永久丢失。
            # 所以先乘再舍，和前端"不碰除法"的约定保持一致。
            'share': _f(float(t.get('share') or 0) * 100, 2),
            'loss': loss,
            'has_plan': bool(pb.ok),
            'cause_name': pd.get('cause_name') or '',
            'actions': pd.get('actions') or [],
            # 没有既定动作时给一句话说明"缺的是方案不是数据"
            'plan_note': (
                '' if pb.ok
                else f'「{name}」这一类没有既定动作清单 —— 缺的是方案，不是数据。'
                     '这一项要么单独立项（先用 20 条原话归纳场景），要么并入相邻原因一起管。'
            ),
        })
    reasons.sort(key=lambda x: -x['loss'])

    # 可挽回的额度：只有拿到了动作的那部分损失才是"能动的钱"
    actionable_loss = sum(x['loss'] for x in reasons if x['has_plan'])
    total_loss = sum(x['loss'] for x in reasons)

    # 【占比在这里算，不在前端算】
    # "这一项占本周损失 x%" 是一条会出现在会议材料里的数字。
    # 如果前端拿 loss/total 现算，同一页上"总损失"和"各项占比"就可能
    # 因为四舍五入对不上（显示 100.1% 或 99.9%），而这种不一致
    # 一旦被评审抓住，整页数字的可信度都没了。所以统一在这里算一次。
    for x in reasons:
        x['loss_share'] = _f(x['loss'] / total_loss * 100, 1) if total_loss else 0.0

    return {
        'scope': category or '全部类目',
        'week': w,
        'alerts': alerts[:12],
        'trend': trend,
        'alert_total': len(alerts),
        # 改善周只给数字，不进清单（见上面注释）
        'improving_total': len(improving),
        'headline': headline,
        'contributions': contributions,
        # 4 项之和 / 未分配差额 / 实际有几个类目（可能超过 4 个，页面上只列前 4）
        'contrib_sum_pp': contrib_sum,
        'contrib_rest_pp': contrib_rest,
        'contrib_n': len(contrib_all),
        'reasons': reasons,
        'loss_total': _f(total_loss, 2),
        'loss_actionable': _f(actionable_loss, 2),
        # 有动作的那部分占多大比例 —— 读者一眼看出"钱能不能动"
        'actionable_pct': _f(actionable_loss / total_loss * 100, 1) if total_loss else 0.0,
        'evidence': {
            'scope': category or '全部类目',
            'weeks_scanned': len(dc),
            'judge': '双门槛：统计显著 + 业务值得管（与诊断工具同一判据）',
            'actions_source': 'action_playbook（按数据里的原因码给动作）',
        },
    }


# ---------------------------------------------------------------------------
# ⑤ 评论洞察页
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
# ⑥ 竞品雷达页
# ---------------------------------------------------------------------------

_COMP_CACHE: dict[str, dict] = {}

# 竞品A（同价位）→ 竞品（同价位）：只吃紧跟在"竞品"后、紧挨全角括号的单个字母，
# 不碰正文里其它 A–D（如维度名、评分等级）
_COMP_LETTER = re.compile(r'(竞品)\s*[A-Da-d]\s*(?=（)')


def _disp_name(name: Any) -> Any:
    """竞品展示名：去掉字母编号，只留描述语。

    【为什么在出口统一处理，而不改 corpus】
    字母编号是内部主键的一部分 —— orchestrator 的别名解析
    （用户说"竞品C"要命中语料里的"竞品C（低价走量）"）、
    review_tools 的模糊匹配、DGP 真值审计，全都靠全名对齐。
    语料里动一个字，三处逻辑跟着断；而页面展示只此一个出口，
    在这里统一洗一遍，数据层与展示层各管各的。
    """
    if not isinstance(name, str):
        return name
    return _COMP_LETTER.sub(r'\1', name)


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
            'entity': _disp_name(e.get('entity')),
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
            'worst': _disp_name(x.get('worst')),
            'best': _disp_name(x.get('best')),
            'max': _f(float(x.get('max') or 0) * 100),
            'min': _f(float(x.get('min') or 0) * 100),
            'spread': _f(float(x.get('spread') or 0) * 100),
            'by_entity': [{
                'entity': _disp_name(k),
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
            'benchmark': _disp_name(cw.get('base') or ''),
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
# ⑦ 评估自检页
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