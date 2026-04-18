#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四策略量化交易服务
- S1: 早期做多  (未启动行情, 7天, 20%止盈, 无止损, 5x, 300U, 最多3单)
- S2: 无量回调做多 (4小时, 5%止盈, 2%止损, 10x, 300U, 最多3单)
- S3: 顶部做空  (3天, 15%止盈, 无止损, 5x, 300U, 最多3单)
- S4: 反弹动能衰竭做空 (5小时, 10%止盈, 3%止损, 5x, 300U, 最多3单)

调度方式: 在 smart_trader_service.py 主循环中调用
- run_fast(): 每5分钟, 负责 S2+S4
- run_slow(): 每30分钟, 负责 S1+S3 (内部限速)
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

    ALL_SOURCES = (S1_SOURCE, S2_SOURCE, S3_SOURCE, S4_SOURCE)
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
        """S1: 发现未启动的做多信号 — 1H RSI超卖回升 + 4H MACD金叉 + 价格在20日MA下方"""
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
                # 1H RSI 28-45：超卖区刚回升，行情尚未启动
                df_1h = self._get_klines(symbol, '1h', 30)
                if df_1h is None or len(df_1h) < 20:
                    continue
                rsi_series = self.ti.calculate_rsi(df_1h)
                last_rsi = float(rsi_series.iloc[-1])
                if not (28 <= last_rsi <= 45):
                    continue

                # 4H MACD histogram 由负转正（金叉初期）
                df_4h = self._get_klines(symbol, '4h', 40)
                if df_4h is None or len(df_4h) < 35:
                    continue
                _, _, hist_4h = self.ti.calculate_macd(df_4h)
                prev_hist = float(hist_4h.iloc[-2])
                last_hist = float(hist_4h.iloc[-1])
                if not (prev_hist < 0 and last_hist > -0.000001):
                    continue

                # 价格在20日均线下方 0-5% 之间（未突破但接近）
                df_1d = self._get_klines(symbol, '1d', 25)
                if df_1d is None or len(df_1d) < 21:
                    continue
                ma20 = float(df_1d['close'].rolling(20).mean().iloc[-1])
                cur_price_d = float(df_1d['close'].iloc[-1])
                if not (ma20 * 0.95 <= cur_price_d <= ma20 * 1.01):
                    continue

                # 量能开始回升：今日量 > 近7日均量 × 1.1
                vol_today = float(df_1d['volume'].iloc[-1])
                vol_avg7 = float(df_1d['volume'].iloc[-8:-1].mean()) if len(df_1d) >= 8 else vol_today
                if vol_avg7 > 0 and vol_today < vol_avg7 * 1.1:
                    continue

                reason = (
                    f"S1:1H_RSI={last_rsi:.1f},"
                    f"4H_MACD_hist={prev_hist:.4f}->{last_hist:.4f},"
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
        """S2: 无量上涨后回调 25-40%，15m RSI企稳回升时做多"""
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
                # 48根1H K线（覆盖48小时）
                df_1h = self._get_klines(symbol, '1h', 52)
                if df_1h is None or len(df_1h) < 48:
                    continue

                closes = df_1h['close'].values
                volumes = df_1h['volume'].values
                recent_high = float(closes[-48:].max())
                recent_low = float(closes[-48:].min())
                current_close = float(closes[-1])

                # 48小时曾有 >15% 涨幅
                price_range_pct = (recent_high - recent_low) / recent_low if recent_low > 0 else 0
                if price_range_pct < 0.15:
                    continue

                # 当前已从高点回调 25-40%
                drawdown_pct = (recent_high - current_close) / recent_high if recent_high > 0 else 0
                if not (0.25 <= drawdown_pct <= 0.40):
                    continue

                # 上涨期间成交量 < 7日均量/24×1.2（无量特征）
                df_1d = self._get_klines(symbol, '1d', 10)
                if df_1d is None or len(df_1d) < 7:
                    continue
                vol_avg7d_daily = float(df_1d['volume'].iloc[-8:-1].mean()) if len(df_1d) >= 8 else float(df_1d['volume'].mean())
                vol_avg7d_hourly = vol_avg7d_daily / 24

                # 上涨阶段：高点前12根1H的平均量
                high_idx = int(df_1h['close'].iloc[-48:].values.argmax())
                start_idx = max(0, high_idx - 12)
                rise_vols = volumes[-48 + start_idx: -48 + high_idx] if high_idx > 0 else volumes[-24:-12]
                avg_rise_vol = float(rise_vols.mean()) if len(rise_vols) > 0 else vol_avg7d_hourly
                if vol_avg7d_hourly > 0 and avg_rise_vol > vol_avg7d_hourly * 1.2:
                    continue  # 上涨放量，不符合无量特征

                # 15m RSI 35-52 且最近3根上升
                df_15m = self._get_klines(symbol, '15m', 30)
                if df_15m is None or len(df_15m) < 20:
                    continue
                rsi_15m = self.ti.calculate_rsi(df_15m)
                last_rsi = float(rsi_15m.iloc[-1])
                if not (35 <= last_rsi <= 52):
                    continue
                r1, r2, r3 = float(rsi_15m.iloc[-3]), float(rsi_15m.iloc[-2]), float(rsi_15m.iloc[-1])
                if not (r1 < r2 < r3):
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
        """S3: 1H RSI>75 + 价格破布林上轨 + 48H涨幅>25% → 顶部做空"""
        if self._strategy_position_count(self.S3_SOURCE) >= self.S3_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 == 'STRONG_BULLISH':
            logger.info("[S3] Big4 强牛市，跳过顶部做空")
            return

        # 只扫描24h大涨的币
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

                # 1H RSI > 75
                rsi_series = self.ti.calculate_rsi(df_1h)
                last_rsi = float(rsi_series.iloc[-1])
                if last_rsi <= 75:
                    continue

                # 价格在布林带上轨以上
                upper, _, _ = self.ti.calculate_bollinger_bands(df_1h)
                current_price = float(df_1h['close'].iloc[-1])
                last_upper = float(upper.iloc[-1])
                if current_price <= last_upper:
                    continue

                # 48小时涨幅 > 25%
                price_48h_ago = float(df_1h['close'].iloc[-48]) if len(df_1h) >= 48 else float(df_1h['close'].iloc[0])
                gain_48h = (current_price - price_48h_ago) / price_48h_ago if price_48h_ago > 0 else 0
                if gain_48h <= 0.25:
                    continue

                reason = (
                    f"S3:1H_RSI={last_rsi:.1f},"
                    f"价格={current_price:.4g},布林上轨={last_upper:.4g},"
                    f"48H涨={gain_48h * 100:.1f}%"
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
        """S4: 反弹到7日高点62-82%，MACD+RSI顶背离，量能萎缩 → 做空"""
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
                # 7日高点
                df_1d = self._get_klines(symbol, '1d', 10)
                if df_1d is None or len(df_1d) < 7:
                    continue
                week_high = float(df_1d['high'].iloc[-7:].max())
                current_price = float(df_1d['close'].iloc[-1])

                # 当前价在7日高点的 62-82%
                rebound_pct = current_price / week_high if week_high > 0 else 0
                if not (0.62 <= rebound_pct <= 0.82):
                    continue

                # 1H K线：MACD + RSI + 量能
                df_1h = self._get_klines(symbol, '1h', 52)
                if df_1h is None or len(df_1h) < 30:
                    continue

                # MACD histogram 最近3根下降
                _, _, hist_1h = self.ti.calculate_macd(df_1h)
                h1 = float(hist_1h.iloc[-3])
                h2 = float(hist_1h.iloc[-2])
                h3 = float(hist_1h.iloc[-1])
                if not (h1 > h2 > h3):
                    continue

                # RSI < 62 且下降
                rsi_1h = self.ti.calculate_rsi(df_1h)
                r1 = float(rsi_1h.iloc[-3])
                r2 = float(rsi_1h.iloc[-2])
                r3 = float(rsi_1h.iloc[-1])
                if r3 >= 62 or not (r1 > r2 > r3):
                    continue

                # 反弹量能萎缩：近期上涨K均量 < 近期下跌K均量 × 0.75
                closes = df_1h['close'].values
                vols = df_1h['volume'].values
                up_vols = [vols[i] for i in range(-9, -1) if closes[i] > closes[i - 1]]
                dn_vols = [vols[i] for i in range(-9, -1) if closes[i] < closes[i - 1]]
                if len(up_vols) >= 2 and len(dn_vols) >= 2:
                    avg_up = sum(up_vols[-3:]) / len(up_vols[-3:])
                    avg_dn = sum(dn_vols[-3:]) / len(dn_vols[-3:])
                    if avg_dn > 0 and avg_up >= avg_dn * 0.75:
                        continue

                reason = (
                    f"S4:7日高={week_high:.4g},当前={current_price:.4g},"
                    f"反弹={rebound_pct * 100:.1f}%,"
                    f"1H_RSI={r3:.1f}(下降),MACD_hist衰竭"
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
    # 调度入口
    # ─────────────────────────────────────────

    def run_fast(self):
        """每5分钟调度：S2（无量回调做多）+ S4（反弹做空）"""
        try:
            self.scan_s2_pullback_long()
        except Exception as e:
            logger.error(f"[S2] 扫描异常: {e}")
        try:
            self.scan_s4_rebound_short()
        except Exception as e:
            logger.error(f"[S4] 扫描异常: {e}")

    def run_slow(self):
        """每30分钟调度：S1（早期做多）+ S3（顶部做空）; 内部限速防重复"""
        now = datetime.utcnow()
        if (self._last_slow_scan and
                (now - self._last_slow_scan).total_seconds() < self.SLOW_SCAN_INTERVAL_SEC):
            return
        self._last_slow_scan = now
        logger.info("[多策略] S1+S3 慢速扫描开始")
        try:
            self.scan_s1_early_long()
        except Exception as e:
            logger.error(f"[S1] 扫描异常: {e}")
        try:
            self.scan_s3_top_short()
        except Exception as e:
            logger.error(f"[S3] 扫描异常: {e}")
