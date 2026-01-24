#!/usr/bin/env python3
"""
手动运行交易对评级更新
分析最近7天的交易表现,自动调整黑名单等级
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.config_loader import load_config
from app.services.symbol_rating_manager import SymbolRatingManager
from loguru import logger

def main():
    # 加载配置
    config = load_config()
    db_config = config.get('database', {}).get('mysql', {})

    # 创建评级管理器
    manager = SymbolRatingManager(db_config)

    # 更新所有交易对评级
    logger.info("=" * 80)
    logger.info("开始更新交易对评级...")
    logger.info("=" * 80)

    results = manager.update_all_symbol_ratings(observation_days=7)

    # 输出结果
    logger.info("\n" + "=" * 80)
    logger.info("评级更新完成!")
    logger.info("=" * 80)
    logger.info(f"总交易对数: {results['total_symbols']}")

    if results['new_rated']:
        logger.info(f"\n新增评级 ({len(results['new_rated'])}个):")
        for item in results['new_rated']:
            logger.info(f"  {item['symbol']:15} -> Level {item['new_level']} | {item['reason']}")
            logger.info(f"    胜率:{item['stats']['win_rate']*100:.1f}%, "
                       f"交易:{item['stats']['total_trades']}笔, "
                       f"亏损:${item['stats']['total_loss_amount']:.2f}")

    if results['upgraded']:
        logger.info(f"\n升级到更差等级 ({len(results['upgraded'])}个):")
        for item in results['upgraded']:
            logger.info(f"  {item['symbol']:15} Level {item['old_level']} -> {item['new_level']} | {item['reason']}")
            logger.info(f"    胜率:{item['stats']['win_rate']*100:.1f}%, "
                       f"交易:{item['stats']['total_trades']}笔, "
                       f"亏损:${item['stats']['total_loss_amount']:.2f}")

    if results['downgraded']:
        logger.info(f"\n降级到更好等级 ({len(results['downgraded'])}个):")
        for item in results['downgraded']:
            logger.info(f"  {item['symbol']:15} Level {item['old_level']} -> {item['new_level']} | {item['reason']}")
            logger.info(f"    胜率:{item['stats']['win_rate']*100:.1f}%, "
                       f"交易:{item['stats']['total_trades']}笔, "
                       f"盈利:${item['stats']['total_profit_amount']:.2f}")

    logger.info(f"\n无需变更: {len(results['unchanged'])}个")

    # 显示当前各级别统计
    import pymysql
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    cursor.execute("""
        SELECT rating_level, COUNT(*) as count
        FROM trading_symbol_rating
        GROUP BY rating_level
        ORDER BY rating_level
    """)

    level_stats = cursor.fetchall()
    logger.info("\n当前评级分布:")
    logger.info("-" * 80)
    for row in level_stats:
        level_name = {0: '白名单', 1: '黑名单1级', 2: '黑名单2级', 3: '黑名单3级'}.get(
            row['rating_level'], f"等级{row['rating_level']}"
        )
        logger.info(f"  {level_name}: {row['count']}个")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    main()
