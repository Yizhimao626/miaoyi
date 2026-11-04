"""
喵艺输入校验模块
==================
输入文本的安全校验与清洗，防止注入攻击和恶意内容。

核心功能：
- HTML 标签剥离
- Unicode 规范化（NFKC）
- 危险模式检测（SQL 注入、XSS、命令注入、路径遍历）
- 消息长度限制
- 空白字符规范化

安全设计要点：
- 黑名单 + 正则双重检测
- 校验与清洗分离：sanitize_text 返回清洗后的文本
- validate_message 返回 (bool, str) 用于 API 拦截
- 可扩展：支持自定义危险模式添加
"""

import html
import logging
import re
import unicodedata
from typing import Dict, List, Tuple

_log = logging.getLogger("MiaoyiInputGuard")

# 默认消息最大长度
DEFAULT_MAX_LENGTH = 2000

# ========== 危险模式定义 ==========
# SQL 注入特征
_SQL_INJECTION_PATTERNS = [
    re.compile(r"(\b(UNION\s+SELECT)\b)", re.IGNORECASE),
    re.compile(r"(\b(OR\s+1\s*=\s*1)\b)", re.IGNORECASE),
    re.compile(r"(\b(AND\s+1\s*=\s*1)\b)", re.IGNORECASE),
    re.compile(r"(--|#|;)\s*(DROP|DELETE|UPDATE|INSERT|TRUNCATE)\b", re.IGNORECASE),
    re.compile(r"(\b(EXEC|EXECUTE)\s+(sp_|xp_))", re.IGNORECASE),
    re.compile(r"(\bxp_cmdshell\b)", re.IGNORECASE),
    re.compile(r"(\bINFORMATION_SCHEMA\b)", re.IGNORECASE),
    re.compile(r"(\bBENCHMARK\s*\()", re.IGNORECASE),
    re.compile(r"(\bSLEEP\s*\()", re.IGNORECASE),
    re.compile(r"(\bLOAD_FILE\s*\()", re.IGNORECASE),
    re.compile(r"(\bSELECT\s+.*\s+FROM\s+.*\b)", re.IGNORECASE),
    re.compile(r"('|%27)\s*(OR|AND)\s+('|%27)?\d+('|%27)?\s*=\s*('|%27)?\d+", re.IGNORECASE),
    re.compile(r"(\bWAITFOR\s+DELAY\b)", re.IGNORECASE),
    re.compile(r"(;\s*DROP\s+TABLE)", re.IGNORECASE),
    re.compile(r"(;\s*DELETE\s+FROM)", re.IGNORECASE),
    re.compile(r"(--\s|#\s|/\*|\*/).*(--|#|/\*|\*/)", re.IGNORECASE),
]

# XSS 特征
_XSS_PATTERNS = [
    re.compile(r"<\s*script[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*/\s*script[^>]*>", re.IGNORECASE),
    re.compile(r"javascript\s*:", re.IGNORECASE),
    re.compile(r"on\w+\s*=", re.IGNORECASE),  # onclick=, onerror= 等
    re.compile(r"<\s*iframe[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*object[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*embed[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*applet[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*form[^>]*>", re.IGNORECASE),
    re.compile(r"<\s*img[^>]*onerror[^>]*>", re.IGNORECASE),
    re.compile(r"alert\s*\(", re.IGNORECASE),
    re.compile(r"document\.cookie", re.IGNORECASE),
    re.compile(r"eval\s*\(", re.IGNORECASE),
    re.compile(r"expression\s*\(", re.IGNORECASE),
    re.compile(r"<\s*svg[^>]*on\w+[^>]*>", re.IGNORECASE),
    re.compile(r"data\s*:\s*text/html", re.IGNORECASE),
    re.compile(r"<\s*[^>]+on\w+\s*=\s*['\"]?[^>]*>", re.IGNORECASE),
]

# 命令注入特征
_COMMAND_INJECTION_PATTERNS = [
    re.compile(r"[;&|`]\s*(ls|cat|rm|chmod|chown|wget|curl|bash|sh|python|ruby|perl|nc)\b", re.IGNORECASE),
    re.compile(r"\$\([^)]+\)"),
    re.compile(r"`[^`]+`"),
    re.compile(r"\$\{[^}]+\}"),
    re.compile(r"\|\s*(ls|cat|rm|chmod|bash|sh|nc|python)\b", re.IGNORECASE),
    re.compile(r";\s*(ls|cat|rm|chmod|bash|sh|nc|python)\b", re.IGNORECASE),
    re.compile(r"\b(system|exec|passthru|shell_exec|popen)\s*\(", re.IGNORECASE),
    re.compile(r"&&\s*(rm|wget|curl|bash|sh|chmod)\b", re.IGNORECASE),
    re.compile(r"\|\|\s*(rm|wget|curl|bash|sh)\b", re.IGNORECASE),
    re.compile(r">\s*/etc/(passwd|shadow)", re.IGNORECASE),
    re.compile(r"\beval\s*\(\s*['\"]", re.IGNORECASE),
]

# 路径遍历特征
_PATH_TRAVERSAL_PATTERNS = [
    re.compile(r"\.\./"),
    re.compile(r"\.\.\\"),
    re.compile(r"%2e%2e%2f", re.IGNORECASE),
    re.compile(r"%2e%2e/", re.IGNORECASE),
    re.compile(r"\.\.%2f", re.IGNORECASE),
    re.compile(r"%252e%252e%252f", re.IGNORECASE),
    re.compile(r"/etc/passwd", re.IGNORECASE),
    re.compile(r"/proc/self", re.IGNORECASE),
    re.compile(r"c:\\windows", re.IGNORECASE),
    re.compile(r"boot\.ini", re.IGNORECASE),
]

# 危险协议特征
_DANGEROUS_PROTOCOL_PATTERNS = [
    re.compile(r"javascript:", re.IGNORECASE),
    re.compile(r"vbscript:", re.IGNORECASE),
    re.compile(r"data:text/html", re.IGNORECASE),
    re.compile(r"data:text/javascript", re.IGNORECASE),
    re.compile(r"data:application/javascript", re.IGNORECASE),
]


class InputGuard:
    """
    输入安全守卫

    提供文本清洗和危险内容检测的完整能力。

    Usage:
        guard = InputGuard()
        cleaned = guard.sanitize_text(user_input)
        is_valid, reason = guard.validate_message(user_input)

        # 自定义规则
        guard.add_custom_pattern(r"bad_word", "custom")
    """

    def __init__(self, max_length: int = DEFAULT_MAX_LENGTH):
        """
        初始化输入守卫

        Args:
            max_length: 消息最大长度
        """
        self._max_length = max_length
        self._custom_patterns: list[tuple[re.Pattern, str]] = []

        # 预编译所有危险模式
        self._sql_patterns = _SQL_INJECTION_PATTERNS
        self._xss_patterns = _XSS_PATTERNS
        self._cmd_patterns = _COMMAND_INJECTION_PATTERNS
        self._path_patterns = _PATH_TRAVERSAL_PATTERNS
        self._protocol_patterns = _DANGEROUS_PROTOCOL_PATTERNS

        _log.info(f"[InputGuard] 初始化完成，最大长度: {max_length}")

    def sanitize_text(self, text: str) -> str:
        """
        清洗文本：剥离 HTML、规范化空白、截断长度

        Args:
            text: 原始文本

        Returns:
            str: 清洗后的文本
        """
        if not text:
            return ''

        # 1. 剥离 HTML 标签（先剥离，防止转义后无法匹配）
        text = self._strip_html_tags(text)

        # 2. HTML 实体解码
        text = html.unescape(text)

        # 3. HTML 转义（防 XSS 二次注入）
        text = html.escape(text, quote=False)

        # 4. Unicode NFKC 规范化
        text = unicodedata.normalize('NFKC', text)

        # 5. 规范化空白字符
        text = self._normalize_whitespace(text)

        # 6. 长度截断
        if len(text) > self._max_length:
            text = text[:self._max_length]

        return text

    def validate_message(self, text: str) -> tuple[bool, str]:
        """
        校验消息是否安全

        Args:
            text: 用户输入的原始文本

        Returns:
            Tuple[bool, str]: (是否通过校验, 失败原因描述)
        """
        if not text or not text.strip():
            return False, "消息内容不能为空"

        # 长度检查
        if len(text) > self._max_length:
            return False, f"消息长度超过限制（最大 {self._max_length} 字符）"

        # 检测危险模式
        detections = self._detect_dangerous_patterns(text)
        if detections:
            reason = self._format_detection_reason(detections)
            _log.warning(f"[InputGuard] 检测到危险内容: {reason}")
            return False, reason

        return True, ""

    def add_custom_pattern(self, pattern: str, category: str = "custom") -> None:
        """
        添加自定义危险模式

        Args:
            pattern: 正则表达式字符串
            category: 分类名称
        """
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            self._custom_patterns.append((compiled, category))
            _log.info(f"[InputGuard] 添加自定义模式: {pattern} ({category})")
        except re.error as e:
            _log.error(f"[InputGuard] 自定义模式编译失败: {e}")

    def _detect_dangerous_patterns(self, text: str) -> list[dict[str, str]]:
        """
        检测文本中的所有危险模式

        Args:
            text: 待检测文本

        Returns:
            List[Dict]: 检测到的危险项列表
        """
        detections: list[dict[str, str]] = []

        # SQL 注入检测
        for pattern in self._sql_patterns:
            if pattern.search(text):
                detections.append({
                    'category': 'sql_injection',
                    'pattern': pattern.pattern[:50],
                    'description': '检测到 SQL 注入特征',
                })
                break  # 只报告第一个匹配，避免信息泄露

        # XSS 检测
        for pattern in self._xss_patterns:
            if pattern.search(text):
                detections.append({
                    'category': 'xss',
                    'pattern': pattern.pattern[:50],
                    'description': '检测到 XSS 攻击特征',
                })
                break

        # 命令注入检测
        for pattern in self._cmd_patterns:
            if pattern.search(text):
                detections.append({
                    'category': 'command_injection',
                    'pattern': pattern.pattern[:50],
                    'description': '检测到命令注入特征',
                })
                break

        # 路径遍历检测
        for pattern in self._path_patterns:
            if pattern.search(text):
                detections.append({
                    'category': 'path_traversal',
                    'pattern': pattern.pattern[:50],
                    'description': '检测到路径遍历特征',
                })
                break

        # 危险协议检测
        for pattern in self._protocol_patterns:
            if pattern.search(text):
                detections.append({
                    'category': 'dangerous_protocol',
                    'pattern': pattern.pattern[:50],
                    'description': '检测到危险协议',
                })
                break

        # 自定义模式检测
        for pattern, category in self._custom_patterns:
            if pattern.search(text):
                detections.append({
                    'category': category,
                    'pattern': pattern.pattern[:50],
                    'description': f'检测到自定义规则匹配: {category}',
                })

        return detections

    @staticmethod
    def _strip_html_tags(text: str) -> str:
        """
        剥离 HTML 标签（纯标准库实现）

        Args:
            text: 含 HTML 的文本

        Returns:
            str: 剥离标签后的文本
        """
        # 移除所有 HTML 标签（包括自闭合标签）
        text = re.sub(r'<[^>]*>', '', text)
        # 移除 HTML 注释
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        return text

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """
        规范化空白字符：合并多余空行为单个换行，去除首尾空白

        Args:
            text: 原始文本

        Returns:
            str: 规范化后的文本
        """
        # 将连续的空白字符（空格、制表符）替换为单个空格
        text = re.sub(r'[ \t]+', ' ', text)
        # 将多余的空行合并
        text = re.sub(r'\n\s*\n', '\n\n', text)
        # 去除首尾空白
        text = text.strip()
        return text

    @staticmethod
    def _format_detection_reason(detections: list[dict[str, str]]) -> str:
        """
        格式化检测结果为用户友好的提示

        Args:
            detections: 危险项列表

        Returns:
            str: 格式化的原因描述
        """
        categories = set(d['category'] for d in detections)
        category_names = {
            'sql_injection': 'SQL 注入',
            'xss': 'XSS 攻击',
            'command_injection': '命令注入',
            'path_traversal': '路径遍历',
            'dangerous_protocol': '危险协议',
        }
        names = [category_names.get(c, c) for c in categories]
        return f"消息中检测到 {', '.join(names)} 特征，请修改后重试喵～"