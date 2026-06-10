#!/usr/bin/env python3
"""将币安 U 本位永续全部 TRADING 交易对写入 config.yaml symbols（供 WS K 线采集订阅）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"


def fetch_usdt_perpetuals() -> list[str]:
    resp = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30)
    resp.raise_for_status()
    out: list[str] = []
    for s in resp.json().get("symbols", []):
        if (
            s.get("status") == "TRADING"
            and s.get("contractType") == "PERPETUAL"
            and s.get("quoteAsset") == "USDT"
        ):
            out.append(f"{s['baseAsset']}/USDT")
    return sorted(set(out))


def count_config_symbols() -> int:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    return sum(1 for line in text.splitlines() if line.startswith("- ") and "/USDT" in line)


def replace_symbols_in_config(symbols: list[str]) -> None:
    """只替换 symbols: 段，保留 config.yaml 其余内容与注释。"""
    lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "symbols:":
            start = i
            break
    if start is None:
        raise RuntimeError("config.yaml 中未找到 symbols: 段")

    new_lines = lines[: start + 1]
    new_lines.extend(f"- {sym}" for sym in symbols)
    CONFIG_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印数量，不写文件")
    args = parser.parse_args()

    symbols = fetch_usdt_perpetuals()
    print(f"币安 U 本位永续 TRADING: {len(symbols)} 个")
    for check in ("TREE/USDT", "VANA/USDT", "BTC/USDT"):
        print(f"  {check}: {'YES' if check in symbols else 'NO'}")

    if args.dry_run:
        return 0

    old_count = count_config_symbols()
    replace_symbols_in_config(symbols)
    print(f"config.yaml 已更新: {old_count} -> {len(symbols)} symbols")
    print("请重启 ws_kline_collector_service 使 WS 订阅生效")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
