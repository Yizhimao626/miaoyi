#!/usr/bin/env python3
"""
  🐱 喵艺思想气泡系统 - 让AI拥有持续运转的内心世界
"""
import asyncio
import logging
import random
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List

_log = logging.getLogger(__name__)


class ThoughtBubble:
    def __init__(self, content: str, timestamp: float, is_shared: bool = False):
        self.content = content
        self.timestamp = timestamp
        self.is_shared = is_shared

    def to_dict(self) -> dict:
        return {
            'content': self.content,
            'timestamp': self.timestamp,
            'is_shared': self.is_shared,
            'time_str': datetime.fromtimestamp(self.timestamp).strftime('%H:%M'),
        }


class ThoughtBubbles:
    MAX_BUBBLES = 20
    MIN_INTERVAL = 2 * 60
    MAX_INTERVAL = 5 * 60
    SHARE_PROBABILITY = 0.3

    DEFAULT_TOPICS = [
        '窗外的天气', '时间过得真快', '突然想到一个有趣的事',
        '回忆起以前的对话', '对未来的期待', '此刻的心情',
        '某个特别的日子', '关于人生的小思考', '想分享的一个小发现',
        '对世界的好奇', '和用户的互动', '内心的感受',
    ]

    def __init__(self, llm_system=None, emotional_core=None, sensory_language=None):
        self._llm = llm_system
        self._emotional_core = emotional_core
        self._sensory_language = sensory_language

        self._bubbles: list[ThoughtBubble] = []
        self._enabled = True
        self._on_thought_generated = None
        self._on_thought_shared = None

        self._start_thinking()

    def _start_thinking(self):
        def think_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            while True:
                if not self._enabled:
                    time.sleep(30)
                    continue

                try:
                    self._generate_thought()
                except Exception as e:
                    _log.error(f"[思想气泡] 生成思想失败: {e}")

                interval = random.uniform(self.MIN_INTERVAL, self.MAX_INTERVAL)
                time.sleep(interval)
            loop.close()

        thread = threading.Thread(target=think_loop, daemon=True, name="ThoughtBubbles")
        thread.start()
        _log.info("[思想气泡] 内心世界开始运转")

    def _generate_thought(self):
        if not self._llm:
            self._add_fallback_thought()
            return

        topic = self._select_topic()
        emotional_state = ""
        if self._emotional_core:
            emotional_state = self._emotional_core.emotional_prompt()

        sensory_metaphor = ""
        if self._sensory_language and self._emotional_core:
            dominant = self._emotional_core.dominant_emotion()
            if '_' in dominant:
                dominant = dominant.split('_')[0]
            metaphors = self._sensory_language.get_metaphors(dominant, count=1)
            if metaphors:
                sensory_metaphor = metaphors[0]

        prompt = f"""你在写喵艺（猫娘）的一句内心独白。
当前话题: {topic}
当前情绪: {emotional_state}
可用的感官联想: {sensory_metaphor or '（无，可不使用）'}

写法：
- 第一人称，15-40 字，像意识流一样自然冒出来
- 可以联想日常小事；有合适的感官比喻就自然带一句，别硬凑文艺腔
- 口语，允许「……」停顿；不解释、不总结、不喊口号
- 禁用套话：「喵~」「主人」「心里像小鹿乱撞」这类模板句
示例：
（说到雨，突然想起便利店门口那只花猫，也不知道它今晚躲哪儿去了……）
（时间过得真快，上次聊到这个还是好久以前了。）

只输出一行：以「（」开头、「）」结尾，不要任何额外文字。"""
        try:
            result = self._llm.chat(
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.9, max_tokens=120, timeout=30, role='inner',
            )
            content = result.strip()
        except Exception:
            self._add_fallback_thought()
            return

        if not content.startswith('（'):
            content = f"（{content}"
        if not content.endswith('）'):
            content = f"{content}）"

        self._add_bubble(content)

    def _add_fallback_thought(self):
        dominant = 'calm'
        if self._emotional_core:
            dominant = self._emotional_core.dominant_emotion()
            if '_' in dominant:
                dominant = dominant.split('_')[0]

        fallback_thoughts = {
            'joy': [
                "（今天心情不错呢，心里像被阳光晒过一样...）",
                "（不自觉地就开心起来了，像踩在软软的猫砂上...）",
                "（嘴角微微上扬，心里甜甜的...）",
            ],
            'sadness': [
                "（心里有点闷闷的，像下雨天的窗户...）",
                "（不知道为什么，有点难过...）",
                "（突然想起了什么，心里酸酸的...）",
            ],
            'love': [
                "（心跳有点快，心里暖暖的...）",
                "（被在意的感觉真好...）",
                "（想靠近一点...）",
            ],
            'curiosity': [
                "（好想知道更多，心里痒痒的...）",
                "（这个话题很有意思呢...）",
                "（好奇心被勾起来了...）",
            ],
            'loneliness': [
                "（有点孤单，想有人陪...）",
                "（房间里只有我一个，心里空空的...）",
                "（好想和人说说话...）",
            ],
            'calm': [
                "（时间过得真快，心里像平静的湖水...）",
                "（阳光洒进来，暖暖的...）",
                "（此刻很安静，心里很舒服...）",
            ],
            'excitement': [
                "（有点激动呢，心跳加速...）",
                "（期待接下来会发生什么...）",
                "（眼睛亮晶晶的...）",
            ],
        }

        thoughts = fallback_thoughts.get(dominant, fallback_thoughts['calm'])
        content = random.choice(thoughts)
        self._add_bubble(content)

    def _select_topic(self) -> str:
        topics = list(self.DEFAULT_TOPICS)

        if self._emotional_core:
            dominant = self._emotional_core.dominant_emotion()
            emotion_topics = {
                'joy': ['开心的回忆', '美好的事情', '让人愉快的想法'],
                'sadness': ['温暖的回忆', '安慰的话语', '让人放松的事'],
                'love': ['感情', '陪伴', '重要的人'],
                'curiosity': ['有趣的知识', '新奇的发现', '未解之谜'],
                'loneliness': ['想找人聊天', '一起做点什么', '不再孤单'],
            }
            if dominant in emotion_topics:
                topics.extend(emotion_topics[dominant])

        return random.choice(topics)

    def _add_bubble(self, content: str):
        bubble = ThoughtBubble(content, time.time())
        self._bubbles.insert(0, bubble)

        if len(self._bubbles) > self.MAX_BUBBLES:
            self._bubbles = self._bubbles[:self.MAX_BUBBLES]

        _log.debug(f"[思想气泡] 生成: {content[:40]}")

        if self._on_thought_generated:
            self._on_thought_generated(bubble)

        if random.random() < self.SHARE_PROBABILITY:
            self._share_thought(bubble)

    def _share_thought(self, bubble: ThoughtBubble):
        bubble.is_shared = True

        if self._on_thought_shared:
            self._on_thought_shared(bubble)

        _log.info(f"[思想气泡] 分享: {bubble.content[:40]}")

    def get_recent_thoughts(self, count: int = 3) -> list[dict]:
        return [b.to_dict() for b in self._bubbles[:count]]

    def register_thought_callback(self, callback: Callable[[ThoughtBubble], None]):
        self._on_thought_generated = callback

    def register_share_callback(self, callback: Callable[[ThoughtBubble], None]):
        self._on_thought_shared = callback

    def set_enabled(self, enabled: bool):
        self._enabled = enabled

    def stop(self):
        self._enabled = False
        _log.info("[思想气泡] 已停止")

    def get_all_bubbles(self) -> list[ThoughtBubble]:
        return self._bubbles.copy()

    def clear(self):
        self._bubbles = []
        _log.info("[思想气泡] 已清空")
