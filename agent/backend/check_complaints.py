# -*- coding: utf-8 -*-
"""把用户投诉过的原话逐条重放，检查界面上还会不会出现那两句话。

【为什么单独写这个脚本】
run_all.py 测的是"系统行为对不对"（有没有走对工具、徽章对不对）。
但用户投诉的不是行为，是**某一句话出现在了屏幕上**：
  · 「样本量不足以支撑任何归因」
  · 「这个问题不在我的能力范围，我不硬答」

这两句话消失过了吗？代码改了不等于界面上没有。
这个脚本直接在最终 answer 的全文里搜这些字符串，搜到就报 FAIL。
它测的是**措辞**，和 run_all 的行为测试互补，不是替代。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import get_agent

# 用户投诉的原话，以及它可能变形的几种写法。
# 只要命中任何一个，就说明这套话术又回来了。
BANNED = [
    # 用户投诉过的原话，以及它们的变形
    '样本量不足',
    '样本量不足以',
    '样本不足',
    '不足以支撑任何归因',
    '不在我的能力范围',
    '我不硬答',
    '不做通用问答',
    '超出范围',
    # 生造词：把布尔判断直接拼成中文（"不" + "业务显著"）
    '不业务显著',
    '业务不显著',
    '未异常',
    '不统计显著',
    '统计不显著',
    # 元话语：跟读者交代"我怎么写报告"
    '本报告',
    '本文认为',
    '核心结论就是',
    '我判定为',
    # 【知识库兜底场景的禁用语】
    # 这几句是"数据里没这一类"时最容易脱口而出的说法，
    # 但它们都在推卸而不是回答：
    #   · "未收录原因" —— 用户的问题真实存在，是我的分类没收它
    #   · "没有相关数据" —— 事实，但说完就没了，用户拿不到任何东西
    # 正确的说法是"数据里没单列这一类，按通用做法给你"。
    '未收录',
    '暂不支持该原因',
    '无法回答该问题',
]

# 每条用例：问题、不能出现的词、必须出现的要素（人话，不是精确字符串）
CASES = [
    ('数码类目第52周为什么退货率高', '样本不足那类话术'),
    ('服饰类目第52周为什么退货率高', '样本不足那类话术'),
    ('生鲜第52周退货率正常吗', '样本不足那类话术'),
    ('个护类目最新一周怎么样', '样本不足那类话术'),
    ('你好你是谁', '拒绝卡片那套话'),
    ('你能做什么', '拒绝卡片那套话'),
    ('你好', '拒绝卡片那套话'),
    # 边界：带业务词的问候不能被当成问候吞掉
    ('你好，第44周为什么涨', '把业务问题误判成问候'),
    # 【知识库兜底：数据里没有这一类的原因】
    # 这几条要验证的不是"答得对不对"，而是"有没有答"。
    # 旧行为是 action_playbook 返回未收录 → 整份回答退化成拒绝。
    ('标签脱落这个问题该怎么解决', '知识库兜底不该退化成拒绝'),
    ('吊牌缺失怎么处理', '知识库兜底不该退化成拒绝'),
    ('收到货有异味怎么办', '知识库兜底不该退化成拒绝'),
    ('赠品没送怎么改进', '知识库兜底不该退化成拒绝'),
]


def collect_text(ans: dict) -> str:
    """把一条回答里所有会显示给用户的文字拼起来。"""
    bits = [
        ans.get('headline') or '',
        ans.get('summary') or '',
        str((ans.get('badge') or {}).get('label') or ''),
    ]
    for c in ans.get('caveats') or []:
        bits.append(str(c))
    # blocks 里的表格/要点也会显示，一并搜
    for b in ans.get('blocks') or []:
        if isinstance(b, dict):
            for k in ('title', 'text', 'body', 'note'):
                if b.get(k):
                    bits.append(str(b[k]))
            for row in b.get('rows') or []:
                bits.append(' '.join(str(x) for x in row) if isinstance(row, (list, tuple))
                            else str(row))
        else:
            bits.append(str(b))
    return '\n'.join(bits)


async def main() -> int:
    agent = get_agent()
    old = agent.pace
    agent.pace = 0.0
    fails = 0
    try:
        for q, why in CASES:
            badge, ans, tools = '', {}, []
            async for ev in agent.run(q):
                if ev.type == 'tool_call':
                    tools.append(ev.payload['tool'])
                elif ev.type == 'answer':
                    ans = ev.payload
                    badge = (ans.get('badge') or {}).get('label', '')
            text = collect_text(ans)
            hits = [w for w in BANNED if w in text]
            ok = not hits
            if not ok:
                fails += 1
            print(f'{"PASS" if ok else "FAIL"}  {q}')
            print(f'      徽章 {badge} | 工具 {"→".join(tools) or "(无)"} | 检查项 {why}')
            if hits:
                print(f'      !! 命中禁用语：{hits}')
                # 把命中的上下文打出来，方便直接定位是哪一句
                for w in hits:
                    i = text.find(w)
                    print(f'      上下文：…{text[max(0, i-40):i+len(w)+40]}…')
            else:
                # 顺带把第一句显示出来，人工扫一眼语气
                first = (ans.get('headline') or '').strip()
                print(f'      首句：{first[:70]}')
            print()
    finally:
        agent.pace = old
    print(f'结果：{len(CASES)-fails}/{len(CASES)} 通过；禁用语命中 {fails} 处')
    return 0 if fails == 0 else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))