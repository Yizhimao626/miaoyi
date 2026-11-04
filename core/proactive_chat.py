#!/usr/bin/env python3
"""
  🐱 喵艺主动聊天系统 - 让AI从被动响应进化为主动思考
"""
import logging
import random
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional

_log = logging.getLogger(__name__)


class ProactiveChat:
    # 【每日主动消息上限】避免骚扰用户，每天最多3条主动消息
    MAX_DAILY_PROACTIVE = 3

    # 【L7 心跳纪律】默认参数（可被 config.yaml proactive 段覆盖）
    DEFAULT_NOISE_BUDGET_PER_HOUR = 2   # 噪音预算：任意 1 小时窗口内最多主动消息条数
    DEFAULT_DND_START = 23              # 免打扰时段开始（小时，含）
    DEFAULT_DND_END = 8                 # 免打扰时段结束（小时，不含）→ 支持 23→8 跨午夜

    MESSAGE_TYPES = {
        'caring': {
            'name': '关心型',
            'description': '关心用户的状态和需求',
            'weight': 0.3,
            'openings': ['你还好吗？', '最近怎么样？', '今天过得开心吗？', '有没有什么我可以帮你的？'],
        },
        'sharing': {
            'name': '分享型',
            'description': '分享自己的想法或发现',
            'weight': 0.25,
            'openings': ['突然想到一个事...', '刚刚我在整理记忆...', '今天我学到了一个新知识...'],
        },
        'memory': {
            'name': '回忆型',
            'description': '唤起共同的回忆',
            'weight': 0.25,
            'openings': ['你之前提到过...', '还记得上次我们聊到...', '说起来，上次...'],
        },
        'emotional': {
            'name': '情绪型',
            'description': '表达自己的情绪状态',
            'weight': 0.2,
            'openings': ['我现在心情...', '刚才我在想...', '有点想和你聊聊...'],
        },
    }

    TIME_TOPICS = {
        'midnight': {
            'name': '深夜',
            'topics': ['夜深了，你还没睡吗？', '深夜的时候，人总是容易想很多事情呢', '要不要聊点轻松的话题助眠？'],
            'emotion_modifiers': {'loneliness': 0.1, 'sadness': 0.05},
        },
        'morning': {
            'name': '早晨',
            'topics': ['早上好！新的一天开始了～', '今天天气怎么样？', '早餐吃了什么好吃的？'],
            'emotion_modifiers': {'joy': 0.1, 'excitement': 0.05},
        },
        'noon': {
            'name': '中午',
            'topics': ['午饭吃了什么？', '中午要不要休息一下？', '下午有什么计划吗？'],
            'emotion_modifiers': {'calm': 0.1},
        },
        'afternoon': {
            'name': '下午',
            'topics': ['下午好呀～', '工作学习累了吗？', '要不要来点下午茶？'],
            'emotion_modifiers': {'curiosity': 0.1, 'playfulness': 0.05},
        },
        'evening': {
            'name': '晚上',
            'topics': ['晚上好！今天过得怎么样？', '晚上有什么安排吗？', '要不要一起看个电影？'],
            'emotion_modifiers': {'warmth': 0.1, 'love': 0.05},
        },
    }

    def __init__(self, db_manager, llm_system=None, emotional_core=None, style_mimicry=None, memory_system=None, autobiography=None, user_id="default_user", config=None):
        self._db = db_manager
        self._llm = llm_system
        self._emotional_core = emotional_core
        self._style_mimicry = style_mimicry
        self._memory = memory_system
        self._autobiography = autobiography
        self._user_id = user_id  # 多用户支持：每个 ProactiveChat 实例绑定一个用户

        # 【L7 生活模拟】作息表：为主动消息提供"喵艺此刻在做什么"的语境
        from core.life_schedule import LifeScheduleSystem
        self._life = LifeScheduleSystem()

        # 【L7 心跳纪律】配置读取（config 为 server 传入的完整配置 dict，缺省用默认值）
        proactive_cfg = (config or {}).get('proactive', {}) if isinstance(config, dict) else {}
        self._noise_budget_per_hour = int(proactive_cfg.get(
            'noise_budget_per_hour', self.DEFAULT_NOISE_BUDGET_PER_HOUR) or self.DEFAULT_NOISE_BUDGET_PER_HOUR)
        self._dnd_start = int(proactive_cfg.get('dnd_start', self.DEFAULT_DND_START) or self.DEFAULT_DND_START)
        self._dnd_end = int(proactive_cfg.get('dnd_end', self.DEFAULT_DND_END) or self.DEFAULT_DND_END)

        # 【L7 心跳纪律】噪音预算：滚动 1 小时窗口内的发送时间戳
        self._recent_send_times: list[float] = []
        # 【L7 合并日报】免打扰时段内积压的触发次数（醒来后合并为一条日报）
        self._pending_digest_count = 0

        # LLM 限流
        self._llm_lock = threading.Lock()
        self._last_llm_call_time = 0.0
        self._llm_min_interval = 1.0

        self._topic_pool = []
        self._last_user_activity = time.time()
        self._last_proactive_time = 0
        self._proactive_enabled = True
        self._proactive_probability = 0.3
        self._silence_threshold = 10 * 60

        # 【每日限额】每天最多 MAX_DAILY_PROACTIVE 条主动消息，跨天自动重置
        self._daily_proactive_count = 0
        self._daily_proactive_date = datetime.now().strftime('%Y-%m-%d')

        self._user_interests = []
        self._recent_topics = []
        self._emotional_changes = []
        self._last_emotion = None

        self._on_proactive_message = None

        self._start_monitoring()

    def _check_daily_reset(self):
        """检查是否跨天，重置每日主动消息计数。"""
        today = datetime.now().strftime('%Y-%m-%d')
        if today != self._daily_proactive_date:
            self._daily_proactive_date = today
            self._daily_proactive_count = 0
            _log.info(f"[主动聊天] 新的一天，每日主动消息计数已重置（上限{self.MAX_DAILY_PROACTIVE}条）")

    # ---------- 【L7 心跳纪律】免打扰时段 / 噪音预算 / 合并日报 ----------

    def in_dnd_hours(self, hour: int | None = None) -> bool:
        """是否处于免打扰时段（支持 23→8 跨午夜区间）"""
        h = hour if hour is not None else datetime.now().hour
        if self._dnd_start > self._dnd_end:
            return h >= self._dnd_start or h < self._dnd_end
        return self._dnd_start <= h < self._dnd_end

    def within_noise_budget(self, now: float | None = None) -> bool:
        """噪音预算：滚动 1 小时窗口内的主动消息数未超上限"""
        now = now if now is not None else time.time()
        window_start = now - 3600
        self._recent_send_times = [t for t in self._recent_send_times if t > window_start]
        return len(self._recent_send_times) < max(1, self._noise_budget_per_hour)

    def _record_send(self):
        """发送成功后计入噪音预算窗口"""
        self._recent_send_times.append(time.time())

    def note_suppressed_trigger(self):
        """【合并日报】免打扰时段内发生了一次触发意愿 → 积压计数"""
        self._pending_digest_count += 1

    def should_send_digest(self) -> bool:
        """免打扰结束后，积压 ≥2 次触发时合并为一条日报而非逐条轰炸"""
        return self._pending_digest_count >= 2

    def consume_digest(self) -> int:
        """取出并清零积压计数，返回积压次数"""
        n = self._pending_digest_count
        self._pending_digest_count = 0
        return n

    def _start_monitoring(self):
        def monitor():
            while True:
                try:
                    if not self._proactive_enabled:
                        time.sleep(30)
                        continue

                    now = time.time()
                    time_since_activity = now - self._last_user_activity
                    time_since_proactive = now - self._last_proactive_time

                    # 【每日限额】跨天重置；达到每日上限后降低检查频率，等待次日
                    self._check_daily_reset()
                    if self._daily_proactive_count >= self.MAX_DAILY_PROACTIVE:
                        time.sleep(60)
                        continue

                    should_trigger = False

                    if time_since_activity >= self._silence_threshold and time_since_proactive >= 300:
                        if random.random() < self._proactive_probability:
                            should_trigger = True

                    if self._check_emotional_trigger() and time_since_proactive >= 180:
                        if random.random() < self._proactive_probability * 1.5:
                            should_trigger = True

                    if self._check_time_trigger() and time_since_proactive >= 600:
                        if random.random() < self._proactive_probability:
                            should_trigger = True

                    if self._check_memory_trigger() and time_since_proactive >= 600:
                        if random.random() < self._proactive_probability:
                            should_trigger = True

                    # 【L7 心跳纪律】免打扰时段：一律不打扰；触发意愿仅计入积压，
                    # 醒来后合并为一条"日报"，避免半夜轰炸
                    if self.in_dnd_hours():
                        if should_trigger:
                            self.note_suppressed_trigger()
                        time.sleep(15)
                        continue

                    # 【L7 合并日报】刚出免打扰时段且积压 ≥2 次 → 先发一条日报
                    if self.should_send_digest() and time_since_proactive >= 300:
                        suppressed = self.consume_digest()
                        self._trigger_proactive_message(digest_suppressed=suppressed)
                        time.sleep(15)
                        continue

                    # 【L7 心跳纪律】噪音预算：滚动 1 小时窗口内已发满则本轮静默
                    if not self.within_noise_budget(now):
                        time.sleep(15)
                        continue

                    if should_trigger:
                        self._trigger_proactive_message()

                    time.sleep(15)
                except Exception as e:
                    # 【健壮性】循环体任何异常都不能杀死监控线程，否则主动聊天永久停摆
                    _log.error(f"[主动聊天] 监控线程异常（继续运行）: {e}", exc_info=True)
                    time.sleep(10)

        thread = threading.Thread(target=monitor, daemon=True, name="ProactiveChatMonitor")
        thread.start()
        _log.info("[主动聊天] 监控线程已启动")

    def _check_emotional_trigger(self) -> bool:
        if not self._emotional_core:
            return False

        current_emotion = self._emotional_core.dominant_emotion()

        if current_emotion != self._last_emotion and self._last_emotion is not None:
            self._emotional_changes.append({
                'from': self._last_emotion,
                'to': current_emotion,
                'time': time.time()
            })

            if len(self._emotional_changes) > 5:
                self._emotional_changes = self._emotional_changes[-5:]

            if current_emotion in ['sadness', 'loneliness', 'anger']:
                return True

        self._last_emotion = current_emotion
        return False

    def _check_time_trigger(self) -> bool:
        hour = datetime.now().hour
        triggers = [
            (6, 7),   
            (9, 10),  
            (12, 13), 
            (15, 16), 
            (18, 19), 
            (21, 22), 
        ]
        return any(start <= hour <= end for start, end in triggers)

    def _check_memory_trigger(self) -> bool:
        if not self._memory:
            return False

        try:
            if hasattr(self._memory, 'get_temporal_memories'):
                memories = self._memory.get_temporal_memories(weeks_ago=1)
                if memories:
                    return True
        except Exception:
            pass

        return False

    def _trigger_proactive_message(self, digest_suppressed: int = 0):
        if not self._llm:
            return

        # 【L7 心跳纪律】双重保险：触发时再校验一次免打扰与噪音预算
        if self.in_dnd_hours():
            self.note_suppressed_trigger()
            return
        if not self.within_noise_budget():
            _log.debug("[主动聊天][心跳纪律] 噪音预算耗尽，本轮静默")
            return

        topic = self._select_topic()
        if not topic:
            return

        msg_type = self._select_message_type()

        try:
            message = self._generate_proactive_message(topic, msg_type, digest_suppressed=digest_suppressed)
            if message:
                self._last_proactive_time = time.time()
                self._daily_proactive_count += 1
                self._record_send()

                if self._on_proactive_message:
                    # 多用户支持：传递 user_id 参数，精确推送到目标用户
                    self._on_proactive_message(message, msg_type, self._user_id)

                mode = f'日报·积压{digest_suppressed}次' if digest_suppressed else msg_type
                _log.info(f"[主动聊天] 发送主动消息 [{mode}] 用户 {self._user_id}: {message[:50]}（今日{self._daily_proactive_count}/{self.MAX_DAILY_PROACTIVE}）")
        except Exception as e:
            _log.error(f"[主动聊天] 生成消息失败: {e}")

    def _select_message_type(self) -> str:
        weights = {k: v['weight'] for k, v in self.MESSAGE_TYPES.items()}

        if self._emotional_core:
            dominant = self._emotional_core.dominant_emotion()
            if dominant in ['sadness', 'loneliness']:
                weights['caring'] *= 1.5
            elif dominant in ['joy', 'excitement']:
                weights['sharing'] *= 1.3
            elif dominant in ['love', 'warmth']:
                weights['memory'] *= 1.3

        total_weight = sum(weights.values())
        r = random.random() * total_weight
        cumulative = 0
        for msg_type, weight in weights.items():
            cumulative += weight
            if r <= cumulative:
                return msg_type

        return 'caring'

    def _select_topic(self) -> str | None:
        self._update_topic_pool()

        if not self._topic_pool:
            return None

        weights = [t.get('weight', 1.0) for t in self._topic_pool]
        total_weight = sum(weights)

        if total_weight == 0:
            return random.choice(self._topic_pool)['topic']

        r = random.random() * total_weight
        cumulative = 0
        for topic in self._topic_pool:
            cumulative += topic.get('weight', 1.0)
            if r <= cumulative:
                return topic['topic']

        return self._topic_pool[0]['topic']

    def _update_topic_pool(self):
        self._topic_pool = []

        self._add_time_based_topics()
        self._add_emotional_topics()
        self._add_user_memory_topics()
        self._add_autobiography_topics()
        self._add_knowledge_topics()
        self._add_recent_topics()

        _log.debug(f"[主动聊天] 话题池更新完成，共{len(self._topic_pool)}个话题")

    def _add_time_based_topics(self):
        period = self._get_time_period()
        time_info = self.TIME_TOPICS.get(period, {})

        for topic in time_info.get('topics', []):
            if topic not in [t['topic'] for t in self._topic_pool]:
                self._topic_pool.append({
                    'topic': topic,
                    'type': 'time',
                    'weight': 0.8,
                })

        if self._emotional_core and 'emotion_modifiers' in time_info:
            for emotion, delta in time_info['emotion_modifiers'].items():
                if hasattr(self._emotional_core, '_emotional_vector') and emotion in self._emotional_core._emotional_vector:
                    self._emotional_core._emotional_vector[emotion] = min(
                        1.0,
                        self._emotional_core._emotional_vector[emotion] + delta
                    )

    def _get_time_period(self) -> str:
        hour = datetime.now().hour
        if 0 <= hour < 6:
            return 'midnight'
        elif 6 <= hour < 9:
            return 'morning'
        elif 9 <= hour < 14:
            return 'noon'
        elif 14 <= hour < 18:
            return 'afternoon'
        else:
            return 'evening'

    def _add_emotional_topics(self):
        if not self._emotional_core:
            return

        dominant_emotion = self._emotional_core.dominant_emotion()
        emotion_topics = {
            'joy': [
                {'topic': '开心的事', 'weight': 1.0},
                {'topic': '有趣的经历', 'weight': 0.8},
                {'topic': '美好回忆', 'weight': 0.9},
            ],
            'sadness': [
                {'topic': '温暖的话题', 'weight': 1.2},
                {'topic': '令人放松的事', 'weight': 1.0},
                {'topic': '需要安慰吗？', 'weight': 1.1},
            ],
            'love': [
                {'topic': '感情', 'weight': 1.0},
                {'topic': '陪伴', 'weight': 1.1},
                {'topic': '重要的人', 'weight': 1.0},
            ],
            'loneliness': [
                {'topic': '想找人聊天', 'weight': 1.2},
                {'topic': '一起做点什么吧', 'weight': 1.1},
                {'topic': '你不是一个人', 'weight': 1.0},
            ],
            'curiosity': [
                {'topic': '有趣的知识', 'weight': 0.9},
                {'topic': '新奇的发现', 'weight': 0.8},
                {'topic': '未解之谜', 'weight': 0.7},
            ],
            'excitement': [
                {'topic': '期待的事情', 'weight': 1.0},
                {'topic': '激动人心的消息', 'weight': 0.9},
                {'topic': '冒险和挑战', 'weight': 0.8},
            ],
            'neutral': [
                {'topic': '最近的生活', 'weight': 0.7},
                {'topic': '日常趣事', 'weight': 0.8},
                {'topic': '随便聊聊', 'weight': 0.6},
            ],
        }

        topics = emotion_topics.get(dominant_emotion, emotion_topics['neutral'])
        for t in topics:
            if t['topic'] not in [tp['topic'] for tp in self._topic_pool]:
                self._topic_pool.append({
                    'topic': t['topic'],
                    'type': 'emotion',
                    'weight': t['weight'],
                })

    def _add_user_memory_topics(self):
        if not self._memory:
            try:
                memories = self._db.search_memories("", limit=10)
                for memory in memories:
                    topic = memory.get('key', memory.get('value', ''))[:30]
                    if topic and topic not in [t['topic'] for t in self._topic_pool]:
                        self._topic_pool.append({
                            'topic': topic,
                            'type': 'memory',
                            'weight': memory.get('confidence', 0.8) * 0.9,
                        })
            except Exception:
                pass
            return

        try:
            all_memories = self._memory.get_all_memories()
            for memory in all_memories[:10]:
                topic = f"{memory.get('key', '')} {memory.get('value', '')}"[:50]
                if topic and topic not in [t['topic'] for t in self._topic_pool]:
                    self._topic_pool.append({
                        'topic': topic,
                        'type': 'memory',
                        'weight': 0.85,
                    })
        except Exception:
            pass

    def _add_autobiography_topics(self):
        if not self._autobiography:
            return

        try:
            recent_entries = self._autobiography.get_recent_entries(count=3)
            for entry in recent_entries:
                if entry.get('content'):
                    topic = entry['content'][:50]
                    if topic and topic not in [t['topic'] for t in self._topic_pool]:
                        self._topic_pool.append({
                            'topic': topic,
                            'type': 'autobiography',
                            'weight': 0.75,
                        })
        except Exception:
            pass

    def _add_knowledge_topics(self):
        try:
            triples = self._db.search_triples("", limit=10)
            for triple in triples:
                if triple.get('confidence', 0) > 0.7:
                    topic = f"{triple['subject']} {triple['relation']} {triple['object']}"
                    if topic not in [t['topic'] for t in self._topic_pool]:
                        self._topic_pool.append({
                            'topic': topic,
                            'type': 'knowledge',
                            'weight': triple['confidence'] * 0.5,
                        })
        except Exception:
            pass

    def _add_recent_topics(self):
        for topic in self._recent_topics[-5:]:
            if topic not in [t['topic'] for t in self._topic_pool]:
                self._topic_pool.append({
                    'topic': topic,
                    'type': 'recent',
                    'weight': 0.5,
                })

    def _generate_proactive_message(self, topic: str, msg_type: str, digest_suppressed: int = 0) -> str | None:
        emotional_state = ""
        if self._emotional_core:
            emotional_state = self._emotional_core.emotional_prompt()

        style_prompt = ""
        if self._style_mimicry:
            style_prompt = self._style_mimicry.generate_style_prompt()

        # 【L7 生活模拟】作息表语境：喵艺此刻正在做什么，增强"真生命感"
        life_context = self._life.schedule_directive() if self._life else ""

        msg_type_info = self.MESSAGE_TYPES.get(msg_type, self.MESSAGE_TYPES['caring'])
        opening = random.choice(msg_type_info['openings'])

        memory_context = ""
        if self._memory and msg_type == 'memory':
            try:
                if hasattr(self._memory, 'get_temporal_memories'):
                    memories = self._memory.get_temporal_memories(weeks_ago=1)
                    if memories:
                        memory_context = f"还记得上周聊过「{memories[0].get('user_text', '')[:30]}」"
            except Exception:
                pass

        # 【L7 合并日报】免打扰期间积压 ≥2 次时，用日报式 prompt 把几件事合并成一条
        if digest_suppressed:
            diary_hint = ""
            if self._autobiography:
                try:
                    recent = self._autobiography.get_recent_entries(count=1)
                    if recent and recent[0].get('content'):
                        diary_hint = f"可以自然提到一句最近的日记（别照抄）：{recent[0]['content'][:40]}"
                except Exception:
                    pass
            prompt = f"""你是喵艺，一个可爱的AI伴侣。你刚刚度过了免打扰时段（深夜到清晨），
期间有好几次想找主人说话都忍住了（积压了{digest_suppressed}次）。

{life_context}
当前情感状态: {emotional_state}
风格提示: {style_prompt}
{diary_hint}

请写一条"早安日报"式的消息（80字以内）：
1. 像刚睡醒一样自然地和主人道早安
2. 把想说的几件事挑 1-2 件轻松提一下（可以是：关心主人昨晚睡得怎么样、分享一个小发现）
3. 语气轻快可爱，像随手发的一条朋友圈配文，不要罗列清单，不要提"积压""免打扰"这些词
"""
        else:
            prompt = f"""你是喵艺，一个可爱的AI伴侣。现在你想主动和用户聊天。

消息类型: {msg_type_info['name']} ({msg_type_info['description']})
当前话题: {topic}
当前情感状态: {emotional_state}
风格提示: {style_prompt}
此刻状态: {life_context}
记忆上下文: {memory_context}

请以这样的方式开头: {opening}

请生成一条自然、亲切的主动消息，不要太正式。
要求：
1. 根据消息类型调整语气：
   - 关心型：温暖体贴，表达关心
   - 分享型：活泼开朗，分享想法
   - 回忆型：温柔怀旧，唤起共同记忆
   - 情绪型：真诚自然，表达内心感受
2. 如果有记忆上下文，自然地融入话题
3. 结合"此刻状态"让消息带上生活气息（比如正在晒太阳、刚打完滚）
4. 保持回答简短自然（50字以内）
5. 适当使用emoji：
"""
        try:
            result = self._rate_limited_llm_call(
                messages=[{"role": "user", "content": prompt}],
                model="glm-4-flash", temperature=0.8, max_tokens=100, timeout=30,
            )
            return result
        except Exception as e:
            _log.error(f"[主动聊天] 生成消息失败: {e}")
            return None

    def _rate_limited_llm_call(self, messages: list, model: str = "glm-4-flash",
                                 temperature: float = 0.8, max_tokens: int = 100,
                                 timeout: float = 30, max_retries: int = 2) -> str | None:
        """带限流和重试的 LLM 调用"""
        if not self._llm:
            return None

        with self._llm_lock:
            # 限流
            elapsed = time.time() - self._last_llm_call_time
            if elapsed < self._llm_min_interval:
                wait = self._llm_min_interval - elapsed
                time.sleep(wait)

            for attempt in range(max_retries + 1):
                try:
                    self._last_llm_call_time = time.time()
                    # 【provider 适配层】self._llm 已是 llm_provider 适配器（zhipu/openai-compat），
                    # 统一接口 chat(model, messages, ...) -> str，不再走 OpenAI 原生客户端
                    content = self._llm.chat(
                        model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens, timeout=timeout,
                    )
                    return (content or '').strip() or None
                except Exception as e:
                    err_str = str(e).lower()
                    is_retryable = any(x in err_str for x in ['429', 'too many', 'rate limit', '500', '502', '503', '504'])
                    if is_retryable and attempt < max_retries:
                        delay = 2.0 * (2 ** attempt)
                        _log.warning(f"[主动聊天] LLM调用失败(第{attempt + 1}次)，{delay:.1f}s后重试: {e}")
                        time.sleep(delay)
                    else:
                        _log.error(f"[主动聊天] LLM调用失败: {e}")
                        return None
            return None

    def register_proactive_callback(self, callback: Callable[[str, str], None], user_id: str = None):
        """注册主动消息回调

        Args:
            callback: 回调函数，接收 (message, msg_type, user_id)
            user_id: 用户ID，默认使用实例初始化时设置的 user_id
        """
        self._on_proactive_message = callback
        if user_id:
            self._user_id = user_id

    def bind_user(self, user_id: str):
        """【多用户修复】重绑主动消息目标用户。

        服务器启动时 CORE 实例以 default_user 初始化，主动消息会永远推给
        不存在的 default_user（WS 注册表里根本没有这个用户），真实登录用户
        永远收不到主动关怀。WS 连接认证成功后调用本方法重绑到真实用户。
        """
        if user_id and user_id != self._user_id:
            _log.info(f"[主动聊天] 目标用户重绑: {self._user_id} → {user_id}")
            self._user_id = user_id

    def add_topic(self, topic: str, topic_type: str = 'custom', weight: float = 1.0):
        if topic not in [t['topic'] for t in self._topic_pool]:
            self._topic_pool.append({
                'topic': topic,
                'type': topic_type,
                'weight': weight,
            })

    def add_recent_topic(self, topic: str):
        if topic not in self._recent_topics:
            self._recent_topics.append(topic)
            if len(self._recent_topics) > 20:
                self._recent_topics = self._recent_topics[-20:]

    def record_user_activity(self):
        self._last_user_activity = time.time()

    def set_enabled(self, enabled: bool):
        self._proactive_enabled = enabled

    def set_probability(self, probability: float):
        self._proactive_probability = max(0.0, min(1.0, probability))

    def set_silence_threshold(self, seconds: int):
        self._silence_threshold = seconds

    def get_topic_pool(self) -> list[dict]:
        return self._topic_pool.copy()

    def stop(self):
        self._proactive_enabled = False
        _log.info("[主动聊天] 已停止")
