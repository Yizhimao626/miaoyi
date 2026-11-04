"""
  🐱 喵艺数据库后端适配层 - SQLite / MySQL 双模式

  设计目标：
  - 业务层（DBManager）零改动即可切换数据库后端
  - MySQL 模式下自动完成 SQL 方言翻译：
      ? 占位符          -> %s
      INSERT OR REPLACE -> REPLACE INTO
      INSERT OR IGNORE  -> INSERT IGNORE INTO
      AUTOINCREMENT     -> AUTO_INCREMENT
      PRAGMA table_info -> information_schema 查询
      BEGIN IMMEDIATE   -> START TRANSACTION
      CREATE INDEX IF NOT EXISTS -> 运行时判断后创建
      TEXT DEFAULT x    -> TEXT DEFAULT (x)  (MySQL 8.0.13+ 表达式默认值)
      保留字列名 key/value -> `key` / `value`
  - 连接断线自动重连（ping）
  - 索引存在性检查（MySQL 不支持 CREATE INDEX IF NOT EXISTS）

  Usage:
      backend = DatabaseBackend({'type': 'mysql', 'mysql': {...}})
      conn = backend.connect()   # 返回 TranslatedConnection
"""
import logging
import os
import re
import sqlite3
import time

_log = logging.getLogger("MiaoyiDB")

# MySQL 保留字列名（需反引号包裹）
_MYSQL_RESERVED_COLS = {'key', 'value'}

# 字符串字面量分割：奇数段是 '...' 字面量，不做翻译
_STRING_LITERAL_SPLIT = re.compile(r"('(?:[^']|'')*')")

# 需要整体忽略的 PRAGMA
_PRAGMA_TABLE_INFO = re.compile(r"\s*PRAGMA\s+table_info\(\s*(\w+)\s*\)\s*;?\s*$", re.I)
_CREATE_INDEX_INE = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+ON\s+(\w+)\s*\((.+)\)\s*;?\s*$",
    re.I | re.S)


def _translate_segment(seg: str, dml: bool = True) -> str:
    """翻译一段 SQL（不含字符串字面量）。dml=False 时跳过保留字列名替换（保护 PRIMARY KEY 等 DDL 语法）"""
    s = seg

    # 1. BEGIN IMMEDIATE -> START TRANSACTION
    s = re.sub(r'\bBEGIN\s+IMMEDIATE\b', 'START TRANSACTION', s, flags=re.I)

    # 2. INSERT OR REPLACE / IGNORE
    s = re.sub(r'\bINSERT\s+OR\s+REPLACE\s+INTO\b', 'REPLACE INTO', s, flags=re.I)
    s = re.sub(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b', 'INSERT IGNORE INTO', s, flags=re.I)

    # 3. AUTOINCREMENT（含 INTEGER PRIMARY KEY AUTOINCREMENT 组合）
    s = re.sub(r'\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
               'BIGINT AUTO_INCREMENT PRIMARY KEY', s, flags=re.I)
    s = re.sub(r'\bAUTOINCREMENT\b', 'AUTO_INCREMENT', s, flags=re.I)

    # 4. TEXT/BLOB 的标量默认值 -> 表达式默认值（MySQL 8.0.13+）
    s = re.sub(r'\b(TEXT|BLOB)\s+DEFAULT\s+\'([^\']*)\'', r"\1 DEFAULT ('\2')", s, flags=re.I)
    s = re.sub(r'\b(TEXT|BLOB)\s+DEFAULT\s+(\d+(?:\.\d+)?)', r'\1 DEFAULT (\2)', s, flags=re.I)

    # 5. TEXT UNIQUE -> VARCHAR(191) UNIQUE（MySQL 的 TEXT 列不能做唯一键）
    s = re.sub(r'\bTEXT\s+UNIQUE\b', 'VARCHAR(191) UNIQUE', s, flags=re.I)

    # 6. 占位符 ? -> %s
    s = re.sub(r'\?', '%s', s)

    # 7. 保留字列名 -> 反引号（仅 DML；DDL 中 PRIMARY KEY/UNIQUE KEY 等语法不能被替换）
    if dml:
        for w in _MYSQL_RESERVED_COLS:
            s = re.sub(rf'(?<!`)\b{w}\b(?!`)', f'`{w}`', s, flags=re.I)

    return s


def translate_sql(sql: str) -> str:
    """SQLite SQL -> MySQL SQL（字符串字面量不做翻译）"""
    if not sql:
        return sql

    # 已是 MySQL 原生 DDL（含 ENGINE= 声明）则跳过翻译，避免破坏既有语法
    if 'ENGINE=' in sql.upper():
        return sql

    # PRAGMA table_info(tbl) -> information_schema 查询（保持列序：col[1]=列名）
    m = _PRAGMA_TABLE_INFO.match(sql)
    if m:
        t = m.group(1)
        # 【P2加固】表名进 SQL 前做标识符白名单校验：当前调用方均为硬编码表名，
        # 但这里是 f-string 直拼，未来传变量即成注入点。
        if not re.match(r'^\w+$', t):
            return 'SELECT 1 AS pragma_skipped'
        return (
            "SELECT 0 AS cid, COLUMN_NAME AS name, COLUMN_TYPE AS type, "
            "0 AS notnull, NULL AS dflt_value, 0 AS pk "
            "FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{t}' "
            "ORDER BY ORDINAL_POSITION"
        )

    # 其余 PRAGMA（journal_mode/synchronous/cache_size/optimize 等）MySQL 无需执行
    if re.match(r'\s*PRAGMA\s+', sql, re.I):
        return 'SELECT 1 AS pragma_skipped'

    # DDL 语句跳过保留字替换
    is_ddl = bool(re.match(r'\s*(CREATE|ALTER|DROP|TRUNCATE|RENAME)\b', sql, re.I))

    # 按字符串字面量分段，仅翻译代码段
    parts = _STRING_LITERAL_SPLIT.split(sql)
    for i in range(0, len(parts), 2):
        parts[i] = _translate_segment(parts[i], dml=not is_ddl)
    return ''.join(parts)


class DatabaseBackend:
    """数据库后端：根据配置创建 SQLite / MySQL 连接并提供方言翻译"""

    def __init__(self, db_config: dict = None):
        cfg = db_config or {}
        self.db_type = str(cfg.get('type', 'sqlite')).lower()
        if self.db_type not in ('sqlite', 'mysql'):
            _log.warning(f"[DB] 未知数据库类型 {self.db_type}，回退 SQLite")
            self.db_type = 'sqlite'
        self.mysql_cfg = cfg.get('mysql') or {}
        # 【安全】密码支持环境变量注入：config.yaml 中写 "${MYSQL_PASSWORD}" 占位符，
        # 实际密码从环境变量 / .env 读取，避免明文密钥入库（git 泄露风险）。
        _env_pw = os.environ.get('MYSQL_PASSWORD', '')
        _cfg_pw = str(self.mysql_cfg.get('password', '') or '')
        if _cfg_pw.startswith('${') or not _cfg_pw:
            if _env_pw:
                self.mysql_cfg['password'] = _env_pw
            elif self.db_type == 'mysql':
                _log.warning("[DB] MySQL 密码未配置：config.yaml 为占位符且环境变量 MYSQL_PASSWORD 为空")

        if self.db_type == 'mysql':
            import pymysql
            import pymysql.err
            self.IntegrityError = pymysql.err.IntegrityError
            self.OperationalError = pymysql.err.OperationalError
        else:
            self.IntegrityError = sqlite3.IntegrityError
            self.OperationalError = sqlite3.OperationalError

        self._raw_conn = None
        self._translated = None
        self._last_ping_ts = 0.0

    # ---------- 连接管理 ----------
    def _create_mysql_raw(self):
        """创建一个新的原生 MySQL 连接"""
        import pymysql
        return pymysql.connect(
            host=self.mysql_cfg.get('host', '127.0.0.1'),
            # 【P2修复】MySQL 标准端口是 3306；旧缺省 3303 会在配置遗漏时静默连错实例
            port=int(self.mysql_cfg.get('port', 3306)),
            user=self.mysql_cfg.get('user', 'miaoyi'),
            password=str(self.mysql_cfg.get('password', '')),
            database=self.mysql_cfg.get('database', 'miaoyi'),
            charset=self.mysql_cfg.get('charset', 'utf8mb4'),
            autocommit=False,
            connect_timeout=10,
        )

    def connect(self, db_path: str = 'miaoyi.db'):
        if self.db_type == 'mysql':
            self._raw_conn = self._create_mysql_raw()
            _log.info(f"[DB] 已连接 MySQL: {self.mysql_cfg.get('host')}:{self.mysql_cfg.get('port')}/{self.mysql_cfg.get('database')}")
        else:
            self._raw_conn = sqlite3.connect(db_path, check_same_thread=False)
            # 启用 WAL 模式，提升并发读写性能，减少锁冲突
            self._raw_conn.execute('PRAGMA journal_mode=WAL')
            self._raw_conn.execute('PRAGMA synchronous=NORMAL')
            self._raw_conn.execute('PRAGMA cache_size=10000')
            self._raw_conn.execute('PRAGMA optimize')
        self._translated = TranslatedConnection(self._raw_conn, self)
        self._last_ping_ts = time.time()
        return self._translated

    def reconnect(self):
        """强制重建底层连接，并把新连接换进已有的包装对象（业务方持有的 conn/cursor 引用不失效）"""
        if self.db_type != 'mysql':
            return
        old = self._raw_conn
        self._raw_conn = self._create_mysql_raw()
        try:
            if old is not None:
                old.close()
        except Exception:
            pass
        if self._translated is not None:
            self._translated._raw = self._raw_conn
        self._last_ping_ts = time.time()
        _log.info("[DB] MySQL 连接已重建")

    def is_connection_error(self, exc: Exception) -> bool:
        """判断异常是否为连接失效（2006 gone away / 2013 lost / 2027 / 关闭的文件句柄等）"""
        if self.db_type != 'mysql':
            return False
        try:
            import pymysql.err
            if isinstance(exc, (pymysql.err.OperationalError, pymysql.err.InterfaceError)):
                code = exc.args[0] if exc.args else None
                if code in (0, 2006, 2013, 2027, 2055):
                    return True
        except Exception:
            pass
        if isinstance(exc, ValueError) and 'closed file' in str(exc):
            return True
        return False

    def ensure_alive(self):
        """MySQL 断线自动重连（SQLite 无需）"""
        if self.db_type != 'mysql' or self._raw_conn is None:
            return
        now = time.time()
        if now - self._last_ping_ts < 30:
            return
        try:
            self._raw_conn.ping(reconnect=True)
            self._last_ping_ts = now
        except Exception as e:
            _log.warning(f"[DB] MySQL ping 失败，强制重建连接: {e}")
            try:
                self.reconnect()
            except Exception as e2:
                _log.error(f"[DB] MySQL 重建连接失败: {e2}")

    # ---------- 元数据 ----------
    def table_exists(self, cursor, table: str) -> bool:
        if self.db_type == 'mysql':
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,))
            return cursor.fetchone()[0] > 0
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,))
        return cursor.fetchone()[0] > 0

    def index_exists(self, cursor, index_name: str, table: str) -> bool:
        if self.db_type == 'mysql':
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s",
                (table, index_name))
            return cursor.fetchone()[0] > 0
        cursor.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name=?", (index_name,))
        return cursor.fetchone()[0] > 0

    # ---------- 运维 ----------
    def optimize(self, conn, tables):
        """SQLite: VACUUM / MySQL: OPTIMIZE TABLE"""
        if self.db_type == 'mysql':
            for t in tables:
                try:
                    conn.execute(f'OPTIMIZE TABLE `{t}`')
                except Exception as e:
                    _log.debug(f"[DB] OPTIMIZE {t} 失败: {e}")
        else:
            conn.execute('VACUUM')

    def checkpoint(self, raw_conn):
        """SQLite WAL checkpoint（MySQL 无需）"""
        if self.db_type == 'sqlite':
            try:
                raw_conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            except Exception:
                pass


class TranslatedConnection:
    """连接包装：MySQL 模式下自动翻译 SQL，业务代码无需感知后端差异"""

    def __init__(self, raw_conn, backend: DatabaseBackend):
        self._raw = raw_conn
        self._backend = backend
        self.mysql = backend.db_type == 'mysql'

    # ---------- 内部 ----------
    def _prepare(self, sql: str):
        """返回 (翻译后SQL, 特殊处理标记)"""
        if not self.mysql:
            return sql
        # CREATE INDEX IF NOT EXISTS：MySQL 不支持，运行时判断
        m = _CREATE_INDEX_INE.match(sql)
        if m:
            idx, table, cols = m.group(1), m.group(2), m.group(3)
            cur = self._raw.cursor()
            exists = self._backend.index_exists(cur, idx, table)
            cur.close()
            if exists:
                return None  # 已存在，跳过
            sql = _CREATE_INDEX_INE.sub(
                lambda mm: f"CREATE INDEX {mm.group(1)} ON {mm.group(2)} ({mm.group(3)})", sql)
        return translate_sql(sql)

    # ---------- 兼容 sqlite3.Connection 的接口 ----------
    def cursor(self):
        return _CursorWrapper(self._raw.cursor(), self)

    def execute(self, sql, params=()):
        w = self.cursor()
        return w.execute(sql, params)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    @property
    def in_transaction(self) -> bool:
        """当前是否处于未提交事务中（SQLite 原生支持；MySQL 视 autocommit=False 场景返回 False）"""
        if self.mysql:
            return False
        return bool(getattr(self._raw, 'in_transaction', False))

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class _CursorWrapper:
    """游标包装：翻译 SQL 并代理 fetch/属性"""

    def __init__(self, raw_cursor, conn: TranslatedConnection):
        self._cur = raw_cursor
        self._conn = conn
        self._backend = conn._backend

    def _do_execute(self, prepared, params, has_params):
        if has_params:
            return self._cur.execute(prepared, params)
        return self._cur.execute(prepared)

    def execute(self, sql, params=(), _retry: bool = True):
        self._backend.ensure_alive()
        prepared = self._conn._prepare(sql)
        if prepared is None:
            return self  # 索引已存在等无操作场景
        has_params = params is not None and len(params) > 0 \
            if isinstance(params, (tuple, list)) else bool(params)
        try:
            return self._do_execute(prepared, params, has_params)
        except Exception as e:
            # 连接失效：强制重建后用新游标重试一次
            if _retry and self._backend.is_connection_error(e):
                _log.warning(f"[DB] 查询命中失效连接，重建后重试: {e}")
                self._backend.reconnect()
                self._cur = self._conn._raw.cursor()
                return self.execute(sql, params, _retry=False)
            raise

    def executemany(self, sql, seq_of_params, _retry: bool = True):
        self._backend.ensure_alive()
        prepared = self._conn._prepare(sql)
        if prepared is None:
            return self
        try:
            return self._cur.executemany(prepared, seq_of_params)
        except Exception as e:
            if _retry and self._backend.is_connection_error(e):
                _log.warning(f"[DB] 批量查询命中失效连接，重建后重试: {e}")
                self._backend.reconnect()
                self._cur = self._conn._raw.cursor()
                return self.executemany(sql, seq_of_params, _retry=False)
            raise

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size) if size else self._cur.fetchmany()

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def load_db_config() -> dict:
    """从 config.yaml 读取数据库配置，支持环境变量 MIAOYI_DB_TYPE 覆盖"""
    cfg = {}
    try:
        import yaml
        with open('config.yaml', encoding='utf-8') as f:
            cfg = (yaml.safe_load(f) or {}).get('database') or {}
    except Exception as e:
        _log.debug(f"[DB] 读取 config.yaml 数据库配置失败（使用默认 SQLite）: {e}")

    import os
    env_type = os.environ.get('MIAOYI_DB_TYPE')
    if env_type:
        cfg = dict(cfg)
        cfg['type'] = env_type.lower()
    return cfg
