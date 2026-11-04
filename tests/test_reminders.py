"""定时提醒功能单元测试（tests/test_reminders.py）"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.reminder_manager import ReminderManager


class TestReminderParsing(unittest.TestCase):
    """时间解析测试（不依赖 LLM，纯正则降级路径）"""

    def setUp(self):
        self.mgr = ReminderManager(db_manager=None, llm_call=None, fire_proactive=None)

    def test_detect_intent(self):
        self.assertTrue(self.mgr.detect_intent("明天下午三点提醒我开会"))
        self.assertTrue(self.mgr.detect_intent("别忘了明天取快递"))
        self.assertTrue(self.mgr.detect_intent("7点叫我起床"))
        self.assertFalse(self.mgr.detect_intent("今天天气怎么样"))
        self.assertFalse(self.mgr.detect_intent(""))

    def test_parse_relative_minutes(self):
        r = self.mgr._parse_fallback("20分钟后提醒我喝水")
        self.assertIsNotNone(r)
        expect = time.time() + 20 * 60
        self.assertAlmostEqual(r['remind_ts'], expect, delta=5)
        self.assertEqual(r['content'], '喝水')

    def test_parse_relative_hours(self):
        r = self.mgr._parse_fallback("2小时后提醒我下班打卡")
        self.assertIsNotNone(r)
        expect = time.time() + 2 * 3600
        self.assertAlmostEqual(r['remind_ts'], expect, delta=5)
        self.assertEqual(r['content'], '下班打卡')

    def test_parse_absolute_tomorrow(self):
        r = self.mgr._parse_fallback("明天早上8点提醒我开会")
        self.assertIsNotNone(r)
        dt = datetime.fromtimestamp(r['remind_ts'])
        self.assertEqual(dt.hour, 8)
        self.assertEqual(dt.minute, 0)
        self.assertEqual(dt.date(), (datetime.now() + timedelta(days=1)).date())
        self.assertEqual(r['content'], '开会')

    def test_parse_afternoon_hour(self):
        r = self.mgr._parse_fallback("明天下午3点提醒我拿快递")
        dt = datetime.fromtimestamp(r['remind_ts'])
        self.assertEqual(dt.hour, 15)
        self.assertEqual(r['content'], '拿快递')

    def test_parse_half_past(self):
        r = self.mgr._parse_fallback("明天晚上10点半提醒我睡觉")
        dt = datetime.fromtimestamp(r['remind_ts'])
        self.assertEqual((dt.hour, dt.minute), (22, 30))
        self.assertEqual(r['content'], '睡觉')

    def test_parse_bare_time_rolls_to_tomorrow(self):
        """未带日期的已过时间应滚到明天"""
        r = self.mgr._parse_fallback("8点提醒我起床")
        dt = datetime.fromtimestamp(r['remind_ts'])
        self.assertGreater(dt, datetime.now())

    def test_create_paste_time_returns_none(self):
        """过去时间不应创建提醒"""
        r = self.mgr._parse_fallback("昨天提醒我吃饭")
        if r:
            self.assertLess(r['remind_ts'], time.time() + ReminderManager.MIN_LEAD_SECONDS)

    def test_confirmation_format(self):
        msg = self.mgr._format_confirmation(time.time() + 3600, '开会', 3600)
        self.assertIn('开会', msg)

    def test_list_intent(self):
        self.assertTrue(self.mgr.detect_list_intent("看看我的提醒"))
        self.assertTrue(self.mgr.detect_list_intent("你有什么提醒我吗"))
        self.assertFalse(self.mgr.detect_list_intent("提醒我喝水"))

    def test_cancel_intent(self):
        self.assertTrue(self.mgr.detect_cancel_intent("取消提醒"))
        self.assertTrue(self.mgr.detect_cancel_intent("别提醒我了"))
        self.assertFalse(self.mgr.detect_cancel_intent("提醒我喝水"))


class TestReminderDB(unittest.TestCase):
    """提醒 CRUD 测试（SQLite 内存库，隔离不碰 MySQL）"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ['JWT_SECRET_KEY'] = 'test-secret-key-for-unit-testing-2024'
        from db_manager import DBManager
        # db_path 必须作为独立位置参数传入（db_config 内的 db_path 键不生效，
        # 否则会连到项目根目录的真实 miaoyi.db 造成测试污染）
        self.db = DBManager(os.path.join(self.tmpdir, 'test.db'), db_config={'type': 'sqlite'})

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_and_due(self):
        rid = self.db.create_reminder('77', time.time() - 1, '测试提醒')
        self.assertGreater(rid, 0)
        due = self.db.get_due_reminders()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]['content'], '测试提醒')
        self.assertEqual(due[0]['user_id'], '77')

    def test_future_not_due(self):
        self.db.create_reminder('77', time.time() + 3600, '未来提醒')
        self.assertEqual(len(self.db.get_due_reminders()), 0)

    def test_fire_once(self):
        rid = self.db.create_reminder('77', time.time() - 1, '一次性提醒')
        self.assertTrue(self.db.mark_reminder_fired(rid))
        # 二次标记失败（原子性）
        self.assertFalse(self.db.mark_reminder_fired(rid))
        self.assertEqual(len(self.db.get_due_reminders()), 0)

    def test_cancel(self):
        rid = self.db.create_reminder('77', time.time() + 3600, '要取消的提醒')
        self.assertTrue(self.db.cancel_reminder('77', rid))
        # 其他用户不能取消
        rid2 = self.db.create_reminder('77', time.time() + 3600, '别人的提醒')
        self.assertFalse(self.db.cancel_reminder('999', rid2))
        pending = self.db.get_user_reminders('77')
        self.assertEqual(len(pending), 1)

    def test_legacy_table_migration(self):
        """legacy reminders 表（text/trigger_time/fired）应自动升级为新结构"""
        import sqlite3
        # 1. 先造一个 legacy 结构的库
        legacy_path = os.path.join(self.tmpdir, 'legacy.db')
        conn = sqlite3.connect(legacy_path)
        conn.execute('''CREATE TABLE reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            trigger_time REAL,
            fired INTEGER DEFAULT 0
        )''')
        conn.execute("INSERT INTO reminders (text, trigger_time, fired) VALUES ('旧提醒', ?, 1)",
                     (time.time() + 7200,))
        conn.commit()
        conn.close()

        # 2. DBManager 打开时自动迁移
        from db_manager import DBManager
        db2 = DBManager(legacy_path, db_config={'type': 'sqlite'})
        try:
            cols = [c['name'] for c in self._table_info(db2, 'reminders')]
            self.assertIn('status', cols)
            self.assertIn('user_id', cols)
            rows = db2.get_user_reminders('', include_fired=True)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['content'], '旧提醒')
            self.assertEqual(rows[0]['status'], 'fired')
        finally:
            db2.close()

    @staticmethod
    def _table_info(db, table):
        cur = db._conn.cursor()
        cur.execute(f"PRAGMA table_info({table})")
        return [{'name': r[1]} for r in cur.fetchall()]


if __name__ == '__main__':
    unittest.main()
