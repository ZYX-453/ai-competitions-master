# -*- coding: utf-8 -*-
"""报告数字全量核对（第十一轮审计）。

来历：数据重建过 3 次，每次都会有些表格里的数字忘了跟着改。
前几轮一直是"发现一处改一处"，治标不治本。这个脚本换了个思路：
把报告里所有可核验的关键指标，逐条从数据现场重算并比对，
一次性暴露全部不一致，而不是靠人眼一行行看。

判据分三档：
  P0 = 数字直接矛盾（同一指标两个值），必须修
  P1 = 与数据不符但可能有口径解释，需确认
  -- = 报告未提及（不算错）
"""
import pandas as pd, numpy as np, json, re, sys
sys.stdout.reconfigure(encoding='utf-8')

P = 'data/processed/'
mon = pd.read_csv(P + 'reviews_monitor.csv')
tr = pd.read_csv(P + 'reviews_train.csv')
ret = pd.read_csv(P + 'returns.csv')
pan = pd.read_csv(P + 'panel_weekly.csv')
sku = pd.read_csv(P + 'sku_master.csv')
log = pd.read_csv(P + 'service_logs.csv')
gs = pd.read_csv(P + 'gold_standard_200.csv')
V = json.load(open(P + '_verification.json', encoding='utf-8'))

html = open('reports/dataset-report.html', encoding='utf-8').read()
txt = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
txt = re.sub(r'<[^>]+>', ' ', txt)
txt = re.sub(r'\s+', ' ', txt)

fails = []
def chk(name, real_str, must_in_report=True, level='P1'):
    """real_str 是从数据算出的值；检查它是否出现在报告里"""
    if real_str in txt:
        print(f'  [OK]   {name}: {real_str}')
        return True
    if must_in_report:
        print(f'  [!!{level}] {name}: 重算为 {real_str}，但报告里找不到')
        fails.append((level, name, real_str))
    else:
        print(f'  [--]   {name}: 重算为 {real_str}（报告未引用，不计）')
    return False

# 【怎么区分"写错了"和"在复述历史错误"】
# 报告第十章（缺陷记录）和告警框本来就要写出"初版算成了多少"，
# 那些数字留在文中是**正确**的，甚至是加分项（证明你查过）。
# 所以不能用"某个数字出现了就算错"，也不能靠关键词猜 ——
# 最稳的办法是**按章节结构**判断：先定位每段内容的归属，
# 只有落在"正文结论区"的旧值才算错。
def _section_ranges(html):
    """返回 [(start, end, 是否历史区)]，按字符偏移切分全文"""
    marks = [
        ('<h2>十、数据质量', '<h2>十一、'),      # 缺陷记录表：历史区
        ('<h2>十三、', '</body>'),               # 已知局限：历史区
    ]
    hist = []
    for a, b in marks:
        if a in html:
            i = html.index(a)
            j = html.index(b, i) if b in html[i:] else len(html)
            hist.append((i, j))
    # 告警框 / 高亮卡片里的"曾经错了"也属历史区
    for m in re.finditer(r'<div class="(?:alert[^"]*|card hl)"', html):
        seg_end = html.find('</div>', m.start())
        if seg_end > 0:
            hist.append((m.start(), seg_end))
    return hist

_HIST = _section_ranges(html)

def _is_historical(html_pos):
    return any(a <= html_pos < b for a, b in _HIST)

def chk_absent(name, bad_strs):
    """检查报告是否把**过期旧值当成当前结论**在用。

    落在缺陷记录 / 告警框里的旧值不算错 —— 那是历史复述。
    """
    real_bad = []
    for b in bad_strs:
        for m in re.finditer(re.escape(b), html):
            if not _is_historical(m.start()):
                seg = html[max(0, m.start() - 80):m.start() + 50]
                seg = re.sub(r'<[^>]+>', ' ', seg)
                real_bad.append((b, re.sub(r'\s+', ' ', seg)))
                break
    if real_bad:
        print(f'  [!!P0] {name}: 旧值出现在正文结论区 {real_bad}')
        fails.append(('P0', name, real_bad))
    else:
        print(f'  [OK]   {name}: 旧值仅出现在缺陷记录/告警框中（历史复述）')

print('=' * 76)
print('A. 业务规模（重建后最容易被忘掉的一类）')
print('=' * 76)
chk('年订单量', f'{pan.orders.sum():,}')
chk('年 GMV（万）', f'¥{pan.gmv.sum()/1e4:.1f} 万')
chk('客单价', f'¥{pan.gmv.sum()/pan.orders.sum():.1f}')
chk('SKU 总数', f'{len(sku)} 个')
chk('平均毛利率', f'{pan.gross_profit.sum()/pan.gmv.sum()*100:.1f}%')
chk('价格区间下限', f'¥{sku.price.min():.1f}')
chk('价格区间上限', f'¥{sku.price.max():.1f}')
chk_absent('过期的订单/GMV 组合', ['68,503', '609.8', '¥89.0', '1,169.9'])

print()
print('=' * 76)
print('B. 数据规模')
print('=' * 76)
chk('监控评论', f'{len(mon):,}')
chk('训练评论', f'{len(tr):,}')
chk('面板行数', f'{len(pan):,}')
chk('退货记录', f'{len(ret):,}')
chk('客服工单', f'{len(log):,}')
chk('金标准', f'{len(gs)}')
chk('期中下架 SKU 数', f'{(sku.end_week < 52).sum()} 个')

print()
print('=' * 76)
print('C. 核心比率')
print('=' * 76)
chk('整体退货率', f'{len(ret)/pan.orders.sum()*100:.2f}%')
chk('1–2 星占比', f'{(mon.rating<=2).mean()*100:.2f}%')
chk('3 星占比', f'{(mon.rating==3).mean()*100:.2f}%')
chk('4–5 星占比', f'{(mon.rating>=4).mean()*100:.2f}%')
chk('未归因率', f'{(tr.cause_l2=="X").mean()*100:.2f}%')
chk('挽单率', f'{log.is_saved.mean()*100:.1f}%')
chk('平均补偿', f'¥{log.compensation.mean():.2f}')
chk('实验可比样本', f'{(log.exp_eligible==1).sum():,}')

print()
print('=' * 76)
print('D. 退货损失（本轮口径改过，重点核对）')
print('=' * 76)
chk('单均退货损失', f'¥{ret.loss_amount.mean():.2f}')
chk('全年退货损失（万）', f'¥{ret.loss_amount.sum()/1e4:.1f} 万')
chk('占毛利', f'{ret.loss_amount.sum()/pan.gross_profit.sum()*100:.1f}%')
chk('可二次销售率', f'{ret.is_resellable.mean()*100:.1f}%')
chk_absent('按零售价计提的旧值', ['79.39', '74.8 万', '36.5%'])

print()
print('=' * 76)
print('E. 传导参数')
print('=' * 76)
for k, lab in [('lambda1', 'λ1'), ('lambda2', 'λ2'), ('omega1', 'ω1'), ('omega2', 'ω2')]:
    chk(f'{lab} 估计值', f'{V[k]["est"]:.3f}')
chk('衰减后会计口径', f'{V["attenuation"]["accounting"]:.3f}')
chk('衰减比例', f'{V["attenuation"]["attenuation_pct"]:.1f}%')

print()
print('=' * 76)
print('F. 类目一致性（退货率排序必须与行业事实同序）')
print('=' * 76)
cat = ret.groupby('biz_category').size() / pan.groupby('biz_category').orders.sum()
order = cat.sort_values(ascending=False)
print('  实测排序:', ' > '.join(f'{c} {v*100:.2f}%' for c, v in order.items()))
expect = ['服饰', '生鲜', '数码', '个护']
if list(order.index) == expect:
    print('  [OK]   排序符合行业事实：服饰 > 生鲜 > 数码 > 个护')
else:
    print(f'  [!!P0] 排序异常，期望 {expect}，实际 {list(order.index)}')
    fails.append(('P0', '类目退货率排序', list(order.index)))
for c in expect:
    chk(f'{c} 退货率', f'{cat[c]*100:.2f}%', must_in_report=False)

print()
print('=' * 76)
if fails:
    print(f'结论：{len(fails)} 项待修')
    for lv, n, v in fails:
        print(f'  [{lv}] {n} → {v}')
    sys.exit(1)
print('结论：报告全部关键数字与数据一致')
