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
    fetch_all_due_hold_positions,
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
    validate_open_advisor_approval,
)


GEMINI_TIMEOUT_MS = 180_000
HOLD_MIN_MINUTES = 15           # 持仓满 15min 纳入监管
HOLD_MIN_HOURS = HOLD_MIN_MINUTES / 60.0
HOLD_CHECK_INTERVAL_S = 900      # scheduler 每 15min tick；同仓间隔由 DB 审核记录控制
GEMINI_PER_CALL_DELAY_S = 1.0   # 防 Gemini rate limit
HOLD_15M_BARS = 16              # 持仓顾问：近 16 根 15m（4h 交易窗口，主判据）
HOLD_5M_BARS = 6                # 持仓顾问：近 6 根 5m（约 30min，辅证）
HOLD_1H_BARS = 4                # 持仓顾问：近 4 根 1h（背景参考，非主判）
HOLD_PROFIT_TEMPER_ROI = 3.0    # 程序化复核：浮盈≥3% ROI 即开始防回吐
HOLD_PROFIT_SELL_ROI = 5.0      # prompt：浮盈≥5% 且 15m 明确转弱可 sell
HOLD_LOSS_STRICT_ROI = -1.0     # 保证金 ROI ≤ -1%：严格审查 15m+量价+RSI
HOLD_LOSS_MILD_ROI = -5.0       # 保证金 ROI %，轻微亏损
HOLD_LOSS_MODERATE_ROI = -12.0  # 中度亏损
HOLD_LOSS_SEVERE_ROI = -15.0    # 严重亏损（近策略 SL）
HOLD_PEAK_ROI_GIVEBACK_SELL = 5.0
HOLD_NO_FOLLOW_PEAK_ROI = 1.5
HOLD_NO_FOLLOW_SELL_ROI = -8.0
HOLD_RISK_REASON_TAGS = (
    "risk_guard",
    "profit_to_loss",
    "profit_giveback",
    "no_follow",
    "mature_loss",
)

# DeepSeek/GPT 持仓顾问 system（与 user prompt 中文 reason 一致）
HOLD_ADVISOR_JSON_SYSTEM_ZH = (
    "你是模拟仓持仓监管顾问，本笔按 4 小时交易窗口复核，**以 15m K 线为主**（近16根=4h），"
    "结合5m、量价、RSI、价格位置与 ROI 后给结论。"
    "仅输出合法 JSON：action 为 hold|observe|sell；"
    "reason 为50字以内中文，须引用 **15m** 表格形态 + 量能或 RSI 要点；"
    "保证金 ROI≤-1% 须严格审查 15m 是否仍支持原方向，不可敷衍 hold；"
    "浮盈 ROI≥+3% 且 15m 转弱时至少 observe，ROI≥+5% 且 15m 明确转弱时应倾向 sell，避免盈利回吐；"
    "15m 未破方向时不得 sell；亏损越深 hold 门槛越高；禁止仅凭 ROI、Big4 或 1h 滞后信号单独 sell。"
)

# DeepSeek/GPT 开仓顾问 system（与 build_open_advisor_prompt 中文 reason 一致）
OPEN_ADVISOR_JSON_SYSTEM_ZH = (
    "你是模拟仓开仓审核顾问，审核 4 小时交易窗口的开仓单是否允许入场。"
    "仅输出合法 JSON：decision 为 approve|reject；"
    "reason 为50字以内中文，须写明 **15m 价格趋势/形态** + 量价 + 策略名要点；"
    "探索/预测：**15m 价格趋势定方向**，核对 catalyst 与 **15m 表**是否矛盾，矛盾或空洞 → reject；"
    "战术策略：**15m 定入场**，1h 仅背景；存疑一律 reject，禁止仅凭 catalyst 措辞或 RSI 单独 approve。"
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
        """到期模拟仓：每 15min/仓；浮盈转亏立即 urgent 再审."""
        try:
            conn = self._get_conn()
            rows = fetch_all_due_hold_positions(
                conn,
                reviews_table="gemini_advisor_reviews",
                source_sql=GEMINI_HOLD_SOURCE_SQL,
                get_price=self._get_current_price,
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
        """近 16 根 15m（4h 主判）+ 近 24 根 1h（背景）+ candidate_pool 叙事 + Big4 + 方向闸门."""
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
                f"- **以 15m 为主**（近16根=4h）：交易逻辑是否仍成立？对照量价、{side_cn} 方向。\n"
                f"- **15m 趋势仍支持 {side_cn}** 且 ROI < +5% → 倾向 **hold**；5m 回撤 alone 不足 sell。\n"
                f"- ROI ≥ +{HOLD_PROFIT_SELL_ROI:.0f}% 且 15m 转弱（反向≥3）+ 量价/RSI 不支持 → **sell**。\n"
                f"- ROI ≥ +3% 且 15m 明显转弱 → 至少 **observe**，勿无脑 hold 等回吐。\n"
                "- 仅 5m 震荡、15m 未破 → **hold/observe**。"
            )
        if roi_pct > HOLD_LOSS_STRICT_ROI:
            return (
                "## 盈亏档位：微利/保本 (-1% < ROI ≤ 0%)\n"
                f"- **15m 仍支持 {side_cn}** → 倾向 **hold**；勿因 5m 单根杂音 panic sell。\n"
                "- 15m 模糊、RSI/量能中性 → **observe**。\n"
                "- **sell** 须 15m 近 4 根确认反向且 RSI/量能不支持；缺一 → **observe**。"
            )
        if roi_pct > HOLD_LOSS_MILD_ROI:
            return (
                "## 盈亏档位：小幅亏损·严格审查 (-5% < ROI ≤ -1%)\n"
                f"- **亏损已超 1%**：须严格审查 **15m 结构 + 量价 + RSI** 后再给结论，禁止敷衍一律 hold。\n"
                f"- **hold** 须 15m 仍支持 {side_cn}，且 5m 未连续反向，量能/RSI 未明显转弱。\n"
                "- **sell** 须 15m 近 4 根确认反向 + 量价/RSI 不支持；仅 5m 弱而 15m 未破 → **observe**。\n"
                "- reason 须写明 15m/5m 表格形态与量能/RSI 读法。"
            )
        if roi_pct > HOLD_LOSS_MODERATE_ROI:
            return (
                "## 盈亏档位：中度亏损 (-12% < ROI ≤ -5%)\n"
                f"- **严格审查 15m+量价+RSI**；15m 趋势未破时优先 **hold/observe**，不因浮亏情绪化 **sell**。\n"
                f"- **hold 门槛更高**：近 4 根 15m 仍支持 {side_cn}，且 5m 顺向≥反向，量能未放量反向。\n"
                "- 5m 连续反向≥3 或 15m 反向≥4 → 倾向 **sell**；结构不清 → **observe**，不要弱 hold。"
            )
        return (
            "## 盈亏档位：严重亏损 (ROI ≤ -12%)\n"
            f"- **默认止损倾向**：除非 5m 明确企稳/反转且 15m 未延续亏损方向，否则优先 **sell/observe**，极少 **hold**。\n"
            f"- **hold** 须同时满足：15m 顺向≥反向且连反向≤2；近 4 根 15m 未延续亏损方向；reason 引用表格 K 线。\n"
            "- ROI ≤ -15% 且 15m 无企稳，或 15m 走弱 + 量价/RSI 不支持 → **sell**。"
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
        """亏损单 hold 复核：ROI≤-1% 严格审查 15m；深亏须 15m 确认转弱才下调."""
        if action != 'hold' or roi_pct >= 0:
            return action, reason

        s5 = s5 or {}
        override = ""

        if roi_pct <= HOLD_LOSS_STRICT_ROI:
            s15_against = s15.get("against", 0)
            s15_for = s15.get("for", 0)
            s15_trail = s15.get("trail_against", 0)
            if s15_against >= 4:
                if roi_pct <= HOLD_LOSS_MILD_ROI:
                    action = 'sell'
                    override = "亏损>5%+15m确认转弱"
                else:
                    action = 'observe'
                    override = "亏损>1%+15m转弱待确认"
            elif roi_pct <= HOLD_LOSS_SEVERE_ROI and (s15_trail >= 3 or s15_against >= 5):
                action = 'sell'
                override = "严重亏损+15m连续反向"
            elif roi_pct <= HOLD_LOSS_MODERATE_ROI and s15_against > s15_for and s15_trail >= 2:
                action = 'observe'
                override = "亏损较深+15m偏弱"
            elif s15_against >= 3 and s15_for <= s15_against:
                action = 'observe'
                override = "亏损>1%+15m结构模糊"

        if not override and roi_pct <= HOLD_LOSS_SEVERE_ROI:
            if s15.get("against", 0) >= 4:
                action = 'sell'
                override = "严重亏损+15m转弱"
        elif not override and roi_pct <= HOLD_LOSS_MODERATE_ROI:
            if s15.get("against", 0) >= 4:
                action = 'observe'
                override = "中度亏损+15m转弱"

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
        peak_roi_pct: float = 0.0,
    ) -> Tuple[str, str]:
        """Tighten profitable holds once ROI is meaningful and 15m weakens."""
        peak_roi_pct = max(float(peak_roi_pct or 0.0), roi_pct)
        peak_drawdown = peak_roi_pct - roi_pct

        if (
            action in ('hold', 'observe')
            and peak_roi_pct >= HOLD_PEAK_ROI_GIVEBACK_SELL
            and roi_pct <= HOLD_LOSS_STRICT_ROI
        ):
            action = 'sell'
            override = (
                f"risk_guard:profit_to_loss peak_roi={peak_roi_pct:.1f}% "
                f"drawdown={peak_drawdown:.1f}%"
            )
            reason = f"{reason[:80]}|guard:{override}"[:200]
            logger.info(f"[GeminiAdvisor] profit giveback guard -> sell ({override})")
            return action, reason

        if (
            action == 'hold'
            and peak_roi_pct < HOLD_NO_FOLLOW_PEAK_ROI
            and roi_pct <= HOLD_NO_FOLLOW_SELL_ROI
        ):
            action = 'sell'
            override = (
                f"risk_guard:no_follow peak_roi<{HOLD_NO_FOLLOW_PEAK_ROI:.1f}% "
                f"roi={roi_pct:.1f}%"
            )
            reason = f"{reason[:80]}|guard:{override}"[:200]
            logger.info(f"[GeminiAdvisor] no-follow loss guard -> sell ({override})")
            return action, reason

        if action != 'hold' or roi_pct < HOLD_PROFIT_TEMPER_ROI:
            return action, reason

        override = ""
        s15_against = s15.get("against", 0)
        s15_for = s15.get("for", 0)
        s15_trail = s15.get("trail_against", 0)

        if roi_pct >= HOLD_PROFIT_SELL_ROI and s15_against >= 3 and s15_trail >= 2:
            action = 'sell'
            override = f"profit_guard_roi>={HOLD_PROFIT_SELL_ROI:.0f}%+15m_weak"
        elif s15_against >= 3 and s15_against > s15_for:
            action = 'observe'
            override = f"profit_guard_roi>={HOLD_PROFIT_TEMPER_ROI:.0f}%+15m_soft_weak"

        if override:
            reason = f"{reason[:80]}|guard:{override}"[:200]
            logger.info(f"[GeminiAdvisor] profitable hold guard -> {action} ({override})")
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
        """综合复核：禁止仅凭 5m/ROI 就 sell；须 15m 近 4 根确认反转."""
        if action != 'sell':
            return action, reason

        s5 = s5 or {}
        s15_for = s15.get("for", 0)
        s15_against = s15.get("against", 0)
        reason_l = (reason or "").lower()

        if any(tag in reason_l for tag in HOLD_RISK_REASON_TAGS):
            return action, reason

        severe_loss_confirmed = (
            roi_pct <= HOLD_LOSS_MODERATE_ROI
            and (
                s15_against >= 3
                or s15.get("trail_against", 0) >= 2
            )
        ) or (
            roi_pct <= HOLD_LOSS_SEVERE_ROI
            and (
                s15_against >= 4
                or s15.get("trail_against", 0) >= 3
            )
        )
        if severe_loss_confirmed:
            return action, reason

        override = ""
        if s15_for > s15_against:
            action = 'hold'
            override = "15m仍顺向·禁止仅凭5m/1h平仓"
        elif s15_against < 4:
            action = 'hold' if s15_for >= s15_against else 'observe'
            override = "15m未确认反转·禁止平仓"
        elif roi_pct > HOLD_LOSS_MILD_ROI:
            action = 'observe'
            override = "15m转弱但亏损不深·暂缓平仓"

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
        rsi_line = f"RSI(1h,辅证): {rsi_s:.1f}" if rsi_s is not None else "RSI(1h,辅证): N/A"
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
            "**本仓亏损已超 1%（保证金 ROI≤-1%）**：须严格审查 **15m 结构** + 量价 + RSI 后再给结论，禁止敷衍一律 hold。\n"
            if strict_loss else ""
        )
        vol_hint = GeminiPositionAdvisor._volume_hint_from_klines(k15)
        return f"""你是模拟仓**持仓监管**顾问。本笔按 **4 小时交易窗口** 复核。

## 周期（必读）
- **16 根 15m = 4 根 1h = 4h**（1 根 1h ≈ 4 根 15m）。
- **核心判据**：下方 **15m 表**（{HOLD_15M_BARS} 根）+ 15m 叙事。
- **1h 保留**：近 **{HOLD_1H_BARS} 根 1h 表**（同窗口交叉验证）+ **1h 叙事**（24 根更长背景）；**不得**仅凭 1h 做 sell/hold。

{strict_note}## 核心原则（**15m 核心**，1h 交叉验证，5m 辅证）
- **决策顺序**：① ROI 档位 → ② **15m 趋势结构**（主判据）→ ③ **成交量** → ④ **RSI + 7日位置** → ⑤ 5m 辅证 → ⑥ 宏观辅证
- **每轮必须给出明确结论**；reason 须写明 **15m** 表格形态 + 量能或 RSI 要点
- **15m 未破方向**时，5m 回调/震荡 → **hold** 或 **observe**；**浮盈≥+3% 且 15m 转弱** → 至少 **observe**，**浮盈≥+5% 且 15m 明确转弱** → 倾向 **sell**
- **sell**：近 4 根 15m 确认反转 **且** 量价/RSI 不支持原方向；浮盈≥+5% 时 15m 反向≥3 且近 2 根连续不利即可 sell
- ROI、Big4 单独变化不足以 sell；须 **15m** 结构 + 量价印证
- **禁止**主要依据 1h 表做 sell/hold（1h 滞后约 1h，易误判）

## 禁止（违反则 reason 无效）
- **严禁**仅凭 5m 单根就 **sell**（无 15m 反转证据时一律 hold/observe）
- **严禁**仅凭 1h 信号 sell（1h 仅背景参考）
- **不得**主要因 Big4/BTC/ETH 宏观偏多偏空就 sell
- **不得**空泛主观（「感觉要跌」「大盘不好」「先跑为敬」）；reason 须引用 **15m** 表格 + 量价/RSI
- **不得**仅因 ROI 正负或幅度就 sell；亏损>1% 时更须写明 15m 与量能/RSI 评估

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
  15m量能(近{HOLD_15M_BARS}根): {vol_hint}

## 客观统计（须与 reason 一致，勿矛盾）
  15m({HOLD_15M_BARS}根): {s15['summary']}  ← **核心判据（4h）**
  1h({HOLD_1H_BARS}根):  {s1h['summary']}  ← **同窗口交叉验证**（≈16×15m）
  5m({HOLD_5M_BARS}根):  {s5['summary']}  ← 辅证
  结构位: {struct_line}

{loss_rules}

## 综合叙事（辅助，权重低于 K 线）
### 15m 叙事（**核心**）
{narr_15m[:800]}

### 1h 叙事（**保留**，24根更长背景 + 近6根明细；近4根≈同窗口）
{narr_1h[:800]}

## 综合评估清单（给 sell 前必须逐项核对）
1. **ROI 档位**（{loss_tier}）是否允许在当前趋势下平仓？
2. **15m 趋势**是否已破 {side}？近 4 根 15m 是否确认反转？→ 未破则 **不得 sell**
3. **成交量**：对照 **15m** 表 volume 列与上方量能摘要 — 是否支持 hold/sell？
4. **RSI/结构位**（{rsi_line}，{struct_line}）是否支持继续持仓？
5. **5m** 仅辅证，不能单独触发 sell
6. **宏观**仅作辅证，不能单独触发 sell

## K 线读法（15m 定 4h 交易逻辑）
1. **近 {HOLD_15M_BARS} 根 15m**（**主判据**）：大趋势是否仍支持 {side}？
2. **量价**：突破/延续须放量，衰竭须缩量
3. **RSI(1h)**：仅辅证，是否与 15m 方向大致一致？
4. reason 示例：「15m仍抬高+RSI58→hold」或「15m连阴+放量下破+RSI转弱→sell」

## 近 {HOLD_15M_BARS} 根 15m K 线 ← **核心判据**
{klines_15m_str}

## 近 {HOLD_1H_BARS} 根 1h K 线 ← **同 4h 窗口（1h≈4×15m），交叉验证**
{klines_1h_str}

## 近 {HOLD_5M_BARS} 根 5m K 线
{klines_5m_str}

## 宏观（辅证，权重低于 K 线）
  Big4: {big4} (strength {big4_strength:.0f}) | BTC 6h {btc_6h:+.2f}% | ETH 6h {eth_6h:+.2f}%
  仅当 **15m** 已明确反向时，方可引用 Big4 加强 sell 理由；不得单独因 Big4 sell。

## 决策
- **hold**: 15m 仍支持 {side}；量价/RSI 未明显转弱
- **observe**: 15m/RSI/量能信号混杂（**应主动使用**）
- **sell**（须**综合**满足）:
  (A) ROI 档位允许（见上）
  (B) 近 4 根 15m **确认**趋势反转
  (C) 量价或 RSI 不支持继续 {side}（写进 reason）
  例外：ROI ≤ -15% **且** 15m 走弱 + 量价/RSI 不支持 → **sell**

**15m 未破时不得 sell**；亏损越深 hold 门槛越高；reason 须含 **15m** + 量能或 RSI 结论。

只输出合法 JSON:
{{
  "action": "hold" | "observe" | "sell",
  "reason": "<50字中文，含15m形态+量价/RSI要点>"
}}
"""

    @staticmethod
    def _volume_hint_from_klines(klines: list) -> str:
        if not klines or len(klines) < 4:
            return "N/A"
        vols = [float(k.get("v", 0) or 0) for k in klines]
        r_avg = sum(vols[-3:]) / 3.0
        p_avg = sum(vols[:-3]) / max(len(vols) - 3, 1)
        if p_avg <= 0:
            return "量数据不足"
        if r_avg > p_avg * 1.1:
            trend = "放量"
        elif r_avg < p_avg * 0.9:
            trend = "缩量"
        else:
            trend = "持平"
        return f"近段均量={r_avg:.0f} 前段均量={p_avg:.0f} ({trend})"

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
        ok_pre, pre_reason = precheck_open_advisor(
            profile, side, ctx, catalyst=catalyst, source=source,
        )
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
                "open", "reject", symbol,
                position_side=side, source=source, entry_price=price,
                leverage=leverage, reason="Gemini API 异常，保守拒绝开仓",
                catalyst=catalyst, conn=conn, prompt_text=prompt,
                input_json=input_payload, system_prompt=OPEN_ADVISOR_JSON_SYSTEM_ZH,
            )
            return False, "Gemini API 异常，保守拒绝开仓"

        decision = str(result.get("decision", "reject")).lower()
        reason = str(result.get("reason", ""))[:500]
        approved = decision == "approve"
        if approved:
            ok_post, post_reason = validate_open_advisor_approval(profile, side, ctx, reason)
            if not ok_post:
                approved = False
                decision = "reject"
                reason = post_reason
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
            system_msg = OPEN_ADVISOR_JSON_SYSTEM_ZH if open_mode else HOLD_ADVISOR_JSON_SYSTEM_ZH
            effective_prompt = (
                f"{system_msg}\n"
                "强制要求：JSON 内所有自然语言字段必须使用中文，尤其 reason 禁止英文。\n\n"
                f"{prompt}"
            )
            cfg = types.GenerateContentConfig(
                response_mime_type='application/json',
                http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
            )
            model_name = os.getenv('GEMINI_MODEL', 'gemini-3-flash-preview')
            resp = client.models.generate_content(
                model=model_name, contents=effective_prompt, config=cfg,
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
                        f"[开仓顾问] 非法 decision={decision}, 降级 reject"
                    )
                    decision = 'reject'
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
                leverage = int(pos['leverage'])
                roi = pct * leverage
                peak_roi = max(float(pos.get('max_profit_pct') or 0.0) * leverage, roi)

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
                    roi, action, reason, pos['position_side'], s15, s1h, peak_roi,
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
