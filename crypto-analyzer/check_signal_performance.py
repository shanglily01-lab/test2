"""
检查各信号类型的表现，识别需要禁用的信号
"""
import pymysql
import sys

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

print("=" * 120)
print("信号类型表现分析")
print("=" * 120)

conn = pymysql.connect(**db_config)
cursor = conn.cursor(pymysql.cursors.DictCursor)

# 分析所有信号类型的表现
print("\n1. 按信号类型和方向统计表现")
print("-" * 120)

cursor.execute("""
    SELECT
        entry_signal_type,
        position_side,
        COUNT(*) as total_orders,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_orders,
        SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as loss_orders,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        MIN(realized_pnl) as max_loss,
        MAX(realized_pnl) as max_profit,
        AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_hold_minutes,
        ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    GROUP BY entry_signal_type, position_side
    HAVING total_orders >= 5
    ORDER BY total_pnl ASC
""")

signals = cursor.fetchall()

print(f"\n{'信号类型':<20} {'方向':<6} {'订单数':>8} {'胜率':>8} {'总盈亏':>12} {'平均盈亏':>10} {'最大亏损':>12} {'持仓时长':>12}")
print("-" * 120)

bad_signals = []

for signal in signals:
    signal_type = signal['entry_signal_type'] or 'unknown'
    side = signal['position_side']
    total_orders = signal['total_orders']
    win_rate = signal['win_rate']
    total_pnl = signal['total_pnl']
    avg_pnl = signal['avg_pnl']
    max_loss = signal['max_loss']
    avg_hold = signal['avg_hold_minutes']

    pnl_str = f"+${total_pnl:.2f}" if total_pnl > 0 else f"${total_pnl:.2f}"

    # 标识问题信号
    is_bad = False
    reasons = []

    if total_pnl < -50:
        is_bad = True
        reasons.append(f"总亏损${abs(total_pnl):.2f}")

    if win_rate < 20:
        is_bad = True
        reasons.append(f"胜率{win_rate:.1f}%")

    if avg_pnl < -5:
        is_bad = True
        reasons.append(f"平均亏${abs(avg_pnl):.2f}")

    status = "❌ 建议禁用" if is_bad else "✅ 正常"
    reason_str = " | ".join(reasons) if reasons else ""

    print(f"{signal_type:<20} {side:<6} {total_orders:>8} {win_rate:>7.1f}% {pnl_str:>12} ${avg_pnl:>9.2f} ${max_loss:>11.2f} {avg_hold:>9.0f}分钟 {status} {reason_str}")

    if is_bad:
        bad_signals.append({
            'signal_type': signal_type,
            'side': side,
            'total_pnl': total_pnl,
            'win_rate': win_rate,
            'total_orders': total_orders,
            'reasons': reasons
        })

# 总结
print("\n" + "=" * 120)
print("识别到的问题信号")
print("=" * 120)

if bad_signals:
    print(f"\n发现 {len(bad_signals)} 个表现差的信号组合，建议禁用:\n")

    for signal in bad_signals:
        print(f"  ❌ {signal['signal_type']} {signal['side']}")
        print(f"     总亏损: ${abs(signal['total_pnl']):.2f} | 胜率: {signal['win_rate']:.1f}% | 订单数: {signal['total_orders']}")
        print(f"     问题: {', '.join(signal['reasons'])}")
        print()

    # 生成禁用建议
    print("-" * 120)
    print("建议操作:")
    print("-" * 120)
    print("\n方案1: 添加到adaptive_params表（推荐）")
    print("在adaptive_params表中添加disabled_signals参数:\n")

    disabled_list = [f"{s['signal_type']}_{s['side']}" for s in bad_signals]
    print(f"disabled_signals = {disabled_list}")
    print()

    print("SQL语句:")
    print("""
INSERT INTO adaptive_params (param_key, param_value, param_type, description)
VALUES ('disabled_signals', '""" + ",".join(disabled_list) + """', 'system', '禁用的信号列表')
ON DUPLICATE KEY UPDATE param_value = VALUES(param_value);
""")

    print("\n方案2: 创建独立的信号黑名单表")
    print("""
CREATE TABLE IF NOT EXISTS signal_blacklist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    signal_type VARCHAR(50) NOT NULL,
    position_side VARCHAR(10) NOT NULL,
    reason VARCHAR(255),
    total_loss DECIMAL(15,2),
    win_rate DECIMAL(5,4),
    order_count INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE KEY unique_signal_side (signal_type, position_side)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='信号黑名单';
""")

    print("\n然后插入数据:")
    for signal in bad_signals:
        print(f"""
INSERT INTO signal_blacklist (signal_type, position_side, reason, total_loss, win_rate, order_count, is_active)
VALUES ('{signal['signal_type']}', '{signal['side']}', '{"; ".join(signal['reasons'])}',
        {abs(signal['total_pnl']):.2f}, {signal['win_rate']/100:.4f}, {signal['total_orders']}, TRUE)
ON DUPLICATE KEY UPDATE
    total_loss = VALUES(total_loss),
    win_rate = VALUES(win_rate),
    order_count = VALUES(order_count),
    reason = VALUES(reason);
""")

else:
    print("\n✅ 所有信号表现正常，暂不需要禁用")

# 按得分分析
print("\n" + "=" * 120)
print("2. 按信号得分区间分析")
print("=" * 120)

# 提取得分（从SMART_BRAIN_30中提取30）
cursor.execute("""
    SELECT
        CASE
            WHEN entry_signal_type LIKE 'SMART_BRAIN_%' THEN
                CAST(SUBSTRING_INDEX(entry_signal_type, '_', -1) AS UNSIGNED)
            ELSE 0
        END as score,
        position_side,
        COUNT(*) as total_orders,
        SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_orders,
        SUM(realized_pnl) as total_pnl,
        AVG(realized_pnl) as avg_pnl,
        ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate
    FROM futures_positions
    WHERE status = 'closed'
    AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    AND entry_signal_type LIKE 'SMART_BRAIN_%'
    GROUP BY score, position_side
    HAVING total_orders >= 3
    ORDER BY position_side, score
""")

score_analysis = cursor.fetchall()

if score_analysis:
    print(f"\n{'得分':<6} {'方向':<6} {'订单数':>8} {'胜率':>8} {'总盈亏':>12} {'平均盈亏':>10} {'评价':<20}")
    print("-" * 80)

    for row in score_analysis:
        score = row['score']
        side = row['position_side']
        total = row['total_orders']
        win_rate = row['win_rate']
        total_pnl = row['total_pnl']
        avg_pnl = row['avg_pnl']

        pnl_str = f"+${total_pnl:.2f}" if total_pnl > 0 else f"${total_pnl:.2f}"

        # 评价
        if win_rate >= 60 and avg_pnl > 5:
            rating = "🌟 优秀"
        elif win_rate >= 50 and avg_pnl > 0:
            rating = "✅ 良好"
        elif win_rate >= 40:
            rating = "⚠️ 一般"
        elif win_rate >= 30:
            rating = "❌ 较差"
        else:
            rating = "🚫 极差，建议禁用"

        print(f"{score:<6} {side:<6} {total:>8} {win_rate:>7.1f}% {pnl_str:>12} ${avg_pnl:>9.2f} {rating:<20}")

    # 建议的最低得分阈值
    print("\n建议的最低得分阈值:")
    for side in ['LONG', 'SHORT']:
        side_data = [row for row in score_analysis if row['position_side'] == side and row['win_rate'] >= 40]
        if side_data:
            min_score = min(row['score'] for row in side_data)
            print(f"  {side}: >= {min_score}分 (胜率40%以上)")
        else:
            print(f"  {side}: 无合适阈值，建议暂停{side}交易")

cursor.close()
conn.close()

print("\n" + "=" * 120)
print("分析完成")
print("=" * 120)
