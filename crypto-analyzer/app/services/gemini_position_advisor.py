"""
Gemini 顾问 — 模拟仓开仓审核 + 持仓监管

开仓顾问:
  - 所有 account_id=2 模拟开仓前审核 (gate_simulated_open / paper_open_gate)
  - decision=reject 则不开仓; 开关 gemini_open_advisor_enabled

持仓顾问:
  - 所有模拟持仓 >= 2h 每 15min 问 Gemini (hold/observe/sell)
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
from app.services.open_advisor_strategy_rubrics import (
    build_big4_subjective_block,
    check_direction_gates,
    check_expected_side,
    resolve_strategy_profile,
    _KLINE_1H_READING,
)


GEMINI_TIMEOUT_MS = 180_000
HOLD_MIN_HOURS = 2.0            # 持仓满 2h 纳入监管
HOLD_CHECK_INTERVAL_S = 900     # 同仓 15min 内不重复问
GEMINI_PER_CALL_DELAY_S = 1.0   # 防 Gemini rate limit

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
        min_hours = int(HOLD_MIN_HOURS)
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
                  AND TIMESTAMPDIFF(HOUR, open_time, NOW()) >= %s
                ORDER BY open_time ASC
                """,
                (min_hours,)
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            return rows
        except Exception as e:
            logger.error(f"[Gemini顾问] 查模拟仓失败: {e}")
            return []

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """取当前价: 优先 5m K 线 (最近 15 分钟内有数据)"""
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
                "SELECT narrative_1h, narrative_15m, rsi_14 "
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

        # K线表格
        klines_str = "  time           open         high          low        close       volume\n"
        for k in ctx.get('klines_15m', []):
            klines_str += (
                f"  {k['t']}  {k['o']:>11}  {k['h']:>11}  {k['l']:>11}  "
                f"{k['c']:>11}  {k['v']:>11}\n"
            )

        big4 = ctx.get('big4_signal', 'NEUTRAL')
        big4_strength = ctx.get('big4_strength', 0)
        btc_6h = ctx.get('btc_6h_change', 0)
        eth_6h = ctx.get('eth_6h_change', 0)

        return f"""你是一个超级交易大师。
一个模拟仓位已持仓超过 {hold_h:.1f}h。请决定是 hold / observe / sell。

仓位信息
  Symbol:          {symbol}
  Direction:       {side}
  Entry price:     {entry}
  Current price:   {current_price}
  Leverage:        {leverage}x
  Hold hours:      {hold_h:.1f}h
  Price change:    {price_change_pct:+.2f}%
  ROI on margin:   {roi_pct:+.2f}%
  Source strategy: {source}

MARKET CONTEXT
  Big4 signal:     {big4} (strength {big4_strength:.0f})
  BTC 6h change:   {btc_6h:+.2f}%
  ETH 6h change:   {eth_6h:+.2f}%

RECENT 4H 15M KLINES (oldest -> newest)
{klines_str}

DECISION RULES
  - "hold":    Trend favors the position, signals stable. Continue.
  - "observe": Mixed signals, neither clear continuation nor clear reversal.
  - "sell":    Close NOW. Triggers:
      * ROI <= -15% with no reversal signal in 15m bars
      * ROI >= +20% with clear reversal candle (engulfing/pin bar)
      * Strong opposite Big4 signal while position is losing
      * Multiple 15m bars against position with expanding volume

Be decisive. False holds (should have sold) cost more than false sells.

Output ONLY a single valid JSON object, no markdown fence:
{{
  "action": "hold" | "observe" | "sell",
  "reason": "<50 chars max, in Chinese>"
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
        return out or "  (无数据)\n"

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
        big4_block = build_big4_subjective_block(
            ctx.get('big4_signal', 'NEUTRAL'),
            float(ctx.get('big4_strength') or 0),
            bool(ctx.get('allow_long', True)),
            bool(ctx.get('allow_short', True)),
            side,
            float(ctx.get('btc_6h_change') or 0),
            float(ctx.get('eth_6h_change') or 0),
        )
        klines_15m = GeminiPositionAdvisor._format_kline_table(ctx.get('klines_15m', []))
        klines_1h = GeminiPositionAdvisor._format_kline_table(ctx.get('klines_1h', []))
        narr_1h = (ctx.get('narrative_1h') or '').strip() or '(缓存暂无，以上表为准)'
        narr_15m = (ctx.get('narrative_15m') or '').strip() or '(无)'
        rsi_s = ctx.get('rsi_14_1h')
        rsi_line = f"RSI(1h): {rsi_s:.1f}" if rsi_s is not None else "RSI(1h): N/A"
        sl_s = f"{sl_pct}%" if sl_pct is not None else "默认"
        tp_s = f"{tp_pct}%" if tp_pct is not None else "默认"
        hold_s = f"{hold_hours}h" if hold_hours is not None else "策略默认"
        return f"""你是超级交易大师。系统在**开模拟仓之前**请你按**策略专属标准**审核是否允许开仓。

## 本笔策略
  策略名:     {profile.title_zh} (source={source})
  固定方向:   {profile.expected_side or '按信号 LONG/SHORT'}

{profile.rubric}

{_KLINE_1H_READING}

{big4_block}

## 拟开仓
  Symbol:     {symbol}
  Direction:  {side}
  Entry:      {price}
  Leverage:   {leverage}x
  SL/TP:      {sl_s} / {tp_s}
  Plan hold:  {hold_s}
  Catalyst:   {(catalyst or '')[:500]}

## 市场数据
  {rsi_line}
  candidate_pool 1h 叙事:
{narr_1h}
  candidate_pool 15m 叙事:
{narr_15m}

## 近 24 根 1h K 线 (oldest → newest)
{klines_1h}

## 近 4h 15m K 线
{klines_15m}

## 审核步骤（必须执行）
1. 方向闸门 + Big4 主观规则（上节）— 冲突则 reject。
2. **策略专属标准** — 例如顶空底多须真见顶/见底；回调做多须24h上涨+近4~6根回踩；反弹做空须下降+缩量反弹等。
3. catalyst 与 K 线、方向一致；仅24h涨跌幅 / 只看1根1h → reject。
4. 仅当**完全符合该策略定义**时 approve。

Output ONLY JSON:
{{
  "decision": "approve" | "reject",
  "reason": "<50字中文，写明策略名+驳回/通过要点>"
}}
"""

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
            if text.startswith('```'):
                text = text.strip('`').lstrip('json').strip()
            sig = json.loads(text)
            if open_mode:
                decision = str(sig.get('decision', '')).strip().lower()
                if decision not in ('approve', 'reject'):
                    logger.warning(
                        f"[开仓顾问] 非法 decision={decision}, 降级 approve"
                    )
                    decision = 'approve'
                return {
                    'decision': decision,
                    'reason': str(sig.get('reason', ''))[:100],
                }
            action = str(sig.get('action', '')).strip().lower()
            if action not in ('hold', 'observe', 'sell'):
                logger.warning(f"[Gemini顾问] 非法 action={action} text={text[:200]},降级 observe")
                action = 'observe'
            return {
                'action': action,
                'reason': str(sig.get('reason', ''))[:100],
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

    def _close_paper_only(self, position: dict, reason: str, close_price: float) -> bool:
        """仅关模拟仓（实盘不存在或关实盘失败时的兜底）"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                """UPDATE futures_positions
                   SET status='closed',
                       close_time=NOW(),
                       mark_price=%s,
                       realized_pnl=ROUND((%s * quantity) - (entry_price * quantity), 2),
                       unrealized_pnl=0,
                       unrealized_pnl_pct=0,
                       notes=CONCAT(IFNULL(notes,''),'|gemini_advisor:',%s)
                   WHERE id=%s AND status='open'""",
                (close_price, close_price, reason, position['id'])
            )
            if cur.rowcount > 0:
                logger.info(
                    f"[Gemini顾问] 模拟仓已关闭 id={position['id']} "
                    f"{position['symbol']} {position['position_side']} @ {close_price}"
                )
            cur.close(); conn.close()
            return True
        except Exception as e:
            logger.error(f"[Gemini顾问] 关模拟仓异常 id={position['id']}: {e}")
            return False

    def _close_live_position(self, position: dict, reason: str) -> bool:
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
                                   close_reason='gemini_advisor',
                                   notes=CONCAT(IFNULL(notes,''),'|gemini_advisor:',%s)
                               WHERE id=%s AND status='OPEN'""",
                            (close_price_f, live_pnl, reason, live_row['id'])
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
                                    f"[Gemini顾问 SELL] {position['symbol']} {position['position_side']} "
                                    f"已平仓 pnl={live_pnl}U\nreason={reason[:60]}"
                                )
                        except Exception:
                            pass

                        # 实盘平成功，用实盘成交价关模拟仓
                        self._close_paper_only(position, reason, close_price_f or float(position['entry_price']))
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
        self._close_paper_only(position, reason, close_price)
        return True

    # ────────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────────

    def tick(self) -> dict:
        """
        外部每 15 min 调一次; 模拟仓持仓 >=2h 按 15min/position 节流。
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

                action = decision['action']
                reason = decision['reason']
                stats[action] += 1
                stats['evaluated'] += 1

                # 价格差描述
                entry = float(pos['entry_price'])
                if pos['position_side'] == 'LONG':
                    pct = (current_price - entry) / entry * 100
                else:
                    pct = (entry - current_price) / entry * 100
                roi = pct * int(pos['leverage'])

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
