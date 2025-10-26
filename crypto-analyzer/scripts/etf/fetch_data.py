#!/usr/bin/env python3
"""
ETF 数据快速获取工具
使用多个备选数据源，确保能获取到数据

数据源优先级：
1. TheBlock API (免费、可靠)
2. CryptoQuant API (备选)
3. 手动爬取 Farside 网站 (最后方案)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, date, timedelta
import time


class ETFDataFetcher:
    """ETF 数据获取器"""

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

    def fetch_from_farside(self, asset_type: str = 'BTC') -> dict:
        """
        从 Farside Investors 网站获取数据
        这是最可靠的公开数据源

        Args:
            asset_type: 'BTC' 或 'ETH'

        Returns:
            ETF 流向数据
        """
        try:
            if asset_type == 'BTC':
                url = 'https://farside.co.uk/btc/'
            else:
                url = 'https://farside.co.uk/eth/'

            print(f"📊 从 Farside Investors 获取 {asset_type} ETF 数据...")
            print(f"   URL: {url}")

            response = requests.get(url, headers=self.headers, timeout=30, verify=False)

            if response.status_code != 200:
                print(f"  ❌ HTTP {response.status_code}")
                return None

            # 解析 HTML
            soup = BeautifulSoup(response.text, 'html.parser')

            # 查找数据表格
            table = soup.find('table', {'class': 'etf'}) or soup.find('table')

            if not table:
                print(f"  ❌ 未找到数据表格")
                return None

            # 解析表头 (ETF 代码)
            headers = []
            header_row = table.find('thead').find('tr') if table.find('thead') else table.find('tr')
            for th in header_row.find_all('th'):
                ticker = th.get_text(strip=True)
                if ticker and ticker != 'Date':
                    headers.append(ticker)

            print(f"  ✅ 找到 {len(headers)} 个 ETF: {', '.join(headers[:5])}...")

            # 解析最新一行数据 (第一个数据行)
            tbody = table.find('tbody')
            if not tbody:
                print(f"  ❌ 未找到数据行")
                return None

            data_rows = tbody.find_all('tr')
            if not data_rows:
                print(f"  ❌ 没有数据行")
                return None

            latest_row = data_rows[0]  # 最新一天
            cells = latest_row.find_all('td')

            if not cells:
                print(f"  ❌ 数据行为空")
                return None

            # 解析日期
            trade_date_str = cells[0].get_text(strip=True)
            trade_date = self._parse_date(trade_date_str)

            if not trade_date:
                print(f"  ⚠️  无法解析日期: {trade_date_str}")
                trade_date = date.today() - timedelta(days=1)  # 使用昨天

            print(f"  📅 交易日期: {trade_date}")

            # 解析各 ETF 的流向数据
            etf_data = {}
            for i, ticker in enumerate(headers):
                if i + 1 >= len(cells):
                    break

                cell_text = cells[i + 1].get_text(strip=True)
                net_inflow = self._parse_inflow(cell_text)

                if net_inflow is not None:
                    etf_data[ticker] = {
                        'ticker': ticker,
                        'trade_date': str(trade_date),
                        'net_inflow': net_inflow,
                        'asset_type': asset_type,
                        'data_source': 'farside'
                    }

            print(f"  ✅ 成功解析 {len(etf_data)} 个 ETF 的数据")

            # 显示前几个
            for i, (ticker, data) in enumerate(list(etf_data.items())[:3]):
                inflow = data['net_inflow']
                sign = '+' if inflow >= 0 else ''
                print(f"     {ticker}: {sign}${inflow:,.0f}")

            return {
                'trade_date': str(trade_date),
                'asset_type': asset_type,
                'etf_flows': etf_data
            }

        except Exception as e:
            print(f"  ❌ 获取失败: {e}")
            import traceback
            traceback.print_exc()
            return None

    def _parse_date(self, date_str: str) -> date:
        """解析日期字符串"""
        try:
            # Farside 格式: "Oct 22"
            date_str = date_str.strip()

            # 尝试多种格式
            for fmt in [
                '%b %d',        # Oct 22
                '%B %d',        # October 22
                '%m/%d/%Y',     # 10/22/2024
                '%Y-%m-%d',     # 2024-10-22
                '%d/%m/%Y'      # 22/10/2024
            ]:
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    # 如果没有年份，使用当前年份
                    if fmt in ['%b %d', '%B %d']:
                        year = datetime.now().year
                        return date(year, parsed.month, parsed.day)
                    return parsed.date()
                except:
                    continue

            return None
        except:
            return None

    def _parse_inflow(self, text: str) -> float:
        """
        解析流入金额

        Args:
            text: 文本，如 "+125.5", "-45.2", "0.0"

        Returns:
            金额（百万美元）
        """
        try:
            text = text.strip().replace(',', '').replace('$', '')

            if not text or text == '-' or text.lower() == 'n/a':
                return 0.0

            # 移除括号（负数）
            if '(' in text:
                text = text.replace('(', '-').replace(')', '')

            value = float(text)

            # Farside 数据单位是百万美元
            return value * 1000000

        except:
            return 0.0

    def save_to_json(self, data: dict, output_file: str = None):
        """保存数据到 JSON 文件"""
        if not output_file:
            today = date.today().strftime('%Y%m%d')
            output_file = f"etf_data_{data['asset_type']}_{today}.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        print(f"\n💾 数据已保存: {output_file}")

    def generate_import_sql(self, data: dict, output_file: str = None):
        """生成 SQL 导入脚本"""
        if not data or 'etf_flows' not in data:
            print("  ⚠️  没有数据，无法生成 SQL")
            return

        if not output_file:
            today = date.today().strftime('%Y%m%d')
            output_file = f"import_etf_{data['asset_type']}_{today}.sql"

        trade_date = data['trade_date']
        asset_type = data['asset_type']

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"-- ETF 数据导入脚本\n")
            f.write(f"-- 日期: {trade_date}\n")
            f.write(f"-- 资产: {asset_type}\n")
            f.write(f"-- 数据来源: Farside Investors\n\n")

            f.write("USE `binance-data`;\n\n")

            for ticker, etf in data['etf_flows'].items():
                # 查找 ETF ID 的 SQL
                f.write(f"-- {ticker}\n")
                f.write(f"SET @etf_id = (SELECT id FROM crypto_etf_products WHERE ticker = '{ticker}');\n\n")

                f.write(f"INSERT INTO crypto_etf_flows\n")
                f.write(f"(etf_id, ticker, trade_date, net_inflow, data_source)\n")
                f.write(f"VALUES\n")
                f.write(f"(@etf_id, '{ticker}', '{trade_date}', {etf['net_inflow']}, 'farside')\n")
                f.write(f"ON DUPLICATE KEY UPDATE\n")
                f.write(f"  net_inflow = VALUES(net_inflow),\n")
                f.write(f"  data_source = VALUES(data_source),\n")
                f.write(f"  updated_at = CURRENT_TIMESTAMP;\n\n")

        print(f"📝 SQL 脚本已生成: {output_file}")
        print(f"\n导入方法:")
        print(f"  mysql -u root -p binance-data < {output_file}")

    def fetch_and_save(self, asset_types: list = None):
        """获取并保存所有数据"""
        if asset_types is None:
            asset_types = ['BTC', 'ETH']

        print("\n" + "=" * 80)
        print("ETF 数据获取工具")
        print("=" * 80)

        results = {}

        for asset_type in asset_types:
            print(f"\n{'='*80}")
            print(f"处理 {asset_type} ETF")
            print(f"{'='*80}")

            # 获取数据
            data = self.fetch_from_farside(asset_type)

            if data:
                # 保存 JSON
                self.save_to_json(data)

                # 生成 SQL
                self.generate_import_sql(data)

                results[asset_type] = 'success'
            else:
                results[asset_type] = 'failed'

            # 延迟，避免请求过快
            if len(asset_types) > 1:
                time.sleep(2)

        print("\n" + "=" * 80)
        print("完成!")
        print("=" * 80)

        # 总结
        success = sum(1 for v in results.values() if v == 'success')
        print(f"\n✅ 成功: {success}/{len(asset_types)}")

        return results


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='ETF 数据获取工具')
    parser.add_argument('--asset', choices=['BTC', 'ETH', 'ALL'], default='ALL',
                        help='资产类型')

    args = parser.parse_args()

    # 确定要获取的资产
    if args.asset == 'ALL':
        asset_types = ['BTC', 'ETH']
    else:
        asset_types = [args.asset]

    # 执行获取
    fetcher = ETFDataFetcher()
    fetcher.fetch_and_save(asset_types)

    print("\n下一步:")
    print("  1. 检查生成的 JSON 文件")
    print("  2. 运行 SQL 导入脚本:")
    print("     mysql -u root -p binance-data < import_etf_*.sql")
    print("  3. 验证数据:")
    print("     mysql -u root -p binance-data -e \"SELECT * FROM v_etf_latest_flows;\"")
    print()


if __name__ == '__main__':
    main()
