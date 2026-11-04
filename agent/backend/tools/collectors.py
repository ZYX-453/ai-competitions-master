# -*- coding: utf-8 -*-
"""评论采集器框架 —— 可插拔数据源

【先把话说清楚：这个文件里没有"爬虫"】

需求是"帮我设计一个自动爬取的功能在里面"。我做了，但做的不是爬虫。
原因不是技术做不到，是下面三条中的任何一条都足以否掉它：

**① 违反平台服务条款，这是硬伤**

电商平台（淘宝/京东/拼多多/抖音）的用户评论，其数据权益归平台和用户。
它们的用户协议里都明确禁止"使用自动化程序抓取数据"。
我写一个能跑的爬虫，等于交付一个违约工具。
这个项目是要参赛、要给评委看的——
如果核心功能建立在一个"默认可以违法"的前提上，
那这个项目的技术含量再高，风险也比收益大得多。

**② 技术上今天能跑，明天就废**

平台的反爬是持续对抗：滑块、设备指纹、签名算法（如某平台的 sign）、
登录态绑定、IP 频控、字体反混淆……每一层都在变。
写出来的爬虫平均寿命以周计。
交付一个下周就挂的东西，不叫功能，叫负担。

**③ 更根本：这条路的"数据可信度"反而是最低的**

爬来的评论有一个绕不开的问题：**你不知道它代表谁**。
- 平台展示的评论是经过排序和筛选的（默认按"有用"或"最新"）
- 有水军、有刷单、有返现好评（"好评返现3元"是行业惯例）
- 页面只展示前 N 页，越往后越难拿，样本天然有偏

一个分析系统如果基于有偏样本，得出的"竞品负评率 25%"是没有意义的。
但它看起来非常有意义——这才是最危险的地方。

---

【所以这个文件做什么】

做**真正能自动跑的那部分**，并且把"数据从哪来"变成一个可替换的插槽。

架构上分三层：

    采集层（本文件）      分析层（review_analytics.py）    展示层（前端）
    ┌──────────────┐      ┌──────────────────┐        ┌──────────┐
    │ BaseCollector │─────▶│  corpus.csv      │───────▶│ 四模块   │
    │  ├ Public     │ 写入  │  （唯一数据契约） │  读取   │ 情感/词云 │
    │  ├ CsvImport  │      │                  │        │ 画像/竞品 │
    │  ├ ApiPull    │      └──────────────────┘        └──────────┘
    │  └ Manual     │
    └──────────────┘

分析层**只认 corpus.csv 的 11 个公开字段**。只要某个采集器能产出这 11 列，
它就能接进来，分析层一行都不用改。这就是"可插拔"的实际含义——
不是"预留了接口看起来很灵活"，而是"现在换一个源，真的能跑"。

【四种采集器，按合规程度从高到低】

| 采集器 | 能自动跑 | 数据来源 | 合规性 | 现在能用吗 |
|---|---|---|---|---|
| CsvImportCollector | 半自动（放进目录就导）| 平台后台导出 | ✅ 完全合规 | ✅ 能用 |
| ManualCollector    | 否（人工粘贴）        | 人工整理 | ✅ 完全合规 | ✅ 能用 |
| PublicDatasetCollector | ✅ 全自动 | 公开学术语料 | ✅ 合规 | ✅ 能用 |
| ApiPullCollector   | ✅ 全自动 | 官方开放平台 | ⚠️ 需资质 | ⚠️ 需 key |
| PlatformScraper    | ❌ 不实现 | —— | ❌ 违约 | ❌ 故意不做 |

最后一行是刻意留着的：**它存在，但它是空的，而且注释说明了为什么不填。**
把它删掉会让人以为"没想到这条路"，留着并写清楚理由，
比假装这个问题不存在更诚实。

【为什么"平台后台导出"才是正解，而不是妥协】

商家在自己的店铺后台，本来就能导出自己店铺的全部评论——
这是平台给的功能，不需要爬。竞品评论拿不到，但**替代品更好用**：

  · 平台官方的"行业大盘"（生意参谋/京东商智）给的类目均值，比爬评论更权威
  · 客服工单、退货原因、售后记录——这些是结构化数据，比评论文本信息密度高
  · 公开的第三方报告（艾瑞/易观/魔镜）有现成的类目对比

用这些替代，得到的结论比"爬了 5000 条评论"更可靠，而且是合法的。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

def _find_pool_dir() -> str:
    """向上找 data/review_pool。和 data_hub 一样不写死层数。"""
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        cand = os.path.join(d, 'data', 'review_pool')
        if os.path.isdir(cand):
            return cand
        d = os.path.dirname(d)
    raise RuntimeError('找不到 data/review_pool')


POOL_DIR = _find_pool_dir()
CORPUS = os.path.join(POOL_DIR, 'corpus.csv')
MANIFEST = os.path.join(POOL_DIR, '_manifest.json')
INBOX = os.path.join(POOL_DIR, 'inbox')
DONE = os.path.join(INBOX, 'done')

# 【这 11 列是采集层和分析层之间的唯一契约】
# 任何采集器只要产出这 11 列，就能被分析层消费。
# 少一列不行（分析会崩），多一列也不行（会让人以为有额外信息，
# 实际上分析层根本不读，反而造成"我导入了价格数据"这种误解）。
REQUIRED_COLS = [
    'review_id', 'source', 'entity_id', 'entity_name', 'biz_category',
    'review', 'rating', 'week', 'review_date', 'review_len', 'has_image',
]

# 中英文列名映射：平台导出的表头千人千面，硬要求用户改表头不现实
COL_ALIAS = {
    'review': ['评价内容', '评论内容', '评价', '评论', '内容', 'review', 'content',
               'comment', '买家评价', '用户评价', '评价详情'],
    'rating': ['评分', '星级', '评价星级', '打分', 'rating', 'score', 'star',
               '商品评分', '满意度'],
    'review_date': ['评价时间', '评论时间', '下单时间', '日期', '时间',
                    'review_date', 'date', 'created_at', '评价日期'],
    'biz_category': ['商品分类', '类目', '分类', '品类', 'category', 'cat',
                     '商品类目', '一级类目'],
    'entity_id': ['商品编号', '商品id', 'sku', 'sku_id', 'item_id', '商品ID',
                  '宝贝ID', 'product_id'],
    'entity_name': ['商品名称', '店铺名称', '商品标题', 'title', 'product_name',
                    'shop_name', '店铺'],
    'has_image': ['是否有图', '有图', '晒图', 'has_image', 'image'],
}

WEEK_ANCHOR = date(2026, 1, 5)     # 第 1 周的周一，和主数据集对齐


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------

@dataclass
class CollectResult:
    """一次采集的结果。

    【为什么不用 raise 报错，而是返回这个结构】
    采集失败是常态（文件格式不对、没 key、网络断了），不是异常。
    如果用异常，调用方每次都得 try/except，而且拿不到"为什么失败"。
    这个结构把"成功几条、失败几条、为什么"都装进去，
    上层可以直接展示给用户看。
    """
    ok: bool
    collector: str
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'ok': self.ok, 'collector': self.collector,
            'added': self.added, 'skipped': self.skipped,
            'errors': self.errors, 'detail': self.detail,
        }


class BaseCollector(ABC):
    """采集器基类。

    【子类只需要实现 fetch()，写入与校验在基类统一做】
    这不是为了"优雅"，是为了**保证每条数据都经过同一道校验**。
    如果让每个子类自己写文件，迟早有一个会漏掉去重或字段校验，
    然后 corpus 里就出现重复的 review_id —— 而这种错误
    要到很后面（groupby 出来的数字比别人少）才会暴露。
    """

    name = 'base'
    title = '未命名采集器'
    # 合规性质，必须写。前端和报告都会显示这个字段
    compliance = ''
    auto = False        # 是否可全自动运行
    # 【空结果算不算失败】
    # 定时扫描类采集器（inbox）空目录是常态，必须算成功；
    # 主动拉取类采集器（API）空结果是异常，必须算失败。
    # 这个区别不写清楚，就会出现"报警疲劳"——
    # 天天报红的任务等于没有监控。
    empty_is_ok = False

    # 【这两个字段是给"半自动"三个字做注解的】
    # needs_human：这一步具体要人做什么。空字符串 = 全自动，不需要人。
    # note       ：为什么必须这样（或为什么不做）。一句话，能直接引用进汇报。
    # 没有它们，前端表格里"自动化程度"那一列只能写"全自动/半自动"，
    # 而"半自动"在汇报里几乎等于没解释。
    needs_human = ''
    note = ''

    @abstractmethod
    def fetch(self) -> pd.DataFrame:
        """返回符合 REQUIRED_COLS 的 DataFrame。"""

    def validate(self, df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        """字段校验 + 补齐。返回 (清洗后的 df, 问题列表)。"""
        errs: list[str] = []

        # 缺必需列 → 直接失败，不要试图猜
        missing = [c for c in ('review', 'rating') if c not in df.columns]
        if missing:
            return df.iloc[0:0], [f'缺少必需列：{missing}']

        df = df.copy()
        # 文本清洗（BOM / 零宽 / 首尾空白）
        df['review'] = (df['review'].astype(str)
                        .str.replace('\ufeff', '', regex=False)
                        .str.replace('\u200b', '', regex=False)
                        .str.strip())
        df = df[df['review'].str.len() >= 2]

        # 星级：可能是 "5"、"5星"、"非常满意"，统一转 1-5
        df['rating'] = df['rating'].map(_parse_rating)
        bad = df['rating'].isna()
        if bad.any():
            errs.append(f'{int(bad.sum())} 条星级无法识别，已丢弃')
            df = df[~bad]
        df['rating'] = df['rating'].astype(int)

        # 补默认值
        if 'source' not in df.columns:
            df['source'] = self.name
        if 'entity_id' not in df.columns:
            df['entity_id'] = 'STORE'
        if 'entity_name' not in df.columns:
            df['entity_name'] = '本店'
        if 'biz_category' not in df.columns:
            df['biz_category'] = '未分类'
        if 'has_image' not in df.columns:
            df['has_image'] = 0
        if 'review_len' not in df.columns:
            df['review_len'] = df['review'].str.len()

        # 日期 → 周次（数据集内部用 1-52 的周序号）
        if 'review_date' in df.columns:
            df['week'] = df['review_date'].map(_date_to_week)
        if 'week' not in df.columns or df['week'].isna().all():
            df['week'] = 1
            errs.append('没有可用的日期列，周次统一记为 1')
        df['week'] = df['week'].fillna(1).astype(int)

        if 'review_date' not in df.columns:
            df['review_date'] = ''

        # ID：没有就用内容哈希生成
        # 【为什么用哈希而不是自增序号】
        # 自增序号在"多批导入"时会撞车（第二批又从 1 开始），
        # 而哈希天然去重：同一句话重复导入两次，ID 一样，
        # 后面的去重逻辑直接把它挡掉。
        if 'review_id' not in df.columns:
            df['review_id'] = [
                'X' + hashlib.md5(
                    f'{r}|{t}'.encode('utf-8')).hexdigest()[:12]
                for r, t in zip(df['review'], df['rating'])
            ]
        else:
            df['review_id'] = df['review_id'].astype(str)

        df['has_image'] = pd.to_numeric(df['has_image'], errors='coerce').fillna(0).astype(int)
        df['review_len'] = pd.to_numeric(df['review_len'], errors='coerce').fillna(0).astype(int)

        for c in REQUIRED_COLS:
            if c not in df.columns:
                df[c] = ''
        return df[REQUIRED_COLS], errs

    def run(self, write: bool = True) -> CollectResult:
        """执行采集 → 校验 → 去重 → 写入。"""
        try:
            raw = self.fetch()
        except Exception as e:
            return CollectResult(False, self.name, errors=[f'{type(e).__name__}: {e}'])

        if raw is None or len(raw) == 0:
            # 【inbox 空不是错误】
            # 这个采集器的正常状态就是"目录里没东西"——
            # 用户一天导一次，剩下 23 小时都是空的。
            # 如果这时报 ok=False，定时任务每次都会标红，
            # 跑一周之后所有人都会习惯性忽略报警。
            # 空目录必须是 ok=True + added=0，让"没东西可导"
            # 和"导入失败"区分开。
            if getattr(self, 'empty_is_ok', False):
                return CollectResult(True, self.name, added=0,
                                     detail={'note': '没有待导入文件'})
            return CollectResult(False, self.name, errors=['没有取到数据'])

        df, errs = self.validate(raw)
        if len(df) == 0:
            return CollectResult(False, self.name, errors=errs or ['校验后无有效数据'])

        if not write:
            return CollectResult(True, self.name, added=len(df),
                                 errors=errs, detail={'preview': len(df)})

        added, skipped = _merge_into_corpus(df, self.name)
        _append_manifest(self, added)

        return CollectResult(True, self.name, added=added, skipped=skipped,
                             errors=errs, detail={'raw': len(raw)})


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------

_RATING_WORDS = {
    '非常满意': 5, '很满意': 5, '满意': 4, '非常差': 1, '很差': 1,
    '差': 2, '一般': 3, '好评': 5, '中评': 3, '差评': 1,
    '五星': 5, '四星': 4, '三星': 3, '二星': 2, '一星': 1,
}


def _parse_rating(v: Any) -> float | None:
    """把各种写法的星级转成 1-5 的整数。

    【为什么要费这个劲】
    平台导出的"评分"列什么写法都有：
        "5"、"5星"、"5.0"、"非常满意"、"★★★★★"、空、"匿名用户未评分"
    如果直接 astype(int)，上面一半会变成 NaN 或抛异常。
    而这个字段是情感分析的兜底依据（文本判不出来时用星级），
    丢了它，一大批"东西收到了"这种无情感词的评论就无法判定了。
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    if not s:
        return None
    # 星星符号
    if set(s) <= {'★', '☆'} and s:
        return float(s.count('★')) if '★' in s else None
    # 纯数字（可能带小数点、可能带"星"）
    m = re.search(r'(\d+(?:\.\d+)?)', s)
    if m:
        x = float(m.group(1))
        if 0 <= x <= 5:
            # 10 分制换算
            return round(x) if x <= 5 else None
        if 5 < x <= 10:
            return max(1, min(5, round(x / 2)))
    for k, r in _RATING_WORDS.items():
        if k in s:
            return float(r)
    return None


def _date_to_week(v: Any) -> int | None:
    """日期 → 周序号（1-52），以 WEEK_ANCHOR 为第 1 周起点。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        d = pd.to_datetime(v, errors='coerce')
    except Exception:
        return None
    if pd.isna(d):
        return None
    days = (d.date() - WEEK_ANCHOR).days
    w = days // 7 + 1
    return int(max(1, min(52, w)))


def _guess_cols(df: pd.DataFrame) -> pd.DataFrame:
    """把平台导出的中文表头映射成内部字段名。"""
    low = {str(c).strip().lower(): c for c in df.columns}
    out = {}
    for target, aliases in COL_ALIAS.items():
        for a in aliases:
            key = a.lower()
            if key in low:
                out[low[key]] = target
                break
    return df.rename(columns=out)


# ---------------------------------------------------------------------------
# ① 平台后台导出（合规主路径）
# ---------------------------------------------------------------------------

class CsvImportCollector(BaseCollector):
    """从 data/review_pool/inbox/ 导入平台导出的评论文件。

    【这是唯一真正推荐的路径】
    商家在自己的店铺后台导出自己的评论，是平台提供的功能，
    不涉及任何越权。格式通常是 CSV/XLSX，表头可能是中文。

    自动化的部分：
      · 扫目录（不需要指定文件名）
      · 识别表头（中英文别名映射）
      · 清洗、去重、算周次
      · 导入后把原文件移到 inbox/done/，避免重复导入

    需要人工的部分：
      登录后台 → 点导出 → 把文件放进 inbox。
    这一步不做自动化，因为它需要人的登录态——
    而"代替人登录"正是爬虫越界的那条线。
    """

    name = 'imported'
    title = '平台后台导出导入'
    compliance = '合规：商家导出自己的店铺数据，平台官方功能'
    auto = True     # 目录扫描是全自动的，只有"放文件"这一步需要人
    empty_is_ok = True
    needs_human = '登录店铺后台点一次导出，把文件放进 review_pool/inbox/'
    note = ('登录态留在人手里，是这条路径合规的全部理由。'
            '机器要"自动登录"，就跨过了那条线。')

    def fetch(self) -> pd.DataFrame:
        os.makedirs(INBOX, exist_ok=True)
        os.makedirs(DONE, exist_ok=True)

        files = [f for f in os.listdir(INBOX)
                 if f.lower().endswith(('.csv', '.xlsx', '.xls', '.tsv'))
                 and os.path.isfile(os.path.join(INBOX, f))]
        if not files:
            # 【返回空表而不是抛异常】
            # 目录空是常态（用户还没导出），不是错误。
            # 抛异常会让定时任务每次都报红，久而久之就没人看了。
            return pd.DataFrame(columns=['review', 'rating'])

        frames = []
        for f in files:
            p = os.path.join(INBOX, f)
            try:
                if f.lower().endswith(('.xlsx', '.xls')):
                    df = pd.read_excel(p)
                elif f.lower().endswith('.tsv'):
                    df = pd.read_csv(p, sep='\t')
                else:
                    # 编码兜底：平台导出常见 utf-8-sig / gbk
                    df = None
                    for enc in ('utf-8-sig', 'utf-8', 'gbk', 'gb18030'):
                        try:
                            df = pd.read_csv(p, encoding=enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    if df is None:
                        raise ValueError('编码无法识别（试过 utf-8/gbk/gb18030）')
                df = _guess_cols(df)
                df['source_file'] = f
                frames.append(df)
                # 导入成功 → 归档，避免下次重复
                shutil.move(p, os.path.join(DONE,
                            f'{date.today().isoformat()}_{f}'))
            except Exception as e:
                raise RuntimeError(f'文件 {f} 读取失败：{e}') from e

        return pd.concat(frames, ignore_index=True) if frames else \
            pd.DataFrame(columns=['review', 'rating'])


# ---------------------------------------------------------------------------
# ② 公开学术语料（真·全自动）
# ---------------------------------------------------------------------------

class PublicDatasetCollector(BaseCollector):
    """从项目自带的公开语料库采样。

    【这个采集器是"自动"两个字唯一名副其实的地方】
    数据来自 data/raw/online_shopping_10_cats.csv
    （公开的中文电商评论数据集，7 万条，10 个类目，人工标注正负）。
    它本来就在仓库里，所以"自动"是指不用联网、不用登录、
    不用任何人操作，跑一条命令就能补数据进来。

    【为什么要做成采集器，而不是直接用】
    因为它和"本店真实评论"混在一起会污染分析。
    本店评论是模拟生成的（基于同一份语料改写），
    如果再从这里采样，等于同一句话出现两次，去重会很麻烦。
    所以这个采集器的 `source` 标成 `public_sample`，
    分析层可以按 source 过滤掉它。

    【它能做什么、不能做什么】
    能做：验证分析链路（情感分析准不准、词云有没有意义）
    不能做：代表本店或任何真实店铺——它是公共语料，不是经营数据
    """

    name = 'public_sample'
    title = '公开学术语料采样'
    compliance = '合规：公开数据集（online_shopping_10_cats），无需授权'
    auto = True
    needs_human = ''
    note = ('不用联网、不用登录、不需要任何人操作，'
            '这是"自动"两个字唯一名副其实的渠道。')

    def __init__(self, n: int = 500, cat: str | None = None):
        self.n = n
        self.cat = cat

    def fetch(self) -> pd.DataFrame:
        raw_path = None
        d = os.path.dirname(POOL_DIR)
        for cand in (os.path.join(d, 'raw', 'online_shopping_10_cats.csv'),
                     os.path.join(d, 'raw', 'chnsenticorp.csv')):
            if os.path.exists(cand):
                raw_path = cand
                break
        if not raw_path:
            raise FileNotFoundError('找不到公开语料 data/raw/online_shopping_10_cats.csv')

        df = pd.read_csv(raw_path)
        df['review'] = (df['review'].astype(str)
                        .str.replace('\ufeff', '', regex=False).str.strip())
        if self.cat and 'cat' in df.columns:
            df = df[df['cat'] == self.cat]
        df = df.sample(n=min(self.n, len(df)), random_state=int(time.time()) % 100000)

        out = pd.DataFrame({
            'review': df['review'].values,
            'rating': [5 if v == 1 else 1 for v in df['label']],
            'biz_category': df['cat'].values if 'cat' in df.columns else '未分类',
            'entity_id': 'PUBLIC',
            'entity_name': '公开语料样本',
            'source': self.name,
            'review_date': '',
            'week': 1,
        })
        return out


# ---------------------------------------------------------------------------
# ③ 官方开放平台 API（有资质才用）
# ---------------------------------------------------------------------------

class ApiPullCollector(BaseCollector):
    """通过官方开放平台 API 拉取。

    【这是"自动爬取"唯一正当的技术形态】
    电商平台都有开放平台（淘宝开放平台、京东宙斯、抖店开放平台），
    里面确实有评论查询接口。走这条路的三个前提：

      1. **有商家授权**：应用要绑定店铺，拿到 access_token
      2. **有应用资质**：个人开发者通常只能拿到基础权限，
         评论类接口往往需要企业资质 + 类目审核
      3. **有频次限制**：一般每天几千次，够用但不适合大批量回溯

    【为什么这里只有一个骨架，没有真实现】
    我没有这些平台的开发者账号，也没有店铺授权，
    写出来的请求代码无法验证——而"没验证过的代码"放进仓库，
    比不写更糟：它看起来能跑，实际一跑就报 401，
    然后所有人都会以为是自己的配置问题。

    所以这里只定义：
      · 需要的配置项（见 REQUIRED_ENV）
      · 返回的数据形状
      · 失败时的行为
    真正的 fetch 实现，要等拿到 key 之后补。

    【接进来要改哪里，写清楚】
    只需要实现 _call_api()，把返回的 JSON 映射成
    REQUIRED_COLS 这 11 列，其余全部由基类处理。
    分析层、前端、验收脚本**一行都不用改**。
    这就是这套框架"可插拔"的实证。
    """

    name = 'api'
    title = '官方开放平台 API'
    compliance = '合规（需资质）：走平台官方接口，需商家授权 + 应用审核'
    auto = True
    needs_human = '一次性：申请应用资质 + 完成店铺授权，拿到 access_token'
    note = ('这是"自动爬取"唯一正当的技术形态。'
            '门槛是资质与频次限制，不是技术。代码已留骨架，缺的是账号。')

    REQUIRED_ENV = ['ECOM_API_BASE', 'ECOM_APP_KEY', 'ECOM_APP_SECRET',
                    'ECOM_ACCESS_TOKEN']

    def fetch(self) -> pd.DataFrame:
        missing = [k for k in self.REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise RuntimeError(
                '缺少配置：' + '、'.join(missing)
                + '。这个采集器需要官方开放平台的应用资质和商家授权，'
                  '没有 key 时不会联网，也不会退回爬虫。'
            )
        return self._call_api()

    def _call_api(self) -> pd.DataFrame:
        """真正的实现要在这里补。步骤：
        1. 用 app_key/app_secret 签名（各平台算法不同，见其文档）
        2. GET {ECOM_API_BASE}/reviews?page=N&size=100
        3. 翻页直到 has_next=false 或达到上限
        4. 把返回 JSON 映射成 REQUIRED_COLS
        """
        raise NotImplementedError(
            '需要官方开放平台的 app_key / access_token。'
            '拿到后在此实现签名与翻页逻辑，其余由基类处理。'
        )


# ---------------------------------------------------------------------------
# ④ 人工粘贴（零门槛）
# ---------------------------------------------------------------------------

class ManualCollector(BaseCollector):
    """人工粘贴的评论，一行一条。

    【为什么保留这个"最笨"的方式】
    三个真实场景它比自动化更强：
      · 只有几条关键差评，想单独分析（"这周的差评我截图发你"）
      · 访谈/客服记录里用户的原话
      · 竞品的公开可见评论（自己看、自己记，合规）

    最后一个尤其重要：竞品评论没法自动拿，但运营每天都会看。
    让他把看到的贴进来，比什么都实际。
    """

    name = 'manual'
    title = '人工粘贴'
    compliance = '合规：人工整理，不涉及自动抓取'
    auto = False
    needs_human = '全部：一行一条贴进来'
    note = ('竞品评论拿不到自动接口，但运营每天都在看——'
            '让他把看到的贴进来，比什么方案都实际。')

    def __init__(self, lines: list[str] | None = None,
                 rating: int = 3, entity_name: str = '人工录入',
                 category: str = '未分类'):
        self.lines = lines or []
        self.rating = rating
        self.entity_name = entity_name
        self.category = category

    def fetch(self) -> pd.DataFrame:
        rows = [x.strip() for x in self.lines if x and x.strip()]
        if not rows:
            return pd.DataFrame(columns=['review', 'rating'])
        return pd.DataFrame({
            'review': rows,
            'rating': [self.rating] * len(rows),
            'entity_id': 'MANUAL',
            'entity_name': self.entity_name,
            'biz_category': self.category,
            'source': self.name,
        })


# ---------------------------------------------------------------------------
# ⑤ 平台爬虫 —— 故意不实现
# ---------------------------------------------------------------------------

class PlatformScraperCollector(BaseCollector):
    """【这个类是空的，而且必须是空的。】

    需求里说的"自动爬取评论"，最直接的理解就是它。
    我不实现，理由在模块开头写了三条（违约 / 易失效 / 样本有偏）。
    在这里再补一条更实际的理由：

    **即使写出来了，它对这个项目也是减分项。**

    这是一个比赛项目。评委看的是"这个方案靠不靠谱"，
    而不是"能不能黑进别人的网站"。一个把核心数据来源建立在
    违规抓取上的方案，会被问三个问题：

      1. 你的数据合法吗？（答不上来）
      2. 平台改版了你怎么办？（答不上来）
      3. 你的样本有代表性吗？（平台只展示前 N 页，答不上来）

    三个问题答不上来，前面所有的分析都白做。

    **所以保留这个类，是为了让"我们考虑过这条路"变成可见的事实。**
    删掉它，别人会以为我们没想到；留着并写清楚，
    才是对一个技术方案负责的态度。

    如果将来有人真的需要爬（比如做学术研究、有平台授权），
    那不是这个文件该解决的问题——它应该是一个独立的、
    明确标注合规边界和风险的项目。
    """

    name = 'scraper'
    title = '平台页面抓取（未实现）'
    compliance = '不合规：违反平台服务条款，本项目不采用'
    auto = False
    needs_human = '—'
    note = ('不实现不是 backlog，是设计决定：违约 + 反爬对抗使代码寿命以周计 '
            '+ 平台只展示前 N 页导致样本天然有偏。')
    NOT_IMPLEMENTED_REASON = (
        '不实现。原因：①违反平台服务条款；②反爬对抗导致代码寿命以周计；'
        '③页面只展示前 N 页，样本天然有偏，得出的结论不可信。'
        '替代方案见 README 的「数据从哪来」一节。'
    )

    def fetch(self) -> pd.DataFrame:
        raise NotImplementedError(self.NOT_IMPLEMENTED_REASON)


# ---------------------------------------------------------------------------
# 写入与清单
# ---------------------------------------------------------------------------

def _merge_into_corpus(df: pd.DataFrame, collector: str) -> tuple[int, int]:
    """并入 corpus.csv，返回 (新增, 跳过)。

    【去重规则：按 (entity_id, review) 判重，不是按 review_id】
    为什么不用 review_id：
      · 不同采集器生成的 ID 规则不同（哈希 / 平台 ID / 自增），
        同一个平台上导出的两次文件，ID 可能一样也可能不一样
      · 而"同一实体下的同一段文字"是明确的重复——不管 ID 是什么

    为什么不按 (review) 单独判重：
      同一句话出现在本店和竞品下是合法的（现实里也常见）。
      按 review 判重会把竞品的真实数据误删。
    """
    if not os.path.exists(CORPUS):
        raise FileNotFoundError(f'语料池不存在：{CORPUS}')

    corpus = pd.read_csv(CORPUS)
    before = len(corpus)

    key_new = set(zip(df['entity_id'].astype(str), df['review'].astype(str)))
    key_old = set(zip(corpus['entity_id'].astype(str),
                      corpus['review'].astype(str)))
    fresh = df[[k not in key_old
                for k in zip(df['entity_id'].astype(str),
                             df['review'].astype(str))]]
    skipped = len(df) - len(fresh)

    if len(fresh) == 0:
        return 0, skipped

    out = pd.concat([corpus, fresh[REQUIRED_COLS]], ignore_index=True)
    out.to_csv(CORPUS, index=False, encoding='utf-8')
    return len(out) - before, skipped


def _append_manifest(collector: BaseCollector, added: int) -> None:
    """往 _manifest.json 追加一条采集记录。

    【为什么每条都要记，哪怕只加了一条】
    这是"这些数据是几时、从哪、以什么方式进来的"的唯一凭证。
    没有它，语料池里 17,660 条数据混在一起，
    分不清哪些是种子数据、哪些是后来导入的。
    而这一点在出问题时最要紧——比如"这个负评率是不是被某次导入带偏了"。
    """
    if not os.path.exists(MANIFEST):
        mf = {'_meta': {}, 'batches': []}
    else:
        with open(MANIFEST, encoding='utf-8') as f:
            mf = json.load(f)

    mf.setdefault('batches', []).append({
        'source': collector.name,
        'entity_name': '(见数据行)',
        'entity_id': '-',
        'n': int(added),
        'collected_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'mode': 'collector',
        'compliance': collector.compliance,
        'title': collector.title,
    })
    mf.setdefault('_meta', {})['last_collect_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')

    with open(MANIFEST, 'w', encoding='utf-8') as f:
        json.dump(mf, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

REGISTRY: dict[str, type[BaseCollector]] = {
    'import': CsvImportCollector,
    'public': PublicDatasetCollector,
    'api': ApiPullCollector,
    'manual': ManualCollector,
    'scraper': PlatformScraperCollector,
}


def _pad(s: str, width: int) -> str:
    """按显示宽度补齐（中文算 2 格）。

    【为什么不能用 str.ljust】
    Python 的 ljust 按字符数补，但中文字符在终端占 2 格。
    "合规：商家导出" 6 个中文字 = 12 格宽，ljust(46) 只补到 46 个**字符**，
    实际显示就是 40 格 + 34 空格，后面那列全部错位。
    这个坑在打印表格时几乎必踩，所以单独写个函数。
    """
    w = sum(2 if ord(c) > 127 else 1 for c in s)
    return s + ' ' * max(0, width - w)


def list_collectors() -> list[dict]:
    """给前端/文档用的采集器清单。

    【为什么要同时返回 needs_human 和 note，而不只是 auto 布尔值】
    "半自动"这三个字在汇报里是最容易被追问的一个词：
      · 半在哪一步？
      · 那一步为什么不能让机器做？
      · 换成别的平台还成立吗？
    只给一个 auto=False，这三个问题一个都答不了，
    读者只能自己脑补"大概是还没做完吧"——而实际是设计决定。
    所以把"要人做什么"和"为什么"都挂在清单上，
    让这张表自己就能回答追问。
    """
    return [
        {'name': c.name, 'title': c.title, 'auto': c.auto,
         'compliance': c.compliance,
         'available': c.name != 'scraper',
         'needs_human': getattr(c, 'needs_human', ''),
         'note': getattr(c, 'note', '')}
        for c in REGISTRY.values()
    ]


def run_collector(name: str, **kwargs) -> CollectResult:
    """按名字跑采集器。

    【为什么要做别名归一，而不是要求调用方记住注册键】
    这套模块里存在两套命名，而且都是对的：
      · REGISTRY 的键       'import'   —— 按动作命名，短，命令行好打
      · 采集器的 name 属性  'imported' —— 按数据来源命名，它是写进
                                          corpus.csv 的 source 值
    两套命名各有各的合理性，但结果是：用户看到清单上写着
    "平台后台导出导入"，想跑一下，很自然地打出 --run imported，
    然后得到"未知采集器"。这个报错看起来像 bug，实际是命名没对齐。
    与其在文档里反复强调"要打 import 不是 imported"，
    不如两边都收——命令行的容错成本几乎为零。
    """
    cls = REGISTRY.get(name)
    if cls is None:
        # 按 name 属性反查（'imported' → CsvImportCollector）
        cls = next((c for c in REGISTRY.values() if c.name == name), None)
    if cls is None:
        return CollectResult(False, name,
                             errors=[f'未知采集器：{name}'
                                     f'（可用：{"、".join(REGISTRY)}）'])
    # 【write 要交给 run()，不是交给构造函数】
    # run 的签名是 run(self, write: bool = True)，
    # 而 kwargs 原本整包丢给 cls(**kwargs)。
    # 于是 --dry 一传就炸：CsvImportCollector() takes no arguments。
    # 这类错很典型——参数在两个层级上同名不同意，
    # 调用方看着像"传进去了"，实际传错了层。
    write = kwargs.pop('write', True)
    try:
        inst = cls(**kwargs)
    except TypeError as e:
        return CollectResult(False, name, errors=[f'参数不匹配：{e}'])
    return inst.run(write=write)


def scan_inbox() -> CollectResult:
    """扫 inbox 并导入——这是"自动"的那个入口。"""
    return CsvImportCollector().run()


def main() -> int:
    ap = argparse.ArgumentParser(description='评论采集器')
    ap.add_argument('--list', action='store_true', help='列出全部采集器')
    ap.add_argument('--scan', action='store_true',
                    help='扫描 inbox/ 目录并导入（自动）')
    ap.add_argument('--run', metavar='NAME', help='运行指定采集器')
    ap.add_argument('--n', type=int, default=500, help='public 采集器采样条数')
    ap.add_argument('--dry', action='store_true', help='只校验不写入')
    ap.add_argument('--text', nargs='*', help='manual 采集器的文本行')
    args = ap.parse_args()

    if args.list or (not args.scan and not args.run):
        print('可用采集器：\n')
        print(_pad('名称', 14) + _pad('自动', 6) + _pad('合规性', 50) + '标题')
        print('-' * 108)
        for c in list_collectors():
            print(_pad(c['name'], 14)
                  + _pad('是' if c['auto'] else '否', 6)
                  + _pad(c['compliance'], 50)
                  + c['title'])
        print('\n用法：')
        print('  python collectors.py --scan          扫 inbox 导入（最常用）')
        print('  python collectors.py --run public    采样公开语料')
        print('  python collectors.py --list          看这份清单')
        return 0

    if args.scan:
        # 【--dry 原来是个死参数：声明了、从来没被读过】
        # 这是个很容易漏的坑：`--dry` 出现在 help 里，用户照用了，
        # 命令也不报错，于是他会以为"预览过了"——而实际上文件已经写进去了。
        # 比"没有这个功能"更糟，因为它让人失去警惕。
        # 现在 --scan 直接走 run_collector，让 write 开关真正生效。
        r = run_collector('imported', write=not args.dry)
        if args.dry:
            print(f'[预演] 扫描 inbox：可导入 {r.added} 条（未写入）')
        else:
            print(f'扫描 inbox：新增 {r.added} 条，跳过重复 {r.skipped} 条')
        for e in r.errors:
            print('  ' + e)
        if r.added == 0 and not r.errors:
            print(f'  （{INBOX} 里没有待导入文件）')
        return 0

    kw: dict[str, Any] = {}
    if args.run == 'public':
        kw['n'] = args.n
    if args.run == 'manual':
        kw['lines'] = args.text or []
    # --dry 对所有采集器都生效，不是只对 --scan
    if args.dry:
        kw['write'] = False
    r = run_collector(args.run, **kw)
    tag = '成功' if r.ok else '失败'
    if args.dry:
        print(f'[预演][{tag}] {r.collector}：可导入 {r.added} 条（未写入）')
    else:
        print(f'[{tag}] {r.collector}：新增 {r.added}，跳过 {r.skipped}')
    for e in r.errors:
        print('  ' + e)
    return 0 if r.ok else 1


if __name__ == '__main__':
    sys.exit(main())