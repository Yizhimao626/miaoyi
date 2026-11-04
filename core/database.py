"""
喵艺 AI - 数据库抽象层
支持 SQLite (本地) 和 PostgreSQL (云端) 双模式
"""
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class DatabaseBackend(ABC):
    """数据库后端抽象基类"""
    
    @abstractmethod
    def initialize(self):
        """初始化数据库连接和表结构"""
        pass
    
    @abstractmethod
    def execute(self, sql: str, params: tuple = ()):
        """执行 SQL 语句"""
        pass
    
    @abstractmethod
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """查询并返回结果"""
        pass
    
    @abstractmethod
    def insert(self, table: str, data: dict) -> int:
        """插入数据并返回 ID"""
        pass
    
    @abstractmethod
    def update(self, table: str, data: dict, condition: str, params: tuple = ()):
        """更新数据"""
        pass
    
    @abstractmethod
    def delete(self, table: str, condition: str, params: tuple = ()):
        """删除数据"""
        pass
    
    @abstractmethod
    def close(self):
        """关闭数据库连接"""
        pass


class SQLiteBackend(DatabaseBackend):
    """SQLite 本地数据库后端"""
    
    def __init__(self, db_path: str = "miaoyi.db"):
        import sqlite3
        self.db_path = db_path
        self._conn = None
        self._sqlite = sqlite3
        
    def initialize(self):
        self._conn = self._sqlite.connect(self.db_path, check_same_thread=False)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._create_tables()
        self._conn.commit()
        
    def _create_tables(self):
        cursor = self._conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                nickname TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                avatar TEXT DEFAULT '',
                role TEXT DEFAULT 'user',
                emotion_state TEXT DEFAULT '{}',
                intimacy_level REAL DEFAULT 0,
                checkin_streak INTEGER DEFAULT 0,
                last_checkin_ts REAL DEFAULT 0,
                created_ts REAL DEFAULT 0,
                updated_ts REAL DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                user_id INTEGER,
                start_ts REAL DEFAULT 0,
                last_active_ts REAL DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                session_id TEXT,
                user_id INTEGER,
                role TEXT NOT NULL,
                text TEXT,
                sticker_path TEXT DEFAULT '',
                emotion_data TEXT DEFAULT '{}',
                thought TEXT DEFAULT '',
                created_ts REAL DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                content TEXT NOT NULL,
                memory_type TEXT DEFAULT 'general',
                emotion_tag TEXT DEFAULT '',
                importance REAL DEFAULT 0.5,
                access_count INTEGER DEFAULT 0,
                last_access_ts REAL DEFAULT 0,
                created_ts REAL DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emotion_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                emotion_type TEXT NOT NULL,
                intensity REAL DEFAULT 0,
                timestamp REAL DEFAULT 0,
                context TEXT DEFAULT ''
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stickers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                filepath TEXT NOT NULL,
                keyword TEXT DEFAULT '',
                category TEXT DEFAULT '',
                emotion TEXT DEFAULT '',
                meaning TEXT DEFAULT '',
                usage_count INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS proactive_chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                trigger_type TEXT NOT NULL,
                content TEXT NOT NULL,
                sent_ts REAL DEFAULT 0,
                is_sent INTEGER DEFAULT 0
            )
        ''')
    
    def execute(self, sql: str, params: tuple = ()):
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        self._conn.commit()
        return cursor.lastrowid
    
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    
    def insert(self, table: str, data: dict) -> int:
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?' for _ in data])
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        return self.execute(sql, tuple(data.values()))
    
    def update(self, table: str, data: dict, condition: str, params: tuple = ()):
        set_clause = ', '.join([f"{k} = ?" for k in data.keys()])
        sql = f"UPDATE {table} SET {set_clause} WHERE {condition}"
        self.execute(sql, tuple(data.values()) + params)
    
    def delete(self, table: str, condition: str, params: tuple = ()):
        sql = f"DELETE FROM {table} WHERE {condition}"
        self.execute(sql, params)
    
    def close(self):
        if self._conn:
            self._conn.close()


class PostgreSQLBackend(DatabaseBackend):
    """PostgreSQL 云端数据库后端 (预留接口)"""
    
    def __init__(self, connection_url: str):
        self.connection_url = connection_url
        self._conn = None
        
    def initialize(self):
        try:
            import psycopg2
            self._conn = psycopg2.connect(self.connection_url)
            self._create_tables()
        except ImportError:
            raise ImportError("请安装 psycopg2: pip install psycopg2-binary")
    
    def _create_tables(self):
        cursor = self._conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                nickname VARCHAR(100) DEFAULT '',
                phone VARCHAR(20) DEFAULT '',
                email VARCHAR(100) DEFAULT '',
                avatar TEXT DEFAULT '',
                role VARCHAR(20) DEFAULT 'user',
                emotion_state JSONB DEFAULT '{}',
                intimacy_level REAL DEFAULT 0,
                checkin_streak INTEGER DEFAULT 0,
                last_checkin_ts REAL DEFAULT 0,
                created_ts REAL DEFAULT 0,
                updated_ts REAL DEFAULT 0
            )
        ''')
        self._conn.commit()
    
    def execute(self, sql: str, params: tuple = ()):
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        self._conn.commit()
        return cursor.lastrowid
    
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
    
    def insert(self, table: str, data: dict) -> int:
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['%s' for _ in data])
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING id"
        return self.execute(sql, tuple(data.values()))
    
    def update(self, table: str, data: dict, condition: str, params: tuple = ()):
        set_clause = ', '.join([f"{k} = %s" for k in data.keys()])
        sql = f"UPDATE {table} SET {set_clause} WHERE {condition}"
        self.execute(sql, tuple(data.values()) + params)
    
    def delete(self, table: str, condition: str, params: tuple = ()):
        sql = f"DELETE FROM {table} WHERE {condition}"
        self.execute(sql, params)
    
    def close(self):
        if self._conn:
            self._conn.close()


class DatabaseFactory:
    """数据库工厂 - 根据配置创建对应后端"""
    
    @staticmethod
    def create(db_url: str | None = None) -> DatabaseBackend:
        if db_url and db_url.startswith('postgresql://'):
            backend = PostgreSQLBackend(db_url)
        else:
            db_path = os.environ.get('DATABASE_PATH', 'miaoyi.db')
            backend = SQLiteBackend(db_path)
        
        backend.initialize()
        return backend


class CacheManager:
    """缓存管理器 - 支持内存缓存和 Redis (预留接口)"""
    
    def __init__(self, redis_url: str | None = None):
        self._memory_cache: dict[str, Any] = {}
        self._redis_client = None
        
        if redis_url:
            try:
                import redis
                self._redis_client = redis.from_url(redis_url)
            except ImportError:
                pass
    
    def get(self, key: str) -> Any | None:
        if self._redis_client:
            try:
                value = self._redis_client.get(key)
                return value.decode() if value else None
            except:
                pass
        return self._memory_cache.get(key)
    
    def set(self, key: str, value: Any, ttl: int = 3600):
        if self._redis_client:
            try:
                self._redis_client.setex(key, ttl, str(value))
                return
            except:
                pass
        self._memory_cache[key] = value
    
    def delete(self, key: str):
        if self._redis_client:
            try:
                self._redis_client.delete(key)
                return
            except:
                pass
        self._memory_cache.pop(key, None)
    
    def clear(self):
        self._memory_cache.clear()
        if self._redis_client:
            try:
                self._redis_client.flushdb()
            except:
                pass
