"""
喵艺 JWT 认证模块
==================
基于 Python 标准库实现的 JWT (HS256) 认证系统，无需 PyJWT 依赖。

核心功能：
- HMAC-SHA256 签名的 JWT Token 生成与验证
- pbkdf2_hmac 加盐密码哈希
- FastAPI 路由保护装饰器（@require_auth / @require_admin）

安全设计要点：
- 密钥通过 AuthManager 构造时注入，不硬编码
- Token payload 包含 user_id / exp / iat / role
- 密码哈希使用 16 字节随机盐 + 100000 次迭代
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Dict, Optional

from fastapi import HTTPException, Request, status

_log = logging.getLogger("MiaoyiAuth")

# JWT 标准算法标识
JWT_ALGORITHM = 'HS256'
# Access Token 默认有效期（秒）
DEFAULT_ACCESS_TOKEN_EXPIRE = 3600  # 1 小时
# Refresh Token 默认有效期（秒）
DEFAULT_REFRESH_TOKEN_EXPIRE = 604800  # 7 天
# 密码哈希迭代次数
PBKDF2_ITERATIONS = 100000
# 盐长度（字节）
SALT_LENGTH = 16


def _b64url_encode(data: bytes) -> str:
    """Base64 URL 安全编码（去掉 padding）"""
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(data: str) -> bytes:
    """Base64 URL 安全解码（补全 padding）"""
    # 补全 padding
    padding = 4 - len(data) % 4
    if padding != 4:
        data += '=' * padding
    return base64.urlsafe_b64decode(data)


class AuthManager:
    """
    JWT 认证管理器

    负责 Token 的创建、验证、密码哈希以及 FastAPI 路由保护。

    Usage:
        auth = AuthManager(secret_key='your-secret-key')
        token = auth.create_token(user_id=123, role='admin')
        payload = auth.verify_token(token)
    """

    def __init__(
        self,
        secret_key: str | None = None,
        access_token_expire: int = DEFAULT_ACCESS_TOKEN_EXPIRE,
        refresh_token_expire: int = DEFAULT_REFRESH_TOKEN_EXPIRE,
    ):
        """
        初始化认证管理器

        Args:
            secret_key: JWT 签名密钥，为 None 时从环境变量 JWT_SECRET_KEY 读取
            access_token_expire: Access Token 有效期（秒）
            refresh_token_expire: Refresh Token 有效期（秒）
        """
        if secret_key is None:
            secret_key = os.environ.get('JWT_SECRET_KEY', '')
        if not secret_key:
            raise ValueError(
                "[Auth] JWT_SECRET_KEY 未配置，请通过参数或环境变量传入"
            )
        if len(secret_key) < 32:
            _log.warning("[Auth] 密钥长度不足 32 字节，建议使用更强的密钥")

        self._secret_key = secret_key.encode('utf-8')
        self._access_token_expire = access_token_expire
        self._refresh_token_expire = refresh_token_expire

        # 已撤销的 Token 集合（用于登出）
        self._revoked_tokens: set = set()
        # 用户 ID -> 该用户所有 Token 的 jti 集合（用于按用户批量撤销）
        self._user_tokens: dict[int, set] = {}
        self._lock = threading.Lock()

        _log.info("[Auth] AuthManager 初始化完成")

    # ========== Token 生成 ==========

    def create_token(
        self,
        user_id: int,
        role: str = 'user',
        extra_claims: dict[str, Any] | None = None,
    ) -> str:
        """
        创建 JWT Access Token

        Args:
            user_id: 用户唯一标识
            role: 用户角色（'user' / 'admin'）
            extra_claims: 额外的 payload 字段

        Returns:
            str: 签名后的 JWT Token
        """
        now = time.time()
        jti = secrets.token_hex(16)
        payload = {
            'user_id': user_id,
            'role': role,
            'exp': now + self._access_token_expire,
            'iat': now,
            'type': 'access',
            'jti': jti,
        }
        if extra_claims:
            payload.update(extra_claims)
        with self._lock:
            self._user_tokens.setdefault(user_id, set()).add(jti)
        return self._encode_jwt(payload)

    def create_refresh_token(
        self,
        user_id: int,
        role: str = 'user',
    ) -> str:
        """
        创建 Refresh Token（更长有效期）

        Args:
            user_id: 用户唯一标识
            role: 用户角色

        Returns:
            str: 签名后的 Refresh Token
        """
        now = time.time()
        jti = secrets.token_hex(32)
        payload = {
            'user_id': user_id,
            'role': role,
            'exp': now + self._refresh_token_expire,
            'iat': now,
            'type': 'refresh',
            'jti': jti,
        }
        with self._lock:
            self._user_tokens.setdefault(user_id, set()).add(jti)
        return self._encode_jwt(payload)

    # ========== Token 验证 ==========

    def verify_token(self, token: str) -> dict[str, Any] | None:
        """
        验证 JWT Token 并返回 payload

        Args:
            token: JWT Token 字符串

        Returns:
            Dict: Token payload 字典，验证失败返回 None
        """
        try:
            payload = self._decode_jwt(token)
            if payload is None:
                return None

            # 检查是否已撤销
            jti = payload.get('jti', '')
            with self._lock:
                if jti in self._revoked_tokens:
                    _log.warning(f"[Auth] Token 已被撤销: jti={jti}")
                    return None

            # 检查是否过期
            exp = payload.get('exp', 0)
            now_ts = datetime.now(timezone.utc).timestamp()
            if exp < now_ts:
                _log.warning("[Auth] Token 已过期")
                return None

            return payload
        except Exception as e:
            _log.error(f"[Auth] Token 验证异常: {e}")
            return None

    def revoke_token(self, token: str) -> bool:
        """
        撤销 Token（通过 jti 加入黑名单）

        Args:
            token: JWT Token

        Returns:
            bool: 是否成功撤销
        """
        payload = self._decode_jwt(token)
        if payload is None:
            return False
        jti = payload.get('jti', '')
        with self._lock:
            self._revoked_tokens.add(jti)
        _log.info(f"[Auth] Token 已撤销: jti={jti}")
        return True

    def revoke_all_user_tokens(self, user_id: int) -> int:
        """
        撤销指定用户的所有 Token

        通过 user_id -> jti 映射，将该用户所有已签发且尚未撤销的
        Token 加入撤销黑名单。

        Args:
            user_id: 用户 ID

        Returns:
            int: 本次实际撤销的 Token 数量
        """
        with self._lock:
            jtis = self._user_tokens.pop(user_id, set())
            count = 0
            for jti in jtis:
                if jti not in self._revoked_tokens:
                    self._revoked_tokens.add(jti)
                    count += 1
        _log.info(f"[Auth] 撤销用户 {user_id} 的 {count} 个 Token")
        return count

    # ========== 密码哈希 ==========

    @staticmethod
    def hash_password(password: str, salt: bytes | None = None) -> str:
        """
        使用 pbkdf2_hmac 加盐哈希密码

        Args:
            password: 明文密码
            salt: 盐值（为 None 时自动生成 16 字节随机盐）

        Returns:
            str: 格式为 'salt$hash' 的哈希字符串（salt 与 hash 均为 hex 编码）
        """
        if salt is None:
            salt = secrets.token_bytes(SALT_LENGTH)
        elif isinstance(salt, str):
            salt = bytes.fromhex(salt)

        dk = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt,
            PBKDF2_ITERATIONS,
        )
        return f"{salt.hex()}${dk.hex()}"

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        """
        验证密码是否匹配存储的哈希

        Args:
            password: 用户输入的明文密码
            stored_hash: 存储的 'salt$hash' 格式字符串

        Returns:
            bool: 是否匹配
        """
        try:
            salt_hex, hash_hex = stored_hash.split('$', 1)
            salt = bytes.fromhex(salt_hex)
            expected_hash = bytes.fromhex(hash_hex)

            dk = hashlib.pbkdf2_hmac(
                'sha256',
                password.encode('utf-8'),
                salt,
                PBKDF2_ITERATIONS,
            )
            # 常量时间比较，防止时序攻击
            return hmac.compare_digest(dk, expected_hash)
        except (ValueError, AttributeError):
            _log.warning("[Auth] 密码哈希格式无效")
            return False

    # ========== 内部 JWT 编解码 ==========

    def _encode_jwt(self, payload: dict[str, Any]) -> str:
        """将 payload 编码为 JWT 字符串（HS256）"""
        header = {'alg': JWT_ALGORITHM, 'typ': 'JWT'}
        header_b64 = _b64url_encode(
            json.dumps(header, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        )
        payload_b64 = _b64url_encode(
            json.dumps(
                payload,
                separators=(',', ':'),
                ensure_ascii=False,
                default=str,
            ).encode('utf-8')
        )
        signing_input = f"{header_b64}.{payload_b64}"
        signature = hmac.new(
            self._secret_key,
            signing_input.encode('ascii'),
            hashlib.sha256,
        ).digest()
        signature_b64 = _b64url_encode(signature)
        return f"{signing_input}.{signature_b64}"

    def _decode_jwt(self, token: str) -> dict[str, Any] | None:
        """
        解码并验证 JWT 签名

        Args:
            token: JWT Token 字符串

        Returns:
            Dict: payload 字典，验证失败返回 None
        """
        try:
            parts = token.split('.')
            if len(parts) != 3:
                _log.warning("[Auth] Token 格式无效：应包含 3 部分")
                return None

            header_b64, payload_b64, signature_b64 = parts

            # 验证签名
            signing_input = f"{header_b64}.{payload_b64}"
            expected_signature = hmac.new(
                self._secret_key,
                signing_input.encode('ascii'),
                hashlib.sha256,
            ).digest()
            actual_signature = _b64url_decode(signature_b64)

            if not hmac.compare_digest(expected_signature, actual_signature):
                _log.warning("[Auth] Token 签名验证失败")
                return None

            # 解码 payload
            payload_bytes = _b64url_decode(payload_b64)
            payload = json.loads(payload_bytes.decode('utf-8'))
            return payload

        except (ValueError, json.JSONDecodeError, Exception) as e:
            _log.error(f"[Auth] Token 解码失败: {e}")
            return None


# ========== FastAPI 路由保护装饰器 ==========

# 全局 AuthManager 实例（延迟初始化）
_global_auth: AuthManager | None = None


def get_auth_manager() -> AuthManager:
    """获取全局 AuthManager 实例（需先调用 init_auth_manager）"""
    if _global_auth is None:
        raise RuntimeError(
            "AuthManager 未初始化，请先调用 init_auth_manager(secret_key=...)"
        )
    return _global_auth


def init_auth_manager(secret_key: str) -> AuthManager:
    """
    初始化全局 AuthManager 实例

    Args:
        secret_key: JWT 签名密钥

    Returns:
        AuthManager: 初始化后的实例
    """
    global _global_auth
    _global_auth = AuthManager(secret_key=secret_key)
    return _global_auth


def require_auth(auth_manager: AuthManager | None = None) -> Callable:
    """
    FastAPI 路由保护装饰器：要求请求携带有效 JWT

    使用方式：
        auth = AuthManager(secret_key='...')

        @app.get('/api/protected')
        @require_auth(auth)
        async def protected_route(request: Request):
            user_id = request.state.user_id
            return {"user_id": user_id}

    Args:
        auth_manager: AuthManager 实例（为 None 时使用全局实例）

    Returns:
        Callable: 装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 从 kwargs 中获取 Request 对象
            request: Request | None = kwargs.get('request')
            if request is None:
                # 尝试从 args 中查找
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            if request is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="[Auth] 无法获取请求对象"
                )

            # 从 Authorization header 提取 Token
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="[Auth] 缺少有效的 Authorization 头"
                )

            token = auth_header[7:]  # 去掉 'Bearer ' 前缀

            # 使用指定的或全局的 AuthManager
            manager = auth_manager or _global_auth
            if manager is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="[Auth] 认证管理器未初始化"
                )

            payload = manager.verify_token(token)
            if payload is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="[Auth] Token 无效或已过期"
                )

            # 将用户信息注入 request.state
            request.state.user_id = payload.get('user_id')
            request.state.user_role = payload.get('role', 'user')
            request.state.token_payload = payload

            return await func(*args, **kwargs)
        return wrapper
    return decorator


def require_admin(auth_manager: AuthManager | None = None) -> Callable:
    """
    FastAPI 路由保护装饰器：要求用户具有 admin 角色

    使用方式：
        auth = AuthManager(secret_key='...')

        @app.get('/api/admin/users')
        @require_admin(auth)
        async def admin_route(request: Request):
            return {"users": [...]}

    Args:
        auth_manager: AuthManager 实例（为 None 时使用全局实例）

    Returns:
        Callable: 装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 先执行认证
            request: Request | None = kwargs.get('request')
            if request is None:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            if request is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="[Auth] 无法获取请求对象"
                )

            # 从 Authorization header 提取 Token
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Bearer '):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="[Auth] 缺少有效的 Authorization 头"
                )

            token = auth_header[7:]
            manager = auth_manager or _global_auth
            if manager is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="[Auth] 认证管理器未初始化"
                )

            payload = manager.verify_token(token)
            if payload is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="[Auth] Token 无效或已过期"
                )

            # 检查角色权限
            user_role = payload.get('role', 'user')
            if user_role not in ('admin', 'founder'):
                _log.warning(
                    f"[Auth] 权限不足: user_id={payload.get('user_id')}, role={user_role}"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="[Auth] 需要管理员权限"
                )

            # 注入用户信息
            request.state.user_id = payload.get('user_id')
            request.state.user_role = user_role
            request.state.token_payload = payload

            return await func(*args, **kwargs)
        return wrapper
    return decorator