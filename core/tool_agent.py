# -*- coding: utf-8 -*-
"""工具智能体（Tool Agent）——喵艺"能办事"的大脑区

借鉴星黎/小智的 MCP 工具调用思路，落地为轻量两层：
  1. 决策：role='tool' 小模型按工具清单输出 JSON（tool + args），
     前置正则预过滤省延迟（无工具意图的消息 0 开销）
  2. 执行：调用本地已有能力（天气爬虫/时间/搜索），结果以
     "[系统提示] 工具结果..." 注入对话上下文，让喵艺自然转述

配置：llm_apis.yaml roles.tool（模型）+ agents.tool_agent（开关/白名单）。
与星黎 MCP 的差异：无外部设备控制，工具集为服务端内置能力。
"""
import json
import logging
import re
import time
from datetime import datetime

_log = logging.getLogger(__name__)

# 工具意图预过滤（正则快筛，命中才走 LLM 决策；防每条消息都多一次调用）
_TOOL_HINT_RE = re.compile(
    r'天气|气温|下雨|温度|几点|现在时间|今天几号|日期|星期|搜索|搜一下|'
    r'查一下|查查|百度|热搜|新闻|网址|是多少|怎么样了'
)


def _fmt_weather(data: dict) -> str:
    """天气 dict → 一句话摘要（WebCrawler.get_weather 返回结构自适应）"""
    try:
        # 常见返回结构兼容：{city, temperature, weather, ...} 或嵌套 data
        d = data.get('data', data)
        city = d.get('city', '')
        temp = d.get('temperature', d.get('temp', ''))
        desc = d.get('weather', d.get('description', d.get('condition', '')))
        bits = [str(x) for x in (city, desc, f'{temp}°C' if temp not in ('', None) else '') if x]
        return ' '.join(bits) if bits else json.dumps(data, ensure_ascii=False)[:150]
    except Exception:
        return json.dumps(data, ensure_ascii=False)[:150]


class ToolAgent:
    """工具决策与执行器（无状态，注入依赖后即可用）"""

    def __init__(self, llm_caller=None, web_crawler_factory=None):
        """
        llm_caller: (messages, temperature, max_tokens, timeout, role) -> str
                    通常注入 miaoyi_core._safe_llm_call（role='tool' 路由）
        web_crawler_factory: () -> WebCrawler（延迟取全局实例）
        """
        self._llm_caller = llm_caller
        self._crawler_factory = web_crawler_factory

    # ---------- 工具清单（决策 prompt 与执行共用） ----------
    TOOLS_DESC = (
        "1. get_weather —— 查询某城市实时天气。参数: {\"city\": \"城市名\"}\n"
        "2. get_time —— 查询当前日期时间和星期。参数: {}\n"
        "3. web_search —— 搜索实时网络信息（新闻/热点/事实）。参数: {\"query\": \"关键词\"}\n"
    )

    # ---------- 决策 ----------
    def _rule_decide(self, text: str) -> list[dict]:
        """规则兜底：LLM 决策不稳定（尤其多工具 JSON）时按正则直提取。
        覆盖最高频的两种：时间、天气+城市。"""
        out = []
        if re.search(r'几点|现在时间|今天.{0,4}号|星期[一二三四五六日天]', text):
            out.append({'tool': 'get_time', 'args': {}})
        m = re.search(r'([\u4e00-\u9fa5]{2,6})(?:今天|明天|现在)?的?(?:天气|气温|温度|下不下雨)', text)
        if m:
            city = m.group(1)
            # 去掉常见动词/语气前缀
            for pre in ('看看', '查查', '帮我', '我想知道', '请问', '一下'):
                if city.startswith(pre):
                    city = city[len(pre):]
            if 1 < len(city) < 8:
                out.append({'tool': 'get_weather', 'args': {'city': city}})
        return out

    def decide(self, text: str, enabled: bool = True) -> list[dict]:
        """判断消息需要调用哪些工具（支持一次多工具）。

        返回 [{'tool':..., 'args':{...}}, ...]；无需工具返回空列表。"""
        if not enabled or not _TOOL_HINT_RE.search(text or ''):
            return []
        if not self._llm_caller:
            return []
        try:
            prompt = (
                "你是喵艺的工具调度器。判断用户消息要不要调用工具（可多个）。\n"
                "只输出 JSON：{\"tools\": [{\"tool\": \"工具名\", \"args\": {...}}]}；"
                "不需要工具就输出 {\"tools\": []}。\n"
                f"可用工具：\n{self.TOOLS_DESC}\n"
                "要求：\n"
                "- 只输出 JSON 本体：不要代码块标记、不要解释、不要前后缀\n"
                "- 无参数的工具 args 写 {}；城市/关键词不确定时省略该字段\n"
                "- 闲聊、情绪表达、追问上下文、评价类说法一律不调工具\n"
                f"用户消息：「{str(text)[:150]}」"
            )
            raw = self._llm_caller(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=160, timeout=10, role='tool')
            raw = (raw or '').strip()
            m = re.search(r'\{.*\}', raw, re.S)
            if not m:
                return []
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                # 【容错】小模型常把多工具写成非法 JSON（两个对象并列）：
                # 直接抓取所有 {"tool": "xxx", "args": {...}} 片段
                data = {'tools': []}
                for tm in re.finditer(r'\{"tool"\s*:\s*"([a-z_]+)"\s*,\s*"args"\s*:\s*(\{[^{}]*\})\}', m.group(0)):
                    args = {}
                    try:
                        args = json.loads(tm.group(2))
                    except (ValueError, TypeError):
                        pass
                    data['tools'].append({'tool': tm.group(1), 'args': args})
            tools = data.get('tools')
            # 兼容旧单工具格式
            if isinstance(tools, dict):
                tools = [tools]
            out = []
            for t in tools or []:
                if isinstance(t, dict):
                    name = str(t.get('tool', 'none')).strip()
                    if name not in ('none', '', 'null'):
                        out.append({'tool': name, 'args': t.get('args') or {}})
            # 【规则补全】LLM 对多工具输出不稳定（"几点+天气"常只答一个）：
            # 正则规则命中的高频工具若被遗漏则补全，合并去重
            for r in self._rule_decide(text):
                if not any(x['tool'] == r['tool'] for x in out):
                    out.append(r)
            return out[:3]  # 上限 3 个，防幻觉刷工具
        except Exception as e:
            _log.debug(f"[ToolAgent] 决策失败（跳过工具）: {e}")
            return []

    # ---------- 执行 ----------
    def execute(self, decision: dict) -> str | None:
        """执行工具决策，返回人类可读结果文本；失败返回 None。"""
        tool = decision.get('tool', '')
        args = decision.get('args') or {}
        try:
            if tool == 'get_time':
                now = datetime.now()
                weekdays = '一二三四五六日'
                return (f"当前时间 {now.strftime('%Y年%m月%d日 %H:%M')}，"
                        f"星期{weekdays[now.weekday()]}")
            if tool == 'get_weather':
                if not self._crawler_factory:
                    return None
                city = str(args.get('city', '') or '北京').strip()
                data = self._crawler_factory().get_weather(city)
                if not data:
                    return None
                return f'{city}实时天气：{_fmt_weather(data)}'
            if tool == 'web_search':
                if not self._crawler_factory:
                    return None
                query = str(args.get('query', '')).strip()
                if not query:
                    return None
                data = self._crawler_factory().get_knowledge(query)
                if not data:
                    return None
                d = data.get('data', data)
                text = d.get('answer') or d.get('summary') or json.dumps(d, ensure_ascii=False)
                return f'网络检索「{query}」：{str(text)[:300]}'
            return None
        except Exception as e:
            _log.warning(f"[ToolAgent] 执行 {tool} 失败: {e}")
            return None

    def handle(self, text: str, enabled: bool = True) -> tuple[str, str] | None:
        """决策+执行一步完成（支持多工具）。返回 (工具名串, 合并结果) 或 None。"""
        t0 = time.time()
        decisions = self.decide(text, enabled=enabled)
        if not decisions:
            return None
        names = []
        results = []
        for d in decisions:
            result = self.execute(d)
            if result:
                names.append(d['tool'])
                results.append(f"【{d['tool']}】{result}")
        if not results:
            return None
        _log.info(f"[ToolAgent] {','.join(names)} 完成（{time.time()-t0:.1f}s）: {results[0][:60]}")
        return '+'.join(names), '\n'.join(results)
