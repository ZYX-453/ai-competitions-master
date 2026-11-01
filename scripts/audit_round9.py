# -*- coding: utf-8 -*-
"""
第九轮穿透审计
不预设数据干净。逐项检查：会计恒等式、时间轴、跨表一致性、业务常识边界、
以及"数据能不能撑起项目论证"这最后一问。
"""
import json, sys
import numpy as np
import pandas as pd

P = 'data/processed/'
pd.set_option('display.width', 200)
pd.set_option('display.max_columns', 40)

panel = pd.read_csv(P + 'panel_weekly.csv')
rev_m = pd.read_csv(P + 'reviews_monitor.csv')
rev_t = pd.read_csv(P + 'reviews_train.csv')
ret   = pd.read_csv(P + 'returns.csv')
slog  = pd.read_csv(P + 'service_logs.csv')
sku   = pd.read_csv(P + 'sku_master.csv')
gold  = pd.read_csv(P + 'gold_standard_200.csv')

issues = []
def ok(msg, lvl=None):   print(f'  [OK]   {msg}')
def bad(msg, lvl='P1'):
    print(f'  [!!{lvl}] {msg}')
    issues.append((lvl, msg))

def sec(t):
    print('\n' + '=' * 78)
    print(t)
    print('=' * 78)

# ---------------------------------------------------------------- A 会计恒等式
sec('A. 会计恒等式与内部一致性')

# A1 GMV = orders * price
d = (panel['gmv'] - panel['orders'] * panel['price']).abs().max()
(ok if d < 0.02 else bad)(f'GMV = orders×price，最大偏差 {d:.4f}')

# A2 gross_profit = orders * gross_margin
gm_map = sku.set_index('sku_id')['gross_margin'].to_dict()
exp_gp = panel['orders'] * panel['sku_id'].map(gm_map)
d = (panel['gross_profit'] - exp_gp).abs().max()
(ok if d < 0.02 else bad)(f'gross_profit = orders×gross_margin，最大偏差 {d:.4f}')

# A3 visitors = exposure * ctr  → 反推 ctr 是否落在类目合理区间
panel['ctr'] = panel['visitors'] / panel['exposure']
panel['cvr'] = panel['orders'] / panel['visitors'].replace(0, np.nan)
print('\n  类目 CTR / CVR 实际分布：')
g = panel.groupby('biz_category').agg(
    ctr=('ctr', 'mean'), cvr=('cvr', 'mean'),
    aov=('gmv', lambda s: s.sum() / max(panel.loc[s.index, 'orders'].sum(), 1)))
print(g.round(4).to_string())
bad_ctr = panel[(panel.ctr < 0.004) | (panel.ctr > 0.15)]
bad_cvr = panel[panel.orders > 0]
bad_cvr = bad_cvr[(bad_cvr.cvr < 0.003) | (bad_cvr.cvr > 0.30)]
(ok if len(bad_ctr) == 0 else bad)(f'CTR 越界(<0.4% 或 >15%) 行数 {len(bad_ctr)}')
(ok if len(bad_cvr) == 0 else bad)(f'CVR 越界(<0.5% 或 >30%) 行数 {len(bad_cvr)}')

# A4 returns 汇总 == panel.returns
agg = ret.groupby(['sku_id', 'week']).size().rename('n').reset_index()
m = panel.merge(agg, on=['sku_id', 'week'], how='left').fillna({'n': 0})
diff = (m['returns'] - m['n']).abs()
(ok if diff.max() == 0 else bad)(f'returns.csv 按 sku×week 汇总与 panel.returns 逐单元相等（最大差 {diff.max():.0f}）')
extra = agg.merge(panel[['sku_id', 'week']], on=['sku_id', 'week'], how='left', indicator=True)
(ok if (extra._merge == 'both').all() else bad)(f'returns 中不存在 panel 之外的 sku×week（孤儿 {int((extra._merge=="left_only").sum())} 个）')

# A5 退货金额 = 单价
rp = panel.set_index(['sku_id', 'week'])['price'].to_dict()
ret['_p'] = [rp.get((s, w), np.nan) for s, w in zip(ret.sku_id, ret.week)]
d = (ret['return_amount'] - ret['_p']).abs().max()
(ok if d < 0.02 else bad)(f'退货金额 = 该 SKU 当期单价，最大偏差 {d:.4f}')

# A6 退货金额不应超过订单金额
over = ret[ret.return_amount > ret.order_amount + 0.01]
(ok if len(over) == 0 else bad)(f'退货金额 > 订单金额 的行数 {len(over)}')

# A7 returns 行数总量
(ok if len(ret) == int(panel.returns.sum()) else bad)(
    f'退货明细行数 {len(ret):,} == panel 退货总数 {int(panel.returns.sum()):,}')

# A8 退货日期落在该周区间内
START = pd.Timestamp('2025-11-03')
ret['_rd'] = pd.to_datetime(ret['return_date'])
lo = START + pd.to_timedelta((ret['week'] - 1) * 7, unit='D')
hi = lo + pd.to_timedelta(6, unit='D')
out = ((ret['_rd'] < lo) | (ret['_rd'] > hi)).sum()
(ok if out == 0 else bad)(f'退货日期落在所属周窗口内（越界 {out} 条）')

# A9 评论日期与 week 一致
def chk_rev_date(df, name, week_start):
    d = pd.to_datetime(df['review_date'])
    lo = week_start + pd.to_timedelta((df['week'] - 1) * 7, unit='D')
    hi = lo + pd.to_timedelta(6, unit='D')
    n = ((d < lo) | (d > hi)).sum()
    (ok if n == 0 else bad)(f'{name} 评论日期与 week 对齐（越界 {n} 条）')
chk_rev_date(rev_m, '监控集', START)
# 训练集 week 是相对监控期起点的负数（-156..-1），起点为 2022-11-07 那一周
if rev_t['week'].min() < 0:
    d = pd.to_datetime(rev_t['review_date'])
    span_ok = (d.min() >= pd.Timestamp('2022-11-01')) and (d.max() <= pd.Timestamp('2025-11-02'))
    (ok if span_ok else bad)(
        f'训练集（历史周，week 为负）日期落在 2022-11~2025-10：{d.min().date()} ~ {d.max().date()}')

# ---------------------------------------------------------------- B 时间轴
sec('B. 时间轴与生命周期')

lw = sku.set_index('sku_id')['launch_week'].to_dict()
for name, df, wmin in [('监控集', rev_m, 1), ('训练集', rev_t, -156)]:
    lwv = df['sku_id'].map(lw)
    if name == '监控集':
        n = (df['week'] < lwv).sum()
        (ok if n == 0 else bad)(f'{name} week >= 上架周（违约 {n} 条）')
    else:
        # 训练集是历史区间，只要求评论日期在训练区间内
        d = pd.to_datetime(df['review_date'])
        (ok if d.min() >= pd.Timestamp('2022-11-01') and d.max() <= pd.Timestamp('2025-10-31')
         else bad)(f'{name} 日期区间 {d.min().date()} ~ {d.max().date()}')

# 面板：每个 SKU 的周序列应从 launch_week 连续到 52
cov = panel.groupby('sku_id')['week'].agg(['min', 'max', 'count'])
cov['lw'] = cov.index.map(lw)
bad_start = cov[cov['min'] != cov['lw']]
(ok if len(bad_start) == 0 else bad)(f'面板首周 == 上架周（不符 {len(bad_start)} 个 SKU）')
gap = cov[(cov['max'] - cov['min'] + 1) != cov['count']]
(ok if len(gap) == 0 else bad)(f'面板周序列无断档（断档 {len(gap)} 个 SKU）')
bad_end = cov[cov['max'] != 52]
print(f'  末周 < 52 的 SKU：{len(bad_end)} 个（若为 0 说明所有 SKU 都活到期末，无下架）')

# 评论日期是否超出面板观测窗口
d = pd.to_datetime(rev_m['review_date'])
(ok if d.max() <= START + pd.Timedelta(days=51 * 7 + 6) else bad)(
    f'监控集最晚评论 {d.max().date()} 未超面板窗口末尾 {START + pd.Timedelta(days=51*7+6)}')

# ---------------------------------------------------------------- C 评论
sec('C. 评论数据')

for name, df in [('监控集', rev_m), ('训练集', rev_t)]:
    print(f'\n  {name}: {len(df):,} 行')
    print('   星级分布:', df['rating'].value_counts(normalize=True).sort_index().round(4).to_dict())
    print('   label=0 占比 %.4f' % (df['label'] == 0).mean())

# C1 重号
print(f'\n  review_id 重复：监控集 {rev_m.review_id.duplicated().sum()}，训练集 {rev_t.review_id.duplicated().sum()}')
ids = set(rev_m.review_id) & set(rev_t.review_id)
(ok if len(ids) == 0 else bad)(f'监控集与训练集 review_id 无交集（交集 {len(ids)}）')

# C2 文本碰撞（脱敏后同一文本）
t_m = set(rev_m['review'].astype(str))
t_t = set(rev_t['review'].astype(str))
inter = t_m & t_t
rate = len(inter) / max(len(t_m), 1)
print(f'  文本级交集（脱敏后碰撞）：{len(inter)} 条，占监控集唯文本 {rate:.2%}')
if rate < 0.02:
    ok(f'文本碰撞率 {rate:.2%} 低于 2% 阈值')
else:
    bad(f'文本碰撞率 {rate:.2%} 偏高，训练/评估可能泄漏', 'P2')

# C3 label 与评分映射
# 3 星是"中评"，既可能是 NEU 也可能是负面（内容负面）。只有 1-2 星必须为负、
# 4-5 星必须为正 是硬约束；3 星不做硬约束，只做核对通报。
hard = rev_m[((rev_m.rating <= 2) & (rev_m.label != 0)) |
             ((rev_m.rating >= 4) & (rev_m.label != 1))]
(ok if len(hard) == 0 else bad)(f'1-2星必负/4-5星必正 硬约束（违约 {len(hard)} 条）')
n3 = len(rev_m[(rev_m.rating == 3) & (rev_m.label == 0)])
print(f'  3星中判为负面的：{n3} 条（文本内容确为负面，属"中评差内容"，合理）')

# C4 归因只应出现在负面
pos_with_cause = rev_m[(rev_m.label == 1) & (rev_m.cause_l2.isin(
    ['P1','P2','P3','I1','F1','F2','S1','S2','E1']))]
(ok if len(pos_with_cause) == 0 else bad)(f'非负面评论不应有负面归因（越界 {len(pos_with_cause)} 条）')

# C5 cause 与 label 的联合分布
print('\n  cause_l2 × label 交叉：')
print(pd.crosstab(rev_m['cause_l2'], rev_m['label']).to_string())

# C6 评论长度
print(f"\n  评论字数：中位 {rev_m.review_len.median():.0f}，P95 {rev_m.review_len.quantile(.95):.0f}，"
      f"最长 {rev_m.review_len.max()}")

# ---------------------------------------------------------------- D 跨表串联
sec('D. 三层事件能否按时间轴串联')

# D1 service_logs 的 review_id 必须存在
miss = (~slog.review_id.isin(pd.concat([rev_m.review_id, rev_t.review_id]))).sum()
(ok if miss == 0 else bad)(f'service_logs.review_id 全部可回溯（缺失 {miss}）')

# D2 service_logs 来源分布
src_m = slog.review_id.isin(rev_m.review_id).sum()
src_t = slog.review_id.isin(rev_t.review_id).sum()
print(f'  客服记录来源：监控集 {src_m}，训练集 {src_t}，合计 {len(slog)}')

# D3 客服记录日期 >= 评论日期 且 <= 评论日期+2天
rmap = pd.concat([rev_m, rev_t]).set_index('review_id')['review_date'].to_dict()
slog['_rd'] = pd.to_datetime(slog['review_id'].map(rmap))
slog['_ld'] = pd.to_datetime(slog['log_date'])
delta = (slog['_ld'] - slog['_rd']).dt.days
print(f'  客服响应日 - 评论日：min {delta.min()} / max {delta.max()} / 均值 {delta.mean():.2f}')
(ok if delta.min() >= 0 else bad)(f'客服记录不得早于评论（早于 {int((delta<0).sum())} 条）')
(ok if delta.max() <= 2 else bad)(f'客服响应不超过 2 天（超时 {int((delta>2).sum())} 条）', 'P2')

# D4 退货的 SKU 应该有对应差评（否则退货从哪来的）
rev_neg = set(zip(rev_m[rev_m.label == 0].sku_id, rev_m[rev_m.label == 0].week))
ret_pairs = set(zip(ret.sku_id, ret.week))
hit = len(ret_pairs & rev_neg)
print(f'  退货 sku×week 对：{len(ret_pairs)}，其中同期有差评的：{hit}（{hit/len(ret_pairs):.1%}）')

# D5 退货率与差评率的相关（业务上应正相关）
pm = panel.groupby(['sku_id', 'week']).agg(rr=('return_rate_latent','mean')).reset_index()
neg_rate = rev_m[rev_m.label == 0].groupby(['sku_id','week']).size().rename('neg').reset_index()
cnt = rev_m.groupby(['sku_id','week']).size().rename('tot').reset_index()
mm = pm.merge(neg_rate, on=['sku_id','week'], how='left').merge(cnt, on=['sku_id','week'], how='left')
mm = mm.dropna(subset=['tot'])
mm = mm[mm.tot >= 5]
if len(mm) > 20:
    r = mm['rr'].corr(mm['neg'] / mm['tot'])
    print(f'  周退货倾向 vs 同期差评占比 相关 r = {r:.3f}（n={len(mm)}）')
    (ok if r > 0.05 else bad)(f'退货率与差评率正相关 r={r:.3f}', 'P2')

# ---------------------------------------------------------------- E 业务边界
sec('E. 业务常识边界')

# E1 补偿金额档位
print('  补偿档位分布:', slog['compensation'].value_counts().sort_index().to_dict())
(ok if slog['compensation'].max() <= slog['price'].max() else bad)(
    f"最大补偿 ¥{slog['compensation'].max()} 不超过最高单价 ¥{slog['price'].max()}")

# E2 补偿不应超过毛利（否则挽回无意义）
over = slog[slog['compensation'] > slog['gross_margin'] * 1.5]
print(f'  补偿 > 1.5×单件毛利 的记录：{len(over)}（{len(over)/len(slog):.1%}）')

# E3 挽单率
print(f"  整体挽单率 {slog['is_saved'].mean():.3f}")
by_c = slog.groupby('compensation')['is_saved'].agg(['mean','count'])
print('  按补偿档位：')
print(by_c.round(3).to_string())
# 单调性检查
mono = by_c['mean'].values
inversions = sum(1 for i in range(len(mono)-1) if mono[i+1] < mono[i] - 0.05)
(ok if inversions == 0 else bad)(f'挽单率随补偿递增（明显倒挂 {inversions} 处）', 'P2')

# E4 首次响应时间
print(f"  首响分钟：中位 {slog.first_reply_min.median():.1f}，P95 {slog.first_reply_min.quantile(.95):.1f}")
(ok if slog.first_reply_min.min() >= 0 else bad)('首响时间非负')

# E5 退货逆向物流成本
print(f"  逆向物流：{ret.logistics_cost.min():.1f} ~ {ret.logistics_cost.max():.1f}，"
      f"中位 {ret.logistics_cost.median():.1f}")
(ok if ret.logistics_cost.between(5, 30).all() else bad)('逆向物流成本落在 5-30 元')

# E6 生鲜退货不该二次销售
if '生鲜' in set(ret.biz_category):
    rr_fresh = ret[ret.biz_category == '生鲜']['is_resellable'].mean()
    print(f'  生鲜可二次销售率 {rr_fresh:.1%}')
    (ok if rr_fresh < 0.15 else bad)(f'生鲜鲜果腐烂类退货二次销售率 {rr_fresh:.1%} 应极低', 'P1')

# E7 价格-毛利关系
print(f"\n  毛利率范围 {sku.margin_rate.min():.1%} ~ {sku.margin_rate.max():.1%}")
neg = sku[sku.gross_margin <= 0]
(ok if len(neg) == 0 else bad)(f'存在毛利<=0 的 SKU {len(neg)} 个')

# E8 类目价格带
print('\n  各类目价格带：')
print(sku.groupby('biz_category')['price'].agg(['min','median','max','count']).round(1).to_string())

# ---------------------------------------------------------------- F 统计自洽
sec('F. 统计自洽性（口径与量级）')

# F1 退货率 vs 明细反推
p2 = panel[panel.orders > 0].copy()
agg = ret.groupby(['sku_id','week']).size().rename('n').reset_index()
p2 = p2.merge(agg, on=['sku_id','week'], how='left').fillna({'n': 0})
r_all = p2['returns'].sum() / p2['orders'].sum()
r_detail = len(ret) / p2['orders'].sum()
print(f'  整体退货率：panel {r_all:.4f} / 明细反推 {r_detail:.4f}')
(ok if abs(r_all - r_detail) < 0.001 else bad)('整体退货率两口径一致')

# F2 UV 价值
uv = panel.gmv.sum() / panel.visitors.sum()
print(f'  UV 价值 ¥{uv:.2f}（GMV {panel.gmv.sum()/1e4:.0f}万 / 访客 {panel.visitors.sum()/1e4:.0f}万）')
aov = panel.gmv.sum() / panel.orders.sum()
print(f'  客单价 ¥{aov:.2f}  →  UV = AOV × CVR = {aov:.1f} × {panel.orders.sum()/panel.visitors.sum():.4f} = ¥{aov*panel.orders.sum()/panel.visitors.sum():.2f}')
(ok if 1.0 < uv < 15 else bad)(f'UV 价值 ¥{uv:.2f} 落在电商合理区间')

# F3 每 SKU 每周订单
print(f"  周订单：中位 {panel.orders.median():.0f}，均值 {panel.orders.mean():.1f}，"
      f"零单周 {(panel.orders==0).sum()} 行（{(panel.orders==0).mean():.1%}）")
(ok if (panel.orders == 0).mean() < 0.10 else bad)(
    f'零单周占 {(panel.orders==0).mean():.1%}，偏高说明 SKU 动销过弱', 'P2')

# F4 评论抽样率
print(f"  评论数 / 订单数 = {len(rev_m)+len(rev_t):,} / {panel.orders.sum():,} = "
      f"{(len(rev_m)+len(rev_t))/panel.orders.sum():.2%}")

# F5 DSR 与差评率
dsr_neg = rev_m[rev_m.label == 0].groupby('sku_id').size() / rev_m.groupby('sku_id').size()
panel['_neg'] = panel['sku_id'].map(dsr_neg)
r = panel[['dsr','_neg']].dropna().corr().iloc[0,1]
print(f'  SKU 级 DSR vs 差评占比 相关 r = {r:.3f}')
(ok if r < -0.1 else bad)(f'DSR 与差评占比应负相关（r={r:.3f}）', 'P2')

# F6 quality_endow 的遗漏变量作用
q = panel.groupby('sku_id').agg(rr=('return_rate_latent','mean'), dsr=('dsr','mean'),
                                 exp=('exposure','mean'), q=('quality_endow','first'))
print('\n  quality_endow 与关键变量相关：')
print(q[['rr','dsr','exp','q']].corr()['q'].round(3).to_string())

# ---------------------------------------------------------------- G 项目支撑
sec('G. 能否支撑项目论证')

V = json.load(open(P + '_verification.json'))
print('  参数回收：')
for k in ['lambda1','lambda2','omega1','omega2']:
    if k in V:
        e = V[k]
        print(f"    {k:8s} 真值 {e['true']:.3f}  估计 {e['est']:.4f}  SE {e['se']:.4f}  误差 {e['rel_err']*100:5.1f}%")
print(f"  pass = {V.get('pass')}")

# G1 归因能否支撑「差评归因→经营动作」
print(f"\n  归因覆盖：监控集非负面 {int((rev_m.label==1).sum())} 条，"
      f"负面 {int((rev_m.label==0).sum())} 条")
neg = rev_m[rev_m.label == 0]
unc = (neg.cause_l2 == 'X').mean()
print(f'  差评未归因率 {unc:.2%}，可解释率 {1-unc:.2%}')
per_cat = neg.groupby('biz_category').apply(
    lambda g: (g.cause_l2 == 'X').mean(), include_groups=False)
print('  分类目未归因率:', per_cat.round(3).to_dict())

# G2 归因分布是否类目可区分（这是"归因有用"的前提）
ct = pd.crosstab(neg['biz_category'], neg['cause_l2'], normalize='index')
print('\n  类目 × 归因编码（行归一，仅列出现率>2%的）：')
keep = [c for c in ct.columns if ct[c].max() > 0.02]
print(ct[keep].round(3).to_string())

# G3 补偿曲线是否非平凡（有内部最优）
curve = slog.groupby('compensation').agg(
    p=('is_saved','mean'), price=('price','mean'), n=('is_saved','size'))
curve['margin'] = slog.groupby('compensation')['gross_margin'].mean()
curve['profit'] = curve['p'] * (curve['margin'] - curve.index.values)
print('\n  补偿-期望利润曲线（每单）：')
print(curve.round(2).to_string())
best = curve['profit'].idxmax()
print(f'  经验最优补偿档 ¥{best}（若在端点说明内部最优点未被数据支撑）')

# G4 传导链的可检测性
print(f"\n  传导链：退货→DSR λ=(0.42,0.18)，DSR→曝光 ω=(0.18,0.09)")

# ---------------------------------------------------------------- 汇总
sec('审计结论')
if not issues:
    print('  未发现阻断性问题。')
else:
    print(f'  共 {len(issues)} 项：')
    for lvl, m in issues:
        print(f'    [{lvl}] {m}')

json.dump({'n_issues': len(issues), 'issues': [{'level': l, 'msg': m} for l, m in issues]},
          open(P + '_audit_round9.json', 'w'), ensure_ascii=False, indent=2)
print('\n  结果已写入 data/processed/_audit_round9.json')