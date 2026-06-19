# -*- coding: utf-8 -*-
"""
模拟盘开仓单 -> 实盘同步服务

职责：
- 每 10 秒扫描**刚成交**的模拟盘 OPEN_LONG/OPEN_SHORT（live_sync_status IS NULL）
- **仅开仓瞬间同步**：成交时 live 已开且闸门通过才留 NULL；否则当场标 SKIPPED
- 打开 live_trading_enabled **不会**回填历史模拟仓（超窗 NULL 标 SKIPPED）
- 与 AI 策略相同：check_live_open_allowed(source 白名单 + symbol 评级闸门)
- 成功：SYNCED；闸门拒绝/实盘关：SKIPPED；技术失败：FAILED（均不重试）
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import requests
from loguru import logger
from urllib.parse import quote

from app.services.paper_limit_entry import PAPER_ACCOUNT_ID
from app.utils.config_loader import get_db_config

# 仅同步成交后此时间窗内的单（开仓瞬间）；禁止打开实盘开关后回填历史模拟仓
LIVE_SYNC_FILL_WINDOW_MINUTES = 5


def _db_cfg() -> Dict[str, Any]:
    cfg = dict(get_db_config())
    cfg.setdefault("charset", "utf8mb4")
    cfg.setdefault("connect_timeout", 5)
    cfg.setdefault("read_timeout", 10)
    cfg.setdefault("write_timeout", 10)
    cfg["cursorclass"] = pymysql.cursors.DictCursor
    return cfg


def decide_live_sync_at_paper_fill(
    symbol: str,
    source: str,
    cursor=None,
) -> Tuple[Optional[str], str]:
    """
    模拟盘限价/市价成交瞬间决定是否排队实盘同步。

    Returns:
        (live_sync_status, reason)
        - (None, '')  → 留 NULL，PaperSync 在时间窗内同步
        - ('SKIPPED', reason) → 永不同步此单（含实盘关、策略不在白名单等）
    """
    from app.services.trading_gates import check_live_open_allowed

    allowed, reason = check_live_open_allowed(symbol, source, cursor=cursor)
    if allowed:
        return None, ""
    return "SKIPPED", reason


def mark_stale_unsynced_paper_orders(
    cursor,
    *,
    window_minutes: int = LIVE_SYNC_FILL_WINDOW_MINUTES,
    only_open_positions: bool = True,
) -> int:
    """
    将超时间窗仍为 NULL 的模拟开仓单标为 SKIPPED，防止打开实盘开关后回填历史仓。
    only_open_positions=False 时用于用户刚打开 live 开关的一次性清理。
    """
    open_clause = "AND fp.status = 'open'" if only_open_positions else ""
    cursor.execute(
        f"""
        UPDATE futures_orders fo
        JOIN futures_positions fp ON fp.id = fo.position_id
        JOIN futures_trading_accounts fta ON fta.id = fo.account_id
        SET fo.live_sync_status = 'SKIPPED',
            fo.live_synced_at = NOW(),
            fo.live_position_id = NULL
        WHERE fo.status = 'FILLED'
          AND fo.side IN ('OPEN_LONG', 'OPEN_SHORT')
          AND fo.live_sync_status IS NULL
          AND fta.id = %s
          {open_clause}
          AND (
                fo.fill_time IS NULL
                OR fo.fill_time < NOW() - INTERVAL %s MINUTE
              )
        """,
        (PAPER_ACCOUNT_ID, int(window_minutes)),
    )
    return int(cursor.rowcount or 0)


def skip_all_pending_paper_live_sync(cursor) -> int:
    """用户打开实盘同步开关时：所有仍 open 且未同步的模拟单一律 SKIPPED，禁止回填。"""
    cursor.execute(
        """
        UPDATE futures_orders fo
        JOIN futures_positions fp ON fp.id = fo.position_id
        JOIN futures_trading_accounts fta ON fta.id = fo.account_id
        SET fo.live_sync_status = 'SKIPPED',
            fo.live_synced_at = NOW(),
            fo.live_position_id = NULL
        WHERE fo.status = 'FILLED'
          AND fo.side IN ('OPEN_LONG', 'OPEN_SHORT')
          AND fo.live_sync_status IS NULL
          AND fp.status = 'open'
          AND fta.id = %s
        """,
        (PAPER_ACCOUNT_ID,),
    )
    return int(cursor.rowcount or 0)


class PaperLimitSyncService:
    """模拟盘限价单成交后自动同步到实盘。"""

    def __init__(
        self,
        interval_seconds: float = 10.0,
        api_base: str = "http://localhost:9020",
    ) -> None:
        self.interval = interval_seconds
        self.api_base = api_base.rstrip("/")
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    def start(self) -> None:
        if self._task and not self._task.done():
            logger.info("[PaperSync] 已在运行，跳过重复启动")
            return
        self._stop = False
        self._task = asyncio.create_task(self._run())
        logger.info(f"[PaperSync] 启动 (interval={self.interval}s)")

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
                logger.error(f"[PaperSync] tick 异常: {e}")
            await asyncio.sleep(self.interval)

    # ── 主逻辑 ────────────────────────────────────────────────────

    def _tick_once(self) -> None:
        try:
            conn = pymysql.connect(**_db_cfg())
        except Exception as e:
            logger.error(f"[PaperSync] 数据库连接失败: {e}")
            return

        try:
            with conn.cursor() as cur:
                if not self._is_live_enabled(cur):
                    # 实盘关：超窗 NULL 标 SKIPPED，避免日后开开关回填
                    n = mark_stale_unsynced_paper_orders(cur)
                    if n:
                        conn.commit()
                        logger.info(f"[PaperSync] 实盘关闭，已将 {n} 笔超窗未同步单标为 SKIPPED")
                    return

                n = mark_stale_unsynced_paper_orders(cur)
                if n:
                    conn.commit()
                    logger.info(f"[PaperSync] 已将 {n} 笔超窗未同步单标为 SKIPPED（禁止历史回填）")

                orders = self._fetch_pending_sync(cur)

            for order in orders:
                self._sync_one(conn, order)
        finally:
            conn.close()

    def _is_live_enabled(self, cur) -> bool:
        cur.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key='live_trading_enabled' LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return False
        return str(row["setting_value"]).strip() == "1"

    def _fetch_pending_sync(self, cur) -> List[Dict]:
        # 仅同步成交后 LIVE_SYNC_FILL_WINDOW_MINUTES 内的单（开仓瞬间）
        cur.execute(
            """
            SELECT
                fo.id, fo.account_id, fo.symbol, fo.side,
                fo.leverage, fo.quantity, fo.avg_fill_price,
                COALESCE(fo.stop_loss_price, fp.stop_loss_price) AS stop_loss_price,
                COALESCE(fo.take_profit_price, fp.take_profit_price) AS take_profit_price,
                fo.order_source, fo.position_id,
                fta.user_id
            FROM futures_orders fo
            JOIN futures_trading_accounts fta ON fta.id = fo.account_id
            JOIN futures_positions fp        ON fp.id  = fo.position_id
            WHERE fo.status = 'FILLED'
              AND fo.side IN ('OPEN_LONG', 'OPEN_SHORT')
              AND fo.live_sync_status IS NULL
              AND fo.fill_time >= NOW() - INTERVAL %s MINUTE
              AND fp.status = 'open'
              AND fta.id = %s
              AND NOT EXISTS (
                  SELECT 1 FROM futures_orders fo2
                  WHERE fo2.position_id = fo.position_id
                    AND fo2.id <> fo.id
                    AND fo2.live_sync_status = 'SYNCED'
              )
            ORDER BY fo.fill_time ASC
            LIMIT 20
            """,
            (LIVE_SYNC_FILL_WINDOW_MINUTES, PAPER_ACCOUNT_ID),
        )
        return cur.fetchall()

    def _sync_one(self, conn, order: Dict) -> None:
        order_id = order["id"]
        symbol = order["symbol"]
        user_id = order["user_id"]
        pos_side = "LONG" if "LONG" in str(order["side"]) else "SHORT"

        # 原子级兄弟单去重（2026-04-24）：_fetch_pending_sync 的 NOT EXISTS 是批次快照，
        # 同一 paper_pid 的 LIMIT+MARKET 若同批进入，第一笔写 SYNCED 后第二笔仍用旧 dict，
        # 会重复开仓。这里在 engine.open_position 前再查一次，命中则挂上兄弟的 live_pid 标 SYNCED 退出。
        paper_pid = order.get("position_id")
        if paper_pid is not None:
            try:
                with conn.cursor() as _cur:
                    _cur.execute(
                        """SELECT id, live_position_id FROM futures_orders
                        WHERE position_id=%s AND id<>%s
                          AND live_sync_status='SYNCED' LIMIT 1""",
                        (paper_pid, order_id),
                    )
                    sibling = _cur.fetchone()
            except Exception as e:
                logger.warning(f"[PaperSync] 兄弟单校验异常 order_id={order_id}: {e}")
                sibling = None
            if sibling:
                logger.info(
                    f"[PaperSync] order_id={order_id} 同 paper_pid={paper_pid} 兄弟单 {sibling['id']} "
                    f"已 SYNCED (live_pid={sibling.get('live_position_id')}), 跳过避免双开"
                )
                self._mark(conn, order_id, "SYNCED", sibling.get("live_position_id"))
                return

        order_source = (order.get("order_source") or "manual").strip()

        try:
            from app.services.trading_gates import check_live_open_allowed

            with conn.cursor() as gate_cur:
                allowed, reason = check_live_open_allowed(
                    symbol, order_source, cursor=gate_cur,
                )
            if not allowed:
                logger.info(
                    "[PaperSync] order_id=%s %s source=%s 跳过实盘同步: %s",
                    order_id, symbol, order_source, reason,
                )
                self._mark(conn, order_id, "SKIPPED", None)
                return

            api_cfg = self._get_api_config(user_id)
            if api_cfg is None:
                logger.warning(f"[PaperSync] user_id={user_id} 无活跃 API key，跳过 order_id={order_id}")
                self._mark(conn, order_id, "FAILED", None)
                return

            live_account_id = self._get_live_account_id(user_id)
            if live_account_id is None:
                logger.warning(f"[PaperSync] user_id={user_id} 无实盘账户，跳过 order_id={order_id}")
                self._mark(conn, order_id, "FAILED", None)
                return

            from app.services.trading_gates import get_live_margin_ratio

            with conn.cursor() as ratio_cur:
                margin_ratio = get_live_margin_ratio(symbol, ratio_cur)
            margin = float(api_cfg["base_margin"]) * margin_ratio
            leverage = int(api_cfg["max_leverage"])
            if margin_ratio <= 0 or margin < 5:
                logger.info(
                    "[PaperSync] order_id=%s %s margin_ratio=%s margin=%.2fU, 跳过实盘同步",
                    order_id, symbol, margin_ratio, margin,
                )
                self._mark(conn, order_id, "SKIPPED", None)
                return

            price = self._get_price(symbol)
            if price is None or price <= 0:
                logger.warning(f"[PaperSync] 获取 {symbol} 价格失败，跳过 order_id={order_id}")
                self._mark(conn, order_id, "FAILED", None)
                return

            quantity = Decimal(str(round(margin * leverage / price, 6)))

            from app.services.user_trading_engine_manager import get_engine_manager
            mgr = get_engine_manager()
            if mgr is None:
                logger.error(f"[PaperSync] engine_manager 未初始化，跳过 order_id={order_id}")
                self._mark(conn, order_id, "FAILED", None)
                return

            engine = mgr.get_engine(user_id)
            if engine is None:
                logger.warning(f"[PaperSync] user_id={user_id} 引擎为 None，跳过 order_id={order_id}")
                self._mark(conn, order_id, "FAILED", None)
                return

            # 将纸面 SL/TP 转为百分比，基于实盘实际成交价重算绝对价格
            # 避免纸面绝对价与实盘成交价偏差导致 SL/TP 验证失败
            paper_fill = float(order["avg_fill_price"] or 0)
            paper_sl = float(order["stop_loss_price"] or 0)
            paper_tp = float(order["take_profit_price"] or 0)

            sl_pct: Optional[Decimal] = None
            tp_pct: Optional[Decimal] = None

            if paper_fill > 0:
                if paper_sl > 0:
                    raw_sl = (paper_fill - paper_sl) / paper_fill * 100 if pos_side == "LONG" \
                        else (paper_sl - paper_fill) / paper_fill * 100
                    if raw_sl > 0:
                        sl_pct = Decimal(str(round(raw_sl, 4)))
                if paper_tp > 0:
                    raw_tp = (paper_tp - paper_fill) / paper_fill * 100 if pos_side == "LONG" \
                        else (paper_fill - paper_tp) / paper_fill * 100
                    if raw_tp > 0:
                        tp_pct = Decimal(str(round(raw_tp, 4)))

            if paper_fill <= 0 or sl_pct is None or tp_pct is None:
                logger.warning(
                    "[PaperSync] order_id=%s %s 无法计算SL/TP百分比 "
                    "fill=%.6f sl=%.6f tp=%.6f sl_pct=%s tp_pct=%s",
                    order_id, symbol, paper_fill, paper_sl, paper_tp, sl_pct, tp_pct,
                )
                self._mark(conn, order_id, "FAILED", None)
                return

            result = engine.open_position(
                account_id=live_account_id,
                symbol=symbol,
                position_side=pos_side,
                quantity=quantity,
                leverage=leverage,
                stop_loss_pct=sl_pct,
                take_profit_pct=tp_pct,
                source=order_source,
                paper_position_id=order.get("position_id"),
            )

            if result and result.get("success"):
                live_pid = str(result.get("position_id") or result.get("id") or "")
                logger.info(
                    "[PaperSync] 同步成功 order_id=%s %s %s %s qty=%.4f lev=%dx live_pid=%s",
                    order_id, symbol, pos_side, price, float(quantity), leverage, live_pid,
                )
                self._mark(conn, order_id, "SYNCED", live_pid or None)
            else:
                res = result or {}
                err = res.get("error") or res.get("message") or "unknown"
                code = res.get("code")
                if code is not None and str(code) not in str(err):
                    err = f"[{code}] {err}"
                logger.error(f"[PaperSync] 实盘开仓失败 order_id={order_id} {symbol}: {err}")
                self._mark(conn, order_id, "FAILED", None)

        except Exception as e:
            logger.error(f"[PaperSync] 同步异常 order_id={order_id} {symbol}: {e}")
            self._mark(conn, order_id, "FAILED", None)

    # ── 辅助 ─────────────────────────────────────────────────────

    def _get_api_config(self, user_id: int) -> Optional[Dict]:
        try:
            conn = pymysql.connect(**_db_cfg())
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT max_position_value, max_leverage
                        FROM user_api_keys
                        WHERE user_id=%s AND status='active'
                        ORDER BY id ASC LIMIT 1""",
                        (user_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        return None
                    return {
                        "base_margin": float(row["max_position_value"] or 40.0),
                        "max_leverage": int(row["max_leverage"] or 5),
                    }
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[PaperSync] 读取 api_config 失败 user_id={user_id}: {e}")
            return None

    def _get_live_account_id(self, user_id: int) -> Optional[int]:
        try:
            conn = pymysql.connect(**_db_cfg())
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM live_trading_accounts WHERE user_id=%s LIMIT 1",
                        (user_id,),
                    )
                    row = cur.fetchone()
                    return int(row["id"]) if row else None
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"[PaperSync] 读取 live_account_id 失败 user_id={user_id}: {e}")
            return None

    def _get_price(self, symbol: str) -> Optional[float]:
        try:
            r = requests.get(
                f"{self.api_base}/api/futures/price/{quote(symbol, safe='')}", timeout=5
            )
            r.raise_for_status()
            return float(r.json()["price"])
        except Exception as e:
            price = self._get_price_fallback(symbol)
            if price and price > 0:
                return price
            logger.warning(f"[PaperSync] 获取价格失败 {symbol}: {e}")
            return None

    def _get_price_fallback(self, symbol: str) -> Optional[float]:
        try:
            from app.utils.futures_price import get_futures_trade_price

            price = get_futures_trade_price(
                None,
                symbol,
                max_age_seconds=90,
                log_tag="PaperSync",
                require_fresh=False,
            )
            if price and price > 0:
                return float(price)
        except Exception as e:
            logger.warning(f"[PaperSync] REST fallback price failed {symbol}: {e}")
        return None

    def _mark(self, conn, order_id: int, status: str, live_position_id: Optional[str]) -> None:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE futures_orders
                    SET live_sync_status=%s, live_synced_at=NOW(), live_position_id=%s
                    WHERE id=%s""",
                    (status, live_position_id, order_id),
                )
            conn.commit()
        except Exception as e:
            logger.error(f"[PaperSync] 更新同步状态失败 order_id={order_id}: {e}")


_service: Optional[PaperLimitSyncService] = None


def get_paper_limit_sync_service() -> Optional[PaperLimitSyncService]:
    return _service


def init_paper_limit_sync_service(
    interval_seconds: float = 10.0,
    api_base: str = "http://localhost:9020",
) -> PaperLimitSyncService:
    global _service
    _service = PaperLimitSyncService(interval_seconds=interval_seconds, api_base=api_base)
    return _service
