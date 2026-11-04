# -*- coding: utf-8 -*-
"""多智能体池角色路由单测（独立可运行：python tests/test_llm_pool.py）
验证：角色优先跳转、回退链、显式 model 抑制角色、全冷却兜底、模型名防串发。"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.llm_provider import MultiProviderLLM


def main():
    calls = []

    class FakeClient:
        def __init__(self, name, models):
            self.name, self.models = name, models

        def chat(self, model=None, messages=None, **kw):
            calls.append((self.name, model))
            assert model in self.models, f'{self.name} 收到不支持的模型 {model}'
            return 'ok'

    entries = [
        {'provider': 'zhipu', 'client': FakeClient('zhipu', ['glm-4-flash']),
         'model_name': 'glm-4-flash', 'label': 'z'},
        {'provider': 'qwen', 'client': FakeClient('qwen', ['qwen-plus', 'qwen-turbo']),
         'model_name': 'qwen-plus', 'label': 'q'},
    ]
    roles = {'think': {'model': 'qwen-plus'},
             'chat': {'model': 'glm-4-flash'},
             'emotion': {'model': 'glm-4-flash'}}
    pool = MultiProviderLLM(entries, roles=roles)

    # 1. think 角色优先跳到 qwen（角色模型在池中命中）
    pool.chat(role='think', messages=[{'role': 'user', 'content': 'x'}])
    assert calls[-1] == ('qwen', 'qwen-plus'), calls

    # 2. chat 角色走主供应商
    pool.chat(role='chat', messages=[{'role': 'user', 'content': 'x'}])
    assert calls[-1] == ('zhipu', 'glm-4-flash'), calls

    # 3. 未区分角色（memory 无差异配置）回退主条目
    pool.chat(role='memory', messages=[{'role': 'user', 'content': 'x'}])
    assert calls[-1] == ('zhipu', 'glm-4-flash'), calls

    # 4. 目标供应商冷却 → 角色调用的回退链（zhipu 用自家模型，不会串发 qwen 模型名）
    pool._cooldown_until[1] = time.time() + 60
    pool.chat(role='think', messages=[{'role': 'user', 'content': 'x'}])
    assert calls[-1] == ('zhipu', 'glm-4-flash'), calls
    pool._cooldown_until[1] = 0

    # 5. 显式 model 抑制角色路由
    pool.chat(model='glm-4-flash', role='think', messages=[{'role': 'user', 'content': 'x'}])
    assert calls[-1] == ('zhipu', 'glm-4-flash'), calls

    # 6. 故障转移：qwen 429 → 冷却并回退 zhipu
    class Fail429:
        def chat(self, **kw):
            raise RuntimeError('HTTP 429: rate limited')
    entries[1]['client'] = Fail429()
    pool.chat(role='think', messages=[{'role': 'user', 'content': 'x'}])
    assert calls[-1] == ('zhipu', 'glm-4-flash'), calls
    assert pool._cooldown_until[1] > time.time(), '429 后应冷却'

    print('LLM POOL TESTS: 6/6 PASS')


if __name__ == '__main__':
    main()
