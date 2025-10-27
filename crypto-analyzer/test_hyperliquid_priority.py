#!/usr/bin/env python3
"""
测试 Hyperliquid 聪明钱包分级监控功能

验证:
1. 数据库中有多少监控钱包
2. 按优先级获取钱包是否正常
3. 监控逻辑是否从数据库读取地址
4. 预估监控频率和数据量
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
from loguru import logger
from app.database.hyperliquid_db import HyperliquidDB
from app.collectors.hyperliquid_collector import HyperliquidCollector


async def test_hyperliquid_priority():
    """测试 Hyperliquid 分级监控功能"""

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 初始化采集器
    hyperliquid_collector = HyperliquidCollector(config)

    logger.info("=" * 80)
    logger.info("Hyperliquid 聪明钱包分级监控测试")
    logger.info("=" * 80)

    # 1. 检查数据库中的钱包数量
    logger.info("\n📊 步骤1: 检查数据库中的监控钱包数量")
    try:
        with HyperliquidDB() as db:
            all_wallets = db.get_monitored_wallets(active_only=True)
            logger.info(f"✓ 数据库中活跃钱包总数: {len(all_wallets)}")

            if all_wallets:
                # 显示前3个钱包样本
                logger.info("  样本钱包:")
                for i, wallet in enumerate(all_wallets[:3], 1):
                    logger.info(f"    {i}. {wallet['address'][:10]}... "
                               f"(PnL: ${wallet.get('discovered_pnl', 0):,.0f}, "
                               f"ROI: {wallet.get('discovered_roi', 0):.1f}%, "
                               f"最后交易: {wallet.get('last_trade_at', 'N/A')})")

    except Exception as e:
        logger.error(f"✗ 获取钱包失败: {e}")
        logger.info("提示: 请先运行 Hyperliquid 排行榜采集，以发现和添加监控钱包")
        return

    # 2. 测试按优先级获取钱包
    logger.info("\n📊 步骤2: 测试按优先级获取钱包")

    # 2.1 高优先级
    try:
        with HyperliquidDB() as db:
            high_priority = db.get_monitored_wallets_by_priority(
                min_pnl=10000,
                min_roi=50,
                days_active=7,
                limit=200
            )
            logger.info(f"✓ 高优先级钱包 (PnL>10K, ROI>50%, 7天内活跃): {len(high_priority)} 个")

            if high_priority:
                top_wallet = high_priority[0]
                logger.info(f"  最佳钱包: {top_wallet['address'][:10]}... "
                           f"(PnL: ${top_wallet.get('discovered_pnl', 0):,.0f}, "
                           f"ROI: {top_wallet.get('discovered_roi', 0):.1f}%)")
    except Exception as e:
        logger.error(f"✗ 获取高优先级钱包失败: {e}")
        high_priority = []

    # 2.2 中优先级
    try:
        with HyperliquidDB() as db:
            medium_priority = db.get_monitored_wallets_by_priority(
                min_pnl=5000,
                min_roi=30,
                days_active=30,
                limit=500
            )
            logger.info(f"✓ 中优先级钱包 (PnL>5K, ROI>30%, 30天内活跃): {len(medium_priority)} 个")
    except Exception as e:
        logger.error(f"✗ 获取中优先级钱包失败: {e}")
        medium_priority = []

    # 3. 测试监控逻辑（只测试配置模式，避免API调用）
    logger.info("\n📊 步骤3: 测试监控逻辑 (config模式)")
    try:
        results = await hyperliquid_collector.monitor_all_addresses(
            hours=1,
            priority='config',
            hyperliquid_db=None  # 不传db，使用配置文件
        )
        logger.info(f"✓ 配置模式监控: {len(results)} 个地址")
    except Exception as e:
        logger.error(f"✗ 配置模式监控失败: {e}")

    # 4. 测试从数据库加载地址（模拟，不实际调用API）
    logger.info("\n📊 步骤4: 测试从数据库加载地址 (模拟)")
    try:
        # 测试高优先级加载
        logger.info("  测试加载高优先级钱包...")
        if high_priority:
            logger.info(f"  ✓ 可以加载 {len(high_priority)} 个高优先级钱包")
            logger.info(f"    每次监控耗时: ~{len(high_priority)} 秒 (每个地址1秒)")
            logger.info(f"    监控频率: 每5分钟")
        else:
            logger.warning("  ⚠ 没有找到高优先级钱包")
            logger.info("    建议: 降低阈值 (PnL: 10K→5K, ROI: 50%→30%)")

        # 测试中优先级加载
        logger.info("  测试加载中优先级钱包...")
        if medium_priority:
            logger.info(f"  ✓ 可以加载 {len(medium_priority)} 个中优先级钱包")
            logger.info(f"    每次监控耗时: ~{len(medium_priority)} 秒")
            logger.info(f"    监控频率: 每1小时")
        else:
            logger.warning("  ⚠ 没有找到中优先级钱包")

        # 测试全量加载
        logger.info("  测试加载全量钱包...")
        if all_wallets:
            logger.info(f"  ✓ 可以加载 {len(all_wallets)} 个活跃钱包")
            logger.info(f"    每次监控耗时: ~{len(all_wallets)} 秒 (~{len(all_wallets)/60:.1f} 分钟)")
            logger.info(f"    监控频率: 每6小时")

    except Exception as e:
        logger.error(f"✗ 测试失败: {e}")

    # 5. 计算监控效率
    logger.info("\n📊 步骤5: 预估监控效率")
    try:
        # 假设每个地址监控耗时1秒
        high_time = len(high_priority) * 1  # 秒
        medium_time = len(medium_priority) * 1
        all_time = len(all_wallets) * 1

        logger.info(f"  高优先级 (每5分钟): {high_time} 秒 (~{high_time/60:.1f} 分钟)")
        logger.info(f"  中优先级 (每小时): {medium_time} 秒 (~{medium_time/60:.1f} 分钟)")
        logger.info(f"  全量扫描 (每6小时): {all_time} 秒 (~{all_time/60:.1f} 分钟)")

        # 检查是否有任务耗时过长
        if high_time > 240:  # 超过4分钟
            logger.warning(f"  ⚠ 高优先级监控耗时较长 ({high_time/60:.1f}分钟)")
            logger.info(f"    建议: 减少高优先级钱包数量 (200 → 100)")

        if all_time > 1800:  # 超过30分钟
            logger.warning(f"  ⚠ 全量扫描耗时较长 ({all_time/60:.1f}分钟)")
            logger.info(f"    建议: 考虑分批监控或减少监控钱包数")

    except Exception as e:
        logger.error(f"✗ 计算失败: {e}")

    # 6. 数据量预估
    logger.info("\n📊 步骤6: 预估每日数据量")
    try:
        # 假设每个钱包平均每小时产生0.5笔交易
        trades_per_wallet_per_hour = 0.5

        high_trades = len(high_priority) * trades_per_wallet_per_hour * 288  # 288次/天
        medium_trades = len(medium_priority) * trades_per_wallet_per_hour * 24  # 24次/天
        all_trades = len(all_wallets) * trades_per_wallet_per_hour * 4  # 4次/天

        total_trades = high_trades + medium_trades + all_trades

        logger.info(f"  高优先级 (每5分钟): ~{high_trades:,.0f} 笔交易/天")
        logger.info(f"  中优先级 (每小时): ~{medium_trades:,.0f} 笔交易/天")
        logger.info(f"  全量扫描 (每6小时): ~{all_trades:,.0f} 笔交易/天")
        logger.info(f"  ----------------------------------------")
        logger.info(f"  总计: ~{total_trades:,.0f} 笔交易/天")

        if total_trades > 10000:
            logger.info(f"  ✓ 数据量充足，适合进行量化分析")
        else:
            logger.warning(f"  ⚠ 数据量较少，可能影响分析效果")

    except Exception as e:
        logger.error(f"✗ 计算失败: {e}")

    logger.info("\n" + "=" * 80)
    logger.info("测试完成")
    logger.info("=" * 80)

    # 7. 给出建议
    logger.info("\n💡 建议:")
    if len(all_wallets) == 0:
        logger.warning("⚠ 数据库中没有监控钱包，需要先运行 Hyperliquid 排行榜采集")
        logger.info("  运行命令: python -m app.collectors.hyperliquid_collector")
    elif len(high_priority) == 0:
        logger.warning("⚠ 没有高优先级钱包，建议降低阈值:")
        logger.info("  修改 hyperliquid_db.py 中的 get_monitored_wallets_by_priority")
        logger.info("  将 min_pnl=10000 改为 min_pnl=5000")
        logger.info("  将 min_roi=50 改为 min_roi=30")
    elif high_time < 300:  # 监控时间合理
        logger.info("✓ 监控效率合理，可以直接启用分级监控")
        logger.info("✓ 修改 config.yaml 确保 hyperliquid.enabled: true，然后重启系统")
    else:
        logger.warning("⚠ 监控耗时较长，建议优化:")
        logger.info("  1. 减少高优先级钱包数量")
        logger.info("  2. 提高优先级阈值")
        logger.info("  3. 考虑使用异步并发监控")


if __name__ == '__main__':
    asyncio.run(test_hyperliquid_priority())
