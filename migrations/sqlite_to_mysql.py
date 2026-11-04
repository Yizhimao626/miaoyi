"""
  🐱 SQLite → MySQL 一键数据迁移脚本

  功能：
  - 读取 config.yaml 中 database.mysql 连接配置（可用命令行参数覆盖）
  - 在 MySQL 端创建完整详细 schema（复用 DBManager.MYSQL_SCHEMA_STATEMENTS）
  - 按外键依赖顺序迁移全部 22 张表数据（分批 500 行/次）
  - memories 表按 (user_id, key) 去重（保留最新一条，与唯一索引一致）
  - 迁移后重置 AUTO_INCREMENT 并输出各表数量核对报告

  Usage:
      # 使用 config.yaml 中的 MySQL 配置
      python migrations/sqlite_to_mysql.py

      # 命令行覆盖连接参数
      python migrations/sqlite_to_mysql.py --host 127.0.0.1 --port 3306 \
          --user miaoyi --password xxx --database miaoyi

      # 指定源 SQLite 文件（默认 miaoyi.db）
      python migrations/sqlite_to_mysql.py --source miaoyi.db

  注意：
  - 目标库中的同名表数据会被清空后重写（幂等，可重复执行）
  - 需要 MySQL >= 8.0.13（TEXT 表达式默认值）
"""
import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3

import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
_log = logging.getLogger("SQLiteToMySQL")

# 迁移顺序：先父表后子表（仅逻辑顺序，未启用外键约束）
TABLE_ORDER = [
    'users',               # 用户
    'conversations',       # 会话
    'stickers',            # 表情包
    'memories',            # 语义记忆（需去重）
    'memory_tags',         # 记忆标签
    'messages',            # 消息
    'emotions',            # 情感记录
    'growth',              # 成长数据
    'search_cache',        # 搜索缓存
    'learning',            # 自我学习
    'emotional_states',    # 情感状态
    'knowledge',           # 知识库
    'knowledge_triples',   # 知识三元组
    'emotional_memories',  # 情感记忆
    'autobiography',       # 喵艺日记
    'refresh_tokens',      # 刷新令牌
    'call_history',        # 通话历史
    'feedback',            # 用户反馈
    'rag_emotional_memory',   # RAG 情感记忆
    'rag_identity_memory',    # RAG 身份记忆
    'rag_fact_memory',        # RAG 事实记忆
    'rag_interaction_memory', # RAG 交互记忆
    'schema_version',      # 迁移版本
]

# 源库中的历史遗留空表（无代码引用、0 行数据），迁移时跳过并提示
LEGACY_SKIPPED = [
    'checkin', 'config', 'conversation', 'emotion', 'emotion_history',
    'emotion_memory', 'gif_collection', 'memory', 'relationship',
    'reminders', 'sticker_collection',
]

BATCH_SIZE = 500


def load_mysql_config(args) -> dict:
    """加载 MySQL 连接配置：config.yaml database.mysql + 命令行覆盖"""
    cfg = {}
    try:
        with open('config.yaml', encoding='utf-8') as f:
            cfg = ((yaml.safe_load(f) or {}).get('database') or {}).get('mysql') or {}
    except Exception as e:
        _log.warning(f"读取 config.yaml 失败（仅用命令行参数）: {e}")

    final = {
        'host': args.host or cfg.get('host', '127.0.0.1'),
        'port': args.port or int(cfg.get('port', 3306)),
        'user': args.user or cfg.get('user', 'miaoyi'),
        'password': args.password if args.password is not None else str(cfg.get('password', '')),
        'database': args.database or cfg.get('database', 'miaoyi'),
    }
    return final


def dedupe_memories(sqlite_conn) -> int:
    """memories 表按 (user_id, key) 去重，保留最新一条。返回去重后行数"""
    cur = sqlite_conn.cursor()
    cur.execute("SELECT COUNT(*) FROM memories")
    total = cur.fetchone()[0]
    # 保留 id 最大（最新）的一条
    cur.execute('''
        SELECT COUNT(*) FROM (
            SELECT id FROM memories m
            WHERE NOT EXISTS (
                SELECT 1 FROM memories newer
                WHERE newer.user_id IS m.user_id AND newer.`key` IS m.key AND newer.id > m.id
            )
        )
    ''')
    kept = cur.fetchone()[0]
    _log.info(f"[memories] 原始 {total} 行，按 (user_id, key) 去重后保留 {kept} 行")
    return kept


def copy_table(sqlite_conn, mysql_conn, table: str, skip_missing=True) -> int:
    """迁移单表（分批 executemany）。返回迁移行数"""
    # 【加固】表名进 SQL 前做标识符白名单校验（调用方为脚本内置清单，防配置污染）
    import re as _re
    if not _re.match(r'^\w+$', str(table)):
        raise ValueError(f'非法表名: {table!r}')
    s_cur = sqlite_conn.cursor()
    m_cur = mysql_conn.cursor()

    # 源表列
    s_cur.execute(f"PRAGMA table_info({table})")
    src_cols = [r[1] for r in s_cur.fetchall()]
    if not src_cols:
        _log.warning(f"[{table}] 源表不存在，跳过")
        return 0

    # 目标表列（只迁移交集，目标额外的列走默认值）
    m_cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION",
        (table,))
    dst_cols = {r[0] for r in m_cur.fetchall()}
    cols = [c for c in src_cols if c in dst_cols]
    missing = [c for c in src_cols if c not in dst_cols]
    if missing:
        _log.warning(f"[{table}] 源有目标无的列（跳过）: {missing}")

    # memories 特殊处理：按 (user_id, key) 去重
    if table == 'memories':
        s_cur.execute(f"SELECT {', '.join(cols)} FROM memories ORDER BY id ASC")
        rows = s_cur.fetchall()
        idx_u = cols.index('user_id') if 'user_id' in cols else None
        idx_k = cols.index('key')
        dedup = {}
        for r in rows:
            k = (str(r[idx_u]) if idx_u is not None else '', str(r[idx_k]))
            dedup[k] = r  # 后写的覆盖先写的 = 保留最新
        all_rows = list(dedup.values())
    else:
        s_cur.execute(f"SELECT {', '.join(cols)} FROM {table}")
        all_rows = s_cur.fetchall()

    if not all_rows:
        _log.info(f"[{table}] 0 行")
        return 0

    col_sql = ', '.join(f'`{c}`' for c in cols)
    ph = ', '.join(['%s'] * len(cols))
    insert_sql = f"REPLACE INTO `{table}` ({col_sql}) VALUES ({ph})"

    count = 0
    for i in range(0, len(all_rows), BATCH_SIZE):
        batch = all_rows[i:i + BATCH_SIZE]
        m_cur.executemany(insert_sql, batch)
        mysql_conn.commit()
        count += len(batch)
    return count


def reset_auto_increment(mysql_conn, table: str):
    """重置 AUTO_INCREMENT 为当前最大 id + 1（保证新数据 id 不冲突）。
    跳过无自增主键的表（如 schema_version 主键为 version）"""
    m_cur = mysql_conn.cursor()
    m_cur.execute(
        "SELECT COLUMN_NAME, EXTRA FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND EXTRA LIKE '%%auto_increment%%'", (table,))
    row = m_cur.fetchone()
    if not row:
        return  # 无自增列，无需重置
    pk_col = row[0]
    m_cur.execute(f"SELECT COALESCE(MAX(`{pk_col}`), 0) FROM `{table}`")
    max_id = m_cur.fetchone()[0]
    if max_id > 0:
        m_cur.execute(f"ALTER TABLE `{table}` AUTO_INCREMENT = {max_id + 1}")
        mysql_conn.commit()


def main():
    parser = argparse.ArgumentParser(description='喵艺 SQLite → MySQL 数据迁移')
    parser.add_argument('--source', default='miaoyi.db', help='源 SQLite 文件路径')
    parser.add_argument('--host', default=None)
    parser.add_argument('--port', default=None, type=int)
    parser.add_argument('--user', default=None)
    parser.add_argument('--password', default=None)
    parser.add_argument('--database', default=None)
    args = parser.parse_args()

    mysql_cfg = load_mysql_config(args)
    _log.info(f"目标 MySQL: {mysql_cfg['host']}:{mysql_cfg['port']}/{mysql_cfg['database']}")

    if not os.path.exists(args.source):
        _log.error(f"源 SQLite 文件不存在: {args.source}")
        sys.exit(1)

    # 1. 连接源 SQLite
    sqlite_conn = sqlite3.connect(args.source, check_same_thread=False)
    sqlite_cur = sqlite_conn.cursor()
    sqlite_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    src_tables = {r[0] for r in sqlite_cur.fetchall()}
    _log.info(f"源库包含 {len(src_tables)} 张表: {sorted(src_tables)}")

    # 历史遗留空表核对：确认跳过的表确实为空（防误判丢数据）
    for t in LEGACY_SKIPPED:
        if t in src_tables:
            sqlite_cur.execute(f"SELECT COUNT(*) FROM {t}")
            n = sqlite_cur.fetchone()[0]
            if n > 0:
                _log.warning(f"[{t}] 标记为遗留表但含 {n} 行数据！如需保留请加入 TABLE_ORDER")
            else:
                _log.info(f"[{t}] 遗留空表，跳过")

    # 2. 连接目标 MySQL（复用 DBManager 的建表逻辑，自动创建全部详细 schema）
    _log.info("正在创建/校验 MySQL 表结构...")
    from db_manager import DBManager
    db = DBManager(db_path=args.source, db_config={
        'type': 'mysql',
        'mysql': mysql_cfg,
    })
    mysql_conn = db._conn._raw  # 原生 pymysql 连接（无需翻译）
    _log.info("MySQL 表结构就绪")

    # 3. 清空目标表（幂等重跑）
    m_cur = mysql_conn.cursor()
    m_cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for t in TABLE_ORDER:
        m_cur.execute(f"TRUNCATE TABLE `{t}`")
    mysql_conn.commit()
    m_cur.execute("SET FOREIGN_KEY_CHECKS = 1")

    # 4. 逐表迁移
    report = {}
    start = time.time()
    for t in TABLE_ORDER:
        if t not in src_tables:
            _log.info(f"[{t}] 源库无此表，跳过")
            report[t] = 0
            continue
        n = copy_table(sqlite_conn, mysql_conn, t)
        reset_auto_increment(mysql_conn, t)
        report[t] = n
        _log.info(f"[{t}] 迁移 {n} 行")

    elapsed = time.time() - start

    # 5. 数量核对
    _log.info("=" * 60)
    _log.info(f"{'表名':<22}{'SQLite源':>12}{'MySQL目标':>12}{'状态':>6}")
    all_ok = True
    for t in TABLE_ORDER:
        if t not in src_tables:
            continue
        sqlite_cur.execute(f"SELECT COUNT(*) FROM {t}")
        src_n = sqlite_cur.fetchone()[0]
        if t == 'memories':
            s_cur2 = sqlite_conn.cursor()
            s_cur2.execute('''
                SELECT COUNT(*) FROM memories m
                WHERE NOT EXISTS (
                    SELECT 1 FROM memories newer
                    WHERE newer.user_id IS m.user_id AND newer.`key` IS m.key AND newer.id > m.id
                )
            ''')
            src_n = s_cur2.fetchone()[0]
        m_cur.execute(f"SELECT COUNT(*) FROM `{t}`")
        dst_n = m_cur.fetchone()[0]
        ok = '✓' if src_n == dst_n else '✗'
        if src_n != dst_n:
            all_ok = False
        _log.info(f"{t:<22}{src_n:>12}{dst_n:>12}{ok:>6}")

    _log.info("=" * 60)
    _log.info(f"迁移完成，耗时 {elapsed:.1f} 秒，总计 {sum(report.values())} 行")
    if all_ok:
        _log.info("✅ 全部表数量核对一致")
    else:
        _log.warning("⚠️ 存在数量不一致的表，请检查上方报告")

    # 6. 收尾
    sqlite_conn.close()
    db.close()


if __name__ == '__main__':
    main()
