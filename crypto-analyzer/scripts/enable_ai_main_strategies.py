#!/usr/bin/env python3
"""启用三教师主探索+主预测（6 策略）kill switch，便于服务器手动开单验证。

用法:
  python scripts/enable_ai_main_strategies.py
  python scripts/enable_ai_main_strategies.py --live   # 同时 live_trading_enabled=1（仅 4 source 可同步 Binance）
  python scripts/enable_ai_main_strategies.py --dry-run

跑一轮并校验开仓:
  python scripts/run_ai_main_open_rounds.py --main --require-open
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql
from dotenv import dotenv_values

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None and k not in os.environ:
        os.environ[k] = v

MAIN_ENABLED_KEYS = (
    ("gemini_explore_enabled", "Gemini 探索"),
    ("deepseek_explore_enabled", "DeepSeek 探索"),
    ("gpt_explore_enabled", "GPT 探索"),
    ("gemini_predict_enabled", "Gemini 预测"),
    ("deepseek_predict_enabled", "DeepSeek 预测"),
    ("gpt_predict_enabled", "GPT 预测"),
)

LIVE_SYNC_SOURCES = (
    "gemini_explore",
    "deepseek_explore",
    "gemini_predict",
    "deepseek_predict",
)

READ_KEYS = [k for k, _ in MAIN_ENABLED_KEYS] + [
    "live_trading_enabled",
    "live_close_enabled",
    "live_top50_required",
    "live_whitelist_enabled",
]


def _db_cfg() -> dict:
    env = dotenv_values(ROOT / ".env")
    return {
        "host": env["DB_HOST"],
        "port": int(env["DB_PORT"]),
        "user": env["DB_USER"],
        "password": env["DB_PASSWORD"],
        "database": env["DB_NAME"],
        "charset": "utf8mb4",
    }


def _upsert_bool(cur, key: str, value: str, desc: str) -> None:
    cur.execute(
        """
        INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
        VALUES (%s, %s, %s, 'enable_ai_main', NOW())
        ON DUPLICATE KEY UPDATE
            setting_value = VALUES(setting_value),
            description = VALUES(description),
            updated_by = VALUES(updated_by),
            updated_at = NOW()
        """,
        (key, value, desc),
    )


def _read_all(cur) -> dict:
    out = {}
    for key in READ_KEYS:
        cur.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
            (key,),
        )
        row = cur.fetchone() or {}
        out[key] = row.get("setting_value")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="启用 6 个主 AI 探索/预测策略")
    parser.add_argument(
        "--live",
        action="store_true",
        help="同时设置 live_trading_enabled=1",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    conn = pymysql.connect(**_db_cfg(), cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    try:
        with conn.cursor() as cur:
            before = _read_all(cur)
            print("=== 当前开关 ===")
            for key in READ_KEYS:
                print(f"  {key} = {before.get(key, '(missing)')}")

            if args.dry_run:
                print("\n(dry-run) 将设置 6 个 *_enabled = 1")
                return 0

            for key, label in MAIN_ENABLED_KEYS:
                _upsert_bool(cur, key, "1", f"{label} (enable_ai_main)")

            if args.live:
                _upsert_bool(
                    cur,
                    "live_trading_enabled",
                    "1",
                    "实盘开仓总开关 (enable_ai_main)",
                )

            conn.commit()
            after = _read_all(cur)

        print("\n=== 已更新 ===")
        for key, _ in MAIN_ENABLED_KEYS:
            print(f"  {key} = {after.get(key)}")
        if args.live:
            print(f"  live_trading_enabled = {after.get('live_trading_enabled')}")

        print("\n实盘白名单 source:")
        for s in LIVE_SYNC_SOURCES:
            print(f"  - {s}")

        print("\n下一步:")
        print("  python scripts/run_ai_main_open_rounds.py --main --require-open")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"错误: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
