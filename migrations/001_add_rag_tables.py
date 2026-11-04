#!/usr/bin/env python3
"""
  迁移脚本 #001 — 新建 RAG 四层记忆表
  ============================================================
  为 memory_rag.py 模块创建所需的四张数据库表：
    1. rag_emotional_memory    — 第一层：情感记忆（30天过期）
    2. rag_identity_memory     — 第二层：身份记忆（不可变）
    3. rag_fact_memory          — 第三层：事实记忆（90天过期）
    4. rag_interaction_memory   — 第四层：交互记忆（每日摘要）

  所有表均建立 user_id 索引以支持多用户隔离查询。
  设计遵循 db_manager.py 中的 SQLite 编码规范（REAL 时间戳、
  TEXT 默认空串、WAL 模式兼容）。
"""

import logging

_log = logging.getLogger("Migration_001")


def run(conn):
    """执行迁移：创建 RAG 四层记忆表及索引。

    Args:
        conn: sqlite3 数据库连接对象（已由 migration_runner 配置为 WAL 模式）
    """
    cursor = conn.cursor()

    # ========== 1. 情感记忆表 ==========
    # 存储关键情感事件（相遇、争吵、告白、夸奖等），保留30天
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rag_emotional_memory (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT DEFAULT '',
            event_type      TEXT NOT NULL,
            emotion         TEXT DEFAULT '',
            detail          TEXT NOT NULL,
            keywords        TEXT DEFAULT '',
            created_ts      REAL DEFAULT 0,
            expires_at      REAL DEFAULT 0,
            hash_vector     TEXT DEFAULT ''
        )
    ''')

    # user_id 索引：支持按用户隔离查询情感记忆
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_emotional_user
        ON rag_emotional_memory(user_id)
    ''')
    # event_type 索引：支持按事件类型筛选
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_emotional_event
        ON rag_emotional_memory(event_type)
    ''')
    # expires_at 索引：支持快速清理过期记忆
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_emotional_expires
        ON rag_emotional_memory(expires_at)
    ''')

    # ========== 2. 身份记忆表 ==========
    # 存储用户身份偏好信息（姓名、生日、职业等），不可变
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rag_identity_memory (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            key             TEXT NOT NULL,
            value           TEXT NOT NULL,
            confidence      REAL DEFAULT 1.0,
            created_ts      REAL DEFAULT 0,
            updated_ts      REAL DEFAULT 0,
            UNIQUE(user_id, key)
        )
    ''')

    # user_id 索引：支持按用户加载所有身份信息
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_identity_user
        ON rag_identity_memory(user_id)
    ''')
    # key 索引：支持按 key 快速查询
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_identity_key
        ON rag_identity_memory(key)
    ''')

    # ========== 3. 事实记忆表 ==========
    # 存储用户告知的事实（如"我是程序员"），保留90天
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rag_fact_memory (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT DEFAULT '',
            topic           TEXT NOT NULL,
            content         TEXT NOT NULL,
            confidence      REAL DEFAULT 1.0,
            source          TEXT DEFAULT '',
            created_ts      REAL DEFAULT 0,
            expires_at      REAL DEFAULT 0,
            hash_vector     TEXT DEFAULT ''
        )
    ''')

    # user_id 索引：支持按用户隔离查询事实记忆
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_fact_user
        ON rag_fact_memory(user_id)
    ''')
    # topic 索引：支持按主题筛选
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_fact_topic
        ON rag_fact_memory(topic)
    ''')
    # expires_at 索引：支持快速清理过期记忆
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_fact_expires
        ON rag_fact_memory(expires_at)
    ''')

    # ========== 4. 交互记忆表 ==========
    # 存储每日交互摘要，7天详细 + 30天周度汇总
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rag_interaction_memory (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         TEXT NOT NULL,
            summary         TEXT NOT NULL,
            day_date        TEXT NOT NULL,
            is_daily        INTEGER DEFAULT 1,
            created_ts      REAL DEFAULT 0,
            hash_vector     TEXT DEFAULT ''
        )
    ''')

    # user_id 索引：支持按用户隔离查询交互记忆
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_interaction_user
        ON rag_interaction_memory(user_id)
    ''')
    # day_date 索引：支持按日期范围查询
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_interaction_date
        ON rag_interaction_memory(day_date)
    ''')
    # is_daily 索引：支持区分日度/周度摘要
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_rag_interaction_daily
        ON rag_interaction_memory(is_daily)
    ''')

    conn.commit()
    _log.info("[迁移001] RAG 四层记忆表创建完成")


def down(conn):
    """回滚迁移：删除 RAG 四层记忆表。

    Args:
        conn: sqlite3 数据库连接对象
    """
    cursor = conn.cursor()

    # 按依赖相反顺序删除（先删有外键依赖的，再删被依赖的）
    # 本批次无非外键依赖，但保持规范
    cursor.execute('DROP INDEX IF EXISTS idx_rag_emotional_expires')
    cursor.execute('DROP INDEX IF EXISTS idx_rag_emotional_event')
    cursor.execute('DROP INDEX IF EXISTS idx_rag_emotional_user')
    cursor.execute('DROP TABLE IF EXISTS rag_emotional_memory')

    cursor.execute('DROP INDEX IF EXISTS idx_rag_fact_expires')
    cursor.execute('DROP INDEX IF EXISTS idx_rag_fact_topic')
    cursor.execute('DROP INDEX IF EXISTS idx_rag_fact_user')
    cursor.execute('DROP TABLE IF EXISTS rag_fact_memory')

    cursor.execute('DROP INDEX IF EXISTS idx_rag_interaction_daily')
    cursor.execute('DROP INDEX IF EXISTS idx_rag_interaction_date')
    cursor.execute('DROP INDEX IF EXISTS idx_rag_interaction_user')
    cursor.execute('DROP TABLE IF EXISTS rag_interaction_memory')

    cursor.execute('DROP INDEX IF EXISTS idx_rag_identity_key')
    cursor.execute('DROP INDEX IF EXISTS idx_rag_identity_user')
    cursor.execute('DROP TABLE IF EXISTS rag_identity_memory')

    conn.commit()
    _log.info("[迁移001] RAG 四层记忆表已回滚")