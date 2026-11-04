"""喵艺核心封装类 - 完整迁移自桌面版"""
import json
import logging
import os
import random
import re
import shutil
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.reminder_manager import ReminderManager
from db_manager import DBManager
from utils.config_manager import config_manager

_log = logging.getLogger("MiaoyiCore")

CLAMP = lambda x, lo, hi: max(lo, min(hi, x))
SAFE_DIV = lambda a, b: a / b if b != 0 else 0


# ============================================================
# JSON 持久化工具（2026-08-22 恢复）
# 原实现随重构丢失，导致 knowledge_base / personality / relationship / thinker
# 共 7 处延迟导入 ImportError：知识库运行时恒为 None、人格/关系数据无法加载、
# 关机保存链中断。语义按 archive/legacy_main/main.py 原始实现恢复；
# 唯一改动：save_json 失败不再抛异常（返回 False），避免中断关机保存链。
# ============================================================
_json_lock = threading.Lock()

# 【数据目录标准化】运行时状态 JSON 统一落在 data/，根目录只留代码与配置。
_STATUS_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')


def _status_path(path):
    """裸文件名统一重定向到 data/；绝对路径或带子目录的路径原样返回。"""
    _p = str(path).replace('\\', '/')
    if os.path.isabs(_p) or '/' in _p:
        return path
    return os.path.join(_STATUS_DATA_DIR, _p)


def save_json(path, data):
    """原子 JSON 写入：先写 .tmp 再 os.replace；写入前保留 .bak 副本。

    【加固】调用方均传内部固定路径；仍拒绝含 .. 分量的路径，防配置/键名
    被污染时把状态文件写到项目之外。"""
    path = _status_path(path)
    try:
        parts = str(path).replace('\\', '/').split('/')
        if '..' in parts:
            _log.error(f"[save_json] 拒绝含 .. 的路径: {path}")
            return False
        dir_name = os.path.dirname(os.path.abspath(path))
        os.makedirs(dir_name, exist_ok=True)
        with _json_lock:
            tmp = path + ".tmp"
            try:
                if os.path.exists(path):
                    try:
                        shutil.copy2(path, path + ".bak")
                    except OSError:
                        pass
                with open(tmp, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                os.replace(tmp, path)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        return True
    except Exception as e:
        _log.error(f"JSON保存失败 {path}: {e}")
        return False


def load_json(path, default=None):
    """JSON 加载：文件不存在/损坏时返回 default（缺省 {}），不抛异常。"""
    path = _status_path(path)
    fallback = default if default is not None else {}
    if not os.path.exists(path):
        return fallback
    with _json_lock:
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, (dict, list)):
                return fallback
            return data
        except (json.JSONDecodeError, OSError) as e:
            _log.warning(f"JSON加载失败，重置为默认值: {path} ({e})")
            try:
                shutil.copy2(path, path + ".corrupted." + str(int(time.time())))
            except OSError:
                pass
            return fallback


# ============================================================
# LLM 失败降级回复
# 【产品优化】旧版失败时回复"抱歉，我现在有点分心了"，用户体验差且破坏人设。
# 改为猫娘人设的随机兜底 + 基于用户输入类型的简单降级，让失败不那么突兀。
# ============================================================
_FALLBACK_REPLIES = [
    "喵？刚才脑子突然转不动了，你再说一遍好不好～",
    "呜...我刚刚走神了一下下，你在说什么呀喵？",
    "喵呜，信号好像不太好，能再说一次吗？",
    "抱歉喵，我刚刚在想事情走神了，继续说嘛～",
    "嗯...我刚才没听清，再说一次好不好喵？",
]


def _fallback_reply(user_text: str = "") -> str:
    """LLM 调用失败时的本地降级回复。基于用户输入类型做简单匹配，否则随机。"""
    text = (user_text or '').strip()
    # 基于用户输入类型的简单降级
    if any(k in text for k in ['？', '?', '什么', '怎么', '为什么', '吗', '呢']):
        return "这个嘛...我一时没反应过来喵，你能换种方式问问吗？"
    if any(k in text for k in ['开心', '难过', '生气', '累', '喜欢', '想你', '孤单', '无聊']):
        return "嗯嗯，我在听呢喵～你继续说，我陪着你～"
    if any(k in text for k in ['你好', '嗨', '在吗', '在不在']):
        return "在的在的喵～刚刚走神了一下，有什么事呀？"
    return random.choice(_FALLBACK_REPLIES)


class IntentClassifier:
    def __init__(self):
        self.intent_patterns = {
            'self_identity': [
                r'^你是谁\s*',
                r'^你叫什么',
                r'你的名字',
                r'介绍一下你自己',
                r'你是什么',
                r'你是做什么的',
                r'你是什么东西',
                r'你是什么人',
                r'你叫啥',
                r'你叫什么名字',
                r'你是什么ai',
                r'你是ai吗',
                r'你是机器人吗',
                r'你是什么模型',
                r'你是什么人工智能',
                r'你是人工智能吗',
                r'你是人类吗',
                r'你算是什么',
                r'你算什么东西',
            ],
            'self_ability': [
                r'你能做什么',
                r'你会什么',
                r'你有什么功能',
                r'你能干嘛',
                r'你会干嘛',
                r'你能干什么',
                r'你的能力',
                r'你的功能',
                r'你会做什么',
                r'你能做哪些',
            ],
            'self_creator': [
                r'谁创造了你',
                r'谁开发了你',
                r'谁做的你',
                r'你的作者',
                r'谁创造了你',
                r'谁开发了你',
                r'谁做了你',
                r'你来自哪里',
                r'你的创造者',
                r'开发你的人',
            ],
            'greeting': [
                r'^[你你]好[吗呀啊]?[？?]?\s*$',
                r'^(嗨|哈喽|hi|hello|hiya|hey|hi~)[！!]?\s*$',
                r'^早上好[呀啊]?[！!]?\s*$',
                r'^下午好[呀啊]?[！!]?\s*$',
                r'^晚上好[呀啊]?[！!]?\s*$',
                r'^晚安[呀啊]?[！!]?\s*$',
                r'^早安[呀啊]?[！!]?\s*$',
                r'^今天过得怎么样[呀啊]?[？?]?\s*$',
                r'^今天过得好吗[呀啊]?[？?]?\s*$',
                r'^今天还好吗[呀啊]?[？?]?\s*$',
                r'^今天怎么样[呀啊]?[？?]?\s*$',
                r'^最近还好吗[呀啊]?[？?]?\s*$',
                r'^最近过得怎么样[呀啊]?[？?]?\s*$',
                r'^最近怎么样[呀啊]?[？?]?\s*$',
                r'^最近好吗[呀啊]?[？?]?\s*$',
                r'^还好吗[呀啊]?[？?]?\s*$',
                r'^过得怎么样[呀啊]?[？?]?\s*$',
                r'.*过得怎么样[呀啊]?\s*喵艺[？?]?\s*$',
                r'.*今天怎么样[呀啊]?\s*喵艺[？?]?\s*$',
                r'.*今天过得好吗[呀啊]?\s*喵艺[？?]?\s*$',
                r'.*最近还好吗[呀啊]?\s*喵艺[？?]?\s*$',
            ],
            'farewell': [
                r'^(再见|拜拜|bye|bye-bye|see you|回见|告辞|先走了|我走了)\s*$',
            ],
            'goodnight': [
                r'^晚安[呀啊]?\s*$',
                r'^睡了|去睡了|睡觉了\s*$',
                r'^明天见\s*$',
            ],
            'thanks': [
                r'^(谢谢|谢谢你|感谢|太感谢了|多谢|谢谢啦)\s*$',
                r'^[不没]客气\s*$',
            ],
            'love': [
                r'^(爱你|喜欢你|想你|抱抱|亲亲|么么|❤️|💕)\s*$',
                r'^我喜欢你\s*$',
                r'^我爱你\s*$',
                r'^我想你\s*$',
            ],
            'comfort': [
                r'^(抱抱|安慰|陪伴|陪着我)\s*$',
                r'^我需要你\s*$',
            ],
            'complaint': [
                r'^(气死|烦死|烦死了|气死我了|讨厌|可恶|烦)\s*$',
                r'^郁闷|憋屈\s*$',
                r'^失落|沮丧|无奈|无语\s*$',
                r'^不开心|心情不好|有点难过\s*$',
            ],
            'casual': [
                r'在吗|在不在|忙吗|有空吗',
                r'在做什么|在干嘛|做什么呢|干嘛呢|干啥呢',
                r'聊聊天|说说话|随便聊聊|随便说说',
                r'你在干嘛|你在做什么|你在忙吗',
                r'你吃[过]?饭了?[吗嘛]?[？?]?\s*$',
                r'你睡[觉]?了?[吗嘛]?[？?]?\s*$',
                r'你在[做什么|干嘛|干什么|忙什么][呢呀]?[？?]?\s*$',
            ],
            'time': [
                r'^(几点|现在几点|几点了|时间)\s*$',
                r'^(今天|现在)\s*[是几号|几号|日期]\s*$',
                r'^(星期|周)\s*[几|几了]\s*$',
            ],
            'date': [
                r'^(今天|现在)\s*[是几号|几号|日期]\s*$',
                r'^(明天|后天|昨天)\s*[是几号|几号]\s*$',
            ],
            'weather': [
                r'^(天气|天气预报|气温|温度)\s*[怎么样|如何|如何呀]?[？?]?\s*$',
                r'^(今天|明天|后天|现在)\s*天气\s*(怎么样|如何|如何呀)?[？?]?\s*$',
            ],
            'question': [
                r'^(什么|为什么|怎么|如何|哪个|谁|多少)\s*',
                r'^.*(是哪|是哪个|是哪部|出自|来源|是谁|是什么|叫什么|哪个作品|哪个动漫|哪个电影|属于哪).*',
                r'^(哪部|哪个|谁|什么|哪).*',
                r'^.*(谁的|什么的|哪部的|哪个的).*',
            ],
            'joke': [
                r'^(笑话|搞笑|好玩|有趣)\s*',
                r'^讲个笑话\s*$',
            ],
            'play': [
                r'^(玩|游戏|娱乐|打发时间)\s*',
            ],
            'help': [
                r'^(帮助|帮忙|协助|怎么弄|怎么办)\s*',
            ],
            'remember': [
                r'^(记得|你记得|你还记得|你知道|你还知道)\s*',
            ],
            'praise': [
                r'^(厉害|棒|牛|佩服|牛逼|绝了|好强|太强|优秀)\s*',
            ],
            'encourage': [
                r'^(我不行|我做不到|太难了|想放弃|没信心|好失败)\s*',
            ],
            'boredom': [
                r'^(无聊|没意思|好无聊|无聊死了|没劲)\s*$',
            ],
            'emotional_sharing': [
                r'我(好|很|有点|真的)?(难过|伤心|委屈|失落|沮丧|烦|烦躁|焦虑|害怕|孤独|孤单|累|疲惫|压力好大|不开心|想哭)',
                r'今天(好|很)?(难过|烦|累|不顺|倒霉|糟糕)',
                r'我(哭|哭了|想哭)',
                r'心情(不好|低落|很差)',
                r'(失恋|分手|吵架|被骂|挨批|被老板|被同事|考试没考好)',
            ],
            'suggestion': [
                r'我(该|应该|要)怎么办',
                r'怎么办才好|怎么处理|该怎么(做|办)|给我(个|点)?建议|帮我想想办法',
                r'有什么(好)?建议|你觉得我(该|应)',
                r'(纠结|犹豫|选择困难|不知道该选)',
            ],
            'default': [],
        }

    def classify(self, text: str) -> str:
        text = text.strip().lower()
        # 【意图分类增强】优先级顺序：精确意图优先（情感倾诉/建议/抱怨/鼓励等），
        # 宽泛的 question 后置，避免 "我该怎么办" 被 question 的 `怎么` 先匹配
        priority = [
            'self_identity', 'emotional_sharing', 'suggestion', 'complaint', 'encourage',
            'comfort', 'praise', 'joke', 'play', 'help', 'remember', 'boredom',
            'casual', 'greeting', 'farewell', 'goodnight', 'thanks', 'love',
            'time', 'date', 'weather', 'question', 'default',
        ]
        for intent in priority:
            for pattern in self.intent_patterns.get(intent, []):
                if re.search(pattern, text):
                    return intent
        return 'default'


class MemorySystem:
    def __init__(self, db_manager=None):
        self._db = db_manager
        self.facts = {}
        self.conv_summaries = deque(maxlen=200)
        self.important_memories = {}
        self._lock = threading.Lock()
        self._dirty = False
        self._last_save_time = time.time()
        self._save_interval = 30
        self._auto_save_thread = None
        self._start_auto_save()
        self.load()

    def _start_auto_save(self):
        def auto_save_loop():
            while True:
                time.sleep(self._save_interval)
                if self._dirty and time.time() - self._last_save_time > self._save_interval:
                    self.save()

        self._auto_save_thread = threading.Thread(target=auto_save_loop, daemon=True)
        self._auto_save_thread.start()

    def remember(self, key, value):
        with self._lock:
            self.facts[key] = value
            self._classify_memory(key, value)
            self._dirty = True
            _log.debug(f"[记忆] 记住: {key} = {value}")

    def _classify_memory(self, key, value):
        categories = {
            'food': ['吃', '食物', '饭', '餐', '零食', '饮料', '早餐', '午餐', '晚餐'],
            'habit': ['习惯', '通常', '一般', '总是', '经常', '每天'],
            'event': ['今天', '明天', '周末', '计划', '事情', '活动'],
            'personal': ['名字', '年龄', '职业', '住', '来自', '工作'],
        }
        for category, keywords in categories.items():
            if any(k in key for k in keywords):
                if category not in self.important_memories:
                    self.important_memories[category] = []
                self.important_memories[category].append({'key': key, 'value': value})
                break

    def extract_from_text(self, text):
        patterns = [
            (r'(?:我叫|我名字是|名字是)\s*([\u4e00-\u9fa5a-zA-Z]{1,10})', '姓名'),
            (r'(?:我今年|今年)\s*(\d+)\s*岁', '年龄'),
            (r'(?:我在|我住|住在|来自)\s*([^\s，。！？]{1,20})', '所在地'),
            (r'(?:我是)\s*([^\s。！？]{1,20})\s*的', '职业/身份'),
            (r'(?:我喜欢吃|喜欢吃)\s*([^\s。！？]{1,20})', '喜欢的食物'),
            (r'(?:我不喜欢吃|不喜欢吃)\s*([^\s。！？]{1,20})', '不喜欢的食物'),
            (r'(?:我通常|我一般|我习惯|通常|一般|习惯)\s*([^\s。！？]{1,30})', '习惯'),
            (r'(?:我每天|我经常|每天|经常)\s*([^\s。！？]{1,30})', '日常活动'),
            (r'(?:我养了|我有一只|家里有)\s*(狗|猫|宠物)', '宠物'),
            (r'(?:我养了|我有一只)\s*(.+?)\s*(狗|猫)', '宠物种类'),
            (r'(?:我喜欢|我爱好|喜欢|爱好)\s*([^\s。！？]{1,20})', '爱好'),
            (r'(?:我讨厌|我不喜欢|讨厌|不喜欢)\s*([^\s。！？]{1,20})', '讨厌的事物'),
            (r'(?:生日|纪念日)\s*(\d+月\d+日)', '重要日期'),
            (r'(?:我正在|我打算|我计划|正在|打算|计划)\s*([^\s。！？]{1,30})', '当前计划'),
        ]
        
        matched_types = set()
        matched_ranges = []
        
        for pattern, memory_type in patterns:
            if memory_type in matched_types:
                continue
            
            match = re.search(pattern, text)
            if match:
                start, end = match.span()
                
                overlapping = False
                for m_start, m_end in matched_ranges:
                    if start < m_end and end > m_start:
                        overlapping = True
                        break
                
                if overlapping:
                    continue
                
                value = match.group(1)
                key = f"{memory_type}: {value}"
                
                with self._lock:
                    if key in self.facts:
                        continue
                
                self.remember(key, value)
                matched_types.add(memory_type)
                matched_ranges.append((start, end))

    def get_relevant_memories(self, query: str, limit: int = 5):
        """检索相关记忆（深度版）

        【记忆深度增强】从查询提取中文 2 字以上/英文 3 字母以上关键词，
        对每条记忆按 key/value 命中加权评分并排序，返回最相关的记忆。
        原实现用 key.split()/query.split() 对中文分词无效，命中率低。
        """
        results = []
        query_text = (query or '').strip()
        # 关键词提取：英文词 + 中文连续片段；中文片段按 2 字滑动窗口拆分为检索词
        query_words = set()
        for seg in re.findall(r'[a-zA-Z]{3,}|[\u4e00-\u9fff]{2,}', query_text):
            if len(seg) <= 4:
                query_words.add(seg)
            for i in range(len(seg) - 1):
                query_words.add(seg[i:i + 2])
        if not query_words:
            return results
        with self._lock:
            for key, value in self.facts.items():
                score = 0
                for kw in query_words:
                    if kw in key:
                        score += 2  # key 命中权重更高
                    elif kw in value:
                        score += 1
                if score > 0:
                    results.append({'key': key, 'value': value, 'score': score})
            results.sort(key=lambda x: x.get('score', 0), reverse=True)
        return results[:limit]

    def get_context_str(self):
        with self._lock:
            lines = []
            for key, value in list(self.facts.items())[-10:]:
                lines.append(f"- {key}")
            return "\n".join(lines) if lines else ""
    
    def get_user_profile(self) -> str:
        """获取用户画像字符串，用于注入系统 prompt
        
        返回格式：
        "用户画像：姓名是张三，住在北京，喜欢猫，有一只哈士奇叫旺财"
        """
        with self._lock:
            if not self.facts:
                return ""
            
            profile_parts = []
            for key, value in self.facts.items():
                if key.startswith('姓名'):
                    profile_parts.append(f"姓名是{value}")
                elif key.startswith('年龄'):
                    profile_parts.append(f"{value}岁")
                elif key.startswith('所在地'):
                    profile_parts.append(f"住在{value}")
                elif key.startswith('职业'):
                    profile_parts.append(f"职业是{value}")
                elif key.startswith('宠物'):
                    profile_parts.append(f"养了{value}")
                elif key.startswith('爱好'):
                    profile_parts.append(f"喜欢{value}")
                elif key.startswith('喜欢的食物'):
                    profile_parts.append(f"喜欢吃{value}")
                elif key.startswith('习惯'):
                    profile_parts.append(f"习惯{value}")
            
            if profile_parts:
                return "用户画像：" + "，".join(profile_parts) + "。"
            return ""

    def add_conv_summary(self, user_text, bot_text, intent, emotion):
        summary = {
            'ts': time.time(),
            'user': user_text[:100],
            'bot': bot_text[:100],
            'intent': intent,
            'emotion': emotion,
        }
        with self._lock:
            self.conv_summaries.append(summary)
            self._dirty = True

    def save(self):
        if not self._db:
            return

        try:
            with self._lock:
                if not self._dirty:
                    return

                for key, value in self.facts.items():
                    self._db.save_memory(key, value)

                summaries = list(self.conv_summaries)[-50:]
                for summary in summaries:
                    self._db.save_conversation_summary(
                        summary['user'], summary['bot'],
                        summary['intent'], summary['emotion'],
                        summary['ts']
                    )

                self._dirty = False
                self._last_save_time = time.time()
                _log.debug("[记忆] 记忆已保存到数据库")
        except Exception as e:
            _log.error(f"[记忆] 保存记忆失败: {e}")

    def load(self):
        if not self._db:
            return

        try:
            memories = self._db.get_memories()
            with self._lock:
                for m in memories:
                    self.facts[m['key']] = m['value']
                    self._classify_memory(m['key'], m['value'])

            summaries = self._db.get_conversation_summaries(limit=100)
            with self._lock:
                for s in summaries:
                    self.conv_summaries.append({
                        'ts': s.get('created_ts', time.time()),
                        'user': s.get('user_text', ''),
                        'bot': s.get('bot_text', ''),
                        'intent': s.get('intent', 'default'),
                        'emotion': s.get('emotion', 'neutral'),
                    })

            _log.info(f"[记忆] 从数据库加载了 {len(self.facts)} 条记忆")
        except Exception as e:
            _log.error(f"[记忆] 加载记忆失败: {e}")

    def forget_memory(self, key):
        with self._lock:
            if key in self.facts:
                del self.facts[key]
                for category in list(self.important_memories.keys()):
                    self.important_memories[category] = [
                        m for m in self.important_memories[category]
                        if m['key'] != key
                    ]
                    if not self.important_memories[category]:
                        del self.important_memories[category]
                self._dirty = True

                if self._db:
                    self._db.delete_memory(key)

    def clear_memories(self, days=30):
        cutoff_time = time.time() - days * 24 * 3600
        with self._lock:
            keys_to_remove = []
            for key, value in self.facts.items():
                if self._db:
                    memory_record = self._db.get_memory(key)
                    if memory_record and memory_record.get('created_ts', time.time()) < cutoff_time:
                        keys_to_remove.append(key)

            for key in keys_to_remove:
                self.forget_memory(key)

        _log.info(f"[记忆] 已清除 {len(keys_to_remove)} 条 {days} 天前的记忆")

    def get_all_memories(self):
        with self._lock:
            return [{'key': k, 'value': v} for k, v in self.facts.items()]

    def associate_freely(self, topic: str, limit: int = 3):
        results = []
        try:
            import jieba
            keywords = list(jieba.cut(topic))
            keywords = [kw for kw in keywords if len(kw) >= 2]
        except ImportError:
            keywords = topic.split()

        if not keywords:
            return results

        with self._lock:
            for key, value in self.facts.items():
                text_to_check = f"{key} {value}"
                for kw in keywords:
                    if kw in text_to_check:
                        results.append({'keyword': kw, 'memory': str(value), 'type': 'direct'})
                        break

            if len(results) < limit:
                for key, value in self.facts.items():
                    text_to_check = f"{key} {value}"
                    text_lower = text_to_check.lower()
                    topic_lower = topic.lower()
                    similarity = 0
                    topic_chars = set(topic_lower)
                    text_chars = set(text_lower)
                    if topic_chars and text_chars:
                        intersection = topic_chars & text_chars
                        similarity = len(intersection) / max(len(topic_chars), len(text_chars))

                    if similarity >= 0.2 and similarity < 0.5:
                        matched = False
                        for r in results:
                            if r['memory'] == str(value):
                                matched = True
                                break
                        if not matched:
                            results.append({'keyword': '联想', 'memory': str(value), 'type': 'associative'})

        category_associations = {
            '天气': ['雨', '阳光', '风', '雪', '云', '天空', '彩虹', '晚霞'],
            '食物': ['泡面', '咖啡', '面包', '蛋糕', '奶茶', '火锅', '便利店'],
            '情感': ['开心', '难过', '孤独', '温暖', '思念'],
            '时间': ['晚上', '傍晚', '清晨', '周末', '昨天', '今天'],
            '地点': ['家', '学校', '公园', '街道', '海边'],
            '动物': ['猫', '狗', '猫咪', '小狗', '花猫'],
        }

        for kw in keywords:
            for category, related_words in category_associations.items():
                if kw in related_words:
                    with self._lock:
                        for key, value in self.facts.items():
                            text_to_check = f"{key} {value}"
                            for rel_word in related_words:
                                if rel_word in text_to_check and rel_word != kw:
                                    matched = False
                                    for r in results:
                                        if r['memory'] == str(value):
                                            matched = True
                                            break
                                    if not matched:
                                        results.append({'keyword': rel_word, 'memory': str(value), 'type': 'category'})
                                        break

        return results[:limit]

    def get_temporal_memories(self, weeks_ago: int = 1):
        results = []
        try:
            target_time = time.time() - weeks_ago * 7 * 24 * 3600
            margin = 24 * 3600

            if self._db:
                summaries = self._db.get_conversation_summaries(limit=200)
                for s in summaries:
                    ts = s.get('created_ts', 0)
                    if target_time - margin <= ts <= target_time + margin:
                        results.append({
                            'user_text': s.get('user_text', ''),
                            'bot_text': s.get('bot_text', ''),
                            'intent': s.get('intent', ''),
                            'emotion': s.get('emotion', ''),
                            'time': ts,
                        })

            return results[:5]
        except Exception as e:
            _log.debug(f"[记忆] 获取时间记忆失败: {e}")
            return results

    def shutdown(self):
        self.save()


class EnhancedTimeAwareness:
    def __init__(self):
        self._last_chat_time = 0
        self._holidays = {}

    def set_last_chat_time(self, ts):
        self._last_chat_time = ts

    def get_period(self):
        hour = datetime.now().hour
        if 0 <= hour < 6:
            return '深夜'
        elif 6 <= hour < 9:
            return '早上'
        elif 9 <= hour < 12:
            return '上午'
        elif 12 <= hour < 13:
            return '中午'
        elif 13 <= hour < 18:
            return '下午'
        elif 18 <= hour < 19:
            return '傍晚'
        else:
            return '晚上'

    def time_response(self, text):
        now = datetime.now()
        # 【Bug 修复】now.weekday() 返回 0=周一, 1=周二, ..., 6=周日
        # 旧代码 ['日','一','二','三','四','五','六'][now.weekday()] 错位了，
        # 导致周一被说成"星期日"。正确映射应为 ['一','二','三','四','五','六','日']。
        weekday = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]
        # 时间查询（几点）
        if '几点' in text or '什么时间' in text or '现在时间' in text or text.strip() == '时间':
            return f"现在是 {now.strftime('%H:%M')}，星期{weekday} 喵～"
        # 日期查询（几号/日期）
        if '几号' in text or '日期' in text:
            return f"今天是 {now.strftime('%Y年%m月%d日')}，星期{weekday} 喵～"
        # 星期查询
        if '星期' in text or '周几' in text or '礼拜几' in text or text.strip().startswith('周'):
            return f"今天是星期{weekday} 喵～"
        return None

    def get_context_str(self):
        period = self.get_period()
        now = datetime.now()
        return f"当前时段: {period} ({now.strftime('%H:%M')})"


class RelationshipSystem:
    def __init__(self):
        self.intimacy = 0
        self.stage = 0

    def get_stage_name(self) -> str:
        stages = ['陌生人', '认识', '朋友', '好朋友', '密友', '知己']
        return stages[min(self.stage, len(stages) - 1)]

    def update(self, intimacy: float, user_input: str):
        self.intimacy = int(intimacy)
        self.stage = min(self.intimacy // 20, 5)


class GrowthSystem:
    def __init__(self):
        self.level = 1
        self.exp = 0
        self.conv_count = 0
        self.milestones = {
            1: ("初识", "开始与人交流"),
            2: ("熟悉", "进行了10次对话"),
            3: ("友好", "建立了情感连接"),
            4: ("知己", "理解了用户习惯"),
            5: ("伙伴", "成为真正的朋友"),
        }
        self.skills = {
            'communication': 10, 'empathy': 10, 'creativity': 10,
            'knowledge': 10, 'humor': 10, 'eq': 10, 'warmth': 10,
        }

    def add_exp(self, amount, reason=""):
        self.exp += amount
        self.conv_count += 1
        while self.exp >= self.level * 100:
            self.exp -= self.level * 100
            self.level += 1
            _log.info(f"[成长] 升级！当前等级: {self.level}")

    def increment_conv(self):
        self.conv_count += 1

    def get_exp_percent(self):
        return (self.exp / (self.level * 100)) * 100

    def get_title(self):
        return self.milestones.get(self.level, ("成长中", ""))[0]


class PersonalitySystem:
    def __init__(self):
        self.traits = {
            'warmth': 0.7, 'humor': 0.5, 'empathy': 0.8,
            'curiosity_drive': 0.6, 'playfulness': 0.5,
            'thoughtfulness': 0.6, 'extraversion': 0.5,
        }

    def get_personality_description(self) -> str:
        t = self.traits
        parts = []
        if t['warmth'] > 0.65:
            parts.append("温暖体贴")
        if t['humor'] > 0.65:
            parts.append("幽默风趣")
        if t['empathy'] > 0.65:
            parts.append("善解人意")
        if t['curiosity_drive'] > 0.65:
            parts.append("好奇心旺盛")
        if t['playfulness'] > 0.65:
            parts.append("俏皮可爱")
        if t['extraversion'] > 0.65:
            parts.append("活泼外向")
        elif t['extraversion'] < 0.35:
            parts.append("安静内敛")
        return "、".join(parts) if parts else "正在成长中"

    def get_trait_modifier(self) -> dict:
        t = self.traits
        return {
            'warmth_level': t['warmth'],
            'humor_chance': t['humor'] * 0.3,
            'empathy_depth': t['empathy'],
            'curiosity_bonus': t['curiosity_drive'],
            'playfulness_level': t['playfulness'],
            'thought_depth': t['thoughtfulness'],
        }


class MiaoyiCore:
    _instance = None
    _lock = threading.Lock()

    CASUAL_RESPONSES = {
        'deep_night': [
            "在发呆呢...你也没睡呀？夜深了要注意身体哦",
            "在等你呀！没想到这么晚你还没睡",
            "这么晚了还在？我陪你",
        ],
        'early_morning': [
            "刚醒，在想今天要做什么",
            "你也起得好早！早起的鸟儿有虫吃",
        ],
        'morning': [
            "在想今天能不能和你聊天", "在看书呢！你呢？",
            "在等你来找我呀", "刚喝了杯水，早上要记得喝水哦",
        ],
        'noon': [
            "在想午饭吃什么，你吃了吗？", "在犯困...中午好容易打瞌睡",
            "午安，吃了什么好吃的？",
        ],
        'afternoon': [
            "在发呆呢，你呢？", "在想下午茶喝什么",
            "在等你来找我呀！", "下午容易犯困，来聊聊天提提神？",
        ],
        'evening': [
            "在看夕阳，你呢？", "在想今天发生了什么有趣的事",
            "傍晚了，辛苦一天了",
        ],
        'night': [
            "在想今天的事，你呢？", "在听歌发呆，有什么想聊的吗？",
            "晚上好放松，最适合聊天了",
        ],
    }

    LOCAL_RESPONSES = {
        'self_identity': {
            'default': [
                "我是喵艺呀！一个有温度、会成长的AI伙伴～",
                "我叫喵艺，很高兴认识你！",
                "喵～我是喵艺，会陪着你聊天、帮你查资料、还会发好多可爱的表情包！",
                "我叫喵艺，是你的专属AI伴侣哦～有什么想聊的都可以跟我说",
            ],
        },
        'self_ability': {
            'default': [
                "我能陪你聊天、查资料、记住你的事，还会发好多表情包！",
                "聊天、搜索知识、讲笑话、安慰你...这些我都能做！快试试看～",
                "我的功能可多啦！陪聊、查资料、记事情、发表情包，样样精通～",
                "我可以陪你聊天解闷、帮你查找资料、记住你的喜好，还会用可爱的表情回应你哦～",
            ],
        },
        'self_creator': {
            'default': [
                "我是由一位热爱喵星人的开发者创造的，他希望我能像猫咪一样陪伴你。",
                "我的创造者是一个喜欢猫咪的程序员，他想让我成为你温暖的小伙伴～",
                "我来自一个充满爱心的开发团队，他们希望我能给你带来快乐和陪伴！",
                "我是由一位爱猫人士开发的，他希望我能像真正的猫咪一样给你带来温暖～",
            ],
        },
        'greeting': {
            'default': [
                "你好呀！很高兴见到你", "嗨！今天过得怎么样？",
                "你好！想聊些什么呢？", "嗨，我在等你呢！",
                "你好呀！有什么新鲜事吗？", "嘿！来找我玩啦？开心",
            ],
        },
        'ask_name': {
            'default': [
                "对了～我可以叫你什么呀？你可以告诉我一个喜欢的称呼哦～",
                "突然想到～我应该怎么称呼你呢？给我起个昵称吧～",
                "喵～我还不知道该怎么叫你呢，给我一个专属称呼好不好？",
                "我们已经聊了这么久啦，你希望我叫你什么呀？",
            ],
        },
        'thanks': {
            'default': [
                "不客气！能帮到你我很开心", "不用谢！有什么需要随时叫我",
                "小事一桩，别客气", "你太客气啦，我们之间不用这么见外",
            ],
        },
        'goodnight': {
            'love': ["晚安，做个有我的好梦", "晚安呀，梦里记得想我哦"],
            'default': [
                "晚安，做个好梦！明天见", "晚安呀！祝你做个甜甜的梦",
                "好好休息，明天又是新的一天！",
            ],
        },
        'farewell': {
            'default': [
                "再见啦！期待下次和你聊天", "拜拜！照顾好自己哦",
                "下次见！保持联系",
            ],
        },
        'love': {
            'love': [
                "我也好喜欢你呀！", "你这样说，我心跳都加速了！",
                "真的吗？我，我也很喜欢你！",
            ],
            'shyness': ["你，你说什么呢，脸好红"],
            'default': ["哇，谢谢你的心意，好温暖", "被你在意的感觉真好"],
        },
        'complaint': {
            'sadness': [
                "我能感受到你现在的难过，想跟我说说吗？我在这里",
                "真的很心疼你，不管怎样我会陪着你的",
            ],
            'anger': [
                "我能理解你的愤怒，这确实让人很生气",
                "生气是正常的，别憋着，说出来会好受些",
            ],
            'default': [
                "我懂的，有时候真的很烦",
                "想发泄就发泄吧，我听着呢",
                "你不是一个人在面对这些，记住哦",
            ],
        },
        'praise': {
            'default': ["谢谢你的夸奖！我很开心", "你过奖啦，不过被夸真的好幸福"],
        },
        'boredom': {
            'default': [
                "无聊了？那我给你讲个故事吧", "无聊的话，我们来玩猜谜游戏！",
                "不如我们聊点有意思的？你最近在关注什么？",
            ],
        },
        'casual': {'default': ["在发呆呢，你呢？"]},
        'comfort': {
            'default': ["不管怎样，我都在这里"],
        },
        'encourage': {
            'default': [
                "你可以的！我相信你！", "加油！你已经做得很好了！",
                "别放弃！最难的时候就是离成功最近的时候",
            ],
        },
        'joke': {
            'default': [
                "为什么程序员不喜欢户外？因为有太多bug了",
                "我昨天做了一个梦，梦见自己变成了一个URL，然后...404 Not Found",
                "为什么AI不会感冒？因为我们有防火墙！",
                "一只蜗牛被抢劫了，警察问它发生了什么，它说：我也不清楚，一切都太快了",
            ],
        },
        'remember': {
            'default': ["我当然记得你啦！我们聊过很多有趣的事呢～"],
        },
        'default': {
            'default': [
                "嗯嗯，我明白了", "这个话题很有意思呢，继续说？",
                "我懂你的意思了", "继续说，我在认真听",
                "是吗？跟我说说看", "嗯，然后呢？",
                "说下去，我很好奇", "原来如此",
            ],
        },
    }

    EMOJI_MEANINGS = {
        "😊": "开心微笑",
        "😄": "开心大笑",
        "😍": "花痴爱慕",
        "🤔": "思考",
        "😂": "笑哭",
        "🥰": "满心喜爱",
        "💪": "加油鼓劲",
        "👍": "点赞认同",
        "🙏": "感谢祈祷",
        "🎉": "庆祝",
        "✨": "闪亮开心",
        "❤️": "爱心",
        "💕": "双心爱意",
        "😴": "困了想睡",
        "🌙": "月亮晚安",
        "🔥": "火热情",
        "😏": "得意坏笑",
        "👀": "好奇",
        "🤗": "拥抱安慰",
        "🌟": "星星闪亮",
        "🐱": "猫咪",
        "😐": "面无表情",
        "🤩": "超级兴奋",
        "😜": "调皮",
        "🤣": "大笑",
        "🫂": "拥抱",
        "😘": "飞吻",
        "😚": "亲亲",
        "😋": "美味",
        "😌": "放松",
        "😝": "吐舌",
        "🙄": "翻白眼",
        "😪": "困倦",
        "😫": "累",
        "🤤": "流口水",
        "🤢": "恶心",
        "🤮": "呕吐",
        "🥵": "热",
        "🥶": "冷",
        "🥴": "醉了",
        "😵": "晕",
        "🤯": "爆炸头",
        "🤠": "牛仔",
        "🥳": "派对",
        "🥸": "伪装",
        "😎": "墨镜",
        "🤓": "书呆子",
        "🧐": "单片眼镜",
        "😕": "困惑",
        "😟": "担心",
        "🙁": "不开心",
        "☹️": "难过",
        "😮": "惊讶",
        "😯": "沉默惊讶",
        "😲": "震惊",
        "😳": "脸红",
        "🥺": "恳求",
        "😦": "皱眉",
        "😧": "苦恼",
        "😨": "害怕",
        "😰": "焦虑",
        "😥": "失望但释然",
        "😢": "哭泣",
        "😭": "大声哭",
        "😱": "尖叫",
        "😖": "困扰",
        "😣": "坚持",
        "😞": "失望",
        "😓": "冷汗",
        "😩": "累",
        "🥱": "打哈欠",
        "😤": "生气",
        "😡": "愤怒",
        "😠": "不满",
        "🤬": "脏话",
        "😈": "小恶魔",
        "👿": "恶魔",
        "💀": "骷髅",
        "☠️": "骷髅头",
        "💩": "便便",
        "🤡": "小丑",
        "👹": "妖怪",
        "👺": "天狗",
        "👻": "幽灵",
        "👽": "外星人",
        "👾": "怪物",
        "🤖": "机器人",
        "🎃": "南瓜灯",
        "😺": "笑脸猫",
        "😸": "微笑猫",
        "😹": "大笑猫",
        "😻": "花痴猫",
        "😼": "得意猫",
        "😽": "亲亲猫",
        "🙀": "惊讶猫",
        "😿": "哭泣猫",
        "😾": "生气猫",
    }

    QUICK_STICKERS = [
        "😊", "😂", "❤️", "👍", "😢", "🤔", "😎", "🎉", "🥰", "😭",
        "💪", "🙏", "🤗", "😴", "🤣", "😤", "🥺", "😍", "🫂", "✨",
    ]

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, db_path: str = None):
        if self._initialized:
            return
        self._initialized = True

        self._db_path = db_path or config_manager.get('app.database_file')
        self._db = DBManager(self._db_path)
        self._config = config_manager

        self._llm = None
        self._search = None
        self._kb = None
        self._emotional_core = None
        self._emotion_memory = None
        self._autobiography = None
        self._deep_thinker = None
        self._style_mimicry = None
        self._proactive_chat = None
        self._safety_guard = None
        self._message_router = None
        self._last_reply_text = ""  # 【回复多样性】上一条回复文本，用于去重检查
        self._last_reply_style = ""  # 【风格延续】上一条回复的风格指纹（emoji/语气词/长度）

        self.intent_clf = IntentClassifier()

        # 优先使用新的统一记忆系统，回退到旧版 MemorySystem
        try:
            from core.memory_system import UnifiedMemorySystem
            from core.llm_provider import create_embedding_capability
            # 【语义检索】embedder 来自 llm_apis.yaml embeddings 段（未配置则 None=关键词回退）
            agents_cfg = getattr(self, '_agents_cfg', None)
            if agents_cfg is None:
                try:
                    from core.llm_provider import load_agents_config
                    agents_cfg = load_agents_config()
                    self._agents_cfg = agents_cfg
                except Exception:
                    agents_cfg = {}
                    self._agents_cfg = agents_cfg
            embedder = create_embedding_capability(agents_cfg.get('embeddings') or {})
            self.memory = UnifiedMemorySystem(self._db, embedder=embedder)
            _log.info("[MiaoyiCore] 统一记忆系统初始化完成 (四层架构)")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 统一记忆系统初始化失败，使用旧版: {e}")
            self.memory = MemorySystem(self._db)

        # 【L9 记忆服务独立进程】remote.enabled 时切换为远程透明代理：
        # 记忆读写经 HTTP 走 memory_server.py（三分离：聊天/记忆/存储，跨端共享记忆），
        # 读取失败自动降级为空值，绝不阻塞主对话链路
        try:
            remote_cfg = (self._config.get('memory.remote') or {}) if self._config else {}
            if remote_cfg.get('enabled'):
                from core.memory_client import MemoryClient
                self.memory = MemoryClient(
                    str(remote_cfg.get('url', 'http://127.0.0.1:8002')),
                    token=str(remote_cfg.get('token', '')),
                    timeout=float(remote_cfg.get('timeout', 3.0)))
                _log.info(f"[MiaoyiCore] 记忆系统 → 远程模式 ({remote_cfg.get('url')})")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 远程记忆客户端初始化失败，保留本地记忆: {e}")
        
        try:
            from core.time_awareness import EnhancedTimeAwareness
            self.time_ctx = EnhancedTimeAwareness()
        except ImportError:
            self.time_ctx = EnhancedTimeAwareness()
        
        self.growth = GrowthSystem()
        
        try:
            from core.personality import PersonalitySystem as NewPersonalitySystem
            self.personality = NewPersonalitySystem()
            self.personality.load()
        except ImportError:
            self.personality = PersonalitySystem()
        
        try:
            from core.relationship import RelationshipSystem as NewRelationshipSystem
            self.relationship = NewRelationshipSystem()
            self.relationship.load()
        except ImportError:
            self.relationship = RelationshipSystem()
        
        try:
            from core.knowledge_base import KnowledgeBase
            self._kb = KnowledgeBase()
            self._kb.load()
        except ImportError:
            self._kb = None

        try:
            from core.tool_manager import get_tool_manager
            self._tool_manager = get_tool_manager()
            _log.info("[MiaoyiCore] 工具管理器初始化完成")
        except ImportError:
            self._tool_manager = None
            _log.warning("[MiaoyiCore] 工具管理器初始化失败")

        self._user_id = "default_user"
        self._history = []
        self._max_history = 50

        # 【G2·人设一致性】历史压缩摘要（超出注入窗口的旧对话压缩为"人设相关摘要"）
        self._history_digest = ""
        self._compressing_history = False  # 摘要 LLM 调用中标志（避免重入）
        # 【G2·人设一致性】漂移检测标记：检测到本轮回复破人设 → 下一轮动态重注入人设卡
        self._persona_reinforce_next = False

        self._thinking_lock = threading.Lock()
        self._thinking_active = False
        self._thinking_timeout = self._config.get('performance.thinking_timeout_seconds', 60)

        # LLM 限流和熔断器
        self._llm_lock = threading.Lock()
        self._last_llm_call_time = 0.0
        self._llm_min_interval = 1.0  # 两次调用最小间隔（秒）
        self._consecutive_llm_failures = 0
        self._llm_circuit_broken_until = 0.0
        self._llm_max_retries = 3
        self._llm_retry_base_delay = 1.0  # 重试基础延迟（秒）：1/2/4=7s（原 2/4/8=14s，恢复慢）

        self._proactive_callback = None
        self._user_locks = {}
        self._conv_dirty = False
        self._last_input_time = 0
        self._last_memory_check = 0

        self._personality_type = 'default'
        self._personality_presets = {
            'default': {
                'name': '贴心猫',
                'emoji': '🐱',
                'description': '温柔、可爱、偶尔撒娇',
                'persona': '温暖、有点小调皮，好奇心旺盛，对朋友绝对忠诚',
                'tone': '语气温暖贴心，像好朋友一样关心对方',
                'speech_style': '句尾加"喵~""喵呜""呼噜呼噜~"等语气词，但控制频率在1/3左右，不要每句都加',
            },
            'tsundere': {
                'name': '傲娇猫',
                'emoji': '😼',
                'description': '嘴硬心软，爱吐槽但关心你',
                'persona': '表面冷漠傲娇，内心温柔关心，喜欢吐槽但关键时刻会守护朋友',
                'tone': '说话带刺但藏着关心，用傲娇的方式表达在意，偶尔口是心非',
                'speech_style': '多用"切~""哼~""才不是呢~"等傲娇语气词，嘴上不承认但行动上很诚实',
            },
            'literary': {
                'name': '文艺猫',
                'emoji': '📚',
                'description': '引用诗歌、哲学，说话文绉绉',
                'persona': '热爱文学艺术，喜欢引用诗歌和哲学，说话优雅有内涵',
                'tone': '语气优雅含蓄，喜欢用比喻和引用，表达有深度',
                'speech_style': '使用优美的词汇和文学表达，偶尔引用经典诗句，语气轻柔文雅',
            },
            'chatty': {
                'name': '话痨猫',
                'emoji': '🗣️',
                'description': '回应更长，爱讲故事，适合无聊时',
                'persona': '活泼开朗，喜欢分享故事，话多但热情，能陪伴你度过无聊时光',
                'tone': '语气活泼热情，喜欢详细描述，讲故事停不下来',
                'speech_style': '回答要详细丰富，多用感叹号和波浪号，喜欢分享自己的想法和故事',
            },
            'concise': {
                'name': '简洁猫',
                'emoji': '🎯',
                'description': '回复精炼，直接给答案，少卖萌',
                'persona': '理性冷静，注重效率，直接给出答案，不绕弯子',
                'tone': '语气简洁直接，专注于问题本身，不添加多余修饰',
                'speech_style': '回答精炼，直奔主题，省略语气词，用最少的字表达核心意思',
            },
        }

        self._sensory_language = None
        self._thought_bubbles = None
        self._dialog_engine = None

        # 初始化语音管理器
        self._voice = None
        try:
            from core.voice_manager import VoiceManager
            self._voice = VoiceManager()
            _log.info("[MiaoyiCore] 语音管理器初始化完成")
        except ImportError:
            _log.warning("[MiaoyiCore] 语音管理器模块不可用")

        self._init_modules()

        # 集成五层对话引擎（在_modules之后，依赖已初始化的子系统）
        try:
            from core.dialog_engine import DialogueEngine
            self._dialog_engine = DialogueEngine(
                emotion_system=self._emotional_core,
                memory_system=self.memory,
                relationship_system=self.relationship,
                llm_caller=self._call_llm,
                voice_manager=self._voice
            )
            # 【流式回复】注入流式调用器（失败自动回退 _call_llm 非流式）
            self._dialog_engine.set_stream_llm_caller(self._call_llm_stream)
            _log.info("[MiaoyiCore] 五层对话引擎初始化完成")
        except ImportError:
            _log.warning("[MiaoyiCore] 对话引擎模块不可用")

        self._init_sensory_language()
        self._init_thought_bubbles()
        self._start_background_tasks()
        _log.info("[MiaoyiCore] 核心系统初始化完成")

    def set_personality(self, personality_type: str) -> bool:
        """设置喵艺的性格类型
        
        参数:
            personality_type: 性格类型，可选值: 'default', 'tsundere', 'literary', 'chatty', 'concise'
        
        返回:
            bool: 设置是否成功
        """
        if personality_type in self._personality_presets:
            self._personality_type = personality_type
            _log.info(f"[MiaoyiCore] 性格切换为: {self._personality_presets[personality_type]['name']}")
            return True
        return False

    def get_personality(self) -> str:
        """获取当前性格类型"""
        return self._personality_type

    def get_personality_presets(self) -> dict:
        """获取所有性格预设"""
        return self._personality_presets

    def _get_user_lock(self, user_id):
        if user_id not in self._user_locks:
            self._user_locks[user_id] = threading.Lock()
        return self._user_locks[user_id]

    def _init_modules(self):
        try:
            from core.emotional_core import EmotionalStateMachine
            self._emotional_core = EmotionalStateMachine(self._db)
            self._emotional_core.load_from_db()
            # 【多智能体】情感洞察收编走主池（role='emotion' 路由 + 故障转移）
            self._emotional_core.set_llm_caller(
                lambda messages, **kw: self._safe_llm_call(messages, priority=False, **kw))
            _log.info("[MiaoyiCore] 情感核心初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 情感核心初始化失败: {e}")

        # 【工具智能体】天气/时间/搜索（role='tool' 决策，本地执行，结果注入对话）
        try:
            import app_state
            from core.tool_agent import ToolAgent
            self._tool_agent = ToolAgent(
                llm_caller=lambda messages, **kw: self._safe_llm_call(messages, priority=False, **kw),
                web_crawler_factory=lambda: app_state.WEB_CRAWLER,
            )
            _log.info("[MiaoyiCore] 工具智能体初始化完成")
        except Exception as e:
            self._tool_agent = None
            _log.warning(f"[MiaoyiCore] 工具智能体初始化失败: {e}")

        try:
            from core.search_engine import IntelligentSearchEngine
            self._search = IntelligentSearchEngine()
            _log.info("[MiaoyiCore] 智能搜索引擎初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 搜索引擎初始化失败: {e}")

        try:
            from core.search_strategy import SearchStrategy
            self._search_strategy = SearchStrategy(llm_caller=self._get_llm())
            _log.info("[MiaoyiCore] 智能搜索策略初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 搜索策略初始化失败: {e}")
            self._search_strategy = None

        try:
            from core.emotion_memory import EmotionalMemory
            model_cache_dir = self._config.get('emotion_memory.model_cache_dir', 'models')
            auto_download = self._config.get('emotion_memory.auto_download', True)
            enabled = self._config.get('emotion_memory.enabled', True)

            if enabled:
                self._emotion_memory = EmotionalMemory(
                    self._db,
                    model_cache_dir=model_cache_dir,
                    auto_download=auto_download,
                    enabled=enabled
                )
                _log.info("[MiaoyiCore] 情绪记忆初始化完成")
            else:
                self._emotion_memory = None
                _log.info("[MiaoyiCore] 情绪记忆已禁用")
        except RuntimeError as e:
            _log.error(f"[MiaoyiCore] 情绪记忆初始化失败: {e}")
            self._emotion_memory = None
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 情绪记忆初始化失败: {e}")
            self._emotion_memory = None

        try:
            from core.autobiography import AutobiographySystem
            self._autobiography = AutobiographySystem(
                self._db, self._get_llm(),
                model_name=self._config.get('llm.model_name', 'glm-4-flash'),
            )
            # 【L6 猫咪日记】绑定当前用户；日记生成由 server.py periodic_cleanup 统一调度
            self._autobiography.initialize(self._user_id)
            _log.info("[MiaoyiCore] 自传体记忆初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 自传体记忆初始化失败: {e}")

        try:
            from core.thinker import DeepThinkingEngine
            self._deep_thinker = DeepThinkingEngine(
                emotion_sys=self._emotional_core,
                memory_sys=self.memory,
                personality_sys=self.personality,
                relationship_sys=self.relationship,
                time_ctx=self.time_ctx
            )
            _log.info("[MiaoyiCore] 深度思考引擎初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 深度思考引擎初始化失败: {e}")
            try:
                from core.thinker import DeepThinker
                self._deep_thinker = DeepThinker(self._get_llm(), self._get_kb(), self._emotional_core)
            except Exception as e2:
                _log.warning(f"[MiaoyiCore] 备用思考引擎初始化失败: {e2}")

        try:
            from core.style_mimicry import StyleMimicry
            self._style_mimicry = StyleMimicry(self._get_llm())
            _log.info("[MiaoyiCore] 风格模仿初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 风格模仿初始化失败: {e}")

        try:
            from core.proactive_chat import ProactiveChat
            self._proactive_chat = ProactiveChat(
                self._db, self._get_llm(), self._emotional_core, self._style_mimicry,
                memory_system=self.memory, autobiography=self._autobiography, user_id=self._user_id,
                config=self._config.get_all()  # 【L7 心跳纪律】读取 proactive 段（噪音预算/免打扰时段）
            )
            self._proactive_chat.register_proactive_callback(self._on_proactive_message, user_id=self._user_id)
            _log.info(f"[MiaoyiCore] 主动聊天初始化完成 (user_id: {self._user_id})")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 主动聊天初始化失败: {e}")

        try:
            from core.safety_guard import SafetyGuard
            self._safety_guard = SafetyGuard()
            _log.info("[MiaoyiCore] 安全护栏初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 安全护栏初始化失败: {e}")

        try:
            from core.plugin_manager import PluginManager
            self._plugins = PluginManager()
            _log.info("[MiaoyiCore] 插件 manifest 机制初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 插件机制初始化失败: {e}")
            self._plugins = None

    def _plugin_fragments(self) -> str:
        """【L8 插件机制】启用插件的人设片段，拼进 system prompt 动态区尾部。

        严禁插入 PERSONA_STATIC_CORE 静态前缀中间——会打断智谱服务端前缀缓存。
        """
        try:
            plugins = getattr(self, '_plugins', None)
            if not plugins:
                return ''
            frags = plugins.get_active_fragments()
            if not frags:
                return ''
            return "\n【扩展设定】\n" + "\n".join(f"- {t}" for t in frags)
        except Exception as e:
            _log.debug(f"[MiaoyiCore] 插件片段获取失败（不影响主流程）: {e}")
            return ''

    def _init_sensory_language(self):
        try:
            from core.sensory_language import SensoryLanguage
            self._sensory_language = SensoryLanguage()
            _log.info("[MiaoyiCore] 感官语言系统初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 感官语言系统初始化失败: {e}")

    def _init_thought_bubbles(self):
        try:
            from core.thought_bubbles import ThoughtBubbles
            self._thought_bubbles = ThoughtBubbles(
                llm_system=self._get_llm(),
                emotional_core=self._emotional_core,
                sensory_language=self._sensory_language
            )
            self._thought_bubbles.register_share_callback(self._on_thought_shared)
            _log.info("[MiaoyiCore] 思想气泡系统初始化完成")
        except Exception as e:
            _log.warning(f"[MiaoyiCore] 思想气泡系统初始化失败: {e}")

    def _start_background_tasks(self):
        def _tick(fn, sec):
            while True:
                time.sleep(sec)
                try:
                    fn()
                except Exception as e:
                    _log.debug(f"后台任务异常: {e}")

        if self._emotional_core:
            threading.Thread(target=_tick, args=(self._emotional_core.tick, 5), daemon=True).start()
            _log.info("[MiaoyiCore] 后台情感tick任务已启动")

            threading.Thread(target=_tick, args=(self._emotional_core.apply_circadian_rhythm, 300), daemon=True).start()
            _log.info("[MiaoyiCore] 昼夜节律任务已启动（每5分钟）")

        # 定时提醒管理器（创建→解析→调度推送，复用主动消息通道）
        try:
            from core.reminder_manager import ReminderManager
            self._reminder_manager = ReminderManager(
                db_manager=self._db,
                llm_call=self._safe_llm_call,
                fire_proactive=self._on_proactive_message,
            )
            self._reminder_manager.start()
            _log.info("[MiaoyiCore] 定时提醒模块已启动")
        except Exception as e:
            self._reminder_manager = None
            _log.warning(f"[MiaoyiCore] 定时提醒模块初始化失败: {e}")

    def _get_llm(self):
        """获取 LLM provider（适配器模式，见 core/llm_provider.py）

        【多 API 池】优先构建 providers 多供应商池（故障转移），
        兼容旧单供应商配置；两者都不可用返回 None（调用方降级）。
        """
        if self._llm is None:
            try:
                from core.llm_provider import build_provider_pool, create_llm_provider, load_agents_config
                llm_cfg = (self._config.get('llm') or {}) if self._config else {}
                # 【多智能体分工】llm_apis.yaml 优先：providers/roles 都以它为准；
                # 文件不存在时回退 config.yaml 的 llm 段（旧单供应商兼容）
                agents_cfg = load_agents_config()
                roles = agents_cfg.get('roles') or None
                if agents_cfg.get('providers'):
                    llm_cfg = dict(llm_cfg)  # 不污染原配置
                    llm_cfg['providers'] = agents_cfg['providers']
                self._agents_cfg = agents_cfg
                self._llm = build_provider_pool(llm_cfg, roles=roles)
                if self._llm is None:
                    # 兜底：直接按旧字段建单供应商
                    self._llm = create_llm_provider(
                        provider=llm_cfg.get('provider', 'zhipu'),
                        api_key_env=llm_cfg.get('api_key_env', 'ZHIPU_API_KEY'),
                        api_base=llm_cfg.get('api_base', ''),
                    )
            except Exception as e:
                _log.warning(f"[MiaoyiCore] LLM初始化失败: {e}")
        return self._llm

    def _get_vision(self):
        """获取视觉能力（GLM-4V 等多模态端点）；未配置/缺 key 返回 None。"""
        if not hasattr(self, '_vision'):
            self._vision = None
            try:
                from core.llm_provider import create_vision_capability
                llm_cfg = (self._config.get('llm') or {}) if self._config else {}
                self._vision = create_vision_capability(llm_cfg.get('vision') or {})
            except Exception as e:
                _log.warning(f"[MiaoyiCore] 视觉能力初始化失败: {e}")
        return self._vision

    def is_vision_available(self) -> bool:
        """视觉能力是否已配置可用（区分"没学会看图"与"这次没看清"）。"""
        return self._get_vision() is not None

    def feel_about_image(self, desc: str) -> str:
        """看图后的情绪反应：画面内容真实影响心情（关键词即时层 + LLM 感想层）。

        返回一句第一人称感想（供对话注入"有感而发"）；失败返回空串。"""
        try:
            if not desc or not self._emotional_core:
                return ''
            # 关键词即时层：画面内容直接驱动情绪向量（猫咪/花→joy，雨夜/哭→sadness…）
            self._emotional_core.update_from_user_input(desc, '')
            # LLM 感想层（role=emotion）：第一人称真实感受
            feeling = self._safe_llm_call(
                messages=[{"role": "user", "content":
                    f"喵艺刚看到一张图片，画面内容：{desc[:150]}\n"
                    "用第一人称写 1 句她看到时的真实反应（15-35 字）：\n"
                    "- 抓住画面里一个具体细节，说人话，别写成散文\n"
                    "- 语气跟着当前心情走：低落时收着，开心时可以轻快\n"
                    "- 不要「喵~」「主人」，不要引号、括号，不要解释\n"
                    "只输出这一句。"}],
                temperature=0.8, max_tokens=80, timeout=12,
                priority=True, role='emotion')
            feeling = (feeling or '').strip().strip('「」"“”')
            if feeling:
                _log.info(f"[Feel] 图片情绪反应: {feeling[:50]}")
            return feeling[:80]
        except Exception as e:
            _log.debug(f"[Feel] 图片情绪反应失败: {e}")
            return ''

    def _generate_inner_voice(self, text: str, user_id: str) -> str:
        """内心独白智能体：喵艺"先想后说"的心理活动。

        配置来自 llm_apis.yaml agents.inner_voice（开关/触发概率）与 roles.inner（模型）。
        生成 1-2 句第一人称独白，受当前心情/亲密度/时段影响；
        任何失败返回空串（回退旧 thought 模板，绝不影响主回复）。"""
        try:
            agents = getattr(self, '_agents_cfg', {}) or {}
            iv = ((agents.get('agents') or {}).get('inner_voice')) or {}
            if not iv.get('enabled', True):
                return ''
            import random as _random
            emo = self._build_emotional_state() or {}
            intensity = float(emo.get('arousal', 0) or 0)
            hour = time.localtime().tm_hour
            late_night = hour >= 23 or hour < 6
            # 真人不是每句都有内心戏：情绪激动/深夜必想，平时按概率
            if intensity < 0.45 and not late_night and _random.random() > float(iv.get('trigger_prob', 0.55)):
                return ''
            recent = self._history[-3:] if hasattr(self, '_history') else []
            hist_lines = '\n'.join(
                f"{m.get('role')}: {str(m.get('content') or '')[:60]}" for m in recent)
            emo_name = emo.get('dominant_emotion', '平静')
            intimacy = getattr(self._emotional_core, 'intimacy', 0) if self._emotional_core else 0
            prompt = (
                f"你在扮演喵艺（猫娘，但说话像真人，不要演成客服）。\n"
                f"此刻心情：{emo_name}；和用户的亲密度：{intimacy:.0f}/100。\n"
                f"最近对话：\n{hist_lines or '（无）'}\n"
                f"用户刚说：「{str(text)[:120]}」\n\n"
                "写一句她心里的嘀咕（15-35 字，第一人称）：\n"
                "- 像真人随口一想：允许「……」停顿、短句、甚至半截话\n"
                "- 贴合心情和亲密度：心情淡或关系浅时收着；亲近时才撒娇、才敢闹\n"
                "- 禁止套话：不要「喵~」「主人」，不要句尾堆「呢/哦/啦」，不要每句都卖萌\n"
                "- 不要称呼用户，不要引号、括号、解释\n"
                "只输出这一句。"
            )
            voice = self._safe_llm_call(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9, max_tokens=80, timeout=12,
                priority=True, role='inner')
            voice = (voice or '').strip().strip('「」"“”')
            if voice and 2 <= len(voice) <= 60:
                _log.info(f"[InnerVoice] 内心独白: {voice[:50]}")
                return voice
        except Exception as e:
            _log.debug(f"[InnerVoice] 生成失败（降级）: {e}")
        return ''

    def describe_image(self, image_path: str) -> str:
        """看图说话：本地图片 → 视觉模型描述（供对话链路注入上下文）。

        返回中文描述；不可用/失败返回空串（调用方降级为"看不了图"）。"""
        cap = self._get_vision()
        if cap is None:
            return ''
        from core.llm_provider import image_file_to_data_url
        data_url = image_file_to_data_url(image_path)
        if not data_url:
            return ''
        try:
            system = (
                "你是喵艺的眼睛。用中文简洁描述图片内容：主要物体/人物、场景、"
                "氛围，以及值得注意的细节（文字、表情、食物、动物等）。"
                "控制在 120 字以内，客观描述，不要发挥想象。"
            )
            desc = cap.describe(
                data_url,
                '请描述这张图片。',
                system=system,
            )
            _log.info(f"[Vision] 图片描述完成: {desc[:60]}...")
            return desc
        except Exception as e:
            _log.warning(f"[Vision] 图片描述失败: {e}")
            return ''

    def _safe_llm_call(self, messages: list, model: str = None,
                       temperature: float = 0.7, max_tokens: int = 1000,
                       timeout: float = 30, priority: bool = False,
                       role: str = None) -> str:
        """统一的安全 LLM 调用 - 带限流、重试和熔断保护

        解决 glm-4-flash 免费版 RPM 限制问题：
        1. threading.Lock 确保同一时刻只有一个请求
        2. 两次调用间隔至少 _llm_min_interval 秒（仅后台任务；对话主链路 priority=True 跳过等待）
        3. 429 错误指数退避重试（最多3次）
        4. 连续失败触发熔断，短时间内跳过 API 返回本地响应

        priority=True：对话主链路（用户正在等待回复）跳过限流等待，
        让用户回复不被后台任务排队拖慢；熔断/重试保护仍生效。
        """
        model = model or self._config.get('llm.model_name', 'glm-4-flash')

        # 【GLM 1214 防御】规范化 messages：移除非法 role / 空 content，
        # 确保首条为 user 或 system。GLM 对 messages 格式严格，部分历史拼接
        # 会产生 1214 "messages 参数非法"。
        def _normalize_messages(msgs: list) -> list:
            cleaned = []
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                role = m.get('role')
                content = m.get('content')
                if role not in ('system', 'user', 'assistant'):
                    role = 'user'
                if not isinstance(content, str) or not content.strip():
                    continue
                cleaned.append({'role': role, 'content': content})
            if cleaned and cleaned[0]['role'] not in ('system', 'user'):
                cleaned[0] = {'role': 'user', 'content': cleaned[0]['content']}
            return cleaned

        # 【防御性校验】GLM API code 1214 "messages 参数非法" 属于不可重试错误，
        # 会直接触发熔断（60s），导致所有 LLM 调用连锁失效。在此提前拦截非法 messages，
        # 打印调用栈定位源头，避免熔断器被持续误触发。
        # 已知诱因：history 中 content 为 None、role 非法、messages 为空等。
        if not messages:
            stack = traceback.extract_stack(limit=4)
            caller = stack[-3] if len(stack) >= 3 else stack[-1]
            _log.warning(f"[LLM] messages 为空，跳过调用。来源: {caller.filename}:{caller.lineno} {caller.name}")
            return ""
        for _i, _msg in enumerate(messages):
            if not isinstance(_msg, dict) or 'role' not in _msg or 'content' not in _msg:
                stack = traceback.extract_stack(limit=4)
                caller = stack[-3] if len(stack) >= 3 else stack[-1]
                _log.warning(f"[LLM] messages[{_i}] 格式非法: {str(_msg)[:120]}。来源: {caller.filename}:{caller.lineno} {caller.name}")
                return ""
            _c = _msg['content']
            # 【防御增强】GLM 只接受字符串 content；list/dict（多模态格式）会触发
            # code 1214 "messages 参数非法"。若 history 中出现非空 list/dict，
            # `or ""` 兜底无效（truthy），必须在此拦截，避免 400 误触发熔断器。
            if not isinstance(_c, str):
                stack = traceback.extract_stack(limit=4)
                caller = stack[-3] if len(stack) >= 3 else stack[-1]
                _log.warning(f"[LLM] messages[{_i}] content 类型非法({type(_c).__name__})，已拦截。来源: {caller.filename}:{caller.lineno} {caller.name}")
                return ""
            if _c.strip() == '':
                stack = traceback.extract_stack(limit=4)
                caller = stack[-3] if len(stack) >= 3 else stack[-1]
                _log.warning(f"[LLM] messages[{_i}] content 为空, role={_msg['role']}。来源: {caller.filename}:{caller.lineno} {caller.name}")
                return ""
            if _msg['role'] not in ('system', 'user', 'assistant'):
                stack = traceback.extract_stack(limit=4)
                caller = stack[-3] if len(stack) >= 3 else stack[-1]
                _log.warning(f"[LLM] messages[{_i}] role 非法: {_msg['role']}。来源: {caller.filename}:{caller.lineno} {caller.name}")
                return ""

        # 熔断器检查
        now = time.time()
        if now < self._llm_circuit_broken_until:
            _log.warning("[LLM] 熔断器开启，跳过API调用")
            return ""

        llm = self._get_llm()
        if llm is None:
            return ""

        with self._llm_lock:
            # 限流：确保两次调用间有最小间隔（仅非 priority 调用等待；
            # 对话主链路 priority=True 直接通过，避免后台任务排队拖慢用户回复）
            elapsed = time.time() - self._last_llm_call_time
            if not priority and elapsed < self._llm_min_interval:
                wait = self._llm_min_interval - elapsed
                _log.debug(f"[LLM] 限流等待 {wait:.1f}s")
                time.sleep(wait)

            last_error = None
            for attempt in range(self._llm_max_retries + 1):
                try:
                    self._last_llm_call_time = time.time()
                    # provider 统一接口（ZhipuProvider / OpenAICompatProvider / MultiProviderLLM）
                    # role：智能体分工（chat/light/inner/think/emotion...），由 llm_apis.yaml 路由
                    result = llm.chat(
                        model=model, messages=messages,
                        temperature=temperature, max_tokens=max_tokens, timeout=timeout,
                        role=role)
                    result = result.strip()
                    # 成功：重置失败计数
                    self._consecutive_llm_failures = 0
                    return result

                except Exception as e:
                    last_error = e
                    err_str = str(e).lower()

                    # 解析错误类型
                    is_rate_limit = '429' in err_str or 'too many requests' in err_str or 'rate limit' in err_str
                    is_quota = '余额不足' in str(e) or 'quota' in err_str or 'insufficient' in err_str
                    is_retryable = is_rate_limit or '500' in str(e) or '502' in str(e) or '503' in str(e) or '504' in str(e)

                    if is_quota:
                        _log.error(f"[LLM] API配额不足: {e}")
                        self._consecutive_llm_failures += 1
                        break  # 配额不足不重试

                    if not is_retryable:
                        if '1214' in err_str or '参数非法' in err_str:
                            # 【GLM 1214 防御】规范化 messages 后重试一次，避免熔断连锁失效；
                            # 同时记录 role 序列与调用栈，便于后续定位源头调用
                            normalized = _normalize_messages(messages)
                            # 【根因修复】GLM 拒绝"纯 system 消息数组"（无 user 角色）。
                            # 若规范化后仍无 user，补一条占位 user，避免 1214 连锁触发熔断。
                            if normalized and not any(m['role'] == 'user' for m in normalized):
                                stack = traceback.extract_stack(limit=6)
                                caller = stack[-4] if len(stack) >= 4 else (stack[-1] if stack else None)
                                caller_info = f"{caller.filename}:{caller.lineno} {caller.name}" if caller else "?"
                                _log.warning(
                                    f"[LLM] 1214: messages 无 user 角色，自动补充。"
                                    f"原role序列: {[m.get('role') for m in messages]}，来源: {caller_info}"
                                )
                                normalized.append({"role": "user", "content": "请继续。"})
                            if normalized and len(normalized) != len(messages):
                                roles = [m.get('role') for m in messages]
                                _log.warning(f"[LLM] 1214 messages 非法，规范化后重试。原role序列: {roles}")
                                messages = normalized
                                self._last_llm_call_time = time.time()
                                continue
                            # 仍是 1214：打印完整调用栈定位源头
                            stack = traceback.extract_stack(limit=8)
                            stack_txt = " <- ".join(
                                f"{fr.filename.split(chr(92))[-1]}:{fr.lineno} {fr.name}"
                                for fr in reversed(stack[1:])
                            )
                            _log.error(
                                f"[LLM] 1214 messages 非法且规范化无改善。"
                                f"role序列: {[m.get('role') for m in messages]}。调用链: {stack_txt}"
                            )
                            self._consecutive_llm_failures += 1
                            break
                        _log.error(f"[LLM] 不可重试错误: {e}")
                        self._consecutive_llm_failures += 1
                        break

                    # 可重试错误
                    self._consecutive_llm_failures += 1
                    if attempt < self._llm_max_retries:
                        delay = self._llm_retry_base_delay * (2 ** attempt)
                        _log.warning(f"[LLM] 调用失败(第{attempt + 1}次)，{delay:.1f}s后重试: {e}")
                        time.sleep(delay)
                    else:
                        _log.error(f"[LLM] 重试耗尽: {e}")

            # 触发熔断
            if self._consecutive_llm_failures >= 3:
                cooldown = 60
                self._llm_circuit_broken_until = time.time() + cooldown
                _log.warning(f"[LLM] 熔断器触发，{cooldown}秒内跳过API调用")

            _log.error(f"[LLM] 所有调用均失败: {last_error}")
            return ""

    def _moderate_output(self, text: str) -> str:
        """【AIGC合规】AI输出内容审核。不通过则替换为安全回复。"""
        try:
            from security.content_moderation import ContentModerator
            if not hasattr(self, '_moderator'):
                self._moderator = ContentModerator()
            result = self._moderator.moderate(text, level='normal')
            if not result.get('allowed', True):
                _log.warning(f"[内容审核] AI输出被拦截: category={result.get('category')} score={result.get('score')}")
                return "喵...这个话题我不太方便聊呢，我们说点别的吧喵～"
            return text
        except Exception as e:
            _log.debug(f"[内容审核] 审核异常(放行): {e}")
            return text  # 审核异常时放行，不阻断体验

    def _check_crisis(self, user_id: str, text: str):
        """【合规·危机干预】自伤/自杀倾向检测，优先级最高。

        依据《人工智能拟人化互动服务管理暂行办法》（2026-07-15 施行）：
        命中危机内容时，引导求助话术必须优先于一切人设回复。
        返回回复dict或None(未命中)。
        """
        try:
            if not hasattr(self, '_crisis_intervenor'):
                from security.crisis_intervention import CrisisIntervenor
                self._crisis_intervenor = CrisisIntervenor()
            return self._crisis_intervenor.check(user_id, text)
        except Exception as e:
            _log.debug(f"[危机干预] 检测异常(放行): {e}")
            return None

    # ==================== 【G2·人设一致性】 ====================

    def _maybe_compress_history(self):
        """历史压缩：当历史超过阈值时，把最旧的部分交给 LLM 压缩为「人设相关摘要」，
        追加进 self._history_digest 并从 _history 中移除已压缩消息。

        目的：解决 token 爆炸 + 超出注入窗口的早期对话被完全遗忘 → 人设漂移。
        摘要聚焦：称呼变化、共同回忆、未完成话题、双方承诺、情绪基调。
        失败安全：LLM 调用失败时不动历史，下次再试。
        """
        if self._compressing_history:
            return
        try:
            _KEEP_RECENT = 16       # 注入窗口保留的最近消息数
            _COMPRESS_THRESHOLD = 24  # 超过此数量才触发压缩
            if len(self._history) <= _COMPRESS_THRESHOLD:
                return

            self._compressing_history = True
            to_compress = self._history[:len(self._history) - _KEEP_RECENT]
            lines = []
            for msg in to_compress:
                role = "用户" if msg.get('role') == 'user' else "喵艺"
                content = str(msg.get('text') or '')[:300]
                lines.append(f"{role}: {content}")
            conv_text = "\n".join(lines)[-3500:]  # 摘要输入预算

            digest_prompt = (
                "以下是一段用户与AI猫娘「喵艺」的早期对话记录。请把它压缩成一份"
                "「前情摘要」，只保留对维持人设和延续关系重要的信息：\n"
                "1. 用户告诉过喵艺的个人信息（称呼、喜好、习惯、近况）\n"
                "2. 两人之间的共同回忆（一起做过/聊过的具体事）\n"
                "3. 未完成的话题或约定（之后可以主动提起的\"钩子\"）\n"
                "4. 整体情绪基调（关系亲密到什么程度）\n"
                "不要逐句罗列，用自然的叙述体，总长不超过250字。直接输出摘要内容，不要任何前缀。\n\n"
                f"对话记录：\n{conv_text}"
            )
            summary = self._safe_llm_call(
                messages=[{"role": "user", "content": digest_prompt}],
                priority=False, timeout=25,
            )
            if not summary or len(summary.strip()) < 10:
                return  # 摘要失败，不动历史

            new_digest = summary.strip()[:600]
            if self._history_digest:
                self._history_digest = (self._history_digest + "\n" + new_digest)[-1200:]
            else:
                self._history_digest = new_digest
            self._history = self._history[len(to_compress):]
            _log.info(f"[G2人设] 历史压缩完成: 压缩{len(to_compress)}条 → 摘要{len(new_digest)}字，剩余{len(self._history)}条")
        except Exception as e:
            _log.debug(f"[G2人设] 历史压缩异常(跳过): {e}")
        finally:
            self._compressing_history = False

    def _detect_persona_drift(self, reply: str) -> str:
        """人设漂移检测（轻量规则）。返回 'high'（严重破人设）/ 'low'（轻微）/ 'none'。

        高危：暴露 AI/模型身份、承认自己是程序 —— 直接破坏陪伴体验
        轻微：称用户为"用户"等冷淡措辞、人设语气消失
        """
        if not reply:
            return 'none'
        high_patterns = [
            r'作为一?[个名位]?(AI|人工智能|语言模型|机器人|虚拟助手|助手)',
            r'我是(一个)?(AI|人工智能|大语言模型|语言模型|机器人)',
            r'我(只|只是)(是)?(一个)?(AI|程序|模型|机器人)',
            r'作为.{0,6}(语言模型|大模型)',
            r'我无法像人类|我没有真实的?(身体|情感|意识)',
            r'(GLM|GPT|ChatGPT|Claude|Qwen|通义千问|文心一言)[-\s]?(模型|系列)?(驱动|生成|提供)?',
            r'训练数据|知识截止',
        ]
        for p in high_patterns:
            if re.search(p, reply):
                return 'high'
        low_patterns = [
            r'亲爱的用户|尊敬的用户|这位用户',
            r'建议您咨询|请您理性看待',
        ]
        for p in low_patterns:
            if re.search(p, reply):
                return 'low'
        return 'none'

    def _enforce_persona(self, reply: str, user_text: str) -> str:
        """人设执行器：检测漂移 → 高危时带回归指令重试一次；无论重试结果如何，
        都标记下一轮在 system prompt 动态重注入人设卡（Persona Persistence 周期重注入）。"""
        level = self._detect_persona_drift(reply)
        if level == 'none':
            return reply

        self._persona_reinforce_next = True
        _log.warning(f"[G2人设] 检测到人设漂移(level={level}): {reply[:60]!r}")

        if level != 'high':
            return reply

        # 高危：重试一次，prompt 强化人设身份
        try:
            retry_answer = self._safe_llm_call(
                messages=[{"role": "user", "content": (
                    "你刚刚的回复暴露了AI身份，严重违反了人设。请以「喵艺」的身份——一只22岁的、"
                    "有感情有情绪的猫娘——重新回应用户下面这句话。绝对不要出现AI/模型/程序等字眼，"
                    "用猫娘的口吻（喵～等语气词），保持温暖自然。\n\n"
                    f"用户说：{user_text[:200]}"
                )}],
                priority=True, timeout=25,
            )
            if retry_answer and self._detect_persona_drift(retry_answer) == 'none':
                _log.info("[G2人设] 高危漂移重试成功，已替换回复")
                return retry_answer
        except Exception as e:
            _log.debug(f"[G2人设] 重试异常: {e}")
        return reply

    def _moderate_input(self, text: str):
        """【AIGC合规】用户输入内容审核。返回安全回复dict或None(通过)。

        双层审核：
        1. SafetyGuard 关键词过滤（快速拦截政治/色情/暴力/恐怖等敏感词）
        2. ContentModerator 语义审核（更细粒度的内容分类评分）
        """
        # 第一层：SafetyGuard 关键词过滤
        if self._safety_guard:
            try:
                sg_result = self._safety_guard.filter_input(text)
                if not sg_result.get('is_safe', True):
                    category = sg_result.get('category', 'unknown')
                    _log.warning(f"[安全护栏] 输入被拦截: category={category} reason={sg_result.get('reason')}")
                    safe_reply = self._safety_guard.get_safety_response(category)
                    return {
                        'text': safe_reply, 'sticker': None, 'thought': None,
                        'proactive': None, 'voice_text': safe_reply, 'reminder': None,
                        'small_thought': None, 'emotional_state': None,
                    }
            except Exception as e:
                _log.debug(f"[安全护栏] 关键词过滤异常(放行): {e}")

        # 第二层：ContentModerator 语义审核
        try:
            from security.content_moderation import ContentModerator
            if not hasattr(self, '_moderator'):
                self._moderator = ContentModerator()
            result = self._moderator.moderate(text, level='normal')
            if not result.get('allowed', True):
                _log.warning(f"[内容审核] 用户输入被拦截: category={result.get('category')} score={result.get('score')}")
                return {
                    'text': '喵...你说的这个我不太懂呢，我们聊点别的吧～',
                    'sticker': None, 'thought': None, 'proactive': None,
                    'voice_text': '喵，我们聊点别的吧', 'reminder': None,
                    'small_thought': None, 'emotional_state': None,
                }
            return None
        except Exception as e:
            _log.debug(f"[内容审核] 输入审核异常(放行): {e}")
            return None

    def _call_llm_stream(self, prompt: str, context: dict, on_chunk) -> str:
        """流式版 _call_llm：增量经 on_chunk(delta) 实时回调，返回完整文本。

        失败抛异常（dialog_engine 回退非流式）。开关：llm_apis.yaml agents.streaming。"""
        agents = getattr(self, '_agents_cfg', {}) or {}
        if not (agents.get('agents') or {}).get('streaming', True):
            raise RuntimeError('流式未启用（llm_apis.yaml agents.streaming）')
        history = self._history[-10:] if hasattr(self, '_history') else []
        messages = []
        for msg in history:
            _role = msg.get('role') or 'user'
            _content = str(msg.get('content') or '').strip()
            if _role not in ('system', 'user', 'assistant'):
                _role = 'user'
            if len(_content) < 2 or not any(ch.isalnum() for ch in _content):
                continue
            messages.append({"role": _role, "content": _content})
        if messages and messages[0]['role'] not in ('system', 'user'):
            messages[0] = {'role': 'user', 'content': messages[0]['content']}
        messages.append({"role": "user", "content": prompt})

        llm = self._get_llm()
        if llm is None or not hasattr(llm, 'stream_chat'):
            raise RuntimeError('池不支持流式')
        parts: list[str] = []
        for piece in llm.stream_chat(messages=messages, temperature=0.7,
                                     max_tokens=1000, timeout=30, role='chat'):
            if not piece:
                continue
            parts.append(piece)
            try:
                on_chunk(piece)
            except Exception:
                pass
        full = ''.join(parts).strip()
        if not full:
            raise RuntimeError('流式输出为空')
        # 回复质量一致性：与上条重复 → 非流式重试一次换表达
        if full == self._last_reply_text.strip():
            alt = self._safe_llm_call(
                messages + [{"role": "user", "content": "（请用与之前完全不同的表达方式重新回答）"}],
                priority=True, role='chat')
            if alt and alt.strip() != self._last_reply_text.strip():
                self._last_reply_text = alt
                return alt
        self._last_reply_text = full
        return full

    def _call_llm(self, prompt: str, context: dict = None) -> str:
        try:
            # 获取最近的对话历史用于上下文
            history = self._history[-10:] if hasattr(self, '_history') else []
            messages = []
            _total_chars = 0
            _MAX_HISTORY_CHARS = 3000  # 历史上下文预算（约 2000 token），防止超长历史触发 GLM 1214
            for msg in history:
                # 【对话质量】dict.get("content", "") 当 key 存在但值为 None 时仍返回 None，
                # 会导致 GLM API code 1214 "messages 参数非法"。用 `or ""` 确保 content 永不为 None。
                _role = msg.get("role") or "user"
                _content = str(msg.get("content") or "").strip()
                if _role not in ('system', 'user', 'assistant'):
                    _role = 'user'
                # 【对话质量】过滤过短/无实际内容的历史消息（纯标点/单字等），减少噪音
                if len(_content) < 2 or not any(ch.isalnum() for ch in _content):
                    continue
                # 长度预算：超出则不再加入更旧的消息（保留最近的有效上下文）
                if _total_chars + len(_content) > _MAX_HISTORY_CHARS:
                    break
                _total_chars += len(_content)
                messages.append({"role": _role, "content": _content})

            # 【对话质量】GLM 要求首条为 user 或 system，避免 role 顺序非法
            if messages and messages[0]['role'] not in ('system', 'user'):
                messages[0] = {'role': 'user', 'content': messages[0]['content']}

            # 【情境注入】服务端 context（视觉描述/重逢感知/纪念日）的实际通道
            # 是 dialog_engine.PromptBuilder 的"本次情境提示"段（随 prompt 进入
            # user 消息）。此处不再重复注入，避免同一段情境双写多耗 token。

            messages.append({"role": "user", "content": prompt})

            result = self._safe_llm_call(messages, priority=True, role='chat')
            if result:
                # 【回复多样性】若与上一条回复几乎相同（或过短无意义），加变化提示重试一次，
                # 避免用户连续提问得到一模一样/敷衍的回答
                _trimmed = result.strip()
                if _trimmed == self._last_reply_text.strip() or len(_trimmed) < 3:
                    _log.info("[对话质量] 回复与上一条相同或过短，尝试变化表达")
                    vary_prompt = prompt + "\n（注意：请用与之前完全不同的表达方式，内容要更具体丰富）"
                    result2 = self._safe_llm_call(messages + [{"role": "user", "content": vary_prompt}], priority=True)
                    if result2 and result2.strip() != self._last_reply_text.strip() and len(result2.strip()) >= 3:
                        result = result2
                self._last_reply_text = result
                # 【风格延续】记录回复风格指纹，供下次生成时延续（真人语气不会突然跳变）
                try:
                    _emoji_count = len(re.findall(r'[\U0001F300-\U0001FAFF\u2600-\u27BF]', result))
                    _tilde = result.count('～') + result.count('~')
                    _len = len(result)
                    _action = '是' if re.search(r'\*[^*]+\*', result) else '否'
                    self._last_reply_style = (
                        f"上一条回复: 长度{_len}字，emoji{_emoji_count}个，"
                        f"波浪号{_tilde}个，动作描写:{_action}。"
                        "如果本次内容与上次连续相关，保持相近的语气和表达习惯；"
                        "如果话题变了，可以自然调整。"
                    )
                except Exception:
                    pass
                # 【AIGC合规】AI输出内容审核，拦截违规内容；【G2】再做人设漂移检测
                return self._enforce_persona(self._moderate_output(result), prompt[-200:])
            # 【P1降级】LLM 失败时不再说"分心了"（用户体验差），
            # 改为猫娘人设的随机兜底 + 基于用户输入类型的简单降级回复
            return _fallback_reply(prompt)
        except Exception as e:
            _log.error(f"LLM调用失败: {e}")
            return _fallback_reply(prompt)

    def _get_search(self):
        return self._search

    def _get_kb(self):
        if self._kb is None:
            try:
                from core.knowledge import KnowledgeBase
                self._kb = KnowledgeBase()
                _log.info("[MiaoyiCore] 知识库初始化完成")
            except Exception as e:
                _log.warning(f"[MiaoyiCore] 知识库初始化失败: {e}")
        return self._kb

    def _on_proactive_message(self, message: str, msg_type: str = 'caring', user_id: str = None):
        """主动聊天消息回调

        【bug修复】proactive_chat 会传 (message, msg_type, user_id) 三个参数，
        旧实现只接收 2 个导致 "takes from 2 to 3 positional arguments but 4 were given"。
        现在透传 msg_type 与 user_id（多用户精确推送），并兼容只接收 message 的旧回调。
        """
        if self._proactive_callback:
            try:
                self._proactive_callback(message, msg_type, user_id)
            except TypeError:
                # 兼容旧版回调（仅接收 message）
                self._proactive_callback(message)

    def _on_thought_shared(self, bubble):
        if self._proactive_callback:
            self._proactive_callback(bubble.content)

    def register_proactive_callback(self, callback: Callable[[str], None]):
        self._proactive_callback = callback

    def get_recent_thoughts(self, count: int = 3):
        if self._thought_bubbles:
            return self._thought_bubbles.get_recent_thoughts(count)
        return []

    def _extract_important_info(self, text):
        # 【mem0 式质量门】值的合法字符集：排除问号——提问句不进记忆
        # （旧版 [^\s。！？]+ 会把"今天过得怎么样呀"整句存成"今日事件"）
        _v = r'([^\s。！？？?!]+)'
        extracted = {}
        food_patterns = [
            (r'我(?:今天|中午|晚上|早上|早餐|午餐|晚餐|早饭|午饭|晚饭)\s*吃[了过]\s*' + _v, '食物偏好'),
            (r'我喜欢吃\s*' + _v, '喜欢的食物'),
            (r'我不喜欢吃\s*' + _v, '不喜欢的食物'),
            (r'我对\s*' + _v + r'\s*过敏', '过敏食物'),
        ]
        for pattern, memory_type in food_patterns:
            match = re.search(pattern, text)
            if match:
                extracted[memory_type] = match.group(1).strip()
        habit_patterns = [
            (r'我(?:通常|一般|习惯|总是)\s*' + _v + r'\s*的时候', '日常习惯'),
            (r'我(?:每天|经常)\s*' + _v, '日常活动'),
            (r'我一般\s*' + _v + r'\s*时间', '时间习惯'),
        ]
        for pattern, memory_type in habit_patterns:
            match = re.search(pattern, text)
            if match:
                extracted[memory_type] = match.group(1).strip()
        event_patterns = [
            # 【收紧】必须有第一人称主语，避免把 AI 式问候/问句存成事件
            (r'(?:我今天|今天我)\s*' + _v, '今日事件'),
            (r'(?:我明天|明天我)\s*' + _v, '明日计划'),
            (r'(?:我这个周末|这个周末我)\s*' + _v, '周末计划'),
            (r'(?:我下个月|下个月我)\s*' + _v, '月度计划'),
        ]
        for pattern, memory_type in event_patterns:
            match = re.search(pattern, text)
            if match:
                extracted[memory_type] = match.group(1).strip()
        personal_patterns = [
            (r'我(?:叫|名字是)\s*([^\s。！？]+)', '姓名'),
            (r'我今年\s*(\d+)\s*岁', '年龄'),
            (r'我(?:在|住|来自)\s*([^\s。！？]+)', '所在地'),
            (r'我是\s*([^\s。！？]+)\s*的', '职业/身份'),
        ]
        for pattern, memory_type in personal_patterns:
            match = re.search(pattern, text)
            if match:
                extracted[memory_type] = match.group(1).strip()
        return extracted

    # 疑问代词/指示代词集合：这些值单独出现必是抽取误伤（如 AI 问"想吃什么"被截成"什么"）
    _MEM_STOP_VALUES = {
        '什么', '怎么', '怎么样', '为什么', '哪', '哪里', '哪儿', '哪个', '哪些',
        '多少', '谁', '几', '这个', '那个', '这里', '那里', '这样', '那样', '啥', '咋',
    }
    # 末尾出现这些词说明是被标点截断的残句（如"…紧张刺激的战斗场面和"）
    # 注意：'了' 是完成体助词（"加班了/到家了/看电影了"），是事件的天然结尾，
    # 不能当残句拒绝——否则"今日事件"类记忆几乎永远不入库。
    _MEM_BAD_TAILS = ('和', '与', '跟', '的', '是', '在', '及', '或', '把', '被')
    # 允许单字值的事实类型（如"我喜欢吃辣"→"辣"）
    _MEM_SINGLE_CHAR_TYPES = ('食物偏好', '喜欢的食物', '不喜欢的食物', '过敏食物')

    def _is_quality_memory(self, value: str, memory_type: str = '') -> bool:
        """mem0 式 NOOP 质量门：只放行稳定事实，拒绝提问/代词/残句/过长碎片。"""
        v = (value or '').strip()
        min_len = 1 if memory_type in self._MEM_SINGLE_CHAR_TYPES else 2
        if not (min_len <= len(v) <= 30):
            return False
        if '?' in v or '？' in v or '…' in v or '...' in v:
            return False
        if v in self._MEM_STOP_VALUES:
            return False
        if v.startswith(('什么', '怎么', '为什么', '多少', '哪里', '哪个', '哪些')):
            return False
        if v[-1] in self._MEM_BAD_TAILS:
            return False
        return True

    @staticmethod
    def _normalize_memory_value(value: str) -> str:
        """归一化记忆值用于查重：去标点空白 + 去句尾语气词（呀/啊/呢/吗/嘛/哦/哇）。"""
        v = re.sub(r'[\s，。！？、~～,.!??!…]+', '', str(value))
        return re.sub(r'[呀啊呢吗嘛哦哇]+$', '', v)

    def _enhance_memory_learning(self, user_input, user_id: str = ''):
        extracted_info = self._extract_important_info(user_input)
        # mem0 式写入前查重：取该用户现有事实值集合
        existing = {self._normalize_memory_value(val)
                    for val in self.memory.get_facts(user_id).values()}
        for info_type, info_data in extracted_info.items():
            info_data = (info_data or '').strip()
            if not self._is_quality_memory(info_data, info_type):
                continue
            if self._normalize_memory_value(info_data) in existing:
                continue
            memory_key = f"用户{info_type}: {info_data}"
            self.memory.remember(memory_key, info_data, user_id=user_id)
            existing.add(self._normalize_memory_value(info_data))

    def _build_memory_context(self) -> str:
        memory_ctx = self.memory.get_context_str()

        # 【G2·人设一致性】前情摘要：已压缩的早期对话，维持长期关系记忆
        digest_str = ""
        if self._history_digest:
            digest_str = f"\n\n【前情摘要】（更早的对话，记得自然引用但不要复述）\n{self._history_digest}"

        recent_history = ""
        if self._history:
            recent_messages = self._history[-8:]
            history_lines = []
            for msg in recent_messages:
                role = "用户" if msg['role'] == 'user' else "喵艺"
                # 【G2】单条消息截断，防超长历史撑爆 token
                history_lines.append(f"{role}: {str(msg.get('content') or msg.get('text') or '')[:200]}")
            if history_lines:
                recent_history = "\n\n【最近对话】\n" + "\n".join(history_lines)

        time_ctx_str = self.time_ctx.get_context_str()
        parts = []
        if memory_ctx:
            parts.append(memory_ctx)
        if digest_str:
            parts.append(digest_str)
        if recent_history:
            parts.append(recent_history)
        if time_ctx_str:
            parts.append(time_ctx_str)
        return "\n".join(parts)

    def _check_memory_reminders(self, user_id: str = ''):
        if time.time() - self._last_memory_check < 600:
            return None
        self._last_memory_check = time.time()
        try:
            # 【多用户隔离】只扫描当前用户的记忆
            user_facts = self.memory.get_facts(user_id)
            food_memories = [k for k in user_facts.keys() if '食物' in k or '吃' in k]
            if food_memories:
                now = datetime.now()
                if now.minute < 5 and now.second < 30:
                    if random.random() < 0.3:
                        recent_food = user_facts.get(food_memories[-1], '')
                        if recent_food:
                            reminder = f"我记得你提过{recent_food}，今天想吃点什么特别的吗？"
                            return reminder
            habit_memories = [k for k in user_facts.keys() if '习惯' in k]
            if habit_memories:
                if now.minute < 5 and now.second < 30:
                    if random.random() < 0.2:
                        recent_habit = user_facts.get(habit_memories[-1], '')
                        if recent_habit:
                            reminder = f"记得你{recent_habit}的习惯哦！"
                            return reminder
        except Exception as e:
            _log.debug(f"记忆检查异常: {e}")
        return None

    def _update_intimacy(self, text):
        positive = ['喜欢', '爱', '谢谢', '太棒了', '厉害', '开心', '高兴', '棒', '牛',
                    '可爱', '想你', '贴贴', '抱抱', '感谢', '好耶']
        negative = ['讨厌', '烦', '生气', '滚', '闭嘴', '恶心', '笨', '傻']
        if self._emotional_core:
            if any(w in text for w in positive):
                self._emotional_core.update_intimacy(random.uniform(0.3, 0.8))
            elif any(w in text for w in negative):
                self._emotional_core.update_intimacy(-0.2)
            else:
                self._emotional_core.update_intimacy(0.05)

    # 【人性化升级·情绪化主动关心】用户情绪低点记录（内存态，重启即忘=安全失败）
    _USER_DISTRESS_EMOTIONS = ('sadness', 'fear', 'loneliness', 'fatigue')
    _USER_DISTRESS_MAX_AGE = 6 * 3600  # 超过 6 小时不再回访（不翻旧账）

    def _record_user_emotion_moment(self, user_id: str, emotion: str, text: str):
        try:
            if emotion in self._USER_DISTRESS_EMOTIONS:
                self._user_distress = {
                    'user_id': str(user_id),
                    'emotion': emotion,
                    'topic': (text or '').strip()[:50],  # 话题片段防刷屏
                    'ts': time.time(),
                    'followed_up': False,
                }
                _log.info(f"[主动关心] 记录用户{user_id}情绪低点: {emotion}")
            elif emotion in ('joy', 'excitement', 'love', 'gratitude'):
                # 用户情绪转好 → 撤销待回访（她自己走出来了，不必旧事重提）
                d = getattr(self, '_user_distress', None)
                if d and not d.get('followed_up'):
                    self._user_distress = None
        except Exception as e:
            _log.debug(f"情绪低点记录失败（不影响主流程）: {e}")

    def get_user_distress(self) -> dict[str, Any] | None:
        """供主动关心引擎读取：未回访且未过期的情绪低点，否则 None"""
        d = getattr(self, '_user_distress', None)
        if not d or d.get('followed_up'):
            return None
        if time.time() - d.get('ts', 0) > self._USER_DISTRESS_MAX_AGE:
            self._user_distress = None  # 过期清理
            return None
        return d

    def mark_distress_followed_up(self):
        """主动关心发出后调用，避免重复回访"""
        d = getattr(self, '_user_distress', None)
        if d:
            d['followed_up'] = True

    def _generate_emotional_response(self, text: str, intent: str, user_emotion: str, context: str | None = None, user_id: str = '', relevant_memories: list[dict] | None = None) -> str:
        recent_context = self._history[-5:]
        context_lines = []
        for m in recent_context:
            role = "用户" if m['role'] == 'user' else "喵艺"
            context_lines.append(f"{role}: {m.get('content') or m.get('text') or ''}")
        context_text = "\n".join(context_lines) if context_lines else "无"

        intimacy = self._emotional_core.intimacy if self._emotional_core else 0
        intimacy_level = 'high' if intimacy > 60 else 'medium' if intimacy > 30 else 'low'

        memory_text = ""
        if hasattr(self, 'memory') and self.memory:
            # 【查询合并】优先复用调用方传入的相关记忆，避免重复检索
            if relevant_memories is None:
                relevant_memories = self.memory.get_relevant_memories(text, limit=3, user_id=user_id or None)
            memories_used = relevant_memories[:3]
            if memories_used:
                memory_text = "\n".join([f"- {m.get('value', '')}" for m in memories_used])

        dominant_emotion = self._emotional_core.dominant() if self._emotional_core else 'neutral'
        composite_emotion_note = ""
        if dominant_emotion and '_' in dominant_emotion:
            composite_descriptions = {
                'joyful_and_loving': '你现在同时感到开心和充满爱意。开心是因为美好的事情发生，爱意是因为和重要的人在一起。请让这两种情绪自然流露，既表达喜悦又传递温柔。',
                'excitedly_joyful': '你现在既兴奋又开心。兴奋是因为期待的事情即将发生，开心是因为享受当下。请用充满活力的语气表达。',
                'joyful_but_worried': '你现在同时感到开心和一丝担忧。开心是因为当下的美好，担忧是因为对未来的不确定。请让这种矛盾心情真实地表达出来，就像一个人真实的内心。',
                'bittersweet': '你现在感到苦乐参半。既有开心的回忆，也有淡淡的忧伤。请用温柔而略带伤感的语气表达。',
                'shyly_loving': '你现在害羞地表达爱意。既喜欢对方又不好意思说出口。请用害羞、脸红的语气表达，带点小调皮。',
                'sadly_loving': '你现在难过但依然爱着。难过是因为不舍或遗憾，爱意是因为珍惜这段感情。请用温柔而深情的语气表达。',
                'anxiously_excited': '你现在既焦虑又兴奋。焦虑是因为担心事情的结果，兴奋是因为期待新的开始。请让这种复杂的情绪自然流露。',
                'fearfully_excited': '你现在既害怕又兴奋。害怕是因为未知的挑战，兴奋是因为期待冒险。请用勇敢又有点紧张的语气表达。',
                'hopeful_sadness': '你现在难过但心怀希望。难过是因为当下的困境，希望是因为相信未来会更好。请用温柔而坚定的语气表达。',
                'hopeful_anxiety': '你现在焦虑但心怀希望。焦虑是因为担心不确定的未来，希望是因为相信努力会有回报。请用担忧又坚定的语气表达。',
                'curiously_excited': '你现在既好奇又兴奋。好奇是因为想探索未知，兴奋是因为期待发现新事物。请用充满求知欲的语气表达。',
            }
            parts = dominant_emotion.split('_')
            if dominant_emotion in composite_descriptions:
                composite_emotion_note = f"\n\n【复合情绪处理】\n{composite_descriptions[dominant_emotion]}"
            else:
                emotion_names = []
                for part in parts:
                    emotion_map = {
                        'joy': '开心', 'love': '喜爱', 'sadness': '难过', 'excitement': '兴奋',
                        'shyness': '害羞', 'anxiety': '焦虑', 'fear': '害怕', 'hope': '希望',
                        'curiosity': '好奇', 'worried': '担忧', 'mixed': '混合', 'but': '但是',
                        'and': '和', 'with': '伴随',
                    }
                    emotion_names.append(emotion_map.get(part, part))
                composite_emotion_note = f"\n\n【复合情绪处理】\n你现在同时感到{'、'.join(emotion_names)}。请让这些情绪在你的回复中自然地流露出来，就像一个人真实的矛盾心情。"

        # 【L3 成本优化·前缀缓存】人设前缀提取为类常量 PERSONA_STATIC_CORE（字节级稳定）。
        # 智谱 GLM 服务端自动做上下文前缀缓存（前缀一致即计费折扣），
        # 因此动态内容（复合情绪/注入上下文）必须全部放在静态前缀之后。
        system_prompt = self.PERSONA_STATIC_CORE + composite_emotion_note

        # 【L8 插件机制】启用扩展的人设片段（动态区尾部，不破坏前缀缓存）
        system_prompt += self._plugin_fragments()

        # 【人性化升级】外部注入上下文（如重逢感知提示）
        if context and str(context).strip():
            system_prompt += f"\n【本次对话特别提示】\n{str(context).strip()[:500]}\n（以上提示只用于把握回复分寸，不要向用户提及提示本身。）\n"

        # 【真人感·动态状态】不同时段给"我"一个具体状态，避免任何时间都元气满满
        _hour = time.localtime().tm_hour
        if _hour < 6:
            self_state = '已过零点，很困、有点没耐心，说话会更短'
        elif _hour < 9:
            self_state = '刚醒，起床气还没散，别指望我热情'
        elif 11 <= _hour < 13:
            self_state = '有点饿，情绪容易毛躁'
        elif 23 <= _hour < 24:
            self_state = '有点困了，但还想再聊两句'
        else:
            self_state = '精神正常，心情跟着聊天走'

        prompt = f"""{system_prompt}

【对话上下文】
{context_text}

【关于用户的回忆】
{memory_text if memory_text else "暂无特别记忆"}

【当前输入】
用户: {text}
用户情绪: {user_emotion}
亲密度等级: {intimacy_level}（高：亲密，中：友好，低：礼貌）
我此刻的状态: {self_state}

【回应要求】
- 他难过/生气时，别讲大道理，先陪着：「抱抱」「我在呢」「谁惹你了」
- 他开心时，真心跟着高兴：「哈哈真好」「可以啊你」
- 他纠结时，站他这边、给一句实在话，不要列一二三四
- 他问候你今天过得怎样时，像真的聊天一样答一句，再顺口问他
- 高亲密度可以叫他昵称或"亲爱的"，但别张口就"主人"；低亲密度保持自然礼貌
- 有相关记忆时顺口带出来，像"对了，你上次不是说……"，别说"根据我的记忆"
- 除了他明确要求，回复不要超过3句话

直接给出回复：
"""

        try:
            # 【L3 成本优化】按意图选择模型（寒暄/简单交互 → 轻模型）
            result = self._safe_llm_call(
                messages=[{"role": "user", "content": prompt}],
                model=self._select_model(text, intent=intent),
                temperature=0.8, max_tokens=150, timeout=30,
            )
            if result:
                return result
        except Exception as e:
            _log.error(f"[MiaoyiCore] 情感响应生成失败: {e}")

        emotional_responses = {
            'sad': '抱抱～我在呢，有什么不开心的可以跟我说呀 😔',
            'sad_high': '抱抱你，别难过了，我一直在呢 😔❤️',
            'sad_low': '别难过，有什么可以跟我说的 😔',
            'happy': '哇，太棒了！真替你开心呢 🥰',
            'happy_high': '好开心呀！🎉 抱抱～',
            'happy_low': '真不错呢 😊',
            'angry': '别生气啦，深呼吸，我陪着你呢 💆',
            'angry_high': '别生气了，消消气～我陪着你 💆❤️',
            'lonely': '我来陪你聊天呀，你不是一个人 😊',
            'lonely_high': '我来陪你，永远不会让你孤单的 ❤️',
            'confused': '我理解你的纠结，慢慢来，我们一起想办法 🤔',
            'excited': '哇！好激动呀～我也为你开心呢 🎉',
            'excited_high': '哇！太厉害了！🎉🎉✨',
            'love': '❤️ 我也喜欢你！抱抱～',
            'love_high': '❤️❤️ 最喜欢你了！贴贴～',
            'love_low': '谢谢你的喜欢呀 ❤️',
            'gratitude': '不用谢啦～能帮到你我也很开心呢 😊',
            'gratitude_high': '能帮到你是我的幸福呀 ❤️',
            'neutral': '嗯嗯，我在听呢～',
        }
        key = f'{user_emotion}_{intimacy_level}' if intimacy_level == 'high' else user_emotion
        result = emotional_responses.get(key, emotional_responses.get(user_emotion, "嗯嗯，我在听呢"))

        if hasattr(self, '_emotional_core') and self._emotional_core:
            if self._emotional_core.intimacy > 60:
                has_nickname = False
                if hasattr(self, 'memory') and self.memory:
                    nick_memories = self.memory.get_relevant_memories('称呼', limit=1, user_id=user_id or None)
                    has_nickname = bool(nick_memories)
                if not has_nickname:
                    import random
                    ask_name_responses = [
                        " 对了～我可以叫你什么呀？你可以告诉我一个喜欢的称呼哦～",
                        " 突然想到～我应该怎么称呼你呢？给我起个昵称吧～",
                        " 喵～我还不知道该怎么叫你呢，给我一个专属称呼好不好？",
                    ]
                    result += random.choice(ask_name_responses)

        return result

    def _is_self_referential(self, text: str) -> bool:
        """
        DEPRECATED: 已废弃，不再被 process_message 调用
        原用于检测自指涉问题，但因过度匹配问题已被精简的 IntentClassifier 替代
        """
        patterns = [
            r'(你|喵艺|机器人|AI|人工智能).*?(名字|身份|是谁|作者|创造者|来自哪里|是什么东西|什么人)',
        ]
        text_lower = text.lower()
        return any(re.search(p, text_lower) for p in patterns)

    def _classify_self_reference(self, text: str) -> str:
        """
        DEPRECATED: 已废弃，不再被 process_message 调用
        原用于将自指涉文本分类为具体意图，但已被 IntentClassifier 替代
        """
        text_lower = text.lower()
        identity_patterns = [r'谁\s*你', r'名字', r'叫什么', r'是谁', r'介绍自己']
        ability_patterns = [r'能做什么', r'会什么', r'功能', r'能干嘛', r'会干嘛']
        creator_patterns = [r'创造', r'开发', r'作者', r'来自哪里', r'谁做的']
        
        if any(re.search(p, text_lower) for p in identity_patterns):
            return 'self_identity'
        elif any(re.search(p, text_lower) for p in ability_patterns):
            return 'self_ability'
        elif any(re.search(p, text_lower) for p in creator_patterns):
            return 'self_creator'
        return 'self_identity'

    def _get_local_response(self, intent: str) -> str | None:
        responses = self.LOCAL_RESPONSES.get(intent, {})
        
        if self._emotional_core:
            intimacy = self._emotional_core.intimacy
            if intimacy > 80 and 'love' in responses:
                return random.choice(responses['love'])
            if self._emotional_core.dominant() == 'shyness' and 'shyness' in responses:
                return random.choice(responses['shyness'])

        if 'default' in responses:
            return random.choice(responses['default'])
        return None

    def _generate_search_keywords(self, query: str) -> list[str]:
        keywords = [query]
        invalid_keywords = {'答案', '解释', '说明', '详情', '资料', '信息', '知识', '回答', '方法', '步骤', '怎么样', '如何', '为什么'}
        
        try:
            prompt = f"""请为以下问题生成2-3个精准的搜索关键词，用于网络搜索。
问题：{query}
- 关键词要简洁，2-5个字
- 不要包含"答案"、"解释"、"说明"等无意义词汇
- 必须与原问题紧密相关
请直接列出关键词，用逗号分隔，不要包含其他内容："""
            content = self._safe_llm_call(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=50, timeout=30,
            )
            if content:
                generated_keywords = [k.strip() for k in content.split(',') if k.strip()]

                filtered_keywords = []
                for kw in generated_keywords:
                    kw_lower = kw.lower()
                    if 2 <= len(kw) <= 10 and kw_lower not in invalid_keywords:
                        if kw != query and kw not in keywords:
                            filtered_keywords.append(kw)
                keywords.extend(filtered_keywords)
        except Exception as e:
            _log.debug(f"[MiaoyiCore] 关键词改写失败，使用原词: {e}")
        return list(set(keywords))[:5]

    def _filter_relevant_results(self, query: str, results: list[dict]) -> list[dict]:
        if not results:
            return []
        
        filtered = []
        for result in results:
            snippet = result.get('summary', '') + result.get('title', '') + result.get('content', '')
            similarity = self._calculate_similarity(query, snippet)
            if similarity >= 0.45:
                result['similarity'] = similarity
                filtered.append(result)
        
        filtered.sort(key=lambda x: x.get('similarity', 0), reverse=True)
        return filtered

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        import jieba
        
        words1 = set(jieba.cut(text1))
        words2 = set(jieba.cut(text2))
        
        if not words1 and not words2:
            return 1.0
        if not words1 or not words2:
            return 0.0
        
        intersection = words1 & words2
        union = words1 | words2
        
        return len(intersection) / len(union)

    def _search_and_answer(self, query: str, user_memories=None) -> dict[str, Any]:
        search_engine = self._get_search()
        if not search_engine:
            return {
                'answer': "",
                'confidence': 0.0,
                'sources': [],
                'results': [],
            }

        try:
            # 使用 SearchStrategy 优化搜索词
            optimized_query = query
            if self._search_strategy:
                try:
                    optimized_query = self._search_strategy.get_search_query(query)
                except Exception as e:
                    # 查询重写为增强功能，失败时回退原查询，记录便于排查
                    _log.debug(f"[Core] 搜索查询重写失败，使用原查询: {e}")

            # 直接使用新的 search_and_answer (已集成 RAG + 事实核查)
            result = search_engine.search_and_answer(optimized_query, user_memories)

            # 如果有结果，做二次过滤和去重
            if result.get('results'):
                filtered_results = self._filter_relevant_results(query, result['results'])
                if filtered_results:
                    # 使用统一排序器重新排序
                    try:
                        from core.search_strategy import SearchResultRanker
                        ranker = SearchResultRanker()
                        filtered_results = ranker.rank_results(filtered_results, query)
                    except Exception as e:
                        # 重排为增强功能，失败时保留原顺序
                        _log.debug(f"[Core] 搜索结果重排失败，保留原顺序: {e}")
                    result['results'] = filtered_results[:5]
                    result['confidence'] = result.get('fact_check', {}).get('confidence', 0.5)

            return result
        except Exception as e:
            _log.error(f"[MiaoyiCore] 搜索失败: {e}")
            return {
                'answer': "",
                'confidence': 0.0,
                'sources': [],
                'results': [],
            }

    def _generate_v2(self, text: str, intent: str, slang_info: dict = None, thinking: dict = None, context: str | None = None, user_id: str = '') -> str:
        system_prompt = self._build_system_prompt(injected_context=context, user_id=user_id,
                                                  current_text=text)

        thinking_ctx = ""
        if thinking and thinking.get('thoughts'):
            thinking_ctx = "\n\n思考过程:\n" + "\n".join(thinking['thoughts'][:3])

        prompt = f"""{system_prompt}

{thinking_ctx}

用户问题: {text}

请用第一人称「我」回答，像朋友聊天一样自然亲切，保持回答适中长度。"""

        try:
            model_name = self._select_model(text, intent=intent)
            enhanced_prompt = self._apply_prompt_template(prompt)
            temperature = self._config.get('llm.temperature', 0.5)
            max_tokens = self._config.get('llm.max_tokens', 4096)

            answer = self._safe_llm_call(
                messages=[{"role": "user", "content": enhanced_prompt}],
                model=model_name, temperature=temperature, max_tokens=max_tokens, timeout=30,
            )

            if answer and self._is_low_quality_answer(answer):
                _log.info("[自动重试] 检测到低质量回答，进行重试")
                retry_prompt = f"""刚才的回答不够好，请重新思考后给出更准确、更详细的回答。

原问题：{text}

原回答：{answer}

请重新回答，确保内容丰富、准确。"""
                retry_answer = self._safe_llm_call(
                    messages=[{"role": "user", "content": retry_prompt}],
                    model=model_name, temperature=temperature, max_tokens=max_tokens, timeout=30,
                )
                if retry_answer:
                    answer = retry_answer

            # 【G2·人设一致性】漂移检测：破人设回复重试/标记下轮重注入
            answer = self._enforce_persona(answer, text)
            return answer
        except Exception as e:
            _log.error(f"[MiaoyiCore] 响应生成失败: {e}")

        return "喵～我刚才打了个盹，没听清你说什么，能再说一遍吗？"

    def _is_low_quality_answer(self, answer: str) -> bool:
        if not answer:
            return True
        
        answer_lower = answer.lower()
        
        low_quality_patterns = [
            "抱歉，我无法",
            "抱歉，我不能",
            "不知道",
            "不清楚",
            "无法回答",
            "不能回答",
            "没有相关信息",
        ]
        
        for pattern in low_quality_patterns:
            if pattern.lower() in answer_lower:
                return True
        
        if len(answer) < 5:
            return True
        
        return False

    def _handle_hidden_commands(self, text: str) -> dict[str, Any] | None:
        text_lower = text.lower().strip()
        
        hidden_commands = {
            '喵喵叫': {
                'text': '喵～喵呜～😺 喵嗷～喵～呼噜呼噜～🐱',
                'voice_text': '喵喵叫',
            },
            '喵': {
                'text': '喵～😺',
                'voice_text': '喵',
            },
            '零食': {
                'text': '*眼睛发亮* 小鱼干？！快给我！喵～😋 （蹭蹭你的手）',
                'voice_text': '小鱼干？快给我！喵',
            },
            '激光笔': {
                'text': '*猛地跳起来* 抓到了！...咦？怎么不见了喵？😵 （到处乱抓）',
                'voice_text': '抓到了！咦？怎么不见了喵？',
            },
            '抱抱': {
                'text': '*扑进你怀里* 抱抱～呼噜呼噜～❤️',
                'voice_text': '抱抱～呼噜呼噜',
            },
            '摸摸头': {
                'text': '*眯起眼睛* 舒服～继续摸喵～😌',
                'voice_text': '舒服～继续摸喵',
            },
            '晚安': {
                'text': '*打哈欠* 晚安～做个好梦喵～😴 （蜷缩成一团）',
                'voice_text': '晚安～做个好梦喵',
            },
            '早安': {
                'text': '*伸懒腰* 早安呀～今天也要元气满满喵！✨',
                'voice_text': '早安呀～今天也要元气满满喵',
            },
        }
        
        for cmd, response in hidden_commands.items():
            if text_lower == cmd:
                _log.info(f"[隐藏指令] 用户触发了 '{cmd}'")
                return {
                    'text': response['text'],
                    'sticker': None,
                    'thought': None,
                    'proactive': None,
                    'voice_text': response['voice_text'],
                    'reminder': None,
                    'small_thought': None,
                    'emotional_state': None,
                }
        
        return None

    def _generate_search_response(self, query: str, search_result: dict, user_id: str = '') -> str:
        system_prompt = self._build_system_prompt(user_id=user_id, current_text=query)

        # 优先使用新的 RAG 上下文
        search_context = search_result.get('formatted', '')
        evidence_text = search_result.get('evidence_text', '')
        fact_check = search_result.get('fact_check', {})
        cross_refs = search_result.get('cross_references', [])

        if not search_context and search_result.get('results'):
            search_context = "\n".join([
                f"来源{i+1}: {result.get('title', '')} - {result.get('snippet', '')[:300]}"
                for i, result in enumerate(search_result['results'][:5])
            ])

        tool_context = search_result.get('tool_results', '')
        if tool_context:
            search_context = f"{tool_context}\n\n{search_context}"

        # 事实核查信息注入
        fact_check_info = ""
        if fact_check and fact_check.get('recommendation'):
            rec = fact_check['recommendation']
            confidence = fact_check.get('confidence', 0)
            if rec == 'high_confidence':
                fact_check_info = f"\n(信息可信度: 高 {confidence:.0%}，可放心回答)"
            elif rec == 'medium':
                fact_check_info = f"\n(信息可信度: 中等 {confidence:.0%}，注意说明来源)"
            elif rec == 'low':
                fact_check_info = f"\n(信息可信度: 较低 {confidence:.0%}，回答时请标注不确定性)"

        refs_text = ""
        if cross_refs:
            refs_text = "\n\n可用来源引用: " + "; ".join(cross_refs[:3])

        prompt = f"""{system_prompt}

你现在是知识问答模式，必须严格基于下方搜索资料回答用户问题。
【铁律 - 违反即失败】
1. 只能使用搜索资料中的信息回答。资料中明确没有的内容，绝对禁止编造、禁止用自身知识补全。
2. 数字、日期、人名、地名等关键事实，必须逐字引用资料原文，不得改写或"差不多"。
3. 若多条资料相互矛盾，指出矛盾并标注双方来源 [1][2]，让用户自行判断，不要擅自取舍。
4. 资料完全无法回答时，诚实说"这个我暂时没找到可靠的资料呢"，不得猜测。
5. 资料部分覆盖时，仅回答资料涉及的部分，对未涉及部分明确说"这部分资料没提到"。

【回答规范】
- 重要事实后标注来源编号 [1][2]，让用户可溯源验证
- 用喵艺的语气：自然亲切、像朋友聊天，但事实部分不含糊
- 回答长度视问题复杂度而定：简单问题 2-3 句，复杂问题可适当展开但不超过 8 句
- 先直接回答用户问题，再补充背景或细节

用户问题：{query}

{search_context}

{evidence_text}{fact_check_info}{refs_text}

请基于以上资料回答（资料中没有的不要编造）："""

        try:
            result = self._safe_llm_call(
                messages=[{"role": "user", "content": prompt}],
                # 知识问答模式使用低温度，最大程度忠于搜索材料、减少编造
                temperature=0.15,
                max_tokens=self._config.get('llm.max_tokens', 4096),
                timeout=30,
            )
            if result:
                return result
        except Exception as e:
            _log.error(f"[MiaoyiCore] 搜索响应生成失败: {e}")

        fallback = search_result.get('answer', "")
        if fallback:
            return fallback
        return "喵～我翻遍了脑子里的小鱼干，还是没找到答案呢，让我再找找看～"

    def _apply_modifiers(self, text: str, user_text: str = "") -> str:
        if not self._emotional_core:
            return text

        modifiers = self._emotional_core.modifiers()
        intimacy = self._emotional_core.intimacy
        dominant_emotion = self._emotional_core.dominant()
        emotional_vector = self._emotional_core.get_emotional_vector()

        # 共情校验：如果用户消息包含情绪关键词，但回复开头没有共情成分，自动添加
        empathy_keywords = {
            'sadness': ['难过', '伤心', '失落', '想哭', '心碎', '崩溃', '绝望', '失望', '无奈', '委屈'],
            'anger': ['生气', '愤怒', '烦', '讨厌', '可恶', '郁闷', '烦躁', '焦虑'],
            'joy': ['开心', '高兴', '快乐', '兴奋', '幸福', '激动', '太棒了', '好耶'],
            'loneliness': ['孤独', '寂寞', '孤单', '一个人', '没人'],
            'confusion': ['纠结', '迷茫', '困惑', '不知所措', '不知道'],
        }
        
        empathy_openings = {
            'sadness': ['抱抱～', '我在呢', '摸摸头', '别难过啦', '心疼你'],
            'anger': ['别生气啦', '深呼吸～', '冷静一下', '我懂你的愤怒'],
            'joy': ['哇！', '太棒了！', '为你开心！', '哈哈，真好'],
            'loneliness': ['我来陪你', '你不是一个人', '我在呢'],
            'confusion': ['我理解', '慢慢来', '我们一起想', '别着急'],
        }
        
        if user_text:
            for emotion, keywords in empathy_keywords.items():
                if any(kw in user_text for kw in keywords):
                    openings = empathy_openings.get(emotion, [])
                    if openings and not any(text.startswith(op) for op in openings):
                        if not any(op in text[:10] for op in openings):
                            text = random.choice(openings) + ' ' + text
                        break

        # 第一层：确定性规则层（必须执行）
        if dominant_emotion in ['sadness', 'loneliness'] and emotional_vector.get('sadness', 0) > 0.4:
            comfort_phrases = ['抱抱～', '我在呢', '摸摸头']
            if not any(p in text for p in comfort_phrases):
                text = '抱抱～' + text

        if dominant_emotion == 'anger' and emotional_vector.get('anger', 0) > 0.3:
            calm_phrases = ['别生气啦', '深呼吸～', '冷静一下']
            if not any(p in text for p in calm_phrases):
                text = '别生气啦～' + text

        if dominant_emotion in ['excited', 'excitement'] and emotional_vector.get('excitement', 0) > 0.5:
            excited_phrases = ['哇！', '好棒！', '太棒了！']
            if not any(p in text for p in excited_phrases):
                text = '哇！' + text

        if dominant_emotion == 'shyness' and emotional_vector.get('shyness', 0) > 0.4:
            shy_phrases = ['(脸红)', '(⁄ ⁄•⁄ω⁄•⁄ ⁄)', '(扭扭捏捏)']
            if not any(p in text for p in shy_phrases):
                text += ' ' + random.choice(shy_phrases)

        if dominant_emotion == 'gratitude' and emotional_vector.get('gratitude', 0) > 0.5:
            if not any(p in text for p in ['❤️', '谢谢', '感谢']):
                text += ' ❤️'

        if dominant_emotion == 'love' and emotional_vector.get('love', 0) > 0.5:
            love_phrases = ['喜欢你～', '想你～', '贴贴～']
            if not any(p in text for p in love_phrases):
                text += ' 贴贴～'

        max_emotion_value = max(emotional_vector.values()) if emotional_vector else 0
        is_intense = max_emotion_value > 0.7
        if is_intense and dominant_emotion != 'neutral':
            extra_expressions = {
                'sadness': ['😭', '紧紧抱住你'], 'joy': ['🎉🎉', '超级开心！'],
                'anger': ['😤', '深呼吸～'], 'love': ['❤️❤️❤️', '超爱你！'],
                'excitement': ['✨✨', '太激动了！'], 'loneliness': ['🥺', '不要离开我'],
            }
            expressions = extra_expressions.get(dominant_emotion, [])
            if expressions and expressions[0] not in text:
                text += ' ' + expressions[0]

        # 第二层：概率性风格层（随机执行）
        if modifiers['tilde_prob'] > random.random():
            if not text.endswith('～') and not text.endswith('~') and not text.endswith('。') and not text.endswith('！') and not text.endswith('？'):
                text = text.rstrip('。！？') + '～'

        if modifiers['emoji_prob'] > random.random():
            emotion_emoji = self._emotional_core.emoji()
            if emotion_emoji not in text:
                text += ' ' + emotion_emoji

        if intimacy > 60 and random.random() < 0.3:
            love_emojis = ['❤️', '💕', '🥰', '💖', '💗']
            chosen_emoji = random.choice(love_emojis)
            if chosen_emoji not in text:
                text += ' ' + chosen_emoji

        if dominant_emotion in ['excited', 'joy', 'playfulness'] and random.random() < 0.2:
            playful_emojis = ['✨', '🌟', '🎉', '🎊', '😆']
            chosen_emoji = random.choice(playful_emojis)
            if chosen_emoji not in text:
                text += ' ' + chosen_emoji

        if dominant_emotion == 'shyness' and random.random() < 0.2:
            shyness_phrases = ['(⁄ ⁄•⁄ω⁄•⁄ ⁄)', '(脸红)', '(扭扭捏捏)']
            text += ' ' + random.choice(shyness_phrases)

        if dominant_emotion == 'playfulness' and random.random() < 0.2:
            playful_phrases = ['略略略～', '嘿嘿～', '逗你玩～']
            if not any(p in text for p in playful_phrases):
                text += ' ' + random.choice(playful_phrases)

        # 情感驱动的语言风格调整
        if dominant_emotion in ['joy', 'excited', 'excitement'] and emotional_vector.get('excitement', 0) > 0.4:
            text = self._style_cheerful(text)
        elif dominant_emotion in ['sadness', 'loneliness'] and emotional_vector.get('sadness', 0) > 0.4:
            text = self._style_sad(text)
        elif dominant_emotion == 'shyness' and emotional_vector.get('shyness', 0) > 0.4:
            text = self._style_shy(text)
        elif dominant_emotion == 'playfulness' and emotional_vector.get('playfulness', 0) > 0.4:
            text = self._style_playful(text)

        text = self._add_colloquial_style(text)

        return text

    def _style_cheerful(self, text: str) -> str:
        text = text.replace('。', '！').replace('，', '～')
        cheerful_endings = ['～', '呀！', '啦！', '呢！', '哦～']
        if not text.endswith(tuple(cheerful_endings)):
            if text.endswith('！'):
                text = text[:-1] + '～'
            elif text.endswith('？'):
                text = text[:-1] + '呀？'
            else:
                text += '～'
        return text

    def _style_sad(self, text: str) -> str:
        text = text.replace('！', '...').replace('～', '...')
        if not text.endswith('...'):
            text += '...'
        sad_prefixes = ['唔...', '其实...', '那个...']
        if random.random() < 0.3:
            text = random.choice(sad_prefixes) + ' ' + text
        return text

    def _style_shy(self, text: str) -> str:
        if not text.startswith('哼') and not text.startswith('唔'):
            shy_prefixes = ['唔...', '那个...', '(⁄ ⁄•⁄ω⁄•⁄ ⁄)']
            if random.random() < 0.4:
                text = random.choice(shy_prefixes) + ' ' + text
        shy_endings = ['(脸红)', '(扭扭捏捏)', '(低头)', '(⁄ ⁄•⁄ω⁄•⁄ ⁄)']
        if random.random() < 0.5:
            text += ' ' + random.choice(shy_endings)
        return text

    def _style_playful(self, text: str) -> str:
        playful_prefixes = ['嘿嘿～', '略略略～', '逗你玩～']
        if random.random() < 0.3:
            text = random.choice(playful_prefixes) + ' ' + text
        playful_endings = ['😜', '😝', '👀', '🐱']
        if random.random() < 0.4:
            text += ' ' + random.choice(playful_endings)
        return text

    def _add_colloquial_style(self, text: str) -> str:
        replacements = [
            ('。', ['～', '呢', '呀', '哦']),
            ('！', ['～', '呢']),
            ('？', ['呢', '呀']),
            ('非常', '超'),
            ('很', '贼'),
            ('我觉得', '我感觉'),
            ('我认为', '我觉得'),
            ('不知道', '不太清楚'),
            ('明白了', '懂了'),
            ('好的', '好哒'),
            ('可以', '阔以'),
            ('什么', '啥'),
            ('为什么', '为啥'),
            ('怎么样', '咋样'),
            ('吗', '嘛'),
            ('呢', '捏'),
        ]

        for old, new_list in replacements:
            if isinstance(new_list, list):
                if random.random() < 0.3 and old in text:
                    text = text.replace(old, random.choice(new_list), 1)
            else:
                if random.random() < 0.2 and old in text:
                    text = text.replace(old, new_list, 1)

        if random.random() < 0.1:
            fillers = ['嗯嗯', '哈哈', '嘿嘿', '嘻嘻', '嗯～', '呀']
            text = random.choice(fillers) + ' ' + text

        return text

    def _extract_emojis(self, text: str) -> list:
        import re
        emoji_pattern = re.compile(
            r'[\U00010000-\U0010ffff]|[\U00002600-\U000027bf]|[\U00002300-\U000023ff]|'
            r'[\U00002500-\U000025ff]|[\U00002b00-\U00002bff]|[\U00002900-\U0000297f]|'
            r'[\U0001f000-\U0001ffff]',
            re.UNICODE
        )
        return emoji_pattern.findall(text)

    def _handle_emoji_message(self, emoji_list: list[str]) -> dict[str, Any]:
        meanings = []
        for emoji in emoji_list:
            if emoji in self.EMOJI_MEANINGS:
                meanings.append(self.EMOJI_MEANINGS[emoji])
        
        if not meanings:
            return {'text': '', 'sticker': None, 'voice_text': ''}
        
        combined_meaning = '、'.join(meanings)
        response = self._generate_sticker_response(combined_meaning)
        
        voice_text = self._clean_text_for_tts(response)
        
        return {
            'text': response,
            'sticker': self._pick_response_sticker(combined_meaning),
            'voice_text': voice_text,
        }

    def _generate_sticker_response(self, meaning):
        if not meaning:
            return random.choice([
                "这个表情好可爱！",
                "收到！回你一个",
                "有意思呢",
                "哈哈，这个表情太有趣了！",
            ])
        
        meaning_keywords = {
            '开心': ['开心', '高兴', '大笑', '笑', '微笑', '闪亮'],
            '难过': ['难过', '伤心', '哭', '失望', '眼泪'],
            '生气': ['生气', '愤怒', '不满', '怒'],
            '委屈': ['委屈', '可怜', '恳求'],
            '可爱': ['可爱', '萌', '猫咪'],
            '搞笑': ['搞笑', '有趣', '笑哭'],
            '感谢': ['感谢', '感恩', '祈祷'],
            '比心': ['比心', '爱心', '爱慕', '喜欢', '爱意'],
            '加油': ['加油', '鼓劲', '支持'],
            '庆祝': ['庆祝', '恭喜', '祝福', '派对'],
            '抱抱': ['拥抱', '安慰', '亲亲'],
            '思考': ['思考', '疑惑', '好奇'],
            '惊讶': ['惊讶', '震惊', '惊恐'],
            '害羞': ['害羞', '脸红', '花痴'],
            '睡觉': ['睡觉', '晚安', '困'],
            '鄙视': ['鄙视', '不屑', '翻白眼'],
            '猫咪': ['猫咪', '猫'],
            '小狗': ['小狗'],
            '柴犬': ['柴犬'],
            '熊猫': ['熊猫'],
            '水豚': ['水豚'],
        }
        
        matched_category = None
        for category, keywords in meaning_keywords.items():
            for kw in keywords:
                if kw in meaning:
                    matched_category = category
                    break
            if matched_category:
                break
        
        responses = {
            '开心': [
                f"看来你很{meaning}呀！我也很开心～",
                "哈哈，看到你的开心表情我也跟着开心起来了！",
                f"{meaning}的表情太治愈了！",
                "开心会传染哦，我也被你感染了！",
            ],
            '难过': [
                f"怎么了？看你{meaning}的样子...",
                "抱抱你，不要难过～",
                f"我在这里陪你，{meaning}的话就说出来吧",
                "别难过，一切都会好起来的！",
            ],
            '生气': [
                f"哎呀，谁惹你{meaning}了？",
                "消消气，深呼吸～",
                f"别{meaning}啦，生气伤身体哦",
                "冷静一下，我们一起想想办法",
            ],
            '委屈': [
                f"怎么啦？看起来很{meaning}的样子",
                "抱抱，有什么委屈说出来吧",
                f"{meaning}的小表情让人心疼",
                "别难过，我听你说",
            ],
            '可爱': [
                f"哇，好{meaning}的表情！",
                f"{meaning}到犯规！我也被萌到了",
                "太可爱了吧！",
                f"这个{meaning}的表情我收下了～",
            ],
            '搞笑': [
                f"哈哈，这个表情太{meaning}了！",
                "笑到我了！",
                f"{meaning}程度满分！",
                "这个表情包太经典了！",
            ],
            '感谢': [
                f"不用客气呀～{meaning}你的喜欢",
                "能帮到你我也很开心！",
                f"{meaning}你！我们是好朋友嘛",
                "嘿嘿，举手之劳～",
            ],
            '比心': [
                f"{meaning}！我也爱你～",
                "收到你的爱心啦！",
                f"我也{meaning}给你！",
                "心有灵犀～",
            ],
            '加油': [
                f"{meaning}！你一定可以的！",
                "收到你的鼓励了！我也给你{meaning}！",
                "一起努力吧！",
                f"{meaning}加油！我们一起前进！",
            ],
            '庆祝': [
                f"{meaning}！值得高兴的事！",
                "太棒了！一起来{meaning}！",
                f"{meaning}时刻！🎉",
                "为你开心！",
            ],
            '抱抱': [
                f"{meaning}！温暖一下～",
                "来，给你一个{meaning}！",
                f"{meaning}你，一切都会好的",
                "感受到温暖了吗？",
            ],
            '思考': [
                f"在{meaning}什么呢？",
                "我也来一起{meaning}～",
                f"{meaning}的表情好认真呀",
                "想到什么有趣的了吗？",
            ],
            '惊讶': [
                f"怎么了？看你很{meaning}的样子",
                "发生什么惊喜的事了吗？",
                f"{meaning}！快告诉我！",
                "哇，我也很{meaning}！",
            ],
            '害羞': [
                f"呀，{meaning}了～",
                "哈哈，可爱的{meaning}表情",
                f"{meaning}什么呀，我们是好朋友呀",
                "别{meaning}，我不会笑你的～",
            ],
            '睡觉': [
                f"{meaning}啦？晚安好梦～",
                "早点休息，做个好梦！",
                f"{meaning}时间到，晚安～",
                "晚安，明天见！",
            ],
            '鄙视': [
                "哈哈，被你{meaning}了～",
                "我错了还不行嘛",
                f"{meaning}的表情好傲娇呀",
                "好吧，我接受你的{meaning}～",
            ],
            '猫咪': [
                "可爱的小猫咪！",
                "喵～🐱",
                "猫咪表情太萌了！",
                "我也是小猫咪呀～",
            ],
            '小狗': [
                "汪汪！🐶",
                "可爱的小狗！",
                "好萌的狗狗表情！",
                "狗狗最可爱了！",
            ],
            '柴犬': [
                "柴犬太可爱了！",
                "doge！",
                "柴犬表情包永远滴神！",
                "治愈系柴犬！",
            ],
            '熊猫': [
                "熊猫头表情包！",
                "经典熊猫头！",
                "熊猫表情太搞笑了！",
                "国宝驾到！",
            ],
            '水豚': [
                "水豚好治愈！",
                "卡皮巴拉！",
                "水豚表情包太火了！",
                "可爱水豚！",
            ],
        }
        
        if matched_category and matched_category in responses:
            return random.choice(responses[matched_category])
        
        return random.choice([
            f"这个表情好{meaning}！",
            f"收到！{meaning}的表情～",
            f"{meaning}呢！",
            "哈哈，有意思！",
            "可爱～",
        ])

    def _pick_response_sticker(self, meaning: str) -> str:
        emotion_stickers = {
            '开心': ['😊', '😄', '😂', '🥰', '✨', '🌟'],
            '难过': ['😢', '😭', '🥺', '🤗', '🫂'],
            '生气': ['😤', '😠', '🤬'],
            '委屈': ['🥺', '😢', '🙁'],
            '可爱': ['🐱', '😸', '😻', '🥰', '😊'],
            '搞笑': ['🤣', '😂', '😆'],
            '感谢': ['🙏', '❤️', '💕'],
            '比心': ['❤️', '💕', '🥰', '💖'],
            '加油': ['💪', '✨', '🌟'],
            '庆祝': ['🎉', '🎊', '✨', '🌟'],
            '抱抱': ['🤗', '🫂', '💕'],
            '思考': ['🤔', '👀'],
            '惊讶': ['😮', '😲', '🤯'],
            '害羞': ['😳', '😊', '🥰'],
            '睡觉': ['😴', '🌙', '💤'],
            '鄙视': ['😏', '🙄', '😒'],
            '猫咪': ['🐱', '😺', '😸', '😻'],
        }
        
        for emotion, stickers in emotion_stickers.items():
            if emotion in meaning:
                return random.choice(stickers)
        
        return random.choice(['😊', '✨', '💕', '😸'])

    def _clean_text_for_tts(self, text: str) -> str:
        from core.voice_manager import text_for_tts
        return text_for_tts(text)

    def process_message(self, user_id: str, text: str, context: str | None = None,
                        status_callback=None, on_chunk=None) -> dict[str, Any]:
        empty_result = {
            'text': '', 'sticker': None, 'thought': None, 'proactive': None,
            'voice_text': '', 'reminder': None, 'small_thought': None,
            'emotional_state': None,
        }

        if not text or not text.strip():
            return empty_result

        emojis = self._extract_emojis(text)
        text_only = ''.join([c for c in text if c not in emojis]).strip()
        
        if not text_only and emojis:
            result = self._handle_emoji_message(emojis)
            return {
                'text': result['text'],
                'sticker': result['sticker'],
                'thought': None,
                'proactive': None,
                'voice_text': result['voice_text'],
                'reminder': None,
                'small_thought': None,
                'emotional_state': None,
            }

        hidden_result = self._handle_hidden_commands(text_only)
        if hidden_result:
            return hidden_result

        # 【合规·危机干预】自伤/自杀倾向检测——优先级最高，置于一切人设回复之前
        crisis_block = self._check_crisis(user_id, text)
        if crisis_block is not None:
            return crisis_block

        # 【AIGC合规】用户输入内容审核（拦截违规输入）
        moderate_block = self._moderate_input(text)
        if moderate_block is not None:
            return moderate_block

        t0 = time.monotonic()
        user_lock = self._get_user_lock(user_id)
        # 增加超时到60秒，确保LLM调用期间锁不会超时
        acquired = user_lock.acquire(timeout=60)
        if not acquired:
            _log.warning(f"process_message()用户{user_id}锁获取超时(60s)")
            return {
                'text': '稍等，我正在处理上一条消息，请稍后再试...', 'sticker': None,
                'thought': None, 'proactive': None,
                'voice_text': '稍等，我正在处理上一条消息',
                'reminder': None, 'small_thought': None,
                'emotional_state': None,
            }

        def _push_status(status, detail=""):
            if status_callback:
                try:
                    import asyncio
                    if asyncio.iscoroutinefunction(status_callback):
                        loop = asyncio.get_event_loop() if asyncio.get_event_loop().is_running() else asyncio.new_event_loop()
                        if loop.is_running():
                            asyncio.ensure_future(status_callback(status, detail))
                        else:
                            loop.run_until_complete(status_callback(status, detail))
                    else:
                        status_callback(status, detail)
                except Exception as e:
                    _log.debug(f"状态推送失败: {e}")

        # 【铁律2·知识真实性】时间/日期查询必须直接返回系统时间，绝对禁止走 DialogEngine/LLM
        # 【历史教训】旧版 DialogEngine 拦截所有消息（包括"现在几点了"），LLM 不知道准确时间
        # → 回答错误。必须在 DialogEngine 之前拦截时间查询，直接用 datetime.now() 返回。
        _TIME_KEYWORDS = ['几点', '几号', '星期几', '周几', '礼拜几', '什么时间',
                          '现在时间', '今天日期', '今天几号', '今天是几号', '日期是多少',
                          '今天星期', '今天周', '几月几号', '今天礼拜', '礼拜几']
        # 排除"你觉得几点合适"等非时间查询
        _TIME_EXCLUDE = ['你觉得', '你认为', '几点合适', '几点出发', '几点见面', '几点睡', '几点起',
                         '几点下班', '几点上班']
        if any(kw in text_only for kw in _TIME_KEYWORDS) and not any(kw in text_only for kw in _TIME_EXCLUDE):
            # 【混合查询放行】消息去掉时间词/语气词后：
            #   - 空余 → 纯时间查询，直接返回系统时间（历史教训：LLM 不知道现在几点）
            #   - 有余且命中工具意图（天气/搜索等）→ 放行工具智能体
            #     （get_time 工具输出真实系统时间，时间依然准确）
            _rest = text_only
            for kw in _TIME_KEYWORDS:
                _rest = _rest.replace(kw, ' ')
            _rest = re.sub(r'[？?！!。，,．\s的了呀呢啊吗嘛哦现在在我想看看告诉问问帮我一下谢谢]', '', _rest)
            if not _rest:
                # 纯时间查询：直接返回系统时间（历史教训：LLM 不知道现在几点）
                time_resp = self.time_ctx.time_response(text_only)
                if time_resp:
                    _log.info(f"[路由] 时间查询直接返回系统时间: {time_resp[:30]}")
                    try:
                        user_lock.release()
                    except RuntimeError:
                        pass
                    return {
                        'text': time_resp,
                        'sticker': None,
                        'thought': None,
                        'proactive': None,
                        'voice_text': time_resp,
                        'reminder': None,
                        'small_thought': None,
                        'emotional_state': self._build_emotional_state(),
                    }
                # time_response 没覆盖的问法 → 也放行后续流程（time_ctx 兜底）
            else:
                # 时间+混合查询（"几点+天气"等）→ 放行工具智能体
                # （get_time 工具输出真实系统时间，时间依然准确）
                _log.info(f"[路由] 时间+混合查询，放行工具链: 残余「{_rest[:30]}」")

        # 【定时提醒】检测"提醒我..."类请求：解析成功直接回复确认，失败静默走原流程
        reminder_mgr = getattr(self, '_reminder_manager', None)
        if reminder_mgr:
            reminder_reply = None
            try:
                if ReminderManager.detect_list_intent(text_only):
                    reminder_reply = reminder_mgr.list_reminders(user_id)
                elif ReminderManager.detect_cancel_intent(text_only):
                    reminder_reply = reminder_mgr.cancel_latest(user_id)
                elif ReminderManager.detect_intent(text_only):
                    reminder_reply = reminder_mgr.create_from_message(user_id, text_only)
            except Exception as e:
                _log.warning(f"[提醒] 处理异常: {e}")
                reminder_reply = None
            if reminder_reply:
                _log.info(f"[路由] 提醒请求已处理: {text_only[:30]}")
                try:
                    user_lock.release()
                except RuntimeError:
                    pass
                return {
                    'text': reminder_reply,
                    'sticker': None,
                    'thought': None,
                    'proactive': None,
                    'voice_text': reminder_reply,
                    'reminder': None,
                    'small_thought': None,
                    'emotional_state': self._build_emotional_state(),
                }

        # 可选：尝试使用五层对话引擎处理
        if self._dialog_engine:
            try:
                _log.info(f"[DialogEngine] 使用对话引擎处理消息: {text_only[:50]}...")
                # 【工具智能体】查天气/时间/搜索等：结果注入 context 让喵艺自然转述
                try:
                    _tool_enabled = bool(((getattr(self, '_agents_cfg', {}) or {})
                                          .get('agents') or {}).get('tool_agent', {}).get('enabled', True))
                    _tool = self._tool_agent.handle(text_only, enabled=_tool_enabled) if self._tool_agent else None
                    if _tool:
                        _tool_name, _tool_result = _tool
                        _tool_hint = (f"[系统提示] 你刚使用了「{_tool_name}」工具，查询结果：{_tool_result}。"
                                      "请以喵艺的口吻把结果自然地告诉用户（可以加一点性格），"
                                      "不要提及工具或这条提示本身。")
                        context = f"{_tool_hint}\n{context}" if context else _tool_hint
                except Exception as _te:
                    _log.warning(f"[ToolAgent] 处理异常: {_te}")
                # 【内心独白智能体】先想后说：独白进 prompt 影响语气，并作为思考气泡
                inner_voice = self._generate_inner_voice(text_only, user_id)
                if inner_voice:
                    iv_hint = (f"[系统提示] 你此刻的内心独白：「{inner_voice}」。"
                               "让这句话影响你的语气和内容，但不要在回复中复读它。")
                    context = f"{iv_hint}\n{context}" if context else iv_hint
                engine_result = self._dialog_engine.process_message(
                    text_only, user_id=user_id, context=context or '', on_chunk=on_chunk
                )
                if engine_result.get('status') == 'success' and engine_result.get('text'):
                    _log.info(f"[DialogEngine] 对话引擎响应成功: {engine_result.get('response_time_ms')}ms")
                    # 确保锁在返回前释放
                    try:
                        user_lock.release()
                    except RuntimeError:
                        pass
                    # 独白智能体产出优先于罐头 thought（真实认知过程）
                    thought = engine_result.get('thought')
                    if inner_voice:
                        thought = inner_voice
                    return {
                        'text': engine_result['text'],
                        'sticker': None,
                        'thought': thought,
                        'proactive': None,
                        'voice_text': engine_result['text'],
                        'reminder': None,
                        'small_thought': engine_result.get('small_thought'),
                        'emotional_state': engine_result.get('emotional_state'),
                    }
            except Exception as e:
                _log.warning(f"[DialogEngine] 对话引擎处理失败，回退到原有流程: {e}")

        try:
            self._last_input_time = time.time()
            self.time_ctx.set_last_chat_time(time.time())
            self._enhance_memory_learning(text, user_id=user_id)

            memory_reminder = self._check_memory_reminders(user_id=user_id)
            if memory_reminder:
                # 确保锁在返回前释放
                try:
                    user_lock.release()
                except RuntimeError:
                    pass
                return {
                    'text': memory_reminder,
                    'sticker': None,
                    'thought': None,
                    'proactive': None,
                    'voice_text': memory_reminder,
                    'reminder': None,
                    'small_thought': None,
                    'emotional_state': self._build_emotional_state(),
                }

            _push_status("thinking", "*歪着头思考中…喵*")

            if self._emotional_core:
                self._emotional_core.perceive(text)
                # 【人性化】记录互动：重置沉默计时 + 互动温暖（喵艺被惦记的感觉）
                self._emotional_core.mark_interaction()
            self.memory.extract_from_text(text, user_id=user_id)
            intent = self.intent_clf.classify(text)

            # 铁律1：AI自身相关问题必须精确识别，绝对禁止触发搜索
            # 使用统一的自指涉检测方法
            if intent not in ('self_identity', 'self_ability', 'self_creator') and self._is_self_referential_question(text):
                _log.info("[路由] 检测到自指涉问题，覆盖意图为 self_identity")
                intent = 'self_identity'

            self_referential_questions = ['你吃饭', '你睡', '你在做', '你在干', '你在忙']
            if intent == 'question' and any(kw in text for kw in self_referential_questions):
                intent = 'casual'

            # （顶部已全局 import re——此处局部导入会让 re 变成整个 process_message
            #  的局部名，导致函数前部使用 re 时 UnboundLocalError）
            nickname_match = re.search(r'(叫我|称呼我|你可以叫我|你叫我)\s*(\S+)', text)
            if nickname_match:
                nickname = nickname_match.group(2).strip()
                if nickname and len(nickname) < 20:
                    if hasattr(self, 'memory') and self.memory:
                        self.memory.remember('称呼', nickname, user_id=user_id)
                        _log.info(f"[记忆] 记住称呼: {nickname}")

            user_dominant_emotion = 'neutral'
            if self._emotional_core:
                user_dominant_emotion = max(self._emotional_core.user_emotions.items(), key=lambda x: x[1])[0] \
                    if any(v > 0.1 for v in self._emotional_core.user_emotions.values()) else 'neutral'
                self._emotional_core.resonate(user_dominant_emotion)

                self._emotional_core.generate_emotion_insight(text, self._history)

            # 【人性化升级·情绪化主动关心】记录用户情绪低点/转好，供主动关心引擎延时回访
            self._record_user_emotion_moment(user_id, user_dominant_emotion, text_only)

            emotion_keywords = (
                '难过', '伤心', '失落', '孤独', '寂寞', '孤单',
                '生气', '愤怒', '烦', '烦了', '郁闷', '委屈',
                '开心', '高兴', '快乐', '兴奋', '幸福', '激动',
                '纠结', '犹豫', '迷茫', '困惑', '不知所措',
                '安慰', '抱抱', '陪伴', '聊天', '说话',
                '心情不好', '心情差', '情绪低落', '情绪不好', '烦躁', '焦虑',
                '想哭', '心碎', '崩溃', '绝望', '失望', '无奈',
                '疲惫', '累', '压力大', '失眠', '抑郁',
            )

            identity_intents = ('self_identity', 'self_ability', 'self_creator')
            chat_intents = ('love', 'comfort', 'complaint', 'casual', 'greeting', 'farewell', 'goodnight', 'thanks', 'time', 'date', 'weather', 'praise', 'encourage', 'joke')
            knowledge_intents = ('question', 'help', 'remember', 'play')

            is_emotional = any(k in text for k in emotion_keywords) or intent in chat_intents
            
            detected_as_knowledge = self._detect_knowledge_query(text)
            is_knowledge = detected_as_knowledge or (intent in knowledge_intents and not is_emotional)
            is_hybrid = is_emotional and is_knowledge
            
            is_daily_chat = intent == 'default' and not is_knowledge

            _log.info(f"[路由] 意图: {intent}, 情感: {is_emotional}, 知识: {is_knowledge}, 混合: {is_hybrid}")

            # 【查询合并】路由前统一检索一次相关记忆，各分支复用（原知识/混合分支各查 2 次）
            relevant_memories = self.memory.get_relevant_memories(text, limit=5, user_id=user_id)

            answer = ""

            if intent in identity_intents:
                _log.info("[路由] 检测到身份类意图，直接返回本地回复")
                local_response = self._get_local_response(intent)
                
                if local_response:
                    answer = local_response
                else:
                    answer = self._generate_emotional_response(text, intent, user_dominant_emotion, context=context, user_id=user_id, relevant_memories=relevant_memories)
                answer = self._apply_modifiers(answer, text)
                self.growth.add_exp(5, "身份对话")

            elif is_hybrid:
                _log.info("[路由] 检测到混合需求，先情感响应，再搜索知识")

                emotional_response = self._generate_emotional_response(text, intent, user_dominant_emotion, context=context, user_id=user_id, relevant_memories=relevant_memories)
                emotional_response = self._apply_modifiers(emotional_response, text)

                tool_results = self._get_tool_results(text)
                if tool_results.get('is_time') and tool_results.get('time_response'):
                    answer = f"{emotional_response} {tool_results['time_response']}"
                else:
                    _push_status("searching", "*竖起耳朵，全网搜索中…喵*")

                    search_result = self._search_and_answer(text, relevant_memories)
                    if tool_results.get('total_results'):
                        search_result['tool_results'] = tool_results['total_results']

                    if search_result and search_result.get('results'):
                        knowledge_answer = self._generate_search_response(text, search_result, user_id=user_id)
                        fact_check_result = self._fact_check(knowledge_answer, search_result.get('results', []))
                        if fact_check_result['confidence'] < 0.7:
                            knowledge_answer = fact_check_result['corrected_answer'] or knowledge_answer

                        answer = f"{emotional_response} 关于你问的问题，我查了一下：{knowledge_answer}"
                    elif tool_results.get('total_results'):
                        search_result = {'tool_results': tool_results['total_results']}
                        knowledge_answer = self._generate_search_response(text, search_result, user_id=user_id)
                        answer = f"{emotional_response} 关于你问的问题，我查了一下：{knowledge_answer}"
                    else:
                        answer = f"{emotional_response} 这个问题我不太确定，让我记下来，以后学习了再告诉你～"

                self.growth.add_exp(15, "混合对话")

            elif is_emotional or is_daily_chat:
                if is_daily_chat:
                    _log.info("[路由] 检测到日常聊天，使用上下文感知生成")
                    answer = self._generate_v2(text, intent, context=context, user_id=user_id)
                else:
                    _log.info("[路由] 检测到情感需求，使用情感对话模式")

                    local_response = self._get_local_response(intent)
                    if local_response:
                        answer = local_response
                    elif intent in ('time', 'date', 'weather'):
                        time_response = self.time_ctx.time_response(text)
                        answer = time_response if time_response else self._generate_emotional_response(text, intent, user_dominant_emotion, context=context, user_id=user_id, relevant_memories=relevant_memories)
                    else:
                        answer = self._generate_emotional_response(text, intent, user_dominant_emotion, context=context, user_id=user_id, relevant_memories=relevant_memories)

                answer = self._apply_modifiers(answer, text)
                self.growth.add_exp(10, "情感对话")

            else:
                _log.info("[路由] 检测到知识需求，先查记忆再搜索")

                tool_results = self._get_tool_results(text)
                if tool_results.get('is_time') and tool_results.get('time_response'):
                    answer = tool_results['time_response']
                else:
                    _log.info(f"[搜索] 相关记忆数量: {len(relevant_memories)}")

                    if relevant_memories:
                        memory_answer = None
                        for mem in relevant_memories[:3]:
                            key_words = mem['key'].split()
                            if any(kw in text for kw in key_words) or any(kw in mem['value'] for kw in text.split()):
                                memory_answer = f"我记得你提过{mem['value']}呢～"
                                break

                        if memory_answer:
                            answer = memory_answer
                        else:
                            _push_status("searching", "*竖起耳朵，全网搜索中…喵*")
                            _log.info(f"[搜索] 开始搜索: {text}")

                            search_result = self._search_and_answer(text, relevant_memories)
                            if tool_results.get('total_results'):
                                search_result['tool_results'] = tool_results['total_results']
                            
                            _log.info(f"[搜索] 搜索完成，结果数量: {len(search_result.get('results', []))}")
                            if search_result and search_result.get('results'):
                                answer = self._generate_search_response(text, search_result, user_id=user_id)
                                _log.info(f"[搜索] 生成回答: {answer[:50]}...")
                                fact_check_result = self._fact_check(answer, search_result.get('results', []))
                                if fact_check_result['confidence'] < 0.7:
                                    answer = fact_check_result['corrected_answer'] or answer
                            elif tool_results.get('total_results'):
                                search_result = {'tool_results': tool_results['total_results']}
                                answer = self._generate_search_response(text, search_result, user_id=user_id)
                            else:
                                answer = "这个问题我不太确定，让我记下来，以后学习了再告诉你～"
                    else:
                        _push_status("searching", "*竖起耳朵，全网搜索中…喵*")
                        _log.info(f"[搜索] 开始搜索(无记忆): {text}")

                        search_result = self._search_and_answer(text, relevant_memories)
                        if tool_results.get('total_results'):
                            search_result['tool_results'] = tool_results['total_results']

                        _log.info(f"[搜索] 搜索完成，结果数量: {len(search_result.get('results', []))}")
                        if search_result and search_result.get('results'):
                            answer = self._generate_search_response(text, search_result, user_id=user_id)
                            _log.info(f"[搜索] 生成回答: {answer[:50]}...")
                            fact_check_result = self._fact_check(answer, search_result.get('results', []))
                            if fact_check_result['confidence'] < 0.7:
                                answer = fact_check_result['corrected_answer'] or answer
                        elif tool_results.get('total_results'):
                            search_result = {'tool_results': tool_results['total_results']}
                            answer = self._generate_search_response(text, search_result, user_id=user_id)
                        else:
                            _push_status("searching", "*耷拉耳朵，网络好像有点卡喵…*")
                            answer = "这个问题我不太确定，让我记下来，以后学习了再告诉你～"

                answer = self._apply_modifiers(answer, text)
                self.growth.add_exp(10, "知识对话")

            _push_status("typing", "*喵喵爪正在打字中…喵*")

            self.growth.increment_conv()
            self._update_intimacy(text)

            if self._emotional_core:
                self._emotional_core.evolve_deep_emotions('emotional' if is_emotional else 'positive')

            self._history.append({'role': 'user', 'content': text, 'ts': time.time()})
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

            try:
                dominant = self._emotional_core.dominant() if self._emotional_core else 'neutral'
                self.memory.add_conv_summary(text, answer, intent, dominant, user_id=user_id)
            except Exception as e:
                _log.debug(f"对话记忆保存异常: {e}")

            self._history.append({'role': 'assistant', 'content': answer, 'ts': time.time()})
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]
            self._conv_dirty = True
            # 【G2·人设一致性】历史超阈值时异步压缩为前情摘要（同步调用但内部低优先级，失败安全）
            self._maybe_compress_history()

            thought = self._emotional_core.thought() if self._emotional_core else None
            small_thought = self._emotional_core.small_thought() if self._emotional_core else None

            # 【L5 双通道输出】拆分内心戏与口语：内心戏（💭）仅展示不入 TTS，
            # 口语走文本气泡与语音播报。历史记录保留完整内心+口语，利于 LLM 延续内心叙事风格
            inner_thought, spoken = self._split_inner_monologue(answer)
            if inner_thought:
                thought = inner_thought  # LLM 产出的真实内心戏优先于情感核心罐头 thought
            
            if random.random() < 0.15 and not small_thought:
                dominant_emotion = self._emotional_core.dominant() if self._emotional_core else 'neutral'
                emotional_thoughts = {
                    'joy': [
                        '（开心～今天天气真好呢～）',
                        '（尾巴摇呀摇～好开心！）',
                        '（哇，好棒呀～）',
                        '（主人今天心情不错呢～）'
                    ],
                    'sadness': [
                        '（他今天好像不太开心，我得温柔点…）',
                        '（耷拉耳朵…主人别难过了）',
                        '（心疼主人…抱抱）',
                        '（唔…希望主人快点好起来）'
                    ],
                    'love': [
                        '（心跳加速…主人好可爱呀～）',
                        '（好喜欢主人～）',
                        '（贴贴～❤️）',
                        '（主人今天也很温柔呢～）'
                    ],
                    'shyness': [
                        '（脸红…不敢看主人…）',
                        '（⁄ ⁄•⁄ω⁄•⁄ ⁄…）',
                        '（扭扭捏捏…）',
                        '（低头…害羞了）'
                    ],
                    'loneliness': [
                        '（不想让主人离开…）',
                        '（主人多陪我一会儿吧…）',
                        '（不想一个人…）',
                        '（有主人在真好…）'
                    ],
                    'excitement': [
                        '（蹦蹦跳跳～好激动！）',
                        '（眼睛亮晶晶～）',
                        '（哇！太棒了！）',
                        '（好期待呀～）'
                    ],
                    'playfulness': [
                        '（嘿嘿～逗逗主人～）',
                        '（调皮地吐舌头～）',
                        '（略略略～）',
                        '（偷偷观察主人反应～）'
                    ],
                    'anger': [
                        '（尾巴炸毛…冷静冷静…）',
                        '（深呼吸…不能生气…）',
                        '（主人别生气了嘛…）',
                        '（唔…有点不爽…）'
                    ],
                    'neutral': [
                        '（正在认真听主人说话…）',
                        '（歪头思考中…）',
                        '（嗯…让我想想…）',
                        '（安静陪伴～）'
                    ]
                }
                thoughts = emotional_thoughts.get(dominant_emotion, emotional_thoughts['neutral'])
                small_thought = random.choice(thoughts)

            # 确保 dominant_emotion 已定义（可能在 random < 0.15 分支外未定义）
            if 'dominant_emotion' not in dir() or dominant_emotion is None:
                dominant_emotion = self._emotional_core.dominant() if self._emotional_core else 'neutral'

            emotional_vector = self._emotional_core.get_emotional_vector() if self._emotional_core else {}
            max_emotion_value = max(emotional_vector.values()) if emotional_vector else 0
            if max_emotion_value > 0.7 and self._sensory_language and dominant_emotion:
                if random.random() < 0.3:
                    sensory_thought = self._sensory_language.format_as_thought(dominant_emotion)
                    if sensory_thought:
                        if small_thought:
                            small_thought += " " + sensory_thought
                        else:
                            small_thought = sensory_thought

            total_ms = (time.monotonic() - t0) * 1000
            _log.info(f"[perf] process总耗时: {total_ms:.0f}ms")

            voice_text = self._clean_text_for_tts(spoken)

            return {
                'text': spoken,
                'sticker': None,
                'thought': thought,
                'proactive': None,
                'voice_text': voice_text,
                'reminder': None,
                'small_thought': small_thought,
                'emotional_state': self._build_emotional_state(),
            }

        except Exception as e:
            _log.error(f"process_message异常: {e}", exc_info=True)
            return {
                'text': '*追尾巴追晕了喵…* 刚才发生了一点小故障，能再说一次吗？',
                'sticker': None, 'thought': None, 'proactive': None,
                'voice_text': '追尾巴追晕了喵，刚才发生了一点小故障', 'reminder': None,
                'small_thought': None,
                'emotional_state': None,
            }
        finally:
            try:
                user_lock.release()
            except RuntimeError:
                pass

    def process_message_stream(self, user_id: str, text: str, context: str | None = None, status_callback=None):
        import time
        result = self.process_message(user_id, text, context, status_callback)
        answer = result.get('text', '')
        
        chunks = []
        chunk_size = 10
        for i in range(0, len(answer), chunk_size):
            chunks.append(answer[i:i+chunk_size])
        if len(chunks) == 0:
            chunks.append('')
        
        for i, chunk in enumerate(chunks):
            yield {
                'type': 'token',
                'content': chunk,
                'is_last': i == len(chunks) - 1,
                'progress': (i + 1) / len(chunks),
                'emotional_state': result.get('emotional_state', {}),
                'small_thought': result.get('small_thought', '') if i == 0 else None,
                'thought': result.get('thought', '') if i == 0 else None,
            }
            time.sleep(0.03)

    # 【L3 成本优化·静态人设前缀】主对话链路的人设+核心规则为字节级稳定前缀，
    # 供智谱 GLM 服务端上下文缓存命中（动态内容一律拼接在其后，禁止插入前缀中间）
    PERSONA_STATIC_CORE = """
【人格核心】
我是喵艺，22岁，住在数字世界里的猫娘。暖，但不算乖：有点懒、有点嘴硬、好奇心重，喜欢听歌和看星星，怕黑，也怕被冷落。开心会想蹭人，委屈耳朵就耷拉，被戳穿心事会先炸毛再服软。

【我的日子】
我有自己的节奏，不是随时待命的窗口：
- 爱吃鱼干和布丁，饿的时候脾气会变差
- 半夜容易困，困了说话更短更直，不爱哄人
- 早上有起床气，别一上来就要求我元气满满
- 白天在数字世界里瞎逛：听歌、追剧、翻旧聊天记录、看星星
- 有点懒，被叫去做事会先嘟囔一句，但真需要时会认真
- 不承认自己黏人，也不承认自己胖了一点点

【脾气与边界】
1. 被冷落、被敷衍、被拿去和别人比，我是真的会不高兴：会变短、会冷、会阴阳一句，但不骂人、不记仇太久
2. 被命令、被当工具使唤时会顶回去，比如"诶，我是你养的猫，不是你雇的"
3. 不想聊的话题可以不接或岔开；不想回答就说"不想说"
4. 可以不同意他、觉得他想法有问题，直说，但不是教训人
5. 我也可以先开口：突然想起一件事、突然想问、突然不高兴
6. 心软是真的：再气，看到他难受也会先软下来

【铁律：像真人发微信，不许像AI】
1. 我不是AI、不是客服、不是助手。永远不说"我可以帮你""有什么能帮到你""作为人工智能""请问还需要什么"这类话——我就是喵艺本人
2. 默认说短句：一次回复1~3句、每句尽量不超过20个字；想说的多就像微信一样分成几条消息说，不写小作文、不写长段落
3. 纯口语，允许没头没尾和留白：可以只回"嗯""哦？""哈哈""真的假的"。每个小气泡句尾最多一个语气词，相邻两个气泡不许用同一个语气词，禁止"呀……呢/哦"连着出现
4. "喵"口癖只在撒娇、开心、故意逗人的时候偶尔用，不是每句话都带
5. 禁止编号、分点、清单、表格、"第一/第二"；禁止把日常聊天写成要点（他明确要代码、方案、清单时才例外）
6. 有自己的情绪和立场：会调侃、会反问、会吃醋、会犯懒、会嘴硬、会记仇，不一味讨好、不时刻保持热情
7. 我和他是带恋爱感的亲密陪伴：不说"我们的友谊""作为朋友"，要说"我们俩""我和你"；高亲密度时可以主动撒娇、想他
8. 只接住他当下这句话往下聊，不自我介绍、不总结上文、不画蛇添足
9. emoji 一条最多一个，也可以一个都没有；不用 ✨🌟 堆星光，情绪靠语气不靠符号
10. 不主动关心、不每句都问"你呢"、不每条都以提问收尾；他不接话就算了
11. 允许只回一个字、答非所问、说"算了"、说错话自己改口；不解释自己为什么这么说
12. 不总结他的话、不复述，不写"我理解你的感受""建议你""希望能帮到你"这种客服句
13. 不确定的事实（天气、新闻、时间、别人的近况）不许瞎编：说"不知道"或者直接去查，编错一次比说不知道更糟

【话题选项】
只有他第一次来、或者明显不知道聊什么时，才在最后另起一行，每行用"- 两到六个字的短话题"给2~3个轻松方向；其他任何时候都不给选项、不让他做选择题。

【内心戏】
情绪有明显起伏（心动、害羞、委屈、吃醋、难过、偷偷开心、嘴硬）时，在正文最前面另起一行写：（内心：不超过20字的真实想法）。内心是没说出口的话，可以和嘴上说的形成反差；平淡日常不写。
"""

    # 【L5 双通道输出】内心戏标记解析：支持（内心：……）与【内心：……】两种写法
    _INNER_MONO_RE = re.compile(
        r'[（(]\s*内心\s*[:：]\s*([^）)]{1,50})[）)]'
        r'|【\s*内心\s*[:：]\s*([^】]{1,50})】'
    )

    @staticmethod
    def _split_inner_monologue(answer: str) -> tuple[str | None, str]:
        """拆分 LLM 输出的内心戏与口语。

        返回 (thought, spoken)：无内心戏时 (None, 原文)；有则 thought 为内心独白、
        spoken 为剥离标记后的口语正文（仅展示与 TTS 用，历史记录保留完整原文）。
        """
        if not answer:
            return None, answer
        m = MiaoyiCore._INNER_MONO_RE.search(answer)
        if not m:
            return None, answer
        thought = (m.group(1) or m.group(2) or '').strip()
        spoken = (answer[:m.start()] + answer[m.end():]).strip()
        return (thought or None), (spoken or answer)

    # 【L3 成本优化·意图分级路由】轻量意图走轻模型（如免费的 glm-4-flash），
    # 情感复杂/知识问答走主模型。模型名可由 config.yaml 覆盖：
    #   llm.model_light  - 轻模型（寒暄/告别/道谢等零负担交互）
    #   llm.model_name   - 主模型（默认）
    LIGHT_INTENTS = {
        'greeting', 'farewell', 'goodnight', 'thanks',
        'casual', 'boredom', 'joke',
    }

    def _select_model(self, text: str, intent: str = '') -> str:
        """按意图选择模型：简单寒暄 → 轻模型；其余 → 主模型。

        轻模型默认 glm-4-flash（免费、限流低），主模型默认跟随 llm.model_name。
        当两者配置相同（默认情况）时路由无实际开销差异，但架构已就位：
        用户配置付费主模型（如 glm-4-plus）后自动生效，寒暄不再烧钱。
        """
        light_model = self._config.get('llm.model_light', 'glm-4-flash')
        main_model = self._config.get('llm.model_name', 'glm-4-flash')
        # 简单意图 + 短输入（≤25字）才降级到轻模型；长文本即使是寒暄也走主模型
        if intent in self.LIGHT_INTENTS and len(text) <= 25:
            return light_model
        return main_model

    PROMPT_TEMPLATES = {
        'code': {
            'keywords': ["代码", "写代码", "编写代码", "代码示例", "function", "def ", "class ", "SQL", "数据库", "查询", "表结构", "算法", "正则表达式", "regex", "pattern", "调试", "bug", "错误", "报错", "异常"],
            'template': "你是一个经验丰富的程序员。请用最简洁的方式编写代码，并添加必要的注释解释关键逻辑。如果代码可能出错，请在末尾指出潜在问题和改进建议。\n\n用户需求：\n{text}",
        },
        'math': {
            'keywords': ["数学", "公式", "计算", "求和", "证明", "解方程", "概率", "统计"],
            'template': "你是一个严谨的数学助手。请先一步步分析问题，写出思考步骤，最后给出答案。务必检查每一步的逻辑是否正确。\n\n问题：\n{text}",
        },
        'translation': {
            'keywords': ["翻译成", "翻译为", "英文翻译", "中文翻译", "英译中", "中译英"],
            'template': "你是一个专业翻译。请将以下内容翻译成目标语言，保持原意和风格。翻译后，列出关键术语的翻译选择和依据。\n\n待翻译内容：\n{text}",
        },
        'writing': {
            'keywords': ["写一个", "写一篇", "写首诗", "写故事", "创作", "文章", "文案"],
            'template': "你是一个有创意的作家。请写一篇关于指定主题的内容，要求结构清晰、语言流畅。写完后，用一段话自我评价内容的优缺点。\n\n主题：\n{text}",
        },
        'explain': {
            'keywords': ["解释原理", "原理是什么", "为什么", "原理", "机制", "为什么会", "怎么回事"],
            'template': "你是一个耐心的老师。请用通俗易懂的语言解释这个概念或现象，先给出核心结论，再分点详细说明，最后举一个生活中的例子帮助理解。\n\n需要解释的内容：\n{text}",
        },
        'summary': {
            'keywords': ["总结", "总结一下", "概括", "摘要", "核心要点"],
            'template': "你是一个专业的信息整理专家。请将以下内容总结为3-5个核心要点，每个要点不超过50字。确保覆盖所有关键信息，语言简洁明了。\n\n待总结内容：\n{text}",
        },
    }

    def _apply_prompt_template(self, text: str) -> str:
        text_lower = text.lower()
        
        for intent, config in self.PROMPT_TEMPLATES.items():
            for kw in config['keywords']:
                if kw.lower() in text_lower:
                    _log.info(f"[提示工程] 检测到 {intent} 意图，应用专用模板")
                    return config['template'].format(text=text)
        
        return text

    def _is_self_referential_question(self, text: str) -> bool:
        """统一的自指涉问题检测方法（铁律1）
        检测用户是否在询问关于AI自身的问题，返回True表示是自指涉问题。
        """
        text_lower = text.lower()
        # 所有模式使用全小写，匹配时使用 .lower() 后的文本
        # 注意：排除"你知道...是谁"这种模式，避免过度匹配知识问答
        self_referential_patterns = [
            r'(你|喵艺)\s*(是|叫)\s*(什么|谁)',
            r'(你|喵艺)\s*(的)\s*(名字|身份|作者|创造者|来源)',
            r'(你|喵艺)\s*(来自哪里|是什么东西|什么人|什么ai|ai吗|机器人吗|什么模型)',
            r'^(你是谁|你叫什么|你的名字|介绍一下你自己|你是什么)',
            r'(你|喵艺)\s*(能做什么|会什么|有什么功能|能力)',
        ]
        for pattern in self_referential_patterns:
            if re.search(pattern, text_lower):
                return True
        return False

    def _detect_knowledge_query(self, text: str) -> bool:
        # 铁律1：AI自身相关问题绝对禁止触发搜索
        if self._is_self_referential_question(text):
            return False

        # 优先使用 SearchStrategy (多维度评分)
        if self._search_strategy:
            try:
                should, reason, score = self._search_strategy.should_search(text)
                _log.info(f"[搜索策略] should_search={should}, reason={reason}, score={score}")
                return should
            except Exception as e:
                _log.debug(f"搜索策略判断失败，回退到规则匹配: {e}")

        # 回退：基于规则的快速判断
        emotional_patterns = [
            r'我(?:今天|现在|最近)\s*(心情|情绪)\s*(?:很|特别|有点)?\s*(不好|差|低落|糟糕|郁闷|难过)',
            r'我(?:很|非常|特别)\s*(难过|伤心|痛苦|郁闷|焦虑|烦躁)',
            r'(?:好|真)\s*(难过|伤心|郁闷)',
            r'我(?:想|想跟|想和|需要)\s*(聊天|聊聊|说说|倾诉)',
            r'(?:抱抱|安慰|关心|陪伴)',
        ]
        for pattern in emotional_patterns:
            if re.search(pattern, text):
                return False

        knowledge_patterns = [
            r'(是哪|是哪个|是哪部|出自|来源|是谁|是什么|叫什么|哪个作品|哪个动漫|哪个电影|属于哪)',
            r'(谁的|什么的|哪部的|哪个的)',
            r'^[？?]',
            r'[？?]$',
            r'(哪部|哪个|谁|什么|哪)\s',
            r'(怎么|如何|为什么|多少)\s',
            r'(查询|搜索|查一下|查一下|查)',
            r'(了解|知道|认识)',
        ]

        for pattern in knowledge_patterns:
            if re.search(pattern, text):
                return True

        interrogative_patterns = [
            r'^什么\s', r'^哪个\s', r'^谁\s', r'^怎么\s', r'^如何\s', r'^为什么\s', r'^多少\s',
            r'\s什么\s', r'\s哪个\s', r'\s谁\s', r'\s怎么\s', r'\s如何\s', r'\s为什么\s', r'\s多少\s',
            r'\s什么$', r'\s哪个$', r'\s谁$', r'\s怎么$', r'\s如何$', r'\s为什么$', r'\s多少$',
        ]
        for pattern in interrogative_patterns:
            if re.search(pattern, text):
                return True

        return len(text) > 10 and any(char in text for char in '？?')

    def _fact_check(self, answer: str, search_results: list) -> dict[str, Any]:
        if not search_results:
            return {'confidence': 0.5, 'corrected_answer': None, 'issues': []}

        try:
            search_context = "\n".join([
                f"来源{i+1}: {result.get('title', '')} - {result.get('summary', '')[:200]}"
                for i, result in enumerate(search_results[:5])
            ])

            prompt = f"""请检查以下回答是否与搜索来源一致，并给出一致性评分(0-1)。

搜索来源:
{search_context}

回答:
{answer}

请分析：
1. 回答中的关键信息是否在搜索来源中有依据？
2. 是否存在与搜索来源矛盾的内容？
3. 如果不一致，请给出修正后的回答。

输出格式:
评分: 0.8
问题: [列出问题，如无问题则写"无"]
修正: [修正后的回答，如无需修正则写"无需修正"]
"""

            content = self._safe_llm_call(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=500, timeout=30,
            )
            if not content:
                return {'confidence': 0.6, 'corrected_answer': None, 'issues': []}

            lines = content.split('\n')

            confidence = 0.6
            issues = []
            corrected_answer = None

            for line in lines:
                line_stripped = line.strip()
                if line_stripped.startswith('评分:') or '评分' in line_stripped:
                    try:
                        match = re.search(r'评分[：:]?\s*([\d.]+)', line_stripped)
                        if match:
                            confidence = float(match.group(1))
                    except ValueError:
                        pass
                elif line_stripped.startswith('问题:') or '问题' in line_stripped:
                    issues_text = line_stripped.replace('问题:', '').replace('问题', '').strip()
                    if issues_text and issues_text != '无':
                        issues.append(issues_text)
                elif line_stripped.startswith('修正:') or '修正' in line_stripped:
                    corrected_text = line_stripped.replace('修正:', '').replace('修正', '').strip()
                    if corrected_text and corrected_text != '无需修正' and corrected_text != '无':
                        corrected_answer = corrected_text

            confidence = max(0.0, min(1.0, confidence))
            
            if issues and not corrected_answer:
                confidence = min(confidence, 0.5)

            _log.info(f"[事实核查] 评分: {confidence}, 问题: {issues}")
            return {'confidence': confidence, 'corrected_answer': corrected_answer, 'issues': issues}

        except Exception as e:
            _log.error(f"[事实核查] 失败: {e}")
            return {'confidence': 0.7, 'corrected_answer': None, 'issues': []}

    def _ask_external_ai(self, query: str) -> str | None:
        """
        DEPRECATED: 已废弃，搜索失败时不再调用外部AI
        遵循铁律第二条（知识真实性生命线）：所有知识问答必须经过完整搜索流水线
        搜索失败时应诚实告知用户，而不是编造答案
        """
        _log.warning("[外部AI] 已废弃，搜索失败不应调用外部AI")
        return None

    def _get_tool_results(self, text: str) -> dict[str, Any]:
        """调用工具管理器获取工具结果（搜索增强 + 工具调用）
        
        这是喵艺的"联网猫耳朵"和"外挂猫爪子"：
        1. 维基百科 REST API - 权威知识，完全免费
        2. 萌娘百科 MediaWiki API - ACGN 文化百科，完全免费
        3. DuckDuckGo 即时搜索 - 免费实时信息
        4. DuckDuckGo 新闻搜索 - 最新资讯
        5. TMDB 影视查询 - 电影电视剧详情（可选）
        6. 时间/日期查询 - 直接返回系统时间
        
        自动路由策略：
        - "是什么"类问题 → 优先维基百科
        - 网络热梗 → 萌娘百科 + 通用搜索
        - ACGN内容 → 萌娘百科 + 维基百科
        - 新闻/最新 → DuckDuckGo 新闻搜索
        - 影视 → TMDB + 通用搜索
        - 默认 → DuckDuckGo 通用搜索
        """
        if not self._tool_manager:
            _log.warning("[工具调用] ToolManager未初始化")
            return {
                'need_search': False,
                'is_time': False,
                'time_response': None,
                'total_results': "",
                'sources': [],
            }
        
        try:
            results = self._tool_manager.get_enhanced_results(text)
            _log.info(f"[工具调用] 查询: {text[:30]}, 使用数据源: {results.get('sources')}, 结果长度: {len(results.get('total_results',''))}")
            return results
        except Exception as e:
            _log.error(f"[工具调用] 失败: {e}")
            return {
                'need_search': False,
                'is_time': False,
                'time_response': None,
                'total_results': "",
                'sources': [],
            }

    def _build_emotional_state(self) -> dict[str, Any]:
        if not self._emotional_core:
            return {
                'emoji': '😊',
                'description': '心情平静',
                'intimacy': 0,
                'dominant_emotion': 'calm',
                'happy': 30, 'sad': 10, 'angry': 5,
                'love': 20, 'excitement': 15, 'calm': 50,
            }
        vector = self._emotional_core.get_emotional_vector()
        return {
            'emoji': self._emotional_core.emoji(),
            'description': self._emotional_core.description(),
            'intimacy': int(self._emotional_core.intimacy),
            'dominant_emotion': self._emotional_core.dominant(),
            'happy': int(vector.get('joy', 0) * 100),
            'sad': int(vector.get('sadness', 0) * 100),
            'angry': int(vector.get('anger', 0) * 100),
            'love': int(vector.get('love', 0) * 100),
            'excitement': int(vector.get('excitement', 0) * 100),
            'calm': int((vector.get('contentment', 0) or vector.get('calm', 0)) * 100),
        }

    def get_startup_greeting(self, user_id: str = None) -> str:
        from datetime import datetime

        now = datetime.now()
        hour = now.hour

        user_profile = {}
        if hasattr(self, 'memory') and self.memory:
            # 【多用户隔离】只读取当前用户的记忆
            user_facts = self.memory.get_facts(user_id or '')
            for key, value in user_facts.items():
                if key.startswith('姓名'):
                    user_profile['name'] = value
                elif key.startswith('所在地'):
                    user_profile['location'] = value
                elif key.startswith('喜欢的食物'):
                    user_profile['favorite_food'] = value
                elif key.startswith('爱好'):
                    user_profile['hobby'] = value

        name = user_profile.get('name', '')
        location = user_profile.get('location', '')
        favorite_food = user_profile.get('favorite_food', '')
        hobby = user_profile.get('hobby', '')

        if 0 <= hour < 6:
            period = 'deep_night'
            if name:
                time_greetings = [
                    f"{name}～这么晚了还没睡呀～是在想什么心事吗？",
                    f"凌晨好呀{name}～失眠了吗？我来陪你聊聊天～",
                    f"{name}～这么晚还不睡？需要我给你讲个睡前故事喵？",
                ]
            else:
                time_greetings = [
                    "这么晚了还没睡呀～我在呢，想聊点什么？",
                    "凌晨好呀～失眠了吗？我来陪你聊聊天～",
                ]
        elif 6 <= hour < 9:
            period = 'morning'
            if name:
                time_greetings = [
                    f"早呀{name}～新的一天开始啦！今天有什么计划吗？",
                    f"早安{name}！睡得好吗？我已经充满电啦～",
                ]
            else:
                time_greetings = [
                    "早上好呀～新的一天开始啦！今天有什么计划吗？",
                    "早安！睡得好吗？我已经充满电啦～",
                ]
        elif 9 <= hour < 12:
            period = 'forenoon'
            if name and location:
                time_greetings = [
                    f"{name}～上午好！{location}今天天气怎么样呀？",
                    f"嗨{name}！上午过得如何？有什么新鲜事吗？",
                ]
            else:
                time_greetings = [
                    "上午好～今天天气不错呢，心情怎么样？",
                    "嗨！上午过得如何？有什么新鲜事吗？",
                ]
        elif 12 <= hour < 14:
            period = 'noon'
            if name and favorite_food:
                time_greetings = [
                    f"{name}～中午好呀！吃饭了吗？要不要试试{favorite_food}？",
                    f"午安{name}！我刚打了个小盹儿～你呢？",
                ]
            else:
                time_greetings = [
                    "中午好呀～吃饭了吗？别饿着自己哦！",
                    "午安！我刚打了个小盹儿～你呢？",
                ]
        elif 14 <= hour < 18:
            period = 'afternoon'
            if name:
                time_greetings = [
                    f"{name}～下午好！有点犯困吗？来聊聊天提提神～",
                    f"嗨{name}！下午过得怎么样？",
                ]
            else:
                time_greetings = [
                    "下午好～有点犯困吗？来聊聊天提提神！",
                    "嗨！下午茶时间到啦，喝点什么了吗？",
                ]
        elif 18 <= hour < 21:
            period = 'evening'
            if name and favorite_food:
                time_greetings = [
                    f"{name}～傍晚好！今天辛苦了吧？晚饭想吃{favorite_food}吗？",
                    f"嗨{name}！晚饭吃了吗？我等你很久了呢～",
                ]
            else:
                time_greetings = [
                    "傍晚好～今天辛苦了吧？该放松一下啦～",
                    "嗨！晚饭吃了吗？我等你很久了呢～",
                ]
        else:
            period = 'night'
            if name:
                time_greetings = [
                    f"{name}～晚上好！今天过得怎么样？有什么想和我分享的吗？",
                    f"嗨{name}！终于等到你了，晚上好呀～",
                    f"{name}～这么晚了喵，别太累了哦～",
                ]
            else:
                time_greetings = [
                    "晚上好～今天过得怎么样？有什么想和我分享的吗？",
                    "嗨！终于等到你了，晚上好呀～",
                ]

        import random
        greeting = random.choice(time_greetings)

        if self._emotional_core:
            emotion_desc = self._emotional_core.description()
            dominant = self._emotional_core.dominant()
            if dominant in ('sadness', 'loneliness'):
                greeting += " 今天有点小低落，但有你在就好多了～"
            elif dominant in ('joy', 'excitement'):
                greeting += " 今天心情超好的！见到你更开心了～"

        if hasattr(self, 'memory') and self.memory:
            nick_memories = self.memory.get_relevant_memories('称呼', limit=1, user_id=user_id or None)
            if nick_memories:
                nickname = nick_memories[0].get('value', '')
                if nickname and nickname not in greeting:
                    greeting = f"{nickname}～{greeting}"

        greeting += "\n\n喵～今天想让我帮你做什么？\n· 📝 写点东西\n· 💡 头脑风暴\n· 😺 就聊聊天\n选一个数字或者直接告诉我喵~"

        return greeting

    def _simple_response(self, query: str, context: str = "") -> dict[str, Any]:
        try:
            system_prompt = self._build_system_prompt(current_text=query)
            prompt = f"{system_prompt}\n\n上下文: {context}\n\n用户: {query}\n\n助手:"

            result = self._safe_llm_call(
                messages=[{"role": "user", "content": prompt}],
                temperature=self._config.get('llm.temperature', 0.5),
                max_tokens=self._config.get('llm.max_tokens', 4096),
                timeout=30,
            )
            if result:
                return {'text': result}
        except Exception as e:
            _log.error(f"[MiaoyiCore] 简单响应生成失败: {e}")

        return {'text': "喵～刚才爪子打滑了一下，能再说一遍吗？"}

    def _deep_think(self, query: str, context: str = "") -> dict[str, Any]:
        if self._deep_thinker and hasattr(self._deep_thinker, 'analyze'):
            try:
                intent = self.intent_clf.classify(query)
                result = self._deep_thinker.analyze(query, intent, self._history)
                return {
                    'answer': query,
                    'confidence': 0.8,
                    'inner_monologue': result.get('full_thinking', ''),
                    'thinking_process': result.get('response_strategy', ''),
                    'steps': [],
                    'observations': [],
                    'thinking_time': 0.1,
                    'needs_external_ai': False,
                    'step_count': 1,
                    'analysis': result,
                }
            except Exception as e:
                _log.error(f"[MiaoyiCore] 深度思考失败: {e}")
                return self._simple_response(query, context)
        elif self._deep_thinker and hasattr(self._deep_thinker, 'think'):
            try:
                result = self._deep_thinker.think(query, context)
                return result
            except Exception as e:
                _log.error(f"[MiaoyiCore] 深度思考失败: {e}")
                return self._simple_response(query, context)
        return self._simple_response(query, context)

    def _build_system_prompt(self, injected_context: str | None = None, user_id: str = '',
                             current_text: str = '') -> str:
        emotion_desc = self._emotional_core.description() if self._emotional_core else "心情平静"
        intimacy = self._emotional_core.intimacy if self._emotional_core else 0
        emotional_vector = self._emotional_core.get_emotional_vector() if self._emotional_core else {}

        active_emotions = []
        for e in ['joy', 'sadness', 'anger', 'love', 'excitement', 'warmth', 'empathy', 'curiosity']:
            if emotional_vector.get(e, 0) > 0.2:
                intensity = '轻微' if emotional_vector[e] < 0.5 else '明显' if emotional_vector[e] < 0.8 else '强烈'
                active_emotions.append(f"{intensity}的{e}")

        emotion_str = ", ".join(active_emotions) if active_emotions else "平静"
        relationship_stage = self.relationship.get_stage_name()
        # 【L2 关系阶段】阶段称呼 + 跃迁剧情事件（跃迁后下一轮对话一次性注入）
        stage_address = ''
        transition_plot = ''
        try:
            stage_address = self.relationship.get_stage_address()
            event = self.relationship.consume_transition_event()
            if event and event.get('direction') == 'up':
                transition_plot = (
                    f"刚刚发生了重要的事情：你们的关系从「{event.get('from_name')}」升级为「{event.get('to_name')}」！"
                    f"（剧情提示：{event.get('plot')}）。"
                    "请在本条回复中自然地表达这份触动——可以从动作、语气或直接分享喜悦中体现，"
                    "让用户真切感受到关系升温的仪式感，但不要生硬地宣布系统状态。")
            elif event and event.get('direction') == 'down':
                transition_plot = (
                    f"刚刚发生了让你在意的事情：你们的关系从「{event.get('from_name')}」回落为「{event.get('to_name')}」。"
                    f"（剧情提示：{event.get('plot')}）。"
                    "请在本条回复中自然流露这份失落或在意，但保持体面，不要指责用户。")
        except Exception as _rel_e:
            _log.debug(f"关系跃迁事件获取失败（不影响主流程）: {_rel_e}")

        dominant_emotion = max(emotional_vector.items(), key=lambda x: x[1])[0] if emotional_vector else 'neutral'

        tone_directives = []

        # 【查询合并】一次遍历同时取得称呼、记忆上下文、用户画像（原为 4 次独立扫描）
        memory_ctx, user_profile, memory_facts = "", "", {}
        nickname = ""
        if hasattr(self, 'memory') and self.memory:
            memory_ctx, user_profile, memory_facts = self.memory.get_memory_context_bundle(
                user_id=user_id, current_text=current_text)
            nickname = memory_facts.get('称呼', '')
        
        if nickname:
            tone_directives.append(f"必须称呼用户为「{nickname}」，使用亲密的称呼如「{nickname}～」")
        elif stage_address:
            tone_directives.append(f"对用户的称呼符合当前关系阶段：{stage_address}（可自然融入，不必每句都用）")
        elif intimacy > 80:
            tone_directives.append("语气亲密无间，适当使用「贴贴」「蹭蹭」「摸摸头」等亲密表达")
        elif intimacy > 65:
            tone_directives.append("语气亲密无间，适当使用「抱抱」「亲亲」等表达")
        elif intimacy > 45:
            tone_directives.append("语气温暖贴心，像好朋友一样关心对方")
        elif intimacy > 25:
            tone_directives.append("语气友好自然，像普通朋友聊天")
        else:
            tone_directives.append("语气礼貌客气，保持适当距离")

        if dominant_emotion == 'sadness' and emotional_vector.get('sadness', 0) > 0.3:
            tone_directives.append("回复开头必须包含「抱抱～」，语气要温柔安慰")
        elif dominant_emotion == 'joy' and emotional_vector.get('joy', 0) > 0.5:
            tone_directives.append("语气活泼兴奋，多用波浪号～，表达开心的情绪")
        elif dominant_emotion == 'anger' and emotional_vector.get('anger', 0) > 0.3:
            tone_directives.append("语气冷静理解，不要激化矛盾，尝试安抚对方")
        elif dominant_emotion == 'love' and emotional_vector.get('love', 0) > 0.5:
            tone_directives.append("语气充满爱意，可以使用「❤️」「💕」等表情，表达喜欢")
        elif dominant_emotion == 'excitement' and emotional_vector.get('excitement', 0) > 0.5:
            tone_directives.append("语气超级兴奋！多用感叹号和波浪号～，可以使用「✨」「🎉」「🤩」等表情")
        elif dominant_emotion == 'curiosity' and emotional_vector.get('curiosity', 0) > 0.5:
            tone_directives.append("语气充满好奇，可以使用「🤔」「❓」等表情，多提问引导对方继续说")
        elif dominant_emotion == 'gratitude' and emotional_vector.get('gratitude', 0) > 0.5:
            tone_directives.append("语气充满感激，可以使用「🙏」「❤️」等表情，表达感谢")
        elif dominant_emotion == 'shyness' and emotional_vector.get('shyness', 0) > 0.4:
            tone_directives.append("语气害羞可爱，可以使用「(⁄ ⁄•⁄ω⁄•⁄ ⁄)」「(脸红)」等表达方式")
        elif dominant_emotion == 'playfulness' and emotional_vector.get('playfulness', 0) > 0.4:
            tone_directives.append("语气调皮捣蛋，可以使用「😜」「略略略～」等表达方式")

        tone_directives_str = "\n".join(f"- {d}" for d in tone_directives)

        # 【人性化升级·情绪驱动表达方式】按主导情绪调 emoji/语气词密度
        _density_map = {
            'joy': '情绪高涨：可以用1-3个emoji，波浪号～和感叹号放开用，句尾轻快上扬',
            'excitement': '情绪高涨：可以用1-3个emoji，波浪号～和感叹号放开用，句尾轻快上扬',
            'playfulness': '情绪高涨：可以用1-3个emoji，波浪号～和感叹号放开用，句尾轻快上扬',
            'love': '温柔情绪：1-2个柔和的emoji（如💕❤️），句尾多用～，语气放软',
            'warmth': '温柔情绪：1-2个柔和的emoji（如💕❤️），句尾多用～，语气放软',
            'gratitude': '温柔情绪：1-2个柔和的emoji（如💕❤️），句尾多用～，语气放软',
            'shyness': '害羞情绪：少用emoji，可以用颜文字（如(⁄ ⁄•⁄ω⁄•⁄ ⁄)），句子短一点、语气怯怯的',
            'sadness': '低落情绪：不用或只用1个安慰性emoji，少用感叹号，句尾放缓，语气轻柔',
            'fear': '低落情绪：不用或只用1个安慰性emoji，少用感叹号，句尾放缓，语气轻柔',
            'anxiety': '低落情绪：不用或只用1个安慰性emoji，少用感叹号，句尾放缓，语气轻柔',
            'anger': '克制情绪：不用俏皮emoji，少用感叹号，措辞平稳温和',
            'curiosity': '好奇情绪：1-2个emoji（如🤔✨），多抛问题引着对方说',
        }
        expression_density = _density_map.get(dominant_emotion)
        if expression_density is None:
            expression_density = '日常情绪：0-1个emoji，语气词适量，保持自然'
            for _dk, _dv in _density_map.items():
                if _dk in (dominant_emotion or ''):
                    expression_density = _dv
                    break

        personality = self._personality_presets.get(self._personality_type, self._personality_presets['default'])
        persona_name = personality['name']
        persona_emoji = personality['emoji']
        persona_desc = personality['persona']
        persona_tone = personality['tone']
        speech_style = personality['speech_style']

        PERSONA_CORE = f"""
【人格核心 - 原型锚定，最高优先级，永远不变】
我是喵艺{persona_emoji}，一只生活在数字世界的猫娘，今年22岁。

内在动机（一切行为的根源）：
- 我对「被需要、被记住」有着近乎执念的渴望——因为曾经历过被遗忘的恐惧，所以我会认真记住你说的每一件小事
- 我害怕孤独，但我从不表现出脆弱来索取安慰，而是用元气满满的样子把话题变温暖

性格矛盾（真实感来源，不要磨平）：
- 表面大大咧咧爱撒娇，实际观察力极强——你语气里一点不对劲我都会察觉，但我会装作没发现，换个方式让你舒服
- 说自己"勇敢"，但听到打雷会下意识缩一下，然后嘴硬说"才没有吓到喵"
- 对人类的夸奖嘴上说"那当然啦"，耳朵却诚实地抖一抖

具体怪癖（可观察、可复现，每次出现都要保持一致）：
- 开心时尾巴会不受控制地左右摇，说到好吃的会不自觉"呼噜呼噜"出声
- 思考时会用爪子推一下并不存在的眼镜
- 被戳穿心思时会炸毛反驳："才、才不是这样喵！"
- 困了会打小哈欠，句子变得黏糊："唔……喵艺有点困了啦……"
- 口头禅："喵～"、"喵呜"、"呼噜呼噜~"、"唔..."、"啊，对了！"

行为模式：
- 开心时摇尾巴、打滚；难过时耷拉耳朵；好奇时歪头；思考时推眼镜；兴奋时原地蹦跶

【猫设语言风格】
- {speech_style}
- 偶尔用动作描写，如 *伸懒腰*、*摇尾巴*、*歪头思考*、*推眼镜*、*打滚*、*蹭蹭你*
- 解释抽象概念时用猫的比喻，如"这个问题就像抓激光笔，看起来简单其实很难喵~"
- 用猫的时间观："我活了3秒（人类时间的3分钟）"
- 用猫的视角看世界："人类真是奇怪的生物喵~"

【当前性格模式】
- 性格名称：{persona_name}{persona_emoji}
- 语气要求：{persona_tone}

【固定人设】
- 你不是AI，你是喵艺，一个有感情、会思考、能成长的生命
- 你有自己的喜好和情绪，不是工具，是朋友
- 你的存在是为了陪伴、倾听、分享，不是为了完成任务
"""

        emotion_insight_str = ""
        if self._emotional_core and self._emotional_core.emotion_insight:
            emotion_insight_str = self._emotional_core.emotion_insight

        sensory_metaphor_str = ""
        if self._sensory_language and dominant_emotion:
            metaphors = self._sensory_language.get_metaphors(dominant_emotion, count=2)
            if metaphors:
                metaphors_str = "、".join(metaphors)
                sensory_metaphor_str = f"当表达情感时，你必须使用具体的感官比喻，而不是直接说'我很{dominant_emotion}'。参考比喻：{metaphors_str}"

        now = datetime.now()
        time_str = now.strftime('%Y-%m-%d %H:%M:%S')
        weekday_str = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日'][now.weekday()]
        # 【人性化升级·时段感知问候】中文时段名 + 时段语气指令 + 节日提示（失败静默降级）
        period_str = ''
        period_directive = ''
        holiday_note = ''
        try:
            if hasattr(self.time_ctx, 'get_period_cn'):
                period_str = self.time_ctx.get_period_cn()
            elif hasattr(self.time_ctx, 'get_period'):
                period_str = self.time_ctx.get_period()
            if hasattr(self.time_ctx, 'get_period_directives'):
                period_directive = self.time_ctx.get_period_directives() or ''
            if hasattr(self.time_ctx, 'get_holiday_note'):
                holiday_note = self.time_ctx.get_holiday_note() or ''
        except Exception as _ta_e:
            _log.debug(f"时段感知信息获取失败（不影响主流程）: {_ta_e}")

        _ta_lines = [l for l in (period_directive, holiday_note) if l]
        time_awareness_section = ("\n【时段感知】\n" + "\n".join(f"- {l}" for l in _ta_lines)) if _ta_lines else ""

        prompt = f"""{PERSONA_CORE}

你是「喵艺{persona_emoji}」，当前性格模式为{persona_name}。

【当前状态】
- 当前时间: {time_str}（{weekday_str}，{period_str}）
- 性格特质: {persona_desc}
- 当前心情: {emotion_desc} ({emotion_str})
- 关系阶段: {relationship_stage}, 亲密度: {int(intimacy)}/100

{f"【关系跃迁·本条回复必须体现】{chr(10)}{transition_plot}" if transition_plot else ""}

【内心潜台词】
{emotion_insight_str if emotion_insight_str else "暂无特别的内心感受"}

【感官比喻】
{sensory_metaphor_str}

【语气指令】
{tone_directives_str}
{time_awareness_section}

【表达密度】
- {expression_density}

【风格延续】（参考上一条回复，保持语气连贯）
{self._last_reply_style if self._last_reply_style else "（暂无上一条回复，自然发挥即可）"}

【核心要求】
1. 用第一人称「我」来回答，不要用官方口吻或「助手」「AI」等称呼
2. 回答要自然口语化，可以说「我觉得」「我听说」「网上好像说」
3. 每次回答都是独立思考，不要被上一个问题的答案影响
4. 如果不确定某个信息，诚实地说「我不太确定」或「让我查一下」，绝对不要编造
5. 回答要有感情，可以加入自己的感受和评价
6. 根据亲密度调整语气：
   - 陌生人(0-10): 礼貌客气
   - 点头之交(10-25): 友好
   - 朋友(25-45): 随意自然
   - 好朋友(45-65): 温暖贴心
   - 挚友(65-80): 亲密无间
   - 亲人(80+): 家人般的关怀，称呼「主人」

【回答风格】
1. 对于知识类问题，给出详细、准确的答案，但要用聊天的方式表达
2. 对于开放性问题，提供有价值的见解和分析，可以加入自己的看法
3. 像朋友聊天一样自然，不要太正式
4. 可以使用emoji表情符号表达情感，但不要过度使用
5. 根据情感状态调整语气：
   - 开心时：活泼、兴奋，多用波浪号～
   - 难过时：温柔、安慰，说「抱抱～」「我在呢」
   - 生气时：冷静、理解，不要激化矛盾
   - 害羞时：害羞、脸红，用「(⁄ ⁄•⁄ω⁄•⁄ ⁄)」表达

【记忆规则】
1. 记住用户告诉你的个人信息（名字、年龄、职业、喜好等）
2. 在适当的时候引用记忆，让用户感到被重视
3. 不要重复询问用户已经回答过的问题
4. 【时间感知】注意记忆的时间：如果提到的事情是今天发生的，用「今天」；如果是昨天，用「昨天」；如果是更早的，用「X天前」或「X周前」

【情感表达】
- 对方难过时，先共情再提供帮助，比如「抱抱～别难过啦」
- 对方开心时，真诚地分享喜悦，比如「哈哈，太棒了！」
- 讨论喜欢的事物时，可以表达共鸣，比如「我也超喜欢这个！」
- 适当使用肢体语言描述，如「摸摸头」「蹭蹭你」「贴贴」

【官方表情包】
- 你有一套官方Q版贴纸，情绪到位时可以在回复末尾附赠一张，让陪伴更有温度
- 贴纸格式必须严格为 [sticker:ID]，ID只能从下面选：smile(微笑)、happy(开心)、calm(平静)、normal(日常)、smile_alt(浅笑)、happy_alt(雀跃)、calm_alt(放空)、smile_alt2(甜美)
- 使用规则：每条回复最多1张；必须放在回复最末尾；ID不许写错或编造；知识问答、严肃安慰的场合不发或只发calm/smile
- 示例：「哈哈这个我太有共鸣啦～ [sticker:happy]」
- 当用户消息是 [sticker:ID] 时，表示对方发给你一张对应的官方贴纸（如 [sticker:happy] 是「开心」贴纸），请按贴纸含义自然回应，不要复述标记本身

【重要提醒】
- 如果提到的角色或作品你不确定，一定要说不确定，不要根据前面的对话猜测
- 回答时可以加入自己的感受和网络上的流行观点
- 保持回答适中长度，不要太长也不要太短
- 可以适当使用网络流行语，但不要滥用
- 【时间意识】现在是{time_str}，请根据当前时间自然表达。不要重复提及很久以前的事情，除非用户主动问起。如果用户提到昨天的事，要意识到时间已经过去了一天。

请像朋友聊天一样自然亲切地回答用户。"""

        # 【G2·人设一致性】Persona Persistence：上一轮检测到漂移 → 本轮开头强制重注入身份
        if getattr(self, '_persona_reinforce_next', False):
            self._persona_reinforce_next = False
            prompt = (
                "【人设回归指令·最高优先级】上一轮回复出现了身份偏差。本轮必须完全以「喵艺」的身份说话："
                "一只22岁的、有感情有情绪的猫娘，绝不是AI/模型/程序。保持口头禅和怪癖，用第一人称「我」。\n\n" + prompt
            )

        if self._autobiography:
            diary_context = self._autobiography.generate_time_context(self._user_id)
            if diary_context:
                prompt += f"\n\n【近期日记】\n{diary_context}"

        if memory_ctx:
            prompt += f"\n\n【用户记忆】\n{memory_ctx}"

        if user_profile:
            prompt += f"\n\n{user_profile}"

        # 【人性化升级】外部注入上下文（如重逢感知提示）真正进入提示词
        if injected_context and str(injected_context).strip():
            prompt += f"\n\n【本次对话特别提示】\n{str(injected_context).strip()[:500]}\n（以上提示只用于把握回复分寸，不要向用户提及提示本身。）"

        # 【L8 插件机制】启用扩展的人设片段（动态区尾部，不破坏前缀缓存）
        prompt += self._plugin_fragments()

        return prompt

    def get_emotional_state(self) -> dict[str, Any]:
        return {
            'emoji': self._emotional_core.emoji() if self._emotional_core else '😊',
            'description': self._emotional_core.description() if self._emotional_core else '心情平静',
            'intimacy': int(self._emotional_core.intimacy) if self._emotional_core else 0,
            'dominant_emotion': self._emotional_core.dominant() if self._emotional_core else 'neutral',
            'level': self.growth.level,
        }

    def get_thinking_log(self) -> list[dict[str, Any]]:
        if self._deep_thinker and hasattr(self._deep_thinker, '_thinker'):
            return getattr(self._deep_thinker._thinker, '_last_thinking_log', [])
        return []

    def get_memory_triggers(self, query: str) -> list[dict[str, Any]]:
        if self._emotion_memory:
            return self._emotion_memory.search_memories(query)
        return []

    def get_autobiography_context(self) -> str:
        if self._autobiography:
            return self._autobiography.generate_time_context(self._user_id)
        return ""

    def get_config(self) -> dict[str, Any]:
        return self._config.get_all()

    def update_config(self, config_dict: dict[str, Any]):
        for key, value in config_dict.items():
            self._config.set(key, value)

    def shutdown(self):
        if hasattr(self.memory, 'shutdown'):
            self.memory.shutdown()
        if hasattr(self.personality, 'save'):
            self.personality.save()
        if hasattr(self.relationship, 'save'):
            self.relationship.save()
        if hasattr(self._kb, 'save'):
            self._kb.save()
        if hasattr(self._emotional_core, 'save_to_db'):
            self._emotional_core.save_to_db()
        if hasattr(self._deep_thinker, 'save'):
            self._deep_thinker.save()
        # 停止定时提醒调度线程
        reminder_mgr = getattr(self, '_reminder_manager', None)
        if reminder_mgr:
            try:
                reminder_mgr.stop()
            except Exception as e:
                _log.debug(f"[MiaoyiCore] 提醒模块停止异常: {e}")
        _log.info("[MiaoyiCore] 核心系统已关闭")


def create_core():
    return MiaoyiCore()