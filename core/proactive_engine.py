"""
喵艺 AI 伴侣 · 主动行为引擎
============================
职责：
  - 后台 asyncio 定时任务，扫描在线用户
  - 决策是否触发主动消息/来电
  - 支持 8 种主动场景

决策优先级（从高到低）：
  1. 勿扰模式 → 跳过
  2. 冷却检查（距上次主动消息 < 12分钟）→ 跳过
  3. 深夜催睡（23~5点，用户2小时内有活跃）
  4. 早晨早安（7~10点，沉默≥4小时）
  5. 长时间不回（≥30分钟）→ 傲娇催促
  6. 短时间不回（≥4分钟，25%概率）→ 找存在感
  7. 随机想你（6%概率）
  8. 随机想打电话（3%概率，亲密度≥45）
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

_log = logging.getLogger("ProactiveEngine")


# ============================================================
# 配置参数
# ============================================================

@dataclass
class ProactiveConfig:
    """主动行为配置"""

    # 扫描周期（秒）
    scan_interval: int = 60

    # 不回复触发阈值
    no_reply_long_min: int = 30       # 超过该分钟未回 → 傲娇催促
    no_reply_short_min: int = 4       # 超过该分钟未回 → 随手找存在感

    # 冷却时间
    cooldown_min: int = 12            # 两次主动行为最小间隔（防打扰）

    # 触发概率
    miss_you_prob: float = 0.06       # 每次扫描随机"想你"概率
    call_prob: float = 0.03           # 每次扫描随机"想打电话"概率
    no_reply_short_prob: float = 0.25 # 短时间不回触发概率

    # 亲密度阈值
    call_min_intimacy: float = 45.0   # 亲密度达到才可能主动来电

    # 时段定义（小时范围）
    late_night_start: int = 23
    late_night_end: int = 5
    morning_start: int = 7
    morning_end: int = 10

    # 活跃判定
    active_window_min: int = 120      # 最近多少分钟内有消息算活跃
    morning_silence_min: int = 240    # 早晨触发需要沉默≥4小时


# ============================================================
# 用户上下文
# ============================================================

@dataclass
class UserCtx:
    """用户上下文快照"""

    user_id: str
    intimacy: float = 50.0
    joy: float = 0.5
    arousal: float = 0.5
    trust: float = 0.5
    last_active: float = 0.0          # 最后活跃时间戳
    last_proactive: float = 0.0      # 上次主动消息时间戳
    do_not_disturb: bool = False     # 勿扰模式

    def minutes_since_active(self) -> float:
        """距离上次活跃的分钟数"""
        if self.last_active == 0:
            return float('inf')
        return (time.time() - self.last_active) / 60.0

    def minutes_since_proactive(self) -> float:
        """距离上次主动消息的分钟数"""
        if self.last_proactive == 0:
            return float('inf')
        return (time.time() - self.last_proactive) / 60.0


# ============================================================
# 主动场景决策器
# ============================================================

def decide_scenario(
    ctx: UserCtx,
    config: ProactiveConfig,
    now: float | None = None,
) -> str | None:
    """
    纯函数决策：输入 UserCtx + Config → 返回场景名或 None

    决策优先级：
      1. 勿扰模式 → 跳过
      2. 冷却检查
      3. 深夜催睡
      4. 早晨早安
      5. 长时间不回
      6. 短时间不回
      7. 随机想你
      8. 随机想打电话

    Args:
        ctx: 用户上下文
        config: 配置参数
        now: 当前时间戳（用于测试）

    Returns:
        str or None: 触发的场景名
    """
    if now is None:
        now = time.time()

    # 1. 勿扰模式
    if ctx.do_not_disturb:
        _log.debug(f"用户 {ctx.user_id} 开启勿扰模式，跳过")
        return None

    # 2. 冷却检查
    cooldown_elapsed = ctx.minutes_since_proactive()
    if cooldown_elapsed < config.cooldown_min:
        _log.debug(f"用户 {ctx.user_id} 冷却中 ({cooldown_elapsed:.0f}min < {config.cooldown_min}min)")
        return None

    hour = time.localtime(now).tm_hour
    minutes_since_active = ctx.minutes_since_active()

    # 3. 深夜催睡（23~5点，用户活跃过）
    if _is_hour_in_range(hour, config.late_night_start, config.late_night_end):
        if minutes_since_active <= config.active_window_min:
            _log.info(f"触发场景 late_night: user={ctx.user_id}, hour={hour}")
            return "late_night"

    # 4. 早晨早安（7~10点，沉默≥4小时）
    if _is_hour_in_range(hour, config.morning_start, config.morning_end):
        if minutes_since_active >= config.morning_silence_min:
            _log.info(f"触发场景 morning: user={ctx.user_id}, silence={minutes_since_active:.0f}min")
            return "morning"

    # 5. 长时间不回（≥30分钟）
    if minutes_since_active >= config.no_reply_long_min:
        _log.info(f"触发场景 no_reply_long: user={ctx.user_id}, silent={minutes_since_active:.0f}min")
        return "no_reply_long"

    # 6. 短时间不回（≥4分钟，25%概率）
    if minutes_since_active >= config.no_reply_short_min:
        if random.random() < config.no_reply_short_prob:
            _log.info(f"触发场景 no_reply_short: user={ctx.user_id}")
            return "no_reply_short"

    # 7. 随机想你（6%概率）
    if random.random() < config.miss_you_prob:
        _log.info(f"触发场景 miss_you: user={ctx.user_id}")
        return "miss_you"

    # 8. 随机想打电话（3%概率，亲密度≥45）
    if ctx.intimacy >= config.call_min_intimacy:
        if random.random() < config.call_prob:
            _log.info(f"触发场景 want_call: user={ctx.user_id}, intimacy={ctx.intimacy}")
            return "want_call"

    return None


def _is_hour_in_range(hour: int, start: int, end: int) -> bool:
    """判断小时是否在范围内（支持跨午夜）"""
    if start <= end:
        return start <= hour < end
    else:
        # 跨午夜，如 23~5
        return hour >= start or hour < end


# ============================================================
# 主动行为引擎
# ============================================================

class ProactiveEngine:
    """
    主动行为引擎

    特性：
      - 后台 asyncio 定时循环扫描
      - 可插拔：通过回调函数对接用户数据源和消息发送
      - 防打扰：冷却时间 + 勿扰模式
    """

    def __init__(
        self,
        config: ProactiveConfig | None = None,
        get_active_users: Callable[[], list[UserCtx]] | None = None,
        on_trigger: Callable[[str, UserCtx], None] | None = None,
    ):
        """
        Args:
            config: 配置对象
            get_active_users: 获取在线用户列表的回调
            on_trigger: 触发主动行为的回调 (场景名, 用户上下文)
        """
        self.config = config or ProactiveConfig()
        self._get_active_users = get_active_users
        self._on_trigger = on_trigger
        self._running = False
        self._task: asyncio.Task | None = None
        self._trigger_count = 0

    async def start(self):
        """启动后台扫描循环"""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._scan_loop())
        _log.info(f"主动行为引擎启动: interval={self.config.scan_interval}s")

    async def stop(self):
        """停止后台扫描循环"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        _log.info("主动行为引擎停止")

    async def trigger(self, user_id: str, scene: str | None = None) -> str | None:
        """
        手动触发某个用户的主动行为

        Args:
            user_id: 用户 ID
            scene: 指定场景（None 则自动决策）

        Returns:
            str or None: 触发的场景名
        """
        ctx = await self._get_user_ctx(user_id)
        if not ctx:
            _log.warning(f"无法获取用户 {user_id} 的上下文")
            return None

        if scene:
            trigger_scene = scene
        else:
            trigger_scene = decide_scenario(ctx, self.config)

        if trigger_scene:
            await self._execute_trigger(trigger_scene, ctx)
        return trigger_scene

    def preview_scenarios(self, user_id: str) -> list[dict[str, Any]]:
        """预览当前用户可能触发的场景（同步版）"""
        # 注意：此方法需要同步获取上下文，可能返回空
        ctx = UserCtx(user_id=user_id)
        scenarios = []

        hour = time.localtime().tm_hour
        minutes_since_active = ctx.minutes_since_active()

        if _is_hour_in_range(hour, self.config.late_night_start, self.config.late_night_end):
            if minutes_since_active <= self.config.active_window_min:
                scenarios.append({"scene": "late_night", "reason": "深夜时段"})

        if _is_hour_in_range(hour, self.config.morning_start, self.config.morning_end):
            if minutes_since_active >= self.config.morning_silence_min:
                scenarios.append({"scene": "morning", "reason": "早晨时段+长时间未回复"})

        if minutes_since_active >= self.config.no_reply_long_min:
            scenarios.append({"scene": "no_reply_long", "reason": f"沉默{minutes_since_active:.0f}分钟"})

        if ctx.intimacy >= self.config.call_min_intimacy:
            scenarios.append({"scene": "want_call", "reason": f"亲密度{ctx.intimacy:.0f}≥{self.config.call_min_intimacy}"})

        return scenarios

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "trigger_count": self._trigger_count,
            "scan_interval": self.config.scan_interval,
            "cooldown_min": self.config.cooldown_min,
        }

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    async def _scan_loop(self):
        """后台扫描循环"""
        while self._running:
            try:
                await self._do_scan()
            except Exception as e:
                _log.error(f"扫描循环异常: {e}")
            await asyncio.sleep(self.config.scan_interval)

    async def _do_scan(self):
        """执行一次扫描"""
        if not self._get_active_users:
            return

        users = self._get_active_users()
        if not users:
            return

        _log.debug(f"开始扫描: {len(users)} 个活跃用户")

        for ctx in users:
            if not self._running:
                break
            try:
                scene = decide_scenario(ctx, self.config)
                if scene:
                    await self._execute_trigger(scene, ctx)
            except Exception as e:
                _log.error(f"处理用户 {ctx.user_id} 失败: {e}")

    async def _execute_trigger(self, scene: str, ctx: UserCtx):
        """执行触发"""
        _log.info(f"执行主动行为: scene={scene}, user={ctx.user_id}")

        # 更新冷却时间
        ctx.last_proactive = time.time()

        self._trigger_count += 1

        # 调用回调
        if self._on_trigger:
            try:
                result = self._on_trigger(scene, ctx)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                _log.error(f"触发回调失败: {e}")

    async def _get_user_ctx(self, user_id: str) -> UserCtx | None:
        """获取单个用户的上下文"""
        if self._get_active_users:
            users = self._get_active_users()
            for u in users:
                if u.user_id == user_id:
                    return u

        # 返回默认上下文
        return UserCtx(user_id=user_id)
