"""
喵艺四层记忆架构 - 统一记忆系统
============================================
第一层：工作记忆 (Working Memory) - 当前对话上下文
第二层：情景记忆 (Episodic Memory) - 历史对话事件
第三层：语义记忆 (Semantic Memory) - 稳定事实知识
第四层：程序记忆 (Procedural Memory) - 行为模式/技能
============================================
设计原则:
- 记忆分层管理，不同类型记忆独立存储和检索
- 支持记忆遗忘机制 (时间衰减 + 访问频率)
- 支持情感标记和情感匹配检索
- 支持记忆强化 (多次访问提升权重)
"""
import logging
import re
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from core.llm_provider import cosine_sim

_log = logging.getLogger("memory_system")


class WorkingMemory:
    """第一层：工作记忆 - 当前对话上下文"""

    MAX_TURNS = 8  # 最多保留 8 轮对话
    MAX_TOKENS = 2000  # 上下文 token 限制 (近似)

    def __init__(self):
        self._messages: deque = deque(maxlen=self.MAX_TURNS * 2)
        self._current_topic: str | None = None
        self._user_emotion: str = 'neutral'
        self._last_intent: str = 'casual'
        self._context_window: list[dict] = []

    def add_message(self, role: str, content: str, emotion: str = 'neutral'):
        """添加一条消息到工作记忆"""
        entry = {
            'role': role,
            'content': content,
            'emotion': emotion,
            'timestamp': time.time(),
        }
        self._messages.append(entry)
        self._context_window.append(entry)

        # 控制窗口大小
        if len(self._context_window) > self.MAX_TURNS * 4:
            self._context_window = self._context_window[-self.MAX_TURNS * 2:]

    def get_context(self, last_n: int = None) -> list[dict]:
        """获取最近的对话上下文"""
        if last_n:
            return list(self._messages)[-last_n:]
        return list(self._messages)

    def get_context_text(self, last_n: int = 6) -> str:
        """获取格式化的上下文文本"""
        messages = self.get_context(last_n)
        if not messages:
            return ""

        parts = []
        for msg in messages:
            role = "用户" if msg['role'] == 'user' else "喵艺"
            content = msg['content'][:100]
            parts.append(f"{role}: {content}")

        return "\n".join(parts)

    def set_topic(self, topic: str):
        self._current_topic = topic

    def set_user_emotion(self, emotion: str):
        self._user_emotion = emotion

    def set_last_intent(self, intent: str):
        self._last_intent = intent

    @property
    def current_topic(self) -> str:
        return self._current_topic or 'general'

    @property
    def user_emotion(self) -> str:
        return self._user_emotion

    @property
    def last_intent(self) -> str:
        return self._last_intent

    def clear(self):
        self._messages.clear()
        self._context_window.clear()
        self._current_topic = None


class EpisodicMemory:
    """第二层：情景记忆 - 历史对话事件"""

    def __init__(self, db_manager=None):
        self._db = db_manager
        self._episodes: list[dict] = []
        self._loaded = False
        self._lock = threading.Lock()

    def add_episode(self, user_text: str, bot_text: str,
                    intent: str = '', emotion: str = '',
                    importance: float = 0.5, user_id: str = ''):
        """记录一个情景事件"""
        episode = {
            'id': int(time.time() * 1000),
            'user_id': str(user_id),
            'user_text': user_text[:200],
            'bot_text': bot_text[:200],
            'intent': intent,
            'emotion': emotion,
            'importance': importance,
            'timestamp': time.time(),
            'access_count': 0,
        }

        with self._lock:
            self._episodes.append(episode)

        # 持久化
        if self._db:
            try:
                self._db.save_episode(episode)
            except Exception as e:
                _log.debug(f"情景记忆保存失败: {e}")

        # 控制数量
        if len(self._episodes) > 2000:
            self._episodes = self._episodes[-1000:]

    def search(self, query: str, limit: int = 5,
               emotion_filter: str = None,
               time_range: str = None,
               user_id: str = None) -> list[dict]:
        """
        检索相关情景记忆

        Args:
            query: 查询文本
            limit: 返回数量
            emotion_filter: 情感过滤器
            time_range: 时间范围 ('today', 'week', 'month', 'all')
            user_id: 用户ID过滤。None 不过滤，传值则只返回该用户情景
        """
        with self._lock:
            if not self._episodes:
                return []

            query_lower = query.lower()
            query_words = set(re.findall(r'[\u4e00-\u9fa5a-zA-Z]+', query_lower))

            scored = []
            for ep in self._episodes:
                # 【多用户隔离】用户过滤
                if user_id is not None and ep.get('user_id', '') != str(user_id):
                    continue

                score = 0.0

                # 关键词匹配
                text_combined = (ep.get('user_text', '') + ' ' + ep.get('bot_text', '')).lower()
                for word in query_words:
                    if word in text_combined:
                        score += 1.0

                # 情感匹配 (如果指定)
                if emotion_filter and ep.get('emotion') == emotion_filter:
                    score += 2.0

                # 时间衰减 (越近权重越高)
                age_hours = (time.time() - ep.get('timestamp', 0)) / 3600
                time_weight = max(0.1, 1.0 - age_hours / (24 * 30))  # 30天衰减
                score *= time_weight

                # 重要性加成
                score *= (0.5 + ep.get('importance', 0.5))

                # 访问次数加成 (被多次访问说明重要)
                score += ep.get('access_count', 0) * 0.1

                if score > 0:
                    scored.append((score, ep))

            scored.sort(key=lambda x: x[0], reverse=True)

            # 更新访问计数
            results = []
            for score, ep in scored[:limit]:
                ep['access_count'] = ep.get('access_count', 0) + 1
                ep['_relevance'] = round(score, 2)
                results.append(ep)

            return results

    def get_recent(self, hours: int = 24) -> list[dict]:
        """获取最近的情景记忆"""
        cutoff = time.time() - hours * 3600
        with self._lock:
            return [
                ep for ep in self._episodes
                if ep.get('timestamp', 0) >= cutoff
            ]

    def load_from_db(self):
        """从数据库加载历史情景"""
        if not self._db or self._loaded:
            return

        try:
            episodes = self._db.get_episodes(limit=500)
            with self._lock:
                self._episodes = episodes
            self._loaded = True
            _log.info(f"[情景记忆] 加载 {len(episodes)} 条历史情景")
        except Exception as e:
            _log.warning(f"[情景记忆] 加载失败: {e}")

    def decay(self, days: int = 30):
        """遗忘机制：衰减太久远的低重要性记忆"""
        cutoff = time.time() - days * 86400
        with self._lock:
            before = len(self._episodes)
            self._episodes = [
                ep for ep in self._episodes
                if ep.get('timestamp', 0) >= cutoff or ep.get('importance', 0) >= 0.8
            ]
            after = len(self._episodes)
            if before != after:
                _log.info(f"[情景记忆] 遗忘机制: {before} -> {after} 条")


class SemanticMemory:
    """第三层：语义记忆 - 稳定事实知识"""

    CATEGORIES = {
        'profile': 0,       # 用户画像 (永不遗忘)
        'preference': 1,    # 喜好偏好 (缓慢衰减)
        'habit': 2,         # 习惯 (中速衰减)
        'event': 3,         # 事件 (快速衰减)
        'fact': 4,          # 一般事实 (中速衰减)
        'skill': 5,         # 技能知识 (缓慢衰减)
    }

    DECAY_RATES = {
        'profile': 0.0,      # 不衰减
        'preference': 0.01,  # 每天衰减 1%
        'habit': 0.03,
        'event': 0.10,
        'fact': 0.05,
        'skill': 0.02,
    }

    def __init__(self, db_manager=None, embedder=None):
        self._db = db_manager
        self._facts: dict[str, dict] = {}
        self._categories: dict[str, list[str]] = {k: [] for k in self.CATEGORIES}
        self._lock = threading.Lock()
        # 【语义检索】embedder：llm_apis.yaml embeddings 段的向量化能力（可选）。
        # 有向量的事实参与语义评分；无向量/失败自动回退关键词评分。
        self._embedder = embedder

    def store(self, key: str, value: Any, category: str = 'fact',
              confidence: float = 0.8, importance: float = 0.5,
              user_id: str = ''):
        """
        存储一个语义事实

        Args:
            key: 事实键 (如 "用户_姓名")
            value: 事实值
            category: 类别 (profile/preference/habit/event/fact/skill)
            confidence: 置信度 0-1
            importance: 重要性 0-1
            user_id: 用户ID（多用户隔离，空字符串表示全局共享）
        """
        # 【多用户隔离】缓存键为 (user_id, key) 元组，不同用户同名事实互不覆盖
        cache_key = (str(user_id), key)

        with self._lock:
            existing = self._facts.get(cache_key, {})

            fact = {
                'key': key,
                'value': value,
                'category': category,
                'confidence': confidence,
                'importance': importance,
                'user_id': str(user_id),
                'created_at': existing.get('created_at', time.time()),
                'updated_at': time.time(),
                'access_count': existing.get('access_count', 0),
                'strength': existing.get('strength', 0.5 + confidence * 0.5),
                # 【L1 记忆卡片流】AI 更新同 key 记忆时保留用户手动钉住的状态
                'pinned': existing.get('pinned', False),
            }

            # 【语义检索】写入时算向量（key+value 联合文本）；失败留空走关键词回退
            if self._embedder is not None:
                try:
                    emb = self._embedder.embed(f'{key} {value}')
                    if emb:
                        fact['emb'] = emb
                except Exception as e:
                    _log.debug(f'[语义记忆] 向量化失败（关键词回退）: {e}')

            self._facts[cache_key] = fact

            # 更新分类索引
            for cat_keys in self._categories.values():
                if cache_key in cat_keys:
                    cat_keys.remove(cache_key)
            if category in self._categories:
                self._categories[category].append(cache_key)

        # 持久化
        if self._db:
            try:
                self._db.save_semantic_fact(key, fact, user_id=str(user_id))
            except Exception as e:
                # 【修复】debug 级别看不到：内存有此记忆但 DB 没有，重启后记忆"失忆"难排查
                _log.warning(f"语义记忆持久化失败（重启后该记忆将丢失）: {key} - {e}")

    def forget(self, key: str, user_id: str = '') -> bool:
        """删除一个语义事实（mem0 式记忆冲突淘汰时使用）

        同时清理内存缓存与 DB 持久化，保持两层一致。

        Returns:
            bool: 该事实是否存在过
        """
        cache_key = (str(user_id), key)
        existed = False
        with self._lock:
            if cache_key in self._facts:
                existed = True
                del self._facts[cache_key]
            for cat_keys in self._categories.values():
                if cache_key in cat_keys:
                    cat_keys.remove(cache_key)

        if self._db:
            try:
                self._db.delete_semantic_fact(key, user_id=str(user_id))
            except Exception as e:
                # 【修复】debug 级别看不到：内存已删但 DB 残留，重启后"遗忘"的记忆会复活
                _log.warning(f"语义记忆 DB 删除失败（重启后该记忆会复活）: {key} - {e}")

        if existed:
            _log.info(f"[语义记忆] 遗忘: {key} (user: {user_id or 'global'})")
        return existed

    def clear_user(self, user_id: str = '') -> int:
        """清空某用户的全部专属记忆：内存缓存 + DB + 标签同步清除。

        全局共享记忆（user_id=''）不在清理范围。返回 DB 删除条数。
        """
        uid = str(user_id)
        with self._lock:
            dead_keys = [k for k in self._facts if k[0] == uid]
            for k in dead_keys:
                del self._facts[k]
            for cat_keys in self._categories.values():
                cat_keys[:] = [k for k in cat_keys if k[0] != uid]
        deleted = 0
        if self._db:
            try:
                deleted = self._db.clear_all_memories(uid)
            except Exception as e:
                _log.warning(f"[语义记忆] 清空 DB 失败（内存已清，DB 残留）: {e}")
        _log.info(f"[语义记忆] 已清空用户全部记忆: {deleted} 条 (user: {uid})")
        return deleted

    def update_value(self, key: str, value: Any, user_id: str = '') -> bool:
        """【L1 记忆卡片流】编辑记忆内容（内存缓存 + DB 双写）

        用户在前端编辑记忆后调用，保证对话中的召回立即生效（无需重启）。

        Returns:
            bool: 内存中是否存在该记忆
        """
        cache_key = (str(user_id), key)
        existed = False
        with self._lock:
            fact = self._facts.get(cache_key)
            if fact:
                existed = True
                fact['value'] = value
                fact['updated_at'] = time.time()

        if self._db:
            try:
                self._db.update_semantic_fact_value(key, str(value), user_id=str(user_id))
            except Exception as e:
                _log.warning(f"语义记忆 DB 编辑失败（重启后编辑会丢失）: {key} - {e}")

        return existed

    def set_pinned(self, key: str, pinned: bool, user_id: str = '') -> bool:
        """【L1 记忆卡片流】设置/取消钉住（内存缓存 + DB 双写）

        钉住的记忆在召回（get_memory_context_bundle）中豁免退休与冷却、优先注入。

        Returns:
            bool: 内存中是否存在该记忆
        """
        cache_key = (str(user_id), key)
        existed = False
        with self._lock:
            fact = self._facts.get(cache_key)
            if fact:
                existed = True
                fact['pinned'] = bool(pinned)

        if self._db:
            try:
                self._db.update_semantic_fact_pinned(key, bool(pinned), user_id=str(user_id))
            except Exception as e:
                _log.warning(f"语义记忆 DB 钉住失败（重启后钉住会丢失）: {key} - {e}")

        return existed

    def recall(self, query: str, limit: int = 5,
               category_filter: str = None,
               user_id: str = None) -> list[dict]:
        """
        从语义记忆中检索

        Args:
            query: 查询文本
            limit: 返回数量
            category_filter: 类别过滤器
            user_id: 用户ID过滤。None 不过滤（系统级检索），传值则只返回该用户记忆
        """
        with self._lock:
            if not self._facts:
                return []

            query_lower = query.lower()
            results = []

            # 【语义检索】query 先向量化（1 次 API 调用）；失败/未配置时
            # 语义层不生效，评分完全回退关键词（原行为）
            query_emb = None
            if self._embedder is not None:
                try:
                    query_emb = self._embedder.embed(query)
                except Exception:
                    query_emb = None

            for cache_key, fact in self._facts.items():
                fact_uid, fact_key = cache_key

                # 【多用户隔离】用户过滤
                if user_id is not None and fact_uid != str(user_id):
                    continue

                # 类别过滤
                if category_filter and fact.get('category') != category_filter:
                    continue

                score = 0.0

                # 语义相似度（余弦 0~1，权重高——同义表达也能召回）
                if query_emb and fact.get('emb'):
                    sim = cosine_sim(query_emb, fact['emb'])
                    if sim > 0.45:
                        score += sim * 6.0

                # 关键词匹配
                if query_lower in fact_key.lower():
                    score += 3.0
                elif any(w in fact_key.lower() for w in query_lower.split()):
                    score += 1.5

                # 值匹配
                value_str = str(fact.get('value', '')).lower()
                if query_lower in value_str:
                    score += 2.0

                # 强度加成
                score *= fact.get('strength', 0.5)

                # 重要性加成
                score *= (0.5 + fact.get('importance', 0.5))

                if score > 0:
                    fact['access_count'] = fact.get('access_count', 0) + 1
                    fact['_relevance'] = round(score, 2)
                    results.append(fact)

            results.sort(key=lambda x: x['_relevance'], reverse=True)
            return results[:limit]

    def get_user_profile(self, user_id: str = None) -> dict[str, Any]:
        """获取完整用户画像

        Args:
            user_id: 用户ID过滤。None 返回全部（含所有用户），传值则只返回该用户画像
        """
        with self._lock:
            profile = {}
            for cache_key, fact in self._facts.items():
                fact_uid, fact_key = cache_key
                if user_id is not None and fact_uid != str(user_id):
                    continue
                if fact.get('category') == 'profile':
                    profile[fact_key] = fact.get('value')
            return profile

    def get_all_preferences(self, user_id: str = None) -> list[dict]:
        """获取所有偏好

        Args:
            user_id: 用户ID过滤。None 返回全部，传值则只返回该用户偏好
        """
        with self._lock:
            return [
                {'key': cache_key[1], 'value': v['value']}
                for cache_key, v in self._facts.items()
                if v.get('category') == 'preference'
                and (user_id is None or cache_key[0] == str(user_id))
            ]

    def decay(self):
        """执行衰减：降低长期未访问事实的强度"""
        with self._lock:
            now = time.time()
            to_remove = []

            for key, fact in self._facts.items():
                category = fact.get('category', 'fact')
                decay_rate = self.DECAY_RATES.get(category, 0.05)
                if decay_rate == 0:
                    continue  # profile 不衰减

                # 时间衰减
                days_old = (now - fact.get('updated_at', now)) / 86400
                decay_amount = decay_rate * days_old / 30  # 每月衰减基础

                # 访问频率反弹
                access_bounce = fact.get('access_count', 0) * 0.02

                fact['strength'] = max(0.1, min(1.0,
                    fact.get('strength', 0.5) - decay_amount + access_bounce
                ))

                # 低强度事实被遗忘
                if fact['strength'] < 0.15 and fact.get('importance', 0) < 0.5:
                    to_remove.append(key)

            for key in to_remove:
                del self._facts[key]
                _log.debug(f"[语义记忆] 遗忘事实: {key}")

            return len(to_remove)

    def load_from_db(self):
        if not self._db:
            return
        try:
            # 全量加载（含所有用户），缓存键携带 user_id 实现隔离
            facts = self._db.get_semantic_facts()
            with self._lock:
                for key, value in facts.items():
                    cache_key = (str(value.get('user_id', '')), key)
                    self._facts[cache_key] = value
                    cat = value.get('category', 'fact')
                    if cat in self._categories:
                        self._categories[cat].append(cache_key)
            _log.info(f"[语义记忆] 加载 {len(facts)} 条事实")
        except Exception as e:
            _log.warning(f"[语义记忆] 加载失败: {e}")


class ProceduralMemory:
    """第四层：程序记忆 - 行为模式和技能"""

    def __init__(self):
        self._patterns: dict[str, dict] = {}
        self._skill_levels: dict[str, float] = {}
        self._conversation_styles: list[dict] = []

    def learn_pattern(self, trigger: str, response_pattern: str,
                     context: str = 'general', confidence: float = 0.5):
        """学习一个对话模式"""
        key = f"{context}:{trigger}"
        self._patterns[key] = {
            'trigger': trigger,
            'response': response_pattern,
            'context': context,
            'confidence': confidence,
            'learned_at': time.time(),
            'trigger_count': 0,
        }

    def match_pattern(self, text: str, context: str = 'general') -> dict | None:
        """匹配已知的对话模式"""
        text_lower = text.lower()

        # 精确匹配优先
        for key, pattern in self._patterns.items():
            if pattern['context'] == context and pattern['trigger'].lower() in text_lower:
                pattern['trigger_count'] = pattern.get('trigger_count', 0) + 1
                return pattern

        # 通用匹配
        for key, pattern in self._patterns.items():
            if pattern['trigger'].lower() in text_lower:
                pattern['trigger_count'] = pattern.get('trigger_count', 0) + 1
                return pattern

        return None

    def update_skill(self, skill_name: str, delta: float = 0.05):
        """更新技能熟练度"""
        current = self._skill_levels.get(skill_name, 0.0)
        self._skill_levels[skill_name] = min(1.0, current + delta)

    def get_skill_level(self, skill_name: str) -> float:
        return self._skill_levels.get(skill_name, 0.0)

    def learn_style(self, user_input: str, bot_response: str,
                    user_emotion: str = 'neutral'):
        """学习用户的对话风格"""
        style = {
            'user_input': user_input[:50],
            'bot_response': bot_response[:50],
            'emotion': user_emotion,
            'timestamp': time.time(),
        }
        self._conversation_styles.append(style)
        if len(self._conversation_styles) > 500:
            self._conversation_styles = self._conversation_styles[-200:]

    def get_style_insights(self, recent_count: int = 20) -> dict[str, Any]:
        """获取风格洞察"""
        recent = self._conversation_styles[-recent_count:]
        if not recent:
            return {}

        emotions = {}
        for s in recent:
            emo = s.get('emotion', 'neutral')
            emotions[emo] = emotions.get(emo, 0) + 1

        dominant_emotion = max(emotions, key=emotions.get) if emotions else 'neutral'

        return {
            'dominant_emotion': dominant_emotion,
            'emotion_distribution': emotions,
            'sample_count': len(recent),
        }


class UnifiedMemorySystem:
    """
    统一记忆系统 - 四层架构

    使用方式:
        memory = UnifiedMemorySystem(db_manager)
        memory.semantic.store('用户_姓名', '小明', 'profile')
        memory.episodic.add_episode(...)
        memory.working.add_message(...)
        memories = memory.recall(query)
    """

    def __init__(self, db_manager=None, embedder=None):
        self._db = db_manager

        # 四层记忆（embedder：语义检索向量化能力，可选，注入 SemanticMemory）
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory(db_manager)
        self.semantic = SemanticMemory(db_manager, embedder=embedder)
        self.procedural = ProceduralMemory()

        # 兼容旧接口
        self.facts: dict[str, str] = {}
        self.conv_summaries = deque(maxlen=200)
        self.important_memories: dict[str, list] = {}
        self._lock = threading.Lock()

        # 【G3·记忆退休】注入节奏控制（SillyTavern 式 sticky/cooldown）
        # 记录每条记忆最近一次被注入 prompt 的时间 → 基础层冷却，避免每轮重复打扰
        self._injection_history: dict[tuple, float] = {}

        # 退休/预算参数
        self._MEMORY_RETIRE_DAYS = 90       # 超过此天数且重要性 <0.7 → 退休（不再主动注入，仍可被触发召回）
        self._MEMORY_COOLDOWN_SECONDS = 600 # 基础层冷却：近期已注入的记忆 10 分钟内降优先级
        self._MEMORY_TOKEN_BUDGET = 800     # 注入记忆总字数预算（约 500 token）

        self._init_legacy_interface()

        # 后台衰减任务
        self._start_maintenance_loop()

    def _init_legacy_interface(self):
        """初始化兼容旧接口的属性"""
        if self._db:
            try:
                self.semantic.load_from_db()
                self.episodic.load_from_db()

                # 从语义记忆回填 facts (旧接口兼容)
                # 【多用户隔离】仅回填全局共享记忆（user_id=''），避免跨用户泄露
                for cache_key, fact in self.semantic._facts.items():
                    if cache_key[0] == '':
                        self.facts[cache_key[1]] = str(fact.get('value', ''))
                _log.info(f"[统一记忆] 加载 {len(self.facts)} 条兼容事实")
            except Exception as e:
                _log.warning(f"[统一记忆] 初始化失败: {e}")

    def _start_maintenance_loop(self):
        """启动后台记忆维护"""
        def maintenance_loop():
            while True:
                time.sleep(3600)  # 每小时
                try:
                    self.episodic.decay(days=30)
                    self.semantic.decay()
                except Exception as e:
                    _log.error(f"[统一记忆] 维护任务失败: {e}")

        thread = threading.Thread(target=maintenance_loop, daemon=True)
        thread.start()

    # ============ 旧接口兼容 (保持向后兼容) ============

    def remember(self, key: str, value: Any, user_id: str = ''):
        """记住一个事实 (兼容旧接口)

        Args:
            key: 事实键
            value: 事实值
            user_id: 用户ID。非空时仅写入该用户的语义记忆（不进全局 legacy facts）
        """
        # 同时存入语义记忆（用户隔离）
        category = self._infer_category(key)
        self.semantic.store(key, value, category=category, user_id=user_id)

        # 【多用户隔离】legacy facts 仅存放全局/系统级记忆，用户记忆走 semantic 层
        if not user_id:
            with self._lock:
                self.facts[key] = value

        _log.debug(f"[统一记忆] 记住: {key} = {value} (user: {user_id or 'global'})")

    def forget(self, key: str, user_id: str = ''):
        """遗忘一个事实（mem0 式冲突淘汰，语义层内存缓存与 DB 同步删除）"""
        self.semantic.forget(key, user_id=user_id)

    def clear_user(self, user_id: str = '') -> int:
        """清空指定用户的全部记忆（语义层：内存缓存 + DB + 标签）"""
        return self.semantic.clear_user(user_id)

    def _infer_category(self, key: str) -> str:
        """根据 key 推断类别"""
        category_map = {
            '姓名': 'profile', '年龄': 'profile', '所在地': 'profile',
            '职业': 'profile', '生日': 'profile',
            '喜欢': 'preference', '爱好': 'preference', '讨厌': 'preference',
            '习惯': 'habit', '日常': 'habit',
            '事件': 'event', '计划': 'event',
        }
        for keyword, cat in category_map.items():
            if keyword in key:
                return cat
        return 'fact'

    def extract_from_text(self, text: str, user_id: str = ''):
        """从文本中提取信息 (增强版)

        Args:
            text: 用户输入文本
            user_id: 用户ID（记忆归属）
        """
        patterns = [
            (r'(?:我叫|我名字是|名字是)\s*([\u4e00-\u9fa5a-zA-Z]{1,10})', '姓名', 'profile'),
            (r'(?:我今年|今年)\s*(\d+)\s*岁', '年龄', 'profile'),
            (r'(?:我在|我住|住在|来自)\s*([^\s，。！？]{1,20})', '所在地', 'profile'),
            (r'(?:我是)\s*([^\s。！？]{1,20})\s*的', '职业/身份', 'profile'),
            (r'(?:我喜欢吃|喜欢吃)\s*([^\s。！？]{1,20})', '喜欢的食物', 'preference'),
            (r'(?:我不喜欢吃|不喜欢吃)\s*([^\s。！？]{1,20})', '不喜欢的食物', 'preference'),
            (r'(?:我通常|我一般|我习惯)\s*([^\s。！？]{1,30})', '习惯', 'habit'),
            (r'(?:我每天|我经常|每天|经常)\s*([^\s。！？]{1,30})', '日常活动', 'habit'),
            (r'(?:我养了|我有一只|家里有)\s*(狗|猫|宠物)', '宠物', 'profile'),
            (r'(?:我喜欢|我爱好|爱好)\s*([^\s。！？]{1,20})', '爱好', 'preference'),
            (r'(?:我讨厌|我不喜欢|讨厌|不喜欢)\s*([^\s。！？]{1,20})', '讨厌的事物', 'preference'),
            (r'(?:生日|纪念日)\s*(\d+月\d+日)', '重要日期', 'profile'),
        ]

        for pattern, mem_type, category in patterns:
            match = re.search(pattern, text)
            if match:
                value = match.group(1)
                key = f"{mem_type}: {value}"
                self.remember(key, value, user_id=user_id)
                break

    def get_facts(self, user_id: str = '') -> dict[str, str]:
        """获取指定用户的事实字典（多用户隔离读取）

        Args:
            user_id: 用户ID。空字符串返回全局 legacy facts

        Returns:
            Dict[str, str]: {key: value}
        """
        if not user_id:
            return dict(self.facts)
        result = {}
        for cache_key, fact in self.semantic._facts.items():
            if cache_key[0] == str(user_id):
                result[cache_key[1]] = str(fact.get('value', ''))
        return result

    def get_relevant_memories(self, query: str, limit: int = 5,
                              user_id: str = None) -> list[dict]:
        """从各层记忆检索相关内容

        Args:
            query: 查询文本
            limit: 返回数量
            user_id: 用户ID。None/'' 保留旧行为（含全局 legacy 兜底），
                     传具体用户ID则严格用户隔离
        """
        results = []

        # 1. 语义记忆检索（用户隔离）
        semantic_results = self.semantic.recall(query, limit=limit, user_id=user_id)
        for r in semantic_results:
            # 防御性访问：确保 key 字段存在，兼容不同数据格式
            key = r.get('key', '') or r.get('subject', '') or ''
            value = r.get('value', '') or r.get('content', '') or ''
            if key:
                results.append({
                    'key': key,
                    'value': str(value),
                    'source': 'semantic',
                    'relevance': r.get('_relevance', 0.5),
                })

        # 2. 情景记忆检索（用户隔离）
        episodic_results = self.episodic.search(query, limit=3, user_id=user_id)
        for ep in episodic_results:
            results.append({
                'key': f"情景: {ep.get('user_text', '')[:20]}",
                'value': ep.get('bot_text', ''),
                'source': 'episodic',
                'relevance': ep.get('_relevance', 0.3),
            })

        # 3. 旧接口 facts 兜底
        # 【多用户隔离】用户级检索（user_id 非空）跳过全局 legacy 兜底，防止跨用户泄露
        if not user_id:
            for key, value in self.facts.items():
                if any(kw in query for kw in key.split()[:3]):
                    if not any(r['key'] == key for r in results):
                        results.append({
                            'key': key,
                            'value': str(value),
                            'source': 'legacy',
                            'relevance': 0.5,
                        })

        # 按相关性排序
        results.sort(key=lambda x: x.get('relevance', 0), reverse=True)
        return results[:limit]

    def get_context_str(self, user_id: str = '') -> str:
        """获取上下文字符串 (兼容接口)

        Args:
            user_id: 用户ID。非空时返回该用户的记忆上下文
        """
        if user_id:
            facts = self.get_facts(user_id)
        else:
            facts = self.facts
        lines = []
        for key, value in list(facts.items())[-10:]:
            lines.append(f"- {key}: {value}")
        return "\n".join(lines) if lines else ""

    def get_user_profile(self, user_id: str = '') -> str:
        """获取用户画像字符串

        Args:
            user_id: 用户ID。非空时只返回该用户的画像
        """
        profile = self.semantic.get_user_profile(user_id=user_id if user_id else None)
        if not profile:
            # 回退到旧接口（仅全局模式）
            if user_id:
                return ""
            parts = []
            for key, value in self.facts.items():
                if key.startswith('姓名'):
                    parts.append(f"姓名是{value}")
                elif key.startswith('年龄'):
                    parts.append(f"{value}岁")
                elif key.startswith('所在地'):
                    parts.append(f"住在{value}")
                elif key.startswith('爱好') or key.startswith('喜欢'):
                    parts.append(f"喜欢{value}")
            return "用户画像：" + "，".join(parts) + "。" if parts else ""

        parts = []
        for key, value in profile.items():
            if '姓名' in key:
                parts.append(f"姓名是{value}")
            elif '年龄' in key:
                parts.append(f"{value}岁")
            elif '所在地' in key:
                parts.append(f"住在{value}")
            elif '职业' in key:
                parts.append(f"职业是{value}")
            elif '生日' in key:
                parts.append(f"生日是{value}")

        return "用户画像：" + "，".join(parts) + "。" if parts else ""

    def get_memory_context_bundle(self, user_id: str = '',
                                  current_text: str = '') -> tuple[str, str, dict[str, str]]:
        """一次遍历同时返回【记忆上下文】【用户画像】与【事实字典】

        替代 get_context_str + get_user_profile 的两次全量遍历（同源数据），
        用于构建系统提示词，减少每条消息的重复扫描。

        借鉴 SillyTavern 世界书（World Info / Lorebook）机制：除按时间取最近的
        基础记忆外，当 current_text（用户当前消息）中出现某条记忆的触发词时，
        该条记忆被强制注入上下文 —— 让"用户提过的设定"在相关话题出现时不再被
        滑出上下文窗口而遗忘。

        Args:
            user_id: 用户ID。非空时只返回该用户的记忆；空字符串返回全局 legacy facts
            current_text: 用户当前消息文本，用于关键词触发相关记忆（空则退化为原行为）

        Returns:
            (memory_ctx, user_profile, facts): 前两者格式与 get_context_str/get_user_profile 完全一致
        """
        if user_id:
            facts: dict[str, str] = {}
            profile_facts: dict[str, Any] = {}
            with self._lock:
                for cache_key, fact in self.semantic._facts.items():
                    if cache_key[0] != str(user_id):
                        continue
                    fact_key = cache_key[1]
                    facts[fact_key] = str(fact.get('value', ''))
                    if fact.get('category') == 'profile':
                        profile_facts[fact_key] = fact.get('value')
        else:
            with self._lock:
                facts = dict(self.facts)
            profile_facts = {k: v for k, v in facts.items()
                             if any(p in k for p in ('姓名', '年龄', '所在地', '职业', '生日'))}

        # 基础层：【G3·记忆退休】按 新鲜度+重要性 排序（替代原"取最后10条"），
        # mem0 式退休：超过 RETIRE_DAYS 且 importance<0.7 的记忆不再主动注入（仍可被触发层召回）
        # SillyTavern 式 cooldown：近期已注入过的记忆降权，让位给没说过的，防重复打扰
        now_ts = time.time()
        retire_cutoff = now_ts - self._MEMORY_RETIRE_DAYS * 86400
        cooldown_cutoff = now_ts - self._MEMORY_COOLDOWN_SECONDS

        def _base_score(fact_key: str) -> float:
            if not user_id:
                return 0.0  # global legacy facts 无时间戳，保持原插入顺序
            fact = self.semantic._facts.get((str(user_id), fact_key), {})
            imp = float(fact.get('importance', 0.5))
            updated = float(fact.get('updated_at', 0) or 0)
            # 【L1 记忆卡片流】用户钉住的记忆置顶，且豁免退休与冷却（"钉住=永不遗忘"）
            if fact.get('pinned'):
                return 100.0
            # 退休：长期未更新且不重要 → 不进基础层
            if updated and updated < retire_cutoff and imp < 0.7:
                return -1.0
            score = imp + 0.5 * (0.995 ** max(0.0, (now_ts - updated) / 3600.0))
            # 冷却：10 分钟内已注入过的降权
            if self._injection_history.get((str(user_id), fact_key), 0) > cooldown_cutoff:
                score -= 1.0
            return score

        if user_id:
            ranked_keys = sorted(facts.keys(), key=_base_score, reverse=True)
            base_keys = [k for k in ranked_keys if _base_score(k) >= 0][:10]
        else:
            base_keys = list(facts.keys())[-10:]

        # 触发层：关键词命中强制注入（SillyTavern 世界书思路）
        # 匹配规则（低误报优先）：
        #   1) 记忆键本身出现在当前消息中，如提到"生日"→ 注入"生日"记忆
        #   2) 短记忆值（≤12字）完整出现在当前消息中，如记忆值"火锅"→ 用户聊火锅即命中
        triggered_keys: list[str] = []
        if current_text:
            text = current_text.strip()
            if text:
                for fact_key, fact_value in facts.items():
                    core_key = fact_key
                    for prefix in ('喜欢的', '不喜欢的', '用户_', '我的'):
                        if core_key.startswith(prefix):
                            core_key = core_key[len(prefix):]
                            break
                    hit = (len(core_key) >= 2 and core_key in text) \
                        or (0 < len(fact_value) <= 12 and fact_value in text)
                    if hit:
                        triggered_keys.append(fact_key)

        # 合并：触发条目在前（按 三因子得分 高→低），基础最近条目在后，去重，上限 15 条
        final_keys: list[str] = []
        if triggered_keys:
            def _score(key: str) -> float:
                # 【三因子简化版·generative_agents 思路】importance 为主，
                # 叠加 recency 时间衰减（0.995^小时），让近期被激活的记忆优先注入
                fact = self.semantic._facts.get((str(user_id), key), {})
                imp = float(fact.get('importance', 0.5)) if user_id else 0.5
                updated = float(fact.get('updated_at', 0) or 0)
                hours = max(0.0, (time.time() - updated) / 3600.0)
                score = imp + 0.3 * (0.995 ** hours)
                # 【L1 记忆卡片流】钉住的记忆在触发层同样优先
                if fact.get('pinned'):
                    score += 5.0
                return score

            for k in sorted(set(triggered_keys), key=_score, reverse=True):
                if k not in final_keys:
                    final_keys.append(k)
        for k in base_keys:
            if k not in final_keys:
                final_keys.append(k)
        final_keys = final_keys[:15]

        # 记忆上下文（与 get_context_str 格式一致）
        # 【G3】token 预算：总字数 ≤ _MEMORY_TOKEN_BUDGET，单条值截断 60 字，超预算从后丢弃
        lines = []
        total_chars = 0
        injected_keys: list[str] = []
        for k in final_keys:
            if k not in facts:
                continue
            line = f"- {k}: {str(facts[k])[:60]}"
            if lines and total_chars + len(line) > self._MEMORY_TOKEN_BUDGET:
                break
            total_chars += len(line)
            lines.append(line)
            injected_keys.append(k)
        memory_ctx = "\n".join(lines) if lines else ""

        # 【G3】记录注入时间（cooldown 依据）
        if user_id:
            for k in injected_keys:
                self._injection_history[(str(user_id), k)] = now_ts

        # 用户画像（与 get_user_profile 拼接规则一致）
        parts = []
        for key, value in profile_facts.items():
            if '姓名' in key:
                parts.append(f"姓名是{value}")
            elif '年龄' in key:
                parts.append(f"{value}岁")
            elif '所在地' in key:
                parts.append(f"住在{value}")
            elif '职业' in key:
                parts.append(f"职业是{value}")
            elif '生日' in key:
                parts.append(f"生日是{value}")
        user_profile = "用户画像：" + "，".join(parts) + "。" if parts else ""

        return memory_ctx, user_profile, facts

    def add_conv_summary(self, user_text: str, bot_text: str,
                        intent: str = '', emotion: str = '',
                        user_id: str = ''):
        """添加对话摘要"""
        summary = {
            'ts': time.time(),
            'user': user_text[:100],
            'bot': bot_text[:100],
            'intent': intent,
            'emotion': emotion,
        }
        with self._lock:
            self.conv_summaries.append(summary)

        # 同时存入情景记忆（用户隔离）
        self.episodic.add_episode(
            user_text=user_text,
            bot_text=bot_text,
            intent=intent,
            emotion=emotion,
            user_id=user_id,
        )

        # 存入程序记忆 (学习对话风格)
        self.procedural.learn_style(user_text, bot_text, emotion)

    def save(self):
        """保存所有记忆"""
        if not self._db:
            return

        try:
            with self._lock:
                # 【修复】不在关闭时回写语义事实：store/forget/update_value
                # 均已即时双写 DB；全量回写只会把已被外部删除/遗忘的记忆复活
                # （历史事故：脚本清空记忆 → 重启后被 shutdown.save() 全部重建）。

                # 保存对话摘要（内存中追加、仅此处统一持久化）
                summaries = list(self.conv_summaries)[-50:]
                for s in summaries:
                    self._db.save_conversation_summary(
                        s['user'], s['bot'],
                        s.get('intent', 'default'),
                        s.get('emotion', 'neutral'),
                        s.get('ts', time.time())
                    )

            _log.debug("[统一记忆] 记忆已保存")
        except Exception as e:
            _log.error(f"[统一记忆] 保存失败: {e}")

    def load(self):
        """加载所有记忆"""
        if not self._db:
            return
        self._init_legacy_interface()

    def shutdown(self):
        """关闭并保存"""
        self.save()
        self.working.clear()
        _log.info("[统一记忆] 已关闭")

    def get_stats(self) -> dict[str, Any]:
        """获取记忆系统统计"""
        return {
            'working_memory_messages': len(self.working.get_context()),
            'episodic_count': len(self.episodic._episodes),
            'semantic_count': len(self.semantic._facts),
            'procedural_patterns': len(self.procedural._patterns),
            'total_conversations': len(self.conv_summaries),
        }
