"""
区块链Gas消耗统计API
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict
from datetime import datetime, date, timedelta, timezone
import mysql.connector
from mysql.connector import pooling
from app.utils.config_loader import load_config
from pathlib import Path
from decimal import Decimal
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

# 从config.yaml加载数据库配置
project_root = Path(__file__).parent.parent.parent
config_path = project_root / "config.yaml"
connection_pool = None
_init_failed = False

def get_db_connection():
    """获取数据库连接（延迟初始化连接池）"""
    global connection_pool, _init_failed
    
    # 如果之前初始化失败过，直接返回错误，避免重复尝试
    if _init_failed:
        raise HTTPException(status_code=500, detail="数据库连接池初始化失败，请检查配置和数据库状态")
    
    # 延迟初始化：只在第一次调用时创建连接池
    if connection_pool is None:
        try:
            if not config_path.exists():
                _init_failed = True
                raise HTTPException(status_code=500, detail=f"config.yaml 不存在: {config_path}")

            # 使用 config_loader 加载配置，自动替换环境变量
            config = load_config(config_path)

            mysql_config = config.get('database', {}).get('mysql', {})

            # 确保端口号是整数类型
            port = mysql_config.get('port', 3306)
            if isinstance(port, str):
                port = int(port)

            db_config = {
                "host": mysql_config.get('host', 'localhost'),
                "port": port,
                "user": mysql_config.get('user', 'root'),
                "password": mysql_config.get('password', ''),
                "database": mysql_config.get('database', 'crypto_analyzer'),
                "pool_name": "blockchain_gas_pool",
                "pool_size": 10,  # 增加连接池大小
                "pool_reset_session": True,
                "autocommit": True
            }
            
            connection_pool = pooling.MySQLConnectionPool(**db_config)
            logger.info(f"✅ 区块链Gas统计数据库连接池创建成功: {db_config['database']}")
            
        except HTTPException:
            raise
        except mysql.connector.Error as e:
            _init_failed = True
            error_msg = f"MySQL连接失败: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise HTTPException(status_code=500, detail=error_msg)
        except Exception as e:
            _init_failed = True
            import traceback
            error_trace = traceback.format_exc()
            logger.error(f"❌ 数据库连接池初始化失败:\n{error_trace}")
            raise HTTPException(status_code=500, detail=f"初始化失败: {str(e)}")
    
    # 从连接池获取连接
    try:
        conn = connection_pool.get_connection()
        return conn
    except mysql.connector.Error as e:
        error_msg = f"获取数据库连接失败: {str(e)}"
        logger.error(f"❌ {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/api/blockchain-gas/daily")
async def get_daily_gas_stats(
    chain_name: Optional[str] = Query(None, description="链名称，如 ethereum, bsc, polygon 等"),
    start_date: Optional[str] = Query(None, description="开始日期，格式: YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期，格式: YYYY-MM-DD"),
    limit: int = Query(30, ge=1, le=365, description="返回记录数")
):
    """
    获取Gas消耗统计数据
    
    参数:
    - chain_name: 可选，筛选特定链
    - start_date: 可选，开始日期
    - end_date: 可选，结束日期
    - limit: 返回记录数，默认30天
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # 构建查询
        where_clauses = []
        params = []
        
        if chain_name:
            where_clauses.append("chain_name = %s")
            params.append(chain_name)
        
        if start_date:
            where_clauses.append("date >= %s")
            params.append(start_date)
        
        if end_date:
            where_clauses.append("date <= %s")
            params.append(end_date)
        
        if not start_date and not end_date:
            # 如果没有指定日期范围，返回最近N天
            where_clauses.append("date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)")
            params.append(limit)
        
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        
        query = f"""
            SELECT 
                id,
                chain_name,
                chain_display_name,
                date,
                total_gas_used,
                total_transactions,
                avg_gas_per_tx,
                avg_gas_price,
                max_gas_price,
                min_gas_price,
                native_token_price_usd,
                total_gas_value_usd,
                avg_gas_value_usd,
                total_blocks,
                active_addresses,
                new_addresses,
                data_source,
                created_at,
                updated_at
            FROM blockchain_gas_daily
            WHERE {where_sql}
            ORDER BY date DESC, chain_name
            LIMIT %s
        """
        params.append(limit * 10)  # 如果多链，需要更多记录
        
        try:
            cursor.execute(query, tuple(params))
            results = cursor.fetchall()
        except mysql.connector.Error as e:
            # 如果表不存在，返回空数据
            if e.errno == 1146:  # Table doesn't exist
                logger.warning(f"表 blockchain_gas_daily 不存在，返回空数据")
                results = []
            else:
                raise
        
        # 格式化数据
        for row in results:
            row['total_gas_used'] = float(row.get('total_gas_used') or 0)
            row['total_transactions'] = int(row.get('total_transactions') or 0)
            row['avg_gas_per_tx'] = float(row.get('avg_gas_per_tx') or 0)
            row['avg_gas_price'] = float(row.get('avg_gas_price') or 0)
            row['total_gas_value_usd'] = float(row.get('total_gas_value_usd') or 0)
            row['avg_gas_value_usd'] = float(row.get('avg_gas_value_usd') or 0)
            row['native_token_price_usd'] = float(row.get('native_token_price_usd')) if row.get('native_token_price_usd') else None
            if row.get('date'):
                if isinstance(row['date'], str):
                    row['date'] = row['date']
                else:
                    row['date'] = row['date'].strftime('%Y-%m-%d')
            else:
                row['date'] = None
        
        return {
            "success": True,
            "data": results,
            "count": len(results),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"获取Gas统计数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取Gas统计数据失败: {str(e)}")
    finally:
        if conn:
            cursor.close()
            conn.close()


@router.get("/api/blockchain-gas/summary")
async def get_gas_summary(
    days: int = Query(7, ge=1, le=90, description="统计天数")
):
    """
    获取Gas消耗汇总统计
    
    参数:
    - days: 统计天数，默认7天
    """
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
        # 获取最近N天的汇总数据
        query = """
            SELECT 
                chain_name,
                chain_display_name,
                SUM(total_gas_used) as total_gas_used,
                SUM(total_transactions) as total_transactions,
                AVG(avg_gas_per_tx) as avg_gas_per_tx,
                AVG(avg_gas_price) as avg_gas_price,
                SUM(total_gas_value_usd) as total_gas_value_usd,
                AVG(avg_gas_value_usd) as avg_gas_value_usd,
                AVG(native_token_price_usd) as avg_native_token_price_usd,
                COUNT(*) as days_count
            FROM blockchain_gas_daily
            WHERE date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            GROUP BY chain_name, chain_display_name
            ORDER BY total_gas_value_usd DESC
        """
        
        try:
            cursor.execute(query, (days,))
            results = cursor.fetchall()
        except mysql.connector.Error as e:
            # 如果表不存在，返回空数据
            if e.errno == 1146:  # Table doesn't exist
                logger.warning(f"表 blockchain_gas_daily 不存在，返回空数据")
                results = []
            else:
                raise
        
        # 计算总计
        total_gas_value = sum(float(r.get('total_gas_value_usd') or 0) for r in results)
        total_transactions = sum(int(r.get('total_transactions') or 0) for r in results)
        
        # 格式化数据
        for row in results:
            row['total_gas_used'] = float(row.get('total_gas_used') or 0)
            row['total_transactions'] = int(row.get('total_transactions') or 0)
            row['avg_gas_per_tx'] = float(row.get('avg_gas_per_tx') or 0)
            row['avg_gas_price'] = float(row.get('avg_gas_price') or 0)
            row['total_gas_value_usd'] = float(row.get('total_gas_value_usd') or 0)
            row['avg_gas_value_usd'] = float(row.get('avg_gas_value_usd') or 0)
            row['avg_native_token_price_usd'] = float(row.get('avg_native_token_price_usd')) if row.get('avg_native_token_price_usd') else None
        
        return {
            "success": True,
            "data": {
                "chains": results,
                "summary": {
                    "total_chains": len(results),
                    "total_gas_value_usd": total_gas_value,
                    "total_transactions": total_transactions,
                    "avg_gas_value_usd": total_gas_value / total_transactions if total_transactions > 0 else 0,
                    "period_days": days
                }
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"获取Gas汇总统计失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取Gas汇总统计失败: {str(e)}")
    finally:
        if conn:
            cursor.close()
            conn.close()


@router.get("/api/blockchain-gas/chains")
async def get_supported_chains():
    """
    获取支持的链列表
    """
    chains = [
        {"name": "ethereum", "display_name": "Ethereum", "native_token": "ETH"},
        {"name": "bsc", "display_name": "BSC", "native_token": "BNB"},
        {"name": "polygon", "display_name": "Polygon", "native_token": "MATIC"},
        {"name": "arbitrum", "display_name": "Arbitrum", "native_token": "ETH"},
        {"name": "optimism", "display_name": "Optimism", "native_token": "ETH"},
        {"name": "avalanche", "display_name": "Avalanche", "native_token": "AVAX"}
    ]
    
    return {
        "success": True,
        "data": chains,
        "count": len(chains)
    }

