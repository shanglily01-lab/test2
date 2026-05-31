"""持仓展示辅助 — 平仓原因中文等."""
from __future__ import annotations


def enrich_closed_position_rows(rows: list) -> list:
    """为 CLOSED 仓位补充 close_reason_cn（futures_positions.notes 即平仓原因）."""
    from app.api.futures_review_api import parse_close_reason

    for r in rows:
        notes = r.get('notes') or ''
        _, cn = parse_close_reason(notes)
        r['close_reason'] = notes
        r['close_reason_cn'] = cn
    return rows
