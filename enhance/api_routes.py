"""
喵艺 · 人性化增强 —— FastAPI 路由与 WebSocket 接入
把 persona / proactive_engine / call_manager / voice_pipeline 接到现有 app。

【ADAPT 重点】本文件中的 LLMAdapter / TTSAdapter / ContextAdapter / WSNotifier
需要你根据 Phase 0 读到的真实函数签名修改，把占位调用替换成项目里真实的函数。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

import logging

_log = logging.getLogger("MiaoyiCall")

from .core.call_manager import CallManager, CallState
from .core.proactive_engine import ProactiveConfig, ProactiveEngine, UserCtx, decide_scenario
from .core.voice_pipeline import VoicePipeline

router = APIRouter()


# ============================================================
# 适配器：把现有 LLM / TTS / 记忆 / WebSocket 适配进来
# 已根据 Phase 0 调研的真实函数签名对接：
#   - LLM: core.miaoyi_core.MiaoyiCore._safe_llm_call(messages:list)->str （同步）
#   - TTS: core.voice_manager.VoiceManager.synthesize_to_file(text,emotion)->str路径 （同步）
#   - DB:   db_manager.DBManager （users表有created_ts，conversations表有last_active_ts）
#   - 情感: core.emotional_core.EmotionalCore （intimacy属性0-100, get_state(), _emotional_vector）
#   - WS:   server.connection_manager.ConnectionManager.send_to_user(uid, dict)
#           需新增 send_bytes_to_user（Phase 3 在 server.py 补）
# ============================================================

class LLMAdapter:
    """对接现有的 GLM-4-Flash 调用。
    真实函数 MiaoyiCore._safe_llm_call 是【同步】的、收 messages 列表、返回完整字符串。
    用 asyncio.to_thread 包裹避免阻塞事件循环；chat_stream 降级为一次性 yield
    （项目原有流式走 SSE 拼接，无现成 async generator，这里保持单次返回以保证稳定性）。
    """
    def __init__(self, core):
        # core: MiaoyiCore 实例（app_state.CORE_INSTANCE）
        self._core = core

    async def chat(self, system_prompt: str, user_prompt: str = "") -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        if not messages:
            return ""
        # 【GLM 1214 修复】GLM 拒绝"纯 system 消息数组"（无 user 角色会报
        # code 1214 messages 参数非法，并触发 _safe_llm_call 熔断器连锁失效）。
        # 调用方可能只传 system_prompt（如主动消息生成），此处兜底补占位 user。
        if not any(m["role"] == "user" for m in messages):
            messages.append({"role": "user", "content": "请根据上面的设定生成内容。"})
        try:
            return await asyncio.to_thread(self._core._safe_llm_call, messages)
        except Exception as e:
            print(f"[LLMAdapter] chat error: {e}")
            return ""

    async def chat_stream(self, system_prompt: str, user_prompt: str):
        # 降级：一次性生成后整体 yield（保证接口签名兼容 call_manager 的 async for）
        full = await self.chat(system_prompt, user_prompt)
        if full:
            yield full


class TTSAdapter:
    """对接现有的晓晓 TTS。
    真实函数 VoiceManager.synthesize_to_file 是【同步】的、返回文件路径（不是 bytes）。
    用 to_thread 包裹，拿到路径后读成 bytes 返回，符合 call_manager 期望的 bytes。
    """
    def __init__(self, voice_manager):
        # voice_manager: VoiceManager 实例（app_state.VOICE_MANAGER）
        self._vm = voice_manager

    async def synthesize(self, text: str) -> bytes:
        if not text or not text.strip():
            return b""
        try:
            path = await asyncio.to_thread(self._vm.synthesize_to_file, text)
            if not path:
                return b""
            def _read():
                with open(path, "rb") as f:
                    return f.read()
            return await asyncio.to_thread(_read)
        except Exception as e:
            print(f"[TTSAdapter] synthesize error: {e}")
            return b""


class ContextAdapter:
    """对接现有的记忆/情感/用户模块。
    - 亲密度：从 CORE_INSTANCE._emotional_core.intimacy（0-100 内存值）
    - 相识天数：users.created_ts 至今
    - 最后活跃：conversations 表 last_active_ts
    - mood：emotional_core.get_state() 映射为 {joy,arousal,intimacy,trust}（0-1）
    - mark_bot_spoke：db.add_message 存 assistant 消息
    - bump_mood：emotional_core.update_intimacy + 直接改 _emotional_vector
    """
    def __init__(self, db, core):
        self.db = db
        self._core = core

    def _emotion_core(self):
        return getattr(self._core, "_emotional_core", None)

    def _mood_snapshot(self) -> dict:
        """把 emotional_core 的情感向量映射为 persona 需要的四维 (0-1)。"""
        ec = self._emotion_core()
        if not ec:
            return {"joy": 0.5, "arousal": 0.5, "intimacy": 0.3, "trust": 0.4}
        try:
            state = ec.get_state()
            vec = state.get("emotional_vector", {})
            joy = min(1.0, max(0.0,
                vec.get("joy", 0) + vec.get("love", 0) + vec.get("gratitude", 0)))
            arousal = min(1.0, max(0.0,
                vec.get("excitement", 0) + vec.get("anger", 0) +
                vec.get("fear", 0) + vec.get("surprise", 0)))
            intimacy_01 = min(1.0, max(0.0, ec.intimacy / 100.0))
            # 信任度项目无直接字段，用 intimacy + 低负面情绪近似
            neg = vec.get("anger", 0) + vec.get("fear", 0)
            trust = min(1.0, max(0.0, intimacy_01 * 0.7 + (1 - neg) * 0.3))
            return {"joy": joy, "arousal": arousal, "intimacy": intimacy_01, "trust": trust}
        except Exception:
            return {"joy": 0.5, "arousal": 0.5, "intimacy": 0.3, "trust": 0.4}

    async def get(self, user_id: str) -> UserCtx:
        ec = self._emotion_core()
        intimacy = float(ec.intimacy) if ec else 30.0

        # 相识天数 + 最后活跃时间 + 最后喵艺消息时间 + 最近记忆（同步 DB，用 to_thread 包裹）
        def _query_user():
            relation_days = 1
            last_active_ts = None
            last_bot_ts = None
            memory_context = ""
            recent_bot_text = ""
            try:
                # users.created_ts → 相识天数
                u = self.db.get_user_by_id(int(user_id)) if str(user_id).isdigit() else None
                if u and u.get("created_ts"):
                    relation_days = max(1, int((time.time() - u["created_ts"]) / 86400))
            except Exception:
                pass
            try:
                # 【人性化】最近记忆：让主动消息能提到上次聊过的内容（真人感）
                mems = self.db.get_memories(limit=3, user_id=str(user_id))
                pieces = []
                for m in mems:
                    text = (m.get("value") or m.get("content") or "").strip()
                    if text:
                        pieces.append(text[:60])
                memory_context = "；".join(pieces[:3])
            except Exception:
                pass
            try:
                # conversations.last_active_ts → 用户最后活跃
                convs = self.db.get_conversations(limit=50)
                for c in convs:
                    # session_id 形如 "{user_id}_..."，匹配当前用户
                    if str(c.get("session_id", "")).startswith(str(user_id)):
                        last_active_ts = c.get("last_active_ts")
                        break
            except Exception:
                pass
            try:
                # 最近一条 assistant 消息时间 → 喵艺最后发言；文本 → 回旋镖话题
                msgs = self.db.get_messages(f"{user_id}_default", limit=50)
                for m in reversed(msgs):
                    if m.get("role") == "assistant":
                        last_bot_ts = m.get("created_ts")
                        txt = (m.get("text") or m.get("content") or "").strip()
                        if txt:
                            recent_bot_text = txt[:80]
                        break
            except Exception:
                pass
            return relation_days, last_active_ts, last_bot_ts, memory_context, recent_bot_text

        relation_days, last_active_ts, last_bot_ts, memory_context, recent_bot_text = await asyncio.to_thread(_query_user)

        now = datetime.utcnow()
        last_active = datetime.utcfromtimestamp(last_active_ts) if last_active_ts else now
        last_bot = datetime.utcfromtimestamp(last_bot_ts) if last_bot_ts else None

        return UserCtx(
            user_id=str(user_id),
            intimacy=intimacy,
            relation_days=relation_days,
            mood=self._mood_snapshot(),
            last_active_at=last_active,
            last_bot_msg_at=last_bot,
            online=True,
            memory_context=memory_context,
            recent_bot_text=recent_bot_text,
            user_distress=self._user_distress_snapshot(),
            anniversary=self._anniversary_snapshot(),
        )

    async def get_all_active(self):
        """返回当前 WebSocket 在线用户 id 列表（用 connection_manager.active_connections）。
        由 init_enhance 时通过 connection_manager 注入更准确，这里先从 db 取最近活跃用户兜底。
        """
        try:
            cm = getattr(self, "_connection_manager", None)
            if cm is not None:
                return list(cm.active_connections.keys())
        except Exception:
            pass
        # 兜底：只取"最近 7 天登录过的真实用户"，避免给从未活跃的用户（含 default 兜底账号）
        # 发送幽灵主动消息。无在线连接时主动行为应当克制，而不是全量轰炸。
        def _recent():
            try:
                cutoff = time.time() - 7 * 86400
                return [
                    str(u["id"])
                    for u in self.db.get_all_users()
                    if (u.get("last_login_ts") or 0) >= cutoff
                ]
            except Exception:
                return []
        return await asyncio.to_thread(_recent)

    async def mark_bot_spoke(self, user_id: str, text: str):
        """存一条 assistant 消息。session_id 约定为 '{user_id}_default'。"""
        def _save():
            try:
                session_id = f"{user_id}_default"
                self.db.add_message(session_id, "assistant", text)
            except Exception as e:
                print(f"[ContextAdapter] mark_bot_spoke error: {e}")
        await asyncio.to_thread(_save)

    def _user_distress_snapshot(self) -> dict | None:
        """【情绪化主动关心】从 core 读用户情绪低点（内存态；无/过期/已回访 → None）"""
        try:
            getter = getattr(self._core, "get_user_distress", None)
            return getter() if getter else None
        except Exception:
            return None

    def _anniversary_snapshot(self) -> dict | None:
        """【纪念日仪式感】今天是相识里程碑日且尚未庆祝 → 返回纪念日信息，否则 None。"""
        try:
            rel = getattr(self._core, "relationship", None)
            if not rel or not hasattr(rel, "get_relationship_anniversary"):
                return None
            anniv = rel.get_relationship_anniversary()
            m = anniv.get("today_milestone")
            if not m:
                return None
            return {
                "milestone": m,
                "days_together": anniv.get("days_together", 0),
                "description": anniv.get("today_milestone_description") or "",
                "message": anniv.get("today_milestone_message") or "",
            }
        except Exception:
            return None

    async def mark_care_delivered(self, user_id: str):
        """【情绪化主动关心】关心消息发出后标记已回访，避免对同一件事重复关心"""
        try:
            marker = getattr(self._core, "mark_distress_followed_up", None)
            if marker:
                marker()
        except Exception as e:
            print(f"[ContextAdapter] mark_care_delivered error: {e}")

    async def mark_anniversary_celebrated(self, user_id: str, milestone: int):
        """【纪念日仪式感】庆祝消息发出后记录该里程碑已庆祝（幂等），并落盘。"""
        def _mark():
            try:
                rel = getattr(self._core, "relationship", None)
                if rel and milestone and hasattr(rel, "celebrate_anniversary"):
                    rel.celebrate_anniversary(int(milestone))
                    rel.save()
            except Exception as e:
                print(f"[ContextAdapter] mark_anniversary_celebrated error: {e}")
        await asyncio.to_thread(_mark)

    async def bump_mood(self, user_id: str, **deltas):
        """调整情感四维。deltas 形如 joy=0.1, arousal=-0.2。
        项目 emotional_core 没有 apply_delta，直接改 _emotional_vector + update_intimacy。
        """
        ec = self._emotion_core()
        if not ec:
            return
        try:
            vec_map = {"joy": "joy", "arousal": "excitement", "intimacy": None, "trust": None}
            def _apply():
                with ec._lock:
                    for k, v in deltas.items():
                        if k == "intimacy":
                            ec.intimacy = max(0, min(100, ec.intimacy + v * 100))
                            ec._dirty = True
                            continue
                        if k == "trust":
                            continue  # 项目无信任字段，忽略
                        target = vec_map.get(k)
                        if target and target in ec._emotional_vector:
                            ec._emotional_vector[target] = max(0.0, min(1.0,
                                ec._emotional_vector[target] + v))
                            ec._dirty = True
            await asyncio.to_thread(_apply)
        except Exception as e:
            print(f"[ContextAdapter] bump_mood error: {e}")


class WSNotifier:
    """把事件推给前端。
    真实 ConnectionManager.send_to_user(user_id, message: Dict) 收 dict（内部 send_json）。
    二进制音频走 send_bytes_to_user（Phase 3 在 ConnectionManager 新增）。
    """
    def __init__(self, connection_manager):
        self.cm = connection_manager

    async def push_message(self, user_id: str, text: str):
        # 【历史教训】type 必须为 "proactive"，对齐 useWebSocket 的 case 'proactive'，
        # 否则前端 proactive 消息会被忽略（useWebSocket 不认 proactive_message）。
        # 同时去掉了 useEnhanceEvents 中的 proactive_message 处理，避免消息重复注入。
        await self.cm.send_to_user(user_id, {
            "type": "proactive",
            "text": text,
            "msgType": "proactive",
            "timestamp": int(time.time() * 1000),
        })

    async def push_incoming_call(self, user_id: str, reason: str):
        # 【P0-1 修复】统一 data 嵌套，前端用 data.data || data 兜底解析
        await self.cm.send_to_user(user_id,
            {"type": "incoming_call", "data": {"reason": reason}})

    async def push_call_event(self, user_id: str, event: dict):
        # 【P0-1 修复】event 包进 data，避免 event 内字段（如 event/status）覆盖顶层 type
        await self.cm.send_to_user(user_id, {"type": "call_event", "data": event})

    async def push_subtitle(self, user_id: str, text: str, speaker: str):
        await self.cm.send_to_user(user_id,
            {"type": "call_subtitle", "data": {"speaker": speaker, "text": text}})

    async def push_audio(self, user_id: str, audio: bytes):
        # 二进制音频走 WS（Phase 3 已在 ConnectionManager 补 send_bytes_to_user）
        await self.cm.send_bytes_to_user(user_id, audio)

    async def push_chat_record(self, user_id: str, record: dict):
        await self.cm.send_to_user(user_id, {"type": "chat_record", "data": record})


# ============================================================
# 全局实例（init_enhance 时注入）
# ============================================================
_ENHANCE = {}


def init_enhance(app, core, voice_manager, db, connection_manager,
                 asr=None, proactive_cfg: ProactiveConfig | None = None):
    """在 app 启动时调用，装配所有增强模块。
    参数已适配项目真实对象：
      - core: app_state.CORE_INSTANCE (MiaoyiCore)
      - voice_manager: app_state.VOICE_MANAGER (VoiceManager)
      - db: app_state.DB_INSTANCE (DBManager)
      - connection_manager: server.connection_manager (ConnectionManager)
    """
    llm_a = LLMAdapter(core)
    tts_a = TTSAdapter(voice_manager)
    ctx_provider = ContextAdapter(db, core)
    # 注入 connection_manager，供 get_all_active 取在线用户列表
    ctx_provider._connection_manager = connection_manager
    notifier = WSNotifier(connection_manager)

    import app_state as _app_state_db
    call_mgr = CallManager(llm_a, tts_a, notifier, asr=asr, ctx_provider=ctx_provider,
                           db=_app_state_db.DB_INSTANCE)
    proactive = ProactiveEngine(
        llm_a, ctx_provider, notifier,
        cfg=proactive_cfg or ProactiveConfig(),
        call_initiator=lambda uid, reason: call_mgr.ai_dial(uid, reason),
    )

    _ENHANCE["llm"] = llm_a
    _ENHANCE["tts"] = tts_a
    _ENHANCE["ctx"] = ctx_provider
    _ENHANCE["notifier"] = notifier
    _ENHANCE["call"] = call_mgr
    _ENHANCE["proactive"] = proactive
    _ENHANCE["asr"] = asr

    # 【P0-2 修复】统一暴露到 app_state.CALL_MANAGER，供 server.py 主 WS（voice_text 等）
    # 和 enhance_api 的 /call/history 复用同一份 enhance 版 CallManager 实例，
    # 避免双实例导致通话状态/记录对不上。
    try:
        import app_state
        app_state.CALL_MANAGER = call_mgr
    except Exception:
        pass

    # 注意：项目使用 lifespan 模式，不再用 @app.on_event("startup")。
    # 主动引擎的 start/stop 由调用方（server.py lifespan）负责：
    #   启动时 await _ENHANCE["proactive"].start()
    #   关闭时 await _ENHANCE["proactive"].stop()
    # 这样避免 lifespan 与 on_event 冲突（FastAPI 官方推荐 lifespan）。

    return _ENHANCE


def _get(name):
    if name not in _ENHANCE:
        raise RuntimeError("enhance 未初始化，请先调用 init_enhance()")
    return _ENHANCE[name]


# ============================================================
# REST 路由
# ============================================================
# 【ADAPT】user_id 解析策略：
#   1. 优先从 Authorization Bearer token 解析（安全，前端用项目统一 request 封装）
#   2. 兜底从 body.user_id 取（兼容文档原始设计，便于调试）
# 这样前端 callStore 既可以用带 token 的 request() 调用（不传 user_id），
# 也可以直接 fetch 传 user_id（调试）。
class DialBody(BaseModel):
    user_id: str | None = None


def _resolve_user_id(body_user_id: str | None, request: Request) -> str:
    """从 Token 解析 user_id（安全，禁止 body.user_id 覆盖）。

    【P0安全修复】旧版 body.user_id 优先于 Token，存在 IDOR 越权漏洞：
    任意登录用户传 {"user_id": "他人ID"} 即可操作他人通话状态机。
    现改为：仅从 Token 解析 user_id，忽略 body.user_id。
    """
    # 从 Authorization header 解析 token
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        token = auth[7:]
        try:
            from core.auth import decode_token
            user_data = decode_token(token)
            if user_data and 'user_id' in user_data:
                return str(user_data['user_id'])
        except Exception:
            pass
    raise HTTPException(status_code=401, detail="喵～请先登录再使用通话功能")


@router.post("/call/dial")
async def call_dial(body: DialBody, request: Request):
    """用户拨给喵艺。"""
    uid = _resolve_user_id(body.user_id, request)
    await _get("call").user_dial(uid)
    return {"ok": True}


@router.post("/call/answer")
async def call_answer(body: DialBody, request: Request):
    """用户接听喵艺来电。"""
    uid = _resolve_user_id(body.user_id, request)
    await _get("call").user_answer(uid)
    return {"ok": True}


@router.post("/call/decline")
async def call_decline(body: DialBody, request: Request):
    """用户拒接喵艺来电。"""
    uid = _resolve_user_id(body.user_id, request)
    await _get("call").user_decline(uid)
    return {"ok": True}


@router.post("/call/hangup")
async def call_hangup(body: DialBody, request: Request):
    uid = _resolve_user_id(body.user_id, request)
    await _get("call").hangup(uid)
    return {"ok": True}


class TriggerBody(BaseModel):
    user_id: str | None = None
    scenario: str = "miss_you"


@router.post("/proactive/trigger")
async def proactive_trigger(body: TriggerBody, request: Request):
    """手动触发一次主动行为（调试用）。"""
    uid = _resolve_user_id(body.user_id, request)
    await _get("proactive").trigger(uid, body.scenario)
    return {"ok": True}


@router.get("/proactive/preview")
async def proactive_preview(request: Request):
    """预览：当前会触发什么场景（不实际发送）。仅可查自己的状态。"""
    # 【P1安全修复】旧版 user_id 从 query 取且不鉴权，匿名可查任意用户亲密度/心情（IDOR）
    uid = _resolve_user_id(None, request)
    ctx = await _get("ctx").get(uid)
    scen = decide_scenario(ctx, ProactiveConfig())
    return {"scenario": scen, "intimacy": ctx.intimacy, "mood": ctx.mood}


# ============================================================
# WebSocket：通话语音流 + 前端 ASR 文本
# ============================================================
@router.websocket("/ws/call/{user_id}")
async def call_ws(ws: WebSocket, user_id: str):
    """
    通话中的双向通道：
      - 前端发文本帧: {"type":"voice_text","text":"..."} 或 {"type":"answer"/"decline"/"hangup"}
      - 前端发二进制帧: PCM 音频 (用后端ASR时)
      - 后端推: 文本帧=事件/字幕，二进制帧=TTS音频
    """
    await ws.accept()

    # 【P0安全修复】通话 WS 鉴权：token 必须有效且与路径 user_id 一致。
    # 旧版 accept 后直接信任路径 user_id，任意连接可注入 voice_text/answer/hangup
    # 接管他人通话状态机。校验模式与 server.py /ws 一致（accept 后 close 4001）。
    token = ws.query_params.get('token', '') or ws.cookies.get('miaoyi_token', '')
    user_data = None
    if token and len(token.strip()) >= 8:
        try:
            from core.auth import decode_token
            user_data = decode_token(token.strip())
        except Exception:
            user_data = None
    if not user_data or str(user_data.get('user_id', '')) != str(user_id):
        _log.warning("通话WS连接拒绝：token 无效或与 user_id 不匹配")
        try:
            await ws.close(code=4001, reason='认证失败，请重新登录')
        except Exception:
            pass
        return

    voice: VoicePipeline | None = None
    asr = _get("asr")
    call_mgr = _get("call")

    if asr is not None:
        async def on_utt(text: str):
            await call_mgr.bot_reply_to_voice(user_id, text)
        voice = VoicePipeline(asr, on_utt)
        await voice.start()

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"]:
                if voice:
                    voice.push_audio(msg["bytes"])
            elif "text" in msg and msg["text"]:
                data = json.loads(msg["text"])
                t = data.get("type")
                if t == "voice_text":
                    await call_mgr.bot_reply_to_voice(user_id, data.get("text", ""))
                elif t == "answer":
                    await call_mgr.user_answer(user_id)
                elif t == "decline":
                    await call_mgr.user_decline(user_id)
                elif t == "hangup":
                    await call_mgr.hangup(user_id)
    except WebSocketDisconnect:
        pass
    finally:
        if voice:
            await voice.stop()
        if call_mgr.get_state(user_id) in (CallState.CONNECTED, CallState.RINGING):
            await call_mgr.hangup(user_id)
