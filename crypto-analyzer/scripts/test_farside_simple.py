#!/usr/bin/env python3
"""
简化版 Farside ETF 爬虫测试
不依赖项目的其他模块，独立运行
"""

def test_farside_scraper():
    """测试 Farside 网站爬取"""
    try:
        import requests
        from bs4 import BeautifulSoup
        from datetime import datetime
    except ImportError as e:
        print(f"❌ 缺少必要的库: {e}")
        print("请安装: pip install requests beautifulsoup4 lxml")
        return

    print("\n" + "=" * 80)
    print("Farside ETF 爬虫简化测试")
    print("=" * 80 + "\n")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    # 测试 BTC ETF
    print(">>> 测试 BTC ETF 数据爬取...")
    try:
        url = 'https://farside.co.uk/btc/'
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            print(f"✅ 成功访问 {url} (HTTP {response.status_code})")

            # 解析 HTML
            soup = BeautifulSoup(response.text, 'lxml')

            # 查找表格
            table = soup.find('table')
            if table:
                print(f"✅ 找到数据表格")

                # 解析表头
                headers_list = []
                header_row = table.find('thead')
                if header_row:
                    th_elements = header_row.find_all('th')
                    headers_list = [th.text.strip() for th in th_elements]
                else:
                    # 尝试第一行
                    first_row = table.find('tr')
                    if first_row:
                        headers_list = [td.text.strip() for td in first_row.find_all(['th', 'td'])]

                print(f"✅ 表头 ({len(headers_list)} 列): {', '.join(headers_list[:5])}...")

                # 解析数据行
                tbody = table.find('tbody')
                rows = tbody.find_all('tr') if tbody else table.find_all('tr')[1:]

                print(f"✅ 找到 {len(rows)} 行数据")

                # 显示最后一行（最新数据）
                if rows:
                    latest_row = rows[-1]
                    cells = latest_row.find_all(['td', 'th'])

                    print(f"\n最新数据 ({len(cells)} 列):")
                    print("-" * 60)

                    # 日期
                    date_str = cells[0].text.strip()
                    print(f"日期: {date_str}")

                    # 显示前5个ETF的数据
                    print(f"\n{'ETF':<15} {'资金流入':<20}")
                    print("-" * 35)
                    for i in range(1, min(6, len(cells))):
                        if i < len(headers_list):
                            ticker = headers_list[i]
                            value = cells[i].text.strip()
                            if ticker and ticker.lower() not in ['date', 'total', '总计']:
                                print(f"{ticker:<15} {value:<20}")

                    print(f"\n✅ BTC ETF 数据爬取测试成功")
                else:
                    print("❌ 未找到数据行")
            else:
                print("❌ 未找到表格")
        else:
            print(f"❌ 访问失败 (HTTP {response.status_code})")

    except Exception as e:
        print(f"❌ BTC ETF 爬取失败: {e}")
        import traceback
        traceback.print_exc()

    # 测试 ETH ETF
    print("\n\n>>> 测试 ETH ETF 数据爬取...")
    try:
        url = 'https://farside.co.uk/eth/'
        response = requests.get(url, headers=headers, timeout=30)

        if response.status_code == 200:
            print(f"✅ 成功访问 {url} (HTTP {response.status_code})")

            soup = BeautifulSoup(response.text, 'lxml')
            table = soup.find('table')

            if table:
                print(f"✅ 找到数据表格")

                # 简单统计
                tbody = table.find('tbody')
                rows = tbody.find_all('tr') if tbody else table.find_all('tr')[1:]
                print(f"✅ 找到 {len(rows)} 行数据")

                print(f"\n✅ ETH ETF 数据爬取测试成功")
            else:
                print("❌ 未找到表格")
        else:
            print(f"❌ 访问失败 (HTTP {response.status_code})")

    except Exception as e:
        print(f"❌ ETH ETF 爬取失败: {e}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80 + "\n")


if __name__ == '__main__':
    test_farside_scraper()
