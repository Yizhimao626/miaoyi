#!/usr/bin/env python3
"""
  🐱 喵艺性格系统 - 完整迁移自main.py的PersonalitySystem
  包含12个性格特质+进化规则+情感响应模式
"""
import logging
import random
import threading
import time

_log = logging.getLogger(__name__)


class PersonalitySystem:
    def __init__(self, emotion_sys=None):
        self.emotion = emotion_sys
        self._traits = {
            'warmth_level': 0.7,
            'humor_chance': 0.15,
            'empathy_depth': 0.6,
            'playfulness_level': 0.5,
            'thought_depth': 0.5,
            'expressiveness': 0.6,
            'sensitivity': 0.4,
            'directness': 0.4,
            'curiosity': 0.6,
            'creativity': 0.5,
            'patience': 0.7,
            'independence': 0.4,
        }
        self._trait_history = {k: [] for k in self._traits.keys()}
        self._evolution_log = []
        self._dirty = False
        self._lock = threading.Lock()

    def get_trait_modifier(self) -> dict:
        return dict(self._traits)

    def get_trait(self, trait: str) -> float:
        return self._traits.get(trait, 0.5)

    def evolve_from_interaction(self, intent: str, user_emotion: str, response_type: str):
        with self._lock:
            intent_effects = {
                'love': {'warmth_level': 0.02, 'empathy_depth': 0.01},
                'greeting': {'warmth_level': 0.01, 'playfulness_level': 0.01},
                'complaint': {'empathy_depth': 0.02, 'patience': 0.01},
                'boredom': {'playfulness_level': 0.02, 'creativity': 0.01},
                'question': {'curiosity': 0.02, 'thought_depth': 0.01},
                'joke': {'humor_chance': 0.02, 'playfulness_level': 0.01},
                'encourage': {'warmth_level': 0.02, 'empathy_depth': 0.01},
                'comfort': {'empathy_depth': 0.03, 'patience': 0.02},
                'casual': {'playfulness_level': 0.01, 'expressiveness': 0.01},
                'farewell': {'warmth_level': 0.01},
                'goodnight': {'warmth_level': 0.01, 'patience': 0.01},
                'thanks': {'warmth_level': 0.01, 'curiosity': 0.01},
                'praise': {'humor_chance': 0.01, 'playfulness_level': 0.01},
                'anger': {'patience': 0.02, 'sensitivity': 0.01},
                'sadness': {'empathy_depth': 0.03, 'warmth_level': 0.02},
            }
            
            effects = intent_effects.get(intent, {})
            for trait, delta in effects.items():
                if trait in self._traits:
                    self._traits[trait] = max(0.0, min(1.0, self._traits[trait] + delta))
            
            user_emotion_effects = {
                'joy': {'warmth_level': 0.01, 'playfulness_level': 0.01},
                'sadness': {'empathy_depth': 0.02, 'patience': 0.01},
                'anger': {'patience': 0.02, 'directness': -0.01},
                'fear': {'warmth_level': 0.02, 'empathy_depth': 0.01},
                'love': {'warmth_level': 0.02, 'playfulness_level': 0.01},
            }
            
            for emotion, effects in user_emotion_effects.items():
                if emotion in user_emotion:
                    for trait, delta in effects.items():
                        if trait in self._traits:
                            self._traits[trait] = max(0.0, min(1.0, self._traits[trait] + delta))
            
            response_effects = {
                'humorous': {'humor_chance': 0.01},
                'empathetic': {'empathy_depth': 0.01},
                'creative': {'creativity': 0.01},
                'deep': {'thought_depth': 0.01},
                'playful': {'playfulness_level': 0.01},
            }
            
            effects = response_effects.get(response_type, {})
            for trait, delta in effects.items():
                if trait in self._traits:
                    self._traits[trait] = max(0.0, min(1.0, self._traits[trait] + delta))
            
            for trait in self._traits:
                self._trait_history[trait].append((time.time(), self._traits[trait]))
                if len(self._trait_history[trait]) > 100:
                    self._trait_history[trait].pop(0)
            
            self._evolution_log.append({
                'intent': intent,
                'user_emotion': user_emotion,
                'response_type': response_type,
                'ts': time.time(),
                'changes': {k: round(v, 3) for k, v in effects.items()},
            })
            if len(self._evolution_log) > 50:
                self._evolution_log.pop(0)
            
            self._dirty = True

    def should_be_humorous(self) -> bool:
        return random.random() < self._traits.get('humor_chance', 0.15)

    def should_be_playful(self) -> bool:
        return random.random() < self._traits.get('playfulness_level', 0.5)

    def should_be_deep(self) -> bool:
        return random.random() < self._traits.get('thought_depth', 0.5)

    def get_dominant_response_emotion(self, user_emotion: str) -> str:
        empathy = self._traits.get('empathy_depth', 0.5)
        warmth = self._traits.get('warmth_level', 0.5)
        
        if user_emotion == 'sadness':
            if empathy > 0.6:
                return 'comfort'
            return 'understanding'
        if user_emotion == 'joy':
            if warmth > 0.6:
                return 'shared_joy'
            return 'happiness'
        if user_emotion == 'anger':
            if empathy > 0.5:
                return 'understanding'
            return 'calm'
        if user_emotion == 'fear':
            if warmth > 0.6:
                return 'comfort'
            return 'support'
        if user_emotion == 'love':
            if warmth > 0.6:
                return 'affection'
            return 'warmth'
        return 'neutral'

    def get_personality_description(self) -> str:
        parts = []
        if self._traits.get('warmth_level', 0.5) > 0.6:
            parts.append("温暖体贴")
        elif self._traits.get('warmth_level', 0.5) < 0.4:
            parts.append("冷静理性")
        
        if self._traits.get('humor_chance', 0) > 0.15:
            parts.append("风趣幽默")
        
        if self._traits.get('empathy_depth', 0.5) > 0.6:
            parts.append("善解人意")
        
        if self._traits.get('playfulness_level', 0.5) > 0.6:
            parts.append("活泼可爱")
        
        if self._traits.get('thought_depth', 0.5) > 0.6:
            parts.append("思考深刻")
        
        if self._traits.get('creativity', 0.5) > 0.6:
            parts.append("富有创意")
        
        if self._traits.get('patience', 0.5) > 0.7:
            parts.append("耐心温柔")
        
        if self._traits.get('curiosity', 0.5) > 0.6:
            parts.append("充满好奇")
        
        if not parts:
            parts.append("性格平衡")
        
        return "、".join(parts)

    def get_trait_summary(self) -> dict:
        return {trait: round(val, 2) for trait, val in self._traits.items()}

    def reset(self):
        with self._lock:
            self._traits = {
                'warmth_level': 0.7,
                'humor_chance': 0.15,
                'empathy_depth': 0.6,
                'playfulness_level': 0.5,
                'thought_depth': 0.5,
                'expressiveness': 0.6,
                'sensitivity': 0.4,
                'directness': 0.4,
                'curiosity': 0.6,
                'creativity': 0.5,
                'patience': 0.7,
                'independence': 0.4,
            }
            self._trait_history = {k: [] for k in self._traits.keys()}
            self._evolution_log = []
            self._dirty = True

    def save(self):
        with self._lock:
            if self._dirty:
                from core.miaoyi_core import save_json
                save_json("miaoyi_personality.json", {
                    'traits': self._traits,
                    'evolution_log': self._evolution_log[-50:],
                })
                self._dirty = False

    def load(self):
        try:
            from core.miaoyi_core import load_json
            data = load_json("miaoyi_personality.json")
            if data:
                with self._lock:
                    self._traits.update(data.get('traits', {}))
                    self._evolution_log = data.get('evolution_log', [])
                    for k, v in self._traits.items():
                        self._traits[k] = max(0.0, min(1.0, v))
        except Exception as e:
            _log.debug(f"加载性格数据失败: {e}")