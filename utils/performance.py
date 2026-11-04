"""性能优化模块 - 缓存、超时、重试"""
import hashlib
import threading
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable, Dict, List, Optional


class MemoryCache:
    """内存缓存 - LRU策略"""
    
    def __init__(self, max_size: int = 50):
        self._cache = OrderedDict()
        self._max_size = max_size
        self._lock = threading.Lock()
    
    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None
    
    def set(self, key: str, value: Any):
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = value
            
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
    
    def invalidate(self, key: str):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
    
    def clear(self):
        with self._lock:
            self._cache.clear()
    
    def size(self) -> int:
        with self._lock:
            return len(self._cache)


class VectorMemoryCache:
    """向量记忆专用缓存"""
    
    def __init__(self, max_size: int = 50):
        self._cache = MemoryCache(max_size)
        self._enabled = True
    
    def get_vector(self, memory_id: int) -> Any | None:
        if not self._enabled:
            return None
        return self._cache.get(f"vector_{memory_id}")
    
    def set_vector(self, memory_id: int, vector: Any):
        if not self._enabled:
            return
        self._cache.set(f"vector_{memory_id}", vector)
    
    def get_search_result(self, query_hash: str) -> list[dict] | None:
        if not self._enabled:
            return None
        return self._cache.get(f"search_{query_hash}")
    
    def set_search_result(self, query_hash: str, results: list[dict]):
        if not self._enabled:
            return
        self._cache.set(f"search_{query_hash}", results)
    
    def compute_query_hash(self, query: str) -> str:
        return hashlib.md5(query.encode('utf-8')).hexdigest()
    
    def clear(self):
        self._cache.clear()
    
    def disable(self):
        self._enabled = False
    
    def enable(self):
        self._enabled = True


def timeout(timeout_seconds: int, default_return: Any = None):
    """超时装饰器"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = [default_return]
            exception = [None]
            event = threading.Event()
            
            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e
                finally:
                    event.set()
            
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            event.wait(timeout=timeout_seconds)
            
            if exception[0]:
                raise exception[0]
            
            return result[0]
        
        return wrapper
    return decorator


def retry(max_retries: int = 3, delay: float = 1.0, backoff_factor: float = 2.0, exceptions: tuple = (Exception,)):
    """重试装饰器"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        time.sleep(current_delay)
                        current_delay *= backoff_factor
            
            raise last_exception
        
        return wrapper
    return decorator


class RequestPriority:
    """请求优先级枚举"""
    HIGH = 1
    MEDIUM = 2
    LOW = 3


class PriorityQueue:
    """优先级队列"""
    
    def __init__(self):
        self._queues = {
            RequestPriority.HIGH: [],
            RequestPriority.MEDIUM: [],
            RequestPriority.LOW: [],
        }
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
    
    def put(self, item: Any, priority: int = RequestPriority.MEDIUM):
        with self._lock:
            self._queues[priority].append(item)
            self._not_empty.notify()
    
    def get(self, timeout: float | None = None) -> Any:
        with self._not_empty:
            for priority in [RequestPriority.HIGH, RequestPriority.MEDIUM, RequestPriority.LOW]:
                if self._queues[priority]:
                    return self._queues[priority].pop(0)
            
            if timeout is None:
                self._not_empty.wait()
            elif timeout > 0:
                if not self._not_empty.wait(timeout=timeout):
                    raise TimeoutError("队列超时")
            
            for priority in [RequestPriority.HIGH, RequestPriority.MEDIUM, RequestPriority.LOW]:
                if self._queues[priority]:
                    return self._queues[priority].pop(0)
            
            raise TimeoutError("队列为空")
    
    def empty(self) -> bool:
        with self._lock:
            return all(len(q) == 0 for q in self._queues.values())
    
    def qsize(self) -> int:
        with self._lock:
            return sum(len(q) for q in self._queues.values())


class ThinkingTimeoutGuard:
    """思考超时保护"""
    
    def __init__(self, timeout_seconds: int = 60):
        self._timeout = timeout_seconds
        self._active = False
        self._start_time = 0
        self._lock = threading.Lock()
    
    def start(self):
        with self._lock:
            self._active = True
            self._start_time = time.time()
    
    def check(self) -> bool:
        """检查是否超时，返回True表示已超时"""
        if not self._active:
            return False
        
        elapsed = time.time() - self._start_time
        if elapsed >= self._timeout:
            self._active = False
            return True
        
        return False
    
    def stop(self):
        with self._lock:
            self._active = False
    
    def is_active(self) -> bool:
        return self._active


class ConcurrencyLimiter:
    """并发限制器"""
    
    def __init__(self, max_concurrent: int = 1):
        self._max_concurrent = max_concurrent
        self._current = 0
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
    
    def acquire(self):
        with self._condition:
            while self._current >= self._max_concurrent:
                self._condition.wait()
            self._current += 1
    
    def release(self):
        with self._condition:
            self._current -= 1
            self._condition.notify()
    
    def get_current(self) -> int:
        with self._lock:
            return self._current


memory_cache = MemoryCache()
vector_cache = VectorMemoryCache()
thinking_guard = ThinkingTimeoutGuard()
thinking_limiter = ConcurrencyLimiter(max_concurrent=1)
request_queue = PriorityQueue()
