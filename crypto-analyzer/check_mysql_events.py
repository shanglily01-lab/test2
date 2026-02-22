#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MySQL Event 调度器状态检查工具
自动诊断并提供修复建议
"""
import pymysql
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

# 设置UTF-8输出
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

load_dotenv()

def get_db_config():
    """获取数据库配置"""
    return {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'charset': 'utf8mb4'
    }

def check_event_scheduler(cursor):
    """检查 Event Scheduler 状态"""
    print("\n" + "="*80)
    print("【1】Event Scheduler 全局状态")
    print("="*80)

    cursor.execute("""
        SELECT VARIABLE_VALUE
        FROM information_schema.GLOBAL_VARIABLES
        WHERE VARIABLE_NAME = 'event_scheduler'
    """)
    result = cursor.fetchone()
    status = result[0] if result else 'UNKNOWN'

    if status == 'ON':
        print(f"✅ Event Scheduler: {status} (正常运行)")
        return True
    else:
        print(f"❌ Event Scheduler: {status} (已关闭！)")
        print("\n修复命令:")
        print("  SET GLOBAL event_scheduler = ON;")
        print("\n永久修复（在 my.cnf 中添加）:")
        print("  [mysqld]")
        print("  event_scheduler = ON")
        return False

def check_events(cursor):
    """检查所有 Event 状态"""
    print("\n" + "="*80)
    print("【2】数据库中的所有 Event")
    print("="*80)

    cursor.execute("""
        SELECT
            EVENT_SCHEMA,
            EVENT_NAME,
            STATUS,
            EVENT_TYPE,
            INTERVAL_VALUE,
            INTERVAL_FIELD,
            LAST_EXECUTED,
            TIMESTAMPDIFF(HOUR, LAST_EXECUTED, NOW()) AS hours_since_last_run
        FROM information_schema.EVENTS
        ORDER BY EVENT_SCHEMA, EVENT_NAME
    """)

    events = cursor.fetchall()

    if not events:
        print("⚠️  未找到任何 Event")
        return []

    disabled_events = []
    stale_events = []

    print(f"\n找到 {len(events)} 个 Event:\n")
    print(f"{'数据库':<20} {'Event名称':<30} {'状态':<10} {'类型':<15} {'上次执行':<20} {'距今(小时)':<12}")
    print("-" * 120)

    for event in events:
        schema, name, status, event_type, interval_val, interval_field, last_exec, hours = event

        # 状态图标
        status_icon = '✅' if status == 'ENABLED' else '❌'

        # 执行计划
        if event_type == 'RECURRING':
            schedule = f"EVERY {interval_val} {interval_field}"
        else:
            schedule = "ONE TIME"

        # 上次执行时间
        last_exec_str = last_exec.strftime('%Y-%m-%d %H:%M:%S') if last_exec else '从未执行'

        # 距今时间
        hours_str = f"{hours}小时" if hours else 'N/A'

        print(f"{schema:<20} {name:<30} {status_icon} {status:<8} {schedule:<15} {last_exec_str:<20} {hours_str:<12}")

        # 收集问题 Event
        if status == 'DISABLED':
            disabled_events.append((schema, name))

        if hours and hours > 24:
            stale_events.append((schema, name, hours))

    # 报告问题
    if disabled_events:
        print("\n❌ 发现被禁用的 Event:")
        for schema, name in disabled_events:
            print(f"  ALTER EVENT `{schema}`.`{name}` ENABLE;")

    if stale_events:
        print("\n⚠️  发现长时间未执行的 Event:")
        for schema, name, hours in stale_events:
            print(f"  {schema}.{name} - {hours}小时未执行")

    return disabled_events

def check_timezone(cursor):
    """检查时区设置"""
    print("\n" + "="*80)
    print("【3】MySQL 时区设置")
    print("="*80)

    cursor.execute("""
        SELECT
            @@global.time_zone AS global_tz,
            @@session.time_zone AS session_tz,
            NOW() AS current_time,
            UTC_TIMESTAMP() AS utc_time
    """)

    result = cursor.fetchone()
    global_tz, session_tz, current_time, utc_time = result

    print(f"全局时区: {global_tz}")
    print(f"会话时区: {session_tz}")
    print(f"当前MySQL时间: {current_time}")
    print(f"UTC时间: {utc_time}")

    if global_tz == 'SYSTEM':
        print("\n⚠️  使用系统时区可能不稳定")
        print("建议: SET GLOBAL time_zone = '+08:00';  # 中国时区")

def fix_event_scheduler(cursor, conn):
    """尝试修复 Event Scheduler"""
    print("\n" + "="*80)
    print("【自动修复】尝试启动 Event Scheduler")
    print("="*80)

    try:
        cursor.execute("SET GLOBAL event_scheduler = ON")
        conn.commit()
        print("✅ Event Scheduler 已启动")
        return True
    except Exception as e:
        print(f"❌ 启动失败: {e}")
        print("请确保有足够的权限（需要 SUPER 权限）")
        return False

def generate_report():
    """生成完整的诊断报告"""
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        print("\n" + "="*80)
        print("MySQL Event Scheduler 诊断报告")
        print(f"诊断时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)

        # 检查 Event Scheduler 状态
        scheduler_ok = check_event_scheduler(cursor)

        # 检查所有 Event
        disabled_events = check_events(cursor)

        # 检查时区
        check_timezone(cursor)

        # 总结
        print("\n" + "="*80)
        print("【诊断总结】")
        print("="*80)

        issues = []
        if not scheduler_ok:
            issues.append("Event Scheduler 未启动")
        if disabled_events:
            issues.append(f"{len(disabled_events)}个 Event 被禁用")

        if issues:
            print("❌ 发现以下问题:")
            for issue in issues:
                print(f"  - {issue}")

            # 询问是否自动修复
            if not scheduler_ok:
                print("\n是否尝试自动修复 Event Scheduler？(y/n): ", end='')
                choice = input().strip().lower()
                if choice == 'y':
                    fix_event_scheduler(cursor, conn)
        else:
            print("✅ Event Scheduler 配置正常")

        print("\n" + "="*80)
        print("建议:")
        print("="*80)
        print("1. 确保 my.cnf 中配置了 event_scheduler = ON")
        print("2. 定期检查 Event 执行日志")
        print("3. 为重要的 Event 添加执行日志记录")
        print("4. 监控长时间未执行的 Event")
        print("="*80 + "\n")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"\n❌ 诊断失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    generate_report()
