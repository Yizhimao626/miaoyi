# -*- coding: utf-8 -*-
"""回归测试：遗留模块必须使用 provider 适配层签名 chat(messages=..., ...)。

2026-10-28 整体检查发现 thought_bubbles / search_strategy 仍以 chat(prompt) 调用，
prompt 被当成 model 名、messages 为空 → 智谱返回 400，异常被静默兜底，
导致内心独白恒走罐头文案、搜索 LLM 判定/改写完全失效。
此处锁死调用形状，防止后续回退到旧接口。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.thought_bubbles import ThoughtBubbles
from core.search_strategy import SearchStrategy


class _RecordingPool:
    """记录调用参数的假 LLM 池（模拟 MultiProviderLLM）。"""

    def __init__(self, reply: str):
        self.calls = []
        self._reply = reply

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self._reply


def _bare_thought_bubbles(pool):
    """跳过 __init__，避免构造时启动后台思考线程。"""
    tb = object.__new__(ThoughtBubbles)
    tb._llm = pool
    tb._emotional_core = None
    tb._sensory_language = None
    tb._bubbles = []
    tb._on_thought_generated = None
    tb._on_thought_shared = None
    return tb


def test_thought_bubble_uses_provider_signature():
    pool = _RecordingPool('（测试独白，心里暖暖的）')
    tb = _bare_thought_bubbles(pool)

    tb._generate_thought()

    assert pool.calls, '思想气泡未调用 LLM'
    call = pool.calls[0]
    assert 'model' not in call, 'prompt 不能再被当作 model 传入'
    assert call['messages'][0]['role'] == 'user'
    assert call['messages'][0]['content']
    assert call['role'] == 'inner'
    assert tb.get_recent_thoughts(1), '应生成成功（而非兜底）'


def test_search_assess_uses_provider_signature():
    pool = _RecordingPool('1')
    strategy = SearchStrategy(llm_caller=pool)

    score = strategy._llm_assess('最近有什么新电影上映')

    assert pool.calls, '搜索判定未调用 LLM'
    call = pool.calls[0]
    assert 'model' not in call
    assert call['messages'][0]['content']
    assert call['role'] == 'light'
    assert score == 3.0


def test_search_rewrite_uses_provider_signature():
    pool = _RecordingPool('人工智能的定义、应用领域和发展历史')
    strategy = SearchStrategy(llm_caller=pool)

    rewritten = strategy._llm_rewrite_query('AI')

    assert pool.calls, '搜索词改写未调用 LLM'
    assert 'model' not in pool.calls[0]
    assert pool.calls[0]['messages'][0]['content']
    assert rewritten and len(rewritten) > 5
