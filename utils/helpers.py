"""喵艺工具函数模块"""
import os
import random
import re
import string
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, List, Optional, Tuple

from config import NLTK_AVAILABLE, Config
from utils.logger import logger

# 时区设置
tz_shanghai = timezone(timedelta(hours=8))


def get_now() -> datetime:
    """获取当前时间（上海时区）"""
    return datetime.now(tz_shanghai)


def get_friendly_time() -> str:
    """获取友好的时间格式"""
    now = get_now()
    w = ["一", "二", "三", "四", "五", "六", "日"][now.weekday()]
    return now.strftime("%Y年%m月%d日 %H:%M:%S") + f"，星期{w}"


def get_time_period(hour: int | None = None) -> str:
    """获取时间段描述"""
    if hour is None:
        hour = get_now().hour
    if 0 <= hour < 5:
        return "深夜"
    elif 5 <= hour < 7:
        return "凌晨"
    elif 7 <= hour < 9:
        return "早上"
    elif 9 <= hour < 11:
        return "上午"
    elif 11 <= hour < 13:
        return "中午"
    elif 13 <= hour < 17:
        return "下午"
    elif 17 <= hour < 19:
        return "傍晚"
    elif 19 <= hour < 23:
        return "晚上"
    return "深夜"


def get_smart_timestamp(dt: datetime | None = None) -> str:
    """获取智能时间戳"""
    if dt is None:
        dt = datetime.now()
    now = datetime.now()
    diff = now - dt
    if diff < timedelta(minutes=1):
        return "刚刚"
    elif diff < timedelta(hours=1):
        return f"{int(diff.total_seconds() // 60)}分钟前"
    elif diff < timedelta(days=1) and dt.date() == now.date():
        return dt.strftime("%H:%M")
    elif diff < timedelta(days=2) and (now - timedelta(1)).date() == dt.date():
        return "昨天 " + dt.strftime("%H:%M")
    elif diff < timedelta(days=7):
        w = ["一", "二", "三", "四", "五", "六", "日"][dt.weekday()]
        return f"星期{w} " + dt.strftime("%H:%M")
    return dt.strftime("%Y年%m月%d日 %H:%M")


def find_avatar_file() -> str | None:
    """查找头像文件"""
    for ext in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
        p = 'avatar' + ext
        if os.path.exists(p):
            return p
    return None


def get_circle_avatar(img_path: str, size: tuple[int, int] = (40, 40)) -> Any | None:
    """获取圆形头像"""
    if not Config.PIL_AVAILABLE or not img_path or not os.path.exists(img_path):
        return None
    try:
        from PIL import Image, ImageDraw, ImageTk
        img = Image.open(img_path).resize(size, Image.Resampling.LANCZOS)
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size[0], size[1]), fill=255)
        img.putalpha(mask)
        border = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(border)
        draw.ellipse([2, 2, size[0] - 3, size[1] - 3], outline=(255, 255, 255, 150), width=2)
        border.paste(img, (0, 0), img)
        return ImageTk.PhotoImage(border)
    except Exception as e:
        logger.warning(f"加载头像失败: {e}")
        return None


def clean_display_text(text: str) -> str:
    """清理显示文本"""
    if not text:
        return ""
    text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', '', text)
    text = re.sub(r'\[/?[^\]]+\]', '', text)
    text = re.sub(r'【[^】]+】', '', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def extract_topics(text: str, max_topics: int = 5) -> list[str]:
    """从文本中提取话题"""
    if not text or not NLTK_AVAILABLE:
        return []
    try:
        import nltk
        words = nltk.word_tokenize(text)
        chinese_words = re.findall(r'[一-龥]{2,}', text)
        all_words = chinese_words + [w for w in words if len(w) > 2]
        stopwords = {'什么', '怎么', '为什么', '这个', '那个', '一个', '一些', '可以', '应该', '但是', '因为', '所以',
                     '就是', '还是', '已经', '然后', '接着', '之后', '之前'}
        topics = [w for w in all_words if w not in stopwords and len(w) >= 2]
        unique_topics = list(dict.fromkeys(topics))
        return unique_topics[:max_topics]
    except Exception as e:
        logger.warning(f"提取话题失败: {e}")
        return []


def calculate_text_similarity(text1: str, text2: str) -> float:
    """计算文本相似度"""
    if not text1 or not text2:
        return 0.0
    try:
        words1 = set(re.findall(r'[一-龥a-zA-Z0-9]{2,}', text1.lower()))
        words2 = set(re.findall(r'[一-龥a-zA-Z0-9]{2,}', text2.lower()))
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0
    except Exception as e:
        logger.warning(f"计算相似度失败: {e}")
        return 0.0


def retry_with_backoff(max_retries: int = Config.MAX_RETRIES,
                       delay: float = Config.RETRY_DELAY,
                       backoff_factor: float = Config.RETRY_BACKOFF_FACTOR,
                       exceptions: tuple = (Exception,)):
    """带退避的重试装饰器"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = delay * (backoff_factor ** attempt)
                        logger.debug(f"重试 {attempt + 1}/{max_retries}, 等待 {wait_time:.2f}s")
                        time.sleep(wait_time)
            raise last_exception

        return wrapper

    return decorator


def safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """安全除法"""
    return a / b if b != 0 else default


def normalize_value(value: float, min_val: float, max_val: float) -> float:
    """归一化值"""
    if max_val == min_val:
        return 0.0
    return (value - min_val) / (max_val - min_val)


def clamp(value: float, min_val: float, max_val: float) -> float:
    """限制值范围"""
    return max(min_val, min(max_val, value))


def random_delay(min_delay: float, max_delay: float):
    """随机延迟"""
    delay = random.uniform(min_delay, max_delay)
    time.sleep(delay)


def generate_id(prefix: str = "id") -> str:
    """生成唯一ID"""
    timestamp = str(int(time.time() * 1000))
    random_part = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    return f"{prefix}_{timestamp}_{random_part}"