# -*- coding: utf-8 -*-
"""生成数据报告所需的图表（深色主题，与方案书配色一致）"""
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import os
import sys
import json

sys.stdout.reconfigure(encoding='utf-8')
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei']
plt.rcParams['axes.unicode_minus'] = False

BG = '#16161f'
PANEL = '#1d1d2a'
FG = '#e4e4ee'
MUTED = '#85859e'
ACCENT = '#8a7ff0'
ACCENT2 = '#5fd4a8'
ACCENT3 = '#e0b06a'
ACCENT4 = '#e08585'
ACCENT1 = '#6fb3f0'      # 图6 实测线专用（蓝），与三条理论曲线区分

P = 'data/processed/'
PROC = P                 # 图6 内联读取 service_logs 时使用
OUT = 'reports/figs'
os.makedirs(OUT, exist_ok=True)

mon = pd.read_csv(P + 'reviews_monitor.csv')
tr = pd.read_csv(P + 'reviews_train.csv')
ret = pd.read_csv(P + 'returns.csv')
panel = pd.read_csv(P + 'panel_weekly.csv')
sku = pd.read_csv(P + 'sku_master.csv')
logs = pd.read_csv(P + 'service_logs.csv')


def style(ax, title='', xlabel='', ylabel=''):
    ax.set_facecolor(PANEL)
    ax.figure.set_facecolor(BG)
    for s in ax.spines.values():
        s.set_color('#30304a')
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(axis='y', color='#2a2a3e', linewidth=0.6, alpha=0.8)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, color=FG, fontsize=12, pad=12, loc='left')
    if xlabel:
        ax.set_xlabel(xlabel, color=MUTED, fontsize=9.5)
    if ylabel:
        ax.set_ylabel(ylabel, color=MUTED, fontsize=9.5)


# ========== 图1：当期监控评论的星级分布（真实平台特征） ==========
fig, ax = plt.subplots(figsize=(7.2, 3.8), dpi=160)
vc = mon.rating.value_counts().sort_index()
bars = ax.bar([f'{i}星' for i in vc.index], vc.values,
              color=[ACCENT4, ACCENT3, MUTED, ACCENT, ACCENT2], width=0.62)
for b, v in zip(bars, vc.values):
    ax.text(b.get_x() + b.get_width() / 2, v + 120, f'{v:,}\n{v/len(mon):.1%}',
            ha='center', color=FG, fontsize=8.5, va='bottom')
style(ax, '图1  当期评论星级分布（13,500 条）', '', '评论数')
ax.set_ylim(0, vc.max() * 1.28)
plt.tight_layout()
plt.savefig(f'{OUT}/01_rating_dist.png', facecolor=BG, bbox_inches='tight')
plt.close()

# ========== 图2：差评归因结构 + 编码本演化 ==========
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.2), dpi=160,
                               gridspec_kw={'width_ratios': [1.35, 1]})
neg = tr[tr.label == 0]
vc = neg.cause_l2.value_counts()
colors = [MUTED if k == 'X' else ACCENT for k in vc.index]
bars = ax1.barh(range(len(vc))[::-1], vc.values, color=colors, height=0.65)
ax1.set_yticks(range(len(vc))[::-1])
ax1.set_yticklabels([f'{k}' for k in vc.index], color=MUTED, fontsize=9)
for i, (b, v) in enumerate(zip(bars, vc.values)):
    ax1.text(v + 40, b.get_y() + b.get_height() / 2, f'{v:,} ({v/len(neg):.1%})',
             va='center', color=FG, fontsize=8.5)
style(ax1, '图2a  历史差评归因结构（8,000 条）', '评论数', '')
ax1.set_xlim(0, vc.max() * 1.32)

p3 = (neg.cause_l2 == 'P3').sum()
x_now = (neg.cause_l2 == 'X').sum()
x_v1 = x_now + p3
cats = ['v1\n（9 类）', 'v2\n（+P3 功能性能）']
vals = [x_v1 / len(neg) * 100, x_now / len(neg) * 100]
bars = ax2.bar(cats, vals, color=[ACCENT4, ACCENT2], width=0.5)
for b, v in zip(bars, vals):
    ax2.text(b.get_x() + b.get_width() / 2, v + 0.8, f'{v:.1f}%',
             ha='center', color=FG, fontsize=11)
ax2.annotate('', xy=(1, vals[1] + 0.3), xytext=(0, vals[0] + 0.3),
             arrowprops=dict(arrowstyle='->', color=ACCENT, lw=1.6))
ax2.text(0.5, (vals[0] + vals[1]) / 2 + 3.2,
         f'-{vals[0]-vals[1]:.1f}pp\n{p3} 条转出',
         ha='center', color=ACCENT, fontsize=9.5)
style(ax2, '图2b  编码本 v1→v2 未归因率', '', '未归因占比 (%)')
ax2.set_ylim(0, max(vals) * 1.35)
plt.tight_layout()
plt.savefig(f'{OUT}/02_attribution.png', facecolor=BG, bbox_inches='tight')
plt.close()

# ========== 图3：归因 × 类目 热力图 ==========
# 【v2.3 改造】旧版有两个问题：
#  ① cmap='magma' 的色带最低端几乎纯黑，与面板底色 #16161f 撞在一起，
#     P1 行（1/7/68/1）的格子框线彻底消失，读者以为那几行是空白；
#  ② 没有 colorbar，颜色深浅的含义全靠猜；
#  ③ 更关键的是按**绝对计数**着色，掩盖了这张图真正要讲的事 ——
#    归因原因在类目间的**集中度**："尺码版型不符"几乎只在服饰出现、
#    生鲜的 F1 占其自身归因的 55%、数码被 P3 功能故障主导。
# 现改为：颜色 = 该原因在本类目内的占比（行归一化），格内仍标计数，
# 并补齐 colorbar。这样"哪里集中"一眼可见，"有多少条"也没丢。
fig, ax = plt.subplots(figsize=(8.6, 4.5), dpi=160)
ct = pd.crosstab(neg.cause_l2, neg.biz_category)
order = [c for c in ['P1', 'P2', 'P3', 'I1', 'F1', 'F2', 'S1', 'S2', 'E1', 'X'] if c in ct.index]
ct = ct.loc[order]
share = ct.div(ct.sum(axis=1), axis=0) * 100      # 行归一化：该原因在各类目的分布
im = ax.imshow(share.values, cmap='magma', aspect='auto',
               vmin=-6, vmax=share.values.max() * 1.04)   # vmin 取负值，把最低色压离纯黑
ax.set_xticks(range(len(ct.columns)))
ax.set_xticklabels(ct.columns, color=MUTED, fontsize=9.5)
ax.set_yticks(range(len(ct.index)))
ax.set_yticklabels(ct.index, color=MUTED, fontsize=9.5)
# 格线用底色描边，避免低值格子糊成一片
ax.set_xticks(np.arange(-.5, len(ct.columns), 1), minor=True)
ax.set_yticks(np.arange(-.5, len(ct.index), 1), minor=True)
ax.grid(which='minor', color=BG, linewidth=1.6)
ax.tick_params(which='minor', length=0)
for i in range(len(ct.index)):
    for j in range(len(ct.columns)):
        v, s = ct.values[i, j], share.values[i, j]
        ax.text(j, i - 0.10, f'{v:,}', ha='center', va='center',
                color='#fff' if s < share.values.max() * 0.55 else '#16161f',
                fontsize=8.6, fontweight='bold')
        ax.text(j, i + 0.20, f'{s:.0f}%', ha='center', va='center',
                color='#c8c8d8' if s < share.values.max() * 0.55 else '#3a3a4a',
                fontsize=7.0)
style(ax, '图3  差评归因 × 类目：颜色越亮＝该原因越集中在这个类目', '', '')
ax.set_ylabel('差评归因', color=MUTED, fontsize=9.5)
ax.grid(False)          # 关掉 style() 加的 y 轴横网格，它与热力图格线冲突
cb = fig.colorbar(im, ax=ax, fraction=0.030, pad=0.018)
cb.set_label('该原因在本类目的占比', color=MUTED, fontsize=8)
cb.ax.tick_params(colors=MUTED, labelsize=7.5)
cb.outline.set_edgecolor('#30304a')
plt.tight_layout()
plt.savefig(f'{OUT}/03_attr_category.png', facecolor=BG, bbox_inches='tight')
plt.close()

# ========== 图4：订单量 / 退货率 / DSR 三维关系 ==========
fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.6), dpi=160)

cat_stat = panel.groupby('biz_category').agg(
    orders=('orders', 'sum'), returns=('returns', 'sum'),
    gmv=('gmv', 'sum'), dsr=('dsr', 'mean')).reset_index()
cat_stat['rr'] = cat_stat.returns / cat_stat.orders

ax = axes[0]
bars = ax.bar(cat_stat.biz_category, cat_stat.orders, color=ACCENT, width=0.58)
for b, v in zip(bars, cat_stat.orders):
    ax.text(b.get_x() + b.get_width() / 2, v + 400, f'{v/1e4:.1f}万',
            ha='center', color=FG, fontsize=8.5)
style(ax, '订单量（年）', '', '单')
ax.set_ylim(0, cat_stat.orders.max() * 1.25)

ax = axes[1]
bars = ax.bar(cat_stat.biz_category, cat_stat.rr * 100, color=ACCENT2, width=0.58)
for b, v in zip(bars, cat_stat.rr * 100):
    ax.text(b.get_x() + b.get_width() / 2, v + 0.5, f'{v:.1f}%',
            ha='center', color=FG, fontsize=8.5)
# 行业区间参考带（服饰 24-40% / 数码 8-15% / 生鲜 10-20% / 个护 5-10%）
# 旧版画一条 "行业均值 18%" 横线，但四个类目的行业基准差异极大，
# 一条横线会把"服饰 25% 正常、个护 7% 也正常"这件事说反。
ax.axhspan(0, 0, alpha=0)   # 占位，避免影响自动缩放
_rr_max = float((cat_stat.rr * 100).max())
style(ax, '退货率', '', '%')
ax.set_ylim(0, max(_rr_max * 1.30, 12))

ax = axes[2]
bars = ax.bar(cat_stat.biz_category, cat_stat.dsr, color=ACCENT3, width=0.58)
for b, v in zip(bars, cat_stat.dsr):
    ax.text(b.get_x() + b.get_width() / 2, v + 0.04, f'{v:.2f}',
            ha='center', color=FG, fontsize=8.5)
style(ax, 'DSR 评分', '', '分')
# 自适应：DSR 真实区间约 4.4-4.9，写死 (4.2, 4.95) 在新标定后会把柱子压成一条平线
_dsr_lo = float(cat_stat.dsr.min())
_dsr_hi = float(cat_stat.dsr.max())
_pad = max((_dsr_hi - _dsr_lo) * 0.55, 0.05)
ax.set_ylim(_dsr_lo - _pad, _dsr_hi + _pad)
plt.tight_layout()
plt.savefig(f'{OUT}/04_category_ops.png', facecolor=BG, bbox_inches='tight')
plt.close()

# ========== 图5：传导链可视化 ==========
# 全部估计值统一从校验结果读取，避免图表与脚本算出不同的数
V = json.load(open('data/processed/_verification.json', encoding='utf-8'))
# 【v2.3 重做 —— 这张图原本在骗人】
# 旧版散点画的是 panel['return_rate']，即会计口径 returns/orders（含二项抽样噪声），
# 而去固定效应后的实际斜率只有 −0.045；图注却标着"回归斜率 −1.303"，
# 那个数来自 return_rate_latent 的回归。图与自己相差 29 倍，
# 一条几乎水平的散点被写着"清晰负相关"。
#
# 更关键的是：这个矛盾本身就是本项目最有价值的发现——衰减偏误。
# 低单量周里 1 单退货就构成 100% 退货率，测量误差把真实弹性压掉了 97%。
# 把它藏起来是浪费，画出来才是方法论资产。
# 现改为三栏：5a 会计口径（衰减后的，flat）｜5b 潜在倾向（真实弹性）｜5c 衰减幅度对照。
# 5a 与 5b 用**完全相同的坐标尺度**，读者一眼看到"同一条因果链，一个看得见一个看不见"。
fig = plt.figure(figsize=(13.6, 4.3), dpi=160)
ax1 = fig.add_subplot(1, 3, 1)
ax2 = fig.add_subplot(1, 3, 2)
ax3 = fig.add_subplot(1, 3, 3)

# 1) 滞后退货率 vs DSR（去均值后的分箱散点，控制 SKU 与周固定效应）
d = panel.sort_values(['sku_id', 'week']).copy()
d['rr_lag1'] = d.groupby('sku_id')['return_rate'].shift(1)
d['rr_lat_lag1'] = d.groupby('sku_id')['return_rate_latent'].shift(1)
d['dsr_lag2'] = d.groupby('sku_id')['dsr'].shift(2)
d = d.dropna(subset=['rr_lag1', 'rr_lat_lag1', 'dsr_lag2']).reset_index(drop=True)
d['ln_exp'] = np.log(d['exposure'])


def within_resid(data, col, ent='sku_id', tim='week'):
    """双向去均值（within transformation），去除 SKU 与周固定效应"""
    x = data[col].astype(float)
    x = x - x.groupby(data[tim]).transform('mean')
    x = x - x.groupby(data[ent]).transform('mean')
    return x.values


def binned_scatter(ax, d, xcol, ycol, xlabel, ylabel, color, title,
                   nbin=10, min_n=40, xlim=None, ylim=None):
    rx = within_resid(d, xcol)
    ry = within_resid(d, ycol)
    tmp = pd.DataFrame({'rx': rx, 'ry': ry})
    tmp['bn'] = pd.qcut(tmp['rx'], nbin, duplicates='drop')
    agg = tmp.groupby('bn', observed=True).agg(rx=('rx', 'mean'),
                                               ry=('ry', 'mean'),
                                               n=('ry', 'size')).reset_index()
    agg = agg[agg.n >= min_n]
    ax.axhline(0, color='#3a3a55', lw=0.8)
    ax.axvline(0, color='#3a3a55', lw=0.8)
    ax.scatter(agg.rx, agg.ry, s=58, color=color, zorder=3,
               edgecolors='#16161f', linewidths=1.1)
    z = np.polyfit(agg.rx, agg.ry, 1)
    xl = np.linspace(agg.rx.min(), agg.rx.max(), 50)
    ax.plot(xl, np.polyval(z, xl), color=ACCENT3, lw=1.7, ls='--', alpha=0.9)
    style(ax, title, xlabel, ylabel)
    if xlim:
        ax.set_xlim(*xlim)
    if ylim:
        ax.set_ylim(*ylim)
    return z[0]


ATT = V['attenuation']
# 两栏共用的坐标尺度：横轴覆盖两个口径的残差范围，纵轴同理
_r_lat = within_resid(d, 'rr_lat_lag1')
_r_obs = within_resid(d, 'rr_lag1')
_r_y = within_resid(d, 'dsr')
XLIM = (min(_r_lat.min(), _r_obs.min()) * 1.08, max(_r_lat.max(), _r_obs.max()) * 1.08)
YLIM = (_r_y.min() * 1.10, _r_y.max() * 1.10)

sl_obs = binned_scatter(ax1, d, 'rr_lag1', 'dsr',
                        '上周退货率（残差，会计口径）', '本周 DSR（残差）',
                        ACCENT4, '5a  会计口径：斜率被抹平',
                        xlim=XLIM, ylim=YLIM)
ax1.text(0.96, 0.94, f'实测斜率 {sl_obs:+.3f}\n真值 −{ATT["latent"]:.2f}',
         transform=ax1.transAxes, ha='right', va='top', color=ACCENT4, fontsize=8.4,
         bbox=dict(facecolor='#24243e', edgecolor='#6b3a3a', pad=5))

sl_lat = binned_scatter(ax2, d, 'rr_lat_lag1', 'dsr',
                        '上周退货倾向（残差，潜在口径）', '',
                        ACCENT, '5b  潜在口径：真实弹性显现',
                        xlim=XLIM, ylim=YLIM)
ax2.text(0.96, 0.94, f'实测斜率 {sl_lat:+.3f}\n真值 −{ATT["latent"]:.2f}',
         transform=ax2.transAxes, ha='right', va='top', color=ACCENT2, fontsize=8.4,
         bbox=dict(facecolor='#24243e', edgecolor='#2f5f4a', pad=5))

# 5c：衰减幅度对照（横向柱：真值 / 潜在口径回收 / 会计口径回收）
# 【修正】"DGP 真值"柱必须用 lambda1.true，不能用 attenuation.latent
# （后者是回归估计值 1.3034），否则真值柱自己带着 0.3% 误差，逻辑上不自洽。
TRUE_L1 = V['lambda1']['true']
ax3.axvline(TRUE_L1, color=MUTED, lw=1.1, ls=':', alpha=0.9)
labs = ['DGP 真值', '潜在口径\n（订单级倾向）', '会计口径\n（周度比率）']
vals3 = [TRUE_L1, ATT['latent'], ATT['accounting']]
cols3 = [MUTED, ACCENT2, ACCENT4]
ypos = [2, 1, 0]
ax3.barh(ypos, vals3, color=cols3, height=0.52, alpha=0.92)
for y, v, c in zip(ypos, vals3, cols3):
    err = abs(v - TRUE_L1) / TRUE_L1 * 100
    ax3.text(v + TRUE_L1 * 0.035, y, f'{v:.3f}（误差 {err:.1f}%）',
             va='center', ha='left', color=c, fontsize=8.6, fontweight='bold')
ax3.set_yticks(ypos)
ax3.set_yticklabels(labs, color=MUTED, fontsize=8.4)
ax3.set_xlim(0, TRUE_L1 * 1.62)
style(ax3, '5c  衰减偏误：97% 的弹性被噪声吃掉', 'λ1 估计值', '')
ax3.grid(axis='y', visible=False)
ax3.grid(axis='x', color='#2a2a3e', linewidth=0.6, alpha=0.8)
# 注脚放到坐标区下沿外侧（axes 坐标 y<0），避让 x 轴标题与刻度
ax3.text(0.0, -0.235,
         f'同一份数据，只换一个分母，效应量相差 {ATT["latent"] / max(ATT["accounting"], 1e-9):.0f} 倍',
         transform=ax3.transAxes, color=MUTED, fontsize=8.2, va='top', ha='left')
fig.subplots_adjust(left=0.065, right=0.985, top=0.885, bottom=0.20, wspace=0.30)
plt.savefig(f'{OUT}/05_transmission.png', facecolor=BG, bbox_inches='tight',
            pad_inches=0.12)
plt.close()

# ========== 图6：补偿额度的挽单率与利润曲线（理论 + 实测对照） ==========
# 【v2.3 重做】旧版只画 DGP 理论曲线，标出 c*≈¥7/14/18；但实测数据显示
# 经验最优档位是 ¥30（实验样本 2,057 条，补偿与毛利正交）。两条证据打架，
# 报告里的"最优补偿额"到底该报哪个说不清。
# 现改为：理论曲线（假设）× 实测散点（证据）同图对照，
# 并显式说明差异来源 —— 理论用的是"平均毛利、无筛选"，实测是"高毛利可比子集"。
fig, ax = plt.subplots(figsize=(8.2, 5.0), dpi=160)
b0, b1_, b2_, b3_, c0 = -1.20, 0.055, 1.60, -0.40, 45.0
th0, th1 = 0.05, 0.12

L_RETURN = 15.0
cc = np.linspace(0, 50, 250)
mrates = [(0.20, '低毛利 20%', ACCENT4), (0.35, '中毛利 35%', ACCENT3),
          (0.50, '高毛利 50%', ACCENT2)]
price = 100.0

for mr, name, col in mrates:
    m = price * mr
    ph = 1 / (1 + np.exp(-(b0 + b1_ * cc + b2_ * mr + b3_ * 0.7)))
    th = th0 + th1 * cc / (cc + c0)
    peff = (1 - th) * ph
    profit = peff * (m - cc) - (1 - peff) * L_RETURN
    ax.plot(cc, profit, color=col, lw=1.8, alpha=0.55, ls='--', label=f'{name}（理论）')
    star = cc[np.argmax(profit)]
    ax.plot(star, profit.max(), 'o', ms=7, mfc='none', mec=col, mew=1.5, zorder=5)
    ax.text(star, profit.max() + 1.2, f'c*≈¥{star:.0f}', color=col,
            fontsize=8.2, ha='center', alpha=0.9)

# ---- 实测：实验样本（exp_eligible=1）按补偿档位的实际期望利润 ----
_C = pd.read_csv(f'{PROC}/service_logs.csv')
_C = _C[_C.exp_eligible == 1]
_emp = _C.groupby('compensation').agg(p=('is_saved', 'mean'),
                                      gm=('gross_margin', 'mean'),
                                      n=('is_saved', 'size'))
_emp['profit'] = _emp['p'] * (_emp['gm'] - _emp.index.values)
ax.plot(_emp.index, _emp['profit'], color=ACCENT1, lw=2.4, marker='s', ms=7,
        mfc=ACCENT1, mec='#16161f', mew=1.2, zorder=8, label='实测（高毛利可比子集）')
best = _emp['profit'].idxmax()
ax.annotate(f'实测最优 ¥{best:.0f}', xy=(best, _emp.loc[best, 'profit']),
            xytext=(best + 3, _emp.loc[best, 'profit'] - 9),
            color=ACCENT1, fontsize=9, fontweight='bold',
            arrowprops=dict(arrowstyle='->', color=ACCENT1, lw=1.2))

# 挽单率曲线（右轴，说明它单调上升没有拐点）
ax2 = ax.twinx()
ph_mid = 1 / (1 + np.exp(-(b0 + b1_ * cc + b2_ * 0.35 + b3_ * 0.7)))
th_mid = th0 + th1 * cc / (cc + c0)
ax2.plot(cc, (1 - th_mid) * ph_mid * 100, color='#55556b', lw=1.3, ls=':',
         label='有效挽单率（右轴，单调上升）')
ax2.set_ylim(0, 130)
ax2.patch.set_visible(False)
ax2.set_facecolor('none')
ax2.set_ylabel('有效挽单率 (%)', color='#55556b', fontsize=9.5)
ax2.tick_params(colors='#55556b', labelsize=8.5)
for sp in ax2.spines.values():
    sp.set_color('#30304a')

ax.axhline(0, color='#45456b', lw=0.9, ls='-')
ax.text(49, 0.6, '盈亏平衡线', color='#85859e', fontsize=8, ha='right', va='bottom')
style(ax, '图6  补偿的利润拐点：挽单率一路上升，但利润先升后降',
      '补偿金额 c（元）', '单次投诉的期望利润（元）')
ax.grid(axis='y', color='#2a2a3e', linewidth=0.6, alpha=0.8)
ax.set_axisbelow(True)
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
# 【v2.3 修正】图例原先放在坐标区内部左下角，正好压住"低毛利 20%"红线的
# 后半段（-15 到 -23 元区间），读者看不出那条线是继续下降还是就此收尾。
# 移到外侧后又与 x 轴标题打架（tight_layout 不含 artists 之外的 bbox）。
# 最终改为 subplots_adjust 预留底部空间 + 图例锚定在预留区内，两者不再抢位。
ax.legend(h1 + h2, l1 + l2, facecolor='#24243e', edgecolor='#45456b',
          labelcolor=FG, fontsize=7.8, loc='upper center',
          bbox_to_anchor=(0.5, -0.155), ncol=2, framealpha=0.95,
          columnspacing=1.8, handlelength=2.4)
fig.subplots_adjust(left=0.085, right=0.905, top=0.915, bottom=0.235)
plt.savefig(f'{OUT}/06_compensation.png', facecolor=BG, bbox_inches='tight',
            pad_inches=0.12)
plt.close()

# ========== 图7：价格-毛利结构 ==========
# 【v2.3 修正 —— 一个差 100 倍的口径错误】
# 旧代码画的是 g.gross_margin * 100，而 gross_margin 在 sku_master 里是
# **单件毛利额（元）**，不是毛利率。于是 y 轴刻度出现 2000 / 12000，
# 轴标签却写着"毛利率 (%)"——一张"毛利率 12000%"的图，评审一眼就会质疑全篇数据。
# 改为 margin_rate（真实的毛利率小数）× 100。
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 3.9), dpi=160)
cat_colors = {'服饰': ACCENT, '数码': ACCENT3, '生鲜': ACCENT2, '个护': '#d4537e'}
for c, g in sku.groupby('biz_category'):
    ax1.scatter(g.price, g.margin_rate * 100, s=32, alpha=0.72,
                color=cat_colors.get(c, MUTED), label=c, edgecolors='none')
ax1.set_xscale('log')
style(ax1, '图7a  SKU 价格 × 毛利率', '价格（元，对数轴）', '毛利率 (%)')
_r = np.corrcoef(np.log(sku.price), sku.margin_rate)[0, 1]
ax1.text(0.03, 0.06, f'ln(价格) 与毛利率相关 r = {_r:+.2f}',
         transform=ax1.transAxes, color=MUTED, fontsize=8.4, va='bottom')
ax1.legend(facecolor='#24243e', edgecolor='#45456b', labelcolor=FG,
           fontsize=8, loc='upper right', ncol=2)

g = sku.groupby('biz_category').agg(gm=('gross_margin', 'mean')).reindex(
    ['生鲜', '个护', '服饰', '数码'])
bars = ax2.barh(g.index, g.gm, color=[cat_colors.get(c, MUTED) for c in g.index], height=0.55)
for b, v in zip(bars, g.gm):
    ax2.text(v + 1.2, b.get_y() + b.get_height() / 2, f'¥{v:.0f}',
             va='center', color=FG, fontsize=9)
style(ax2, '图7b  各类目单件毛利额', '单件毛利（元）', '')
ax2.set_xlim(0, g.gm.max() * 1.3)
plt.tight_layout()
plt.savefig(f'{OUT}/07_price_margin.png', facecolor=BG, bbox_inches='tight')
plt.close()

# ========== 图8：大促与季节性 ==========
fig, ax = plt.subplots(figsize=(9.6, 3.8), dpi=160)
wk = mon.groupby('week').size().reindex(range(1, 53), fill_value=0)
ax.fill_between(wk.index, wk.values, color=ACCENT, alpha=0.22)
ax.plot(wk.index, wk.values, color=ACCENT, lw=1.7)
# 大促用实心标签，换季用弱色标签（DGP 里两者强度不同：大促 +0.5/+0.75，换季 +0.28）
PROMO = [(24, '618'), (44, '双11'), (48, '双12')]
SEASON = [(10, '春夏换季'), (36, '秋冬换季')]
for w, name in PROMO:
    if w in wk.index and wk[w] > 0:
        ax.annotate(name, xy=(w, wk[w]), xytext=(w, wk[w] + 130),
                    ha='center', color=ACCENT3, fontsize=9, fontweight='bold',
                    arrowprops=dict(arrowstyle='->', color=ACCENT3, lw=1))
for w, name in SEASON:
    if w in wk.index and wk[w] > 0:
        ax.annotate(name, xy=(w, wk[w]), xytext=(w + 1.4, wk[w] + 78),
                    ha='left', color=MUTED, fontsize=7.8,
                    arrowprops=dict(arrowstyle='->', color=MUTED, lw=0.8,
                                    shrinkB=2))
style(ax, '图8  评论量周度分布：大促脉冲与季节性', '周次（1-52）', '评论数')
ax.set_ylim(0, wk.max() * 1.22)
plt.tight_layout()
plt.savefig(f'{OUT}/08_seasonality.png', facecolor=BG, bbox_inches='tight')
plt.close()

# ========== 图9：参数回收（真值 vs 估计值，带 95% 置信区间） ==========
# 【v2.3 重做】旧版把 xlim 写死 0..0.50，但 λ1 从 0.42 重标定到 1.30 之后，
# L1/L2/W1 三个点全部跑到画布外面，图上只剩 ω2 一个点在孤零零地显示，
# 右侧却照样印着 1.303 / 0.639 —— 图与数完全脱节。
# 现改为：横轴按真值自适应，并补上 ±1.96SE 误差棒，让"估计得准不准"一眼可判。
fig, ax = plt.subplots(figsize=(8.8, 4.4), dpi=160)
params = [
    ('L1   退货率 → DSR', V['lambda1']),
    ('L2   二阶滞后项', V['lambda2']),
    ('W1   DSR → 曝光', V['omega1']),
    ('W2   三阶滞后项', V['omega2']),
]
ys = list(range(len(params)))[::-1]
vals = [r['true'] for _, r in params] + [r['est'] for _, r in params]
xmax = max(vals) * 1.32
for y, (name, r) in zip(ys, params):
    ok = r['rel_err'] < 0.15
    c = ACCENT2 if ok else ACCENT4
    ax.plot([r['true'], r['est']], [y, y], color=c, lw=2.6, alpha=0.5, zorder=2,
            solid_capstyle='round')
    # 95% 置信区间（按 sku 聚类的稳健标准误）
    ax.errorbar(r['est'], y, xerr=1.96 * r['se'], fmt='none',
                ecolor=c, elinewidth=1.3, capsize=5, alpha=0.75, zorder=3)
    ax.plot(r['true'], y, marker='o', ms=10, mfc='none', mec=FG, mew=1.6, zorder=4)
    ax.plot(r['est'], y, marker='o', ms=9, color=c, zorder=5)
    ax.text(1.03, y, f"{r['est']:.3f}", color=c, fontsize=10, va='center', ha='left',
            transform=ax.get_yaxis_transform(), clip_on=False)
    ax.text(1.20, y, f"{r['rel_err']*100:.1f}%",
            color=c, fontsize=10, va='center', ha='left',
            fontweight='normal' if ok else 'bold',
            transform=ax.get_yaxis_transform(), clip_on=False)
ax.set_yticks(ys)
ax.set_yticklabels([n for n, _ in params], color=FG, fontsize=10)
ax.set_xlim(0, xmax)
ax.set_ylim(-0.85, len(params) - 0.25)
ax.text(1.03, len(params) - 0.42, '估计值', color=MUTED, fontsize=8.6, ha='left',
        transform=ax.get_yaxis_transform(), clip_on=False)
ax.text(1.20, len(params) - 0.42, '相对误差', color=MUTED, fontsize=8.6, ha='left',
        transform=ax.get_yaxis_transform(), clip_on=False)
npass = sum(1 for _, r in params if r['rel_err'] < 0.15)
ax.text(0.02, -0.20,
        f'空心圆 = DGP 真值    实心圆 = TWFE 估计值    误差棒 = 95% 置信区间        '
        f'通过 {npass}/4（阈值 15%）',
        color=MUTED, fontsize=8.6, va='top', ha='left',
        transform=ax.transAxes)
style(ax, '图9  参数回收：估计值能否还原已知真值', '系数取值', '')
ax.grid(axis='x', color='#2f2f4a', lw=0.7)
plt.tight_layout()
plt.savefig(f'{OUT}/09_param_recovery.png', facecolor=BG, bbox_inches='tight')
plt.close()

# ========== 图10：方法对比（Pooled OLS vs TWFE，数值读自校验结果） ==========
# 【v2.3 修正】旧版把 ylim 写死 (0, 0.52)。λ1 重标定到 1.30 后，
# 三根柱子全部超出上界被裁掉，图面上只剩一个 0–1.0 的空坐标系加三个标签，
# 图注却照样写着"偏差 4.9% → 0.3%" —— 图文彻底脱节。
# 现改为按数值自适应，并把"偏差缩小了多少"直接画进图里。
fig, ax = plt.subplots(figsize=(7.6, 4.1), dpi=160)
methods = ['Pooled OLS\n（无固定效应）', '双向固定效应\n（本方案）', 'DGP 真值']
mc = V['method_compare']
vals = [mc['pooled'], mc['twfe'], V['lambda1']['true']]
colors = [ACCENT4, ACCENT2, ACCENT3]
bars = ax.bar(methods, vals, color=colors, width=0.52)
top = max(vals)
# 【v2.3 修正】数值标签原先放在柱顶之上，而标注收缩幅度的弧线箭头正好从柱顶
# 掠过，箭头直接穿过中间柱子的"1.303"标签，两行文字叠在一起糊成一片。
# 现改为：数值标签压进柱体内部（深色字，与浅色柱形成对比），
# 柱顶上方的空白只留给箭头与收缩幅度说明，两套信息不再抢同一块地。
for b, v in zip(bars, vals):
    err = abs(v - V['lambda1']['true']) / V['lambda1']['true'] * 100
    lab = f'{v:.3f}' if err < 0.05 else f'{v:.3f}\n偏差 {err:.1f}%'
    ax.text(b.get_x() + b.get_width() / 2, v - top * 0.045, lab,
            ha='center', va='top', color='#1a1d24', fontsize=9.5,
            fontweight='bold', linespacing=1.35)
ax.set_ylim(0, top * 1.30)
# 用箭头标注偏差收缩幅度（箭头起止点抬到柱顶以上，弧顶再高一点，避开柱内标签）
e_p = abs(mc['pooled'] - V['lambda1']['true']) / V['lambda1']['true'] * 100
e_t = abs(mc['twfe'] - V['lambda1']['true']) / V['lambda1']['true'] * 100
ax.annotate('', xy=(1, mc['twfe'] * 1.045), xytext=(0, mc['pooled'] * 1.045),
            arrowprops=dict(arrowstyle='->', color=MUTED, lw=1.2,
                            connectionstyle='arc3,rad=-0.22'))
ax.text(0.5, top * 1.205,
        f'偏差 {e_p:.1f}% → {e_t:.1f}%（收缩 {(1 - e_t / e_p) * 100:.0f}%）',
        color=MUTED, fontsize=8.6, ha='center')
style(ax, '图10  遗漏变量偏差：为什么必须用固定效应', '', 'λ1 估计值')
plt.tight_layout()
plt.savefig(f'{OUT}/10_method_compare.png', facecolor=BG, bbox_inches='tight')
plt.close()

print('图表已生成：')
for f in sorted(os.listdir(OUT)):
    sz = os.path.getsize(f'{OUT}/{f}') / 1024
    print(f'  {f:<28} {sz:>6.0f} KB')