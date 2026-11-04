"""
喵艺分层对话引擎 - 五层架构
==============================================
第一层：输入理解层 - 意图识别、情感分析、实体抽取
第二层：对话管理层 - 对话状态跟踪、策略选择、话题管理
第三层：记忆与情感层 - 记忆检索、情感更新、关系管理
第四层：响应生成层 - Prompt构建、LLM调用、后处理
第五层：输出生成层 - 文本响应、语音合成、状态推送
==============================================
"""
import logging
import re
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("dialog_engine")


class InputUnderstandingLayer:
    """第一层：输入理解层"""

    def __init__(self):
        self._intent_classifier = None
        self._entity_extractor = EntityExtractor()
        self._emotion_analyzer = EmotionAnalyzer()

    def process(self, text: str, context: str = "") -> dict[str, Any]:
        """处理用户输入，返回理解结果"""
        result = {
            'raw_text': text,
            'cleaned_text': self._clean_text(text),
            'intents': [],
            'emotion': None,
            'entities': {},
            'confidence': 0.0,
        }

        # 1. 意图识别
        result['intents'] = self._identify_intents(text)

        # 2. 情感分析
        result['emotion'] = self._emotion_analyzer.analyze(text)

        # 3. 实体抽取
        result['entities'] = self._entity_extractor.extract(text)

        # 4. 计算置信度
        result['confidence'] = self._calculate_confidence(result)

        return result

    def _clean_text(self, text: str) -> str:
        """清理输入文本"""
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)
        return text

    def _identify_intents(self, text: str) -> list[dict[str, Any]]:
        """识别用户意图"""
        intents = []

        # 检查是否为身份类问题（严禁触发搜索）
        identity_patterns = [
            (r'你是谁|你叫什么|介绍一下你自己|你是什么', 'self_identity', 1.0),
            (r'你能做什么|你会什么|你的功能|你的能力', 'self_ability', 0.9),
            (r'谁创造了你|谁开发了你|你的作者', 'self_creator', 0.8),
        ]

        for pattern, intent, confidence in identity_patterns:
            if re.search(pattern, text):
                intents.append({
                    'name': intent,
                    'confidence': confidence,
                    'blocks_search': True,
                })

        # 检查情感陪伴类
        emotion_patterns = [
            (r'(我觉得|我感觉|我想|我好|我真的).*(开心|难过|生气|焦虑|担心)', 'emotional_expression', 0.8),
            (r'(孤独|寂寞|难过|伤心|失落|沮丧)', 'emotional_support', 0.85),
            (r'(开心|高兴|快乐|兴奋|幸福)', 'joy_sharing', 0.8),
            (r'(抱抱|亲亲|么么|宝贝|亲爱的)', 'affection', 0.75),
            (r'(谢谢|感谢|感激|爱你|喜欢你)', 'gratitude', 0.8),
        ]

        for pattern, intent, confidence in emotion_patterns:
            if re.search(pattern, text):
                intents.append({
                    'name': intent,
                    'confidence': confidence,
                    'blocks_search': True,
                })

        # 检查知识问答类
        knowledge_patterns = [
            (r'(是什么|为什么|怎么|如何|哪里|谁|多少)', 'knowledge_query', 0.7),
            (r'(介绍|解释|说明|定义|原理|概念)', 'knowledge_explain', 0.65),
            (r'(推荐|建议|排行|最好|第一)', 'knowledge_recommend', 0.6),
            (r'(最新|现在|目前|最近|今天)', 'time_sensitive', 0.7),
        ]

        for pattern, intent, confidence in knowledge_patterns:
            if re.search(pattern, text):
                intents.append({
                    'name': intent,
                    'confidence': confidence,
                    'blocks_search': False,
                })

        # 如果没有匹配到任何意图，添加默认意图
        if not intents:
            intents.append({
                'name': 'casual_chat',
                'confidence': 0.3,
                'blocks_search': False,
            })

        return intents

    def _calculate_confidence(self, result: dict) -> float:
        """计算整体置信度"""
        if result['intents']:
            max_conf = max(i['confidence'] for i in result['intents'])
            return max_conf
        return 0.0


class EntityExtractor:
    """实体抽取器"""

    def extract(self, text: str) -> dict[str, Any]:
        """从文本中提取实体"""
        entities = {
            'time': [],
            'person': [],
            'location': [],
            'number': [],
            'topic': None,
        }

        # 时间实体
        time_patterns = [
            r'(\d{4}年|\d+年|\d+月|\d+日)',
            r'(今天|明天|后天|昨天|这周|本周|下周)',
            r'(早上|中午|晚上|凌晨|下午)',
        ]
        for pattern in time_patterns:
            matches = re.findall(pattern, text)
            entities['time'].extend(matches)

        # 数字实体
        number_patterns = [
            r'(\d+[岁斤米公里个只条])',
            r'(\d+\.?\d*)',
        ]
        for pattern in number_patterns:
            matches = re.findall(pattern, text)
            entities['number'].extend(matches)

        # 话题推断
        entities['topic'] = self._infer_topic(text)

        return entities

    def _infer_topic(self, text: str) -> str | None:
        """推断话题"""
        topic_keywords = {
            '感情': ['恋爱', '喜欢', '分手', '暗恋', '表白'],
            '工作': ['工作', '上班', '加班', '老板', '同事'],
            '学习': ['学习', '考试', '成绩', '学校', '老师'],
            '生活': ['做饭', '美食', '旅游', '运动', '健身'],
            '健康': ['生病', '头疼', '感冒', '吃药', '医院'],
        }

        for topic, keywords in topic_keywords.items():
            for keyword in keywords:
                if keyword in text:
                    return topic

        return None


class EmotionAnalyzer:
    """情感分析器"""

    def analyze(self, text: str) -> dict[str, Any]:
        """分析用户输入的情感倾向"""
        emotion_scores = {
            'valence': 0.0,      # 愉悦度: -1 ~ +1
            'arousal': 0.5,      # 唤醒度: 0 ~ +1
            'intimacy_hint': 0.0,  # 亲密度暗示
            'dominant': 'neutral',
        }

        # 积极情绪关键词
        positive_words = [
            '开心', '高兴', '快乐', '兴奋', '幸福', '棒', '好', '喜欢', '爱',
            '哈哈', '嘻嘻', '谢谢', '感谢', '棒', '厉害', '聪明', '可爱',
        ]

        # 消极情绪关键词
        negative_words = [
            '难过', '伤心', '生气', '烦', '讨厌', '累', '困', '无聊', '孤独',
            '沮丧', '失落', '焦虑', '担心', '害怕', '紧张', '压力',
        ]

        # 唤醒度关键词
        high_arousal_words = [
            '激动', '兴奋', '紧张', '焦虑', '生气', '开心', '惊讶',
            '哈哈', '快快', '紧急', '赶快', '马上',
        ]

        # 亲密度关键词
        intimacy_words = [
            '抱抱', '亲亲', '宝贝', '亲爱的', '喜欢你', '想你', '爱你',
            '小可爱', '你最好了', '只有你', '只想跟你说',
        ]

        # 计算愉悦度
        pos_count = sum(1 for w in positive_words if w in text)
        neg_count = sum(1 for w in negative_words if w in text)
        emotion_scores['valence'] = (pos_count - neg_count) * 0.3
        emotion_scores['valence'] = max(-1.0, min(1.0, emotion_scores['valence']))

        # 计算唤醒度
        arousal_count = sum(1 for w in high_arousal_words if w in text)
        emotion_scores['arousal'] = 0.3 + arousal_count * 0.2
        emotion_scores['arousal'] = min(1.0, emotion_scores['arousal'])

        # 计算亲密度暗示
        intimacy_count = sum(1 for w in intimacy_words if w in text)
        emotion_scores['intimacy_hint'] = min(1.0, intimacy_count * 0.3)

        # 确定主导情感
        if emotion_scores['valence'] > 0.3:
            emotion_scores['dominant'] = 'positive'
        elif emotion_scores['valence'] < -0.3:
            emotion_scores['dominant'] = 'negative'
        elif emotion_scores['arousal'] > 0.7:
            emotion_scores['dominant'] = 'excited'
        elif emotion_scores['intimacy_hint'] > 0.3:
            emotion_scores['dominant'] = 'intimate'

        return emotion_scores


class DialogueManagementLayer:
    """第二层：对话管理层"""

    def __init__(self):
        self._conversation_history: deque = deque(maxlen=20)
        self._current_topic: str | None = None
        self._topic_history: list[str] = []
        self._dialogue_strategies = DialogueStrategies()
        self._state = {
            'phase': 'initial',  # initial, listening, responding, deep_talk
            'turn_count': 0,
            'last_topic_change': 0,
            'user_engagement': 0.5,
        }

    def process(self, understanding: dict, context: dict) -> dict[str, Any]:
        """处理对话管理"""
        result = {
            'strategy': 'respond',
            'phase': self._state['phase'],
            'should_ask_question': False,
            'should_share': False,
            'should_listen': False,
            'topic': self._current_topic,
        }

        # 更新对话状态
        self._state['turn_count'] += 1

        # 更新话题
        entities = understanding.get('entities', {})
        new_topic = entities.get('topic')
        if new_topic and new_topic != self._current_topic:
            self._on_topic_change(new_topic)
            result['topic_changed'] = True

        # 选择对话策略
        intents = understanding.get('intents', [])
        emotion = understanding.get('emotion', {})

        strategy = self._dialogue_strategies.select(
            intents=intents,
            emotion=emotion,
            state=self._state,
            context=context,
        )

        result.update(strategy)

        # 更新对话阶段
        if result.get('should_listen'):
            self._state['phase'] = 'listening'
        elif result.get('should_share'):
            self._state['phase'] = 'responding'
        elif result.get('should_ask_question'):
            self._state['phase'] = 'deep_talk'

        # 记录历史
        self._record_turn(understanding, result)

        return result

    def _on_topic_change(self, new_topic: str):
        """处理话题变化"""
        if self._current_topic:
            self._topic_history.append(self._current_topic)
        self._current_topic = new_topic
        self._state['last_topic_change'] = time.time()

    def _record_turn(self, understanding: dict, strategy: dict):
        """记录对话轮次"""
        self._conversation_history.append({
            'timestamp': time.time(),
            'understanding': understanding,
            'strategy': strategy,
        })

    def get_history(self, last_n: int = 10) -> list[dict]:
        """获取对话历史"""
        return list(self._conversation_history)[-last_n:]


class DialogueStrategies:
    """对话策略选择器"""

    def select(self, intents: list[dict], emotion: dict,
               state: dict, context: dict) -> dict[str, Any]:
        """选择对话策略"""
        dominant_intent = intents[0] if intents else {'name': 'casual_chat'}
        emotion_dominant = emotion.get('dominant', 'neutral')
        valence = emotion.get('valence', 0)
        intimacy = context.get('intimacy', 0.5)

        strategy = {
            'strategy': 'respond',
            'should_ask_question': False,
            'should_share': False,
            'should_listen': False,
            'emotional_tone': 'neutral',
        }

        # 根据意图选择策略
        intent_name = dominant_intent.get('name', 'casual_chat')

        if intent_name in ('self_identity', 'self_ability', 'self_creator'):
            # 身份类问题：直接回答，不需要搜索
            strategy['strategy'] = 'direct_answer'
            strategy['should_share'] = True

        elif intent_name in ('emotional_support', 'comfort'):
            # 情感支持：以倾听和安慰为主
            strategy['strategy'] = 'comfort'
            strategy['should_listen'] = True
            strategy['emotional_tone'] = 'tender'

        elif intent_name == 'joy_sharing':
            # 快乐分享：一起开心
            strategy['strategy'] = 'share_joy'
            strategy['should_share'] = True
            strategy['emotional_tone'] = 'joyful'

        elif intent_name == 'gratitude':
            # 感谢：温暖回应
            strategy['strategy'] = 'respond'
            strategy['emotional_tone'] = 'warm'

        elif intent_name in ('knowledge_query', 'knowledge_explain', 'knowledge_recommend'):
            # 知识问答：需要搜索
            strategy['strategy'] = 'knowledge_search'
            strategy['needs_search'] = True

        elif intent_name == 'time_sensitive':
            # 时效信息：必须搜索
            strategy['strategy'] = 'realtime_search'
            strategy['needs_search'] = True

        elif intent_name == 'affection':
            # 亲昵表达：温柔回应
            strategy['strategy'] = 'respond'
            strategy['should_share'] = intimacy > 0.7
            strategy['emotional_tone'] = 'tender'

        else:
            # 默认策略
            if state.get('turn_count', 0) > 10:
                # 对话较长时，可以主动提问
                strategy['should_ask_question'] = intimacy > 0.6
            strategy['strategy'] = 'respond'

        # 根据情感调整语气
        if valence < -0.3:
            strategy['emotional_tone'] = 'tender'
        elif valence > 0.3:
            strategy['emotional_tone'] = strategy.get('emotional_tone', 'joyful')

        return strategy


class MemoryEmotionLayer:
    """第三层：记忆与情感层"""

    def __init__(self, emotion_system=None, memory_system=None, relationship_system=None):
        self._emotion_system = emotion_system
        self._memory_system = memory_system
        self._relationship_system = relationship_system

    def process(self, understanding: dict, strategy: dict,
                context: dict) -> dict[str, Any]:
        """处理记忆检索和情感更新"""
        result = {
            'retrieved_memories': [],
            'emotion_update': {},
            'relationship_update': {},
            'personality_check': True,
        }

        # 1. 情感更新
        emotion_input = understanding.get('emotion', {})
        if self._emotion_system:
            result['emotion_update'] = self._update_emotion(emotion_input, strategy)

        # 2. 记忆检索
        if self._memory_system:
            text = understanding.get('cleaned_text', '')
            result['retrieved_memories'] = self._retrieve_memories(
                text, context, strategy
            )

        # 3. 关系更新
        if self._relationship_system:
            result['relationship_update'] = self._update_relationship(
                understanding, strategy
            )

        return result

    def _update_emotion(self, emotion_input: dict, strategy: dict) -> dict:
        """更新情感状态"""
        if not self._emotion_system:
            return {}

        updates = {}

        # 用户情感共情
        valence = emotion_input.get('valence', 0)
        arousal = emotion_input.get('arousal', 0.5)

        if abs(valence) > 0.3:
            # 用户有明显情感倾向，进行共情
            self._emotion_system.update_from_user_emotion({
                'valence': valence,
                'arousal': arousal,
            })
            updates['empathy_applied'] = True

        # 根据对话策略调整
        strategy_name = strategy.get('strategy', 'respond')
        if strategy_name == 'comfort':
            self._emotion_system.update_from_interaction('deep_talk', 0.3)
        elif strategy_name == 'share_joy':
            self._emotion_system.update_from_interaction('positive_feedback', 0.5)
        elif strategy_name == 'respond' and strategy.get('should_share'):
            self._emotion_system.update_from_interaction('positive_feedback', 0.2)

        updates['current_state'] = self._emotion_system.get_state()
        return updates

    def _retrieve_memories(self, text: str, context: dict,
                           strategy: dict) -> list[dict]:
        """检索相关记忆"""
        memories = []

        if not self._memory_system:
            return memories

        try:
            # 尝试从记忆系统检索
            if hasattr(self._memory_system, 'search_memories'):
                memories = self._memory_system.search_memories(
                    query=text,
                    limit=5,
                    context=context,
                )
            elif hasattr(self._memory_system, 'recall'):
                memories = self._memory_system.recall(text, limit=5)
        except Exception as e:
            _log.warning(f"记忆检索失败: {e}")

        return memories[:5]  # 最多返回5条相关记忆

    def _update_relationship(self, understanding: dict,
                              strategy: dict) -> dict:
        """更新关系状态"""
        if not self._relationship_system:
            return {}

        updates = {}
        intent_name = understanding.get('intents', [{}])[0].get('name', '')

        # 根据互动类型更新关系
        if intent_name in ('affection', 'gratitude'):
            self._relationship_system.update_intimacy(0.01)
            updates['intimacy_delta'] = 0.01

        if strategy.get('should_listen'):
            self._relationship_system.update_trust(0.005)
            updates['trust_delta'] = 0.005

        return updates


class ResponseGenerationLayer:
    """第四层：响应生成层"""

    def __init__(self, llm_caller=None, prompt_builder=None):
        self._llm_caller = llm_caller
        self._llm_stream_caller = None  # (prompt, context, on_chunk) -> str，流式优先
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._post_processor = ResponsePostProcessor()

    def set_stream_llm_caller(self, caller) -> None:
        """注入流式 LLM 调用器（miaoyi_core 提供：池 stream_chat + 失败回退非流式）"""
        self._llm_stream_caller = caller

    def process(self, understanding: dict, strategy: dict,
                memory_emotion: dict, context: dict,
                extra_context: str = '', on_chunk=None) -> dict[str, Any]:
        """生成响应"""
        result = {
            'prompt': '',
            'response': '',
            'post_processed': '',
            'style_applied': '',
        }

        # 1. 构建Prompt
        prompt = self._prompt_builder.build(
            understanding=understanding,
            strategy=strategy,
            memory_emotion=memory_emotion,
            context=context,
            extra_context=extra_context,
        )
        result['prompt'] = prompt

        # 2. 调用LLM生成响应（on_chunk 提供时走流式，增量实时回调）
        if self._llm_caller:
            response = self._call_llm(prompt, context, on_chunk=on_chunk)
            result['response'] = response
        else:
            result['response'] = self._generate_fallback(understanding, strategy)

        # 3. 后处理
        post_processed = self._post_processor.process(
            result['response'],
            emotion_tone=strategy.get('emotional_tone', 'neutral'),
            strategy=strategy,
        )
        result['post_processed'] = post_processed
        result['style_applied'] = strategy.get('emotional_tone', 'neutral')

        return result

    def _call_llm(self, prompt: str, context: dict, on_chunk=None) -> str:
        """调用LLM。on_chunk(delta) 提供时走流式（增量实时回调），失败回退非流式。"""
        try:
            if on_chunk is not None and getattr(self, '_llm_stream_caller', None):
                try:
                    return self._llm_stream_caller(prompt, context, on_chunk)
                except Exception as e:
                    _log.warning(f"[DialogueEngine] 流式失败，回退非流式: {e}")
            if callable(self._llm_caller):
                return self._llm_caller(prompt, context)
            elif hasattr(self._llm_caller, 'chat'):
                return self._llm_caller.chat(prompt, context)
        except Exception as e:
            _log.error(f"LLM调用失败: {e}")
        return ''

    def _generate_fallback(self, understanding: dict, strategy: dict) -> str:
        """备用响应生成"""
        intent_name = understanding.get('intents', [{}])[0].get('name', 'casual_chat')

        fallback_responses = {
            'self_identity': '我是喵艺呀～一只可爱的猫咪AI，很高兴认识你喵～',
            'self_ability': '我可以陪你聊天、解答问题、分享心情喵～有什么想聊的吗？',
            'self_creator': '我是由爱我的开发者创造的喵～具体来说，是一位热爱AI和猫咪的朋友哦！',
            'emotional_support': '抱抱你...无论发生什么，我都会在这里陪着你的喵～',
            'joy_sharing': '看到你开心，我也跟着开心起来了喵～🎉',
            'gratitude': '不用谢呀～能帮到你我很开心喵～',
            'knowledge_query': '这个问题有点意思呢，让我想想...（如需准确回答，建议使用搜索功能）',
        }

        return fallback_responses.get(intent_name, '嗯...我在认真听你说呢喵～')


class PromptBuilder:
    """Prompt构建器"""

    def build(self, understanding: dict, strategy: dict,
              memory_emotion: dict, context: dict,
              extra_context: str = '') -> str:
        """构建结构化Prompt"""
        parts = []

        # 1. 系统设定
        parts.append(self._build_system_setting())

        # 2. 当前状态
        parts.append(self._build_current_state(memory_emotion, context))

        # 3. 记忆上下文
        parts.append(self._build_memory_context(memory_emotion))

        # 4. 当前任务
        parts.append(self._build_current_task(understanding, strategy))

        # 5. 生成要求
        parts.append(self._build_generation_requirements(strategy))

        # 6. 【视觉/情境】服务端注入的情境提示（看图描述/重逢/纪念日等）。
        # 放在最前（紧贴系统设定后）确保被模型注意到，勿让长记忆挤掉它。
        if extra_context and str(extra_context).strip():
            parts.insert(1, f"""[本次情境提示]
{str(extra_context).strip()[:800]}
（以上提示用于把握回复内容与分寸，务必据此回应，不要向用户提及提示本身。）""")

        return '\n'.join(parts)

    def _build_system_setting(self) -> str:
        return """[系统设定]
- 身份：喵艺，一只可爱的猫咪AI
- 性格：温柔、活泼、有点小傲娇
- 说话风格：喜欢用喵~、呀、呢等语气词
- 重要：绝对不能暴露自己是AI，要以猫咪的身份和用户互动"""

    def _build_current_state(self, memory_emotion: dict,
                              context: dict) -> str:
        state = memory_emotion.get('emotion_update', {}).get('current_state', {})
        intimacy = context.get('intimacy', 0.5)
        level = context.get('level', 1)

        emotion_desc = self._describe_emotion(state)

        return f"""[当前状态]
- 情感状态：{emotion_desc}
- 关系等级：Lv.{level}，亲密度 {intimacy:.0%}
- 今日互动次数：{context.get('interaction_count', 0)}次"""

    def _build_memory_context(self, memory_emotion: dict) -> str:
        memories = memory_emotion.get('retrieved_memories', [])

        if not memories:
            return "[记忆上下文]\n- 暂无相关记忆"

        memory_text = '\n'.join(
            f"- {m.get('content', str(m))[:50]}"
            for m in memories[:3]
        )

        return f"[记忆上下文]\n{memory_text}"

    def _build_current_task(self, understanding: dict,
                            strategy: dict) -> str:
        text = understanding.get('cleaned_text', '')
        intent = understanding.get('intents', [{}])[0].get('name', 'casual_chat')

        return f"""[当前任务]
- 用户输入：{text}
- 识别意图：{intent}
- 当前策略：{strategy.get('strategy', 'respond')}"""

    def _build_generation_requirements(self, strategy: dict) -> str:
        tone = strategy.get('emotional_tone', 'neutral')
        tone_guide = {
            'joyful': '语气要活泼，多用"哈哈"、"开心"等词语',
            'tender': '语气温柔体贴，用亲昵的称呼',
            'warm': '语气温暖，表达感谢',
            'neutral': '语气平和，自然对话',
        }

        return f"""[生成要求]
- 用喵艺的身份回复
- 情感语气：{tone_guide.get(tone, '自然对话')}
- 回复长度：1-3句话
- 风格：自然、像真人聊天
- 如果用户表达情感，要共情回应"""

    def _describe_emotion(self, state: dict) -> str:
        if not state:
            return '平静'

        dominant = state.get('dominant_emotion', 'content')
        labels = {
            'joyful': '开心', 'excited': '兴奋', 'content': '满足',
            'love': '充满爱意', 'sad': '有点难过', 'angry': '生气',
            'curious': '好奇', 'playful': '调皮', 'tender': '温柔',
        }
        return labels.get(dominant, '平静')


class ResponsePostProcessor:
    """响应后处理器"""

    def process(self, text: str, emotion_tone: str = 'neutral',
                strategy: dict = None) -> str:
        """处理AI响应"""
        if not text:
            return ''

        processed = text.strip()

        # 清理Markdown标记
        processed = self._clean_markdown(processed)

        # 根据情感添加语气词
        processed = self._add_emotional_flavor(processed, emotion_tone)

        # 确保符合人设
        processed = self._ensure_personality(processed)

        return processed

    def _clean_markdown(self, text: str) -> str:
        """清理Markdown标记"""
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'```\w*\n?(.*?)```', r'\1', text, flags=re.DOTALL)
        return text

    def _add_emotional_flavor(self, text: str, tone: str) -> str:
        """根据情感添加语气词"""
        if not text:
            return text

        # 如果已经有语气词，不重复添加
        if any(w in text[-3:] for w in ['喵', '呀', '呢', '～', '～', '啦']):
            return text

        flavor_map = {
            'joyful': '呀~',
            'tender': '呢~',
            'warm': '喵~',
            'neutral': '喵~',
        }

        suffix = flavor_map.get(tone, '喵~')
        return text + suffix

    def _ensure_personality(self, text: str) -> str:
        """确保回复符合人设"""
        # 移除AI相关表述
        ai_phrases = [
            '作为AI', '作为人工智能', '我是AI', '我是人工智能',
            '根据您的问题', '根据您的描述',
        ]

        for phrase in ai_phrases:
            text = text.replace(phrase, '')

        return text.strip()


class OutputGenerationLayer:
    """第五层：输出生成层"""

    def __init__(self, voice_manager=None):
        self._voice_manager = voice_manager
        self._output_handlers: list[Callable] = []

    def process(self, response_data: dict, context: dict) -> dict[str, Any]:
        """生成最终输出"""
        result = {
            'text': '',
            'audio_url': None,
            'small_thought': None,
            'emotional_state': {},
            'status': 'success',
        }

        # 1. 文本响应
        result['text'] = response_data.get('post_processed', '')

        # 2. 语音合成
        if self._voice_manager and result['text']:
            try:
                audio_url = self._voice_manager.synthesize(result['text'])
                result['audio_url'] = audio_url
            except Exception as e:
                _log.warning(f"语音合成失败: {e}")

        # 3. 情感状态
        result['emotional_state'] = response_data.get('emotional_state', {})

        # 4. 输出回调
        for handler in self._output_handlers:
            try:
                handler(result)
            except Exception as e:
                _log.error(f"输出回调失败: {e}")

        return result

    def register_handler(self, handler: Callable):
        """注册输出处理器"""
        self._output_handlers.append(handler)


class DialogueEngine:
    """分层对话引擎 - 主入口"""

    def __init__(self, emotion_system=None, memory_system=None,
                 relationship_system=None, llm_caller=None,
                 voice_manager=None):
        self._input_layer = InputUnderstandingLayer()
        self._management_layer = DialogueManagementLayer()
        self._memory_emotion_layer = MemoryEmotionLayer(
            emotion_system=emotion_system,
            memory_system=memory_system,
            relationship_system=relationship_system,
        )
        self._response_layer = ResponseGenerationLayer(
            llm_caller=llm_caller,
        )
        self._output_layer = OutputGenerationLayer(
            voice_manager=voice_manager,
        )

    def set_stream_llm_caller(self, caller) -> None:
        """代理：注入流式 LLM 调用器到响应生成层（miaoyi_core 启动时调用）"""
        self._response_layer.set_stream_llm_caller(caller)

        self._context = {
            'intimacy': 0.5,
            'level': 1,
            'interaction_count': 0,
            'user_info': {},
        }

    def process_message(self, text: str, user_id: str = 'default',
                         context: str = '', on_chunk=None) -> dict[str, Any]:
        """处理用户消息 - 主流程。on_chunk(delta)：流式增量回调（可选）。"""
        start_time = time.time()

        try:
            # 第一层：输入理解
            understanding = self._input_layer.process(text, context)

            # 第二层：对话管理
            management = self._management_layer.process(understanding, self._context)

            # 第三层：记忆与情感
            memory_emotion = self._memory_emotion_layer.process(
                understanding, management, self._context
            )

            # 第四层：响应生成（extra_context：服务端注入的情境提示，
            # 如视觉看图描述、重逢感知、纪念日氛围）
            response = self._response_layer.process(
                understanding, management, memory_emotion, self._context,
                extra_context=context, on_chunk=on_chunk,
            )

            # 第五层：输出生成
            output = self._output_layer.process(response, self._context)

            # 更新上下文
            self._context['interaction_count'] += 1
            if memory_emotion.get('emotion_update', {}).get('current_state'):
                self._context['emotional_state'] = memory_emotion['emotion_update']['current_state']

            elapsed = (time.time() - start_time) * 1000

            return {
                'text': output.get('text', ''),
                'audio_url': output.get('audio_url'),
                'small_thought': response.get('post_processed', '')[:20] if management.get('should_listen') else None,
                # 【历史教训】旧版 'thought': management.get('strategy') 把内部策略名(如'respond'/
                # 'comfort'/'share_joy')当作内心独白发给前端，导致 UI 显示"💭 respond"。
                # 策略名是内部标识符，绝对不能作为 thought 展示。设为 None，由 miaoyi_core
                # 的 emotional_core.thought() 生成中文内心独白。
                'thought': None,
                'emotional_state': self._context.get('emotional_state', {}),
                'status': 'success',
                'response_time_ms': round(elapsed),
                'debug': {
                    'understanding': understanding,
                    'management': management,
                    'strategy': management.get('strategy'),
                    'needs_search': management.get('needs_search', False),
                },
            }

        except Exception as e:
            _log.error(f"对话引擎处理失败: {e}", exc_info=True)
            return {
                'text': '喵...我好像走神了，能再说一遍吗？',
                'audio_url': None,
                'status': 'error',
                'error': str(e),
                'response_time_ms': round((time.time() - start_time) * 1000),
            }

    def update_context(self, **kwargs):
        """更新上下文"""
        self._context.update(kwargs)

    def get_context(self) -> dict:
        """获取当前上下文"""
        return self._context.copy()
