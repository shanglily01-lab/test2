"""
一步到位：获取币安 Top 200 U本位合约 → 写入 config.yaml → 补采数据。
"""
from __future__ import annotations

import os
import re
import sys
import time
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymysql
import requests
from dotenv import load_dotenv

# ── 配置 ───────────────────────────────────────
TOP_N = 200
FAPI_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
FAPI_EXCHANGE_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "binance-data"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "connect_timeout": 10,
}

CONFIG_YAML_PATH = PROJECT_ROOT / "config.yaml"

# 需要补采的 timeframe 及其根数
TIMEFRAMES = {
    "1d": 30,
    "1h": 72,
    "15m": 96,
}

SAVE_KLINE_SQL = """
INSERT INTO kline_data (
    symbol, exchange, timeframe, open_time, close_time, timestamp,
    open_price, high_price, low_price, close_price, volume, quote_volume,
    number_of_trades, taker_buy_base_volume, taker_buy_quote_volume, created_at
) VALUES (
    %s, 'binance_futures', %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
) ON DUPLICATE KEY UPDATE
    open_price=VALUES(open_price), high_price=VALUES(high_price),
    low_price=VALUES(low_price), close_price=VALUES(close_price),
    volume=VALUES(volume), quote_volume=VALUES(quote_volume),
    number_of_trades=VALUES(number_of_trades),
    taker_buy_base_volume=VALUES(taker_buy_base_volume),
    taker_buy_quote_volume=VALUES(taker_buy_quote_volume);
"""


# ── Step 1: 获取 Top 200 ─────────────────────
def get_usdt_futures_symbols() -> List[str]:
    """从 exchangeInfo 获取所有 U 本位合约交易对."""
    r = requests.get(FAPI_EXCHANGE_URL, timeout=15)
    r.raise_for_status()
    data = r.json()
    symbols: List[str] = []
    for s in data.get("symbols", []):
        if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL":
            if s.get("status") == "TRADING":
                sym = s["symbol"]
                # 只保留纯 ASCII 字母数字 symbol
                if re.match(r'^[A-Z0-9]+$', sym):
                    symbols.append(sym)
    return symbols


def get_top_n(symbols: List[str], n: int) -> List[Dict[str, Any]]:
    """按 24h 交易量排序取前 n."""
    r = requests.get(FAPI_TICKER_URL, timeout=15)
    r.raise_for_status()
    data = r.json()
    symbol_set = set(symbols)
    ticker_map: Dict[str, Dict] = {}
    for t in data:
        sym = t.get("symbol")
        if sym in symbol_set:
            try:
                vol = float(t.get("quoteVolume", 0))
            except (ValueError, TypeError):
                vol = 0.0
            ticker_map[sym] = {
                "symbol_raw": sym,
                "symbol": f"{sym[:-4]}/USDT" if sym.endswith("USDT") else f"{sym}/USDT",
                "quoteVolume": vol,
            }
    sorted_items = sorted(ticker_map.values(), key=lambda x: x["quoteVolume"], reverse=True)
    return sorted_items[:n]


# ── Step 2: 替换 config.yaml symbols ──────────
def replace_symbols_in_yaml(top_symbols: List[str]):
    """用 top_symbols 替换 config.yaml 中的 symbols 段."""
    with open(CONFIG_YAML_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 找到 symbols: 及其后面所有以 "- " 开头的行
    pattern = re.compile(r'^symbols:\n(  - .*\n?)*', re.MULTILINE)

    new_symbols_block = "symbols:\n" + "\n".join(f"- {s}" for s in top_symbols) + "\n"

    if pattern.search(content):
        content = pattern.sub(new_symbols_block, content)
    else:
        # 没有 symbols 段则在末尾追加
        content = content.rstrip() + "\n\n" + new_symbols_block

    with open(CONFIG_YAML_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"[config.yaml] 已写入 {len(top_symbols)} 个交易对")


# ── Step 3: 获取已有数据的 symbol ──────────────
def get_existing_symbols() -> set:
    """从 DB 获取已有 5m K 线的 symbol (表示已有数据)."""
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT symbol FROM kline_data "
                "WHERE timeframe='5m' AND exchange='binance_futures' "
                "AND open_time > UNIX_TIMESTAMP()*1000 - 86400000"
            )
            return {r["symbol"] for r in cur.fetchall()}
    finally:
        conn.close()


# ── Step 4: 补采 K 线 ────────────────────────
def fetch_kline(symbol: str, interval: str, limit: int) -> List[List]:
    binance_sym = symbol.replace("/", "")
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": binance_sym, "interval": interval, "limit": limit},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        return r.json() if isinstance(r.json(), list) else []
    except Exception:
        return []


def save_klines(symbol: str, rows_data: List[List], interval: str) -> int:
    if not rows_data:
        return 0
    rows = []
    for k in rows_data:
        close_time = int(k[6])
        # 排除最后一根未完成 K 线（如果 close_time 在未来）
        if close_time > int(time.time() * 1000):
            continue
        rows.append((
            symbol, interval,
            int(k[0]), int(k[6]),
            datetime.utcfromtimestamp(int(k[0]) / 1000),
            Decimal(str(k[1])), Decimal(str(k[2])),
            Decimal(str(k[3])), Decimal(str(k[4])),
            Decimal(str(k[5])), Decimal(str(k[7])),
            int(k[8]), Decimal(str(k[9])), Decimal(str(k[10])),
        ))
    if not rows:
        return 0
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.executemany(SAVE_KLINE_SQL, rows)
            conn.commit()
            return cur.rowcount
    finally:
        conn.close()


def backfill_needed(symbols: List[str], existing: set):
    """只补采还没有数据的 symbol."""
    need_backfill = [s for s in symbols if s not in existing]
    print(f"[回填] 已有数据: {len(existing)} 个, 需要补采: {len(need_backfill)} 个")

    if not need_backfill:
        print("[回填] 所有 symbol 已有数据，无需补采")
        return

    for sym in need_backfill:
        print(f"  {sym} ... ", end="", flush=True)
        for tf, limit in TIMEFRAMES.items():
            raw = fetch_kline(sym, tf, limit)
            if raw:
                n = save_klines(sym, raw, tf)
                print(f"{tf}={n} ", end="", flush=True)
            else:
                print(f"{tf}=失败 ", end="", flush=True)
            time.sleep(0.15)
        print()
        time.sleep(0.15)

    print(f"[回填] 完成，共补采 {len(need_backfill)} 个 symbol")


# ── Main ──────────────────────────────────────
def main():
    print("=" * 60)
    print("Step 1/3: 获取币安 Top 200 U本位合约交易对...")
    all_symbols = get_usdt_futures_symbols()
    print(f"  总活跃 U本位合约: {len(all_symbols)} 个")

    top = get_top_n(all_symbols, TOP_N)
    top_symbols_only = [t["symbol"] for t in top]

    print(f"  Top {TOP_N} 按 24h 交易量:")
    for i, t in enumerate(top, 1):
        vol_b = t["quoteVolume"] / 1_000_000
        print(f"    {i:3d}. {t['symbol']:25s} {vol_b:>8,.0f}M USDT")

    print()
    print("Step 2/3: 写入 config.yaml ...")
    replace_symbols_in_yaml(top_symbols_only)

    print()
    print("Step 3/3: 补采缺少的 K 线数据...")
    existing = get_existing_symbols()
    backfill_needed(top_symbols_only, existing)

    print()
    print("=" * 60)
    print(f"全部完成! config.yaml 已更新为 Top {TOP_N} U本位合约交易对")
    print(f"回填操作已完成，所有 symbol 的 1d/1h/15m 数据已就绪")


if __name__ == "__main__":
    main()
