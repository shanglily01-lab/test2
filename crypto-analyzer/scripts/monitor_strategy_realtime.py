"""
实时监控策略执行器状态和信号检测
每隔一段时间检查策略执行器是否在运行，是否有信号被检测到
"""
import sys
from pathlib import Path
import time
from datetime import datetime, timedelta

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
import yaml
import subprocess
import platform

config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

def check_process_running():
    """检查策略执行器进程是否在运行"""
    try:
        if platform.system() == 'Windows':
            result = subprocess.run(['tasklist'], capture_output=True, text=True, encoding='gbk')
            return 'strategy_scheduler' in result.stdout or 'python' in result.stdout
        else:
            result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
            return 'strategy_scheduler' in result.stdout
    except:
        return False

def check_database_hits(minutes=5):
    """检查最近N分钟内是否有新的策略命中记录"""
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
        
        since_time = datetime.now() - timedelta(minutes=minutes)
        cursor.execute("""
            SELECT COUNT(*) as count FROM strategy_hits
            WHERE created_at >= %s
        """, (since_time,))
        count = cursor.fetchone()['count']
        
        cursor.close()
        connection.close()
        return count
    except Exception as e:
        print(f"检查数据库时出错: {e}")
        return -1

def check_latest_logs():
    """检查最新的日志文件，查找策略执行器的活动"""
    logs_dir = project_root / 'logs'
    if not logs_dir.exists():
        return "日志目录不存在"
    
    log_files = list(logs_dir.glob('*.log'))
    if not log_files:
        return "没有日志文件"
    
    # 找到最新的日志文件
    latest_log = max(log_files, key=lambda p: p.stat().st_mtime)
    
    try:
        with open(latest_log, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            # 检查最后100行
            recent_lines = lines[-100:] if len(lines) > 100 else lines
            content = ''.join(recent_lines)
            
            # 查找关键信息
            if '策略实时监控服务已启动' in content or 'strategy_scheduler' in content:
                if '检测到' in content and '信号' in content:
                    return "✅ 策略执行器运行中，已检测到信号"
                elif '未检测到交叉信号' in content or '未触发交易信号' in content:
                    return "⚠️ 策略执行器运行中，但未检测到信号（正常，需要等待EMA交叉）"
                else:
                    return "✅ 策略执行器运行中，等待信号中..."
            else:
                return "❌ 日志中没有策略执行器记录"
    except Exception as e:
        return f"读取日志时出错: {e}"

def main():
    print("=" * 80)
    print("策略执行器实时监控")
    print("=" * 80)
    print(f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 1. 检查进程
    print("1. 检查策略执行器进程...")
    process_running = check_process_running()
    if process_running:
        print("   ✅ 找到Python进程（可能是策略执行器）")
    else:
        print("   ❌ 未找到策略执行器进程")
        print("   💡 提示: 运行 'python app/strategy_scheduler.py' 启动策略执行器")
    print()
    
    # 2. 检查数据库记录
    print("2. 检查数据库记录...")
    hits_5min = check_database_hits(5)
    hits_30min = check_database_hits(30)
    hits_24h = check_database_hits(24 * 60)
    
    print(f"   最近5分钟: {hits_5min} 条记录")
    print(f"   最近30分钟: {hits_30min} 条记录")
    print(f"   最近24小时: {hits_24h} 条记录")
    
    if hits_5min > 0:
        print("   ✅ 有新的策略命中记录！")
    elif hits_30min > 0:
        print("   ⚠️ 最近30分钟内有记录，但最近5分钟没有（可能市场没有信号）")
    elif hits_24h > 0:
        print("   ⚠️ 今天有记录，但最近30分钟没有（可能市场没有信号）")
    else:
        print("   ❌ 最近24小时内没有任何记录")
        print("   💡 可能原因:")
        print("      - 策略执行器没有运行")
        print("      - 市场没有EMA交叉信号（这是正常的）")
        print("      - 信号被过滤条件过滤掉了")
    print()
    
    # 3. 检查日志
    print("3. 检查日志文件...")
    log_status = check_latest_logs()
    print(f"   {log_status}")
    print()
    
    # 总结
    print("=" * 80)
    print("总结")
    print("=" * 80)
    
    if not process_running:
        print("❌ 策略执行器可能没有运行！")
        print("   请运行: python app/strategy_scheduler.py")
    elif hits_24h == 0:
        print("⚠️ 策略执行器可能在运行，但没有检测到信号")
        print("   这是正常的，因为:")
        print("   1. 市场可能没有EMA交叉信号")
        print("   2. 信号可能被过滤条件过滤掉了")
        print("   3. 策略执行器可能刚启动，还没有检测到信号")
        print()
        print("   建议:")
        print("   - 运行诊断脚本: python scripts/diagnose_strategy_signals.py")
        print("   - 查看日志文件: tail -f logs/scheduler_*.log")
    else:
        print("✅ 系统运行正常！")
        print(f"   最近24小时内有 {hits_24h} 条策略命中记录")
    
    print()

if __name__ == '__main__':
    main()

