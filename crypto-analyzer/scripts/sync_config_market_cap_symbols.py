#!/usr/bin/env python3
"""按 CoinGecko 市值把已上 Binance U 本位永续的交易对写入 config.yaml。

默认写入前 300 名（超级大脑扫描全集；破位策略取前 100）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.sync_config_usdt_futures_symbols import (  # noqa: E402
    CONFIG_PATH,
    count_config_symbols,
    replace_symbols_in_config,
)

FAPI_EXCHANGE_INFO = (
    "https://fapi.binance.com/fapi/v1/exchangeInfo",
    "https://data-api.binance.vision/fapi/v1/exchangeInfo",
)

CG_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
COINPAPRIKA_TICKERS = "https://api.coinpaprika.com/v1/tickers"
COINLORE_TICKERS = "https://api.coinlore.net/api/tickers/"
COINCAP_ASSETS = "https://api.coincap.io/v2/assets"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; crypto-analyzer-market-cap-sync/1.0)",
}


def _get_json(url: str, params: dict | None = None, timeout: int = 30) -> dict | list:
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if resp.status_code == 429:
                time.sleep(2 + attempt * 2)
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"GET {url} failed: {last_err}")


def fetch_usdt_perpetuals() -> list[str]:
    last_err: Exception | None = None
    for url in FAPI_EXCHANGE_INFO:
        try:
            data = _get_json(url, timeout=30)
            out: list[str] = []
            for s in (data or {}).get("symbols", []):
                if (
                    s.get("status") == "TRADING"
                    and s.get("contractType") == "PERPETUAL"
                    and s.get("quoteAsset") == "USDT"
                ):
                    out.append(f"{s['baseAsset']}/USDT")
            if out:
                return sorted(set(out))
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"exchangeInfo failed: {last_err}")


def fetch_coingecko_tickers(pages: int = 3, per_page: int = 250) -> list[str]:
    tickers: list[str] = []
    for page in range(1, pages + 1):
        data = _get_json(
            CG_MARKETS,
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
            },
        )
        if not isinstance(data, list) or not data:
            break
        for row in data:
            sym = str((row or {}).get("symbol") or "").upper().strip()
            if sym:
                tickers.append(sym)
        time.sleep(1.2)
    return tickers


def fetch_coinpaprika_tickers() -> list[str]:
    data = _get_json(COINPAPRIKA_TICKERS, {"quotes": "USD"})
    rows = data if isinstance(data, list) else []
    rows = sorted(rows, key=lambda r: int((r or {}).get("rank") or 10**9))
    tickers: list[str] = []
    for row in rows:
        sym = str((row or {}).get("symbol") or "").upper().strip()
        if sym:
            tickers.append(sym)
    return tickers


def fetch_coinlore_tickers(limit: int = 400) -> list[str]:
    tickers: list[str] = []
    start = 0
    while start < limit:
        data = _get_json(COINLORE_TICKERS, {"start": start, "limit": min(100, limit - start)})
        rows = (data or {}).get("data") if isinstance(data, dict) else data
        chunk = []
        for row in rows or []:
            sym = str((row or {}).get("symbol") or "").upper().strip()
            if sym:
                chunk.append(sym)
        if not chunk:
            break
        tickers.extend(chunk)
        start += len(chunk)
    return tickers


def fetch_coincap_tickers(limit: int = 400) -> list[str]:
    data = _get_json(COINCAP_ASSETS, {"limit": limit})
    rows = (data or {}).get("data") if isinstance(data, dict) else data
    tickers: list[str] = []
    for row in rows or []:
        sym = str((row or {}).get("symbol") or "").upper().strip()
        if sym:
            tickers.append(sym)
    return tickers


def fetch_ranked_tickers() -> tuple[list[str], str]:
    errors: list[str] = []
    for name, fn, min_n in (
        ("coinpaprika", fetch_coinpaprika_tickers, 200),
        ("coinlore", lambda: fetch_coinlore_tickers(500), 200),
        ("coingecko", fetch_coingecko_tickers, 200),
        ("coincap", fetch_coincap_tickers, 50),
    ):
        try:
            tickers = fn()
            if len(tickers) >= min_n:
                return tickers, name
            errors.append(f"{name} n={len(tickers)}")
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError("市值源全部失败: " + "; ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.services.market_cap_universe import match_coingecko_to_binance

    perps = fetch_usdt_perpetuals()
    print(f"币安 U 本位永续 TRADING: {len(perps)}")
    tickers, source = fetch_ranked_tickers()
    print(f"市值源 {source}: {len(tickers)} tickers")

    symbols = match_coingecko_to_binance(tickers, perps, limit=args.limit)
    print(f"映射到永续: {len(symbols)} / 目标 {args.limit}")
    if len(symbols) < min(100, args.limit):
        raise RuntimeError(f"映射过少: {len(symbols)}")

    for check in ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT"):
        idx = symbols.index(check) + 1 if check in symbols else None
        print(f"  {check}: {'#' + str(idx) if idx else 'NO'}")
    print("  前10:", ", ".join(symbols[:10]))
    if args.limit >= 100:
        print("  第100:", symbols[99] if len(symbols) >= 100 else "N/A")

    if args.dry_run:
        return 0

    old_count = count_config_symbols()
    lines = CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("# U 本位合约监控列表"):
            lines[i] = (
                "# U 本位合约监控列表 (仅 /USDT) — 按市值序，映射 Binance U 本位永续。"
            )
        elif "不含黑名单3级" in line and i > 400:
            lines[i] = (
                f"# 超级大脑扫描前 {args.limit}；破位策略扫描前 100。"
                " 证券类与 L2+/锁定仍由运行时闸门剔除。"
            )
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    replace_symbols_in_config(symbols)
    print(f"config.yaml 已更新: {old_count} -> {len(symbols)} symbols ({source})")
    print("请重启 crypto-ws-kline / crypto-scheduler / crypto-app-main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
