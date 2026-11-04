"""
喵艺 AI 伴侣 · 情感持久化
==============================
职责：
  - 情感状态的 SQLite 持久化
  - 情感事件日志记录
  - 情感快照恢复
"""

import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

_log = logging.getLogger("EmotionPersistence")


# ============================================================
# SQL 建表语句
# ============================================================

_CREATE_EMOTION_STATE_SQL = """
CREATE TABLE IF NOT EXISTS emotion_state (
    user_id TEXT PRIMARY KEY,
    joy REAL DEFAULT 0.5,
    arousal REAL DEFAULT 0.5,
    intimacy REAL DEFAULT 0.5,
    trust REAL DEFAULT 0.5,
    label TEXT DEFAULT 'neutral',
    updated_at REAL,
    last_saved REAL
);
"""

_CREATE_EMOTION_LOG_SQL = """
CREATE TABLE IF NOT EXISTS emotion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    event TEXT NOT NULL,
    old_state TEXT,
    new_state TEXT,
    trigger_source TEXT,
    created_at REAL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_emotion_log_user_id
ON emotion_log (user_id, created_at);
"""


# ============================================================
# 情感持久化管理器
# ============================================================

class EmotionPersistence:
    """
    情感状态持久化管理

    特性：
      - SQLite 存储
      - 支持情感快照保存与恢复
      - 情感变更日志
      - 批量操作
    """

    def __init__(self, db_path: str = "data/emotion.db"):
        """
        Args:
            db_path: SQLite 数据库路径
        """
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self):
        """初始化数据库连接和表结构"""
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row

            # 创建表
            self._conn.execute(_CREATE_EMOTION_STATE_SQL)
            self._conn.execute(_CREATE_EMOTION_LOG_SQL)
            self._conn.execute(_CREATE_INDEX_SQL)
            self._conn.commit()

            _log.info(f"情感持久化数据库已初始化: {self._db_path}")
        except Exception as e:
            _log.error(f"数据库初始化失败: {e}")
            raise

    def save_state(
        self,
        user_id: str,
        emotion: dict[str, float],
        label: str = "",
    ) -> None:
        """
        保存用户情感状态

        Args:
            user_id: 用户 ID
            emotion: 情感向量 {joy, arousal, intimacy, trust}
            label: 情感标签
        """
        now = time.time()

        try:
            self._conn.execute("""
                INSERT INTO emotion_state
                (user_id, joy, arousal, intimacy, trust, label, updated_at, last_saved)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    joy = excluded.joy,
                    arousal = excluded.arousal,
                    intimacy = excluded.intimacy,
                    trust = excluded.trust,
                    label = excluded.label,
                    updated_at = excluded.updated_at,
                    last_saved = excluded.last_saved
            """, (
                user_id,
                emotion.get("joy", 0.5),
                emotion.get("arousal", 0.5),
                emotion.get("intimacy", 0.5),
                emotion.get("trust", 0.5),
                label,
                now,
                now,
            ))
            self._conn.commit()

            _log.debug(f"情感状态已保存: user={user_id}, label={label}")
        except Exception as e:
            _log.error(f"保存情感状态失败: {e}")

    def load_state(self, user_id: str) -> dict[str, Any] | None:
        """
        加载用户情感状态

        Args:
            user_id: 用户 ID

        Returns:
            情感状态字典或 None
        """
        try:
            row = self._conn.execute("""
                SELECT * FROM emotion_state WHERE user_id = ?
            """, (user_id,)).fetchone()

            if row:
                return {
                    "user_id": row["user_id"],
                    "joy": row["joy"],
                    "arousal": row["arousal"],
                    "intimacy": row["intimacy"],
                    "trust": row["trust"],
                    "label": row["label"],
                    "updated_at": row["updated_at"],
                }
            return None
        except Exception as e:
            _log.error(f"加载情感状态失败: {e}")
            return None

    def log_event(
        self,
        user_id: str,
        event: str,
        old_state: dict[str, float] | None = None,
        new_state: dict[str, float] | None = None,
        trigger_source: str = "",
    ) -> int:
        """
        记录情感变更事件

        Args:
            user_id: 用户 ID
            event: 事件名（如 "user_message", "bot_reply"）
            old_state: 变更前情感
            new_state: 变更后情感
            trigger_source: 触发来源

        Returns:
            int: 日志记录 ID
        """
        now = time.time()

        try:
            cursor = self._conn.execute("""
                INSERT INTO emotion_log
                (user_id, event, old_state, new_state, trigger_source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                event,
                json.dumps(old_state or {}, ensure_ascii=False),
                json.dumps(new_state or {}, ensure_ascii=False),
                trigger_source,
                now,
            ))
            self._conn.commit()
            return cursor.lastrowid or 0
        except Exception as e:
            _log.error(f"记录情感事件失败: {e}")
            return 0

    def get_recent_logs(
        self,
        user_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        获取最近的情感日志

        Args:
            user_id: 用户 ID
            limit: 条数限制

        Returns:
            日志列表
        """
        try:
            rows = self._conn.execute("""
                SELECT * FROM emotion_log
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (user_id, limit)).fetchall()

            return [
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "event": row["event"],
                    "old_state": json.loads(row["old_state"] or "{}"),
                    "new_state": json.loads(row["new_state"] or "{}"),
                    "trigger_source": row["trigger_source"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
        except Exception as e:
            _log.error(f"获取情感日志失败: {e}")
            return []

    def delete_old_logs(self, days: int = 30) -> int:
        """
        删除指定天数前的日志

        Args:
            days: 保留天数

        Returns:
            int: 删除条数
        """
        cutoff = time.time() - (days * 24 * 3600)

        try:
            cursor = self._conn.execute("""
                DELETE FROM emotion_log WHERE created_at < ?
            """, (cutoff,))
            self._conn.commit()
            deleted = cursor.rowcount
            _log.info(f"清理情感日志: 删除 {deleted} 条超过 {days} 天的记录")
            return deleted
        except Exception as e:
            _log.error(f"清理日志失败: {e}")
            return 0

    def delete_user(self, user_id: str) -> bool:
        """
        删除指定用户的情感持久化数据（快照与日志）

        Args:
            user_id: 用户 ID

        Returns:
            bool: 是否成功
        """
        try:
            self._conn.execute("DELETE FROM emotion_state WHERE user_id = ?", (user_id,))
            self._conn.execute("DELETE FROM emotion_log WHERE user_id = ?", (user_id,))
            self._conn.commit()
            return True
        except Exception as e:
            _log.error(f"删除用户情感数据失败: {e}")
            return False

    def get_stats(self) -> dict[str, Any]:
        """获取持久化统计"""
        try:
            state_count = self._conn.execute(
                "SELECT COUNT(*) FROM emotion_state"
            ).fetchone()[0]
            log_count = self._conn.execute(
                "SELECT COUNT(*) FROM emotion_log"
            ).fetchone()[0]

            return {
                "users_with_state": state_count,
                "total_logs": log_count,
                "db_path": self._db_path,
            }
        except Exception as e:
            _log.error(f"获取统计失败: {e}")
            return {"error": str(e)}

    def shutdown(self):
        """关闭数据库连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            _log.info("情感持久化已关闭")
