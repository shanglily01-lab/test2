#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
六策略量化交易服务
- S1: 早期做多  (未启动行情, 7天, 20%止盈, 无止损, 5x, 300U, 最多3单)
- S2: 无量回调做多 (4小时, 5%止盈, 2%止损, 10x, 300U, 最多3单)
- S3: 顶部做空  (3天, 15%止盈, 无止损, 5x, 300U, 最多3单)
- S4: 反弹动能衰竭做空 (5小时, 10%止盈, 3%止损, 5x, 300U, 最多3单)
- S5: 大币超卖反弹做多 (48小时, 5%止盈, 2%止损, 5x, 200U, 最多3单, 仅BTC/ETH/SOL/BNB/XRP)
- S6: 小币量能异动做多 (8小时, 8%止盈, 3%止损, 5x, 200U, 最多5单, 排除大市值)
- S7: 小币均线支撑反弹做多 (8小时, 6%止盈, 2.5%止损, 5x, 200U, 最多5单, 排除大市值)

调度方式: 在 smart_trader_service.py 主循环中调用
- run_fast(): 每5分钟, 负责 S2+S4+S7
- run_slow(): 每30分钟, 负责 S1+S3+S5+S6 (内部限速)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import pymysql
import pymysql.cursors
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, List, Tuple
from loguru import logger

from app.analyzers.technical_indicators import TechnicalIndicators


class MultiStrategyService:
    ACCOUNT_ID = 2  # 模拟 U 本位账号

    # 策略1: 早期做多
    S1_LEVERAGE = 5
    S1_MARGIN = 300
    S1_MAX_POSITIONS = 999  # 测试阶段不限制，上线后改回 3
    S1_TP_PCT = 0.20
    S1_HOLD_HOURS = 7 * 24
    S1_SOURCE = 's1_early_long'

    # 策略2: 无量回调做多
    S2_LEVERAGE = 10
    S2_MARGIN = 300
    S2_MAX_POSITIONS = 999  # 测试阶段不限制，上线后改回 3
    S2_TP_PCT = 0.05
    S2_SL_PCT = 0.02
    S2_HOLD_HOURS = 4
    S2_SOURCE = 's2_pullback_long'

    # 策略3: 顶部做空
    S3_LEVERAGE = 5
    S3_MARGIN = 300
    S3_MAX_POSITIONS = 999  # 测试阶段不限制，上线后改回 3
    S3_TP_PCT = 0.15
    S3_HOLD_HOURS = 3 * 24
    S3_SOURCE = 's3_top_short'

    # 策略4: 反弹动能衰竭做空
    S4_LEVERAGE = 5
    S4_MARGIN = 300
    S4_MAX_POSITIONS = 999  # 测试阶段不限制，上线后改回 3
    S4_TP_PCT = 0.10
    S4_SL_PCT = 0.03
    S4_HOLD_HOURS = 5
    S4_SOURCE = 's4_rebound_short'

    # 策略5: 大币4H超卖反弹做多 (BTC/ETH/SOL/BNB/XRP)
    S5_LEVERAGE = 5
    S5_MARGIN = 200
    S5_MAX_POSITIONS = 3
    S5_TP_PCT = 0.05
    S5_SL_PCT = 0.02
    S5_HOLD_HOURS = 48
    S5_SOURCE = 's5_large_oversold'
    S5_SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT']

    # 策略6: 小币量能异动做多 (排除大市值)
    S6_LEVERAGE = 5
    S6_MARGIN = 200
    S6_MAX_POSITIONS = 5
    S6_TP_PCT = 0.08
    S6_SL_PCT = 0.03
    S6_HOLD_HOURS = 8
    S6_SOURCE = 's6_vol_spike'
    # 排除大市值币种（它们的量能信号无效）
    S6_EXCLUDE_SYMBOLS = {
        'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
        'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'TRX/USDT', 'LINK/USDT',
        'TON/USDT', 'DOT/USDT', 'MATIC/USDT', 'SHIB/USDT', 'LTC/USDT',
        'UNI/USDT', 'ATOM/USDT', 'ETC/USDT', 'BCH/USDT', 'FIL/USDT',
    }

    # 策略7: 小币均线支撑反弹做多 (MA20下方82-95%区间反弹)
    S7_LEVERAGE = 5
    S7_MARGIN = 200
    S7_MAX_POSITIONS = 5
    S7_TP_PCT = 0.06
    S7_SL_PCT = 0.025
    S7_HOLD_HOURS = 8
    S7_SOURCE = 's7_ma_support'

    ALL_SOURCES = (S1_SOURCE, S2_SOURCE, S3_SOURCE, S4_SOURCE, S5_SOURCE, S6_SOURCE, S7_SOURCE)
    SLOW_SCAN_INTERVAL_SEC = 1800  # 30 分钟

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.ti = TechnicalIndicators()
        self._last_slow_scan: Optional[datetime] = None

    # ─────────────────────────────────────────
    # DB 工具
    # ─────────────────────────────────────────

    def _get_conn(self):
        return pymysql.connect(
            **self.db_config,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def _get_klines(self, symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
        """查询K线，返回标准化 DataFrame (columns: open/high/low/close/volume)"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT open_time, open_price, high_price, low_price, close_price, volume
                FROM kline_data
                WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures'
                ORDER BY open_time DESC LIMIT %s
            """, (symbol, timeframe, limit))
            rows = cur.fetchall()
            cur.close(); conn.close()

            if len(rows) < max(10, limit // 3):
                return None

            df = pd.DataFrame(rows)
            df = df.rename(columns={
                'open_price': 'open', 'high_price': 'high',
                'low_price': 'low', 'close_price': 'close',
            })
            for col in ('open', 'high', 'low', 'close', 'volume'):
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df = df.sort_values('open_time').reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"[多策略] K线查询失败 {symbol}/{timeframe}: {e}")
            return None

    def _get_current_price(self, symbol: str) -> Optional[float]:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT current_price FROM price_stats_24h WHERE symbol=%s LIMIT 1", (symbol,)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            return float(row['current_price']) if row else None
        except Exception as e:
            logger.warning(f"[多策略] 获取价格失败 {symbol}: {e}")
            return None

    def _get_big4_signal(self) -> str:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT overall_signal FROM big4_trend_history ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            return (row or {}).get('overall_signal', 'NEUTRAL')
        except Exception as e:
            logger.warning(f"[多策略] 获取Big4失败: {e}")
            return 'NEUTRAL'

    def _is_live_enabled(self) -> bool:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key='live_trading_enabled' LIMIT 1"
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            return str((row or {}).get('setting_value', '0')) in ('1', 'true')
        except Exception:
            return False

    def _get_candidate_symbols(self, min_abs_change: float = 5.0) -> List[str]:
        """从 price_stats_24h 过滤出有明显波动的 USDT 交易对"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol FROM price_stats_24h
                WHERE symbol LIKE '%%/USDT'
                  AND ABS(change_24h) >= %s
                ORDER BY ABS(change_24h) DESC
            """, (min_abs_change,))
            rows = cur.fetchall()
            cur.close(); conn.close()
            return [r['symbol'] for r in rows]
        except Exception as e:
            logger.warning(f"[多策略] 获取候选币种失败: {e}")
            return []

    def _strategy_position_count(self, source: str) -> int:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM futures_positions "
                "WHERE source=%s AND status='open' AND account_id=%s",
                (source, self.ACCOUNT_ID)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            return int((row or {}).get('cnt', 0))
        except Exception:
            return 0

    def _has_multi_strategy_position(self, symbol: str) -> bool:
        """该 symbol 是否已有四策略中任意一个持仓（防同 symbol 叠仓）"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            placeholders = ','.join(['%s'] * len(self.ALL_SOURCES))
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM futures_positions "
                f"WHERE symbol=%s AND source IN ({placeholders}) "
                f"AND status='open' AND account_id=%s",
                (symbol, *self.ALL_SOURCES, self.ACCOUNT_ID)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            return int((row or {}).get('cnt', 0)) > 0
        except Exception:
            return False

    # ─────────────────────────────────────────
    # 开仓
    # ─────────────────────────────────────────

    def _open_position(
        self,
        symbol: str,
        side: str,
        margin: float,
        leverage: int,
        tp_pct: Optional[float],
        sl_pct: Optional[float],
        hold_hours: float,
        source: str,
        reason: str,
    ) -> bool:
        """开模拟仓位；live_trading_enabled=1 时同步实盘"""
        try:
            price = self._get_current_price(symbol)
            if not price or price <= 0:
                return False

            notional = margin * leverage
            qty = round(notional / price, 6)
            planned_close = datetime.utcnow() + timedelta(hours=hold_hours)

            if side == 'LONG':
                tp_price = round(price * (1 + tp_pct), 8) if tp_pct else None
                sl_price = round(price * (1 - sl_pct), 8) if sl_pct else None
            else:
                tp_price = round(price * (1 - tp_pct), 8) if tp_pct else None
                sl_price = round(price * (1 + sl_pct), 8) if sl_pct else None

            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO futures_positions
                    (account_id, symbol, position_side, leverage, quantity, notional_value,
                     margin, entry_price, mark_price,
                     stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct,
                     status, source, entry_reason, open_time, planned_close_time,
                     unrealized_pnl, unrealized_pnl_pct)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,NOW(),%s,0,0)
            """, (
                self.ACCOUNT_ID, symbol, side, leverage, qty, round(notional, 2),
                margin, price, price,
                sl_price, tp_price,
                (sl_pct * 100) if sl_pct else None,
                (tp_pct * 100) if tp_pct else None,
                source, reason, planned_close,
            ))
            cur.close(); conn.close()

            sl_str = f"{sl_pct * 100:.1f}%" if sl_pct else "无"
            tp_str = f"{tp_pct * 100:.1f}%" if tp_pct else "无"
            logger.info(
                f"[{source}] 开仓 {symbol} {side} @ {price:.6g}  "
                f"SL={sl_str}  TP={tp_str}  持仓{hold_hours}H"
            )

            if self._is_live_enabled():
                self._sync_live(symbol, side, price, margin, leverage,
                                tp_pct, sl_pct, hold_hours, source)
            return True
        except Exception as e:
            logger.error(f"[{source}] 开仓失败 {symbol}: {e}")
            return False

    def _sync_live(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        margin: float,
        leverage: int,
        tp_pct: Optional[float],
        sl_pct: Optional[float],
        hold_hours: float,
        source: str,
    ):
        """同步实盘下单"""
        try:
            from app.services.api_key_service import APIKeyService
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            svc = APIKeyService(self.db_config)
            active_keys = svc.get_all_active_api_keys('binance')
        except Exception as e:
            logger.error(f"[{source}] 获取实盘账号失败: {e}")
            return

        for ak in active_keys:
            try:
                conn = self._get_conn()
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM live_futures_positions "
                    "WHERE account_id=%s AND status='OPEN'",
                    (ak['id'],)
                )
                live_cnt = int((cur.fetchone() or {}).get('cnt', 0))
                cur.close(); conn.close()
                if live_cnt >= 20:
                    continue

                notional = margin * leverage
                qty = Decimal(str(round(notional / entry_price, 6)))
                planned_close = datetime.utcnow() + timedelta(hours=hold_hours)
                engine = BinanceFuturesEngine(
                    self.db_config,
                    api_key=ak['api_key'],
                    api_secret=ak['api_secret'],
                )
                engine.open_position(
                    account_id=ak['id'],
                    symbol=symbol,
                    position_side=side,
                    quantity=qty,
                    leverage=leverage,
                    stop_loss_pct=sl_pct,
                    take_profit_pct=tp_pct,
                    source=source,
                    planned_close_time=planned_close,
                )
                logger.info(f"[{source}] 实盘下单: {ak['account_name']} {symbol} {side}")
            except Exception as e:
                logger.error(f"[{source}] 实盘下单失败 {ak.get('account_name', '?')}: {e}")

    # ─────────────────────────────────────────
    # 策略1: 早期做多
    # ─────────────────────────────────────────

    def scan_s1_early_long(self):
        """S1: RSI 25-52 上升 + 4H MACD连续向上(仍在低位) + 价格在MA20 80-105% + 量能回升"""
        if self._strategy_position_count(self.S1_SOURCE) >= self.S1_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 in ('BEARISH', 'STRONG_BEARISH'):
            logger.info("[S1] Big4 熊市，跳过")
            return

        symbols = self._get_candidate_symbols(min_abs_change=3.0)
        opened = 0

        for symbol in symbols:
            if self._strategy_position_count(self.S1_SOURCE) + opened >= self.S1_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                # 1H RSI 25-52，且最近2根在上升
                df_1h = self._get_klines(symbol, '1h', 30)
                if df_1h is None or len(df_1h) < 20:
                    continue
                rsi_series = self.ti.calculate_rsi(df_1h)
                if len(rsi_series) < 3:
                    continue
                last_rsi = float(rsi_series.iloc[-1])
                if not (25 <= last_rsi <= 52):
                    continue
                if float(rsi_series.iloc[-2]) >= last_rsi:
                    continue

                # 4H MACD histogram 连续2根向上，且前一根 < 0.002（还在低位）
                df_4h = self._get_klines(symbol, '4h', 40)
                if df_4h is None or len(df_4h) < 30:
                    continue
                _, _, hist_4h = self.ti.calculate_macd(df_4h)
                if len(hist_4h) < 3:
                    continue
                h1 = float(hist_4h.iloc[-3])
                h2 = float(hist_4h.iloc[-2])
                h3 = float(hist_4h.iloc[-1])
                if not (h2 > h1 and h3 > h2 and h1 < 0.002):
                    continue

                # 价格在MA20的80-105%（放宽区间）
                df_1d = self._get_klines(symbol, '1d', 25)
                if df_1d is None or len(df_1d) < 21:
                    continue
                ma20 = float(df_1d['close'].rolling(20).mean().iloc[-1])
                cur_price_d = float(df_1d['close'].iloc[-1])
                if not (ma20 * 0.80 <= cur_price_d <= ma20 * 1.05):
                    continue

                # 量能回升：今日量 > 近7日均量 × 0.9（放宽）
                vol_today = float(df_1d['volume'].iloc[-1])
                vol_avg7 = float(df_1d['volume'].iloc[-8:-1].mean()) if len(df_1d) >= 8 else vol_today
                if vol_avg7 > 0 and vol_today < vol_avg7 * 0.9:
                    continue

                reason = (
                    f"S1:1H_RSI={last_rsi:.1f}(上升),"
                    f"4H_MACD_hist={h1:.4f}->{h2:.4f}->{h3:.4f},"
                    f"价格={cur_price_d:.4g},MA20={ma20:.4g},"
                    f"量比={vol_today / vol_avg7:.2f}"
                )
                if self._open_position(
                    symbol, 'LONG', self.S1_MARGIN, self.S1_LEVERAGE,
                    self.S1_TP_PCT, None, self.S1_HOLD_HOURS, self.S1_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S1] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S1] 本轮新开 {opened} 单")

    # ─────────────────────────────────────────
    # 策略2: 无量回调做多
    # ─────────────────────────────────────────

    def scan_s2_pullback_long(self):
        """S2: 48H涨>12%后回调15-38%，15m RSI 30-58企稳做多（去掉无量条件）"""
        if self._strategy_position_count(self.S2_SOURCE) >= self.S2_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 in ('BEARISH', 'STRONG_BEARISH'):
            return

        symbols = self._get_candidate_symbols(min_abs_change=8.0)
        opened = 0

        for symbol in symbols:
            if self._strategy_position_count(self.S2_SOURCE) + opened >= self.S2_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                df_1h = self._get_klines(symbol, '1h', 52)
                if df_1h is None or len(df_1h) < 48:
                    continue

                closes = df_1h['close'].values
                recent_high = float(closes[-48:].max())
                recent_low = float(closes[-48:].min())
                current_close = float(closes[-1])

                # 48H价格区间 > 12%
                price_range_pct = (recent_high - recent_low) / recent_low if recent_low > 0 else 0
                if price_range_pct < 0.12:
                    continue

                # 从48H高点回调15-38%
                drawdown_pct = (recent_high - current_close) / recent_high if recent_high > 0 else 0
                if not (0.15 <= drawdown_pct <= 0.38):
                    continue

                # 15m RSI 30-58 且最近2根上升
                df_15m = self._get_klines(symbol, '15m', 30)
                if df_15m is None or len(df_15m) < 15:
                    continue
                rsi_15m = self.ti.calculate_rsi(df_15m)
                if len(rsi_15m) < 3:
                    continue
                last_rsi = float(rsi_15m.iloc[-1])
                prev_rsi = float(rsi_15m.iloc[-2])
                if not (30 <= last_rsi <= 58 and last_rsi > prev_rsi):
                    continue

                reason = (
                    f"S2:48H涨={price_range_pct * 100:.1f}%,"
                    f"回调={drawdown_pct * 100:.1f}%,"
                    f"15m_RSI={last_rsi:.1f}(上升中)"
                )
                if self._open_position(
                    symbol, 'LONG', self.S2_MARGIN, self.S2_LEVERAGE,
                    self.S2_TP_PCT, self.S2_SL_PCT, self.S2_HOLD_HOURS, self.S2_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S2] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S2] 本轮新开 {opened} 单")

    # ─────────────────────────────────────────
    # 策略3: 顶部做空
    # ─────────────────────────────────────────

    def scan_s3_top_short(self):
        """S3: RSI>=70顶背离 + 从高点回落3-15% + 4H MACD下行 + 近3根2根阴线 + 48H涨>20%"""
        if self._strategy_position_count(self.S3_SOURCE) >= self.S3_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 == 'STRONG_BULLISH':
            logger.info("[S3] Big4 强牛市，跳过顶部做空")
            return

        symbols = self._get_candidate_symbols(min_abs_change=15.0)
        opened = 0

        for symbol in symbols:
            if self._strategy_position_count(self.S3_SOURCE) + opened >= self.S3_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                df_1h = self._get_klines(symbol, '1h', 52)
                if df_1h is None or len(df_1h) < 40:
                    continue

                # 1H RSI > 70
                rsi_series = self.ti.calculate_rsi(df_1h)
                if len(rsi_series) < 6:
                    continue
                last_rsi = float(rsi_series.iloc[-1])
                if last_rsi <= 70:
                    continue

                # RSI顶背离：当前RSI不是最近6根的最高点
                recent_rsi_max = float(rsi_series.iloc[-6:].max())
                if last_rsi >= recent_rsi_max:
                    continue

                # 价格已从48H最高点回落3-15%（明显见顶迹象）
                current_price = float(df_1h['close'].iloc[-1])
                high_48h = float(df_1h['high'].iloc[-48:].max()) if len(df_1h) >= 48 else float(df_1h['high'].max())
                retreat_pct = (high_48h - current_price) / high_48h if high_48h > 0 else 0
                if not (0.03 <= retreat_pct <= 0.15):
                    continue

                # 48H涨幅 > 20%
                price_48h_ago = float(df_1h['close'].iloc[-48]) if len(df_1h) >= 48 else float(df_1h['close'].iloc[0])
                gain_48h = (high_48h - price_48h_ago) / price_48h_ago if price_48h_ago > 0 else 0
                if gain_48h <= 0.20:
                    continue

                # 4H MACD histogram 已经开始下行（多时间框架确认弱势）
                df_4h = self._get_klines(symbol, '4h', 40)
                if df_4h is None or len(df_4h) < 30:
                    continue
                _, _, hist_4h = self.ti.calculate_macd(df_4h)
                if len(hist_4h) < 3:
                    continue
                h4_1 = float(hist_4h.iloc[-3])
                h4_2 = float(hist_4h.iloc[-2])
                h4_3 = float(hist_4h.iloc[-1])
                if not (h4_3 < h4_2 or h4_2 < h4_1):
                    continue

                # 近3根1H K线至少2根阴线（顶部压力确认）
                bearish_count = sum(
                    1 for k in range(-3, 0)
                    if float(df_1h['close'].iloc[k]) < float(df_1h['open'].iloc[k])
                )
                if bearish_count < 2:
                    continue

                reason = (
                    f"S3:1H_RSI={last_rsi:.1f}(顶背离,max={recent_rsi_max:.1f}),"
                    f"从高点回落={retreat_pct * 100:.1f}%,"
                    f"4H_MACD下行,48H涨={gain_48h * 100:.1f}%"
                )
                if self._open_position(
                    symbol, 'SHORT', self.S3_MARGIN, self.S3_LEVERAGE,
                    self.S3_TP_PCT, None, self.S3_HOLD_HOURS, self.S3_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S3] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S3] 本轮新开 {opened} 单")

    # ─────────────────────────────────────────
    # 策略4: 反弹动能衰竭做空
    # ─────────────────────────────────────────

    def scan_s4_rebound_short(self):
        """S4: 14日高点50-85%反弹+曾下跌15%+从低点反弹5%，MACD/RSI/量能三选二"""
        if self._strategy_position_count(self.S4_SOURCE) >= self.S4_MAX_POSITIONS:
            return

        symbols = self._get_candidate_symbols(min_abs_change=8.0)
        opened = 0

        for symbol in symbols:
            if self._strategy_position_count(self.S4_SOURCE) + opened >= self.S4_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                # 取14日1H K线计算14日高点
                df_1h_14d = self._get_klines(symbol, '1h', 14 * 24 + 2)
                if df_1h_14d is None or len(df_1h_14d) < 52:
                    continue

                two_week_high = float(df_1h_14d['high'].max())
                current_price = float(df_1h_14d['close'].iloc[-1])

                # 当前价格在14日高点的50-85%
                rebound_pct = current_price / two_week_high if two_week_high > 0 else 0
                if not (0.50 <= rebound_pct <= 0.85):
                    continue

                # 曾经从14日高点下跌超过15%（有实质性回落，不是横盘）
                low_7d = float(df_1h_14d['low'].iloc[-7 * 24:].min())
                max_drop = (two_week_high - low_7d) / two_week_high if two_week_high > 0 else 0
                if max_drop < 0.15:
                    continue

                # 当前价格高于7日最低点×1.05（从低点已反弹5%+，有真正反弹）
                if current_price < low_7d * 1.05:
                    continue

                # 取最近52根K线做指标分析
                df_1h = df_1h_14d.iloc[-52:].reset_index(drop=True)

                # 条件1: MACD histogram 任意一段下降
                _, _, hist_1h = self.ti.calculate_macd(df_1h)
                macd_bearish = False
                if len(hist_1h) >= 3:
                    h1 = float(hist_1h.iloc[-3])
                    h2 = float(hist_1h.iloc[-2])
                    h3 = float(hist_1h.iloc[-1])
                    if h3 < h2 or h2 < h1:
                        macd_bearish = True

                # 条件2: RSI < 65 且最后一根在下降
                rsi_1h = self.ti.calculate_rsi(df_1h)
                rsi_bearish = False
                r3 = 50.0
                if len(rsi_1h) >= 3:
                    r2 = float(rsi_1h.iloc[-2])
                    r3 = float(rsi_1h.iloc[-1])
                    if r3 < 65 and r3 < r2:
                        rsi_bearish = True

                # 条件3: 上涨K均量 < 下跌K均量（量能萎缩）
                vol_shrink = False
                closes = df_1h['close'].values
                vols = df_1h['volume'].values
                up_vols = [vols[j] for j in range(-10, -1) if closes[j] > closes[j - 1]]
                dn_vols = [vols[j] for j in range(-10, -1) if closes[j] < closes[j - 1]]
                if len(up_vols) >= 2 and len(dn_vols) >= 2:
                    avg_up = sum(up_vols) / len(up_vols)
                    avg_dn = sum(dn_vols) / len(dn_vols)
                    if avg_dn > 0 and avg_up < avg_dn:
                        vol_shrink = True

                # 三选二
                if sum([macd_bearish, rsi_bearish, vol_shrink]) < 2:
                    continue

                reason = (
                    f"S4:14日高={two_week_high:.4g},反弹={rebound_pct * 100:.1f}%,"
                    f"1H_RSI={r3:.1f},"
                    f"MACD={'空' if macd_bearish else '-'},"
                    f"量萎缩={'是' if vol_shrink else '-'}"
                )
                if self._open_position(
                    symbol, 'SHORT', self.S4_MARGIN, self.S4_LEVERAGE,
                    self.S4_TP_PCT, self.S4_SL_PCT, self.S4_HOLD_HOURS, self.S4_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S4] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S4] 本轮新开 {opened} 单")

    # ─────────────────────────────────────────
    # 策略5: 大币4H超卖反弹做多
    # ─────────────────────────────────────────

    def scan_s5_large_oversold(self):
        """S5: BTC/ETH/SOL/BNB/XRP 的 4H RSI<32 + 价格低于日MA20 做多"""
        if self._strategy_position_count(self.S5_SOURCE) >= self.S5_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 in ('STRONG_BEARISH',):
            logger.info("[S5] Big4强熊市，跳过大币超卖")
            return

        opened = 0
        for symbol in self.S5_SYMBOLS:
            if self._strategy_position_count(self.S5_SOURCE) + opened >= self.S5_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                # 4H RSI < 32（深度超卖）
                df_4h = self._get_klines(symbol, '4h', 40)
                if df_4h is None or len(df_4h) < 20:
                    continue
                rsi_4h = self.ti.calculate_rsi(df_4h)
                if len(rsi_4h) < 4:
                    continue
                prev_rsi_4h = float(rsi_4h.iloc[-2])
                last_rsi_4h = float(rsi_4h.iloc[-1])
                if last_rsi_4h >= 32:
                    continue
                if last_rsi_4h <= prev_rsi_4h:  # RSI仍在下降，不开仓
                    continue

                # 价格低于日线MA20（处于均线下方，有均值回归空间）
                df_1d = self._get_klines(symbol, '1d', 25)
                if df_1d is None or len(df_1d) < 21:
                    continue
                ma20_1d = float(df_1d['close'].rolling(20).mean().iloc[-1])
                cur_price = float(df_1d['close'].iloc[-1])
                if cur_price >= ma20_1d:
                    continue

                price_vs_ma = cur_price / ma20_1d

                reason = (
                    f"S5:4H_RSI={prev_rsi_4h:.1f}->{last_rsi_4h:.1f}(超卖回升),"
                    f"价格={cur_price:.4g},日MA20={ma20_1d:.4g}({price_vs_ma * 100:.1f}%)"
                )
                if self._open_position(
                    symbol, 'LONG', self.S5_MARGIN, self.S5_LEVERAGE,
                    self.S5_TP_PCT, self.S5_SL_PCT, self.S5_HOLD_HOURS, self.S5_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S5] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S5] 本轮新开 {opened} 单")

    # ─────────────────────────────────────────
    # 策略6: 小币量能异动做多
    # ─────────────────────────────────────────

    def _get_small_cap_symbols(self) -> List[str]:
        """从 price_stats_24h 获取小中市值 USDT 交易对（排除大市值）"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT symbol FROM price_stats_24h WHERE symbol LIKE '%/USDT'"
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            return [
                r['symbol'] for r in rows
                if r['symbol'] not in self.S6_EXCLUDE_SYMBOLS
            ]
        except Exception as e:
            logger.warning(f"[S6] 获取小币列表失败: {e}")
            return []

    def scan_s6_vol_spike(self):
        """S6: 12H量峰>3.5x均量 + 当前量1.2-5x + RSI 28-55 + 价格在MA20 75-108% + 3H涨<5%"""
        if self._strategy_position_count(self.S6_SOURCE) >= self.S6_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 in ('BEARISH', 'STRONG_BEARISH'):
            logger.info("[S6] Big4熊市，跳过量能异动做多")
            return

        symbols = self._get_small_cap_symbols()
        opened = 0

        for symbol in symbols:
            if self._strategy_position_count(self.S6_SOURCE) + opened >= self.S6_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                df_1h = self._get_klines(symbol, '1h', 50)
                if df_1h is None or len(df_1h) < 35:
                    continue

                # 量能基准：过去48根的均量（约48H均量）
                vol_base = float(df_1h['volume'].iloc[-49:-1].mean()) if len(df_1h) >= 49 else float(df_1h['volume'].iloc[:-1].mean())
                if vol_base <= 0:
                    continue

                # 当前量比 1.2-5x
                cur_vol = float(df_1h['volume'].iloc[-1])
                vol_ratio_cur = cur_vol / vol_base
                if not (1.2 <= vol_ratio_cur <= 5.0):
                    continue

                # 12H内量峰 > 3.5x（先有异动）
                max_vol_12h = float(df_1h['volume'].iloc[-12:].max())
                peak_ratio = max_vol_12h / vol_base
                if peak_ratio < 3.5:
                    continue

                # 1H RSI 28-55
                rsi_1h = self.ti.calculate_rsi(df_1h)
                if len(rsi_1h) < 3:
                    continue
                last_rsi = float(rsi_1h.iloc[-1])
                if not (28 <= last_rsi <= 55):
                    continue

                # 价格在MA20的75-108%（量能异动时价格仍在合理区间）
                ma20 = float(df_1h['close'].rolling(20).mean().iloc[-1])
                if ma20 <= 0:
                    continue
                close_v = float(df_1h['close'].iloc[-1])
                price_ratio = close_v / ma20
                if not (0.75 <= price_ratio <= 1.08):
                    continue

                # 过去3H涨幅 < 5%（避免追高）
                prev_3h = float(df_1h['close'].iloc[-4]) if len(df_1h) >= 4 else close_v
                gain_3h = (close_v - prev_3h) / prev_3h if prev_3h > 0 else 0
                if gain_3h > 0.05:
                    continue

                reason = (
                    f"S6:量峰={peak_ratio:.1f}x,当前量={vol_ratio_cur:.1f}x,"
                    f"1H_RSI={last_rsi:.1f},价格/MA20={price_ratio * 100:.1f}%,"
                    f"3H涨={gain_3h * 100:.1f}%"
                )
                if self._open_position(
                    symbol, 'LONG', self.S6_MARGIN, self.S6_LEVERAGE,
                    self.S6_TP_PCT, self.S6_SL_PCT, self.S6_HOLD_HOURS, self.S6_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S6] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S6] 本轮新开 {opened} 单")

    # ─────────────────────────────────────────
    # 策略7: 小币均线支撑反弹做多
    # ─────────────────────────────────────────

    def scan_s7_ma_support(self):
        """S7: 价格跌至20H均线82-95%区间 + 反弹阳线 + 量能确认"""
        if self._strategy_position_count(self.S7_SOURCE) >= self.S7_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 in ('BEARISH', 'STRONG_BEARISH'):
            logger.info("[S7] Big4熊市，跳过均线支撑反弹")
            return

        symbols = self._get_small_cap_symbols()
        opened = 0

        for symbol in symbols:
            if self._strategy_position_count(self.S7_SOURCE) + opened >= self.S7_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                df_1h = self._get_klines(symbol, '1h', 30)
                if df_1h is None or len(df_1h) < 22:
                    continue

                # 20H简单均线
                ma20 = float(df_1h['close'].iloc[-21:-1].mean())
                if ma20 <= 0:
                    continue

                close_v = float(df_1h['close'].iloc[-1])
                open_v = float(df_1h['open'].iloc[-1])
                prev_close = float(df_1h['close'].iloc[-2])

                # 价格在MA20的82-95%区间
                ratio = close_v / ma20
                if not (0.82 <= ratio <= 0.95):
                    continue

                # 当前是阳线且高于前一根收盘（反弹确认）
                if not (close_v > open_v and close_v > prev_close):
                    continue

                # 量能确认：当前量 > 近10根均量×1.2
                vol_base = float(df_1h['volume'].iloc[-11:-1].mean())
                cur_vol = float(df_1h['volume'].iloc[-1])
                if vol_base > 0 and cur_vol < vol_base * 1.2:
                    continue

                vol_ratio = cur_vol / vol_base if vol_base > 0 else 1.0
                reason = (
                    f"S7:价格/20H_MA={ratio * 100:.1f}%,"
                    f"阳线反弹,量比={vol_ratio:.2f}x"
                )
                if self._open_position(
                    symbol, 'LONG', self.S7_MARGIN, self.S7_LEVERAGE,
                    self.S7_TP_PCT, self.S7_SL_PCT, self.S7_HOLD_HOURS, self.S7_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S7] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S7] 本轮新开 {opened} 单")

    # ─────────────────────────────────────────
    # 调度入口
    # ─────────────────────────────────────────

    def run_fast(self):
        """每5分钟调度：S2 + S4 + S7"""
        try:
            self.scan_s2_pullback_long()
        except Exception as e:
            logger.error(f"[S2] 扫描异常: {e}")
        try:
            self.scan_s4_rebound_short()
        except Exception as e:
            logger.error(f"[S4] 扫描异常: {e}")
        try:
            self.scan_s7_ma_support()
        except Exception as e:
            logger.error(f"[S7] 扫描异常: {e}")

    def run_slow(self):
        """每30分钟调度：S1+S3+S5+S6；内部限速防重复"""
        now = datetime.utcnow()
        if (self._last_slow_scan and
                (now - self._last_slow_scan).total_seconds() < self.SLOW_SCAN_INTERVAL_SEC):
            return
        self._last_slow_scan = now
        logger.info("[多策略] S1+S3+S5+S6 慢速扫描开始")
        try:
            self.scan_s1_early_long()
        except Exception as e:
            logger.error(f"[S1] 扫描异常: {e}")
        try:
            self.scan_s3_top_short()
        except Exception as e:
            logger.error(f"[S3] 扫描异常: {e}")
        try:
            self.scan_s5_large_oversold()
        except Exception as e:
            logger.error(f"[S5] 扫描异常: {e}")
        try:
            self.scan_s6_vol_spike()
        except Exception as e:
            logger.error(f"[S6] 扫描异常: {e}")
