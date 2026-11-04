"""
喵艺 AI 伴侣 · 结构化日志配置
==============================
职责：
  - JSON 格式日志输出到 stdout
  - 方便日志收集系统（ELK/Loki）处理
  - 自动降级第三方库日志级别
  - 支持请求追踪 ID
"""

import json
import logging
import sys
import time
import uuid
from typing import Any, Dict, Optional

_log = logging.getLogger("LoggingConfig")


# ============================================================
# JSON 日志格式化器
# ============================================================

class JsonFormatter(logging.Formatter):
    """
    JSON 格式日志格式化器

    输出格式：
    {
      "timestamp": "2026-01-15T10:30:00+08:00",
      "level": "INFO",
      "logger": "miaoyi.chat",
      "message": "对话完成",
      "module": "chat_engine",
      "line": 71,
      "user_id": "user_001",
      "request_id": "abc123",
      "duration": 1.23,
      "extra": {...}
    }
    """

    def __init__(self, include_extras: bool = True):
        super().__init__()
        self._include_extras = include_extras

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": self._format_time(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "line": record.lineno,
        }

        # 附加字段
        extra_fields = {}
        for key in ("user_id", "request_id", "duration", "session_id"):
            if hasattr(record, key):
                extra_fields[key] = getattr(record, key)

        if extra_fields:
            log_entry.update(extra_fields)

        # 异常堆栈
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        # 额外上下文
        if self._include_extras and hasattr(record, "extra_ctx"):
            log_entry["extra"] = record.extra_ctx

        return json.dumps(log_entry, ensure_ascii=False)

    def _format_time(self, record: logging.LogRecord) -> str:
        import datetime
        # 使用本地时区（UTC+8 北京时间近似）
        dt = datetime.datetime.fromtimestamp(record.created)
        # 格式化并添加 +08:00 偏移
        formatted = dt.strftime("%Y-%m-%dT%H:%M:%S")
        return f"{formatted}+08:00"


# ============================================================
# 请求追踪
# ============================================================

_request_context: dict[str, Any] = {
    "request_id": None,
    "user_id": None,
    "start_time": None,
}


def new_request_id() -> str:
    """生成新的请求 ID"""
    return uuid.uuid4().hex[:12]


def set_request_context(
    request_id: str | None = None,
    user_id: str | None = None,
):
    """设置当前请求上下文"""
    _request_context["request_id"] = request_id or new_request_id()
    _request_context["user_id"] = user_id
    _request_context["start_time"] = time.time()


def clear_request_context():
    """清除请求上下文"""
    _request_context["request_id"] = None
    _request_context["user_id"] = None
    _request_context["start_time"] = None


def get_request_id() -> str | None:
    """获取当前请求 ID"""
    return _request_context["request_id"]


def _get_duration_ms() -> float | None:
    """获取请求耗时（毫秒）"""
    if _request_context["start_time"]:
        return (time.time() - _request_context["start_time"]) * 1000
    return None


# ============================================================
# 扩展日志方法
# ============================================================

def log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    user_id: str | None = None,
    request_id: str | None = None,
    duration: float | None = None,
    **extra,
):
    """
    带上下文的日志记录

    Args:
        logger: 日志记录器
        level: 日志级别
        message: 日志消息
        user_id: 用户 ID
        request_id: 请求 ID
        duration: 耗时（秒）
        **extra: 额外字段
    """
    # 附加到 LogRecord 的属性
    extra_attrs = {}
    if user_id:
        extra_attrs["user_id"] = user_id
    if request_id:
        extra_attrs["request_id"] = request_id
    if duration is not None:
        extra_attrs["duration"] = duration
    if extra:
        extra_attrs["extra_ctx"] = extra

    # 使用 logger.log 并通过 extra 传递上下文
    logger.log(level, message, extra=extra_attrs if extra_attrs else {})


def log_performance(
    logger: logging.Logger,
    operation: str,
    duration_ms: float,
    user_id: str | None = None,
    **metrics,
):
    """
    记录性能日志

    Args:
        logger: 日志记录器
        operation: 操作名
        duration_ms: 耗时（毫秒）
        user_id: 用户 ID
        **metrics: 其他指标（如 token_count, message_length 等）
    """
    message = f"[{operation}] 耗时 {duration_ms:.1f}ms"
    if metrics:
        metric_str = ", ".join(f"{k}={v}" for k, v in metrics.items())
        message += f" | {metric_str}"

    log_with_context(
        logger, logging.INFO, message,
        user_id=user_id,
        duration=duration_ms / 1000,
        **metrics,
    )


# ============================================================
# 初始化配置
# ============================================================

_configured = False


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    log_file: str | None = None,
):
    """
    配置全局日志

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        json_format: 是否使用 JSON 格式
        log_file: 日志文件路径（None 则只输出到 stdout）
    """
    global _configured
    if _configured:
        return

    # 【Windows 兼容】stdout/stderr 默认 GBK 无法编码 emoji（✅ 等），强制 UTF-8 容错替换
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    # 设置根日志级别
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 清除已有 handler
    root_logger.handlers.clear()

    # 创建 stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.DEBUG)

    if json_format:
        formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    # 文件日志（可选）
    if log_file:
        # 【数据安全】RotatingFileHandler 按大小轮转：单个 5MB、保留 5 份历史，
        # 防止日志无限增长占满磁盘（此前用 FileHandler 只增不减）。
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # 降级第三方库日志
    _suppress_noisy_logs()

    _configured = True
    _log.info(f"日志配置完成: level={level}, json={json_format}")


def _suppress_noisy_logs():
    """降级第三方库的日志级别"""
    noisy_modules = [
        "urllib3",
        "httpx",
        "httpcore",
        "httpx._config",
        "httpx._transports",
        "asyncio",
        "aiohttp",
        "starlette",
        "uvicorn.access",
        "uvicorn.error",
    ]

    for module_name in noisy_modules:
        logging.getLogger(module_name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    获取带命名空间的日志记录器

    Args:
        name: 模块名

    Returns:
        logging.Logger
    """
    return logging.getLogger(f"miaoyi.{name}")
