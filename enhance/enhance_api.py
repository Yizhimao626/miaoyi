"""
喵艺 AI 伴侣 · 增强 API 路由（独有端点）
======================================
【P0-2 修复】本文件原与 enhance/api_routes.py 大量路由重复（/call/*、/proactive/*、
/ws/call/*、/health），全部被 api_routes.py 先注册遮蔽，永不会执行，反而造成：
  - /call/history 用 core 版 CallManager，查不到 enhance 版的通话记录
  - /health 与 server.py 的 /api/health 冲突，HEAD 返回 405（前端在线检测误判断网）
  - /ws/call 用 json 未 import，NameError
现已删除所有重复路由，只保留本文件独有的端点：
  - /call/history          通话历史（改用 enhance 版 CallManager）
  - /proactive/status      主动引擎状态
  - /crawler/*             爬虫
  - /persona/*             人设
  - /system/*              系统
通话控制（dial/answer/decline/hangup）统一走 enhance/api_routes.py；
通话中 voice_text 统一走 server.py 主 WS。避免双 CallManager 实例。
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import app_state
from core.auth import decode_token
from core.cache import get_cache
from core.persona import (
    build_system_prompt,
    detect_ai_probe,
    get_all_scenarios,
    get_level_configs,
    level_from_intimacy,
)
from core.token_counter import get_counter
from security.error_handler import MiaoyiException, ValidationException

_log = logging.getLogger("EnhanceAPI")

router = APIRouter()


# ============================================================
# 认证辅助：从请求头/Cookie 提取 user_id（前端不传 user_id，统一从 token 解析）
# ============================================================

def _get_user_id_from_request(request: Request) -> str:
    """
    从 Authorization 头或 Cookie 中解析当前登录用户 ID。

    【历史教训】前端 callStore.ts 用项目统一的 request() 封装，只带 Bearer token，
    不在 body 里传 user_id。旧版后端从 body.get('user_id') 取值必然为空 → USER_ID_REQUIRED。
    修复：统一从 token 解析 user_id，与 server.py 的 get_current_user 逻辑一致。
    """
    token = request.headers.get('Authorization', '')
    if token.startswith('Bearer '):
        token = token[7:]
    if not token:
        token = request.cookies.get('miaoyi_token', '')

    if not token:
        raise HTTPException(status_code=401, detail="喵～请先登录")

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="喵～登录已过期，请重新登录")

    user_id = str(payload.get('user_id') or payload.get('sub') or '')
    if not user_id:
        raise HTTPException(status_code=401, detail="喵～Token无效，请重新登录")

    return user_id


# ============================================================
# 辅助函数
# ============================================================

def _build_response(code: int = 200, data: Any = None, message: str = "ok") -> dict[str, Any]:
    """构建统一响应"""
    return {"code": code, "data": data, "message": message}


def _get_call_manager():
    """获取 enhance 版通话管理器（统一实例，由 init_enhance 注入 app_state.CALL_MANAGER）。

    【P0-2 修复】不再创建 core 版 CallManager（无 LLM/TTS，事件格式还不兼容）。
    """
    mgr = app_state.CALL_MANAGER
    if mgr is None:
        # 兜底：直接从 enhance 模块取
        try:
            from enhance.api_routes import _get as _enhance_get
            mgr = _enhance_get("call")
        except Exception:
            mgr = None
    if mgr is None:
        raise MiaoyiException(message="通话服务未就绪", code="CALL_NOT_READY", status_code=503)
    return mgr


def _get_proactive_engine():
    """获取 enhance 版主动行为引擎（已由 server.py lifespan 启动）。"""
    try:
        from enhance.api_routes import _get as _enhance_get
        return _enhance_get("proactive")
    except Exception:
        return app_state.PROACTIVE_ENGINE


def _get_web_crawler():
    """获取爬虫实例"""
    if app_state.WEB_CRAWLER is None:
        from core.web_crawler import WebCrawler
        app_state.WEB_CRAWLER = WebCrawler()
    return app_state.WEB_CRAWLER


def _get_persona_system():
    """获取人设系统状态"""
    return {
        "level_configs": get_level_configs(),
        "scenarios": list(get_all_scenarios().keys()),
    }


# ============================================================
# 1. 通话 API（仅历史查询；dial/answer/decline/hangup 在 api_routes.py）
# ============================================================

@router.get("/call/history")
async def call_history(request: Request, limit: int = 20):
    """获取通话历史（只能看自己的）。"""
    try:
        user_id = _get_user_id_from_request(request)
    except MiaoyiException:
        user_id = ""
    manager = _get_call_manager()
    records = manager.get_history(user_id=user_id, limit=limit)
    # 兼容：get_history 可能返回 list[dict]（enhance版）或 list[CallSession]（旧版）
    if records and not isinstance(records[0], dict):
        records = [r.to_dict() for r in records]
    return _build_response(data=records)


# ============================================================
# 2. 主动行为 API（仅状态查询；trigger/preview 在 api_routes.py）
# ============================================================

@router.get("/proactive/status")
async def proactive_status(request: Request):
    """获取主动行为引擎状态。"""
    _get_user_id_from_request(request)  # 强制认证
    engine = _get_proactive_engine()
    stats = getattr(engine, "stats", None)
    if stats is None and hasattr(engine, "get_stats"):
        stats = engine.get_stats()
    if stats is None:
        stats = {"running": getattr(engine, "is_running", False)}
    return _build_response(data=stats)


# ============================================================
# 3. 爬虫 API
# ============================================================

@router.get("/crawler/weather")
async def crawler_weather(request: Request, city: str = "北京"):
    """获取天气信息"""
    _get_user_id_from_request(request)  # 强制认证
    crawler = _get_web_crawler()
    try:
        result = await crawler.crawl_weather(city)
        return _build_response(data={"city": city, "weather": result})
    except Exception as e:
        _log.error(f"天气获取失败: {e}")
        raise MiaoyiException(message=f"天气获取失败: {city}", code="CRAWL_FAILED", status_code=502)


@router.get("/crawler/news")
async def crawler_news(request: Request, category: str = "general", limit: int = 8):
    """获取新闻"""
    _get_user_id_from_request(request)  # 强制认证
    crawler = _get_web_crawler()
    try:
        result = await crawler.crawl_news(category=category, limit=limit)
        return _build_response(data={"news": result})
    except Exception as e:
        _log.error(f"新闻获取失败: {e}")
        raise MiaoyiException(message="新闻获取失败", code="CRAWL_FAILED", status_code=502)


@router.get("/crawler/hot")
async def crawler_hot(request: Request, limit: int = 15):
    """获取热搜"""
    _get_user_id_from_request(request)  # 强制认证
    crawler = _get_web_crawler()
    try:
        result = await crawler.crawl_hot(limit=limit)
        return _build_response(data={"hot_topics": result})
    except Exception as e:
        _log.error(f"热搜获取失败: {e}")
        raise MiaoyiException(message="热搜获取失败", code="CRAWL_FAILED", status_code=502)


@router.get("/crawler/holidays")
async def crawler_holidays(request: Request):
    """获取节假日信息"""
    _get_user_id_from_request(request)  # 强制认证
    crawler = _get_web_crawler()
    try:
        result = await crawler.crawl_holidays()
        return _build_response(data={"holidays": result})
    except Exception as e:
        _log.error(f"节假日获取失败: {e}")
        raise MiaoyiException(message="节假日获取失败", code="CRAWL_FAILED", status_code=502)


@router.get("/crawler/exchange")
async def crawler_exchange(request: Request, base: str = "CNY"):
    """获取汇率"""
    _get_user_id_from_request(request)  # 强制认证
    crawler = _get_web_crawler()
    try:
        result = await crawler.crawl_exchange(base=base)
        return _build_response(data={"exchange": result})
    except Exception as e:
        _log.error(f"汇率获取失败: {e}")
        raise MiaoyiException(message="汇率获取失败", code="CRAWL_FAILED", status_code=502)


@router.get("/crawler/cache/stats")
async def crawler_cache_stats(request: Request):
    """获取爬虫缓存统计"""
    _get_user_id_from_request(request)  # 强制认证
    cache = get_cache()
    return _build_response(data=cache.get_stats())


# ============================================================
# 4. 人设 API
# ============================================================

class PersonaPromptRequest(BaseModel):
    user_text: str = Field(default="", max_length=2000, description="用户输入")
    intimacy: float = Field(default=50.0, ge=0.0, le=100.0, description="亲密度")
    relation_days: int = Field(default=0, ge=0, description="交往天数")


@router.post("/persona/build-prompt")
async def persona_build_prompt(request: Request):
    """构建系统提示词（人设）"""
    _get_user_id_from_request(request)  # 强制认证
    try:
        body = await request.json()
    except Exception:
        raise ValidationException(message="请求体格式错误", code="BODY_PARSE_ERROR")

    user_text = body.get("user_text", "")
    intimacy = body.get("intimacy", 50.0)
    relation_days = body.get("relation_days", 0)
    mood = body.get("mood", None)

    prompt = build_system_prompt(
        intimacy=float(intimacy),
        relation_days=int(relation_days),
        mood=mood,
        user_text=user_text,
    )

    ai_detected = detect_ai_probe(user_text) if user_text else False

    return _build_response(
        data={
            "prompt": prompt,
            "level": level_from_intimacy(intimacy).value,
            "ai_probe_detected": ai_detected,
        }
    )


@router.get("/persona/levels")
async def persona_levels(request: Request):
    """获取亲密度等级配置"""
    _get_user_id_from_request(request)  # 强制认证（补齐）
    return _build_response(data=get_level_configs())


@router.get("/persona/scenarios")
async def persona_scenarios(request: Request):
    """获取主动场景配置"""
    _get_user_id_from_request(request)  # 强制认证（补齐）
    return _build_response(data=list(get_all_scenarios().keys()))


# ============================================================
# 5. 系统 API
# ============================================================

@router.get("/system/info")
async def system_info(request: Request):
    """获取系统信息"""
    _get_user_id_from_request(request)  # 强制认证
    counter = get_counter()
    return _build_response(
        data={
            "system": "喵艺 AI 伴侣",
            "version": "2.0.0",
            "environment": __import__('os').environ.get('ENVIRONMENT', 'development'),
            "token_stats": counter.get_total_stats(),
            "persona": _get_persona_system(),
        }
    )


@router.get("/system/token-stats")
async def token_stats(request: Request):
    """获取 Token 使用统计（仅本人；全局汇总附在 total 字段）"""
    uid = _get_user_id_from_request(request)  # 强制认证
    counter = get_counter()

    # 【P1安全修复】旧版 user_id 从 query 取，任何登录用户可查任意用户用量/配额（IDOR）；
    # 现强制使用 token 解析出的用户，全局汇总仅作为 total 字段附带返回。
    usage = counter.get_usage(uid)
    quota = counter.check_quota(uid)
    return _build_response(data={"user_id": uid, "usage": usage, "quota": quota, "total": counter.get_total_stats()})
