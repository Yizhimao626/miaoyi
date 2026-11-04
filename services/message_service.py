"""
喵艺 · 消息服务层（Service Layer）
------------------------------------------------------------
方案第十一部分：后端分层 Router → Service → Core → DB

本模块封装"消息收发 + 持久化"业务逻辑，路由（server.py）只做
HTTP/WS 解析与响应，业务规则与数据落地集中在 Service 层，
便于复用与测试，避免业务逻辑散落在路由里。
"""
import logging

_log = logging.getLogger("MiaoyiService")

# 会话 ID 约定：每个用户一个默认会话（与前端 SessionList 一致）
def default_session_id(user_id: str) -> str:
    return f"{user_id}_default"


class MessageService:
    """消息业务服务：统一处理聊天消息的持久化与查询。"""

    def __init__(self, db):
        self._db = db

    def save_user_message(self, user_id: str, text: str, session_id: str = "",
                          image_url: str = "") -> None:
        """持久化用户消息（自动创建/更新会话）。image_url：视觉消息附图。"""
        try:
            sid = session_id or default_session_id(user_id)
            self._db.add_message(sid, 'user', text, emotion='', response_time_ms=0,
                                 user_name=str(user_id), image_url=image_url or '')
        except Exception as e:
            _log.error(f"保存用户消息失败: {e}")

    def save_ai_message(self, user_id: str, text: str, emotion: str = "",
                        response_time_ms: int = 0, session_id: str = "") -> None:
        """持久化 AI 回复（自动创建/更新会话）。"""
        try:
            sid = session_id or default_session_id(user_id)
            self._db.add_message(sid, 'assistant', text, emotion=str(emotion),
                                 response_time_ms=int(response_time_ms or 0), user_name=str(user_id))
        except Exception as e:
            _log.error(f"保存AI回复失败: {e}")

    def save_message_pair(self, user_id: str, user_text: str, ai_text: str,
                          emotion: str = "", response_time_ms: int = 0,
                          session_id: str = "") -> None:
        """原子保存一对消息（用户 + AI），REST 路径使用。"""
        self.save_user_message(user_id, user_text, session_id)
        self.save_ai_message(user_id, ai_text, emotion, response_time_ms, session_id)

    def save_proactive_message(self, user_id: str, text: str, msg_type: str = "") -> None:
        """持久化主动推送消息（主动关怀/定时提醒等），刷新后历史可恢复。"""
        try:
            sid = default_session_id(user_id)
            self._db.add_message(sid, 'assistant', text, emotion=str(msg_type or 'proactive'),
                                 response_time_ms=0, user_name=str(user_id))
        except Exception as e:
            _log.error(f"保存主动消息失败: {e}")

    def get_messages(self, user_id: str, limit: int = 50, session_id: str = "") -> list:
        """读取某用户会话的历史消息（刷新恢复用）。"""
        try:
            sid = session_id or default_session_id(user_id)
            return self._db.get_messages(sid, limit=limit)
        except Exception as e:
            _log.error(f"读取历史消息失败: {e}")
            return []

    def get_session_summary(self, user_id: str, limit: int = 20) -> list:
        """读取会话列表（多用户隔离）。"""
        try:
            return self._db.get_conversations(str(user_id), limit=limit)
        except Exception as e:
            _log.error(f"读取会话列表失败: {e}")
            return []
