"""
币安数据网关 (BinanceDataHub)

进程内唯一对外打 Binance REST 的入口。所有业务代码必须通过本模块获取
币安行情数据 (价格 / K 线 / 资金费率), 不允许再直接 import requests 或
aiohttp 调用 fapi/api.binance.com。

设计目标:
1. 把"每秒 x N 持仓"的 REST 暴露面收敛成"60 秒 x 全市场 1 次"
2. 统一接入 binance_rate_guard 熔断, ban 中所有 REST 立刻拒绝
3. 内置令牌桶 (200 req/min) 兜底, 防止短时突发打爆限额

数据源优先级 (get_price):
    L1 WS 实时价格池 (binance_ws_price)        ~50ms
    L2 hub 进程内 ticker 缓存 (60s 全市场刷新)  <= 60s
    L3 DB kline_data 5m 最新一根               <= 5min
    L4 同步 REST 单拉 (受令牌桶 + rate_guard)   实时

K 线 (get_klines):
    L1 DB kline_data 命中                     <= 5min
    L2 hub 进程内 kline 缓存                   <= TTL
    L3 同步 REST 单拉 (兜底)

资金费率 / premiumIndex 走 60 秒后台批量拉取。

注意: 本模块设计为 main.py 启动时 init_global_data_hub() 一次, 之后所有
业务代码通过 get_global_data_hub() 拿单例。
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import requests
from loguru import logger

from app.utils.binance_rate_guard import parse_ban_msg, rate_guard


# ---------------------------------------------------------------------------
# 内部工具: 令牌桶限速器
# ---------------------------------------------------------------------------


class _TokenBucket:
    """简单线程安全令牌桶 - 控制 REST 调用速率."""

    def __init__(self, rate_per_sec: float, capacity: int) -> None:
        self.rate = float(rate_per_sec)
        self.capacity = int(capacity)
        self._tokens: float = float(capacity)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def try_acquire(self, n: int = 1) -> bool:
        """尝试取 n 个令牌, 取到返回 True, 否则 False (不阻塞)."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------


class BinanceDataHub:
    """统一币安数据网关 - 进程内唯一 REST 入口."""

    # 全市场批量端点 (1 个请求拿全部 symbol)
    FAPI_TICKER_PRICE_ALL = "https://fapi.binance.com/fapi/v1/ticker/price"
    FAPI_PREMIUM_INDEX_ALL = "https://fapi.binance.com/fapi/v1/premiumIndex"
    SPOT_TICKER_PRICE_ALL = "https://api.binance.com/api/v3/ticker/price"

    # 单拉端点 (兜底)
    FAPI_KLINES = "https://fapi.binance.com/fapi/v1/klines"
    FAPI_TICKER_PRICE = "https://fapi.binance.com/fapi/v1/ticker/price"
    FAPI_PREMIUM_INDEX = "https://fapi.binance.com/fapi/v1/premiumIndex"

    # 全市场刷新周期 (秒) - 60s 是设计目标, 见模块 docstring
    PERIODIC_FETCH_INTERVAL = 60

    # 进程内 K 线缓存 TTL (秒)
    KLINE_CACHE_TTL = {
        "5m": 300,
        "15m": 900,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    # 令牌桶配置: 200 req/min, 突发 20
    RATE_LIMIT_PER_SEC = 200.0 / 60.0
    RATE_BURST = 20

    def __init__(self, db_config: Optional[dict] = None) -> None:
        self.db_config = db_config

        # 延迟拿 WS 单例, 避免循环 import
        self._ws_futures = None

        # 进程内缓存
        # ticker_cache: symbol_clean -> {price: Decimal, ts: float, source: str}
        self._ticker_cache: Dict[str, Dict[str, Any]] = {}
        # premium_index_cache: symbol_clean -> {mark_price, ts}
        self._premium_cache: Dict[str, Dict[str, Any]] = {}
        # kline_cache: (symbol, interval, limit) -> {rows: list, ts: float}
        self._kline_cache: Dict[Tuple[str, str, int], Dict[str, Any]] = {}

        self._cache_lock = threading.Lock()
        self._rate_limiter = _TokenBucket(self.RATE_LIMIT_PER_SEC, self.RATE_BURST)

        # 异步后台任务
        self._fetch_task: Optional[asyncio.Task] = None
        self._stop = False

        # 异步 session 复用 (用于业务侧 async get_price 路径)
        self._aiohttp_session: Optional[aiohttp.ClientSession] = None

        # 同步 session 复用 (用于业务侧 sync 路径)
        self._sync_session: Optional[requests.Session] = None
        self._sync_session_lock = threading.Lock()

        # 统计
        self._stat_rest_calls = 0
        self._stat_rest_rejected_by_ban = 0
        self._stat_rest_rejected_by_bucket = 0

        logger.info(
            f"[DataHub] 初始化完成 (fetch_interval={self.PERIODIC_FETCH_INTERVAL}s, "
            f"rate_limit={self.RATE_LIMIT_PER_SEC * 60:.0f}req/min)"
        )

    # -------------------------------------------------------------------
    # 启停
    # -------------------------------------------------------------------

    async def start(self) -> None:
        """启动后台 60s 拉取任务."""
        if self._fetch_task and not self._fetch_task.done():
            logger.warning("[DataHub] 后台任务已在运行")
            return
        self._stop = False
        self._fetch_task = asyncio.create_task(self._periodic_fetch_loop())
        logger.info("[DataHub] 后台拉取任务已启动")

    async def stop(self) -> None:
        self._stop = True
        if self._fetch_task:
            self._fetch_task.cancel()
            try:
                await self._fetch_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()
        if self._sync_session is not None:
            try:
                self._sync_session.close()
            except Exception as e:
                logger.warning(f"[DataHub] sync session close 异常: {e}")
        logger.info("[DataHub] 已停止")

    # -------------------------------------------------------------------
    # WS 单例 (lazy)
    # -------------------------------------------------------------------

    def _get_ws_futures(self):
        if self._ws_futures is None:
            try:
                from app.services.binance_ws_price import get_ws_price_service
                self._ws_futures = get_ws_price_service("futures")
            except Exception as e:
                logger.warning(f"[DataHub] 获取 futures WS 服务失败: {e}")
                return None
        return self._ws_futures

    # -------------------------------------------------------------------
    # 业务侧公开接口: 价格
    # -------------------------------------------------------------------

    async def get_price(
        self,
        symbol: str,
        max_age_seconds: int = 90,
        allow_rest_fallback: bool = True,
    ) -> Optional[Decimal]:
        """
        获取实时价格 (异步).

        Args:
            symbol:               BTC/USDT 或 BTC/USD 写法
            max_age_seconds:      L1/L2 缓存最大年龄, 超过则触发 L3/L4 兜底
            allow_rest_fallback:  False 时只走 WS+缓存+DB, 完全不打 REST

        Returns:
            Decimal 价格, 全部失败返回 None
        """
        is_coin = symbol.endswith("/USD") and not symbol.endswith("/USDT")
        if is_coin:
            return None

        symbol_clean = symbol.replace("/", "").upper()

        ws = self._get_ws_futures()
        if ws is not None:
            try:
                p = ws.get_price(symbol, max_age_seconds=max_age_seconds)
                if p is not None and p > 0:
                    return Decimal(str(p))
            except Exception as e:
                logger.debug(f"[DataHub] {symbol} WS 取价异常: {e}")

        # L2: hub 进程内 ticker 缓存
        cached = self._cache_get_ticker(symbol_clean, max_age_seconds)
        if cached is not None:
            return cached

        # L3: DB 5m K 线兜底
        db_price = self._db_kline_fallback(symbol)
        if db_price is not None:
            return db_price

        if not allow_rest_fallback:
            return None
        return await self._rest_single_price(symbol, symbol_clean)

    def get_price_sync(
        self,
        symbol: str,
        max_age_seconds: int = 90,
        allow_rest_fallback: bool = True,
    ) -> Optional[Decimal]:
        """
        同步版 get_price - 给非协程上下文用.

        逻辑与 get_price 一致, 但 L4 使用 requests 同步调用.
        """
        is_coin = symbol.endswith("/USD") and not symbol.endswith("/USDT")
        if is_coin:
            return None

        symbol_clean = symbol.replace("/", "").upper()

        ws = self._get_ws_futures()
        if ws is not None:
            try:
                p = ws.get_price(symbol, max_age_seconds=max_age_seconds)
                if p is not None and p > 0:
                    return Decimal(str(p))
            except Exception as e:
                logger.debug(f"[DataHub] {symbol} WS 取价异常: {e}")

        cached = self._cache_get_ticker(symbol_clean, max_age_seconds)
        if cached is not None:
            return cached

        db_price = self._db_kline_fallback(symbol)
        if db_price is not None:
            return db_price

        if not allow_rest_fallback:
            return None
        return self._rest_single_price_sync(symbol, symbol_clean)

    def get_trade_price_sync(
        self,
        symbol: str,
        max_age_seconds: int = 90,
        allow_rest_fallback: bool = True,
    ) -> Optional[Decimal]:
        """
        模拟开仓/平仓用价 — 优先 U 本位 mark（WS / premiumIndex），再 last / 5m 收盘。
        """
        is_coin = symbol.endswith("/USD") and not symbol.endswith("/USDT")
        if is_coin:
            return None

        symbol_clean = symbol.replace("/", "").upper()

        ws = self._get_ws_futures()
        if ws is not None:
            try:
                p = ws.get_price(symbol, max_age_seconds=max_age_seconds)
                if p is not None and p > 0:
                    return Decimal(str(p))
            except Exception as e:
                logger.debug(f"[DataHub] {symbol} WS mark 取价异常: {e}")

        mark_cached = self._cache_get_mark_price(symbol_clean, max_age_seconds)
        if mark_cached is not None and mark_cached > 0:
            return mark_cached

        cached = self._cache_get_ticker(symbol_clean, max_age_seconds)
        if cached is not None:
            return cached

        db_price = self._db_kline_fallback(symbol)
        if db_price is not None:
            return db_price

        if not allow_rest_fallback:
            return None

        mark_rest = self._rest_mark_price_sync(symbol, symbol_clean)
        if mark_rest is not None:
            return mark_rest
        return self._rest_single_price_sync(symbol, symbol_clean)

    async def get_prices_batch(
        self, symbols: List[str], max_age_seconds: int = 90
    ) -> Dict[str, Decimal]:
        """
        批量价格 - 用于批量更新场景.

        全部命中缓存 = 0 REST. 缓存 miss 也不发单拉 REST, 由 60s 后台任务负责.

        Returns:
            {symbol: Decimal} 仅包含成功取到价格的
        """
        out: Dict[str, Decimal] = {}
        for sym in symbols:
            p = await self.get_price(sym, max_age_seconds=max_age_seconds, allow_rest_fallback=False)
            if p is not None:
                out[sym] = p
        return out

    def get_full_ticker_map(self, market: str = "futures") -> Dict[str, Decimal]:
        """
        返回整个 ticker 缓存的快照 (symbol_clean -> price), 用于批量场景.

        Args:
            market: 'futures' (U本位) / 'spot'

        Note:
            缓存数据来自 60s 后台拉取, 不保证非常新鲜, 不主动触发 REST.
        """
        with self._cache_lock:
            if market == "futures":
                return {k: v["price"] for k, v in self._ticker_cache.items() if v.get("source") == "fapi"}
            else:
                return {k: v["price"] for k, v in self._ticker_cache.items() if v.get("source") == "spot"}

    def get_premium_index_map(self, market: str = "futures") -> Dict[str, Decimal]:
        """返回 premiumIndex 缓存的 markPrice 映射."""
        with self._cache_lock:
            if market == "futures":
                return {k: v["mark_price"] for k, v in self._premium_cache.items() if v.get("source") == "fapi"}
            return {}

    # -------------------------------------------------------------------
    # 业务侧公开接口: K 线
    # -------------------------------------------------------------------

    async def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 1,
        allow_rest_fallback: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        获取 K 线.

        优先级:
            L1 DB kline_data (smart_collector 已每 5min 写)
            L2 hub 进程内 kline 缓存
            L3 同步 REST 单拉 (兜底)

        Returns:
            List[{open, high, low, close, open_time(datetime), close_time(datetime), volume}]
            为空表示全部失败.
        """
        is_coin = symbol.endswith("/USD") and not symbol.endswith("/USDT")
        if is_coin:
            return []

        symbol_clean = symbol.replace("/", "").upper()

        # L1: DB
        db_rows = self._db_klines_fallback(symbol, interval, limit)
        if db_rows:
            return db_rows

        # L2: 进程内缓存
        key = (symbol_clean, interval, limit)
        ttl = self.KLINE_CACHE_TTL.get(interval, 300)
        with self._cache_lock:
            entry = self._kline_cache.get(key)
            if entry and (time.time() - entry["ts"]) < ttl:
                return entry["rows"]

        # L3: REST 单拉
        if not allow_rest_fallback:
            return []
        rows = await self._rest_klines(symbol, symbol_clean, interval, limit)
        if rows:
            with self._cache_lock:
                self._kline_cache[key] = {"rows": rows, "ts": time.time()}
        return rows

    # -------------------------------------------------------------------
    # 通用 REST 入口 - 给 collector / news monitor 等杂项请求复用
    # -------------------------------------------------------------------

    async def fapi_request_get(
        self, path: str, params: Optional[dict] = None, timeout: float = 8.0
    ) -> Optional[Any]:
        """
        通用 fapi GET 请求 (异步), 受 rate_guard 熔断 + 令牌桶限速.

        path 必须以 '/' 开头, 例如 '/fapi/v1/openInterest'.
        Returns: 解析后的 json 对象 (dict/list), 失败返回 None.
        """
        return await self._rest_get_generic(
            "https://fapi.binance.com" + path, params, timeout, src="fapi"
        )

    async def spot_request_get(
        self, path: str, params: Optional[dict] = None, timeout: float = 8.0
    ) -> Optional[Any]:
        """通用现货 GET 请求 (异步), path 例如 '/api/v3/ticker/price'."""
        return await self._rest_get_generic(
            "https://api.binance.com" + path, params, timeout, src="spot"
        )

    def fapi_request_get_sync(
        self, path: str, params: Optional[dict] = None, timeout: float = 8.0
    ) -> Optional[Any]:
        """同步版 fapi GET. 给非协程上下文使用 (例如 schedule 库的 sync 任务)."""
        return self._rest_get_generic_sync(
            "https://fapi.binance.com" + path, params, timeout, src="fapi"
        )

    def spot_request_get_sync(
        self, path: str, params: Optional[dict] = None, timeout: float = 8.0
    ) -> Optional[Any]:
        """同步版现货 GET, 例如 path='/api/v3/exchangeInfo'."""
        return self._rest_get_generic_sync(
            "https://api.binance.com" + path, params, timeout, src="spot"
        )

    async def _rest_get_generic(
        self, url: str, params: Optional[dict], timeout: float, src: str
    ) -> Optional[Any]:
        if rate_guard.is_banned():
            self._stat_rest_rejected_by_ban += 1
            logger.debug(f"[DataHub] {url} 被熔断拒绝")
            return None
        if not self._rate_limiter.try_acquire(1):
            self._stat_rest_rejected_by_bucket += 1
            logger.debug(f"[DataHub] {url} 被令牌桶拒绝")
            return None

        session = await self._get_aiohttp_session()
        try:
            self._stat_rest_calls += 1
            async with session.get(
                url, params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    self._maybe_record_ban(resp.status, text, src=f"hub:generic:{src}")
                    return None
                import json
                return json.loads(text)
        except Exception as e:
            logger.warning(f"[DataHub] {url} 请求异常: {type(e).__name__}: {e}")
            return None

    def _rest_get_generic_sync(
        self, url: str, params: Optional[dict], timeout: float, src: str
    ) -> Optional[Any]:
        if rate_guard.is_banned():
            self._stat_rest_rejected_by_ban += 1
            return None
        if not self._rate_limiter.try_acquire(1):
            self._stat_rest_rejected_by_bucket += 1
            return None
        sess = self._get_sync_session()
        try:
            self._stat_rest_calls += 1
            r = sess.get(url, params=params, timeout=timeout)
            if r.status_code != 200:
                self._maybe_record_ban(r.status_code, r.text, src=f"hub:generic:sync:{src}")
                return None
            return r.json()
        except Exception as e:
            logger.warning(f"[DataHub] {url} 同步请求异常: {type(e).__name__}: {e}")
            return None

    # -------------------------------------------------------------------
    # 业务侧公开接口: 资金费率 / premiumIndex
    # -------------------------------------------------------------------

    def get_funding_rate_sync(self, symbol: str) -> Optional[Decimal]:
        """
        获取资金费率 (走 premium_index 缓存).

        仅读缓存, 不触发 REST. 60s 后台任务会刷新 premiumIndex 全市场.
        """
        symbol_clean = symbol.replace("/", "").upper()
        with self._cache_lock:
            entry = self._premium_cache.get(symbol_clean)
            if entry:
                return entry.get("funding_rate")
        return None

    # -------------------------------------------------------------------
    # 后台拉取任务
    # -------------------------------------------------------------------

    async def _periodic_fetch_loop(self) -> None:
        """每 60 秒拉一次全市场 ticker + premiumIndex."""
        await asyncio.sleep(2)  # 启动后给 WS 一点时间
        while not self._stop:
            try:
                if rate_guard.is_banned():
                    remaining = rate_guard.seconds_until_unban()
                    logger.warning(f"[DataHub] IP 仍在熔断中, 跳过本轮拉取 (剩余 {remaining:.0f}s)")
                else:
                    await self._fetch_all_tickers_fapi()
                    await self._fetch_all_premium_index_fapi()
                    self._log_stats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[DataHub] 后台拉取异常: {type(e).__name__}: {e}")
            try:
                await asyncio.sleep(self.PERIODIC_FETCH_INTERVAL)
            except asyncio.CancelledError:
                break

    async def _fetch_all_tickers_fapi(self) -> None:
        """拉 U 本位全市场价格 (1 个 REST 取所有 symbol)."""
        await self._fetch_all_tickers_generic(self.FAPI_TICKER_PRICE_ALL, source="fapi")

    async def _fetch_all_tickers_generic(self, url: str, source: str) -> None:
        if not self._rate_limiter.try_acquire(1):
            self._stat_rest_rejected_by_bucket += 1
            logger.debug(f"[DataHub] 令牌桶拒绝: {url}")
            return
        if rate_guard.is_banned():
            self._stat_rest_rejected_by_ban += 1
            return

        session = await self._get_aiohttp_session()
        try:
            self._stat_rest_calls += 1
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    self._maybe_record_ban(resp.status, text, src=f"hub:{source}")
                    logger.warning(f"[DataHub] {url} HTTP {resp.status}: {text[:200]}")
                    return
                import json
                data = json.loads(text)
        except Exception as e:
            logger.warning(f"[DataHub] {url} 请求异常: {type(e).__name__}: {e}")
            return

        if not isinstance(data, list):
            logger.warning(f"[DataHub] {url} 返回非列表: {str(data)[:200]}")
            return

        now = time.time()
        added = 0
        with self._cache_lock:
            for item in data:
                if not isinstance(item, dict):
                    continue
                sym = item.get("symbol")
                price = item.get("price")
                if not sym or price is None:
                    continue
                try:
                    dec = Decimal(str(price))
                    if dec <= 0:
                        continue
                except Exception:
                    continue
                self._ticker_cache[sym] = {"price": dec, "ts": now, "source": source}
                added += 1
        logger.debug(f"[DataHub] {source} ticker 缓存刷新 {added} 个 symbol")

    async def _fetch_all_premium_index_fapi(self) -> None:
        await self._fetch_all_premium_index_generic(self.FAPI_PREMIUM_INDEX_ALL, source="fapi")

    async def _fetch_all_premium_index_generic(self, url: str, source: str) -> None:
        if not self._rate_limiter.try_acquire(1):
            self._stat_rest_rejected_by_bucket += 1
            return
        if rate_guard.is_banned():
            self._stat_rest_rejected_by_ban += 1
            return

        session = await self._get_aiohttp_session()
        try:
            self._stat_rest_calls += 1
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                text = await resp.text()
                if resp.status != 200:
                    self._maybe_record_ban(resp.status, text, src=f"hub:premium:{source}")
                    logger.warning(f"[DataHub] {url} HTTP {resp.status}: {text[:200]}")
                    return
                import json
                data = json.loads(text)
        except Exception as e:
            logger.warning(f"[DataHub] {url} 请求异常: {type(e).__name__}: {e}")
            return

        if not isinstance(data, list):
            return

        now = time.time()
        with self._cache_lock:
            for item in data:
                if not isinstance(item, dict):
                    continue
                sym = item.get("symbol")
                mp = item.get("markPrice")
                fr = item.get("lastFundingRate")
                if not sym or mp is None:
                    continue
                try:
                    mp_dec = Decimal(str(mp))
                    fr_dec = Decimal(str(fr)) if fr is not None else None
                except Exception:
                    continue
                self._premium_cache[sym] = {
                    "mark_price": mp_dec,
                    "funding_rate": fr_dec,
                    "ts": now,
                    "source": source,
                }

    # -------------------------------------------------------------------
    # L3/L4 兜底实现
    # -------------------------------------------------------------------

    def _cache_get_ticker(self, symbol_clean: str, max_age_seconds: int) -> Optional[Decimal]:
        with self._cache_lock:
            entry = self._ticker_cache.get(symbol_clean)
            if not entry:
                return None
            if entry.get("source") != "fapi":
                return None
            if (time.time() - entry["ts"]) > max_age_seconds:
                return None
            return entry["price"]

    def _cache_get_mark_price(self, symbol_clean: str, max_age_seconds: int) -> Optional[Decimal]:
        with self._cache_lock:
            entry = self._premium_cache.get(symbol_clean)
            if not entry or entry.get("source") != "fapi":
                return None
            if (time.time() - entry["ts"]) > max_age_seconds:
                return None
            return entry.get("mark_price")

    def _db_kline_fallback(self, symbol: str, max_age_minutes: int = 15) -> Optional[Decimal]:
        """从 kline_data 读最近一根 K 线收盘价（兼容 BASEUSDT / BASE/USDT）。"""
        if not self.db_config:
            return None
        try:
            from app.utils.futures_symbol import futures_symbol_kline_keys

            import pymysql
            conn = pymysql.connect(
                **self.db_config,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
                connect_timeout=5,
            )
            try:
                with conn.cursor() as cur:
                    for sym_key in futures_symbol_kline_keys(symbol):
                        for tf in ("1m", "5m"):
                            cur.execute(
                                "SELECT close_price, open_time FROM kline_data "
                                "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
                                "ORDER BY open_time DESC LIMIT 1",
                                (sym_key, tf),
                            )
                            row = cur.fetchone()
                            if not row or not row.get("close_price"):
                                continue
                            age_min = (
                                datetime.now().timestamp() - row["open_time"] / 1000
                            ) / 60
                            if age_min <= max_age_minutes:
                                return Decimal(str(row["close_price"]))
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"[DataHub] {symbol} DB kline 兜底失败: {e}")
        return None

    def _db_klines_fallback(self, symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
        """从 kline_data 表批量读 K 线."""
        if not self.db_config:
            return []
        try:
            import pymysql
            conn = pymysql.connect(
                **self.db_config,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True,
                connect_timeout=5,
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT open_price, high_price, low_price, close_price, "
                        "       volume, open_time "
                        "FROM kline_data "
                        "WHERE symbol=%s AND timeframe=%s "
                        "ORDER BY open_time DESC LIMIT %s",
                        (symbol, interval, limit),
                    )
                    rows = cur.fetchall() or []
            finally:
                conn.close()
            if not rows:
                return []
            # DB 是按 open_time DESC, 调用方习惯按时间升序
            rows = list(reversed(rows))
            out: List[Dict[str, Any]] = []
            for r in rows:
                ot_ms = r["open_time"]
                # 估算 close_time: open_time + interval 长度
                interval_ms = self._interval_to_ms(interval)
                ct_ms = ot_ms + interval_ms - 1
                out.append({
                    "open": float(r["open_price"]),
                    "high": float(r["high_price"]),
                    "low": float(r["low_price"]),
                    "close": float(r["close_price"]),
                    "volume": float(r.get("volume") or 0),
                    "open_time": datetime.utcfromtimestamp(ot_ms / 1000),
                    "close_time": datetime.utcfromtimestamp(ct_ms / 1000),
                })
            # 检查最后一根新鲜度: 5m K 线超过 15 分钟视为不可信, 让上层走 REST
            last_ms = rows[-1]["open_time"]
            age_min = (datetime.now().timestamp() - last_ms / 1000) / 60
            stale_limit_min = self._stale_limit_minutes(interval)
            if age_min > stale_limit_min:
                logger.debug(
                    f"[DataHub] DB {interval} K 线滞后 {age_min:.1f}min "
                    f"> {stale_limit_min}min, 让上层走 REST"
                )
                return []
            return out
        except Exception as e:
            logger.debug(f"[DataHub] {symbol} DB klines 兜底失败: {e}")
            return []

    @staticmethod
    def _interval_to_ms(interval: str) -> int:
        m = {
            "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
            "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
            "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000,
            "1d": 86_400_000, "3d": 259_200_000, "1w": 604_800_000,
        }
        return m.get(interval, 300_000)

    @staticmethod
    def _stale_limit_minutes(interval: str) -> float:
        """每种周期 K 线允许的最大滞后 (分钟)."""
        # 一般是 3 倍周期, 超过就认为采集器掉线, 让上层走 REST
        m = {"5m": 15, "15m": 45, "1h": 180, "4h": 720, "1d": 4320}
        return m.get(interval, 30)

    async def _rest_single_price(
        self, symbol: str, symbol_clean: str
    ) -> Optional[Decimal]:
        """REST 单 symbol 拉价 (异步, 受熔断 + 令牌桶)."""
        if rate_guard.is_banned():
            self._stat_rest_rejected_by_ban += 1
            logger.debug(f"[DataHub] {symbol} REST 单拉被熔断拒绝")
            return None
        if not self._rate_limiter.try_acquire(1):
            self._stat_rest_rejected_by_bucket += 1
            logger.debug(f"[DataHub] {symbol} REST 单拉被令牌桶拒绝")
            return None

        url = self.FAPI_TICKER_PRICE
        api_sym = symbol_clean

        session = await self._get_aiohttp_session()
        try:
            self._stat_rest_calls += 1
            async with session.get(
                url, params={"symbol": api_sym},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    self._maybe_record_ban(resp.status, text, src="hub:single")
                    return None
                import json
                data = json.loads(text)
        except Exception as e:
            logger.warning(f"[DataHub] {symbol} REST 单拉异常: {type(e).__name__}: {e}")
            return None

        target = data[0] if isinstance(data, list) and data else data
        if not isinstance(target, dict) or "price" not in target:
            return None
        try:
            dec = Decimal(str(target["price"]))
            if dec > 0:
                with self._cache_lock:
                    self._ticker_cache[symbol_clean] = {
                        "price": dec, "ts": time.time(),
                        "source": "fapi",
                    }
                return dec
        except Exception:
            return None
        return None

    def _rest_mark_price_sync(
        self, symbol: str, symbol_clean: str
    ) -> Optional[Decimal]:
        """REST 单 symbol 拉 markPrice (premiumIndex)."""
        if rate_guard.is_banned():
            self._stat_rest_rejected_by_ban += 1
            return None
        if not self._rate_limiter.try_acquire(1):
            self._stat_rest_rejected_by_bucket += 1
            return None

        sess = self._get_sync_session()
        try:
            self._stat_rest_calls += 1
            r = sess.get(
                self.FAPI_PREMIUM_INDEX,
                params={"symbol": symbol_clean},
                timeout=3,
            )
            if r.status_code != 200:
                self._maybe_record_ban(r.status_code, r.text, src="hub:premium:single")
                return None
            data = r.json()
        except Exception as e:
            logger.warning(f"[DataHub] {symbol} premiumIndex 同步拉取异常: {e}")
            return None

        if not isinstance(data, dict) or data.get("markPrice") is None:
            return None
        try:
            dec = Decimal(str(data["markPrice"]))
            if dec > 0:
                now = time.time()
                with self._cache_lock:
                    self._premium_cache[symbol_clean] = {
                        "mark_price": dec,
                        "funding_rate": None,
                        "ts": now,
                        "source": "fapi",
                    }
                return dec
        except Exception:
            return None
        return None

    def _rest_single_price_sync(
        self, symbol: str, symbol_clean: str
    ) -> Optional[Decimal]:
        """REST 单 symbol 拉价 (同步版)."""
        if rate_guard.is_banned():
            self._stat_rest_rejected_by_ban += 1
            return None
        if not self._rate_limiter.try_acquire(1):
            self._stat_rest_rejected_by_bucket += 1
            return None

        url = self.FAPI_TICKER_PRICE
        api_sym = symbol_clean

        sess = self._get_sync_session()
        try:
            self._stat_rest_calls += 1
            r = sess.get(url, params={"symbol": api_sym}, timeout=3)
            if r.status_code != 200:
                self._maybe_record_ban(r.status_code, r.text, src="hub:single:sync")
                return None
            data = r.json()
        except Exception as e:
            logger.warning(f"[DataHub] {symbol} 同步 REST 单拉异常: {type(e).__name__}: {e}")
            return None

        target = data[0] if isinstance(data, list) and data else data
        if not isinstance(target, dict) or "price" not in target:
            return None
        try:
            dec = Decimal(str(target["price"]))
            if dec > 0:
                with self._cache_lock:
                    self._ticker_cache[symbol_clean] = {
                        "price": dec, "ts": time.time(),
                        "source": "fapi",
                    }
                return dec
        except Exception:
            return None
        return None

    async def _rest_klines(
        self, symbol: str, symbol_clean: str, interval: str, limit: int
    ) -> List[Dict[str, Any]]:
        if rate_guard.is_banned():
            self._stat_rest_rejected_by_ban += 1
            return []
        if not self._rate_limiter.try_acquire(1):
            self._stat_rest_rejected_by_bucket += 1
            return []

        url = self.FAPI_KLINES
        api_sym = symbol_clean

        session = await self._get_aiohttp_session()
        try:
            self._stat_rest_calls += 1
            async with session.get(
                url, params={"symbol": api_sym, "interval": interval, "limit": limit},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    self._maybe_record_ban(resp.status, text, src="hub:klines")
                    return []
                import json
                data = json.loads(text)
        except Exception as e:
            logger.warning(f"[DataHub] {symbol} REST K 线异常: {type(e).__name__}: {e}")
            return []

        if not isinstance(data, list) or not data:
            return []

        out: List[Dict[str, Any]] = []
        for k in data:
            try:
                out.append({
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]) if len(k) > 5 else 0.0,
                    "open_time": datetime.utcfromtimestamp(k[0] / 1000),
                    "close_time": datetime.utcfromtimestamp(k[6] / 1000),
                })
            except (IndexError, ValueError, TypeError) as e:
                logger.debug(f"[DataHub] K 线行解析失败: {e}")
                continue
        return out

    # -------------------------------------------------------------------
    # Session / ban 处理
    # -------------------------------------------------------------------

    async def _get_aiohttp_session(self) -> aiohttp.ClientSession:
        if self._aiohttp_session is None or self._aiohttp_session.closed:
            connector = aiohttp.TCPConnector(limit=20, limit_per_host=10)
            self._aiohttp_session = aiohttp.ClientSession(connector=connector)
        return self._aiohttp_session

    def _get_sync_session(self) -> requests.Session:
        with self._sync_session_lock:
            if self._sync_session is None:
                self._sync_session = requests.Session()
            return self._sync_session

    def _maybe_record_ban(self, status_code: int, body: str, src: str) -> None:
        """收到 4xx 时尝试从消息里解析 banned until 并写熔断."""
        if status_code not in (418, 429, 403):
            return
        until_ms = parse_ban_msg(body or "")
        if until_ms:
            if rate_guard.set_banned_until(until_ms, source=src):
                logger.error(
                    f"[DataHub] 触发币安 IP 熔断 (src={src}, "
                    f"banned_until_ms={until_ms}, http={status_code})"
                )

    def _log_stats(self) -> None:
        if self._stat_rest_calls == 0 and self._stat_rest_rejected_by_ban == 0:
            return
        logger.info(
            f"[DataHub] stats: rest_calls={self._stat_rest_calls}, "
            f"rejected_by_ban={self._stat_rest_rejected_by_ban}, "
            f"rejected_by_bucket={self._stat_rest_rejected_by_bucket}, "
            f"ticker_cache={len(self._ticker_cache)}, "
            f"kline_cache={len(self._kline_cache)}"
        )


# ---------------------------------------------------------------------------
# HubHttpProxy - 跨进程 hub 访问代理
# ---------------------------------------------------------------------------
#
# 设计动机:
#   BinanceDataHub 是进程内单例, 但项目有多个独立 Python 进程
#   (app/main.py / smart_trader_service / 等).
#   如果每个进程各 init 一个 hub, 会出现:
#     - 多个 hub 并发打币安 (违背 "单源" 原则)
#     - token bucket 跨进程无法共享
#     - 60s 全市场拉取被重复 N 次, 浪费配额
#
# 解决方案:
#   只有 app/main.py 进程 init 真正的 BinanceDataHub.
#   其他进程调用 get_global_data_hub() 自动返回 HubHttpProxy 实例,
#   接口与 BinanceDataHub 一致 (duck typing), 内部通过 HTTP 跳到 main 进程
#   暴露的 /api/datahub/* 端点拿数据. 业务代码完全无感知.
#
# 失败策略:
#   HTTP 请求异常 / 5xx / hub 未初始化 -> 返回 None (业务侧已有 DB 兜底).
#
# 性能:
#   一次 HTTP 跳转 (localhost:9020) 约 1-5ms, 比直接打币安 (50-200ms) 快.
#   高频热点路径建议优先 WS, REST 路径走代理可接受.

class HubHttpProxy:
    """跨进程访问 main.py 进程的 hub. 接口与 BinanceDataHub 鸭子类型一致."""

    DEFAULT_API_BASE = "http://localhost:9020"
    SYNC_TIMEOUT = 3.0
    ASYNC_TIMEOUT = 5.0

    def __init__(self, api_base: Optional[str] = None) -> None:
        self.api_base = (api_base or self.DEFAULT_API_BASE).rstrip("/")
        self._aio_session: Optional[aiohttp.ClientSession] = None
        self._sync_session: Optional[requests.Session] = None

    async def _get_aio(self) -> aiohttp.ClientSession:
        if self._aio_session is None or self._aio_session.closed:
            self._aio_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=10)
            )
        return self._aio_session

    def _get_sync(self) -> requests.Session:
        if self._sync_session is None:
            self._sync_session = requests.Session()
        return self._sync_session

    async def _aget(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        sess = await self._get_aio()
        try:
            async with sess.get(
                self.api_base + path, params=params,
                timeout=aiohttp.ClientTimeout(total=self.ASYNC_TIMEOUT),
            ) as r:
                if r.status != 200:
                    return None
                return await r.json()
        except Exception as e:
            logger.debug(f"[HubProxy] aget {path} 异常: {type(e).__name__}: {e}")
            return None

    async def _apost(self, path: str, body: dict) -> Optional[dict]:
        sess = await self._get_aio()
        try:
            async with sess.post(
                self.api_base + path, json=body,
                timeout=aiohttp.ClientTimeout(total=self.ASYNC_TIMEOUT),
            ) as r:
                if r.status != 200:
                    return None
                return await r.json()
        except Exception as e:
            logger.debug(f"[HubProxy] apost {path} 异常: {type(e).__name__}: {e}")
            return None

    def _sget(self, path: str, params: Optional[dict] = None) -> Optional[dict]:
        sess = self._get_sync()
        try:
            r = sess.get(self.api_base + path, params=params, timeout=self.SYNC_TIMEOUT)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:
            logger.debug(f"[HubProxy] sget {path} 异常: {type(e).__name__}: {e}")
            return None

    def _spost(self, path: str, body: dict) -> Optional[dict]:
        sess = self._get_sync()
        try:
            r = sess.post(self.api_base + path, json=body, timeout=self.SYNC_TIMEOUT)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception as e:
            logger.debug(f"[HubProxy] spost {path} 异常: {type(e).__name__}: {e}")
            return None

    # ── 接口 (与 BinanceDataHub 同名) ────────────────────────────

    async def get_price(
        self, symbol: str, max_age_seconds: int = 90, allow_rest_fallback: bool = True
    ) -> Optional[Decimal]:
        sym = symbol.replace("/", "%2F")
        data = await self._aget(
            f"/api/datahub/price/{sym}",
            {"max_age_seconds": max_age_seconds, "allow_rest": "true" if allow_rest_fallback else "false"},
        )
        if not data:
            return None
        p = data.get("price")
        return Decimal(p) if p else None

    def get_price_sync(
        self, symbol: str, max_age_seconds: int = 90, allow_rest_fallback: bool = True
    ) -> Optional[Decimal]:
        sym = symbol.replace("/", "%2F")
        data = self._sget(
            f"/api/datahub/price/{sym}",
            {"max_age_seconds": max_age_seconds, "allow_rest": "true" if allow_rest_fallback else "false"},
        )
        if not data:
            return None
        p = data.get("price")
        return Decimal(p) if p else None

    async def get_prices_batch(
        self, symbols: List[str], max_age_seconds: int = 90
    ) -> Dict[str, Decimal]:
        data = await self._apost(
            "/api/datahub/prices/batch",
            {"symbols": symbols, "max_age_seconds": max_age_seconds},
        )
        if not data:
            return {}
        out: Dict[str, Decimal] = {}
        for k, v in (data.get("prices") or {}).items():
            try:
                out[k] = Decimal(v)
            except Exception:
                continue
        return out

    async def get_klines(
        self,
        symbol: str,
        interval: str = "5m",
        limit: int = 1,
        allow_rest_fallback: bool = True,
    ) -> List[Dict[str, Any]]:
        sym = symbol.replace("/", "%2F")
        data = await self._aget(
            f"/api/datahub/klines/{sym}",
            {"interval": interval, "limit": limit,
             "allow_rest": "true" if allow_rest_fallback else "false"},
        )
        if not data:
            return []
        rows = data.get("klines") or []
        out: List[Dict[str, Any]] = []
        for r in rows:
            try:
                out.append({
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r.get("volume", 0)),
                    "open_time": datetime.fromisoformat(r["open_time"]) if r.get("open_time") else None,
                    "close_time": datetime.fromisoformat(r["close_time"]) if r.get("close_time") else None,
                })
            except Exception as e:
                logger.debug(f"[HubProxy] K 线行解析失败: {e}")
                continue
        return out

    def get_full_ticker_map(self, market: str = "futures") -> Dict[str, Decimal]:
        data = self._sget("/api/datahub/ticker_map", {"market": market})
        if not data:
            return {}
        return {k: Decimal(v) for k, v in (data.get("prices") or {}).items()}

    def get_premium_index_map(self, market: str = "futures") -> Dict[str, Decimal]:
        data = self._sget("/api/datahub/premium_map", {"market": market})
        if not data:
            return {}
        return {k: Decimal(v) for k, v in (data.get("mark_prices") or {}).items()}

    def get_funding_rate_sync(self, symbol: str) -> Optional[Decimal]:
        sym = symbol.replace("/", "%2F")
        data = self._sget(f"/api/datahub/funding_rate/{sym}")
        if not data:
            return None
        fr = data.get("funding_rate")
        return Decimal(fr) if fr else None

    async def fapi_request_get(
        self, path: str, params: Optional[dict] = None, timeout: float = 8.0
    ) -> Optional[Any]:
        data = await self._apost(
            "/api/datahub/rest/fapi/get",
            {"path": path, "params": params, "timeout": timeout},
        )
        return data.get("data") if data else None

    async def spot_request_get(
        self, path: str, params: Optional[dict] = None, timeout: float = 8.0
    ) -> Optional[Any]:
        data = await self._apost(
            "/api/datahub/rest/spot/get",
            {"path": path, "params": params, "timeout": timeout},
        )
        return data.get("data") if data else None

    def fapi_request_get_sync(
        self, path: str, params: Optional[dict] = None, timeout: float = 8.0
    ) -> Optional[Any]:
        data = self._spost(
            "/api/datahub/rest/fapi/get",
            {"path": path, "params": params, "timeout": timeout},
        )
        return data.get("data") if data else None

    def spot_request_get_sync(
        self, path: str, params: Optional[dict] = None, timeout: float = 8.0
    ) -> Optional[Any]:
        data = self._spost(
            "/api/datahub/rest/spot/get",
            {"path": path, "params": params, "timeout": timeout},
        )
        return data.get("data") if data else None


# ---------------------------------------------------------------------------
# 全局单例 + 跨进程代理
# ---------------------------------------------------------------------------

_global_hub: Optional[BinanceDataHub] = None
_global_proxy: Optional[HubHttpProxy] = None


def get_global_data_hub():
    """
    获取 hub 访问器.

    - 本进程已 init hub: 返回真正的 BinanceDataHub 实例 (in-process, 零开销)
    - 本进程未 init hub: 返回 HubHttpProxy (跨进程, 走 HTTP 到 main.py)

    业务代码可统一调用 hub.get_price() 等方法, 自动适配两种场景.
    """
    if _global_hub is not None:
        return _global_hub
    global _global_proxy
    if _global_proxy is None:
        _global_proxy = HubHttpProxy()
        logger.info("[DataHub] 本进程无 hub 实例, 自动启用 HubHttpProxy (跨进程访问 main)")
    return _global_proxy


def init_global_data_hub(db_config: Optional[dict] = None) -> BinanceDataHub:
    """
    初始化全局 hub. 多次调用安全 (返回已存在实例).

    仅在 app/main.py lifespan 启动时调用一次. 其他独立进程不应调用此函数,
    它们应通过 get_global_data_hub() 自动获得 HubHttpProxy.
    """
    global _global_hub
    if _global_hub is None:
        _global_hub = BinanceDataHub(db_config=db_config)
        logger.info("[DataHub] 全局单例已初始化 (本进程为 hub 宿主)")
    return _global_hub


async def stop_global_data_hub() -> None:
    global _global_hub
    if _global_hub is not None:
        await _global_hub.stop()
        _global_hub = None
