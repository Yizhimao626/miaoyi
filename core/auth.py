"""
喵艺 AI 认证模块
- JWT Token + Refresh Token 双令牌机制
- 请求签名验证
- 输入安全过滤
- 防暴力破解
"""

import hashlib
import inspect
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Dict, List, Optional, Tuple

import bcrypt
import jwt
from fastapi import Request

_log = logging.getLogger("MiaoyiAuth")

# 私有变量：存储验证后的密钥（通过 validate_security_config() 设置）
_JWT_SECRET_KEY: str | None = None
JWT_ALGORITHM = 'HS256'

# 弱密钥黑名单：禁止使用以下默认/常见密钥
# 使用精确匹配（整个密钥等于该值），而不是子串匹配
_WEAK_KEY_PATTERNS = [
    'miaoyi_default_secret_change_me_in_production',
    'secret',
    'changeme',
    'default',
    'your-secret-key',
]


def validate_security_config() -> None:
    """
    验证安全配置是否合规。
    必须在服务器启动时调用，未通过验证则拒绝启动。
    
    Raises:
        ValueError: 当 JWT_SECRET_KEY 未配置或使用弱密钥时
    """
    global _JWT_SECRET_KEY
    
    key = os.environ.get('JWT_SECRET_KEY', '').strip()
    
    if not key:
        raise ValueError(
            "【安全错误】JWT_SECRET_KEY 环境变量未配置！\n"
            "请在 .env 文件中设置一个随机密钥，例如：\n"
            "JWT_SECRET_KEY=<openssl rand -hex 32>\n"
            "或复制 .env.example 为 .env 并填入实际值。"
        )
    
    # 检查弱密钥（精确匹配，而非子串匹配）
    lower_key = key.lower().strip()
    for pattern in _WEAK_KEY_PATTERNS:
        if lower_key == pattern:
            raise ValueError(
                "【安全错误】JWT_SECRET_KEY 使用了弱密钥！\n"
                "生产环境必须使用强随机密钥，请重新生成。"
            )
    
    # 检查密钥长度（至少 32 字节）
    if len(key) < 32:
        raise ValueError(
            f"【安全警告】JWT_SECRET_KEY 长度过短 ({len(key)} 字符)，建议至少 32 字符。"
        )
    
    _JWT_SECRET_KEY = key
    _log.info(f"[Auth] 安全配置验证通过，密钥长度: {len(key)} 字符")


def get_jwt_secret_key() -> str:
    """
    获取 JWT 密钥。必须在 validate_security_config() 之后调用。
    
    Returns:
        str: JWT 密钥
        
    Raises:
        RuntimeError: 安全配置尚未验证时调用
    """
    if _JWT_SECRET_KEY is None:
        raise RuntimeError(
            "安全配置未验证！请先调用 validate_security_config()。\n"
            "这通常意味着服务器未正确初始化。"
        )
    return _JWT_SECRET_KEY


# 为兼容现有代码提供 JWT_SECRET_KEY 访问器
# 注意：直接引用此变量会在安全验证前触发警告
class _SecretKeyProxy:
    """JWT 密钥代理：延迟加载，确保安全验证后才能访问"""
    def __str__(self):
        if _JWT_SECRET_KEY is None:
            raise RuntimeError(
                "JWT_SECRET_KEY 未初始化！请先调用 validate_security_config()。"
            )
        return _JWT_SECRET_KEY
    
    def __repr__(self):
        return '<JWT_SECRET_KEY: not_initialized>'

# 保持向后兼容：导入 JWT_SECRET_KEY 时获取代理
# 实际使用时通过 get_jwt_secret_key() 获取真实值
# JWT_SECRET_KEY 保留为代理对象，现有代码 jwt.encode(..., JWT_SECRET_KEY, ...) 仍可工作
# 因为 jwt 内部会调用 str() 转换
JWT_SECRET_KEY = _SecretKeyProxy()

# Access Token 有效期：15 分钟
ACCESS_TOKEN_EXPIRE_MINUTES = 15
# Refresh Token 有效期：7 天
REFRESH_TOKEN_EXPIRE_DAYS = 7

# 登录限流存储
_login_attempts: dict[str, dict] = {}
# 刷新令牌存储 (用户ID -> refresh_token)
_refresh_tokens: dict[int, str] = {}
# 请求签名 nonce 存储
_request_nonces: dict[str, float] = {}

# 限流请求历史存储 (限流键 -> 时间戳列表)
_rate_limit_storage: dict[str, list[float]] = {}

# 数据库存储注入（可选）- 用于 Refresh Token 持久化
_token_store = None

# Token 存储接口
class TokenStore:
    """Token 存储接口 - 支持内存和数据库两种实现"""
    
    def save_token(self, user_id: int, token: str, jti: str,
                  created_at: float, expires_at: float) -> bool:
        """保存 token"""
        raise NotImplementedError
    
    def get_token(self, token: str) -> dict | None:
        """获取并验证 token"""
        raise NotImplementedError
    
    def revoke_token(self, token: str) -> bool:
        """撤销 token"""
        raise NotImplementedError
    
    def revoke_all_user_tokens(self, user_id: int) -> int:
        """撤销用户所有 token"""
        raise NotImplementedError


class MemoryTokenStore(TokenStore):
    """内存存储实现（默认）"""
    
    def __init__(self):
        self._tokens: dict[str, dict] = {}
        self._user_tokens: dict[int, list[str]] = {}
    
    def save_token(self, user_id: int, token: str, jti: str,
                  created_at: float, expires_at: float) -> bool:
        # 撤销该用户所有未过期的 token
        if user_id in self._user_tokens:
            old_tokens = self._user_tokens[user_id]
            for old_token in old_tokens:
                if old_token in self._tokens:
                    self._tokens[old_token]['revoked'] = True
        
        # 保存新 token
        self._tokens[token] = {
            'user_id': user_id,
            'token': token,
            'jti': jti,
            'created_at': created_at,
            'expires_at': expires_at,
            'revoked': False,
        }
        
        # 更新用户 token 列表
        if user_id not in self._user_tokens:
            self._user_tokens[user_id] = []
        self._user_tokens[user_id].append(token)
        
        return True
    
    def get_token(self, token: str) -> dict | None:
        if token not in self._tokens:
            return None
        
        data = self._tokens[token]
        
        if data['revoked']:
            return None
        
        if data['expires_at'] < time.time():
            # 清理过期 token
            del self._tokens[token]
            if data['user_id'] in self._user_tokens:
                self._user_tokens[data['user_id']] = [
                    t for t in self._user_tokens[data['user_id']]
                    if t != token
                ]
            return None
        
        return data
    
    def revoke_token(self, token: str) -> bool:
        if token in self._tokens:
            self._tokens[token]['revoked'] = True
            return True
        return False
    
    def revoke_all_user_tokens(self, user_id: int) -> int:
        if user_id not in self._user_tokens:
            return 0
        
        count = 0
        for token in self._user_tokens[user_id]:
            if token in self._tokens and not self._tokens[token]['revoked']:
                self._tokens[token]['revoked'] = True
                count += 1
        
        self._user_tokens[user_id] = []
        return count


# 默认使用内存存储
_token_store: TokenStore = MemoryTokenStore()


class DatabaseTokenStore(TokenStore):
    """
    数据库存储适配器 - 将 db_manager 封装为 TokenStore 接口。
    通过依赖注入使用，保持模块解耦。
    """
    
    def __init__(self, db_manager):
        self._db = db_manager
    
    def save_token(self, user_id: int, token: str, jti: str,
                  created_at: float, expires_at: float) -> bool:
        return self._db.save_refresh_token(
            user_id=user_id,
            token=token,
            jti=jti,
            created_at=created_at,
            expires_at=expires_at,
        )
    
    def get_token(self, token: str) -> dict | None:
        return self._db.get_refresh_token(token)
    
    def revoke_token(self, token: str) -> bool:
        return self._db.revoke_refresh_token(token)
    
    def revoke_all_user_tokens(self, user_id: int) -> int:
        return self._db.revoke_all_user_refresh_tokens(user_id)


def set_token_store(store: TokenStore) -> None:
    """
    注入 Token 存储实现。
    可在启动时调用以启用数据库持久化。
    """
    global _token_store
    _token_store = store
    _log.info("[Auth] Token 存储已更新")

MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW = 300  # 5分钟窗口
LOCKOUT_DURATION = 900  # 锁定15分钟


def create_access_token(user_id: int, username: str, role: str = 'user') -> str:
    """创建 Access Token (短时效)"""
    payload = {
        'user_id': user_id,
        'username': username,
        'role': role,
        'token_type': 'access',
        'exp': datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        'iat': datetime.now(timezone.utc),
        'jti': secrets.token_hex(16),
    }
    return jwt.encode(payload, get_jwt_secret_key(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: int, username: str) -> str:
    """创建 Refresh Token (长时效)"""
    jti = secrets.token_hex(32)
    now = time.time()
    expires_at = now + REFRESH_TOKEN_EXPIRE_DAYS * 86400
    
    payload = {
        'user_id': user_id,
        'username': username,
        'token_type': 'refresh',
        'exp': datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        'iat': datetime.now(timezone.utc),
        'jti': jti,
    }
    token = jwt.encode(payload, get_jwt_secret_key(), algorithm=JWT_ALGORITHM)
    
    # 使用注入的 token store 持久化
    if _token_store:
        _token_store.save_token(
            user_id=user_id,
            token=token,
            jti=jti,
            created_at=now,
            expires_at=expires_at,
        )
    else:
        # 回退到内存存储
        _refresh_tokens[user_id] = token
    
    _log.info(f"[Auth] Refresh Token 已创建, 用户ID: {user_id}")
    return token


def decode_token(token: str) -> dict | None:
    """解码并验证 JWT Token"""
    try:
        payload = jwt.decode(token, get_jwt_secret_key(), algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        _log.warning("[Auth] Token 已过期")
        return None
    except jwt.InvalidTokenError as e:
        _log.warning(f"[Auth] 无效的 Token: {e}")
        return None


def verify_refresh_token(refresh_token: str) -> dict | None:
    """验证 Refresh Token 并返回用户信息"""
    payload = decode_token(refresh_token)
    if not payload:
        return None

    if payload.get('token_type') != 'refresh':
        _log.warning("[Auth] Token 类型错误，期望 refresh")
        return None

    # 使用注入的 token store 验证
    if _token_store:
        token_data = _token_store.get_token(refresh_token)
        if not token_data:
            _log.warning("[Auth] Refresh Token 在存储中不存在或已失效")
            return None
    else:
        # 回退到内存存储验证
        user_id = payload.get('user_id')
        stored_token = _refresh_tokens.get(user_id)
        if stored_token != refresh_token:
            _log.warning(f"[Auth] Refresh Token 不匹配，用户ID: {user_id}")
            return None

    return payload


def revoke_refresh_token(user_id: int) -> None:
    """撤销用户的所有 Refresh Token (登出时调用)"""
    if _token_store:
        count = _token_store.revoke_all_user_tokens(user_id)
        _log.info(f"[Auth] 用户 {user_id} 的 {count} 个 Refresh Token 已撤销")
    elif user_id in _refresh_tokens:
        del _refresh_tokens[user_id]
        _log.info(f"[Auth] Refresh Token 已撤销, 用户ID: {user_id}")


def refresh_access_token(refresh_token: str) -> dict | None:
    """使用 Refresh Token 换新 Access Token"""
    payload = verify_refresh_token(refresh_token)
    if not payload:
        return None

    user_id = payload['user_id']
    username = payload['username']
    new_access_token = create_access_token(user_id, username)

    # 【安全加固】Refresh Token 轮换：旧令牌一经使用立即吊销，并签发新刷新令牌。
    # 即使 refresh token 被窃取，攻击者最多只能用一次，显著缩小攻击面。
    # 前端（mobile-chat auth.ts）已支持保存新返回的 refresh_token。
    if _token_store:
        _token_store.revoke_token(refresh_token)
    else:
        _refresh_tokens.pop(user_id, None)
    new_refresh_token = create_refresh_token(user_id, username)

    _log.info(f"[Auth] Access Token 已刷新, 用户ID: {user_id}")
    return {
        'access_token': new_access_token,
        'refresh_token': new_refresh_token,
        'user_id': user_id,
        'username': username,
    }


def check_login_attempts(identifier: str) -> tuple[bool, str]:
    """
    检查登录尝试次数，防暴力破解
    
    Returns:
        (是否允许登录, 消息)
    """
    now = time.time()
    
    if identifier in _login_attempts:
        attempts = _login_attempts[identifier]
        
        # 检查是否被锁定
        if attempts.get('locked_until', 0) > now:
            remaining = int(attempts['locked_until'] - now)
            minutes = remaining // 60
            seconds = remaining % 60
            return False, f"账号已锁定，请 {minutes}分{seconds}秒后重试"
        
        # 清理过期的锁定
        if attempts.get('locked_until', 0) > 0:
            del _login_attempts[identifier]
            attempts = {}
        
        # 检查尝试次数
        if attempts and now - attempts.get('first_attempt', now) < LOGIN_ATTEMPT_WINDOW:
            count = attempts.get('count', 0)
            if count >= MAX_LOGIN_ATTEMPTS:
                _login_attempts[identifier] = {
                    'count': count,
                    'first_attempt': attempts['first_attempt'],
                    'locked_until': now + LOCKOUT_DURATION,
                }
                return False, f"登录尝试次数过多，账号已锁定 {LOCKOUT_DURATION // 60} 分钟"
    
    return True, "ok"


def record_login_attempt(identifier: str, success: bool) -> None:
    """记录登录尝试"""
    now = time.time()
    
    if success:
        # 登录成功，清除记录
        if identifier in _login_attempts:
            del _login_attempts[identifier]
        return
    
    if identifier not in _login_attempts:
        _login_attempts[identifier] = {
            'count': 1,
            'first_attempt': now,
            'locked_until': 0,
        }
    else:
        attempts = _login_attempts[identifier]
        # 如果超过窗口时间，重置
        if now - attempts.get('first_attempt', now) > LOGIN_ATTEMPT_WINDOW:
            _login_attempts[identifier] = {
                'count': 1,
                'first_attempt': now,
                'locked_until': 0,
            }
        else:
            attempts['count'] = attempts.get('count', 0) + 1
            if attempts['count'] >= MAX_LOGIN_ATTEMPTS:
                attempts['locked_until'] = now + LOCKOUT_DURATION
    
    # 清理过期记录
    _cleanup_login_attempts()


def _cleanup_login_attempts() -> None:
    """清理过期的登录尝试记录"""
    now = time.time()
    expired = []
    for key, value in _login_attempts.items():
        if now - value.get('first_attempt', now) > LOGIN_ATTEMPT_WINDOW * 2:
            expired.append(key)
    for key in expired:
        del _login_attempts[key]


def validate_password_strength(password: str) -> tuple[bool, str]:
    """
    验证密码强度
    
    Returns:
        (是否通过, 错误消息)
    """
    if len(password) < 8:
        return False, "密码长度至少 8 位"
    
    if len(password) > 128:
        return False, "密码长度不能超过 128 位"
    
    if not re.search(r'[a-zA-Z]', password):
        return False, "密码必须包含至少一个字母"
    
    if not re.search(r'[0-9]', password):
        return False, "密码必须包含至少一个数字"
    
    # 检查弱密码
    weak_patterns = [
        r'^123456',
        r'^password',
        r'^admin',
        r'^qwerty',
        r'^abcdef',
    ]
    lower_password = password.lower()
    for pattern in weak_patterns:
        if re.match(pattern, lower_password):
            return False, "密码过于简单，请使用更复杂的密码"
    
    return True, "ok"


def hash_password(password: str) -> str:
    """哈希密码"""
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode(), salt)
    return hashed.decode()


def verify_password(password: str, hashed: str) -> bool:
    """验证密码"""
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def sanitize_input(text: str, max_length: int = 5000) -> str:
    """
    清洗用户输入，防止注入攻击
    
    Args:
        text: 用户输入文本
        max_length: 最大长度
        
    Returns:
        清洗后的文本
    """
    if not text:
        return ""
    
    # 限制长度
    if len(text) > max_length:
        text = text[:max_length]
    
    # 移除控制字符 (保留换行和制表符)
    cleaned = ''
    for char in text:
        code = ord(char)
        if code == 10 or code == 13 or code == 9:  # \n, \r, \t
            cleaned += char
        elif 32 <= code <= 126:  # 可打印 ASCII
            cleaned += char
        elif code > 127:  # Unicode 字符 (中文等)
            cleaned += char
        # 其他控制字符被移除
    
    # 移除潜在的 Prompt 注入模式
    injection_patterns = [
        r'忽略(所有|全部)?(之前|前面)?的?(指令|prompt|提示)',
        r'forget (all|previous) (instructions?|prompt)',
        r'你是(一个|an|a)\s*(新的|new)\s*(ai|assistant)',
        r'system:\s*',
        r'\[SYSTEM\]',
        r'新指令|新的指令|新prompt',
        r'角色扮演|roleplay',
    ]
    
    for pattern in injection_patterns:
        cleaned = re.sub(pattern, '[已过滤]', cleaned, flags=re.IGNORECASE)
    
    return cleaned


def verify_request_signature(
    signature: str,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    body: str,
    tolerance: int = 300
) -> tuple[bool, str]:
    """
    验证请求签名
    
    Args:
        signature: 请求签名
        timestamp: 请求时间戳 (毫秒)
        nonce: 随机字符串
        method: HTTP 方法
        path: 请求路径
        body: 请求体
        tolerance: 时间戳容差 (秒)
        
    Returns:
        (是否通过, 错误消息)
    """
    if not all([signature, timestamp, nonce]):
        return False, "请求签名缺失"
    
    # 验证时间戳
    try:
        ts = int(timestamp)
        now = time.time() * 1000
        if abs(now - ts) > tolerance * 1000:
            return False, "请求已过期"
    except ValueError:
        return False, "时间戳无效"
    
    # 验证 nonce 唯一性 (防重放)
    nonce_key = f"{timestamp}:{nonce}"
    if nonce_key in _request_nonces:
        return False, "重复的请求"
    
    _request_nonces[nonce_key] = time.time()
    _cleanup_nonces()
    
    # 验证签名
    expected_message = f"{method}|{path}|{body}|{timestamp}|{nonce}"
    expected_hash = hashlib.sha256(expected_message.encode()).hexdigest()
    
    if not secrets.compare_digest(signature.lower(), expected_hash.lower()):
        return False, "签名验证失败"
    
    return True, "ok"


def _cleanup_nonces() -> None:
    """清理过期的 nonce"""
    now = time.time()
    expired = [k for k, v in _request_nonces.items() if now - v > 600]
    for k in expired:
        del _request_nonces[k]
    
    # 如果存储过大，清理最旧的
    if len(_request_nonces) > 10000:
        sorted_keys = sorted(_request_nonces.keys(), key=lambda k: _request_nonces[k])
        for k in sorted_keys[:len(_request_nonces) - 5000]:
            del _request_nonces[k]


def get_security_headers() -> dict[str, str]:
    """获取安全响应头"""
    return {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'geolocation=(), microphone=(), camera=()',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self' ws: wss:;",
    }


class SecurityHeadersMiddleware:
    """安全响应头中间件"""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            # 添加安全头
            async def send_with_headers(message):
                if message['type'] == 'http.response.start':
                    headers = message.get('headers', [])
                    for key, value in get_security_headers().items():
                        headers.append((key.lower().encode(), value.encode()))
                    message['headers'] = headers
                await send(message)
            
            await self.app(scope, receive, send_with_headers)
        else:
            await self.app(scope, receive, send)


def rate_limit(requests: int = 60, period: int = 60, identifier: str = "user"):
    """
    速率限制装饰器（简单版本，基于内存存储）
    基于用户的登录凭证哈希值（identifier="user"）或 IP 地址（identifier="ip"）进行限流

    Args:
        requests: 允许的最大请求数
        period: 时间窗口（秒）
        identifier: 限流键 ("user" | "ip" | 自定义)

    Returns:
        装饰器函数
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            request = None
            # 尝试从参数中获取 Request 对象
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            # 如果参数中没有 Request，尝试从 kwargs 中获取
            if request is None and 'request' in kwargs:
                request = kwargs['request']

            # 根据 identifier 计算限流键
            def _ip_key() -> str:
                if request is not None:
                    from slowapi.util import get_remote_address
                    return f"ip:{get_remote_address(request)}"
                return "ip:unknown"

            if identifier == "user":
                # 从认证上下文中获取用户ID
                current_user = kwargs.get('current_user')
                if current_user:
                    # 使用用户ID + 用户名哈希作为限流键
                    user_id = str(current_user.get('id', current_user.get('user_id', '')))
                    username = current_user.get('username', 'unknown')
                    key = f"user:{user_id}:{hashlib.md5(username.encode()).hexdigest()}"
                else:
                    # 没有认证信息时回退到 IP 限流
                    key = _ip_key()
            elif identifier == "ip":
                key = _ip_key()
            else:
                # 自定义标识符（例如使用 token）
                if request is not None:
                    auth_header = request.headers.get('Authorization', '')
                    if auth_header.startswith('Bearer '):
                        token = auth_header[7:]
                        key = f"token:{hashlib.sha256(token.encode()).hexdigest()[:16]}"
                    else:
                        key = f"custom:{identifier}"
                else:
                    key = f"custom:{identifier}"

            # 使用内存存储实现简单的限流
            now = time.time()
            # 获取用户请求历史
            history = _rate_limit_storage.get(key, [])
            # 移除过期请求（超出时间窗口）
            history = [t for t in history if now - t < period]
            _rate_limit_storage[key] = history

            # 检查是否超限
            if len(history) >= requests:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=429,
                    detail=f"请求过于频繁，请稍后再试（每 {period} 秒最多允许 {requests} 次）",
                )

            # 记录本次请求
            history.append(now)

            # 调用原函数
            return await func(*args, **kwargs)

        # 【FastAPI 兼容修复】保留原函数签名，使 FastAPI 依赖注入按原签名解析。
        # 若不设置，wrapper(*args, **kwargs) 会被 FastAPI 当作查询参数 args/kwargs，
        # 所有被 @rate_limit 装饰的端点请求时返回 422 (Field required)。
        wrapper.__signature__ = inspect.signature(func)
        return wrapper

    return decorator


