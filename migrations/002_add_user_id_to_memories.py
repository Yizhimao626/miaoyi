"""迁移 #002 — 为 memories 表添加 user_id 列，支持多用户记忆隔离。

影响：
  - memories 表新增 user_id 列（TEXT 类型）
  - 现有记忆的 user_id 设为空字符串（共享）
  - 创建 user_id 索引提升查询性能

执行时机：
  - 在任何用户登录前执行（避免数据混乱）
  - 或作为第 002 版本迁移脚本自动执行

回滚脚本：
  - 删除 user_id 列（所有记忆变为全局共享）
"""
import logging

_log = logging.getLogger("Migration_002")


def run(conn):
    """执行迁移：为 memories 表添加 user_id 列。"""
    cursor = conn.cursor()

    # 检查表是否存在
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
    if not cursor.fetchone():
        _log.warning("memories 表不存在，跳过迁移")
        return

    # 检查 user_id 列是否已存在
    cursor.execute("PRAGMA table_info(memories)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'user_id' in columns:
        _log.info("memories 表已包含 user_id 列，跳过迁移")
        return

    # 1. 添加 user_id 列（默认空字符串，兼容现有数据）
    cursor.execute("ALTER TABLE memories ADD COLUMN user_id TEXT DEFAULT ''")
    _log.info("已为 memories 表添加 user_id 列")

    # 2. 创建索引（提升按用户查询记忆的性能）
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)')
    _log.info("已创建 idx_memories_user 索引")

    # 3. 数据迁移说明（记录到日志）
    _log.info("迁移完成：现有记忆的 user_id 设为空字符串（全局共享），新记忆可按用户隔离")

    conn.commit()


def down(conn):
    """回滚迁移：删除 user_id 列。"""
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(memories)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'user_id' not in columns:
        _log.info("memories 表未包含 user_id 列，无需回滚")
        return

    cursor.execute("DROP INDEX IF EXISTS idx_memories_user")
    cursor.execute("ALTER TABLE memories DROP COLUMN user_id")
    conn.commit()

    _log.info("已回滚：memories 表已移除 user_id 列")
