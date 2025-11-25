"""
验证策略执行器是否正常工作
全面检查所有可能的问题
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
import yaml
import json
from datetime import datetime, timedelta

config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

print("=" * 80)
print("策略执行器完整验证")
print("=" * 80)
print()

# 1. 检查数据库表
print("1. 检查数据库表...")
try:
    connection = pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 3306),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'binance-data'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    cursor = connection.cursor()
    
    # 检查表是否存在
    cursor.execute("SHOW TABLES LIKE 'strategy_hits'")
    if cursor.fetchone():
        print("   [OK] strategy_hits 表存在")
        
        # 检查字段类型
        cursor.execute("DESCRIBE strategy_hits")
        columns = cursor.fetchall()
        strategy_id_col = next((c for c in columns if c['Field'] == 'strategy_id'), None)
        if strategy_id_col and 'bigint' in strategy_id_col['Type'].lower():
            print("   [OK] strategy_id 字段类型正确 (BIGINT)")
        else:
            print("   [ERROR] strategy_id 字段类型错误！需要是 BIGINT")
            print("   💡 运行修复脚本: python scripts/fix_strategy_id_column.py")
    else:
        print("   [ERROR] strategy_hits 表不存在！")
        print("   [TIP] 运行迁移脚本: python scripts/run_migration_009.py")
    
    cursor.close()
    connection.close()
except Exception as e:
    print(f"   [ERROR] 数据库连接失败: {e}")
print()

# 2. 检查策略配置
print("2. 检查策略配置...")
try:
    strategies_file = project_root / 'config' / 'strategies' / 'futures_strategies.json'
    if strategies_file.exists():
        with open(strategies_file, 'r', encoding='utf-8') as f:
            strategies = json.load(f)
        enabled_strategies = [s for s in strategies if s.get('enabled', False)]
        print(f"   [OK] 找到 {len(enabled_strategies)} 个启用的策略")
        for s in enabled_strategies:
            print(f"      - {s.get('name')} (ID: {s.get('id')})")
    else:
        print("   [ERROR] 策略配置文件不存在")
except Exception as e:
    print(f"   [ERROR] 读取策略配置失败: {e}")
print()

# 3. 测试数据库保存功能
print("3. 测试数据库保存功能...")
try:
    from app.services.strategy_hit_recorder import StrategyHitRecorder
    
    recorder = StrategyHitRecorder(db_config)
    
    # 创建测试数据
    test_strategy = {
        'id': 1735123456790,
        'name': 'TEST',
        'account_id': 2
    }
    test_kline = {
        'close_price': 85939.7,
        'ema_short': 86015.92,
        'ema_long': 85478.52
    }
    
    result = recorder.record_signal_hit(
        strategy=test_strategy,
        symbol='BTC/USDT',
        signal_type='BUY_LONG',
        signal_source='ema_9_26',
        signal_timeframe='15m',
        kline_data=test_kline,
        direction='long',
        executed=False,
        execution_result='SKIPPED',
        execution_reason='验证测试'
    )
    
    if result:
        print("   [OK] 数据库保存功能正常")
    else:
        print("   [ERROR] 数据库保存功能失败")
except Exception as e:
    print(f"   [ERROR] 测试保存功能时出错: {e}")
    import traceback
    traceback.print_exc()
print()

# 4. 检查最近的记录
print("4. 检查最近的记录...")
try:
    connection = pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 3306),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'binance-data'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )
    cursor = connection.cursor()
    
    # 检查最近1小时的记录
    since_time = datetime.now() - timedelta(hours=1)
    cursor.execute("""
        SELECT COUNT(*) as count FROM strategy_hits
        WHERE created_at >= %s
    """, (since_time,))
    count = cursor.fetchone()['count']
    
    if count > 0:
        print(f"   [OK] 最近1小时内有 {count} 条记录")
        
        # 显示最近的记录
        cursor.execute("""
            SELECT * FROM strategy_hits
            WHERE created_at >= %s
            ORDER BY created_at DESC
            LIMIT 5
        """, (since_time,))
        records = cursor.fetchall()
        print("   最近的记录:")
        for r in records:
            print(f"      - {r['strategy_name']} | {r['symbol']} | {r['signal_type']} | {r['created_at']}")
    else:
        print("   [WARN] 最近1小时内没有记录")
        print("   [TIP] 可能原因:")
        print("      - 策略执行器没有运行")
        print("      - 市场没有EMA交叉信号")
        print("      - 信号被过滤条件过滤掉了")
    
    cursor.close()
    connection.close()
except Exception as e:
    print(f"   [ERROR] 检查记录时出错: {e}")
print()

# 5. 检查日志文件
print("5. 检查日志文件...")
logs_dir = project_root / 'logs'
if logs_dir.exists():
    log_files = list(logs_dir.glob('*.log'))
    if log_files:
        latest_log = max(log_files, key=lambda p: p.stat().st_mtime)
        print(f"   [OK] 最新日志文件: {latest_log.name}")
        print(f"   修改时间: {datetime.fromtimestamp(latest_log.stat().st_mtime)}")
        
        try:
            with open(latest_log, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                if '策略实时监控服务已启动' in content:
                    print("   [OK] 日志中有策略执行器启动记录")
                else:
                    print("   [WARN] 日志中没有策略执行器启动记录")
        except:
            pass
    else:
        print("   [WARN] 没有日志文件")
else:
    print("   [WARN] 日志目录不存在")
print()

# 总结
print("=" * 80)
print("验证完成")
print("=" * 80)
print()
print("如果所有检查都通过，但晚上还是没有数据，可能的原因:")
print("1. 市场确实没有EMA交叉信号（这是正常的，需要等待）")
print("2. 信号被过滤条件过滤掉了（检查策略配置的过滤条件）")
print("3. 策略执行器在运行，但检测逻辑有问题（查看日志）")
print()
print("建议:")
print("- 运行实时监控: python scripts/monitor_strategy_realtime.py")
print("- 查看日志: tail -f logs/scheduler_*.log")
print("- 诊断信号: python scripts/diagnose_strategy_signals.py")

