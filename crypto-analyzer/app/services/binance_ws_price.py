"""
币安 WebSocket 实时价格服务

支持现货和合约市场的实时价格推送
- 合约市场: 使用 markPrice 标记价格（避免操纵）
- 现货市场: 使用 ticker 价格（实时成交价）

用于高频监控移动止盈/止损，不再依赖轮询
"""

import asyncio
import json
from typing import Dict, Set, Callable, Optional, List
from datetime import datetime
from loguru import logger

try:
    import websockets
except ImportError:
    websockets = None
    logger.warning("websockets 未安装，请运行: pip install websockets")


class BinanceWSPriceService:
    """币安 WebSocket 实时价格服务 - 支持现货和合约"""

    # 币安 WebSocket 地址
    WS_FUTURES_URL = "wss://fstream.binance.com/ws"  # 合约
    WS_SPOT_URL = "wss://stream.binance.com:9443/ws"  # 现货

    def __init__(self, market_type: str = 'futures'):
        """
        初始化 WebSocket 服务

        Args:
            market_type: 市场类型 'futures' 或 'spot'
        """
        self.market_type = market_type
        self.prices: Dict[str, float] = {}  # symbol -> price
        self.max_prices: Dict[str, float] = {}  # symbol -> max_price (用于做多)
        self.min_prices: Dict[str, float] = {}  # symbol -> min_price (用于做空)
        self.subscribed_symbols: Set[str] = set()
        self.callbacks: List[Callable[[str, float], None]] = []  # 价格更新回调
        self.ws = None
        self.running = False
        self._reconnect_delay = 5  # 重连延迟（秒）
        self._last_prices: Dict[str, float] = {}  # 上次价格，用于判断是否有变化

        # 健康检查相关
        self._last_update_time: Optional[datetime] = None  # 最后收到数据的时间
        self._health_check_interval = 5  # 健康检查间隔（秒）
        self._stale_threshold = 10  # 数据过期阈值（秒），超过此时间未收到数据视为不健康
        self._health_callbacks: List[Callable[[bool, str], None]] = []  # 健康状态回调 (is_healthy, reason)

    def add_callback(self, callback: Callable[[str, float], None]):
        """添加价格更新回调"""
        self.callbacks.append(callback)

    def remove_callback(self, callback: Callable[[str, float], None]):
        """移除价格更新回调"""
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    def add_health_callback(self, callback: Callable[[bool, str], None]):
        """添加健康状态回调"""
        self._health_callbacks.append(callback)

    def remove_health_callback(self, callback: Callable[[bool, str], None]):
        """移除健康状态回调"""
        if callback in self._health_callbacks:
            self._health_callbacks.remove(callback)

    def get_last_update_time(self) -> Optional[datetime]:
        """获取最后更新时间"""
        return self._last_update_time

    def is_healthy(self) -> bool:
        """检查 WebSocket 服务是否健康"""
        if not self.running or self.ws is None:
            return False
        if self._last_update_time is None:
            return False
        elapsed = (datetime.utcnow() - self._last_update_time).total_seconds()
        return elapsed < self._stale_threshold

    def get_health_status(self) -> dict:
        """获取详细的健康状态"""
        elapsed = None
        if self._last_update_time:
            elapsed = (datetime.utcnow() - self._last_update_time).total_seconds()

        return {
            'running': self.running,
            'connected': self.ws is not None,
            'healthy': self.is_healthy(),
            'last_update_time': self._last_update_time.isoformat() if self._last_update_time else None,
            'seconds_since_update': round(elapsed, 2) if elapsed else None,
            'stale_threshold': self._stale_threshold,
            'subscribed_symbols': list(self.subscribed_symbols),
            'prices_count': len(self.prices)
        }

    def _notify_health_change(self, is_healthy: bool, reason: str):
        """通知健康状态变化"""
        for callback in self._health_callbacks:
            try:
                callback(is_healthy, reason)
            except Exception as e:
                logger.error(f"健康状态回调执行失败: {e}")

    def get_price(self, symbol: str) -> Optional[float]:
        """获取当前价格"""
        return self.prices.get(symbol)

    def get_max_price(self, symbol: str) -> Optional[float]:
        """获取订阅以来的最高价（用于做多的移动止盈）"""
        return self.max_prices.get(symbol)

    def get_min_price(self, symbol: str) -> Optional[float]:
        """获取订阅以来的最低价（用于做空的移动止盈）"""
        return self.min_prices.get(symbol)

    def reset_price_tracking(self, symbol: str, current_price: float = None):
        """重置价格追踪（开仓时调用）"""
        if current_price:
            self.max_prices[symbol] = current_price
            self.min_prices[symbol] = current_price
        elif symbol in self.prices:
            self.max_prices[symbol] = self.prices[symbol]
            self.min_prices[symbol] = self.prices[symbol]

    def _symbol_to_stream(self, symbol: str) -> str:
        """转换交易对格式：BTC/USDT -> btcusdt@markPrice 或 btcusdt@ticker"""
        # 移除斜杠并转小写
        stream_symbol = symbol.replace('/', '').lower()

        if self.market_type == 'futures':
            # 合约: 使用 markPrice 流获取实时标记价格（避免操纵）
            return f"{stream_symbol}@markPrice@1s"  # 每秒更新
        else:
            # 现货: 使用 ticker 流获取实时价格
            return f"{stream_symbol}@ticker"  # 实时推送

    def _stream_to_symbol(self, stream: str) -> str:
        """转换流名称回交易对格式：btcusdt -> BTC/USDT"""
        # 从 btcusdt@markPrice 或 btcusdt@ticker 提取 btcusdt
        base = stream.split('@')[0].upper()
        # 假设都是 USDT 交易对
        if base.endswith('USDT'):
            return base[:-4] + '/USDT'
        return base

    async def subscribe(self, symbols: List[str]):
        """订阅交易对的价格"""
        new_symbols = set(symbols) - self.subscribed_symbols
        if not new_symbols:
            return

        self.subscribed_symbols.update(new_symbols)

        # 初始化价格追踪
        for symbol in new_symbols:
            if symbol not in self.max_prices:
                self.max_prices[symbol] = 0
            if symbol not in self.min_prices:
                self.min_prices[symbol] = float('inf')

        # 如果 WebSocket 已连接，发送订阅请求
        if self.ws:
            streams = [self._symbol_to_stream(s) for s in new_symbols]
            subscribe_msg = {
                "method": "SUBSCRIBE",
                "params": streams,
                "id": int(datetime.utcnow().timestamp())
            }
            await self.ws.send(json.dumps(subscribe_msg))
            logger.info(f"WebSocket 订阅新交易对: {new_symbols}")

    async def unsubscribe(self, symbols: List[str]):
        """取消订阅交易对"""
        symbols_to_remove = set(symbols) & self.subscribed_symbols
        if not symbols_to_remove:
            return

        self.subscribed_symbols -= symbols_to_remove

        # 清理价格数据
        for symbol in symbols_to_remove:
            self.prices.pop(symbol, None)
            self.max_prices.pop(symbol, None)
            self.min_prices.pop(symbol, None)

        # 如果 WebSocket 已连接，发送取消订阅请求
        if self.ws:
            streams = [self._symbol_to_stream(s) for s in symbols_to_remove]
            unsubscribe_msg = {
                "method": "UNSUBSCRIBE",
                "params": streams,
                "id": int(datetime.utcnow().timestamp())
            }
            await self.ws.send(json.dumps(unsubscribe_msg))
            logger.info(f"WebSocket 取消订阅: {symbols_to_remove}")

    def _on_price_update(self, symbol: str, price: float):
        """价格更新时触发"""
        old_price = self.prices.get(symbol, 0)
        self.prices[symbol] = price

        # 更新最后收到数据的时间
        was_healthy = self.is_healthy()
        self._last_update_time = datetime.utcnow()

        # 如果之前不健康，现在恢复了，通知健康状态变化
        if not was_healthy and self.is_healthy():
            logger.info("✅ WebSocket 数据恢复正常")
            self._notify_health_change(True, "数据恢复正常")

        # 更新最高/最低价
        if price > self.max_prices.get(symbol, 0):
            self.max_prices[symbol] = price
        if price < self.min_prices.get(symbol, float('inf')):
            self.min_prices[symbol] = price

        # 只有价格有变化时才触发回调
        if abs(price - old_price) > 0.000001:
            for callback in self.callbacks:
                try:
                    callback(symbol, price)
                except Exception as e:
                    logger.error(f"价格回调执行失败: {e}")

    async def _handle_message(self, message: str):
        """处理 WebSocket 消息"""
        try:
            data = json.loads(message)

            # 忽略订阅确认消息
            if 'result' in data or 'id' in data:
                return

            if self.market_type == 'futures':
                # 处理合约 markPrice 消息
                if 'e' in data and data['e'] == 'markPriceUpdate':
                    stream_symbol = data['s'].lower()  # BTCUSDT -> btcusdt
                    symbol = self._stream_to_symbol(stream_symbol)
                    price = float(data['p'])  # 标记价格
                    self._on_price_update(symbol, price)
            else:
                # 处理现货 ticker 消息
                if 'e' in data and data['e'] == '24hrTicker':
                    stream_symbol = data['s'].lower()  # BTCUSDT -> btcusdt
                    symbol = self._stream_to_symbol(stream_symbol)
                    price = float(data['c'])  # 最新成交价
                    self._on_price_update(symbol, price)

        except json.JSONDecodeError:
            logger.warning(f"WebSocket 消息解析失败: {message[:100]}")
        except Exception as e:
            logger.error(f"处理 WebSocket 消息异常: {e}")

    async def _connect(self):
        """建立 WebSocket 连接"""
        if not websockets:
            logger.error("websockets 库未安装，无法启动 WebSocket 服务")
            return

        while self.running:
            try:
                # 选择正确的 WebSocket URL
                base_url = self.WS_FUTURES_URL if self.market_type == 'futures' else self.WS_SPOT_URL

                # 构建订阅 URL
                if self.subscribed_symbols:
                    streams = [self._symbol_to_stream(s) for s in self.subscribed_symbols]
                    url = f"{base_url}/{'/'.join(streams)}"
                else:
                    url = base_url

                market_label = "合约" if self.market_type == 'futures' else "现货"
                logger.info(f"WebSocket [{market_label}] 连接中: {url[:80]}...")

                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self.ws = ws
                    logger.info(f"✅ WebSocket 已连接，订阅 {len(self.subscribed_symbols)} 个交易对")

                    # 如果是空连接，需要发送订阅请求
                    if not self.subscribed_symbols:
                        pass  # 等待 subscribe() 调用

                    async for message in ws:
                        if not self.running:
                            break
                        await self._handle_message(message)

            except asyncio.CancelledError:
                logger.info("WebSocket 连接被取消")
                break
            except Exception as e:
                logger.error(f"WebSocket 连接异常: {e}")
                if self.running:
                    logger.info(f"{self._reconnect_delay}秒后重连...")
                    await asyncio.sleep(self._reconnect_delay)

        self.ws = None

    async def _health_check_loop(self):
        """健康检查循环 - 定期检查数据是否过期"""
        logger.info(f"🏥 WebSocket 健康检查服务已启动（间隔: {self._health_check_interval}秒，阈值: {self._stale_threshold}秒）")
        last_healthy = True

        while self.running:
            await asyncio.sleep(self._health_check_interval)

            if not self.running:
                break

            current_healthy = self.is_healthy()

            # 健康状态变化时触发回调
            if last_healthy and not current_healthy:
                elapsed = 0
                if self._last_update_time:
                    elapsed = (datetime.utcnow() - self._last_update_time).total_seconds()
                reason = f"超过 {self._stale_threshold} 秒未收到数据（已过 {elapsed:.1f}s）"
                logger.warning(f"⚠️ WebSocket 数据过期: {reason}")
                self._notify_health_change(False, reason)
            elif not last_healthy and current_healthy:
                # 恢复健康的通知在 _on_price_update 中处理
                pass

            last_healthy = current_healthy

        logger.info("WebSocket 健康检查服务已停止")

    async def start(self, symbols: List[str] = None):
        """启动 WebSocket 服务"""
        if self.running:
            logger.warning("WebSocket 服务已在运行")
            return

        self.running = True

        if symbols:
            self.subscribed_symbols = set(symbols)
            for symbol in symbols:
                self.max_prices[symbol] = 0
                self.min_prices[symbol] = float('inf')

        logger.info(f"🚀 启动 WebSocket 实时价格服务，初始订阅: {self.subscribed_symbols}")

        # 启动健康检查任务
        asyncio.create_task(self._health_check_loop())

        await self._connect()

    async def stop(self):
        """停止 WebSocket 服务"""
        logger.info("正在停止 WebSocket 服务...")
        self.running = False

        if self.ws:
            await self.ws.close()
            self.ws = None

        logger.info("WebSocket 服务已停止")

    def is_running(self) -> bool:
        """检查服务是否运行中"""
        return self.running and self.ws is not None


# 全局单例
_ws_price_service_futures: Optional[BinanceWSPriceService] = None
_ws_price_service_spot: Optional[BinanceWSPriceService] = None


def get_ws_price_service(market_type: str = 'futures') -> BinanceWSPriceService:
    """
    获取 WebSocket 价格服务单例

    Args:
        market_type: 市场类型 'futures' 或 'spot'

    Returns:
        对应市场的 WebSocket 服务实例
    """
    global _ws_price_service_futures, _ws_price_service_spot

    if market_type == 'futures':
        if _ws_price_service_futures is None:
            _ws_price_service_futures = BinanceWSPriceService(market_type='futures')
        return _ws_price_service_futures
    else:
        if _ws_price_service_spot is None:
            _ws_price_service_spot = BinanceWSPriceService(market_type='spot')
        return _ws_price_service_spot


async def init_ws_price_service(symbols: List[str] = None, market_type: str = 'futures') -> BinanceWSPriceService:
    """
    初始化并启动 WebSocket 价格服务

    Args:
        symbols: 要订阅的交易对列表
        market_type: 市场类型 'futures' 或 'spot'
    """
    service = get_ws_price_service(market_type)
    if not service.is_running():
        # 在后台启动
        asyncio.create_task(service.start(symbols))
        # 等待连接建立
        await asyncio.sleep(2)
    return service
