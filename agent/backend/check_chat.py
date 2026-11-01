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
    4. 数字是否全部来自事实包（这里只做粗筛：抓出所有小数，看是否成对出现在 facts 里）
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

    # 数字粗筛：抽出所有小数，看是否能在 facts 里找到
    blob = json.dumps(facts, ensure_ascii=False)
    nums = set(re.findall(r'\d+\.\d+', body))
    orphan = [x for x in nums if x not in blob and x.lstrip('0') not in blob]
    if orphan:
        notes.append('小数未直接出现在 facts（可能是派生值）：' + ', '.join(sorted(orphan)[:8]))

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
    facts = next((d.get('facts') for n, d in events if n == 'answer'), None) or {}

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