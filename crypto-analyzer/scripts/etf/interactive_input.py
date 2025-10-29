#!/usr/bin/env python3
"""
ETF 数据交互式录入工具
可以在终端直接输入数据，自动生成CSV文件

使用方法:
    python3 interactive_input.py
"""

import csv
from datetime import date, timedelta
from typing import List, Dict
import os
import sys


class ETFInteractiveInput:
    """ETF 数据交互式录入"""

    @staticmethod
    def parse_number(input_str: str) -> float:
        """
        解析数字输入，支持多种格式：
        - 普通数字: 123.45
        - 千分位: 65,430 -> 65430
        - 括号表示负数: (60.5) -> -60.5
        - 带括号和逗号: (1,234.56) -> -1234.56
        """
        if not input_str:
            return 0.0

        input_str = input_str.strip()

        # 检查是否有括号（表示负数）
        is_negative = False
        if input_str.startswith('(') and input_str.endswith(')'):
            is_negative = True
            input_str = input_str[1:-1].strip()

        # 移除千分位逗号
        input_str = input_str.replace(',', '')

        # 转换为浮点数
        try:
            value = float(input_str)
            return -value if is_negative else value
        except ValueError:
            raise ValueError(f"无法解析数字: {input_str}")

    # ETF 列表
    BTC_ETFS = [
        ('IBIT', 'BlackRock'),
        ('FBTC', 'Fidelity'),
        ('BITB', 'Bitwise'),
        ('ARKB', 'ARK'),
        ('BTCO', 'Invesco'),
        ('EZBC', 'Franklin'),
        ('BRRR', 'Valkyrie'),
        ('HODL', 'VanEck'),
        ('BTCW', 'WisdomTree'),
        ('GBTC', 'Grayscale'),
        ('DEFI', 'Hashdex')
    ]

    ETH_ETFS = [
        ('ETHA', 'BlackRock'),
        ('FETH', 'Fidelity'),
        ('ETHW', 'Bitwise'),
        ('ETHV', 'VanEck'),
        ('QETH', 'Invesco'),
        ('EZET', 'Franklin'),
        ('CETH', '21Shares'),
        ('ETHE', 'Grayscale'),
        ('ETH', 'Grayscale Mini')
    ]

    def __init__(self):
        self.data = []
        self.trade_date = None
        self.asset_type = None

    def clear_screen(self):
        """清屏"""
        os.system('clear' if os.name == 'posix' else 'cls')

    def print_header(self):
        """打印标题"""
        print("\n" + "=" * 80)
        print("  ETF 数据交互式录入工具")
        print("=" * 80 + "\n")

    def select_asset_type(self) -> str:
        """选择资产类型"""
        print("请选择要录入的ETF类型:")
        print("  [1] BTC ETF (11个)")
        print("  [2] ETH ETF (9个)")
        print("  [3] 两者都录入")
        print()

        while True:
            choice = input("请输入选项 (1/2/3): ").strip()
            if choice == '1':
                return 'BTC'
            elif choice == '2':
                return 'ETH'
            elif choice == '3':
                return 'BOTH'
            else:
                print("❌ 无效选项，请重新输入")

    def input_date(self) -> str:
        """输入日期"""
        yesterday = date.today() - timedelta(days=1)
        default_date = str(yesterday)

        print(f"\n请输入交易日期 (格式: YYYY-MM-DD)")
        print(f"直接按回车使用默认日期: {default_date}")

        while True:
            date_input = input("日期: ").strip()

            if not date_input:
                return default_date

            # 简单验证日期格式
            try:
                year, month, day = date_input.split('-')
                if len(year) == 4 and len(month) == 2 and len(day) == 2:
                    return date_input
                else:
                    print("❌ 日期格式错误，请使用 YYYY-MM-DD 格式")
            except:
                print("❌ 日期格式错误，请使用 YYYY-MM-DD 格式")

    def input_etf_data(self, etf_list: List[tuple], asset_type: str):
        """录入ETF数据"""
        print(f"\n{'=' * 80}")
        print(f"  开始录入 {asset_type} ETF 数据 ({len(etf_list)} 个)")
        print(f"{'=' * 80}\n")

        print("💡 提示:")
        print("  - 净流入：Farside网站显示的单位是百万美元（M），请直接输入该数字")
        print("    • 正常数字: 125.5")
        print("    • 带千分位: 65,430 (自动识别)")
        print("    • 括号表示负数: (60.5) = -60.5")
        if asset_type == 'BTC':
            print("  - BTC持仓：输入BTC数量")
            print("    • 例如: 45123.5 或 45,123.5")
        else:
            print("  - ETH持仓：输入ETH数量")
            print("    • 例如: 123456.78 或 123,456.78")
        print("  - 如果没有数据或为0，直接按回车跳过")
        print("  - 输入 'q' 退出当前录入\n")

        for idx, (ticker, provider) in enumerate(etf_list, 1):
            print(f"[{idx}/{len(etf_list)}] {ticker} ({provider})")

            # 1. 录入净流入
            while True:
                net_inflow_input = input(f"    净流入(M USD): ").strip()

                # 退出
                if net_inflow_input.lower() == 'q':
                    print("⚠️  退出当前录入")
                    return

                # 空值或0
                if not net_inflow_input:
                    net_inflow = 0
                    net_inflow_m = 0
                    break

                # 尝试解析数字（支持千分位和括号）
                try:
                    net_inflow_m = self.parse_number(net_inflow_input)
                    net_inflow = int(net_inflow_m * 1_000_000)  # 转换为完整数字
                    break
                except ValueError as e:
                    print(f"    ❌ 无效输入: {e}")

            # 2. 录入持仓总量
            holdings = 0
            if asset_type == 'BTC':
                while True:
                    holdings_input = input(f"    BTC持仓总量: ").strip()

                    if holdings_input.lower() == 'q':
                        print("⚠️  退出当前录入")
                        return

                    if not holdings_input:
                        holdings = 0
                        break

                    try:
                        holdings = self.parse_number(holdings_input)
                        break
                    except ValueError as e:
                        print(f"    ❌ 无效输入: {e}")

            elif asset_type == 'ETH':
                while True:
                    holdings_input = input(f"    ETH持仓总量: ").strip()

                    if holdings_input.lower() == 'q':
                        print("⚠️  退出当前录入")
                        return

                    if not holdings_input:
                        holdings = 0
                        break

                    try:
                        holdings = self.parse_number(holdings_input)
                        break
                    except ValueError as e:
                        print(f"    ❌ 无效输入: {e}")

            # 添加到数据列表
            data_row = {
                'Date': self.trade_date,
                'Ticker': ticker,
                'NetInflow': net_inflow
            }

            if asset_type == 'BTC':
                data_row['BTC_Holdings'] = holdings
            elif asset_type == 'ETH':
                data_row['ETH_Holdings'] = holdings

            self.data.append(data_row)

            # 显示转换后的值
            if net_inflow != 0:
                print(f"    ✓ 净流入: {net_inflow_m:,.1f}M = ${net_inflow:,}")
            else:
                print(f"    ✓ 净流入: 0")

            if holdings != 0:
                print(f"    ✓ 持仓: {holdings:,.2f} {asset_type}")
            else:
                print(f"    ✓ 持仓: 0")
            print()

    def show_summary(self):
        """显示汇总"""
        if not self.data:
            print("\n⚠️  没有录入任何数据")
            return

        print(f"\n{'=' * 80}")
        print("  数据录入汇总")
        print(f"{'=' * 80}\n")

        print(f"交易日期: {self.trade_date}")
        print(f"记录数量: {len(self.data)} 条")

        # 计算总净流入
        total_inflow = sum(item['NetInflow'] for item in self.data)
        total_inflow_m = total_inflow / 1_000_000
        print(f"总净流入: {total_inflow_m:,.1f}M USD")

        # 计算总持仓
        if 'BTC_Holdings' in self.data[0]:
            total_btc = sum(item.get('BTC_Holdings', 0) for item in self.data)
            print(f"BTC总持仓: {total_btc:,.2f} BTC")
        elif 'ETH_Holdings' in self.data[0]:
            total_eth = sum(item.get('ETH_Holdings', 0) for item in self.data)
            print(f"ETH总持仓: {total_eth:,.2f} ETH")

        print()

        # 显示前10条记录
        print("数据预览 (前10条):")
        if 'BTC_Holdings' in self.data[0]:
            print(f"{'Ticker':<10} {'NetInflow(M)':<15} {'BTC Holdings':<20}")
        elif 'ETH_Holdings' in self.data[0]:
            print(f"{'Ticker':<10} {'NetInflow(M)':<15} {'ETH Holdings':<20}")
        else:
            print(f"{'Ticker':<10} {'NetInflow(M)':<15}")
        print("-" * 50)

        for item in self.data[:10]:
            ticker = item['Ticker']
            net_inflow = item['NetInflow']
            net_inflow_m = net_inflow / 1_000_000

            holdings_str = ""
            if 'BTC_Holdings' in item:
                holdings = item['BTC_Holdings']
                holdings_str = f"{holdings:>19,.2f}"
            elif 'ETH_Holdings' in item:
                holdings = item['ETH_Holdings']
                holdings_str = f"{holdings:>19,.2f}"

            if holdings_str:
                print(f"{ticker:<10} {net_inflow_m:>14,.1f} {holdings_str}")
            else:
                print(f"{ticker:<10} {net_inflow_m:>14,.1f}")

        if len(self.data) > 10:
            print(f"... 还有 {len(self.data) - 10} 条记录")
        print()

    def save_to_csv(self, filename: str = None):
        """保存为CSV文件"""
        if not self.data:
            print("⚠️  没有数据可保存")
            return None

        # 生成文件名
        if not filename:
            if self.asset_type == 'BTC':
                filename = f'btc_etf_{self.trade_date}.csv'
            elif self.asset_type == 'ETH':
                filename = f'eth_etf_{self.trade_date}.csv'
            else:
                filename = f'etf_data_{self.trade_date}.csv'

        # 保存CSV
        try:
            # 根据数据类型确定CSV字段
            fieldnames = ['Date', 'Ticker', 'NetInflow']
            if 'BTC_Holdings' in self.data[0]:
                fieldnames.append('BTC_Holdings')
            elif 'ETH_Holdings' in self.data[0]:
                fieldnames.append('ETH_Holdings')

            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.data)

            print(f"✅ 数据已保存到: {filename}")

            # 显示文件绝对路径
            abs_path = os.path.abspath(filename)
            print(f"   完整路径: {abs_path}")

            return filename

        except Exception as e:
            print(f"❌ 保存失败: {e}")
            return None

    def confirm_import(self, csv_file: str) -> bool:
        """确认是否立即导入"""
        print(f"\n{'=' * 80}")
        print("是否立即导入到数据库？")
        print(f"{'=' * 80}\n")

        print("导入命令:")
        print(f"  python3 import_data.py {csv_file}")
        print()

        choice = input("是否立即执行导入？(y/n): ").strip().lower()
        return choice == 'y'

    def run_import(self, csv_file: str):
        """执行导入"""
        from pathlib import Path

        # 获取项目根目录和导入脚本路径
        script_dir = Path(__file__).parent
        project_root = script_dir.parent.parent
        import_script = script_dir / 'import_data.py'

        if not import_script.exists():
            print("❌ 导入脚本不存在，请手动运行:")
            print(f"   cd {project_root}")
            print(f"   python3 scripts/etf/import_data.py --file {csv_file} --asset-type {self.asset_type}")
            return

        print(f"\n开始导入数据...")
        print("-" * 80)

        # 切换到项目根目录执行导入（因为import_data.py需要读取config.yaml）
        csv_abs_path = (script_dir / csv_file).absolute()
        asset_type = self.asset_type if self.asset_type else 'BTC'

        import_cmd = f"cd {project_root} && python3 scripts/etf/import_data.py --action import --file {csv_abs_path} --asset-type {asset_type}"
        os.system(import_cmd)

    def run(self):
        """主流程"""
        self.clear_screen()
        self.print_header()

        # 选择资产类型
        choice = self.select_asset_type()

        # 输入日期
        self.trade_date = self.input_date()

        # 录入数据
        if choice == 'BTC' or choice == 'BOTH':
            self.asset_type = 'BTC'
            self.input_etf_data(self.BTC_ETFS, 'BTC')

        if choice == 'ETH' or choice == 'BOTH':
            self.asset_type = 'ETH'
            self.input_etf_data(self.ETH_ETFS, 'ETH')

        # 显示汇总
        self.show_summary()

        # 保存文件
        if self.data:
            csv_file = self.save_to_csv()

            if csv_file:
                # 询问是否导入
                if self.confirm_import(csv_file):
                    self.run_import(csv_file)
                else:
                    print("\n✅ 数据已保存，稍后可手动导入")
                    print(f"   导入命令: python3 import_data.py {csv_file}")

        print(f"\n{'=' * 80}")
        print("  录入完成！")
        print(f"{'=' * 80}\n")


def main():
    """主函数"""
    try:
        tool = ETFInteractiveInput()
        tool.run()
    except KeyboardInterrupt:
        print("\n\n⚠️  用户取消操作")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
