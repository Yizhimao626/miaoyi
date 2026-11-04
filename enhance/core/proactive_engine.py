"""
喵艺 · 主动行为引擎
让喵艺从"被动问答"变成"会主动找你"的活人：
  - 用户很久没回 → 傲娇催促
  - 深夜 → 撒娇催睡/晚安
  - 早晨 → 懒洋洋早安
  - 想你了 → 主动说想你
  - 想打电话 → 主动来电

设计为后台 asyncio 任务，定时扫描在线用户并决策。
通过依赖注入对接你现有的：LLM、记忆、情感、WebSocket 推送。
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional, Protocol

from .persona import AffectionLevel, build_proactive_prompt, level_from_intimacy


# ---------- 依赖接口（对接你现有 core 模块） ----------
class LLMClient(Protocol):
    async def chat(self, system_prompt: str, user_prompt: str = "") -> str: ...


class UserContextProvider(Protocol):
    """返回某用户的实时上下文，供决策与生成使用。"""
    async def get(self, user_id: str) -> dict: ...  # 见 UserCtx


class Notifier(Protocol):
    """把主动消息/来电推送给前端（走你现有的 WebSocket connection_manager）。"""
    async def push_message(self, user_id: str, text: str) -> None: ...
    async def push_incoming_call(self, user_id: str, reason: str) -> None: ...


@dataclass
class UserCtx:
    user_id: str
    intimacy: float = 30.0                # 0~100
    relation_days: int = 1
    mood: dict = field(default_factory=lambda: {"joy": 0.5, "arousal": 0.5, "intimacy": 0.3, "trust": 0.4})
    last_active_at: datetime = field(default_factory=datetime.utcnow)  # 用户最后发消息时间
    last_bot_msg_at: datetime | None = None                          # 喵艺最后主动/被动消息时间
    online: bool = True
    dnd: bool = False                    # 勿扰
    memory_context: str = ""             # 【人性化】最近记忆摘要，让主动消息提到上次聊的内容
    recent_bot_text: str = ""            # 【回旋镖】喵艺最近一条消息文本（用户没回时延续话题）
    user_distress: dict | None = None  # 【情绪化主动关心】{'emotion','topic','ts'(epoch),'followed_up'}
    anniversary: dict | None = None    # 【纪念日仪式感】今天是否里程碑日：{'milestone','days_together','description','message'}


# ---------- 决策 ----------
@dataclass
class ProactiveConfig:
    scan_interval: int = 60              # 扫描周期(秒)
    no_reply_long_min: int = 30          # 超过该分钟未回 → 傲娇催促
    no_reply_short_min: int = 4          # 超过该分钟未回 → 随手找存在感
    cooldown_min: int = 12               # 两次主动行为最小间隔(防打扰)
    miss_you_prob: float = 0.06          # 每次扫描随机"想你"概率
    call_prob: float = 0.03              # 每次扫描随机"想打电话"概率(仅 soulmate/friend)
    call_min_intimacy: float = 45.0      # 亲密度达到才可能主动来电
    late_night_hours: tuple = (23, 5)    # 深夜时段
    morning_hours: tuple = (7, 10)       # 早晨时段
    timezone_offset: int = 8             # UTC+8
    max_per_day: int = 3                 # 【产品优化】每日主动消息上限，避免打扰用户
    care_min_delay_min: int = 20         # 【情绪化主动关心】情绪低点后至低多久才回访（给对方冷静空间）
    care_max_delay_min: int = 360        # 【情绪化主动关心】超过 6 小时不再回访（不翻旧账）


def _now_local(cfg: ProactiveConfig) -> datetime:
    return datetime.utcnow() + timedelta(hours=cfg.timezone_offset)


def _hours_in(h: int, span: tuple) -> bool:
    lo, hi = span
    if lo <= hi:
        return lo <= h < hi
    return h >= lo or h < hi   # 跨夜，如 23~5


def decide_scenario(ctx: UserCtx, cfg: ProactiveConfig) -> str | None:
    """核心决策：返回场景名或 None（不发）。纯函数，便于测试。

    时间基准约定：间隔计算（沉默时长/冷却/回访窗口）一律用 UTC（last_active_at、
    last_bot_msg_at、distress ts 均为 UTC 基准）；时段判断（深夜/早晨）用本地时间。
    【修复】旧版用 UTC+8 的 now 直接减 UTC 时间戳，沉默时长恒虚高 8 小时，
    导致深夜催睡永不触发、傲娇催促无视冷却过度触发。
    """
    now = _now_local(cfg)        # 本地时间 → 仅用于时段（深夜/早晨）判断
    now_utc = datetime.utcnow()  # UTC → 用于一切间隔计算

    # 1. 勿扰 / 离线：除深夜催睡外不打扰
    if ctx.dnd:
        return None

    # 2. 冷却：距离上次喵艺消息太近则跳过
    if ctx.last_bot_msg_at:
        if now_utc - ctx.last_bot_msg_at < timedelta(minutes=cfg.cooldown_min):
            return None

    level = level_from_intimacy(ctx.intimacy)
    hours_silent = (now_utc - ctx.last_active_at).total_seconds() / 3600.0

    # 2.5 【情绪化主动关心】用户之前情绪低落且之后一直沉默 → 延时回访
    # 优先级高于催促类：用户难过时绝不能傲娇催促，要先温柔关心。
    d = getattr(ctx, 'user_distress', None)
    if d and not d.get('followed_up'):
        # distress ts 为 time.time() epoch → 用 time.time() 比，避免 naive datetime.timestamp() 的本地时区陷阱
        since_care_min = (time.time() - float(d.get('ts') or 0)) / 60.0
        silent_min = hours_silent * 60
        if cfg.care_min_delay_min <= since_care_min <= cfg.care_max_delay_min \
           and silent_min >= cfg.care_min_delay_min:
            return "emotional_care"

    # 2.6 【纪念日仪式感】今天是相识里程碑日（7/30/100/365 天）且尚未庆祝 → 主动庆祝。
    # 只影响"当天"（ctx 由 ContextAdapter 实时判定 today_milestone），次日自动失效。
    anniv = getattr(ctx, 'anniversary', None)
    if anniv and anniv.get('milestone'):
        return "anniversary"

    # 3. 深夜催睡（即使用户沉默不久也触发，但要有冷却）
    if _hours_in(now.hour, cfg.late_night_hours):
        # 仅在用户还"醒着"（最近2小时内活跃过）才催
        if hours_silent <= 2:
            return "late_night"

    # 4. 早晨早安
    if _hours_in(now.hour, cfg.morning_hours) and hours_silent >= 4:
        return "morning"

    # 5. 长时间不回 → 傲娇催促
    if hours_silent * 60 >= cfg.no_reply_long_min:
        return "no_reply_long"

    # 6. 短时间不回 → 偶尔找存在感
    if hours_silent * 60 >= cfg.no_reply_short_min and random.random() < 0.25:
        return "no_reply_short"

    # 7. 随机"想你"
    if random.random() < cfg.miss_you_prob:
        return "miss_you"

    # 8. 随机"想打电话"（亲密度足够）
    if level in (AffectionLevel.FRIEND, AffectionLevel.SOULMATE) \
       and ctx.intimacy >= cfg.call_min_intimacy \
       and random.random() < cfg.call_prob:
        return "want_call"

    return None


# ---------- 引擎 ----------
class ProactiveEngine:
    def __init__(
        self,
        llm: LLMClient,
        ctx_provider: UserContextProvider,
        notifier: Notifier,
        cfg: ProactiveConfig | None = None,
        call_initiator: Callable[[str, str], Awaitable[None]] | None = None,
    ):
        self.llm = llm
        self.ctx_provider = ctx_provider
        self.notifier = notifier
        self.cfg = cfg or ProactiveConfig()
        # 主动来电由 call_manager 负责，这里只触发
        self.call_initiator = call_initiator
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # 【产品优化】每日主动消息计数：{user_id: count}，跨天清零
        self._daily_sent: dict = {}
        self._daily_date: str = ""

    async def start(self):
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while not self._stop.is_set():
            try:
                await self._scan_once()
            except Exception as e:  # 后台任务不能挂
                print(f"[proactive] loop error: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.cfg.scan_interval)
            except asyncio.TimeoutError:
                pass

    async def _scan_once(self):
        # ctx_provider 需返回当前需检查的用户列表（在线/活跃用户）
        users = await self.ctx_provider.get_all_active()  # type: ignore[attr-defined]
        for user_id in users:
            try:
                await self._check_user(user_id)
            except Exception as e:
                print(f"[proactive] user {user_id} error: {e}")

    async def _check_user(self, user_id: str):
        # 【产品优化】每日主动消息上限检查，避免频繁打扰用户
        today = _now_local(self.cfg).strftime("%Y-%m-%d")
        if self._daily_date != today:
            self._daily_date = today
            self._daily_sent = {}
        if self._daily_sent.get(user_id, 0) >= self.cfg.max_per_day:
            return  # 今日已达上限，跳过

        ctx: UserCtx = await self.ctx_provider.get(user_id)
        scenario = decide_scenario(ctx, self.cfg)
        if scenario is None:
            return

        if scenario == "want_call":
            # 主动来电，交给 call_manager
            if self.call_initiator:
                await self.call_initiator(user_id, "想听你的声音了，接电话喵~")
            return

        await self._fire_message(user_id, ctx, scenario)
        # 计数+1
        self._daily_sent[user_id] = self._daily_sent.get(user_id, 0) + 1

    async def _fire_message(self, user_id: str, ctx: UserCtx, scenario: str):
        system_prompt = build_proactive_prompt(
            scenario, ctx.intimacy, ctx.relation_days, ctx.mood,
            memory_context=getattr(ctx, "memory_context", "") or "",
        )
        # 【话题回旋镖】用户很久没回 + 喵艺最后说过话 → 自然延续上次的话题
        # （真人会念念不忘地再提一次，而不是每次都换新话题）
        # 【情绪化主动关心】把惦记的那件事告诉模型，让关心落到实处而不是泛泛问候
        if scenario == "emotional_care":
            d = getattr(ctx, "user_distress", None) or {}
            emo_cn = {'sadness': '有些难过', 'fear': '有些担心不安',
                      'loneliness': '有些孤单', 'fatigue': '很累'}.get(d.get('emotion'), '心情不太好')
            topic = (d.get('topic') or '').strip()
            care_block = f"\n\n【你一直惦记的事】用户之前跟你聊天时显得{emo_cn}"
            if topic:
                care_block += f"，当时说到：{topic[:60]}"
            care_block += ("。之后用户一直没再说话。你现在轻轻回访一下：温柔地提起这件事，"
                           "告诉对方你一直在、想听对方说；不追问细节、不说教、不评价，"
                           "也别提'你之前没回我'。如果对方不想说，就安静陪着。")
            system_prompt += care_block
        # 【纪念日仪式感】今天是相识里程碑日 → 像真人一样小小庆祝
        if scenario == "anniversary":
            a = getattr(ctx, "anniversary", None) or {}
            m = a.get("milestone")
            desc = a.get("description") or ""
            days = a.get("days_together") or ctx.relation_days
            fallback = (a.get("message") or "").strip()
            anniv_block = (
                f"\n\n【今天是个特别的日子】今天是你们相识第 {days} 天"
                f"（{desc}），你一直记得这个日子。"
            )
            if fallback:
                anniv_block += f"可以自然地围绕这个感觉展开，例如：{fallback}"
            anniv_block += (
                "像真人一样小小庆祝一下：1-2 句真心话就好，"
                "不要广播式祝福、不要列条目、不要反复强调；"
                "可以轻轻回忆一句相处的小事（如有记忆可用），按亲密度把握分寸，别矫情。"
            )
            system_prompt += anniv_block
        recent_bot = (getattr(ctx, "recent_bot_text", "") or "").strip()
        if recent_bot and scenario in ("no_reply_long", "miss_you", "late_night"):
            system_prompt += (
                f"\n\n【回旋镖】你上次跟用户说：{recent_bot}\n"
                "现在用户一直没回。自然地再提起上次这件事（带一点点小委屈或撒娇），"
                "不要假装是第一次说，也不要说'你上次怎么没回'这么直白。"
            )
        # 【GLM 1214 修复】llm.chat 要求同时提供 user_prompt（纯 system 会被 GLM 拒绝，
        # 报 code 1214 并触发 _safe_llm_call 熔断）。给一个明确的生成指令。
        text = await self.llm.chat(system_prompt, "请以喵艺的口吻生成一条主动消息。")
        text = (text or "").strip()
        if not text:
            return
        await self.notifier.push_message(user_id, text)
        # 通知记忆/情感层更新 last_bot_msg_at（由 ctx_provider 落库）
        await self.ctx_provider.mark_bot_spoke(user_id, text)  # type: ignore[attr-defined]
        # 【情绪化主动关心】回访已发出 → 标记，避免重复关心同一件事
        if scenario == "emotional_care":
            mark_care = getattr(self.ctx_provider, "mark_care_delivered", None)
            if mark_care:
                try:
                    await mark_care(user_id)
                except Exception as e:
                    print(f"[proactive] mark_care_delivered error: {e}")
        # 【纪念日仪式感】庆祝消息已发出 → 标记已庆祝（幂等），避免当天重复庆祝
        if scenario == "anniversary":
            mark_anniv = getattr(self.ctx_provider, "mark_anniversary_celebrated", None)
            if mark_anniv:
                try:
                    m = (getattr(ctx, "anniversary", None) or {}).get("milestone") or 0
                    await mark_anniv(user_id, m)
                except Exception as e:
                    print(f"[proactive] mark_anniversary_celebrated error: {e}")

    # 手动触发（供调试 / API 调用）
    async def trigger(self, user_id: str, scenario: str = "miss_you"):
        ctx: UserCtx = await self.ctx_provider.get(user_id)
        await self._fire_message(user_id, ctx, scenario)
