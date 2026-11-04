"""迁移 #005 — autobiography 表 user_id 隔离 + (user_id, diary_date) 唯一索引。

背景：
  - 蓝图 L6「猫咪日记 + 回忆相册」：历史版本的日记写入不填 user_id，
    且表上无唯一约束 —— INSERT OR REPLACE 从不触发替换，同一天可能有多行；
    多用户场景下日记串数据（A 用户能看到 B 用户的日记）。

影响：
  - 去重历史重复行（同 user_id + diary_date 保留最新一条）
  - 补建唯一索引 uk_autobiography_user_date，此后写入幂等
  - 旧数据 user_id 为空串，视为公共日记（新写入起按用户隔离）

回滚脚本：
  - 删除唯一索引（已去重的数据不可恢复重复行）
"""
import logging

_log = logging.getLogger("Migration_005")


def run(conn):
    """执行迁移：autobiography 去重 + 唯一索引。"""
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='autobiography'")
    if not cursor.fetchone():
        _log.warning("autobiography 表不存在，跳过迁移")
        return

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='uk_autobiography_user_date'")
    if cursor.fetchone():
        _log.info("autobiography 表已包含唯一索引，跳过迁移")
        return

    cursor.execute('''
        DELETE FROM autobiography WHERE id NOT IN (
            SELECT MAX(id) FROM autobiography GROUP BY user_id, diary_date
        )
    ''')
    cursor.execute('CREATE UNIQUE INDEX uk_autobiography_user_date '
                   'ON autobiography(user_id, diary_date)')
    conn.commit()
    _log.info("autobiography 表已去重并补建唯一索引 uk_autobiography_user_date")


def down(conn):
    """回滚迁移：删除唯一索引。"""
    cursor = conn.cursor()
    cursor.execute('DROP INDEX IF EXISTS uk_autobiography_user_date')
    conn.commit()
    _log.info("已回滚：autobiography 表已移除唯一索引（去重的数据不可恢复）")
