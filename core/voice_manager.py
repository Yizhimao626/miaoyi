"""语音管理器 - 完整迁移自main.py的VoiceEngine，支持edge-tts、百度TTS、gTTS、pyttsx3多级降级链

【L4 TTS 流式化】新增 CosyVoice2 流式引擎（DashScope WebSocket 协议，零额外依赖，
边合成边收包，首包 ~150ms）与 GPT-SoVITS 本地引擎（可选）。
降级链：缓存 → CosyVoice2（需 DASHSCOPE_API_KEY）→ GPT-SoVITS（需配置 url）→
edge-tts（需代理）→ 百度 → gTTS → pyttsx3。
"""
import asyncio
import hashlib
import json
import logging
import os
import queue
import random
import re
import threading
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

_log = logging.getLogger("VoiceManager")

# ════════════════════════════════════════════════════════════════════════
#  TTS文本清洗
# ════════════════════════════════════════════════════════════════════════
EMOJI_TO_TEXT = {
    '😊': '开心', '😄': '大笑', '😢': '难过', '😭': '伤心',
    '😤': '生气', '😍': '喜欢', '🤔': '思考', '😂': '笑哭',
    '🥰': '喜爱', '💪': '加油', '👍': '点赞', '🙏': '感谢',
    '🎉': '庆祝', '✨': '闪亮', '❤️': '爱心', '💕': '爱意',
    '😴': '困了', '🌙': '晚安', '🔥': '热情', '😏': '得意',
    '👀': '好奇', '🤗': '拥抱', '🌟': '星星', '🐱': '猫咪',
    '🤩': '兴奋', '😜': '调皮', '🥺': '委屈', '😎': '酷',
    '🤣': '大笑', '🫂': '拥抱', '😥': '失望', '😱': '惊恐',
    '🥳': '庆祝', '😘': '飞吻', '😚': '亲亲', '😋': '美味',
    '😌': '放松', '🙄': '白眼', '😪': '困倦', '😫': '累',
    '💖': '心动', '💗': '喜欢', '💞': '心动', '💓': '心动',
    '💝': '爱心', '💟': '爱心', '❣️': '爱心',
    '💔': '心碎', '❌': '错误', '✅': '正确', '❎': '错误',
    '📌': '标记', '📍': '位置', '🔖': '书签', '🏷️': '标签',
}


def text_for_tts(text: str) -> str:
    """清理文本用于语音合成，删除Emoji但保留常用中文和英文标点"""
    import re
    
    if not text:
        return "喵"
    
    try:
        import emoji as emoji_lib
        text = emoji_lib.replace_emoji(text, replace='')
    except ImportError:
        # 降级正则：保留中文、英文、数字、常用标点，只移除特殊符号和Emoji
        text = re.sub(r'[^\w\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef'
                      r'，。！？；：、‘’“”（）\[\]【】《》〈〉—…·～'
                      r'\.\,\!\?\;\:\'\"\-\(\)\[\]\{\}\@\#\$\%\^\&\*\+\=\/\<\>\~\`]', '', text)
    
    emoji_ranges = [
        (0x1F600, 0x1F64F), (0x1F300, 0x1F5FF), (0x1F680, 0x1F6FF),
        (0x2600, 0x26FF), (0x2700, 0x27BF), (0x1F900, 0x1F9FF),
        (0x1FA00, 0x1FAFF), (0x1F004, 0x1F0CF), (0x2B00, 0x2BFF),
        (0x2934, 0x2935), (0x3297, 0x3299), (0x1F1E6, 0x1F1FF),
    ]
    
    def is_emoji(char):
        code = ord(char)
        for start, end in emoji_ranges:
            if start <= code <= end:
                return True
        return False
    
    result = []
    for char in text:
        if is_emoji(char):
            continue
        code = ord(char)
        # 保留中文、中文标点、全角字符、ASCII可打印字符、扩展ASCII
        if (0x4e00 <= code <= 0x9fff or      # 中文（扩展至CJK统一表意文字）
            0x3000 <= code <= 0x303f or      # 中文标点
            0xff00 <= code <= 0xffef or      # 全角字符
            0x0020 <= code <= 0x007e or      # ASCII可打印字符（含英文标点）
            0x00a0 <= code <= 0x00ff or      # 扩展ASCII
            0x2010 <= code <= 0x205e or      # 通用标点（破折号、省略号等）
            0x2100 <= code <= 0x214f):       # 类字母符号
            result.append(char)
    
    text = ''.join(result)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-zA-Z]+;', '', text)

    # 【拟人化动作过滤】借鉴 Open-LLM-VTuber：*动作*、（表情）等舞台指示
    # 只用于视觉表达，不应被朗读。短括号内容按动作处理，长内容（说明性文字）保留。
    text = re.sub(r'\*+[^*]{0,30}\*+', '', text)      # *动作*
    text = re.sub(r'＊+[^＊]{0,30}＊+', '', text)     # 全角星号动作
    text = re.sub(r'（[^（）]{1,12}）', '', text)      # 短全角括号动作，如（歪头）
    text = re.sub(r'\(([^()]{1,12})\)', '', text)     # 短半角括号动作

    # 【L5 双通道】兜底剥离泄漏进 TTS 的内心戏标记（正常已在生成层拆分移除）
    text = re.sub(r'[（(]\s*内心\s*[:：][^）)]{0,60}[）)]', '', text)
    text = re.sub(r'【\s*内心\s*[:：][^】]{0,60}】', '', text)

    text = text.replace('~', '，').replace('～', '，')
    text = re.sub(r'\s{2,}', ' ', text).strip()[:500]

    return text or "喵"


# ════════════════════════════════════════════════════════════════════════
#  配置常量
# ════════════════════════════════════════════════════════════════════════
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pick_voice_cache_dir() -> str:
    """语音缓存目录：优先 assets/voice_cache；迁移未完成时回退根目录 voice_cache。"""
    _new = os.path.join(_PKG_ROOT, 'assets', 'voice_cache')
    _old = os.path.join(_PKG_ROOT, 'voice_cache')
    if os.path.isdir(_new):
        return _new
    if os.path.isdir(_old):
        return _old
    return _new


class VoiceConfig:
    # 【数据目录标准化】语音缓存落到 assets/voice_cache
    VOICE_CACHE_DIR = _pick_voice_cache_dir()
    VOICE_QUEUE_SIZE = 10
    VOICE_TIMEOUT = 30
    VOICE_MODE_DEFAULT = 1
    EDGE_TTS_VOICE = "zh-CN-XiaoxiaoNeural"
    VOICE_RETRY_MAX_ATTEMPTS = 3
    VOICE_TIMEOUT_SEC = 10
    
    VOICE_STYLES = {
        "default": {"female-tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+2%",
                                   "volume": "+5%", "pitch": "+5Hz", "style": "friendly"}},
        "cheerful": {"female-tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+15%",
                                    "volume": "+10%", "pitch": "+15Hz", "style": "cheerful"}},
        "calm": {"female-tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "-10%",
                                "volume": "-5%", "pitch": "+2Hz", "style": "calm"}},
        "sad": {"female-tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "-15%",
                               "volume": "+0%", "pitch": "-10Hz", "style": "sad"}},
        "excited": {"female-tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+25%",
                                   "volume": "+15%", "pitch": "+20Hz", "style": "cheerful"}},
        "warm": {"female-tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "+0%",
                                "volume": "+8%", "pitch": "+8Hz", "style": "friendly"}},
        "gentle": {"female-tts": {"voice": "zh-CN-XiaoxiaoNeural", "rate": "-5%",
                                  "volume": "+5%", "pitch": "+5Hz", "style": "friendly"}},
    }


# ════════════════════════════════════════════════════════════════════════
#  可选依赖检测
# ════════════════════════════════════════════════════════════════════════
_REQUESTS_OK = False
_EDGE_TTS_OK = False
_PYTTSX3_OK = False
_GTTS_OK = False

try:
    import requests as req_lib
    _REQUESTS_OK = True
except ImportError:
    req_lib = None

try:
    import edge_tts
    _EDGE_TTS_OK = True
except ImportError:
    edge_tts = None

try:
    import pyttsx3
    _PYTTSX3_OK = True
except ImportError:
    pyttsx3 = None

try:
    from gtts import gTTS
    _GTTS_OK = True
except ImportError:
    gTTS = None

# 【L4】websocket-client（DashScope CosyVoice2 WS 协议需要，项目已随依赖安装）
try:
    import websocket as _ws_lib
    _WEBSOCKET_OK = True
except ImportError:
    _ws_lib = None
    _WEBSOCKET_OK = False


# ════════════════════════════════════════════════════════════════════════
#  【L4】CosyVoice2 流式合成（DashScope WebSocket 协议）
# ════════════════════════════════════════════════════════════════════════
_DASHSCOPE_TTS_WS_URL = 'wss://dashscope.aliyuncs.com/api-ws/v1/inference'
_DASHSCOPE_TTS_FORMAT = 'mp3_22050hz_mono_256kbps'

# 情感风格（_get_voice_style 输出）→ CosyVoice2 语速/音调/音量 + instruct 指令前缀。
# instruct 走 CosyVoice2 的 <|endofprompt|> 指令语法，实现"情感引擎输出直通语音情绪"；
# 未列出的风格回退 default 参数（引擎参数异常只会导致该次合成失败降级，不阻塞对话）。
_COSYVOICE_STYLE_PARAMS = {
    'default':   {'rate': 1.0,  'pitch': 1.08, 'volume': 55},
    'friendly':  {'rate': 1.0,  'pitch': 1.1,  'volume': 58, 'instruct': '用亲切友善的语气说'},
    'cheerful':  {'rate': 1.1,  'pitch': 1.15, 'volume': 60, 'instruct': '用欢快雀跃的语气说'},
    'excited':   {'rate': 1.2,  'pitch': 1.25, 'volume': 65, 'instruct': '用兴奋激动的语气说'},
    'playful':   {'rate': 1.12, 'pitch': 1.2,  'volume': 60, 'instruct': '用俏皮调皮的语气说'},
    'sad':       {'rate': 0.88, 'pitch': 0.9,  'volume': 48, 'instruct': '用低落委屈的语气说'},
    'serious':   {'rate': 0.95, 'pitch': 0.95, 'volume': 55},
    'nervous':   {'rate': 1.05, 'pitch': 1.05, 'volume': 50, 'instruct': '用紧张不安的语气说'},
    'calm':      {'rate': 0.95, 'pitch': 1.05, 'volume': 52, 'instruct': '用平静放松的语气说'},
    'warm':      {'rate': 1.0,  'pitch': 1.12, 'volume': 58, 'instruct': '用温暖贴心的语气说'},
    'gentle':    {'rate': 0.95, 'pitch': 1.1,  'volume': 55, 'instruct': '用温柔体贴的语气说'},
    'tender':    {'rate': 0.92, 'pitch': 1.1,  'volume': 55, 'instruct': '用温柔充满爱意的语气说'},
    'shy':       {'rate': 0.9,  'pitch': 1.15, 'volume': 50, 'instruct': '用害羞小声的语气说'},
    'surprised': {'rate': 1.15, 'pitch': 1.25, 'volume': 62, 'instruct': '用惊讶的语气说'},
    'confident': {'rate': 1.05, 'pitch': 1.05, 'volume': 60, 'instruct': '用自信坚定的语气说'},
    'hopeful':   {'rate': 1.0,  'pitch': 1.12, 'volume': 56, 'instruct': '用充满希望的语气说'},
    'curious':   {'rate': 1.05, 'pitch': 1.18, 'volume': 55, 'instruct': '用好奇的语气说'},
    'tired':     {'rate': 0.85, 'pitch': 0.95, 'volume': 45, 'instruct': '用困倦慵懒的语气说'},
    'nostalgic': {'rate': 0.9,  'pitch': 1.0,  'volume': 50, 'instruct': '用怀念的语气说'},
}


# ════════════════════════════════════════════════════════════════════════
#  非阻塞音频播放器
# ════════════════════════════════════════════════════════════════════════
class AudioPlayer:
    def __init__(self, max_queue=5):
        self.audio_queue = queue.Queue(maxsize=max_queue)
        self._active = True
        self._start_player_thread()

    def _start_player_thread(self):
        def player_loop():
            while self._active:
                try:
                    path = self.audio_queue.get(timeout=1)
                    self._play_audio_file(path)
                except queue.Empty:
                    continue
                except Exception as e:
                    _log.error(f"播放器异常: {e}", exc_info=True)

        threading.Thread(target=player_loop, daemon=True).start()

    def play(self, path):
        try:
            self.audio_queue.put(path, timeout=2)
        except queue.Full:
            _log.warning("音频队列已满，丢弃旧文件")
            try:
                self.audio_queue.get_nowait()
                self.audio_queue.put(path, timeout=1)
            except queue.Full:
                # 队列仍满，放弃本次入队（避免使用裸 except 吞掉 KeyboardInterrupt 等信号）
                _log.warning("音频队列丢弃后仍满，放弃本次播放入队")
            except Exception as e:
                _log.warning(f"音频队列入队异常: {e}")

    def _play_audio_file(self, path: str):
        """内置音频播放（不弹出任何外部窗口）"""
        try:
            if not os.path.exists(path):
                _log.warning(f"音频文件不存在: {path}")
                return

            played = False
            
            try:
                import sounddevice as sd
                import soundfile as sf
                audio_data, sr = sf.read(path, dtype='float32')
                sd.stop()
                sd.play(audio_data, sr)
                sd.wait()
                sd.stop()
                played = True
                _log.info("使用 sounddevice 播放成功")
            except Exception as e:
                _log.warning(f"sounddevice 播放失败: {e}")
                try:
                    sd.stop()
                except Exception as e:
                    _log.debug(f"[Voice] sounddevice 停止失败（忽略）: {e}")

            if not played:
                try:
                    import playsound
                    playsound.playsound(path)
                    played = True
                    _log.info("使用 playsound 播放成功")
                except ImportError:
                    _log.warning("playsound 未安装，跳过")
                except Exception as e:
                    _log.warning(f"playsound 播放失败: {e}")

            if not played:
                _log.error(f"所有内置播放方式均失败，无法播放音频: {path}")
        except Exception as e:
            _log.warning(f"播放音频时发生异常: {e}")
        finally:
            def safe_delete():
                time.sleep(10)
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        _log.info(f"音频文件已删除: {path}")
                except Exception as e:
                    _log.error(f"删除文件失败: {e}")

            threading.Thread(target=safe_delete, daemon=True).start()

    def stop(self):
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                pass


# ════════════════════════════════════════════════════════════════════════
#  分段流式合成（首句优先，借鉴 Open-LLM-VTuber faster_first_response）
# ════════════════════════════════════════════════════════════════════════
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[。！？；!?;\n])')

# task_id -> {'total': int, 'paths': {index: path_or_None}, 'failed': bool, 'ts': float}
_SEGMENT_TASKS: dict[str, dict] = {}
_SEGMENT_TASKS_LOCK = threading.Lock()


def split_tts_segments(text: str, max_segments: int = 6, min_len: int = 6) -> list[str]:
    """把回复文本按句切分成 TTS 播放段（首句优先的前提）。

    - 按 。！？；!?;\\n 分句（标点保留在句尾）
    - 过短的句子并入前一段，避免"嗯。"这类碎段单独播放
    - 超过 max_segments 时合并进末段，保证内容不丢
    """
    text = (text or '').strip()
    if not text:
        return []
    raw = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s and s.strip()]
    if len(raw) <= 1:
        return [text]

    merged: list[str] = []
    for piece in raw:
        if merged and len(merged[-1]) < min_len:
            merged[-1] += piece
        else:
            merged.append(piece)

    if len(merged) <= max_segments:
        return merged
    head = merged[:max_segments - 1]
    head.append(''.join(merged[max_segments - 1:]))
    return head


# ════════════════════════════════════════════════════════════════════════
#  语音引擎（完整迁移自main.py的VoiceEngine）
# ════════════════════════════════════════════════════════════════════════
class VoiceManager:
    # 【数据目录标准化】语音设置落到 data/，不再写项目根目录
    SETTINGS_FILE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'settings.json')
    
    def __init__(self):
        self._load_settings()
        # 【L4】读取 config.yaml 的 voice 段（CosyVoice2 / GPT-SoVITS 引擎配置）
        self._voice_cfg: dict = {}
        try:
            import yaml as _yaml
            _cfg_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.yaml')
            if os.path.exists(_cfg_path):
                with open(_cfg_path, encoding='utf-8') as _f:
                    self._voice_cfg = (_yaml.safe_load(_f) or {}).get('voice', {}) or {}
        except Exception as e:
            _log.debug(f"读取 config.yaml voice 段失败（使用默认值）: {e}")
        self.queue = queue.Queue(maxsize=VoiceConfig.VOICE_QUEUE_SIZE)
        self._retry_queue = queue.Queue()
        self._active = True
        self._is_playing = False
        self._playback_lock = threading.Lock()
        self._cache_dir = VoiceConfig.VOICE_CACHE_DIR
        os.makedirs(self._cache_dir, exist_ok=True)
        self._pyttsx3_ok = _PYTTSX3_OK
        self._pyttsx3_engine = None
        self._edge_tts = edge_tts if _EDGE_TTS_OK else None
        self._whisper_model = None  # 本地 Whisper 模型（懒加载）
        self.audio_player = AudioPlayer()
        self._start_worker()
        self._start_retry_worker()
        _log.info(f"[VoiceManager] 语音引擎初始化完成，当前模式: {self.get_voice_mode_name()}")
    
    def _load_settings(self):
        try:
            if os.path.exists(self.SETTINGS_FILE):
                with open(self.SETTINGS_FILE, encoding='utf-8') as f:
                    settings = json.load(f)
                    self.voice_mode = settings.get('voice_mode', VoiceConfig.VOICE_MODE_DEFAULT)
                    self.mute_mode = settings.get('mute_mode', False)
            else:
                self.voice_mode = VoiceConfig.VOICE_MODE_DEFAULT
                self.mute_mode = False
        except Exception as e:
            _log.warning(f"加载语音设置失败，使用默认值: {e}")
            self.voice_mode = VoiceConfig.VOICE_MODE_DEFAULT
            self.mute_mode = False
    
    def _save_settings(self):
        try:
            settings = {}
            if os.path.exists(self.SETTINGS_FILE):
                with open(self.SETTINGS_FILE, encoding='utf-8') as f:
                    settings = json.load(f)
            settings['voice_mode'] = self.voice_mode
            settings['mute_mode'] = self.mute_mode
            with open(self.SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            _log.warning(f"保存语音设置失败: {e}")

    def _start_worker(self):
        def worker():
            while self._active:
                try:
                    task = self.queue.get(timeout=1)
                    if task:
                        self._process_task(task)
                except queue.Empty:
                    continue
                except Exception as e:
                    _log.debug(f"语音处理异常: {e}", exc_info=True)

        threading.Thread(target=worker, daemon=True).start()

    def _start_retry_worker(self):
        def retry_worker():
            while self._active:
                try:
                    if self._retry_queue.qsize() > 0:
                        task = self._retry_queue.get_nowait()
                        if task.get('attempt', 0) < 3:
                            _log.info(f"重试语音任务，尝试次数: {task.get('attempt', 0) + 1}")
                            self._process_task(task)
                    time.sleep(1)
                except queue.Empty:
                    time.sleep(0.5)
                except Exception as e:
                    _log.debug(f"重试任务异常: {e}", exc_info=True)

        threading.Thread(target=retry_worker, daemon=True).start()

    def _get_ssml_params(self, style='default'):
        params = {
            'default': {'rate': '+0%', 'pitch': '+8Hz', 'volume': '+10%'},
            'cheerful': {'rate': '+10%', 'pitch': '+12Hz', 'volume': '+15%'},
            'calm': {'rate': '-5%', 'pitch': '+5Hz', 'volume': '+8%'},
            'sad': {'rate': '-10%', 'pitch': '-8Hz', 'volume': '+5%'},
            'excited': {'rate': '+20%', 'pitch': '+15Hz', 'volume': '+18%'},
            'warm': {'rate': '+0%', 'pitch': '+10Hz', 'volume': '+12%'},
            'gentle': {'rate': '-3%', 'pitch': '+6Hz', 'volume': '+10%'},
            'happy': {'rate': '+8%', 'pitch': '+10Hz', 'volume': '+15%'},
            'surprised': {'rate': '+15%', 'pitch': '+20Hz', 'volume': '+12%'},
            'angry': {'rate': '+15%', 'pitch': '+5Hz', 'volume': '+20%'},
            'love': {'rate': '-5%', 'pitch': '+12Hz', 'volume': '+10%'},
        }
        return params.get(style, params['default'])

    def _process_task(self, task):
        text = task.get('text', '')
        style = task.get('style', 'default')
        callback = task.get('callback')
        if not text or not self._active:
            if callback:
                try:
                    callback(success=False, error="empty text or inactive")
                except Exception as e:
                    _log.debug(f"[Voice] 回调通知失败（empty/inactive）: {e}")
            return

        with self._playback_lock:
            if self._is_playing:
                _log.debug("语音播放中，打断当前播放")
                self.stop()

            clean = text_for_tts(text)
            if not clean:
                clean = re.sub(r'\s+', ' ', text).strip()
                if not clean:
                    if callback:
                        try:
                            callback(success=False, error="empty clean text")
                        except Exception as e:
                            _log.debug(f"[Voice] 回调通知失败（empty clean）: {e}")
                    return

            _log.info(f"语音处理: '{clean[:50]}...' 风格: {style}")

            try:
                self._is_playing = True
                success = self._speak_with_fallback(clean, self._get_fallback_chain(style))
                if callback:
                    try:
                        callback(success=success)
                    except Exception as e:
                        _log.warning(f"语音回调执行失败: {e}")
            except Exception as e:
                _log.error(f"语音播放异常: {e}", exc_info=True)
                if callback:
                    try:
                        callback(success=False, error=str(e))
                    except Exception as e:
                        _log.debug(f"[Voice] 回调通知失败（播放异常）: {e}")
                try:
                    self._retry_queue.put_nowait({
                        'text': text, 
                        'style': style,
                        'callback': callback,
                        'attempt': task.get('attempt', 0) + 1
                    })
                    _log.info(f"任务已加入重试队列，当前重试次数: {task.get('attempt', 0) + 1}")
                except queue.Full:
                    _log.warning("重试队列已满，放弃重试")
            finally:
                self._is_playing = False

    def _get_fallback_chain(self, style='default') -> list:
        """根据语音模式获取降级链

        【历史教训】此前女声模式链里没有 edge-tts，导致"晓晓女声"模式
        实际降级到百度人机声音。现把 edge-tts 置于女声模式首位。
        """
        voice_map = {
            0: [self._speak_local, self._speak_local_female, self._speak_baidu_female, self._speak_gtts],
            # 【声音优化】百度在线 TTS 比本地系统语音更自然，降级顺序提到 local 之前
            1: [self._speak_edge_female, self._speak_baidu_female, self._speak_local_female, self._speak_gtts],
            2: [self._speak_local, self._speak_baidu_male, self._speak_gtts],
            3: [self._speak_edge_female, self._speak_baidu_female, self._speak_local, self._speak_gtts],
        }
        return voice_map.get(self.voice_mode, voice_map[1])

    def _speak_with_fallback(self, text, fallback_chain):
        """统一降级逻辑"""
        _log.info(f"开始语音合成，文本长度: {len(text)}，降级链: {[f.__name__ for f in fallback_chain]}")
        for i, fallback_method in enumerate(fallback_chain):
            try:
                _log.info(f"尝试第 {i+1}/{len(fallback_chain)} 个引擎: {fallback_method.__name__}")
                if fallback_method(text):
                    _log.info(f"语音合成成功，使用引擎: {fallback_method.__name__}")
                    return True
                _log.info(f"{fallback_method.__name__} 返回 False，尝试下一个")
            except Exception as e:
                _log.warning(f"{fallback_method.__name__} 异常: {e}")
        _log.warning(f"所有语音引擎均失败，文本长度: {len(text)}")
        return False

    def _speak_edge_female(self, text) -> bool:
        """使用 edge-tts 女声（晓晓）"""
        return self._speak_edge(text, voice=VoiceConfig.EDGE_TTS_VOICE)

    def _speak_edge_male(self, text) -> bool:
        """使用 edge-tts 男声（云希）"""
        return self._speak_edge(text, voice='zh-CN-YunxiNeural')

    def _speak_edge_female2(self, text) -> bool:
        """使用 edge-tts 女声（晓晨 - 温暖）"""
        return self._speak_edge(text, voice='zh-CN-XiaochenNeural')

    def _speak_edge_female3(self, text) -> bool:
        """使用 edge-tts 女声（晓琪 - 活泼）"""
        return self._speak_edge(text, voice='zh-CN-XiaoqiNeural')

    def _speak_edge_male2(self, text) -> bool:
        """使用 edge-tts 男声（云海）"""
        return self._speak_edge(text, voice='zh-CN-YunhaiNeural')

    def _speak_edge_male3(self, text) -> bool:
        """使用 edge-tts 男声（晓东）"""
        return self._speak_edge(text, voice='zh-CN-XiaodongNeural')

    def _speak_edge(self, text, voice=None, style='default') -> bool:
        """使用 edge-tts 合成语音（在独立线程中运行异步调用，避免事件循环冲突）"""
        if not self._edge_tts:
            _log.warning("edge-tts 未安装或不可用")
            return False

        if voice is None:
            voice = VoiceConfig.EDGE_TTS_VOICE
        params = self._get_ssml_params(style)
        tmp_path = os.path.join(
            self._cache_dir, f"edge_{int(time.time() * 1000)}_{random.randint(1000, 9999)}.mp3")

        _log.info(f"edge-tts 开始合成，文本长度: {len(text)}, voice: {voice}, params: {params}")

        result = [False]

        def _run_edge_tts():
            """在独立线程中运行异步 edge-tts 调用"""
            async def _do_tts():
                communicate = self._edge_tts.Communicate(
                    text, voice,
                    rate=params['rate'],
                    pitch=params['pitch'],
                    volume=params['volume']
                )
                await asyncio.wait_for(communicate.save(tmp_path), timeout=10)

            for retry in range(3):
                try:
                    asyncio.run(_do_tts())
                    if os.path.exists(tmp_path):
                        file_size = os.path.getsize(tmp_path)
                        if file_size > 500:
                            self.audio_player.play(tmp_path)
                            _log.info("edge-tts 音频播放已启动")
                            result[0] = True
                            return
                        else:
                            _log.warning(f"edge-tts 音频文件太小: {file_size} bytes")
                    else:
                        _log.warning("edge-tts 音频文件不存在")
                except asyncio.TimeoutError:
                    _log.warning(f"edge-tts 合成超时（重试 {retry+1}/3）")
                except Exception as e:
                    _log.warning(f"edge-tts 调用异常（重试 {retry+1}/3）: {e}")
                if retry < 2:
                    time.sleep(0.5)

        thread = threading.Thread(target=_run_edge_tts, daemon=True)
        thread.start()
        thread.join(timeout=15)

        return result[0]

    def _speak_baidu(self, text, per=0) -> bool:
        """使用百度翻译TTS免费接口（中国大陆可用，无需API Key）
        发音人 per：0=标准女声，1=男声，4=度丫丫（情感女声）
        """
        if not text or not text.strip():
            return False
        if not _REQUESTS_OK:
            _log.warning("requests 未安装，无法使用百度TTS")
            return False

        try:
            tmp_path = os.path.join(
                self._cache_dir, f"baidu_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp3")
            
            # 【真人感】度丫丫（per=4）比标准女声更自然；spd=5 接近日常语速
            url = f"https://fanyi.baidu.com/gettts?lan=zh&text={quote_plus(text)}&spd=5&source=web&per={per}"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = req_lib.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200 and len(response.content) > 500:
                with open(tmp_path, 'wb') as f:
                    f.write(response.content)
                self.audio_player.play(tmp_path)
                gender = "女声" if per in (0, 4) else "男声"
                _log.info(f"百度TTS音频播放已启动（{gender}）")
                return True
            else:
                _log.warning(f"百度TTS请求失败，状态码: {response.status_code}")
                return False
        except req_lib.exceptions.Timeout:
            _log.warning("百度TTS请求超时")
            return False
        except Exception as e:
            _log.warning(f"百度TTS合成失败: {e}")
            return False

    def _speak_baidu_female(self, text) -> bool:
        """百度TTS情感女声（per=4 度丫丫，比标准女声 per=0 更接近真人）"""
        return self._speak_baidu(text, per=4)

    def _speak_baidu_male(self, text) -> bool:
        """使用百度TTS男声"""
        return self._speak_baidu(text, per=1)

    def _speak_gtts(self, text) -> bool:
        """使用 gTTS 合成（备选）"""
        if not text or not text.strip():
            _log.warning("gTTS 跳过空文本")
            return False
        if not _GTTS_OK:
            _log.warning("gTTS 未安装")
            return False
        try:
            tmp_path = os.path.join(
                self._cache_dir, f"gtts_{int(time.time() * 1000)}_{random.randint(1000, 9999)}.mp3")
            tts = gTTS(text=text, lang='zh-CN')
            tts.save(tmp_path)
            if not os.path.exists(tmp_path):
                _log.error("gTTS 未能生成音频文件")
                return False
            self.audio_player.play(tmp_path)
            return True
        except Exception as e:
            _log.warning(f"gTTS 语音合成失败: {e}")
            return False

    def _speak_local(self, text, rate=180) -> bool:
        """使用 pyttsx3 合成语音，通过 rate 参数控制语速（默认180，女声推荐160）"""
        if not self._pyttsx3_ok:
            _log.warning("pyttsx3 不可用，无法使用本地语音")
            return False
        try:
            engine = pyttsx3.init()
            try:
                voices = engine.getProperty('voices') or []
                chinese_voice = None
                for v in voices:
                    name = (getattr(v, 'name', '') or '').lower()
                    vid = (getattr(v, 'id', '') or '').lower()
                    if 'zh' in name or 'chinese' in name or 'zh' in vid or 'chinese' in vid or 'huihui' in name:
                        chinese_voice = v.id
                        break
                if chinese_voice:
                    engine.setProperty('voice', chinese_voice)
            except Exception as e:
                # 语音属性设置失败时降级用默认语音
                _log.debug(f"[Voice] 中文语音设置失败，使用默认: {e}")
            try:
                engine.setProperty('rate', rate)
                engine.setProperty('volume', 1.0)
            except Exception as e:
                # 音量/语速设置失败时降级用默认值
                _log.debug(f"[Voice] 语速/音量设置失败，使用默认: {e}")
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            _log.info(f"pyttsx3 本地语音播放成功 (rate={rate})")
            return True
        except Exception as e:
            _log.warning(f"本地语音播放失败: {e}")
            return False

    def _speak_local_female(self, text) -> bool:
        """使用 pyttsx3 合成中文女声（调用 _speak_local 并设置较慢语速）"""
        return self._speak_local(text, rate=160)

    def speak(self, text, style='default', callback=None):
        """入队语音任务"""
        if not text or not self._active:
            _log.info(f"speak被调用但文本为空或引擎未激活: text='{text}', active={self._active}")
            return
        if self.mute_mode:
            _log.info("静音模式，跳过语音播放")
            return
        _log.info(f"speak被调用，文本长度: {len(text)}, 文本前50字: '{text[:50]}...'")
        task = {'text': text, 'style': style, 'callback': callback}
        try:
            self.queue.put_nowait(task)
            _log.info("语音任务入队成功")
        except queue.Full:
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(task)
                _log.info("语音队列满，丢弃旧任务后入队")
            except queue.Empty:
                pass

    def set_voice_mode(self, mode):
        self.voice_mode = max(0, min(3, mode))
        self._save_settings()
        _log.info(f"语音模式已切换为: {self.get_voice_mode_name()}")

    def toggle_mute(self):
        self.mute_mode = not self.mute_mode
        self._save_settings()
        _log.info(f"静音模式已切换为: {'开启' if self.mute_mode else '关闭'}")
        return self.mute_mode

    def get_voice_mode_name(self):
        return {0: "本地克隆", 1: "晓晓女声", 2: "云希男声", 3: "混合模式"}.get(self.voice_mode, "未知")

    def stop(self):
        self.audio_player.stop()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception as e:
            _log.debug(f"[Voice] sounddevice 停止失败（stop 清理）: {e}")

    def shutdown(self):
        self._active = False
        self.stop()

    def _get_voice_style(self, emotion: str = None) -> str:
        """根据情感返回语音风格 - 支持30+种情感的精细映射"""
        emotion_map = {
            # 正面情感 - 使用欢快/友好语调
            'joy': 'cheerful',
            'love': 'tender',
            'excitement': 'cheerful',
            'amusement': 'cheerful',
            'playfulness': 'playful',
            'gratitude': 'friendly',
            'pride': 'confident',
            'hope': 'hopeful',
            'curiosity': 'curious',
            'surprise': 'surprised',
            'contentment': 'calm',
            'serenity': 'calm',
            'warmth': 'tender',
            'wonder': 'curious',
            'pleasure': 'cheerful',
            
            # 负面情感 - 使用低沉/温柔语调
            'sadness': 'sad',
            'loneliness': 'sad',
            'anger': 'serious',
            'fear': 'nervous',
            'anxiety': 'nervous',
            'disappointment': 'sad',
            'frustration': 'serious',
            
            # 中性/复杂情感 - 使用对应语调
            'shyness': 'shy',
            'embarrassment': 'shy',
            'determination': 'confident',
            'trust': 'friendly',
            'anticipation': 'hopeful',
            'boredom': 'calm',
            'fatigue': 'tired',
            'nostalgia': 'nostalgic',
            'empathy': 'tender',
            'vulnerability': 'tender',
            'resilience': 'confident',
        }
        return emotion_map.get(emotion, 'friendly')

    def synthesize(self, text: str, emotion: str = None) -> str | None:
        """合成语音并返回音频URL - 供外部调用"""
        return self.synthesize_to_file(text, emotion)

    def _synthesize_edge_to_file(self, text: str, emotion: str, output_path: str) -> bool:
        """使用 edge-tts 合成语音到指定文件（服务器场景主引擎）

        - 不本地播放，仅写文件，返回路径供前端通过 URL 拉取播放
        - 按情感选择 rate/pitch/volume，落实"情感表达通过语音"铁律
        - 成功返回 True，失败返回 False（由调用方降级到 fallback_chain）

        【历史教训】此前 synthesize_to_file 完全没有 edge-tts 分支，
        导致"晓晓女声"模式实际走百度人机声音。此处为服务器场景的 edge-tts 主入口。
        """
        if not self._edge_tts:
            _log.warning("edge-tts 未安装或不可用，跳过在线情感语音")
            return False
        if not text or not text.strip():
            return False

        # 【P0 提速】无代理时直接跳过 edge-tts：中国大陆网络连不上微软
        # speech.platform.bing.com（实测 Cannot connect / 403），每次尝试 + 重试
        # 浪费 2-3 秒才降级到百度。检测到无 HTTPS_PROXY/HTTP_PROXY 时，
        # 直接走 fallback_chain，让对话回复快 2-3 秒。
        _proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY') or os.environ.get('https_proxy') or os.environ.get('http_proxy')
        if not _proxy:
            _log.info(
                "未检测到 HTTPS_PROXY/HTTP_PROXY，跳过 edge-tts 直接降级"
                "（微软语音服务对中国大陆 IP 有区域限制；如需晓晓请配置代理后重启）"
            )
            return False

        # 情感 → 风格 → 语速/音调/音量（复用现有映射，单一真相源）
        voice_style = self._get_voice_style(emotion)
        params = self._get_ssml_params(voice_style)
        voice = VoiceConfig.EDGE_TTS_VOICE  # zh-CN-XiaoxiaoNeural（晓晓）

        # 【晓晓在线修复】edge-tts 连接微软服务器在部分网络下被墙/403，
        # 从环境变量读取 HTTPS_PROXY/HTTP_PROXY 并传给 Communicate，配代理后晓晓即可用
        _log.info(
            f"edge-tts 文件合成开始，文本长度: {len(text)}, voice: {voice}, "
            f"情感: {emotion}, 风格: {voice_style}, params: {params}"
            + (f", proxy: {_proxy}" if _proxy else "")
        )

        result = {'ok': False}

        def _run():
            async def _do():
                communicate = self._edge_tts.Communicate(
                    text, voice,
                    rate=params['rate'],
                    pitch=params['pitch'],
                    volume=params['volume'],
                    proxy=_proxy,
                )
                await asyncio.wait_for(communicate.save(output_path), timeout=10)

            # 【声音优化】edge-tts 网络失败时重试无意义（403/连不上为环境问题），
            # 仅重试 1 次即降级到百度，避免 6-9 秒等待拖慢响应
            for retry in range(1):
                try:
                    asyncio.run(_do())
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 500:
                        result['ok'] = True
                        return
                    else:
                        _log.warning(f"edge-tts 文件太小或不存在（重试 {retry+1}/1）")
                except asyncio.TimeoutError:
                    _log.warning(f"edge-tts 文件合成超时（重试 {retry+1}/1）")
                except Exception as e:
                    _log.warning(f"edge-tts 文件合成异常（重试 {retry+1}/1）: {e}")
                if retry < 2:
                    time.sleep(0.5)

        # 在独立线程中跑异步调用，避免与 FastAPI/uvicorn 主事件循环冲突
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=15)

        if result['ok']:
            _log.info(f"edge-tts 文件合成成功: {output_path}")
        else:
            _log.warning("edge-tts 文件合成失败，将降级到 fallback_chain")
        return result['ok']

    # ------------------------------------------------------------------
    #  【L4】CosyVoice2 流式引擎（DashScope WebSocket，首包 ~150ms）
    # ------------------------------------------------------------------
    def _cosyvoice_enabled(self) -> bool:
        """CosyVoice2 可用性：websocket-client 已装 + config 未关闭 + .env 有 Key"""
        if not _WEBSOCKET_OK:
            return False
        if self._voice_cfg.get('cosyvoice_enabled') is False:
            return False
        return bool(os.environ.get('DASHSCOPE_API_KEY', '').strip())

    def _synthesize_cosyvoice_to_file(self, text: str, emotion: str, output_path: str) -> bool:
        """CosyVoice2 流式合成（DashScope WS 协议，边合成边收音频块）。

        - 情感直通：emotion → 语音风格 → 语速/音调/音量 + <|endofprompt|> 指令前缀
        - instruct 失败时自动去掉指令前缀重试一次（部分音色不支持 instruct）
        - 任何失败返回 False，由调用方降级到后续引擎，绝不阻塞对话
        """
        if not self._cosyvoice_enabled():
            return False
        if not text or not text.strip():
            return False

        api_key = os.environ.get('DASHSCOPE_API_KEY', '').strip()
        model = self._voice_cfg.get('cosyvoice_model', 'cosyvoice-v2')
        voice = self._voice_cfg.get('cosyvoice_voice', 'longxiaochun_v2')
        instruct_on = self._voice_cfg.get('cosyvoice_emotion_instruct', True)

        style = self._get_voice_style(emotion)
        params = _COSYVOICE_STYLE_PARAMS.get(style, _COSYVOICE_STYLE_PARAMS['default'])
        instruct = params.get('instruct') if instruct_on else None

        def _attempt(with_instruct: bool) -> bool:
            full_text = f"{instruct}<|endofprompt|>{text}" if (with_instruct and instruct) else text
            task_id = uuid.uuid4().hex
            audio = bytearray()
            ws = None
            try:
                ws = _ws_lib.create_connection(
                    _DASHSCOPE_TTS_WS_URL,
                    header=[
                        f'Authorization: bearer {api_key}',
                        'X-DashScope-DataInspection: enable',
                        'user-agent: miaoyi-tts',
                    ],
                    timeout=15,
                )

                def _send(action: str, payload_input: dict | None = None):
                    ws.send(json.dumps({
                        'headers': {'action': action, 'task_id': task_id, 'streaming': 'out'},
                        'payload': {
                            'task_group': 'audio', 'task': 'tts',
                            'function': 'SpeechSynthesizer', 'model': model,
                            'parameters': {
                                'text_type': 'PlainText', 'voice': voice,
                                'format': _DASHSCOPE_TTS_FORMAT,
                                'rate': params['rate'], 'pitch': params['pitch'],
                                'volume': params['volume'],
                            },
                            'input': payload_input or {},
                        },
                    }, ensure_ascii=False))

                _send('run-task')
                # 等待 task-started（期间可能已混入音频帧，一并收集）
                started = False
                deadline = time.time() + 10
                while time.time() < deadline:
                    frame = ws.recv()
                    if isinstance(frame, (bytes, bytearray)):
                        audio.extend(frame)
                        continue
                    event = (json.loads(frame).get('headers') or {}).get('event', '')
                    if event == 'task-started':
                        started = True
                        break
                    if event == 'task-failed':
                        reason = json.loads(frame).get('payload', {}).get('message', 'unknown')
                        _log.warning(f"CosyVoice2 task-failed: {reason}")
                        return False
                if not started:
                    _log.warning("CosyVoice2 等待 task-started 超时")
                    return False

                # 流式发送文本并结束输入，音频块随后经 on_data 式二进制帧持续返回
                _send('continue-task', {'text': full_text})
                _send('finish-task')

                deadline = time.time() + 20
                while time.time() < deadline:
                    try:
                        frame = ws.recv()
                    except Exception:
                        break  # 超时/连接关闭 → 按已收内容判断
                    if isinstance(frame, (bytes, bytearray)):
                        audio.extend(frame)
                        continue
                    event = (json.loads(frame).get('headers') or {}).get('event', '')
                    if event == 'task-finished':
                        if len(audio) > 500:
                            with open(output_path, 'wb') as f:
                                f.write(bytes(audio))
                            return True
                        return False
                    if event == 'task-failed':
                        reason = json.loads(frame).get('payload', {}).get('message', 'unknown')
                        _log.warning(f"CosyVoice2 task-failed: {reason}")
                        return False
                _log.warning("CosyVoice2 等待合成结束超时")
                return False
            except Exception as e:
                _log.warning(f"CosyVoice2 合成异常: {e}")
                return False
            finally:
                try:
                    if ws is not None:
                        ws.close()
                except Exception:
                    pass

        _log.info(f"CosyVoice2 开始合成: '{text[:30]}...' 情感: {emotion} 风格: {style} 音色: {voice}")
        if _attempt(True):
            _log.info("CosyVoice2 流式合成成功（含情感指令）")
            return True
        if instruct:
            # instruct 不被当前音色支持时去掉指令重试一次，保住"能用"再谈"有情"
            _log.info("CosyVoice2 指令合成失败，去掉 instruct 重试")
            if _attempt(False):
                _log.info("CosyVoice2 流式合成成功（无情感指令）")
                return True
        return False

    # ------------------------------------------------------------------
    #  【L4】GPT-SoVITS 本地引擎（可选，api_v2 /tts 流式接口）
    # ------------------------------------------------------------------
    def _gptsovits_url(self) -> str:
        return (self._voice_cfg.get('gptsovits_url') or '').strip().rstrip('/')

    def _synthesize_gptsovits_to_file(self, text: str, output_path: str) -> bool:
        """GPT-SoVITS 本地合成（config: voice.gptsovits_url 配置后启用）。

        参考音频经 config 传入：gptsovits_ref_audio / gptsovits_prompt_text。
        未配置 url 或服务未启动 → 返回 False 走后续降级链。
        """
        base = self._gptsovits_url()
        if not base or not _REQUESTS_OK or not text or not text.strip():
            return False
        payload = {
            'text': text,
            'text_lang': 'zh',
            'ref_audio_path': self._voice_cfg.get('gptsovits_ref_audio', ''),
            'prompt_text': self._voice_cfg.get('gptsovits_prompt_text', ''),
            'prompt_lang': 'zh',
            'streaming': True,
        }
        try:
            resp = req_lib.post(f'{base}/tts', json=payload, stream=True, timeout=(2, 60))
            if resp.status_code != 200:
                _log.debug(f"GPT-SoVITS HTTP {resp.status_code}")
                return False
            tmp = f'{output_path}.gs.part'
            size = 0
            with open(tmp, 'wb') as f:
                for chunk in resp.iter_content(4096):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
            if size > 500:
                os.replace(tmp, output_path)
                _log.info(f"GPT-SoVITS 本地合成成功: {output_path}")
                return True
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception as e:
            _log.debug(f"GPT-SoVITS 合成失败（降级）: {e}")
        return False

    def _get_cache_salt(self) -> str:
        """部署级随机盐：让缓存文件名无法由文本内容猜出。

        【P2加固】/voice_cache 为匿名静态挂载（<audio> 无法带请求头鉴权），
        旧文件名 cache_md5(text+emotion) 意味着"知道文本即可拼出 URL"。
        加盐后 URL 不可预测；盐持久化在缓存目录 .salt，跨重启缓存仍命中。
        """
        try:
            salt_file = os.path.join(self._cache_dir, '.salt')
            if os.path.exists(salt_file):
                with open(salt_file, 'r', encoding='utf-8') as fh:
                    s = fh.read().strip()
                    if s:
                        return s
            s = uuid.uuid4().hex
            with open(salt_file, 'w', encoding='utf-8') as fh:
                fh.write(s)
            return s
        except Exception:
            return 'miaoyi-static-salt'

    def synthesize_to_file(self, text: str, emotion: str = None) -> str | None:
        cleaned_text = text_for_tts(text)
        if not cleaned_text:
            return None

        try:
            cache_dir = self._cache_dir
            os.makedirs(cache_dir, exist_ok=True)

            cache_key = hashlib.md5(
                f"{self._get_cache_salt()}_{cleaned_text}_{emotion or 'default'}".encode()
            ).hexdigest()
            cache_path = os.path.join(cache_dir, f'cache_{cache_key}.mp3')
            
            if os.path.exists(cache_path) and os.path.getsize(cache_path) > 500:
                _log.info(f"语音缓存命中: {cache_key[:8]}")
                return cache_path

            # ===== 【L4】引擎 1：CosyVoice2 流式合成（DashScope，情感直通+流式收包，首包 ~150ms）=====
            # 直接写入 cache_path，使后续相同文本+情感命中缓存；失败自动降级
            if self._synthesize_cosyvoice_to_file(cleaned_text, emotion, cache_path):
                _log.info(f"CosyVoice2 合成成功并写入缓存: {cache_key[:8]}")
                return cache_path

            # ===== 【L4】引擎 2：GPT-SoVITS 本地（可选，voice.gptsovits_url 配置后启用）=====
            # 产出 wav，单独缓存路径（扩展名与容器匹配，浏览器 Content-Type 才正确）
            if self._gptsovits_url():
                gptsovits_path = os.path.join(cache_dir, f'sovits_{cache_key}.wav')
                if os.path.exists(gptsovits_path) and os.path.getsize(gptsovits_path) > 500:
                    return gptsovits_path
                if self._synthesize_gptsovits_to_file(cleaned_text, gptsovits_path):
                    return gptsovits_path

            # ===== 主引擎：edge-tts 在线情感女声（晓晓 Neural）=====
            # 直接写入 cache_path，使后续相同文本+情感命中缓存
            if self._synthesize_edge_to_file(cleaned_text, emotion, cache_path):
                _log.info(f"edge-tts 合成成功并写入缓存: {cache_key[:8]}")
                return cache_path
            _log.info("edge-tts 不可用或失败，进入 fallback_chain 降级。"
                      "如需晓晓在线女声，请配置 HTTPS_PROXY 代理环境变量后重启（微软语音服务对中国大陆 IP 有区域限制）")

            timestamp = int(time.time() * 1000)
            output_path = os.path.join(cache_dir, f'tts_{timestamp}.mp3')

            voice_style = self._get_voice_style(emotion)
            fallback_chain = self._get_fallback_chain(self.voice_mode)

            for fallback_method in fallback_chain:
                try:
                    _log.info(f"尝试引擎: {fallback_method.__name__}")
                    
                    if fallback_method.__name__ == '_speak_baidu' or 'baidu' in fallback_method.__name__:
                        # 【声音修复】百度 gettts 发音人：0=女声，1=男声（此前女声误用 per=1 男声）
                        per = 0 if 'female' in fallback_method.__name__ else 1
                        tmp_path = os.path.join(cache_dir, f"baidu_{timestamp}.mp3")
                        if _REQUESTS_OK:
                            url = f"https://fanyi.baidu.com/gettts?lan=zh&text={quote_plus(cleaned_text)}&spd=4&source=web&per={per}"
                            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                            response = req_lib.get(url, headers=headers, timeout=10)
                            if response.status_code == 200 and len(response.content) > 500:
                                with open(tmp_path, 'wb') as f:
                                    f.write(response.content)
                                _log.info(f"百度TTS音频已保存: {tmp_path}")
                                return tmp_path
                                
                    elif fallback_method.__name__ == '_speak_gtts':
                        if _GTTS_OK:
                            tmp_path = os.path.join(cache_dir, f"gtts_{timestamp}.mp3")
                            tts = gTTS(text=cleaned_text, lang='zh-CN')
                            tts.save(tmp_path)
                            if os.path.exists(tmp_path):
                                _log.info(f"gTTS音频已保存: {tmp_path}")
                                return tmp_path
                                
                    elif fallback_method.__name__ == '_speak_local':
                        if self._pyttsx3_ok:
                            output_path_wav = output_path.replace('.mp3', '.wav')
                            engine = pyttsx3.init()
                            engine.save_to_file(cleaned_text, output_path_wav)
                            engine.runAndWait()
                            if os.path.exists(output_path_wav):
                                _log.info(f"pyttsx3音频已保存: {output_path_wav}")
                                return output_path_wav
                                
                except Exception as e:
                    _log.warning(f"引擎 {fallback_method.__name__} 失败: {e}")
                    continue
            
            _log.warning("所有TTS引擎均失败")
            return None
                
        except Exception as e:
            _log.error(f"TTS文件合成失败: {e}")
            return None

    # ------------------------------------------------------------------
    #  分段流式合成（首句优先）
    # ------------------------------------------------------------------
    def start_segment_synthesis(self, task_id: str, segments: list[str],
                                emotion: str = None, first_path: str | None = None) -> None:
        """注册分段任务并后台逐段合成 segments[1:]（segments[0] 已由调用方同步合成）。

        paths[i] 为 None 表示该段所有引擎都失败，前端收到 null 后停止续播。
        """
        with _SEGMENT_TASKS_LOCK:
            # 顺手清理超过 10 分钟的旧任务，防止注册表无限增长
            now = time.time()
            for k in [k for k, v in _SEGMENT_TASKS.items() if now - v.get('ts', now) > 600]:
                _SEGMENT_TASKS.pop(k, None)
            _SEGMENT_TASKS[task_id] = {
                'total': len(segments),
                'paths': {0: first_path},
                'failed': False,
                'ts': now,
            }

        def _worker():
            for i in range(1, len(segments)):
                try:
                    path = self.synthesize_to_file(segments[i], emotion=emotion)
                    with _SEGMENT_TASKS_LOCK:
                        task = _SEGMENT_TASKS.get(task_id)
                        if task is None:
                            return
                        task['paths'][i] = path if (path and os.path.exists(path)) else None
                        task['ts'] = time.time()
                except Exception as e:
                    _log.warning(f"分段合成失败 task={task_id[:8]} seg={i}: {e}")
                    with _SEGMENT_TASKS_LOCK:
                        task = _SEGMENT_TASKS.get(task_id)
                        if task is not None:
                            task['paths'][i] = None
                            task['failed'] = True
                    break
            _log.info(f"分段合成任务结束: {task_id[:8]} ({len(segments)} 段)")

        threading.Thread(target=_worker, daemon=True, name=f'seg-{task_id[:8]}').start()

    def wait_segment(self, task_id: str, index: int, timeout: float = 15.0) -> str | None:
        """阻塞等待第 index 段合成结束，返回文件路径。

        None 语义：任务丢失 / 该段失败 / 任务已失败 / 全部结束但无此段 / 超时
        —— 前端收到后停止续播。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with _SEGMENT_TASKS_LOCK:
                info = _SEGMENT_TASKS.get(task_id)
                if info is None:
                    return None
                if index in info['paths']:
                    return info['paths'][index]
                if info['failed'] or len(info['paths']) >= info['total']:
                    return None
            time.sleep(0.25)
        return None

    def clean_text_for_tts(self, text: str) -> str:
        return text_for_tts(text)
    
    def get_status(self) -> dict[str, Any]:
        return {
            'voice_mode': self.voice_mode,
            'voice_mode_name': self.get_voice_mode_name(),
            'mute_mode': self.mute_mode,
            'available_modes': [
                {'mode': 0, 'name': '本地克隆'},
                {'mode': 1, 'name': '晓晓女声'},
                {'mode': 2, 'name': '云希男声'},
                {'mode': 3, 'name': '混合模式'},
            ],
            'edge_tts_available': self._edge_tts is not None,
            'pyttsx3_available': self._pyttsx3_ok,
            'gtts_available': _GTTS_OK,
            'requests_available': _REQUESTS_OK,
            # 【L4】流式引擎可用性（供前端/管理页展示）
            'cosyvoice_available': self._cosyvoice_enabled(),
            'gptsovits_available': bool(self._gptsovits_url()),
        }
    
    def register_asr_callback(self, callback):
        self._asr_callback = callback
    
    def transcribe_file(self, file_path: str) -> str | None:
        try:
            _log.info(f"尝试语音识别: {file_path}")
            # 【本地 ASR 优先】faster-whisper 离线识别，不依赖 Google（网络被墙时仍可用）
            try:
                # 【2026-10 修复】模型默认从 huggingface.co 下载，国内服务器连不上
                # 导致 WhisperModel 加载失败；改用 hf-mirror.com 镜像（仅在未显式
                # 配置 HF_ENDPOINT 时生效，模型已在服务部署时预下载到本地缓存）
                os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
                import shutil
                import subprocess
                from faster_whisper import WhisperModel
                if self._whisper_model is None:
                    _log.info("加载本地 Whisper 模型（small, int8）...")
                    # 优先用随项目预下载的本地模型（models/faster-whisper-small），
                    # 运行时零联网；也可用环境变量 MIAOYI_WHISPER_DIR 指定
                    from pathlib import Path
                    _local_dir = os.environ.get("MIAOYI_WHISPER_DIR") or str(
                        Path(__file__).resolve().parent.parent / "models" / "faster-whisper-small"
                    )
                    _model_ref = (_local_dir
                                  if os.path.isfile(os.path.join(_local_dir, "model.bin"))
                                  else "small")
                    self._whisper_model = WhisperModel(_model_ref, device="cpu", compute_type="int8")

                def _run_fw(path: str) -> str:
                    segs, _ = self._whisper_model.transcribe(path, language="zh")
                    return "".join(s.text for s in segs).strip()

                try:
                    text = _run_fw(file_path)
                except Exception as decode_e:
                    # webm/opus 等容器 PyAV 解不开时，用 ffmpeg 转 16k 单声道 wav 再试
                    if shutil.which("ffmpeg"):
                        wav_path = file_path + ".conv.wav"
                        _log.warning(f"PyAV 解码失败（{decode_e}），ffmpeg 转 wav 重试")
                        subprocess.run(
                            ["ffmpeg", "-y", "-i", file_path, "-ar", "16000", "-ac", "1",
                             "-c:a", "pcm_s16le", wav_path],
                            capture_output=True, timeout=30,
                        )
                        text = _run_fw(wav_path)
                    else:
                        raise
                if text:
                    text = self._post_process_asr(text)
                    _log.info(f"Whisper 语音识别成功: {text}")
                    return text
            except ImportError:
                _log.debug("faster-whisper 未安装，回退 speech_recognition")
            except Exception as e:
                _log.warning(f"Whisper 识别失败（回退）: {e}")
            # 降级：speech_recognition（Google，可能因网络被墙不可用）
            try:
                import speech_recognition as sr
                r = sr.Recognizer()
                with sr.AudioFile(file_path) as source:
                    audio_data = r.record(source)
                    text = r.recognize_google(audio_data, language='zh-CN')
                    text = self._post_process_asr(text)
                    _log.info(f"语音识别成功: {text}")
                    return text
            except ImportError:
                _log.warning("speech_recognition 未安装")
            except Exception as e:
                _log.warning(f"语音识别失败: {e}")
            return None
        except Exception as e:
            _log.error(f"语音识别异常: {e}")
            return None

    def _post_process_asr(self, text: str) -> str:
        corrections = {
            '你嚎': '你好', '你好啊': '你好', '你好呀': '你好',
            '呢': '', '嘛': '', '吧': '', '啊': '',
            '啥': '什么', '咋': '怎么', '为啥': '为什么',
            '嗯': '', '嗯嗯': '', '哦': '', '哦哦': '',
        }
        for wrong, correct in corrections.items():
            text = text.replace(wrong, correct)
        text = text.strip().rstrip('。！？，、')
        return text


voice_manager = VoiceManager()