"""
Gemini 顾问 — 模拟仓开仓审核 + 持仓监管

开仓顾问:
  - 所有 account_id=2 模拟开仓前审核 (gate_simulated_open / paper_open_gate)
  - decision=reject 则不开仓; 开关 gemini_open_advisor_enabled

持仓顾问:
  - 所有模拟持仓 >= 30min 每 15min 问 Gemini (hold/observe/sell)
  - sell 关模拟仓; 有实盘则同步平仓 (需 live_close_enabled)
  - 开关 gemini_position_advisor_enabled (默认开)
  - smart_trader 主循环每 15min 调 tick()
"""
from __future__ import annotations

import datetime
import json
import os
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.gemini_advisor_reviews import log_advisor_review
from app.services.open_advisor_routing import is_gemini_order_source
from app.services.open_advisor_routing import should_use_gemini_hold_advisor
from app.services.open_advisor_strategy_rubrics import (
    build_open_advisor_prompt,
    check_direction_gates,
    check_expected_side,
    precheck_open_advisor,
    resolve_strategy_profile,
)


GEMINI_TIMEOUT_MS = 180_000
HOLD_MIN_MINUTES = 30           # 持仓满 30min 纳入监管
HOLD_MIN_HOURS = HOLD_MIN_MINUTES / 60.0
HOLD_CHECK_INTERVAL_S = 900     # 同仓 15min 内不重复问
GEMINI_PER_CALL_DELAY_S = 1.0   # 防 Gemini rate limit
HOLD_15M_BARS = 6               # 持仓顾问：近 4~6 根 15m
HOLD_1H_BARS = 4                # 持仓顾问：近 4 根 1h
HOLD_LOSS_MILD_ROI = -5.0       # 保证金 ROI %，轻微亏损
HOLD_LOSS_MODERATE_ROI = -12.0  # 中度亏损
HOLD_LOSS_SEVERE_ROI = -15.0    # 严重亏损（近策略 SL）


def _normalize_symbol_for_db(symbol: str) -> str:
    """kline_data / candidate_pool 存 BTC/USDT；部分模拟仓为 BTCUSDT."""
    s = (symbol or "").strip()
    if not s or "/" in s:
        return s
    if s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    if s.endswith("USD"):
        return f"{s[:-3]}/USD"
    return s


_open_advisor_singleton: Optional["GeminiPositionAdvisor"] = None


def get_open_advisor() -> "GeminiPositionAdvisor":
    global _open_advisor_singleton
    if _open_advisor_singleton is None:
        from app.utils.config_loader import get_db_config
        _open_advisor_singleton = GeminiPositionAdvisor(get_db_config())
    return _open_advisor_singleton


class GeminiPositionAdvisor:
    """模拟仓 Gemini 顾问：问 Gemini 是否继续持有，sell 则关模拟仓自动同步实盘"""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self._client = None
        # {position_id: last_check_unix_ts}
        self._last_check_ts: Dict[int, float] = {}

    def _advisor_rules(self, source: str) -> Optional[Tuple[float, int]]:
        """返回 (min_hold_hours, check_interval_s); None 表示持仓顾问关闭."""
        if not self._is_hold_advisor_enabled():
            return None
        return (HOLD_MIN_HOURS, HOLD_CHECK_INTERVAL_S)

    # ────────────────────────────────────────────────────────
    # 开关读取
    # ────────────────────────────────────────────────────────

    def _read_setting_bool(self, key: str, default: str = '1') -> bool:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT setting_value FROM system_settings "
                "WHERE setting_key=%s LIMIT 1",
                (key,),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if not row:
                val = default
            else:
                val = str(row.get('setting_value', default)).strip().lower()
            return val in ('1', 'true', 'yes', 'on')
        except Exception:
            return default in ('1', 'true', 'yes', 'on')

    def _is_hold_advisor_enabled(self) -> bool:
        return self._read_setting_bool('gemini_position_advisor_enabled', '1')

    def _is_open_advisor_enabled(self) -> bool:
        return self._read_setting_bool('gemini_open_advisor_enabled', '1')

    def _read_direction_gates(self) -> Tuple[bool, bool]:
        return (
            self._read_setting_bool('allow_long', '1'),
            self._read_setting_bool('allow_short', '1'),
        )

    # ────────────────────────────────────────────────────────
    # DB
    # ────────────────────────────────────────────────────────

    def _get_conn(self):
        return pymysql.connect(
            **self.db_config, charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor, autocommit=True
        )

    # ────────────────────────────────────────────────────────
    # Gemini client
    # ────────────────────────────────────────────────────────

    def _init_client(self):
        if self._client is not None:
            return self._client
        api_key = os.getenv('GEMINI_API_KEY', '')
        if not api_key:
            logger.warning("[Gemini顾问] GEMINI_API_KEY 未配置,跳过")
            return None
        try:
            from google import genai
            self._client = genai.Client(api_key=api_key)
            logger.info("[Gemini顾问] client 已就绪")
            return self._client
        except ImportError:
            logger.warning("[Gemini顾问] google-genai 未安装")
            return None
        except Exception as e:
            logger.error(f"[Gemini顾问] client 初始化失败: {e}")
            return None

    # ────────────────────────────────────────────────────────
    # 数据准备
    # ────────────────────────────────────────────────────────

    def get_eligible_positions(self) -> List[Dict]:
        """
        查模拟仓 (futures_positions) U本位 OPEN 单。
        只查 account_id=2; tick() 内再按持仓时长与节流过滤。
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, account_id, symbol, position_side, entry_price,
                       quantity, leverage, margin, open_time, source,
                       TIMESTAMPDIFF(MINUTE, open_time, NOW())/60.0 AS hold_hours
                FROM futures_positions
                WHERE status='open'
                  AND account_id = 2
                  AND TIMESTAMPDIFF(MINUTE, open_time, NOW()) >= %s
                  AND (source IS NULL OR LOWER(source) NOT LIKE 'deepseek_%%')
                ORDER BY open_time ASC
                """,
                (HOLD_MIN_MINUTES,)
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            return rows
        except Exception as e:
            logger.error(f"[Gemini顾问] 查模拟仓失败: {e}")
            return []

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """取当前价: 优先 5m K 线 (最近 15 分钟内有数据)"""
        symbol = _normalize_symbol_for_db(symbol)
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT close_price, open_time FROM kline_data "
                "WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 1",
                (symbol,)
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if not row:
                return None
            age_min = (datetime.datetime.now().timestamp() - row['open_time'] / 1000) / 60
            if age_min > 15:
                logger.warning(f"[Gemini顾问] {symbol} 5m K线 {age_min:.0f}min 旧,跳过")
                return None
            return float(row['close_price'])
        except Exception as e:
            logger.warning(f"[Gemini顾问] {symbol} 取价失败: {e}")
            return None

    def _fetch_market_context(self, symbol: str) -> dict:
        """近 4h 15m K 线 + 近 24 根 1h + candidate_pool 叙事 + Big4 + 方向闸门."""
        symbol = _normalize_symbol_for_db(symbol)
        ctx = {
            'klines_15m': [],
            'klines_1h': [],
            'big4_signal': 'NEUTRAL',
            'big4_strength': 0,
            'btc_6h_change': 0.0,
            'eth_6h_change': 0.0,
            'narrative_1h': '',
            'narrative_15m': '',
            'rsi_14_1h': None,
            'below_7d_high_pct': None,
            'above_7d_low_pct': None,
            'allow_long': True,
            'allow_short': True,
        }
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT open_time, open_price, high_price, low_price, close_price, volume "
                "FROM kline_data "
                "WHERE symbol=%s AND timeframe='15m' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 16",
                (symbol,)
            )
            rows = cur.fetchall()
            klines = []
            for r in reversed(rows):
                t = datetime.datetime.utcfromtimestamp(r['open_time'] / 1000)
                klines.append({
                    't': t.strftime('%m-%d %H:%M'),
                    'o': round(float(r['open_price']), 8),
                    'h': round(float(r['high_price']), 8),
                    'l': round(float(r['low_price']), 8),
                    'c': round(float(r['close_price']), 8),
                    'v': round(float(r['volume'] or 0), 2),
                })
            ctx['klines_15m'] = klines

            cur.execute(
                "SELECT open_time, open_price, high_price, low_price, close_price, volume "
                "FROM kline_data "
                "WHERE symbol=%s AND timeframe='1h' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 24",
                (symbol,)
            )
            rows_1h = cur.fetchall()
            klines_1h = []
            for r in reversed(rows_1h):
                t = datetime.datetime.utcfromtimestamp(r['open_time'] / 1000)
                klines_1h.append({
                    't': t.strftime('%m-%d %H:%M'),
                    'o': round(float(r['open_price']), 8),
                    'h': round(float(r['high_price']), 8),
                    'l': round(float(r['low_price']), 8),
                    'c': round(float(r['close_price']), 8),
                    'v': round(float(r['volume'] or 0), 2),
                })
            ctx['klines_1h'] = klines_1h

            cur.execute(
                "SELECT narrative_1h, narrative_15m, rsi_14, "
                "below_7d_high_pct, above_7d_low_pct "
                "FROM data_cache.candidate_pool_snapshot "
                "WHERE symbol=%s AND exchange='binance_futures' LIMIT 1",
                (symbol,)
            )
            pool = cur.fetchone()
            if pool:
                ctx['narrative_1h'] = (pool.get('narrative_1h') or '')[:2000]
                ctx['narrative_15m'] = (pool.get('narrative_15m') or '')[:1200]
                if pool.get('rsi_14') is not None:
                    ctx['rsi_14_1h'] = float(pool['rsi_14'])
                if pool.get('below_7d_high_pct') is not None:
                    ctx['below_7d_high_pct'] = float(pool['below_7d_high_pct'])
                if pool.get('above_7d_low_pct') is not None:
                    ctx['above_7d_low_pct'] = float(pool['above_7d_low_pct'])

            cur.execute(
                "SELECT overall_signal, signal_strength, btc_price_change_6h, eth_price_change_6h "
                "FROM big4_trend_history ORDER BY created_at DESC LIMIT 1"
            )
            big4 = cur.fetchone()
            if big4:
                ctx['big4_signal'] = big4.get('overall_signal') or 'NEUTRAL'
                ctx['big4_strength'] = float(big4.get('signal_strength') or 0)
                ctx['btc_6h_change'] = float(big4.get('btc_price_change_6h') or 0)
                ctx['eth_6h_change'] = float(big4.get('eth_price_change_6h') or 0)

            cur.close()
            conn.close()
        except Exception as e:
            logger.warning(f"[Gemini顾问] 取市场上下文 {symbol} 失败: {e}")

        allow_long, allow_short = self._read_direction_gates()
        ctx['allow_long'] = allow_long
        ctx['allow_short'] = allow_short
        return ctx

    # ────────────────────────────────────────────────────────
    # Prompt + Gemini call
    # ────────────────────────────────────────────────────────

    @staticmethod
    def _score_klines_for_side(klines: list, side: str) -> dict:
        """客观统计 K 线对持仓方向的支持/反向根数（供 prompt + 代码复核）."""
        empty = {
            "for": 0, "against": 0, "last3": "", "trail_against": 0,
            "summary": "insufficient data",
        }
        if not klines:
            return empty
        dirs: List[str] = []
        for_count = 0
        against_count = 0
        for k in klines:
            o, c = float(k['o']), float(k['c'])
            if c > o:
                d = "G"
            elif c < o:
                d = "R"
            else:
                d = "D"
            dirs.append(d)
            if side == 'LONG':
                if d == "G":
                    for_count += 1
                elif d == "R":
                    against_count += 1
            else:
                if d == "R":
                    for_count += 1
                elif d == "G":
                    against_count += 1
        want = "G" if side == 'LONG' else "R"
        trail_against = 0
        for d in reversed(dirs):
            if d == "D":
                continue
            if d != want:
                trail_against += 1
            else:
                break
        last3 = "".join(dirs[-3:])
        summary = (
            f"with_trend={for_count} against={against_count} "
            f"last3={last3} trail_against={trail_against}"
        )
        return {
            "for": for_count,
            "against": against_count,
            "last3": last3,
            "trail_against": trail_against,
            "summary": summary,
        }

    @staticmethod
    def _loss_tier_label(roi_pct: float) -> str:
        if roi_pct >= 0:
            return "profit/breakeven"
        if roi_pct > HOLD_LOSS_MILD_ROI:
            return "mild_loss"
        if roi_pct > HOLD_LOSS_MODERATE_ROI:
            return "moderate_loss"
        return "severe_loss"

    @staticmethod
    def _loss_tier_rules(roi_pct: float, side: str) -> str:
        tier = GeminiPositionAdvisor._loss_tier_label(roi_pct)
        if tier == "profit/breakeven":
            return (
                "## PnL tier: profit / breakeven\n"
                "- **hold** only if 15m+1h still support the position side; clear reversal → **sell** to lock profit.\n"
                "- ROI ≥ +20% with 15m reversal pattern → prefer **sell**."
            )
        if tier == "mild_loss":
            return (
                "## PnL tier: mild loss (ROI > -5%)\n"
                "- Do **not** sell on small loss alone; need 15m majority against + ≥2 confirming 1h bars.\n"
                "- If K-lines still support side → **hold**; mixed → **observe** (avoid weak hold)."
            )
        if tier == "moderate_loss":
            return (
                "## PnL tier: moderate loss (-12% < ROI ≤ -5%)\n"
                "- **Higher hold bar**: 15m with_trend ≥ against and last 2x1h still support "
                f"{side} (cite bars in reason).\n"
                "- 15m trail_against ≥3 or 1h against ≥2 → lean **sell**; unclear → **observe** (not hold).\n"
                "- Big4 cannot replace K-line evidence."
            )
        return (
            "## PnL tier: severe loss (ROI ≤ -12%)\n"
            "- **Default stop-loss bias**: unless 15m shows clear stabilization/reversal "
            f"(e.g. long lower wick + bullish engulf for LONG), prefer **sell** or **observe**, rarely **hold**.\n"
            "- **hold** only if: (1) 15m with_trend ≥ against and trail_against ≤1; "
            "(2) last 2x1h not extending loss direction; (3) reason cites table bars.\n"
            "- ROI ≤ -15% with no 15m stabilization → **must sell**."
        )

    @staticmethod
    def _temper_losing_hold(
        roi_pct: float,
        action: str,
        reason: str,
        side: str,
        s15: dict,
        s1h: dict,
    ) -> Tuple[str, str]:
        """亏损单：Gemini 若建议 hold，用客观 K 线统计复核，防止「死扛」."""
        if action != 'hold' or roi_pct >= 0:
            return action, reason

        override = ""
        if roi_pct <= HOLD_LOSS_SEVERE_ROI:
            if s15.get("trail_against", 0) >= 2 or s15.get("against", 0) >= 4:
                action = 'sell'
                override = "severe_loss+15m_against"
            elif s15.get("for", 0) < s15.get("against", 0):
                action = 'observe'
                override = "severe_loss+15m_no_base"
        elif roi_pct <= HOLD_LOSS_MODERATE_ROI:
            if (
                s15.get("against", 0) > s15.get("for", 0)
                and s15.get("trail_against", 0) >= 2
            ):
                action = 'sell'
                override = "moderate_loss+15m_trail_against"
            elif s15.get("for", 0) < 2 and s1h.get("against", 0) >= 2:
                action = 'observe'
                override = "moderate_loss+1h_15m_weak"
        elif roi_pct <= HOLD_LOSS_MILD_ROI:
            if (
                s15.get("against", 0) >= s15.get("for", 0) + 2
                and s1h.get("against", 0) >= 2
            ):
                action = 'observe'
                override = "mild_loss+kline_against"

        if override:
            reason = f"{reason[:80]}|review:{override}"[:200]
            logger.info(f"[Gemini advisor] losing hold review → {action} ({override})")
        return action, reason

    @staticmethod
    def _recent_klines(klines: list, n: int) -> list:
        if not klines or n <= 0:
            return klines or []
        return klines[-n:]

    @staticmethod
    def _build_prompt(position: dict, current_price: float, ctx: dict) -> str:
        entry = float(position['entry_price'])
        leverage = int(position['leverage'])
        side = position['position_side']
        symbol = position['symbol']
        hold_h = float(position['hold_hours'])
        source = position.get('source', 'unknown')

        if side == 'LONG':
            price_change_pct = (current_price - entry) / entry * 100
        else:
            price_change_pct = (entry - current_price) / entry * 100
        roi_pct = price_change_pct * leverage

        k15 = GeminiPositionAdvisor._recent_klines(
            ctx.get('klines_15m', []), HOLD_15M_BARS,
        )
        k1h = GeminiPositionAdvisor._recent_klines(
            ctx.get('klines_1h', []), HOLD_1H_BARS,
        )
        klines_15m_str = GeminiPositionAdvisor._format_kline_table(k15)
        klines_1h_str = GeminiPositionAdvisor._format_kline_table(k1h)
        s15 = GeminiPositionAdvisor._score_klines_for_side(k15, side)
        s1h = GeminiPositionAdvisor._score_klines_for_side(k1h, side)
        loss_rules = GeminiPositionAdvisor._loss_tier_rules(roi_pct, side)
        loss_tier = GeminiPositionAdvisor._loss_tier_label(roi_pct)

        rsi_s = ctx.get('rsi_14_1h')
        rsi_line = f"RSI(1h): {rsi_s:.1f}" if rsi_s is not None else "RSI(1h): N/A"
        big4 = ctx.get('big4_signal', 'NEUTRAL')
        big4_strength = ctx.get('big4_strength', 0)
        btc_6h = ctx.get('btc_6h_change', 0)
        eth_6h = ctx.get('eth_6h_change', 0)

        side_label = f"{side} (LONG)" if side == 'LONG' else f"{side} (SHORT)"
        return f"""You are a **paper position** supervisor. Decide **hold**, **observe**, or **sell** from **this coin's recent K-line structure**.

## Invalid reasons (reject vague answers)
- Do **not** decide mainly from Big4/BTC/ETH macro alone
- Do **not** use vague sentiment ("feels weak", "market bad"); **reason** must cite 15m/1h table patterns
- If K-lines conflict with position side, prioritize K-lines; Big4 alone cannot trigger sell

## Position
  Symbol:          {symbol}
  Direction:       {side_label}
  Entry:           {entry}
  Current:         {current_price}
  Leverage:        {leverage}x
  Hold:            {hold_h:.1f}h
  Price change:    {price_change_pct:+.2f}%
  ROI on margin:   {roi_pct:+.2f}%  (tier: {loss_tier})
  Source:          {source}
  {rsi_line}

## Objective stats (must match reason)
  15m ({HOLD_15M_BARS} bars): {s15['summary']}
  1h ({HOLD_1H_BARS} bars):  {s1h['summary']}

{loss_rules}

## K-line rules (primary)
1. **Last {HOLD_1H_BARS}x1h** (table): still supports {side}?
   - LONG: higher highs/lows or last 2x1h still bullish (green bodies / lower-wick support)
   - SHORT: lower highs/lows or last 2x1h still bearish (red bodies / upper-wick rejection)
2. **Last {HOLD_15M_BARS}x15m** (table): short-term **reversal** against the position?
   - Count streaks, engulfing, long wicks, volume on reversal bars
3. Example reason: "last 5x15m four red bars break prior low + last 2x1h red bodies"

## Last {HOLD_1H_BARS}x1h K-lines (oldest → newest)
{klines_1h_str}

## Last {HOLD_15M_BARS}x15m K-lines (oldest → newest)
{klines_15m_str}

## Macro (secondary)
  Big4: {big4} (strength {big4_strength:.0f}) | BTC 6h {btc_6h:+.2f}% | ETH 6h {eth_6h:+.2f}%
  Cite Big4 only when K-lines already show reversal; never sell on Big4 alone.

## Decision
- **hold**: last {HOLD_1H_BARS}x1h + {HOLD_15M_BARS}x15m still support {side}, no clear reversal
  (losing positions need higher bar; moderate/severe loss without stabilization → not hold)
- **observe**: mixed 15m/1h, chop, or insufficient data (mild/moderate loss + unclear → prefer observe)
- **sell**: when (A) {HOLD_15M_BARS}x15m majority against position (cite pattern), AND
  (B) last 2x1h confirm reversal; OR severe loss without 15m base; OR ROI≤-15% no 15m reversal;
  OR ROI≥+20% with clear 15m reversal (take profit)

Deeper loss needs bar-by-bar evidence to hold. Unclear → **observe**; severe loss → **sell** unless 15m reverses.

Output ONLY JSON:
{{
  "action": "hold" | "observe" | "sell",
  "reason": "<=120 English words; must cite 15m/1h patterns, not Big4 alone>"
}}
"""

    @staticmethod
    def _format_kline_table(klines: list) -> str:
        out = "  time           open         high          low        close       volume\n"
        for k in klines:
            out += (
                f"  {k['t']}  {k['o']:>11}  {k['h']:>11}  {k['l']:>11}  "
                f"{k['c']:>11}  {k['v']:>11}\n"
            )
        return out or "  (no data)\n"

    @staticmethod
    def _build_open_prompt(
        symbol: str,
        side: str,
        price: float,
        source: str,
        catalyst: str,
        leverage: int,
        sl_pct: Optional[float],
        tp_pct: Optional[float],
        hold_hours: Optional[float],
        ctx: dict,
    ) -> str:
        profile = resolve_strategy_profile(source)
        return build_open_advisor_prompt(
            profile=profile,
            symbol=symbol,
            side=side,
            price=price,
            source=source,
            catalyst=catalyst,
            leverage=leverage,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            hold_hours=hold_hours,
            ctx=ctx,
            format_kline_table=GeminiPositionAdvisor._format_kline_table,
        )

    def review_open(
        self,
        symbol: str,
        side: str,
        price: float,
        source: str,
        catalyst: str = "",
        leverage: int = 5,
        sl_pct: Optional[float] = None,
        tp_pct: Optional[float] = None,
        hold_hours: Optional[float] = None,
        conn=None,
    ) -> Tuple[bool, str]:
        """返回 (允许开仓, 原因). reject 时禁止开仓."""
        if not is_gemini_order_source(source):
            return True, "non_gemini_source_skip"
        if not self._is_open_advisor_enabled():
            return True, "open_advisor_disabled"

        profile = resolve_strategy_profile(source)
        allow_long, allow_short = self._read_direction_gates()
        ok, gate_reason = check_direction_gates(side, allow_long, allow_short)
        if not ok:
            log_advisor_review(
                "open", "reject", symbol,
                position_side=side, source=source, entry_price=price,
                leverage=leverage, reason=gate_reason, catalyst=catalyst, conn=conn,
            )
            return False, gate_reason

        ok_side, side_reason = check_expected_side(profile, side)
        if not ok_side:
            log_advisor_review(
                "open", "reject", symbol,
                position_side=side, source=source, entry_price=price,
                leverage=leverage, reason=side_reason, catalyst=catalyst, conn=conn,
            )
            return False, side_reason

        ctx = self._fetch_market_context(symbol)
        ok_pre, pre_reason = precheck_open_advisor(profile, side, ctx)
        if not ok_pre:
            log_advisor_review(
                "open", "reject", symbol,
                position_side=side, source=source, entry_price=price,
                leverage=leverage, reason=pre_reason, catalyst=catalyst, conn=conn,
            )
            return False, pre_reason

        prompt = self._build_open_prompt(
            symbol, side, price, source, catalyst, leverage,
            sl_pct, tp_pct, hold_hours, ctx,
        )
        result = self._call_gemini(prompt, open_mode=True)
        if not result:
            log_advisor_review(
                "open", "approve", symbol,
                position_side=side, source=source, entry_price=price,
                leverage=leverage, reason="gemini_api_error_allow",
                catalyst=catalyst, conn=conn,
            )
            return True, "gemini_api_error_allow"

        decision = str(result.get("decision", "approve")).lower()
        reason = str(result.get("reason", ""))[:500]
        approved = decision == "approve"
        log_advisor_review(
            "open",
            "approve" if approved else "reject",
            symbol,
            position_side=side,
            source=source,
            entry_price=price,
            leverage=leverage,
            reason=reason,
            catalyst=catalyst,
            conn=conn,
        )
        if not approved:
            logger.info(
                f"[开仓顾问] REJECT {symbol} {side} @ {price} "
                f"src={source} | {reason[:80]}"
            )
        return approved, reason

    def _call_gemini(self, prompt: str, open_mode: bool = False) -> Optional[dict]:
        client = self._init_client()
        if not client:
            return None
        text = ''
        try:
            from google.genai import types
            cfg = types.GenerateContentConfig(
                response_mime_type='application/json',
                http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
            )
            model_name = os.getenv('GEMINI_MODEL', 'gemini-3-flash-preview')
            resp = client.models.generate_content(
                model=model_name, contents=prompt, config=cfg,
            )
            text = (resp.text or '').strip()
            from app.services.ai_explore_prompt import _extract_llm_json_text, _try_parse_json
            parsed, _ = _try_parse_json(_extract_llm_json_text(text))
            if parsed is None:
                logger.warning(f"[Gemini顾问] 返回非 JSON: {text[:200]}")
                return None
            sig = parsed
            if open_mode:
                decision = str(sig.get('decision', '')).strip().lower()
                if decision not in ('approve', 'reject'):
                    logger.warning(
                        f"[开仓顾问] 非法 decision={decision}, 降级 approve"
                    )
                    decision = 'approve'
                return {
                    'decision': decision,
                    'reason': str(sig.get('reason', ''))[:500],
                }
            action = str(sig.get('action', '')).strip().lower()
            if action not in ('hold', 'observe', 'sell'):
                logger.warning(f"[Gemini顾问] 非法 action={action} text={text[:200]},降级 observe")
                action = 'observe'
            return {
                'action': action,
                'reason': str(sig.get('reason', ''))[:500],
            }
        except json.JSONDecodeError:
            logger.warning(f"[Gemini顾问] 返回非 JSON: {text[:200]}")
            return None
        except Exception as e:
            logger.warning(f"[Gemini顾问] Gemini API 异常: {e}")
            return None

    # ────────────────────────────────────────────────────────
    # 实盘平仓
    # ────────────────────────────────────────────────────────

    def _close_paper_only(
        self, position: dict, reason: str, close_price: float,
        advisor_tag: str = "gemini_advisor",
    ) -> bool:
        """仅关模拟仓（走 FuturesTradingEngine，写入 futures_trades / 更新账户）"""
        try:
            from app.trading.futures_trading_engine import FuturesTradingEngine

            close_note = f'{advisor_tag}({reason[:120]})'
            engine = FuturesTradingEngine(self.db_config)
            result = engine.close_position(
                position['id'],
                reason=close_note,
                close_price=Decimal(str(close_price)),
            )
            if result.get('success'):
                if not result.get('already_closed'):
                    logger.info(
                        f"[Gemini顾问] 模拟仓已关闭 id={position['id']} "
                        f"{position['symbol']} {position['position_side']} @ {close_price}"
                    )
                return True
            logger.warning(
                f"[Gemini顾问] 模拟仓平仓未成功 id={position['id']}: "
                f"{result.get('message', result)}"
            )
            return False
        except Exception as e:
            logger.error(f"[Gemini顾问] 关模拟仓异常 id={position['id']}: {e}")
            return False

    def _close_live_position(
        self, position: dict, reason: str, advisor_tag: str = "gemini_advisor",
    ) -> bool:
        """
        关闭模拟仓 + 主动平实盘。
        无论如何模拟仓都会关（实盘平成功与否都不影响模拟仓关仓）。
        """
        close_price = None
        live_pnl = None
        live_row = None

        try:
            # ---- 1. 找对应的实盘记录 ----
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, account_id FROM live_futures_positions "
                "WHERE paper_position_id=%s AND status='OPEN' LIMIT 1",
                (position['id'],)
            )
            live_row = cur.fetchone()
            cur.close(); conn.close()
        except Exception as e:
            logger.warning(f"[Gemini顾问] 查实盘记录异常 id={position['id']}: {e}")

        # ---- 2. 尝试平实盘 ----
        if live_row:
            try:
                from app.services.api_key_service import APIKeyService
                from app.trading.binance_futures_engine import BinanceFuturesEngine

                svc = APIKeyService(self.db_config)
                keys = svc.get_all_active_api_keys('binance')
                target_key = next((k for k in keys if k['id'] == live_row['account_id']), None)

                if target_key:
                    engine = BinanceFuturesEngine(
                        self.db_config,
                        api_key=target_key['api_key'],
                        api_secret=target_key['api_secret'],
                    )
                    result = engine.close_position_direct(
                        symbol=position['symbol'],
                        position_side=position['position_side'],
                        quantity=Decimal(str(position['quantity'])),
                        entry_price=Decimal(str(position['entry_price'])),
                        reason=reason,
                    )
                    if result.get('success'):
                        close_price = result.get('close_price', 0)
                        live_pnl = result.get('realized_pnl', 0)
                        close_price_f = float(close_price) if close_price else None

                        # ---- 3. 更新 live_futures_positions ----
                        conn2 = self._get_conn()
                        cur2 = conn2.cursor()
                        cur2.execute(
                            """UPDATE live_futures_positions
                               SET status='CLOSED',
                                   close_time=NOW(),
                                   close_price=%s,
                                   realized_pnl=%s,
                                   close_reason=%s,
                                   notes=CONCAT(IFNULL(notes,''),%s,%s)
                               WHERE id=%s AND status='OPEN'""",
                            (
                                close_price_f, live_pnl, advisor_tag,
                                f'|{advisor_tag}:', reason, live_row['id'],
                            )
                        )
                        if cur2.rowcount > 0:
                            logger.info(
                                f"[Gemini顾问] live_futures_positions 已关闭 id={live_row['id']} "
                                f"{position['symbol']} pnl={live_pnl}"
                            )
                        cur2.close(); conn2.close()

                        # ---- 4. Telegram ----
                        try:
                            from app.services.trade_notifier import get_trade_notifier
                            notif = get_trade_notifier()
                            if notif:
                                notif.send_message(
                                    f"[{advisor_tag} SELL] {position['symbol']} {position['position_side']} "
                                    f"已平仓 pnl={live_pnl}U\nreason={reason[:60]}"
                                )
                        except Exception:
                            pass

                        # 实盘平成功，用实盘成交价关模拟仓
                        self._close_paper_only(
                            position, reason, close_price_f or float(position['entry_price']),
                            advisor_tag=advisor_tag,
                        )
                        return True
                    else:
                        logger.error(
                            f"[Gemini顾问] 平实盘失败 id={live_row['id']}: {result.get('error', '')}"
                        )
                else:
                    logger.warning(
                        f"[Gemini顾问] account_id={live_row['account_id']} 无活跃 API key, 跳过平实盘"
                    )
            except Exception as e:
                logger.error(f"[Gemini顾问] 平实盘异常 id={live_row['id'] if live_row else '?'}: {e}")

        # ---- 5. 实盘平失败 / 无实盘记录，只关模拟仓 ----
        if not close_price:
            current_price = self._get_current_price(position['symbol'])
            close_price = current_price or float(position['entry_price'])
        self._close_paper_only(position, reason, close_price, advisor_tag=advisor_tag)
        return True

    # ────────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────────

    def tick(self) -> dict:
        """
        外部每 15 min 调一次; 模拟仓持仓 >=30min 按 15min/position 节流。
        Returns 统计 dict {'evaluated', 'hold', 'observe', 'sell', 'skipped', 'errors'}
        """
        stats = {'evaluated': 0, 'hold': 0, 'observe': 0, 'sell': 0,
                 'skipped': 0, 'errors': 0, 'closed': 0}

        positions = self.get_eligible_positions()
        if not positions:
            return stats

        logger.info(f"[Gemini顾问] tick 开始,候选 {len(positions)} 模拟单")

        now = time.time()
        for pos in positions:
            if not should_use_gemini_hold_advisor(pos.get('source') or ''):
                stats['skipped'] += 1
                continue
            rules = self._advisor_rules(pos.get('source') or '')
            if not rules:
                stats['skipped'] += 1
                continue
            min_hold_h, interval_s = rules
            hold_h = float(pos.get('hold_hours') or 0)
            if hold_h < min_hold_h:
                stats['skipped'] += 1
                continue

            pid = int(pos['id'])
            last = self._last_check_ts.get(pid)
            if last and (now - last) < interval_s:
                stats['skipped'] += 1
                continue
            self._last_check_ts[pid] = now

            try:
                current_price = self._get_current_price(pos['symbol'])
                if not current_price:
                    stats['errors'] += 1
                    continue
                pos['current_price'] = current_price

                ctx = self._fetch_market_context(pos['symbol'])
                prompt = self._build_prompt(pos, current_price, ctx)
                decision = self._call_gemini(prompt)
                if not decision:
                    stats['errors'] += 1
                    continue

                entry = float(pos['entry_price'])
                if pos['position_side'] == 'LONG':
                    pct = (current_price - entry) / entry * 100
                else:
                    pct = (entry - current_price) / entry * 100
                roi = pct * int(pos['leverage'])

                k15 = self._recent_klines(ctx.get('klines_15m', []), HOLD_15M_BARS)
                k1h = self._recent_klines(ctx.get('klines_1h', []), HOLD_1H_BARS)
                s15 = self._score_klines_for_side(k15, pos['position_side'])
                s1h = self._score_klines_for_side(k1h, pos['position_side'])

                action = decision['action']
                reason = decision['reason']
                action, reason = self._temper_losing_hold(
                    roi, action, reason, pos['position_side'], s15, s1h,
                )
                stats[action] += 1
                stats['evaluated'] += 1

                logger.info(
                    f"[Gemini顾问] {action.upper():8s} id={pos['id']} {pos['symbol']:15s} "
                    f"{pos['position_side']:5s} hold={pos['hold_hours']:.1f}h ROI={roi:+.1f}% "
                    f"| {reason[:60]}"
                )

                log_advisor_review(
                    "hold",
                    action,
                    pos["symbol"],
                    position_side=pos.get("position_side"),
                    source=pos.get("source"),
                    position_id=int(pos["id"]),
                    entry_price=float(pos["entry_price"]),
                    leverage=int(pos.get("leverage") or 5),
                    hold_hours=hold_h,
                    roi_pct=round(roi, 2),
                    reason=reason,
                )

                if action == 'sell':
                    closed = self._close_live_position(
                        pos, f"gemini_advisor:{reason[:50]}"
                    )
                    if closed:
                        stats['closed'] += 1

                if GEMINI_PER_CALL_DELAY_S > 0:
                    time.sleep(GEMINI_PER_CALL_DELAY_S)

            except Exception as e:
                logger.error(f"[Gemini顾问] 处理 id={pos['id']} 异常: {e}")
                stats['errors'] += 1

        logger.info(f"[Gemini顾问] tick 完成: {stats}")
        return stats
