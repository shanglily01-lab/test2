"""
将FIGHT/USDT加入黑名单 - 简化版
"""

import pymysql
import sys
import io
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

        # 先查看FIGHT的历史表现
        print("\n📊 FIGHT/USDT历史表现:")
        print("-" * 60)
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
            print(f"总交易: {stats[0]}笔")
            print(f"盈利: {stats[1]}笔")
            print(f"胜率: {stats[2]:.2f}%")
            print(f"总盈亏: {stats[3]:.2f} USDT")
            print(f"最大亏损: {stats[4]:.2f} USDT")
            print(f"最大盈利: {stats[5]:.2f} USDT")
        else:
            print("没有历史记录")

        # 检查是否已存在
        cursor.execute("""
            SELECT id FROM signal_blacklist
            WHERE signal_type = 'SYMBOL_FIGHT/USDT'
        """)

        existing = cursor.fetchone()

        if existing:
            # 更新
            cursor.execute("""
                UPDATE signal_blacklist
                SET
                    is_active = 1,
                    reason = 'FIGHT/USDT严重亏损-46.34U，永久拉黑',
                    total_loss = %s,
                    win_rate = %s,
                    order_count = %s,
                    notes = '3级永久黑名单',
                    updated_at = NOW()
                WHERE signal_type = 'SYMBOL_FIGHT/USDT'
            """, (abs(stats[3]) if stats else 0, stats[2] if stats else 0, stats[0] if stats else 0))
            print("\n✅ 已更新 FIGHT/USDT 黑名单")
        else:
            # 插入
            cursor.execute("""
                INSERT INTO signal_blacklist (
                    signal_type,
                    position_side,
                    reason,
                    total_loss,
                    win_rate,
                    order_count,
                    is_active,
                    notes,
                    created_at,
                    updated_at
                ) VALUES (
                    'SYMBOL_FIGHT/USDT',
                    '',
                    'FIGHT/USDT严重亏损-46.34U，永久拉黑',
                    %s,
                    %s,
                    %s,
                    1,
                    '3级永久黑名单',
                    NOW(),
                    NOW()
                )
            """, (abs(stats[3]) if stats else 0, stats[2] if stats else 0, stats[0] if stats else 0))
            print("\n✅ 已添加 FIGHT/USDT 到黑名单")

        conn.commit()

        # 检查持仓
        cursor.execute("""
            SELECT id, position_side, quantity, entry_price, mark_price
            FROM futures_positions
            WHERE symbol = 'FIGHT/USDT'
            AND status = 'open'
        """)

        open_positions = cursor.fetchall()

        if open_positions:
            print(f"\n⚠️  当前还有 {len(open_positions)} 个FIGHT/USDT持仓:")
            for pos in open_positions:
                print(f"   ID:{pos[0]} | {pos[1]} | 数量:{pos[2]:.2f} | 开仓:{pos[3]:.6f} | 当前:{pos[4]:.6f}")
            print("   ⚠️ 建议立即手动平仓!")
        else:
            print("\n✅ 没有FIGHT/USDT持仓")

        # 查看当前黑名单
        print("\n📋 当前黑名单(前10个):")
        print("-" * 60)
        cursor.execute("""
            SELECT signal_type, reason, total_loss, win_rate
            FROM signal_blacklist
            WHERE is_active = 1
            ORDER BY updated_at DESC
            LIMIT 10
        """)

        blacklist = cursor.fetchall()
        for idx, bl in enumerate(blacklist, 1):
            print(f"{idx}. {bl[0]}")
            print(f"   原因: {bl[1]}")
            print(f"   亏损: {bl[2]:.2f}U | 胜率: {bl[3]:.2f}%")

        cursor.close()
        conn.close()

        print("\n🚫 FIGHT/USDT已加入3级永久黑名单\n")

    except Exception as e:
        print(f"❌ 操作失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    blacklist_fight()
