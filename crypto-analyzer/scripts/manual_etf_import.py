#!/usr/bin/env python3
"""
手动 ETF 数据录入工具
支持从 CSV 文件或命令行直接输入 ETF 数据
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import csv
import yaml
from datetime import datetime, date
from decimal import Decimal
from app.database.db_service import DatabaseService


class ManualETFImporter:
    """手动 ETF 数据导入器"""

    def __init__(self, db_service):
        self.db = db_service

    def import_from_csv(self, csv_file: str) -> dict:
        """
        从 CSV 文件导入 ETF 数据

        CSV 格式要求:
        ticker,trade_date,net_inflow,aum,btc_holdings,eth_holdings,data_source
        IBIT,2025-01-27,125.5,50000,21000,,manual
        FBTC,2025-01-27,85.3,30000,15000,,manual

        Args:
            csv_file: CSV 文件路径

        Returns:
            导入结果统计
        """
        print(f"\n📊 从 CSV 导入 ETF 数据: {csv_file}")
        print("=" * 80)

        success = 0
        failed = 0
        errors = []

        try:
            with open(csv_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)

                for row in reader:
                    try:
                        etf_data = {
                            'ticker': row['ticker'].strip().upper(),
                            'trade_date': datetime.strptime(row['trade_date'], '%Y-%m-%d').date(),
                            'net_inflow': float(row.get('net_inflow', 0)),
                            'gross_inflow': float(row.get('gross_inflow', 0)) if row.get('gross_inflow') else None,
                            'gross_outflow': float(row.get('gross_outflow', 0)) if row.get('gross_outflow') else None,
                            'aum': float(row.get('aum')) if row.get('aum') else None,
                            'btc_holdings': float(row.get('btc_holdings')) if row.get('btc_holdings') else None,
                            'eth_holdings': float(row.get('eth_holdings')) if row.get('eth_holdings') else None,
                            'shares_outstanding': float(row.get('shares_outstanding')) if row.get('shares_outstanding') else None,
                            'nav': float(row.get('nav')) if row.get('nav') else None,
                            'close_price': float(row.get('close_price')) if row.get('close_price') else None,
                            'volume': float(row.get('volume')) if row.get('volume') else None,
                            'data_source': row.get('data_source', 'manual').strip()
                        }

                        if self.save_etf_flow(etf_data):
                            success += 1
                            print(f"  ✅ {etf_data['ticker']}: {etf_data['trade_date']} - 净流入 ${etf_data['net_inflow']:.2f}M")
                        else:
                            failed += 1
                            errors.append(f"{etf_data['ticker']} - 保存失败")

                    except Exception as e:
                        failed += 1
                        errors.append(f"行 {reader.line_num}: {e}")
                        print(f"  ❌ 行 {reader.line_num}: {e}")

        except FileNotFoundError:
            print(f"❌ 文件不存在: {csv_file}")
            return {'success': 0, 'failed': 0, 'errors': ['文件不存在']}
        except Exception as e:
            print(f"❌ 读取文件失败: {e}")
            return {'success': 0, 'failed': 0, 'errors': [str(e)]}

        print("=" * 80)
        print(f"导入完成: 成功 {success}, 失败 {failed}")
        if errors:
            print(f"\n错误详情:")
            for error in errors:
                print(f"  - {error}")

        return {'success': success, 'failed': failed, 'errors': errors}

    def import_single_etf(self, ticker: str, trade_date: str, net_inflow: float,
                         aum: float = None, holdings: float = None,
                         asset_type: str = 'BTC') -> bool:
        """
        手动输入单条 ETF 数据

        Args:
            ticker: ETF 代码 (如 IBIT, FBTC)
            trade_date: 交易日期 (YYYY-MM-DD)
            net_inflow: 净流入 (单位: 百万美元)
            aum: 资产管理规模 (单位: 百万美元)
            holdings: 持仓量 (BTC 数量或 ETH 数量)
            asset_type: 资产类型 ('BTC' 或 'ETH')

        Returns:
            是否成功
        """
        try:
            etf_data = {
                'ticker': ticker.strip().upper(),
                'trade_date': datetime.strptime(trade_date, '%Y-%m-%d').date(),
                'net_inflow': float(net_inflow),
                'aum': float(aum) if aum else None,
                'btc_holdings': float(holdings) if asset_type == 'BTC' and holdings else None,
                'eth_holdings': float(holdings) if asset_type == 'ETH' and holdings else None,
                'data_source': 'manual'
            }

            if self.save_etf_flow(etf_data):
                print(f"✅ 成功保存: {ticker} - {trade_date} - 净流入 ${net_inflow:.2f}M")
                return True
            else:
                print(f"❌ 保存失败: {ticker}")
                return False

        except Exception as e:
            print(f"❌ 错误: {e}")
            return False

    def save_etf_flow(self, etf_data: dict) -> bool:
        """
        保存 ETF 数据到数据库

        Args:
            etf_data: ETF 数据字典

        Returns:
            是否成功
        """
        try:
            session = self.db.get_session()

            # 查找 ETF 产品 ID
            from sqlalchemy import text
            result = session.execute(
                text("SELECT id FROM crypto_etf_products WHERE ticker = :ticker"),
                {'ticker': etf_data['ticker']}
            )
            row = result.fetchone()

            if not row:
                print(f"  ⚠️  警告: ETF 产品 {etf_data['ticker']} 不存在于数据库，请先添加产品信息")
                return False

            etf_id = row[0]

            # 插入或更新数据
            insert_sql = text("""
            INSERT INTO crypto_etf_flows
            (etf_id, ticker, trade_date, net_inflow, gross_inflow, gross_outflow,
             aum, btc_holdings, eth_holdings, shares_outstanding, nav, close_price, volume, data_source)
            VALUES
            (:etf_id, :ticker, :trade_date, :net_inflow, :gross_inflow, :gross_outflow,
             :aum, :btc_holdings, :eth_holdings, :shares_outstanding, :nav, :close_price, :volume, :data_source)
            ON DUPLICATE KEY UPDATE
                net_inflow = VALUES(net_inflow),
                gross_inflow = VALUES(gross_inflow),
                gross_outflow = VALUES(gross_outflow),
                aum = VALUES(aum),
                btc_holdings = VALUES(btc_holdings),
                eth_holdings = VALUES(eth_holdings),
                shares_outstanding = VALUES(shares_outstanding),
                nav = VALUES(nav),
                close_price = VALUES(close_price),
                volume = VALUES(volume),
                data_source = VALUES(data_source),
                updated_at = CURRENT_TIMESTAMP
            """)

            session.execute(insert_sql, {
                'etf_id': etf_id,
                'ticker': etf_data['ticker'],
                'trade_date': etf_data['trade_date'],
                'net_inflow': etf_data.get('net_inflow', 0),
                'gross_inflow': etf_data.get('gross_inflow'),
                'gross_outflow': etf_data.get('gross_outflow'),
                'aum': etf_data.get('aum'),
                'btc_holdings': etf_data.get('btc_holdings'),
                'eth_holdings': etf_data.get('eth_holdings'),
                'shares_outstanding': etf_data.get('shares_outstanding'),
                'nav': etf_data.get('nav'),
                'close_price': etf_data.get('close_price'),
                'volume': etf_data.get('volume'),
                'data_source': etf_data.get('data_source', 'manual')
            })

            session.commit()
            session.close()
            return True

        except Exception as e:
            print(f"  ❌ 保存失败: {e}")
            if session:
                session.rollback()
                session.close()
            return False

    def list_etf_products(self):
        """列出所有已注册的 ETF 产品"""
        try:
            session = self.db.get_session()
            from sqlalchemy import text

            result = session.execute(text("""
                SELECT ticker, name, issuer, asset_type, launch_date
                FROM crypto_etf_products
                ORDER BY asset_type, ticker
            """))

            rows = result.fetchall()

            if not rows:
                print("\n⚠️  数据库中没有 ETF 产品，请先添加产品信息")
                return

            print("\n📋 已注册的 ETF 产品:")
            print("=" * 80)
            print(f"{'代码':<8} {'名称':<30} {'发行商':<20} {'类型':<6} {'上市日期'}")
            print("-" * 80)

            for row in rows:
                ticker, name, issuer, asset_type, launch_date = row
                print(f"{ticker:<8} {name:<30} {issuer:<20} {asset_type:<6} {launch_date}")

            print("=" * 80)
            session.close()

        except Exception as e:
            print(f"❌ 查询失败: {e}")


def interactive_mode(importer):
    """交互式输入模式"""
    print("\n" + "=" * 80)
    print("手动 ETF 数据录入 - 交互模式")
    print("=" * 80)

    # 显示 ETF 产品列表
    importer.list_etf_products()

    print("\n请输入 ETF 数据 (输入 'q' 退出):")

    while True:
        try:
            print("\n" + "-" * 80)
            ticker = input("ETF 代码 (如 IBIT): ").strip().upper()
            if ticker == 'Q':
                break

            trade_date = input("交易日期 (YYYY-MM-DD): ").strip()
            net_inflow = float(input("净流入 (百万美元): ").strip())

            aum_input = input("AUM (百万美元, 回车跳过): ").strip()
            aum = float(aum_input) if aum_input else None

            holdings_input = input("持仓量 (BTC/ETH 数量, 回车跳过): ").strip()
            holdings = float(holdings_input) if holdings_input else None

            asset_type = input("资产类型 (BTC/ETH, 默认 BTC): ").strip().upper() or 'BTC'

            # 保存数据
            importer.import_single_etf(ticker, trade_date, net_inflow, aum, holdings, asset_type)

        except KeyboardInterrupt:
            print("\n\n退出录入")
            break
        except Exception as e:
            print(f"❌ 输入错误: {e}")


def main():
    """主函数"""
    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 初始化数据库
    db_service = DatabaseService(config)

    # 创建导入器
    importer = ManualETFImporter(db_service)

    # 检查命令行参数
    if len(sys.argv) > 1:
        if sys.argv[1] == '--csv' and len(sys.argv) > 2:
            # CSV 导入模式
            csv_file = sys.argv[2]
            result = importer.import_from_csv(csv_file)

        elif sys.argv[1] == '--list':
            # 列出 ETF 产品
            importer.list_etf_products()

        elif sys.argv[1] == '--single' and len(sys.argv) >= 5:
            # 单条数据导入
            ticker = sys.argv[2]
            trade_date = sys.argv[3]
            net_inflow = float(sys.argv[4])
            aum = float(sys.argv[5]) if len(sys.argv) > 5 else None
            holdings = float(sys.argv[6]) if len(sys.argv) > 6 else None
            asset_type = sys.argv[7] if len(sys.argv) > 7 else 'BTC'

            importer.import_single_etf(ticker, trade_date, net_inflow, aum, holdings, asset_type)
        else:
            print("用法:")
            print("  列出所有 ETF 产品:")
            print("    python scripts/manual_etf_import.py --list")
            print()
            print("  从 CSV 导入:")
            print("    python scripts/manual_etf_import.py --csv <csv文件路径>")
            print()
            print("  单条数据导入:")
            print("    python scripts/manual_etf_import.py --single <代码> <日期> <净流入> [AUM] [持仓量] [类型]")
            print("    例如: python scripts/manual_etf_import.py --single IBIT 2025-01-27 125.5 50000 21000 BTC")
            print()
            print("  交互模式:")
            print("    python scripts/manual_etf_import.py")
    else:
        # 交互模式
        interactive_mode(importer)


if __name__ == '__main__':
    main()