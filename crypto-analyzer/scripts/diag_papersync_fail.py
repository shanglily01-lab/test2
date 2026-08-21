#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print PaperSync / live-open failures from crypto-app-main logs.

Run on the server, from the project root (where logs/ lives):

    cd /path/to/crypto-analyzer
    python3 scripts/diag_papersync_fail.py
    python3 scripts/diag_papersync_fail.py --date 2026-08-21
    python3 scripts/diag_papersync_fail.py --date 2026-08-21 --symbol DOGS

Does not query MySQL. Does not retry or change any order.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"

LINE_RE = re.compile(
    r"PaperSync|实盘开仓失败|开仓失败|开仓异常|开仓后补保存|"
    r"无法获取.*价格|IP banned|币安API错误|"
    r"engine_manager 未初始化|交易引擎为 None|"
    r"无法计算SL/TP|获取价格失败"
)
KEEP_SYMBOLS = {
    "DOGS", "BABY", "AVAX", "DOGE", "MOODENG", "ETHW", "COMP", "BSB",
    "RUNE", "XLM", "AIOT", "BERA", "NIL", "MORPHO",
}


def _iter_log_files(date: str | None) -> list[Path]:
    if date:
        exact = LOG_DIR / f"main_{date}.log"
        systemd = LOG_DIR / "main_systemd.log"
        files = [p for p in (exact, systemd) if p.is_file()]
        if not files:
            print(f"missing {exact}", file=sys.stderr)
        return files
    files = sorted(LOG_DIR.glob("main_2026-08-2*.log"))
    systemd = LOG_DIR / "main_systemd.log"
    if systemd.is_file():
        files.append(systemd)
    return files


def _want(line: str, symbol: str | None) -> bool:
    if not LINE_RE.search(line):
        return False
    if symbol:
        return symbol.upper() in line.upper()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Grep PaperSync live-open failures")
    parser.add_argument("--date", help="UTC log date, e.g. 2026-08-21")
    parser.add_argument("--symbol", help="filter one symbol, e.g. DOGS")
    parser.add_argument("--limit", type=int, default=400, help="max lines to print")
    args = parser.parse_args()

    files = _iter_log_files(args.date)
    if not files:
        print(f"no logs in {LOG_DIR}", file=sys.stderr)
        return 1

    print(f"cwd logs dir: {LOG_DIR}")
    n = 0
    by_sym: dict[str, int] = {}
    for path in files:
        print(f"\n===== {path.name} =====")
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"read fail: {e}", file=sys.stderr)
            continue
        for line in text.splitlines():
            if not _want(line, args.symbol):
                continue
            n += 1
            if n <= args.limit:
                print(line[:1200])
            for token in KEEP_SYMBOLS:
                if token in line.upper() or f"{token}/USDT" in line.upper():
                    by_sym[token] = by_sym.get(token, 0) + 1
                    break
        if n > args.limit:
            print(f"... truncated, matched {n} lines (use --limit)")

    print("\n===== counts by known symbol =====")
    for k, v in sorted(by_sym.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"{k:10} {v}")
    print(f"total matched lines: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
