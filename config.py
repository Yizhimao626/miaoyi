"""喵艺配置模块"""
import os
from enum import Enum


class Language(Enum):
    CHINESE = 'zh'
    ENGLISH = 'en'
    JAPANESE = 'ja'

class DialogueState(Enum):
    IDLE = 'idle'
    ACTIVE = 'active'
    LEARNING = 'learning'
    SLEEPING = 'sleeping'

class ResponseMode(Enum):
    NORMAL = 'normal'
    THINKING = 'thinking'
    SEARCHING = 'searching'
    EMOTIONAL = 'emotional'

class Config:
    """系统配置"""
    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
    LOG_DIR = os.path.join(PROJECT_ROOT, 'logs')
    
    DATABASE_FILE = os.path.join(DATA_DIR, 'miao_yi.db')
    MEMORY_FILE = os.path.join(DATA_DIR, 'memory.json')
    MEMORY_BACKUP_FILE = os.path.join(DATA_DIR, 'memory_backup.json')
    KNOWLEDGE_FILE = os.path.join(DATA_DIR, 'knowledge.json')
    LEARNING_FILE = os.path.join(DATA_DIR, 'learning.json')
    PERSONALITY_FILE = os.path.join(DATA_DIR, 'personality.json')
    
    MAX_HISTORY = 50
    MAX_SEARCH_RESULTS = 10
    MAX_PARALLEL_REQUESTS = 4
    SEARCH_TIMEOUT = 15
    API_TIMEOUT = 30
    CACHE_TTL_HOURS = 24
    
    LEARNING_INTERVAL = 300
    KNOWLEDGE_CONSOLIDATION_INTERVAL = 3600
    GROWTH_UPDATE_INTERVAL = 1800
    
    MODEL_NAME = "glm-4-flash"
    THINKING_MODEL = "glm-4-flash"
    MAX_RETRIES = 3
    RETRY_DELAY = 1.0
    RETRY_BACKOFF_FACTOR = 2.0
    PIL_AVAILABLE = False
    
    @staticmethod
    def get_zhipu_api_key():
        return os.environ.get('ZHIPU_API_KEY', '')
    
    @staticmethod
    def ensure_directories():
        os.makedirs(Config.DATA_DIR, exist_ok=True)
        os.makedirs(Config.LOG_DIR, exist_ok=True)

try:
    import requests  # noqa: F401 - optional dependency availability probe
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

try:
    import bs4  # noqa: F401 - optional dependency availability probe
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

try:
    from zhipuai import ZhipuAI  # noqa: F401 - optional dependency availability probe
    ZHIPU_AVAILABLE = True
except ImportError:
    ZHIPU_AVAILABLE = False

try:
    import nltk  # noqa: F401 - optional dependency availability probe
    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

MAX_RETRIES = 3

class DependencyManager:
    """依赖管理器"""
    
    @staticmethod
    def get_module(module_name: str):
        """获取模块"""
        try:
            return __import__(module_name)
        except ImportError:
            return None
