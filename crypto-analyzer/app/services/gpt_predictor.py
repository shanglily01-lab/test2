"""
GPT 预测 worker (v1 — 2026-05-29)

每 2h 对 candidate_pool 全量候选 (至多 500) 调用 GPT 预测未来 2h 方向,
根据预测结果挂模拟限价单.

仓位参数:
  - account_id = 2 (U本位模拟盘)
  - margin    = 500U
  - leverage  = 5x
  - hold     = 2 小时
  - SL       = 2%
  - TP       = 3%

闸门:
  - system_settings.gpt_predict_enabled (默认 0, 关时早返回)
  - Big4: 仅宏观参考；非极端行情须多空独立评估 (见 prompt / big4_trading_hint)
  - 同 symbol+side 已有 OPEN gpt_predict 仓位 -> 跳过
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.utils.futures_symbol import futures_symbol_rating_canonical

from app.services.ai_big4_prompt import (
    big4_conflict_risk_note,
    enrich_global_context,
    market_regime_from_btc_change,
)
from app.services.ai_explore_prompt import (
    get_ai_position_hold_hours,
    get_ai_position_sl_pct,
    get_ai_position_tp_pct,
    EXPLORE_LLM_MAX_OUTPUT_TOKENS,
    PREDICT_CONFIDENCE_THRESHOLD,
    explore_catalyst_technical_ok,
    parse_explore_llm_json,
    sym_data_for_catalyst_gate,
)
from app.services.ai_predict_prompt import build_predict_prompt, build_predict_prompt_en
from app.services.gemini_swan_worker import (
    _is_excluded,
    _read_setting,
)
from app.services.ai_predict_schedule import (
    GPT_PREDICT_NEXT_DUE_KEY,
    get_ai_round_interval_hours,
    predict_claim_next_slot,
    predict_round_is_due,
)
from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_MODEL, GPT_TIMEOUT_S
from app.services.gpt_llm_client import GPT_JSON_SYSTEM_EN, GPT_JSON_SYSTEM_ZH, gpt_chat_json

# ── data_cache 层: 尝试从缓存读取, 失败回退 ──
_DATA_CACHE_AVAILABLE_PREDICT = False
try:
    from app.services.data_cache_service import (
        get_candidate_pool,
        get_market_snapshot,
        get_position_stats,
    )
    _DATA_CACHE_AVAILABLE_PREDICT = True
except ImportError:
    pass

_candidate_pool_memo: Dict[str, Any] = {"ts": 0.0, "pool": []}
_CANDIDATE_POOL_TTL_S = 60.0


def _get_candidate_pool_cached() -> List[Dict]:
    """Read candidate_pool_snapshot once per predict round instead of per symbol."""
    if not _DATA_CACHE_AVAILABLE_PREDICT:
        return []
    now = time.time()
    pool = _candidate_pool_memo.get("pool") or []
    if pool and now - float(_candidate_pool_memo.get("ts") or 0.0) < _CANDIDATE_POOL_TTL_S:
        return pool
    pool = get_candidate_pool(min_volume=0, limit=500)
    _candidate_pool_memo["ts"] = now
    _candidate_pool_memo["pool"] = pool
    return pool


def _try_candidate(symbol: str) -> Optional[Dict]:
    """按 symbol 从缓存读取单个交易对数据."""
    if not _DATA_CACHE_AVAILABLE_PREDICT:
        return None
    try:
        from app.utils.futures_price import candidate_pool_row

        pool = _get_candidate_pool_cached()
        return candidate_pool_row(pool, symbol)
    except Exception:
        pass
    return None


def _try_snapshot() -> Optional[Dict]:
    if not _DATA_CACHE_AVAILABLE_PREDICT:
        return None
    try:
        return get_market_snapshot()
    except Exception:
        return None


# ============================================================
# 常量
# ============================================================
PREDICT_MARGIN_USD = 500.0
PREDICT_LEVERAGE = 5
PREDICT_ACCOUNT_ID = 2
PREDICT_SOURCE = 'gpt_predict'
PREDICT_CANDIDATE_LIMIT = 500
PREDICT_TOP_N_FALLBACK = 50

# 数据新鲜度门槛
PREDICT_PRICE_FRESH_MIN = 20
PREDICT_FUNDING_FRESH_MIN = 30


# ============================================================
# DB 连接
# ============================================================
def _get_local_db_config() -> dict:
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _connect():
    cfg = _get_local_db_config()
    return pymysql.connect(
        **cfg,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ============================================================
# 数据查询 — 候选池 (全量, 非 TOP50 截断)
# ============================================================
def _get_predict_symbols(conn) -> List[str]:
    """从 candidate_pool_snapshot 全量取 symbol；缓存不可用时回退盈利 TOP50."""
    banned = set()
    try:
        from app.services.trading_gates import load_blacklist_level3_symbols
        banned = load_blacklist_level3_symbols(conn)
    except Exception:
        banned = set()

    rows = _get_candidate_pool_cached()
    if rows:
        from app.services.ai_explore_prompt import select_all_symbols_from_pool

        symbols = select_all_symbols_from_pool(
            rows[:PREDICT_CANDIDATE_LIMIT],
            banned=banned,
        )
        if symbols:
            logger.info(
                f"[GPT预测] 从 candidate_pool_snapshot 全量获取 {len(symbols)} 个 symbol"
            )
            return symbols

    from app.services.trading_gates import is_blacklist_level3_enforced, sql_exclude_level3_filter
    _l3 = sql_exclude_level3_filter("symbol")
    with conn.cursor() as cur:
        if is_blacklist_level3_enforced():
            cur.execute(
                f"SELECT symbol FROM top_performing_symbols "
                f"WHERE 1=1 {_l3} "
                f"ORDER BY rank_score ASC LIMIT %s",
                (PREDICT_TOP_N_FALLBACK,),
            )
        else:
            cur.execute(
                "SELECT symbol FROM top_performing_symbols "
                "ORDER BY rank_score ASC LIMIT %s",
                (PREDICT_TOP_N_FALLBACK,),
            )
        symbols = [r['symbol'] for r in cur.fetchall()]
        logger.warning(
            f"[GPT预测] candidate_pool 不可用, 回退 top_performing_symbols {len(symbols)} 个"
        )
        return symbols


def _get_current_price(conn, symbol: str) -> Optional[float]:
    """实时取当前价 (U 本位 mark 优先), 用于开仓."""
    from app.utils.futures_price import get_futures_trade_price

    return get_futures_trade_price(conn, symbol, log_tag="GPT预测")


# ============================================================
# 技术指标 (复用小工具)
# ============================================================
def _ema(values: List[float], period: int) -> Optional[float]:
    if not values or len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if not closes or len(closes) < period + 1:
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
    return 100 - 100 / (1 + rs)


def _fetch_klines(cur, symbol: str, timeframe: str, limit: int) -> List[Dict]:
    cur.execute(
        "SELECT open_time, open_price, high_price, low_price, close_price, volume "
        "FROM kline_data "
        "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
        "ORDER BY open_time DESC LIMIT %s",
        (symbol, timeframe, limit),
    )
    rows = list(reversed(cur.fetchall()))
    return rows


def _format_kline_narrative(k_rows: List[Dict], timeframe_label: str, max_lines: int = 4) -> str:
    """将 K 线数据转为自然语言描述."""
    if not k_rows:
        return f"[{timeframe_label}] 无数据"

    lines = []
    prices = []
    total_vol = 0.0
    up_count = 0
    down_count = 0

    for r in k_rows:
        try:
            t = datetime.utcfromtimestamp(float(r['open_time']) / 1000)
            o = float(r['open_price'])
            h = float(r['high_price'])
            l = float(r['low_price'])
            c = float(r['close_price'])
            v = float(r.get('volume') or 0)
            pct = (c - o) / o * 100 if o > 0 else 0
            prices.append(c)
            total_vol += v
            if pct >= 0.1:
                up_count += 1
            elif pct <= -0.1:
                down_count += 1
            if len(lines) < max_lines:
                t_str = t.strftime('%H:%M') if timeframe_label in ('15m', '1h') else t.strftime('%m-%d')
                conv = '\u2191' if pct >= 0.1 else ('\u2193' if pct <= -0.1 else '\u2192')
                lines.append(
                    f"  {t_str}: O={o:.6g} H={h:.6g} L={l:.6g} C={c:.6g} "
                    f"({pct:+.2f}%){conv} Vol={v:.4g}"
                )
        except Exception:
            continue

    n = len(prices)
    pattern_desc = "数据不足"
    if n >= 3:
        change_overall = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0
        if up_count > down_count * 1.5 and change_overall > 1:
            pattern_desc = f"偏多 ({up_count}阳/{down_count}阴, 整体{change_overall:+.1f}%)"
        elif down_count > up_count * 1.5 and change_overall < -1:
            pattern_desc = f"偏空 ({up_count}阳/{down_count}阴, 整体{change_overall:+.1f}%)"
        elif change_overall > 3:
            trend = "上升" if prices[-1] > prices[0] else "下降"
            pattern_desc = f"强势{trend} ({change_overall:+.1f}%)"
        elif change_overall < -3:
            trend = "上升" if prices[-1] > prices[0] else "下降"
            pattern_desc = f"强势{trend} ({change_overall:+.1f}%)"
        else:
            trend = "上升" if prices[-1] > prices[0] else "下降"
            pattern_desc = f"震荡 ({trend}趋势, {change_overall:+.1f}%)"

    header = f"[{timeframe_label} · 最近 {len(k_rows)} 根] (总成交量 {total_vol:.4g})"
    body = '\n'.join(lines[:max_lines])
    if len(lines) > max_lines:
        body += f"\n  ... 还有 {len(lines) - max_lines} 根 (略)"
    return f"{header}\n{body}\n形态: {pattern_desc}"


# ============================================================
# 数据组装
# ============================================================
def _build_symbol_data(conn, symbol: str) -> Optional[Dict]:
    """获取单个 symbol 的完整数据: K 线叙事 + 技术指标 + 当前价.

    优先从 data_cache.candidate_pool_snapshot 读取.
    """
    cached = _try_candidate(symbol)
    if cached and cached.get("current_price"):
        kline_narrative = {}
        if cached.get("narrative_1h"):
            kline_narrative["1h"] = cached["narrative_1h"]
        if cached.get("narrative_15m"):
            kline_narrative["15m"] = cached["narrative_15m"]
        if cached.get("narrative_1d"):
            kline_narrative["1d"] = cached["narrative_1d"]
        return {
            "symbol": symbol,
            "current_price": float(cached["current_price"]) if cached.get("current_price") else None,
            "change_24h": float(cached["change_24h"]) if cached.get("change_24h") else None,
            "quote_volume_24h": float(cached["quote_volume_24h"]) if cached.get("quote_volume_24h") else None,
            "funding_rate": float(cached["funding_rate"]) if cached.get("funding_rate") else None,
            "kline_narrative": kline_narrative,
            "rsi_14_1h": float(cached["rsi_14"]) if cached.get("rsi_14") else None,
            "above_7d_low_pct": float(cached["above_7d_low_pct"]) if cached.get("above_7d_low_pct") else None,
            "below_7d_high_pct": float(cached["below_7d_high_pct"]) if cached.get("below_7d_high_pct") else None,
        }

    from app.services.data_cache_service import _make_kline_narrative

    with conn.cursor() as cur:
        k_1d = _fetch_klines(cur, symbol, '1d', 7)
        k_1h = _fetch_klines(cur, symbol, '1h', 24)
        k_15m = _fetch_klines(cur, symbol, '15m', 8)

        if not k_1d or not k_1h or not k_15m:
            return None

        kline_narrative = {
            '1d': _make_kline_narrative(k_1d, '1d'),
            '1h': _make_kline_narrative(k_1h, '1h'),
            '15m': _make_kline_narrative(k_15m, '15m'),
        }

        closes_1h = [float(r['close_price']) for r in k_1h]
        rsi_14_1h = _rsi(closes_1h, 14) if len(closes_1h) >= 15 else None

        above_7d_low = below_7d_high = None
        try:
            highs_7d = [float(r['high_price']) for r in k_1d]
            lows_7d = [float(r['low_price']) for r in k_1d]
            high_7d = max(highs_7d)
            low_7d = min(lows_7d)
            cur_price = _get_current_price(conn, symbol)
            if cur_price and low_7d > 0:
                above_7d_low = round((cur_price - low_7d) / low_7d * 100, 2)
            if cur_price and high_7d > 0:
                below_7d_high = round((cur_price - high_7d) / high_7d * 100, 2)
        except Exception:
            pass

        cur.execute(
            "SELECT current_price, change_24h, quote_volume_24h FROM price_stats_24h WHERE symbol=%s",
            (symbol,),
        )
        stats = cur.fetchone() or {}
        current_price = _get_current_price(conn, symbol)

        cur.execute(
            "SELECT funding_rate FROM funding_rate_data "
            "WHERE symbol=%s ORDER BY funding_time DESC LIMIT 1",
            (symbol,),
        )
        fr_row = cur.fetchone()
        funding_rate = float(fr_row['funding_rate']) if fr_row and fr_row.get('funding_rate') else None

    return {
        'symbol': symbol,
        'current_price': current_price,
        'change_24h': float(stats['change_24h']) if stats.get('change_24h') is not None else None,
        'quote_volume_24h': float(stats['quote_volume_24h']) if stats.get('quote_volume_24h') is not None else None,
        'funding_rate': funding_rate,
        'kline_narrative': kline_narrative,
        'rsi_14_1h': round(rsi_14_1h, 1) if rsi_14_1h is not None else None,
        'above_7d_low_pct': above_7d_low,
        'below_7d_high_pct': below_7d_high,
    }


def _build_global_context(conn) -> dict:
    """全局市场环境.

    优先从 data_cache.market_snapshot 读取.
    """
    ctx = {'asof_utc': datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

    snap = _try_snapshot()
    if snap:
        ctx['big4_signal'] = snap.get('big4_signal', 'NEUTRAL')
        for pair, base in [('BTCUSDT', 'btc'), ('ETHUSDT', 'eth'), ('SOLUSDT', 'sol')]:
            price_key = f'{pair[:3].lower()}_price'
            chg_key = f'{pair[:3].lower()}_change_24h'
            if snap.get(price_key):
                ctx[f'{base}_price'] = float(snap[price_key])
                ctx[f'{base}_change_24h'] = float(snap[chg_key] or 0)
        ctx['market_regime'] = market_regime_from_btc_change(ctx.get('btc_change_24h'))
        return enrich_global_context(ctx)

    ctx['big4_signal'] = _get_big4_signal(conn)

    try:
        with conn.cursor() as cur:
            for sym in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT'):
                base = sym.split('/')[0].lower()
                cur.execute(
                    "SELECT current_price, change_24h FROM price_stats_24h WHERE symbol=%s",
                    (sym,),
                )
                r = cur.fetchone()
                if r:
                    ctx[f'{base}_price'] = float(r['current_price']) if r['current_price'] else None
                    ctx[f'{base}_change_24h'] = float(r['change_24h']) if r['change_24h'] is not None else None
    except Exception:
        pass
    return enrich_global_context(ctx)


# ============================================================
# Big4 闸门
# ============================================================
def _get_big4_signal(conn) -> str:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT overall_signal FROM big4_trend_history "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            return (row or {}).get('overall_signal', 'NEUTRAL') or 'NEUTRAL'
    except Exception as e:
        logger.warning(f"[GPT预测] 读 Big4 失败 (保守视为 NEUTRAL): {e}")
        return 'NEUTRAL'


def _big4_blocks(big4_signal: str, side: str) -> bool:
    if side == 'LONG':
        return big4_signal in ('BEARISH', 'STRONG_BEARISH')
    if side == 'SHORT':
        return big4_signal in ('BULLISH', 'STRONG_BULLISH')
    return False


def _has_open_position(conn, symbol: str) -> bool:
    from app.services.trading_gates import has_open_futures_position
    return has_open_futures_position(conn, PREDICT_SOURCE, symbol, PREDICT_ACCOUNT_ID)


# ============================================================
# GPT 调用 (OpenAI-compatible)
# ============================================================
def _call_gpt_predict(symbols_data: List[Dict], global_ctx: dict) -> Optional[dict]:
    """调用 GPT — 批量预测所有 TOP50 方向."""
    if not GPT_API_KEY:
        logger.error("[GPT预测] GPT_API_KEY 未设置")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("[GPT预测] 缺依赖, 请 pip install openai")
        return None

    prompt = build_predict_prompt_en(symbols_data, global_ctx)

    logger.info(f"[GPT预测] prompt 长度 = {len(prompt)} chars (~{len(prompt) // 4} tokens)")

    client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)

    t0 = time.time()
    try:
        text = gpt_chat_json(
            client,
            user_prompt=prompt,
            max_tokens=EXPLORE_LLM_MAX_OUTPUT_TOKENS,
            timeout=GPT_TIMEOUT_S,
            system_prompt=GPT_JSON_SYSTEM_EN,
        )
    except Exception as e:
        logger.error(f"[GPT预测] GPT 调用失败: {e}")
        return None
    logger.info(f"[GPT预测] GPT 用时 {time.time()-t0:.1f}s, output_len={len(text)}")

    parsed, parse_err = parse_explore_llm_json(text, "GPT预测")
    if parsed is None:
        logger.error(f"[GPT预测] JSON 解析失败: {parse_err}; raw[:500]={text[:500]}")
        return None
    if not isinstance(parsed.get("verdicts"), list):
        logger.warning("[GPT预测] GPT 返回格式异常, verdicts 非 list")
        parsed["verdicts"] = []
    parsed["_prompt"] = prompt
    parsed["_raw_response"] = text
    return parsed


# ============================================================
# 开仓
# ============================================================
def _sync_to_live(
    paper_position_id: int,
    symbol: str,
    side: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    qty: float,
    catalyst: str,
) -> None:
    """将模拟单同步到实盘账号 (调用 Binance API 真实下单).

    受统一闸门 system_settings.live_trading_enabled 控制.
    """
    try:
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key='live_trading_enabled'"
            )
            row = cur.fetchone()
            enabled = (row and str(row.get('setting_value', '0')).strip().lower() in ('1', 'true', 'yes'))
            if not enabled:
                logger.info(f"[GPT预测] live_trading_enabled=0, 跳过实盘同步 {symbol}")
                conn.close()
                return

            from app.services.trading_gates import check_live_symbol_allowed
            allowed, reason = check_live_symbol_allowed(symbol, cursor=cur)
            if not allowed:
                logger.warning(
                    f"[GPT预测] {symbol} {reason}, 跳过实盘同步 "
                    f"(模拟单已开, 但不同步到实盘)"
                )
                conn.close()
                return
        conn.close()
    except Exception as e:
        logger.warning(f"[GPT预测] 检查实盘开关/白名单失败, 跳过实盘同步: {e}")
        return

    try:
        from app.services.api_key_service import APIKeyService
        from app.trading.binance_futures_engine import BinanceFuturesEngine
        from decimal import Decimal

        db_config = _get_local_db_config()
        svc = APIKeyService(db_config)
        active_keys = svc.get_all_active_api_keys('binance')
    except Exception as e:
        logger.error(f"[GPT预测] 获取实盘账号失败: {e}")
        return

    if not active_keys:
        logger.info("[GPT预测] 无活跃 API Key, 跳过实盘同步")
        return

    try:
        conn = _connect()
        with conn.cursor() as cur:
            from app.services.trading_gates import get_live_margin_ratio
            margin_ratio = get_live_margin_ratio(symbol, cursor=cur)
        conn.close()
    except Exception:
        margin_ratio = 1.0

    if margin_ratio <= 0:
        logger.info(f"[GPT预测] {symbol} 保证金比例={margin_ratio}, 跳过实盘同步")
        return

    for ak in active_keys:
        try:
            base_margin = float(ak.get('max_position_value') or PREDICT_MARGIN_USD)
            act_margin = base_margin * margin_ratio
            act_lev = int(ak.get('max_leverage') or PREDICT_LEVERAGE)
            if act_margin < 5:
                logger.info(f"[GPT预测] {symbol} 账号{ak.get('account_name','')} margin={act_margin:.1f}U < 5, 跳过")
                continue
            notional = act_margin * act_lev
            live_qty = Decimal(str(round(notional / entry_price, 6)))
            if live_qty <= 0:
                continue

            engine = BinanceFuturesEngine(
                db_config,
                api_key=ak['api_key'],
                api_secret=ak['api_secret'],
            )
            result = engine.open_position(
                account_id=ak['id'],
                symbol=symbol,
                position_side=side,
                quantity=live_qty,
                leverage=act_lev,
                stop_loss_price=Decimal(str(sl_price)),
                take_profit_price=Decimal(str(tp_price)),
                stop_loss_pct=Decimal(str(get_ai_position_sl_pct())),
                take_profit_pct=Decimal(str(get_ai_position_tp_pct())),
                source='gpt_predict',
                paper_position_id=paper_position_id,
            )
            if result.get('success'):
                logger.info(
                    f"[GPT预测] 实盘下单成功 {ak['account_name']} {symbol} {side} "
                    f"margin={act_margin}U lev={act_lev}x "
                    f"paper_id={paper_position_id}"
                )
            else:
                logger.error(
                    f"[GPT预测] 实盘下单失败 {ak['account_name']} {symbol}: "
                    f"{result.get('error', '')}"
                )
        except Exception as e:
            logger.error(
                f"[GPT预测] 实盘下单异常 {ak.get('account_name','')} {symbol}: {e}"
            )


def _open_simulated_position(
    conn,
    symbol: str,
    side: str,
    price: float,
    catalyst: str,
) -> Optional[int]:
    """INSERT 到 futures_positions, 模拟单, 返回 position_id."""
    symbol = futures_symbol_rating_canonical(symbol)
    from app.services.trading_gates import is_symbol_blocked_level3
    if is_symbol_blocked_level3(symbol):
        logger.warning(f"[GPT预测] {symbol} 黑名单3级, 禁止开仓模拟单")
        return None

    from app.services.paper_open_gate import gate_simulated_open
    allowed, _gate_reason = gate_simulated_open(
        symbol, side, price, PREDICT_SOURCE, catalyst,
        leverage=PREDICT_LEVERAGE,
        sl_pct=get_ai_position_sl_pct(), tp_pct=get_ai_position_tp_pct(),
        hold_hours=get_ai_position_hold_hours(), conn=conn,
    )
    if not allowed:
        return None

    entry_reason = (catalyst or 'gpt_predict')[:180]
    from app.services.paper_limit_entry import create_paper_limit_order
    return create_paper_limit_order(
        conn,
        symbol=symbol,
        side=side,
        ref_price=price,
        source=PREDICT_SOURCE,
        leverage=PREDICT_LEVERAGE,
        margin=PREDICT_MARGIN_USD,
        stop_loss_pct=get_ai_position_sl_pct(),
        take_profit_pct=get_ai_position_tp_pct(),
        entry_signal_type='gpt_predict',
        entry_reason=entry_reason,
        max_hold_minutes=get_ai_position_hold_hours() * 60,
        planned_close_time=datetime.now() + timedelta(hours=get_ai_position_hold_hours()),
        account_id=PREDICT_ACCOUNT_ID,
    )


# ============================================================
# 持久化
# ============================================================
def _insert_run(
    conn,
    asof_utc: datetime,
    symbol_count: int,
    summary_zh: str,
    elapsed_s: float,
    status: str,
    error_msg: Optional[str],
    triggered_by: str,
    prompt_text: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gpt_predict_runs
              (asof_utc, model, symbol_count, predictions_made, orders_opened,
               elapsed_s, status, error_msg, triggered_by, summary_zh,
               prompt_text, raw_response)
            VALUES (%s, %s, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s)
            """,
            (asof_utc, GPT_MODEL, symbol_count, elapsed_s, status, error_msg,
             triggered_by, summary_zh, prompt_text, raw_response),
        )
        return cur.lastrowid


def _update_run_stats(conn, run_id: int, predictions_made: int, orders_opened: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gpt_predict_runs SET predictions_made=%s, orders_opened=%s WHERE id=%s",
            (predictions_made, orders_opened, run_id),
        )


def _insert_verdicts(conn, run_id: int, verdict_rows: List[Tuple]) -> None:
    if not verdict_rows:
        return
    safe_rows = []
    for row in verdict_rows:
        row_list = list(row)
        row_list[2] = (row_list[2] or 'skip')[:20]       # category
        row_list[4] = (str(row_list[4]) or '')[:500]     # catalyst
        row_list[5] = (str(row_list[5]) or '')[:500]     # data_signal
        row_list[6] = (str(row_list[6]) or '')[:500]     # risk_note
        row_list[8] = (row_list[8] or '')[:30]           # action_taken
        if row_list[10] is not None:
            row_list[10] = str(row_list[10])[:255]       # skip_reason
        safe_rows.append(tuple(row_list))
    with conn.cursor() as cur:
        try:
            cur.executemany(
                """
                INSERT INTO gpt_predict_verdicts
                  (run_id, symbol, category, confidence,
                   catalyst, data_signal, risk_note,
                   price_at_pred, action_taken, position_id, skip_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                safe_rows,
            )
        except Exception as e:
            logger.error(f"[GPT预测] 写入 verdicts 失败: {e}")


# ============================================================
# 全局并发锁
# ============================================================
_predict_running_lock = threading.Lock()


# ============================================================
# 主入口
# ============================================================
def run_predict_round(triggered_by: str = 'scheduler') -> Optional[int]:
    """跑一轮 GPT 预测. 成功返回 run_id, 失败/跳过返回 None."""
    if not _predict_running_lock.acquire(blocking=False):
        logger.warning(f"[GPT预测] 上一轮还未结束, 跳过 (triggered_by={triggered_by})")
        return None
    try:
        return _run_predict_round_body(triggered_by)
    finally:
        try:
            _predict_running_lock.release()
        except Exception:
            pass


def _run_predict_round_body(triggered_by: str) -> Optional[int]:
    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    manual = triggered_by == 'manual'

    try:
        with _connect() as conn_chk:
            with conn_chk.cursor() as cur:
                enabled_raw = _read_setting(cur, 'gpt_predict_enabled', '0').strip().lower()
            if enabled_raw not in ('1', 'true', 'yes', 'on'):
                logger.info(f"[GPT预测] kill switch=0, 跳过 (triggered_by={triggered_by})")
                return None

            if not GPT_API_KEY:
                logger.error('[GPT预测] OPENAI_API_KEY 未设置, 跳过 (请配置 .env)')
                return None

            due, due_reason = predict_round_is_due(
                conn_chk,
                strategy_key='gpt_predict',
                runs_table='gpt_predict_runs',
                next_due_key=GPT_PREDICT_NEXT_DUE_KEY,
                now=asof_utc,
                manual=manual,
                log_tag='GPT预测',
            )
            if not due:
                logger.info(f"[GPT预测] {due_reason}, 跳过 (triggered_by={triggered_by})")
                return None

            if not manual:
                predict_claim_next_slot(
                    conn_chk,
                    strategy_key='gpt_predict',
                    next_due_key=GPT_PREDICT_NEXT_DUE_KEY,
                    now=asof_utc,
                    log_tag='GPT预测',
                )
    except Exception as e:
        logger.error(f"[GPT预测] 调度检查失败, 保守跳过: {e}")
        return None

    logger.info(
        f"[GPT预测] === 一轮开始 (triggered_by={triggered_by}, "
        f"周期={get_ai_round_interval_hours()}h) ==="
    )

    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"[GPT预测] DB 连接失败: {e}")
        return None

    try:
        # 2. 获取候选池 (全量)
        predict_symbols = _get_predict_symbols(conn)
        if not predict_symbols:
            logger.warning("[GPT预测] 候选池为空, 跳过")
            elapsed = time.time() - t0
            _insert_run(conn, asof_utc, 0, '', elapsed, 'skipped', '候选池为空', triggered_by)
            return None

        logger.info(f"[GPT预测] 候选池获取到 {len(predict_symbols)} 个 symbol")

        # 3. 构建每个 symbol 的数据
        symbols_data = []
        failed_symbols = []
        for sym in predict_symbols:
            data = _build_symbol_data(conn, sym)
            if data and data['current_price']:
                symbols_data.append(data)
            else:
                failed_symbols.append(sym)

        if failed_symbols:
            logger.warning(f"[GPT预测] {len(failed_symbols)} 个 symbol 数据获取失败: {failed_symbols[:5]}...")

        if not symbols_data:
            logger.warning("[GPT预测] 所有 symbol 数据获取失败, 跳过")
            elapsed = time.time() - t0
            _insert_run(conn, asof_utc, len(predict_symbols), '', elapsed, 'skipped', '所有symbol数据获取失败', triggered_by)
            return None

        # 4. 全局上下文
        global_ctx = _build_global_context(conn)
        logger.info(f"[GPT预测] 全局: Big4={global_ctx.get('big4_signal')}")

        # 5. 调 GPT
        ds_response = _call_gpt_predict(symbols_data, global_ctx)
        if ds_response is None:
            elapsed = time.time() - t0
            _insert_run(conn, asof_utc, len(symbols_data), '', elapsed, 'error', 'GPT 调用失败', triggered_by)
            logger.error("[GPT预测] GPT 调用失败, 本轮结束")
            return None

        summary_zh = (ds_response.get('summary_zh') or '')[:1000]
        verdicts = ds_response.get('verdicts') or []
        logger.info(f"[GPT预测] GPT 返回 verdicts={len(verdicts)}, summary={summary_zh[:80]}")

        # 6. 写 run 记录
        elapsed = time.time() - t0
        prompt_text = ds_response.get('_prompt')
        raw_response = ds_response.get('_raw_response')
        run_id = _insert_run(
            conn, asof_utc, len(symbols_data), summary_zh, elapsed,
            'ok', None, triggered_by, prompt_text, raw_response,
        )

        # 7. 逐 verdict 决策开仓
        big4 = _get_big4_signal(conn)
        logger.info(f"[GPT预测] Big4 当前信号={big4}")

        orders_opened = 0
        predictions_made = 0
        verdict_rows: List[Tuple] = []

        for v in verdicts:
            symbol = futures_symbol_rating_canonical(v.get('symbol') or '')
            category = (v.get('category') or 'skip').lower()
            try:
                confidence = float(v.get('confidence') or 0)
            except Exception:
                confidence = 0.0
            catalyst = (v.get('catalyst') or '')[:500]
            data_signal = (v.get('data_signal') or '')[:255]
            risk_note = (v.get('risk_note') or '')[:255]

            if not symbol:
                continue

            price_at_pred = None
            for sd in symbols_data:
                if sd['symbol'] == symbol:
                    price_at_pred = sd.get('current_price')
                    break

            if category == 'bullish' and confidence >= PREDICT_CONFIDENCE_THRESHOLD:
                side = 'LONG'
            elif category == 'bearish' and confidence >= PREDICT_CONFIDENCE_THRESHOLD:
                side = 'SHORT'
            else:
                skip_reason = f"category={category} confidence={confidence:.2f}"
                if category in ('bullish', 'bearish') and confidence < PREDICT_CONFIDENCE_THRESHOLD:
                    skip_reason = f"低置信度: conf={confidence:.2f} < {PREDICT_CONFIDENCE_THRESHOLD}"
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_confidence', None, skip_reason,
                ))
                predictions_made += 1
                continue

            sym_row = next(
                (
                    sd for sd in symbols_data
                    if (sd.get("symbol") or "").upper().replace("/", "")
                    == symbol.replace("/", "")
                ),
                None,
            )
            tech_ok, tech_reason = explore_catalyst_technical_ok(
                catalyst, data_signal, sym_data_for_catalyst_gate(sym_row),
            )
            if not tech_ok:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_weak_catalyst', None, tech_reason,
                ))
                predictions_made += 1
                logger.info(f"[GPT预测] {symbol} 跳过弱 catalyst: {tech_reason}")
                continue

            if _big4_blocks(big4, side):
                big4_warning = big4_conflict_risk_note(big4, side)
                risk_note = (risk_note + ' | ' + big4_warning) if risk_note else big4_warning

            if _has_open_position(conn, symbol):
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_dedup', None,
                    f"{symbol} 已有 OPEN 仓位, 跳过反方向",
                ))
                predictions_made += 1
                continue

            from app.services.trading_gates import is_symbol_blocked_level3, check_max_positions_allowed
            if is_symbol_blocked_level3(symbol):
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_blacklist', None,
                    "黑名单3级, 永久禁止交易",
                ))
                predictions_made += 1
                continue

            mp_ok, mp_reason = check_max_positions_allowed(conn, PREDICT_ACCOUNT_ID)
            if not mp_ok:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_max_positions', None,
                    mp_reason,
                ))
                predictions_made += 1
                continue

            from app.utils.futures_price import get_futures_trade_price
            price = get_futures_trade_price(
                conn,
                symbol,
                max_age_seconds=30,
                log_tag="GPT predict open",
                require_fresh=True,
            )
            if price is None or price <= 0:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_other', None,
                    "无最新价格",
                ))
                predictions_made += 1
                continue

            position_id = _open_simulated_position(conn, symbol, side, price, catalyst)
            if position_id is None:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_other', None,
                    "开仓 INSERT 失败",
                ))
                predictions_made += 1
                continue

            orders_opened += 1
            predictions_made += 1
            verdict_rows.append((
                run_id, symbol, category, confidence,
                catalyst, data_signal, risk_note,
                price_at_pred, 'opened', position_id, None,
            ))

        # 8. 落库
        _insert_verdicts(conn, run_id, verdict_rows)
        _update_run_stats(conn, run_id, predictions_made, orders_opened)

        try:
            from app.services.ai_shadow_explore import (
                build_shadow_universe,
                run_shadow_after_teacher_explore,
            )
            run_shadow_after_teacher_explore(
                teacher_source=PREDICT_SOURCE,
                teacher_run_id=run_id,
                universe=build_shadow_universe(symbols_data),
                global_ctx=global_ctx,
                teacher_verdicts=verdicts,
                conn=conn,
            )
        except Exception as _shadow_err:
            logger.warning(f"[GPT预测] Shadow 对比跳过: {_shadow_err}")

        logger.info(
            f"[GPT预测] === 一轮结束 run_id={run_id} 开仓={orders_opened} "
            f"预测={predictions_made} 跳过={len(verdict_rows)-orders_opened} "
            f"symbols={len(symbols_data)} 耗时={time.time()-t0:.1f}s ==="
        )
        return run_id

    except Exception as e:
        logger.error(f"[GPT预测] 一轮异常: {e}", exc_info=True)
        try:
            elapsed = time.time() - t0
            _insert_run(conn, asof_utc, 0, '', elapsed, 'error', str(e)[:480], triggered_by)
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    rid = run_predict_round(triggered_by='manual')
    print(f"run_id={rid}")
