"""
Hyperliquid 数据采集器（修复版本）
监控 Hyperliquid DEX 上的大户交易和聪明钱地址
支持：仓位追踪、大额交易、PnL分析、清算数据
"""

import asyncio
import aiohttp
import sys
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from loguru import logger
from decimal import Decimal

# Windows系统特殊处理
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class HyperliquidCollector:
    """Hyperliquid 数据采集器"""

    def __init__(self, config: dict):
        """初始化 Hyperliquid 采集器"""
        self.config = config
        self.hyperliquid_config = config.get('hyperliquid', {})

        # API端点
        self.api_url = 'https://api.hyperliquid.xyz/info'

        # 代理配置
        smart_money_config = config.get('smart_money', {})
        self.proxy = smart_money_config.get('proxy') if smart_money_config else None
        if self.proxy and self.proxy.strip() == '':
            self.proxy = None

        # 监控地址列表 - 添加None检查
        addresses = self.hyperliquid_config.get('addresses')
        self.monitored_addresses = addresses if addresses is not None else []

        # 最小交易金额阈值(USD)
        self.min_trade_usd = self.hyperliquid_config.get('min_trade_usd', 50000)

        logger.info(f"Hyperliquid 采集器初始化完成 - 监控地址数: {len(self.monitored_addresses)}")
        if self.proxy:
            logger.info(f"使用代理: {self.proxy}")

    async def fetch_leaderboard(self, period: str = "day") -> List[Dict]:
        """获取PnL排行榜"""
        try:
            payload = {"type": "leaderboard", "req": period}
            timeout = aiohttp.ClientTimeout(total=30, connect=10)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.api_url, json=payload, proxy=self.proxy, ssl=False) as response:
                    if response.status == 200:
                        leaderboard = await response.json()
                        logger.info(f"获取 {period} 排行榜: {len(leaderboard) if leaderboard else 0} 个交易者")
                        return leaderboard if leaderboard else []
                    else:
                        logger.error(f"获取排行榜失败: HTTP {response.status}")
                        return []
        except Exception as e:
            logger.error(f"获取排行榜异常: {e}")
            return []

    async def discover_smart_traders(self, period: str = "week", min_pnl: float = 10000) -> List[Dict]:
        """从排行榜发现聪明交易者"""
        try:
            logger.info(f"发现 {period} 排行榜上的聪明交易者...")
            leaderboard = await self.fetch_leaderboard(period)

            smart_traders = []
            for entry in leaderboard:
                account_value = float(entry.get('accountValue', 0))
                pnl = float(entry.get('pnl', 0))
                vlm = float(entry.get('vlm', 0))
                user = entry.get('user', '')

                if pnl >= min_pnl and account_value > 0:
                    roi = (pnl / account_value) * 100 if account_value > 0 else 0
                    trader = {
                        'address': user,
                        'pnl': pnl,
                        'account_value': account_value,
                        'roi': roi,
                        'volume': vlm,
                        'period': period,
                        'discovered_at': datetime.now()
                    }
                    smart_traders.append(trader)

            logger.info(f"发现 {len(smart_traders)} 个符合条件的聪明交易者")
            return smart_traders
        except Exception as e:
            logger.error(f"发现聪明交易者失败: {e}")
            return []
