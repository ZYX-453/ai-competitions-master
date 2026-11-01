# -*- coding: utf-8 -*-
"""构建统一评论语料池 data/review_pool/corpus.csv

【为什么要有"语料池"这个中间层，而不是直接分析 reviews_monitor.csv】

需求要的是四件事：情感分析、词云、用户画像、竞品对比。
这四件事有一个共同前提：**它们只应该看"平台上真实能看到的东西"**，
不应该看仓库里已经算好的 label 和 cause_l1/l2。

这个前提不是洁癖，是这套东西能不能被信任的分界线：

  · reviews_monitor.csv 里的 label 是规则引擎打的分。实测 13,317/13,500
    （98.6%）的文本来自公开语料 online_shopping_10_cats.csv，label 与
    原始标注 100% 一致。如果情感分析直接读这一列，那"准确率 100%"就是
    自己判自己——这不是验证，这是抄答案。
  · 用户画像如果读 cause_l2，"发现用户最在意尺码"这句话就退化成
    "把已经归好类的字段数了一遍"，那也不叫画像。

所以 corpus 只保留**平台上肉眼可见的字段**：
文本、星级、时间、类目、有没有图。
label 与归因结果另存 _corpus_truth.csv，只有审计脚本读，分析层读不到。

【第二个作用：让"采集"有地方落脚】

collectors.py 往语料池里灌数据，分析层从语料池读数据，两边互不知道对方存在。
换一个数据来源（平台导出、公开数据集、人工粘贴），分析层一行都不用改。
这是"可插拔"这四个字唯一有意义的实现方式。

【字段口径统一到什么程度】

本店表有 18 列、竞品表有 14 列，交集只有一半。统一后保留 11 列。
刻意**不统一**的两列：

  · price —— 本店有真实成交价，竞品只有 price_index（相对倍数）。
    硬凑成一列等于把"推算"和"实测"混在同一个字段里，
    下游任何按价格分组的分析都会失真。所以这一列直接不要，
    价格敏感度改从文本证据推（"太贵了/不值/性价比高"）。
  · order_returned —— 只有本店有退货标记。放进 corpus 会让
    "这个差评有没有导致退货"变成一个看起来人人都有的字段，
    实际上竞品那边全是空。
"""
from __future__ import annotations

import json
import os
import shutil
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROC = os.path.join(BASE, 'data', 'processed')
OUT_DIR = os.path.join(BASE, 'data', 'review_pool')

# 公开可见字段：分析层允许看到的全部内容
PUB_COLS = [
    'review_id', 'source', 'entity_id', 'entity_name', 'biz_category',
    'review', 'rating', 'week', 'review_date', 'review_len', 'has_image',
]

# 审计专用字段：分析层禁用
TRUTH_COLS = ['review_id', 'label', 'cause_l1', 'cause_l2']


def _clean_text(s: pd.Series) -> pd.Series:
    """评论文本清洗。

    【BOM 必须去掉】
    这份语料的原始文件是带 BOM 的，pandas 读进来第一行文本前面挂着 \\ufeff。
    后果不是显示问题，是**去重失效**：同一句话带不带 BOM 是两个不同的字符串，
    做 in 匹配、做 groupby、做竞品对比时都会各算一份。
    实测本店与公开语料按原文匹配只对上 13,317 条，清掉 BOM 后仍然是
    13,317 —— 说明这一类脏数据在这份语料里恰好只有首行一条，
    但换一份数据就未必了，所以清洗要写成默认动作。
    """
    return (s.astype(str)
            .str.replace('\ufeff', '', regex=False)
            .str.replace('\u200b', '', regex=False)
            .str.strip())


def load_store() -> tuple[pd.DataFrame, pd.DataFrame]:
    """本店评论。label 与归因列拆到 truth 里。"""
    df = pd.read_csv(os.path.join(PROC, 'reviews_monitor.csv'))
    df['review'] = _clean_text(df['review'])

    pub = pd.DataFrame({
        'review_id': df['review_id'],
        'source': 'store',
        'entity_id': 'STORE',
        'entity_name': '本店',
        'biz_category': df['biz_category'],
        'review': df['review'],
        'rating': df['rating'].astype(int),
        'week': df['week'].astype(int),
        'review_date': df['review_date'],
        'review_len': df['review'].str.len().astype(int),
        'has_image': df['has_image'].astype(int),
    })
    truth = df[TRUTH_COLS].copy()
    return pub, truth


def load_competitor() -> tuple[pd.DataFrame, pd.DataFrame]:
    """竞品评论。comp_id 换成可读名。"""
    df = pd.read_csv(os.path.join(PROC, 'reviews_competitor.csv'))
    df['review'] = _clean_text(df['review'])

    master = pd.read_csv(os.path.join(PROC, 'competitor_master.csv'))
    name_map = (master.drop_duplicates('comp_id')
                .set_index('comp_id')['comp_name'].to_dict())

    pub = pd.DataFrame({
        'review_id': df['review_id'],
        'source': 'competitor',
        'entity_id': df['comp_id'],
        'entity_name': df['comp_id'].map(name_map).fillna(df['comp_id']),
        'biz_category': df['biz_category'],
        'review': df['review'],
        'rating': df['rating'].astype(int),
        'week': df['week'].astype(int),
        'review_date': df['review_date'],
        'review_len': df['review'].str.len().astype(int),
        'has_image': df['has_image'].astype(int),
    })
    truth = df[TRUTH_COLS].copy()
    return pub, truth


def write_manifest(pub: pd.DataFrame) -> dict:
    """记录每一批数据是怎么来的。

    【为什么采集记录要和数据分开放】
    corpus.csv 是"分析层看到的全部"，它必须干净；
    manifest 是"这些行是从哪来的、合不合规、什么时候进来"，
    它是给人看的东西。两者混在一起，就会出现
    "一句话后面跟着一条合规声明"这种没法分析也不像报告的结构。

    没有这份记录，"自动采集"就只是个说法——
    你没法回答"这 17,660 条是几时进来的、来自几个源"。
    """
    rows = []
    for (src, ent), g in pub.groupby(['source', 'entity_name'], sort=True):
        rows.append({
            'source': src,
            'entity_name': ent,
            'entity_id': g['entity_id'].iloc[0],
            'n': int(len(g)),
            'week_min': int(g['week'].min()),
            'week_max': int(g['week'].max()),
            'date_min': str(g['review_date'].min()),
            'date_max': str(g['review_date'].max()),
            'collected_at': '2026-10-30T00:00:00',
            'mode': 'batch_seed',          # 种子批：随数据集一起生成
            'compliance': ('本店自有监控数据' if src == 'store'
                           else '模拟竞品池（DGP 标定，非实时抓取）'),
        })

    return {
        '_meta': {
            'purpose': ('统一评论语料池的来源台账。分析层不读这个文件，'
                        '它只用于回答"这些数据是哪来的"。'),
            'total': int(len(pub)),
            'sources': sorted(pub['source'].unique().tolist()),
            'built_at': '2026-11-01',
            'field_policy': {
                'public': PUB_COLS,
                'audit_only': TRUTH_COLS,
                'excluded': {
                    'price': '本店为实测成交价、竞品只有相对倍数，混成一列会失真',
                    'order_returned': '仅本店有，放进公共表会造成"人人都有"的错觉',
                },
            },
        },
        'batches': rows,
    }


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)

    store_pub, store_truth = load_store()
    comp_pub, comp_truth = load_competitor()

    pub = pd.concat([store_pub, comp_pub], ignore_index=True)
    truth = pd.concat([store_truth, comp_truth], ignore_index=True)

    # ID 唯一性：两张表的 ID 前缀不同（M / C），但导入数据可能撞车，
    # 所以这里做一次硬校验——重复 ID 会让"去重"和"关联"全部失效，
    # 而且错得很安静（groupby 会把两条合成一条）。
    dup = pub['review_id'].duplicated()
    if dup.any():
        bad = pub.loc[dup, 'review_id'].head(5).tolist()
        raise SystemExit(f'review_id 重复：{bad}')

    # 文本去重检查：不删，只报。同一条文本出现在不同实体下是合法的
    # （竞品池本身就刻意复用模板），但出现在同一实体下就是重复采集。
    same_ent_dup = pub.duplicated(['entity_id', 'review']).sum()

    keep = [c for c in PUB_COLS if c in pub.columns]
    pub = pub[keep]

    # 【pub 和 truth 的排序必须同步做，否则行号会错位】
    # 第一版只排了 pub：
    #     pub = pub.sort_values([...]).reset_index(drop=True)
    #     truth 保持原始顺序没动。
    # 于是 pub 第 i 行和 truth 第 i 行再也不是同一条评论。
    # 症状特别隐蔽——报表照常出，负评率照常有，只是全错：
    #     竞品C 真值 38.4%，打印出来是 3.9%（其实是别家的数）
    #     本店  真值  4.6%，打印出来是 10.2%
    # 而且错得很像"合理的业务结论"，没人会怀疑。
    #
    # 修法：把排序键单独抽出来，pub 和 truth 用同一把尺子取行号，
    # 而不是各自 reset_index 之后假设两边还对齐。
    order = np.lexsort((pub['week'].values, pub['entity_id'].values,
                        pub['source'].values))
    pub = pub.iloc[order].reset_index(drop=True)
    truth = truth.iloc[order].reset_index(drop=True)

    # 排序之后再做一次硬校验：如果行号错位，这里必须炸，而不是继续往下跑
    if not (pub['review_id'].values == truth['review_id'].values).all():
        raise SystemExit('语料池与真值表行顺序不一致，拒绝写出（会导致审计数字全错）')

    corpus_path = os.path.join(OUT_DIR, 'corpus.csv')
    truth_path = os.path.join(OUT_DIR, '_corpus_truth.csv')
    pub.to_csv(corpus_path, index=False, encoding='utf-8')
    truth.to_csv(truth_path, index=False, encoding='utf-8')

    manifest = write_manifest(pub)
    manifest['_meta']['same_entity_duplicated_text'] = int(same_ent_dup)
    with open(os.path.join(OUT_DIR, '_manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # 空的 inbox 目录：collectors 往里丢文件，自动导入
    inbox = os.path.join(OUT_DIR, 'inbox')
    os.makedirs(inbox, exist_ok=True)
    readme = os.path.join(inbox, 'README.md')
    if not os.path.exists(readme):
        with open(readme, 'w', encoding='utf-8') as f:
            f.write(
                '# 评论导入箱\n\n'
                '把平台后台导出的评论文件（CSV / XLSX）放进这个目录，\n'
                '然后运行 `python agent/backend/tools/collectors.py --scan`，\n'
                '文件就会被解析、去重、并入语料池。\n\n'
                '需要的列（列名可以是中文，会自动映射）：\n\n'
                '| 必需 | 说明 | 常见叫法 |\n'
                '|---|---|---|\n'
                '| 是 | 评论文本 | 评价内容 / 评论内容 / content / review |\n'
                '| 是 | 星级 | 评分 / 星级 / rating / score |\n'
                '| 否 | 时间 | 评价时间 / 下单时间 / date |\n'
                '| 否 | 类目 | 商品分类 / 类目 / category |\n'
                '| 否 | 商品 ID | 商品编号 / sku / item_id |\n\n'
                '导入后会写入 `imported` 来源，并在 `_manifest.json` 里留一条记录。\n'
                '原始文件会被移到 `inbox/done/`，不会重复导入。\n'
            )

    print(f'语料池      {len(pub):,} 行 -> corpus.csv')
    print(f'审计真值    {len(truth):,} 行 -> _corpus_truth.csv（分析层禁用）')
    print()
    print('各来源构成：')
    for (src, ent), g in pub.groupby(['source', 'entity_name'], sort=True):
        neg = (truth.loc[g.index, 'label'] == 0).mean()
        print(f'  {ent:18s} {src:11s} {len(g):5,d} 条   负评 {neg*100:5.1f}%')
    if same_ent_dup:
        print(f'\n注意：同一实体下重复文本 {same_ent_dup:,} 条'
              f'（竞品池模板复用属预期，本店不为 0 才是问题）')
    print(f'\n字段口径：公开 {len(PUB_COLS)} 列 / 审计专用 {len(TRUTH_COLS)} 列'
          f'（label、归因结果已隔离）')
    return 0


if __name__ == '__main__':
    sys.exit(main())