#!/usr/bin/env python3
"""服务器手动跑战术四策略 + 顶空底多（反转），校验 run 完成且 trades_opened >= 1。

需要 .env 中 DB + LLM API；读 explore_prepared_snapshot，会消耗 token，写模拟仓 account_id=2。

用法:
  python scripts/run_ai_tactical_open_rounds.py --tactical
  python scripts/run_ai_tactical_open_rounds.py --reversal
  python scripts/run_ai_tactical_open_rounds.py --all
  python scripts/run_ai_tactical_open_rounds.py --quick
  python scripts/run_ai_tactical_open_rounds.py --teacher gemini
  python scripts/run_ai_tactical_open_rounds.py --strategy pullback
  python scripts/run_ai_tactical_open_rounds.py --only gemini_pullback,deepseek_dump
  python scripts/run_ai_tactical_open_rounds.py --tactical --allow-no-open

key 列表（15）:
  战术: gemini_pullback, gemini_rebound, gemini_chase, gemini_dump,
        deepseek_pullback, deepseek_rebound, deepseek_chase, deepseek_dump,
        gpt_pullback, gpt_rebound, gpt_chase, gpt_dump
  反转: gemini_reversal, deepseek_reversal, gpt_reversal

退出码: 0 = 全部通过；1 = 有失败或未满足 --require-open
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from dotenv import dotenv_values

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None and k not in os.environ:
        os.environ[k] = v

from run_ai_main_open_rounds import (  # noqa: E402
    RoundResult,
    RoundSpec,
    _build_specs,
    _connect,
    run_one,
)

TEACHERS = ("gemini", "deepseek", "gpt")
TACTICAL_SK = ("pullback", "rebound", "chase", "dump")

TACTICAL_KEYS: Set[str] = {f"{t}_{s}" for t in TEACHERS for s in TACTICAL_SK}
REVERSAL_KEYS: Set[str] = {"gemini_reversal", "deepseek_reversal", "gpt_reversal"}
ALL_KEYS: Set[str] = TACTICAL_KEYS | REVERSAL_KEYS

# 每教师各抽 1 个战术，冒烟用
QUICK_KEYS = ("gemini_pullback", "deepseek_chase", "gpt_dump")


def _print_report(results: List[RoundResult], require_open: bool) -> int:
    print("\n" + "=" * 72)
    print(
        "AI 战术/反转开单轮次报告",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )
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
        line = f"  [{icon:7}] {r.label:22} key={r.key}"
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
        print("  --require-open: 无开仓的策略视为未通过（战术常 0 开仓，可用 --allow-no-open）")
    print("=" * 72)
    return 1 if fails > 0 or (require_open and no_open > 0) else 0


def _resolve_keys(args: argparse.Namespace) -> List[str]:
    if args.only:
        return [k.strip() for k in args.only.split(",") if k.strip()]

    keys: Set[str] = set()
    if args.quick:
        keys.update(QUICK_KEYS)
    if args.tactical or args.all:
        keys.update(TACTICAL_KEYS)
    if args.reversal or args.all:
        keys.update(REVERSAL_KEYS)

    if not keys:
        keys.update(TACTICAL_KEYS)

    if args.teacher:
        t = args.teacher.strip().lower()
        if t not in TEACHERS:
            raise ValueError(f"未知 teacher: {t}，可选 {TEACHERS}")
        keys = {k for k in keys if k.startswith(f"{t}_")}

    if args.strategy:
        s = args.strategy.strip().lower()
        if s not in TACTICAL_SK:
            raise ValueError(f"未知 strategy: {s}，可选 {TACTICAL_SK}")
        keys = {k for k in keys if k.endswith(f"_{s}") or k == s or f"_{s}" in k}
        # 仅战术 key 含四策略名；反转不受 --strategy 过滤
        if not (args.reversal or args.all):
            keys = {k for k in keys if k in TACTICAL_KEYS}

    return sorted(keys)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 战术四策略 + 反转 手动开单轮次")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--tactical", action="store_true", help="12 个战术槽位（默认）")
    mode.add_argument("--reversal", action="store_true", help="3 个顶空底多")
    mode.add_argument("--all", action="store_true", help="战术 12 + 反转 3")
    mode.add_argument("--quick", action="store_true", help="冒烟 3 个: gemini_pullback, deepseek_chase, gpt_dump")
    parser.add_argument("--teacher", type=str, default="", help="gemini | deepseek | gpt")
    parser.add_argument(
        "--strategy",
        type=str,
        default="",
        help="pullback | rebound | chase | dump（仅战术）",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="逗号分隔 key，如 gemini_pullback,gemini_reversal",
    )
    parser.add_argument(
        "--require-open",
        action="store_true",
        default=True,
        help="要求 trades_opened >= 1（默认开启）",
    )
    parser.add_argument(
        "--allow-no-open",
        action="store_true",
        help="仅校验 pipeline 跑通，不要求开仓",
    )
    args = parser.parse_args()
    require_open = not args.allow_no_open

    try:
        keys = _resolve_keys(args)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    all_specs: Dict[str, RoundSpec] = {s.key: s for s in _build_specs()}
    unknown = [k for k in keys if k not in all_specs]
    if unknown:
        print(f"未知 key: {unknown}", file=sys.stderr)
        print(f"战术/反转可用: {', '.join(sorted(ALL_KEYS))}", file=sys.stderr)
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
