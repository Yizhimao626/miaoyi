"""
🐱 L10 低频客串群聊 —— "喵艺的朋友" 剧情事件
============================================
三位猫朋友（布丁/雪球/煤球）低频到访聊天窗，与喵艺一起上演小剧场。

设计约束（与 L7 心跳纪律同源）：
- 零表迁移、零前端改动：台词复用主动消息通道（_on_proactive_message）落库 + WS 推送，
  msg_type='guest_event'，前端按既有 proactive 类型渲染
- 触发状态经统一记忆层持久化（key='客串_上次'），L9 远程记忆模式下跨端/跨实例共享
- 低频：距上次 >= 3 天 + 概率门槛 + 适宜时段（本地 10~22 点），绝不打扰深夜
- 绝不挂主链路：任何失败静默返回 None，下一小时调度重试
============================================
"""
import json
import logging
import random
import re
import threading
import time

_log = logging.getLogger("guest_events")

# ============ 朋友角色库 ============
GUESTS = [
    {
        'name': '布丁', 'emoji': '🍮',
        'persona': '圆滚滚的橘猫，永远在吃，热情话痨，跟谁都能聊起来',
        'catchphrase': '嗷呜',
    },
    {
        'name': '雪球', 'emoji': '❄️',
        'persona': '高冷的白猫，嘴上毒舌其实心软，嘴上嫌弃但每次都来',
        'catchphrase': '哼',
    },
    {
        'name': '煤球', 'emoji': '🖤',
        'persona': '社恐的黑猫，说话小声，温柔细腻，擅长安静地倾听',
        'catchphrase': '那个……',
    },
]

# ============ 触发参数 ============
MIN_INTERVAL_SECONDS = 3 * 86400   # 低频约束：距上次客串至少 3 天
TRIGGER_PROBABILITY = 0.5          # 间隔满足后每次检查的触发概率（平均约 4~5 天一次）
OK_HOUR_START = 10                 # 适宜时段下界（含）：本地时间 10 点
OK_HOUR_END = 22                   # 适宜时段上界（不含）：22 点，与 L7 免打扰时段衔接
MEMORY_KEY = '客串_上次'            # 触发状态的记忆键（统一记忆层持久化）

# 台词约束
MIN_LINES = 3                      # 剧本最少有效台词数（少于则视为生成失败，下小时重试）
MAX_LINE_CHARS = 60                # 单条台词最大长度（超出截断）


class GuestEventSystem:
    """低频客串事件系统：触发判定 → LLM 生成小剧场剧本 → 格式化为消息列表"""

    def __init__(self, llm=None, memory=None):
        """
        Args:
            llm: LLM 适配器（core._llm，llm_provider 统一接口 chat(model, messages, ...)）
            memory: 统一记忆系统（UnifiedMemorySystem 或 L9 MemoryClient，duck-typing 透明）
        """
        self._llm = llm
        self._memory = memory
        self._llm_lock = threading.Lock()
        self._last_llm_call_time = 0.0
        self._llm_min_interval = 1.0

    # ---------- 触发判定 ----------

    @staticmethod
    def _can_trigger(last_ts: float | None, now_ts: float, hour: int) -> bool:
        """纯函数触发判定（便于单测）：适宜时段 + 间隔约束

        Args:
            last_ts: 上次客串时间戳（None 表示从未触发过）
            now_ts: 当前时间戳
            hour: 本地小时数（0~23）
        """
        if hour < OK_HOUR_START or hour >= OK_HOUR_END:
            return False
        if last_ts is None:
            return True
        return (now_ts - last_ts) >= MIN_INTERVAL_SECONDS

    def _read_last_ts(self, user_id: str) -> float | None:
        """从记忆层读取上次客串时间（读失败降级为 None，等价于从未触发）"""
        try:
            facts = self._memory.get_facts(user_id=user_id) or {}
            raw = str(facts.get(MEMORY_KEY, '') or '').strip()
            return float(raw) if raw else None
        except Exception as e:
            _log.debug(f"[客串] 读取上次触发时间失败（按从未触发处理）: {e}")
            return None

    # ---------- 主入口 ----------

    def maybe_run(self, user_id: str, db=None, now_ts: float = None) -> list[str] | None:
        """判定并生成一次客串事件

        Args:
            user_id: 用户 ID（触发状态按用户隔离）
            db: DBManager（取最近聊天作为剧本素材，可为 None）
            now_ts: 当前时间戳（默认取真实时间；测试可注入）

        Returns:
            消息列表（首条开场卡 + 台词行）；None 表示本次不触发
        """
        try:
            if not self._llm or self._memory is None:
                return None
            now_ts = now_ts if now_ts is not None else time.time()
            hour = time.localtime(now_ts).tm_hour
            last_ts = self._read_last_ts(user_id)
            if not self._can_trigger(last_ts, now_ts, hour):
                return None
            if random.random() > TRIGGER_PROBABILITY:
                return None

            messages = self._generate_script(user_id, db)
            if not messages:
                return None

            # 触发成功 → 记录时间戳（经记忆层持久化，远程模式同样生效）
            try:
                self._memory.remember(MEMORY_KEY, str(now_ts), user_id=user_id)
            except Exception as e:
                _log.warning(f"[客串] 记录触发时间失败（不影响本次推送）: {e}")
            return messages
        except Exception as e:
            _log.warning(f"[客串] 事件生成失败（静默跳过，下小时重试）: {e}")
            return None

    # ---------- 剧本生成 ----------

    def _generate_script(self, user_id: str, db=None) -> list[str] | None:
        """1 次 LLM 调用生成 [{speaker, line}] 剧本，格式化为消息列表"""
        context_parts = []
        # 最近聊天片段（生活气息来源，与日记生成同款素材口径）
        if db is not None:
            try:
                msgs = db.get_user_messages(user_id, limit=10) or []
                lines = []
                for m in msgs[-8:]:
                    role = '主人' if m.get('role') == 'user' else '喵艺'
                    text = str(m.get('text', '')).strip()[:60]
                    if text:
                        lines.append(f"{role}: {text}")
                if lines:
                    context_parts.append("最近聊天片段：\n" + "\n".join(lines))
            except Exception as e:
                _log.debug(f"[客串] 聊天素材获取失败: {e}")
        # 记忆里的用户信息（最多 6 条，避免 prompt 膨胀）
        try:
            facts = self._memory.get_facts(user_id=user_id) or {}
            picks = [
                f"- {k}: {v}" for k, v in list(facts.items())[:6]
                if k and v and k != MEMORY_KEY
            ]
            if picks:
                context_parts.append("喵艺记得的关于主人的事：\n" + "\n".join(picks))
        except Exception as e:
            _log.debug(f"[客串] 记忆素材获取失败: {e}")
        context = "\n\n".join(context_parts) or "（暂无上下文，自由发挥日常话题）"

        prompt = f"""你是喵艺，一只温暖治愈的猫娘。今天你的三位猫朋友来家里做客，在聊天窗里上演一段轻松的小剧场。

喵艺的性格：元气、小调皮、嘴硬心软，口头禅"喵～"；称呼用户为"主人"。
朋友们：
{chr(10).join(f"- {g['name']}{g['emoji']}：{g['persona']}（口癖：{g['catchphrase']}）" for g in GUESTS)}

{context}

要求：
1. 写 4~6 条台词的连续小剧场：朋友做客闲聊打趣，可聊主人的日常趣事，最后由喵艺收尾（顺便向主人介绍朋友）。
2. 严格输出 JSON 数组，不要输出任何其他文字，格式：
[{{"speaker": "布丁", "line": "台词"}}, {{"speaker": "喵艺", "line": "台词"}}]
3. speaker 只能是：{' / '.join(g['name'] for g in GUESTS)} / 喵艺；每条 line 不超过 40 字，口语化，可带 1 个 emoji。"""

        raw = self._rate_limited_llm_call(prompt)
        if not raw:
            return None
        script = self._parse_script(raw)
        if not script:
            return None
        return self._format_lines(script)

    def _rate_limited_llm_call(self, prompt: str, max_retries: int = 2) -> str | None:
        """带限流和重试的 LLM 调用（与 proactive_chat 同款纪律）"""
        if not self._llm:
            return None
        with self._llm_lock:
            elapsed = time.time() - self._last_llm_call_time
            if elapsed < self._llm_min_interval:
                time.sleep(self._llm_min_interval - elapsed)
            for attempt in range(max_retries + 1):
                try:
                    self._last_llm_call_time = time.time()
                    content = self._llm.chat(
                        model='glm-4-flash',
                        messages=[{'role': 'user', 'content': prompt}],
                        temperature=0.9, max_tokens=500, timeout=30,
                    )
                    return (content or '').strip() or None
                except Exception as e:
                    err = str(e).lower()
                    retryable = any(x in err for x in ('429', 'too many', 'rate limit', '500', '502', '503', '504'))
                    if retryable and attempt < max_retries:
                        delay = 2.0 * (2 ** attempt)
                        _log.warning(f"[客串] LLM 调用失败(第{attempt + 1}次)，{delay:.1f}s 后重试: {e}")
                        time.sleep(delay)
                    else:
                        _log.warning(f"[客串] LLM 调用失败: {e}")
                        return None
        return None

    # ---------- 解析与格式化 ----------

    @staticmethod
    def _parse_script(raw: str) -> list[dict] | None:
        """解析 LLM 输出为 [{speaker, line}]（容错：剥围栏/截取 JSON 片段/过滤非法 speaker）"""
        if not raw:
            return None
        text = raw.strip()
        # 剥掉 markdown 代码围栏
        if text.startswith('```'):
            text = re.sub(r'^```[a-zA-Z]*\s*', '', text)
            text = re.sub(r'\s*```$', '', text).strip()
        data = None
        try:
            data = json.loads(text)
        except Exception:
            m = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    return None
        if not isinstance(data, list):
            return None
        valid_names = {g['name'] for g in GUESTS} | {'喵艺'}
        script = []
        for item in data:
            if not isinstance(item, dict):
                continue
            speaker = str(item.get('speaker', '')).strip()
            line = str(item.get('line', '')).strip()
            if speaker in valid_names and line:
                script.append({'speaker': speaker, 'line': line[:MAX_LINE_CHARS]})
        return script if len(script) >= MIN_LINES else None

    @staticmethod
    def _format_lines(script: list[dict]) -> list[str]:
        """格式化为推送消息列表：首条开场卡 + "名字emoji：台词" 行"""
        emoji_map = {g['name']: g['emoji'] for g in GUESTS}
        guests_in_show = []
        for item in script:
            name = item['speaker']
            if name != '喵艺' and name not in guests_in_show:
                guests_in_show.append(name)
        intro = '、'.join(f"{emoji_map.get(n, '')}{n}" for n in guests_in_show)
        lines = [f"🏡 叮咚——{intro or '喵艺的朋友'}按响门铃，来做客啦！"]
        for item in script:
            emoji = emoji_map.get(item['speaker'], '🐱')
            lines.append(f"{item['speaker']}{emoji}：{item['line']}")
        return lines
