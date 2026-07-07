#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓止盈止损监控服务（轻量版）

职责：
- 周期扫描 futures_positions 中所有 status='open' 的持仓
- 用进程内 DataHub 实时价与 DB 里存的 stop_loss_price / take_profit_price 比较
- 命中 SL/TP 后直接调用模拟盘平仓引擎，避免 main 进程 HTTP 反打自己
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import os
import time
from typing import Any, Dict, List, Optional

import pymysql
from loguru import logger
from app.utils.position_time import utc_now_naive


def _db_cfg() -> Dict[str, Any]:
    """全部从 .env 读，不带任何生产值默认。"""
    return {
        "host":     os.getenv("DB_HOST", "localhost"),
        "port":     int(os.getenv("DB_PORT", "3306")),
        "user":     os.getenv("DB_USER", ""),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", ""),
        "charset":  "utf8mb4",
        "connect_timeout": 5,
        "read_timeout": 10,
        "write_timeout": 10,
        "cursorclass": pymysql.cursors.DictCursor,
    }


# 动态出场规则（与 strategy_live/whale/bigmid MID 同）
# monitor 扫描频率高（默认 1s）可以更及时抓到小币快速穿越
TRAIL_TP_TIERS = [
    (0.10, 0.03),  # peak ≥ 10% → 回落 3% 平
    (0.05, 0.02),  # peak ≥ 5%  → 回落 2% 平
    (0.03, 0.01),  # peak ≥ 3%  → 回落 1% 平
]
EARLY_SL_PCT             = 0.03   # 浮亏 ≥ 3% 早期止损
# peak ≥ 1.5% 启用保本守护（2026-04-24 从 3% 降低；补 peak 1-3% 的盲区）
BREAKEVEN_AFTER_PEAK_PCT = 0.015
BREAKEVEN_SL_PCT         = -0.005 # 保本线 -0.5%
# 入场保护期：开仓 N 分钟内 early-sl/breakeven 不触发（硬 SL 兜底）
# 2026-04-24：数据显示 38% early-sl 在 5m 内扎中（入场瞬间均值回归误杀）
ENTRY_GRACE_MIN          = 45

# AI 探索/预测/战术：硬 SL/TP + 轻量 ai-trail-tp；不走 early-sl / breakeven
_AI_HARD_SLTP_ONLY_SOURCES = frozenset({
    'gemini_explore', 'gemini_predict',
    'deepseek_explore', 'deepseek_predict',
})
# AI 轻量移动止盈：峰值价格收益 ≥3% 后，从峰值回撤 ≥1% 平仓
_AI_TRAIL_TP_ACTIVATE = 0.03
_AI_TRAIL_TP_PULLBACK = 0.01
_AI_TRAIL_TP_ROI_ACTIVATE = 0.06
_AI_TRAIL_TP_ROI_PULLBACK = 0.02
_MIDLINE_SOURCES = frozenset({
    'gemini_midline_long', 'gemini_midline_short',
    'deepseek_midline_long', 'deepseek_midline_short',
})


def _is_midline_source(src: str) -> bool:
    return (src or "").strip().lower() in _MIDLINE_SOURCES
# 硬 TP 开仓保护：避免 entry 价与 monitor 市价源不一致时秒平（SL 仍立即生效）
_AI_TP_GRACE_MIN = 5


def _is_ai_hard_sltp_source(src: str) -> bool:
    if not src:
        return False
    if _is_midline_source(src):
        return True
    if src in _AI_HARD_SLTP_ONLY_SOURCES:
        return True
    if src.startswith(("gemini_", "deepseek_")):
        for key in ("explore", "predict"):
            if key in src:
                return True
    return False


def _dynamic_trail_pullback(peak_pct: float) -> float:
    for threshold, pullback in TRAIL_TP_TIERS:
        if peak_pct >= threshold:
            return pullback
    return float('inf')


def _check_ai_trail_tp(pnl_pct: float, peak_pct: float, leverage: int = 1) -> Optional[str]:
    """AI strategies trail by either price move or leverage-adjusted ROI."""
    lev = max(int(leverage or 1), 1)
    pullback_pct = peak_pct - pnl_pct
    peak_roi = peak_pct * lev
    pullback_roi = pullback_pct * lev
    price_trail = peak_pct >= _AI_TRAIL_TP_ACTIVATE and pullback_pct >= _AI_TRAIL_TP_PULLBACK
    roi_trail = peak_roi >= _AI_TRAIL_TP_ROI_ACTIVATE and pullback_roi >= _AI_TRAIL_TP_ROI_PULLBACK
    if price_trail or roi_trail:
        return (
            f"AI trail-tp(peak_price={peak_pct * 100:.2f}%, "
            f"drawdown_price={pullback_pct * 100:.2f}%, "
            f"peak_roi={peak_roi * 100:.2f}%, "
            f"drawdown_roi={pullback_roi * 100:.2f}%, ai-trail-tp)"
        )
    return None


class PositionSLTPMonitor:
    """价格驱动的止盈止损监控循环。"""

    def __init__(
        self,
        engine=None,                 # 保留参数兼容旧调用，不再使用
        interval_seconds: float = 1.0,
        source_filter: str = "%",
        price_max_age_seconds: int = 30,
        api_base: str = "http://localhost:9020",
    ) -> None:
        self.interval = float(interval_seconds)
        self.source_filter = source_filter
        self.price_max_age = int(price_max_age_seconds)
        self.api_base = api_base.rstrip("/")
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        self._cooldown: Dict[int, float] = {}
        self._cooldown_seconds = 10.0
        # peak_pnl_pct 内存映射：进程重启会丢，但一般持仓 <= 24h 影响可控
        self._peak_pnl_map: Dict[int, float] = {}
        # disable_sl_tp_hold 开关缓存，避免每秒查 DB
        self._disable_cache: tuple[float, bool] = (0.0, False)
        self._disable_cache_ttl = 10.0

    def start(self) -> None:
        if self._task and not self._task.done():
            logger.info("[SL/TP Monitor] 已在运行，跳过重复启动")
            return
        self._stop = False
        self._task = asyncio.create_task(self._run())
        logger.info(
            f"[SL/TP Monitor] 启动 (interval={self.interval}s, "
            f"source_filter='{self.source_filter}')"
        )

    def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        while not self._stop:
            try:
                await asyncio.to_thread(self._tick_once)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[SL/TP Monitor] tick 异常: {e}")
            await asyncio.sleep(self.interval)

    def _tick_once(self) -> None:
        positions = self._fetch_open_positions()
        if not positions:
            return

        try:
            from app.services.binance_ws_price import get_ws_price_service
            ws = get_ws_price_service("futures")
        except Exception:
            ws = None

        # 全局裸奔开关（10s 缓存）
        disable_rules = self._is_disable_sl_tp_hold()

        # 清理已不在 open 列表的 peak 记录
        alive_pids = {int(p["id"]) for p in positions}
        self._peak_pnl_map = {k: v for k, v in self._peak_pnl_map.items() if k in alive_pids}

        now = time.time()
        for pos in positions:
            pid = int(pos["id"])
            if self._cooldown.get(pid, 0) > now:
                continue

            symbol = pos["symbol"]
            side = pos["position_side"]
            entry_price = float(pos.get("entry_price") or 0)
            sl = pos.get("stop_loss_price")
            tp = pos.get("take_profit_price")
            src = pos.get('source') or ''

            if sl is None and tp is None:
                # 无 SL/TP 的中线旧仓：仅计划到期 + 爆仓（不走 ai-trail-tp）
                if _is_midline_source(src):
                    price = self._get_live_price(ws, symbol)
                    if price is None or price <= 0:
                        continue
                    liq = pos.get("liquidation_price")
                    reason_mid: Optional[str] = None
                    pct = pos.get("planned_close_time")
                    if pct is not None:
                        if isinstance(pct, _dt.datetime):
                            if pct.tzinfo is not None:
                                pct = pct.replace(tzinfo=None)
                            if utc_now_naive() >= pct:
                                reason_mid = "planned_close_time_expired"
                    if not reason_mid and liq is not None and float(liq) > 0:
                        liq_f = float(liq)
                        if side.upper() == "LONG" and price <= liq_f:
                            reason_mid = "liquidation"
                        elif side.upper() == "SHORT" and price >= liq_f:
                            reason_mid = "liquidation"
                    if reason_mid:
                        logger.warning(
                            f"[SL/TP Monitor] 中线平仓 pid={pid} {symbol} {side} "
                            f"reason={reason_mid} price={price:.6f}"
                        )
                        self._cooldown[pid] = now + self._cooldown_seconds
                        self._do_close(pid, symbol, side, reason_mid, price, now)
                continue
            if entry_price <= 0:
                continue

            # 计划持仓到期（AI 探索/预测等为 2h）— 与 SmartExitOptimizer 互补；
            # 本服务随 FastAPI 常驻，避免仅 smart_trader 在跑时才到期平仓。
            pct = pos.get("planned_close_time")
            if pct is not None:
                if isinstance(pct, _dt.datetime):
                    if pct.tzinfo is not None:
                        pct = pct.replace(tzinfo=None)
                    if utc_now_naive() >= pct:
                        price = self._get_live_price(ws, symbol) or entry_price
                        logger.warning(
                            f"[SL/TP Monitor] 计划平仓到期 pid={pid} {symbol} {side} "
                            f"planned={pct.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        self._cooldown[pid] = now + self._cooldown_seconds
                        self._peak_pnl_map.pop(pid, None)
                        self._do_close(
                            pid, symbol, side,
                            "planned_close_time_expired",
                            price, now,
                        )
                        continue

            price = self._get_live_price(ws, symbol)
            if price is None or price <= 0:
                continue

            # 计算浮盈浮亏百分比（价格维度）
            if side.upper() == "LONG":
                pnl_pct = (price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - price) / entry_price

            # 更新 peak
            prev_peak = self._peak_pnl_map.get(pid, 0.0)
            new_peak = max(prev_peak, pnl_pct)
            if new_peak != prev_peak:
                self._peak_pnl_map[pid] = new_peak

            reason: Optional[str] = None
            trigger_price = price

            # ────────────────────────────────────────────────────────────────
            # AI 探索/预测：硬 SL/TP + 轻量 ai-trail-tp（中线仅硬 SL/TP，无 ai-trail-tp）
            # ────────────────────────────────────────────────────────────────
            src = pos.get('source') or ''
            if _is_ai_hard_sltp_source(src):
                in_tp_grace = False
                age_s = 0.0
                open_time = pos.get("open_time")
                if open_time:
                    if isinstance(open_time, _dt.datetime):
                        age_s = (utc_now_naive() - open_time).total_seconds()
                        in_tp_grace = age_s < _AI_TP_GRACE_MIN * 60

                trig = self._check_trigger(side, price, sl, tp)
                if trig:
                    reason, trigger_price = trig
                    if reason == "take_profit" and in_tp_grace:
                        logger.info(
                            f"[AI硬SL/TP] pid={pid} {symbol} TP 保护期内跳过 "
                            f"({age_s:.0f}s < {_AI_TP_GRACE_MIN}min) "
                            f"price={price:.6f} TP={tp} entry={entry_price:.6f}"
                        )
                        continue
                    logger.info(
                        f"[AI硬SL/TP] pid={pid} {symbol} {side} source={src} "
                        f"reason={reason} price={price:.6f} SL={sl} TP={tp}"
                    )
                    self._cooldown[pid] = now + self._cooldown_seconds
                    self._peak_pnl_map.pop(pid, None)
                    self._do_close(pid, symbol, side, reason, trigger_price, now)
                    continue

                if not _is_midline_source(src):
                    trail_ai = _check_ai_trail_tp(
                        pnl_pct,
                        new_peak,
                        int(pos.get("leverage") or 1),
                    )
                    if trail_ai:
                        self._sync_peak_to_db(pid, new_peak * 100)
                        logger.info(
                            f"[AI trail-tp] pid={pid} {symbol} {side} source={src} "
                            f"reason={trail_ai} price={price:.6f} peak={new_peak * 100:.2f}%"
                        )
                        self._cooldown[pid] = now + self._cooldown_seconds
                        self._peak_pnl_map.pop(pid, None)
                        self._do_close(pid, symbol, side, trail_ai, price, now)
                continue

            # 1. 新规则（受 disable_sl_tp_hold 控制）
            if not disable_rules:
                # 入场保护期：开仓 ENTRY_GRACE_MIN 分钟内 early-sl/breakeven 不触发
                open_time = pos.get("open_time")
                in_grace = False
                if open_time:
                    if isinstance(open_time, _dt.datetime):
                        age_s = (utc_now_naive() - open_time).total_seconds()
                        in_grace = age_s < ENTRY_GRACE_MIN * 60

                pullback_thresh = _dynamic_trail_pullback(new_peak)
                if (new_peak - pnl_pct) >= pullback_thresh:
                    current_drawdown = (new_peak - pnl_pct) * 100
                    reason = (
                        f"移动止盈(峰值价格收益{new_peak*100:.2f}% "
                        f"回撤{current_drawdown:.2f}%, trail-tp)"
                    )
                    # 同步 peak 到 DB，方便复盘分析
                    self._sync_peak_to_db(pid, new_peak * 100)
                elif not in_grace and new_peak >= BREAKEVEN_AFTER_PEAK_PCT and pnl_pct <= BREAKEVEN_SL_PCT:
                    reason = "breakeven-sl"
                elif not in_grace and pnl_pct <= -EARLY_SL_PCT:
                    reason = "early-sl"

            # 2. 原硬 SL/TP（兜底，永远生效）
            if not reason:
                trig = self._check_trigger(side, price, sl, tp)
                if trig:
                    reason, trigger_price = trig

            if not reason:
                continue

            logger.warning(
                f"[SL/TP Monitor] 触发平仓 pid={pid} {symbol} {side} "
                f"reason={reason} price={price:.6f} pnl={pnl_pct*100:+.2f}% "
                f"peak={new_peak*100:+.2f}% SL={sl} TP={tp}"
            )
            self._cooldown[pid] = now + self._cooldown_seconds
            self._peak_pnl_map.pop(pid, None)
            self._do_close(pid, symbol, side, reason, trigger_price, now)

    def _is_disable_sl_tp_hold(self) -> bool:
        """读 system_settings.disable_sl_tp_hold，10s 缓存避免每秒查 DB"""
        now = time.time()
        ts, val = self._disable_cache
        if (now - ts) < self._disable_cache_ttl:
            return val
        try:
            from app.services.system_settings_loader import get_disable_sl_tp_hold
            val = get_disable_sl_tp_hold()
        except Exception:
            val = False
        self._disable_cache = (now, val)
        return val

    def _sync_peak_to_db(self, pid: int, peak_pct: float) -> None:
        """将峰值价格收益率同步到 futures_positions.max_profit_pct（DB 字段为价格%）。

        仅在 peak_pct > 当前 DB 记录时更新，避免旧值覆盖新值。
        使用独立短连接，异常不抛出。
        """
        try:
            conn = pymysql.connect(**_db_cfg())
            try:
                with conn.cursor() as c:
                    c.execute(
                        "UPDATE futures_positions "
                        "SET max_profit_pct = GREATEST(COALESCE(max_profit_pct, 0), %s) "
                        "WHERE id=%s AND status='open'",
                        (peak_pct, pid),
                    )
                    conn.commit()
            finally:
                conn.close()
        except Exception:
            pass  # 峰值同步非关键路径，静默失败

    def _do_close(self, pid: int, symbol: str, side: str, reason: str,
                  trigger_price: float, now: float) -> None:
        """直接调用模拟盘平仓引擎，避免后台监控 HTTP 反打 FastAPI 自己。"""
        try:
            from decimal import Decimal
            from app.api.futures_api import _get_engine

            data = _get_engine().close_position(
                position_id=pid,
                close_quantity=None,
                reason=reason,
                close_price=Decimal(str(trigger_price)) if trigger_price else None,
            )
        except Exception as e:
            logger.exception(f"[SL/TP Monitor] 平仓调用异常 pid={pid}: {e}")
            self._cooldown[pid] = now + max(self._cooldown_seconds, 60.0)
            return

        if not data.get("success"):
            self._cooldown[pid] = now + max(self._cooldown_seconds, 60.0)
            logger.error(
                f"[SL/TP Monitor] 平仓失败 pid={pid}: "
                f"{data.get('message') or data.get('error') or data}"
            )
            return

        inner = data.get("data") or data
        if inner.get("already_closed") or data.get("already_closed"):
            logger.info(f"[SL/TP Monitor] pid={pid} 已在别处平仓，跳过")
        else:
            logger.info(
                f"[SL/TP Monitor] 平仓成功 pid={pid} {symbol} {side} "
                f"realized_pnl={inner.get('realized_pnl')} "
                f"pnl_pct={inner.get('pnl_pct')} "
                f"exit_price={inner.get('exit_price') or inner.get('close_price')}"
            )

    def _fetch_open_positions(self) -> List[Dict[str, Any]]:
        sql = (
            "SELECT id, symbol, position_side, entry_price, leverage, "
            "       stop_loss_price, take_profit_price, liquidation_price, "
            "       source, open_time, planned_close_time "
            "FROM futures_positions "
            "WHERE status='open' "
            "  AND (source LIKE %s) "
            "  AND ("
            "    stop_loss_price IS NOT NULL OR take_profit_price IS NOT NULL "
            "    OR source IN ('gemini_midline_long','gemini_midline_short',"
            "                  'deepseek_midline_long','deepseek_midline_short')"
            "  ) "
            "LIMIT 500"
        )
        try:
            conn = pymysql.connect(**_db_cfg())
            try:
                with conn.cursor() as c:
                    c.execute(sql, (self.source_filter,))
                    rows = c.fetchall() or []
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[SL/TP Monitor] 查询持仓失败: {e}")
            return []

        out: List[Dict[str, Any]] = []
        for r in rows:
            r["stop_loss_price"]   = float(r["stop_loss_price"])   if r.get("stop_loss_price")   is not None else None
            r["take_profit_price"] = float(r["take_profit_price"]) if r.get("take_profit_price") is not None else None
            r["liquidation_price"] = float(r["liquidation_price"]) if r.get("liquidation_price") is not None else None
            out.append(r)
        return out

    def _get_live_price(self, ws, symbol: str) -> Optional[float]:
        # 1. 首选 DataHub 进程内缓存 / WS / 受限 REST，避免 HTTP 反打 FastAPI 自己。
        try:
            from app.services.binance_data_hub import get_global_data_hub
            hub = get_global_data_hub()
            if hub is not None:
                p = hub.get_trade_price_sync(
                    symbol,
                    max_age_seconds=self.price_max_age,
                    allow_rest_fallback=True,
                    allow_db_fallback=False,
                )
                if p is not None and p > 0:
                    return float(p)
        except Exception as e:
            logger.debug(f"[SL/TP Monitor] DataHub 取价失败 {symbol}: {e}")

        # 2. 兼容旧 WS price 服务（如果进程内仍有启动）。
        if ws is not None:
            try:
                p = ws.get_price(symbol, max_age_seconds=self.price_max_age)
                if p is not None and p > 0:
                    return float(p)
            except Exception:
                pass

        # 3. 最后回退 DB 5m K 线，保证后台循环不依赖本机 HTTP 服务。
        try:
            from app.utils.futures_symbol import futures_symbol_kline_keys

            keys = futures_symbol_kline_keys(symbol)
            conn = pymysql.connect(**_db_cfg())
            try:
                with conn.cursor() as c:
                    placeholders = ",".join(["%s"] * len(keys))
                    c.execute(
                        f"""
                        SELECT close_price
                        FROM kline_data
                        WHERE symbol IN ({placeholders})
                          AND timeframe='5m'
                          AND exchange='binance_futures'
                        ORDER BY `timestamp` DESC
                        LIMIT 1
                        """,
                        tuple(keys),
                    )
                    row = c.fetchone()
                    if row and row.get("close_price"):
                        price = float(row["close_price"])
                        if price > 0:
                            return price
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"[SL/TP Monitor] DB 价格回退失败 {symbol}: {e}")
        return None

    @staticmethod
    def _check_trigger(
        side: str,
        price: float,
        sl: Optional[float],
        tp: Optional[float],
    ) -> Optional[tuple]:
        side = (side or "").upper()
        if side == "LONG":
            if sl is not None and price <= sl:
                return ("stop_loss", sl)
            if tp is not None and price >= tp:
                return ("take_profit", tp)
        elif side == "SHORT":
            if sl is not None and price >= sl:
                return ("stop_loss", sl)
            if tp is not None and price <= tp:
                return ("take_profit", tp)
        return None


_monitor_instance: Optional[PositionSLTPMonitor] = None


def init_sl_tp_monitor(engine=None, **kwargs) -> PositionSLTPMonitor:
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = PositionSLTPMonitor(engine, **kwargs)
    return _monitor_instance


def get_sl_tp_monitor() -> Optional[PositionSLTPMonitor]:
    return _monitor_instance
