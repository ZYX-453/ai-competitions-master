"""LLM 客户端：OpenAI 兼容的 chat/completions 调用。

【为什么手写而不用 openai SDK】
本机没装 requests / httpx / openai，pip 索引又不通（见项目记忆）。
而 urllib 是标准库，零依赖、零安装，反而最稳。
代价是要自己处理超时、重试和 JSON 解析——下面都补上了。

【为什么默认 base_url 指向 DashScope】
用户给的是 sk-ws- 开头的 key，实测：
  · api.deepseek.com   → 401 invalid api key
  · api.moonshot.cn    → 401
  · api.openai.com     → 网络不通
  · dashscope.aliyuncs.com → 200（qwen-plus / qwen-max 可用）
所以默认走阿里云百炼的 OpenAI 兼容端点。
换厂商只需设 CANYU_LLM_BASE + CANYU_LLM_MODEL，代码不用动。

【为什么所有失败都返回 None 而不是抛异常】
这个模块挂在「生成答案」的关键路径上。
LLM 挂了不该让整个请求失败——降级到确定性模板照样能出结论，
而且用户必须知道降级了（前端会显示"模板组装"而不是模型名）。
把异常吞掉但记录原因，比让页面白屏好。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

# 首选 qwen-plus：qwen-max 更贵，qwen-turbo 实测会被限流（429）
DEFAULT_BASE = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
DEFAULT_MODEL = 'qwen-plus'

# 超时给足：一次生成 600 字左右，qwen-plus 通常 20–40s
_TIMEOUT = float(os.environ.get('CANYU_LLM_TIMEOUT', '90'))
# 重试次数。只对可重试的错误重试（超时 / 429 / 5xx），
# 401/403 这类认证问题重试没意义，直接失败。
_RETRIES = int(os.environ.get('CANYU_LLM_RETRIES', '2'))


class LLMClient:
    def __init__(self, key: str, base: str | None = None, model: str | None = None):
        self.key = key
        self.base = (base or DEFAULT_BASE).rstrip('/')
        self.model = model or DEFAULT_MODEL
        # 最近一次失败原因，供健康检查与前端提示使用
        self.last_error = ''

    # ---------------- 底层调用 ----------------
    def _post(self, payload: dict) -> dict | None:
        url = f'{self.base}/chat/completions'
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        req = urllib.request.Request(url, data=body, headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.key}',
        })
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            return json.loads(r.read().decode('utf-8'))

    def chat(self, messages: list[dict], temperature: float = 0.3,
             max_tokens: int = 1400, json_mode: bool = False) -> str | None:
        """返回模型文本；任何失败返回 None，并把原因写进 last_error。"""
        payload = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if json_mode:
            # 让模型只吐 JSON。不是所有厂商都支持，失败会自动回退到文本解析。
            payload['response_format'] = {'type': 'json_object'}

        for attempt in range(_RETRIES + 1):
            try:
                data = self._post(payload)
                if not data:
                    self.last_error = 'empty response'
                    return None
                choices = data.get('choices') or []
                if not choices:
                    self.last_error = f'no choices: {str(data)[:160]}'
                    return None
                content = (choices[0].get('message') or {}).get('content') or ''
                if not content.strip():
                    self.last_error = 'blank content'
                    return None
                self.last_error = ''
                return content
            except urllib.error.HTTPError as e:
                detail = e.read()[:300].decode('utf-8', 'ignore')
                self.last_error = f'HTTP {e.code}: {detail}'
                # 认证 / 参数错误重试无意义
                if e.code in (400, 401, 403, 404):
                    return None
                if attempt >= _RETRIES:
                    return None
            except Exception as e:
                self.last_error = f'{type(e).__name__}: {e}'
                if attempt >= _RETRIES:
                    return None
            # 指数退避：1s, 2s。限流时给服务端一点时间。
            time.sleep(1.0 * (attempt + 1))
        return None

    def ok(self) -> bool:
        """一次极轻量的连通性探测，用于健康检查。"""
        r = self.chat([{'role': 'user', 'content': 'ping'}],
                      temperature=0, max_tokens=4)
        return r is not None


def from_env() -> LLMClient | None:
    """从环境变量构造客户端。没配 key 返回 None（走确定性引擎）。"""
    key = (os.environ.get('CANYU_LLM_KEY')
           or os.environ.get('DASHSCOPE_API_KEY')
           or os.environ.get('DEEPSEEK_API_KEY')
           or os.environ.get('OPENAI_API_KEY'))
    if not key:
        return None
    base = os.environ.get('CANYU_LLM_BASE') or DEFAULT_BASE
    model = os.environ.get('CANYU_LLM_MODEL') or DEFAULT_MODEL
    return LLMClient(key, base, model)