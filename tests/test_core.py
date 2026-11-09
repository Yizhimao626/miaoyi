"""
🐱 喵艺 AI 核心单元测试

测试范围:
1. 认证模块 - Token 生成、密码哈希
2. 数据库操作 - 记忆存储、消息管理
3. 意图识别 - 关键意图路由
4. 响应格式 - 规范检查

运行:
    python -m pytest tests/test_core.py -v
    或
    python tests/test_core.py
"""

import os
import shutil
import sys
import tempfile
import time
import unittest

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 1. 认证模块测试
# ============================================================

class TestAuth(unittest.TestCase):
    """测试认证功能"""
    
    def setUp(self):
        """测试前准备"""
        # 设置测试用的 JWT 密钥（至少 32 字符）
        import os
        os.environ['JWT_SECRET_KEY'] = 'test-secret-key-for-unit-testing-2024'
        
        import core.auth as auth
        # 在测试中初始化安全配置
        auth.validate_security_config()
        self.auth = auth
        
    def test_create_access_token(self):
        """测试 Access Token 生成"""
        token = self.auth.create_access_token(
            user_id=1, 
            username='testuser', 
            role='user'
        )
        
        # Token 不为空
        self.assertTrue(token, "Token 不能为空")
        
        # 获取真实密钥进行解码
        secret_key = self.auth.get_jwt_secret_key()
        
        # 解码验证
        payload = self.auth.jwt.decode(
            token, 
            secret_key, 
            algorithms=[self.auth.JWT_ALGORITHM]
        )
        
        self.assertEqual(payload['user_id'], 1)
        self.assertEqual(payload['username'], 'testuser')
        self.assertEqual(payload['role'], 'user')
        self.assertEqual(payload['token_type'], 'access')
        
    def test_create_refresh_token(self):
        """测试 Refresh Token 生成"""
        token = self.auth.create_refresh_token(
            user_id=1, 
            username='testuser'
        )
        
        self.assertTrue(token, "Refresh Token 不能为空")
        
        # 获取真实密钥进行解码
        secret_key = self.auth.get_jwt_secret_key()
        
        payload = self.auth.jwt.decode(
            token, 
            secret_key, 
            algorithms=[self.auth.JWT_ALGORITHM]
        )
        
        self.assertEqual(payload['user_id'], 1)
        self.assertEqual(payload['username'], 'testuser')
        self.assertEqual(payload['token_type'], 'refresh')
        
    def test_token_invalid(self):
        """测试无效 Token 应该解码失败"""
        # 获取真实密钥
        secret_key = self.auth.get_jwt_secret_key()
        
        # 无效 Token 应该抛出异常
        with self.assertRaises(Exception):
            self.auth.jwt.decode(
                'invalid_token_string',
                secret_key,
                algorithms=[self.auth.JWT_ALGORITHM]
            )
        
    def test_password_hashing(self):
        """测试密码哈希"""
        password = 'Test123!Secure'
        
        # 哈希
        hashed = self.auth.hash_password(password)
        self.assertTrue(hashed, "密码哈希不为空")
        
        # 验证
        self.assertTrue(
            self.auth.verify_password(password, hashed),
            "密码验证应返回 True"
        )
        
        # 错误密码
        self.assertFalse(
            self.auth.verify_password('WrongPassword', hashed),
            "错误密码应返回 False"
        )


# ============================================================
# 2. 数据库操作测试
# ============================================================

class TestDatabase(unittest.TestCase):
    """测试数据库功能"""
    
    @classmethod
    def setUpClass(cls):
        """测试类前准备"""
        cls.test_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.test_dir, 'test_miaoyi.db')
        
        # 导入 DBManager（显式 SQLite 隔离，避免误连 config.yaml 的 MySQL）
        from db_manager import DBManager
        cls.db = DBManager(db_path=cls.db_path, db_config={'type': 'sqlite'})
        cls.session_id = f"test_session_{int(time.time())}"
        
    @classmethod
    def tearDownClass(cls):
        """测试类后清理"""
        cls.db.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)
        
    def test_user_operations(self):
        """测试用户操作"""
        username = f'test_db_{int(time.time()) % 10000}'
        password_hash = 'hashed_password_123'
        
        # 创建用户
        result = self.db.create_user(
            username=username,
            password_hash=password_hash,
            phone='13800138000',
            email='test@example.com',
            role='user'
        )
        self.assertTrue(result, "创建用户应返回 True")
        
        # 查询用户
        user = self.db.get_user_by_username(username)
        self.assertIsNotNone(user, "查询用户应返回结果")
        self.assertEqual(user['username'], username)
        
    def test_message_operations(self):
        """测试消息操作"""
        session_id = self.session_id
        
        # 发送消息
        self.db.add_message(
            session_id=session_id,
            role='user',
            text='你好喵艺'
        )
        
        self.db.add_message(
            session_id=session_id,
            role='assistant',
            text='你好呀~',
            emotion='happy',
            confidence=0.9,
            response_time_ms=500
        )
        
        # 查询对话历史
        messages = self.db.get_conversation_messages(session_id, limit=10)
        self.assertGreater(len(messages), 0, "应能查询到消息")
        
        # 验证内容
        user_msgs = [m for m in messages if m['role'] == 'user']
        if user_msgs:
            self.assertIn('你好喵艺', str(user_msgs[0].get('text', '')))
            
        assistant_msgs = [m for m in messages if m['role'] == 'assistant']
        if assistant_msgs:
            self.assertIn('你好呀', str(assistant_msgs[0].get('text', '')))
            
    def test_memory_operations(self):
        """测试记忆操作"""
        # 保存语义事实
        self.db.save_semantic_fact(
            key='user_pet_preference',
            fact={
                'value': '猫咪',
                'category': 'fact',
                'confidence': 0.95,
                'source': 'memory_system',
                'importance': 0.8
            }
        )
        
        # 查询记忆
        facts = self.db.get_memories(limit=20, category='fact')
        self.assertGreater(len(facts), 0, "应有记忆数据")
        
        # 验证数据
        cat_facts = [f for f in facts if 'pet_preference' in str(f.get('key', ''))]
        if cat_facts:
            self.assertIn('猫咪', str(cat_facts[0].get('value', '')))
            
    def test_conversation_management(self):
        """测试会话管理"""
        user_id = f'test_user_{int(time.time())}'
        
        # 创建会话
        conv_id = self.db.create_conversation(user_id)
        self.assertIsNotNone(conv_id, "创建会话应返回 ID")
        
        # 查询会话列表
        convs = self.db.get_conversations(user_id)
        self.assertGreater(len(convs), 0, "应有会话数据")


# ============================================================
# 3. 意图识别测试
# ============================================================

class TestIntentRecognition(unittest.TestCase):
    """测试意图识别功能"""
    
    def test_identity_intent(self):
        """测试身份问题路由 - 必须返回预设回复"""
        identity_questions = [
            '你是谁',
            '你是什么',
            '你是AI吗',
            '你叫什么名字',
            '你是机器人吗',
        ]
        
        for question in identity_questions:
            intent = self._classify_intent(question)
            self.assertEqual(
                intent, 
                'identity', 
                f"问题'{question}'应被识别为 identity 意图"
            )
            
    def test_emotion_intent(self):
        """测试情感陪伴路由"""
        emotion_questions = [
            '今天心情不好',
            '我有点难过',
            '很孤独',
            '感觉很开心',
        ]
        
        for question in emotion_questions:
            intent = self._classify_intent(question)
            self.assertIn(
                intent, 
                ['emotion', 'chat'], 
                f"问题'{question}'应被识别为 emotion/chat 意图"
            )
            
    def test_knowledge_intent(self):
        """测试知识问答路由"""
        knowledge_questions = [
            '什么是量子力学',
            '解释一下相对论',
            'Python 怎么学习',
        ]
        
        for question in knowledge_questions:
            intent = self._classify_intent(question)
            self.assertIn(
                intent, 
                ['knowledge', 'search'], 
                f"问题'{question}'应被识别为 knowledge/search 意图"
            )
            
    def _classify_intent(self, text: str) -> str:
        """模拟意图分类（简化版）"""
        identity_keywords = ['你是谁', '你是什么', '你是AI', '你叫什么', '你是机器人', '介绍一下你']
        emotion_keywords = ['难过', '伤心', '孤独', '开心', '高兴', '无聊', '寂寞', '烦恼']
        knowledge_keywords = ['什么是', '解释', '怎么', '为什么', '如何', '介绍', '原理', '教程']
        
        for kw in identity_keywords:
            if kw in text:
                return 'identity'
                
        for kw in emotion_keywords:
            if kw in text:
                return 'emotion'
                
        for kw in knowledge_keywords:
            if kw in text:
                return 'knowledge'
                
        return 'chat'


# ============================================================
# 4. 响应格式测试
# ============================================================

class TestResponseFormat(unittest.TestCase):
    """测试响应格式规范"""
    
    def test_response_structure(self):
        """测试响应数据结构"""
        response = {
            'code': 200,
            'message': '喵～回复内容',
            'data': {
                'text': '你好呀～',
                'sticker': '😺',
                'thought': None,
                'proactive': False,
                'voice_text': '你好呀',
                'emotional_state': {
                    'valence': 0.5,
                    'arousal': 0.3
                }
            }
        }
        
        self.assertIn('code', response)
        self.assertIn('message', response)
        self.assertIn('data', response)
        self.assertIn('text', response['data'])
        
    def test_error_response_format(self):
        """测试错误响应格式"""
        error_response = {
            'code': 401,
            'message': '喵～登录已失效，请重新登录喵~',
            'data': None
        }
        
        self.assertNotEqual(error_response['code'], 200)
        self.assertIsNone(error_response['data'])
        self.assertTrue(len(error_response['message']) > 0)
        
    def test_forbidden_phrases_detection(self):
        """测试禁止语句检测"""
        forbidden_phrases = [
            '作为一个AI模型',
            '我没有情感',
            '我无法处理',
            'I am an AI',
            'I do not have emotions',
        ]
        
        # 包含禁止语句的回复应该被标记
        bad_replies = [
            '作为一个AI模型，我无法回答这个问题',
            '我没有情感，只是代码',
        ]
        
        for reply in bad_replies:
            is_forbidden = any(phrase in reply for phrase in forbidden_phrases)
            self.assertTrue(is_forbidden, f"回复'{reply[:30]}...'应该包含禁止语句")
        
        # 正常回复不应该被标记
        good_replies = [
            '喵～这个问题我不太懂，能再说说吗？',
            '本喵现在有点懵呢~',
            '你好呀，今天过得怎么样？',
        ]
        
        for reply in good_replies:
            is_forbidden = any(phrase in reply for phrase in forbidden_phrases)
            self.assertFalse(is_forbidden, f"回复'{reply[:30]}...'不应该包含禁止语句")


# ============================================================
# 5. 情感状态测试
# ============================================================

class TestEmotionalState(unittest.TestCase):
    """测试情感状态功能"""
    
    def test_emotion_vector_range(self):
        """测试情感向量范围"""
        # valence (愉悦度): -1 到 1
        # arousal (唤醒度): 0 到 1
        # intimacy (亲密度): 0 到 1
        # trust (信任度): 0 到 1
        
        test_vectors = [
            {'valence': 0.8, 'arousal': 0.6, 'intimacy': 0.5, 'trust': 0.7},
            {'valence': -0.3, 'arousal': 0.2, 'intimacy': 0.3, 'trust': 0.5},
        ]
        
        for vec in test_vectors:
            # valence 范围
            self.assertGreaterEqual(vec['valence'], -1.0)
            self.assertLessEqual(vec['valence'], 1.0)
            # arousal, intimacy, trust 范围
            for key in ['arousal', 'intimacy', 'trust']:
                self.assertGreaterEqual(vec[key], 0.0)
                self.assertLessEqual(vec[key], 1.0)
                
    def test_emotion_decay(self):
        """测试情感衰减（模拟）"""
        initial_valence = 0.9
        decay_rate = 0.1
        
        for i in range(3):
            initial_valence = initial_valence * (1 - decay_rate)
            
        self.assertLess(initial_valence, 0.9, "情感应随时间衰减")
        self.assertGreater(initial_valence, 0, "情感不应完全消失")


# ============================================================
# 运行测试
# ============================================================

if __name__ == '__main__':
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestSuite()
    
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestAuth))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestDatabase))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestIntentRecognition))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestResponseFormat))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestEmotionalState))
    
    result = runner.run(suite)
    
    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print("🎉 所有单元测试通过！")
    else:
        print(f"❌ 有 {len(result.failures) + len(result.errors)} 个测试失败")
    print("=" * 60)
    
    sys.exit(0 if result.wasSuccessful() else 1)

# ============================================================
# 6. 人性化提示词测试（时段感知问候 / 情绪表达密度 / 重逢上下文消费）
# ============================================================

class TestHumanizationPrompt(unittest.TestCase):
    """验证 _build_system_prompt 的人性化升级（桩对象，不走 LLM/DB）"""

    def _make_core(self, emotion_vector=None, holiday=None, hour=None):
        import time as _time
        import types

        from core.miaoyi_core import MiaoyiCore
        from core.time_awareness import EnhancedTimeAwareness

        c = MiaoyiCore.__new__(MiaoyiCore)  # 跳过重量级 __init__
        c._config = {}  # 【L3】_select_model 需读取 llm.model_light / llm.model_name
        vec = emotion_vector or {}
        c._emotional_core = types.SimpleNamespace(
            description=lambda: "测试心情", intimacy=50,
            get_emotional_vector=lambda: vec,
            dominant=lambda: (max(vec.items(), key=lambda x: x[1])[0] if vec else 'neutral'),
            emotion_insight='',
        )
        c.relationship = types.SimpleNamespace(get_stage_name=lambda: "朋友")
        c.memory = types.SimpleNamespace(
            get_relevant_memories=lambda *a, **k: [],
            get_context_str=lambda *a, **k: "", get_user_profile=lambda *a, **k: "",
            get_memory_context_bundle=lambda *a, **k: ("", "", {}),
        )
        c._personality_presets = {'default': {'name': '默认', 'emoji': '🐱', 'persona': '温暖', 'tone': '温柔', 'speech_style': '喵～'}}
        c._personality_type = 'default'
        ta = EnhancedTimeAwareness()
        ta.update()
        if hour is not None:
            t = _time.strptime(f"2026-08-22 {hour:02d}:00", "%Y-%m-%d %H:%M")
            ta._current_period = ta._get_period(t)
            ta.update = lambda: None  # 冻结刷新，保留手工时段
        if holiday:
            ta.update = lambda: None
            ta._holiday_today = {'name': holiday, 'type': 'national'}
        c.time_ctx = ta
        c._last_reply_style = None
        c._sensory_language = None
        c._autobiography = None
        return c

    def test_time_period_directive_night(self):
        """深夜档：中文时段名 + 温柔/休息关心指令进入提示词"""
        p = self._make_core(hour=23)._build_system_prompt()
        self.assertIn("【时段感知】", p)
        self.assertIn("，深夜）", p)  # 当前状态行为中文时段名
        self.assertIn("熬夜", p)      # 深夜关心休息的指引

    def test_time_period_directive_dawn(self):
        """清晨档：自然早安开场指引"""
        p = self._make_core(hour=6)._build_system_prompt()
        self.assertIn("早安", p)

    def test_holiday_note(self):
        """节日提示注入且拒绝广播式祝福"""
        p = self._make_core(hour=10, holiday="元旦")._build_system_prompt()
        self.assertIn("元旦", p)
        self.assertIn("广播", p)

    def test_expression_density_by_emotion(self):
        """情绪驱动表达密度：高涨/低落/复合/中性四档"""
        self.assertIn("情绪高涨", self._make_core(emotion_vector={'joy': 0.9})._build_system_prompt())
        self.assertIn("低落情绪", self._make_core(emotion_vector={'sadness': 0.8})._build_system_prompt())
        # 复合情绪走子串匹配
        self.assertIn("情绪高涨", self._make_core(emotion_vector={'joyful_and_loving': 0.9})._build_system_prompt())
        self.assertIn("日常情绪", self._make_core(emotion_vector={})._build_system_prompt())

    def test_injected_context_reaches_prompt(self):
        """修复验证：外部 context（重逢感知提示）真正进入主提示词"""
        reunion = "[系统提示] 用户已经 3 天没来了（这是回来的第一条消息）。"
        c = self._make_core(hour=21)
        p = c._build_system_prompt(injected_context=reunion)
        self.assertIn("【本次对话特别提示】", p)
        self.assertIn("3 天没来", p)
        # 无 context 时不出现该块
        self.assertNotIn("【本次对话特别提示】", c._build_system_prompt())

    def test_injected_context_reaches_emotional_path(self):
        """情感对话路径同样消费 context（捕获 prompt，不调 LLM）"""
        reunion = "[系统提示] 用户已经 3 天没来了。"
        c = self._make_core(hour=21)
        c._history = [{'role': 'user', 'text': '在吗', 'ts': 0}]
        captured = {}

        def fake_llm(messages=None, **kw):
            captured['prompt'] = messages[0]['content']
            return "OK"

        c._safe_llm_call = fake_llm
        r = c._generate_emotional_response("好久不见", "greeting", "neutral", context=reunion)
        self.assertEqual(r, "OK")
        self.assertIn("【本次对话特别提示】", captured['prompt'])

    def test_time_awareness_unit(self):
        """time_awareness 纯逻辑：时段边界 + 中文名 + 节日提示"""
        import time as _time

        from core.time_awareness import EnhancedTimeAwareness
        ta = EnhancedTimeAwareness()
        cases = [(6, 'dawn', '清晨'), (10, 'morning', '上午'), (13, 'noon', '中午'),
                 (16, 'afternoon', '下午'), (20, 'evening', '傍晚'), (23, 'night', '深夜'), (2, 'night', '深夜')]
        for hour, exp_period, exp_cn in cases:
            t = _time.strptime(f"2026-08-22 {hour:02d}:00", "%Y-%m-%d %H:%M")
            self.assertEqual(ta._get_period(t), exp_period)
            self.assertEqual(ta.PERIOD_CN[exp_period], exp_cn)
        ta.update = lambda: None
        ta._holiday_today = {'name': '元旦', 'type': 'national'}
        self.assertIn('元旦', ta.get_holiday_note())
        ta._holiday_today = None
        self.assertEqual(ta.get_holiday_note(), '')

# ============================================================
# 7. 情绪化主动关心测试（决策矩阵 + 时区回归 + 低点记录）
# ============================================================

class TestProactiveCare(unittest.TestCase):
    """情绪化主动关心：用户情绪低点后沉默 → 延时温柔回访"""

    FAKE_EPOCH = 1_800_000_000.0

    def setUp(self):
        import time as _t
        from datetime import datetime as _dt

        import enhance.core.proactive_engine as pe
        self._pe = pe
        self._real_dt = pe.datetime
        self._real_random = pe.random
        self._real_time_time = _t.time
        self._time_mod = _t
        fake_epoch = self.FAKE_EPOCH

        class FakeDT(_dt):
            @classmethod
            def utcnow(cls):
                return _dt.utcfromtimestamp(fake_epoch)

        pe.datetime = FakeDT
        import types as _types
        pe.random = _types.SimpleNamespace(random=lambda: 0.99)  # 屏蔽随机分支
        _t.time = lambda: fake_epoch

    def tearDown(self):
        self._pe.datetime = self._real_dt
        self._pe.random = self._real_random
        self._time_mod.time = self._real_time_time

    def _cfg(self, **kw):
        from enhance.core.proactive_engine import ProactiveConfig
        base = dict(miss_you_prob=0.0, call_prob=0.0)
        base.update(kw)
        return ProactiveConfig(**base)

    def _ctx(self, last_active_min_ago, distress=None, last_bot_min_ago=60):
        from datetime import datetime as _dt

        from enhance.core.proactive_engine import UserCtx
        return UserCtx(
            user_id="1", intimacy=60, relation_days=30,
            last_active_at=_dt.utcfromtimestamp(self.FAKE_EPOCH - last_active_min_ago * 60),
            last_bot_msg_at=_dt.utcfromtimestamp(self.FAKE_EPOCH - last_bot_min_ago * 60),
            user_distress=distress,
        )

    def _distress(self, min_ago, emotion='sadness', topic='最近工作压力好大', followed_up=False):
        return {'user_id': '1', 'emotion': emotion, 'topic': topic,
                'ts': self.FAKE_EPOCH - min_ago * 60, 'followed_up': followed_up}

    def _decide(self, ctx):
        from enhance.core.proactive_engine import decide_scenario
        return decide_scenario(ctx, self._cfg())

    def test_care_fires_in_window(self):
        """低点 30 分钟 + 持续沉默 → emotional_care"""
        self.assertEqual(self._decide(self._ctx(30, self._distress(30))), "emotional_care")

    def test_care_too_early(self):
        """10 分钟（< 20 分钟窗口下限）→ 不关心"""
        self.assertNotEqual(self._decide(self._ctx(10, self._distress(10))), "emotional_care")

    def test_care_too_late_falls_back(self):
        """7 小时（> 6 小时窗口）→ 不翻旧账，走正常催促"""
        s = self._decide(self._ctx(420, self._distress(420)))
        self.assertNotEqual(s, "emotional_care")
        self.assertEqual(s, "no_reply_long")

    def test_care_no_repeat(self):
        """已回访 → 不重复关心"""
        self.assertNotEqual(
            self._decide(self._ctx(30, self._distress(30, followed_up=True))),
            "emotional_care")

    def test_care_skipped_if_user_returned(self):
        """低点之后用户又活跃（5 分钟前）→ 不关心旧情绪"""
        self.assertNotEqual(self._decide(self._ctx(5, self._distress(40))), "emotional_care")

    def test_care_priority_over_nag(self):
        """关心优先于傲娇催促（60 分钟沉默+低点）"""
        self.assertEqual(self._decide(self._ctx(60, self._distress(60))), "emotional_care")

    def test_timezone_regression_no_false_nag(self):
        """【时区回归】沉默 10 分钟绝不能被误判（旧 bug 恒虚高 8 小时 → 必中 no_reply_long）"""
        self.assertIsNone(self._decide(self._ctx(10)))

    def test_timezone_regression_late_night_works(self):
        """【时区回归】深夜催睡恢复可用（本地 23:30，30 分钟前活跃）"""
        import calendar
        from datetime import datetime as _dt
        late_epoch = float(calendar.timegm(_dt(2026, 8, 22, 15, 30).timetuple()))  # UTC → 本地 23:30
        fake_epoch = late_epoch

        class FakeDT(_dt):
            @classmethod
            def utcnow(cls):
                return _dt.utcfromtimestamp(fake_epoch)

        self._pe.datetime = FakeDT
        self._time_mod.time = lambda: fake_epoch
        from enhance.core.proactive_engine import UserCtx, decide_scenario
        c = UserCtx(user_id="1", intimacy=60, relation_days=30,
                    last_active_at=_dt.utcfromtimestamp(late_epoch - 30 * 60),
                    last_bot_msg_at=_dt.utcfromtimestamp(late_epoch - 60 * 60))
        self.assertEqual(decide_scenario(c, self._cfg()), "late_night")

    def test_distress_recording_lifecycle(self):
        """miaoyi_core 低点记录：记录/回访标记/情绪转好撤销/过期清理/中性不记录"""
        from core.miaoyi_core import MiaoyiCore
        c = MiaoyiCore.__new__(MiaoyiCore)
        c._record_user_emotion_moment("1", "sadness", "今天真的好难过")
        d = c.get_user_distress()
        self.assertTrue(d and d['emotion'] == 'sadness' and '难过' in d['topic'])
        c.mark_distress_followed_up()
        self.assertIsNone(c.get_user_distress())
        c._record_user_emotion_moment("1", "sadness", "又难过了")
        c._record_user_emotion_moment("1", "joy", "哈哈没事了")
        self.assertIsNone(c.get_user_distress())
        c._record_user_emotion_moment("1", "fear", "好担心面试")
        c._user_distress['ts'] -= 7 * 3600
        self.assertIsNone(c.get_user_distress())
        c._record_user_emotion_moment("1", "neutral", "在干嘛")
        self.assertIsNone(c.get_user_distress())

    def test_persona_scenario_registered(self):
        """persona 场景文案已注册"""
        from enhance.core.persona import PROACTIVE_SCENARIOS
        self.assertIn("emotional_care", PROACTIVE_SCENARIOS)


class TestAnniversaryRitual(unittest.TestCase):
    """纪念日仪式感：里程碑日主动庆祝 + 已庆祝幂等 + 7天标签回归"""

    FAKE_EPOCH = 1_800_000_000.0

    def setUp(self):
        import time as _t
        self._time_mod = _t
        self._real_time_time = _t.time
        _t.time = lambda: self.FAKE_EPOCH

    def tearDown(self):
        self._time_mod.time = self._real_time_time

    def _rs(self, days_ago, plus_seconds=3600):
        from core.relationship import RelationshipSystem
        rs = RelationshipSystem()
        rs._first_meeting_time = time.time() - days_ago * 86400 - plus_seconds
        return rs

    def test_milestone_day_detection_and_label_fix(self):
        """相识第7天 → today_milestone=7，且文案修正为'一周纪念日'（原误为'一周年'）"""
        a = self._rs(7).get_relationship_anniversary()
        self.assertEqual(a["today_milestone"], 7)
        self.assertEqual(a["today_milestone_description"], "一周纪念日")
        self.assertEqual(a["days_together"], 7)

    def test_celebrate_idempotent(self):
        """庆祝标记幂等：首次 True、重复 False、today_milestone 消失"""
        rs = self._rs(30)
        self.assertTrue(rs.celebrate_anniversary(30))
        self.assertFalse(rs.celebrate_anniversary(30))
        a = rs.get_relationship_anniversary()
        self.assertIsNone(a["today_milestone"])
        self.assertIn(30, a["celebrated_milestones"])

    def test_non_milestone_day_no_fire(self):
        """非里程碑日 → today_milestone=None"""
        self.assertIsNone(self._rs(12).get_relationship_anniversary()["today_milestone"])

    def test_decide_scenario_fires_anniversary(self):
        """里程碑日且未庆祝 → anniversary 场景"""
        from datetime import datetime as _dt

        from enhance.core.proactive_engine import ProactiveConfig, UserCtx, decide_scenario
        ctx = UserCtx(user_id="1", intimacy=60, relation_days=100,
                      last_active_at=_dt.utcnow(), last_bot_msg_at=None,
                      anniversary={"milestone": 100, "days_together": 100,
                                   "description": "百日纪念日", "message": "百日快乐！"})
        self.assertEqual(decide_scenario(ctx, ProactiveConfig(miss_you_prob=0.0, call_prob=0.0)),
                         "anniversary")

    def test_decide_scenario_respects_cooldown(self):
        """纪念日也受冷却约束：刚发过消息不触发"""
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from enhance.core.proactive_engine import ProactiveConfig, UserCtx, decide_scenario
        ctx = UserCtx(user_id="1", intimacy=60, relation_days=100,
                      last_active_at=_dt.utcnow(),
                      last_bot_msg_at=_dt.utcnow() - _td(minutes=1),
                      anniversary={"milestone": 100, "days_together": 100,
                                   "description": "百日纪念日", "message": ""})
        self.assertIsNone(decide_scenario(ctx, ProactiveConfig(miss_you_prob=0.0, call_prob=0.0)))

    def test_fire_message_anniversary_prompt_and_mark(self):
        """主动庆祝：prompt 注入里程碑信息 + 发出后标记已庆祝"""
        import asyncio
        from datetime import datetime as _dt

        # 系统人格 prompt 属于本地私有资源，不随开源仓库分发
        persona_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'enhance', 'prompts', 'system_persona.txt',
        )
        if not os.path.exists(persona_path):
            self.skipTest('enhance/prompts/system_persona.txt 未随仓库分发')

        from enhance.core.proactive_engine import ProactiveConfig, ProactiveEngine, UserCtx

        class FakeLLM:
            def __init__(self):
                self.system_prompt = ""

            async def chat(self, system_prompt, user_prompt=""):
                self.system_prompt = system_prompt
                return "今天是百日纪念日！我们居然已经一起走过100天啦～"

        class FakeNotifier:
            def __init__(self):
                self.sent = []

            async def push_message(self, user_id, text):
                self.sent.append((user_id, text))

        class FakeProvider:
            def __init__(self):
                self.marked = []

            async def mark_bot_spoke(self, user_id, text):
                pass

            async def mark_anniversary_celebrated(self, user_id, milestone):
                self.marked.append((user_id, milestone))

        llm, notifier, provider = FakeLLM(), FakeNotifier(), FakeProvider()
        engine = ProactiveEngine(llm=llm, ctx_provider=provider, notifier=notifier,
                                 cfg=ProactiveConfig(miss_you_prob=0.0, call_prob=0.0))
        ctx = UserCtx(user_id="1", intimacy=60, relation_days=100,
                      last_active_at=_dt.utcnow(), last_bot_msg_at=None,
                      anniversary={"milestone": 100, "days_together": 100,
                                   "description": "百日纪念日",
                                   "message": "百日快乐！这100天里发生了好多美好的事情～💖"})

        async def _run():
            await engine._fire_message("1", ctx, "anniversary")

        asyncio.run(_run())
        self.assertIn("百日纪念日", llm.system_prompt)
        self.assertIn("相识第 100 天", llm.system_prompt)
        self.assertTrue(notifier.sent and notifier.sent[0][1].startswith("今天是百日纪念日"))
        self.assertEqual(provider.marked, [("1", 100)])

    def test_context_adapter_wiring(self):
        """ContextAdapter：UserCtx.anniversary 填充 + mark_anniversary_celebrated 幂等"""
        import asyncio

        from enhance.api_routes import ContextAdapter

        rs = self._rs(100)
        rs.save = lambda: None  # 打桩，避免覆盖运行时 miaoyi_relationship.json

        class FakeDB:
            def get_user_by_id(self, uid):
                return {"created_ts": time.time() - 100 * 86400}

            def get_memories(self, limit=3, user_id=""):
                return []

            def get_conversations(self, limit=50):
                return []

            def get_messages(self, session_id, limit=50):
                return []

            def add_message(self, session_id, role, text):
                pass

        class FakeCore:
            relationship = rs

            def get_user_distress(self):
                return None

        async def _run():
            adapter = ContextAdapter(FakeDB(), FakeCore())
            ctx = await adapter.get("1")
            await adapter.mark_anniversary_celebrated("1", 100)
            return ctx

        ctx = asyncio.run(_run())
        self.assertTrue(ctx.anniversary and ctx.anniversary["milestone"] == 100)
        self.assertIsNone(rs.get_relationship_anniversary()["today_milestone"])

    def test_persona_scenario_registered(self):
        """persona 场景文案已注册"""
        from enhance.core.persona import PROACTIVE_SCENARIOS
        self.assertIn("anniversary", PROACTIVE_SCENARIOS)


# ============================================================
# 6. L4/L5：双通道输出（内心戏/口语）+ TTS 流式化参数（2026-09-24）
# ============================================================

class TestDualChannel(unittest.TestCase):
    """L5 内心戏拆分 + TTS 内心戏剥离 + L4 CosyVoice2 情感参数表"""

    def test_split_inner_prefix(self):
        """（内心：……）前缀拆分"""
        from core.miaoyi_core import MiaoyiCore
        thought, spoken = MiaoyiCore._split_inner_monologue(
            '（内心：心跳有点快…）主人今天好好看呀！')
        self.assertEqual(thought, '心跳有点快…')
        self.assertEqual(spoken, '主人今天好好看呀！')

    def test_split_inner_midtext_bracket_variant(self):
        """【内心：……】变体 + 正文中段拆分"""
        from core.miaoyi_core import MiaoyiCore
        thought, spoken = MiaoyiCore._split_inner_monologue(
            '唔…【内心：他在担心我】别担心啦，我在呢')
        self.assertEqual(thought, '他在担心我')
        self.assertEqual(spoken, '唔…别担心啦，我在呢')

    def test_split_inner_absent(self):
        """无内心戏时原样返回"""
        from core.miaoyi_core import MiaoyiCore
        thought, spoken = MiaoyiCore._split_inner_monologue('今天天气不错喵～')
        self.assertIsNone(thought)
        self.assertEqual(spoken, '今天天气不错喵～')

    def test_tts_strips_inner_leak(self):
        """泄漏进 TTS 的内心戏标记被兜底剥离"""
        from core.voice_manager import text_for_tts
        cleaned = text_for_tts('（内心：有点想他）主人晚安喵～')
        self.assertNotIn('内心', cleaned)
        self.assertIn('主人晚安喵', cleaned)

    def test_cosyvoice_params_cover_styles(self):
        """_get_voice_style 产出的风格都有 CosyVoice2 参数（且参数在合法区间）"""
        from core.voice_manager import _COSYVOICE_STYLE_PARAMS, VoiceManager
        vm = VoiceManager()
        for emo in ['joy', 'sadness', 'love', 'shyness', 'anger',
                    'boredom', 'fatigue', 'nostalgia', 'curiosity', 'trust']:
            style = vm._get_voice_style(emo)
            self.assertIn(style, _COSYVOICE_STYLE_PARAMS,
                          f'情感 {emo} → 风格 {style} 缺少 CosyVoice2 参数')
        p = _COSYVOICE_STYLE_PARAMS['default']
        self.assertTrue(0.5 <= p['rate'] <= 2.0)
        self.assertTrue(0.5 <= p['pitch'] <= 2.0)
        self.assertTrue(0 <= p['volume'] <= 100)

    def test_cosyvoice_disabled_without_key(self):
        """未配置 DASHSCOPE_API_KEY 时 CosyVoice2 引擎自动跳过（走原降级链）"""
        import os
        from core.voice_manager import VoiceManager
        vm = VoiceManager()
        old = os.environ.pop('DASHSCOPE_API_KEY', None)
        try:
            self.assertFalse(vm._cosyvoice_enabled())
        finally:
            if old is not None:
                os.environ['DASHSCOPE_API_KEY'] = old


# ============================================================
# 8. L6 猫咪日记 + 回忆相册（user_id 隔离 / 幂等写入 / 那年今日 / 签到状态）
# ============================================================

class TestDiaryMemoryAlbum(unittest.TestCase):
    """L6 日记系统：多用户隔离 + 唯一约束幂等 + 那年今日 + 签到打通"""

    @classmethod
    def setUpClass(cls):
        import tempfile, shutil
        cls.test_dir = tempfile.mkdtemp()
        cls.db_path = os.path.join(cls.test_dir, 'test_diary.db')
        from db_manager import DBManager
        cls.db = DBManager(db_path=cls.db_path, db_config={'type': 'sqlite'})
        cls.core = None

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        shutil.rmtree(cls.test_dir, ignore_errors=True)

    def test_diary_user_isolation_and_idempotent(self):
        """日记按 user_id 隔离；(user_id, diary_date) 唯一约束下重复写入幂等"""
        db = self.db
        db.add_autobiography_entry('user_a', '2026-09-24', 'A 的日记')
        db.add_autobiography_entry('user_b', '2026-09-24', 'B 的日记')
        # 重复写入同日：REPLACE 而非新增
        db.add_autobiography_entry('user_a', '2026-09-24', 'A 的日记（更新版）')

        a = db.get_autobiography_by_date('user_a', '2026-09-24')
        b = db.get_autobiography_by_date('user_b', '2026-09-24')
        self.assertEqual(a['diary_text'], 'A 的日记（更新版）')
        self.assertEqual(b['diary_text'], 'B 的日记')
        # 隔离：A 查不到 B 的
        self.assertIsNone(db.get_autobiography_by_date('user_a', 'no-such'))
        # 相册列表各自独立
        a_list = db.get_autobiography_archive('user_a')
        self.assertEqual(len(a_list), 1)
        self.assertEqual(a_list[0]['user_id'], 'user_a')

    def test_memory_lane_year_ago(self):
        """那年今日：同月同日历史日记返回且排除今年"""
        import datetime
        today = datetime.date.today()
        year_ago_date = (today - datetime.timedelta(days=365)).isoformat()
        db = self.db
        db.add_autobiography_entry('user_lane', year_ago_date, '一年前的今天')
        db.add_autobiography_entry('user_lane', today.isoformat(), '今天的日记')
        got = db.get_autobiography_on_month_day('user_lane', today.month, today.day, today.year)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]['diary_text'], '一年前的今天')

    def test_autobiography_system_wrapper(self):
        """AutobiographySystem 包装层：recent_entries 兼容 + user_id 传递"""
        from core.autobiography import AutobiographySystem
        self.db.add_autobiography_entry('user_wrap', '2026-09-23', '昨天的日记')
        auto = AutobiographySystem(self.db, llm_system=None)
        auto._user_id = 'user_wrap'
        entries = auto.get_recent_entries(count=3)
        self.assertTrue(all('content' in e for e in entries))
        # generate_daily_diary 在 LLM 不可用时安全返回 None
        self.assertIsNone(auto.generate_daily_diary('user_wrap'))
        # 相册包装方法
        self.assertEqual(len(auto.get_archive('user_wrap')), 1)

    def test_checkin_status(self):
        """get_checkin_status 与签到打通：签到后 streak/checked_today 正确"""
        db = self.db
        before = db.get_checkin_status('user_checkin')
        self.assertEqual(before['streak'], 0)
        self.assertFalse(before['checked_today'])
        db.checkin_user('user_checkin')
        after = db.get_checkin_status('user_checkin')
        self.assertEqual(after['streak'], 1)
        self.assertTrue(after['checked_today'])


class TestL8Extensions(unittest.TestCase):
    """【L8】角色卡导出（SillyTavern V2）+ 插件 manifest 机制"""

    def test_character_card_spec(self):
        """角色卡符合 chara_card_v2 spec：必填字段 + 世界书由记忆生成"""
        from core.character_card import build_character_card
        card = build_character_card(
            name='喵艺', description='d', personality='p', scenario='s',
            first_mes='fm', system_prompt='sp',
            memories=[{'key': '喜欢的颜色', 'value': '薄荷绿'}, {'key': '', 'value': '脏数据应被丢弃'}],
            tags=['猫娘'], miaoyi_extensions={'stage_name': '朋友'})
        self.assertEqual(card['spec'], 'chara_card_v2')
        self.assertEqual(card['spec_version'], '2.0')
        data = card['data']
        for field in ('name', 'description', 'personality', 'scenario', 'first_mes', 'system_prompt'):
            self.assertTrue(data[field], f'data.{field} 不应为空')
        self.assertEqual(data['name'], '喵艺')
        # 世界书：无效记忆条目被过滤
        entries = data['character_book']['entries']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['keys'], ['喜欢的颜色'])
        self.assertIn('薄荷绿', entries[0]['content'])
        self.assertIn('miaoyi', data['extensions'])
        self.assertEqual(data['extensions']['miaoyi']['stage_name'], '朋友')

    def test_plugin_manager_scan_and_toggle(self):
        """插件扫描：有效 manifest 加载、无效跳过、toggle 写回并即时生效"""
        import json
        import tempfile
        from core.plugin_manager import PluginManager
        tmp = tempfile.mkdtemp()
        try:
            # 有效插件 a（默认启用）+ 无效插件（id 不匹配）+ 无效插件（缺 fragments text）
            os.makedirs(os.path.join(tmp, 'good'))
            with open(os.path.join(tmp, 'good', 'manifest.json'), 'w', encoding='utf-8') as f:
                json.dump({'id': 'good', 'name': '好插件', 'version': '1.0.0', 'enabled': True,
                           'fragments': [{'position': 'append', 'text': '扩展设定A'}]}, f, ensure_ascii=False)
            os.makedirs(os.path.join(tmp, 'bad_id'))
            with open(os.path.join(tmp, 'bad_id', 'manifest.json'), 'w', encoding='utf-8') as f:
                json.dump({'id': 'mismatch', 'name': 'x', 'version': '1.0'}, f, ensure_ascii=False)
            os.makedirs(os.path.join(tmp, 'bad_frag'))
            with open(os.path.join(tmp, 'bad_frag', 'manifest.json'), 'w', encoding='utf-8') as f:
                json.dump({'id': 'bad_frag', 'name': 'x', 'version': '1.0',
                           'fragments': [{'text': ' '}]}, f, ensure_ascii=False)

            pm = PluginManager(plugins_dir=tmp)
            self.assertEqual([p['id'] for p in pm.list_plugins()], ['good'])
            # 启用 → 片段可见；停用 → 片段消失且写回 manifest
            self.assertEqual(pm.get_active_fragments(), ['扩展设定A'])
            self.assertTrue(pm.set_enabled('good', False))
            self.assertEqual(pm.get_active_fragments(), [])
            with open(os.path.join(tmp, 'good', 'manifest.json'), encoding='utf-8') as f:
                self.assertFalse(json.load(f)['enabled'])
            # 重新扫描后保持停用
            pm.scan()
            self.assertEqual(pm.get_active_fragments(), [])
            # 不存在的插件 toggle 返回 False
            self.assertFalse(pm.set_enabled('ghost', True))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_project_plugins_dir_loads(self):
        """项目 plugins/ 目录的真实插件可被加载（示例插件 night_owl）"""
        from core.plugin_manager import PluginManager
        pm = PluginManager()
        ids = [p['id'] for p in pm.list_plugins()]
        self.assertIn('night_owl', ids)
        night_owl = next(p for p in pm.list_plugins() if p['id'] == 'night_owl')
        self.assertEqual(night_owl['fragment_count'], 1)


class TestL9MemoryClient(unittest.TestCase):
    """【L9】记忆服务远程客户端：离线降级 + HTTP 回环"""

    def test_memory_client_degrades_offline(self):
        """记忆服务不可达时：读返回空值、写不抛异常（绝不挂主链路）"""
        from core.memory_client import MemoryClient
        # 端口 1 保留端口，连接必然快速失败
        mc = MemoryClient('http://127.0.0.1:1', token='t', timeout=0.5)
        self.assertEqual(mc.get_facts('u1'), {})
        self.assertEqual(mc.get_context_str('u1'), '')
        self.assertEqual(mc.get_user_profile('u1'), '')
        self.assertEqual(mc.get_memory_context_bundle('u1', 'hi'), ('', '', {}))
        self.assertEqual(mc.get_relevant_memories('q', user_id='u1'), [])
        self.assertEqual(mc.get_stats(), {})
        self.assertFalse(mc.health())
        # 写操作静默降级
        mc.remember('k', 'v', user_id='u1')
        mc.forget('k', user_id='u1')
        mc.add_conv_summary('u', 'b', user_id='u1')
        self.assertFalse(mc.semantic.update_value('k', 'v2', user_id='u1'))
        self.assertFalse(mc.semantic.set_pinned('k', True, user_id='u1'))

    def test_memory_client_roundtrip_stub(self):
        """与 memory_server 协议回环：请求带 token 头、响应按 {code, data} 解包"""
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from core.memory_client import MemoryClient

        seen = {}

        class Stub(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                seen['path'] = self.path
                seen['token'] = self.headers.get('X-Memory-Token', '')
                body = {'code': 200, 'data': {'memory_ctx': 'ctx', 'user_profile': 'p',
                                              'facts': {'称呼': '小明'}}}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

            def do_POST(self):
                length = int(self.headers.get('Content-Length', 0))
                payload = json.loads(self.rfile.read(length) or b'{}')
                seen['post'] = {'path': self.path, 'payload': payload}
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'code': 200, 'data': True}).encode())

        server = HTTPServer(('127.0.0.1', 0), Stub)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            mc = MemoryClient(f'http://127.0.0.1:{server.server_port}', token='tok-123', timeout=2)
            memory_ctx, user_profile, facts = mc.get_memory_context_bundle('u1', 'hi')
            self.assertEqual((memory_ctx, user_profile), ('ctx', 'p'))
            self.assertEqual(facts, {'称呼': '小明'})
            self.assertEqual(seen['token'], 'tok-123')
            self.assertIn('user_id=u1', seen['path'])
            mc.remember('爱好', '画画', user_id='u1')
            self.assertEqual(seen['post']['path'], '/remember')
            self.assertEqual(seen['post']['payload'],
                             {'key': '爱好', 'value': '画画', 'user_id': 'u1'})
            mc.semantic.set_pinned('爱好', True, user_id='u1')
            self.assertEqual(seen['post']['path'], '/semantic/set_pinned')
        finally:
            server.shutdown()


# ============================================================
# 【L10】低频客串群聊测试
# ============================================================

class _StubMemory:
    """duck-typing 记忆层 stub：与 UnifiedMemorySystem / MemoryClient 同款接口"""

    def __init__(self, facts=None):
        self._facts = dict(facts or {})
        self.remembered = []

    def get_facts(self, user_id=''):
        return dict(self._facts)

    def remember(self, key, value, user_id=''):
        self._facts[key] = str(value)
        self.remembered.append({'key': key, 'value': value, 'user_id': user_id})


class _StubLLM:
    """固定返回剧本 JSON 的 LLM stub（chat 统一接口）"""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


class TestL10GuestEvents(unittest.TestCase):
    """【L10】低频客串：触发判定 + 剧本解析 + 端到端（stub LLM）"""

    # 适宜时段内的固定时刻（2026-06-01 15:00 本地时间），避免测试受真实时钟影响
    NOW = time.mktime((2026, 6, 1, 15, 0, 0, 0, 0, -1))

    def test_can_trigger_hour_and_interval(self):
        """触发判定纯函数：适宜时段 + 3 天间隔约束"""
        from core.guest_events import GuestEventSystem
        # 深夜/清晨不触发（与 L7 免打扰时段衔接：10~22 点为适宜时段）
        self.assertFalse(GuestEventSystem._can_trigger(None, self.NOW, 3))
        self.assertFalse(GuestEventSystem._can_trigger(None, self.NOW, 23))
        self.assertTrue(GuestEventSystem._can_trigger(None, self.NOW, 10))
        self.assertTrue(GuestEventSystem._can_trigger(None, self.NOW, 21))
        # 间隔约束：2 天不触发、满 3 天触发、从未触发直接放行
        self.assertFalse(GuestEventSystem._can_trigger(self.NOW - 2 * 86400, self.NOW, 15))
        self.assertTrue(GuestEventSystem._can_trigger(self.NOW - 3 * 86400, self.NOW, 15))
        self.assertTrue(GuestEventSystem._can_trigger(None, self.NOW, 15))

    def test_maybe_run_disabled_without_llm(self):
        """LLM 未就绪时静默不触发（绝不挂主链路）"""
        from core.guest_events import GuestEventSystem
        ge = GuestEventSystem(llm=None, memory=_StubMemory())
        self.assertIsNone(ge.maybe_run('u1', db=None, now_ts=self.NOW))

    def test_maybe_run_blocked_by_interval(self):
        """间隔冷却期内不触发，且不写触发状态"""
        from core.guest_events import GuestEventSystem
        memory = _StubMemory({'客串_上次': str(self.NOW - 2 * 86400)})
        ge = GuestEventSystem(llm=_StubLLM('[]'), memory=memory)
        self.assertIsNone(ge.maybe_run('u1', db=None, now_ts=self.NOW))
        self.assertEqual(memory.remembered, [])

    def test_parse_script(self):
        """剧本解析：围栏剥离/片段截取/非法 speaker 过滤/台词数与长度约束"""
        from core.guest_events import GuestEventSystem
        parse = GuestEventSystem._parse_script
        # markdown 围栏 JSON
        fenced = ('```json\n[{"speaker": "布丁", "line": "嗷呜，我来啦！"},'
                  ' {"speaker": "雪球", "line": "哼。"}, {"speaker": "喵艺", "line": "喵～欢迎！"}]\n```')
        script = parse(fenced)
        self.assertEqual(len(script), 3)
        self.assertEqual(script[0]['speaker'], '布丁')
        # 裸 JSON 前后夹带文字时截取 [...] 片段
        noisy = ('好的，这是剧本：[{"speaker": "煤球", "line": "那个……你好"},'
                 ' {"speaker": "喵艺", "line": "别怕喵"}, {"speaker": "雪球", "line": "哼，无聊"}] 完毕')
        self.assertEqual(len(parse(noisy)), 3)
        # 非法 speaker 被过滤
        bad = ('[{"speaker": "路人", "line": "x"}, {"speaker": "喵艺", "line": "a"},'
               ' {"speaker": "布丁", "line": "b"}, {"speaker": "雪球", "line": "c"}]')
        self.assertEqual([s['speaker'] for s in parse(bad)], ['喵艺', '布丁', '雪球'])
        # 台词数不足 / 垃圾输入 / 空输入
        self.assertIsNone(parse('[{"speaker": "喵艺", "line": "只有一条"}]'))
        self.assertIsNone(parse('完全不是 JSON'))
        self.assertIsNone(parse(''))
        # 超长台词截断到 60 字
        import json as _json
        long = _json.dumps([
            {'speaker': '喵艺', 'line': '喵' * 100},
            {'speaker': '布丁', 'line': '嗷'},
            {'speaker': '雪球', 'line': '哼'},
        ], ensure_ascii=False)
        self.assertLessEqual(len(parse(long)[0]['line']), 60)

    def test_maybe_run_end_to_end_stub(self):
        """stub LLM 全链路：生成→格式化→写入触发状态→进入间隔冷却"""
        import json as _json
        from unittest import mock
        import core.guest_events as ge_mod
        from core.guest_events import GuestEventSystem

        reply = _json.dumps([
            {'speaker': '布丁', 'line': '嗷呜！小鱼干呢？'},
            {'speaker': '雪球', 'line': '哼，就知道吃。'},
            {'speaker': '煤球', 'line': '那个……大家好。'},
            {'speaker': '喵艺', 'line': '喵～这是我朋友！'},
        ], ensure_ascii=False)
        memory = _StubMemory()
        ge = GuestEventSystem(llm=_StubLLM(reply), memory=memory)
        with mock.patch.object(ge_mod, 'TRIGGER_PROBABILITY', 1.0):
            lines = ge.maybe_run('u1', db=None, now_ts=self.NOW)
        self.assertIsNotNone(lines)
        # 开场卡 + 台词行格式
        self.assertEqual(lines[0], '🏡 叮咚——🍮布丁、❄️雪球、🖤煤球按响门铃，来做客啦！')
        self.assertIn('布丁🍮：嗷呜！小鱼干呢？', lines)
        self.assertIn('喵艺🐱：喵～这是我朋友！', lines)
        # 触发状态经记忆层持久化（按用户隔离，L9 远程模式同键共享）
        self.assertEqual(memory.remembered[0]['key'], '客串_上次')
        self.assertEqual(memory.remembered[0]['user_id'], 'u1')
        # 触发后立即进入间隔冷却
        with mock.patch.object(ge_mod, 'TRIGGER_PROBABILITY', 1.0):
            self.assertIsNone(ge.maybe_run('u1', db=None, now_ts=self.NOW + 60))

