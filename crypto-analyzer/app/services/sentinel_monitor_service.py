"""
哨兵单监控服务 (Sentinel Monitor Service)

后台服务，定期检查哨兵单的止盈/止损触发情况
优先使用 WebSocket 实时价格，数据库作为备用
"""

import asyncio
import logging
import pymysql
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from app.services.sentinel_order_manager import SentinelOrderManager
from app.services.binance_ws_price import get_ws_price_service

logger = logging.getLogger(__name__)


class SentinelMonitorService:
    """哨兵单监控服务"""

    CHECK_INTERVAL = 5  # 检查间隔（秒）

    def __init__(self, db_config: Dict, on_recovery_callback=None):
        """
        初始化监控服务

        Args:
            db_config: 数据库配置
            on_recovery_callback: 恢复时的回调函数 callback(direction: str)
        """
        self.db_config = db_config
        self.sentinel_manager = SentinelOrderManager(db_config)
        self.on_recovery_callback = on_recovery_callback
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """启动监控服务"""
        if self._running:
            logger.warning("[哨兵监控] 服务已在运行中")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("[哨兵监控] 服务已启动")

    async def stop(self):
        """停止监控服务"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[哨兵监控] 服务已停止")

    async def _monitor_loop(self):
        """监控循环"""
        while self._running:
            try:
                await self._check_sentinels()
            except Exception as e:
                logger.error(f"[哨兵监控] 检查出错: {e}")

            await asyncio.sleep(self.CHECK_INTERVAL)

    async def _check_sentinels(self):
        """检查所有哨兵单"""
        # 获取所有活跃哨兵单的交易对
        open_orders = self.sentinel_manager.get_open_sentinels()

        if not open_orders:
            return

        # 获取需要的交易对
        symbols = list(set(order['symbol'] for order in open_orders))

        # 获取当前价格
        current_prices = await self._get_current_prices(symbols)

        if not current_prices:
            return

        # 检查并更新哨兵单
        result = self.sentinel_manager.check_and_update_sentinel_orders(current_prices)

        # 处理恢复
        for direction in ['long', 'short']:
            if result['recovery'][direction]:
                logger.info(f"🎉 [哨兵监控] {direction.upper()} 方向触发恢复!")

                # 清除该方向的未平仓哨兵单
                self.sentinel_manager.clear_open_sentinels(direction)

                # 调用恢复回调
                if self.on_recovery_callback:
                    try:
                        self.on_recovery_callback(direction)
                    except Exception as e:
                        logger.error(f"[哨兵监控] 恢复回调失败: {e}")

    def _get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config.get('host', 'localhost'),
            port=self.db_config.get('port', 3306),
            user=self.db_config.get('user', 'root'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    async def _get_current_prices(self, symbols: list) -> Dict[str, float]:
        """
        获取当前价格

        优先使用 WebSocket 实时价格，数据库作为备用
        """
        prices = {}
        missing_symbols = []

        # 1. 优先从 WebSocket 获取实时价格
        try:
            ws_service = get_ws_price_service()
            if ws_service.is_healthy():
                for symbol in symbols:
                    price = ws_service.get_price(symbol)
                    if price and price > 0:
                        prices[symbol] = price
                    else:
                        missing_symbols.append(symbol)
            else:
                # WebSocket 不健康，全部从数据库获取
                missing_symbols = symbols
        except Exception as e:
            logger.warning(f"[哨兵监控] WebSocket 获取价格失败: {e}")
            missing_symbols = symbols

        # 2. 从数据库获取缺失的价格
        if missing_symbols:
            try:
                connection = self._get_db_connection()

                with connection.cursor() as cursor:
                    for symbol in missing_symbols:
                        try:
                            cursor.execute("""
                                SELECT price FROM price_data
                                WHERE symbol = %s
                                ORDER BY timestamp DESC LIMIT 1
                            """, (symbol,))
                            row = cursor.fetchone()
                            if row:
                                prices[symbol] = float(row['price'])
                        except Exception as e:
                            logger.warning(f"[哨兵监控] 数据库获取 {symbol} 价格失败: {e}")

                connection.close()

            except Exception as e:
                logger.error(f"[哨兵监控] 数据库获取价格失败: {e}")

        return prices

    def get_status(self) -> Dict:
        """获取监控服务状态"""
        stats = self.sentinel_manager.get_sentinel_stats()

        return {
            'running': self._running,
            'check_interval': self.CHECK_INTERVAL,
            'stats': stats,
            'consecutive_wins': {
                'long': self.sentinel_manager.get_consecutive_wins('long'),
                'short': self.sentinel_manager.get_consecutive_wins('short')
            },
            'wins_required': SentinelOrderManager.CONSECUTIVE_WINS_REQUIRED
        }

    def create_sentinel(
        self,
        direction: str,
        symbol: str,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        strategy_id: int = None
    ) -> Optional[int]:
        """
        创建哨兵单（供外部调用）

        Returns:
            哨兵单ID
        """
        return self.sentinel_manager.create_sentinel_order(
            direction=direction,
            symbol=symbol,
            entry_price=entry_price,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            strategy_id=strategy_id
        )

    def is_recovery_triggered(self, direction: str) -> bool:
        """检查是否触发恢复"""
        return self.sentinel_manager.is_recovery_triggered(direction)

    def clear_sentinels(self, direction: str = None):
        """清除哨兵单"""
        self.sentinel_manager.clear_open_sentinels(direction)


# 全局实例
_sentinel_monitor: Optional[SentinelMonitorService] = None


def get_sentinel_monitor(db_config: Dict = None, on_recovery_callback=None) -> SentinelMonitorService:
    """获取哨兵监控服务实例（单例）"""
    global _sentinel_monitor

    if _sentinel_monitor is None:
        if db_config is None:
            raise ValueError("首次初始化需要提供 db_config")
        _sentinel_monitor = SentinelMonitorService(db_config, on_recovery_callback)

    return _sentinel_monitor
