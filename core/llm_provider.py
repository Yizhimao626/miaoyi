"""LLM Provider 适配层（多供应商池 + 视觉）

借鉴 Kirara AI / LobeChat 的适配器模式：把具体 LLM 服务商从业务代码中解耦。
业务层只依赖统一的 chat() 接口。

【多 API 池】config.yaml llm.providers 列表按 priority 顺序故障转移：
主供应商 429/5xx/超时/网络错误 → 自动切下一个供应商（用它自己的模型）。
列表里 api_key_env 对应的环境变量未配置的条目在构建期自动跳过，
因此可以预置多家供应商而无需所有用户都配齐 key。

【视觉】llm.vision 独立配置（provider: openai-compat），chat_vision()
接受 base64 data URL / 公网 URL 的 OpenAI 视觉消息格式。
智谱 GLM-4V-Flash 免费，与主 key 通用。

错误约定：chat() 抛出的异常 message 必须包含 HTTP 状态码原文，
上层 _safe_llm_call 依赖字符串匹配 '429'/'500' 等决定重试策略。
"""
import base64
import json
import logging
import os
import time
from typing import Any, Optional

import requests

_log = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 60  # 单次 HTTP 请求硬超时（秒），防止悬挂
_FAILABLE_MARKERS = ('429', '500', '502', '503', '504', 'timeout', 'timed out',
                     'connection', 'rate limit', 'quota', 'exceeded',
                     '401', '403')  # key 失效/被封也要切换（400 参数错除外——换家也没用）


class ZhipuProvider:
    """智谱 GLM - 官方 zhipuai SDK"""

    name = 'zhipu'

    def __init__(self, api_key: str):
        try:
            from zhipuai import ZhipuAI
        except ImportError as e:
            raise RuntimeError('未安装 zhipuai SDK (pip install zhipuai)') from e
        self._client = ZhipuAI(api_key=api_key)

    def chat(self, model: str, messages: list[dict], temperature: float,
             max_tokens: int, timeout: float) -> str:
        response = self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        content = response.choices[0].message.content
        return content.strip() if isinstance(content, str) else ''

    def chat_stream(self, model: str, messages: list[dict], temperature: float,
                    max_tokens: int, timeout: float):
        """流式生成：yield 文本增量块（zhipuai SDK 原生 stream 支持）。"""
        response = self._client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            stream=True,
        )
        for chunk in response:
            try:
                delta = chunk.choices[0].delta
                piece = getattr(delta, 'content', None)
                if piece:
                    yield piece
            except (IndexError, AttributeError):
                continue


class OpenAICompatProvider:
    """OpenAI 兼容端点 - requests 实现（DeepSeek / Kimi / 硅基流动 /
    DashScope 兼容模式 / 智谱 OpenAI 兼容端点等）"""

    name = 'openai-compat'

    def __init__(self, api_key: str, api_base: str, min_max_tokens: int = 0):
        if not api_base:
            raise RuntimeError('openai-compat 模式必须配置 llm.api_base')
        self._api_base = api_base.rstrip('/')
        self._api_key = api_key
        # 推理模型（DeepSeek V4 等）先花 token 生成 reasoning_content，
        # max_tokens 过小会导致正文为空；配置 min_max_tokens 兜底。
        self._min_max_tokens = max(0, int(min_max_tokens or 0))

    def _budget(self, max_tokens: int) -> int:
        try:
            want = int(max_tokens or 0)
        except (TypeError, ValueError):
            want = 0
        return max(want, self._min_max_tokens)

    def chat(self, model: str, messages: list[dict], temperature: float,
             max_tokens: int, timeout: float) -> str:
        url = f'{self._api_base}/chat/completions'
        headers = {
            'Authorization': f'Bearer {self._api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': self._budget(max_tokens),
        }
        resp = requests.post(
            url, json=payload, headers=headers,
            timeout=min(timeout, _REQUEST_TIMEOUT),
        )
        # 非 200 必须携带状态码抛异常：上层靠 err_str 里的 '429'/'503' 等判断重试
        if resp.status_code != 200:
            body = (resp.text or '')[:300]
            raise RuntimeError(f'HTTP {resp.status_code}: {body}')
        try:
            data = resp.json()
            content = data['choices'][0]['message']['content']
        except (ValueError, KeyError, IndexError) as e:
            raise RuntimeError(f'响应格式非法(HTTP 200): {e}; body={resp.text[:200]}') from e
        return content.strip() if isinstance(content, str) else ''

    def chat_stream(self, model: str, messages: list[dict], temperature: float,
                    max_tokens: int, timeout: float):
        """流式生成：SSE 逐块 yield 文本增量。

        非 200 在首块前抛异常（调用方可回退非流式/下一供应商）。"""
        url = f'{self._api_base}/chat/completions'
        headers = {
            'Authorization': f'Bearer {self._api_key}',
            'Content-Type': 'application/json',
        }
        payload = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': self._budget(max_tokens),
            'stream': True,
        }
        resp = requests.post(
            url, json=payload, headers=headers, stream=True,
            timeout=min(timeout, _REQUEST_TIMEOUT),
        )
        if resp.status_code != 200:
            body = (resp.text or '')[:300]
            raise RuntimeError(f'HTTP {resp.status_code}: {body}')
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith('data:'):
                continue
            data_str = line[5:].strip()
            if data_str == '[DONE]':
                break
            try:
                data = json.loads(data_str)
                delta = data['choices'][0].get('delta', {})
                piece = delta.get('content')
                if piece:
                    yield piece
            except (ValueError, KeyError, IndexError):
                continue

    def chat_vision(self, model: str, messages: list[dict], temperature: float,
                    max_tokens: int, timeout: float) -> str:
        """视觉调用：messages 中 content 为多模态数组（text + image_url）。

        与 chat() 同一端点（OpenAI 多模态格式），仅透传，不做本地校验。"""
        return self.chat(model, messages, temperature, max_tokens, timeout)


def create_llm_provider(provider: str, api_key_env: str = 'ZHIPU_API_KEY',
                        api_base: str = '', min_max_tokens: int = 0) -> Any:
    """根据配置创建单个 LLM provider 实例；失败返回 None（调用方降级处理）

    Args:
        provider: 'zhipu' | 'openai-compat'
        api_key_env: 存放 API Key 的环境变量名
        api_base: openai-compat 模式的 API 基础地址
        min_max_tokens: 推理模型正文预算下限（避免 reasoning 吃光 max_tokens）
    """
    api_key = os.environ.get(api_key_env, '')
    if not api_key:
        _log.warning(f'[LLM Provider] 环境变量 {api_key_env} 未配置 API Key')
        return None

    try:
        if provider == 'openai-compat':
            return OpenAICompatProvider(api_key=api_key, api_base=api_base,
                                        min_max_tokens=min_max_tokens)
        if provider == 'zhipu':
            return ZhipuProvider(api_key=api_key)
        _log.warning(f'[LLM Provider] 未知 provider: {provider}，回退 zhipu')
        return ZhipuProvider(api_key=api_key)
    except Exception as e:
        _log.warning(f'[LLM Provider] 创建 {provider} 失败: {e}')
        return None


class MockProvider:
    """离线演示提供方：一个 API Key 都没配时兜底，返回内置示例回复。

    只用于本地开发与开源演示（无 Key 也能跑通登录 → 聊天 → 记忆全链路），
    不发起任何网络请求。生产环境（ENVIRONMENT=production）默认不启用，
    需要强制启用时设置 MIAOYI_MOCK_LLM=1。
    子任务角色单独处理：tool 返回空工具决策、light 返回 0，
    避免演示模式下误触发工具调用与联网搜索。"""

    name = 'mock'

    _CHAT_REPLIES = (
        '喵～听到你说「{echo}」啦。不过现在是离线演示模式（还没配置 API Key），'
        '我先这样回应你，填上 Key 之后我就能好好陪你聊了。',
        '「{echo}」……喵，我在认真听哦。离线演示模式下的回复是内置示例文本，'
        '把 ZHIPU_API_KEY 或 DEEPSEEK_API_KEY 配上，我就能真正回答你了。',
        '喵喵～「{echo}」我记住啦。现在是离线演示模式，'
        '等铲屎官配好 Key，我再给你更聪明的回答。',
    )

    def __init__(self):
        self._turn = 0

    def chat(self, model: str = '', messages: list[dict] = None,
             temperature: float = 0.7, max_tokens: int = 1000,
             timeout: float = 30, role: str = None, **_ignored) -> str:
        return self._reply(messages or [], role)

    def chat_stream(self, model: str = '', messages: list[dict] = None,
                    temperature: float = 0.7, max_tokens: int = 1000,
                    timeout: float = 30, role: str = None, **_ignored):
        """逐块吐字，模拟流式打字效果（小块 + 微延时）。"""
        text = self._reply(messages or [], role)
        for i in range(0, len(text), 6):
            yield text[i:i + 6]
            time.sleep(0.02)

    def _reply(self, messages: list[dict], role: str = None) -> str:
        text = self._last_user_text(messages)
        # 子任务：工具决策要 JSON，搜索判定要数字——按提示词特征识别
        # （池内调用不转发 role，只能看提示词）
        if role == 'tool' or ('"tools"' in text and 'json' in text.lower()):
            return '{"tools": []}'
        if role == 'light' or '请仅回答数字' in text:
            return '0'
        echo = self._echo(text)
        self._turn = (self._turn + 1) % len(self._CHAT_REPLIES)
        return self._CHAT_REPLIES[self._turn].format(echo=echo)

    @staticmethod
    def _last_user_text(messages: list[dict]) -> str:
        """最后一条用户消息的完整文本（组合提示词可能混入系统设定）。"""
        for m in reversed(messages):
            if m.get('role') == 'user' and isinstance(m.get('content'), str):
                return m['content']
        return ''

    @staticmethod
    def _echo(text: str) -> str:
        """提取真实用户话语用于回显：优先「用户消息」标记之后的内容，
        过长（多半是组合提示词）则放弃回显。"""
        for marker in ('用户消息', '用户说', '用户输入'):
            idx = text.rfind(marker)
            if idx != -1:
                text = text[idx + len(marker):]
                break
        text = text.strip().lstrip('：:').strip()
        return text[:24] if len(text) <= 60 else '你'


def _mock_enabled() -> bool:
    """离线演示模式开关：MIAOYI_MOCK_LLM 显式设置优先；
    未设置时仅非生产环境自动启用（本地开发 / 开源演示）。"""
    flag = os.environ.get('MIAOYI_MOCK_LLM', '').strip().lower()
    if flag in ('1', 'true', 'yes', 'on'):
        return True
    if flag in ('0', 'false', 'no', 'off'):
        return False
    return os.environ.get('ENVIRONMENT', '').strip().lower() not in ('production', 'prod')


def _build_mock_pool(roles: dict = None) -> Any:
    """构造只含 MockProvider 的供应商池（接口与 MultiProviderLLM 一致）。"""
    _log.warning('[LLM Pool] 未配置任何 API Key → 启用离线演示模式'
                 '（回复为内置示例文本，非真实模型；MIAOYI_MOCK_LLM=0 可关闭）')
    return MultiProviderLLM([{
        'provider': 'mock',
        'client': MockProvider(),
        'api_key': '',
        'api_base': '',
        'model_name': 'mock-offline',
        'label': 'mock:offline-demo',
    }], roles=roles)


# ============================================================
# 多供应商池
# ============================================================

_AGENTS_FILE = 'llm_apis.yaml'


def load_agents_config(base_dir: str = '') -> dict:
    """读取智能体分工配置：roles（脑区→模型）/ agents（开关）/ embeddings。

    来源优先级：
    1. llm_apis.yaml（旧独立文件，存在则优先——向后兼容）
    2. config.yaml 的 llm 段（当前标准位置：llm.roles / llm.agents / llm.embeddings）
    均不存在时返回 {}（调用方回退 config.yaml 的 llm 段）。"""
    import yaml
    path = os.path.join(base_dir or os.getcwd(), _AGENTS_FILE)
    try:
        if os.path.isfile(path):
            data = yaml.safe_load(open(path, encoding='utf-8')) or {}
            if isinstance(data, dict):
                return data
        # 标准来源：config.yaml llm 段
        cfg_path = os.path.join(base_dir or os.getcwd(), 'config.yaml')
        if os.path.isfile(cfg_path):
            cfg = yaml.safe_load(open(cfg_path, encoding='utf-8')) or {}
            llm = (cfg.get('llm') or {}) if isinstance(cfg, dict) else {}
            out = {}
            for key in ('roles', 'agents', 'embeddings'):
                if llm.get(key):
                    out[key] = llm[key]
            return out
        return {}
    except Exception as e:
        _log.warning(f'[LLM Agents] 读取智能体配置失败: {e}')
        return {}


class MultiProviderLLM:
    """多供应商 LLM 池：按 priority 顺序故障转移 + 按角色路由模型。

    对业务层完全兼容旧单供应商接口：
      chat(model=..., messages=..., temperature=..., max_tokens=..., timeout=...)
    新增角色路由：chat(role='think', ...) 按 llm_apis.yaml 的 roles 表选择
    该脑区的模型——roles 里的模型名在池中能找到就用那条（享受故障转移），
    找不到回退主供应商自带模型。
    主供应商失败（429/5xx/超时/网络）自动切换下一个供应商；
    调用方显式指定的 model 只对主供应商生效，fallback 使用各供应商自己的
    model_name（避免把 glm 专属模型名发给 qwen/deepseek 导致 400）。
    """

    name = 'multi'

    # 故障转移后的供应商冷却期（秒）：冷却期内跳过，避免每次调用都先撞超时
    _COOLDOWN_SECONDS = 60

    def __init__(self, entries: list[dict], roles: dict = None):
        """entries: [{provider, api_key, api_base, model_name, label}] 已就绪的供应商
        roles: 智能体分工表 {role: {model: xxx}}（来自 llm_apis.yaml）"""
        if not entries:
            raise RuntimeError('供应商池为空')
        self._entries = entries
        self._primary_model = entries[0].get('model_name', '')
        self._roles = roles or {}
        self._cooldown_until = [0.0] * len(entries)
        self._role_warned: set[str] = set()

    @property
    def providers_desc(self) -> str:
        return ' → '.join(e.get('label', e['provider']) for e in self._entries)

    def status(self) -> dict:
        """池运行状态（供管理端监控）：供应商/冷却剩余/角色分工/智能体开关。"""
        now = time.time()
        return {
            'providers': [
                {
                    'label': e.get('label', ''),
                    'provider': e.get('provider', ''),
                    'model_name': e.get('model_name', ''),
                    'is_primary': i == 0,
                    'cooldown_remaining': max(0, round(self._cooldown_until[i] - now, 1)),
                }
                for i, e in enumerate(self._entries)
            ],
            'roles': self._roles,
        }

    def _resolve_role_model(self, role: str) -> Optional[str]:
        """角色 → 模型名：池中存在该模型的条目才生效，否则回退 None（用主条目）。"""
        role_cfg = self._roles.get(role) or {}
        want = str(role_cfg.get('model', '') or '').strip()
        if not want:
            return None
        for e in self._entries:
            if e.get('model_name', '') == want:
                return want
        if role not in self._role_warned:
            self._role_warned.add(role)
            _log.warning(f'[LLM Pool] 角色 {role} 的模型 {want} 不在池中，回退主模型'
                         f'（检查 llm_apis.yaml providers 是否包含该模型且 key 已配置）')
        return None

    def chat(self, model: str = None, messages: list[dict] = None,
             temperature: float = 0.7, max_tokens: int = 1000,
             timeout: float = 30, role: str = None, **_ignored) -> str:
        if messages is None:
            messages = []
        # 模型三态：调用方显式 model（仅主供应商生效）> 角色模型（匹配池内同名
        # 模型的条目，非主也生效）> 各条目自带模型
        # 模型三态：调用方显式 model（抑制角色路由，仅主供应商生效）
        # > 角色模型（优先跳到池内同名模型条目）> 各条目自带模型
        role_model = None if model else (self._resolve_role_model(role) if role else None)
        now = time.time()
        # 冷却期内跳过故障供应商；若全部在冷却则忽略冷却（保证总有尝试）
        order = [i for i in range(len(self._entries)) if self._cooldown_until[i] <= now]
        if not order:
            order = list(range(len(self._entries)))
        # 【角色路由】角色指定的模型在池中有对应条目时，把该条目提到最前
        # （think→qwen 先试；qwen 挂了按序回退 zhipu 等其他大脑）
        if role_model:
            for i, idx in enumerate(order):
                if self._entries[idx].get('model_name', '') == role_model:
                    if i != 0:
                        order = [idx] + order[:i] + order[i + 1:]
                    break
        last_error: Optional[Exception] = None
        for order_pos, idx in enumerate(order):
            entry = self._entries[idx]
            use_model = entry.get('model_name', '')
            if model and idx == 0:
                use_model = model
            elif role_model and entry.get('model_name', '') == role_model:
                use_model = role_model
            try:
                result = entry['client'].chat(
                    model=use_model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens, timeout=timeout)
                self._cooldown_until[idx] = 0.0  # 成功即解除冷却
                return result
            except Exception as e:
                err_str = str(e).lower()
                failover = any(m in err_str for m in _FAILABLE_MARKERS)
                if failover:
                    self._cooldown_until[idx] = time.time() + self._COOLDOWN_SECONDS
                    if order_pos < len(order) - 1:
                        _log.warning(f'[LLM Pool] {entry.get("label")} 失败({e})，'
                                     f'切换下一供应商并冷却 {self._COOLDOWN_SECONDS}s')
                        last_error = e
                        continue
                raise
        raise last_error if last_error else RuntimeError('所有供应商均不可用')

    def stream_chat(self, model: str = None, messages: list[dict] = None,
                    temperature: float = 0.7, max_tokens: int = 1000,
                    timeout: float = 30, role: str = None, **_ignored):
        """流式生成：yield 文本增量块。路由/冷却/角色语义与 chat 一致。

        首块前失败 → 自动切下一供应商；流中途失败 → 结束生成（调用方以
        已收文本兜底）。不支持流式的供应商自动跳过。"""
        if messages is None:
            messages = []
        role_model = None if model else (self._resolve_role_model(role) if role else None)
        now = time.time()
        order = [i for i in range(len(self._entries)) if self._cooldown_until[i] <= now]
        if not order:
            order = list(range(len(self._entries)))
        if role_model:
            for i, idx in enumerate(order):
                if self._entries[idx].get('model_name', '') == role_model:
                    if i != 0:
                        order = [idx] + order[:i] + order[i + 1:]
                    break
        last_error: Optional[Exception] = None
        for order_pos, idx in enumerate(order):
            entry = self._entries[idx]
            client = entry['client']
            stream_fn = getattr(client, 'chat_stream', None)
            if not callable(stream_fn):
                # 该供应商无流式实现 → 若还有下一家则跳过，否则最后一家也照用
                if order_pos < len(order) - 1:
                    _log.warning(f'[LLM Pool] {entry.get("label")} 不支持流式，跳过')
                    continue
            use_model = entry.get('model_name', '')
            if model and idx == 0:
                use_model = model
            elif role_model and use_model == role_model:
                use_model = role_model
            try:
                got_any = False
                for piece in stream_fn(use_model, messages, temperature, max_tokens, timeout):
                    got_any = True
                    yield piece
                self._cooldown_until[idx] = 0.0
                return
            except Exception as e:
                # 流中途断开：已产出的内容返回给调用方兜底，不再重试（避免重复输出）
                if got_any:
                    _log.warning(f'[LLM Pool] {entry.get("label")} 流中断: {e}')
                    return
                if order_pos < len(order) - 1:
                    _log.warning(f'[LLM Pool] {entry.get("label")} 流式失败({e})，切换下一供应商')
                    last_error = e
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError('所有供应商均不可用')


def build_provider_pool(llm_cfg: dict, roles: dict = None) -> Any:
    """按配置构建供应商池。

    兼容三种配置来源（llm_apis.yaml 优先）：
    1. llm_apis.yaml providers 列表 + roles（智能体分工，见该文件注释）
    2. config.yaml llm.providers 列表 → 多条目池（api_key 缺失的条目自动跳过）
    3. 旧单供应商字段（provider/api_key_env/api_base/model_name）→ 单条目池
    """
    if not llm_cfg:
        return _build_mock_pool(roles) if _mock_enabled() else None
    entries: list[dict] = []
    seen_keys: set[str] = set()

    def _add(provider: str, key_env: str, api_base: str, model_name: str,
             min_max_tokens: int = 0) -> None:
        api_key = os.environ.get(key_env or '', '')
        if not api_key:
            return  # 未配置 key 的供应商静默跳过（预置配置不要求配齐）
        # 去重键含 api_base：同 key 同模型但不同端点（国内/海外双线路）是合法配置
        dedupe = f'{provider}:{key_env}:{model_name}:{api_base or ""}'
        if dedupe in seen_keys:
            return
        try:
            client = create_llm_provider(provider, key_env, api_base or '',
                                         min_max_tokens=min_max_tokens)
        except Exception as e:
            _log.warning(f'[LLM Pool] 创建 {provider} 失败: {e}')
            return
        if client is None:
            return
        seen_keys.add(dedupe)
        entries.append({
            'provider': provider,
            'client': client,
            'api_key': api_key,
            'api_base': api_base or '',
            'model_name': model_name,
            'label': f'{provider}:{model_name}',
        })

    # 新：providers 列表（按书写顺序即优先级）
    for item in (llm_cfg.get('providers') or []):
        if not isinstance(item, dict) or item.get('enabled') is False:
            continue
        _add(
            str(item.get('provider', 'openai-compat')),
            str(item.get('api_key_env', '')),
            str(item.get('api_base', '') or ''),
            str(item.get('model_name', 'gpt-3.5-turbo')),
            int(item.get('min_max_tokens', 0) or 0),
        )

    # 旧：单供应商字段作为兜底条目
    _add(
        str(llm_cfg.get('provider', 'zhipu')),
        str(llm_cfg.get('api_key_env', 'ZHIPU_API_KEY')),
        str(llm_cfg.get('api_base', '') or ''),
        str(llm_cfg.get('model_name', 'glm-4-flash')),
    )

    if not entries:
        # 一个 Key 都没配：非生产环境回退离线演示，避免"clone 下来跑不起来"
        return _build_mock_pool(roles) if _mock_enabled() else None
    pool = MultiProviderLLM(entries, roles=roles)
    _log.info(f'[LLM Pool] 就绪：{pool.providers_desc}')
    return pool


# ============================================================
# 视觉
# ============================================================

class VisionCapability:
    """视觉理解：OpenAI 兼容多模态端点（默认智谱 GLM-4V-Flash，免费）。"""

    def __init__(self, api_key: str, api_base: str, model: str):
        self._api_base = api_base.rstrip('/')
        self._api_key = api_key
        self.model = model

    def describe(self, image_data_url: str, prompt: str,
                 system: str = '', timeout: float = 45) -> str:
        """描述一张图片。image_data_url: data:image/...;base64,xxx 或公网 http(s) URL。

        返回描述文本；失败抛异常（调用方决定降级）。"""
        content: list[dict] = [{'type': 'text', 'text': prompt or '请描述这张图片。'}]
        content.append({'type': 'image_url', 'image_url': {'url': image_data_url}})
        messages: list[dict] = []
        if system:
            messages.append({'role': 'system', 'content': system})
        messages.append({'role': 'user', 'content': content})
        resp = requests.post(
            f'{self._api_base}/chat/completions',
            headers={
                'Authorization': f'Bearer {self._api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': self.model,
                'messages': messages,
                'temperature': 0.4,
                'max_tokens': 500,
            },
            timeout=min(timeout, _REQUEST_TIMEOUT),
        )
        if resp.status_code != 200:
            raise RuntimeError(f'视觉接口 HTTP {resp.status_code}: {(resp.text or "")[:200]}')
        data = resp.json()
        content_text = data['choices'][0]['message']['content']
        return content_text.strip() if isinstance(content_text, str) else ''


# ============================================================
# 语义向量（记忆检索用）
# ============================================================

class ZhipuEmbedding:
    """文本向量化：智谱 embedding-3（OpenAI 兼容 /embeddings 端点）。

    用于记忆语义检索——比本地 sentence-transformers 轻（零模型加载，
    2G 内存服务器友好），按量计费极低。"""

    name = 'zhipu-embedding'

    def __init__(self, api_key: str, api_base: str, model: str):
        self._api_base = api_base.rstrip('/')
        self._api_key = api_key
        self.model = model

    def embed(self, text: str, timeout: float = 15) -> Optional[list[float]]:
        """文本 → 向量；失败返回 None（调用方回退关键词检索）。"""
        if not text or not str(text).strip():
            return None
        try:
            resp = requests.post(
                f'{self._api_base}/embeddings',
                headers={
                    'Authorization': f'Bearer {self._api_key}',
                    'Content-Type': 'application/json',
                },
                json={'model': self.model, 'input': str(text)[:2000]},
                timeout=min(timeout, _REQUEST_TIMEOUT),
            )
            if resp.status_code != 200:
                _log.warning(f'[Embedding] HTTP {resp.status_code}: {(resp.text or "")[:120]}')
                return None
            data = resp.json()
            vec = data['data'][0]['embedding']
            return [float(x) for x in vec] if vec else None
        except Exception as e:
            _log.warning(f'[Embedding] 向量化失败: {e}')
            return None


def create_embedding_capability(emb_cfg: dict) -> Optional[ZhipuEmbedding]:
    """按 llm_apis.yaml embeddings 段创建向量化能力；未配置/缺 key 返回 None。"""
    if not emb_cfg or emb_cfg.get('enabled') is False:
        return None
    api_key = os.environ.get(str(emb_cfg.get('api_key_env', 'ZHIPU_API_KEY')), '')
    if not api_key:
        return None
    api_base = str(emb_cfg.get('api_base', '') or 'https://open.bigmodel.cn/api/paas/v4')
    model = str(emb_cfg.get('model', 'embedding-3'))
    try:
        cap = ZhipuEmbedding(api_key, api_base, model)
        _log.info(f'[Embedding] 语义向量化就绪: {model}')
        return cap
    except Exception as e:
        _log.warning(f'[Embedding] 初始化失败: {e}')
        return None


def cosine_sim(vec_a, vec_b) -> float:
    """余弦相似度（纯 python，输入为数字列表；任一为空返回 0）。"""
    try:
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        na = sum(a * a for a in vec_a) ** 0.5
        nb = sum(b * b for b in vec_b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
    except Exception:
        return 0.0


def image_file_to_data_url(path: str, max_side: int = 1280) -> Optional[str]:
    """本地图片文件 → base64 data URL。

    超大图按 Pillow 等比缩到 max_side（视觉模型不需要原图分辨率），
    统一转 JPEG 控制请求体（DashScope HTTP 上限 6MB）。失败返回 None。"""
    try:
        from PIL import Image
        import io
        img = Image.open(path)
        img.load()
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        w, h = img.size
        if max(w, h) > max_side:
            scale = max_side / float(max(w, h))
            img = img.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=85)
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:image/jpeg;base64,{b64}'
    except Exception as e:
        _log.warning(f'[Vision] 图片转 data URL 失败: {e}')
        return None


def create_vision_capability(vision_cfg: dict) -> Optional[VisionCapability]:
    """按 llm.vision 配置创建视觉能力；未配置/缺 key 返回 None（调用方降级）。"""
    if not vision_cfg or vision_cfg.get('enabled') is False:
        return None
    api_key = os.environ.get(str(vision_cfg.get('api_key_env', 'ZHIPU_API_KEY')), '')
    if not api_key:
        return None
    api_base = str(vision_cfg.get('api_base', '') or 'https://open.bigmodel.cn/api/paas/v4')
    model = str(vision_cfg.get('model_name', 'glm-4v-flash'))
    try:
        cap = VisionCapability(api_key, api_base, model)
        _log.info(f'[Vision] 视觉能力就绪: {api_base} {model}')
        return cap
    except Exception as e:
        _log.warning(f'[Vision] 初始化失败: {e}')
        return None
