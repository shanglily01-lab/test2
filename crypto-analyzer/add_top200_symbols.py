#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取Binance市值前200的USDT交易对并添加到config.yaml
"""
import ccxt
import yaml
from typing import List, Set
import time

def get_existing_symbols(config_path: str) -> Set[str]:
    """从config.yaml读取已有的交易对"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    existing = set(config.get('symbols', []))
    print(f"现有交易对数量: {len(existing)}")
    return existing

def get_top_binance_symbols(limit: int = 200) -> List[str]:
    """
    获取Binance按24小时交易量排名前N的USDT交易对

    Args:
        limit: 获取的交易对数量

    Returns:
        交易对列表，格式如 ['BTC/USDT', 'ETH/USDT', ...]
    """
    print(f"正在从Binance获取交易对数据...")

    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {
            'defaultType': 'spot',
        }
    })

    # 获取所有市场信息
    markets = exchange.load_markets()

    # 筛选USDT交易对
    usdt_pairs = []
    for symbol, market in markets.items():
        if market['quote'] == 'USDT' and market['active'] and market['spot']:
            usdt_pairs.append({
                'symbol': symbol,
                'base': market['base']
            })

    print(f"找到 {len(usdt_pairs)} 个活跃的USDT交易对")

    # 分批获取24小时ticker数据（每批100个）
    print("正在分批获取24小时交易数据...")
    batch_size = 100
    all_tickers = {}

    for i in range(0, len(usdt_pairs), batch_size):
        batch = usdt_pairs[i:i+batch_size]
        batch_symbols = [p['symbol'] for p in batch]

        try:
            print(f"  获取第 {i//batch_size + 1} 批 ({len(batch)} 个交易对)...")
            tickers = exchange.fetch_tickers(batch_symbols)
            all_tickers.update(tickers)
            time.sleep(0.5)  # 避免触发频率限制
        except Exception as e:
            print(f"  警告: 批次获取失败: {e}")
            # 如果批量失败，尝试逐个获取
            for symbol in batch_symbols:
                try:
                    ticker = exchange.fetch_ticker(symbol)
                    all_tickers[symbol] = ticker
                    time.sleep(0.1)
                except:
                    continue

    # 按24小时交易量排序
    sorted_pairs = []
    for pair in usdt_pairs:
        symbol = pair['symbol']
        if symbol in all_tickers:
            ticker = all_tickers[symbol]
            volume_usdt = ticker.get('quoteVolume', 0) or 0  # 使用USDT计价的交易量
            sorted_pairs.append({
                'symbol': symbol,
                'volume': volume_usdt
            })

    # 按交易量降序排序
    sorted_pairs.sort(key=lambda x: x['volume'], reverse=True)

    # 取前N个
    top_symbols = [p['symbol'] for p in sorted_pairs[:limit]]

    print(f"已获取交易量前{limit}的交易对")
    return top_symbols

def update_config_yaml(config_path: str, new_symbols: List[str]):
    """更新config.yaml，添加新的交易对"""
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 获取现有交易对
    existing = config.get('symbols', [])

    # 添加新交易对（保持原有顺序，新的追加到后面）
    config['symbols'] = existing + new_symbols

    # 写回文件
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"已更新 config.yaml，新增 {len(new_symbols)} 个交易对")

def main():
    config_path = 'd:\\test2\\crypto-analyzer\\config.yaml'

    try:
        # 1. 读取已有交易对
        existing_symbols = get_existing_symbols(config_path)

        # 2. 获取Binance前200交易对
        top200_symbols = get_top_binance_symbols(200)

        # 3. 过滤掉已存在的
        new_symbols = [s for s in top200_symbols if s not in existing_symbols]

        print(f"\n发现 {len(new_symbols)} 个新交易对需要添加:")
        print("="*80)

        # 显示前20个新交易对
        for i, symbol in enumerate(new_symbols[:20], 1):
            print(f"{i:3d}. {symbol}")

        if len(new_symbols) > 20:
            print(f"... 还有 {len(new_symbols) - 20} 个交易对")

        print("="*80)

        # 4. 更新config.yaml
        if new_symbols:
            update_config_yaml(config_path, new_symbols)

            print(f"\n统计:")
            print(f"   原有交易对: {len(existing_symbols)}")
            print(f"   新增交易对: {len(new_symbols)}")
            print(f"   总计交易对: {len(existing_symbols) + len(new_symbols)}")
        else:
            print("\n所有前200的交易对都已存在，无需添加")

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
