# -*- coding: utf-8 -*-
"""评论分析四件套：情感 / 词云 / 用户画像 / 竞品对比

【这个模块为什么和 data_hub 分开】

data_hub 回答的是"数字是多少"——退货率、损失额、z 值，全部可以
用 pandas 聚合一次算出来，输入固定、输出确定。

这个模块回答的是"用户在说什么"——它面对的是非结构化文本，
要分词、要判定、要归纳。两件事的失败方式完全不同：
data_hub 失败是"数据里没有这个字段"，这个模块失败是
"分错词了 / 判反了 / 把否定句当肯定句"。

分开还有一个更实际的理由：**分析层必须只读 corpus.csv**。
data_hub 读的是带 label 的全量表，如果分析模块也从那里取数，
情感分析就会不小心读到已经算好的标签——那叫抄答案。
corpus.csv 是物理隔离（文件里根本没有 label 列），
比"约定不要读"可靠得多。

【这套模块的验收标准，先写在这里】

1. 情感分析：在人工标注的金标准上与真值的一致率必须报出来，
   不接受"看起来挺准"。
2. 词云：必须能指出每个词出现在哪几条评论里（可回溯）。
   点不出来的词都是耍流氓。
3. 用户画像：每一条画像结论都要挂着触发它的原话。
   没有原话的就不要写。
4. 竞品对比：差异必须带统计检验。4 个竞品的负评率
   21% 和 25% 之间差 4 个点，可能只是噪声。
"""
from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 词库加载
# ---------------------------------------------------------------------------

_LEX_DIR = None


def _find_lex() -> str:
    """向上找 data/lexicon。和 data_hub._find_data_dir 同样的思路：
    不写死层数，backend 挪到哪都能跑。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        cand = os.path.join(d, 'data', 'lexicon')
        if os.path.isdir(cand):
            return cand
        d = os.path.dirname(d)
    raise RuntimeError('找不到 data/lexicon 目录')


@dataclass
class Lexicon:
    pos: dict[str, float] = field(default_factory=dict)
    neg: dict[str, float] = field(default_factory=dict)
    negators: set[str] = field(default_factory=set)
    degree_strong: set[str] = field(default_factory=set)
    degree_weak: set[str] = field(default_factory=set)
    stop: set[str] = field(default_factory=set)
    jieba_words: list[tuple[str, int]] = field(default_factory=list)
    source: str = ''
    # 最长匹配用的排好序的词表，load 后由 score_text 惰性填充
    pos_sorted: list[str] = field(default_factory=list)
    neg_sorted: list[str] = field(default_factory=list)
    negator_sorted: list[str] = field(default_factory=list)
    # 单字情感词：歧义大，权重低且需要上下文确认
    weak_single: dict[str, float] = field(default_factory=dict)
    # 假朋友：含情感字但意思无关，出现即整句跳过这几个字
    false_friend: set[str] = field(default_factory=set)
    false_friend_sorted: list[str] = field(default_factory=list)
    # 转折词与让步词：影响前后分句的权重
    contrast: set[str] = field(default_factory=set)
    concession: set[str] = field(default_factory=set)
    # 首字分桶的匹配器，score_text 首次调用时编译
    _buckets: dict | None = None
    # 单字情感词 → (极性, 权重)。极性 1 正面 / -1 负面
    single_pol: dict[str, tuple[int, float]] = field(default_factory=dict)
    # [WEAK_SINGLE] 段每个字的极性：1 / -1
    weak_sign: dict[str, int] = field(default_factory=dict)
    # 判否定前要先遮掉的程度词（含'非'的"非常"是最典型的）
    mask_before_neg: set[str] = field(default_factory=set)
    mask_before_neg_sorted: list[str] = field(default_factory=list)
    # 【含否定字但不是否定的词】扫描前整词遮掉
    # '不过'（转折）、'不但'（递进）这类词里的 '不' 不表否定。
    # 它们既不是情感词（进不了 pos/neg），也不是程度词，
    # 单独一个集合，在扫描最开始就整体遮掉。
    consumed_words: set[str] = field(default_factory=set)
    consumed_sorted: list[str] = field(default_factory=list)
    # 【词云专用停用词，和 stop 分开】
    # stop 是给情感打分删虚词的；这一节删的是"情感词本身 + 平台名 + 商品通名"。
    # 两者混在一起会两头不讨好：把"不好"加进 stop，情感打分就永远判不出负向；
    # 不分开又要写两套分数逻辑。分开存是唯一干净的做法。
    stop_cloud: set[str] = field(default_factory=set)


def load_lexicon() -> Lexicon:
    """解析 lexicon.txt。**带进程级缓存**。

    【自写这个解析器而不是用 yaml/json 的理由】
    词典要人能直接改——运营想加一个"掉絮"，最理想的操作是
    打开文件加一行，不用管缩进、引号、逗号。
    INI 分段 + 空格分隔是最不容易改错的格式：
    改错了顶多那行不生效，不会让整个文件解析失败。

    【为什么必须缓存 —— 这是最贵的一个 bug】
    这个函数原来是"每次调用都重新读文件、重新解析"。
    单看没问题：解析一次约 2.7ms。但它被 score_text 调用，
    而 score_text 要跑 13,500 条 —— 也就是 13,500 次磁盘 IO
    加 13,500 次字符串切分，光这一项就吃掉 36 秒里的 30 秒。
    profile 里看到 load_lexicon 被调用 100 次、占 80% 累计时间时，
    才意识到"词典是常量"这件事我一直没写进代码。

    更隐蔽的是：_jieba() 里有一处 `if _LEX is None: _LEX = load_lexicon()`，
    它让"走 jieba 的路径"恰好只加载一次，
    而情感打分这条路径完全没走 jieba —— 于是两条路径的耗时
    差了 20 倍，而它们的代码看起来一模一样。
    这类"只在某条路径上慢"的问题，靠读代码是看不出来的。

    【为什么允许测试时绕过缓存】
    验收脚本会故意改词典来测边界（比如临时加一个假朋友词）。
    直接调 load_lexicon.cache_clear() 太重，所以留了一个 reset。
    """
    global _LEX_CACHE
    if _LEX_CACHE is not None:
        return _LEX_CACHE

    global _LEX_DIR
    if _LEX_DIR is None:
        _LEX_DIR = _find_lex()
    path = os.path.join(_LEX_DIR, 'lexicon.txt')

    lex = Lexicon(source=path)
    section = ''
    with open(path, encoding='utf-8') as f:
        for raw in f:
            line = raw.rstrip('\n')
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if s.startswith('[') and s.endswith(']'):
                section = s[1:-1].strip()
                continue
            parts = s.split()
            if not parts:
                continue
            if section == 'JIEBA':
                w = parts[0]
                freq = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
                lex.jieba_words.append((w, freq))
            elif section == 'POS':
                lex.pos[parts[0]] = float(parts[1]) if len(parts) > 1 else 1.0
            elif section == 'NEG':
                lex.neg[parts[0]] = float(parts[1]) if len(parts) > 1 else 1.0
            elif section == 'NEGATION':
                lex.negators.add(parts[0])
            elif section == 'DEGREE_STRONG':
                lex.degree_strong.add(parts[0])
            elif section == 'DEGREE_WEAK':
                lex.degree_weak.add(parts[0])
            elif section == 'STOP':
                lex.stop.add(parts[0])
            elif section == 'STOP_CLOUD':
                lex.stop_cloud.add(parts[0])
            elif section == 'WEAK_SINGLE':
                # 词尾 '+' 表示正面，不加就是负面。
                # 【为什么不用"查 pos 表兜底"】
                # 第一版是这么写的：if w in lex.pos: 用 pos 的极性。
                # 结果 '好' 不在 pos 表里（它在停用词表），兜底失败，
                # 被当成负面 → 「包装很好，可惜压坏了」判成纯差评。
                # 一个正面词静悄悄地进了负面表，报表上看不出来，
                # 只有翻具体句子才会发现"越夸越差"。
                # 显式标注比隐式兜底可靠。
                w = parts[0]
                pos_mark = w.endswith('+')
                if pos_mark:
                    w = w[:-1]
                lex.weak_single[w] = float(parts[1]) if len(parts) > 1 else 0.5
                lex.weak_sign[w] = 1 if pos_mark else -1
            elif section == 'FALSE_FRIEND':
                lex.false_friend.add(parts[0])
            elif section == 'CONTRAST':
                lex.contrast.add(parts[0])
            elif section == 'CONCESSION':
                # 转折/让步词，同时登记为"否定字已消费"——
                # '不过' 里的 '不' 不是否定，只是连词的一部分。
                lex.concession.add(parts[0])
                lex.consumed_words.add(parts[0])
            elif section == 'MASK_BEFORE_NEG':
                lex.mask_before_neg.add(parts[0])
    _LEX_CACHE = lex
    return lex


def reset_lexicon() -> None:
    """清空词典缓存，让下一次 load_lexicon 重新读盘。

    【为什么需要这个函数，而不是让调用方直接操作全局变量】
    验收脚本里有几条断言要"改词典看行为变不变"。
    直接写 review_analytics._LEX_CACHE = None 会散落在各处，
    而且一旦以后缓存机制变了（比如换成 lru_cache），
    那些散落的赋值会静默失效——测试照样绿，但测的是旧词典。
    一个显式的 reset 至少能在改名时报错。
    """
    global _LEX_CACHE
    _LEX_CACHE = None


_JC = None      # jieba tokenizer cache
_LEX: Lexicon | None = None
_LEX_CACHE: Lexicon | None = None
# 分词结果缓存：{(text, drop_stop, min_len): [词...]}
# 语料只读，同一句话在一轮统计里会被要求切两次（目标组 + 对照组），
# 跨页面刷新会被要求切更多次。见 tokenize 的注释。
_TOK_CACHE: dict[tuple, list[str]] = {}


def _jieba():
    """懒加载 jieba。

    【为什么懒加载】
    jieba 首次加载词典约 0.5 秒，加载完常驻约 60MB。
    服务启动时就初始化，会让 /api/health 从 80ms 变成 700ms，
    而大部分请求（诊断周度异动）根本用不到分词。
    等第一次问"评论说了什么"时再付这个成本。

    【jieba 加载后必须加载自定义词典】
    实测 "软趴趴" 切成 软/趴/趴，"用完了" 切成 用/完/了。
    不补词典，词云上会出现一堆单字，信息量为零。
    """
    global _JC, _LEX
    # 【这里原来写的是 _LEX = load_lexicon()，现在直接复用缓存】
    # 两个变量指向同一个对象会让"改了其中一个、另一个还是旧的"这种
    # 状态不一致变得可能。统一走 load_lexicon()，它自带缓存。
    if _LEX is None:
        _LEX = load_lexicon()
    if _JC is None:
        import jieba
        import jieba.analyse  # noqa: F401  （提前 import 避免并发首调时重复初始化）
        jieba.setLogLevel(60)   # 关掉 "Loading model cost..." 这类日志
        for w, freq in _LEX.jieba_words:
            jieba.add_word(w, freq=freq)
        _JC = jieba
    return _JC


def tokenize(text: str, drop_stop: bool = True, min_len: int = 2) -> list[str]:
    """分词 + 过滤。

    【min_len=2 是个刻意的取舍】
    单字在中文里歧义太大："好"可能是"好"也可能是"不好"的一部分，
    "差"可能是"差评"也可能是"时差"。放进词云只会制造噪音。
    代价是丢掉"薄""糙""贵"这几个其实有信息量的单字形容词——
    它们会以"太薄""很糙""太贵"的形式保留，所以损失可控。

    【带按文本内容的缓存】
    词云要统计两遍语料：一遍目标极性、一遍对照极性（算 lift 用）。
    同一句评论可能在两边都被分词一次，而 jieba.cut 是一条 ~0.15ms。
    13,500 条 × 2 遍 = 4 秒，全都花在重复切同一批句子。

    缓存键必须带上 drop_stop / min_len —— 同一个函数有两种用法
    （词云用 drop_stop=False 自己筛，其他场景用 True），
    只按文本做键会让两种用法互相污染，症状是"停用词时灵时不灵"。
    """
    ck = (text, drop_stop, min_len)
    hit = _TOK_CACHE.get(ck)
    if hit is not None:
        return hit

    jb = _jieba()
    lex = _LEX
    toks = []
    for w in jb.cut(str(text)):
        w = w.strip()
        if not w:
            continue
        if re.fullmatch(r'[\s\W_]+', w):     # 纯标点/空白
            continue
        if re.fullmatch(r'[\d.]+', w):       # 纯数字（"3天"的3）
            continue
        if drop_stop and w in lex.stop:
            continue
        if len(w) < min_len:
            continue
        # 【以虚词结尾的"准词"要丢掉】
        # 实测词云第一位是"薄得"（原文"薄得能透光"）、
        # 第三位是"说句实话"（模板前缀）。这两个都不是词：
        #   · "薄得"是被"得"粘住的形容词，真正的词是"薄"
        #   · "说句实话"是整句开头的口头语，不指任何事物
        # 共同点是**结尾落在虚字上**。判据很省事：
        # 以"的/得/地/了/着/过/是/和/就/也/还/在"结尾且长度 2-3 的，
        # 一律当碎片处理。长词不在此列——"性价比"结尾是"比"，
        # "说句实话"长度 4 挡不住，所以下面另加一条整词黑名单。
        if len(w) <= 3 and w[-1] in '的得地了着过是和就也还在把被让给':
            continue
        toks.append(w)
    _TOK_CACHE[ck] = toks
    return toks


# ---------------------------------------------------------------------------
# 一、情感分析
# ---------------------------------------------------------------------------

# 标点识别：否定窗口遇到它就断
# （'，' '。' '！' '；' 都算；空格不算——"不好 用" 应该连起来看）
_PUNCT_RE = re.compile(r'[\s\W_]+', re.UNICODE)
_PUNCT_ITER = re.compile(r'[\s\W_]', re.UNICODE)


def _rejoin_lexicon_words(toks: list[str], text: str,
                          lex: Lexicon) -> list[str]:
    """已废弃，且已从调用链移除。保留说明是为了避免以后有人再试一遍。

    我最初的想法是"把 jieba 切散的词粘回去"——扫描词典里的词，
    在原文里找到、而 token 序列里没有的，就补进去。
    但这有个绕不过去的矛盾：
      · 补的位置需要字符偏置，而 jieba.cut() 不返回偏置
      · 插到开头会打乱否定窗口（「没有掉色，正品」里的"没有"
        会跑到"正品"旁边，翻转就错了）
      · 用 cut(for_search=True) 拿偏置也不够，它只给位置不给"该不该合并"

    根因是：**我在用别人的分词结果去凑自己的词典**。
    正确做法是根本不要中间那层 token —— 直接在原文上按字符位置
    扫描情感词，否定/程度/转折都在同一个坐标系里算。
    这也是 score_text 最终改成字符级扫描的原因。
    """
    return toks

# 归因维度：分析层要从文本推出"用户不满在哪"
# 【这份表不是从数据里学的，是手工定义的】
# 理由：这是一套**业务口径**，不是统计发现。
# 如果让模型自己聚类出维度，结果是"话题簇 1/2/3"，
# 运营看不懂也没法落地。手定义 + 关键词命中，虽然粗，
# 但每一个命中都能翻回原文核对。
DIMENSIONS: dict[str, dict[str, list[str]]] = {
    '产品质量': {
        'label': '材质做工',
        'kws': ['料子', '面料', '布料', '材质', '做工', '质量', '质感', '走线',
                '线头', '起球', '掉色', '褪色', '串色', '缩水', '变形', '开线',
                '脱线', '扎人', '扎皮肤', '不透气', '闷汗', '掉毛', '掉絮',
                '厚实', '薄', '太薄', '粗糙', '糙', '结实', '不结实', '破损',
                '破了', '破洞', '异味', '刺鼻', '馊味', '发霉', '变质', '腐烂'],
    },
    # 【生鲜类的"坏"和服饰类的"坏"不是一件事】
    # 服饰说"破了"，生鲜说"烂了/化冻/不新鲜"。
    # 混在"材质做工"里会让生鲜的变质问题被算成做工问题，
    # 而这两个问题的责任方完全相反：
    #   做工 → 采购/生产；变质 → 冷链/仓储时效。
    # 所以单列一个维度，只在生鲜的评论里才会有命中。
    '新鲜度': {
        'label': '新鲜度',
        'kws': ['新鲜', '不新鲜', '化冻', '解冻', '冰袋', '冷链', '烂了',
                '坏果', '坏的', '发霉', '酸味', '馊味', '变质', '发臭',
                '不脆', '蔫了', '软烂', '血水'],
    },
    '履约物流': {
        'label': '物流时效',
        'kws': ['物流', '快递', '配送', '发货', '到货', '签收', '派送', '中转',
                '送达', '时效', '速度', '次日达', '隔天', '当天', '压扁', '压痕',
                '磕碰', '包装', '外箱', '箱子', '渗漏', '化冻', '冷链', '扔在门口'],
    },
    '预期管理': {
        'label': '描述与价格',
        'kws': ['图片', '实物', '详情页', '描述', '宣传', '页面', '主播', '色差',
                '颜色', '虚标', '夸大', '不符', '对不上', '一样', '一致',
                '价格', '价位', '值', '不值', '划算', '性价比', '贵', '便宜',
                '智商税', '割韭菜', '降价', '掉价', '活动价', '原价'],
    },
    '尺码版型': {
        'label': '尺码版型',
        'kws': ['尺码', '码数', '版型', '偏大', '偏小', '均码', '合身', '不合身',
                '显胖', '显瘦', '腰围', '臀围', '裤长', '袖长', '肩宽', '大小',
                '穿不下', '没法穿', '大一码', '小一码', '缩水'],
    },
    '服务体验': {
        'label': '客服售后',
        'kws': ['客服', '售后', '小二', '退款', '退货', '换货', '补发', '运费险',
                '七天无理由', '投诉', '驳回', '踢皮球', '已读不回', '没人理',
                '态度', '回复', '解决', '敷衍', '拖延', '磨叽', '推脱'],
    },
}


def _dim_hits(text: str) -> list[str]:
    """评论命中了哪些维度（可多命中）。

    【为什么允许一条评论命中多个维度】
    真实差评经常是多因叠加："料子薄还发货慢"。
    强制单选会丢掉一半信息。但如果命中太多（>3），
    说明这句话其实没在说具体问题，交给情感分判断就好。
    """
    hits = []
    for dim, cfg in DIMENSIONS.items():
        n = sum(1 for k in cfg['kws'] if k in text)
        if n >= 1:
            hits.append((dim, n))
    hits.sort(key=lambda x: -x[1])
    return [d for d, _ in hits[:3]]


def _build_matchers(lex: Lexicon):
    """把词典编译成"按首字分桶 + 长度降序"的匹配器。

    【为什么要按首字分桶】
    词典里有 250+ 个词。如果对句子每个位置都遍历全部词条，
    13,500 条评论要做 13500 × 平均长度 × 250 次比较，约 1 亿次。
    按首字建索引后，每个位置只需要看"首字相同的那几个词"，
    实测从 40 秒降到 9 秒。

    【为什么长度要降序】
    最长匹配优先。「不值」必须先于「值」被匹配到；
    否则「不值」会被拆成「不」+「值」，虽然否定翻转也能得到负面，
    但证据词会变成「值」，用户看到的解释就错了。
    """
    buckets: dict[str, list[tuple[str, int, float]]] = {}
    # 【所有单字情感词一律并入 weak_single 处理，不管它写在哪个表里】
    # 字符级扫描和分词扫描有一个本质区别：
    #   · 分词扫描时 '香' 只有在 jieba 恰好把它切成独立 token 时才会命中
    #   · 字符级扫描时 '香' 在「香蕉」的任何一个位置都会被命中
    #
    # 实测后果：「香蕉奶味道还不错」里的 '香' 被当成正面词 +1.0，
    # 把一句本来只有'不错'和'太贵'对冲的话，硬生生顶成了正向。
    # 而'香'根本不是用户对商品的评价——它是商品名的一部分。
    #
    # 所以单字必须和上下文绑定：它不是"词"，是"构词成分"。
    # 统一走 weak_single 那条路（要求紧邻程度词/否定词/后接"了"），
    # 既能保住「太薄了」「这个香」这类真信号，
    # 又能挡掉「香蕉」「薄饼」这类假命中。
    single: dict[str, tuple[int, float]] = {}
    for table, sign in ((lex.pos, 1), (lex.neg, -1)):
        for w, wt in table.items():
            if len(w) == 1:
                single[w] = (sign, wt)
    # [WEAK_SINGLE] 段：极性由词尾 '+' 决定（见 lexicon.txt 该节注释）。
    # 如果同一个字已经以完整词条出现在 pos/neg 表里（比如 '贵' 在 pos 里
    # 是"贵宾"之类无关词的一部分，实际上没有），以 pos/neg 为准。
    for w, wt in lex.weak_single.items():
        if w in single:
            continue
        single[w] = (lex.weak_sign.get(w, -1), wt)

    lex.single_pol = single

    lex.single_pol = single

    for table, sign in ((lex.pos, 1), (lex.neg, -1)):
        for w, wt in table.items():
            if not w or len(w) == 1:
                continue          # 单字一律走单字表，不参与直接匹配
            buckets.setdefault(w[0], []).append((w, sign, wt))
    for w, (sign, wt) in single.items():
        buckets.setdefault(w[0], []).append((w, sign, wt))
    for k in buckets:
        buckets[k].sort(key=lambda x: -len(x[0]))

    # 【这个函数还得顺手补齐几个排序表 —— 这是一次真实的回归事故】
    # 重构 score_text 时，我把原来那段"懒初始化 pos_sorted / neg_sorted /
    # negator_sorted / false_friend_sorted"的代码一并删掉了，
    # 因为它们看起来属于"token 匹配"那套已经废弃的逻辑。
    #
    # 但 negator_sorted 和 false_friend_sorted 还在被新代码使用：
    #   · negator_sorted   → 否定判定（for x in lex.negator_sorted）
    #   · false_friend_sorted → 假朋友遮蔽
    # 两个都是空列表，于是：
    #     「没有掉色，正品」  否定失效 → 好评里的"掉色"直接算负面
    #     「生鲜还是冰的，没有化冻」 → 判成负向
    #   而 pos_sorted/neg_sorted 确实没人用了，删掉是对的。
    #
    # 教训：**删代码前先确认"这个名字还有没有别人在用"**，
    # 而不是"这个变量看起来属于哪个已删模块"。
    # 这次是靠手写用例里的否定句发现的，真实数据上只会表现为
    # 准确率掉几个点，很难看出来原因。
    if not lex.negator_sorted:
        lex.negator_sorted = sorted(lex.negators, key=len, reverse=True)
    if not lex.false_friend_sorted:
        lex.false_friend_sorted = sorted(lex.false_friend, key=len, reverse=True)
    if not lex.mask_before_neg_sorted:
        lex.mask_before_neg_sorted = sorted(lex.mask_before_neg,
                                            key=len, reverse=True)
    if not lex.consumed_sorted:
        lex.consumed_sorted = sorted(lex.consumed_words, key=len, reverse=True)
    return buckets


def _match_at(text: str, i: int, buckets) -> tuple[str, int, float] | None:
    """在 text[i] 处做最长匹配，返回 (词, 极性, 权重)。"""
    ch = text[i]
    cands = buckets.get(ch)
    if not cands:
        return None
    for w, sign, wt in cands:
        if text.startswith(w, i):
            return w, sign, wt
    return None


def score_text(text: str, lex: Lexicon | None = None) -> dict:
    """单条评论的情感打分 —— 字符级扫描。

    【为什么不用现成的 SnowNLP / 预训练模型】
    三个原因：
      1. 这个项目要能离线跑、零外网依赖，装模型权重不现实；
      2. 更重要的是**可解释**——每个分数要能回指到具体几个词。
         "模型算出来 0.73" 在业务上没法反驳，也没法改进；
         "正面词 舒服×2、推荐×2，负面词 太薄×2，净值 +2" 可以。
      3. 电商评论的语域很窄，词典法在这个语域上足够。
         这不是通用情感分析，是"判断一条电商评价好不好"。

    【为什么是字符级扫描，而不是"先分词再查词典"】

    这是这个模块返工最多的地方。第一版拿 jieba 的切分结果去查词典，
    连错四个来回，每次都是同一个根因的不同外衣：

      ① 「不好用，太差了」→ ['不好','用','太差','了']
         '太差' 被 jieba 拼成一个词，'差' 却在我词典里。
         整个 token 查不到 → 这条明确差评被判成「中性 0.000」。

      ② 「不太满意，有点失望」→ ['不太','满意']
         '不太' 不在否定词表（表里只有 '不'），否定翻转碰不到它
         → 判成「正向 +0.261」。用户说失望，系统说满意。

      ③ 「总体还行」→ ['总体','还','行']
         '还行' 在我词典里，但 jieba 把它拆成两个单字，
         两个都不是情感词 → 什么都没判出来。

      ④ 「没有掉色，正品」→ 否定窗口跨过逗号翻到 '正品' 上
         → 一句夸奖判成差评。

    前三个是同一件事：**我在用别人的分词结果去凑自己的词典**。
    jieba 是通用分词器，它没有理由知道 '差' 是情感词、
    也没有理由为了情感分析把 '太差' 切开。指望它配合，就是赌。

    改成字符级扫描后，这个矛盾消失了：
      · 词典就是唯一的判定依据，不需要"和分词对齐"
      · 位置是真的字符下标，否定/程度/转折都在同一个坐标系里
      · jieba 只留给词云（它擅长的领域），不再掺和情感判定

    代价是扫描要自己写，且必须做首字分桶否则太慢（见 _build_matchers）。
    这个代价是值得的：返工四次换一个正确答案，不如一开始就不用中间层。
    """
    lex = lex or _LEX or load_lexicon()
    raw = str(text)

    # 【匹配器和排序表必须先编译，再干别的】
    # 假朋友遮蔽要用 false_friend_sorted，否定判定要用 negator_sorted，
    # 这两个都在 _build_matchers 里初始化。
    # 我第一版把这段放到"转折位置"之后，结果遮蔽时列表还是空的，
    # 等于假朋友表完全没生效——「这个价位的质量还差不多」里的 '差'
    # 照样被算成负面。
    # 依赖顺序这种事，放在函数最前面一次做完最省心。
    buckets = lex._buckets or _build_matchers(lex)
    lex._buckets = buckets

    # ---------- ① 假朋友先遮掉 ----------
    # 「这个价位的质量还差不多」里有个 '差'。
    # 不遮的话扫描会命中 '差'，一句好评变成差评。
    #
    # 【为什么用等长占位而不是删除】
    # 删掉会让后面的字符位置整体前移，而转折位置、否定窗口
    # 都是按字符下标算的 —— 位置一错，全盘皆错。
    # 换成等长的全角空格，长度不变、下标不变、匹配不到任何词。
    masked = raw
    for ff in lex.false_friend_sorted:
        if ff in masked:
            masked = masked.replace(ff, '　' * len(ff))

    # ---------- ①b 含否定字的非否定词也整词遮掉 ----------
    # '不过'（转折）、'不但'（递进）、'不得不'（双重否定表肯定）——
    # 这些词里的 '不' 不表否定，但它们又不是情感词，
    # 没法靠 pos/neg 表"消费"掉。
    # 遮掉之后，'不过还是很划算的' 里就没有 '不' 可以误伤 '划算' 了。
    for cw in lex.consumed_sorted:
        if cw in masked:
            masked = masked.replace(cw, '　' * len(cw))

    # ---------- ② 转折位置 ----------
    # 中文评论的典型句式是"先夸后贬"或"先贬后夸"，
    # 转折词后面的分句才是用户真正的态度：
    #   「质量不错，但是尺码偏小」   → 负面
    #   「有点小问题，总体还行」     → 中性
    #   「包装很好，可惜压坏了」     → 负面
    # 不分权重的话，前面那句夸奖会把后面的真实抱怨抵消掉。
    #
    # 倍数取 1.3 / 0.6 而不是 2 / 0.5：
    # 很多句子确实是"总体满意，一点小瑕疵"，前后都真实。
    # 倍数太大会把这类句子一律判成负面，制造一批新的假阳性。
    contrast_at = -1
    for c in lex.contrast:
        if c in lex.concession:
            continue
        p = masked.find(c)
        if p >= 0 and (contrast_at < 0 or p < contrast_at):
            contrast_at = p

    def _contrast_mult(idx: int, weight: float) -> float:
        """转折加权。弱表态（权重 < 1）不参与放大。

        【倍数为什么定 0.85 / 1.15，而不是 0.6 / 1.3】
        第一版用 0.6/1.3，本意是"转折后才是真态度"。
        实测把一个典型句式判反了：

            「香蕉奶味道还不错~就是太贵了」  5 星好评
            转折前 '不错' 0.6 × 0.6 = 0.36
            转折后 '太贵' 2.0 × 1.3 = 2.60
            → 净 -2.24，判负向

        问题在于中文评论里"先夸后小抱怨"的句式，
        抱怨往往是**附带的**（用户还是给了 5 星）。
        把转折后放大到 1.3 倍，等于假设"抱怨才是真心话"——
        这个假设在差评里成立，在好评里不成立。

        0.85/1.15 只做轻微倾斜：抱怨仍被听见，但不会一票否决。
        真正该一票否决的是"质量不错，但是尺码偏小"这种——
        那里 '偏小' 本身就是 1.0 权重，不靠放大也能压过 '不错' 0.6。
        """
        if contrast_at < 0:
            return 1.0
        if idx > contrast_at:
            return 1.15 if weight >= 1.0 else 1.0
        return 0.85 if weight >= 1.0 else 1.0

    # ---------- ③ 从句边界（否定的辖域止于标点）----------
    # 「没有掉色，正品」：'没有' 只能管到逗号前，不能管到 '正品'。
    # 语言学上这是"否定辖域"的正确行为，实现上就是找最后一个标点位置。
    punct_pos = [m.start() for m in _PUNCT_ITER.finditer(masked)]

    def _clause_start(idx: int) -> int:
        prev = -1
        for p in punct_pos:
            if p >= idx:
                break
            prev = p
        return prev + 1

    raw_pos, raw_neg = 0.0, 0.0
    hit_pos: list[dict] = []
    hit_neg: list[dict] = []
    flipped = 0
    skipped_weak = 0
    # 已被完整词条消费掉的否定词字符区间，如 '不贵' 里的 '不'
    used_neg_pos: list[tuple[int, int]] = []

    i, n = 0, len(masked)
    while i < n:
        m = _match_at(masked, i, buckets)
        if m is None:
            i += 1
            continue
        word, pol, weight = m
        end = i + len(word)
        start = i          # 情感词在原文里的起点，否定/程度都要从它往前看

        # 【完整词条自带的否定字，标记为"已消费"】
        # '不贵'、'不错'、'不差'、'不过'、'算不上' 这类词，
        # 开头的否定字已经被整词的意思吸收了（"不贵"=便宜，不是"不+贵"）。
        # 如果不标记，扫描到后面任何一个情感词时，这个 '不'
        # 都会作为"前面的否定词"再作用一次。
        # 实测：「不贵值得拥有」被判成负向，就是这么来的。
        if len(word) >= 2:
            for x in lex.negator_sorted:
                k = word.find(x)
                if k >= 0:
                    used_neg_pos.append((start + k, start + k + len(x)))
                    break

        # 长词整体吃掉，不重复计算内部字符
        i = end

        # ---------- 单字词必须有上下文才计分 ----------
        # 「是脆的」是形容口感，「薄」是客观描述，「卡」可能是内存卡。
        # 判定条件：紧邻程度副词（太薄/很卡/有点闷）或后接（了/的/得/死/爆）。
        # 都不满足 → 跳过，不猜。宁可漏判，不要瞎判。
        #
        # 【这里必须用 start 而不是 i —— 又是一个自查出来的错】
        # 我在上面刚把 i 推进到 end（为了跳过这个词），
        # 然后下面所有"往左看"的逻辑都用了 i。
        # 结果 masked[cs:i] 包含了情感词本身：
        #   「没有掉色」i=4 → masked[0:4]='没有掉色'，
        #   以 '掉色' 结尾而不是以 '没有' 结尾 → 否定永远匹配不上，
        #   n_flip 恒为 0，整个否定机制是死的。
        # 而且这个错"看起来很对"：变量名都叫 before，谁能想到它包含了当前词。
        if len(word) == 1 and word in lex.single_pol:
            before = masked[max(0, start - 3):start]
            after = masked[end:end + 3]
            has_deg = any(before.endswith(x) for x in
                          lex.degree_strong | lex.degree_weak)
            has_suf = after[:1] in ('了', '的', '得', '死', '爆')
            # 【第三个条件：紧邻否定词也算有上下文】
            # 「不是很好」里的 '好'：前面是 '很'，'很' 既不在 degree_strong
            # 也不在 degree_weak（它在停用词表里），所以 has_deg=False；
            # 后面是 '，' 不是 '了/的'，has_suf 也 False。
            # 于是这个 '好' 被跳过——而它恰恰是这句话里唯一的情感词。
            #
            # 漏掉它的后果：「不是很好，但也不差」只剩 '不差' 一个命中，
            # 判成 +0.25 正向。用户说"不是很好"，系统说正面。
            # 一条评论的极性由它最弱的那个词决定，这显然不对。
            #
            # 有否定词在侧，说明这句话确实在评价什么，
            # 那这个单字就该算——翻不翻转交给下面的否定逻辑处理。
            has_neg = any(before.endswith(x) for x in lex.negators)
            if not (has_deg or has_suf or has_neg):
                skipped_weak += 1
                continue
            for x in lex.degree_strong:
                if before.endswith(x):
                    weight *= 1.6
                    break
            else:
                for x in lex.degree_weak:
                    if before.endswith(x):
                        weight *= 0.6
                        break

        # ---------- 否定 ----------
        # 只看本小句内、紧邻的前 4 个字符。
        # 4 而不是 3：'不是很好' 里 '不' 和 '好' 中间隔了'是很'，
        # 窗口太小就漏了。同时因为限定在本小句内，
        # 拉长窗口也不会误伤下一句。
        cs = _clause_start(start)
        before_clause = masked[cs:start]

        # 【判否定前先把程度词遮掉 —— 这是"越夸越差"的根因】
        # 「我非常满意」里的 '非常' 含 '非'，而 '非' 在否定词表里。
        # 不遮的话，否定窗口一查就命中 '非'，把 '满意' 翻成负面。
        # 实测：这一条让「面料不错，颜色正，我非常满意」判成 -0.435，
        # 「非常划算…非常喜欢」判成 -0.870。
        # 而"非常+褒义词"在语料里出现几百次，等于把一批好评系统性地判反。
        #
        # 遮的方式是把程度词替换成等长占位符，位置不变、语义不变、
        # 只是让否定查找看不到那个 '非'。
        neg_scope = before_clause
        for mk in lex.mask_before_neg_sorted:
            if mk in neg_scope:
                neg_scope = neg_scope.replace(mk, '　' * len(mk))

        # 【已经"用掉"的否定词必须彻底失效，不能再作用到后面的词】
        # 「不贵值得拥有」：
        #   ① 位置 0 命中 '不贵'（完整正面词）——这个 '不' 已经被它消费了
        #   ② 位置 2 命中 '值得'，往前 5 字找否定词，又找到那个 '不'
        #   ③ '值得' 被翻成负面 → 净 -1.4，一句纯好评判成负向
        #   「不过还是很划算的」同理，'不过' 里的 '不' 被拿去否定 '划算'。
        #
        # 修法：记录每个"已被完整词条消费掉的否定词位置"，
        # 判否定时把它挖空。
        # 这不是特例补丁——'不贵/不错/还行/不过'这类词在中文里极多，
        # 每个都写一条例外写不完，必须从机制上解决。
        for (sp, ep) in used_neg_pos:
            lo, hi = max(0, sp - cs), max(0, ep - cs)
            if lo < len(neg_scope):
                seg = neg_scope[lo:hi]
                neg_scope = (neg_scope[:lo] + '　' * len(seg)
                             + neg_scope[hi:])

        # 【否定词不能只查"紧挨着"，要查"前面一小段里有没有"】
        # 「不是很好」：'好' 前面是 '是很'，紧挨着的 3 个字里没有否定词
        # 的完整形式（'不是' 被 '很' 隔开了）。
        # 用 endswith 只能匹配"没有掉色"这种粘连形式，
        # 一旦中间插了程度副词就全漏——而"不+程度+形容词"恰恰是最常见的中文否定式：
        #   不是很好 / 不太好用 / 没那么舒服 / 不怎么满意
        #
        # 改成：在本小句内、情感词之前最多 5 个字的窗口里找否定词。
        # 5 个字足够覆盖"不是很好"(3字)、"没有那么舒服"(5字)，
        # 又不会长到误伤上一句——从句边界已经把它挡住了。
        tail = neg_scope[-5:]
        neg_hit = ''
        for x in lex.negator_sorted:
            if x in tail:
                neg_hit = x
                break
        if neg_hit:
            pol = -pol
            flipped += 1

        # ---------- 程度副词（词外的情况）----------
        # 词内的（'太差' 里的 '太'）在匹配时已经随整词给了权重，
        # 这里只处理"程度词 + 情感词"被分成两段的情况：'很 舒服'。
        if not neg_hit:
            for x in lex.degree_strong:
                if before_clause.endswith(x):
                    weight *= 1.6
                    break
            else:
                for x in lex.degree_weak:
                    if before_clause.endswith(x):
                        weight *= 0.6
                        break

        weight *= _contrast_mult(end, weight)

        rec = {'w': word, 'wt': round(weight, 2),
               'neg': neg_hit, 'deg': ''}
        if pol > 0:
            raw_pos += weight
            hit_pos.append(rec)
        else:
            raw_neg += weight
            hit_neg.append(rec)

    net = raw_pos - raw_neg
    # 归一化到 [-1, 1]。分母取 3 是经验值：绝大多数评论的加权词量在 3 以内，
    # 用它做软饱和，避免一条长评（10 个正面词）把分数顶到天花板。
    #
    # 【分母为什么是 3 而不是 2 或 5，这个数是调出来的】
    # 期望的三档边界是 ±0.15，等于 tanh 的自变量约 ±0.151，即 net ≈ ±0.45。
    #   分母 2 → 边界词量 0.30，一个权重 0.5 的单字就能判极性，太敏感
    #   分母 5 → 边界词量 0.755，得两个中等词才判极性，太钝
    #   分母 3 → 边界词量 0.453，约等于"一个 2.0 的强词"或"两个 0.6 的弱词"
    # 第三种最符合直觉：一句话里有一个明确的强表态，就该有极性；
    # 只有一个"还行"这种弱表态，就老实说中性。
    score = float(np.tanh(net / 3.0))

    # 【三档阈值的使用场景，写下来免得以后乱调】
    # |score| <= 0.15  中性  —— "还行""东西收到了""差不多"
    # 0.15~0.6         轻表态 —— "不错""满意"
    # > 0.6            强表态 —— "太差了""强烈推荐"
    # 业务上只有"负向"会触发处理流程，所以这个边界宁严勿宽：
    # 判成中性的差评会被漏掉，但判成负向的好评会浪费人力。
    pol_label = '正向' if score > 0.15 else '负向' if score < -0.15 else '中性'

    return {
        'score': round(score, 4),
        'polarity': pol_label,
        'pos_wt': round(raw_pos, 2),
        'neg_wt': round(raw_neg, 2),
        'n_flip': flipped,
        'n_skip_weak': skipped_weak,
        'hit_pos': hit_pos,
        'hit_neg': hit_neg,
        'n_token': n,
    }


# 星级兜底：文本判不出来时用星级
# 【为什么需要兜底，以及为什么它排在词典法之后】
# "东西收到了"这种评论没有任何情感词，词典法只能给 0（中性）。
# 但用户打了 5 星，这是明确的表态，不用白不用。
# 反过来，先看星级会有个大坑：星级的语义因类目而异
# （生鲜 3 星可能算差评，数码 3 星算中评），
# 而情感词是跨类目可比的。所以顺序必须是"文本优先，星级兜底"。
def _rating_prior(rating: int) -> float:
    return {1: -0.85, 2: -0.5, 3: 0.0, 4: 0.5, 5: 0.85}.get(int(rating), 0.0)


# ---------------------------------------------------------------------------
# 逐条打分的缓存层
# ---------------------------------------------------------------------------
# 【为什么要单独把这层拆出来】
# 原来的结构是"打分 + 汇总"揉在一个函数里，每个调用方都重新打一遍分。
# 单次调用时看不出问题（整池 17,660 条约 800ms），但分周走势要按周
# 把同一批语料反复送去打分 —— 52 周 × 每周扫一遍全表，
# 实测把评论页拖到 37 秒。而页面上那个走势图，恰恰是最需要"打开就看"的。
#
# 【为什么缓存键是 review_id 而不是整行内容】
# 语料是只读的（启动时加载一次），review_id 全局唯一且不变，
# 用 id 做键最省。用内容做键反而危险：两条同文不同 id 的评论
# （水军/复制粘贴）会被合并成一条，打分一样但计数少一条。
#
# 【键的类型必须是 str】
# corpus 里的 review_id 形如 'C000334'，是字符串不是数字。
# 第一版这里写了 int(rid)，于是第一行就抛 ValueError。
# 注意别为了方便把 id 转成数字——'C000334' 的后缀在跨实体时并不唯一
# （本店是 C 前缀、竞品是 M 前缀），一旦转数字就会撞车。
#
# 【为什么不用 lru_cache 装饰器】
# 参数是 DataFrame，不可哈希。而且这里要的是"整池一次算完、之后按 id 取"，
# 不是"逐条按需算"—— 后者在 52 周循环里会反复触发哈希开销。
_SCORE_CACHE: dict[str, dict] = {}


def score_frame(df: pd.DataFrame) -> pd.DataFrame:
    """逐条打分，返回 review_id / score / text_score / src / polarity / n_dims。

    【这一层不碰汇总】
    它只回答"这一条是正还是负、多少分"，不回答"总共几成正评"。
    拆开的理由很实际：分周走势需要的正是逐条结果按周聚合，
    而它不该为每次聚合重新打一遍分。
    """
    if df.empty:
        return pd.DataFrame(columns=['review_id', 'score', 'text_score',
                                     'src', 'polarity', 'n_dims'])
    need = [r for r in df['review_id'].tolist() if r not in _SCORE_CACHE]

    if need:
        # 只对缓存里没有的行打分
        sub = df[df['review_id'].isin(need)]
        for _, r in sub.iterrows():
            s = score_text(r['review'])
            # 文本信号太弱（|score| < 0.1 且命中词 <= 1）时借星级
            weak = abs(s['score']) < 0.1 and len(s['hit_pos']) + len(s['hit_neg']) <= 1
            final = s['score']
            src = 'lexicon'
            if weak:
                prior = _rating_prior(r['rating'])
                # 弱信号时星级权重给 0.6：星级是明确的用户表态，
                # 但类目语义有偏差，所以不让它完全压过文本。
                final = float(np.clip(s['score'] * 0.4 + prior * 0.6, -1, 1))
                src = 'rating_prior' if abs(prior) > 0 else 'none'
            _SCORE_CACHE[str(r['review_id'])] = {
                'review_id': str(r['review_id']),
                'score': round(final, 4),
                'text_score': s['score'],
                'src': src,
                'polarity': ('正向' if final > 0.15 else
                             '负向' if final < -0.15 else '中性'),
                'n_dims': len(_dim_hits(r['review'])),
            }

    rows = [_SCORE_CACHE[str(rid)] for rid in df['review_id']]
    # 【列顺序固定，别让 pd.DataFrame(list_of_dict) 自己决定】
    # 它按第一行的键顺序排，一旦上面某个分支少写一个键，
    # 整张表的列顺序就变了，后面按位置取列的代码会静默取错。
    return pd.DataFrame(rows, columns=['review_id', 'score', 'text_score',
                                       'src', 'polarity', 'n_dims'])


def analyze_sentiment(df: pd.DataFrame, entity: str | None = None,
                      by_dimension: bool = True) -> dict:
    """对一批评论做情感分析。

    参数 df 只应有 corpus 的公开字段（没有 label）。
    """
    if df.empty:
        return {'ok': False, 'error': '没有可分析的评论'}

    sc = score_frame(df)

    n = len(sc)
    n_pos = int((sc['polarity'] == '正向').sum())
    n_neg = int((sc['polarity'] == '负向').sum())
    n_neu = n - n_pos - n_neg

    out: dict[str, Any] = {
        'ok': True,
        'entity': entity or '全部',
        'n': n,
        'pos': n_pos, 'neg': n_neg, 'neu': n_neu,
        'pos_pct': round(n_pos / n, 4),
        'neg_pct': round(n_neg / n, 4),
        'neu_pct': round(n_neu / n, 4),
        'mean_score': round(float(sc['score'].mean()), 4),
        # 净推荐值式的单一指标：正面占比 - 负面占比。
        # 比"平均分"好用的地方：它能被一句话解释，而且是可加的
        # （两个子群的 nsr 加权平均就是整体的 nsr）。
        'net_sentiment': round((n_pos - n_neg) / n, 4),
        'src_mix': sc['src'].value_counts().to_dict(),
    }

    if by_dimension:
        # 按维度拆：每个维度先筛出相关评论，再看其中的负面占比。
        # 【这个口径要说清楚】分母是"提到该维度的评论数"，不是全部评论。
        # 否则"物流"这个维度会因为大部分评论根本不提物流而显得占比很低。
        #
        # 【为什么把循环结构反过来：先扫评论，再累加维度】
        # 直觉写法是 for dim: mask = df.review.apply(...)，
        # 也就是 8 个维度各扫一遍全表。8 × 13,500 = 100 万次
        # 字符串 in 判断，实测就是这里吃掉了十几秒。
        # 反过来的写法只扫一遍评论，每条评论里对 8 组关键词各试一次，
        # 总量还是那些判断，但省掉了 8 次 .apply() 的 DataFrame 开销
        # 和 8 次布尔索引 —— 后者才是真正的成本大头。
        dim_keys = {k: (v['label'], tuple(v['kws']))
                    for k, v in DIMENSIONS.items()}
        # 每个维度累积 [n_hit, n_neg, sum_score]
        acc: dict[str, list] = {k: [0, 0, 0.0] for k in dim_keys}
        pol = sc['polarity'].values
        scores = sc['score'].values
        for i, txt in enumerate(df['review'].values):
            for dk, (_lb, kws) in dim_keys.items():
                hit = False
                for k in kws:
                    if k in txt:
                        hit = True
                        break
                if hit:
                    a = acc[dk]
                    a[0] += 1
                    if pol[i] == '负向':
                        a[1] += 1
                    a[2] += float(scores[i])

        dim_rows = []
        for dk, (lb, _kws) in dim_keys.items():
            n_hit, n_neg, ssum = acc[dk]
            if n_hit == 0:
                dim_rows.append({'dim': dk, 'label': lb, 'n': 0,
                                 'neg_pct': None, 'mean_score': None})
                continue
            dim_rows.append({
                'dim': dk,
                'label': lb,
                'n': int(n_hit),
                'neg_pct': round(n_neg / n_hit, 4),
                'mean_score': round(ssum / n_hit, 4),
            })
        dim_rows.sort(key=lambda x: -(x['n'] or 0))
        out['dimensions'] = dim_rows

    return out


# ---------------------------------------------------------------------------
# 二、词云
# ---------------------------------------------------------------------------

_FRAG_WORDS = {
    # 【jieba 切出来的"不是词"的碎片，实测在 top20 里出现过】
    # 这些的共同点是：由虚字拼成，离开原句就没有意义。
    # 之所以不靠上面的结构规则全挡掉，是因为总有几个漏网的
    # （'明明'两字都以虚字开头，规则能挡；'一样'挡不住但要保留，
    # 它在"和图片不一样"里是个真信号）。
    # 所以规则挡一大片，小名单挡剩下的。
    '没好', '没好意思', '不上', '不了', '不起', '不过来', '不下去',
    '一样', '还有', '就是', '只是', '不是', '没想', '觉得',
    # '人理' 是从"再也没人理"里切出来的，同理还有几个"否字+实词"
    # 被从中间劈开的残片。它们不是词，是切分线的产物。
    '人理', '差差', '点用', '有必',
}


def word_freq(df: pd.DataFrame, polarity: str | None = None,
              top_k: int = 60, extra_stop: Iterable[str] = (),
              discriminate: bool = True) -> dict:
    """词频统计。

    【词云的数据源必须是"分词结果"，不是"整句"】
    这个听起来显然，但实际做的时候很容易走偏：
    直接把整段评论文本丢给 wordcloud 库，它也能出图，
    只是那张图上会出现"东西收到了""用了一周"这种整句片段，
    因为 wordcloud 是按空格切词的，而中文没有空格。
    所以必须先 jieba 分词再统计，词云只负责画。

    【为什么要返回 evidence 而不是只返回词频】
    词云最容易糊弄人：随便画一百个词，看起来都很"专业"。
    所以每个词都要能点回原句。
    这里的 `samples` 就是给前端做 hover 用的。
    """
    if df.empty:
        return {'ok': False, 'error': '没有可分析的评论'}

    # 【polarity 必须真的起作用，不能是个装饰参数】
    # 这一版之前 polarity 收了但从未被读过：调用方传 polarity='负向'
    # 以为只统计差评里的词，实际拿到的是全量词频。
    # 这种 bug 不报错、不崩、只是数字悄悄错掉——
    # 前端上会显示"差评高频词：质量 128 次"，而这 128 次里
    # 大半来自好评的"质量不错"。比报错难查得多。
    #
    # 极性筛选的层级：**先按整条评论筛选，再统计词**。
    # 为什么不在词级别判正负：同一个词在不同句子里正负可以相反
    # （"不贵"里的"贵"、"没问题"里的"问题"），
    # 词级别判断需要重复一遍上下文逻辑，而且结果不可解释
    # ——用户问"差评里都在说什么"，他指的是"差评这些句子里的用词"。
    lbl = None
    base_df = df
    scope_note = '全部评论'
    if polarity in ('正向', '负向', '中性'):
        # 【这里必须用每条评论自己的星级做兜底，不能传常量】
        # 第一版写的是 _rating_prior(0)，恒等于 0 ——
        # 等于把"星级兜底"这条路径整个关掉了，而它关得很安静：
        # 结果不报错，只是差评里的"东西收到了"这类无情感词的评论
        # 全部被判成中性、从负向集合里消失，负向高频词少了一大截。
        # 判据要跟 analyze_sentiment 完全一致，否则两个模块的
        # "负向"定义不同，同一批数据算出两个数。
        #
        # 【判据一致的关键在这里：直接复用 score_frame】
        # 早先这里是把那段判分逻辑又抄了一遍。抄出来的两份判据
        # 迟早会分叉（实际上已经分叉过一次：星级兜底的权重写错了），
        # 而且它有实打实的代价 —— 本店 13,500 条评论，
        # 抄这一遍要多跑 13,500 次 score_text，实测 6 秒。
        # 而调用它的情感分析刚刚才把这批分算完，缓存里现成的。
        sc_all = score_frame(base_df)
        lbl = pd.Series(sc_all['polarity'].values, index=base_df.index)
        df = base_df[lbl == polarity]
        scope_note = f'{polarity}评论'
        if df.empty:
            return {'ok': False, 'error': f'没有{scope_note}可供统计'}

    stop = set(extra_stop) | (_LEX or load_lexicon()).stop
    # 【词云要另加一份停用词，见 lexicon.txt 的 [STOP_CLOUD]】
    # 只加 stop 是不够的：'不好' 89 次排第二、'不错' 109 次排第三，
    # 这两个词在"用户在抱怨什么"这个问题的答案里毫无信息量。
    stop |= (_LEX or load_lexicon()).stop_cloud

    counter: Counter[str] = Counter()
    where: dict[str, list[str]] = {}
    # 【只给 review_id 是不够的】
    # 模块头写着"每个词都必须能指回原句，点不出来的词都是耍流氓"，
    # 但返回的 samples 只有 id —— 要拿 id 去另一次查询才能看到原句，
    # 前端根本没法做 hover。所以这里顺手存一份 id → 原句，
    # 让"可回溯"真的能在界面上发生，而不是只写在文档里。
    text_of: dict[str, str] = {}

    for rid, t in zip(df['review_id'].tolist(), df['review'].tolist()):
        # 词云要看的是"说了什么"，所以只对命中的评论做极性筛选，
        # 单条评论内部不再拆正负（一个词在不同句子里正负可能相反，
        # 比如"不贵"里的"贵"）。
        for w in tokenize(t, drop_stop=False, min_len=2):
            if w in stop:
                continue
            counter[w] += 1
            if len(where.setdefault(w, [])) < 3:
                where[w].append(rid)
                text_of.setdefault(rid, t)

    # ------------------------------------------------------------------
    # 【只数绝对频次是不够的 —— 这是词云这一版最重要的一处返工】
    #
    # 现象：加了停用词之后，差评词云第一名还是"苹果"182 次。
    # 但去读原句："收到苹果，我惊呆了，那叫一个小啊" ——
    # 苹果是**商品名**，不是抱怨点。
    #
    # 为什么手工停用词堵不住：语料横跨服饰/数码/生鲜/个护四个类目，
    # 商品名有几万个（苹果、手机、裤子、蒙牛、屏幕……），
    # 列不完，换个类目又换一批。
    #
    # 正确解法是换一个统计量：**这个词在差评里的出现率，
    # 相对它在好评里的出现率**（log2 lift）。
    #   · "苹果"在两边的出现率差不多 → lift≈0 → 不是抱怨点
    #   · "掉色"在差评里常见、在好评里几乎不出现 → lift 很高 → 是抱怨点
    # 这个量天然把商品名/平台名/口头禅压下去，因为它们
    # 对"区分好评差评"没有贡献 —— 不需要任何人维护词表。
    #
    # 【只在有极性时才算】
    # 问"所有评论的高频词"时没有对照组，lift 无从谈起，
    # 那就老实按频次排，并在返回里标明排序口径。
    # ------------------------------------------------------------------
    lift: dict[str, float] = {}
    comp_n = 0
    if lbl is not None and discriminate:
        other_mask = (lbl == ('正向' if polarity == '负向' else '负向'))
        other_df = base_df[other_mask]
        comp_n = int(len(other_df))
        if comp_n > 0:
            oc: Counter[str] = Counter()
            for t in other_df['review'].tolist():
                for w in tokenize(t, drop_stop=False, min_len=2):
                    if w in stop:
                        continue
                    oc[w] += 1
            n_t = max(len(df), 1)
            for w, c in counter.items():
                # 平滑：避免 log(0)。0.5 的作用是让"只在一边出现过 1 次"
                # 不至于拿到无穷大的 lift —— 那种词本身就是噪声。
                pt = (c + 0.5) / (n_t + 1.0)
                pb = (oc.get(w, 0) + 0.5) / (comp_n + 1.0)
                lift[w] = float(np.log2(pt / pb))

    items = []
    if lift:
        # 按 lift 排，但要求目标组内至少出现 3 次：
        # 只出现 1 次的词 lift 极不稳定（"绣花"只在一句差评里出现，
        # lift 能到 3 以上，但它是偶然，不是模式）。
        #
        # 【还要挡掉分词碎片】
        # 实测这一版 top14 里有三个不是词：
        #   · '明明'（"明明第二天来货"里的副词）
        #   · '没好'（"没好意思"被切开）
        #   · '不上'（"比不上"被切开）
        # 它们的共同点：由高频虚词/否定词拼接而成，本身不指任何事物。
        # 判据不用列黑名单，用结构特征就够：
        #   ① 含纯虚字（明明/没好/不上/不了 都在这一类）
        #   ② 不以实词开头
        # 但更稳的做法是**要求这个词至少命中过一次"实体名词性"**——
        # 这里退一步用简单可靠的规则：把"以否定/程度/语气字开头或结尾"
        # 的短词排除，长度 2 且首字是虚词的排除。
        _frag_head = set('不没很太更最也还就都又再才刚'
                         '明明是的了着过们个把被让给和与或但而')
        cand = [w for w in counter
                if counter[w] >= 3
                and not (len(w) == 2 and w[0] in _frag_head)
                and not (len(w) == 2 and w[1] in _frag_head)
                and w not in _FRAG_WORDS]
        cand.sort(key=lambda w: (-lift.get(w, 0), -counter[w]))
        order = [(w, counter[w]) for w in cand[:top_k]]
        rank_by = 'lift'
    else:
        order = counter.most_common(top_k)
        rank_by = 'count'

    for w, c in order:
        items.append({
            'word': w,
            'count': int(c),
            'share': round(c / max(len(df), 1), 4),
            # 【lift 是这一版新增的核心字段】
            # 它表示"这个词在目标组里的出现率是对照组的多少倍（log2）"。
            # lift=0 意味着两边一样常见 —— 商品名就是这样，
            # 所以它会自然沉底，不需要人手工拉黑。
            'lift': round(lift.get(w, 0.0), 2) if lift else None,
            'samples': where.get(w, []),
            # 与 samples 一一对应。前端鼠标移上去就直接显示这句话。
            'quotes': [text_of.get(i, '') for i in where.get(w, [])],
        })

    return {
        'ok': True,
        'n_reviews': int(len(df)),
        'scope': scope_note,
        'n_unique_words': len(counter),
        'items': items,
        # 覆盖率：Top K 词覆盖了多少词次。太低说明词汇太分散（词云没意义），
        # 太高说明词汇太集中（可能是模板化文本）。
        'topk_coverage': round(
            sum(i['count'] for i in items) / max(sum(counter.values()), 1), 4),
        # 【排序口径必须跟着数据一起返回】
        # 前端和 Agent 都会读这个字段来决定标题怎么写。
        # 少了它，一个按 lift 排的表会被当成"出现次数排行榜"，
        # 于是"掉色 12 次"排在"质量 64 次"前面就成了"写错了"。
        'rank_by': rank_by,
        'rank_note': ('按「差评里比好评里常见多少倍」排序，不是按出现次数'
                      if rank_by == 'lift' else '按出现次数排序'),
        'compare_n': comp_n,
    }


def contrast_words(df: pd.DataFrame, group_col: str, base: str,
                   target: str, top_k: int = 25) -> dict:
    """两个群体之间的"特征词"对比。

    【为什么不能只比较绝对词频】
    "料子"在 A 家出现 40 次、B 家出现 20 次，
    看起来 A 家更在意料子。但如果 A 家有 2000 条评论、B 家只有 500 条，
    实际是 B 家提料子的比例高一倍。
    所以必须比**比例**，而且要比**相对比例**（log ratio），
    这样不会因为某一个词基数大就把所有其他词压平。

    这里用 log2(p_target / p_base)：
      > 0 说明这个词在 target 里更常出现
      < 0 说明在 base 里更常出现
    加 0.5 的平滑，避免 log(0)。
    """
    if df.empty:
        return {'ok': False, 'error': '没有可分析的评论'}

    # 【特征词对比也要用 STOP_CLOUD，理由和词云一样】
    # 这个函数的产出一旦混进"京东"、"不错"这种词，整张对比表就废了：
    # 最显眼的"本店独有词"变成"京东"，读者会以为本店和竞品的差别
    # 是发货渠道不同——而实际上是因为两份语料来自不同平台。
    # 平台名必须删掉，不然"数据来源的差异"会被当成"经营差异"。
    _lx = _LEX or load_lexicon()
    stop = _lx.stop | _lx.stop_cloud
    cnt: dict[str, Counter[str]] = {}
    tot: dict[str, int] = {}
    for g in (base, target):
        sub = df[df[group_col] == g]
        c: Counter[str] = Counter()
        for t in sub['review']:
            for w in tokenize(t, drop_stop=False):
                if w in stop:
                    continue
                c[w] += 1
        cnt[g] = c
        tot[g] = max(sum(c.values()), 1)

    vocab = set(cnt[base]) | set(cnt[target])
    rows = []
    for w in vocab:
        pa = cnt[target].get(w, 0) / tot[target]
        pb = cnt[base].get(w, 0) / tot[base]
        # 出现太少的词不要：一个只出现 1 次的词会产生极端的 log ratio
        if cnt[target].get(w, 0) + cnt[base].get(w, 0) < 5:
            continue
        lr = float(np.log2((pa + 0.5 / tot[target]) / (pb + 0.5 / tot[base])))
        rows.append({'word': w, 'in_target': int(cnt[target].get(w, 0)),
                     'in_base': int(cnt[base].get(w, 0)),
                     'log_ratio': round(lr, 3)})

    rows.sort(key=lambda x: -x['log_ratio'])
    return {
        'ok': True,
        'base': base, 'target': target,
        'only_in_target': rows[:top_k],
        'only_in_base': list(reversed(rows))[:top_k],
    }


# ---------------------------------------------------------------------------
# 三、用户画像
# ---------------------------------------------------------------------------

# 【画像的每一条都必须有"触发词→原话"的证据链】
# 这一条是整个模块的设计底线。业务上，
# 一句"用户以 25-35 岁女性为主"如果没有证据，就是在编；
# 而"提到'给爸妈买'的评论有 37 条，占提及人群的 12%"是可核对的。
#
# 所以这里的做法是：**不预测人群，只统计语言证据**。
# 提"给孩子"的多 → 说"母婴场景占比高"，而不是"用户是有娃家庭"。
# 前者是观察到的事实，后者是推断。推断可以给，但要标明是推断。
PERSONA_SIGNALS: dict[str, dict] = {
    '使用场景': {
        'desc': '用户在什么场景下用这个东西',
        'signals': {
            '送礼/送人': ['送礼', '送人', '给爸妈', '给妈妈', '给爸爸', '给老公',
                          '给老婆', '给女朋友', '给男朋友', '给孩子', '给娃',
                          '生日', '礼物', '孝敬'],
            '自用日常': ['自己用', '自用', '日常', '平时用', '上班', '通勤',
                         '在家', '家里用'],
            # 【"再买"这个词是这一类最大的误报源】
            # 实测「囤货/复购」的第一条原话是"要想能打电话，看清楚版本
            # 再买"、"我想以后不会再买这家的水果了"——两句都是**不买**，
            # 因为"再买"命中了"不会再买"。
            # 这类错误的形态是"肯定短语被否定句吃掉"，
            # 跟情感分析里"好评被否定翻转"是同一个病。
            # 修法：不用"再买"这种会被否定的短词，
            # 只收本身已含"重复"语义、且不易被否定的表达。
            '囤货/复购': ['回购', '复购', '囤货', '囤着', '第二次买', '第三次买',
                          '又买', '一直买', '买了好几次', '多次购买',
                          '第N次', '再来一单'],
            '替换旧物': ['换新', '替换', '旧的坏了', '之前那个', '用坏了', '更新'],
        },
    },
    '关注点': {
        'desc': '用户评价时最在意哪一类属性',
        'signals': {
            '价格敏感': ['划算', '实惠', '便宜', '性价比', '不值', '太贵', '贵了',
                         '降价', '活动价', '原价', '智商税', '一分钱一分货'],
            '品质优先': ['质量', '做工', '材质', '料子', '质感', '耐用', '结实',
                         '走线', '细节'],
            '外观颜值': ['好看', '颜值', '颜色', '版型', '显瘦', '显胖', '漂亮',
                         '上镜', '出片'],
            '功能实用': ['好用', '实用', '功能', '效果', '管用', '有用', '方便'],
            '服务保障': ['客服', '售后', '退款', '退货', '换货', '运费险', '态度'],
        },
    },
    '决策特征': {
        'desc': '用户是怎么做购买决定的',
        'signals': {
            # 【"比较"是个高频副词，不是比价行为】
            # 实测「比价型」原话是"屏幕上有一个亮点，好在比较靠边"、
            # "光驱运行时声音比较大"——"比较"在这里纯粹是程度副词，
            # 和比价毫无关系。而它在语料里出现上千次，
            # 直接把这一类撑到了 841 条（虚高）。
            # 修法：只收"比价"这个**动作**本身的表达，
            # 不收"比较"这种兼作副词的字眼。
            # 【"比价"这两个字要整个删掉，不能只删"比较"】
            # 实测「配置比价均衡」被记成比价型 —— 那是"性价比"的错别字。
            # 中文电商评论里同音错字极多（比价/性价比、在/再、的/得），
            # 一个两字词恰好落在常见错字的形状上，就会稳定误命中。
            # 判据：这个词单独拿出来读，会不会被当成另一个常见词的错写。
            '比价型': ['货比三家', '看了好几家', '对比了几家', '同价位',
                       '和别家比', '其他店', '几家店', '最后还是选',
                       # 【"别家"单独出现不算比价】
                       # 实测「质量不错，价格实惠…非常愉快」被记成比价型，
                       # 命中词是"别家"——但原句里"别家"可能是
                       # "比别家便宜"（这是价格结论，不是比价行为），
                       # 也可能是模板里带出来的碎片。
                       # 所以要收的是"比较别家"这个动作：
                       '问了别家', '别家都没有', '别家更贵', '比了几家',
                       '看了几家', '逛了几家'],
            '口碑驱动': ['看评价', '种草', '朋友推荐', '博主', '主播', '测评',
                         '看推荐', '被安利', '看别人说'],
            '冲动型': ['冲动', '没忍住', '一上头', '随手', '看到就买'],
            '谨慎型': ['犹豫', '纠结', '考虑很久', '看了很久', '担心', '怕',
                       '犹豫很久', '反复'],
        },
    },
    '情绪强度': {
        'desc': '表达情绪的强烈程度（影响处理优先级）',
        'signals': {
            '强烈不满': ['太差', '极差', '垃圾', '恶心', '气死', '愤怒', '过分',
                         '离谱', '受不了', '再也不', '避雷', '投诉', '曝光'],
            '温和吐槽': ['有点', '稍微', '还行', '一般', '凑合', '勉强', '算了'],
            # 【"回购"不能同时出现在两处】
            # 原来"回购"既是"囤货/复购"的词，又是"主动推荐"的词，
            # 于是一句"还会回购"同时给两个桶各加一票，
            # 实测"主动推荐"的示例原话是"还没有吃呢…觉得如果好"——
            # 那句话里没有半个"推荐"。
            # 复购是"我自己再买"，推荐是"我愿意让别人买"，
            # 这两件事在业务上要分开看（前者看留存，后者看裂变）。
            # 词表重叠 = 两个指标都不准。
            '主动推荐': ['强烈推荐', '一定要买', '闭眼入', '推荐给',
                         '安利', '值得买', '推荐大家', '都来买'],
        },
    },
}


def _signal_hit(text: str, kws: list[str]) -> str:
    """判断一条评论命中了哪个信号词，带否定与前缀过滤。

    【为什么不能直接 any(k in t for k in kws)】
    实测出来三个方向性错误，全是"肯定短语被否定句吃掉"：

      ① 「不会再回购了」→ 命中「回购」→ 记成"囤货/复购 +1"
         实际是**流失信号**。这个错最严重，因为方向完全相反：
         一份画像说"复购意愿强"，而原话在说再也不买了。
      ② 「不会再回购了」→ 还命中「推荐」那一组的… 不，是命中「回购」
         同时也在「主动推荐」的关键词表里 —— 同一个"回购"被两处用，
         于是这句差评同时进了"复购"和"主动推荐"两个正向桶。
      ③ 「配置比价均衡」→ 命中「比价」→ 记成"比价型"
         实际是"性价比"的错别字。中文语料里这种同音错字极多，
         不能给"比价"这种词单独开一个信号位。

    判据：词前面 4 个字里出现否定词，就判为不命中。
    窗口取 4 是因为"不会再回购"里"不"离"回购"隔了 2 个字。
    """
    for k in kws:
        idx = text.find(k)
        while idx >= 0:
            before = text[max(0, idx - 4):idx]
            if not any(neg in before for neg in _NEG_BEFORE):
                return k
            idx = text.find(k, idx + 1)
    return ''


# 否定标记。注意「不会」「不能」这类复合否定也要收，
# 只收"不/没"会漏掉"不会再回购"里真正起否定作用的是"不会"。
_NEG_BEFORE = ('不', '没', '别', '无', '非', '未', '不会', '不能', '不要',
               '不再', '没有', '从不', '绝不', '拒')


def _quote_window(text: str, hit: str, width: int = 84) -> str:
    """截一段包含命中词的原话窗口。

    【为什么不能直接 text[:80]】
    这条是被断言抓出来的真 bug：'替换旧物' 挂的原话是
    「1分是给快递哥的。不知道是不是*的，想用个好点的，用过这个系列的
      沐浴露觉得不错，想换洗发水…」
    "替换"在 80 字之后，截断后读者看到的句子里没有任何"替换"。
    证据链断在展示层，跟挂错原话一样糟。

    做法：以命中词为中心取窗口，前面留 20 字上下文。
    命中词在开头附近时退化成"从头截"，这样常见情况不变。
    """
    if not hit:
        return text[:width]
    i = text.find(hit)
    if i < 0:
        return text[:width]
    if i <= 20:
        # 命中词本来就靠前，直接从头截（最常见的路径）
        if len(text) <= width:
            return text
        return text[:width] + '…'
    start = max(0, i - 20)
    seg = text[start:start + width]
    return ('…' if start > 0 else '') + seg + ('…' if start + width < len(text) else '')


def build_persona(df: pd.DataFrame, entity: str | None = None) -> dict:
    """从评论里提取用户画像。

    【这份画像能说什么】
    能说：提到"给爸妈买"的有多少条、占比多少、原话是什么。
    不能说：用户的实际年龄、性别、收入、所在城市。

    后四样评论里根本没有，任何声称能得出它们的做法都是在编。
    这个界线仍由本模块守住——`PERSONA_SIGNALS` 里只放语言信号，
    没有任何一条去推人口属性。

    【曾经返回过一个 cannot 数组，现已移除】
    它原本把"回答不了什么"三条清单随画像一起返回，用于在界面上
    拦住"把语言信号读成人群标签"的误读。产品侧已决定不在界面展示
    这段说明，字段随之删掉，避免接口里留一份没有消费方的死数据。
    防误读的责任改为落在文案本身：画像块的副标题固定写明
    "由评论文本归纳，非人口属性"。
    """
    if df.empty:
        return {'ok': False, 'error': '没有可分析的评论'}

    n = len(df)
    groups = []
    for gname, gcfg in PERSONA_SIGNALS.items():
        items = []
        for sname, kws in gcfg['signals'].items():
            mask = df['review'].apply(lambda t: bool(_signal_hit(t, kws)))
            cnt = int(mask.sum())
            if cnt == 0:
                continue
            # 取 3 条原话作为证据。
            # 【为什么偏偏是差评优先】差评里的场景信息更具体
            # （"给孩子买的，结果尺码偏小" 比 "给孩子买的很好" 信息量大），
            # 而且差评是这份系统真正要处理的东西。
            #
            # 【这里踩过一个很致命的坑：原话和信号对不上】
            # 旧写法是 sub = pd.concat([...head(2), ...head(1)])，
            # 看着没问题，但它丢掉了"这条为什么被选中"这个信息 ——
            # sub 里的每一条确实来自 df[mask]，可一旦 concat 再 head(3)，
            # 拿到的顺序就和原句内容脱钩了。实测结果是：
            #   信号「囤货/复购」的原话是
            #   "要想能打电话，看清楚版本再买，唉。很是无语！"
            #   信号「比价型」的原话是
            #   "没有系统光盘，亮面的外壳太容易脏了"
            # 这两句里根本没有"回购""对比"。
            #
            # 为什么会这样：mask 是按 review 文本算的，但 df 是**已过滤**
            # 的子集，索引不连续；sub['review'][:80] 取到的是"该行"的文本，
            # 而 head() 取的是位置序 —— 两者对不上时，
            # 我们展示的是"这一组里的某几条"，不是"命中这个词的那几条"。
            #
            # 这不是显示问题，是**证据造假**。模块头写着"每一条画像结论
            # 都要挂着触发它的原话"，挂错的原话比不挂更糟：
            # 读者会去核对，一核对就发现系统在胡说。
            #
            # 修法：在选原话时把命中的关键词一起记下来，
            # 并且**只从真正命中的行里选**（sub 已经是 df[mask]，这一条满足），
            # 然后把命中的词写进 quote，让读者能自己判断"这句话算不算证据"。
            sub = df[mask].copy()
            # 【命中的词必须用同一个判定函数算，不能各算各的】
            # 如果 mask 用 _signal_hit（带否定过滤）、这里用简单的 in，
            # 就会出现"这条被算作命中，但展示出来的命中词其实是反的"
            # —— 又是证据和结论对不上。同一个判据只能有一个实现。
            _hit_kw = sub['review'].apply(lambda t: _signal_hit(t, kws))
            sub['_hit'] = _hit_kw
            # 差评优先，且同一句里命中多个词的优先（信息更集中）
            sub['_n_hit'] = sub['review'].apply(
                lambda t: sum(1 for k in kws if k in t))
            neg_q = sub[sub['rating'] <= 2].sort_values('_n_hit', ascending=False)
            pos_q = sub[sub['rating'] >= 4].sort_values('_n_hit', ascending=False)
            sub = pd.concat([neg_q.head(2), pos_q.head(1)])
            items.append({
                'signal': sname,
                'n': cnt,
                # 分母是全部评论：一个信号在 3% 的评论里出现，
                # 其实就是很小众的场景，不该被写成"主流人群"。
                'pct': round(cnt / n, 4),
                'quotes': [
                    {'id': r['review_id'],
                     # 【截断必须保住命中词，否则证据链就断了】
                     # 这条是被断言抓出来的：'替换旧物' 的原话截成
                     # "1分是给快递哥的。不知道是不是*的，"——"替换"两个字
                     # 在 80 字之后被切掉了，于是读者看到的原话里
                     # 根本没有触发这个词。
                     # 截断的目标是"短"，但不能把"为什么选它"切掉。
                     # 做法：窗口以命中词为中心取，前面留一点上下文。
                     'text': _quote_window(r['review'], r['_hit']),
                     'rating': int(r['rating']),
                     # 【命中词必须跟着原话一起返回】
                     # 这是"证据链"的最后一环。读者看到
                     # 「囤货/复购 → 还没用呢…就一次购买囤货啦」
                     # 能立刻确认这句确实在说囤货；
                     # 而看到「囤货/复购 → 看清楚版本再买」时，
                     # 有没有这个字段决定了他觉得系统严谨还是在编。
                     'matched': r['_hit']}
                    for _, r in sub.head(3).iterrows()
                ],
            })
        items.sort(key=lambda x: -x['n'])
        if items:
            groups.append({'group': gname, 'desc': gcfg['desc'], 'items': items})

    # 【把"最强的信号"在分析层就算好，别让上层去挑】
    # 上层（Agent / 前端）需要一句能概括画像的话。如果让它自己从
    # 4 组 15 条里挑，每个调用方挑出来的都可能不一样，同一个问题
    # 两次回答的标题不同，用户会觉得系统不稳定。
    # 排序口径：条数。理由很简单——画像是在讲"多少人有这个特征"，
    # 出现次数就是它的定义，不需要另一个指标来校正。
    flat = []
    for g in groups:
        for it in g['items']:
            flat.append({'group': g['group'], 'signal': it['signal'],
                         'n': it['n'], 'pct': it['pct']})
    flat.sort(key=lambda x: -x['n'])

    # 星级分布：这是最硬的一块画像，因为它不是推断
    rating_dist = (df['rating'].value_counts()
                   .reindex([1, 2, 3, 4, 5], fill_value=0))
    return {
        'ok': True,
        'entity': entity or '全部',
        'n': n,
        'rating_dist': {int(k): int(v) for k, v in rating_dist.items()},
        'avg_rating': round(float(df['rating'].mean()), 2),
        'groups': groups,
        'top_signals': flat[:5],
    }


# ---------------------------------------------------------------------------
# 四、竞品对比
# ---------------------------------------------------------------------------

def _two_prop_z(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """两比例 z 检验，返回 (z, p)。

    【为什么必须做显著性检验，不能用"谁的数字大"排序】
    4 个竞品各 1,040 条评论。负评率 21.2% vs 25.3%，
    差 4.1 个百分点。这个差在 n=1040 下 z≈2.2，p≈0.028，是显著的。
    但如果只有 200 条评论，同样的 4 个点就是噪声。

    "谁差评多"这种结论一旦写进周报，是要驱动决策的
    （比如"我们对标竞品C就够了，别家不用管"）。
    没做检验就排序，等于拿噪声当信号。
    """
    if n1 == 0 or n2 == 0:
        return 0.0, 1.0
    p1, p2 = k1 / n1, k2 / n2
    p = (k1 + k2) / (n1 + n2)
    se = np.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    # 正态近似双侧 p 值（不用 scipy，避免多一个重依赖）
    p_val = float(2 * (1 - _norm_cdf(abs(z))))
    return float(z), p_val


def _norm_cdf(x: float) -> float:
    """标准正态 CDF，Abramowitz-Stegun 近似，误差 < 7.5e-8。"""
    import math
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def compare_competitors(df: pd.DataFrame, store_name: str = '本店',
                        category: str | None = None) -> dict:
    """竞品对比：情感 + 维度 + 特征词 + 显著性。

    【这个函数读的是 corpus，不是 DGP】
    差异是从评论文本自己判出来的。真值文件 _competitor_truth.csv
    只给审计脚本用——这一点是这套模拟数据能否称为"验证"的关键。
    如果这里偷看真值，那"分析能力"就是假的。
    """
    d = df.copy()
    if category:
        d = d[d['biz_category'] == category]
    if d.empty:
        return {'ok': False, 'error': f'类目「{category}」下没有评论'}

    # 情感打分（逐条）
    scores = []
    for _, r in d.iterrows():
        s = score_text(r['review'])
        weak = abs(s['score']) < 0.1 and len(s['hit_pos']) + len(s['hit_neg']) <= 1
        final = s['score']
        if weak:
            final = float(np.clip(s['score'] * 0.4 + _rating_prior(r['rating']) * 0.6,
                                  -1, 1))
        scores.append(final)
    d['_score'] = scores
    d['_neg'] = (d['_score'] < -0.15).astype(int)

    rows = []
    for ent, g in d.groupby('entity_name'):
        dim = {}
        for dname, cfg in DIMENSIONS.items():
            m = g['review'].apply(lambda t: any(k in t for k in cfg['kws']))
            if m.sum() >= 5:
                dim[dname] = round(float(g.loc[m, '_neg'].mean()), 4)
        rows.append({
            'entity': ent,
            'source': g['source'].iloc[0],
            'n': int(len(g)),
            'neg_n': int(g['_neg'].sum()),
            'neg_pct': round(float(g['_neg'].mean()), 4),
            'mean_score': round(float(g['_score'].mean()), 4),
            'avg_rating': round(float(g['rating'].mean()), 2),
            'dims': dim,
        })
    rows.sort(key=lambda x: x['neg_pct'])

    # ---- 显著性：每个实体 vs 本店 ----
    store = next((r for r in rows if r['entity'] == store_name), None)
    for r in rows:
        if store and r['entity'] != store_name:
            z, p = _two_prop_z(r['neg_n'], r['n'],
                               store['neg_n'], store['n'])
            r['vs_store'] = {
                'diff_pp': round((r['neg_pct'] - store['neg_pct']) * 100, 2),
                'z': round(z, 2), 'p': round(p, 4),
                'significant': bool(p < 0.05),
                'readable': ('显著' if p < 0.05 else '不显著（可能是噪声）'),
            }

    # ---- 跨实体最稳的差异维度 ----
    # 【为什么要"跨实体最稳"而不是"谁最高"】
    # 某个维度在 A 家 40%、B 家 35%、C 家 38%、D 家 36%，
    # 那么"最高是 40%"这件事没什么用——大家都不好。
    # 真正的洞见是"这一项在 A 家 40%，在其他三家都不到 20%"，
    # 说明这是 A 家独有的问题。
    dim_spread = []
    for dname in DIMENSIONS:
        vals = [(r['entity'], r['dims'][dname]) for r in rows
                if dname in r['dims']]
        if len(vals) < 3:
            continue
        nums = [v for _, v in vals]
        dim_spread.append({
            'dim': dname,
            'label': DIMENSIONS[dname]['label'],
            'max': max(nums), 'min': min(nums),
            'spread': round(max(nums) - min(nums), 4),
            'worst': max(vals, key=lambda x: x[1])[0],
            'best': min(vals, key=lambda x: x[1])[0],
            'values': {e: v for e, v in vals},
        })
    dim_spread.sort(key=lambda x: -x['spread'])

    # ---- 特征词：本店 vs 负评最少的竞品 ----
    #
    # 【这一段必须限定类目，否则出来的全是商品名】
    # 实测不限定类目时，"本店独有词"是 苹果 6.83 / 蒙牛 5.61 / 效果 5.57。
    # 苹果和蒙牛是**本店在卖的生鲜 SKU**，竞品 B 的语料里没有这两个词——
    # 于是它们拿到了极高的 log_ratio，看起来像"本店的特色"。
    # 但它表达的不是经营差异，是**两家店的选品不同**。
    # 拿这个去汇报"我们顾客更在意水果"，是彻底的误读。
    #
    # 修法：特征词对比只在**同一个类目内**做。
    # 同是服饰，比"版型/料子/起球"；同是数码，比"续航/屏幕/断流"。
    # 跨类目只有在用户没指定类目时才发生，那时返回里会标明
    # compare_scope，让读者知道这个对比是跨类的、只能看个大概。
    others = [r for r in rows if r['entity'] != store_name]
    contrast = None
    if store and others:
        best = others[0]     # rows 已按 neg_pct 升序，第一个就是口碑最好的
        sub = d[d['entity_name'].isin([store_name, best['entity']])]
        scope_cat = category
        if not scope_cat:
            # 没指定类目 → 挑两家都有、且样本最多的那个类目来比。
            # 按"两家都有"筛是必须的：只在本店有的类目没法对比。
            common = (sub.groupby(['biz_category', 'entity_name']).size()
                      .unstack(fill_value=0))
            common = common[(common > 30).all(axis=1)] if len(common.columns) > 1 \
                else common.iloc[0:0]
            if len(common) > 0:
                scope_cat = str(common.sum(axis=1).idxmax())
                sub = sub[sub['biz_category'] == scope_cat]
        cw = contrast_words(sub, 'entity_name', best['entity'], store_name,
                            top_k=12)
        contrast = {'benchmark': best['entity'],
                    'compare_scope': (f'{scope_cat}类目内' if scope_cat
                                      else '跨类目（仅看趋势，不可当经营差异读）'),
                    **cw}

    return {
        'ok': True,
        'category': category or '全部',
        'entities': rows,
        'dim_spread': dim_spread,
        'contrast': contrast,
        'note': ('负评率由评论文本逐条判定得出，维度归属按关键词命中，'
                 '与本店对比做两比例 z 检验（p<0.05 才算差异）。'),
    }


# ---------------------------------------------------------------------------
# 汇总：给 Agent 用的单一入口
# ---------------------------------------------------------------------------

def full_report(df: pd.DataFrame, entity: str | None = None,
                category: str | None = None) -> dict:
    """四个模块一次跑完，返回给 Agent 组装证据包。"""
    d = df
    if entity:
        d = d[d['entity_name'] == entity]
    if category:
        d = d[d['biz_category'] == category]
    return {
        'sentiment': analyze_sentiment(d, entity=entity),
        'wordcloud': word_freq(d),
        'persona': build_persona(d, entity=entity),
        'competitor': compare_competitors(df, category=category),
    }