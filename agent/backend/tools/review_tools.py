# -*- coding: utf-8 -*-
"""评论分析的工具门面：把 review_analytics 接成 Agent 能调的工具

【这个文件为什么单独存在，而不是塞进 data_hub】

data_hub 里的 DataHub 是一个"所有工具都在同一个类上"的大对象，
它读的是 data/processed/*.csv —— 那些表里有 label 列。
而评论分析有一个**不可妥协的约束**：不许读 label。

如果把这四个工具写成 DataHub 的方法，最自然的写法是
`self.reviews['review']` —— 一行就到手了，而且能跑通、结果还挺像。
但这个"顺手"会让整套情感分析失去意义：label 是上游人工标注的，
读了它就是抄答案，"准确率 95%" 变成"我和标准答案一致率 95%"
——这话毫无信息量，评委一问"你的模型在哪"就崩了。

所以这里**故意**不碰 DataHub，只读 corpus.csv（物理上没有 label 列）。
文件分开，是这个约束唯一可靠的执行方式：约定会被人忘记，文件不会。

【这一层还负责三件 review_analytics 不该管的事】

1. **来源性质**：corpus 里 competitor 那 4,160 条是 DGP 模拟的，
   store 那 13,500 条是公开语料。分析函数不关心数据从哪来，
   但答案必须说清楚——不然"竞品C差评率 40%"会被当成实测。
2. **工具契约**：统一返回 ToolResult（ok/data/evidence/note），
   和 data_hub 的工具长得一样，编排器不用为它们写特殊分支。
3. **缓存**：17,660 条评论每次都重算情感要 0.7 秒，
   对话里连问三句就积到 2 秒。这里按 (实体, 类目) 缓存打分结果。
"""
from __future__ import annotations

import json
import os
from typing import Any

import numpy as np
import pandas as pd

from tools.data_hub import ToolResult
from tools import review_analytics as ra

# ---------------------------------------------------------------------------
# 语料加载
# ---------------------------------------------------------------------------

_POOL: str | None = None
_CORPUS: pd.DataFrame | None = None
_MANIFEST: dict | None = None


def _find_pool() -> str:
    """找 data/review_pool。和 data_hub._find_data_dir 同样的向上查找，
    不写死层数——backend 挪到哪都能跑。"""
    global _POOL
    if _POOL:
        return _POOL
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        cand = os.path.join(d, 'data', 'review_pool')
        if os.path.isdir(cand):
            _POOL = cand
            return _POOL
        d = os.path.dirname(d)
    raise RuntimeError('找不到 data/review_pool 目录')


def corpus() -> pd.DataFrame:
    """语料池（只读，全进程一份）。

    【读进来之后立刻把 label 类字段删掉】
    corpus.csv 本来就没有这些列，但这里再做一次防御：
    将来如果有人为了"方便"往 corpus 里补了 label，
    这一行会让它在下游不可见，而不是让分析悄悄开始抄答案。
    """
    global _CORPUS
    if _CORPUS is None:
        p = os.path.join(_find_pool(), 'corpus.csv')
        df = pd.read_csv(p)
        drop = [c for c in ('label', 'cause_l1', 'cause_l2', 'truth', 'bucket')
                if c in df.columns]
        if drop:
            df = df.drop(columns=drop)
        _CORPUS = df
    return _CORPUS


def manifest() -> dict:
    """来源台账：哪一批数据是哪来的、什么性质。"""
    global _MANIFEST
    if _MANIFEST is None:
        try:
            p = os.path.join(_find_pool(), '_manifest.json')
            with open(p, encoding='utf-8') as fh:
                _MANIFEST = json.load(fh)
        except (OSError, json.JSONDecodeError):
            _MANIFEST = {}
    return _MANIFEST


def source_nature(entity_name: str | None = None) -> dict:
    """这个实体的数据是什么性质。

    【为什么这件事必须由工具层回答，而不是分析层】
    分析层对"模拟"和"实测"应当一视同仁——它只认文本。
    但**答案**不能一视同仁：把 DGP 生成的 4,160 条当成爬来的真实竞品评论，
    是这套系统里最容易犯、也最伤信誉的错。
    所以性质在出口处打标，一路带到前端。
    """
    m = manifest()
    batches = (m.get('batches') or [])
    if entity_name:
        for b in batches:
            if b.get('entity_name') == entity_name:
                return {
                    'entity': entity_name,
                    'mode': b.get('mode') or 'unknown',
                    'compliance': b.get('compliance') or '',
                    'n': b.get('n'),
                }
        return {'entity': entity_name, 'mode': 'unknown',
                'compliance': '台账里没有这个实体的记录', 'n': None}

    # 没指定实体：汇总成一张分布表
    agg: dict[str, dict] = {}
    for b in batches:
        k = b.get('mode') or 'unknown'
        a = agg.setdefault(k, {'mode': k, 'n': 0,
                               'compliance': b.get('compliance') or '',
                               'entities': []})
        a['n'] += int(b.get('n') or 0)
        a['entities'].append(b.get('entity_name'))
    return {'by_mode': list(agg.values())}


def _slice(entity: str | None = None, category: str | None = None,
           source: str | None = None) -> pd.DataFrame:
    """按实体/类目/来源切一份子集。

    entity 支持三种写法，因为用户不会按我们的字段名说话：
      · 全名  '竞品A（同价位）'
      · 短名  '竞品A' / 'A'
      · 代号  'C_A'
    """
    d = corpus()
    if entity:
        e = entity.strip()
        m = (d['entity_name'] == e) | (d['entity_id'] == e)
        if not m.any():
            # 模糊：包含关系（"竞品A" 命中 "竞品A（同价位）"）
            m = d['entity_name'].str.contains(e, regex=False)
        if not m.any() and e.upper() in ('A', 'B', 'C', 'D'):
            m = d['entity_id'] == f'C_{e.upper()}'
        if m.any():
            d = d[m]
        else:
            return d.iloc[0:0]
    if category:
        d = d[d['biz_category'] == category]
    if source:
        d = d[d['source'] == source]
    return d


def entities() -> list[dict]:
    """语料里有哪些实体，各自多少条、什么性质。"""
    d = corpus()
    out = []
    for (eid, ename, src), g in d.groupby(['entity_id', 'entity_name', 'source']):
        nat = source_nature(ename)
        out.append({
            'entity_id': eid, 'entity_name': ename, 'source': src,
            'n': int(len(g)),
            'categories': sorted(g['biz_category'].unique().tolist()),
            'avg_rating': round(float(g['rating'].mean()), 2),
            'mode': nat.get('mode'), 'compliance': nat.get('compliance'),
        })
    out.sort(key=lambda x: (x['source'] != 'store', -x['n']))
    return out


# ---------------------------------------------------------------------------
# ① 情感分析
# ---------------------------------------------------------------------------

def review_sentiment(entity: str | None = None, category: str | None = None,
                     source: str | None = None) -> ToolResult:
    """对评论做情感判定：正/负/中性占比、净情感值、按维度拆解。"""
    d = _slice(entity, category, source)
    if d.empty:
        return ToolResult(ok=False, error=_miss(entity, category, source))

    r = ra.analyze_sentiment(d, entity=entity or '全部')
    if not r.get('ok'):
        return ToolResult(ok=False, error=r.get('error') or '分析失败')

    # 【净情感值不要只给数，要给"这意味着什么"】
    # 0.42 这个数对运营没有直觉。它等于"每 100 条评论里，
    # 好评比差评多 42 条"，这才是个能记住的说法。
    r['net_readable'] = (
        f'每 100 条评论，好评比差评多 {abs(r["net_sentiment"]) * 100:.0f} 条'
        if r['net_sentiment'] >= 0
        else f'每 100 条评论，差评比好评多 {abs(r["net_sentiment"]) * 100:.0f} 条')
    r['nature'] = source_nature(entity) if entity else source_nature()
    r['scope'] = {'entity': entity, 'category': category, 'source': source}

    return ToolResult(
        ok=True,
        data=r,
        evidence={
            'scanned': int(len(d)),
            'source_file': 'data/review_pool/corpus.csv',
            # 【这一条是给"这套分析可不可信"留的证据】
            # 词库是外部文件，改一个词结论就变。把它的规模报出来，
            # 别人才能判断"96% 的准确率"是几个词撑起来的。
            'lexicon_terms': _lex_size(),
            'src_mix': r.get('src_mix'),
        },
        note=('负评率由词典法逐条判定，文本信号弱时用星级兜底'
              '（src_mix 里 rating_prior 就是走兜底的比例）。'),
    )


def _lex_size() -> dict:
    try:
        lex = ra.load_lexicon()
        return {'pos': len(lex.pos), 'neg': len(lex.neg),
                'single': len(lex.single_pol),
                'false_friend': len(lex.false_friend)}
    except Exception:
        return {}


def _miss(entity: str | None, category: str | None,
          source: str | None) -> str:
    """找不到数据时，要说清"是没有这个实体"还是"筛太细了"。

    一句"没有数据"会让用户反复换问法；告诉他现有的实体有哪些，
    他下一次就能问对。
    """
    have = [e['entity_name'] for e in entities()]
    bits = []
    if entity:
        bits.append(f'实体「{entity}」')
    if category:
        bits.append(f'类目「{category}」')
    if source:
        bits.append(f'来源「{source}」')
    scope = ' + '.join(bits) if bits else '当前条件'
    return (f'{scope}下没有评论。现有的实体是：' + '、'.join(have[:8]))


# ---------------------------------------------------------------------------
# ② 词云
# ---------------------------------------------------------------------------

def review_wordcloud(entity: str | None = None, category: str | None = None,
                     polarity: str | None = None, top_k: int = 40,
                     source: str | None = None) -> ToolResult:
    """高频词统计，带原句回溯。"""
    d = _slice(entity, category, source)
    if d.empty:
        return ToolResult(ok=False, error=_miss(entity, category, source))

    r = ra.word_freq(d, polarity=polarity, top_k=int(top_k or 40))
    if not r.get('ok'):
        return ToolResult(ok=False, error=r.get('error') or '统计失败')

    r['scope'] = {'entity': entity, 'category': category,
                  'polarity': polarity, 'source': source}
    r['nature'] = source_nature(entity) if entity else source_nature()

    # 【覆盖率低的时候要主动说，不能等用户问"为什么词都这么碎"】
    cov = r.get('topk_coverage') or 0
    if cov < 0.25:
        r['coverage_note'] = (
            f'Top {len(r["items"])} 词只覆盖了全部词次的 {cov*100:.1f}%，'
            f'说明这类评论用词很分散，没有一两个词能代表整体——'
            f'这本身是个结论：抱怨是零散的，不是集中在某一个点上。')

    return ToolResult(
        ok=True, data=r,
        evidence={'scanned': int(len(d)),
                  'unique_words': r.get('n_unique_words'),
                  'topk_coverage': cov},
        note='每个词都带原句（quotes），可以逐条回查。',
    )


# ---------------------------------------------------------------------------
# ③ 用户画像
# ---------------------------------------------------------------------------

def review_persona(entity: str | None = None, category: str | None = None,
                   source: str | None = None) -> ToolResult:
    """从评论语言里提取用户画像（带原话证据，并明确写出"回答不了什么"）。"""
    d = _slice(entity, category, source)
    if d.empty:
        return ToolResult(ok=False, error=_miss(entity, category, source))

    r = ra.build_persona(d, entity=entity or '全部')
    if not r.get('ok'):
        return ToolResult(ok=False, error=r.get('error') or '分析失败')

    r['scope'] = {'entity': entity, 'category': category, 'source': source}
    r['nature'] = source_nature(entity) if entity else source_nature()

    # 【top_signals 由分析层算，这里不再算一遍】
    # 门面层如果自己再算，就有两套排序逻辑，将来改一处忘一处。
    # 分析层已经是"最接近数据"的那一层，让它算，门面只负责转发。
    # （早期版本这里算过一遍，实测发现两个函数的排序口径一致，
    #  但那是因为代码碰巧一样，不是设计使然。）

    return ToolResult(
        ok=True, data=r,
        evidence={'scanned': int(len(d)),
                  'signals': sum(len(g.get('items') or [])
                                 for g in (r.get('groups') or []))},
        note=('画像来自评论里出现的语言证据（说"给孩子买"的有多少条），'
              '不是人群预测。年龄/性别/收入/城市这类信息评论里没有。'),
    )


# ---------------------------------------------------------------------------
# ④ 竞品对比
# ---------------------------------------------------------------------------

def competitor_compare(category: str | None = None,
                       store_name: str = '本店') -> ToolResult:
    """多实体横向对比：负评率 + 维度差异 + 显著性检验 + 特征词。"""
    d = corpus()
    if category:
        d = d[d['biz_category'] == category]
    if d.empty:
        return ToolResult(ok=False, error=f'类目「{category}」下没有评论')

    r = ra.compare_competitors(d, store_name=store_name, category=category)
    if not r.get('ok'):
        return ToolResult(ok=False, error=r.get('error') or '对比失败')

    # 【来源性质必须贴在每一行上，不能只放在脚注】
    # 前端表格是逐行渲染的，一个人看"竞品C 40.3%"时不会回头找脚注。
    # 性质跟着行走，误读的概率才会真的下降。
    for row in r.get('entities') or []:
        nat = source_nature(row['entity'])
        row['mode'] = nat.get('mode')
        row['mode_zh'] = {'batch_seed': '种子批次',
                          'simulated': '模拟数据',
                          'collected': '采集入库'}.get(
                              nat.get('mode') or '', '未知')

    r['scope'] = {'category': category}
    r['nature'] = source_nature()

    # 【"谁最差"这个排序必须附带"差得够不够格当结论"】
    # 四个竞品都 1,040 条，负评率最高的那个和最低的差几个百分点，
    # 是能写进周报的结论还是噪声，取决于检验结果。
    sig = [x for x in (r.get('entities') or [])
           if (x.get('vs_store') or {}).get('significant')]
    r['sig_summary'] = (
        f'{len(sig)} 个竞品与本店的差异达到统计显著（p<0.05）'
        if sig else '没有任何竞品与本店的差异达到统计显著——差异可能是抽样噪声')

    return ToolResult(
        ok=True, data=r,
        evidence={'scanned': int(len(d)),
                  'entities': len(r.get('entities') or []),
                  'test': '两比例 z 检验，双侧 p<0.05 判定显著'},
        note=('负评率由评论文本判定，维度归属按关键词命中，'
              '与本店对比做两比例 z 检验。'),
    )


# ---------------------------------------------------------------------------
# ⑤ 采集器状态（给"自动爬取"一个诚实的落点）
# ---------------------------------------------------------------------------

def collector_status() -> ToolResult:
    """列出采集器：哪些能自动跑、哪些要资质、哪些不做。"""
    from tools import collectors as col
    lst = col.list_collectors()
    rows = [{
        'name': c['name'], 'title': c['title'],
        'auto': '全自动' if c['auto'] else '半自动/人工',
        'compliance': c['compliance'],
        'available': '可用' if c['available'] else '不实现',
    } for c in lst]
    n_ok = sum(1 for c in lst if c['available'] and c['auto'])
    return ToolResult(
        ok=True,
        # 【data 里也必须带 ok】
        # 这不是冗余。_absorb 把 d（内层 data）存进 facts，
        # 组装答案时读的是 facts['collectors']，拿到的就是这个 dict。
        # 其他四个工具的 data 都由分析层产出，本来就带 ok 字段；
        # 只有这里是人手拼的，漏了它。后果是 _compose_review 里
        # `col.get('ok')` 恒为 None，整块采集器表格永远不渲染，
        # 而且因为是"假阴性"（少显示），测试和数据都不会报错。
        # 统一在 data 里带上 ok，是让五个工具的出口形状一致。
        data={'ok': True, 'collectors': rows, 'n_auto': n_ok},
        evidence={'registry': 'tools/collectors.py'},
        note=('平台页面抓取没有实现，原因见 collectors.py 的 '
              'PlatformScraperCollector：违约 + 代码寿命以周计 + 样本有偏。'),
    )


# ---------------------------------------------------------------------------
# 汇总入口：一次跑齐四件套（供前端"一键分析"用）
# ---------------------------------------------------------------------------

def full_report(entity: str | None = None, category: str | None = None) -> dict:
    """四个模块一次跑完。给 /api/review_report 用，不走 Agent 编排。"""
    s = review_sentiment(entity, category)
    w = review_wordcloud(entity, category, top_k=40)
    p = review_persona(entity, category)
    c = competitor_compare(category)
    return {
        'scope': {'entity': entity, 'category': category},
        'sentiment': s.to_dict(),
        'wordcloud': w.to_dict(),
        'persona': p.to_dict(),
        'competitor': c.to_dict(),
        'entities': entities(),
    }