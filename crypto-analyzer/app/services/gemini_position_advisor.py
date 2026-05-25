"""
Gemini 实盘持仓顾问 (2026-05-17)

功能:
  - 扫描 live_futures_positions 中 U本位 (account_id 对应 user_api_keys.id)
    status=OPEN 且持仓 >= 4 小时 的实盘单
  - 喂给 Gemini: 持仓数据 + 近 4h 15m K线 + Big4 当前评分
  - Gemini 三选一: hold / observe / sell
  - "sell" 自动通过 BinanceFuturesEngine.close_position_direct 真实平仓
  - 每 1 小时检查一次 (内部去重,同 position 1h 内不重复问)

开关:
  system_settings.gemini_position_advisor_enabled = 1 启用 (默认 0)
  + live_trading_enabled = 1 (实盘开仓开关) 才会真正动实盘开仓
  + live_close_enabled = 1 (实盘平仓开关) 才会真正动实盘平仓

设计文档: design/系统设计文档.md (S9 Gemini + 持仓顾问独立模块)
"""
from __future__ import annotations

import datetime
import json
import os
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pymysql
import pymysql.cursors
from loguru import logger


GEMINI_TIMEOUT_MS = 180_000
PER_POSITION_INTERVAL_S = 3600  # 同 position 1 小时内不重复问
MIN_HOLD_HOURS = 4              # 持仓不满 4h 不查
GEMINI_PER_CALL_DELAY_S = 1.0   # 防 Gemini rate limit


class GeminiPositionAdvisor:
    """实盘持仓 Gemini 顾问"""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self._client = None
        # {(account_id, symbol, side): last_check_unix_ts}
        self._last_check_ts: Dict[tuple, float] = {}

    # ────────────────────────────────────────────────────────
    # 开关读取
    # ────────────────────────────────────────────────────────

    def _is_enabled(self) -> bool:
        """读 system_settings.gemini_position_advisor_enabled"""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT setting_value FROM system_settings "
                "WHERE setting_key='gemini_position_advisor_enabled' LIMIT 1"
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if not row:
                return False
            val = str(row.get('setting_value', '0')).strip().lower()
            return val in ('1', 'true', 'yes', 'on')
        except Exception:
            return False

    def _is_close_enabled(self) -> bool:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT setting_value FROM system_settings "
                "WHERE setting_key='live_close_enabled' LIMIT 1"
            )
            row = cur.fetchone()
            cur.close(); conn.close()
            if not row:
                return False
            return str(row.get('setting_value', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
        except Exception:
            return False

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
        查 U本位实盘 OPEN 持仓 >= 4h 的所有单。
        通过 join user_api_keys.exchange='binance' 过滤掉币本位 (币本位用不同 exchange)。
        """
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT lp.id, lp.account_id, lp.symbol, lp.position_side, lp.entry_price,
                       lp.quantity, lp.leverage, lp.margin, lp.open_time, lp.source,
                       TIMESTAMPDIFF(MINUTE, lp.open_time, NOW())/60.0 AS hold_hours
                FROM live_futures_positions lp
                JOIN user_api_keys k ON lp.account_id = k.id
                WHERE lp.status='OPEN'
                  AND k.exchange='binance'
                  AND TIMESTAMPDIFF(HOUR, lp.open_time, NOW()) >= %s
                ORDER BY lp.open_time ASC
                """,
                (MIN_HOLD_HOURS,)
            )
            rows = cur.fetchall()
            cur.close(); conn.close()
            return rows
        except Exception as e:
            logger.error(f"[Gemini顾问] 查实盘持仓失败: {e}")
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
            age_min = (datetime.datetime.utcnow().timestamp() - row['open_time'] / 1000) / 60
            if age_min > 15:
                logger.warning(f"[Gemini顾问] {symbol} 5m K线 {age_min:.0f}min 旧,跳过")
                return None
            return float(row['close_price'])
        except Exception as e:
            logger.warning(f"[Gemini顾问] {symbol} 取价失败: {e}")
            return None

    def _fetch_market_context(self, symbol: str) -> dict:
        """取近 4h 15m K 线 (16根) + Big4 当前评分"""
        ctx = {'klines_15m': [], 'big4_signal': 'NEUTRAL', 'big4_strength': 0}
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            # 16 根 15m K 线 = 4 小时
            cur.execute(
                "SELECT open_time, open_price, high_price, low_price, close_price, volume "
                "FROM kline_data "
                "WHERE symbol=%s AND timeframe='15m' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 16",
                (symbol,)
            )
            rows = cur.fetchall()
            klines = []
            for r in reversed(rows):  # oldest -> newest
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

            # Big4 最新
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

            cur.close(); conn.close()
        except Exception as e:
            logger.warning(f"[Gemini顾问] 取市场上下文 {symbol} 失败: {e}")
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

        return f"""You are a crypto futures position management AI.
A LIVE position has been held over {hold_h:.1f}h. Decide whether to hold, observe, or sell.

POSITION
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

    def _call_gemini(self, prompt: str) -> Optional[dict]:
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

    def _close_live_position(self, position: dict, reason: str) -> bool:
        """通过 BinanceFuturesEngine 真实平仓 + 更新 live_futures_positions"""
        if not self._is_close_enabled():
            logger.warning(
                f"[Gemini顾问] live_close_enabled=0,不平 "
                f"id={position['id']} {position['symbol']} {position['position_side']}"
            )
            return False
        try:
            from app.services.api_key_service import APIKeyService
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            svc = APIKeyService(self.db_config)
            keys = svc.get_all_active_api_keys('binance')
            target_key = next((k for k in keys if k['id'] == position['account_id']), None)
            if not target_key:
                logger.error(
                    f"[Gemini顾问] account_id={position['account_id']} 无活跃 API key,无法平仓"
                )
                return False

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
                # 更新 live_futures_positions
                close_p = result.get('close_price', 0)
                live_pnl = result.get('realized_pnl', 0)
                conn = self._get_conn()
                cur = conn.cursor()
                cur.execute(
                    """UPDATE live_futures_positions
                       SET status='CLOSED', close_time=NOW(),
                           close_price=%s, realized_pnl=%s, close_reason=%s,
                           notes=CONCAT(IFNULL(notes,''),'|gemini_advisor:',%s)
                       WHERE id=%s""",
                    (close_p, live_pnl, reason, reason, position['id'])
                )

                # 同步关闭对应的模拟盘 (futures_positions)
                cur.execute(
                    "SELECT paper_position_id FROM live_futures_positions WHERE id=%s",
                    (position['id'],),
                )
                link_row = cur.fetchone()
                paper_id = link_row and link_row.get('paper_position_id')
                if paper_id:
                    paper_sym = position['symbol']
                    paper_side = position['position_side']
                    cur.execute(
                        """UPDATE futures_positions
                           SET status='closed',
                               close_time=NOW(),
                               mark_price=%s,
                               realized_pnl=ROUND((%s * quantity) - (entry_price * quantity), 2),
                               unrealized_pnl=0,
                               unrealized_pnl_pct=0,
                               notes='synced_close:gemini_advisor已平实盘'
                           WHERE id=%s AND status='open'""",
                        (close_p, close_p, paper_id),
                    )
                    if cur.rowcount > 0:
                        logger.info(
                            f"[Gemini顾问] 同步关闭模拟盘 id={paper_id} "
                            f"{paper_sym} {paper_side}"
                        )

                cur.close(); conn.close()
                logger.info(
                    f"[Gemini顾问] 已平 id={position['id']} {position['symbol']} "
                    f"{position['position_side']} pnl={live_pnl}"
                )
                # Telegram
                try:
                    from app.services.trade_notifier import get_trade_notifier
                    notif = get_trade_notifier()
                    if notif:
                        notif.send_message(
                            f"[Gemini顾问 SELL] {position['symbol']} {position['position_side']} "
                            f"已平仓\nentry={position['entry_price']} "
                            f"close={close_p} pnl={live_pnl}U\nreason={reason[:60]}"
                        )
                except Exception:
                    pass
                return True
            else:
                logger.error(
                    f"[Gemini顾问] 平仓失败 id={position['id']}: {result.get('error', '')}"
                )
                return False
        except Exception as e:
            logger.error(f"[Gemini顾问] 平仓异常 id={position['id']}: {e}")
            return False

    # ────────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────────

    def tick(self) -> dict:
        """
        外部每 15 min 调一次,内部按 PER_POSITION_INTERVAL_S 节流。
        Returns 统计 dict {'evaluated', 'hold', 'observe', 'sell', 'skipped', 'errors'}
        """
        stats = {'evaluated': 0, 'hold': 0, 'observe': 0, 'sell': 0,
                 'skipped': 0, 'errors': 0, 'closed': 0}

        if not self._is_enabled():
            return stats  # OFF,静默

        positions = self.get_eligible_positions()
        if not positions:
            return stats

        logger.info(f"[Gemini顾问] tick 开始,候选 {len(positions)} 实盘单 >= 4h")

        now = time.time()
        for pos in positions:
            key = (pos['account_id'], pos['symbol'], pos['position_side'])
            last = self._last_check_ts.get(key)
            if last and (now - last) < PER_POSITION_INTERVAL_S:
                stats['skipped'] += 1
                continue
            self._last_check_ts[key] = now

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
