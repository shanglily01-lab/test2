"""
Binance WebSocket K线采集器

替代 fast_collector_service.py 的 REST 轮询采集 (路径 B),
避免 IP 被 ban (-1003)。

设计:
- 多个 WS 连接, 按"市场 + 周期"分片
  Phase 1: 仅 U本位 5m + 15m, ~500 streams, 2-3 个连接
  后续扩展: 加 1h/1d 和币本位
- 只处理 k.x == true (K 线 closed) 的消息, 进行中的 K 线丢弃
- 启动顺序: 连 WS (进 buffer) -> REST hydration -> drain buffer 落盘
- 落盘用 run_in_executor 隔离, 不阻塞 WS event loop
- 重连指数退避, 上限 60s
- 24h 自动断连 (币安服务端) 由 websockets 库的重连逻辑兜底

落盘表: kline_data (复用 SmartFuturesCollector.save_klines)
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable, Optional

import websockets
from loguru import logger


WS_BASE_USDT = "wss://fstream.binance.com/stream"
WS_BASE_COIN = "wss://dstream.binance.com/stream"

MAX_STREAMS_PER_CONN = 50           # 单连接上限. 200/100 实测都被 Binance 静默不推, 50 试水
                                    # 5m+15m × 249 symbols / 50 = 10 个连接, 在 300/IP 上限内安全
SUBSCRIBE_RATE_PER_SEC = 5          # 币安建连速率限制
PING_INTERVAL = 20                  # 主动 ping 间隔
PING_TIMEOUT = 10                   # ping 超时
RECONNECT_BACKOFF_BASE_S = 5        # 重连基础退避
RECONNECT_BACKOFF_MAX_S = 60        # 重连退避上限
BUFFER_MAX_SIZE = 5000              # WS buffer 上限, 防内存爆炸
DB_FLUSH_INTERVAL_S = 1.0           # batch flush 间隔
HEALTH_STALE_THRESHOLD_S = 120      # 健康检查阈值 (报告用)
WS_RECV_TIMEOUT_S = 90              # 单次 recv 超时 — 90s 无消息视为僵尸, 主动断开重连
                                    # markPrice@1s 正常每秒推一条, 90s 一条都没有必有问题
STALE_FATAL_COUNT = 5               # 连续 N 次僵尸重连仍无数据 → 严重错误日志


# WS 消息回调签名: (symbol: str, interval: str, kline_dict: dict, market: str) -> Awaitable[None]
OnKlineClosed = Callable[[str, str, dict, str], Awaitable[None]]


class WSKlineConnection:
    """单个 WS 连接, 订阅一批 streams"""

    def __init__(
        self,
        ws_url: str,
        streams: list[str],
        on_kline_closed: OnKlineClosed,
        market: str,  # 'usdt' or 'coin'
        name: str,
    ) -> None:
        self.ws_url = ws_url
        self.streams = streams
        self.on_kline_closed = on_kline_closed
        self.market = market
        self.name = name
        self.last_msg_at: float = 0.0
        self.connected_at: float = 0.0

    def is_healthy(self) -> bool:
        """超过阈值没收到消息视为不健康"""
        if self.last_msg_at == 0:
            return False
        return (time.time() - self.last_msg_at) < HEALTH_STALE_THRESHOLD_S

    async def run_forever(self) -> None:
        """长跑, 自愈重连 (建空连接 + SUBSCRIBE 帧订阅 + recv 超时检测僵尸)

        2026-05-18: 实测 combined URL ?streams=... 在多 streams 时 Binance 静默不推 (200/100/50 都不行),
        改用官方推荐的 SUBSCRIBE 帧方式:
        1. 连 /stream 空 URL
        2. send {"method":"SUBSCRIBE","params":[...],"id":1}
        3. 接收订阅确认后开始拉数据
        """
        backoff = RECONNECT_BACKOFF_BASE_S
        consecutive_stale = 0
        while True:
            try:
                logger.info(f"[{self.name}] 连接 WS (SUBSCRIBE 模式): {len(self.streams)} streams")
                # 用空 URL + SUBSCRIBE 帧, 避免 combined URL ?streams=... 被静默不推
                async with websockets.connect(
                    self.ws_url,  # 不带 ?streams=
                    ping_interval=PING_INTERVAL,
                    ping_timeout=PING_TIMEOUT,
                ) as ws:
                    # 发送 SUBSCRIBE 帧
                    sub_msg = json.dumps({
                        "method": "SUBSCRIBE",
                        "params": self.streams,
                        "id": int(time.time()),
                    })
                    await ws.send(sub_msg)
                    self.connected_at = time.time()
                    self.last_msg_at = time.time()
                    backoff = RECONNECT_BACKOFF_BASE_S
                    logger.info(f"[{self.name}] WS 已连接, SUBSCRIBE 已发送")

                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT_S)
                        except asyncio.TimeoutError:
                            consecutive_stale += 1
                            logger.warning(
                                f"[{self.name}] WS {WS_RECV_TIMEOUT_S}s 无数据 (僵尸连接 #{consecutive_stale}), 主动断开重连"
                            )
                            if consecutive_stale >= STALE_FATAL_COUNT:
                                logger.error(
                                    f"[{self.name}] 连续 {consecutive_stale} 次僵尸重连仍无数据 — "
                                    f"可能 streams 数量/订阅有问题, 检查 MAX_STREAMS_PER_CONN={MAX_STREAMS_PER_CONN}"
                                )
                            break
                        self.last_msg_at = time.time()
                        consecutive_stale = 0
                        await self._handle_msg(msg)
            except (websockets.ConnectionClosed, OSError) as e:
                logger.warning(f"[{self.name}] WS 断开: {e}, {backoff}s 后重连")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[{self.name}] WS 异常: {e}, {backoff}s 后重连")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_S)

    async def _handle_msg(self, msg: str | bytes) -> None:
        """解析 WS 消息, 只对 k.x == true (closed) 的回调"""
        try:
            data = json.loads(msg)
            # 忽略 SUBSCRIBE 帧的确认消息: {"result": null, "id": N}
            if 'result' in data and 'id' in data:
                return
            # combined stream 格式: {"stream":..., "data":{...}}; 也兼容 raw 格式直接 {...}
            k = data.get('data', {}).get('k') if 'data' in data else data.get('k')
            if not k or not k.get('x'):
                # 不是 closed K 线, 直接丢弃 (99% 消息走这里)
                return
            symbol = k['s']
            interval = k['i']
            kline = {
                'open_time': int(k['t']),
                'close_time': int(k['T']),
                'open': k['o'],
                'high': k['h'],
                'low': k['l'],
                'close': k['c'],
                'volume': k['v'],
                'quote_volume': k['q'],
                'trades': int(k['n']),
                'taker_buy_base': k['V'],
                'taker_buy_quote': k['Q'],
            }
            await self.on_kline_closed(symbol, interval, kline, self.market)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"[{self.name}] WS 消息解析失败: {e}")


class WSKlineCollector:
    """采集器主类: 管理多个 WS 连接 + buffer + batch DB flusher"""

    def __init__(
        self,
        db_config: dict,
        usdt_symbols: list[str],
        coin_symbols: list[str],
        intervals: list[str],
    ) -> None:
        """
        Args:
            db_config: MySQL 连接配置
            usdt_symbols: U本位 symbols (Binance 格式, 如 ['BTCUSDT', ...])
            coin_symbols: 币本位 symbols (Binance 格式, 如 ['BTCUSD_PERP', ...])
            intervals: K线周期列表 (如 ['5m', '15m'])
        """
        self.db_config = db_config
        self.usdt_symbols = usdt_symbols
        self.coin_symbols = coin_symbols
        self.intervals = intervals
        self.buffer: list[dict] = []
        self.buffer_lock = asyncio.Lock()
        self.connections: list[WSKlineConnection] = []
        self._stats = {
            'total_closed': 0,
            'total_flushed': 0,
            'flush_errors': 0,
        }

    def _build_shards(self) -> list[tuple[str, str, list[str]]]:
        """
        按 (市场, 周期) 分片
        返回: [(market, interval, streams), ...]
        """
        shards: list[tuple[str, str, list[str]]] = []
        # U本位: 按周期分, 每个周期占一个或多个连接
        for interval in self.intervals:
            streams = [f"{s.lower()}@kline_{interval}" for s in self.usdt_symbols]
            # 单连接 streams 超限时再切片
            for i in range(0, len(streams), MAX_STREAMS_PER_CONN):
                shards.append(('usdt', interval, streams[i:i + MAX_STREAMS_PER_CONN]))
        # 币本位: 单连接通常够 (5 symbols * 4 周期 = 20 streams)
        if self.coin_symbols:
            coin_streams = [
                f"{s.lower()}@kline_{i}"
                for s in self.coin_symbols
                for i in self.intervals
            ]
            for i in range(0, len(coin_streams), MAX_STREAMS_PER_CONN):
                shards.append(('coin', 'mixed', coin_streams[i:i + MAX_STREAMS_PER_CONN]))
        return shards

    async def _on_kline_closed(
        self,
        symbol: str,
        interval: str,
        kline: dict,
        market: str,
    ) -> None:
        """WS 回调: 进 buffer"""
        async with self.buffer_lock:
            if len(self.buffer) >= BUFFER_MAX_SIZE:
                logger.warning(f"WS buffer 满 ({BUFFER_MAX_SIZE}), 丢弃最旧一条")
                self.buffer.pop(0)
            self.buffer.append({
                'symbol': symbol,
                'interval': interval,
                'kline': kline,
                'market': market,
            })
            self._stats['total_closed'] += 1

    async def _flusher_loop(self) -> None:
        """每 1s 把 buffer 批量写库 (executor 隔离)"""
        from app.collectors.smart_futures_collector import SmartFuturesCollector
        # 复用 save_klines 落盘逻辑, 但在 executor 里跑避免阻塞 ws loop
        writer = SmartFuturesCollector(self.db_config)
        loop = asyncio.get_event_loop()

        while True:
            await asyncio.sleep(DB_FLUSH_INTERVAL_S)
            async with self.buffer_lock:
                batch = self.buffer[:]
                self.buffer.clear()
            if not batch:
                continue
            try:
                klines_to_save = [self._to_save_format(item) for item in batch]
                inserted = await loop.run_in_executor(
                    None, writer.save_klines, klines_to_save
                )
                self._stats['total_flushed'] += len(klines_to_save)
                logger.debug(f"WS flush: {len(klines_to_save)} 条, 落盘 {inserted}")
            except Exception as e:
                self._stats['flush_errors'] += 1
                logger.error(f"WS 批量写库失败 (buffer 已清, 数据丢失): {e}")

    @staticmethod
    def _to_save_format(item: dict) -> dict:
        """把 WS dict 转成 SmartFuturesCollector.save_klines() 期望的格式"""
        from datetime import datetime
        from decimal import Decimal

        kline = item['kline']
        symbol_raw = item['symbol']  # 如 BTCUSDT or BTCUSD_PERP
        market = item['market']

        if market == 'coin':
            # BTCUSD_PERP -> BTC/USD
            symbol_norm = symbol_raw.replace('USD_PERP', '/USD')
        else:
            # BTCUSDT -> BTC/USDT (跟 SmartFuturesCollector.fetch_kline 一致)
            symbol_norm = f"{symbol_raw[:-4]}/USDT"

        return {
            'symbol': symbol_norm,
            'contract_type': 'coin_futures' if market == 'coin' else 'usdt_futures',
            'timeframe': item['interval'],
            'open_time': kline['open_time'],
            'close_time': kline['close_time'],
            'timestamp': datetime.fromtimestamp(kline['open_time'] / 1000),
            'open_price': Decimal(kline['open']),
            'high_price': Decimal(kline['high']),
            'low_price': Decimal(kline['low']),
            'close_price': Decimal(kline['close']),
            'volume': Decimal(kline['volume']),
            'quote_volume': Decimal(kline['quote_volume']),
            'number_of_trades': kline['trades'],
            'taker_buy_base_volume': Decimal(kline['taker_buy_base']),
            'taker_buy_quote_volume': Decimal(kline['taker_buy_quote']),
        }

    async def _hydrate_history(self) -> None:
        """启动时拉一次历史 K 线 (REST), 让 DB 有历史数据"""
        from app.collectors.smart_futures_collector import SmartFuturesCollector
        hydrator = SmartFuturesCollector(self.db_config)

        # U本位历史
        if self.usdt_symbols:
            for interval in self.intervals:
                limit = 200 if interval in ('5m', '15m') else 50
                logger.info(
                    f"REST hydration: U本位 {interval} x {len(self.usdt_symbols)} symbols (limit={limit})"
                )
                klines = await hydrator.collect_batch(
                    self.usdt_symbols, interval=interval, limit=limit
                )
                if klines:
                    saved = hydrator.save_klines(klines)
                    logger.info(f"  U本位 {interval} hydration 落盘: {saved} 条")

        # 币本位历史
        if self.coin_symbols:
            for interval in self.intervals:
                limit = 200 if interval in ('5m', '15m') else 50
                logger.info(
                    f"REST hydration: 币本位 {interval} x {len(self.coin_symbols)} symbols (limit={limit})"
                )
                klines = await hydrator.collect_coin_batch(
                    self.coin_symbols, interval=interval, limit=limit
                )
                if klines:
                    saved = hydrator.save_klines(klines)
                    logger.info(f"  币本位 {interval} hydration 落盘: {saved} 条")

    async def start(self) -> None:
        """
        启动顺序 (重要, 不要改):
        1. 启动所有 WS 连接 (开始 buffer 数据)
        2. 等 3s 让 WS 全部连上
        3. REST hydration 拉历史 (这段时间 buffer 仍在收新数据)
        4. 启动 batch flusher (drain buffer 落盘)
        5. 启动健康度报告任务
        """
        # 1. 启动 WS 连接
        shards = self._build_shards()
        logger.info(f"准备启动 {len(shards)} 个 WS 连接")

        for idx, (market, interval, streams) in enumerate(shards):
            ws_url = WS_BASE_USDT if market == 'usdt' else WS_BASE_COIN
            name = f"ws_{market}_{interval}_#{idx}"
            conn = WSKlineConnection(
                ws_url=ws_url,
                streams=streams,
                on_kline_closed=self._on_kline_closed,
                market=market,
                name=name,
            )
            self.connections.append(conn)
            asyncio.create_task(conn.run_forever())
            # 错峰建连, 避开 5/s 建连限制
            if idx < len(shards) - 1:
                await asyncio.sleep(2.0 / SUBSCRIBE_RATE_PER_SEC)

        # 2. 等 WS 全部连上
        await asyncio.sleep(3)

        # 3. REST hydration
        try:
            await self._hydrate_history()
        except Exception as e:
            logger.error(f"REST hydration 失败 (继续): {e}")

        # 4. 启动 batch flusher
        asyncio.create_task(self._flusher_loop())

        # 5. 启动健康度报告
        asyncio.create_task(self._health_report_loop())

        logger.info(
            f"WS K线采集已启动: {len(self.connections)} 连接, "
            f"U本位 {len(self.usdt_symbols)} symbols, "
            f"币本位 {len(self.coin_symbols)} symbols, "
            f"intervals={self.intervals}"
        )

    async def _health_report_loop(self) -> None:
        """每 5 分钟打印一次健康度"""
        while True:
            await asyncio.sleep(5 * 60)
            healthy = sum(1 for c in self.connections if c.is_healthy())
            total = len(self.connections)
            buffer_size = len(self.buffer)
            logger.info(
                f"[健康度] 连接 {healthy}/{total} 健康, buffer={buffer_size}, "
                f"closed={self._stats['total_closed']}, "
                f"flushed={self._stats['total_flushed']}, "
                f"flush_errors={self._stats['flush_errors']}"
            )
            # 列出不健康的连接
            for c in self.connections:
                if not c.is_healthy():
                    stale_s = time.time() - c.last_msg_at if c.last_msg_at else -1
                    logger.warning(f"  不健康: {c.name}, last_msg {stale_s:.0f}s 前")
