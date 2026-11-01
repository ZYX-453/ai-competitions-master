# -*- coding: utf-8 -*-
"""差评炼金炉 · 数据访问层

设计原则（这一层决定了 Agent 是"真"还是"演"）：

1. 每个方法都是**可真执行**的 pandas 查询，返回的数据带 `evidence` 字段，
   记录这次查询实际扫了多少行、命中了多少行。前端会把 evidence 直接展示，
   评委可以拿任何一个数字回原始 CSV 核对。
2. 不做缓存包装成"AI 算出来的"——AI 只负责选工具和解释结果，
   数字永远来自这里。
3. 所有"异常"都通过对比得出（本周 vs 基线），而不是硬编码阈值，
   阈值本身来自数据分布（如 2 倍标准差）。

一旦某个结论无法从数据里算出来，就返回 insufficient 标记，
让 Agent 老实说"这个我查不到"，而不是编一个像样的数字。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

def _find_data_dir() -> str:
    """向上逐级查找 data/processed，避免写死层数。

    原来写的是 os.path.join(__file__, '..', '..')，但文件在
    agent/backend/tools/ 下，退两级只到 agent/，差一层。改成主动查找后，
    以后无论把 backend 挪到哪都能跑。
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        cand = os.path.join(d, 'data', 'processed')
        if os.path.isdir(cand):
            return cand
        d = os.path.dirname(d)
    # 兜底：允许用环境变量指定
    env = os.environ.get('CANYU_DATA_DIR')
    if env and os.path.isdir(env):
        return env
    raise RuntimeError('找不到 data/processed 目录，请设置环境变量 CANYU_DATA_DIR')


BASE = _find_data_dir()


def _p(name: str) -> str:
    return os.path.abspath(os.path.join(BASE, name))


@dataclass
class ToolResult:
    """工具统一返回结构。

    evidence 是这套系统能被信任的关键：它把"我查了什么"摊开给用户看，
    而不是只给一个结论。评委追问时，任何一行都能对上原表。
    """
    ok: bool
    data: Any = None
    evidence: dict = field(default_factory=dict)
    note: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            'ok': self.ok,
            'data': self.data,
            'evidence': self.evidence,
            'note': self.note,
            'error': self.error,
        }


class DataHub:
    """数据集中心：一次加载，全局复用。"""

    def __init__(self) -> None:
        self.panel = pd.read_csv(_p('panel_weekly.csv'))
        self.returns = pd.read_csv(_p('returns.csv'))
        self.reviews = pd.read_csv(_p('reviews_monitor.csv'))
        self.train = pd.read_csv(_p('reviews_train.csv'))
        self.logs = pd.read_csv(_p('service_logs.csv'))
        self.sku = pd.read_csv(_p('sku_master.csv'))

        # 类目基准：Agent 判断"是否异常"时要跟自己的历史比，而不是拍一个数
        self._cat_week = (self.panel.groupby(['biz_category', 'week'], as_index=False)
                          .agg(orders=('orders', 'sum'),
                               returns=('returns', 'sum'),
                               gmv=('gmv', 'sum'),
                               gp=('gross_profit', 'sum'),
                               dsr=('dsr', 'mean'),
                               exposure=('exposure', 'sum')))
        self._cat_week['rr'] = (self._cat_week['returns']
                                / self._cat_week['orders'].replace(0, np.nan))

        self.categories = sorted(self.panel['biz_category'].unique().tolist())
        self.weeks = sorted(self.panel['week'].unique().tolist())
        self.latest_week = int(max(self.weeks))

    # ---------- 元信息 ----------
    def overview(self) -> ToolResult:
        p, r = self.panel, self.returns
        total_orders = int(p['orders'].sum())
        total_gmv = float(p['gmv'].sum())
        total_gp = float(p['gross_profit'].sum())
        loss = float(r['loss_amount'].sum())
        return ToolResult(
            ok=True,
            data={
                'skus': int(self.sku.shape[0]),
                'categories': self.categories,
                'weeks': len(self.weeks),
                'orders_year': total_orders,
                'gmv_year': round(total_gmv, 2),
                'gross_profit_year': round(total_gp, 2),
                'aov': round(total_gmv / total_orders, 2) if total_orders else 0,
                'return_rate': round(len(r) / total_orders, 4) if total_orders else 0,
                'loss_total': round(loss, 2),
                'loss_share_of_gp': round(loss / total_gp, 4) if total_gp else 0,
                'reviews_monitor': int(self.reviews.shape[0]),
                'service_logs': int(self.logs.shape[0]),
            },
            evidence={
                'scanned': {'panel_weekly': len(p), 'returns': len(r),
                            'sku_master': len(self.sku)},
                'formula': '退货率 = returns 行数 / sum(orders)；损失 = sum(loss_amount)',
            },
        )

    # ---------- 核心：周度异动归因 ----------
    # 【为什么要做二项检验，而不是简单的 z 分数】
    # 第一版用的是 (本周 − 基线) / 基线周度rr的标准差。这个做法有个致命缺陷：
    # 它把"周与周之间的真实波动"和"有限订单量带来的抽样噪声"混在一起。
    # 后果在数据里立刻显现：第 3 周只有 151 单，退货率 9.27%，
    # 朴素 z = −3.72，看起来是极其显著的异动；但按二项分布算，
    # 151 单在 14.58% 的基准下本就有 ±2.87pp 的天然抖动，
    # 真实 z 只有 −1.85 —— 根本不算异常。
    # 反过来，第 44 周有 2,969 单，朴素 z 只有 +2.41，
    # 但二项 z 高达 +5.41，是真正站得住的异常。
    # 结论：样本量小的周会把噪声误判成异常，必须用二项 SE 归一化。
    # 目标检出幅度：能稳定分辨 3 个百分点以上的偏离。
    #
    # 【口径为什么从"固定 300 单"改成"按退货率反推"】
    # 旧版写死 300 单，看似保守，实际是个隐含偏见：
    # 由 n = (2/Δ)² · p(1−p) 反推，300 单在 p=27.75%（服饰）时
    # 只能分辨 4.8pp 的偏离，在 p=7.2%（个护）时能分辨 3.1pp ——
    # 同一个门槛，对高退货率类目其实更宽松，对低退货率类目更严格，
    # 而且系统从没告诉用户"你这个样本量到底能看出多大的问题"。
    # 现在改成按类目实际退货率算出该周的可分辨幅度 DETECT_PP = 3.0，
    # 门槛随之浮动，并且**把这个幅度直接告诉用户**：
    # "61 单这个量级，能分辨的是 15pp 以上的变化，1pp 的波动我看不出来，
    #   但那也不值得你为它开会。"
    # 这比笼统说"样本不足"有用得多 —— 用户要的是"能看什么"，
    # 不是"你为什么不能看"。
    TARGET_DETECT_PP = 3.0   # 目标可分辨幅度（百分点）

    # 【v2.5：样本补齐之后，冒出来一个反方向的问题】
    #
    # 上面这套门槛解决的是"样本太少，看不出"。把店铺流量放大 6 倍、
    # 让所有类目所有周都过门槛之后，"看不出"没了，却冒出了它的孪生兄弟：
    # **样本一多，什么都能看出显著。**
    #
    # 实测：年订单从 10 万涨到 61.8 万后，全店周均 1.19 万单，
    # 3pp 门槛只要 521 单，余量 20 倍。此时 z ≥ 2 只需要 0.63pp 的偏离。
    # 结果 52 周里有 22 周判"显著异常"，其中 8 周的偏离不到 1pp
    # ——统计上完全站得住（0.6pp 在 1.2 万单下确实不是巧合），
    # 但**运营上没有任何意义**。一个 13.5% 的退货率上下浮 0.6pp，
    # 不值得开一次复盘会，更不该派整改动作。
    #
    # 两种错法其实是一回事的两端：
    #   · 样本少 → 假阴性（真实异动淹没在噪声里）→ 用户觉得系统什么都不说
    #   · 样本多 → 假阳性（噪声被验成显著）  → 用户觉得系统天天报警
    # 单靠 p 值解决不了第二端，因为 p 值只问"是不是巧合"，
    # 从不问"这事儿大不大"。
    #
    # 所以判定改成**双条件**：既要统计显著（z ≥ 2），
    # 又要大到值得管（|偏离| ≥ MIN_EFFECT_PP）。
    # 后者是业务口径，不是统计口径，写在这里就是要让它显式、
    # 可商量、可改 —— 而不是藏在某个人对"显著"的默认理解里。
    MIN_EFFECT_PP = 1.5

    def _min_orders_for(self, p_base: float) -> int:
        """在目标检出幅度下，该基线退货率需要多少单才能达到 z=2。"""
        d = self.TARGET_DETECT_PP / 100.0
        n = (2.0 / d) ** 2 * p_base * (1.0 - p_base)
        return int(np.ceil(max(n, 30)))   # 下限 30 单，避免极低退货率算出不合理的门槛

    def _detectable_pp(self, n: int, p_base: float) -> float:
        """当前样本量 n 实际能分辨的偏离幅度（百分点）。"""
        if n <= 0 or not (0 < p_base < 1):
            return float('nan')
        return float(2.0 * np.sqrt(p_base * (1.0 - p_base) / n) * 100)

    def _binom_z(self, k: int, n: int, p0: float) -> float | None:
        """本周退货数 k / 订单数 n 相对基准率 p0 的二项 z 值。"""
        if n <= 0 or not (0 < p0 < 1):
            return None
        se = float(np.sqrt(p0 * (1 - p0) / n))
        if se <= 0:
            return None
        return (k / n - p0) / se

    def _why_binom_text(self, n: int, k: int, p0: float,
                        naive_sd: float, naive_z: float | None,
                        z: float | None, n_star: float,
                        scope_week_orders: "pd.Series | None" = None) -> str:
        """按本周真实的 n 与分界点 n*，说明朴素 z 错在哪一边。

        【为什么这段文案必须动态生成，不能写死一句】
        旧版这里硬编码了"朴素 z 会把小样本周的抽样噪声误判为异动
        （如第 3 周 151 单，朴素 z −3.7，二项 z 仅 −1.9）"。
        问题是：这段文字是**界面上推理链里用户点开就能看到的证据**。
        数据一重新生成，第 3 周变成 1,713 单、二项 z −1.22，
        那句话里的每个数字都过期了，而且方向也讲反了 ——
        它会让用户以为系统"防止了假警报"，实际这份数据上朴素 z 是漏报。
        文档写错可以改，界面上的证据写错等于当场骗人。
        所以改成按本周实算的 n 与 n* 分情况陈述。
        """
        if not (naive_sd and naive_sd > 0) or n_star != n_star:
            return ('二项 z 用 √(p(1−p)/n) 归一化，样本量已进入分母；'
                    '朴素 z 用周间标准差做分母，分母不随样本量变化。')
        head = (f'朴素 z 用固定的周间标准差 {naive_sd*100:.2f}pp 做分母，'
                f'二项 z 用 √(p(1−p)/n)。两者相等时 n* = '
                f'p(1−p)/sd² ≈ {n_star:,.0f} 单。')

        # 【本范围内逐周样本量的分布，先算好】
        # 这段解释的用途是"告诉用户当前范围里朴素 z 会往哪边错"，
        # 所以必须用**当前范围**的周订单分布，不能用全店的。
        # 第一版就栽在这：问数码类目时说的是"最低一周也有 1,197 单"，
        # 那是全店的数，数码自己最低只有 215 单 —— 张冠李戴。
        if scope_week_orders is not None and len(scope_week_orders) > 0:
            _wk = scope_week_orders
        else:
            _wk = self.panel.groupby('week')['orders'].sum()
        _lo = int(_wk.min())
        _nlow = int((_wk < n_star).sum())
        _tail = (f' 本范围内最低的一周也有 {_lo:,} 单，全部在 n* 之上，'
                 f'所以朴素 z 只会漏报、不会虚报。'
                 if _lo >= n_star else
                 f' 本范围内最低的一周只有 {_lo:,} 单'
                 f'（{_nlow}/{len(_wk)} 周低于 n*），'
                 f'低于 n* 的周上朴素 z 会虚报，高于 n* 的周上会漏报。')

        se = np.sqrt(p0 * (1 - p0) / max(n, 1)) * 100
        if n < n_star:
            # 小样本：真实 SE > sd，朴素 z 分母偏小 → 放大偏离 → 假警报
            return (head + f' 本周 {n:,} 单 **低于** n*，真实抽样误差 '
                    f'±{se:.2f}pp 已经大于周间标准差，'
                    f'朴素 z 会把抽样噪声放大成异动'
                    f'（朴素 {naive_z:+.2f} vs 二项 {z:+.2f}）。' + _tail)

        # 【本周只是勉强越过 n* 时，别说"会漏掉真实异动"这种大话】
        # 服饰第 52 周 244 单、n* 240 单，两者只差 1.7%，
        # 这时朴素 z（−1.11）与二项 z（−1.12）几乎一样，
        # 却还写着"朴素 z 反而会压小偏离"，读者一对照数字会觉得自相矛盾。
        # 两个 z 的相对差小于 5% 就老实说两者接近。
        _gap_ratio = abs((naive_z - z) / z) if (z and abs(z) > 1e-9) else 0.0
        if _gap_ratio < 0.05:
            return (head + f' 本周 {n:,} 单刚好落在 n* 附近，'
                    f'真实抽样误差 ±{se:.2f}pp 与周间标准差相当，'
                    f'两种算法给出几乎一样的结论'
                    f'（朴素 {naive_z:+.2f} vs 二项 {z:+.2f}）。' + _tail)

        # 大样本：真实 SE < sd，朴素 z 分母偏大 → 压低偏离 → 漏报
        return (head + f' 本周 {n:,} 单 **高于** n*，真实抽样误差只有 '
                f'±{se:.2f}pp，小于周间标准差，'
                f'朴素 z 反而会压小偏离、漏掉真实异动'
                f'（朴素 {naive_z:+.2f} vs 二项 {z:+.2f}）。' + _tail)

    def diagnose_week(self, week: int | None = None,
                      category: str | None = None) -> ToolResult:
        """诊断某一周退货率/DSR 相对基线的异动，并拆出贡献最大的类目与原因。

        这是整个 Agent 的主力工具。它不返回一个"退货率涨了"的空结论，
        而是回答三个问题：涨了多少、谁贡献的、什么原因贡献的。
        """
        week = int(week or self.latest_week)
        cw = self._cat_week
        if category:
            cw = cw[cw['biz_category'] == category]
        if cw.empty:
            return ToolResult(ok=False, error=f'没有 {category} 这个类目')

        # 基线：剔除本周后的其余周合计（避免用"含本周的均值"抬高基线）
        base_rows = cw[cw['week'] != week]
        cur_rows = cw[cw['week'] == week]
        if cur_rows.empty:
            return ToolResult(ok=False, error=f'第 {week} 周无数据')

        cur_ret = int(cur_rows['returns'].sum())
        cur_ord = int(cur_rows['orders'].sum())
        base_ret = int(base_rows['returns'].sum())
        base_ord = int(base_rows['orders'].sum())
        cur_rr = cur_ret / max(cur_ord, 1)
        base_rr = base_ret / max(base_ord, 1)

        # 两个 z 都算出来，前端可以对比展示"为什么朴素 z 不可靠"
        naive_sd = float(base_rows.groupby('week')['rr'].mean().std(ddof=1))
        naive_z = (cur_rr - base_rr) / naive_sd if naive_sd > 0 else None
        # 【朴素 z 的偏误方向不是固定的，这里要算清楚是哪一边】
        #
        # 这一点我原来写错了。旧文档把朴素 z 的毛病概括成"会把小样本周的
        # 采样噪声误判成异动"，理由是第 3 周只有 151 单、朴素 z −3.72 而
        # 二项 z 只有 −1.85。那个例子在旧数据上成立，但我当时没意识到
        # 它成立的条件是「该周样本量 < 分界点」，而不是无条件成立。
        #
        # 朴素 z 的分母是周间标准差 sd（固定值），二项 z 的分母是
        # √(p(1−p)/n)（随 n 收缩）。令两者相等：
        #     n* = p(1−p) / sd²
        # 于是：
        #     n < n*  → 真实抽样 SE > sd → 朴素 z 分母偏小 → **高估**（假警报）
        #     n > n*  → 真实抽样 SE < sd → 朴素 z 分母偏大 → **低估**（漏报）
        #
        # 当前数据（全店 13.18% 基线、周间 sd 1.30pp）算下来 n* ≈ 678 单，
        # 而 v2.4 最低周订单是 1,197 单 —— **每一周都在分界点之上**，
        # 所以在这份数据上朴素 z 只会漏报，不会虚报：
        # 它挑出 4 周（44/48/24/5），二项 z 挑出 10 周，前者是后者的子集。
        # 例如第 47 周偏离 −2.40pp，朴素 z −1.89 判"没事"，
        # 二项 z −2.71 才看出它其实显著。
        #
        # 所以 evidence 里不能再说"朴素 z 会把噪声误判成异动"——
        # 那是在说反话。要按本周的实际 n 和 n* 判断该说哪一边。
        n_star = (base_rr * (1 - base_rr) / (naive_sd ** 2)
                  if naive_sd and naive_sd > 0 else float('nan'))
        z = self._binom_z(cur_ret, cur_ord, base_rr)
        # 门槛按该基线退货率反推，不再是固定 300 单
        min_orders = self._min_orders_for(base_rr)
        enough = cur_ord >= min_orders
        delta_pp_now = (cur_rr - base_rr) * 100
        # 【v2.5 双条件判定】
        # 既要统计显著（z ≥ 2），又要大到值得管（|偏离| ≥ 1.5pp）。
        # 只卡 z 会在样本量大的时候把 0.6pp 的日常抖动全报成异常；
        # 只卡幅度又会在小样本时把噪声当结论。两个都得满足。
        #
        # 还有一个组合需要单独说：**统计显著但业务上不值一提**。
        # 这种情况不能简单归到"无异常"里，因为用户如果自己去算
        # 会发现 z 确实过了 2，会觉得系统在瞒事。所以单独标出来，
        # 明说"确实显著，但幅度太小，不值得动手"。
        stat_sig = bool(enough and z is not None and abs(z) >= 2.0)
        biz_sig = abs(delta_pp_now) >= self.MIN_EFFECT_PP
        is_anomaly = bool(stat_sig and biz_sig)
        trivial_sig = bool(stat_sig and not biz_sig)
        # 本周样本量实际能分辨多大偏离 —— 这是给用户的"能看什么"，
        # 比"样本不足"有用：他知道 1pp 的波动看不见，也就不会追着问。
        detect_pp = self._detectable_pp(cur_ord, base_rr)
        # 即使样本不够判定，只要偏离本身超过了该样本量的可分辨幅度，
        # 仍然值得提示"疑似异动，但样本不足以下结论"。
        # 这样用户不会因为一句"样本不足"就错过一个真实信号。
        suspicious = bool((not enough) and detect_pp == detect_pp
                          and abs(delta_pp_now) > detect_pp)

        # 类目贡献：拉高整体退货率的百分点数
        contrib = []
        for _, c in cur_rows.iterrows():
            b = base_rows[base_rows['biz_category'] == c['biz_category']]
            if b.empty:
                continue
            b_rr = float(b['returns'].sum() / max(b['orders'].sum(), 1))
            gap = float(c['rr']) - b_rr
            w = float(c['orders']) / max(cur_rows['orders'].sum(), 1)
            contrib.append({
                'category': c['biz_category'],
                'rr_now': round(float(c['rr']), 4),
                'rr_base': round(b_rr, 4),
                'gap_pp': round(gap * 100, 2),
                'orders_share': round(w, 4),
                'contribution_pp': round(gap * w * 100, 3),
                'orders_now': int(c['orders']),
                # 类目内部的 z 也按二项算，避免小类目被误判
                'z': round(self._binom_z(int(c['returns']), int(c['orders']), b_rr) or 0, 2),
            })
        contrib.sort(key=lambda x: abs(x['contribution_pp']), reverse=True)

        # 原因拆解：只看本周退货明细
        ret_w = self.returns[(self.returns['week'] == week)
                             & (self.returns['biz_category'].isin(
                                 cur_rows['biz_category'].tolist()))]
        reason = (ret_w.groupby('return_reason_text')
                  .agg(n=('return_id', 'size'), loss=('loss_amount', 'sum'))
                  .sort_values('n', ascending=False).head(6).reset_index())
        reason_total = int(ret_w.shape[0])

        note = '贡献度可正可负；正数表示该类目把整体退货率往上拉。'
        # 【样本量不足时，必须把"天然抖动多大"这个数一起预支出去】
        # 踩过的坑：这个 ±x pp 我原来只写在 note / evidence 里（给人看的字符串），
        # 模型的 facts 里拿不到。于是模型自己算了一遍
        # —— 用 1.96×√(p(1-p)/n) 推出 ±6.4pp，逻辑完全正确，
        # 却撞上"不许出现事实里没有的数字"的校验，两次重试都被拦，最后回退模板。
        # 这不是模型的错，是我的错：凡是需要换算才能得到的结论值，后端必须算好并于事实包内给到。
        wobble_pp = float(np.sqrt(base_rr * (1 - base_rr) / max(cur_ord, 1)) * 100)
        # 【一个标准误不够，模型会自己乘系数】
        # 实测：给了 ±3.91pp（1 SE），模型仍然不满足，
        # 又自己乘 1.645 算出 ±6.46pp 写成"90% 置信区间"——
        # 数学上完全正确，但 6.46 不在事实里，被数字校验拦掉，整段回退模板。
        # 它这么做是因为"一个标准误"对运营读者不够直观，
        # 它想说的是"波动范围"。那就把 95% 的区间半宽也算好给它，省得它自己动手。
        wobble95_pp = wobble_pp * 1.96
        if not enough:
            # 【措辞从"不能判断"改成"能分辨多大的变化"】
            # 旧文案只说"样本不足，不足以判定异常"，用户读完仍然不知道
            # 自己该看什么、能看什么，于是换一个问法再问一次，还是同样的答复。
            # 现在直接把可分辨幅度讲清楚：61 单这个量级只能看出十几 pp 的跳变，
            # 1pp 的波动看不出来——但那本来也不值得开会。
            note = (f'本周 {cur_ord} 单，未达 {min_orders} 单的判定门槛；'
                    f'这个样本量能分辨的是 {detect_pp:.1f}pp 以上的变化，'
                    f'更小的波动落在噪声里（天然抖动 ±{wobble_pp:.2f}pp）。')
            if suspicious:
                note += f' 注意：本周偏离 {abs(cur_rr - base_rr) * 100:.1f}pp 已超过可分辨幅度，疑似异动但样本不足以下结论。'

        return ToolResult(
            ok=True,
            data={
                'week': week,
                'category_scope': category or '全部类目',
                'rr_now': round(cur_rr, 4),
                'rr_base': round(base_rr, 4),
                'delta_pp': round((cur_rr - base_rr) * 100, 2),
                'zscore': round(z, 2) if z is not None else None,
                'zscore_naive': round(naive_z, 2) if naive_z is not None else None,
                'is_anomaly': is_anomaly,
                'enough_sample': enough,
                'min_orders': min_orders,
                # 【v2.5】统计显著 ≠ 值得管。两个判定分开给，
                # 让模型/前端能分别说"过了统计门槛"和"但幅度不值一提"。
                'stat_significant': stat_sig,
                'biz_significant': biz_sig,
                'min_effect_pp': self.MIN_EFFECT_PP,
                # 统计显著但幅度太小：既不能算异常，也不能说"没事"
                'trivial_but_significant': trivial_sig,
                # 本周样本量能分辨的最小偏离 —— 回答"能看什么"而不是"为什么不能看"
                'detectable_pp': round(detect_pp, 2) if detect_pp == detect_pp else None,
                'suspicious_but_small': suspicious,
                # 抽样波动幅度：一个标准误 + 95% 区间半宽，两个都给
                'sampling_wobble_pp': round(wobble_pp, 2),
                'sampling_wobble95_pp': round(wobble95_pp, 2),
                'returns_now': cur_ret,
                'category_contribution': contrib,
                'top_reasons': [
                    {'reason': r['return_reason_text'],
                     'count': int(r['n']),
                     'share': round(r['n'] / max(reason_total, 1), 4),
                     'loss': round(float(r['loss']), 2)}
                    for _, r in reason.iterrows()
                ],
                'orders_this_week': cur_ord,
            },
            evidence={
                'scanned': {'panel_rows': int(len(cw)),
                            'return_rows_this_week': reason_total},
                'baseline': f'剔除第 {week} 周后其余 {base_rows["week"].nunique()} 周的合计退货率 '
                            f'({base_ret:,}/{base_ord:,})',
                'anomaly_rule': (f'二项 z = (本周退货率 − 基准率) / √(p(1−p)/n) ≥ 2；'
                                 f'并要求订单量 ≥ {min_orders}（按该基线退货率反推，'
                                 f'目标为可分辨 {self.TARGET_DETECT_PP:.0f}pp 以上偏离）'),
                'why_binom': self._why_binom_text(
                    cur_ord, cur_ret, base_rr, naive_sd, naive_z, z, n_star,
                    scope_week_orders=cw.groupby('week')['orders'].sum()),
                'formula': '类目贡献(pp) = (本周类目退货率 − 该类目基线) × 该类目订单占比',
            },
            note=note,
        )

    # ---------- 归因结构 ----------
    def attribution_structure(self, week: int | None = None,
                              category: str | None = None,
                              top_n: int = 8) -> ToolResult:
        """差评/退货原因结构分析。"""
        week = int(week or self.latest_week)
        r = self.returns[self.returns['week'] == week]
        scope = f'第 {week} 周'
        if category:
            r = r[r['biz_category'] == category]
            scope += f' · {category}'
        if r.empty:
            return ToolResult(ok=False, error=f'{scope} 无退货记录')

        agg = (r.groupby(['return_reason_l1', 'return_reason_text'])
               .agg(n=('return_id', 'size'),
                    loss=('loss_amount', 'sum'),
                    resell=('is_resellable', 'mean'))
               .reset_index()
               .sort_values('n', ascending=False).head(top_n))
        total = int(r.shape[0])

        # 【为什么要算基期占比】
        # 只看"本周破损占 24.4%"会引出一个致命误读：
        # 读者会以为"破损变多了"，进而怀疑商品质量。
        # 但我们要证明的恰恰是"促销改变了退货倾向、质量没变差"。
        # 只有把本周占比和"非促销周基线占比"摆在一起，
        # 才能坐实"破损一直是最大类，但占比并没有上升"。
        # 这是报告里最容易被质疑的一处，必须用数据堵住。
        promo_weeks = set(
            int(w) for w in self.panel.loc[self.panel['promo_week'] == 1, 'week'].unique())
        base_mask = ~self.returns['week'].isin(promo_weeks) & (self.returns['week'] != week)
        b = self.returns[base_mask]
        if category:
            b = b[b['biz_category'] == category]
        base_total = int(b.shape[0])
        base_share = {}
        if base_total > 0:
            bc = b.groupby('return_reason_text').size()
            base_share = {k: float(v) / base_total for k, v in bc.items()}

        return ToolResult(
            ok=True,
            data={
                'scope': scope,
                'total_returns': total,
                'total_loss': round(float(r['loss_amount'].sum()), 2),
                'baseline_scope': '非促销周合计' + (f' · {category}' if category else ''),
                'baseline_returns': base_total,
                'items': [
                    {'l1': x['return_reason_l1'], 'l2': x['return_reason_text'],
                     'count': int(x['n']), 'share': round(x['n'] / total, 4),
                     'share_base': round(base_share.get(x['return_reason_text'], 0.0), 4),
                     'share_shift_pp': round(
                         (x['n'] / total - base_share.get(x['return_reason_text'], 0.0)) * 100, 2),
                     'loss': round(float(x['loss']), 2),
                     'resellable_rate': round(float(x['resell']), 4)}
                    for _, x in agg.iterrows()
                ],
            },
            evidence={
                'scanned': {'returns_this_week': total, 'returns_baseline': base_total},
                'formula': '占比 = 该原因条数 / 本周退货总条数；share_base = 同原因在非促销周的占比',
                'baseline': '剔除全部促销周与本周后的其余周合计，用于区分"占比最大"与"占比上升"',
            },
        )

    # ---------- 补偿实验 ----------
    def compensation_curve(self, category: str | None = None) -> ToolResult:
        """补偿档位 → 挽单率 / 期望利润。

        只用 exp_eligible == 1 的可比子集，这是本项目的关键设计：
        全样本里补偿档位与毛利高度相关（r≈0.21），直接回归会把
        "贵商品本身好卖"误读成"补得多更有效"。
        """
        c = self.logs[self.logs['exp_eligible'] == 1].copy()
        scope = '全部类目'
        if category:
            c = c[c['biz_category'] == category]
            scope = category
        if c.empty:
            return ToolResult(ok=False, error=f'{scope} 无实验样本')

        g = (c.groupby('compensation')
             .agg(n=('is_saved', 'size'),
                  saved=('is_saved', 'mean'),
                  gm=('gross_margin', 'mean'))
             .reset_index())
        g['expected_profit'] = g['saved'] * (g['gm'] - g['compensation'])
        best = g.loc[g['expected_profit'].idxmax()]

        r_comp_gm = float(np.corrcoef(c['compensation'], c['gross_margin'])[0, 1])

        return ToolResult(
            ok=True,
            data={
                'scope': scope,
                'sample_n': int(len(c)),
                'points': [
                    {'compensation': float(x['compensation']),
                     'n': int(x['n']),
                     'save_rate': round(float(x['saved']), 4),
                     'avg_gm': round(float(x['gm']), 2),
                     'expected_profit': round(float(x['expected_profit']), 2)}
                    for _, x in g.iterrows()
                ],
                'optimal': {'compensation': float(best['compensation']),
                            'expected_profit': round(float(best['expected_profit']), 2),
                            'save_rate': round(float(best['saved']), 4)},
                'confound_check': round(r_comp_gm, 4),
            },
            evidence={
                'scanned': {'experiment_rows': int(len(c)),
                            'total_service_logs': int(len(self.logs))},
                'filter': 'exp_eligible == 1（毛利 ≥ ¥50，够得着全部 7 档，可进入完整随机）',
                'confound': f'r(补偿, 毛利) = {r_comp_gm:+.3f}；接近 0 说明未混淆',
                'formula': '期望利润 = 挽单率 × (单件毛利 − 补偿额)',
            },
            note='这是"牺牲单次利润换留存"的权衡曲线，最优档不一定是最贵的档。',
        )

    # ---------- 单原因画像 ----------
    def reason_profile(self, cause: str, category: str | None = None) -> ToolResult:
        """某一个退货原因的全景画像：规模、损失、类目分布、趋势、可二次销售率。

        【为什么必须单独有这个工具】
        用户问「尺码版型不符怎么解决」，系统却答了一份第 44 周为什么涨。
        根因不只是意图分类错了，更深一层是：**没有任何一个工具能回答
        "这个原因本身是什么情况"**。所有工具都是按"周"组织的
        （第44周的原因结构、第44周的损失），没有一个是按"原因"组织的。

        而"怎么治理一个原因"需要的恰恰是按原因看：
          · 它有多少条、占多少 → 决定了值不值得管
          · 损失多少、单均多少 → 决定了能投入多少预算
          · 集中在哪些类目 → 决定了找谁去改
          · 在涨还是在跌 → 决定了是救火还是长期优化
          · 可二次销售率 → 决定了损失的真实口径
        这五件事跟"是哪一周"完全无关。尺码问题不是某一周才有的。

        另外这里会算一个"单均损失 vs 全店单均"的对比，这是最容易
        被忽略的一点：尺码退货看条数很吓人（10,802 条，排第 4），
        但单均损失只有 ¥37，远低于全店 ¥63.5 —— 因为退回来的衣服
        大多还能二次销售。所以治理优先级不该按条数排。
        """
        r = self.returns[self.returns['return_reason_text'] == cause]
        scope = cause
        if category:
            r = r[r['biz_category'] == category]
            scope = f'{cause} · {category}'
        if r.empty:
            return ToolResult(ok=False, error=f'没有「{scope}」这个原因的退货记录')

        total_n = int(len(self.returns))
        n = int(len(r))
        loss = float(r['loss_amount'].sum())
        per = loss / max(n, 1)
        # 全店单均损失：用来回答"这个原因算贵还是便宜"
        all_per = float(self.returns['loss_amount'].sum()) / max(total_n, 1)

        by_cat = (r.groupby('biz_category')
                  .agg(n=('return_id', 'size'), loss=('loss_amount', 'sum'))
                  .reset_index().sort_values('n', ascending=False))
        cat_total = self.returns.groupby('biz_category').size()
        # 每个类目"这类原因占它自己退货的比例" —— 比绝对条数更有指向性，
        # 因为大类目天然条数多。
        cats = [{'category': x['biz_category'],
                 'n': int(x['n']),
                 'share_in_cat': round(int(x['n']) / max(int(cat_total.get(x['biz_category'], 1)), 1), 4),
                 'loss': round(float(x['loss']), 2)}
                for _, x in by_cat.iterrows()]

        # 周度趋势：用最近 12 周，看它在变好还是变坏
        wk = (r.groupby('week').size().rename('n').to_frame()
              .join(self.returns.groupby('week').size().rename('tot'), how='left'))
        wk['share'] = wk['n'] / wk['tot'].replace(0, np.nan)
        wk = wk.tail(12)
        trend = [{'week': int(w), 'n': int(x['n']),
                  'share': round(float(x['share']), 4) if x['share'] == x['share'] else None}
                 for w, x in wk.iterrows()]
        # 前半段 vs 后半段，给一句能直接进汇报的判断
        if len(wk) >= 6:
            half = len(wk) // 2
            early = float(wk['share'].iloc[:half].mean())
            late = float(wk['share'].iloc[half:].mean())
            drift_pp = (late - early) * 100
        else:
            early = late = float('nan')
            drift_pp = 0.0

        resell = float(r['is_resellable'].mean())

        return ToolResult(
            ok=True,
            data={
                'scope': scope,
                'cause': cause,
                'n': n,
                'share_of_all_returns': round(n / max(total_n, 1), 4),
                'loss_total': round(loss, 2),
                'loss_per_return': round(per, 2),
                'all_loss_per_return': round(all_per, 2),
                # 单均损失比：<1 说明这个原因"退得多但每单不贵"
                'per_loss_ratio': round(per / max(all_per, 0.01), 3),
                'resellable_rate': round(resell, 4),
                'by_category': cats,
                'trend_12w': trend,
                'drift_pp': round(drift_pp, 2),
                'drift_note': ('在收窄' if drift_pp < -0.2 else
                               '在扩大' if drift_pp > 0.2 else '基本持平'),
            },
            evidence={
                'scanned': {'returns_rows': int(len(self.returns)),
                            'matched': n},
                'formula': ('占比 = 该原因退货单数 / 全部退货单数；'
                            '单均损失比 = 该原因单均 / 全店单均；'
                            '漂移 = 近 12 周后半段占比均值 − 前半段占比均值'),
                'note': '可二次销售率高 → 损失以物流费为主；低 → 货值也要计提',
            },
        )

    # ---------- 文本归因 ----------
    # 原因名 → 差评表里的归因代码
    #
    # 【这张表是补一个静默失效的 bug】
    # reviews 表的 cause_l2 存的是代码（P1/P2/F1...），而上游传下来的
    # 是中文原因名（"尺码版型不符"）。旧版直接拿中文名去筛 `cause_l2`，
    # 永远筛不出东西 —— 于是 review_samples 一直返回 ok=False，
    # "用户到底怎么说的"这条最有说服力的证据**从来没有在回答里出现过**。
    # 而且它不报错、不抛异常，只是安静地返回空，
    # 所以直到做"按原因给方案"这个功能时才发现。
    REASON_CODE = {
        '尺码版型不符': 'P1',
        '材质做工问题': 'P2',
        '功能性能故障': 'P3',
        '破损/变质/污损': 'F1',
        '时效慢/错发漏发': 'F2',
        '价值感知不符': 'E1',
        '描述与实物不符': 'I1',
        '售后流程问题': 'S1',
        '七天无理由/不想要了': 'S2',
    }

    def review_samples(self, cause: str | None = None,
                      category: str | None = None, limit: int = 8) -> ToolResult:
        """取真实评论样本，供 Agent 做文本级归因或生成改写建议。"""
        d = self.reviews[self.reviews['label'] == 0]
        scope = '全部差评'
        if cause:
            # 中文原因名先映射成代码；已经是代码就原样用
            code = self.REASON_CODE.get(cause, cause)
            d = d[d['cause_l2'] == code]
            scope = f'归因 {cause}'
        if category:
            d = d[d['biz_category'] == category]
            scope += f' · {category}'
        if d.empty:
            return ToolResult(ok=False, error=f'{scope} 无匹配评论')

        # 【不能直接 head(limit)】
        # 踩过的坑：尺码这个原因共 9 条带原文的差评，上游只要了前 5 条，
        # 而"同码不同大、发错码"那条正好排在第 7 —— 于是证据集里
        # 只剩下"偏大/偏小"这类，结论也就写成了"加尺码选择器就行"。
        # 真正的问题（生产公差）连出场机会都没有。
        #
        # 第一层原因在上游（limit 给得太小，见 orchestrator._args），
        # 第二层原因在这里：head 取的是建表时的前 N 行，行序没有任何业务含义。
        # 匹配到几千条时，"前 20 条"可能全部来自同一个 SKU、同一个月，
        # 看着像抽样，其实是同一批货的重复抱怨。
        # 等距抽样能在不改变条数的前提下覆盖整个分布，且结果仍然确定。
        if len(d) > limit:
            step = len(d) / float(limit)
            s = d.iloc[[int(i * step) for i in range(limit)]]
        else:
            s = d

        return ToolResult(
            ok=True,
            data={'scope': scope, 'total_matched': int(len(d)),
                  'samples': [
                      {'review_id': x['review_id'], 'rating': int(x['rating']),
                       'category': x['biz_category'], 'cause': x['cause_l2'],
                       'text': x['review']}
                      for _, x in s.iterrows()]},
            evidence={'scanned': {'negative_reviews': int((self.reviews['label'] == 0).sum()),
                                  'matched': int(len(d))}},
        )

    # ---------- 损失测算 ----------
    def loss_breakdown(self, category: str | None = None) -> ToolResult:
        """退货损失的结构化拆解：按类目 / 按原因 / 按口径（折损 vs 报废）。"""
        r = self.returns.copy()
        scope = '全部类目'
        if category:
            r = r[r['biz_category'] == category]
            scope = category
        by_cat = (r.groupby('biz_category')
                  .agg(n=('return_id', 'size'),
                       loss=('loss_amount', 'sum'),
                       per=('loss_amount', 'mean'))
                  .reset_index())
        gp = self.panel.groupby('biz_category')['gross_profit'].sum()
        by_cat['gp'] = by_cat['biz_category'].map(gp)
        by_cat['share_of_gp'] = by_cat['loss'] / by_cat['gp']
        by_cat = by_cat.sort_values('loss', ascending=False)

        basis = (r.groupby('loss_basis')
                 .agg(n=('return_id', 'size'), loss=('loss_amount', 'sum'))
                 .reset_index())

        # 整体占毛利比：这个数比"总额"更能说明问题严重程度。
        # 之前只有分类目明细，结论里想引用整体占比还得让上层自己再算一遍，
        # 容易算错（第一版就拿过 by_category[0] 的 52.2% 当成整体值）。
        total_loss_f = float(r['loss_amount'].sum())
        gp_scope = float(gp.sum()) if category else float(self.panel['gross_profit'].sum())
        share_all = total_loss_f / gp_scope if gp_scope else 0.0

        return ToolResult(
            ok=True,
            data={
                'scope': scope,
                'total_loss': round(total_loss_f, 2),
                'total_gross_profit': round(gp_scope, 2),
                'share_of_gp': round(share_all, 4),
                'per_return': round(float(r['loss_amount'].mean()), 2),
                'resellable_rate': round(float(r['is_resellable'].mean()), 4),
                'by_category': [
                    {'category': x['biz_category'], 'count': int(x['n']),
                     'loss': round(float(x['loss']), 2),
                     'per_return': round(float(x['per']), 2),
                     'share_of_gp': round(float(x['share_of_gp']), 4)}
                    for _, x in by_cat.iterrows()],
                'by_basis': [
                    {'basis': x['loss_basis'], 'count': int(x['n']),
                     'loss': round(float(x['loss']), 2)}
                    for _, x in basis.iterrows()],
            },
            evidence={
                'scanned': {'returns': int(len(r))},
                'formula': '损失按成本计提：报废 = 物流 + cogs；可二次销售 = 物流 + cogs × 折损率',
                'why_cost': '退款后商家损失的是商品成本，零售价里的毛利从未落袋',
            },
        )

    # ---------- 多周对比 ----------
    def compare_weeks(self, weeks: list[int],
                      category: str | None = None) -> ToolResult:
        """把多个周放在同一口径下对比，回答"哪个更严重"。

        【为什么单独做这个工具，而不是让编排层连着调几次 diagnose_week】
        两个原因：
        1. **口径必须完全一致**。分别调用时，每次的基线都是
           "剔除本周的其余周"，两份基线略有差别，
           放在一张表里比大小会让读者以为是同一个尺子量出来的。
           这里统一用"剔除这几周之后的其余周"做共同基线。
        2. **"更严重"需要一个可比标量**。退货率高低、偏离基线多少、
           z 值大小，三个指标排序可能不一致（比如某周退货率高但样本小、
           z 反而不显著）。必须把这三个都摆出来，并给出一个明确的排序依据。
        """
        cw = self._cat_week.copy()
        scope = '全部类目'
        if category:
            cw = cw[cw['biz_category'] == category]
            scope = category
            if cw.empty:
                return ToolResult(ok=False, error=f'没有 {category} 这个类目')

        ws = [int(w) for w in (weeks or [])]
        ws = [w for w in ws if w in set(cw['week'].unique())]
        if len(ws) < 2:
            return ToolResult(ok=False, error='至少需要两个有效周次才能对比')

        # 共同基线：剔除被比较的这几周，避免"拿自己跟自己比"
        base_rows = cw[~cw['week'].isin(ws)]
        base_ret = int(base_rows['returns'].sum())
        base_ord = int(base_rows['orders'].sum())
        base_rr = base_ret / max(base_ord, 1)

        rows = []
        for w in ws:
            cur = cw[cw['week'] == w]
            if cur.empty:
                continue
            ret = int(cur['returns'].sum())
            ordn = int(cur['orders'].sum())
            rr = ret / max(ordn, 1)
            z = self._binom_z(ret, ordn, base_rr)
            # 门槛同样按基线反推，与 diagnose_week 保持一致口径
            mo = self._min_orders_for(base_rr)
            d_pp = (rr - base_rr) * 100
            # 【v2.5】同样用双条件，否则"哪个更严重"会被 0.6pp 的
            # 统计显著噪声带偏 —— 排序依据是 z，但入选门槛必须含业务幅度。
            stat_sig = bool(ordn >= mo and z is not None and abs(z) >= 2.0)
            biz_sig = abs(d_pp) >= self.MIN_EFFECT_PP
            rows.append({
                'week': w,
                'rr': round(rr, 4),
                'orders': ordn,
                'returns': ret,
                'delta_pp': round(d_pp, 2),
                'z': round(z, 2) if z is not None else None,
                'enough_sample': ordn >= mo,
                'detectable_pp': round(self._detectable_pp(ordn, base_rr), 2),
                'stat_significant': stat_sig,
                'biz_significant': biz_sig,
                'trivial_but_significant': bool(stat_sig and not biz_sig),
                # 显著、为上升、且幅度够大，才算"问题"；下降的显著不算
                'is_anomaly_up': bool(stat_sig and biz_sig and d_pp > 0),
            })

        # 【排序依据要说清楚，不能只给名次】
        # 按 z 排而不是按退货率排：退货率高低受类目结构和样本量影响，
        # z 才是"偏离正常水位多少个标准差"，跨周可比。
        # 但样本不足的周 z 不可信，必须排到后面并标注。
        rows.sort(key=lambda x: (x['enough_sample'], abs(x['z'] or 0)), reverse=True)
        worst = rows[0] if rows else None

        return ToolResult(
            ok=True,
            data={
                'scope': scope,
                'weeks': rows,
                'baseline_rr': round(base_rr, 4),
                'baseline_note': f'基线为剔除第 {"、".join(str(w) for w in ws)} 周后的其余周合计',
                'worst_week': worst['week'] if worst else None,
                'rank_by': 'z 值绝对值（偏离基线多少个标准差），样本不足的周排在后面',
            },
            evidence={
                'scanned': {'weeks_compared': len(rows),
                            'baseline_orders': base_ord},
                'formula': 'z = (本周退货数 − 期望退货数) / √(n·p·(1−p))，p 为共同基线退货率',
                'why_z': '退货率不能直接跨周比大小：样本量不同，同样 18% 的可信度差别很大',
            },
            note='排序按 z 而非退货率；z 相同则看偏离基线的百分点。',
        )

    # ---------- 趋势 ----------
    def trend(self, metric: str = 'return_rate', category: str | None = None,
              weeks: int = 12) -> ToolResult:
        """近 N 周趋势，用于看图说话而不是只看单点。"""
        cw = self._cat_week.copy()
        if category:
            cw = cw[cw['biz_category'] == category]
        g = (cw.groupby('week')
             .agg(orders=('orders', 'sum'), returns=('returns', 'sum'),
                  gmv=('gmv', 'sum'), dsr=('dsr', 'mean'),
                  exposure=('exposure', 'sum'))
             .reset_index())
        g['rr'] = g['returns'] / g['orders'].replace(0, np.nan)
        g = g.tail(int(weeks))
        key = {'return_rate': 'rr', 'dsr': 'dsr', 'gmv': 'gmv',
               'orders': 'orders', 'exposure': 'exposure'}.get(metric, 'rr')
        return ToolResult(
            ok=True,
            data={'metric': metric,
                  'scope': category or '全部类目',
                  'series': [{'week': int(x['week']),
                              'value': round(float(x[key]), 4)}
                             for _, x in g.iterrows()]},
            evidence={'scanned': {'weeks': int(len(g))},
                      'formula': f'按周聚合 {metric}'},
        )

    # ---------- 价值换算 ----------
    def value_of_reduction(self, pp: float = 1.0,
                           category: str | None = None) -> ToolResult:
        """把"退货率降 X 个百分点"翻译成钱。

        单位换算是这里最容易错的地方：回归里 rr 是 0–1 小数，
        λ=1.30 表示 rr 变 1.0（100 个百分点）时 DSR 变 1.30 分，
        所以降 pp 个百分点必须乘 pp/100。
        """
        import json
        with open(_p('_verification.json'), encoding='utf-8') as f:
            v = json.load(f)
        lam = float(v['lambda1']['est'])
        omega = float(v['omega1']['est'])

        p = self.panel if not category else self.panel[self.panel['biz_category'] == category]
        monthly_gmv = float(p['gmv'].sum()) / 12
        monthly_exp = float(p['exposure'].sum()) / 12
        uv_value = float(p['gmv'].sum()) / max(float(p['visitors'].sum()), 1)

        delta_dsr = lam * (pp / 100.0)
        delta_ln_e = omega * delta_dsr
        lift = monthly_gmv * (np.exp(delta_ln_e) - 1)

        return ToolResult(
            ok=True,
            data={
                'scope': category or '全部类目',
                'pp_reduced': pp,
                'delta_dsr': round(delta_dsr, 5),
                'exposure_lift_pct': round(delta_ln_e * 100, 4),
                'monthly_gmv_lift': round(lift, 2),
                'annual_gmv_lift': round(lift * 12, 2),
                'monthly_gmv': round(monthly_gmv, 2),
                'pct_of_gmv': round(lift / monthly_gmv, 5) if monthly_gmv else 0,
                'uv_value': round(uv_value, 4),
            },
            evidence={
                'lambda1': lam, 'omega1': omega,
                'formula': ('DSR↑ = λ1 × (pp/100)；曝光↑ = exp(ω1 × DSR↑) − 1；'
                            'GMV 增量 = 月 GMV × 曝光增幅'),
                'why_divide_100': 'λ1 定义在 0–1 的 rr 尺度上，1 个百分点 = 0.01 个单位',
            },
        )

    # ---------- 促销机制归因 ----------
    def promo_mechanism(self, category: str | None = None) -> ToolResult:
        """检验"促销周退货率上涨"到底是**结构效应**还是**机制效应**。

        为什么需要这个工具：看到"双11 退货率涨了"，最常见的解释是
        "爆单导致履约压力大、破损变多"。这是一个直觉故事，但它可能完全是错的。
        判断它真假只需要三个检验，全部可以在本数据里做完：

          检验1 结构效应：促销周的订单是否往高退货率类目倾斜？
                 若类目占比几乎不变，则"结构变化"解释不了涨幅。
          检验2 机制效应：同一个 SKU（控制住品类、价格、质量），
                 在促销周自身的退货倾向是否也抬高了？
                 这是配对比较，能排掉"选品不同"的干扰。
          检验3 替代解释：促销周是否伴随降价、是否推了低质 SKU？
                 若都没有，则"降价换量"或"清库存"也解释不了。

        三个检验都排除后，剩下的结论才是可信的：**促销这个动作本身**
        改变了退货倾向（可能是冲动下单、比价后放弃、凑单后退等），
        而不是商品或物流变差了。这个区分直接决定运营该改什么。
        """
        p = self.panel.copy()
        # 【v2.5：必须按类目过滤，否则两个类目会拿到同一份数字】
        #
        # 实测发现的问题：问"数码的退货率为什么新高"和问"服饰类目怎么样"，
        # 两份报告里的机制贡献都是 3.98pp、配对 t 都是 15.8、
        # 都是"151 个 SKU 中 97% 上升"。因为旧版这里根本没看 category，
        # 永远算全店。生成器只给了 158 个 SKU，所以宏观口径下
        # "151 个参与促销的 SKU"这个数在**任何一个**类目的报告里都成立——
        # 它当然成立，它压根没按类目分过。
        #
        # 这比"数字过期"更隐蔽：数字本身是真的（全店确实如此），
        # 但它回答的不是用户问的那件事。用户问数码，拿到的却是全店结论，
        # 再用"数码"这个词包装起来。静默降级最典型的形态。
        scope = category or '全部类目'
        if category:
            p = p[p['biz_category'] == category]
            if p.empty:
                return ToolResult(ok=False, error=f'没有 {category} 这个类目')

        promo_weeks = sorted(p.loc[p['promo_week'] == 1, 'week'].unique().tolist())
        if not promo_weeks:
            return ToolResult(ok=False, error='数据中没有 promo_week 标记')

        p['is_promo'] = p['week'].isin(promo_weeks)

        # ---- 检验1：类目结构是否偏移 ----
        # 限定到单个类目时，"结构效应"这一项在定义上就恒等于 0
        # （只有一个类目，占比永远是 100%）。这不是缺陷，是口径使然：
        # 类目内部的涨跌不可能由"类目构成变化"解释。所以这里保留计算，
        # 但会在 note 里说明为什么结构项对本类目无意义。
        cw = (p.groupby(['biz_category', 'week'], as_index=False)
              .agg(orders=('orders', 'sum'), returns=('returns', 'sum')))
        tot = cw.groupby('week')['orders'].sum().rename('tot')
        cw = cw.join(tot, on='week')
        cw['share'] = cw['orders'] / cw['tot']
        struct = []
        for c in sorted(cw['biz_category'].unique()):
            s = cw[cw['biz_category'] == c]
            in_promo = s[s['week'].isin(promo_weeks)]
            out_promo = s[~s['week'].isin(promo_weeks)]
            now = float(in_promo['share'].mean())
            base = float(out_promo['share'].mean())
            rr_now = float(in_promo['returns'].sum() / max(in_promo['orders'].sum(), 1))
            rr_base = float(out_promo['returns'].sum() / max(out_promo['orders'].sum(), 1))
            struct.append({
                'category': c,
                'share_promo': round(now, 4),
                'share_base': round(base, 4),
                'share_shift_pp': round((now - base) * 100, 2),
                'rr_promo': round(rr_now, 4),
                'rr_base': round(rr_base, 4),
                'rr_gap_pp': round((rr_now - rr_base) * 100, 2),
            })
        # 结构效应能解释的涨幅 = Σ (促销占比 − 基线占比) × 基线类目退货率
        # 这是标准的 shift-share 分解：假设类目内部退货率不变，只看构成变化
        struct_effect = sum(
            (x['share_promo'] - x['share_base']) * x['rr_base'] for x in struct) * 100

        # ---- 检验2：同 SKU 配对（控制 SKU 固定效应）----
        pv = p.pivot_table(index='sku_id', columns='is_promo',
                           values='return_rate_latent', aggfunc='mean')
        pv.columns = ['off', 'on']
        pv = pv.dropna()
        pair_diff = (pv['on'] - pv['off'])
        # 配对 t 检验（手算，不引 scipy）
        n_pair = int(len(pair_diff))
        mean_d = float(pair_diff.mean())
        se_d = float(pair_diff.std(ddof=1) / np.sqrt(max(n_pair, 1)))
        t_stat = mean_d / se_d if se_d > 0 else None

        # ---- 检验3：替代解释 ----
        price_pv = p.pivot_table(index='sku_id', columns='is_promo',
                                 values='price', aggfunc='mean').dropna()
        price_pv.columns = ['off', 'on']
        price_chg = (price_pv['on'] / price_pv['off'] - 1) * 100
        q_promo = float(p.loc[p['is_promo'], 'quality_endow'].mean())
        q_base = float(p.loc[~p['is_promo'], 'quality_endow'].mean())
        order_share = (p.groupby('is_promo')
                       .agg(orders=('orders', 'sum'), gmv=('gmv', 'sum'),
                            visitors=('visitors', 'sum')))
        order_share['cvr'] = order_share['orders'] / order_share['visitors']

        # 实际涨幅
        cur = p[p['is_promo']]
        base_rows = p[~p['is_promo']]
        rr_promo = float(cur['returns'].sum() / max(cur['orders'].sum(), 1))
        rr_base = float(base_rows['returns'].sum() / max(base_rows['orders'].sum(), 1))
        actual_gap = (rr_promo - rr_base) * 100

        # 机制效应 = 配对差 → 换算成整体退货率的百分点
        mech_pp = mean_d * 100

        return ToolResult(
            ok=True,
            data={
                'scope': scope,
                'promo_weeks': [int(w) for w in promo_weeks],
                'rr_promo': round(rr_promo, 4),
                'rr_base': round(rr_base, 4),
                'actual_gap_pp': round(actual_gap, 2),
                'decomposition': {
                    'structure_pp': round(struct_effect, 2),
                    'mechanism_pp': round(mech_pp, 2),
                    'structure_share': round(
                        abs(struct_effect) / max(abs(struct_effect) + abs(mech_pp), 1e-9), 4),
                },
                'category_structure': struct,
                'paired_test': {
                    'n_sku': n_pair,
                    'mean_diff_pp': round(mean_d * 100, 3),
                    'sd_pp': round(float(pair_diff.std(ddof=1)) * 100, 3),
                    't_stat': round(float(t_stat), 2) if t_stat is not None else None,
                    'pct_sku_increased': round(float((pair_diff > 0).mean()), 4),
                },
                'alternatives': {
                    'price_change_median_pct': round(float(price_chg.median()), 3),
                    'pct_sku_discounted': round(float((price_chg < -0.5).mean()), 4),
                    'quality_promo': round(q_promo, 4),
                    'quality_base': round(q_base, 4),
                    'quality_gap': round(q_promo - q_base, 4),
                    'cvr_promo': round(float(order_share.loc[True, 'cvr']), 4),
                    'cvr_base': round(float(order_share.loc[False, 'cvr']), 4),
                    'orders_promo_avg': int(cur.groupby('week')['orders'].sum().mean()),
                    'orders_base_avg': int(base_rows.groupby('week')['orders'].sum().mean()),
                },
                'verdict': self._promo_verdict(struct_effect, mech_pp, price_chg, q_promo, q_base),
            },
            evidence={
                'scanned': {'panel_rows': int(len(p)),
                            'sku_paired': n_pair,
                            'promo_weeks': len(promo_weeks),
                            'scope': scope},
                'method_structure': (
                    'shift-share 分解：结构效应 = Σ(促销占比 − 基线占比) × 基线类目退货率'
                    + ('。注意：当前限定单一类目，类目占比恒为 100%，'
                       '结构项在这个口径下没有解释力，判断只看机制项'
                       if category else '')),
                'method_mechanism': 'SKU 配对差分（同一 SKU 促销周 vs 非促销周），等价于加 SKU 固定效应',
                'formula': '实际涨幅 = 结构效应 + 机制效应（残差项另计）',
            },
            note=('结构效应与机制效应之和不完全等于实际涨幅，差额来自交叉项。'
                  + (f'本结果只统计「{category}」类目内的 SKU 与订单，'
                     f'不含其他类目。' if category else '')),
        )

    @staticmethod
    def _promo_verdict(struct_eff: float, mech_pp: float,
                       price_chg: 'pd.Series', q_promo: float, q_base: float) -> dict:
        """把三个检验的结果转成一句能直接进汇报的话。

        这里刻意不写死结论：判定逻辑跟着数据走，换一份数据会得到不同答案。
        """
        struct_small = abs(struct_eff) < 0.5
        mech_big = abs(mech_pp) >= 1.0
        no_discount = float((price_chg < -0.5).mean()) < 0.2
        no_quality_drop = (q_promo - q_base) > -0.05

        if struct_small and mech_big and no_discount and no_quality_drop:
            return {
                'code': 'PROMO_BEHAVIOR',
                'headline': '促销周退货率上升源于促销行为本身，不是商品或物流变差',
                'evidence': [
                    '类目构成几乎没变，结构效应可以忽略',
                    '同一批 SKU 在促销周自身的退货倾向就更高（配对比较）',
                    '没有降价、也没有推低质 SKU，排除"低价低质"解释',
                ],
                'implication': '治理方向应放在促销期的下单预期管理（凑单提醒、延迟发货预告、'
                               '到手价说明），而不是去查供应链',
            }
        if not struct_small:
            return {
                'code': 'STRUCTURE',
                'headline': '退货率上升主要来自订单结构偏移',
                'evidence': ['促销期订单向高退货率类目集中'],
                'implication': '治理方向是类目流量配比与高退货类目的准入门槛',
            }
        return {
            'code': 'MIXED',
            'headline': '退货率上升是多种因素叠加，需要进一步拆分',
            'evidence': [f'结构效应 {struct_eff:+.2f}pp，机制效应 {mech_pp:+.2f}pp'],
            'implication': '建议按类目分别做配对检验，定位主导因素',
        }

    # ---------- 商家动作建议 ----------
    def action_playbook(self, cause: str) -> ToolResult:
        """按归因原因给出可执行动作清单（含预估影响与责任方）。

        这是"分析"变"决策"的一步：每条动作都挂在具体原因上，
        并且写清它影响哪个指标、谁来执行、怎么验收。
        """
        PLAY = {
            'P1': ('尺码版型不符', [
                ('在详情页首屏加"尺码选择器 + 真人试穿视频"，按身高体重推荐',
                 '退货率', '运营/美工', '尺码类退货率周环比'),
                ('把各尺码的实际三围数据做成表格（现在多数详情页只有 S/M/L）',
                 '退货率', '商品', '尺码咨询转退货占比'),
                ('对已退货用户推"换码不退款"券，把退货转成换货',
                 '挽单率', '客服', '换货转化率'),
            ]),
            'P2': ('材质做工问题', [
                ('详情页补 3 组微距实拍：面料纹理、缝线、水洗标',
                 '退货率', '美工', '材质类差评占比'),
                ('把"起球/掉色"写进洗涤说明，前置管理预期',
                 '差评率', '运营', '材质类差评占比'),
                ('抽检同批次留样，锁定问题批次并下架',
                 '退货率', '供应链', '批次不良率'),
            ]),
            'P3': ('功能性能故障', [
                ('详情页补"真实续航测试"数据（标注测试条件）',
                 '退货率', '运营', '功能类差评占比'),
                ('上架前做 72 小时老化测试，剔除早期失效批次',
                 '退货率', '供应链', '开箱不良率'),
                ('客服话术加"故障自检 3 步"，减少无效退货',
                 '挽单率', '客服', '一次解决率'),
            ]),
            'F1': ('破损/变质', [
                ('生鲜改用"泡沫箱 + 冰袋 + 防撞隔板"，按品类定温区',
                 '退货率', '供应链', '破损类退货率'),
                ('把破损率纳入承运商考核，按月结算扣罚',
                 '退货率', '物流', '承运商破损率'),
                ('破损订单 2 小时内主动补偿，不等客户投诉',
                 '差评率', '客服', '破损单差评率'),
            ]),
            'F2': ('时效/错发', [
                ('按区域分仓，把华东华南的承诺时效从 72h 压到 48h',
                 'DSR', '物流', '超时订单占比'),
                ('发货前加一道"扫码复核"，错发率是人工拣货的主要漏洞',
                 '退货率', '仓储', '错发率'),
                ('超时订单自动推送延迟说明 + 补偿券',
                 '差评率', '客服', '超时单差评率'),
            ]),
            'E1': ('价值感知不符', [
                ('详情页首屏放"这个价格你买到什么"的对比图',
                 '退货率', '运营', '价值类差评占比'),
                ('把赠品/服务权益显性化（现在多数藏在详情页底部）',
                 '客单价', '运营', '加购率'),
                ('对"觉得不值"的退货用户做回访问卷，定位预期落差',
                 '差评率', '客服', '回访回收率'),
            ]),
            'I1': ('描述不符', [
                ('主图与实物做同色卡比对，杜绝滤镜色差',
                 '退货率', '美工', '描述不符类退货率'),
                ('标题去掉"显瘦/高端"等无法验证的形容词',
                 '差评率', '运营', '描述类差评占比'),
                ('建立"详情页承诺"清单，发货前逐条核对',
                 '退货率', '品控', '承诺达成率'),
            ]),
        }
        # 【接受中文原因名，不只是代码】
        # 踩过的坑：上游传下来的是中文名（"尺码版型不符"），而这里只认
        # P1/P2 这类代码，于是返回 ok=False、playbook 从未真正产出过。
        # 更隐蔽的是它不报错中断，只安静地返回失败，
        # 上层于是拿到空的 facts['playbook']，最后写出一份"无事实支撑"的回答。
        # 现在两种都收：中文名用 REASON_CODE 翻译成代码，已经是代码就直接用。
        #
        # 【这里我第一次写反了方向】
        # 写的是 _rev = {v: k for k, v in REASON_CODE.items()} 然后 _rev.get(cause)
        # —— 那样得到的映射是 {P1: 中文名}，拿中文名去查当然查不到，
        # 结果中文名全被拒。方向应该是 {中文名: P1}，也就是 REASON_CODE 本身。
        # 教训：字典反查很容易想当然，写完必须拿真实值跑一遍。
        code = cause if cause in PLAY else self.REASON_CODE.get(cause, cause)
        if code not in PLAY:
            return ToolResult(
                ok=False,
                error=(f'未收录原因「{cause}」。可用的中文名：'
                       f'{"、".join(PLAY[k][0] for k in PLAY)}'))
        name, acts = PLAY[code]
        return ToolResult(
            ok=True,
            data={'cause': cause, 'cause_name': name,
                  'actions': [{'action': a, 'impact_on': m, 'owner': o,
                               'kpi': k} for a, m, o, k in acts]},
            evidence={'source': '团队基于归因体系整理的运营手册（非模型生成）',
                      'note': '动作与 KPI 的对应关系是可验收的，便于落地追踪'},
        )

    # ---------- 知识库兜底 ----------
    def reason_kb(self, cause: str) -> ToolResult:
        """数据里没有这个原因时，从本地知识库取通用治理方案。

        【为什么需要它】
        数据里的原因结构只有九类（P1/P2/P3/F1/F2/E1/I1/S1/S2）。
        用户问「标签脱落怎么解决」，这九类里一个都没有——
        旧行为是 action_playbook 返回 ok=False，上层写"未收录原因"，
        然后整份回答退化成"这个问题我答不了"。
        用户不关心我的分类体系叫什么，他只想知道标签脱落该找谁改。

        【但绝不能把知识库和本店数据混在一起说】
        知识库里的动作是行业通用做法，不是从这家店的 61.8 万单里推出来的。
        这两者的可信度差一个量级：前者是"一般来说应该这样"，
        后者是"你这店里 10,802 条投诉都指向这个"。
        混着说，用户会以为所有结论都有数据支撑，一旦追问就露馅。
        所以返回值里带 source_tier，组装答案时必须据此标注。

        【找不到时的退路】
        知识库里也没有完全对应的，就按 fallback_to 指到最接近的已知原因，
        并带上 fallback_reason 说明为什么这么归。
        比如「吊牌价格牌印错」→ 归到「材质做工问题」，
        回答里要写"数据里没有单列这一类，我把它归到了 XX"。
        凭空说"数据里没有"是事实，但用户拿到手还是不会做，
        所以退路是"用最接近的那类真实数据 + 说明替代关系"，不是"拒绝"。
        """
        import json as _json

        kb_path = os.path.abspath(os.path.join(BASE, '..', 'reason_kb.json'))
        if not os.path.exists(kb_path):
            # 数据目录可能被整体搬走，给一个明确的错误而不是静默失败
            return ToolResult(ok=False, error=f'知识库文件不存在：{kb_path}')
        try:
            with open(kb_path, 'r', encoding='utf-8') as fp:
                kb = _json.load(fp)
        except Exception as e:
            return ToolResult(ok=False, error=f'知识库读取失败：{type(e).__name__}: {e}')

        q = (cause or '').strip()
        if not q:
            return ToolResult(ok=False, error='未指定原因')

        # ---------- ① 按别名命中 ----------
        hit = None
        for e in kb.get('entries', []):
            if q == e['name'] or q in e.get('aliases', []):
                hit = e
                break
        # ---------- ② 别名包含匹配（用户说法更长或不完全一致）----------
        if hit is None:
            for e in kb.get('entries', []):
                if any(a in q or q in a for a in e.get('aliases', [])):
                    hit = e
                    break

        if hit is not None:
            return ToolResult(
                ok=True,
                data={
                    'matched': True,
                    'cause': q,
                    'kb_id': hit['id'],
                    'kb_name': hit['name'],
                    'signs': hit.get('signs', []),
                    'actions': [
                        {'action': a, 'owner': o, 'kpi': k}
                        for a, o, k in hit.get('actions', [])
                    ],
                    'watch_out': hit.get('watch_out', ''),
                    'source_tier': 'kb',
                    'source_note': ('行业通用做法，来自本地知识库，'
                                    '**不是本店数据推出来的**'),
                    'fallback_to': hit.get('fallback_to'),
                    'fallback_reason': hit.get('fallback_reason'),
                },
                evidence={'source': 'data/reason_kb.json',
                          'match': f'按别名命中「{hit["name"]}」'},
            )

        # ---------- ③ 都没有：给通用动作，但明确说是通用 ----------
        return ToolResult(
            ok=True,
            data={
                'matched': False,
                'cause': q,
                'kb_id': None,
                'kb_name': None,
                'signs': [],
                'actions': [
                    {'action': a, 'owner': o, 'kpi': k}
                    for a, o, k in kb.get('generic_actions', [])
                ],
                'watch_out': '',
                'source_tier': 'generic',
                'source_note': ('知识库里也没有这一类，以下是通用的立项方法，'
                                '**不含任何本店数据结论**'),
                'fallback_to': None,
                'fallback_reason': None,
            },
            evidence={'source': 'data/reason_kb.json',
                      'match': '未命中具体条目，返回通用动作'},
        )


_hub: DataHub | None = None


def get_hub() -> DataHub:
    global _hub
    if _hub is None:
        _hub = DataHub()
    return _hub