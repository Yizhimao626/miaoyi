"""喵艺对话追踪模块"""
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Dict, List

_log = logging.getLogger(__name__)


class AdvancedConversationTracker:
    """高级对话追踪器"""

    def __init__(self, llm_system=None, db_manager=None):
        self.llm = llm_system
        self.db = db_manager
        self.conversation_turns = 0
        self.current_topic = None
        self.topic_history: list[str] = []
        self.context_stack: list[dict] = []
        self.unresolved_references: list[dict] = []
        self.engagement_score = 0.5
        self.user_participation = 0.5
        self.conversation_quality = 0.5
        self.response_times: list[float] = []
        self.topic_transitions: list[dict] = []
        self.interaction_types = defaultdict(int)
        self.conversation_patterns = {
            'greeting_patterns': [],
            'farewell_patterns': [],
            'question_patterns': [],
            'statement_patterns': []
        }
        self.dialogue_states: list[dict] = []
        self.conversation_sessions: list[dict] = []
        self.current_session_start = time.time()
        self.lock = threading.Lock()
        
        self._message_history = deque(maxlen=100)
        self._compressed_summaries = []
        self._compression_threshold = 30
        self._keyframe_emotions = {'joy', 'sadness', 'anger', 'love', 'fear'}
        self._keyframe_threshold = 0.4

    def update(self, user_input: str, intent: str, bot_response: str, response_time: float):
        """更新对话状态"""
        with self.lock:
            self.conversation_turns += 1
            self.interaction_types[intent] += 1
            if response_time > 0:
                self.response_times.append(response_time)
                if len(self.response_times) > 30:
                    self.response_times = self.response_times[-15:]
            if intent == 'information':
                topics = self._extract_topics_simple(user_input)
                if topics:
                    previous_topic = self.current_topic
                    self.current_topic = topics[0]
                    if previous_topic and previous_topic != self.current_topic:
                        self.topic_transitions.append({
                            'from': previous_topic,
                            'to': self.current_topic,
                            'timestamp': time.time()
                        })
                    if self.current_topic not in self.topic_history:
                        self.topic_history.append(self.current_topic)
                        if len(self.topic_history) > 20:
                            self.topic_history = self.topic_history[-15:]
            if bot_response:
                self._assess_engagement(user_input, bot_response)
                self._assess_conversation_quality(user_input, bot_response, intent)
                self._record_conversation_patterns(user_input, intent)
            self.dialogue_states.append({
                'turn': self.conversation_turns,
                'intent': intent,
                'topic': self.current_topic,
                'timestamp': time.time()
            })

    def _extract_topics_simple(self, text: str) -> list[str]:
        """简单提取话题"""
        import re
        chinese_words = re.findall(r'[一-龥]{2,}', text)
        stopwords = {'什么', '怎么', '为什么', '这个', '那个', '一个', '一些', '可以', '应该'}
        topics = [w for w in chinese_words if w not in stopwords and len(w) >= 2]
        return topics[:3]

    def _assess_engagement(self, user_input: str, bot_response: str):
        """评估参与度"""
        response_length = len(bot_response)
        user_length = len(user_input)
        if 20 <= response_length <= 180:
            self.engagement_score = min(1.0, self.engagement_score + 0.04)
        elif response_length > 250:
            self.engagement_score = max(0.2, self.engagement_score - 0.02)
        if user_length > 12:
            self.user_participation = min(1.0, self.user_participation + 0.04)
        elif user_length <= 3:
            self.user_participation = max(0.2, self.user_participation - 0.02)

    def _assess_conversation_quality(self, user_input: str, bot_response: str, intent: str):
        """评估对话质量"""
        quality_factors = []
        if self.current_topic and self.current_topic in bot_response:
            quality_factors.append(0.35)
        elif self.current_topic:
            quality_factors.append(0.15)
        else:
            quality_factors.append(0.25)
        if '?' in user_input and ('是' in bot_response or any(word in bot_response for word in ['是', '不是', '可能', '应该'])):
            quality_factors.append(0.25)
        else:
            quality_factors.append(0.15)
        if 15 <= len(bot_response) <= 250:
            quality_factors.append(0.2)
        else:
            quality_factors.append(0.1)
        avg_quality = sum(quality_factors) / len(quality_factors)
        self.conversation_quality = self.conversation_quality * 0.92 + avg_quality * 0.08

    def _record_conversation_patterns(self, user_input: str, intent: str):
        """记录对话模式"""
        if intent == 'greeting':
            self.conversation_patterns['greeting_patterns'].append(user_input[:40])
            if len(self.conversation_patterns['greeting_patterns']) > 15:
                self.conversation_patterns['greeting_patterns'] = self.conversation_patterns['greeting_patterns'][-8:]
        elif intent == 'farewell':
            self.conversation_patterns['farewell_patterns'].append(user_input[:40])
            if len(self.conversation_patterns['farewell_patterns']) > 15:
                self.conversation_patterns['farewell_patterns'] = self.conversation_patterns['farewell_patterns'][-8:]
        elif '?' in user_input:
            self.conversation_patterns['question_patterns'].append(user_input[:60])
            if len(self.conversation_patterns['question_patterns']) > 20:
                self.conversation_patterns['question_patterns'] = self.conversation_patterns['question_patterns'][-12:]
        else:
            self.conversation_patterns['statement_patterns'].append(user_input[:60])
            if len(self.conversation_patterns['statement_patterns']) > 25:
                self.conversation_patterns['statement_patterns'] = self.conversation_patterns['statement_patterns'][-18:]

    def get_conversation_insights(self) -> dict[str, Any]:
        """获取对话洞察"""
        insights = {
            'engagement_level': self.engagement_score,
            'conversation_quality': self.conversation_quality,
            'user_participation': self.user_participation,
            'avg_response_time': 0.0,
            'conversation_patterns': {
                'greeting_diversity': len(set(self.conversation_patterns['greeting_patterns'])),
                'question_frequency': len(self.conversation_patterns['question_patterns']),
                'statement_variety': len(set(self.conversation_patterns['statement_patterns'])),
            },
            'interaction_distribution': dict(self.interaction_types),
            'conversation_turns': self.conversation_turns,
            'topic_diversity': len(set(self.topic_history)),
        }
        if self.response_times:
            insights['avg_response_time'] = sum(self.response_times) / len(self.response_times)
        return insights

    def start_new_session(self):
        """开始新的对话会话"""
        if self.conversation_turns > 0:
            session_duration = time.time() - self.current_session_start
            self.conversation_sessions.append({
                'start_time': self.current_session_start,
                'end_time': time.time(),
                'duration': session_duration,
                'turns': self.conversation_turns,
                'avg_quality': self.conversation_quality,
                'topics': self.topic_history[-5:],
                'intent_distribution': dict(self.interaction_types)
            })
        self.current_session_start = time.time()
        self.conversation_turns = 0

    def add_message(self, user_input: str, bot_response: str, emotion: str = 'neutral', emotion_strength: float = 0.0):
        """添加消息到历史记录"""
        with self.lock:
            self._message_history.append({
                'user_input': user_input,
                'bot_response': bot_response,
                'emotion': emotion,
                'emotion_strength': emotion_strength,
                'timestamp': time.time(),
                'is_keyframe': emotion in self._keyframe_emotions and emotion_strength >= self._keyframe_threshold
            })
            
            self.conversation_turns += 1
            
            if len(self._message_history) >= self._compression_threshold:
                self._trigger_compression()

    def _trigger_compression(self):
        """触发对话摘要压缩"""
        if len(self._message_history) < 30:
            return
        
        _log.info(f"[对话压缩] 消息数量达到{len(self._message_history)}，开始压缩")
        
        messages_to_compress = list(self._message_history)[:20]
        keyframes = [m for m in messages_to_compress if m.get('is_keyframe', False)]
        recent_messages = list(self._message_history)[-10:]
        
        summary = self._generate_summary(messages_to_compress, keyframes)
        
        if summary:
            self._compressed_summaries.append({
                'summary': summary,
                'timestamp': time.time(),
                'original_count': len(messages_to_compress),
                'keyframe_count': len(keyframes)
            })
            
            if self.db:
                try:
                    self.db.add_memory(f"对话摘要_{int(time.time())}", summary, category='conversation_summary')
                    _log.info("[对话压缩] 摘要已保存到数据库")
                except Exception as e:
                    _log.error(f"[对话压缩] 保存摘要失败: {e}")
        
        self._message_history = deque(recent_messages)
        _log.info(f"[对话压缩] 完成，保留{len(self._message_history)}条消息")

    def _generate_summary(self, messages: list[dict], keyframes: list[dict]) -> str:
        """生成对话摘要"""
        if not messages:
            return ""
        
        conversation_text = "\n".join([
            f"用户: {m['user_input']}\n喵艺: {m['bot_response']}" 
            for m in messages
        ])
        
        keyframe_text = ""
        if keyframes:
            keyframe_text = "\n关键情感事件:\n" + "\n".join([
                f"- [{m['emotion']}] {m['user_input'][:30]}..." 
                for m in keyframes
            ])
        
        if self.llm:
            prompt = f"""
请为以下对话生成一段简洁的摘要（100字以内）：

对话内容:
{conversation_text[:1500]}

{keyframe_text}

摘要需要包含：
1. 主要人物和事件
2. 关键决定或结论
3. 重要情感变化
4. 保持自然流畅

直接给出摘要：
"""
            try:
                result = self.llm.chat(prompt, max_tokens=150, temperature=0.3)
                return result.strip()
            except Exception as e:
                _log.error(f"[对话压缩] LLM生成摘要失败: {e}")
        
        return self._simple_summary(messages, keyframes)

    def _simple_summary(self, messages: list[dict], keyframes: list[dict]) -> str:
        """简单摘要生成（备用）"""
        topics = set()
        emotions = []
        
        for m in messages:
            user_text = m['user_input']
            for word in ['喜欢', '讨厌', '想', '需要', '觉得', '认为']:
                if word in user_text:
                    topics.add(word + user_text[user_text.index(word)+1:user_text.index(word)+5])
            
            if m.get('emotion') and m.get('emotion') != 'neutral':
                emotions.append(m['emotion'])
        
        parts = []
        if topics:
            parts.append(f"讨论了: {', '.join(list(topics)[:3])}")
        if emotions:
            parts.append(f"情感: {', '.join(set(emotions))}")
        if keyframes:
            parts.append(f"关键事件: {len(keyframes)}个")
        
        return "，".join(parts) if parts else "对话摘要"

    def get_context(self) -> str:
        """获取对话上下文（包含压缩摘要）"""
        context_parts = []
        
        if self._compressed_summaries:
            recent_summaries = self._compressed_summaries[-3:]
            for summary in recent_summaries:
                context_parts.append(f"【之前对话】{summary['summary']}")
        
        if self.current_topic:
            context_parts.append(f"当前话题：{self.current_topic}")
        
        if self.topic_history:
            recent_topics = self.topic_history[-4:]
            context_parts.append(f"最近话题：{', '.join(recent_topics)}")
        
        if self.context_stack:
            stack_summaries = ' -> '.join([ctx['topic'] for ctx in self.context_stack[-3:]])
            context_parts.append(f"话题脉络：{stack_summaries}")
        
        recent_messages_context = self._get_recent_messages_context()
        if recent_messages_context:
            context_parts.append(recent_messages_context)
        
        return "；".join(context_parts) if context_parts else "新对话"

    def _get_recent_messages_context(self) -> str:
        """获取最近消息的上下文"""
        if len(self._message_history) < 3:
            return ""
        
        recent = list(self._message_history)[-3:]
        parts = []
        for m in recent:
            parts.append(f"用户: {m['user_input'][:40]}")
            parts.append(f"喵艺: {m['bot_response'][:40]}")
        
        return "最近对话: " + " | ".join(parts)

    def get_full_context(self) -> dict[str, Any]:
        """获取完整的对话上下文信息"""
        return {
            'message_count': len(self._message_history),
            'compressed_summary_count': len(self._compressed_summaries),
            'current_topic': self.current_topic,
            'topic_history': self.topic_history[-5:],
            'engagement_score': self.engagement_score,
            'conversation_turns': self.conversation_turns,
            'recent_summaries': [s['summary'] for s in self._compressed_summaries[-3:]],
            'recent_messages': list(self._message_history)[-5:]
        }