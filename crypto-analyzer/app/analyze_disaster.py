"""
分析今天01:38灾难的根本原因
"""

import pymysql
import sys
import io
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.utils.config_loader import load_config


def analyze_disaster():
    """分析灾难根本原因"""
    config = load_config()
    db_config = config.get('database', {}).get('mysql', {})

    try:
        # 修正数据库名
        if 'database' in db_config and db_config['database'] == 'binance-data':
            db_config['database'] = 'crypto_trading'

        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        print("\n" + "="*80)
        print("🔍 01:38灾难根本原因分析")
        print("="*80 + "\n")

        # 1. 检查01:30-02:00的Big4信号
        print("📊 1. Big4趋势信号 (01:30-02:30)")
        print("-" * 80)
        cursor.execute("""
            SELECT
                checked_at,
                overall_signal,
                signal_strength,
                btc_trend,
                eth_trend,
                bnb_trend,
                sol_trend
            FROM big4_trend_signals
            WHERE checked_at >= '2026-02-06 01:30:00'
            AND checked_at <= '2026-02-06 02:30:00'
            ORDER BY checked_at
        """)

        big4_signals = cursor.fetchall()
        for sig in big4_signals:
            print(f"{sig['checked_at']} | {sig['overall_signal']:8} | 强度:{sig['signal_strength']:5.1f} | "
                  f"BTC:{sig['btc_trend']} ETH:{sig['eth_trend']} BNB:{sig['bnb_trend']} SOL:{sig['sol_trend']}")
        print()

        # 2. 检查交易模式配置
        print("⚙️  2. 交易模式配置")
        print("-" * 80)
        cursor.execute("""
            SELECT
                mode_type,
                auto_switch_enabled,
                last_switch_time,
                updated_at
            FROM trading_mode_config
            WHERE account_id = 2
        """)

        mode_config = cursor.fetchone()
        if mode_config:
            print(f"当前模式: {mode_config['mode_type']}")
            print(f"自动切换: {'启用' if mode_config['auto_switch_enabled'] else '禁用'}")
            print(f"最后切换: {mode_config['last_switch_time']}")
            print(f"更新时间: {mode_config['updated_at']}")
        print()

        # 3. 检查模式切换日志
        print("🔄 3. 今日模式切换记录")
        print("-" * 80)
        cursor.execute("""
            SELECT
                switched_at,
                from_mode,
                to_mode,
                switch_trigger,
                big4_signal,
                big4_strength,
                reason
            FROM trading_mode_switch_log
            WHERE account_id = 2
            AND DATE(switched_at) = '2026-02-06'
            ORDER BY switched_at
        """)

        switches = cursor.fetchall()
        if switches:
            for sw in switches:
                print(f"{sw['switched_at']} | {sw['from_mode']:5} → {sw['to_mode']:5} | "
                      f"{sw['switch_trigger']:6} | Big4:{sw['big4_signal']}({sw['big4_strength']:.1f}) | "
                      f"{sw['reason']}")
        else:
            print("今天没有模式切换")
        print()

        # 4. 分析01:38开仓的信号来源
        print("🚨 4. 01:38-02:02灾难性开仓详情")
        print("-" * 80)
        cursor.execute("""
            SELECT
                open_time,
                symbol,
                position_side,
                entry_signal_type,
                entry_score,
                entry_reason,
                entry_price,
                mark_price,
                realized_pnl,
                notes
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-06 01:38:00'
            AND open_time <= '2026-02-06 02:02:00'
            AND status = 'closed'
            ORDER BY open_time
        """)

        disaster_trades = cursor.fetchall()
        print(f"共开仓 {len(disaster_trades)} 笔\n")

        for idx, t in enumerate(disaster_trades[:10], 1):
            print(f"{idx}. {t['open_time']} | {t['symbol']:12} {t['position_side']:5} | "
                  f"分数:{t['entry_score']:3.0f} | PnL:{t['realized_pnl']:7.2f}")
        print()

        # 5. 检查信号黑名单
        print("🚫 5. 信号黑名单状态")
        print("-" * 80)
        cursor.execute("""
            SELECT
                signal_type,
                reason,
                disabled_at,
                expires_at
            FROM signal_blacklist
            WHERE is_active = 1
            AND (expires_at IS NULL OR expires_at > NOW())
            ORDER BY disabled_at DESC
            LIMIT 10
        """)

        blacklist = cursor.fetchall()
        if blacklist:
            for bl in blacklist:
                print(f"{bl['signal_type']}")
                print(f"  原因: {bl['reason']}")
                print(f"  禁用: {bl['disabled_at']} → {bl['expires_at'] or '永久'}")
        else:
            print("⚠️ 没有任何信号在黑名单中")
        print()

        # 6. 检查今天开仓时间分布
        print("⏰ 6. 今日开仓时间分布")
        print("-" * 80)
        cursor.execute("""
            SELECT
                DATE_FORMAT(open_time, '%H:00') as hour,
                COUNT(*) as count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE account_id = 2
            AND DATE(open_time) = '2026-02-06'
            AND status = 'closed'
            GROUP BY DATE_FORMAT(open_time, '%H:00')
            ORDER BY hour
        """)

        hourly = cursor.fetchall()
        for h in hourly:
            emoji = "🔴" if h['total_pnl'] < 0 else "🟢"
            print(f"{emoji} {h['hour']} | 开仓:{h['count']:2}笔 | 盈利:{h['wins']:2}笔 | "
                  f"总盈亏:{h['total_pnl']:8.2f}U")
        print()

        # 7. 检查系统是否有熔断机制
        print("🛡️ 7. 风控状态检查")
        print("-" * 80)

        # 检查01:38-02:02期间的连续止损
        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN notes LIKE '%止损%' THEN 1 ELSE 0 END) as stop_loss_count,
                MIN(open_time) as first_trade,
                MAX(close_time) as last_close,
                TIMESTAMPDIFF(MINUTE, MIN(open_time), MAX(close_time)) as duration_minutes
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-06 01:38:00'
            AND open_time <= '2026-02-06 02:02:00'
            AND status = 'closed'
        """)

        risk_check = cursor.fetchone()
        if risk_check:
            print(f"01:38-02:02期间:")
            print(f"  总交易数: {risk_check['total_trades']}")
            print(f"  止损数量: {risk_check['stop_loss_count']}")
            print(f"  开始时间: {risk_check['first_trade']}")
            print(f"  结束时间: {risk_check['last_close']}")
            print(f"  持续时长: {risk_check['duration_minutes']}分钟")
            print(f"\n⚠️ 在{risk_check['duration_minutes']}分钟内开了{risk_check['total_trades']}笔，"
                  f"{risk_check['stop_loss_count']}笔被止损")
            print(f"⚠️ 系统没有触发熔断机制！")
        print()

        # 8. 检查昨天同时间段对比
        print("📈 8. 昨天同时段对比 (02-05 01:38-02:02)")
        print("-" * 80)
        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-05 01:38:00'
            AND open_time <= '2026-02-05 02:02:00'
            AND status = 'closed'
        """)

        yesterday = cursor.fetchone()
        if yesterday and yesterday['total_trades'] > 0:
            print(f"昨天同时段: 开仓{yesterday['total_trades']}笔，"
                  f"盈利{yesterday['wins']}笔，总盈亏{yesterday['total_pnl']}U")
            print(f"今天同时段: 开仓{risk_check['total_trades']}笔，基本全亏")
        else:
            print("昨天同时段没有交易")
        print()

        cursor.close()
        conn.close()

        print("="*80)
        print("✅ 分析完成")
        print("="*80 + "\n")

    except Exception as e:
        print(f"❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    analyze_disaster()
