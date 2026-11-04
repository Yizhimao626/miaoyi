#!/usr/bin/env python3
"""
  喵艺数据库迁移管理器
  ============================================================
  自包含的数据库版本迁移工具，用于管理 SQLite 数据库结构的演进。

  功能：
    - 自动扫描 migrations/ 目录下的迁移脚本
    - 按版本号顺序执行迁移（文件名前缀数字即版本号）
    - 通过 schema_version 表跟踪已应用的迁移
    - 支持 up（升级）、down（回滚）、status（状态）三种操作

  使用方式：
    python migration_runner.py up       # 应用所有待执行的迁移
    python migration_runner.py down     # 回滚最近一次迁移
    python migration_runner.py status   # 查看迁移状态

  迁移脚本规范：
    - 文件名格式：{版本号}_{描述}.py（如 001_add_rag_tables.py）
    - 必须包含 run(conn) 函数执行迁移
    - 可选包含 down(conn) 函数用于回滚
    - run 函数内使用 CREATE TABLE IF NOT EXISTS 保证幂等性
"""

import importlib.util
import logging
import os
import re
import sqlite3
import sys
import time
from typing import List, Optional, Tuple

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
_log = logging.getLogger("MigrationRunner")

# 迁移脚本存放目录（相对于本文件所在目录）
MIGRATIONS_DIR = os.path.dirname(os.path.abspath(__file__))

# 默认数据库路径（与 db_manager.py 保持一致）
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "miaoyi.db"
)


def _parse_migration_filename(filename: str) -> tuple[int, str] | None:
    """解析迁移文件名，提取版本号和描述。

    文件名格式：{版本号}_{描述}.py
    例如：001_add_rag_tables.py → (1, "add_rag_tables")

    Args:
        filename: 文件名（不含路径）

    Returns:
        (版本号, 描述) 或 None（非迁移文件）
    """
    # 仅匹配以数字开头、以 .py 结尾的文件
    match = re.match(r'^(\d+)_(.+)\.py$', filename)
    if not match:
        return None
    version = int(match.group(1))
    description = match.group(2)
    return (version, description)


def _load_migration_module(filepath: str):
    """动态加载迁移脚本模块。

    Args:
        filepath: 迁移脚本的绝对路径

    Returns:
        模块对象，失败时返回 None
    """
    module_name = f"migration_{os.path.basename(filepath).replace('.py', '')}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception as e:
        _log.error(f"加载迁移脚本失败 {filepath}: {e}")
        return None


class MigrationRunner:
    """数据库迁移管理器。

    负责迁移脚本的发现、执行、回滚和状态查询。
    使用 WAL 模式连接数据库，确保与 db_manager.py 兼容。
    """

    VERSION_TABLE = "schema_version"

    def __init__(self, db_path: str = None):
        """初始化迁移管理器。

        Args:
            db_path: SQLite 数据库路径，默认使用项目根目录的 miaoyi.db
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._connect()
        self._ensure_version_table()

    def _connect(self):
        """建立数据库连接，配置 WAL 模式。"""
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        self._conn.execute('PRAGMA cache_size=5000')
        _log.info(f"已连接数据库: {self.db_path}")

    def _ensure_version_table(self):
        """创建迁移版本跟踪表（若不存在）。"""
        cursor = self._conn.cursor()
        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {self.VERSION_TABLE} (
                version     INTEGER PRIMARY KEY,
                filename    TEXT NOT NULL,
                applied_at  REAL DEFAULT 0,
                description TEXT DEFAULT ''
            )
        ''')
        self._conn.commit()

    def _discover_migrations(self) -> list[tuple[int, str, str]]:
        """扫描 migrations 目录，返回按版本号排序的迁移脚本列表。

        Returns:
            [(版本号, 描述, 文件路径), ...]
        """
        migrations = []
        for filename in os.listdir(MIGRATIONS_DIR):
            parsed = _parse_migration_filename(filename)
            if parsed is None:
                continue
            version, description = parsed
            filepath = os.path.join(MIGRATIONS_DIR, filename)
            migrations.append((version, description, filepath))

        # 按版本号升序排列
        migrations.sort(key=lambda x: x[0])
        return migrations

    def _get_applied_versions(self) -> list[int]:
        """获取已应用的迁移版本号列表。

        Returns:
            已按版本号升序排列的版本号列表
        """
        cursor = self._conn.cursor()
        cursor.execute(
            f'SELECT version FROM {self.VERSION_TABLE} ORDER BY version ASC'
        )
        return [row[0] for row in cursor.fetchall()]

    def _record_migration(self, version: int, filename: str, description: str):
        """记录已应用的迁移到版本跟踪表。

        Args:
            version: 版本号
            filename: 迁移脚本文件名
            description: 迁移描述
        """
        cursor = self._conn.cursor()
        cursor.execute(f'''
            INSERT INTO {self.VERSION_TABLE} (version, filename, applied_at, description)
            VALUES (?, ?, ?, ?)
        ''', (version, filename, time.time(), description))
        self._conn.commit()

    def _remove_migration_record(self, version: int):
        """删除已回滚的迁移记录。

        Args:
            version: 版本号
        """
        cursor = self._conn.cursor()
        cursor.execute(
            f'DELETE FROM {self.VERSION_TABLE} WHERE version = ?',
            (version,)
        )
        self._conn.commit()

    def up(self):
        """应用所有待执行的迁移。

        按版本号顺序逐一执行，跳过已应用的版本。
        每条迁移在事务中执行，失败时自动回滚。
        """
        applied = self._get_applied_versions()
        all_migrations = self._discover_migrations()

        if not all_migrations:
            _log.info("未发现任何迁移脚本")
            return

        pending = [m for m in all_migrations if m[0] not in applied]

        if not pending:
            _log.info("数据库已是最新版本，无需迁移")
            return

        _log.info(f"发现 {len(pending)} 个待执行迁移: "
                   f"{', '.join(f'v{v}' for v, _, _ in pending)}")

        for version, description, filepath in pending:
            filename = os.path.basename(filepath)
            _log.info(f"正在执行迁移 v{version}: {filename}")

            module = _load_migration_module(filepath)
            if module is None:
                _log.error(f"跳过无法加载的迁移: {filename}")
                continue

            if not hasattr(module, 'run'):
                _log.error(f"迁移脚本 {filename} 缺少 run() 函数，跳过")
                continue

            try:
                # 在事务中执行迁移
                self._conn.execute('BEGIN IMMEDIATE')
                module.run(self._conn)
                self._record_migration(version, filename, description)
                self._conn.commit()
                _log.info(f"  ✓ 迁移 v{version} 成功")
            except Exception as e:
                self._conn.rollback()
                _log.error(f"  ✗ 迁移 v{version} 失败: {e}")
                _log.error("  已自动回滚，后续迁移已中止")
                raise

        _log.info(f"全部迁移完成，共应用 {len(pending)} 条")

    def down(self):
        """回滚最近一次已应用的迁移。

        调用迁移脚本中的 down(conn) 函数。
        若脚本未定义 down 函数则给出警告并跳过。
        """
        applied = self._get_applied_versions()

        if not applied:
            _log.info("没有已应用的迁移，无需回滚")
            return

        # 获取最近一次迁移
        latest_version = applied[-1]
        all_migrations = self._discover_migrations()
        target = None
        for v, desc, path in all_migrations:
            if v == latest_version:
                target = (v, desc, path)
                break

        if target is None:
            _log.error(f"找不到版本 {latest_version} 对应的迁移脚本")
            return

        version, description, filepath = target
        filename = os.path.basename(filepath)
        _log.info(f"正在回滚迁移 v{version}: {filename}")

        module = _load_migration_module(filepath)
        if module is None:
            _log.error(f"无法加载迁移脚本: {filename}")
            return

        if not hasattr(module, 'down'):
            _log.warning(f"迁移脚本 {filename} 未定义 down() 函数，无法回滚")
            return

        try:
            self._conn.execute('BEGIN IMMEDIATE')
            module.down(self._conn)
            self._remove_migration_record(version)
            self._conn.commit()
            _log.info(f"  ✓ 迁移 v{version} 已回滚")
        except Exception as e:
            self._conn.rollback()
            _log.error(f"  ✗ 回滚失败: {e}")
            raise

    def status(self):
        """显示迁移状态，返回状态字典供程序化调用。

        Returns:
            dict: {'applied': [{'version': int, 'description': str, 'applied_at': str}],
                   'pending': [{'version': int, 'description': str}],
                   'applied_count': int, 'pending_count': int}
        """
        applied = self._get_applied_versions()
        all_migrations = self._discover_migrations()

        cursor = self._conn.cursor()
        cursor.execute(f'SELECT version, filename, applied_at, description '
                       f'FROM {self.VERSION_TABLE} ORDER BY version ASC')
        applied_details = {row[0]: row for row in cursor.fetchall()}

        result = {'applied': [], 'pending': [], 'applied_count': 0, 'pending_count': 0}

        print("\n" + "=" * 60)
        print("  喵艺数据库迁移状态")
        print("=" * 60)

        if not all_migrations:
            print("  (未发现任何迁移脚本)")
        else:
            for version, description, filepath in all_migrations:
                status_mark = "✓ 已应用" if version in applied else "○ 待执行"
                detail = applied_details.get(version)
                if detail:
                    applied_time = time.strftime(
                        '%Y-%m-%d %H:%M:%S',
                        time.localtime(detail[2])
                    )
                    print(f"  v{version:03d}  {status_mark}  "
                          f"{description}  (applied: {applied_time})")
                    result['applied'].append({
                        'version': version,
                        'description': description,
                        'applied_at': applied_time,
                    })
                else:
                    print(f"  v{version:03d}  {status_mark}  {description}")
                    result['pending'].append({
                        'version': version,
                        'description': description,
                    })

        result['applied_count'] = len(result['applied'])
        result['pending_count'] = len(result['pending'])

        print("=" * 60)
        print(f"  已应用: {result['applied_count']} 条  |  "
              f"待执行: {result['pending_count']} 条")
        print("=" * 60 + "\n")

        return result

    def close(self):
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None
            _log.info("数据库连接已关闭")


def main():
    """CLI 入口：解析命令行参数并执行对应操作。

    用法：
        python migration_runner.py up [db_path]
        python migration_runner.py down [db_path]
        python migration_runner.py status [db_path]

    Args:
        sys.argv[1]: 操作类型 (up/down/status)
        sys.argv[2]: 可选的数据库路径
    """
    if len(sys.argv) < 2:
        print("用法: python migration_runner.py <up|down|status> [db_path]")
        print("  up     — 应用所有待执行的迁移")
        print("  down   — 回滚最近一次迁移")
        print("  status — 查看迁移状态")
        sys.exit(1)

    action = sys.argv[1].lower()
    db_path = sys.argv[2] if len(sys.argv) > 2 else None

    if action not in ('up', 'down', 'status'):
        print(f"未知操作: {action}")
        print("有效操作: up, down, status")
        sys.exit(1)

    runner = MigrationRunner(db_path)

    try:
        if action == 'up':
            runner.up()
        elif action == 'down':
            runner.down()
        elif action == 'status':
            runner.status()
    except Exception as e:
        _log.error(f"迁移操作失败: {e}")
        sys.exit(1)
    finally:
        runner.close()


if __name__ == '__main__':
    main()