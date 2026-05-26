"""
Gemini 市场情绪 + 川普分析 worker (v1 — 2026-05-23)

每 8h 执行一轮:
1. 市场情緒分析 — 采集 BTC/ETH/SOL 等核心数据、资金费率、涨跌幅异动,
   调用 Gemini 分析市场整体情绪躁动程度和大方向决策。
2. 川普推特/讲话分析 — 从已有新闻池/推特采集器获取川普相关内容,
   调用 Gemini 做深度解读及对市场方向的影响判断。

⚠ 两个分析均不做下单依据，仅为辅助参考。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.gemini_swan_worker import (
    GEMINI_MODEL,
    GEMINI_API_KEY,
    GEMINI_TIMEOUT_S,
    _read_setting,
)

# ── data_cache 层 ──
_DATA_CACHE_SENTIMENT = False
try:
    from app.services.data_cache_service import (
        get_market_snapshot,
        get_market_movers,
        get_setting as get_cached_setting,
    )
    _DATA_CACHE_SENTIMENT = True
except ImportError:
    pass


# ============================================================
# 常量
# ============================================================
SENTIMENT_SOURCE = "gemini_sentiment"
CORE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")

# 采集数据新鲜度门槛 (秒)
PRICE_FRESH_S = 120
FUNDING_FRESH_S = 300

# 情绪得分范围
SENTIMENT_TIMEOUT_S = int(os.getenv("GEMINI_SENTIMENT_TIMEOUT_S", "240"))


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
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ============================================================
# 数据采集
# ============================================================
def _fetch_core_prices(conn) -> dict:
    """获取核心币种最新价格及 24h 变化。

    优先从 data_cache.market_snapshot 读取。
    """
    if _DATA_CACHE_SENTIMENT:
        try:
            snap = get_market_snapshot()
            if snap:
                result = {}
                for sym, pair in [("BTCUSDT", "BTCUSDT"), ("ETHUSDT", "ETHUSDT"),
                                  ("SOLUSDT", "SOLUSDT"), ("BNBUSDT", "BNBUSDT"),
                                  ("XRPUSDT", "XRPUSDT")]:
                    # market_snapshot 列名是小写加下划线
                    prefix = sym[:3].lower()
                    price = snap.get(f"{prefix}_price")
                    chg = snap.get(f"{prefix}_change_24h")
                    if price:
                        result[sym] = {
                            "price": float(price),
                            "change_24h": float(chg) if chg else None,
                            "volume_24h": None,
                        }
                if result:
                    return result
        except Exception:
            pass

    # 回退: 原逻辑
    sql = """
        SELECT
            k.symbol,
            k.close_price,
            s.price_change_pct_24h,
            s.quote_volume_24h
        FROM kline_data k
        JOIN price_stats_24h s ON k.symbol = s.symbol
        WHERE k.timeframe = '5m'
          AND k.exchange = 'binance_futures'
          AND k.symbol IN ({})
          AND k.open_time >= DATE_SUB(NOW(), INTERVAL 5 MINUTE)
        ORDER BY k.open_time DESC
    """
    placeholders = ",".join("%s" for _ in CORE_SYMBOLS)
    sql = sql.format(placeholders)

    with conn.cursor() as cur:
        cur.execute(sql, CORE_SYMBOLS)
        rows = cur.fetchall()

    # 去重：每个 symbol 只取最新行
    result = {}
    seen = set()
    for r in rows:
        sym = r["symbol"]
        if sym not in seen:
            seen.add(sym)
            result[sym] = {
                "price": float(r["close_price"]) if r.get("close_price") else None,
                "change_24h": float(r["price_change_pct_24h"]) if r.get("price_change_pct_24h") else None,
                "volume_24h": float(r["quote_volume_24h"]) if r.get("quote_volume_24h") else None,
            }
    return result


def _fetch_top_movers(conn, top_n: int = 10) -> dict:
    """获取 24h 涨幅/跌幅最大交易对。

    优先从 data_cache.market_movers_snapshot 读取。
    """
    if _DATA_CACHE_SENTIMENT:
        try:
            gainers = get_market_movers("gainers", top_n)
            losers = get_market_movers("losers", top_n)
            if gainers or losers:
                return {
                    "gainers": [
                        {"symbol": r["symbol"], "change": float(r["value"] or 0), "volume": 0}
                        for r in gainers
                    ],
                    "losers": [
                        {"symbol": r["symbol"], "change": float(r["value"] or 0), "volume": 0}
                        for r in losers
                    ],
                }
        except Exception:
            pass

    # 回退
    sql = """
        SELECT symbol, price_change_pct_24h, quote_volume_24h
        FROM price_stats_24h
        WHERE quote_volume_24h >= 10000000
          AND symbol LIKE '%%/USDT'
        ORDER BY price_change_pct_24h DESC
        LIMIT %s
    """
    result = {"gainers": [], "losers": []}
    with conn.cursor() as cur:
        cur.execute(sql, (top_n,))
        rows = cur.fetchall()
        for r in rows:
            result["gainers"].append({
                "symbol": r["symbol"],
                "change": float(r["price_change_pct_24h"]),
                "volume": float(r["quote_volume_24h"]),
            })

        cur.execute(
            sql.replace("DESC", "ASC"),
            (top_n,),
        )
        rows = cur.fetchall()
        for r in rows:
            result["losers"].append({
                "symbol": r["symbol"],
                "change": float(r["price_change_pct_24h"]),
                "volume": float(r["quote_volume_24h"]),
            })
    return result


def _fetch_funding_extremes(conn, top_n: int = 6) -> dict:
    """获取资金费率最高/最低的交易对。

    优先从 data_cache.market_movers_snapshot 读取。
    """
    if _DATA_CACHE_SENTIMENT:
        try:
            high = get_market_movers("funding_high", top_n)
            low = get_market_movers("funding_low", top_n)
            if high or low:
                return {
                    "highest": [
                        {"symbol": r["symbol"], "rate": float(r["value"] or 0) * 100}
                        for r in high
                    ],
                    "lowest": [
                        {"symbol": r["symbol"], "rate": float(r["value"] or 0) * 100}
                        for r in low
                    ],
                }
        except Exception:
            pass

    # 回退
    sql = """
        SELECT symbol, funding_rate, funding_time
        FROM funding_rate_data
        WHERE funding_time >= DATE_SUB(NOW(), INTERVAL 2 HOUR)
        ORDER BY funding_rate DESC
        LIMIT %s
    """
    result = {"highest": [], "lowest": []}
    with conn.cursor() as cur:
        cur.execute(sql, (top_n,))
        rows = cur.fetchall()
        for r in rows:
            result["highest"].append({
                "symbol": r["symbol"],
                "rate": float(r["funding_rate"]) * 100 if r.get("funding_rate") else 0,
            })

        cur.execute(
            sql.replace("DESC", "ASC"),
            (top_n,),
        )
        rows = cur.fetchall()
        for r in rows:
            result["lowest"].append({
                "symbol": r["symbol"],
                "rate": float(r["funding_rate"]) * 100 if r.get("funding_rate") else 0,
            })
    return result


def _fetch_big4_signal(conn) -> Optional[str]:
    """获取 Big4 市场趋势信号.

    优先从 data_cache.market_snapshot 读取.
    """
    if _DATA_CACHE_SENTIMENT:
        try:
            snap = get_market_snapshot()
            if snap and snap.get("big4_signal"):
                return str(snap["big4_signal"])
        except Exception:
            pass
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT setting_value FROM system_settings "
                "WHERE setting_key = 'big4_market_regime' LIMIT 1"
            )
            row = cur.fetchone()
            return str(row["setting_value"]).strip() if row else None
    except Exception:
        return None


def _fetch_fear_greed_index(conn) -> Optional[dict]:
    """从数据库中获取恐惧贪婪指数 (如果有)。"""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value, value_classification, update_time "
                "FROM fear_greed_index "
                "ORDER BY update_time DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                return {
                    "value": int(row["value"]) if row.get("value") else None,
                    "classification": row.get("value_classification"),
                    "update_time": row.get("update_time"),
                }
    except Exception:
        pass
    return None


def _fetch_trump_news(conn, limit: int = 10) -> List[dict]:
    """从新闻表中获取川普相关的最新新闻 (如果有)。"""
    try:
        with conn.cursor() as cur:
            # 尝试多个可能的新闻表
            news_tables = ["news_cache", "crypto_news", "market_news"]
            for tbl in news_tables:
                try:
                    cur.execute(
                        f"SELECT title, content, source, url, published_at, "
                        f"       sentiment_label, sentiment_score "
                        f"FROM {tbl} "
                        f"WHERE (title LIKE '%%川普%%' OR title LIKE '%%Trump%%' "
                        f"       OR content LIKE '%%川普%%' OR content LIKE '%%Trump%%' "
                        f"       OR source LIKE '%%twitter%%' OR source LIKE '%%Trump%%') "
                        f"  AND published_at >= DATE_SUB(NOW(), INTERVAL 48 HOUR) "
                        f"ORDER BY published_at DESC LIMIT %s",
                        (limit,),
                    )
                    rows = cur.fetchall()
                    if rows:
                        return [
                            {
                                "title": r.get("title", ""),
                                "content": r.get("content", "")[:500],
                                "source": r.get("source", ""),
                                "url": r.get("url", ""),
                                "published_at": str(r.get("published_at", ""))[:19],
                                "sentiment": r.get("sentiment_label"),
                            }
                            for r in rows
                        ]
                except Exception:
                    continue
    except Exception:
        pass
    return []


# ============================================================
# Gemini 调用
# ============================================================
def _call_gemini(prompt: str) -> Optional[str]:
    """调用 Gemini API 获取文本回复 (使用新版 google.genai SDK)。"""
    if not GEMINI_API_KEY:
        logger.error("[情绪分析] GEMINI_API_KEY 未设置")
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("[情绪分析] 缺依赖, 请 pip install google-genai")
        return None

    client = genai.Client(api_key=GEMINI_API_KEY)
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                top_p=0.95,
                max_output_tokens=4096,
                http_options=types.HttpOptions(timeout=SENTIMENT_TIMEOUT_S * 1000),
            ),
        )
        return resp.text
    except Exception as e:
        logger.error(f"[情绪分析] Gemini API 调用失败: {e}")
        return None


def _build_sentiment_prompt(
    core_prices: dict,
    movers: dict,
    funding: dict,
    big4_signal: Optional[str],
    fear_greed: Optional[dict],
    trump_news: List[dict],
) -> str:
    """构建市场情绪 + 川普分析的 Prompt。"""

    # 核心币种描述
    core_lines = []
    for sym, data in core_prices.items():
        change = data.get("change_24h")
        change_str = f"{change:+.2f}%" if change is not None else "N/A"
        vol = data.get("volume_24h")
        vol_str = f"${vol:,.0f}" if vol is not None else "N/A"
        core_lines.append(
            f"  - {sym}: 当前价 {data['price']}, 24h 涨跌 {change_str}, 24h 成交额 {vol_str}"
        )

    # 涨幅榜
    gainer_lines = []
    for g in movers.get("gainers", [])[:5]:
        gainer_lines.append(f"  - {g['symbol']}: +{g['change']:.2f}% (成交额 ${g['volume']:,.0f})")
    loser_lines = []
    for g in movers.get("losers", [])[:5]:
        loser_lines.append(f"  - {g['symbol']}: {g['change']:.2f}% (成交额 ${g['volume']:,.0f})")

    # 资金费率
    fund_high = " | ".join(f"{f['symbol']} {f['rate']:+.4f}%" for f in funding.get("highest", []))
    fund_low = " | ".join(f"{f['symbol']} {f['rate']:+.4f}%" for f in funding.get("lowest", []))

    # 恐惧贪婪
    fg_str = ""
    if fear_greed:
        fg_str = f"恐惧贪婪指数: {fear_greed['value']} ({fear_greed['classification']})"

    # 川普新闻
    trump_lines = []
    for n in trump_news:
        trump_lines.append(
            f"  - [{n['source']}] {n['title']} ({n['published_at']})"
        )
    trump_section = "\n".join(trump_lines) if trump_lines else "  (暂无川普相关新闻数据)"

    prompt = f"""你是超级交易大师。请基于以下市场数据，完成两项分析任务。

## 任务一：市场情绪综合分析
分析当前市场整体情绪状态，判断是否存在情绪躁动（FOMO/恐慌），并给出大方向决策建议。
注意：**请独立客观分析，不要受川普新闻的影响**。

## 任务二：川普推特/讲话深度解读
如果下方提供了川普相关的新闻，请分析其内容、重要性及对市场方向的潜在影响。
如果暂无川普新闻，请根据你对近期宏观政治经济形势的了解，提供政治/宏观层面的市场影响参考。

---

## 当前市场数据

### 核心币种价格
{chr(10).join(core_lines)}

### 24h 涨幅榜 Top 5
{chr(10).join(gainer_lines) if gainer_lines else "  无数据"}

### 24h 跌幅榜 Top 5
{chr(10).join(loser_lines) if loser_lines else "  无数据"}

### 资金费率极端
- 最高正费率: {fund_high or "无"}
- 最低负费率: {fund_low or "无"}

### 市场整体趋势信号
- Big4 市场状态: {big4_signal or "未知"}

### 市场情绪指标
{fg_str or "无恐惧贪婪数据"}

### 川普相关新闻
{trump_section}

---

## 输出格式要求

请严格按照以下 JSON 格式输出，不要添加额外内容：

```json
{{
    "market_sentiment": {{
        "overall_label": "bullish/bearish/neutral/anxious/euphoric",
        "sentiment_score": 0.00,
        "analysis_zh": "用中文详细分析当前市场情绪状态、是否存在情绪躁动、关键驱动因素",
        "direction_verdict": "用中文给出大方向决策建议，如短期是看多还是看空，是否需要警惕回调或反转"
    }},
    "trump_analysis": {{
        "analysis_zh": "用中文深度解读川普近期重要言论/政策信号，分析其对加密货币市场的潜在影响",
        "impact_label": "positive/negative/neutral/mixed",
        "impact_score": 0.00,
        "key_topics": "川普讲话涉及的关键主题，逗号分隔",
        "market_impact": "用中文描述对市场方向的具体影响判断"
    }}
}}
```

评分说明:
- sentiment_score: 0.00(极度悲观) ~ 1.00(极度乐观), 0.50=中性
- impact_score: -1.00(强烈利空) ~ 1.00(强烈利好), 0=中性
- overall_label: euphoric(极度贪婪/FOMO) > anxious(恐慌) > bullish/bearish/neutral
- impact_label: positive/negative/neutral/mixed
"""
    return prompt


# ============================================================
# 主入口
# ============================================================
def run_sentiment_round(triggered_by: str = "scheduler"):
    """
    每 8h 执行一轮市场情绪 + 川普分析。

    Args:
        triggered_by: 'scheduler' 或 'manual'
    """
    t0 = time.time()
    conn = None
    run_id = None

    try:
        # 1. Kill switch 检查
        conn = _connect()
        with conn.cursor() as cur:
            enabled = _read_setting(cur, "gemini_sentiment_enabled", "1")
        if enabled != "1":
            logger.info("[情绪分析] 已禁用 (kill switch), 跳过本轮")
            return

        # 2. 采集数据
        logger.info("[情绪分析] 开始采集市场数据...")
        core_prices = _fetch_core_prices(conn)
        logger.info(f"[情绪分析] 核心币种: {len(core_prices)} 个")

        movers = _fetch_top_movers(conn)
        logger.info(f"[情绪分析] 涨跌榜: {len(movers.get('gainers', []))} 涨 / {len(movers.get('losers', []))} 跌")

        funding = _fetch_funding_extremes(conn)
        logger.info(f"[情绪分析] 资金费率极端: {len(funding.get('highest', []))} 正 / {len(funding.get('lowest', []))} 负")

        big4_signal = _fetch_big4_signal(conn)
        logger.info(f"[情绪分析] Big4 信号: {big4_signal}")

        fear_greed = _fetch_fear_greed_index(conn)
        logger.info(f"[情绪分析] 恐惧贪婪: {fear_greed}")

        trump_news = _fetch_trump_news(conn)
        logger.info(f"[情绪分析] 川普新闻: {len(trump_news)} 条")

        # 3. 构建 Prompt 并调用 Gemini
        logger.info("[情绪分析] 调用 Gemini...")
        prompt = _build_sentiment_prompt(
            core_prices, movers, funding, big4_signal, fear_greed, trump_news
        )
        raw_text = _call_gemini(prompt)

        if not raw_text:
            logger.error("[情绪分析] Gemini 返回空, 插入 error 记录")
            _insert_run_record(
                conn, "error", "Gemini 返回空", t0,
                triggered_by, None, None, None, None, None, None, None, None, None,
            )
            return

        # 4. 解析 JSON
        parsed = _parse_sentiment_json(raw_text)
        if parsed.get("_error"):
            logger.error(f"[情绪分析] JSON 解析失败: {parsed['_error']}")
            _insert_run_record(
                conn, "error", f"JSON 解析失败: {parsed['_error']}", t0,
                triggered_by,
                raw_text[:500], None, None, None,
                raw_text[:500], None, None, None, None,
            )
            return

        sentiment = parsed.get("market_sentiment", {})
        trump = parsed.get("trump_analysis", {})

        # 5. 插入运行记录
        run_id = _insert_run_record(
            conn, "ok", None, t0,
            triggered_by,
            sentiment.get("analysis_zh"),
            sentiment.get("overall_label"),
            sentiment.get("sentiment_score"),
            sentiment.get("direction_verdict"),
            trump.get("analysis_zh"),
            trump.get("impact_label"),
            trump.get("impact_score"),
            trump.get("key_topics"),
            trump.get("market_impact"),
        )

        elapsed = time.time() - t0
        logger.info(
            "[情绪分析] 完成 (id={}, {:.1f}s) — 情绪标签={}, 川普影响={}",
            run_id, elapsed,
            sentiment.get("overall_label", "?"),
            trump.get("impact_label", "?"),
        )

    except Exception as e:
        elapsed = time.time() - t0
        logger.error(f"[情绪分析] 异常: {e}", exc_info=True)
        if conn:
            try:
                _insert_run_record(
                    conn, "error", str(e), t0,
                    triggered_by, None, None, None, None, None, None, None, None, None,
                )
            except Exception:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def _parse_sentiment_json(raw: str) -> dict:
    """从 Gemini 回复中提取 JSON 部分并解析。"""
    # 尝试提取 ```json ... ``` 块
    m = __import__("re").search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    body = m.group(1).strip() if m else raw.strip()

    # 尝试找第一个 { 到最后一个 }
    start = body.find("{")
    end = body.rfind("}")
    if start >= 0 and end > start:
        body = body[start : end + 1]

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return {"_error": f"JSON 解析失败: {e}, raw={body[:300]}"}

    # 确保结构完整
    if "market_sentiment" not in data:
        return {"_error": "缺少 market_sentiment 字段"}
    return data


def _insert_run_record(
    conn,
    status: str,
    error_msg: Optional[str],
    t0: float,
    triggered_by: str,
    sentiment_summary_zh: Optional[str],
    market_sentiment_label: Optional[str],
    market_sentiment_score: Optional[float],
    market_direction_verdict: Optional[str],
    trump_analysis_zh: Optional[str],
    trump_impact_label: Optional[str],
    trump_impact_score: Optional[float],
    trump_key_topics: Optional[str],
    trump_market_impact: Optional[str],
) -> Optional[int]:
    """插入运行记录到 gemini_sentiment_runs。
    
    所有 varchar 字段做了截断保护，避免 Data too long 错误。
    """
    elapsed = time.time() - t0
    asof_utc = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

    # 截断保护 (对应 DB 列长度)
    market_sentiment_label = (market_sentiment_label or '')[:20] or None
    market_direction_verdict = (market_direction_verdict or '')[:500] or None
    trump_impact_label = (trump_impact_label or '')[:20] or None
    trump_key_topics = (trump_key_topics or '')[:500] or None
    trump_market_impact = (trump_market_impact or '')[:200] or None
    error_msg = (error_msg or '')[:500] or None

    sql = """
        INSERT INTO gemini_sentiment_runs
            (asof_utc, model, elapsed_s, status, error_msg, triggered_by,
             sentiment_summary_zh, market_sentiment_label, market_sentiment_score,
             market_direction_verdict,
             trump_analysis_zh, trump_impact_label, trump_impact_score,
             trump_key_topics, trump_market_impact)
        VALUES (%s, %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s,
                %s, %s, %s,
                %s, %s)
    """
    params = (
        asof_utc, GEMINI_MODEL, elapsed, status, error_msg, triggered_by,
        sentiment_summary_zh, market_sentiment_label, market_sentiment_score,
        market_direction_verdict,
        trump_analysis_zh, trump_impact_label, trump_impact_score,
        trump_key_topics, trump_market_impact,
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        run_id = cur.lastrowid
    return run_id


# ============================================================
# 手动触发入口
# ============================================================
_run_lock = threading.Lock()


def run_sentiment_round_async(triggered_by: str = "manual"):
    """在后台线程启动一轮。"""
    if not _run_lock.acquire(blocking=False):
        logger.warning("[情绪分析] 上一轮还在跑, 跳过")
        return False

    def _worker():
        try:
            run_sentiment_round(triggered_by=triggered_by)
        finally:
            try:
                _run_lock.release()
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True, name="sentiment_manual")
    t.start()
    return True
