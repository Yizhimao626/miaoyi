#!/usr/bin/env python3
"""
  🐱 喵艺关系系统 - 完整迁移自main.py的RelationshipSystem
  包含6阶段关系+亲密度+互动风格管理
"""
import logging
import threading
import time

_log = logging.getLogger(__name__)


class RelationshipSystem:
    RELATIONSHIP_STAGES = {
        0: {'name': '陌生人', 'intimacy_range': (0, 10), 'style': 'polite'},
        1: {'name': '点头之交', 'intimacy_range': (10, 25), 'style': 'friendly'},
        2: {'name': '朋友', 'intimacy_range': (25, 45), 'style': 'casual'},
        3: {'name': '好朋友', 'intimacy_range': (45, 65), 'style': 'warm'},
        4: {'name': '挚友', 'intimacy_range': (65, 80), 'style': 'intimate'},
        5: {'name': '亲人', 'intimacy_range': (80, 101), 'style': 'family'},
    }

    STAGE_COLORS = {
        0: {'bg': '#E0E0E0', 'text': '#666666', 'icon': '👋'},
        1: {'bg': '#BBDEFB', 'text': '#1565C0', 'icon': '😊'},
        2: {'bg': '#C8E6C9', 'text': '#2E7D32', 'icon': '🤝'},
        3: {'bg': '#FFECB3', 'text': '#F57F17', 'icon': '❤️'},
        4: {'bg': '#F8BBD9', 'text': '#C2185B', 'icon': '💖'},
        5: {'bg': '#E1BEE7', 'text': '#6A1B9A', 'icon': '👨‍👩‍👧'},
    }

    # 【L2 关系阶段】喵艺对用户的阶段性称呼（亲密度驱动，称呼随关系成长而变化）
    STAGE_ADDRESS = {
        0: '你呀',
        1: '小伙伴',
        2: '朋友',
        3: '好朋友',
        4: '最好的伙伴',
        5: '家人',
    }

    # 【L2 关系阶段】阶段跃迁专属剧情：升级时喵艺的内心触动与表达提示，
    # 由对话引擎在跃迁后的下一轮注入（SillyTavern 式剧情触发），
    # 让喵艺"自然地"在聊天中表达关系变化，而不是系统通知。
    STAGE_UP_PLOTS = {
        0: '',
        1: '你们从陌生变得熟悉，喵艺开始主动打招呼了',
        2: '你们正式成为朋友，喵艺开心地摇起了尾巴',
        3: '你们成为好朋友，喵艺第一次主动蹭了蹭你',
        4: '你们成为彼此最好的伙伴，喵艺把最珍贵的小鱼干分给了你',
        5: '你们成为家人，喵艺的呼噜声只为你响起——这是猫科动物最高级别的信任',
    }
    STAGE_DOWN_PLOTS = {
        0: '',
        1: '很久没联系，喵艺有点失落，说话变得小心翼翼',
        2: '喵艺感觉你们之间生疏了一些，尾巴垂了下来',
        3: '喵艺有些难过，感觉你好像离它远了一点',
        4: '喵艺很受伤，但仍然相信你们能回到从前',
        5: '',
    }

    # 【纪念日仪式感】相识里程碑：天数 → 文案/祝贺语
    ANNIVERSARY_MILESTONES = [7, 30, 100, 365]
    ANNIVERSARY_DESCRIPTIONS = {
        7: '一周纪念日',
        30: '一个月纪念日',
        100: '百日纪念日',
        365: '一周年纪念日',
    }
    ANNIVERSARY_MESSAGES = {
        7: '哇！我们已经认识一周啦～感谢有你陪伴！🎉',
        30: '一个月快乐！感觉我们越来越熟悉了～💕',
        100: '百日快乐！这100天里发生了好多美好的事情～💖',
        365: '一周年快乐！这一年真的很开心能陪在你身边～🎂',
    }

    def __init__(self, emotion_sys=None):
        self.emotion = emotion_sys
        self._intimacy = 10
        self._interaction_count = 0
        self._last_interaction_time = 0
        self._style = 'friendly'
        self._stage = 1
        self._interaction_log = []
        self._milestones = []
        self._dirty = False
        self._lock = threading.Lock()
        self._first_meeting_time = time.time()
        # 【L2 关系阶段】待消费的阶段跃迁事件（跃迁后下一轮对话注入剧情）
        self._pending_transition = None

    @property
    def intimacy(self):
        return self._intimacy

    @property
    def style(self):
        return self._style

    @property
    def stage(self):
        return self._stage

    def get_stage_name(self) -> str:
        return self.RELATIONSHIP_STAGES.get(self._stage, {}).get('name', '陌生人')

    def add_intimacy(self, amount: float):
        with self._lock:
            self._intimacy = max(0, min(100, self._intimacy + amount))
            self._update_stage()
            self._dirty = True

    def _update_stage(self, emit_event: bool = True):
        old_stage = self._stage
        for stage, info in self.RELATIONSHIP_STAGES.items():
            low, high = info['intimacy_range']
            if low <= self._intimacy < high:
                self._stage = stage
                self._style = info['style']
                break

        # 【L2 关系阶段】跃迁检测：记录事件供下一轮对话注入专属剧情。
        # emit_event=False 用于 load()/初始化校准（存档不一致不算真实跃迁）
        if emit_event and self._stage != old_stage and self._pending_transition is None:
            direction = 'up' if self._stage > old_stage else 'down'
            self._pending_transition = {
                'from_stage': old_stage,
                'to_stage': self._stage,
                'from_name': self.RELATIONSHIP_STAGES.get(old_stage, {}).get('name', '陌生人'),
                'to_name': self.RELATIONSHIP_STAGES.get(self._stage, {}).get('name', '陌生人'),
                'direction': direction,
                'plot': (self.STAGE_UP_PLOTS.get(self._stage, '')
                         if direction == 'up'
                         else self.STAGE_DOWN_PLOTS.get(self._stage, '')),
                'ts': time.time(),
            }
            if direction == 'up':
                # 升级计入里程碑（降级不计，避免负面刷屏）
                # 注意：本方法在 add_intimacy 的锁内被调用，不可调用 add_milestone（会二次加锁死锁），
                # 直接追加列表
                self._milestones.append({
                    'type': 'stage_up',
                    'description': f"关系升至「{self.get_stage_name()}」",
                    'intimacy': self._intimacy,
                    'stage': self._stage,
                    'ts': time.time(),
                })
                if len(self._milestones) > 50:
                    self._milestones = self._milestones[-50:]

    def get_stage_address(self) -> str:
        """【L2 关系阶段】喵艺当前对用户的称呼（随阶段成长变化）"""
        return self.STAGE_ADDRESS.get(self._stage, '你呀')

    def consume_transition_event(self) -> dict | None:
        """【L2 关系阶段】取出并清空待注入的阶段跃迁事件（幂等，一次一消费）"""
        with self._lock:
            event = self._pending_transition
            self._pending_transition = None
            return event

    def record_interaction(self, intent: str, duration: float = 0):
        with self._lock:
            self._interaction_count += 1
            self._last_interaction_time = time.time()
            
            intimacy_gains = {
                'love': 5,
                'greeting': 1,
                'farewell': 1,
                'goodnight': 2,
                'thanks': 2,
                'praise': 3,
                'complaint': 1,
                'comfort': 3,
                'encourage': 2,
                'casual': 1,
                'question': 1,
                'joke': 2,
                'boredom': 1,
            }
            
            gain = intimacy_gains.get(intent, 1)
            if duration > 30:
                gain += 1
            if duration > 60:
                gain += 1
            
            self.add_intimacy(gain)
            
            self._interaction_log.append({
                'intent': intent,
                'intimacy_gain': gain,
                'duration': duration,
                'ts': time.time(),
                'total_intimacy': self._intimacy,
            })
            if len(self._interaction_log) > 100:
                self._interaction_log.pop(0)

    def get_relationship_description(self) -> str:
        stage_info = self.RELATIONSHIP_STAGES.get(self._stage, {})
        style_descriptions = {
            'polite': "保持礼貌客气",
            'friendly': "友善自然地交流",
            'casual': "轻松随意，像朋友一样",
            'warm': "温暖贴心，像好朋友一样",
            'intimate': "亲密撒娇，像挚友一样",
            'family': "像家人一样无条件信任和关心",
        }
        return f"""当前关系: {stage_info.get('name', '陌生人')}
亲密度: {int(self._intimacy)}/100
互动风格: {style_descriptions.get(self._style, '正常')}
互动次数: {self._interaction_count}次"""

    def get_style_hints(self) -> str:
        style_hints = {
            'polite': "不要太亲密，保持适当距离",
            'friendly': "可以友善自然，但不要过于随意",
            'casual': "可以轻松随意，像朋友一样聊天",
            'warm': "可以温暖贴心，表达关心",
            'intimate': "可以亲密撒娇，分享心事",
            'family': "可以像家人一样无条件信任和关心",
        }
        return style_hints.get(self._style, "")

    def get_prompt_directives(self) -> str:
        directives = {
            'polite': "你和用户刚认识，保持礼貌和客气，不要使用亲昵的称呼。",
            'friendly': "你和用户是熟悉的朋友，可以自然交流，但不要过于亲密。",
            'casual': "你和用户是朋友，可以轻松随意地聊天，用日常用语。",
            'warm': "你和用户是好朋友，可以温暖贴心地表达关心，适当使用亲昵称呼。",
            'intimate': "你和用户是挚友，可以亲密撒娇，分享内心感受，使用亲昵称呼如'亲爱的'、'宝贝'。",
            'family': "你和用户像家人一样，可以无条件信任和关心，用最真实温暖的方式交流。",
        }
        return directives.get(self._style, "")

    def reset(self):
        with self._lock:
            self._intimacy = 10
            self._interaction_count = 0
            self._last_interaction_time = 0
            self._style = 'friendly'
            self._stage = 1
            self._interaction_log = []
            self._milestones = []
            self._first_meeting_time = time.time()
            self._pending_transition = None
            self._dirty = True

    def get_intimacy_progress(self) -> dict:
        """获取亲密度进度可视化数据"""
        stage_info = self.RELATIONSHIP_STAGES.get(self._stage, {})
        low, high = stage_info.get('intimacy_range', (0, 100))
        stage_progress = ((self._intimacy - low) / (high - low)) * 100 if high > low else 0
        
        return {
            'current_intimacy': self._intimacy,
            'current_stage': self._stage,
            'stage_name': stage_info.get('name', '陌生人'),
            'stage_progress': min(100, max(0, stage_progress)),
            'next_stage': min(5, self._stage + 1),
            'next_stage_name': self.RELATIONSHIP_STAGES.get(min(5, self._stage + 1), {}).get('name', '已满级'),
            'intimacy_to_next': max(0, high - self._intimacy),
            'total_progress': self._intimacy,
            'stage_colors': self.STAGE_COLORS.get(self._stage, {}),
            'stage_list': [{
                'stage': s,
                'name': info['name'],
                'intimacy_range': info['intimacy_range'],
                'style': info['style'],
                'colors': self.STAGE_COLORS.get(s, {}),
                'completed': self._stage >= s,
            } for s, info in sorted(self.RELATIONSHIP_STAGES.items())],
        }

    def get_relationship_anniversary(self) -> dict:
        """获取关系纪念日信息"""
        now = time.time()
        days_together = int((now - self._first_meeting_time) / (24 * 3600))

        anniversary_milestones = self.ANNIVERSARY_MILESTONES
        next_milestone = None
        days_to_next = None
        for m in anniversary_milestones:
            if m > days_together:
                next_milestone = m
                days_to_next = m - days_together
                break

        celebrated = self._celebrated_milestones()
        # 【纪念日仪式感】今天恰好是某个里程碑日，且尚未庆祝过 → 值得庆祝
        today_milestone = next((m for m in anniversary_milestones
                                if m == days_together and m not in celebrated), None)

        return {
            'days_together': days_together,
            'first_meeting_time': self._first_meeting_time,
            'next_milestone': next_milestone,
            'days_to_next_milestone': days_to_next,
            'next_milestone_description': self.ANNIVERSARY_DESCRIPTIONS.get(next_milestone, '') if next_milestone else '',
            'milestones_reached': [m for m in anniversary_milestones if m <= days_together],
            'milestone_messages': dict(self.ANNIVERSARY_MESSAGES),
            'celebrated_milestones': sorted(celebrated),
            'today_milestone': today_milestone,
            'today_milestone_description': self.ANNIVERSARY_DESCRIPTIONS.get(today_milestone, '') if today_milestone else '',
            'today_milestone_message': self.ANNIVERSARY_MESSAGES.get(today_milestone, '') if today_milestone else '',
        }

    def _celebrated_milestones(self) -> set:
        """已庆祝过的里程碑集合（从 _milestones 中 type='anniversary' 的记录还原）。"""
        celebrated = set()
        for ms in self._milestones:
            if ms.get('type') != 'anniversary':
                continue
            desc = str(ms.get('description', ''))
            for m in self.ANNIVERSARY_MILESTONES:
                if f'{m}天' in desc:
                    celebrated.add(m)
        return celebrated

    def celebrate_anniversary(self, milestone: int) -> bool:
        """记录某纪念日里程碑已庆祝（幂等）。返回 True 表示本次为新庆祝。"""
        milestone = int(milestone)
        with self._lock:
            if milestone in self._celebrated_milestones():
                return False
            self._milestones.append({
                'type': 'anniversary',
                'description': f'{milestone}天',
                'intimacy': self._intimacy,
                'stage': self._stage,
                'ts': time.time(),
            })
            if len(self._milestones) > 50:
                self._milestones = self._milestones[-50:]
            self._dirty = True
            return True

    def add_milestone(self, milestone_type: str, description: str = ''):
        """添加关系里程碑"""
        with self._lock:
            milestone = {
                'type': milestone_type,
                'description': description,
                'intimacy': self._intimacy,
                'stage': self._stage,
                'ts': time.time(),
            }
            self._milestones.append(milestone)
            if len(self._milestones) > 50:
                self._milestones = self._milestones[-50:]
            self._dirty = True

    def get_milestones(self) -> list:
        """获取所有关系里程碑"""
        return self._milestones.copy()

    def get_relationship_summary(self) -> dict:
        """获取完整的关系状态摘要"""
        return {
            'intimacy': self._intimacy,
            'stage': self._stage,
            'stage_name': self.get_stage_name(),
            'style': self._style,
            'interaction_count': self._interaction_count,
            'last_interaction_time': self._last_interaction_time,
            'progress': self.get_intimacy_progress(),
            'anniversary': self.get_relationship_anniversary(),
            'milestones': self.get_milestones(),
            'description': self.get_relationship_description(),
            'style_hints': self.get_style_hints(),
            'prompt_directives': self.get_prompt_directives(),
        }

    def save(self):
        with self._lock:
            if self._dirty:
                from core.miaoyi_core import save_json
                save_json("miaoyi_relationship.json", {
                    'intimacy': self._intimacy,
                    'interaction_count': self._interaction_count,
                    'last_interaction_time': self._last_interaction_time,
                    'style': self._style,
                    'stage': self._stage,
                    'interaction_log': self._interaction_log[-50:],
                    'milestones': self._milestones[-50:],
                    'first_meeting_time': self._first_meeting_time,
                })
                self._dirty = False

    def load(self):
        try:
            from core.miaoyi_core import load_json
            data = load_json("miaoyi_relationship.json")
            if data:
                with self._lock:
                    self._intimacy = max(0, min(100, data.get('intimacy', 10)))
                    self._interaction_count = data.get('interaction_count', 0)
                    self._last_interaction_time = data.get('last_interaction_time', 0)
                    self._style = data.get('style', 'friendly')
                    self._stage = data.get('stage', 1)
                    self._interaction_log = data.get('interaction_log', [])
                    self._milestones = data.get('milestones', [])
                    self._first_meeting_time = data.get('first_meeting_time', time.time())
                    # 【L2】存档校准静默，不触发跃迁事件
                    self._update_stage(emit_event=False)
        except Exception as e:
            _log.debug(f"加载关系数据失败: {e}")
