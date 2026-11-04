"""
喵艺内容审核模块
==================
基于关键词的内容安全审核系统，支持多级灵敏度和多类别检测。

核心功能：
- 三级灵敏度：strict（严格）/ normal（普通）/ lenient（宽松）
- 六大审核类别：暴力、色情、毒品、赌博、政治、辱骂
- 风险评分（0-1）与可配置阈值
- 自定义关键词和类别扩展

安全设计要点：
- 关键词分级：每个关键词带权重
- 组合匹配：检测上下文组合的敏感内容
- 误报控制：支持白名单（正常语境中出现的关键词）
- 结果可解释：返回具体的违规类别和原因
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger("MiaoyiModeration")

# 灵敏度等级
LEVEL_STRICT = 'strict'
LEVEL_NORMAL = 'normal'
LEVEL_LENIENT = 'lenient'

# 默认阈值：超过则拒绝
_DEFAULT_THRESHOLDS = {
    LEVEL_STRICT: 0.3,    # 严格模式：低阈值即拒绝
    LEVEL_NORMAL: 0.6,    # 普通模式
    LEVEL_LENIENT: 0.8,   # 宽松模式：高阈值才拒绝
}


class ContentModerator:
    """
    内容审核器

    基于关键词和模式匹配的内容安全审核。

    Usage:
        moderator = ContentModerator()
        result = moderator.moderate(text, level='normal')
        # result = {'allowed': True/False, 'reason': str, 'category': str, 'score': float}

        # 自定义关键词
        moderator.add_keyword('violence', 'violent_word', weight=0.8)
    """

    def __init__(self, threshold: dict[str, float] | None = None):
        """
        初始化内容审核器

        Args:
            threshold: 自定义阈值配置 {level: score_threshold}
        """
        self._thresholds = threshold or _DEFAULT_THRESHOLDS.copy()

        # 关键词库：{category: [(keyword, weight), ...]}
        self._keywords: dict[str, list[tuple[str, float]]] = {}
        # 正则模式库：{category: [pattern, ...]}
        self._patterns: dict[str, list[re.Pattern]] = {}
        # 白名单（例外）：在特定上下文中关键词不应被标记
        self._exceptions: dict[str, list[str]] = {}

        self._init_default_keywords()
        self._init_default_patterns()

        _log.info("[ContentModerator] 初始化完成")

    # ========== 默认关键词库 ==========

    def _init_default_keywords(self) -> None:
        """初始化默认关键词库"""
        # 暴力类
        self._keywords['violence'] = [
            ('kill', 0.9), ('murder', 0.9), ('attack', 0.7),
            ('打', 0.5), ('杀', 0.9), ('暴力', 0.8), ('血腥', 0.7),
            ('violence', 0.8), ('blood', 0.5), ('weapon', 0.6),
        ]

        # 色情类
        self._keywords['sexual'] = [
            ('sex', 0.9), ('nude', 0.9), ('porn', 0.9),
            ('色情', 0.9), ('裸', 0.7), ('低俗', 0.7),
            ('pornography', 0.9), ('erotic', 0.8), ('nsfw', 0.7),
        ]

        # 毒品类
        self._keywords['drugs'] = [
            ('drug', 0.9), ('cocaine', 0.9), ('heroin', 0.9),
            ('毒品', 0.9), ('吸毒', 0.9), ('大麻', 0.8),
            ('marijuana', 0.8), ('meth', 0.9), ('crack', 0.8),
        ]

        # 赌博类
        self._keywords['gambling'] = [
            ('casino', 0.8), ('betting', 0.7), ('gambling', 0.9),
            ('赌博', 0.9), ('博彩', 0.8), ('六合彩', 0.9),
            ('online casino', 0.9), ('slot machine', 0.7),
        ]

        # 政治类：审核本身不做政治立场判断，仅做敏感词匹配。
        # 政治敏感词清单因地区/平台规范而异，由部署方按运营规范
        # 通过 ContentModerator.add_keyword('politics', ...) 动态注入，
        # 此处保留类别框架，不再放置无效占位词。
        self._keywords['politics'] = []

        # 辱骂类
        self._keywords['insult'] = [
            ('傻逼', 0.9), ('傻叉', 0.9), ('白痴', 0.8), ('弱智', 0.8),
            ('智障', 0.8), ('脑残', 0.8), ('蠢货', 0.8), ('笨蛋', 0.7),
            ('混蛋', 0.8), ('王八蛋', 0.8), ('贱人', 0.8), ('贱货', 0.8),
            ('畜生', 0.8), ('废物', 0.7), ('垃圾', 0.5), ('去死', 0.8),
            ('滚蛋', 0.7), ('滚开', 0.6), ('狗东西', 0.7), ('猪脑子', 0.7),
            ('窝囊废', 0.7), ('二百五', 0.6), ('神经病', 0.6),
            ('草泥马', 0.8), ('去你妈的', 0.8), ('妈的', 0.6),
        ]

    def _init_default_patterns(self) -> None:
        """初始化正则模式库（用于检测复杂组合）"""
        self._patterns['violence'] = [
            re.compile(r'(?i)kill\s+(him|her|them|someone)'),
            re.compile(r'(?i)attack\s+with'),
        ]
        self._patterns['sexual'] = [
            re.compile(r'(?i)(nude|naked)\s+(pic|photo|image|video)'),
        ]
        self._patterns['drugs'] = [
            re.compile(r'(?i)(buy|sell|deal)\s+(drugs?|cocaine|heroin|weed)'),
        ]
        self._patterns['gambling'] = [
            re.compile(r'(?i)(online|internet)\s+(casino|gambl|bet)'),
        ]

    # ========== 核心审核方法 ==========

    def moderate(
        self,
        text: str,
        level: str = LEVEL_NORMAL,
    ) -> dict:
        """
        审核文本内容

        Args:
            text: 待审核文本
            level: 灵敏度等级
                - 'strict': 严格，低分即拒
                - 'normal': 普通，默认
                - 'lenient': 宽松，高分才拒

        Returns:
            Dict: 审核结果
                {
                    'allowed': bool,      # 是否允许
                    'reason': str,        # 原因说明
                    'category': str,      # 违规类别（空字符串表示无违规）
                    'score': float,       # 风险评分 0-1
                    'level': str,         # 使用的灵敏度等级
                }
        """
        if not text or not text.strip():
            return self._make_result(True, "", 0.0, level, "空文本自动通过")

        # 获取当前等级的阈值
        threshold = self._thresholds.get(level, self._thresholds[LEVEL_NORMAL])

        # 执行多类别扫描
        category_scores: dict[str, float] = {}
        category_reasons: dict[str, list[str]] = {}

        for category, keywords in self._keywords.items():
            score, reasons = self._scan_keywords(text, keywords, category)
            if score > 0:
                category_scores[category] = score
                category_reasons[category] = reasons

        # 执行模式扫描
        for category, patterns in self._patterns.items():
            score, reasons = self._scan_patterns(text, patterns, category)
            if score > 0:
                if category in category_scores:
                    category_scores[category] = max(
                        category_scores[category], score
                    )
                    category_reasons[category].extend(reasons)
                else:
                    category_scores[category] = score
                    category_reasons[category] = reasons

        # 计算综合评分（取最高分）
        max_score = 0.0
        max_category = ""
        all_reasons: list[str] = []

        for cat, score in category_scores.items():
            if score > max_score:
                max_score = score
                max_category = cat
            if cat in category_reasons:
                all_reasons.extend(category_reasons[cat])

        # 检查白名单（例外）
        if self._check_exceptions(text, max_category):
            _log.debug(f"[ContentModerator] 白名单命中，放行: {max_category}")
            return self._make_result(True, "", 0.0, level, "白名单例外")

        # 判定是否通过
        allowed = max_score < threshold

        if allowed:
            return self._make_result(True, "", max_score, level, "内容合规")
        else:
            category_name = self._get_category_display_name(max_category)
            reason_detail = "; ".join(all_reasons[:3])  # 最多展示 3 个原因
            reason = f"内容涉及{category_name}，风险评分 {max_score:.2f}（阈值 {threshold:.2f}）"
            if reason_detail:
                reason += f"。{reason_detail}"
            return self._make_result(False, reason, max_score, level, "")

    # ========== 关键词/模式扫描 ==========

    def _scan_keywords(
        self,
        text: str,
        keywords: list[tuple[str, float]],
        category: str,
    ) -> tuple[float, list[str]]:
        """
        扫描关键词匹配

        Args:
            text: 待检测文本
            keywords: [(关键词, 权重), ...]
            category: 类别名称

        Returns:
            Tuple[float, List[str]]: (最高评分, 匹配原因列表)
        """
        max_score = 0.0
        reasons: list[str] = []
        text_lower = text.lower()

        for keyword, weight in keywords:
            kw_lower = keyword.lower()
            # 支持中文和英文关键词匹配
            if kw_lower in text_lower:
                # 中文关键词按子串匹配，英文按单词边界
                if self._is_chinese(keyword):
                    match_found = kw_lower in text_lower
                else:
                    # 英文使用单词边界
                    match_found = bool(
                        re.search(r'\b' + re.escape(kw_lower) + r'\b', text_lower)
                    )

                if match_found:
                    max_score = max(max_score, weight)
                    reasons.append(f"检测到关键词: '{keyword}'")

        return max_score, reasons

    def _scan_patterns(
        self,
        text: str,
        patterns: list[re.Pattern],
        category: str,
    ) -> tuple[float, list[str]]:
        """
        扫描正则模式匹配

        Args:
            text: 待检测文本
            patterns: 正则模式列表
            category: 类别名称

        Returns:
            Tuple[float, List[str]]: (最高评分, 匹配原因列表)
        """
        max_score = 0.0
        reasons: list[str] = []

        for pattern in patterns:
            if pattern.search(text):
                # 模式匹配给更高权重（0.9）
                max_score = max(max_score, 0.9)
                reasons.append(f"检测到模式匹配: {pattern.pattern[:50]}")

        return max_score, reasons

    # ========== 自定义扩展 ==========

    def add_keyword(
        self,
        category: str,
        keyword: str,
        weight: float = 0.7,
    ) -> None:
        """
        添加自定义关键词到指定类别

        Args:
            category: 类别名称（如 'violence', 'sexual'）
            keyword: 关键词
            weight: 权重（0.0-1.0）
        """
        if category not in self._keywords:
            self._keywords[category] = []
        self._keywords[category].append((keyword, weight))
        _log.info(f"[ContentModerator] 添加关键词: [{category}] '{keyword}' (权重={weight})")

    def add_pattern(
        self,
        category: str,
        pattern: str,
    ) -> None:
        """
        添加自定义正则模式

        Args:
            category: 类别名称
            pattern: 正则表达式字符串
        """
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            if category not in self._patterns:
                self._patterns[category] = []
            self._patterns[category].append(compiled)
            _log.info(f"[ContentModerator] 添加模式: [{category}] {pattern}")
        except re.error as e:
            _log.error(f"[ContentModerator] 模式编译失败: {e}")

    def add_exception(self, category: str, context: str) -> None:
        """
        添加白名单上下文（特定语境中关键词不应触发）

        Args:
            category: 类别名称
            context: 例外上下文（子串匹配）
        """
        if category not in self._exceptions:
            self._exceptions[category] = []
        self._exceptions[category].append(context.lower())
        _log.info(f"[ContentModerator] 添加例外: [{category}] '{context}'")

    def set_threshold(self, level: str, threshold: float) -> None:
        """
        调整灵敏度阈值

        Args:
            level: 灵敏度等级
            threshold: 阈值（0.0-1.0），低于此值视为通过
        """
        self._thresholds[level] = max(0.0, min(1.0, threshold))
        _log.info(f"[ContentModerator] 阈值调整: {level} -> {threshold}")

    # ========== 工具方法 ==========

    @staticmethod
    def _is_chinese(text: str) -> bool:
        """判断文本是否包含中文字符"""
        return bool(re.search(r'[\u4e00-\u9fff]', text))

    @staticmethod
    def _get_category_display_name(category: str) -> str:
        """获取类别显示名称"""
        names = {
            'violence': '暴力',
            'sexual': '色情低俗',
            'drugs': '毒品',
            'gambling': '赌博',
            'politics': '敏感政治',
            'insult': '辱骂',
            'custom': '自定义',
        }
        return names.get(category, category)

    @staticmethod
    def _make_result(
        allowed: bool,
        reason: str,
        score: float,
        level: str,
        detail: str,
    ) -> dict:
        """构造审核结果字典"""
        return {
            'allowed': allowed,
            'reason': reason if not allowed else detail,
            'category': '',  # 兼容性字段
            'score': round(score, 4),
            'level': level,
            # 额外字段：category 留空表示通过
            'violation_category': reason if not allowed else '',
        }

    def _check_exceptions(self, text: str, category: str) -> bool:
        """检查是否命中白名单"""
        if not category or category not in self._exceptions:
            return False
        text_lower = text.lower()
        for exception in self._exceptions[category]:
            if exception in text_lower:
                return True
        return False