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

MAX_STREAMS_PER_CONN = 30           # 单连接上限 (2026-05-26: 从 50 降到 30, 减少静默不推概率)
                                    # 5m+15m+1h × 249 symbols / 30 ≈ 25 连接, 在 300/IP 上限内安全
SUBSCRIBE_RATE_PER_SEC = 5          # 币安建连速率限制
PING_INTERVAL = 20                  # 主动 ping 间隔
PING_TIMEOUT = 10                   # ping 超时
RECONNECT_BACKOFF_BASE_S = 5        # 重连基础退避
RECONNECT_BACKOFF_MAX_S = 60        # 重连退避上限
BUFFER_MAX_SIZE = 5000              # WS buffer 上限, 防内存爆炸
DB_FLUSH_INTERVAL_S = 1.0           # batch flush 间隔
HEALTH_STALE_THRESHOLD_S = 120      # 健康检查阈值 (报告用)
WS_RECV_TIMEOUT_S = 30              # 单次 recv 超时 — 30s 无消息视为僵尸 (2026-05-26: 从 90 降到 30)
                                    # 5m kline 每 5min 才一条, 但正常情况下 markPrice 每秒推一条
STALE_FATAL_COUNT = 5               # 连续 N 次僵尸重连仍无数据 → 严重错误日志
WATCHDOG_STALE_CYCLES = 3           # 健康报告连续 N 次全部连接不健康 → 进程退出,触发 systemd 重启
FLUSHER_MAX_RETRIES = 5             # 连续写库失败 N 次后丢弃 buffer 避免无限堆积

# ── REST 回填 ──
BACKFILL_CHECK_INTERVAL_S = 5 * 60   # 每 5 分钟检查 DB 新鲜度
BACKFILL_ALLOWED_INTERVALS = ('5m', '15m', '1h')  # 检查这些周期
BACKFILL_LAG_THRESHOLD_S = {         # 超过此阈值则认为缺失, 触发 REST 回填
    '5m': 8 * 60,   # 8 分钟
    '15m': 20 * 60,  # 20 分钟
    '1h': 65 * 60,   # 65 分钟
}
BACKFILL_LOOKBACK_MINUTES = {        # REST 回填拉多少分钟历史
    '5m': 30,
    '15m': 120,
    '1h': 360,
}


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
        self.last_closed_at: float = 0.0  # 最后收到 closed K 线的时间
        self.connected_at: float = 0.0

    def is_healthy(self) -> bool:
        """超过阈值没收到消息视为不健康.
        
        同时检查 last_msg_at (任何消息) 和 last_closed_at (closed K 线).
        有些僵尸连接 binance 还发着 ping/x:false 更新, last_msg_at 一直在刷新,
        但 closed K 线不进来, DB 不更新. 此时视为不健康.
        """
        if self.last_msg_at == 0:
            return False
        now = time.time()
        msg_ok = (now - self.last_msg_at) < HEALTH_STALE_THRESHOLD_S
        if self.last_closed_at > 0:
            # 单连接 50 streams, 5m/15m 多 symbol 应该每分钟都有 close 事件
            STALE_CLOSED_THRESHOLD_S = 300  # 5 分钟无 closed kline → 数据肯定卡了
            data_ok = (now - self.last_closed_at) < STALE_CLOSED_THRESHOLD_S
            return msg_ok and data_ok
        return msg_ok

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
                    logger.info(f"[{self.name}] WS 已连接, SUBSCRIBE 已发送 ({len(self.streams)} streams, 示例: {self.streams[:3]})")

                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT_S)
                        except asyncio.TimeoutError:
                            consecutive_stale += 1
                            stale_duration = time.time() - self.last_msg_at
                            logger.warning(
                                f"[{self.name}] WS {WS_RECV_TIMEOUT_S}s 无数据 "
                                f"(僵尸连接 #{consecutive_stale}, "
                                f"距上条消息 {stale_duration:.0f}s), 主动断开重连"
                            )
                            if consecutive_stale >= STALE_FATAL_COUNT:
                                logger.error(
                                    f"[{self.name}] 连续 {consecutive_stale} 次僵尸重连仍无数据 — "
                                    f"可能 streams 数量/订阅有问题, 检查 MAX_STREAMS_PER_CONN={MAX_STREAMS_PER_CONN}, "
                                    f"streams 示例: {self.streams[:3]}"
                                )
                            break
                        self.last_msg_at = time.time()
                        consecutive_stale = 0
                        await self._handle_msg(msg)
            except (websockets.ConnectionClosed, OSError) as e:
                logger.warning(f"[{self.name}] WS 断开: {e.__class__.__name__}: {e}, {backoff}s 后重连")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[{self.name}] WS 未知异常: {e.__class__.__name__}: {e}, {backoff}s 后重连")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX_S)

    async def _handle_msg(self, msg: str | bytes) -> None:
        """解析 WS 消息, 只对 k.x == true (closed) 的回调"""
        try:
            data = json.loads(msg)
            # 忽略 SUBSCRIBE 帧的确认消息: {"result": null, "id": N}
            if 'result' in data and 'id' in data:
                return
            # 检测 SUBSCRIBE 错误: {"error": {...}, "id": N}
            if 'error' in data and 'id' in data:
                logger.error(f"[{self.name}] SUBSCRIBE 被拒: {data['error']}")
                return
            # combined stream 格式: {"stream":..., "data":{...}}; 也兼容 raw 格式直接 {...}
            k = data.get('data', {}).get('k') if 'data' in data else data.get('k')
            if not k or not k.get('x'):
                # 不是 closed K 线, 直接丢弃 (99% 消息走这里)
                return
            self.last_closed_at = time.time()
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
        """每 1s 把 buffer 批量写库 (executor 隔离)

        写库失败时不丢数据: 放入 retry_buffer, 下次 flush 优先重试.
        连续重试 FLUSHER_MAX_RETRIES 次仍失败则丢弃, 防止无限堆积.
        """
        from app.collectors.smart_futures_collector import SmartFuturesCollector
        writer = SmartFuturesCollector(self.db_config)
        loop = asyncio.get_event_loop()

        retry_buffer: list[dict] = []
        retry_count = 0

        while True:
            await asyncio.sleep(DB_FLUSH_INTERVAL_S)

            # 从 WS buffer 取新数据
            async with self.buffer_lock:
                batch = self.buffer[:]
                self.buffer.clear()

            # 合并重试 buffer + 新数据
            to_save = retry_buffer + batch
            if not to_save:
                continue

            # 格式转换也在 executor 里做, 不阻塞 event loop
            try:
                klines_to_save = self._to_save_format_batch(to_save)
            except Exception as e:
                logger.error(f"WS 格式转换失败 (丢弃 {len(to_save)} 条): {e}")
                retry_buffer = []
                retry_count = 0
                continue

            try:
                inserted = await loop.run_in_executor(
                    None, writer.save_klines, klines_to_save
                )
                self._stats['total_flushed'] += len(klines_to_save)
                retry_buffer = []
                retry_count = 0
                logger.debug(f"WS flush: {len(klines_to_save)} 条, 落盘 {inserted}")
            except Exception as e:
                self._stats['flush_errors'] += 1
                retry_count += 1
                if retry_count >= FLUSHER_MAX_RETRIES:
                    logger.error(
                        f"WS 连续 {FLUSHER_MAX_RETRIES} 次写库失败, 丢弃 {len(to_save)} 条数据: {e}"
                    )
                    retry_buffer = []
                    retry_count = 0
                else:
                    logger.warning(
                        f"WS 写库失败 (重试 #{retry_count}/{FLUSHER_MAX_RETRIES}, "
                        f"{len(to_save)} 条待重试): {e}"
                    )
                    retry_buffer = to_save

    @staticmethod
    def _to_save_format(item: dict) -> dict:
        """把单个 WS dict 转成 SmartFuturesCollector.save_klines() 期望的格式"""
        from datetime import datetime
        from decimal import Decimal

        kline = item['kline']
        symbol_raw = item['symbol']
        market = item['market']

        if market == 'coin':
            symbol_norm = symbol_raw.replace('USD_PERP', '/USD')
        else:
            symbol_norm = f"{symbol_raw[:-4]}/USDT"

        return {
            'symbol': symbol_norm,
            'contract_type': 'coin_futures' if market == 'coin' else 'usdt_futures',
            'timeframe': item['interval'],
            'open_time': kline['open_time'],
            'close_time': kline['close_time'],
            'timestamp': datetime.utcfromtimestamp(kline['open_time'] / 1000),
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

    def _to_save_format_batch(self, items: list[dict]) -> list[dict]:
        """批量格式转换 — 在 executor 中执行, 不阻塞 event loop"""
        return [self._to_save_format(item) for item in items]

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

        # 6. 启动 REST 回填 (定时检查 + 自动补漏)
        asyncio.create_task(self._backfill_loop())

        logger.info(
            f"WS K线采集已启动: {len(self.connections)} 连接, "
            f"U本位 {len(self.usdt_symbols)} symbols, "
            f"币本位 {len(self.coin_symbols)} symbols, "
            f"intervals={self.intervals}"
        )

    async def _health_report_loop(self) -> None:
        """每 5 分钟打印一次健康度.
        连续 WATCHDOG_STALE_CYCLES 次全部连接不健康 → 进程退出 (触发 systemd 重启).
        """
        consecutive_all_dead = 0
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

            # 看门狗: 全部不健康 → 累计计数 → 达到阈值后退出
            if total > 0 and healthy == 0:
                consecutive_all_dead += 1
                logger.warning(
                    f"[看门狗] 全部 {total} 个连接不健康 (连续 {consecutive_all_dead}"
                    f"/{WATCHDOG_STALE_CYCLES} 次), 等待 systemd 重启..."
                )
                if consecutive_all_dead >= WATCHDOG_STALE_CYCLES:
                    logger.error(
                        f"[看门狗] 全部连接持续不健康 {WATCHDOG_STALE_CYCLES} 次检查, "
                        f"主动退出以触发 systemd 重启"
                    )
                    import os
                    os._exit(1)  # 强制退出, systemd Restart=on-failure 会重启
            else:
                consecutive_all_dead = 0

    async def _check_and_backfill(self, market: str, interval: str) -> None:
        """检查单个市场+周期的 DB 新鲜度, 落后则 REST 回填"""
        try:
            import pymysql
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor, autocommit=True)
            try:
                with conn.cursor() as cur:
                    exchange = 'usdt_futures' if market == 'usdt' else 'coin_futures'
                    cur.execute(
                        "SELECT MAX(open_time) AS ot FROM kline_data "
                        "WHERE timeframe=%s AND exchange=%s",
                        (interval, exchange),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()

            if not row or not row['ot']:
                logger.info(f"[回填] {market} {interval}: DB 无数据, 跳过回填检查")
                return

            latest_ot = row['ot'] / 1000  # ms → s
            age_seconds = time.time() - latest_ot
            threshold = BACKFILL_LAG_THRESHOLD_S.get(interval, 600)

            if age_seconds < threshold:
                return  # 数据够新, 不需要回填

            logger.warning(
                f"[回填] {market} {interval} 数据滞后 {age_seconds/60:.0f} 分钟 "
                f"(阈值 {threshold/60:.0f} 分钟), 触发 REST 回填"
            )

            # 计算需要拉多少历史
            lookback_minutes = BACKFILL_LOOKBACK_MINUTES.get(interval, 60)
            limit = max(2, int(lookback_minutes / self._interval_to_minutes(interval)) + 1)

            symbols = self.usdt_symbols if market == 'usdt' else self.coin_symbols
            if not symbols:
                return

            from app.collectors.smart_futures_collector import SmartFuturesCollector
            hydrator = SmartFuturesCollector(self.db_config)
            loop = asyncio.get_event_loop()

            if market == 'usdt':
                klines = await hydrator.collect_batch(symbols, interval=interval, limit=limit)
            else:
                klines = await hydrator.collect_coin_batch(symbols, interval=interval, limit=limit)

            if klines:
                # save_klines 是同步 DB 操作, 放 executor 避免阻塞 event loop
                saved = await loop.run_in_executor(None, hydrator.save_klines, klines)
                logger.info(f"[回填] {market} {interval}: REST 采集 {len(klines)} 条, 落盘 {saved} 条")
            else:
                logger.warning(f"[回填] {market} {interval}: REST 采集返回空")
        except Exception as e:
            logger.error(f"[回填] {market} {interval} 异常: {e}")

    @staticmethod
    def _interval_to_minutes(interval: str) -> int:
        """'5m'→5, '15m'→15, '1h'→60, '4h'→240, '1d'→1440"""
        if interval.endswith('m'):
            return int(interval[:-1])
        elif interval.endswith('h'):
            return int(interval[:-1]) * 60
        elif interval.endswith('d'):
            return int(interval[:-1]) * 1440
        return 5

    async def _backfill_loop(self) -> None:
        """每 5 分钟检查一次 DB 最新 K 线新鲜度, 有空洞则 REST 回填"""
        logger.info(f"[回填] REST 回填服务已启动 (每 {BACKFILL_CHECK_INTERVAL_S/60:.0f} 分钟检查)")
        await asyncio.sleep(BACKFILL_CHECK_INTERVAL_S)  # 先给 WS 一段时间预热
        while True:
            try:
                markets = ['usdt']
                if self.coin_symbols:
                    markets.append('coin')
                for market in markets:
                    for interval in BACKFILL_ALLOWED_INTERVALS:
                        await self._check_and_backfill(market, interval)
            except Exception as e:
                logger.error(f"[回填] 循环异常: {e}")
            await asyncio.sleep(BACKFILL_CHECK_INTERVAL_S)
