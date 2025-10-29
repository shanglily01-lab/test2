"""
加密货币 ETF 数据采集器
采集 Bitcoin ETF 和 Ethereum ETF 的每日资金流向数据

数据来源：
1. sosovalue.com API (推荐) - 实时ETF资金流向
2. farside.co.uk (备选) - Farside Investors ETF数据
3. 手动导入 (CSV/Excel)
"""

import requests
import json
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import List, Dict, Optional
import time


class CryptoETFCollector:
    """加密货币 ETF 数据采集器"""

    def __init__(self, db_service=None):
        """
        初始化采集器

        Args:
            db_service: 数据库服务实例
        """
        self.db = db_service

        # API 配置
        self.sosovalue_api = 'https://open-api.sosovalue.com/api'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }

    def fetch_sosovalue_data(self, asset_type: str = 'BTC', days: int = 1) -> List[Dict]:
        """
        从 SoSoValue API 获取 ETF 数据

        Args:
            asset_type: 资产类型 ('BTC' 或 'ETH')
            days: 获取天数

        Returns:
            ETF 数据列表
        """
        try:
            # SoSoValue API 端点
            # BTC ETF: /etf/spot-bitcoin-etf/flows
            # ETH ETF: /etf/spot-ethereum-etf/flows
            if asset_type == 'BTC':
                endpoint = f'{self.sosovalue_api}/etf/spot-bitcoin-etf/flows'
            else:
                endpoint = f'{self.sosovalue_api}/etf/spot-ethereum-etf/flows'

            params = {
                'days': days
            }

            print(f"📊 获取 {asset_type} ETF 数据 (最近 {days} 天)...")
            response = requests.get(endpoint, headers=self.headers, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json()
                flows = data.get('data', [])
                print(f"  ✅ 成功获取 {len(flows)} 条数据")
                return flows
            else:
                print(f"  ❌ API 返回错误: HTTP {response.status_code}")
                return []

        except Exception as e:
            print(f"  ❌ 获取数据失败: {e}")
            return []

    def fetch_farside_data(self, asset_type: str = 'BTC') -> List[Dict]:
        """
        从 Farside Investors 网站抓取数据 (备选方案)

        Args:
            asset_type: 资产类型

        Returns:
            ETF 数据列表
        """
        try:
            # Farside 提供 CSV 格式的数据
            if asset_type == 'BTC':
                url = 'https://farside.co.uk/btc/'
            else:
                url = 'https://farside.co.uk/eth/'

            print(f"📊 从 Farside 获取 {asset_type} ETF 数据...")
            response = requests.get(url, headers=self.headers, timeout=30)

            if response.status_code == 200:
                # 这里需要解析 HTML 或 CSV
                # 实际实现需要使用 BeautifulSoup 或 pandas
                print(f"  ℹ️  Farside 数据需要 HTML 解析，建议使用 SoSoValue API")
                return []
            else:
                print(f"  ❌ 无法访问 Farside: HTTP {response.status_code}")
                return []

        except Exception as e:
            print(f"  ❌ 获取 Farside 数据失败: {e}")
            return []

    def fetch_alternative_api(self, asset_type: str = 'BTC') -> List[Dict]:
        """
        从备选 API 获取数据
        使用公开的 CoinGlass API

        Args:
            asset_type: 资产类型

        Returns:
            ETF 数据列表
        """
        try:
            # CoinGlass ETF API (免费，无需API key)
            url = 'https://open-api.coinglass.com/public/v2/etf_flows'

            params = {
                'symbol': asset_type.upper(),
                'interval': '1d'
            }

            print(f"📊 从 CoinGlass 获取 {asset_type} ETF 数据...")
            response = requests.get(url, headers=self.headers, params=params, timeout=30)

            if response.status_code == 200:
                data = response.json()
                if data.get('success'):
                    flows = data.get('data', [])
                    print(f"  ✅ 成功获取 {len(flows)} 条数据")
                    return flows
                else:
                    print(f"  ❌ API 返回失败: {data.get('msg')}")
                    return []
            else:
                print(f"  ❌ API 返回错误: HTTP {response.status_code}")
                return []

        except Exception as e:
            print(f"  ❌ 获取数据失败: {e}")
            return []

    def save_etf_flow(self, etf_data: Dict) -> bool:
        """
        保存单条 ETF 资金流向数据

        Args:
            etf_data: ETF 数据字典

        Returns:
            是否成功
        """
        if not self.db:
            print("  ⚠️  数据库未连接")
            return False

        try:
            # 查找 ETF 产品 ID
            cursor = self.db.get_cursor()
            cursor.execute(
                "SELECT id FROM crypto_etf_products WHERE ticker = %s",
                (etf_data['ticker'],)
            )
            result = cursor.fetchone()

            if not result:
                print(f"  ⚠️  未找到 ETF: {etf_data['ticker']}")
                return False

            etf_id = result[0]

            # 插入或更新数据
            insert_sql = """
            INSERT INTO crypto_etf_flows
            (etf_id, ticker, trade_date, net_inflow, gross_inflow, gross_outflow,
             aum, btc_holdings, eth_holdings, shares_outstanding, nav, close_price, volume, data_source)
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            """

            cursor.execute(insert_sql, (
                etf_id,
                etf_data['ticker'],
                etf_data['trade_date'],
                etf_data.get('net_inflow', 0),
                etf_data.get('gross_inflow', 0),
                etf_data.get('gross_outflow', 0),
                etf_data.get('aum'),
                etf_data.get('btc_holdings'),
                etf_data.get('eth_holdings'),
                etf_data.get('shares_outstanding'),
                etf_data.get('nav'),
                etf_data.get('close_price'),
                etf_data.get('volume'),
                etf_data.get('data_source', 'api')
            ))

            self.db.conn.commit()
            return True

        except Exception as e:
            print(f"  ❌ 保存数据失败: {e}")
            if self.db:
                self.db.conn.rollback()
            return False

    def calculate_daily_summary(self, trade_date: date, asset_type: str) -> bool:
        """
        计算并保存每日汇总数据

        Args:
            trade_date: 交易日期
            asset_type: 资产类型

        Returns:
            是否成功
        """
        if not self.db:
            return False

        try:
            cursor = self.db.get_cursor()

            # 计算汇总数据
            query = """
            SELECT
                SUM(f.net_inflow) as total_net_inflow,
                SUM(f.gross_inflow) as total_gross_inflow,
                SUM(f.gross_outflow) as total_gross_outflow,
                SUM(f.aum) as total_aum,
                SUM(CASE WHEN p.asset_type = 'BTC' THEN f.btc_holdings ELSE f.eth_holdings END) as total_holdings,
                COUNT(*) as etf_count,
                SUM(CASE WHEN f.net_inflow > 0 THEN 1 ELSE 0 END) as inflow_count,
                SUM(CASE WHEN f.net_inflow < 0 THEN 1 ELSE 0 END) as outflow_count,
                (SELECT ticker FROM crypto_etf_flows WHERE trade_date = %s AND net_inflow > 0 ORDER BY net_inflow DESC LIMIT 1) as top_inflow_ticker,
                (SELECT MAX(net_inflow) FROM crypto_etf_flows WHERE trade_date = %s AND net_inflow > 0) as top_inflow_amount,
                (SELECT ticker FROM crypto_etf_flows WHERE trade_date = %s AND net_inflow < 0 ORDER BY net_inflow ASC LIMIT 1) as top_outflow_ticker,
                (SELECT MIN(net_inflow) FROM crypto_etf_flows WHERE trade_date = %s AND net_inflow < 0) as top_outflow_amount
            FROM crypto_etf_flows f
            JOIN crypto_etf_products p ON f.etf_id = p.id
            WHERE f.trade_date = %s AND p.asset_type = %s
            """

            cursor.execute(query, (trade_date, trade_date, trade_date, trade_date, trade_date, asset_type))
            result = cursor.fetchone()

            if result:
                # 插入或更新汇总数据
                insert_sql = """
                INSERT INTO crypto_etf_daily_summary
                (trade_date, asset_type, total_net_inflow, total_gross_inflow, total_gross_outflow,
                 total_aum, total_holdings, etf_count, inflow_count, outflow_count,
                 top_inflow_ticker, top_inflow_amount, top_outflow_ticker, top_outflow_amount)
                VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    total_net_inflow = VALUES(total_net_inflow),
                    total_gross_inflow = VALUES(total_gross_inflow),
                    total_gross_outflow = VALUES(total_gross_outflow),
                    total_aum = VALUES(total_aum),
                    total_holdings = VALUES(total_holdings),
                    etf_count = VALUES(etf_count),
                    inflow_count = VALUES(inflow_count),
                    outflow_count = VALUES(outflow_count),
                    top_inflow_ticker = VALUES(top_inflow_ticker),
                    top_inflow_amount = VALUES(top_inflow_amount),
                    top_outflow_ticker = VALUES(top_outflow_ticker),
                    top_outflow_amount = VALUES(top_outflow_amount),
                    updated_at = CURRENT_TIMESTAMP
                """

                cursor.execute(insert_sql, (
                    trade_date, asset_type,
                    result[0] or 0,  # total_net_inflow
                    result[1] or 0,  # total_gross_inflow
                    result[2] or 0,  # total_gross_outflow
                    result[3],       # total_aum
                    result[4],       # total_holdings
                    result[5] or 0,  # etf_count
                    result[6] or 0,  # inflow_count
                    result[7] or 0,  # outflow_count
                    result[8],       # top_inflow_ticker
                    result[9],       # top_inflow_amount
                    result[10],      # top_outflow_ticker
                    result[11]       # top_outflow_amount
                ))

                self.db.conn.commit()
                return True

        except Exception as e:
            print(f"  ❌ 计算汇总失败: {e}")
            if self.db:
                self.db.conn.rollback()
            return False

    def calculate_sentiment(self, trade_date: date, asset_type: str) -> bool:
        """
        计算市场情绪指标

        Args:
            trade_date: 交易日期
            asset_type: 资产类型

        Returns:
            是否成功
        """
        if not self.db:
            return False

        try:
            cursor = self.db.get_cursor()

            # 获取最近的数据
            query = """
            SELECT
                trade_date,
                total_net_inflow
            FROM crypto_etf_daily_summary
            WHERE asset_type = %s
              AND trade_date <= %s
            ORDER BY trade_date DESC
            LIMIT 20
            """

            cursor.execute(query, (asset_type, trade_date))
            rows = cursor.fetchall()

            if not rows:
                return False

            # 计算指标
            current_inflow = rows[0][1] or 0
            flows_5day = [r[1] for r in rows[:5] if r[1] is not None]
            flows_10day = [r[1] for r in rows[:10] if r[1] is not None]
            flows_20day = [r[1] for r in rows[:20] if r[1] is not None]

            ma_5day = sum(flows_5day) / len(flows_5day) if flows_5day else 0
            ma_10day = sum(flows_10day) / len(flows_10day) if flows_10day else 0
            ma_20day = sum(flows_20day) / len(flows_20day) if flows_20day else 0

            # 计算连续流入/流出天数
            consecutive_inflow = 0
            consecutive_outflow = 0
            for i, (_, inflow) in enumerate(rows):
                if inflow and inflow > 0:
                    if i == consecutive_inflow:
                        consecutive_inflow += 1
                    else:
                        break
                elif inflow and inflow < 0:
                    if i == consecutive_outflow:
                        consecutive_outflow += 1
                    else:
                        break

            # 计算情绪评分 (0-100)
            sentiment_score = 50  # 中性
            if ma_5day > 0:
                sentiment_score += min(25, ma_5day / 10000000 * 25)  # 流入加分
            else:
                sentiment_score -= min(25, abs(ma_5day) / 10000000 * 25)  # 流出减分

            if ma_5day > ma_10day > ma_20day:
                sentiment_score += 10  # 趋势向上
            elif ma_5day < ma_10day < ma_20day:
                sentiment_score -= 10  # 趋势向下

            sentiment_score = max(0, min(100, sentiment_score))

            # 判断趋势
            if sentiment_score >= 75:
                flow_trend = 'strong_inflow'
            elif sentiment_score >= 60:
                flow_trend = 'inflow'
            elif sentiment_score <= 25:
                flow_trend = 'strong_outflow'
            elif sentiment_score <= 40:
                flow_trend = 'outflow'
            else:
                flow_trend = 'neutral'

            # 计算MTD和YTD
            month_start = trade_date.replace(day=1)
            year_start = trade_date.replace(month=1, day=1)

            cursor.execute("""
                SELECT SUM(total_net_inflow)
                FROM crypto_etf_daily_summary
                WHERE asset_type = %s AND trade_date >= %s AND trade_date <= %s
            """, (asset_type, month_start, trade_date))
            mtd_inflow = cursor.fetchone()[0] or 0

            cursor.execute("""
                SELECT SUM(total_net_inflow)
                FROM crypto_etf_daily_summary
                WHERE asset_type = %s AND trade_date >= %s AND trade_date <= %s
            """, (asset_type, year_start, trade_date))
            ytd_inflow = cursor.fetchone()[0] or 0

            # 保存情绪数据
            insert_sql = """
            INSERT INTO crypto_etf_sentiment
            (trade_date, asset_type, sentiment_score, flow_trend,
             consecutive_inflow_days, consecutive_outflow_days,
             ma_5day_inflow, ma_10day_inflow, ma_20day_inflow,
             mtd_net_inflow, ytd_net_inflow)
            VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                sentiment_score = VALUES(sentiment_score),
                flow_trend = VALUES(flow_trend),
                consecutive_inflow_days = VALUES(consecutive_inflow_days),
                consecutive_outflow_days = VALUES(consecutive_outflow_days),
                ma_5day_inflow = VALUES(ma_5day_inflow),
                ma_10day_inflow = VALUES(ma_10day_inflow),
                ma_20day_inflow = VALUES(ma_20day_inflow),
                mtd_net_inflow = VALUES(mtd_net_inflow),
                ytd_net_inflow = VALUES(ytd_net_inflow),
                updated_at = CURRENT_TIMESTAMP
            """

            cursor.execute(insert_sql, (
                trade_date, asset_type, sentiment_score, flow_trend,
                consecutive_inflow, consecutive_outflow,
                ma_5day, ma_10day, ma_20day,
                mtd_inflow, ytd_inflow
            ))

            self.db.conn.commit()
            return True

        except Exception as e:
            print(f"  ❌ 计算情绪失败: {e}")
            if self.db:
                self.db.conn.rollback()
            return False

    def collect_daily_data(self, target_date: date = None, asset_types: List[str] = None) -> Dict:
        """
        采集每日 ETF 数据

        Args:
            target_date: 目标日期 (None = 今天)
            asset_types: 资产类型列表 (None = ['BTC', 'ETH'])

        Returns:
            采集结果统计
        """
        if target_date is None:
            target_date = date.today()

        if asset_types is None:
            asset_types = ['BTC', 'ETH']

        print("\n" + "=" * 80)
        print(f"ETF 数据采集 - {target_date}")
        print("=" * 80)

        total_saved = 0
        results = {}

        for asset_type in asset_types:
            print(f"\n处理 {asset_type} ETF...")

            # 尝试从不同数据源获取
            flows = self.fetch_sosovalue_data(asset_type, days=1)

            if not flows:
                flows = self.fetch_alternative_api(asset_type)

            if not flows:
                print(f"  ⚠️  无法获取 {asset_type} ETF 数据")
                results[asset_type] = {'saved': 0, 'failed': 0}
                continue

            # 保存数据
            saved = 0
            failed = 0

            for flow in flows:
                if self.save_etf_flow(flow):
                    saved += 1
                else:
                    failed += 1

            # 计算汇总和情绪
            self.calculate_daily_summary(target_date, asset_type)
            self.calculate_sentiment(target_date, asset_type)

            total_saved += saved
            results[asset_type] = {'saved': saved, 'failed': failed}

            print(f"  ✅ 保存: {saved} 条, 失败: {failed} 条")

        print("\n" + "=" * 80)
        print(f"采集完成! 总共保存 {total_saved} 条数据")
        print("=" * 80 + "\n")

        return results


# 独立运行示例
if __name__ == '__main__':
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from app.database.db_service import DatabaseService
    import yaml

    # 加载配置（使用绝对路径）
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / 'config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 初始化数据库
    db_service = DatabaseService(config['database'])

    # 创建采集器
    collector = CryptoETFCollector(db_service)

    # 采集今日数据
    results = collector.collect_daily_data()

    print("\n采集结果:", results)
