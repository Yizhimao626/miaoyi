"""
喵艺 API 限流模块
==================
基于令牌桶算法（Token Bucket）的 API 限流系统，纯 Python 标准库实现。

核心功能：
- 按 IP 地址进行请求频率限制
- 令牌桶算法：恒定速率填充令牌，突发流量可短时超额
- 线程安全的内存存储
- FastAPI 路由装饰器 @rate_limit

默认配置：
- 普通端点：100 次请求 / 60 秒
- 聊天端点：30 次请求 / 60 秒（更严格）
- 单 IP 隔离计数
"""

import logging
import threading
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

from fastapi import HTTPException, Request, status

_log = logging.getLogger("MiaoyiRateLimit")

# 默认限流配置
DEFAULT_REQUESTS = 100
DEFAULT_PERIOD = 60
CHAT_REQUESTS = 30
CHAT_PERIOD = 60


class TokenBucket:
    """
    令牌桶实现

    算法原理：
    - 桶有固定容量（capacity = max_requests）
    - 令牌以恒定速率（rate = max_requests / period）填充
    - 每个请求消耗 1 个令牌
    - 桶为空时请求被拒绝，等待令牌补充

    该算法允许短时突发流量（桶内有积累的令牌时），
    同时保证长时间速率不超过限制。
    """

    def __init__(self, capacity: int, period: int):
        """
        初始化令牌桶

        Args:
            capacity: 桶容量（最大令牌数 = 允许的请求次数）
            period: 周期时长（秒）
        """
        self._capacity = float(capacity)
        self._tokens = float(capacity)  # 初始装满
        self._rate = capacity / period   # 令牌填充速率（个/秒）
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> bool:
        """
        尝试消费令牌

        Args:
            tokens: 需要消费的令牌数（默认 1）

        Returns:
            bool: 是否成功获取令牌
        """
        with self._lock:
            now = time.monotonic()
            # 计算自上次填充以来的时间差，补充令牌
            elapsed = now - self._last_refill
            if elapsed > 0:
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._rate,
                )
                self._last_refill = now

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def get_tokens(self) -> float:
        """获取当前可用令牌数（调试用）"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            if elapsed > 0:
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._rate,
                )
                self._last_refill = now
            return self._tokens

    def reset(self) -> None:
        """重置令牌桶（恢复到满桶状态）"""
        with self._lock:
            self._tokens = self._capacity
            self._last_refill = time.monotonic()


class RateLimiter:
    """
    API 限流器

    为每个 IP 地址维护独立的令牌桶，支持动态配置限流参数。

    Usage:
        limiter = RateLimiter()
        # 注册路由限流规则
        limiter.register_endpoint('/api/chat', requests=30, period=60)

        @app.get('/api/chat')
        @limiter.limit(requests=30, period=60)
        async def chat_endpoint(request: Request):
            ...
    """

    def __init__(self):
        """初始化限流器"""
        # 全局默认桶（用于未注册的端点）
        self._default_bucket = TokenBucket(DEFAULT_REQUESTS, DEFAULT_PERIOD)
        # 按端点路径注册的默认桶配置
        self._endpoint_configs: dict[str, tuple[int, int]] = {}
        # 按 (IP, 端点) 维护的令牌桶实例
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

        # 记录被拒绝的请求
        self._rejected_count = 0
        self._allowed_count = 0

        _log.info("[RateLimit] RateLimiter 初始化完成")

    def register_endpoint(
        self,
        path: str,
        requests: int = DEFAULT_REQUESTS,
        period: int = DEFAULT_PERIOD,
    ) -> None:
        """
        注册端点的默认限流规则

        Args:
            path: API 端点路径（如 '/api/chat'）
            requests: 允许的请求次数
            period: 周期时长（秒）
        """
        with self._lock:
            self._endpoint_configs[path] = (requests, period)
        _log.info(
            f"[RateLimit] 注册端点规则: {path} -> {requests}次/{period}秒"
        )

    def _get_client_ip(self, request: Request) -> str:
        """
        从请求中提取客户端 IP

        支持 X-Forwarded-For 和 X-Real-IP 代理头。

        Args:
            request: FastAPI Request 对象

        Returns:
            str: 客户端 IP 地址
        """
        forwarded = request.headers.get('X-Forwarded-For', '')
        if forwarded:
            # 取第一个 IP（最初的客户端）
            return forwarded.split(',')[0].strip()

        real_ip = request.headers.get('X-Real-IP', '')
        if real_ip:
            return real_ip.strip()

        # 回退到直接连接 IP
        client = request.client
        if client:
            return client.host

        return '127.0.0.1'

    def get_or_create_bucket(
        self,
        ip: str,
        path: str,
        requests: int = 30,
        period: int = 60,
    ) -> TokenBucket:
        """公共接口：获取或创建指定 IP + 端点的令牌桶"""
        return self._get_or_create_bucket(ip, path, requests, period)

    def _get_or_create_bucket(
        self,
        ip: str,
        path: str,
        requests: int,
        period: int,
    ) -> TokenBucket:
        """
        获取或创建指定 IP + 端点的令牌桶

        Args:
            ip: 客户端 IP
            path: 端点路径
            requests: 容量
            period: 周期

        Returns:
            TokenBucket: 令牌桶实例
        """
        key = f"{ip}:{path}"
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(requests, period)
                self._buckets[key] = bucket
            return bucket

    def is_allowed(
        self,
        request: Request,
        requests: int | None = None,
        period: int | None = None,
    ) -> tuple[bool, float]:
        """
        检查请求是否被允许

        Args:
            request: FastAPI Request
            requests: 覆盖默认请求次数限制
            period: 覆盖默认周期

        Returns:
            Tuple[bool, float]: (是否允许, 剩余令牌数)
        """
        ip = self._get_client_ip(request)
        path = request.url.path

        # 确定限流参数：装饰器参数 > 端点注册 > 全局默认
        if requests is not None and period is not None:
            pass  # 使用装饰器传入的参数
        elif path in self._endpoint_configs:
            requests, period = self._endpoint_configs[path]
        else:
            requests, period = DEFAULT_REQUESTS, DEFAULT_PERIOD

        bucket = self._get_or_create_bucket(ip, path, requests, period)
        allowed = bucket.consume(1)
        remaining = bucket.get_tokens()

        if allowed:
            self._allowed_count += 1
        else:
            self._rejected_count += 1
            _log.warning(
                f"[RateLimit] 请求被限流: ip={ip}, path={path}, "
                f"remaining={remaining:.1f}"
            )

        return allowed, remaining

    def get_stats(self) -> dict:
        """获取限流统计信息"""
        with self._lock:
            return {
                'active_buckets': len(self._buckets),
                'allowed_total': self._allowed_count,
                'rejected_total': self._rejected_count,
                'endpoint_configs': dict(self._endpoint_configs),
            }

    def cleanup(self) -> None:
        """清理过期的令牌桶（空闲桶回收）"""
        with self._lock:
            # 当前实现不清理，保持简单
            # 生产环境可考虑定时清理长期未使用的桶
            pass


# ========== 全局限流器实例 ==========
_global_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    """获取全局限流器实例"""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = RateLimiter()
    return _global_limiter


def init_limiter() -> RateLimiter:
    """初始化全局限流器并注册默认规则"""
    global _global_limiter
    _global_limiter = RateLimiter()

    # 注册默认端点规则
    _global_limiter.register_endpoint('/api/chat', requests=CHAT_REQUESTS, period=CHAT_PERIOD)
    _global_limiter.register_endpoint('/api/voice', requests=CHAT_REQUESTS, period=CHAT_PERIOD)
    _global_limiter.register_endpoint('/api/search', requests=50, period=60)
    _global_limiter.register_endpoint('/api/auth/login', requests=10, period=300)  # 登录更严格

    return _global_limiter


def rate_limit(
    requests: int = DEFAULT_REQUESTS,
    period: int = DEFAULT_PERIOD,
    limiter: RateLimiter | None = None,
) -> Callable:
    """
    FastAPI 路由限流装饰器

    使用方式：
        # 方式一：使用全局限流器
        @app.get('/api/chat')
        @rate_limit(requests=30, period=60)
        async def chat_endpoint(request: Request):
            ...

        # 方式二：使用自定义限流器
        my_limiter = RateLimiter()
        @app.get('/api/data')
        @rate_limit(requests=500, period=3600, limiter=my_limiter)
        async def data_endpoint(request: Request):
            ...

    Args:
        requests: 周期内允许的请求次数（默认 100）
        period: 周期时长秒数（默认 60）
        limiter: 自定义限流器实例（为 None 时使用全局）

    Returns:
        Callable: 装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 获取 Request 对象
            request: Request | None = kwargs.get('request')
            if request is None:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            if request is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="[RateLimit] 无法获取请求对象"
                )

            # 检查限流
            target_limiter = limiter or get_limiter()
            allowed, remaining = target_limiter.is_allowed(
                request, requests=requests, period=period,
            )

            if not allowed:
                ip = target_limiter._get_client_ip(request)
                retry_after = period if remaining <= 0 else 1

                _log.warning(
                    f"[RateLimit] 限流触发: ip={ip}, "
                    f"path={request.url.path}, requests={requests}, period={period}"
                )

                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "code": "RATE_LIMITED",
                        "message": "请求过于频繁，请稍后再试喵～",
                        "detail": f"每 {period} 秒最多允许 {requests} 次请求",
                        "retry_after": retry_after,
                    }
                )

            # 允许通过
            return await func(*args, **kwargs)
        return wrapper
    return decorator