"""五个预置场景的批量回归。

【为什么要单独写这个脚本】
之前我是"改一处 → 手动跑一个 → 看一眼 → 再改"，五轮下来发现：
每改一条校验，很容易在另一个场景上引入新问题（比如放宽 t 检验的正则，
就顺手放宽了字段名检测）。单点验证看不出这种交叉回归。
这里把 5 个场景一次跑完，只输出摘要行，便于对比前后。

用法：
    python run_all.py          # 跑全部
    python run_all.py E1 E3    # 只跑指定用例

【它不管什么 —— 这一点很重要】
本脚本只测"系统行为对不对"：走没走对工具、徽章对不对、数字有没有编。
它**不测措辞**。用户投诉过的两件事（"样本不足"字样、拒绝卡片）
都发生在文案上，行为完全正常 —— 所以在这里永远测不出来。
那两项由 check_complaints.py 负责，两者互补，缺一不可。
"""
import json
import sys

import check_chat as cc

CASES = [
    ('E1', '第44周退货率为什么涨了', '机制已识别',
     '促销周：必须识别机制、且不针对破损开动作'),
    ('E2', '最新一周差评有异常吗', '已重定向',
     '最新周正常 → 自动重定向到真正异常的第 44 周'),
    # 【用例随数据版本更新】
    # 第 3 条原为"第3周151单 → 样本不足"，v2.4 改成"1,713 单但 z 不显著"，
    # v2.5 把店铺流量放大 6 倍后，第 3 周是 10,274 单、z=+0.66，仍然不显著
    # —— 这一条的前提居然一直成立，因为"第 3 周本来就不是异动周"是 DGP 的事实，
    # 跟样本量无关。它测的仍然是"敢不敢直说不是异常"。
    #
    # 第 4 条换过两次：v2.3 用第41周（z=-3.77），v2.4 因 z 降到 -1.76 改用第5周
    # （z=-2.92），v2.5 放大样本后第5周 z=-1.25 又不显著了 —— 因为第 5 周
    # 本来就只是"略低"，样本一小才被验成显著。现在全店唯一站得住的
    # 显著下降是第 49 周（z=-5.10，-1.84pp），用它。
    ('E3', '第3周为什么异常', '无异常',
     '第3周数据充足且不显著：必须直说"这周不是异常周"，不许改题去答第44周'),
    ('E4', '第49周怎么回事', '显著改善',
     '第49周 z=-5.10 显著下降：不是问题，不该套归因模板'),
    ('E5', '客服补偿补多少最划算', '权衡分析',
     '先求最优档，再换算价值'),
    # ---- 边界用例：这几条是"对抗测试"里发现的真实缺陷，固化下来防回归 ----
    ('E6', '第60周退货率为什么涨了', '超出范围',
     '数据只到 52 周：必须明说没有这一周，不许拿别周顶替'),
    # 【v2.5】E7 的期望徽章从"超出范围"改成"超出能力"。
    # 原来它和数据缺周（E6）共用一个徽章，但用户投诉的恰恰是这件事：
    # 问个不在业务范围内的问题，收到一张和"数据没有这一周"一模一样的
    # 红牌子，会以为这个产品只有一种回答方式。
    # 现在两者分开：数据边界 vs 能力边界，徽章、正文、caveat 全都不一样。
    ('E7', '帮我写一首诗', '超出能力',
     '非业务问题：必须拒绝，但要说清自己能做什么，不许硬套归因模板作答'),
    ('E8', '第44周和第41周哪个更严重', '多周对比',
     '对比题：必须两个周都答，并说清排序依据'),
]


def main():
    only = {a.upper() for a in sys.argv[1:] if not a.startswith('-')}
    rows, ndig = [], 0
    for cid, q, want_badge, why in CASES:
        if only and cid not in only:
            continue
        ev, cost = cc.stream_chat(q)
        ans = next((d for n, d in ev if n == 'answer'), None)
        facts = (ans or {}).get('facts') or {}
        if not ans:
            rows.append((cid, 'NO-ANSWER', '-', '-', f'{cost:.1f}s'))
            continue

        badge = (ans.get('badge') or {}).get('label', '')
        # 越界/无关问题本来就是固定答复，不走模型；把"没走 LLM"判成失败是错的。
        # 【v2.5】"超出能力"也是固定答复，一起放过。
        allow_tpl = badge in ('超出范围', '超出能力')
        problems, notes = cc.check(ans, facts, allow_template=allow_tpl)
        # 徽章不匹配也是硬性失败：徽章是后端判定结论，写错了等于结论错了。
        if badge != want_badge:
            problems = problems + [f'徽章 {badge} ≠ 期望 {want_badge}']
        by_llm = ans.get('by_llm')
        status = 'PASS' if not problems else 'FAIL'
        # 【失败时必须点名原因，不能只说"兜底了"】
        # 之前 E5 偶发 FAIL，摘要只显示"by_llm=False，是模板兜底"，
        # 到底是模型没返回、还是被自己的校验拦了，从这一行看不出来，
        # 只能靠打开 CANYU_LLM_DEBUG 重新复现 —— 而它又不是每次都能复现。
        # 校验函数本来就把原因写在 llm_note 里（"模型两次输出均未通过校验（xxx）"），
        # 这里直接把它接进备注，一次跑完就知道该改提示词还是改校验。
        note = (ans.get('llm_note') or '').strip()
        if not by_llm and note:
            brief = note.replace('模型两次输出均未通过校验', '校验拦截')
            rows.append((cid, status, 'LLM' if by_llm else '模板',
                         'OK' if badge == want_badge else f'{badge}≠{want_badge}',
                         f'{cost:.1f}s', brief[:70]))
        else:
            rows.append((cid, status, 'LLM' if by_llm else '模板',
                         'OK' if badge == want_badge else f'{badge}≠{want_badge}',
                         f'{cost:.1f}s', (problems or notes or [''])[0][:52]))
        if not by_llm and not allow_tpl:
            ndig += 1

    print(f'{"用例":4s} {"结果":10s} {"来源":4s} {"徽章":16s} {"耗时":7s} 备注')
    print('-' * 96)
    for r in rows:
        print(f'{r[0]:4s} {r[1]:10s} {r[2]:4s} {r[3]:16s} {r[4]:7s} {r[5] if len(r) > 5 else ""}')
    print('-' * 96)
    npass = sum(1 for r in rows if r[1] == 'PASS')
    print(f'通过 {npass}/{len(rows)}；模板兜底 {ndig} 个')
    return 0 if npass == len(rows) else 1


if __name__ == '__main__':
    sys.exit(main())