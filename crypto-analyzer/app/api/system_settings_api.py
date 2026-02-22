"""
系统配置管理API
提供V1/V2策略切换、Big4过滤器等系统配置的读取和更新
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Dict, Optional
import pymysql
import os
from loguru import logger

router = APIRouter(prefix="/api/system", tags=["System Settings"])


def get_db_config():
    """获取数据库配置"""
    return {
        'host': os.getenv('DB_HOST', '13.212.252.171'),
        'user': os.getenv('DB_USER', 'admin'),
        'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
        'database': os.getenv('DB_NAME', 'binance-data'),
        'charset': 'utf8mb4'
    }


class SystemSetting(BaseModel):
    """系统配置模型"""
    setting_key: str
    setting_value: str
    description: Optional[str] = None


class BatchEntryStrategyUpdate(BaseModel):
    """分批建仓策略更新"""
    strategy: str  # 'kline_pullback' or 'price_percentile'


class Big4FilterUpdate(BaseModel):
    """Big4过滤器更新"""
    enabled: bool


class TradingDirectionUpdate(BaseModel):
    """交易方向更新"""
    allow_long: Optional[bool] = None
    allow_short: Optional[bool] = None


@router.get("/settings")
async def get_system_settings():
    """
    获取所有系统配置

    Returns:
        所有系统配置的字典
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT setting_key, setting_value, description
            FROM system_settings
            ORDER BY setting_key
        """)

        settings = cursor.fetchall()
        cursor.close()
        conn.close()

        # 转换为字典格式
        result = {}
        for setting in settings:
            result[setting['setting_key']] = {
                'value': setting['setting_value'],
                'description': setting['description']
            }

        return {
            'success': True,
            'data': result
        }

    except Exception as e:
        logger.error(f"获取系统配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings/{key}")
async def get_setting(key: str):
    """
    获取单个配置项

    Args:
        key: 配置键名

    Returns:
        配置项详情
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT setting_key, setting_value, description
            FROM system_settings
            WHERE setting_key = %s
        """, (key,))

        setting = cursor.fetchone()
        cursor.close()
        conn.close()

        if not setting:
            raise HTTPException(status_code=404, detail=f"配置项 {key} 不存在")

        return {
            'success': True,
            'data': {
                'key': setting['setting_key'],
                'value': setting['setting_value'],
                'description': setting['description']
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取配置项 {key} 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/batch-entry-strategy")
async def update_batch_entry_strategy(data: BatchEntryStrategyUpdate):
    """
    更新分批建仓策略

    Args:
        data: 策略更新数据

    Returns:
        更新结果
    """
    if data.strategy not in ['kline_pullback', 'price_percentile']:
        raise HTTPException(
            status_code=400,
            detail="策略必须是 'kline_pullback' (V2) 或 'price_percentile' (V1)"
        )

    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE system_settings
            SET setting_value = %s,
                updated_by = 'web_ui',
                updated_at = CURRENT_TIMESTAMP
            WHERE setting_key = 'batch_entry_strategy'
        """, (data.strategy,))

        conn.commit()
        cursor.close()
        conn.close()

        strategy_name = 'V2 K线回调策略' if data.strategy == 'kline_pullback' else 'V1 价格分位数策略'
        logger.info(f"✅ 分批建仓策略已更新为: {strategy_name} ({data.strategy})")

        return {
            'success': True,
            'message': f'策略已更新为 {strategy_name}',
            'data': {
                'strategy': data.strategy,
                'strategy_name': strategy_name,
                'note': '需要重启服务才能生效'
            }
        }

    except Exception as e:
        logger.error(f"更新分批建仓策略失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/big4-filter")
async def update_big4_filter(data: Big4FilterUpdate):
    """
    更新Big4过滤器状态

    Args:
        data: Big4过滤器更新数据

    Returns:
        更新结果
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        setting_value = 'true' if data.enabled else 'false'

        cursor.execute("""
            UPDATE system_settings
            SET setting_value = %s,
                updated_by = 'web_ui',
                updated_at = CURRENT_TIMESTAMP
            WHERE setting_key = 'big4_filter_enabled'
        """, (setting_value,))

        conn.commit()
        cursor.close()
        conn.close()

        status_text = '已启用' if data.enabled else '已禁用'
        logger.info(f"✅ Big4过滤器{status_text}")

        return {
            'success': True,
            'message': f'Big4过滤器{status_text}',
            'data': {
                'enabled': data.enabled,
                'note': '需要重启服务才能生效'
            }
        }

    except Exception as e:
        logger.error(f"更新Big4过滤器失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trading-direction")
async def get_trading_direction():
    """
    获取交易方向设置

    Returns:
        交易方向设置
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT param_key, param_value, description
            FROM adaptive_params
            WHERE param_key IN ('allow_long', 'allow_short')
            ORDER BY param_key
        """)

        settings = cursor.fetchall()
        cursor.close()
        conn.close()

        # 转换为字典格式
        result = {}
        for setting in settings:
            result[setting['param_key']] = {
                'value': int(float(setting['param_value'])) == 1,
                'description': setting['description']
            }

        # 如果没有找到设置，使用默认值
        if 'allow_long' not in result:
            result['allow_long'] = {'value': True, 'description': '是否允许做多'}
        if 'allow_short' not in result:
            result['allow_short'] = {'value': True, 'description': '是否允许做空'}

        return {
            'success': True,
            'data': result
        }

    except Exception as e:
        logger.error(f"获取交易方向设置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/trading-direction")
async def update_trading_direction(data: TradingDirectionUpdate):
    """
    更新交易方向设置

    Args:
        data: 交易方向更新数据

    Returns:
        更新结果
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        updates = []
        if data.allow_long is not None:
            value = 1 if data.allow_long else 0
            cursor.execute("""
                UPDATE adaptive_params
                SET param_value = %s, updated_by = 'web_ui', updated_at = NOW()
                WHERE param_key = 'allow_long'
            """, (value,))
            updates.append(f"做多: {'允许' if data.allow_long else '禁止'}")

        if data.allow_short is not None:
            value = 1 if data.allow_short else 0
            cursor.execute("""
                UPDATE adaptive_params
                SET param_value = %s, updated_by = 'web_ui', updated_at = NOW()
                WHERE param_key = 'allow_short'
            """, (value,))
            updates.append(f"做空: {'允许' if data.allow_short else '禁止'}")

        conn.commit()
        cursor.close()
        conn.close()

        update_msg = ', '.join(updates)
        logger.info(f"✅ 交易方向已更新: {update_msg}")

        return {
            'success': True,
            'message': f'交易方向已更新',
            'data': {
                'allow_long': data.allow_long,
                'allow_short': data.allow_short,
                'updates': updates,
                'note': '配置实时生效，最长延迟5分钟（缓存刷新）'
            }
        }

    except Exception as e:
        logger.error(f"更新交易方向设置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings/{key}")
async def update_setting(key: str, data: SystemSetting):
    """
    更新单个配置项（通用接口）

    Args:
        key: 配置键名
        data: 配置数据

    Returns:
        更新结果
    """
    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO system_settings (setting_key, setting_value, description, updated_by)
            VALUES (%s, %s, %s, 'web_ui')
            ON DUPLICATE KEY UPDATE
                setting_value = VALUES(setting_value),
                description = VALUES(description),
                updated_by = 'web_ui',
                updated_at = CURRENT_TIMESTAMP
        """, (key, data.setting_value, data.description))

        conn.commit()
        cursor.close()
        conn.close()

        logger.info(f"✅ 配置项 {key} 已更新为: {data.setting_value}")

        return {
            'success': True,
            'message': f'配置项 {key} 更新成功',
            'data': {
                'key': key,
                'value': data.setting_value
            }
        }

    except Exception as e:
        logger.error(f"更新配置项 {key} 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
