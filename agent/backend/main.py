# -*- coding: utf-8 -*-
"""差评炼金炉 · FastAPI 服务

三个端点：
  GET  /api/health          健康检查 + 当前推理模式 + 可用工具清单
  GET  /api/meta            可问的问题、类目、周次范围（前端用来做提示词与筛选）
  POST /api/chat            SSE 流式问答（核心）
  GET  /api/eval            预置问题的自检报告（评委会问"你自己测过没有"）

关于 SSE：用 POST + text/event-stream 而不是 EventSource，
因为 EventSource 只支持 GET，问题文本会进 URL 编码，中文长问题容易出问题。
前端用 fetch + ReadableStream 手动解析（见 frontend/src/lib/sse.ts）。

关于"假流式"：每一步之间插入可配置的间隔（pace_ms），目的是让推理过程
可被人类阅读。数字全部在间隔之前就算完了——这不是在等模型，是在给人看。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# 让 `python main.py` 和 `uvicorn main:app` 都能跑
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from orchestrator import TOOL_SCHEMA, TOOL_TITLE, get_agent
from tools.data_hub import get_hub

app = FastAPI(
    title='差评炼金炉 · 归因诊断 Agent',
    description='把「这周差评为什么涨了」变成一条带证据的归因链',
    version='1.0.0',
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],           # 本地演示；生产应收窄到前端域名
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    week: int | None = Field(default=None, ge=1, le=52)
    pace_ms: int = Field(default=260, ge=0, le=2000)


# ---------------------------------------------------------------------------
# 基础端点
# ---------------------------------------------------------------------------

@app.get('/api/health')
def health():
    a = get_agent()
    hub = get_hub()
    return {
        'ok': True,
        'mode': a.mode,
        # llm 现在是一个 LLMClient 对象，不是 dict。
        # 顺手把 base_url 也报出来：换厂商时最容易错的是 base 拼错，
        # 直接在健康检查里暴露，比翻日志快。
        'model': a.llm.model if a.llm else '确定性推理引擎 v1',
        'llm_base': a.llm.base if a.llm else None,
        'llm_last_error': (a.llm.last_error if a.llm else ''),
        'data': {
            'skus': int(hub.sku.shape[0]),
            'weeks': len(hub.weeks),
            'latest_week': hub.latest_week,
            'categories': hub.categories,
            # 【这两个数是给前端左栏"数据底盘"用的】
            # 那两格原来把 64659 单和 14.6% 写死在 JSX 里，
            # 数据重建到 v2.5（61.8 万单 / 13.53%）后没人记得改前端，
            # 于是页面上常驻着一个三个版本之前的数字。
            # 这种错比正文写错更伤：正文只在回答时出现，
            # 左栏是每一屏都在的，而且看起来最像"事实"。
            # 现在统一从 hub 算，前端不再自己编。
            'orders_year': int(hub.panel['orders'].sum()),
            'return_rate': round(
                float(hub.panel['returns'].sum() / max(hub.panel['orders'].sum(), 1)), 4),
            'rows': {
                'panel_weekly': int(hub.panel.shape[0]),
                'returns': int(hub.returns.shape[0]),
                'reviews_monitor': int(hub.reviews.shape[0]),
                'service_logs': int(hub.logs.shape[0]),
                'sku_master': int(hub.sku.shape[0]),
            },
        },
        'tools': [t['function']['name'] for t in TOOL_SCHEMA],
    }


@app.get('/api/meta')
def meta():
    """给前端做引导用的元信息。"""
    hub = get_hub()
    promo = sorted(hub.panel.loc[hub.panel['promo_week'] == 1, 'week'].unique().tolist())
    return {
        'categories': hub.categories,
        'weeks': {'min': int(min(hub.weeks)), 'max': int(hub.latest_week)},
        'promo_weeks': [int(w) for w in promo],
        'tools': [
            {'name': t['function']['name'],
             'title': TOOL_TITLE.get(t['function']['name'], t['function']['name']),
             'description': t['function']['description']}
            for t in TOOL_SCHEMA
        ],
        'suggestions': [
            {'label': '双11 那周为什么退货率高', 'question': '第44周退货率为什么涨了'},
            {'label': '最近一周有问题吗', 'question': '最新一周差评有异常吗'},
            {'label': '退货到底亏了多少钱', 'question': '退货总共损失了多少钱'},
            {'label': '补多少钱最划算', 'question': '客服补偿补多少最划算'},
            {'label': '服饰类目怎么回事', 'question': '服饰类目这周退货为什么涨了'},
            {'label': '给我一套可执行方案', 'question': '第44周退货率涨了，我该怎么办'},
            # ---- v3.1 评论分析四件套 ----
            # 【这四条为什么必须放进 suggestions】
            # 新能力如果只写在 README 里，演示时没有任何人会去点——
            # 首页能点的按钮就是这套系统"会做什么"的全部声明。
            # 四条分别对应情感 / 词云 / 画像 / 竞品，
            # 覆盖了四个模块各自最有代表性的问法。
            {'label': '评论情感怎么样', 'question': '帮我分析一下评论的情感'},
            {'label': '差评都在说什么', 'question': '差评都在说什么'},
            {'label': '用户画像是什么样', 'question': '用户画像是什么样的'},
            {'label': '和竞品比怎么样', 'question': '我们和竞品比怎么样'},
            {'label': '数据是怎么来的', 'question': '你的评论数据是从哪来的'},
        ],
    }


# ---------------------------------------------------------------------------
# 核心：SSE 流式问答
# ---------------------------------------------------------------------------

@app.post('/api/chat')
async def chat(req: ChatRequest):
    if not req.question.strip():
        raise HTTPException(status_code=422, detail='问题不能为空')

    agent = get_agent()
    agent.pace = req.pace_ms / 1000.0

    async def gen():
        # 首帧先推一个注释行，避免某些代理缓冲住流导致前端白屏
        yield ': connected\n\n'
        try:
            async for ev in agent.run(req.question, week=req.week):
                yield ev.to_sse()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 任何未捕获异常都要以事件形式告诉前端，而不是断流
            payload = {'type': 'error', 'message': f'{type(e).__name__}: {e}'}
            yield f'data: {json.dumps(payload, ensure_ascii=False)}\n\n'

    return StreamingResponse(
        gen(),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',   # nginx 下禁用缓冲
        },
    )


# ---------------------------------------------------------------------------
# 采集器：数据是怎么进来的
# ---------------------------------------------------------------------------

@app.get('/api/collectors')
def collectors():
    """采集渠道清单。

    【这个端点存在的理由，不是为了"多一个 API"】
    演示时最容易被追问的一句是"你的评论数据哪来的"。如果答案只能是
    "我编的"，整套分析的可信度就没了。所以把采集链路本身做成可查的：
    每个渠道说明自动化程度、要不要平台资质、以及——哪个明确不做、为什么。

    【为什么把"不做"也列出来】
    只列能做的，等于暗示"平台页面抓取只是还没做"。
    实际是决定不做，理由写在 collectors.py 的模块头里。
    这两件事在汇报里必须分清：一个是 backlog，一个是 design decision。
    """
    from tools import collectors as col
    lst = col.list_collectors()
    return {
        'total': len(lst),
        'auto': sum(1 for c in lst if c['auto'] and c['available']),
        'items': [{
            'name': c['name'],
            'title': c['title'],
            'auto': bool(c['auto']),
            'compliance': c['compliance'],
            'available': bool(c['available']),
            # 每个渠道都说清"这一步要人做什么"。
            # 只有布尔值的话，"半自动"到底半在哪，看的人只能猜。
            'needs_human': c.get('needs_human', ''),
            'note': c.get('note', ''),
        } for c in lst],
        'policy': ('平台页面抓取不实现：违反服务条款、反爬对抗导致代码寿命以周计、'
                   '平台只展示前 N 页使样本天然有偏。替代方案是'
                   '「平台后台导出 → inbox → 自动导入」，'
                   '把人的登录态留在人手里。'),
    }


@app.post('/api/collectors/scan')
def collectors_scan():
    """扫一遍 inbox，报告"现在有几个文件待导入、能补进来多少条"。

    【为什么用 dry 模式，不直接导入】
    导入是写操作：会往 corpus.csv 追加行、会移动 inbox 里的文件。
    一个 GET 风格的信息查询不该有副作用——否则任何人刷一下这个 URL
    就把数据改了，而且没人知道是谁触发的。
    真要导入，走命令行 `python -m tools.collectors --run imported`，
    那是一条有明确发起人、有日志的动作。
    """
    from tools import collectors as col
    try:
        # 【用 run(write=False) 而不是构造什么 dry 参数】
        # BaseCollector.run 本来就留了 write 开关：
        # write=False 时它走到校验为止，然后返回 added=len(df) 就停，
        # 不调 _merge_into_corpus、不写 manifest、不移动 inbox 文件。
        # 这是这个模块里唯一一处"预演"能力，别在外面重新发明一遍。
        r = col.run_collector('imported', write=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'{type(e).__name__}: {e}')

    return {
        'ok': True,
        'dry': True,
        'collector': r.collector,
        'ok_run': bool(r.ok),
        # 【added 在预演时含义变了】
        # 写模式下它是"真正落库的条数"，预演时是"如果导入会落多少条"。
        # 前端必须知道这个区别，否则会以为已经导进去了。
        'would_add': int(getattr(r, 'added', 0) or 0),
        'errors': list(getattr(r, 'errors', []) or []),
        'detail': getattr(r, 'detail', {}) or {},
        'note': '这是预演：只统计能导入多少条，没有写入 corpus、没有移动 inbox 文件。',
    }


# ---------------------------------------------------------------------------
# 自检：把"我自己验证过"变成可点的一页
# ---------------------------------------------------------------------------

EVAL_CASES = [
    {'id': 'E1', 'question': '第44周退货率为什么涨了',
     'expect_tools': ['diagnose_week', 'promo_mechanism', 'attribution_structure'],
     'expect_badge': '机制已识别',
     'why': '双11 周，必须识别出是促销机制而非商品或物流问题'},
    {'id': 'E2', 'question': '最新一周差评有异常吗',
     'expect_tools': ['diagnose_week'],
     'expect_badge': '已重定向',
     'why': '最新周正常，应自动重定向到真正异常的第 44 周'},
    # 【这两条用例随数据版本 v2.4 更新】
    # 原版针对"第3周151单 → 样本不足"和"第41周 z=-3.77 → 显著改善"。
    # 补齐样本量后数据变了：第3周变 1,713 单、z=-1.22；第41周 z=-1.76。
    # 旧断言测的已经不是系统行为，而是"数据有没有变回旧版本"。
    # 现在改用新数据下的真实情况，测的仍然是同一件事——
    # 系统会不会在"这周根本不是异常"的时候老实说出来。
    {'id': 'E3', 'question': '第3周为什么异常',
     'expect_tools': ['diagnose_week'],
     'expect_badge': '无异常',
     'why': '第3周数据充足且不显著，必须直说"不是异常周"，不许改题去答第44周'},
    # 【这两条用例随数据版本 v2.5 更新】
    # v2.5 把店铺流量放大 6 倍后重算，全店唯一的显著下降周变成第 49 周
    # （z=-5.10）；第 5 周原本 z=-2.92，样本放大后回落到 -1.25，不再显著。
    {'id': 'E4', 'question': '第49周怎么回事',
     'expect_tools': ['diagnose_week'],
     'expect_badge': '显著改善',
     'why': '第49周 z=-5.10 显著下降，不是问题，不该套用归因模板'},
    {'id': 'E5', 'question': '客服补偿补多少最划算',
     'expect_tools': ['compensation_curve'],
     'expect_badge': '权衡分析',
     'why': '应先求最优档，再换算价值'},
]


@app.get('/api/eval')
async def eval_run():
    """跑一遍预置用例，返回实际路径与期望是否一致。

    这个端点是给评委用的：不用听我讲，点一下就知道每个场景是不是真的按预期走。
    """
    agent = get_agent()
    old_pace = agent.pace
    agent.pace = 0.0
    out = []
    try:
        for c in EVAL_CASES:
            tools, badge, headline, ok_tools, ok_badge = [], '', '', False, False
            async for ev in agent.run(c['question']):
                if ev.type == 'tool_call':
                    tools.append(ev.payload['tool'])
                elif ev.type == 'answer':
                    badge = (ev.payload.get('badge') or {}).get('label', '')
                    headline = ev.payload.get('headline', '')
            ok_tools = all(t in tools for t in c['expect_tools'])
            ok_badge = badge == c['expect_badge']
            out.append({
                **c,
                'actual_tools': tools,
                'actual_badge': badge,
                'actual_headline': headline,
                'pass': bool(ok_tools and ok_badge),
            })
    finally:
        agent.pace = old_pace
    return {
        'total': len(out),
        'passed': sum(1 for x in out if x['pass']),
        'cases': out,
    }


# ---------------------------------------------------------------------------
# 功能页面端点
# ---------------------------------------------------------------------------
#
# 【为什么要有一整组 /api/pages/*，而不是让前端各取所需拼】
#
# 页面要用到的每个数字都必须能追回到 data/processed/*.csv 的某一行。
# 一旦前端自己乘 100、自己求和，它就变成了第二套数据源。
# 这套系统已经为此付过代价：左栏"年订单 64.7k / 退货率 14.6%"
# 是写死在前端 JSX 里的，数据重建之后没人记得改，
# 于是每一屏都在展示一个三个版本之前的数字。
#
# 所以这一组端点的分工是死的：
#   · 后端算完，连百分号都算好（传 17.04 而不是 0.1704）
#   · 前端只排版，不许出现任何换算
#
# 【为什么它们不走 Agent】
# 问答要"按需深入"，由 Agent 决定查什么；
# 页面要"一屏看全"，固定查一组，不做动态决策。
# 两条路径共用同一批底层函数（DataHub / review_analytics），
# 数字口径完全一致——这也是为什么归因页的异常判定要调
# diagnose_week，而不是自己再写一个阈值。

@app.get('/api/pages/dashboard')
def page_dashboard():
    """总览：KPI + 52 周曲线 + 类目横向对比 + 原因结构 + 异常周。"""
    from pages import dashboard
    return dashboard()


@app.get('/api/pages/attribution')
def page_attribution(week: int | None = None):
    """归因：给定一周，拆出涨了多少、涨在哪、为什么涨。

    【week 为空时不等同于"第 1 周"】
    省略 week 时返回最新一周，这是页面首次打开的默认。
    如果传了越界值（0 或 99），diagnose_week 会返回 ok=False，
    这里如实透出，不替它兜一个"最近一周"——那样用户会以为
    自己看到的是第 99 周的真实数据。
    """
    from pages import attribution
    return attribution(week)


@app.get('/api/pages/reasons')
def page_reasons(category: str | None = None):
    """原因治理：9 个原因各自值不值得管 + 类目×原因热力矩阵。"""
    from pages import reason_matrix
    hub = get_hub()
    if category and category not in hub.categories:
        # 【未知类目要报错，不能静默返回全量】
        # 静默兜底的话，前端传错一个类目名，页面会显示"全部类目"的数据，
        # 而标题上写着那个错类目名 —— 看起来完全正常，数字全错。
        raise HTTPException(400, f'未知类目：{category}（可选：{"、".join(hub.categories)}）')
    return reason_matrix(category)


@app.get('/api/pages/reviews')
def page_reviews(entity: str = '本店'):
    """评论洞察：情感结构 + 抱怨点 + 画像 + 分周走势。"""
    from pages import review_overview
    from tools import review_tools as rt
    # 【rt.entities() 返回的是 dict 列表，不是字符串列表】
    # 每个元素形如 {'entity_id','entity_name','n','mode',...}。
    # 第一版直接 join，报 TypeError: sequence item 0: expected str, dict found。
    # 好在它当场就炸了 —— 如果改成 `in` 判断，会静默地全部判为"未知实体"，
    # 表现为"不管选哪个都 400"，同样难查但更费时间。
    ents = [e['entity_name'] for e in rt.entities()]
    if entity not in ents:
        raise HTTPException(400, f'未知实体：{entity}（可选：{"、".join(ents)}）')
    return review_overview(entity)


@app.get('/api/pages/competitors')
def page_competitors(category: str | None = None):
    """竞品雷达：负评率对比 + 维度差距 + 用词差异 + 数据性质。"""
    from pages import competitor_radar
    hub = get_hub()
    if category and category not in hub.categories:
        raise HTTPException(400, f'未知类目：{category}')
    return competitor_radar(category)


@app.get('/api/pages/eval')
def page_eval():
    """自检：四套验收清单 + 数据规模快照 + 独立性论证。

    【和 /api/eval 的区别，别混】
    /api/eval 是**现场跑**八场景行为回归（要 LLM Key，几十秒）；
    这个是把**已固化**的验收清单展示出来（毫秒级）。
    两个端点名字像，职责完全不同：
      · /api/eval       = "现在这台机器还能不能通过"
      · /api/pages/eval = "我们到底验了什么、在多大的数据上验的"
    """
    from pages import eval_summary
    return eval_summary()


@app.get('/api/pages/nav')
def page_nav():
    """页面清单，给前端渲染导航。

    【为什么要由后端给导航，而不是前端写死一个数组】
    每个页面的副标题里都带着当前可选的维度（类目、实体、周次）。
    写死的话，数据一改（比如加了一个类目）前端就得跟着改，
    而这类"改了后端忘改前端"的事在这个项目里已经发生过。
    """
    hub = get_hub()
    from tools import review_tools as rt
    from pages import suggest_week
    return {
        'pages': [
            {'key': 'dashboard', 'title': '总览',
             'subtitle': '现在怎么样、哪里最疼', 'icon': 'grid'},
            {'key': 'attribution', 'title': '归因',
             'subtitle': '这一周为什么涨了', 'icon': 'route'},
            {'key': 'reasons', 'title': '原因治理',
             'subtitle': '9 个原因各自值不值得管', 'icon': 'layers'},
            {'key': 'reviews', 'title': '评论洞察',
             'subtitle': '情感 · 抱怨点 · 画像', 'icon': 'message'},
            {'key': 'competitors', 'title': '竞品雷达',
             'subtitle': '我们在同类里处在什么位置', 'icon': 'target'},
            {'key': 'eval', 'title': '自检',
             'subtitle': '验了什么、在多大的数据上验的', 'icon': 'check'},
            {'key': 'chat', 'title': '问答',
             'subtitle': '按需深入，让 Agent 自己查', 'icon': 'spark'},
        ],
        'categories': hub.categories,
        # 【给前端的是实体名的扁平列表，不是 rt.entities() 的原样】
        # 原样返回也行，但那会把 entity_id / mode / compliance 一起塞给导航栏，
        # 而导航栏只需要一个能点下去的名字。
        # 需要详情（哪个是竞品、多少条）时用 /api/pages/competitors。
        'entities': [e['entity_name'] for e in rt.entities()],
        'weeks': [int(w) for w in hub.weeks],
        'latest_week': int(hub.latest_week),
        # 【"打开哪一周"必须由后端决定，不能让前端自己猜】
        # 前端第一版写的是 `n.weeks.filter(w => w > 0)`，取最后一个——
        # 那其实是 latest_week（第 52 周），而最新一周常常是正常周。
        # 于是归因页默认打开一个"没什么可解释"的周，
        # 双门槛判定两块都是"不显著/不值得管"，看起来像系统没生效。
        # 这里用和判定完全相同的 _diagnose_all，挑出**幅度最大的异常周**。
        'default_week': suggest_week(),
    }


# ---------------------------------------------------------------------------
# 静态前端（构建产物存在时直接托管，现场演示不需要跑两个终端）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 启动预热
# ---------------------------------------------------------------------------
#
# 【为什么要有这一步，而不是"第一次访问时再算"】
# 六个页面里有两个是"首次很贵"：
#   · 总览：53 次 diagnose_week，约 900ms（之后 20ms）
#   · 评论：jieba 首次加载 + 27,000 条分词，约 5.5s（之后 60ms）
# 如果留到首次访问，用户点开"评论洞察"要盯着空白屏等 5 秒，
# 而且会以为是自己网慢或者程序卡了。
#
# 【为什么放后台线程，不放在启动时同步跑】
# 同步跑会把 /api/health 从 80ms 拖到 6 秒 ——
# 健康检查是用来判断"服务起来了没有"的，它必须立刻回。
# 放后台线程，前端打完健康检查再点页面时，预热通常已经完成。
#
# 【为什么失败只记日志不抛出】
# 预热是优化，不是功能。预热失败最多是首次访问慢一点，
# 不应该让整个服务起不来。
@app.on_event('startup')  # noqa: deprecated（本机 FastAPI 版本仍支持）
def _warmup() -> None:
    """后台预热最贵的两页，让首屏不承担冷启动成本。"""
    import threading
    import time as _t

    def _run() -> None:
        try:
            from pages import dashboard, review_overview
            t0 = _t.time()
            dashboard()
            t1 = _t.time()
            review_overview('本店')
            t2 = _t.time()
            print(f'[warmup] dashboard {int((t1 - t0) * 1000)}ms · '
                  f'reviews {int((t2 - t1) * 1000)}ms', flush=True)
        except Exception as e:          # noqa: BLE001
            print(f'[warmup] 跳过（不影响服务）：{e}', flush=True)

    threading.Thread(target=_run, name='warmup', daemon=True).start()


_DIST = ROOT.parent / 'frontend' / 'dist'
if _DIST.is_dir():
    app.mount('/', StaticFiles(directory=str(_DIST), html=True), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8848, log_level='info')