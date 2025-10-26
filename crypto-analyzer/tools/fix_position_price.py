#!/usr/bin/env python3
"""
修复模拟交易持仓价格问题
诊断并修复持仓的当前价格显示不正确的问题
"""

import yaml
import pymysql
from decimal import Decimal
from datetime import datetime

def main():
    print("=" * 80)
    print("模拟交易持仓价格诊断与修复")
    print("=" * 80)
    print()

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
        db_config = config['database']['mysql']

    # 连接数据库
    conn = pymysql.connect(
        host=db_config['host'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        cursor = conn.cursor()

        # 1. 检查当前持仓
        print("📊 步骤 1: 检查当前持仓")
        print("-" * 80)
        cursor.execute("""
            SELECT id, symbol, quantity, avg_entry_price, current_price,
                   market_value, unrealized_pnl, last_update_time
            FROM paper_trading_positions
            WHERE status = 'open'
            ORDER BY symbol
        """)
        positions = cursor.fetchall()

        if not positions:
            print("   ℹ️  没有持仓")
            return

        print(f"   找到 {len(positions)} 个持仓:")
        for pos in positions:
            print(f"\n   {pos['symbol']}:")
            print(f"      数量: {pos['quantity']}")
            print(f"      成本价: {pos['avg_entry_price']}")
            print(f"      当前价: {pos['current_price']} ⚠️")
            print(f"      市值: {pos['market_value']}")
            print(f"      未实现盈亏: {pos['unrealized_pnl']}")
            print(f"      最后更新: {pos['last_update_time']}")

        # 2. 检查价格数据源
        print("\n" + "=" * 80)
        print("📈 步骤 2: 检查价格数据源")
        print("-" * 80)

        for pos in positions:
            symbol = pos['symbol']
            print(f"\n   检查 {symbol} 的价格数据:")

            # 检查 kline_data (1分钟)
            cursor.execute("""
                SELECT close_price, open_time
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol,))
            kline_1m = cursor.fetchone()

            # 检查 kline_data (5分钟)
            cursor.execute("""
                SELECT close_price, open_time
                FROM kline_data
                WHERE symbol = %s AND timeframe = '5m'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol,))
            kline_5m = cursor.fetchone()

            # 检查 price_data
            cursor.execute("""
                SELECT price, timestamp
                FROM price_data
                WHERE symbol = %s
                ORDER BY timestamp DESC LIMIT 1
            """, (symbol,))
            price_data = cursor.fetchone()

            if kline_1m:
                print(f"      ✅ kline_data (1m): {kline_1m['close_price']} @ {kline_1m['open_time']}")
            else:
                print(f"      ❌ kline_data (1m): 无数据")

            if kline_5m:
                print(f"      ✅ kline_data (5m): {kline_5m['close_price']} @ {kline_5m['open_time']}")
            else:
                print(f"      ❌ kline_data (5m): 无数据")

            if price_data:
                print(f"      ✅ price_data: {price_data['price']} @ {price_data['timestamp']}")
            else:
                print(f"      ❌ price_data: 无数据")

            # 确定最佳价格
            latest_price = None
            source = None
            if kline_1m:
                latest_price = kline_1m['close_price']
                source = "kline_data (1m)"
            elif kline_5m:
                latest_price = kline_5m['close_price']
                source = "kline_data (5m)"
            elif price_data:
                latest_price = price_data['price']
                source = "price_data"

            if latest_price:
                print(f"      💡 选择价格: {latest_price} (来源: {source})")

                # 比较当前价格
                current_price = pos['current_price']
                if current_price is None or abs(float(current_price) - float(latest_price)) / float(latest_price) > 0.01:
                    print(f"      ⚠️  价格差异过大或为空! 持仓显示: {current_price}, 最新价格: {latest_price}")
                else:
                    print(f"      ✅ 价格正常")
            else:
                print(f"      ❌ 所有数据源都没有价格数据!")

        # 3. 执行修复
        print("\n" + "=" * 80)
        print("🔧 步骤 3: 执行修复")
        print("-" * 80)

        confirm = input("\n是否更新所有持仓的当前价格? (y/n): ")
        if confirm.lower() != 'y':
            print("取消修复")
            return

        updated_count = 0
        for pos in positions:
            symbol = pos['symbol']
            quantity = Decimal(str(pos['quantity']))
            avg_cost = Decimal(str(pos['avg_entry_price']))

            # 获取最新价格
            cursor.execute("""
                SELECT close_price FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol,))
            result = cursor.fetchone()

            if not result:
                cursor.execute("""
                    SELECT close_price FROM kline_data
                    WHERE symbol = %s AND timeframe = '5m'
                    ORDER BY open_time DESC LIMIT 1
                """, (symbol,))
                result = cursor.fetchone()

            if not result:
                cursor.execute("""
                    SELECT price as close_price FROM price_data
                    WHERE symbol = %s
                    ORDER BY timestamp DESC LIMIT 1
                """, (symbol,))
                result = cursor.fetchone()

            if not result:
                print(f"   ❌ {symbol}: 无法获取价格，跳过")
                continue

            current_price = Decimal(str(result['close_price']))
            market_value = current_price * quantity
            unrealized_pnl = (current_price - avg_cost) * quantity
            unrealized_pnl_pct = ((current_price - avg_cost) / avg_cost * 100)

            # 更新持仓
            cursor.execute("""
                UPDATE paper_trading_positions
                SET current_price = %s,
                    market_value = %s,
                    unrealized_pnl = %s,
                    unrealized_pnl_pct = %s,
                    last_update_time = %s
                WHERE id = %s
            """, (current_price, market_value, unrealized_pnl, unrealized_pnl_pct,
                  datetime.now(), pos['id']))

            print(f"   ✅ {symbol}: 更新价格从 {pos['current_price']} → {current_price}")
            updated_count += 1

        # 更新账户总盈亏
        cursor.execute("""
            SELECT account_id FROM paper_trading_positions WHERE status = 'open' LIMIT 1
        """)
        result = cursor.fetchone()
        if result:
            account_id = result['account_id']

            # 计算总未实现盈亏
            cursor.execute("""
                SELECT COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl
                FROM paper_trading_positions
                WHERE account_id = %s AND status = 'open'
            """, (account_id,))
            result = cursor.fetchone()
            total_unrealized_pnl = result['total_unrealized_pnl']

            # 更新账户
            cursor.execute("""
                UPDATE paper_trading_accounts
                SET unrealized_pnl = %s,
                    total_profit_loss = realized_pnl + %s,
                    total_profit_loss_pct = ((realized_pnl + %s) / initial_balance) * 100
                WHERE id = %s
            """, (total_unrealized_pnl, total_unrealized_pnl, total_unrealized_pnl, account_id))

            # 更新总权益
            cursor.execute("""
                SELECT
                    current_balance,
                    COALESCE(SUM(market_value), 0) as total_position_value
                FROM paper_trading_accounts a
                LEFT JOIN paper_trading_positions p ON a.id = p.account_id AND p.status = 'open'
                WHERE a.id = %s
                GROUP BY a.id, a.current_balance
            """, (account_id,))
            result = cursor.fetchone()
            if result:
                total_equity = Decimal(str(result['current_balance'])) + Decimal(str(result['total_position_value'] or 0))
                cursor.execute("""
                    UPDATE paper_trading_accounts SET total_equity = %s WHERE id = %s
                """, (total_equity, account_id))

            print(f"\n   ✅ 更新账户统计: 未实现盈亏 = {total_unrealized_pnl}, 总权益 = {total_equity}")

        conn.commit()

        print("\n" + "=" * 80)
        print(f"✅ 修复完成! 更新了 {updated_count} 个持仓")
        print("=" * 80)
        print()
        print("💡 建议:")
        print("   1. 刷新模拟交易页面 (Ctrl+Shift+R)")
        print("   2. 确保数据采集器正在运行，以便持续更新价格")
        print("   3. 如果问题仍然存在，检查数据采集器配置")
        print()

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()

    finally:
        conn.close()

if __name__ == '__main__':
    main()
