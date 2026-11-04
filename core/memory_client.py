# -*- coding: utf-8 -*-
"""【L9 记忆服务独立进程】远程记忆客户端

与 UnifiedMemorySystem duck-typing 兼容：memory.remote.enabled 开启时，
MiaoyiCore 用本类替换本地 UnifiedMemorySystem，所有记忆读写经 HTTP 走独立
记忆服务进程（memory_server.py），实现 聊天服务/记忆服务/存储 三分离与跨端共享。

降级策略（记忆不是关键路径，绝不挂主链路）：
- 读失败 → 返回空值并记 warning
- 写失败 → 记 warning 后丢弃（下次交互可自然重建）
"""
import logging

import requests

_log = logging.getLogger("MemoryClient")

_DEFAULT_TIMEOUT = 3.0


class _RemoteSemantic:
    """semantic 层远程代理（server.py 的记忆卡片路由直接访问 memory.semantic.*）"""

    def __init__(self, client):
        self._c = client

    def forget(self, key: str, user_id: str = '') -> bool:
        return self._c._post('/semantic/forget', {'key': key, 'user_id': user_id}, default=False)

    def update_value(self, key: str, value, user_id: str = '') -> bool:
        return self._c._post('/semantic/update_value', {'key': key, 'value': value, 'user_id': user_id}, default=False)

    def set_pinned(self, key: str, pinned: bool, user_id: str = '') -> bool:
        return self._c._post('/semantic/set_pinned', {'key': key, 'pinned': pinned, 'user_id': user_id}, default=False)


class MemoryClient:
    """UnifiedMemorySystem 的远程透明代理"""

    def __init__(self, base_url: str, token: str = '', timeout: float = _DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = timeout
        # server.py 的 L1 记忆卡片路由直接访问 memory.semantic.*，保持同名子对象
        self.semantic = _RemoteSemantic(self)
        self._session = requests.Session()
        if token:
            self._session.headers['X-Memory-Token'] = token

    # ------------------------------------------------------------------
    def _post(self, path: str, payload: dict, default=None):
        try:
            r = self._session.post(f'{self.base_url}{path}', json=payload, timeout=self.timeout)
            if r.status_code == 200:
                return r.json().get('data', default)
            _log.warning(f"[记忆服务] POST {path} → {r.status_code}: {r.text[:120]}")
        except Exception as e:
            _log.warning(f"[记忆服务] POST {path} 失败（降级）: {e}")
        return default

    def _get(self, path: str, params: dict = None, default=None):
        try:
            r = self._session.get(f'{self.base_url}{path}', params=params, timeout=self.timeout)
            if r.status_code == 200:
                return r.json().get('data', default)
            _log.warning(f"[记忆服务] GET {path} → {r.status_code}")
        except Exception as e:
            _log.warning(f"[记忆服务] GET {path} 失败（降级）: {e}")
        return default

    # ------------------------------------------------------------------
    # 写接口
    def remember(self, key: str, value, user_id: str = ''):
        self._post('/remember', {'key': key, 'value': value, 'user_id': user_id})

    def forget(self, key: str, user_id: str = ''):
        self._post('/forget', {'key': key, 'user_id': user_id})

    def extract_from_text(self, text: str, user_id: str = ''):
        return self._post('/extract_from_text', {'text': text, 'user_id': user_id}, default=[])

    def add_conv_summary(self, user_text: str, bot_text: str, intent: str = '',
                         emotion: str = '', user_id: str = ''):
        self._post('/add_conv_summary', {'user_text': user_text, 'bot_text': bot_text,
                                         'intent': intent, 'emotion': emotion, 'user_id': user_id})

    # 读接口
    def get_facts(self, user_id: str = '') -> dict:
        return self._get('/facts', {'user_id': user_id}, default={})

    def get_relevant_memories(self, query: str, limit: int = 5, user_id=None) -> list:
        return self._get('/relevant', {'query': query, 'limit': limit, 'user_id': user_id or ''},
                         default=[])

    def get_context_str(self, user_id: str = '') -> str:
        return self._get('/context_str', {'user_id': user_id}, default='')

    def get_user_profile(self, user_id: str = '') -> str:
        return self._get('/profile', {'user_id': user_id}, default='')

    def get_memory_context_bundle(self, user_id: str = '', current_text: str = '') -> tuple:
        data = self._get('/bundle', {'user_id': user_id, 'current_text': current_text},
                         default={'memory_ctx': '', 'user_profile': '', 'facts': {}})
        # 返回结构与本地 UnifiedMemorySystem 一致的三元组
        if isinstance(data, dict):
            return (data.get('memory_ctx', ''), data.get('user_profile', ''),
                    data.get('facts', {}))
        return ('', '', {})

    def get_stats(self) -> dict:
        return self._get('/stats', default={}) or {}

    def health(self) -> bool:
        return self._get('/health', default=False) is True

    # 生命周期（远程模式下本地 save/load 为空操作，记忆服务进程自管）
    def save(self):
        pass

    def load(self):
        pass

    def shutdown(self):
        self._post('/shutdown', {})
