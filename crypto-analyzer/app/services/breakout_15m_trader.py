#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
15M背离突破策略
- 震荡市中，当15M出现价格突破+RSI拐头+成交量放大时，提前入场
- 只跑模拟盘（account_id=2），不同步实盘
- 止损2%，止盈5%，每交易对4小时冷却，最多同时10单
"""

import pymysql
import pymysql.cursors
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from loguru import logger


class Breakout15MTrader:
    MARGIN = 100
    LEVERAGE = 5
    SL_PCT = 0.02       # 2%
    TP_PCT = 0.05       # 5%
    ACCOUNT_ID = 2
    MAX_POSITIONS = 10
    COOLDOWN_HOURS = 4

    def __init__(self, db_config: dict, ws_price_service=None):
        self.db_config = db_config
        self.ws_service = ws_price_service

    def _get_conn(self):
        return pymysql.connect(
            **self.db_config, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, autocommit=True
        )

    # ──────────────────────────────────────────
    # 指标计算
    # ──────────────────────────────────────────

    def _calc_rsi(self, closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        gains, losses = [], []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        ag = sum(gains[-period:]) / period
        al = sum(losses[-period:]) / period
        if al == 0:
            return 100.0
        return 100 - 100 / (1 + ag / al)

    # ──────────────────────────────────────────
    # 数据获取
    # ──────────────────────────────────────────

    def _fetch_15m(self, cursor, symbol: str, limit: int = 20) -> List[Dict]:
        cursor.execute(
            "SELECT open_price, high_price, low_price, close_price, volume "
            "FROM kline_data "
            "WHERE symbol=%s AND timeframe='15m' AND exchange='binance_futures' "
            "ORDER BY open_time DESC LIMIT %s",
            (symbol, limit)
        )
        rows = cursor.fetchall()
        return list(reversed(rows))  # 时间从旧到新

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """WS实时价优先，fallback kline 5m（30分钟内有效）"""
        if self.ws_service:
            p = self.ws_service.get_price(symbol)
            if p and float(p) > 0:
                return float(p)
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            for tf in ('5m', '15m'):
                cur.execute(
                    "SELECT close_price, open_time FROM kline_data "
                    "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
                    "ORDER BY open_time DESC LIMIT 1",
                    (symbol, tf)
                )
                row = cur.fetchone()
                if row:
                    age = (datetime.utcnow().timestamp() - row['open_time'] / 1000) / 60
                    if age <= 30:
                        cur.close(); conn.close()
                        return float(row['close_price'])
            cur.close(); conn.close()
        except Exception:
            pass
        return None

    # ──────────────────────────────────────────
    # 信号检测
    # ──────────────────────────────────────────

    def _check_signal(self, cursor, symbol: str) -> Optional[str]:
        """
        检查15M突破信号，返回 'LONG' / 'SHORT' / None
        三个条件同时满足才触发：
        1. 价格突破近8根15M高点（做多）或破低点（做空）
        2. RSI(14) > 50 且比4根前上升≥3（做多），反之做空
        3. 最新1根成交量 > 前8根均量 × 1.5
        """
        k15 = self._fetch_15m(cursor, symbol, 20)
        if len(k15) < 15:
            return None

        closes = [float(k['close_price']) for k in k15]
        highs  = [float(k['high_price'])  for k in k15]
        lows   = [float(k['low_price'])   for k in k15]
        vols   = [float(k['volume'])      for k in k15]

        current = self._get_current_price(symbol) or closes[-1]

        # 1. 突破：前8根（不含最新K线）的高低点
        recent_high = max(highs[-9:-1])
        recent_low  = min(lows[-9:-1])

        # 2. RSI
        rsi_now  = self._calc_rsi(closes, 14)
        rsi_prev = self._calc_rsi(closes[:-4], 14)  # 4根K线前的RSI

        # 3. 成交量
        vol_now = vols[-1]
        vol_avg = sum(vols[-9:-1]) / 8 if len(vols) >= 9 else vol_now

        # 判断
        breakout_long  = current > recent_high
        breakout_short = current < recent_low
        rsi_long  = rsi_now > 50 and (rsi_now - rsi_prev) >= 3
        rsi_short = rsi_now < 50 and (rsi_prev - rsi_now) >= 3
        vol_ok    = vol_avg > 0 and vol_now > vol_avg * 1.5

        if breakout_long and rsi_long and vol_ok:
            return 'LONG'
        elif breakout_short and rsi_short and vol_ok:
            return 'SHORT'
        return None

    def _has_cooldown(self, cursor, symbol: str, direction: str) -> bool:
        """4小时内是否已有同向仓"""
        cursor.execute(
            "SELECT COUNT(*) as cnt FROM futures_positions "
            "WHERE source='15M_BREAKOUT' AND symbol=%s AND position_side=%s "
            "AND open_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)",
            (symbol, direction, self.COOLDOWN_HOURS)
        )
        row = cursor.fetchone()
        return (row['cnt'] if row else 0) > 0

    # ──────────────────────────────────────────
    # 开仓
    # ──────────────────────────────────────────

    def _is_live_enabled(self) -> bool:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT setting_value FROM system_settings WHERE setting_key='live_trading_enabled'")
            row = cur.fetchone()
            cur.close(); conn.close()
            return row and str(row['setting_value']) in ('1', 'true')
        except Exception:
            return False

    def _is_in_top50(self, symbol: str) -> bool:
        """检查交易对是否在 TOP50 盈利列表中"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM top_performing_symbols WHERE symbol=%s", (symbol,))
            row = cur.fetchone()
            cur.close(); conn.close()
            return (row['cnt'] if row else 0) > 0
        except Exception as e:
            logger.warning(f"[15M突破] 检查TOP50失败: {e}")
            return False

    def _get_big4_signal(self):
        """返回 (signal, strength)，失败返回 (None, 0)"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT overall_signal, signal_strength FROM big4_trend_history "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if row:
                return row['overall_signal'], float(row['signal_strength'] or 0)
        except Exception as e:
            logger.warning(f"[15M突破] 读取Big4失败: {e}")
        return None, 0

    def _sync_live(self, symbol: str, direction: str, entry_price: float,
                   paper_pos_id: int, reason: str):
        """同步到实盘账号（调用交易引擎真实下单）"""
        if not self._is_live_enabled():
            return
        if not self._is_in_top50(symbol):
            logger.debug(f"[15M突破] {symbol} 不在TOP50，跳过实盘同步")
            return

        # Big4 方向过滤：熊市禁止做多，牛市禁止做空
        b4_signal, b4_strength = self._get_big4_signal()
        if b4_signal:
            if direction == 'LONG' and b4_signal in ('BEARISH', 'STRONG_BEARISH') and b4_strength >= 50:
                logger.warning(f"[15M突破-BIG4-BLOCK] {symbol} Big4={b4_signal}({b4_strength:.1f}) 熊市禁止实盘做多")
                return
            if direction == 'SHORT' and b4_signal in ('BULLISH', 'STRONG_BULLISH') and b4_strength >= 50:
                logger.warning(f"[15M突破-BIG4-BLOCK] {symbol} Big4={b4_signal}({b4_strength:.1f}) 牛市禁止实盘做空")
                return

        try:
            from app.services.api_key_service import APIKeyService
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            svc = APIKeyService(self.db_config)
            active_keys = svc.get_all_active_api_keys('binance')
        except Exception as e:
            logger.error(f"[15M突破] 获取实盘账号失败: {e}")
            return

        for ak in active_keys:
            try:
                margin = float(ak.get('max_position_value') or self.MARGIN)
                lev = int(ak.get('max_leverage') or self.LEVERAGE)
                notional = margin * lev
                qty = Decimal(str(round(notional / entry_price, 6)))

                engine = BinanceFuturesEngine(
                    self.db_config,
                    api_key=ak['api_key'],
                    api_secret=ak['api_secret']
                )
                result = engine.open_position(
                    account_id=ak['id'],
                    symbol=symbol,
                    position_side=direction,
                    quantity=qty,
                    leverage=lev,
                    stop_loss_pct=Decimal(str(self.SL_PCT * 100)),
                    take_profit_pct=Decimal(str(self.TP_PCT * 100)),
                    source='15M_BREAKOUT',
                    paper_position_id=paper_pos_id
                )
                if result.get('success'):
                    logger.info(f"[15M突破] 实盘下单成功 {ak['account_name']} {symbol} {direction}")
                    try:
                        from app.services.trade_notifier import get_trade_notifier
                        notifier = get_trade_notifier()
                        if notifier:
                            sl = round(entry_price * (1 - self.SL_PCT), 4) if direction == 'LONG' \
                                else round(entry_price * (1 + self.SL_PCT), 4)
                            tp = round(entry_price * (1 + self.TP_PCT), 4) if direction == 'LONG' \
                                else round(entry_price * (1 - self.TP_PCT), 4)
                            notifier.notify_open_position(
                                symbol=symbol, direction=direction,
                                quantity=float(qty), entry_price=entry_price,
                                leverage=lev, stop_loss_price=sl, take_profit_price=tp,
                                margin=margin,
                                strategy_name=f'15M突破[{ak["account_name"]}] {reason}'
                            )
                    except Exception:
                        pass
                else:
                    logger.error(f"[15M突破] 实盘下单失败 {ak['account_name']} {symbol}: {result.get('error','')}")
            except Exception as e:
                logger.error(f"[15M突破] 实盘下单异常 {ak.get('account_name','')} {symbol}: {e}")

    def _open_position(self, symbol: str, direction: str,
                        entry_price: float, reason: str) -> int:
        """开模拟盘仓位，返回插入的行 ID（失败返回 0）"""
        try:
            notional = self.MARGIN * self.LEVERAGE
            qty = round(notional / entry_price, 6)
            if direction == 'LONG':
                sl = round(entry_price * (1 - self.SL_PCT), 8)
                tp = round(entry_price * (1 + self.TP_PCT), 8)
            else:
                sl = round(entry_price * (1 + self.SL_PCT), 8)
                tp = round(entry_price * (1 - self.TP_PCT), 8)

            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO futures_positions
                    (account_id, symbol, position_side, leverage, quantity, notional_value,
                     margin, entry_price, mark_price, stop_loss_price, take_profit_price,
                     stop_loss_pct, take_profit_pct, status, source, entry_reason, open_time,
                     unrealized_pnl, unrealized_pnl_pct)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open','15M_BREAKOUT',%s,NOW(),0,0)
            """, (
                self.ACCOUNT_ID, symbol, direction, self.LEVERAGE, qty,
                round(notional, 2), self.MARGIN, entry_price, entry_price,
                sl, tp, self.SL_PCT * 100, self.TP_PCT * 100, reason
            ))
            new_id = cur.lastrowid
            cur.close(); conn.close()
            logger.info(f"[15M突破] 开仓 {symbol} {direction} @ {entry_price:.6g}  SL={sl:.6g}  TP={tp:.6g}")
            return new_id or 0
        except Exception as e:
            logger.error(f"[15M突破] 开仓失败 {symbol}: {e}")
            return 0

    # ──────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────

    def _get_banned_symbols(self, cursor) -> set:
        """获取 rating_level=3 永久禁止的交易对"""
        try:
            cursor.execute(
                "SELECT symbol FROM trading_symbol_rating WHERE rating_level >= 3"
            )
            return {r['symbol'] for r in cursor.fetchall()}
        except Exception as e:
            logger.warning(f"[15M突破] 获取黑名单3级失败: {e}")
            return set()

    def run(self, symbols: List[str]) -> int:
        """扫描所有交易对，开满足条件的仓位，返回本轮开仓数"""
        conn = self._get_conn()
        cur = conn.cursor()

        # 检查系统开关
        try:
            cur.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key='breakout_15m_enabled'"
            )
            row = cur.fetchone()
            if row and str(row['setting_value']) in ('0', 'false', 'False'):
                logger.info("[15M突破] breakout_15m_enabled=0，本轮跳过")
                cur.close(); conn.close()
                return 0
        except Exception as e:
            logger.warning(f"[15M突破] 读取breakout_15m_enabled失败，默认继续: {e}")

        banned = self._get_banned_symbols(cur)

        # 检查当前持仓数
        cur.execute(
            "SELECT COUNT(*) as cnt FROM futures_positions "
            "WHERE source='15M_BREAKOUT' AND status='open' AND account_id=%s",
            (self.ACCOUNT_ID,)
        )
        row = cur.fetchone()
        current_count = row['cnt'] if row else 0
        if current_count >= self.MAX_POSITIONS:
            logger.debug(f"[15M突破] 已有{current_count}单达上限({self.MAX_POSITIONS})，跳过")
            cur.close(); conn.close()
            return 0

        slots = self.MAX_POSITIONS - current_count
        opened = 0

        for symbol in symbols:
            if opened >= slots:
                break
            if symbol in banned:
                logger.debug(f"[15M突破] {symbol} Level3禁止，跳过")
                continue
            try:
                direction = self._check_signal(cur, symbol)
                if not direction:
                    continue
                if self._has_cooldown(cur, symbol, direction):
                    logger.debug(f"[15M突破] {symbol} {direction} 冷却中，跳过")
                    continue
                # Big4 方向过滤
                b4_sig, b4_str = self._get_big4_signal()
                if b4_sig:
                    if direction == 'LONG' and b4_sig in ('BEARISH', 'STRONG_BEARISH') and b4_str >= 50:
                        logger.debug(f"[15M突破] {symbol} Big4熊市({b4_sig})，跳过做多")
                        continue
                    if direction == 'SHORT' and b4_sig in ('BULLISH', 'STRONG_BULLISH') and b4_str >= 50:
                        logger.debug(f"[15M突破] {symbol} Big4牛市({b4_sig})，跳过做空")
                        continue
                entry_price = self._get_current_price(symbol)
                if not entry_price:
                    logger.warning(f"[15M突破] {symbol} 取不到价格，跳过")
                    continue
                reason = f"15M突破 {direction} RSI拐头+量能放大"
                paper_id = self._open_position(symbol, direction, entry_price, reason)
                if paper_id:
                    opened += 1
                    self._sync_live(symbol, direction, entry_price, paper_id, reason)
            except Exception as e:
                logger.error(f"[15M突破] {symbol} 处理异常: {e}")

        cur.close(); conn.close()
        if opened:
            logger.info(f"[15M突破] 本轮开仓 {opened} 单，当前共 {current_count + opened} 单")
        return opened
