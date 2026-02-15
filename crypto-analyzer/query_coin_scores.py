#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查询代币评分（从数据库）
"""
import sys
import os
from dotenv import load_dotenv
import pymysql

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()


def query_scores(limit=50, direction=None):
    """查询代币评分"""
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 构建查询
        where_clause = ""
        if direction:
            where_clause = f"WHERE direction = '{direction}'"

        query = f"""
            SELECT *
            FROM coin_kline_scores
            {where_clause}
            ORDER BY total_score DESC
            LIMIT %s
        """

        cursor.execute(query, (limit,))
        results = cursor.fetchall()

        cursor.close()
        conn.close()

        return results

    except Exception as e:
        print(f"❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()
        return []


def display_scores(results, title="代币评分"):
    """显示评分"""
    if not results:
        print("❌ 没有数据")
        return

    print("\n" + "="*140)
    print(f"📊 {title}")
    print("="*140)
    print(f"{'排名':<4} {'代币':<15} {'总分':<6} {'主分':<6} {'5M':<5} {'1H':<20} {'15M':<20} {'方向':<8} {'强度':<8} {'更新时间':<20}")
    print("─"*140)

    for i, r in enumerate(results, 1):
        h1_str = f"{r['h1_score']:+3d}分({r['h1_bullish_count']}阳{r['h1_bearish_count']}阴)"
        m15_str = f"{r['m15_score']:+3d}分({r['m15_bullish_count']}阳{r['m15_bearish_count']}阴)"

        # 根据方向和强度添加标记
        marker = ""
        if r['direction'] == 'LONG' and r['strength_level'] == 'strong':
            marker = "🟢"
        elif r['direction'] == 'SHORT' and r['strength_level'] == 'strong':
            marker = "🔴"
        elif r['strength_level'] == 'medium':
            marker = "🟡"

        updated_time = r['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if r['updated_at'] else 'N/A'

        print(f"{i:<4} {r['symbol']:<15} {r['total_score']:+6d} {r['main_score']:+6d} {r['five_m_bonus']:+5d} "
              f"{h1_str:<20} {m15_str:<20} {r['direction']:<8} {r['strength_level']:<8} {updated_time:<20} {marker}")

    print("="*140)


def main():
    import argparse

    parser = argparse.ArgumentParser(description='查询代币K线评分')
    parser.add_argument('--limit', type=int, default=50, help='显示数量，默认50')
    parser.add_argument('--direction', type=str, choices=['LONG', 'SHORT', 'NEUTRAL'], help='过滤方向')
    parser.add_argument('--top', type=int, help='只显示Top N')
    parser.add_argument('--bottom', type=int, help='只显示Bottom N')

    args = parser.parse_args()

    print("\n🚀 代币K线评分查询工具")

    if args.top:
        # Top N（做多）
        results = query_scores(limit=args.top, direction='LONG')
        display_scores(results, f"Top{args.top} 做多机会")

    elif args.bottom:
        # Bottom N（做空）
        results = query_scores(limit=args.bottom, direction='SHORT')
        # 反转顺序，显示最低分在前
        results_reversed = sorted(results, key=lambda x: x['total_score'])
        display_scores(results_reversed, f"Top{args.bottom} 做空机会")

    else:
        # 显示所有（按总分排序）
        results = query_scores(limit=args.limit, direction=args.direction)

        if args.direction:
            title = f"{args.direction}方向 Top{args.limit}"
        else:
            title = f"所有代币 Top{args.limit}"

        display_scores(results, title)

        # 统计信息
        conn = pymysql.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 3306)),
            user=os.getenv('DB_USER', 'root'),
            password=os.getenv('DB_PASSWORD', ''),
            database=os.getenv('DB_NAME', 'binance-data')
        )
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT
                direction,
                strength_level,
                COUNT(*) as count
            FROM coin_kline_scores
            GROUP BY direction, strength_level
            ORDER BY direction, strength_level
        """)

        stats = cursor.fetchall()

        print("\n" + "="*80)
        print("📈 市场统计")
        print("="*80)

        for stat in stats:
            print(f"  {stat['direction']:<10} {stat['strength_level']:<10} {stat['count']:>3}个")

        cursor.close()
        conn.close()

        print("="*80)


if __name__ == "__main__":
    main()
