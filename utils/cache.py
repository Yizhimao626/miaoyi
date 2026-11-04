"""喵艺缓存管理模块"""
import threading
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional


class AdvancedCache:
    """高级缓存管理器"""

    def __init__(self, max_size: int = 1000, ttl_hours: int = 4):
        self.cache: dict[str, dict[str, Any]] = {}
        self.max_size = max_size
        self.ttl = ttl_hours * 3600
        self.access_order: deque = deque(maxlen=max_size * 2)
        self.lock = threading.RLock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.last_cleanup = time.time()
        self.cleanup_interval = 300
        self.stats = defaultdict(int)

    def get(self, key: str) -> Any | None:
        """获取缓存"""
        with self.lock:
            if key not in self.cache:
                self.misses += 1
                self.stats['misses'] += 1
                return None
            item = self.cache[key]
            if time.time() - item['timestamp'] > self.ttl:
                self._remove_key(key)
                self.misses += 1
                self.stats['misses'] += 1
                return None
            if key in self.access_order:
                self.access_order.remove(key)
            self.access_order.append(key)
            self.hits += 1
            self.stats['hits'] += 1
            item['access_count'] = item.get('access_count', 0) + 1
            item['last_access'] = time.time()
            return item['value']

    def set(self, key: str, value: Any, importance: float = 0.5, metadata: dict = None):
        """设置缓存"""
        with self.lock:
            if key in self.cache:
                if key in self.access_order:
                    self.access_order.remove(key)
            elif len(self.cache) >= self.max_size:
                self._evict_low_priority()
            self.cache[key] = {
                'value': value,
                'timestamp': time.time(),
                'importance': importance,
                'access_count': 0,
                'last_access': time.time(),
                'metadata': metadata or {}
            }
            self.access_order.append(key)
            self.stats['sets'] += 1
            self._maybe_cleanup()

    def _evict_low_priority(self):
        """淘汰低优先级缓存项"""
        if not self.access_order:
            return
        candidates = []
        for key in list(self.access_order)[:min(20, len(self.access_order))]:
            if key in self.cache:
                item = self.cache[key]
                age = time.time() - item['timestamp']
                score = item.get('importance', 0.5) / (1 + age / 3600)
                candidates.append((score, key))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            _, key_to_evict = candidates[0]
            self._remove_key(key_to_evict)
            self.evictions += 1
            self.stats['evictions'] += 1

    def _maybe_cleanup(self):
        """可能清理过期缓存"""
        if time.time() - self.last_cleanup > self.cleanup_interval:
            self.cleanup_expired()
            self.last_cleanup = time.time()

    def _remove_key(self, key: str):
        """删除缓存键"""
        if key in self.cache:
            del self.cache[key]
        if key in self.access_order:
            try:
                self.access_order.remove(key)
            except ValueError:
                pass

    def remove(self, key: str):
        """删除缓存"""
        with self.lock:
            self._remove_key(key)
            self.stats['removals'] += 1

    def clear(self):
        """清空缓存"""
        with self.lock:
            self.cache.clear()
            self.access_order.clear()
            self.hits = 0
            self.misses = 0
            self.evictions = 0
            self.stats.clear()

    def get_stats(self) -> dict[str, Any]:
        """获取缓存统计信息"""
        with self.lock:
            total_requests = self.hits + self.misses
            hit_rate = self.hits / total_requests if total_requests > 0 else 0
            return {
                'size': len(self.cache),
                'max_size': self.max_size,
                'hits': self.hits,
                'misses': self.misses,
                'hit_rate': hit_rate,
                'evictions': self.evictions,
                'cleanup_count': getattr(self, 'cleanup_count', 0),
                'stats': dict(self.stats)
            }

    def cleanup_expired(self):
        """清理过期缓存"""
        with self.lock:
            now = time.time()
            expired_keys = []
            low_priority_keys = []
            for key, item in self.cache.items():
                age = now - item['timestamp']
                if age > self.ttl:
                    expired_keys.append(key)
                elif item.get('importance', 0.5) < 0.2 and age > self.ttl * 0.5:
                    low_priority_keys.append(key)
            keys_to_remove = expired_keys + low_priority_keys[:int(self.max_size * 0.1)]
            for key in keys_to_remove:
                self._remove_key(key)
            if not hasattr(self, 'cleanup_count'):
                self.cleanup_count = 0
            self.cleanup_count += 1
            self.stats['cleanups'] += 1