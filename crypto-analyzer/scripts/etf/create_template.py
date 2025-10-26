#!/usr/bin/env python3
"""
创建 ETF 数据导入模板
手动填写后可以导入到数据库

使用方法:
1. 运行此脚本生成模板
2. 访问 https://farside.co.uk/btc/ 查看最新数据
3. 手动填写 CSV 模板
4. 使用 import_etf_data.py 导入
"""

import csv
from datetime import date, timedelta


def create_btc_etf_template(filename='btc_etf_template.csv'):
    """创建 BTC ETF 导入模板"""

    # BTC ETF 列表 (按 Farside 顺序)
    btc_etfs = [
        'IBIT',   # BlackRock
        'FBTC',   # Fidelity
        'BITB',   # Bitwise
        'ARKB',   # ARK
        'BTCO',   # Invesco
        'EZBC',   # Franklin
        'BRRR',   # Valkyrie
        'HODL',   # VanEck
        'BTCW',   # WisdomTree
        'GBTC',   # Grayscale
        'DEFI'    # Hashdex
    ]

    yesterday = date.today() - timedelta(days=1)

    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        # 写入表头
        writer.writerow([
            'Date',
            'Ticker',
            'NetInflow',
            'GrossInflow',
            'GrossOutflow',
            'AUM',
            'BTCHoldings',
            'NAV',
            'Close',
            'Volume'
        ])

        # 写入示例行
        writer.writerow([
            str(yesterday),
            'IBIT',
            '125000000',      # 示例: $125M 净流入
            '150000000',      # 总流入
            '25000000',       # 总流出
            '35000000000',    # AUM: $35B
            '365000',         # BTC 持仓
            '35.50',          # NAV
            '35.48',          # 收盘价
            '85000000'        # 交易量
        ])

        # 为每个 ETF 创建空行
        for ticker in btc_etfs[1:]:  # 跳过 IBIT (已有示例)
            writer.writerow([
                str(yesterday),
                ticker,
                '0',  # 需要手动填写
                '',
                '',
                '',
                '',
                '',
                '',
                ''
            ])

    print(f"✅ BTC ETF 模板已创建: {filename}")
    print(f"\n包含 {len(btc_etfs)} 个 ETF:")
    for ticker in btc_etfs:
        print(f"  - {ticker}")


def create_eth_etf_template(filename='eth_etf_template.csv'):
    """创建 ETH ETF 导入模板"""

    # ETH ETF 列表
    eth_etfs = [
        'ETHA',   # BlackRock
        'FETH',   # Fidelity
        'ETHW',   # Bitwise
        'ETHV',   # VanEck
        'QETH',   # Invesco
        'EZET',   # Franklin
        'CETH',   # 21Shares
        'ETHE',   # Grayscale
        'ETH'     # Grayscale Mini
    ]

    yesterday = date.today() - timedelta(days=1)

    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)

        # 写入表头
        writer.writerow([
            'Date',
            'Ticker',
            'NetInflow',
            'GrossInflow',
            'GrossOutflow',
            'AUM',
            'ETHHoldings',
            'NAV',
            'Close',
            'Volume'
        ])

        # 写入示例行
        writer.writerow([
            str(yesterday),
            'ETHA',
            '25000000',       # 示例: $25M 净流入
            '35000000',
            '10000000',
            '5000000000',     # AUM: $5B
            '1250000',        # ETH 持仓
            '28.50',
            '28.48',
            '15000000'
        ])

        # 为每个 ETF 创建空行
        for ticker in eth_etfs[1:]:
            writer.writerow([
                str(yesterday),
                ticker,
                '0',
                '',
                '',
                '',
                '',
                '',
                '',
                ''
            ])

    print(f"✅ ETH ETF 模板已创建: {filename}")
    print(f"\n包含 {len(eth_etfs)} 个 ETF:")
    for ticker in eth_etfs:
        print(f"  - {ticker}")


def create_instructions():
    """创建填写说明"""

    instructions = """
================================================================================
ETF 数据填写说明
================================================================================

1. 数据来源
   推荐网站: https://farside.co.uk/
   - BTC ETF: https://farside.co.uk/btc/
   - ETH ETF: https://farside.co.uk/eth/

2. 字段说明
   - Date: 交易日期 (YYYY-MM-DD)
   - Ticker: ETF 代码
   - NetInflow: 净流入 (USD)，可以是负数
   - GrossInflow: 总流入 (可选)
   - GrossOutflow: 总流出 (可选)
   - AUM: 管理资产规模 (可选)
   - BTCHoldings/ETHHoldings: 持仓量 (可选)
   - NAV: 单位净值 (可选)
   - Close: 收盘价 (可选)
   - Volume: 交易量 (可选)

3. 填写示例
   如果 Farside 显示:

   Date        IBIT    FBTC    ARKB    BITB
   Oct 21      125.5   89.2    45.3    -12.1

   则在 CSV 中填写:

   Date,Ticker,NetInflow,...
   2024-10-21,IBIT,125500000,...
   2024-10-21,FBTC,89200000,...
   2024-10-21,ARKB,45300000,...
   2024-10-21,BITB,-12100000,...

   注意: Farside 的数字单位是百万美元 (M)，需要乘以 1,000,000

4. 导入数据
   填写完成后，运行:

   python3 import_etf_data.py --action import --file btc_etf_template.csv --asset-type BTC
   python3 import_etf_data.py --action import --file eth_etf_template.csv --asset-type ETH

5. 验证数据
   mysql -u root -p binance-data -e "SELECT * FROM v_etf_latest_flows;"

================================================================================
快捷方式
================================================================================

如果只需要填写净流入数据 (最简单):

1. 只填写 Date, Ticker, NetInflow 三列
2. 其他列留空即可
3. 系统会自动处理

示例:
Date,Ticker,NetInflow
2024-10-21,IBIT,125500000
2024-10-21,FBTC,89200000
2024-10-21,ARKB,45300000

================================================================================
"""

    with open('ETF_IMPORT_INSTRUCTIONS.txt', 'w', encoding='utf-8') as f:
        f.write(instructions)

    print("\n✅ 填写说明已创建: ETF_IMPORT_INSTRUCTIONS.txt")


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("ETF 数据模板生成工具")
    print("=" * 80 + "\n")

    # 创建模板
    create_btc_etf_template()
    print()
    create_eth_etf_template()
    print()
    create_instructions()

    print("\n" + "=" * 80)
    print("下一步")
    print("=" * 80)
    print("\n1. 访问 https://farside.co.uk/btc/ 查看最新 BTC ETF 数据")
    print("2. 打开 btc_etf_template.csv，填写 NetInflow 列")
    print("3. 访问 https://farside.co.uk/eth/ 查看最新 ETH ETF 数据")
    print("4. 打开 eth_etf_template.csv，填写 NetInflow 列")
    print("\n5. 导入数据:")
    print("   python3 import_etf_data.py --action import --file btc_etf_template.csv --asset-type BTC")
    print("   python3 import_etf_data.py --action import --file eth_etf_template.csv --asset-type ETH")
    print("\n6. 查看结果:")
    print("   mysql -u root -p binance-data -e \"SELECT * FROM v_etf_latest_flows;\"")
    print("\n" + "=" * 80 + "\n")


if __name__ == '__main__':
    main()
