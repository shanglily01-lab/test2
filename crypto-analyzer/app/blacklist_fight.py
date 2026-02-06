"""
将FIGHT/USDT加入3级黑名单
"""

import pymysql
import sys
import io
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.utils.config_loader import load_config


def blacklist_fight():
    """将FIGHT/USDT加入黑名单"""
    config = load_config()
    db_config = config.get('database', {}).get('mysql', {})

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        # 检查是否已存在
        cursor.execute("""
            SELECT id, is_active, expires_at
            FROM signal_blacklist
            WHERE signal_type = 'SYMBOL_FIGHT/USDT'
        """)

        existing = cursor.fetchone()

        if existing:
            # 更新现有记录
            cursor.execute("""
                UPDATE signal_blacklist
                SET
                    is_active = 1,
                    severity_level = 3,
                    reason = 'FIGHT/USDT严重亏损，单笔-46.34U(-19.31%)，紧急拉黑',
                    disabled_at = NOW(),
                    expires_at = NULL,
                    updated_at = NOW()
                WHERE signal_type = 'SYMBOL_FIGHT/USDT'
            """)
            print("✅ 已更新 FIGHT/USDT 黑名单记录")
        else:
            # 插入新记录
            cursor.execute("""
                INSERT INTO signal_blacklist (
                    signal_type,
                    severity_level,
                    reason,
                    disabled_at,
                    expires_at,
                    is_active,
                    created_at,
                    updated_at
                ) VALUES (
                    'SYMBOL_FIGHT/USDT',
                    3,
                    'FIGHT/USDT严重亏损，单笔-46.34U(-19.31%)，紧急拉黑',
                    NOW(),
                    NULL,
                    1,
                    NOW(),
                    NOW()
                )
            """)
            print("✅ 已添加 FIGHT/USDT 到黑名单")

        # 同时禁用所有FIGHT相关的信号
        cursor.execute("""
            UPDATE futures_positions
            SET notes = CONCAT(notes, ' [已拉黑]')
            WHERE symbol = 'FIGHT/USDT'
            AND status = 'open'
        """)

        conn.commit()

        # 查看FIGHT的历史表现
        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
                ROUND(SUM(realized_pnl), 2) as total_pnl,
                ROUND(MIN(realized_pnl), 2) as worst_loss,
                ROUND(MAX(realized_pnl), 2) as best_win
            FROM futures_positions
            WHERE symbol = 'FIGHT/USDT'
            AND status = 'closed'
        """)

        stats = cursor.fetchone()

        if stats and stats[0] > 0:
            print("\n📊 FIGHT/USDT历史表现:")
            print(f"   总交易: {stats[0]}笔")
            print(f"   盈利: {stats[1]}笔")
            print(f"   胜率: {stats[2]:.2f}%")
            print(f"   总盈亏: {stats[3]:.2f} USDT")
            print(f"   最大亏损: {stats[4]:.2f} USDT")
            print(f"   最大盈利: {stats[5]:.2f} USDT")

        # 检查是否有持仓
        cursor.execute("""
            SELECT id, position_side, quantity, entry_price
            FROM futures_positions
            WHERE symbol = 'FIGHT/USDT'
            AND status = 'open'
        """)

        open_positions = cursor.fetchall()

        if open_positions:
            print(f"\n⚠️  当前还有 {len(open_positions)} 个FIGHT/USDT持仓:")
            for pos in open_positions:
                print(f"   ID:{pos[0]} | {pos[1]} | 数量:{pos[2]} | 开仓价:{pos[3]}")
            print("   建议手动平仓!")
        else:
            print("\n✅ 没有FIGHT/USDT持仓")

        cursor.close()
        conn.close()

        print("\n🚫 FIGHT/USDT已加入3级永久黑名单\n")

    except Exception as e:
        print(f"❌ 操作失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    blacklist_fight()
