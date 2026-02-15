#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同步config.yaml中的交易对到数据库
"""
import sys
import os
from dotenv import load_dotenv
import yaml
import pymysql

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()


def load_symbols_from_config():
    """从配置文件加载交易对列表"""
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        symbols = config.get('symbols', [])
        return symbols
    except Exception as e:
        print(f"❌ 加载配置文件失败: {e}")
        return []


def sync_symbols():
    """同步交易对到数据库"""
    # 加载交易对
    symbols = load_symbols_from_config()
    if not symbols:
        print("❌ 配置文件中没有交易对")
        return False

    print(f"\n📊 共找到 {len(symbols)} 个交易对")

    # 数据库配置
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        # 插入或更新交易对
        success_count = 0
        for symbol in symbols:
            try:
                cursor.execute("""
                    INSERT INTO trading_symbols (symbol, exchange, enabled)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        enabled = 1,
                        updated_at = CURRENT_TIMESTAMP
                """, (symbol, 'binance_futures', 1))
                success_count += 1
            except Exception as e:
                print(f"⚠️ {symbol} 同步失败: {e}")

        conn.commit()

        print(f"\n✅ 成功同步 {success_count}/{len(symbols)} 个交易对到数据库")

        # 显示统计
        cursor.execute("SELECT COUNT(*) FROM trading_symbols WHERE enabled = 1")
        total = cursor.fetchone()[0]
        print(f"📊 数据库中共有 {total} 个启用的交易对")

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        print(f"❌ 数据库操作失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    print("\n" + "="*80)
    print("🔄 同步交易对到数据库")
    print("="*80)

    if sync_symbols():
        print("\n" + "="*80)
        print("✅ 同步完成")
        print("="*80)
    else:
        print("\n" + "="*80)
        print("❌ 同步失败")
        print("="*80)
