"""Gemini / DeepSeek 探索 — 共用 prompt (v2 技术面强制).

核心: verdict 必须来自 K 线形态、成交量、RSI、多周期结构;
禁止仅用 24h 涨跌幅、资金费率、宏观/Big4 做主观多空.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from app.services.ai_big4_prompt import BIG4_PROMPT_BLOCK_EXPLORE, BIG4_PROMPT_BLOCK_EXPLORE_EN

# 送入 LLM 的 symbol 上限 (全池 ~200 时 prompt/output 都会爆)
EXPLORE_LLM_MAX_SYMBOLS = 50
EXPLORE_LLM_MAX_OUTPUT_TOKENS = 8192
def get_ai_position_hold_hours() -> int:
    """计划持仓时长（小时）。读 system_settings.max_hold_hours。"""
    from app.services.system_settings_loader import get_max_hold_hours
    return get_max_hold_hours()


def get_ai_schedule_interval_hours() -> int:
    """AI 探索/预测调度周期（小时），与持仓时长共用 max_hold_hours，范围 2~8。"""
    return get_ai_position_hold_hours()


def get_ai_position_sl_pct() -> float:
    """开仓止损百分比（百分点，如 3.0=3%）。读 system_settings.stop_loss_pct。"""
    from app.services.system_settings_loader import get_sl_tp_pct_points
    sl, _ = get_sl_tp_pct_points()
    return sl


def get_ai_position_tp_pct() -> float:
    """开仓止盈百分比（百分点）。读 system_settings.take_profit_pct。"""
    from app.services.system_settings_loader import get_sl_tp_pct_points
    _, tp = get_sl_tp_pct_points()
    return tp


def _format_pct_label(pct_points: float) -> str:
    if pct_points == int(pct_points):
        return f"{int(pct_points)}%"
    s = f"{pct_points:.1f}".rstrip('0').rstrip('.')
    return f"{s}%"


def _sl_tp_prompt_kwargs() -> Dict[str, str]:
    from app.services.system_settings_loader import get_sl_tp_pct_points
    sl, tp = get_sl_tp_pct_points()
    hold = get_ai_position_hold_hours()
    return {
        "sl_pct": _format_pct_label(sl),
        "tp_pct": _format_pct_label(tp),
        "hold_hours": str(hold),
    }
# 模拟仓持仓顾问: 满 15min 后每 15min 轮询 (见 gemini_position_advisor.HOLD_MIN_MINUTES)
AI_ADVISOR_MIN_HOLD_HOURS = 0.25  # 15min, 与 gemini_position_advisor.HOLD_MIN_MINUTES 一致
AI_ADVISOR_CHECK_INTERVAL_S = 900
# 主探索/预测开仓置信度 (与 prompt 校准表 0.60+ 对齐)
EXPLORE_CONFIDENCE_THRESHOLD = 0.60
PREDICT_CONFIDENCE_THRESHOLD = 0.60

KLINE_1H_READING_BLOCK = """
## 1h K 线读法 (6~8 小时持仓 — **以 1h 为主**)
- **持仓视角**: 判断未来 **6~8 小时**内方向是否可持有（本笔计划 {hold_hours}h）；**主证据是 1h**，15m/1d 仅作辅助。
- **整体趋势**: `kline_narrative.1h` 中「整体 N 根趋势 / 整体形态」≈ 近 **24 根 1h**。
- **近期结构**: 「最近 4~8 根明细 / 近期形态」描述回调、反弹、连阳连阴（对应 6~8h 窗口）。
- catalyst 须引用 **24 根整体 + 近 4~8 根 1h**，并配合 **RSI / 成交量 / 价格位置**；不得只写「最近 1 根 1h」或仅 24h 涨跌幅。
"""

_KLINE_MARKERS = (
    "1h", "15m", "1d", "连阳", "连阴", "形态", "放量", "缩量",
    "突破", "回踩", "震荡", "上攻", "下杀", "k线", "k 线",
)
_STRUCTURE_MARKERS = ("连阳", "连阴", "阳线", "阴线", "放量", "缩量", "突破", "回踩", "形态")
_WEAK_ONLY_PHRASES = (
    "已经涨", "已经跌", "涨幅过大", "跌幅过大", "涨多了", "跌多了",
    "24h涨", "24h跌", "24h 涨", "24h 跌",
)
_TF_RE = re.compile(r"\b(1h|15m|1d)\b", re.I)
_KBAR_COUNT_RE = re.compile(r"\d+\s*[阳阴]")
_PCT_RE = re.compile(r"[-+]?\d+(\.\d+)?%")


_VOLUME_MARKERS = ("放量", "缩量", "量缩", "量增", "量能", "成交量", "量平", "量萎缩", "量放大", "volume")


def _1h_kline_structure_ok(text: str) -> bool:
    """1h 为主：须出现 1h 且有具体 K 线结构词或阳阴根数."""
    low = text.lower()
    if "1h" not in low:
        return False
    return any(m in text for m in _STRUCTURE_MARKERS) or bool(_KBAR_COUNT_RE.search(text))


def _volume_mentioned_ok(text: str) -> bool:
    low = text.lower()
    return any(m in text for m in _VOLUME_MARKERS) or "volume" in low


def _multi_tf_kline_ok(text: str) -> bool:
    """兼容旧逻辑；探索/预测开仓以 _1h_kline_structure_ok 为准."""
    return _1h_kline_structure_ok(text)


def _quantified_technical_ok(text: str) -> bool:
    """RSI 或 EMA 数值、7d 距离、阳阴根数等可量化描述 (不要求字面 RSI)."""
    low = text.lower()
    if "rsi" in low:
        return True
    if "ema" in low and (_PCT_RE.search(text) or re.search(r"ema\s*[\d(]", low)):
        return True
    if ("7d" in low or "7 d" in low) and (
        "高点" in text or "低点" in text or "above_7d" in low or _PCT_RE.search(text)
    ):
        return True
    if _KBAR_COUNT_RE.search(text):
        return True
    return False


def _as_sym_dict(sym_data: Optional[Any]) -> dict:
    return sym_data if isinstance(sym_data, dict) else {}


def _flatten_verdict_items(raw: Any) -> List[dict]:
    """verdicts 可能是 list / dict / 嵌套 list，统一摊平为 dict 列表."""
    out: List[dict] = []
    if isinstance(raw, dict):
        if raw and all(isinstance(v, dict) for v in raw.values()):
            for sym, detail in raw.items():
                item = dict(detail)
                if not item.get("symbol"):
                    item["symbol"] = str(sym).upper()
                out.append(item)
            return out
        for key in ("entries", "items", "results", "signals", "verdicts"):
            nested = raw.get(key)
            if isinstance(nested, (list, dict)):
                return _flatten_verdict_items(nested)
        return out
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            nested = item.get("verdicts")
            if isinstance(nested, list):
                out.extend(_flatten_verdict_items(nested))
            elif "symbol" in item or "category" in item or "confidence" in item:
                out.append(item)
            else:
                out.extend(_flatten_verdict_items(item))
        elif isinstance(item, list):
            out.extend(_flatten_verdict_items(item))
    return out


def explore_llm_stub_with_trace(prompt: str, raw: str) -> dict:
    """JSON 解析失败时仍把 prompt/原始响应交给 runs 表落库."""
    return {
        "summary_zh": "",
        "verdicts": [],
        "_prompt": prompt,
        "_raw_response": raw or "",
    }


def _llm_meta_from_payload(parsed: Any) -> dict:
    """保留 call_llm 附带的 _prompt / _raw_response 等元字段（normalize 不得丢弃）."""
    if isinstance(parsed, dict):
        return {
            k: v
            for k, v in parsed.items()
            if isinstance(k, str) and k.startswith("_")
        }
    if isinstance(parsed, list) and len(parsed) == 1 and isinstance(parsed[0], dict):
        return _llm_meta_from_payload(parsed[0])
    return {}


def normalize_explore_llm_payload(parsed: Any) -> Optional[dict]:
    """LLM 偶发 list 顶层 / verdicts 为 dict / 单元素 wrapper → 标准 {summary_zh, verdicts}."""
    if parsed is None:
        return None
    meta = _llm_meta_from_payload(parsed)
    if isinstance(parsed, list):
        if len(parsed) == 1 and isinstance(parsed[0], dict):
            inner = parsed[0]
            if "verdicts" in inner or "summary_zh" in inner or "summary" in inner:
                parsed = inner
            else:
                parsed = {"summary_zh": "", "verdicts": _flatten_verdict_items(parsed)}
        else:
            parsed = {"summary_zh": "", "verdicts": _flatten_verdict_items(parsed)}
    if not isinstance(parsed, dict):
        return None
    for key in ("response", "data", "result", "output", "content"):
        inner = parsed.get(key)
        if isinstance(inner, dict) and (
            "verdicts" in inner or "summary_zh" in inner or "summary" in inner
        ):
            parsed = inner
            break
        if isinstance(inner, list):
            parsed = {
                "summary_zh": parsed.get("summary_zh") or parsed.get("summary") or "",
                "verdicts": _flatten_verdict_items(inner),
            }
            break
    summary = parsed.get("summary_zh") or parsed.get("summary") or ""
    if not isinstance(summary, str):
        summary = str(summary)
    verdicts = _flatten_verdict_items(parsed.get("verdicts"))
    out: dict = {"summary_zh": summary, "verdicts": verdicts}
    out.update(meta)
    return out


def explore_catalyst_technical_ok(
    catalyst: str,
    data_signal: str = "",
    sym_data: Optional[dict] = None,
) -> Tuple[bool, str]:
    """开仓前校验: 多周期 K 线 + 可量化技术位; 拦截仅涨跌幅/费率."""
    text = f"{catalyst or ''} {data_signal or ''}"
    low = text.lower()

    if not _1h_kline_structure_ok(text):
        return False, "catalyst 须写明 1h K 线结构（连阳/连阴/突破/回踩/放量缩量等）"

    if re.search(r"最近\s*1\s*根\s*1h", low) or re.search(r"单根\s*1h", low):
        return False, "须基于 1h 近 4~8 根结构 + 24 根整体趋势，禁止只看 1 根 1h"

    if not _volume_mentioned_ok(text):
        return False, "catalyst 须写明成交量变化（放量/缩量/量平等）"

    if not _quantified_technical_ok(text):
        sym = _as_sym_dict(sym_data)
        rsi = sym.get("tech") or {}
        rsi_v = rsi.get("rsi_14_1h")
        if rsi_v is None:
            rsi_v = sym.get("rsi_14_1h")
        if rsi_v is not None:
            return False, f"catalyst 须含 RSI 1h={rsi_v} 或 EMA/7d/阳阴根数等量化描述"
        return False, "catalyst 须含 EMA/7d 距离/阳阴根数或 RSI 等量化技术位"

    weak_only = any(p in text for p in _WEAK_ONLY_PHRASES) and not any(
        m in text for m in _STRUCTURE_MARKERS
    )
    has_kline_structure = any(
        m in text for m in ("背离", "拐点", "连阳", "连阴", "突破", "形态")
    ) or bool(_KBAR_COUNT_RE.search(text))
    funding_only = ("资金费" in text or "funding" in low) and not has_kline_structure
    if weak_only:
        return False, "主因仅为 24h 涨跌幅, 缺少 K 线结构"
    if funding_only:
        return False, "主因仅为资金费率, 缺少 K 线结构确认"
    return True, ""


def sym_data_for_catalyst_gate(item: Optional[dict]) -> dict:
    """explore universe / predict symbols_data → explore_catalyst_technical_ok 统一结构."""
    sym = _as_sym_dict(item)
    tech = dict(sym.get("tech") or {})
    if sym.get("rsi_14_1h") is not None and tech.get("rsi_14_1h") is None:
        tech["rsi_14_1h"] = sym.get("rsi_14_1h")
    if sym.get("above_7d_low_pct") is not None and tech.get("above_7d_low_pct") is None:
        tech["above_7d_low_pct"] = sym.get("above_7d_low_pct")
    if sym.get("below_7d_high_pct") is not None and tech.get("below_7d_high_pct") is None:
        tech["below_7d_high_pct"] = sym.get("below_7d_high_pct")
    return {"tech": tech, "kline_narrative": sym.get("kline_narrative") or {}}


def _explore_universe_score(item: dict) -> float:
    """选币: 中等波动 + 技术极端 + 流动性，避免纯 |24h| 极端池."""
    chg = abs(float(item.get("change_24h") or 0))
    if 3 <= chg <= 14:
        chg_score = chg
    elif chg < 3:
        chg_score = chg * 0.6
    else:
        chg_score = max(0.0, 14.0 - (chg - 14.0) * 0.85)
    tech = item.get("tech") or {}
    rsi_raw = tech.get("rsi_14_1h") or item.get("rsi_14_1h")
    try:
        rsi_f = float(rsi_raw) if rsi_raw is not None else 50.0
    except (TypeError, ValueError):
        rsi_f = 50.0
    rsi_score = min(abs(rsi_f - 50.0), 25.0) * 0.35
    vol = float(item.get("quote_volume_24h") or 0)
    vol_score = min(vol / 5_000_000.0, 4.0)
    b7h = tech.get("below_7d_high_pct") or item.get("below_7d_high_pct")
    ext_score = 0.0
    try:
        if b7h is not None and abs(float(b7h)) < 18:
            ext_score = 2.0
    except (TypeError, ValueError):
        pass
    return chg_score * 0.45 + rsi_score + vol_score * 0.25 + ext_score


def pool_rows_to_universe(rows: List[dict]) -> dict:
    """candidate_pool_snapshot 行 → 与探索同构的 universe (供技术面 TOP N 选币)."""
    from app.utils.futures_symbol import futures_symbol_clean

    universe: dict = {}
    for row in rows:
        sym = (row.get("symbol") or "").strip()
        if not sym:
            continue
        sym_data: dict = {
            "symbol": sym,
            "change_24h": float(row.get("change_24h") or 0),
            "quote_volume_24h": float(row.get("quote_volume_24h") or 0),
            "tech": {},
        }
        if row.get("rsi_14") is not None:
            sym_data["tech"]["rsi_14_1h"] = float(row["rsi_14"])
        if row.get("below_7d_high_pct") is not None:
            sym_data["tech"]["below_7d_high_pct"] = float(row["below_7d_high_pct"])
        if row.get("above_7d_low_pct") is not None:
            sym_data["tech"]["above_7d_low_pct"] = float(row["above_7d_low_pct"])
        universe[sym] = sym_data
    return universe


def select_llm_symbols_from_pool(
    rows: List[dict],
    *,
    banned: Optional[Set[str]] = None,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> List[str]:
    """预测/探索共用: 从 candidate_pool 按技术面评分取 TOP N symbol."""
    from app.utils.futures_symbol import futures_symbol_clean

    banned = banned or set()
    universe = pool_rows_to_universe(rows)
    filtered = {
        sym: data
        for sym, data in universe.items()
        if futures_symbol_clean(sym) not in banned
    }
    selected, meta = prepare_universe_for_llm(filtered, max_symbols=max_symbols)
    if meta.get("llm_symbols_truncated"):
        logger.info(
            f"[Predict] LLM 候选截断: 全池 {meta['universe_total']} → "
            f"送模 {meta['llm_symbol_count']} (技术面 TOP{max_symbols})"
        )
    return [item["symbol"] for item in selected if item.get("symbol")]


def select_all_symbols_from_pool(
    rows: List[dict],
    *,
    banned: Optional[Set[str]] = None,
    limit: int = 500,
) -> List[str]:
    """GPT 预测等: candidate_pool 全量送模 (排除 L3), 不做技术面 TOP N 截断."""
    from app.utils.futures_symbol import futures_symbol_clean

    banned = banned or set()
    symbols: List[str] = []
    seen: Set[str] = set()
    for row in rows[:limit]:
        sym = (row.get("symbol") or "").strip()
        if not sym:
            continue
        clean = futures_symbol_clean(sym)
        if clean in banned or clean in seen:
            continue
        seen.add(clean)
        symbols.append(sym)
    return symbols


def prepare_universe_for_llm(
    universe: dict,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[List[dict], Dict[str, Any]]:
    """按技术面相关性取 TOP N (非单纯 |24h| 极端波动)."""
    items = sorted(
        universe.values(),
        key=_explore_universe_score,
        reverse=True,
    )
    for item in items:
        item.pop("k_1d_ohlc", None)
        item.pop("k_1h_ohlc", None)
        item.pop("k_15m_ohlc", None)

    selected = items[:max_symbols]
    meta = {
        "universe_total": len(items),
        "llm_symbol_count": len(selected),
        "llm_symbols_truncated": max(0, len(items) - len(selected)),
    }
    if meta["llm_symbols_truncated"]:
        logger.info(
            f"[Explore] LLM 候选截断: 全池 {meta['universe_total']} → "
            f"送模 {meta['llm_symbol_count']} (技术面评分 TOP{max_symbols})"
        )
    return selected, meta


def build_explore_prompt_zh(
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
    *,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[str, Dict[str, Any]]:
    """Chinese main explore prompt (A/B benchmark only)."""
    universe_list, meta = prepare_universe_for_llm(universe, max_symbols=max_symbols)
    compact = {"ensure_ascii": False, "separators": (",", ":"), "default": str}
    prompt = EXPLORE_PROMPT_TEMPLATE.format(
        global_context_json=json.dumps(global_ctx, **compact),
        universe_json=json.dumps(universe_list, **compact),
        historical_stats_json=json.dumps(historical_stats, **compact),
        llm_universe_note=(
            f"本列表为全池 {meta['universe_total']} 个中按 **技术面评分** "
            f"(中等波动+RSI/7d+流动性，非单纯|24h|极端) 取 TOP {meta['llm_symbol_count']}，"
            f"仅对这些 symbol 输出 verdict (其余忽略)."
        ),
        **_sl_tp_prompt_kwargs(),
    )
    return prompt, meta


def build_explore_prompt(
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
    *,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[str, Dict[str, Any]]:
    """Production default: Chinese main explore prompt."""
    return build_explore_prompt_zh(
        universe, global_ctx, historical_stats, max_symbols=max_symbols,
    )


def _sanitize_json_string_literals(raw: str) -> str:
    """LLM 常在 catalyst 等字段里写真实换行/制表符，导致 json.loads Invalid control character."""
    out: List[str] = []
    in_string = False
    escape = False
    for ch in raw:
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\" and in_string:
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ord(ch) < 32:
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(f"\\u{ord(ch):04x}")
            continue
        out.append(ch)
    return "".join(out)


def _extract_llm_json_text(text: str) -> str:
    """从 LLM 原文取出 JSON 主体 ( fenced block / 首个 { 或 [ )."""
    raw = (text or "").strip()
    if not raw:
        return raw
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    for i, ch in enumerate(raw):
        if ch in "{[":
            return raw[i:].strip()
    return raw


def _try_parse_json(raw: str) -> Tuple[Optional[Any], Optional[str]]:
    last_err = "empty"
    for candidate in (raw, _sanitize_json_string_literals(raw)):
        stripped = candidate.lstrip()
        if not stripped:
            continue
        try:
            obj, end = json.JSONDecoder().raw_decode(stripped)
            trailing = stripped[end:].strip()
            if trailing:
                logger.warning(
                    f"JSON 尾部多余内容已忽略 ({len(trailing)} chars): {trailing[:120]!r}"
                )
            return obj, None
        except json.JSONDecodeError as e:
            last_err = str(e)
        try:
            return json.loads(candidate), None
        except json.JSONDecodeError as e:
            last_err = str(e)
    return None, last_err


def _repair_truncated_json(raw: str) -> str:
    """截断/未闭合字符串时补引号并平衡 [] {}."""
    s = raw.rstrip()
    if not s:
        return s
    in_string = False
    escape = False
    stack: List[str] = []
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == "{":
                stack.append("}")
            elif ch == "[":
                stack.append("]")
            elif ch in "}]" and stack and stack[-1] == ch:
                stack.pop()
    if in_string:
        s += '"'
    while stack:
        s += stack.pop()
    return s


def _salvage_loose_verdicts(text: str) -> Optional[dict]:
    """JSON 严重损坏时仍尝试提取 symbol/category/confidence（catalyst 可能缺失）."""
    summary_m = re.search(r'"summary_zh"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    summary_zh = summary_m.group(1) if summary_m else ""
    verdicts: List[dict] = []
    loose = re.compile(
        r'"symbol"\s*:\s*"([^"]+)"\s*,\s*"category"\s*:\s*"([^"]+)"\s*,\s*"confidence"\s*:\s*([\d.]+)',
        re.IGNORECASE,
    )
    seen: set = set()
    for m in loose.finditer(text):
        sym = m.group(1).upper().replace("/", "")
        if sym in seen:
            continue
        seen.add(sym)
        try:
            conf = float(m.group(3))
        except (TypeError, ValueError):
            conf = 0.0
        verdicts.append({
            "symbol": sym,
            "category": m.group(2),
            "confidence": conf,
            "catalyst": "",
            "data_signal": "",
            "risk_note": "",
        })
    if not verdicts:
        return None
    return {"summary_zh": summary_zh, "verdicts": verdicts}


def parse_explore_llm_json(text: str, tag: str = "Explore") -> Tuple[Optional[dict], Optional[str]]:
    """解析 LLM JSON; 截断时尝试抢救已完整的 verdict 对象."""
    raw = _extract_llm_json_text(text)

    err: Optional[str] = None
    parsed, parse_err = _try_parse_json(raw)
    if parsed is None:
        err = parse_err
        for candidate in (
            _repair_truncated_json(raw),
            _repair_truncated_json(_sanitize_json_string_literals(raw)),
        ):
            parsed, _ = _try_parse_json(candidate)
            if parsed is not None:
                break
    if parsed is None:
        err = parse_err
        for text_src in (raw, _sanitize_json_string_literals(raw)):
            parsed = _salvage_truncated_explore_json(text_src)
            if parsed is not None:
                break
    if parsed is None:
        parsed = _salvage_loose_verdicts(raw) or _salvage_loose_verdicts(
            _sanitize_json_string_literals(raw)
        )
        if parsed is not None:
            logger.warning(
                f"[{tag}] JSON 宽松抢救 {len(parsed.get('verdicts') or [])} 条 "
                f"(catalyst 可能缺失): {err}"
            )
    if parsed is None:
        logger.error(f"[{tag}] JSON 解析失败: {err}; raw[:500]={raw[:500]}")
        return None, err
    if err:
        logger.warning(
            f"[{tag}] JSON 需抢救/清洗, 已恢复 {len(parsed.get('verdicts') or [])} 条 verdict: {err}"
        )

    normalized = normalize_explore_llm_payload(parsed)
    if normalized is None:
        logger.error(f"[{tag}] JSON 结构无法规范化; raw[:500]={raw[:500]}")
        return None, err or "invalid JSON payload"
    if isinstance(parsed, list):
        logger.warning(f"[{tag}] JSON 顶层为 array, 已规范化 ({len(normalized.get('verdicts') or [])} 条 verdict)")
    parsed = normalized
    return parsed, err


def _salvage_truncated_explore_json(text: str) -> Optional[dict]:
    """从截断响应中提取完整的 verdict 对象."""
    summary_m = re.search(r'"summary_zh"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    summary_zh = summary_m.group(1) if summary_m else ""

    verdicts: List[dict] = []
    obj_re = re.compile(
        r'\{\s*"symbol"\s*:\s*"([^"]+)"\s*,\s*"category"\s*:\s*"([^"]+)"\s*,'
        r'\s*"confidence"\s*:\s*([\d.]+)\s*,\s*"catalyst"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,'
        r'\s*"data_signal"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"risk_note"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        re.DOTALL,
    )
    for m in obj_re.finditer(text):
        try:
            conf = float(m.group(3))
        except (TypeError, ValueError):
            conf = 0.0
        verdicts.append({
            "symbol": m.group(1),
            "category": m.group(2),
            "confidence": conf,
            "catalyst": m.group(4),
            "data_signal": m.group(5),
            "risk_note": m.group(6),
        })
    if not verdicts:
        return None
    return {"summary_zh": summary_zh, "verdicts": verdicts}


# catalyst 必须引用的字段类型 (写进 prompt 供 LLM 自检; 与 explore_catalyst_technical_ok 对齐)
CATALYST_EVIDENCE_BLOCK = """
""" + KLINE_1H_READING_BLOCK + """
# catalyst 写法 (强制 — 后端校验；不合格 → skipped_weak_catalyst，勿侥幸凑字)

## bullish/bearish 四条必填（全部写在 catalyst，缺一 → 必须 skip）

1. **1h 整体趋势** — 引用 `kline_narrative.1h`「整体/24根」偏多、偏空或震荡（禁「走势偏强」等空话）
2. **1h 近期结构** — 「近4~8根」连阳/连阴/回踩/突破等（禁只看最近 1 根 1h）
3. **RSI(1h) 数字** — 若 `tech.rsi_14_1h` 有值，**必须写出 RSI 数值**并说明是否支持方向
4. **成交量** — 必须写放量/缩量/量平/量萎缩等及与方向关系

可选: `below_7d_high_pct` / `above_7d_low_pct` 是否仍有 TP 空间。
15m/1d **最多一句**辅助，**不得**用「15m同向」代替 1h 四条。

## 禁止（→ skip, confidence<0.55）

- 主因仅为 `change_24h`、资金费率、Big4、triggers
- 无 1h 结构词（连阳/连阴/突破/回踩/放量/缩量等）的空洞 catalyst
- 套话：「技术面好」「有催化剂」「符合预期」「值得关注」
- 为凑开仓数强行给 conf=0.60~0.64

## data_signal（一行）

例: `1h:5连阳回踩缩量, RSI=58, 距7d高3%` — 禁止仅 `24h+12%`

## 自检

- 删掉 24h% 与 funding 后理由仍成立？
- catalyst 是否同时有 **1h结构** + **RSI数字(若有)** + **量能词**？
- 任一否 → skip（默认应对**多数**币）
"""

EXPLORE_PROMPT_TEMPLATE = """你是超级交易大师. **未来 6~8 小时持仓**（本笔计划 {hold_hours}h）, SL={sl_pct}, TP={tp_pct}, 杠杆 5x; 满 15min 后 Gemini 持仓顾问每 15min 可建议平仓.

你的任务是: 以 **1h K 线为主**，结合量价、RSI、价格位置，判断未来 6~8 小时内是否值得持有; 不是复述行情、不是宏观押注.

# 仓位设置 (供你理解容错空间)
- 杠杆 5x, 名义本金 ~2500U, SL={sl_pct} 价格跌幅, TP={tp_pct} 涨幅
- {hold_hours} 小时到期强制平仓 — 方向须能在 {hold_hours}h 内尽量走向 {tp_pct} TP 或至少不触发 {sl_pct} SL
- 不要选「只会小幅波动 1-2%」的标的

# 全局市场环境 (宏观仅背景, 见 big4_trading_hint)
{global_context_json}
""" + BIG4_PROMPT_BLOCK_EXPLORE + """
# 历史表现 (校准尺度, 勿用宏观理由开仓)
{historical_stats_json}

# 候选数据说明
{llm_universe_note}
每个 symbol 包含:
- triggers: 入选原因 (不能当作 catalyst)
- current_price / change_24h / quote_volume_24h: 涨跌幅**不能单独**作为多空依据
- current_rate: 资金费率 — 仅在与 K 线背离同时出现时作辅助
- kline_narrative: **1h 自然语言 K 线形态与成交量** (主证据); 15m/1d 辅助
- tech.rsi_14_1h, tech.above_7d_low_pct, tech.below_7d_high_pct

{universe_json}

""" + CATALYST_EVIDENCE_BLOCK + """
# 任务
为列表内**每个** symbol 各输出一条 verdict:
- category: 'bullish' / 'bearish' / 'skip'（**无把握必须 skip**）
- confidence: 0.0-1.0
- catalyst: **1h 四条必填**（见上）, 至少 2 句具体技术依据
- data_signal: 一行量化摘要
- risk_note: 反向风险一句 (Big4/BTC 仅写此处)

# 开仓纪律（质量优先 — 宁可漏单不可滥开）

- **默认 skip**：1h 震荡、量价矛盾、RSI 中性(45~55)且无结构突破 → skip
- 整轮 bullish+bearish **合计不超过 6 个**；预期 skip 占比 **≥70%**
- conf=0.60~0.64 **整轮最多 2 个**且须四维齐全；拿不准一律 skip
- 系统开仓门槛 conf≥0.60，但你应**极少**给到 0.60~0.64

# 置信度校准 (无 1h+量价+RSI 四维则不得 bullish/bearish)

| confidence | 条件 (全部满足才可给该档) |
|---|---|
| 0.75-1.00 | 1h 强趋势 + 量能确认 + RSI 顺向 + 距 7d 极值 ≥3% 空间 |
| 0.65-0.74 | 1h **24根整体** + **近4~8根** 同向 + RSI数字 + 量能词 + 7d位置合理 |
| 0.60-0.64 | 四维齐但边际 — **整轮≤2个**，否则应 skip |
| 0.00-0.59 | 震荡/矛盾/仅涨跌幅 — **必须 skip**（多数币应落此档） |

# 判定原则 — {hold_hours} 小时持仓

## ✅ 可给 bullish/bearish (须技术面)

**A. 趋势延续** — 1h 连续同向 K 线 + 放量 + RSI 支持 (写在 catalyst 里; 15m 仅辅助)
**B. 费率背离** — 费率极端 + RSI 反向 + **1h K 线已出现拐点形态** (三者缺一不可)
**C. 突破回踩** — 突破时放量、回踩缩量 (引用 narrative 原文)

## ❌ 必须 skip

**D. 死猫反弹** — 24h ±20%+ 且量异常, 无 1h 结构反转
**E. 震荡** — 1h/15m narrative 均为震荡、量萎缩
**F. 超跌抄底 / 超涨摸顶** — 无 1h 拐点 K 线, 只有涨跌幅
**G. 主观宏观** — catalyst 主因是「大盘」「Big4」「涨多了」而无 K 线/RSI
**H. Big4 单边偏见** — 不得整池只空或只多; 逐币独立技术面

# 反例 (禁止模仿)

BAD — 仅复述行情 (应 skip, confidence≤0.35):
{{
  "symbol": "ABC/USDT",
  "category": "bearish",
  "confidence": 0.70,
  "catalyst": "24h 已涨 18%, 资金费率 +0.06% 多头拥挤, 涨幅过大应回调",
  "data_signal": "24h+18%",
  "risk_note": ""
}}

GOOD — 技术面 (可 bullish 0.68):
{{
  "symbol": "NEAR/USDT",
  "category": "bullish",
  "confidence": 0.68,
  "catalyst": "1h整体24根偏多; 近5根连阳后缩量回踩; RSI 1h=58未超买; 突破段放量回踩缩量",
  "data_signal": "1h:5连阳回踩缩量, RSI=58, 距7d高4%",
  "risk_note": "BTC 若急跌可能拖累"
}}

GOOD — skip:
{{
  "symbol": "XRP/USDT",
  "category": "skip",
  "confidence": 0.30,
  "catalyst": "1h/15m narrative 均为震荡, 量持平; RSI=52 中性. 虽 24h+3% 但无 2h 可交易结构",
  "data_signal": "双周期震荡, RSI=52",
  "risk_note": ""
}}

# 输出要求
**仅** 一个合法 JSON, 不要 markdown 围栏.
**质量优先**: 无 1h 四维证据一律 skip; 整轮开仓≤6; 不要为凑数降低标准.

{{
  "summary_zh": "整体技术面氛围 1-2 句 (勿写只做多/只做空)",
  "verdicts": [
    {{
      "symbol": "FOO/USDT",
      "category": "bullish",
      "confidence": 0.72,
      "catalyst": "必须含 kline 周期+RSI 数字...",
      "data_signal": "一行量化",
      "risk_note": "..."
    }}
  ]
}}
"""

KLINE_1H_READING_BLOCK_EN = KLINE_1H_READING_BLOCK
CATALYST_EVIDENCE_BLOCK_EN = CATALYST_EVIDENCE_BLOCK
EXPLORE_PROMPT_TEMPLATE_EN = EXPLORE_PROMPT_TEMPLATE


def build_explore_prompt_en(
    universe: dict,
    global_ctx: dict,
    historical_stats: dict,
    *,
    max_symbols: int = EXPLORE_LLM_MAX_SYMBOLS,
) -> Tuple[str, Dict[str, Any]]:
    """兼容旧调用名：现在一律返回中文主探索 prompt。"""
    return build_explore_prompt_zh(
        universe, global_ctx, historical_stats, max_symbols=max_symbols,
    )
