"""
企业金库监控 API
Corporate Treasury Monitoring API

提供企业 BTC 持仓、购买行为、融资信息、股价变动等数据
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import mysql.connector
from mysql.connector import pooling
import yaml
from pathlib import Path

router = APIRouter()

# 从 config.yaml 加载数据库配置
# 使用绝对路径，相对于项目根目录
project_root = Path(__file__).parent.parent.parent
config_path = project_root / "config.yaml"
connection_pool = None

try:
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        mysql_config = config.get('database', {}).get('mysql', {})

        db_config = {
            "host": mysql_config.get('host', 'localhost'),
            "port": mysql_config.get('port', 3306),
            "user": mysql_config.get('user', 'root'),
            "password": mysql_config.get('password', ''),
            "database": mysql_config.get('database', 'binance-data'),
            "pool_name": "corporate_treasury_pool",
            "pool_size": 5
        }

        connection_pool = pooling.MySQLConnectionPool(**db_config)
        print(f"✅ 企业金库监控数据库连接池创建成功: {db_config['database']}")
    else:
        print("⚠️  config.yaml 不存在，无法初始化数据库连接")
except Exception as e:
    print(f"❌ 企业金库监控数据库连接池创建失败: {e}")
    import traceback
    traceback.print_exc()
    connection_pool = None


def get_db_connection():
    """获取数据库连接"""
    if connection_pool is None:
        raise HTTPException(status_code=500, detail="数据库连接池未初始化")
    try:
        return connection_pool.get_connection()
    except mysql.connector.Error as e:
        raise HTTPException(status_code=500, detail=f"数据库连接失败: {str(e)}")


@router.get("/api/corporate-treasury/summary")
async def get_corporate_treasury_summary():
    """
    获取企业金库总览数据

    返回:
    - 总公司数量
    - 总 BTC 持仓
    - 总市值（美元）
    - 最近 30 天活跃公司数
    - Top 10 持仓公司
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # 获取当前 BTC 价格（从最近的持仓记录获取）
        cursor.execute("""
            SELECT average_price
            FROM corporate_treasury_purchases
            WHERE average_price > 0
            ORDER BY purchase_date DESC
            LIMIT 1
        """)
        btc_price_result = cursor.fetchone()
        current_btc_price = btc_price_result['average_price'] if btc_price_result else 100000

        # 统计数据
        cursor.execute("""
            SELECT
                COUNT(DISTINCT c.id) as total_companies,
                COALESCE(SUM(latest.cumulative_holdings), 0) as total_btc_holdings
            FROM corporate_treasury_companies c
            LEFT JOIN (
                SELECT
                    company_id,
                    cumulative_holdings,
                    ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY purchase_date DESC) as rn
                FROM corporate_treasury_purchases
            ) latest ON c.id = latest.company_id AND latest.rn = 1
            WHERE c.is_active = 1
        """)
        stats = cursor.fetchone()

        total_companies = stats['total_companies'] or 0
        total_btc = float(stats['total_btc_holdings'] or 0)
        total_value_usd = total_btc * current_btc_price

        # 最近 30 天活跃公司
        cursor.execute("""
            SELECT COUNT(DISTINCT company_id) as active_companies
            FROM corporate_treasury_purchases
            WHERE purchase_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
        """)
        active_result = cursor.fetchone()
        active_companies_30d = active_result['active_companies'] or 0

        # Top 10 持仓公司
        cursor.execute("""
            SELECT
                c.company_name,
                c.ticker_symbol,
                c.category,
                latest.cumulative_holdings as btc_holdings,
                latest.cumulative_holdings * %s as value_usd,
                latest.purchase_date as last_update
            FROM corporate_treasury_companies c
            INNER JOIN (
                SELECT
                    company_id,
                    cumulative_holdings,
                    purchase_date,
                    ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY purchase_date DESC) as rn
                FROM corporate_treasury_purchases
            ) latest ON c.id = latest.company_id AND latest.rn = 1
            WHERE c.is_active = 1 AND latest.cumulative_holdings > 0
            ORDER BY latest.cumulative_holdings DESC
            LIMIT 10
        """, (current_btc_price,))
        top_holders = cursor.fetchall()

        # 格式化数据
        for holder in top_holders:
            holder['btc_holdings'] = float(holder['btc_holdings'])
            holder['value_usd'] = float(holder['value_usd'])
            holder['last_update'] = holder['last_update'].strftime('%Y-%m-%d') if holder['last_update'] else None

        return {
            "success": True,
            "data": {
                "summary": {
                    "total_companies": total_companies,
                    "total_btc_holdings": round(total_btc, 2),
                    "total_value_usd": round(total_value_usd, 2),
                    "current_btc_price": round(current_btc_price, 2),
                    "active_companies_30d": active_companies_30d
                },
                "top_holders": top_holders
            },
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取数据失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()


@router.get("/api/corporate-treasury/companies")
async def get_companies_list(
    category: Optional[str] = Query(None, description="公司类别: mining, holding, payment"),
    limit: int = Query(50, ge=1, le=100, description="返回数量")
):
    """
    获取公司列表及最新持仓

    参数:
    - category: 可选，筛选公司类别
    - limit: 返回数量，默认 50
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # 构建查询
        where_clause = "WHERE c.is_active = 1"
        params = []

        if category:
            where_clause += " AND c.category = %s"
            params.append(category)

        query = f"""
            SELECT
                c.id,
                c.company_name,
                c.ticker_symbol,
                c.category,
                COALESCE(latest.cumulative_holdings, 0) as current_btc_holdings,
                latest.purchase_date as last_update,
                prev.cumulative_holdings as previous_holdings,
                latest.quantity as last_change,
                CASE
                    WHEN latest.quantity > 0 THEN 'buy'
                    WHEN latest.quantity < 0 THEN 'sell'
                    ELSE 'hold'
                END as last_action
            FROM corporate_treasury_companies c
            LEFT JOIN (
                SELECT
                    company_id,
                    cumulative_holdings,
                    purchase_date,
                    quantity,
                    ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY purchase_date DESC) as rn
                FROM corporate_treasury_purchases
            ) latest ON c.id = latest.company_id AND latest.rn = 1
            LEFT JOIN (
                SELECT
                    company_id,
                    cumulative_holdings,
                    ROW_NUMBER() OVER (PARTITION BY company_id ORDER BY purchase_date DESC) as rn
                FROM corporate_treasury_purchases
            ) prev ON c.id = prev.company_id AND prev.rn = 2
            {where_clause}
            ORDER BY latest.cumulative_holdings DESC
            LIMIT %s
        """
        params.append(limit)

        cursor.execute(query, tuple(params))
        companies = cursor.fetchall()

        # 格式化数据
        for company in companies:
            company['current_btc_holdings'] = float(company['current_btc_holdings'] or 0)
            company['previous_holdings'] = float(company['previous_holdings'] or 0)
            company['last_change'] = float(company['last_change'] or 0)
            company['last_update'] = company['last_update'].strftime('%Y-%m-%d') if company['last_update'] else None

        return {
            "success": True,
            "data": companies,
            "count": len(companies),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取公司列表失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()


@router.get("/api/corporate-treasury/company/{company_id}/history")
async def get_company_history(
    company_id: int,
    days: int = Query(90, ge=1, le=365, description="历史天数")
):
    """
    获取单个公司的历史持仓变化

    参数:
    - company_id: 公司 ID
    - days: 历史天数，默认 90 天
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # 获取公司信息
        cursor.execute("""
            SELECT company_name, ticker_symbol, category
            FROM corporate_treasury_companies
            WHERE id = %s AND is_active = 1
        """, (company_id,))
        company = cursor.fetchone()

        if not company:
            raise HTTPException(status_code=404, detail="公司不存在")

        # 获取持仓历史
        cursor.execute("""
            SELECT
                purchase_date,
                quantity,
                cumulative_holdings,
                average_price,
                total_cost,
                CASE
                    WHEN quantity > 0 THEN 'buy'
                    WHEN quantity < 0 THEN 'sell'
                    ELSE 'hold'
                END as action
            FROM corporate_treasury_purchases
            WHERE company_id = %s
                AND purchase_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY purchase_date DESC
        """, (company_id, days))
        purchases = cursor.fetchall()

        # 格式化数据
        for purchase in purchases:
            purchase['purchase_date'] = purchase['purchase_date'].strftime('%Y-%m-%d')
            purchase['quantity'] = float(purchase['quantity'])
            purchase['cumulative_holdings'] = float(purchase['cumulative_holdings'])
            purchase['average_price'] = float(purchase['average_price'] or 0)
            purchase['total_cost'] = float(purchase['total_cost'] or 0)

        # 获取融资历史
        cursor.execute("""
            SELECT
                financing_date,
                financing_type,
                amount,
                purpose,
                notes
            FROM corporate_treasury_financing
            WHERE company_id = %s
                AND financing_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY financing_date DESC
        """, (company_id, days))
        financing = cursor.fetchall()

        for fin in financing:
            fin['financing_date'] = fin['financing_date'].strftime('%Y-%m-%d')
            fin['amount'] = float(fin['amount'] or 0)

        # 获取股价历史
        cursor.execute("""
            SELECT
                trade_date,
                open_price,
                close_price,
                high_price,
                low_price,
                volume,
                change_pct,
                market_cap
            FROM corporate_treasury_stock_prices
            WHERE company_id = %s
                AND trade_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY trade_date DESC
        """, (company_id, days))
        stock_prices = cursor.fetchall()

        for price in stock_prices:
            price['trade_date'] = price['trade_date'].strftime('%Y-%m-%d')
            for key in ['open_price', 'close_price', 'high_price', 'low_price', 'change_pct']:
                if price.get(key):
                    price[key] = float(price[key])
            if price.get('volume'):
                price['volume'] = int(price['volume'])
            if price.get('market_cap'):
                price['market_cap'] = float(price['market_cap'])

        return {
            "success": True,
            "data": {
                "company": company,
                "purchases": purchases,
                "financing": financing,
                "stock_prices": stock_prices
            },
            "timestamp": datetime.now().isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取公司历史失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()


@router.get("/api/corporate-treasury/recent-activities")
async def get_recent_activities(
    days: int = Query(7, ge=1, le=30, description="最近天数"),
    limit: int = Query(20, ge=1, le=50, description="返回数量")
):
    """
    获取最近的购买活动

    参数:
    - days: 最近天数，默认 7 天
    - limit: 返回数量，默认 20
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                c.company_name,
                c.ticker_symbol,
                c.category,
                p.purchase_date,
                p.quantity,
                p.cumulative_holdings,
                p.average_price,
                CASE
                    WHEN p.quantity > 0 THEN 'buy'
                    WHEN p.quantity < 0 THEN 'sell'
                    ELSE 'hold'
                END as action
            FROM corporate_treasury_purchases p
            INNER JOIN corporate_treasury_companies c ON p.company_id = c.id
            WHERE p.purchase_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                AND c.is_active = 1
            ORDER BY p.purchase_date DESC, ABS(p.quantity) DESC
            LIMIT %s
        """, (days, limit))
        activities = cursor.fetchall()

        # 格式化数据
        for activity in activities:
            activity['purchase_date'] = activity['purchase_date'].strftime('%Y-%m-%d')
            activity['quantity'] = float(activity['quantity'])
            activity['cumulative_holdings'] = float(activity['cumulative_holdings'])
            activity['average_price'] = float(activity['average_price'] or 0)

        return {
            "success": True,
            "data": activities,
            "count": len(activities),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取最近活动失败: {str(e)}")
    finally:
        cursor.close()
        conn.close()
