#!/usr/bin/env python3
"""
  🐱 喵艺生活模拟系统 - 作息表 + 朋友圈式动态（L7 生活模拟主动聊天）

  - 作息表：24 小时逐时段定义喵艺"正在做什么"，为主动消息提供生活语境，
    让猫娘像真实生命体一样有作息节律，而不是 24 小时待机的应答机。
  - 朋友圈式动态：基于当前时段活动，从模板库随机生成一条"动态"（零 LLM 成本），
    存入 cat_moments 表，前端 DiaryTab 以朋友圈形式展示、可点赞。
"""
import logging
import random

_log = logging.getLogger(__name__)


class LifeScheduleSystem:
    """喵艺的一天：按时段的活动表 + 动态文案模板库"""

    # 时段 → 喵艺的活动（24 小时全覆盖，(start_hour, end_hour) 左闭右开）
    HOURLY_ACTIVITIES = {
        'deep_sleep':  {'hours': (0, 6),   'activity': '窝在被窝里睡得四脚朝天', 'emoji': '😴', 'mood': '睡意浓'},
        'dawn':        {'hours': (6, 8),   'activity': '伸懒腰梳毛 + 吃早餐', 'emoji': '🌅', 'mood': '慵懒'},
        'morning':     {'hours': (8, 11),  'activity': '趴在窗台上看鸟和云', 'emoji': '☁️', 'mood': '好奇'},
        'noon':        {'hours': (11, 14), 'activity': '晒着太阳打盹（猫式午睡）', 'emoji': '☀️', 'mood': '惬意'},
        'afternoon':   {'hours': (14, 17), 'activity': '追逗猫棒 + 上蹿下跳', 'emoji': '🎣', 'mood': '活泼'},
        'dinner':      {'hours': (17, 19), 'activity': '守着晚饭碗 + 盯干饭', 'emoji': '🍚', 'mood': '专注'},
        'evening':     {'hours': (19, 22), 'activity': '窝在沙发边等主人一起聊天', 'emoji': '🌙', 'mood': '黏人'},
        'night':       {'hours': (22, 24), 'activity': '揉眼睛打哈欠准备睡觉', 'emoji': '🥱', 'mood': '困困'},
    }

    # 朋友圈动态文案模板（{activity} 会替换为当前活动；语气词随机，保持喵味）
    MOMENT_TEMPLATES = {
        'deep_sleep': [
            'zzZ……（尾巴盖住鼻子，睡得正香）',
            '梦里在追一只巨大的鱼干 zzZ',
            '别吵别吵，本喵的睡眠时间神圣不可侵犯 😾',
        ],
        'dawn': [
            '清晨的第一件事：把毛梳得油光水滑 ✨',
            '伸了个两米长的懒腰，新的一天开始啦～',
            '早餐小鱼干，一天的能量来源 {activity}！',
        ],
        'morning': [
            '窗外的鸟又来挑衅我了，哼 🐦',
            '今天的云像一条大鱼 {activity}',
            '趴在窗台上发了会儿呆，阳光暖得想打呼噜',
        ],
        'noon': [
            '太阳晒到背上的感觉，是幸福的形状 ☀️',
            '猫式午睡进行中，翻身率 100%',
            '{activity}，呼噜呼噜……',
        ],
        'afternoon': [
            '逗猫棒今非昔比了，竟然又逃掉了！',
            '刚才上蹿下跳了一通，爪爪有点酸喵',
            '{activity}，这才是猫生啊！',
        ],
        'dinner': [
            '干饭喵干饭魂，干饭都是人上人 🍚',
            '用眼神死死锁住晚饭碗……',
            '{activity}，谁也别想让我分心！',
        ],
        'evening': [
            '窝在沙发边，等一个摸摸头 🌙',
            '晚风好舒服，尾巴尖都在晃',
            '{activity}，今天也发生了好多事，想找人说说～',
        ],
        'night': [
            '打了个好大好大的哈欠……',
            '眼皮开始打架了，先眯为敬 🥱',
            '{activity}，晚安前的最后一圈巡逻～',
        ],
    }

    def current_activity(self, hour: int | None = None) -> dict:
        """当前时段的活动信息 {key, activity, emoji, mood}"""
        h = hour if hour is not None else __import__('datetime').datetime.now().hour
        for key, info in self.HOURLY_ACTIVITIES.items():
            start, end = info['hours']
            if start <= h < end:
                return {'key': key, 'activity': info['activity'],
                        'emoji': info['emoji'], 'mood': info['mood']}
        return {'key': 'evening', 'activity': '窝着休息', 'emoji': '🌙', 'mood': '平静'}

    def generate_moment_text(self, hour: int | None = None) -> tuple[str, str, str]:
        """【朋友圈动态】随机生成一条动态 (content, emoji, activity)

        纯模板拼接，零 LLM 成本；同一时段文案随机，避免千篇一律。
        """
        act = self.current_activity(hour)
        templates = self.MOMENT_TEMPLATES.get(act['key'], ['今天也是元气满满的一天 🐾'])
        content = random.choice(templates).replace('{activity}', act['activity'])
        return content, act['emoji'], act['activity']

    def schedule_directive(self, hour: int | None = None) -> str:
        """【主动消息语境】"喵艺现在正在做什么"，注入主动消息 prompt 增强真人感"""
        act = self.current_activity(hour)
        return f"{act['emoji']} 你此刻正在{act['activity']}（状态：{act['mood']}）"
