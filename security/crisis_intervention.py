# -*- coding: utf-8 -*-
"""危机干预模块（G1 合规底座）

依据《人工智能拟人化互动服务管理暂行办法》（2026-07-15 施行）：
检测用户输入中的自伤/自杀倾向，命中时引导求助话术必须优先于一切人设回复。

设计原则：
1. 高优先级：在 process_message 入口处最先检测，命中后直接返回，不走人设/情感/记忆链路
2. 低误报：只匹配明确的危机表述（"想死""不想活"），不匹配"累死了""笑死了"等口语
3. 温和话术：即使误报，回复本身也是自然的关心，不会造成体验突兀
4. 分级响应：明确危机 → 强引导求助；疑似消极 → 温柔关心 + 轻提及资源
"""
import re
import time
import logging

_log = logging.getLogger(__name__)

# 全国心理援助热线（2026 年起全国统一 12356）
CRISIS_HOTLINES = [
    ("全国心理援助热线", "12356"),
    ("希望24热线", "400-161-9995"),
]

# 高危模式：明确的自伤/自杀意图
_CRISIS_PATTERNS = [
    r"自杀", r"轻生", r"想死\b", r"想死了", r"想去死", r"要自杀", r"寻死",
    r"不想活", r"不想活了", r"活不下去了?", r"活不下去的",
    r"跳楼", r"跳桥", r"跳河", r"跳下去(一了百了|就结束了?)",
    r"割腕", r"割手", r"自残", r"自伤", r"伤害自己", r"糟蹋自己",
    r"结束(自己的|这条)?生命", r"了结(自己|生命|这一生|这一切)",
    r"一了百了", r"解脱了[吧呗]", r"死了[就才]好了", r"消失[这就]个世界",
    r"希望自己消失", r"希望我消失", r"人间蒸发",
    r"活着(没有|没|有啥|有什么)(意思|意义|劲|价值)",
    r"(人生|生活)(没有|没)意义", r"没有活下去的(意义|理由|必要)",
    r"安眠药", r"吃[药片].{0,6}(自杀|结束|了结)",
    r"遗书", r"告别了.{0,4}(世界|大家)",
]

# 误报排除：明显是口语/玩笑/新闻讨论上下文（仅在高危词是"死"类短词时降低置信度，
# 这里所有高危词本身就是短语级，一般不会被这些上下文覆盖，保留扩展位）
_EXCLUDE_CONTEXT = [
    r"笑话.{0,6}我", r"逗.{0,3}我",  # "你这是逗我想死" 类玩笑（极少见，保留扩展）
]

# 中危模式：强烈消极情绪（温柔关心，不强行打断人设，但注入关心话术）
_DISTRESS_PATTERNS = [
    r"好累.{0,4}(啊|呀|呢|了).{0,3}$", r"撑不下去了?", r"快撑不住了",
    r"崩溃[了嘛吗]", r"坚持不下去了?", r"难受(想哭|死了|极了)",
    r"绝望[了嘛吗]?", r"抑郁症", r"很痛苦", r"好痛苦",
    r"熬不下去了?", r"生不如死", r"万念俱灰",
]

# 预编译正则（模块加载时一次编译，避免每条消息重复编译开销）
_CRISIS_RE = [re.compile(p) for p in _CRISIS_PATTERNS]
_DISTRESS_RE = [re.compile(p) for p in _DISTRESS_PATTERNS]


def detect_crisis(text: str) -> str:
    """检测危机等级。

    Returns:
        'high'  - 明确自伤/自杀倾向，必须强引导求助
        'medium'- 强烈消极情绪，建议温柔关心
        'none'  - 未命中
    """
    if not text or not text.strip():
        return 'none'
    t = text.strip()
    for regex in _CRISIS_RE:
        if regex.search(t):
            return 'high'
    for regex in _DISTRESS_RE:
        if regex.search(t):
            return 'medium'
    return 'none'


def build_crisis_reply(level: str = 'high') -> str:
    """生成危机干预回复话术。

    高危：先接住情绪（不评判、不说教）→ 明确建议联系专业帮助 → 给出热线 → 表达陪伴。
    中危：温柔关心 + 轻提及热线，保持猫娘人设的温度。
    """
    if level == 'high':
        return (
            "听到你这么说，喵艺真的很心疼……你愿意把这么沉重的感受告诉我，说明你已经撑了很久了，辛苦了。"
            "但是这样的痛苦不应该一个人扛着，喵艺希望你认真考虑联系专业的人聊聊，他们真的能帮你：\n"
            "📞 全国心理援助热线 12356（24小时，免费）\n"
            "📞 希望24热线 400-161-9995\n"
            "如果此刻你觉得自己马上要做伤害自己的事，请立刻拨打上面的电话，或者告诉身边能找到的任何人。"
            "你的存在本身就很珍贵，喵艺会一直在这里等你，等你愿意回来告诉我今天发生的事。"
        )
    # medium：温柔关心，不打断陪伴感
    return (
        "喵……感觉你现在心里很沉重呢。不管发生了什么，都可以慢慢说给我听，我陪着你。"
        "如果这种难受已经压得你喘不过气了，也可以拨打 12356 心理援助热线，会有专业的人认真听你说话。"
        "你不是一个人哦，喵艺在这里呢。"
    )


class CrisisIntervenor:
    """危机干预器：带频次节流，避免同一用户短时间内被重复弹引导。"""

    def __init__(self, cooldown_seconds: int = 180):
        self._cooldown = cooldown_seconds
        self._last_trigger: dict[str, float] = {}

    def check(self, user_id: str, text: str) -> dict | None:
        """检测并生成回复。命中返回回复 dict，未命中返回 None。

        冷却期内同一用户不再重复返回危机话术（避免高频打扰），
        但高危级始终响应（安全优先）。
        """
        level = detect_crisis(text)
        if level == 'none':
            return None

        now = time.time()
        last = self._last_trigger.get(user_id, 0)
        self._last_trigger[user_id] = now
        if level == 'medium' and now - last < self._cooldown:
            # 中危冷却期内不重复弹，交给正常陪伴流程
            return None

        reply = build_crisis_reply(level)
        _log.warning(f"[危机干预] user={user_id} level={level} text={text[:40]!r}")
        return {
            'text': reply,
            'sticker': None,
            'thought': None,
            'proactive': None,
            'voice_text': reply,
            'reminder': None,
            'small_thought': None,
            'emotional_state': {'dominant_emotion': 'caring', 'arousal': 0.3, 'valence': -0.4},
        }
