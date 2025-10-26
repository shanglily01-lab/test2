#!/usr/bin/env python3
"""
ETF 数据转换助手
帮助你快速将 Farside 网站的数据转换为 CSV 格式

使用方法:
1. 从 https://farside.co.uk/btc/ 复制最新一天的数据
2. 运行此脚本
3. 粘贴数据
4. 自动生成 CSV
"""

from datetime import date, timedelta


def convert_farside_to_csv():
    """交互式转换工具"""

    print("\n" + "=" * 80)
    print("ETF 数据转换助手")
    print("=" * 80)

    # 获取日期
    print("\n📅 请输入交易日期 (格式: Oct 21 或 10-21)")
    print("   提示: 按回车使用昨天的日期")
    date_input = input("日期: ").strip()

    if not date_input:
        trade_date = date.today() - timedelta(days=1)
        print(f"   使用昨天: {trade_date}")
    else:
        # 解析日期
        trade_date = parse_date_input(date_input)
        if not trade_date:
            print("❌ 日期格式错误，使用昨天")
            trade_date = date.today() - timedelta(days=1)

    # 选择资产类型
    print("\n📊 请选择资产类型:")
    print("   1. BTC (Bitcoin ETF)")
    print("   2. ETH (Ethereum ETF)")
    asset_choice = input("选择 (1 或 2): ").strip()

    if asset_choice == '2':
        asset_type = 'ETH'
        tickers = ['ETHA', 'FETH', 'ETHW', 'ETHV', 'QETH', 'EZET', 'CETH', 'ETHE', 'ETH']
    else:
        asset_type = 'BTC'
        tickers = ['IBIT', 'FBTC', 'BITB', 'ARKB', 'BTCO', 'EZBC', 'BRRR', 'HODL', 'BTCW', 'GBTC', 'DEFI']

    print(f"\n✅ 选择了 {asset_type} ETF")
    print(f"   需要输入 {len(tickers)} 个 ETF 的数据")

    # 收集数据
    print("\n" + "=" * 80)
    print("📝 请输入每个 ETF 的数值")
    print("=" * 80)
    print("\n提示:")
    print("  - 从 Farside 网站复制的数字 (如: 125.5)")
    print("  - 负数可以输入负号 (如: -156.2)")
    print("  - 红色括号数字也可以直接输入 (如: (156.2) 会自动转为 -156.2)")
    print("  - 如果没有数据，输入 0")
    print("  - 单位会自动转换 (× 1,000,000)\n")

    etf_data = []

    for ticker in tickers:
        while True:
            value_input = input(f"{ticker:6s}: ").strip()

            if not value_input:
                value_input = "0"

            try:
                # 处理括号 (红色括号表示负数)
                if value_input.startswith('(') and value_input.endswith(')'):
                    # (156.2) -> -156.2
                    value_input = '-' + value_input[1:-1]

                # 解析数值
                farside_value = float(value_input)
                # 转换为实际金额 (百万 -> 美元)
                net_inflow = int(farside_value * 1000000)

                etf_data.append({
                    'ticker': ticker,
                    'farside_value': farside_value,
                    'net_inflow': net_inflow
                })

                # 显示转换结果
                sign = '+' if net_inflow >= 0 else ''
                print(f"       → {sign}${net_inflow:,}")
                break

            except ValueError:
                print("       ❌ 输入错误，请输入数字 (如: 125.5 或 -156.2)")

    # 生成 CSV
    print("\n" + "=" * 80)
    print("💾 生成 CSV 文件")
    print("=" * 80)

    filename = f"{asset_type.lower()}_etf_{trade_date.strftime('%Y%m%d')}.csv"

    with open(filename, 'w', encoding='utf-8') as f:
        # 写入表头
        f.write("Date,Ticker,NetInflow\n")

        # 写入数据
        for data in etf_data:
            f.write(f"{trade_date},{data['ticker']},{data['net_inflow']}\n")

    print(f"\n✅ CSV 文件已生成: {filename}")

    # 显示汇总
    total_inflow = sum(d['net_inflow'] for d in etf_data)
    inflow_count = sum(1 for d in etf_data if d['net_inflow'] > 0)
    outflow_count = sum(1 for d in etf_data if d['net_inflow'] < 0)

    print("\n" + "=" * 80)
    print("📊 数据汇总")
    print("=" * 80)
    print(f"\n日期: {trade_date}")
    print(f"资产: {asset_type}")
    print(f"ETF 数量: {len(etf_data)}")
    print(f"流入 ETF: {inflow_count} 个")
    print(f"流出 ETF: {outflow_count} 个")
    print(f"总净流入: ${total_inflow:,}")

    if total_inflow > 200000000:
        print("\n🚀 强烈看涨信号！(总流入 > $200M)")
    elif total_inflow > 100000000:
        print("\n📈 看涨信号 (总流入 > $100M)")
    elif total_inflow > 0:
        print("\n✅ 温和看涨 (总流入 > $0)")
    else:
        print("\n⚠️  看跌信号 (总流出)")

    # 前三名
    sorted_data = sorted(etf_data, key=lambda x: x['net_inflow'], reverse=True)

    print("\n流入前三名:")
    for i, data in enumerate(sorted_data[:3], 1):
        print(f"  {i}. {data['ticker']}: ${data['net_inflow']:,}")

    print("\n流出最多:")
    for i, data in enumerate(reversed(sorted_data[-3:]), 1):
        if data['net_inflow'] < 0:
            print(f"  {i}. {data['ticker']}: ${data['net_inflow']:,}")

    # 下一步提示
    print("\n" + "=" * 80)
    print("下一步")
    print("=" * 80)
    print(f"\n1. 导入到数据库:")
    print(f"   python3 import_etf_data.py --action import --file {filename} --asset-type {asset_type}")
    print(f"\n2. 查看结果:")
    print(f"   mysql -u root -p binance-data -e \"SELECT * FROM v_etf_latest_flows;\"")
    print("\n" + "=" * 80 + "\n")


def parse_date_input(date_str: str) -> date:
    """解析用户输入的日期"""
    import datetime

    try:
        # 尝试 "Oct 21" 格式
        if ' ' in date_str:
            month_str, day_str = date_str.split()
            month_map = {
                'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
                'may': 5, 'jun': 6, 'jul': 7, 'aug': 8,
                'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
            }
            month = month_map.get(month_str.lower()[:3])
            day = int(day_str)
            year = datetime.date.today().year
            return datetime.date(year, month, day)

        # 尝试 "10-21" 格式
        elif '-' in date_str:
            parts = date_str.split('-')
            if len(parts) == 2:
                month, day = map(int, parts)
                year = datetime.date.today().year
                return datetime.date(year, month, day)

        return None
    except:
        return None


def main():
    """主函数"""
    try:
        convert_farside_to_csv()
    except KeyboardInterrupt:
        print("\n\n❌ 已取消\n")
    except Exception as e:
        print(f"\n❌ 错误: {e}\n")


if __name__ == '__main__':
    main()
