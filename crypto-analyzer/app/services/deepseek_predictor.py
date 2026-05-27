"""
DeepSeek 预测 worker (v1 — 2026-05-28)

每 12h 对 TOP 50 交易对调用 DeepSeek 预测未来 12h 方向,
根据预测结果直接开模拟单.

仓位参数:
  - account_id = 2 (U本位模拟盘)
  - margin    = 500U
  - leverage  = 5x
  - 最多 20 仓
  - hold     = 12 小时
  - SL       = 5%
  - TP       = 10%

闸门:
  - system_settings.deepseek_predict_enabled (默认 0, 关时早返回)
  - Big4 趋势: BEARISH/STRONG_BEARISH 禁 LONG, BULLISH/STRONG_BULLISH 禁 SHORT
  - 同 symbol+side 已有 OPEN deepseek_predict 仓位 -> 跳过
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

from app.services.gemini_swan_worker import (
    _is_excluded,
    _read_setting,
)
from app.services.deepseek_explore_worker import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_MODEL,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_TIMEOUT_S,
)

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


def _try_candidate(symbol: str) -> Optional[Dict]:
    """按 symbol 从缓存读取单个交易对数据."""
    if not _DATA_CACHE_AVAILABLE_PREDICT:
        return None
    try:
        pool = get_candidate_pool(min_volume=0, limit=500)
        for r in pool:
            if r["symbol"] == symbol:
                return r
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
PREDICT_MAX_POSITIONS = 20
PREDICT_HOLD_HOURS = 12
PREDICT_SL_PCT = 5.0
PREDICT_TP_PCT = 10.0
PREDICT_CONFIDENCE_THRESHOLD = 0.60
PREDICT_ACCOUNT_ID = 2
PREDICT_SOURCE = 'deepseek_predict'
PREDICT_TOP_N = 50

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
# 数据查询 — TOP 50
# ============================================================
def _get_top50_symbols(conn) -> List[str]:
    """从 top_performing_symbols 获取 TOP 50 交易对, 排除黑名单3级."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM top_performing_symbols "
            "WHERE symbol NOT IN ("
            "  SELECT symbol FROM trading_symbol_rating WHERE rating_level >= 3"
            ") "
            "ORDER BY rank_score DESC LIMIT %s",
            (PREDICT_TOP_N,),
        )
        return [r['symbol'] for r in cur.fetchall()]


def _get_current_price(conn, symbol: str) -> Optional[float]:
    """实时取当前价, 用于开仓."""
    try:
        from app.services.binance_data_hub import get_global_data_hub
        hub = get_global_data_hub()
        if hub is not None:
            p = hub.get_price_sync(symbol, max_age_seconds=90)
            if p is not None and p > 0:
                return float(p)
    except Exception:
        pass

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT close_price FROM kline_data "
                "WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 1",
                (symbol,),
            )
            row = cur.fetchone()
            if row and row.get('close_price'):
                return float(row['close_price'])
    except Exception:
        pass
    return None


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
                    f"({pct:+.2%}){conv} Vol={v:.4g}"
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

    with conn.cursor() as cur:
        k_1d = _fetch_klines(cur, symbol, '1d', 7)
        k_1h = _fetch_klines(cur, symbol, '1h', 72)
        k_15m = _fetch_klines(cur, symbol, '15m', 96)

        if not k_1d or not k_1h or not k_15m:
            return None

        kline_narrative = {
            '1d': _format_kline_narrative(k_1d, '1d', 7),
            '1h': _format_kline_narrative(k_1h, '1h', 6),
            '15m': _format_kline_narrative(k_15m, '15m', 6),
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
    ctx = {'asof_utc': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

    snap = _try_snapshot()
    if snap:
        ctx['big4_signal'] = snap.get('big4_signal', 'NEUTRAL')
        for pair, base in [('BTCUSDT', 'btc'), ('ETHUSDT', 'eth'), ('SOLUSDT', 'sol')]:
            price_key = f'{pair[:3].lower()}_price'
            chg_key = f'{pair[:3].lower()}_change_24h'
            if snap.get(price_key):
                ctx[f'{base}_price'] = float(snap[price_key])
                ctx[f'{base}_change_24h'] = float(snap[chg_key] or 0)
        return ctx

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
    return ctx


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
        logger.warning(f"[DeepSeek预测] 读 Big4 失败 (保守视为 NEUTRAL): {e}")
        return 'NEUTRAL'


def _big4_blocks(big4_signal: str, side: str) -> bool:
    if side == 'LONG':
        return big4_signal in ('BEARISH', 'STRONG_BEARISH')
    if side == 'SHORT':
        return big4_signal in ('BULLISH', 'STRONG_BULLISH')
    return False


def _has_open_position(conn, symbol: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM futures_positions "
            "WHERE source=%s AND status='open' AND symbol=%s "
            "LIMIT 1",
            (PREDICT_SOURCE, symbol),
        )
        return cur.fetchone() is not None


def _count_open_positions(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM futures_positions "
            "WHERE source=%s AND status='open' AND account_id=%s",
            (PREDICT_SOURCE, PREDICT_ACCOUNT_ID),
        )
        row = cur.fetchone()
        return int((row or {}).get('cnt', 0) or 0)


# ============================================================
# Prompt 构建
# ============================================================
PREDICT_PROMPT_TEMPLATE = """你是超级交易大师. 预测每个币种在未来 1 天内的方向走势概率.

持仓期 1 天 (24h), SL=5%, TP=15%, 杠杆 3x, 不做中途干预.

选中的币种需要能在 1 天内到达 15% 的涨幅/跌幅空间, 或至少抗住 1 天不跌/不涨过 5%.
不要选"只波动 2-3%"的标的.

# 全局市场环境
{global_context_json}

Big4 (BTC/ETH/BNB/SOL 综合趋势): BEARISH/STRONG_BEARISH 时严禁做多, BULLISH/STRONG_BULLISH 时严禁做空.

# 候选数据说明
每个 symbol 包含:
- kline_narrative: 自然语言描述的日/时/15m K 线形态 (含成交量趋势)
- current_price / change_24h / quote_volume_24h
- funding_rate: 资金费率
- rsi_14_1h: 1h 级别 RSI
- above_7d_low_pct / below_7d_high_pct: 现价距 7 日极值距离

{symbols_data_json}

# 任务
为**每个** symbol 标注:
- category: 'bullish' / 'bearish' / 'skip'
- confidence: 0.0-1.0
- catalyst: 判断依据, 必须引用具体数据, 至少 2 句
- data_signal: 最支持判断的关键数据点
- risk_note: 反向风险一句

# 置信度校准
| confidence | 意义 |
|---|---|
| 0.80-1.00 | 多周期共振 + 成交量确认, 方向明确 |
| 0.65-0.79 | 日线趋势 + 1h 方向一致, Big4 不矛盾 |
| 0.50-0.64 | 仅小时级别支持, 日线中性 — 可开但有限 |
| 0.00-0.49 | 方向模糊/震荡 — 跳过 |

# 判定原则
## 适合开仓
- 日线趋势清晰 + 1h 同向 + 成交量放大
- 资金费率极值与价格严重背离 + 日线拐点确认
- 突破后回踩确认

## 必须跳过
- 暴涨暴跌后的报复性反弹 (Dead Cat Bounce)
- 震荡区间 / 成交量萎缩
- 仅因"跌多了"做多 或 "涨多了"做空

# 输出要求
仅一个合法 JSON, 不要 markdown 围栏.
优先 quality 而非 quantity.

{{
  "summary_zh": "整体市场氛围 1-2 句",
  "verdicts": [
    {{
      "symbol": "FOO/USDT",
      "category": "bullish",
      "confidence": 0.72,
      "catalyst": "具体依据...",
      "data_signal": "...",
      "risk_note": "..."
    }}
  ]
}}
"""


# ============================================================
# DeepSeek 调用 (OpenAI-compatible)
# ============================================================
def _call_deepseek_predict(symbols_data: List[Dict], global_ctx: dict) -> Optional[dict]:
    """调用 DeepSeek — 批量预测所有 TOP50 方向."""
    if not DEEPSEEK_API_KEY:
        logger.error("[DeepSeek预测] DEEPSEEK_API_KEY 未设置")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("[DeepSeek预测] 缺依赖, 请 pip install openai")
        return None

    prompt = PREDICT_PROMPT_TEMPLATE.format(
        global_context_json=json.dumps(global_ctx, ensure_ascii=False, indent=2),
        symbols_data_json=json.dumps(symbols_data, ensure_ascii=False, indent=2, default=str),
    )

    logger.info(f"[DeepSeek预测] prompt 长度 = {len(prompt)} chars (~{len(prompt) // 4} tokens)")

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)

    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=DEEPSEEK_TIMEOUT_S,
        )
    except Exception as e:
        logger.error(f"[DeepSeek预测] DeepSeek 调用失败: {e}")
        return None

    text = (resp.choices[0].message.content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    logger.info(f"[DeepSeek预测] DeepSeek 用时 {time.time()-t0:.1f}s, output_len={len(text)}")

    try:
        parsed = json.loads(text)
        if not isinstance(parsed.get('verdicts'), list):
            logger.warning("[DeepSeek预测] DeepSeek 返回格式异常, verdicts 非 list")
            parsed['verdicts'] = []
        return parsed
    except json.JSONDecodeError as e:
        logger.error(f"[DeepSeek预测] JSON 解析失败: {e}; raw[:500]={text[:500]}")
        return None


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
                logger.info(f"[DeepSeek预测] live_trading_enabled=0, 跳过实盘同步 {symbol}")
                conn.close()
                return

            cur.execute(
                "SELECT "
                "  (SELECT 1 FROM top_performing_symbols WHERE symbol=%s LIMIT 1) AS in_top100,"
                "  (SELECT rating_level FROM trading_symbol_rating WHERE symbol=%s LIMIT 1) AS rating_level",
                (symbol, symbol),
            )
            row = cur.fetchone()
            in_top100 = row and row.get('in_top100') == 1
            is_whitelist = row and row.get('rating_level') is not None and int(row['rating_level']) == 0
            if not in_top100 and not is_whitelist:
                reason = "不在 TOP 50 也非白名单"
                logger.warning(
                    f"[DeepSeek预测] {symbol} {reason}, 跳过实盘同步 "
                    f"(模拟单已开, 但不同步到实盘)"
                )
                conn.close()
                return
        conn.close()
    except Exception as e:
        logger.warning(f"[DeepSeek预测] 检查实盘开关/TOP50失败, 跳过实盘同步: {e}")
        return

    try:
        from app.services.api_key_service import APIKeyService
        from app.trading.binance_futures_engine import BinanceFuturesEngine
        from decimal import Decimal

        db_config = _get_local_db_config()
        svc = APIKeyService(db_config)
        active_keys = svc.get_all_active_api_keys('binance')
    except Exception as e:
        logger.error(f"[DeepSeek预测] 获取实盘账号失败: {e}")
        return

    if not active_keys:
        logger.info("[DeepSeek预测] 无活跃 API Key, 跳过实盘同步")
        return

    db_config = _get_local_db_config()
    for ak in active_keys:
        try:
            act_margin = float(ak.get('max_position_value') or PREDICT_MARGIN_USD)
            act_lev = int(ak.get('max_leverage') or PREDICT_LEVERAGE)
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
                stop_loss_pct=Decimal(str(PREDICT_SL_PCT)),
                take_profit_pct=Decimal(str(PREDICT_TP_PCT)),
                source='deepseek_predict',
                paper_position_id=paper_position_id,
            )
            if result.get('success'):
                logger.info(
                    f"[DeepSeek预测] 实盘下单成功 {ak['account_name']} {symbol} {side} "
                    f"margin={act_margin}U lev={act_lev}x "
                    f"paper_id={paper_position_id}"
                )
                try:
                    from app.services.trade_notifier import get_trade_notifier
                    notifier = get_trade_notifier()
                    if notifier:
                        notifier.notify_open_position(
                            symbol=symbol, direction=side,
                            quantity=float(live_qty), entry_price=entry_price,
                            leverage=act_lev, stop_loss_price=sl_price,
                            take_profit_price=tp_price,
                            margin=act_margin,
                            strategy_name=f'DeepSeek预测[{ak["account_name"]}]'
                        )
                except Exception:
                    pass
            else:
                logger.error(
                    f"[DeepSeek预测] 实盘下单失败 {ak['account_name']} {symbol}: "
                    f"{result.get('error', '')}"
                )
        except Exception as e:
            logger.error(
                f"[DeepSeek预测] 实盘下单异常 {ak.get('account_name','')} {symbol}: {e}"
            )


def _open_simulated_position(
    conn,
    symbol: str,
    side: str,
    price: float,
    catalyst: str,
) -> Optional[int]:
    """INSERT 到 futures_positions, 模拟单, 返回 position_id."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM trading_symbol_rating WHERE (symbol=%s OR symbol=%s) AND rating_level >= 3 LIMIT 1",
            (symbol, symbol.replace('/', '')),
        )
        if cur.fetchone() is not None:
            logger.warning(f"[DeepSeek预测] {symbol} 黑名单3级, 禁止开仓模拟单")
            return None

    try:
        notional = PREDICT_MARGIN_USD * PREDICT_LEVERAGE
        qty = round(notional / price, 6)
        if qty <= 0:
            logger.error(f"[DeepSeek预测] {symbol} {side} 数量计算非正,跳过")
            return None

        if side == 'LONG':
            sl_price = round(price * (1 - PREDICT_SL_PCT / 100), 8)
            tp_price = round(price * (1 + PREDICT_TP_PCT / 100), 8)
        else:
            sl_price = round(price * (1 + PREDICT_SL_PCT / 100), 8)
            tp_price = round(price * (1 - PREDICT_TP_PCT / 100), 8)

        planned_close = datetime.utcnow() + timedelta(hours=PREDICT_HOLD_HOURS)
        max_hold_minutes = PREDICT_HOLD_HOURS * 60

        entry_reason = (catalyst or 'deepseek_predict')[:180]

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO futures_positions
                  (account_id, symbol, position_side, leverage, quantity, notional_value,
                   margin, entry_price, mark_price,
                   stop_loss_price, take_profit_price,
                   stop_loss_pct, take_profit_pct,
                   max_hold_minutes, planned_close_time,
                   status, source, entry_reason, open_time,
                   unrealized_pnl, unrealized_pnl_pct)
                VALUES (%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,
                        %s,%s,
                        %s,%s,
                        %s,%s,
                        'open', %s, %s, NOW(),
                        0, 0)
                """,
                (
                    PREDICT_ACCOUNT_ID, symbol, side, PREDICT_LEVERAGE, qty, round(notional, 2),
                    PREDICT_MARGIN_USD, price, price,
                    sl_price, tp_price,
                    PREDICT_SL_PCT, PREDICT_TP_PCT,
                    max_hold_minutes, planned_close,
                    PREDICT_SOURCE, entry_reason,
                ),
            )
            position_id = cur.lastrowid

        logger.info(
            f"[DeepSeek预测] 开仓 {symbol} {side} @ {price:.6g} "
            f"SL={sl_price:.6g}({PREDICT_SL_PCT}%) TP={tp_price:.6g}({PREDICT_TP_PCT}%) "
            f"qty={qty} lev={PREDICT_LEVERAGE}x hold={PREDICT_HOLD_HOURS}h "
            f"id={position_id}"
        )

        # DeepSeek 暂时不同步实盘
        # _sync_to_live(position_id, symbol, side, price, sl_price, tp_price, qty, catalyst)
        logger.info(f"[DeepSeek预测] 模拟仓 #{position_id} {symbol} {side} 已开, 暂不同步实盘")

        return position_id
    except Exception as e:
        logger.error(f"[DeepSeek预测] 开仓失败 {symbol} {side}: {e}")
        return None


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
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO deepseek_predict_runs
              (asof_utc, model, symbol_count, predictions_made, orders_opened,
               elapsed_s, status, error_msg, triggered_by, summary_zh)
            VALUES (%s, %s, %s, 0, 0, %s, %s, %s, %s, %s)
            """,
            (asof_utc, DEEPSEEK_MODEL, symbol_count, elapsed_s, status, error_msg, triggered_by, summary_zh),
        )
        return cur.lastrowid


def _update_run_stats(conn, run_id: int, predictions_made: int, orders_opened: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE deepseek_predict_runs SET predictions_made=%s, orders_opened=%s WHERE id=%s",
            (predictions_made, orders_opened, run_id),
        )


def _insert_verdicts(conn, run_id: int, verdict_rows: List[Tuple]) -> None:
    if not verdict_rows:
        return
    safe_rows = []
    for row in verdict_rows:
        row_list = list(row)
        row_list[2] = (row_list[2] or 'skip')[:20]
        row_list[8] = (row_list[8] or '')[:30]
        if row_list[10] is not None:
            row_list[10] = str(row_list[10])[:255]
    with conn.cursor() as cur:
        try:
            cur.executemany(
                """
                INSERT INTO deepseek_predict_verdicts
                  (run_id, symbol, category, confidence,
                   catalyst, data_signal, risk_note,
                   price_at_pred, action_taken, position_id, skip_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                safe_rows,
            )
        except Exception as e:
            logger.error(f"[DeepSeek预测] 写入 verdicts 失败: {e}")


# ============================================================
# 全局并发锁
# ============================================================
_predict_running_lock = threading.Lock()


# ============================================================
# 主入口
# ============================================================
def run_predict_round(triggered_by: str = 'scheduler') -> Optional[int]:
    """跑一轮 DeepSeek 预测. 成功返回 run_id, 失败/跳过返回 None."""
    if not _predict_running_lock.acquire(blocking=False):
        logger.warning(f"[DeepSeek预测] 上一轮还未结束, 跳过 (triggered_by={triggered_by})")
        return None

    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. 读 kill switch
    try:
        with _connect() as conn_chk:
            with conn_chk.cursor() as cur:
                enabled_raw = _read_setting(cur, 'deepseek_predict_enabled', '0').strip().lower()
    except Exception as e:
        logger.error(f"[DeepSeek预测] 读 kill switch 失败, 保守跳过: {e}")
        return None

    if enabled_raw not in ('1', 'true', 'yes', 'on'):
        logger.info(f"[DeepSeek预测] kill switch=0, 跳过 (triggered_by={triggered_by})")
        return None

    # 防重: 上次成功 run 距今 < 11h 则跳过
    try:
        with _connect() as conn_chk:
            with conn_chk.cursor() as cur:
                cur.execute(
                    "SELECT MAX(asof_utc) AS last_run FROM deepseek_predict_runs WHERE status='ok'"
                )
                row = cur.fetchone()
                if row and row.get('last_run'):
                    elapsed_h = (asof_utc - row['last_run']).total_seconds() / 3600
                    if elapsed_h < 11:
                        logger.info(f"[DeepSeek预测] 上次成功运行距今 {elapsed_h:.1f}h < 11h, 跳过 (防重启重复)")
                        return None
    except Exception as e:
        logger.warning(f"[DeepSeek预测] 启动防重检查失败, 继续: {e}")

    logger.info(f"[DeepSeek预测] === 一轮开始 (triggered_by={triggered_by}) ===")

    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"[DeepSeek预测] DB 连接失败: {e}")
        return None

    try:
        # 2. 获取 TOP50
        top100 = _get_top50_symbols(conn)
        if not top100:
            logger.warning("[DeepSeek预测] TOP50 为空, 跳过")
            elapsed = time.time() - t0
            _insert_run(conn, asof_utc, 0, '', elapsed, 'skipped', 'TOP50为空', triggered_by)
            return None

        logger.info(f"[DeepSeek预测] TOP50 获取到 {len(top100)} 个 symbol")

        # 3. 构建每个 symbol 的数据
        symbols_data = []
        failed_symbols = []
        for sym in top100:
            data = _build_symbol_data(conn, sym)
            if data and data['current_price']:
                symbols_data.append(data)
            else:
                failed_symbols.append(sym)

        if failed_symbols:
            logger.warning(f"[DeepSeek预测] {len(failed_symbols)} 个 symbol 数据获取失败: {failed_symbols[:5]}...")

        if not symbols_data:
            logger.warning("[DeepSeek预测] 所有 symbol 数据获取失败, 跳过")
            elapsed = time.time() - t0
            _insert_run(conn, asof_utc, len(top100), '', elapsed, 'skipped', '所有symbol数据获取失败', triggered_by)
            return None

        # 4. 全局上下文
        global_ctx = _build_global_context(conn)
        logger.info(f"[DeepSeek预测] 全局: Big4={global_ctx.get('big4_signal')}")

        # 5. 调 DeepSeek
        ds_response = _call_deepseek_predict(symbols_data, global_ctx)
        if ds_response is None:
            elapsed = time.time() - t0
            _insert_run(conn, asof_utc, len(symbols_data), '', elapsed, 'error', 'DeepSeek 调用失败', triggered_by)
            logger.error("[DeepSeek预测] DeepSeek 调用失败, 本轮结束")
            return None

        summary_zh = (ds_response.get('summary_zh') or '')[:1000]
        verdicts = ds_response.get('verdicts') or []
        logger.info(f"[DeepSeek预测] DeepSeek 返回 verdicts={len(verdicts)}, summary={summary_zh[:80]}")

        # 6. 写 run 记录
        elapsed = time.time() - t0
        run_id = _insert_run(
            conn, asof_utc, len(symbols_data), summary_zh, elapsed,
            'ok', None, triggered_by,
        )

        # 7. 逐 verdict 决策开仓
        big4 = _get_big4_signal(conn)
        logger.info(f"[DeepSeek预测] Big4 当前信号={big4}")

        orders_opened = 0
        predictions_made = 0
        verdict_rows: List[Tuple] = []

        for v in verdicts:
            symbol = (v.get('symbol') or '').upper()
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

            if _big4_blocks(big4, side):
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_big4', None,
                    f"Big4={big4} 禁 {side}",
                ))
                predictions_made += 1
                continue

            if _has_open_position(conn, symbol):
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_dedup', None,
                    f"{symbol} 已有 OPEN 仓位, 跳过反方向",
                ))
                predictions_made += 1
                continue

            if _count_open_positions(conn) >= PREDICT_MAX_POSITIONS:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_max_positions', None,
                    f"已达上限 {PREDICT_MAX_POSITIONS}",
                ))
                predictions_made += 1
                continue

            with conn.cursor() as _cur:
                _cur.execute(
                    "SELECT 1 FROM trading_symbol_rating WHERE (symbol=%s OR symbol=%s) AND rating_level >= 3 LIMIT 1",
                    (symbol, symbol.replace('/', '')),
                )
                _is_level3 = _cur.fetchone() is not None
            if _is_level3:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    price_at_pred, 'skipped_blacklist', None,
                    "黑名单3级, 永久禁止交易",
                ))
                predictions_made += 1
                continue

            price = _get_current_price(conn, symbol)
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

        logger.info(
            f"[DeepSeek预测] === 一轮结束 run_id={run_id} 开仓={orders_opened} "
            f"预测={predictions_made} 跳过={len(verdict_rows)-orders_opened} "
            f"symbols={len(symbols_data)} 耗时={time.time()-t0:.1f}s ==="
        )
        return run_id

    except Exception as e:
        logger.error(f"[DeepSeek预测] 一轮异常: {e}", exc_info=True)
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
        try:
            _predict_running_lock.release()
        except Exception:
            pass


if __name__ == '__main__':
    rid = run_predict_round(triggered_by='manual')
    print(f"run_id={rid}")
