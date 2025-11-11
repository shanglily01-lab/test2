#!/usr/bin/env python3
"""
强制刷新 Hyperliquid 聪明钱活动数据
包括重新监控钱包并更新持仓数据（含杠杆倍数）
"""

import sys
import asyncio
import logging
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
from app.scheduler import UnifiedDataScheduler
from app.services.cache_update_service import CacheUpdateService
from app.database.db_service import DatabaseService

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


async def force_refresh_hyperliquid():
    """强制刷新 Hyperliquid 数据"""
    try:
        logger.info("=" * 60)
        logger.info("🚀 开始强制刷新 Hyperliquid 聪明钱活动数据")
        logger.info("=" * 60)

        # 加载配置
        config_path = project_root / 'config.yaml'
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        db_service = DatabaseService(config.get('database', {}))
        
        # 获取所有交易对
        symbols = config.get('symbols', [])
        if not symbols:
            logger.warning("配置文件中没有找到交易对列表")
            return

        logger.info(f"📊 交易对数量: {len(symbols)}")
        logger.info(f"📋 交易对列表: {', '.join(symbols[:10])}{'...' if len(symbols) > 10 else ''}")

        # 1. 重新监控钱包并更新持仓数据（包括杠杆）
        logger.info("\n" + "=" * 60)
        logger.info("步骤 1/3: 重新监控 Hyperliquid 钱包并更新持仓数据")
        logger.info("=" * 60)
        
        scheduler = UnifiedDataScheduler(config_path=str(config_path))
        
        # 监控所有钱包（包括高优先级和普通优先级）
        logger.info("📡 开始监控所有钱包...")
        await scheduler.monitor_hyperliquid_wallets(priority='all')
        logger.info("✅ 钱包监控完成，持仓数据（含杠杆）已更新")

        # 2. 更新 Hyperliquid 聚合缓存
        logger.info("\n" + "=" * 60)
        logger.info("步骤 2/3: 更新 Hyperliquid 聚合缓存")
        logger.info("=" * 60)
        
        cache_service = CacheUpdateService(db_service)
        await cache_service.update_hyperliquid_aggregation(symbols)
        logger.info("✅ Hyperliquid 聚合缓存更新完成")

        # 3. 验证数据
        logger.info("\n" + "=" * 60)
        logger.info("步骤 3/3: 验证数据更新情况")
        logger.info("=" * 60)
        
        session = db_service.get_session()
        try:
            # 检查最近的交易和杠杆数据
            from sqlalchemy import text
            result = session.execute(text("""
                SELECT
                    t.coin,
                    t.side,
                    t.size,
                    t.notional_usd,
                    COALESCE(p.leverage, 1) as leverage,
                    w.label as wallet_label,
                    t.trade_time
                FROM hyperliquid_wallet_trades t
                LEFT JOIN hyperliquid_monitored_wallets w ON t.address = w.address
                LEFT JOIN (
                    SELECT p.trader_id, p.coin, p.leverage, p.snapshot_time,
                           ROW_NUMBER() OVER (PARTITION BY p.trader_id, p.coin ORDER BY p.snapshot_time DESC) as rn
                    FROM hyperliquid_wallet_positions p
                ) p ON t.trader_id = p.trader_id
                    AND t.coin = p.coin
                    AND p.rn = 1
                    AND p.snapshot_time <= t.trade_time
                    AND p.snapshot_time >= DATE_SUB(t.trade_time, INTERVAL 1 HOUR)
                WHERE t.trade_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                    AND w.is_monitoring = 1
                ORDER BY t.trade_time DESC
                LIMIT 10
            """))
            
            trades = result.fetchall()
            if trades:
                logger.info(f"✅ 找到 {len(trades)} 条最近的交易记录（含杠杆信息）:")
                logger.info("-" * 60)
                for trade in trades:
                    coin = trade[0]
                    side = trade[1]
                    size = float(trade[2]) if trade[2] else 0
                    notional = float(trade[3]) if trade[3] else 0
                    leverage = float(trade[4]) if trade[4] else 1
                    wallet = trade[5] or 'Unknown'
                    trade_time = trade[6]
                    
                    leverage_str = f"{leverage:.2f}x" if leverage > 1 else "1x (默认)"
                    logger.info(f"  {wallet[:20]:20s} | {coin:8s} | {side:5s} | "
                              f"数量: {size:>12.2f} | 金额: ${notional:>12,.2f} | "
                              f"杠杆: {leverage_str:>8s} | {trade_time}")
            else:
                logger.warning("⚠️  没有找到最近的交易记录")
            
            # 检查持仓数据中的杠杆
            result = session.execute(text("""
                SELECT
                    p.coin,
                    p.side,
                    p.leverage,
                    p.notional_usd,
                    p.snapshot_time,
                    w.label as wallet_label
                FROM hyperliquid_wallet_positions p
                LEFT JOIN hyperliquid_traders t ON p.trader_id = t.id
                LEFT JOIN hyperliquid_monitored_wallets w ON t.address = w.address
                WHERE p.snapshot_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                    AND w.is_monitoring = 1
                ORDER BY p.snapshot_time DESC
                LIMIT 10
            """))
            
            positions = result.fetchall()
            if positions:
                logger.info(f"\n✅ 找到 {len(positions)} 条最近的持仓记录（含杠杆信息）:")
                logger.info("-" * 60)
                for pos in positions:
                    coin = pos[0]
                    side = pos[1]
                    leverage = float(pos[2]) if pos[2] else 1
                    notional = float(pos[3]) if pos[3] else 0
                    snapshot_time = pos[4]
                    wallet = pos[5] or 'Unknown'
                    
                    leverage_str = f"{leverage:.2f}x" if leverage > 1 else "1x (默认)"
                    logger.info(f"  {wallet[:20]:20s} | {coin:8s} | {side:5s} | "
                              f"金额: ${notional:>12,.2f} | 杠杆: {leverage_str:>8s} | {snapshot_time}")
            else:
                logger.warning("⚠️  没有找到最近的持仓记录")
                
        finally:
            session.close()

        logger.info("\n" + "=" * 60)
        logger.info("✅ 强制刷新完成！")
        logger.info("=" * 60)
        logger.info("💡 提示: 现在可以刷新 Dashboard 查看更新后的数据")
        logger.info("💡 提示: 如果杠杆倍数仍然显示为1x，可能是该钱包的持仓数据中没有杠杆信息")

    except Exception as e:
        logger.error(f"\n❌ 强制刷新失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # 清理资源
        if 'scheduler' in locals():
            scheduler.stop()
        if 'db_service' in locals():
            db_service.close()


if __name__ == '__main__':
    asyncio.run(force_refresh_hyperliquid())

