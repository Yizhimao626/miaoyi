"""
定时提醒管理器
============================================
职责:
1. 从用户消息中识别"提醒我..."类请求（正则预筛 + LLM 解析 + 正则降级）
2. 持久化到 reminders 表
3. 后台调度线程轮询到期提醒，通过主动消息通道推送给用户

设计要点:
- LLM 解析失败自动降级为本地正则解析，保证无 API 时基础提醒仍可用
- mark_reminder_fired 为原子 UPDATE（status='pending' 条件），防止重复触发
- 推送复用 MiaoyiCore._on_proactive_message → WebSocket 'proactive' 通道，前端零改动
"""
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

_log = logging.getLogger("reminder_manager")

# 触发预筛：命中才走解析（避免每条消息都调 LLM）
_DETECT_PATTERN = re.compile(
    r'(提醒我|别忘|不要忘|记得提醒|定个提醒|设个提醒|加个提醒|叫我.{0,8}(起床|起来|起床了))'
)

# 查询/取消提醒意图
_LIST_PATTERN = re.compile(r'(我的提醒|提醒列表|有什么提醒|哪些提醒|查看提醒|看看提醒|提醒有什么)')
_CANCEL_PATTERN = re.compile(r'(取消提醒|删除提醒|别提醒|不用提醒|算了.{0,6}提醒|撤掉提醒|取消刚才的提醒)')

# 相对时间降级解析
_REL_MINUTE = re.compile(r'(\d{1,3})\s*分钟[之以]?后')
_REL_HOUR = re.compile(r'(\d{1,2})\s*(?:个?小时|个半钟)[之以]?后')
_REL_DAY = re.compile(r'(\d{1,2})\s*天[之以]?后')

_PERIOD_MAP = {'早上': 8, '上午': 9, '中午': 12, '下午': 15, '傍晚': 18, '晚上': 20, '夜里': 21, '凌晨': 1}
_ABS_TIME = re.compile(
    r'(今天|明天|后天)?\s*(早上|上午|中午|下午|傍晚|晚上|夜里|凌晨)?\s*(\d{1,2})[点时:：]\s*(半|\d{1,2})?分?'
)


class ReminderManager:
    """定时提醒：创建 + 调度 + 推送"""

    SCAN_INTERVAL = 30          # 调度扫描间隔（秒）
    MIN_LEAD_SECONDS = 10       # 提醒时间至少晚于当前 10 秒
    MAX_LEAD_SECONDS = 366 * 86400  # 最长一年

    def __init__(self, db_manager, llm_call: Callable, fire_proactive: Callable):
        """
        Args:
            db_manager: DBManager 实例
            llm_call: LLM 调用函数，签名 (messages, temperature, max_tokens, timeout) -> str
            fire_proactive: 主动推送函数，签名 (message: str, msg_type: str, user_id: str)
        """
        self._db = db_manager
        self._llm_call = llm_call
        self._fire = fire_proactive
        self._running = False
        self._thread: threading.Thread | None = None

    # ---------- 意图检测与创建 ----------

    @staticmethod
    def detect_intent(text: str) -> bool:
        """快速预筛是否可能是提醒请求"""
        return bool(text and _DETECT_PATTERN.search(text))

    @staticmethod
    def detect_list_intent(text: str) -> bool:
        """查询提醒列表意图"""
        return bool(text and _LIST_PATTERN.search(text))

    @staticmethod
    def detect_cancel_intent(text: str) -> bool:
        """取消提醒意图"""
        return bool(text and _CANCEL_PATTERN.search(text))

    def list_reminders(self, user_id: str) -> str:
        """格式化用户的待触发提醒列表"""
        try:
            items = self._db.get_user_reminders(user_id)
        except Exception as e:
            _log.error(f"[提醒] 查询列表失败: {e}")
            return "喵？查询提醒时出了点小差错，稍后再试试吧～"
        if not items:
            return "你现在没有待触发的提醒哦～要不要让我帮你记一个？⏰"
        lines = []
        for i, item in enumerate(items[:10], 1):
            dt = datetime.fromtimestamp(item['remind_ts'])
            now = datetime.now()
            if dt.date() == now.date():
                time_desc = f"今天 {dt.strftime('%H:%M')}"
            elif dt.date() == (now + timedelta(days=1)).date():
                time_desc = f"明天 {dt.strftime('%H:%M')}"
            else:
                time_desc = dt.strftime('%m月%d日 %H:%M')
            lines.append(f"{i}. {time_desc} —— {item['content']}")
        return "你的提醒清单来啦～📋\n" + "\n".join(lines)

    def cancel_latest(self, user_id: str) -> str:
        """取消该用户最近创建的一条待触发提醒"""
        try:
            items = self._db.get_user_reminders(user_id)
            if not items:
                return "你目前没有待触发的提醒哦～"
            # get_user_reminders 按触发时间升序，取最晚（最近一条）的 pending
            target = items[-1]
            if self._db.cancel_reminder(user_id, target['id']):
                dt = datetime.fromtimestamp(target['remind_ts'])
                return f"好啦，已经帮你取消「{target['content']}」（{dt.strftime('%m月%d日 %H:%M')}）的提醒了～"
            return "取消没有成功，可能这条提醒刚刚触发了，再看看？"
        except Exception as e:
            _log.error(f"[提醒] 取消失败: {e}")
            return "喵？取消提醒时出了点小差错，稍后再试试吧～"

    def create_from_message(self, user_id: str, text: str) -> str | None:
        """解析提醒请求并创建，成功返回确认话术，失败返回 None（调用方继续原流程）

        【提速】本地正则先行（毫秒级），LLM 只兜底解析复杂表达（如"下周五"）。
        """
        parsed = self._parse_fallback(text) or self._parse_with_llm(text)
        if not parsed:
            return None

        remind_ts = parsed['remind_ts']
        content = parsed['content']
        if not content:
            content = '（未说明事项）'

        lead = remind_ts - time.time()
        if lead < self.MIN_LEAD_SECONDS:
            return None  # 过去时间/过近，不拦截，交给原流程回复
        if lead > self.MAX_LEAD_SECONDS:
            return None

        try:
            self._db.create_reminder(user_id, remind_ts, content)
        except Exception as e:
            _log.error(f"[提醒] 创建失败: {e}")
            return None

        _log.info(f"[提醒] 用户{user_id}创建提醒: {content} @ {datetime.fromtimestamp(remind_ts)}")
        return self._format_confirmation(remind_ts, content, lead)

    def _format_confirmation(self, remind_ts: float, content: str, lead: float) -> str:
        dt = datetime.fromtimestamp(remind_ts)
        now = datetime.now()
        if dt.date() == now.date():
            time_desc = f"今天 {dt.strftime('%H:%M')}"
        elif dt.date() == (now + timedelta(days=1)).date():
            time_desc = f"明天 {dt.strftime('%H:%M')}"
        else:
            time_desc = dt.strftime('%m月%d日 %H:%M')
        return (f"好嘞，记下啦～我会在 {time_desc} 提醒你「{content}」⏰ "
                f"到时候就算你把我忘了，我也会来喵你的！")

    # ---------- 解析 ----------

    def _parse_with_llm(self, text: str) -> dict | None:
        """LLM 解析时间与内容（支持复杂表达），失败返回 None"""
        now = datetime.now()
        try:
            prompt = f"""你是时间解析器。当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（星期{'一二三四五六日'[now.weekday()]}）。

从用户消息中提取提醒时间和内容，只输出 JSON，不要输出其他任何文字：
{{"is_reminder": true/false, "remind_time": "YYYY-MM-DD HH:MM", "content": "提醒事项"}}

规则：
1. 相对时间（如"30分钟后"）按当前时间推算
2. 未指明日期的绝对时间（如"晚上8点"）默认最近的未来时刻
3. content 提取提醒事项本身，去掉"提醒我"等字样，简短保留核心
4. 不是提醒请求或无法确定时间时输出 {{"is_reminder": false}}

用户消息：{text}"""
            resp = self._llm_call(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=200, timeout=10,
            )
            if not resp:
                return None
            # 防御性提取 JSON（可能被 markdown 包裹）
            match = re.search(r'\{[^{}]*\}', resp, re.DOTALL)
            if not match:
                return None
            data = json.loads(match.group(0))
            if not data.get('is_reminder'):
                return None
            remind_dt = datetime.strptime(data.get('remind_time', ''), '%Y-%m-%d %H:%M')
            content = str(data.get('content', '')).strip()[:200]
            if not content:
                return None
            return {'remind_ts': remind_dt.timestamp(), 'content': content}
        except Exception as e:
            _log.debug(f"[提醒] LLM 解析失败（将降级正则）: {e}")
            return None

    def _parse_fallback(self, text: str) -> dict | None:
        """本地正则降级解析（无 LLM 时的兜底）"""
        now = datetime.now()

        # 相对时间
        m = _REL_MINUTE.search(text)
        if m:
            return self._build(text, now + timedelta(minutes=int(m.group(1))), m)
        m = _REL_HOUR.search(text)
        if m:
            return self._build(text, now + timedelta(hours=int(m.group(1))), m)
        m = _REL_DAY.search(text)
        if m:
            return self._build(text, now + timedelta(days=int(m.group(1))), m)

        # 绝对时间
        m = _ABS_TIME.search(text)
        if not m:
            return None
        day_word, period_word, hour_s, minute_s = m.groups()
        hour = int(hour_s)
        minute = 30 if minute_s == '半' else (int(minute_s) if minute_s else 0)
        if hour > 23 or minute > 59:
            return None

        # 时段修正：数字小于时段默认值时前移 12 小时（"下午3点"=15点）
        if period_word in ('下午', '傍晚', '晚上', '夜里') and hour < 12:
            hour += 12
        if period_word == '中午' and hour < 12:
            hour = 12
        if period_word == '凌晨' and hour > 6:
            hour -= 12

        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day_word == '明天':
            target += timedelta(days=1)
        elif day_word == '后天':
            target += timedelta(days=2)
        elif not day_word and target <= now:
            target += timedelta(days=1)  # "8点"已过 → 明天8点

        return self._build(text, target, m)

    def _build(self, text: str, remind_dt: datetime, time_match) -> dict | None:
        """从原句剔除时间短语，提取提醒内容"""
        content = text
        for frag in ('提醒我', '提醒一下我', '记得提醒我', '别忘', '请不要忘', '定个提醒', '设个提醒', '加个提醒'):
            content = content.replace(frag, '')
        if time_match:
            content = content.replace(time_match.group(0), '')
        content = re.sub(r'^[，,。.\s]+|[，,。.\s]+$', '', content).strip()
        content = re.sub(r'到(了|时候)?[，,]?\s*$', '', content).strip()
        if not content:
            content = '（未说明事项）'
        return {'remind_ts': remind_dt.timestamp(), 'content': content[:200]}

    # ---------- 调度 ----------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True, name='reminder-scheduler')
        self._thread.start()
        _log.info(f"[提醒] 调度线程已启动（每 {self.SCAN_INTERVAL}s 扫描）")

    def stop(self):
        self._running = False

    def _scheduler_loop(self):
        while self._running:
            time.sleep(self.SCAN_INTERVAL)
            try:
                self._fire_due()
            except Exception as e:
                _log.error(f"[提醒] 调度异常: {e}")

    def _fire_due(self):
        """触发所有到期提醒"""
        due = self._db.get_due_reminders()
        for item in due:
            # 先原子标记再推送：保证同一条提醒只触发一次
            if not self._db.mark_reminder_fired(item['id']):
                continue
            message = f"⏰ 叮咚～时间到啦！提醒你：{item['content']}"
            try:
                self._fire(message, 'reminder', item['user_id'])
                _log.info(f"[提醒] 已触发 #{item['id']} 用户{item['user_id']}: {item['content']}")
            except Exception as e:
                _log.error(f"[提醒] 推送失败 #{item['id']}: {e}")
