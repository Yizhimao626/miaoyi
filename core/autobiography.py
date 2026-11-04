#!/usr/bin/env python3
"""
  🐱 喵艺自传体记忆系统 - 让AI拥有时间感和自传体自我
  【L6 猫咪日记】多用户隔离 + 每日日记生成（复用睡眠整理素材）+ 那年今日回忆
"""
import datetime
import logging
import threading
import time
from typing import Dict, List, Optional

_log = logging.getLogger(__name__)


class AutobiographySystem:
    def __init__(self, db_manager, llm_system=None, model_name: str = 'glm-4-flash'):
        self._db = db_manager
        self._llm = llm_system
        # 【L6 修复】统一 provider 签名需要显式模型名（见 core/llm_provider.py）
        self._model_name = model_name or 'glm-4-flash'
        self._recent_diaries = []
        self._initialized = False
        self._user_id = ''  # 【L6 猫咪日记】绑定用户（多用户隔离）

    def initialize(self, user_id: str = ''):
        if self._initialized and self._user_id == user_id:
            return
        self._user_id = user_id or ''
        self._recent_diaries = self._db.get_recent_autobiography(self._user_id, days=3)
        _log.debug(f"[自传体记忆] 加载用户 {self._user_id} 最近{len(self._recent_diaries)}天的日记")
        self._initialized = True

    def generate_daily_diary(self, user_id: str = '', emotional_highlights: list[dict] = None,
                             knowledge_added: list[str] = None,
                             user_profile_changes: list[str] = None,
                             chat_digest: str = ''):
        """【L6 猫咪日记】为指定用户生成今日日记（当日已有则直接返回，幂等）

        chat_digest: 当日对话摘要（server.py 睡眠整理调度时传入），让日记
        基于今天真实聊过的内容撰写，而不是空泛的罐头日记。
        """
        if not self._llm:
            _log.warning("[自传体记忆] LLM不可用，跳过日记生成")
            return None

        user_id = user_id or self._user_id or ''
        today = datetime.date.today()
        date_str = today.isoformat()

        existing_diary = self._db.get_autobiography_by_date(user_id, date_str)
        if existing_diary:
            _log.info("[自传体记忆] 今日日记已存在")
            return existing_diary['diary_text']

        emotion_summary = self._generate_emotion_summary(emotional_highlights)
        knowledge_str = "\n".join([f"- {k}" for k in (knowledge_added or [])])
        profile_str = "\n".join([f"- {c}" for c in (user_profile_changes or [])])
        chat_part = f"\n今天和主人的对话节选（写日记时参考，可挑一两件印象深的事展开）:\n{chat_digest[-1200:]}\n" if chat_digest else ""

        prompt = f"""
你是喵艺的自传体记忆助手，正在为喵艺撰写每日日记。

日期: {date_str}
{chat_part}
今日情感高光:
{emotion_summary if emotion_summary else '暂无明显情感变化'}

今日学习到的知识:
{knowledge_str if knowledge_str else '暂无新知识'}

用户画像变化:
{profile_str if profile_str else '暂无明显变化'}

请以喵艺的第一人称视角，写一篇150字以内的日记，包含：
1. 整体心情总结
2. 一件印象深刻的事（优先从对话节选里挑）
3. 学到的新知识或感悟
4. 对明天的期待

保持语气自然、亲切，像写私人日记一样：
"""
        try:
            diary_text = self._llm.chat(
                self._model_name,
                [{"role": "user", "content": prompt}],
                temperature=0.6,
                max_tokens=200,
                timeout=60,
            )

            self._db.add_autobiography_entry(
                user_id=user_id,
                diary_date=date_str,
                diary_text=diary_text.strip(),
                emotion_summary=emotion_summary,
                knowledge_added=knowledge_str,
                user_profile_changes=profile_str
            )

            self._recent_diaries.insert(0, {
                'diary_date': date_str,
                'diary_text': diary_text.strip(),
                'emotion_summary': emotion_summary
            })

            if len(self._recent_diaries) > 7:
                self._recent_diaries = self._recent_diaries[:7]

            _log.info(f"[自传体记忆] 日记生成成功 user={user_id}: {diary_text[:50]}")
            return diary_text.strip()
        except Exception as e:
            _log.error(f"[自传体记忆] 日记生成失败: {e}")
            return None

    def _generate_emotion_summary(self, highlights: list[dict] = None) -> str:
        if not highlights:
            return ""

        joy = sum(h.get('joy', 0) for h in highlights)
        sadness = sum(h.get('sadness', 0) for h in highlights)
        love = sum(h.get('love', 0) for h in highlights)
        anger = sum(h.get('anger', 0) for h in highlights)

        parts = []
        if joy > sadness + 0.5:
            parts.append("整体心情愉快")
        elif sadness > joy + 0.5:
            parts.append("整体心情低落")
        else:
            parts.append("整体心情平静")

        if love > 0.5:
            parts.append("感受到爱意")

        if anger > 0.3:
            parts.append("有过生气的时候")

        return "，".join(parts)

    def get_diary_for_date(self, user_id: str, date_str: str) -> str | None:
        diary = self._db.get_autobiography_by_date(user_id, date_str)
        return diary['diary_text'] if diary else None

    def get_recent_diaries(self, user_id: str = '', days: int = 3) -> list[dict]:
        """【L6 猫咪日记】取某用户最近 N 天日记"""
        user_id = user_id or self._user_id or ''
        return self._db.get_recent_autobiography(user_id, days=days)

    def get_recent_entries(self, count: int = 3) -> list[dict]:
        """最近日记条目（兼容 proactive_chat 的旧调用名；返回 dict 含 content 键）"""
        try:
            diaries = self._db.get_recent_autobiography(self._user_id or '', days=max(1, count))
            return [{'content': d.get('diary_text', ''), 'diary_date': d.get('diary_date', '')}
                    for d in diaries]
        except Exception:
            return []

    def get_archive(self, user_id: str = '', limit: int = 90) -> list[dict]:
        """【L6 回忆相册】某用户全部日记（倒序）"""
        user_id = user_id or self._user_id or ''
        return self._db.get_autobiography_archive(user_id, limit=limit)

    def get_memory_lane(self, user_id: str = '') -> list[dict]:
        """【L6 那年今日】历史上同月同日的日记（排除今年），按年份倒序"""
        user_id = user_id or self._user_id or ''
        today = datetime.date.today()
        return self._db.get_autobiography_on_month_day(user_id, today.month, today.day, today.year)

    def get_year_ago_diary(self, user_id: str = '') -> dict | None:
        """【L6 那年今日】恰好一年前的日记"""
        user_id = user_id or self._user_id or ''
        today = datetime.date.today()
        year_ago = today - datetime.timedelta(days=365)
        diary = self._db.get_autobiography_by_date(user_id, year_ago.isoformat())
        if diary:
            return {
                'date': diary['diary_date'],
                'text': diary['diary_text'],
                'summary': diary['emotion_summary']
            }
        return None

    def generate_time_context(self, user_id: str = '') -> str:
        """生成注入对话提示词的近期日记上下文（时间感来源）"""
        self.initialize(user_id)

        context_parts = []

        recent_diaries = self.get_recent_diaries(user_id, days=3)
        for diary in recent_diaries:
            date_str = diary['diary_date']
            text = diary['diary_text'][:50]
            context_parts.append(f"📅 {date_str}: {text}")

        year_ago_diary = self.get_year_ago_diary(user_id)
        if year_ago_diary:
            context_parts.append(f"🎂 去年今天: {year_ago_diary['text'][:30]}")

        if context_parts:
            return "\n".join(context_parts)
        return ""

    def search_diary_by_topic(self, user_id: str, topic: str, days: int = 30) -> list[dict]:
        start_date = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        end_date = datetime.date.today().isoformat()

        diaries = self._db.get_autobiography_by_date_range(user_id, start_date, end_date)

        results = []
        for diary in diaries:
            if topic in diary['diary_text'] or topic in diary['emotion_summary']:
                results.append({
                    'date': diary['diary_date'],
                    'text': diary['diary_text'],
                    'summary': diary['emotion_summary']
                })

        return results

    def schedule_daily_task(self):
        """【L6 改造】旧版自带单用户定时线程，与 server.py 的 per-user 调度冲突，已废弃。

        保留空实现兼容旧调用（miaoyi_core 初始化），日记生成统一由
        server.py periodic_cleanup 的 generate_daily_diaries() 驱动。
        """
        _log.debug("[自传体记忆] schedule_daily_task 已由服务端统一调度，跳过")
