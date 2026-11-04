"""
喵艺 AI 伴侣 · Token 配额管理
==============================
职责：
  - Token 估算（中文约1.5字/token，英文约4字符/token）
  - 配额检查（免费用户 50000/天，VIP 500000/天）
  - 每日重置（自动检测日期变更）
  - 使用记录（prompt/completion 分项）
"""

import json
import logging
import os
import time
from typing import Any, Dict, Optional

_log = logging.getLogger("TokenCounter")


# ============================================================
# 默认配额
# ============================================================

DEFAULT_DAILY_LIMIT_FREE = 50000
DEFAULT_DAILY_LIMIT_VIP = 500000


def estimate_tokens(text: str) -> int:
    """
    估算文本的 Token 数量

    规则：
      - 中文：约 1.5 字 / token
      - 英文：约 4 字符 / token
      - 混合：按字符类型分别计算

    Args:
        text: 输入文本

    Returns:
        int: 估算的 Token 数
    """
    if not text:
        return 0

    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars

    # 中文 1.5 字/token，其他 4 字符/token
    tokens = chinese_chars / 1.5 + other_chars / 4.0
    return max(1, int(tokens))


# ============================================================
# Token 计数器
# ============================================================

class TokenCounter:
    """
    Token 配额管理器

    特性：
      - 按用户独立计数
      - 每日自动重置
      - 支持免费/VIP 两种配额
      - 记录 prompt/completion 分项
    """

    def __init__(
        self,
        db_path: str = "data/token_usage.json",
        daily_limit_free: int = DEFAULT_DAILY_LIMIT_FREE,
        daily_limit_vip: int = DEFAULT_DAILY_LIMIT_VIP,
    ):
        self._db_path = db_path
        self._daily_limit_free = daily_limit_free
        self._daily_limit_vip = daily_limit_vip
        self._usage: dict[str, dict[str, Any]] = {}
        self._load()

    # ----------------------------------------------------------
    # 公共 API
    # ----------------------------------------------------------

    def check_quota(self, user_id: str, is_vip: bool = False) -> dict[str, Any]:
        """
        检查用户配额

        Args:
            user_id: 用户 ID
            is_vip: 是否 VIP

        Returns:
            dict: {allowed, remaining, limit, used_today}
        """
        self._maybe_reset(user_id)
        self._ensure_user(user_id)

        limit = self._daily_limit_vip if is_vip else self._daily_limit_free
        used = self._usage[user_id]["used"]
        remaining = max(0, limit - used)

        return {
            "allowed": remaining > 0,
            "remaining": remaining,
            "limit": limit,
            "used_today": used,
            "is_vip": is_vip,
        }

    def consume(
        self,
        user_id: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> dict[str, Any]:
        """
        消耗 Token

        Args:
            user_id: 用户 ID
            prompt_tokens: prompt Token 数
            completion_tokens: completion Token 数

        Returns:
            dict: {success, remaining, limit, used_today}
        """
        self._maybe_reset(user_id)
        self._ensure_user(user_id)

        total = prompt_tokens + completion_tokens
        usage = self._usage[user_id]
        is_vip = usage.get("is_vip", False)
        limit = self._daily_limit_vip if is_vip else self._daily_limit_free

        if usage["used"] + total > limit:
            _log.warning(f"Token 超额: user={user_id}, used={usage['used']}, limit={limit}")
            return {
                "success": False,
                "remaining": limit - usage["used"],
                "limit": limit,
                "used_today": usage["used"],
            }

        usage["used"] += total
        usage["prompt"] += prompt_tokens
        usage["completion"] += completion_tokens
        usage["last_updated"] = time.time()

        self._save()

        remaining = limit - usage["used"]
        _log.debug(
            f"Token 消耗: user={user_id}, prompt={prompt_tokens}, "
            f"completion={completion_tokens}, remaining={remaining}"
        )

        return {
            "success": True,
            "remaining": remaining,
            "limit": limit,
            "used_today": usage["used"],
        }

    def get_usage(self, user_id: str) -> dict[str, Any]:
        """获取用户使用统计"""
        self._ensure_user(user_id)
        usage = self._usage[user_id]
        return {
            "date": usage["date"],
            "used": usage["used"],
            "prompt": usage["prompt"],
            "completion": usage["completion"],
            "is_vip": usage.get("is_vip", False),
        }

    def set_vip(self, user_id: str, is_vip: bool = True):
        """设置用户 VIP 状态"""
        self._ensure_user(user_id)
        self._usage[user_id]["is_vip"] = is_vip
        self._save()
        _log.info(f"VIP 状态更新: user={user_id}, vip={is_vip}")

    def get_total_stats(self) -> dict[str, Any]:
        """获取全局统计"""
        total_users = len(self._usage)
        total_used = sum(u["used"] for u in self._usage.values())
        total_prompt = sum(u["prompt"] for u in self._usage.values())
        total_completion = sum(u["completion"] for u in self._usage.values())

        return {
            "total_users": total_users,
            "total_used": total_used,
            "total_prompt": total_prompt,
            "total_completion": total_completion,
            "date": self._today_str(),
        }

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _ensure_user(self, user_id: str):
        """确保用户记录存在"""
        if user_id not in self._usage:
            self._usage[user_id] = {
                "date": self._today_str(),
                "used": 0,
                "prompt": 0,
                "completion": 0,
                "is_vip": False,
                "last_updated": time.time(),
            }

    def _maybe_reset(self, user_id: str):
        """检查并执行每日重置"""
        self._ensure_user(user_id)
        if self._usage[user_id]["date"] != self._today_str():
            _log.info(f"每日重置: user={user_id}")
            is_vip = self._usage[user_id].get("is_vip", False)
            self._usage[user_id] = {
                "date": self._today_str(),
                "used": 0,
                "prompt": 0,
                "completion": 0,
                "is_vip": is_vip,
                "last_updated": time.time(),
            }
            self._save()

    def _today_str(self) -> str:
        import datetime
        return datetime.date.today().isoformat()

    def _load(self):
        """从文件加载"""
        try:
            if os.path.exists(self._db_path):
                with open(self._db_path, encoding='utf-8') as f:
                    self._usage = json.load(f)
                _log.info(f"Token 使用记录已加载: {len(self._usage)} 用户")
        except Exception as e:
            _log.warning(f"加载 Token 记录失败: {e}")
            self._usage = {}

    def _save(self):
        """保存到文件"""
        try:
            os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
            with open(self._db_path, 'w', encoding='utf-8') as f:
                json.dump(self._usage, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log.error(f"保存 Token 记录失败: {e}")


# ============================================================
# 便捷函数
# ============================================================

_global_counter: TokenCounter | None = None


def get_counter() -> TokenCounter:
    """获取全局 TokenCounter 实例"""
    global _global_counter
    if _global_counter is None:
        _global_counter = TokenCounter()
    return _global_counter
