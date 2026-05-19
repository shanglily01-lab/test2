"""
Gemini 探索 worker

每 6h 调用 Google Gemini 检测加密货币市场的红/黑天鹅, 根据 verdict 直接开模拟单。

策略:
- red_swan  + confidence >= 0.6 -> LONG
- black_swan + confidence >= 0.6 -> SHORT
- skip 或低置信度 -> 不开仓

仓位参数 (跟 S9 一致):
- account_id = 2 (U本位模拟盘)
- margin    = 500U
- leverage  = 5x
- 最多 20 仓 (本策略 source 范围内)
- hold     = 6 小时 (planned_close_time = open_time + 6h)
- SL       = 3%
- TP       = 8%

闸门:
- system_settings.gemini_explore_enabled (默认 0, 关时早返回)
- Big4 趋势: BEARISH/STRONG_BEARISH 禁 LONG, BULLISH/STRONG_BULLISH 禁 SHORT
- 同 symbol+side 已有 OPEN gemini_explore 仓位 -> 跳过

复用 gemini_swan_worker 模块的:
- _fetch_movers_24h / _fetch_extreme_funding / _merge_universe (candidate pool 选取)
- SWAN_PROMPT_TEMPLATE + _call_gemini (Gemini 调用)

不走实盘, 不与其它 Gemini 模块共享决策表。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.gemini_swan_worker import (
    GEMINI_MODEL,
    GEMINI_API_KEY,
    GEMINI_TIMEOUT_S,
    TOP_MOVER,
    TOP_FUNDING,
    MIN_QUOTE_VOLUME,
    _is_excluded,
    _merge_universe,
    _read_setting,
)


# ---------------- 常量 ----------------
EXPLORE_MARGIN_USD = 500.0
EXPLORE_LEVERAGE = 5
EXPLORE_MAX_POSITIONS = 20
EXPLORE_HOLD_HOURS = 6
EXPLORE_SL_PCT = 3.0   # 3%
EXPLORE_TP_PCT = 8.0   # 8%
EXPLORE_CONFIDENCE_THRESHOLD = 0.6
EXPLORE_ACCOUNT_ID = 2
EXPLORE_SOURCE = 'gemini_explore'

# 数据新鲜度门槛 (本地宽松版, 跟 swan_worker 不同):
#   - swan_worker 用 price=10min / funding=30min (写死 SQL)
#   - 我们用 price=20min, 因为 price_stats_24h 在远程实测有 ~12-13 min 漂移,
#     卡 10min 会让 universe 经常为空, Gemini 调用形同虚设
#   - funding 维持 30min, 资金费率本身刷新频率就是 8h 周期, 30min 足够
EXPLORE_PRICE_FRESH_MIN = 20
EXPLORE_FUNDING_FRESH_MIN = 30


# ---------------- DB 连接 ----------------
def _get_local_db_config() -> dict:
    """从 config_loader 读 binance-data 本地 DB 配置."""
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


# ---------------- 候选池采集 ----------------
# 自己实现 fetcher (跟 swan_worker 同结构, 但门槛放宽到本地常量):
# 不调 swan_worker 的版本因为它 SQL 里把 10 分钟写死了,
# 改 swan_worker 会影响其它使用者, 所以本地复制一份.
def _fetch_movers_24h(cur, top_n: int):
    """24h 涨/跌 各 top_n. 新鲜度门槛: EXPLORE_PRICE_FRESH_MIN 分钟."""
    base_sql = """
        SELECT symbol, current_price, change_24h, quote_volume_24h, trend, updated_at
        FROM price_stats_24h
        WHERE quote_volume_24h >= %s
          AND change_24h IS NOT NULL
          AND updated_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s MINUTE)
        ORDER BY change_24h {order}
        LIMIT %s
    """
    cur.execute(base_sql.format(order="DESC"),
                (MIN_QUOTE_VOLUME, EXPLORE_PRICE_FRESH_MIN, top_n * 3))
    gainers = [r for r in cur.fetchall() if not _is_excluded(r["symbol"])][:top_n]
    cur.execute(base_sql.format(order="ASC"),
                (MIN_QUOTE_VOLUME, EXPLORE_PRICE_FRESH_MIN, top_n * 3))
    losers = [r for r in cur.fetchall() if not _is_excluded(r["symbol"])][:top_n]
    return gainers, losers


def _fetch_extreme_funding(cur, top_n: int):
    """资金费率 极正/极负 各 top_n. 新鲜度门槛: EXPLORE_FUNDING_FRESH_MIN 分钟."""
    base_sql = """
        SELECT t.symbol AS symbol,
               t.funding_rate AS current_rate,
               NULL AS rate_avg_7d,
               t.timestamp AS updated_at
        FROM funding_rate_data t
        INNER JOIN (
            SELECT symbol, MAX(funding_time) AS max_ft
            FROM funding_rate_data
            WHERE timestamp >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s MINUTE)
            GROUP BY symbol
        ) latest ON t.symbol = latest.symbol AND t.funding_time = latest.max_ft
        ORDER BY t.funding_rate {order}
        LIMIT %s
    """
    cur.execute(base_sql.format(order="DESC"),
                (EXPLORE_FUNDING_FRESH_MIN, top_n * 3))
    pos = [r for r in cur.fetchall() if not _is_excluded(r["symbol"])][:top_n]
    cur.execute(base_sql.format(order="ASC"),
                (EXPLORE_FUNDING_FRESH_MIN, top_n * 3))
    neg = [r for r in cur.fetchall() if not _is_excluded(r["symbol"])][:top_n]
    return pos, neg


def _build_universe(conn) -> dict:
    """构建 Gemini 探索候选池 (用本地宽松门槛 fetcher)."""
    with conn.cursor() as cur:
        gainers, losers = _fetch_movers_24h(cur, TOP_MOVER)
        fund_pos, fund_neg = _fetch_extreme_funding(cur, TOP_FUNDING)
    return _merge_universe(gainers, losers, fund_pos, fund_neg)


# ---------------- 技术指标 + 多周期 K 线增强 ----------------
def _ema(values: List[float], period: int) -> Optional[float]:
    """简易 EMA: alpha = 2/(period+1). 返回最后一个值. 数据不足返回 None."""
    if not values or len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(values[:period]) / period  # SMA seed
    for v in values[period:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """标准 RSI 14. 数据不足返回 None."""
    if not closes or len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    # SMA 初值
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _fetch_klines(cur, symbol: str, timeframe: str, limit: int) -> List[Dict]:
    """取最近 N 根 K 线 (按 open_time 升序). 不到 limit 也返回."""
    cur.execute(
        "SELECT open_time, open_price, high_price, low_price, close_price, volume "
        "FROM kline_data "
        "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
        "ORDER BY open_time DESC LIMIT %s",
        (symbol, timeframe, limit),
    )
    rows = list(reversed(cur.fetchall()))
    return rows


def _compact_kline(rows: List[Dict], digits: int = 6) -> List[List[float]]:
    """K 线压缩成 [open, high, low, close] 数组列表 (省 token)."""
    out = []
    for r in rows:
        try:
            out.append([
                round(float(r['open_price']), digits),
                round(float(r['high_price']), digits),
                round(float(r['low_price']), digits),
                round(float(r['close_price']), digits),
            ])
        except Exception:
            continue
    return out


def _enrich_symbol(cur, sym_data: dict) -> None:
    """给单个 symbol dict 加上 K 线 + 指标. 失败不阻塞 (字段缺即 None)."""
    symbol = sym_data['symbol']

    # 多周期 K 线 (按 token 预算: 1d×7 + 1h×12 + 15m×8 ≈ 27 根/symbol)
    k_1d = _fetch_klines(cur, symbol, '1d', 7)
    k_1h = _fetch_klines(cur, symbol, '1h', 12)
    k_15m = _fetch_klines(cur, symbol, '15m', 8)

    sym_data['k_1d_ohlc'] = _compact_kline(k_1d)
    sym_data['k_1h_ohlc'] = _compact_kline(k_1h)
    sym_data['k_15m_ohlc'] = _compact_kline(k_15m)

    # 技术指标
    closes_1h = [float(r['close_price']) for r in k_1h]
    closes_15m = [float(r['close_price']) for r in k_15m]

    rsi_1h = _rsi(closes_1h, 14) if len(closes_1h) >= 15 else None
    ema9_15m = _ema(closes_15m, 9) if len(closes_15m) >= 9 else None

    # 距 7d 高/低距离 (用 1d K 线高低数据算更准确)
    above_7d_low_pct = below_7d_high_pct = None
    if k_1d:
        try:
            highs_7d = [float(r['high_price']) for r in k_1d]
            lows_7d = [float(r['low_price']) for r in k_1d]
            high_7d = max(highs_7d)
            low_7d = min(lows_7d)
            cur_price = sym_data.get('current_price')
            if cur_price and low_7d > 0:
                above_7d_low_pct = round((cur_price - low_7d) / low_7d * 100, 2)
            if cur_price and high_7d > 0:
                below_7d_high_pct = round((cur_price - high_7d) / high_7d * 100, 2)
        except Exception:
            pass

    sym_data['tech'] = {
        'rsi_14_1h': round(rsi_1h, 1) if rsi_1h is not None else None,
        'ema9_15m': round(ema9_15m, 6) if ema9_15m is not None else None,
        'above_7d_low_pct': above_7d_low_pct,
        'below_7d_high_pct': below_7d_high_pct,
    }


def _enrich_universe(conn, universe: dict) -> None:
    """对 universe 里每个 symbol 加 K 线 + 指标."""
    with conn.cursor() as cur:
        for sym_data in universe.values():
            try:
                _enrich_symbol(cur, sym_data)
            except Exception as e:
                logger.debug(f"[Gemini探索] enrich {sym_data.get('symbol')} 失败: {e}")


def _build_global_context(conn) -> dict:
    """全局市场环境 — 给 Gemini 看完整背景."""
    ctx = {'asof_utc': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}

    # Big4 当前信号
    ctx['big4_signal'] = _get_big4_signal(conn)

    # BTC / ETH / SOL 24h 涨跌幅 (大盘节奏)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, current_price, change_24h FROM price_stats_24h "
            "WHERE symbol IN ('BTC/USDT','ETH/USDT','SOL/USDT')"
        )
        for r in cur.fetchall():
            base = r['symbol'].split('/')[0].lower()
            ctx[f'{base}_price'] = float(r['current_price']) if r['current_price'] else None
            ctx[f'{base}_change_24h'] = float(r['change_24h']) if r['change_24h'] is not None else None

    return ctx


# ---------------- Gemini 调用 (Explore 专用 prompt, 不复用 swan) ----------------
EXPLORE_PROMPT_TEMPLATE = """你是加密货币衍生品研究员, 负责识别**未来 6 小时内**最可能爆发的红/黑天鹅事件并给出可交易方向.

# 全局市场环境 (asof: {asof})
{global_context_json}

Big4 是 BTC/ETH/BNB/SOL 综合趋势指标 (取值: STRONG_BULLISH/BULLISH/NEUTRAL/BEARISH/STRONG_BEARISH).
注意 Big4 BEARISH/STRONG_BEARISH 时不要给 LONG 信号 (系统会拒绝); BULLISH/STRONG_BULLISH 时不要给 SHORT.

# 当前候选 symbol (来自 24h 涨跌 + 资金费率极值)
每个 symbol 字段:
- triggers: 进入候选池的原因 (24h_gainer / 24h_loser / funding_pos_extreme / funding_neg_extreme)
- current_price / change_24h / quote_volume_24h: 价格和成交额
- current_rate: 资金费率 (>0 多头付空头, <0 反之)
- k_15m_ohlc: 最近 8 根 15 分钟 K 线, 数组格式 [open, high, low, close], 时间升序
- k_1h_ohlc: 最近 12 根 1 小时 K 线
- k_1d_ohlc: 最近 7 根 日线
- tech.rsi_14_1h: 1 小时 RSI (>70 超买, <30 超卖)
- tech.ema9_15m: 15 分钟 EMA9 (短期均值)
- tech.above_7d_low_pct: 现价距 7 日最低点的百分比 (越接近 0 表示在低位)
- tech.below_7d_high_pct: 现价距 7 日最高点的百分比 (负值, 越接近 0 表示在高位)

{universe_json}

# 任务
为每个 symbol 标注:
- category: 'red_swan' (做多)  / 'black_swan' (做空)  / 'skip' (不交易)
- confidence: 0.0-1.0, 越大越确定
- catalyst: 你的判断依据, **必须引用具体数据** (不接受"高波动""不确定"这类宏观话术)
- data_signal: 哪个数据点最支持判断 (例: "rsi=28 + above_7d_low_pct=2.1 + 资金费率-0.8%")
- risk_note: 反向风险一句

判定原则:
1. 看**多周期一致性**: 1d 主趋势 + 1h 节奏 + 15m 入场点, 三者方向一致才高 confidence
2. 看**资金费率 vs 价格背离**:
   - 资金费极正 (拥挤多) + 价格在 7d 高点附近 + RSI 高 → black_swan (顶部砸盘)
   - 资金费极负 (拥挤空) + 价格在 7d 低点附近 + RSI 低 → red_swan (空头挤兑)
3. **不要单看 24h 涨跌幅**: 已经涨 100% 的不一定继续涨, 看是否还在加速
4. **不熟该币就 skip** + confidence ≤ 0.3, 不要硬猜
5. **结合 Big4 环境**: Big4 BEARISH 时 LONG 要更高 confidence 才下, SHORT 反之

# 输出
**仅** 一个合法 JSON, 不要 markdown 围栏:
{{
  "summary_zh": "整体市场氛围 + 主流叙事 1-2 句",
  "verdicts": [
    {{
      "symbol": "FOO/USDT",
      "category": "red_swan",
      "confidence": 0.72,
      "catalyst": "...具体依据, 引用数据...",
      "data_signal": "rsi=28, above_7d_low_pct=1.5",
      "risk_note": "..."
    }}
  ]
}}
"""


def _call_gemini_explore(universe: dict, global_ctx: dict) -> Optional[dict]:
    """专用版 Gemini 调用 — 多周期 K 线 + Big4 + 技术指标."""
    if not GEMINI_API_KEY:
        logger.error("[Gemini探索] GEMINI_API_KEY 未设置")
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("[Gemini探索] 缺依赖, 请 pip install google-genai")
        return None

    asof = global_ctx.get('asof_utc', datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'))

    # universe 按 24h 涨跌排序, 让 Gemini 优先看异动大的
    universe_list = sorted(
        list(universe.values()),
        key=lambda x: abs(x.get('change_24h') or 0),
        reverse=True,
    )

    prompt = EXPLORE_PROMPT_TEMPLATE.format(
        asof=asof,
        global_context_json=json.dumps(global_ctx, ensure_ascii=False, indent=2),
        universe_json=json.dumps(universe_list, ensure_ascii=False, indent=2, default=str),
    )

    logger.info(f"[Gemini探索] prompt 长度 = {len(prompt)} chars (~{len(prompt) // 4} tokens)")

    client = genai.Client(api_key=GEMINI_API_KEY)
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_S * 1000),
    )

    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=cfg,
        )
    except Exception as e:
        logger.error(f"[Gemini探索] Gemini 调用失败: {e}")
        return None

    text = (resp.text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    logger.info(f"[Gemini探索] gemini 用时 {time.time()-t0:.1f}s, output_len={len(text)}")

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"[Gemini探索] JSON 解析失败: {e}; raw[:500]={text[:500]}")
        return None


# ---------------- 闸门检查 ----------------
def _get_big4_signal(conn) -> str:
    """读最新 Big4 overall_signal. 失败返回 NEUTRAL."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT overall_signal FROM big4_trend_history "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            return (row or {}).get('overall_signal', 'NEUTRAL') or 'NEUTRAL'
    except Exception as e:
        logger.warning(f"[Gemini探索] 读 Big4 失败 (保守视为 NEUTRAL): {e}")
        return 'NEUTRAL'


def _big4_blocks(big4_signal: str, side: str) -> bool:
    """Big4 趋势是否封死该方向. 跟 S1-S9 主策略约定一致."""
    if side == 'LONG':
        return big4_signal in ('BEARISH', 'STRONG_BEARISH')
    if side == 'SHORT':
        return big4_signal in ('BULLISH', 'STRONG_BULLISH')
    return False


def _has_open_position(conn, symbol: str, side: str) -> bool:
    """同 symbol+side 是否已有 gemini_explore 的 OPEN 仓位."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM futures_positions "
            "WHERE source=%s AND status='open' AND symbol=%s AND position_side=%s "
            "LIMIT 1",
            (EXPLORE_SOURCE, symbol, side),
        )
        return cur.fetchone() is not None


def _count_open_positions(conn) -> int:
    """当前 gemini_explore source 下的 OPEN 仓位数."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM futures_positions "
            "WHERE source=%s AND status='open' AND account_id=%s",
            (EXPLORE_SOURCE, EXPLORE_ACCOUNT_ID),
        )
        row = cur.fetchone()
        return int((row or {}).get('cnt', 0) or 0)


# ---------------- 价格获取 ----------------
def _get_current_price(conn, symbol: str) -> Optional[float]:
    """实时取当前价, 用于开仓.

    优先级 (高 -> 低):
      L1. BinanceDataHub.get_price_sync — WS markPrice (秒级实时, 主路径)
          Hub 内部已经 4 级降级 (WS -> 60s ticker -> coin ref -> REST), 用 90s
          新鲜度门槛, 拿到的就算实时.
      L2. kline_data 5m close 兜底 — 单独脚本测试 / Hub 未启时用, 延迟 <= 6 分钟
          5m K 线 open 超过 15 分钟则视为停采, 返回 None 跳过开仓.

    历史教训 (2026-05-20):
      - 旧实现走 price_stats_24h.current_price, 那列由 cache_update_service 维护,
        但内部"优先 1m 回退 5m" 用了 2026-01 停采前的残留 1m 数据, 整整 4 个月
        没更新真实价. HYPE 真价 48 但 column 一直显示 21.
      - 现在主路径走 Hub WS markPrice, 跟 SmartExitOptimizer 用的是同一个价格源,
        开仓价跟 SL/TP 检查价 同步, 避免立刻被打 SL.
    """
    # L1: Hub (主路径)
    try:
        from app.services.binance_data_hub import get_global_data_hub
        hub = get_global_data_hub()
        if hub is not None:
            p = hub.get_price_sync(symbol, max_age_seconds=90)
            if p is not None and p > 0:
                return float(p)
    except Exception as e:
        logger.debug(f"[Gemini探索] Hub 取价失败 {symbol}, 走 L2 兜底: {e}")

    # L2: 5m kline close 兜底
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT close_price, FROM_UNIXTIME(open_time/1000) AS open_dt "
                "FROM kline_data "
                "WHERE symbol=%s AND timeframe='5m' "
                "ORDER BY open_time DESC LIMIT 1",
                (symbol,),
            )
            row = cur.fetchone()
            if not row or row.get('close_price') is None:
                return None
            open_dt = row.get('open_dt')
            if open_dt is None:
                return None
            if isinstance(open_dt, str):
                try:
                    open_dt = datetime.strptime(open_dt, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    return None
            age = (datetime.utcnow() - open_dt).total_seconds()
            if age > 900:  # K 线 open 时间超过 15 分钟视为停采
                logger.warning(
                    f"[Gemini探索] {symbol} L1 失败 L2 兜底 5m K线也过时 "
                    f"({age:.0f}s, open={open_dt}), 跳过"
                )
                return None
            logger.info(f"[Gemini探索] {symbol} L1 失败 L2 兜底 5m close={row['close_price']}")
            return float(row['close_price'])
    except Exception as e:
        logger.warning(f"[Gemini探索] L2 兜底取价失败 {symbol}: {e}")
        return None


# ---------------- 开仓 ----------------
def _open_simulated_position(
    conn,
    symbol: str,
    side: str,
    price: float,
    catalyst: str,
) -> Optional[int]:
    """直接 INSERT 到 futures_positions 表, 模拟单, 返回 position_id 或 None."""
    try:
        notional = EXPLORE_MARGIN_USD * EXPLORE_LEVERAGE
        qty = round(notional / price, 6)
        if qty <= 0:
            logger.error(f"[Gemini探索] {symbol} {side} 数量计算非正,跳过")
            return None

        if side == 'LONG':
            sl_price = round(price * (1 - EXPLORE_SL_PCT / 100), 8)
            tp_price = round(price * (1 + EXPLORE_TP_PCT / 100), 8)
        else:  # SHORT
            sl_price = round(price * (1 + EXPLORE_SL_PCT / 100), 8)
            tp_price = round(price * (1 - EXPLORE_TP_PCT / 100), 8)

        planned_close = datetime.utcnow() + timedelta(hours=EXPLORE_HOLD_HOURS)
        max_hold_minutes = EXPLORE_HOLD_HOURS * 60

        # 截断 catalyst 防止超 entry_reason 列长度 (futures_positions.entry_reason 通常 varchar(255))
        entry_reason = (catalyst or 'gemini_explore')[:200]

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
                    EXPLORE_ACCOUNT_ID, symbol, side, EXPLORE_LEVERAGE, qty, round(notional, 2),
                    EXPLORE_MARGIN_USD, price, price,
                    sl_price, tp_price,
                    EXPLORE_SL_PCT, EXPLORE_TP_PCT,
                    max_hold_minutes, planned_close,
                    EXPLORE_SOURCE, entry_reason,
                ),
            )
            position_id = cur.lastrowid

        logger.info(
            f"[Gemini探索] 开仓 {symbol} {side} @ {price:.6g} "
            f"SL={sl_price:.6g} TP={tp_price:.6g} qty={qty} "
            f"planned_close={planned_close.strftime('%Y-%m-%d %H:%M')} id={position_id}"
        )
        return position_id
    except Exception as e:
        logger.error(f"[Gemini探索] 开仓失败 {symbol} {side}: {e}")
        return None


# ---------------- 持久化 ----------------
def _insert_run(
    conn,
    asof_utc: datetime,
    universe_size: int,
    summary_zh: str,
    elapsed_s: float,
    status: str,
    error_msg: Optional[str],
    triggered_by: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gemini_explore_runs
              (asof_utc, model, universe_size, summary_zh,
               trades_opened, elapsed_s, status, error_msg, triggered_by)
            VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s)
            """,
            (asof_utc, GEMINI_MODEL, universe_size, summary_zh,
             elapsed_s, status, error_msg, triggered_by),
        )
        return cur.lastrowid


def _update_run_trades_opened(conn, run_id: int, trades_opened: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gemini_explore_runs SET trades_opened=%s WHERE id=%s",
            (trades_opened, run_id),
        )


def _insert_verdicts(conn, run_id: int, verdict_rows: List[Tuple]) -> None:
    if not verdict_rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO gemini_explore_verdicts
              (run_id, symbol, category, confidence,
               catalyst, data_signal, risk_note,
               action_taken, position_id, skip_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            verdict_rows,
        )


# ---------------- 主入口 ----------------
def run_explore_round(triggered_by: str = 'scheduler') -> Optional[int]:
    """跑一轮 Gemini 探索. 成功返回 run_id, 失败/跳过返回 None.

    线程安全: 每次新建连接, 不复用全局连接, 跟 gemini_swan_worker 一致.
    """
    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. 读 kill switch
    try:
        with _connect() as conn_chk:
            with conn_chk.cursor() as cur:
                enabled_raw = _read_setting(cur, 'gemini_explore_enabled', '0').strip().lower()
    except Exception as e:
        logger.error(f"[Gemini探索] 读 kill switch 失败,保守跳过: {e}")
        return None

    if enabled_raw not in ('1', 'true', 'yes', 'on'):
        logger.info(f"[Gemini探索] kill switch=0, 跳过 (triggered_by={triggered_by})")
        return None

    logger.info(f"[Gemini探索] === 一轮开始 (triggered_by={triggered_by}) ===")

    # 2. 候选池
    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"[Gemini探索] DB 连接失败: {e}")
        return None

    try:
        universe = _build_universe(conn)
        universe_size = len(universe)
        logger.info(f"[Gemini探索] 候选池 universe_size={universe_size}")

        if universe_size == 0:
            elapsed = time.time() - t0
            run_id = _insert_run(
                conn, asof_utc, 0, '', elapsed,
                'skipped', '候选池为空 (price_stats_24h 或 funding_rate_data 无数据?)',
                triggered_by,
            )
            logger.warning("[Gemini探索] 候选池为空, 本轮结束")
            return run_id

        # 3a. 给每个 symbol 加 1d/1h/15m K 线 + RSI/EMA/距位指标 (实时数据)
        _enrich_universe(conn, universe)
        # 3b. 全局上下文: Big4 + BTC/ETH/SOL 24h 涨跌
        global_ctx = _build_global_context(conn)
        logger.info(
            f"[Gemini探索] 全局: Big4={global_ctx.get('big4_signal')} "
            f"BTC chg24h={global_ctx.get('btc_change_24h')}% "
            f"ETH chg24h={global_ctx.get('eth_change_24h')}%"
        )

        # 3c. 调 Gemini (单轮, 用 explore 专用 prompt)
        gemini_response = _call_gemini_explore(universe, global_ctx)
        if gemini_response is None:
            elapsed = time.time() - t0
            run_id = _insert_run(
                conn, asof_utc, universe_size, '', elapsed,
                'error', 'Gemini 调用失败 (网络/API key/解析?)', triggered_by,
            )
            logger.error("[Gemini探索] Gemini 调用失败, 本轮结束")
            return run_id

        summary_zh = (gemini_response.get('summary_zh') or '')[:1000]
        verdicts = gemini_response.get('verdicts') or []
        logger.info(f"[Gemini探索] gemini 返回 verdicts={len(verdicts)}, summary={summary_zh[:80]}")

        # 4. 写 run (先占行, 拿 run_id, 等会儿更新 trades_opened)
        elapsed = time.time() - t0
        run_id = _insert_run(
            conn, asof_utc, universe_size, summary_zh, elapsed,
            'ok', None, triggered_by,
        )

        # 5. 逐 verdict 决策开仓
        big4 = _get_big4_signal(conn)
        logger.info(f"[Gemini探索] Big4 当前信号={big4}")

        trades_opened = 0
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

            # 5a. 类别与置信度
            if category == 'red_swan' and confidence >= EXPLORE_CONFIDENCE_THRESHOLD:
                side = 'LONG'
            elif category == 'black_swan' and confidence >= EXPLORE_CONFIDENCE_THRESHOLD:
                side = 'SHORT'
            else:
                verdict_rows.append((
                    run_id, symbol, category if category in ('red_swan', 'black_swan', 'skip') else 'skip',
                    confidence, catalyst, data_signal, risk_note,
                    'skipped_confidence', None,
                    f"category={category} confidence={confidence:.2f}",
                ))
                continue

            # 5b. Big4 闸门
            if _big4_blocks(big4, side):
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_big4', None,
                    f"Big4={big4} 禁 {side}",
                ))
                continue

            # 5c. 同 symbol+side 去重
            if _has_open_position(conn, symbol, side):
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_dedup', None,
                    f"{symbol} {side} 已存在 OPEN 仓位",
                ))
                continue

            # 5d. 最大仓位限制
            current_open = _count_open_positions(conn)
            if current_open >= EXPLORE_MAX_POSITIONS:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_max_positions', None,
                    f"当前 OPEN={current_open} >= {EXPLORE_MAX_POSITIONS}",
                ))
                continue

            # 5e. 取价
            price = _get_current_price(conn, symbol)
            if price is None or price <= 0:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_other', None,
                    "无最新价格 (price_stats_24h 缺数据或过时)",
                ))
                continue

            # 5f. 开仓
            position_id = _open_simulated_position(conn, symbol, side, price, catalyst)
            if position_id is None:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_other', None,
                    "开仓 INSERT 失败 (见日志)",
                ))
                continue

            trades_opened += 1
            verdict_rows.append((
                run_id, symbol, category, confidence,
                catalyst, data_signal, risk_note,
                'opened', position_id, None,
            ))

        # 6. 落库 verdicts + 更新 trades_opened
        _insert_verdicts(conn, run_id, verdict_rows)
        _update_run_trades_opened(conn, run_id, trades_opened)

        logger.info(
            f"[Gemini探索] === 一轮结束 run_id={run_id} 开仓={trades_opened} "
            f"跳过={len(verdict_rows) - trades_opened} 耗时={time.time() - t0:.1f}s ==="
        )
        return run_id

    except Exception as e:
        logger.error(f"[Gemini探索] 一轮异常: {e}", exc_info=True)
        try:
            elapsed = time.time() - t0
            _insert_run(
                conn, asof_utc, 0, '', elapsed,
                'error', str(e)[:480], triggered_by,
            )
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    # 手动跑一轮 (调试用): python -m app.services.gemini_explore_worker
    rid = run_explore_round(triggered_by='manual')
    print(f"run_id={rid}")
