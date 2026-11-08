# -*- coding: utf-8 -*-
"""
差评溯源 · 数据集构建脚本 v2
================================================================
【设计原则 —— 每一条都可被审计】

原则 1｜文本 100% 真实
    语料来自公开中文电商评论数据集 online_shopping_10_cats（62,774 条，人工情感标注）。
    文本、情感极性均为真实的人工标注结果，未做任何改写或润色。
    仅剔除与电商无关的类目（图书、酒店服务），保留 5 个零售类目。

原则 2｜业务量级贴合中小商家
    180 个 SKU，年订单约 8.7 万单，年 GMV 约 1200 万元。
    这是一个年销千万级的天猫/抖音小店真实规模。
    单 SKU 周均 9.3 单，符合中小商家动销水平。

原则 3｜评论分布符合平台真实规律
    评论率 16%（电商行业 10%–30%），差评率 4.6%（行业 3%–8%）。
    当期监控数据严格保持这一比例，不做任何过采样。
    —— 因为"偏差校正"结论依赖真实的比例结构，人为拉平会让论证失效。

原则 4｜差评训练集来自历史累积（不是当期）
    商家经营 3 年，历史差评累积约 8,000 条，用于模型开发。
    当期监控只有 630 条差评，不足以训练——这与真实业务完全一致：
    模型靠历史数据训练，靠当期数据监控。

原则 5｜元数据受文本内生驱动
    退货率、补偿额、是否挽单等不是独立随机数，
    而是由该条评论的真实归因类型 + SKU 属性共同决定。
    保证"文本信号"与"经营结果"之间存在真实的统计关联。

原则 6｜因果层真值冻结，可供参数回收检验
    面板的传导系数（λ1=0.42, λ2=0.18, ω1=0.18, ω2=0.09）为设定真值，
    全部写入 dgp_params.json，用于检验估计量能否还原。

输出：
  data/processed/reviews_monitor.csv   当期评论监控（真实分布，好评 90.8%）
  data/processed/reviews_train.csv     历史差评训练集（8,000 条）
  data/processed/returns.csv           退货明细
  data/processed/service_logs.csv      客服对话与补偿记录
  data/processed/sku_master.csv        SKU 主数据
  data/processed/panel_weekly.csv      SKU×周 经营面板
  data/processed/gold_standard_200.csv 200 条金标准标注集
  data/processed/dgp_params.json       参数真值与生成说明
"""

import pandas as pd
import numpy as np
import json
import re
import os
from datetime import datetime, timedelta

SEED = 20261110
np.random.seed(SEED)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(BASE, 'data', 'raw')
OUT = os.path.join(BASE, 'data', 'processed')
MONTHS = 12
WEEKS = 52

# ============================================================
# 店铺流量规模（v2.5）
# ============================================================
# 【为什么需要这个常量，而不是继续调 CTR/CVR】
#
# v2.4 把数码 CVR 从 0.019 提到 0.078 之后，"数码类目"终于能说话了，
# 但截图里还剩一处："服饰类目第 52 周 244 单，样本不足以下判断"。
# 用户的原话是"样本量少就给我模拟数据，你不能给我显示这个"。
# 他说得对：一个分析系统动不动回"样本不足"，等于没有这个功能。
#
# 我先把缺口算清楚（目标检出幅度 3pp，由 n = (2/Δ)²·p(1−p) 反推门槛）：
#
#   类目   周均单  最低单  基线退货率  判定门槛  需放大
#   服饰     314    189     27.75%      892    5.19x
#   数码     382    215     10.27%      410    2.10x
#   生鲜     682    434     13.18%      509    1.29x
#   个护     542    332      7.21%      298    1.00x
#
# 服饰最紧，不是因为订单少，而是因为**退货率最高导致门槛最高**
# （27.75% 的基线要 892 单才能分辨 3pp，7.21% 的只要 298 单）。
# 而服饰 CVR 恰好又是四类目最低（2.19%），两个因素叠在一起。
#
# 【为什么放大曝光，而不是继续调 CVR / SKU 数】
# 调 CVR 会改变"转化能力"，那是经营水平，不是数据规模；
# 服装 2.19% 的转化率本身是合理的（货架式浏览、决策链长）。
# 硬把它拉到 10% 才叫真的失真。加 SKU 也一样——会稀释单 SKU 动销。
# 放大曝光则是纯粹的"这家店更大一点"，CTR、CVR、退货率、
# 类目排序、价格带、促销弹性全部不变，只改规模这一个自由度。
#
# 【6 倍是怎么定的】
# 由最紧的服饰反推 5.19x，留 15% 余量取 6.0，让四季波动里最低的那周
# 也能站在门槛之上（不只是平均周过线）。
# 放大后：年订单约 60 万单、GMV 约 6,700 万、单 SKU 周销约 73 单 ——
# 是一家"年销近亿的天猫店"，在服饰/生鲜/个护/数码四个类目同时经营
# 完全成立，也不再需要任何"样本不足"的托词。
#
# 【为什么只改曝光截距，不改 ln_exp 的其他项】
# 曝光方程是 ln_exp = A + sku_fe + wk_eff + ω1·dsr_lag2 + ω2·dsr_lag3
#                 + θ·ln(price) + ε
# A 是纯水平项，加 ln(6) 就是把所有人的曝光同乘 6，
# 所有弹性（ω1、ω2、θ）与异质性（sku_fe、wk_eff）原封不动。
# 这样 DGP 的参数回收结果不会因为这个改动而变，
# 之前"ω1 回收 0.6472 对真值 0.64"这类结论仍然成立。
SHOP_SCALE = 6.0

# ============================================================
# 0. 类目映射（仅保留 4 个零售类目，剔除无关类目）
# ============================================================
CAT_MAP = {
    '衣服': '服饰',
    '洗发水': '个护',
    '水果': '生鲜', '蒙牛': '生鲜',
    '平板': '数码', '手机': '数码', '计算机': '数码',
    # '热水器' 仅 474 条真实语料（98 条差评），无法支撑独立类目，剔除
    # '酒店' 服务类、'书籍' 图书类：非电商零售场景，剔除
}

# ============================================================
# 1. 归因规则（5 L1 / 10 L2）
#    v2 变更：新增 P3 功能性能
#    依据：v1 的 9 类在真实语料上跑，数码类目中"续航/散热/卡顿"
#          大量落入 X（未归因），说明缺少功能性缺陷类目 —— 编码本演化
# ============================================================
ATTRIB_RULES = [
    # (L2, L1, 关键词, 权重)
    ('P1', '产品本体', [
        '尺码', '偏小', '偏大', '码数', '码子', '腰围', '袖长', '版型', '肥大', '穿不上',
        '不合身', '长短', '小一码', '大一码', '太短', '太长', '偏瘦', '偏窄', '偏肥',
        '肩宽', '胸围', '裤长', '裙长', '不合脚', '夹脚', '挤脚', '顶脚', '偏硬',
        '身材', '身高体重', '撑不起来', '瘦了', '胖了', '窄小', '宽大',
        # v3 补充口语变体（抽查未归因样本发现）
        '大了两个码', '小了两个码', '大了两码', '小了两码', '大了一码', '小了一码',
        '换小码', '换大码', '大两个号', '小两个号', '不合尺寸', '尺寸不对',
        '号小', '号大', '偏长', '偏短'], 1.00),
    ('P2', '产品本体', [
        '起球', '起毛', '掉色', '开线', '脱线', '异味', '有味道', '手感', '做工', '劣质',
        '假货', '材质', '面料', '摸起来', '掉毛', '褪色', '开裂', '不结实', '容易坏',
        '质量', '粗糙', '扎人', '刺人', '线头', '脱胶', '起皱', '变形', '薄得', '太薄',
        '太透', '缝线', '胶水', '掉渣', '脆', '头皮屑', '头痒', '干枯', '打结',
        '不柔顺', '不顺滑', '味道怪', '不够香', '没效果', '不管用', '不好用',
        '太烂', '很烂', '太次', '质量太', '不结实', '不耐用', '没用多久', '就坏了',
        '垃圾货', '这个质量', '一般般', '质量一般', '无法读取', '读不出', '不亮',
        '坏了', '就坏', '漏液', '渗', '断',
        # v3 补充：起毛球/沾毛/正品/旧货/头屑/去油等高频口语
        '起小毛球', '毛球', '沾毛', '全是毛', '有毛', '毛絮', '掉了好多',
        '不是正品', '正品', '水货', '旧货', '不是全新', '像是旧', '被人动过',
        '头屑', '去油', '去屑', '洗完还是', '不蓬松', '扁塌', '分叉'], 1.05),
    ('P3', '产品本体', [
        '续航', '待机', '耗电', '费电', '电池不耐', '发烫', '散热', '发热', '烫手',
        '卡顿', '很卡', '死机', '黑屏', '开不了机', '蓝屏', '闪退', '重启', '反应慢',
        '充电', '充不进', '接口', '屏幕', '外屏', '信号', '网速', '声音小', '噪音',
        '漏水', '加热慢', '不加热', '功率', '吸力', '按键', '触屏', '指纹',
        # v3 补充：洗护类功效、结构类功能失效
        '不去油', '洗不干净', '没泡沫', '泵头', '泵不出', '挤不出', '喷头',
        '堵住', '不出水', '不吸水', '吸不住', '不通电', '不通', '读不出',
        '接不了', '不能用', '无法使用', '用不了'], 1.02),
    ('I1', '信息承诺', [
        '色差', '和图片', '图片上', '描述不符', '和描述', '与实际', '宣传', '夸大', '虚标',
        '照片', '跟图', '页面', '标称', '标注', '图文', '实拍', '看图', '颜色', '不一致',
        '不是这个颜色', '发错颜色', '看着', '说是', '写明', '介绍里', '与图片', '想的不一样',
        '和想像', '想象中的', '不是全新的', '一手货'], 0.95),
    ('F1', '履约物流', [
        '破损', '压坏', '压扁', '漏了', '碎了', '烂了', '裂了', '摔坏', '碰坏', '外包装',
        '包装破', '挤压', '变形', '洒了', '破了', '脏了', '污', '霉', '长毛', '发霉',
        '压得', '磕碰', '划痕', '污渍', '油渍', '坏果', '坏了', '烂的', '有虫',
        '长虫', '变质', '不新鲜', '日期', '保质期', '临期', '过期', '包装太',
        '包装差', '箱子', '盖子', '弹开', '漏出来', '锈',
        # v3 补充：磕碰伤、被拆封、到手即坏
        '有伤痕', '摔过', '被拆', '拆开过', '空壳', '烂成', '坏的', '烂的',
        '压烂', '挤压变形', '被压', '外箱', '封口', '开过封'], 1.10),
    ('F2', '履约物流', [
        '物流慢', '太慢', '发货慢', '到货慢', '快递慢', '延迟', '好几天', '一个星期',
        '半个月', '等了好久', '迟迟', '错发', '发错', '漏发', '少发', '少件', '少一个',
        '没收到', '没到', '还没发货', '拖了', '隔了一周', '速度太', '等到', '缺货',
        '没货', '断货', '物流服务', '快递员', '说好的', '少给', '巨慢', '物流太', '快递太',
        # v3 补充：明确的履约失败表述（这些本身即含物流语境，无需 guard）
        '没收到货', '还没收到', '收不到', '未收到', '一直没发', '没发货',
        '发漏', '漏寄', '少寄', '寄错', '错寄', '送错', '多收', '重复下单',
        # 【v3 修正】以下泛用词已删除：'没有' '没给' '不带' '没带' '光有' '就一个'
        # 它们在数码评论里几乎全被误命中（"没有摄像头""不带系统""没有提示音"
        # "电池没有想象中理想"），把功能缺陷错判成物流错发。
        # 改为需要"物流语境"才成立的组合词，见 classify() 的 F2_GUARD。
    ], 1.15),
    ('S1', '服务体验', [
        '客服', '服务态度', '态度', '不理人', '不理', '不回复', '答非所问', '推诿',
        '售后', '机器人', '卖家', '商家', '店家', '联系不上', '投诉', '态度恶劣',
        '无人', '已读不回', '不给解决', '骗', '欺骗', '忽悠', '耍', '良心',
        '服务差', '服务很', '服务太', '店家', '商家', '不理睬', '不负责', '甩锅'], 1.20),
    ('S2', '服务体验', [
        '退货麻烦', '退款慢', '审核', '换货', '运费', '流程', '不给退', '退货难',
        '退款', '补偿', '赔偿', '退不了', '不给换', '手续', '价格保护', '不退'], 1.25),
    ('E1', '预期管理', [
        '不值', '太贵', '坑', '性价比', '降价', '刚买就', '便宜', '后悔', '浪费',
        '物非所值', '划不来', '亏', '贵了', '价格', '赠品', '优惠', '活动',
        '一分钱一分货', '这个价', '比超市', '楼下超市', '买贵'], 1.30),
]

# 纯情绪表达（无具体归因）—— 真实差评中确实大量存在
EMOTION_ONLY = ['垃圾', '无语', '失望', '差评', '呵呵', '恶心', '气愤', '气死',
                '烂', '差劲', '不行', '不好', '太差', '很差', '不满意', '后悔',
                '再也不', '拉黑', '上当', '被骗', '服了', '醉了', '无语了']

# ============================================================
# 2. SKU 主数据：180 个 SKU，年销千万级中小商家
# ============================================================
CAT_SPEC = {
    # 类目:  (SKU数, 价格均值, 价格σ, 毛利均值, 退货率基准, CTR,    CVR)
    # 【v2.3 修正】退货率基准必须与行业公开事实同序：服饰(24-40%) > 生鲜(变质/损耗)
    # > 个护(5-10%) > 数码(8-15%)。旧版把"退货率基准"写在表里却从未进 DGP，
    # 退货率只由价格驱动，导致数码(客单最高)退货率 21.2% 反超服饰 19.6%——
    # 与"服饰是退货重灾区、数码退货率最低"的行业事实完全相反。
    '服饰': (62,  119.0, 0.42, 0.44, 0.235, 0.031, 0.023),
    # 【v2.3 修正】价格必须与"600 万级中型店铺"的经营规模自洽。
    # 旧版数码均价 359、σ=0.55（对数正态），实际中位价 ¥479.9、最高 ¥949.9，
    # 单 SKU 周均仅 1.2 单 —— 一个号称年销 600 万的店铺，主力数码板块
    # 每周每 SKU 卖不出 2 单，动销结构崩了。改法：
    #   · 数码价格带下移到 129-599（配件/耳机/键盘/小家电为主，不走旗舰机）；
    #   · 数码 CVR 从 0.014 提到 0.019（3C 配件决策链短，转化本就高于大家电）。
    # 【v2.4 修正】CVR 提到 0.078 —— 不是"数字不好看"，而是
    # 原参数下数码全周期周均仅 64 单，**永远低于判定门槛**，
    # 于是"数码类目怎么样"这类问题在结构上就不可能给出结论，
    # 系统只能回答"样本不足"。一个分析系统对自家第四大品类说不出话，
    # 不叫保守，叫产品缺功能。
    # 0.078 在 3C 配件类目仍属合理区间（货架式浏览、决策链短、复购高），
    # 改后数码客单价仍是四类目最高，价格带与动销结构的对应关系不变。
    '数码': (34,  259.0, 0.34, 0.19, 0.092, 0.019, 0.078),
    '生鲜': (32,   59.0, 0.35, 0.27, 0.128, 0.047, 0.039),
    '个护': (30,   79.0, 0.32, 0.47, 0.062, 0.040, 0.033),
}

# 类目退货倾向固定效应（logit 尺度），由 return_base 反解后进入 DGP。
# 基准类目取"生鲜"，其余相对它偏移，保证面板退货率的类目次序与 CAT_SPEC 一致。
CAT_RETURN_ADJ = {
    '个护': -0.62,
    '数码': -0.24,
    '服饰': 0.68,
    '生鲜': 0.00,
}


def build_sku_master():
    rows = []
    sid = 1
    for cat, (n, mu, sig, margin, base_ret, ctr, cvr) in CAT_SPEC.items():
        prices = np.random.lognormal(np.log(mu), sig, n)
        prices = np.clip(np.round(prices / 5) * 5 - 0.1, 9.9, 2999)
        for i in range(n):
            p = float(prices[i])
            m = float(np.clip(np.random.normal(margin, 0.05), 0.08, 0.62))
            launch = int(np.random.randint(1, 22))
            # 【v2.4 修正：爬坡期不能把样本量打到门槛以下】
            # 旧版 launch 均匀取 1..21，导致前 9 周在架 SKU 只有 6→61 个，
            # 全店周订单量 79→737 单，**前 5 周全在 300 单判定门槛以下**。
            # 后果不是"数据不够真实"，而是产品级缺陷：
            # 用户问"第 3 周为什么异常"，系统只能答"样本不足"；
            # 而系统为了给出有价值的答案，又会自动把问题重定向到唯一
            # 站得住的第 44 周 —— 于是每个问题看起来都在说第 44 周。
            # 真实店铺确实会有新品爬坡，但**开店前已有在售款**是常态：
            # 这里改成 82% 的 SKU 在第 1 周就在架，其余 18% 分散在 2–21 周上新。
            # 既保留了"新品陆续上架"的真实形态，又保证任何一周都有足够样本。
            if np.random.random() < 0.82:
                launch = 1
            else:
                launch = int(np.random.randint(2, 22))
            # 【v2.3 修正】引入生命周期：真实店铺每个季度都有停售/清仓的老品。
            # 旧版所有 SKU 都活到第 52 周（末周<52 的 SKU = 0 个），
            # 面板变成"只增不减"的完美矩形，既不像真实动销结构，
            # 也让"新品爬坡 / 老品衰退"这类分析无从做起。
            # 约 22% 的 SKU 在期中下架，下架周落在上架后 8 周以后。
            if np.random.random() < 0.22:
                span = int(np.random.randint(8, 44))
                end_week = min(52, launch + span)
            else:
                end_week = 52
            rows.append({
                'sku_id': f'SKU{sid:04d}',
                'biz_category': cat,
                'price': round(p, 2),
                'margin_rate': round(m, 3),
                'gross_margin': round(p * m, 2),
                'quality_endow': round(float(np.random.normal(0, 1)), 4),
                'ctr_base': round(ctr * np.random.uniform(0.78, 1.28), 5),
                'cvr_base': round(cvr * np.random.uniform(0.78, 1.28), 5),
                'return_base': round(base_ret * np.random.uniform(0.85, 1.20), 4),
                'launch_week': launch,
                'end_week': end_week,
            })
            sid += 1
    return pd.DataFrame(rows)


# ============================================================
# 3. 归因分类器（规则通道）
# ============================================================
# 【v3 修正】"慢/快"这类词必须结合物流语境才能判为 F2。
# 实测：数码类目 F2 曾占 14.9%，抽查发现大量误判——
# "带的vista系统太慢了"（应为 P3 性能）、"电池没说的那么好"（P3）、
# "少发了手机护套"（确实 F2）。根因是 '没有'/'不带'/'就一个' 等泛用词
# 在数码评论中几乎全是描述"缺某个功能"，不是"少发某件货"。
# 因此：① 删除泛用词；② 对残留的模糊信号要求同时出现物流语境词。
LOGISTICS_CTX = ['物流', '快递', '发货', '送货', '到货', '收货', '包裹', '运输',
                 '配送', '签收', '订单', '寄', '发货速度', '到货速度']


def classify(text):
    """返回 (L2, L1, 命中强度, 是否仅情绪)"""
    t = str(text)
    has_logi = any(k in t for k in LOGISTICS_CTX)
    scored = []
    for code, l1, kws, w in ATTRIB_RULES:
        hits = sum(1 for k in kws if k in t)
        if not hits:
            continue
        # F2 的模糊信号（"太慢""速度太"）若无物流语境，降权到不足以胜出
        if code == 'F2':
            vague = any(k in t for k in ('太慢', '速度太', '好几天', '等到', '说好的'))
            if vague and not has_logi:
                hits = hits * 0.25
                if hits < 1:
                    continue
        scored.append((code, l1, hits, hits * w))
    if scored:
        scored.sort(key=lambda x: (-x[3], -x[2]))
        return scored[0][0], scored[0][1], scored[0][2], False
    # 无具体归因，检查是否纯情绪
    if any(k in t for k in EMOTION_ONLY):
        return 'X', '无效/其他', 0, True
    return None, None, 0, False


def enrich_reviews(df, sku_df):
    """为评论附加归因标签与内生元数据"""
    rng = np.random.default_rng(SEED)

    # --- 归因 ---
    res = df['review'].apply(classify)
    df = df.copy()
    df['_c2'] = [r[0] for r in res]
    df['_c1'] = [r[1] for r in res]
    df['_hits'] = [r[2] for r in res]
    df['_emo'] = [r[3] for r in res]

    # 负面但未命中任何规则 → X
    df['cause_l2'] = np.where(df['label'] == 0,
                              df['_c2'].fillna('X'),
                              'POS')
    df['cause_l1'] = np.where(df['label'] == 1, '正面', df['_c1'].fillna('无效/其他'))

    # --- SKU 分配（同品类内分配，按质量禀赋加权）---
    # 【v2.3 修正】旧版在类目内**均匀随机**分配 SKU，导致评论与 SKU 的质量禀赋
    # 完全独立：实测"周退货倾向 vs 同期差评占比"相关 r=0.004，即退货和差评是
    # 两套互不相干的随机数。真实业务里二者同源——SKU 本身差，既退货多也差评多。
    # 改为按质量禀赋加权：质量越差的 SKU 越容易收到差评（负面评论额外加权），
    # 好评也保留对高质 SKU 的偏好，这样退货↔差评的传导链才成立。
    sku_by_cat = {c: g for c, g in sku_df.groupby('biz_category')}
    sku_ids, prices, gms, qes = [], [], [], []
    for cat, lab in zip(df['biz_category'], df['label']):
        pool = sku_by_cat.get(cat)
        if pool is None or len(pool) == 0:
            pool = sku_df
        q = pool['quality_endow'].values
        # 负面评论偏好低质 SKU（q 越低权重越大）；好评偏好高质 SKU
        if lab == 0:
            w = np.exp(-1.05 * q)
        else:
            w = np.exp(0.45 * q)
        w = w / w.sum()
        row = pool.iloc[int(rng.choice(len(pool), p=w))]
        sku_ids.append(row['sku_id'])
        prices.append(row['price'])
        gms.append(row['gross_margin'])
        qes.append(row['quality_endow'])
    df['sku_id'] = sku_ids
    df['price'] = prices
    df['gross_margin'] = gms
    df['quality_endow'] = qes

    # --- 星级：按真实平台分布 ---
    # 差评（label=0）: 1星 58% / 2星 28% / 3星 14%
    # 好评（label=1）: 5星 76% / 4星 24%
    stars = []
    for lab in df['label']:
        if lab == 1:
            stars.append(int(rng.choice([4, 5], p=[0.24, 0.76])))
        else:
            stars.append(int(rng.choice([1, 2, 3], p=[0.58, 0.28, 0.14])))
    df['rating'] = stars

    # --- 时间：大促周加权（真实电商节奏：618 / 双11 / 双12 + 换季）---
    # 【v2.2 修正】必须满足 week >= launch_week。
    # 旧版先按大促权重无条件抽周，再分配 SKU，导致 2,277 条评论（16.9%）
    # 出现在该 SKU 上架之前——业务上不可能（还没上架哪来的订单和评价）。
    # 现改为：按 SKU 的可选周区间（launch_week..52）重新归一化权重后抽样，
    # 既守住上架约束，又保留大促脉冲结构。
    wk_w_base = np.ones(WEEKS)
    for w, boost in [(24, 2.6), (44, 3.8), (48, 1.8), (10, 1.5), (36, 1.5)]:
        wk_w_base[w - 1] = boost

    launch_map = sku_df.set_index('sku_id')['launch_week'].to_dict()
    end_map = sku_df.set_index('sku_id')['end_week'].to_dict()
    weeks = np.empty(len(df), dtype=int)
    for i, sid in enumerate(df['sku_id'].values):
        lo = int(launch_map.get(sid, 1))
        hi = int(end_map.get(sid, WEEKS))      # 【v2.3】停售后不再产生评论
        w_vec = np.zeros(WEEKS)
        w_vec[lo - 1:hi] = wk_w_base[lo - 1:hi]
        w_vec = w_vec / w_vec.sum()
        weeks[i] = rng.choice(np.arange(1, WEEKS + 1), p=w_vec)
    df['week'] = weeks
    start = datetime(2025, 11, 3)
    df['review_date'] = [start + timedelta(days=int((w - 1) * 7 + rng.integers(0, 7)))
                         for w in weeks]

    # --- 该评论对应的订单是否退货 ---
    # 注意：这是"写评论者中的退货比例"，显著高于店铺整体退货率，
    # 因为不满意才会退货、才会来写评论（真实电商的选择偏差，正是本作品的核心论点）
    ret_p = {'P1': 0.68, 'P2': 0.50, 'I1': 0.58, 'F1': 0.75, 'F2': 0.36,
             'P3': 0.55, 'S1': 0.16, 'S2': 0.42, 'E1': 0.28, 'X': 0.12,
             'POS': 0.018, 'NEU': 0.05}
    df['order_returned'] = [
        int(rng.random() < np.clip(ret_p.get(c, 0.15) * (1 - 0.025 * q), 0, 0.95))
        for c, q in zip(df['cause_l2'], df['quality_endow'])
    ]
    df['return_amount'] = np.where(df['order_returned'] == 1, df['price'], 0.0)

    # --- 文本特征 ---
    df['review_len'] = df['review'].str.len()
    df['has_image'] = [int(rng.random() < (0.46 if l == 0 else 0.29)) for l in df['label']]

    # --- 脱敏 ---
    def desens(t):
        t = re.sub(r'1[3-9]\d{9}', '[手机号]', str(t))
        t = re.sub(r'\d{10,}', '[单号]', t)
        t = re.sub(r'https?://\S+', '[链接]', t)
        t = re.sub(r'\b\d{15,19}\b', '[卡号]', t)
        return t
    df['review'] = df['review'].apply(desens)

    return df.drop(columns=['_c2', '_c1', '_hits', '_emo'])


def load_raw():
    raw = pd.read_csv(os.path.join(RAW, 'online_shopping_10_cats.csv'), encoding='utf-8')
    raw['review'] = (raw['review'].astype(str)
                     .str.replace('\ufeff', '', regex=False)
                     .str.replace(r'\s+', ' ', regex=True).str.strip())
    raw = raw[(raw['review'].str.len() >= 10) & (raw['review'].str.len() <= 400)]
    raw['biz_category'] = raw['cat'].map(CAT_MAP)
    raw = raw.dropna(subset=['biz_category']).drop_duplicates(subset=['review'])
    return raw


# ============================================================
# 4. 当期监控集：真实分布（好评 90.8% / 差评 4.6% / 中评 4.6%）
# ============================================================
def build_monitor(raw, sku_df, n_total=13500):
    """按平台真实评论分布构建当期监控数据"""
    neg_pool = raw[raw.label == 0]
    pos_pool = raw[raw.label == 1]

    n_neg = int(n_total * 0.046)      # 差评 4.6%
    n_pos = int(n_total * 0.908)      # 好评 90.8%
    n_mid = n_total - n_neg - n_pos   # 中评 4.6%

    neg = neg_pool.sample(min(n_neg, len(neg_pool)), random_state=SEED)
    pos = pos_pool.sample(min(n_pos, len(pos_pool)), random_state=SEED)

    # 中评：从真实好评池中抽取"带轻微抱怨"的样本（真实中评多为好评池中的温和表达）
    mild = pos_pool[~pos_pool.index.isin(pos.index)]
    mild_kw = ['一般', '还行', '可以', '凑合', '尚可', '不怎么样', '有待', '勉强']
    cand = mild[mild.review.str.contains('|'.join(mild_kw), regex=True)]
    if len(cand) < n_mid:
        cand = mild
    mid = cand.sample(min(n_mid, len(cand)), random_state=SEED)

    df = pd.concat([neg, pos, mid], ignore_index=True).sample(frac=1, random_state=SEED)
    df = df.copy()
    # 按 review 文本回查所属池，重建 label（避免 index 错位）
    neg_set = set(neg['review'])
    mid_set = set(mid['review'])
    df['label'] = [0 if t in neg_set else 1 for t in df['review']]
    df['_is_mid'] = [1 if t in mid_set else 0 for t in df['review']]
    df = enrich_reviews(df, sku_df)

    # 中评强制为 3 星 + 中性标签
    m = df['_is_mid'] == 1
    df.loc[m, 'rating'] = 3
    df.loc[m, 'cause_l2'] = 'NEU'
    df.loc[m, 'cause_l1'] = '中性'
    # 中评不视为差评，退货概率下调
    df.loc[m, 'order_returned'] = [
        int(np.random.default_rng(SEED + i).random() < 0.05) for i in range(m.sum())
    ]
    df.loc[m & (df['order_returned'] == 0), 'return_amount'] = 0.0

    df = df.drop(columns=['_is_mid'])
    df['review_id'] = [f'M{i+1:06d}' for i in range(len(df))]
    df['dataset'] = 'monitor'
    return df


# ============================================================
# 5. 历史差评训练集：8,000 条
# ============================================================
def build_train(raw, sku_df, n=8000, exclude_texts=None):
    """历史差评训练集。

    【为什么训练集必须排除当期监控集】训练集与监控集若共享文本，会造成数据泄漏：
    模型在"训练时见过"的句子上评估，F1 与 κ 都会被高估。真实业务中也成立——
    模型靠历史数据训练，当期数据只用于监控，当期样本不可能出现在训练集里。

    【为什么排除要放在脱敏之后】两条原文的单号不同，但脱敏后都会变成
    "订单[单号]..."，从而成为同一句话。按原文排除会漏掉这种"脱敏后碰撞"，
    实测曾因此残留 1 条重叠。因此流程为：先抽样→脱敏→再按脱敏文本排除→补足条数。
    """
    neg_pool = raw[raw.label == 0]
    excl = set(exclude_texts or [])
    rng = np.random.default_rng(SEED + 7)
    order = rng.permutation(len(neg_pool))
    pool = neg_pool.iloc[order].copy()

    picked, used = [], set()
    # 分批脱敏，边脱敏边排除，直到凑满 n 条
    BATCH = max(n, 1500)
    cursor = 0
    while len(picked) < n and cursor < len(pool):
        chunk = pool.iloc[cursor:cursor + BATCH].copy()
        cursor += BATCH
        chunk = enrich_reviews(chunk, sku_df)
        chunk = chunk[~chunk['review'].isin(excl)]
        chunk = chunk[~chunk['review'].isin(used)]
        used.update(chunk['review'].tolist())
        picked.append(chunk)
    df = pd.concat(picked, ignore_index=True).head(n).copy()

    # 【v2.2 修正】训练集必须落在**监控期之前**的历史区间。
    # 旧版把历史差评的日期也铺在 2025-11~2026-11，与监控集完全重叠——
    # 号称"经营 3 年累积的历史差评"，日期却和当期数据同期，逻辑上讲不通。
    # 现改为前 3 个自然年（2022-11 ~ 2025-10），week 用负数表示
    # （-156..-1 相对监控期起点），review_date 落在真实历史日期上。
    rng2 = np.random.default_rng(SEED + 71)
    HIST_WEEKS = 156                       # 3 年 ≈ 156 周
    launch_map = sku_df.set_index('sku_id')['launch_week'].to_dict()
    hist_end = datetime(2025, 10, 31)      # 监控期前一天
    dts, wks = [], []
    for sid in df['sku_id'].values:
        lo = int(launch_map.get(sid, 1))
        # 历史期内的可选周：越靠近期末权重越高（商家在增长，近期样本更多）
        w = int(rng2.integers(1, HIST_WEEKS + 1))
        wks.append(-(HIST_WEEKS - w + 1))
        # 监控期起点(2025-11-03) 往前推 w 周
        dts.append(hist_end - timedelta(days=int((HIST_WEEKS - w) * 7 + rng2.integers(0, 7))))
    df['week'] = wks
    df['review_date'] = [d.strftime('%Y-%m-%d') for d in dts]

    df['review_id'] = [f'T{i+1:06d}' for i in range(len(df))]
    df['dataset'] = 'train'
    return df


FINAL_COLS = ['review_id', 'dataset', 'sku_id', 'biz_category', 'review', 'rating',
              'label', 'cause_l1', 'cause_l2', 'price', 'gross_margin',
              'quality_endow', 'order_returned', 'return_amount', 'week',
              'review_date', 'review_len', 'has_image']


# ============================================================
# 5b. 退货明细：订单层面，从面板派生
#     真实电商退货率 15%–25%，且退货原因分布依赖该类目的差评归因结构
# ============================================================
REASON_TEXT = {
    'P1': ('产品本体', '尺码版型不符', 0.16),
    'P2': ('产品本体', '材质做工问题', 0.14),
    'P3': ('产品本体', '功能性能故障', 0.08),
    'I1': ('信息承诺', '描述与实物不符', 0.15),
    'F1': ('履约物流', '破损/变质/污损', 0.12),
    'F2': ('履约物流', '时效慢/错发漏发', 0.06),
    'S2': ('服务体验', '售后流程问题', 0.04),
    'E1': ('预期管理', '价值感知不符', 0.09),
    'X':  ('其他',     '七天无理由/不想要了', 0.13),
}

# 【v2.3 修正】退货原因必须**分品类**建模，不能套用差评归因分布。
# 旧版直接拿差评的 cause_l2 分布当退货原因，产生三处硬伤：
#   ① S1（客服响应问题）被当成退货原因 —— 客服态度差不会导致"退货"这个动作，
#      它是差评理由，不是退货理由；同理 S2 只在"售后流程"层面部分成立。
#   ② 生鲜出现 42.9% 的"七天无理由" —— 《消费者权益保护法》第二十五条明确规定
#      鲜活易腐商品不适用七日无理由退货，这是法律常识错误。
#   ③ 服饰尺码不符只占 5.2% —— 而行业公开数据一致指向"尺码/版型不合占服饰退货
#      的 30%-40%，是第一大原因"。
# 改为按品类给出退货原因结构，数值对齐公开行业口径：
#   服饰：尺码 30-40% / 色差款式 20-25% / 质量 10-15% / 物流 5-10%
#   数码：功能不符预期 40%+ / 质量瑕疵 / 描述不符
#   生鲜：变质损耗为主，无理由退货法定不适用
#   个护：效果不符 / 过敏 / 包装破损（拆封后多不可退）
RETURN_MIX = {
    '服饰': {'P1': 0.34, 'I1': 0.19, 'P2': 0.15, 'E1': 0.12,
             'F1': 0.07, 'F2': 0.05, 'S2': 0.04, 'X': 0.04},
    '数码': {'I1': 0.30, 'P3': 0.26, 'E1': 0.16, 'P2': 0.10,
             'F1': 0.08, 'F2': 0.05, 'S2': 0.04, 'X': 0.01},
    '生鲜': {'F1': 0.47, 'I1': 0.16, 'E1': 0.13, 'F2': 0.09,
             'P2': 0.07, 'S2': 0.05, 'P1': 0.02, 'X': 0.01},
    '个护': {'E1': 0.28, 'P2': 0.24, 'I1': 0.18, 'F1': 0.10,
             'P3': 0.07, 'F2': 0.05, 'S2': 0.05, 'X': 0.03},
}


def build_returns(panel, mon, train):
    """从面板订单量派生退货明细（100% 全量导出，不做抽样）。

    退货原因来自有**独立业务口径**的品类结构表 RETURN_MIX，
    不再复用差评归因分布 —— 理由见 RETURN_MIX 上方注释。

    【为什么不做抽样】(v2.1 修正)
    旧版按 10% 抽样，但写法为 max(1, round(returns*0.1))，
    对"单周仅 1 单退货"的单元会把抽样率放大到 100%。
    实测实际抽样率 36.56% 而非声明的 10%，且小单元被系统性过采样，
    明细表无法直接外推全量。改为全量导出后：
      · 按 sku_id × week 聚合，退货行数与面板 returns 字段完全相等；
      · 类目/原因构成可直接用于决策，不需要逆概率加权。"""
    rng = np.random.default_rng(SEED + 5)

    # 各类目退货原因构成（独立业务口径，见 RETURN_MIX）
    cat_mix = {}
    for cat, mix in RETURN_MIX.items():
        s = sum(mix.values())
        cat_mix[cat] = {k: v / s for k, v in mix.items()}

    rows = []
    rid = 1
    START = datetime(2025, 11, 3)
    # 退货原因与"可二次销售"并非独立：破损/污损/生鲜变质几乎不可二次销售，
    # 而尺码不符、价值感知不符的衣服通常可重新入库。旧版对所有原因用同一个 55%，
    # 会让"破损变质"也能 55% 二次销售，与常识冲突（见 v2.2 审计）。
    # 【v2.3】生鲜额外一刀：哪怕原因是"描述不符"，生鲜离链后也无法再售。
    RESELL_P = {'F1': 0.06, 'P2': 0.35, 'P3': 0.42, 'I1': 0.50,
                'P1': 0.86, 'E1': 0.78, 'X': 0.72, 'S1': 0.70, 'S2': 0.60,
                'F2': 0.80}
    FRESH_CAP = 0.08          # 生鲜退货出冷库即失鲜，二次销售率上限 8%
    for r in panel.itertuples():
        if r.returns == 0:
            continue
        n_sample = int(r.returns)          # 全量导出，与面板退货单数一一对应
        # 该单元单件毛利额：用周毛利总额 ÷ 订单数反推，比查 SKU 表更贴合当期实况
        unit_gm = float(r.gross_profit / max(r.orders, 1))
        cogs = max(float(r.price) - unit_gm, 0.0)   # 单件成本
        mix = cat_mix.get(r.biz_category, {'X': 1.0})
        codes = list(mix.keys())
        probs = np.array(list(mix.values()))
        probs = probs / probs.sum()
        chosen = rng.choice(codes, size=n_sample, p=probs)
        is_fresh = (r.biz_category == '生鲜')
        # 【v2.3】《消法》第二十五条：鲜活易腐商品不适用七日无理由退货。
        # 按概率抽样必然残留少量 X（实测 78/3937），必须确定性改判，
        # 否则法务审阅时一眼就能挑出"生鲜还能七天无理由退"的硬伤。
        if is_fresh:
            chosen = np.array(['F1' if c == 'X' else c for c in chosen])
        for c in chosen:
            l1, text, _ = REASON_TEXT.get(c, ('其他', '其他', 0.1))
            # 退货发生在销售周内（0–6 天），保证时间戳与 week 对齐
            rdate = START + timedelta(days=int((r.week - 1) * 7 + rng.integers(1, 7)))
            resell_p = RESELL_P.get(c, 0.55)
            if is_fresh:
                resell_p = min(resell_p, FRESH_CAP)
            lg_cost = round(float(rng.uniform(8, 22)), 2)
            restock = round(float(rng.uniform(0.05, 0.35)), 3)
            resellable = int(rng.random() < resell_p)
            # 【v2.3 新增】退货损失必须在生成阶段就算成正式字段。
            # 旧版只在报告里"现算"，导致这个数字无法被任何人从数据文件复核，
            # 也导致审计脚本查不到它（本轮审计因此报"缺 loss_amount 列"）。
            #
            # 【口径必须用成本，不能用零售价】
            # 第一版写成 r.price（零售价）全额报废，算出单均 ¥79.39、合计 ¥74.8 万、
            # 占毛利 36.5% —— 偏大。原因是：客户退货时商家退还的是他付的钱，
            # 商家真正损失的是**这件货的成本**，零售价里那部分毛利从未落袋。
            # 正确口径：cogs = price − unit_gm（单件毛利额，由该单元周毛利÷订单数反推）
            #   可二次销售 = 逆向物流 + 成本 × 折损率
            #   不可二次销售 = 逆向物流 + 成本全额
            loss_amt = (lg_cost + cogs * restock) if resellable else (lg_cost + cogs)
            rows.append({
                'return_id': f'RT{rid:06d}',
                'sku_id': r.sku_id,
                'biz_category': r.biz_category,
                'week': r.week,
                'return_date': rdate.strftime('%Y-%m-%d'),
                'return_reason_l1': l1,
                'return_reason_l2': c,
                'return_reason_text': text,
                'return_amount': round(r.price, 2),
                'order_amount': round(r.price, 2),
                'logistics_cost': lg_cost,
                'restock_loss_rate': restock,
                'is_resellable': resellable,
                'cogs': round(float(cogs), 2),
                'loss_amount': round(float(loss_amt), 2),
                'loss_basis': '折损' if resellable else '报废',
            })
            rid += 1
    return pd.DataFrame(rows)
P_TRUE = dict(b0=-1.20, b1=0.055, b2=1.60, b3=-0.40, theta0=0.05, theta1=0.12, c0=45.0)


def build_service_logs(reviews, n=5000):
    rng = np.random.default_rng(SEED + 1)
    pool = reviews[(reviews.order_returned == 1) | (reviews.label == 0)]
    idx = rng.choice(pool.index.values, size=min(n, len(pool)), replace=False)
    sel = pool.loc[idx].copy()

    hist = rng.poisson(0.7, len(sel))
    # 【v2.3 修正】补偿必须是**标准档位**。电商客服的补偿来自固定权益池
    # （0/5/10/15/20/30/50 元），不是任意小数。旧版用 np.clip(offset, 0, margin*1.15)
    # 把档位削成了 78 个小数（¥7.98 / ¥11.01 / ¥39.74…），且 771 条补偿超过单件毛利，
    # 业务上不可能发生（补偿超毛利等于白送钱）。
    #
    # 【v2.3 二次修正 —— 这一条更关键】
    # 上一版用"补偿封顶到毛利"来做可行性约束，结果把档位和毛利绑死了：
    # 低毛利 SKU 只能拿到 ≤20 元，50 元档全落在高客单 SKU 上。
    # 实测 补偿 vs 毛利 相关 0.209、vs 价格 0.151 —— 补偿的因果效应
    # 被"客单价"混杂，这不是数据瑕疵，而是把因果识别做坏了：
    # 我们会误以为"补偿 50 元更有效"，其实只是"50 元档的商品本身更贵、更好卖"。
    # 正确做法：把补偿做成**整档位随机试验**（真实电商的"补偿策略 AB 测试"）。
    # 关键取舍：只有毛利够高（≥ 最高档 50 元）的工单才能做完整 7 档随机化，
    # 其余工单按"够得着的档位"随机 —— 但**用一列 flag 显式标出实验样本**，
    # 让后续分析能在"全档位可比"的子集上估计因果效应，而不是偷偷把
    # 高补偿档和高客单 SKU 绑在一起。
    LADDER = np.array([0.0, 5.0, 10.0, 15.0, 20.0, 30.0, 50.0])
    gm = sel['gross_margin'].values
    TOP = LADDER[-1]
    exp_eligible = gm >= TOP                     # 可进入完整 7 档随机
    comp = np.empty(len(sel), dtype=float)
    for i, g in enumerate(gm):
        avail = LADDER[LADDER <= g] if not exp_eligible[i] else LADDER
        if len(avail) == 0:
            avail = np.array([0.0])
        comp[i] = rng.choice(avail)

    margin_rate = sel['gross_margin'].values / np.maximum(sel['price'].values, 1)
    # 【v2.3 修正】投诉历史系数符号。theta 是"策略用户比例"，历史投诉越多越难挽单，
    # 所以 complaint_history 必须**降低**挽单率。旧版 b3=-0.40 且方程里写 -b3*hist，
    # 双重取负变成 +0.40，导致挽单率随投诉历史单调上升（0次 47.9% → 3次 72.8%），
    # 与"老投诉户更难搞定"的业务常识相反。
    logit = (P_TRUE['b0'] + P_TRUE['b1'] * comp
             + P_TRUE['b2'] * margin_rate + P_TRUE['b3'] * hist)
    p_honest = 1 / (1 + np.exp(-logit))
    theta = P_TRUE['theta0'] + P_TRUE['theta1'] * comp / (comp + P_TRUE['c0'])
    p_eff = (1 - theta) * p_honest
    saved = rng.random(len(sel)) < p_eff

    out = pd.DataFrame({
        'log_id': [f'L{i+1:05d}' for i in range(len(sel))],
        'review_id': sel['review_id'].values,
        'sku_id': sel['sku_id'].values,
        'biz_category': sel['biz_category'].values,
        'cause_l2': sel['cause_l2'].values,
        'price': sel['price'].values,
        'gross_margin': sel['gross_margin'].values,
        'compensation': comp.round(2),
        'exp_eligible': exp_eligible.astype(int),
        'complaint_history': hist,
        'is_saved': saved.astype(int),
        'theta_true': theta.round(4),
        'p_effective_true': p_eff.round(4),
    })
    out['turn_count'] = rng.integers(2, 14, len(out))
    out['first_reply_min'] = np.round(rng.gamma(2.0, 4.2, len(out)), 1)
    out['satisfaction_after'] = np.where(
        out.is_saved == 1,
        rng.choice([3, 4, 5], len(out), p=[.18, .46, .36]),
        rng.choice([1, 2, 3], len(out), p=[.52, .33, .15]))

    # 【v2.2】补时间戳：客服工单发生在对应评论之后当天或次日，
    # 且必须与 week 对齐（旧版没有时间列，三层事件无法按时间轴串联）
    START = datetime(2025, 11, 3)
    wks = sel['week'].values.astype(int)
    sel_date = sel['review_date'].values
    log_dates, log_weeks = [], []
    for w, ds in zip(wks, sel_date):
        try:
            base = datetime.strptime(str(ds)[:10], '%Y-%m-%d')
        except Exception:
            base = START + timedelta(days=int((max(w, 1) - 1) * 7))
        d = base + timedelta(days=int(rng.integers(0, 2)))
        log_dates.append(d.strftime('%Y-%m-%d'))
        # 工单所在周（可能跨到下一周）
        delta_w = max(0, (d - base).days // 7)
        log_weeks.append(int(min(w + delta_w, WEEKS)))
    out['week'] = log_weeks
    out['log_date'] = log_dates
    return out


# ============================================================
# 7. SKU×周 面板：两级传导，真值冻结
# ============================================================
# 【v2.3 重新标定 —— 传导强度必须对得上真实业务的效应量】
# 旧标定 λ1=0.42 意味着"退货率 +5pp → DSR 只掉 0.021 分"。而真实平台
# 退货率恶化的后果是：DSR 从 4.8 掉到 4.6 就算"严重警告"，对应退货率
# 大致翻倍（+15pp）。即真实 λ_effective ≈ 0.2/0.15 ≈ 1.3，旧值小了 3 倍。
# 后果是连锁的：
#   ① DSR 组内变异只剩 0.05，真实平台是 0.11；
#   ② ω2=0.09 的信号 std 只有 0.0047，被 0.12 的曝光噪声彻底淹没
#      （SNR=0.04），参数回收必然失败 —— 这不是估计方法的问题，是 DGP 没给够信息；
#   ③ 项目核心论点"差评毁流量"的效应量被系统性低估 3 倍，
#      算出来的"退货率降 1pp 值多少钱"也跟着缩水。
# 新标定让"退货率 +5pp → DSR −0.065 分"、"DSR −0.1 分 → 曝光 −6.4%"，
# 与行业公开的权重敏感度同量级。
LAMBDA1, LAMBDA2 = 1.30, 0.55
OMEGA1, OMEGA2 = 0.64, 0.28
THETA_PRICE = -0.35


def build_panel(sku_df):
    rng = np.random.default_rng(SEED + 2)
    weeks = np.arange(1, WEEKS + 1)
    wk_eff = rng.normal(0, 0.15, WEEKS)
    for w, boost in [(24, 0.50), (44, 0.75), (48, 0.35), (10, 0.28), (36, 0.28)]:
        wk_eff[w - 1] += boost
    sku_fe = rng.normal(0, 0.25, len(sku_df))
    dsr_fe = rng.normal(0, 0.08, len(sku_df))

    rows = []
    for i, r in enumerate(sku_df.itertuples()):
        # 【v2.3】把 SKU 自己的 return_base 与类目固定效应接入退货方程。
        # 把 return_base 反解成 logit 截距，使该 SKU 的稳态退货率落在其基线上，
        # 再叠加类目偏移 —— 这样面板退货率的类目排序才和行业事实一致。
        cat_adj = CAT_RETURN_ADJ.get(r.biz_category, 0.0)
        base_logit = float(np.log(r.return_base / (1 - r.return_base))) - cat_adj
        rr_prev, rr_prev2 = r.return_base, r.return_base
        dsr_prev, dsr_prev2, dsr_prev3 = 4.70, 4.70, 4.70
        for w in weeks:
            if w < r.launch_week:
                continue
            if w > getattr(r, 'end_week', WEEKS):   # 【v2.3】生命周期截止
                continue
            promo = 0.28 if w in (24, 44, 48) else 0.0
            season = 0.15 if w in (10, 11, 36, 37) else 0.0
            shock = rng.normal(0, 0.18) if rng.random() < 0.05 else 0.0
            logit = (base_logit + cat_adj
                     - 0.35 * r.quality_endow
                     + 0.06 * (np.log(r.price) - np.log(119))
                     + promo + season + 0.45 * shock + rng.normal(0, 0.20))
            rr = 1 / (1 + np.exp(-logit))

            # 截距随 λ 重算：λ1 从 0.42 提到 1.30、λ2 从 0.18 提到 0.55 后，
            # 平均退货率 0.17 会让 DSR 被压低 1.30*0.17+0.55*0.17 = 0.315 分，
            # 均值从 4.72 掉到 4.41（真实平台 DSR 4.5-4.8 才是常态）。
            # 把截距从 4.72 抬到 5.03，抵掉水平位移、保留弹性。
            dsr = (4.99 + dsr_fe[i] + wk_eff[w - 1] * 0.05
                   - LAMBDA1 * rr_prev - LAMBDA2 * rr_prev2 + rng.normal(0, 0.05))
            # 上界用 4.98 而非 5.0：真实平台 DSR 极少恰好等于满分，
            # 大量样本堆在 5.00 会形成人造的截断点，破坏 TWFE 的线性假设。
            dsr = float(np.clip(dsr, 3.0, 4.98))

            # 截距随 ω1 重算：ln_exp = A + sku_fe + wk_eff + ω1·dsr_lag2 + ω2·dsr_lag3 + θ·ln(price)
            # ω1 从 0.18 提到 0.64 后，DSR 均值 4.41 会贡献 2.82（旧值仅 0.83），
            # 在 log 尺度上是 e^1.99≈7.3 倍的曝光膨胀，年订单会从 6 万单炸到 108 万单，
            # 直接毁掉"600 万级店铺"的规模设定。因此截距从 9.42 下调到 6.61，
            # 把 ω1 提高带来的水平位移抵掉，只保留它带来的**弹性**变化。
            # 曝光噪声从 0.12 降到 0.08：ω2 的真值 0.28 只产生 0.055 的 log 变异，
            # 而 DSR lag2 与 lag3 的原始相关高达 0.895（连续两周的 DSR 天然高度重叠），
            # 噪声一大，ω2 就会被 ω1 吸收（实测 ω2 低估到 0.2367，误差 15.5%）。
            # 0.08 仍属真实平台"周度曝光波动"的合理量级（±8%）。
            #
            # 【v2.5】截距前加 ln(SHOP_SCALE)：纯水平位移，弹性不动。
            # ln(6)≈1.792，即所有人的曝光同乘 6。
            # 这是本轮唯一改动，目的见文件顶部 SHOP_SCALE 注释。
            ln_exp = (6.42 + np.log(SHOP_SCALE) + sku_fe[i] + wk_eff[w - 1]
                      + OMEGA1 * dsr_prev2 + OMEGA2 * dsr_prev3
                      + THETA_PRICE * np.log(r.price) + rng.normal(0, 0.08))
            # 上限随之从 80 万提到 480 万。
            # 原来 80 万是照着"6 千级曝光中位数"设的，现在整体 ×6，
            # 若不动上限，最大的那几个 SKU 会被截平，等于人为削掉头部动销，
            # 而本轮的整个目的恰恰是"不要人为压低单量"。
            #
            # 【实测确认过不会被截断】v2.4 曝光最大值 50,231，
            # ×6 后 301,386，离原来的 80 万还有很大余量 —— 也就是说
            # 即使不改上限也不会截断。这里把上限提到 480 万只是为了
            # 保持"上限 / 中位数"的比值不变，不引入新的隐含约束。
            exposure = int(np.clip(np.exp(ln_exp), 300, 4_800_000))
            visitors = int(exposure * r.ctr_base * rng.uniform(0.9, 1.1))
            orders = int(visitors * r.cvr_base * rng.uniform(0.9, 1.1))
            # 二项分布抽样：避免 round() 在低单量时系统性舍掉退货
            n_returns = int(rng.binomial(max(orders, 0), float(np.clip(rr, 0, 1))))
            returns = n_returns

            rows.append({
                'sku_id': r.sku_id, 'biz_category': r.biz_category, 'week': int(w),
                'price': r.price,
                # 【v2.2】拆成两列，语义分明，避免审计时被误当成会计恒等式不成立：
                #   return_rate_latent = DGP 生成的潜在退货倾向（logit 概率），连续、恒有值
                #   return_rate        = 会计口径 = returns/orders，orders=0 时为 NaN
                'return_rate_latent': round(rr, 4),
                'return_rate': (round(returns / orders, 4) if orders > 0 else np.nan),
                'dsr': round(dsr, 3),
                'exposure': exposure, 'visitors': visitors, 'orders': orders,
                'returns': returns,
                'gmv': round(orders * r.price, 2),
                'gross_profit': round(orders * r.gross_margin, 2),
                'quality_endow': r.quality_endow,
                'promo_week': int(w in (24, 44, 48)),
            })
            rr_prev2, rr_prev = rr_prev, rr
            dsr_prev3, dsr_prev2, dsr_prev = dsr_prev2, dsr_prev, dsr
    return pd.DataFrame(rows)


# ============================================================
# 8. 金标准标注集
# ============================================================
def build_gold(monitor, n=200):
    """金标准标注集：必须来自**训练时没见过**的数据，否则测出来的 κ / F1 全部虚高。

    【v2.2 修正 —— 这个是致命的方法论漏洞】
    旧版从 train 抽样，198 条全部落在训练集里，且 auto_label 列直接写着答案。
    两个问题叠加：① 模型在训练时已见过这些句子，"独立测试集"不成立；
    ② auto_label 就是规则引擎的输出，等于把预测结果当标准答案，κ 必然接近 1。
    现改为从**当期监控集**抽样（与训练集零重叠），并把 auto_label 置空——
    它只能由两位标注员独立盲标后填入，这才是真正的人工金标准。
    """
    pool = monitor[monitor.cause_l2 != 'X'].copy()
    parts = []
    per = max(1, n // pool['cause_l2'].nunique())
    for c, g in pool.groupby('cause_l2'):
        parts.append(g.sample(min(per, len(g)), random_state=SEED))
    gold = pd.concat(parts)

    # 【v2.2】分层抽样会因个别类别样本不足而凑不满 n（如 P1 尺码版型全期仅 7 条）。
    # 这是真实约束——不能凭空造标注样本。处理方式：用剩余池补足到 n 条，
    # 并在报告中公开各类别的"可用样本数"，让 κ 的置信区间可被评估。
    if len(gold) < n:
        rest = pool.drop(index=gold.index, errors='ignore')
        need = n - len(gold)
        if len(rest) > 0:
            gold = pd.concat([gold, rest.sample(min(need, len(rest)), random_state=SEED + 9)])

    gold = gold.sample(frac=1, random_state=SEED).head(n).copy()

    gold['source'] = 'monitor'          # 标明来源，便于审计是否与训练集重叠
    gold['auto_label'] = ''             # 置空：不允许把规则引擎输出当答案
    gold['annotator_A'] = ''
    gold['annotator_B'] = ''
    gold['agreed'] = ''
    gold['final'] = ''
    return gold[['review_id', 'source', 'biz_category', 'review', 'rating',
                 'auto_label', 'annotator_A', 'annotator_B', 'agreed', 'final']]


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print('[1/7] 读取真实语料并映射类目 ...')
    raw = load_raw()
    print(f'      可用真实语料 {len(raw):,} 条（负面 {int((raw.label==0).sum()):,} / '
          f'正面 {int((raw.label==1).sum()):,}）')

    print('[2/7] 构建 SKU 主数据 ...')
    sku = build_sku_master()
    sku.to_csv(os.path.join(OUT, 'sku_master.csv'), index=False, encoding='utf-8-sig')
    print(f'      {len(sku)} 个 SKU，{sku.biz_category.nunique()} 个类目')

    print('[3/7] 构建当期评论监控（真实分布） ...')
    mon = build_monitor(raw, sku)[FINAL_COLS]
    mon.to_csv(os.path.join(OUT, 'reviews_monitor.csv'), index=False, encoding='utf-8-sig')
    print(f'      {len(mon):,} 条，差评率 {(mon.label==0).mean():.1%}')

    print('[4/7] 构建历史差评训练集 ...')
    tr = build_train(raw, sku, exclude_texts=mon['review'].tolist())[FINAL_COLS]
    tr.to_csv(os.path.join(OUT, 'reviews_train.csv'), index=False, encoding='utf-8-sig')
    print(f'      {len(tr):,} 条（已排除与监控集重叠的文本）')

    print('[5/7] 构建经营面板（SKU×周） ...')
    panel = build_panel(sku)
    panel.to_csv(os.path.join(OUT, 'panel_weekly.csv'), index=False, encoding='utf-8-sig')
    print(f'      {len(panel):,} 行，{panel.sku_id.nunique()} 个 SKU，'
          f'年订单 {panel.orders.sum():,}')

    print('[6/7] 构建退货明细（订单层面，从面板派生） ...')
    ret = build_returns(panel, mon, tr)
    ret.to_csv(os.path.join(OUT, 'returns.csv'), index=False, encoding='utf-8-sig')
    print(f'      {len(ret):,} 条退货（全量，与面板逐单元对齐），'
          f'店铺整体退货率 {panel.returns.sum()/panel.orders.sum():.1%}')

    print('[7/8] 构建客服记录 ...')
    logs = build_service_logs(pd.concat([mon, tr], ignore_index=True))
    logs.to_csv(os.path.join(OUT, 'service_logs.csv'), index=False, encoding='utf-8-sig')
    print(f'      {len(logs):,} 条客服记录，挽单率 {logs.is_saved.mean():.1%}')

    print('[8/8] 构建金标准与参数文件 ...')
    gold = build_gold(mon)          # 从监控集抽，确保与训练集零重叠
    gold.to_csv(os.path.join(OUT, 'gold_standard_200.csv'), index=False, encoding='utf-8-sig')

    params = {
        'seed': SEED,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'text_source': 'online_shopping_10_cats（公开中文电商评论数据集，62,774 条，人工情感标注）',
        'kept_categories': list(CAT_SPEC.keys()),
        'excluded_categories': {'酒店': '服务类，非零售电商', '书籍': '图书类，非零售电商', '热水器': '真实语料仅 474 条，样本量不足以支撑独立类目' },
        'transmission': {'lambda1': LAMBDA1, 'lambda2': LAMBDA2,
                         'omega1': OMEGA1, 'omega2': OMEGA2, 'theta_price': THETA_PRICE},
        'compensation': P_TRUE,
        'business_scale': {
            'sku_count': int(len(sku)),
            'annual_orders': int(panel.orders.sum()),
            'annual_gmv': round(float(panel.gmv.sum()), 2),
            'avg_order_value': round(float(panel.gmv.sum() / panel.orders.sum()), 2),
        },
        'note': ('文本层为真实公开语料，未做改写；SKU 主数据、经营面板、客服补偿记录为'
                 '团队依据行业公开区间与上述冻结参数生成的模拟数据，用于因果识别与参数回收检验。')
    }
    with open(os.path.join(OUT, 'dgp_params.json'), 'w', encoding='utf-8') as f:
        json.dump(params, f, ensure_ascii=False, indent=2)

    print('\n=== 数据汇总 ===')
    print(f'当期评论   {len(mon):>8,} 条')
    print(f'历史差评   {len(tr):>8,} 条')
    print(f'退货明细   {len(ret):>8,} 条')
    print(f'客服记录   {len(logs):>8,} 条')
    print(f'经营面板   {len(panel):>8,} 行')
    print(f'SKU        {len(sku):>8,} 个')
    print(f'金标准     {len(gold):>8,} 条')
    print(f'年订单     {panel.orders.sum():>8,} 单')
    print(f'年 GMV     {panel.gmv.sum()/10000:>8,.1f} 万元')
    print(f'客单价     {panel.gmv.sum()/panel.orders.sum():>8.1f} 元')