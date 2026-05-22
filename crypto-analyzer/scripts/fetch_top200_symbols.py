"""
从币安获取按 24h 交易量排名前 200 的 U 本位合约交易对，
输出可直接写入 config.yaml symbols 段的列表。
"""
import sys
import requests
from typing import List, Dict, Any

FAPI_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
FAPI_EXCHANGE_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

TOP_N = 200


def get_usdt_futures_symbols() -> List[str]:
    """从 exchangeInfo 获取所有 U 本位合约交易对."""
    r = requests.get(FAPI_EXCHANGE_URL, timeout=15)
    if r.status_code != 200:
        print(f"[错误] 获取 exchangeInfo 失败: HTTP {r.status_code}", file=sys.stderr)
        sys.exit(1)
    data = r.json()
    symbols: List[str] = []
    for s in data.get("symbols", []):
        if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL":
            if s.get("status") == "TRADING":
                symbols.append(s["symbol"])
    return symbols


def get_top_n_by_volume(symbols: List[str], n: int) -> List[Dict[str, Any]]:
    """从 ticker/24hr 获取交易量并排序取前 n."""
    r = requests.get(FAPI_TICKER_URL, timeout=15)
    if r.status_code != 200:
        print(f"[错误] 获取 ticker/24hr 失败: HTTP {r.status_code}", file=sys.stderr)
        sys.exit(1)
    data = r.json()
    symbol_set = set(symbols)
    # Filter to our perpetual symbols, extract volume info
    ticker_map: Dict[str, Dict] = {}
    for t in data:
        sym = t.get("symbol")
        if sym in symbol_set:
            try:
                vol = float(t.get("quoteVolume", 0))
            except (ValueError, TypeError):
                vol = 0.0
            ticker_map[sym] = {
                "symbol": f"{sym[:-4]}/USDT" if sym.endswith("USDT") else f"{sym}/USDT",
                "quoteVolume": vol,
            }

    sorted_items = sorted(ticker_map.values(), key=lambda x: x["quoteVolume"], reverse=True)
    return sorted_items[:n]


def main():
    print("[1/2] 正在获取 U 本位合约列表...", file=sys.stderr)
    all_usdt_futures = get_usdt_futures_symbols()
    print(f"      共 {len(all_usdt_futures)} 个活跃 U 本位合约交易对", file=sys.stderr)

    print(f"[2/2] 按 24h 交易量排序取 Top {TOP_N}...", file=sys.stderr)
    top = get_top_n_by_volume(all_usdt_futures, TOP_N)

    if not top:
        print("未获取到数据", file=sys.stderr)
        sys.exit(1)

    print(f"\n按 24h 交易量排序的前 {TOP_N} 个 U 本位合约:\n", file=sys.stderr)
    for i, item in enumerate(top, 1):
        sym_display = item["symbol"]
        vol_b = item["quoteVolume"] / 1_000_000
        print(f"  {i:3d}. {sym_display:20s} 24h交易量={vol_b:,.0f}M USDT", file=sys.stderr)

    # Output just the symbols list for config.yaml
    print("\n=== 写入 config.yaml 的 symbols 列表 ===\n")
    print("symbols:")
    for item in top:
        sym = item["symbol"]
        print(f"- {sym}")

    print(f"\n共 {len(top)} 个交易对")


if __name__ == "__main__":
    main()
