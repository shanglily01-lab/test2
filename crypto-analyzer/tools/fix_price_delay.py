#!/usr/bin/env python3
"""
修复价格延迟问题
将价格数据源从5分钟K线改为1分钟K线
"""

import sys
from pathlib import Path

def fix_price_source():
    """修复价格数据源"""

    print("=" * 80)
    print("修复价格延迟问题")
    print("=" * 80)
    print()

    # 目标文件
    file_path = Path(__file__).parent / 'app' / 'api' / 'enhanced_dashboard.py'

    if not file_path.exists():
        print(f"❌ 文件不存在: {file_path}")
        return False

    print(f"📄 目标文件: {file_path}")
    print()

    # 读取文件
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content

    # 统计需要修改的地方
    count_5m = content.count("get_latest_kline(symbol, '5m')")
    count_5m_double = content.count('get_latest_kline(symbol, "5m")')

    print(f"发现需要修改的位置:")
    print(f"  - get_latest_kline(symbol, '5m'): {count_5m} 处")
    print(f"  - get_latest_kline(symbol, \"5m\"): {count_5m_double} 处")
    print()

    if count_5m == 0 and count_5m_double == 0:
        print("✅ 文件已经是最新的，无需修改")
        return True

    # 执行替换
    print("开始修改...")
    content = content.replace("get_latest_kline(symbol, '5m')", "get_latest_kline(symbol, '1m')")
    content = content.replace('get_latest_kline(symbol, "5m")', 'get_latest_kline(symbol, "1m")')

    # 验证修改
    new_count_1m = content.count("get_latest_kline(symbol, '1m')") + content.count('get_latest_kline(symbol, "1m")')

    print(f"修改后:")
    print(f"  - get_latest_kline(symbol, '1m'): {new_count_1m} 处")
    print()

    # 备份原文件
    backup_path = file_path.with_suffix('.py.bak')
    print(f"📦 创建备份: {backup_path}")
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(original_content)

    # 写入修改后的文件
    print(f"💾 保存修改...")
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print()
    print("=" * 80)
    print("✅ 修复完成！")
    print("=" * 80)
    print()
    print("修改内容:")
    print("  - 价格数据源: 5分钟K线 → 1分钟K线")
    print("  - 价格延迟: 最多5分钟 → 最多1分钟")
    print()
    print("下一步:")
    print("  1. 确保数据采集器正在运行")
    print("     python start_scheduler.py")
    print()
    print("  2. 重启Web服务")
    print("     python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload")
    print()
    print("  3. 访问仪表板验证")
    print("     http://localhost:8000")
    print()
    print("备注:")
    print(f"  - 原文件已备份到: {backup_path}")
    print("  - 如需恢复，复制备份文件覆盖原文件即可")
    print()

    return True


def check_kline_data():
    """检查1分钟K线数据是否存在"""
    print("=" * 80)
    print("检查1分钟K线数据")
    print("=" * 80)
    print()

    try:
        import yaml
        from app.database.db_service import DatabaseService

        # 加载配置
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        db_config = config.get('database', {})
        db_service = DatabaseService(db_config)

        # 检查1分钟K线数据
        symbols = config.get('symbols', ['BTC/USDT'])

        print(f"检查币种: {', '.join(symbols[:3])}")
        print()

        has_data = False
        for symbol in symbols[:3]:
            klines = db_service.get_latest_klines(symbol, '1m', limit=5)

            if klines and len(klines) > 0:
                latest = klines[0]
                print(f"  {symbol:12s} ✅ 有数据 - 最新时间: {latest.timestamp}")
                has_data = True
            else:
                print(f"  {symbol:12s} ⚠️  无数据")

        print()

        if has_data:
            print("✅ 数据库中有1分钟K线数据")
            print()
            print("说明: 修复后价格延迟将降到1分钟")
        else:
            print("⚠️  数据库中没有1分钟K线数据")
            print()
            print("请先启动数据采集器:")
            print("  python start_scheduler.py")
            print()
            print("等待1-2分钟后数据采集器会自动采集1分钟K线数据")

    except ImportError as e:
        print(f"⚠️  无法检查数据: {e}")
        print()
        print("说明: 这个检查在Windows本地运行更准确")
    except Exception as e:
        print(f"⚠️  检查失败: {e}")


if __name__ == '__main__':
    # 执行修复
    success = fix_price_source()

    if success:
        print()
        # 尝试检查数据
        try:
            check_kline_data()
        except:
            pass
