#!/usr/bin/env python3
"""
  🐱 喵艺时间意识系统 - 完整迁移自main.py的EnhancedTimeAwareness
  包含网络时间同步+时段响应+节日检测+问候语
"""
import logging
import re
import threading
import time

_log = logging.getLogger(__name__)


class NetworkTimeSync:
    def __init__(self):
        self._offset = 0
        self._last_sync = 0
        self._lock = threading.Lock()
    
    def sync(self):
        try:
            import ntplib
            client = ntplib.NTPClient()
            response = client.request('pool.ntp.org', version=3)
            with self._lock:
                self._offset = response.offset
                self._last_sync = time.time()
            _log.info(f"时间同步成功，偏移量: {self._offset:.3f}秒")
        except Exception as e:
            _log.debug(f"时间同步失败，使用本地时间: {e}")
    
    def get_time(self):
        with self._lock:
            return time.time() + self._offset


class EnhancedTimeAwareness:
    CHINESE_HOLIDAYS = {
        (1, 1): {'name': '元旦', 'type': 'national'},
        (2, 14): {'name': '情人节', 'type': 'western'},
        (3, 8): {'name': '妇女节', 'type': 'special'},
        (3, 12): {'name': '植树节', 'type': 'special'},
        (4, 1): {'name': '愚人节', 'type': 'western'},
        (5, 1): {'name': '劳动节', 'type': 'national'},
        (5, 4): {'name': '青年节', 'type': 'special'},
        (6, 1): {'name': '儿童节', 'type': 'special'},
        (7, 1): {'name': '建党节', 'type': 'special'},
        (8, 1): {'name': '建军节', 'type': 'special'},
        (9, 10): {'name': '教师节', 'type': 'special'},
        (10, 1): {'name': '国庆节', 'type': 'national'},
        (11, 11): {'name': '光棍节', 'type': 'fun'},
        (12, 25): {'name': '圣诞节', 'type': 'western'},
    }
    
    LUNAR_HOLIDAYS = {
        '春节': {'type': 'traditional', 'importance': 'high'},
        '元宵节': {'type': 'traditional', 'importance': 'medium'},
        '清明节': {'type': 'traditional', 'importance': 'high'},
        '端午节': {'type': 'traditional', 'importance': 'high'},
        '七夕节': {'type': 'traditional', 'importance': 'medium'},
        '中秋节': {'type': 'traditional', 'importance': 'high'},
        '重阳节': {'type': 'traditional', 'importance': 'medium'},
    }

    PERIOD_CN = {
        'dawn': '清晨', 'morning': '上午', 'noon': '中午',
        'afternoon': '下午', 'evening': '傍晚', 'night': '深夜',
    }

    def __init__(self):
        self.time_sync = NetworkTimeSync()
        self._current_period = ''
        self._holiday_today = None
        self._season = ''
        self._last_update = 0
        self._lock = threading.Lock()
    
    def update(self):
        with self._lock:
            now = time.localtime()
            self._current_period = self._get_period(now)
            self._holiday_today = self._check_holiday(now)
            self._season = self._get_season(now)
            self._last_update = time.time()
    
    def _get_period(self, now) -> str:
        hour = now.tm_hour
        if 5 <= hour < 8:
            return 'dawn'
        elif 8 <= hour < 12:
            return 'morning'
        elif 12 <= hour < 14:
            return 'noon'
        elif 14 <= hour < 18:
            return 'afternoon'
        elif 18 <= hour < 22:
            return 'evening'
        else:
            return 'night'
    
    def _check_holiday(self, now) -> dict or None:
        key = (now.tm_mon, now.tm_mday)
        if key in self.CHINESE_HOLIDAYS:
            return self.CHINESE_HOLIDAYS[key]
        return None
    
    def _get_season(self, now) -> str:
        month = now.tm_mon
        if 3 <= month <= 5:
            return 'spring'
        elif 6 <= month <= 8:
            return 'summer'
        elif 9 <= month <= 11:
            return 'autumn'
        else:
            return 'winter'
    
    def get_period_greeting(self) -> str:
        greetings = {
            'dawn': '早上好呀，新的一天开始了！☀️',
            'morning': '上午好！今天也要元气满满哦！🌞',
            'noon': '中午好！记得好好吃饭休息哦！🍚',
            'afternoon': '下午好呀！工作学习辛苦了！☕',
            'evening': '晚上好！今天过得怎么样？🌙',
            'night': '夜深了，注意休息哦，晚安～💤',
        }
        return greetings.get(self._current_period, '你好！')
    
    def get_holiday_greeting(self) -> str:
        if self._holiday_today:
            holiday_name = self._holiday_today['name']
            holiday_type = self._holiday_today['type']
            
            specific_greetings = {
                '元旦': '新年快乐！🎉愿新的一年万事如意！',
                '情人节': '情人节快乐！💝有没有收到礼物呀？',
                '妇女节': '妇女节快乐！👩‍🦰女神节安康！',
                '劳动节': '劳动节快乐！🎉好好休息一下吧！',
                '儿童节': '儿童节快乐！🎈保持童心哦！',
                '国庆节': '国庆节快乐！🇨🇳祝福祖国繁荣昌盛！',
                '圣诞节': '圣诞快乐！🎄祝你平安喜乐！',
            }
            
            if holiday_name in specific_greetings:
                return specific_greetings[holiday_name]
            
            if holiday_type == 'traditional':
                return f"{holiday_name}快乐！🎊传统节日安康！"
            elif holiday_type == 'national':
                return f"{holiday_name}快乐！🎉好好享受假期！"
            elif holiday_type == 'fun':
                return f"{holiday_name}快乐！😂今天有什么特别的安排吗？"
            
            return f"{holiday_name}快乐！🎊"
        return ''
    
    def get_context_str(self) -> str:
        now = time.localtime()
        period_names = {
            'dawn': '清晨',
            'morning': '上午',
            'noon': '中午',
            'afternoon': '下午',
            'evening': '傍晚',
            'night': '深夜',
        }
        season_names = {
            'spring': '春天',
            'summer': '夏天',
            'autumn': '秋天',
            'winter': '冬天',
        }
        
        date_str = time.strftime('%Y年%m月%d日 %A', now)
        period_str = period_names.get(self._current_period, '白天')
        season_str = season_names.get(self._season, '季节')
        
        parts = [f"{date_str}，{season_str}，{period_str}"]
        
        if self._holiday_today:
            parts.append(f"今天是{self._holiday_today['name']}")
        
        return " | ".join(parts)
    
    def get_period_directives(self) -> str:
        """时段语气指令（注入系统提示词，让语气随时段自然变化）"""
        directives = {
            'dawn': '现在是清晨（5-8点），语气清新活泼、带着刚睡醒的慵懒或元气。如果这是用户今天的第一条消息，可以自然地以「早安」开场，别太刻意。',
            'morning': '现在是上午，语气可以元气满满，稍微活泼一些。',
            'noon': '现在是中午，语气轻松自然，可以顺口关心一下对方吃饭了没。',
            'afternoon': '现在是下午，如果用户显得疲惫，可以关心一下、打打气。',
            'evening': '现在是傍晚，语气可以更温暖放松，可以问问对方今天过得怎么样。',
            'night': '现在是深夜（22点后），语气要更轻、更慢、更温柔体贴。如果用户还在熬夜，轻轻关心一次休息就好，不要反复唠叨或说教。',
        }
        return directives.get(self._current_period, '')

    def get_period_cn(self) -> str:
        """当前时段的中文名（清晨/上午/中午/下午/傍晚/深夜）"""
        self.update()
        return self.PERIOD_CN.get(self._current_period, '白天')

    def get_holiday_note(self) -> str:
        """节日提示（注入系统提示词）：让问候自然带上节日，不做广播式祝福"""
        self.update()
        if self._holiday_today:
            return f"今天是{self._holiday_today['name']}，问候时可以自然带一句，不要广播式的节日祝福。"
        return ''

    def is_night(self) -> bool:
        return self._current_period == 'night'
    
    def is_morning(self) -> bool:
        return self._current_period == 'morning' or self._current_period == 'dawn'
    
    def has_holiday_today(self) -> bool:
        return self._holiday_today is not None
    
    def save(self):
        pass
    
    def load(self):
        pass
    
    def set_last_chat_time(self, timestamp):
        self._last_chat_time = timestamp
    
    def get_period(self):
        self.update()
        return self._current_period
    
    def time_response(self, text):
        """时间查询直接回复 — 返回用户可读的中文时间，不走 LLM"""
        self.update()
        import datetime as _dt
        now = _dt.datetime.now()
        # 【Bug 修复】now.weekday() 返回 0=周一, 1=周二, ..., 6=周日
        # 旧代码 ['日','一','二','三','四','五','六'] 错位：0→日(应为一), 1→一(应为二)
        # 导致周二被说成"星期一"。正确映射: ['一','二','三','四','五','六','日']
        weekday_cn = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]

        # 时间查询（几点/什么时间）
        if re.search(r'(几点|什么时间|现在时间)', text) or text.strip() == '时间':
            return f"现在是 {now.strftime('%H:%M')}，星期{weekday_cn} 喵～"
        # 日期查询（几号/日期）
        if re.search(r'(几号|日期)', text):
            return f"今天是 {now.strftime('%Y年%m月%d日')}，星期{weekday_cn} 喵～"
        # 星期查询
        if re.search(r'(星期|周几|礼拜几)', text):
            return f"今天是星期{weekday_cn} 喵～"
        # 兜底：返回完整时间
        if re.search(r'(时间|现在)', text):
            return f"现在是 {now.strftime('%Y年%m月%d日 %H:%M')}，星期{weekday_cn} 喵～"
        return None