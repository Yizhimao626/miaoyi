"""迁移 #003 — memories 表去重并建立 (user_id, key) 唯一索引。

背景：
  - memories 建表时无 UNIQUE(user_id, key) 约束，INSERT OR REPLACE 从不触发替换；
    记忆学习系统反复写入同 key → 重复行堆积 → 前端列表出现大量重复项、
    React duplicate key 警告刷屏。

影响：
  - 删除重复行：每个 (user_id, key) 仅保留 id 最大（最新写入）的一行
  - 创建唯一索引 idx_memories_user_key，今后 INSERT OR REPLACE 正确去重

回滚脚本：
  - 删除唯一索引（已删除的重复行不可恢复）
"""
import logging

_log = logging.getLogger("Migration_003")


def run(conn):
    """执行迁移：去重 + 唯一索引。"""
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
    if not cursor.fetchone():
        _log.warning("memories 表不存在，跳过迁移")
        return

    # 幂等：唯一索引已存在则视为已迁移
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_memories_user_key'")
    if cursor.fetchone():
        _log.info("idx_memories_user_key 已存在，跳过迁移")
        return

    # 1. 归一化 NULL user_id（唯一索引中 NULL 互不相等，会绕过去重）
    cursor.execute("UPDATE memories SET user_id = '' WHERE user_id IS NULL")

    # 2. 去重：每个 (user_id, key) 保留 id 最大（最新写入）的一行
    cursor.execute('''
        DELETE FROM memories WHERE id NOT IN (
            SELECT MAX(id) FROM memories GROUP BY user_id, key
        )
    ''')
    deleted = cursor.rowcount
    if deleted > 0:
        _log.info(f"已删除 {deleted} 条重复记忆")

    # 3. 创建唯一索引（保证今后 INSERT OR REPLACE 生效）
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_user_key ON memories(user_id, key)')
    _log.info("已创建唯一索引 idx_memories_user_key(user_id, key)")

    conn.commit()


def down(conn):
    """回滚迁移：删除唯一索引。"""
    conn.execute('DROP INDEX IF EXISTS idx_memories_user_key')
    conn.commit()
    _log.info("已回滚：删除唯一索引 idx_memories_user_key（重复数据不可恢复）")
