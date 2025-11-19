#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复模拟合约冻结保证金问题
检查并清理异常的冻结余额
"""

import sys
import os
from pathlib import Path

# 设置UTF-8编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
import yaml
from decimal import Decimal

# 加载配置文件
config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

def check_frozen_balance():
    """检查冻结保证金问题"""
    db_config = config.get('database', {}).get('mysql', {})
    connection = pymysql.connect(**db_config)
    cursor = connection.cursor(pymysql.cursors.DictCursor)
    
    try:
        print("=" * 60)
        print("检查模拟合约账户冻结保证金问题")
        print("=" * 60)
        
        # 1. 获取所有账户信息
        cursor.execute("""
            SELECT id, current_balance, frozen_balance, total_equity
            FROM paper_trading_accounts
            WHERE frozen_balance > 0
            ORDER BY id
        """)
        accounts = cursor.fetchall()
        
        if not accounts:
            print("✓ 没有账户存在冻结保证金")
            return
        
        for account in accounts:
            account_id = account['id']
            frozen_balance = float(account['frozen_balance'])
            current_balance = float(account['current_balance'])
            
            print(f"\n账户 ID: {account_id}")
            print(f"  当前余额: {current_balance:.2f} USDT")
            print(f"  冻结余额: {frozen_balance:.2f} USDT")
            print(f"  总权益: {account['total_equity']:.2f} USDT")
            
            # 2. 检查持仓单占用的保证金
            cursor.execute("""
                SELECT SUM(margin) as total_margin
                FROM futures_positions
                WHERE account_id = %s AND status = 'open'
            """, (account_id,))
            position_result = cursor.fetchone()
            position_margin = float(position_result['total_margin'] or 0)
            
            print(f"  持仓单占用保证金: {position_margin:.2f} USDT")
            
            # 3. 检查未成交订单占用的保证金
            cursor.execute("""
                SELECT SUM(margin + COALESCE(fee, 0)) as total_frozen
                FROM futures_orders
                WHERE account_id = %s 
                AND status IN ('PENDING', 'PARTIALLY_FILLED')
                AND order_type = 'LIMIT'
            """, (account_id,))
            order_result = cursor.fetchone()
            order_frozen = float(order_result['total_frozen'] or 0)
            
            print(f"  未成交订单占用保证金: {order_frozen:.2f} USDT")
            
            # 4. 计算应该的冻结余额
            expected_frozen = position_margin + order_frozen
            actual_frozen = frozen_balance
            difference = actual_frozen - expected_frozen
            
            print(f"  预期冻结余额: {expected_frozen:.2f} USDT")
            print(f"  实际冻结余额: {actual_frozen:.2f} USDT")
            print(f"  差异: {difference:.2f} USDT")
            
            # 5. 列出所有未成交订单
            cursor.execute("""
                SELECT order_id, symbol, side, order_type, status, 
                       margin, fee, (margin + COALESCE(fee, 0)) as total_frozen,
                       created_at
                FROM futures_orders
                WHERE account_id = %s 
                AND status IN ('PENDING', 'PARTIALLY_FILLED')
                ORDER BY created_at DESC
            """, (account_id,))
            pending_orders = cursor.fetchall()
            
            if pending_orders:
                print(f"\n  未成交订单列表 ({len(pending_orders)} 个):")
                for order in pending_orders:
                    print(f"    - {order['order_id']}: {order['symbol']} {order['side']} "
                          f"({order['order_type']}) - 冻结: {order['total_frozen']:.2f} USDT "
                          f"状态: {order['status']} 创建时间: {order['created_at']}")
            else:
                print(f"\n  ✓ 没有未成交订单")
            
            # 6. 列出所有持仓
            cursor.execute("""
                SELECT id, symbol, position_side, quantity, margin, entry_price, open_time
                FROM futures_positions
                WHERE account_id = %s AND status = 'open'
                ORDER BY open_time DESC
            """, (account_id,))
            positions = cursor.fetchall()
            
            if positions:
                print(f"\n  持仓列表 ({len(positions)} 个):")
                for pos in positions:
                    print(f"    - 持仓ID {pos['id']}: {pos['symbol']} {pos['position_side']} "
                          f"数量: {pos['quantity']} 保证金: {pos['margin']:.2f} USDT "
                          f"开仓价: {pos['entry_price']:.2f} 开仓时间: {pos['open_time']}")
            else:
                print(f"\n  ✓ 没有持仓")
            
            # 7. 如果存在差异，提示修复
            if abs(difference) > 0.01:  # 允许0.01的误差
                print(f"\n  ⚠️  发现异常：冻结余额与实际占用不匹配！")
                print(f"     建议修复：将冻结余额调整为 {expected_frozen:.2f} USDT")
                
    finally:
        cursor.close()
        connection.close()


def fix_frozen_balance(account_id: int, dry_run: bool = True):
    """修复冻结保证金"""
    db_config = config.get('database', {}).get('mysql', {})
    connection = pymysql.connect(**db_config)
    cursor = connection.cursor(pymysql.cursors.DictCursor)
    
    try:
        print(f"\n{'=' * 60}")
        print(f"{'[模拟运行]' if dry_run else '[实际修复]'} 修复账户 {account_id} 的冻结保证金")
        print("=" * 60)
        
        # 1. 计算应该的冻结余额
        cursor.execute("""
            SELECT SUM(margin) as total_margin
            FROM futures_positions
            WHERE account_id = %s AND status = 'open'
        """, (account_id,))
        position_result = cursor.fetchone()
        position_margin = float(position_result['total_margin'] or 0)
        
        cursor.execute("""
            SELECT SUM(margin + COALESCE(fee, 0)) as total_frozen
            FROM futures_orders
            WHERE account_id = %s 
            AND status IN ('PENDING', 'PARTIALLY_FILLED')
            AND order_type = 'LIMIT'
        """, (account_id,))
        order_result = cursor.fetchone()
        order_frozen = float(order_result['total_frozen'] or 0)
        
        expected_frozen = position_margin + order_frozen
        
        # 2. 获取当前冻结余额
        cursor.execute("""
            SELECT frozen_balance, current_balance
            FROM paper_trading_accounts
            WHERE id = %s
        """, (account_id,))
        account = cursor.fetchone()
        
        if not account:
            print(f"❌ 账户 {account_id} 不存在")
            return
        
        current_frozen = float(account['frozen_balance'])
        current_balance = float(account['current_balance'])
        difference = current_frozen - expected_frozen
        
        print(f"\n当前状态:")
        print(f"  当前余额: {current_balance:.2f} USDT")
        print(f"  冻结余额: {current_frozen:.2f} USDT")
        print(f"  持仓占用: {position_margin:.2f} USDT")
        print(f"  订单占用: {order_frozen:.2f} USDT")
        print(f"  预期冻结: {expected_frozen:.2f} USDT")
        print(f"  差异: {difference:.2f} USDT")
        
        if abs(difference) < 0.01:
            print(f"\n✓ 冻结余额正常，无需修复")
            return
        
        # 3. 修复冻结余额
        if difference > 0:
            # 冻结余额过多，需要释放
            release_amount = difference
            new_frozen = expected_frozen
            new_balance = current_balance + release_amount
            print(f"\n修复方案:")
            print(f"  释放冻结余额: {release_amount:.2f} USDT")
            print(f"  新余额: {new_balance:.2f} USDT")
            print(f"  新冻结余额: {new_frozen:.2f} USDT")
            
            if not dry_run:
                cursor.execute("""
                    UPDATE paper_trading_accounts
                    SET current_balance = %s,
                        frozen_balance = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """, (new_balance, new_frozen, account_id))
                
                # 更新总权益
                cursor.execute("""
                    UPDATE paper_trading_accounts a
                    SET a.total_equity = a.current_balance + a.frozen_balance + COALESCE((
                        SELECT SUM(p.unrealized_pnl) 
                        FROM futures_positions p 
                        WHERE p.account_id = a.id AND p.status = 'open'
                    ), 0)
                    WHERE a.id = %s
                """, (account_id,))
                
                connection.commit()
                print(f"\n✓ 修复完成！")
            else:
                print(f"\n[模拟运行] 如需实际修复，请运行: python scripts/fix_frozen_balance.py --fix --account-id {account_id}")
        else:
            # 冻结余额不足（这种情况很少见）
            print(f"\n⚠️  冻结余额不足，这种情况很少见，请手动检查")
        
    except Exception as e:
        print(f"\n❌ 修复失败: {e}")
        import traceback
        traceback.print_exc()
        if not dry_run:
            connection.rollback()
    finally:
        cursor.close()
        connection.close()


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='修复模拟合约冻结保证金问题')
    parser.add_argument('--check', action='store_true', help='检查冻结保证金问题')
    parser.add_argument('--fix', action='store_true', help='修复冻结保证金问题')
    parser.add_argument('--account-id', type=int, help='指定账户ID（修复时必需）')
    parser.add_argument('--dry-run', action='store_true', default=True, help='模拟运行（默认）')
    parser.add_argument('--force', action='store_true', help='实际执行修复（不使用dry-run）')
    
    args = parser.parse_args()
    
    if args.check:
        check_frozen_balance()
    elif args.fix:
        if not args.account_id:
            print("❌ 修复时需要指定账户ID: --account-id <id>")
            sys.exit(1)
        
        dry_run = not args.force
        fix_frozen_balance(args.account_id, dry_run=dry_run)
    else:
        # 默认执行检查
        check_frozen_balance()
        print("\n" + "=" * 60)
        print("提示:")
        print("  检查问题: python scripts/fix_frozen_balance.py --check")
        print("  模拟修复: python scripts/fix_frozen_balance.py --fix --account-id <id>")
        print("  实际修复: python scripts/fix_frozen_balance.py --fix --account-id <id> --force")
        print("=" * 60)

