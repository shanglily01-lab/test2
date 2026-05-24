#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
六策略量化交易服务 (v2 — 2026-05-22 精简确认层版)

核心改动: 去掉所有滞后确认层, 每个策略保留 2-3 个前置信号, 提前入场。
- S1: 早期做多 — 去掉 4H MACD (滞后 8h), 保留 RSI+MA20
- S2: 回调做多 — 降低门槛 15%→8%, 去掉 15m RSI 上升确认
- S3: 顶部做空 — 去掉"已从高点回落 3-15%"(等跌了才空=灾难), 阴线需求 2→1
- S4: 反弹衰竭做空 — 去掉"反弹 5%+", 三选二→三选一
- S5: 大币超卖 — 去掉 RSI 下降过滤 (RSI<32 本身已足够)
- S6: 小币量能异动 — 去掉 RSI 28-55 (量能先行, 不需要 RSI 再确认)
- S7: MA 支撑反弹 — 去掉量能确认 (等量确认=错过反弹)

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

    # ════════════════════════════════════════════════════════════════
    # 2026-05-17 统一: 全部 5x 杠杆, paper 500U,
    # SL/TP/持仓时间从 system_settings 动态读 (stop_loss_pct / take_profit_pct / max_hold_hours)
    # 实盘 margin 由 user_api_keys.max_position_value 控制 (每 API key 独立)
    # ════════════════════════════════════════════════════════════════

    # 策略1: 早期做多
    S1_LEVERAGE = 5
    S1_MARGIN = 500
    S1_MAX_POSITIONS = 999  # 测试阶段不限制，上线后改回 3
    S1_SOURCE = 's1_early_long'

    # 策略2: 无量回调做多
    S2_LEVERAGE = 5  # 2026-05-17 从 10x 统一为 5x
    S2_MARGIN = 500
    S2_MAX_POSITIONS = 999  # 测试阶段不限制，上线后改回 3
    S2_SOURCE = 's2_pullback_long'

    # 策略3: 顶部做空
    S3_LEVERAGE = 5
    S3_MARGIN = 500
    S3_MAX_POSITIONS = 999  # 测试阶段不限制，上线后改回 3
    S3_SOURCE = 's3_top_short'

    # 策略4: 反弹动能衰竭做空
    S4_LEVERAGE = 5
    S4_MARGIN = 500
    S4_MAX_POSITIONS = 999  # 测试阶段不限制，上线后改回 3
    S4_SOURCE = 's4_rebound_short'

    # 策略5: 大币4H超卖反弹做多 (BTC/ETH/SOL/BNB/XRP)
    S5_LEVERAGE = 5
    S5_MARGIN = 500
    S5_MAX_POSITIONS = 3
    S5_SOURCE = 's5_large_oversold'
    S5_SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT']

    # 策略6: 小币量能异动做多 (排除大市值)
    S6_LEVERAGE = 5
    S6_MARGIN = 500
    S6_MAX_POSITIONS = 5
    S6_SOURCE = 's6_vol_spike'
    # 排除大市值币种（它们的量能信号无效）
    S6_EXCLUDE_SYMBOLS = {
        'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
        'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'TRX/USDT', 'LINK/USDT',
        'TON/USDT', 'DOT/USDT', 'MATIC/USDT', 'SHIB/USDT', 'LTC/USDT',
        'UNI/USDT', 'ATOM/USDT', 'ETC/USDT', 'BCH/USDT', 'FIL/USDT',
        'FIO/USDT',  # 回测验证: 量能信号对极低价格币失真，10天8%胜率
    }

    # 策略7: 小币均线支撑反弹做多 (MA20下方82-95%区间反弹)
    S7_LEVERAGE = 5
    S7_MARGIN = 500
    S7_MAX_POSITIONS = 5
    S7_SOURCE = 's7_ma_support'

    # ═════════════════════════════════════════════════════════════════
    # 策略8: 顶部反转做空 (S8) — 自 strategy_live.py topshort 迁入 2026-05-15
    #   原 strategy_live.py 用 HTTP /api/futures/open + 限价单, 此处简化为市价
    #   入场条件: 48h 涨幅 >= 80% + 6 根 1h 无新高 + 12 天上市历史
    #             + 24h 未跌过 15% + 3h 入场位置 > 10% (不在低点接刀)
    # ═════════════════════════════════════════════════════════════════
    S8_LEVERAGE = 5
    S8_MARGIN = 500
    S8_MAX_POSITIONS = 3
    S8_SOURCE = 's8_topshort'
    S8_PUMP_THRESH = 0.80    # 48h 涨 >= 80%
    S8_NO_NEW_H = 6          # 之后 6 根 1h 无新高
    S8_LOOKBACK_H = 48       # pump 检测窗口
    S8_MIN_HISTORY_DAYS = 12 # 上市最低天数
    S8_MAX_24H_DROP = -15.0  # 24h 跌幅 < -15% 跳过
    S8_MAX_PEAK_DRAWDOWN = 0.50  # 从峰值已跌 50%+ 跳过
    S8_SIGNAL_AGE_HOURS = 6  # 信号最长生效时间
    S8_ENTRY_POS_MIN = 10.0  # 3h 15m 区间位置 >= 10%

    # ═════════════════════════════════════════════════════════════════
    # 策略9: Gemini AI 抄底反转做多 (S9) — 自 strategy_bigmid.py 迁入
    #   每 6h 调用 Google Gemini API, 对成交额达标的 USDT 交易对判 long/skip
    #   抄底反转专项: 仅做 LONG, Gemini 返回 short 也降级 skip
    #   候选池: SQL LIMIT 100 (按成交额降序) - 头部 5 大币 - 证券类
    # ═════════════════════════════════════════════════════════════════
    S9_LEVERAGE = 5
    S9_MARGIN = 500
    S9_MAX_POSITIONS = 5
    S9_SOURCE = 's9_gemini_ai'
    S9_INTERVAL_HOURS = 6    # 每 6h 调一次 Gemini
    S9_MIN_PNL_PCT = 0.01    # Gemini 预期 PnL >= 1% 才下单
    S9_MIN_QUOTE_VOLUME = 10_000_000  # 24h 成交额下限 1000 万 USDT
    S9_EXCLUDE_BASES = {"BTC", "ETH", "BNB", "SOL", "XRP"}  # 排除头部
    S9_PER_SYMBOL_DELAY_S = 1.0  # 每个 symbol 调 Gemini 间隔, 防 rate limit

    ALL_SOURCES = (
        S1_SOURCE, S2_SOURCE, S3_SOURCE, S4_SOURCE, S5_SOURCE, S6_SOURCE, S7_SOURCE,
        S8_SOURCE, S9_SOURCE,
    )
    SLOW_SCAN_INTERVAL_SEC = 1800  # 30 分钟

    def __init__(self, db_config: dict, ws_price_service=None):
        self.db_config = db_config
        self.ws_service = ws_price_service
        self.ti = TechnicalIndicators()
        self._last_slow_scan: Optional[datetime] = None
        # S8 上市历史缓存 (sym -> (ok, ts)),15 分钟 TTL
        self._s8_history_cache: dict = {}
        # S9 限速器: 上次跑 Gemini 的时间
        self._last_s9_run: Optional[datetime] = None
        # S9 Gemini client (lazy init)
        self._gemini_client = None

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

    # 各时间框架允许的最大数据延迟（小时），超过则视为过时数据不开仓
    _TF_MAX_LAG_HOURS = {'1h': 2.0, '15m': 0.5, '4h': 5.0, '1d': 26.0}

    def _get_klines(self, symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
        """查询K线，返回标准化 DataFrame (columns: open/high/low/close/volume)
        若最新一根K线超过允许延迟则返回 None，防止以历史信号在当前价格开仓。
        """
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

            # 新鲜度检查：最新K线不能太旧，否则信号已失效
            max_lag = self._TF_MAX_LAG_HOURS.get(timeframe, 3.0)
            last_ts_sec = int(df['open_time'].iloc[-1]) / 1000
            age_hours = (datetime.utcnow().timestamp() - last_ts_sec) / 3600
            if age_hours > max_lag:
                logger.debug(f"[多策略] {symbol}/{timeframe} 数据过旧 {age_hours:.1f}H，跳过")
                return None

            return df
        except Exception as e:
            logger.warning(f"[多策略] K线查询失败 {symbol}/{timeframe}: {e}")
            return None

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """获取实时价格: 优先 WS → 回退到 5m K 线最新收盘价"""
        if self.ws_service:
            try:
                p = self.ws_service.get_price(symbol)
                if p:
                    return float(p)
            except Exception as e:
                logger.debug(f"[多策略] WS取价失败 {symbol}: {e}")
        # WS 不可用或未订阅该 symbol, 回退到 DB 5m K 线最新收盘价
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT close_price FROM kline_data "
                "WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 1",
                (symbol,)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if row and row.get('close_price') is not None:
                return float(row['close_price'])
        except Exception as e:
            logger.warning(f"[多策略] DB取价失败 {symbol}: {e}")
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

    def _is_allowed_for_live(self, symbol: str) -> bool:
        """白名单或 TOP100 才允许同步实盘。"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT "
                "  (SELECT 1 FROM top_performing_symbols WHERE symbol=%s LIMIT 1) AS in_top100,"
                "  (SELECT rating_level FROM trading_symbol_rating WHERE symbol=%s LIMIT 1) AS rating_level",
                (symbol, symbol),
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if row:
                in_top100 = row.get('in_top100') == 1
                is_whitelist = row.get('rating_level') is not None and int(row['rating_level']) == 0
                if in_top100 or is_whitelist:
                    return True
            return False
        except Exception as e:
            logger.warning(f"[多策略] 检查白名单/TOP100失败 {symbol}: {e}, 默认允许")
            return True

    def _get_runtime_sl_tp_hold(self) -> tuple:
        """从 system_settings 读 (sl_pct, tp_pct, hold_hours). 2026-05-17 起 S1-S9 统一用此值,
        不再用策略硬编码常量。SQL UPDATE setting_value 后 60 秒内动态生效。

        2026-05-22: S1~S9 使用独立的 s1_s9_max_hold_hours（默认 6H），
        Smart Trader/预测神器/BTC动量 仍使用 max_hold_hours（默认 4H）。

        Returns:
            (sl_pct, tp_pct, hold_hours) - 默认 (0.02, 0.05, 6)
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT setting_key, setting_value FROM system_settings
                WHERE setting_key IN ('stop_loss_pct', 'take_profit_pct', 's1_s9_max_hold_hours')
            """)
            rows = {r['setting_key']: r['setting_value'] for r in cur.fetchall()}
            cur.close(); conn.close()
            sl = float(rows.get('stop_loss_pct', 0.02))
            tp = float(rows.get('take_profit_pct', 0.05))
            hold = max(1, int(float(rows.get('s1_s9_max_hold_hours', 6))))
            return sl, tp, hold
        except Exception as e:
            logger.warning(f"[多策略] 读 system_settings SL/TP/hold 失败,用默认 (0.02/0.05/6h): {e}")
            return 0.02, 0.05, 6

    def _read_setting_bool(self, key: str, default: bool = False) -> bool:
        """读 system_settings 布尔开关,失败返回 default. 用于 S8/S9 等策略独立 kill switch.
        '1' / 'true' / 'yes' / 'on' (大小写不敏感) → True, 其余 → False
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
                (key,)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if not row:
                return default
            val = str(row.get('setting_value', '')).strip().lower()
            if val in ('1', 'true', 'yes', 'on'):
                return True
            if val in ('0', 'false', 'no', 'off', ''):
                return False
            return default
        except Exception:
            return default

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
        # TOP100/白名单实盘过滤: 模拟单已开但实盘不下
        if not self._is_allowed_for_live(symbol):
            logger.info(f"[{source}] {symbol} 不在 TOP100, 跳过实盘 (模拟单已开)")
            return
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
        """S1: RSI 25-52(上升) + 价格在MA20 80-105% + 量能回升
        精简: 去掉 4H MACD (滞后8h, 等到确认趋势已走完), 保留核心 RSI+位置
        """
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
                # 1H RSI 25-52，且最近2根在上升（核心信号: RSI从超卖回升）
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

                # 价格在MA20的80-105%（确认处于低位, 不是追高）
                df_1d = self._get_klines(symbol, '1d', 25)
                if df_1d is None or len(df_1d) < 21:
                    continue
                ma20 = float(df_1d['close'].rolling(20).mean().iloc[-1])
                cur_price_d = float(df_1d['close'].iloc[-1])
                if not (ma20 * 0.80 <= cur_price_d <= ma20 * 1.05):
                    continue

                # 量能回升：今日量 > 近7日均量 × 0.9
                vol_today = float(df_1d['volume'].iloc[-1])
                vol_avg7 = float(df_1d['volume'].iloc[-8:-1].mean()) if len(df_1d) >= 8 else vol_today
                if vol_avg7 > 0 and vol_today < vol_avg7 * 0.9:
                    continue

                reason = (
                    f"S1:1H_RSI={last_rsi:.1f}(上升),"
                    f"价格={cur_price_d:.4g},MA20={ma20:.4g},"
                    f"量比={vol_today / vol_avg7:.2f}"
                )
                _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                if self._open_position(
                    symbol, 'LONG', self.S1_MARGIN, self.S1_LEVERAGE,
                    _tp, _sl, _hold, self.S1_SOURCE, reason
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
        """S2: 48H涨>12%后回调8-38%，15m RSI 30-58（低位即可，不要求上升）"""
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
                current_close = float(closes[-1])

                # 48H价格区间 > 8%（降低门槛，更多机会）
                price_range_pct = max(closes[-48:]) / min(closes[-48:]) - 1 if min(closes[-48:]) > 0 else 0
                if price_range_pct < 0.08:
                    continue

                # 从48H高点回调8-38%（降低门槛，提前入场）
                drawdown_pct = (recent_high - current_close) / recent_high if recent_high > 0 else 0
                if not (0.08 <= drawdown_pct <= 0.38):
                    continue

                # 15m RSI 30-58 即可（不要求上升，避免等RSI回升的延迟）
                df_15m = self._get_klines(symbol, '15m', 30)
                if df_15m is None or len(df_15m) < 15:
                    continue
                rsi_15m = self.ti.calculate_rsi(df_15m)
                if len(rsi_15m) < 2:
                    continue
                last_rsi = float(rsi_15m.iloc[-1])
                if not (30 <= last_rsi <= 58):
                    continue

                reason = (
                    f"S2:48H涨={price_range_pct * 100:.1f}%,"
                    f"回调={drawdown_pct * 100:.1f}%,"
                    f"15m_RSI={last_rsi:.1f}(低位)"
                )
                _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                if self._open_position(
                    symbol, 'LONG', self.S2_MARGIN, self.S2_LEVERAGE,
                    _tp, _sl, _hold, self.S2_SOURCE, reason
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
        """S3: RSI>=70顶背离 + 48H涨>20% + 4H MACD下行 + 近3根有阴线
        精简: 去掉"已从高点回落3-15%"(最致命的滞后确认,等跌了才空=灾难)
        """
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

                # 1H RSI > 70（超买）
                rsi_series = self.ti.calculate_rsi(df_1h)
                if len(rsi_series) < 6:
                    continue
                last_rsi = float(rsi_series.iloc[-1])
                if last_rsi <= 70:
                    continue

                # RSI顶背离：当前RSI不是最近6根的最高点（说明动量衰减）
                recent_rsi_max = float(rsi_series.iloc[-6:].max())
                if last_rsi >= recent_rsi_max:
                    continue

                # 48H涨幅 > 20%（确认有可跌空间）
                current_price = float(df_1h['close'].iloc[-1])
                high_48h = float(df_1h['high'].iloc[-48:].max()) if len(df_1h) >= 48 else float(df_1h['high'].max())
                price_48h_ago = float(df_1h['close'].iloc[-48]) if len(df_1h) >= 48 else float(df_1h['close'].iloc[0])
                gain_48h = (high_48h - price_48h_ago) / price_48h_ago if price_48h_ago > 0 else 0
                if gain_48h <= 0.20:
                    continue

                # 4H MACD histogram 开始下行（多时间框架确认弱势）
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

                # 近3根1H至少1根阴线（简化: 从2根改为1根, 不必等完全确认转弱）
                bearish_count = sum(
                    1 for k in range(-3, 0)
                    if float(df_1h['close'].iloc[k]) < float(df_1h['open'].iloc[k])
                )
                if bearish_count < 1:
                    continue

                retreat_pct = (high_48h - current_price) / high_48h if high_48h > 0 else 0
                reason = (
                    f"S3:1H_RSI={last_rsi:.1f}(顶背离,max={recent_rsi_max:.1f}),"
                    f"从高点已回={retreat_pct * 100:.1f}%,"
                    f"4H_MACD下行,48H涨={gain_48h * 100:.1f}%"
                )
                _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                if self._open_position(
                    symbol, 'SHORT', self.S3_MARGIN, self.S3_LEVERAGE,
                    _tp, _sl, _hold, self.S3_SOURCE, reason
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
        """S4: 14日高点50-85%反弹+曾下跌15%+ 1个指标确认（三选一）"""
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
                df_1h_14d = self._get_klines(symbol, '1h', 14 * 24 + 2)
                if df_1h_14d is None or len(df_1h_14d) < 52:
                    continue

                two_week_high = float(df_1h_14d['high'].max())
                current_price = float(df_1h_14d['close'].iloc[-1])

                # 当前价格在14日高点的50-85%（大幅回落后反弹）
                rebound_pct = current_price / two_week_high if two_week_high > 0 else 0
                if not (0.50 <= rebound_pct <= 0.85):
                    continue

                # 曾经从14日高点下跌超过15%（有实质性回落，不是横盘）
                low_7d = float(df_1h_14d['low'].iloc[-7 * 24:].min())
                max_drop = (two_week_high - low_7d) / two_week_high if two_week_high > 0 else 0
                if max_drop < 0.15:
                    continue

                # 取最近52根K线做指标分析
                df_1h = df_1h_14d.iloc[-52:].reset_index(drop=True)

                # 三选一（之前三选二：减少确认延迟）
                # 条件1: MACD histogram 任意一段下降
                _, _, hist_1h = self.ti.calculate_macd(df_1h)
                macd_bearish = False
                if len(hist_1h) >= 3:
                    h3 = float(hist_1h.iloc[-1])
                    h2 = float(hist_1h.iloc[-2])
                    if h3 < h2:
                        macd_bearish = True

                # 条件2: RSI < 65 且最后一根在下降
                rsi_1h = self.ti.calculate_rsi(df_1h)
                rsi_bearish = False
                r3 = 50.0
                if len(rsi_1h) >= 2:
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

                # 三选一
                if not (macd_bearish or rsi_bearish or vol_shrink):
                    continue

                reason = (
                    f"S4:14日高={two_week_high:.4g},反弹={rebound_pct * 100:.1f}%,"
                    f"1H_RSI={r3:.1f},"
                    f"MACD={'空' if macd_bearish else '-'},"
                    f"量萎缩={'是' if vol_shrink else '-'}"
                )
                _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                if self._open_position(
                    symbol, 'SHORT', self.S4_MARGIN, self.S4_LEVERAGE,
                    _tp, _sl, _hold, self.S4_SOURCE, reason
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
        """S5: BTC/ETH/SOL/BNB/XRP 的 4H RSI<32 已回升 + 价格低于日MA20 做多
        精简: 去掉"RSI仍在下降则跳过"（等回升确认=错过最低点）
        """
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
                if len(rsi_4h) < 2:
                    continue
                last_rsi_4h = float(rsi_4h.iloc[-1])
                if last_rsi_4h >= 32:
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
                    f"S5:4H_RSI={last_rsi_4h:.1f}(超卖),"
                    f"价格={cur_price:.4g},日MA20={ma20_1d:.4g}({price_vs_ma * 100:.1f}%)"
                )
                _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                if self._open_position(
                    symbol, 'LONG', self.S5_MARGIN, self.S5_LEVERAGE,
                    _tp, _sl, _hold, self.S5_SOURCE, reason
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
        """S6: 12H量峰>3.5x均量 + 当前量1.2-5x + 价格在MA20 75-108% + 3H涨<5%
        精简: 去掉 RSI 28-55（量能异动本身就是先行信号，不需要RSI再确认）
        """
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

                vol_base = float(df_1h['volume'].iloc[-49:-1].mean()) if len(df_1h) >= 49 else float(df_1h['volume'].iloc[:-1].mean())
                if vol_base <= 0:
                    continue

                # 当前量比 1.2-5x
                cur_vol = float(df_1h['volume'].iloc[-1])
                vol_ratio_cur = cur_vol / vol_base
                if not (1.2 <= vol_ratio_cur <= 5.0):
                    continue

                # 12H内量峰 > 3.5x（核心信号：有资金入场）
                max_vol_12h = float(df_1h['volume'].iloc[-12:].max())
                peak_ratio = max_vol_12h / vol_base
                if peak_ratio < 3.5:
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
                    f"价格/MA20={price_ratio * 100:.1f}%,"
                    f"3H涨={gain_3h * 100:.1f}%"
                )
                _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                if self._open_position(
                    symbol, 'LONG', self.S6_MARGIN, self.S6_LEVERAGE,
                    _tp, _sl, _hold, self.S6_SOURCE, reason
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
        """S7: 价格跌至20H均线82-95%区间 + 阳线反弹（移除量能确认: 等量=错过反弹）"""
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

                # 当前是阳线且高于前一根收盘（核心信号：MA20附近获得支撑反弹）
                if not (close_v > open_v and close_v > prev_close):
                    continue

                reason = (
                    f"S7:价格/20H_MA={ratio * 100:.1f}%,"
                    f"阳线反弹"
                )
                _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                if self._open_position(
                    symbol, 'LONG', self.S7_MARGIN, self.S7_LEVERAGE,
                    _tp, _sl, _hold, self.S7_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S7] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S7] 本轮新开 {opened} 单")

    # ═════════════════════════════════════════════════════════════════
    # 策略8: 顶部反转做空 (topshort) - 自 strategy_live.py 迁入 2026-05-15
    # ═════════════════════════════════════════════════════════════════

    def _s8_has_min_listed_history(self, symbol: str) -> bool:
        """检查上市历史 >= S8_MIN_HISTORY_DAYS 天 (15 分钟缓存)"""
        import time as _t
        now = _t.time()
        ent = self._s8_history_cache.get(symbol)
        if ent and (now - ent[1]) < 15 * 60:
            return ent[0]
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT MIN(open_time) AS tmin FROM kline_data "
                "WHERE timeframe='1h' AND symbol=%s",
                (symbol,)
            )
            r = cur.fetchone() or {}
            cur.close(); conn.close()
            tmin = r.get('tmin')
            if tmin is None:
                self._s8_history_cache[symbol] = (False, now)
                return False
            min_ms = self.S8_MIN_HISTORY_DAYS * 24 * 60 * 60 * 1000
            now_ms = int(now * 1000)
            ok = (now_ms - int(tmin)) >= min_ms
            self._s8_history_cache[symbol] = (ok, now)
            return ok
        except Exception as e:
            logger.warning(f"[S8] 上市历史检查失败 {symbol}: {e}")
            return False

    def _s8_entry_position_check(self, symbol: str, cur_price: float) -> bool:
        """3h 内 15m K 线的位置检查: 价格百分位 >= S8_ENTRY_POS_MIN (不在低位接刀)"""
        try:
            df = self._get_klines(symbol, '15m', 12)
            if df is None or len(df) < 12:
                return True  # 数据不足放行
            hi = float(df['high'].max())
            lo = float(df['low'].min())
            if hi <= lo:
                return True
            pos_pct = (cur_price - lo) / (hi - lo) * 100
            return pos_pct >= self.S8_ENTRY_POS_MIN
        except Exception as e:
            logger.warning(f"[S8] 入场位置检查失败 {symbol}: {e}")
            return True

    def scan_s8_topshort(self):
        """S8: 48h涨>=80% + 6根1h无新高 + 12天上市 + 24h未跌过15% 做空"""
        # 独立 kill switch (default OFF, 需 system_settings 显式开启)
        if not self._read_setting_bool('s8_topshort_enabled', default=False):
            return

        if self._strategy_position_count(self.S8_SOURCE) >= self.S8_MAX_POSITIONS:
            return

        big4 = self._get_big4_signal()
        if big4 == 'STRONG_BULLISH':
            logger.info("[S8] Big4 强牛市,跳过顶部反转做空")
            return

        # 24h 涨跌 >= 15% 的候选币种 (48h pump >= 80% 在 1h 数据里再精筛)
        symbols = self._get_candidate_symbols(min_abs_change=15.0)
        opened = 0
        now_ms = int(datetime.utcnow().timestamp() * 1000)
        signal_age_max_ms = self.S8_SIGNAL_AGE_HOURS * 3600 * 1000

        for symbol in symbols:
            if self._strategy_position_count(self.S8_SOURCE) + opened >= self.S8_MAX_POSITIONS:
                break
            if self._has_multi_strategy_position(symbol):
                continue
            if not self._s8_has_min_listed_history(symbol):
                continue

            try:
                # 1h K 线: 含 48h 窗口 + 6 根观察期 + 余量
                df = self._get_klines(symbol, '1h', self.S8_LOOKBACK_H + self.S8_NO_NEW_H + 20)
                if df is None or len(df) < self.S8_LOOKBACK_H + self.S8_NO_NEW_H + 2:
                    continue

                highs = df['high'].astype(float).tolist()
                lows = df['low'].astype(float).tolist()
                opens_ts = [int(t) for t in df['open_time'].tolist()]
                n = len(df)

                # 倒序找最近一个满足 pump 条件的 i
                for i in range(n - self.S8_NO_NEW_H - 2,
                               max(0, n - self.S8_LOOKBACK_H - self.S8_NO_NEW_H - 10) - 1, -1):
                    lo_window = lows[max(0, i - self.S8_LOOKBACK_H):i]
                    if not lo_window:
                        continue
                    lo_win = min(lo_window)
                    if lo_win == 0:
                        continue
                    pump = (highs[i] - lo_win) / lo_win
                    if pump < self.S8_PUMP_THRESH:
                        continue
                    peak = highs[i]
                    if i + self.S8_NO_NEW_H >= n:
                        continue
                    if not all(highs[i + j] < peak for j in range(1, self.S8_NO_NEW_H + 1)):
                        continue
                    # 信号年龄检查
                    entry_ts = opens_ts[i + self.S8_NO_NEW_H]
                    if now_ms - entry_ts > signal_age_max_ms:
                        continue

                    cur_price = self._get_current_price(symbol)
                    if not cur_price:
                        continue

                    # 现价 > pump 启动低 (信号未失效)
                    if cur_price <= lo_win:
                        continue
                    # 从峰值回落 < 50%
                    drawdown = (peak - cur_price) / peak
                    if drawdown > self.S8_MAX_PEAK_DRAWDOWN:
                        continue

                    # 24h 已跌过 15% 跳过
                    ch24_val = None
                    try:
                        conn = self._get_conn()
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT change_24h FROM price_stats_24h WHERE symbol=%s LIMIT 1",
                            (symbol,)
                        )
                        r24 = cur.fetchone()
                        cur.close(); conn.close()
                        if r24 and r24.get('change_24h') is not None:
                            ch24_val = float(r24['change_24h'])
                    except Exception:
                        pass
                    if ch24_val is not None and ch24_val < self.S8_MAX_24H_DROP:
                        continue

                    # 入场位置检查
                    if not self._s8_entry_position_check(symbol, cur_price):
                        continue

                    ch24_str = f"{ch24_val:.1f}%" if ch24_val is not None else "NA"
                    reason = (
                        f"S8:48h_pump={pump * 100:.0f}%,peak={peak:.5g},"
                        f"cur={cur_price:.5g},dd={drawdown * 100:.0f}%,"
                        f"24h={ch24_str}"
                    )
                    _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                    if self._open_position(
                        symbol, 'SHORT', self.S8_MARGIN, self.S8_LEVERAGE,
                        _tp, _sl, _hold,
                        self.S8_SOURCE, reason
                    ):
                        opened += 1
                    break  # 该 symbol 找到信号即结束

            except Exception as e:
                logger.warning(f"[S8] 扫描 {symbol} 异常: {e}")

        if opened:
            logger.info(f"[S8] 本轮新开 {opened} 单")

    # ═════════════════════════════════════════════════════════════════
    # 策略9: Gemini AI 抄底反转做多 - 自 strategy_bigmid.py 迁入
    # ═════════════════════════════════════════════════════════════════

    def _init_gemini_client(self):
        """Lazy init Gemini client (失败返回 None)"""
        if self._gemini_client is not None:
            return self._gemini_client
        import os
        api_key = os.getenv('GEMINI_API_KEY', '')
        if not api_key:
            logger.warning("[S9] GEMINI_API_KEY 未配置,S9 不工作")
            return None
        try:
            from google import genai
            self._gemini_client = genai.Client(api_key=api_key)
            logger.info("[S9] Gemini client 已就绪")
            return self._gemini_client
        except ImportError:
            logger.warning("[S9] google-genai 未安装: pip install google-genai")
            return None
        except Exception as e:
            logger.error(f"[S9] Gemini client 初始化失败: {e}")
            return None

    def _s9_get_top30_symbols(self) -> List[str]:
        """S9 候选池: 24h 成交额降序 LIMIT 100,排除头部 5 大币 + 证券类。

        历史名字保留是为了向下兼容(其它地方都按这个名字调),实际不再硬性截断到 30。
        """
        try:
            from app.services.securities_filter import is_security
        except ImportError:
            def is_security(_s):
                return False

        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol, volume_24h FROM price_stats_24h
                WHERE symbol LIKE '%%/USDT'
                  AND volume_24h >= %s
                ORDER BY volume_24h DESC
                LIMIT 100
            """, (self.S9_MIN_QUOTE_VOLUME,))
            rows = cur.fetchall()
            cur.close(); conn.close()
        except Exception as e:
            logger.warning(f"[S9] 取候选池失败: {e}")
            return []

        result = []
        for r in rows:
            sym = r['symbol']
            base = sym.split('/')[0] if '/' in sym else sym
            if base in self.S9_EXCLUDE_BASES:
                continue
            if is_security(sym):
                continue
            result.append(sym)
        return result

    @staticmethod
    def _s9_calc_rsi(closes: list, period: int = 14) -> Optional[float]:
        """Wilder 14-period RSI"""
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - 100 / (1 + rs), 2)

    def _s9_fetch_market_data(self, symbol: str) -> Optional[dict]:
        """准备喂给 Gemini 的市场数据 (15d 日线 + 7d 1h + 8h 15m + RSI + 7d 区间位置)"""
        df_1d = self._get_klines(symbol, '1d', 15)
        df_1h = self._get_klines(symbol, '1h', 168)
        df_15m = self._get_klines(symbol, '15m', 32)
        if df_1d is None or df_1h is None or df_15m is None:
            return None
        if len(df_1d) < 14 or len(df_1h) < 140 or len(df_15m) < 24:
            return None

        cur_price = self._get_current_price(symbol)
        if not cur_price:
            return None

        change_24h = 0.0
        vol_24h = 0.0
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT change_24h, volume_24h FROM price_stats_24h WHERE symbol=%s LIMIT 1",
                (symbol,)
            )
            r = cur.fetchone()
            cur.close(); conn.close()
            if r:
                if r.get('change_24h') is not None:
                    change_24h = float(r['change_24h'])
                if r.get('volume_24h') is not None:
                    vol_24h = float(r['volume_24h'])
        except Exception:
            pass

        # 7d 高低 + 区间位置
        lows_7d = df_1h['low'].astype(float).tolist()
        highs_7d = df_1h['high'].astype(float).tolist()
        low_7d, high_7d = min(lows_7d), max(highs_7d)
        band = high_7d - low_7d
        pos_in_band = (cur_price - low_7d) / band if band > 0 else 0.0
        dist_from_low_pct = (cur_price - low_7d) / low_7d * 100 if low_7d > 0 else 0.0

        rsi_1h = self._s9_calc_rsi(df_1h['close'].astype(float).tolist(), 14)
        rsi_15m = self._s9_calc_rsi(df_15m['close'].astype(float).tolist(), 14)
        rsi_1d = self._s9_calc_rsi(df_1d['close'].astype(float).tolist(), 14)

        def _bars_to_dicts(df, tf):
            out = []
            for _, b in df.iterrows():
                t = datetime.utcfromtimestamp(int(b['open_time']) / 1000)
                tstr = t.strftime('%Y-%m-%d %H:%M') if tf != '1d' else t.strftime('%Y-%m-%d')
                out.append({
                    't': tstr, 'o': round(float(b['open']), 8),
                    'h': round(float(b['high']), 8), 'l': round(float(b['low']), 8),
                    'c': round(float(b['close']), 8), 'v': round(float(b['volume'] or 0), 2),
                })
            return out

        return {
            'symbol': symbol,
            'current_price': round(cur_price, 8),
            'change_24h_pct': round(change_24h, 2),
            'volume_24h': round(vol_24h, 2),
            'rsi_1h': rsi_1h,
            'rsi_15m': rsi_15m,
            'rsi_daily': rsi_1d,
            'low_7d': round(low_7d, 8),
            'high_7d': round(high_7d, 8),
            'pos_in_7d_band': round(pos_in_band, 3),
            'dist_from_low_7d_pct': round(dist_from_low_pct, 2),
            'daily_15d': _bars_to_dicts(df_1d, '1d'),
            'h1_7d': _bars_to_dicts(df_1h, '1h'),
            'm15_8h': _bars_to_dicts(df_15m, '15m'),
        }

    @staticmethod
    def _s9_build_prompt(data: dict) -> str:
        """构造 Gemini prompt — 抄底反转 LONG-only (原 strategy_bigmid 2026-05-04 版本)"""
        sym = data['symbol']

        def _fmt_klines(klines, header):
            lines = [header]
            lines.append(f"{'time':<17} {'open':>14} {'high':>14} {'low':>14} {'close':>14} {'vol':>14}")
            for k in klines:
                lines.append(f"{k['t']:<17} {k['o']:>14} {k['h']:>14} {k['l']:>14} {k['c']:>14} {k['v']:>14}")
            return "\n".join(lines)

        return f"""You are a quantitative crypto futures trading analyst.
Your job is to find **bottom-reversal LONG entries** for {sym} on a 12-hour swing trade.
SHORT positions are NOT allowed in this task.

CURRENT STATE
  current_price:           {data['current_price']}
  24h change:              {data['change_24h_pct']}%
  24h volume:              {data['volume_24h']}
  RSI(14, daily):          {data['rsi_daily']}
  RSI(14, 1h):             {data['rsi_1h']}
  RSI(14, 15m):            {data['rsi_15m']}

7-DAY RANGE (key reference for bottom-reversal)
  7d_low:                  {data['low_7d']}
  7d_high:                 {data['high_7d']}
  pos_in_7d_band:          {data['pos_in_7d_band']}  (0.0 = at 7d_low, 1.0 = at 7d_high)
  distance_above_7d_low:   {data['dist_from_low_7d_pct']}%

{_fmt_klines(data['daily_15d'], "DAILY (15 days, oldest -> newest):")}

{_fmt_klines(data['h1_7d'], "1H KLINES (last 7 days, oldest -> newest):")}

{_fmt_klines(data['m15_8h'], "15M KLINES (last 8h, oldest -> newest):")}

DECISION RULES — return direction="long" ONLY when MOST of the following hold:
  1. distance_above_7d_low <= 8%  (price is near the 7-day low, not after a big rebound)
  2. Recent 1h/15m bars show stabilization or reversal:
       - declining selling volume vs the drop leg
       - long lower wicks / hammer / engulfing / divergence
  3. RSI confirms oversold or upturning:
       - RSI(1h) <= 35 OR RSI(15m) <= 30 OR bullish RSI divergence visible
  4. Current price is NOT in clear free-fall (no fresh 7d low in last 1-2 hours
     with expanding volume)

If price is mid-range (pos_in_7d_band > 0.5), or still breaking down, or stuck in
chop without a clear bottom signal, return direction="skip".
Returning skip is encouraged when in doubt — false negatives cost 0, false positives lose money.

POSITION CONSTRAINTS (do NOT propose changes)
  - Side: LONG only
  - Hold: 12 hours
  - Take profit: +5% from entry
  - Stop loss: -2% from entry

Output ONLY a single valid JSON object, no markdown fence, no extra text:
{{
  "direction": "long" | "skip",
  "expected_pnl_pct": <float, 0 to 0.05>,
  "confidence": <float between 0 and 1>,
  "reason": "<brief 1-sentence reason in Chinese>"
}}
"""

    def _s9_call_gemini(self, client, prompt: str) -> Optional[dict]:
        """调 Gemini API,解析 JSON. 任何错误返回 None."""
        import json
        text = ''
        try:
            from google.genai import types
            config = types.GenerateContentConfig(
                response_mime_type='application/json',
                http_options=types.HttpOptions(timeout=180_000),
            )
            import os
            model_name = os.getenv('GEMINI_MODEL', 'gemini-3-flash-preview')
            resp = client.models.generate_content(
                model=model_name, contents=prompt, config=config,
            )
            text = (resp.text or '').strip()
            if text.startswith('```'):
                text = text.strip('`').lstrip('json').strip()
            sig = json.loads(text)
            d = str(sig.get('direction', '')).lower()
            # 抄底反转: 拒绝 short
            if d == 'short':
                logger.info(f"[S9] Gemini 违反 prompt 返回 short,降级 skip")
                d = 'skip'
            if d not in ('long', 'skip'):
                logger.warning(f"[S9] Gemini 非法 direction={d} text={text[:200]}")
                return None
            sig['direction'] = d
            sig['expected_pnl_pct'] = float(sig.get('expected_pnl_pct', 0) or 0)
            sig['confidence'] = float(sig.get('confidence', 0) or 0)
            sig['reason'] = str(sig.get('reason', ''))[:200]
            return sig
        except json.JSONDecodeError:
            logger.warning(f"[S9] Gemini 返回非 JSON: {text[:200]}")
            return None
        except Exception as e:
            logger.warning(f"[S9] Gemini API 错误: {e}")
            return None

    def scan_s9_gemini_ai(self):
        """S9: 每 6h 调 Gemini AI 抄底反转 LONG 决策"""
        # 独立 kill switch (default OFF, 需 system_settings 显式开启,Gemini API 收费)
        if not self._read_setting_bool('s9_gemini_ai_enabled', default=False):
            return

        import time as _t
        now = datetime.utcnow()
        # 6h 限速
        if self._last_s9_run and (now - self._last_s9_run).total_seconds() < self.S9_INTERVAL_HOURS * 3600:
            return
        self._last_s9_run = now

        if self._strategy_position_count(self.S9_SOURCE) >= self.S9_MAX_POSITIONS:
            logger.info(f"[S9] 已达 max_positions={self.S9_MAX_POSITIONS},跳过")
            return

        client = self._init_gemini_client()
        if not client:
            return

        symbols = self._s9_get_top30_symbols()
        if not symbols:
            logger.warning("[S9] 候选池 symbols 为空")
            return

        logger.info(f"[S9] === Gemini 一轮开始, 候选数={len(symbols)} ===")
        opened, skipped, errs = 0, 0, 0

        for symbol in symbols:
            if self._strategy_position_count(self.S9_SOURCE) + opened >= self.S9_MAX_POSITIONS:
                logger.info(f"[S9] 达 max_positions,提前结束")
                break
            if self._has_multi_strategy_position(symbol):
                continue

            try:
                data = self._s9_fetch_market_data(symbol)
                if not data:
                    continue

                prompt = self._s9_build_prompt(data)
                signal = self._s9_call_gemini(client, prompt)
                if self.S9_PER_SYMBOL_DELAY_S > 0:
                    _t.sleep(self.S9_PER_SYMBOL_DELAY_S)

                if not signal:
                    errs += 1
                    continue

                if signal['direction'] == 'skip':
                    logger.info(
                        f"[S9] SKIP {symbol:14s} exp={signal['expected_pnl_pct']*100:.2f}% "
                        f"conf={signal['confidence']:.2f} reason={signal['reason'][:80]}"
                    )
                    skipped += 1
                    continue

                if signal['expected_pnl_pct'] < self.S9_MIN_PNL_PCT:
                    logger.info(
                        f"[S9] 预期 PnL 不足 {symbol:14s} exp={signal['expected_pnl_pct']*100:.2f}% "
                        f"< {self.S9_MIN_PNL_PCT*100:.0f}%"
                    )
                    skipped += 1
                    continue

                # direction='long' 已通过 _s9_call_gemini 强制
                reason = (
                    f"S9_Gemini: exp={signal['expected_pnl_pct']*100:.2f}% "
                    f"conf={signal['confidence']:.2f} {signal['reason'][:80]}"
                )
                _sl, _tp, _hold = self._get_runtime_sl_tp_hold()
                if self._open_position(
                    symbol, 'LONG', self.S9_MARGIN, self.S9_LEVERAGE,
                    _tp, _sl, _hold,
                    self.S9_SOURCE, reason
                ):
                    opened += 1

            except Exception as e:
                logger.warning(f"[S9] {symbol} 异常: {e}")
                errs += 1

        logger.info(f"[S9] === 一轮结束 opened={opened} skipped={skipped} errs={errs} ===")

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
        """每30分钟调度：S1+S3+S5+S6+S8+S9；内部限速防重复"""
        now = datetime.utcnow()
        if (self._last_slow_scan and
                (now - self._last_slow_scan).total_seconds() < self.SLOW_SCAN_INTERVAL_SEC):
            return
        self._last_slow_scan = now
        logger.info("[多策略] S1+S3+S5+S6+S8+S9 慢速扫描开始")
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
        try:
            self.scan_s8_topshort()
        except Exception as e:
            logger.error(f"[S8] 扫描异常: {e}")
        try:
            self.scan_s9_gemini_ai()
        except Exception as e:
            logger.error(f"[S9] 扫描异常: {e}")
