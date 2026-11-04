#!/usr/bin/env python3
"""
喵艺 AI 伴侣 · 数据库备份脚本
==============================
定时备份 SQLite 数据库到 backups/ 目录。
建议配合 cron 每日执行。

用法：
    python scripts/backup_db.py
    python scripts/backup_db.py --db-path /path/to/db --backup-dir /path/to/backups
"""

import argparse
import logging
import os
import shutil
import sys
import time
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
_log = logging.getLogger("BackupDB")


def backup_database(
    db_path: str = "data/miaoyi.db",
    backup_dir: str = "backups",
    max_backups: int = 30,
) -> str:
    """
    备份数据库

    Args:
        db_path: 数据库文件路径
        backup_dir: 备份目录
        max_backups: 最大保留备份数

    Returns:
        str: 备份文件路径
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")

    # 创建备份目录
    os.makedirs(backup_dir, exist_ok=True)

    # 生成备份文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    db_name = os.path.splitext(os.path.basename(db_path))[0]
    backup_filename = f"{db_name}_{timestamp}.db"
    backup_path = os.path.join(backup_dir, backup_filename)

    # 执行备份
    start_time = time.time()
    shutil.copy2(db_path, backup_path)
    elapsed = time.time() - start_time

    backup_size = os.path.getsize(backup_path)
    db_size = os.path.getsize(db_path)

    _log.info(
        f"备份完成: {backup_path} "
        f"(大小: {backup_size / 1024:.1f}KB, 耗时: {elapsed:.2f}s)"
    )

    # 清理旧备份
    _cleanup_old_backups(backup_dir, max_backups)

    return backup_path


def _cleanup_old_backups(backup_dir: str, max_backups: int):
    """清理超过数量的旧备份"""
    backups = sorted(
        [
            f for f in os.listdir(backup_dir)
            if f.endswith('.db')
        ],
        key=lambda x: os.path.getmtime(os.path.join(backup_dir, x)),
        reverse=True,
    )

    if len(backups) > max_backups:
        removed = 0
        for old_backup in backups[max_backups:]:
            old_path = os.path.join(backup_dir, old_backup)
            os.remove(old_path)
            removed += 1
            _log.info(f"删除旧备份: {old_backup}")

        if removed > 0:
            _log.info(f"已清理 {removed} 个旧备份")


def list_backups(backup_dir: str = "backups"):
    """列出所有备份"""
    if not os.path.exists(backup_dir):
        print("备份目录不存在")
        return

    backups = sorted(
        [
            f for f in os.listdir(backup_dir)
            if f.endswith('.db')
        ],
        key=lambda x: os.path.getmtime(os.path.join(backup_dir, x)),
    )

    if not backups:
        print("暂无备份")
        return

    print(f"\n备份列表 ({len(backups)} 个):")
    print("-" * 80)
    print(f"{'文件名':<40} {'大小':>10} {'修改时间':<20}")
    print("-" * 80)

    for backup_file in backups:
        filepath = os.path.join(backup_dir, backup_file)
        size = os.path.getsize(filepath)
        mtime = datetime.fromtimestamp(os.path.getmtime(filepath))

        size_str = f"{size / 1024:.1f}KB"
        mtime_str = mtime.strftime("%Y-%m-%d %H:%M:%S")

        print(f"{backup_file:<40} {size_str:>10} {mtime_str:<20}")

    print("-" * 80)
    total_size = sum(
        os.path.getsize(os.path.join(backup_dir, f))
        for f in backups
    )
    print(f"总计: {len(backups)} 个备份, {total_size / 1024:.1f}KB")


def restore_database(
    backup_path: str,
    db_path: str = "data/miaoyi.db",
):
    """
    从备份恢复数据库

    Args:
        backup_path: 备份文件路径
        db_path: 目标数据库路径
    """
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"备份文件不存在: {backup_path}")

    # 确认操作
    print("警告：即将从备份恢复数据库！")
    print(f"  备份文件: {backup_path}")
    print(f"  目标数据库: {db_path}")
    print("  这将覆盖现有数据！")

    response = input("确认恢复？(输入 YES 确认): ")
    if response != "YES":
        print("已取消恢复操作")
        return

    # 先备份当前数据库
    if os.path.exists(db_path):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_copy = f"{db_path}.safety_{timestamp}"
        shutil.copy2(db_path, safety_copy)
        _log.info(f"已创建安全副本: {safety_copy}")

    # 执行恢复
    shutil.copy2(backup_path, db_path)
    _log.info(f"恢复完成: {backup_path} -> {db_path}")


def main():
    parser = argparse.ArgumentParser(
        description="喵艺 AI 伴侣 · 数据库备份工具"
    )
    parser.add_argument(
        '--db-path',
        default='data/miaoyi.db',
        help='数据库文件路径 (默认: data/miaoyi.db)',
    )
    parser.add_argument(
        '--backup-dir',
        default='backups',
        help='备份目录 (默认: backups)',
    )
    parser.add_argument(
        '--max-backups',
        type=int,
        default=30,
        help='最大保留备份数 (默认: 30)',
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='列出所有备份',
    )
    parser.add_argument(
        '--restore',
        type=str,
        metavar='BACKUP_PATH',
        help='从指定备份恢复',
    )

    args = parser.parse_args()

    if args.list:
        list_backups(args.backup_dir)
        return

    if args.restore:
        restore_database(args.restore, args.db_path)
        return

    # 执行备份
    try:
        backup_path = backup_database(
            db_path=args.db_path,
            backup_dir=args.backup_dir,
            max_backups=args.max_backups,
        )
        print(f"\n✅ 备份成功: {backup_path}")
    except Exception as e:
        _log.error(f"备份失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
