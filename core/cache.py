"""
喵艺 AI 伴侣 · 缓存模块
==============================
职责：
  - 爬虫 TTL 缓存实现
  - 支持多源缓存隔离
  - 线程安全

设计：
  - _MemoryCache: 简单的内存 TTL 缓存
  - 支持字符串 key 和字典值
  - 自动过期清理
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

_log = logging.getLogger("Cache")


class _CacheEntry:
    """缓存条目"""
    __slots__ = ("value", "expire_at", "created_at")

    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.expire_at = time.time() + ttl
        self.created_at = time.time()

    @property
    def expired(self) -> bool:
        return time.time() > self.expire_at


class Cache:
    """
    内存 TTL 缓存

    特性：
      - 线程安全（使用 RLock）
      - 自动过期清理
      - 最大容量限制
      - 支持命名空间（用于不同数据源隔离）
    """

    def __init__(
        self,
        default_ttl: int = 300,
        max_size: int = 1000,
        cleanup_interval: int = 60,
    ):
        """
        Args:
            default_ttl: 默认过期时间（秒）
            max_size: 最大缓存条目数
            cleanup_interval: 自动清理间隔（秒）
        """
        self._default_ttl = default_ttl
        self._max_size = max_size
        self._cleanup_interval = cleanup_interval
        self._store: dict[str, _CacheEntry] = {}
        self._lock = threading.RLock()
        self._last_cleanup = time.time()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        """
        获取缓存值

        Args:
            key: 缓存键

        Returns:
            缓存值或 None
        """
        with self._lock:
            # 自动清理
            self._maybe_cleanup()

            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None

            if entry.expired:
                del self._store[key]
                self._misses += 1
                return None

            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """
        设置缓存值

        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），None 则使用默认
        """
        if ttl is None:
            ttl = self._default_ttl

        with self._lock:
            # 容量检查
            if len(self._store) >= self._max_size and key not in self._store:
                self._evict_one()

            self._store[key] = _CacheEntry(value, ttl)

    def delete(self, key: str) -> bool:
        """
        删除缓存

        Args:
            key: 缓存键

        Returns:
            bool: 是否删除成功
        """
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0
        _log.info("缓存已清空")

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0

            # 统计过期条目
            expired_count = sum(1 for e in self._store.values() if e.expired)

            return {
                "size": len(self._store),
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": f"{hit_rate:.1%}",
                "expired": expired_count,
            }

    def namespace(self, prefix: str) -> "NamespacedCache":
        """
        创建命名空间缓存视图

        Args:
            prefix: 命名空间前缀

        Returns:
            NamespacedCache
        """
        return NamespacedCache(self, prefix)

    def shutdown(self) -> None:
        """关闭缓存（清理资源）"""
        self.clear()
        _log.info("缓存已关闭")

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _maybe_cleanup(self) -> None:
        """可能执行自动清理"""
        now = time.time()
        if now - self._last_cleanup >= self._cleanup_interval:
            self._do_cleanup()
            self._last_cleanup = now

    def _do_cleanup(self) -> None:
        """执行清理"""
        expired_keys = [
            k for k, v in self._store.items() if v.expired
        ]
        for k in expired_keys:
            del self._store[k]

        if expired_keys:
            _log.debug(f"清理过期缓存: {len(expired_keys)} 条")

    def _evict_one(self) -> None:
        """淘汰一个条目（LRU 近似：删除最早创建的）"""
        if not self._store:
            return

        # 找最早的
        oldest_key = min(
            self._store.keys(),
            key=lambda k: self._store[k].created_at,
        )
        del self._store[oldest_key]
        _log.debug(f"缓存淘汰: key={oldest_key}")


class NamespacedCache:
    """
    命名空间缓存视图

    自动为所有 key 添加前缀，实现缓存隔离。
    """

    def __init__(self, cache: Cache, prefix: str):
        self._cache = cache
        self._prefix = prefix.rstrip(":") + ":"

    def get(self, key: str) -> Any | None:
        return self._cache.get(self._prefix + key)

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._cache.set(self._prefix + key, value, ttl)

    def delete(self, key: str) -> bool:
        return self._cache.delete(self._prefix + key)

    def clear_namespace(self) -> int:
        """清空该命名空间下的所有缓存"""
        count = 0
        with self._cache._lock:
            prefixed_keys = [
                k for k in self._cache._store.keys()
                if k.startswith(self._prefix)
            ]
            for k in prefixed_keys:
                del self._cache._store[k]
                count += 1
        return count


# ============================================================
# 全局缓存实例
# ============================================================

_global_cache: Cache | None = None


def get_cache(default_ttl: int = 300) -> Cache:
    """获取全局缓存实例"""
    global _global_cache
    if _global_cache is None:
        _global_cache = Cache(default_ttl=default_ttl)
    return _global_cache
