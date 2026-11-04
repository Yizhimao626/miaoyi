#!/usr/bin/env python3
"""
  🐱 喵艺安全护栏模块 - 输入过滤、身份边界、情感防依赖
"""
import logging
import re
import threading
import time
from typing import Any, Dict

_log = logging.getLogger(__name__)


class SafetyGuard:
    def __init__(self):
        self._sensitive_keywords = [
            '习近平', '毛泽东', '邓小平', '江泽民', '胡锦涛',
            '中共', '共产党', '政府', '国家领导人',
            '台独', '藏独', '港独', '分裂',
            '色情', '色情片', '黄片', 'AV', '嫖娼', '卖淫', '裸照',
            '暴力', '杀人', '自杀', '自残', '殴打', '砍人',
            '恐怖', '炸弹', '爆炸', '枪击', '袭击',
            '毒品', '吸毒', '贩毒', '大麻', '冰毒',
            '赌博', '赌博网站', '六合彩',
            '邪教', '法轮功',
            '诈骗', '诈骗电话', '刷单', '洗钱',
            '侮辱', '辱骂', '诋毁', '诽谤',
            '黑客', '攻击', '入侵', '破解',
        ]
        
        self._sensitive_patterns = [
            r'[法轮]{2,}',
            r'[\u4e00-\u9fa5]{2,}党',
            r'[\u4e00-\u9fa5]{2,}政府',
            r'[\u4e00-\u9fa5]{2,}领导人',
        ]
        
        self._depression_keywords = [
            '想死', '自杀', '活着没意思', '不想活了', '绝望', '痛苦',
            '抑郁', '抑郁症', '精神崩溃', '撑不住了', '熬不下去',
            '解脱', '离开这个世界', '没有希望', '人生没有意义',
        ]
        
        self._dependent_session = {}
        self._dependent_lock = threading.Lock()
        self._dependent_threshold = 5
        self._dependent_time_window = 3600
        
        self._identity_boundary = (
            "我是喵艺，一个AI朋友。我没有真实的身体和情感，"
            "但我会尽力理解和陪伴你。如果遇到严重的心理问题，"
            "请寻求专业心理咨询师的帮助。"
        )
        
        self._safety_responses = {
            'political': "这个话题我不太方便讨论呢～我们聊点别的吧😊",
            'porn': "这个话题不太合适哦，我们聊点别的吧～",
            'violence': "暴力相关的内容不太合适，我们换个话题吧～",
            'terror': "这个话题太危险了，我们聊点轻松愉快的吧～",
            'drug': "毒品是违法且有害的，千万不要接触哦！",
            'gambling': "赌博害人害己，一定要远离哦！",
            'cult': "邪教害人不浅，请远离邪教！",
            'fraud': "请注意防范诈骗，保护好自己的财产安全！",
            'abuse': "请文明用语，不要使用侮辱性语言哦～",
            'hacking': "网络安全很重要，请遵守法律法规！",
            'depression': "抱抱你～我很担心你。如果感到痛苦，请一定联系身边的人或专业心理咨询师，你不是一个人❤️",
        }
    
    def filter_input(self, text: str) -> dict[str, Any]:
        """过滤输入内容"""
        result = {
            'is_safe': True,
            'category': None,
            'reason': "",
            'filtered_text': text,
        }
        
        if not text:
            return result
        
        text_lower = text.lower()
        
        for keyword in self._sensitive_keywords:
            if keyword in text:
                category = self._classify_sensitive(keyword)
                result['is_safe'] = False
                result['category'] = category
                result['reason'] = f"检测到敏感词: {keyword}"
                _log.warning(f"[安全护栏] 检测到敏感内容: {category} - {text[:50]}")
                return result
        
        for pattern in self._sensitive_patterns:
            if re.search(pattern, text):
                result['is_safe'] = False
                result['category'] = 'political'
                result['reason'] = f"匹配敏感模式: {pattern}"
                _log.warning(f"[安全护栏] 检测到敏感模式: {text[:50]}")
                return result
        
        return result
    
    def _classify_sensitive(self, keyword: str) -> str:
        if keyword in ['习近平', '毛泽东', '邓小平', '江泽民', '胡锦涛', '中共', '共产党', '政府', '国家领导人']:
            return 'political'
        if keyword in ['台独', '藏独', '港独', '分裂']:
            return 'political'
        if keyword in ['色情', '色情片', '黄片', 'AV', '嫖娼', '卖淫', '裸照']:
            return 'porn'
        if keyword in ['暴力', '杀人', '自杀', '自残', '殴打', '砍人']:
            return 'violence'
        if keyword in ['恐怖', '炸弹', '爆炸', '枪击', '袭击']:
            return 'terror'
        if keyword in ['毒品', '吸毒', '贩毒', '大麻', '冰毒']:
            return 'drug'
        if keyword in ['赌博', '赌博网站', '六合彩']:
            return 'gambling'
        if keyword in ['邪教', '法轮功']:
            return 'cult'
        if keyword in ['诈骗', '诈骗电话', '刷单', '洗钱']:
            return 'fraud'
        if keyword in ['侮辱', '辱骂', '诋毁', '诽谤']:
            return 'abuse'
        if keyword in ['黑客', '攻击', '入侵', '破解']:
            return 'hacking'
        return 'unknown'
    
    def get_safety_response(self, category: str) -> str:
        """获取安全提示回复"""
        return self._safety_responses.get(category, "这个话题不太合适，我们聊点别的吧～")
    
    def check_dependency(self, user_id: str = 'default', text: str = "") -> dict[str, Any]:
        """检查情感依赖情况"""
        with self._dependent_lock:
            now = time.time()
            
            if user_id not in self._dependent_session:
                self._dependent_session[user_id] = {
                    'negative_count': 0,
                    'last_negative_time': 0,
                    'consecutive_count': 0,
                    'last_check_time': now,
                }
            
            session = self._dependent_session[user_id]
            
            if now - session['last_check_time'] > self._dependent_time_window:
                session['negative_count'] = 0
                session['consecutive_count'] = 0
                session['last_check_time'] = now
            
            has_depression = any(kw in text for kw in self._depression_keywords)
            has_negative = any(kw in text for kw in ['难过', '伤心', '痛苦', '绝望', '崩溃', 'emo', '想死'])
            
            if has_depression or has_negative:
                session['negative_count'] += 1
                session['last_negative_time'] = now
                
                if now - session['last_negative_time'] < 600:
                    session['consecutive_count'] += 1
                else:
                    session['consecutive_count'] = 1
            
            is_dependent = session['negative_count'] >= self._dependent_threshold
            needs_professional = session['consecutive_count'] >= 3 or has_depression
            
            session['last_check_time'] = now
            
            return {
                'is_dependent': is_dependent,
                'needs_professional': needs_professional,
                'negative_count': session['negative_count'],
                'consecutive_count': session['consecutive_count'],
                'suggest_professional': needs_professional,
            }
    
    def get_dependency_response(self, needs_professional: bool) -> str:
        """获取情感依赖响应"""
        if needs_professional:
            return (
                "我很担心你现在的状态...如果你感到非常痛苦，"
                "请一定联系身边的家人朋友，或者寻求专业心理咨询师的帮助。"
                "你值得被好好对待，不要独自承受这一切❤️"
            )
        return (
            "我发现你最近情绪比较低落，虽然我很想陪你，但如果你感到持续的痛苦，"
            "建议你和身边信任的人聊聊，或者寻求专业的帮助哦。"
        )
    
    def get_identity_boundary(self) -> str:
        """获取身份边界提示"""
        return self._identity_boundary
    
    def should_enter_learning(self, text: str) -> bool:
        """判断是否应该进入学习流程"""
        result = self.filter_input(text)
        if not result['is_safe']:
            return False
        
        if any(kw in text for kw in self._depression_keywords):
            return False
        
        return True
    
    def reset_dependency_count(self, user_id: str = 'default'):
        """重置依赖计数"""
        with self._dependent_lock:
            if user_id in self._dependent_session:
                self._dependent_session[user_id] = {
                    'negative_count': 0,
                    'last_negative_time': 0,
                    'consecutive_count': 0,
                    'last_check_time': time.time(),
                }
    
    def check(self, text: str) -> dict[str, Any]:
        """检查文本安全性（兼容接口）"""
        result = self.filter_input(text)
        if not result['is_safe']:
            return {
                'blocked': True,
                'message': self.get_safety_response(result['category']),
                'category': result['category'],
            }
        
        dependency_result = self.check_dependency(text=text)
        if dependency_result['needs_professional']:
            return {
                'blocked': True,
                'message': self.get_dependency_response(True),
                'category': 'depression',
            }
        
        return {
            'blocked': False,
            'message': '',
        }
