"""配置管理器 - 基于YAML的统一配置管理"""
import copy
import logging
import os
from typing import Any, Dict

import yaml

_log = logging.getLogger("ConfigManager")


class ConfigManager:
    _instance = None
    _lock = __import__('threading').Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, config_path: str = None):
        if self._initialized:
            return
        self._initialized = True
        self._config_path = config_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml'
        )
        self._config = {}
        self._defaults = {}
        self._load_config()

    def _load_config(self):
        """加载配置文件"""
        if os.path.exists(self._config_path):
            try:
                with open(self._config_path, encoding='utf-8') as f:
                    self._config = yaml.safe_load(f)
                self._defaults = copy.deepcopy(self._config)
            except Exception as e:
                _log.warning(f"配置文件加载失败: {e}")
                self._config = self._get_default_config()
                self._defaults = copy.deepcopy(self._config)
        else:
            self._config = self._get_default_config()
            self._defaults = copy.deepcopy(self._config)
            self._save_config()

    def _get_default_config(self) -> dict[str, Any]:
        """获取默认配置"""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return {
            'app': {
                'name': '喵艺',
                'version': 'v12.0',
                'version_name': 'Neural Companion',
                'project_root': project_root,
                'data_dir': os.path.join(project_root, 'data'),
                'log_dir': os.path.join(project_root, 'logs'),
                'database_file': os.path.join(project_root, 'miaoyi.db'),
            },
            'server': {
                'host': '127.0.0.1',
                'port': 8000,
                'max_port_try': 8100,
                'debug': False,
                'reload': False,
            },
            'llm': {
                'provider': 'zhipu',
                'model_name': 'glm-4-flash',
                'thinking_model': 'glm-4-flash',
                'api_key_env': 'ZHIPU_API_KEY',
                'max_tokens': 4096,
                'temperature': 0.5,
                'timeout': 30,
                'max_retries': 3,
                'retry_delay': 1.0,
            },
            'emotion': {
                'empathy_coefficient': 1.0,
                'empathy_min': 0.3,
                'empathy_decrease_step': 0.2,
                'emotional_independence': 0.7,
                'negative_window_minutes': 30,
                'negative_count_threshold': 3,
                'redirection_threshold': 0.3,
            },
            'emotion_memory': {
                'enabled': True,
                'model_cache_dir': 'models',
                'auto_download': True,
            },
            'memory': {
                'max_history': 50,
                'max_emotional_memories': 500,
                'memory_retention_days': 90,
                'vector_threshold': 0.7,
                'vector_top_k': 3,
                'cache_size': 50,
                # 【L9 记忆服务独立进程】三分离架构：聊天/记忆/存储
                # enabled=true 时主服务经 HTTP 访问 memory_server.py（跨端共享记忆），
                # false 时行为与旧版完全一致（进程内直连）
                'remote': {
                    'enabled': False,
                    'url': 'http://127.0.0.1:8002',
                    'token': 'miaoyi-memory-local',
                    'timeout': 3.0,
                },
            },
            'style': {
                'mimicry_strength': 0.7,
                'semantic_similarity_threshold': 0.9,
                'light_polish_enabled': True,
            },
            'proactive': {
                'enabled': True,
                'trigger_probability': 0.2,
                'silence_timeout_seconds': 900,
                'topic_pool_size': 20,
                # 【L7 心跳纪律】噪音预算 + 免打扰时段（支持跨午夜）
                'noise_budget_per_hour': 2,
                'dnd_start': 23,
                'dnd_end': 8,
                # 【L7 生活模拟】朋友圈动态
                'moments_enabled': True,
                'moments_interval_hours': 3,
                'moments_max_per_day': 4,
            },
            'voice': {
                'enabled': True,
                'asr_engine': 'vosk',
                'tts_engine': 'edge',
                'local_tts_enabled': False,
                'wake_word_enabled': False,
                'wake_word': '喵艺',
                'speech_timeout_seconds': 5,
                'silence_threshold': 300,
                'whisper_model': 'base',
            },
            'search': {
                'enabled': True,
                'sources': ['baidu', 'bing', 'baike'],
                'timeout': 20,
                'max_results': 5,
                'max_parallel_requests': 3,
            },
            'security': {
                'sensitive_word_enabled': True,
                'dependency_detection_enabled': True,
                'max_message_length': 2000,
            },
            'performance': {
                'max_concurrent_thinking': 1,
                'thinking_timeout_seconds': 60,
                'thread_pool_size': 4,
                'cache_ttl_hours': 24,
            },
            'logging': {
                'level': 'DEBUG',
                'max_bytes': 10485760,
                'backup_count': 5,
            },
            'appearance': {
                'theme': 'dark',
                'primary_color': '#6366F1',
                'accent_color': '#8B5CF6',
                'user_bubble_color': '#6366F1',
                'bot_bubble_color': '#FFFFFF',
            },
            'features': {
                'deep_thinking_enabled': True,
                'emotional_memory_enabled': True,
                'autobiography_enabled': True,
                'proactive_chat_enabled': True,
                'style_mimicry_enabled': True,
                'voice_input_enabled': True,
                'voice_output_enabled': True,
            },
        }

    def _save_config(self):
        """保存配置到文件"""
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, 'w', encoding='utf-8') as f:
                yaml.dump(self._config, f, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            _log.warning(f"配置文件保存失败: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持点分隔路径"""
        keys = key.split('.')
        value = self._config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key: str, value: Any, save: bool = True):
        """设置配置值，支持点分隔路径"""
        keys = key.split('.')
        config = self._config
        for i, k in enumerate(keys[:-1]):
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
        if save:
            self._save_config()

    def reset(self, key: str = None):
        """重置配置到默认值"""
        if key:
            keys = key.split('.')
            config = self._config
            defaults = self._defaults
            for i, k in enumerate(keys[:-1]):
                config = config[k]
                defaults = defaults[k]
            config[keys[-1]] = defaults[keys[-1]]
        else:
            self._config = copy.deepcopy(self._defaults)
        self._save_config()

    def get_all(self) -> dict[str, Any]:
        """获取所有配置"""
        return copy.deepcopy(self._config)

    def ensure_directories(self):
        """确保必要目录存在"""
        os.makedirs(self.get('app.data_dir'), exist_ok=True)
        os.makedirs(self.get('app.log_dir'), exist_ok=True)


config_manager = ConfigManager()
