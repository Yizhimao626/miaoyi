"""应用状态共享模块"""

DB_INSTANCE = None
CORE_INSTANCE = None
VOICE_MANAGER = None

# 商业级增强模块实例
EMOTION_ENGINE = None   # 四维情感引擎
EMOTION_PERSISTENCE = None  # 情感持久化（data/emotion.db）
MEMORY_RAG = None       # 四层记忆 + RAG
WEB_CRAWLER = None      # 多源网络爬虫
CHAT_ENGINE = None      # 增强对话引擎
CALL_MANAGER = None     # 通话状态机
PROACTIVE_ENGINE = None # 主动行为引擎
MESSAGE_SERVICE = None  # 【分层】消息业务服务（services/message_service.py）
# 【P1-2】已移除 DB_MANAGER（从未赋值，与 DB_INSTANCE 重复）。数据库实例统一用 DB_INSTANCE。
START_TIME = 0.0        # 服务启动时间戳
EXPORTS_DIR = None      # 聊天导出目录（server.py 启动时赋值，供 /api/files/* 鉴权下载路由使用）
UPLOADS_DIR = None      # 用户上传目录（同上）
