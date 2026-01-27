#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日复盘 + 自动优化 - 集成脚本

每天自动运行:
1. 复盘分析24H行情
2. 自动优化信号参数
3. 生成报告

Usage:
    python run_daily_review_and_optimize.py
"""

import asyncio
from datetime import datetime
from loguru import logger
import yaml
import os
from dotenv import load_dotenv

from app.services.daily_review_analyzer import DailyReviewAnalyzer
from app.services.auto_parameter_optimizer import AutoParameterOptimizer


async def main():
    """主函数"""
    logger.info("="*80)
    logger.info(f"🚀 每日复盘 + 自动优化 | 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("="*80)

    # 加载环境变量
    load_dotenv()

    # 从.env加载数据库配置
    db_config = {
        'host': os.getenv('DB_HOST'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME')
    }

    # 加载交易对列表
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    symbols = config.get('symbols', [])

    # ========== 步骤1: 执行复盘分析 ==========
    logger.info("\n📊 步骤1/2: 执行复盘分析...")

    analyzer = DailyReviewAnalyzer(db_config)
    report = await analyzer.run_daily_review(symbols)

    logger.info(
        f"✅ 复盘分析完成 | "
        f"总机会: {report.total_opportunities} | "
        f"捕获率: {report.capture_rate:.1f}% | "
        f"错过: {report.missed_count}个"
    )

    # ========== 步骤2: 自动优化参数 ==========
    logger.info("\n🔧 步骤2/2: 自动优化参数...")

    optimizer = AutoParameterOptimizer(db_config)
    optimize_result = await optimizer.optimize_based_on_review(report.date)

    if optimize_result['success']:
        adjustments = optimize_result.get('adjustments', [])

        if adjustments:
            logger.info(f"✅ 参数优化完成 | 调整了{len(adjustments)}个参数:")

            for adj in adjustments:
                logger.info(
                    f"   • {adj['param_group']}.{adj['param_name']}: "
                    f"{adj['old_value']} → {adj['new_value']}"
                )
                logger.info(f"     原因: {adj['reason']}")
        else:
            logger.info("ℹ️  当前参数表现良好，无需调整")

        logger.info("\n💾 优化后的参数已保存到: optimized_params.yaml")
    else:
        logger.error(f"❌ 参数优化失败: {optimize_result.get('error')}")

    # ========== 总结 ==========
    logger.info("\n" + "="*80)
    logger.info("📈 每日复盘 + 自动优化总结")
    logger.info("="*80)

    logger.info(f"\n【复盘结果】")
    logger.info(f"  • 识别到大行情: {report.total_opportunities}个")
    logger.info(f"  • 成功捕获: {report.captured_count}个 ({report.capture_rate:.1f}%)")
    logger.info(f"  • 错过机会: {report.missed_count}个")

    if report.missed_opportunities:
        logger.info(f"\n【错过的重要机会】(前3个)")
        for i, opp in enumerate(report.missed_opportunities[:3], 1):
            logger.info(
                f"  {i}. {opp.symbol} {opp.timeframe} {opp.move_type.upper()} "
                f"{abs(opp.price_change_pct):.2f}% | 原因: {opp.miss_reason}"
            )

    if report.optimization_suggestions:
        logger.info(f"\n【优化建议】")
        for suggestion in report.optimization_suggestions[:5]:
            logger.info(f"  • {suggestion}")

    if optimize_result['success'] and adjustments:
        logger.info(f"\n【已应用的参数调整】")
        for adj in adjustments[:5]:
            logger.info(
                f"  • {adj['param_group']}.{adj['param_name']}: "
                f"{adj['old_value']} → {adj['new_value']}"
            )

    logger.info("\n" + "="*80)
    logger.info(f"✅ 任务完成 | 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("="*80 + "\n")


if __name__ == '__main__':
    asyncio.run(main())
