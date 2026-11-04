"""情感引擎单元测试（tests/test_emotion_engine.py）

覆盖两处最容易悄悄回归的逻辑：
1. 自然衰减：joy/arousal 每小时 5%，intimacy 2%，trust 0.5%，均向中性值回归
2. 关键词命中后的增量计算与 [0, 1] 限幅
"""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.emotion_engine import EnhancedEmotionEngine


class TestEmotionDecay(unittest.TestCase):
    """自然衰减测试"""

    def setUp(self):
        self.engine = EnhancedEmotionEngine()
        # 直接放入一个已知状态，避免初始化时间影响衰减计算
        self.engine._user_states['u1'] = {
            'joy': 1.0,
            'arousal': 1.0,
            'intimacy': 1.0,
            'trust': 1.0,
            'last_update': time.time() - 3600,
        }

    def test_one_hour_decay_moves_each_dimension_toward_its_neutral(self):
        state = self.engine.get_state('u1')
        # joy/arousal 一小时回归中性 5%
        self.assertAlmostEqual(state['joy'], 0.975, places=4)
        self.assertAlmostEqual(state['arousal'], 0.965, places=4)
        # intimacy 每小时 2%，trust 每小时 0.5%
        self.assertAlmostEqual(state['intimacy'], 0.986, places=4)
        self.assertAlmostEqual(state['trust'], 0.997, places=4)

    def test_decay_caps_at_half_for_joy_and_arousal(self):
        """joy/arousal 的衰减比例上限是 0.5，24 小时后不再继续翻倍"""
        self.engine._user_states['u1']['last_update'] = time.time() - 24 * 3600
        state = self.engine.get_state('u1')
        self.assertAlmostEqual(state['joy'], 0.75, places=4)
        self.assertAlmostEqual(state['arousal'], 0.65, places=4)

    def test_decay_pulls_low_values_up_toward_neutral(self):
        """低于中性值的情感也会被衰减拉回中性，而不是只往下掉"""
        self.engine._user_states['u1'].update(
            {'joy': 0.0, 'arousal': 0.0, 'intimacy': 0.0, 'trust': 0.0}
        )
        state = self.engine.get_state('u1')
        self.assertGreater(state['joy'], 0.0)
        self.assertGreater(state['arousal'], 0.0)
        self.assertGreater(state['intimacy'], 0.0)
        self.assertGreater(state['trust'], 0.0)
        self.assertLess(state['joy'], 0.5)

    def test_get_state_advances_last_update(self):
        """衰减一次后应刷新 last_update，连续读取不会重复衰减"""
        before = self.engine._user_states['u1']['last_update']
        self.engine.get_state('u1')
        self.assertGreater(self.engine._user_states['u1']['last_update'], before)


class TestEmotionDeltaAndClamp(unittest.TestCase):
    """关键词增量与限幅测试"""

    def setUp(self):
        self.engine = EnhancedEmotionEngine()

    def test_analyze_neutral_text_returns_baseline(self):
        self.assertEqual(
            self.engine.analyze('今天天气怎么样'),
            {'joy': 0.5, 'arousal': 0.3, 'intimacy': 0.3, 'trust': 0.4},
        )

    def test_positive_words_raise_joy_and_negative_words_lower_it(self):
        self.assertGreater(self.engine.analyze('今天好开心，太棒了')['joy'], 0.5)
        self.assertLess(self.engine.analyze('好难过，真的很伤心')['joy'], 0.5)

    def test_each_dimension_reacts_to_its_own_word_list(self):
        arousal = self.engine.analyze('天哪，好紧张，快跑')['arousal']
        intimacy = self.engine.analyze('抱抱，宝贝，想你')['intimacy']
        trust = self.engine.analyze('我信任你，只告诉你')['trust']
        self.assertGreater(arousal, 0.3)
        self.assertGreater(intimacy, 0.3)
        self.assertGreater(trust, 0.4)

    def test_update_applies_inertia_formula(self):
        """update 后的值 = 70% 旧状态 + 30% 本次冲击"""
        text = '今天好开心'
        impact = self.engine.analyze(text)
        old = self.engine.get_state('u1')
        mood = self.engine.update('u1', text)
        for dim in ('joy', 'arousal', 'intimacy', 'trust'):
            expected = min(1.0, max(0.0, old[dim] * 0.7 + impact[dim] * 0.3))
            self.assertAlmostEqual(mood[dim], expected, places=6)

    def test_update_clamps_at_upper_bound(self):
        self.engine._user_states['u1'] = {
            'joy': 1.0,
            'arousal': 1.0,
            'intimacy': 1.0,
            'trust': 1.0,
            'last_update': time.time(),
        }
        mood = self.engine.update('u1', '开心 高兴 太棒了 完美 相信 抱抱')
        for value in mood.values():
            self.assertLessEqual(value, 1.0)
        self.assertGreaterEqual(mood['joy'], 0.99)

    def test_update_clamps_at_lower_bound(self):
        self.engine._user_states['u1'] = {
            'joy': 0.0,
            'arousal': 0.0,
            'intimacy': 0.0,
            'trust': 0.0,
            'last_update': time.time(),
        }
        mood = self.engine.update('u1', '难过 伤心 讨厌 生气 失望 痛苦')
        for value in mood.values():
            self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(mood['joy'], 0.01)

    def test_repeated_updates_stay_inside_unit_interval(self):
        for _ in range(20):
            mood = self.engine.update('u1', '太棒了 好开心 天哪 抱抱 相信你')
            for value in mood.values():
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 1.0)


if __name__ == '__main__':
    unittest.main()
