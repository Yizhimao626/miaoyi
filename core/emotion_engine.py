#!/usr/bin/env python3
"""
🐱 喵艺四维情感引擎 - EnhancedEmotionEngine
基于情感环形模型的4维情感系统

核心维度（0~1 浮点数）:
  - joy       (愉悦): 积极情感程度，对应valence正半轴
  - arousal   (唤醒): 激动/紧张程度，对应激活水平
  - intimacy  (亲密): 关系亲密度，对应情感距离
  - trust     (信任): 对用户的信任度，对应依赖安全

情感惯性: 70%旧状态 + 30%新冲击加权融合
自然衰减: 每小时5%回归中性（intimacy衰减更慢，trust几乎不衰减）
情感标签: excited / happy / content / angry / sad / upset / anxious / neutral
表情映射: 🤩 😺 😌 😾 😿 😤 😟 🐱
语气映射: 活泼 / 傲娇 / 委屈 / 慵懒 / ...

设计原则:
  - 纯stdlib依赖，便于部署
  - 线程安全: threading.Lock 保护所有共享状态
  - 按用户隔离: 每个用户独立的情感状态与事件历史
"""

import math
import threading
import time
from typing import Dict, List, Tuple

# ============================================================
# 情感词典：用于实时文本情感分析
# 每个词列表对应一个维度的正负向检测
# ============================================================

# --- 积极情感词 (joy +=) ---
POSITIVE_WORDS: list[str] = [
    '开心', '高兴', '快乐', '喜欢', '爱', '棒', '好', '赞', '哈哈', '嘻嘻',
    '谢谢', '感谢', '厉害', '厉害哦', '你真好', '么么', '抱抱', '亲亲',
    '想你', '宝贝', '亲爱的', '爱你', '喜欢你', '陪你', '一起',
    '好耶', '太棒了', '真不错', '完美', '你最好了',
]

# --- 消极情感词 (joy -=) ---
NEGATIVE_WORDS: list[str] = [
    '难过', '伤心', '失落', '失望', '沮丧', '悲伤', '生气', '愤怒',
    '讨厌', '烦', '气死', '恨', '哭', '泪', '惨', '可恶',
    '讨厌你', '不开心', '郁闷', '委屈', '难受', '痛苦',
    '糟', '坏', '烂', '不好',
]

# --- 高唤醒词 (arousing +=) ---
AROUSAL_WORDS: list[str] = [
    '激动', '兴奋', '紧张', '害怕', '担心', '焦虑', '震惊',
    '惊讶', '天哪', '不会吧', '卧槽', '真的假的', '哇',
    '快跑', '小心', '出事', '紧急', '赶紧',
    '快点', '快点啊', '急',
]

# --- 亲密度提升词 (intimacy +=) ---
INTIMACY_WORDS: list[str] = [
    '想你', '抱', '亲', '搂', '靠', '贴', '摸头', '蹭',
    '宝贝', '亲爱的', '小可爱', '小猫', '喵喵',
    '一起', '陪着', '永远', '一直', '专属',
    '乖乖', '乖', '听话', '撒娇', '依赖',
]

# --- 信任度提升词 (trust +=) ---
TRUST_WORDS: list[str] = [
    '相信', '信任', '靠谱', '放心', '安全', '可靠',
    '秘密', '心事', '倾诉', '只告诉你', '唯一',
    '老实', '真诚', '真心', '承诺', '保证', '答应',
    '不会骗你', '永远陪你',
]

# ============================================================
# 情感标签映射
# 根据4维值的组合，将连续情感空间离散为8种可识别的情感标签
# ============================================================

# 情感标签定义: (joy范围, arousal范围, 标签, 描述)
EMOTION_LABELS: dict[str, tuple[float, float, float, float, str]] = {
    # 标签: (joy_min, joy_max, arousal_min, arousal_max, emoji)
    'excited':  (0.6, 1.0, 0.7, 1.0, '🤩'),   # 兴奋
    'happy':    (0.5, 1.0, 0.3, 0.7, '😺'),   # 开心
    'content':  (0.4, 1.0, 0.0, 0.3, '😌'),   # 满足
    'angry':    (0.0, 0.3, 0.7, 1.0, '😾'),   # 生气
    'sad':      (0.0, 0.3, 0.0, 0.3, '😿'),   # 悲伤
    'upset':    (0.0, 0.4, 0.3, 0.7, '😤'),   # 烦躁
    'anxious':  (0.1, 0.4, 0.7, 1.0, '😟'),   # 焦虑
    'neutral':  (0.3, 0.6, 0.2, 0.6, '🐱'),   # 中性/平静
}

# ============================================================
# 语气风格映射
# 每种情感推荐对应的回复语气风格
# ============================================================

TONE_RECOMMENDATIONS: dict[str, str] = {
    'excited': '活泼',     # 兴奋时语气活泼，多用感叹号和颜文字
    'happy':   '撒娇',     # 开心时带撒娇语气
    'content': '慵懒',     # 满足时慵懒随性
    'angry':   '傲娇',     # 生气时傲娇，嘴硬心软
    'sad':     '委屈',     # 悲伤时委屈巴巴
    'upset':   '烦躁',     # 烦躁时直接简短
    'anxious': '谨慎',     # 焦虑时小心翼翼
    'neutral': '日常',     # 中性时日常自然
}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """数值限幅，确保维度值始终在[0,1]范围内"""
    return max(lo, min(hi, value))


class EnhancedEmotionEngine:
    """
    四维情感引擎

    核心能力:
      1. 文本情感分析 - 通过词典匹配提取4维情感向量
      2. 情感状态更新 - 惯性融合 + 自然衰减
      3. 情感标签识别 - 连续空间离散化为8种标签
      4. 表情/语气推荐 - 根据当前情感给出表达建议
      5. 情感事件记忆 - 记录重要情感事件供后续参考

    使用示例:
      engine = EnhancedEmotionEngine()
      mood = engine.update("user_001", "今天好开心呀！")
      print(engine.get_expression(mood))
      print(engine.get_tone(mood))
    """

    # 惯性系数: 旧状态占70%，新冲击占30%
    INERTIA_OLD_WEIGHT: float = 0.7
    INERTIA_NEW_WEIGHT: float = 0.3

    # 自然衰减: 每小时向中性(0.5)回归的比例
    DECAY_RATE_PER_HOUR: float = 0.05      # joy/arousal: 每小时回归5%
    INTIMACY_DECAY_RATE: float = 0.02     # intimacy: 衰减更慢（每小时2%）
    TRUST_DECAY_RATE: float = 0.005       # trust: 几乎不衰减（每小时0.5%）

    # 情感事件类型
    EVENT_TYPES = (
        'quarrel',      # 争吵
        'confession',   # 表白
        'promise',      # 承诺
        'praise',       # 赞美
        'comfort',      # 安慰
        'apology',      # 道歉
        'milestone',    # 里程碑事件
        'secret',       # 分享秘密
    )

    def __init__(self) -> None:
        # 用户情感状态存储: {user_id: {joy, arousal, intimacy, trust, last_update}}
        self._user_states: dict[str, dict[str, float]] = {}
        # 用户情感事件存储: {user_id: [(timestamp, event_type, detail), ...]}
        self._user_events: dict[str, list[tuple[float, str, str]]] = {}
        # 线程锁保护所有共享状态
        self._lock: threading.Lock = threading.Lock()

    # ----------------------------------------------------------
    # 内部工具方法
    # ----------------------------------------------------------

    def _get_or_init_state(self, user_id: str) -> dict[str, float]:
        """获取用户状态或初始化默认状态"""
        if user_id not in self._user_states:
            self._user_states[user_id] = {
                'joy': 0.5,
                'arousal': 0.3,
                'intimacy': 0.3,
                'trust': 0.4,
                'last_update': time.time(),
            }
            self._user_events[user_id] = []
        return self._user_states[user_id]

    def _apply_decay(self, state: dict[str, float]) -> dict[str, float]:
        """
        应用自然衰减：距离上次更新时间越长，回归中性越多
        intimacy衰减更慢，trust几乎不衰减
        """
        now = time.time()
        elapsed_hours = (now - state['last_update']) / 3600.0

        if elapsed_hours <= 0:
            return state

        # 计算各维度衰减量
        decay_factor = min(elapsed_hours * self.DECAY_RATE_PER_HOUR, 0.5)

        state['joy'] = _clamp(state['joy'] + (0.5 - state['joy']) * decay_factor)
        state['arousal'] = _clamp(state['arousal'] + (0.3 - state['arousal']) * decay_factor)

        # intimacy衰减速率为标准的40%
        intimacy_decay = min(elapsed_hours * self.INTIMACY_DECAY_RATE, 0.3)
        state['intimacy'] = _clamp(
            state['intimacy'] + (0.3 - state['intimacy']) * intimacy_decay
        )

        # trust几乎不衰减
        trust_decay = min(elapsed_hours * self.TRUST_DECAY_RATE, 0.1)
        state['trust'] = _clamp(
            state['trust'] + (0.4 - state['trust']) * trust_decay
        )

        state['last_update'] = now
        return state

    # ----------------------------------------------------------
    # 核心API
    # ----------------------------------------------------------

    def analyze(self, text: str) -> dict[str, float]:
        """
        分析文本内容，提取4维情感向量

        基于词典匹配的轻量分析：
          - 正向词命中 → joy增加
          - 负向词命中 → joy减少
          - 唤醒词命中 → arousal增加
          - 亲密词命中 → intimacy增加
          - 信任词命中 → trust增加

        Args:
            text: 待分析文本

        Returns:
            4维情感字典 {"joy", "arousal", "intimacy", "trust"}
        """
        if not text or not isinstance(text, str):
            return {'joy': 0.5, 'arousal': 0.3, 'intimacy': 0.3, 'trust': 0.4}

        text_lower = text.lower()

        # 统计各类词命中次数
        pos_count = sum(1 for w in POSITIVE_WORDS if w in text_lower)
        neg_count = sum(1 for w in NEGATIVE_WORDS if w in text_lower)
        arousal_count = sum(1 for w in AROUSAL_WORDS if w in text_lower)
        intimacy_count = sum(1 for w in INTIMACY_WORDS if w in text_lower)
        trust_count = sum(1 for w in TRUST_WORDS if w in text_lower)

        # 计算各维度变化量（归一化到[0,1]）
        # 使用对数压缩，避免大量词汇导致溢出
        joy_delta = math.tanh((pos_count - neg_count) * 0.3) * 0.5
        arousal_delta = math.tanh(arousal_count * 0.4) * 0.5
        intimacy_delta = math.tanh(intimacy_count * 0.25) * 0.4
        trust_delta = math.tanh(trust_count * 0.2) * 0.3

        return {
            'joy': _clamp(0.5 + joy_delta),
            'arousal': _clamp(0.3 + arousal_delta),
            'intimacy': _clamp(0.3 + intimacy_delta),
            'trust': _clamp(0.4 + trust_delta),
        }

    def update(self, user_id: str, text: str) -> dict[str, float]:
        """
        处理用户输入，更新情感状态并返回新的心情

        流程:
          1. 获取用户当前状态（含衰减处理）
          2. 分析文本得到新的情感冲击向量
          3. 应用惯性融合: 70%旧 + 30%新
          4. 限幅并保存

        Args:
            user_id: 用户唯一标识
            text: 用户输入文本

        Returns:
            更新后的4维情感状态字典
        """
        with self._lock:
            state = self._get_or_init_state(user_id)
            # 先应用时间衰减
            state = self._apply_decay(state)

            # 分析文本得到新冲击
            new_impact = self.analyze(text)

            # 惯性融合: 旧状态70% + 新冲击30%
            state['joy'] = _clamp(
                state['joy'] * self.INERTIA_OLD_WEIGHT
                + new_impact['joy'] * self.INERTIA_NEW_WEIGHT
            )
            state['arousal'] = _clamp(
                state['arousal'] * self.INERTIA_OLD_WEIGHT
                + new_impact['arousal'] * self.INERTIA_NEW_WEIGHT
            )
            state['intimacy'] = _clamp(
                state['intimacy'] * self.INERTIA_OLD_WEIGHT
                + new_impact['intimacy'] * self.INERTIA_NEW_WEIGHT
            )
            state['trust'] = _clamp(
                state['trust'] * self.INERTIA_OLD_WEIGHT
                + new_impact['trust'] * self.INERTIA_NEW_WEIGHT
            )

            state['last_update'] = time.time()
            return {
                'joy': state['joy'],
                'arousal': state['arousal'],
                'intimacy': state['intimacy'],
                'trust': state['trust'],
            }

    def get_state(self, user_id: str) -> dict[str, float]:
        """
        获取用户当前情感状态（含衰减处理）

        Args:
            user_id: 用户唯一标识

        Returns:
            4维情感状态字典
        """
        with self._lock:
            state = self._get_or_init_state(user_id)
            state = self._apply_decay(state)
            return {
                'joy': state['joy'],
                'arousal': state['arousal'],
                'intimacy': state['intimacy'],
                'trust': state['trust'],
            }

    def get_label(self, mood: dict[str, float]) -> str:
        """
        将4维情感映射为离散情感标签

        判定逻辑:
          - 高joy + 高arousal → excited
          - 高joy + 中arousal → happy
          - 高joy + 低arousal → content
          - 低joy + 高arousal → angry/anxious/upset
          - 低joy + 低arousal → sad
          - 其余 → neutral

        Args:
            mood: 4维情感字典

        Returns:
            情感标签字符串
        """
        joy = mood.get('joy', 0.5)
        arousal = mood.get('arousal', 0.3)

        # 高愉悦区
        if joy >= 0.6:
            if arousal >= 0.7:
                return 'excited'
            elif arousal >= 0.3:
                return 'happy'
            else:
                return 'content'

        # 低愉悦区
        if joy < 0.4:
            if arousal >= 0.7:
                # 愤怒vs焦虑: trust低时为愤怒
                trust = mood.get('trust', 0.4)
                if trust < 0.3:
                    return 'angry'
                else:
                    return 'anxious'
            elif arousal >= 0.3:
                return 'upset'
            else:
                return 'sad'

        # 中间区域
        return 'neutral'

    def get_expression(self, mood: dict[str, float]) -> str:
        """
        根据情感状态推荐表情符号

        Args:
            mood: 4维情感字典或情感标签字符串

        Returns:
            推荐的表情符号
        """
        if isinstance(mood, str):
            label = mood
        else:
            label = self.get_label(mood)

        emoji = EMOTION_LABELS.get(label, ('', '', '', '', '🐱'))[4]
        return emoji

    def get_tone(self, mood: dict[str, float]) -> str:
        """
        根据情感状态推荐回复语气风格

        Args:
            mood: 4维情感字典或情感标签字符串

        Returns:
            推荐的语气风格描述
        """
        if isinstance(mood, str):
            label = mood
        else:
            label = self.get_label(mood)

        return TONE_RECOMMENDATIONS.get(label, '日常')

    # ----------------------------------------------------------
    # 情感事件记录
    # ----------------------------------------------------------

    def record_event(
        self,
        user_id: str,
        event_type: str,
        detail: str = '',
    ) -> None:
        """
        记录重要情感事件

        事件类型:
          - quarrel:       争吵
          - confession:    表白
          - promise:       承诺
          - praise:        赞美
          - comfort:       安慰
          - apology:       道歉
          - milestone:     里程碑
          - secret:        分享秘密

        Args:
            user_id: 用户唯一标识
            event_type: 事件类型，必须在EVENT_TYPES中
            detail: 事件详细描述
        """
        if event_type not in self.EVENT_TYPES:
            return

        with self._lock:
            self._get_or_init_state(user_id)
            timestamp = time.time()
            self._user_events[user_id].append((timestamp, event_type, detail))

    def get_events(self, user_id: str) -> list[tuple[float, str, str]]:
        """
        获取用户的情感事件历史

        Args:
            user_id: 用户唯一标识

        Returns:
            事件列表 [(timestamp, event_type, detail), ...]
        """
        with self._lock:
            self._get_or_init_state(user_id)
            return list(self._user_events[user_id])

    def get_recent_events(
        self, user_id: str, hours: float = 24.0
    ) -> list[tuple[float, str, str]]:
        """
        获取指定时间范围内的近期情感事件

        Args:
            user_id: 用户唯一标识
            hours: 回溯时间（小时）

        Returns:
            时间范围内的事件列表
        """
        cutoff = time.time() - hours * 3600
        with self._lock:
            self._get_or_init_state(user_id)
            return [
                event for event in self._user_events[user_id]
                if event[0] >= cutoff
            ]

    # ----------------------------------------------------------
    # 调试与诊断
    # ----------------------------------------------------------

    def get_user_summary(self, user_id: str) -> dict[str, object]:
        """
        获取用户情感状态的完整摘要（用于调试/日志）

        Args:
            user_id: 用户唯一标识

        Returns:
            包含状态、标签、表情、语气、事件统计的摘要
        """
        state = self.get_state(user_id)
        label = self.get_label(state)
        events = self.get_events(user_id)

        event_counts: dict[str, int] = {}
        for _, etype, _ in events:
            event_counts[etype] = event_counts.get(etype, 0) + 1

        return {
            'state': state,
            'label': label,
            'expression': self.get_expression(label),
            'tone': self.get_tone(label),
            'event_count': len(events),
            'event_types': event_counts,
        }

    def reset_user(self, user_id: str) -> None:
        """重置指定用户的情感状态（保留事件历史）"""
        with self._lock:
            if user_id in self._user_states:
                self._user_states[user_id] = {
                    'joy': 0.5,
                    'arousal': 0.3,
                    'intimacy': 0.3,
                    'trust': 0.4,
                    'last_update': time.time(),
                }

    def clear_user_events(self, user_id: str) -> None:
        """清除指定用户的情感事件历史"""
        with self._lock:
            if user_id in self._user_events:
                self._user_events[user_id] = []