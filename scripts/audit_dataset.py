# -*- coding: utf-8 -*-
"""数据集审计脚本：逐项验证分布是否符合真实电商业务规律"""
import pandas as pd
import numpy as np
import sys
import json

sys.stdout.reconfigure(encoding='utf-8')
pd.set_option('display.width', 230)
pd.set_option('display.max_columns', 40)

P = 'data/processed/'
mon = pd.read_csv(P + 'reviews_monitor.csv')
tr = pd.read_csv(P + 'reviews_train.csv')
ret = pd.read_csv(P + 'returns.csv')
logs = pd.read_csv(P + 'service_logs.csv')
panel = pd.read_csv(P + 'panel_weekly.csv')
sku = pd.read_csv(P + 'sku_master.csv')
gold = pd.read_csv(P + 'gold_standard_200.csv')

print('=' * 78)
print('数据集审计报告'.center(70))
print('=' * 78)

print('\n【1】数据规模')
for name, n in [('当期评论监控', len(mon)), ('历史差评训练集', len(tr)),
                ('退货明细', len(ret)), ('客服记录', len(logs)),
                ('经营面板', len(panel)), ('SKU 主数据', len(sku)),
                ('金标准标注集', len(gold))]:
    print(f'  {name:<16} {n:>9,}')

print('\n【2】当期评论监控 — 平台真实分布校验')
print(f'  评论总数        {len(mon):>9,}')
print(f'  差评率          {(mon.label==0).mean():>9.2%}   行业区间 3%–8%')
print(f'  好评率          {(mon.label==1).mean():>9.2%}   行业区间 85%–95%')
print(f'  星级分布:')
for k, v in mon.rating.value_counts().sort_index().items():
    print(f'    {k} 星          {v:>9,}  ({v/len(mon):>5.1%})')

print('\n【3】历史训练集 — 归因结构')
neg = tr[tr.label == 0]
print(f'  差评总数        {len(neg):>9,}')
print(f'  归因覆盖结构:')
for k, v in neg.cause_l2.value_counts().items():
    pct = v / len(neg)
    flag = '  ← 未归因' if k == 'X' else ''
    print(f'    {k:<4} {v:>7,}  {pct:>6.1%}{flag}')

print('\n【4】编码本演化证据 — v1 vs v2')
print('  v1（9 类）新增 P3 功能性能后，未归因率变化:')
# 模拟 v1 无 P3 的情况：把 P3 样本算回 X
p3_n = (neg.cause_l2 == 'P3').sum()
x_n = (neg.cause_l2 == 'X').sum()
v1_x = x_n + p3_n
print(f'    v1 未归因    {v1_x:>7,}  {v1_x/len(neg):>6.1%}')
print(f'    v2 未归因    {x_n:>7,}  {x_n/len(neg):>6.1%}')
print(f'    改善         {p3_n:>7,} 条转出  （P3 功能性能 {p3_n/len(neg):>5.1%}）')

print('\n【5】归因 × 类目 交叉（差评）')
ct = pd.crosstab(neg.cause_l2, neg.biz_category)
print(ct.to_string())

print('\n【6】退货率校验 — 订单层面')
rr = panel.returns.sum() / panel.orders.sum()
print(f'  店铺整体退货率  {rr:>9.2%}   行业区间 15%–25%')
print(f'  年订单          {panel.orders.sum():>9,}')
print(f'  年退货          {panel.returns.sum():>9,}')
print(f'  按类目:')
for c, g in panel.groupby('biz_category'):
    print(f'    {c}  {g.returns.sum()/g.orders.sum():>7.2%}  订单 {g.orders.sum():>7,}')

print('\n【7】退货原因分布（真实业务特征）')
print(ret.return_reason_text.value_counts(normalize=True).round(3).to_string())

print('\n【8】面板数据 — 传导链可识别性')
print(f'  行数            {len(panel):>9,}')
print(f'  SKU 数          {panel.sku_id.nunique():>9,}')
print(f'  周数            {panel.week.nunique():>9,}')
print(f'  退货率均值      {panel.return_rate.mean():>9.2%}')
print(f'  DSR 均值        {panel.dsr.mean():>9.3f}')
print(f'  曝光中位数      {panel.exposure.median():>9,.0f}')
print(f'  相关系数（退货率 vs 次周DSR）:')
p2 = panel.sort_values(['sku_id', 'week']).copy()
p2['dsr_next'] = p2.groupby('sku_id')['dsr'].shift(-1)
print(f'    corr(return_rate, dsr_next) = {p2[["return_rate","dsr_next"]].corr().iloc[0,1]:>7.3f}  （预期为负）')
p2['dsr_lag2'] = p2.groupby('sku_id')['dsr'].shift(2)
print(f'    corr(dsr_lag2, ln_exposure)  = {p2[["dsr_lag2","exposure"]].assign(ln_exposure=np.log(p2.exposure)).corr().iloc[0,1]:>7.3f}  （预期为正）')

print('\n【9】客服记录 — 补偿与挽单')
print(f'  记录数          {len(logs):>9,}')
print(f'  挽单率          {logs.is_saved.mean():>9.2%}')
print(f'  补偿额分布:')
print(logs.compensation.describe().round(2).to_string())
print(f'  挽单率 by 补偿档位:')
logs['bucket'] = pd.cut(logs.compensation, [-1, 0, 10, 20, 30, 60],
                        labels=['0元', '1-10元', '11-20元', '21-30元', '30元+'])
print(logs.groupby('bucket', observed=True).is_saved.agg(['mean', 'count']).round(3).to_string())

print('\n【10】SKU 与价格 — 业务合理性')
print(f'  SKU 数          {len(sku):>9,}')
print(f'  价格区间        ¥{sku.price.min():>8,.1f} – ¥{sku.price.max():,.1f}')
print(f'  价格中位数      ¥{sku.price.median():>8,.1f}')
print(f'  毛利区间        {sku.margin_rate.min():>9.1%} – {sku.margin_rate.max():.1%}')
print(f'  按类目:')
for c, g in sku.groupby('biz_category'):
    print(f'    {c:<4} SKU {len(g):>3}  价格中位 ¥{g.price.median():>7,.0f}  '
          f'毛利 {g.margin_rate.mean():>5.1%}  单件毛利 ¥{g.gross_margin.mean():>6,.0f}')

print('\n【11】单一 SKU 动销合理性')
sku_orders = panel.groupby('sku_id').orders.sum()
print(f'  周均订单  中位 {sku_orders.median()/panel.week.nunique():>6.1f} 单')
print(f'            均值 {sku_orders.mean()/panel.week.nunique():>6.1f} 单')
print(f'            最大 {sku_orders.max()/panel.week.nunique():>6.1f} 单（周）')

print('\n【12】文本质量')
print(f'  平均长度        {mon.review_len.mean():>9.0f} 字')
print(f'  长度中位数      {mon.review_len.median():>9.0f} 字')
print(f'  最短/最长       {mon.review_len.min():>9.0f} / {mon.review_len.max():.0f}')
print(f'  含图比例        {mon.has_image.mean():>9.1%}')
print(f'  重复文本        {mon.review.duplicated().sum():>9,} 条')

print('\n【13】金标准标注集')
print(f'  样本数          {len(gold):>9,}')
print(f'  自动标签分布:')
print(gold.auto_label.value_counts().to_string())

print('\n【14】时间分布 — 大促与季节性')
wk = mon.groupby('week').size()
print(f'  评论量峰值周: {wk.nlargest(5).index.tolist()}')
print(f'  峰值周评论量: {wk.nlargest(5).tolist()}')
print(f'  谷值周评论量: {wk.nsmallest(3).tolist()}   （预期 24/44 周为大促峰值）')

print('\n【15】参数真值文件')
with open(P + 'dgp_params.json', encoding='utf-8') as f:
    p = json.load(f)
print('  传导系数真值:', p['transmission'])
print('  补偿模型真值:', p['compensation'])
print('  业务规模:', p['business_scale'])

print('\n' + '=' * 78)
print('审计完成')
print('=' * 78)