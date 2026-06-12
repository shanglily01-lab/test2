"""
Gemini 顾问 — 模拟仓开仓审核 + 持仓监管

开仓顾问:
  - 所有 account_id=2 模拟开仓前审核 (gate_simulated_open / paper_open_gate)
  - decision=reject 则不开仓; 开关 gemini_open_advisor_enabled

持仓顾问:
  - 所有模拟持仓 >= 15min 每 15min 问 Gemini (hold/observe/sell)
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
from app.services.hold_advisor_query import (
    GEMINI_HOLD_SOURCE_SQL,
    fetch_due_hold_positions,
)
from app.services.open_advisor_routing import should_use_gemini_hold_advisor
from app.services.open_advisor_routing import uses_gemini_open_advisor
from app.services.open_advisor_strategy_rubrics import (
    build_open_advisor_prompt,
    check_direction_gates,
    check_expected_side,
    precheck_open_advisor,
    resolve_strategy_profile,
    should_skip_llm_for_tactical_open,
)


GEMINI_TIMEOUT_MS = 180_000
HOLD_MIN_MINUTES = 15           # 持仓满 15min 纳入监管
HOLD_MIN_HOURS = HOLD_MIN_MINUTES / 60.0
HOLD_CHECK_INTERVAL_S = 900     # 同仓 15min 内不重复问
GEMINI_PER_CALL_DELAY_S = 1.0   # 防 Gemini rate limit
HOLD_15M_BARS = 6               # 持仓顾问：近 6 根 15m
HOLD_5M_BARS = 6                # 持仓顾问：近 6 根 5m（约 30min）
HOLD_1H_BARS = 4                # 持仓顾问：近 4 根 1h
HOLD_PROFIT_TEMPER_ROI = 5.0    # 程序化复核：浮盈较高时才收紧 hold
HOLD_LOSS_STRICT_ROI = -1.0     # 保证金 ROI ≤ -1%：严格审查（须看 15m+5m）
HOLD_LOSS_MILD_ROI = -5.0       # 保证金 ROI %，轻微亏损
HOLD_LOSS_MODERATE_ROI = -12.0  # 中度亏损
HOLD_LOSS_SEVERE_ROI = -15.0    # 严重亏损（近策略 SL）

# DeepSeek/GPT 持仓顾问 system（与 user prompt 中文 reason 一致）
HOLD_ADVISOR_JSON_SYSTEM_ZH = (
    "你是模拟仓持仓监管顾问，每轮须认真综合 ROI、1h 趋势、15m/5m 短周期、RSI、叙事与宏观后给出明确结论。"
    "仅输出合法 JSON：action 为 hold|observe|sell；"
    "reason 为50字以内中文，须引用 1h 为主、15m+5m 为辅的多维证据；"
    "保证金 ROI≤-1% 须严格审查 15m+5m，不可敷衍 hold；"
    "信号混杂时用 observe，1h+15m+5m 三周期确认转弱时才 sell；禁止仅凭单周期或 ROI 单独 sell。"
)

# DeepSeek/GPT 开仓顾问 system（与 build_open_advisor_prompt 中文 reason 一致）
OPEN_ADVISOR_JSON_SYSTEM_ZH = (
    "你是模拟仓开仓审核顾问。"
    "仅输出合法 JSON：decision 为 approve|reject；"
    "reason 为50字以内中文，须综合 1h 形态、15m 形态、RSI、成交量、入场价后给结论；"
    "必须写明本笔策略名及通过/驳回要点，引用 K 线表与量化指标，禁止仅凭 catalyst 措辞 approve。"
)


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

    def get_due_positions(self) -> List[Dict]:
        """到期模拟仓：满 15min 且距上次 hold 审核 ≥15min（或首审）."""
        try:
            conn = self._get_conn()
            rows = fetch_due_hold_positions(
                conn,
                reviews_table="gemini_advisor_reviews",
                source_sql=GEMINI_HOLD_SOURCE_SQL,
            )
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"[Gemini顾问] 查到期仓位失败: {e}")
            return []

    def _get_current_price(self, symbol: str) -> Optional[float]:
        """取当前价: 与限价/探索一致，统一 Hub（禁止 kline_data 旁路）。"""
        symbol = _normalize_symbol_for_db(symbol)
        try:
            from app.utils.futures_price import get_futures_trade_price

            price = get_futures_trade_price(
                None, symbol, max_age_seconds=120, log_tag="Gemini顾问",
            )
            if price is not None and price > 0:
                return float(price)
        except Exception as e:
            logger.warning(f"[Gemini顾问] {symbol} 取价失败: {e}")

        logger.warning(f"[Gemini顾问] {symbol} 无有效市价，跳过本轮审核")
        return None

    @staticmethod
    def _hub_klines_to_ctx(rows: list) -> list:
        """DataHub K 线行 → 顾问 prompt 表格格式。"""
        klines = []
        for r in rows or []:
            ot = r.get("open_time")
            if isinstance(ot, datetime.datetime):
                t = ot
            elif ot:
                t = datetime.datetime.utcfromtimestamp(float(ot) / 1000)
            else:
                continue
            klines.append({
                't': t.strftime('%m-%d %H:%M'),
                'o': round(float(r['open']), 8),
                'h': round(float(r['high']), 8),
                'l': round(float(r['low']), 8),
                'c': round(float(r['close']), 8),
                'v': round(float(r.get('volume') or 0), 2),
            })
        return klines

    def _fetch_klines_via_hub(self, symbol: str, interval: str, limit: int) -> list:
        """K 线统一走 DataHub：DB 新鲜则命中，否则 REST（不用顾问旁路查 kline_data）。"""
        symbol = _normalize_symbol_for_db(symbol)
        try:
            from app.services.binance_data_hub import get_global_data_hub

            hub = get_global_data_hub()
            if hub is None:
                return []
            rows = hub.get_klines_sync(
                symbol, interval=interval, limit=limit, allow_rest_fallback=True,
            )
            if not rows:
                from app.services.binance_data_hub import BinanceDataHub
                rows = BinanceDataHub.rest_klines_emergency(symbol, interval, limit)
            return self._hub_klines_to_ctx(rows)
        except Exception as e:
            logger.warning(f"[Gemini顾问] {symbol} Hub {interval} K 线失败: {e}")
            return []

    def _fetch_market_context(self, symbol: str) -> dict:
        """近 4h 15m K 线 + 近 24 根 1h + candidate_pool 叙事 + Big4 + 方向闸门."""
        symbol = _normalize_symbol_for_db(symbol)
        ctx = {
            'klines_15m': [],
            'klines_5m': [],
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
        ctx['klines_15m'] = self._fetch_klines_via_hub(symbol, '15m', 16)
        ctx['klines_5m'] = self._fetch_klines_via_hub(symbol, '5m', 12)
        ctx['klines_1h'] = self._fetch_klines_via_hub(symbol, '1h', 24)
        if not ctx['klines_15m'] and not ctx['klines_5m'] and not ctx['klines_1h']:
            logger.warning(f"[Gemini顾问] {symbol} K 线全空（DB 过期且 REST 失败）")

        try:
            conn = self._get_conn()
            cur = conn.cursor()
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
            "summary": "数据不足",
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
            f"顺向={for_count} 反向={against_count} "
            f"末3根={last3} 连反向={trail_against}"
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
        if roi_pct > 0:
            return "盈利"
        if roi_pct > HOLD_LOSS_STRICT_ROI:
            return "微利/保本"
        if roi_pct > HOLD_LOSS_MILD_ROI:
            return "小幅亏损·严格审查"
        if roi_pct > HOLD_LOSS_MODERATE_ROI:
            return "中度亏损"
        return "严重亏损"

    @staticmethod
    def _loss_tier_rules(roi_pct: float, side: str) -> str:
        side_cn = "做多" if side == "LONG" else "做空"
        if roi_pct > 0:
            return (
                "## 盈亏档位：盈利 (ROI > 0%)\n"
                f"- **须先看近 15m + 5m**：评估回调是洗盘还是转弱，再给结论。\n"
                f"- **1h 趋势仍支持 {side_cn}** 时默认 **hold**，不因 15m/5m 一两根回撤就止盈。\n"
                "- **sell** 须 1h 结构转弱 + 15m 确认 + 5m 同向转弱；仅短周期震荡 → **hold** 或 **observe**。\n"
                "- ROI ≥ +10% 且 1h+15m+5m 三重反转 → **sell**。"
            )
        if roi_pct > HOLD_LOSS_STRICT_ROI:
            return (
                "## 盈亏档位：微利/保本 (-1% < ROI ≤ 0%)\n"
                f"- **须综合近 15m + 5m** 判断：顺向回调 → **hold**；短周期转弱但 1h 未破 → **observe**。\n"
                f"- 1h 仍支持 {side_cn} → 倾向 **hold**；勿因 5m/15m 单根杂音 panic sell。\n"
                "- **sell** 须 1h 与 15m 同向转弱，且 5m 连反向≥2 确认；缺一 → **observe**。"
            )
        if roi_pct > HOLD_LOSS_MILD_ROI:
            return (
                "## 盈亏档位：小幅亏损·严格审查 (-5% < ROI ≤ -1%)\n"
                f"- **亏损已超 1%**：必须严格审查 **1h + 15m + 5m** 三周期后再给结论，禁止敷衍一律 hold。\n"
                f"- **hold** 须 1h 仍支持 {side_cn} **且** 15m/5m 未多数反向；任一短周期连弱 → **observe**。\n"
                "- **sell** 须 1h 近 2 根确认反向 **且** 15m 连反向≥2 **且** 5m 连反向≥2；仅部分周期弱 → **observe**。\n"
                "- reason 须写明 15m 与 5m 读法（例如「15m连3阴+5m连2阴破结构→sell」）。"
            )
        if roi_pct > HOLD_LOSS_MODERATE_ROI:
            return (
                "## 盈亏档位：中度亏损 (-12% < ROI ≤ -5%)\n"
                f"- **严格审查 15m+5m+1h**；1h 趋势未破时优先 **hold/observe**，不因浮亏情绪化 **sell**。\n"
                f"- **hold** 须近 2 根 1h 仍支持 {side_cn}，且 15m/5m 无连弱；短周期逆势 → **observe**。\n"
                "- **sell** 须 1h 近 2 根确认反向 **且** 15m 连反向≥3 **且** 5m 多数反向；结构不清 → **observe**。"
            )
        return (
            "## 盈亏档位：严重亏损 (ROI ≤ -12%)\n"
            f"- **严格审查**：1h 未破方向且 15m/5m 有企稳 → 可 **hold/observe**，勿单纯因亏损额 **sell**。\n"
            f"- **sell** 须 1h 延续亏损方向 + 15m/5m 无反转形态；仅 ROI 深亏但 1h 仍支持 {side_cn} → **observe**。\n"
            "- ROI ≤ -15% **且** 1h+15m+5m 三重走弱 → **sell**。"
        )

    @staticmethod
    def _short_cycle_weak(s_klines: dict) -> bool:
        return (
            s_klines.get("trail_against", 0) >= 2
            or s_klines.get("against", 0) > s_klines.get("for", 0)
        )

    @staticmethod
    def _short_cycle_confirms_sell(s_klines: dict) -> bool:
        return (
            s_klines.get("trail_against", 0) >= 2
            or s_klines.get("against", 0) >= s_klines.get("for", 0) + 1
        )

    @staticmethod
    def _temper_losing_hold(
        roi_pct: float,
        action: str,
        reason: str,
        side: str,
        s15: dict,
        s1h: dict,
        s5: Optional[dict] = None,
    ) -> Tuple[str, str]:
        """亏损单 hold 复核：ROI≤-1% 严格审查 15m+5m；深亏须三周期同步转弱才下调."""
        if action != 'hold' or roi_pct >= 0:
            return action, reason

        s5 = s5 or {}
        override = ""

        if roi_pct <= HOLD_LOSS_STRICT_ROI:
            s15_weak = GeminiPositionAdvisor._short_cycle_weak(s15)
            s5_weak = GeminiPositionAdvisor._short_cycle_weak(s5)
            if s1h.get("against", 0) >= 2 and s15_weak and s5_weak:
                action = 'sell' if roi_pct <= HOLD_LOSS_MILD_ROI else 'observe'
                override = "亏损>1%+1h15m5m三周期转弱"
            elif s1h.get("against", 0) >= 1 and (s15_weak or s5_weak):
                action = 'observe'
                override = "亏损>1%+短周期转弱待确认"
            elif s15_weak and s5_weak and s1h.get("for", 0) <= s1h.get("against", 0):
                action = 'observe'
                override = "亏损>1%+15m5m双弱·1h未确认"

        if not override and roi_pct <= HOLD_LOSS_SEVERE_ROI:
            if (
                s1h.get("against", 0) >= 2
                and s15.get("trail_against", 0) >= 3
            ):
                action = 'sell'
                override = "严重亏损+1h15m双弱"
            elif s1h.get("against", 0) >= 2 and s15.get("against", 0) > s15.get("for", 0):
                action = 'observe'
                override = "严重亏损+1h转弱"
        elif roi_pct <= HOLD_LOSS_MODERATE_ROI:
            if (
                s1h.get("against", 0) >= 2
                and s15.get("trail_against", 0) >= 3
            ):
                action = 'sell'
                override = "中度亏损+1h15m双弱"
            elif s1h.get("against", 0) >= 2 and s15.get("against", 0) > s15.get("for", 0):
                action = 'observe'
                override = "中度亏损+1h转弱"

        if override:
            reason = f"{reason[:80]}|复核:{override}"[:200]
            logger.info(f"[Gemini顾问] 亏损 hold 复核 → {action} ({override})")
        return action, reason

    @staticmethod
    def _temper_profitable_hold(
        roi_pct: float,
        action: str,
        reason: str,
        side: str,
        s15: dict,
        s1h: dict,
    ) -> Tuple[str, str]:
        """浮盈较高时 hold 复核：须 1h 趋势转弱才下调，避免过早止盈."""
        if action != 'hold' or roi_pct <= HOLD_PROFIT_TEMPER_ROI:
            return action, reason

        override = ""
        if s1h.get("against", 0) >= 2 and s15.get("trail_against", 0) >= 3:
            action = 'sell'
            override = "高浮盈+1h15m双弱"
        elif s1h.get("against", 0) >= 2 and s15.get("against", 0) > s15.get("for", 0):
            action = 'observe'
            override = "高浮盈+1h转弱待确认"

        if override:
            reason = f"{reason[:80]}|复核:{override}"[:200]
            logger.info(f"[Gemini顾问] 盈利 hold 复核 → {action} ({override})")
        return action, reason

    @staticmethod
    def _temper_premature_sell(
        roi_pct: float,
        action: str,
        reason: str,
        side: str,
        s15: dict,
        s1h: dict,
        s5: Optional[dict] = None,
    ) -> Tuple[str, str]:
        """综合复核：禁止仅凭单周期或 ROI 就 sell；须 1h 确认且 15m+5m 同向确认."""
        if action != 'sell':
            return action, reason

        s5 = s5 or {}
        s1h_for = s1h.get("for", 0)
        s1h_against = s1h.get("against", 0)
        s15_trail = s15.get("trail_against", 0)
        s15_against = s15.get("against", 0)
        s15_for = s15.get("for", 0)
        s5_trail = s5.get("trail_against", 0)
        s5_against = s5.get("against", 0)
        s5_for = s5.get("for", 0)
        s15_confirms = s15_trail >= 3 or s15_against > s15_for
        s5_confirms = s5_trail >= 2 or s5_against > s5_for
        short_confirms = s15_confirms and (not s5 or s5_confirms)

        if (
            roi_pct <= HOLD_LOSS_SEVERE_ROI
            and s1h_against >= 2
            and s15_trail >= 2
            and (not s5 or s5_trail >= 2)
        ):
            return action, reason

        override = ""
        if s1h_for > s1h_against:
            action = 'hold'
            override = "1h仍顺向·禁止仅凭短周期平仓"
        elif s1h_against < 2:
            action = 'hold' if s1h_for >= s1h_against else 'observe'
            override = "1h未确认反转·禁止仅凭15m/5m平仓"
        elif roi_pct <= HOLD_LOSS_STRICT_ROI and not short_confirms:
            action = 'observe'
            override = "亏损>1%·15m5m未双确认·暂缓平仓"
        elif not s15_confirms:
            action = 'observe'
            override = "1h转弱但15m未确认·暂缓平仓"
        elif s5 and not s5_confirms:
            action = 'observe'
            override = "1h15m转弱但5m未确认·暂缓平仓"

        if override:
            reason = f"{reason[:80]}|复核:{override}"[:200]
            logger.info(f"[Gemini顾问] 过早 sell 复核 → {action} ({override})")
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
        k5 = GeminiPositionAdvisor._recent_klines(
            ctx.get('klines_5m', []), HOLD_5M_BARS,
        )
        k1h = GeminiPositionAdvisor._recent_klines(
            ctx.get('klines_1h', []), HOLD_1H_BARS,
        )
        klines_15m_str = GeminiPositionAdvisor._format_kline_table(k15)
        klines_5m_str = GeminiPositionAdvisor._format_kline_table(k5)
        klines_1h_str = GeminiPositionAdvisor._format_kline_table(k1h)
        s15 = GeminiPositionAdvisor._score_klines_for_side(k15, side)
        s5 = GeminiPositionAdvisor._score_klines_for_side(k5, side)
        s1h = GeminiPositionAdvisor._score_klines_for_side(k1h, side)
        loss_rules = GeminiPositionAdvisor._loss_tier_rules(roi_pct, side)
        loss_tier = GeminiPositionAdvisor._loss_tier_label(roi_pct)
        strict_loss = roi_pct <= HOLD_LOSS_STRICT_ROI

        rsi_s = ctx.get('rsi_14_1h')
        rsi_line = f"RSI(1h): {rsi_s:.1f}" if rsi_s is not None else "RSI(1h): N/A"
        big4 = ctx.get('big4_signal', 'NEUTRAL')
        big4_strength = ctx.get('big4_strength', 0)
        btc_6h = ctx.get('btc_6h_change', 0)
        eth_6h = ctx.get('eth_6h_change', 0)
        narr_1h = (ctx.get('narrative_1h') or '').strip() or '(无)'
        narr_15m = (ctx.get('narrative_15m') or '').strip() or '(无)'
        pct_high = ctx.get('below_7d_high_pct')
        pct_low = ctx.get('above_7d_low_pct')
        if pct_high is not None and pct_low is not None:
            struct_line = f"距7日高 {pct_high:+.1f}% / 距7日低 {pct_low:+.1f}%"
        else:
            struct_line = "N/A"

        side_cn = "做多 LONG" if side == 'LONG' else "做空 SHORT"
        strict_note = (
            "**本仓亏损已超 1%（保证金 ROI≤-1%）**：须严格审查下方 1h+15m+5m 后再给结论，禁止敷衍一律 hold。\n"
            if strict_loss else ""
        )
        return f"""你是模拟仓**持仓监管**顾问。须**综合评估** 1h + 15m + 5m 后再给 hold / observe / sell。

{strict_note}## 核心原则（1h 定方向，15m/5m 必看）
- **决策顺序**：① ROI 档位 → ② **1h 趋势结构**（主判据）→ ③ **15m 短周期** → ④ **5m 近端确认** → ⑤ RSI/7日 → ⑥ 宏观辅证
- **每轮必须给出明确结论**；须写明 15m 与 5m 读法，不可敷衍一律 hold
- **1h 未破方向**时，15m/5m 回调/震荡 → **hold** 或 **observe**，**不得 sell**
- **sell**：近 2 根 1h 确认反转 **且** 15m 多数反向 **且** 5m 连反向≥2；缺一维 → **observe**
- ROI、Big4 单独变化不足以 sell；须 K 线结构综合印证

## 禁止（违反则 reason 无效）
- **严禁**仅凭 5m/15m 单根或连阴连阳就 **sell**（无 1h 反转证据时一律 hold/observe）
- **不得**主要因 Big4/BTC/ETH 宏观偏多偏空就 sell
- **不得**空泛主观（「感觉要跌」「大盘不好」「先跑为敬」）；reason 须引用 1h+15m+5m 证据
- **不得**仅因 ROI 正负或幅度就 sell；亏损>1% 时更须写明 15m/5m 评估

## 仓位
  Symbol:          {symbol}
  Direction:       {side_cn}
  Entry:           {entry}
  Current:         {current_price}
  Leverage:        {leverage}x
  Hold:            {hold_h:.1f}h
  Price change:    {price_change_pct:+.2f}%
  ROI on margin:   {roi_pct:+.2f}%  （档位: {loss_tier}）
  Source:          {source}
  {rsi_line}

## 客观统计（须与 reason 一致，勿矛盾）
  1h({HOLD_1H_BARS}根):  {s1h['summary']}  ← **主判据**
  15m({HOLD_15M_BARS}根): {s15['summary']}  ← **必看**
  5m({HOLD_5M_BARS}根):  {s5['summary']}  ← **近端确认，必看**
  结构位: {struct_line}

{loss_rules}

## 综合叙事（辅助，权重低于 K 线）
### 1h 叙事
{narr_1h[:800]}

### 15m 叙事
{narr_15m[:500]}

## 综合评估清单（给 sell 前必须逐项核对）
1. **ROI 档位**（{loss_tier}）是否允许在当前趋势下平仓？
2. **1h 趋势**是否已破 {side}？近 2 根 1h 是否确认反转？→ 未破则 **不得 sell**
3. **15m** 是否转弱或仅回调？连反向≥2 是否出现？
4. **5m** 近端是否确认 15m/1h 方向？连反向≥2 是否出现？→ 5m 仍顺向 → 倾向 **hold/observe**
5. **RSI/结构位**是否支持继续持仓或仅观察？
6. **宏观**仅作辅证，不能单独触发 sell

## K 线读法（1h 定趋势，15m/5m 必评估）
1. **近 {HOLD_1H_BARS} 根 1h**（**主判据**）：大趋势是否仍支持 {side}？
2. **近 {HOLD_15M_BARS} 根 15m**（**必看**）：中短周期是否确认或否定 1h？
3. **近 {HOLD_5M_BARS} 根 5m**（**必看**）：最近 30 分钟动量是否支持持仓方向？
4. reason 示例：「1h仍抬高+15m回调+5m连2阳→hold」或「1h连2阴+15m连3阴+5m连2阴→sell」

## 近 {HOLD_1H_BARS} 根 1h K 线 (oldest → newest)
{klines_1h_str}

## 近 {HOLD_15M_BARS} 根 15m K 线 (oldest → newest)
{klines_15m_str}

## 近 {HOLD_5M_BARS} 根 5m K 线 (oldest → newest)
{klines_5m_str}

## 宏观（辅证，权重低于 K 线）
  Big4: {big4} (strength {big4_strength:.0f}) | BTC 6h {btc_6h:+.2f}% | ETH 6h {eth_6h:+.2f}%
  仅当 K 线已明确反向时，方可引用 Big4 加强 sell 理由；不得单独因 Big4 sell。

## 决策
- **hold**: 1h 仍支持 {side}；15m/5m 仅为回调或震荡
- **observe**: 1h/15m/5m/RSI 信号混杂、趋势待确认（**应主动使用**）
- **sell**（须**综合**满足）:
  (A) ROI 档位允许（见上）
  (B) 近 2 根 1h **确认**趋势反转
  (C) 15m **多数反向**且 5m 连反向≥2（写进 reason）
  例外：ROI ≤ -15% **且** 1h+15m+5m 三重走弱 → **sell**

**1h 未破时不得 sell**；reason 须含 15m 与 5m 结论。

Output ONLY JSON:
{{
  "action": "hold" | "observe" | "sell",
  "reason": "<50字中文，含1h主判+15m/5m短周期结论>"
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
        if not uses_gemini_open_advisor(source):
            return True, "non_gemini_advised_source_skip"
        if not self._is_open_advisor_enabled():
            return True, "开仓顾问已关闭"

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

        if should_skip_llm_for_tactical_open(
            profile,
            source,
            tactical_llm_enabled=self._read_setting_bool(
                "tactical_open_advisor_llm_enabled", "1"
            ),
            explore_predict_llm_enabled=self._read_setting_bool(
                "explore_predict_open_advisor_llm_enabled", "1"
            ),
        ):
            skip_reason = "上游已通过 catalyst 门槛，跳过 LLM 复审"
            logger.info(
                f"[Gemini开仓顾问] 跳过LLM {symbol} {side} source={source} | {skip_reason}"
            )
            log_advisor_review(
                "open", "approve", symbol,
                position_side=side, source=source, entry_price=price,
                leverage=leverage, reason=skip_reason,
                catalyst=catalyst, conn=conn,
            )
            return True, skip_reason

        prompt = self._build_open_prompt(
            symbol, side, price, source, catalyst, leverage,
            sl_pct, tp_pct, hold_hours, ctx,
        )
        result = self._call_gemini(prompt, open_mode=True)
        input_payload = {
            "symbol": symbol,
            "side": side,
            "price": price,
            "source": source,
            "catalyst": catalyst,
            "leverage": leverage,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "hold_hours": hold_hours,
            "market_context": ctx,
        }
        if not result:
            log_advisor_review(
                "open", "approve", symbol,
                position_side=side, source=source, entry_price=price,
                leverage=leverage, reason="Gemini API 异常，默认放行",
                catalyst=catalyst, conn=conn, prompt_text=prompt,
                input_json=input_payload, system_prompt=OPEN_ADVISOR_JSON_SYSTEM_ZH,
            )
            return True, "Gemini API 异常，默认放行"

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
            prompt_text=prompt,
            input_json=input_payload,
            raw_response=result.get("_raw_response"),
            system_prompt=result.get("_system_prompt"),
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
                    '_raw_response': text,
                    '_system_prompt': OPEN_ADVISOR_JSON_SYSTEM_ZH,
                }
            action = str(sig.get('action', '')).strip().lower()
            if action not in ('hold', 'observe', 'sell'):
                logger.warning(f"[Gemini顾问] 非法 action={action} text={text[:200]},降级 observe")
                action = 'observe'
            return {
                'action': action,
                'reason': str(sig.get('reason', ''))[:500],
                '_raw_response': text,
                '_system_prompt': HOLD_ADVISOR_JSON_SYSTEM_ZH,
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
        from app.services.trading_gates import is_live_close_enabled

        source = position.get("source") or ""
        if not is_live_close_enabled():
            current_price = self._get_current_price(position["symbol"])
            close_price = current_price or float(position["entry_price"])
            return self._close_paper_only(
                position, reason, close_price, advisor_tag=advisor_tag,
            )

        close_price = None
        live_rows: List[dict] = []

        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT id, account_id, quantity, entry_price FROM live_futures_positions "
                "WHERE paper_position_id=%s AND status='OPEN'",
                (position['id'],)
            )
            live_rows = cur.fetchall() or []
            cur.close(); conn.close()
        except Exception as e:
            logger.warning(f"[Gemini顾问] 查实盘记录异常 id={position['id']}: {e}")

        any_live_closed = False
        if live_rows:
            try:
                from app.services.api_key_service import APIKeyService
                from app.trading.binance_futures_engine import BinanceFuturesEngine

                svc = APIKeyService(self.db_config)
                keys = svc.get_all_active_api_keys('binance')
                keys_by_id = {k['id']: k for k in keys}

                for live_row in live_rows:
                    target_key = keys_by_id.get(live_row['account_id'])
                    if not target_key:
                        logger.warning(
                            f"[Gemini顾问] account_id={live_row['account_id']} 无活跃 API key, "
                            f"跳过 live_id={live_row['id']}"
                        )
                        continue
                    try:
                        engine = BinanceFuturesEngine(
                            self.db_config,
                            api_key=target_key['api_key'],
                            api_secret=target_key['api_secret'],
                        )
                        result = engine.close_position_direct(
                            symbol=position['symbol'],
                            position_side=position['position_side'],
                            quantity=Decimal(str(live_row['quantity'])),
                            entry_price=Decimal(str(live_row['entry_price'])),
                            reason=reason,
                            strategy_name=source or advisor_tag,
                            open_time=position.get('open_time'),
                        )
                        if not result.get('success'):
                            logger.error(
                                f"[Gemini顾问] 平实盘失败 live_id={live_row['id']}: "
                                f"{result.get('error', '')}"
                            )
                            continue

                        any_live_closed = True
                        close_price = result.get('close_price', 0) or close_price
                        live_pnl = result.get('realized_pnl', 0)
                        close_price_f = float(close_price) if close_price else None

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
                    except Exception as e:
                        logger.error(
                            f"[Gemini顾问] 平实盘异常 live_id={live_row.get('id')}: {e}"
                        )

                if any_live_closed:
                    close_price_f = float(close_price) if close_price else float(position['entry_price'])
                    return self._close_paper_only(
                        position, reason, close_price_f,
                        advisor_tag=advisor_tag,
                    )
            except Exception as e:
                logger.error(f"[Gemini顾问] 批量平实盘异常 id={position['id']}: {e}")

        # 实盘平失败 / 无实盘记录，只关模拟仓
        if not close_price:
            current_price = self._get_current_price(position['symbol'])
            close_price = current_price or float(position['entry_price'])
        return self._close_paper_only(position, reason, close_price, advisor_tag=advisor_tag)

    # ────────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────────

    def tick(self) -> dict:
        """
        外部每 15 min 调一次; 仅审核到期仓位（首审优先，每轮最多 50 笔）。
        Returns 统计 dict {'evaluated', 'hold', 'observe', 'sell', 'skipped', 'errors'}
        """
        stats = {'evaluated': 0, 'hold': 0, 'observe': 0, 'sell': 0,
                 'skipped': 0, 'errors': 0, 'closed': 0}

        if not self._is_hold_advisor_enabled():
            stats['note'] = 'gemini_position_advisor_disabled'
            return stats

        positions = self.get_due_positions()
        if not positions:
            return stats

        logger.info(f"[Gemini顾问] tick 开始,到期 {len(positions)} 模拟单")

        for pos in positions:
            if not should_use_gemini_hold_advisor(pos.get('source') or ''):
                stats['skipped'] += 1
                continue

            hold_h = float(pos.get('hold_hours') or 0)
            pid = int(pos['id'])

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
                k5 = self._recent_klines(ctx.get('klines_5m', []), HOLD_5M_BARS)
                k1h = self._recent_klines(ctx.get('klines_1h', []), HOLD_1H_BARS)
                s15 = self._score_klines_for_side(k15, pos['position_side'])
                s5 = self._score_klines_for_side(k5, pos['position_side'])
                s1h = self._score_klines_for_side(k1h, pos['position_side'])

                action = decision['action']
                reason = decision['reason']
                action, reason = self._temper_losing_hold(
                    roi, action, reason, pos['position_side'], s15, s1h, s5,
                )
                action, reason = self._temper_profitable_hold(
                    roi, action, reason, pos['position_side'], s15, s1h,
                )
                action, reason = self._temper_premature_sell(
                    roi, action, reason, pos['position_side'], s15, s1h, s5,
                )
                stats[action] += 1
                stats['evaluated'] += 1
                self._last_check_ts[pid] = time.time()

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
                    prompt_text=prompt,
                    input_json={
                        "position": pos,
                        "current_price": current_price,
                        "market_context": ctx,
                        "roi_pct": round(roi, 2),
                        "kline_scores": {"15m": s15, "5m": s5, "1h": s1h},
                    },
                    raw_response=decision.get("_raw_response"),
                    system_prompt=decision.get("_system_prompt"),
                )

                if action == 'sell':
                    closed = self._close_live_position(
                        pos, f"gemini_advisor:{reason[:50]}"
                    )
                    if closed:
                        stats['closed'] += 1
                    else:
                        stats['errors'] += 1
                        self._last_check_ts.pop(pid, None)

                if GEMINI_PER_CALL_DELAY_S > 0:
                    time.sleep(GEMINI_PER_CALL_DELAY_S)

            except Exception as e:
                logger.error(f"[Gemini顾问] 处理 id={pos['id']} 异常: {e}")
                stats['errors'] += 1

        logger.info(f"[Gemini顾问] tick 完成: {stats}")
        return stats
