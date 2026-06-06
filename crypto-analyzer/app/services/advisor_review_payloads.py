"""Helpers for persisting replayable advisor review payloads."""
from __future__ import annotations

import json
from typing import Any, Optional


def dumps_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"_serialization_error": repr(value)}, ensure_ascii=False)


def table_columns(cur, table: str) -> set[str]:
    cur.execute(f"SHOW COLUMNS FROM {table}")
    return {str(row.get("Field") or row.get("field") or "") for row in cur.fetchall() or []}
