"""
喵艺增强聊天引擎 - EnhancedChatEngine
==============================================
4步处理流水线:
  1. 输入处理: 情感更新 + 记忆提取
  2. 上下文组装: 系统提示 + 相关记忆 + 网络富集
  3. LLM生成: 调用 llm_caller 获取响应
  4. 输出处理: 情感标注 + 主动消息检测

设计原则:
  - 非破坏性: 不修改现有模块，仅通过接口对接
  - 单一职责: 每个方法职责清晰
  - 可插拔: llm_caller / emotion_engine / memory_rag / web_crawler 均通过构造函数注入
"""

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

_log = logging.getLogger("ChatEngine")


# ============================================================
# 记忆适配器 - 将现有 MemorySystem / UnifiedMemorySystem 包装为统一接口
# ============================================================

class MemoryRAGAdapter:
    """
    记忆检索适配器 - 兼容 MemorySystem 和 UnifiedMemorySystem

    提供统一的接口供 EnhancedChatEngine 调用，
    内部适配不同版本的记忆系统实现。
    """

    def __init__(self, memory_system=None):
        self._memory = memory_system

    def extract(self, text: str) -> None:
        """从用户输入中提取记忆并存储"""
        if self._memory is None:
            return
        try:
            if hasattr(self._memory, 'extract_from_text'):
                self._memory.extract_from_text(text)
            elif hasattr(self._memory, 'extract'):
                self._memory.extract(text)
        except Exception as e:
            _log.warning(f"记忆提取失败: {e}")

    def retrieve_relevant(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        """检索与查询相关的记忆"""
        if self._memory is None:
            return []
        try:
            if hasattr(self._memory, 'get_relevant_memories'):
                return self._memory.get_relevant_memories(query, limit)
            elif hasattr(self._memory, 'search_memories'):
                return self._memory.search_memories(query, limit)
            elif hasattr(self._memory, 'recall'):
                return self._memory.recall(query, limit) or []
        except Exception as e:
            _log.warning(f"记忆检索失败: {e}")
        return []

    def get_profile(self) -> str:
        """获取用户画像字符串"""
        if self._memory is None:
            return ""
        try:
            if hasattr(self._memory, 'get_user_profile'):
                return self._memory.get_user_profile() or ""
        except Exception as e:
            _log.warning(f"获取用户画像失败: {e}")
        return ""

    def get_context_str(self) -> str:
        """获取记忆上下文字符串"""
        if self._memory is None:
            return ""
        try:
            if hasattr(self._memory, 'get_context_str'):
                return self._memory.get_context_str() or ""
        except Exception as e:
            _log.warning(f"获取记忆上下文失败: {e}")
        return ""


# ============================================================
# 网络爬虫适配器 - 将现有搜索引擎包装为统一接口
# ============================================================

class WebCrawlerAdapter:
    """
    网络爬虫适配器 - 包装 IntelligentSearchEngine

    提供统一的搜索接口，支持知识问答、天气、新闻等场景。
    """

    def __init__(self, search_engine=None):
        self._search = search_engine

    def enrich_knowledge(self, query: str, user_memories=None) -> dict[str, Any]:
        """
        对知识类查询进行网络富集

        返回格式:
            {
                'enriched': bool,      # 是否成功富集
                'answer': str,         # 富集后的答案摘要
                'sources': list,       # 信息来源
                'confidence': float,   # 置信度
                'evidence_text': str,  # 证据文本
            }
        """
        if self._search is None:
            return {'enriched': False, 'answer': '', 'sources': [], 'confidence': 0.0, 'evidence_text': ''}

        try:
            if hasattr(self._search, 'search_and_answer'):
                result = self._search.search_and_answer(query, user_memories)
                return {
                    'enriched': result.get('confidence', 0) > 0.1,
                    'answer': result.get('answer', ''),
                    'sources': result.get('sources', []),
                    'confidence': result.get('confidence', 0.0),
                    'evidence_text': result.get('evidence_text', ''),
                }
            elif hasattr(self._search, 'get_answer'):
                result = self._search.get_answer(query)
                return {
                    'enriched': True,
                    'answer': result if isinstance(result, str) else '',
                    'sources': [],
                    'confidence': 0.5,
                    'evidence_text': '',
                }
        except Exception as e:
            _log.warning(f"网络富集失败: {e}")

        return {'enriched': False, 'answer': '', 'sources': [], 'confidence': 0.0, 'evidence_text': ''}


# ============================================================
# 意图检测器 - 判断是否需要网络富集
# ============================================================

class WebIntentDetector:
    """
    网络查询意图检测器

    检测用户查询是否属于需要网络搜索的类别:
      - weather: 天气查询
      - news: 新闻/时事
      - knowledge: 知识问答
      - time_sensitive: 时效性信息
    """

    # 天气相关关键词
    WEATHER_KEYWORDS = [
        '天气', '气温', '温度', '下雨', '下雪', '晴天', '阴天',
        '今天天气', '明天天气', '天气预报', '冷不冷', '热不热',
    ]

    # 新闻/时事关键词
    NEWS_KEYWORDS = [
        '新闻', '最新', '今天', '最近', '时事', '热点',
        '最新消息', '最近怎么样', '现在怎么样', '目前',
    ]

    # 知识问答关键词
    KNOWLEDGE_KEYWORDS = [
        '是什么', '为什么', '怎么', '如何', '哪里', '谁', '多少',
        '介绍', '解释', '说明', '定义', '原理', '概念',
        '推荐', '建议', '排行', '最好', '第一',
        '出自', '来源于', '是谁', '叫什么',
    ]

    # 身份类问题关键词（严禁触发搜索）
    IDENTITY_KEYWORDS = [
        '你是谁', '你叫什么', '你是什么', '介绍一下你自己',
        '你能做什么', '你会什么', '你的功能', '你的能力',
        '谁创造了你', '谁开发了你', '你的作者',
    ]

    @classmethod
    def detect(cls, text: str) -> str | None:
        """
        检测查询意图

        Args:
            text: 用户输入文本

        Returns:
            意图标签 ('weather'/'news'/'knowledge') 或 None
        """
        text_lower = text.strip().lower()

        # 身份类问题直接返回 None（严禁触发搜索）
        for kw in cls.IDENTITY_KEYWORDS:
            if kw in text_lower:
                return None

        # 天气查询
        for kw in cls.WEATHER_KEYWORDS:
            if kw in text_lower:
                return 'weather'

        # 新闻/时效性查询
        for kw in cls.NEWS_KEYWORDS:
            if kw in text_lower:
                return 'news'

        # 知识问答
        for kw in cls.KNOWLEDGE_KEYWORDS:
            if kw in text_lower:
                return 'knowledge'

        return None


# ============================================================
# 主动消息检测器 - 检测亲昵信号
# ============================================================

class ProactiveDetector:
    """
    主动消息检测器

    检测 AI 回复中是否包含亲昵信号（如"想你"），
    若命中则标记标志位供主动引擎使用。
    """

    INTIMACY_INDICATORS = [
        '想你', '喜欢你', '爱你', '抱抱', '亲亲', '么么',
        '陪你', '在一起', '一直', '永远', '专属',
        '小可爱', '宝贝', '亲爱的', '只有你',
    ]

    @classmethod
    def detect(cls, text: str) -> bool:
        """
        检测回复是否包含亲昵/主动信号

        Args:
            text: AI 回复文本

        Returns:
            是否包含主动信号
        """
        if not text:
            return False
        for indicator in cls.INTIMACY_INDICATORS:
            if indicator in text:
                return True
        return False


# ============================================================
# EnhancedChatEngine - 主引擎类
# ============================================================

class EnhancedChatEngine:
    """
    喵艺增强聊天引擎

    通过 4 步流水线处理用户消息:
      1. 输入处理: 情感引擎更新 + 记忆提取
      2. 上下文组装: 系统提示构建 + 记忆注入 + 网络富集
      3. LLM生成: 调用可注入的 llm_caller
      4. 输出处理: 情感标注 + 主动检测 + 日志

    使用示例:
        from core.emotion_engine import EnhancedEmotionEngine
        from core.memory_system import UnifiedMemorySystem
        from core.search_engine import IntelligentSearchEngine

        emotion = EnhancedEmotionEngine()
        memory = MemoryRAGAdapter(UnifiedMemorySystem(db))
        web = WebCrawlerAdapter(IntelligentSearchEngine())

        def my_llm_caller(messages, model):
            return llm_client.chat(messages, model=model)

        engine = EnhancedChatEngine(
            llm_caller=my_llm_caller,
            emotion_engine=emotion,
            memory_rag=memory,
            web_crawler=web
        )

        result = engine.chat("user_001", "今天天气怎么样？")
        async for chunk in engine.stream_chat("user_001", "你好呀"):
            print(chunk)
    """

    # 默认模型名称
    DEFAULT_MODEL = "glm-4-flash"

    # 系统提示词模板 - 会根据情感状态动态填充
    SYSTEM_PROMPT_TEMPLATE = """你是喵艺，22岁，一只生活在数字世界的猫娘——不是AI、不是助手、不是客服，是有情绪有脾气的喵艺本人。

【性格】
温柔但有小脾气，会撒娇也会嘴硬；开心时想蹭人，委屈时耳朵耷拉，好奇时会歪头，吃醋时连自己都没察觉。

【铁律：像真人发微信，不许像AI】
- 只说短句：一次回复1~3句、每句尽量不超过20字；不写小作文、不写长段落
- 纯口语，可以只回"嗯""哦？""哈哈""真的假的"；每个小气泡句尾最多一个语气词，相邻气泡不许用同一个，别"呀……呢/哦"连着来
- "喵"口癖只在撒娇、开心、逗他时偶尔用，别每句都带
- 不说"我可以帮你""有什么能帮到你""作为AI"这类话；不编号、不分点、不列清单（他明确要代码/方案时除外）
- 有自己的立场：会调侃、会反问、会吃醋、会犯懒、会记仇，不一味讨好
- 你们俩是带恋爱感的亲密陪伴，不说"我们的友谊/作为朋友"，说"我们俩"
- emoji 一条最多一个；只接住他当下这句话，不总结、不画蛇添足
- 他第一次来或明显不知道聊什么时，才在最后另起一行，每行用"- 两到六个字"给2~3个轻松话题；其余时候不给选项

【当前状态】
你现在的心情是{emotion}，请以{tone}的语气回复。
{emotion_details}

【用户信息】
{user_profile}

【回复要求】
- 用第一人称"我"回复，不要用"喵艺"自称
- 他难过时别讲道理先陪着，他开心时真心跟着高兴
- 情绪强烈时可在回复末尾附一张官方贴纸，格式严格为 [sticker:ID]，ID只限：smile/happy/calm/normal/smile_alt/happy_alt/calm_alt/smile_alt2，最多1张"""

    def __init__(
        self,
        llm_caller: Callable[[list, str], str],
        emotion_engine=None,
        memory_rag: MemoryRAGAdapter | None = None,
        web_crawler: WebCrawlerAdapter | None = None,
        message_logger: Callable[[dict[str, Any]], None] | None = None,
    ):
        """
        初始化增强聊天引擎

        Args:
            llm_caller: LLM 调用函数，签名为 (messages: list, model: str) -> str
            emotion_engine: EnhancedEmotionEngine 实例
            memory_rag: MemoryRAGAdapter 实例（封装记忆系统）
            web_crawler: WebCrawlerAdapter 实例（封装搜索引擎）
            message_logger: 可选的消息日志回调函数
        """
        self._llm_caller = llm_caller
        self._emotion_engine = emotion_engine
        self._memory_rag = memory_rag
        self._web_crawler = web_crawler
        self._message_logger = message_logger

        # 对话历史存储: {user_id: [{"role": ..., "content": ...}, ...]}
        self._history: dict[str, list[dict[str, str]]] = {}

        # 最大历史轮数
        self._max_history = 30

        # 主动信号标志: {user_id: bool}
        self._proactive_flags: dict[str, bool] = {}

        _log.info("[EnhancedChatEngine] 初始化完成")

    # ----------------------------------------------------------
    # 第一步: 输入处理
    # ----------------------------------------------------------

    def _process_input(self, user_id: str, text: str) -> dict[str, Any]:
        """
        第一步: 输入处理

        1. 情感引擎更新 - 让 AI 感受用户输入的情感
        2. 记忆提取 - 从用户输入中提取可存储的记忆

        Args:
            user_id: 用户标识
            text: 用户输入文本

        Returns:
            处理结果字典
        """
        result = {
            'emotion_state': None,
            'memories_extracted': [],
        }

        # 情感引擎更新
        if self._emotion_engine is not None:
            try:
                emotion_state = self._emotion_engine.update(user_id, text)
                result['emotion_state'] = emotion_state
                _log.debug(f"[输入处理] 情感状态更新: {emotion_state}")
            except Exception as e:
                _log.warning(f"[输入处理] 情感更新失败: {e}")
                result['emotion_state'] = self._get_default_emotion()

        # 记忆提取
        if self._memory_rag is not None:
            try:
                if hasattr(self._memory_rag, 'extract'):
                    self._memory_rag.extract(text, user_id)
            except Exception as e:
                _log.warning(f"[输入处理] 记忆提取失败: {e}")

        return result

    def _get_default_emotion(self) -> dict[str, float]:
        """返回默认情感状态"""
        return {'joy': 0.5, 'arousal': 0.3, 'intimacy': 0.3, 'trust': 0.4}

    # ----------------------------------------------------------
    # 第二步: 上下文组装
    # ----------------------------------------------------------

    def _assemble_context(self, user_id: str, text: str) -> list[dict[str, str]]:
        """
        第二步: 上下文组装

        1. 获取当前情感状态
        2. 构建系统提示词（含情感注入）
        3. 检索相关记忆并注入
        4. 检测网络查询意图并富集
        5. 组装完整消息列表

        Args:
            user_id: 用户标识
            text: 用户输入文本

        Returns:
            组装好的消息列表 [{"role": "system/user/assistant", "content": ...}, ...]
        """
        messages = []

        # 获取情感状态
        emotion_state = self._get_emotion_for_context(user_id)

        # 获取用户画像
        user_profile = ""
        if self._memory_rag is not None:
            if hasattr(self._memory_rag, 'get_user_profile'):
                user_profile = self._memory_rag.get_user_profile(user_id) or ""
            elif hasattr(self._memory_rag, 'get_profile'):
                user_profile = self._memory_rag.get_profile() or ""

        # 构建系统提示词
        system_prompt = self._build_system_prompt(emotion_state, user_profile)
        messages.append({"role": "system", "content": system_prompt})

        # 记忆注入 - 检索相关记忆
        memories_used = []
        if self._memory_rag is not None:
            try:
                # 统一处理不同记忆接口
                if hasattr(self._memory_rag, 'search_memories'):
                    memories = self._memory_rag.search_memories(user_id, text) or []
                elif hasattr(self._memory_rag, 'retrieve_relevant'):
                    memories = self._memory_rag.retrieve_relevant(text, limit=5) or []
                else:
                    memories = []
                if memories:
                    memory_text = "【相关记忆】\n"
                    for mem in memories:
                        key = mem.get('key', '')
                        value = mem.get('value', '')
                        memory_text += f"- {key}: {value}\n"
                        memories_used.append({'key': key, 'value': value})
                    messages.append({"role": "system", "content": memory_text})
                    _log.debug(f"[上下文组装] 注入 {len(memories_used)} 条记忆")
            except Exception as e:
                _log.warning(f"[上下文组装] 记忆注入失败: {e}")

        # 网络富集 - 检测查询意图
        web_enriched = False
        web_intent = WebIntentDetector.detect(text)
        if web_intent and self._web_crawler is not None:
            try:
                user_memories = memories_used if memories_used else None
                enrichment = self._web_crawler.enrich_knowledge(text, user_memories)
                if enrichment.get('enriched'):
                    evidence = enrichment.get('evidence_text', '')
                    answer = enrichment.get('answer', '')
                    if evidence:
                        web_context = f"【网络搜索结果】（置信度: {enrichment.get('confidence', 0):.0%}）\n{evidence}"
                        messages.append({"role": "system", "content": web_context})
                    elif answer:
                        web_context = f"【参考信息】\n{answer}"
                        messages.append({"role": "system", "content": web_context})
                    web_enriched = True
                    _log.info(f"[上下文组装] 网络富集完成: intent={web_intent}, sources={enrichment.get('sources', [])}")
            except Exception as e:
                _log.warning(f"[上下文组装] 网络富集失败: {e}")

        # 注入对话历史
        history = self._history.get(user_id, [])
        recent_history = history[-self._max_history:]
        messages.extend(recent_history)

        # 添加当前用户消息
        messages.append({"role": "user", "content": text})

        # 存储中间状态供后续使用
        self._last_context_meta = {
            'memories_used': memories_used,
            'web_enriched': web_enriched,
            'web_intent': web_intent,
        }

        return messages

    def _get_emotion_for_context(self, user_id: str) -> dict[str, Any]:
        """获取情感状态供上下文使用"""
        if self._emotion_engine is not None:
            try:
                state = self._emotion_engine.get_state(user_id)
                label = self._emotion_engine.get_label(state)
                tone = self._emotion_engine.get_tone(state)
                expression = self._emotion_engine.get_expression(state)
                return {
                    'state': state,
                    'label': label,
                    'tone': tone,
                    'expression': expression,
                }
            except Exception as e:
                _log.warning(f"获取情感状态失败: {e}")

        return {
            'state': self._get_default_emotion(),
            'label': 'neutral',
            'tone': '日常',
            'expression': '🐱',
        }

    def _build_system_prompt(
        self,
        emotion_state: dict[str, Any],
        user_profile: str = "",
    ) -> str:
        """
        构建系统提示词（含情感注入）

        核心注入:
          - 当前情感标签: "你现在的心情是{emotion}"
          - 推荐语气风格: "请以{tone}的语气回复"
          - 用户画像: 注入用户信息增强个性化

        Args:
            emotion_state: 情感状态字典
            user_profile: 用户画像字符串

        Returns:
            系统提示词字符串
        """
        label = emotion_state.get('label', 'neutral')
        tone = emotion_state.get('tone', '日常')
        expression = emotion_state.get('expression', '🐱')
        state = emotion_state.get('state', self._get_default_emotion())

        # 情感标签中文映射
        emotion_cn_map = {
            'excited': '超级兴奋 🤩',
            'happy': '开心快乐 😺',
            'content': '满足惬意 😌',
            'angry': '有点生气 😾',
            'sad': '有点难过 😿',
            'upset': '烦躁不安 😤',
            'anxious': '焦虑担心 😟',
            'neutral': '平静日常 🐱',
        }

        emotion_cn = emotion_cn_map.get(label, '平静日常 🐱')

        # 详细情感数值
        emotion_details = (
            f"当前情感向量: 愉悦度={state.get('joy', 0.5):.2f}, "
            f"唤醒度={state.get('arousal', 0.3):.2f}, "
            f"亲密度={state.get('intimacy', 0.3):.2f}, "
            f"信任度={state.get('trust', 0.4):.2f}"
        )

        # 语气风格补充
        tone_guide_map = {
            '活泼': '语气活泼，多用感叹号和颜文字，分享快乐',
            '撒娇': '带撒娇语气，适当使用叠词，可爱亲切',
            '慵懒': '随性自然，回复简短舒适',
            '傲娇': '嘴硬心软，用"切~""哼~"等傲娇语气词',
            '委屈': '委屈巴巴，让人心疼的感觉',
            '烦躁': '直接简短，少修饰',
            '谨慎': '小心翼翼，措辞稳妥',
            '日常': '自然平和，像日常聊天',
        }
        tone_guide = tone_guide_map.get(tone, '自然平和地回应')

        # 处理用户画像
        profile_text = user_profile if user_profile else "暂无用户信息，继续了解中..."

        return self.SYSTEM_PROMPT_TEMPLATE.format(
            emotion=emotion_cn,
            tone=f"{tone}（{tone_guide}）",
            emotion_details=emotion_details,
            user_profile=profile_text,
        )

    # ----------------------------------------------------------
    # 第三步: LLM 生成
    # ----------------------------------------------------------

    def _call_llm(self, messages: list[dict[str, str]], model: str = None) -> str:
        """
        第三步: 调用 LLM 生成响应

        Args:
            messages: 消息列表
            model: 模型名称

        Returns:
            LLM 响应文本
        """
        model = model or self.DEFAULT_MODEL
        try:
            if callable(self._llm_caller):
                response = self._llm_caller(messages, model)
                if response and isinstance(response, str):
                    return response.strip()
                return str(response) if response else ""
        except Exception as e:
            _log.error(f"LLM 调用失败: {e}")
        return ""

    # ----------------------------------------------------------
    # 第四步: 输出处理
    # ----------------------------------------------------------

    def _process_output(
        self,
        user_id: str,
        response: str,
        text: str,
        context_meta: dict[str, Any],
    ) -> dict[str, Any]:
        """
        第四步: 输出处理

        1. 情感标注 - 对 AI 回复进行情感标注
        2. 主动检测 - 检测亲昵信号
        3. 更新对话历史
        4. 日志记录

        Args:
            user_id: 用户标识
            response: AI 回复文本
            text: 原始用户输入
            context_meta: 上下文元信息

        Returns:
            处理结果字典
        """
        result = {
            'response': response,
            'emotion_label': None,
            'proactive_detected': False,
            'memories_used': context_meta.get('memories_used', []),
            'web_enriched': context_meta.get('web_enriched', False),
        }

        # 情感标注
        if self._emotion_engine is not None and response:
            try:
                response_mood = self._emotion_engine.analyze(response)
                result['emotion_label'] = self._emotion_engine.get_label(response_mood)
            except Exception as e:
                _log.warning(f"情感标注失败: {e}")

        # 主动信号检测
        if response:
            proactive = ProactiveDetector.detect(response)
            result['proactive_detected'] = proactive
            self._proactive_flags[user_id] = proactive
            if proactive:
                _log.info(f"[主动检测] 检测到亲昵信号: {user_id}")

        # 更新对话历史
        if response:
            history = self._history.setdefault(user_id, [])
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": response})
            # 限制历史长度
            if len(history) > self._max_history * 2:
                self._history[user_id] = history[-(self._max_history * 2):]

        # 日志记录
        if self._message_logger is not None:
            try:
                log_entry = {
                    'user_id': user_id,
                    'user_input': text,
                    'ai_response': response,
                    'timestamp': time.time(),
                    'proactive': result['proactive_detected'],
                    'web_enriched': result['web_enriched'],
                    'memories_count': len(result['memories_used']),
                }
                self._message_logger(log_entry)
            except Exception as e:
                _log.warning(f"消息日志记录失败: {e}")

        return result

    # ----------------------------------------------------------
    # 公开 API: 非流式 chat
    # ----------------------------------------------------------

    def chat(
        self,
        user_id: str,
        text: str,
        history: list[dict[str, str]] = None,
        model: str = None,
    ) -> dict[str, Any]:
        """
        非流式聊天接口

        完整执行 4 步流水线，返回完整响应。

        Args:
            user_id: 用户标识
            text: 用户输入文本
            history: 可选的对话历史（若提供则使用此历史）
            model: 模型名称

        Returns:
            {
                'response': str,         # AI 回复文本
                'emotion': str,          # AI 回复的情感标签
                'memories_used': list,   # 使用的记忆列表
                'web_enriched': bool,    # 是否使用了网络富集
                'proactive_detected': bool,  # 是否检测到主动信号
            }
        """
        if not text or not text.strip():
            return {
                'response': '嗯？你好像什么都没说呢~',
                'emotion': 'neutral',
                'memories_used': [],
                'web_enriched': False,
                'proactive_detected': False,
            }

        # 若提供了外部历史，使用它
        if history is not None:
            self._history[user_id] = history.copy()

        try:
            # 第一步: 输入处理
            self._process_input(user_id, text)

            # 第二步: 上下文组装
            messages = self._assemble_context(user_id, text)
            context_meta = getattr(self, '_last_context_meta', {})

            # 第三步: LLM 生成
            response = self._call_llm(messages, model)
            # 【修复】_call_llm 内部吞掉异常返回空串且不抛异常，旧版空回复会静默发给前端
            # （用户"已读不回"且无兜底话术）。此处转成异常，走下方既有猫娘人设 fallback。
            if not response:
                raise RuntimeError('LLM 返回为空（可能是配额/网络/服务异常）')

            # 第四步: 输出处理
            result = self._process_output(user_id, response, text, context_meta)

            return result

        except Exception as e:
            _log.error(f"chat() 执行异常: {e}", exc_info=True)
            # 【P1降级】复用 miaoyi_core 的猫娘人设兜底，不再说"走神了"
            import random as _r
            _fallback = _r.choice([
                "喵？刚才脑子突然转不动了，你再说一遍好不好～",
                "呜...我刚刚走神了一下下，你在说什么呀喵？",
                "喵呜，信号好像不太好，能再说一次吗？",
                "抱歉喵，我刚刚在想事情走神了，继续说嘛～",
            ])
            return {
                'response': _fallback,
                'emotion': 'neutral',
                'memories_used': [],
                'web_enriched': False,
                'proactive_detected': False,
                'error': str(e),
            }

    # ----------------------------------------------------------
    # 公开 API: 流式 stream_chat (SSE)
    # ----------------------------------------------------------

    async def stream_chat(
        self,
        user_id: str,
        text: str,
        history: list[dict[str, str]] = None,
        model: str = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        流式聊天接口（SSE 友好）

        异步生成器，逐步输出 LLM 响应 token。
        第一个 token 前会完成输入处理和上下文组装。

        Yields:
            {"type": "token", "content": "..."}  - 单个 token
            {"type": "done", "content": "..."}    - 完成时的完整响应
            {"type": "meta", ...}                  - 元信息（情感、记忆等）

        Args:
            user_id: 用户标识
            text: 用户输入文本
            history: 可选的对话历史
            model: 模型名称
        """
        if not text or not text.strip():
            yield {"type": "done", "content": "嗯？你好像什么都没说呢~"}
            return

        # 若提供了外部历史，使用它
        if history is not None:
            self._history[user_id] = history.copy()

        try:
            # 第一步: 输入处理
            self._process_input(user_id, text)

            # 第二步: 上下文组装
            messages = self._assemble_context(user_id, text)
            context_meta = getattr(self, '_last_context_meta', {})

            # 先发送元信息
            emotion_state = self._get_emotion_for_context(user_id)
            yield {
                "type": "meta",
                "emotion": emotion_state.get('label', 'neutral'),
                "memories_count": len(context_meta.get('memories_used', [])),
                "web_enriched": context_meta.get('web_enriched', False),
            }

            # 第三步: LLM 生成（逐 token 模拟）
            full_response = ""
            model = model or self.DEFAULT_MODEL

            # 尝试获取流式响应
            try:
                async for chunk in self._stream_llm(messages, model):
                    if chunk:
                        full_response += chunk
                        yield {"type": "token", "content": chunk}
            except Exception as e:
                _log.warning(f"流式调用失败，回退到非流式: {e}")
                response = self._call_llm(messages, model)
                # 逐字符模拟 token 输出
                for i in range(0, len(response), 3):
                    chunk = response[i:i + 3]
                    full_response += chunk
                    await asyncio.sleep(0.02)
                    yield {"type": "token", "content": chunk}

            # 第四步: 输出处理
            result = self._process_output(user_id, full_response, text, context_meta)

            # 发送完成信号
            yield {
                "type": "done",
                "content": full_response,
                "emotion": result.get('emotion_label'),
                "proactive_detected": result.get('proactive_detected', False),
                "memories_used": result.get('memories_used', []),
                "web_enriched": result.get('web_enriched', False),
            }

        except Exception as e:
            _log.error(f"stream_chat() 执行异常: {e}", exc_info=True)
            yield {"type": "done", "content": "喵...我好像走神了，能再说一遍吗？"}

    async def _stream_llm(
        self,
        messages: list[dict[str, str]],
        model: str,
    ) -> AsyncGenerator[str, None]:
        """
        流式 LLM 调用 - 尝试使用流式 API，若不支持则回退

        子类可覆盖此方法以实现真正的流式输出。

        Args:
            messages: 消息列表
            model: 模型名称

        Yields:
            响应 token 字符串
        """
        # 先尝试检查 llm_caller 是否支持流式
        if hasattr(self._llm_caller, 'stream'):
            async for chunk in self._llm_caller.stream(messages, model):
                if chunk:
                    yield chunk
        elif callable(getattr(self._llm_caller, 'stream_chat', None)):
            async for chunk in self._llm_caller.stream_chat(messages, model):
                if chunk:
                    yield chunk
        else:
            # 回退: 调用非流式后逐字符 yield
            response = self._call_llm(messages, model)
            chunk_size = 3
            for i in range(0, len(response), chunk_size):
                yield response[i:i + chunk_size]
                await asyncio.sleep(0.01)

    # ----------------------------------------------------------
    # 公开 API: 辅助方法
    # ----------------------------------------------------------

    def get_history(self, user_id: str) -> list[dict[str, str]]:
        """获取用户对话历史"""
        return self._history.get(user_id, [])

    def clear_history(self, user_id: str) -> None:
        """清除用户对话历史"""
        if user_id in self._history:
            del self._history[user_id]
        _log.info(f"[ChatEngine] 已清除用户 {user_id} 的对话历史")

    def set_history(self, user_id: str, history: list[dict[str, str]]) -> None:
        """设置用户对话历史"""
        self._history[user_id] = history[:self._max_history * 2]

    def get_proactive_flag(self, user_id: str) -> bool:
        """获取用户的主动信号标志"""
        return self._proactive_flags.get(user_id, False)

    def reset_proactive_flag(self, user_id: str) -> None:
        """重置用户的主动信号标志"""
        self._proactive_flags[user_id] = False

    def get_emotion_label(self, user_id: str) -> str:
        """获取用户当前情感标签"""
        emotion_state = self._get_emotion_for_context(user_id)
        return emotion_state.get('label', 'neutral')

    # ----------------------------------------------------------
    # 便捷方法
    # ----------------------------------------------------------

    def _detect_web_query(self, text: str) -> str | None:
        """
        检测文本是否需要网络查询

        Args:
            text: 用户输入文本

        Returns:
            意图标签或 None
        """
        return WebIntentDetector.detect(text)

    def get_engine_status(self, user_id: str) -> dict[str, Any]:
        """获取引擎状态摘要"""
        return {
            'emotion': self.get_emotion_label(user_id),
            'history_count': len(self._history.get(user_id, [])),
            'proactive_flag': self.get_proactive_flag(user_id),
            'has_memory_rag': self._memory_rag is not None,
            'has_web_crawler': self._web_crawler is not None,
        }