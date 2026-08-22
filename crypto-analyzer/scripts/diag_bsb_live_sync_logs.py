#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print crypto-app-main logs around BSB / PaperSync live-sync failures.

Run on the server, from the project root (the directory that has logs/):

    cd /home/ec2-user/crypto-analyzer          # 改成你的实际路径
    python3 scripts/diag_bsb_live_sync_logs.py
    python3 scripts/diag_bsb_live_sync_logs.py --symbol BSB
    python3 scripts/diag_bsb_live_sync_logs.py --date 2026-08-22 --around 06:22

Does not query MySQL. Does not place or retry any order.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"

# 已知 FAILED 成交时刻（UTC，与 futures_orders.fill_time 对齐）
KNOWN_FILLS = (
    ("2026-08-22", "06:22", "BSB"),
    ("2026-08-22", "08:42", "DOGE"),
    ("2026-08-22", "05:22", "XVG"),
    ("2026-08-22", "05:10", "ICNT"),
    ("2026-08-22", "03:56", "BTW"),
    ("2026-08-22", "02:46", "CRV"),
    ("2026-08-21", "17:24", "BSB"),
)

TOPIC_RE = re.compile(
    r"PaperSync|模拟成交|发送开仓订单|实盘开仓失败|开仓失败|开仓异常|"
    r"开仓后补保存|无法获取.*价格|获取价格失败|无法计算SL/TP|"
    r"IP banned|币安API错误|engine_manager|交易引擎为 None|"
    r"ticker 无价|实时价不可用|参考价"
)


def _log_files(dates: list[str]) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for date in dates:
        for name in (f"main_{date}.log", "main_systemd.log"):
            path = LOG_DIR / name
            if path.is_file() and path not in seen:
                files.append(path)
                seen.add(path)
    return files


def _near_fill(line: str, date: str, hhmm: str) -> bool:
    """True if line timestamp is in hhmm, hhmm-1, hhmm+1 (same UTC date)."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    for delta in (-1, 0, 1):
        m = minute + delta
        h = hour
        if m < 0:
            h -= 1
            m += 60
        elif m > 59:
            h += 1
            m -= 60
        if h < 0 or h > 23:
            continue
        if f"{date} {h:02d}:{m:02d}" in line:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Print BSB/PaperSync live-sync logs")
    parser.add_argument("--date", help="UTC log date, e.g. 2026-08-22")
    parser.add_argument("--symbol", default="BSB", help="coin, default BSB; ALL = no coin filter")
    parser.add_argument("--around", help="UTC HH:MM window, e.g. 06:22 (dumps all topic lines near it)")
    parser.add_argument("--limit", type=int, default=300)
    args = parser.parse_args()

    dates = [args.date] if args.date else ["2026-08-22", "2026-08-21"]
    files = _log_files(dates)
    if not files:
        print(f"no logs in {LOG_DIR} for {dates}", file=sys.stderr)
        print("pwd should be the project root that contains logs/", file=sys.stderr)
        return 1

    symbol = (args.symbol or "BSB").upper()
    want_all = symbol in {"ALL", "*", "ANY"}
    print(f"logs dir: {LOG_DIR}")
    print(f"dates={dates} symbol={symbol} around={args.around or 'known fills + symbol'}")
    print()

    n = 0
    sent: list[str] = []
    failed: list[str] = []
    for path in files:
        print(f"===== {path.name} =====")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            print(f"read fail: {e}", file=sys.stderr)
            continue

        for line in lines:
            if not TOPIC_RE.search(line):
                continue
            keep = False
            if want_all:
                keep = True
            elif symbol in line.upper():
                keep = True
            elif args.around:
                for date in dates:
                    if _near_fill(line, date, args.around):
                        keep = True
                        break
            else:
                for date, hhmm, _sym in KNOWN_FILLS:
                    if date in str(path.name) and _near_fill(line, date, hhmm):
                        keep = True
                        break
            if not keep:
                continue
            n += 1
            clipped = line[:1400]
            if n <= args.limit:
                print(clipped)
            if "发送开仓订单" in line:
                sent.append(clipped)
            if re.search(r"开仓失败|获取价格失败|无法获取|PaperSync.*失败|同步异常", line):
                failed.append(clipped)
        print()

    if n > args.limit:
        print(f"... truncated, matched {n} lines (use --limit)")

    print("===== 发送开仓订单 (证明有没有打到币安) =====")
    if sent:
        for line in sent:
            print(line)
    else:
        print("(none)  窗口内没有「发送开仓订单」= 单没发到币安")

    print("\n===== 失败/取价 =====")
    if failed:
        for line in failed:
            print(line)
    else:
        print("(none)")

    print(f"\ntotal matched lines: {n}")
    print("hint: python3 scripts/diag_bsb_live_sync_logs.py --symbol ALL --date 2026-08-22 --around 06:22")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
