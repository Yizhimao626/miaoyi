"""
喵艺 · 通话状态机
实现视频里那种"真实打电话"的感觉：
  - 用户拨给喵艺：拨号 → 响铃 → 喵艺决定接听/拒接 → 通话 → 挂断
  - 喵艺拨给用户：推送来电 → 用户接听/拒接 → 通话 → 挂断
  - 喵艺会"傲娇拒接"：亲密度低/在闹脾气时拒接，过几秒再主动回拨
  - 通话中：ASR→LLM→TTS 流式 + 实时字幕推送
  - 通话结束：把通话记录写进聊天流（📞 通话时长 00:50 / 📞 已拒绝）

通过依赖注入对接你现有的 LLM、TTS、ASR、WebSocket。

【P0-1 修复】事件格式统一为 {"event": "dialing|ringing|connected|declined|ended|missed", ...}，
            由 WSNotifier 包成 {"type":"call_event","data":{...}} 推送，与前端 useWebSocket 对齐。
【P0-5 修复】user_dial 立即返回，拨号流程后台异步推进，不再阻塞 HTTP /call/dial 请求。
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Protocol

from .persona import AffectionLevel, build_system_prompt, level_from_intimacy

_log = logging.getLogger(__name__)


# ============================================================
# 通话内时间查询预拦截
# 【历史教训】通话走 call_manager.bot_reply_to_voice → LLM，不经 miaoyi_core 的时间预拦截，
# 导致 LLM 不知道准确时间，回答错误的星期/时间。这里复刻同一套预拦截逻辑。
# ============================================================
_TIME_KEYWORDS = ['几点', '几号', '星期几', '周几', '礼拜几', '什么时间',
                  '现在时间', '今天日期', '今天几号', '今天是几号', '日期是多少',
                  '今天星期', '今天周', '几月几号', '今天礼拜', '礼拜几']
_TIME_EXCLUDE = ['你觉得', '你认为', '几点合适', '几点出发', '几点见面', '几点睡', '几点起',
                 '几点下班', '几点上班']


def _try_time_query(text: str) -> str | None:
    """通话中时间查询预拦截。命中返回中文时间字符串，否则 None。"""
    t = (text or '').strip()
    if not t:
        return None
    if any(x in t for x in _TIME_EXCLUDE):
        return None
    if not any(k in t for k in _TIME_KEYWORDS):
        return None
    now = datetime.now()  # 服务器本地时间
    weekday = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]
    if '几点' in t or '什么时间' in t or '现在时间' in t or t == '时间':
        return f"现在是 {now.strftime('%H:%M')}，星期{weekday} 喵～"
    if '几号' in t or '日期' in t:
        return f"今天是 {now.strftime('%Y年%m月%d日')}，星期{weekday} 喵～"
    return f"今天是星期{weekday}，{now.strftime('%H:%M')} 喵～"


class CallState(str, Enum):
    IDLE = "idle"
    DIALING = "dialing"      # 拨号中（等待对端响应）
    RINGING = "ringing"      # 响铃中
    CONNECTED = "connected"  # 通话中
    ENDED = "ended"
    DECLINED = "declined"
    MISSED = "missed"


# ---------- 依赖接口 ----------
class LLMClient(Protocol):
    async def chat_stream(self, system_prompt: str, user_prompt: str): ...  # async generator of str


class TTSClient(Protocol):
    async def synthesize(self, text: str) -> bytes: ...   # 返回音频字节(可流式)


class ASRClient(Protocol):
    async def transcribe(self, audio_chunk: bytes) -> str: ...


class CallNotifier(Protocol):
    """通话事件推送给前端。前端据此切换 CallScreen 状态。"""
    async def push_call_event(self, user_id: str, event: dict) -> None: ...
    async def push_incoming_call(self, user_id: str, reason: str) -> None: ...
    async def push_subtitle(self, user_id: str, text: str, speaker: str) -> None: ...
    async def push_audio(self, user_id: str, audio: bytes) -> None: ...
    async def push_chat_record(self, user_id: str, record: dict) -> None: ...


@dataclass
class CallSession:
    user_id: str
    direction: str = "outgoing"   # outgoing=用户拨喵艺 / incoming=喵艺拨用户
    state: CallState = CallState.IDLE
    started_at: float | None = None   # 通话接通时间戳
    duration: int = 0
    initiator_reason: str = ""           # 喵艺来电时的理由
    barge_seq: int = 0                   # 【barge-in】打断代次：用户每打断一次 +1，旧回合推送作废

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "direction": self.direction,
            "state": self.state.value,
            "duration": self.duration,
            "started_at": self.started_at,
            "initiator_reason": self.initiator_reason,
        }


class CallManager:
    def __init__(
        self,
        llm: LLMClient,
        tts: TTSClient,
        notifier: CallNotifier,
        asr: ASRClient | None = None,
        ctx_provider=None,
        db=None,
    ):
        self.llm = llm
        self.tts = tts
        self.notifier = notifier
        self.asr = asr
        self.ctx_provider = ctx_provider   # 同 proactive_engine 的 UserContextProvider
        self.db = db  # 通话历史持久化（可选，未注入时保持内存态）
        self.sessions: dict[str, CallSession] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ---------- 用户拨给喵艺 ----------
    async def user_dial(self, user_id: str) -> CallSession | None:
        """用户点'打电话' → 立即返回，拨号→响铃→喵艺决策全程后台异步推进。"""
        sess = self.sessions.get(user_id)
        if sess and sess.state in (CallState.DIALING, CallState.RINGING, CallState.CONNECTED):
            return sess
        sess = CallSession(user_id=user_id, direction="outgoing", state=CallState.DIALING)
        self.sessions[user_id] = sess
        # 【竞态修复】清掉旧残留任务（如拒接回拨的 sleep 任务），防止其稍后醒来销毁新会话
        self._cancel_task(user_id)
        # 后台推进拨号流程，HTTP 请求立即返回，UI 靠 call_event 推送驱动
        self._tasks[user_id] = asyncio.create_task(self._dial_flow(user_id))
        return sess

    async def _dial_flow(self, user_id: str):
        """拨号流程：拨号 → 响铃 → 喵艺决定接/拒（后台异步，不阻塞 HTTP）。"""
        try:
            sess = self.sessions.get(user_id)
            if not sess:
                return
            await self._emit(user_id, {"event": "dialing", "direction": "outgoing"})
            await asyncio.sleep(1.5)  # 拨号等待感
            sess.state = CallState.RINGING
            await self._emit(user_id, {"event": "ringing"})
            accept = await self._ai_decide_answer(user_id)
            if accept:
                await self._connect(user_id)
            else:
                await self._ai_decline_then_maybe_callback(user_id)
        except asyncio.CancelledError:
            pass
        except Exception:
            _log.exception("拨号流程异常 user_id=%s", user_id)

    # ---------- 喵艺拨给用户（主动来电） ----------
    async def ai_dial(self, user_id: str, reason: str = "想听你的声音了喵~"):
        sess = self.sessions.get(user_id)
        if sess and sess.state in (CallState.RINGING, CallState.CONNECTED, CallState.DIALING):
            return
        sess = CallSession(user_id=user_id, direction="incoming",
                           state=CallState.RINGING, initiator_reason=reason)
        self.sessions[user_id] = sess
        await self.notifier.push_incoming_call(user_id, reason)
        # 等待用户接听/拒接，超时未接 → missed
        self._tasks[user_id] = asyncio.create_task(self._incoming_timeout(user_id))

    async def _incoming_timeout(self, user_id: str):
        try:
            await asyncio.sleep(35)  # 35秒未接
            sess = self.sessions.get(user_id)
            if sess and sess.state == CallState.RINGING:
                sess.state = CallState.MISSED
                await self._emit(user_id, {"event": "missed"})
                # 【情绪化升级】未接来电也带点小情绪
                await self.notifier.push_chat_record(user_id, {
                    "type": "call", "status": "missed", "text": "📞 喵艺的未接来电…下次要快点接哦喵"})
                self._persist_history(user_id, "incoming", "missed")
                self.sessions.pop(user_id, None)
        except asyncio.CancelledError:
            pass

    # ---------- 用户操作 ----------
    async def user_answer(self, user_id: str):
        """用户接听喵艺的来电。"""
        sess = self.sessions.get(user_id)
        if not sess or sess.state != CallState.RINGING:
            return
        self._cancel_task(user_id)
        await self._connect(user_id)

    async def user_decline(self, user_id: str):
        """用户拒接喵艺的来电。"""
        sess = self.sessions.get(user_id)
        if not sess or sess.state != CallState.RINGING:
            return
        self._cancel_task(user_id)
        sess.state = CallState.ENDED
        await self._emit(user_id, {"event": "ended", "reason": "declined_by_user"})
        await self.notifier.push_chat_record(user_id, {
            "type": "call", "status": "declined", "text": "📞 已拒绝"})
        self._persist_history(user_id, "incoming", "declined_by_user")
        # 喵艺被拒接 → 小情绪（可选：稍后主动发条傲娇消息）
        await self._on_user_rejected(user_id)
        self.sessions.pop(user_id, None)

    async def hangup(self, user_id: str):
        """任意一方挂断。"""
        sess = self.sessions.get(user_id)
        if not sess:
            return
        self._cancel_task(user_id)
        if sess.state == CallState.CONNECTED:
            dur = int(time.time() - (sess.started_at or time.time()))
            sess.duration = dur
            sess.state = CallState.ENDED
            await self._emit(user_id, {"event": "ended", "duration": dur})
            # 【情绪化升级】按通话时长给出有温度的记录文案
            if dur >= 600:
                record_text = f"📞 和你聊了好久好开心～通话时长 {dur//60:02d}:{dur%60:02d}"
            elif dur >= 120:
                record_text = f"📞 通话时长 {dur//60:02d}:{dur%60:02d}，听到你的声音很开心喵"
            else:
                record_text = f"📞 通话时长 {dur//60:02d}:{dur%60:02d}"
            await self.notifier.push_chat_record(user_id, {
                "type": "call", "status": "ended",
                "text": record_text, "duration": dur})
            self._persist_history(user_id, sess.direction, "ended", dur)
            # 【产品优化】通话时长奖励：每分钟+1亲密度，上限+5
            if self.ctx_provider and hasattr(self.ctx_provider, "bump_mood") and dur > 0:
                duration_bonus = min(0.05, max(0.0, dur / 60 * 0.01))
                if duration_bonus > 0:
                    await self.ctx_provider.bump_mood(user_id, intimacy=duration_bonus)
        else:
            sess.state = CallState.ENDED
            await self._emit(user_id, {"event": "ended", "reason": "cancelled"})
            self._persist_history(user_id, sess.direction, "cancelled", 0, "cancelled")
        self.sessions.pop(user_id, None)

    # ---------- 喵艺决策 ----------
    async def _ai_decide_answer(self, user_id: str) -> bool:
        """喵艺是否接听用户的电话。傲娇时会拒接。"""
        ctx = await self._get_ctx(user_id)
        intimacy = ctx.intimacy if ctx else 30.0
        mood = ctx.mood if ctx else {}
        level = level_from_intimacy(intimacy)

        # 【产品修复】旧版陌生人接听率仅 15%，新用户根本打不通电话，以为功能坏了。
        # 调整：陌生人也高概率接听（85%），傲娇拒接作为偶发彩蛋而非常态。
        if level == AffectionLevel.STRANGER:
            return random.random() < 0.85
        # 在闹脾气（唤醒高+愉悦低）→ 可能拒接，但概率降低
        if mood.get("arousal", 0.5) > 0.7 and mood.get("joy", 0.5) < 0.35:
            return random.random() < 0.65
        # 正常情况高概率接
        return random.random() < 0.95

    async def _ai_decline_then_maybe_callback(self, user_id: str):
        """喵艺拒接用户的电话，过几秒可能主动回拨。"""
        sess = self.sessions.get(user_id)
        if sess:
            sess.state = CallState.DECLINED
        await self._emit(user_id, {"event": "declined"})
        await self.notifier.push_chat_record(user_id, {
            "type": "call", "status": "declined", "text": "📞 已拒绝"})
        self._persist_history(user_id, "outgoing", "declined")
        # 【产品修复】拒接后无论亲密度都快速回拨，让用户能打通电话（不阻断体验）
        # 旧版仅亲密度≥45才回拨，新用户拒接后就彻底打不通了。
        ctx = await self._get_ctx(user_id)
        intimacy = ctx.intimacy if ctx else 30.0
        callback_prob = 0.9 if intimacy < 45 else 0.6  # 亲密度低更倾向回拨弥补
        if random.random() < callback_prob:
            await asyncio.sleep(random.uniform(3, 6))
            # 【竞态修复】等待期间用户可能已重新拨号/接通：有活跃会话则放弃本次回拨，
            # 旧版无条件 pop+ai_dial 会把用户的新会话销毁并制造意外来电
            cur = self.sessions.get(user_id)
            if cur and cur.state in (CallState.DIALING, CallState.RINGING, CallState.CONNECTED):
                _log.info(f"[通话] 回拨前检测到活跃会话，放弃回拨 user_id={user_id}")
                return
            self.sessions.pop(user_id, None)
            await self.ai_dial(user_id, reason="哼，刚才是我不想接……现在换我打给你了喵！")

    async def _on_user_rejected(self, user_id: str):
        """用户拒接喵艺来电 → 喵艺小情绪。"""
        if self.ctx_provider and hasattr(self.ctx_provider, "bump_mood"):
            await self.ctx_provider.bump_mood(user_id, joy=-0.2, arousal=0.2)

    # ---------- 通话中：语音对话循环 ----------
    async def _connect(self, user_id: str):
        sess = self.sessions.get(user_id)
        if not sess:
            return
        sess.state = CallState.CONNECTED
        sess.started_at = time.time()
        await self._emit(user_id, {"event": "connected", "direction": sess.direction})
        # 喵艺先开口
        await self._bot_say(user_id, self._greeting(sess.direction, user_id))
        # 【产品优化】通话接通加亲密度：新用户(intimacy<5)+10快速正反馈，老用户+3
        if self.ctx_provider and hasattr(self.ctx_provider, "bump_mood"):
            ctx = await self._get_ctx(user_id)
            delta = 0.10 if (ctx is None or ctx.intimacy < 5) else 0.03
            await self.ctx_provider.bump_mood(user_id, intimacy=delta, joy=0.05)
        # 进入对话循环（维持通话状态 + 计时）
        self._tasks[user_id] = asyncio.create_task(self._dialog_loop(user_id))

    async def _dialog_loop(self, user_id: str):
        sess = self.sessions.get(user_id)
        if not sess:
            return
        try:
            while sess.state == CallState.CONNECTED:
                await asyncio.sleep(0.2)
                sess.duration = int(time.time() - (sess.started_at or time.time()))
        except asyncio.CancelledError:
            pass

    async def bot_reply_to_voice(self, user_id: str, user_text: str):
        """通话中：收到前端 ASR 文本，喵艺回复并播报+字幕。"""
        my_sess = self.sessions.get(user_id)
        if not my_sess or my_sess.state != CallState.CONNECTED:
            return
        # 【互斥/打断统一】新回合开始即作废旧回合：barge_seq+1 使旧回合所有后续推送失效。
        # 同时解决：a) barge-in（VAD 触发 handle_barge_in 也走 seq+1）
        #          b) 双路回复并发（voice_text / 二进制 ASR 两路同时进来）→ 后到者胜，不再交错叠播
        my_sess.barge_seq += 1
        my_seq = my_sess.barge_seq

        def _stale() -> bool:
            """回合已失效：被新回合/打断作废（seq 变化），或会话已挂断/重拨（对象不同一）。"""
            return self.sessions.get(user_id) is not my_sess or my_sess.barge_seq != my_seq

        # 【去重修复】不再后端 push_subtitle(speaker='user')：前端 ASR 识别时已本地添加 user 字幕，
        # 后端再推一次会导致用户每句话显示两遍。user 字幕统一由前端本地负责。
        # 【时间预拦截】通话中问时间，直接返回系统时间，不走 LLM（LLM 不知道准确时间会答错星期/时间）
        time_resp = _try_time_query(user_text)
        if time_resp:
            await self._bot_say(user_id, time_resp)
            return
        ctx = await self._get_ctx(user_id)
        system_prompt = build_system_prompt(
            intimacy=ctx.intimacy if ctx else 30,
            relation_days=ctx.relation_days if ctx else 1,
            mood=ctx.mood if ctx else None,
            user_text=user_text,
        )
        # 流式生成 → 边出字幕边 TTS
        # 【回声防护】前端在播报期间丢弃录音片段，避免喵艺的声音被识别成用户输入
        full = []
        try:
            async for delta in self.llm.chat_stream(system_prompt, user_text):
                if _stale():
                    _log.info(f"[barge-in] 回合失效（打断/挂断/重拨），终止 LLM 流式推送 user_id={user_id}")
                    return
                full.append(delta)
                await self.notifier.push_subtitle(user_id, delta, speaker="bot")
        except Exception:
            _log.exception("通话中 LLM 流式异常 user_id=%s", user_id)
        text = "".join(full).strip()
        if text:
            if _stale():
                return
            # 【AIGC合规】通话中 AI 输出内容审核，拦截违规内容
            try:
                from security.content_moderation import ContentModerator
                if not hasattr(self, '_moderator'):
                    self._moderator = ContentModerator()
                mod = self._moderator.moderate(text, level='normal')
                if not mod.get('allowed', True):
                    _log.warning(f"[通话审核] AI输出被拦截: {mod.get('category')}")
                    text = "喵...这个话题我不太方便聊呢，我们说点别的吧喵～"
                    await self.notifier.push_subtitle(user_id, text, speaker="bot")
            except Exception as e:
                _log.debug(f"[通话审核] 异常(放行): {e}")
            try:
                audio = await self.tts.synthesize(text)
                if _stale():
                    return
                await self.notifier.push_audio(user_id, audio)
            except Exception:
                _log.exception("通话中 TTS 合成异常 user_id=%s", user_id)

    async def _bot_say(self, user_id: str, text: str):
        sess = self.sessions.get(user_id)
        my_seq = sess.barge_seq if sess else 0
        await self.notifier.push_subtitle(user_id, text, speaker="bot")
        try:
            audio = await self.tts.synthesize(text)
            # 【无声排查】TTS 返回空音频时不再推空包（前端 decode 会抛错），并留下可查日志
            if not audio:
                _log.warning("[通话] TTS 返回空音频，跳过推送 user_id=%s text=%s", user_id, text[:40])
                return
            # 【barge-in】合成完检查回合有效性：打断（seq 变化）或挂断/重拨（对象不同一）则不推送
            if sess is None or self.sessions.get(user_id) is not sess or sess.barge_seq != my_seq:
                _log.info(f"[barge-in] 回合失效，作废旧回合音频推送 user_id={user_id}")
                return
            await self.notifier.push_audio(user_id, audio)
        except Exception:
            _log.exception("通话中 _bot_say TTS 异常 user_id=%s", user_id)

    async def handle_barge_in(self, user_id: str) -> bool:
        """【barge-in】用户在喵艺播报期间开口说话（前端 Silero VAD 检测）。

        打断代次 +1：正在流式推送的旧回合（bot_reply_to_voice/_bot_say）在每次推送前
        核对代次，不一致即静默终止，实现"一开口喵艺就闭嘴"。
        """
        sess = self.sessions.get(user_id)
        if not sess or sess.state != CallState.CONNECTED:
            return False
        sess.barge_seq += 1
        _log.info(f"[barge-in] 用户打断播报 user_id={user_id} seq→{sess.barge_seq}")
        return True

    def _greeting(self, direction: str, user_id: str = '') -> str:
        if direction == "incoming":
            base = random.choice([
                "喂？你终于接啦喵~", "哼，让你等久了。", "喂……想你了嘛。"
            ])
        else:
            base = random.choice([
                "喂？找我什么事呀~", "喵？接啦接啦。", "嗯？打电话来想我啦？"
            ])
        # 【人性化升级·带记忆的开场】自然提起用户最近说过的一件事
        if user_id and self.db is not None:
            try:
                mems = self.db.get_memories(1, '', str(user_id))
                if mems:
                    topic = (mems[0].get('value') or mems[0].get('content') or '').strip()
                    if topic:
                        return f"{base} 对了，我记得你之前说过{topic[:30]}，我还记着呢喵~"
            except Exception as e:
                _log.debug(f"开场白记忆获取失败（用默认开场）: {e}")
        return base

    # ---------- 工具 ----------
    async def _get_ctx(self, user_id: str):
        if self.ctx_provider is None:
            return None
        try:
            return await self.ctx_provider.get(user_id)
        except Exception:
            return None

    async def _emit(self, user_id: str, event: dict):
        """推送通话状态变更事件。event 必须含 'event' 字段（dialing/ringing/...）。"""
        await self.notifier.push_call_event(user_id, event)

    def _cancel_task(self, user_id: str):
        t = self._tasks.pop(user_id, None)
        if t:
            t.cancel()

    def _persist_history(self, user_id: str, direction: str, status: str,
                          duration: int = 0, reason: str = '') -> None:
        """通话记录入库（失败不影响主流程）。"""
        if self.db is None:
            return
        try:
            self.db.save_call_history(user_id, direction, status, duration, reason)
        except Exception as e:
            _log.warning(f"通话历史持久化失败 user_id={user_id}: {e}")

    def get_state(self, user_id: str) -> CallState | None:
        s = self.sessions.get(user_id)
        return s.state if s else None

    def get_active_call(self, user_id: str) -> dict | None:
        s = self.sessions.get(user_id)
        return s.to_dict() if s else None

    def get_history(self, user_id: str, limit: int = 20) -> list:
        """通话历史：优先读 DB 持久化记录（重启不丢），无 DB 时回退内存态。"""
        if self.db is not None:
            try:
                rows = self.db.get_call_history(user_id, limit)
                if rows:
                    return rows
            except Exception as e:
                _log.warning(f"读取通话历史失败: {e}")
        s = self.sessions.get(user_id)
        return [s.to_dict()] if s else []
