#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTC动量跟随策略
- 监测BTC实时价格，15~60分钟内涨跌幅 >= 1.5% 触发
- 开仓：TOP30全部交易对，方向与BTC一致
- 模拟盘：400U x5；实盘：账号配置(100U) x5
- 止损2%，止盈6%，触发后4小时冷却
- 同向持仓保留，反向持仓先平后开
"""

import pymysql
import pymysql.cursors
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Tuple
from loguru import logger


class BTCMomentumTrader:
    COOLDOWN_HOURS = 4
    TRIGGER_PCT = 1.5          # 触发阈值 %
    WINDOWS_MIN = [15, 30, 45, 60]  # 检测窗口（分钟）
    PAPER_MARGIN = 400         # 模拟盘每单保证金(U)
    LEVERAGE = 5
    STOP_LOSS_PCT = 0.02       # 2%
    TAKE_PROFIT_PCT = 0.06     # 6%
    PAPER_ACCOUNT_ID = 2

    def __init__(self, db_config: dict, ws_price_service=None):
        self.db_config = db_config
        self.ws_service = ws_price_service
        self._btc_history: List[Tuple[datetime, float]] = []  # [(time, price)]
        self._last_trigger_time: Optional[datetime] = None
        self._preload_btc_history()  # 启动时从DB预填充，避免重启后等待15分钟

    def _get_conn(self):
        return pymysql.connect(
            **self.db_config, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, autocommit=True
        )

    # ──────────────────────────────────────────
    # 价格跟踪
    # ──────────────────────────────────────────

    def _preload_btc_history(self):
        """启动时从kline_data预加载最近90分钟BTC价格，避免重启后等待积累"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT open_time, close_price FROM kline_data "
                "WHERE symbol='BTC/USDT' AND timeframe='1m' AND exchange='binance_futures' "
                "AND open_time >= (UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 90 MINUTE)) * 1000) "
                "ORDER BY open_time ASC"
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            for row in rows:
                ts = datetime.utcfromtimestamp(row['open_time'] / 1000)
                self._btc_history.append((ts, float(row['close_price'])))
            logger.info(f"[BTC动量] 预加载 {len(self._btc_history)} 条BTC价格历史（最近90分钟）")
        except Exception as e:
            logger.warning(f"[BTC动量] 预加载历史失败: {e}")

    def record_btc_price(self, price: float):
        """主循环每分钟调用，记录BTC当前价格"""
        now = datetime.utcnow()
        self._btc_history.append((now, price))
        cutoff = now - timedelta(minutes=90)
        self._btc_history = [(t, p) for t, p in self._btc_history if t >= cutoff]

    def _get_btc_current_price(self) -> Optional[float]:
        if self.ws_service:
            p = self.ws_service.get_price('BTC/USDT')
            if p:
                return float(p)
        if self._btc_history:
            return self._btc_history[-1][1]
        return None

    # ──────────────────────────────────────────
    # 触发检测
    # ──────────────────────────────────────────

    def check_trigger(self) -> Optional[Tuple[str, int, float]]:
        """
        检查是否触发动量信号
        返回 (direction, window_min, pct) 或 None
        """
        # 冷却期
        if self._last_trigger_time:
            elapsed = (datetime.utcnow() - self._last_trigger_time).total_seconds()
            if elapsed < self.COOLDOWN_HOURS * 3600:
                remaining = (self.COOLDOWN_HOURS * 3600 - elapsed) / 60
                logger.debug(f"[BTC动量] 冷却中，剩余 {remaining:.0f} 分钟")
                return None

        current = self._get_btc_current_price()
        if not current or len(self._btc_history) < 5:
            return None

        now = datetime.utcnow()
        for window in self.WINDOWS_MIN:
            cutoff = now - timedelta(minutes=window)
            past_prices = [(t, p) for t, p in self._btc_history if t <= cutoff]
            if not past_prices:
                continue
            past_price = past_prices[-1][1]
            pct = (current - past_price) / past_price * 100
            if abs(pct) >= self.TRIGGER_PCT:
                direction = 'LONG' if pct > 0 else 'SHORT'
                return (direction, window, round(pct, 2))

        return None

    # ──────────────────────────────────────────
    # 数据查询
    # ──────────────────────────────────────────

    def _get_top30(self) -> List[str]:
        """从 top_performing_symbols 获取TOP30列表"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT symbol FROM top_performing_symbols ORDER BY rank_score DESC LIMIT 30")
            rows = cur.fetchall()
            cur.close(); conn.close()
            if rows:
                return [r['symbol'] for r in rows]
        except Exception as e:
            logger.warning(f"[BTC动量] 获取TOP30失败: {e}")
        return []

    def _get_symbol_price(self, symbol: str) -> Optional[float]:
        """获取交易对当前价格（WS优先，fallback DB）"""
        if self.ws_service:
            p = self.ws_service.get_price(symbol)
            if p:
                return float(p)
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT close_price FROM kline_data "
                "WHERE symbol=%s AND timeframe='1m' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 1", (symbol,)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            return float(row['close_price']) if row else None
        except:
            return None

    def _get_open_positions(self) -> Dict[str, dict]:
        """获取当前模拟盘所有开仓，返回 {symbol: position_row}"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, symbol, position_side, entry_price, margin "
                "FROM futures_positions WHERE status='open' AND account_id=%s",
                (self.PAPER_ACCOUNT_ID,)
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            return {r['symbol']: r for r in rows}
        except Exception as e:
            logger.error(f"[BTC动量] 查询持仓失败: {e}")
            return {}

    def _is_live_enabled(self) -> bool:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT setting_value FROM system_settings WHERE setting_key='live_trading_enabled'")
            row = cur.fetchone()
            cur.close(); conn.close()
            return row and str(row['setting_value']) in ('1', 'true')
        except:
            return False

    def _get_active_live_accounts(self) -> List[dict]:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, account_name, max_position_value, max_leverage "
                "FROM user_api_keys WHERE status='active'"
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            return rows
        except:
            return []

    # ──────────────────────────────────────────
    # 开平仓操作
    # ──────────────────────────────────────────

    def _close_position(self, pos: dict, reason: str = 'BTC动量反向平仓'):
        """平掉模拟盘指定持仓"""
        try:
            price = self._get_symbol_price(pos['symbol'])
            if not price:
                return
            entry = float(pos['entry_price'])
            margin = float(pos['margin'])
            if pos['position_side'] == 'LONG':
                pnl = (price - entry) / entry * margin * self.LEVERAGE
            else:
                pnl = (entry - price) / entry * margin * self.LEVERAGE
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "UPDATE futures_positions SET status='closed', close_time=NOW(), "
                "mark_price=%s, realized_pnl=%s, notes=%s WHERE id=%s",
                (price, round(pnl, 4), reason, pos['id'])
            )
            cur.close(); conn.close()
            logger.info(f"[BTC动量] 平仓 {pos['symbol']} {pos['position_side']} pnl={pnl:+.2f}U")
        except Exception as e:
            logger.error(f"[BTC动量] 平仓失败 {pos['symbol']}: {e}")

    def _open_paper_position(self, symbol: str, direction: str,
                              entry_price: float, trigger_info: str) -> bool:
        """开模拟盘仓位"""
        try:
            margin = self.PAPER_MARGIN
            notional = margin * self.LEVERAGE
            qty = round(notional / entry_price, 6)
            if direction == 'LONG':
                sl = round(entry_price * (1 - self.STOP_LOSS_PCT), 8)
                tp = round(entry_price * (1 + self.TAKE_PROFIT_PCT), 8)
            else:
                sl = round(entry_price * (1 + self.STOP_LOSS_PCT), 8)
                tp = round(entry_price * (1 - self.TAKE_PROFIT_PCT), 8)

            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO futures_positions
                    (account_id, symbol, position_side, leverage, quantity, notional_value,
                     margin, entry_price, mark_price, stop_loss_price, take_profit_price,
                     stop_loss_pct, take_profit_pct, status, source, entry_reason, open_time,
                     unrealized_pnl, unrealized_pnl_pct)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open','BTC_MOMENTUM',%s,NOW(),0,0)
            """, (self.PAPER_ACCOUNT_ID, symbol, direction, self.LEVERAGE, qty,
                  round(notional, 2), margin, entry_price, entry_price,
                  sl, tp, self.STOP_LOSS_PCT * 100, self.TAKE_PROFIT_PCT * 100,
                  trigger_info))
            cur.close(); conn.close()
            logger.info(f"[BTC动量] 开仓 {symbol} {direction} @ {entry_price:.6g}  SL={sl:.6g}  TP={tp:.6g}")
            return True
        except Exception as e:
            logger.error(f"[BTC动量] 开仓失败 {symbol}: {e}")
            return False

    def _sync_live(self, symbol: str, direction: str, entry_price: float,
                   paper_pos_id: int, trigger_info: str):
        """同步到实盘账号（如果live_trading_enabled）"""
        if not self._is_live_enabled():
            return
        accounts = self._get_active_live_accounts()
        for ak in accounts:
            try:
                margin = float(ak['max_position_value'] or 100)
                lev = int(ak['max_leverage'] or 5)
                notional = margin * lev
                qty = round(notional / entry_price, 6)
                if direction == 'LONG':
                    sl = round(entry_price * (1 - self.STOP_LOSS_PCT), 8)
                    tp = round(entry_price * (1 + self.TAKE_PROFIT_PCT), 8)
                else:
                    sl = round(entry_price * (1 + self.STOP_LOSS_PCT), 8)
                    tp = round(entry_price * (1 - self.TAKE_PROFIT_PCT), 8)
                conn = self._get_conn()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO live_futures_positions
                        (account_id, symbol, position_side, leverage, quantity, notional_value,
                         margin, entry_price, stop_loss_price, take_profit_price,
                         status, source, paper_position_id, open_time)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN','BTC_MOMENTUM',%s,NOW())
                """, (ak['id'], symbol, direction, lev, qty, round(notional, 2),
                      margin, entry_price, sl, tp, paper_pos_id))
                cur.close(); conn.close()
                logger.info(f"[BTC动量] 实盘同步 {ak['account_name']} {symbol} {direction}")
            except Exception as e:
                logger.error(f"[BTC动量] 实盘同步失败 {ak['account_name']} {symbol}: {e}")

    # ──────────────────────────────────────────
    # 主执行入口
    # ──────────────────────────────────────────

    def execute(self, direction: str, window: int, pct: float):
        """触发后执行全部TOP30交易"""
        trigger_info = f"BTC {window}分内{pct:+.2f}%"
        logger.info(f"🚀 [BTC动量] 触发！{trigger_info} → 开{direction}")

        top30 = self._get_top30()
        if not top30:
            logger.warning("[BTC动量] TOP30为空，跳过")
            return

        existing = self._get_open_positions()
        opened = 0

        for symbol in top30:
            if symbol in existing:
                pos = existing[symbol]
                if pos['position_side'] == direction:
                    logger.debug(f"[BTC动量] {symbol} 已有同向仓，保留")
                    continue
                else:
                    logger.info(f"[BTC动量] {symbol} 有反向仓，先平后开")
                    self._close_position(pos)

            entry_price = self._get_symbol_price(symbol)
            if not entry_price:
                logger.warning(f"[BTC动量] {symbol} 获取价格失败，跳过")
                continue

            if self._open_paper_position(symbol, direction, entry_price, trigger_info):
                opened += 1
                # 获取刚插入的ID用于实盘同步
                try:
                    conn = self._get_conn()
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT id FROM futures_positions WHERE account_id=%s AND symbol=%s "
                        "AND status='open' ORDER BY open_time DESC LIMIT 1",
                        (self.PAPER_ACCOUNT_ID, symbol)
                    )
                    row = cur.fetchone()
                    cur.close(); conn.close()
                    if row:
                        self._sync_live(symbol, direction, entry_price, row['id'], trigger_info)
                except:
                    pass

        self._last_trigger_time = datetime.utcnow()
        logger.info(f"[BTC动量] 完成，共开仓 {opened}/{len(top30)} 个交易对，4小时内不再触发")

    def check_and_execute(self):
        """
        主循环每分钟调用一次：
        1. 记录当前BTC价格
        2. 检测是否触发
        3. 触发则执行
        """
        btc_price = self._get_btc_current_price()
        if btc_price:
            self.record_btc_price(btc_price)

        result = self.check_trigger()
        if result:
            direction, window, pct = result
            self.execute(direction, window, pct)
