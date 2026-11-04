"""喵艺日志管理模块"""
import logging
import os
from datetime import datetime

from config import Config


class AdvancedLogger:
    """高级日志管理器"""

    def __init__(self, name: str = "喵艺9.0", level: int = logging.INFO):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.handlers = []
        self._setup_handlers()

    def _setup_handlers(self):
        """设置处理器"""
        Config.ensure_directories()
        log_file = os.path.join(Config.LOG_DIR, f"miaoyi9_{datetime.now().strftime('%Y%m%d')}.log")
        try:
            file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')
            file_handler.setFormatter(self._get_formatter())
            self.logger.addHandler(file_handler)
            self.handlers.append(file_handler)
        except Exception as e:
            print(f"文件日志设置失败: {e}")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(self._get_formatter())
        self.logger.addHandler(console_handler)
        self.handlers.append(console_handler)

    def _get_formatter(self):
        """获取格式化器"""
        return logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    def debug(self, msg: str, *args, **kwargs):
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs):
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs):
        self.logger.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs):
        self.logger.exception(msg, *args, **kwargs)

    def close(self):
        """关闭日志处理器"""
        for handler in self.handlers:
            handler.close()
            self.logger.removeHandler(handler)


# 全局日志实例
logger = AdvancedLogger("喵艺9.0 Ultimate")