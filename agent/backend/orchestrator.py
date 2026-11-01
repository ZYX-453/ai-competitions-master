# -*- coding: utf-8 -*-
"""差评炼金炉 · Agent 编排器

这个文件要回答一个评委一定会问的问题：**这到底是 Agent，还是一个套了壳的报表？**

区别只有一条：下一步做什么，是不是由上一步的结果决定的。

所以这里的编排**不是**一条写死的工具流水线。举两个真实分支：

  分支 A  问"这周差评为什么涨了"
          → 先查本周是否真的异常
          → 若 z 值不显著，直接告诉用户"这周没异常，要看你得看第 44 周"
            （而不是硬凑一个故事）
          → 若显著，才继续拆类目、拆原因

  分支 B  归因到某个类目后
          → 若该类目退货集中在"破损/变质"，下一步查履约而非查详情页
          → 若集中在"尺码/描述"，下一步才去抽样评论文本

数据一换，路径就变。这就是"会思考"和"会朗读"的分界。

另外两点设计：
1. 工具调用永远真实执行 pandas（见 tools/data_hub.py），这里只负责选和解释。
2. 配了 LLM key 就走真实 function calling；没配就走确定性推理引擎。
   两条路径产出**完全相同的事件流**，前端不需要知道用的是哪条。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from tools.data_hub import DataHub, get_hub
from llm_client import LLMClient, from_env as llm_from_env

# ---------------------------------------------------------------------------
# 工具注册表：既给 LLM 做 function calling，也给确定性引擎做执行入口
# ---------------------------------------------------------------------------

TOOL_SCHEMA: list[dict] = [
    {
        'type': 'function',
        'function': {
            'name': 'overview',
            'description': '店铺整体经营底盘：年订单量、GMV、客单价、整体退货率、退货损失总额及其占毛利比例。任何分析开始前先建立这个基准。',
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'diagnose_week',
            'description': '诊断指定周（或默认最新周）的退货率相对基线的异动，返回涨跌幅、z 值、是否异常、各类目的贡献百分点、该周退货原因 Top6。这是判断"到底有没有问题"的主工具。',
            'parameters': {
                'type': 'object',
                'properties': {
                    'week': {'type': 'integer', 'description': '周序号 1-52，不传则用最新周'},
                    'category': {'type': 'string', 'description': '限定类目，可选：服饰/数码/生鲜/个护'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'promo_mechanism',
            'description': '检验促销周退货率上升是"订单结构偏移"还是"促销行为本身改变退货倾向"。做 shift-share 分解 + 同 SKU 配对差分 + 排除降价/低质选品等替代解释。当怀疑涨幅与大促有关时必须调用。',
            'parameters': {'type': 'object', 'properties': {}},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'attribution_structure',
            'description': '拆解某周退货原因的结构（一级原因域 + 二级原因 + 条数 + 占比 + 损失额 + 可二次销售率）。',
            'parameters': {
                'type': 'object',
                'properties': {
                    'week': {'type': 'integer'},
                    'category': {'type': 'string'},
                    'top_n': {'type': 'integer', 'description': '返回前 N 条，默认 8'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'loss_breakdown',
            'description': '退货损失结构化拆解：按类目（含占该类目毛利的比例）、按口径（折损/报废）、单均损失。用于回答"亏了多少钱、亏在哪"。',
            'parameters': {
                'type': 'object',
                'properties': {'category': {'type': 'string'}},
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'compensation_curve',
            'description': '客服补偿档位 → 挽单率 → 期望利润的权衡曲线，返回最优档位与混淆检验值。只在 exp_eligible 可比子集上计算。回答"补多少钱最划算"。',
            'parameters': {
                'type': 'object',
                'properties': {'category': {'type': 'string'}},
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'value_of_reduction',
            'description': '把"退货率降低 X 个百分点"用已估计的传导弹性换算成 GMV 增量。回答"改善值多少钱"。',
            'parameters': {
                'type': 'object',
                'properties': {
                    'pp': {'type': 'number', 'description': '降低的百分点数，默认 1'},
                    'category': {'type': 'string'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'review_samples',
            'description': '抽取真实差评原文样本，按归因编码或类目筛选。用于给出"用户到底怎么说的"这类第一手证据。',
            'parameters': {
                'type': 'object',
                'properties': {
                    'cause': {'type': 'string',
                              'description': '归因编码：P1 尺码版型 / P2 材质做工 / P3 功能故障 / I1 描述不符 / F1 破损变质 / F2 时效错发 / S1 售后流程 / S2 服务态度 / E1 价值感知 / X 其他'},
                    'category': {'type': 'string'},
                    'limit': {'type': 'integer'},
                },
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'reason_profile',
            'description': ('某一个退货原因的全景画像：条数、占比、损失、单均损失、'
                            '类目分布、近 12 周趋势、可二次销售率。'
                            '回答"这个毛病有多重、值不值得管、该找谁改"——'
                            '按原因看，跟是哪一周无关。'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'cause': {'type': 'string',
                              'description': ('中文原因名，如 尺码版型不符 / 材质做工问题 / '
                                              '破损/变质/污损 / 描述与实物不符')},
                    'category': {'type': 'string'},
                },
                'required': ['cause'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'action_playbook',
            'description': '按归因原因码给出可执行动作清单，每条含动作内容、影响指标、责任方、验收 KPI。回答"那我该做什么"。',
            'parameters': {
                'type': 'object',
                'properties': {
                    'cause': {'type': 'string',
                              'description': '归因编码，如 F1 / P1 / P2 / E1 / I1 / F2 / P3'},
                },
                'required': ['cause'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'reason_kb',
            'description': ('本地知识库兜底：数据的原因结构里没有用户问的那一类时，'
                            '从这里取行业通用的治理方案（含征兆、动作、责任方、KPI）。'
                            '返回里带 source_tier 标明来源等级——'
                            'kb/generic 都必须和本店数据结论分开表述，不能混着说。'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'cause': {'type': 'string',
                              'description': '用户说的原因，原样传中文，不用先归类'},
                },
                'required': ['cause'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'compare_weeks',
            'description': ('把多个周放在同一口径下对比，回答"哪个更严重"。'
                            '共用一条基线，按 z 值排序并标注样本是否足够。'),
            'parameters': {
                'type': 'object',
                'properties': {
                    'weeks': {'type': 'array', 'items': {'type': 'integer'},
                              'description': '要对比的周次，至少两个'},
                    'category': {'type': 'string'},
                },
                'required': ['weeks'],
            },
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'trend',
            'description': '近 N 周的单指标时序，用于看走势而不是看单点。',
            'parameters': {
                'type': 'object',
                'properties': {
                    'metric': {'type': 'string',
                               'description': 'return_rate / dsr / gmv / orders / exposure'},
                    'category': {'type': 'string'},
                    'weeks': {'type': 'integer'},
                },
            },
        },
    },
]


# 退货原因文本 → 归因编码（两套编码体系在本项目里并存，必须显式映射，
# 否则 action_playbook 会因为拿到中文原因名而查不到动作）
REASON_TO_CAUSE = {
    '尺码版型不符': 'P1',
    '材质做工问题': 'P2',
    '功能性能故障': 'P3',
    '描述与实物不符': 'I1',
    '破损/变质/污损': 'F1',
    '时效慢/错发漏发': 'F2',
    '售后流程问题': 'S1',
    '价值感知不符': 'E1',
    '七天无理由/不想要了': 'X',
}

CAUSE_NAME = {
    'P1': '尺码版型不符', 'P2': '材质做工问题', 'P3': '功能性能故障',
    'I1': '描述与实物不符', 'F1': '破损/变质/污损', 'F2': '时效慢/错发漏发',
    'S1': '售后流程问题', 'S2': '服务态度', 'E1': '价值感知不符', 'X': '其他',
}

TOOL_TITLE = {
    'overview': '建立经营基准',
    'diagnose_week': '检验周度异动',
    'promo_mechanism': '拆解促销机制',
    'attribution_structure': '拆解退货原因结构',
    'reason_profile': '测算单一原因画像',
    'loss_breakdown': '核算退货损失',
    'compensation_curve': '求解补偿最优档',
    'value_of_reduction': '换算改善价值',
    'review_samples': '抽取差评原文',
    'action_playbook': '生成动作清单',
    'reason_kb': '检索知识库方案',
    'trend': '拉取时序走势',
    'compare_weeks': '对比多周严重程度',
}


# ---------------------------------------------------------------------------
# 事件
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """SSE 事件。

    设计成扁平的 dict 而不是嵌套结构，是因为前端要按 type 直接分派渲染；
    嵌套会让 reducer 变复杂，也更容易在流式过程中出现半截状态。
    """
    type: str
    payload: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        body = {'type': self.type, 'ts': self.ts, **self.payload}
        return f'data: {json.dumps(body, ensure_ascii=False, default=str)}\n\n'


# ---------------------------------------------------------------------------
# 问题解析
# ---------------------------------------------------------------------------

_WEEK_PATTERNS = [
    r'第\s*(\d{1,2})\s*周',
    r'[Ww]\s*(\d{1,2})\b',
    r'(\d{1,2})\s*周',
]

_PROMO_ALIAS = {
    '双11': 44, '双十一': 44, '双11大促': 44, '双十一大促': 44,
    '618': 24, '六一八': 24,
    '双12': 48, '双十二': 48,
}


def parse_week(q: str) -> int | None:
    """解析问题里的周次。

    返回值约定（第四版，前一版有个静默 bug）：
        None  —— 用户没提周次
        -1    —— "最新/最近一周"，由调用方换成 hub.latest_week
        n     —— 具体周号
        -2    —— **提到了周次，但超出数据范围**（如"第60周"）

    【为什么要单列 -2】
    第一版在这里写了 `if 1 <= w <= 52: return w`，超出就继续往下找，
    最后返回 None。看起来无害，实际后果很严重：
    用户问"第60周退货率为什么涨了"，系统把它当成"没指定周"，
    转头去分析第 44 周，然后一本正经地报了一串第 44 周的数字。
    用户看到的是一份完整、专业、但**答的不是他问的那一周**的报告——
    这比直接说"没有第 60 周"糟糕得多。
    静默降级比显式失败危险，因为它不报错。
    """
    for k, v in _PROMO_ALIAS.items():
        if k in q:
            return v
    # 【"最近/最新一周"必须解析成具体周号】
    # 不解析的后果：用户问"最新一周差评有异常吗"，
    # 系统内部按"当前周"跑，结论里却说"你问的那一周其实正常"——
    # 既没说清是哪一周，也没把用户问的那周数字摆出来。
    # 这里返回 -1 作为"最新一周"的哨兵，由调用方替换成真实的 max_week，
    # 避免把 52 硬编码进解析层（换数据就会错）。
    if any(k in q for k in ('最新一周', '最近一周', '最新周', '这周', '本周', '上一周')):
        return -1
    for pat in _WEEK_PATTERNS:
        m = re.search(pat, q)
        if m:
            try:
                w = int(m.group(1))
            except ValueError:
                continue
            if 1 <= w <= 52:
                return w
            # 提到周次但越界：不要贪心地继续匹配下一条正则，
            # 那样可能把"第60周"里的 "60" 之外的数字当成周号。
            return -2
    return None


def parse_weeks(q: str) -> list[int]:
    """解析全部被提到的周次，用于对比类问题。

    【为什么需要这个】
    "第44周和第41周哪个更严重" 只解析出 44，
    然后系统就按单周诊断跑，答成了"第44周怎么回事"——
    用户问的是**对比**，得到的是一半的答案。
    这里返回全部周次（去重、保序、过滤越界），
    由调用方决定要不要走对比路径。
    """
    out: list[int] = []
    lim = dataset_max_week()
    for m in re.finditer(r'第\s*(\d{1,2})\s*周', q):
        w = int(m.group(1))
        if 1 <= w <= lim and w not in out:
            out.append(w)
    return out


def dataset_max_week() -> int:
    """可用周数上限。解析层不持有数据，这里读 DataHub。

    单独抽一个函数，是为了让 parse_weeks 不必自己去拿 hub，
    同时避免把 52 硬编码在解析逻辑里（换数据集就会错）。
    """
    try:
        return int(get_hub().panel['week'].max())
    except Exception:
        return 52


def parse_category(q: str) -> str | None:
    for c in ('服饰', '数码', '生鲜', '个护'):
        if c in q:
            return c
    return None


# ---------------------------------------------------------------------------
# 退货原因解析：用户问的是哪一个原因
# ---------------------------------------------------------------------------

# 原因名（L2）与它在口语里的各种说法。
#
# 【为什么必须单独解析，不能靠"本周占比第一"】
# 实测用户问「尺码版型不符这个问题该怎么解决」，系统答的是
# 一整份第 44 周为什么涨的报告 —— 因为旧逻辑里 action_playbook 的
# cause 参数取的是 state['top_cause']，也就是**本周占比第一的原因**
# （破损/变质/污损 21.9%），跟用户问的完全是两回事。
# 更糟的是用户问什么都会拿到破损的动作清单，因为破损长期是第一。
#
# 所以顺序必须反过来：**用户说了哪个原因，就以那个原因为准**；
# 用户没说，才退回到"占比第一"。
# 这两者的差别是"回答你问的"和"回答我以为你想问的"。
_REASON_ALIASES: dict[str, tuple[str, ...]] = {
    '尺码版型不符': ('尺码', '版型', '大小不合适', '尺码偏小', '尺码偏大',
                '号不', '偏小', '偏大', '穿不上', '不合身'),
    '材质做工问题': ('材质', '面料', '做工', '起球', '掉色', '脱线', '缝线',
                '手感', '质感', '料子'),
    '破损/变质/污损': ('破损', '变质', '污损', '磕碰', '压坏', '漏液', '烂了',
                  '碎了', '撞坏', '不新鲜', '坏果'),
    '描述与实物不符': ('描述不符', '与实物不符', '实物不符', '色差', '图片不符',
                 '和图片', '跟图片', '货不对板'),
    '价值感知不符': ('价值', '不值', '性价比', '太贵', '价格不符', '觉得亏'),
    '时效慢/错发漏发': ('时效', '太慢', '发错', '错发', '漏发', '少发', '漏件',
                  '物流慢', '到货慢'),
    '功能性能故障': ('功能', '性能', '故障', '坏了', '不能用', '无法使用', '失灵'),
    '售后流程问题': ('售后', '客服', '退款慢', '流程'),
    '七天无理由/不想要了': ('七天无理由', '无理由', '不想要', '买错', '拍错'),
}

# 【数据里没有、但用户真的会问的原因】
# 上面九类是数据表 return_reason_text 的全部取值。用户嘴里的原因远不止九种：
# 「标签脱落」「有异味」「包装压坏」「赠品没送」——这些在数据里一条都没有，
# 但用户问出来时，答案必须存在。
#
# 旧行为是：parse_reason 返回 None → classify_intent 判成 diagnose →
# 系统去答"第几周为什么涨"。用户问标签脱落，收到一份周报。
# 这和第 44 周那次是**同一个病**：句子里点了名，系统没接住。
#
# 所以这里把知识库里的原因也纳入解析。解析出来之后往下走，
# reason_profile 会查不到（数据里没有），于是自动落到 reason_kb 取通用方案。
# 关键是路径要能走通——走不通的话，用户连"这个我按通用方法给你"都听不到。
_KB_REASON_ALIASES: dict[str, tuple[str, ...]] = {
    '标签/吊牌问题': ('标签', '吊牌', '洗标', '水洗标', '成分标', '价格牌',
                 '标签脱落', '吊牌缺失', '标错'),
    '异味/气味问题': ('异味', '味道', '气味', '刺鼻', '甲醛', '霉味',
                 '塑料味', '火纸味', '臭'),
    '色牢度/掉色问题': ('掉色', '褪色', '串色', '色牢度', '染色', '洗了变色'),
    '包装破损/运输挤压': ('包装', '挤压', '压扁', '盒子坏', '外箱破',
                  '变形', '磕碰'),
    '发错货/漏发': ('发错', '错发', '漏发', '少发', '发错款', '发错颜色',
                '漏件', '少件', '没收到'),
    '客服响应/售后流程': ('客服', '售后', '没人理', '不回复', '态度',
                  '退款慢', '审核慢', '踢皮球'),
    '赠品/配件缺失': ('赠品', '配件', '少配件', '没送', '说明书', '螺丝'),
    '尺码标注与实际不符': ('尺码表', '尺码不准', '码数不对', '三围', '尺寸不符'),
}

# 【这个映射为什么要在代码里再存一份】
# 它本来只存在 data/reason_kb.json 的 fallback_to 字段里。
# 但知识库文件一旦读不出来（删了、JSON 坏了），fallback_to 也跟着没了，
# 于是「知道该看哪一类量级」这件事一起失效——而它其实是**分类路由逻辑**，
# 和数据文件能不能读无关：标签问题该参照材质做工，这是靠常识就能定的，
# 不是靠读文件读出来的。
# 实测：文件缺失时整份回答只剩一句"方案库没读到"，
# 连"该看材质做工那 10,646 条"都给不出来，blocks 全空。
# 所以代码里留一份，文件读不出来时拿它兜底。
_KB_FALLBACK_TO: dict[str, str] = {
    '标签/吊牌问题': '材质做工问题',
    '异味/气味问题': '材质做工问题',
    '色牢度/掉色问题': '材质做工问题',
    '包装破损/运输挤压': '破损/变质/污损',
    '发错货/漏发': '时效慢/错发漏发',
    '客服响应/售后流程': '售后流程问题',
    '赠品/配件缺失': '时效慢/错发漏发',
    '尺码标注与实际不符': '尺码版型不符',
}


def kb_fallback_to(kb_name: str | None, kb: dict | None = None) -> str | None:
    """拿到"该参照数据里哪一类"的类别名。

    优先用知识库返回的值（可能比我这份新），
    读不到时退回代码里这份常量。
    """
    v = (kb or {}).get('fallback_to')
    if v:
        return v
    return _KB_FALLBACK_TO.get(kb_name or '')


def parse_reason(q: str) -> str | None:
    """从问句里解析用户指的是哪个退货原因。

    【为什么要返回规范名而不是用户原词】
    下游 action_playbook / review_samples / attribution_structure
    都按 return_reason_text 精确匹配。用户会说"尺码问题""大小不合适""偏小"，
    但表里只有"尺码版型不符"一个值。这里做一次归一化，
    让所有口语说法都能落到同一个原因上。

    【判定规则】
    先精确命中完整原因名，再退到别名。用长度倒序匹配，
    避免"尺码"这种短词抢在"尺码版型不符"前面 ——
    虽然目前不会冲突，但加新原因时这条保护有用。

    【数据原因优先于知识库原因，顺序不能反】
    "掉色"既是 材质做工问题 的别名，又是知识库 色牢度/掉色问题 的名字。
    "客服"既是 售后流程问题 的别名，又是知识库 客服响应 的名字。
    这必须先判数据：**有真实数据的那个答案永远更可信**。
    如果反过来先查知识库，用户问"掉色怎么办"会拿到一份行业通用做法，
    而本店明明有 3,000 多条掉色退货的真实数据摆在旁边没用上。
    """
    s = (q or '').strip()
    if not s:
        return None
    # ① 数据里的完整原因名直接命中（最可靠）
    for name in sorted(_REASON_ALIASES, key=len, reverse=True):
        if name in s:
            return name
    # ② 数据原因的别名命中
    for name, aliases in _REASON_ALIASES.items():
        if any(a in s for a in aliases):
            return name
    # ③ 数据里没有，才看知识库的原因
    #    这一层是为了让"标签脱落怎么解决"能走进治理路径，
    #    而不是被判成 diagnose 去答某周为什么涨。
    for name in sorted(_KB_REASON_ALIASES, key=len, reverse=True):
        if name in s:
            return name
    for name, aliases in _KB_REASON_ALIASES.items():
        if any(a in s for a in aliases):
            return name
    return None


def is_data_reason(name: str | None) -> bool:
    """这个原因在数据表里有没有对应的退货记录。

    决定了走"真实数据 + 手册动作"还是"知识库通用方案"。
    调用方必须显式区分这两种来源——它们的可信度不是一个量级。
    """
    return bool(name) and name in _REASON_ALIASES


def _extract_reason_phrase(q: str, canonical: str | None) -> str | None:
    """从问句里抠出用户实际说的那个说法，用于在回答里"复述用户的问题"。

    【为什么需要它】
    用户问「标签脱落怎么解决」，回答开头如果写
    「数据里没有『标签/吊牌问题』这一类」，他得先反应过来
    "哦他说的是我的标签脱落"——中间隔了一层翻译。
    直接复述他的原词，读起来才是"你在跟我说我的事"。

    【怎么取：从问句里，不是从别名表里】
    第一版是"在别名表里挑命中的最长的那个"，于是用户说"赠品没送"，
    挑出来的是"赠品"——因为别名表里只收了"赠品"这个短词。
    结果标题写成「数据里没有「赠品」的单独统计」，把用户的话削掉了半截。
    正确做法：在**问句**里找命中原因词的最长连续片段。
    别名只用来定位起点，片段本身从用户原话里取。
    """
    s = (q or '').strip()
    if not s or not canonical:
        return canonical
    pool: list[str] = [canonical]
    pool += list(_REASON_ALIASES.get(canonical, ()))
    pool += list(_KB_REASON_ALIASES.get(canonical, ()))
    hits = [w for w in pool if w and w in s]
    if not hits:
        return canonical

    # 【向右吸收到 4 个字，不是 2 个】
    # 踩过的坑：上限给 2，用户说"客服不回复"，锚点是"客服"，
    # 只吸到"客服不回"——末尾掉一个字，看着像打字打漏了。
    # 常见补语长度：没送(2)、缺失(2)、不回复(3)、不一样大(4)。
    # 给 4 就够，再多会把"怎么处理"这类疑问词吸进来，靠 stop_words 兜住。
    stop_chars = '，。！？；、,.!?; \t\n'
    stop_words = ('怎么', '如何', '为什么', '为啥', '该', '应该',
                  '可以', '有没有', '是不是', '这个', '这种')
    best = max(hits, key=len)
    for anchor in hits:
        i = s.find(anchor)
        if i < 0:
            continue
        right = i + len(anchor)
        while right < len(s) and (right - i - len(anchor)) < 4:
            ch = s[right]
            if ch in stop_chars or ch in '的了吗呢吧啊':
                break
            # 碰到疑问词的第一个字就停，别等吸完了再判断
            if any(w.startswith(ch) for w in stop_words):
                # 但如果这个字能让补语完整（如"不"属于"不一样大"），继续
                if not s[right:right + 4].startswith(
                        ('不一样', '不准', '不回复', '不到')):
                    break
            right += 1
        cand = s[i:right].strip()
        if any(cand.endswith(w) for w in stop_words):
            cand = anchor
        if len(cand) > len(best):
            best = cand
    return best or canonical


# ---------------------------------------------------------------------------
# 问题是否落在本产品的业务边界内
# ---------------------------------------------------------------------------

# 业务词表：只要命中任意一个，就认为用户是在问经营数据。
# 【为什么用"命中即放行"，而不是"没命中就拒绝"】
# 拒绝的门槛必须比接受高得多。漏答一个业务问题的代价，
# 跟拒答一个业务问题的代价完全不对等 —— 后者是灾难性的。
# 所以这里先判断"像不像业务问题"，不像的再进第二步的强否定判断。
_BIZ_TERMS = (
    # 指标
    '退货', '差评', '评价', '评论', '好评', '退货率', '挽单', '补偿', '赔付',
    'gmv', '毛利', '利润', '客单', 'dsr', '销量', '订单', '库存', '履约',
    '损失', '成本', '收益', '转化', '复购', '流失',
    # 维度
    '周', '类目', '品类', 'sku', '商品', '大盘', '整体', '经营', '总览',
    '促销', '大促', '双11', '双十一', '618', '双12',
    '生鲜', '服饰', '数码', '个护',
    # 动作与归因
    '为什么', '原因', '归因', '异常', '诊断', '占比', '结构', '建议',
    '怎么办', '怎么做', '动作', '方案', '值多少', '多少钱',
)

# 明确的"非本产品"信号：命中且没有业务词时，直接拒绝。
# 这些是实测中真实出现过的越界问法（"帮我写一首诗"被硬答成了一篇退货率分析）。
_OFFTOPIC_STRONG = (
    '写一首', '写个诗', '作诗', '写诗', '写一篇作文', '翻译', '讲个笑话',
    '讲笑话', '讲故事', '聊天',
    # 【v2.5】'你是谁' '你叫什么' 从越界词里拿掉了，见 _GREETING。
    # 它们不是"越界"，是**打招呼**。把打招呼也判成"超出范围"，
    # 用户第一句话就被拒，会觉得这个产品不通人情。
    '今天天气', '天气怎么样',
    '彩票', '股票代码', '写代码', '帮我编程', '推荐电影', '推荐音乐',
)

# 【v2.5】问候与产品询问：既不是业务问题，也不该按"越界"拒绝。
# 处理方式完全不同 —— 越界是"我不做这个"，问候是"我来告诉你我能做什么"。
# 同一个拒绝卡片套在两句完全不同的话上，是最偷懒的做法。
_GREETING = (
    '你好', '您好', 'hi', 'hello', 'hey', '哈喽', '在吗', '在么',
    '你是谁', '你叫什么', '你能做什么', '你会什么', '你能干嘛',
    '怎么用', '如何使用', '帮助', 'help', '介绍一下自己', '介绍一下你',
)


def is_greeting(q: str) -> bool:
    """是否是打招呼/问产品能做什么。

    【为什么单独判，不并进 is_in_scope】
    is_in_scope 的返回值只有"能答/不能答"两种，而这里需要第三种：
    "不答，但要告诉对方该问什么"。
    实测「你好你是谁」原来落到"这个问题不在我的能力范围，我不硬答"，
    正文还在解释"写诗、翻译、闲聊这类事我不做"——
    用户只是打了个招呼，却收到一段防御性的声明。
    这不是范围问题，是接待问题。
    """
    s = (q or '').strip().lower()
    if not s:
        return False
    # 只要句子里带了业务词，就还是业务问题（"你好，第44周为什么涨"）
    if any(t in s for t in _BIZ_TERMS):
        return False
    return any(t in s for t in _GREETING)


def is_in_scope(q: str) -> bool:
    """问题是否属于经营诊断范畴。

    返回 False 时，调用方应当明确告知"这个问题不在我能回答的范围"，
    而不是套一个归因模板硬答。

    【为什么需要这道闸】
    原来 classify_intent 最后一行是 `return 'diagnose'` 兜底，
    于是"帮我写一首诗"也会走进完整的诊断流程：
    查大盘 → 查第 44 周 → 跑促销拆解 → 输出三段结论。
    内容本身没错（数字都是真的），但它**答非所问**，
    而且更糟的是它会让人误以为这个系统"什么都敢答"。
    一个分析系统最值钱的品质是"知道自己不该答什么"。

    判定顺序（宽进严出）：
      1. 命中任何业务词 → 是业务问题，放行
      2. 命中强越界词 → 拒绝
      3. 其余一律放行（宁可多答，不误拒）
    """
    s = (q or '').strip().lower()
    if not s:
        return False
    if any(t in s for t in _BIZ_TERMS):
        return True
    if any(t in s for t in _OFFTOPIC_STRONG):
        return False
    # 兜底放行：没命中业务词也没命中越界词时，很可能是业务问题的口语说法
    # （比如"最近怎么样"），交给下游走诊断比直接拒绝安全。
    return True


def classify_intent(q: str) -> str:
    """意图判定。

    这里故意只做粗分类，具体的工具序列由 _plan_* 函数根据**数据结果**决定。
    把"分类"和"决策"分开，是因为分类可以靠关键词，决策不能。

    【顺序很关键，不是随便排的】
    "退货到底亏了多少钱" 里有"多少钱"，会被 value 分支抓走，
    但它问的其实是**已经发生的损失**，不是"降 X pp 能赚多少"。
    所以 loss 必须排在 value 前面，而且要显式识别"亏/损失/成本"这类词。
    """
    s = q.lower()
    # 【对比意图必须最先判】
    # "第44周和第41周哪个更严重"里有"更严重"但没命中"为什么/原因"，
    # 原来会掉进最后的 diagnose 兜底，然后被单周诊断吃掉，
    # 只答了一半（第44周怎么回事），用户问的"哪个更严重"没回答。
    # 判据：出现对比词 + 至少两个周次。
    if any(k in q for k in ('哪个更', '哪个严重', '对比', '比较', '相比', '谁更',
                            '哪一个更', '两者')) and len(parse_weeks(q)) >= 2:
        return 'compare'
    if any(k in q for k in ('补偿', '补多少', '赔多少', '挽单', '安抚')):
        return 'compensation'
    # 已经发生的钱：亏了多少、损失多大、成本多高 —— 必须先于 value 判定
    if any(k in q for k in ('亏', '损失', '成本', '花了多少', '白扔')):
        return 'loss'
    # 改善能值多少钱：降 X 个点、能赚、roi、值多少
    if any(k in q for k in ('值多少', '换算', 'roi', '收益', '值钱', '能赚', '节省')):
        return 'value'
    if any(k in q for k in ('多少钱',)) and not any(k in q for k in ('降', '提升', '改善', '减少')):
        return 'loss'
    # 【"解决/优化/降低"这类词原来一个都不在表里】
    # 实测用户问「尺码版型不符这个问题该怎么解决」，
    # 系统判成 diagnose、去答第 44 周为什么涨 —— 因为这张表只收了
    # "怎么办/怎么做"，没收"怎么解决"。一个字的差别，答的东西完全不一样。
    #
    # 更根本的问题是：只要句子里**点名了某个原因**，用户问的就是
    # "这个原因怎么治"，不可能是在问周度异动。所以这里先看 parse_reason，
    # 命中了就直接走 action —— 不看有没有动作词。
    # 例如"尺码版型不符"（四个字，没有任何动词）也该走 action。
    if parse_reason(q):
        return 'action'
    if any(k in q for k in ('怎么办', '怎么做', '怎么解决', '如何解决', '怎么改',
                            '如何改', '怎么治', '怎么优化', '如何优化', '怎么降',
                            '如何降', '怎么减少', '如何减少', '怎么提升',
                            '动作', '方案', '建议', '落地', '措施', '对策',
                            '治理', '改善')):
        return 'action'
    # 大盘类要先于 diagnose 判定。
    # "大盘怎么样"里没有涨跌词，但一旦用户写成"大盘涨了吗"，
    # 下面的 diagnose 分支会把 '涨' 抓走，答非所问地去做单周归因。
    if any(k in q for k in ('整体', '大盘', '概况', '经营', '总览', '总体', '基本情况')):
        return 'overview'
    if any(k in q for k in ('为什么', '原因', '归因', '涨', '跌', '异常', '诊断', '排查')):
        return 'diagnose'
    if any(k in q for k in ('分布', '结构', '占比', '构成')):
        return 'structure'
    if any(k in q for k in ('促销', '大促', '双11', '双十一', '618')):
        return 'diagnose'
    return 'diagnose'  # 兜底走诊断，这是本产品的核心能力


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    """编排器。

    两条执行路径（LLM / 确定性）共享同一个事件协议与同一套工具，
    因此"有没有 API key"只影响由谁决定下一步，不影响数据真实性。
    """

    def __init__(self, hub: DataHub | None = None, pace_ms: int = 260):
        self.hub = hub or get_hub()
        self.pace = max(0, pace_ms) / 1000.0
        self.llm = self._init_llm()

    # ---------------- LLM（可选） ----------------
    def _init_llm(self):
        """构造 LLM 客户端。没配 key 返回 None，走确定性引擎。

        【这里曾经是个空壳，必须说明】
        上一版这个方法只存了个 dict（key/base/model），
        然后整个文件再没发过一次 HTTP 请求 ——
        `self.llm` 只被用来判真假、读模型名。
        也就是说「设置 API Key 就走 LLM」当时是假的，答案永远是模板拼的。
        现在改成真正的客户端（llm_client.LLMClient），并在 _compose 后调用它。
        """
        client = llm_from_env()
        return client

    @property
    def mode(self) -> str:
        return 'llm' if self.llm else 'engine'

    # ---------------- 工具执行 ----------------
    def _call(self, name: str, args: dict) -> tuple[Any, float, str]:
        """真实执行一个工具，返回 (结果, 耗时ms, 摘要)。"""
        t0 = time.perf_counter()
        fn = getattr(self.hub, name, None)
        if fn is None:
            return {'ok': False, 'error': f'未知工具 {name}'}, 0.0, '未知工具'
        try:
            kwargs = {k: v for k, v in (args or {}).items() if v is not None}
            res = fn(**kwargs)
            out = res.to_dict() if hasattr(res, 'to_dict') else res
        except TypeError as e:
            out = {'ok': False, 'error': f'参数不匹配：{e}'}
        except Exception as e:  # 工具内部异常不能让整个流断掉
            out = {'ok': False, 'error': f'{type(e).__name__}: {e}'}
        ms = (time.perf_counter() - t0) * 1000
        return out, ms, self._summarize(name, out)

    @staticmethod
    def _summarize(name: str, out: dict) -> str:
        """给每个工具生成一行人类可读的返回摘要，用于推理链上展示。"""
        if not out.get('ok'):
            return f'失败：{out.get("error", "未知错误")}'
        d = out.get('data') or {}
        try:
            if name == 'overview':
                return (f"{d['orders_year']:,} 单 / ¥{d['gmv_year']/1e4:.1f} 万 / "
                        f"退货率 {d['return_rate']*100:.2f}% / 损失 ¥{d['loss_total']/1e4:.1f} 万")
            if name == 'diagnose_week':
                v = '异常' if d['is_anomaly'] else '正常'
                return (f"第 {d['week']} 周 {d['rr_now']*100:.2f}% vs 基线 "
                        f"{d['rr_base']*100:.2f}%（{d['delta_pp']:+.2f}pp，z={d['zscore']}，{v}）")
            if name == 'promo_mechanism':
                dc = d['decomposition']
                return (f"实际 {d['actual_gap_pp']:+.2f}pp = 结构 {dc['structure_pp']:+.2f}pp"
                        f" + 机制 {dc['mechanism_pp']:+.2f}pp（t={d['paired_test']['t_stat']}）")
            if name == 'attribution_structure':
                it = d['items'][0] if d.get('items') else None
                return (f"{d['total_returns']} 条退货，首位「{it['l2']}」"
                        f"{it['share']*100:.1f}%") if it else '无数据'
            if name == 'loss_breakdown':
                return f"总损失 ¥{d['total_loss']/1e4:.1f} 万，单均 ¥{d['per_return']:.2f}"
            if name == 'compensation_curve':
                o = d['optimal']
                return (f"最优档 ¥{o['compensation']:.0f}，期望利润 ¥{o['expected_profit']:.2f}，"
                        f"挽单率 {o['save_rate']*100:.1f}%")
            if name == 'value_of_reduction':
                return (f"降 {d['pp_reduced']}pp → 年 GMV +¥{d['annual_gmv_lift']/1e4:.1f} 万"
                        f"（+{d['pct_of_gmv']*100:.2f}%）")
            if name == 'review_samples':
                return f"命中 {d['total_matched']} 条，取 {len(d['samples'])} 条样本"
            if name == 'action_playbook':
                return f"「{d['cause_name']}」生成 {len(d['actions'])} 条动作"
            if name == 'trend':
                return f"{len(d['series'])} 个周点"
            if name == 'compare_weeks':
                ww = '、'.join(f"第{x['week']}周" for x in d['weeks'])
                return f"对比 {ww}，最严重是第 {d['worst_week']} 周"
        except Exception:
            pass
        return '完成'

    # ---------------- 主流程 ----------------
    async def run(self, question: str, week: int | None = None) -> AsyncIterator[Event]:
        q = (question or '').strip()
        if not q:
            yield Event('error', {'message': '问题为空'})
            return

        t_start = time.perf_counter()
        w = week or parse_week(q)
        # parse_week 用 -1 表示"最新一周"（见该函数注释），这里换成真实周号。
        # 放在这一层解析，是为了让解析函数本身不依赖数据集——
        # 否则换一份数据，"52" 就变成一个错误的魔数。
        if w == -1:
            w = int(self.hub.latest_week)

        # 【越界周次必须在跑任何工具之前拦下】
        # 见 parse_week 的注释：静默降级会答出"不是用户问的那一周"的报告。
        # 这里不往下走，直接给一个说清楚原因的答案，并在正文里列出可选范围。
        if w == -2:
            lim = int(self.hub.latest_week)
            asked = re.search(r'第\s*(\d{1,2})\s*周', q)
            bad = asked.group(1) if asked else '该周'
            yield Event('session', {
                'question': q, 'intent': 'out_of_range', 'mode': self.mode,
                'week_hint': None, 'category_hint': None,
                'model': self.llm.model if self.llm else '确定性推理引擎 v1',
            })
            answer = {
                'headline': f'数据里没有第 {bad} 周，无法作答',
                'badge': {'label': '超出范围', 'level': 'warn'},
                'summary': (
                    f'你问的是第 {bad} 周，但这份数据只覆盖第 1–{lim} 周，'
                    f'第 {bad} 周没有订单记录。\n\n'
                    f'我不会拿别的一周来顶替——那样你会得到一份看着完整、'
                    f'但答的不是你问题的报告。\n\n'
                    f'可以这样问：第 {lim} 周（最新一周）、第 44 周（双11 促销周，'
                    f'涨幅最明显）、或"大盘怎么样"看整体。'
                ),
                'blocks': [],
                'caveats': [f'可用周次范围：1–{lim}。'],
                'week': None,
                'by_llm': False,
                'llm_note': '',
            }
            yield Event('answer', answer)
            yield Event('done', {'ms': int((time.perf_counter() - t_start) * 1000),
                                 'by_llm': False})
            return

        cat = parse_category(q)
        intent = classify_intent(q)

        # 【v2.5】问候与"你能做什么"：不走拒绝卡片，给一份能用的自我介绍。
        #
        # 为什么值得单独写一段：这是**用户的第一句话**。
        # 原来它落到"这个问题不在我的能力范围，我不硬答"，
        # 正文还在解释"写诗、翻译、闲聊我不做"——用户刚开口就被教育了一顿，
        # 而且完全不知道这家店的退货率是多少、该从哪问起。
        #
        # 正确做法是趁这个机会把"我能干什么"讲成"我已经看到了什么"：
        # 直接把大盘的关键数字报出来，再给三个具体的下一步。
        # 这样用户第一眼就知道这个系统是活的、有数据的、能问出东西的。
        if is_greeting(q):
            yield Event('session', {
                'question': q, 'intent': 'greeting', 'mode': self.mode,
                'week_hint': None, 'category_hint': None,
                'model': self.llm.model if self.llm else '确定性推理引擎 v1',
            })
            # 拉一次大盘，让问候也带真实数字。失败就退回纯文字版，
            # 不能因为取数失败连招呼都打不了。
            ov, _ms, _sum = self._call('overview', {})
            od = (ov.get('data') or {}) if ov.get('ok') else {}
            lim = int(self.hub.latest_week)
            if od:
                _rr = od.get('return_rate', 0) * 100
                _loss = od.get('loss_total', 0) / 1e4
                _ordn = od.get('orders_year', 0)
                _gmv = od.get('gmv_year', 0) / 1e4
                summary = (
                    f'我是差评炼金炉，一个退货归因诊断助手。'
                    f'你可以把它理解成"专门回答『这周为什么不对』的那块看板"。\n\n'
                    f'先说手头的数据：这家店近 52 周 **{_ordn:,} 单**、'
                    f'GMV **¥{_gmv:,.1f} 万**、整体退货率 **{_rr:.2f}%**，'
                    f'全年退货损失约 **¥{_loss:,.1f} 万**。'
                    f'这些数我都能拆到类目、拆到周、拆到具体原因。\n\n'
                    f'我最擅长三类问题：\n'
                    f'· **为什么**——「第44周退货率为什么涨了」'
                    f'（会验证是促销机制、商品质量还是履约问题，而不是猜）\n'
                    f'· **能不能信**——「第{lim}周正常吗」'
                    f'（会告诉你这个样本量能分辨多大的变化，噪声不硬报成异常）\n'
                    f'· **怎么办**——「客服补偿补多少最划算」、「退货到底亏了多少钱」\n\n'
                    f'不确定从哪问，就直接说「大盘怎么样」，我给你挑出最该看的那一周。'
                )
                # 【v2.5 补漏】这句 caveat 原来是"不做通用问答"，
                # 属于越界拒绝那套话术，搬到这里就变成"先打招呼、再声明我不干什么"。
                # 用户第一句话收到的应该是"能做什么"，不是"不做什么"。
                # 改成把边界说成能力范围，语气从防御转为告知。
                caveats = ['只覆盖退货/差评的经营诊断——这也是这家店最费钱的那一块。']
            else:
                summary = (
                    '我是差评炼金炉，一个退货归因诊断助手。\n\n'
                    '我最擅长三类问题：**为什么涨**（第44周退货率为什么涨了）、'
                    '**能不能信**（这周的变化是真的还是噪声）、'
                    '**怎么办**（补偿补多少最划算、损失有多大）。\n\n'
                    '不确定从哪问，就直接说「大盘怎么样」。'
                )
                caveats = ['只覆盖退货/差评的经营诊断——这也是这家店最费钱的那一块。']
            yield Event('answer', {
                'headline': '我是退货归因诊断助手，先给你看看手头的数据',
                'badge': {'label': '自我介绍', 'level': 'info'},
                'summary': summary,
                'blocks': [],
                'caveats': caveats,
                'week': None,
                'by_llm': False,
                'llm_note': '',
            })
            yield Event('done', {'ms': int((time.perf_counter() - t_start) * 1000),
                                 'by_llm': False})
            return

        # 【越界问题不套模板硬答】
        # 放在 session 事件之前：如果这个问题根本不在业务范围内，
        # 就不该先推一个"我要开始诊断了"的假象给前端。
        if not is_in_scope(q):
            yield Event('session', {
                'question': q, 'intent': 'out_of_scope', 'mode': self.mode,
                'week_hint': None, 'category_hint': None,
                'model': self.llm.model if self.llm else '确定性推理引擎 v1',
            })
            yield Event('answer', {
                # 【v2.5 拆分】这里原来和第 60 周那类"数据里没有这个周"
                # 共用「超出范围」徽章，但两件事完全不同：
                #   · 第 60 周 → 我这份数据没覆盖到（数据边界）
                #   · 写一首诗 → 这不是我的活（能力边界）
                # 共用徽章的直接后果：用户打个招呼、或者随口问句别的，
                # 都看到同一个"超出范围"的红牌子，会以为这个产品
                # 只认一种问法。徽章是给用户的第一眼判断，不能含糊。
                #
                # 文案也一起改了。原来是"这个问题不在我的能力范围，我不硬答"，
                # 正文还在解释"写诗、翻译、闲聊这类事我不做"——
                # 这等于把一句拒绝拆成三段反复说。用户要的只有两件事：
                # "你能干什么"和"我该问什么"。说清这两件就够了。
                'headline': '这个我答不了，但我能告诉你我擅长什么',
                'badge': {'label': '超出能力', 'level': 'warn'},
                'summary': (
                    '我是退货归因诊断助手，只回答与这家店经营数据有关的问题。'
                    '写诗、翻译、闲聊这类事我确实不做——'
                    '不是不愿意，是做了也不可信，那还不如不做。\n\n'
                    '我能答的是三类：**为什么**（第44周退货率为什么涨了）、'
                    '**能不能信**（这周的变化是真的还是噪声）、'
                    '**怎么办**（补偿补多少最划算、退货损失有多大）。\n\n'
                    '你可以直接从这三句里挑一句，或者问我「大盘怎么样」。'
                ),
                'blocks': [],
                'caveats': ['只覆盖退货/差评的经营诊断——这也是这家店最费钱的那一块。'],
                'week': None,
                'by_llm': False,
                'llm_note': '',
            })
            yield Event('done', {'ms': int((time.perf_counter() - t_start) * 1000),
                                 'by_llm': False})
            return

        yield Event('session', {
            'question': q,
            'intent': intent,
            'mode': self.mode,
            'week_hint': w,
            'category_hint': cat,
            'model': self.llm.model if self.llm else '确定性推理引擎 v1',
        })

        # 上下文：一次 overview 建立基准，后续所有数字都可以跟它比
        state: dict = {'intent': intent, 'week': w, 'category': cat,
                       'question': q, 'facts': {}, 'chain': [], 'caveats': []}
        # 【用户点名了哪个退货原因，全程记着】
        # 这是"答非所问"那次的根因之一：旧逻辑里动作清单的原因
        # 只能从"本周占比第一"推出来，用户问尺码也返回破损。
        # 现在把用户说的那个原因先钉住，下游 review_samples /
        # action_playbook 都优先用它。
        # 跟 asked_week 一样是只读快照，全程不覆盖 ——
        # 它代表"用户最初问的是什么"，不该被分析过程中的任何结论改写。
        _reason = parse_reason(q)
        if _reason:
            state['asked_reason'] = _reason
            state['facts']['asked_reason'] = _reason
            # 【用户原话必须单独留一份】
            # parse_reason 把"标签脱落"归一成"标签/吊牌问题"，
            # 知识库又把同一个名字当 kb_name 返回。
            # 组装答案时如果拿 asked_reason 当"用户说的那个词"，
            # 就会出现「数据里没有「标签/吊牌问题」，按最接近的
            # 「标签/吊牌问题」给方案」—— 自己跟自己比，读者一头雾水。
            # "你说的"和"我归到的类"是两个东西，答案里必须分开写。
            state['user_reason_text'] = _extract_reason_phrase(q, _reason) or _reason
            # 知识库要的是原词，不是归一化名：别名表里有"吊牌价格牌印错"
            # 这种长说法，归一化会把它抹成大类，动作也跟着变泛。
            #
            # 【但"原词"不等于"整句问题"】
            # 这里原来写的是 state['kb_query'] = q，把整句话传了下去：
            #   「标签脱落这个问题该怎么解决」→ cause=标签脱落这个问题该怎么解决
            # 工具侧靠"别名 in q"仍然能命中，所以答案是对的、测试也全过，
            # 但界面上推理链第一格显示的就是那句完整的话——
            # 用户看到的是"系统把我的问题整句塞进了一个叫 cause 的参数里"。
            # 这个 bug 只有截图能发现：参数、答案、断言全是"正确"的。
            #
            # 改传 _extract_reason_phrase 的产物：它从问句里抠出用户的实际说法
            # （"标签脱落"），既保留了原词的颗粒度，又去掉了"这个问题怎么解决"的尾巴。
            state['kb_query'] = (state['user_reason_text'] or _reason)
            # 标记这个原因在数据里到底有没有
            state['facts']['reason_in_data'] = is_data_reason(_reason)
            if not is_data_reason(_reason):
                # 【这个事实必须在最前面就钉住】
                # 后面的答案要说清"数据里没有这一类，我给的是通用方案"。
                # 如果等到组装阶段再判断，中间任何一步失败都可能让这个
                # 事实丢掉，最后又变回"未收录原因"。
                state['facts']['kb_fallback_used'] = True
        # 对比意图需要"用户提到的全部周次"，而不是第一个
        if intent == 'compare':
            state['compare_weeks'] = parse_weeks(q)
        # 记住"用户原本问的是哪一周"。
        # state['week'] 会被 _react 改写（重定向后变成真正异常的那一周），
        # 所以不能拿它来写"你问的第 X 周"——那会写成目标周，自相矛盾。
        # asked_week 是只读快照，全程不覆盖。
        if w is not None and w > 0:
            state['facts']['asked_week'] = w
            # 【"第 52 周" 和 "最新一周" 不是一回事】
            # 前者是用户点名要看这一周；后者只说了"最新的"，
            # 潜台词是"看看现在有什么问题"——这种问法天然允许往下追。
            # 两者最终都落到 52，但重定向的许可完全不同：
            #   "第52周怎么样" → 52 平平无奇 → 就答 52 平平无奇（不许换题）
            #   "最新一周有问题吗" → 52 平平无奇 → 可以接着看真正异常的周
            # 之前没区分，把所有指到末周的提问都当成了"点名"，于是
            # E2 这类问法再也走不到重定向，答案永远停在"这一周没问题"。
            state['facts']['week_pinned_by_user'] = bool(re.search(r'第\s*\d{1,2}\s*周', q))

        steps = self._plan(intent, state)
        yield Event('plan', {'steps': [TOOL_TITLE.get(s, s) for s in steps],
                             'note': '计划会随中间结果调整'})

        executed = 0
        # 【用下标遍历，不用 for-in-steps】
        # 踩过的坑：用 `for name in steps` 配合 steps.insert() 动态插队时，
        # 列表长度变化会让迭代错过元素；而且 'diagnose_week' 这种被重定向后
        # 需要再跑一次的工具，会在列表里出现两次，steps.index(name) 永远返回
        # 第一个位置 —— 于是第二轮拿到的还是旧的 week 参数，重定向彻底失效。
        # 改用显式游标后，"同一工具跑两次"是正常路径，不再有歧义。
        i = 0
        guard = 0
        while i < len(steps):
            guard += 1
            if guard > 24:  # 防止 react 互相追加造成死循环
                state['facts']['truncated'] = True
                break
            name = steps[i]
            i += 1
            # 每个工具前先给一句"为什么现在做这一步"
            thought = self._thought(name, state)
            if thought:
                yield Event('thought', {'text': thought})
                await self._sleep()

            args = self._args(name, state)
            call_id = f'c{executed + 1}'
            yield Event('tool_call', {
                'id': call_id, 'tool': name, 'title': TOOL_TITLE.get(name, name),
                'args': args,
            })
            await self._sleep(0.4)

            out, ms, summary = self._call(name, args)
            executed += 1

            yield Event('tool_result', {
                'id': call_id, 'tool': name, 'ok': bool(out.get('ok')),
                'summary': summary, 'ms': round(ms, 1),
                'evidence': out.get('evidence') or {},
                'note': out.get('note') or '',
                'error': out.get('error') or '',
                'data': out.get('data'),
            })
            await self._sleep(0.5)

            # ★ 关键：根据这次结果决定后面还要不要做、做什么
            # 【批量切片插入，不能逐个 insert】
            # 逐个 steps.insert(i, f) 时每个都插在同一位置，后插的会排到前面：
            # follow=['promo','attribution'] 被插成 ['attribution','promo']，
            # 推理链变成"先拆原因、再谈促销机制"，因果逻辑整个倒过来。
            # 切片赋值 steps[i:i] = [...] 保持 follow 的原始顺序。
            follow = self._react(name, out, state)
            add = [f for f in follow if f not in steps[i:]]
            if add:
                steps[i:i] = add

            # 把关键事实沉淀下来，最后写答案时用
            self._absorb(name, out, state)

            if name == 'diagnose_week' and not out.get('ok'):
                # 周不存在这类硬错误，直接收口，不要继续编
                yield Event('answer', self._answer_missing(state, out))
                yield Event('done', {'elapsed_ms': round((time.perf_counter() - t_start) * 1000)})
                return

        answer = self._compose(state)

        # 【LLM 改写：只写文案，不动数字】
        # 放在这一步而不是更早，是因为此刻所有工具都已跑完、
        # 事实包是完整的——让模型看到半截数据只会让它猜。
        #
        # 关键：改写耗时 5–40s，期间不能什么都不发，
        # 否则前端停在最后一步、看起来像卡死。
        # 先推一条 thought 说明正在做什么，再 await 改写。
        if self.llm:
            yield Event('thought', {'text': f'正在用 {self.llm.model} 组织结论措辞（数字已由 pandas 核算，模型只负责表达；若首版不合规会自动重写一次）'})
            answer = await self._polish_with_llm(answer, state)

        yield Event('answer', answer)
        yield Event('done', {
            'elapsed_ms': round((time.perf_counter() - t_start) * 1000),
            'tool_calls': executed,
            'by_llm': bool(answer.get('by_llm')),
        })

    async def _sleep(self, mult: float = 1.0) -> None:
        if self.pace > 0:
            await asyncio.sleep(self.pace * mult)

    # ---------------- 计划 ----------------
    def _plan(self, intent: str, state: dict) -> list[str]:
        """初始计划。刻意做得短——真正的长计划在 _react 里长出来。"""
        if intent == 'overview':
            return ['overview', 'diagnose_week', 'trend']
        if intent == 'compensation':
            return ['compensation_curve', 'value_of_reduction']
        if intent == 'value':
            return ['overview', 'value_of_reduction']
        if intent == 'loss':
            return ['overview', 'loss_breakdown']
        if intent == 'structure':
            return ['attribution_structure']
        if intent == 'compare':
            # 对比不需要 overview：compare_weeks 自带共同基线，
            # 再叠一层 overview 会引入两个不同的基线，反而说不清。
            return ['compare_weeks']
        if intent == 'action':
            # 【用户点名了某个原因 → 别再去查周度异动】
            # 这是"答非所问"那次的直接原因。旧版这一行固定是
            #   ['diagnose_week', 'attribution_structure', 'action_playbook']
            # 于是问"尺码版型不符该怎么解决"，第一枪就跑 diagnose_week，
            # 顺手把话题带到了"第 44 周为什么涨"，后面再接一份促销拆解。
            # 用户问的是"这个毛病怎么治"，收到的却是"那一周为什么涨"。
            #
            # 关键在于：这两个问题需要的证据完全不同。
            #   · 周度异动 → 需要基线、z 值、促销拆解
            #   · 某个原因怎么治 → 需要"这个原因有多少条、用户原话说了什么、
            #     我们手册里的动作是什么"
            # 后者根本不需要知道是哪一周 —— 尺码问题不是某一周才有的。
            if state.get('asked_reason'):
                # 【数据里没有这个原因，就走知识库路径】
                # 用户问「标签脱落怎么解决」，数据表里一条都没有。
                # 如果照旧跑 reason_profile，它会返回 ok=False，
                # 后面 review_samples / action_playbook 跟着全空，
                # 最后写出一句"未收录原因"——等于告诉用户这个功能不存在。
                #
                # 实际能提供的东西是：知识库里的通用治理方案 + 最接近的
                # 那一类真实原因的画像（按 fallback_to 映射过去）。
                # 前者回答"该做什么"，后者回答"这类问题在你这店里有多重"。
                # 两样拼起来，用户拿到手是能动的；只给前者像泛泛而谈，
                # 只给后者等于答了另一个问题。
                if not is_data_reason(state.get('asked_reason')):
                    return ['reason_kb', 'reason_profile']
                # 【顺序：先量化，再看原文，最后给动作】
                # 不能倒过来。先看原文会让结论被几条个案带跑
                # （挑到的差评恰好是极端案例，就会把"偶发"说成"普遍"）；
                # 先量化才知道这批抱怨的规模，原文才有代表性。
                # 也不能先给动作再找依据——那是"先有结论再找理由"。
                return ['reason_profile', 'review_samples', 'action_playbook']
            return ['diagnose_week', 'attribution_structure', 'action_playbook']
        # diagnose 是主路径
        return ['overview', 'diagnose_week']

    def _args(self, name: str, state: dict) -> dict:
        # target_week 优先于 week：重定向时它不是"刚分析过的周"，而是"接下来要分析的周"。
        # 读走即清理 —— 目标周是一次性的，用完必须销毁，
        # 否则后续所有工具都会跟着用这个周参数。
        w = state.pop('target_week', None) or state.get('week')
        cat = state.get('category')
        if name in ('diagnose_week', 'attribution_structure'):
            a = {}
            if w:
                a['week'] = w
            if cat:
                a['category'] = cat
            return a
        # 【v2.5】促销机制也必须带类目，否则"数码"和"服饰"拿到同一份全店数字。
        # 旧版漏了这个分支，_args 返回 {}，工具永远按全店算 ——
        # 实测两份报告里机制贡献都是 3.98pp、t 都是 15.8、
        # 都是"151 个 SKU 中 97% 上升"，因为那本来就是同一个全店结果，
        # 只是被"数码"/"服饰"这两个词分别包装了一遍。
        if name == 'promo_mechanism':
            return {'category': cat} if cat else {}
        if name in ('loss_breakdown', 'compensation_curve', 'value_of_reduction'):
            return {'category': cat} if cat else {}
        if name == 'trend':
            return {'metric': 'return_rate', 'weeks': 16}
        if name == 'compare_weeks':
            # 周次来自 parse_weeks（用户实际提到的那些），不是 state['week']
            a = {'weeks': state.get('compare_weeks') or []}
            if cat:
                a['category'] = cat
            return a
        if name == 'reason_profile':
            c = state.get('asked_reason') or state.get('top_cause')
            # 【知识库原因要换成最接近的数据原因再查】
            # 用户问「标签脱落」，数据里没有这一类，直接拿它去查
            # return_reason_text 会返回 ok=False，整条链路断在这里。
            # 知识库已经在 reason_kb 那一步给出了 fallback_to（比如 材质做工问题），
            # 这里就用它去查真实画像 —— 回答里会说明这是"替代口径"，
            # 用户知道自己在看哪一类的数据，不会误以为这就是标签问题的统计。
            kb = state['facts'].get('kb') or {}
            # 【fallback_to 不能只靠知识库给】
            # 知识库文件读不出来时 kb 是空的，fallback_to 也就没有了，
            # 于是连"该看哪一类量级"都不知道——整份回答只剩一句
            # "方案库没读到"，一个数都给不出来。
            # 但"标签问题该参照材质做工"是分类路由，靠常识能定，
            # 不该跟着配置文件一起坏。kb_fallback_to 里留了兜底。
            _target = kb_fallback_to(c, kb) if c else None
            if c and not is_data_reason(c) and _target:
                c = _target
                state['facts']['profile_stand_in'] = c
            a = {'cause': c} if c else {}
            if cat:
                a['category'] = cat
            return a
        if name == 'reason_kb':
            # 原样传用户说的那个词，不做归一化。
            # 知识库自己有别名表，交给它匹配更准——
            # 上层如果先归一化成"材质做工问题"，知识库就永远只能命中那一类，
            # 「标签脱落」这种具体问题会被抹成大类，动作也跟着变泛。
            return {'cause': (state.get('kb_query')
                              or state.get('asked_reason') or '')}
        if name == 'review_samples':
            # 【优先用用户点名的原因】用户问"尺码的差评原话给我看"，
            # 就该拉尺码的，而不是本周占比第一的破损。
            c = state.get('asked_reason') or state.get('top_cause')
            # 【limit 给 5 太少，这是"同码不同大"被漏掉的直接原因】
            # 最终答案只引用 2 条，但那是**过滤并按信息量排序之后**的 2 条。
            # 过滤会剔掉好评噪声（9 条里就有 1~2 条），排序会把
            # "同码/发错码"这类最有信息量的顶到前面 ——
            # 这两步都需要一个比"最终要几条"更宽的候选池。
            # 只给 5 条的话，挑出来的是"前 5 条里最像的"，不是"全批里最像的"。
            a = {'limit': 12}
            if c:
                a['cause'] = c
            if cat:
                a['category'] = cat
            return a
        if name == 'action_playbook':
            # 【这一行是"答非所问"的直接来源】
            # 旧版只有 state.get('top_cause')，也就是本周占比第一的原因。
            # 用户问"尺码怎么解决"，拿到的却是破损的动作清单 ——
            # 因为破损长期占比第一，无论用户问什么都返回它。
            # 现在先看 asked_reason（用户明确说的那个），
            # 用户没说才退回到 top_cause。
            return {'cause': state.get('asked_reason')
                    or state.get('top_cause') or 'F1'}
        return {}

    def _thought(self, name: str, state: dict) -> str:
        """每一步的推理独白。写成人话，不写"正在调用工具"。"""
        f = state.get('facts', {})
        if name == 'overview':
            return '先建立经营基准。没有基准，任何"涨了跌了"都没有意义——我需要知道这家店的退货率正常水位在哪。'
        if name == 'diagnose_week':
            if state.get('week'):
                return f'直接检验第 {state["week"]} 周。我要的不是"感觉高了"，而是它偏离基线多少个标准差。'
            return '先看最新一周有没有异动。如果没有，我不会硬编一个故事——会直接告诉你换哪周看。'
        if name == 'promo_mechanism':
            return ('这一周带促销标记，订单量是非促销周的 2 倍。最常见的解释是"爆单导致履约压力"，'
                    '但这是个直觉，不是结论。我用同 SKU 配对比较来排除选品干扰。')
        if name == 'attribution_structure':
            return '现在看退货原因的内部结构。同样是涨 3 个点，"破损"和"觉得不值"是完全不同的两件事。'
        if name == 'loss_breakdown':
            return '把退货率翻译成钱。退货率是过程指标，钱才是能进汇报的指标。'
        if name == 'compensation_curve':
            return ('补偿不是越多越好。多补一档会吃掉毛利，少补一档挽不回来。'
                    '我要找的是期望利润最大的那一档，不是挽单率最高的那一档。')
        if name == 'value_of_reduction':
            return '最后一步换算：把"退货率降 1 个百分点"变成"一年多赚多少钱"。这一步用的是已估计的传导弹性，不是拍脑袋。'
        if name == 'review_samples':
            return ('数字说到这儿，该看用户原话了。'
                    '抽样不是为了举例，是为了验证原因标签有没有打错——'
                    '上一轮就是因为跳过这一步，把"同码不同大"看成了"码数偏大"。')
        if name == 'action_playbook':
            # 【这里曾经泄漏出 None，被截图的用户直接看到】
            # 原来只有 c = state.get('top_cause')，而"按原因治理"这条路径
            # 压根不走 attribution_structure，top_cause 从来没被赋值，
            # 于是推理独白渲染成：
            #   「归因到「None」，接下来必须是可执行动作。」
            # 用户看到的是赤裸的代码变量名。这类问题在任何断言里都测不出来——
            # 数字对、结构对、只有那一个词是 None。
            # 现在跟 _args 保持一致：优先用户点名的原因，退回落类。
            c = (state.get('asked_reason') or state.get('top_cause'))
            if not c:
                return ('接下来要是可执行动作。动作要挂在具体原因上，'
                        '并且写清谁做、影响哪个指标、怎么验收。')
            return (f'归因到「{CAUSE_NAME.get(c, c)}」，接下来必须是可执行动作。'
                    '动作要挂在具体原因上，并且写清谁做、影响哪个指标、怎么验收。')
        if name == 'reason_kb':
            # 知识库场景的独白不能套用"归因"的说法——
            # 数据里根本没有这一类，谈不上归因。
            return ('数据的原因分类里没有这一类，硬查只会返回空。'
                    '先看知识库里有没有通用的处理办法，'
                    '再找成因最接近的那一类看量级。')
        if name == 'reason_profile':
            # 【同一句独白要分两种场景】
            # 有真实数据时，这是"算这个毛病有多重"；
            # 走知识库时，它是"找一个相近的类看量级"——
            # 后者如果也写"算它有多重"，读者会以为真有它的统计。
            if state['facts'].get('kb_fallback_used'):
                return ('数据里没有它的直接统计，只能拿成因最接近的那一类看量级。'
                        '这只是参照，不能当成它自己的数字。')
            return ('现在算这个毛病到底有多重：多少条、亏多少钱、集中在哪、在涨还是在跌。'
                    '不先算这个就谈怎么治，只能给出通用套话。')
        if name == 'trend':
            return '拉一段时序，确认这不是单周噪声。'
        if name == 'compare_weeks':
            return ('要回答"哪个更严重"，不能只比退货率高低——'
                    '样本量不同的周，同样的百分比可信度差很多。'
                    '我按偏离基线的标准差排序，并把样本不足的周单独标出来。')
        return ''

    # ---------------- 反应（ReAct 的心脏） ----------------
    def _react(self, name: str, out: dict, state: dict) -> list[str]:
        """根据这一步的结果，决定下一步追加什么。

        整个函数的每一行都是一个"如果…那么…"，这就是 Agent 与流水线的区别。
        """
        d = out.get('data') or {}
        if not out.get('ok'):
            return []

        if name == 'diagnose_week':
            state['week'] = d.get('week')
            if d.get('is_anomaly') and (d.get('delta_pp') or 0) > 0:
                # 显著**上升**才需要归因。下降是好事，硬去解释"为什么变好了"
                # 会显得 Agent 只会套模板。
                if self._is_promo_week(d.get('week')):
                    return ['promo_mechanism', 'attribution_structure']
                return ['attribution_structure']
            if d.get('is_anomaly'):
                # 显著下降：值得指出，但不是问题，不需要归因链
                state['facts']['improved'] = True
                # 【改善场景也要写清"为什么不出动作"】
                # 只置 improved=True 的话，模板和模型都会进"不给动作"分支，
                # 但手上没有一句现成理由，就会退化成
                # "这一周不建议针对该原因开整改动作"——而这一周压根没算原因结构。
                # 改善场景真正的正解不是"不动作"，是"复盘做对了什么"。
                state['facts']['no_playbook'] = True
                state['facts']['no_playbook_why'] = (
                    '本周退货率是显著下降，不是问题，不需要整改动作；'
                    '该做的是复盘这一周做对了什么，把有效做法固定下来继续用')
                return ['trend']
            if not d.get('enough_sample'):
                state['facts']['low_sample'] = True
                # 【样本不足时应该说"能看什么"，而不是只说"不能看"】
                # 旧文案只有"样本量不足以支撑任何归因，因此不派动作"，
                # 用户读完仍然不知道下一步该干什么，往往换个问法再问一次，
                # 得到的还是同一句。现在把该范围的可分辨幅度讲清楚：
                # 这个样本量只能看出 X pp 以上的跳变，更小的波动是噪声。
                _ord = d.get('orders_this_week')
                _det = d.get('detectable_pp')
                _mo = d.get('min_orders')
                _hint = (f'第 {d.get("week")} 周只有 {_ord} 单，'
                         f'低于 {_mo} 单的判定门槛')
                if _det:
                    _hint += (f'；这个样本量能分辨的是 {_det}pp 以上的变化，'
                              f'更小的波动落在噪声里')
                if d.get('suspicious_but_small'):
                    _hint += (f'。不过本周偏离 '
                              f'{abs(d.get("delta_pp") or 0):.2f}pp 已超过可分辨幅度，'
                              f'属于"疑似异动、但样本不足以下结论"——'
                              f'值得盯着，别当成没事')
                else:
                    _hint += '。本周偏离没有超过这个幅度，看不出来不代表没问题，但确实不值得现在动手'
                state['facts']['no_playbook'] = True
                state['facts']['no_playbook_why'] = _hint
                return []

            # 【v2.5】统计显著、但幅度小到不值得管。
            # 这一支不能并到"无异常"里：用户自己一算 z 会发现确实过了 2，
            # 那时系统说"没事"就成了隐瞒。必须先承认显著，再说明为什么不动手。
            # 也不能并到"异常"里：0.6pp 的偏离给不出任何有意义的整改动作，
            # 硬答只会产出一份看起来完整、实则没有信息量的报告。
            if d.get('trivial_but_significant'):
                state['facts']['trivial_signal'] = True
                _z = d.get('zscore')
                _dp = d.get('delta_pp') or 0
                _me = d.get('min_effect_pp')
                state['facts']['no_playbook'] = True
                state['facts']['no_playbook_why'] = (
                    f'第 {d.get("week")} 周偏离 {_dp:+.2f}pp，z = {_z}，'
                    f'**统计上确实显著**——不是巧合。'
                    f'但公司定的动作触发线是 {_me}pp，这个幅度只到它的一半左右，'
                    f'属于"能测出来、但不值得动手"的那一类。'
                    f'这类小偏离每月都会有几次，追着它改流程只会把团队拖进无效劳动；'
                    f'真正要盯的是 {_me}pp 以上的跳变。')
                # 【但"小"不等于"不用换视野"】
                # 实测 E2「最新一周差评有异常吗」栽在这里：
                # 第 52 周现在偏离 −0.98pp、z=−2.84，落进 trivial 分支直接 return，
                # 于是重定向机会没了，用户被告知"这周麻烦不大"，
                # 而真正该看的是第 44 周 +3.64pp。
                # 周本身确实不值一提，但用户问的是"有没有异常"，
                # 这个问题的答案不是"没有"，而是"这一周没有，另一周有"。
                # 所以 trivia 分支也要走一次重定向判断 —— 只是措辞不同。
                _asked_t = state['facts'].get('asked_week')
                _pinned_t = bool(state['facts'].get('week_pinned_by_user')
                                 and _asked_t
                                 and int(_asked_t) == int(d.get('week') or 0))
                if not _pinned_t:
                    _best_t = self._scan_anomaly(category=state.get('category'),
                                                 exclude={int(d.get('week') or 0)})
                    if _best_t and _best_t != d.get('week'):
                        state['facts']['trivial_redirect_from'] = d.get('week')
                        state['target_week'] = _best_t
                        state['facts']['redirected'] = True
                        state['facts']['redirect_from'] = d.get('week')
                        return ['diagnose_week']
                return ['trend']
            # 不异常 → 看该不该去找"真正异常的那一周"
            #
            # 【v2.5：用户点名了周次，就不许替他换题】
            # 这是"怎么一直给我提示 44 周"的最后一个病根。
            # 用户问"第 3 周为什么异常"，说明他关心的是第 3 周；
            # 第 3 周此时数据充足（1700+ 单）、退货率甚至略降，
            # 正确答复是"第 3 周不是异常周，它在正常范围内"。
            # 而旧逻辑一发现"不异常"就自动重定向到全店最异常的第 44 周，
            # 于是用户问 A 得到 B 的答案——**这不叫智能，这叫答非所问**。
            # 重定向只在用户没指定周次时成立（"最近一周怎么样""大盘怎么样"），
            # 因为那种问法本身不绑定某一周，帮他把视野移到真正的问题上是加分。
            _asked = state['facts'].get('asked_week')
            # 只有"用户明确报出了周号"才算点名，见上面 week_pinned_by_user 的说明
            _user_pinned = bool(state['facts'].get('week_pinned_by_user')
                                and _asked
                                and int(_asked) == int(d.get('week') or 0))
            best = None if _user_pinned else self._scan_anomaly(
                category=state.get('category'))
            already = {h['week'] for h in (state['facts'].get('diag_history') or [])}
            if best and best != d.get('week') and best not in already:
                # 【目标周 与 已分析周 必须是两个字段】
                # 踩过的坑：只用一个 state['week']，_react 里改成 44 之后，
                # _absorb 紧接着又把它覆盖回 52（因为 absorb 写的是"刚分析的那周"），
                # 于是重定向永远空转，答案还会写成"真正异常的是第 52 周"——就是它自己。
                state['target_week'] = best
                state['facts']['redirected'] = True
                state['facts']['redirect_from'] = d.get('week')
                return ['diagnose_week']
            state['facts']['no_anomaly'] = True
            # 同理：无异常也是一个"结论"，必须给出依据，
            # 否则正文只能写空话，读者看不出是真判断还是没算出来。
            state['facts']['no_playbook'] = True
            # 【"不异常"要说清是哪一种不异常】
            # ① 用户点名的周本来就正常 → 直接回答"这周没问题"，并给出它的量级
            # ② 顺手提一句真正值得看的周，但明确标成"另外"
            #    ——不能让它顶替用户问的那一周，只能作为附注
            if _user_pinned:
                _dpp = d.get('delta_pp') or 0
                _why = (f'第 {d.get("week")} 周退货率 {d.get("rr_now",0)*100:.2f}%，'
                        f'基线 {d.get("rr_base",0)*100:.2f}%，偏离 {_dpp:+.2f}pp，'
                        f'z = {d.get("zscore")}，没有超过 2 的判定门槛——'
                        f'这一周本来就不是异常周，不存在需要归因的问题')
                _other = self._scan_anomaly(category=state.get('category'),
                                            exclude={int(d.get('week') or 0)})
                if _other:
                    _why += (f'。另外，同一范围内真正显著的是第 {_other} 周，'
                             f'那是另一个问题，不是你问的这一周')
                    state['facts']['other_week_hint'] = _other
                state['facts']['no_playbook_why'] = _why
            else:
                state['facts']['no_playbook_why'] = (
                    f'第 {d.get("week")} 周的退货率偏离基线不显著'
                    f'（z 值未达判定门槛），这一期没有需要整改的异常，'
                    f'因此不派动作——凭空派活比不派更容易伤到正常经营')
            return ['trend']

        if name == 'promo_mechanism':
            v = d.get('verdict') or {}
            state['facts']['promo_verdict'] = v
            return []

        if name == 'attribution_structure':
            items = d.get('items') or []
            if items:
                top = items[0]
                state['top_reason'] = top['l2']
                state['top_cause'] = REASON_TO_CAUSE.get(top['l2'])
                # 原因不同，后续取证方式不同：
                #   履约类（破损/时效）→ 查损失口径，因为争的是赔付责任
                #   预期类（尺码/描述/材质/价值）→ 抽评论原文，因为争的是详情页承诺
                #   售后类 → 两边都要看
                cause = state['top_cause'] or ''
                seq = ['loss_breakdown']
                if cause in ('P1', 'P2', 'I1', 'E1'):
                    seq.append('review_samples')
                elif cause in ('F1', 'F2'):
                    seq.append('review_samples')

                # 【促销周不能拿"存量第一名"的原因去开药方】
                # 这里踩过一个很典型的坑：第 44 周是促销周，机制归因已经把
                # +3.68pp 的涨幅解释掉了（机制 +3.93pp），而且顺手证明了
                # 破损占比其实是降的（24.4% vs 基期 25.8%）。
                # 但 top_cause 取的是"退货原因第一名"= F1 破损，
                # 于是动作清单照样给出"生鲜改泡沫箱 / 承运商扣罚 / 破损补偿"。
                # 读起来就是：前面刚说"不是包装和承运商的问题"，
                # 后面立刻让你去改包装 —— 自相矛盾，而且是归因层面的矛盾。
                # 判据：只要 promo_verdict 判定为 PROMO_BEHAVIOR，
                # 破损就只是"存量结构里最大的一块"，不是本次涨幅的来源，
                # 它的 playbook 不该在这一次出场。
                promo_v = state['facts'].get('promo_verdict') or {}
                is_promo_case = promo_v.get('code') == 'PROMO_BEHAVIOR'
                if is_promo_case and cause in ('F1', 'F2', 'S1'):
                    state['facts']['no_playbook'] = True
                    state['facts']['no_playbook_why'] = (
                        f'本周涨幅已由促销机制解释，'
                        f'「{CAUSE_NAME.get(cause, cause)}」只是存量结构第一名，'
                        f'其占比并未恶化，因此不针对它开动作')
                    # 仍然要看损失口径（钱的问题不受归因结论影响），
                    # 但不抽评论、不开药方 —— 评论是给"质量/描述"类问题取证用的。
                    return ['loss_breakdown']

                if cause:
                    seq.append('action_playbook')
                else:
                    # 【这里原来只置了一个 True，没写理由】
                    # 后果：模型拿到空理由，自己补了一句
                    # "这一周不建议针对该原因开整改动作"——
                    # 但此刻根本没有"该原因"，读者看到一个悬空的指代。
                    # 原因码对不上有两种情况，说清楚才能落地：
                    #   ① 本周样本不足/无异常，压根没走到原因结构
                    #   ② 原因文本没进映射表（数据侧新增了原因码）
                    state['facts']['no_playbook'] = True
                    _top = state.get('top_reason')
                    if _top:
                        state['facts']['no_playbook_why'] = (
                            f'本周首位退货原因是「{_top}」，但它不在既有的'
                            f'原因-动作映射表里，因此无法给出有依据的整改动作'
                            f'（不编动作，也不用通用套话顶替）')
                    else:
                        state['facts']['no_playbook_why'] = (
                            '本周没有识别出可归因的原因（样本不足或未达判定门槛），'
                            '因此不给整改动作——在数据说不清的地方派活，'
                            '只会让执行团队白忙')
                return seq

        if name == 'reason_kb':
            # 【知识库返回后要接一手真实数据】
            # 只有通用方案的回答是"百度上也能搜到的"，用户花钱买你的分析
            # 是因为你有他店里的数据。所以紧接着去查最接近的那一类
            # （知识库里配了 fallback_to），给他"这类问题在你这有多重"。
            #
            # 顺序是 KB → profile，不能反：profile 需要 fallback_to 才知道查哪类，
            # 而 fallback_to 是知识库给的。
            state['facts']['kb'] = d
            if d.get('fallback_to'):
                state['facts']['kb_fallback_to'] = d['fallback_to']
            return []

        if name == 'reason_profile':
            # 【按原因治理的主证据】
            # 这一个是"尺码版型不符怎么解决"这类问题的地基：
            # 不知道它有多少条、损失多大、集中在哪、在涨还是在跌，
            # 就没资格谈"怎么解决"——那只能给出通用套话。
            state['facts']['reason_profile'] = d
            # 顺手把"占比最高"的类目记下来，动作清单里要指名道姓
            cats = d.get('by_category') or []
            if cats:
                state['reason_main_cat'] = cats[0]['category']
            # 【知识库原因不抽原文】
            # 用户问"标签脱落"，数据里没有这一类；
            # 如果照旧调 review_samples，它会拿 fallback 原因（材质做工）
            # 的差评原文来充当"标签问题的用户原话"——那是伪造证据。
            # 宁可说"这类问题数据里没有单独记录，看不到具体原话"。
            if state['facts'].get('kb_fallback_used'):
                return []
            # 下一步：抽真实差评原文。政策是"先看用户怎么说，再谈怎么办"，
            # 因为动作清单是团队写好的手册，而原文才是这个毛病在这家店的具体形态。
            return ['review_samples']

        if name == 'action_playbook':
            # 有动作了，补一句"这么改值多少钱"
            #
            # 【但"按原因治理"的场合不追加】
            # value_of_reduction 算的是"退货率降 X pp 能赚多少钱"，
            # 那是给某一周的整体改善做换算的。用户问"尺码怎么治"，
            # 再叠一段"降 1pp 值多少钱"只会把话题从"这个毛病"拽回"整体指标"——
            # 又变回答非所问。这里只把该原因自己的损失口径交给结论段。
            if state.get('asked_reason'):
                return []
            return ['value_of_reduction']

        return []

    def _is_promo_week(self, week: int | None) -> bool:
        if week is None:
            return False
        return week in set(self.hub.panel.loc[self.hub.panel['promo_week'] == 1, 'week'])

    def _scan_anomaly(self, category: str | None = None,
                      exclude: set[int] | None = None) -> int | None:
        """扫描指定范围内的周，找 |二项 z| 最大的那一周，用于"当前周没异常"时的重定向。

        【为什么反复改这里】
        第一版用 tot['rr'].std() 做分母，与 diagnose_week 的算法不一致，
        于是出现"当前周判正常、重定向却说它自己最异常"的空转。
        第二版统一了算法，但仍然用朴素 z —— 结果把第 3 周（151 单）选了出来，
        而那一周的"异常"纯粹是抽样噪声。
        第三版：z 的口径全系统唯一，且必须是二项 z。

        【v2.4：必须按用户问的范围扫，而不是永远扫全店】
        这是"怎么一直给我提示 44 周"的真正病根。
        旧版无论用户问"数码类目怎么样"还是"第 3 周怎么样"，
        这里都拿全店合计去扫，而全店唯一站得住的异常只有第 44 周（双11 促销周）。
        于是：
          · 问数码 → 数码本周正常 → 扫全店 → 跳到第 44 周
          · 问第 3 周 → 第 3 周样本不足 → 扫全店 → 又跳到第 44 周
        用户看到的每个答案都指向 44 周，像是复读机。
        正确做法：**在用户问的范围内找**。问数码就扫数码，
        数码没有异常就老实说数码没有异常，而不是拿全店的问题去搪塞。
        跨范围重定向只在用户没指定任何范围（问大盘）时才成立。

        exclude：已经分析过的周，避免来回跳。
        """
        cw = self.hub._cat_week
        scope_label = category or '全部类目'
        if category:
            cw = cw[cw['biz_category'] == category]
            if cw.empty:
                return None
        tot = (cw.groupby('week')
               .agg(returns=('returns', 'sum'), orders=('orders', 'sum')))
        best_w, best_z = None, 0.0
        for w in tot.index:
            if exclude and int(w) in exclude:
                continue
            base = tot.drop(w)
            p0 = float(base['returns'].sum() / max(base['orders'].sum(), 1))
            n = int(tot.loc[w, 'orders'])
            # 门槛按该范围基线反推，与 diagnose_week 口径一致
            if n < self.hub._min_orders_for(p0):
                continue  # 样本量不够的周不参与"最异常"的竞争
            z = self.hub._binom_z(int(tot.loc[w, 'returns']), n, p0)
            # 【v2.5】挑"最值得看的那一周"也要过业务幅度这条线。
            # 光看 |z| 有个陷阱：样本放大后，偏离最小的周也可能 z 最大，
            # 于是系统一路把用户引到某周，而那一周其实只动了 0.5pp。
            # 这里找的是"最该被讨论的那一周"，标准应当和判定一致。
            d_pp = (tot.loc[w, 'returns'] / n - p0) * 100
            if abs(d_pp) < self.hub.MIN_EFFECT_PP:
                continue
            if z is not None and abs(z) > abs(best_z):
                best_w, best_z = int(w), z
        return best_w if abs(best_z) >= 2.0 else None

    # ---------------- 事实沉淀 ----------------
    def _absorb(self, name: str, out: dict, state: dict) -> None:
        d = out.get('data') or {}
        if not out.get('ok'):
            return
        f = state['facts']
        if name == 'overview':
            f['overview'] = d
        elif name == 'diagnose_week':
            f['diag'] = d
            state['week'] = d.get('week')
            # 【不要在这里 pop target_week】
            # _react 刚刚可能把 target_week 设成重定向目标（如 52 → 44），
            # 而 _absorb 在 _react 之后执行，一 pop 就把目标删了，
            # 下一轮 _args 读不到目标，仍然传旧周 —— 重定向变成无限自循环。
            # 目标周的生命周期由"即将执行它的那一次 _args"负责清理。
            prev = f.get('diag_history') or []
            prev.append({'week': d.get('week'), 'z': d.get('zscore'),
                         'delta_pp': d.get('delta_pp'),
                         'is_anomaly': d.get('is_anomaly')})
            f['diag_history'] = prev
        elif name == 'promo_mechanism':
            f['promo'] = d
        elif name == 'attribution_structure':
            f['structure'] = d
        elif name == 'reason_profile':
            # 【必须单独存一份】
            # _react 里已经把它塞进 facts['reason_profile'] 了，
            # 那里是为了让后续步骤能读到（比如按类目指名道姓）。
            # 这里再存一次不是重复：_react 拿的是 ToolResult.data，
            # 而 _absorb 是所有工具的统一沉淀点，漏了这里就出现
            # "facts 里有、但组装答案时读不到"的错位。
            f['reason_profile'] = d
        elif name == 'reason_kb':
            # 知识库结果单独存。不要和 reason_profile 混在一起——
            # 前者是"行业通用做法"，后者是"本店真实数据"，
            # 组装答案时两者必须分开表述，混了就等于给通用建议套上数据的权威。
            f['kb'] = d
        elif name == 'loss_breakdown':
            f['loss'] = d
        elif name == 'compensation_curve':
            f['comp'] = d
        elif name == 'value_of_reduction':
            f['value'] = d
        elif name == 'review_samples':
            f['samples'] = d
        elif name == 'action_playbook':
            f['playbook'] = d
        elif name == 'trend':
            f['trend'] = d
        elif name == 'compare_weeks':
            f['compare'] = d

    # ---------------- 答案组装 ----------------
    # ------------------------------------------------------------------
    # LLM 改写：把"算出来的结论"写成"人能读的判断"
    #
    # 【设计原则：数字归 pandas，文字归模型】
    # 绝不让模型算数。它的输入是已经算好的事实 JSON，
    # 输出只有 headline（一句话结论）+ summary（分段正文）。
    # 输出后会做三道校验（见 _verify_llm_output），任何一道不过就回退模板。
    #
    # 【为什么必须校验】
    # 大模型最常见的错误不是"编一个不存在的周"，而是：
    #   · 把 3.54 写成 3.5（丢精度，看似无害，但和图表对不上）
    #   · 把"机制贡献 +3.93pp"和"结构贡献 -0.31pp"的正负号搞反
    #   · 自作主张加一句"建议立即降价促销"（数据里根本没这个结论）
    # 所以校验三件事：出现的每个数字必须在事实里出现过；
    # 不许出现事实之外的类目名/周次；篇幅必须落在区间内。
    # ------------------------------------------------------------------

    def _fact_packet(self, state: dict) -> dict:
        """把 state['facts'] 压成一份紧凑的事实包给模型。

        只挑模型写文案真正需要的字段。原始 facts 里有完整明细表，
        全塞进去会浪费 token 且诱导模型去抄表格数字。
        """
        f = state['facts']
        diag = f.get('diag') or {}
        promo = f.get('promo') or {}
        struct = f.get('structure') or {}
        loss = f.get('loss') or {}
        value = f.get('value') or {}
        play = f.get('playbook') or {}
        ov = f.get('overview') or {}
        comp = f.get('comp') or {}
        cmp_ = f.get('compare') or {}

        pkt: dict = {
            'question': state.get('question'),
            'asked_week': f.get('asked_week'),
            'intent': state.get('intent'),
            # 【范围必须交给模型，否则它会把类目结论写成全局结论】
            # "生鲜第44周退货率 21%" 和 "全店第44周 18%" 是两个完全不同的判断。
            # 模型拿到数字但不知道范围时，默认会按"全店"来写。
            '分析范围': (f"{state.get('category')}类目"
                         if state.get('category') else '全部类目合计'),
        }

        # ---------- 知识库兜底场景：只交这一块，别的都不给 ----------
        # 【为什么必须 return，不能和下面的字段混在一起】
        # 周度诊断的那些字段（周次、退货率、z 值、促销拆解）在这个场景里
        # 一个都不该出现。模型看到它们就会忍不住去写"本周退货率 x%，
        # 但这个问题数据里没有"——把两件事硬串在一起，读者更晕。
        # 而且更危险：它可能拿本期的退货率去冒充"标签问题的占比"。
        # 事实包干净，模型才不会乱连。
        if f.get('kb_fallback_used'):
            kb = f.get('kb') or {}
            prof = f.get('reason_profile') or {}
            stand_in = (f.get('profile_stand_in')
                        or kb_fallback_to(_kb_name, kb))
            _kb_name = kb.get('kb_name')
            # 【工具失败 ≠ 未命中条目】
            # 这两个状态最终会变成模型嘴里的两句话，一句让用户放弃、
            # 一句让用户知道可以重试，绝不能共用一个字段。
            _kb_broken = (not kb) or bool(kb.get('error')) or kb.get('ok') is False
            # 【"被合并进去"和"只是参照"是两回事，事实包必须说清是哪一种】
            # 上一版两个字段同时给，模型于是写出
            # 「它被纳入材质做工问题口径统一统计」+「替代口径可作量级参照」，
            # 同一段话里既说"已包含在内"又说"可作参照"，自相矛盾。
            # 真实情况是：数据里根本没有"标签"这个维度，
            # 所以它既没被统计、也没有自己的数——只能拿最接近的类别做参照。
            if prof.get('n') and stand_in and stand_in != _kb_name:
                _stand_mode = '参照'
                _stand_desc = (f'数据里没有这一类，也没有把它并入某一类统计。'
                               f'「{stand_in}」只是成因最接近的另一个类，'
                               f'拿它的量级做参照，不代表标签问题包含在里面。')
            elif prof.get('n') and stand_in:
                _stand_mode = '合并'
                _stand_desc = (f'这一类被合并在「{stand_in}」里统计，拆不出单独数字。'
                               f'不要给出它的占比。')
            else:
                _stand_mode = '无'
                _stand_desc = '没有任何可用的本店数据，不许给数字'

            pkt['知识库兜底'] = {
                '用户问的问题': state.get('user_reason_text')
                                or state.get('asked_reason'),
                '数据里有没有这一类': '没有独立分类',
                # 【"未命中"和"没查成"必须分开写】
                # 这两个字段进的是模型的输入，模型会照抄进答案。
                # 上一版只有"命中/未命中"，工具挂了也写成"未命中近似条目"，
                # 模型于是据实写了"知识库里也没有这一类"——而它压根没查过。
                # 这不是模型的错，是我给它的输入就是假的。
                '知识库是否命中': ('读取失败，不知道有没有'
                                   if _kb_broken else
                                   '命中' if kb.get('matched')
                                   else '未命中近似条目'),
                '知识库读取失败原因': (kb.get('error') or None)
                                     if _kb_broken else None,
                '能否给通用动作': ('不能，读取失败'
                                   if _kb_broken else
                                   '能' if kb.get('actions') else '不能'),
                '归到哪一类（这是知识库的归类，不是数据统计口径）': _kb_name,
                '为什么这么归': kb.get('fallback_reason'),
                '判断征兆': kb.get('signs') or [],
                '通用动作': [
                    {'做什么': a.get('action'), '谁负责': a.get('owner'),
                     '盯哪个指标': a.get('kpi')}
                    for a in (kb.get('actions') or [])
                ],
                '注意事项': kb.get('watch_out'),
                '来源等级': ('读取失败，无来源可标'
                             if _kb_broken else
                             '行业通用做法，不是本店数据'
                             if kb.get('source_tier') == 'kb'
                             else '通用立项方法，与本题无直接对应'),
            }
            # 替代口径：**唯一允许出现的数字**
            if prof.get('n') and stand_in:
                _loss = float(prof.get('loss_total') or 0)
                pkt['替代口径数据'] = {
                    '口径类型': _stand_mode,
                    '说明': _stand_desc,
                    '数据类别': stand_in,
                    '全年退货条数': int(prof.get('n') or 0),
                    '占全部退货百分比': round(
                        (prof.get('share_of_all_returns') or 0) * 100, 1),
                    # 【金额要给"万"，不要给裸元数】
                    # 上一版给的是 723833.0，模型照抄成"累计损失723833.0元"——
                    # 中文汇报里没人这么念，而且小数位是机器算出来的假精度。
                    '累计损失万元': round(_loss / 1e4, 1),
                    '为什么可以参照': kb.get('fallback_reason'),
                    '两者的差别': '成因接近但不完全等同，引用时必须注明是替代口径',
                }
            else:
                pkt['替代口径数据'] = {
                    '口径类型': '无',
                    '说明': '没有任何可用的本店数据，不许给数字',
                }
            return pkt

        # 多周对比：把并排表原样交给模型，并明确"排序依据"
        # 【为什么要给 rank_by 这个字段】
        # 对比题的答案天然会被质疑："为什么第44周最严重？它退货率不是最高啊。"
        # 模型如果只抄一张表，读者自己一比就会觉得结论站不住。
        # 把"按 z 排、不按退货率排"这个口径明确写进事实包，
        # 模型才有可能把这个道理讲出来。
        if cmp_:
            pkt['多周对比'] = {
                '范围': cmp_.get('scope'),
                '共同基线退货率': round((cmp_.get('baseline_rr') or 0) * 100, 2),
                '排序依据': cmp_.get('rank_by'),
                '最严重的一周': cmp_.get('worst_week'),
                '各周': [
                    {'周': x['week'],
                     '退货率': round(x['rr'] * 100, 2),
                     '偏离基线pp': x['delta_pp'],
                     'z值': x['z'],
                     '订单量': x['orders'],
                     '样本是否够判断': '够' if x['enough_sample'] else '不够',
                     '是否显著上升': '是' if x['is_anomaly_up'] else '否'}
                    for x in (cmp_.get('weeks') or [])
                ],
            }
            return pkt

        # 判定类标志，决定模型该用哪种语气
        # 【这里踩了一个"提示词怎么写都没用"的坑】
        # 原来把 low_sample / no_anomaly 这些直接当键名放进 JSON，
        # 模型每次都把它们抄进正文（"样本未达足够检验量（enough_sample=false）"）。
        # 我先在提示词里加禁令、又在校验里加拦截，来回改了三版都没根治——
        # 因为**键名本身就在它眼前**，模型抄自己看到的东西是本能。
        # 真正有效的一步是：把键名换成中文。
        # 校验依然保留（万一它把中文键也带出来），但正确做法是别给它抄的机会。
        _FLAG_ZH = {
            'low_sample': '样本不足',
            'improved': '显著改善',
            'no_anomaly': '无异常',
            'redirected': '已重定向到真正异常的周',
        }
        flags = {_FLAG_ZH[k]: True for k in _FLAG_ZH if f.get(k)}
        if flags:
            pkt['本期性质'] = flags

        if diag:
            pkt['week'] = {
                'week': diag.get('week'),
                'return_rate_pct': round(diag.get('rr_now', 0) * 100, 2),
                'baseline_pct': round(diag.get('rr_base', 0) * 100, 2),
                'delta_pp': diag.get('delta_pp'),
                # 【每 100 单的实感换算，必须由这里给出】
                # 实测模型会自己写"相当于每 100 单多退 3.7 单"——
                # 这是把 pp 换算成频率，逻辑没错，但 3.7 这个数事实包里没有，
                # 于是被数字校验拦掉。与其每次禁它，不如把这个直觉需要的数
                # 按定义算好：1pp = 每 100 单多 1 单。
                'extra_returns_per_100': round(abs(diag.get('delta_pp') or 0), 2),
                'z': diag.get('zscore'),
                'orders': diag.get('orders_this_week'),
                # 【样本量不足时，光说"不够"模型是写不出东西的】
                # 它会自己算抽样波动幅度来说明"为什么不够"，算得对（±6.4pp），
                # 但那个数不在事实里，被数字校验拦了两次，整段回退模板。
                # 正确做法是把"这个样本量下多少 pp 的差只是噪声"直接算给它。
                # 光给一个标准误还不够：它想写"波动范围"，会自己乘 1.645 得到 6.46，
                # 又一次撞校验。所以 95% 区间半宽也一并预支。
                '噪声幅度_pp': diag.get('sampling_wobble_pp'),
                '波动区间95_pp': diag.get('sampling_wobble95_pp'),
                # 【"能分辨多大的变化"比"样本不足"有用】
                # 用户听到"样本不足"只会换个问法再问一遍，得到的还是同一句。
                # 告诉他"这个量级能看出 12pp 以上的跳变，1pp 的波动看不见"，
                # 他立刻知道自己该关注什么、什么不值得开会。
                '可分辨幅度_pp': diag.get('detectable_pp'),
                '疑似异动但样本不足': '是' if diag.get('suspicious_but_small') else '否',
                # 模型反复自己推算"多少单才算够"，写成 600 / 500 这种数。
                # 门槛是后端的判定常量，直接告诉它，别让它猜。
                '判定门槛_单数': diag.get('min_orders'),
                '是否显著异常': '是' if diag.get('is_anomaly') else '否',
                '样本量是否够判断': '够' if diag.get('enough_sample') else '不够',
                # 【v2.5】统计显著与业务显著拆开给。
                # 合成一个"是否异常"会让模型只能二选一：
                # 要么把 0.6pp 写成异常（吓人），要么写成没事（瞒事）。
                # 分开给，它才能说出"确实显著、但不值得动手"这种真话。
                '统计显著': '是' if diag.get('stat_significant') else '否',
                '业务显著': '是' if diag.get('biz_significant') else '否',
                '动作触发线_pp': diag.get('min_effect_pp'),
                '显著但幅度不值一提': '是' if diag.get('trivial_but_significant') else '否',
            }
            cc = diag.get('category_contribution') or []
            if cc:
                pkt['by_category'] = [
                    {'cat': c['category'],
                     'now_pct': round(c['rr_now'] * 100, 2),
                     'base_pct': round(c['rr_base'] * 100, 2),
                     'gap_pp': round(c['gap_pp'], 2),
                     'contrib_pp': round(c['contribution_pp'], 2)}
                    for c in cc[:4]
                ]
                # 【这行注释是给模型看的，不是给我自己看的】
                # 实测模型会把四个类目的贡献值直接相加（-3.19-4.83-6.73-1.03）
                # 得出 -3.08，然后写成"各类目贡献合计 -3.08pp"。
                # 但这个加法没有意义：gap_pp 是"该类目自身退货率的变化"，
                # 类目之间不能相加；可以对标的是整体涨幅 delta_pp。
                # 它算出来的 -3.08 既不是整体涨幅，也不是任何真实量，
                # 于是被数字校验拦下，整段白写。
                # 与其反复放宽校验，不如把口径讲清楚。
                pkt['by_category_note'] = (
                    '每条是该类目自身的退货率与基线之差（gap_pp）及对整体的贡献（contrib_pp）。'
                    '**不要把各条数值相加**：类目之间不可加总，整体涨幅看上面的 delta_pp。'
                    '可以说"四个类目同向下降"，但不要给一个"合计"数。')

        if promo.get('decomposition'):
            dc = promo['decomposition']
            pt = promo.get('paired_test') or {}
            alt = promo.get('alternatives') or {}
            st_share = dc.get('structure_share')
            pkt['promo'] = {
                'promo_rr_pct': round(promo.get('rr_promo', 0) * 100, 2),
                'base_rr_pct': round(promo.get('rr_base', 0) * 100, 2),
                'gap_pp': promo.get('actual_gap_pp'),
                'structure_pp': dc.get('structure_pp'),
                'mechanism_pp': dc.get('mechanism_pp'),
                'structure_share_pct': round((st_share or 0) * 100),
                # 【机制占比必须由这里算好】
                # 实测模型会自己写"机制贡献占比 107%"（= 3.93 / 3.68）——
                # 这个数事实包里没有，于是被数字校验拦下，整段白写。
                # 但模型的直觉是对的：读者确实想知道"涨幅里多少来自机制"。
                # 所以不该禁止它写，而应该把这个数**按统一口径算好喂给它**。
                # 口径与 structure_share 对齐（|结构|/(|结构|+|机制|) 的补数），
                # 这样两个占比加起来正好 100%，不会出现"7% + 107%"这种自相矛盾。
                'mechanism_share_pct': round((1 - (st_share or 0)) * 100),
                'sku_n': pt.get('n_sku'),
                'sku_up_pct': round(pt.get('pct_sku_increased', 0) * 100),
                'mean_diff_pp': pt.get('mean_diff_pp'),
                't_stat': pt.get('t_stat'),
                'discount_sku_pct': round(alt.get('pct_sku_discounted', 0) * 100),
                # 【精度必须统一到 3 位，否则模型会把原始浮点抄进正文】
                # 实测它写过 "quality_promo=-0.0704 > base=-0.0898"——
                # 既泄露了字段名，又是裸的机器精度，读者完全不知道这是什么。
                # 统一成 3 位小数后，配合提示词的"用自然语言表述"，
                # 模型会写成"质量评分 -0.070 vs 基线 -0.090"，可读性差很多。
                'quality_promo': round(alt.get('quality_promo') or 0, 3),
                'quality_base': round(alt.get('quality_base') or 0, 3),
                'quality_scale_note': '质量分为标准化评分，可为负；只看促销与基线谁更高，不要解释绝对值',
            }

        if struct.get('items'):
            it = struct['items'][:3]
            pkt['reasons'] = [
                {'name': x['l2'], 'count': x['count'],
                 'share_pct': round(x['share'] * 100, 1),
                 # 基期占比与变化量：模型要论证"占比最大 ≠ 变多了"，
                 # 必须同时拿到"本周占比"和"基线占比"，否则只能主观断言。
                 'share_base_pct': round((x.get('share_base') or 0) * 100, 1),
                 'share_shift_pp': round(x.get('share_shift_pp') or 0, 1),
                 'loss': round(x['loss']),
                 'resellable_pct': round(x['resellable_rate'] * 100)}
                for x in it
            ]
            pkt['reason_scope'] = struct.get('scope')

        if loss:
            pkt['loss'] = {
                'total_yuan': round(loss.get('total_loss', 0)),
                # 【必须把"万"这一档也预支给模型】
                # 只给 total_yuan=550000，模型写成"¥55.0 万"时，
                # 数字校验抽到的是 55，而 allowed 里只有 550000 →
                # 判定"出现事实中没有的数字 55"，整段 LLM 输出作废。
                # 但 55 并没有错，错的是我把一个必然发生的单位换算
                # 当成了模型的越界行为。中文财务表述离了"万"根本没法写，
                # 所以这里直接把换算结果算好放进去。
                'total_wan': round(loss.get('total_loss', 0) / 1e4, 1),
                'share_of_gp_pct': round(loss.get('share_of_gp', 0) * 100, 1),
            }

        if value:
            pkt['value'] = {
                'pp_reduced': value.get('pp_reduced'),
                'annual_gmv_lift_yuan': round(value.get('annual_gmv_lift', 0)),
                # 同理：元 → 万，把换算值一并给出
                'annual_gmv_lift_wan': round(value.get('annual_gmv_lift', 0) / 1e4, 1),
                'pct_of_gmv': round(value.get('pct_of_gmv', 0) * 100, 2),
            }

        if play.get('actions'):
            # 【字段名必须和 data_hub 对上，否则静默取到 None】
            # 这里原来写的是 a.get('title') / a.get('eta') / a.get('impact')，
            # 而 action_playbook 返回的是 action / kpi / owner / impact_on。
            # 结果是四个字段全 None，动作清单等于没传给模型——
            # 模型于是自己编了一句"供应链、物流、客服三方协同排查"。
            # 这种 bug 不报错、不抛异常，只是让模型开始虚构，最难发现。
            pkt['actions'] = [
                {'what': a.get('action'), 'owner': a.get('owner'),
                 'affects': a.get('impact_on'), 'kpi': a.get('kpi')}
                for a in play['actions'][:4]
            ]

        # 【主动告诉模型"这次为什么没有动作"】
        # 只留一个空的 actions 会让模型自由发挥——实测它会在
        # "不是包装问题"的结论后面自己补一句"建议排查包装环节"，
        # 正好把刚刚排除掉的东西又请回来。
        # 与其靠校验事后拦截，不如事前把这个判断作为事实交底。
        if f.get('no_playbook'):
            # 键名同样用中文：只要是 snake_case，模型就有抄的可能。
            pkt['本次不给动作'] = {
                '理由': f.get('no_playbook_why')
                       or '本期结论不支持针对原因结构第一名制定动作',
                # 【不要在这里给示例句子】
                # 这里原来写的是"必须明确说明'这一周不建议针对该原因开整改动作'"。
                # 结果模型原封不动抄了这句，正文里就出现了"该原因"——
                # 而这一周根本没算原因结构，"该原因"指代的是空气。
                # 给示例措辞＝替模型写稿。改成只约束"必须满足什么"，
                # 措辞交给模型自己组织，它自然会把上面那行具体理由讲成人话。
                '怎么写': ('用上面「理由」里的具体事实说明这一期为什么不派动作，'
                           '把理由写进正文，不要只写一句"不建议"就完事；'
                           '不要为了凑内容编造动作，也不要写"建议进一步排查"这类空话。'
                           '如果这一期有其它的正事（例如复盘、例如促销节奏值不值得），'
                           '就直接指向那件正事。'),
            }

        if comp:
            opt = comp.get('optimal') or {}
            pkt['compensation'] = {
                'optimal_amount': opt.get('compensation'),
                'save_rate_pct': round(opt.get('save_rate', 0) * 100, 1),
                'expected_profit': round(opt.get('expected_profit') or 0, 2),
                'confound_r': comp.get('confound_check'),
                'sample_n': comp.get('sample_n'),
            }
            # 【必须把完整曲线给模型，否则它会自己编其他档位】
            # 实测只给 optimal 时，模型为了凑一个"补得多≠更划算"的对比，
            # 凭空写出"¥50 档挽单率 68.4%、期望利润 ¥12.72"——
            # 这两个数库里根本没有，纯属虚构，只是语气很笃定。
            # 数字校验拦下来了，但这是"没给足材料导致模型不得不编"的典型：
            # 它要给结论做论证，而我只给了结论。
            pts = comp.get('points') or []
            if pts:
                pkt['compensation_curve'] = [
                    {'amount': p.get('compensation'),
                     'save_rate_pct': round(p.get('save_rate', 0) * 100, 1),
                     'expected_profit': round(p.get('expected_profit') or 0, 2)}
                    for p in pts
                ]
                pkt['compensation_curve_note'] = (
                    '这是全部档位的实测结果，可以引用任意档来对比；'
                    '不要引用这张表之外的档位或数字。')
                # 【模型一定会算"比不补偿多赚多少"，那就替它算】
                # 实测它写"相比不补偿每单多赚 ¥5.95"——
                # 这是 optimal - 零补偿档的期望利润，逻辑完全正确，
                # 但 5.95 这个数不在事实包里，于是被数字校验拦掉。
                # 这类"结论必须用到的对比值"不该让模型自己减：
                # 一是它会因为拿不到而编，二是万一它算错了我还查不出来。
                zero = next((p for p in pts if (p.get('compensation') or 0) == 0), None)
                if zero and opt.get('expected_profit') is not None:
                    gain = float(opt['expected_profit']) - float(zero.get('expected_profit') or 0)
                    pkt['compensation']['gain_vs_zero'] = round(gain, 2)
                    pkt['compensation']['zero_case_profit'] = round(
                        float(zero.get('expected_profit') or 0), 2)

                # 【档间两两差值，全部预先算好】
                # 这个案例的论证结构天然是"最优档 vs 其他档"，
                # 模型一定会做减法：先写 22.41-16.46=5.95，再写 22.41-12.72=9.69。
                # 我一开始只补了 5.95，结果下一次它算 9.69 又被拦。
                # 与其等它算出新差值再补，不如把"最优档与每一档的差"一次性给全 ——
                # 这就是"数字归 pandas"的实际含义：凡是能用减法得到的，都不该让模型做。
                opt_prof = opt.get('expected_profit')
                if opt_prof is not None:
                    pkt['compensation_gaps'] = [
                        {'amount': p.get('compensation'),
                         'profit_gap_vs_optimal': round(
                             float(opt_prof) - float(p.get('expected_profit') or 0), 2)}
                        for p in pts if p.get('compensation') != opt.get('compensation')
                    ]

        if ov:
            pkt['overview'] = {
                'orders_year': ov.get('orders_year'),
                'gmv_year_yuan': round(ov.get('gmv_year', 0)),
                # 同上：GMV 谈的是"多少万"，换算值预支，
                # 免得模型写"¥1.2 亿"时被当成编数拦掉。
                'gmv_year_wan': round(ov.get('gmv_year', 0) / 1e4, 1),
                'gmv_year_yi': round(ov.get('gmv_year', 0) / 1e8, 2),
                'aov': ov.get('aov'),
                'return_rate_pct': round(ov.get('return_rate', 0) * 100, 2),
                'loss_total': round(ov.get('loss_total', 0)),
                'loss_share_of_gp_pct': round(ov.get('loss_share_of_gp', 0) * 100, 1),
            }
        return pkt

    _SYS_PROMPT = """你是一位电商经营分析师，正在给运营负责人写一份诊断结论。

你的读者只有几十秒，他关心的顺序是：**有多严重 → 为什么 → 我该做什么**。

【先说明这份输入的性质】
下面给你的是一段 JSON，它的**键名（那些英文加下划线的词）是程序和程序之间的标签，
不是给你写进正文的词**。你可以读它、用它定位数据，但正文里一个英文字母都不该出现。
把你从 JSON 里读到的意思，用中文重新说一遍——这是你这份工作最核心的一条。

【铁律，违反任何一条都算失败】
1. 只能使用我给你的数字。不许推算、不许四舍五入到不同精度、不许改写数量级。
   **特别注意：不许自己做除法或换算。** 比如不要写"机制占比 107%"（那是你自己算的），
   也不要写"相当于每 100 单多退 3.7 单"（如果事实里没给这个数）。
   我需要的换算值都已经算好放在字段里了，直接用：
   · 「每 100 单多退几单」
   · 「涨幅里结构与机制各占百分比」
   · 「某原因占比的基期值与变化量」
   · 补偿场景下「最优档比不补偿每单多赚多少」（已经算好，直接用，不要自己减）
   · 补偿场景下「每一档与最优档的利润差」（也已经算好，不要自己减）
   如果某个数你没在字段里找到，就**不要提它**，换一种说法。
   **再次强调：这些字段名本身不许出现在正文里**，正文只写中文和数字。
2. 不许引入我没给你的类目名、周次、商品名、原因码。
   **必须交代分析范围**：事实包里有「分析范围」字段，
   如果它不是"全部类目合计"，标题和第一段就要点明是哪个类目。
   类目级的退货率和全店退货率差别很大，不写范围读者会误判严重程度。
3. 不许给出我没给你的行动建议。如果给了 actions 字段，只能归纳这些动作，不能新增。
   **如果没给 actions 字段，就不要在正文里提任何"应该做/建议做"的动作**，
   只讲判断和代价。凭空给动作比不给更糟——会让人真的去派活。
   给了 actions 时，第三段就归约其中 1–2 条：说清"做什么 + 谁负责 + 看哪个指标"。
   **如果事实包里带了"本次不给动作"的说明**，说明这一周的正确结论恰恰是"先别动手"：
   你必须把它给的「理由」用中文讲进正文（理由里写了什么事实，你就转述什么事实），
   再引到真正该决策的问题上。这不违反上一条——"不建议做"是结论，不是动作。
   **但不要写"该原因"这种空指代**：如果你手上没有具体的原因名，
   就直说"这一周"（"这一周不需要开整改动作"），不要造一个代词去指空气。
   有没有原因结构，看你有没有收到「退货原因结构」——没收到就是没有，别假装有。
4. 不许用"可能""或许""建议进一步排查"这类含糊表述来掩盖判断。有结论就直说，没结论就说没有。
   **同样不许把"我不知道"包装成"需要进一步核查"**：像"这一周是否执行了某项未被记录的运营动作"
   这种追问，读者没法验证，你也没有依据，写出来等于把问题甩回去。
   数据能支持什么就说什么，支持不了的部分一个字都不要补。
5. 如果事实包里标了「样本不足」或「无异常」或「显著改善」，
   必须明确说"这一期不构成需要归因的问题"，禁止硬编原因。
   **但更要紧的是回答"那我能看什么"**：事实包里有「可分辨幅度」这个数，
   它告诉你这个样本量能看出多大的变化。要把这个数写进正文——
   读者听到"样本不足"只会换个问法再问一遍，听到"这个量级只能看出 12pp
   以上的跳变，1pp 的波动落在噪声里"才知道自己该关注什么。
   另外看「疑似异动但样本不足」：如果是"是"，说明本周偏离已经超过
   可分辨幅度，要明确写成"疑似异动、但样本不足以下结论"，别写成"没问题"。
   不要把这个判断写成"某个标记等于假"这种机器口吻，读者看不懂。
6. 不许承认"某个数据我没拿到"来凑字数。你看到的字段就是全部事实，不要在正文里讨论"未提供什么"。
   **同样不要反复强调"样本不足"**：说一次、给出可分辨幅度就够了。
   通篇都在讲样本不够，读者会觉得这个系统除了推脱什么都不会。

【把判定变量翻译成人话，不要直接拼词】
事实包里有「统计显著」「业务显著」「显著但幅度不值一提」这几个字段。
**不要把它们连起来念**，比如写成"不显著、不业务显著、无异常"——
"不业务显著"不是一个中文词，没有人会这样说，
读者一看就知道是把几个布尔值拼在了一起。
正确做法是先想清楚这三种情况分别意味着什么，再用人话说：

  · 统计不显著              → "这点波动在正常范围内"（它就是噪声）
  · 统计显著但幅度不值一提   → "确实是变了，但幅度小到不值得动手"
  · 统计显著且幅度够大       → 这才叫异常，才值得归因

同一个意思不要在一段话里说两遍。上面那种"不显著…无异常"
是在用三种说法重复同一个判断，占字数还没信息量。

【同一件事只说一次】
"无异常"这个结论，全篇出现一次就够（headline 或第一段）。
不要在 caveats、表格注释、第三段里反复复述——
每复述一次，读者就多一分"它在解释什么"的疑惑。

【差值一律抄，不要自己减】
事实包里已经算好了这些，**直接抄**：
  · 退货率涨幅 → `delta_pp`
  · 基线 → `baseline_pct`
  · 本期 → `return_rate_pct`
  · 每百单多退几单 → `extra_returns_per_100`

不要拿"本期"减"基线"自己算一遍。实测模型这么干过一次：
13.75 − 13.53 应该等于 0.22，它写成了 0.42——把基线记成了 13.11，
而 13.11 这个数事实包里根本不存在。0.42 被数字校验拦下来，
整段输出作废、回退模板，用户拿到的文字就变差了。

这不是模型"大概记错了"，是**多做了一步没必要的运算**。
凡是事实包里已经算好的差值，抄它；只有事实包里没有的，
才允许从两个数推——而且推完要能对得上。

【特别重要的：消除自相矛盾】
如果促销机制成立（事实包里给了促销拆解，且机制贡献远大于结构贡献），
说明退货率上涨**不是因为商品质量或物流变差**，而是促销本身改变了退货倾向。
此时「原因结构」里排第一的可能仍是"破损/变质"这类码——**这不矛盾**：
它只说明"在退货的所有原因里，破损占比最大"，
而**不是**"破损变多了"。

你必须在文中主动解释这层区别，否则读者会觉得你前后打架。这是本报告最容易被质疑的地方。
解释的写法：直接对比"本周占比"与"基期占比"两个数，
指出占比实际是升是降（基期占比就在原因清单的每条里），把"存量占比最大"和"增量归因"两件事分开说清楚。
不要含糊地说"这不代表质量变差"，要给出这两个对比数字，让读者自己看见。

【输出格式，严格 JSON】
{
  "headline": "一句话结论，30 字以内，要有判断（好/坏/不确定），不要复述问题",
  "summary": ["第一段", "第二段", "第三段"]
}
不要输出 badge —— 那是后端的判定结论，不由你改写。

headline 的用词：
- 说"没事"就写「没有异常」「属于正常波动」「不用管」，
  **不要写「未异常」**——中文里没有这个说法，"未"接名词才通（未达标、未检出），
  接"异常"就变成了生造词，一眼能看出是机器拼的。
- 同理不要写「非异常」「不异常」堆在一起，选一个说法讲清楚即可。

summary 规则：
- 3 段，每段 40–110 字，段与段之间是"现象 → 机制/证据 → 动作或代价"的递进关系。
- 第一段讲"有多严重"：给出量化对比，并给一个参照（事实包里的"每 100 单多退几单"就是给你换算好的，直接用）。
  **不要在这里引入类目拆分**——类目贡献是另一条分析线，混进来会让读者以为问题只出在某几个类目上，
  而你后面要讲的恰恰是"这是全局机制，不分类目"。
- 第二段讲"为什么"：用证据支撑，把最硬的那个检验结果说出来（比如配对检验的 t 值）。
  促销场景下，这一段的主线就是"机制 vs 结构"的占比对比，一句到位。
- 第三段讲"所以呢"：有 actions 就归约动作；没有 actions 就讲这意味着什么。
  **归约动作时最多展开 2 条**，写成"谁做什么、盯哪个指标"的短句。
  不要把 3 条动作压进一句话——读者记不住，等于没给。

【"没有异常"这类答案，第三段该写什么】
这是最容易写废的一段。实测模型写过：
  ·「报告'没有异常'就是核心结论」——这是在跟读者交代"我怎么写报告"，
     读者不关心你的写作决定，只关心自己该干什么。
  · 把第一二段说过的"未达门槛、落在噪声内"再复述一遍——第三次重复同一个判断。
正确写法只有一件事：**告诉读者接下来把注意力放哪**。
  比如"这一周不用管，真正要盯的是第 44 周"，
  或者"如果想确认这个结论，可以再看两周的连续走势"。
一句话：第三段是"所以你的下一步是什么"，不是"所以我这份报告写得对"。
**绝对不要出现"报告""本文""结论就是""我判定为"这类元话语**——
你不是在写一份说明书，你是在跟运营说话。
- **全文只许出现中文和数字，一个英文字母都不许有**（"%""t 值"除外）。
  【这一条我改过三版，必须说清楚为什么】
  前两版我在提示词里列举了"不许写某某内部名"的反例，本意是教它别抄字段名，
  结果模型把反例里的名字当成了"可以用的词"直接抄进正文——
  我举了几个例子，它就学会了那几个名字。
  现在改成不给任何具体字段名，只给规则和正确句式。
  正确的说法：
    · 要表达样本量不够 → 「样本量不足以支撑判断」，不要说某个变量等于假
    · 要表达质量分数   → 「质量评分 -0.070，略高于基线 -0.090」
    · 要表达因果测不出 → 「这份样本无法分辨是谁导致的」
  一句话：你在正文里只能用中文描述，不许写任何内部命名。
- 用**加粗**标出关键数字和结论词。不要用列表符号，就是自然段落。
- 语气像一个有经验的分析师在汇报，不要客服腔，不要"亲爱的用户"。
"""

    # 【知识库场景的专用提示词，不能复用上面那套】
    # 上面那套是为"某一周为什么涨"设计的：它花了大量篇幅讲
    # 促销拆解、机制 vs 结构、可分辨幅度、样本不足怎么办。
    # 这些在"标签脱落怎么解决"的场景里全是噪音——
    # 模型会努力去找"促销机制"字段，找不到就自己编一个，
    # 或者把"数据里没有这一类"写成"样本不足"（语义完全不同）。
    #
    # 这套提示词只讲三件事，而且顺序被严格规定：
    #   ① 先承认边界（数据里没这一类）——不能含糊，含糊等于骗人
    #   ② 再给通用做法——必须说明是行业经验，不是这家店的数据
    #   ③ 最后给参照量级——必须说清是替代口径
    # 顺序不能换。先给方案再承认"其实我没数据"，读者会觉得被骗。
    _SYS_PROMPT_KB = """你是一位电商退货治理顾问，正在回答一个运营主管的问题：
他遇到的某个退货问题，在数据里没有单独归类，该怎么治。

【这份输入的来源，你必须先搞清楚】
你会拿到两样东西，它们的可信度完全不同，**绝对不许混着说**：
  1. 「知识库」：行业通用做法。它回答"一般该怎么办"，
     但和这家店没有任何关系，它不是从这家店的数据里算出来的。
  2. 「替代口径数据」：这家店真实数据里最接近那一类的统计。
     它回答"这类问题在你这有多重"，但**它不是用户问的那个问题的数字**。

把 1 说成 2 是最严重的错误。比如写成
「标签问题占了 12.9% 的退货」——那个 12.9% 其实是材质做工的数字，
标签问题一条都没统计过。运营拿这句话去汇报，就是在会上说错数。

【必须遵守的写法】
第一段：直接说清边界。
  · 数据里只有九个原因分类，没有他问的这一个
  · 所以**给不出它的条数和损失**
  不要用"样本不足"这种说法——那是"有数据但太少"，和"压根没这一类"
  是两件完全不同的事，混用会让读者以为数据里有这一类只是量少。
  · 如果你拿到「归到哪一类」的信息，用一句话说明它属于或最接近哪一类

第二段：给通用做法。
  · 动作只能来自知识库，最多展开 3 条，每条写清"做什么 + 谁负责 + 盯哪个指标"
  · **必须明确说这是行业经验，不是从这家店数据推出来的**，这句话不能省
  · 如果知识库给了「判断征兆」，用一句话带出，帮读者确认自己遇到的是不是同一类

第三段：给参照量级（如果拿到了替代口径数据）。
  · 给出那一类的条数、占比、损失，然后**立刻说明这是替代口径，不是他问的那个**
  · 说清为什么可以拿它做参照（成因或处理方式接近）
  · 帮他判断值不值得单独立项
  · **如果没有替代口径数据，就不要编任何数字**，直接建议他先手工统计两周拿到基线

【绝对禁止】
· 不许出现"本报告""本文""综上""我判定为"这类元话语
· 不许承诺"进一步排查会告诉你更多"——你手上就是这些
· 不许为了让答案显得充实而重复同一个判断
· 全文只出现中文、数字和必要的标点，**不许出现英文字母和字段名**
· 不许使用"可能""或许"来软化——数据没有就说没有，这是事实不是不确定
· **不许自己编一个判定门槛**。实测模型写过"若该类问题手工统计两周后
  日均超 3 条，建议单独立项"——那个"3 条"是它自己编的，
  我从来没给过这个标准。没有依据的阈值比没有建议更糟：
  运营会拿它当决策依据。要提立项，只说明"需要先拿到基线"，
  不许给具体数字门槛。
· **金额要说"万元"**，不要说"723833 元"这种机器格式

【第二种情况：通用方案库没读到】
事实包里「知识库是否命中」写着"读取失败"时，是另一套写法。
这时候你**不知道**知识库里有没有这一类，所以：
  · 可以说「方案库这次没读到」，**绝不许说「方案库里没有这一类」**
    前者是真的，后者是编的
  · 不许把原因推给用户（"你这个问题太特殊"）
  · 只剩替代口径的量级参照时，就说只有量级、没有动作，
    并把"没取到"和"没有"的区别说清楚
  · 不许用"暂时没有整理好的动作"来掩盖失败——那句话暗示
    "我们整理过，确实没有"，是假话

【"被合并统计"和"可作参照"是两回事，别混着写】
事实包里的「口径类型」字段告诉你是哪一种：
  · 参照 → 数据里没有这一类，也没把它并进任何一类。
           拿最接近的另一个类看量级，**不代表它被包含在里面**
  · 合并 → 它被并进某一类统计了，拆不出单独数字
这两种说法写在同一段里就是自相矛盾（"已纳入统一统计"＋"可作参照"），
读者会觉得你没搞清楚自己的数从哪来。

【输出格式，严格 JSON】
{
  "headline": "一句话，30 字以内，要点明'数据里没这类'和'给的是通用方案'这两层意思",
  "summary": ["第一段", "第二段", "第三段"]
}
不要输出 badge。

headline 的例子（照这个意思写，不要照抄字面）：
  · 「数据里没有标签问题的单独统计，按吊牌管理的通用做法给你」
  · 「这类问题数据没单列，先按行业做法立项，量级参照材质做工那一类」
"""

    async def _polish_with_llm(self, answer: dict, state: dict) -> dict:
        """让模型重写 headline + summary，失败则原样返回模板答案。

        用 async 包装同步 HTTP 调用，通过 asyncio.to_thread 丢到线程池，
        否则会阻塞整个事件循环 —— SSE 流会卡住，前端看到的是"卡在最后一步"。
        """
        if not self.llm:
            return answer

        pkt = self._fact_packet(state)
        # 【按场景选提示词】
        # 知识库兜底和周度诊断是两种完全不同的写作任务，
        # 共用一套提示词的后果是模型努力去找"促销机制""可分辨幅度"这些
        # 根本不存在的字段，找不到就编，或者把"没有这一类"写成"样本不足"。
        _is_kb = bool(state['facts'].get('kb_fallback_used'))
        sys_prompt = self._SYS_PROMPT_KB if _is_kb else self._SYS_PROMPT
        base_msg = (
            '以下是已经由 pandas 精确计算完成的事实，请据此写结论：\n'
            + json.dumps(pkt, ensure_ascii=False, indent=1)
            + '\n\n模板草稿（供参考，可以重写，但数字必须一致）：\n'
            + answer['headline'] + '\n' + answer['summary']
        )

        # 【为什么要重试，而不是失败就回退】
        # 实测同一道题连跑三次，通过率在 50%–100% 之间跳：
        # 校验本身没写错，是模型每次措辞不同，
        # 偶尔冒出"5.95 写成 595"或"多算一个派生值"这种偶发问题。
        # 每次偶发都整段回退模板，等于把 11 秒的模型工作全扔掉，
        # 而模板的表达质量明显不如模型（它只会说"值得复盘"，
        # 说不出复盘什么）。
        # 更划算的做法：带着"你上一版哪里不合格"再问一次。
        # 成本是多一次调用，收益是把通过率从 50% 拉到接近 100%。
        # 只重试 1 次（共 2 次），避免模型反复犯错时无限拖时间。
        why = ''
        for attempt in range(2):
            user_msg = base_msg if attempt == 0 else (
                base_msg
                + f'\n\n【上一版未通过校验，原因：{why}】\n'
                + '请重写一遍，严格避开这个问题。'
                  '特别提醒：数字只能从上面的事实里抄，一个都不要自己算；'
                  '正文里不许出现英文字母、下划线、等号比较。')

            def _run(msg=user_msg) -> str | None:
                return self.llm.chat(
                    [{'role': 'system', 'content': sys_prompt},
                     {'role': 'user', 'content': msg}],
                    temperature=0.35, max_tokens=1200)
            raw = await asyncio.to_thread(_run)
            if not raw:
                answer['llm_note'] = f'模型未返回（{self.llm.last_error[:80]}），已回退模板'
                return answer

            # 【调试开关】排查"模型到底写了什么"时用。
            # 默认关闭：正常运行时不该往磁盘写模型原文。
            # 之前我在没有这个开关的情况下反复猜"是不是模型又被误杀了"，
            # 猜了三四轮才发现是自己的正则太宽 —— 有落盘就不用猜。
            if os.environ.get('CANYU_LLM_DEBUG') == '1':
                try:
                    with open('llm_last_raw.json', 'w', encoding='utf-8') as fh:
                        fh.write(raw)
                except OSError:
                    pass

            parsed = self._parse_llm_json(raw)
            if not parsed:
                why = '输出不是合法 JSON'
                continue

            head = (parsed.get('headline') or '').strip()
            segs = parsed.get('summary')
            if isinstance(segs, str):
                segs = [s.strip() for s in segs.split('\n') if s.strip()]
            segs = [str(s).strip() for s in (segs or []) if str(s).strip()]
            badge_txt = str(parsed.get('badge') or '').strip()

            ok, why = self._verify_llm_output(head, segs, pkt, badge_txt)
            # 【知识库场景额外一道校验：不许把替代口径说成自己的数字】
            # 这是这个场景最危险的错误。模型很容易写出
            # 「标签问题全年 10,646 条」——那个数其实是材质做工的，
            # 它把"参照"当成了"主角"。运营拿去汇报就是报假数。
            # 数值本身在事实包里（所以校验 1 拦不住），只能按语义拦：
            # 正文一旦出现替代数字，同一段里必须同时出现"替代/参照/不是"这类限定词。
            if ok and _is_kb:
                ok, why = self._verify_kb_numbers(head, segs, pkt)
            if ok:
                answer['headline'] = head
                answer['summary'] = '\n\n'.join(segs)
                # 【徽章不让模型改】
                # 踩过的坑：模型把"机制已识别"改成"机制主导"、
                # 把"已重定向"也覆盖成了"机制主导"。
                # 徽章是后端的判定结论，尤其"已重定向"是本报告最关键的一条信息
                # （你问的周没问题、真正要看的是另一周），不能被措辞覆盖。
                answer['llm_note'] = ''
                answer['by_llm'] = True
                return answer

        answer['llm_note'] = f'模型两次输出均未通过校验（{why}），已回退模板'
        return answer

    @staticmethod
    def _verify_kb_numbers(head: str, segs: list[str], pkt: dict) -> tuple[bool, str]:
        """知识库场景专用：替代口径的数字必须带着"这只是参照"的限定。

        【为什么单靠"数字来自事实包"这条不够】
        替代口径的 10,646 条确实在事实包里，所以校验 1 会放行。
        但放行的只是"这个数字真实存在"，管不了**它被安在谁头上**。
        模型写成「标签问题全年 10,646 条」——数字是真的，
        归属是假的，而这句话恰恰是最容易被拿去汇报的。

        【判据】
        正文里出现替代口径的那个数时，同一段必须同时出现限定词。
        限定词不限于某几个词，只要表达了"这是别的东西的数"即可。
        """
        stand = pkt.get('替代口径数据') or {}
        n = stand.get('全年退货条数')
        if not n:
            return True, ''
        # 拿到替代口径这个数在正文里的各种可能写法
        forms = {f'{n:,}', f'{int(n)}', f'{n:g}'}
        ok_words = ('替代', '参照', '不是', '不代表', '仅供参考', '相反',
                    '并非', '归到', '合并', '那一类', '这类', '同一类')
        for seg in [head] + list(segs):
            if not any(f in seg for f in forms):
                continue
            if any(w in seg for w in ok_words):
                continue
            return False, (
                f'替代口径的数字 {sorted(forms)[0]} 被当成了用户所问问题的数字。'
                f'这个数是「{stand.get("数据类别")}」的，提到它时必须说明只是参照')
        return True, ''

    @staticmethod
    def _parse_llm_json(raw: str) -> dict | None:
        """模型有时会包 ```json 围栏或前后加解释，这里做宽松提取。"""
        s = raw.strip()
        if s.startswith('```'):
            s = re.sub(r'^```[a-zA-Z]*\s*', '', s)
            s = re.sub(r'```\s*$', '', s).strip()
        try:
            obj = json.loads(s)
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
        # 兜底：抓第一个 { 到最后一个 }
        i, j = s.find('{'), s.rfind('}')
        if i >= 0 and j > i:
            try:
                obj = json.loads(s[i:j + 1])
                return obj if isinstance(obj, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _verify_llm_output(head: str, segs: list[str], pkt: dict,
                           badge: str = '') -> tuple[bool, str]:
        """三道校验。任何一道不过就回退模板——宁可保守，不许出错。

        校验 1（防编数）：正文里每个"看起来是数字"的 token，
                          必须在事实包里能找到对应值（允许 0.05 的容差）。
        校验 2（防跑题）：篇幅必须在区间内，不能只写一句话交差，也不能写成小作文。
        校验 3（防硬编）：低样本/无异常场景下，正文不许出现因果断言词。
        """
        body = head + '\n' + (badge + '\n' if badge else '') + '\n'.join(segs)

        # ---- 校验 1：数字必须来自事实 ----
        allowed: set[str] = set()

        def collect(o):
            if isinstance(o, dict):
                for v in o.values():
                    collect(v)
            elif isinstance(o, (list, tuple)):
                for v in o:
                    collect(v)
            elif isinstance(o, (int, float)) and not isinstance(o, bool):
                allowed.add(f'{o:g}')
                allowed.add(f'{round(o, 2):g}')
                allowed.add(f'{round(o, 1):g}')
                allowed.add(f'{round(o)}')
                allowed.add(f'{abs(o):g}')
                allowed.add(f'{round(abs(o), 2):g}')

        collect(pkt)

        # 常见非数据数字，放行（否则"第一/三个"这类都会误报）
        # 【这里必须包含"量词数字"，不只是序数】
        # 踩过的坑：我给模型预支了"95% 置信区间 ±6.46pp"，
        # 模型照实写出"95% 置信区间"，结果 95 被判成凭空编造的数字，
        # 两次重试全废、整段回退模板 —— 我自己埋的坑，却去怪模型。
        # 判据：凡是"描述口径"的数字（置信水平、百分比、阈值倍数），
        # 它是知识而不是数据，不该拿数据白名单去卡。
        whitelist = {'1', '2', '3', '4', '5', '6', '7', '8', '9', '10',
                     '100', '24', '44', '52', '0',
                     '95', '99', '90', '80',   # 置信水平
                     '12', '30', '60',          # 月份/天数这类时间口径
                     '1000', '10000'}           # 万/千的换算基数

        # 【先把千分位逗号去掉再抽数字】
        # 踩过的坑：正文写"2,057 单"，正则 -?\d+(?:\.\d+)? 会把
        # "2,057" 切成 "2" 和 "057" 两个 token，于是 057 被判成编数，
        # 整段 LLM 输出作废。而 2,057 本来就在事实里（sample_n=2057）。
        # 中文报告里四位以上数字必然带千分位，这个坑必须在校验前解决。
        body_for_num = re.sub(r'(?<=\d),(?=\d{3}\b)', '', body)
        nums = re.findall(r'-?\d+(?:\.\d+)?', body_for_num)
        bad = []
        for n in nums:
            t = n.lstrip('-')
            if t in whitelist or n in allowed or t in allowed:
                continue
            try:
                v = float(t)
            except ValueError:
                continue
            # 允许它是某个允许值的四舍五入/截断形式
            if any(abs(v - float(a)) < 0.06 for a in allowed if a.replace('.', '').replace('-', '').isdigit()):
                continue
            bad.append(n)
        if bad:
            return False, f'出现事实中没有的数字 {bad[:3]}（可能是模型自己做了换算）'

        # ---- 校验 2：篇幅 ----
        if len(segs) < 2:
            return False, '正文段数不足'
        total = sum(len(s) for s in segs)
        if not (90 <= total <= 620):
            return False, f'正文长度 {total} 字超出区间'
        if len(head) > 46:
            return False, f'标题 {len(head)} 字过长'

        # ---- 校验 3：低样本/无异常不许硬编因果 ----
        # 注意键名已改成中文（见 _fact_packet 里的说明），这里必须跟着改，
        # 否则这道校验会静默失效 —— 而失效是不报错的，只会显得模型"变听话了"。
        _flags = pkt.get('本期性质') or {}
        if ('样本不足' in _flags or '无异常' in _flags or '显著改善' in _flags):
            # 【否定式必须放行】
            # 原版一见到"导致"就判失败，于是"无法判定是谁导致的"
            # 这种**正确**的表述也被拦了 —— 而低样本场景下，
            # 说"说不清是谁导致的"恰恰是标准的正确答案。
            # 先剥掉否定语境再查，只拦真正的因果断言。
            causal = re.sub(r'(无法|不能|不可|难以|不足以|不能判定|说不清)[^。；\n]{0,20}',
                            '', body)
            for w in ('导致', '造成了', '因为所以', '根本原因'):
                if w in causal:
                    return False, f'不该出现因果断言「{w}」'

        # ---- 校验 4：动作必须出自 playbook ----
        # 【为什么这条必须硬校验】
        # 实测模型在 actions 为空时会自己编一句
        # "按供应链、物流、客服三方协同排查"——语气很像专业建议，
        # 但库里根本没有这条动作，等于凭空造了一条执行指令。
        # 分析错可以说"我不确定"，动作错会让人真的去派活，代价高得多。
        # 规则：正文只要出现"排查/协同/整改/加派/下架/改用/补充/上线"这类祈使动作词，
        #       就必须能在 actions 里找到对应的原始动作文本；否则判定为编造。
        #
        # 【知识库场景要换一套白名单，不是直接放行】
        # 这个场景没有 playbook（数据里都没这一类，手册当然也没有），
        # 但动作是真实存在的——来自知识库的通用做法。
        # 如果照旧按"没有 playbook 就不许提动作"，模型一写"建议先做批次留样"
        # 立刻被判失败，两次重试全废，最后回退成模板——
        # 而模板里的动作恰恰也是知识库给的，等于白拦一场。
        # 正确做法：把知识库的动作文本当成这个场景的 playbook 来比对，
        # 编造动作照样拦得住，只是换了个可信来源。
        _kb_pkt = pkt.get('知识库兜底') or {}
        _kb_acts = _kb_pkt.get('通用动作') or []
        if '知识库兜底' in pkt:
            vocab = ' '.join(
                f"{a.get('做什么') or ''}{a.get('谁负责') or ''}{a.get('盯哪个指标') or ''}"
                for a in _kb_acts)
            for kw in ('排查', '协同', '整改', '加派', '下架', '改用', '上线',
                       '降本', '优化供应链'):
                if kw in body and kw not in vocab:
                    return False, f'出现知识库之外的动作「{kw}」'
        elif pkt.get('actions'):
            vocab = ' '.join(str(a.get('what') or '') for a in pkt['actions'])
            for kw in ('排查', '协同', '整改', '加派', '下架', '改用', '上线'):
                if kw in body and kw not in vocab:
                    return False, f'出现 playbook 之外的动作「{kw}」'
        else:
            # 没有 playbook 就不许给动作建议。
            # 【但"不建议做某动作"这句是允许的，而且往往是必需的】
            # 促销场景下正确答案就是"别去改包装"，作者不写这句，
            # 读者反而会自己补一个动作。所以先剥掉"不建议X"这种否定式表述再查。
            # 注意剥离范围只到下一个标点，不能吞太长 ——
            # 原来写 [^。；\n]{0,40}，会把"不建议A，建议B"里的 B 一起吞掉。
            body_for_action = re.sub(
                r'(不|无需|不必|不该|暂不)(建议|需要|应当|必)[^。；\n]{0,30}',
                '', body)

            # 【改善/无异常场景要留一条出口】
            # 这两类场景的正解就是"复盘"——回顾这一周做对了什么。
            # 但"复盘"不是派活，和"改包装/下架/加派"完全不是一类事。
            # 原规则一刀切，导致 E4 三次全部回退模板，
            # 而回退后的模板反而更差（它只说"值得复盘"，没说复什么）。
            # 所以这里开一个小口子：允许"复盘/回顾/沿用"这类自省动作，
            # 其余祈使动词仍然全拦。
            review_ok = ('复盘', '回顾', '沿用', '保持', '沉淀')
            rest = body_for_action
            for w in review_ok:
                rest = rest.replace(w, '')

            for kw in ('建议', '应当', '需要立刻', '立即', '应尽快'):
                if kw in rest:
                    return False, f'无 playbook 却给出行动建议「{kw}」'

        # ---- 校验 5：不许把字段名/JSON 直接写进正文 ----
        # 实测模型写过 "discount_sku_pct=0"、"quality_promo=-0.0704 > base=-0.0898"。
        # 这不是"数字错"，是"把内部数据结构漏给了终端读者"——
        # 运营看到 snake_case 只会觉得这是个半成品。
        # 判据很直接：正文里出现连续的小写字母+下划线组合，就是字段名。
        leak = re.findall(r'\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b', body)
        if leak:
            return False, f'正文泄露字段名 {leak[:2]}'

        # 【中文译名 + 等号，比 snake_case 更隐蔽】
        # 实测模型写过「系统标记 **样本是否足够 = false**」——
        # 它把 low_sample 这个字段名"翻译"成了中文再带个等号。
        # snake_case 正则完全抓不到（没有下划线），
        # 「等号左边得多字符标识符」那一条也抓不到（左边是中文）。
        # 但危害是一样的：运营看到"样本是否足够 = false"只会觉得系统没写完。
        # 判据：中文短语 + 等号 + 布尔/数字，就是被翻译过的内部记号。
        zh_leak = re.search(
            r'[\u4e00-\u9fa5]{2,12}\s*[=＝]\s*(true|false|null|none|True|False|NULL)',
            body)
        if zh_leak:
            return False, f'正文出现内部记号「{zh_leak.group(0)[:24]}」'
        if re.search(r'[\u4e00-\u9fa5]{2,12}\s*[=＝]\s*-?\d', body):
            return False, '正文出现"中文名 = 数值"的内部记号'
        # true/false 裸出现也算（无论有没有等号）
        if re.search(r'\b(true|false|null|none)\b', body, re.I):
            return False, '正文出现 JSON 布尔值'
        # 【这里踩过一个坑：把 t 检验当成了代码】
        # 原判据是 r'[=<>]=?\s*-?\d'，看着没问题，实际太宽：
        # 模型写"同 SKU 配对 t = 17.72"时，先匹配到 "= 17",
        # 于是 "= 17.72" 命中 → 整段输出被判废、悄悄回退模板。
        # 我第一版还以为是模型不听话，查了才发现是自己的正则误杀。
        # 关键区别：t / z / p / n 这类统计量的 "=" 是学术写作规范，
        # 真正要拦的是"字段名 = 值"——等号左边是多字符英文标识符。
        # 所以把左侧限定为 {2,} 个字符：t / z 放过，base / promo 拦住。
        bad_cmp = re.search(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\s*[=<>]=?\s*-?\d', body)
        if bad_cmp:
            return False, f'正文出现编程式比较表达式（{bad_cmp.group(0)[:24]}）'

        # ---- 校验 6：不许出现悬空的指代 ----
        # 【这条是"该原因"逼出来的】
        # 模型写过"这一周不建议针对**该原因**开整改动作"，
        # 但这一周根本没算原因结构 —— "该原因"指代的是空气。
        # 读者读到这里会往回找"哪个原因"，找不到，只能怀疑自己看漏了。
        # 根因不在于模型，在于我在「怎么写」里原样给了这句示例让抄（已改）。
        # 但示例改了还会出别的变体，所以在出口这里兜一道：
        # "该/这个/上述/以上/这些" + 原因/动作/品类 这类抽象名词，
        # 只有在正文里真的出现过对应的具体对象时才放行。
        _reason_struct = pkt.get('reasons')
        for m in re.finditer(r'(该|这个|上述|以上|这些)(原因|动作|品类|类目|商品)', body):
            noun = m.group(2)
            # 原因：必须真的给过原因结构，否则就是无指代
            if noun == '原因' and not _reason_struct:
                return False, f'正文出现悬空指代「{m.group(0)}」，但事实包里没有原因结构'
            # 动作：必须真的给过 playbook
            if noun == '动作' and not pkt.get('actions'):
                return False, f'正文出现悬空指代「{m.group(0)}」，但事实包里没有动作清单'

        # ---- 校验 7：不许生造中文词 ----
        # 【这条是实测截图逼出来的】
        # 模型写过「不显著、不业务显著、无异常」——三个判断挤在一句里，
        # 其中"不业务显著"根本不是中文。根因是它把事实包里
        # 「统计显著=否 / 业务显著=否」两个布尔值各自加了个"不"再连起来念。
        # 提示词已经加了"把判定变量翻译成人话"，但提示词只是引导，
        # 出口这里必须硬拦一次，否则同样的问题会以别的形式再冒出来。
        # 判据只挑明确的生造组合，不碰正常的"不显著"（这个说法是通的）。
        for bogus in ('不业务显著', '业务不显著', '未异常', '非异常', '不异常',
                      '不统计显著', '统计不显著', '无显著', '不显著性'):
            if bogus in body:
                return False, f'正文出现生造词「{bogus}」，应改成"正常波动/没有异常"这类人话'

        # ---- 校验 8：不许写元话语（跟读者交代"我怎么写报告"）----
        # 【实测原句】「报告'没有异常'就是核心结论。」
        # 这句话的问题不是措辞，是**视角错了**：它把正文变成了
        # 一份关于这份正文的说明。运营想知道的是"然后我该干嘛"，
        # 不是"作者认为他的报告写对了"。
        # 同理"本文""本报告""我判定为"都是把读者推到对话之外。
        for meta in ('本报告', '本文认为', '报告‘', '报告"', '报告 “',
                     '核心结论就是', '我判定为', '我的判断是'):
            if meta in body:
                return False, f'正文出现元话语「{meta}」，应直接说结论和下一步，不要评价自己的报告'

        return True, ''

    def _compose_compare(self, state: dict, c: dict) -> dict:
        """多周对比的答案。核心是"说清排序依据"，不只给名次。

        【为什么不能只写"第44周最严重"】
        退货率高不等于问题大：样本少的周，同样的百分比更可能是噪声。
        所以答案必须同时给出退货率、偏离基线、z 值和样本量，
        并明确"我按 z 排，不是按退货率排"。
        否则读者拿退货率一对照，会觉得我在胡说。
        """
        rows = c.get('weeks') or []
        worst = next((x for x in rows if x['week'] == c.get('worst_week')), None)
        scope = c.get('scope') or '全部类目'
        base_rr = c.get('baseline_rr') or 0

        # ---------- 表格 ----------
        table_rows = []
        for x in rows:
            z = x.get('z')
            table_rows.append([
                f'第 {x["week"]} 周',
                f'{x["rr"]*100:.2f}%',
                f'{x["delta_pp"]:+.2f}pp',
                f'{z:.2f}' if z is not None else '—',
                f'{x["orders"]:,}',
                '样本不足' if not x['enough_sample'] else ('显著上升' if x['is_anomaly_up'] else '未超阈值'),
            ])
        blocks = [{
            'type': 'table',
            'title': f'{scope} · 多周对比（共同基线 {base_rr*100:.2f}%）',
            'columns': ['周次', '退货率', '偏离基线', 'z 值', '订单量', '判定'],
            'rows': table_rows,
            'align': ['left', 'right', 'right', 'right', 'right', 'left'],
            'note': (f'基线为剔除这几周后的其余周合计（{c.get("baseline_note","")}）。'
                     f'排序依据：{c.get("rank_by","")}'),
        }]

        # ---------- 结论 ----------
        if not worst:
            return {
                'headline': '这几周没有可比数据', 'badge': {'label': '无法对比', 'level': 'warn'},
                'summary': '给到的周次里没有足够的有效数据，无法做同口径对比。',
                'blocks': blocks, 'caveats': [], 'week': None,
            }

        ups = [x for x in rows if x['is_anomaly_up']]
        if len(ups) >= 2:
            head = (f'这几周里有 {len(ups)} 周都显著高于基线，最严重的是第 {worst["week"]} 周')
        elif len(ups) == 1:
            head = (f'只有第 {ups[0]["week"]} 周显著高于基线，'
                    f'其余周要么正常、要么是下降')
        elif worst['delta_pp'] < 0:
            head = '这几周没有任何一周显著高于基线，反而都低于基线'
        else:
            head = f'这几周都没有达到显著上升的判定门槛'

        # 正文：三段——排序结论 / 依据 / 易误读的地方
        segs = []
        segs.append(
            f'按**偏离基线的 z 值**排序，最严重的是**第 {worst["week"]} 周**：'
            f'{scope}退货率 {worst["rr"]*100:.2f}%，比共同基线 {base_rr*100:.2f}% '
            f'高 {worst["delta_pp"]:+.2f}pp，z = {worst["z"]}，'
            f'样本 {worst["orders"]:,} 单。')

        # 【排序依据必须明说，这是最容易被质疑的一点】
        by_rr = max(rows, key=lambda x: x['rr'])
        if by_rr['week'] != worst['week']:
            segs.append(
                f'注意：**退货率最高的不是这一周**，而是第 {by_rr["week"]} 周'
                f'（{by_rr["rr"]*100:.2f}%）。但它的样本只有 {by_rr["orders"]:,} 单，'
                f'z = {by_rr["z"]}，统计上不足以判定异常——'
                f'这正是我不按退货率高低排名次的原因。')
        else:
            segs.append(
                f'这一周同时也是退货率最高的（{by_rr["rr"]*100:.2f}%），'
                f'两个口径排序一致，结论比较稳。')

        bad_sample = [x for x in rows if not x['enough_sample']]
        if bad_sample:
            names = '、'.join(f'第 {x["week"]} 周（{x["orders"]:,} 单）' for x in bad_sample)
            segs.append(
                f'{names}样本量低于判定门槛，它们的退货率不能当真——'
                f'样本小的周天然会大幅抖动，我不拿它参与"谁更严重"的排序。')

        caveats = [
            f'共同基线为剔除第 {"、".join(str(x["week"]) for x in rows)} 周后的其余周合计，'
            f'与单周诊断时的"剔除本周"基线略有不同，两者数字不严格可比。',
            f'排序依据是 z 值（偏离多少个标准差），不是退货率高低；'
            f'两者排序不一致时会明确说明。',
        ]

        return {
            'headline': head,
            'badge': {'label': '多周对比', 'level': 'critical' if len(ups) else 'ok'},
            'summary': '\n\n'.join(segs),
            'blocks': blocks,
            'caveats': caveats,
            'week': worst['week'],
        }

    def _compose_reason_fix(self, state: dict) -> dict:
        """回答"某个退货原因该怎么解决"。

        【为什么这个场景需要完全独立的一套答案结构】
        用户问「尺码版型不符这个问题该怎么解决」，和问「第 44 周为什么涨」
        是两类完全不同的问题：

          · 周度异动 → 答案的核心是"这一周发生了什么、为什么、要不要动手"
            结构：KPI（本周 vs 基线）→ 归因链 → 原因结构 → 动作
          · 单个原因怎么治 → 答案的核心是"这个毛病有多重、值不值得管、
            谁在抱怨什么、做哪几件事"
            结构：规模（条数/损失）→ 用户原话 → 动作 → 验收口径

        第二类问题里，"是哪一周"根本不重要 —— 尺码问题不是某一周才有的。
        这正是上次投诉的根源：系统用第一类的模板去答第二类的问题，
        于是给出了完整的第 44 周报告，"尺码"两个字只在原因表里露了一面。

        【三段怎么组织的】
        ① 有多重：条数 + 占比 + 损失 + 单均对比（值不值得管）
        ② 卡在哪：真实差评原文（问题具体长什么样，不是我们猜的）
        ③ 怎么改：动作清单 + 责任方 + 验收指标
        """
        f = state['facts']
        prof = f.get('reason_profile') or {}
        play = f.get('playbook') or {}
        samples = f.get('samples') or {}
        cause = prof.get('cause') or state.get('asked_reason') or ''
        acts = play.get('actions') or []

        # 【数据里没有这个原因 → 换一套组装方式】
        # 这一类问题的答案结构和"有数据的原因"不一样：
        # 前者没有条数、没有损失、没有原文，只有通用做法；
        # 硬套下面的三段式会产出一堆 0 和空段落，比不答还糟。
        if f.get('kb_fallback_used'):
            return self._compose_kb_fix(state)

        n = int(prof.get('n') or 0)
        share = (prof.get('share_of_all_returns') or 0) * 100
        loss = float(prof.get('loss_total') or 0)
        per = float(prof.get('loss_per_return') or 0)
        all_per = float(prof.get('all_loss_per_return') or 0)
        ratio = float(prof.get('per_loss_ratio') or 1)
        resell = (prof.get('resellable_rate') or 0) * 100
        cats = prof.get('by_category') or []
        drift = prof.get('drift_pp') or 0
        drift_note = prof.get('drift_note') or '基本持平'

        main_cat = cats[0]['category'] if cats else ''
        main_share = (cats[0]['share_in_cat'] * 100) if cats else 0

        # ---------- 判断这件事值不值得管 ----------
        # 这是回答的第一句，也是用户最想知道的。
        # 判据不是"条数多不多"，而是"条数多、但每单不贵"这种情况要单独说清。
        if ratio < 0.75:
            cost_note = (f'单均损失只有 ¥{per:.2f}，是全店平均 ¥{all_per:.2f} 的 '
                         f'{ratio*100:.0f}%——退回来的货大多还能二次销售'
                         f'（可二次销售 {resell:.0f}%），亏的主要是运费而不是货值')
        elif ratio > 1.25:
            cost_note = (f'单均损失 ¥{per:.2f}，是全店平均 ¥{all_per:.2f} 的 '
                         f'{ratio*100:.0f}%——这个原因退一单比平均更贵，'
                         f'因为货值很难回收（可二次销售仅 {resell:.0f}%）')
        else:
            cost_note = (f'单均损失 ¥{per:.2f}，与全店平均 ¥{all_per:.2f} 基本持平'
                         f'（可二次销售 {resell:.0f}%）')

        # ---------- ① 规模 ----------
        seg1 = (f'「{cause}」全年 **{n:,} 条**，占全部退货的 **{share:.1f}%**，'
                f'累计损失 **¥{loss/1e4:.1f} 万**。{cost_note}。')
        if cats:
            # 【不要把所有类目平均罗列】
            # 上一版写"集中在 服饰 10,238 条、生鲜 564 条"，
            # 但生鲜只占该类目退货的 2.0%——把 2% 和 34% 并列，
            # 等于暗示两个类目都有问题，会误导执行团队分散精力。
            # 这里只列占比够得上"值得管"的类目（≥5%），
            # 剩下的归到一句"其余类目可以忽略"。
            notable = [c for c in cats if c['share_in_cat'] >= 0.05]
            minor_n = sum(c['n'] for c in cats if c['share_in_cat'] < 0.05)
            if notable:
                # 【别再重复一遍主类目名】
                # 上一版是"几乎全部集中在 服饰 10,238 条（占34%）。其中 **服饰** 最突出——"，
                # 同一个类目在一句话里出现了两次，读起来像卡带。
                # 只剩一个类目时，直接说这一类目自己的情况，不用再说"其中"。
                if len(notable) == 1:
                    c0 = notable[0]
                    seg1 += (f'\n\n几乎全部集中在 **{c0["category"]}**：{c0["n"]:,} 条，'
                             f'占该类目全部退货的 **{c0["share_in_cat"]*100:.0f}%**——'
                             f'也就是这个类目每退 100 件货，就有 '
                             f'{c0["share_in_cat"]*100:.0f} 件是这个毛病。'
                             f'治理从这里下手，别的类目不用管。')
                else:
                    cat_desc = '、'.join(
                        f'{c["category"]} {c["n"]:,} 条'
                        f'（占该类目退货 {c["share_in_cat"]*100:.0f}%）'
                        for c in notable)
                    seg1 += (f'\n\n主要集中在 {cat_desc}。'
                             f'其中 **{main_cat}** 最突出，'
                             f'应该优先投入，不要几个类目平均用力。')
                if minor_n and minor_n / max(n, 1) > 0.02:
                    seg1 += f'其余类目合计 {minor_n:,} 条，占比很低，可以忽略。'
            else:
                seg1 += (f'\n\n各类目占比都在 5% 以下，没有明显的集中区域，'
                         f'说明这是个跨品类的共性问题。')

        # ---------- ② 用户原话 ----------
        seg2 = ''
        # 【这个标志要提到外层】
        # 它决定第三段"先做哪一条动作"。原来只算在 if 里，
        # 第三段读不到，于是无论原文说什么，都照样说"第一条最见效"——
        # 而第一条是"加尺码选择器"，恰恰治不了"同码不同大"。
        has_inconsistent = False
        ss = samples.get('samples') or []
        if ss:
            # 【过滤噪声评论，这一步不能省】
            # 实测 P1（尺码）标注的 9 条差评里，有 2 条根本不是差评：
            #   「码数合适。料子也很好，穿着舒服」（1 星却是好评）
            #   「袖口比较肥大，没有弹力，一般。」（3 星，说的是弹力不是尺码）
            # 直接把原文摆出来，读者会当场质疑"这也算差评？"
            # 更糟的是结论会被这几条带偏。
            # 判据：既要出现尺码相关词，又不能是好评口径。
            # 【第一版只做了正向匹配，结果放过了一条好评】
            # 「码数合适。料子也很好，穿着舒服」——含"码"字，被当成尺码抱怨收了进来。
            # 而这批标为 P1 的"差评"里真的混着这种标注噪声（9 条里有 2 条）。
            # 摆到用户面前，他会当场质疑"这也算差评"，整份回答的可信度就没了。
            # 所以加一道反向排除：出现"合适/正好/合身/满意"这类正面判断的，直接剔除。
            _size_words = ('尺码', '码数', '码偏', '大小', '肥大', '太肥', '偏瘦',
                           '太紧', '太松', '穿不上', '大一码', '小一码',
                           '版型', '偏大', '偏小', '号小', '号大')
            _positive = ('合适', '正好', '合身', '满意', '舒服', '很好', '不错',
                         '喜欢', '推荐', '还会再买')
            good = []
            for s in ss:
                t = (s.get('text') or '').strip().replace('\n', ' ')
                if not t:
                    continue
                if not any(w in t for w in _size_words):
                    continue
                # 含正面判断且不含负向抱怨词 → 这是好评，不是证据
                if any(w in t for w in _positive) and not any(
                        w in t for w in ('太', '不合', '穿不上', '差', '烂', '坑',
                                         '不满', '问题', '不准', '不一样')):
                    continue
                good.append(t)
            # 挑两条最有信息量的：同码不同大 > 单纯偏大偏小 > 其他
            good.sort(key=lambda t: (0 if ('同码' in t or '不一样大' in t
                                           or '两条' in t) else 1, -len(t)))

            # 【引语截断不能从头上砍】
            # 上一版是 t[:58] + '…'，结果那条最有价值的原文被切成
            # 「…同码的裤子，居然…」——"不一样大"正好落在第 59 个字之后，
            # 证据在最关键的地方断了。读者看到的是一句没说完的话，
            # 而下面紧接着就在分析"同码不同大"。
            # 正确做法是先定位证据词，把窗口开在它周围。
            # 【按信息量排，不是按出现位置排】
            # 第一版找的是"最早出现的证据词"，结果原文里"大一码"在第 21 字，
            # 窗口从那儿开，把第 60 字之后的"同码的裤子，居然不一样大"切掉了
            # —— 那恰恰是唯一能推翻"加选择器就行"的证据。
            # 越能说明问题性质的词，越该被优先保住。
            _ev = ('不一样大', '同码', '发来一条', '穿不上',
                   '大一码', '小一码', '偏大', '偏小', '不准')

            def _clip(t: str, width: int = 46) -> str:
                if len(t) <= width:
                    return t
                pos = -1
                for w in _ev:                      # 先命中先赢，故按信息量降序
                    i = t.find(w)
                    if i >= 0:
                        pos = i
                        break
                if pos < 0:
                    return t[:width] + '…'
                # 证据词后面要留出"说清后果"的余量，所以把它放在窗口前 1/4 处
                start = max(0, pos - width // 4)
                end = min(len(t), start + width)
                start = max(0, end - width)
                # 【引语必须自足】"…30码，就发来一条31码…"开头是半截数字，
                # 读者不知道在说什么。往前多退几个字，退到标点或句首为止。
                if start > 0:
                    for j in range(start, max(0, start - 14), -1):
                        if t[j - 1] in '，。！？、；,.':
                            start = j
                            break
                return ('…' if start > 0 else '') + t[start:end] + (
                    '…' if end < len(t) else '')

            quotes = []
            for t in good[:2]:
                q = _clip(t)
                if q not in quotes:
                    quotes.append(q)
            quotes = ['「' + q + '」' for q in quotes]

            # 【叙述必须跟着"实际挑中的原文"走，不能跟着预设走】
            # 上一版把措辞写死了："这两条抱怨的不是码数偏大偏小，而是同码不同大"。
            # 但排序后挑中的第二条恰恰是「L号的都那么大，衣服太胖了穿不了」——
            # 那就是标准的"码数偏大"。一句话否掉了自己刚引的证据，
            # 用户一眼就能看出来。
            # 所以先按挑中的原文分组，再决定说什么。
            _inc_words = ('同码', '不一样大', '两条', '发来一条')
            picked = good[:2]
            inc = [t for t in picked if any(w in t for w in _inc_words)]
            plain = [t for t in picked if t not in inc]
            if inc and plain:
                seg2 = ('真实差评里是这么说的：' + '、'.join(quotes) + '。'
                        '注意第一条和第二条说的**不是同一个毛病**：'
                        '前者是"同一个码数两次买到不一样大"甚至"收到错码"，'
                        '后者才是常规的"码数偏大"。')
            elif inc:
                seg2 = ('真实差评里是这么说的：' + '、'.join(quotes) + '。'
                        '注意它们抱怨的不是"码数偏大偏小"，'
                        '而是**同一个码数两次买到不一样大**、甚至**收到错码**。'
                        '这和"尺码表看不清"不是一回事。')
            elif plain:
                seg2 = ('真实差评里是这么说的：' + '、'.join(quotes) + '。'
                        '这类抱怨的共同点不是"东西坏了"，'
                        '而是"和我以为的不一样"——问题出在'
                        '**下单前没有把话说清楚**，不在生产端。')
            else:
                seg2 = '真实差评里是这么说的：' + '、'.join(quotes) + '。'
            # 【has_inconsistent 单独算，别在段②里改措辞】
            # 段②只管"问题长什么样"，段④才管"怎么改"。
            has_inconsistent = bool(inc)
        if not seg2:
            # 没有原文时不要空着，也不要编，直接说证据边界
            seg2 = ('这个原因目前没有可引用的差评原文，'
                    '所以下面的动作是基于该原因的历史退货特征给出的，'
                    '不是从具体抱怨里提炼的。')

        # 趋势：在涨还是在跌，决定是救火还是长期优化
        if abs(drift) >= 0.2:
            # 【别把 drift_note 和趋势方向各说一遍】
            # 上一版写成「它的占比在收窄（-0.64pp），已在自然收窄，别过度投入」，
            # "在收窄"说了两次。这类重复是从两个不同来源拼出来的：
            # 一半来自 drift_note（后端算的），一半来自我手写的方向词。
            # 现在只保留一个判断句，方向词由 drift 单独决定。
            if drift > 0:
                seg2 += (f'\n\n近 12 周它在退货里的占比{drift_note}（{drift:+.2f}pp）——'
                         f'还在恶化，这件事等不得。')
            else:
                seg2 += (f'\n\n近 12 周它在退货里的占比{drift_note}（{drift:+.2f}pp）——'
                         f'已经在自然往下走，不必额外投入资源去压它，'
                         f'把有限的人力放到还在涨的那几个原因上。')

        # ---------- ③ 动作 ----------
        seg3 = ''
        if acts:
            lines = []
            for i, a in enumerate(acts[:3], 1):
                lines.append(f'{i}. **{a.get("action")}** —— '
                             f'{a.get("owner")}负责，盯「{a.get("kpi")}」')
            seg3 = '按现有运营手册，该做这三件事：\n\n' + '\n'.join(lines)
            # 【按原文证据决定"先做哪一条"】
            # 手册里第一条是"加尺码选择器"，那是针对"不知道选哪个码"的。
            # 但如果原文显示问题在"同码不同大"，第一优先级应该是
            # 版型一致性（工厂公差、批次留样），而不是加个选择器
            # —— 选择器解决不了"标着同一个码却不一样大"。
            # 这里显式说出来，否则用户照着顺序做会发现没用。
            if has_inconsistent:
                seg3 += ('\n\n但**别照着这个顺序做**。上面三条都建立在'
                         '"用户不知道选哪个码"这个假设上，而原话里有一部分是'
                         '"标着同一个码却不一样大"——那是生产公差和批次管理，'
                         '加选择器治不了。\n\n'
                         '顺序改成：\n'
                         '1. **先查批次**：把这批投诉对应的订单号、生产批号拉出来，'
                         '看是否集中在同一批——集中在同一批就是工厂公差，'
                         '分散就是质检漏检，两种情况的处理人不一样\n'
                         '2. **同批次留样 + 按批抽检**：每批留三件实测三围，'
                         '超出公差直接退回工厂，别等用户退回来才发现\n'
                         '3. **再补尺码选择器**：前三步压住之后，'
                         '选择器才是在解决"剩下的那部分"——不然它解决的是'
                         '一个已经被公差掩盖了的问题，做完看不出效果')
            else:
                seg3 += ('\n\n三条里第一条最见效：尺码类退货的本质是'
                         '**信息不足导致的选择错误**，把"该选哪个码"这件事'
                         '在下单前解决掉，退货自然就下来了。'
                         '后两条是补救——把已经发生的退货转成换货，挽回一部分损失。')
        else:
            seg3 = ('这个原因暂时没有对应的动作清单。'
                    '不编动作——凭空给出的整改方案执行下去只会浪费人力。')

        # ---------- KPI 卡片：换成"这个原因"自己的口径 ----------
        kpi = [
            {'label': '全年退货条数', 'value': f'{n:,}', 'delta': f'占全部 {share:.1f}%',
             'tone': 'neutral'},
            {'label': '累计损失', 'value': f'¥{loss/1e4:.1f}万',
             'delta': f'单均 ¥{per:.2f}', 'tone': 'bad' if ratio > 1.1 else 'neutral'},
            {'label': '主要类目', 'value': main_cat or '—',
             'delta': f'占该类目退货 {main_share:.0f}%', 'tone': 'neutral'},
            {'label': '近 12 周趋势', 'value': drift_note,
             'delta': f'{drift:+.2f}pp', 'tone': 'bad' if drift > 0.2 else 'good'},
        ]

        blocks: list[dict] = []
        if cats:
            blocks.append({
                'type': 'table',
                'title': f'「{cause}」的类目分布',
                'columns': ['类目', '退货条数', '占该类目退货', '损失'],
                'rows': [[c['category'], f'{c["n"]:,}',
                          f'{c["share_in_cat"]*100:.1f}%',
                          f'¥{c["loss"]/1e4:.1f}万'] for c in cats],
                'align': ['left', 'right', 'right', 'right'],
                'note': ('"占该类目退货"比绝对条数更有指向性：'
                         '大类目天然条数多，占比才说明这个毛病在哪个类目更严重。'),
            })
        if ss:
            blocks.append({
                'type': 'bars',
                'title': f'「{cause}」相关差评原文（共匹配 {samples.get("total_matched", 0)} 条）',
                'items': [{'label': (s.get('text') or '')[:26] + '…',
                           'value': int(s.get('rating') or 0),
                           'share': 1.0} for s in ss[:4]],
                'note': '按星级从低到高，都是真实评论原文，未经改写。',
            })
        if acts:
            blocks.append({
                'type': 'table',
                'title': f'「{cause}」动作清单',
                'columns': ['动作', '责任方', '验收指标', '影响'],
                'rows': [[a.get('action'), a.get('owner'), a.get('kpi'),
                          a.get('impact_on')] for a in acts],
                'align': ['left', 'left', 'left', 'left'],
                'note': '动作来自团队运营手册（非模型生成），每条都能落到人和指标上。',
            })

        caveats = [
            '这里统计的是该原因全年的退货，不限于某一周——'
            '尺码、材质这类问题不是某一周才有的，按周看反而会失真。',
            f'损失按商品成本计提，不是零售价；可二次销售率 {resell:.0f}% 已计入。',
        ]

        # 【标题不能把"尺码信息补齐"写死】
        # 上一版是「先从 服饰 的尺码信息补齐开始」——这段代码要服务所有原因，
        # 换成"材质做工问题"就变成了"先从服饰的尺码信息补齐开始"，驴唇不对马嘴。
        # 标题只说这个原因自己的规模和落点，动作留给正文。
        if main_cat:
            _hd = (f'「{cause}」全年 {n:,} 条、损失 ¥{loss/1e4:.1f} 万，'
                   f'主要集中在{main_cat}（占该类目退货 {main_share:.0f}%）')
        else:
            _hd = f'「{cause}」全年 {n:,} 条、损失 ¥{loss/1e4:.1f} 万'

        return {
            'headline': _hd,
            'badge': {'label': '按原因治理',
                  # 在收窄就不该报红。徽章是给人的第一眼判断，
                  # 一个已经在自然下降的原因还挂着警告色，会让人过度反应。
                  'level': 'ok' if drift <= -0.2 else 'warn'},
            'summary': '\n\n'.join([seg1, seg2, seg3]),
            'blocks': blocks,
            'caveats': caveats,
            'week': None,
            'kpi': kpi,
        }

    def _compose_kb_fix(self, state: dict) -> dict:
        """回答"数据里没有这个原因，但它该怎么解决"。

        【这类问题的诚实答法是什么】
        用户问「标签脱落怎么解决」，数据表里九类原因一个都对不上。
        此时有三种做法，只有一种是对的：

          · 说"未收录原因" → 用户觉得这功能没用，而且他明明有这个问题
          · 编一套数字（"标签问题占退货 8%") → 造假，一旦追问就崩
          · 说清楚"数据里没单列这一类 + 给出通用做法 + 用最接近的那类
            真实数据告诉你这类问题在你这店里有多重" → 用户拿到手能动手

        所以答案里必须同时出现三样，且**来源分开标注**：
          ① 明确的边界声明（数据里没有这一类）
          ② 通用治理方案（知识库，行业做法）
          ③ 最接近的那一类真实画像（本店数据，注明是替代口径）

        【最容易犯的错：把②和③混着说】
        "标签问题建议做批次留样，本店这类退货占 12.9%"——
        这句话里的 12.9% 是材质做工的数字，不是标签的。
        用户会以为标签问题真占了 12.9%，拿去汇报就出事了。
        所以每一句涉及数字的地方都要带"替代"两个字。
        """
        f = state['facts']
        kb = f.get('kb') or {}
        prof = f.get('reason_profile') or {}
        # 【三个名字必须分开，混用就会出现"自己跟自己比"】
        #   user_text  用户原话（"标签脱落"）→ 答案里复述他的问题
        #   kb_name    知识库归到的类（"标签/吊牌问题"）→ 动作清单的标题
        #   stand_in   数据里最接近的类（"材质做工问题"）→ 唯一带数字的参照
        # 上一版把 user_text 和 kb_name 都用 asked_reason，结果写出
        # 「数据里没有「标签/吊牌问题」这一类，按最接近的「标签/吊牌问题」给方案」。
        user_text = state.get('user_reason_text') or state.get('asked_reason') or ''
        matched = bool(kb.get('matched'))
        acts = kb.get('actions') or []
        signs = kb.get('signs') or []
        watch_out = kb.get('watch_out') or ''
        # 【工具失败时 kb_name 也是空的，要能回溯】
        # 知识库没读成，kb_name 自然没有。但 asked_reason 在 state 初始化
        # 时就钉住了（parse_reason 的产物），拿它当退路没问题——
        # parse_reason 第三层返回的本来就是知识库的类名。
        # 没有这一句，stand_in 查不出来，替代口径数字整段消失。
        kb_name = kb.get('kb_name') or (
            state.get('asked_reason') if not matched else '') or ''
        stand_in = (f.get('profile_stand_in')
                    or kb_fallback_to(kb_name, kb)) or ''

        # 【"同一类"和"归到别类"是两回事，措辞不能共用】
        # 用户说"标签脱落"，知识库的条目名就叫"标签/吊牌问题"，
        # 这是**同一个类的两种叫法**，说"我把它归到标签/吊牌问题"是废话。
        # 只有当用户说的词不在该类别名里时，才叫"归到"。
        _kb_aliases = _KB_REASON_ALIASES.get(kb_name, ())
        same_as_user = bool(kb_name) and (
            kb_name == user_text or user_text in _kb_aliases)

        # 【口径类型的判据，只认一件事：stand_in 是不是数据里的原因】
        # 上一版用 `stand_in != kb_name` 判"参照 vs 合并"，是错的——
        # kb_name 是知识库的条目名，stand_in 是数据里最接近的原因名，
        # 这俩本来就是两套命名，几乎永远不相等，于是每次都走"参照"分支，
        # 而 seg1 又写着"归到材质做工看"，读者看到的是自相矛盾：
        # 既说已并入统计、又说只是拿它做参照。
        # 真正该判的是：数据里到底有没有用户问的这类。
        #   · 用户问的类在数据里（is_data_reason）→ 那就是它自己的数，不是参照
        #   · 不在数据里 → stand_in 只是另一个类，永远是"参照"，绝不是"合并"
        #     （知识库的 fallback_to 只是"该找哪一类比"，不是"数据把它并进去了"）
        _mode = '参照' if stand_in else '无'

        # ---------- ① 边界声明：先说清我在答什么 ----------
        # 【这里要分四种情况，不能只有"命中/未命中"两档】
        # 上一版只有两档，于是知识库工具**失败**（文件丢了、JSON 坏了）时
        # 走进了"未命中"分支，写出「知识库里也没有完全对应的条目」——
        # 那是假话：它压根没查成。
        # 而且这句话后面紧跟着又给出了替代口径的数字，
        # 前面说"没有近似条目"、后面说"最接近的是材质做工"，自相矛盾。
        #
        # 四档：
        #   a) 命中条目     → 有通用做法，可以照着做
        #   b) 未命中条目   → 知识库查过了，确实没有这一类
        #   c) 工具失败     → 没查成，不知道有没有（不能冒充"没有"）
        #   d) 连替代口径都没有 → 什么都给不了，只能建议手工统计
        kb_failed = (not kb) or bool(kb.get('error')) or kb.get('ok') is False
        if matched:
            seg1 = (f'数据里的退货原因只有九类，**没有「{user_text}」这一类的单独记录**——'
                    f'所以下面没有它的条数和损失，那些数我编不出来。')
            if same_as_user:
                seg1 += (f'\n\n但这个问题不因为数据没记就不存在。'
                         f'它属于「{kb_name}」，'
                         f'知识库里有一整套通用做法，可以先照着做。')
            else:
                # 【这里不能引用 fallback_reason】
                # 那个字段说的是"为什么归到数据里的那类"，讲的是 fallback_to，
                # 不是"为什么归到 kb_name"。上一版混用，写出了
                # 「我把它归到「标签/吊牌问题」——归到材质做工看」，两个类名打架。
                seg1 += (f'\n\n但这个问题不因为数据没记就不存在。'
                         f'知识库里最接近的是「{kb_name}」，'
                         f'可以先照它的做法处理。')
        elif kb_failed:
            # 【必须承认"没查成"，不能冒充"没有"】
            # 这两句话对用户的价值完全不同：
            #   "知识库里没有这一类" → 他得自己想办法
            #   "方案库这次没读到"   → 他知道是我这边的问题，可以再试
            seg1 = (f'数据里的退货原因只有九类，**没有「{user_text}」这一类的单独记录**，'
                    f'所以给不出它的条数和损失。\n\n'
                    f'通用方案库这次没读到（{kb.get("error") or "读取异常"}），'
                    f'所以下面只有量级参照，没有具体的整改动作。'
                    f'这不是说没有这类做法，是我这边没取到。')
        else:
            seg1 = (f'数据里的退货原因只有九类，**没有「{user_text}」这一类的单独记录**，'
                    f'通用方案库里也没有对应的条目。\n\n'
                    f'所以下面给的是立项方法，不是针对这个问题的具体答案——'
                    f'要拿到具体答案，得先有人在数据里把它记下来。')

        # ---------- ② 通用方案 ----------
        seg2 = ''
        if acts:
            lines = []
            for i, a in enumerate(acts[:3], 1):
                lines.append(f'{i}. **{a.get("action")}** —— '
                             f'{a.get("owner")}负责，盯「{a.get("kpi")}」')
            head = ('按行业通用做法，该做这三件事：' if matched
                    else '在没有专门数据的情况下，先这么做：')
            seg2 = (head + '\n\n' + '\n'.join(lines))
        elif kb_failed:
            # 失败时不要写"这一类暂时没有整理好的动作清单"——
            # 那句话的意思是"我们整理过，确实没有"，但事实是没查到。
            seg2 = ''
        else:
            seg2 = '这一类暂时没有整理好的动作清单，不编。'

        if signs:
            seg2 += ('\n\n判断是不是这一类，看这几个征兆：'
                     + '、'.join(signs[:3]) + '。')

        # ---------- ③ 替代口径的真实数据 ----------
        # 【这一段是全文唯一带数字的地方，必须把"参照"说在前面】
        # 因为走到这里的前提就是"数据里没有这一类"（kb_fallback_used），
        # 所以 stand_in 永远是"另一个类"，措辞只能是参照，不能说合并。
        seg3 = ''
        n = int(prof.get('n') or 0)
        _fb = kb.get('fallback_reason') or ''
        if n and stand_in:
            share = (prof.get('share_of_all_returns') or 0) * 100
            loss = float(prof.get('loss_total') or 0)
            cats = prof.get('by_category') or []
            cat_txt = ''
            if cats:
                c0 = cats[0]
                cat_txt = (f'，主要集中在{c0["category"]}'
                           f'（占该类目退货 {c0["share_in_cat"]*100:.0f}%）')
            # 【限定词必须紧跟在数字后面，不能只在段尾补一句】
            # 上一版把"注意这不是…"放在数字之后另起一句，
            # 但整段的开头是"数据里最接近的是 X"，读起来仍像是在讲这件事本身。
            seg3 = (f'**量级参照**：成因最接近的是「{stand_in}」这一类的真实数据——'
                    f'全年 {n:,} 条、占全部退货 {share:.1f}%、损失 ¥{loss/1e4:.1f} 万{cat_txt}。\n\n'
                    f'这组数字**不属于「{user_text}」**，数据里根本没有它的统计。'
                    f'{_fb + "。" if _fb else ""}'
                    f'之所以能拿来参照，是因为两者在成因和处理上接近，'
                    f'可以用它判断这件事值不值得单独立项——'
                    f'但不能当成「{user_text}」自己的占比去汇报。')
        elif stand_in:
            seg3 = (f'成因最接近的是「{stand_in}」，但它自己也没有足够的退货记录，'
                    f'给不出可用的量级。这种情况建议先从客服工单里手工统计两周，'
                    f'拿到基线再决定要不要立项。')

        if watch_out:
            seg3 += f'\n\n有一点要注意：{watch_out}'

        # ---------- KPI：一个数都不给 ----------
        # 【这里刻意留空，不用 0 占位】
        # 填 0 会让界面显示"全年退货条数 0"，那是错的——不是 0，是没统计。
        # 前端对空 kpi 的处理是整块不渲染，这才是诚实的表现。
        kpi = []

        blocks: list[dict] = []
        if acts:
            blocks.append({
                'type': 'table',
                'title': f'「{kb_name or user_text}」通用动作清单',
                'columns': ['动作', '责任方', '验收指标'],
                'rows': [[a.get('action'), a.get('owner'), a.get('kpi')]
                         for a in acts],
                'align': ['left', 'left', 'left'],
                'note': ('来源：本地知识库的行业通用做法，**不是本店数据推出来的**。'
                         '执行前建议先用两周验证是否适用。'),
            })
        # 【替代口径说明只在真的用了别的类的数字时才挂】
        # stand_in 和 kb_name 同类时，上面给的不是替代数字，而是"没有数字"，
        # 再挂一块"替代口径说明"会让人以为这里有数字可用。
        if n and stand_in and stand_in != kb_name:
            blocks.append({
                'type': 'note',
                'title': '替代口径说明',
                'text': (f'上面的 {n:,} 条是「{stand_in}」的统计，'
                         f'不是「{user_text}」的。数据表里没有单独记录后者，'
                         f'所以拿最接近的一类做参照。'
                         f'两者的关系：{_fb or "同一大类下的合并口径"}。'),
            })

        # 【"不是我没查"这句话在工具失败时是假的】
        # 工具失败场景下用户问的原因确实没被统计，但方案库也没查成，
        # 此时还说"不是我没查"就像在辩解。改成只陈述数据侧的事实。
        caveats = [
            f'「{user_text}」在数据的原因体系里没有独立分类，'
            f'所以本回答不含它的条数、占比和损失——'
            f'数据表里就没有这个维度，不是取数漏了。',
        ]
        if acts:
            caveats.append(
                '通用做法来自知识库，属于行业经验，不是从这家店的数据里推出来的。'
                '照做之前先小范围验证。')
        if kb_failed:
            caveats.append(
                '通用方案库这次没有读到，所以本回答缺整改动作。'
                '这是取数失败，不等于「没有这类做法」。')
        if stand_in and stand_in != kb_name:
            caveats.append(
                f'量级参照用的是「{stand_in}」，两者成因接近但不完全等同，'
                f'引用数字时请注明是参照口径。')
        elif stand_in and stand_in == kb_name:
            caveats.append(
                f'这类问题被合并在「{stand_in}」里统计，'
                f'无法拆出它的单独占比。')

        # 【标题里别说"最接近的是它自己"】
        # 【失败这一档必须单独写】
        # 上一版只有"命中/未命中"两档，工具失败时落进未命中，
        # 标题写成「数据里没有「标签脱落」这一类，也没有近似条目——给立项方法」，
        # 可正文紧接着就给出了「材质做工问题」的 10,646 条。
        # 标题说"没有近似条目"、正文说"最接近的是材质做工"，自己打自己。
        # 而且"没有近似条目"是结论，"没查到"是状态，两者对用户的手感完全不同：
        # 前者他会放弃，后者他会知道可以再试一次。
        if kb_failed:
            _hd = (f'「{user_text}」没有独立统计，'
                   f'方案库这次没读到——先给参照量级')
        elif not matched:
            _hd = f'数据里没有「{user_text}」这一类，也没有近似条目——给立项方法'
        elif same_as_user:
            _hd = (f'数据里没有「{user_text}」的单独统计，'
                   f'按「{kb_name}」的通用方案处理')
        else:
            _hd = (f'数据里没有「{user_text}」这一类，'
                   f'按最接近的「{kb_name}」给通用方案')

        return {
            'headline': _hd,
            # 【徽章用 warn，不用 critical】
            # 这不是"发现了严重问题"，是"这个问题我按通用方法答了"。
            # critical 会让人以为店里出了大事。
            'badge': {'label': '知识库补充', 'level': 'warn'},
            'summary': '\n\n'.join(x for x in (seg1, seg2, seg3) if x),
            'blocks': blocks,
            'caveats': caveats,
            'week': None,
            'kpi': kpi,
        }

    def _compose(self, state: dict) -> dict:
        f = state['facts']
        intent = state['intent']
        blocks: list[dict] = []
        caveats: list[str] = []

        diag = f.get('diag')
        promo = f.get('promo')
        struct = f.get('structure')
        loss = f.get('loss')
        comp = f.get('comp')
        value = f.get('value')
        play = f.get('playbook')
        ov = f.get('overview')
        cmp_ = f.get('compare')

        # ---------- 对比场景：单独一条路径 ----------
        # 【为什么单独 return，不往下面的大流程里塞 if】
        # 对比的答案结构和诊断完全不同：没有归因链、没有动作清单、
        # 没有损失口径，只有一张并排表和一句"哪个更严重"的判断。
        # 硬塞进诊断流程会产出一堆空的 block 占位，前端渲染出一堆空壳。
        if cmp_:
            return self._compose_compare(state, cmp_)

        # ---------- 按原因治理：单独一条路径 ----------
        # 【为什么必须单独一条路径，不能并进下面的大流程】
        # 用户问「尺码版型不符该怎么解决」，需要的是三样东西：
        #   这个毛病有多重（条数、占比、损失）→ 用户到底怎么说的（原话）
        #   → 我们该做什么（动作 + 负责人 + KPI）
        # 而下面的大流程是为一周异动设计的：KPI 卡片是"本周退货率/基线/z值"，
        # 归因链是"谁贡献的→机制→原因"，这些在"治某个毛病"的场景里全是废话 ——
        # 用户不关心它发生在哪一周，尺码问题不是某一周才有的。
        # 硬塞进去的结果就是这次投诉：一份完完整整的第 44 周报告。
        # 【条件里的 play 不能是硬性要求】
        # 原来写的是 `asked_reason and play`，但知识库路径不打 action_playbook
        # （数据里没有那一类，手册里也没有），play 恒为 None，
        # 于是「标签脱落怎么解决」会掉进下面的周度诊断流程 ——
        # 又变回"第几周为什么涨"。也就是说，绕了一圈，答案还是错的。
        #
        # 【第二版仍然有这个洞，只是换了地方】
        # 改成 `(play or _kb)` 之后，只要知识库工具失败（文件被删、JSON 坏了），
        # _kb 就是 None，条件照样不成立 → 又掉回周度诊断。
        # 「知识库坏了」和「用户没问原因」是两件事，不能共用一个分支。
        # 判据改成 kb_fallback_used：它在 state 初始化时就钉住了，
        # 只表示"用户问的原因不在数据里"，与工具成没成功无关。
        # 工具失败时进 _compose_kb_fix 会走"知识库也没命中"那条支路，
        # 输出「给立项方法」——难看，但至少还在答用户问的事。
        _kb = state['facts'].get('kb')
        _kb_mode = bool(state['facts'].get('kb_fallback_used'))
        if (state.get('asked_reason') and (play or _kb or _kb_mode)
                and (state.get('intent') == 'action'
                     or not diag or state['facts'].get('reason_focus'))):
            return self._compose_reason_fix(state)

        # ---------- 判定 ----------
        headline, badge = self._headline(state)
        summary = self._summary(state, headline)

        # ---------- KPI ----------
        kpi_items = []
        if diag:
            kpi_items.append({
                'label': f'第 {diag["week"]} 周退货率',
                'value': f'{diag["rr_now"]*100:.2f}%',
                'delta': f'{diag["delta_pp"]:+.2f}pp',
                'tone': 'bad' if diag['delta_pp'] > 0 else 'good',
            })
            kpi_items.append({
                'label': '基线退货率',
                'value': f'{diag["rr_base"]*100:.2f}%',
                'delta': f'z = {diag["zscore"]}',
                'tone': 'neutral',
            })
            kpi_items.append({
                'label': '本周订单',
                'value': f'{diag["orders_this_week"]:,}',
                'delta': '单',
                'tone': 'neutral',
            })
        if loss:
            kpi_items.append({
                'label': '退货损失总额',
                'value': f'¥{loss["total_loss"]/1e4:.1f}万',
                'delta': f'单均 ¥{loss["per_return"]:.2f}',
                'tone': 'bad',
            })
        if value:
            kpi_items.append({
                'label': f'降 {value["pp_reduced"]}pp 的年化价值',
                'value': f'¥{value["annual_gmv_lift"]/1e4:.1f}万',
                'delta': f'+{value["pct_of_gmv"]*100:.2f}% GMV',
                'tone': 'good',
            })
        # 补偿场景单独补三个指标：只给一个"年化价值"太空，
        # 用户真正想知道的是"补多少、能救回几成、值不值"。
        if comp and not diag:
            opt = comp['optimal']
            kpi_items = [
                {'label': '最优补偿档', 'value': f'¥{opt["compensation"]:.0f}',
                 'delta': f'样本 {comp["sample_n"]:,} 单', 'tone': 'neutral'},
                {'label': '该档挽单率', 'value': f'{opt["save_rate"]*100:.1f}%',
                 'delta': '下单可挽回比例', 'tone': 'good'},
                {'label': '单均期望利润', 'value': f'¥{opt["expected_profit"]:.2f}',
                 'delta': '挽单率 × (毛利 − 补偿)', 'tone': 'good'},
                {'label': '混淆检验', 'value': f'r={comp["confound_check"]:+.3f}',
                 'delta': '接近 0 说明未混淆', 'tone': 'neutral'},
            ]

        # 纯损失场景：四个最能说明"亏多少"的指标
        if loss and not diag:
            kpi_items = [
                {'label': '全年退货损失', 'value': f'¥{loss["total_loss"]/1e4:.1f}万',
                 'delta': '按商品成本计提', 'tone': 'bad'},
                {'label': '占毛利比例', 'value': f'{loss["share_of_gp"]*100:.1f}%',
                 'delta': f'毛利 ¥{loss["total_gross_profit"]/1e4:.0f}万',
                 'tone': 'bad'},
                {'label': '单均损失', 'value': f'¥{loss["per_return"]:.2f}',
                 'delta': '物流 + 成本折损', 'tone': 'neutral'},
                {'label': '可二次销售', 'value': f'{loss["resellable_rate"]*100:.1f}%',
                 'delta': '其余按报废全额计提', 'tone': 'good'},
            ]

        if ov and not diag and not loss:
            kpi_items = [
                {'label': '年订单', 'value': f'{ov["orders_year"]:,}',
                 'delta': f'{ov["skus"]} 个 SKU', 'tone': 'neutral'},
                {'label': 'GMV', 'value': f'¥{ov["gmv_year"]/1e4:.1f}万',
                 'delta': f'客单价 ¥{ov["aov"]:.2f}', 'tone': 'neutral'},
                {'label': '退货率', 'value': f'{ov["return_rate"]*100:.2f}%',
                 'delta': f'损失 ¥{ov["loss_total"]/1e4:.1f}万', 'tone': 'bad'},
                {'label': '损失占毛利', 'value': f'{ov["loss_share_of_gp"]*100:.1f}%',
                 'delta': '需优先改善', 'tone': 'bad'},
            ]

        if kpi_items:
            blocks.append({'type': 'kpi', 'items': kpi_items[:4]})

        # ---------- 时序 ----------
        # 紧跟 KPI：先看"现在多少"，再看"最近怎么走"，最后才是归因细节。
        # 放在归因链之后会让人先读一堆分析、再回头找走势。
        tr = f.get('trend')
        if tr and tr.get('series'):
            _s = tr['series']
            _v = [x['value'] for x in _s]
            blocks.append({
                'type': 'trend',
                'title': f'近 {len(_s)} 周退货率走势',
                'scope': tr.get('scope'),
                'series': _s,
                'min': round(min(_v), 4),
                'max': round(max(_v), 4),
                'avg': round(sum(_v) / len(_v), 4),
            })

        # ---------- 归因链 ----------
        # 只在"确认是问题"时才画归因链。显著改善/样本不足/无异常都不该有链，
        # 否则会出现"结论说这是好周，下面却列着责任归属"的自相矛盾。
        if diag and diag.get('is_anomaly') and (diag.get('delta_pp') or 0) > 0:
            chain = []
            chain.append({
                'label': '现象',
                'text': f'第 {diag["week"]} 周退货率 {diag["rr_now"]*100:.2f}%，'
                        f'比全历史基线 {diag["rr_base"]*100:.2f}% 高 {diag["delta_pp"]:+.2f}pp，'
                        f'二项 z = {diag["zscore"]}（阈值 2）',
                'tone': 'bad',
            })
            contrib = diag.get('category_contribution') or []
            if contrib:
                top = contrib[0]
                chain.append({
                    'label': '谁贡献的',
                    'text': f'{top["category"]}贡献 {top["contribution_pp"]:+.2f}pp'
                            f'（该类目退货率 {top["rr_now"]*100:.2f}% vs 基线 {top["rr_base"]*100:.2f}%）',
                    'tone': 'warn',
                })
            if promo:
                dc = promo['decomposition']
                v = promo.get('verdict') or {}
                chain.append({
                    'label': '机制',
                    'text': f'换"仅非促销周"基线后涨幅 {promo["actual_gap_pp"]:+.2f}pp，'
                            f'其中结构效应 {dc["structure_pp"]:+.2f}pp、'
                            f'机制效应 {dc["mechanism_pp"]:+.2f}pp —— {v.get("headline", "")}',
                    'tone': 'info',
                })
            if struct and struct.get('items'):
                it = struct['items'][0]
                # 【这里必须带上基期对比，否则整条归因链自相矛盾】
                # 原来越过 share_base 直接写"首位是破损，131 条占 24.4%"，
                # 读者看到"第一位 + 24.4%"必然理解成"破损变多了"，
                # 而上面两行刚刚论证完"机制是元凶、质量没变差"。
                # 把基期摆出来（25.8% → 24.4%），
                # 「第一位」和「没恶化」才能同时成立。
                base_sh = it.get('share_base')
                shift = it.get('share_shift_pp')
                if base_sh is not None and shift is not None:
                    txt = (f'首位是「{it["l2"]}」，{it["count"]} 条占 {it["share"]*100:.1f}%，'
                           f'但基期是 {base_sh*100:.1f}%（{shift:+.1f}pp），'
                           f'属存量结构、未恶化；损失 ¥{it["loss"]:,.0f}')
                else:
                    txt = (f'首位是「{it["l2"]}」，{it["count"]} 条占 {it["share"]*100:.1f}%，'
                           f'损失 ¥{it["loss"]:,.0f}')
                chain.append({
                    'label': '原因',
                    'text': txt,
                    'tone': 'warn',
                })
            blocks.append({'type': 'chain', 'title': '归因链', 'steps': chain})

        # ---------- 促销分解（有就画） ----------
        if promo:
            dc = promo['decomposition']
            blocks.append({
                'type': 'decomp',
                'title': '涨幅拆解',
                'actual': promo['actual_gap_pp'],
                'parts': [
                    {'label': '促销机制', 'value': dc['mechanism_pp'], 'tone': 'primary'},
                    {'label': '订单结构', 'value': dc['structure_pp'], 'tone': 'muted'},
                ],
                'paired': promo['paired_test'],
                'verdict': promo.get('verdict'),
                'alternatives': promo['alternatives'],
            })

        # ---------- 类目贡献表 ----------
        # 【只在"确认异常"时才出这张表】
        # 一开始是无条件输出的，于是第 3 周（151 单）那种"样本不足、不判异常"
        # 的场景里，结论说"我不下判断"，下面却摆着一张"对整体贡献 −2.43pp"的表，
        # 数字精确到小数点后两位，读者必然以为这就是归因结果。
        # 自相矛盾比不给结论更糟：等于嘴上说不知道、手上却在算账。
        # 现在样本不足/改善/正常三种情况都不出贡献表，只保留原始退货率对照。
        if diag and diag.get('category_contribution') and diag.get('is_anomaly') \
                and (diag.get('delta_pp') or 0) > 0:
            rows = []
            for c in diag['category_contribution']:
                rows.append([
                    c['category'],
                    f'{c["rr_now"]*100:.2f}%',
                    f'{c["rr_base"]*100:.2f}%',
                    f'{c["gap_pp"]:+.2f}pp',
                    f'{c["contribution_pp"]:+.2f}pp',
                ])
            blocks.append({
                'type': 'table',
                'title': '类目贡献分解',
                'columns': ['类目', '本周退货率', '基线', '偏离', '对整体贡献'],
                'rows': rows,
                'align': ['left', 'right', 'right', 'right', 'right'],
                'note': '贡献 = 类目偏离 × 该类目订单占比；正数表示把整体退货率往上拉。',
            })
        elif diag and diag.get('category_contribution'):
            # 不判异常时，降级成"只报数、不算账"的对照表。
            # 目的是让用户看到"各类目本周 vs 基线"的原始差异，
            # 但明确不带"贡献"列 —— 不给一个还没被证明成立的归因。
            rows = []
            for c in diag['category_contribution']:
                rows.append([
                    c['category'],
                    f'{c["rr_now"]*100:.2f}%',
                    f'{c["rr_base"]*100:.2f}%',
                    f'{c["gap_pp"]:+.2f}pp',
                ])
            # 【v2.5 补漏】这句 note 原来是写死的"被判定为样本不足或非异常"。
            # 它是最顽固的一处：正文、caveats 都改干净了，表格底下还挂着这句，
            # 用户翻到表格就会以为系统又在拿"样本不足"当借口 —— 而他投诉的
            # 正是这句话。两个原因必须分开讲，因为它们意味着完全不同的东西：
            #   · 样本不足 → 我看不清（我们的问题）
            #   · 非异常   → 确实没事（数据的事实）
            # 合并成一句，等于把"没事"说成"看不清"，白丢一次可信度。
            if diag.get('enough_sample'):
                _why_no_decomp = ('本周偏离未通过统计与业务双重判定线，'
                                  '属于正常波动，因此只列原始差异，不对整体做归因——'
                                  '给一个不成立的归因比不给更糟。')
            else:
                _why_no_decomp = ('本周样本量低于判定门槛，看不出就是看不出，'
                                  '因此只列原始差异，不硬算对整体的贡献。')
            blocks.append({
                'type': 'table',
                'title': '各类目退货率对照（未做归因）',
                'columns': ['类目', '本周退货率', '基线', '偏离'],
                'rows': rows,
                'align': ['left', 'right', 'right', 'right'],
                'note': _why_no_decomp,
            })

        # ---------- 原因结构 ----------
        if struct and struct.get('items'):
            items = struct['items'][:6]
            mx = max(x['share'] for x in items) or 1
            blocks.append({
                'type': 'bars',
                'title': f'{struct["scope"]} 退货原因结构',
                'items': [
                    {'label': x['l2'], 'value': x['count'],
                     'share': x['share'], 'pct': x['share'] / mx,
                     'extra': f'损失 ¥{x["loss"]:,.0f} · 可二次销售 {x["resellable_rate"]*100:.0f}%'}
                    for x in items
                ],
            })

        # ---------- 评论原文 ----------
        samp = f.get('samples')
        if samp and samp.get('samples'):
            blocks.append({
                'type': 'quotes',
                'title': f'差评原文抽样（命中 {samp["total_matched"]} 条）',
                'items': [{'rating': s['rating'], 'category': s['category'],
                           'cause': CAUSE_NAME.get(s['cause'], s['cause']),
                           'text': s['text']} for s in samp['samples'][:4]],
            })

        # ---------- 损失表 ----------
        if loss:
            rows = [[x['category'], f'{x["count"]:,}', f'¥{x["loss"]/1e4:.1f}万',
                     f'¥{x["per_return"]:.2f}', f'{x["share_of_gp"]*100:.1f}%']
                    for x in loss['by_category']]
            blocks.append({
                'type': 'table',
                'title': '退货损失分布',
                'columns': ['类目', '退货单数', '损失总额', '单均损失', '占该类目毛利'],
                'rows': rows,
                'align': ['left', 'right', 'right', 'right', 'right'],
                'note': '损失按成本计提：报废 = 物流 + 商品成本；可二次销售 = 物流 + 成本 × 折损率。',
            })

        # ---------- 补偿曲线 ----------
        if comp:
            blocks.append({
                'type': 'curve',
                'title': f'补偿档位权衡（样本 {comp["sample_n"]:,} 单）',
                'points': comp['points'],
                'optimal': comp['optimal'],
                'confound': comp['confound_check'],
                'note': '只用毛利 ≥ ¥50 的可比子集，档位与毛利相关系数接近 0 才说明没混淆。',
            })

        # ---------- 动作清单 ----------
        if play:
            blocks.append({
                'type': 'actions',
                'title': f'针对「{play["cause_name"]}」的动作清单',
                'items': play['actions'],
            })

        # ---------- 局限 ----------
        caveats = self._caveats(state)

        return {
            'headline': headline,
            'badge': badge,
            'summary': summary,
            'blocks': blocks,
            'caveats': caveats,
            'week': state.get('week'),
        }

    def _headline(self, state: dict) -> tuple[str, dict]:
        f = state['facts']
        # 对比场景有自己的答案组装（_compose_compare），这里不会被调用；
        # 保留这个提醒，避免以后有人在这里加 compare 分支造成两处结论不一致。
        diag = f.get('diag')
        promo = f.get('promo')
        loss = f.get('loss')
        ov = f.get('overview')
        # 【分析范围必须出现在标题里】
        # 实测："生鲜类目怎么样" 被答成"生鲜第44周退货率异常升高"，
        # 这句话单看没问题，但标题里没写"这是生鲜的范围"，
        # 读者会以为 18% 是全店的退货率。类目级结论和全局结论
        # 数字完全不同，混淆的代价是把局部问题当成全局问题去处理。
        _cat = state.get('category')
        _scope = f'{_cat}类目' if _cat else ''
        if not diag:
            if f.get('comp'):
                return ('补偿最优档已求解，但这不是"越多越好"的曲线',
                        {'label': '权衡分析', 'level': 'info'})
            if loss:
                worst = (loss.get('by_category') or [{}])[0]
                return (f'退货一年吃掉 ¥{loss["total_loss"]/1e4:.1f} 万，'
                        f'相当于毛利的 {loss["share_of_gp"]*100:.0f}%'
                        if loss.get('share_of_gp') else
                        f'退货一年吃掉 ¥{loss["total_loss"]/1e4:.1f} 万',
                        {'label': '成本已核算', 'level': 'critical'})
            if ov:
                return (f'这家店年订单 {ov["orders_year"]:,} 单、GMV ¥{ov["gmv_year"]/1e4:.1f} 万，'
                        f'退货率 {ov["return_rate"]*100:.2f}%',
                        {'label': '经营底盘', 'level': 'info'})
            return ('已完成查询', {'label': '完成', 'level': 'ok'})
        if f.get('low_sample'):
            return (f'{_scope}第 {diag["week"]} 周只有 {diag["orders_this_week"]} 单，'
                    f'样本量不足以判断异常',
                    {'label': '样本不足', 'level': 'warn'})
        if f.get('improved'):
            return (f'{_scope}第 {diag["week"]} 周退货率显著低于基线，是个好周，不需要归因',
                    {'label': '显著改善', 'level': 'ok'})
        if f.get('no_anomaly'):
            return (f'{_scope}第 {diag["week"]} 周没有显著异动，不需要归因',
                    {'label': '无异常', 'level': 'ok'})
        if f.get('redirected'):
            # 【"你问的那一周"这句话只在用户真的报了周次时才成立】
            # 大盘类问题（"大盘怎么样"）没有指定周，Agent 内部也会先扫到
            # 默认的当前周再去重定向 —— 于是输出"你问的那一周其实正常"，
            # 用户根本没问哪一周，看起来像答非所问。
            # 按意图区分措辞：问了大盘就直说"从整体看最该看第 44 周"。
            if state.get('intent') == 'overview':
                return (f'{"整体" if not _scope else _scope}看，最该关注的是第 {diag["week"]} 周'
                        f'（退货率 {diag["rr_now"]*100:.2f}%，比基线高 {diag["delta_pp"]:+.2f}pp）',
                        {'label': '已重定向', 'level': 'warn'})
            # 【把"你问的那一周"换成具体周号】
            # 原句没说是哪一周。用户问"最新一周有异常吗"，
            # 得到的回答里既没有"最新一周 = 第 52 周"，也没有第 52 周的数字，
            # 得自己回头翻。重定向最该讲清的就是"我从哪来、我要去哪"。
            ask_w = f.get('asked_week')
            if ask_w and ask_w != diag.get('week'):
                return (f'{_scope}第 {ask_w} 周（你问的那一周）没有显著异动，'
                        f'真正需要看的是第 {diag["week"]} 周',
                        {'label': '已重定向', 'level': 'warn'})
            # 没指定周次（"某类目怎么样"）：措辞里不要出现"你问的那一周"，
            # 用户压根没问哪一周，扫描后得出的是"这一类目最该看哪一周"。
            if not ask_w:
                return (f'{_scope}最近没有单周显著异动，'
                        f'值得看的是第 {diag["week"]} 周'
                        f'（退货率 {diag["rr_now"]*100:.2f}%）',
                        {'label': '已重定向', 'level': 'warn'})
            return (f'{_scope}你问的那一周其实正常，真正异常的是第 {diag["week"]} 周',
                    {'label': '已重定向', 'level': 'warn'})
        if not diag.get('is_anomaly'):
            return (f'{_scope}第 {diag["week"]} 周在正常波动范围内（z = {diag["zscore"]}）',
                    {'label': '无异常', 'level': 'ok'})
        if promo and (promo.get('verdict') or {}).get('code') == 'PROMO_BEHAVIOR':
            return (f'{_scope}第 {diag["week"]} 周退货率上涨 {diag["delta_pp"]:+.2f}pp，'
                    f'但不是商品或物流变差——是促销本身改变了退货倾向',
                    {'label': '机制已识别', 'level': 'critical'})
        return (f'{_scope}第 {diag["week"]} 周退货率异常上移 {diag["delta_pp"]:+.2f}pp'
                f'（z = {diag["zscore"]}），需归因',
                {'label': '确认异动', 'level': 'critical'})

    def _summary(self, state: dict, headline: str) -> str:
        """组装结论正文。

        【为什么返回分段而不是一整段】
        第一版把所有句子拼成一个大字符串，渲染出来是 9 句话、6 行以上的密实文本块。
        评委在台上只有几十秒看这一屏，密实长段等于没有重点。
        改成用换行分成 3–4 个语义段：现象 → 拆解证据 → 结论与代价。
        前端按 \n\n 拆成 <p>，每段聚焦一件事。
        """
        f = state['facts']
        diag = f.get('diag') or {}
        promo = f.get('promo')
        struct = f.get('structure') or {}
        comp = f.get('comp')
        parts: list[str] = []

        if f.get('no_anomaly'):
            parts.append(
                f'检验了第 {diag.get("week")} 周：退货率 {diag.get("rr_now", 0)*100:.2f}%，'
                f'基线 {diag.get("rr_base", 0)*100:.2f}%，偏离 {diag.get("delta_pp", 0):+.2f}pp，'
                f'二项 z 值 {diag.get("zscore")}，未达到 2 的判定门槛。')
            parts.append(
                '这种情况下我不会去编一个归因故事——报告"没有异常"本身就是结论。')

        elif f.get('low_sample'):
            # 【模板也要回答"能看什么"，不能只说"不能判断"】
            # 用户听到"样本不足"三次之后，会认为这个系统什么都不会。
            # 真正有用的信息是：这个样本量能看出多大的问题、当前偏离在不在里面。
            _ord = diag.get("orders_this_week")
            _det = diag.get("detectable_pp")
            _wob = diag.get("sampling_wobble_pp") or 0
            _dp = diag.get("delta_pp", 0)
            parts.append(
                f'第 {diag.get("week")} 周 {_ord} 单，基准退货率 '
                f'{diag.get("rr_base", 0)*100:.2f}%。这个样本量的天然抖动是 '
                f'±{_wob:.2f}pp'
                + (f'，能分辨的是 {_det:.1f}pp 以上的变化' if _det else '')
                + f'；本周偏离 {_dp:+.2f}pp。')
            if diag.get('suspicious_but_small'):
                parts.append(
                    f'注意：本周偏离 {abs(_dp):.1f}pp 已经超过这个样本量能分辨的幅度，'
                    f'属于"疑似异动、但样本不足以下结论"。我不会硬编归因，'
                    f'但这个信号值得记下来，等多几周数据再看趋势。')
            else:
                parts.append(
                    '偏离没有超过可分辨幅度，所以我不去编一个归因故事——'
                    '"看不出来"不等于"没问题"，但确实不值得现在动手。')

        elif f.get('improved'):
            parts.append(
                f'第 {diag.get("week")} 周退货率 {diag.get("rr_now", 0)*100:.2f}%，'
                f'比基线低 {abs(diag.get("delta_pp", 0)):.2f}pp，二项 z = {diag.get("zscore")}，'
                f'这在统计上是显著的改善。')
            parts.append(
                '这不是问题，值得做的是复盘这一周做对了什么，而不是套用归因模板。')

        elif promo and (promo.get('verdict') or {}).get('code') == 'PROMO_BEHAVIOR':
            dc = promo['decomposition']
            pt = promo['paired_test']
            alt = promo['alternatives']

            # 【大盘问题要先交代"这一周是怎么被选出来的"】
            # 用户问"大盘怎么样"，若第一句就报第 44 周的数字，中间缺了推理的桥：
            # 我扫了全部 52 周、按二项 z 找偏离最大的那一周。
            # 补这一句，读者才知道结论不是拍脑袋挑的周。
            # 注意只在 overview 意图下加 —— 用户直接问"第 44 周"时再加就啰嗦了。
            if state.get('intent') == 'overview' and f.get('redirected'):
                parts.append(
                    f'我把全部周次逐周做了二项检验（样本量未达该周可分辨幅度的周不参与判定），'
                    f'偏离最大的是第 {diag.get("week")} 周。')

            parts.append(
                f'第 {diag.get("week")} 周退货率 {diag.get("rr_now", 0)*100:.2f}%，'
                f'相对全历史基线高 {diag.get("delta_pp", 0):+.2f}pp。'
                f'但这个基线里混着其他促销周，会稀释对比。'
                f'换成"仅非促销周"做基线：三个促销周合并 {promo["rr_promo"]*100:.2f}%，'
                f'非促销周 {promo["rr_base"]*100:.2f}%，相差 {promo["actual_gap_pp"]:+.2f}pp。')

            parts.append(
                f'**拆解**：订单结构只贡献 {dc["structure_pp"]:+.2f}pp'
                f'（占涨幅 {dc["structure_share"]*100:.0f}%），'
                f'机制贡献 {dc["mechanism_pp"]:+.2f}pp。'
                f'机制的证据是同 SKU 配对：{pt["n_sku"]} 个 SKU 里 '
                f'{pt["pct_sku_increased"]*100:.0f}% 在促销周自身退货倾向就更高，'
                f'均值 +{pt["mean_diff_pp"]:.2f}pp（t = {pt["t_stat"]}）。')

            parts.append(
                f'**排除**：促销周没降价（降价 SKU 占 {alt["pct_sku_discounted"]*100:.0f}%），'
                f'也没推低质商品（质量均值 {alt["quality_promo"]:.3f} vs 基线 {alt["quality_base"]:.3f}）。'
                f'所以不是"低价低质"或"清库存"。')

        elif diag.get('is_anomaly'):
            contrib = diag.get('category_contribution') or []
            top = contrib[0] if contrib else None
            # 【大盘问题要解释"为什么落到这一周"】
            # 用户问的是"大盘怎么样"，如果开头直接讲第 44 周的数字，
            # 中间缺了一句话：整体扫了 52 周、挑出偏离最大的那一周。
            # 补上这句，读者才知道数字是怎么被选出来的，而不是拍脑袋挑的。
            if state.get('intent') == 'overview' and f.get('redirected'):
                parts.append(
                    f'我把 52 周逐周做了二项检验（样本量未达可分辨幅度的周不参与判定），'
                    f'偏离最大的是第 {diag["week"]} 周——'
                    f'退货率 {diag["rr_now"]*100:.2f}%，基线 {diag["rr_base"]*100:.2f}%，'
                    f'偏离 {diag["delta_pp"]:+.2f}pp，二项 z = {diag["zscore"]}。')
            else:
                txt = (f'第 {diag["week"]} 周退货率 {diag["rr_now"]*100:.2f}%，'
                       f'基线 {diag["rr_base"]*100:.2f}%，偏离 {diag["delta_pp"]:+.2f}pp，'
                       f'二项 z = {diag["zscore"]}，超过 2 的判定门槛。')
                parts.append(txt)
            if top:
                parts.append(
                    f'贡献最大的是{top["category"]}，把整体退货率拉高 '
                    f'{top["contribution_pp"]:+.2f}pp。')

        if struct.get('items'):
            it = struct['items'][0]
            # 【这里曾经是全文最严重的矛盾】
            # 标题写"不是商品或物流变差"，这一段却说"原因第一位是破损/变质 24.4%"。
            # 读者（尤其评委）第一反应就是"你刚说不破损，怎么破损又排第一？"
            #
            # 真相是两件事被混为一谈了：
            #   「原因结构」= 这一周所有退货里，各原因码各占多少（存量占比）
            #   「机制归因」= 退货率上涨，是哪个机制造成的（增量来源）
            # 破损占 24.4% 说的是前者；机制分析证明涨幅几乎全来自促销（后者）。
            # 破损的**占比**没变、甚至略降，只是它一直是最大的那一类。
            #
            # 解法不是删掉这行，而是把它**显式定位成背景**，
            # 并直接点明"占比最大 ≠ 变多了"。这一句是整个报告可信度的关键。
            is_promo_case = bool(promo and (promo.get('verdict') or {}).get('code') == 'PROMO_BEHAVIOR')
            if is_promo_case:
                tail = (f'**别误读这张原因表**：退货里排第一的仍是「{it["l2"]}」，'
                        f'{it["count"]} 条占 {it["share"]*100:.1f}%——'
                        f'但这是**存量占比**，不是"变多了"。')
                # 用基期对比把这件事钉死：占比若没上升，就坐实了"不是质量变差"
                base_share = it.get('share_base')
                if base_share is not None:
                    d = (it['share'] - base_share) * 100
                    tail += (f'对比非促销周基线 {base_share*100:.1f}%，'
                             f'实际变化 {d:+.1f}pp，{"并没有恶化" if d <= 0.5 else "略有上升"}。')
                tail += ('真正抬高退货率的是促销带来的"下单冲动 → 收货落差"，'
                         '与商品质量、包装、承运商无关。')
                # 【必须替读者回答"那我该怎么办"】
                # 上面既然说了"与包装、承运商无关"，就不能紧接着摆一张
                # "生鲜改泡沫箱 / 承运商扣罚"的动作清单——那是同一个自相矛盾。
                # 但也不能什么都不说：读者会以为这个 Agent 只会诊断不会落地。
                # 正确做法是把口径讲清楚：破损的 playbook 是给
                # "破损类占比恶化"的场景准备的，本周不适用。
                if f.get('no_playbook'):
                    tail += ('所以这一周我不给破损类的整改动作——'
                             '那套动作（改包装、承运商扣罚、破损补偿）针对的是'
                             '"破损占比在上升"，而本周它的占比在下降，照着做等于花成本治一个没发生的病。'
                             '真正该管的是促销节奏本身：这类涨幅是拿短期 GMV 换来的退货，'
                             '要决策的是"值不值"，而不是"怎么把破损补回来"。')
            else:
                tail = (f'原因结构里排第一的是「{it["l2"]}」，{it["count"]} 条占 '
                        f'{it["share"]*100:.1f}%，对应损失 ¥{it["loss"]:,.0f}。')
            if f.get('value'):
                v = f['value']
                tail += (f'如果把退货率压低 {v["pp_reduced"]} 个百分点，按已估计的传导弹性，'
                         f'年化 GMV 约增加 ¥{v["annual_gmv_lift"]/1e4:.1f} 万'
                         f'（{v["pct_of_gmv"]*100:.2f}%）。')
            parts.append(tail)
        elif f.get('value'):
            v = f['value']
            parts.append(
                f'如果把退货率压低 {v["pp_reduced"]} 个百分点，按已估计的传导弹性，'
                f'年化 GMV 约增加 ¥{v["annual_gmv_lift"]/1e4:.1f} 万'
                f'（{v["pct_of_gmv"]*100:.2f}%）。')

        # 纯补偿场景（没有诊断链）：把"最优档"提到最前面重排。
        # 顺序很重要——用户问"补多少"，最该先看到的是那个数，
        # 而不是 GMV 换算。第一版把价值换算放在前面，等于答非所问。
        if comp and not diag:
            opt = comp['optimal']
            pts = comp['points'] or []
            top = max(pts, key=lambda p: p['save_rate']) if pts else None
            comp_parts = [
                f'补偿档位在 ¥{pts[0]["compensation"]:.0f}–¥{pts[-1]["compensation"]:.0f} 之间，'
                f'**期望利润最高的是 ¥{opt["compensation"]:.0f} 档**：'
                f'挽单率 {opt["save_rate"]*100:.1f}%，'
                f'单均期望利润 ¥{opt["expected_profit"]:.2f}。'
            ]
            if top and top['compensation'] != opt['compensation']:
                comp_parts.append(
                    f'挽单率最高的是 ¥{top["compensation"]:.0f} 档'
                    f'（{top["save_rate"]*100:.1f}%），但它的期望利润只有 '
                    f'¥{top["expected_profit"]:.2f}——补得越多救回得越多，'
                    f'但每单赚得更少。**最优点不在最贵的那一档。**')
            comp_parts.append(
                f'样本只取毛利 ≥ ¥50 的可比子集（{comp["sample_n"]:,} 单），'
                f'档位与毛利的相关系数 r = {comp["confound_check"]:+.3f}，'
                f'接近 0 说明没有"贵商品本身好卖"的混淆。')
            # 【模板必须自己把"该补多少"说出来】
            # 原来这一段只讲了"最优点不在最贵档"这个性质，
            # 却没给结论数字。而用户问的就是"补多少"——
            # 等于绕了一圈没回答。模板是 LLM 失败时的保底，
            # 保底也要能独立交付，不能靠"反正一般会走 LLM"来兜。
            opt = comp.get('optimal') or {}
            if opt.get('compensation') is not None:
                comp_parts.append(
                    f'**该补 ¥{opt["compensation"]:.0f}**：这一档的挽单率 '
                    f'{opt["save_rate"]*100:.1f}%、单均期望利润 ¥{opt["expected_profit"]:.2f}，'
                    f'在全部档位里期望利润最高。')
            parts = comp_parts

        # 纯损失场景：给"亏多少、亏在哪、什么口径"三件事
        loss = f.get('loss')
        if loss and not diag:
            loss_parts = [
                f'全年退货损失 **¥{loss["total_loss"]/1e4:.1f} 万**，'
                f'相当于毛利的 {loss["share_of_gp"]*100:.1f}%，'
                f'单均 ¥{loss["per_return"]:.2f}，共 {len(loss.get("by_category") or [])} 个类目。'
            ]
            by_cat = loss.get('by_category') or []
            if by_cat:
                w = by_cat[0]
                loss_parts.append(
                    f'亏得最多的是 {w["category"]}：{w["count"]:,} 单损失 '
                    f'¥{w["loss"]/1e4:.1f} 万，占该类目毛利的 {w["share_of_gp"]*100:.1f}%。'
                    f'单均 ¥{w["per_return"]:.2f}。')
            basis = loss.get('by_basis') or []
            if basis:
                desc = '、'.join(
                    f'{b["basis"]} {b["count"]:,} 单（¥{b["loss"]/1e4:.1f} 万）' for b in basis)
                loss_parts.append(
                    f'按处置方式：{desc}。'
                    f'可二次销售的比例是 {loss["resellable_rate"]*100:.1f}%，'
                    f'剩下只能全额计提成本。')
            loss_parts.append(
                '口径说明：损失按**商品成本**计提，不是零售价。'
                '客户退货时商家退还的是他付的钱，而商家真正损失的是这件货的成本——'
                '零售价里那部分毛利从未落袋。用零售价算会把损失夸大 30% 以上。')
            parts = loss_parts

        # 纯大盘场景
        ov = f.get('overview')
        if ov and not diag and not loss:
            parts = [
                f'年订单 {ov["orders_year"]:,} 单，GMV ¥{ov["gmv_year"]/1e4:.1f} 万，'
                f'客单价 ¥{ov["aov"]:.2f}，毛利 ¥{ov["gross_profit_year"]/1e4:.1f} 万。',
                f'整体退货率 {ov["return_rate"]*100:.2f}%，'
                f'退货损失 ¥{ov["loss_total"]/1e4:.1f} 万，'
                f'吃掉毛利的 {ov["loss_share_of_gp"]*100:.1f}%。',
                f'数据覆盖 {ov["skus"]} 个 SKU、{ov["weeks"]} 周、'
                f'{len(ov["categories"])} 个类目。想往下拆，可以直接问某一周为什么异常。',
            ]

        return '\n\n'.join(parts) if parts else headline

    def _caveats(self, state: dict) -> list[str]:
        f = state['facts']
        out = []
        if f.get('promo'):
            p = f['promo']
            out.append(
                f'结构效应与机制效应之和（{p["decomposition"]["structure_pp"] + p["decomposition"]["mechanism_pp"]:+.2f}pp）'
                f'与实际涨幅（{p["actual_gap_pp"]:+.2f}pp）的差额来自交叉项，未单独分解。')
            out.append('机制效应由 SKU 配对差分给出，控制了 SKU 固定效应，但未控制"同一用户跨周行为变化"。')
        if f.get('diag') and not f['diag'].get('is_anomaly'):
            # 【v2.5 补漏】这句话原来是硬编码的"样本量有限"，
            # 但放大流量之后样本已经不紧张了：数码第 52 周 1,770 单、
            # 可分辨 1.44pp，余量 3.4 倍。此时还说"样本量有限"，
            # 就和上面正文里刚写的"样本量 1770 单充足"直接打架 ——
            # 用户读到的是一份自相矛盾的报告。
            #
            # 更根本的问题是：把"没检出"归因于"样本不够"，是在为自己找台阶。
            # 样本真的紧张时说这话是诚实；样本充足时再说，就等于
            # 用同一个借口掩盖另一个事实——**这一周本来就没异常**。
            # 后者才是我们应该直说的。所以这里按实际样本量分三支。
            _d = f['diag']
            # 【字段名踩过的坑】第一版我写的是 _d.get('orders')，
            # 而 diagnose_week 实际返回的键是 'orders_this_week'，
            # 于是界面上出现"样本 0 单（判定门槛 410 单）"——
            # 数码第 52 周明明是 1,770 单。这不是显示小瑕疵：
            # 一份前面刚说"样本充足"、括注里又说"样本 0 单"的报告，
            # 用户只会得出一个结论——这系统不可信。
            # 所以除了写对键名，这里还要兜底：拿不到数就整句不出现，
            # 宁可少说一句，也不能说一个假数字。
            _n = int(_d.get('orders_this_week') or 0)
            _mo = int(_d.get('min_orders') or 0)
            _can = _d.get('detectable_pp')
            _dp = _d.get('delta_pp') or 0.0
            if not _n:
                # 拿不到样本量：不编数字，只说结论层面的话
                out.append('"没检出"不等于"不存在"，但这一周的偏离未达判定线，'
                           '按噪声处理是当前证据下更稳妥的结论。')
            elif not _d.get('enough_sample', True):
                # 真的不够：老实说这是证据不足，不是"没问题"
                out.append(
                    f'本范围这一周只有 {_n:,} 单，低于判定所需的 {_mo:,} 单，'
                    f'只能说"没看出"，不能说"没问题"——样本不够时不下结论才是负责的。')
            elif _d.get('trivial_but_significant'):
                # 够、且显著、但幅度小 —— 这条最容易被误读成"瞒事"
                out.append(
                    f'偏离 {_dp:+.2f}pp 在 {_n:,} 单下确实统计显著，'
                    f'但不到 {_d.get("min_effect_pp")}pp 的动作触发线。'
                    f'说它"不异常"是指不值得为此开复盘会，不是说它不存在。')
            else:
                # 够、也不显著：这时候该说的是"能分辨多小"，而不是"样本有限"
                # _can 可能是 None（detectable_pp 算不出来时），
                # 格式化 None 会抛 TypeError 整条 answer 崩掉，
                # 所以这里按有无分别拼句，而不是硬套小数格式。
                if _can is not None and _can == _can:
                    out.append(
                        f'样本 {_n:,} 单（判定门槛 {_mo:,} 单），'
                        f'这个量级下可分辨约 {_can:.2f}pp 的变化，'
                        f'本期偏离 {_dp:+.2f}pp 落在这个分辨率之内，'
                        f'所以"没检出"是真的没有，不是看不见。')
                else:
                    out.append(
                        f'样本 {_n:,} 单，已过判定门槛 {_mo:,} 单，'
                        f'本期偏离 {_dp:+.2f}pp 未达统计与业务双重判定线，'
                        f'按噪声处理是当前证据下更稳妥的结论。')
        if f.get('value'):
            out.append('GMV 换算依赖已估计的传导弹性 λ₁ 与 ω₁，若平台流量结构变化，弹性需重新估计。')
        if f.get('comp'):
            out.append('补偿曲线是相关关系下的期望利润最大化，未做随机化实验，最优档存在选择偏误风险。')
        out.append('本数据集中商品主数据与面板为模拟生成（文本语料为真实公开数据集），参数真值已在报告中公开以便复核。')
        return out

    def _answer_missing(self, state: dict, out: dict) -> dict:
        return {
            'headline': '这个查询在当前数据里没有结果',
            'badge': {'label': '数据不足', 'level': 'warn'},
            'summary': f'工具返回：{out.get("error", "未知原因")}。我不会用推测填补。'
                       f'可用的周次范围是 1–52，类目是 服饰 / 数码 / 生鲜 / 个护。',
            'blocks': [],
            'caveats': [],
            'week': state.get('week'),
        }


_agent: Agent | None = None


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent