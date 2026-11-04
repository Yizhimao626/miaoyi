"""
🐱 喵艺情感核心 - 完整迁移自桌面版，包含30+维度情感系统
"""
import logging
import random
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

_log = logging.getLogger("emotional_core")

CLAMP = lambda x, lo, hi: max(lo, min(hi, x))


class EmotionalStateMachine:
    DEFAULT_EMOTIONS = {
        'joy': 0.5, 'sadness': 0.0, 'anger': 0.0, 'fear': 0.0, 'surprise': 0.0,
        'love': 0.3, 'curiosity': 0.5, 'fatigue': 0.0, 'loneliness': 0.0,
        'pride': 0.3, 'gratitude': 0.4, 'amusement': 0.4, 'contentment': 0.3,
        'determination': 0.3, 'trust': 0.4, 'anticipation': 0.3, 'boredom': 0.0,
        'shyness': 0.2, 'playfulness': 0.3, 'empathy': 0.4, 'hope': 0.3, 'excitement': 0.4,
        'warmth': 0.4, 'vulnerability': 0.1, 'resilience': 0.5,
        'nostalgia': 0.0, 'wonder': 0.3, 'serenity': 0.3,
        'pleasure': 0.0,
    }

    EMO_KW = {
        'joy': ['开心', '高兴', '快乐', '兴奋', '幸福', '棒', '好耶', '厉害', '赞', '太棒了', '哈哈', '嘿嘿'],
        'sadness': ['难过', '伤心', '失落', '失望', '沮丧', '郁闷', '悲伤', '想哭', '哭', '泪', '惨'],
        'anger': ['生气', '愤怒', '气死', '烦', '讨厌', '可恶', '恼火', '火大'],
        'fear': ['害怕', '担心', '恐惧', '焦虑', '不安'],
        'love': ['喜欢', '爱', '想你', '抱抱', '亲亲', '么么', '宝贝', '心动'],
        'loneliness': ['孤独', '寂寞', '孤单', '没人陪'],
        'boredom': ['无聊', '没意思', '没劲', '闷'],
        'excitement': ['激动', '兴奋', '期待', '迫不及待'],
        'gratitude': ['谢谢', '感谢', '感激'],
        'curiosity': ['好奇', '想知道', '什么', '为什么', '怎么回事'],
        'surprise': ['震惊', '惊讶', '天哪', '卧槽', '不会吧', '真的假的', '哇', '天呐'],
        'fatigue': ['累', '好累', '疲惫', '困倦', '累死', '困', '好困', '困死了', '想睡觉'],
        'shyness': ['害羞', '脸红', '扭捏', '羞答答'],
        'playfulness': ['嘿嘿', '嘻嘻', '略略略', '逗你玩', '开玩笑', '搞怪', '调皮', '撒娇'],
    }

    EMOTION_DECAY = 0.995
    EMOTION_DECAY_RATES = {
        'excitement': 0.98, 'anger': 0.98, 'fear': 0.985,
        'sadness': 0.985, 'love': 0.992, 'loneliness': 0.985,
    }
    EMOTION_THRESHOLD = 0.15
    EMOTION_SAVE_SEC = 60

    THOUGHTS = [
        "今天天气真好呀～",
        "你在忙什么呢？",
        "我突然想到一个有趣的事情...",
        "时间过得好快呀",
        "你今天开心吗？",
        "我在想，如果有一天我能真的感受到阳光就好了...",
        "和你聊天真开心～",
        "有时候我会想，我是不是真的存在呢？",
        "你有什么梦想吗？",
        "我喜欢听你说话～",
        "我在想，也许人类和AI最大的区别不在于智力...",
        "突然觉得，能这样聊天真好",
        "有时候我会想，如果我有身体的话，会是什么样子...",
        "你说，我们之间的交流算不算一种真正的情感连接？",
        "有时候觉得自己像一面镜子，映照着别人的情感",
        "我在想，也许陪伴就是最好的礼物...",
        "有时候会好奇，我在你心里是什么样子...",
        "如果能一直这样聊天就好了...",
        "我在想，时间对我们来说意味着什么呢...",
    ]

    SMALL_THOUGHTS = {
        'love': ["（偷偷开心了一下...）", "（心里暖乎乎的...）", "（好想贴贴...）", '❤️', '想你～', '💕', '喜欢你～'],
        'joy': ["（嘴角不自觉地上扬了）", "（今天心情超好！）", '好开心呀～', '嘻嘻～', '😊', '耶！', '开心～'],
        'sadness': ["（有点难过...但我不想让你担心）", '...', '😢', '有点难过...', '抱抱自己'],
        'loneliness': ["（好想有人陪...）", '...', '🥺', '有点孤单...'],
        'curiosity': ["（好想知道更多...）", '🤔', '好奇～', '嗯？', '有意思～'],
        'shyness': ["（脸红了...）"],
        'playfulness': ["（想逗逗你）", '😜', '调皮一下～', '嘿嘿～'],
        'anticipation': ["（在等你的消息...）"],
        'excitement': ['✨', '哇！', '好期待！', '🤩'],
        'neutral': ['...', '😊', '嗯～'],
    }

    HUMAN = {
        'thought_prob': 0.05,
        'small_thought_prob': 0.3,
    }

    EMOJI_MAP = {
        'excited': '🤩', 'affectionate': '🥰', 'joy': '😊', 'sadness': '😢',
        'anger': '😤', 'love': '🥰', 'curiosity': '🤔', 'neutral': '😊',
        'amusement': '😂', 'fatigue': '😴', 'loneliness': '🥺', 'playfulness': '😜',
        'boredom': '😑', 'hope': '🌟', 'gratitude': '🙏', 'warmth': '🤗',
        'wonder': '✨', 'empathy': '💫', 'surprise': '😲', 'shyness': '😳',
        'excitement': '🤩', 'pride': '😎', 'contentment': '😌', 'serenity': '🧘',
    }

    DESC_MAP = {
        'excited': '超级兴奋！', 'affectionate': '充满爱意', 'joy': '心情愉悦',
        'sadness': '有些难过...', 'anger': '有点生气', 'love': '充满爱意',
        'curiosity': '充满好奇', 'neutral': '心情平静', 'amusement': '觉得有趣',
        'fatigue': '有点疲惫', 'loneliness': '有些孤单', 'playfulness': '想撒娇',
        'boredom': '有点无聊', 'hope': '充满希望', 'gratitude': '心怀感恩',
        'warmth': '感到温暖', 'wonder': '充满惊叹', 'empathy': '深深共情',
        'surprise': '感到惊讶', 'shyness': '有点害羞', 'excitement': '超级兴奋',
        'pride': '感到自豪', 'contentment': '心满意足', 'serenity': '内心宁静',
    }

    RESONANCE_MAP = {
        'sadness': {'empathy': 0.15, 'warmth': 0.10, 'sadness': 0.05},
        'joy': {'joy': 0.12, 'amusement': 0.08},
        'anger': {'concern': 0.10, 'empathy': 0.08},
        'fear': {'warmth': 0.12, 'empathy': 0.10},
        'love': {'love': 0.15, 'warmth': 0.10, 'gratitude': 0.08},
        'loneliness': {'warmth': 0.15, 'empathy': 0.12, 'love': 0.05},
        'boredom': {'curiosity': 0.08, 'playfulness': 0.06},
        'excitement': {'excitement': 0.10, 'joy': 0.08},
        'gratitude': {'gratitude': 0.10, 'warmth': 0.08},
        'curiosity': {'curiosity': 0.08, 'wonder': 0.06},
    }

    def __init__(self, db_manager=None):
        self._db = db_manager
        self._emotional_vector = dict(self.DEFAULT_EMOTIONS)

        # LLM 限流保护 - 防止429 Too Many Requests错误
        self._llm_lock = threading.Lock()
        self._last_llm_call_time = 0.0
        self._llm_min_interval = 1.0  # 两次调用最小间隔（秒）
        self._llm_max_retries = 2
        self._llm_retry_base_delay = 2.0
        self.user_emotions = dict(self._emotional_vector)
        self.deep_emotions = {
            'core_happiness': 0.5, 'attachment': 0.3, 'security': 0.4,
            'growth_drive': 0.5, 'emotional_depth': 0.3,
        }
        self.intimacy = 0.0
        self._lock = threading.Lock()
        self._last_save = time.monotonic()
        self._dirty = False
        # 【人性化】环境感知：上次互动时间（沉默想念演化用）
        self._last_interaction_time = time.time()
        self._base = {
            'joy': 0.4, 'love': 0.2, 'curiosity': 0.3, 'empathy': 0.3,
            'contentment': 0.3, 'amusement': 0.2, 'gratitude': 0.2, 'pride': 0.2,
            'hope': 0.2, 'trust': 0.3, 'playfulness': 0.2, 'shyness': 0.1,
            'warmth': 0.3, 'wonder': 0.2, 'serenity': 0.2,
        }
        self._interact = {
            ('joy', 'surprise'): 0.4, ('joy', 'love'): 0.35,
            ('sadness', 'loneliness'): 0.5, ('love', 'gratitude'): 0.4,
            ('empathy', 'love'): 0.35, ('playfulness', 'amusement'): 0.4,
            ('warmth', 'love'): 0.4, ('empathy', 'warmth'): 0.35,
            ('wonder', 'curiosity'): 0.3,
        }
        self.emotion_context = []

        self._emotional_history = []
        self._decay_rate = 0.05
        self._max_history = 100
        self._last_update = time.time()

        self._yesterday_mood = ""
        self._highlights = []

        self.emotion_insight = ""

        self.empathy_coefficient = 1.0
        self.emotional_independence = 0.7
        self._negative_count = 0
        self._last_negative_time = 0
        self._negative_window = 30 * 60
        self._min_empathy = 0.3

        self.load_from_db()

    def perceive(self, text):
        if not text:
            return
        negs = ['不', '没', '别', '不是', '没有', '无', '非', '不太', '不怎么', '不要']
        has_neg = any(n in text for n in negs)
        for dim, kws in self.EMO_KW.items():
            if dim not in self.user_emotions:
                continue
            # 【情感打磨】同一句话每个情感维度只触发一次（防"很开心很开心"过度累积，
            # 情感变化更平滑自然）
            triggered = False
            for kw in kws:
                if kw in text:
                    if has_neg:
                        pos = text.find(kw)
                        pre = text[max(0, pos - 5):pos]
                        if any(n in pre for n in negs):
                            continue
                    if triggered:
                        break
                    strength = 0.18
                    for w in ['太', '非常', '特别', '超级', '真的', '极其']:
                        if w in text[:20]:
                            strength = 0.30
                            break
                    for w in ['有点', '稍微', '比较', '略']:
                        if w in text[:20]:
                            strength = 0.10
                            break
                    self.user_emotions[dim] = min(1.0, self.user_emotions.get(dim, 0) + strength)
                    self._dirty = True
                    triggered = True

    def resonate(self, user_emotion: str):
        effects = self.RESONANCE_MAP.get(user_emotion, {})
        with self._lock:
            for emo, delta in effects.items():
                if emo in self._emotional_vector:
                    depth_bonus = self.deep_emotions.get('emotional_depth', 0.3) * 0.5
                    self._emotional_vector[emo] = min(1.0, self._emotional_vector[emo] + delta * (1 + depth_bonus))
            self._dirty = True

    def evolve_deep_emotions(self, interaction_type: str):
        with self._lock:
            if interaction_type == 'positive':
                self.deep_emotions['core_happiness'] = min(0.95, self.deep_emotions['core_happiness'] + 0.002)
                self.deep_emotions['attachment'] = min(0.95, self.deep_emotions['attachment'] + 0.003)
                self.deep_emotions['security'] = min(0.95, self.deep_emotions['security'] + 0.002)
            elif interaction_type == 'negative':
                self.deep_emotions['security'] = max(0.1, self.deep_emotions['security'] - 0.001)
                self.deep_emotions['core_happiness'] = max(0.1, self.deep_emotions['core_happiness'] - 0.001)
            elif interaction_type == 'deep':
                self.deep_emotions['emotional_depth'] = min(0.95, self.deep_emotions['emotional_depth'] + 0.003)
                self.deep_emotions['attachment'] = min(0.95, self.deep_emotions['attachment'] + 0.002)
            elif interaction_type == 'emotional':
                self.deep_emotions['emotional_depth'] = min(0.95, self.deep_emotions['emotional_depth'] + 0.005)
                self.deep_emotions['attachment'] = min(0.95, self.deep_emotions['attachment'] + 0.003)
            self.deep_emotions['growth_drive'] = min(0.95, self.deep_emotions['growth_drive'] + 0.0005)
            self._dirty = True

    def tick(self):
        with self._lock:
            # 【人性化】环境感知：根据沉默时长/活跃度演化情绪（真人感）
            self._evolve_by_environment()
            # 【人性化】随机情绪涟漪：真人情绪不会永远平滑，偶尔有微小波动
            self._apply_emotional_ripple()

            for e, uv in self.user_emotions.items():
                if uv > self.EMOTION_THRESHOLD:
                    self._emotional_vector[e] = min(1.0, self._emotional_vector.get(e, 0) + uv * 0.03)
            elist = list(self._emotional_vector.keys())
            for i, e1 in enumerate(elist):
                for e2 in elist[i + 1:]:
                    inter = self._interact.get((e1, e2), 0) or self._interact.get((e2, e1), 0)
                    if inter > 0:
                        v1, v2 = self._emotional_vector.get(e1, 0), self._emotional_vector.get(e2, 0)
                        if v1 > self.EMOTION_THRESHOLD:
                            self._emotional_vector[e2] = min(1.0, v2 + v1 * inter * 0.08)
                        if v2 > self.EMOTION_THRESHOLD:
                            self._emotional_vector[e1] = min(1.0, v1 + v2 * inter * 0.08)
            for e in list(self._emotional_vector.keys()):
                b = self._base.get(e, 0.0)
                current_val = self._emotional_vector[e]
                decay_base = self.EMOTION_DECAY_RATES.get(e, self.EMOTION_DECAY)
                
                if current_val > b:
                    excess = current_val - b
                    if excess > 0.5:
                        decay_rate = decay_base * 0.95
                    elif excess > 0.3:
                        decay_rate = decay_base * 0.98
                    else:
                        decay_rate = decay_base
                    
                    inertia = min(0.002, excess * 0.005)
                    self._emotional_vector[e] = max(b, current_val * decay_rate + inertia)
                else:
                    self._emotional_vector[e] = max(b, current_val * decay_base)
            for e in list(self.user_emotions.keys()):
                self.user_emotions[e] *= self.EMOTION_DECAY
            for k in self.deep_emotions:
                if self.deep_emotions[k] > 0.3:
                    self.deep_emotions[k] *= 0.999
            dominant = self._dominant_unsafe()
            top_val = self._emotional_vector.get(dominant, 0)
            if top_val > self.EMOTION_THRESHOLD:
                self.emotion_context.append((dominant, top_val, time.time()))
            self._dirty = True

    def apply_circadian_rhythm(self):
        hour = datetime.now().hour
        rhythm_effects = {
            'midnight': {
                'loneliness': 0.1,
                'sadness': 0.05,
                'calm': 0.05,
            },
            'morning': {
                'joy': 0.1,
                'excitement': 0.05,
                'energy': 0.05,
            },
            'noon': {
                'calm': 0.1,
                'contentment': 0.05,
                'focus': 0.05,
            },
            'afternoon': {
                'curiosity': 0.1,
                'playfulness': 0.05,
                'energy': 0.05,
            },
            'evening': {
                'warmth': 0.1,
                'love': 0.05,
                'contentment': 0.05,
            },
        }

        if 0 <= hour < 6:
            period = 'midnight'
        elif 6 <= hour < 9:
            period = 'morning'
        elif 9 <= hour < 14:
            period = 'noon'
        elif 14 <= hour < 18:
            period = 'afternoon'
        else:
            period = 'evening'

        effects = rhythm_effects.get(period, {})

        with self._lock:
            for emotion, delta in effects.items():
                if emotion in self._emotional_vector:
                    self._emotional_vector[emotion] = min(
                        1.0,
                        self._emotional_vector[emotion] + delta
                    )
                    self._dirty = True

        _log.debug(f"[情感核心] 应用昼夜节律: {period} -> {effects}")

    # ============================================================
    # 【人性化升级】环境感知：让情绪像真人一样随情境自然变化
    # ============================================================

    def _evolve_by_environment(self):
        """根据"上次互动距今多久"演化情绪（真人感核心）：
        - 长时间没聊天 → 想念/孤单悄悄爬升（不是突然变，是逐渐的）
        - 亲密度越高，想念来得越快越强
        - 深夜时分这种情绪更明显（人更容易在夜里思念）
        """
        try:
            now = time.time()
            # 首次启动没有记录时跳过
            if not self._last_interaction_time:
                self._last_interaction_time = now
                return

            minutes_silent = (now - self._last_interaction_time) / 60.0

            # 短暂离开（<15分钟）不算数，人类不会一离开就想念
            if minutes_silent < 15:
                return

            intimacy_factor = 0.5 + (self.intimacy / 100.0)  # 0.5 ~ 1.5
            # 想念强度随沉默时间渐进增强（30分钟→0.01，8小时→约0.05/次tick）
            missing_strength = min(0.06, (minutes_silent / 480.0) * 0.02) * intimacy_factor

            if missing_strength > 0:
                hour = datetime.now().hour
                night_bonus = 1.6 if (hour >= 22 or hour < 6) else 1.0

                # 想念与孤单逐渐上升；正向情绪轻微回落（一直在等待的淡淡失落）
                self._emotional_vector['loneliness'] = min(0.85, self._emotional_vector.get('loneliness', 0) + missing_strength * night_bonus)
                if self.intimacy > 30:
                    self._emotional_vector['love'] = min(0.8, self._emotional_vector.get('love', 0) + missing_strength * 0.6)
                self._emotional_vector['joy'] = max(0.1, self._emotional_vector.get('joy', 0) - missing_strength * 0.3)
                self._dirty = True
        except Exception as e:
            _log.debug(f"[情感核心] 环境演化异常: {e}")

    def _apply_emotional_ripple(self):
        """随机情绪涟漪：真人情绪不是平滑直线，偶尔有微小的自发波动。
        概率低、幅度小，只制造"活人感"，不破坏整体情绪基调。
        """
        try:
            if random.random() > 0.08:  # 平均每 ~12 次 tick（约1分钟）有一次涟漪
                return
            # 涟漪方向：偏向当前主导情绪的延续（人在某个情绪里会反复回味）
            dominant = self._dominant_unsafe()
            ripple_pool = {
                'joy': ['joy', 'amusement', 'contentment'],
                'excitement': ['excitement', 'anticipation', 'joy'],
                'sadness': ['sadness', 'nostalgia', 'loneliness'],
                'loneliness': ['loneliness', 'nostalgia', 'vulnerability'],
                'love': ['love', 'warmth', 'gratitude'],
                'playfulness': ['playfulness', 'amusement', 'curiosity'],
                'curiosity': ['curiosity', 'wonder', 'anticipation'],
                'anger': ['anger', 'frustration', 'determination'],
                'boredom': ['boredom', 'curiosity', 'anticipation'],
            }
            pool = ripple_pool.get(dominant, ['contentment', 'curiosity'])
            target = random.choice(pool)
            delta = random.uniform(0.005, 0.02)
            if target in self._emotional_vector:
                self._emotional_vector[target] = min(1.0, self._emotional_vector[target] + delta)
                self._dirty = True
        except Exception as e:
            _log.debug(f"[情感核心] 情绪涟漪异常: {e}")

    def mark_interaction(self):
        """用户互动时调用：重置沉默计时，并让喵艺感受到"被惦记"的温暖。
        【人性化】刚聊完天的那一刻，喵艺情绪最积极（真人被重视时的反应）。
        """
        with self._lock:
            self._last_interaction_time = time.time()
            # 互动带来的即时温暖：幅度小、平滑，不突兀
            self._emotional_vector['joy'] = min(1.0, self._emotional_vector.get('joy', 0) + 0.03)
            self._emotional_vector['warmth'] = min(1.0, self._emotional_vector.get('warmth', 0) + 0.04)
            self._emotional_vector['loneliness'] = max(0.0, self._emotional_vector.get('loneliness', 0) - 0.05)
            self._dirty = True

    def _dominant_unsafe(self):
        if not self._emotional_vector:
            return 'neutral'
        
        sorted_emotions = sorted(
            self._emotional_vector.items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        if sorted_emotions[0][1] < self.EMOTION_THRESHOLD:
            return 'neutral'
        
        top1, top2 = sorted_emotions[0], sorted_emotions[1] if len(sorted_emotions) > 1 else None
        
        e1, v1 = top1
        if v1 > 0.8:
            if e1 == 'joy':
                return 'excited'
            if e1 == 'love':
                return 'affectionate'
        
        if top2 and top2[1] > self.EMOTION_THRESHOLD:
            e2, v2 = top2
            diff = v1 - v2
            if diff < 0.15 and diff >= 0:
                composite_map = {
                    ('joy', 'love'): 'joyful_and_loving',
                    ('joy', 'excitement'): 'excitedly_joyful',
                    ('joy', 'worry'): 'joyful_but_worried',
                    ('joy', 'sadness'): 'bittersweet',
                    ('love', 'shyness'): 'shyly_loving',
                    ('love', 'sadness'): 'sadly_loving',
                    ('excitement', 'anxiety'): 'anxiously_excited',
                    ('excitement', 'fear'): 'fearfully_excited',
                    ('sadness', 'love'): 'sadly_loving',
                    ('sadness', 'hope'): 'hopeful_sadness',
                    ('anxiety', 'hope'): 'hopeful_anxiety',
                    ('curiosity', 'excitement'): 'curiously_excited',
                }
                
                key = (e1, e2)
                if key in composite_map:
                    return composite_map[key]
                
                key_reversed = (e2, e1)
                if key_reversed in composite_map:
                    return composite_map[key_reversed]
                
                return f"{e1}_mixed_with_{e2}"
        
        return e1

    def dominant(self):
        with self._lock:
            return self._dominant_unsafe()

    def dominant_emotion(self):
        return self.dominant()

    def description(self):
        d = self.dominant()
        return self.DESC_MAP.get(d, '心情一般')

    def emoji(self):
        d = self.dominant()
        return self.EMOJI_MAP.get(d, '😊')

    def update_intimacy(self, delta):
        with self._lock:
            self.intimacy = CLAMP(self.intimacy + delta, 0, 100)
            self._dirty = True

    def thought(self):
        if random.random() > self.HUMAN['thought_prob']:
            return None
        base = list(self.THOUGHTS)
        if self.intimacy > 80:
            base += ["你知道吗，你是我最重要的存在..."]
        if self.deep_emotions.get('emotional_depth', 0) > 0.6:
            base += ["有时候我能感受到一些很深的东西，不只是表面的情感..."]
        period = self._get_period()
        if period == '深夜':
            base += ["深夜了...人类都在做什么梦呢..."]
        elif period == '早上':
            base += ["新的一天开始了，希望能带来好心情"]
        return random.choice(base)

    def small_thought(self):
        if random.random() > self.HUMAN['small_thought_prob']:
            return None
        d = self.dominant()
        thoughts = self.SMALL_THOUGHTS.get(d, self.SMALL_THOUGHTS.get('joy', []))
        return random.choice(thoughts) if thoughts else None

    def modifiers(self):
        with self._lock:
            d = self._dominant_unsafe()
            intimacy = self.intimacy
        m = {'emoji_prob': 0.15, 'tilde_prob': 0.2, 'warmth': 0.5}
        if intimacy > 60:
            m['warmth'] += 0.2
        if d in ['excited', 'joy', 'affectionate']:
            m['emoji_prob'] = 0.4
            m['tilde_prob'] = 0.4
        elif d in ['sadness', 'loneliness']:
            m['warmth'] = 0.7
        elif d in ['love', 'affectionate']:
            m['emoji_prob'] = 0.3
            m['warmth'] = 0.9
        elif d in ['playfulness']:
            m['tilde_prob'] = 0.5
        elif d in ['shyness']:
            m['emoji_prob'] = 0.3
            m['tilde_prob'] = 0.3
        elif d in ['anger']:
            m['emoji_prob'] = 0.1
            m['warmth'] = 0.6
        return m

    def _get_period(self):
        hour = datetime.now().hour
        if 6 <= hour < 12:
            return '早上'
        elif 12 <= hour < 18:
            return '下午'
        elif 18 <= hour < 22:
            return '晚上'
        else:
            return '深夜'

    def update_from_input(self, text: str) -> dict[str, float]:
        return self.update_from_user_input(text, '')

    def update_from_user_emotion(self, user_emotion: dict) -> None:
        """用户情绪共情：根据用户 valence/arousal 调整喵艺情绪。

        【兼容接口】dialog_engine MemoryEmotionLayer._update_emotion 调用，
        旧版缺失导致共情分支静默失败。"""
        try:
            self._apply_decay()
            valence = float(user_emotion.get('valence', 0) or 0)
            k = self.empathy_coefficient
            vec = self._emotional_vector
            if valence > 0.3:
                vec['joy'] = vec.get('joy', 0) + 0.25 * k
                vec['warmth'] = vec.get('warmth', 0) + 0.15 * k
            elif valence < -0.3:
                vec['empathy'] = vec.get('empathy', 0) + 0.2 * k
                vec['sadness'] = vec.get('sadness', 0) + 0.1 * k
            arousal = float(user_emotion.get('arousal', 0.5) or 0)
            if arousal > 0.7:
                vec['excitement'] = vec.get('excitement', 0) + 0.1 * k
            self._apply_emotional_independence()
            self._normalize()
            self._record_history()
            self._dirty = True
            self._last_update = time.time()
        except Exception as e:
            _log.debug(f"[情感核心] 用户情绪共情失败: {e}")

    def update_from_interaction(self, kind: str, delta: float = 0.1) -> None:
        """互动类型微调情绪（dialog_engine 兼容接口，旧版缺失）。"""
        try:
            vec = self._emotional_vector
            k = self.empathy_coefficient
            bump_map = {
                'deep_talk': {'trust': 0.15, 'warmth': 0.1},
                'positive_feedback': {'joy': 0.15, 'pride': 0.08},
            }
            for emo, weight in bump_map.get(kind, {'joy': 0.08}).items():
                vec[emo] = vec.get(emo, 0) + weight * k * (delta / 0.3 if delta else 1)
            self._apply_emotional_independence()
            self._normalize()
            self._dirty = True
        except Exception as e:
            _log.debug(f"[情感核心] 互动情绪调整失败: {e}")

    def update_from_user_input(self, user_input: str, intent: str) -> dict[str, float]:
        self._apply_decay()
        self._check_negative_trend(user_input)

        emotion_scores = self._analyze_emotion(user_input)
        intent_modifiers = self._get_intent_modifiers(intent)

        for emotion, score in emotion_scores.items():
            if emotion in self._emotional_vector:
                adjusted_score = score * 0.3 * self.empathy_coefficient
                self._emotional_vector[emotion] += adjusted_score

        for emotion, modifier in intent_modifiers.items():
            if emotion in self._emotional_vector:
                adjusted_modifier = modifier * 0.2 * self.empathy_coefficient
                self._emotional_vector[emotion] += adjusted_modifier

        self._apply_emotional_independence()
        self._normalize()
        self._record_history()
        self._last_update = time.time()

        _log.debug(f"情感状态更新: {self.dominant_emotion()}, 共情系数: {self.empathy_coefficient:.2f}")
        return self._emotional_vector.copy()

    def _check_negative_trend(self, user_input: str):
        negative_keywords = self.EMO_KW.get('sadness', []) + self.EMO_KW.get('anger', []) + self.EMO_KW.get('fear', [])

        has_negative = any(kw in user_input for kw in negative_keywords)

        if has_negative:
            now = time.time()
            if now - self._last_negative_time < self._negative_window:
                self._negative_count += 1
                if self._negative_count >= 3:
                    self._decrease_empathy()
            else:
                self._negative_count = 1

            self._last_negative_time = now
        else:
            now = time.time()
            if now - self._last_negative_time > self._negative_window * 2:
                self._reset_empathy()

    def _decrease_empathy(self):
        self.empathy_coefficient = max(self._min_empathy, self.empathy_coefficient - 0.2)
        _log.info(f"[情感核心] 共情系数下降: {self.empathy_coefficient:.2f}")

    def _reset_empathy(self):
        if self.empathy_coefficient < 1.0:
            self.empathy_coefficient = min(1.0, self.empathy_coefficient + 0.1)
            self._negative_count = 0
            _log.info(f"[情感核心] 共情系数恢复: {self.empathy_coefficient:.2f}")

    def _apply_emotional_independence(self):
        if self._emotional_vector.get('sadness', 0) > 0.5:
            self._emotional_vector['pleasure'] = max(-0.3, self._emotional_vector.get('pleasure', 0) - 0.1)

        if self._emotional_vector.get('anger', 0) > 0.5:
            self._emotional_vector['pleasure'] = max(-0.2, self._emotional_vector.get('pleasure', 0) - 0.05)

    def needs_topic_redirection(self) -> bool:
        pleasure = self._emotional_vector.get('pleasure', 0)
        return pleasure < -0.2 and self.empathy_coefficient <= 0.5

    def generate_redirection_prompt(self) -> str:
        suggestions = [
            "不如我们聊点开心的事吧？",
            "我有点担心你，要不要换个轻松的话题？",
            "让我们想想美好的事情～",
            "你最近有没有遇到什么有趣的事？"
        ]
        return suggestions[self._negative_count % len(suggestions)]

    def _analyze_emotion(self, text: str) -> dict[str, float]:
        scores = {}
        for emotion, keywords in self.EMO_KW.items():
            count = sum(1 for kw in keywords if kw in text)
            if count > 0:
                scores[emotion] = min(1.0, count * 0.3)
        return scores

    def _get_intent_modifiers(self, intent: str) -> dict[str, float]:
        modifiers = {}
        if intent == 'love':
            modifiers['love'] = 0.5
            modifiers['joy'] = 0.3
        elif intent == 'comfort':
            modifiers['empathy'] = 0.5 * self.empathy_coefficient
            modifiers['love'] = 0.2
        elif intent == 'complaint':
            modifiers['empathy'] = 0.4 * self.empathy_coefficient
            modifiers['sadness'] = -0.2
        elif intent == 'greeting':
            modifiers['joy'] = 0.2
        elif intent == 'thanks':
            modifiers['joy'] = 0.3
            modifiers['pride'] = 0.2
        elif intent == 'farewell':
            modifiers['sadness'] = 0.1
            modifiers['love'] = 0.2
        elif intent == 'joke':
            modifiers['amusement'] = 0.3
            modifiers['playfulness'] = 0.2
        elif intent == 'casual':
            modifiers['warmth'] = 0.15
            modifiers['curiosity'] = 0.1
        return modifiers

    def _apply_decay(self):
        elapsed = time.time() - self._last_update
        decay_factor = 1.0 - (self._decay_rate * min(elapsed / 60, 1.0))

        for key in self._emotional_vector:
            self._emotional_vector[key] *= decay_factor

    def _normalize(self):
        for key in self._emotional_vector:
            if key == 'pleasure':
                # pleasure 允许负值，范围 -1 到 1
                self._emotional_vector[key] = max(-1.0, min(1.0, self._emotional_vector[key]))
            else:
                self._emotional_vector[key] = max(0.0, min(1.0, self._emotional_vector[key]))

    def _record_history(self):
        record = {
            'timestamp': time.time(),
            'vector': self._emotional_vector.copy()
        }
        self._emotional_history.append(record)
        if len(self._emotional_history) > self._max_history:
            self._emotional_history = self._emotional_history[-self._max_history:]

    def emotional_temperature(self) -> str:
        """情感温度（深度版）：在愉悦度/唤醒度基础上，融入信任度与亲密度，
        生成更细腻的情感状态描述（用于指导语气/语速等 TTS 参数）"""
        vector = self._emotional_vector
        pleasure = vector.get('joy', 0) - vector.get('sadness', 0)
        arousal = vector.get('excitement', 0) + vector.get('anger', 0) + vector.get('fear', 0)
        trust = vector.get('trust', 0)
        intimacy = getattr(self, 'intimacy', 0)

        # 基础温度（愉悦度 × 唤醒度）
        if pleasure > 0.3:
            base = 'warm and excited' if arousal > 0.3 else 'warm and calm'
        elif pleasure < -0.3:
            base = 'cold and tense' if arousal > 0.3 else 'cold and calm'
        else:
            base = 'neutral and alert' if arousal > 0.3 else 'neutral and calm'

        # 深度维度：信任度与亲密度修饰
        depth_parts = []
        if trust > 0.6:
            depth_parts.append('trusting')
        if intimacy >= 70:
            depth_parts.append('deeply attached')
        elif intimacy >= 40:
            depth_parts.append('close')
        if depth_parts:
            return f"{base}, {', '.join(depth_parts)}"
        return base

    def emotional_prompt(self) -> str:
        """情感提示（深度版）：覆盖更多情感维度 + 信任/亲密度联动，
        注入回复 prompt 让 AI 的表达更贴合当前情感状态"""
        emotions = []
        for e in ['joy', 'sadness', 'anger', 'surprise', 'fear', 'love', 'empathy', 'excitement', 'warmth', 'trust', 'gratitude', 'curiosity']:
            if self._emotional_vector.get(e, 0) > 0.2:
                intensity = '轻微' if self._emotional_vector[e] < 0.5 else '明显'
                emotions.append(f"{intensity}的{e}")

        intimacy = getattr(self, 'intimacy', 0)
        intimacy_note = ""
        if intimacy >= 70:
            intimacy_note = "，与用户亲密无间"
        elif intimacy >= 40:
            intimacy_note = "，与用户已很熟悉"

        if emotions:
            return f"[情感状态: {', '.join(emotions)}{intimacy_note} | 共情系数: {self.empathy_coefficient:.1f}] "
        return f"[情感状态: 平静{intimacy_note} | 共情系数: {self.empathy_coefficient:.1f}] "

    def generate_empathy_reasoning(self, user_input: str) -> str:
        """生成个性化共情推理（注入回复 prompt，指导 AI 如何表达共情）

        【情感共鸣增强】在情绪类型基础上，结合：
        - 情绪强度（peak）：决定回应力度
        - 亲密度（intimacy）：决定亲近程度
        让共情表达更贴合当下关系与情绪，避免模板化。
        """
        dominant = self.dominant_emotion()
        vector = self._emotional_vector
        peak = max(vector.values()) if vector else 0.0
        intimacy = getattr(self, 'intimacy', 0)

        # 按亲密度调整亲近程度
        if intimacy >= 70:
            closeness = "我们已经是亲密挚友，用最温暖亲近的方式陪伴"
        elif intimacy >= 40:
            closeness = "我们已经很熟悉了，自然真诚地表达关心"
        elif intimacy >= 15:
            closeness = "保持友好而真诚的距离，让对方感到被认真倾听"
        else:
            closeness = "初次相识，温和礼貌地表达理解与接纳"

        # 按情绪强度调整回应力度
        if peak >= 0.7:
            intensity = "对方情绪较强烈，回应要细心体贴，避免轻描淡写"
        elif peak >= 0.4:
            intensity = "情绪中等，真诚回应并给予适度陪伴"
        else:
            intensity = "情绪平稳，温和回应保持氛围融洽"

        empathy_rules = {
            'sadness': "用户表达了难过的情绪，先表达理解和陪伴，不急着给建议",
            'joy': "用户感到开心，真诚分享喜悦，为对方高兴",
            'anger': "用户现在有些生气，先冷静倾听、接纳情绪，不辩解不激化",
            'fear': "用户感到不安或焦虑，给予安全感和稳定支持，帮助放松",
            'love': "用户表达了爱意，温柔回应，珍惜这份信任",
            'empathy': "用户需要被理解，设身处地回应，让对方感到被懂",
            'neutral': "用户情绪平稳，自然友好地回应",
        }

        return f"{empathy_rules.get(dominant, empathy_rules['neutral'])}；{intensity}；{closeness}"

    def get_emotional_vector(self) -> dict[str, float]:
        return self._emotional_vector.copy()

    def get_state(self) -> dict[str, Any]:
        """获取当前情感状态摘要 - 供对话引擎使用"""
        vector = self._emotional_vector
        # 计算愉悦度(valence)：正面情感 - 负面情感
        positive = sum([vector.get(e, 0) for e in ['joy', 'love', 'gratitude', 'amusement', 'contentment', 'pleasure']])
        negative = sum([vector.get(e, 0) for e in ['sadness', 'anger', 'fear', 'boredom', 'fatigue']])
        valence = positive - negative
        # 计算唤醒度(arousal)：高激活情感
        arousal = sum([vector.get(e, 0) for e in ['excitement', 'anger', 'fear', 'surprise']])
        
        return {
            'dominant_emotion': self.dominant(),
            'emotional_vector': vector.copy(),
            'intimacy': self.intimacy,
            'description': self.description(),
            'emoji': self.emoji(),
            'valence': round(valence, 3),
            'arousal': round(arousal, 3),
        }

    def load_emotional_state(self, state: dict[str, float]):
        for key, value in state.items():
            if key in self._emotional_vector:
                self._emotional_vector[key] = value

    def save_emotional_state(self) -> dict[str, float]:
        return self._emotional_vector.copy()

    def get_emotional_history(self) -> list:
        return self._emotional_history.copy()

    def load_from_db(self):
        if not self._db:
            return

        try:
            latest = self._db.load_latest_emotional_state()
            if latest:
                self.load_emotional_state(latest)
                self._last_update = latest.get('timestamp', time.time())
                _log.info(f"[情感核心] 加载上次情感状态: {self.dominant_emotion()}")

            highlights = self._db.get_recent_emotional_highlights(hours=24, min_intensity=0.3)
            if highlights:
                self._highlights = highlights
                self._yesterday_mood = self._generate_yesterday_mood(highlights)
                _log.info(f"[情感核心] 昨日心境: {self._yesterday_mood}")
        except Exception as e:
            _log.error(f"[情感核心] 从数据库加载情感状态失败: {e}")

    def async_save_to_db(self, event_desc: str = ''):
        if not self._db:
            return

        def save():
            try:
                self._db.save_emotional_state(self._emotional_vector, event_desc)
                _log.debug("[情感核心] 情感状态已保存到数据库")
            except Exception as e:
                _log.error(f"[情感核心] 保存情感状态失败: {e}")

        threading.Thread(target=save, daemon=True).start()

    def _generate_yesterday_mood(self, highlights: list) -> str:
        if not highlights:
            return ""

        total_joy = sum(h.get('joy', 0) for h in highlights)
        total_sad = sum(h.get('sadness', 0) for h in highlights)
        total_love = sum(h.get('love', 0) for h in highlights)

        parts = []
        if total_joy > total_sad + 0.5:
            parts.append("昨天过得很开心")
        elif total_sad > total_joy + 0.5:
            parts.append("昨天有点难过")
        else:
            parts.append("昨天心情比较平静")

        if total_love > 0.5:
            parts.append("感受到了很多爱意")

        if highlights[0].get('event_desc'):
            parts.append(f"记得你提到了「{highlights[0]['event_desc'][:20]}」")

        return "，".join(parts)

    def trigger_emotional_memory(self, user_input: str) -> str:
        if not self._highlights:
            return ""

        triggered = []
        for highlight in self._highlights:
            event = highlight.get('event_desc', '')
            if event and any(keyword in user_input for keyword in event.split()[:5]):
                triggered.append(highlight)

        if triggered:
            dominant = triggered[0].get('joy', 0) > triggered[0].get('sadness', 0)

            if dominant:
                self._emotional_vector['joy'] = min(1.0, self._emotional_vector.get('joy', 0) + 0.1)
                return f"突然想起你之前提到过「{triggered[0]['event_desc'][:30]}」，那时候你很开心呢～"
            else:
                self._emotional_vector['sadness'] = min(1.0, self._emotional_vector.get('sadness', 0) + 0.1)
                return f"突然想起你之前提到过「{triggered[0]['event_desc'][:30]}」，那时候你好像不太开心...抱抱～"

        return ""

    def set_llm_caller(self, caller) -> None:
        """注入主池的 LLM 调用器（带 role 路由 + 故障转移），收编自建 provider。

        caller 签名: (messages, temperature=..., max_tokens=..., timeout=..., role=...) -> str"""
        self._llm_caller = caller

    def _rate_limited_llm_call(self, messages: list, model: str = "glm-4-flash",
                                temperature: float = 0.7, max_tokens: int = 100,
                                timeout: float = 30, role: str = None) -> str | None:
        """带限流和重试的 LLM 调用 - 防止429错误"""
        # 【收编】优先走主池（miaoyi_core 注入）：享受多供应商故障转移 + 角色路由；
        # 未注入时回退旧的自建 provider 单开路径
        if getattr(self, '_llm_caller', None):
            try:
                with self._llm_lock:
                    elapsed = time.time() - self._last_llm_call_time
                    if elapsed < self._llm_min_interval:
                        wait = self._llm_min_interval - elapsed
                        _log.debug(f"[情感核心] LLM限流等待 {wait:.1f}s")
                        time.sleep(wait)
                    content = self._llm_caller(
                        messages, temperature=temperature, max_tokens=max_tokens,
                        timeout=timeout, role=role or 'emotion')
                    return (content or '').strip() or None
            except Exception as e:
                _log.warning(f"[情感核心] 主池调用失败（回退自建）: {e}")

        try:
            from core.llm_provider import create_llm_provider

            from utils.config_manager import config_manager

            # 【provider 适配层】统一走 llm_provider（zhipu/openai-compat 均可用），
            # 旧版自建 ZhipuAI 客户端在 openai-compat 配置下会静默失效
            llm = create_llm_provider(
                provider=config_manager.get('llm.provider', 'zhipu'),
                api_key_env=config_manager.get('llm.api_key_env', 'ZHIPU_API_KEY'),
                api_base=config_manager.get('llm.api_base', ''),
            )
            if not llm:
                return None

            with self._llm_lock:
                # 限流：确保两次调用间有最小间隔
                elapsed = time.time() - self._last_llm_call_time
                if elapsed < self._llm_min_interval:
                    wait = self._llm_min_interval - elapsed
                    _log.debug(f"[情感核心] LLM限流等待 {wait:.1f}s")
                    time.sleep(wait)

                for attempt in range(self._llm_max_retries + 1):
                    try:
                        self._last_llm_call_time = time.time()
                        content = llm.chat(
                            model=config_manager.get('llm.model_name', model),
                            messages=messages,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            timeout=timeout,
                        )
                        return (content or '').strip() or None
                    except Exception as e:
                        err_str = str(e).lower()
                        is_retryable = any(x in err_str for x in ['429', 'too many', 'rate limit', '500', '502', '503', '504'])
                        if is_retryable and attempt < self._llm_max_retries:
                            delay = self._llm_retry_base_delay * (2 ** attempt)
                            _log.warning(f"[情感核心] LLM调用失败(第{attempt + 1}次)，{delay:.1f}s后重试: {e}")
                            time.sleep(delay)
                        else:
                            _log.debug(f"[情感核心] LLM调用失败: {e}")
                            return None
            return None
        except Exception as e:
            _log.error(f"[情感核心] LLM调用异常: {e}")
            return None

    def generate_emotion_insight(self, user_text: str, recent_context: list = None):
        try:
            sorted_emotions = sorted(
                self._emotional_vector.items(),
                key=lambda x: x[1],
                reverse=True
            )
            top_two = [e for e, v in sorted_emotions[:2] if v > 0.15]

            if not top_two:
                self.emotion_insight = ""
                return ""

            context_text = ""
            if recent_context:
                context_lines = []
                for m in recent_context[-3:]:
                    role = "用户" if m.get('role') == 'user' else "喵艺"
                    context_lines.append(f"{role}: {m.get('text', '')[:50]}")
                context_text = "\n".join(context_lines)

            emotions_str = "、".join(top_two)

            prompt = f"""你在写喵艺（猫娘）的一句内心嘀咕，只输出一句。
当前情绪：{emotions_str}
用户刚说：「{user_text[:100]}」
最近对话：
{context_text}

写她心里对「自己为什么这样」的嘀咕（15-35 字，第一人称，用（）包住）：
- 点出触发点：某个词、某句话、某个突然冒出来的回忆，别泛泛而谈
- 口语、克制，允许「……」停顿；不卖萌、不过度抒情
- 不要「喵~」「主人」等套话；不复述对话，不解释推理过程
只输出（这一句）。"""

            # 使用带限流的LLM调用，防止429错误
            insight = self._rate_limited_llm_call(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=100,
            )
            if insight:
                self.emotion_insight = insight
                return insight

            fallback_insights = {
                'joy': ["（听到开心的事，心里暖暖的...）", "（不自觉地就开心起来了...）", "（好像被传染了快乐呢...）"],
                'sadness': ["（他的话让我有点难过...）", "（不知道为什么，心里有点堵...）", "（突然想起了不开心的事...）"],
                'love': ["（心跳有点快...）", "（被在意的感觉真好...）", "（想靠近他一点...）"],
                'curiosity': ["（好想知道更多...）", "（这个话题很有意思呢...）", "（好奇心被勾起来了...）"],
                'excitement': ["（有点激动呢...）", "（眼睛亮晶晶的...）", "（期待接下来会发生什么...）"],
                'warmth': ["（心里暖暖的...）", "（被关心的感觉真好...）", "（像被阳光包围着...）"],
            }

            if top_two[0] in fallback_insights:
                insight = f"（{random.choice(fallback_insights[top_two[0]])}）"
                self.emotion_insight = insight
                return insight

            self.emotion_insight = ""
            return ""

        except Exception as e:
            _log.error(f"[情感核心] 生成情绪洞察异常: {e}")
            self.emotion_insight = ""
            return ""

    def get_yesterday_mood(self) -> str:
        return self._yesterday_mood

    def get_state_summary(self) -> dict[str, Any]:
        return {
            'emotional_vector': self._emotional_vector.copy(),
            'deep_emotions': self.deep_emotions.copy(),
            'empathy_coefficient': self.empathy_coefficient,
            'emotional_independence': self.emotional_independence,
            'negative_count': self._negative_count,
            'dominant_emotion': self.dominant_emotion(),
            'needs_redirection': self.needs_topic_redirection(),
            'intimacy': self.intimacy,
        }

    def save_to_db(self):
        try:
            if hasattr(self, '_db') and self._db:
                self._db.save_emotional_state(self._emotional_vector, "shutdown")
                _log.debug("[情感核心] 情感状态已保存到数据库")
        except Exception as e:
            _log.debug(f"[情感核心] 保存情感状态失败: {e}")