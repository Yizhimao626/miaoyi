#!/usr/bin/env python3
"""
  喵艺四层记忆 + RAG 系统
  ============================================================
  第一层：情感记忆 (EmotionalMemory) - 关键事件，30天过期
  第二层：身份记忆 (IdentityMemory) - 用户身份信息，不可变
  第三层：事实记忆 (FactMemory) - 用户告知的事实，90天过期
  第四层：交互记忆 (InteractionMemory) - 每日交互摘要
  ============================================================
  设计原则：
  - 分层存储，不同类型记忆独立管理
  - 支持过期机制（时间衰减 + 置信度）
  - 基于哈希的轻量向量相似度检索（无需重型模型）
  - 后台自动清理过期记忆
  - 优雅降级：DBManager 不可用时自动使用内存存储
"""

import hashlib
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

_log = logging.getLogger("MemoryRAG")


class EmotionalMemory:
    """第一层：情感记忆 - 存储关键情感事件
    
    记录用户的重要情感事件（相遇、争吵、告白、夸奖等），
    保留30天，支持按情感类型和关键词检索。
    """

    EXPIRY_DAYS = 30

    def __init__(self, db_instance=None):
        self._db = db_instance
        self._cache: list[dict] = []
        self._lock = threading.Lock()
        self._ensure_table()

    def _ensure_table(self):
        """确保情感记忆表存在"""
        if not self._db:
            return
        try:
            with self._db._lock:
                self._db._conn.execute('''
                    CREATE TABLE IF NOT EXISTS rag_emotional_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT DEFAULT (''),
                        event_type TEXT NOT NULL,
                        emotion TEXT DEFAULT (''),
                        detail TEXT NOT NULL,
                        keywords TEXT DEFAULT (''),
                        created_ts REAL DEFAULT 0,
                        expires_at REAL DEFAULT 0,
                        hash_vector TEXT DEFAULT ('')
                    )
                ''')
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_emotional_user '
                    'ON rag_emotional_memory(user_id)'
                )
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_emotional_event '
                    'ON rag_emotional_memory(event_type)'
                )
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_emotional_expires '
                    'ON rag_emotional_memory(expires_at)'
                )
                self._db._conn.commit()
                _log.info("[情感记忆] 表结构已就绪")
        except Exception as e:
            _log.warning(f"[情感记忆] 建表失败(将使用内存存储): {e}")
            self._db = None

    def save(self, user_id: str, event_type: str, emotion: str, detail: str) -> int:
        """保存情感事件"""
        now = time.time()
        expires_at = now + self.EXPIRY_DAYS * 86400
        keywords = self._extract_keywords(detail)
        hash_vector = self._compute_hash_vector(detail)

        record = {
            'user_id': user_id,
            'event_type': event_type,
            'emotion': emotion,
            'detail': detail,
            'keywords': keywords,
            'created_ts': now,
            'expires_at': expires_at,
            'hash_vector': hash_vector,
        }

        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        INSERT INTO rag_emotional_memory
                        (user_id, event_type, emotion, detail, keywords,
                         created_ts, expires_at, hash_vector)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        user_id, event_type, emotion, detail,
                        keywords, now, expires_at, hash_vector
                    ))
                    self._db._conn.commit()
                    record['id'] = cursor.lastrowid
            except Exception as e:
                _log.warning(f"[情感记忆] DB保存失败(降级内存): {e}")
                with self._lock:
                    record['id'] = len(self._cache) + 1
                    self._cache.append(record)
        else:
            with self._lock:
                record['id'] = len(self._cache) + 1
                self._cache.append(record)

        _log.debug(f"[情感记忆] 保存事件: {event_type} - {detail[:30]}")
        return record['id']

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        """搜索情感记忆"""
        query_vector = self._compute_hash_vector(query)
        results = []

        records = self._load_records(user_id)
        for rec in records:
            score = self._cosine_similarity(rec.get('hash_vector', ''), query_vector)
            keyword_bonus = 0.0
            for kw in self._extract_keywords(query):
                if kw.lower() in rec.get('keywords', '').lower():
                    keyword_bonus += 0.3
            rec['_score'] = score + keyword_bonus
            if rec['_score'] > 0:
                results.append(rec)

        results.sort(key=lambda x: x['_score'], reverse=True)
        return results[:limit]

    def _load_records(self, user_id: str) -> list[dict]:
        """加载指定用户的情感记忆"""
        now = time.time()
        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        SELECT id, user_id, event_type, emotion, detail,
                               keywords, created_ts, expires_at, hash_vector
                        FROM rag_emotional_memory
                        WHERE user_id = ? AND expires_at > ?
                        ORDER BY created_ts DESC LIMIT 200
                    ''', (user_id, now))
                    rows = cursor.fetchall()
                    return [self._row_to_dict(row) for row in rows]
            except Exception as e:
                _log.warning(f"[情感记忆] 从DB加载失败: {e}")

        with self._lock:
            return [r for r in self._cache
                    if r['user_id'] == user_id and r.get('expires_at', 0) > now]

    def cleanup_expired(self):
        """清理过期情感记忆"""
        now = time.time()
        if self._db:
            try:
                with self._db._lock:
                    self._db._conn.execute(
                        'DELETE FROM rag_emotional_memory WHERE expires_at < ?',
                        (now,)
                    )
                    self._db._conn.commit()
            except Exception as e:
                _log.warning(f"[情感记忆] 清理失败: {e}")

        with self._lock:
            self._cache = [r for r in self._cache
                          if r.get('expires_at', 0) > now]

    @staticmethod
    def _extract_keywords(text: str) -> str:
        """提取关键词（简单分词）"""
        words = re.findall(r'[\u4e00-\u9fa5a-zA-Z]+', text.lower())
        stop_words = {'的', '了', '和', '是', '就', '都', '而', '及', '与',
                      '着', '或', '一个', '我们', '你们', '他们', '它', '这',
                      '那', '在', '有', '我', '你', '他', '她', '也', '很',
                      '太', '非常', '不', '没有', '可以', '能', '会', '应该',
                      '需要', '已经', '正在', '将', '要', '什么', '怎么',
                      '没', '说', '请', '吧', '呢', '啊', '哦', '嗯'}
        keywords = [w for w in words if len(w) >= 2 and w not in stop_words]
        return ','.join(keywords[:10])

    @staticmethod
    def _compute_hash_vector(text: str) -> str:
        """基于MD5哈希生成伪向量表示"""
        if not text:
            return ''
        text = text.lower().strip()
        words = re.findall(r'[\u4e00-\u9fa5a-zA-Z]+', text)
        vector_parts = []
        for word in words:
            h = hashlib.md5(word.encode('utf-8')).hexdigest()
            vector_parts.append(h[:8])
        return '|'.join(vector_parts[:50])

    @staticmethod
    def _cosine_similarity(vec_a: str, vec_b: str) -> float:
        """基于哈希向量的余弦相似度"""
        if not vec_a or not vec_b:
            return 0.0
        set_a = set(vec_a.split('|'))
        set_b = set(vec_b.split('|'))
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        if not union:
            return 0.0
        return len(intersection) / len(union)

    @staticmethod
    def _row_to_dict(row) -> dict:
        """将数据库行转为字典"""
        return {
            'id': row[0], 'user_id': row[1], 'event_type': row[2],
            'emotion': row[3], 'detail': row[4], 'keywords': row[5],
            'created_ts': row[6], 'expires_at': row[7],
            'hash_vector': row[8],
        }


class IdentityMemory:
    """第二层：身份记忆 - 用户身份偏好信息
    
    存储用户的姓名、生日、偏好等，一旦设定不可变，
    只有用户明确要求修改时才会更新。
    """

    def __init__(self, db_instance=None):
        self._db = db_instance
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._ensure_table()

    def _ensure_table(self):
        """确保身份记忆表存在"""
        if not self._db:
            return
        try:
            with self._db._lock:
                self._db._conn.execute('''
                    CREATE TABLE IF NOT EXISTS rag_identity_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        `key` TEXT NOT NULL,
                        value TEXT NOT NULL,
                        confidence REAL DEFAULT 1.0,
                        created_ts REAL DEFAULT 0,
                        updated_ts REAL DEFAULT 0,
                        UNIQUE(user_id, `key`)
                    )
                ''')
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_identity_user '
                    'ON rag_identity_memory(user_id)'
                )
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_identity_key '
                    'ON rag_identity_memory(key)'
                )
                self._db._conn.commit()
                _log.info("[身份记忆] 表结构已就绪")
        except Exception as e:
            _log.warning(f"[身份记忆] 建表失败(将使用内存存储): {e}")
            self._db = None

    def save(self, user_id: str, key: str, value: str, confidence: float = 1.0) -> bool:
        """保存身份信息（已存在的key需要显式修改）"""
        now = time.time()

        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        SELECT id, value FROM rag_identity_memory
                        WHERE user_id = ? AND key = ?
                    ''', (user_id, key))
                    existing = cursor.fetchone()

                    if existing:
                        _log.debug(f"[身份记忆] key '{key}' 已存在，跳过写入（需显式修改）")
                        return False

                    self._db._conn.execute('''
                        INSERT INTO rag_identity_memory
                        (user_id, key, value, confidence, created_ts, updated_ts)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (user_id, key, value, confidence, now, now))
                    self._db._conn.commit()
                    with self._lock:
                        self._cache[f"{user_id}:{key}"] = {
                            'id': cursor.lastrowid or len(self._cache) + 1,
                            'user_id': user_id, 'key': key, 'value': value,
                            'confidence': confidence, 'created_ts': now,
                            'updated_ts': now,
                        }
                    _log.debug(f"[身份记忆] 新增: {key} = {value}")
                    return True
            except Exception as e:
                _log.warning(f"[身份记忆] DB保存失败(降级内存): {e}")

        with self._lock:
            cache_key = f"{user_id}:{key}"
            if cache_key in self._cache:
                _log.debug(f"[身份记忆] 内存中key '{key}' 已存在，跳过写入")
                return False
            self._cache[cache_key] = {
                'id': len(self._cache) + 1,
                'user_id': user_id, 'key': key, 'value': value,
                'confidence': confidence, 'created_ts': now,
                'updated_ts': now,
            }
            _log.debug(f"[身份记忆] 新增(内存): {key} = {value}")
            return True

    def force_update(self, user_id: str, key: str, value: str) -> bool:
        """强制更新身份信息（用户明确要求修改时调用）"""
        now = time.time()

        if self._db:
            try:
                with self._db._lock:
                    self._db._conn.execute('''
                        UPDATE rag_identity_memory
                        SET value = ?, updated_ts = ?
                        WHERE user_id = ? AND key = ?
                    ''', (value, now, user_id, key))
                    self._db._conn.commit()
                    changed = self._db._conn.total_changes
                    if changed > 0:
                        with self._lock:
                            cache_key = f"{user_id}:{key}"
                            if cache_key in self._cache:
                                self._cache[cache_key]['value'] = value
                                self._cache[cache_key]['updated_ts'] = now
                        _log.info(f"[身份记忆] 显式更新: {key} = {value}")
                        return True
                    else:
                        return self.save(user_id, key, value)
            except Exception as e:
                _log.warning(f"[身份记忆] DB更新失败(降级内存): {e}")

        with self._lock:
            cache_key = f"{user_id}:{key}"
            if cache_key in self._cache:
                self._cache[cache_key]['value'] = value
                self._cache[cache_key]['updated_ts'] = now
                _log.info(f"[身份记忆] 显式更新(内存): {key} = {value}")
                return True
            else:
                self._cache[cache_key] = {
                    'id': len(self._cache) + 1,
                    'user_id': user_id, 'key': key, 'value': value,
                    'confidence': 1.0, 'created_ts': now, 'updated_ts': now,
                }
                return True

    def get(self, user_id: str, key: str) -> str | None:
        """获取单条身份信息"""
        cache_key = f"{user_id}:{key}"
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]['value']

        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        SELECT value FROM rag_identity_memory
                        WHERE user_id = ? AND key = ?
                    ''', (user_id, key))
                    row = cursor.fetchone()
                    if row:
                        with self._lock:
                            self._cache[cache_key] = {
                                'user_id': user_id, 'key': key,
                                'value': row[0], 'confidence': 1.0,
                                'created_ts': time.time(), 'updated_ts': time.time(),
                            }
                        return row[0]
            except Exception as e:
                _log.warning(f"[身份记忆] DB查询失败: {e}")
        return None

    def get_all(self, user_id: str) -> dict[str, str]:
        """获取用户所有身份信息"""
        result = {}

        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        SELECT key, value FROM rag_identity_memory
                        WHERE user_id = ?
                    ''', (user_id,))
                    for row in cursor.fetchall():
                        result[row[0]] = row[1]
            except Exception as e:
                _log.warning(f"[身份记忆] DB加载失败: {e}")

        with self._lock:
            for cache_key, rec in self._cache.items():
                if rec['user_id'] == user_id and rec['key'] not in result:
                    result[rec['key']] = rec['value']

        return result


class FactMemory:
    """第三层：事实记忆 - 用户告知的事实信息
    
    存储用户告诉她的事实（如"我是程序员"），
    保留90天，带有置信度等级。
    """

    EXPIRY_DAYS = 90

    def __init__(self, db_instance=None):
        self._db = db_instance
        self._cache: list[dict] = []
        self._lock = threading.Lock()
        self._ensure_table()

    def _ensure_table(self):
        """确保事实记忆表存在"""
        if not self._db:
            return
        try:
            with self._db._lock:
                self._db._conn.execute('''
                    CREATE TABLE IF NOT EXISTS rag_fact_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT DEFAULT (''),
                        topic TEXT NOT NULL,
                        content TEXT NOT NULL,
                        confidence REAL DEFAULT 1.0,
                        source TEXT DEFAULT (''),
                        created_ts REAL DEFAULT 0,
                        expires_at REAL DEFAULT 0,
                        hash_vector TEXT DEFAULT ('')
                    )
                ''')
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_fact_user '
                    'ON rag_fact_memory(user_id)'
                )
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_fact_topic '
                    'ON rag_fact_memory(topic)'
                )
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_fact_expires '
                    'ON rag_fact_memory(expires_at)'
                )
                self._db._conn.commit()
                _log.info("[事实记忆] 表结构已就绪")
        except Exception as e:
            _log.warning(f"[事实记忆] 建表失败(将使用内存存储): {e}")
            self._db = None

    def save(self, user_id: str, topic: str, content: str,
             confidence: float = 1.0) -> int:
        """保存一个事实"""
        now = time.time()
        expires_at = now + self.EXPIRY_DAYS * 86400
        hash_vector = EmotionalMemory._compute_hash_vector(f"{topic} {content}")

        record = {
            'user_id': user_id, 'topic': topic, 'content': content,
            'confidence': confidence, 'source': 'user',
            'created_ts': now, 'expires_at': expires_at,
            'hash_vector': hash_vector,
        }

        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        INSERT INTO rag_fact_memory
                        (user_id, topic, content, confidence, source,
                         created_ts, expires_at, hash_vector)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        user_id, topic, content, confidence, 'user',
                        now, expires_at, hash_vector
                    ))
                    self._db._conn.commit()
                    record['id'] = cursor.lastrowid
            except Exception as e:
                _log.warning(f"[事实记忆] DB保存失败(降级内存): {e}")
                with self._lock:
                    record['id'] = len(self._cache) + 1
                    self._cache.append(record)
        else:
            with self._lock:
                record['id'] = len(self._cache) + 1
                self._cache.append(record)

        _log.debug(f"[事实记忆] 保存: {topic} = {content[:30]}")
        return record['id']

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        """搜索事实记忆"""
        query_vector = EmotionalMemory._compute_hash_vector(query)
        results = []

        records = self._load_records(user_id)
        for rec in records:
            score = EmotionalMemory._cosine_similarity(
                rec.get('hash_vector', ''), query_vector
            )
            if query.lower() in rec.get('topic', '').lower():
                score += 0.3
            if query.lower() in rec.get('content', '').lower():
                score += 0.2
            score *= rec.get('confidence', 1.0)
            rec['_score'] = score
            if rec['_score'] > 0:
                results.append(rec)

        results.sort(key=lambda x: x['_score'], reverse=True)
        return results[:limit]

    def _load_records(self, user_id: str) -> list[dict]:
        """加载指定用户的事实记忆"""
        now = time.time()
        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        SELECT id, user_id, topic, content, confidence,
                               source, created_ts, expires_at, hash_vector
                        FROM rag_fact_memory
                        WHERE user_id = ? AND expires_at > ?
                        ORDER BY confidence DESC, created_ts DESC LIMIT 200
                    ''', (user_id, now))
                    rows = cursor.fetchall()
                    return [self._row_to_dict(row) for row in rows]
            except Exception as e:
                _log.warning(f"[事实记忆] 从DB加载失败: {e}")

        with self._lock:
            return [r for r in self._cache
                    if r['user_id'] == user_id and r.get('expires_at', 0) > now]

    def cleanup_expired(self):
        """清理过期事实记忆"""
        now = time.time()
        if self._db:
            try:
                with self._db._lock:
                    self._db._conn.execute(
                        'DELETE FROM rag_fact_memory WHERE expires_at < ?',
                        (now,)
                    )
                    self._db._conn.commit()
            except Exception as e:
                _log.warning(f"[事实记忆] 清理失败: {e}")

        with self._lock:
            self._cache = [r for r in self._cache
                          if r.get('expires_at', 0) > now]

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            'id': row[0], 'user_id': row[1], 'topic': row[2],
            'content': row[3], 'confidence': row[4], 'source': row[5],
            'created_ts': row[6], 'expires_at': row[7],
            'hash_vector': row[8],
        }


class InteractionMemory:
    """第四层：交互记忆 - 每日交互摘要
    
    记录每日交互摘要，最近7天保留详细内容，
    超过7天的自动汇总为周度摘要。
    """

    DETAILED_DAYS = 7
    WEEKLY_DAYS = 30

    def __init__(self, db_instance=None):
        self._db = db_instance
        self._cache: list[dict] = []
        self._lock = threading.Lock()
        self._ensure_table()

    def _ensure_table(self):
        """确保交互记忆表存在"""
        if not self._db:
            return
        try:
            with self._db._lock:
                self._db._conn.execute('''
                    CREATE TABLE IF NOT EXISTS rag_interaction_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        day_date TEXT NOT NULL,
                        is_daily INTEGER DEFAULT 1,
                        created_ts REAL DEFAULT 0,
                        hash_vector TEXT DEFAULT ('')
                    )
                ''')
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_interaction_user '
                    'ON rag_interaction_memory(user_id)'
                )
                self._db._conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_rag_interaction_user_date '
                    'ON rag_interaction_memory(user_id, day_date)'
                )
                self._db._conn.commit()
                _log.info("[交互记忆] 表结构已就绪")
        except Exception as e:
            _log.warning(f"[交互记忆] 建表失败(将使用内存存储): {e}")
            self._db = None

    def update(self, user_id: str, summary: str) -> int:
        """更新当日交互摘要（追加或合并）"""
        today = datetime.now().strftime('%Y-%m-%d')
        now = time.time()
        hash_vector = EmotionalMemory._compute_hash_vector(summary)

        existing_id = self._find_today(user_id, today)

        if existing_id:
            return self._append_to(existing_id, summary)

        record = {
            'user_id': user_id, 'summary': summary,
            'day_date': today, 'is_daily': 1,
            'created_ts': now, 'hash_vector': hash_vector,
        }

        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        INSERT INTO rag_interaction_memory
                        (user_id, summary, day_date, is_daily,
                         created_ts, hash_vector)
                        VALUES (?, ?, ?, 1, ?, ?)
                    ''', (user_id, summary, today, now, hash_vector))
                    self._db._conn.commit()
                    record['id'] = cursor.lastrowid
            except Exception as e:
                _log.warning(f"[交互记忆] DB保存失败(降级内存): {e}")
                with self._lock:
                    record['id'] = len(self._cache) + 1
                    self._cache.append(record)
        else:
            with self._lock:
                record['id'] = len(self._cache) + 1
                self._cache.append(record)

        _log.debug(f"[交互记忆] 新增当日摘要: {summary[:50]}")
        return record['id']

    def _find_today(self, user_id: str, day_date: str) -> int | None:
        """查找今日是否已有摘要"""
        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        SELECT id FROM rag_interaction_memory
                        WHERE user_id = ? AND day_date = ? AND is_daily = 1
                    ''', (user_id, day_date))
                    row = cursor.fetchone()
                    if row:
                        return row[0]
            except Exception:
                pass

        with self._lock:
            for rec in self._cache:
                if (rec['user_id'] == user_id and
                    rec['day_date'] == day_date and
                    rec.get('is_daily', 1) == 1):
                    return rec['id']
        return None

    def _append_to(self, record_id: int, new_summary: str) -> int:
        """追加内容到已有摘要"""
        separator = ' | '
        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        SELECT summary FROM rag_interaction_memory WHERE id = ?
                    ''', (record_id,))
                    row = cursor.fetchone()
                    if row:
                        old_summary = row[0] or ''
                        combined = old_summary + separator + new_summary
                        hash_vector = EmotionalMemory._compute_hash_vector(combined)
                        self._db._conn.execute('''
                            UPDATE rag_interaction_memory
                            SET summary = ?, hash_vector = ?
                            WHERE id = ?
                        ''', (combined, hash_vector, record_id))
                        self._db._conn.commit()
                        _log.debug(f"[交互记忆] 追加到摘要#{record_id}")
                        return record_id
            except Exception as e:
                _log.warning(f"[交互记忆] DB追加失败(降级内存): {e}")

        with self._lock:
            for rec in self._cache:
                if rec['id'] == record_id:
                    rec['summary'] = rec['summary'] + separator + new_summary
                    rec['hash_vector'] = EmotionalMemory._compute_hash_vector(
                        rec['summary']
                    )
                    _log.debug(f"[交互记忆] 追加到摘要#{record_id}(内存)")
                    return record_id
        return record_id

    def get_recent(self, user_id: str, days: int = 7) -> list[dict]:
        """获取最近N天的详细交互摘要"""
        results = []
        cutoff_date = (
            datetime.now() - timedelta(days=days)
        ).strftime('%Y-%m-%d')

        if self._db:
            try:
                with self._db._lock:
                    cursor = self._db._conn.execute('''
                        SELECT id, user_id, summary, day_date, is_daily,
                               created_ts, hash_vector
                        FROM rag_interaction_memory
                        WHERE user_id = ? AND day_date >= ?
                        ORDER BY day_date DESC, created_ts DESC
                    ''', (user_id, cutoff_date))
                    for row in cursor.fetchall():
                        results.append(self._row_to_dict(row))
            except Exception as e:
                _log.warning(f"[交互记忆] DB加载失败: {e}")

        with self._lock:
            for rec in self._cache:
                if rec['user_id'] == user_id and rec['day_date'] >= cutoff_date:
                    if not any(r['id'] == rec['id'] for r in results):
                        results.append(rec)

        results.sort(key=lambda x: x['day_date'], reverse=True)
        return results

    def search(self, user_id: str, query: str, limit: int = 5) -> list[dict]:
        """搜索交互记忆"""
        query_vector = EmotionalMemory._compute_hash_vector(query)
        results = self.get_recent(user_id, days=self.WEEKLY_DAYS)

        scored = []
        for rec in results:
            score = EmotionalMemory._cosine_similarity(
                rec.get('hash_vector', ''), query_vector
            )
            if query.lower() in rec.get('summary', '').lower():
                score += 0.3
            rec['_score'] = score
            if score > 0:
                scored.append(rec)

        scored.sort(key=lambda x: x['_score'], reverse=True)
        return scored[:limit]

    def weekly_summarize(self, user_id: str = None):
        """将超过7天的每日摘要汇总为周度摘要
        
        Args:
            user_id: 指定用户ID，为None时处理所有用户
        """
        cutoff_date = (
            datetime.now() - timedelta(days=self.DETAILED_DAYS)
        ).strftime('%Y-%m-%d')

        if self._db:
            try:
                with self._db._lock:
                    if user_id:
                        cursor = self._db._conn.execute('''
                            SELECT id, user_id, summary, day_date
                            FROM rag_interaction_memory
                            WHERE user_id = ? AND day_date < ? AND is_daily = 1
                            ORDER BY day_date ASC
                        ''', (user_id, cutoff_date))
                    else:
                        cursor = self._db._conn.execute('''
                            SELECT id, user_id, summary, day_date
                            FROM rag_interaction_memory
                            WHERE day_date < ? AND is_daily = 1
                            ORDER BY user_id, day_date ASC
                        ''', (cutoff_date,))
                    old_records = cursor.fetchall()

                    if not old_records:
                        return

                    user_groups: dict[str, list] = {}
                    for row in old_records:
                        uid = row[1]
                        user_groups.setdefault(uid, []).append(row)

                    total_old = 0
                    total_new = 0
                    for uid, records in user_groups.items():
                        weeks: dict[str, list[str]] = {}
                        for row in records:
                            summary = row[2]
                            date_str = row[3]
                            try:
                                dt = datetime.strptime(date_str, '%Y-%m-%d')
                                week_start = (dt - timedelta(
                                    days=dt.weekday()
                                )).strftime('%Y-%m-%d')
                            except ValueError:
                                week_start = date_str
                            weeks.setdefault(week_start, []).append(summary)

                        for week_start, summaries in weeks.items():
                            combined = '；'.join(summaries)
                            hash_vector = EmotionalMemory._compute_hash_vector(combined)
                            self._db._conn.execute('''
                                INSERT OR IGNORE INTO rag_interaction_memory
                                (user_id, summary, day_date, is_daily,
                                 created_ts, hash_vector)
                                VALUES (?, ?, ?, 0, ?, ?)
                            ''', (
                                uid, combined, week_start,
                                time.time(), hash_vector
                            ))

                        old_ids = [row[0] for row in records]
                        if old_ids:
                            placeholders = ','.join('?' * len(old_ids))
                            self._db._conn.execute(
                                f'DELETE FROM rag_interaction_memory '
                                f'WHERE id IN ({placeholders})',
                                old_ids
                            )
                        total_old += len(records)
                        total_new += len(weeks)

                    self._db._conn.commit()
                    _log.info(
                        f"[交互记忆] 周度汇总: "
                        f"{len(user_groups)}用户, "
                        f"{total_old}条 → {total_new}条周摘要"
                    )
            except Exception as e:
                _log.warning(f"[交互记忆] 周度汇总失败: {e}")

    def cleanup_expired(self):
        """清理超过30天的周度摘要"""
        cutoff_date = (
            datetime.now() - timedelta(days=self.WEEKLY_DAYS)
        ).strftime('%Y-%m-%d')

        if self._db:
            try:
                with self._db._lock:
                    self._db._conn.execute('''
                        DELETE FROM rag_interaction_memory
                        WHERE is_daily = 0 AND day_date < ?
                    ''', (cutoff_date,))
                    self._db._conn.commit()
            except Exception as e:
                _log.warning(f"[交互记忆] 清理失败: {e}")

        with self._lock:
            self._cache = [
                r for r in self._cache
                if not (r.get('is_daily', 0) == 0 and r['day_date'] < cutoff_date)
            ]

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            'id': row[0], 'user_id': row[1], 'summary': row[2],
            'day_date': row[3], 'is_daily': bool(row[4]),
            'created_ts': row[5], 'hash_vector': row[6],
        }


class MemoryRAG:
    """四层记忆 + RAG 统一入口
    
    对外提供统一的记忆读写和上下文检索接口。
    整合情感、身份、事实、交互四层记忆。
    
    Usage:
        rag = MemoryRAG(db_manager)
        rag.save_emotional_event('user_1', 'meet', 'joy', '今天认识了你')
        rag.save_identity('user_1', '姓名', '小明')
        context = rag.get_context('user_1', '你好')
    """

    def __init__(self, db_manager=None):
        self._db = db_manager
        self.emotional = EmotionalMemory(db_manager)
        self.identity = IdentityMemory(db_manager)
        self.fact = FactMemory(db_manager)
        self.interaction = InteractionMemory(db_manager)
        self._cleanup_running = False
        self._start_cleanup_thread()
        _log.info("[MemoryRAG] 四层记忆系统初始化完成")

    def _start_cleanup_thread(self):
        """启动后台清理线程"""
        if self._cleanup_running:
            return

        def cleanup_loop():
            self._cleanup_running = True
            _log.info("[MemoryRAG] 后台清理线程已启动")
            while True:
                try:
                    time.sleep(3600)  # 每小时执行一次
                    self._do_cleanup()
                except Exception as e:
                    _log.error(f"[MemoryRAG] 清理循环异常: {e}")
                except KeyboardInterrupt:
                    break

        thread = threading.Thread(target=cleanup_loop, daemon=True)
        thread.start()

    def _do_cleanup(self):
        """执行清理操作"""
        _log.debug("[MemoryRAG] 开始清理过期记忆...")
        try:
            self.emotional.cleanup_expired()
            self.fact.cleanup_expired()
            self.interaction.cleanup_expired()
            self.interaction.weekly_summarize()
            _log.debug("[MemoryRAG] 清理完成")
        except Exception as e:
            _log.error(f"[MemoryRAG] 清理失败: {e}")

    def save_emotional_event(self, user_id: str, event_type: str,
                              emotion: str, detail: str) -> int:
        """保存情感事件（第一层）"""
        return self.emotional.save(user_id, event_type, emotion, detail)

    def save_identity(self, user_id: str, key: str, value: str,
                       confidence: float = 1.0) -> bool:
        """保存身份信息（第二层）"""
        return self.identity.save(user_id, key, value, confidence)

    def save_fact(self, user_id: str, topic: str, content: str,
                  confidence: float = 1.0) -> int:
        """保存事实信息（第三层）"""
        return self.fact.save(user_id, topic, content, confidence)

    def update_interaction(self, user_id: str, summary: str) -> int:
        """更新交互摘要（第四层）"""
        return self.interaction.update(user_id, summary)

    def get_context(self, user_id: str, query: str) -> str:
        """获取相关记忆作为LLM上下文
        
        从四层记忆中检索与query相关的内容，
        组装成结构化上下文字符串，供LLM使用。
        """
        context_parts = []

        identity_info = self.identity.get_all(user_id)
        if identity_info:
            identity_lines = []
            for k, v in identity_info.items():
                identity_lines.append(f"  - {k}: {v}")
            context_parts.append(
                "【用户身份信息】\n" + "\n".join(identity_lines)
            )

        emotional_results = self.emotional.search(user_id, query, limit=3)
        if emotional_results:
            emo_lines = []
            for r in emotional_results:
                emo_lines.append(
                    f"  - [{r.get('event_type', '事件')}] "
                    f"{r.get('detail', '')[:80]} "
                    f"(情感: {r.get('emotion', 'neutral')})"
                )
            context_parts.append(
                "【相关情感事件】\n" + "\n".join(emo_lines)
            )

        fact_results = self.fact.search(user_id, query, limit=3)
        if fact_results:
            fact_lines = []
            for r in fact_results:
                conf = r.get('confidence', 1.0)
                fact_lines.append(
                    f"  - [{r.get('topic', '')}]: "
                    f"{r.get('content', '')[:80]} "
                    f"(置信度: {conf:.0%})"
                )
            context_parts.append(
                "【相关事实】\n" + "\n".join(fact_lines)
            )

        interaction_results = self.interaction.search(user_id, query, limit=3)
        if interaction_results:
            inter_lines = []
            for r in interaction_results:
                date_str = r.get('day_date', '')
                summary = r.get('summary', '')[:100]
                inter_lines.append(f"  - [{date_str}]: {summary}")
            context_parts.append(
                "【近期交互摘要】\n" + "\n".join(inter_lines)
            )

        if not context_parts:
            return ""

        header = (
            "以下是关于用户的记忆上下文，请在回复中自然地运用这些信息：\n"
        )
        return header + "\n\n".join(context_parts)

    def get_user_profile(self, user_id: str) -> str:
        """获取组装好的用户画像"""
        profile = self.identity.get_all(user_id)
        if not profile:
            return ""

        lines = ["【用户画像】"]
        key_labels = {
            '姓名': '用户姓名', '名字': '用户姓名',
            '年龄': '用户年龄', '生日': '用户生日',
            '所在地': '所在城市', '城市': '所在城市',
            '职业': '职业', '工作': '职业',
            '爱好': '爱好', '喜欢': '喜好',
            '性别': '性别',
        }

        for key, value in profile.items():
            label = key_labels.get(key, key)
            lines.append(f"  - {label}: {value}")

        return "\n".join(lines)

    def search_memories(self, user_id: str, query: str) -> list[dict]:
        """跨四层记忆全局关键词搜索"""
        all_results = []

        for layer_name, search_fn in [
            ('emotional', lambda: self.emotional.search(user_id, query, limit=5)),
            ('fact', lambda: self.fact.search(user_id, query, limit=5)),
            ('interaction', lambda: self.interaction.search(user_id, query, limit=5)),
        ]:
            try:
                results = search_fn()
                for r in results:
                    r['_layer'] = layer_name
                    all_results.append(r)
            except Exception as e:
                _log.warning(f"[MemoryRAG] {layer_name}层搜索失败: {e}")

        identity_info = self.identity.get_all(user_id)
        query_lower = query.lower()
        for k, v in identity_info.items():
            if query_lower in k.lower() or query_lower in v.lower():
                all_results.append({
                    'key': k, 'value': v,
                    '_layer': 'identity', '_score': 1.0,
                })

        all_results.sort(key=lambda x: x.get('_score', 0), reverse=True)
        return all_results[:15]

    def force_update_identity(self, user_id: str, key: str,
                               value: str) -> bool:
        """强制更新身份信息（用户明确要求修改）"""
        return self.identity.force_update(user_id, key, value)

    def get_stats(self) -> dict[str, Any]:
        """获取记忆系统统计"""
        return {
            'emotional_cache': len(self.emotional._cache),
            'identity_cache': len(self.identity._cache),
            'fact_cache': len(self.fact._cache),
            'interaction_cache': len(self.interaction._cache),
            'db_connected': self._db is not None,
        }

    def extract(self, text: str, user_id: str = "") -> dict[str, Any]:
        """从文本中提取关键信息并自动存储。
        
        提取规则：
        - "我叫XXX" → 保存姓名身份
        - "我是/做XXX的" → 保存职业事实
        - "我喜欢XXX" → 保存偏好事实
        - "我在XXX" → 保存地点事实
        - 提取到的信息自动存入对应层级
        """
        result = {"extracted": [], "saved": []}
        
        if not text or not text.strip():
            return result
        
        import re
        
        # 姓名提取
        name_patterns = [
            r'(?:我叫|我是|我名字是|我的名字是|大家叫我)([^\s，。！？,!\?]{1,10})',
            r'(?:my name is|i am called)\s+(\w+)',
        ]
        for pat in name_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                name = m.group(1).strip()
                result['extracted'].append({"type": "name", "value": name})
                if user_id:
                    try:
                        self.identity.save(user_id, "姓名", name, confidence=1.0)
                        result['saved'].append("identity:姓名")
                    except Exception:
                        pass
                break
        
        # 职业提取
        job_patterns = [
            r'(?:我是|我做|我从事|我职业是|我在做)([^\s，。！？,!\?]{1,15})(?:的|工作|这个|这行)?',
            r'(?:i am|i\'m)\s+a\s+(\w+)',
        ]
        for pat in job_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                job = m.group(1).strip()
                result['extracted'].append({"type": "job", "value": job})
                if user_id:
                    try:
                        self.fact.save(user_id, "职业", job, confidence=0.8)
                        result['saved'].append("fact:职业")
                    except Exception:
                        pass
                break
        
        # 偏好提取
        like_patterns = [
            r'(?:我喜欢|我爱|我偏爱|我很喜欢)([^\s，。！？,!\?]{1,15})',
            r'(?:i like|i love|i enjoy)\s+(\w+(?:\s+\w+){0,2})',
        ]
        for pat in like_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                like = m.group(1).strip()
                result['extracted'].append({"type": "preference", "value": like})
                if user_id:
                    try:
                        self.fact.save(user_id, "偏好", like, confidence=0.8)
                        result['saved'].append("fact:偏好")
                    except Exception:
                        pass
                break
        
        # 地点提取
        place_patterns = [
            r'(?:我在|我来自|我住在|我生活在)([^\s，。！？,!\?]{1,10})',
            r'(?:i am from|i live in)\s+(\w+(?:\s+\w+){0,2})',
        ]
        for pat in place_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                place = m.group(1).strip()
                result['extracted'].append({"type": "place", "value": place})
                if user_id:
                    try:
                        self.fact.save(user_id, "地点", place, confidence=0.7)
                        result['saved'].append("fact:地点")
                    except Exception:
                        pass
                break
        
        return result

    def shutdown(self):
        """关闭记忆系统"""
        self._cleanup_running = False
        _log.info("[MemoryRAG] 已关闭")