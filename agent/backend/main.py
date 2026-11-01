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
# 静态前端（构建产物存在时直接托管，现场演示不需要跑两个终端）
# ---------------------------------------------------------------------------

_DIST = ROOT.parent / 'frontend' / 'dist'
if _DIST.is_dir():
    app.mount('/', StaticFiles(directory=str(_DIST), html=True), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=8848, log_level='info')