#!/usr/bin/env python3
"""
ETF 数据导入工具
支持从 CSV/Excel 文件导入 ETF 资金流向数据

数据格式示例 (CSV):
Date,Ticker,Provider,NetInflow,AUM,Holdings,NAV,Close,Volume
2024-10-22,IBIT,BlackRock,125000000,35000000000,365000,35.50,35.48,85000000
2024-10-22,FBTC,Fidelity,89000000,28000000000,292000,32.10,32.08,62000000
...
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent  # scripts/etf -> scripts -> crypto-analyzer
sys.path.insert(0, str(project_root))

import csv
import pandas as pd
from datetime import datetime, date
from decimal import Decimal
from app.database.db_service import DatabaseService
import yaml


class ETFDataImporter:
    """ETF 数据导入器"""

    def __init__(self, db_service):
        """
        初始化导入器

        Args:
            db_service: 数据库服务实例
        """
        self.db = db_service
        self.cursor = db_service.get_cursor()

    def import_from_csv(self, csv_file: str, asset_type: str = 'BTC') -> int:
        """
        从 CSV 文件导入数据

        Args:
            csv_file: CSV 文件路径
            asset_type: 资产类型

        Returns:
            导入的记录数
        """
        print(f"\n📊 从 CSV 导入 {asset_type} ETF 数据: {csv_file}")
        print("=" * 80)

        imported = 0
        errors = 0

        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)

                for row in reader:
                    try:
                        # 解析数据
                        trade_date = self._parse_date(row.get('Date') or row.get('date'))
                        ticker = row.get('Ticker') or row.get('ticker')

                        if not trade_date or not ticker:
                            print(f"  ⚠️  跳过无效行: {row}")
                            errors += 1
                            continue

                        # 查找 ETF ID
                        self.cursor.execute(
                            "SELECT id, asset_type FROM crypto_etf_products WHERE ticker = %s",
                            (ticker,)
                        )
                        result = self.cursor.fetchone()

                        if not result:
                            print(f"  ⚠️  未找到 ETF: {ticker}")
                            errors += 1
                            continue

                        etf_id, db_asset_type = result

                        # 插入数据
                        insert_sql = """
                        INSERT INTO crypto_etf_flows
                        (etf_id, ticker, trade_date, net_inflow, gross_inflow, gross_outflow,
                         aum, btc_holdings, eth_holdings, nav, close_price, volume, data_source)
                        VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'manual')
                        ON DUPLICATE KEY UPDATE
                            net_inflow = VALUES(net_inflow),
                            gross_inflow = VALUES(gross_inflow),
                            gross_outflow = VALUES(gross_outflow),
                            aum = VALUES(aum),
                            btc_holdings = VALUES(btc_holdings),
                            eth_holdings = VALUES(eth_holdings),
                            nav = VALUES(nav),
                            close_price = VALUES(close_price),
                            volume = VALUES(volume),
                            updated_at = CURRENT_TIMESTAMP
                        """

                        # 解析持仓量（支持多种字段名格式）
                        btc_holdings = None
                        eth_holdings = None
                        if db_asset_type == 'BTC':
                            btc_holdings = self._parse_number(
                                row.get('BTC_Holdings') or
                                row.get('BTCHoldings') or
                                row.get('btc_holdings')
                            )
                        elif db_asset_type == 'ETH':
                            eth_holdings = self._parse_number(
                                row.get('ETH_Holdings') or
                                row.get('ETHHoldings') or
                                row.get('eth_holdings')
                            )

                        self.cursor.execute(insert_sql, (
                            etf_id,
                            ticker,
                            trade_date,
                            self._parse_number(row.get('NetInflow') or row.get('net_inflow')),
                            self._parse_number(row.get('GrossInflow') or row.get('gross_inflow')),
                            self._parse_number(row.get('GrossOutflow') or row.get('gross_outflow')),
                            self._parse_number(row.get('AUM') or row.get('aum')),
                            btc_holdings,
                            eth_holdings,
                            self._parse_number(row.get('NAV') or row.get('nav')),
                            self._parse_number(row.get('Close') or row.get('close_price')),
                            self._parse_number(row.get('Volume') or row.get('volume'))
                        ))

                        imported += 1

                        # 显示导入详情
                        holdings_info = ""
                        if btc_holdings:
                            holdings_info = f", BTC持仓: {btc_holdings}"
                        elif eth_holdings:
                            holdings_info = f", ETH持仓: {eth_holdings}"

                        print(f"  ✓ {ticker} ({trade_date}): 净流入 ${self._parse_number(row.get('NetInflow') or row.get('net_inflow'))}{holdings_info}")

                        if imported % 10 == 0:
                            print(f"  已导入 {imported} 条...")

                    except Exception as e:
                        print(f"  ❌ 导入行失败: {row} - {e}")
                        errors += 1

                self.db.conn.commit()

        except Exception as e:
            print(f"❌ 导入失败: {e}")
            self.db.conn.rollback()
            return 0

        print(f"\n✅ 导入完成! 成功: {imported} 条, 失败: {errors} 条")
        print("=" * 80)
        return imported

    def import_from_excel(self, excel_file: str, asset_type: str = 'BTC', sheet_name: str = None) -> int:
        """
        从 Excel 文件导入数据

        Args:
            excel_file: Excel 文件路径
            asset_type: 资产类型
            sheet_name: 工作表名称 (None = 第一个)

        Returns:
            导入的记录数
        """
        print(f"\n📊 从 Excel 导入 {asset_type} ETF 数据: {excel_file}")
        print("=" * 80)

        try:
            # 读取 Excel
            df = pd.read_excel(excel_file, sheet_name=sheet_name or 0)

            # 转换为 CSV 格式
            temp_csv = '/tmp/etf_temp.csv'
            df.to_csv(temp_csv, index=False)

            # 使用 CSV 导入
            result = self.import_from_csv(temp_csv, asset_type)

            # 清理临时文件
            Path(temp_csv).unlink(missing_ok=True)

            return result

        except Exception as e:
            print(f"❌ 读取 Excel 失败: {e}")
            return 0

    def _parse_date(self, date_str: str) -> date:
        """解析日期字符串"""
        if not date_str:
            return None

        try:
            # 尝试多种日期格式
            for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d']:
                try:
                    return datetime.strptime(str(date_str).strip(), fmt).date()
                except:
                    continue
            return None
        except:
            return None

    def _parse_number(self, num_str: str) -> Decimal:
        """解析数字字符串"""
        if not num_str:
            return None

        try:
            # 移除逗号和货币符号
            cleaned = str(num_str).replace(',', '').replace('$', '').strip()
            if cleaned:
                return Decimal(cleaned)
            return None
        except:
            return None

    def generate_template_csv(self, output_file: str = 'etf_import_template.csv'):
        """
        生成导入模板 CSV

        Args:
            output_file: 输出文件路径
        """
        template_data = [
            {
                'Date': '2024-10-22',
                'Ticker': 'IBIT',
                'NetInflow': '125000000',
                'GrossInflow': '150000000',
                'GrossOutflow': '25000000',
                'AUM': '35000000000',
                'BTCHoldings': '365000',
                'NAV': '35.50',
                'Close': '35.48',
                'Volume': '85000000'
            },
            {
                'Date': '2024-10-22',
                'Ticker': 'FBTC',
                'NetInflow': '89000000',
                'GrossInflow': '105000000',
                'GrossOutflow': '16000000',
                'AUM': '28000000000',
                'BTCHoldings': '292000',
                'NAV': '32.10',
                'Close': '32.08',
                'Volume': '62000000'
            }
        ]

        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=template_data[0].keys())
            writer.writeheader()
            writer.writerows(template_data)

        print(f"✅ 模板已生成: {output_file}")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='ETF 数据导入工具')
    parser.add_argument('--action', choices=['import', 'template'], default='import',
                        help='操作类型')
    parser.add_argument('--file', type=str,
                        help='导入文件路径 (CSV 或 Excel)')
    parser.add_argument('--asset-type', choices=['BTC', 'ETH'], default='BTC',
                        help='资产类型')
    parser.add_argument('--sheet', type=str,
                        help='Excel 工作表名称 (可选)')
    parser.add_argument('--template', type=str, default='etf_import_template.csv',
                        help='模板文件路径')

    args = parser.parse_args()

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 适配配置结构: database.mysql
    if 'mysql' in config['database']:
        db_config = config['database']['mysql']
    else:
        db_config = config['database']

    # 初始化数据库 (使用 pymysql 直接连接)
    import pymysql
    try:
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        print(f"\n请检查:")
        print(f"  1. MySQL 服务是否运行")
        print(f"  2. config.yaml 中的数据库配置是否正确")
        print(f"  3. 数据库 '{db_config['database']}' 是否存在")
        return

    # 创建简化的数据库服务对象
    class SimpleDBService:
        def __init__(self, connection):
            self.conn = connection
        def get_cursor(self):
            return self.conn.cursor()

    db_service = SimpleDBService(conn)
    importer = ETFDataImporter(db_service)

    if args.action == 'template':
        # 生成模板
        importer.generate_template_csv(args.template)

    elif args.action == 'import':
        if not args.file:
            print("❌ 请指定导入文件: --file <path>")
            return

        file_path = Path(args.file)
        if not file_path.exists():
            print(f"❌ 文件不存在: {args.file}")
            return

        # 根据文件类型导入
        if file_path.suffix.lower() == '.csv':
            importer.import_from_csv(str(file_path), args.asset_type)
        elif file_path.suffix.lower() in ['.xlsx', '.xls']:
            importer.import_from_excel(str(file_path), args.asset_type, args.sheet)
        else:
            print(f"❌ 不支持的文件类型: {file_path.suffix}")


if __name__ == '__main__':
    main()
