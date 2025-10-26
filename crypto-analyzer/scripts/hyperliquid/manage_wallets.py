#!/usr/bin/env python3
"""
Hyperliquid 聪明钱包智能管理系统
功能：
  1. 自动筛选最活跃的聪明钱包
  2. 停止监控不活跃的钱包
  3. 添加新发现的高绩效钱包
  4. 生成监控报告
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, date
from typing import List, Dict, Tuple

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.database.hyperliquid_db import HyperliquidDB


class SmartWalletManager:
    """聪明钱包智能管理器"""

    def __init__(self):
        """初始化管理器"""
        self.db = None

    def __enter__(self):
        self.db = HyperliquidDB()
        self.db.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.db:
            self.db.__exit__(exc_type, exc_val, exc_tb)

    def get_top_performers(
        self,
        period: str = 'week',
        min_pnl: float = 10000,
        min_roi: float = 10,
        limit: int = 50
    ) -> List[Dict]:
        """
        获取顶级表现者

        Args:
            period: 周期 (week/month)
            min_pnl: 最低PnL要求 (USD)
            min_roi: 最低ROI要求 (%)
            limit: 返回数量

        Returns:
            交易者列表
        """
        try:
            if period == 'week':
                # 获取最新一周的数据
                query = """
                SELECT
                    t.id as trader_id,
                    t.address,
                    t.display_name,
                    wp.pnl,
                    wp.roi,
                    wp.volume,
                    wp.account_value,
                    wp.pnl_rank,
                    wp.week_start,
                    wp.week_end,
                    wp.recorded_at
                FROM hyperliquid_traders t
                JOIN hyperliquid_weekly_performance wp ON t.id = wp.trader_id
                WHERE wp.week_start = (SELECT MAX(week_start) FROM hyperliquid_weekly_performance)
                  AND wp.pnl >= %s
                  AND wp.roi >= %s
                ORDER BY wp.pnl DESC
                LIMIT %s
                """
            else:  # month
                query = """
                SELECT
                    t.id as trader_id,
                    t.address,
                    t.display_name,
                    mp.pnl,
                    mp.roi,
                    mp.volume,
                    mp.account_value,
                    mp.pnl_rank,
                    mp.month_start as week_start,
                    mp.month_end as week_end,
                    mp.recorded_at
                FROM hyperliquid_traders t
                JOIN hyperliquid_monthly_performance mp ON t.id = mp.trader_id
                WHERE mp.month_start = (SELECT MAX(month_start) FROM hyperliquid_monthly_performance)
                  AND mp.pnl >= %s
                  AND mp.roi >= %s
                ORDER BY mp.pnl DESC
                LIMIT %s
                """

            self.db.cursor.execute(query, (min_pnl, min_roi, limit))
            rows = self.db.cursor.fetchall()

            columns = [desc[0] for desc in self.db.cursor.description]
            traders = [dict(zip(columns, row)) for row in rows]

            print(f"✅ 找到 {len(traders)} 个顶级表现者 ({period}, PnL >= ${min_pnl:,}, ROI >= {min_roi}%)")
            return traders

        except Exception as e:
            print(f"❌ 获取顶级表现者失败: {e}")
            return []

    def get_current_monitors(self) -> List[Dict]:
        """
        获取当前所有监控的钱包

        Returns:
            监控钱包列表
        """
        try:
            query = """
            SELECT
                mw.id,
                mw.trader_id,
                mw.address,
                mw.label,
                mw.monitor_type,
                mw.is_monitoring,
                mw.discovered_pnl,
                mw.discovered_roi,
                mw.discovered_account_value,
                mw.discovered_at,
                mw.last_check_at,
                mw.last_trade_at,
                mw.check_count,
                t.display_name
            FROM hyperliquid_monitored_wallets mw
            JOIN hyperliquid_traders t ON mw.trader_id = t.id
            ORDER BY mw.is_monitoring DESC, mw.last_check_at ASC
            """

            self.db.cursor.execute(query)
            rows = self.db.cursor.fetchall()

            columns = [desc[0] for desc in self.db.cursor.description]
            monitors = [dict(zip(columns, row)) for row in rows]

            active = sum(1 for m in monitors if m['is_monitoring'])
            print(f"📊 当前监控钱包: {len(monitors)} 个 (活跃: {active}, 暂停: {len(monitors) - active})")

            return monitors

        except Exception as e:
            print(f"❌ 获取监控钱包失败: {e}")
            return []

    def add_to_monitoring(
        self,
        trader_id: int,
        address: str,
        label: str = None,
        monitor_type: str = 'auto',
        performance_data: Dict = None
    ) -> bool:
        """
        添加钱包到监控列表

        Args:
            trader_id: 交易者ID
            address: 钱包地址
            label: 标签
            monitor_type: 监控类型 (auto/manual)
            performance_data: 发现时的性能数据

        Returns:
            是否成功
        """
        try:
            now = datetime.now()

            # 检查是否已存在
            self.db.cursor.execute(
                "SELECT id, is_monitoring FROM hyperliquid_monitored_wallets WHERE trader_id = %s",
                (trader_id,)
            )
            existing = self.db.cursor.fetchone()

            if existing:
                # 如果已存在但被暂停，重新激活
                if not existing[1]:
                    self.db.cursor.execute(
                        """
                        UPDATE hyperliquid_monitored_wallets
                        SET is_monitoring = TRUE,
                            updated_at = %s
                        WHERE trader_id = %s
                        """,
                        (now, trader_id)
                    )
                    self.db.conn.commit()
                    print(f"  ✅ 重新激活监控: {address[:10]}... ({label})")
                    return True
                else:
                    print(f"  ⏭️  已在监控列表: {address[:10]}...")
                    return False

            # 插入新监控
            perf = performance_data or {}

            self.db.cursor.execute(
                """
                INSERT INTO hyperliquid_monitored_wallets
                (trader_id, address, label, monitor_type, is_monitoring,
                 discovered_pnl, discovered_roi, discovered_account_value,
                 discovered_at, created_at, updated_at, check_count)
                VALUES
                (%s, %s, %s, %s, TRUE,
                 %s, %s, %s,
                 %s, %s, %s, 0)
                """,
                (
                    trader_id, address, label, monitor_type,
                    perf.get('pnl', 0), perf.get('roi', 0), perf.get('account_value', 0),
                    now, now, now
                )
            )
            self.db.conn.commit()

            print(f"  ✅ 添加监控: {address[:10]}... ({label}) - PnL: ${perf.get('pnl', 0):,.2f}, ROI: {perf.get('roi', 0):.2f}%")
            return True

        except Exception as e:
            print(f"  ❌ 添加监控失败 {address[:10]}...: {e}")
            self.db.conn.rollback()
            return False

    def pause_monitoring(self, trader_id: int, reason: str = None) -> bool:
        """
        暂停监控钱包

        Args:
            trader_id: 交易者ID
            reason: 暂停原因

        Returns:
            是否成功
        """
        try:
            notes = f"暂停原因: {reason}" if reason else None

            self.db.cursor.execute(
                """
                UPDATE hyperliquid_monitored_wallets
                SET is_monitoring = FALSE,
                    updated_at = %s,
                    notes = CONCAT(IFNULL(notes, ''), '\n', IFNULL(%s, ''))
                WHERE trader_id = %s AND is_monitoring = TRUE
                """,
                (datetime.now(), notes, trader_id)
            )
            self.db.conn.commit()

            if self.db.cursor.rowcount > 0:
                print(f"  ⏸️  暂停监控: Trader #{trader_id} ({reason})")
                return True
            return False

        except Exception as e:
            print(f"  ❌ 暂停监控失败: {e}")
            self.db.conn.rollback()
            return False

    def evaluate_current_monitors(
        self,
        weeks_to_check: int = 4,
        min_recent_pnl: float = 5000
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        评估当前监控钱包的表现

        Args:
            weeks_to_check: 检查最近几周的表现
            min_recent_pnl: 最近周期最低PnL要求

        Returns:
            (活跃钱包列表, 不活跃钱包列表)
        """
        try:
            print(f"\n📊 评估监控钱包表现 (最近 {weeks_to_check} 周)...")

            # 获取监控钱包及其最近表现
            query = """
            SELECT
                mw.trader_id,
                mw.address,
                mw.label,
                t.display_name,
                mw.discovered_pnl,
                mw.discovered_roi,
                mw.last_check_at,
                wp.pnl as recent_pnl,
                wp.roi as recent_roi,
                wp.volume as recent_volume,
                wp.week_start
            FROM hyperliquid_monitored_wallets mw
            JOIN hyperliquid_traders t ON mw.trader_id = t.id
            LEFT JOIN hyperliquid_weekly_performance wp ON mw.trader_id = wp.trader_id
                AND wp.week_start >= DATE_SUB(CURDATE(), INTERVAL %s WEEK)
            WHERE mw.is_monitoring = TRUE
            ORDER BY wp.pnl DESC
            """

            self.db.cursor.execute(query, (weeks_to_check,))
            rows = self.db.cursor.fetchall()

            # 按 trader_id 分组
            from collections import defaultdict
            trader_performance = defaultdict(list)

            for row in rows:
                trader_id = row[0]
                trader_performance[trader_id].append({
                    'trader_id': row[0],
                    'address': row[1],
                    'label': row[2],
                    'display_name': row[3],
                    'discovered_pnl': row[4],
                    'discovered_roi': row[5],
                    'last_check_at': row[6],
                    'recent_pnl': row[7],
                    'recent_roi': row[8],
                    'recent_volume': row[9],
                    'week_start': row[10]
                })

            active_wallets = []
            inactive_wallets = []

            for trader_id, performances in trader_performance.items():
                # 合并所有周的PnL
                total_recent_pnl = sum(p['recent_pnl'] or 0 for p in performances)
                avg_recent_roi = sum(p['recent_roi'] or 0 for p in performances) / max(len(performances), 1)

                wallet = performances[0]  # 基本信息
                wallet['total_recent_pnl'] = total_recent_pnl
                wallet['avg_recent_roi'] = avg_recent_roi
                wallet['weeks_active'] = len([p for p in performances if p['recent_pnl']])

                # 判断是否活跃
                if total_recent_pnl >= min_recent_pnl and wallet['weeks_active'] > 0:
                    active_wallets.append(wallet)
                else:
                    inactive_wallets.append(wallet)

            print(f"  ✅ 活跃钱包: {len(active_wallets)} 个")
            print(f"  ⚠️  不活跃钱包: {len(inactive_wallets)} 个")

            return active_wallets, inactive_wallets

        except Exception as e:
            print(f"❌ 评估失败: {e}")
            import traceback
            traceback.print_exc()
            return [], []

    def optimize_monitoring_list(
        self,
        max_monitors: int = 100,
        min_pnl_threshold: float = 10000,
        min_roi_threshold: float = 15,
        auto_add: bool = False,
        auto_remove: bool = False
    ):
        """
        优化监控列表

        Args:
            max_monitors: 最大监控数量
            min_pnl_threshold: 新增钱包最低PnL要求
            min_roi_threshold: 新增钱包最低ROI要求
            auto_add: 是否自动添加新钱包
            auto_remove: 是否自动移除不活跃钱包

        Returns:
            优化报告
        """
        print("\n" + "=" * 80)
        print("🔧 智能优化监控列表")
        print("=" * 80)

        # 1. 获取当前监控
        current_monitors = self.get_current_monitors()
        active_count = sum(1 for m in current_monitors if m['is_monitoring'])

        print(f"\n📊 当前状态:")
        print(f"  - 总监控数: {len(current_monitors)}")
        print(f"  - 活跃监控: {active_count}")
        print(f"  - 暂停监控: {len(current_monitors) - active_count}")

        # 2. 评估当前监控表现
        active_wallets, inactive_wallets = self.evaluate_current_monitors(
            weeks_to_check=4,
            min_recent_pnl=5000
        )

        # 3. 处理不活跃钱包
        removed_count = 0
        if auto_remove and inactive_wallets:
            print(f"\n⏸️  暂停不活跃钱包...")
            for wallet in inactive_wallets:
                reason = f"最近4周PnL低于$5,000 (总PnL: ${wallet['total_recent_pnl']:,.2f})"
                if self.pause_monitoring(wallet['trader_id'], reason):
                    removed_count += 1

        # 4. 获取顶级新人
        new_traders = []
        if auto_add and active_count < max_monitors:
            print(f"\n🔍 发现新的聪明钱包...")
            top_performers = self.get_top_performers(
                period='week',
                min_pnl=min_pnl_threshold,
                min_roi=min_roi_threshold,
                limit=max_monitors
            )

            # 过滤已监控的
            monitored_ids = set(m['trader_id'] for m in current_monitors)
            new_traders = [t for t in top_performers if t['trader_id'] not in monitored_ids]

        # 5. 添加新钱包
        added_count = 0
        if auto_add and new_traders:
            print(f"\n➕ 添加新钱包到监控列表...")
            slots_available = max_monitors - (active_count - removed_count)

            for trader in new_traders[:slots_available]:
                label = f"Auto-Week-Top{trader.get('pnl_rank', '?')}"
                perf = {
                    'pnl': trader['pnl'],
                    'roi': trader['roi'],
                    'account_value': trader['account_value']
                }

                if self.add_to_monitoring(
                    trader_id=trader['trader_id'],
                    address=trader['address'],
                    label=label,
                    monitor_type='auto',
                    performance_data=perf
                ):
                    added_count += 1

        # 6. 生成报告
        print("\n" + "=" * 80)
        print("📋 优化报告")
        print("=" * 80)
        print(f"  ✅ 活跃钱包: {len(active_wallets)} 个")
        print(f"  ⏸️  暂停监控: {removed_count} 个")
        print(f"  ➕ 新增监控: {added_count} 个")
        print(f"  📊 最终监控数: {active_count - removed_count + added_count}/{max_monitors}")

        if active_wallets:
            print(f"\n🏆 Top 10 活跃钱包:")
            for i, wallet in enumerate(sorted(active_wallets, key=lambda x: x['total_recent_pnl'], reverse=True)[:10], 1):
                print(f"  {i:2d}. {wallet['address'][:10]}... | "
                      f"PnL: ${wallet['total_recent_pnl']:>10,.2f} | "
                      f"ROI: {wallet['avg_recent_roi']:>6.2f}% | "
                      f"活跃周数: {wallet['weeks_active']}")

        return {
            'active_wallets': len(active_wallets),
            'removed': removed_count,
            'added': added_count,
            'final_count': active_count - removed_count + added_count
        }

    def generate_monitor_report(self):
        """生成监控报告"""
        print("\n" + "=" * 80)
        print("📊 监控钱包详细报告")
        print("=" * 80)

        try:
            # 获取活跃监控统计
            query = """
            SELECT
                mw.monitor_type,
                COUNT(*) as count,
                AVG(mw.discovered_pnl) as avg_discovered_pnl,
                AVG(mw.discovered_roi) as avg_discovered_roi,
                MIN(mw.discovered_at) as earliest,
                MAX(mw.discovered_at) as latest
            FROM hyperliquid_monitored_wallets mw
            WHERE mw.is_monitoring = TRUE
            GROUP BY mw.monitor_type
            """

            self.db.cursor.execute(query)
            stats = self.db.cursor.fetchall()

            print(f"\n按类型统计:")
            for row in stats:
                monitor_type, count, avg_pnl, avg_roi, earliest, latest = row
                print(f"  {monitor_type:10s}: {count:3d} 个 | "
                      f"平均发现PnL: ${avg_pnl:>10,.2f} | "
                      f"平均发现ROI: {avg_roi:>6.2f}%")

            # 获取最近检查时间分布
            query = """
            SELECT
                CASE
                    WHEN last_check_at IS NULL THEN '从未检查'
                    WHEN last_check_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR) THEN '1小时内'
                    WHEN last_check_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR) THEN '1天内'
                    WHEN last_check_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN '1周内'
                    ELSE '1周以上'
                END as check_status,
                COUNT(*) as count
            FROM hyperliquid_monitored_wallets
            WHERE is_monitoring = TRUE
            GROUP BY check_status
            ORDER BY
                CASE check_status
                    WHEN '1小时内' THEN 1
                    WHEN '1天内' THEN 2
                    WHEN '1周内' THEN 3
                    WHEN '1周以上' THEN 4
                    ELSE 5
                END
            """

            self.db.cursor.execute(query)
            check_stats = self.db.cursor.fetchall()

            print(f"\n检查时间分布:")
            for status, count in check_stats:
                print(f"  {status:10s}: {count:3d} 个")

        except Exception as e:
            print(f"❌ 生成报告失败: {e}")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='Hyperliquid 聪明钱包智能管理')
    parser.add_argument('--action', choices=['report', 'optimize', 'list'], default='report',
                        help='操作类型')
    parser.add_argument('--max-monitors', type=int, default=100,
                        help='最大监控数量 (默认: 100)')
    parser.add_argument('--min-pnl', type=float, default=10000,
                        help='新增钱包最低PnL (USD, 默认: 10000)')
    parser.add_argument('--min-roi', type=float, default=15,
                        help='新增钱包最低ROI (%%, 默认: 15)')
    parser.add_argument('--auto-add', action='store_true',
                        help='自动添加新的聪明钱包')
    parser.add_argument('--auto-remove', action='store_true',
                        help='自动暂停不活跃钱包')

    args = parser.parse_args()

    print("=" * 80)
    print("Hyperliquid 聪明钱包智能管理系统")
    print("=" * 80)
    print()

    with SmartWalletManager() as manager:
        if args.action == 'report':
            # 生成报告
            manager.generate_monitor_report()

        elif args.action == 'optimize':
            # 优化监控列表
            manager.optimize_monitoring_list(
                max_monitors=args.max_monitors,
                min_pnl_threshold=args.min_pnl,
                min_roi_threshold=args.min_roi,
                auto_add=args.auto_add,
                auto_remove=args.auto_remove
            )

        elif args.action == 'list':
            # 列出所有监控
            monitors = manager.get_current_monitors()
            active = [m for m in monitors if m['is_monitoring']]

            print(f"\n📊 活跃监控钱包 ({len(active)} 个):")
            print(f"{'#':>3} {'地址':42} {'标签':20} {'发现PnL':>12} {'发现ROI':>10} {'最后检查':20}")
            print("-" * 110)

            for i, m in enumerate(active, 1):
                last_check = m['last_check_at'].strftime('%Y-%m-%d %H:%M') if m['last_check_at'] else '从未'
                print(f"{i:3d} {m['address']:42} {(m['label'] or 'N/A')[:20]:20} "
                      f"${m['discovered_pnl']:>11,.2f} {m['discovered_roi']:>9.2f}% {last_check:20}")

    print("\n✅ 完成!\n")


if __name__ == '__main__':
    main()
