"""喵艺会话管理模块"""
import threading
from collections import defaultdict
from typing import Any, Dict

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import REQUESTS_AVAILABLE, Config, DependencyManager

# 获取requests模块
requests = DependencyManager.get_module('requests')


class RequestSessionManager:
    """请求会话管理器"""

    def __init__(self):
        self._session = None
        self._lock = threading.Lock()
        self._request_stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'avg_response_time': 0.0,
            'errors': defaultdict(int)
        }

    def get_session(self):
        """获取配置好的requests会话"""
        if not REQUESTS_AVAILABLE:
            return None
        if self._session is None:
            with self._lock:
                if self._session is None:
                    self._session = requests.Session()
                    retry_strategy = Retry(
                        total=Config.MAX_RETRIES,
                        backoff_factor=0.5,
                        status_forcelist=[429, 500, 502, 503, 504],
                        allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
                    )
                    adapter = HTTPAdapter(
                        max_retries=retry_strategy,
                        pool_connections=15,
                        pool_maxsize=30,
                        pool_block=False
                    )
                    self._session.mount("http://", adapter)
                    self._session.mount("https://", adapter)
                    self._session.headers.update({
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Connection': 'keep-alive',
                        'Upgrade-Insecure-Requests': '1'
                    })
        return self._session

    def record_request(self, success: bool, response_time: float = None):
        """记录请求统计"""
        self._request_stats['total_requests'] += 1
        if success:
            self._request_stats['successful_requests'] += 1
        else:
            self._request_stats['failed_requests'] += 1
        if response_time:
            self._request_stats['avg_response_time'] = (
                    self._request_stats['avg_response_time'] * 0.9 + response_time * 0.1
            )

    def close(self):
        """关闭会话"""
        with self._lock:
            if self._session:
                self._session.close()
                self._session = None

    def get_stats(self) -> dict[str, Any]:
        """获取统计信息"""
        with self._lock:
            return {
                'total_requests': self._request_stats['total_requests'],
                'successful_requests': self._request_stats['successful_requests'],
                'failed_requests': self._request_stats['failed_requests'],
                'avg_response_time': self._request_stats['avg_response_time'],
                'errors': dict(self._request_stats['errors'])
            }


# 全局会话管理器实例
session_manager = RequestSessionManager()