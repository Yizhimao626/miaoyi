#!/usr/bin/env python3
"""
  🐱 喵艺数据库管理器 - 双模式存储模块（SQLite / MySQL）
  替代JSON文件存储，实现数据持久化、压缩和查询优化

  核心特性：
  - SQLite / MySQL 双后端（config.yaml database.type 或环境变量 MIAOYI_DB_TYPE 切换）
  - SQLite: WAL 模式并发优化
  - MySQL: utf8mb4 详细 schema + 断线重连 + 方言自动翻译（db_backend.py）
  - 事务保护（_transaction 上下文管理器）
  - 数据库锁竞争自动重试
  - 高频查询索引优化
"""
import functools
import hashlib
import json
import logging
import os
import threading
import time
import zlib
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from db_backend import DatabaseBackend, load_db_config

_log = logging.getLogger("MiaoyiDB")


def _user_session_like_pattern(user_id: str) -> str:
    """构造 session_id 用户前缀的 LIKE 模式，转义 LIKE 通配符（_ %）。

    【安全加固】旧版 f'{user_id}_%' 中的 _ 会被当作单字符通配符，
    用户 1 的模式会连带命中用户 10/11/... 的会话，造成跨用户数据泄漏。
    【跨库兼容】转义符用 '!' 而非 '\'：MySQL 字符串字面量里 '\' 是转义符，
    会把 ESCAPE '\' 弄成未闭合字符串（SQLite 无此问题），'!' 两库均安全。
    """
    uid = str(user_id).replace('!', '!!').replace('%', '!%').replace('_', '!_')
    return uid + '!_%'


def _make_session_title(text: str, max_len: int = 20) -> str:
    """从首条用户消息生成会话标题：压成单行、截断加省略号"""
    t = ' '.join(str(text or '').split())
    if not t:
        return ''
    return t[:max_len] + '…' if len(t) > max_len else t


def retry_on_locked(max_retries=3, delay=0.1):
    """数据库写操作重试装饰器 - 处理 SQLite/MySQL 并发锁竞争。

    当遇到锁相关错误时，自动等待并重试，避免因瞬时锁竞争导致的写入失败。
    - SQLite: 'database is locked'
    - MySQL:  Lock wait timeout (1205) / Deadlock found (1213)

    Args:
        max_retries: 最大重试次数（默认3次）
        delay: 初始重试间隔秒数（默认0.1秒），采用线性递增策略

    Usage:
        @retry_on_locked(max_retries=5, delay=0.05)
        def save_message(self, ...):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # 检查是否为数据库锁错误（SQLite / MySQL）
                    error_msg = str(e).lower()
                    is_lock_error = (
                        'database is locked' in error_msg
                        or 'database table is locked' in error_msg
                        or 'lock wait timeout' in error_msg
                        or 'deadlock found' in error_msg
                    )
                    if is_lock_error:
                        last_exc = e
                        wait_time = delay * (attempt + 1)  # 线性递增
                        _log.warning(f"[DB] 数据库锁竞争，第{attempt + 1}/{max_retries}次重试，等待{wait_time:.2f}s")
                        time.sleep(wait_time)
                    else:
                        # 非锁错误直接抛出
                        raise
            # 所有重试失败，抛出最后一个异常
            _log.error(f"[DB] 数据库锁竞争重试失败({max_retries}次)")
            raise last_exc
        return wrapper
    return decorator


class DBManager:
    def __init__(self, db_path: str = "miaoyi.db", db_config: dict = None):
        self.db_path = db_path
        # 数据库后端配置：显式传入 > config.yaml database 段 > 环境变量 MIAOYI_DB_TYPE > 默认 sqlite
        self.db_config = db_config if db_config is not None else load_db_config()
        self._backend = DatabaseBackend(self.db_config)
        self._lock = threading.Lock()
        self._conn = None
        self._init_db()

    @property
    def db_type(self) -> str:
        return self._backend.db_type

    @contextmanager
    def _transaction(self):
        """事务上下文管理器 - 确保多步写操作的原子性。
        使用 BEGIN IMMEDIATE 在写操作前立即获取锁，避免死锁（MySQL 自动翻译为 START TRANSACTION）。
        失败时自动回滚，成功时提交。
        """
        with self._lock:
            try:
                # 【防御】清理此前失败操作残留的未提交隐式事务（如唯一键冲突后未回滚）
                if self._conn.in_transaction:
                    self._conn.rollback()
                self._conn.execute('BEGIN IMMEDIATE')
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _init_db(self):
        with self._lock:
            # 连接由 db_backend 创建：SQLite 应用 WAL 等优化，MySQL 配置 utf8mb4 + 自动重连
            self._conn = self._backend.connect(self.db_path)
            if self._backend.db_type == 'mysql':
                # 建库（首次部署时 database 可能不存在，需要 root 授权 CREATE）
                self._ensure_mysql_database()
                self._create_tables_mysql()
            else:
                self._create_tables()
            self._conn.commit()
            # 自动应用数据库迁移（仅 SQLite；MySQL DDL 已是最终态）
            self._run_auto_migrations()

    def _ensure_mysql_database(self):
        """确保 MySQL 数据库存在；不存在时给出明确指引（不代为创建）。

        【加固】数据库标识符无法参数绑定，动态拼接 CREATE DATABASE 存在
        配置污染注入面，故改为只检不建，由操作者按提示手动创建。"""
        try:
            dbname = str(self._backend.mysql_cfg.get('database', 'miaoyi'))
            cur = self._conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                (dbname,))
            if cur.fetchone()[0] == 0:
                _log.warning(
                    "[DB] MySQL 数据库 %s 不存在。请先在 MySQL 中手动执行："
                    "CREATE DATABASE `%s` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
                    " 然后重启服务（不再自动建库，避免动态 SQL 注入面）",
                    dbname, dbname)
        except Exception as e:
            _log.warning(f"[DB] 检查 MySQL 数据库失败（若库已存在可忽略）: {e}")

    def _run_auto_migrations(self):
        """自动应用 migrations/ 目录下未执行的迁移脚本。

        仅 SQLite 需要（MySQL 的 DDL 已是最终态，含全部演进列与索引）。
        SQLite :memory: 库每个连接相互独立，跳过自动迁移。
        迁移失败不阻断启动（原有设计：核心模块仍可工作）。
        """
        if self._backend.db_type != 'sqlite':
            return
        if self.db_path == ':memory:':
            return
        try:
            from migrations.migration_runner import MigrationRunner
            runner = MigrationRunner(self.db_path)
            try:
                runner.up()
            finally:
                runner.close()
        except Exception as e:
            _log.warning(f"[DB] 自动迁移执行失败（继续运行）: {e}")

    # ==================================================================
    # MySQL 详细 Schema（utf8mb4 / InnoDB）
    # 设计说明：
    # - 与 SQLite 结构逐表对齐，并包含全部演进列的最终态
    #   （users.role/nickname/phone、growth.streak/max_exp、
    #    memories.user_id/importance/strength、emotional_states.vector_json）
    # - 不定义 FOREIGN KEY 约束：与 SQLite 行为保持一致（应用层管理完整性），
    #   避免删除会话/用户时因外键级联策略差异导致失败
    # - 短字符串用 VARCHAR + DEFAULT ''；长文本用 TEXT DEFAULT ('')
    #   （MySQL 8.0.13+ 表达式默认值，语义等同 SQLite 的 TEXT DEFAULT ''）
    # - key/value 为 MySQL 保留字，统一反引号
    # - 每表每列带 COMMENT，便于运维查阅
    # ==================================================================
    MYSQL_SCHEMA_STATEMENTS = [
        # ---------- 用户与认证 ----------
        '''CREATE TABLE IF NOT EXISTS users (
            id            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '用户ID',
            username      VARCHAR(64)  NOT NULL COMMENT '用户名（唯一）',
            password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希(PBKDF2)',
            phone         VARCHAR(20)  DEFAULT '' COMMENT '手机号',
            email         VARCHAR(128) DEFAULT '' COMMENT '邮箱',
            nickname      VARCHAR(64)  DEFAULT '' COMMENT '昵称',
            avatar        VARCHAR(255) DEFAULT '' COMMENT '头像URL',
            role          VARCHAR(16)  DEFAULT 'user' COMMENT '角色: user/admin',
            created_ts    DOUBLE DEFAULT 0 COMMENT '注册时间戳',
            last_login_ts DOUBLE DEFAULT 0 COMMENT '最后登录时间戳',
            status        VARCHAR(16)  DEFAULT 'active' COMMENT '状态: active/disabled',
            UNIQUE KEY uk_users_username (username),
            KEY idx_users_username (username),
            KEY idx_users_email (email),
            KEY idx_users_phone (phone)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[用户认证] 用户表' ''',

        '''CREATE TABLE IF NOT EXISTS refresh_tokens (
            id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id    BIGINT NOT NULL COMMENT '所属用户ID',
            token      VARCHAR(512) NOT NULL COMMENT 'Refresh Token 内容',
            jti        VARCHAR(64)  NOT NULL COMMENT 'JWT ID（唯一）',
            created_at DOUBLE NOT NULL COMMENT '签发时间戳',
            expires_at DOUBLE NOT NULL COMMENT '过期时间戳',
            revoked    TINYINT DEFAULT 0 COMMENT '是否已撤销: 0否/1是',
            UNIQUE KEY uk_refresh_token (token),
            UNIQUE KEY uk_refresh_jti (jti),
            KEY idx_refresh_tokens_user (user_id),
            KEY idx_refresh_tokens_expires (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[用户认证] 刷新令牌表（支持重启后保持登录）' ''',

        # ---------- 会话与消息 ----------
        '''CREATE TABLE IF NOT EXISTS conversations (
            id             BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '会话ID',
            session_id     VARCHAR(128) NOT NULL COMMENT '会话唯一标识',
            user_name      VARCHAR(64)  DEFAULT '' COMMENT '所属用户ID（历史命名user_name，实际存user_id）',
            name           VARCHAR(64)  DEFAULT '' COMMENT '会话标题（首条消息自动命名）',
            start_ts       DOUBLE DEFAULT 0 COMMENT '开始时间戳',
            last_active_ts DOUBLE DEFAULT 0 COMMENT '最后活跃时间戳',
            message_count  INT DEFAULT 0 COMMENT '消息数',
            emotion_score  DOUBLE DEFAULT 0 COMMENT '情感得分',
            intimacy_level DOUBLE DEFAULT 0 COMMENT '亲密度',
            status         VARCHAR(16) DEFAULT 'active' COMMENT '状态',
            UNIQUE KEY uk_conversations_session (session_id),
            KEY idx_conversations_last_active (last_active_ts),
            KEY idx_conversations_user_name (user_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[聊天会话] 会话表' ''',

        '''CREATE TABLE IF NOT EXISTS messages (
            id               BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '消息ID',
            conversation_id  BIGINT COMMENT '所属会话ID',
            session_id       VARCHAR(128) COMMENT '会话标识（含用户前缀）',
            role             VARCHAR(16) NOT NULL COMMENT '角色: user/assistant/system',
            text             TEXT COMMENT '消息内容',
            sticker_path     VARCHAR(255) DEFAULT '' COMMENT '表情包路径',
            sticker_meaning  TEXT DEFAULT ('') COMMENT '表情含义',
            emotion          VARCHAR(32) DEFAULT '' COMMENT '情感标签',
            confidence       DOUBLE DEFAULT 0 COMMENT '置信度',
            response_time_ms INT DEFAULT 0 COMMENT '响应耗时(毫秒)',
            created_ts       DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            image_url        VARCHAR(500) DEFAULT '' COMMENT '附图URL（视觉消息）',
            KEY idx_messages_conv_time (conversation_id, created_ts),
            KEY idx_messages_created_ts (created_ts),
            KEY idx_messages_role (role)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[聊天会话] 聊天消息表' ''',

        # ---------- 记忆系统 ----------
        '''CREATE TABLE IF NOT EXISTS memories (
            id             BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '记忆ID',
            user_id        VARCHAR(64)  DEFAULT '' COMMENT '所属用户ID（空=全局/系统记忆）',
            `key`          VARCHAR(255) NOT NULL COMMENT '记忆键',
            value          TEXT DEFAULT ('') COMMENT '记忆值',
            category       VARCHAR(64)  DEFAULT '' COMMENT '分类: profile/preference/habit/fact等',
            confidence     DOUBLE DEFAULT 0.8 COMMENT '置信度0-1',
            source         VARCHAR(64)  DEFAULT '' COMMENT '来源',
            usage_count    INT DEFAULT 0 COMMENT '访问次数',
            last_access_ts DOUBLE DEFAULT 0 COMMENT '最后访问时间戳',
            created_ts     DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            expires_at     DOUBLE DEFAULT 0 COMMENT '过期时间戳(0=不过期)',
            importance     DOUBLE DEFAULT 0.5 COMMENT '重要性0-1',
            strength       DOUBLE DEFAULT 0.5 COMMENT '记忆强度0-1',
            pinned         TINYINT DEFAULT 0 COMMENT '用户钉住0-1(L1记忆卡片流)',
            emb_json       TEXT COMMENT '语义向量JSON（embedding-3，语义检索用）',
            UNIQUE KEY uk_memories_user_key (user_id, `key`),
            KEY idx_memories_key (`key`),
            KEY idx_memories_category (category),
            KEY idx_memories_confidence (confidence)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[记忆系统] 语义记忆表（按用户隔离）' ''',

        '''CREATE TABLE IF NOT EXISTS memory_tags (
            id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            memory_id  BIGINT NOT NULL COMMENT '记忆ID',
            tag        VARCHAR(64) NOT NULL COMMENT '标签',
            created_ts DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            UNIQUE KEY uk_memory_tag (memory_id, tag)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[记忆系统] 记忆标签表' ''',

        # ---------- 情感系统 ----------
        '''CREATE TABLE IF NOT EXISTS emotions (
            id           BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id      VARCHAR(64) DEFAULT '' COMMENT '所属用户ID',
            emotion_type VARCHAR(32) NOT NULL COMMENT '情感类型',
            value        DOUBLE DEFAULT 0 COMMENT '情感强度',
            timestamp    DOUBLE DEFAULT 0 COMMENT '记录时间戳',
            KEY idx_emotions_user (user_id),
            KEY idx_emotions_timestamp (timestamp),
            KEY idx_emotions_type (emotion_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[情感系统] 情感记录表' ''',

        '''CREATE TABLE IF NOT EXISTS emotional_states (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id     VARCHAR(64) DEFAULT '' COMMENT '所属用户ID',
            pleasure    DOUBLE DEFAULT 0 COMMENT '愉悦度(PAD模型)',
            arousal     DOUBLE DEFAULT 0 COMMENT '唤醒度',
            dominance   DOUBLE DEFAULT 0 COMMENT '支配度',
            joy         DOUBLE DEFAULT 0 COMMENT '快乐',
            sadness     DOUBLE DEFAULT 0 COMMENT '悲伤',
            anger       DOUBLE DEFAULT 0 COMMENT '愤怒',
            surprise    DOUBLE DEFAULT 0 COMMENT '惊讶',
            fear        DOUBLE DEFAULT 0 COMMENT '恐惧',
            love        DOUBLE DEFAULT 0 COMMENT '爱',
            empathy     DOUBLE DEFAULT 0 COMMENT '共情',
            pride       DOUBLE DEFAULT 0 COMMENT '自豪',
            shame       DOUBLE DEFAULT 0 COMMENT '羞愧',
            event_desc  VARCHAR(512) DEFAULT '' COMMENT '触发事件描述',
            timestamp   DOUBLE DEFAULT 0 COMMENT '记录时间戳',
            vector_json TEXT DEFAULT ('') COMMENT '情感向量JSON',
            KEY idx_emotional_states_timestamp (timestamp),
            KEY idx_emotional_states_user_time (user_id, timestamp)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[情感系统] 情感状态快照表' ''',

        '''CREATE TABLE IF NOT EXISTS emotional_memories (
            id             BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id        VARCHAR(64) DEFAULT '' COMMENT '所属用户ID',
            content        TEXT NOT NULL COMMENT '情感记忆内容',
            emotion_type   VARCHAR(32) DEFAULT '' COMMENT '情感类型',
            intensity      DOUBLE DEFAULT 0 COMMENT '强度0-1',
            vector_blob    BLOB COMMENT '情感向量二进制',
            keywords       TEXT DEFAULT ('') COMMENT '关键词',
            created_ts     DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            last_access_ts DOUBLE DEFAULT 0 COMMENT '最后访问时间戳',
            access_count   INT DEFAULT 0 COMMENT '访问次数',
            KEY idx_emotional_memories_user (user_id),
            KEY idx_emotional_memories_emotion (emotion_type),
            KEY idx_emotional_memories_created (created_ts)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='情感记忆表' ''',

        # ---------- 知识系统 ----------
        '''CREATE TABLE IF NOT EXISTS knowledge (
            id             BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            topic          VARCHAR(255) NOT NULL COMMENT '知识主题',
            content        TEXT NOT NULL COMMENT '知识内容',
            source         VARCHAR(128) DEFAULT '' COMMENT '来源',
            quality        DOUBLE DEFAULT 0.5 COMMENT '质量分0-1',
            usage_count    INT DEFAULT 0 COMMENT '使用次数',
            last_access_ts DOUBLE DEFAULT 0 COMMENT '最后访问时间戳',
            created_ts     DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            KEY idx_knowledge_topic (topic)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[知识学习] 知识库表' ''',

        '''CREATE TABLE IF NOT EXISTS knowledge_triples (
            id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            subject    VARCHAR(255) NOT NULL COMMENT '主体',
            relation   VARCHAR(64)  NOT NULL COMMENT '关系',
            object     VARCHAR(255) NOT NULL COMMENT '客体',
            confidence DOUBLE DEFAULT 0.8 COMMENT '置信度',
            source     VARCHAR(128) DEFAULT '' COMMENT '来源',
            verified   TINYINT DEFAULT 0 COMMENT '是否已验证',
            created_ts DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            usage_count INT DEFAULT 0 COMMENT '使用次数',
            KEY idx_triples_subject (subject),
            KEY idx_triples_relation (relation),
            KEY idx_triples_object (object)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[知识学习] 知识三元组表' ''',

        '''CREATE TABLE IF NOT EXISTS search_cache (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            query_hash  VARCHAR(64) NOT NULL COMMENT '查询哈希（唯一）',
            query       VARCHAR(512) NOT NULL COMMENT '原始查询',
            results     MEDIUMTEXT NOT NULL COMMENT '搜索结果JSON',
            confidence  DOUBLE DEFAULT 0 COMMENT '置信度',
            created_ts  DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            expires_at  DOUBLE DEFAULT 0 COMMENT '过期时间戳',
            UNIQUE KEY uk_search_cache_qhash (query_hash)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[知识学习] 搜索缓存表' ''',

        '''CREATE TABLE IF NOT EXISTS learning (
            id             BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            learning_type  VARCHAR(64) NOT NULL COMMENT '学习类型',
            content        TEXT NOT NULL COMMENT '学习内容',
            source         VARCHAR(128) DEFAULT '' COMMENT '来源',
            quality        DOUBLE DEFAULT 0.5 COMMENT '质量分',
            usage_count    INT DEFAULT 0 COMMENT '使用次数',
            last_access_ts DOUBLE DEFAULT 0 COMMENT '最后访问时间戳',
            created_ts     DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            metadata       TEXT DEFAULT ('') COMMENT '扩展元数据JSON',
            KEY idx_learning_type (learning_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[知识学习] 自我学习表' ''',

        # ---------- 成长与个性化 ----------
        '''CREATE TABLE IF NOT EXISTS growth (
            id                 BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id            VARCHAR(64) DEFAULT '' COMMENT '所属用户ID',
            level              INT DEFAULT 1 COMMENT '等级',
            exp                INT DEFAULT 0 COMMENT '经验值',
            max_exp            INT DEFAULT 100 COMMENT '当前等级所需最大经验',
            skill_points       INT DEFAULT 0 COMMENT '技能点',
            last_checkin_ts    DOUBLE DEFAULT 0 COMMENT '最后签到时间戳',
            total_interactions INT DEFAULT 0 COMMENT '累计互动次数',
            streak             INT DEFAULT 0 COMMENT '连续签到天数',
            created_ts         DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            UNIQUE KEY uk_growth_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[成长个性] 用户成长表（签到/等级）' ''',

        '''CREATE TABLE IF NOT EXISTS autobiography (
            id                   BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id              VARCHAR(64) DEFAULT '' COMMENT '所属用户ID',
            diary_date           VARCHAR(10) NOT NULL COMMENT '日记日期 YYYY-MM-DD',
            diary_text           TEXT NOT NULL COMMENT '日记内容',
            emotion_summary      TEXT DEFAULT ('') COMMENT '当日情感总结',
            knowledge_added      TEXT DEFAULT ('') COMMENT '当日新增知识',
            user_profile_changes TEXT DEFAULT ('') COMMENT '用户画像变化',
            created_ts           DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            UNIQUE KEY uk_autobiography_user_date (user_id, diary_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[成长个性] 喵艺日记（自传体）表' ''',

        '''CREATE TABLE IF NOT EXISTS cat_moments (
            id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id    VARCHAR(64) DEFAULT '' COMMENT '所属用户ID',
            content    TEXT NOT NULL COMMENT '动态内容',
            mood_emoji VARCHAR(16) DEFAULT '🐾' COMMENT '心情emoji',
            activity   VARCHAR(64) DEFAULT '' COMMENT '来自作息表的活动名',
            source     VARCHAR(16) DEFAULT 'template' COMMENT '来源: template/llm',
            liked      TINYINT DEFAULT 0 COMMENT '用户点赞0-1',
            liked_ts   DOUBLE DEFAULT 0 COMMENT '点赞时间戳',
            created_ts DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            KEY idx_cat_moments_user (user_id, created_ts)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[成长个性] 喵艺朋友圈动态表（L7 生活模拟）' ''',

        '''CREATE TABLE IF NOT EXISTS call_history (
            id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id    VARCHAR(64) NOT NULL COMMENT '所属用户ID',
            direction  VARCHAR(16) NOT NULL COMMENT '方向: outgoing/incoming',
            status     VARCHAR(16) NOT NULL COMMENT '状态: completed/missed/cancelled',
            duration   INT DEFAULT 0 COMMENT '通话时长(秒)',
            reason     VARCHAR(255) DEFAULT '' COMMENT '挂断原因',
            created_ts DOUBLE NOT NULL COMMENT '创建时间戳',
            KEY idx_call_history_user (user_id, created_ts)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[聊天会话] 通话历史表' ''',

        '''CREATE TABLE IF NOT EXISTS feedback (
            id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id    VARCHAR(64) NOT NULL COMMENT '反馈用户ID',
            content    TEXT NOT NULL COMMENT '反馈内容',
            created_ts DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            KEY idx_feedback_user (user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[系统运营] 用户反馈表' ''',

        # ---------- 表情包 ----------
        '''CREATE TABLE IF NOT EXISTS stickers (
            id           BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            filename     VARCHAR(191) NOT NULL COMMENT '文件名（唯一）',
            filepath     VARCHAR(255) NOT NULL COMMENT '文件路径',
            keyword      VARCHAR(64)  DEFAULT '' COMMENT '触发关键词',
            category     VARCHAR(32)  DEFAULT '' COMMENT '分类',
            emotion      VARCHAR(32)  DEFAULT '' COMMENT '情感标签',
            meaning      TEXT DEFAULT ('') COMMENT '含义说明',
            source       VARCHAR(32)  DEFAULT '' COMMENT '来源: builtin/custom/network',
            size         INT DEFAULT 0 COMMENT '文件大小(字节)',
            width        INT DEFAULT 0 COMMENT '宽度(px)',
            height       INT DEFAULT 0 COMMENT '高度(px)',
            is_gif       TINYINT DEFAULT 0 COMMENT '是否GIF',
            is_custom    TINYINT DEFAULT 0 COMMENT '是否用户自定义',
            usage_count  INT DEFAULT 0 COMMENT '使用次数',
            last_used_ts DOUBLE DEFAULT 0 COMMENT '最后使用时间戳',
            created_ts   DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            hash         VARCHAR(64) COMMENT '内容哈希（去重）',
            UNIQUE KEY uk_stickers_filename (filename),
            UNIQUE KEY uk_stickers_hash (hash),
            KEY idx_stickers_keyword (keyword),
            KEY idx_stickers_emotion (emotion)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[成长个性] 表情包表' ''',

        # ---------- 迁移版本跟踪 ----------
        '''CREATE TABLE IF NOT EXISTS schema_version (
            version     INT PRIMARY KEY COMMENT '迁移版本号',
            filename    VARCHAR(255) NOT NULL COMMENT '迁移脚本文件名',
            applied_at  DOUBLE DEFAULT 0 COMMENT '应用时间戳',
            description VARCHAR(512) DEFAULT '' COMMENT '迁移描述'
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[系统运营] 数据库迁移版本表' ''',

        # ---------- RAG 四层记忆（core/memory_rag.py 使用） ----------
        '''CREATE TABLE IF NOT EXISTS rag_emotional_memory (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id     VARCHAR(64) DEFAULT '' COMMENT '所属用户ID',
            event_type  VARCHAR(64) NOT NULL COMMENT '事件类型(相遇/争吵/告白等)',
            emotion     VARCHAR(32) DEFAULT '' COMMENT '情感标签',
            detail      TEXT NOT NULL COMMENT '事件详情',
            keywords    VARCHAR(255) DEFAULT '' COMMENT '关键词',
            created_ts  DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            expires_at  DOUBLE DEFAULT 0 COMMENT '过期时间戳(30天)',
            hash_vector VARCHAR(512) DEFAULT '' COMMENT '内容哈希向量',
            KEY idx_rag_emotional_user (user_id),
            KEY idx_rag_emotional_event (event_type),
            KEY idx_rag_emotional_expires (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[记忆系统] RAG情感记忆表' ''',

        '''CREATE TABLE IF NOT EXISTS rag_identity_memory (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id     VARCHAR(64) NOT NULL COMMENT '所属用户ID',
            `key`       VARCHAR(64) NOT NULL COMMENT '身份键(姓名/生日/职业等)',
            value       VARCHAR(255) NOT NULL COMMENT '身份值',
            confidence  DOUBLE DEFAULT 1.0 COMMENT '置信度',
            created_ts  DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            updated_ts  DOUBLE DEFAULT 0 COMMENT '更新时间戳',
            UNIQUE KEY uk_rag_identity (user_id, `key`),
            KEY idx_rag_identity_user (user_id),
            KEY idx_rag_identity_key (`key`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[记忆系统] RAG身份记忆表(不可变)' ''',

        '''CREATE TABLE IF NOT EXISTS rag_fact_memory (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id     VARCHAR(64) DEFAULT '' COMMENT '所属用户ID',
            topic       VARCHAR(128) NOT NULL COMMENT '主题',
            content     TEXT NOT NULL COMMENT '事实内容',
            confidence  DOUBLE DEFAULT 1.0 COMMENT '置信度',
            source      VARCHAR(64) DEFAULT '' COMMENT '来源',
            created_ts  DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            expires_at  DOUBLE DEFAULT 0 COMMENT '过期时间戳(90天)',
            hash_vector VARCHAR(512) DEFAULT '' COMMENT '内容哈希向量',
            KEY idx_rag_fact_user (user_id),
            KEY idx_rag_fact_topic (topic),
            KEY idx_rag_fact_expires (expires_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[记忆系统] RAG事实记忆表' ''',

        '''CREATE TABLE IF NOT EXISTS rag_interaction_memory (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '自增ID',
            user_id     VARCHAR(64) NOT NULL COMMENT '所属用户ID',
            summary     TEXT NOT NULL COMMENT '交互摘要',
            day_date    VARCHAR(16) NOT NULL COMMENT '日期(YYYY-MM-DD)',
            is_daily    TINYINT DEFAULT 1 COMMENT '是否日度摘要: 1日度/0周度',
            created_ts  DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            hash_vector VARCHAR(512) DEFAULT '' COMMENT '内容哈希向量',
            KEY idx_rag_interaction_user (user_id),
            KEY idx_rag_interaction_user_date (user_id, day_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[记忆系统] RAG交互记忆表(每日摘要)' ''',

        # ---------- 主动服务 ----------
        '''CREATE TABLE IF NOT EXISTS reminders (
            id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '提醒ID',
            user_id     VARCHAR(64) NOT NULL DEFAULT '' COMMENT '所属用户ID',
            remind_ts   DOUBLE NOT NULL COMMENT '触发时间戳',
            content     VARCHAR(512) NOT NULL COMMENT '提醒内容',
            status      VARCHAR(16) DEFAULT 'pending' COMMENT '状态: pending/fired/cancelled',
            created_ts  DOUBLE DEFAULT 0 COMMENT '创建时间戳',
            fired_ts    DOUBLE DEFAULT 0 COMMENT '实际触发时间戳',
            KEY idx_reminders_due (status, remind_ts),
            KEY idx_reminders_user (user_id, status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='[主动服务] 定时提醒表' ''',
    ]

    def _create_tables_mysql(self):
        """创建 MySQL 全部表结构（详细版，见 MYSQL_SCHEMA_STATEMENTS 注释）"""
        cursor = self._conn.cursor()
        for ddl in self.MYSQL_SCHEMA_STATEMENTS:
            cursor.execute(ddl)
        self._conn.commit()
        self._upgrade_mysql_schema(cursor)
        _log.info(f"[DB] MySQL 表结构创建完成（{len(self.MYSQL_SCHEMA_STATEMENTS)} 张表，utf8mb4/InnoDB）")

    def _upgrade_mysql_schema(self, cursor):
        """MySQL 已有库的幂等列升级（CREATE TABLE IF NOT EXISTS 不会为旧表补列）

        【L1 记忆卡片流】memories 表新增 pinned 列（0=未钉住 1=已钉住）。
        【L6 猫咪日记】autobiography 表旧库去重后补建 (user_id, diary_date) 唯一索引。
        """
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'memories' AND COLUMN_NAME = 'pinned'")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE memories ADD COLUMN pinned TINYINT DEFAULT 0 "
                "COMMENT '用户钉住0-1(L1记忆卡片流)'")
            self._conn.commit()
            _log.info("[DB] 已为 memories 表添加 pinned 列")

        # 【视觉能力】messages 表迁移：添加 image_url 附图字段
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'messages' AND COLUMN_NAME = 'image_url'")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE messages ADD COLUMN image_url VARCHAR(500) DEFAULT '' "
                "COMMENT '附图URL（视觉消息）'")
            self._conn.commit()
            _log.info("[DB] 已为 messages 表添加 image_url 列")

        # 【语义检索】memories 表迁移：添加 emb_json 语义向量字段
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'memories' AND COLUMN_NAME = 'emb_json'")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE memories ADD COLUMN emb_json TEXT "
                "COMMENT '语义向量JSON（embedding-3，语义检索用）'")
            self._conn.commit()
            _log.info("[DB] 已为 memories 表添加 emb_json 列")

        # 【L6 猫咪日记】旧库可能存在同用户同日重复日记行，先去重（保留最新）再补唯一索引
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'autobiography' "
            "AND INDEX_NAME = 'uk_autobiography_user_date'")
        if cursor.fetchone()[0] == 0:
            cursor.execute('''
                DELETE FROM autobiography WHERE id NOT IN (
                    SELECT t.max_id FROM (
                        SELECT MAX(id) AS max_id FROM autobiography GROUP BY user_id, diary_date
                    ) AS t
                )
            ''')
            cursor.execute('CREATE UNIQUE INDEX uk_autobiography_user_date '
                           'ON autobiography(user_id, diary_date)')
            self._conn.commit()
            _log.info("[DB] autobiography 表已去重并补建唯一索引 uk_autobiography_user_date")

        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'conversations' AND COLUMN_NAME = 'name'")
        if cursor.fetchone()[0] == 0:
            cursor.execute(
                "ALTER TABLE conversations ADD COLUMN name VARCHAR(64) DEFAULT '' "
                "COMMENT '会话标题（首条消息自动命名）'")
            self._conn.commit()
            _log.info("[DB] 已为 conversations 表添加 name 列")

    def _create_tables(self):
        cursor = self._conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS stickers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                filepath TEXT NOT NULL,
                keyword TEXT DEFAULT '',
                category TEXT DEFAULT '',
                emotion TEXT DEFAULT '',
                meaning TEXT DEFAULT '',
                source TEXT DEFAULT '',
                size INTEGER DEFAULT 0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                is_gif INTEGER DEFAULT 0,
                is_custom INTEGER DEFAULT 0,
                usage_count INTEGER DEFAULT 0,
                last_used_ts REAL DEFAULT 0,
                created_ts REAL DEFAULT 0,
                hash TEXT UNIQUE
            )
        ''')

        # 【结构迁移】legacy reminders 表（text/trigger_time/fired）升级为新结构
        # （user_id/remind_ts/content/status），避免 CREATE INDEX 因缺 status 列而启动失败
        try:
            cursor.execute("PRAGMA table_info(reminders)")
            rem_cols = [r[1] for r in cursor.fetchall()]
            if rem_cols and 'status' not in rem_cols:
                _log.info("[DB] 检测到 legacy reminders 表结构，迁移升级...")
                cursor.execute('DROP INDEX IF EXISTS idx_reminders_due')
                cursor.execute('DROP INDEX IF EXISTS idx_reminders_user')
                cursor.execute('ALTER TABLE reminders RENAME TO reminders_legacy')
                cursor.execute('''
                    CREATE TABLE reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT DEFAULT '',
                        remind_ts REAL NOT NULL,
                        content TEXT DEFAULT '',
                        status TEXT DEFAULT 'pending',
                        created_ts REAL DEFAULT 0,
                        fired_ts REAL DEFAULT 0
                    )
                ''')
                # 旧数据迁移：text->content, trigger_time->remind_ts, fired->status
                cursor.execute('''
                    INSERT INTO reminders (user_id, remind_ts, content, status, created_ts, fired_ts)
                    SELECT '', trigger_time, text,
                           CASE WHEN fired = 1 THEN 'fired' ELSE 'pending' END,
                           0, 0
                    FROM reminders_legacy
                ''')
                migrated = cursor.execute('SELECT COUNT(*) FROM reminders').fetchone()[0]
                cursor.execute('DROP TABLE reminders_legacy')
                _log.info(f"[DB] legacy reminders 迁移完成（{migrated} 条）")
        except Exception as e:
            _log.warning(f"[DB] reminders 结构迁移检查失败（继续）: {e}")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT '',
                remind_ts REAL NOT NULL,
                content TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_ts REAL DEFAULT 0,
                fired_ts REAL DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_reminders_due
            ON reminders(status, remind_ts)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_reminders_user
            ON reminders(user_id, status)
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE NOT NULL,
                user_name TEXT DEFAULT '',
                name TEXT DEFAULT '',
                start_ts REAL DEFAULT 0,
                last_active_ts REAL DEFAULT 0,
                message_count INTEGER DEFAULT 0,
                emotion_score REAL DEFAULT 0,
                intimacy_level REAL DEFAULT 0,
                status TEXT DEFAULT 'active'
            )
        ''')

        # conversations 表迁移：添加 name 会话标题字段
        cursor.execute("PRAGMA table_info(conversations)")
        conv_columns = [col[1] for col in cursor.fetchall()]
        if 'name' not in conv_columns:
            cursor.execute('ALTER TABLE conversations ADD COLUMN name TEXT DEFAULT ""')
            self._conn.commit()
            _log.info("已为conversations表添加name字段")

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER,
                session_id TEXT,
                role TEXT NOT NULL,
                text TEXT,
                sticker_path TEXT DEFAULT '',
                sticker_meaning TEXT DEFAULT '',
                emotion TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                response_time_ms INTEGER DEFAULT 0,
                created_ts REAL DEFAULT 0,
                image_url TEXT DEFAULT '',
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        ''')

        # messages 表迁移：添加 image_url 附图字段（视觉消息）
        cursor.execute("PRAGMA table_info(messages)")
        msg_columns = [col[1] for col in cursor.fetchall()]
        if 'image_url' not in msg_columns:
            cursor.execute('ALTER TABLE messages ADD COLUMN image_url TEXT DEFAULT ""')
            self._conn.commit()
            _log.info("已为messages表添加image_url字段")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                category TEXT DEFAULT '',
                confidence REAL DEFAULT 0.8,
                source TEXT DEFAULT '',
                usage_count INTEGER DEFAULT 0,
                last_access_ts REAL DEFAULT 0,
                created_ts REAL DEFAULT 0,
                expires_at REAL DEFAULT 0,
                emb_json TEXT DEFAULT ''
            )
        ''')

        # memories 表迁移：添加 emb_json 语义向量字段（语义检索）
        cursor.execute("PRAGMA table_info(memories)")
        mem_columns = [col[1] for col in cursor.fetchall()]
        if 'emb_json' not in mem_columns:
            cursor.execute('ALTER TABLE memories ADD COLUMN emb_json TEXT DEFAULT ""')
            self._conn.commit()
            _log.info("已为memories表添加emb_json字段")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS memory_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                created_ts REAL DEFAULT 0,
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE,
                UNIQUE(memory_id, tag)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emotions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT '',
                emotion_type TEXT NOT NULL,
                value REAL DEFAULT 0,
                timestamp REAL DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT DEFAULT '',
                quality REAL DEFAULT 0.5,
                usage_count INTEGER DEFAULT 0,
                last_access_ts REAL DEFAULT 0,
                created_ts REAL DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS growth (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT '' UNIQUE,
                level INTEGER DEFAULT 1,
                exp INTEGER DEFAULT 0,
                max_exp INTEGER DEFAULT 100,
                skill_points INTEGER DEFAULT 0,
                last_checkin_ts REAL DEFAULT 0,
                total_interactions INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                created_ts REAL DEFAULT 0
            )
        ''')

        # 旧库修复：去重后补建唯一索引（INSERT OR REPLACE 依赖它防止重复行）
        cursor.execute('''
            DELETE FROM growth WHERE id NOT IN (
                SELECT MAX(id) FROM growth GROUP BY user_id
            )
        ''')
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS uk_growth_user ON growth(user_id)')

        cursor.execute("PRAGMA table_info(growth)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'streak' not in columns:
            cursor.execute('ALTER TABLE growth ADD COLUMN streak INTEGER DEFAULT 0')
            self._conn.commit()
            _log.info("已为growth表添加streak字段")
        if 'max_exp' not in columns:
            cursor.execute('ALTER TABLE growth ADD COLUMN max_exp INTEGER DEFAULT 100')
            self._conn.commit()
            _log.info("已为growth表添加max_exp字段")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS search_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT UNIQUE NOT NULL,
                query TEXT NOT NULL,
                results TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                created_ts REAL DEFAULT 0,
                expires_at REAL DEFAULT 0
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_stickers_keyword ON stickers(keyword)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_stickers_emotion ON stickers(emotion)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_conv_time ON messages(conversation_id, created_ts)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memories_key ON memories(key)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_topic ON knowledge(topic)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learning (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                learning_type TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT DEFAULT '',
                quality REAL DEFAULT 0.5,
                usage_count INTEGER DEFAULT 0,
                last_access_ts REAL DEFAULT 0,
                created_ts REAL DEFAULT 0,
                metadata TEXT DEFAULT ''
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_learning_type ON learning(learning_type)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emotional_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT '',
                pleasure REAL DEFAULT 0,
                arousal REAL DEFAULT 0,
                dominance REAL DEFAULT 0,
                joy REAL DEFAULT 0,
                sadness REAL DEFAULT 0,
                anger REAL DEFAULT 0,
                surprise REAL DEFAULT 0,
                fear REAL DEFAULT 0,
                love REAL DEFAULT 0,
                empathy REAL DEFAULT 0,
                pride REAL DEFAULT 0,
                shame REAL DEFAULT 0,
                event_desc TEXT DEFAULT '',
                timestamp REAL DEFAULT 0,
                vector_json TEXT DEFAULT ''
            )
        ''')
        
        cursor.execute("PRAGMA table_info(emotional_states)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'vector_json' not in columns:
            cursor.execute('ALTER TABLE emotional_states ADD COLUMN vector_json TEXT DEFAULT ""')
            self._conn.commit()
            _log.info("已为emotional_states表添加vector_json字段")
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_emotional_states_timestamp ON emotional_states(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_emotional_states_user_time ON emotional_states(user_id, timestamp)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_triples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject TEXT NOT NULL,
                relation TEXT NOT NULL,
                object TEXT NOT NULL,
                confidence REAL DEFAULT 0.8,
                source TEXT DEFAULT '',
                verified INTEGER DEFAULT 0,
                created_ts REAL DEFAULT 0,
                usage_count INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_triples_subject ON knowledge_triples(subject)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_triples_relation ON knowledge_triples(relation)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_triples_object ON knowledge_triples(object)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS emotional_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT '',
                content TEXT NOT NULL,
                emotion_type TEXT DEFAULT '',
                intensity REAL DEFAULT 0,
                vector_blob BLOB,
                keywords TEXT DEFAULT '',
                created_ts REAL DEFAULT 0,
                last_access_ts REAL DEFAULT 0,
                access_count INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memories_user ON emotional_memories(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memories_emotion ON emotional_memories(emotion_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memories_created ON emotional_memories(created_ts)')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS autobiography (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT '',
                diary_date TEXT NOT NULL,
                diary_text TEXT NOT NULL,
                emotion_summary TEXT DEFAULT '',
                knowledge_added TEXT DEFAULT '',
                user_profile_changes TEXT DEFAULT '',
                created_ts REAL DEFAULT 0
            )
        ''')

        # 【L6 猫咪日记】旧库修复：历史版本不写 user_id 且无唯一约束，可能存在同日重复行；
        # 去重（保留最新）后补建唯一索引，INSERT OR REPLACE 才能幂等
        cursor.execute('''
            DELETE FROM autobiography WHERE id NOT IN (
                SELECT MAX(id) FROM autobiography GROUP BY user_id, diary_date
            )
        ''')
        cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS uk_autobiography_user_date '
                       'ON autobiography(user_id, diary_date)')

        # 【L7 生活模拟】朋友圈动态表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cat_moments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT DEFAULT '',
                content TEXT NOT NULL,
                mood_emoji TEXT DEFAULT '🐾',
                activity TEXT DEFAULT '',
                source TEXT DEFAULT 'template',
                liked INTEGER DEFAULT 0,
                liked_ts REAL DEFAULT 0,
                created_ts REAL DEFAULT 0
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_cat_moments_user '
                       'ON cat_moments(user_id, created_ts)')
        
        cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    phone TEXT DEFAULT '',
                    email TEXT DEFAULT '',
                    nickname TEXT DEFAULT '',
                    avatar TEXT DEFAULT '',
                    role TEXT DEFAULT 'user',
                    created_ts REAL DEFAULT 0,
                    last_login_ts REAL DEFAULT 0,
                    status TEXT DEFAULT 'active'
                )
            ''')
        
        cursor.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in cursor.fetchall()]
        if 'role' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN role TEXT DEFAULT "user"')
            self._conn.commit()
            _log.info("已为users表添加role字段")
        if 'nickname' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN nickname TEXT DEFAULT ""')
            self._conn.commit()
        if 'phone' not in columns:
            cursor.execute('ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ""')
            self._conn.commit()
        
        # Refresh Token 持久化表 - 支持服务重启后保持登录状态
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                jti TEXT UNIQUE NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                revoked INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_refresh_tokens_token ON refresh_tokens(token)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_refresh_tokens_expires ON refresh_tokens(expires_at)')
        
        # memories 表迁移：添加 importance 和 strength 字段
        cursor.execute("PRAGMA table_info(memories)")
        mem_columns = [col[1] for col in cursor.fetchall()]
        if 'importance' not in mem_columns:
            cursor.execute('ALTER TABLE memories ADD COLUMN importance REAL DEFAULT 0.5')
            self._conn.commit()
            _log.info("已为memories表添加importance字段")
        if 'strength' not in mem_columns:
            cursor.execute('ALTER TABLE memories ADD COLUMN strength REAL DEFAULT 0.5')
            self._conn.commit()
            _log.info("已为memories表添加strength字段")
        if 'pinned' not in mem_columns:
            cursor.execute('ALTER TABLE memories ADD COLUMN pinned INTEGER DEFAULT 0')
            self._conn.commit()
            _log.info("已为memories表添加pinned字段")
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')

        # 通话历史（2026-08-22 新增：重启后未接来电/通话记录不再丢失）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS call_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                direction TEXT NOT NULL,
                status TEXT NOT NULL,
                duration INTEGER DEFAULT 0,
                reason TEXT DEFAULT '',
                created_ts REAL NOT NULL
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_call_history_user ON call_history(user_id, created_ts)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone)')
        
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_autobiography_user ON autobiography(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_autobiography_date ON autobiography(diary_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_emotions_user ON emotions(user_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_emotions_timestamp ON emotions(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_emotions_type ON emotions(emotion_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_growth_user ON growth(user_id)')
        
        # 高频查询索引优化
        # messages 表：按创建时间排序的消息历史查询
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_created_ts ON messages(created_ts)')
        # messages 表：按角色过滤（user/assistant）
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role)')
        # conversations 表：按最后活跃时间排序
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_conversations_last_active ON conversations(last_active_ts)')
        # conversations 表：按用户名查询
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_conversations_user_name ON conversations(user_name)')
        # memories 表：按分类查询
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category)')
        # memories 表：按置信度排序
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_memories_confidence ON memories(confidence)')
        
        self._conn.commit()
        _log.info("[DB] 数据库表结构和索引创建完成")

    def _compress(self, data: str) -> bytes:
        return zlib.compress(data.encode('utf-8'), level=6)

    def _decompress(self, compressed: bytes) -> str:
        return zlib.decompress(compressed).decode('utf-8')

    def _compute_hash(self, content: str) -> str:
        return hashlib.md5(content.encode()).hexdigest()

    @retry_on_locked(max_retries=3, delay=0.1)
    def add_sticker(self, filename: str, filepath: str, keyword: str = '', 
                   category: str = '', emotion: str = '', meaning: str = '',
                   source: str = '', size: int = 0, width: int = 0, 
                   height: int = 0, is_gif: bool = False, is_custom: bool = False):
        with self._lock:
            try:
                cursor = self._conn.cursor()
                file_hash = self._compute_hash(filepath)
                cursor.execute('''
                    INSERT OR REPLACE INTO stickers 
                    (filename, filepath, keyword, category, emotion, meaning, 
                     source, size, width, height, is_gif, is_custom, created_ts, hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (filename, filepath, keyword, category, emotion, meaning,
                      source, size, width, height, 1 if is_gif else 0, 
                      1 if is_custom else 0, time.time(), file_hash))
                self._conn.commit()
                return True
            except Exception as e:
                _log.warning(f"添加表情包失败: {e}")
                return False

    def get_stickers(self, keyword: str = None, emotion: str = None, 
                    category: str = None, limit: int = 50) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            query = 'SELECT * FROM stickers WHERE 1=1'
            params = []
            
            if keyword:
                query += ' AND (keyword LIKE ? OR filename LIKE ?)'
                params.extend([f'%{keyword}%', f'%{keyword}%'])
            if emotion:
                query += ' AND emotion LIKE ?'
                params.append(f'%{emotion}%')
            if category:
                query += ' AND category LIKE ?'
                params.append(f'%{category}%')
            
            query += ' ORDER BY usage_count DESC, created_ts DESC LIMIT ?'
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'filename': row[1], 'filepath': row[2],
                    'keyword': row[3], 'category': row[4], 'emotion': row[5],
                    'meaning': row[6], 'source': row[7], 'size': row[8],
                    'width': row[9], 'height': row[10], 'is_gif': bool(row[11]),
                    'is_custom': bool(row[12]), 'usage_count': row[13],
                    'last_used_ts': row[14], 'created_ts': row[15]
                })
            return result

    @retry_on_locked(max_retries=3, delay=0.1)
    def update_sticker_usage(self, filepath: str):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE stickers SET usage_count = usage_count + 1, last_used_ts = ?
                WHERE filepath = ?
            ''', (time.time(), filepath))
            self._conn.commit()

    @retry_on_locked(max_retries=3, delay=0.1)
    def delete_sticker(self, filename: str):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('DELETE FROM stickers WHERE filename = ?', (filename,))
            self._conn.commit()

    @retry_on_locked(max_retries=3, delay=0.1)
    def add_message(self, session_id: str, role: str, text: str,
                   sticker_path: str = '', sticker_meaning: str = '',
                   emotion: str = '', confidence: float = 0, response_time_ms: int = 0,
                   user_name: str = '', image_url: str = ''):
        # 使用事务确保会话更新 + 消息插入的原子性
        with self._transaction() as conn:
            cursor = conn.cursor()
            
            cursor.execute('SELECT id, name FROM conversations WHERE session_id = ?', (session_id,))
            conv_row = cursor.fetchone()
            conv_id = None
            if conv_row:
                conv_id = conv_row[0]
                cursor.execute('''
                    UPDATE conversations SET last_active_ts = ?, message_count = message_count + 1
                    WHERE id = ?
                ''', (time.time(), conv_id))
                # 【会话标题体系】无标题或占位标题时，用首条用户消息自动命名
                if role == 'user' and (conv_row[1] or '') in ('', '新会话'):
                    title = _make_session_title(text)
                    if title:
                        cursor.execute(
                            'UPDATE conversations SET name = ? WHERE id = ?',
                            (title, conv_id))
            else:
                init_title = _make_session_title(text) if role == 'user' else ''
                cursor.execute('''
                    INSERT INTO conversations (session_id, user_name, name, start_ts, last_active_ts, message_count)
                    VALUES (?, ?, ?, ?, ?, 1)
                ''', (session_id, user_name or session_id.split('_')[0], init_title, time.time(), time.time()))
                conv_id = cursor.lastrowid
            
            cursor.execute('''
                INSERT INTO messages
                (conversation_id, session_id, role, text, sticker_path, sticker_meaning,
                 emotion, confidence, response_time_ms, created_ts, image_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (conv_id, session_id, role, text, sticker_path, sticker_meaning,
                  emotion, confidence, response_time_ms, time.time(), image_url or ''))

    def get_conversation_messages(self, session_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT m.* FROM messages m
                JOIN conversations c ON m.conversation_id = c.id
                WHERE c.session_id = ?
                ORDER BY m.created_ts DESC LIMIT ?
            ''', (session_id, limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'conversation_id': row[1], 'session_id': row[2],
                    'role': row[3], 'text': row[4], 'sticker_path': row[5],
                    'sticker_meaning': row[6], 'emotion': row[7], 'confidence': row[8],
                    'response_time_ms': row[9], 'created_ts': row[10], 'image_url': row[11] if len(row) > 11 else ''
                })
            return result[::-1]

    def get_messages(self, session_id: str, limit: int = 20, before_message_id: int = None) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            if before_message_id:
                cursor.execute('''
                    SELECT m.* FROM messages m
                    JOIN conversations c ON m.conversation_id = c.id
                    WHERE c.session_id = ? AND m.id < ?
                    ORDER BY m.created_ts DESC LIMIT ?
                ''', (session_id, int(before_message_id), limit))
            else:
                cursor.execute('''
                    SELECT m.* FROM messages m
                    JOIN conversations c ON m.conversation_id = c.id
                    WHERE c.session_id = ?
                    ORDER BY m.created_ts DESC LIMIT ?
                ''', (session_id, limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'conversation_id': row[1], 'session_id': row[2],
                    'role': row[3], 'text': row[4], 'sticker_path': row[5],
                    'sticker_meaning': row[6], 'emotion': row[7], 'confidence': row[8],
                    'response_time_ms': row[9], 'created_ts': row[10], 'image_url': row[11] if len(row) > 11 else ''
                })
            return result[::-1]

    def get_user_messages(self, user_id: str, limit: int = 20, before_message_id: int = None) -> list[dict]:
        """按用户查询全部会话的消息（session_id 以 user_id_ 前缀标识）"""
        with self._lock:
            cursor = self._conn.cursor()
            if before_message_id:
                cursor.execute('''
                    SELECT m.* FROM messages m
                    JOIN conversations c ON m.conversation_id = c.id
                    WHERE c.session_id LIKE ? ESCAPE '!' AND m.id < ?
                    ORDER BY m.created_ts DESC LIMIT ?
                ''', (_user_session_like_pattern(user_id), int(before_message_id), limit))
            else:
                cursor.execute('''
                    SELECT m.* FROM messages m
                    JOIN conversations c ON m.conversation_id = c.id
                    WHERE c.session_id LIKE ? ESCAPE '!'
                    ORDER BY m.created_ts DESC LIMIT ?
                ''', (_user_session_like_pattern(user_id), limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'conversation_id': row[1], 'session_id': row[2],
                    'role': row[3], 'text': row[4], 'sticker_path': row[5],
                    'sticker_meaning': row[6], 'emotion': row[7], 'confidence': row[8],
                    'response_time_ms': row[9], 'created_ts': row[10], 'image_url': row[11] if len(row) > 11 else ''
                })
            return result[::-1]

    @retry_on_locked(max_retries=3, delay=0.1)
    def delete_message(self, message_id: int, user_id: str = '') -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                # 【安全加固】归属校验：防止越权删除他人消息（消息 id 可枚举）
                uid = str(user_id).strip()
                if uid:
                    cursor.execute('''
                        SELECT c.session_id, c.user_name FROM messages m
                        JOIN conversations c ON m.conversation_id = c.id
                        WHERE m.id = ?
                    ''', (int(message_id),))
                    row = cursor.fetchone()
                    if not row:
                        return False
                    sid = row[0] or ''
                    uname = str(row[1] or '')
                    if not (sid.startswith(f"{uid}_") or uname == uid):
                        _log.warning(f"[安全] 用户 {uid} 试图删除不属于自己的消息 {message_id}")
                        return False
                cursor.execute('DELETE FROM messages WHERE id = ?', (int(message_id),))
                self._conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                _log.warning(f"删除消息失败: {e}")
                return False

    def get_conversations(self, user_id: str = '', limit: int = 20) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            if user_id:
                # 【bug修复】按用户隔离会话：user_name 精确匹配或 session_id 前缀匹配
                # （兼容 migrate_from_json 导入的旧数据 user_name 为空的情况）
                query = '''
                    SELECT id, session_id, user_name, name, start_ts, last_active_ts,
                           message_count, emotion_score, intimacy_level, status
                    FROM conversations 
                    WHERE user_name = ? OR session_id LIKE ? ESCAPE '!'
                    ORDER BY last_active_ts DESC LIMIT ?
                '''
                params = [user_id, _user_session_like_pattern(user_id), limit]
            else:
                query = '''
                    SELECT id, session_id, user_name, name, start_ts, last_active_ts,
                           message_count, emotion_score, intimacy_level, status
                    FROM conversations 
                    ORDER BY last_active_ts DESC LIMIT ?
                '''
                params = [limit]
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'session_id': row[1], 'user_name': row[2],
                    'name': row[3],
                    'start_ts': row[4], 'last_active_ts': row[5], 'message_count': row[6],
                    'emotion_score': row[7], 'intimacy_level': row[8], 'status': row[9]
                })
            return result

    def create_conversation(self, user_id: str, name: str = '') -> str:
        with self._lock:
            try:
                # 【bug修复】秒级时间戳在同秒重复创建会话时撞 UNIQUE 约束；
                # 改用毫秒时间戳+随机后缀（前缀仍为 {user_id}_，兼容所有归属校验/解析）
                session_id = f"{user_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
                cursor = self._conn.cursor()
                cursor.execute('''
                    INSERT INTO conversations (session_id, user_name, name, start_ts, last_active_ts)
                    VALUES (?, ?, ?, ?, ?)
                ''', (session_id, user_id, (name or '')[:64], time.time(), time.time()))
                self._conn.commit()
                return session_id
            except Exception as e:
                _log.warning(f"创建会话失败: {e}")
                return f"{user_id}_{int(time.time() * 1000)}_{os.urandom(2).hex()}"

    @retry_on_locked(max_retries=3, delay=0.1)
    def delete_conversation(self, session_id: str, user_id: str = '') -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute('SELECT id, session_id, user_name FROM conversations WHERE session_id = ?', (session_id,))
                conv_row = cursor.fetchone()
                if not conv_row:
                    return False
                # 【安全加固】归属校验：防止越权删除他人会话（session_id 可枚举）
                uid = str(user_id).strip()
                if uid:
                    sid = conv_row[1] or ''
                    uname = str(conv_row[2] or '')
                    if not (sid.startswith(f"{uid}_") or uname == uid):
                        _log.warning(f"[安全] 用户 {uid} 试图删除不属于自己的会话 {session_id}")
                        return False
                conv_id = conv_row[0]
                cursor.execute('DELETE FROM messages WHERE conversation_id = ?', (conv_id,))
                cursor.execute('DELETE FROM conversations WHERE id = ?', (conv_id,))
                self._conn.commit()
                return True
            except Exception as e:
                _log.warning(f"删除会话失败: {e}")
                return False

    def clear_all_conversations(self, user_id: str = '') -> bool:
        """清空指定用户的会话与消息。

        【安全加固】必须传入 user_id，不再支持全库清空。
        旧版无条件 DELETE FROM messages/conversations，
        多用户模式下任何登录用户都能清空所有人的聊天记录。
        """
        uid = str(user_id).strip()
        if not uid:
            _log.warning("[安全] clear_all_conversations 拒绝执行：未提供 user_id")
            return False
        with self._lock:
            try:
                cursor = self._conn.cursor()
                pattern = _user_session_like_pattern(uid)
                cursor.execute(
                    "DELETE FROM messages WHERE conversation_id IN "
                    "(SELECT id FROM conversations WHERE session_id LIKE ? ESCAPE '!')",
                    (pattern,))
                cursor.execute(
                    "DELETE FROM conversations WHERE session_id LIKE ? ESCAPE '!'",
                    (pattern,))
                self._conn.commit()
                return True
            except Exception as e:
                _log.warning(f"清空会话失败: {e}")
                return False

    @retry_on_locked(max_retries=3, delay=0.1)
    def clear_conversation(self, session_id: str) -> bool:
        """清空指定会话的消息（保留会话本身）"""
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute('''
                    SELECT id FROM conversations WHERE session_id = ?
                ''', (session_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                cursor.execute('DELETE FROM messages WHERE conversation_id = ?', (row[0],))
                cursor.execute('UPDATE conversations SET message_count = 0 WHERE id = ?', (row[0],))
                self._conn.commit()
                return True
            except Exception as e:
                _log.warning(f"清空会话失败: {e}")
                return False

    def clear_user_profile(self, user_id: int) -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute('UPDATE users SET nickname = ?, avatar = ? WHERE id = ?', ('', '', user_id))
                # 【多用户隔离】仅删除当前用户的记忆，不删除其他用户的数据
                cursor.execute('DELETE FROM memories WHERE user_id = ?', (str(user_id),))
                self._conn.commit()
                return True
            except Exception as e:
                _log.warning(f"清除用户画像失败: {e}")
                return False

    def add_memory(self, key: str, value: str, category: str = '', 
                  confidence: float = 0.8, source: str = '', user_id: str = ''):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO memories 
                (user_id, key, value, category, confidence, source, created_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, key, value, category, confidence, source, time.time()))
            self._conn.commit()

    def get_memory(self, key: str) -> str | None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE memories SET usage_count = usage_count + 1, last_access_ts = ?
                WHERE key = ?
            ''', (time.time(), key))
            cursor.execute('SELECT value FROM memories WHERE key = ?', (key,))
            row = cursor.fetchone()
            self._conn.commit()
            return row[0] if row else None

    # 【已清理】重复的 search_memories 定义已移除，统一使用 line 1636 的完整版
    # （支持 category 搜索 + expires_at 字段 + last_access_ts 排序）

    def add_knowledge(self, topic: str, content: str, source: str = '', quality: float = 0.5):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO knowledge 
                (topic, content, source, quality, created_ts)
                VALUES (?, ?, ?, ?, ?)
            ''', (topic, content, source, quality, time.time()))
            self._conn.commit()

    def search_knowledge(self, query: str, limit: int = 10) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT * FROM knowledge 
                WHERE topic LIKE ? OR content LIKE ?
                ORDER BY usage_count DESC, quality DESC LIMIT ?
            ''', (f'%{query}%', f'%{query}%', limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'topic': row[1], 'content': row[2],
                    'source': row[3], 'quality': row[4], 'usage_count': row[5],
                    'last_access_ts': row[6], 'created_ts': row[7]
                })
            return result

    def add_search_cache(self, query: str, results: list[dict], confidence: float = 0):
        with self._lock:
            cursor = self._conn.cursor()
            query_hash = self._compute_hash(query)
            expires_at = time.time() + (48 * 3600)
            results_json = json.dumps(results, ensure_ascii=False)
            cursor.execute('''
                INSERT OR REPLACE INTO search_cache 
                (query_hash, query, results, confidence, created_ts, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (query_hash, query, results_json, confidence, time.time(), expires_at))
            self._conn.commit()

    def get_search_cache(self, query: str) -> list[dict] | None:
        with self._lock:
            cursor = self._conn.cursor()
            query_hash = self._compute_hash(query)
            cursor.execute('''
                SELECT results FROM search_cache 
                WHERE query_hash = ? AND expires_at > ?
            ''', (query_hash, time.time()))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None

    def clean_expired_cache(self):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('DELETE FROM search_cache WHERE expires_at < ?', (time.time(),))
            self._conn.commit()

    def vacuum(self):
        with self._lock:
            try:
                # SQLite: VACUUM / MySQL: OPTIMIZE TABLE（由后端适配）
                self._backend.optimize(self._conn, [
                    'users', 'conversations', 'messages', 'memories',
                    'memory_tags', 'emotions', 'knowledge', 'growth',
                    'search_cache', 'learning', 'emotional_states',
                    'knowledge_triples', 'emotional_memories', 'autobiography'
                ])
                self._conn.commit()
                _log.info("数据库压缩完成")
            except Exception as e:
                _log.warning(f"压缩失败: {e}")

    def add_learning(self, learning_type: str, content: str, source: str = '', quality: float = 0.5, metadata: str = ''):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT INTO learning 
                (learning_type, content, source, quality, metadata, created_ts)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (learning_type, content, source, quality, metadata, time.time()))
            self._conn.commit()

    def search_learning(self, learning_type: str = None, query: str = '', limit: int = 10) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            sql_query = 'SELECT * FROM learning WHERE 1=1'
            params = []
            
            if learning_type:
                sql_query += ' AND learning_type = ?'
                params.append(learning_type)
            if query:
                sql_query += ' AND (content LIKE ? OR source LIKE ?)'
                params.extend([f'%{query}%', f'%{query}%'])
            
            sql_query += ' ORDER BY quality DESC, created_ts DESC LIMIT ?'
            params.append(limit)
            
            cursor.execute(sql_query, params)
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'learning_type': row[1], 'content': row[2],
                    'source': row[3], 'quality': row[4], 'usage_count': row[5],
                    'last_access_ts': row[6], 'created_ts': row[7], 'metadata': row[8]
                })
            return result

    def update_learning_usage(self, learning_id: int):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE learning SET usage_count = usage_count + 1, last_access_ts = ?
                WHERE id = ?
            ''', (time.time(), learning_id))
            self._conn.commit()

    def save_emotional_state(self, emotional_vector: dict, event_desc: str = ''):
        with self._lock:
            cursor = self._conn.cursor()
            vector_json = json.dumps(emotional_vector, ensure_ascii=False)
            cursor.execute('''
                INSERT INTO emotional_states 
                (user_id, pleasure, arousal, dominance, joy, sadness, anger, surprise, 
                 fear, love, empathy, pride, shame, event_desc, timestamp, vector_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                '', emotional_vector.get('pleasure', 0), emotional_vector.get('arousal', 0),
                emotional_vector.get('dominance', 0), emotional_vector.get('joy', 0),
                emotional_vector.get('sadness', 0), emotional_vector.get('anger', 0),
                emotional_vector.get('surprise', 0), emotional_vector.get('fear', 0),
                emotional_vector.get('love', 0), emotional_vector.get('empathy', 0),
                emotional_vector.get('pride', 0), emotional_vector.get('shame', 0),
                event_desc, time.time(), vector_json
            ))
            self._conn.commit()

    def load_latest_emotional_state(self) -> dict | None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT pleasure, arousal, dominance, joy, sadness, anger, surprise, 
                       fear, love, empathy, pride, shame, event_desc, timestamp, vector_json
                FROM emotional_states 
                ORDER BY timestamp DESC LIMIT 1
            ''')
            row = cursor.fetchone()
            if row:
                result = {
                    'pleasure': row[0], 'arousal': row[1], 'dominance': row[2],
                    'joy': row[3], 'sadness': row[4], 'anger': row[5],
                    'surprise': row[6], 'fear': row[7], 'love': row[8],
                    'empathy': row[9], 'pride': row[10], 'shame': row[11],
                    'event_desc': row[12], 'timestamp': row[13]
                }
                if row[14]:
                    try:
                        vector_data = json.loads(row[14])
                        result.update(vector_data)
                    except (json.JSONDecodeError, TypeError) as e:
                        # vector 字段格式异常时记录但不阻断主流程
                        _log.warning(f"[DB] emotional_state vector 解析失败: {e}")
                return result
            return None

    def get_recent_emotional_highlights(self, hours: int = 24, min_intensity: float = 0.3) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            cutoff_time = time.time() - hours * 3600
            cursor.execute('''
                SELECT * FROM emotional_states 
                WHERE timestamp > ? 
                ORDER BY (joy + sadness + anger + love + empathy) DESC
            ''', (cutoff_time,))
            rows = cursor.fetchall()
            highlights = []
            for row in rows:
                intensity = row[4] + row[5] + row[6] + row[10] + row[11]
                if intensity >= min_intensity:
                    highlights.append({
                        'pleasure': row[1], 'arousal': row[2], 'dominance': row[3],
                        'joy': row[4], 'sadness': row[5], 'anger': row[6],
                        'surprise': row[7], 'fear': row[8], 'love': row[9],
                        'empathy': row[10], 'pride': row[11], 'shame': row[12],
                        'event_desc': row[13], 'timestamp': row[14]
                    })
            return highlights[:10]

    # ============ 定时提醒（主动服务） ============

    @retry_on_locked(max_retries=3, delay=0.1)
    def create_reminder(self, user_id: str, remind_ts: float, content: str) -> int:
        """创建一条定时提醒，返回提醒ID"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT INTO reminders (user_id, remind_ts, content, status, created_ts)
                VALUES (?, ?, ?, 'pending', ?)
            ''', (str(user_id), float(remind_ts), content[:500], time.time()))
            self._conn.commit()
            return cursor.lastrowid

    @retry_on_locked(max_retries=3, delay=0.1)
    def get_due_reminders(self, limit: int = 20) -> list[dict]:
        """获取所有已到期待触发的提醒（调度线程轮询）"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT id, user_id, remind_ts, content, created_ts
                FROM reminders
                WHERE status = 'pending' AND remind_ts <= ?
                ORDER BY remind_ts ASC
                LIMIT ?
            ''', (time.time(), limit))
            rows = cursor.fetchall()
            return [
                {'id': r[0], 'user_id': r[1], 'remind_ts': r[2],
                 'content': r[3], 'created_ts': r[4]}
                for r in rows
            ]

    @retry_on_locked(max_retries=3, delay=0.1)
    def mark_reminder_fired(self, reminder_id: int) -> bool:
        """标记提醒已触发（原子操作，防止多实例重复触发）"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE reminders SET status = 'fired', fired_ts = ?
                WHERE id = ? AND status = 'pending'
            ''', (time.time(), reminder_id))
            self._conn.commit()
            return cursor.rowcount > 0

    @retry_on_locked(max_retries=3, delay=0.1)
    def get_user_reminders(self, user_id: str, include_fired: bool = False) -> list[dict]:
        """查询用户提醒列表（pending 在前，按时间升序）"""
        with self._lock:
            cursor = self._conn.cursor()
            if include_fired:
                cursor.execute('''
                    SELECT id, remind_ts, content, status, created_ts, fired_ts
                    FROM reminders WHERE user_id = ?
                    ORDER BY status DESC, remind_ts ASC LIMIT 50
                ''', (str(user_id),))
            else:
                cursor.execute('''
                    SELECT id, remind_ts, content, status, created_ts, fired_ts
                    FROM reminders WHERE user_id = ? AND status = 'pending'
                    ORDER BY remind_ts ASC LIMIT 50
                ''', (str(user_id),))
            rows = cursor.fetchall()
            return [
                {'id': r[0], 'remind_ts': r[1], 'content': r[2],
                 'status': r[3], 'created_ts': r[4], 'fired_ts': r[5]}
                for r in rows
            ]

    @retry_on_locked(max_retries=3, delay=0.1)
    def cancel_reminder(self, user_id: str, reminder_id: int) -> bool:
        """取消用户的一条待触发提醒"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE reminders SET status = 'cancelled'
                WHERE id = ? AND user_id = ? AND status = 'pending'
            ''', (reminder_id, str(user_id)))
            self._conn.commit()
            return cursor.rowcount > 0

    def add_knowledge_triple(self, subject: str, relation: str, object: str, 
                            confidence: float = 0.8, source: str = '', verified: bool = False):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT id, confidence FROM knowledge_triples 
                WHERE subject = ? AND relation = ? AND object = ?
            ''', (subject, relation, object))
            row = cursor.fetchone()
            if row:
                if confidence > row[1]:
                    cursor.execute('''
                        UPDATE knowledge_triples 
                        SET confidence = ?, verified = ?, usage_count = usage_count + 1
                        WHERE id = ?
                    ''', (confidence, 1 if verified else 0, row[0]))
            else:
                cursor.execute('''
                    INSERT INTO knowledge_triples 
                    (subject, relation, object, confidence, source, verified, created_ts)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (subject, relation, object, confidence, source, 1 if verified else 0, time.time()))
            self._conn.commit()

    def search_triples(self, query: str, limit: int = 10) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT * FROM knowledge_triples 
                WHERE subject LIKE ? OR object LIKE ? OR relation LIKE ?
                ORDER BY confidence DESC, usage_count DESC LIMIT ?
            ''', (f'%{query}%', f'%{query}%', f'%{query}%', limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'subject': row[1], 'relation': row[2], 'object': row[3],
                    'confidence': row[4], 'source': row[5], 'verified': bool(row[6]),
                    'created_ts': row[7], 'usage_count': row[8]
                })
            return result

    def get_triples_by_subject(self, subject: str, limit: int = 10) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT * FROM knowledge_triples 
                WHERE subject = ? 
                ORDER BY confidence DESC, usage_count DESC LIMIT ?
            ''', (subject, limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                cursor.execute('UPDATE knowledge_triples SET usage_count = usage_count + 1 WHERE id = ?', (row[0],))
                result.append({
                    'id': row[0], 'subject': row[1], 'relation': row[2], 'object': row[3],
                    'confidence': row[4], 'source': row[5], 'verified': bool(row[6]),
                    'created_ts': row[7], 'usage_count': row[8]
                })
            self._conn.commit()
            return result

    def get_conflicting_triples(self, subject: str, relation: str, object: str) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT * FROM knowledge_triples 
                WHERE subject = ? AND relation = ? AND object != ? AND verified = 1
            ''', (subject, relation, object))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'subject': row[1], 'relation': row[2], 'object': row[3],
                    'confidence': row[4], 'source': row[5], 'verified': bool(row[6]),
                    'created_ts': row[7], 'usage_count': row[8]
                })
            return result

    def verify_triple(self, triple_id: int, verified: bool, confidence: float = 1.0):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE knowledge_triples 
                SET verified = ?, confidence = ? 
                WHERE id = ?
            ''', (1 if verified else 0, confidence, triple_id))
            self._conn.commit()

    def cleanup_low_confidence_triples(self, min_confidence: float = 0.3):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                DELETE FROM knowledge_triples 
                WHERE confidence < ? AND verified = 0
            ''', (min_confidence,))
            self._conn.commit()

    def add_emotional_memory(self, content: str, emotion_type: str = '', intensity: float = 0,
                            vector_blob: bytes = None, keywords: str = ''):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT INTO emotional_memories 
                (content, emotion_type, intensity, vector_blob, keywords, created_ts, last_access_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (content, emotion_type, intensity, vector_blob, keywords, time.time(), time.time()))
            self._conn.commit()
            return cursor.lastrowid

    def get_all_emotional_memories(self, user_id: str = '', limit: int = 100) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            if user_id:
                cursor.execute('''
                    SELECT * FROM emotional_memories 
                    WHERE user_id = ? OR user_id = ''
                    ORDER BY intensity DESC, created_ts DESC LIMIT ?
                ''', (user_id, limit))
            else:
                cursor.execute('''
                    SELECT * FROM emotional_memories 
                    ORDER BY intensity DESC, created_ts DESC LIMIT ?
                ''', (limit,))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'user_id': row[1], 'content': row[2], 'emotion_type': row[3],
                    'intensity': row[4], 'vector_blob': row[5], 'keywords': row[6],
                    'created_ts': row[7], 'last_access_ts': row[8], 'access_count': row[9]
                })
            return result

    def update_emotional_memory_vector(self, memory_id: int, vector_blob: bytes):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE emotional_memories SET vector_blob = ? WHERE id = ?
            ''', (vector_blob, memory_id))
            self._conn.commit()

    def increment_memory_access(self, memory_id: int):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE emotional_memories 
                SET access_count = access_count + 1, last_access_ts = ? 
                WHERE id = ?
            ''', (time.time(), memory_id))
            self._conn.commit()

    def get_emotional_memories_by_keyword(self, keywords: list[str], limit: int = 10) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            if not keywords:
                return []

            # 【加固】原实现按关键词数量动态拼 LIKE 子句（占位符安全但属动态 SQL）。
            # 改为静态 SQL 取全量 + Python 侧关键词过滤，语义与排序不变。
            cursor.execute('''
                SELECT * FROM emotional_memories
                ORDER BY intensity DESC, access_count DESC
            ''')
            rows = cursor.fetchall()
            kw_lower = [str(kw).lower() for kw in keywords[:8]]
            result = []
            for row in rows:
                kw_field = str(row[6] or '').lower()
                if any(kw in kw_field for kw in kw_lower):
                    result.append({
                        'id': row[0], 'user_id': row[1], 'content': row[2], 'emotion_type': row[3],
                        'intensity': row[4], 'vector_blob': row[5], 'keywords': row[6],
                        'created_ts': row[7], 'last_access_ts': row[8], 'access_count': row[9]
                    })
                    if len(result) >= limit:
                        break
            return result

    @staticmethod
    def _autobiography_row_to_dict(row) -> dict:
        """autobiography 行 → dict（列序固定，避免 SELECT * 在 MySQL 列序错位）"""
        return {
            'id': row[0], 'user_id': row[1] or '', 'diary_date': row[2], 'diary_text': row[3],
            'emotion_summary': row[4], 'knowledge_added': row[5], 'user_profile_changes': row[6],
            'created_ts': row[7],
        }

    # 【L6 猫咪日记】统一显式列名查询（MySQL/SQLite 列序一致性由列名保证）
    _AUTOBIOGRAPHY_COLS = 'id, user_id, diary_date, diary_text, emotion_summary, knowledge_added, user_profile_changes, created_ts'

    @retry_on_locked(max_retries=3, delay=0.1)
    def add_autobiography_entry(self, user_id: str, diary_date: str, diary_text: str, emotion_summary: str = '',
                               knowledge_added: str = '', user_profile_changes: str = ''):
        """【L6 猫咪日记】写入某用户某天的日记（(user_id, diary_date) 唯一约束下 INSERT OR REPLACE 幂等）"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO autobiography
                (user_id, diary_date, diary_text, emotion_summary, knowledge_added, user_profile_changes, created_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id or '', diary_date, diary_text, emotion_summary, knowledge_added, user_profile_changes, time.time()))
            self._conn.commit()

    def get_autobiography_by_date(self, user_id: str, diary_date: str) -> dict | None:
        """【L6 猫咪日记】按用户+日期取日记"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            # 列清单为类常量，直接内联字面量保持 SQL 全静态（防配置污染注入面）
            cursor.execute('''
                SELECT id, user_id, diary_date, diary_text, emotion_summary, knowledge_added, user_profile_changes, created_ts
                FROM autobiography
                WHERE user_id = ? AND diary_date = ?
            ''', (user_id or '', diary_date))
            row = cursor.fetchone()
            return self._autobiography_row_to_dict(row) if row else None

    def get_recent_autobiography(self, user_id: str, days: int = 3) -> list[dict]:
        """【L6 猫咪日记】取某用户最近 N 天的日记（缺日记的天跳过）"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            import datetime
            today = datetime.date.today()
            result = []
            for i in range(days):
                date_str = (today - datetime.timedelta(days=i)).isoformat()
                # 列清单为类常量，直接内联字面量保持 SQL 全静态
                cursor.execute('''
                    SELECT id, user_id, diary_date, diary_text, emotion_summary, knowledge_added, user_profile_changes, created_ts
                    FROM autobiography
                    WHERE user_id = ? AND diary_date = ?
                ''', (user_id or '', date_str))
                row = cursor.fetchone()
                if row:
                    result.append(self._autobiography_row_to_dict(row))
            return result

    def get_autobiography_by_date_range(self, user_id: str, start_date: str, end_date: str) -> list[dict]:
        """【L6 回忆相册】取某用户日期区间内的日记"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(f'''
                SELECT {self._AUTOBIOGRAPHY_COLS} FROM autobiography
                WHERE user_id = ? AND diary_date BETWEEN ? AND ?
                ORDER BY diary_date DESC
            ''', (user_id or '', start_date, end_date))
            return [self._autobiography_row_to_dict(row) for row in cursor.fetchall()]

    def get_autobiography_archive(self, user_id: str, limit: int = 90) -> list[dict]:
        """【L6 回忆相册】某用户全部日记（按日期倒序，限 limit 条）"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(f'''
                SELECT {self._AUTOBIOGRAPHY_COLS} FROM autobiography
                WHERE user_id = ?
                ORDER BY diary_date DESC
                LIMIT {int(limit)}
            ''', (user_id or '',))
            return [self._autobiography_row_to_dict(row) for row in cursor.fetchall()]

    def get_autobiography_on_month_day(self, user_id: str, month: int, day: int, exclude_year: int) -> list[dict]:
        """【L6 那年今日】某用户历史上同月同日的日记（排除今年），按年份倒序"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            # diary_date 格式为 YYYY-MM-DD，用 LIKE '%%-MM-DD' 匹配同月同日（排除指定年份）
            pattern = f'-{int(month):02d}-{int(day):02d}'
            cursor.execute(f'''
                SELECT {self._AUTOBIOGRAPHY_COLS} FROM autobiography
                WHERE user_id = ? AND diary_date LIKE ? AND diary_date NOT LIKE ?
                ORDER BY diary_date DESC
            ''', (user_id or '', f'%{pattern}', f'{int(exclude_year)}%'))
            return [self._autobiography_row_to_dict(row) for row in cursor.fetchall()]

    # ---------- 【L7 生活模拟】朋友圈动态 CRUD ----------

    _CAT_MOMENT_COLS = 'id, user_id, content, mood_emoji, activity, source, liked, liked_ts, created_ts'

    @staticmethod
    def _cat_moment_row_to_dict(row) -> dict:
        return {
            'id': row[0], 'user_id': row[1] or '', 'content': row[2],
            'mood_emoji': row[3] or '🐾', 'activity': row[4] or '',
            'source': row[5] or 'template',
            'liked': bool(row[6]), 'liked_ts': row[7], 'created_ts': row[8],
        }

    @retry_on_locked(max_retries=3, delay=0.1)
    def add_cat_moment(self, user_id: str, content: str, mood_emoji: str = '🐾',
                       activity: str = '', source: str = 'template') -> int:
        """写入一条朋友圈动态，返回行 id"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT INTO cat_moments (user_id, content, mood_emoji, activity, source, created_ts)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id or '', content, mood_emoji, activity, source, time.time()))
            self._conn.commit()
            return cursor.lastrowid

    def get_recent_cat_moments(self, user_id: str, limit: int = 20) -> list[dict]:
        """【朋友圈】某用户最近的动态（倒序）"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(f'''
                SELECT {self._CAT_MOMENT_COLS} FROM cat_moments
                WHERE user_id = ?
                ORDER BY created_ts DESC
                LIMIT {int(limit)}
            ''', (user_id or '',))
            return [self._cat_moment_row_to_dict(row) for row in cursor.fetchall()]

    def count_cat_moments_today(self, user_id: str) -> int:
        """【噪音纪律】某用户今日动态条数（供每日上限判断）"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            cursor.execute('''
                SELECT COUNT(*) FROM cat_moments WHERE user_id = ? AND created_ts >= ?
            ''', (user_id or '', day_start))
            return cursor.fetchone()[0]

    def get_latest_cat_moment_ts(self, user_id: str) -> float:
        """【噪音纪律】某用户最近一条动态的时间戳（无动态返回 0）"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT created_ts FROM cat_moments WHERE user_id = ?
                ORDER BY created_ts DESC LIMIT 1
            ''', (user_id or '',))
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0.0

    @retry_on_locked(max_retries=3, delay=0.1)
    def set_cat_moment_liked(self, user_id: str, moment_id: int, liked: bool) -> bool:
        """点赞/取消点赞动态（校验 user_id 归属）"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                'UPDATE cat_moments SET liked = ?, liked_ts = ? WHERE id = ? AND user_id = ?',
                (1 if liked else 0, time.time() if liked else 0, moment_id, user_id or ''))
            self._conn.commit()
            return cursor.rowcount > 0

    def create_user(self, username: str, password_hash: str, phone: str = '', email: str = '', role: str = 'user') -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute('''
                    INSERT INTO users (username, password_hash, phone, email, role, created_ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (username, password_hash, phone, email, role, time.time()))
                self._conn.commit()
                return True
            except self._backend.IntegrityError:
                # 唯一键冲突：回滚残留的隐式事务，避免影响后续操作
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                return False

    def get_user_by_username(self, username: str) -> dict | None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, phone, email, avatar, role, created_ts, last_login_ts, status
                FROM users WHERE username = ?
            ''', (username,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0], 'username': row[1], 'password_hash': row[2],
                    'phone': row[3], 'email': row[4], 'avatar': row[5], 'role': row[6],
                    'created_ts': row[7], 'last_login_ts': row[8], 'status': row[9]
                }
            return None

    def get_user_by_phone(self, phone: str) -> dict | None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, phone, email, avatar, role, created_ts, last_login_ts, status
                FROM users WHERE phone = ?
            ''', (phone,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0], 'username': row[1], 'password_hash': row[2],
                    'phone': row[3], 'email': row[4], 'avatar': row[5], 'role': row[6],
                    'created_ts': row[7], 'last_login_ts': row[8], 'status': row[9]
                }
            return None

    def update_user_login(self, user_id: int):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE users SET last_login_ts = ? WHERE id = ?
            ''', (time.time(), user_id))
            self._conn.commit()

    def update_user_profile(self, user_id: int, email: str = None, avatar: str = None, nickname: str = None):
        with self._lock:
            cursor = self._conn.cursor()
            if email:
                cursor.execute('UPDATE users SET email = ? WHERE id = ?', (email, user_id))
            if avatar:
                cursor.execute('UPDATE users SET avatar = ? WHERE id = ?', (avatar, user_id))
            if nickname:
                cursor.execute('UPDATE users SET nickname = ? WHERE id = ?', (nickname, user_id))
            self._conn.commit()

    def update_user_password(self, user_id: int, password_hash: str):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash, user_id))
            self._conn.commit()

    def delete_user(self, user_id: int):
        """删除用户及其所有关联数据（级联清理，避免孤儿数据）"""
        with self._lock:
            cursor = self._conn.cursor()
            uid = str(user_id)
            # 会话与消息（conversations.user_name 实际存储 user_id）
            cursor.execute('''
                DELETE FROM messages WHERE conversation_id IN (
                    SELECT id FROM conversations WHERE user_name = ?
                )
            ''', (uid,))
            cursor.execute('DELETE FROM conversations WHERE user_name = ?', (uid,))
            # 按 user_id 归属的业务表
            for table in (
                'autobiography', 'call_history', 'emotional_memories',
                'emotional_states', 'emotions', 'feedback', 'growth',
                'memories', 'rag_emotional_memory', 'rag_fact_memory',
                'rag_identity_memory', 'rag_interaction_memory', 'reminders',
                'refresh_tokens',
            ):
                cursor.execute(f'DELETE FROM {table} WHERE user_id = ?', (uid,))
            cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
            self._conn.commit()

    def get_user_by_id(self, user_id: int) -> dict | None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT id, username, password_hash, phone, email, nickname, avatar, role, created_ts, last_login_ts, status
                FROM users WHERE id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            if row:
                return {
                    'id': row[0], 'username': row[1], 'password_hash': row[2],
                    'phone': row[3], 'email': row[4], 'nickname': row[5], 'avatar': row[6], 'role': row[7],
                    'created_ts': row[8], 'last_login_ts': row[9], 'status': row[10]
                }
            return None

    def get_all_users(self) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT id, username, phone, email, role, created_ts, last_login_ts, status
                FROM users ORDER BY created_ts DESC
            ''')
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'username': row[1], 'phone': row[2],
                    'email': row[3], 'role': row[4], 'created_ts': row[5],
                    'last_login_ts': row[6], 'status': row[7]
                })
            return result

    def get_users_page(self, page: int = 1, page_size: int = 10,
                       search: str = '') -> dict:
        """分页获取用户列表（管理面板），支持按用户名/手机号模糊搜索。

        返回 {'users': [...], 'total': n}
        """
        page = max(1, int(page))
        page_size = min(max(1, int(page_size)), 100)
        search = (search or '').strip()
        with self._lock:
            cursor = self._conn.cursor()
            where = ''
            params: list = []
            if search:
                where = ' WHERE username LIKE ? OR phone LIKE ?'
                kw = f'%{search}%'
                params = [kw, kw]
            cursor.execute(f'SELECT COUNT(*) FROM users{where}', params)
            total = cursor.fetchone()[0]
            cursor.execute(f'''
                SELECT id, username, phone, email, role, created_ts, last_login_ts, status
                FROM users{where} ORDER BY created_ts DESC LIMIT ? OFFSET ?
            ''', params + [page_size, (page - 1) * page_size])
            rows = cursor.fetchall()
            users = []
            for row in rows:
                users.append({
                    'id': row[0], 'username': row[1], 'phone': row[2],
                    'email': row[3], 'role': row[4], 'created_ts': row[5],
                    'last_login_ts': row[6], 'status': row[7]
                })
            return {'users': users, 'total': total}

    def update_user_role(self, user_id: int, role: str):
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('UPDATE users SET role = ? WHERE id = ?', (role, user_id))
            self._conn.commit()

    # ========== 管理后台专用方法 ==========

    def update_user_status(self, user_id: int, status: str) -> bool:
        """更新用户状态（active 正常 / disabled 禁用）"""
        if status not in ('active', 'disabled'):
            return False
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('UPDATE users SET status = ? WHERE id = ?', (status, user_id))
            self._conn.commit()
            return cursor.rowcount > 0

    def reset_user_password(self, user_id: int, password_hash: str) -> bool:
        """管理员重置用户密码（password_hash 由调用方生成）"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                           (password_hash, user_id))
            self._conn.commit()
            return cursor.rowcount > 0

    def get_admin_messages(self, page: int = 1, page_size: int = 20,
                           keyword: str = '') -> dict:
        """管理后台：分页查询全部消息，支持按内容/会话关键词过滤"""
        with self._lock:
            cursor = self._conn.cursor()
            where = ''
            params: list = []
            if keyword:
                where = 'WHERE text LIKE ? OR session_id LIKE ?'
                params = [f'%{keyword}%', f'%{keyword}%']
            cursor.execute(f'SELECT COUNT(*) FROM messages {where}', params)
            total = cursor.fetchone()[0]
            offset = max(0, (page - 1)) * page_size
            cursor.execute(f'''
                SELECT id, session_id, role, text, emotion, response_time_ms, created_ts
                FROM messages {where}
                ORDER BY created_ts DESC LIMIT ? OFFSET ?
            ''', params + [page_size, offset])
            rows = cursor.fetchall()
            items = [{
                'id': r[0], 'session_id': r[1], 'role': r[2], 'text': r[3],
                'emotion': r[4], 'response_time_ms': r[5], 'created_ts': r[6]
            } for r in rows]
            return {'total': total, 'page': page, 'page_size': page_size, 'items': items}

    def get_admin_feedback(self, page: int = 1, page_size: int = 20) -> dict:
        """管理后台：分页查询用户反馈"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM feedback')
            total = cursor.fetchone()[0]
            offset = max(0, (page - 1)) * page_size
            cursor.execute('''
                SELECT id, user_id, content, created_ts
                FROM feedback ORDER BY created_ts DESC LIMIT ? OFFSET ?
            ''', (page_size, offset))
            rows = cursor.fetchall()
            items = [{'id': r[0], 'user_id': r[1], 'content': r[2], 'created_ts': r[3]}
                     for r in rows]
            return {'total': total, 'page': page, 'page_size': page_size, 'items': items}

    def delete_feedback(self, feedback_id: int) -> bool:
        """管理后台：删除用户反馈"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('DELETE FROM feedback WHERE id = ?', (feedback_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def get_admin_dashboard(self) -> dict:
        """管理后台看板：核心统计 + 近 7 天消息趋势"""
        with self._lock:
            cursor = self._conn.cursor()
            stats: dict[str, int] = {}
            for table in ['users', 'conversations', 'messages', 'memories',
                          'emotional_memories', 'feedback', 'call_history']:
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                stats[table] = cursor.fetchone()[0]

            # 近 7 天每日消息量
            trend = []
            now = time.time()
            for i in range(6, -1, -1):
                day = time.localtime(now - i * 86400)
                # 按本地时区零点切日（修复原 UTC 对齐导致的 8 小时统计偏差）
                day_start = time.mktime((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0, 0, 0, -1))
                day_end = day_start + 86400
                cursor.execute(
                    'SELECT COUNT(*) FROM messages WHERE created_ts >= ? AND created_ts < ?',
                    (day_start, day_end))
                trend.append({'ts': day_start, 'count': cursor.fetchone()[0]})

            # 今日消息 & 活跃用户（近 24h 有消息的会话）
            cursor.execute('SELECT COUNT(*) FROM messages WHERE created_ts >= ?',
                           (now - 86400,))
            today_messages = cursor.fetchone()[0]
            cursor.execute('''
                SELECT COUNT(DISTINCT session_id) FROM messages WHERE created_ts >= ?
            ''', (now - 86400,))
            active_sessions = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'disabled'")
            disabled_users = cursor.fetchone()[0]

            return {
                'counts': stats,
                'trend_7d': trend,
                'today_messages': today_messages,
                'active_sessions_24h': active_sessions,
                'disabled_users': disabled_users,
                'db_type': getattr(self._backend, 'db_type', 'sqlite'),
            }

    # ========== Refresh Token 持久化方法 ==========
    
    @retry_on_locked(max_retries=3, delay=0.1)
    def save_refresh_token(self, user_id: int, token: str, jti: str, 
                           created_at: float, expires_at: float) -> bool:
        """
        保存 Refresh Token 到数据库。
        【多端在线 20260930】不再撤销该用户的旧 token：旧版「登录即撤销全部旧
        refresh token」的单会话设计与多端在线（手机+PC 同时登录）直接冲突——
        任一端重新登录都会废掉另一端的续期能力，access token 一过期（默认 1 小时）
        另一端就陷入 4001 断线风暴、被迫重新登录。
        安全边界不变：登出/修改密码仍撤销，token 本身 7 天过期。
        """
        with self._transaction():
            # 插入新 token
            self._conn.execute('''
                INSERT INTO refresh_tokens (user_id, token, jti, created_at, expires_at, revoked)
                VALUES (?, ?, ?, ?, ?, 0)
            ''', (user_id, token, jti, created_at, expires_at))
            
            _log.info(f"[DB] Refresh Token 已保存, 用户ID: {user_id}")
            return True

    def get_refresh_token(self, token: str) -> dict | None:
        """
        验证并获取 Refresh Token 信息。
        返回 token 详情或 None（若已撤销/过期/不存在）。
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT id, user_id, token, jti, created_at, expires_at, revoked
                FROM refresh_tokens 
                WHERE token = ?
            ''', (token,))
            row = cursor.fetchone()
            
            if not row:
                return None
            
            # 检查是否已撤销
            if row[6]:  # revoked
                _log.warning("[DB] Refresh Token 已被撤销")
                return None
            
            # 检查是否过期
            if row[5] < time.time():  # expires_at
                _log.warning("[DB] Refresh Token 已过期")
                # 自动清理过期 token
                self._cleanup_expired_refresh_tokens()
                return None
            
            return {
                'id': row[0],
                'user_id': row[1],
                'token': row[2],
                'jti': row[3],
                'created_at': row[4],
                'expires_at': row[5],
            }

    @retry_on_locked(max_retries=3, delay=0.1)
    def revoke_refresh_token(self, token: str) -> bool:
        """撤销指定的 Refresh Token"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE refresh_tokens SET revoked = 1 WHERE token = ?
            ''', (token,))
            self._conn.commit()
            revoked = cursor.rowcount > 0
            if revoked:
                _log.info("[DB] Refresh Token 已撤销")
            return revoked

    @retry_on_locked(max_retries=3, delay=0.1)
    def revoke_all_user_refresh_tokens(self, user_id: int) -> int:
        """撤销指定用户的所有 Refresh Token，返回撤销数量"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                UPDATE refresh_tokens SET revoked = 1 
                WHERE user_id = ? AND revoked = 0
            ''', (user_id,))
            self._conn.commit()
            count = cursor.rowcount
            _log.info(f"[DB] 用户 {user_id} 的 {count} 个 Refresh Token 已撤销")
            return count

    def _cleanup_expired_refresh_tokens(self) -> int:
        """清理过期的 Refresh Token，返回删除数量"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                DELETE FROM refresh_tokens 
                WHERE expires_at < ? OR revoked = 1
            ''', (time.time(),))
            self._conn.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                _log.info(f"[DB] 已清理 {deleted} 个过期/已撤销的 Refresh Token")
            return deleted

    def cleanup_stale_data(self, state_days: int = 7, emotion_days: int = 30) -> dict[str, int]:
        """数据生命周期清理：控制增长型流水表的体积。

        - emotional_states（情感快照）：保留最近 state_days 天
        - emotions（情感事件流水，当前只写不读）：保留最近 emotion_days 天
        - search_cache：惰性清理已有（查询时触发），此处兜底强刷一次
        - reminders（定时提醒）：已触发/已取消超过 reminder_days 天的清理

        由 server.py 每日后台任务调用，返回各表清理行数。
        """
        stats: dict[str, int] = {}
        now = time.time()
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute('DELETE FROM emotional_states WHERE timestamp < ?',
                               (now - state_days * 86400,))
                stats['emotional_states'] = cursor.rowcount
                cursor.execute('DELETE FROM emotions WHERE timestamp < ?',
                               (now - emotion_days * 86400,))
                stats['emotions'] = cursor.rowcount
                cursor.execute('DELETE FROM search_cache WHERE expires_at > 0 AND expires_at < ?',
                               (now,))
                stats['search_cache'] = cursor.rowcount
                cursor.execute('''
                    DELETE FROM reminders
                    WHERE status IN ('fired', 'cancelled')
                      AND COALESCE(NULLIF(fired_ts, 0), created_ts, 0) < ?
                ''', (now - 30 * 86400,))
                stats['reminders'] = cursor.rowcount
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
                _log.warning(f"[DB] 数据生命周期清理失败: {e}")
                return {}
            total = sum(stats.values())
            if total > 0:
                _log.info(f"[DB] 数据生命周期清理完成: {stats}（共 {total} 行）")
            return stats

    def cleanup_orphan_data(self) -> dict[str, int]:
        """孤儿数据清理：删除外键目标已不存在的行（防御异常路径产生的残留）。

        仅处理零风险两类（子表 → 父表 ID 确定性关联）：
        - messages → conversations（会话被删后残留的消息）
        - memory_tags → memories（记忆被删后残留的标签）

        注意：不自动清理"无主用户"的各业务表数据——用户删除已由
        delete_user 级联负责，此处避免误删 user_name 为空的历史全局数据。
        由 server.py 周期任务调用，幂等。
        """
        stats: dict[str, int] = {}
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute(
                    'DELETE FROM messages WHERE conversation_id NOT IN '
                    '(SELECT id FROM conversations)')
                stats['orphan_messages'] = cursor.rowcount
                cursor.execute(
                    'DELETE FROM memory_tags WHERE memory_id NOT IN '
                    '(SELECT id FROM memories)')
                stats['orphan_memory_tags'] = cursor.rowcount
                self._conn.commit()
            except Exception as e:
                self._conn.rollback()
                _log.warning(f"[DB] 孤儿数据清理失败: {e}")
                return {}
            total = sum(stats.values())
            if total > 0:
                _log.info(f"[DB] 孤儿数据清理完成: {stats}（共 {total} 行）")
            return stats

    def save_call_history(self, user_id: str, direction: str, status: str,
                          duration: int = 0, reason: str = '') -> None:
        """持久化一条通话记录（ended/missed/declined）。"""
        with self._lock:
            try:
                self._conn.execute(
                    'INSERT INTO call_history (user_id, direction, status, duration, reason, created_ts) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (str(user_id), direction, status, int(duration or 0), reason, time.time()))
                self._conn.commit()
            except Exception as e:
                _log.warning(f"保存通话历史失败: {e}")

    def get_call_history(self, user_id: str, limit: int = 20) -> list[dict]:
        """按用户查询通话历史（最新在前）。"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                'SELECT direction, status, duration, reason, created_ts '
                'FROM call_history WHERE user_id = ? ORDER BY created_ts DESC LIMIT ?',
                (str(user_id), int(limit)))
            return [
                {'direction': r[0], 'status': r[1], 'duration': r[2],
                 'reason': r[3], 'created_ts': r[4]}
                for r in cursor.fetchall()
            ]

    def get_stats(self) -> dict[str, int]:
        with self._lock:
            cursor = self._conn.cursor()
            stats = {}
            tables = ['stickers', 'conversations', 'messages', 'memories', 'knowledge', 
                      'growth', 'search_cache', 'learning', 'emotional_states', 'knowledge_triples',
                      'emotional_memories', 'autobiography', 'users']
            for table in tables:
                cursor.execute(f'SELECT COUNT(*) FROM {table}')
                stats[table] = cursor.fetchone()[0]
            return stats

    def get_message_count(self, user_id: str) -> int:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT COUNT(*) FROM messages 
                WHERE session_id LIKE ? ESCAPE '!'
            ''', (_user_session_like_pattern(user_id),))
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_memory_count(self, user_id: str) -> int:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM memories')
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_today_message_count(self) -> int:
        with self._lock:
            cursor = self._conn.cursor()
            today_start = time.time() - 86400
            cursor.execute('SELECT COUNT(*) FROM messages WHERE created_ts >= ?', (today_start,))
            row = cursor.fetchone()
            return row[0] if row else 0

    def add_feedback(self, user_id: str, content: str) -> bool:
        with self._lock:
            try:
                cursor = self._conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_ts REAL DEFAULT 0
                    )
                ''')
                cursor.execute('''
                    INSERT INTO feedback (user_id, content, created_ts)
                    VALUES (?, ?, ?)
                ''', (user_id, content, time.time()))
                self._conn.commit()
                return True
            except Exception as e:
                _log.warning(f"添加反馈失败: {e}")
                return False

    def migrate_from_json(self, json_dir: str = '.'):
        try:
            _log.info("开始从JSON迁移数据...")
            
            sticker_file = os.path.join(json_dir, 'miaoyi_stickers.json')
            if os.path.exists(sticker_file):
                try:
                    _log.info(f"正在迁移表情文件: {sticker_file}")
                    with open(sticker_file, encoding='utf-8') as f:
                        data = json.load(f)
                    count = 0
                    for sticker in data.get('user_stickers', []) + data.get('bot_stickers', []):
                        if isinstance(sticker, str):
                            filename = os.path.basename(sticker)
                            filepath = sticker if os.path.isabs(sticker) else os.path.join(
                                os.path.dirname(os.path.abspath(__file__)), 'assets', 'stickers_src', filename)
                            # 目录迁移期间兜底：新目录缺文件时回退旧的根目录名
                            if not os.path.exists(filepath) and not os.path.isabs(sticker):
                                filepath = os.path.join(
                                    os.path.dirname(os.path.abspath(__file__)), '表情包存放', filename)
                            if os.path.exists(filepath):
                                try:
                                    self.add_sticker(filename, filepath)
                                    count += 1
                                except Exception as se:
                                    _log.warning(f"添加表情失败: {filename}, 错误: {se}")
                    _log.info(f"成功迁移 {count} 个表情")
                except Exception as e:
                    _log.warning(f"表情文件迁移失败: {e}")
            else:
                _log.info(f"表情文件不存在: {sticker_file}")
            
            memory_file = os.path.join(json_dir, 'miaoyi_memory.json')
            if os.path.exists(memory_file):
                try:
                    _log.info(f"正在迁移记忆文件: {memory_file}")
                    with open(memory_file, encoding='utf-8') as f:
                        data = json.load(f)
                    count = 0
                    for item in data.get('memories', []):
                        try:
                            self.add_memory(item.get('key', ''), item.get('value', ''),
                                           category=item.get('category', ''),
                                           confidence=item.get('confidence', 0.8))
                            count += 1
                        except Exception as me:
                            _log.warning(f"添加记忆失败: {item.get('key', '')}, 错误: {me}")
                    _log.info(f"成功迁移 {count} 条记忆")
                except Exception as e:
                    _log.warning(f"记忆文件迁移失败: {e}")
            else:
                _log.info(f"记忆文件不存在: {memory_file}")
            
            knowledge_file = os.path.join(json_dir, 'miaoyi_knowledge_db.json')
            if os.path.exists(knowledge_file):
                try:
                    _log.info(f"正在迁移知识文件: {knowledge_file}")
                    with open(knowledge_file, encoding='utf-8') as f:
                        data = json.load(f)
                    count = 0
                    for item in data.get('knowledge', []):
                        try:
                            self.add_knowledge(item.get('topic', ''), item.get('content', ''),
                                               source=item.get('source', ''),
                                               quality=item.get('quality', 0.5))
                            count += 1
                        except Exception as ke:
                            _log.warning(f"添加知识失败: {item.get('topic', '')}, 错误: {ke}")
                    _log.info(f"成功迁移 {count} 条知识")
                except Exception as e:
                    _log.warning(f"知识文件迁移失败: {e}")
            else:
                _log.info(f"知识文件不存在: {knowledge_file}")
            
            _log.info("JSON数据迁移完成")
        except Exception as e:
            _log.warning(f"迁移过程异常: {e}")

    def _refresh_mysql_snapshot(self):
        """【L1 修复】结束 MySQL 隐式读事务，刷新 REPEATABLE READ 快照。

        pymysql autocommit=False 下，SELECT 会开启隐式事务且不会自动结束，
        后续读永远命中旧快照（"刚写入的记忆要等下一次写事务后才可见"）。
        读前 commit 一次即可结束旧快照（无未提交写时 commit 是无害的 no-op）。
        """
        if self.db_type == 'mysql':
            try:
                self._conn.commit()
            except Exception:
                pass

    def get_memories(self, limit: int = 50, category: str = '', user_id: str = '') -> list[dict]:
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            query = ('SELECT id, key, value, category, confidence, source, '
                     'usage_count, last_access_ts, created_ts, expires_at, importance, pinned '
                     'FROM memories WHERE 1=1')
            params = []

            if user_id:
                # 用户专属记忆 + 全局共享记忆（user_id=''）都可见
                query += ' AND (user_id = ? OR user_id = ?)'
                params.extend([user_id, ''])

            if category:
                query += ' AND category LIKE ?'
                params.append(f'%{category}%')

            # 【L1 记忆卡片流】钉住的记忆置顶展示
            query += ' ORDER BY pinned DESC, last_access_ts DESC, created_ts DESC LIMIT ?'
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'key': row[1], 'value': row[2],
                    'category': row[3], 'confidence': row[4], 'source': row[5],
                    'usage_count': row[6], 'last_access_ts': row[7],
                    'created_ts': row[8], 'expires_at': row[9],
                    'importance': row[10] if row[10] is not None else 0.5,
                    'pinned': bool(row[11]) if row[11] is not None else False,
                })
            return result

    @retry_on_locked(max_retries=3, delay=0.1)
    def save_semantic_fact(self, key: str, fact: dict, user_id: str = ''):
        """保存语义记忆事实 - 供统一记忆系统使用

        Args:
            key: 事实键
            fact: 事实数据字典 (包含 value, category, confidence 等)
            user_id: 用户ID，空字符串表示全局共享记忆
        """
        value = fact.get('value', '')
        category = fact.get('category', 'fact')
        confidence = fact.get('confidence', 0.8)
        source = fact.get('source', 'memory_system')
        importance = fact.get('importance', 0.5)
        strength = fact.get('strength', 0.5)

        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO memories
                (user_id, key, value, category, confidence, source, importance, strength,
                 usage_count, last_access_ts, created_ts, pinned, emb_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                str(user_id), key, str(value), category, confidence, source,
                importance, strength,
                fact.get('access_count', 0),
                time.time(),
                fact.get('created_at', time.time()),
                1 if fact.get('pinned') else 0,
                json.dumps(fact['emb']) if fact.get('emb') else '',
            ))

    @retry_on_locked(max_retries=3, delay=0.1)
    def update_semantic_fact_value(self, key: str, value: str, user_id: str = '') -> bool:
        """按 user_id + key 编辑语义记忆内容 — 供 SemanticMemory.update_value 双写使用

        Returns:
            bool: 是否实际更新了至少一行
        """
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE memories SET value = ?, last_access_ts = ? '
                           'WHERE user_id = ? AND key = ?',
                           (str(value), time.time(), str(user_id), key))
            return cursor.rowcount > 0

    @retry_on_locked(max_retries=3, delay=0.1)
    def update_semantic_fact_pinned(self, key: str, pinned: bool, user_id: str = '') -> bool:
        """按 user_id + key 设置语义记忆钉住 — 供 SemanticMemory.set_pinned 双写使用

        Returns:
            bool: 是否实际更新了至少一行
        """
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE memories SET pinned = ? WHERE user_id = ? AND key = ?',
                           (1 if pinned else 0, str(user_id), key))
            return cursor.rowcount > 0

    def delete_semantic_fact(self, key: str, user_id: str = '') -> bool:
        """删除语义记忆事实（按 user_id + key 精确删除）— 供记忆冲突淘汰使用

        Args:
            key: 事实键
            user_id: 用户ID，空字符串表示全局共享记忆
        """
        with self._transaction() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM memories WHERE user_id = ? AND key = ?',
                           (str(user_id), key))
            return cursor.rowcount > 0

    def get_semantic_facts(self, user_id: str = None) -> dict[str, dict]:
        """获取语义记忆事实 - 供统一记忆系统使用

        Args:
            user_id: 用户ID。None 表示加载所有用户的事实（启动时全量加载）；
                     传具体用户ID（含空字符串）则只返回该用户的记忆。

        Returns:
            Dict[str, Dict]: 以 key 为索引的事实字典，包含统一字段名
        """
        with self._lock:
            cursor = self._conn.cursor()
            params = []
            sql = (
                "SELECT id, key, value, confidence, source, category, "
                "importance, strength, usage_count, created_ts, user_id, pinned, emb_json "
                "FROM memories WHERE confidence >= 0.5 "
            )
            if user_id is not None:
                sql += "AND user_id = ? "
                params.append(str(user_id))
            sql += "ORDER BY confidence DESC, usage_count DESC LIMIT 1000"
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            result = {}
            for row in rows:
                key = row[1]  # key 字段
                if key:
                    # emb_json：语义向量（JSON 数组），解析失败留空走关键词回退
                    emb = None
                    if row[12]:
                        try:
                            emb = json.loads(row[12])
                        except (ValueError, TypeError):
                            emb = None
                    result[key] = {
                        'key': key,
                        'value': row[2],
                        'category': row[5] or 'fact',
                        'confidence': row[3] or 0.5,
                        'source': row[4] or '',
                        'importance': row[6] or 0.5,
                        'strength': row[7] or 0.5,
                        'access_count': row[8] or 0,
                        'created_at': row[9] or time.time(),
                        'updated_at': time.time(),
                        'user_id': row[10] if row[10] is not None else '',
                        'pinned': bool(row[11]) if row[11] is not None else False,
                        'emb': emb,
                    }
            return result

    def get_episodes(self, limit: int = 500) -> list[dict]:
        """获取情景记忆片段 - 供统一记忆系统使用"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT id, session_id, role, text, emotion, confidence, created_ts "
                "FROM messages WHERE text IS NOT NULL AND text != '' "
                "ORDER BY created_ts DESC LIMIT ?",
                (limit,)
            )
            rows = cursor.fetchall()
            return [
                {
                    'id': row[0], 'session_id': row[1], 'role': row[2],
                    'content': row[3], 'emotion': row[4],
                    'confidence': row[5], 'timestamp': row[6]
                }
                for row in rows
            ]

    def save_memory(self, key: str, value: str, user_id: str = ''):
        """保存或更新一条用户记忆（user_id 为空表示全局共享记忆）"""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO memories (user_id, key, value, created_ts) VALUES (?, ?, ?, ?)",
                (user_id, key, value, time.time()))
            self._conn.commit()

    @retry_on_locked(max_retries=3, delay=0.1)
    def delete_memory(self, key: str, user_id: str = '') -> bool:
        """删除一条用户记忆（仅删除该用户专属记忆；user_id 为空时删除全局共享记忆）

        Returns:
            bool: 是否实际删除了至少一行（server.py 依此返回 200/404）

        【历史教训】旧版无返回值（None），而 server.py 用返回值判断成功，
        导致 DELETE /api/memory/{key} 永远返回 404"记忆不存在"。
        """
        with self._lock:
            # 【兼容】TranslatedConnection.execute 在 MySQL 下返回 int（pymysql rowcount），
            # 必须经 cursor() 执行才能拿到带 rowcount 的游标
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM memories WHERE key = ? AND (user_id = ? OR user_id = ?)",
                (key, user_id, ''))
            self._conn.commit()
            return cursor.rowcount > 0

    @retry_on_locked(max_retries=3, delay=0.1)
    def delete_memory_by_id(self, memory_id: int, user_id: str = '') -> bool:
        """按行 id 精确删除记忆（用户专属与全局共享均允许删除）

        前端列表以数字行 id 为主键；按 id 删除比按 key 更精确，
        也不会误删其他用户/重复 key 的行。

        Returns:
            bool: 是否实际删除了至少一行
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM memories WHERE id = ? AND (user_id = ? OR user_id = ?)",
                (memory_id, user_id, ''))
            self._conn.commit()
            return cursor.rowcount > 0

    def clear_all_memories(self, user_id: str) -> int:
        """清空指定用户的全部【专属】记忆（不影响其他用户与全局共享记忆）。

        同步删除 memory_tags 中指向这些记忆的标签，返回删除条数。
        【MySQL 兼容】子查询跨表（memory_tags→memories），不受 1093 限制。
        """
        if not user_id:
            return 0
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM memory_tags WHERE memory_id IN "
                "(SELECT id FROM memories WHERE user_id = ?)",
                (str(user_id),))
            cursor.execute(
                "DELETE FROM memories WHERE user_id = ?", (str(user_id),))
            deleted = cursor.rowcount
            self._conn.commit()
            return deleted

    def clear_memories_before(self, user_id: str, days: int) -> int:
        """删除指定用户 N 天前的专属记忆（同步清标签），返回删除条数。"""
        if not user_id:
            return 0
        cutoff = time.time() - max(1, int(days)) * 86400
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "DELETE FROM memory_tags WHERE memory_id IN "
                "(SELECT id FROM memories WHERE user_id = ? AND created_ts < ?)",
                (str(user_id), cutoff))
            cursor.execute(
                "DELETE FROM memories WHERE user_id = ? AND created_ts < ?",
                (str(user_id), cutoff))
            deleted = cursor.rowcount
            self._conn.commit()
            return deleted

    def get_memory_by_id(self, memory_id: int, user_id: str = '') -> dict | None:
        """按行 id 查询单条记忆（供 server 编辑/钉住路由回查 key/user_id 以同步内存缓存）

        Returns:
            dict: 记忆行数据（含 id/key/value/user_id/pinned 等）；不存在或不属于该用户返回 None
        """
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT id, key, value, category, confidence, importance, pinned, user_id "
                "FROM memories WHERE id = ? AND (user_id = ? OR user_id = ?)",
                (memory_id, user_id, ''))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                'id': row[0], 'key': row[1], 'value': row[2],
                'category': row[3], 'confidence': row[4], 'importance': row[5],
                'pinned': bool(row[6]) if row[6] is not None else False,
                'user_id': row[7] if row[7] is not None else '',
            }

    @retry_on_locked(max_retries=3, delay=0.1)
    def update_memory_value(self, memory_id: int, value: str, user_id: str = '') -> bool:
        """按行 id 编辑记忆内容（L1 记忆卡片流）

        Returns:
            bool: 是否实际更新了至少一行
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "UPDATE memories SET value = ?, last_access_ts = ? "
                "WHERE id = ? AND (user_id = ? OR user_id = ?)",
                (str(value), time.time(), memory_id, user_id, ''))
            self._conn.commit()
            return cursor.rowcount > 0

    @retry_on_locked(max_retries=3, delay=0.1)
    def set_memory_pinned(self, memory_id: int, pinned: bool, user_id: str = '') -> bool:
        """按行 id 设置/取消钉住（L1 记忆卡片流）

        钉住的记忆在前端置顶展示，且在 AI 召回中豁免退休与冷却、优先注入。

        Returns:
            bool: 是否实际更新了至少一行
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "UPDATE memories SET pinned = ? WHERE id = ? AND (user_id = ? OR user_id = ?)",
                (1 if pinned else 0, memory_id, user_id, ''))
            self._conn.commit()
            return cursor.rowcount > 0

    def search_memories(self, query: str, limit: int = 20, user_id: str = '') -> list[dict]:
        """模糊搜索记忆，支持搜索 key 和 value；user_id 过滤（含全局共享记忆）"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            query_pattern = f'%{query}%'
            # 【bug修复】原 SELECT * + 位置索引映射：MySQL 表含 user_id/importance 等额外列，
            # 列顺序与 SQLite 不同导致 value/category 全部错位。改为显式列名。
            cursor.execute('''
                SELECT id, key, value, category, confidence, source,
                usage_count, last_access_ts, created_ts, expires_at
                FROM memories
                WHERE (key LIKE ? OR value LIKE ? OR category LIKE ?)
                AND (user_id = ? OR user_id = ?)
                ORDER BY last_access_ts DESC, created_ts DESC
                LIMIT ?
            ''', (query_pattern, query_pattern, query_pattern, user_id, '', limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'key': row[1], 'value': row[2],
                    'category': row[3], 'confidence': row[4], 'source': row[5],
                    'usage_count': row[6], 'last_access_ts': row[7],
                    'created_ts': row[8], 'expires_at': row[9]
                })
            return result

    def get_memory_categories(self) -> list[str]:
        """获取所有记忆分类/标签"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('SELECT DISTINCT category FROM memories WHERE category IS NOT NULL AND category != ""')
            rows = cursor.fetchall()
            return [row[0] for row in rows]

    def save_memory_with_tags(self, key: str, value: str, tags: list[str] = None, category: str = '', user_id: str = ''):
        """保存记忆并支持标签"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO memories 
                (user_id, key, value, category, created_ts, last_access_ts) 
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, key, value, category or '', time.time(), time.time()))
            self._conn.commit()
            
            if tags and len(tags) > 0:
                cursor.execute('SELECT id FROM memories WHERE key = ? AND user_id = ?', (key, user_id))
                row = cursor.fetchone()
                if row:
                    memory_id = row[0]
                    for tag in tags:
                        tag = tag.strip()
                        if tag:
                            cursor.execute('''
                                INSERT OR IGNORE INTO memory_tags (memory_id, tag, created_ts)
                                VALUES (?, ?, ?)
                            ''', (memory_id, tag, time.time()))
                    self._conn.commit()

    def get_memories_by_tag(self, tag: str, limit: int = 20, user_id: str = '') -> list[dict]:
        """按标签获取记忆（user_id 过滤，含全局共享记忆）"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                SELECT m.* FROM memories m
                JOIN memory_tags mt ON m.id = mt.memory_id
                WHERE mt.tag = ? AND (m.user_id = ? OR m.user_id = ?)
                ORDER BY m.last_access_ts DESC, m.created_ts DESC
                LIMIT ?
            ''', (tag, user_id, '', limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'key': row[1], 'value': row[2],
                    'category': row[3], 'confidence': row[4], 'source': row[5],
                    'usage_count': row[6], 'last_access_ts': row[7],
                    'created_ts': row[8], 'expires_at': row[9]
                })
            return result

    def get_memory_tags(self, memory_id: int = None) -> list[str]:
        """获取记忆标签，不带参数时获取所有标签"""
        with self._lock:
            cursor = self._conn.cursor()
            if memory_id:
                cursor.execute('SELECT tag FROM memory_tags WHERE memory_id = ?', (memory_id,))
            else:
                cursor.execute('SELECT tag FROM memory_tags GROUP BY tag ORDER BY MAX(created_ts) DESC')
            rows = cursor.fetchall()
            return [row[0] for row in rows]

    def add_memory_tag(self, memory_id: int, tag: str):
        """为记忆添加标签"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO memory_tags (memory_id, tag, created_ts)
                VALUES (?, ?, ?)
            ''', (memory_id, tag.strip(), time.time()))
            self._conn.commit()

    def remove_memory_tag(self, memory_id: int, tag: str):
        """移除记忆标签"""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('DELETE FROM memory_tags WHERE memory_id = ? AND tag = ?', (memory_id, tag.strip()))
            self._conn.commit()

    def save_conversation_summary(self, user_text: str, bot_text: str, intent: str, emotion: str = '', timestamp: float = None):
        if timestamp is None:
            timestamp = time.time()
        with self._lock:
            try:
                cursor = self._conn.cursor()
                
                summary_text = f"User: {user_text} | Bot: {bot_text} | Intent: {intent}"
                
                # 使用 user_id + 时间戳生成唯一 session_id，避免冲突
                session_id = f"summary_{int(timestamp)}_{hash(user_text) & 0xFFFF}"
                
                cursor.execute('SELECT id FROM conversations WHERE session_id = ?', (session_id,))
                conv_row = cursor.fetchone()
                if conv_row:
                    conv_id = conv_row[0]
                else:
                    cursor.execute('''
                        INSERT INTO conversations (session_id, start_ts, last_active_ts, message_count)
                        VALUES (?, ?, ?, 0)
                    ''', (session_id, timestamp, timestamp))
                    conv_id = cursor.lastrowid
                
                # role 根据调用上下文动态设置，默认 'assistant'
                role = 'assistant'
                
                cursor.execute('''
                    INSERT INTO messages 
                    (conversation_id, session_id, role, text, emotion, created_ts)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (conv_id, session_id, role, summary_text, emotion, timestamp))
                
                self._conn.commit()
                return True
            except Exception as e:
                _log.warning(f"保存对话摘要失败: {e}")
                return False

    def get_conversation_summaries(self, session_id: str = '', limit: int = 20) -> list[dict]:
        with self._lock:
            cursor = self._conn.cursor()
            if session_id:
                cursor.execute('''
                    SELECT m.* FROM messages m
                    JOIN conversations c ON m.conversation_id = c.id
                    WHERE c.session_id = ? AND m.role = 'system'
                    ORDER BY m.created_ts DESC LIMIT ?
                ''', (session_id, limit))
            else:
                cursor.execute('''
                    SELECT m.* FROM messages m
                    WHERE m.role = 'system'
                    ORDER BY m.created_ts DESC LIMIT ?
                ''', (limit,))
            
            rows = cursor.fetchall()
            result = []
            for row in rows:
                result.append({
                    'id': row[0], 'conversation_id': row[1], 'session_id': row[2],
                    'role': row[3], 'text': row[4], 'sticker_path': row[5],
                    'sticker_meaning': row[6], 'emotion': row[7], 'confidence': row[8],
                    'response_time_ms': row[9], 'created_ts': row[10], 'image_url': row[11] if len(row) > 11 else ''
                })
            return result[::-1]

    def checkin_user(self, user_id: str) -> dict[str, int]:
        with self._lock:
            today = datetime.now().strftime('%Y-%m-%d')
            cursor = self._conn.cursor()
            cursor.execute('SELECT last_checkin_ts, streak, level, exp, skill_points, total_interactions, created_ts FROM growth WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()

            streak = 0
            # 旧记录缺字段时的默认值（新行同样用这套）
            _now = time.time()
            level = 1
            exp = 0
            skill_points = 0
            total_interactions = 0
            created_ts = _now
            if row:
                last_checkin_ts = row[0]
                streak = row[1] if row[1] else 0
                level = row[2] or 1
                exp = row[3] or 0
                skill_points = row[4] or 0
                total_interactions = row[5] or 0
                created_ts = row[6] or _now
                
                if last_checkin_ts:
                    last_checkin_date = datetime.fromtimestamp(last_checkin_ts).strftime('%Y-%m-%d')
                    if last_checkin_date == today:
                        return {'streak': streak, 'already_checked': True}
                    
                    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
                    if last_checkin_date == yesterday:
                        streak += 1
                    else:
                        streak = 1
            else:
                streak = 1
            
            # 【2026-10 修复 MySQL 1093】不能在 INSERT 目标表上做同表子查询；
            # 所需字段已在上面 SELECT 取出、Python 算好；INSERT OR REPLACE 会由
            # db_backend 转成 MySQL REPLACE INTO，两种库通用
            cursor.execute('''
                INSERT OR REPLACE INTO growth (user_id, level, exp, skill_points, last_checkin_ts, total_interactions, created_ts, streak)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, level, exp + 10, skill_points, _now, total_interactions + 1, created_ts, streak))
            self._conn.commit()
            
            return {'streak': streak, 'already_checked': False}

    def get_checkin_status(self, user_id: str) -> dict:
        """【L6 纪念卡片】查询签到状态（连续天数/今天是否已签，不写入）"""
        self._refresh_mysql_snapshot()
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute('SELECT last_checkin_ts, streak FROM growth WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            if not row:
                return {'streak': 0, 'checked_today': False}
            streak = row[1] or 0
            checked_today = False
            if row[0]:
                checked_today = datetime.fromtimestamp(row[0]).strftime('%Y-%m-%d') == datetime.now().strftime('%Y-%m-%d')
            return {'streak': streak, 'checked_today': checked_today}

    def close(self):
        """关闭数据库连接（SQLite 先执行 WAL checkpoint 确保数据安全）"""
        if self._conn:
            try:
                # WAL checkpoint 仅 SQLite 需要，MySQL 由后端自动跳过
                self._backend.checkpoint(self._conn._raw)
                self._conn.commit()
            except Exception as e:
                _log.warning(f"WAL checkpoint 失败: {e}")
            finally:
                self._conn.close()
                self._conn = None


if __name__ == '__main__':
    db = DBManager()
    print("数据库管理器测试")
    print(db.get_stats())
    
    db.add_memory('测试', '测试值')
    print(db.get_memory('测试'))
    
    db.vacuum()
    print(db.get_stats())
    
    db.close()