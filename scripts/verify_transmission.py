# -*- coding: utf-8 -*-
"""
传导链可识别性验证 + 参数回收检验（简化版 E3）
目的：验证生成的面板数据中，两级传导系数能否被正确估计。
若无法识别，说明 DGP 有问题，必须在提交前修正。
"""
import pandas as pd
import numpy as np
import sys
import json
import os

sys.stdout.reconfigure(encoding='utf-8')
np.random.seed(20261110)

P = 'data/processed/'
panel = pd.read_csv(P + 'panel_weekly.csv')
sku = pd.read_csv(P + 'sku_master.csv')
with open(P + 'dgp_params.json', encoding='utf-8') as f:
    TRUE = json.load(f)['transmission']

print('=' * 74)
print('传导链可识别性验证'.center(66))
print('=' * 74)
print(f'\n真值: λ1={TRUE["lambda1"]}, λ2={TRUE["lambda2"]}, '
      f'ω1={TRUE["omega1"]}, ω2={TRUE["omega2"]}')

# ---------- 构造滞后项 ----------
# 【v2.2 关键修正】这里必须用 return_rate_latent，不能用 return_rate。
# DGP 中 DSR 方程由潜在退货倾向 rr（logit 概率）驱动：
#     DSR_it = ... - λ1·rr_{i,t-1} - λ2·rr_{i,t-2}
# 而 return_rate 是会计口径 returns/orders，经过二项抽样，
# 在低单量周里噪声极大（1 单订单退货就是 100%），
# 用它回归会把 λ1 从 0.409 稀释到 0.022（实测相对误差 94.8%）。
# 这正是"测量误差导致衰减偏误"（attenuation bias）的教科书案例，
# 脚本末尾会同时报告两种口径，作为一条额外的稳健性证据。
df = panel.sort_values(['sku_id', 'week']).copy()
g = df.groupby('sku_id')
df['rr_lag1'] = g['return_rate_latent'].shift(1)
df['rr_lag2'] = g['return_rate_latent'].shift(2)
# 会计口径（含二项噪声）——仅用于对照检验
df['rr_obs_lag1'] = g['return_rate'].shift(1)
df['rr_obs_lag2'] = g['return_rate'].shift(2)
df['dsr_lag2'] = g['dsr'].shift(2)
df['dsr_lag3'] = g['dsr'].shift(3)
df['ln_exposure'] = np.log(df['exposure'])
df = df.dropna(subset=['rr_lag1', 'rr_lag2', 'dsr_lag2', 'dsr_lag3'])

print(f'\n可用样本: {len(df):,} 行')


def twfe_ols(data, y, xs, entity='sku_id', time='week'):
    """双向固定效应 OLS：用组内去均值（within transformation）实现"""
    d = data.copy()
    # 先去时间均值，再去个体均值（近似双向去均值）
    for x in [y] + xs:
        d[x] = d[x].astype(float)
        d[x] = d[x] - d.groupby(time)[x].transform('mean')
        d[x] = d[x] - d.groupby(entity)[x].transform('mean')
    Y = d[y].values
    X = np.column_stack([d[x].values for x in xs] + [np.ones(len(d))])
    beta, *_ = np.linalg.lstsq(X, Y, rcond=None)
    resid = Y - X @ beta
    # 聚类稳健标准误
    n, k = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    clusters = d[entity].values
    uq = np.unique(clusters)
    meat = np.zeros((k, k))
    for c in uq:
        m = clusters == c
        Xc = X[m]
        ec = resid[m]
        sc = Xc.T @ ec
        meat += np.outer(sc, sc)
    G = len(uq)
    adj = G / (G - 1) * (n - 1) / (n - k)
    V = XtX_inv @ meat @ XtX_inv * adj
    se = np.sqrt(np.diag(V))
    return beta, se, n, G


# ---------- 一级传导：退货率 → DSR ----------
print('\n' + '-' * 74)
print('一级传导：DSR_it = δ_i + ψ_t − λ1·rr_{i,t−1} − λ2·rr_{i,t−2}')
print('-' * 74)
b1, s1, n1, g1 = twfe_ols(df, 'dsr', ['rr_lag1', 'rr_lag2'])
print(f'  样本 {n1:,}  聚类数 {g1}')
for name, est, se, truth in [('λ1', -b1[0], s1[0], TRUE['lambda1']),
                             ('λ2', -b1[1], s1[1], TRUE['lambda2'])]:
    t = abs(est) / se if se > 0 else 0
    err = abs(est - truth) / truth * 100
    flag = 'OK' if err < 15 else '偏差大'
    print(f'  {name}  估计 {est:>7.4f}  SE {se:>7.4f}  t={t:>6.1f}  '
          f'真值 {truth:>5.2f}  相对误差 {err:>5.1f}%  [{flag}]')

# ---------- 二级传导：DSR → 曝光 ----------
print('\n' + '-' * 74)
print('二级传导：ln(Exposure)_it = η_i + φ_t + ω1·DSR_{i,t−2} + ω2·DSR_{i,t−3}')
print('-' * 74)
b2, s2, n2, g2 = twfe_ols(df, 'ln_exposure', ['dsr_lag2', 'dsr_lag3'])
print(f'  样本 {n2:,}  聚类数 {g2}')
for name, est, se, truth in [('ω1', b2[0], s2[0], TRUE['omega1']),
                             ('ω2', b2[1], s2[1], TRUE['omega2'])]:
    t = abs(est) / se if se > 0 else 0
    err = abs(est - truth) / truth * 100
    flag = 'OK' if err < 15 else '偏差大'
    print(f'  {name}  估计 {est:>7.4f}  SE {se:>7.4f}  t={t:>6.1f}  '
          f'真值 {truth:>5.2f}  相对误差 {err:>5.1f}%  [{flag}]')

# ---------- 方法对比：Pooled OLS vs TWFE ----------
print('\n' + '-' * 74)
print('方法对比：为何必须用双向固定效应（遗漏变量偏差的实证）')
print('-' * 74)
d = df.dropna(subset=['dsr', 'rr_lag1', 'rr_lag2']).copy()
Y = d['dsr'].values
X = np.column_stack([d['rr_lag1'].values, d['rr_lag2'].values, np.ones(len(d))])
bp, *_ = np.linalg.lstsq(X, Y, rcond=None)
print(f'  Pooled OLS（无固定效应）:  λ1 = {-bp[0]:>7.4f}   真值 {TRUE["lambda1"]:.2f}')
print(f'  双向固定效应（本方案）  :  λ1 = {-b1[0]:>7.4f}   真值 {TRUE["lambda1"]:.2f}')
print(f'  → Pooled OLS 偏差 {abs(-bp[0]-TRUE["lambda1"])/TRUE["lambda1"]*100:>5.1f}%'
      f'，固定效应偏差 {abs(-b1[0]-TRUE["lambda1"])/TRUE["lambda1"]*100:>5.1f}%')
print('  → 质量禀赋（quality_endow）同时影响退货率与曝光，构成遗漏变量；')
print('    不加固定效应会把它误读为退货率效应，导致系数有偏。')

# ---------- 安慰剂检验 ----------
print('\n' + '-' * 74)
print('安慰剂检验：随机打乱 SKU 标签后重估，系数应不显著')
print('-' * 74)
dp = d.copy()
shuffled = dp['rr_lag1'].values.copy()
np.random.shuffle(shuffled)
dp['rr_placebo'] = shuffled
bp2, sp2, _, _ = twfe_ols(dp, 'dsr', ['rr_placebo'])
t_pl = abs(bp2[0]) / sp2[0] if sp2[0] > 0 else 0
print(f'  安慰剂 λ1 = {-bp2[0]:>7.4f}  SE {sp2[0]:>6.4f}  t = {t_pl:>5.2f}')
print(f'  → {"通过（|t|<1.96，无显著效应）" if t_pl < 1.96 else "未通过，需检查"}')

# ---------- 衰减偏误对照：用带噪的会计口径回归会怎样 ----------
print('\n' + '-' * 74)
print('测量误差导致衰减偏误：潜在倾向 vs 会计口径（本数据集的额外证据）')
print('-' * 74)
d_obs = d.dropna(subset=['rr_obs_lag1', 'rr_obs_lag2'])
b_obs, s_obs, _, _ = twfe_ols(d_obs, 'dsr', ['rr_obs_lag1', 'rr_obs_lag2'])
lam1_obs = abs(b_obs[0])
err_obs = abs(lam1_obs - TRUE['lambda1']) / TRUE['lambda1'] * 100
print(f'  用 return_rate（returns/orders，含二项噪声）:  λ1 = {lam1_obs:.4f}  相对误差 {err_obs:.1f}%')
print(f'  用 return_rate_latent（DGP 倾向，无抽样噪声）:  λ1 = {abs(b1[0]):.4f}  相对误差 {abs(abs(b1[0])-TRUE["lambda1"])/TRUE["lambda1"]*100:.1f}%')
print(f'  → 衰减比例 {(1-lam1_obs/TRUE["lambda1"])*100:.1f}%')
print('  → 结论：低单量周里 1 单退货就构成 100% 退货率，会计口径的测量误差')
print('    会把真实弹性系统性压低。做归因分析应尽可能用"退货率"的分母充足的周，')
print('    或改用订单级退货概率模型（logit），而不是直接回归周度比率。')

# ---------- 等效流量价值 ----------
print('\n' + '-' * 74)
print('等效流量价值换算（把系数翻译成钱）')
print('-' * 74)
est_l1 = abs(b1[0])
est_w1 = b2[0]
# 正确算法：退货率降1pp → DSR 回升 est_l1 分 → 曝光增幅 = est_l1 * est_w1
# 曝光增幅应按弹性换算：Δln(E) = ω1 * ΔDSR，即 E 变为 E * exp(ω1*ΔDSR)
# 【v2.3 修正 —— 单位换算差 100 倍】
# 回归里 rr 是 0-1 小数，λ1 = dDSR/drr = 1.30 的含义是
# "退货率变化 1.0（即 100 个百分点）时 DSR 变 1.30 分"。
# 我们要算的是"降 1 个百分点"，即 Δrr = 0.01，所以必须再乘 0.01。
# 旧代码直接写 delta_dsr = est_l1，把 1pp 当成 1.0 个 rr 单位，
# 结果算出"曝光 +83%、月 GMV +130%"——一个月生意翻倍还多，一眼假。
PP = 0.01                   # 一个百分点
delta_dsr = est_l1 * PP     # 退货率降 1pp 对应的 DSR 回升分
delta_ln_e = est_w1 * delta_dsr
monthly_exp = panel.exposure.sum() / 12
monthly_gmv = panel.gmv.sum() / 12
uv_value = panel.gmv.sum() / panel.visitors.sum()

# 曝光增长 → 访客同比例增长 → GMV 同比例增长（假设 CVR/客单价不变）
gmv_lift = monthly_gmv * (np.exp(delta_ln_e) - 1)
print(f'  估计 λ1 = {est_l1:.4f}  （退货率 +1pp → DSR 下降 {est_l1*PP:.4f} 分）')
print(f'  估计 ω1 = {est_w1:.4f}  （DSR +1 分 → 曝光 +{est_w1*100:.1f}%）')
print(f'  月均曝光 {monthly_exp:,.0f}  |  月均 GMV ¥{monthly_gmv:,.0f}')
print(f'  UV 价值 ¥{uv_value:.2f}/访客  |  CTR {(panel.visitors/panel.exposure).mean():.2%}')
print(f'  → 退货率降 1pp：DSR 回升 {delta_dsr:.4f} 分 → 曝光 +{delta_ln_e*100:.2f}%')
print(f'  → 月等效 GMV 增量 ≈ ¥{gmv_lift:,.0f}   （按 GMV 等比例放大）')
print(f'  → 年化 ≈ ¥{gmv_lift*12:,.0f}')
print(f'  → 占月 GMV 的 {gmv_lift/monthly_gmv*100:.2f}%')

print('\n' + '=' * 74)
print('验证结论')
print('=' * 74)
ok1 = abs(abs(b1[0]) - TRUE['lambda1']) / TRUE['lambda1'] < 0.15
ok2 = abs(b2[0] - TRUE['omega1']) / TRUE['omega1'] < 0.15
print(f'  一级传导可识别: {"是" if ok1 else "否"}')
print(f'  二级传导可识别: {"是" if ok2 else "否"}')
print(f'  综合: {"通过 —— DGP 结构合理，参数回收可用于正式实验" if (ok1 and ok2) else "未通过 —— 需调整 DGP"}')

# ---------- 结果落盘：图表与报告统一读这一份，杜绝手抄错数 ----------
result = {
    'sample_n': int(n1), 'cluster_n': int(g1),
    'lambda1': {'true': TRUE['lambda1'], 'est': float(abs(b1[0])), 'se': float(s1[0])},
    'lambda2': {'true': TRUE['lambda2'], 'est': float(abs(b1[1])), 'se': float(s1[1])},
    'omega1': {'true': TRUE['omega1'], 'est': float(b2[0]), 'se': float(s2[0])},
    'omega2': {'true': TRUE['omega2'], 'est': float(b2[1]), 'se': float(s2[1])},
    'method_compare': {'pooled': float(abs(bp[0])), 'twfe': float(abs(b1[0]))},
    # 【v2.3 新增】衰减偏误对照必须落盘。此前只在终端打印，
    # 图表脚本读不到，结果图5 散点画的是会计口径（斜率≈-0.045）、
    # 图注标的却是潜在口径的 -1.303 —— 图与自己相差 29 倍。
    # 这两组数本身就是本项目最有价值的证据，必须成为正式产物。
    'attenuation': {'latent': float(abs(b1[0])), 'latent_se': float(s1[0]),
                    'accounting': float(lam1_obs),
                    'attenuation_pct': float((1 - lam1_obs / TRUE['lambda1']) * 100)},
    'placebo': {'est': float(abs(bp2[0])), 'se': float(sp2[0]), 't': float(t_pl)},
    'value': {'monthly_gmv': float(monthly_gmv), 'monthly_lift': float(gmv_lift),
              'annual_lift': float(gmv_lift * 12), 'pct_of_gmv': float(gmv_lift / monthly_gmv),
              'uv_value': float(uv_value), 'exposure_lift_pct': float(delta_ln_e * 100)},
    'pass': bool(ok1 and ok2),
}
for k in ('lambda1', 'lambda2', 'omega1', 'omega2'):
    r = result[k]
    r['rel_err'] = abs(r['est'] - r['true']) / r['true']
with open(os.path.join(P, '_verification.json'), 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
print(f'\n结果已写入 data/processed/_verification.json')