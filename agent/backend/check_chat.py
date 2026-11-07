"""命令行验收脚本：发一个问题，把 SSE 流拼成最终答案并做机器检查。

【为什么需要这个脚本】
/api/chat 返回的是 SSE（text/event-stream），不是 JSON。
直接用 urllib 读会是 "Expecting value: line 1 column 1" ——
不是服务挂了，是我请求方式错了。

用法：
    python check_chat.py "第44周退货率为什么涨了"
    python check_chat.py "第44周退货率为什么涨了" --raw

机器检查项（KNOWN-BAD 清单）：
    1. by_llm 是否为 True、llm_note 是否为空
    2. 正文是否泄露 snake_case 字段名（discount_sku_pct / quality_promo ...）
    3. 正文是否出现编程式比较表达式（a=0、b > c）
    4. 正文里的每个小数能否从工具返回推出来（换算、两数相减放行；
       推不出来判编造）。取证源是 tool_result 的 data/evidence，
       不是 answer —— answer 里没有 facts 字段
    5. 段数、字数是否在预期区间
"""
import json
import re
import sys
import time
import urllib.request

BASE = 'http://127.0.0.1:8848'


def stream_chat(question, pace=0, timeout=240):
    """按 SSE 帧读流。必须维护 buffer —— 一帧可能跨两个 TCP 包。"""
    body = json.dumps({'question': question, 'pace': pace}).encode('utf-8')
    req = urllib.request.Request(
        BASE + '/api/chat', data=body,
        headers={'Content-Type': 'application/json',
                 'Accept': 'text/event-stream'})
    events = []
    buf = ''
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            chunk = resp.read(2048)
            if not chunk:
                break
            buf += chunk.decode('utf-8', 'replace')
            # 只处理以空行结尾的完整帧；残帧留在 buffer 里等下一包
            while '\n\n' in buf:
                frame, buf = buf.split('\n\n', 1)
                data = []
                for line in frame.split('\n'):
                    if line.startswith(':'):
                        continue          # 注释行（首帧 ': connected' 就是这种）
                    if line.startswith('data:'):
                        data.append(line[5:].strip())
                if not data:
                    continue
                try:
                    obj = json.loads('\n'.join(data))
                except json.JSONDecodeError:
                    continue
                # 【帧格式】服务端 to_sse() 只发 'data: {...}'，没有 'event:' 行，
                # 事件类型放在 JSON 的 type 字段里。
                # 我第一版按标准 SSE 去读 event: 行，结果事件数恒为 0 ——
                # 不是服务的问题，是解析器的问题。
                events.append((obj.get('type'), obj))
    return events, time.time() - t0


# ---------- 数字溯源 ----------
# 【这条检查原来是个摆设，两个原因叠加】
# 1) 取证源取错。run_all 传进来的是 answer['facts']，而 answer 顶层
#    根本没有这个字段（只有 headline/badge/summary/blocks/caveats/week/
#    llm_note/by_llm），所以 facts 恒为 {}。比对对象是空字典，
#    任何小数都必然"查无"。现已改为从 tool_result 的 data/evidence 取证。
# 2) 结论不进 problems。orphan 只写进 notes，就算 facts 是对的，
#    编造的数字也只是"提示"一下，用例照样 PASS。
# 2026-11-02 实测：编造 7 个与 facts 毫无关系的小数
# （19.87 / 6.42 / 21.35 / 9.15 / 4.88 / 10.72 / 12.03），problems 返回 []。
#
# 现在改成"能推导才放行"：
#   ① 原样出现在 facts 里                → 通过
#   ② 换个单位能对上（0.1704 ↔ 17.04%）  → 通过
#   ③ 是两个数之差（百分点差）           → 通过
#   ④ 都不满足                          → 判为编造，进 problems
# 容差 0.06 是给两位小数的四舍五入留的，不是给"差一点"的编造留的。
_SCALES = (1.0, 100.0, 0.01, 10000.0, 0.0001)


def _tol_for(raw):
    """容差跟着正文的写法走：写了两位小数，就必须精确到两位。

    【为什么不能定一个死容差】
    第一版用固定 0.06，结果 facts 里有个真实存在的 18.0，
    于是 17.94 和 18.04 都落在它 ±0.06 内被判"推导得出"——
    篡改一位小数的编造就这样混过去了，实测两个都漏。
    改成按小数位数给容差后："18.04" 只允许 0.006，
    与 18.0 差 0.04，抓得住；"13.5" 仍允许 0.06，
    与真实的 13.53 差 0.03，不误伤。
    规则说白了就是：写多细就得对多细。
    """
    dec = len(raw.split('.')[1]) if '.' in raw else 0
    return 0.6 * (10 ** -dec)


def _collect_numbers(obj, out):
    """递归收集 facts 里的所有数字。字符串里的也要，因为 evidence 是文本。"""
    if isinstance(obj, dict):
        for v in obj.values():
            _collect_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_numbers(v, out)
    elif isinstance(obj, bool):
        pass                       # bool 是 int 的子类，别把 True 当成 1
    elif isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        for m in re.findall(r'-?\d+\.?\d*', obj):
            try:
                out.append(float(m))
            except ValueError:
                pass
    return out


def _strip_thousands(s):
    """去掉千分位逗号。

    正文里的 "83,645.5" 直接按 \\d+\\.\\d+ 抽会得到 "645.5"，
    跟 facts 里的 83645.5 对不上 —— 这是纯误判来源。
    只在逗号后正好接三位数字时删，不碰 "3.6, 2.9" 这种并列。
    """
    return re.sub(r'(?<=\d),(?=\d{3}(?!\d))', '', s)


def _week_index(facts):
    """把 facts 里所有带 week 的记录收成 周次 → 该周所有数值 的索引。"""
    recs = []

    def walk(o):
        if isinstance(o, dict):
            wk = o.get('week')
            if isinstance(wk, (int, float)) and not isinstance(wk, bool) and len(o) > 1:
                recs.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(facts)
    idx = {}
    for r in recs:
        w = int(r['week'])
        vals = idx.setdefault(w, [])
        for k, v in r.items():
            if k == 'week' or isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                vals.append(float(v))
    return idx


def _belongs(f, vals, tol):
    """f 是不是这一周的某个数（含百分比换算、取绝对值）。"""
    for v in vals:
        for x in (v, v * 100, abs(v), abs(v) * 100):
            if abs(f - x) <= tol:
                return True
    return False


def _check_week_binding(body, facts):
    """周次绑定：句子里只提一个周，数字却只属于另一个周 → 张冠李戴。

    【为什么只判"唯一周"的句子】
    一句话里同时提两个周时（"第44周比第41周高 3.63pp"），
    数字属于谁本来就说不清，硬判会大面积误伤。
    只挑主体唯一的那种句子 —— 而张冠李戴恰恰最容易发生在这里：
    把第41周的 13.04% 写成第44周的，上一层的数值溯源查不出来，
    因为 13.04 确实来自工具返回，只是安错了对象。
    """
    idx = _week_index(facts)
    if len(idx) < 2:
        return []
    bad = []
    for s in re.split(r'[。；;\n]', body):
        weeks = {int(w) for w in re.findall(r'第\s*(\d+)\s*周', s)}
        weeks &= set(idx)
        if len(weeks) != 1:
            continue
        w = weeks.pop()
        for raw in sorted(set(re.findall(r'\d+\.\d+', _strip_thousands(s)))):
            f = float(raw)
            tol = _tol_for(raw)
            if _belongs(f, idx[w], tol):
                continue
            owners = [k for k in idx if k != w and _belongs(f, idx[k], tol)]
            if owners:
                bad.append('%s（实为第 %s 周，句中被写成第 %d 周）'
                           % (raw, '/'.join(map(str, sorted(owners))), w))
    return bad


def facts_from(events):
    """从 SSE 事件里组装"取证对象"。

    【为什么必须由 tool_result 组装，而不是读 answer['facts']】
    answer 顶层只有 type/ts/headline/badge/summary/blocks/caveats/week/
    llm_note/by_llm —— 没有 facts 字段。所以 `answer.get('facts')` 恒为 {}，
    后面"正文里每个小数都要能在 facts 里找到"这条检查就是在跟空字典比，
    **任何小数都必然找不到，等于从上线起空转**，只留一句"未核对"的软提示。

    2026-11-02 已把 run_all 改成从 tool_result 取证，但命令行这条路
    （本文件的 main）还留着旧写法 —— 同一个功能写了两份取证逻辑，
    修一份另一份照旧空转。所以抽成函数，两个入口都调它。

    【注意这里有两个同名不同义的 facts】
    本函数的 facts = "证据包"（工具返回的 data/evidence，用来核对正文里的数字）；
    orchestrator 里的 state['facts'] = "本轮判定标志"（asked_week / no_playbook /
    kb_fallback_used 之类，是流程状态）。
    两者没有关系，谁也喂不了谁。搜代码时看到 facts 先分清是哪一个。
    """
    return {
        '__tools__': [
            {'tool': d.get('tool'), 'data': d.get('data'),
             'evidence': d.get('evidence')}
            for n, d in events if n == 'tool_result' and d.get('ok')
        ]
    }


def check(answer, facts, allow_template=False):
    """返回 (问题列表, 提示列表)。问题=必须修；提示=可接受但要知道。

    allow_template：越界/无关问题本来就该直接给固定答案、不调模型，
    这类用例把"没走 LLM"当失败是错的。由调用方显式声明。
    """
    problems, notes = [], []
    # 【字段名踩坑】answer 里正文是 summary（一整段 markdown），
    # 不是 paras 数组。我第一版按 paras 读，于是恒为 0 段，
    # 还把"正文字数 41"当成模型的锅 —— 那是标题的长度。
    body = (answer.get('headline') or '') + '\n' + (answer.get('summary') or '')

    if not allow_template:
        if not answer.get('by_llm'):
            problems.append('by_llm=False，模型没参与，是模板兜底')
        note = (answer.get('llm_note') or '').strip()
        if note:
            problems.append('llm_note 非空：' + note)

    leak = re.findall(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b', body)
    if leak:
        problems.append('泄露字段名：' + ', '.join(sorted(set(leak))[:6]))

    bad_cmp = re.search(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\s*[=<>]=?\s*-?\d', body)
    if bad_cmp:
        problems.append('出现编程式比较表达式：' + bad_cmp.group(0)[:30])

    # 统计量（t / z / p）是学术写作规范，不该被当成代码
    # 注意用 finditer 而不是 findall：正则只有一个捕获组时，
    # findall 返回的是字符串列表，解包成 (k, v) 会报
    # "not enough values to unpack (expected 2, got 1)"。
    stats = [(m.group(1), m.group(2))
             for m in re.finditer(r'\b([tzpn])\s*=\s*(-?\d+\.?\d*)', body)]
    if stats:
        notes.append('含统计量表述：' + ', '.join(f'{k}={v}' for k, v in stats[:4]))

    n = len(re.sub(r'[*#`\s]', '', body))
    if not allow_template and not (110 <= n <= 900):
        notes.append(f'正文字数 {n}（预期 110–900）')

    # 数字溯源：正文里每个小数都必须能从 facts 推出来（规则见 _SCALES 上方）
    blob = json.dumps(facts, ensure_ascii=False)
    vals = _collect_numbers(facts, [])
    pool = set()
    for v in vals:
        for k in _SCALES:
            for base in (v, abs(v)):
                pool.add(round(base * k, 4))
    nums = sorted(set(re.findall(r'\d+\.\d+', _strip_thousands(body))))
    orphan, derived = [], []
    # 【没有取证对象，不等于数字是编的】
    # 越界/无关问题走固定答复，没有 tool_result；这时候判"编造"是错的，
    # 该老实说"没核对"，而不是给一个假红灯 —— 假红灯会让人去改本来没坏的东西。
    if not vals and nums:
        notes.append('无工具返回，%d 个小数未核对' % len(nums))
        nums = []
    for raw in nums:
        if raw in blob:            # 原样出现过，最直接
            continue
        f = float(raw)
        tol = _tol_for(raw)
        by_scale = any(abs(f - x) <= tol for x in pool)
        by_diff = any(abs(f - abs(a - b)) <= tol
                      for i, a in enumerate(vals) for b in vals[i + 1:])
        if by_scale or by_diff:
            derived.append(raw)
        else:
            orphan.append(raw)
    if orphan:
        problems.append('小数无法从工具返回推出（疑似编造）：'
                        + ', '.join(orphan[:6]))
    if derived:
        notes.append('派生小数已核对：' + ', '.join(derived[:6]))

    # 周次绑定：数字本身对得上，但安到了错的周，也算错。
    # 上一层（能否推导）查不出这种情况，它只关心"这个数来没来过"。
    if vals:
        wrong = _check_week_binding(body, facts)
        if wrong:
            problems.append('数字与周次错配：' + '；'.join(wrong[:3]))

    # 内容层检查：这几条是促销归因题的已知陷阱，
    # 只在"诊断类回答"上要求，越界/无关问题不该套这套标准。
    if not allow_template:
        must = {
            '促销机制归因': ['机制'],
        }
        for label, kws in must.items():
            if not any(k in body for k in kws):
                notes.append(f'未覆盖要点：{label}')

    return problems, notes


def main():
    if len(sys.argv) < 2:
        print('用法: python check_chat.py "你的问题" [--raw]')
        return 1
    question = sys.argv[1]
    raw = '--raw' in sys.argv

    events, cost = stream_chat(question)
    answer = next((d for n, d in events if n == 'answer'), None)
    # 取证对象从 tool_result 组装（见 facts_from 的说明）；
    # 以前这里写的是 answer.get('facts')，恒为 {}，数字溯源整条空转。
    facts = facts_from(events)

    print(f'问题: {question}')
    print(f'耗时: {cost:.1f}s   事件数: {len(events)}   '
          f'类型: {[n for n, _ in events]}')

    if not answer:
        print('!! 没有收到 answer 事件')
        for n, d in events[-3:]:
            print('  ', n, str(d)[:200])
        return 1

    print('=' * 72)
    print('HEAD:', answer.get('headline'))
    print('=' * 72)
    for i, p in enumerate((answer.get('summary') or '').split('\n\n'), 1):
        if p.strip():
            print(f'[{i}] {p.strip()}')
            print('-' * 72)

    problems, notes = check(answer, facts)
    print(f'by_llm={answer.get("by_llm")}  llm_note={answer.get("llm_note")!r}')
    print('-' * 72)
    if problems:
        print('【必须修】')
        for x in problems:
            print('  ✗', x)
    else:
        print('【必须修】无')
    if notes:
        print('【提示】')
        for x in notes:
            print('  ·', x)

    if raw:
        print('=' * 72)
        print(json.dumps(answer, ensure_ascii=False, indent=2)[:6000])

    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())