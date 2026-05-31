"""
AI Shadow 对比 — Teacher (Gemini/DeepSeek 探索/预测/战术/顶空底多) 跑完后,
用相同 universe 跑规则引擎.

不开仓, 只落库对比 category/confidence, 积累样本用于后续蒸馏超级策略.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pymysql
from loguru import logger

from app.utils.config_loader import get_db_config

SHADOW_RULES_VERSION = "v1"
SHADOW_TRADEABLE_THRESHOLD = 0.50  # 与 explore EXPLORE_CONFIDENCE_THRESHOLD 对齐


def _connect():
    cfg = get_db_config()
    return pymysql.connect(
        **cfg,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _normalize_category(cat: str) -> str:
    c = (cat or "skip").lower().strip()
    if c in ("bullish", "long", "buy"):
        return "bullish"
    if c in ("bearish", "short", "sell"):
        return "bearish"
    return "skip"


def _normalize_teacher_category(cat: str, teacher_source: str) -> str:
    """探索/预测用 bullish/bearish; 战术 entry / 顶空底多映射为同向 category."""
    c = (cat or "skip").lower().strip()
    if c in ("bullish", "long", "buy"):
        return "bullish"
    if c in ("bearish", "short", "sell"):
        return "bearish"
    if c == "top_reversal":
        return "bearish"
    if c == "bottom_reversal":
        return "bullish"
    if c in ("entry", "signal", "trade"):
        src = (teacher_source or "").lower()
        if src.endswith(("_pullback", "_chase")):
            return "bullish"
        if src.endswith(("_rebound", "_dump")):
            return "bearish"
    return "skip"


def normalize_symbol_data_for_shadow(sym_data: dict) -> dict:
    """探索 universe 与预测 symbols_data 统一为 evaluate_explore_symbol 可读结构."""
    if not sym_data:
        return sym_data
    out = dict(sym_data)
    sym = (out.get("symbol") or "").upper()
    if sym:
        out["symbol"] = sym
    tech = dict(out.get("tech") or {})
    if tech.get("rsi_14_1h") is None and out.get("rsi_14_1h") is not None:
        tech["rsi_14_1h"] = out["rsi_14_1h"]
    if tech.get("above_7d_low_pct") is None and out.get("above_7d_low_pct") is not None:
        tech["above_7d_low_pct"] = out["above_7d_low_pct"]
    if tech.get("below_7d_high_pct") is None and out.get("below_7d_high_pct") is not None:
        tech["below_7d_high_pct"] = out["below_7d_high_pct"]
    out["tech"] = tech
    if out.get("current_rate") is None and out.get("funding_rate") is not None:
        out["current_rate"] = out["funding_rate"]
    return out


def build_shadow_universe(universe_or_list) -> Dict[str, dict]:
    """dict[symbol] 或 list[{symbol,...}] → 规范化 universe."""
    if isinstance(universe_or_list, dict):
        out: Dict[str, dict] = {}
        for key, val in universe_or_list.items():
            sym = (key or "").upper()
            if not sym:
                continue
            row = val if isinstance(val, dict) else {"symbol": sym}
            if not row.get("symbol"):
                row = dict(row)
                row["symbol"] = sym
            out[sym] = normalize_symbol_data_for_shadow(row)
        return out
    out = {}
    for row in universe_or_list or []:
        if not isinstance(row, dict):
            continue
        sym = (row.get("symbol") or "").upper()
        if sym:
            out[sym] = normalize_symbol_data_for_shadow(row)
    return out


def _narrative_bias(text: str) -> float:
    """从 kline 叙事文本提取多空倾向 (-2 ~ +2)."""
    if not text or "无数据" in text:
        return 0.0
    score = 0.0
    if any(k in text for k in ("偏多", "强势上升", "阳线", "放量上攻", "突破")):
        score += 2.0
    elif any(k in text for k in ("偏空", "强势下降", "阴线", "放量下杀", "跌破")):
        score -= 2.0
    elif "震荡" in text:
        score += 0.0
    if "↑" in text and "↓" not in text:
        score += 0.5
    elif "↓" in text and "↑" not in text:
        score -= 0.5
    return max(-2.0, min(2.0, score))


def evaluate_explore_symbol(sym_data: dict, global_ctx: dict) -> dict:
    """
    规则引擎 v1 — 从 explore prompt 提炼的可量化规则 (可迭代 rules_version).
    返回: category, confidence, signals[], reason
    """
    signals: List[str] = []
    sym = sym_data.get("symbol", "?")
    tech = sym_data.get("tech") or {}
    chg = float(sym_data.get("change_24h") or 0)
    fr = float(sym_data.get("current_rate") or sym_data.get("funding_rate") or 0)
    rsi = tech.get("rsi_14_1h")
    if rsi is not None:
        rsi = float(rsi)

    narr = sym_data.get("kline_narrative") or {}
    narr_1h = narr.get("1h") or ""
    narr_15m = narr.get("15m") or ""

    # F. 死猫 / 极端波动
    if abs(chg) >= 20:
        return {
            "category": "skip",
            "confidence": 0.35,
            "signals": ["dead_cat_24h"],
            "reason": f"|change_24h|={chg:.1f}%>=20",
        }

    if not narr_1h or "无数据" in str(narr_1h):
        return {
            "category": "skip",
            "confidence": 0.2,
            "signals": ["missing_1h_narrative"],
            "reason": "缺少 1h K 线叙事",
        }

    score = 0.0

    b1h = _narrative_bias(narr_1h)
    if b1h > 0:
        score += b1h
        signals.append("narr_1h_bull")
    elif b1h < 0:
        score += b1h
        signals.append("narr_1h_bear")

    b15 = _narrative_bias(narr_15m) * 0.5
    if abs(b15) >= 0.5:
        score += b15
        signals.append("narr_15m_bias")

    # 24h 动量 (趋势延续 A)
    if chg >= 5:
        score += 1.0
        signals.append("mom_24h_up")
    elif chg <= -5:
        score -= 1.0
        signals.append("mom_24h_down")
    if chg >= 10:
        score += 0.5
    elif chg <= -10:
        score -= 0.5

    # RSI + 费率背离 B
    if rsi is not None:
        if rsi <= 32:
            score += 1.5
            signals.append("rsi_oversold")
        elif rsi >= 68:
            score -= 1.5
            signals.append("rsi_overbought")
        if fr > 0.0008 and rsi >= 65:
            score -= 1.0
            signals.append("funding_crowded_long")
        elif fr < -0.0003 and rsi <= 40:
            score += 1.0
            signals.append("funding_crowded_short")

    # 7d 空间
    above_low = tech.get("above_7d_low_pct")
    below_high = tech.get("below_7d_high_pct")
    if above_low is not None and float(above_low) > 8 and score > 0:
        score += 0.5
        signals.append("room_above_7d_low")
    if below_high is not None and float(below_high) < -3 and score < 0:
        score -= 0.5
        signals.append("room_below_7d_high")

    # Big4 仅微调置信度, 不单边禁多/空
    big4 = (global_ctx.get("big4_signal") or "NEUTRAL").upper()
    conf_penalty = 0.0
    if big4 in ("STRONG_BEARISH", "BEARISH") and score > 0:
        conf_penalty = 0.05
        signals.append("big4_soft_bear")
    elif big4 in ("STRONG_BULLISH", "BULLISH") and score < 0:
        conf_penalty = 0.05
        signals.append("big4_soft_bull")

    # 映射 score → category + confidence
    if score >= 2.5:
        category, base_conf = "bullish", 0.68 + min(0.17, (score - 2.5) * 0.06)
    elif score <= -2.5:
        category, base_conf = "bearish", 0.68 + min(0.17, (abs(score) - 2.5) * 0.06)
    elif score >= 1.5:
        category, base_conf = "bullish", 0.58 + min(0.09, (score - 1.5) * 0.05)
    elif score <= -1.5:
        category, base_conf = "bearish", 0.58 + min(0.09, (abs(score) - 1.5) * 0.05)
    else:
        category, base_conf = "skip", max(0.25, 0.45 - abs(score) * 0.08)

    confidence = round(max(0.0, min(0.92, base_conf - conf_penalty)), 4)
    reason = f"score={score:+.2f} big4={big4}"

    return {
        "category": category,
        "confidence": confidence,
        "signals": signals,
        "reason": reason,
    }


def _is_tradeable(category: str, confidence: float) -> bool:
    cat = _normalize_category(category)
    return cat in ("bullish", "bearish") and confidence >= SHADOW_TRADEABLE_THRESHOLD


def _teacher_map(teacher_verdicts: List[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for v in teacher_verdicts or []:
        sym = (v.get("symbol") or "").upper().strip()
        if sym:
            out[sym] = v
    return out


def run_shadow_after_teacher_explore(
    teacher_source: str,
    teacher_run_id: int,
    universe: Dict[str, dict],
    global_ctx: dict,
    teacher_verdicts: List[dict],
    conn=None,
) -> Optional[int]:
    """
    Teacher 策略轮次成功后立即调用 (探索/预测/战术/顶空底多).
    使用内存中同一 universe/global_ctx, 不开仓. 返回 shadow_run_id 或 None.
    """
    own_conn = conn is None
    if own_conn:
        conn = _connect()

    universe = build_shadow_universe(universe)

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT setting_value FROM system_settings "
                "WHERE setting_key='ai_shadow_compare_enabled' LIMIT 1"
            )
            row = cur.fetchone()
            val = (row or {}).get("setting_value", "1")
            if val is not None:
                val = str(val).strip().lower()
            else:
                val = "1"
            if val not in ("1", "true", "yes", "on"):
                logger.info(
                    f"[Shadow] ai_shadow_compare_enabled=0, 跳过 "
                    f"{teacher_source} run={teacher_run_id}"
                )
                return None

            cur.execute(
                "SELECT id FROM ai_shadow_compare_runs "
                "WHERE teacher_source=%s AND teacher_run_id=%s LIMIT 1",
                (teacher_source, teacher_run_id),
            )
            if cur.fetchone():
                logger.info(f"[Shadow] 已对比过 {teacher_source} run={teacher_run_id}, 跳过")
                return None

        t0 = time.time()
        tmap = _teacher_map(teacher_verdicts)
        symbols = sorted(set(universe.keys()) | set(tmap.keys()))

        verdict_rows: List[Tuple] = []
        category_match = 0
        teacher_tradeable = 0
        shadow_tradeable = 0
        tradeable_agree = 0
        disagree_samples: List[dict] = []

        for sym in symbols:
            sym_data = universe.get(sym)
            if not sym_data:
                continue

            shadow = evaluate_explore_symbol(sym_data, global_ctx)
            s_cat = _normalize_category(shadow["category"])
            s_conf = float(shadow["confidence"])

            tv = tmap.get(sym)
            if tv:
                t_cat = _normalize_teacher_category(
                    tv.get("category"), teacher_source,
                )
                try:
                    t_conf = float(tv.get("confidence") or 0)
                except (TypeError, ValueError):
                    t_conf = 0.0
            else:
                t_cat, t_conf = "skip", 0.0

            matched = t_cat == s_cat
            if matched:
                category_match += 1

            t_trade = _is_tradeable(t_cat, t_conf)
            s_trade = _is_tradeable(s_cat, s_conf)
            if t_trade:
                teacher_tradeable += 1
            if s_trade:
                shadow_tradeable += 1
            if t_trade and s_trade and t_cat == s_cat:
                tradeable_agree += 1
            elif t_trade and s_trade and t_cat != s_cat:
                pass  # direction conflict among tradeable

            diff_reason = None
            if not matched:
                diff_reason = (
                    f"teacher={t_cat}({t_conf:.2f}) shadow={s_cat}({s_conf:.2f}) "
                    f"| {shadow.get('reason', '')}"
                )
                if len(disagree_samples) < 15:
                    disagree_samples.append({
                        "symbol": sym,
                        "teacher": f"{t_cat}@{t_conf:.2f}",
                        "shadow": f"{s_cat}@{s_conf:.2f}",
                        "reason": shadow.get("reason"),
                        "signals": shadow.get("signals"),
                    })

            verdict_rows.append((
                sym,
                t_cat,
                t_conf,
                s_cat,
                s_conf,
                1 if matched else 0,
                (diff_reason or "")[:500] or None,
                json.dumps(shadow.get("signals") or [], ensure_ascii=False),
            ))

        compared = len(verdict_rows)
        if compared == 0:
            logger.warning(f"[Shadow] {teacher_source} run={teacher_run_id} 无 symbol 可对比")
            return None

        elapsed_ms = int((time.time() - t0) * 1000)
        agree_pct = category_match / compared * 100 if compared else 0.0

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai_shadow_compare_runs
                  (teacher_source, teacher_run_id, rules_version, universe_size,
                   compared_count, category_match, direction_match,
                   teacher_tradeable, shadow_tradeable, tradeable_agree,
                   disagree_samples, elapsed_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    teacher_source,
                    teacher_run_id,
                    SHADOW_RULES_VERSION,
                    len(universe),
                    compared,
                    category_match,
                    category_match,
                    teacher_tradeable,
                    shadow_tradeable,
                    tradeable_agree,
                    json.dumps(disagree_samples, ensure_ascii=False),
                    elapsed_ms,
                ),
            )
            shadow_run_id = cur.lastrowid

            rows_with_sid = [(shadow_run_id,) + r for r in verdict_rows]
            cur.executemany(
                """
                INSERT INTO ai_shadow_verdicts
                  (shadow_run_id, symbol, teacher_category, teacher_confidence,
                   shadow_category, shadow_confidence, category_match,
                   diff_reason, shadow_signals)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows_with_sid,
            )

        logger.info(
            f"[Shadow] {teacher_source} run={teacher_run_id} shadow_run={shadow_run_id} "
            f"一致率={agree_pct:.1f}% ({category_match}/{compared}) "
            f"可交易 teacher={teacher_tradeable} shadow={shadow_tradeable} "
            f"同向={tradeable_agree} 耗时={elapsed_ms}ms"
        )
        if disagree_samples:
            top = disagree_samples[:3]
            logger.info(f"[Shadow] 差异样例: {top}")
        return shadow_run_id

    except Exception as e:
        logger.warning(f"[Shadow] 对比失败 {teacher_source} run={teacher_run_id}: {e}")
        return None
    finally:
        if own_conn:
            try:
                conn.close()
            except Exception:
                pass


def compare_teacher_run_from_db(teacher_source: str, teacher_run_id: int) -> Optional[int]:
    """
    从 DB 重跑对比 (调试/补跑). 仅 explore: 需 teacher run 存了 raw_response.
    """
    # 探索 worker 未持久化 universe JSON; 补跑需下轮 hook. 预留接口.
    logger.warning(
        f"[Shadow] compare_teacher_run_from_db 暂未实现 universe 回放 "
        f"({teacher_source} run={teacher_run_id})"
    )
    return None
