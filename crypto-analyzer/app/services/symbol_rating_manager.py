#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易对评级管理器 - 问题2优化
实现3级黑名单制度的自动升级/降级
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
from loguru import logger
import pymysql
from .optimization_config import OptimizationConfig

# 与 update_top_performers 日终白名单同一口径（全仓累计，非近7天毛利）
try:
    from update_top_performers import MIN_TRADES as WL_MIN_TRADES, qualifies_whitelist
except ImportError:
    WL_MIN_TRADES = 5

    def qualifies_whitelist(pnl: float, win_rate: float) -> bool:
        return pnl > 200.0 or win_rate > 55.0


class SymbolRatingManager:
    """交易对评级管理器 - 自动升级/降级黑名单等级"""

    def __init__(self, db_config: dict):
        """
        初始化评级管理器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None
        self.opt_config = OptimizationConfig(db_config)

        logger.info("✅ 交易对评级管理器已初始化")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                **self.db_config,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(
                    **self.db_config,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
        return self.connection

    def analyze_symbol_performance(self, symbol: str, days: int = 7) -> Dict:
        """
        分析交易对近N天表现

        Args:
            symbol: 交易对符号
            days: 分析天数

        Returns:
            统计数据字典
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cutoff_date = datetime.now() - timedelta(days=days)

        cursor.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN notes LIKE '%%hard_stop_loss%%' THEN 1 ELSE 0 END) as hard_stop_loss_count,
                SUM(realized_pnl) as total_pnl,
                SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) as total_profit,
                SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END) as total_loss,
                AVG(realized_pnl) as avg_pnl
            FROM futures_positions
            WHERE symbol = %s
            AND status = 'closed'
            AND close_time >= %s
        """, (symbol, cutoff_date))

        result = cursor.fetchone()
        cursor.close()

        if not result or result['total_trades'] == 0:
            return {
                'symbol': symbol,
                'total_trades': 0,
                'win_rate': 0,
                'hard_stop_loss_count': 0,
                'total_loss_amount': 0,
                'total_profit_amount': 0,
                'net_pnl': 0
            }

        win_rate = result['wins'] / result['total_trades'] if result['total_trades'] > 0 else 0

        # 安全转换float,避免None导致错误
        def safe_float(val):
            return float(val) if val is not None else 0.0

        return {
            'symbol': symbol,
            'total_trades': result['total_trades'],
            'wins': result['wins'],
            'losses': result['losses'],
            'win_rate': win_rate,
            'hard_stop_loss_count': result['hard_stop_loss_count'],
            'total_loss_amount': safe_float(result['total_loss']),
            'total_profit_amount': safe_float(result['total_profit']),
            'net_pnl': safe_float(result['total_pnl'])
        }

    def analyze_symbol_performance_all_time(self, symbol: str, account_id: int = 2) -> Dict:
        """全仓累计表现（与 TOP50 / 日终白名单口径一致）."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_trades,
                COALESCE(SUM(realized_pnl), 0) AS net_pnl,
                CASE
                    WHEN COUNT(*) > 0
                    THEN SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
                    ELSE 0
                END AS win_rate_pct
            FROM futures_positions
            WHERE symbol = %s
              AND account_id = %s
              AND status = 'closed'
              AND realized_pnl IS NOT NULL
            """,
            (symbol, account_id),
        )
        result = cursor.fetchone()
        cursor.close()
        if not result or int(result["total_trades"] or 0) < WL_MIN_TRADES:
            return {"total_trades": 0, "net_pnl": 0.0, "win_rate_pct": 0.0}
        return {
            "total_trades": int(result["total_trades"]),
            "net_pnl": float(result["net_pnl"] or 0),
            "win_rate_pct": float(result["win_rate_pct"] or 0),
        }

    def calculate_new_rating_level(self, stats: Dict, current_level: int) -> tuple:
        """
        根据统计数据计算新的评级等级

        Args:
            stats: 统计数据
            current_level: 当前评级等级

        Returns:
            (新等级, 变更原因)
        """
        hard_stop_count = stats['hard_stop_loss_count']
        total_loss = stats['total_loss_amount']
        total_profit = stats['total_profit_amount']
        win_rate = stats['win_rate']
        total_trades = stats['total_trades']

        # 如果交易数量太少,保持当前等级
        if total_trades < 3:
            return current_level, "交易数量不足,保持现状"

        symbol = stats.get("symbol") or ""

        # 升至白名单 L0：仅允许全仓累计达日终白名单门槛（禁止用近7天毛利误判）
        if current_level == 1 and symbol:
            at = self.analyze_symbol_performance_all_time(symbol)
            if at["total_trades"] >= WL_MIN_TRADES and qualifies_whitelist(
                at["net_pnl"], at["win_rate_pct"]
            ):
                parts = []
                if at["net_pnl"] > 200.0:
                    parts.append(f"累计盈利{at['net_pnl']:.0f}U")
                if at["win_rate_pct"] > 55.0:
                    parts.append(f"胜率{at['win_rate_pct']:.1f}%")
                return 0, "全仓达白名单: " + ", ".join(parts)

        # L2 → L1：仍用观察窗内表现（非白名单）
        upgrade_config = self.opt_config.get_blacklist_upgrade_config()
        required_profit = upgrade_config['profit_amount']
        required_win_rate = upgrade_config['win_rate']

        if current_level == 2:
            if total_profit >= required_profit and win_rate >= required_win_rate:
                return 1, (
                    f"表现良好(盈利${total_profit:.2f}, 胜率{win_rate*100:.1f}%), 降级到Level 1"
                )

        # 升级逻辑 (降级到更差的等级)
        for target_level in [1, 2, 3]:
            trigger_config = self.opt_config.get_blacklist_trigger_config(target_level)
            trigger_stop_loss = trigger_config['stop_loss_count']
            trigger_loss_amount = trigger_config['loss_amount']

            # 触发条件: hard_stop_loss次数 >= 阈值 或 总亏损 >= 阈值
            if hard_stop_count >= trigger_stop_loss or total_loss >= trigger_loss_amount:
                if target_level > current_level:
                    return target_level, \
                           f"触发Level{target_level}条件(hard_stop_loss={hard_stop_count}, 亏损=${total_loss:.2f})"

        # 无需变更
        return current_level, "无需变更"

    def update_all_symbol_ratings(self, observation_days: int = None) -> Dict:
        """
        更新所有交易对的评级

        Args:
            observation_days: 观察天数,默认从配置读取

        Returns:
            更新结果统计
        """
        if observation_days is None:
            observation_days = self.opt_config.get_blacklist_upgrade_config()['observation_days']

        logger.info(f"🔍 开始更新所有交易对评级 (观察{observation_days}天)")

        conn = self._get_connection()
        cursor = conn.cursor()

        # 获取所有交易过的交易对
        cursor.execute("""
            SELECT DISTINCT symbol
            FROM futures_positions
            WHERE status = 'closed'
            AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
        """, (observation_days,))

        symbols = [row['symbol'] for row in cursor.fetchall()]
        cursor.close()

        if not symbols:
            logger.warning("没有需要评级的交易对")
            return {
                'total_symbols': 0,
                'upgraded': [],
                'downgraded': [],
                'unchanged': [],
                'new_rated': []
            }

        results = {
            'total_symbols': len(symbols),
            'upgraded': [],      # 升级到更差等级
            'downgraded': [],    # 降级到更好等级
            'unchanged': [],
            'new_rated': []      # 新增评级
        }

        for symbol in symbols:
            # 分析表现
            stats = self.analyze_symbol_performance(symbol, observation_days)

            if stats['total_trades'] == 0:
                continue

            # 获取当前评级
            current_rating = self.opt_config.get_symbol_rating(symbol)
            old_level = current_rating['rating_level'] if current_rating else 0

            # 计算新评级
            new_level, reason = self.calculate_new_rating_level(stats, old_level)

            # 更新评级
            if new_level != old_level:
                self.opt_config.update_symbol_rating(
                    symbol, new_level, reason,
                    hard_stop_loss_count=stats['hard_stop_loss_count'],
                    total_loss_amount=stats['total_loss_amount'],
                    total_profit_amount=stats['total_profit_amount'],
                    win_rate=stats['win_rate'],
                    total_trades=stats['total_trades']
                )

                change_info = {
                    'symbol': symbol,
                    'old_level': old_level,
                    'new_level': new_level,
                    'reason': reason,
                    'stats': stats
                }

                if current_rating is None:
                    results['new_rated'].append(change_info)
                elif new_level > old_level:
                    results['upgraded'].append(change_info)  # 升级到更差等级
                else:
                    results['downgraded'].append(change_info)  # 降级到更好等级

            else:
                results['unchanged'].append({
                    'symbol': symbol,
                    'level': old_level,
                    'reason': reason
                })

        logger.info(f"✅ 评级更新完成: 总计{results['total_symbols']}个交易对")
        logger.info(f"   升级(变差): {len(results['upgraded'])}, "
                   f"降级(变好): {len(results['downgraded'])}, "
                   f"无变化: {len(results['unchanged'])}, "
                   f"新增: {len(results['new_rated'])}")

        return results

    def print_rating_report(self, results: Dict):
        """打印评级报告"""
        print("\n" + "=" * 100)
        print("🏆 交易对评级更新报告")
        print("=" * 100)

        if results['upgraded']:
            print("\n📉 升级到更差等级 (需要改进):")
            for item in results['upgraded']:
                print(f"  {item['symbol']}: Level {item['old_level']} → Level {item['new_level']}")
                print(f"    原因: {item['reason']}")
                print(f"    统计: 交易{item['stats']['total_trades']}次, "
                      f"胜率{item['stats']['win_rate']*100:.1f}%, "
                      f"hard_stop_loss={item['stats']['hard_stop_loss_count']}次")

        if results['downgraded']:
            print("\n📈 降级到更好等级 (表现良好):")
            for item in results['downgraded']:
                print(f"  {item['symbol']}: Level {item['old_level']} → Level {item['new_level']}")
                print(f"    原因: {item['reason']}")
                print(f"    统计: 交易{item['stats']['total_trades']}次, "
                      f"胜率{item['stats']['win_rate']*100:.1f}%, "
                      f"盈利${item['stats']['total_profit_amount']:.2f}")

        if results['new_rated']:
            print("\n🆕 新增评级:")
            for item in results['new_rated']:
                print(f"  {item['symbol']}: Level {item['new_level']}")
                print(f"    原因: {item['reason']}")

        print("\n" + "=" * 100)


# 测试代码
if __name__ == '__main__':
    db_config = {
        'host': '13.212.252.171',
        'port': 3306,
        'user': 'admin',
        'password': 'Tonny@1000',
        'database': 'binance-data'
    }

    manager = SymbolRatingManager(db_config)

    # 测试单个交易对分析
    print("\n=== 测试单个交易对分析 ===")
    stats = manager.analyze_symbol_performance('BTC/USDT', days=7)
    print(f"BTC/USDT 近7天表现:")
    print(f"  交易次数: {stats['total_trades']}")
    print(f"  胜率: {stats['win_rate']*100:.1f}%")
    print(f"  hard_stop_loss: {stats['hard_stop_loss_count']}次")
    print(f"  净盈亏: ${stats['net_pnl']:.2f}")

    # 测试全量更新
    print("\n=== 测试全量更新评级 ===")
    results = manager.update_all_symbol_ratings(observation_days=7)
    manager.print_rating_report(results)
