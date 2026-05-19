"""
本地一次性 K 线回填工具

用途:
    当服务器端 fast_collector 因某些原因 (IP 被 ban / 进程卡死 / 配置 bug)
    没采到某个 timeframe 的 K 线 (典型: 1d K 线滞后 > 26 小时, 导致 S9
    Gemini AI 策略 _s9_fetch_market_data 静默 return None, 全部候选跳过),
    用本地机器把缺失数据从币安公开 API 拉回来, 写入远程 DB 的 kline_data.

为什么必须本地跑 (不能在服务器跑):
    - 服务器 IP 之前已多次 -1003 ban, 配额已紧张
    - 本地 IP 不同, 拉币安公开 K 线不计入服务器 weight 配额
    - 本机直连 fapi 是合法的 (扫描脚本对本工具白名单)

设计要点:
    - 不依赖 hub (hub 在 main 进程里, 本地连不上)
    - 复用 smart_futures_collector 的入库 SQL (INSERT ... ON DUPLICATE KEY UPDATE)
    - 排除最后一根未完成 K 线 (与 fast_collector 行为一致)
    - 默认每个 symbol 间 80ms sleep, 控制速率

用法:
    python scripts/backfill_klines.py                       # 默认: 补 1d, 拉每对 30 根
    python scripts/backfill_klines.py --tf 1d --limit 30
    python scripts/backfill_klines.py --tf 1h --limit 168   # 补 7d 1h
    python scripts/backfill_klines.py --tf 15m --limit 32   # 补 8h 15m
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List

import pymysql
import requests
from dotenv import load_dotenv

# Windows 终端 GBK 默认编码, 中文输出统一切 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

# 直接读项目根 .env, 不污染系统环境变量
project_root = Path(__file__).resolve().parent.parent
load_dotenv(project_root / ".env")

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

FAPI_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"


def get_active_symbols(symbols_cap: int) -> List[str]:
    """
    从 DB 取最近 24h 有 5m K 线的活跃 U 本位 symbol.
    5m K 线由 fast_collector 持续采集, 是判断 symbol "是否还活跃" 的可靠依据.
    """
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT symbol FROM kline_data
                WHERE timeframe='5m' AND exchange='binance_futures'
                  AND open_time > UNIX_TIMESTAMP() * 1000 - 86400000
                ORDER BY symbol
                """
            )
            rows = cur.fetchall() or []
        return [r["symbol"] for r in rows][:symbols_cap]
    finally:
        conn.close()


def fetch_kline(symbol: str, interval: str, limit: int) -> List[List]:
    """从币安拉 K 线. symbol 内部格式 BTC/USDT -> 币安格式 BTCUSDT."""
    binance_sym = symbol.replace("/", "")
    try:
        r = requests.get(
            FAPI_KLINES_URL,
            params={"symbol": binance_sym, "interval": interval, "limit": limit},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"  [{symbol}] HTTP {r.status_code}: {r.text[:200]}")
            return []
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"  [{symbol}] 异常: {type(e).__name__}: {e}")
        return []


SAVE_SQL = """
INSERT INTO kline_data (
    symbol, exchange, timeframe, open_time, close_time, timestamp,
    open_price, high_price, low_price, close_price,
    volume, quote_volume, number_of_trades,
    taker_buy_base_volume, taker_buy_quote_volume,
    created_at
) VALUES (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, NOW()
)
ON DUPLICATE KEY UPDATE
    open_price = VALUES(open_price), high_price = VALUES(high_price),
    low_price = VALUES(low_price), close_price = VALUES(close_price),
    volume = VALUES(volume), quote_volume = VALUES(quote_volume),
    number_of_trades = VALUES(number_of_trades),
    taker_buy_base_volume = VALUES(taker_buy_base_volume),
    taker_buy_quote_volume = VALUES(taker_buy_quote_volume)
"""


def save_klines(symbol: str, interval: str, raw_klines: List[List]) -> int:
    """
    写入 kline_data 表. 与 smart_futures_collector.save_klines 行为一致:
    - 排除最后一根未完成的 K 线
    - exchange 固定 'binance_futures'
    - ON DUPLICATE KEY UPDATE 安全重跑
    """
    if not raw_klines:
        return 0
    completed = raw_klines[:-1] if len(raw_klines) > 1 else raw_klines
    rows = []
    for k in completed:
        rows.append(
            (
                symbol, "binance_futures", interval,
                int(k[0]), int(k[6]),
                datetime.utcfromtimestamp(int(k[0]) / 1000),
                Decimal(str(k[1])), Decimal(str(k[2])),
                Decimal(str(k[3])), Decimal(str(k[4])),
                Decimal(str(k[5])), Decimal(str(k[7])),
                int(k[8]),
                Decimal(str(k[9])), Decimal(str(k[10])),
            )
        )
    if not rows:
        return 0
    conn = pymysql.connect(**DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.executemany(SAVE_SQL, rows)
            conn.commit()
            return cur.rowcount
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="本地一次性回填 K 线到远程 DB")
    ap.add_argument(
        "--tf", default="1d",
        choices=["1d", "4h", "1h", "15m", "5m"],
        help="时间周期 (默认 1d)",
    )
    ap.add_argument(
        "--limit", type=int, default=30,
        help="每个 symbol 拉多少根 (默认 30)",
    )
    ap.add_argument(
        "--symbols-cap", type=int, default=300,
        help="最多处理多少 symbol (默认 300)",
    )
    ap.add_argument(
        "--sleep-ms", type=int, default=80,
        help="每 symbol 之间 sleep ms (默认 80, 防 ban)",
    )
    args = ap.parse_args()

    print(
        f"[配置] tf={args.tf} limit={args.limit} "
        f"symbols_cap={args.symbols_cap} sleep_ms={args.sleep_ms}"
    )
    print(
        f"[DB] {DB_CONFIG['user']}@{DB_CONFIG['host']}:"
        f"{DB_CONFIG['port']}/{DB_CONFIG['database']}"
    )

    symbols = get_active_symbols(args.symbols_cap)
    print(f"[symbols] 共 {len(symbols)} 个活跃 symbol")
    if not symbols:
        print("没有活跃 symbol, 退出")
        return 0

    total_saved = 0
    failed: List[str] = []
    t0 = time.time()
    for i, sym in enumerate(symbols, 1):
        raw = fetch_kline(sym, args.tf, args.limit)
        if not raw:
            failed.append(sym)
            time.sleep(args.sleep_ms / 1000.0)
            continue
        try:
            n = save_klines(sym, args.tf, raw)
            total_saved += n
        except Exception as e:
            print(f"  [{sym}] 入库失败: {e}")
            failed.append(sym)
        if i % 25 == 0 or i == len(symbols):
            elapsed = time.time() - t0
            print(
                f"  [{i}/{len(symbols)}] 累计入库 {total_saved} 行, "
                f"失败 {len(failed)} 个, {elapsed:.1f}s"
            )
        time.sleep(args.sleep_ms / 1000.0)

    elapsed = time.time() - t0
    print(
        f"\n[完成] 总耗时 {elapsed:.1f}s, 入库 {total_saved} 行, "
        f"失败 {len(failed)} 个"
    )
    if failed:
        print(f"  失败前 20: {failed[:20]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
