"""
币安 WebSocket 实时价格服务

使用币安合约 WebSocket 获取实时价格推送，用于高频监控移动止盈/止损
不再依赖轮询，价格变动即时触发回调
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
    """币安 WebSocket 实时价格服务"""

    # 币安合约 WebSocket 地址
    WS_BASE_URL = "wss://fstream.binance.com/ws"

    def __init__(self):
        self.prices: Dict[str, float] = {}  # symbol -> price
        self.max_prices: Dict[str, float] = {}  # symbol -> max_price (用于做多)
        self.min_prices: Dict[str, float] = {}  # symbol -> min_price (用于做空)
        self.subscribed_symbols: Set[str] = set()
        self.callbacks: List[Callable[[str, float], None]] = []  # 价格更新回调
        self.ws = None
        self.running = False
        self._reconnect_delay = 5  # 重连延迟（秒）
        self._last_prices: Dict[str, float] = {}  # 上次价格，用于判断是否有变化

    def add_callback(self, callback: Callable[[str, float], None]):
        """添加价格更新回调"""
        self.callbacks.append(callback)

    def remove_callback(self, callback: Callable[[str, float], None]):
        """移除价格更新回调"""
        if callback in self.callbacks:
            self.callbacks.remove(callback)

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
        """转换交易对格式：BTC/USDT -> btcusdt@markPrice"""
        # 移除斜杠并转小写
        stream_symbol = symbol.replace('/', '').lower()
        # 使用 markPrice 流获取实时标记价格
        return f"{stream_symbol}@markPrice@1s"  # 每秒更新

    def _stream_to_symbol(self, stream: str) -> str:
        """转换流名称回交易对格式：btcusdt -> BTC/USDT"""
        # 从 btcusdt@markPrice 提取 btcusdt
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
                "id": int(datetime.now().timestamp())
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
                "id": int(datetime.now().timestamp())
            }
            await self.ws.send(json.dumps(unsubscribe_msg))
            logger.info(f"WebSocket 取消订阅: {symbols_to_remove}")

    def _on_price_update(self, symbol: str, price: float):
        """价格更新时触发"""
        old_price = self.prices.get(symbol, 0)
        self.prices[symbol] = price

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

            # 处理 markPrice 消息
            if 'e' in data and data['e'] == 'markPriceUpdate':
                stream_symbol = data['s'].lower()  # BTCUSDT -> btcusdt
                symbol = self._stream_to_symbol(stream_symbol)
                price = float(data['p'])  # 标记价格
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
                # 构建订阅 URL
                if self.subscribed_symbols:
                    streams = [self._symbol_to_stream(s) for s in self.subscribed_symbols]
                    url = f"{self.WS_BASE_URL}/{'/'.join(streams)}"
                else:
                    url = self.WS_BASE_URL

                logger.info(f"WebSocket 连接中: {url[:80]}...")

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
_ws_price_service: Optional[BinanceWSPriceService] = None


def get_ws_price_service() -> BinanceWSPriceService:
    """获取 WebSocket 价格服务单例"""
    global _ws_price_service
    if _ws_price_service is None:
        _ws_price_service = BinanceWSPriceService()
    return _ws_price_service


async def init_ws_price_service(symbols: List[str] = None) -> BinanceWSPriceService:
    """初始化并启动 WebSocket 价格服务"""
    service = get_ws_price_service()
    if not service.is_running():
        # 在后台启动
        asyncio.create_task(service.start(symbols))
        # 等待连接建立
        await asyncio.sleep(2)
    return service
