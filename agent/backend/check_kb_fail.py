# -*- coding: utf-8 -*-
"""知识库工具失败时的措辞检查。

【为什么单独写这个】
check_complaints.py 测的是"方案库真的查过、真的没有"这条路径。
但还有一条更尴尬的路径：方案库压根没查成（文件被删、JSON 坏了）。

这两件事在界面上长得很像，说出来的话却是相反的：
  · "方案库里没有这一类"  → 用户会放弃，认为这个问题无解
  · "方案库这次没读到"    → 用户知道是我这边的问题，可以重试

上一版只有"命中/未命中"两档，工具挂了就被写成"知识库里也没有
完全对应的条目"——那是假话，而且后面紧跟着又给出替代口径数字，
前面说"没有近似条目"、后面说"最接近的是材质做工"，自己打自己。

这个脚本用**注入故障**的方式跑真实链路：
  ① 把 reason_kb.json 改名 → 工具报"文件不存在"
  ② 写一个非法 JSON 进去 → 工具报"读取失败"
两种故障下都必须做到：
  · 不出现"知识库里没有""未收录""暂时没有整理好的动作"这类断言
  · 必须出现"没读到 / 读取失败"这类承认
  · 标题不能说"也没有近似条目"（因为确实给了替代口径数字）
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestrator import get_agent


def _find_kb() -> Path:
    """定位知识库文件。

    【为什么不能写死 ../..】
    同一个 backend 目录，在项目里是 <proj>/agent/backend（上溯两级到 <proj>），
    在归档里是 <归档>/backend（上溯两级会跑到 <归档> 外面）。
    写死相对层级，两边必然有一边错——而且只有换环境才暴露。
    改成按候选顺序找：先找同级 data/，再找上两级 data/。
    """
    here = Path(__file__).resolve().parent
    for base in (here, here.parent, here.parent.parent):
        cand = base / 'data' / 'reason_kb.json'
        if cand.exists():
            return cand
    # 都找不到时返回最可能的那个，让后面的报错信息能指对地方
    return here.parent / 'data' / 'reason_kb.json'


KB = _find_kb()

# 失败时**绝不能**出现的断言。这些都是"我查过了，确实没有"的语气。
# 注意：不能把"没有"两个字一刀切禁掉——正文里合法地会出现
# "数据里没有它的统计"，那是真话。所以只禁这几个具体句式。
BANNED_WHEN_FAILED = [
    '知识库里也没有',
    '知识库中也没有',
    '方案库里也没有',
    '也没有对应的条目',
    '未收录',
    '暂时没有整理好的动作',
    '没有整理好的动作清单',
    '这一类暂时没有',
    # 标题里的假话（它明明给了替代口径数字）
    '也没有近似条目',
]

# 失败时必须出现的承认。至少命中一个，否则就是没交代清楚。
MUST_ADMIT = ['没读到', '读取失败', '没取到', '读取异常', '没查成']

CASES = [
    ('标签脱落这个问题该怎么解决', '文件缺失'),
    ('收到货有异味怎么办', '文件缺失'),
]


def _collect(ans: dict) -> str:
    parts = [ans.get('headline') or '', ans.get('summary') or '']
    for b in ans.get('blocks') or []:
        t = b.get('type')
        if t == 'table':
            parts.append(b.get('title') or '')
            for row in b.get('rows') or []:
                parts.append(' '.join(str(c) for c in row if c))
            parts.append(b.get('note') or '')
        elif t == 'note':
            parts.append(b.get('title') or '')
            parts.append(b.get('text') or '')
    parts += [c for c in (ans.get('caveats') or []) if c]
    return '\n'.join(parts)


async def run_one(q: str, tag: str) -> list[str]:
    agent = get_agent()
    old = agent.pace
    agent.pace = 0.0
    ans: dict = {}
    try:
        async for ev in agent.run(q):
            if ev.type == 'answer':
                ans = ev.payload
    finally:
        agent.pace = old
    body = _collect(ans)
    head = ans.get('headline') or ''
    errs: list[str] = []

    for w in BANNED_WHEN_FAILED:
        if w in body:
            errs.append(f'出现假话「{w}」')

    if not any(w in body for w in MUST_ADMIT):
        errs.append('没有承认"没读到"，用户会以为真的没有这类做法')

    # 标题不能自相矛盾：说"没有近似条目"却给了替代口径数字
    if '近似条目' in head:
        errs.append(f'标题说"没有近似条目"，但正文给了替代口径数字：{head}')

    print(f'\n=== [{tag}] {q} ===')
    print(f'headline: {head}')
    print(f'badge   : {ans.get("badge")}')
    print(f'caveats : {ans.get("caveats")}')
    print('--- summary ---')
    print(ans.get('summary'))
    print('--- blocks ---')
    for b in ans.get('blocks') or []:
        print(f'  [{b.get("type")}] {b.get("title") or b.get("text", "")[:60]}')
    if errs:
        for e in errs:
            print(f'  !! {e}')
    else:
        print('  ok')
    return errs


async def main() -> int:
    if not KB.exists():
        print(f'找不到知识库文件：{KB}')
        return 2

    # 【备份放内存，不落盘】
    # 原来这里 shutil.copy2 出一个 data/reason_kb.json.bak_check 再在 finally 里删掉。
    # 两个问题：① 往 data/ 里写临时文件，测试会在项目数据目录留下垃圾；
    # ② 删它要经过环境的 safe-delete 钩子，钩子一拦，进程直接被中断
    #    （表现为退出码 1、没有 FAIL 行、PASS 永远打不出来，看起来像断言失败）。
    # 读进内存就能完全避开删除操作 —— 恢复也不依赖这个文件是否还在。
    orig = KB.read_bytes()
    total: list[str] = []
    try:
        # ---------- 故障 ①：文件不存在 ----------
        os.replace(KB, KB.with_suffix('.json.moved'))
        for q, tag in CASES:
            total += await run_one(q, tag)

        # ---------- 故障 ②：JSON 非法 ----------
        KB.write_text('{ this is not json ', encoding='utf-8')
        total += await run_one('吊牌缺失怎么处理', 'JSON损坏')
    finally:
        # 恢复：优先把移走的挪回来（同盘 rename，原子的），否则用内存原件重写
        moved = KB.with_suffix('.json.moved')
        if moved.exists():
            os.replace(moved, KB)
        else:
            KB.write_bytes(orig)

    # 【恢复必须校验，不能靠"没报错"就当好了】
    # 这个脚本会真的把线上在用的知识库改名、写坏，所以"恢复成功"本身
    # 就是它最该保证的一件事。原版只有一个 `except OSError: pass`，
    # 恢复失败会被静默吞掉，然后脚本继续去评判措辞 ——
    # 也就是说它可能一边把知识库弄坏、一边报 PASS。
    # 现在恢复完必须逐字节比对：文件在、内容和开工前完全一致。
    if not KB.exists():
        print(f'★ 恢复失败：{KB} 不存在了 —— 知识库已被测试破坏')
        return 3
    try:
        now = KB.read_bytes()
    except OSError as e:
        print(f'★ 恢复失败：读不回来 {e}')
        return 3
    if now != orig:
        print(f'★ 恢复不完整：{len(now)} 字节，开工前是 {len(orig)} 字节')
        try:
            json.loads(now.decode('utf-8'))
        except Exception:                                   # noqa: BLE001
            print('★ 恢复后的文件不是合法 JSON —— 这份知识库现在是坏的')
        return 3
    print('\n' + '=' * 60)
    if total:
        print(f'FAIL  {len(total)} 处')
        for e in total:
            print(f'  - {e}')
        return 1
    print('PASS  知识库失败场景措辞全部正确（知识库已原样恢复，逐字节一致）')
    return 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))