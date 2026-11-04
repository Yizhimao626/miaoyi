"""
喵艺智能搜索策略 v2.0 - 多维度评分触发机制
================================================
核心原则: 情感陪伴优先，知识问答才搜索
改进:
1. 多维度评分 (情绪/知识/时效/实体) 替代单一正则匹配
2. 引入 LLM 辅助判断 (当正则不确定时)
3. 实体感知触发 (识别潜在知识实体)
4. 响应时间预算自适应 (简单问题直接回答)
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("search_strategy")


class SearchStrategy:
    """智能搜索策略 - 多维度评分版"""

    # ============ 情感陪伴模式 (高优先级拦截) ============
    EMOTIONAL_HIGH_PRIORITY = [
        (r'^(我觉得|我感觉|我想|我好|我真的|我总是|我经常)', 'emotion_expression'),
        (r'^(今天|最近|刚才|昨天).*(开心|难过|生气|焦虑|担心|害怕)', 'emotion_sharing'),
        (r'(孤独|寂寞|难过|伤心|失落|沮丧)', 'emotion_support'),
        (r'(抱抱|亲亲|么么|宝贝|亲爱的|小可爱|爱你|喜欢你|想你)', 'affection'),
        (r'(害羞|脸红|不好意思|尴尬)', 'emotion_shy'),
        (r'(生气|愤怒|烦|讨厌|恼火)', 'emotion_anger'),
        (r'(累|好累|疲惫|困|想睡觉)', 'emotion_tired'),
    ]

    EMOTIONAL_RESPONSE_PATTERNS = [
        (r'^(你.*吗|你.*呢|你觉得|你认为|你喜欢|你想)', 'chat_query'),
        (r'^(哈哈|嘿嘿|嘻嘻|嗯嗯|哦哦|哇|哦|哈哈哈)', 'chat_acknowledge'),
    ]

    CHAT_PATTERNS = [
        (r'^(你好|嗨|哈喽|hi|hello|在吗|在不在)', 'greeting'),
        (r'^(再见|拜拜|晚安|早安|午安|好好休息)', 'farewell'),
    ]

    # ============ 知识问答模式 ============
    KNOWLEDGE_PATTERNS = [
        (r'(是什么|为什么|怎么|如何|哪里|谁|多少|几)', 'fact_query'),
        (r'(介绍|解释|说明|定义|原理|概念)', 'knowledge_explain'),
        (r'(历史|起源|发展|背景|原因|过程)', 'knowledge_history'),
        (r'(对比|区别|差异|优缺点|哪个好)', 'knowledge_compare'),
        (r'(推荐|建议|排行|最好|第一|顶级)', 'knowledge_recommend'),
        (r'(教程|方法|步骤|指南|攻略|技巧)', 'knowledge_tutorial'),
        (r'(例子|案例|示例|比如|举例)', 'knowledge_example'),
        (r'(真的吗|是真的|真假|属实|辟谣|真相)', 'fact_verify'),
    ]

    TIME_SENSITIVE_PATTERNS = [
        (r'(最新|现在|目前|最近|今天|今年)', 'realtime'),
        (r'(天气|新闻|股票|汇率|价格|时间)', 'realtime_info'),
        (r'(实时|动态|变化|更新)', 'realtime_dynamic'),
    ]

    FACT_INDICATORS = [
        r'(\d{4}年|\d+年|\d+月|\d+日)',
        r'(\d+岁|\d+斤|\d+米|\d+公里)',
        r'(世界上|全球|国内|国外|中国|美国)',
        r'(公司|企业|机构|组织|大学|医院)',
    ]

    SIMPLE_QUESTION_THRESHOLD = 15  # 15字以下的简单问题直接回答

    def __init__(self, llm_caller=None):
        # 编译正则，提升性能
        self._emotion_high_re = [(re.compile(p), n) for p, n in self.EMOTIONAL_HIGH_PRIORITY]
        self._emotion_resp_re = [(re.compile(p), n) for p, n in self.EMOTIONAL_RESPONSE_PATTERNS]
        self._chat_re = [(re.compile(p), n) for p, n in self.CHAT_PATTERNS]
        self._knowledge_re = [(re.compile(p), n) for p, n in self.KNOWLEDGE_PATTERNS]
        self._time_re = [(re.compile(p), n) for p, n in self.TIME_SENSITIVE_PATTERNS]
        self._fact_re = [re.compile(p) for p in self.FACT_INDICATORS]

        self._llm_caller = llm_caller
        self._score_weights = {
            'emotion_high': -100,   # 高优先级情感：强制不搜索
            'chat': -50,            # 闲聊：不搜索
            'knowledge': 10,        # 知识问答：加分触发搜索
            'time_sensitive': 15,   # 时效查询：强制搜索
            'fact_query': 8,        # 事实查询：触发搜索
            'simple_question': -20, # 简单问题：不搜索
            'entity_detection': 5,  # 实体检测：辅助加分
        }

    def should_search(self, text: str, context: str = "") -> tuple[bool, str, float]:
        """
        多维度评分判断是否触发搜索

        Returns:
            (是否搜索, 原因说明, 置信度分数)
            分数 > 0 触发搜索，分数 <= 0 不搜索
        """
        if not text or not text.strip():
            return False, "空消息", -100.0

        text_lower = text.lower()
        score = 0.0
        reasons = []

        # 1. 高优先级情感拦截 (直接返回，不进入后续判断)
        for pattern, name in self._emotion_high_re:
            if pattern.search(text_lower):
                return False, f"高优先级情感陪伴: {name}", self._score_weights['emotion_high']

        # 2. 闲聊问候拦截
        for pattern, name in self._chat_re:
            if pattern.match(text_lower):
                return False, f"闲聊: {name}", self._score_weights['chat']

        # 3. 情感回应类 (讨论情感话题)
        for pattern, name in self._emotion_resp_re:
            if pattern.match(text_lower):
                score += self._score_weights['chat']
                reasons.append(f"情感回应: {name}")

        # 4. 知识问答识别 (加权累计)
        for pattern, name in self._knowledge_re:
            if pattern.search(text_lower):
                score += self._score_weights['knowledge']
                reasons.append(f"知识问答: {name}")

        # 5. 时效信息识别 (强制加分)
        for pattern, name in self._time_re:
            if pattern.search(text_lower):
                score += self._score_weights['time_sensitive']
                reasons.append(f"时效信息: {name}")

        # 6. 事实查询识别
        for pattern in self._fact_re:
            if pattern.search(text):
                score += self._score_weights['fact_query']
                reasons.append("事实查询")

        # 7. 简单问题检测 (短句 + 常见字词)
        if len(text.strip()) <= self.SIMPLE_QUESTION_THRESHOLD:
            # 短问题可能是简单查询也可能是情感表达，保留原始评分
            if score <= 0:
                score += self._score_weights['simple_question']
                reasons.append("简单短问题")

        # 8. 实体检测加分
        entity_score = self._detect_knowledge_entities(text)
        if entity_score > 0:
            score += entity_score * self._score_weights['entity_detection']
            reasons.append(f"检测到{entity_score}个知识实体")

        # 9. 如果评分处于模糊区间 (-5 ~ 5) 且有 LLM，则尝试 LLM 辅助判断
        if -5 <= score <= 5 and self._llm_caller:
            llm_score = self._llm_assess(text)
            if llm_score is not None:
                score += llm_score
                reasons.append("LLM辅助判断")

        # 最终决策
        should = score > 0
        reason = "、".join(reasons) if reasons else ("知识问答" if should else "默认不搜索")

        return should, reason, round(score, 1)

    def _detect_knowledge_entities(self, text: str) -> int:
        """检测可能的知识实体 (人名、地名、专业术语等)"""
        entity_count = 0

        # 检测英文/数字混合实体 (可能是产品、公司名)
        if re.search(r'[a-zA-Z]+\d+', text):
            entity_count += 1

        # 检测括号内容 (通常是补充说明)
        if re.search(r'[（(][^）)]{2,20}[）)]', text):
            entity_count += 1

        # 检测连续大写英文 (可能是缩写)
        if re.search(r'\b[A-Z]{2,}\b', text):
            entity_count += 1

        # 检测书名号内容 (通常是作品名)
        if re.search(r'《[^》]+》', text):
            entity_count += 1

        # 检测引号内容 (可能是专有名词)
        if re.search(r'[""\']{1}[^"\']{2,10}[""\']{1}', text):
            entity_count += 1

        # 检测数字+单位组合 (可能是统计数据查询)
        if re.search(r'\d+[.%％]', text):
            entity_count += 1

        return min(entity_count, 3)

    def _llm_assess(self, text: str) -> float | None:
        """LLM 辅助判断 (当正则无法确定时)"""
        if not self._llm_caller:
            return None

        prompt = f"""请判断以下用户消息是否需要通过网络搜索来回答:
判断标准:
- 需要搜索: 涉及事实查询、最新信息、具体数据、专业知识
- 不需要搜索: 情感表达、闲聊问候、自我身份询问、简单常识

用户消息: "{text}"

请仅回答数字:
1=需要搜索
-1=不需要搜索
0=无法判断"""

        try:
            if callable(self._llm_caller):
                result = self._llm_caller(prompt)
            elif hasattr(self._llm_caller, 'chat'):
                result = self._llm_caller.chat(
                    messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.1, max_tokens=10, timeout=8, role='light',
                )
            else:
                return None

            # 解析数字结果
            match = re.search(r'[-]?\d+', result)
            if match:
                val = int(match.group())
                return val * 3  # 放大 LLM 决策权重
        except Exception as e:
            _log.debug(f"LLM 辅助判断失败: {e}")

        return None

    def get_search_query(self, text: str, llm_rewrite: bool = True) -> str:
        """优化搜索词 - 支持 LLM 改写"""
        query = text.strip()

        # 基础清理
        query = re.sub(r'^(请|帮我|能不能|可以吗|我想知道|告诉我)', '', query)
        query = re.sub(r'(好吗|吗|呢|呀|啊|吧|呢|是什么|是什么意思)$', '', query)
        query = re.sub(r'\s+', ' ', query).strip()

        # 如果有 LLM 且查询较短，尝试改写
        if llm_rewrite and self._llm_caller and len(query) < 20:
            rewritten = self._llm_rewrite_query(query)
            if rewritten and len(rewritten) > len(query):
                query = rewritten

        return query if query else text

    def _llm_rewrite_query(self, query: str) -> str | None:
        """LLM 搜索词改写 (补全上下文关键词)"""
        prompt = f"""请将以下简短搜索查询扩展为更完整、更利于搜索引擎查找的查询:
例如:
- "AI" -> "人工智能的定义、应用领域和发展历史"
- "北京天气" -> "北京市今天天气预报实时气温空气质量"
- "Python" -> "Python 编程语言的特点、应用场景和基础语法"

原始查询: "{query}"

请直接输出扩展后的查询词，不要添加其他说明:"""

        try:
            if callable(self._llm_caller):
                result = self._llm_caller(prompt)
            elif hasattr(self._llm_caller, 'chat'):
                result = self._llm_caller.chat(
                    messages=[{'role': 'user', 'content': prompt}],
                    temperature=0.3, max_tokens=60, timeout=10, role='light',
                )
            else:
                return None

            # 清理结果
            result = result.strip().strip('"').strip('"').strip("'").split('\n')[0]
            if result and len(result) > 5:
                return result
        except Exception as e:
            _log.debug(f"LLM 搜索词改写失败: {e}")

        return None


class SearchResultRanker:
    """搜索结果排序器 - 基于权威度和相关性"""

    BOOST_KEYWORDS = {
        '官方': 2.0, '最新': 1.8, '权威': 1.5,
        '百科': 1.3, '知乎': 1.2, 'B站': 1.2,
        'Wikipedia': 1.5, '教程': 1.5, '指南': 1.5,
        '官方网站': 2.0, 'GitHub': 1.8,
    }

    def rank_results(self, results: list[dict], query: str = "") -> list[dict]:
        if not results:
            return []

        scored_results = []
        query_lower = query.lower()

        for result in results:
            score = 1.0
            title = result.get('title', '').lower()
            snippet = result.get('snippet', '').lower()
            url = result.get('url', '').lower()
            source = result.get('source', '').lower()

            # 查询词在标题中的匹配度
            if query_lower:
                query_words = set(query_lower.split())
                title_words = set(re.split(r'\W+', title))
                overlap = len(query_words & title_words)
                score += overlap * 0.3

            # 标题中关键词加分
            for keyword, boost in self.BOOST_KEYWORDS.items():
                if keyword.lower() in title:
                    score *= boost
                    break

            # URL 权威度
            if 'wikipedia' in url or 'wiki' in url:
                score *= 1.5
            if any(edu in url for edu in ['.edu', '.ac', '.gov']):
                score *= 1.3
            if 'github' in url:
                score *= 1.8

            # 来源权重
            source_weights = {
                'baidu': 1.0, 'bing': 1.0, '百度百科': 1.5,
                '知乎': 1.4, 'B站': 1.1, '微博': 0.9,
            }
            if source in source_weights:
                score *= source_weights[source]

            # 摘要长度加分 (信息量更多)
            if len(snippet) > 200:
                score *= 1.2
            elif len(snippet) > 100:
                score *= 1.1

            scored_results.append({
                **result,
                '_relevance_score': round(score, 2),
            })

        scored_results.sort(key=lambda x: x['_relevance_score'], reverse=True)
        return scored_results

    def filter_low_quality(self, results: list[dict], min_score: float = 0.5) -> list[dict]:
        return [r for r in results if r.get('_relevance_score', 0) >= min_score]


class SearchResponseFormatter:
    """搜索结果格式化器 - 构建 RAG 上下文"""

    @staticmethod
    def format_rag_context(results: list[dict], query: str,
                           user_memories: list[dict] = None,
                           max_sources: int = 5) -> str:
        """
        构建 RAG 增强上下文 (用于注入 LLM prompt)

        包含: 搜索结果 + 用户记忆 + 来源引用
        """
        if not results:
            return ""

        parts = [f"【搜索主题】{query}\n"]

        # 1. 搜索结果
        for i, r in enumerate(results[:max_sources], 1):
            title = r.get('title', '未知来源')
            snippet = r.get('snippet', r.get('content', ''))
            source = r.get('source', '未知')
            url = r.get('url', '')

            snippet_clean = snippet[:300].replace('\n', ' ')
            parts.append(
                f"[来源{i}] {source} | {title}\n"
                f"  内容: {snippet_clean}\n"
            )

        # 2. 用户记忆 (个性化)
        if user_memories:
            parts.append("\n【用户记忆参考】")
            for mem in user_memories[:3]:
                key = mem.get('key', '')
                value = mem.get('value', '')
                parts.append(f"  - {key}: {value}")

        # 3. 回答要求 (事实约束) — 与 _generate_search_response 的铁律保持一致
        # 【历史教训】旧版规则较弱（"资料有限"措辞模糊），导致 LLM 编造答案。
        # 此处强化为逐字引用 / 矛盾处理 / 禁止猜测，确保所有调用路径一致。
        parts.append("""
【回答规则】
1. 严格基于以上搜索资料回答，资料中没有的信息绝对禁止编造、禁止用自身知识补全
2. 数字、日期、人名、地名等关键事实必须逐字引用资料原文
3. 资料完全无法回答时诚实说明"没找到可靠资料"，不得猜测；资料部分覆盖时仅回答涉及部分
4. 多条资料矛盾时，指出矛盾并标注双方来源 [1][2]，不擅自取舍
5. 重要信息标注来源编号 [1][2]，可结合用户记忆调整表述方式但不改变事实
""")

        return "\n".join(parts)

    @staticmethod
    def format_summary(results: list[dict], max_sources: int = 3) -> str:
        """生成简洁摘要"""
        if not results:
            return ""

        sources = results[:max_sources]
        parts = []
        for i, source in enumerate(sources, 1):
            title = source.get('title', '未知来源')
            url = source.get('url', '')
            parts.append(f"[{i}] {title}")

        return "\n".join(parts)

    @staticmethod
    def extract_key_points(results: list[dict], max_points: int = 5) -> list[str]:
        """从搜索结果中提取关键点"""
        points = []

        for result in results:
            snippet = result.get('snippet', '')
            if not snippet:
                continue

            sentences = re.split(r'[。！？]', snippet)
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) >= 10:
                    points.append(sentence)
                    if len(points) >= max_points:
                        return points

        return points[:max_points]


class FactChecker:
    """事实核查器 - 交叉验证搜索结果"""

    def __init__(self):
        self._min_sources_for_confidence = 2  # 至少2个来源才高置信

    def assess_confidence(self, results: list[dict], claim: str = "") -> dict[str, Any]:
        """
        评估搜索结果的可信度

        Returns:
            {
                'confidence': 0.0 ~ 1.0,
                'supporting_sources': int,
                'quality_breakdown': dict,
                'recommendation': 'high_confidence' | 'medium' | 'low'
            }
        """
        if not results:
            return {
                'confidence': 0.0,
                'supporting_sources': 0,
                'quality_breakdown': {},
                'recommendation': 'no_results',
            }

        total = len(results)
        quality_scores = [r.get('quality', 0.5) for r in results]
        avg_quality = sum(quality_scores) / len(quality_scores) if quality_scores else 0.5

        # 计算信息源多样性
        unique_sources = set(r.get('source', '') for r in results)
        source_diversity = len(unique_sources) / total if total > 0 else 0

        # 计算内容长度
        avg_snippet_len = sum(len(r.get('snippet', '')[:200]) for r in results) / total if total > 0 else 0

        # 综合置信度
        confidence = (
            avg_quality * 0.4 +
            min(source_diversity * 2, 1.0) * 0.3 +
            min(avg_snippet_len / 200, 1.0) * 0.3
        )
        confidence = min(1.0, max(0.0, confidence))

        # 推荐等级
        if confidence >= 0.7 and total >= self._min_sources_for_confidence:
            recommendation = 'high_confidence'
        elif confidence >= 0.4:
            recommendation = 'medium'
        else:
            recommendation = 'low'

        return {
            'confidence': round(confidence, 2),
            'supporting_sources': total,
            'unique_sources': len(unique_sources),
            'avg_quality': round(avg_quality, 2),
            'recommendation': recommendation,
        }

    def extract_cross_references(self, results: list[dict]) -> list[str]:
        """提取交叉引用 (用于标注回答来源)"""
        refs = []
        for i, r in enumerate(results[:5], 1):
            title = r.get('title', '')
            source = r.get('source', '')
            if title:
                refs.append(f"[{i}] {source}: {title[:30]}")
        return refs
