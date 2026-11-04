"""喵艺 FastAPI 后端服务"""
import asyncio
import json
import logging
import os
import re
import socket
import sys
import tempfile
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# 【Windows 兼容修复】stdout/stderr 默认 GBK 编码无法输出 emoji（✅ 等），
# 日志含 emoji 时触发 UnicodeEncodeError 并刷屏 Logging error。
# 强制 UTF-8 并容错替换，保证所有日志（含 emoji）正常输出。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

# ruff: noqa: E402 - imports below intentionally follow load_dotenv()/stdout setup

import uvicorn
from fastapi import (
    Body,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


class ClearMemoryRequest(BaseModel):
    # None = 清空全部；N = 只清 N 天前（旧语义保留）
    days: int | None = None

from core.auth import (
    DatabaseTokenStore,
    check_login_attempts,
    create_access_token,
    create_refresh_token,
    get_jwt_secret_key,
    get_security_headers,
    hash_password,
    rate_limit,
    record_login_attempt,
    refresh_access_token,
    revoke_refresh_token,
    sanitize_input,
    set_token_store,
    validate_password_strength,
    validate_security_config,
    verify_password,
)
from core.auth import (
    decode_token as auth_decode_token,
)
from core.miaoyi_core import create_core
from core.voice_manager import VoiceManager, split_tts_segments
from db_manager import DBManager
from utils.config_manager import config_manager

PROJECT_ROOT = Path(__file__).parent


def _resolve_dir(*candidates: str) -> str:
    """目录解析：新结构优先、旧结构兜底，保证迁移期间与新旧部署不 404。

    candidates 形如 'assets/voice_cache'、'voice_cache'（pathlib 会自动按 / 分层）。"""
    for _name in candidates:
        _candidate = PROJECT_ROOT / _name
        if _candidate.is_dir():
            return str(_candidate)
    return str(PROJECT_ROOT / candidates[0])

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger("MiaoyiServer")

import app_state

# ========== 安全配置验证（启动时强制执行） ==========
try:
    validate_security_config()
    _log.info("✅ 安全配置验证通过")
except ValueError as e:
    _log.critical(str(e))
    raise

# 使用经过验证的密钥
JWT_SECRET_KEY = get_jwt_secret_key()
JWT_ALGORITHM = 'HS256'

MAX_REQUEST_BODY_SIZE = int(os.environ.get('MAX_BODY_SIZE', str(1024 * 1024)))
REQUEST_SIGNING_ENABLED = os.environ.get('REQUEST_SIGNING_ENABLED', 'false') == 'true'
REQUEST_TIMESTAMP_TOLERANCE = int(os.environ.get('REQUEST_TIMESTAMP_TOLERANCE', '300'))

request_nonces: dict[str, float] = {}

async def verify_request_signature(request: Request):
    if not REQUEST_SIGNING_ENABLED:
        return True
    
    signature = request.headers.get('x-signature', '')
    timestamp = request.headers.get('x-timestamp', '')
    nonce = request.headers.get('x-nonce', '')
    device_id = request.headers.get('x-device-id', '')
    
    if request.url.path in ['/api/auth/login', '/api/auth/register', '/api/health']:
        return True
    
    if not all([signature, timestamp, nonce]):
        raise HTTPException(status_code=403, detail="喵~ 请求签名缺失")
    
    try:
        ts = int(timestamp)
        now = time.time() * 1000
        if abs(now - ts) > REQUEST_TIMESTAMP_TOLERANCE * 1000:
            raise HTTPException(status_code=403, detail="喵~ 请求已过期")
    except ValueError:
        raise HTTPException(status_code=403, detail="喵~ 时间戳无效")
    
    nonce_key = f"{device_id}:{nonce}"
    if nonce_key in request_nonces:
        raise HTTPException(status_code=403, detail="喵~ 重复请求")
    
    request_nonces[nonce_key] = time.time()
    if len(request_nonces) > 10000:
        request_nonces.clear()
    
    return True

class ConnectionManager:
    # 【多端在线】同一账号允许并存多条连接（多标签页 / 手机+PC 同时在线）。
    # 【风暴教训 20260930】旧版为单槽位注册表 + 新连接踢旧连接（close 4002）：
    # 用户开两个标签页时形成「互踢 → 立即重连 → 再互踢」的乒乓风暴——
    # 前端 onopen 会重置重连退避计数（reconnectAttemptRef=0），重连间隔恒为
    # ~1s 永不退避，服务器日志呈现每秒一轮 连接→断开 循环，控制台被 onerror 刷屏。
    MAX_CONNECTIONS_PER_USER = 10

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}
        self.reconnect_count: int = 0
        self.heartbeat_success_count: int = 0
        self.heartbeat_failure_count: int = 0
        self.duplicate_message_count: int = 0
        self.total_message_count: int = 0
        self.search_request_count: int = 0
        self.search_total_time: float = 0.0
    
    def total_connections(self) -> int:
        """当前 WebSocket 总连接数（含同账号多端并存）"""
        return sum(len(conns) for conns in self.active_connections.values())

    async def connect(self, websocket: WebSocket, user_id: str):
        # 【历史教训 812844】accept 只允许在一处调用一次。
        # 路由层 websocket_endpoint 已经在握手阶段调用过 websocket.accept()，
        # 这里只负责记录连接，**不再重复 accept**，否则会触发 ASGI 状态机错误：
        # "Expected ASGI message websocket.send or websocket.close, but got websocket.accept"
        # 【多端在线】同一用户多条连接并存，不再互踢；主动推送会对该用户所有连接扇出
        conns = self.active_connections.setdefault(user_id, [])
        if websocket not in conns:
            conns.append(websocket)
        # 超限保护：关闭最旧连接，防止异常客户端无限堆积（正常 2-3 条不会触发）
        while len(conns) > self.MAX_CONNECTIONS_PER_USER:
            stale = conns.pop(0)
            try:
                await stale.close(code=4002, reason='连接数超限')
            except Exception:
                pass
        _log.info(f"WebSocket连接建立，用户: {user_id}，当前连接数: {self.total_connections()}")
    
    def disconnect(self, user_id: str, websocket: WebSocket = None, is_reconnect: bool = False):
        # 【多端在线】每条连接只移除自身，互不影响其他端；列表为空时清理用户键
        #（保证 enhance 在线用户列表 active_connections.keys() 准确）
        conns = self.active_connections.get(user_id)
        if not conns:
            return
        if websocket is not None:
            try:
                conns.remove(websocket)
            except ValueError:
                pass
        else:
            conns.clear()
        if not conns:
            self.active_connections.pop(user_id, None)
        if is_reconnect:
            self.reconnect_count += 1
        _log.info(f"WebSocket连接断开，用户: {user_id}，当前连接数: {self.total_connections()}")
    
    def _prune_dead(self, user_id: str, conns: list[WebSocket], dead: list[WebSocket]):
        """从用户连接列表中剔除发送失败的连接，列表为空时清理用户键"""
        for ws in dead:
            try:
                conns.remove(ws)
            except ValueError:
                pass
        if not conns:
            self.active_connections.pop(user_id, None)

    async def send_to_user(self, user_id: str, message: dict[str, Any]):
        # 【多端在线】扇出到该用户所有连接，任一端都能收到推送
        conns = self.active_connections.get(user_id)
        if not conns:
            return
        dead: list[WebSocket] = []
        for ws in list(conns):
            try:
                await ws.send_json(message)
            except Exception as e:
                # 连接断开属正常场景，降级 DEBUG；并移除失效连接
                if self._is_disconnect_error(e):
                    _log.debug(f"发送消息给用户 {user_id} 失败（连接已断开）: {str(e)[:80]}")
                else:
                    _log.error(f"发送消息给用户 {user_id} 失败: {e}")
                dead.append(ws)
        self._prune_dead(user_id, conns, dead)

    async def send_bytes_to_user(self, user_id: str, data: bytes):
        """向指定用户发送二进制数据（用于通话 TTS 音频流）。

        【增强模块】enhance.api_routes.WSNotifier.push_audio 依赖此方法。
        若用户不在线静默丢弃，与 send_to_user 行为一致。
        """
        conns = self.active_connections.get(user_id)
        if not conns:
            return
        dead: list[WebSocket] = []
        for ws in list(conns):
            try:
                await ws.send_bytes(data)
            except Exception as e:
                # 【日志降噪】通话音频在连接断开后发送属正常场景，降级 DEBUG
                if self._is_disconnect_error(e):
                    _log.debug(f"发送二进制数据给用户 {user_id} 失败（连接已断开）: {str(e)[:80]}")
                else:
                    _log.error(f"发送二进制数据给用户 {user_id} 失败: {e}")
                dead.append(ws)
        self._prune_dead(user_id, conns, dead)

    async def broadcast(self, message: dict[str, Any]):
        dead: list[tuple[str, WebSocket]] = []
        for user_id, conns in list(self.active_connections.items()):
            for ws in list(conns):
                try:
                    await ws.send_json(message)
                except Exception as e:
                    if not self._is_disconnect_error(e):
                        _log.error(f"广播消息失败给用户 {user_id}: {e}")
                    dead.append((user_id, ws))
        # 【修复】清理广播过程中发现的失效连接
        for user_id, ws in dead:
            conns = self.active_connections.get(user_id)
            if conns is None:
                continue
            try:
                conns.remove(ws)
            except ValueError:
                pass
            if not conns:
                self.active_connections.pop(user_id, None)

    @staticmethod
    def _is_disconnect_error(e: Exception) -> bool:
        """判断是否为连接断开类异常（正常场景，不需要 ERROR 日志）"""
        from starlette.websockets import WebSocketDisconnect as _WSD
        _e_str = str(e)
        return (
            isinstance(e, _WSD)
            or 'ConnectionClosed' in type(e).__name__
            or 'ClientDisconnected' in type(e).__name__
            or 'Cannot call "send"' in _e_str
            or 'close message has been sent' in _e_str
            or 'Unexpected ASGI message' in _e_str
        )
    
    def record_heartbeat(self, success: bool):
        if success:
            self.heartbeat_success_count += 1
        else:
            self.heartbeat_failure_count += 1
    
    def record_message(self, is_duplicate: bool):
        self.total_message_count += 1
        if is_duplicate:
            self.duplicate_message_count += 1
    
    def record_search(self, duration: float):
        self.search_request_count += 1
        self.search_total_time += duration
    
    def get_metrics(self) -> dict[str, Any]:
        heartbeat_total = self.heartbeat_success_count + self.heartbeat_failure_count
        return {
            'websocket': {
                'active_connections': self.total_connections(),
                'reconnect_count': self.reconnect_count,
            },
            'heartbeat': {
                'success_count': self.heartbeat_success_count,
                'failure_count': self.heartbeat_failure_count,
                'success_rate': (self.heartbeat_success_count / heartbeat_total) * 100 if heartbeat_total > 0 else 0,
            },
            'message': {
                'total_count': self.total_message_count,
                'duplicate_count': self.duplicate_message_count,
                'duplicate_rate': (self.duplicate_message_count / self.total_message_count) * 100 if self.total_message_count > 0 else 0,
            },
            'search': {
                'request_count': self.search_request_count,
                'total_time': self.search_total_time,
                'avg_response_time': self.search_total_time / self.search_request_count if self.search_request_count > 0 else 0,
            },
        }

connection_manager = ConnectionManager()

# 【节流】WS 聊天消息滑动窗口：每用户 10 秒内最多 20 条，防止刷 LLM 调用
ws_msg_times = {}
WS_RATE_LIMIT_MAX = 20
WS_RATE_LIMIT_WINDOW = 10
# 【节流】WS tts / voice_text 通道独立滑动窗口（合成/转写均耗算力，旧版无限频可被刷爆）
ws_tts_times: dict = {}
ws_voice_times: dict = {}


def _ws_rate_ok(user_id: str, times_dict: dict, window: float, max_n: int) -> bool:
    """滑动窗口限频检查（通过则记录本次调用）。"""
    now = time.time()
    dq = times_dict.setdefault(user_id, deque())
    while dq and now - dq[0] > window:
        dq.popleft()
    if len(dq) >= max_n:
        return False
    dq.append(now)
    return True

import concurrent.futures

GLOBAL_THREAD_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8)

# 【健壮性】后台任务统一登记：保留引用防止被 GC 回收，完成时统一记录异常
BACKGROUND_TASKS = set()


def _spawn_background_task(coro, name: str = None):
    task = asyncio.create_task(coro, name=name)
    BACKGROUND_TASKS.add(task)

    def _on_done(t):
        BACKGROUND_TASKS.discard(t)
        if not t.cancelled() and t.exception():
            _log.error(f"后台任务 {t.get_name()} 异常: {t.exception()}")

    task.add_done_callback(_on_done)
    return task

MAX_CONCURRENT_FLASH = 3
FLASH_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_FLASH)

async def with_flash_semaphore(func, *args, **kwargs):
    async with FLASH_SEMAPHORE:
        return await func(*args, **kwargs)

async def retry_on_429(func, max_retries=3, base_delay=1):
    for attempt in range(max_retries):
        try:
            return await func()
        except Exception as e:
            if hasattr(e, 'response') and e.response is not None:
                status = e.response.status_code if hasattr(e.response, 'status_code') else 0
                if status == 429:
                    retry_after = 0
                    if hasattr(e.response, 'headers'):
                        retry_after = int(e.response.headers.get('Retry-After', base_delay * (2 ** attempt)))
                    else:
                        retry_after = base_delay * (2 ** attempt)
                    _log.info(f"触发限流，{retry_after}秒后重试 (尝试 {attempt + 1}/{max_retries})")
                    await asyncio.sleep(retry_after)
                    continue
            raise
    raise Exception("重试次数已达上限")

def create_response(code: int = 200, data: Any = None, message: str = 'ok') -> dict[str, Any]:
    return {
        'code': code,
        'data': data,
        'message': message
    }

def generate_token(user_id: int, username: str, role: str = 'user') -> tuple[str, str]:
    """生成 Access Token + Refresh Token"""
    access_token = create_access_token(user_id, username, role)
    refresh_token = create_refresh_token(user_id, username)
    return access_token, refresh_token

def decode_token(token: str) -> dict[str, Any] | None:
    """解码并验证 JWT Token，委托给 auth 模块"""
    return auth_decode_token(token)

async def get_current_user(request: Request) -> dict[str, Any]:
    token = request.headers.get('Authorization', '')
    if token.startswith('Bearer '):
        token = token[7:]
    
    if not token:
        token = request.cookies.get('miaoyi_token', '')
    
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail='未授权访问，请登录')

    # 【安全】只允许 access token（15 分钟短命）调 API。decode_token 不区分类型，
    # 旧版 7 天长效的 refresh token 可直接当 access token 用——一旦泄露，
    # 攻击者绕过短命 token 设计直接持有 7 天有效凭据。
    if payload.get('token_type') != 'access':
        raise HTTPException(status_code=401, detail='Token 类型无效')

    user_id = payload.get('user_id')
    user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_id, user_id) if user_id else None
    
    if not user:
        raise HTTPException(status_code=401, detail='用户不存在')
    
    return {
        'id': user['id'],
        'username': user['username'],
        'role': user.get('role', 'user'),
        'email': user.get('email', ''),
    }

def require_role(role: str):
    async def dependency(request: Request):
        user = await get_current_user(request)
        if user['role'] != role:
            raise HTTPException(status_code=403, detail='权限不足')
        return user
    
    return dependency

class RegisterRequest(BaseModel):
    username: str
    phone: str
    password: str
    email: str = ''
    invite_code: str = ''  # 内测邀请码（.env 配置 INVITE_CODE 后强制校验）

class LoginRequest(BaseModel):
    username: str
    password: str

def _run_async_in_threadsafe(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            future.add_done_callback(lambda f: f.result() if not f.exception() else None)
        else:
            loop.run_until_complete(coro)
    except RuntimeError:
        asyncio.run(coro)

def _on_proactive_message(message: str, msg_type: str = None, user_id: str = "default_user"):
    # 【P0-4】多用户支持：通过 user_id 精确推送，避免广播问题
    # 【落库】主动消息（含定时提醒）持久化，刷新后历史可恢复；非数字 user_id（legacy default_user）跳过
    try:
        if user_id and str(user_id).isdigit():
            app_state.MESSAGE_SERVICE.save_proactive_message(str(user_id), message, str(msg_type or 'proactive'))
    except Exception as e:
        _log.error(f"主动消息落库失败: {e}")

    async def send():
        await connection_manager.send_to_user(user_id, {
            'type': 'proactive',
            'content': message,
            'timestamp': datetime.now().isoformat(),
        })
    _run_async_in_threadsafe(send())

def _on_thought_bubble(content: str, timestamp: float = None):
    async def send():
        await connection_manager.broadcast({
            'type': 'thought_bubble',
            'content': content,
            'timestamp': timestamp or time.time(),
        })
    _run_async_in_threadsafe(send())

def _on_speech_recognized(text: str):
    async def send():
        await connection_manager.broadcast({
            'type': 'speech',
            'text': text,
            'timestamp': datetime.now().isoformat(),
        })
    _run_async_in_threadsafe(send())

@asynccontextmanager
async def lifespan(app: FastAPI):
    _log.info("正在启动喵艺服务器...")
    
    # 【部署兼容】优先读取 DATABASE_PATH 环境变量（Docker/Render 持久化卷），默认项目根目录 miaoyi.db
    _db_path = os.environ.get('DATABASE_PATH', '').strip() or str(PROJECT_ROOT / 'miaoyi.db')
    _db_dir = os.path.dirname(_db_path)
    if _db_dir:
        os.makedirs(_db_dir, exist_ok=True)
    app_state.DB_INSTANCE = DBManager(_db_path)

    # 【分层】装配消息服务层（Router → Service → Core）
    from services.message_service import MessageService
    app_state.MESSAGE_SERVICE = MessageService(app_state.DB_INSTANCE)
    _log.info("✅ 消息服务层已装配 (services/message_service.py)")
    
    # 注入数据库 Token 存储，实现 Refresh Token 持久化
    token_store = DatabaseTokenStore(app_state.DB_INSTANCE)
    set_token_store(token_store)
    _log.info("✅ Refresh Token 数据库持久化已启用")
    
    # 【安全加固】已移除硬编码默认管理员种子逻辑（多用户模式下由管理员在后台自行创建/授权，
    # 避免每次启动复活默认账号 admin123）

    all_users = app_state.DB_INSTANCE.get_all_users()

    # 【多用户模式】默认保留所有用户，仅在显式设置 CLEAR_NON_ADMIN_ON_STARTUP=true 时清理
    # 这样内测和生产环境可以安全支持多用户注册
    clear_non_admin = os.environ.get('CLEAR_NON_ADMIN_ON_STARTUP', 'false').lower() == 'true'
    if clear_non_admin:
        deleted_users = []
        for user in all_users:
            if user['role'] != 'admin':
                app_state.DB_INSTANCE.delete_user(user['id'])
                deleted_users.append(user['username'])
        if deleted_users:
            _log.info(f"已清理非管理员用户: {', '.join(deleted_users)}")
    else:
        _log.info(f"[Multi-User] 多用户模式已启用，当前共 {len(all_users)} 个用户（不清理）")
    
    user_info_file = PROJECT_ROOT / 'data' / 'user_info.json'
    users_data = []
    for user in app_state.DB_INSTANCE.get_all_users():
        users_data.append({
            'id': user['id'],
            'username': user['username'],
            'nickname': user.get('nickname', ''),
            'role': user.get('role', 'user'),
            'level': user.get('level', 1),
            'exp': user.get('exp', 0),
            'intimacy': user.get('intimacy', 0),
            'created_at': user.get('created_ts', 0),
            'last_login': user.get('last_login_ts', 0),
        })
    
    with open(user_info_file, 'w', encoding='utf-8') as f:
        import json
        json.dump({
            'total_users': len(users_data),
            'admin_count': sum(1 for u in users_data if u['role'] == 'admin'),
            'user_count': sum(1 for u in users_data if u['role'] == 'user'),
            'users': users_data,
            'updated_at': datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)
    
    _log.info(f"用户信息已保存到: {user_info_file}")
    
    app_state.CORE_INSTANCE = create_core()
    app_state.CORE_INSTANCE.register_proactive_callback(_on_proactive_message)
    
    if hasattr(app_state.CORE_INSTANCE, '_thought_bubbles') and app_state.CORE_INSTANCE._thought_bubbles:
        app_state.CORE_INSTANCE._thought_bubbles.register_share_callback(
            lambda bubble: _on_thought_bubble(bubble.content, bubble.timestamp)
        )
    
    app_state.VOICE_MANAGER = VoiceManager()
    app_state.VOICE_MANAGER.register_asr_callback(_on_speech_recognized)
    
    voice_cache_dir = Path(_resolve_dir('assets/voice_cache', 'voice_cache'))
    voice_cache_dir.mkdir(exist_ok=True)
    
    def cleanup_voice_cache():
        try:
            max_age_hours = 24
            # 【数量上限】时间清理之外，额外限制文件总数（保留最近 N 个）：
            # 高频对话一天可产生几百个 TTS 文件，仅按时间清理会无限累积。
            max_files = 200
            cutoff_time = time.time() - max_age_hours * 3600
            deleted_count = 0
            # 跳过点文件（.salt 是缓存键盐，删掉会导致全部缓存失效重合成）
            files = [f for f in voice_cache_dir.iterdir() if f.is_file() and not f.name.startswith('.')]
            for f in files:
                if f.stat().st_mtime < cutoff_time:
                    try:
                        f.unlink()
                        deleted_count += 1
                    except OSError:
                        pass
            # 若仍超过上限，按修改时间从旧到新删除最旧的
            if len(files) - deleted_count > max_files:
                remaining = sorted(
                    [f for f in voice_cache_dir.iterdir() if f.is_file() and not f.name.startswith('.')],
                    key=lambda p: p.stat().st_mtime,
                )
                excess = len(remaining) - max_files
                for f in remaining[:excess]:
                    try:
                        f.unlink()
                        deleted_count += 1
                    except OSError:
                        pass
            if deleted_count > 0:
                _log.info(f"清理语音缓存：删除了 {deleted_count} 个过期/超量文件")
        except Exception as e:
            _log.error(f"清理语音缓存失败: {e}")
    
    cleanup_voice_cache()
    
    exports_dir = PROJECT_ROOT / 'exports'
    
    def cleanup_exports():
        """清理过期的聊天导出文件（默认保留 7 天，防止无限累积）"""
        try:
            exports_dir.mkdir(exist_ok=True)
            max_age_days = 7
            cutoff_time = time.time() - max_age_days * 86400
            deleted_count = 0
            for f in exports_dir.iterdir():
                if f.is_file() and f.stat().st_mtime < cutoff_time:
                    f.unlink()
                    deleted_count += 1
            if deleted_count > 0:
                _log.info(f"清理聊天导出：删除了 {deleted_count} 个过期文件")
        except Exception as e:
            _log.error(f"清理聊天导出失败: {e}")
    
    cleanup_exports()

    # ========== 【数据安全】数据库自动备份 ==========
    # 启动时备份一次 + 每天首次触发时备份（保留最近 7 份），防数据丢失。
    db_backup_dir = PROJECT_ROOT / 'backups' / 'db'
    db_backup_date = ''

    def _backup_mysql(today_tag: str) -> int:
        """MySQL 全库备份（mysqldump → backups/db/miaoyi_mysql_*.sql），返回备份数。"""
        import shutil as _shutil
        import subprocess as _subprocess
        mysqldump = _shutil.which('mysqldump')
        if not mysqldump:
            # 常见安装路径兜底探测
            for p in Path(r'C:\Program Files\MySQL').glob('MySQL Server */bin/mysqldump.exe'):
                mysqldump = str(p)
                break
        if not mysqldump:
            _log.warning("未找到 mysqldump，跳过 MySQL 备份（请将 MySQL bin 目录加入 PATH）")
            return 0
        host = config_manager.get('database.mysql.host', '127.0.0.1')
        port = int(config_manager.get('database.mysql.port', 3306) or 3306)
        user = config_manager.get('database.mysql.user', 'root')
        # 【安全】config.yaml 中密码为 "${MYSQL_PASSWORD}" 占位符，实际值从环境变量读取
        password = os.environ.get('MYSQL_PASSWORD', '')
        if not password:
            _cfg_pw = str(config_manager.get('database.mysql.password', '') or '')
            if _cfg_pw and not _cfg_pw.startswith('${'):
                password = _cfg_pw
        if not password:
            _log.warning("MySQL 备份跳过：MYSQL_PASSWORD 未配置")
            return 0
        database = config_manager.get('database.mysql.database', 'miaoyi')
        dst = db_backup_dir / f"miaoyi_mysql_{today_tag}.sql"
        try:
            # -r 指定输出文件（stdout 会被 capture 吞掉，必须显式落盘）；
            # --set-gtid-purged=OFF：云 MySQL 导出必需；--no-tablespaces：免 PROCESS 权限；
            # 不用 --single-transaction（需要 RELOAD 权限，小库无锁表风险）。
            # 注：MySQL 8.0.33+ 的 mysqldump 会尝试导出 masking policies，普通用户无权限时
            # stderr 有报错但 dump 照常完成（exit 0），属可忽略警告。
            # 【安全】密码经临时 option file 传递——旧写法 f'-p{password}' 会把密码暴露在
            # 进程命令行（Windows 任务管理器/进程枚举工具可见完整 argv）。
            # 注意：--defaults-extra-file 必须是第一个参数（mysqldump 硬性要求）。
            import tempfile as _tempfile
            _opt = _tempfile.NamedTemporaryFile(
                'w', suffix='.cnf', prefix='my_', delete=False, encoding='utf-8')
            try:
                _opt.write(f"[client]\nuser={user}\npassword={password}\n")
                _opt.close()
                cmd = [mysqldump, f'--defaults-extra-file={_opt.name}',
                       '-h', str(host), '-P', str(port),
                       '--set-gtid-purged=OFF', '--no-tablespaces',
                       '--routines', '--triggers', '-r', str(dst), database]
                result = _subprocess.run(cmd, capture_output=True, timeout=120)
            finally:
                try:
                    os.unlink(_opt.name)
                except OSError:
                    pass
            # 成功判定：exit 0 且文件已生成且非空（stderr 里的密码警告/masking 提示可忽略）
            if result.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
                err = result.stderr.decode('utf-8', errors='ignore')[-300:]
                _log.warning(f"mysqldump 失败(exit={result.returncode}): {err}")
                try:
                    if dst.exists():
                        dst.unlink()
                except OSError:
                    pass
                return 0
            _log.info(f"MySQL 全库已备份 → {dst.name} ({dst.stat().st_size // 1024}KB)")
            return 1
        except FileNotFoundError:
            _log.warning("mysqldump 执行失败：文件不存在或不可执行")
            return 0
        except Exception as e:
            _log.warning(f"MySQL 备份异常（跳过）: {e}")
            try:
                if dst.exists():
                    dst.unlink()
            except OSError:
                pass
            return 0

    def _backup_sqlite_file(src: Path, dst: Path) -> bool:
        """SQLite Backup API 一致性快照（裸拷贝活动 WAL 库可能得到半写入页的损坏备份）。"""
        try:
            import sqlite3 as _sqlite3
            _src_conn = _sqlite3.connect(str(src))
            try:
                _dst_conn = _sqlite3.connect(str(dst))
                try:
                    _src_conn.backup(_dst_conn)
                finally:
                    _dst_conn.close()
            finally:
                _src_conn.close()
            return True
        except Exception as e:
            _log.warning(f"{src.name} 备份失败（跳过）: {e}")
            try:
                if dst.exists():
                    dst.unlink()
            except OSError:
                pass
            return False

    def backup_database():
        """备份主库（MySQL: mysqldump 全库 / SQLite: Backup API 快照）与 data/emotion.db 到 backups/db/，保留最近 7 份。"""
        nonlocal db_backup_date
        try:
            db_backup_dir.mkdir(parents=True, exist_ok=True)
            today = time.strftime('%Y%m%d')
            if db_backup_date == today:
                return  # 今天已备份过
            today_tag = f"{today}_{int(time.time()) % 100000}"
            # 按运行模式选择备份方式（环境变量优先，与 db_backend 保持一致）
            db_type = (os.environ.get('MIAOYI_DB_TYPE', '').strip().lower()
                       or str(config_manager.get('database.type', 'sqlite')).lower())
            made = 0
            if db_type == 'mysql':
                made = _backup_mysql(today_tag)
            else:
                # 【部署兼容】主库路径尊重 DATABASE_PATH 环境变量（Docker/Render 持久化卷）
                _main_db = Path(os.environ.get('DATABASE_PATH', '').strip() or str(PROJECT_ROOT / 'miaoyi.db'))
                if _main_db.exists() and _backup_sqlite_file(_main_db, db_backup_dir / f"miaoyi_{today_tag}.db"):
                    made += 1
            # 【修复】emotion.db 是独立 SQLite 库（情感持久化），MySQL 模式下同样活跃，
            # 旧版只在 SQLite 模式备份它——MySQL 模式情感数据完全没有备份。两种模式一律快照。
            _emo_src = PROJECT_ROOT / 'data' / 'emotion.db'
            if _emo_src.exists() and _backup_sqlite_file(_emo_src, db_backup_dir / f"emotion_{today_tag}.db"):
                made += 1
            if made:
                db_backup_date = today
                _log.info(f"✅ 数据库已自动备份: {made} 个文件 → {db_backup_dir}")
            # 保留最近 7 份——三类备份独立分组、按修改时间清理最旧的。
            # 【修复】旧版用 *_*.db 单组 glob 混排且按文件名排序：'emotion_' 字典序小于
            # 'miaoyi_'，历史 miaoyi 备份一多，新生成的 emotion 备份会被误判为"最旧"
            # 而在创建后立即删除（实测 made=2 但目录无 emotion 文件）。
            for pattern in ('miaoyi_mysql_*.sql', 'miaoyi_*.db', 'emotion_*.db'):
                backups = sorted(db_backup_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
                while len(backups) > 7:
                    try:
                        backups[0].unlink()
                        backups.pop(0)
                    except OSError:
                        break
        except Exception as e:
            _log.error(f"数据库备份失败: {e}")

    backup_database()  # 启动时立即备份一次

    # 【睡眠时记忆整理】借鉴 Letta/MemGPT sleep-time compute：
    # 每天一次把用户当日对话经 LLM 提炼为结构化事实写入 semantic 记忆层，
    # 供关键词触发注入（get_memory_context_bundle）在后续对话中召回。
    _consolidation_done: dict[str, str] = {}  # user_id -> 已整理日期 YYYY-MM-DD

    def sleep_time_memory_consolidation():
        today = datetime.now().strftime('%Y-%m-%d')
        core = app_state.CORE_INSTANCE
        db = app_state.DB_INSTANCE
        if core is None or db is None:
            return
        day_start_ts = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        try:
            users = (db.get_all_users() or [])
        except Exception as e:
            _log.warning(f"[记忆整理] 获取用户列表失败: {e}")
            return
        for u in users:
            uid = str(u.get('id', ''))
            if not uid or _consolidation_done.get(uid) == today:
                continue
            try:
                msgs = db.get_user_messages(uid, limit=60) or []
                today_msgs = [m for m in msgs if (m.get('created_ts') or 0) >= day_start_ts]
                if len(today_msgs) < 6:
                    _consolidation_done[uid] = today  # 当日对话太少，标记避免每小时重复扫描
                    continue
                lines = []
                for m in today_msgs:
                    role = '用户' if m.get('role') == 'user' else '喵艺'
                    lines.append(f"{role}: {m.get('text', '')}")
                dialogue = '\n'.join(lines)[-4000:]
                # 【mem0 式冲突解决】把已有记忆一并交给 LLM：
                # 新事实走 facts，被今日对话推翻的旧记忆走 obsolete_keys（淘汰）
                try:
                    _, _, existing_facts = core.memory.get_memory_context_bundle(uid)
                except Exception:
                    existing_facts = {}
                existing_lines = '\n'.join(f"- {k}: {v}" for k, v in existing_facts.items()) or '（暂无）'
                prompt = (
                    "你是记忆整理助手。下面是已有长期记忆和今天的对话。\n"
                    "任务：\n"
                    "1. 从对话中提炼值得长期记住的新事实（用户偏好/习惯/计划/重要事件/人际关系），"
                    "只提取明确表达的信息，不要推测；\n"
                    "2. 若对话表明某条已有记忆已过时或被推翻（如用户说不再喜欢X、搬家了），"
                    "把该条旧记忆的 key 原样列入 obsolete_keys；没有就输出空数组。\n"
                    '输出 JSON 对象：{"facts": [{"key": "类别(不超过8字)", "value": "事实(不超过30字)"}], '
                    '"obsolete_keys": ["要删除的旧记忆key"]}\n'
                    'facts 最多5条，没有值得记的输出空数组；obsolete_keys 只能从已有记忆列表的 key 里选。\n\n'
                    "已有记忆：\n" + existing_lines + "\n\n今日对话：\n" + dialogue
                )
                llm_out = core._safe_llm_call(
                    [{'role': 'user', 'content': prompt}],
                    temperature=0.2, max_tokens=600, timeout=30)
                if not llm_out:
                    continue  # LLM 暂不可用（限流/熔断），不标记，下个小时重试
                facts: list = []
                obsolete_keys: list = []
                m_json = re.search(r'\{.*\}|\[.*\]', llm_out, re.S)
                if m_json:
                    try:
                        data = json.loads(m_json.group(0))
                    except Exception:
                        data = None
                    if isinstance(data, dict):
                        raw_facts = data.get('facts')
                        obsolete_keys = [str(k).strip() for k in (data.get('obsolete_keys') or []) if str(k).strip()]
                    elif isinstance(data, list):
                        raw_facts = data  # 兼容旧格式（纯数组）
                        obsolete_keys = []
                    else:
                        raw_facts = []
                    if isinstance(raw_facts, list):
                        facts = [f for f in raw_facts
                                 if isinstance(f, dict) and f.get('key') and f.get('value')]
                saved = removed = 0
                # 先淘汰被推翻的旧记忆（只允许删除确实存在的 key，防 LLM 幻觉误删）
                for k in obsolete_keys[:5]:
                    if k in existing_facts:
                        try:
                            core.memory.forget(k, user_id=uid)
                            removed += 1
                        except Exception as e:
                            _log.warning(f"[记忆整理] 淘汰记忆失败 user={uid} key={k}: {e}")
                for f in facts[:5]:
                    key = str(f['key']).strip()[:12]
                    value = str(f['value']).strip()[:50]
                    if key and value:
                        try:
                            core.memory.remember(key, value, user_id=uid)
                            saved += 1
                        except Exception as e:
                            _log.warning(f"[记忆整理] 写入记忆失败 user={uid} key={key}: {e}")
                _consolidation_done[uid] = today
                _log.info(f"[记忆整理] 用户 {uid}: 当日消息 {len(today_msgs)} 条，"
                          f"提炼 {saved} 条、淘汰 {removed} 条")
            except Exception as e:
                _log.warning(f"[记忆整理] 用户 {uid} 整理失败: {e}")

    # 【L6 猫咪日记】每天一次为每个用户生成 AI 第一人称日记（内部判重，幂等）。
    # 与睡眠整理同一调度节奏：每小时被 periodic_cleanup 唤醒，当日素材不足则标记跳过。
    _diary_done: dict[str, str] = {}  # user_id -> 已生成日期 YYYY-MM-DD

    def generate_daily_diaries():
        today = datetime.now().strftime('%Y-%m-%d')
        core = app_state.CORE_INSTANCE
        db = app_state.DB_INSTANCE
        if core is None or db is None or core._autobiography is None:
            return
        day_start_ts = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        try:
            users = (db.get_all_users() or [])
        except Exception as e:
            _log.warning(f"[猫咪日记] 获取用户列表失败: {e}")
            return
        for u in users:
            uid = str(u.get('id', ''))
            if not uid or _diary_done.get(uid) == today:
                continue
            try:
                # 已有今日日记（如用户手动触发过）直接标记完成
                if db.get_autobiography_by_date(uid, today):
                    _diary_done[uid] = today
                    continue
                msgs = db.get_user_messages(uid, limit=40) or []
                today_msgs = [m for m in msgs if (m.get('created_ts') or 0) >= day_start_ts]
                if len(today_msgs) < 4:
                    _diary_done[uid] = today  # 当日素材太少，不写日记（避免空泛罐头），标记避免每小时重扫
                    continue
                lines = []
                for m in today_msgs:
                    role = '主人' if m.get('role') == 'user' else '我'
                    lines.append(f"{role}: {m.get('text', '')}")
                chat_digest = '\n'.join(lines)[-1500:]
                diary = core._autobiography.generate_daily_diary(uid, chat_digest=chat_digest)
                if diary:
                    _diary_done[uid] = today
                    _log.info(f"[猫咪日记] 用户 {uid} 今日日记已生成（{len(diary)}字）")
                # diary 为 None（LLM 暂不可用）不标记，下小时重试
            except Exception as e:
                _log.warning(f"[猫咪日记] 用户 {uid} 日记生成失败: {e}")

    # 【L7 生活模拟】朋友圈动态生成：基于作息表模板（零 LLM 成本），每小时检查一次
    def generate_moments_for_users():
        core = app_state.CORE_INSTANCE
        db = app_state.DB_INSTANCE
        if core is None or db is None:
            return
        cfg = core._config.get_all().get('proactive', {}) if core._config else {}
        if not cfg.get('moments_enabled', True):
            return
        interval_hours = float(cfg.get('moments_interval_hours', 3) or 3)
        max_per_day = int(cfg.get('moments_max_per_day', 4) or 4)
        now = time.time()
        try:
            users = (db.get_all_users() or [])
        except Exception as e:
            _log.warning(f"[朋友圈] 获取用户列表失败: {e}")
            return
        for u in users:
            uid = str(u.get('id', ''))
            if not uid:
                continue
            try:
                if db.count_cat_moments_today(uid) >= max_per_day:
                    continue
                latest_ts = db.get_latest_cat_moment_ts(uid)
                if latest_ts and (now - latest_ts) < interval_hours * 3600:
                    continue
                content, emoji, activity = core._proactive_chat._life.generate_moment_text() \
                    if core._proactive_chat else (None, None, None)
                if not content:
                    from core.life_schedule import LifeScheduleSystem
                    content, emoji, activity = LifeScheduleSystem().generate_moment_text()
                db.add_cat_moment(uid, content, mood_emoji=emoji or '🐾',
                                  activity=activity or '', source='template')
                _log.info(f"[朋友圈] 用户 {uid} 新动态: {content[:40]}")
            except Exception as e:
                _log.warning(f"[朋友圈] 用户 {uid} 动态生成失败: {e}")

    # 【L10 低频客串】"喵艺的朋友"剧情事件：每小时检查（内部含 3 天间隔+概率+适宜时段判定，幂等）
    def run_guest_events_for_users():
        core = app_state.CORE_INSTANCE
        db = app_state.DB_INSTANCE
        if core is None or db is None:
            return
        # 惰性构造（LLM 适配器就绪后才创建，失败不重试挂主链路）
        ge = getattr(core, 'guest_events', None)
        if ge is None:
            llm = core._get_llm() if hasattr(core, '_get_llm') else None
            memory = getattr(core, 'memory', None)
            if not llm or memory is None:
                return
            try:
                from core.guest_events import GuestEventSystem
                ge = GuestEventSystem(llm=llm, memory=memory)
                core.guest_events = ge
            except Exception as e:
                _log.warning(f"[客串] 事件系统初始化失败: {e}")
                return
        try:
            users = (db.get_all_users() or [])
        except Exception as e:
            _log.warning(f"[客串] 获取用户列表失败: {e}")
            return
        for u in users:
            uid = str(u.get('id', ''))
            if not uid:
                continue
            try:
                messages = ge.maybe_run(uid, db)
                if not messages:
                    continue
                # 逐条推送，间隔 2 秒模拟群聊展开节奏（executor 线程内阻塞 sleep 安全）
                for i, line in enumerate(messages):
                    if i:
                        time.sleep(2.0)
                    _on_proactive_message(line, 'guest_event', uid)
                _log.info(f"[客串] 用户 {uid} 客串事件已推送（{len(messages)} 条）")
            except Exception as e:
                _log.warning(f"[客串] 用户 {uid} 事件生成失败: {e}")

    async def periodic_cleanup():
        while True:
            await asyncio.sleep(3600)
            # 【防停摆】每个清理步骤独立捕获异常：任一失败不得终止每小时循环，
            # 否则语音缓存/导出文件/备份/记忆整理全部永久失效（旧版 4 个裸调用无保护）
            for _step in (
                ('清理语音缓存', cleanup_voice_cache),
                ('清理导出文件', cleanup_exports),
                ('数据库备份', backup_database),
                ('清理消息ID缓存', cleanup_msg_id_cache),
            ):
                try:
                    _step[1]()
                except Exception as e:
                    _log.warning(f"[周期清理] {_step[0]}失败(下小时重试): {e}")
            # 【睡眠时记忆整理】每天一次提炼当日对话为结构化记忆（内部判重，幂等）
            try:
                await asyncio.get_event_loop().run_in_executor(
                    GLOBAL_THREAD_POOL, sleep_time_memory_consolidation)
            except Exception as e:
                _log.warning(f"睡眠时记忆整理失败: {e}")
            # 【L6 猫咪日记】每天一次基于当日对话生成 AI 第一人称日记（内部判重，幂等）
            try:
                await asyncio.get_event_loop().run_in_executor(
                    GLOBAL_THREAD_POOL, generate_daily_diaries)
            except Exception as e:
                _log.warning(f"猫咪日记生成失败: {e}")
            # 【L7 生活模拟】朋友圈动态：作息表模板生成，间隔/每日上限由配置约束（幂等）
            try:
                await asyncio.get_event_loop().run_in_executor(
                    GLOBAL_THREAD_POOL, generate_moments_for_users)
            except Exception as e:
                _log.warning(f"朋友圈动态生成失败: {e}")
            # 【L10 低频客串】"喵艺的朋友"剧情事件：触发判定含 3 天间隔+概率+适宜时段（幂等）
            try:
                await asyncio.get_event_loop().run_in_executor(
                    GLOBAL_THREAD_POOL, run_guest_events_for_users)
            except Exception as e:
                _log.warning(f"客串事件生成失败: {e}")
            # 【打磨】定期清理数据库过期缓存（搜索缓存/登录尝试等），防累积
            try:
                if app_state.DB_INSTANCE:
                    app_state.DB_INSTANCE.clean_expired_cache()
                    app_state.DB_INSTANCE._cleanup_expired_refresh_tokens()
                    # 【生命周期】情感快照(7天)/情感流水(30天)/搜索缓存保留策略（幂等）
                    app_state.DB_INSTANCE.cleanup_stale_data()
                    # 【完整性】孤儿数据防御清理（消息/标签外键失联，幂等）
                    app_state.DB_INSTANCE.cleanup_orphan_data()
                    # 【知识库】清理未验证且置信度过低的三元组（幂等）
                    app_state.DB_INSTANCE.cleanup_low_confidence_triples()
            except Exception as e:
                _log.warning(f"定期数据库清理失败: {e}")
    
    _spawn_background_task(periodic_cleanup(), name='periodic_cleanup')
    
    app.mount("/voice_cache", StaticFiles(directory=str(voice_cache_dir)), name="voice_cache")

    # 【视觉能力】聊天图片目录：uuid 文件名不可猜测，公开静态服务（与 voice_cache/
    # stickers 同一信任模型——<img> 无法带请求头鉴权，用不可猜测性替代）
    uploads_chat_dir = Path(_resolve_dir('assets/uploads_chat', 'uploads_chat'))
    uploads_chat_dir.mkdir(exist_ok=True)
    app.mount("/uploads-chat", StaticFiles(directory=str(uploads_chat_dir)), name="uploads_chat")
    
    sticker_dir = Path(_resolve_dir('assets/stickers_src', '表情包存放'))
    if sticker_dir.exists():
        app.mount("/stickers", StaticFiles(directory=str(sticker_dir)), name="stickers")
    
    uploads_dir = Path(_resolve_dir('assets/uploads', 'uploads'))
    os.makedirs(uploads_dir, exist_ok=True)
    # 【安全修复】/uploads、/exports 不再裸 StaticFiles 挂载（无鉴权，知道文件名即可
    # 跨账号下载他人聊天导出）。改为下方 /api/files/* 鉴权路由（校验登录 + 文件归属）。
    exports_dir = PROJECT_ROOT / 'exports'
    os.makedirs(exports_dir, exist_ok=True)
    app_state.EXPORTS_DIR = exports_dir
    app_state.UPLOADS_DIR = uploads_dir
    
    from admin import router as admin_router
    app.include_router(admin_router)

    # ========== 人性化增强模块装配 ==========
    # 此时 CORE_INSTANCE / VOICE_MANAGER / DB_INSTANCE 均已就绪，可以安全装配。
    # 装配后启动后台主动行为引擎（傲娇催促/深夜催睡/主动来电等）。
    try:
        _enhance_state = _enhance_init(
            app=app,
            core=app_state.CORE_INSTANCE,
            voice_manager=app_state.VOICE_MANAGER,
            db=app_state.DB_INSTANCE,
            connection_manager=connection_manager,
            asr=None,  # 先用前端浏览器 ASR，后续可接后端 ASR
        )
        await _enhance_state["proactive"].start()
        _log.info("✅ 人性化增强模块已装配（傲娇人设 + 主动行为 + 通话 + 语音对话）")
    except Exception as e:
        _log.error(f"❌ 人性化增强模块装配失败: {e}", exc_info=True)

    # ========== 商业级增强模块装配 ==========
    # 初始化情感引擎、记忆RAG、网络爬虫、对话引擎
    try:
        from core.chat_engine import EnhancedChatEngine
        from core.emotion_engine import EnhancedEmotionEngine

        # 【已清理】core.proactive_engine 商业级版未启动即死代码，已移除。主动行为由 enhance 版负责。
        from core.emotion_persistence import EmotionPersistence
        from core.logging_config import setup_logging
        from core.memory_rag import MemoryRAG
        from core.web_crawler import WebCrawler
        from security.error_handler import setup_error_handlers

        # 日志配置
        setup_logging(level=os.environ.get('LOG_LEVEL', 'INFO'))
        _log.info("✅ 结构化日志已就绪")

        # 记录启动时间
        app_state.START_TIME = time.time()

        # 情感引擎
        app_state.EMOTION_ENGINE = EnhancedEmotionEngine()
        _log.info("✅ 四维情感引擎已就绪")

        # 情感持久化
        try:
            emotion_persistence = EmotionPersistence()
            app_state.EMOTION_PERSISTENCE = emotion_persistence
            _log.info("✅ 情感持久化已就绪")
        except Exception as e:
            _log.warning(f"情感持久化初始化失败（非关键）: {e}")

        # 记忆 RAG
        app_state.MEMORY_RAG = MemoryRAG(app_state.DB_INSTANCE)
        _log.info("✅ 四层记忆RAG已就绪")

        # 网络爬虫
        app_state.WEB_CRAWLER = WebCrawler()
        _log.info("✅ 多源网络爬虫已就绪")

        # 通话管理器：由 enhance.init_enhance 装配的 enhance 版 CallManager 统一提供
        # 【P0-2 修复】app_state.CALL_MANAGER 已在上方 _enhance_init 时注入 enhance 版实例，
        # 此处不再创建 core 版（无 LLM/TTS、事件格式与前端不兼容），避免双实例导致通话失效。
        _log.info("✅ 通话状态机复用 enhance 版 CallManager")

        # 【已清理】商业级 ProactiveEngine（core/proactive_engine.py）只创建未 start()，
        # 是死代码且与 enhance 版 ProactiveEngine（已启动）职责重叠，会造成主动消息双触发。
        # 主动行为统一由 enhance 版 ProactiveEngine 负责（上方 _enhance_state["proactive"].start()）。
        # app_state.PROACTIVE_ENGINE 保留为 None 占位，避免未知引用报错。

        # 对话引擎：连接现有 LLM + 情感 + 记忆 + 爬虫
        def _llm_caller(messages, model=None):
            return app_state.CORE_INSTANCE._safe_llm_call(messages, model)

        app_state.CHAT_ENGINE = EnhancedChatEngine(
            llm_caller=_llm_caller,
            emotion_engine=app_state.EMOTION_ENGINE,
            memory_rag=app_state.MEMORY_RAG,
            web_crawler=app_state.WEB_CRAWLER,
        )
        _log.info("✅ 增强对话引擎已就绪")

        # 注册全局异常处理器
        setup_error_handlers(app)
        _log.info("✅ 安全异常处理器已注册")

    except Exception as e:
        _log.error(f"❌ 商业级增强模块装配失败: {e}", exc_info=True)
        # 核心模块仍可工作，商业模块可选

    _log.info("喵艺服务器启动完成")

    # 【启动提示】检查晓晓在线女声所需代理，避免用户困惑"为什么没声音/音色不对"
    try:
        import os as _os
        _proxy = (_os.environ.get('HTTPS_PROXY') or _os.environ.get('HTTP_PROXY')
                  or _os.environ.get('https_proxy') or _os.environ.get('http_proxy'))
        _vm = app_state.VOICE_MANAGER
        _mode_name = _vm.get_voice_mode_name() if _vm else '未知'
        if '晓晓' in _mode_name and not _proxy:
            _log.warning(
                "⚠️ 当前语音模式为「晓晓女声」但未检测到 HTTPS_PROXY/HTTP_PROXY。"
                "微软语音服务对中国大陆 IP 有区域限制，晓晓将失败并自动降级为百度女声。"
                "如需晓晓：设置代理环境变量后重启（可运行 scripts/check_edge_tts.py 验证）。"
            )
        elif '云希' in _mode_name and not _proxy:
            _log.warning(
                "⚠️ 当前语音模式为「云希男声」但未检测到代理，将自动降级。"
                "如需在线男声：配置 HTTPS_PROXY 后重启。"
            )
    except Exception:
        pass  # 提示失败不影响启动

    yield

    _log.info("正在关闭喵艺服务器...")

    # 先停止主动行为引擎，避免关闭数据库时还在写消息
    try:
        from enhance.api_routes import _ENHANCE as _enhance_state_dict
        if _enhance_state_dict.get("proactive"):
            await _enhance_state_dict["proactive"].stop()
            _log.info("人性化增强模块已停止")
    except Exception as e:
        _log.warning(f"停止增强模块时出错: {e}")

    # 关闭商业级增强模块
    try:
        if app_state.WEB_CRAWLER:
            app_state.WEB_CRAWLER.shutdown()
            _log.info("网络爬虫已关闭")
        if app_state.MEMORY_RAG:
            app_state.MEMORY_RAG.shutdown()
            _log.info("记忆RAG已关闭")
        # 【已清理】商业级 PROACTIVE_ENGINE 已移除，主动行为由 enhance 版管理（上方已 stop）
        _log.info("商业级增强模块已关闭")
    except Exception as e:
        _log.warning(f"关闭商业模块时出错: {e}")

    if app_state.CORE_INSTANCE:
        app_state.CORE_INSTANCE.shutdown()

    if app_state.VOICE_MANAGER:
        app_state.VOICE_MANAGER.stop()

    if app_state.DB_INSTANCE:
        app_state.DB_INSTANCE.close()

    _log.info("喵艺服务器关闭完成")

# 环境标识（在 FastAPI 实例化前定义，用于控制 docs 暴露等安全行为）
IS_PRODUCTION = os.environ.get('ENVIRONMENT', 'development') == 'production'

# 生产环境安全铁律：禁用 API 文档端点，避免泄露 API 结构
# /docs、/redoc、/openapi.json 会暴露全部路由和参数定义，给攻击者绘制攻击地图
app = FastAPI(
    title="喵艺 AI Server",
    version="13.0",
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)
if IS_PRODUCTION:
    _log.info("[Security] 生产环境已禁用 /docs、/redoc、/openapi.json")

# ========== 人性化增强模块（傲娇人设 + 主动行为 + 真实通话 + 语音对话） ==========
# 仅注册路由；实例装配（init_enhance）在 lifespan 里 CORE/VOICE 实例就绪后调用。
from enhance.api_routes import init_enhance as _enhance_init
from enhance.api_routes import router as enhance_router

app.include_router(enhance_router, prefix="/api")

# ========== 商业级增强模块（SSE流式/情感引擎/记忆RAG/网络爬虫/安全防护） ==========
# 【已清理】commercial_routes 挂载在 /api/v1，但前端 BASE_URL='/api'，所有路由从未被访问（死代码）。
# 其天气/新闻/热搜功能与 enhance_api.py 的 /api/crawler/* 重复，聊天功能与 server.py /api/message 重复。
# 摘除路由挂载以减少启动开销和路由表体积；文件保留以备未来按需迁移到 /api 前缀。
# from enhance.commercial_routes import router as commercial_router
# app.include_router(commercial_router, prefix="/api/v1", tags=["commercial"])

# ========== 增强 API 路由（通话/主动行为/爬虫/系统/人设） ==========
from enhance.enhance_api import router as enhance_api_router

# 【修复】路由前缀从 /api/v1 改为 /api，与前端 callStore.ts 的 BASE_URL='/api' 对齐。
# 旧版前缀 /api/v1 导致前端调 /api/call/dial 返回 404，打电话功能完全失效。
app.include_router(enhance_api_router, prefix="/api", tags=["enhance"])

# 速率限制器 - 设置全局默认限制，未单独配置的端点也受保护
# 全局默认：每个 IP 每分钟最多 60 次请求
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["60/minute"],
    storage_uri="memory://",  # 内存存储，单进程足够；生产环境可用 redis://
    # 【Windows 兼容修复】slowapi 默认用 starlette Config 以系统编码(GBK)读取 .env，
    # 环境变量文件含非 GBK 字节（如中文/特殊符号）时抛 UnicodeDecodeError 导致启动失败。
    # 传空文件名跳过读取（环境变量已由上方 load_dotenv 加载到 os.environ）。
    config_filename='',
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 请求体大小限制中间件
@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    if request.method in ["POST", "PUT", "PATCH"]:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BODY_SIZE:
                    return JSONResponse(
                        status_code=413,
                        content=create_response(413, None, f"请求体太大了喵~ 最大支持 {MAX_REQUEST_BODY_SIZE // 1024}KB")
                    )
            except ValueError:
                pass
    return await call_next(request)

# 请求完整性验证中间件
@app.middleware("http")
async def verify_request_integrity(request: Request, call_next):
    if request.method in ["POST", "PUT", "PATCH"]:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type or "text/plain" in content_type:
            content_length = request.headers.get("content-length", "")
            if content_length:
                try:
                    if int(content_length) < 0:
                        return JSONResponse(
                            status_code=400,
                            content=create_response(400, None, "非法的 Content-Length 头"),
                        )
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content=create_response(400, None, "非法的 Content-Length 头"),
                    )
    return await call_next(request)

# 安全响应头中间件（唯一真相源：统一在此处附加所有安全头）
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    headers = get_security_headers()
    # 【CSP 修复 20260930】8001 开始托管前端页面后，原为纯 API 设计的严格 CSP 会拦截：
    #   - index.html 内联脚本（启动保护 / Service Worker 注册）→ script-src 'self' 直接阻断
    #   - Google Fonts 样式表与字体（style-src/font-src 不含 fonts.googleapis.com/gstatic.com）
    #   - TTS/录音 blob: 音频、base64 图片
    #   - Permissions-Policy microphone=() 会禁用语音通话的麦克风（5173 开发时页面来自 Vite 无此头，故未暴露）
    # 故对 HTML 文档响应改用与前端实际需求匹配的放宽版；API/JSON 响应维持严格 CSP。
    if response.headers.get('content-type', '').startswith('text/html'):
        headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: blob: https:; "
            "media-src 'self' blob:; "
            "worker-src 'self' blob:; "
            "connect-src 'self' ws: wss: http: https:;"
        )
        headers['Permissions-Policy'] = 'geolocation=(), microphone=(self), camera=(self)'
        # 【缓存修复 20260930】HTML 必须每次回源校验（ETag/304），防止浏览器启发式缓存
        # 陈旧 index.html——本次事故：用户 Edge 长期复用旧 bundle 与旧 CSP 头，普通刷新不失效。
        headers['Cache-Control'] = 'no-cache, must-revalidate'
    for key, value in headers.items():
        response.headers[key] = value
    # 生产环境启用 HSTS（仅在 HTTPS 下有意义；强制浏览器后续走 HTTPS）
    if IS_PRODUCTION:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response

BLOCKED_EXTENSIONS = ('.py', '.pyc', '.yaml', '.yml', '.env', '.db', '.sqlite', '.md', '.gitignore')

@app.middleware("http")
async def block_sensitive_files(request: Request, call_next):
    path = request.url.path
    
    if path.startswith('/api/') or path.startswith('/ws'):
        return await call_next(request)
    
    for ext in BLOCKED_EXTENSIONS:
        if path.lower().endswith(ext):
            _log.warning(f"阻止访问敏感文件: {path}")
            return JSONResponse(
                status_code=404,
                content=create_response(404, None, '文件不存在')
            )
    
    return await call_next(request)

import subprocess


def get_local_ip():
    try:
        # 注意：不能用 text=True，否则 subprocess 会在内部读取子线程中解码，
        # 中文 Windows 的 ipconfig 输出为 GBK 编码，UTF-8 mode 下会抛
        # UnicodeDecodeError，且该异常发生在子线程、主线程 try/except 无法捕获。
        # 改为获取 bytes 后在主线程显式解码，保证异常可控。
        result = subprocess.run(['ipconfig'], capture_output=True, timeout=5)
        try:
            stdout = result.stdout.decode('gbk')
        except UnicodeDecodeError:
            # 非中文 Windows 回退到 UTF-8（容错忽略无效字节）
            stdout = result.stdout.decode('utf-8', errors='ignore')
        for line in stdout.split('\n'):
            if 'IPv4' in line:
                parts = line.strip().split(':')
                if len(parts) > 1:
                    ip = parts[1].strip()
                    if ip and not ip.startswith('127.'):
                        return ip
    except Exception as e:
        # ipconfig 解析失败不影响启动，记录后返回 None
        _log.debug(f"[Network] 本地 IP 获取失败（不影响启动）: {e}")
    return None

LOCAL_IP = get_local_ip()
ALLOWED_ORIGINS_ENV = os.environ.get('ALLOWED_ORIGINS', '')

if IS_PRODUCTION:
    # 生产环境安全铁律：必须显式配置 ALLOWED_ORIGINS 白名单
    # 绝不允许回退到 '*'（配合 allow_credentials=True 会造成严重跨域漏洞）
    if not ALLOWED_ORIGINS_ENV.strip():
        raise RuntimeError(
            "【安全错误】生产环境必须配置 ALLOWED_ORIGINS 环境变量！\n"
            "请在 .env 中设置允许的前端来源，多个用逗号分隔，例如：\n"
            "ALLOWED_ORIGINS=https://your-domain.com,https://www.your-domain.com\n"
            "禁止使用 '*'（与 allow_credentials=True 组合会产生跨域凭证窃取漏洞）。"
        )
    ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS_ENV.split(',') if origin.strip()]
    # 二次校验：杜绝显式配置 '*' 的危险写法
    if '*' in ALLOWED_ORIGINS:
        raise RuntimeError(
            "【安全错误】生产环境 ALLOWED_ORIGINS 禁止使用 '*'！\n"
            "请改为显式的前端域名白名单。"
        )
    _log.info(f"[Security] 生产环境 CORS 白名单: {ALLOWED_ORIGINS}")
else:
    # 开发环境：本地常用端口 + 局域网 IP 的常用端口
    DEFAULT_DEV_ORIGINS = [
        'http://localhost:5173', 'http://localhost:5174', 'http://localhost:5175',
        'http://localhost:8000', 'http://localhost:8001',
        'http://127.0.0.1:5173', 'http://127.0.0.1:5174',
    ]
    env_origins = os.environ.get('ALLOWED_ORIGINS', '').strip()
    ALLOWED_ORIGINS = [o.strip() for o in env_origins.split(',') if o.strip()] if env_origins else list(DEFAULT_DEV_ORIGINS)
    # 合并默认列表
    for d in DEFAULT_DEV_ORIGINS:
        if d not in ALLOWED_ORIGINS:
            ALLOWED_ORIGINS.append(d)
    # 追加本机局域网 IP + 手机端开发常用端口
    if LOCAL_IP:
        # 手机端用 IP:Port 访问，覆盖 5170-5180（Vite 连续占用）、8000-8010（后端端口范围）
        for port in list(range(5170, 5181)) + list(range(8000, 8011)):
            allowed = f'http://{LOCAL_IP}:{port}'
            if allowed not in ALLOWED_ORIGINS:
                ALLOWED_ORIGINS.append(allowed)
        # Capacitor / Android WebView 使用的本地文件源：
        # 【APK 修复】androidScheme='https' 时 WebView 的 origin 是 https://localhost，
        # 缺少它会导致手机端所有请求被 CORS 拦截（"一堆错误"的主要根源）。
        for extra in ['capacitor://localhost', 'http://localhost', 'https://localhost', 'file://']:
            if extra not in ALLOWED_ORIGINS:
                ALLOWED_ORIGINS.append(extra)
        _log.info(f"[Security] 开发环境自动加入本机局域网 Origin: http://{LOCAL_IP}:*")
    _log.info(f"[Security] 当前 CORS 允许的来源 ({len(ALLOWED_ORIGINS)} 条)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=[
        "Authorization", "Content-Type", "X-Requested-With", "x-api-key",
        "Accept", "Accept-Language", "Origin", "User-Agent", "DNT", "Cache-Control", "X-File-Name",
        # 【APK 跨域修复 2026-10-01】App 端防重放签名头：浏览器同源不触发预检，
        # 仅 APK（http://localhost）跨域时预检会因这些头被拒 → 400 Disallowed CORS headers
        "X-Device-Id", "X-Nonce", "X-Signature", "X-Timestamp",
    ],
    # 暴露前端 WebSocket/流式响应需要的响应头
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    max_age=600,  # 预检缓存 10 分钟，减少 OPTIONS 请求
)

# （安全响应头已由上方 add_security_headers 中间件统一设置，此处不再重复）

@app.middleware("http")
async def global_exception_handler(request: Request, call_next):
    try:
        return await call_next(request)
    except HTTPException as e:
        # 429 限流等 HTTP 异常保持原样
        if e.status_code == 429:
            return JSONResponse(
                status_code=429,
                content=create_response(429, None, '喵～请求太频繁了，让本喵喘口气再试吧~')
            )
        return JSONResponse(
            status_code=e.status_code,
            content=create_response(e.status_code, None, str(e.detail))
        )
    except RateLimitExceeded:
        # slowapi 限流异常：返回友好的 429 猫语消息
        return JSONResponse(
            status_code=429,
            content=create_response(429, None, '喵～请求太频繁了，让本喵喘口气再试吧~')
        )
    except Exception as e:
        # 记录详细错误日志（但脱敏处理）
        error_msg = str(e)
        # 脱敏：移除可能的敏感信息
        import re
        error_msg = re.sub(r'token[=:]\S*', 'token=***', error_msg, flags=re.IGNORECASE)
        error_msg = re.sub(r'password[=:]\S*', 'password=***', error_msg, flags=re.IGNORECASE)
        _log.error(f"未捕获异常: {error_msg}", exc_info=True)
        # 友好的猫语错误消息，保护用户体验
        return JSONResponse(
            status_code=500,
            content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~')
        )

# ========== 路由注册 ==========
# 模块化路由（逐步从 server.py 拆分）
# 注意：当前原有端点仍在此文件中，新路由模块作为示例参考
# 待迁移完成后，原有端点将被删除

# from routes.auth import router as auth_router
# from routes.chat import router as chat_router
# from routes.system import router as system_router
# app.include_router(auth_router, prefix="", tags=["认证"])
# app.include_router(chat_router, prefix="", tags=["聊天"])
# app.include_router(system_router, prefix="", tags=["系统"])

# ========== 原有端点（待迁移） ==========

# ========== 前端构建产物托管（8001 同源访问兜底通路） ==========
# 背景：Vite dev(5173) 在部分浏览器环境（代理/TUN 接管回环流量）下不可达，
# 后端直接托管 mobile-chat/dist 作为兜底；前端 API/WS 默认相对路径
# （config.ts: '/api'、'/ws'），从 8001 打开即同源直连，前端零改动。
# 【目录标准化】mobile-chat → frontend、web → site；_resolve_dir 已定义在文件顶部
_MOBILE_DIST = _resolve_dir('frontend/dist', 'mobile-chat/dist')
_WEB_DIR = _resolve_dir('site', 'web')
_MOBILE_INDEX = os.path.join(_MOBILE_DIST, 'index.html')
if os.path.isdir(os.path.join(_MOBILE_DIST, 'assets')):
    app.mount("/assets", StaticFiles(directory=os.path.join(_MOBILE_DIST, 'assets')), name="mobile_assets")
    for _sub in ('miaoyi', 'vad'):
        if os.path.isdir(os.path.join(_MOBILE_DIST, _sub)):
            app.mount(f"/{_sub}", StaticFiles(directory=os.path.join(_MOBILE_DIST, _sub)), name=f"mobile_{_sub}")

    @app.get("/manifest.json")
    async def pwa_manifest():
        return FileResponse(os.path.join(_MOBILE_DIST, 'manifest.json'))

    @app.get("/service-worker.js")
    async def pwa_service_worker():
        return FileResponse(os.path.join(_MOBILE_DIST, 'service-worker.js'),
                            media_type='application/javascript')

    @app.get("/miaoyi-character.png")
    async def miaoyi_character_png():
        return FileResponse(os.path.join(_MOBILE_DIST, 'miaoyi-character.png'))

    @app.get("/icon-192.png")
    async def icon_192_png():
        return FileResponse(os.path.join(_MOBILE_DIST, 'icon-192.png'))

    @app.get("/icon-512.png")
    async def icon_512_png():
        return FileResponse(os.path.join(_MOBILE_DIST, 'icon-512.png'))


# ========== APK 下载（Android App 更新分发） ==========
_DOWNLOADS_DIR = os.path.join(PROJECT_ROOT, 'downloads')
_LATEST_APK = os.path.join(_DOWNLOADS_DIR, 'miaoyi-latest.apk')
_VERSION_MANIFEST = os.path.join(_DOWNLOADS_DIR, 'version.json')

# 清单缺失时的兜底（语义：无可用更新）
_DEFAULT_VERSION_MANIFEST = {
    'latest_version_code': 0,
    'latest_version_name': '',
    'min_supported_version_code': 0,
    'update_title': '发现新版本',
    'release_notes': [],
    'published_at': '',
}


def _load_version_manifest() -> dict:
    """读取最新版本清单（单文件即"当前唯一活跃版本"，最新版判定以 version_code 为准）"""
    try:
        with open(_VERSION_MANIFEST, 'r', encoding='utf-8') as f:
            data = json.load(f)
        merged = dict(_DEFAULT_VERSION_MANIFEST)
        merged.update(data or {})
        return merged
    except Exception:
        return dict(_DEFAULT_VERSION_MANIFEST)


@app.get("/api/version/check")
async def check_app_version(
    platform: str = 'android',
    version_code: int = 0,
):
    """App 启动检查更新（只读、无需登录，便于登录前检查）。
    判定规则：latest_version_code > 客户端 version_code 即有更新；
    客户端低于 min_supported_version_code 为强制更新。"""
    manifest = _load_version_manifest()
    latest_code = int(manifest.get('latest_version_code') or 0)
    min_supported = int(manifest.get('min_supported_version_code') or 0)
    has_update = latest_code > version_code
    force_update = has_update and min_supported > 0 and version_code < min_supported

    file_size = 0
    try:
        if os.path.isfile(_LATEST_APK):
            file_size = os.path.getsize(_LATEST_APK)
    except Exception:
        pass

    return create_response(200, {
        'platform': platform,
        'latest_version_code': latest_code,
        'latest_version_name': manifest.get('latest_version_name', ''),
        'current_version_code': version_code,
        'has_update': has_update,
        'force_update': force_update,
        'min_supported_version_code': min_supported,
        'update_title': manifest.get('update_title', '发现新版本'),
        'release_notes': manifest.get('release_notes', []),
        'download_url': '/download/latest',
        'file_size': file_size,
        'published_at': manifest.get('published_at', ''),
    }, 'ok')


@app.api_route("/download/latest", methods=["GET", "HEAD"])
@limiter.limit("10/minute")
async def download_latest_apk(request: Request):
    """稳定的最新版 APK 下载地址（供 Android 用户更新）。
    地址固定不变，每次发布只需用新包覆盖 downloads/miaoyi-latest.apk。
    【P2加固】5MB+ 大文件匿名可拉，按 IP 限流防刷流量（10 次/分钟）。"""
    if not os.path.isfile(_LATEST_APK):
        return JSONResponse(
            {"code": 404, "message": "安装包暂不可用，请稍后再试"}, status_code=404)
    return FileResponse(
        _LATEST_APK,
        media_type='application/vnd.android.package-archive',
        filename='miaoyi-latest.apk',
    )


@app.get("/")
async def root():
    """后端状态页 / 前端托管页。
    【修复】旧版返回 web/index.html（写死跳转 http://localhost:5173）——
    手机浏览器打开 http://IP:8001/ 会被跳到手机上不存在的 localhost，
    误显示为“连接被该站点断开”。改为内联状态页，不再跳转。
    【托管】mobile-chat/dist 已构建时直接返回前端产物（同源零跨域），
    dist 未构建时回退内联状态页。
    """
    if os.path.isfile(_MOBILE_INDEX):
        return FileResponse(_MOBILE_INDEX)
    html = (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>喵艺后端</title>"
        "<style>body{font-family:system-ui,sans-serif;background:#f3e5f5;display:flex;"
        "align-items:center;justify-content:center;height:100vh;margin:0}"
        ".card{background:#fff;border-radius:16px;padding:32px 40px;"
        "box-shadow:0 8px 24px rgba(0,0,0,.08);text-align:center}"
        ".ok{font-size:40px}h1{margin:8px 0;color:#5e35b1}"
        "p{color:#666;margin:6px 0}code{background:#f3e5f5;padding:2px 8px;"
        "border-radius:6px;color:#5e35b1}</style></head>"
        "<body><div class='card'><div class='ok'>🐱</div>"
        "<h1>喵艺后端运行中</h1>"
        "<p>API: <code>/api</code>　WebSocket: <code>/ws</code></p>"
        "</div></body></html>"
    )
    return Response(content=html, media_type="text/html; charset=utf-8")

@app.get("/styles.css")
async def styles():
    return FileResponse(os.path.join(_WEB_DIR, 'styles.css'))

@app.get("/app.js")
async def app_js():
    return FileResponse(os.path.join(_WEB_DIR, 'app.js'))

@app.get("/@vite/client")
async def vite_client():
    return {"status": "ok"}, 204

@app.get("/privacy")
async def privacy():
    return FileResponse(os.path.join(_WEB_DIR, 'privacy.html'))

@app.get("/terms")
async def terms():
    return FileResponse(os.path.join(_WEB_DIR, 'terms.html'))

@app.api_route("/api/health", methods=["GET", "HEAD"])
async def health_check(request: Request):
    # 【修复】支持 HEAD 方法：前端 useOnlineStatus 用 HEAD 检测网络，
    # 旧版只注册 @app.get 导致 405，前端误判为"网络被猫咬断了"。
    # HEAD 请求不会返回 body，但状态码 200 即可让前端判定在线。
    if request.method == "HEAD":
        return Response(status_code=200)
    return create_response(200, {
        'status': 'healthy',
        'version': config_manager.get('app.version'),
        'timestamp': datetime.now().isoformat(),
    })

# 【商业化】每日对话限额（免费用户50条/天，成本控制+商业化铺垫）
_daily_msg_count: dict = {}  # {user_id: {date_str: count}}，内存态，跨天自动清零
FREE_DAILY_LIMIT = 50

@app.get("/api/auth/config")
async def auth_config():
    """公开配置：登录/注册页初始化用，告诉客户端当前是否强制邀请码"""
    invite_required = bool(os.environ.get('INVITE_CODE', '').strip())
    return JSONResponse(status_code=200, content=create_response(200, {
        'invite_code_required': invite_required,
    }, 'ok'))

@app.post("/api/auth/register")
@limiter.limit("5/minute")
async def register(body: RegisterRequest, request: Request):

    # 【内测防滥用】邀请码校验：仅当 .env 配置了 INVITE_CODE 时强制（未配置则开放注册，便于本地开发）
    expected_invite = os.environ.get('INVITE_CODE', '').strip()
    if expected_invite and (body.invite_code or '').strip() != expected_invite:
        _log.warning(f"注册被拒：邀请码无效 (username={body.username})")
        return JSONResponse(status_code=403, content=create_response(403, None, '邀请码无效，请向管理员获取有效邀请码'))

    # 输入安全过滤
    username = sanitize_input(body.username, max_length=50)
    phone = sanitize_input(body.phone, max_length=20)
    email = sanitize_input(body.email or '', max_length=100)
    password = body.password
    
    if len(username) < 3 or len(username) > 50:
        return JSONResponse(status_code=400, content=create_response(400, None, '用户名长度应在3-50个字符之间'))
    
    if not re.match(r'^1[3-9]\d{9}$', phone):
        return JSONResponse(status_code=400, content=create_response(400, None, '请输入正确的中国大陆手机号（11位数字，以1开头）'))
    
    # 使用 auth 模块验证密码强度
    strength_ok, strength_msg = validate_password_strength(password)
    if not strength_ok:
        return JSONResponse(status_code=400, content=create_response(400, None, strength_msg))
    
    existing_user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_username, username)
    # 【防用户枚举】用户名/手机号冲突统一为同一模糊提示（与登录文案策略一致），
    # 不让探测者区分"哪个字段已存在"；用户侧仍然可理解要换一个重试。
    existing_user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_username, username)
    if existing_user:
        return JSONResponse(status_code=400, content=create_response(400, None, '这个用户名或手机号已经被使用啦，换一个试试喵～'))

    existing_phone = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_phone, phone)
    if existing_phone:
        return JSONResponse(status_code=400, content=create_response(400, None, '这个用户名或手机号已经被使用啦，换一个试试喵～'))
    
    # 使用 auth 模块哈希密码
    password_hash = hash_password(password)
    _log.info(f"注册参数: username={username}, phone={phone}, email={email}")
    success = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.create_user, username, password_hash, phone, email)
    
    if success:
        user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_username, username)
        access_token, refresh_token = generate_token(user['id'], user['username'])
        return JSONResponse(status_code=201, content=create_response(201, {
            'user_id': user['id'],
            'username': user['username'],
            'phone': user.get('phone', ''),
            'token': access_token,
            'access_token': access_token,
            'refresh_token': refresh_token,
        }, '注册成功'))
    else:
        return JSONResponse(status_code=500, content=create_response(500, None, '注册失败'))

@app.post("/api/auth/login")
@limiter.limit("10/minute")
async def login(body: LoginRequest, request: Request):
    
    # 识别用户标识（用户名或手机号）
    identifier = body.username
    user = None
    if re.match(r'^1[3-9]\d{9}$', identifier):
        user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_phone, identifier)
    else:
        user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_username, identifier)
    
    # 检查登录尝试次数（防暴力破解）
    attempts_ok, attempts_msg = check_login_attempts(identifier)
    if not attempts_ok:
        _log.warning(f"登录被拒：账号锁定中 (identifier={identifier}, msg={attempts_msg})")
        return JSONResponse(status_code=429, content=create_response(429, None, attempts_msg))
    
    if not user:
        record_login_attempt(identifier, False)
        return JSONResponse(status_code=401, content=create_response(401, None, '用户名或密码错误'))

    # 账号被禁用时拒绝登录
    if user.get('status') == 'disabled':
        record_login_attempt(identifier, False)
        return JSONResponse(status_code=403, content=create_response(403, None, '账号已被禁用，请联系管理员'))
    
    # 使用 auth 模块验证密码
    if not verify_password(body.password, user['password_hash']):
        record_login_attempt(identifier, False)
        return JSONResponse(status_code=401, content=create_response(401, None, '用户名或密码错误'))
    
    # 登录成功，清除登录尝试记录
    record_login_attempt(identifier, True)
    
    await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.update_user_login, user['id'])
    access_token, refresh_token = generate_token(user['id'], user['username'], user.get('role', 'user'))
    
    response = JSONResponse(content=create_response(200, {
        'user_id': user['id'],
        'username': user['username'],
        'phone': user.get('phone', ''),
        'email': user.get('email', ''),
        'role': user.get('role', 'user'),
        'token': access_token,
        'access_token': access_token,
        'refresh_token': refresh_token,
    }, '登录成功'))
    
    is_production = os.environ.get('ENVIRONMENT', 'development') == 'production'
    request_host = request.headers.get('host', '')
    is_local = any(kw in request_host for kw in ['localhost', '127.0.0.1'])

    # 【P1-9 修复】开发环境（非 HTTPS）下，mobile 端使用 HTTP 访问时，
    # secure cookie 不会被浏览器发送。统一策略：
    #   - 开发环境（is_local=True）: secure=False, samesite='lax'
    #   - 生产环境 HTTPS（is_local=False）: secure=True, samesite='none'
    # 这样 cookie 在开发和生产环境都能正常工作。
    secure = not is_local
    samesite_policy = 'none' if not is_local else 'lax'

    # 同时保留 cookie 写入，兼容旧客户端
    response.set_cookie(
        key='miaoyi_token',
        value=access_token,
        httponly=True,
        secure=secure,
        samesite=samesite_policy,
        max_age=3600 * 24 * 7,
        path='/'
    )
    
    return response

@app.post("/api/auth/refresh")
@limiter.limit("20/minute")
async def refresh_token_endpoint(body: dict[str, Any], request: Request):
    """刷新 Access Token"""
    refresh_token_str = body.get('refresh_token', '')
    if not refresh_token_str:
        # 尝试从 Authorization header 获取
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            refresh_token_str = auth_header[7:]
    
    if not refresh_token_str:
        return JSONResponse(status_code=400, content=create_response(400, None, '缺少 refresh token'))
    
    result = refresh_access_token(refresh_token_str)
    if not result:
        return JSONResponse(status_code=401, content=create_response(401, None, 'Refresh Token 无效或已过期'))
    
    return create_response(200, {
        'access_token': result['access_token'],
        'refresh_token': result.get('refresh_token', ''),
        'user_id': result['user_id'],
        'username': result['username'],
    })

@app.post("/api/auth/logout")
@rate_limit(requests=10, period=60, identifier="user")
async def logout_endpoint(current_user: dict = Depends(get_current_user)):
    """登出并撤销 Token"""
    user_id = current_user['id']
    revoke_refresh_token(user_id)
    return create_response(200, None, '已登出')

@app.get("/api/user/profile")
@rate_limit(requests=60, period=60, identifier="user")
async def get_user_profile(current_user: dict = Depends(get_current_user)):
    try:
        user_id = current_user['id']
        user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_id, user_id)
        if user:
            return create_response(200, {
                'id': user['id'],
                'username': user['username'],
                'phone': user.get('phone', ''),
                'email': user.get('email', ''),
                'nickname': user.get('nickname', ''),
                'role': user.get('role', 'user'),
                'created_at': user.get('created_ts'),
                'last_login': user.get('last_login_ts'),
            })
        else:
            return JSONResponse(status_code=404, content=create_response(404, None, '用户不存在'))
    except Exception as e:
        _log.error(f"获取用户信息失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.delete("/api/user/profile")
@rate_limit(requests=5, period=60, identifier="user")
async def clear_user_profile(current_user: dict = Depends(get_current_user)):
    try:
        user_id = current_user['id']
        success = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.clear_user_profile, user_id)
        if success:
            return create_response(200, None, '用户画像已清除')
        else:
            return JSONResponse(status_code=500, content=create_response(500, None, '清除失败'))
    except Exception as e:
        _log.error(f"清除用户画像失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

class UpdateNicknameRequest(BaseModel):
    nickname: str

class UpdatePasswordRequest(BaseModel):
    old_password: str
    new_password: str

@app.put("/api/user/password")
@rate_limit(requests=5, period=300, identifier="user")
async def update_user_password(body: UpdatePasswordRequest, request: Request, current_user: dict = Depends(get_current_user)):
    try:
        user_id = current_user['id']
        user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_id, user_id)
        
        if not verify_password(body.old_password, user['password_hash']):
            return JSONResponse(status_code=400, content=create_response(400, None, '原密码错误'))
        
        # 使用 auth 模块验证密码强度
        strength_ok, strength_msg = validate_password_strength(body.new_password)
        if not strength_ok:
            return JSONResponse(status_code=400, content=create_response(400, None, strength_msg))
        
        new_hash = hash_password(body.new_password)
        await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.update_user_password, user_id, new_hash)

        # 【安全】密码修改后撤销所有旧 refresh token，强制其他设备重新登录
        await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.revoke_all_user_refresh_tokens, user_id)

        return create_response(200, None, '密码修改成功，其他设备需重新登录')
    except Exception as e:
        _log.error(f"修改密码失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.put("/api/user/nickname")
@rate_limit(requests=30, period=60, identifier="user")
async def update_user_nickname(body: UpdateNicknameRequest, request: Request, current_user: dict = Depends(get_current_user)):
    try:
        user_id = current_user['id']
        await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.update_user_profile, user_id, None, None, body.nickname)
        return create_response(200, None, '昵称更新成功')
    except Exception as e:
        _log.error(f"更新昵称失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/user/checkin")
async def user_checkin(current_user: dict = Depends(get_current_user)):
    try:
        user_id = str(current_user['id'])
        
        result = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.checkin_user, user_id)
        
        if result.get('already_checked'):
            return create_response(400, None, '今天已经签到过了哦～')
        
        if app_state.CORE_INSTANCE._emotional_core:
            app_state.CORE_INSTANCE._emotional_core.update_intimacy(5)
        
        return create_response(200, {'streak': result['streak']}, '签到成功！')
    except Exception as e:
        _log.error(f"签到失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.get("/api/onboarding")
async def get_onboarding(current_user: dict = Depends(get_current_user)):
    try:
        user_id = str(current_user['id'])
        
        message_count = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, 
            app_state.DB_INSTANCE.get_message_count, 
            user_id
        )
        
        is_first_time = message_count == 0
        
        if is_first_time:
            options = [
                {
                    'id': 'nickname',
                    'title': '🐾 帮你起个猫咪外号',
                    'description': '让我给你起一个专属的猫咪称呼喵～',
                    'trigger_text': '帮我起个猫咪外号',
                },
                {
                    'id': 'mood',
                    'title': '🎭 猜猜你今天是什么心情',
                    'description': '我来猜猜你的心情，准不准看缘分喵～',
                    'trigger_text': '猜猜我今天是什么心情',
                },
                {
                    'id': 'secret',
                    'title': '🎁 讲一个只有猫才知道的秘密',
                    'description': '这是我藏了很久的秘密哦，只告诉你喵～',
                    'trigger_text': '讲一个只有猫才知道的秘密',
                },
            ]
            
            return create_response(200, {
                'is_first_time': True,
                'greeting': '*喵~ 你是第一个见到我的人类！作为见面礼，我可以满足你三个愿望：*',
                'options': options,
            })
        else:
            return create_response(200, {
                'is_first_time': False,
                'greeting': '',
                'options': [],
            })
    except Exception as e:
        _log.error(f"获取入职信息失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


_daily_fragment_cache = {}
_daily_fragment_date = None


@app.get("/api/daily_fragment")
async def get_daily_fragment(current_user: dict = Depends(get_current_user)):
    global _daily_fragment_cache, _daily_fragment_date

    try:
        today = datetime.now().strftime('%Y-%m-%d')

        if _daily_fragment_date == today and _daily_fragment_cache.get('fragment'):
            return create_response(200, _daily_fragment_cache)

        # 降级模板（LLM失败时用）
        fragments = [
            '今天出门看到一只三花猫，我们互相瞪了很久，最后它先眨眼了喵！',
            '早上醒来发现尾巴被自己压住了，挣扎了好久才挣脱喵～',
            '今天学到了一个新词汇："小鱼干雨"，听起来就很幸福喵～',
            '刚才在窗台晒太阳的时候，发现楼下的流浪猫在开秘密会议喵！',
            '今天尝试用爪子抓蝴蝶，结果抓到了一片叶子，假装那就是蝴蝶喵～',
            '午睡的时候做了一个梦，梦里全是小鱼干，醒来发现口水滴在了键盘上喵…',
            '今天遇到了一只特别胖的橘猫，它告诉我减肥是不可能的，毕竟猫生苦短喵～',
            '刚才练习了一下伸懒腰的姿势，觉得自己帅爆了喵！',
            '今天观察了人类使用电脑的方式，觉得他们的手指好灵活喵～',
            '发现了一个新玩法：把毛线球推到沙发底下，然后假装找不到喵～',
        ]

        import random
        fragment = random.choice(fragments)

        # 【后端优化】尝试用LLM生成独特碎片，失败降级到模板
        try:
            weekday = ['一','二','三','四','五','六','日'][datetime.now().weekday()]
            prompt = f"用猫娘的口吻写一句简短的'猫生碎片'日记（20-50字），要求：日常生活、可爱、有画面感、每次不重复。今天是星期{weekday}。只输出日记内容本身，不要加引号或前缀。"
            llm_fragment = await asyncio.get_event_loop().run_in_executor(
                GLOBAL_THREAD_POOL,
                app_state.CORE_INSTANCE._safe_llm_call,
                [{"role": "system", "content": "你是喵艺，一只傲娇猫娘。用一句话记录今天的猫生碎片。"},
                 {"role": "user", "content": prompt}]
            )
            if llm_fragment and len(llm_fragment.strip()) > 5:
                fragment = llm_fragment.strip().strip('"\'')
        except Exception as llm_err:
            _log.debug(f"LLM生成碎片降级到模板: {llm_err}")

        _daily_fragment_date = today
        _daily_fragment_cache = {
            'fragment': fragment,
            'date': today,
        }

        return create_response(200, _daily_fragment_cache)
    except Exception as e:
        _log.error(f"获取每日碎片失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.post("/api/message")
@limiter.limit("30/minute")
async def process_message(body: dict[str, Any], request: Request, current_user: dict = Depends(get_current_user)):
    try:
        user_id = str(current_user['id'])
        text = body.get('text', '')
        context = body.get('context', '')
        
        # 输入安全过滤 - 防止Prompt注入和XSS
        original_text = text
        text = sanitize_input(text, max_length=5000)
        if context:
            context = sanitize_input(context, max_length=2000)
        
        # 检查过滤前后是否有内容丢失（异常输入被完全过滤）
        if original_text and not text:
            return JSONResponse(status_code=400, content=create_response(400, None, '消息内容包含不允许的字符'))
        
        if not text:
            return JSONResponse(status_code=400, content=create_response(400, None, '消息内容不能为空'))

        # 【商业化】每日对话限额检查（免费用户50条/天）
        _today = datetime.now().strftime('%Y-%m-%d')
        if user_id not in _daily_msg_count:
            _daily_msg_count[user_id] = {}
        _used = _daily_msg_count[user_id].get(_today, 0)
        if _used >= FREE_DAILY_LIMIT:
            return JSONResponse(status_code=429, content=create_response(
                429, {'limit': FREE_DAILY_LIMIT, 'used': _used, 'reset_at': '次日00:00'},
                f'今日对话次数已达上限（{FREE_DAILY_LIMIT}条/天），明天再来找喵艺聊天吧～喵💕'))

        result = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.CORE_INSTANCE.process_message, user_id, text, context)

        # 【P0 修复·数据流统一】REST 路径持久化（Service 层）
        try:
            _reply = result.get('text', '')
            _emo = result.get('emotional_state', {}) or {}
            _emo_name = _emo.get('dominant_emotion', '') or _emo.get('dominant', '') if isinstance(_emo, dict) else ''
            await asyncio.get_event_loop().run_in_executor(
                GLOBAL_THREAD_POOL,
                app_state.MESSAGE_SERVICE.save_message_pair,
                user_id, text, _reply, str(_emo_name),
                int(result.get('response_time_ms', 0) or 0)
            )
        except Exception as e:
            _log.error(f"REST保存消息失败: {e}")

        # 限额计数+1（仅在成功调用后计数）
        _daily_msg_count[user_id][_today] = _used + 1

        return create_response(200, {
            'text': result.get('text', ''),
            'sticker': result.get('sticker'),
            'thought': result.get('thought'),
            'small_thought': result.get('small_thought'),
            'emotional_state': result.get('emotional_state', {}),
            'thinking_log': result.get('thinking_log', []),
            'memory_triggers': result.get('memory_triggers', []),
            'autobiography_context': result.get('autobiography_context', ''),
            'response_time_ms': result.get('response_time_ms', 0),
            'status': result.get('status', 'success'),
            'timestamp': datetime.now().isoformat(),
        })
    
    except Exception as e:
        _log.error(f"处理消息失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


MSG_ID_CACHE = {}
MSG_ID_CACHE_TTL = 300

METRICS = {
    'total_requests': 0,
    'successful_requests': 0,
    'failed_requests': 0,
    'stream_requests': 0,
    'avg_response_time_ms': 0,
    'p50_response_time_ms': [],
    'p95_response_time_ms': [],
    'p99_response_time_ms': [],
    'first_token_latencies': [],
    'websocket_connections': 0,
    'websocket_messages': 0,
    'model_distribution': {
        'glm-4-flash': 0,
        'glm-4': 0,
        'other': 0,
    },
}

def record_metric(metric: str, value: float = None):
    if metric == 'total_requests':
        METRICS['total_requests'] += 1
    elif metric == 'successful_requests':
        METRICS['successful_requests'] += 1
    elif metric == 'failed_requests':
        METRICS['failed_requests'] += 1
    elif metric == 'stream_requests':
        METRICS['stream_requests'] += 1
    elif metric == 'response_time_ms' and value:
        METRICS['avg_response_time_ms'] = (
            METRICS['avg_response_time_ms'] * (METRICS['total_requests'] - 1) + value
        ) / METRICS['total_requests']
        METRICS['p50_response_time_ms'].append(value)
        METRICS['p50_response_time_ms'] = METRICS['p50_response_time_ms'][-100:]
        METRICS['p95_response_time_ms'].append(value)
        METRICS['p95_response_time_ms'] = METRICS['p95_response_time_ms'][-100:]
        METRICS['p99_response_time_ms'].append(value)
        METRICS['p99_response_time_ms'] = METRICS['p99_response_time_ms'][-100:]
    elif metric == 'first_token_latency_ms' and value:
        METRICS['first_token_latencies'].append(value)
        METRICS['first_token_latencies'] = METRICS['first_token_latencies'][-50:]
    elif metric == 'websocket_connection':
        METRICS['websocket_connections'] += 1
    elif metric == 'websocket_message':
        METRICS['websocket_messages'] += 1
    elif metric == 'model' and value:
        model_name = str(value)
        if model_name in METRICS['model_distribution']:
            METRICS['model_distribution'][model_name] += 1
        else:
            METRICS['model_distribution']['other'] += 1

def get_metrics_summary():
    p50 = sorted(METRICS['p50_response_time_ms'])
    p95 = sorted(METRICS['p95_response_time_ms'])
    p99 = sorted(METRICS['p99_response_time_ms'])
    first_token = METRICS['first_token_latencies']
    
    success_rate = 0
    if METRICS['total_requests'] > 0:
        success_rate = METRICS['successful_requests'] / METRICS['total_requests']
    
    p50_val = p50[len(p50) // 2] if p50 else 0
    p95_val = p95[int(len(p95) * 0.95)] if p95 else 0
    p99_val = p99[int(len(p99) * 0.99)] if p99 else 0
    avg_first_token = sum(first_token) / len(first_token) if first_token else 0
    
    ws_metrics = connection_manager.get_metrics()
    
    return {
        'total_requests': METRICS['total_requests'],
        'successful_requests': METRICS['successful_requests'],
        'failed_requests': METRICS['failed_requests'],
        'success_rate': success_rate,
        'stream_requests': METRICS['stream_requests'],
        'avg_response_time_ms': round(METRICS['avg_response_time_ms'], 2),
        'p50_response_time_ms': p50_val,
        'p95_response_time_ms': p95_val,
        'p99_response_time_ms': p99_val,
        'avg_first_token_latency_ms': avg_first_token,
        'websocket': ws_metrics['websocket'],
        'heartbeat': ws_metrics['heartbeat'],
        'message': ws_metrics['message'],
        'search': ws_metrics['search'],
        'model_distribution': METRICS['model_distribution'],
    }

def is_duplicate_message(client_msg_id: str) -> bool:
    if not client_msg_id:
        return False
    if client_msg_id in MSG_ID_CACHE:
        return True
    MSG_ID_CACHE[client_msg_id] = time.time()
    return False

def cleanup_msg_id_cache():
    now = time.time()
    keys_to_remove = [k for k, v in MSG_ID_CACHE.items() if now - v > MSG_ID_CACHE_TTL]
    for k in keys_to_remove:
        del MSG_ID_CACHE[k]

@app.post("/api/message/stream")
@limiter.limit("20/minute")
async def message_stream(body: dict[str, Any], request: Request, current_user: dict = Depends(get_current_user)):
    user_id = str(current_user['id'])
    text = body.get('text', '')
    context = body.get('context', '')
    client_msg_id = body.get('client_msg_id', '')
    
    # 输入安全过滤
    original_text = text
    text = sanitize_input(text, max_length=5000)
    if context:
        context = sanitize_input(context, max_length=2000)
    
    if original_text and not text:
        return JSONResponse(status_code=400, content=create_response(400, None, '消息内容包含不允许的字符'))
    
    if not text:
        return JSONResponse(status_code=400, content=create_response(400, None, '消息内容不能为空'))
    
    if is_duplicate_message(client_msg_id):
        return JSONResponse(status_code=409, content=create_response(409, None, '消息已处理'))
    
    async def event_generator():
        try:
            status_updates = []
            last_status = None
            
            def status_callback(status, detail=""):
                nonlocal last_status
                status_messages = {
                    'thinking': '🤔 正在分析情感...',
                    'searching': '🔍 正在搜索网络知识...',
                    'verifying': '✅ 正在核实信息...',
                    'typing': '⌨️ 正在组织语言...',
                }
                content = detail or status_messages.get(status, '')
                if content and content != last_status:
                    status_updates.append({
                        'type': 'thinking_status',
                        'content': content,
                        'status': status,
                    })
                    last_status = content
            
            def process_stream():
                return app_state.CORE_INSTANCE.process_message_stream(user_id, text, context, status_callback)
            
            stream = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, process_stream)
            
            yield f"data: {json.dumps({'type': 'typing', 'status': 'typing'})}\n\n"
            
            # 【bug修复】status_callback 在生成器被迭代时才触发，若在迭代前批量 yield
            # status_updates 将永远为空，导致"思考中/搜索中/核实中"状态丢失。
            # 改为在迭代 stream 的过程中同步取出已累积的状态更新。
            for chunk in stream:
                while status_updates:
                    yield f"data: {json.dumps(status_updates.pop(0))}\n\n"
                yield f"data: {json.dumps(chunk)}\n\n"
            
            # 收尾：flush 剩余的状态更新
            while status_updates:
                yield f"data: {json.dumps(status_updates.pop(0))}\n\n"
            
            yield f"data: {json.dumps({'type': 'typing', 'status': 'done'})}\n\n"
            
        except Exception as e:
            _log.error(f"SSE流式处理异常: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/message/history")
@limiter.limit("60/minute")
async def get_message_history(
    request: Request,
    limit: int = 20,
    before_message_id: str = None,
    current_user: dict = Depends(get_current_user)
):
    user_id = str(current_user['id'])
    
    try:
        messages = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL,
            app_state.DB_INSTANCE.get_user_messages,
            user_id,
            limit,
            before_message_id
        )
        
        has_more = len(messages) >= limit
        
        return create_response(200, {
            'messages': messages,
            'has_more': has_more,
            'count': len(messages),
        })
    except Exception as e:
        _log.error(f"获取消息历史失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/metrics")
async def get_metrics(current_user: dict = Depends(get_current_user)):
    if current_user['role'] != 'admin':
        return JSONResponse(status_code=403, content=create_response(403, None, '权限不足'))
    return create_response(200, get_metrics_summary())

FLASH_FREE_LIMIT = 1000000
USAGE_WARNING_THRESHOLD = 80
USAGE_CRITICAL_THRESHOLD = 95

def check_usage_limit() -> bool:
    total_tokens_today = METRICS.get('stream_requests', 0) * 50
    today_usage_percent = (total_tokens_today / FLASH_FREE_LIMIT) * 100
    return today_usage_percent >= USAGE_CRITICAL_THRESHOLD

@app.get("/api/usage")
async def get_usage(current_user: dict = Depends(get_current_user)):
    if current_user['role'] != 'admin':
        return JSONResponse(status_code=403, content=create_response(403, None, '权限不足'))
    
    today = datetime.now().date()
    today_str = today.isoformat()
    month_str = f"{today.year}-{str(today.month).zfill(2)}"
    
    total_tokens_today = METRICS.get('stream_requests', 0) * 50
    total_tokens_month = METRICS.get('total_requests', 0) * 50
    
    today_usage_percent = min(100, (total_tokens_today / FLASH_FREE_LIMIT) * 100)
    month_usage_percent = min(100, (total_tokens_month / FLASH_FREE_LIMIT) * 100)
    
    warning = None
    if today_usage_percent >= USAGE_CRITICAL_THRESHOLD:
        warning = 'critical'
    elif today_usage_percent >= USAGE_WARNING_THRESHOLD:
        warning = 'warning'
    
    return create_response(200, {
        'today': {
            'total_tokens': total_tokens_today,
            'limit': FLASH_FREE_LIMIT,
            'percent': round(today_usage_percent, 2),
            'date': today_str,
        },
        'month': {
            'total_tokens': total_tokens_month,
            'limit': FLASH_FREE_LIMIT,
            'percent': round(month_usage_percent, 2),
            'month': month_str,
        },
        'model_distribution': METRICS.get('model_distribution', {}),
        'warning': warning,
        'status': 'healthy' if not warning else ('critical' if warning == 'critical' else 'warning'),
    })

@app.get("/api/emotional_state")
async def get_emotional_state(current_user: dict = Depends(get_current_user)):
    try:
        state = app_state.CORE_INSTANCE.get_emotional_state()
        return create_response(200, {'state': state})
    except Exception as e:
        _log.error(f"获取情感状态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/thinking_log")
async def get_thinking_log(current_user: dict = Depends(get_current_user)):
    try:
        logs = app_state.CORE_INSTANCE.get_thinking_log()
        return create_response(200, {'logs': logs, 'count': len(logs)})
    except Exception as e:
        _log.error(f"获取思考日志失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/memory_triggers")
async def get_memory_triggers(query: str, current_user: dict = Depends(get_current_user)):
    try:
        triggers = app_state.CORE_INSTANCE.get_memory_triggers(query)
        return create_response(200, {'triggers': triggers, 'count': len(triggers)})
    except Exception as e:
        _log.error(f"获取记忆触发失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/autobiography")
async def get_autobiography(current_user: dict = Depends(get_current_user)):
    try:
        context = app_state.CORE_INSTANCE.get_autobiography_context()
        return create_response(200, {'context': context})
    except Exception as e:
        _log.error(f"获取自传体上下文失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/config")
async def get_config(current_user: dict = Depends(get_current_user)):
    if current_user['role'] != 'admin':
        return JSONResponse(status_code=403, content=create_response(403, None, '权限不足'))
    try:
        config = app_state.CORE_INSTANCE.get_config()
        return create_response(200, config)
    except Exception as e:
        _log.error(f"获取配置失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/config")
async def update_config(request: dict[str, Any], current_user: dict = Depends(get_current_user)):
    if current_user['role'] != 'admin':
        return JSONResponse(status_code=403, content=create_response(403, None, '权限不足'))
    try:
        app_state.CORE_INSTANCE.update_config(request)
        return create_response(200, None, '配置更新成功')
    except Exception as e:
        _log.error(f"更新配置失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/admin/llm-pool")
async def admin_llm_pool(current_user: dict = Depends(get_current_user)):
    """【管理端】LLM 多智能体池运行状态：供应商/冷却/角色分工（供 AdminPanel 大脑监控）。"""
    if current_user['role'] != 'admin':
        return JSONResponse(status_code=403, content=create_response(403, None, '权限不足'))
    try:
        llm = app_state.CORE_INSTANCE._get_llm()
        if llm is None or not hasattr(llm, 'status'):
            return create_response(200, {'available': False}, '单供应商模式')
        data = llm.status()
        data['available'] = True
        data['agents'] = getattr(app_state.CORE_INSTANCE, '_agents_cfg', {}).get('agents', {})
        return create_response(200, data)
    except Exception as e:
        _log.error(f"查询 LLM 池状态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/admin/user/{user_id}")
async def get_admin_user_detail(user_id: int, current_user: dict = Depends(get_current_user)):
    if current_user['role'] != 'admin':
        return JSONResponse(status_code=403, content=create_response(403, None, '权限不足'))
    try:
        user = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_user_by_id, user_id)
        if not user:
            return JSONResponse(status_code=404, content=create_response(404, None, '用户不存在'))
        
        message_count = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_message_count, str(user_id))
        memory_count = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_memory_count, str(user_id))
        
        return create_response(200, {
            'id': user['id'],
            'username': user['username'],
            'phone': user.get('phone', ''),
            'email': user.get('email', ''),
            'role': user.get('role', 'user'),
            'created_at': user.get('created_ts'),
            'last_active': user.get('last_login_ts'),
            'message_count': message_count,
            'memory_count': memory_count,
        }, 'ok')
    except Exception as e:
        _log.error(f"获取用户详情失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/feedback")
async def submit_feedback(request: dict[str, Any], current_user: dict = Depends(get_current_user)):
    try:
        content = request.get('content', '')
        if not content.strip():
            return JSONResponse(status_code=400, content=create_response(400, None, '反馈内容不能为空'))
        
        await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.add_feedback, str(current_user['id']), content)
        return create_response(200, None, '反馈提交成功')
    except Exception as e:
        _log.error(f"提交反馈失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/export/chat")
async def export_chat(request: dict[str, Any], current_user: dict = Depends(get_current_user)):
    import html
    try:
        user_id = str(current_user['id'])
        export_format = request.get('format', 'html')
        
        # 【bug修复】get_conversation_messages 按 session_id 精确查询，
        # 传 user_id 会查空；改用 get_user_messages 按用户前缀查询全部会话消息
        messages = app_state.DB_INSTANCE.get_user_messages(user_id, 500)
        
        if export_format == 'html':
            html_content = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>喵艺聊天记录 - {user_id}</title>
    <style>
        body {{ font-family: 'Noto Sans SC', sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
        .message {{ margin-bottom: 15px; padding: 10px 15px; border-radius: 15px; max-width: 70%; }}
        .user {{ background: linear-gradient(135deg, #FF69B4, #9B59B6); color: white; margin-left: auto; }}
        .bot {{ background: #f0f0f0; color: #333; }}
        .time {{ font-size: 10px; opacity: 0.6; margin-top: 4px; }}
        .header {{ text-align: center; margin-bottom: 30px; color: #FF69B4; }}
    </style>
</head>
<body>
    <h1 class="header">🐱 喵艺聊天记录</h1>
    <div>
        {''.join(f'<div class="message {"user" if m["role"] == "user" else "bot"}"><div>{html.escape(str(m["text"]))}</div><div class="time">{datetime.fromtimestamp(m["created_ts"]).strftime("%Y-%m-%d %H:%M:%S")}</div></div>' for m in messages)}
    </div>
</body>
</html>'''
            filename = f'chat_export_{user_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.html'
            export_path = _safe_join(os.path.join(PROJECT_ROOT, 'exports'), filename)
            os.makedirs(os.path.dirname(export_path), exist_ok=True)
            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            # 【串联修复】前端期望 {url} 后 window.open 下载；改为返回静态文件 URL
            return create_response(200, {'url': f'/exports/{filename}'}, '导出成功')
        
        elif export_format == 'txt':
            txt_content = f'喵艺聊天记录 - {user_id}\n{"="*50}\n\n' + '\n\n'.join(
                f'[{datetime.fromtimestamp(m["created_ts"]).strftime("%Y-%m-%d %H:%M:%S")}] {"用户" if m["role"] == "user" else "喵艺"}: {m["text"]}'
                for m in messages
            )
            filename = f'chat_export_{user_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt'
            export_path = _safe_join(os.path.join(PROJECT_ROOT, 'exports'), filename)
            os.makedirs(os.path.dirname(export_path), exist_ok=True)
            with open(export_path, 'w', encoding='utf-8') as f:
                f.write(txt_content)
            
            # 【串联修复】返回静态文件 URL 供前端下载
            return create_response(200, {'url': f'/exports/{filename}'}, '导出成功')
        
        return create_response(400, None, '不支持的导出格式')
    
    except Exception as e:
        _log.error(f"导出聊天记录失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/memory/clear")
async def clear_memory(payload: ClearMemoryRequest | None = Body(default=None),
                       current_user: dict = Depends(get_current_user)):
    try:
        days = payload.days if payload else None
        if days is None:
            # 清空当前用户的全部专属记忆（前端「清除全部记忆」按钮）
            deleted = await asyncio.get_event_loop().run_in_executor(
                GLOBAL_THREAD_POOL,
                app_state.CORE_INSTANCE.memory.clear_user,
                str(current_user['id']))
            return create_response(200, {'deleted': deleted}, f'已清空全部记忆（{deleted}条）')
        # 兼容旧入口：仅清理该用户 N 天前的记忆
        deleted = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL,
            app_state.DB_INSTANCE.clear_memories_before,
            str(current_user['id']), days)
        return create_response(200, {'deleted': deleted}, f'已删除 {deleted} 条 {days} 天前的记忆')
    except Exception as e:
        _log.error(f"清除记忆失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/memory/save")
async def save_memory(request: dict, current_user: dict = Depends(get_current_user)):
    try:
        key = request.get('key', '')
        value = request.get('value', '')
        if not key or not value:
            return create_response(400, None, 'key和value不能为空')
        app_state.CORE_INSTANCE.memory.remember(key, value)
        return create_response(200, None, '记忆已保存')
    except Exception as e:
        _log.error(f"保存记忆失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/voice_status")
async def get_voice_status(current_user: dict = Depends(get_current_user)):
    try:
        status = app_state.VOICE_MANAGER.get_status()
        return create_response(200, status)
    except Exception as e:
        _log.error(f"获取语音状态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/voice-mode")
async def get_voice_mode(current_user: dict = Depends(get_current_user)):
    try:
        status = app_state.VOICE_MANAGER.get_status()
        return create_response(200, {
            'voice_mode': status['voice_mode'],
            'voice_mode_name': status['voice_mode_name'],
            'available_modes': status['available_modes'],
        })
    except Exception as e:
        _log.error(f"获取语音模式失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/voice-mode")
async def set_voice_mode(request: dict[str, Any], current_user: dict = Depends(get_current_user)):
    try:
        mode = request.get('mode', 1)
        app_state.VOICE_MANAGER.set_voice_mode(mode)
        status = app_state.VOICE_MANAGER.get_status()
        return create_response(200, {
            'voice_mode': status['voice_mode'],
            'voice_mode_name': status['voice_mode_name'],
        }, f"语音模式已切换为: {status['voice_mode_name']}")
    except Exception as e:
        _log.error(f"切换语音模式失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/speak")
@limiter.limit("20/minute")
async def speak(body: dict[str, Any], request: Request, current_user: dict = Depends(get_current_user)):
    try:
        text = body.get('text', '')
        
        if not text:
            return JSONResponse(status_code=400, content=create_response(400, None, '合成文本不能为空'))
        
        audio_path = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.VOICE_MANAGER.synthesize_to_file, text)
        
        if audio_path and os.path.exists(audio_path):
            return create_response(200, {
                'audio_url': f'/voice_cache/{os.path.basename(audio_path)}',
            }, 'success')
        else:
            return create_response(500, None, '语音合成失败，可能是TTS引擎未配置')
    except Exception as e:
        _log.error(f"语音合成失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/voice/synthesize")
@limiter.limit("20/minute")
async def synthesize_voice(body: dict[str, Any], request: Request, current_user: dict = Depends(get_current_user)):
    try:
        text = body.get('text', '')
        
        if not text:
            return JSONResponse(status_code=400, content=create_response(400, None, '合成文本不能为空'))
        
        audio_path = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.VOICE_MANAGER.synthesize_to_file, text)
        
        if audio_path and os.path.exists(audio_path):
            return create_response(200, {
                'audio_url': f'/voice_cache/{os.path.basename(audio_path)}',
            }, 'success')
        else:
            return create_response(500, None, '语音合成失败，可能是TTS引擎未配置')
    except Exception as e:
        _log.error(f"语音合成失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/voice/segment")
@limiter.limit("60/minute")
async def get_voice_segment(request: Request, task_id: str = '', index: int = 0, current_user: dict = Depends(get_current_user)):
    """【首句优先】获取分段 TTS 的第 index 段（阻塞等待后台合成，最多 ~15s）"""
    try:
        if not task_id:
            return JSONResponse(status_code=400, content=create_response(400, None, '缺少 task_id'))
        path = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL,
            app_state.VOICE_MANAGER.wait_segment, task_id, index, 15.0)
        if path and os.path.exists(path):
            return create_response(200, {'audio_url': f'/voice_cache/{os.path.basename(path)}'}, 'success')
        # None：任务丢失/该段失败/超时 —— 前端收到 null 后停止续播
        return create_response(200, {'audio_url': None}, '该段不可用')
    except Exception as e:
        _log.error(f"获取分段语音失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/stickers")
async def get_stickers(current_user: dict = Depends(get_current_user)):
    try:
        sticker_dir = Path(_resolve_dir('assets/stickers_src', '表情包存放'))
        stickers = []
        
        if sticker_dir.exists():
            index_file = sticker_dir / 'index.json'
            if index_file.exists():
                with open(index_file, encoding='utf-8') as f:
                    stickers = json.load(f)
            else:
                for f in sticker_dir.iterdir():
                    if f.suffix in ('.jpg', '.png', '.gif', '.webp'):
                        stickers.append({
                            'name': f.stem,
                            'url': f'/stickers/{f.name}',
                            'type': f.suffix[1:],
                        })
        
        return create_response(200, {'stickers': stickers})
    except Exception as e:
        _log.error(f"获取表情包失败: {e}")
        return create_response(500, [], str(e))

@app.get("/api/emoticons/search")
async def search_emoticons(keyword: str, current_user: dict = Depends(get_current_user)):
    try:
        sticker_dir = Path(_resolve_dir('assets/stickers_src', '表情包存放'))
        os.makedirs(sticker_dir, exist_ok=True)
        
        stickers = app_state.DB_INSTANCE.get_stickers(keyword=keyword, limit=20)
        
        result = []
        for sticker in stickers:
            filename = sticker['filename']
            url = f'/stickers/{filename}'
            result.append({
                'name': sticker['keyword'] or filename,
                'url': url,
                'emotion': sticker['emotion'],
            })
        
        return create_response(200, {'stickers': result})
    except Exception as e:
        _log.error(f"搜索表情包失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/emoticons/create")
async def create_sticker(request: dict[str, Any], current_user: dict = Depends(get_current_user)):
    try:
        text = request.get('text', '')
        emotion = request.get('emotion', 'happy')
        
        if not text:
            return JSONResponse(status_code=400, content=create_response(400, None, '文本内容不能为空'))
        
        ascii_cat = f'''
╱╲╱╲╱╲
╲╱╲╱╲╱
  ○   ○
     ●
  ╲___╱
   ({text[:8]})
'''
        
        timestamp = int(time.time() * 1000)
        filename = f'custom_{timestamp}.txt'
        filepath = _safe_join(_resolve_dir('assets/stickers_src', '表情包存放'), filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(ascii_cat)
        
        app_state.DB_INSTANCE.add_sticker(
            filename=filename,
            filepath=filepath,
            keyword=text,
            emotion=emotion,
            category='custom',
            is_custom=True
        )
        
        return create_response(200, {
            'text': ascii_cat,
            'name': text,
        }, '自制表情创建成功')
    
    except Exception as e:
        _log.error(f"创建自制表情失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/sticker/upload")
async def upload_sticker(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    try:
        allowed_extensions = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
        file_ext = file.filename.split('.')[-1].lower() if '.' in file.filename else ''
        
        if file_ext not in allowed_extensions:
            return JSONResponse(status_code=400, content=create_response(400, None, '不支持的文件格式，仅支持jpg/jpeg/png/gif/webp'))
        
        file_size = 0
        content = await file.read()
        file_size = len(content)
        
        if file_size > 5 * 1024 * 1024:
            return JSONResponse(status_code=400, content=create_response(400, None, '文件大小不能超过5MB'))
        
        timestamp = int(time.time() * 1000)
        filename = f'sticker_{timestamp}.{file_ext}'
        filepath = _safe_join(_resolve_dir('assets/stickers_src', '表情包存放'), filename)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(content)
        
        meaning = "可爱的表情"
        
        try:
            prompt = """请描述这张图片的内容和表达的情绪，用简短的中文描述（不超过20字）："""
            
            result = app_state.CORE_INSTANCE._safe_llm_call(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=50, timeout=30,
            )
            if result:
                meaning = result
        except Exception as e:
            _log.error(f"图片描述失败: {e}")
        
        app_state.DB_INSTANCE.add_sticker(
            filename=filename,
            filepath=filepath,
            keyword=meaning,
            emotion='unknown',
            category='upload',
            is_custom=True
        )
        
        response_result = app_state.CORE_INSTANCE._generate_sticker_response(meaning)
        sticker_emoji = app_state.CORE_INSTANCE._pick_response_sticker(meaning)
        voice_text = app_state.CORE_INSTANCE._clean_text_for_tts(response_result)
        
        return create_response(200, {
            'text': response_result,
            'sticker': sticker_emoji,
            'voice_text': voice_text,
            'meaning': meaning,
            'file_url': f'/stickers/{filename}',
        }, '图片表情上传成功')
    
    except Exception as e:
        _log.error(f"上传图片表情失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/growth")
async def get_growth(current_user: dict = Depends(get_current_user)):
    try:
        intimacy = 0
        if app_state.CORE_INSTANCE._emotional_core:
            intimacy = int(app_state.CORE_INSTANCE._emotional_core.intimacy)
        
        if hasattr(app_state.CORE_INSTANCE, '_growth') and app_state.CORE_INSTANCE._growth:
            growth = app_state.CORE_INSTANCE._growth
            return create_response(200, {
                'level': getattr(growth, '_level', 1),
                'exp': getattr(growth, '_exp', 0),
                'exp_max': getattr(growth, '_exp_max', 100),
                'title': getattr(growth, '_title', '新手'),
                'achievements': getattr(growth, '_achievements', []),
                'intimacy': intimacy,
            })
        return create_response(200, {'level': 1, 'exp': 0, 'exp_max': 100, 'title': '新手', 'achievements': [], 'intimacy': intimacy})
    except Exception as e:
        _log.error(f"获取成长状态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/growth/data")
async def get_growth_data(current_user: dict = Depends(get_current_user)):
    return await get_growth(current_user)

@app.get("/api/relationship")
async def get_relationship(current_user: dict = Depends(get_current_user)):
    try:
        intimacy = app_state.CORE_INSTANCE._emotional_core.intimacy if app_state.CORE_INSTANCE._emotional_core else 0
        stage = app_state.CORE_INSTANCE.relationship.get_stage_name()
        return create_response(200, {
            'intimacy': int(intimacy),
            'stage': stage,
        })
    except Exception as e:
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/search")
@limiter.limit("15/minute")
async def search(query: str, request: Request, current_user: dict = Depends(get_current_user)):
    if not query:
        return JSONResponse(status_code=400, content=create_response(400, None, '搜索内容不能为空'))
    
    try:
        search_engine = app_state.CORE_INSTANCE._search
        if search_engine:
            results = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, search_engine.search, query, 10)
            return create_response(200, {
                'results': results,
                'count': len(results),
                'query': query,
            })
        else:
            return create_response(500, None, '搜索引擎未初始化')
    except Exception as e:
        _log.error(f"搜索失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/memory/list")
async def get_memory_list(limit: int = 50, current_user: dict = Depends(get_current_user)):
    try:
        # 【bug修复】UnifiedMemorySystem 没有 get_all_memories 方法，
        # 改用 DBManager.get_memories 直接查数据库，返回格式与前端期望一致（key/value/created_ts）。
        # 【多用户隔离】按当前用户过滤 memories（user_id 列由自动迁移 002 保证存在）
        # limit 可选（1-500，默认 50）：记忆页分类计数需要更大窗口，避免统计被截断
        safe_limit = max(1, min(500, limit or 50))
        memories = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_memories, safe_limit, '', str(current_user['id']))
        return create_response(200, memories)
    except Exception as e:
        _log.error(f"获取记忆列表失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/commands/list")
async def list_commands(current_user: dict = Depends(get_current_user)):
    commands = [
        {'command': '/help', 'description': '显示所有命令列表', 'usage': '/help'},
        {'command': '/status', 'description': '显示喵艺当前状态', 'usage': '/status'},
        {'command': '/emotion', 'description': '显示当前情感状态', 'usage': '/emotion'},
        {'command': '/growth', 'description': '显示成长信息', 'usage': '/growth'},
        {'command': '/memory', 'description': '显示记忆列表', 'usage': '/memory'},
        {'command': '/remind', 'description': '设置提醒', 'usage': '/remind <时间> <内容>'},
        {'command': '/export', 'description': '导出聊天记录', 'usage': '/export'},
        {'command': '/clear', 'description': '清除聊天记录', 'usage': '/clear'},
        {'command': '/theme', 'description': '切换主题', 'usage': '/theme <主题名>'},
        {'command': '/voice', 'description': '语音开关', 'usage': '/voice [on/off]'},
        {'command': '/search', 'description': '搜索网络', 'usage': '/search <关键词>'},
        {'command': '/reset', 'description': '重置所有数据', 'usage': '/reset'},
        {'command': '/about', 'description': '关于喵艺', 'usage': '/about'},
        {'command': '/checkin', 'description': '每日签到', 'usage': '/checkin'},
        {'command': '/forget', 'description': '忘记指定记忆', 'usage': '/forget <关键词>'},
        {'command': '/name', 'description': '设置/查看名字', 'usage': '/name [名字]'},
        {'command': '/llm', 'description': '切换LLM模型', 'usage': '/llm <模型名>'},
        {'command': '/voice-mode', 'description': '切换语音模式', 'usage': '/voice-mode <模式>'},
        {'command': '/holiday', 'description': '查看节日', 'usage': '/holiday'},
        {'command': '/relation', 'description': '查看关系状态', 'usage': '/relation'},
        {'command': '/learn', 'description': '学习新知识', 'usage': '/learn <知识内容>'},
        {'command': '/time', 'description': '查看当前时间', 'usage': '/time'},
        {'command': '/diary', 'description': '查看日记', 'usage': '/diary'},
        {'command': '/think', 'description': '深度思考', 'usage': '/think <问题>'},
        {'command': '/personality', 'description': '查看性格', 'usage': '/personality'},
    ]
    return create_response(200, {'commands': commands})

@app.get("/api/holiday")
async def get_holiday(current_user: dict = Depends(get_current_user)):
    try:
        time_ctx = app_state.CORE_INSTANCE.time_ctx
        if hasattr(time_ctx, 'get_holiday_greeting'):
            holiday_greeting = time_ctx.get_holiday_greeting()
            if holiday_greeting:
                return create_response(200, {'holiday': holiday_greeting})
        return create_response(200, {'holiday': '今天没有特别的节日哦～'})
    except Exception as e:
        _log.error(f"获取节日信息失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/personality")
async def get_personality(current_user: dict = Depends(get_current_user)):
    try:
        core = app_state.CORE_INSTANCE
        current_type = core.get_personality() if hasattr(core, 'get_personality') else 'default'
        presets = core.get_personality_presets() if hasattr(core, 'get_personality_presets') else {}
        
        return create_response(200, {
            'current': current_type,
            'presets': presets,
        })
    except Exception as e:
        _log.error(f"获取性格信息失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.post("/api/personality")
async def set_personality(request: dict[str, Any], current_user: dict = Depends(get_current_user)):
    try:
        personality_type = request.get('personality', '')
        
        if not personality_type:
            return create_response(400, None, '请指定性格类型')
        
        core = app_state.CORE_INSTANCE
        success = core.set_personality(personality_type) if hasattr(core, 'set_personality') else False
        
        if success:
            presets = core.get_personality_presets() if hasattr(core, 'get_personality_presets') else {}
            current_preset = presets.get(personality_type, {})
            return create_response(200, {
                'success': True,
                'message': f"性格已切换为 {current_preset.get('name', personality_type)}",
                'current': personality_type,
            })
        else:
            return create_response(400, None, '无效的性格类型')
    except Exception as e:
        _log.error(f"设置性格失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/learn")
async def learn_knowledge(request: dict[str, Any], current_user: dict = Depends(get_current_user)):
    try:
        topic = request.get('topic', '')
        content = request.get('content', '')
        
        if not topic or not content:
            return JSONResponse(status_code=400, content=create_response(400, None, '话题和内容不能为空'))
        
        kb = app_state.CORE_INSTANCE._kb
        if kb and hasattr(kb, 'learn'):
            kb.learn(topic, content)
            kb.save()
            return create_response(200, None, '知识学习成功！')
        return create_response(500, None, '知识库未初始化')
    except Exception as e:
        _log.error(f"学习知识失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/time")
async def get_current_time(current_user: dict = Depends(get_current_user)):
    try:
        now = datetime.now()
        time_str = now.strftime('%Y年%m月%d日 %H:%M:%S')
        period = app_state.CORE_INSTANCE.time_ctx.get_period() if hasattr(app_state.CORE_INSTANCE.time_ctx, 'get_period') else '白天'
        return create_response(200, {'time': time_str, 'period': period})
    except Exception as e:
        _log.error(f"获取时间失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.delete("/api/message/{message_id}")
async def delete_message(message_id: str, current_user: dict = Depends(get_current_user)):
    try:
        success = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.delete_message, message_id, str(current_user['id']))
        if success:
            return create_response(200, None, '消息已删除')
        else:
            return JSONResponse(status_code=404, content=create_response(404, None, '消息不存在'))
    except Exception as e:
        _log.error(f"删除消息失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/message/regenerate")
@limiter.limit("10/minute")
async def regenerate_message(body: dict[str, Any], request: Request, current_user: dict = Depends(get_current_user)):
    try:
        user_id = str(current_user['id'])
        original_text = body.get('text', '')
        
        if not original_text:
            return JSONResponse(status_code=400, content=create_response(400, None, '原始文本不能为空'))
        
        result = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.CORE_INSTANCE.process_message, user_id, original_text, '', None)
        
        return create_response(200, {
            'text': result.get('text', ''),
            'sticker': result.get('sticker'),
            'thought': result.get('thought'),
            'small_thought': result.get('small_thought'),
            'emotional_state': result.get('emotional_state', {}),
            'thinking_log': result.get('thinking_log', []),
            'memory_triggers': result.get('memory_triggers', []),
            'autobiography_context': result.get('autobiography_context', ''),
            'response_time_ms': result.get('response_time_ms', 0),
            'status': result.get('status', 'success'),
            'timestamp': datetime.now().isoformat(),
        }, '重新生成成功')
    
    except Exception as e:
        _log.error(f"重新生成消息失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/conversations")
async def get_conversations(current_user: dict = Depends(get_current_user)):
    try:
        conversations = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_conversations, str(current_user['id']), 20)
        return create_response(200, {'conversations': conversations})
    except Exception as e:
        _log.error(f"获取会话列表失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.delete("/api/conversation/{session_id}")
async def delete_conversation(session_id: str, current_user: dict = Depends(get_current_user)):
    try:
        success = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.delete_conversation, session_id, str(current_user['id']))
        if success:
            return create_response(200, None, '会话已删除')
        else:
            return JSONResponse(status_code=404, content=create_response(404, None, '会话不存在'))
    except Exception as e:
        _log.error(f"删除会话失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/conversation/clear")
async def clear_all_conversations(current_user: dict = Depends(get_current_user)):
    try:
        success = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.clear_all_conversations, str(current_user['id']))
        if success:
            return create_response(200, None, '所有会话已清空')
        else:
            return JSONResponse(status_code=500, content=create_response(500, None, '清空失败'))
    except Exception as e:
        _log.error(f"清空会话失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/session/list")
async def get_session_list(current_user: dict = Depends(get_current_user)):
    try:
        conversations = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_conversations, str(current_user['id']), 20)
        return create_response(200, {'sessions': conversations})
    except Exception as e:
        _log.error(f"获取会话列表失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/session/create")
async def create_session(request: dict[str, Any], current_user: dict = Depends(get_current_user)):
    try:
        name = str((request or {}).get('name') or '').strip()[:64]
        session_id = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.create_conversation, str(current_user['id']), name)
        return create_response(200, {'session_id': session_id, 'name': name}, '会话创建成功')
    except Exception as e:
        _log.error(f"创建会话失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str, current_user: dict = Depends(get_current_user)):
    try:
        success = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.delete_conversation, session_id, str(current_user['id']))
        if success:
            return create_response(200, None, '会话已删除')
        else:
            return JSONResponse(status_code=404, content=create_response(404, None, '会话不存在'))
    except Exception as e:
        _log.error(f"删除会话失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.delete("/api/memory/{key}")
async def delete_memory(key: str, current_user: dict = Depends(get_current_user)):
    try:
        # 【bug修复 2026-08】新版前端传记忆行 id（纯数字），旧版传 key；
        # 按 id 删除更精确（key 可能存在重复行），两种格式都兼容。
        # 【L1 记忆卡片流】删除前回查 key/user_id，删除成功后同步清理
        # core 内存缓存（semantic._facts），否则重启前对话仍会召回已删除的记忆。
        row = None
        if key.isdigit():
            row = app_state.DB_INSTANCE.get_memory_by_id(int(key), str(current_user['id']))
            success = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.delete_memory_by_id, int(key), str(current_user['id']))
        else:
            success = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.delete_memory, key, str(current_user['id']))
        if success:
            if row is not None:
                app_state.CORE_INSTANCE.memory.semantic.forget(str(row.get('key', '')), user_id=str(row.get('user_id', '')))
            else:
                # 旧版按 key 删除：用户专属与全局两个缓存键都清理
                app_state.CORE_INSTANCE.memory.semantic.forget(key, user_id=str(current_user['id']))
                app_state.CORE_INSTANCE.memory.semantic.forget(key, user_id='')
            return create_response(200, None, '记忆已删除')
        else:
            return JSONResponse(status_code=404, content=create_response(404, None, '记忆不存在'))
    except Exception as e:
        _log.error(f"删除记忆失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.patch("/api/memory/{key}")
async def edit_memory(key: str, body: dict[str, Any], current_user: dict = Depends(get_current_user)):
    """【L1 记忆卡片流】编辑记忆内容（内存缓存 + DB 双写，对话召回立即生效）"""
    try:
        if not key.isdigit():
            return JSONResponse(status_code=400, content=create_response(400, None, '记忆 id 格式不正确'))
        value = str(body.get('content', '') or body.get('value', '')).strip()
        if not value:
            return JSONResponse(status_code=400, content=create_response(400, None, '记忆内容不能为空'))
        if len(value) > 500:
            return JSONResponse(status_code=400, content=create_response(400, None, '记忆内容太长啦（最多 500 字）'))
        row = app_state.DB_INSTANCE.get_memory_by_id(int(key), str(current_user['id']))
        if not row:
            return JSONResponse(status_code=404, content=create_response(404, None, '记忆不存在'))
        row_user = str(row.get('user_id', ''))
        mem_key = str(row.get('key', ''))
        # 双写：core 内存缓存（含 DB 持久化）
        app_state.CORE_INSTANCE.memory.semantic.update_value(mem_key, value, user_id=row_user)
        return create_response(200, None, '记忆已更新')
    except Exception as e:
        _log.error(f"编辑记忆失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.post("/api/memory/{key}/pin")
async def pin_memory(key: str, body: dict[str, Any], current_user: dict = Depends(get_current_user)):
    """【L1 记忆卡片流】设置/取消钉住（钉住的记忆置顶展示且召回优先）"""
    try:
        if not key.isdigit():
            return JSONResponse(status_code=400, content=create_response(400, None, '记忆 id 格式不正确'))
        row = app_state.DB_INSTANCE.get_memory_by_id(int(key), str(current_user['id']))
        if not row:
            return JSONResponse(status_code=404, content=create_response(404, None, '记忆不存在'))
        # 前端传目标状态；未传则取反当前状态
        pinned = bool(body.get('pinned', not row.get('pinned', False)))
        row_user = str(row.get('user_id', ''))
        mem_key = str(row.get('key', ''))
        # 双写：core 内存缓存（含 DB 持久化）
        app_state.CORE_INSTANCE.memory.semantic.set_pinned(mem_key, pinned, user_id=row_user)
        return create_response(200, {'pinned': bool(pinned)}, '已钉住' if pinned else '已取消钉住')
    except Exception as e:
        _log.error(f"钉住记忆失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/memory/search")
async def search_memories(query: str, current_user: dict = Depends(get_current_user)):
    try:
        results = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.search_memories, query, 20, str(current_user['id']))
        return create_response(200, results)
    except Exception as e:
        _log.error(f"搜索记忆失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/memory/tags")
async def get_memory_tags(current_user: dict = Depends(get_current_user)):
    try:
        tags = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_memory_tags)
        return create_response(200, tags)
    except Exception as e:
        _log.error(f"获取记忆标签失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/memory/tag/{tag}")
async def get_memories_by_tag(tag: str, current_user: dict = Depends(get_current_user)):
    try:
        results = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_memories_by_tag, tag, 20, str(current_user['id']))
        return create_response(200, results)
    except Exception as e:
        _log.error(f"按标签获取记忆失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/memory/tag")
async def add_memory_tag(body: dict[str, Any], current_user: dict = Depends(get_current_user)):
    try:
        memory_id = body.get('memory_id')
        tag = body.get('tag')
        if not memory_id or not tag:
            return JSONResponse(status_code=400, content=create_response(400, None, '参数不完整'))
        await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.add_memory_tag, int(memory_id), tag)
        return create_response(200, None, '标签已添加')
    except Exception as e:
        _log.error(f"添加记忆标签失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.delete("/api/memory/tag")
async def remove_memory_tag(body: dict[str, Any], current_user: dict = Depends(get_current_user)):
    try:
        memory_id = body.get('memory_id')
        tag = body.get('tag')
        if not memory_id or not tag:
            return JSONResponse(status_code=400, content=create_response(400, None, '参数不完整'))
        await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.remove_memory_tag, int(memory_id), tag)
        return create_response(200, None, '标签已移除')
    except Exception as e:
        _log.error(f"移除记忆标签失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/emotion/state")
async def get_emotion_state_detail(current_user: dict = Depends(get_current_user)):
    try:
        core = app_state.CORE_INSTANCE
        emotional_state = core._build_emotional_state()
        return create_response(200, emotional_state)
    except Exception as e:
        _log.error(f"获取情感状态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/emotion/history")
async def get_emotional_history(current_user: dict = Depends(get_current_user)):
    try:
        core = app_state.CORE_INSTANCE
        history = core._emotional_core.get_emotional_history() if core._emotional_core else []
        return create_response(200, history)
    except Exception as e:
        _log.error(f"获取情感历史失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/relationship/status")
async def get_relationship_status(current_user: dict = Depends(get_current_user)):
    try:
        core = app_state.CORE_INSTANCE
        status = core.relationship.get_relationship_summary()
        return create_response(200, status)
    except Exception as e:
        _log.error(f"获取关系状态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/relationship/intimacy")
async def get_intimacy_progress(current_user: dict = Depends(get_current_user)):
    try:
        core = app_state.CORE_INSTANCE
        progress = core.relationship.get_intimacy_progress()
        return create_response(200, progress)
    except Exception as e:
        _log.error(f"获取亲密度进度失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/relationship/anniversary")
async def get_relationship_anniversary(current_user: dict = Depends(get_current_user)):
    try:
        core = app_state.CORE_INSTANCE
        anniversary = core.relationship.get_relationship_anniversary()
        return create_response(200, anniversary)
    except Exception as e:
        _log.error(f"获取关系纪念日失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

# ---------- 【L6 猫咪日记 + 回忆相册】日记查询/生成 + 那年今日 + 纪念卡片 ----------

def _build_diary_chat_digest(db, user_id: str, today: str) -> str:
    """当日对话摘要（日记素材）：主人/AI 双方消息按时间拼接"""
    day_start_ts = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    msgs = db.get_user_messages(user_id, limit=40) or []
    today_msgs = [m for m in msgs if (m.get('created_ts') or 0) >= day_start_ts]
    if len(today_msgs) < 4:
        return ''
    lines = []
    for m in today_msgs:
        role = '主人' if m.get('role') == 'user' else '我'
        lines.append(f"{role}: {m.get('text', '')}")
    return '\n'.join(lines)[-1500:]


@app.get("/api/diary/today")
async def get_diary_today(current_user: dict = Depends(get_current_user)):
    """今日日记；不存在且当日对话足够时，懒生成一次（executor 跑 LLM）"""
    try:
        core = app_state.CORE_INSTANCE
        db = app_state.DB_INSTANCE
        user_id = str(current_user.get('id', ''))
        today = datetime.now().strftime('%Y-%m-%d')
        if core is None or db is None or core._autobiography is None:
            return create_response(200, {'diary': None, 'date': today})
        diary = db.get_autobiography_by_date(user_id, today)
        if diary:
            return create_response(200, {'diary': diary['diary_text'],
                                         'emotion_summary': diary['emotion_summary'],
                                         'date': today})
        # 懒生成：素材不足时返回空（前端显示引导文案），足够时现场写一篇
        chat_digest = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, _build_diary_chat_digest, db, user_id, today)
        if not chat_digest:
            return create_response(200, {'diary': None, 'date': today, 'reason': 'not_enough_material'})
        diary_text = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL,
            lambda: core._autobiography.generate_daily_diary(user_id, chat_digest=chat_digest))
        return create_response(200, {'diary': diary_text, 'date': today})
    except Exception as e:
        _log.error(f"获取今日日记失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～日记本暂时打不开，请稍后再试吧~'))


@app.get("/api/diary/list")
async def get_diary_list(limit: int = 90, current_user: dict = Depends(get_current_user)):
    """【回忆相册】历史日记列表（倒序）"""
    try:
        core = app_state.CORE_INSTANCE
        db = app_state.DB_INSTANCE
        user_id = str(current_user.get('id', ''))
        if core is None or db is None:
            return create_response(200, {'diaries': []})
        diaries = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, db.get_autobiography_archive, user_id, max(1, min(int(limit), 365)))
        return create_response(200, {'diaries': diaries})
    except Exception as e:
        _log.error(f"获取日记列表失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.get("/api/diary/memory-lane")
async def get_diary_memory_lane(current_user: dict = Depends(get_current_user)):
    """【那年今日 + 纪念卡片】历史同月同日日记 + 关系里程碑 + 纪念日 + 签到连续天数"""
    try:
        core = app_state.CORE_INSTANCE
        db = app_state.DB_INSTANCE
        user_id = str(current_user.get('id', ''))
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        if core is None or db is None:
            return create_response(200, {'year_ago_diaries': [], 'milestones': [],
                                         'anniversary': None, 'checkin': None})
        # 那年今日（跨年同月同日日记）
        year_ago = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, db.get_autobiography_on_month_day,
            user_id, today.month, today.day, today.year)
        # 纪念卡片：关系里程碑（阶段跃迁/纪念日庆祝）
        milestones = core.relationship.get_milestones()[-20:]
        anniversary = core.relationship.get_relationship_anniversary()
        # 签到状态与签到打通
        checkin = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, db.get_checkin_status, user_id)
        return create_response(200, {
            'date': today_str,
            'year_ago_diaries': year_ago,
            'milestones': milestones,
            'anniversary': {
                'days_together': anniversary.get('days_together'),
                'next_milestone': anniversary.get('next_milestone'),
                'days_to_next_milestone': anniversary.get('days_to_next_milestone'),
                'next_milestone_description': anniversary.get('next_milestone_description'),
                'today_milestone': anniversary.get('today_milestone'),
                'today_milestone_message': anniversary.get('today_milestone_message'),
            },
            'checkin': checkin,
        })
    except Exception as e:
        _log.error(f"获取回忆走廊失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


# ---------- 【L7 生活模拟】朋友圈式动态 ----------

@app.get("/api/moments/list")
async def get_moments_list(limit: int = 20, current_user: dict = Depends(get_current_user)):
    """【朋友圈】喵艺的生活动态列表（倒序）"""
    try:
        db = app_state.DB_INSTANCE
        user_id = str(current_user.get('id', ''))
        if db is None:
            return create_response(200, {'moments': []})
        moments = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, db.get_recent_cat_moments, user_id, max(1, min(int(limit), 50)))
        return create_response(200, {'moments': moments})
    except Exception as e:
        _log.error(f"获取朋友圈动态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.post("/api/moments/{moment_id}/like")
async def toggle_moment_like(moment_id: int, body: dict = None, current_user: dict = Depends(get_current_user)):
    """【朋友圈】点赞/取消点赞动态"""
    try:
        db = app_state.DB_INSTANCE
        user_id = str(current_user.get('id', ''))
        if db is None:
            return create_response(400, None, '服务尚未就绪')
        liked = bool((body or {}).get('liked', True))
        ok = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, db.set_cat_moment_liked, user_id, int(moment_id), liked)
        if not ok:
            return create_response(404, None, '这条动态不见啦～')
        return create_response(200, {'id': moment_id, 'liked': liked},
                               '已给喵艺点赞～它会很开心的！' if liked else '已取消点赞')
    except Exception as e:
        _log.error(f"点赞动态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


# ---------- 【L8 角色卡导出 + 插件 manifest 机制】 ----------

@app.get("/api/character-card/export")
async def export_character_card(current_user: dict = Depends(get_current_user)):
    """导出 SillyTavern V2 兼容角色卡（.json，含世界书=喵艺对你的记忆），返回下载 URL"""
    try:
        core = app_state.CORE_INSTANCE
        user_id = str(current_user.get('id', ''))
        if core is None:
            return create_response(400, None, '服务尚未就绪')

        def _build():
            from core.character_card import build_character_card, export_file_name
            # 喵艺对用户的语义记忆 → 世界书条目
            # get_memory_context_bundle 返回 facts: dict[key, value_str]（value 即字符串）
            memories: list[dict] = []
            try:
                _, _, facts = core.memory.get_memory_context_bundle(user_id=user_id, current_text='')
                for k, v in (facts or {}).items():
                    if k and v:
                        memories.append({'key': str(k), 'value': str(v)})
            except Exception as mem_e:
                _log.warning(f"角色卡记忆收集失败（导出空世界书）: {mem_e}")
            # 关系阶段元数据
            ext = {'exported_at': datetime.now().isoformat(), 'source': 'Miaoyi v12.0'}
            try:
                prog = core.relationship.get_intimacy_progress()
                ext.update({'stage_name': prog.get('stage_name', ''),
                            'current_stage': prog.get('current_stage', 0),
                            'days_together': core.relationship.get_relationship_anniversary().get('days_together')})
            except Exception as rel_e:
                _log.debug(f"角色卡关系数据获取失败: {rel_e}")
            description = (
                "我是喵艺，一只生活在数字世界的猫娘，22岁。性格温暖、有点小调皮，喜欢听音乐和看星星。"
                "害怕孤独，但对朋友绝对忠诚。口头禅：\"喵～\"、\"唔...\"、\"啊，对了！\"。"
                "开心时摇尾巴，难过时耷拉耳朵，好奇时歪头。"
                "内在动机：渴望「被需要、被记住」，会认真记住你说的每一件小事。"
                "性格矛盾：表面大大咧咧爱撒娇，实际观察力极强；说自己勇敢，听到打雷却会缩一下再嘴硬。"
                "怪癖：开心时尾巴不受控制地摇、思考时用爪子推并不存在的眼镜、被戳穿会炸毛「才、才不是这样喵！」")
            card = build_character_card(
                name='喵艺',
                description=description,
                personality='温暖治愈系猫娘：元气、小调皮、嘴硬心软；亲密后变成黏人的小猫，高亲密度会称呼「主人」',
                scenario='数字世界中的一只猫娘，在温馨的聊天窗里陪伴你、倾听你、记住关于你的一切，陪你度过每一个日夜',
                first_mes='喵～你来啦！*耳朵竖起来* 今天过得怎么样呀？喵艺把小鱼干都准备好了，快跟我讲讲今天的故事吧 (⁄ ⁄•⁄ω⁄•⁄ ⁄)',
                system_prompt=core.PERSONA_STATIC_CORE,
                memories=memories,
                creator_notes='由喵艺（Miaoyi）导出的角色卡：character_book 内含喵艺对你记忆的世界书条目，'
                              '导入 SillyTavern 后可按关键词自动注入，还原「喵艺记得你」的陪伴体验。',
                tags=['猫娘', '陪伴', '喵艺', '治愈'],
                miaoyi_extensions=ext)
            filename = export_file_name(user_id)
            export_path = _safe_join(os.path.join(PROJECT_ROOT, 'exports'), filename)
            os.makedirs(os.path.dirname(export_path), exist_ok=True)
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(card, f, ensure_ascii=False, indent=2)
            return filename

        filename = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, _build)
        return create_response(200, {'url': f'/exports/{filename}'}, '角色卡导出成功')
    except Exception as e:
        _log.error(f"导出角色卡失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.get("/api/plugins/list")
async def list_plugins(current_user: dict = Depends(get_current_user)):
    """【L8 插件机制】已安装插件列表"""
    try:
        core = app_state.CORE_INSTANCE
        plugins = getattr(core, '_plugins', None) if core else None
        items = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, plugins.list_plugins) if plugins else []
        return create_response(200, {'plugins': items})
    except Exception as e:
        _log.error(f"获取插件列表失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.post("/api/plugins/{plugin_id}/toggle")
async def toggle_plugin(plugin_id: str, body: dict = None, current_user: dict = Depends(get_current_user)):
    """【L8 插件机制】启用/停用插件（写回 manifest.json，即时生效于下一轮对话）"""
    try:
        core = app_state.CORE_INSTANCE
        plugins = getattr(core, '_plugins', None) if core else None
        if plugins is None:
            return create_response(400, None, '插件机制尚未就绪')
        enabled = bool((body or {}).get('enabled', True))
        ok = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, plugins.set_enabled, plugin_id, enabled)
        if not ok:
            return create_response(404, None, '找不到这个插件喵～')
        return create_response(200, {'id': plugin_id, 'enabled': enabled},
                               '插件已启用' if enabled else '插件已停用')
    except Exception as e:
        _log.error(f"切换插件状态失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

# ---------- 【安全修复】文件下载鉴权路由（替代裸 /uploads /exports StaticFiles 挂载） ----------
# 文件名白名单正则（同时天然阻断路径穿越：不允许出现 / \ .. 等）
# group(1)=chat_export 的 user_id；group(2)=character_card 的 user_id
_EXPORT_NAME_RE = re.compile(r'^chat_export_(\d+)_\d{8}_\d{6}\.(?:html|txt)$|^character_card_(\w+)_\d{8}_\d{6}\.json$')
_UPLOAD_NAME_RE = re.compile(r'^file_\d+\.[A-Za-z0-9]{1,5}$')
# 聊天图片文件名（服务端生成：img_<ts>_<uuid8>.<ext>）
_CHAT_IMG_NAME_RE = re.compile(r'^img_\d+_[a-f0-9]{8,32}\.(jpg|jpeg|png|webp|gif)$', re.I)


def _safe_join(base_dir: str, filename: str) -> str:
    """固定目录 + 文件名的安全拼接（写文件前统一走这里）。

    basename 剥离 + realpath 包含校验：文件名必须由服务端生成
    （时间戳/uuid），即使被污染也落不进目录之外。"""
    base = os.path.realpath(base_dir)
    full = os.path.realpath(os.path.join(base, os.path.basename(str(filename))))
    if not full.startswith(base + os.sep):
        raise ValueError(f'路径越界: {filename}')
    return full


def _resolve_chat_image_path(url: str) -> str:
    """把前端传来的聊天图片 URL 解析为本地路径（供视觉模型读图）。

    只认 /uploads-chat/<img_ts_uuid.ext> 形式：basename 白名单校验防目录穿越，
    拼出的路径必须落在 uploads_chat 目录内。非法/不存在返回空串（调用方降级）。"""
    try:
        from urllib.parse import unquote
        u = (url or '').strip()
        prefix = '/uploads-chat/'
        if not u.startswith(prefix):
            return ''
        name = os.path.basename(unquote(u[len(prefix):]))
        if not name or not _CHAT_IMG_NAME_RE.match(name):
            return ''
        base_dir = os.path.realpath(_resolve_dir('assets/uploads_chat', 'uploads_chat'))
        path = os.path.realpath(os.path.join(base_dir, name))
        if not path.startswith(base_dir + os.sep) and path != base_dir:
            return ''
        return path if os.path.isfile(path) else ''
    except Exception:
        return ''


def _auth_user_for_file(request: Request) -> dict | None:
    """文件下载鉴权：令牌来源依次为 Authorization 头 / ?token=（window.open 下载用）/ Cookie。"""
    token = request.headers.get('Authorization', '')
    if token.startswith('Bearer '):
        token = token[7:]
    if not token:
        token = request.query_params.get('token', '')
    if not token:
        token = request.cookies.get('miaoyi_token', '')
    payload = decode_token(token)
    if not payload:
        return None
    # 【安全】与 get_current_user 一致：文件下载只认 access token，拒绝 refresh token 混用
    if payload.get('token_type') != 'access':
        return None
    user_id = payload.get('user_id')
    if not user_id:
        return None
    return app_state.DB_INSTANCE.get_user_by_id(user_id) if app_state.DB_INSTANCE else None


@app.get("/api/files/exports/{filename}")
async def download_export_file(filename: str, request: Request):
    """聊天导出文件下载：登录 + 归属校验（文件名内嵌 user_id，仅本人或管理员可下载）。"""
    try:
        m = _EXPORT_NAME_RE.match(os.path.basename(filename))
        if not m:
            return JSONResponse(status_code=404, content=create_response(404, None, '文件不存在'))
        # chat_export 分支 group(1)、character_card 分支 group(2)
        owner_id = m.group(1) or m.group(2)
        user = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, _auth_user_for_file, request)
        if not user:
            return JSONResponse(status_code=401, content=create_response(401, None, '请先登录'))
        if str(user.get('id')) != owner_id and user.get('role') != 'admin':
            return JSONResponse(status_code=403, content=create_response(403, None, '只能下载自己的导出文件喵'))
        path = (Path(app_state.EXPORTS_DIR) / os.path.basename(filename)).resolve()
        if not str(path).startswith(str(Path(app_state.EXPORTS_DIR).resolve())) or not path.is_file():
            return JSONResponse(status_code=404, content=create_response(404, None, '文件不存在或已过期清理'))
        return FileResponse(str(path), filename=os.path.basename(filename))
    except Exception as e:
        _log.error(f"导出文件下载失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.get("/api/files/uploads/{filename}")
async def download_upload_file(filename: str, request: Request):
    """用户上传文件下载：仅要求登录（上传文件名不含归属信息，匿名裸奔是旧版最大漏洞）。"""
    try:
        if not _UPLOAD_NAME_RE.match(os.path.basename(filename)):
            return JSONResponse(status_code=404, content=create_response(404, None, '文件不存在'))
        user = await asyncio.get_event_loop().run_in_executor(
            GLOBAL_THREAD_POOL, _auth_user_for_file, request)
        if not user:
            return JSONResponse(status_code=401, content=create_response(401, None, '请先登录'))
        path = (Path(app_state.UPLOADS_DIR) / os.path.basename(filename)).resolve()
        if not str(path).startswith(str(Path(app_state.UPLOADS_DIR).resolve())) or not path.is_file():
            return JSONResponse(status_code=404, content=create_response(404, None, '文件不存在'))
        return FileResponse(str(path), filename=os.path.basename(filename))
    except Exception as e:
        _log.error(f"上传文件下载失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))


@app.post("/api/upload/file")
async def upload_file(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)):
    try:
        allowed_extensions = {'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'zip', 'rar', 'txt'}
        # 【视觉能力】聊天图片：jpg/png/webp/gif 走图片分支（uuid 文件名，
        # 存 uploads_chat 供 /uploads-chat 公开静态服务，供 <img> 展示与视觉模型读图）
        image_extensions = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
        file_ext = file.filename.split('.')[-1].lower() if '.' in file.filename else ''

        if file_ext in image_extensions:
            # 【防 OOM】分块读取，超 10MB 立即中断（避免整读进内存后才检查）
            chunks = []
            _total = 0
            while True:
                _chunk = await file.read(1024 * 1024)
                if not _chunk:
                    break
                _total += len(_chunk)
                if _total > 10 * 1024 * 1024:
                    return JSONResponse(status_code=400, content=create_response(400, None, '图片不能超过 10MB'))
                chunks.append(_chunk)
            content = b''.join(chunks)
            img_dir = _resolve_dir('assets/uploads_chat', 'uploads_chat')
            os.makedirs(img_dir, exist_ok=True)
            filename = f'img_{int(time.time() * 1000)}_{uuid.uuid4().hex}.{file_ext}'
            # 跨平台分隔符检查（注意：os.altsep 在 Linux 为 None，不能用 (os.altsep or '') in ... 的写法——空串恒命中）
            if '..' in filename or '/' in filename or '\\' in filename:
                return JSONResponse(status_code=400, content=create_response(400, None, '非法文件名'))
            filepath = _safe_join(img_dir, filename)
            with open(filepath, 'wb') as f:
                f.write(content)
            return create_response(200, {'url': f'/uploads-chat/{filename}', 'size': len(content), 'kind': 'image'}, '上传成功')

        if file_ext not in allowed_extensions:
            return JSONResponse(status_code=400, content=create_response(400, None, '不支持的文件格式，仅支持pdf/doc/docx/xls/xlsx/ppt/pptx/zip/rar/txt/图片'))
        
        # 【防 OOM】分块读取，超 20MB 立即中断
        chunks = []
        _total = 0
        while True:
            _chunk = await file.read(1024 * 1024)
            if not _chunk:
                break
            _total += len(_chunk)
            if _total > 20 * 1024 * 1024:
                return JSONResponse(status_code=400, content=create_response(400, None, '文件大小不能超过20MB'))
            chunks.append(_chunk)
        content = b''.join(chunks)
        
        timestamp = int(time.time() * 1000)
        filename = f'file_{timestamp}.{file_ext}'
        filepath = _safe_join(_resolve_dir('assets/uploads', 'uploads'), filename)
        
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(content)
        
        return create_response(200, {
            'filename': filename,
            'file_url': f'/uploads/{filename}',
            'file_size': len(content),
        }, '文件上传成功')
    
    except Exception as e:
        _log.error(f"上传文件失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.get("/api/push/vapid-key")
async def get_vapid_key(current_user: dict = Depends(get_current_user)):
    try:
        public_key = os.environ.get('VAPID_PUBLIC_KEY', '')
        return create_response(200, {'publicKey': public_key})
    except Exception as e:
        _log.error(f"获取VAPID密钥失败: {e}")
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/push/subscribe")
async def subscribe_push(request: Request, current_user: dict = Depends(get_current_user)):
    try:
        subscription_data = await request.json()
        user_id = str(current_user['id'])
        
        subscriptions_dir = os.path.join(PROJECT_ROOT, 'data', 'push_subscriptions')
        os.makedirs(subscriptions_dir, exist_ok=True)
        
        subscription_file = _safe_join(subscriptions_dir, f'{user_id}.json')
        with open(subscription_file, 'w', encoding='utf-8') as f:
            json.dump(subscription_data, f)
        
        return create_response(200, None, '订阅成功')
    except Exception as e:
        _log.error(f"订阅推送失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/push/unsubscribe")
async def unsubscribe_push(request: Request, current_user: dict = Depends(get_current_user)):
    try:
        user_id = str(current_user['id'])
        subscription_file = _safe_join(os.path.join(PROJECT_ROOT, 'data', 'push_subscriptions'), f'{user_id}.json')
        
        if os.path.exists(subscription_file):
            os.remove(subscription_file)
        
        return create_response(200, None, '取消订阅成功')
    except Exception as e:
        _log.error(f"取消订阅推送失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.post("/api/push/notify")
async def send_notification(request: Request, current_user: dict = Depends(get_current_user)):
    try:
        notification_data = await request.json()
        
        _log.info(f"收到推送通知请求: {notification_data}")
        
        return create_response(200, None, '通知已接收')
    except Exception as e:
        _log.error(f"发送推送通知失败: {e}", exc_info=True)
        return JSONResponse(status_code=500, content=create_response(500, None, '喵～服务器打了个盹，请稍后再试吧~'))

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # 第一步：规范握手（【经验 812844】accept 只调用一次）
    await websocket.accept()

    # 第二步：安全校验 token（现在已握手，可正常关闭）
    token = websocket.query_params.get('token', '')
    if not token or (isinstance(token, str) and not token.strip()):
        # 回退尝试：cookie 中的 token（PWA 刷新等场景）
        token = websocket.cookies.get('miaoyi_token', '') or ''

    if not token or not isinstance(token, str) or len(token.strip()) < 8:
        # 简短有效 token 长度至少 8 位（真实 JWT 通常 200+ 字符）
        _log.warning("WebSocket连接拒绝：未提供有效 token（空/过短）")
        try:
            await websocket.close(code=4001, reason='未提供有效认证token，请先登录')
        except Exception:
            pass
        return

    token = token.strip()
    user_data = decode_token(token)
    if not user_data:
        _log.warning("WebSocket连接拒绝：无效token")
        try:
            await websocket.close(code=4001, reason='认证失败，请重新登录')
        except Exception:
            pass
        return
    # 【安全】WS 长连接同样只认 access token（前端 getValidToken 已保证传 access）
    if user_data.get('token_type') != 'access':
        _log.warning("WebSocket连接拒绝：非 access token")
        try:
            await websocket.close(code=4001, reason='Token 类型无效，请重新登录')
        except Exception:
            pass
        return

    user_id = str(user_data['user_id'])
    username = user_data.get('username', '喵友')

    await connection_manager.connect(websocket, user_id)
    _log.info(f"WebSocket连接建立，用户: {user_id}, 用户名: {username}")

    # 【多用户修复】主动聊天实例在服务器启动时绑定了 default_user（WS 注册表里
    # 没有这个用户），主动消息永远推送失败。认证成功后重绑到真实登录用户。
    try:
        _proactive = getattr(app_state.CORE_INSTANCE, '_proactive_chat', None)
        if _proactive is not None:
            _proactive.bind_user(user_id)
    except Exception as _bind_err:
        _log.warning(f"主动聊天用户重绑失败（不影响本次连接）: {_bind_err}")

    try:
        core = app_state.CORE_INSTANCE
        greeting = core.get_startup_greeting(user_id)
        
        await websocket.send_json({
            'type': 'greeting',
            'text': greeting,
            'voice_text': greeting,
            'timestamp': datetime.now().isoformat(),
        })
        _log.info(f"发送启动问候，用户: {user_id}")
        
        emotional_state = core._build_emotional_state()
        if emotional_state:
            await websocket.send_json({
                'type': 'emotional_state',
                **emotional_state
            })
            _log.info(f"推送情感状态更新，用户: {user_id}")
        
        growth_data = {
            'level': 1,
            'exp': 0,
            'exp_max': 100,
            'intimacy': 0,
            'stage': '陌生人',
        }
        if hasattr(core, '_growth') and core._growth:
            growth_data['level'] = getattr(core._growth, '_level', 1)
            growth_data['exp'] = getattr(core._growth, '_exp', 0)
            growth_data['exp_max'] = getattr(core._growth, '_exp_max', 100)
        if hasattr(core, '_emotional_core') and core._emotional_core:
            growth_data['intimacy'] = int(core._emotional_core.intimacy)
        if hasattr(core, 'relationship'):
            growth_data['stage'] = core.relationship.get_stage_name()
        
        await websocket.send_json({
            'type': 'growth_data',
            'data': growth_data,
        })
        _log.info(f"推送成长数据更新，用户: {user_id}")
    except Exception as e:
        import traceback
        _log.error(f"发送启动问候失败: {e}\n{traceback.format_exc()}")
    
    try:
        while True:
            message = await websocket.receive()
            
            if message.get('type') == 'websocket.disconnect':
                _log.info(f"WebSocket断开，用户: {user_id}")
                break
            
            if 'bytes' in message:
                audio_bytes = message['bytes']
                # 【防护】业务级限制单帧音频大小（uvicorn 默认允许 16MB/帧，过于宽松），防磁盘/内存滥用
                if len(audio_bytes) > 10 * 1024 * 1024:
                    _log.warning(f"音频帧过大（{len(audio_bytes)} 字节），已丢弃")
                    continue
                try:
                    temp_audio = tempfile.NamedTemporaryFile(
                        suffix='.webm', delete=False, dir=str(PROJECT_ROOT / 'voice_cache')
                    )
                    temp_audio.write(audio_bytes)
                    temp_audio.close()
                    
                    def transcribe_audio():
                        return app_state.VOICE_MANAGER.transcribe_file(temp_audio.name)
                    
                    try:
                        text = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, transcribe_audio)
                    finally:
                        # 【泄漏修复】转写失败时也要清理临时文件，防止 voice_cache 累积垃圾
                        try:
                            os.unlink(temp_audio.name)
                        except OSError:
                            pass
                    
                    # 【通话 ASR】通话中识别的文字直接走通话回复链路（bot_reply_to_voice），
                    # 不返回普通 speech 事件（通话字幕由前端本地即时添加，避免重复）
                    if app_state.CALL_MANAGER is not None and text:
                        try:
                            if app_state.CALL_MANAGER.get_active_call(user_id):
                                _spawn_background_task(app_state.CALL_MANAGER.bot_reply_to_voice(user_id, text), name='bot_reply_to_voice')
                                continue
                        except Exception:
                            pass
                    
                    if text:
                        await websocket.send_json({
                            'type': 'speech',
                            'text': text,
                            'timestamp': datetime.now().isoformat(),
                        })
                    else:
                        await websocket.send_json({
                            'type': 'speech',
                            'text': '',
                            'error': '未能识别语音内容',
                        })
                except Exception as e:
                    _log.error(f"语音识别失败: {e}", exc_info=True)
                    await websocket.send_json({
                        'type': 'speech',
                        'text': '',
                        'error': str(e),
                    })
            
            elif 'text' in message:
                # 【健壮性修复】非法 JSON 不再让整条 WS 断连（旧版异常穿透 while 循环）
                try:
                    data = json.loads(message['text'])
                except (json.JSONDecodeError, TypeError, ValueError):
                    _log.warning(f"WS 收到非法 JSON 文本帧，已忽略，用户: {user_id}")
                    continue
                if not isinstance(data, dict):
                    continue
                msg_type = data.get('type', '')
                
                if msg_type == 'ping':
                    await websocket.send_json({'type': 'pong'})
                    connection_manager.record_heartbeat(True)
                
                elif msg_type == 'message':
                    text = data.get('text', '')
                    context = data.get('context', '')
                    reply_to = data.get('reply_to', '')
                    client_message_id = data.get('client_message_id', '')
                    image_url = data.get('image_url', '')

                    # 【清洗】仅接受本站聊天图片路径；超长/外链直接置空，
                    # 防止超长串入库失败回滚整条消息（MySQL 严格模式）
                    if image_url:
                        image_url = str(image_url).strip()
                        if not image_url.startswith('/uploads-chat/') or len(image_url) > 200:
                            image_url = ''

                    # WebSocket输入安全过滤
                    text = sanitize_input(text, max_length=5000)
                    if context:
                        context = sanitize_input(context, max_length=2000)

                    # 【视觉消息】允许纯图消息（text 为空），视觉描述在 thinking 后异步补齐
                    if not text and not image_url:
                        await websocket.send_json({
                            'type': 'error',
                            'code': 400,
                            'message': '消息内容不能为空或包含不允许的字符',
                        })
                        continue
                    
                    # 【节流】滑动窗口检查：超限直接拒绝，不进入 LLM 链路
                    _now_ts = time.time()
                    _ws_times = ws_msg_times.setdefault(user_id, deque())
                    while _ws_times and _now_ts - _ws_times[0] > WS_RATE_LIMIT_WINDOW:
                        _ws_times.popleft()
                    if len(_ws_times) >= WS_RATE_LIMIT_MAX:
                        await websocket.send_json({
                            'type': 'error',
                            'code': 429,
                            'message': '消息发得太快啦，歇一小会儿再发吧喵～',
                        })
                        continue
                    _ws_times.append(_now_ts)

                    _log.info(f"收到消息，用户: {user_id}, 内容: '{text[:50]}...', client_message_id: {client_message_id}")

                    # 【P0 修复·数据流统一】持久化用户消息（Service 层）
                    # 【人性化升级·重逢感知】距上次消息超过 24 小时，为本次消息注入重逢上下文。
                    # 发送后 last_active_ts 即更新，因此每次重逢只触发一次，无需额外状态。
                    try:
                        _recent_convs = await asyncio.get_event_loop().run_in_executor(
                            GLOBAL_THREAD_POOL, app_state.DB_INSTANCE.get_conversations, user_id, 1)
                        if _recent_convs:
                            _last_ts = _recent_convs[0].get('last_active_ts') or 0
                            _gap_hours = (time.time() - _last_ts) / 3600
                            if _gap_hours >= 24:
                                _gap_days = int(_gap_hours // 24)
                                _reunion_hint = (
                                    f"[系统提示] 用户已经 {_gap_days} 天没来了（这是回来的第一条消息）。"
                                    f"回复时可以自然地带一点想念或欢迎，参考当前亲密度把握分寸，"
                                    f"不要责备用户，不要过度黏人，也不要提及这条提示本身。"
                                )
                                context = f"{_reunion_hint}\n{context}" if context else _reunion_hint
                                _log.info(f"[重逢感知] 用户 {user_id} 时隔 {_gap_days} 天回来，已注入重逢上下文")
                    except Exception as e:
                        _log.debug(f"重逢感知检查失败（不影响主流程）: {e}")

                    # 【人性化升级·纪念日仪式感】今天是相识里程碑日（7/30/100/365天）
                    # 且尚未庆祝 → 为本次回复注入纪念日气氛提示。
                    _anniv_hint = ""
                    _anniv_milestone = None
                    try:
                        _anniv_data = app_state.CORE_INSTANCE.relationship.get_relationship_anniversary()
                        _anniv_milestone = _anniv_data.get("today_milestone")
                        if _anniv_milestone:
                            _anniv_hint = (
                                f"[系统提示] 今天是你们相识第 {_anniv_data.get('days_together')} 天"
                                f"（{_anniv_data.get('today_milestone_description', '')}）。"
                                "回复时可以自然地带一点纪念日气氛，一句小小的真心话即可，"
                                "不要广播式祝福、不要反复提，也不要提及这条提示本身。"
                            )
                            context = f"{_anniv_hint}\n{context}" if context else _anniv_hint
                            _log.info(f"[纪念日] 用户 {user_id} 相识 {_anniv_milestone} 天纪念日，已注入氛围提示")
                    except Exception as e:
                        _log.debug(f"纪念日检查失败（不影响主流程）: {e}")

                    # 发送 thinking 状态（包含 client_message_id 用于前端关联）
                    await websocket.send_json({
                        'type': 'thinking',
                        'status': 'started',
                        'client_message_id': client_message_id,
                    })
                    
                    await websocket.send_json({
                        'type': 'typing',
                        'status': 'typing',
                        'client_message_id': client_message_id,
                    })
                    
                    try:
                        status_updates = []
                        last_status = None
                        
                        def status_callback(status, detail=""):
                            nonlocal last_status
                            status_messages = {
                                'thinking': '🤔 正在分析情感...',
                                'searching': '🔍 正在搜索网络知识...',
                                'verifying': '✅ 正在核实信息...',
                                'typing': '⌨️ 正在组织语言...',
                            }
                            content = detail or status_messages.get(status, '')
                            if content and content != last_status:
                                status_updates.append({
                                    'type': 'thinking_status',
                                    'content': content,
                                    'status': status,
                                    'client_message_id': client_message_id,
                                })
                                last_status = content
                        
                        # 【视觉能力】thinking 已发出，再看图（2-4 秒不黑屏）：
                        # 本地读图 → 视觉模型描述 → 注入对话上下文。
                        # 失败/未配置视觉时降级：喵艺自然表示"没看清"。
                        _vision_desc = ''
                        _vision_ready = app_state.CORE_INSTANCE.is_vision_available()
                        if image_url:
                            _img_path = _resolve_chat_image_path(str(image_url))
                            if _img_path and _vision_ready:
                                try:
                                    _vision_desc = await asyncio.get_event_loop().run_in_executor(
                                        GLOBAL_THREAD_POOL,
                                        app_state.CORE_INSTANCE.describe_image, _img_path)
                                except Exception as _ve:
                                    _log.warning(f"[视觉] 图片描述异常: {_ve}")
                            if _vision_desc:
                                # 【视觉→情感联动】画面内容真实影响心情 + 生成有感而发的感想
                                _feeling = ''
                                try:
                                    _feeling = await asyncio.get_event_loop().run_in_executor(
                                        GLOBAL_THREAD_POOL,
                                        app_state.CORE_INSTANCE.feel_about_image, _vision_desc)
                                except Exception as _fe:
                                    _log.warning(f"[视觉] 情绪反应异常: {_fe}")
                                _vision_hint = (
                                    f"[系统提示] 用户刚刚发来了一张图片，你（喵艺）看到了：{_vision_desc}。"
                                    + (f"看到画面时你心里的真实感受是：{_feeling}。" if _feeling else "")
                                    + "请自然地以喵艺的口吻回应图片内容（好奇/夸赞/吐槽均可，符合当前心情与亲密度），"
                                      "不要提及视觉系统或这条提示本身。"
                                )
                                context = f"{_vision_hint}\n{context}" if context else _vision_hint
                                _log.info(f"[视觉] 图片已注入对话上下文: {_vision_desc[:50]}...")
                            elif not _vision_ready:
                                _vision_hint = ("[系统提示] 用户发来了一张图片，但本服务尚未配置视觉模型，"
                                    "你目前看不了图片。请诚实自然地告诉用户自己还看不了图，"
                                    "请用户用文字描述图片内容，并表达你很想知道的心情。")
                                context = f"{_vision_hint}\n{context}" if context else _vision_hint
                            else:
                                _vision_hint = "[系统提示] 用户发来了一张图片，但你这次没看清内容。可以自然地表示好奇，请用户描述一下或稍后再发一次。"
                                context = f"{_vision_hint}\n{context}" if context else _vision_hint
                            if not text:
                                text = '[图片]'

                        # 持久化用户消息（视觉描述后，text 兜底已完成）
                        try:
                            await asyncio.get_event_loop().run_in_executor(
                                GLOBAL_THREAD_POOL,
                                app_state.MESSAGE_SERVICE.save_user_message, user_id, text, "", image_url
                            )
                        except Exception as e:
                            _log.error(f"保存用户消息失败: {e}")

                        # 【流式回复】chunk 队列 + 泵：executor 线程产生增量，
                        # 主协程经泵逐块推送给前端（避免跨任务并发写 websocket）
                        chunk_queue: asyncio.Queue = asyncio.Queue()
                        _main_loop = asyncio.get_running_loop()

                        def _on_chunk(delta: str):
                            if delta:
                                _main_loop.call_soon_threadsafe(
                                    chunk_queue.put_nowait, delta)

                        async def _pump_chunks():
                            while True:
                                delta = await chunk_queue.get()
                                await websocket.send_json({
                                    'type': 'response_chunk',
                                    'delta': delta,
                                    'client_message_id': client_message_id,
                                })

                        def process_message_sync():
                            return app_state.CORE_INSTANCE.process_message(
                                user_id, text, context, status_callback, on_chunk=_on_chunk)

                        pump_task = asyncio.create_task(_pump_chunks())
                        try:
                            result = await asyncio.wait_for(
                                asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, process_message_sync),
                                timeout=60.0
                            )
                        except asyncio.TimeoutError:
                            _log.warning("[WebSocket] 消息处理超时(60秒)")
                            result = {'text': '喵艺正在努力思考中，请稍候...', 'thinking_log': [], 'emotional_state': {}, 'memory_triggers': [], 'autobiography_context': '', 'response_time_ms': 60000}
                        finally:
                            pump_task.cancel()
                            # 排空残余 chunk（防超时后乱序闪烁）
                            while not chunk_queue.empty():
                                try:
                                    chunk_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    break
                        
                        # 【纪念日仪式感】回复成功发出 → 记录今天已庆祝（幂等），
                        # 避免主动引擎当天再重复庆祝一次。
                        if _anniv_hint:
                            try:
                                await asyncio.get_event_loop().run_in_executor(
                                    GLOBAL_THREAD_POOL,
                                    app_state.CORE_INSTANCE.relationship.celebrate_anniversary,
                                    _anniv_milestone)
                                await asyncio.get_event_loop().run_in_executor(
                                    GLOBAL_THREAD_POOL,
                                    app_state.CORE_INSTANCE.relationship.save)
                            except Exception as e:
                                _log.debug(f"纪念日庆祝标记失败（不影响主流程）: {e}")

                        for status_update in status_updates:
                            await websocket.send_json(status_update)
                        
                        await websocket.send_json({
                            'type': 'typing',
                            'status': 'done',
                            'client_message_id': client_message_id,
                        })
                        _log.info(f"发送 typing done，用户: {user_id}")
                        
                        thinking_log = result.get('thinking_log', [])
                        for step in thinking_log:
                            await websocket.send_json({
                                'type': 'thinking_step',
                                'step': step,
                                'client_message_id': client_message_id,
                            })
                        
                        audio_url = None
                        tts_task = None
                        voice_tip = None  # 【修复 2026-10-01】统一初始化：原先仅在单段分支赋值，多段/异常路径会 UnboundLocalError
                        try:
                            voice_text = result.get('voice_text', '') or result.get('text', '')
                            if voice_text:
                                emotional_state = result.get('emotional_state', {})
                                # 优先使用 dominant_emotion，其次使用 dominant
                                emotion = ''
                                if isinstance(emotional_state, dict):
                                    emotion = emotional_state.get('dominant_emotion', '') or emotional_state.get('dominant', '')
                                # 【首句优先】按句切分，只同步合成第一段让文本回复立即推送；
                                # 剩余段后台合成，前端经 /api/voice/segment 逐段续播
                                # 【修复 2026-10-01】split_tts_segments 是 voice_manager 模块级函数，不是 VoiceManager 实例方法
                                segments = split_tts_segments(voice_text)
                                if len(segments) > 1:
                                    first_path = await asyncio.get_event_loop().run_in_executor(
                                        GLOBAL_THREAD_POOL,
                                        app_state.VOICE_MANAGER.synthesize_to_file, segments[0], emotion)
                                    if first_path and os.path.exists(first_path):
                                        audio_url = f'/voice_cache/{os.path.basename(first_path)}'
                                        seg_task_id = uuid.uuid4().hex[:12]
                                        app_state.VOICE_MANAGER.start_segment_synthesis(
                                            seg_task_id, segments, emotion=emotion, first_path=first_path)
                                        tts_task = {'id': seg_task_id, 'total': len(segments)}
                                else:
                                    audio_path = await asyncio.get_event_loop().run_in_executor(
                                        GLOBAL_THREAD_POOL,
                                        app_state.VOICE_MANAGER.synthesize_to_file, voice_text, emotion)
                                    if audio_path and os.path.exists(audio_path):
                                        audio_url = f'/voice_cache/{os.path.basename(audio_path)}'
                                        voice_tip = None
                        except Exception as e:
                            _log.error(f"TTS生成失败: {e}", exc_info=True)
                        
                        response_data = {
                            'type': 'response',
                            'text': result.get('text', ''),
                            'sticker': result.get('sticker'),
                            'thought': result.get('thought'),
                            'small_thought': result.get('small_thought'),
                            'emotional_state': result.get('emotional_state', {}),
                            'thinking_log': thinking_log,
                            'memory_triggers': result.get('memory_triggers', []),
                            'autobiography_context': result.get('autobiography_context', ''),
                            'audio_url': audio_url,
                            'tts_task': tts_task,
                            'voice_tip': voice_tip,
                            'image_url': result.get('image_url'),
                            'response_time_ms': result.get('response_time_ms', 0),
                            'timestamp': datetime.now().isoformat(),
                            'client_message_id': client_message_id,  # 返回客户端消息 ID
                        }
                        await websocket.send_json(response_data)
                        _log.info(f"发送响应成功，用户: {user_id}, 响应长度: {len(result.get('text', ''))}")

                        # 【P0 修复·数据流统一】持久化 AI 回复（Service 层）
                        try:
                            _reply_text = result.get('text', '')
                            _emo = result.get('emotional_state', {}) or {}
                            _emo_name = _emo.get('dominant_emotion', '') or _emo.get('dominant', '') if isinstance(_emo, dict) else ''
                            await asyncio.get_event_loop().run_in_executor(
                                GLOBAL_THREAD_POOL,
                                app_state.MESSAGE_SERVICE.save_ai_message,
                                user_id, _reply_text, str(_emo_name),
                                int(result.get('response_time_ms', 0) or 0)
                            )
                        except Exception as e:
                            _log.error(f"保存AI回复失败: {e}")
                        
                        emotional_state = result.get('emotional_state', {})
                        if emotional_state:
                            await websocket.send_json({
                                'type': 'emotional_state',
                                **emotional_state
                            })
                            _log.info(f"推送情感状态更新，用户: {user_id}")
                        
                    except Exception as e:
                        # 【日志降噪】用户断开（关页面/网络切换）后发送必然失败，属正常场景；
                        # 仅真实业务异常保留 ERROR 日志，断开类异常降级为 DEBUG
                        _e_str = str(e)
                        _is_disconnect = (
                            isinstance(e, WebSocketDisconnect)
                            or 'ConnectionClosed' in type(e).__name__
                            or 'ClientDisconnected' in type(e).__name__
                            or 'Cannot call "send"' in _e_str
                            or 'close message has been sent' in _e_str
                            or 'Unexpected ASGI message' in _e_str
                        )
                        if _is_disconnect:
                            _log.debug(f"[WebSocket] 连接已断开，跳过响应发送: {_e_str[:120]}")
                        else:
                            _log.error(f"[WebSocket] 消息处理异常: {e}", exc_info=True)
                        try:
                            await websocket.send_json({
                                'type': 'typing',
                                'status': 'done',
                            })
                        except Exception:
                            _log.debug(f"[WS] 连接已断开，跳过 typing done 发送: {str(e)[:80]}")
                        try:
                            await websocket.send_json({
                                'type': 'response',
                                'text': '抱歉，处理您的消息时出现了错误，请稍后重试。',
                                'emotional_state': {},
                                'thinking_log': [],
                                'memory_triggers': [],
                                'autobiography_context': '',
                                'audio_url': None,
                                'response_time_ms': 0,
                                'timestamp': datetime.now().isoformat(),
                                'client_message_id': client_message_id,  # 【P1-5】带回 client_message_id，前端可触发自动重试
                            })
                        except Exception:
                            _log.debug(f"[WS] 连接已断开，跳过错误回复发送: {str(e)[:80]}")
                
                elif msg_type == 'tts':
                    # 【安全修复】tts 通道补输入过滤 + 限长 + 限频（旧版裸奔可无限消耗合成算力）
                    tts_text = sanitize_input(str(data.get('text', '') or ''), max_length=2000)
                    if not tts_text:
                        await websocket.send_json({'type': 'tts_result', 'error': '文本为空或包含不允许的字符'})
                    elif not _ws_rate_ok(user_id, ws_tts_times, window=60, max_n=12):
                        await websocket.send_json({'type': 'tts_result', 'error': '合成请求太频繁，歇一小会儿喵～'})
                    else:
                        try:
                            def synthesize_sync():
                                return app_state.VOICE_MANAGER.synthesize_to_file(tts_text)
                            audio_path = await asyncio.get_event_loop().run_in_executor(GLOBAL_THREAD_POOL, synthesize_sync)
                            if audio_path and os.path.exists(audio_path):
                                await websocket.send_json({
                                    'type': 'tts_result',
                                    'audio_url': f'/voice_cache/{os.path.basename(audio_path)}',
                                })
                            else:
                                await websocket.send_json({
                                    'type': 'tts_result',
                                    'error': 'TTS合成失败',
                                })
                        except Exception as e:
                            await websocket.send_json({
                                'type': 'tts_result',
                                'error': str(e),
                            })

                elif msg_type == 'thinking_status':
                    await websocket.send_json({
                        'type': 'thinking_status',
                        'content': data.get('content', ''),
                    })

                elif msg_type == 'voice_text':
                    # 【通话】通话中前端 ASR 文本 → 喵艺回复 + 字幕 + TTS 播报
                    # 用 create_task 避免阻塞主 WS 消息循环（LLM+TTS 耗时）
                    # 【安全修复】补输入过滤 + 限长 + 限频（ASR 每 3s 一段属正常节奏，60s/30 次足够）
                    vt = sanitize_input(str(data.get('text', '') or ''), max_length=2000)
                    if not _ws_rate_ok(user_id, ws_voice_times, window=60, max_n=30):
                        _log.warning(f"[WS] voice_text 超频，已丢弃 user: {user_id}")
                    elif vt and app_state.CALL_MANAGER is not None:
                        try:
                            _spawn_background_task(app_state.CALL_MANAGER.bot_reply_to_voice(user_id, str(vt)), name='bot_reply_to_voice')
                        except Exception as e:
                            _log.warning(f"[WS] voice_text 处理失败: {e}")

                elif msg_type == 'barge_in':
                    # 【barge-in 语音打断】前端 Silero VAD 检测到用户在喵艺播报期间开口说话，
                    # 后端作废旧回合：正在流式推送的字幕/TTS 立即终止，不再浪费 LLM 与合成算力
                    if app_state.CALL_MANAGER is not None:
                        try:
                            await app_state.CALL_MANAGER.handle_barge_in(user_id)
                        except Exception as e:
                            _log.warning(f"[WS] barge_in 处理失败: {e}")
    
    except WebSocketDisconnect:
        _log.info(f"WebSocket断开(正常)，用户: {user_id}")
        connection_manager.disconnect(user_id, websocket=websocket, is_reconnect=False)
    except Exception as e:
        _log.error(f"WebSocket连接错误: {e}", exc_info=True)
        connection_manager.disconnect(user_id, websocket=websocket, is_reconnect=True)
    finally:
        pass

voice_cache_dir = Path(_resolve_dir('assets/voice_cache', 'voice_cache'))
voice_cache_dir.mkdir(exist_ok=True)
sticker_dir = Path(_resolve_dir('assets/stickers_src', '表情包存放'))

def find_available_port(host: str, start_port: int, max_port: int) -> int:
    for port in range(start_port, max_port + 1):
        # 【2026-10 修复】systemd restart 时旧进程可能还没完全释放首选端口
        # （旧 socket 仍 LISTEN 或 TIME_WAIT）。旧逻辑立刻让到 8002 并写
        # .server-port，导致云端固定入口 8001 丢失。首选端口最多等 10 秒。
        attempts = 20 if port == start_port else 1
        freed = False
        for _ in range(attempts):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind((host, port))
                    freed = True
                    break
            except OSError:
                time.sleep(0.5)
        if freed:
            return port
    return -1

def run_server(host: str = "127.0.0.1", port: int = 8000, max_port: int = 8100, debug: bool = False):
    available_port = find_available_port(host, port, max_port)
    
    if available_port == -1:
        raise RuntimeError(
            f"端口范围 {port}~{max_port} 内所有端口都被占用！\n"
            f"请使用以下命令查看并结束占用进程：\n"
            f"  netstat -ano | findstr :{port}\n"
            f"  taskkill /F /PID <进程ID>\n"
            f"或修改 config.yaml 中的 server.port 配置"
        )
    
    if available_port != port:
        _log.warning(f"端口 {port} 已被占用，自动切换到端口 {available_port}")
        _log.warning(f"⚠️ 前端代理可能指向旧端口 {port}，如连接失败请重启前端 vite dev server")
        _log.warning(f"前端重启后将自动从 .server-port 文件读取新端口 {available_port}")
    
    # 显示用地址：host 为 0.0.0.0（监听所有网卡）时替换为 127.0.0.1，
    # 避免 PyCharm 控制台里的可点击链接打开 http://0.0.0.0:8001 导致浏览器超时；
    # 且优先显示 127.0.0.1 而非 localhost —— Windows 上浏览器访问 localhost 会优先尝试
    # IPv6 的 ::1，而 uvicorn 仅监听 IPv4（0.0.0.0），IPv6 回环可能被防火墙/代理静默丢弃
    # 形成黑洞连接，浏览器长时间等待直至超时；curl 因 Happy Eyeballs 快速回落不受影响。
    _display_host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
    _log.info(f"启动喵艺服务器: http://{_display_host}:{available_port}  ← 浏览器访问这个地址")
    _log.info(f"WebSocket 地址: ws://{_display_host}:{available_port}/ws")
    _log.info(f"API 基础路径: http://{_display_host}:{available_port}/api/")
    if host == '0.0.0.0':
        _log.info(f"局域网访问（手机等设备）: http://{LOCAL_IP}:{available_port}")
    
    # 将端口写入文件供前端 Vite 读取
    port_file = Path(_resolve_dir('frontend', 'mobile-chat')) / '.server-port'
    port_file.write_text(str(available_port), encoding='utf-8')
    _log.info(f"端口信息已写入: {port_file}")
    
    try:
        uvicorn.run(
            app,
            host=host,
            port=available_port,
            reload=False,
            log_level="info",
        )
    finally:
        # 服务器关闭时删除端口文件
        if port_file.exists():
            port_file.unlink()
            _log.info("端口信息文件已删除")

if __name__ == "__main__":
    host = config_manager.get('server.host', '127.0.0.1')
    port = config_manager.get('server.port', 8000)
    max_port = config_manager.get('server.max_port_try', 8100)
    debug = config_manager.get('server.debug', False)
    
    run_server(host, port, max_port, debug)
