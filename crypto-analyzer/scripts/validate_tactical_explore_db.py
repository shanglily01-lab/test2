#!/usr/bin/env python3
"""
校验战术探索相关表结构及关键词长度 (source / category / action_taken).

用法:
  python scripts/validate_tactical_explore_db.py
  python scripts/validate_tactical_explore_db.py --apply-migrations  # 仅提示，不自动执行 SQL
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
env = dict(dotenv_values(ROOT / ".env"))

DB_CFG = {
    "host": env.get("DB_HOST", "localhost"),
    "port": int(env.get("DB_PORT", 3306)),
    "user": env.get("DB_USER", "root"),
    "password": env.get("DB_PASSWORD", ""),
    "database": env.get("DB_NAME", "binance-data"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}

TACTICAL_SOURCES = [
    "gemini_reversal",
    "gemini_pullback",
    "gemini_rebound",
    "gemini_chase",
    "gemini_dump",
    "deepseek_reversal",
    "deepseek_pullback",
    "deepseek_rebound",
    "deepseek_chase",
    "deepseek_dump",
]

RUNS_TABLES = [
    "gemini_reversal_explore_runs",
    "gemini_pullback_explore_runs",
    "gemini_rebound_explore_runs",
    "gemini_chase_explore_runs",
    "gemini_dump_explore_runs",
    "deepseek_reversal_explore_runs",
    "deepseek_pullback_explore_runs",
    "deepseek_rebound_explore_runs",
    "deepseek_chase_explore_runs",
    "deepseek_dump_explore_runs",
]

VERDICTS_TABLES = [t.replace("_runs", "_verdicts") for t in RUNS_TABLES]

REVERSAL_CATEGORIES = ("top_reversal", "bottom_reversal", "skip")
TACTICAL_CATEGORIES = ("entry", "skip")
ACTION_TAKEN_SAMPLES = (
    "opened",
    "skipped_confidence",
    "skipped_weak_catalyst",
    "skipped_direction_lock",
    "skipped_dedup",
    "skipped_blacklist",
    "skipped_other",
)


def _col_max_len(cur, table: str, column: str) -> int | None:
    cur.execute(
        """
        SELECT CHARACTER_MAXIMUM_LENGTH AS ml, DATA_TYPE AS dt, COLUMN_TYPE AS ct
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (DB_CFG["database"], table, column),
    )
    row = cur.fetchone()
    if not row:
        return None
    if row["dt"] in ("varchar", "char") and row["ml"]:
        return int(row["ml"])
    if row["dt"] == "enum":
        return None  # enum handled separately
    return 9999


def _is_enum(cur, table: str, column: str) -> list[str] | None:
    cur.execute(
        """
        SELECT COLUMN_TYPE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
        """,
        (DB_CFG["database"], table, column),
    )
    row = cur.fetchone()
    if not row or "enum" not in (row["COLUMN_TYPE"] or "").lower():
        return None
    ct = row["COLUMN_TYPE"]
    inner = ct[ct.index("(") + 1 : ct.rindex(")")]
    return [v.strip().strip("'") for v in inner.split(",")]


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        conn = pymysql.connect(**DB_CFG)
    except Exception as e:
        print(f"FAIL: 无法连接数据库: {e}")
        return 1

    try:
        with conn.cursor() as cur:
            # 1) 表存在
            for t in RUNS_TABLES + VERDICTS_TABLES:
                cur.execute(
                    "SELECT 1 FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (DB_CFG["database"], t),
                )
                if not cur.fetchone():
                    errors.append(f"缺少表: {t} (请执行 migrations/008 与 009)")

            # 2) futures_positions.source
            src_len = _col_max_len(cur, "futures_positions", "source")
            if src_len is None:
                enum_vals = _is_enum(cur, "futures_positions", "source")
                if enum_vals:
                    for s in TACTICAL_SOURCES:
                        if s not in enum_vals:
                            errors.append(
                                f"futures_positions.source ENUM 不含 '{s}' "
                                f"(需 ALTER 为 varchar(50) 或扩展 ENUM)"
                            )
            else:
                for s in TACTICAL_SOURCES:
                    if len(s) > src_len:
                        errors.append(f"source '{s}' 长度 {len(s)} > varchar({src_len})")

            missing = {e.split(": ")[-1] for e in errors if e.startswith("缺少表:")}
            sample_v = "gemini_pullback_explore_verdicts"
            sample_r = "gemini_pullback_explore_runs"
            if sample_v not in missing and sample_r not in missing:
                cat_len = _col_max_len(cur, sample_v, "category")
                act_enum = _is_enum(cur, sample_v, "action_taken")
                act_len = _col_max_len(cur, sample_v, "action_taken")

                for c in REVERSAL_CATEGORIES + TACTICAL_CATEGORIES:
                    if cat_len and len(c) > cat_len:
                        errors.append(f"{sample_v}.category 无法容纳 '{c}' (max {cat_len})")

                for a in ACTION_TAKEN_SAMPLES:
                    if act_enum and a not in act_enum:
                        errors.append(
                            f"{sample_v}.action_taken ENUM 不含 '{a}' "
                            "(参考 migration 007 改为 varchar(50))"
                        )
                    elif act_len and len(a) > act_len:
                        errors.append(f"action_taken '{a}' 过长 (max {act_len})")

            # 4) runs.status 写入 partial/ok/skipped/error
            status_enum = _is_enum(cur, sample_r, "status")
            for st in ("partial", "ok", "skipped", "error"):
                if status_enum and st not in status_enum:
                    errors.append(f"{sample_r}.status ENUM 不含 '{st}'")

            # 5) explore_prepared_snapshot 表 (data_cache 库)
            cur.execute(
                "SELECT 1 FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA='data_cache' AND TABLE_NAME='explore_prepared_snapshot'",
            )
            if not cur.fetchone():
                errors.append(
                    "缺少表: data_cache.explore_prepared_snapshot (请执行 migration 010)"
                )

            # 6) 试插入 + 回滚 (验证 FK 与列类型)
            if not errors:
                cur.execute("START TRANSACTION")
                try:
                    cur.execute(
                        f"""
                        INSERT INTO {sample_r}
                          (asof_utc, model, universe_size, status, triggered_by)
                        VALUES (UTC_TIMESTAMP(), 'schema_test', 0, 'partial', 'schema_test')
                        """
                    )
                    rid = cur.lastrowid
                    cur.execute(
                        f"""
                        INSERT INTO {sample_v}
                          (run_id, symbol, category, confidence, action_taken)
                        VALUES (%s, 'BTCUSDT', 'entry', 0.5, 'skipped_weak_catalyst')
                        """,
                        (rid,),
                    )
                    cur.execute(
                        """
                        SELECT 1 FROM futures_positions
                        WHERE source=%s AND account_id=2 LIMIT 1
                        """,
                        ("gemini_pullback",),
                    )
                    conn.rollback()
                    print("OK: 试插入 verdict (entry / skipped_weak_catalyst) 已回滚")
                except Exception as e:
                    conn.rollback()
                    errors.append(f"试插入失败: {e}")

    finally:
        conn.close()

    print(f"\n数据库: {DB_CFG['database']} @ {DB_CFG['host']}")
    print(f"战术 source 关键词 ({len(TACTICAL_SOURCES)}): {', '.join(TACTICAL_SOURCES)}")

    if warnings:
        for w in warnings:
            print(f"WARN: {w}")
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        return 1

    print("PASS: 战术探索表结构与关键词校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
