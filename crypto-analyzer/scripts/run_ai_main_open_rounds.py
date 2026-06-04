#!/usr/bin/env python3
"""服务器手动跑一轮 AI 主探索/预测，校验 run 完成且 trades_opened/orders_opened >= 1。

需要 .env 中 DB + LLM API，会消耗 token 并在 account_id=2 写模拟仓。

用法:
  python scripts/run_ai_main_open_rounds.py --quick
  python scripts/run_ai_main_open_rounds.py --only gpt_explore,gemini_predict
  python scripts/run_ai_main_open_rounds.py --main --require-open
  python scripts/run_ai_main_open_rounds.py --full

先启用 kill switch:
  python scripts/enable_ai_main_strategies.py

退出码: 0 = 全部通过；1 = 有失败或未满足 --require-open
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql
from dotenv import dotenv_values
from loguru import logger

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None and k not in os.environ:
        os.environ[k] = v


@dataclass
class RoundSpec:
    key: str
    label: str
    source: str
    runs_table: str
    open_column: str  # trades_opened | orders_opened
    enabled_key: Optional[str]
    verdicts_table: Optional[str]
    runner: Callable[[str], Optional[int]]


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


def _connect():
    return pymysql.connect(
        **_db_cfg(),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _read_enabled(cur, key: Optional[str]) -> bool:
    if not key:
        return True
    cur.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
        (key,),
    )
    row = cur.fetchone() or {}
    return str(row.get("setting_value", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _fetch_run(conn, table: str, run_id: int) -> Optional[dict]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM `{table}` WHERE id=%s LIMIT 1", (run_id,))
        return cur.fetchone()


def _count_opened_verdicts(conn, verdicts_table: Optional[str], run_id: int) -> Tuple[int, List[dict]]:
    if not verdicts_table:
        return 0, []
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT action_taken, skip_reason, symbol, position_id
            FROM `{verdicts_table}`
            WHERE run_id=%s
            ORDER BY id
            """,
            (run_id,),
        )
        rows = cur.fetchall() or []
    opened = [r for r in rows if r.get("action_taken") == "opened"]
    return len(opened), rows


def _verify_positions(conn, position_ids: List[int]) -> List[dict]:
    if not position_ids:
        return []
    placeholders = ",".join(["%s"] * len(position_ids))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, symbol, position_side, source, status, account_id, entry_price
            FROM futures_positions
            WHERE id IN ({placeholders})
            """,
            position_ids,
        )
        return cur.fetchall() or []


def _build_specs() -> List[RoundSpec]:
    from app.services.gemini_explore_worker import run_explore_round as gemini_explore
    from app.services.deepseek_explore_worker import run_explore_round as deepseek_explore
    from app.services.gpt_explore_worker import run_explore_round as gpt_explore
    from app.services.gemini_predictor import run_predict_round as gemini_predict
    from app.services.deepseek_predictor import run_predict_round as deepseek_predict
    from app.services.gpt_predictor import run_predict_round as gpt_predict
    from app.services.gemini_reversal_explore_worker import (
        run_gemini_reversal_explore_round,
    )
    from app.services.deepseek_reversal_explore_worker import (
        run_deepseek_reversal_explore_round,
    )
    from app.services.gpt_reversal_explore_worker import run_gpt_reversal_explore_round
    import app.services.tactical_explore_workers as tw

    specs: List[RoundSpec] = [
        RoundSpec(
            "gemini_explore", "Gemini探索", "gemini_explore",
            "gemini_explore_runs", "trades_opened", "gemini_explore_enabled",
            "gemini_explore_verdicts", gemini_explore,
        ),
        RoundSpec(
            "deepseek_explore", "DeepSeek探索", "deepseek_explore",
            "deepseek_explore_runs", "trades_opened", "deepseek_explore_enabled",
            "deepseek_explore_verdicts", deepseek_explore,
        ),
        RoundSpec(
            "gpt_explore", "GPT探索", "gpt_explore",
            "gpt_explore_runs", "trades_opened", "gpt_explore_enabled",
            "gpt_explore_verdicts", gpt_explore,
        ),
        RoundSpec(
            "gemini_predict", "Gemini预测", "gemini_predict",
            "gemini_predict_runs", "orders_opened", "gemini_predict_enabled",
            "gemini_predict_verdicts", gemini_predict,
        ),
        RoundSpec(
            "deepseek_predict", "DeepSeek预测", "deepseek_predict",
            "deepseek_predict_runs", "orders_opened", "deepseek_predict_enabled",
            "deepseek_predict_verdicts", deepseek_predict,
        ),
        RoundSpec(
            "gpt_predict", "GPT预测", "gpt_predict",
            "gpt_predict_runs", "orders_opened", "gpt_predict_enabled",
            "gpt_predict_verdicts", gpt_predict,
        ),
        RoundSpec(
            "gemini_reversal", "Gemini顶空底多", "gemini_reversal",
            "gemini_reversal_explore_runs", "trades_opened", None,
            "gemini_reversal_explore_verdicts", run_gemini_reversal_explore_round,
        ),
        RoundSpec(
            "deepseek_reversal", "DeepSeek顶空底多", "deepseek_reversal",
            "deepseek_reversal_explore_runs", "trades_opened", None,
            "deepseek_reversal_explore_verdicts", run_deepseek_reversal_explore_round,
        ),
        RoundSpec(
            "gpt_reversal", "GPT顶空底多", "gpt_reversal",
            "gpt_reversal_explore_runs", "trades_opened", None,
            "gpt_reversal_explore_verdicts", run_gpt_reversal_explore_round,
        ),
    ]

    tactical_runners = [
        (tw.run_gemini_pullback_explore_round, "gemini", "pullback", "回调做多"),
        (tw.run_gemini_rebound_explore_round, "gemini", "rebound", "反弹做空"),
        (tw.run_gemini_chase_explore_round, "gemini", "chase", "追涨做多"),
        (tw.run_gemini_dump_explore_round, "gemini", "dump", "杀跌做空"),
        (tw.run_deepseek_pullback_explore_round, "deepseek", "pullback", "回调做多"),
        (tw.run_deepseek_rebound_explore_round, "deepseek", "rebound", "反弹做空"),
        (tw.run_deepseek_chase_explore_round, "deepseek", "chase", "追涨做多"),
        (tw.run_deepseek_dump_explore_round, "deepseek", "dump", "杀跌做空"),
        (tw.run_gpt_pullback_explore_round, "gpt", "pullback", "回调做多"),
        (tw.run_gpt_rebound_explore_round, "gpt", "rebound", "反弹做空"),
        (tw.run_gpt_chase_explore_round, "gpt", "chase", "追涨做多"),
        (tw.run_gpt_dump_explore_round, "gpt", "dump", "杀跌做空"),
    ]
    for runner, teacher, sk, title in tactical_runners:
        key = f"{teacher}_{sk}"
        specs.append(
            RoundSpec(
                key,
                f"{teacher.upper()}战术-{title}",
                key,
                f"{teacher}_{sk}_explore_runs",
                "trades_opened",
                None,
                f"{teacher}_{sk}_explore_verdicts",
                runner,
            )
        )
    return specs


QUICK_KEYS = {"gemini_explore", "deepseek_explore", "gpt_explore"}
MAIN_KEYS = QUICK_KEYS | {
    "gemini_predict", "deepseek_predict", "gpt_predict",
}


@dataclass
class RoundResult:
    key: str
    label: str
    state: str  # pass | fail | skip | no_open
    run_id: Optional[int]
    opens: int
    status: Optional[str]
    error_msg: str
    skip_summary: str
    position_ids: List[int]


def _summarize_skips(rows: List[dict]) -> str:
    from collections import Counter

    c = Counter()
    for r in rows:
        if r.get("action_taken") != "opened":
            c[r.get("action_taken") or "unknown"] += 1
    if not c:
        return ""
    return ", ".join(f"{k}={v}" for k, v in c.most_common(6))


def run_one(conn, spec: RoundSpec, require_open: bool) -> RoundResult:
    with conn.cursor() as cur:
        if spec.enabled_key and not _read_enabled(cur, spec.enabled_key):
            return RoundResult(
                spec.key, spec.label, "skip", None, 0, None,
                f"kill switch {spec.enabled_key}=0", "", [],
            )

    logger.info(f"========== 开始 {spec.label} ({spec.key}) ==========")
    t0 = time.time()
    try:
        run_id = spec.runner("manual")
    except Exception as e:
        logger.exception(f"[{spec.label}] 异常")
        return RoundResult(
            spec.key, spec.label, "fail", None, 0, "exception",
            str(e)[:300], "", [],
        )

    elapsed = time.time() - t0
    if run_id is None:
        return RoundResult(
            spec.key, spec.label, "skip", None, 0, None,
            "runner 返回 None（锁占用/未到点/kill/API 缺失/候选池空）", "", [],
        )

    run = _fetch_run(conn, spec.runs_table, run_id)
    if not run:
        return RoundResult(
            spec.key, spec.label, "fail", run_id, 0, None,
            f"run_id={run_id} 在 {spec.runs_table} 不存在", "", [],
        )

    status = run.get("status")
    opens = int(run.get(spec.open_column) or 0)
    err = (run.get("error_msg") or "")[:200]
    verdict_opens, verdict_rows = _count_opened_verdicts(
        conn, spec.verdicts_table, run_id,
    )
    position_ids = [
        int(r["position_id"])
        for r in verdict_rows
        if r.get("action_taken") == "opened" and r.get("position_id")
    ]
    positions = _verify_positions(conn, position_ids)
    skip_summary = _summarize_skips(verdict_rows)

    logger.info(
        f"[{spec.label}] run_id={run_id} status={status} "
        f"{spec.open_column}={opens} verdict_opened={verdict_opens} "
        f"positions_ok={len(positions)}/{len(position_ids)} "
        f"耗时={elapsed:.1f}s"
    )

    if status == "error":
        return RoundResult(
            spec.key, spec.label, "fail", run_id, opens, status, err,
            skip_summary, position_ids,
        )

    effective_opens = max(opens, verdict_opens)
    if effective_opens < 1:
        state = "no_open" if require_open else "pass"
        return RoundResult(
            spec.key, spec.label, state, run_id, opens, status,
            err or "本轮无开仓", skip_summary, position_ids,
        )
    if opens >= 1 and verdict_opens < 1:
        skip_summary = (skip_summary + "; verdict未写入但run已记开仓").strip("; ")

    if position_ids and len(position_ids) != len(positions):
        return RoundResult(
            spec.key, spec.label, "fail", run_id, opens, status,
            "verdict 有 opened 但 futures_positions 缺失", skip_summary,
            position_ids,
        )

    for p in positions:
        if p.get("status") != "open":
            return RoundResult(
                spec.key, spec.label, "fail", run_id, opens, status,
                f"position {p.get('id')} status={p.get('status')}", skip_summary,
                position_ids,
            )

    return RoundResult(
        spec.key, spec.label, "pass", run_id, effective_opens, status, "", skip_summary,
        position_ids,
    )


def _print_report(results: List[RoundResult], require_open: bool) -> int:
    print("\n" + "=" * 72)
    print("AI 主策略开单轮次报告", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    print("=" * 72)
    fails = 0
    opens_total = 0
    for r in results:
        icon = {
            "pass": "OK",
            "fail": "FAIL",
            "skip": "SKIP",
            "no_open": "NO_OPEN",
        }.get(r.state, r.state)
        line = f"  [{icon:7}] {r.label:20} key={r.key}"
        if r.run_id:
            line += f" run_id={r.run_id} opens={r.opens} status={r.status}"
        if r.error_msg and r.state != "pass":
            line += f" | {r.error_msg}"
        if r.skip_summary and r.state in ("no_open", "fail"):
            line += f" | skips: {r.skip_summary}"
        if r.position_ids:
            line += f" | pos={r.position_ids}"
        print(line)
        if r.state == "fail":
            fails += 1
        if r.state == "no_open" and require_open:
            fails += 1
        if r.state == "pass":
            opens_total += r.opens

    print("-" * 72)
    passed = sum(1 for r in results if r.state == "pass")
    skipped = sum(1 for r in results if r.state == "skip")
    no_open = sum(1 for r in results if r.state == "no_open")
    print(
        f"  通过(有开仓): {passed}  无开仓: {no_open}  跳过: {skipped}  "
        f"失败: {sum(1 for r in results if r.state == 'fail')}  总开仓数: {opens_total}"
    )
    if require_open and no_open > 0:
        print("  --require-open: 无开仓的已启用策略视为未通过")
    print("=" * 72)
    return 1 if fails > 0 or (require_open and no_open > 0) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 主探索/预测手动开单轮次")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--quick", action="store_true", help="仅三教师主探索")
    g.add_argument("--main", action="store_true", help="主探索+主预测 (6)")
    g.add_argument("--full", action="store_true", help="全部 21 策略")
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="逗号分隔 key，如 gpt_explore,gemini_predict",
    )
    parser.add_argument(
        "--require-open",
        action="store_true",
        default=True,
        help="要求每轮 trades_opened/orders_opened >= 1（默认开启）",
    )
    parser.add_argument(
        "--allow-no-open",
        action="store_true",
        help="仅校验 pipeline 完成，不要求实际开仓",
    )
    args = parser.parse_args()
    require_open = not args.allow_no_open

    all_specs = {s.key: s for s in _build_specs()}
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
    elif args.full:
        keys = list(all_specs.keys())
    elif args.main:
        keys = sorted(MAIN_KEYS)
    else:
        keys = sorted(QUICK_KEYS)

    unknown = [k for k in keys if k not in all_specs]
    if unknown:
        print(f"未知 key: {unknown}", file=sys.stderr)
        print(f"可用: {', '.join(sorted(all_specs))}", file=sys.stderr)
        return 1

    specs = [all_specs[k] for k in keys]
    print(f"将测试 {len(specs)} 个策略: {', '.join(keys)}")
    print(f"require_open={require_open}")

    conn = _connect()
    results: List[RoundResult] = []
    for spec in specs:
        results.append(run_one(conn, spec, require_open))
    conn.close()
    return _print_report(results, require_open)


if __name__ == "__main__":
    sys.exit(main())
