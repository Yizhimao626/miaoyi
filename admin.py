"""管理后台API路由

接口清单（全部需要管理员 JWT）：
  GET    /api/admin/dashboard              看板统计（核心计数 + 7天趋势 + 系统状态）
  GET    /api/admin/users                  用户列表
  POST   /api/admin/users/{id}/role        修改用户角色
  POST   /api/admin/users/{id}/status      启用/禁用用户
  POST   /api/admin/users/{id}/reset-password  重置用户密码
  DELETE /api/admin/users/{id}             删除用户
  GET    /api/admin/messages               全站消息分页查询（支持关键词）
  GET    /api/admin/feedback               用户反馈分页查询
  DELETE /api/admin/feedback/{id}          删除反馈
  GET    /api/admin/config                 查看配置（敏感字段脱敏）
  GET    /api/admin/stats                  数据库/系统状态
"""
from typing import Dict

import jwt
import psutil
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

import app_state
from core.auth import get_jwt_secret_key, hash_password, validate_security_config

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 使用统一的安全配置验证
validate_security_config()
JWT_SECRET_KEY = get_jwt_secret_key()
JWT_ALGORITHM = 'HS256'


def _mask_sensitive_config(config: dict) -> dict:
    """屏蔽敏感配置字段"""
    sensitive_keys = ['api_key', 'secret', 'password', 'token', '_api_key']
    result = {}

    for key, value in config.items():
        if isinstance(value, dict):
            result[key] = _mask_sensitive_config(value)
        elif isinstance(value, str):
            should_mask = any(sk.lower() in key.lower() for sk in sensitive_keys)
            result[key] = '***' if should_mask else value
        else:
            result[key] = value

    return result


def _ok(data=None, message: str = '成功'):
    return JSONResponse({"code": 200, "data": data, "message": message})


def _err(message: str, code: int = 500):
    return JSONResponse({"code": code, "data": None, "message": message}, status_code=code)


async def _require_admin(request: Request):
    token = request.headers.get('Authorization', '')
    if not token.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='未授权访问，请登录')

    token = token[7:]
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get('role') not in ('admin', 'founder'):
            raise HTTPException(status_code=403, detail='权限不足')
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail='Token无效或已过期')


# ==================== 看板 ====================

@router.get("/dashboard")
async def get_dashboard(current_user: dict = Depends(_require_admin)):
    """看板：核心计数 + 近7天消息趋势 + 系统状态"""
    try:
        db = app_state.DB_INSTANCE
        data = db.get_admin_dashboard()

        process = psutil.Process()
        memory_info = process.memory_info()
        data['system'] = {
            'memory_usage_mb': round(memory_info.rss / (1024 * 1024), 2),
            'cpu_percent': process.cpu_percent(interval=0.1),
            'threads': process.num_threads(),
        }
        return _ok(data)
    except Exception as e:
        return _err(f"获取看板数据失败: {str(e)}")


# ==================== 用户管理 ====================

@router.get("/users")
async def get_users(
    page: int = 1,
    page_size: int = 10,
    search: str = '',
    current_user: dict = Depends(_require_admin)
):
    """用户管理列表：服务端分页 + 用户名/手机号模糊搜索"""
    try:
        data = app_state.DB_INSTANCE.get_users_page(
            page=page, page_size=page_size, search=search)
        data['page'] = max(1, page)
        data['page_size'] = min(max(1, page_size), 100)
        return _ok(data)
    except Exception as e:
        return _err(f"获取用户列表失败: {str(e)}")


@router.post("/users/{user_id}/role")
async def update_user_role(
    user_id: int,
    role: str,
    current_user: dict = Depends(_require_admin)
):
    try:
        if role not in ['user', 'admin']:
            return _err('无效的角色类型', 400)
        # 不允许修改自己的角色（防止唯一管理员降级后锁死）
        if user_id == current_user.get('user_id'):
            return _err('不能修改自己的角色', 400)
        app_state.DB_INSTANCE.update_user_role(user_id, role)
        return _ok({'user_id': user_id, 'role': role}, '角色更新成功')
    except Exception as e:
        return _err(f"更新角色失败: {str(e)}")


@router.post("/users/{user_id}/status")
async def update_user_status(
    user_id: int,
    status: str,
    current_user: dict = Depends(_require_admin)
):
    """启用/禁用用户（disabled 用户将被拒绝登录）"""
    try:
        if status not in ['active', 'disabled']:
            return _err('无效的状态类型', 400)
        if user_id == current_user.get('user_id'):
            return _err('不能禁用自己的账号', 400)
        ok = app_state.DB_INSTANCE.update_user_status(user_id, status)
        if not ok:
            return _err('用户不存在或状态未变化', 404)
        return _ok({'user_id': user_id, 'status': status}, '状态更新成功')
    except Exception as e:
        return _err(f"更新状态失败: {str(e)}")


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int,
    body: dict,
    current_user: dict = Depends(_require_admin)
):
    """管理员重置用户密码"""
    try:
        new_password = str(body.get('password', ''))
        if len(new_password) < 8:
            return _err('密码长度至少 8 位', 400)
        ok = app_state.DB_INSTANCE.reset_user_password(user_id, hash_password(new_password))
        if not ok:
            return _err('用户不存在', 404)
        return _ok({'user_id': user_id}, '密码重置成功')
    except Exception as e:
        return _err(f"重置密码失败: {str(e)}")


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: dict = Depends(_require_admin)
):
    try:
        if user_id == current_user.get('user_id'):
            return _err('不能删除自己的账号', 400)
        app_state.DB_INSTANCE.delete_user(user_id)
        # 联动清理独立情感持久化库（data/emotion.db）
        if app_state.EMOTION_PERSISTENCE:
            app_state.EMOTION_PERSISTENCE.delete_user(str(user_id))
        return _ok({'user_id': user_id}, '用户已删除')
    except Exception as e:
        return _err(f"删除用户失败: {str(e)}")


# ==================== 消息管理 ====================

@router.get("/messages")
async def get_messages(
    page: int = 1,
    page_size: int = 20,
    keyword: str = '',
    current_user: dict = Depends(_require_admin)
):
    """全站消息分页查询（keyword 匹配内容或会话ID）"""
    try:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        data = app_state.DB_INSTANCE.get_admin_messages(page, page_size, keyword.strip())
        return _ok(data)
    except Exception as e:
        return _err(f"获取消息失败: {str(e)}")


# ==================== 反馈管理 ====================

@router.get("/feedback")
async def get_feedback(
    page: int = 1,
    page_size: int = 20,
    current_user: dict = Depends(_require_admin)
):
    try:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        data = app_state.DB_INSTANCE.get_admin_feedback(page, page_size)
        return _ok(data)
    except Exception as e:
        return _err(f"获取反馈失败: {str(e)}")


@router.delete("/feedback/{feedback_id}")
async def delete_feedback(
    feedback_id: int,
    current_user: dict = Depends(_require_admin)
):
    try:
        ok = app_state.DB_INSTANCE.delete_feedback(feedback_id)
        if not ok:
            return _err('反馈不存在', 404)
        return _ok({'feedback_id': feedback_id}, '反馈已删除')
    except Exception as e:
        return _err(f"删除反馈失败: {str(e)}")


# ==================== 配置与系统状态 ====================

@router.get("/config")
async def get_config(current_user: dict = Depends(_require_admin)):
    try:
        from utils.config_manager import config_manager
        config = config_manager.get_all()
        masked_config = _mask_sensitive_config(config)
        return _ok(masked_config)
    except Exception as e:
        return _err(f"获取配置失败: {str(e)}")


@router.get("/stats")
async def get_stats(current_user: dict = Depends(_require_admin)):
    try:
        db_stats = app_state.DB_INSTANCE.get_stats()

        process = psutil.Process()
        memory_info = process.memory_info()

        stats = {
            "database": db_stats,
            "system": {
                "memory_usage_mb": round(memory_info.rss / (1024 * 1024), 2),
                "cpu_percent": process.cpu_percent(interval=0.1),
                "threads": process.num_threads()
            }
        }
        return _ok(stats)
    except Exception as e:
        return _err(f"获取系统状态失败: {str(e)}")
