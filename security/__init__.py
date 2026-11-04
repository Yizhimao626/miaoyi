"""
喵艺安全模块
============
提供 JWT 认证、API 限流、输入校验、内容审核、错误处理等安全能力。
所有模块均基于 Python 标准库实现，无第三方依赖。
"""

from security.auth import AuthManager, require_admin, require_auth
from security.content_moderation import ContentModerator
from security.error_handler import (
    AuthException,
    InternalException,
    MiaoyiException,
    RateLimitException,
    ValidationException,
    setup_error_handlers,
)
from security.input_guard import InputGuard
from security.rate_limit import RateLimiter, rate_limit

__all__ = [
    'AuthManager',
    'require_auth',
    'require_admin',
    'RateLimiter',
    'rate_limit',
    'InputGuard',
    'ContentModerator',
    'MiaoyiException',
    'AuthException',
    'RateLimitException',
    'ValidationException',
    'InternalException',
    'setup_error_handlers',
]