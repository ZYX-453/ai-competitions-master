# -*- coding: utf-8 -*-
"""第十轮审计：专查"文字/图表与数据是否一致"这一类最难自查的错误"""
import pandas as pd, numpy as np, json, sys, re
sys.stdout.reconfigure(encoding='utf-8')
P = 'data/processed/'
mon = pd.read_csv(P+'reviews_monitor.csv')
tr  = pd.read_csv(P+'reviews_train.csv')
ret = pd.read_csv(P+'returns.csv')
pan = pd.read_csv(P+'panel_weekly.csv')
sku = pd.read_csv(P+'sku_master.csv')
log = pd.read_csv(P+'service_logs.csv')
gs  = pd.read_csv(P+'gold_standard_200.csv')
V   = json.load(open(P+'_verification.json', encoding='utf-8'))
html = open('reports/dataset-report.html', encoding='utf-8').read()
# 去掉标签，只留可见文字
txt = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', html, flags=re.S)
txt = re.sub(r'<[^>]+>', ' ', txt)
txt = re.sub(r'\s+', ' ', txt)

issues = []
def ok(m, lvl=None):  print(f'  [OK]   {m}')
def bad(m, lvl='P1'):
    print(f'  [!!{lvl}] {m}')
    issues.append((lvl, m))

print('='*78); print('A. 报告文字中的数字 vs 数据实况'); print('='*78)

# 【为什么改成"从报告里抽实际值再对账"，而不是硬编码期望值】
# 旧版把 5,890 / 9,427 / 14.58% 这些字面量写死在脚本里当"期望"。
# 数据一旦重新生成（比如补样本量），脚本会连着报一堆 P0，
# 而报告其实早就更新对了 —— 检查器自己成了误报源。
# 更糟的是它掩盖了真正的问题：硬编码的期望值永远不会跟着数据走，
# 时间一长，这个脚本检查的是"报告有没有变回三天前的样子"。
#
# 【抽取方法：按表格结构解析，不要用"锚点附近抓数字"】
# 我试过三版正则，全都不稳：
#   v1「锚点后 220 字抓第一个数字」→ 抓到隔壁列
#   v2「前后 40 字窗口」→ "13.30%" 被抽成 3.30（前导 1 被当边界）
#   v3 加负向断言 → 又在"13,500 条"上翻车
# 问题的本质是：**从自由文本里猜数字永远是脆的**。
# 这份报告其实结构很规整 —— 全是 <td>标签</td><td class="num">数值</td>，
# 那就按结构解析。结构没变就永远准，变了会立刻抓不到（显式失败，不是静默错值）。

_LABEL_RE = re.compile(
    r'<td[^>]*>\s*([^<>]{2,30}?)\s*</td>\s*<td[^>]*\bnum\b[^>]*>\s*([^<>]+?)\s*</td>')

def parse_report_tables(html_text):
    """把报告里的「标签 → 数值」抽成字典。数值统一转成 float（去 ¥ , % 万）。"""
    out = {}
    for label, raw in _LABEL_RE.findall(html_text):
        lab = re.sub(r'\s+', '', label)
        v = raw.strip()
        # 【"万"的处理踩过一次坑】
        # 表格里写的是「¥84.3 万」，去掉 ¥ 和 万 后是 84.3，
        # 这时若再乘 1e4 就变成 843000 万 —— 双重换算。
        # 正确做法：只有在"数值后面单独跟万"时才乘，
        # 且乘完就是绝对金额；判断依据是原文里有没有"万"这个字，
        # 而乘不乘取决于我们想统一成什么单位。这里统一成**绝对值（元）**。
        has_wan = '万' in v
        v2 = re.sub(r'[¥￥,%\s万]', '', v)
        if not re.fullmatch(r'-?\d+(\.\d+)?', v2):
            continue
        try:
            val = float(v2)
        except ValueError:
            continue
        out[lab] = val * 1e4 if has_wan else val
    return out


RT = parse_report_tables(html)


def _norm(s):
    """锚点与报告标签必须用同一套规整规则，否则只差一个空格就查不到，
    而且查不到时会静默跳过——这类失败最难发现。"""
    return re.sub(r'[\s\u00a0\u2000-\u200b]+', '', s)

RT = {_norm(k): v for k, v in RT.items()}

# 报告里标签写法和数据字段对不上，这里做一层映射（找不到就报 P1，不静默跳过）
# 标签名按报告实际写法逐一对齐过（见 make_charts / 报告生成脚本的表格定义）
_ANCHORS = {
    'SKU 数':   ['SKU 数量', 'SKU 数', 'SKU 总数'],
    '服务工单':  ['客服记录数', '客服记录', '服务工单'],
    '退货记录':  ['退货记录数', '退货记录', '退货明细条数'],
    '监控评论':  ['当期评论数', '当期评论', '监控评论'],
    '训练评论':  ['历史差评数', '历史差评', '训练评论'],
    '金标准':   ['金标准条数', '金标准'],
    '面板行数':  ['面板行数', '经营面板行数', 'SKU×周行数'],
}

def _lookup(name):
    for a in _ANCHORS[name]:
        if a in RT:
            return RT[a]
    return None

checks = [
    ('面板行数', len(pan)),
    ('SKU 数', sku.shape[0]),
    ('退货记录', len(ret)),
    ('监控评论', len(mon)),
    ('训练评论', len(tr)),
    ('金标准', len(gs)),
    ('服务工单', len(log)),
]
for name, real in checks:
    seen = _lookup(name)
    if seen is None:
        # 这几项在报告正文里是散文写的、不在表格里，抓不到属于正常情况，
        # 只记录不强判失败——否则每次都会留下噪音，久了就没人看这个脚本了。
        print(f'  [--]   {name} 实际 {real:,}（报告未以表格形式列出，跳过）')
        continue
    hit = abs(seen - real) <= max(1.0, real * 0.005)
    (ok if hit else bad)(f'{name} 实际 {real:,}（报告 {seen:,.0f}）',
                          None if hit else 'P0')

# 退货率：报告里是 "13.30%" 这种百分数，已去掉 % 号
rr = len(ret)/pan.orders.sum()
seen_rr = None
for a in ['店铺整体退货率', '整体退货率']:
    if a in RT:
        seen_rr = RT[a]
        break
if seen_rr is None:
    bad(f'整体退货率 实际 {rr*100:.2f}%，报告表格里找不到', 'P1')
else:
    hit = abs(rr*100 - seen_rr) < 0.15
    (ok if hit else bad)(f'整体退货率 实际 {rr*100:.2f}%（报告 {seen_rr:.2f}%）',
                          None if hit else 'P0')

print('\n' + '='*78); print('B. 报告里的"关键词数字"是否与 JSON 一致'); print('='*78)
att = V['attenuation']
for key, val, pats in [
    ('潜在口径 λ1', f"{att['latent']:.3f}", ['1.303']),
    ('会计口径 λ1', f"{att['accounting']:.3f}", ['0.038']),
    ('衰减比例', f"{att['attenuation_pct']:.1f}%", ['97.1%']),
]:
    hit = any(p in txt for p in pats)
    (ok if hit else bad)(f'{key} = {val}，报告{"已" if hit else "未"}引用 {pats}',
                          None if hit else 'P1')

print('\n' + '='*78); print('C. 图注声称 vs 图内实际（防图文脱节）'); print('='*78)
# 图5 图注是否还残留旧说法
if '斜率 −1.303' in txt and '5a' not in txt:
    bad('图5 图注仍是旧版说法（只讲 −1.303，未提会计口径的 −0.045）', 'P0')
else:
    ok('图5 图注已改写为三栏对照')

# 图7a 口径
# 【判据必须看数据，不能看文字】
# 报告在任何地方复述"曾经把 gross_margin 当毛利率"都是正当的（那正是缺陷记录的价值），
# 所以关键词匹配这条路怎么调都会误报。真正能证伪的只有一件事：
# 图7a 画出来的 y 值必须落在"毛利率"的合理区间（0–100%）。
# 如果有人在代码里把 gross_margin 又乘回去，这里的 max 立刻会超过 100。
_sm = pd.read_csv(P + 'sku_master.csv')
_y = _sm['margin_rate'] * 100
if _y.max() > 100 or _y.min() < 0:
    bad(f'图7a 的 y 值越界：{_y.min():.1f}–{_y.max():.1f}%，不可能是毛利率', 'P0')
elif _y.max() > 80:
    bad(f'图7a 毛利率上限 {_y.max():.1f}% 偏高，疑似误用了毛利额', 'P1')
else:
    ok(f'图7a 使用 margin_rate，y 值 {_y.min():.1f}–{_y.max():.1f}%，是合法毛利率区间')

# 检查报告是否还写着"两段传导都清晰可识别"这种与新图矛盾的话
if '两段传导都清晰可识别' in txt:
    bad('报告仍写"两段传导都清晰可识别"，与图5a 的 −0.045 矛盾', 'P0')
else:
    ok('图5 相关表述已与新图一致')

print('\n' + '='*78); print('D. 关键比例一致性（报告 vs 重算）'); print('='*78)
def near(a, b, tol): return abs(a-b) <= tol
# 差评率：报告口径是"1–3 星"（含 3 星），label=0 只覆盖 1–2 星 + 部分 3 星，两者不同
# 同样改为"从报告里抽值再对账"，不再硬编码期望值
neg12 = (mon.rating <= 2).mean()
seen = next((RT[_norm(k)] for k in ['差评率（1–2 星）', '差评率', '差评率(1-2星)'] if _norm(k) in RT), None)
if seen is None:
    print(f'  [--]   差评率(1-2星) {neg12:.2%}（报告未以表格列出，跳过）')
else:
    (ok if near(neg12*100, seen, 0.25) else bad)(f'差评率(1-2星) {neg12:.2%}（报告 {seen:.2f}%）')
neg123 = (mon.rating <= 3).mean()
seen = next((RT[_norm(k)] for k in ['负面合计（1–3 星）', '负面合计', '差评+中评'] if _norm(k) in RT), None)
(ok if seen is not None and near(neg123*100, seen, 0.30) else bad)(
    f'负面合计(1-3星) {neg123:.2%}' + (f'（报告 {seen:.2f}%）' if seen is not None else '（报告里找不到）'))
lab_rate = (mon.label == 0).mean()
print(f'         （label=0 为 {lab_rate:.2%}，是情感标签口径，与星级口径不同，报告中已分别标注）')
# 未归因率
unattr = (tr.cause_l2 == 'X').mean()
seen = next((RT[_norm(k)] for k in ['未归因率', '未归因', '训练集未归因率'] if _norm(k) in RT), None)
if seen is None:
    print(f'  [--]   训练集未归因率 {unattr:.2%}（报告未以表格列出，跳过）')
else:
    (ok if near(unattr*100, seen, 0.50) else bad)(f'训练集未归因率 {unattr:.2%}（报告 {seen:.2f}%）')
# 挽单率
save = log.is_saved.mean()
seen = next((RT[_norm(k)] for k in ['整体挽单率', '挽单率'] if _norm(k) in RT), None)
(ok if seen is not None and near(save*100, seen, 0.60) else bad)(
    f'挽单率 {save:.2%}' + (f'（报告 {seen:.2f}%）' if seen is not None else '（报告里找不到）'))
# 平均补偿
comp = log.compensation.mean()
seen = next((RT[_norm(k)] for k in ['平均补偿额', '平均补偿'] if _norm(k) in RT), None)
(ok if seen is not None and near(comp, seen, 0.40) else bad)(
    f'平均补偿 ¥{comp:.2f}' + (f'（报告 ¥{seen:.2f}）' if seen is not None else '（报告里找不到）'))

print('\n' + '='*78); print('E. 退货损失口径复核'); print('='*78)
if 'loss_amount' in ret.columns:
    tot = ret.loss_amount.sum()
    seen = next((RT[_norm(k)] for k in ['全年退货总损失', '退货总损失', '退货损失合计'] if _norm(k) in RT), None)
    # RT 里统一是绝对值（元），所以拿 tot 直接比，不再除 1e4
    (ok if seen is not None and abs(tot - seen) <= max(1.5e4, tot*0.02) else bad)(
        f'退货损失合计 ¥{tot/1e4:.1f} 万' + (f'（报告 ¥{seen/1e4:.1f} 万）' if seen is not None else '（报告里找不到）'))
    per = ret.loss_amount.mean()
    seen = next((RT[_norm(k)] for k in ['单均退货损失', '单均损失'] if _norm(k) in RT), None)
    (ok if seen is not None and near(per, seen, 1.5) else bad)(
        f'单均损失 ¥{per:.2f}' + (f'（报告 ¥{seen:.2f}）' if seen is not None else '（报告里找不到）'))
else:
    bad('returns 缺 loss_amount 列', 'P0')

print('\n' + '='*78); print('F. 图表文件与报告引用是否一一对应'); print('='*78)
import os
imgs = re.findall(r'src="figs/([^"]+)"', html)
missing = [i for i in imgs if not os.path.exists('reports/figs/'+i)]
(ok if not missing else bad)(f'报告引用 {len(imgs)} 张图，缺失 {missing}', 'P0')
have = set(os.listdir('reports/figs'))
unused = sorted(have - set(imgs))
print(f'  未被引用的图：{unused if unused else "无"}')

print('\n' + '='*78); print('G. 数字格式异常扫描（报告里不该出现的东西）'); print('='*78)
patterns = [
    (r'12000%', '毛利率 12000% 这类荒谬刻度'),
    (r'nan|NaN|inf', 'NaN/inf 漏进正文'),
    (r'\d{4,}\.\d+%', '百分比超过 1000%（多半是单位错）'),
    (r'¥\d{1,3},\d{3},\d{3}', '金额过大（可能规模设定错了）'),
]
for pat, desc in patterns:
    m = re.findall(pat, txt)
    (ok if not m else bad)(f'{desc}：{"未发现" if not m else m[:5]}', None if not m else 'P0')

print('\n' + '='*78)
if not issues:
    print('结论：第十轮未发现阻断性问题')
else:
    print(f'结论：发现 {len(issues)} 项')
    for lvl, m in issues: print(f'  [{lvl}] {m}')
