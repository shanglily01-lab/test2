#!/usr/bin/env python3
"""
交易控制API - 用于管理交易开关状态
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import pymysql
import os
from loguru import logger

router = APIRouter(prefix="/api/trading-control", tags=["trading-control"])

# 数据库配置
def get_db_connection():
    """获取数据库连接"""
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', '3306')),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data'),
        'charset': 'utf8mb4'
    }
    return pymysql.connect(**db_config)


class TradingControlUpdate(BaseModel):
    """交易控制更新请求"""
    account_id: int
    trading_type: str  # usdt_futures 或 coin_futures
    trading_enabled: bool
    updated_by: Optional[str] = "web_user"


class TradingControlResponse(BaseModel):
    """交易控制响应"""
    account_id: int
    trading_type: str
    trading_enabled: bool
    updated_by: Optional[str]
    updated_at: Optional[str]


@router.get("/status/{account_id}/{trading_type}", response_model=TradingControlResponse)
async def get_trading_status(account_id: int, trading_type: str):
    """
    获取交易开关状态

    Args:
        account_id: 账户ID (2=U本位合约, 3=币本位合约)
        trading_type: 交易类型 (usdt_futures/coin_futures)

    Returns:
        交易开关状态
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT account_id, trading_type, trading_enabled, updated_by,
                   DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') as updated_at
            FROM trading_control
            WHERE account_id = %s AND trading_type = %s
        """, (account_id, trading_type))

        result = cursor.fetchone()
        cursor.close()
        conn.close()

        if not result:
            # 如果不存在，返回默认启用状态
            return TradingControlResponse(
                account_id=account_id,
                trading_type=trading_type,
                trading_enabled=True,
                updated_by="system",
                updated_at=None
            )

        return TradingControlResponse(**result)

    except Exception as e:
        logger.error(f"Failed to get trading status: {e}")
        raise HTTPException(status_code=500, detail=f"获取交易状态失败: {str(e)}")


@router.post("/toggle", response_model=TradingControlResponse)
async def toggle_trading_status(data: TradingControlUpdate):
    """
    切换交易开关状态

    Args:
        data: 交易控制更新数据

    Returns:
        更新后的交易开关状态
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 插入或更新交易控制状态
        cursor.execute("""
            INSERT INTO trading_control (account_id, trading_type, trading_enabled, updated_by)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                trading_enabled = VALUES(trading_enabled),
                updated_by = VALUES(updated_by),
                updated_at = CURRENT_TIMESTAMP
        """, (data.account_id, data.trading_type, data.trading_enabled, data.updated_by))

        conn.commit()

        # 获取更新后的状态
        cursor.execute("""
            SELECT account_id, trading_type, trading_enabled, updated_by,
                   DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') as updated_at
            FROM trading_control
            WHERE account_id = %s AND trading_type = %s
        """, (data.account_id, data.trading_type))

        result = cursor.fetchone()
        cursor.close()
        conn.close()

        status_text = "启用" if data.trading_enabled else "停止"
        type_text = "U本位合约" if data.trading_type == "usdt_futures" else "币本位合约"
        logger.info(f"Trading status updated: {type_text} (account_id={data.account_id}) -> {status_text} by {data.updated_by}")

        return TradingControlResponse(**result)

    except Exception as e:
        logger.error(f"Failed to toggle trading status: {e}")
        raise HTTPException(status_code=500, detail=f"更新交易状态失败: {str(e)}")


@router.get("/all")
async def get_all_trading_status():
    """
    获取所有交易开关状态

    Returns:
        所有账户的交易开关状态列表
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT account_id, trading_type, trading_enabled, updated_by,
                   DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s') as updated_at
            FROM trading_control
            ORDER BY account_id, trading_type
        """)

        results = cursor.fetchall()
        cursor.close()
        conn.close()

        return {"data": results}

    except Exception as e:
        logger.error(f"Failed to get all trading status: {e}")
        raise HTTPException(status_code=500, detail=f"获取所有交易状态失败: {str(e)}")
