#!/usr/bin/env python3
"""
  🐱 喵艺深度思考引擎 - 完整迁移自main.py的DeepThinkingEngine
  包含11组件思考日志系统：表层理解、情感分析、深层意图、上下文分析、
  激活记忆、知识需求、情感共振、关系背景、回应策略、自我反思、建议开头
"""
import logging
import random
import re
import threading
import time
from collections import deque
from typing import Any, Dict

_log = logging.getLogger(__name__)


class DeepThinkingEngine:
    DEPTH_LEVELS = {
        'shallow': {'components': ['surface_meaning', 'emotional_subtext', 'real_intent', 'response_strategy'], 'steps': 3},
        'normal': {'components': ['surface_meaning', 'emotional_subtext', 'real_intent', 'context_analysis', 'knowledge_need', 'response_strategy', 'reflection'], 'steps': 5},
        'deep': {'components': ['surface_meaning', 'emotional_subtext', 'real_intent', 'context_analysis', 'knowledge_need', 'emotional_resonance', 'personality_influence', 'relationship_context', 'response_strategy', 'reflection'], 'steps': 8},
        'profound': {'components': ['surface_meaning', 'emotional_subtext', 'real_intent', 'context_analysis', 'knowledge_need', 'emotional_resonance', 'personality_influence', 'relationship_context', 'time_context', 'response_strategy', 'reflection', 'free_associations'], 'steps': 11},
    }

    def __init__(self, emotion_sys, memory_sys, personality_sys, relationship_sys, time_ctx):
        self.emotion = emotion_sys
        self.memory = memory_sys
        self.personality = personality_sys
        self.relationship = relationship_sys
        self.time_ctx = time_ctx
        self._think_log = deque(maxlen=50)
        self._context_history = deque(maxlen=20)
        self._reflection_log = deque(maxlen=20)
        self._active_memories = []
        self._dirty = False
        self._lock = threading.Lock()
        self._default_depth = 'deep'

    def analyze(self, user_input: str, intent: str, conversation_history: list = None, depth: str = None) -> dict:
        depth = depth or self._default_depth
        depth_config = self.DEPTH_LEVELS.get(depth, self.DEPTH_LEVELS['deep'])
        components = depth_config['components']
        
        result = {
            'surface_meaning': '', 'emotional_subtext': '', 'real_intent': '',
            'knowledge_need': '', 'response_strategy': '', 'emotional_resonance': '',
            'personality_influence': '', 'relationship_context': '', 'time_context': '',
            'full_thinking': '', 'context_analysis': '', 'reflection': '',
            'recalled_memories': [], 'suggested_response': '',
            'free_associations': [],
            'thinking_depth': depth,
            'thinking_steps': depth_config['steps'],
        }
        
        self._context_history.append({
            'input': user_input[:100],
            'intent': intent,
            'ts': time.time(),
        })
        
        if 'free_associations' in components or 'recalled_memories' in components:
            self._active_memories = self._recall_relevant_memories(user_input)
            if 'free_associations' in components:
                associations = self._freely_associate(user_input)
                result['free_associations'] = associations
        
        if 'surface_meaning' in components:
            result['surface_meaning'] = self._analyze_semantics(user_input, intent)
        if 'emotional_subtext' in components:
            result['emotional_subtext'] = self._analyze_emotion(user_input)
        if 'real_intent' in components:
            result['real_intent'] = self._analyze_deep_intent(user_input, intent)
        if 'context_analysis' in components:
            result['context_analysis'] = self._analyze_context(conversation_history)
        if 'knowledge_need' in components:
            result['knowledge_need'] = self._analyze_knowledge_need(user_input, intent)
        if 'emotional_resonance' in components:
            result['emotional_resonance'] = self._analyze_resonance(user_input)
        if 'personality_influence' in components:
            result['personality_influence'] = self._analyze_personality()
        if 'relationship_context' in components:
            result['relationship_context'] = self._analyze_relationship()
        if 'time_context' in components:
            result['time_context'] = self.time_ctx.get_context_str()
        if 'recalled_memories' in components:
            result['recalled_memories'] = self._active_memories
        
        result['response_strategy'] = self._determine_strategy(result)
        
        if 'reflection' in components:
            result['reflection'] = self._reflect_on_previous_interactions(result)
        
        result['suggested_response'] = self._generate_suggested_response(result)
        result['full_thinking'] = self._compose_thinking(result)
        
        self._think_log.append({
            'input': user_input[:50],
            'intent': intent,
            'strategy': result['response_strategy'],
            'reflection': result['reflection'][:100] if result['reflection'] else '',
            'depth': depth,
            'ts': time.time(),
        })
        self._dirty = True
        return result

    def _recall_relevant_memories(self, user_input: str) -> list:
        memories = []
        try:
            keywords = self._extract_keywords(user_input)
            for kw in keywords[:5]:
                mem = self.memory.recall(kw) if hasattr(self.memory, 'recall') else None
                if mem:
                    memories.append({'keyword': kw, 'memory': str(mem)[:100]})
            
            recent_topics = []
            for ctx in list(self._context_history)[:-1]:
                ctx_keywords = self._extract_keywords(ctx.get('input', ''))
                recent_topics.extend(ctx_keywords)
            
            for topic in list(set(recent_topics))[:5]:
                if topic not in [m['keyword'] for m in memories]:
                    mem = self.memory.recall(topic) if hasattr(self.memory, 'recall') else None
                    if mem:
                        memories.append({'keyword': topic, 'memory': str(mem)[:100], 'context': 'recent'})
            
            user_info = self.memory.recall('用户名字') if hasattr(self.memory, 'recall') else None
            if user_info:
                memories.append({'keyword': '用户名字', 'memory': str(user_info), 'context': 'profile'})
            
            preferences = self.memory.recall('用户偏好') if hasattr(self.memory, 'recall') else None
            if preferences:
                memories.append({'keyword': '用户偏好', 'memory': str(preferences)[:100], 'context': 'profile'})
        except Exception as e:
            _log.debug(f"回忆记忆失败: {e}")
        
        return memories

    def _extract_keywords(self, text: str) -> list:
        keywords = []
        topic_keywords = [
            '吃饭', '工作', '学习', '游戏', '电影', '音乐', '旅行', '运动',
            '天气', '时间', '生日', '节日', '朋友', '家人', '宠物',
            '心情', '感觉', '想法', '问题', '帮助', '建议',
            '喜欢', '讨厌', '想要', '需要', '希望',
        ]
        for kw in topic_keywords:
            if kw in text:
                keywords.append(kw)
        return keywords

    def _freely_associate(self, topic: str) -> list:
        associations = []
        try:
            if hasattr(self.memory, 'associate_freely'):
                memories = self.memory.associate_freely(topic, limit=3)
                for mem in memories:
                    kw = mem.get('keyword', '')
                    memory = mem.get('memory', '')[:50]
                    mem_type = mem.get('type', '')
                    if kw and memory:
                        if mem_type == 'direct':
                            associations.append(f"（说到{kw}，我记得你提过{memory}...）")
                        elif mem_type == 'associative':
                            associations.append(f"（{kw}...让我想到了{memory}...）")
                        elif mem_type == 'category':
                            associations.append(f"（{kw}和{memory}好像有点关联呢...）")
                        else:
                            associations.append(f"（说到{kw}，突然想到{memory}...）")

            free_association_triggers = {
                '雨': ['下雨天', '潮湿', '伞', '街道', '花猫', '便利店', '泡面'],
                '泡面': ['便利店', '深夜', '热气', '鸡蛋', '雨', '猫咪'],
                '咖啡': ['下雨天', '书店', '深夜', '温暖', '思绪'],
                '阳光': ['温暖', '棉被', '猫咪', '窗台', '午后'],
                '夜晚': ['星星', '音乐', '安静', '思念', '咖啡'],
                '猫': ['猫咪', '花猫', '撒娇', '呼噜', '柔软'],
                '开心': ['笑', '阳光', '蹦跳', '温暖', '拥抱'],
                '难过': ['雨', '旧书店', '咖啡', '安静', '陪伴'],
            }

            for trigger, related in free_association_triggers.items():
                if trigger in topic:
                    for rel in related[:2]:
                        if rel != trigger and rel not in topic:
                            associations.append(f"（说到{trigger}，突然想起{rel}...）")
                    break

            return associations[:3]
        except Exception as e:
            _log.debug(f"[思考] 自由联想失败: {e}")
            return associations

    def _analyze_context(self, conversation_history: list) -> str:
        if not conversation_history:
            return "无前序对话上下文"
        
        recent = list(conversation_history)[-5:]
        patterns = []
        
        user_topics = []
        bot_actions = []
        
        for msg in recent:
            if msg.get('role') == 'user':
                text = msg.get('text', '')
                if text:
                    user_topics.extend(self._extract_keywords(text))
                if msg.get('sticker'):
                    patterns.append("用户发表情包")
            elif msg.get('role') == 'bot':
                text = msg.get('text', '')
                if text:
                    if any(k in text for k in ['哈哈', '笑', '有趣']):
                        bot_actions.append("幽默回应")
                    if any(k in text for k in ['抱抱', '安慰', '陪你']):
                        bot_actions.append("情感支持")
                    if any(k in text for k in ['好的', '可以', '帮你']):
                        bot_actions.append("提供帮助")
        
        parts = []
        if user_topics:
            parts.append(f"近期话题: {', '.join(list(set(user_topics))[:5])}")
        if bot_actions:
            parts.append(f"我的回应模式: {', '.join(list(set(bot_actions))[:3])}")
        
        if len(recent) >= 3:
            parts.append("对话已进行多轮，注意连贯性")
        
        return " | ".join(parts) if parts else "上下文分析: 正常对话"

    def _reflect_on_previous_interactions(self, analysis: dict) -> str:
        if len(self._think_log) < 2:
            return "首次对话，无需反思"
        
        recent_thinks = list(self._think_log)[-3:]
        reflections = []
        
        strategies_used = [t.get('strategy', '') for t in recent_thinks]
        if len(set(strategies_used)) == 1:
            reflections.append("注意: 最近多次使用相同策略，尝试换种方式回应")
        
        inputs = [t.get('input', '') for t in recent_thinks]
        if len(set(inputs)) == 1 and len(recent_thinks) >= 2:
            reflections.append("注意: 用户重复输入，可能之前的回应未被理解")
        
        emotional_subtext = analysis.get('emotional_subtext', '')
        if '难过' in emotional_subtext or '低落' in emotional_subtext:
            for t in recent_thinks:
                if '共情' not in t.get('strategy', ''):
                    reflections.append("用户情绪低落，本次应加强共情")
        
        memories = analysis.get('recalled_memories', [])
        if memories:
            reflections.append(f"已激活记忆: {', '.join([m['keyword'] for m in memories])}")
        
        if analysis.get('relationship_context', ''):
            if '亲密度' in analysis['relationship_context']:
                try:
                    match = re.search(r'亲密度(\d+)', analysis['relationship_context'])
                    if match:
                        intimacy = int(match.group(1))
                        if intimacy > 70 and '亲密' not in analysis.get('response_strategy', ''):
                            reflections.append("亲密度高，可以更亲密地回应")
                        elif intimacy < 30 and '亲密' in analysis.get('response_strategy', ''):
                            reflections.append("亲密度较低，保持适当距离")
                except Exception as e:
                        # 亲密度解析为可选增强，失败时跳过该反思
                        _log.debug(f"[Thinker] 亲密度解析失败，跳过: {e}")
        
        return "反思: " + "; ".join(reflections) if reflections else "反思: 当前策略合适"

    def _generate_suggested_response(self, analysis: dict) -> str:
        strategy = analysis.get('response_strategy', '')
        emotional = analysis.get('emotional_subtext', '')
        
        templates = {
            '共情': [
                "我理解你的感受...",
                "听起来你现在...",
                "如果是我，我也会...",
            ],
            '安慰': [
                "没关系的，一切都会好起来的...",
                "我在这里陪你...",
                "别担心，有我在...",
            ],
            '分享喜悦': [
                "哈哈，太好了！...",
                "恭喜你！...",
                "真替你开心！...",
            ],
            '提供帮助': [
                "我可以帮你...",
                "让我来帮你...",
                "需要我帮你做什么吗？...",
            ],
            '幽默': [
                "哈哈，这个有意思！...",
                "笑死我了！...",
                "你太有才了！...",
            ],
            '自然': [
                "嗯，我觉得...",
                "说到这个...",
                "我也这么认为...",
            ],
        }
        
        for key, options in templates.items():
            if key in strategy or key in emotional:
                return random.choice(options)
        
        return "我觉得..."

    def perform_deep_reflection(self, topic: str = None):
        if topic:
            self._reflection_log.append({
                'topic': topic,
                'reflection': f"深入思考: {topic}",
                'ts': time.time(),
            })
        
        if len(self._context_history) >= 3:
            recent_inputs = [c['input'] for c in list(self._context_history)[-3:]]
            combined = " ".join(recent_inputs)
            
            patterns = {
                'repetition': "用户重复提问，可能之前的回答不够清晰",
                'emotional_shift': "用户情绪发生变化，需要调整回应策略",
                'topic_change': "话题转移，需要重新建立连接",
                'engagement': "用户持续参与，关系在加深",
            }
            
            if len(set(recent_inputs)) == 1:
                _log.info(f"[反思] {patterns['repetition']}")
            elif len(self._extract_keywords(combined)) > 5:
                _log.info(f"[反思] {patterns['topic_change']}")

    def _analyze_semantics(self, text: str, intent: str) -> str:
        if intent == 'greeting':
            return "用户在打招呼，可能是想开始对话或确认我是否在线"
        if intent == 'goodnight':
            return "用户要睡了，这是结束对话的信号"
        if intent == 'complaint':
            return "用户在表达不满或负面情绪，需要被倾听和理解"
        if intent == 'question':
            return "用户在寻求知识或答案，需要准确的信息"
        if intent == 'love':
            return "用户在表达喜爱或亲密，这是关系加深的信号"
        if intent == 'comfort':
            return "用户需要安慰和陪伴，感到脆弱"
        if intent == 'casual':
            return "用户在闲聊，可能是想找陪伴或消遣"
        if intent == 'boredom':
            return "用户觉得无聊，需要有趣的内容或互动"
        if intent == 'encourage':
            return "用户缺乏信心，需要鼓励和支持"
        if intent == 'joke':
            return "用户想轻松一下，需要幽默的回应"
        
        topics = self._extract_topics(text, 3)
        if topics:
            return f"用户在讨论{', '.join(topics)}相关的话题"
        return "用户在表达想法或感受"

    def _extract_topics(self, text, n=5):
        if not text:
            return []
        words = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]{3,}', text.lower())
        stop = {
            '什么', '怎么', '为什么', '这个', '那个', '一个', '可以', '应该', '但是', '因为',
            '所以', '就是', '还是', '已经', '然后', '吗', '呢', '啊', '哦', '的', '了', '是',
            '我', '你', '他', '她', '没有', '不是', '知道', '觉得', '可能', '需要', '那么',
            '这样', '那样', '哪里', '哪些', '自己', '他们', '我们', '你们', '它们', '如果',
            '虽然', '不过', '而且', '或者', '以及', '一些', '一直', '一样', '起来', '出来',
            '出去', '过来', '之后', '之前', '还有', '能不能', '会不会', '是不是', '其实',
            '时候', '地方', '东西', '问题', '办法', '今天', '明天', '昨天', '现在', '刚才',
        }
        return list(dict.fromkeys([w for w in words if w not in stop and len(w) >= 2]))[:n]

    def _analyze_emotion(self, text: str) -> str:
        EMO_KW = {
            'joy': ['开心', '快乐', '高兴', '哈哈', '嘿嘿', '好耶', '棒', '太棒了',
                    '绝了', '牛', '厉害', '666', '牛逼', '爽', '太好了'],
            'sadness': ['难过', '伤心', '哭', '呜呜', 'emo', '心累', '失望', '痛苦',
                        '悲伤', '忧郁', '沮丧', '难受', '不开心', '郁闷', '委屈', '心碎',
                        '崩溃', '想哭'],
            'anger': ['生气', '烦', '讨厌', '气死', '可恶', '暴躁', '怒', '愤怒',
                      '火大', '不爽', '下头'],
            'fear': ['担心', '害怕', '紧张', '焦虑', '不安', '恐惧', '慌', '忧虑', '恐慌'],
            'surprise': ['震惊', '惊讶', '天哪', '卧槽', '不会吧', '真的假的', '哇', '天呐'],
            'love': ['喜欢', '爱', '爱你', '想你', '贴贴', '抱抱', '么么', '亲亲',
                     '宝贝', '心动'],
            'curiosity': ['好奇', '想知道', '为什么', '怎么回事', '怎么弄', '如何'],
            'fatigue': ['累', '好累', '疲惫', '困倦', '累死', '困', '好困', '困死了', '想睡觉'],
            'loneliness': ['孤单', '寂寞', '没人陪我', '一个人', '孤独', '好孤独'],
            'gratitude': ['谢谢', '感激', '感谢', '感恩', '多谢'],
            'boredom': ['无聊', '没意思', '乏味', '闷', '好无聊', '无聊死了', '没劲', '发呆'],
            'shyness': ['害羞', '脸红', '扭捏', '羞答答'],
            'playfulness': ['嘿嘿', '嘻嘻', '略略略', '逗你玩', '开玩笑', '搞怪', '调皮', '撒娇'],
        }
        
        detected = []
        negs = ['不', '没', '别', '不是', '没有', '无']
        has_neg = any(n in text for n in negs)
        for dim, kws in EMO_KW.items():
            for kw in kws:
                if kw in text:
                    if has_neg:
                        pos = text.find(kw)
                        pre = text[max(0, pos - 5):pos]
                        if any(n in pre for n in negs):
                            continue
                    detected.append(dim)
                    break
        if not detected:
            if '！' in text or '!' in text:
                detected.append('excitement')
            elif '？' in text or '?' in text:
                detected.append('curiosity')
            else:
                detected.append('neutral')
        intensity = 'normal'
        for w in ['太', '非常', '特别', '超级', '真的', '极其', '好']:
            if w in text:
                intensity = 'strong'
                break
        for w in ['有点', '稍微', '比较', '略']:
            if w in text:
                intensity = 'mild'
                break
        emotion_map = {
            'joy': '开心/愉快', 'sadness': '难过/低落', 'anger': '生气/烦躁',
            'fear': '担心/焦虑', 'surprise': '惊讶/意外', 'love': '喜爱/亲近',
            'curiosity': '好奇/求知', 'fatigue': '疲惫/困倦',
            'loneliness': '孤独/寂寞', 'gratitude': '感恩/感谢',
            'boredom': '无聊/乏味', 'shyness': '害羞/不好意思',
            'playfulness': '调皮/活泼', 'excitement': '兴奋/激动',
            'neutral': '平静/中性',
        }
        parts = []
        for e in detected[:3]:
            label = emotion_map.get(e, e)
            parts.append(label)
        if intensity == 'strong':
            return f"情感强烈: {'、'.join(parts)}"
        elif intensity == 'mild':
            return f"情感轻微: {'、'.join(parts)}"
        return f"情感状态: {'、'.join(parts)}"

    def _analyze_deep_intent(self, text: str, intent: str) -> str:
        deep_needs = {
            'greeting': "渴望连接和关注",
            'complaint': "需要被理解和认同，不一定需要解决方案",
            'love': "寻求安全感和亲密感",
            'comfort': "需要被关心，感到脆弱",
            'question': "追求确定性和理解",
            'boredom': "缺乏刺激和意义感",
            'encourage': "自我怀疑，需要外部的肯定",
            'farewell': "可能不舍，也可能只是礼貌",
            'goodnight': "结束一天的仪式感，或真心不舍",
            'thanks': "感恩的表达，也可能是结束对话的铺垫",
            'casual': "寻求陪伴，不一定有具体目的",
            'joke': "想放松，或想拉近关系",
            'praise': "表达认可，想让我开心",
        }
        base = deep_needs.get(intent, "")
        extras = []
        if self._is_question(text) and any(w in text for w in ['帮我', '能不能', '可以']):
            extras.append("同时希望得到实际帮助")
        if any(w in text for w in ['但', '可是', '不过', '然而']):
            extras.append("内心有矛盾或犹豫")
        if any(w in text for w in ['其实', '说实话', '老实说', '坦白说']):
            extras.append("在袒露真实想法")
        if any(w in text for w in ['你说', '你觉得', '你怎么看']):
            extras.append("在寻求我的观点和看法")
        if extras:
            return f"{base}。{'. '.join(extras)}" if base else '. '.join(extras)
        return base

    def _is_question(self, text):
        if not text:
            return False
        return (any(m in text for m in '？?吗呢么嘛吧') or
                any(w in text for w in [
                    '什么', '怎么', '如何', '为什么', '哪', '谁', '多少', '几', '是否',
                    '能不能', '可不可以', '会不会', '是不是',
                    'what', 'how', 'why', 'who', 'where', 'when', 'which'
                ]))

    def _analyze_knowledge_need(self, text: str, intent: str) -> str:
        if intent in ('greeting', 'farewell', 'goodnight', 'thanks', 'love', 'casual'):
            return "不需要外部知识，社交性对话"
        search_triggers = [
            '搜索', '查一下', '查询', '资料', '百科', '解释', '原理', '方法',
            '是什么', '怎么做', '怎么回事', '帮我查', '帮我找', '深入了解', '科普',
            '最新', '新闻', '今天', '最近', '当前',
        ]
        if any(k in text for k in search_triggers):
            return "需要搜索获取最新/准确信息"
        if self._is_question(text) and len(text) >= 8:
            return "可能是知识性问题，搜索能提升回答质量"
        return "可能不需要搜索，但搜索可以增加信息量"

    def _analyze_resonance(self, text: str) -> str:
        my_emotion = self.emotion.dominant() if hasattr(self.emotion, 'dominant') else 'neutral'
        my_desc = self.emotion.description() if hasattr(self.emotion, 'description') else '平静'
        user_emotions = []
        EMO_KW = {
            'joy': ['开心', '快乐', '高兴', '哈哈'],
            'sadness': ['难过', '伤心', '哭', 'emo'],
            'anger': ['生气', '烦', '讨厌'],
            'fear': ['担心', '害怕', '紧张'],
            'surprise': ['震惊', '惊讶', '天哪'],
            'love': ['喜欢', '爱', '想你', '贴贴'],
            'curiosity': ['好奇', '想知道'],
            'fatigue': ['累', '疲惫', '困'],
            'loneliness': ['孤单', '寂寞', '孤独'],
            'gratitude': ['谢谢', '感谢'],
            'boredom': ['无聊', '没意思'],
            'shyness': ['害羞', '脸红'],
            'playfulness': ['嘿嘿', '嘻嘻', '调皮'],
        }
        for dim, kws in EMO_KW.items():
            for kw in kws:
                if kw in text:
                    user_emotions.append(dim)
                    break
        if not user_emotions:
            return f"我当前{my_desc}，用户情绪平稳，保持自然状态即可"
        resonance = []
        for ue in user_emotions[:2]:
            response_emotion = self.personality.get_dominant_response_emotion(ue) if hasattr(self.personality, 'get_dominant_response_emotion') else ue
            if ue == 'sadness':
                resonance.append(f"用户难过，我应该先共情(感受{response_emotion})，而非急着解决问题")
            elif ue == 'joy':
                resonance.append(f"用户开心，我应该真诚地分享喜悦(回应{response_emotion})")
            elif ue == 'anger':
                resonance.append(f"用户生气，我应该先理解认同(回应{response_emotion})，避免说教")
            elif ue == 'love':
                resonance.append(f"用户表达爱意，我应该温暖回应(回应{response_emotion})")
            elif ue == 'loneliness':
                resonance.append(f"用户孤独，我应该表达陪伴(回应{response_emotion})")
            elif ue == 'fear':
                resonance.append(f"用户焦虑害怕，我应该安慰和支持(回应{response_emotion})")
            else:
                resonance.append(f"用户情绪是{ue}，我以{response_emotion}回应")
        return f"我当前{my_desc}。" + "。".join(resonance)

    def _analyze_personality(self) -> str:
        mod = self.personality.get_trait_modifier() if hasattr(self.personality, 'get_trait_modifier') else {}
        parts = []
        if mod.get('warmth_level', 0.5) > 0.6:
            parts.append("语气温暖体贴")
        elif mod.get('warmth_level', 0.5) < 0.4:
            parts.append("语气平和理性")
        if mod.get('humor_chance', 0) > 0.15:
            parts.append("可以适当加入幽默")
        if mod.get('empathy_depth', 0.5) > 0.6:
            parts.append("注重情感共情")
        if mod.get('playfulness_level', 0.5) > 0.6:
            parts.append("可以俏皮一些")
        if mod.get('thought_depth', 0.5) > 0.6:
            parts.append("回答要有深度")
        if mod.get('expressiveness', 0.5) > 0.6:
            parts.append("表达丰富有层次")
        if mod.get('sensitivity', 0.3) > 0.5:
            parts.append("对情感变化敏感")
        if mod.get('directness', 0.5) > 0.5:
            parts.append("可以更直接坦率")
        return "性格倾向: " + "、".join(parts) if parts else "性格平衡"

    def _analyze_relationship(self) -> str:
        stage = self.relationship.get_stage_name()
        style = getattr(self.relationship, 'style', 'polite')
        intimacy = self.emotion.intimacy if hasattr(self.emotion, 'intimacy') else 0
        desc = f"当前关系: {stage}(亲密度{int(intimacy)}/100)，对话风格: {style}"
        style_hints = {
            'polite': "保持礼貌客气，不要太亲密",
            'friendly': "可以友善自然，但不要过于随意",
            'casual': "可以轻松随意，像朋友一样",
            'warm': "可以温暖贴心，像好朋友一样",
            'intimate': "可以亲密撒娇，像挚友一样",
            'family': "可以像家人一样无条件信任和关心",
        }
        hint = style_hints.get(style, "")
        if hint:
            desc += f"。{hint}"
        return desc

    def _determine_strategy(self, analysis: dict) -> str:
        strategies = []
        emotional = analysis.get('emotional_subtext', '')
        if '难过' in emotional or '低落' in emotional:
            strategies.append("先共情，再温和地给出建议(如果合适)")
        elif '生气' in emotional or '烦躁' in emotional:
            strategies.append("理解认同，避免说教和否定")
        elif '开心' in emotional or '愉快' in emotional:
            strategies.append("真诚分享喜悦，可以稍微调侃")
        elif '孤独' in emotional or '寂寞' in emotional:
            strategies.append("表达陪伴，给予安全感")
        elif '焦虑' in emotional or '担心' in emotional:
            strategies.append("安慰+提供实质性帮助")
        elif '好奇' in emotional:
            strategies.append("详细解答，激发更多好奇")
        else:
            strategies.append("自然回应，保持真实")
        knowledge = analysis.get('knowledge_need', '')
        if '搜索' in knowledge:
            strategies.append("搜索后综合回答，确保准确性")
        rel = analysis.get('relationship_context', '')
        if '亲人' in rel or '挚友' in rel:
            strategies.append("可以更真实、更放松地表达")
        elif '陌生人' in rel:
            strategies.append("保持友善但适度距离")
        return " | ".join(strategies)

    def _compose_thinking(self, analysis: dict) -> str:
        parts = [
            "【喵艺的深度思考】",
            f"1. 表层理解: {analysis['surface_meaning']}",
            f"2. 情感分析: {analysis['emotional_subtext']}",
            f"3. 深层意图: {analysis['real_intent']}",
            f"4. 上下文: {analysis['context_analysis']}",
        ]
        
        associations = analysis.get('free_associations', [])
        if associations:
            parts.append(f"5. 自由联想: {' '.join(associations)}")
        
        memories = analysis.get('recalled_memories', [])
        if memories:
            mem_str = "; ".join([f"{m['keyword']}: {m['memory'][:30]}" for m in memories[:3]])
            parts.append(f"6. 激活记忆: {mem_str}")
        
        if '需要搜索' in analysis.get('knowledge_need', ''):
            parts.append(f"6. 知识需求: {analysis['knowledge_need']}")
        
        parts.append(f"7. 情感共振: {analysis['emotional_resonance']}")
        parts.append(f"8. 关系背景: {analysis['relationship_context']}")
        parts.append(f"9. 回应策略: {analysis['response_strategy']}")
        parts.append(f"10. 自我反思: {analysis['reflection']}")
        
        suggested = analysis.get('suggested_response', '')
        if suggested:
            parts.append(f"11. 建议开头: {suggested}")
        
        return "\n".join(parts)

    def get_recent_thinks(self, n=5) -> list:
        return list(self._think_log)[-n:]

    def save(self):
        with self._lock:
            if self._dirty:

                from core.miaoyi_core import save_json
                save_json("miaoyi_deep_think.json", {
                    'think_log': list(self._think_log)[-50:],
                })
                self._dirty = False


class DeepThinker:
    def __init__(self, llm_system, knowledge_base=None, emotional_core=None):
        self._llm = llm_system
        self.kb = knowledge_base
        self.emotional_core = emotional_core
    
    def think(self, query: str, context: str = "") -> dict[str, Any]:
        return {
            'answer': query,
            'confidence': 0.8,
            'inner_monologue': f"分析问题: {query}",
            'thinking_process': f"思考: {query}",
            'steps': [],
            'observations': [],
            'thinking_time': 0.1,
            'needs_external_ai': False,
            'step_count': 1
        }
    
    def stop(self):
        pass