"""
喵艺错误处理模块
==================
自定义异常体系 + FastAPI 全局异常处理器，统一错误响应格式。

核心功能：
- 异常层级：MiaoyiException 基类 → AuthException / RateLimitException / ...
- 每个异常类型映射到正确的 HTTP 状态码
- 结构化错误响应：{code, message, detail, request_id}
- 自动记录错误日志（含堆栈）
- 生成唯一 request_id 便于问题追踪

安全设计要点：
- 生产环境不向客户端暴露堆栈细节
- 错误信息使用友好的用户语言（喵艺风格）
- 日志中记录完整堆栈，客户端仅获取必要信息
"""

import logging
import time
import traceback
import uuid
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    pass

_log = logging.getLogger("MiaoyiErrorHandler")


# ========== 自定义异常体系 ==========

class MiaoyiException(Exception):
    """
    喵艺基础异常类

    所有业务异常均继承自此类。

    Attributes:
        code: 错误代码（用于前端判断）
        message: 用户友好的错误描述
        status_code: HTTP 状态码
        detail: 技术细节（仅日志中记录）
    """

    def __init__(
        self,
        message: str = "喵艺遇到了一点小问题",
        code: str = "UNKNOWN_ERROR",
        status_code: int = 500,
        detail: str | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or message
        super().__init__(self.message)

    def to_dict(self, request_id: str = "") -> dict[str, Any]:
        """
        转换为结构化响应字典

        Args:
            request_id: 请求唯一标识

        Returns:
            Dict: 结构化错误响应
        """
        return {
            'code': self.code,
            'status_code': self.status_code,
            'message': self.message,
            'detail': self.detail,
            'request_id': request_id,
        }


class AuthException(MiaoyiException):
    """
    认证异常（401 / 403）

    用途：
    - Token 缺失、无效、过期
    - 权限不足
    - 登录失败

    HTTP 状态码：401（未认证）或 403（禁止访问）
    """

    def __init__(
        self,
        message: str = "认证失败，请重新登录喵～",
        code: str = "AUTH_FAILED",
        status_code: int = 401,
        detail: str | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status_code,
            detail=detail,
        )


class RateLimitException(MiaoyiException):
    """
    限流异常（429）

    用途：
    - 请求频率超过限制
    - 触发反爬策略

    HTTP 状态码：429（请求过多）
    """

    def __init__(
        self,
        message: str = "请求过于频繁，请稍后再试喵～",
        code: str = "RATE_LIMITED",
        retry_after: int = 60,
        detail: str | None = None,
    ):
        self.retry_after = retry_after
        super().__init__(
            message=message,
            code=code,
            status_code=429,
            detail=detail or f"请在 {retry_after} 秒后重试",
        )

    def to_dict(self, request_id: str = "") -> dict[str, Any]:
        """重写以包含 retry_after"""
        result = super().to_dict(request_id)
        result['retry_after'] = self.retry_after
        return result


class ValidationException(MiaoyiException):
    """
    输入校验异常（400）

    用途：
    - 输入为空
    - 输入长度超限
    - 危险内容被检测到
    - 参数格式错误

    HTTP 状态码：400（请求错误）
    """

    def __init__(
        self,
        message: str = "输入内容不符合要求喵～",
        code: str = "VALIDATION_FAILED",
        field: str | None = None,
        detail: str | None = None,
    ):
        self.field = field
        super().__init__(
            message=message,
            code=code,
            status_code=400,
            detail=detail,
        )

    def to_dict(self, request_id: str = "") -> dict[str, Any]:
        """重写以包含 field"""
        result = super().to_dict(request_id)
        if self.field:
            result['field'] = self.field
        return result


class ContentModerationException(MiaoyiException):
    """
    内容审核异常（400）

    用途：
    - 用户输入触发内容安全策略
    - 消息被审核系统拦截

    HTTP 状态码：400（请求错误）
    """

    def __init__(
        self,
        message: str = "内容审核未通过，请修改后重试喵～",
        code: str = "CONTENT_BLOCKED",
        category: str = "",
        score: float = 0.0,
        detail: str | None = None,
    ):
        self.category = category
        self.score = score
        super().__init__(
            message=message,
            code=code,
            status_code=400,
            detail=detail,
        )

    def to_dict(self, request_id: str = "") -> dict[str, Any]:
        """重写以包含审核详情"""
        result = super().to_dict(request_id)
        result['category'] = self.category
        result['score'] = self.score
        return result


class InternalException(MiaoyiException):
    """
    内部服务器异常（500）

    用途：
    - 数据库连接失败
    - 第三方服务不可用
    - 未预期的运行时错误

    HTTP 状态码：500（服务器内部错误）
    """

    def __init__(
        self,
        message: str = "服务器开小差了，请稍后再试喵～",
        code: str = "INTERNAL_ERROR",
        detail: str | None = None,
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=500,
            detail=detail,
        )


# ========== 请求 ID 工具 ==========

def _generate_request_id() -> str:
    """生成唯一请求 ID"""
    return f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


# ========== FastAPI 异常处理器 ==========

async def _miaoyi_exception_handler(
    request: Request,
    exc: MiaoyiException,
) -> JSONResponse:
    """
    处理 MiaoyiException 及其子类

    Args:
        request: FastAPI 请求对象
        exc: 捕获的异常

    Returns:
        JSONResponse: 结构化错误响应
    """
    request_id = _generate_request_id()
    path = request.url.path
    method = request.method

    # 日志记录（完整堆栈）
    _log.warning(
        f"[ErrorHandler] 业务异常: "
        f"code={exc.code}, status={exc.status_code}, "
        f"path={method} {path}, request_id={request_id}, "
        f"message={exc.message}, detail={exc.detail}"
    )

    # 构造响应
    response_data = exc.to_dict(request_id=request_id)

    response = JSONResponse(
        status_code=exc.status_code,
        content=response_data,
    )

    # 安全头
    response.headers['X-Request-ID'] = request_id
    response.headers['X-Error-Code'] = exc.code

    return response


async def _http_exception_handler(
    request: Request,
    exc: HTTPException,
) -> JSONResponse:
    """
    处理 FastAPI/Starlette 原生 HTTPException

    将 FastAPI 原生异常转换为喵艺格式的响应。

    Args:
        request: FastAPI 请求对象
        exc: 捕获的 HTTPException

    Returns:
        JSONResponse: 喵艺格式的错误响应
    """
    request_id = _generate_request_id()
    path = request.url.path
    method = request.method

    # 提取原始 detail
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get('message', str(detail))
        code = detail.get('code', 'HTTP_ERROR')
        extra_detail = detail.get('detail', '')
    else:
        message = str(detail) if detail else "请求处理失败喵～"
        code = f"HTTP_{exc.status_code}"
        extra_detail = str(detail) if detail else ''

    _log.warning(
        f"[ErrorHandler] HTTP异常: "
        f"status={exc.status_code}, path={method} {path}, "
        f"request_id={request_id}, message={message}"
    )

    response_data = {
        'code': code,
        'message': message,
        'detail': extra_detail,
        'request_id': request_id,
    }

    # 处理 429 的 retry_after
    headers = {}
    if exc.status_code == 429:
        retry_after = 60
        if isinstance(detail, dict):
            retry_after = detail.get('retry_after', 60)
        headers['Retry-After'] = str(retry_after)

    if exc.status_code == 401:
        headers['WWW-Authenticate'] = 'Bearer'

    response = JSONResponse(
        status_code=exc.status_code,
        content=response_data,
        headers=headers,
    )
    response.headers['X-Request-ID'] = request_id

    return response


async def _validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """
    处理请求参数校验异常（FastAPI 自动验证失败）

    Args:
        request: FastAPI 请求对象
        exc: 校验异常

    Returns:
        JSONResponse: 喵艺格式的错误响应
    """
    request_id = _generate_request_id()
    path = request.url.path

    # 提取校验错误信息
    errors = exc.errors()
    error_messages = []
    for error in errors:
        loc = error.get('loc', [])
        msg = error.get('msg', '未知错误')
        field = '.'.join(str(l) for l in loc) if loc else 'body'
        error_messages.append(f"{field}: {msg}")

    detail = '; '.join(error_messages[:5])  # 最多展示 5 个错误

    _log.warning(
        f"[ErrorHandler] 参数校验失败: "
        f"path={path}, request_id={request_id}, "
        f"errors={len(errors)}, detail={detail[:200]}"
    )

    response_data = {
        'code': 'VALIDATION_FAILED',
        'message': '请求参数不符合要求喵～',
        'detail': detail,
        'request_id': request_id,
        'fields': [
            {
                'field': '.'.join(str(l) for l in e.get('loc', [])),
                'message': e.get('msg', ''),
            }
            for e in errors[:10]  # 最多 10 个字段
        ],
    }

    response = JSONResponse(
        status_code=400,
        content=response_data,
    )
    response.headers['X-Request-ID'] = request_id

    return response


async def _unexpected_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """
    处理未预期的服务器异常

    生产环境下不向客户端暴露堆栈信息，仅记录完整日志。

    Args:
        request: FastAPI 请求对象
        exc: 意外异常

    Returns:
        JSONResponse: 通用 500 错误响应
    """
    request_id = _generate_request_id()
    path = request.url.path
    method = request.method

    # 完整堆栈日志（仅日志中可见）
    _log.error(
        f"[ErrorHandler] 未预期异常: "
        f"path={method} {path}, request_id={request_id}, "
        f"error_type={type(exc).__name__}, error={str(exc)}"
    )
    _log.error(f"[ErrorHandler] 堆栈:\n{traceback.format_exc()}")

    # 客户端友好响应（不包含堆栈细节）
    response_data = {
        'code': 'INTERNAL_ERROR',
        'message': '服务器开小差了，请稍后再试喵～',
        'detail': f"错误类型: {type(exc).__name__}",
        'request_id': request_id,
    }

    response = JSONResponse(
        status_code=500,
        content=response_data,
    )
    response.headers['X-Request-ID'] = request_id

    return response


# ========== 安装异常处理器 ==========

def setup_error_handlers(app: FastAPI) -> None:
    """
    为 FastAPI 应用安装全局异常处理器

    使用方式：
        from fastapi import FastAPI
        from security.error_handler import setup_error_handlers

        app = FastAPI()
        setup_error_handlers(app)

        # 之后抛出 MiaoyiException 会被自动处理
        from security.error_handler import AuthException
        @app.get('/api/protected')
        async def protected():
            raise AuthException("Token 无效")

    Args:
        app: FastAPI 应用实例
    """
    # 喵艺自定义异常
    app.add_exception_handler(MiaoyiException, _miaoyi_exception_handler)

    # FastAPI/Starlette 原生 HTTPException
    # 注意：FastAPI 自身的 HTTPException 需要用 Starlette 的
    try:
        from starlette.exceptions import HTTPException as StarletteHTTPException
        app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    except ImportError:
        # 回退方案：直接用 FastAPI 的 HTTPException
        # FastAPI 的 HTTPException 继承自 StarletteHTTPException
        app.add_exception_handler(HTTPException, _http_exception_handler)

    # 请求参数校验异常
    app.add_exception_handler(
        RequestValidationError, _validation_exception_handler
    )

    # 捕获所有未预期的异常（最后一道防线）
    app.add_exception_handler(Exception, _unexpected_exception_handler)

    _log.info("[ErrorHandler] FastAPI 异常处理器已安装")