"""迁移 #004 — memories 表添加 pinned 列，支持用户钉住重要记忆。

背景：
  - 蓝图 L1「记忆卡片流」：用户可将重要记忆钉住，钉住的记忆
    在前端列表置顶展示，且在 AI 召回（get_memory_context_bundle）中
    优先注入、豁免退休与冷却，保证"用户钉住的设定永不遗忘"。

影响：
  - memories 表新增 pinned 列（INTEGER，0=未钉住 1=已钉住，默认 0）
  - 已有记忆默认全部未钉住，行为与升级前一致

回滚脚本：
  - 删除 pinned 列（钉住状态不可恢复）
"""
import logging

_log = logging.getLogger("Migration_004")


def run(conn):
    """执行迁移：为 memories 表添加 pinned 列。"""
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memories'")
    if not cursor.fetchone():
        _log.warning("memories 表不存在，跳过迁移")
        return

    cursor.execute("PRAGMA table_info(memories)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'pinned' in columns:
        _log.info("memories 表已包含 pinned 列，跳过迁移")
        return

    cursor.execute("ALTER TABLE memories ADD COLUMN pinned INTEGER DEFAULT 0")
    _log.info("已为 memories 表添加 pinned 列")

    conn.commit()


def down(conn):
    """回滚迁移：删除 pinned 列。"""
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(memories)")
    columns = [col[1] for col in cursor.fetchall()]

    if 'pinned' not in columns:
        _log.info("memories 表未包含 pinned 列，无需回滚")
        return

    cursor.execute("ALTER TABLE memories DROP COLUMN pinned")
    conn.commit()

    _log.info("已回滚：memories 表已移除 pinned 列（钉住状态不可恢复）")
