# -*- coding: utf-8 -*-
"""数据字典与数据文件的自动对齐检查（可复跑）。

为什么需要这个脚本：字典此前是手工维护的，本轮新增 cogs / loss_amount /
loss_basis 三个字段后字典没跟上，直到审计才被发现。手工维护的元数据
必然会腐化，所以这里把"字典 == 文件实际列"变成一条可执行断言。
"""
import pandas as pd, os, sys, json
sys.stdout.reconfigure(encoding='utf-8')

P = 'data/processed/'
FILES = ['reviews_monitor.csv', 'reviews_train.csv', 'panel_weekly.csv',
         'returns.csv', 'service_logs.csv', 'sku_master.csv',
         'gold_standard_200.csv']

d = pd.read_csv(P + '_data_dictionary.csv')
issues = []
print('=' * 74)
print('数据字典对齐检查')
print('=' * 74)

for f in FILES:
    cols = list(pd.read_csv(P + f, nrows=0).columns)
    # 字典里 table 字段可能写多个文件（如合并的评论表）
    sub = d[d.table.str.contains(f, regex=False, na=False)]
    documented = set(sub.field)
    actual = set(cols)
    miss = sorted(actual - documented)
    extra = sorted(documented - actual)
    if miss or extra:
        issues.append((f, miss, extra))
        print(f'  [!!] {f}: 未记录 {miss} / 多余 {extra}')
    else:
        print(f'  [OK] {f:26s} {len(actual):2d} 个字段，字典完全覆盖')

# JSON 键也要覆盖
for jf in ['_verification.json', 'dgp_params.json']:
    if not os.path.exists(P + jf):
        continue
    keys = set(json.load(open(P + jf, encoding='utf-8')).keys())
    sub = d[d.table.str.contains(jf, regex=False, na=False)]
    documented = set(sub.field)
    miss = sorted(keys - documented)
    if miss:
        issues.append((jf, miss, []))
        print(f'  [!!] {jf}: 未记录 {miss}')
    else:
        print(f'  [OK] {jf:26s} {len(keys):2d} 个键，字典完全覆盖')

print()
print(f'字典条目总数：{len(d)}')
if issues:
    print(f'结论：{len(issues)} 个文件存在未记录字段')
    sys.exit(1)
print('结论：字典与全部数据文件完全对齐')

# ---------- 附加检查：报告正文里声明的"N 列"是否与文件实际列数一致 ----------
print()
print('-' * 74)
print('报告正文「N 列」声明 vs 文件实际列数')
print('-' * 74)
import re
html = open('reports/dataset-report.html', encoding='utf-8').read()
bad = 0
for f in FILES:
    actual = len(pd.read_csv(P + f, nrows=0).columns)
    # 匹配形如 `<h3>xxx.csv（16 列，9,427 行）</h3>`
    pat = re.escape(f) + r'（(\d+)\s*列'
    hits = re.findall(pat, html)
    if not hits:
        print(f'  [--] {f}: 报告未声明列数（跳过）')
        continue
    for h in hits:
        if int(h) == actual:
            print(f'  [OK] {f}: 报告写 {h} 列，实际 {actual} 列')
        else:
            print(f'  [!!] {f}: 报告写 {h} 列，实际 {actual} 列')
            bad += 1
if bad:
    print(f'结论：{bad} 处列数声明与实际不符')
    sys.exit(1)
print('结论：所有列数声明与实际一致')

# ---------- 附加检查：缺陷记录表格行数是否与正文声明的"N 处"一致 ----------
print()
print('-' * 74)
print('缺陷记录表格行数 vs 正文声明')
print('-' * 74)
m = re.search(r'完整记录了\s*<b>(\d+)\s*处数据缺陷', html)
declared = int(m.group(1)) if m else None
if '<h2>十、数据质量' in html:
    seg = html.split('<h2>十、数据质量')[1].split('<h2>十一')[0]
    nrows = len(re.findall(r'<tr>\s*<td>(\d+)</td>', seg))
    if declared is None:
        print(f'  [!!] 正文未声明缺陷数量，表格有 {nrows} 行')
        sys.exit(1)
    if declared == nrows:
        print(f'  [OK] 正文声明 {declared} 处，表格实际 {nrows} 行')
    else:
        print(f'  [!!] 正文声明 {declared} 处，表格实际 {nrows} 行')
        sys.exit(1)
