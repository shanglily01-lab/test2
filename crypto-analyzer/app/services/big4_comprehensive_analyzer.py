"""
Big4 综合行情 LLM 分析 — Gemini / DeepSeek 共用 worker.

每 2h 对 BTC/ETH/BNB/SOL 提供:
  - 7 根 1d K 线
  - 24 根 1h K 线
  - 48 根 15m K 线
并输出综合走势判断 (不做下单依据)。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.data_cache_service import (
    MAIN_DB,
    _fetch_klines,
    _make_kline_narrative,
)
from app.services.gemini_swan_worker import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    _read_setting,
)

BIG4_COINS: List[Tuple[str, str]] = [
    ("BTC/USDT", "BTCUSDT"),
    ("ETH/USDT", "ETHUSDT"),
    ("BNB/USDT", "BNBUSDT"),
    ("SOL/USDT", "SOLUSDT"),
]

INTERVAL_HOURS = 2
ANALYSIS_TIMEOUT_S = int(os.getenv("BIG4_ANALYSIS_TIMEOUT_S", "300"))

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("DeepSeek_API_KEY", "")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

PROVIDER_CONFIG = {
    "gemini": {
        "setting_key": "gemini_big4_analysis_enabled",
        "default_enabled": "1",
        "model": GEMINI_MODEL,
    },
    "deepseek": {
        "setting_key": "deepseek_big4_analysis_enabled",
        "default_enabled": "1",
        "model": DEEPSEEK_MODEL,
    },
}

_run_locks = {
    "gemini": threading.Lock(),
    "deepseek": threading.Lock(),
}


def _connect():
    from app.utils.config_loader import get_db_config
    return pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _table_exists(conn, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE %s", (table,))
        return cur.fetchone() is not None


def _fetch_big4_quant(conn) -> dict:
    out = {"overall_signal": "NEUTRAL", "detail": {}}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT overall_signal, signal_strength, "
                "btc_signal, eth_signal, bnb_signal, sol_signal, "
                "btc_price_change_6h, created_at "
                "FROM big4_trend_history ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                out["overall_signal"] = row.get("overall_signal") or "NEUTRAL"
                out["detail"] = row
    except Exception as e:
        logger.warning(f"[Big4分析] 读 big4_trend_history 失败: {e}")
    return out


def _fetch_coin_klines(cur, symbol: str) -> dict:
    k1d = _fetch_klines(cur, MAIN_DB, symbol, "1d", 7)
    k1h = _fetch_klines(cur, MAIN_DB, symbol, "1h", 24)
    k15 = _fetch_klines(cur, MAIN_DB, symbol, "15m", 48)
    return {
        "1d": k1d,
        "1h": k1h,
        "15m": k15,
        "narrative_1d": _make_kline_narrative(k1d, "1d"),
        "narrative_1h": _make_kline_narrative(k1h, "1h"),
        "narrative_15m": _format_15m_bars(k15),
    }


def _format_15m_bars(k_rows: List[Dict]) -> str:
    """15m ×48: 全量 OHLC 紧凑列表 (探索页 cache 叙事只保留 4 根, 此处需完整)."""
    if not k_rows:
        return "[15m] 无数据"
    lines = []
    for r in k_rows:
        try:
            o = float(r["open_price"])
            h = float(r["high_price"])
            l = float(r["low_price"])
            c = float(r["close_price"])
            pct = (c - o) / o * 100 if o else 0
            t = r.get("open_time")
            t_str = t.strftime("%m-%d %H:%M") if hasattr(t, "strftime") else str(t)
            lines.append(f"  {t_str}: O={o:.6g} H={h:.6g} L={l:.6g} C={c:.6g} ({pct:+.2f}%)")
        except Exception:
            continue
    return f"[15m · {len(k_rows)} 根明细]\n" + "\n".join(lines)


def _build_prompt(big4_quant: dict, coin_data: dict) -> str:
    quant = big4_quant.get("overall_signal", "NEUTRAL")
    detail = big4_quant.get("detail") or {}
    quant_lines = [
        f"- 量化综合信号: {quant} (强度 {detail.get('signal_strength', 'N/A')})",
        f"- BTC 子信号: {detail.get('btc_signal', 'N/A')}",
        f"- ETH 子信号: {detail.get('eth_signal', 'N/A')}",
        f"- BNB 子信号: {detail.get('bnb_signal', 'N/A')}",
        f"- SOL 子信号: {detail.get('sol_signal', 'N/A')}",
        f"- BTC 近 6h 涨跌: {detail.get('btc_price_change_6h', 'N/A')}%",
    ]

    coin_sections = []
    for display, sym in BIG4_COINS:
        cd = coin_data.get(sym, {})
        coin_sections.append(
            f"### {display}\n"
            f"#### 1d × 7\n{cd.get('narrative_1d', '[无数据]')}\n\n"
            f"#### 1h × 24\n{cd.get('narrative_1h', '[无数据]')}\n\n"
            f"#### 15m × 48\n{cd.get('narrative_15m', '[无数据]')}"
        )

    return f"""你是加密货币 Big4 (BTC/ETH/BNB/SOL) 综合行情分析师。
请基于下方多周期 K 线结构 + 量化 Big4 信号，给出 **Big4 综合走势分析**。
⚠ 本分析仅供宏观参考，**不是**开仓/平仓指令。

## 量化 Big4 背景 (规则引擎, 仅供参考)
{chr(10).join(quant_lines)}

## 各币 K 线 (UTC 收盘, 由旧到新)

{chr(10).join(coin_sections)}

---

## 分析要求
1. 先看 1d×7 定大方向, 再用 1h×24 看中期结构, 15m×48 看近端动能/拐点。
2. 四币权重: BTC>ETH>BNB≈SOL; 需说明是否共振或分化。
3. 输出 JSON ONLY:

```json
{{
  "overall_label": "bullish/bearish/neutral/sideways/mixed",
  "overall_score": 0.0,
  "direction_verdict": "中文 2-4 句: 未来 4-12h Big4 综合倾向",
  "analysis_summary_zh": "中文详细分析: 各周期结构、共振/分化、关键位、风险",
  "coins": {{
    "BTC": {{"label": "bullish/bearish/neutral", "score": 0.0, "note_zh": "该币多周期要点"}},
    "ETH": {{"label": "...", "score": 0.0, "note_zh": "..."}},
    "BNB": {{"label": "...", "score": 0.0, "note_zh": "..."}},
    "SOL": {{"label": "...", "score": 0.0, "note_zh": "..."}}
  }}
}}
```

overall_score: -1.0(极度看空) ~ +1.0(极度看多), 0=中性。
"""


def _parse_json(raw: str) -> dict:
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    body = m.group(1).strip() if m else raw.strip()
    start = body.find("{")
    end = body.rfind("}")
    if start >= 0 and end > start:
        body = body[start: end + 1]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return {"_error": f"JSON 解析失败: {e}"}
    if "overall_label" not in data:
        return {"_error": "缺少 overall_label"}
    return data


def _call_gemini(prompt: str) -> Optional[str]:
    if not GEMINI_API_KEY:
        logger.error("[Big4分析/Gemini] GEMINI_API_KEY 未设置")
        return None
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("[Big4分析/Gemini] 缺 google-genai")
        return None
    client = genai.Client(api_key=GEMINI_API_KEY)
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                top_p=0.9,
                max_output_tokens=8192,
                http_options=types.HttpOptions(timeout=ANALYSIS_TIMEOUT_S * 1000),
            ),
        )
        return resp.text
    except Exception as e:
        logger.error(f"[Big4分析/Gemini] API 失败: {e}")
        return None


def _call_deepseek(prompt: str) -> Optional[str]:
    if not DEEPSEEK_API_KEY:
        logger.error("[Big4分析/DeepSeek] DEEPSEEK_API_KEY 未设置")
        return None
    try:
        from openai import OpenAI
    except ImportError:
        logger.error("[Big4分析/DeepSeek] 缺 openai 库")
        return None
    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "You are a crypto Big4 market analyst. Output ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=8192,
            timeout=ANALYSIS_TIMEOUT_S,
            response_format={"type": "json_object"},
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        logger.error(f"[Big4分析/DeepSeek] API 失败: {e}")
        return None


def _should_skip_interval(conn, provider: str, triggered_by: str) -> bool:
    if triggered_by == "manual":
        return False
    with conn.cursor() as cur:
        cur.execute(
            "SELECT asof_utc FROM big4_analysis_runs "
            "WHERE provider=%s AND status='ok' ORDER BY id DESC LIMIT 1",
            (provider,),
        )
        row = cur.fetchone()
    if not row or not row.get("asof_utc"):
        return False
    last = row["asof_utc"]
    if hasattr(last, "timestamp"):
        elapsed_h = (datetime.now() - last).total_seconds() / 3600
    else:
        return False
    if elapsed_h < INTERVAL_HOURS - 0.05:
        logger.info(
            f"[Big4分析/{provider}] 上次成功距今 {elapsed_h:.1f}h < {INTERVAL_HOURS}h, 跳过"
        )
        return True
    return False


def _insert_run(
    conn,
    provider: str,
    status: str,
    error_msg: Optional[str],
    t0: float,
    triggered_by: str,
    model: str,
    big4_quant: dict,
    parsed: Optional[dict],
    prompt: str,
    raw: Optional[str],
) -> Optional[int]:
    elapsed = time.time() - t0
    asof_utc = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    overall_label = None
    overall_score = None
    direction_verdict = None
    analysis_summary_zh = None
    per_coin_json = None
    if parsed and not parsed.get("_error"):
        overall_label = (parsed.get("overall_label") or "")[:32] or None
        overall_score = parsed.get("overall_score")
        direction_verdict = (parsed.get("direction_verdict") or "")[:500] or None
        analysis_summary_zh = parsed.get("analysis_summary_zh")
        coins = parsed.get("coins")
        if coins:
            per_coin_json = json.dumps(coins, ensure_ascii=False)

    sql = """
        INSERT INTO big4_analysis_runs
            (provider, asof_utc, model, elapsed_s, status, error_msg, triggered_by,
             big4_quant_signal, overall_label, overall_score, direction_verdict,
             analysis_summary_zh, per_coin_json, prompt_text, raw_response)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    params = (
        provider,
        asof_utc,
        model,
        round(elapsed, 2),
        status,
        (error_msg or "")[:500] or None,
        triggered_by,
        (big4_quant.get("overall_signal") or "")[:32] or None,
        overall_label,
        overall_score,
        direction_verdict,
        analysis_summary_zh,
        per_coin_json,
        prompt[:500000] if prompt else None,
        raw[:500000] if raw else None,
    )
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.lastrowid


def run_big4_analysis_round(provider: str, triggered_by: str = "scheduler") -> None:
    """执行一轮 Big4 综合行情分析."""
    provider = provider.lower()
    if provider not in PROVIDER_CONFIG:
        raise ValueError(f"unknown provider: {provider}")

    cfg = PROVIDER_CONFIG[provider]
    lock = _run_locks[provider]
    if not lock.acquire(blocking=False):
        logger.info(f"[Big4分析/{provider}] 上一轮还未结束, 跳过")
        return

    t0 = time.time()
    conn = None
    try:
        conn = _connect()
        if not _table_exists(conn, "big4_analysis_runs"):
            logger.error("[Big4分析] 表 big4_analysis_runs 不存在, 请执行 migration 012")
            return

        with conn.cursor() as cur:
            enabled = _read_setting(cur, cfg["setting_key"], cfg["default_enabled"])
        if enabled != "1":
            logger.info(f"[Big4分析/{provider}] 已禁用, 跳过")
            return

        if _should_skip_interval(conn, provider, triggered_by):
            return

        logger.info(f"[Big4分析/{provider}] 开始采集 K 线...")
        big4_quant = _fetch_big4_quant(conn)
        coin_data = {}
        with conn.cursor() as cur:
            for _display, sym in BIG4_COINS:
                coin_data[sym] = _fetch_coin_klines(cur, sym)

        prompt = _build_prompt(big4_quant, coin_data)
        logger.info(f"[Big4分析/{provider}] 调用 LLM, prompt≈{len(prompt)} chars")

        if provider == "gemini":
            raw = _call_gemini(prompt)
        else:
            raw = _call_deepseek(prompt)

        if not raw:
            _insert_run(conn, provider, "error", "LLM 返回空", t0, triggered_by, cfg["model"],
                        big4_quant, None, prompt, None)
            return

        parsed = _parse_json(raw)
        if parsed.get("_error"):
            _insert_run(conn, provider, "error", parsed["_error"], t0, triggered_by, cfg["model"],
                        big4_quant, None, prompt, raw)
            return

        run_id = _insert_run(conn, provider, "ok", None, t0, triggered_by, cfg["model"],
                             big4_quant, parsed, prompt, raw)
        logger.info(
            f"[Big4分析/{provider}] 完成 run_id={run_id} label={parsed.get('overall_label')} "
            f"elapsed={time.time()-t0:.1f}s"
        )
    except Exception as e:
        logger.error(f"[Big4分析/{provider}] 异常: {e}", exc_info=True)
        if conn:
            try:
                _insert_run(conn, provider, "error", str(e), t0, triggered_by,
                            PROVIDER_CONFIG[provider]["model"], {}, None, "", None)
            except Exception:
                pass
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        try:
            lock.release()
        except Exception:
            pass


def run_big4_analysis_round_gemini(triggered_by: str = "scheduler") -> None:
    run_big4_analysis_round("gemini", triggered_by=triggered_by)


def run_big4_analysis_round_deepseek(triggered_by: str = "scheduler") -> None:
    run_big4_analysis_round("deepseek", triggered_by=triggered_by)
