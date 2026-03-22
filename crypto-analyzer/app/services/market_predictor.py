"""
市场走势预测器
对每个交易对做72H K线分析（1H + 15M），预测未来3~4小时走势
每3小时由 app/main.py 调度运行一次
"""
import pymysql
import pymysql.cursors
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger


class MarketPredictor:
    def __init__(self, db_config: dict):
        self.db_config = db_config

    def _get_conn(self):
        return pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)

    # ──────────────────────────────────────────
    # 指标计算（参考 market_regime_detector.py）
    # ──────────────────────────────────────────

    def _calc_ema(self, closes: List[float], period: int) -> List[float]:
        if len(closes) < period:
            return []
        k = 2 / (period + 1)
        ema = [sum(closes[:period]) / period]
        for price in closes[period:]:
            ema.append(price * k + ema[-1] * (1 - k))
        return ema

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

    def _calc_adx(self, highs: List[float], lows: List[float],
                  closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 25.0
        tr_list, plus_dm, minus_dm = [], [], []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]),
                     abs(lows[i] - closes[i - 1]))
            tr_list.append(tr)
            pdm = max(0, highs[i] - highs[i - 1]) if highs[i] - highs[i - 1] > lows[i - 1] - lows[i] else 0
            mdm = max(0, lows[i - 1] - lows[i]) if lows[i - 1] - lows[i] > highs[i] - highs[i - 1] else 0
            plus_dm.append(pdm)
            minus_dm.append(mdm)
        if len(tr_list) < period:
            return 25.0
        atr = sum(tr_list[-period:]) / period
        if atr == 0:
            return 25.0
        pdi = sum(plus_dm[-period:]) / period / atr * 100
        mdi = sum(minus_dm[-period:]) / period / atr * 100
        ds = pdi + mdi
        return abs(pdi - mdi) / ds * 100 if ds > 0 else 0.0

    def _calc_macd_hist(self, closes: List[float],
                        fast: int = 8, slow: int = 21, signal: int = 5) -> List[float]:
        """返回MACD柱状图序列（最新在末尾）"""
        if len(closes) < slow + signal:
            return []
        ema_f = self._calc_ema(closes, fast)
        ema_s = self._calc_ema(closes, slow)
        min_len = min(len(ema_f), len(ema_s))
        macd_line = [ema_f[-(min_len - i)] - ema_s[-(min_len - i)] for i in range(min_len)]
        sig_line = self._calc_ema(macd_line, signal)
        hist = [macd_line[-(len(sig_line) - i)] - sig_line[-(len(sig_line) - i)]
                for i in range(len(sig_line))]
        return hist

    # ──────────────────────────────────────────
    # K线数据获取
    # ──────────────────────────────────────────

    def _fetch_klines(self, cursor, symbol: str, timeframe: str, limit: int) -> List[Dict]:
        cursor.execute(
            "SELECT open_price, high_price, low_price, close_price, volume "
            "FROM kline_data "
            "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
            "ORDER BY open_time DESC LIMIT %s",
            (symbol, timeframe, limit)
        )
        rows = cursor.fetchall()
        return list(reversed(rows))  # 时间从旧到新

    # ──────────────────────────────────────────
    # 单币分析
    # ──────────────────────────────────────────

    def analyze(self, symbol: str) -> Optional[Dict]:
        try:
            conn = self._get_conn()
            cursor = conn.cursor()

            # 1H 数据（72根）
            k1h = self._fetch_klines(cursor, symbol, '1h', 72)
            # 15M 数据（96根 = 24H）
            k15m = self._fetch_klines(cursor, symbol, '15m', 96)

            cursor.close()
            conn.close()

            if len(k1h) < 30 or len(k15m) < 20:
                logger.debug(f"[预测] {symbol} K线数据不足，跳过")
                return None

            # 提取序列
            h1_c  = [float(k['close_price']) for k in k1h]
            h1_h  = [float(k['high_price'])  for k in k1h]
            h1_l  = [float(k['low_price'])   for k in k1h]
            h1_v  = [float(k['volume'])      for k in k1h]

            m15_c = [float(k['close_price']) for k in k15m]
            m15_v = [float(k['volume'])      for k in k15m]

            # ── 1H 指标 ──
            ema9  = self._calc_ema(h1_c, 9)
            ema26 = self._calc_ema(h1_c, 26)
            rsi1h = self._calc_rsi(h1_c, 14)
            adx1h = self._calc_adx(h1_h, h1_l, h1_c, 14)
            macd_hist = self._calc_macd_hist(h1_c, 8, 21, 5)

            ema9_now  = ema9[-1]  if ema9  else h1_c[-1]
            ema26_now = ema26[-1] if ema26 else h1_c[-1]
            ema_bullish = ema9_now > ema26_now
            ema_bearish = ema9_now < ema26_now

            macd_bullish = (len(macd_hist) >= 3 and
                            macd_hist[-1] > 0 and
                            macd_hist[-1] >= macd_hist[-2])
            macd_bearish = (len(macd_hist) >= 3 and
                            macd_hist[-1] < 0 and
                            macd_hist[-1] <= macd_hist[-2])

            trend_1h_bull = ema_bullish and macd_bullish
            trend_1h_bear = ema_bearish and macd_bearish

            # ── 15M 指标 ──
            last8 = m15_c[-8:]
            bull_bars = sum(1 for i in range(1, len(last8)) if last8[i] > last8[i - 1])
            bear_bars = len(last8) - 1 - bull_bars

            rsi15m_recent = [self._calc_rsi(m15_c[:i], 14) for i in range(max(15, len(m15_c) - 4), len(m15_c) + 1)]
            rsi15m_rising = len(rsi15m_recent) >= 2 and rsi15m_recent[-1] > rsi15m_recent[0]

            vol_recent = sum(m15_v[-4:]) / 4 if len(m15_v) >= 4 else 0
            vol_prev   = sum(m15_v[-8:-4]) / 4 if len(m15_v) >= 8 else vol_recent
            vol_expanding = vol_prev > 0 and vol_recent > vol_prev * 1.3

            trend_15m_bull = bull_bars >= 5 and rsi15m_rising
            trend_15m_bear = bear_bars >= 5 and not rsi15m_rising

            # ── 支撑/阻力（72H极值）──
            support    = min(h1_l)
            resistance = max(h1_h)

            # ── 方向判断 ──
            if trend_1h_bull and trend_15m_bull:
                direction = 'BULLISH'
            elif trend_1h_bear and trend_15m_bear:
                direction = 'BEARISH'
            else:
                direction = 'NEUTRAL'

            # ── 置信度评分 ──
            confidence = 0
            reasons = []

            if direction != 'NEUTRAL':
                confidence += 30
                reasons.append(f"1H+15M方向一致({direction})")

            if adx1h > 30:
                confidence += 25
                reasons.append(f"ADX={adx1h:.1f}(强趋势)")
            elif adx1h > 20:
                confidence += 10
                reasons.append(f"ADX={adx1h:.1f}(弱趋势)")
            else:
                reasons.append(f"ADX={adx1h:.1f}(震荡)")

            if rsi1h < 40 or rsi1h > 60:
                confidence += 15
                reasons.append(f"RSI={rsi1h:.1f}(动能明确)")
            else:
                reasons.append(f"RSI={rsi1h:.1f}(中性)")

            if (direction == 'BULLISH' and macd_bullish) or (direction == 'BEARISH' and macd_bearish):
                confidence += 15
                reasons.append("MACD柱同向扩大")

            if vol_expanding:
                confidence += 15
                reasons.append("成交量放大>1.3x")

            confidence = min(confidence, 100)

            # ── 趋势描述 ──
            if trend_1h_bull:
                trend_1h_str = 'BULLISH'
            elif trend_1h_bear:
                trend_1h_str = 'BEARISH'
            else:
                trend_1h_str = 'NEUTRAL'

            if trend_15m_bull:
                trend_15m_str = 'BULLISH'
            elif trend_15m_bear:
                trend_15m_str = 'BEARISH'
            else:
                trend_15m_str = 'NEUTRAL'

            reasoning = ' | '.join(reasons)

            return {
                'symbol': symbol,
                'direction': direction,
                'confidence': confidence,
                'reasoning': reasoning,
                'trend_1h': trend_1h_str,
                'trend_15m': trend_15m_str,
                'rsi_1h': round(rsi1h, 2),
                'adx_1h': round(adx1h, 2),
                'key_level_support': round(support, 8),
                'key_level_resistance': round(resistance, 8),
            }

        except Exception as e:
            logger.error(f"[预测] {symbol} 分析失败: {e}")
            return None

    # ──────────────────────────────────────────
    # 虚拟回测：平仓 / 开仓
    # ──────────────────────────────────────────

    def _get_current_price(self, cursor, symbol: str) -> Optional[float]:
        """从 kline_data 取最新1M/5M收盘价作为当前价"""
        for tf in ('1m', '5m', '15m', '1h'):
            cursor.execute(
                "SELECT close_price FROM kline_data "
                "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 1",
                (symbol, tf)
            )
            row = cursor.fetchone()
            if row:
                return float(row['close_price'])
        return None

    def _close_open_backtests(self, cursor, now: datetime) -> int:
        """结算所有超过2.5小时的OPEN虚拟单，计算P&L"""
        cutoff = now - timedelta(hours=2, minutes=30)
        cursor.execute(
            "SELECT id, symbol, direction, entry_price FROM prediction_backtest "
            "WHERE status='OPEN' AND entry_time <= %s",
            (cutoff,)
        )
        rows = cursor.fetchall()
        closed = 0
        for row in rows:
            exit_price = self._get_current_price(cursor, row['symbol'])
            if exit_price is None:
                continue
            entry = float(row['entry_price'])
            if row['direction'] == 'BULLISH':
                pnl_pct = (exit_price - entry) / entry * 5 * 100
            else:
                pnl_pct = (entry - exit_price) / entry * 5 * 100
            pnl_usdt = pnl_pct / 100 * 100  # 100U 本金
            cursor.execute(
                "UPDATE prediction_backtest SET status='CLOSED', exit_price=%s, exit_time=%s, "
                "pnl_pct=%s, pnl_usdt=%s WHERE id=%s",
                (exit_price, now, round(pnl_pct, 4), round(pnl_usdt, 4), row['id'])
            )
            closed += 1
        return closed

    def _open_new_backtests(self, cursor, results: List[Dict], now: datetime) -> int:
        """对 BULLISH/BEARISH 且 confidence>=40 的每个交易对各开一个虚拟单"""
        opened = 0
        for r in results:
            if r['direction'] == 'NEUTRAL' or r['confidence'] < 40:
                continue
            entry_price = self._get_current_price(cursor, r['symbol'])
            if entry_price is None:
                continue
            try:
                cursor.execute(
                    "INSERT INTO prediction_backtest "
                    "(symbol, direction, confidence, entry_price, entry_time) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (r['symbol'], r['direction'], r['confidence'], entry_price, now)
                )
                opened += 1
            except Exception as e:
                logger.error(f"[回测] {r['symbol']} 开虚拟单失败: {e}")
        return opened

    # ──────────────────────────────────────────
    # 批量运行 + 存储
    # ──────────────────────────────────────────

    def run_all(self, symbols: List[str]) -> int:
        now = datetime.utcnow()
        valid_until = now + timedelta(hours=4)
        saved = 0
        all_results = []

        conn = self._get_conn()
        cursor = conn.cursor()

        # ① 先结算上一轮到期的虚拟单
        try:
            closed = self._close_open_backtests(cursor, now)
            if closed:
                conn.commit()
                # 打印近期回测统计
                cursor.execute(
                    "SELECT COUNT(*) AS cnt, "
                    "SUM(CASE WHEN pnl_usdt>0 THEN 1 ELSE 0 END) AS wins, "
                    "SUM(pnl_usdt) AS total_pnl "
                    "FROM prediction_backtest WHERE status='CLOSED' "
                    "AND exit_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)"
                )
                stat = cursor.fetchone()
                cnt = stat['cnt'] or 0
                wins = stat['wins'] or 0
                total_pnl = stat['total_pnl'] or 0.0
                win_rate = wins / cnt * 100 if cnt > 0 else 0
                logger.info(
                    f"[回测] 结算{closed}单 | 近7日: {cnt}单 胜率{win_rate:.1f}% 总PnL={total_pnl:+.2f}U"
                )
        except Exception as e:
            logger.error(f"[回测] 结算虚拟单失败: {e}")

        # ② 运行预测
        for symbol in symbols:
            result = self.analyze(symbol)
            if not result:
                continue
            all_results.append(result)
            try:
                cursor.execute("""
                    INSERT INTO market_prediction
                        (symbol, prediction_time, direction, confidence, reasoning,
                         trend_1h, trend_15m, rsi_1h, adx_1h,
                         key_level_support, key_level_resistance, valid_until)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        prediction_time=%s, direction=%s, confidence=%s, reasoning=%s,
                        trend_1h=%s, trend_15m=%s, rsi_1h=%s, adx_1h=%s,
                        key_level_support=%s, key_level_resistance=%s, valid_until=%s
                """, (
                    symbol, now, result['direction'], result['confidence'], result['reasoning'],
                    result['trend_1h'], result['trend_15m'], result['rsi_1h'], result['adx_1h'],
                    result['key_level_support'], result['key_level_resistance'], valid_until,
                    now, result['direction'], result['confidence'], result['reasoning'],
                    result['trend_1h'], result['trend_15m'], result['rsi_1h'], result['adx_1h'],
                    result['key_level_support'], result['key_level_resistance'], valid_until,
                ))
                saved += 1
            except Exception as e:
                logger.error(f"[预测] {symbol} 存储失败: {e}")

        # ③ 开新的虚拟单
        try:
            opened = self._open_new_backtests(cursor, all_results, now)
            if opened:
                logger.info(f"[回测] 新开{opened}个虚拟单（100U x5）")
        except Exception as e:
            logger.error(f"[回测] 开虚拟单失败: {e}")

        conn.commit()
        cursor.close()
        conn.close()

        logger.info(f"[预测] 完成 {saved}/{len(symbols)} 个交易对分析，有效期至 {valid_until.strftime('%H:%M')} UTC")
        return saved
