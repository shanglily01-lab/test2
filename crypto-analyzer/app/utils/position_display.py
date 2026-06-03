"""持仓展示辅助 — 平仓原因中文等."""
from __future__ import annotations

from app.utils.futures_symbol import futures_symbol_rating_canonical


def canonicalize_symbol_fields(rows: list, *fields: str) -> list:
    """API 返回前统一 symbol 为 BASE/USDT（兼容历史 XXXUSDT 行）."""
    keys = fields or ("symbol",)
    for r in rows:
        for f in keys:
            if r.get(f):
                r[f] = futures_symbol_rating_canonical(str(r[f]))
    return rows


def enrich_closed_position_rows(rows: list) -> list:
    """为 CLOSED 仓位补充 close_reason_cn（futures_positions.notes 即平仓原因）."""
    from app.api.futures_review_api import parse_close_reason

    for r in rows:
        notes = r.get('notes') or ''
        _, cn = parse_close_reason(notes)
        r['close_reason'] = notes
        r['close_reason_cn'] = cn
    return rows
