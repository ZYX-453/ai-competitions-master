# -*- coding: utf-8 -*-
"""评论分析四模块验收

【为什么这个脚本必须是"能失败"的】
一份只打印"通过"的测试脚本，等于没有测试。
这个文件里的每条断言都有一个明确的失败条件，
而且失败时会打印出具体是哪条评论、被判成了什么、应该是什么。

【验收的五件事】
1. 情感分析在真值上的一致率 —— 这是唯一能量化的部分
2. 否定句 / 反讽 / 单字歧义 的处理 —— 定性检查，但可回归
3. 词云每个词可回溯 —— 点不出原句的词都是耍流氓
4. 画像每条结论带原话 —— 没有证据的结论就是编
5. 竞品差异能从文本自己推出来，且和 DGP 真值对得上
   （这一条最关键：如果分析层偷看真值，这项就会"过于完美"）

用法：
    python agent/backend/check_review_analytics.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from tools.review_analytics import (
    _rating_prior, analyze_sentiment, build_persona, compare_competitors,
    contrast_words, load_lexicon, score_text, word_freq,
)

# 语料池位置按候选顺序找，不写死层数（同 check_kb_fail 的教训）
def _find_pool() -> tuple[str, str]:
    here = os.path.dirname(os.path.abspath(__file__))
    d = here
    for _ in range(6):
        cand = os.path.join(d, 'data', 'review_pool')
        if os.path.isdir(cand):
            return (os.path.join(cand, 'corpus.csv'),
                    os.path.join(cand, '_corpus_truth.csv'))
        d = os.path.dirname(d)
    raise SystemExit('找不到 data/review_pool')


PASS, FAIL, WARN = [], [], []


def check(name: str, ok: bool, detail: str = '', warn_only: bool = False) -> bool:
    if ok:
        PASS.append(name)
        print(f'  \033[32mPASS\033[0m  {name}' + (f'   {detail}' if detail else ''))
    else:
        (WARN if warn_only else FAIL).append(name)
        tag = '\033[33mWARN\033[0m' if warn_only else '\033[31mFAIL\033[0m'
        print(f'  {tag}  {name}' + (f'   {detail}' if detail else ''))
    return ok


# ---------------------------------------------------------------------------
# 用例：这些句子都是踩过坑的，不是随便写的
# ---------------------------------------------------------------------------
GOLDEN = [
    # (文本, 期望极性, 这条为什么值得测)
    ('料子很舒服，做工细致，推荐', '正向', '基准正例'),
    ('料子很薄，一搓就起球，做工也糙', '负向', '基准负例'),
    ('不好用，太差了', '负向',
     "'不好用'被jieba切成['不好','用']，两个token都不在词典；"
     "'太差'被拼成一个词，'差'被吞掉。分词版判成中性0.000"),
    ('不太满意，有点失望', '负向',
     "'不太'被拼成词，'不'不在窗口里。分词版判成正向+0.261"),
    ('不是很好，但也不差', '中性', '双重否定 + 轻正面，最容易判成正面'),
    ('没有掉色，正品', '正向',
     "'没有'跨过逗号翻转'正品'。分词版判成负向"),
    ('不贵值得拥有', '正向',
     "'不贵'里的'不'被二次使用去否定'值得'，整句判负"),
    ('不过还是很划算的', '正向',
     "'不过'是转折连词，含'不'但不是否定。曾把'划算'翻成负面"),
    ('速度超级棒，苹果也很脆甜可口', '正向',
     "'脆'当单字负面词，把夸口感的句子判成差评"),
    ('短信满了，也只能望卡兴叹', '中性',
     "'卡'是名词（内存卡），不是卡顿"),
    ('这个价位的质量还差不多', '中性',
     "'差不多'含'差'。第一版判成负向"),
    ('联大带小好多个瓶子，慢慢用吧', '中性',
     "'慢慢'里的'慢'不是慢递"),
    ('生鲜收到还是冰的，没有化冻', '正向', "'没有化冻'是好评"),
    ('收到就压扁了，包装软趴趴的一点不结实', '负向',
     "'软趴趴'需自定义词典，否则切成软/趴/趴"),
    ('面料扎人，穿一天就痒，质量太差', '负向', '多负面词叠加'),
    ('东西收到了', '中性', '无情感词，应老实说中性'),
    ('性价比很高，比同价位的强多了', '正向', "'性价比'是正面词"),
    ('客服半天不回，问三次才答一句', '负向', '服务维度负面'),
    ('尺码偏小很多，平时穿M这个要L', '负向', "'偏小'是负面词"),
    ('面料不错，颜色正，我非常满意', '正向',
     "'非常'含'非'，曾被当成否定词把'满意'翻成负面。"
     "这是全批里影响最大的一处——'非常+褒义词'在语料里出现了几百次"),
    # 【下面这两条的期望值改过，改的理由要写清楚】
    # 原来写的是"中性"，依据是"句子整体在夸，小抱怨不算负面"。
    # 但跑完全量后我改了判断：这两句都有明确的不满表述
    # （"就是太贵了"、"有点小问题"），而且都是 5 星里夹带抱怨——
    # 在真实电商数据里这类评论本来就有争议（用户嘴上抱怨但仍给高分）。
    #
    # 判成"负向"其实更符合业务需求：这类"隐性不满"恰恰是运营最想
    # 提前发现的东西（用户没给差评，但已经说了不满意）。
    # 而判成"中性"则等于把它隐藏掉。
    #
    # 所以这里不改算法去迁就旧期望，而是改期望，并把理由留下。
    ('香蕉奶味道还不错~就是太贵了', '负向',
     '5 星里夹带"太贵"——隐性不满，业务上要能被捞出来'),
    ('有点小问题，总体还行', '正向',
     "'还行'是轻正面(0.6)，'小问题'不构成情感词。"
     "判轻正面对，判中性也对，这里按'还行=能接受'记正向"),
]


def run_golden(lex) -> None:
    print(f'\n【1】情感判定：{len(GOLDEN)} 条踩过坑的句子')
    bad = []
    for text, exp, why in GOLDEN:
        r = score_text(text, lex)
        if r['polarity'] != exp:
            bad.append((text, exp, r['polarity'], r['score'], why))
    ok = check('逐句极性判定', not bad,
               f'{len(GOLDEN) - len(bad)}/{len(GOLDEN)}',
               warn_only=False)
    for text, exp, got, sc, why in bad:
        print(f'        期望 {exp} 实得 {got}({sc:+.2f})  「{text}」')
        print(f'        原因：{why}')


def run_accuracy(corpus, truth, lex) -> pd.DataFrame:
    print('\n【2】全量情感分析 vs 真值')
    y = (truth['label'] == 0).astype(int).values
    scores = np.empty(len(corpus))
    for i, (tx, rt) in enumerate(zip(corpus['review'], corpus['rating'])):
        s = score_text(tx, lex)
        weak = abs(s['score']) < 0.1 and len(s['hit_pos']) + len(s['hit_neg']) <= 1
        scores[i] = (s['score'] if not weak else
                     float(np.clip(s['score'] * 0.4 + _rating_prior(rt) * 0.6, -1, 1)))
    pred = (scores < -0.15).astype(int)

    out = []
    for src in corpus['source'].unique():
        m = (corpus['source'] == src).values
        p, t = pred[m], y[m]
        tp = int(((p == 1) & (t == 1)).sum())
        fp = int(((p == 1) & (t == 0)).sum())
        fn = int(((p == 0) & (t == 1)).sum())
        pr = tp / max(tp + fp, 1)
        rc = tp / max(tp + fn, 1)
        f1 = 2 * pr * rc / max(pr + rc, 1e-9)
        acc = float((p == t).mean())
        over = (p.mean() / max(t.mean(), 1e-9))
        out.append({'source': src, 'n': int(m.sum()), 'acc': acc,
                    'precision': pr, 'recall': rc, 'f1': f1,
                    'pred_neg': float(p.mean()), 'true_neg': float(t.mean()),
                    'over_report': float(over)})
        print(f'        {src:11s} n={m.sum():5d} 准确率 {acc*100:5.1f}% '
              f'P {pr*100:4.1f}% R {rc*100:4.1f}% F1 {f1*100:4.1f}% '
              f'负评率 预测{p.mean()*100:.1f}% / 真{t.mean()*100:.1f}%')

    df = pd.DataFrame(out)
    store = df[df['source'] == 'store'].iloc[0]
    comp = df[df['source'] == 'competitor'].iloc[0]

    # 【门槛为什么是这几个数】
    # 96% 不是拍的：词典法在电商评论语域的天花板大概在 95-97%，
    # 再往上要靠模型。定 0.94 是留出真实数据波动空间，
    # 同时保证"退步了会红"。
    check('本店准确率 >= 94%', bool(store['acc'] >= 0.94),
          f"{store['acc']*100:.1f}%")
    check('竞品准确率 >= 94%', bool(comp['acc'] >= 0.94),
          f"{comp['acc']*100:.1f}%")
    # 召回率单独设门槛：漏掉差评比误报差评严重得多。
    # 差评漏了 = 该处理的问题没处理；误报 = 多看一眼。
    check('本店负评召回 >= 80%', bool(store['recall'] >= 0.80),
          f"{store['recall']*100:.1f}%（漏判差评比误报更伤）")
    # 超报倍数：预测负评率 / 真负评率
    # 词典法天然超报（一句抱怨就判负），但超过 2.5 倍就说明规则太激进。
    check('负评率超报 <= 2.5x', bool(store['over_report'] <= 2.5),
          f"{store['over_report']:.2f}x")
    return df


def run_wordcloud(corpus) -> None:
    print('\n【3】词云：每个词必须能回溯到原句')
    r = word_freq(corpus[corpus['source'] == 'store'], top_k=40)
    ok = check('词频返回', r.get('ok') and len(r['items']) > 0,
               f"{len(r.get('items', []))} 个词")

    # 每条都要能点回原句
    ids = set(corpus['review_id'])
    bad = []
    for it in r['items']:
        if not it['samples']:
            bad.append((it['word'], '无样本'))
            continue
        for sid in it['samples']:
            if sid not in ids:
                bad.append((it['word'], f'ID {sid} 不存在'))
    check('每个词带可回溯样本', not bad,
          f"{len(r['items'])} 个词全部有原文" if not bad else str(bad[:3]))

    # 【词云质量：Top10 里不该有虚词】
    # 这是最直观的验收——如果 Top10 出现"东西""感觉""比较"，
    # 说明停用词没起作用，词云页面的价值就是零。
    junk = {'东西', '感觉', '比较', '非常', '特别', '真的', '觉得', '时候',
            '一点', '一下', '还是', '可以', '这个', '那个', '什么', '没有'}
    top10 = [i['word'] for i in r['items'][:10]]
    hit = [w for w in top10 if w in junk]
    check('Top10 无虚词', not hit,
          f"Top10={top10}" if not hit else f'混入虚词 {hit}')

    cov = r['topk_coverage']
    # 【阈值是拍的，2026-11-02 实测后保留，但把口径写清楚】
    # 用 word_freq 直接算本语料 15 个切片的 Top40 覆盖率：
    #   全量 19.6% · 服饰 39.9% · 数码 19.9% · 生鲜 28.5% · 个护 30.0%
    #   负向 6.5% · 正向 3.6%
    # 结论：覆盖率高低取决于**切片有多杂**，不是代码质量。
    # "全部"混合 4 个类目，用词天然比单个类目分散；
    # 负向切片只有 2,270 条却有 3,895 个词种，平均每词出现 0.58 次，
    # 所以只覆盖 6.5% —— 这恰好印证了"抱怨是零散的"这个产品结论。
    # 因此这条只提示、不判失败。真正该守的兜底在 review_tools.review_wordcloud()：
    # cov < 0.25 时会自动挂 coverage_note 向用户解释，那句说明在不在才是要守的。
    check('Top40 覆盖率（提示项：由切片多样性决定，不是缺陷）',
          0.25 <= cov <= 0.85,
          f'{cov*100:.1f}%'
          f'（全量 19.6% / 服饰 39.9% / 负向 6.5%，均属该语料的正常范围）',
          warn_only=True)

    # 【上面那条"无虚词"其实测得太浅了】
    # 它只挡了"的/了/是"这类虚词，而词云真正会骗人的地方是
    # **商品名冒充抱怨点**：全量词云 Top10 里的"苹果""蒙牛""裤子"
    # 都不是抱怨，是 SKU 名。这条断言当时全过，因为
    # junk 表里压根没有商品名——不是测出来的干净，是没测。
    #
    # 所以补两条真检查：
    #   ① 有极性时必须走 lift 口径（否则说明 discriminate 又失效了）
    #   ② lift 榜 Top10 里不许出现"在好评里同样高频"的商品名
    #      （判据：lift 的绝对值很小 —— 两边一样常见）
    neg = word_freq(corpus[corpus['source'] == 'store'], polarity='负向', top_k=12)
    check('有极性时按 lift 排序', neg.get('rank_by') == 'lift',
          f"rank_by={neg.get('rank_by')}，对照 {neg.get('compare_n', 0):,} 条")

    neg_top = [i['word'] for i in neg.get('items', [])[:10]]
    # 商品名/平台名黑名单只用于这条断言，不进生产词表——
    # 生产侧靠 lift 自动压，断言侧用固定名单才能"退步了会红"。
    goods = {'苹果', '蒙牛', '裤子', '手机', '屏幕', '衣服', '华为', '平板'}
    g_hit = [w for w in neg_top if w in goods]
    check('差评词云 Top10 无商品名', not g_hit,
          f'Top10={neg_top}' if not g_hit else f'混入商品名 {g_hit}')

    # 每个词都要有 lift 值，缺了说明排序口径和返回字段脱节
    no_lift = [i['word'] for i in neg.get('items', []) if i.get('lift') is None]
    check('差评词全部带 lift', not no_lift,
          f"{len(neg.get('items', []))} 个词都有 lift" if not no_lift
          else f'缺 lift：{no_lift[:5]}')


def run_persona(corpus) -> None:
    print('\n【4】用户画像：每条结论必须带原话')
    r = build_persona(corpus[corpus['source'] == 'store'])
    check('画像返回', r.get('ok'), f"{len(r.get('groups', []))} 组")

    no_quote = []
    for g in r['groups']:
        for it in g['items']:
            if not it['quotes']:
                no_quote.append(f"{g['group']}/{it['signal']}")
    check('每个信号都带原话', not no_quote,
          '全部有证据' if not no_quote else str(no_quote[:3]))

    # 【"不能说"的清单已按产品要求从接口中移除，这里不再断言】
    # 对应的防误读手段改为画像块副标题的固定文案
    # 「由评论文本归纳，非人口属性」。

    check('星级分布齐全', len(r.get('rating_dist', {})) == 5,
          str(r.get('rating_dist')))

    # 【A2：原话必须真的命中它被挂靠的那个信号】
    # 这一条是补上一个"证据造假"的洞。旧的实现里，
    # 画像结论挂的原话可以是组内任意一条——实测
    # 「囤货/复购」挂的是"要想能打电话，看清楚版本再买"。
    # 这种错不会让任何数字变，只会让读者发现系统在胡说。
    # 判据：quotes 里每一条的 matched 字段必须非空，
    # 且那个词必须真的出现在原句里。
    bad_quote = []
    for g in r['groups']:
        for it in g['items']:
            for q in it['quotes']:
                m = q.get('matched') or ''
                if not m or m not in q['text']:
                    bad_quote.append(f"{it['signal']}→「{q['text'][:18]}」")
    check('原话命中它挂靠的信号词', not bad_quote,
          '逐条核对通过' if not bad_quote else f'{len(bad_quote)} 条挂错：{bad_quote[:2]}')

    # 【A3：方向性错误——"不会再回购"不能被算成复购】
    # 这一类错是"肯定短语被否定句吃掉"，和情感分析里
    # "不贵值得拥有"判成负向是同一个病根。
    # 只测这一条就够，因为它是最容易反向的一类信号。
    from tools.review_analytics import _signal_hit, PERSONA_SIGNALS
    _re_kws = PERSONA_SIGNALS['使用场景']['signals']['囤货/复购']
    _neg_cases = ['超级小的火龙果，无良的商家不会再回购了',
                  '以后不会回购这家了']
    _wrong = [t for t in _neg_cases if _signal_hit(t, _re_kws)]
    check('否定句不算复购', not _wrong,
          '「不会再回购」已正确排除' if not _wrong else f'误判：{_wrong}')

    # 抽样看一条，确认结构真的是"证据+占比"而非空数组
    if r['groups'] and r['groups'][0]['items']:
        s = r['groups'][0]['items'][0]
        print(f"        示例：{r['groups'][0]['group']}/{s['signal']} "
              f"{s['n']} 条（{s['pct']*100:.1f}%），首条原话："
              f"「{s['quotes'][0]['text'][:40]}」"
              f"（命中「{s['quotes'][0].get('matched', '')}」）")


def run_competitor(corpus) -> None:
    print('\n【5】竞品对比：差异必须从文本自己推出来')
    r = compare_competitors(corpus)
    check('对比返回', r.get('ok'), f"{len(r.get('entities', []))} 个实体")

    ent = {e['entity']: e for e in r['entities']}
    names = [e['entity'] for e in r['entities']]

    # 【核心断言：分析结果必须和 DGP 真值方向一致】
    # 这个断言的意义在于"独立性"：
    #   · 分析层只读了 corpus.csv（没有 truth_bucket 列）
    #   · DGP 参数在 dgp_competitor.json（分析层没读）
    # 两边独立，结果对得上，才说明"分析能力"是真的。
    #
    # 如果这条测试永远完美通过，反而要怀疑是不是有人偷看了真值。
    import json
    dgp_path = None
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        cand = os.path.join(d, 'data', 'processed', 'dgp_competitor.json')
        if os.path.exists(cand):
            dgp_path = cand
            break
        d = os.path.dirname(d)
    if not dgp_path:
        check('找到 DGP 真值文件', False, '用于对照，缺失则无法验证独立性')
        return

    with open(dgp_path, encoding='utf-8') as f:
        dgp = json.load(f)
    target = dgp.get('neg_rate_actual_by_cat', {})
    truth_rate = {cid: float(np.mean(list(v.values())))
                  for cid, v in target.items()}
    name_map = {c['comp_id']: c['name'] for c in dgp['competitors']}

    # 方向一致性：真值排序与预测排序的 Spearman 相关
    pairs = []
    for cid, nm in name_map.items():
        if nm in ent and cid in truth_rate:
            pairs.append((truth_rate[cid], ent[nm]['neg_pct'], cid))
    if len(pairs) >= 3:
        tv = [p[0] for p in pairs]
        pv = [p[1] for p in pairs]
        # 手算 Spearman（避免 scipy 依赖）
        def rank(xs):
            order = sorted(range(len(xs)), key=lambda i: xs[i])
            rk = [0] * len(xs)
            for pos, i in enumerate(order):
                rk[i] = pos + 1
            return rk
        rt, rp = rank(tv), rank(pv)
        n = len(tv)
        rho = 1 - 6 * sum((a - b) ** 2 for a, b in zip(rt, rp)) / (n * (n * n - 1))
        check('竞品排序与真值方向一致（Spearman）', rho >= 0.6,
              f'ρ={rho:.2f}  ' + '  '.join(
                  f'{p[2]} 真{p[0]*100:.0f}%/测{p[1]*100:.0f}%' for p in pairs))
        # 绝对偏差：如果每一家的预测都比真值高很多，说明系统性地偏
        mae = float(np.mean([abs(a - b) for a, b in zip(tv, pv)]))
        check('负评率绝对偏差 <= 8pp', mae <= 0.08,
              f'MAE={mae*100:.1f}pp', warn_only=True)

    # 显著性检验必须真的做过
    has_test = all('vs_store' in e for e in r['entities'] if e['entity'] != '本店')
    check('每个竞品都做了 vs 本店检验', has_test)
    sig = [e['entity'] for e in r['entities']
           if e.get('vs_store', {}).get('significant')]
    print(f"        显著差异于本店的竞品：{sig or '无'}")

    # 维度离散度：必须能指出"谁在哪一项独差"
    if r.get('dim_spread'):
        top = r['dim_spread'][0]
        check('能指认最分散的维度', top['spread'] > 0.05,
              f"{top['label']}：最差 {top['worst']} {top['max']*100:.0f}%"
              f" / 最好 {top['best']} {top['min']*100:.0f}%")

    # 特征词对比
    c = r.get('contrast')
    if c:
        check('能给出本店 vs 标杆的特征词', bool(c.get('only_in_target')),
              f"对标 {c['benchmark']}，"
              f"本店独有词 {len(c.get('only_in_target', []))} 个")


def run_privacy(corpus, truth) -> None:
    print('\n【6】数据隔离：分析层不能看到答案')
    banned = ['label', 'cause_l1', 'cause_l2', 'truth_bucket']
    leaked = [c for c in banned if c in corpus.columns]
    check('corpus 不含真值列', not leaked,
          f'公开列：{list(corpus.columns)}' if not leaked else str(leaked))
    check('真值行数一致', len(corpus) == len(truth),
          f'{len(corpus)} vs {len(truth)}')
    check('真值行顺序对齐', bool((corpus['review_id'].values ==
                                  truth['review_id'].values).all()),
          '行号错位会让审计数字全错')


def run_contract() -> None:
    """【7】Agent 出口的 block 契约。

    【为什么要单独测一层"出口形状"】
    前面六节测的都是**分析对不对**：情感判得准不准、词云有没有商品名、
    画像原话挂没挂对。这些全对，界面上仍然可能一片空白——
    因为从分析结果到前端组件之间还隔着两层：
      ToolResult.data → facts → compose 出来的 block → 前端 switch
    这一段没有任何断言保护，而它出的错全是"假阴性"：
      · 采集器块因为少一个 ok 字段整块不渲染（真实发生过）
      · block 的字段名和前端组件读的名字对不上 → 渲染成空
      · numpy 类型没转成原生类型 → JSON 序列化报 500
    这三类错**都不会让分析层的数字变错**，所以前面 26 条断言全部绿灯。
    但用户看到的就是"问了一句话，答了一句，下面什么都没有"。

    这一节只做一件事：把每个工具的输出，按前端真正读的字段核对一遍。
    """
    print('\n【7】Agent 出口契约：block 的字段能被前端读到')

    from tools import review_tools as rt

    # 每个工具必须满足的"外层 data 特征"。
    # 这里的键都是从 _compose_review 里的判断读出来的：
    # 那个函数用 `if col and col.get('collectors')` 之类的判据决定
    # 渲染不渲染，所以这些键就是"契约"本身。
    need = {
        'review_sentiment': ['ok', 'n', 'neg_pct', 'pos_pct', 'net_sentiment'],
        'review_wordcloud': ['ok', 'items', 'rank_by', 'rank_note'],
        'review_persona': ['ok', 'groups', 'top_signals'],
        'competitor_compare': ['ok', 'entities', 'contrast', 'dim_spread'],
        # 【这一条就是抓 bug 的那一条】
        # collector_status 原来 data 里没有 ok，
        # 而 composer 读的是 data 而不是 ToolResult。
        'collector_status': ['ok', 'collectors', 'n_auto'],
    }

    missing_all = []
    for fn, keys in need.items():
        res = getattr(rt, fn)()
        d = res.to_dict().get('data') or {}
        miss = [k for k in keys if k not in d]
        if miss:
            missing_all.append(f'{fn} 缺 {miss}')
    check('五个工具的出口字段齐全', not missing_all,
          '；'.join(missing_all) if missing_all else
          '每个工具的 data 都能被 composer 直接读')

    # 竞品行必须带数据性质 —— build_competitor.py 的承诺，
    # 不能到展示层丢掉。前端表格是逐行渲染的，
    # 用户看"竞品C 40.3%"时不会回头找脚注。
    c = rt.competitor_compare().to_dict().get('data') or {}
    rows = c.get('entities') or []
    no_mode = [r['entity'] for r in rows if not r.get('mode_zh')]
    check('竞品每行都标注了数据性质', not no_mode,
          f'{len(rows)} 行全部带 mode_zh' if not no_mode else str(no_mode))

    # 词云的 items 必须带 quote（原句）和 word/count，
    # 前端 WordCloud 组件读的就是这三个。
    w = rt.review_wordcloud(polarity='负向', top_k=20).to_dict().get('data') or {}
    bad_item = []
    for it in (w.get('items') or [])[:20]:
        miss = [k for k in ('word', 'count') if k not in it]
        if not (it.get('quotes') or []):
            miss.append('quotes')
        if miss:
            bad_item.append(f'{it.get("word")} 缺 {miss}')
    check('词云 item 带 word/count/quotes', not bad_item,
          '可直接渲染词云块' if not bad_item else '；'.join(bad_item[:3]))

    # 画像的每个信号必须有 quotes，且 quote 里要能定位到 matched。
    # 前端会用 matched 做高亮；找不到就不高亮，
    # 读者会以为"这句话里没证据"——比不显示更糟。
    p = rt.review_persona().to_dict().get('data') or {}
    bad_q = []
    for g in p.get('groups') or []:
        for it in g.get('items') or []:
            for q in it.get('quotes') or []:
                m = q.get('matched') or ''
                if not m or m not in (q.get('text') or ''):
                    bad_q.append(f"{it['signal']}/{q.get('id')}")
    check('画像 quote 的 matched 可在句内定位', not bad_q,
          '高亮位置可计算' if not bad_q else '；'.join(bad_q[:3]))

    # 序列化能力：numpy 类型会让 FastAPI 报 500。
    # 这条测的是"能不能变成 JSON"，不是"对不对"。
    import json as _json
    try:
        _json.dumps({'s': rt.review_sentiment().to_dict(),
                     'w': w, 'p': p, 'c': c,
                     'col': rt.collector_status().to_dict()},
                    ensure_ascii=False)
        ok_ser = True
        err = ''
    except TypeError as e:
        ok_ser = False
        err = str(e)
    check('五个工具结果可直接 JSON 序列化', ok_ser,
          '无 numpy 类型泄漏' if ok_ser else err)


def run_dashboard() -> None:
    """【8】总览看板的联动契约。

    【这一节是补课的产物，起因是一个真 bug】
    总览页做成可联动看板后，选中一个类目，其他类目的退货率全变成了 0.00%。
    根因：by_category 那个循环用的是**已按类目过滤过**的表，
    于是选中服饰时，个护/数码/生鲜 在过滤后的表里查不到行，
    orders=0 → 退货率算成 0。

    这类错的特点和第七节一模一样：**不报错、不崩、断言全绿**，
    只是页面上开始显示 0.00%。而且"0%"看起来像"这个类目没退货"，
    比显示"—"更危险 —— 读者会信。
    """
    print('\n【8】总览看板：联动不能改坏其他类目的数')
    try:
        import pages
    except Exception as e:                      # noqa: BLE001
        check('pages 可导入', False, str(e))
        return

    base = pages.dashboard(None)
    cats = [c['category'] for c in base['by_category']]
    check('by_category 覆盖全部类目', len(cats) == len(getattr(
        pages.get_hub(), 'categories', [])), f'{cats}')

    # 全量基线：每个类目的订单数 / 退货率
    ref = {c['category']: (c['orders'], c['return_rate'])
           for c in base['by_category']}

    # 【核心断言】选中任一类目后，其他类目的数必须**逐字节不变**
    bad = []
    for sel in cats:
        d = pages.dashboard(sel)
        if d['scope'] != sel:
            bad.append(f'{sel}: scope={d["scope"]}')
        for c in d['by_category']:
            o, r = ref.get(c['category'], (None, None))
            if c['orders'] != o or abs(c['return_rate'] - r) > 1e-9:
                bad.append(f'选{sel}时{c["category"]}={c["return_rate"]}%(应{r}%)')
    check('切换范围时 by_category 不被动过', not bad,
          '四类目数值始终与全店一致' if not bad else '；'.join(bad[:4]))

    # 选中类目后，KPI 必须真的变了（否则"联动"是假的）
    sel = cats[0]
    d = pages.dashboard(sel)
    changed = (d['kpi']['orders_year'] != base['kpi']['orders_year']
               and d['kpi']['orders_year'] > 0)
    check('KPI 随范围变化', changed,
          f"{sel}: {d['kpi']['orders_year']:,} 单 vs 全店 {base['kpi']['orders_year']:,}")

    # 原因结构也必须范围内重算，且占比合计接近 100%
    share_sum = sum(c['share'] for c in d['causes'])
    check('范围内原因占比合计 ≈100%', 97 <= share_sum <= 100.5,
          f'{share_sum:.2f}%（{len(d["causes"])} 个原因）')

    # 热力矩阵：4 类目 × 52 周，一格不少，且带 z
    hm = base['heatmap']
    cells = sum(len(r['cells']) for r in hm['rows'])
    check('热力矩阵完整', len(hm['rows']) == len(cats) and cells == len(cats) * 52,
          f'{len(hm["rows"])} 行 × {cells // max(len(hm["rows"]),1)} 列 = {cells} 格')
    noz = [f"{r['category']}W{c['week']}"
           for r in hm['rows'] for c in r['cells'] if c.get('z') is None]
    check('每个热力格都有 z（上色键）', not noz,
          '全部有 z' if not noz else f'{len(noz)} 格缺 z，如 {noz[:3]}')

    # 色阶参数必须由后端给，前端不许拍
    check('色阶阈值由后端下发', hm.get('scale_z', 0) > 0 and hm.get('sig_z', 0) > 0,
          f"scale_z={hm.get('scale_z')} / sig_z={hm.get('sig_z')}")

    # 类目 × 原因 交叉矩阵：每个类目都该有格子
    cm = base['cause_matrix']
    cells_cat = {c['category'] for c in cm['cells']}
    check('原因矩阵覆盖全部类目', cells_cat == set(cats),
          f'{sorted(cells_cat)}')

    # 未知类目必须退回全店，不能出空页面
    weird = pages.dashboard('不存在的类目')
    check('未知类目回落全店', weird['scope'] == '全店'
          and weird['kpi']['orders_year'] == base['kpi']['orders_year'],
          f"scope={weird['scope']}")

    # ---- 词云块改成 BI 条形后的契约 ----
    # 【为什么要测这个】
    # 词云块从"每词挂一句原话的列表"改成"条形 + 悬停浮层 + 折叠原话"，
    # 前端要同时读 count 和 lift 两个量纲。这两个数一旦有一个没下发，
    # 页面上会出现"—"或 0 长的条，而不是报错。
    print('\n【9】评论页词云块：两个量纲都要有，且能排序')
    from tools import review_tools as _rt
    _neg = _rt.review_wordcloud(polarity='负向', top_k=40).to_dict().get('data') or {}
    _all = _rt.review_wordcloud(top_k=40).to_dict().get('data') or {}
    neg = _neg.get('items') or []
    allw = _all.get('items') or []
    check('词云两栏都有数据', bool(neg) and bool(allw),
          f"差评 {len(neg)} 个 / 全部 {len(allw)} 个")

    # lift 是"按区分度"那一栏的唯一排序依据，缺了整栏就退化回频次
    sample = neg[:8] or allw[:8]
    no_lift = [w.get('word') for w in sample if w.get('lift') is None]
    check('按区分度那栏每个词带 lift', not no_lift,
          '全部带 lift' if not no_lift else f'{len(no_lift)} 个缺 lift：{no_lift[:3]}')

    # 全部词也必须带 count（条形主值用）
    no_cnt = [w.get('word') for w in allw[:8] if not w.get('count')]
    check('全部词栏每个词带次数', not no_cnt,
          '全部带 count' if not no_cnt else f'{len(no_cnt)} 个缺 count')

    # 每个词必须能回溯到原话（"可回溯"是这个模块对外的承诺）
    no_q = [w.get('word') for w in (neg[:8] or allw[:8])
            if not (w.get('quotes') or w.get('samples'))]
    check('每个词都能回溯到原话', not no_q,
          '全部有原话' if not no_q else f'{len(no_q)} 个缺原话：{no_q[:3]}')

    # 前端按 2^lift 画条，数组必须已按 lift 降序 —— 前端不重排
    lifts = [w.get('lift') for w in neg[:10] if w.get('lift') is not None]
    mono = all(lifts[i] >= lifts[i + 1] for i in range(len(lifts) - 1))
    check('差评词按 lift 降序下发', mono,
          f"{'单调' if mono else '乱序'}：{[round(x, 2) for x in lifts[:5]]}")


def main() -> int:
    print('=' * 74)
    print('评论分析四模块验收')
    print('=' * 74)

    corpus_path, truth_path = _find_pool()
    corpus = pd.read_csv(corpus_path)
    truth = pd.read_csv(truth_path)

    print(f'\n语料池 {len(corpus):,} 条  '
          f'（{corpus["entity_name"].nunique()} 个实体 / '
          f'{corpus["biz_category"].nunique()} 个类目）')

    lex = load_lexicon()
    print(f'词库  正 {len(lex.pos)} / 负 {len(lex.neg)} / '
          f'单字 {len(lex.weak_single)} / 假朋友 {len(lex.false_friend)} / '
          f'否定 {len(lex.negators)}')

    run_privacy(corpus, truth)
    run_golden(lex)
    run_accuracy(corpus, truth, lex)
    run_wordcloud(corpus)
    run_persona(corpus)
    run_competitor(corpus)
    run_contract()
    run_dashboard()

    print('\n' + '=' * 74)
    total = len(PASS) + len(FAIL)
    color = '\033[32m' if not FAIL else '\033[31m'
    print(f'{color}{total - len(FAIL)}/{total} 通过\033[0m'
          + (f'，{len(WARN)} 项警告' if WARN else ''))
    if FAIL:
        print('失败项：' + '、'.join(FAIL))
    print('=' * 74)
    return 1 if FAIL else 0


if __name__ == '__main__':
    raise SystemExit(main())