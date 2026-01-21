#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署验证脚本 - 检查所有优化是否生效
运行: python3 verify_deployment.py
"""

import sys
import io
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import pymysql
from datetime import datetime, timedelta
from typing import Dict, List
import json
import os
from dotenv import load_dotenv

# 加载.env文件
load_dotenv()

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

def print_section(title: str):
    """打印章节标题"""
    print("\n" + "="*60)
    print(f"  {title}")
    print("="*60)

def verify_signal_components(cursor) -> Dict:
    """验证signal_components是否被记录"""
    print_section("1. 验证信号组件记录")

    # 检查最近的记录
    cursor.execute("""
        SELECT
            COUNT(*) as total,
            SUM(CASE WHEN signal_components IS NOT NULL THEN 1 ELSE 0 END) as with_components,
            SUM(CASE WHEN entry_score IS NOT NULL THEN 1 ELSE 0 END) as with_score
        FROM futures_positions
        WHERE source = 'smart_trader'
            AND open_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    """)
    stats = cursor.fetchone()

    total = int(stats['total'])
    with_components = int(stats['with_components'])
    with_score = int(stats['with_score'])

    print(f"最近24小时交易数: {total}")
    print(f"记录signal_components: {with_components} ({with_components/total*100 if total > 0 else 0:.1f}%)")
    print(f"记录entry_score: {with_score} ({with_score/total*100 if total > 0 else 0:.1f}%)")

    if with_components > 0:
        print("\n✅ 信号组件记录正常")

        # 显示最新一条
        cursor.execute("""
            SELECT symbol, position_side, entry_score, signal_components, open_time
            FROM futures_positions
            WHERE source = 'smart_trader'
                AND signal_components IS NOT NULL
            ORDER BY open_time DESC
            LIMIT 1
        """)
        latest = cursor.fetchone()
        if latest:
            print(f"\n最新记录:")
            print(f"  交易对: {latest['symbol']}")
            print(f"  方向: {latest['position_side']}")
            print(f"  得分: {latest['entry_score']}")
            print(f"  组件: {latest['signal_components']}")
            print(f"  时间: {latest['open_time']}")
    else:
        print("\n❌ 警告: 没有记录signal_components")
        print("   请检查smart_trader_service.py是否已重启并加载最新代码")

    return {'status': 'ok' if with_components > 0 else 'warning', 'coverage': with_components/total*100 if total > 0 else 0}

def verify_weights(cursor) -> Dict:
    """验证信号权重配置"""
    print_section("2. 验证信号权重配置")

    cursor.execute("SELECT COUNT(*) as count FROM signal_scoring_weights")
    count = cursor.fetchone()['count']

    print(f"配置的权重数量: {count}")

    if count > 0:
        cursor.execute("""
            SELECT signal_component, weight_long, weight_short, last_adjusted
            FROM signal_scoring_weights
            WHERE is_active = 1
            ORDER BY last_adjusted DESC
            LIMIT 5
        """)
        weights = cursor.fetchall()

        print("\n最近更新的权重:")
        for w in weights:
            print(f"  {w['signal_component']:20s} LONG:{w['weight_long']:5.1f} SHORT:{w['weight_short']:5.1f} (更新: {w['last_adjusted']})")

        print("\n✅ 信号权重配置正常")
        return {'status': 'ok', 'count': count}
    else:
        print("\n❌ 警告: 没有配置权重")
        return {'status': 'error', 'count': 0}

def verify_risk_params(cursor) -> Dict:
    """验证交易对风险参数"""
    print_section("3. 验证交易对风险参数")

    cursor.execute("SELECT COUNT(*) as count FROM symbol_risk_params")
    count = cursor.fetchone()['count']

    print(f"配置的交易对数量: {count}")

    if count > 0:
        cursor.execute("""
            SELECT
                symbol,
                long_take_profit_pct,
                long_stop_loss_pct,
                position_multiplier,
                win_rate,
                total_trades,
                total_pnl,
                last_optimized
            FROM symbol_risk_params
            ORDER BY total_pnl DESC
            LIMIT 10
        """)
        params = cursor.fetchall()

        print("\n表现最好的10个交易对:")
        print(f"{'交易对':<15} {'止盈':<8} {'止损':<8} {'仓位倍数':<10} {'胜率':<8} {'交易数':<8}")
        print("-" * 80)
        for p in params:
            print(f"{p['symbol']:<15} "
                  f"{float(p['long_take_profit_pct'] or 0)*100:>6.2f}% "
                  f"{float(p['long_stop_loss_pct'] or 0)*100:>6.2f}% "
                  f"{float(p['position_multiplier'] or 1.0):>8.2f}x "
                  f"{float(p['win_rate'] or 0)*100:>6.1f}% "
                  f"{int(p['total_trades'] or 0):>6}")

        print("\n✅ 交易对风险参数配置正常")
        return {'status': 'ok', 'count': count}
    else:
        print("\n❌ 警告: 没有配置交易对参数")
        return {'status': 'error', 'count': 0}

def verify_market_observations(cursor) -> Dict:
    """验证市场观察记录"""
    print_section("4. 验证市场观察记录")

    cursor.execute("""
        SELECT COUNT(*) as count
        FROM market_observations
        WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    """)
    count = cursor.fetchone()['count']

    print(f"最近24小时观察记录: {count}")

    if count > 0:
        cursor.execute("""
            SELECT
                timestamp,
                overall_trend,
                market_strength,
                btc_price,
                eth_price,
                warnings
            FROM market_observations
            ORDER BY timestamp DESC
            LIMIT 3
        """)
        obs = cursor.fetchall()

        print("\n最新3次市场观察:")
        for o in obs:
            warnings_count = len(o['warnings'].split(',')) if o['warnings'] else 0
            print(f"  {o['timestamp']} | {o['overall_trend']:8s} | 强度:{float(o['market_strength'] or 0):5.1f} | "
                  f"BTC:${float(o['btc_price'] or 0):,.2f} | ETH:${float(o['eth_price'] or 0):,.2f} | "
                  f"预警:{warnings_count}")

        # 计算观察频率
        hours = 24
        expected_per_5min = hours * 60 / 5  # 5分钟一次
        coverage = count / expected_per_5min * 100

        print(f"\n观察频率: {count}/{int(expected_per_5min)} = {coverage:.1f}%")

        if coverage > 80:
            print("✅ 市场观察记录正常")
            return {'status': 'ok', 'coverage': coverage}
        else:
            print("⚠️ 市场观察覆盖率偏低，检查cron任务是否正常运行")
            return {'status': 'warning', 'coverage': coverage}
    else:
        print("\n❌ 警告: 没有市场观察记录")
        print("   请运行: python3 run_market_observer.py")
        return {'status': 'error', 'count': 0}

def verify_market_regime(cursor) -> Dict:
    """验证市场状态记录"""
    print_section("5. 验证6小时市场状态记录")

    # market_regime_states表不存在，跳过此检查
    print("⚠️ 提示: market_regime_states表暂未使用，跳过检查")
    return {'status': 'info', 'message': 'Table not used'}

def verify_optimization_history(cursor) -> Dict:
    """验证优化历史记录"""
    print_section("6. 验证优化历史记录")

    cursor.execute("""
        SELECT COUNT(*) as count
        FROM optimization_history
        WHERE optimized_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    """)
    count = cursor.fetchone()['count']

    print(f"最近7天优化记录: {count}")

    if count > 0:
        cursor.execute("""
            SELECT
                optimized_at,
                optimization_type,
                target_name,
                param_name,
                old_value,
                new_value,
                reason
            FROM optimization_history
            ORDER BY optimized_at DESC
            LIMIT 5
        """)
        history = cursor.fetchall()

        print("\n最近5次优化:")
        for h in history:
            print(f"  {h['optimized_at']} | {h['optimization_type']:20s} | "
                  f"{h['target_name'] or ''} - {h['param_name'] or ''} | {h['reason'] or ''}")

        print("\n✅ 优化历史记录正常")
        return {'status': 'ok', 'count': count}
    else:
        print("\n⚠️ 提示: 还没有优化历史记录")
        print("   这是正常的，优化会在定时任务运行后产生")
        return {'status': 'info', 'count': 0}

def verify_global_config(cursor) -> Dict:
    """验证全局配置"""
    print_section("7. 验证全局止盈止损配置")

    cursor.execute("""
        SELECT param_key, param_value, updated_at
        FROM adaptive_params
        WHERE param_type = 'global'
            AND param_key IN ('long_take_profit_pct', 'long_stop_loss_pct',
                               'short_take_profit_pct', 'short_stop_loss_pct')
        ORDER BY param_key
    """)
    configs = cursor.fetchall()

    if configs:
        print("\n全局止盈止损设置:")
        for c in configs:
            value = float(c['param_value'])
            print(f"  {c['param_key']:25s} = {value*100:>6.2f}% (更新: {c['updated_at']})")

        # 检查是否已更新为新配置
        long_tp = next((float(c['param_value']) for c in configs if c['param_key'] == 'long_take_profit_pct'), None)
        long_sl = next((float(c['param_value']) for c in configs if c['param_key'] == 'long_stop_loss_pct'), None)

        if long_tp and long_sl:
            if long_tp >= 0.05 and long_sl <= 0.02:
                print("\n✅ 全局配置已优化 (TP≥5%, SL≤2%)")
                return {'status': 'ok', 'tp': long_tp*100, 'sl': long_sl*100}
            else:
                print(f"\n⚠️ 全局配置未优化 (TP={long_tp*100:.1f}%, SL={long_sl*100:.1f}%)")
                print("   建议运行: python3 update_stop_loss_take_profit.py")
                return {'status': 'warning', 'tp': long_tp*100, 'sl': long_sl*100}
    else:
        print("\n❌ 错误: 没有找到全局配置")
        return {'status': 'error'}

def main():
    print("\n" + "█"*60)
    print("█" + " "*58 + "█")
    print("█" + "  超级大脑部署验证工具".center(56) + "█")
    print("█" + " "*58 + "█")
    print("█"*60)

    conn = None
    try:
        # 连接数据库
        print("\n正在连接数据库...")
        conn = pymysql.connect(**DB_CONFIG)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        print("✅ 数据库连接成功")

        # 执行所有验证
        results = {}
        results['signal_components'] = verify_signal_components(cursor)
        results['weights'] = verify_weights(cursor)
        results['risk_params'] = verify_risk_params(cursor)
        results['market_observations'] = verify_market_observations(cursor)
        results['market_regime'] = verify_market_regime(cursor)
        results['optimization_history'] = verify_optimization_history(cursor)
        results['global_config'] = verify_global_config(cursor)

        # 生成总结报告
        print_section("📊 验证总结报告")

        total_checks = len(results)
        ok_count = sum(1 for r in results.values() if r.get('status') == 'ok')
        warning_count = sum(1 for r in results.values() if r.get('status') == 'warning')
        error_count = sum(1 for r in results.values() if r.get('status') == 'error')

        print(f"\n总检查项: {total_checks}")
        print(f"✅ 正常: {ok_count}")
        print(f"⚠️ 警告: {warning_count}")
        print(f"❌ 错误: {error_count}")

        # 判断总体状态
        if error_count == 0 and warning_count == 0:
            print("\n" + "🎉"*20)
            print("   所有检查通过！超级大脑已完全激活！".center(40))
            print("🎉"*20)
        elif error_count == 0:
            print("\n✅ 基本功能正常，但有一些警告需要关注")
        else:
            print("\n❌ 发现严重问题，请按照上述提示修复")

        # 下一步建议
        print("\n" + "="*60)
        print("  下一步建议")
        print("="*60)

        if results['signal_components']['status'] != 'ok':
            print("\n1. 信号组件记录异常:")
            print("   cd /path/to/crypto-analyzer")
            print("   git pull")
            print("   pkill -f smart_trader_service.py")
            print("   nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &")

        if results['market_observations']['status'] == 'error':
            print("\n2. 市场观察未启动:")
            print("   python3 run_market_observer.py")
            print("   # 然后设置cron任务: */5 * * * * python3 run_market_observer.py")

        if results['market_regime']['status'] == 'error':
            print("\n3. 市场状态分析未启动:")
            print("   python3 run_market_regime_analysis.py")
            print("   # 然后设置cron任务: 0 */6 * * * python3 run_market_regime_analysis.py")

        if ok_count >= 5:
            print("\n✅ 大部分功能已就绪，可以开始监控交易表现:")
            print("   tail -f logs/smart_trader_*.log | grep -E '开仓|平仓'")
            print("   python3 analyze_smart_brain_2days.py")

        cursor.close()

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return 1

    finally:
        if conn:
            conn.close()

    return 0

if __name__ == '__main__':
    sys.exit(main())
